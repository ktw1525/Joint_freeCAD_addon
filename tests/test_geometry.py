"""Run with FreeCAD's bundled Python: python tests/test_geometry.py -v."""
import os
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import FreeCAD as App
import Part
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from joint_splitter.geometry import Parameters, JOINT_TYPES, JointError, split, defaults, plane_frame, world_shape, minimum_distance
from joint_splitter.features import create_split


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.source = Part.makeBox(60, 40, 30, App.Vector(-30, -20, -15))
        self.plane = Part.makePlane(100, 100, App.Vector(-50, -50, 0))
        self.params = Parameters(width=24, length=12, depth=4, count=3)

    def check_pair(self, pair, clearance):
        a, b = pair
        for part in pair:
            self.assertTrue(part.isValid())
            self.assertEqual(len(part.Solids), 1)
            self.assertGreater(part.Volume, 0)
            self.assertLess(part.cut(self.source).Volume, 1e-5)
        self.assertLess(a.common(b).Volume, 1e-5)
        self.assertAlmostEqual(minimum_distance(a, b), clearance, places=5)

    def test_mortise_swaps_physical_sides(self):
        tenon = split(self.source, self.plane, self.params)
        mortise = split(self.source, self.plane, replace(self.params, kind="Mortise"))
        # Part 1 remains below the original plane, with a cavity instead of a pin.
        self.assertGreater(tenon[0].BoundBox.ZMax, 0)
        self.assertLess(mortise[0].BoundBox.ZMax, 0)

    def test_all_joint_types_and_clearances(self):
        for kind in JOINT_TYPES:
            for clearance in (0, 0.2):
                with self.subTest(kind=kind, clearance=clearance):
                    p = replace(self.params, kind=kind, clearance=clearance)
                    pair = split(self.source, self.plane, p)
                    self.check_pair(pair, clearance)
                    if kind in ("Mortise", "Tenon", "Finger Joint") and clearance == 0:
                        self.assertAlmostEqual(sum(s.Volume for s in pair), self.source.Volume, places=4)

    def test_rotated_plane_and_model(self):
        move = App.Placement(App.Vector(123, -37, 80), App.Rotation(App.Vector(1, 2, 3), 37))
        self.source.Placement = move.multiply(self.source.Placement)
        self.plane.Placement = move.multiply(self.plane.Placement)
        self.check_pair(split(self.source, self.plane, self.params), 0.2)

    def test_oblique_cut(self):
        self.plane.rotate(App.Vector(), App.Vector(1, 0, 0), 24)
        self.check_pair(split(self.source, self.plane, self.params), 0.2)

    def test_offsets_rotation_and_reverse(self):
        p = replace(self.params, offset_x=4, offset_y=-3, rotation=25, reverse=True)
        self.check_pair(split(self.source, self.plane, p), 0.2)

    def test_small_finite_plane_is_extended(self):
        tiny = Part.makePlane(0.1, 0.1)
        self.check_pair(split(self.source, tiny, self.params), 0.2)

    def test_nonintersecting_plane(self):
        self.plane.translate(App.Vector(0, 0, 40))
        with self.assertRaises(JointError):
            split(self.source, self.plane, self.params)

    def test_curved_face(self):
        curved = Part.makeSphere(10).Faces[0]
        with self.assertRaises(JointError):
            plane_frame(curved)

    def test_invalid_inputs(self):
        for changes in ({"clearance": -0.1}, {"width": 0}, {"depth": float("nan")},
                        {"count": 0}, {"kind": "unknown"}, {"clearance": 4},
                        {"count": 3.2}):
            with self.subTest(changes=changes), self.assertRaises(JointError):
                split(self.source, self.plane, replace(self.params, **changes))
        with self.assertRaises(JointError):
            other = self.source.copy()
            other.translate(App.Vector(100, 0, 0))
            split(Part.makeCompound([self.source, other]), self.plane, self.params)

    def test_fingers_overlap(self):
        with self.assertRaises(JointError):
            split(self.source, self.plane, replace(self.params, kind="Dovetail", count=16))

    def test_joint_outside_source(self):
        with self.assertRaises(JointError):
            split(self.source, self.plane, replace(self.params, offset_x=100))

    def test_hollow_source_rejects_joint_in_empty_space(self):
        hollow = self.source.cut(Part.makeCylinder(15, 40, App.Vector(0, 0, -20)))
        with self.assertRaises(JointError):
            split(hollow, self.plane, self.params)

    def test_defaults(self):
        p = defaults(self.source, self.plane)
        for kind in JOINT_TYPES:
            with self.subTest(kind=kind):
                self.check_pair(split(self.source, self.plane, replace(p, kind=kind)), p.clearance)


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("JointSplitterTests")
        self.source = self.doc.addObject("Part::Feature", "Source")
        self.source.Shape = Part.makeBox(60, 40, 30, App.Vector(-30, -20, -15))
        self.plane = self.doc.addObject("Part::Plane", "Plane")
        self.plane.Length = 100
        self.plane.Width = 100
        self.plane.Placement.Base = App.Vector(-50, -50, 0)
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_recompute_save_reopen(self):
        _, result, parts = create_split(self.doc, self.source, self.plane, "Face1", Parameters())
        original = parts[1].Shape.Volume
        result.Clearance = 0.4
        self.doc.recompute()
        self.assertLess(parts[1].Shape.Volume, original)
        self.assertAlmostEqual(parts[0].Shape.distToShape(parts[1].Shape)[0], 0.4, places=5)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "roundtrip.FCStd")
            self.doc.saveAs(path)
            name = self.doc.Name
            App.closeDocument(name)
            self.doc = App.openDocument(path)
            self.doc.JointParameters.Depth = 6
            self.doc.recompute()
            self.assertTrue(self.doc.JointPart1.Shape.isValid())
            self.assertEqual(len(self.doc.JointParameters.Shape.Solids), 2)
            self.assertAlmostEqual(self.doc.JointPart1.Shape.distToShape(self.doc.JointPart2.Shape)[0], 0.4, places=5)

    def test_container_global_placements(self):
        container = self.doc.addObject("App::Part", "Container")
        container.addObject(self.source)
        container.addObject(self.plane)
        container.Placement = App.Placement(App.Vector(70, 80, 90), App.Rotation(App.Vector(1, 1, 0), 35))
        self.doc.recompute()
        _, _, parts = create_split(self.doc, self.source, self.plane, "Face1", Parameters())
        global_source = world_shape(self.source)
        for part in parts:
            self.assertLess(part.Shape.cut(global_source).Volume, 1e-5)
        self.assertAlmostEqual(parts[0].Shape.distToShape(parts[1].Shape)[0], 0.2, places=5)

    def test_failed_creation_leaves_document_unchanged(self):
        names = [obj.Name for obj in self.doc.Objects]
        with self.assertRaises(JointError):
            create_split(self.doc, self.source, self.plane, "Face1", Parameters(depth=100))
        self.assertEqual([obj.Name for obj in self.doc.Objects], names)

    def test_plane_edit_updates_parts(self):
        _, result, parts = create_split(self.doc, self.source, self.plane, "Face1", Parameters())
        volume = parts[0].Shape.Volume
        self.plane.Placement.Base.z = 2
        self.doc.recompute()
        self.assertNotAlmostEqual(parts[0].Shape.Volume, volume)
        self.assertEqual(len(result.Shape.Solids), 2)

    def test_invalid_recompute_clears_outputs_then_recovers(self):
        _, result, parts = create_split(self.doc, self.source, self.plane, "Face1", Parameters())
        result.Depth = 100
        self.doc.recompute()
        self.assertTrue(result.Shape.isNull())
        self.assertTrue(all(part.Shape.isNull() for part in parts))
        self.assertTrue(result.Status.startswith("오류:"))
        result.Depth = 5
        self.doc.recompute()
        self.assertTrue(all(len(part.Shape.Solids) == 1 for part in parts))


if __name__ == "__main__":
    unittest.main()
