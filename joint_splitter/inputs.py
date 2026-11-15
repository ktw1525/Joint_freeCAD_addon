"""Document input lists and explicit model/Face selection widgets."""
import FreeCADGui as Gui
from .geometry import world_shape, plane_frame, require_solid
from .qt import QtCore, QtWidgets


def document_inputs(doc):
    models, faces = [], []
    for obj in doc.Objects:
        if not hasattr(obj, "Shape"):
            continue
        try:
            shape = world_shape(obj)
            if shape.isNull():
                continue
            if shape.Solids:
                require_solid(shape, "")
                models.append(obj)
            else:
                for index in range(len(shape.Faces)):
                    name = "Face" + str(index + 1)
                    try:
                        plane_frame(world_shape(obj, name))
                        faces.append((obj, name))
                    except Exception:
                        pass
        except Exception:
            continue
    return models, faces


class InputPicker(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, doc, source=None, plane=None, face_name=None, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.source_combo = QtWidgets.QComboBox()
        self.face_combo = QtWidgets.QComboBox()
        self.refresh_button = QtWidgets.QPushButton("목록 새로고침")
        self.highlight_button = QtWidgets.QPushButton("선택한 면 표시")
        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addRow("분할할 모델", self.source_combo)
        layout.addRow("절단면 · 기존 Face", self.face_combo)
        actions = QtWidgets.QHBoxLayout()
        actions.addWidget(self.highlight_button)
        actions.addWidget(self.refresh_button)
        layout.addRow(actions)
        self.refresh(source, plane, face_name)
        self.source_combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.face_combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.refresh_button.clicked.connect(self.refresh)
        self.highlight_button.clicked.connect(self.highlight)

    def refresh(self, source=None, plane=None, face_name=None):
        if isinstance(source, bool):
            source = None
        previous_source, previous_plane, previous_face = self.current()
        source = source or previous_source
        plane = plane or previous_plane
        face_name = face_name or previous_face
        self.models, self.faces = document_inputs(self.doc)
        for combo in (self.source_combo, self.face_combo):
            combo.blockSignals(True)
            combo.clear()
        self.source_combo.addItem("모델을 선택하세요")
        self.face_combo.addItem("기존 Face를 선택하세요")
        for obj in self.models:
            self.source_combo.addItem("{} ({})".format(obj.Label, obj.Name))
        for obj, name in self.faces:
            self.face_combo.addItem("{} · {} ({})".format(obj.Label, name, obj.Name))
        for index, obj in enumerate(self.models, 1):
            if obj is source or (source is None and len(self.models) == 1):
                self.source_combo.setCurrentIndex(index)
        for index, (obj, name) in enumerate(self.faces, 1):
            if (obj is plane and (face_name is None or name == face_name)) or (plane is None and len(self.faces) == 1):
                self.face_combo.setCurrentIndex(index)
        for combo in (self.source_combo, self.face_combo):
            combo.blockSignals(False)
        self.changed.emit()

    def current(self):
        models = getattr(self, "models", [])
        faces = getattr(self, "faces", [])
        si = self.source_combo.currentIndex() - 1
        fi = self.face_combo.currentIndex() - 1
        source = models[si] if 0 <= si < len(models) else None
        plane, name = faces[fi] if 0 <= fi < len(faces) else (None, None)
        return source, plane, name

    def highlight(self):
        _, plane, name = self.current()
        if plane is not None:
            plane.Visibility = True
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(plane, name)
