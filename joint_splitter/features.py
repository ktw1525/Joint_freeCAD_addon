"""Persistent parametric document features, including two exportable parts."""
import FreeCAD as App
import Part
from .geometry import Parameters, JOINT_TYPES, JointError, split, world_shape

FIELDS = {
    "clearance": ("Clearance", "App::PropertyLength", "맞닿는 면 사이의 최소 간격 (mm)"),
    "width": ("Width", "App::PropertyLength", "조인트 전체 폭 / annular 외경"),
    "length": ("Length", "App::PropertyLength", "조인트 길이 / annular 외경 제한"),
    "depth": ("Depth", "App::PropertyLength", "절단면 위로 돌출하는 깊이"),
    "count": ("Count", "App::PropertyInteger", "핑거 개수"),
    "angle": ("DovetailAngle", "App::PropertyAngle", "도브테일 벌어짐 각도"),
    "rotation": ("Rotation", "App::PropertyAngle", "절단면 내 회전"),
    "offset_x": ("OffsetX", "App::PropertyDistance", "단면 중심 기준 X 이동"),
    "offset_y": ("OffsetY", "App::PropertyDistance", "단면 중심 기준 Y 이동"),
    "reverse": ("Reverse", "App::PropertyBool", "암수 방향 뒤집기"),
}


def read_parameters(obj):
    values = {key: getattr(obj, spec[0]) for key, spec in FIELDS.items()}
    values = {key: (value.Value if hasattr(value, "Value") else value)
              for key, value in values.items()}
    return Parameters(kind=str(obj.JointType), **values)


class SplitFeature:
    def __init__(self, obj, source, plane, face_name, parameters):
        obj.addProperty("App::PropertyLink", "Source", "Inputs").Source = source
        obj.addProperty("App::PropertyLinkSub", "CuttingFace", "Inputs").CuttingFace = (plane, [face_name])
        obj.addProperty("App::PropertyEnumeration", "JointType", "Joint").JointType = list(JOINT_TYPES)
        obj.JointType = parameters.kind
        for key, (name, type_id, help_text) in FIELDS.items():
            obj.addProperty(type_id, name, "Joint", help_text)
            setattr(obj, name, getattr(parameters, key))
        obj.addProperty("App::PropertyString", "Status", "Result")
        obj.setEditorMode("Status", 1)
        obj.Proxy = self

    def execute(self, obj):
        try:
            if obj.Source is None or not obj.CuttingFace or obj.CuttingFace[0] is None:
                raise JointError("원본 모델 또는 절단면이 없습니다.")
            plane, names = obj.CuttingFace
            if not names:
                raise JointError("절단 Face 참조가 없습니다.")
            a, b = split(world_shape(obj.Source), world_shape(plane, names[0]), read_parameters(obj))
            obj.Shape = Part.makeCompound([a, b])
            obj.Status = "완료: 2 solids"
        except Exception as exc:
            obj.Shape = Part.Shape()
            obj.Status = "오류: " + str(exc)
            App.Console.PrintError("Joint Splitter: {}\n".format(exc))
            # Finish recomputing so dependent OutputParts also clear their shapes.
            # Raising here would cause FreeCAD to skip them and retain stale solids.

    def dumps(self):
        return None

    def loads(self, state):
        pass


class OutputPart:
    def __init__(self, obj, result, index):
        obj.addProperty("App::PropertyLink", "Result", "Joint Splitter").Result = result
        obj.addProperty("App::PropertyInteger", "PartIndex", "Joint Splitter").PartIndex = index
        obj.setEditorMode("PartIndex", 1)
        obj.Proxy = self

    def execute(self, obj):
        if obj.Result is None or obj.Result.Shape.isNull():
            obj.Shape = Part.Shape()
            return
        obj.Shape = obj.Result.Shape.childShapes()[obj.PartIndex].copy()

    def dumps(self):
        return None

    def loads(self, state):
        pass


class ViewProvider:
    def __init__(self, obj):
        obj.Proxy = self

    def attach(self, obj):
        pass

    def getIcon(self):
        import os
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "joint_splitter.svg")

    def dumps(self):
        return None

    def loads(self, state):
        pass


def create_split(doc, source, plane, face_name, parameters):
    if source is plane:
        raise JointError("모델과 절단면은 서로 다른 객체여야 합니다.")
    if source.Document is not doc or plane.Document is not doc:
        raise JointError("같은 문서의 모델과 절단면을 선택하세요.")
    # Validate before mutating the document; no half-created results on failure.
    split(world_shape(source), world_shape(plane, face_name), parameters)
    doc.openTransaction("조인트 분할")
    try:
        group = doc.addObject("App::DocumentObjectGroup", "JointSplit")
        group.Label = source.Label + " · " + parameters.kind
        result = doc.addObject("Part::FeaturePython", "JointParameters")
        result.Label = "조인트 설정 (수정 가능)"
        SplitFeature(result, source, plane, face_name, parameters)
        group.addObject(result)
        parts = []
        for index in range(2):
            part = doc.addObject("Part::FeaturePython", "JointPart" + str(index+1))
            part.Label = source.Label + " · Part " + str(index+1)
            OutputPart(part, result, index)
            group.addObject(part)
            parts.append(part)
        doc.recompute()
        if result.Shape.isNull() or any(part.Shape.isNull() for part in parts):
            raise JointError(result.Status or "분할 결과를 만들지 못했습니다.")
        if App.GuiUp:
            for obj in [result] + parts:
                ViewProvider(obj.ViewObject)
            parts[0].ViewObject.ShapeColor = (0.30, 0.64, 0.87)
            parts[1].ViewObject.ShapeColor = (0.96, 0.68, 0.31)
            source.Visibility = False
            plane.Visibility = False
            result.Visibility = False
        doc.commitTransaction()
        return group, result, parts
    except Exception:
        doc.abortTransaction()
        raise
