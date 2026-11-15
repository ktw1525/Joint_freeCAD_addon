"""Axis planes and interactive three-point plane drawing."""
import FreeCAD as App
import FreeCADGui as Gui
from .geometry import JointError, world_shape
from .qt import QtCore, QtWidgets


def frame_from_points(points):
    if len(points) != 3:
        raise JointError("서로 일직선에 있지 않은 세 점이 필요합니다.")
    a, b, c = points
    x, other = b-a, c-a
    if min(x.Length, other.Length) < 1e-6:
        raise JointError("점이 겹칩니다. 다른 위치를 선택하세요.")
    normal = x.cross(other)
    if normal.Length / (x.Length*other.Length) < 1e-6:
        raise JointError("세 점이 일직선입니다. 마지막 점을 다른 위치에 찍으세요.")
    x.normalize()
    normal.normalize()
    return App.Placement((a+b+c)/3, App.Rotation(x, normal.cross(x), normal, "ZXY"))


def create_plane(doc, source, frame, size=None):
    size = size or max(world_shape(source).BoundBox.DiagonalLength*1.2, 1)
    origin = frame.Base - frame.Rotation.multVec(App.Vector(size/2, size/2, 0))
    doc.openTransaction("절단면 만들기")
    try:
        plane = doc.addObject("Part::Plane", "JointCuttingPlane")
        plane.Label = "조인트 절단면"
        plane.Length = size
        plane.Width = size
        plane.Placement = App.Placement(origin, frame.Rotation)
        if App.GuiUp:
            plane.ViewObject.ShapeColor = (0.4, 0.8, 0.95)
            plane.ViewObject.Transparency = 70
        doc.recompute()
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise
    if App.GuiUp:
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(plane)
    return plane


def create_axis_plane(source, axis):
    normal = {"XY": App.Vector(0,0,1), "YZ": App.Vector(1,0,0), "XZ": App.Vector(0,1,0)}[axis]
    frame = App.Placement(world_shape(source).BoundBox.Center, App.Rotation(App.Vector(0,0,1), normal))
    return create_plane(source.Document, source, frame)


class DrawPlaneTask:
    def __init__(self, source, view=None):
        self.source = source
        self.doc = source.Document
        self.view = view or Gui.activeDocument().activeView()
        self.points = []
        self.callback = None
        self.overlay = None
        self.scene = None
        self.finished = False
        self.observing = False
        self.created_plane = None
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("절단면 그리기 · 3점")
        layout = QtWidgets.QVBoxLayout(self.form)
        help_text = QtWidgets.QLabel("3D 화면에서 세 점을 차례로 클릭하세요.\n물체 위는 표면 좌표, 빈 공간은 현재 화면 깊이의 좌표를 사용합니다.\n세 번째 점이 정해지면 절단 평면을 만듭니다.\nEsc / 취소: 종료 · 마지막 점 취소: 다시 선택\n정확한 좌표는 아래에 직접 입력할 수도 있습니다.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.status = QtWidgets.QLabel("첫 번째 점을 선택하세요 (0/3)")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.coords = []
        form = QtWidgets.QFormLayout()
        for label in ("X", "Y", "Z"):
            widget = QtWidgets.QDoubleSpinBox()
            widget.setRange(-1e9, 1e9)
            widget.setDecimals(4)
            widget.setSuffix(" mm")
            self.coords.append(widget)
            form.addRow(label, widget)
        layout.addLayout(form)
        add = QtWidgets.QPushButton("입력 좌표를 점으로 추가")
        add.clicked.connect(self.add_coordinates)
        layout.addWidget(add)
        undo = QtWidgets.QPushButton("마지막 점 취소")
        undo.clicked.connect(self.undo_point)
        layout.addWidget(undo)

    def start(self):
        if Gui.Control.activeDialog():
            raise JointError("열려 있는 작업을 완료하거나 닫은 뒤 면 그리기를 시작하세요.")
        Gui.Control.showDialog(self)
        try:
            App.addDocumentObserver(self)
            self.observing = True
            from pivy import coin
            self.overlay = coin.SoSeparator()
            style = coin.SoDrawStyle()
            style.pointSize = 9
            style.lineWidth = 3
            color = coin.SoBaseColor()
            color.rgb = (1.0, 0.45, 0.1)
            self.coordinates = coin.SoCoordinate3()
            self.lines = coin.SoLineSet()
            self.markers = coin.SoPointSet()
            for node in (style, color, self.coordinates, self.lines, self.markers):
                self.overlay.addChild(node)
            self.scene = self.view.getSceneGraph()
            self.scene.addChild(self.overlay)
            self.callback = self.view.addEventCallback("SoEvent", self.on_event)
        except Exception:
            self.close()
            raise

    def getStandardButtons(self):
        button = QtWidgets.QDialogButtonBox.Cancel
        return int(getattr(button, "value", button))

    def slotDeletedDocument(self, doc):
        if doc is self.doc:
            self.close()

    def reject(self):
        self.close()
        return True

    def close(self):
        if self.finished:
            return
        self.finished = True
        if self.observing:
            App.removeDocumentObserver(self)
            self.observing = False
        if self.callback is not None:
            try:
                self.view.removeEventCallback("SoEvent", self.callback)
            except (RuntimeError, ReferenceError):
                pass
            self.callback = None
        if self.overlay is not None:
            try:
                if self.scene is not None and self.scene.findChild(self.overlay) >= 0:
                    self.scene.removeChild(self.overlay)
            except (RuntimeError, ReferenceError):
                pass
            self.overlay = None
            self.scene = None
        Gui.Control.closeDialog()

    def update_preview(self):
        if self.overlay is not None:
            self.coordinates.point.setValues(0, len(self.points), [(p.x,p.y,p.z) for p in self.points])
            self.coordinates.point.setNum(len(self.points))
            self.lines.numVertices.setValue(len(self.points))
            self.markers.numPoints = len(self.points)

    def undo_point(self):
        if self.points:
            self.points.pop()
        self.status.setText("다음 점을 선택하세요 ({}/3)".format(len(self.points)))
        self.update_preview()

    def add_coordinates(self):
        self.add_point(App.Vector(*(widget.value() for widget in self.coords)))

    def add_point(self, point):
        if self.finished:
            return
        try:
            if App.ActiveDocument is not self.doc or self.doc.getObject(self.source.Name) is not self.source:
                raise JointError("원본 문서가 바뀌었습니다. 취소 후 다시 시작하세요.")
            if self.points and (point-self.points[-1]).Length < 1e-6:
                raise JointError("점이 겹칩니다. 다른 위치를 선택하세요.")
            if len(self.points) == 2:
                points = self.points + [point]
                frame = frame_from_points(points)
                size = max(world_shape(self.source).BoundBox.DiagonalLength*1.2,
                           max((a-b).Length for a in points for b in points)*2, 1)
                self.created_plane = create_plane(self.doc, self.source, frame, size)
                self.close()
                return
            self.points.append(point)
            self.status.setText("다음 점을 선택하세요 ({}/3)".format(len(self.points)))
            self.update_preview()
        except Exception as exc:
            self.status.setText(str(exc))

    def on_event(self, event):
        if self.finished:
            return
        if event.get("Type") == "SoKeyboardEvent" and event.get("Key") == "ESCAPE":
            self.close()
        elif event.get("Type") == "SoMouseButtonEvent" and event.get("State") == "DOWN" and event.get("Button") == "BUTTON1":
            try:
                position = event["Position"]
                hit = self.view.getObjectInfo(position)
                point = App.Vector(hit["x"], hit["y"], hit["z"]) if hit else self.view.getPoint(*position)
                self.add_point(point)
            except Exception as exc:
                self.status.setText(str(exc))
