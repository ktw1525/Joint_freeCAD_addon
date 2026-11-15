"""BRep operations; imports no GUI code, so this also runs in FreeCAD's Python."""
from dataclasses import dataclass, replace
import math

import FreeCAD as App
import Part

JOINT_TYPES = ("Mortise", "Tenon", "Dovetail", "Finger Joint", "Cantilever",
               "Cantilever Mate", "Annular", "Annular Mate")
REVERSED_TYPES = {"Mortise", "Cantilever Mate", "Annular Mate"}
EPS = 1e-7


class JointError(ValueError):
    pass


@dataclass
class Parameters:
    kind: str = "Tenon"
    clearance: float = 0.2
    width: float = 16.0
    length: float = 8.0
    depth: float = 5.0
    count: int = 4
    angle: float = 12.0
    rotation: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    reverse: bool = False

    def validate(self):
        if self.kind not in JOINT_TYPES:
            raise JointError("지원하지 않는 조인트 종류입니다.")
        values = (self.clearance, self.width, self.length, self.depth, self.angle,
                  self.rotation, self.offset_x, self.offset_y)
        if not all(math.isfinite(v) for v in values):
            raise JointError("모든 치수는 유한한 숫자여야 합니다.")
        if min(self.width, self.length, self.depth) <= 0 or self.clearance < 0:
            raise JointError("크기는 0보다 크고 공차는 0 이상이어야 합니다.")
        if self.clearance >= min(self.width, self.length, self.depth) / 3:
            raise JointError("공차가 조인트에 비해 너무 큽니다. 치수의 1/3 미만으로 지정하세요.")
        if not isinstance(self.count, int) or not 1 <= self.count <= 64:
            raise JointError("핑거 수는 1~64의 정수여야 합니다.")
        if not 0 < self.angle < 40:
            raise JointError("도브테일 각도는 0도 초과, 40도 미만이어야 합니다.")


def require_solid(shape, message):
    if shape.isNull() or not shape.isValid() or len(shape.Solids) != 1 or shape.Volume <= EPS:
        raise JointError(message)
    return shape.Solids[0]


def minimum_distance(first, second):
    """OCCT's extrema solver can fail in one argument order on trimmed cylinders."""
    try:
        return first.distToShape(second)[0]
    except (RuntimeError, Part.OCCError):
        return second.distToShape(first)[0]


def world_shape(obj, subname=""):
    """getShape accounts for Body/App::Part/Link ancestor placements."""
    return Part.getShape(obj, subname, needSubElement=bool(subname))


def plane_frame(face):
    if face.ShapeType != "Face" or not isinstance(face.Surface, Part.Plane):
        raise JointError("절단 도구의 평면 Face를 선택하세요. 곡면은 사용할 수 없습니다.")
    u0, u1, v0, v1 = face.ParameterRange
    normal = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    normal.normalize()
    x = App.Vector(1, 0, 0)
    if abs(x.dot(normal)) > 0.95:
        x = App.Vector(0, 1, 0)
    x = x - normal * x.dot(normal)
    x.normalize()
    y = normal.cross(x)
    return App.Placement(face.CenterOfMass, App.Rotation(x, y, normal, "ZXY"))


def in_frame(shape, frame):
    result = shape.copy()
    result.Placement = frame.inverse().multiply(result.Placement)
    return result


def _box(x, y, z, px, py, pz):
    return Part.makeBox(x, y, z, App.Vector(px, py, pz))


def _halfspace(bb, z, positive):
    margin = max(bb.DiagonalLength, 1.0)
    bottom, top = (z, bb.ZMax + margin) if positive else (bb.ZMin - margin, z)
    if top <= bottom:
        raise JointError("절단면이 모델 내부를 지나지 않습니다.")
    return _box(bb.XLength + 2 * margin, bb.YLength + 2 * margin,
                top - bottom, bb.XMin - margin, bb.YMin - margin, bottom)


def section_info(source, frame):
    local = in_frame(source, frame)
    bb = local.BoundBox
    if bb.ZMin >= -EPS or bb.ZMax <= EPS:
        raise JointError("절단면이 모델 내부를 통과해야 합니다.")
    margin = max(bb.DiagonalLength, 1.0)
    plane = Part.makePlane(bb.XLength + 2 * margin, bb.YLength + 2 * margin,
                           App.Vector(bb.XMin - margin, bb.YMin - margin, 0))
    section = local.section(plane)
    if section.isNull() or not section.Edges:
        raise JointError("모델과 절단면의 교선을 찾지 못했습니다.")
    return local, section.BoundBox


def defaults(source, face):
    local, bb = section_info(source, plane_frame(face))
    small = min(bb.XLength, bb.YLength)
    return Parameters(width=max(bb.XLength * 0.55, 0.01),
                      length=max(bb.YLength * 0.40, 0.01),
                      depth=max(min(-local.BoundBox.ZMin, local.BoundBox.ZMax, small) * 0.3, 0.01),
                      clearance=min(0.2, small * 0.01))


def _prism_xz(points, length):
    vertices = [App.Vector(x, -length / 2, z) for x, z in points]
    wire = Part.makePolygon(vertices + [vertices[0]])
    return Part.Face(wire).extrude(App.Vector(0, length, 0))


def _union(shapes):
    result = shapes[0]
    for other in shapes[1:]:
        result = result.fuse(other)
    return result.removeSplitter()


def joint_tools(p):
    """Return (male, cavity envelope). Snap slots are never filled by the mate."""
    w, length, d = p.width, p.length, p.depth
    anchor = max(d * 0.25, p.clearance * 2, 0.01)
    if p.kind in ("Tenon", "Mortise"):
        # Capsule plan, with its long axis on X (a circle when w <= length).
        r = min(w, length) / 2
        span = max(0, w - 2 * r)
        parts = [Part.makeCylinder(r, d + anchor, App.Vector(x, 0, -anchor))
                 for x in (-span / 2, span / 2)]
        if span > EPS:
            parts.append(_box(span, 2 * r, d + anchor, -span / 2, -r, -anchor))
        male = _union(parts)
        if length > w:
            # Honour both dimensions for tall capsules as well.
            male = _union([Part.makeCylinder(w / 2, d + anchor,
                           App.Vector(0, y, -anchor)) for y in (-(length-w)/2, (length-w)/2)] +
                          [_box(w, length-w, d+anchor, -w/2, -(length-w)/2, -anchor)])
        return male, male.copy()
    if p.kind in ("Finger Joint", "Dovetail"):
        pitch = w / p.count
        finger = pitch * 0.5
        flare = d * math.tan(math.radians(p.angle)) if p.kind == "Dovetail" else 0
        if finger + 2 * flare + 2 * p.clearance >= pitch:
            raise JointError("핑거가 서로 겹칩니다. 개수·깊이·각도·공차를 줄이거나 폭을 늘리세요.")
        shapes = []
        for index in range(p.count):
            x = -w / 2 + pitch * (index + 0.5)
            shapes.append(_prism_xz([(x-finger/2, -anchor), (x+finger/2, -anchor),
                          (x+finger/2, 0), (x+finger/2+flare, d),
                          (x-finger/2-flare, d), (x-finger/2, 0)], length))
        male = _union(shapes)
        return male, male.copy()
    if p.kind in ("Cantilever", "Cantilever Mate"):
        # A beam rising along Z, with a retaining shoulder and a sloping nose.
        stem = w * 0.60
        male = _prism_xz([(-w/2, -anchor), (-w/2+stem, -anchor),
                         (-w/2+stem, d*0.65), (w/2, d*0.65),
                         (-w/2+stem*0.75, d), (-w/2, d)], length)
        # Give the beam room to bend away from the retaining shoulder.
        relief = _box(w*0.42, length, d*0.85, -w/2-w*0.40, -length/2, 0)
        return male, male.fuse(relief).removeSplitter()
    # Slotted annular snap: annular wall with a retaining lip and tapered lead-in.
    radius = min(w, length) / 2
    inner = radius * 0.52
    shaft = radius * 0.78
    lower = Part.makeCylinder(shaft, d*0.65+anchor, App.Vector(0, 0, -anchor))
    nose = Part.makeCone(radius, shaft*0.9, d*0.35, App.Vector(0, 0, d*0.65))
    envelope = lower.fuse(nose).removeSplitter()
    male = envelope.cut(Part.makeCylinder(inner, d+anchor+1, App.Vector(0, 0, -anchor)))
    slot = max(radius * 0.12, 0.05)
    for angle in (0, 90, 180, 270):
        tool = _box(radius*1.2, slot, d, 0, -slot/2, d*0.20)
        tool.rotate(App.Vector(), App.Vector(0, 0, 1), angle)
        male = male.cut(tool)
    return male.removeSplitter(), envelope


def split(source, face, parameters):
    """Return two world-space solids; input shapes are never modified.

    Clearance is the distance between opposing surfaces: planar halves are
    separated by c; the female tool is a normal outward offset of c.
    """
    p = replace(parameters)
    p.validate()
    require_solid(source, "모델은 유효한 단일 솔리드여야 합니다. 메시·열린 셸·여러 솔리드는 변환하세요.")
    frame = plane_frame(face)
    flipped = p.reverse ^ (p.kind in REVERSED_TYPES)
    if flipped:
        frame.Rotation = frame.Rotation.multiply(App.Rotation(App.Vector(0, 1, 0), 180))
    frame.Rotation = frame.Rotation.multiply(App.Rotation(App.Vector(0, 0, 1), p.rotation))
    local, section = section_info(source, frame)
    bb = local.BoundBox
    c = p.clearance
    negative = local.common(_halfspace(bb, -c/2, False))
    positive = local.common(_halfspace(bb, c/2, True))
    require_solid(negative, "절단 후 첫 파트가 여러 조각입니다. 절단면 위치를 바꾸세요.")
    require_solid(positive, "절단 후 두 번째 파트가 여러 조각입니다. 절단면 위치를 바꾸세요.")
    male, envelope = joint_tools(p)
    if p.kind == "Dovetail":
        # A blind, undercut pocket cannot accept a rigid dovetail. Open the
        # female grooves through both Y ends so the pair can slide together.
        _, envelope = joint_tools(replace(p, length=bb.YLength + bb.DiagonalLength*4))
    translation = App.Vector(section.Center.x + p.offset_x, section.Center.y + p.offset_y, 0)
    male.translate(translation)
    envelope.translate(translation)
    volume_tol = max(source.Volume * 1e-9, 1e-6)
    if male.cut(local).Volume > volume_tol:
        raise JointError("조인트가 모델 외부 또는 빈 공간으로 나갑니다. 폭·길이·깊이를 줄이거나 위치를 조정하세요.")
    cavity = envelope.copy()
    if c > 0:
        # Offset separate fingers independently to avoid compound-offset ambiguity.
        expanded = [s.makeOffsetShape(c, EPS, join=0) for s in envelope.Solids]
        cavity = _union(expanded)
    if cavity.isNull() or not cavity.isValid() or not cavity.Solids:
        raise JointError("공차 형상 생성에 실패했습니다. 공차나 조인트 치수를 조정하세요.")
    first = negative.fuse(male).removeSplitter()
    second = positive.cut(cavity).removeSplitter()
    first = require_solid(first, "수 조인트가 파트에 연결되지 않습니다. 위치나 치수를 조정하세요.")
    second = require_solid(second, "암 조인트가 파트를 관통하거나 분리했습니다. 치수를 줄이세요.")
    if first.cut(negative).Volume < volume_tol or positive.cut(second).Volume < volume_tol:
        raise JointError("맞물리는 영역이 없습니다. 절단면과 조인트 위치를 조정하세요.")
    if first.common(second).Volume > volume_tol:
        raise JointError("파트 사이에 겹침이 생겼습니다. 조인트 치수를 조정하세요.")
    if c > 0 and minimum_distance(first, second) < c - max(1e-5, c*1e-4):
        raise JointError("요청한 최소 간격을 확보하지 못했습니다. 조인트 치수를 조정하세요.")
    for shape in (first, second):
        shape.Placement = frame.multiply(shape.Placement)
    # Part 1 always denotes the negative side of the original plane normal.
    return (second, first) if flipped else (first, second)
