"""Run with FreeCAD's Python; generate the parametric demo and real BRep meshes."""
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import FreeCAD as App
import FreeCADGui as Gui
import Part
sys.path.insert(0, str(root))
from joint_splitter.geometry import Parameters, JOINT_TYPES, split
from joint_splitter.features import create_split

Gui.showMainWindow()
doc = App.newDocument("JointSplitterDemo")
model = doc.addObject("Part::Feature", "DemoModel")
model.Label = "예제 모델 60 × 40 × 30 mm"
model.Shape = Part.makeBox(60, 40, 30, App.Vector(-30, -20, -15))
plane = doc.addObject("Part::Plane", "CuttingPlane")
plane.Label = "예제 절단면"
plane.Length = 80
plane.Width = 60
plane.Placement.Base = App.Vector(-40, -30, 0)
doc.recompute()
create_split(doc, model, plane, "Face1", Parameters())
Gui.activeDocument().activeView().viewAxonometric()
doc.recompute()
doc.saveAs(str(root / "examples" / "JointSplitterDemo.FCStd"))
App.closeDocument(doc.Name)

source = Part.makeBox(40, 28, 20, App.Vector(-20, -14, -10))
face = Part.makePlane(80, 80, App.Vector(-40, -40, 0))
gallery = []
for kind in JOINT_TYPES:
    pair = split(source, face, Parameters(kind=kind, width=24, length=12, depth=4, count=3))
    item = {"name": kind, "parts": []}
    for index, shape in enumerate(pair):
        if index:
            shape.rotate(App.Vector(), App.Vector(1, 0, 0), 180)
        shape.translate(App.Vector((-25 if index == 0 else 25), 0, 0))
        vertices, triangles = shape.tessellate(0.08)
        item["parts"].append({"vertices": [[v.x, v.y, v.z] for v in vertices], "triangles": triangles})
    gallery.append(item)
(root / "build").mkdir(exist_ok=True)
(root / "build" / "gallery.json").write_text(json.dumps(gallery), encoding="utf-8")
Gui.getMainWindow().close()
print("Generated examples/JointSplitterDemo.FCStd and build/gallery.json")
