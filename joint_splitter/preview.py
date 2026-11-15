"""Transient Coin graphics: preview and dimension guides never enter the tree."""
import math
import FreeCAD as App
from pivy import coin
from .geometry import plane_frame, section_info, REVERSED_TYPES, in_frame


HINTS = {
    "kind": "노란 접합부가 선택한 조인트 형상입니다.",
    "width": "폭: 절단면의 로컬 X 방향 전체 범위입니다. 원형 조인트에서는 외경 제한입니다.",
    "length": "길이: 절단면의 로컬 Y 방향 범위입니다. 원형 조인트에서는 외경 제한입니다.",
    "depth": "깊이: 절단면에서 수 조인트 끝까지의 돌출 길이입니다.",
    "clearance": "노란 면에 공차를 적용합니다. 입력값은 암수 표면 사이의 간격이며, 홈 총 폭 차이는 두 배입니다.",
    "count": "노란 숫자와 표시가 각 핑거의 위치입니다. 개수는 폭 안의 핑거 수입니다.",
    "angle": "노란 선: 도브테일 옆면과 수직 방향 사이의 벌어짐 각도입니다.",
    "rotation": "노란 원호: 절단면 안에서 조인트 전체를 회전하는 방향입니다.",
    "offset_x": "노란 선: 단면 중심에서 조인트 중심까지 로컬 X 방향 이동입니다.",
    "offset_y": "노란 선: 단면 중심에서 조인트 중심까지 로컬 Y 방향 이동입니다.",
    "reverse": "노란 화살표: 수 조인트가 돌출하는 방향입니다.",
}


def material(color, transparency=0):
    node = coin.SoMaterial()
    node.diffuseColor = color
    node.emissiveColor = tuple(c*0.12 for c in color)
    node.transparency = transparency
    return node


def mesh(shape, color, transparency=0):
    group = coin.SoSeparator()
    group.addChild(material(color, transparency))
    hints = coin.SoShapeHints()
    hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
    hints.shapeType = coin.SoShapeHints.SOLID
    hints.creaseAngle = 0.5
    group.addChild(hints)
    points, triangles = shape.tessellate(max(shape.BoundBox.DiagonalLength*0.001, 0.03))
    coordinates = coin.SoCoordinate3()
    coordinates.point.setValues(0, len(points), [(p.x,p.y,p.z) for p in points])
    faces = coin.SoIndexedFaceSet()
    indices = [value for tri in triangles for value in (*tri, -1)]
    faces.coordIndex.setValues(0, len(indices), indices)
    group.addChild(coordinates)
    group.addChild(faces)
    return group


def line(points, color=(1.0,0.85,0.05), width=4):
    group = coin.SoSeparator()
    color_node = coin.SoBaseColor()
    color_node.rgb = color
    style = coin.SoDrawStyle()
    style.lineWidth = width
    group.addChild(color_node)
    group.addChild(style)
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, len(points), [(p.x,p.y,p.z) for p in points])
    path = coin.SoLineSet()
    path.numVertices.setValue(len(points))
    group.addChild(coords)
    group.addChild(path)
    return group


def label(point, text):
    group = coin.SoSeparator()
    color = coin.SoBaseColor()
    color.rgb = (1.0,0.85,0.05)
    group.addChild(color)
    translation = coin.SoTranslation()
    translation.translation = (point.x,point.y,point.z)
    group.addChild(translation)
    font = coin.SoFont()
    font.name = "sans"
    font.size = 15
    group.addChild(font)
    node = coin.SoText2()
    node.string.setValue(text)
    group.addChild(node)
    return group


class JointPreview:
    def __init__(self, view, source, plane):
        self.view, self.source, self.plane = view, source, plane
        self.root = coin.SoSeparator()
        # Do not let preview geometry interfere with point/face picking.
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        self.root.addChild(pick)
        self.parts_node = coin.SoSeparator()
        self.surface_node = coin.SoSeparator()
        self.guide_node = coin.SoAnnotation()
        self.root.addChild(self.parts_node)
        self.root.addChild(self.surface_node)
        self.root.addChild(self.guide_node)
        self.scene = self.view.getSceneGraph()
        self.scene.addChild(self.root)
        self.original_visibility = [(obj, obj.Visibility) for obj in (source, plane)]
        self.pair = None
        self.parameters = None
        self.separation = 0
        self.active = "kind"
        self.parts_cache = []

    def dispose(self):
        if self.root is not None:
            try:
                if self.scene.findChild(self.root) >= 0:
                    self.scene.removeChild(self.root)
            except (RuntimeError, ReferenceError):
                pass
            self.root = None
            self.scene = None
        for obj, visible in self.original_visibility:
            try:
                obj.Visibility = visible
            except (RuntimeError, ReferenceError):
                pass
        self.original_visibility = []

    def show(self, pair, source_shape, face, parameters, separation, active):
        self.pair, self.parameters = pair, parameters
        self.active = active
        self.frame = plane_frame(face)
        flipped = parameters.reverse ^ (parameters.kind in REVERSED_TYPES)
        self.male_index = 1 if flipped else 0
        if flipped:
            self.frame.Rotation = self.frame.Rotation.multiply(App.Rotation(App.Vector(0,1,0),180))
        self.frame.Rotation = self.frame.Rotation.multiply(App.Rotation(App.Vector(0,0,1), parameters.rotation))
        _, bb = section_info(source_shape, self.frame)
        self.section_center = App.Vector(bb.Center.x, bb.Center.y, 0)
        self.center = self.section_center + App.Vector(parameters.offset_x,parameters.offset_y,0)
        self.normal = plane_frame(face).Rotation.multVec(App.Vector(0,0,1))
        self.parts_node.removeAllChildren()
        self.parts_cache = []
        for index, shape in enumerate(pair):
            node = coin.SoSeparator()
            translation = coin.SoTranslation()
            node.addChild(translation)
            node.addChild(mesh(shape, (0.25,0.61,0.85) if index == 0 else (0.96,0.65,0.24)))
            self.parts_node.addChild(node)
            self.parts_cache.append(translation)
        self.source.Visibility = False
        self.plane.Visibility = False
        self.set_separation(separation)

    def displacement(self, index):
        return self.normal * (self.separation/2 * (-1 if index == 0 else 1))

    def set_separation(self, value):
        self.separation = value
        for index, translation in enumerate(self.parts_cache):
            vector = self.displacement(index)
            translation.translation = (vector.x,vector.y,vector.z)
        self.highlight(self.active)

    def world(self, point, index=None):
        index = self.male_index if index is None else index
        return self.frame.multVec(point) + self.displacement(index)

    def highlight(self, active):
        self.active = active
        self.guide_node.removeAllChildren()
        self.surface_node.removeAllChildren()
        if self.pair is None:
            return
        p = self.parameters
        center = self.center
        w, length, d = p.width, p.length, p.depth
        margin = max(w,length,d)*0.12
        local_points = []
        text = ""
        if active == "width":
            local_points = [center+App.Vector(x,-length/2-margin,d) for x in (-w/2,w/2)]
            text = "Width: {:.3f} mm".format(w)
        elif active == "length":
            local_points = [center+App.Vector(w/2+margin,y,d) for y in (-length/2,length/2)]
            text = "Length: {:.3f} mm".format(length)
        elif active in ("depth","reverse"):
            local_points = [center+App.Vector(w/2+margin,0,z) for z in (0,d)]
            text = "Depth: {:.3f} mm".format(d) if active == "depth" else "Male direction"
        elif active in ("offset_x","offset_y"):
            axis = App.Vector(p.offset_x,0,0) if active == "offset_x" else App.Vector(0,p.offset_y,0)
            start = self.section_center + App.Vector(0,0,d+margin)
            local_points = [start, start+axis]
            text = "{}: {:.3f} mm".format(active, p.offset_x if active == "offset_x" else p.offset_y)
        elif active == "rotation":
            radius = max(w,length)/2+margin
            local_points = [center+App.Vector(radius*math.cos(a),radius*math.sin(a),d)
                            for a in (i*math.pi/36 for i in range(37))]
            text = "Rotation: {:.1f} deg".format(p.rotation)
        elif active == "angle" and p.kind == "Dovetail":
            x = -w/2 + w/p.count*0.5 + w/p.count*0.25
            local_points = [center+App.Vector(x,-length/2,d),center+App.Vector(x,-length/2,0),
                            center+App.Vector(x+d*math.tan(math.radians(p.angle)),-length/2,d)]
            text = "Angle: {:.1f} deg".format(p.angle)
        elif active == "count" and p.kind in ("Dovetail","Finger Joint"):
            for i in range(p.count):
                point = center+App.Vector(-w/2+w/p.count*(i+0.5),0,d+margin)
                self.guide_node.addChild(label(self.world(point),str(i+1)))
        if local_points:
            points = [self.world(point) for point in local_points]
            self.guide_node.addChild(line(points))
            self.guide_node.addChild(label(points[-1],text))
            # Endpoint ticks make the measured span visible from oblique views.
            tick = self.frame.Rotation.multVec(App.Vector(margin*0.25,margin*0.25,0))
            for point in (points[0],points[-1]):
                self.guide_node.addChild(line([point-tick,point+tick]))
        # Highlight actual mating faces, rather than a screen-space illustration.
        if active in ("clearance","kind","count","angle","reverse"):
            for index, shape in enumerate(self.pair):
                for face in shape.Faces:
                    bb = in_frame(face,self.frame).BoundBox
                    if bb.ZMin >= -p.clearance/2-1e-4 and bb.ZMax <= d+p.clearance+1e-4:
                        glow = face.copy()
                        glow.translate(self.displacement(index))
                        self.surface_node.addChild(mesh(glow,(1.0,0.85,0.05),0.25))
            if active == "clearance":
                self.guide_node.addChild(label(self.world(center+App.Vector(0,0,d+margin)),
                                              "Gap: {:.3f} mm".format(p.clearance)))
