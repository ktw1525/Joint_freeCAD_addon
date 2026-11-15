import os
import FreeCAD as App
import FreeCADGui as Gui
import Part
from .qt import QtCore, QtGui, QtWidgets

from .geometry import JOINT_TYPES, JointError, defaults, plane_frame, world_shape, Parameters, require_solid, split
from .features import create_split
from .inputs import InputPicker
from .planes import create_axis_plane, DrawPlaneTask
from .preview import JointPreview, HINTS

ICON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "joint_splitter.svg")
DESCRIPTIONS = {
    "Mortise": "Part 1에 홈, Part 2에 둥근 장부를 만듭니다.",
    "Tenon": "Part 1에 둥근 장부, Part 2에 홈을 만듭니다.",
    "Dovetail": "사다리꼴 핑거와 Y 방향 관통 홈입니다. 홈을 따라 옆으로 밀어 조립합니다.",
    "Finger Joint": "직사각형 핑거와 대응하는 홈을 만듭니다.",
    "Cantilever": "외팔보 스냅 후크와 대응 홈입니다. 재료의 탄성과 조립 경로를 확인하세요.",
    "Cantilever Mate": "외팔보 후크의 암수 방향을 뒤집습니다.",
    "Annular": "네 개의 유연 슬롯이 있는 원형 스냅과 대응 홈입니다.",
    "Annular Mate": "원형 스냅의 암수 방향을 뒤집습니다.",
}


def selected_inputs():
    entries = Gui.Selection.getSelectionEx()
    if len(entries) != 2 or entries[0].Object is entries[1].Object:
        raise JointError("Ctrl로 솔리드 모델 1개와 별도 절단면 1개를 함께 선택하세요.")
    candidates = []
    for entry in entries:
        if entry.SubElementNames:
            if len(entry.SubElementNames) != 1 or not entry.SubElementNames[0].startswith("Face"):
                continue
            name = entry.SubElementNames[0]
        else:
            shape = world_shape(entry.Object)
            if len(shape.Faces) != 1:
                continue
            name = "Face1"
        try:
            plane_frame(world_shape(entry.Object, name))
            candidates.append((entry.Object, name))
        except (JointError, Part.OCCError):
            continue
    # A face selected on the source is allowed; identify the other, non-solid tool.
    for plane, face_name in candidates:
        source = next(entry.Object for entry in entries if entry.Object is not plane)
        plane_shape = world_shape(plane)
        if not plane_shape.Solids:
            require_solid(world_shape(source), "선택한 모델이 단일 솔리드가 아닙니다.")
            if source.Document is not plane.Document:
                raise JointError("같은 문서의 객체를 선택하세요.")
            return source, plane, face_name
    raise JointError("단일 솔리드 모델과 별도 평면을 선택하세요. '절단면 만들기'로 평면을 만들 수 있습니다.")


def spin(value, minimum=0.001, maximum=1000000, suffix=" mm"):
    widget = QtWidgets.QDoubleSpinBox()
    widget.setDecimals(3)
    widget.setRange(minimum, maximum)
    widget.setSingleStep(0.1)
    widget.setSuffix(suffix)
    widget.setValue(value)
    return widget


class SplitDialog(QtWidgets.QDialog):
    def __init__(self, source=None, plane=None, face_name=None):
        super().__init__(Gui.getMainWindow())
        self.doc = App.ActiveDocument
        self.preview = None
        self.active_field = "kind"
        self.watched_fields = {}
        self.preview_timer = QtCore.QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.update_preview)
        self.source, self.plane, self.face_name = source, plane, face_name
        p = Parameters()
        self.setWindowTitle("Joint Splitter · 조인트 분할")
        self.setMinimumWidth(520)
        self.setStyleSheet("""
            QDialog { background: #f5f7fa; }
            QLabel, QCheckBox { color: #24374a; }
            QComboBox, QDoubleSpinBox, QSpinBox {
                background: white; color: #152c40; padding: 5px;
                border: 1px solid #b8c6d3; border-radius: 4px; min-height: 20px;
            }
            QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {
                color: #8b9aa8; background: #edf0f4;
            }
            QPushButton { min-width: 80px; padding: 7px 12px; }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("<h2>맞물리는 두 파트로 분할</h2>")
        layout.addWidget(title)
        self.inputs = InputPicker(self.doc, source, plane, face_name)
        layout.addWidget(self.inputs)
        preview_controls = QtWidgets.QHBoxLayout()
        self.preview_checkbox = QtWidgets.QCheckBox("수치 변경 시 미리보기")
        self.preview_checkbox.setChecked(True)
        preview_controls.addWidget(self.preview_checkbox)
        preview_controls.addWidget(QtWidgets.QLabel("파트 벌리기"))
        self.explode = spin(12, 0)
        self.explode.setMaximumWidth(135)
        preview_controls.addWidget(self.explode)
        layout.addLayout(preview_controls)
        self.preview_status = QtWidgets.QLabel("미리보기 준비 중…")
        layout.addWidget(self.preview_status)
        self.dimension_hint = QtWidgets.QLabel(HINTS["kind"])
        self.dimension_hint.setWordWrap(True)
        layout.addWidget(self.dimension_hint)
        form = QtWidgets.QFormLayout()
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(JOINT_TYPES)
        self.kind.setCurrentText("Tenon")
        self.kind.setItemDelegate(QtWidgets.QStyledItemDelegate(self.kind))
        form.addRow("조인트 모양", self.kind)
        self.description = QtWidgets.QLabel()
        self.description.setWordWrap(True)
        form.addRow(self.description)
        self.fields = {}
        for key, label in (("clearance", "공차 · 면 사이 간격"), ("width", "전체 폭 / 원형 외경"),
                           ("length", "길이 / 원형 외경 제한"), ("depth", "조인트 깊이"),
                           ("offset_x", "단면 중심에서 X 이동"), ("offset_y", "단면 중심에서 Y 이동"),
                           ("rotation", "절단면 내 회전"), ("angle", "도브테일 각도")):
            minimum = 0 if key == "clearance" else (-1000000 if key in ("offset_x", "offset_y", "rotation") else 0.001)
            maximum = 39.999 if key == "angle" else 1000000
            widget = spin(getattr(p, key), minimum, maximum, " °" if key in ("angle", "rotation") else " mm")
            self.fields[key] = widget
            form.addRow(label, widget)
        self.count = QtWidgets.QSpinBox()
        self.count.setRange(1, 64)
        self.count.setValue(p.count)
        form.addRow("핑거 개수", self.count)
        self.reverse = QtWidgets.QCheckBox("암수 방향 뒤집기")
        form.addRow(self.reverse)
        form_container = QtWidgets.QWidget()
        form_container.setLayout(form)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(form_container)
        layout.addWidget(scroll, 1)
        help_text = QtWidgets.QLabel("공차 0.20 mm = 서로 마주 보는 면 사이 0.20 mm.\n홈 폭은 장부 폭보다 총 0.40 mm 커집니다.\n평면 크기는 무한히 연장해 사용합니다. 원본은 보존됩니다.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #c44832;")
        layout.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.split_button = buttons.addButton("분할", QtWidgets.QDialogButtonBox.AcceptRole)
        self.split_button.clicked.connect(self.perform_split)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.kind.currentTextChanged.connect(self.update_kind)
        self.kind.currentTextChanged.connect(lambda *_: self.parameter_changed("kind"))
        self.count.valueChanged.connect(lambda *_: self.parameter_changed("count"))
        self.reverse.toggled.connect(lambda *_: self.parameter_changed("reverse"))
        for key, widget in self.fields.items():
            widget.valueChanged.connect(lambda value, key=key: self.parameter_changed(key))
        for key, widget in dict(self.fields, kind=self.kind, count=self.count, reverse=self.reverse).items():
            self.watched_fields[widget] = key
            widget.installEventFilter(self)
            if hasattr(widget, "lineEdit") and widget.lineEdit() is not None:
                self.watched_fields[widget.lineEdit()] = key
                widget.lineEdit().installEventFilter(self)
        self.preview_checkbox.toggled.connect(self.toggle_preview)
        self.explode.valueChanged.connect(self.change_separation)
        self.inputs.changed.connect(self.input_changed)
        App.addDocumentObserver(self)
        self.observing = True
        self.update_kind()
        self.input_changed()
        screen = QtGui.QGuiApplication.primaryScreen()
        self.resize(550, min(900, screen.availableGeometry().height()-80) if screen else 900)

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.FocusIn and watched in self.watched_fields:
            self.set_active_field(self.watched_fields[watched])
        return super().eventFilter(watched, event)

    def set_active_field(self, key):
        self.active_field = key
        self.dimension_hint.setText(HINTS[key])
        if self.preview is not None:
            self.preview.highlight(key)

    def parameter_changed(self, key):
        self.set_active_field(key)
        self.schedule_preview()

    def schedule_preview(self):
        if self.preview_checkbox.isChecked() and self.split_button.isEnabled():
            self.preview_status.setText("미리보기 갱신 대기…")
            self.preview_timer.start(450)

    def parameters(self):
        return Parameters(kind=self.kind.currentText(), count=self.count.value(), reverse=self.reverse.isChecked(),
                          **{key: widget.value() for key, widget in self.fields.items()})

    def clear_preview(self):
        self.preview_timer.stop()
        if self.preview is not None:
            self.preview.dispose()
            self.preview = None

    def toggle_preview(self, enabled):
        if enabled:
            self.schedule_preview()
        else:
            self.clear_preview()
            self.preview_status.setText("미리보기 꺼짐")

    def change_separation(self, value):
        if self.preview is not None:
            self.preview.set_separation(value)

    def update_preview(self):
        self.preview_timer.stop()
        if not self.preview_checkbox.isChecked() or not self.split_button.isEnabled():
            return
        try:
            if App.ActiveDocument is not self.doc:
                raise JointError("원본 문서를 활성화하세요.")
            p = self.parameters()
            source = world_shape(self.source)
            face = world_shape(self.plane, self.face_name)
            pair = split(source, face, p)
            first_preview = self.preview is None
            if self.preview is None:
                self.preview = JointPreview(Gui.activeDocument().activeView(), self.source, self.plane)
            self.preview.show(pair, source, face, p, self.explode.value(), self.active_field)
            if first_preview:
                self.preview.view.fitAll()
            self.preview_status.setText("미리보기 · 파랑 Part 1 / 주황 Part 2 / 노랑 선택 치수")
            self.error.clear()
        except Exception as exc:
            self.clear_preview()
            self.preview_status.setText("미리보기 실패 · 수치 또는 면 위치를 확인하세요")
            self.error.setText(str(exc))

    def done(self, result):
        self.clear_preview()
        if getattr(self,"observing",False):
            App.removeDocumentObserver(self)
            self.observing = False
        super().done(result)

    def slotDeletedDocument(self, doc):
        if doc is self.doc:
            self.reject()

    def slotActivateDocument(self, doc):
        if doc is not self.doc:
            self.reject()

    def input_changed(self):
        self.clear_preview()
        self.source, self.plane, self.face_name = self.inputs.current()
        try:
            if self.source is None or self.plane is None:
                raise JointError("분할할 모델과 절단면 Face를 목록에서 선택하세요.")
            p = defaults(world_shape(self.source), world_shape(self.plane, self.face_name))
            for key in ("width", "length", "depth", "clearance", "offset_x", "offset_y", "rotation"):
                self.fields[key].blockSignals(True)
                self.fields[key].setValue(getattr(p, key))
                self.fields[key].blockSignals(False)
            self.split_button.setEnabled(True)
            self.error.clear()
            self.schedule_preview()
        except Exception as exc:
            self.split_button.setEnabled(False)
            self.error.setText(str(exc))
            self.preview_status.setText("모델과 절단면을 선택하세요")

    def update_kind(self, *_):
        kind = self.kind.currentText()
        self.description.setText(DESCRIPTIONS[kind])
        self.count.setEnabled(kind in ("Finger Joint", "Dovetail"))
        self.fields["angle"].setEnabled(kind == "Dovetail")

    def perform_split(self):
        if not self.split_button.isEnabled():
            return
        p = self.parameters()
        self.clear_preview()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            _, _, parts = create_split(self.source.Document, self.source, self.plane, self.face_name, p)
            Gui.Selection.clearSelection()
            for part in parts:
                Gui.Selection.addSelection(part)
            Gui.activeDocument().activeView().fitAll()
            self.accept()
        except Exception as exc:
            self.error.setText(str(exc))
            App.Console.PrintError("Joint Splitter: {}\n".format(exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()


class SplitCommand:
    def GetResources(self):
        return {"Pixmap": ICON, "MenuText": "조인트로 분할…", "ToolTip": "모델과 기존 Face를 목록에서 선택하고 조인트로 분할"}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        global _split_dialog
        try:
            if _split_dialog is not None and _split_dialog.isVisible():
                _split_dialog.raise_()
                _split_dialog.activateWindow()
                return
            dialog = SplitDialog(*input_hint())
            _split_dialog = dialog
            dialog.show()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "Joint Splitter", str(exc))


def input_hint():
    try:
        return selected_inputs()
    except JointError:
        for obj in Gui.Selection.getSelection():
            try:
                require_solid(world_shape(obj), "")
                return obj, None, None
            except Exception:
                pass
        return None, None, None


class PlaneDialog(QtWidgets.QDialog):
    DrawResult = 2

    def __init__(self, source=None, plane=None, face_name=None):
        super().__init__(Gui.getMainWindow())
        self.setWindowTitle("절단면 선택 / 만들기")
        self.setMinimumWidth(490)
        layout = QtWidgets.QVBoxLayout(self)
        self.inputs = InputPicker(App.ActiveDocument, source, plane, face_name)
        layout.addWidget(self.inputs)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        use = QtWidgets.QPushButton("선택한 기존 Face 사용")
        use.clicked.connect(self.use_face)
        layout.addWidget(use)
        layout.addWidget(QtWidgets.QLabel("또는 새 절단면을 만드세요:"))
        axis_buttons = QtWidgets.QHBoxLayout()
        for axis in ("XY", "YZ", "XZ"):
            button = QtWidgets.QPushButton(axis + " 면 생성")
            button.clicked.connect(lambda checked=False, axis=axis: self.make_axis(axis))
            axis_buttons.addWidget(button)
        layout.addLayout(axis_buttons)
        draw = QtWidgets.QPushButton("3D 화면에서 3점으로 절단면 그리기")
        draw.clicked.connect(self.draw_plane)
        layout.addWidget(draw)
        cancel = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        cancel.rejected.connect(self.reject)
        layout.addWidget(cancel)

    def source(self):
        source, _, _ = self.inputs.current()
        if source is None:
            raise JointError("분할할 모델을 먼저 선택하세요.")
        return source

    def use_face(self):
        try:
            source = self.source()
            _, plane, name = self.inputs.current()
            if plane is None:
                raise JointError("사용할 Face를 선택하세요.")
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(source)
            plane.Visibility = True
            Gui.Selection.addSelection(plane, name)
            self.accept()
        except Exception as exc:
            self.error.setText(str(exc))

    def make_axis(self, axis):
        try:
            create_axis_plane(self.source(), axis)
            self.accept()
        except Exception as exc:
            self.error.setText(str(exc))

    def draw_plane(self):
        try:
            self.source()
            self.done(self.DrawResult)
        except Exception as exc:
            self.error.setText(str(exc))


class PlaneCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(ICON), "select_plane.svg"),
                "MenuText": "절단면 선택 / 만들기…", "ToolTip": "기존 Face를 지정하거나 축 평면·3점 평면 생성"}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        try:
            close_split_dialog()
            dialog = PlaneDialog(*input_hint())
            if dialog.exec_() == PlaneDialog.DrawResult:
                start_drawing(dialog.source())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "Joint Splitter", str(exc))


_drawing_task = None
_split_dialog = None


def close_split_dialog():
    if _split_dialog is not None and _split_dialog.isVisible():
        _split_dialog.reject()


def start_drawing(source):
    global _drawing_task
    if _drawing_task is not None and not _drawing_task.finished:
        raise JointError("이미 면을 그리고 있습니다. 완료하거나 취소하세요.")
    _drawing_task = DrawPlaneTask(source)
    _drawing_task.start()


class DrawPlaneCommand(PlaneCommand):
    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(ICON), "draw_plane.svg"),
                "MenuText": "절단면 그리기 (3점)…", "ToolTip": "화면의 세 점 또는 XYZ 좌표로 절단 평면 생성"}

    def Activated(self):
        try:
            close_split_dialog()
            source, _, _ = input_hint()
            if source is None:
                dialog = PlaneDialog()
                if dialog.exec_() != PlaneDialog.DrawResult:
                    return
                source = dialog.source()
            start_drawing(source)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "Joint Splitter", str(exc))


def register():
    Gui.addCommand("JointSplitter_Plane", PlaneCommand())
    Gui.addCommand("JointSplitter_DrawPlane", DrawPlaneCommand())
    Gui.addCommand("JointSplitter_Split", SplitCommand())
