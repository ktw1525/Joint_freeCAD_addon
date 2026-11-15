"""Offscreen smoke tests using the installed FreeCAD/Qt GUI, not UI automation."""
import os
import sys
import unittest
from unittest.mock import Mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import FreeCAD as App
import FreeCADGui as Gui
import Part

Gui.showMainWindow()
# FreeCAD prepends installed Mod folders while initializing; test this checkout.
sys.path.insert(0, str(ROOT))
for module_name in list(sys.modules):
    if module_name == "joint_splitter" or module_name.startswith("joint_splitter."):
        del sys.modules[module_name]
from joint_splitter.commands import QtWidgets, QtGui, QtCore, SplitDialog, PlaneDialog, selected_inputs, register
from joint_splitter.geometry import JointError, JOINT_TYPES, minimum_distance
from joint_splitter.inputs import document_inputs
from joint_splitter.planes import DrawPlaneTask, frame_from_points

# The offscreen platform does not discover Windows system fonts automatically.
QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/malgun.ttf")
QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/malgunbd.ttf")
QtWidgets.QApplication.setFont(QtGui.QFont("Malgun Gothic", 10))


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("GuiSmoke")
        self.source = self.doc.addObject("Part::Feature", "Source")
        self.source.Label = "테스트 모델"
        self.source.Shape = Part.makeBox(60, 40, 30, App.Vector(-30, -20, -15))
        self.plane = self.doc.addObject("Part::Plane", "Plane")
        self.plane.Length = 100
        self.plane.Width = 100
        self.plane.Placement.Base = App.Vector(-50, -50, 0)
        self.doc.recompute()
        Gui.Selection.clearSelection()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_workbench_loads(self):
        # FreeCAD executes InitGui.py without injecting __file__ into its globals.
        if "JointSplitterWorkbench" in Gui.listWorkbenches():
            Gui.removeWorkbench("JointSplitterWorkbench")
        startup_globals = {}
        exec(compile((ROOT / "InitGui.py").read_text(encoding="utf-8"), str(ROOT / "InitGui.py"), "exec"),
             startup_globals)
        self.assertNotIn("__file__", startup_globals)
        Gui.activateWorkbench("JointSplitterWorkbench")
        self.assertIn("JointSplitterWorkbench", Gui.listWorkbenches())
        self.assertTrue(Path(startup_globals["JointSplitterWorkbench"].Icon).is_file())
        self.assertIn("JointSplitter_DrawPlane", Gui.listCommands())

    def test_selection_and_dialog_split(self):
        Gui.Selection.addSelection(self.source)
        Gui.Selection.addSelection(self.plane)
        source, plane, name = selected_inputs()
        dialog = SplitDialog(source, plane, name)
        dialog.kind.setCurrentText("Finger Joint")
        self.assertTrue(dialog.count.isEnabled())
        self.assertFalse(dialog.fields["angle"].isEnabled())
        dialog.kind.setCurrentText("Dovetail")
        self.assertTrue(dialog.fields["angle"].isEnabled())
        dialog.kind.setCurrentText("Tenon")
        dialog.show()
        QtWidgets.QApplication.processEvents()
        output = ROOT / "docs"
        output.mkdir(exist_ok=True)
        dialog.grab().save(str(output / "settings.png"))
        dialog.perform_split()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertFalse(self.source.Visibility)
        self.assertFalse(self.plane.Visibility)
        self.assertTrue(self.doc.JointPart1.Visibility)
        self.assertTrue(self.doc.JointPart2.Visibility)
        self.assertFalse(self.doc.JointParameters.Visibility)
        self.assertEqual(len(Gui.Selection.getSelection()), 2)

    def test_cancel_and_invalid_selection(self):
        with self.assertRaises(JointError):
            selected_inputs()
        Gui.Selection.addSelection(self.source, "Face1")
        Gui.Selection.addSelection(self.plane, "Face1")
        source, plane, name = selected_inputs()
        before = len(self.doc.Objects)
        dialog = SplitDialog(source, plane, name)
        dialog.reject()
        self.assertEqual(before, len(self.doc.Objects))

    def test_explicit_face_picker_without_preselection(self):
        face = self.doc.addObject("Part::Feature", "UserFace")
        face.Label = "내가 만든 Face"
        face.Shape = Part.makePlane(80, 60, App.Vector(-40, -30, 1))
        self.doc.recompute()
        dialog = SplitDialog()
        index = next(i for i, (obj, name) in enumerate(dialog.inputs.faces, 1) if obj is face)
        dialog.inputs.face_combo.setCurrentIndex(index)
        self.assertIs(dialog.plane, face)
        self.assertTrue(dialog.split_button.isEnabled())
        dialog.perform_split()
        self.assertEqual(self.doc.JointParameters.CuttingFace[0], face)
        self.assertEqual(len(self.doc.JointParameters.Shape.Solids), 2)

    def test_native_part_face_created_from_sketch(self):
        import Sketcher
        sketch = self.doc.addObject("Sketcher::SketchObject", "CutSketch")
        points = [App.Vector(-40,-30,0), App.Vector(40,-30,0), App.Vector(40,30,0), App.Vector(-40,30,0)]
        for index in range(4):
            sketch.addGeometry(Part.LineSegment(points[index], points[(index+1)%4]), False)
        face = self.doc.addObject("Part::Face", "Face")
        face.Sources = [sketch]
        self.doc.recompute()
        self.assertEqual(len(face.Shape.Faces), 1)
        dialog = SplitDialog(self.source, face, "Face1")
        self.assertIs(dialog.plane, face)
        self.assertTrue(dialog.split_button.isEnabled())
        dialog.perform_split()
        self.assertEqual(self.doc.JointParameters.CuttingFace[0], face)

    def test_multiple_face_objects_and_curves(self):
        extra = self.doc.addObject("Part::Feature", "SeveralFaces")
        first = Part.makePlane(20, 20, App.Vector(-10,-10,0))
        second = Part.makePlane(20, 20, App.Vector(-10,-10,2))
        extra.Shape = Part.makeCompound([first, second])
        curve = self.doc.addObject("Part::Feature", "CurvedFace")
        curve.Shape = Part.makeSphere(3).Faces[0]
        models, faces = document_inputs(self.doc)
        self.assertIn(self.source, models)
        self.assertEqual([name for obj,name in faces if obj is extra], ["Face1", "Face2"])
        self.assertFalse(any(obj is curve for obj,name in faces))
        dialog = SplitDialog(self.source, extra, "Face2")
        self.assertEqual(dialog.face_name, "Face2")
        self.assertTrue(dialog.split_button.isEnabled())
        dialog.reject()

    def test_face_picker_refresh_and_existing_face_action(self):
        dialog = PlaneDialog(self.source, self.plane, "Face1")
        before = len(self.doc.Objects)
        dialog.use_face()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(before, len(self.doc.Objects))
        self.assertEqual(selected_inputs(), (self.source, self.plane, "Face1"))
        split_dialog = SplitDialog(self.source, self.plane, "Face1")
        self.doc.removeObject(self.plane.Name)
        self.doc.recompute()
        split_dialog.inputs.refresh()
        self.assertFalse(split_dialog.split_button.isEnabled())
        split_dialog.reject()

    def test_axis_plane_creation_in_workbench(self):
        dialog = PlaneDialog(self.source)
        dialog.make_axis("YZ")
        self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        plane = self.doc.JointCuttingPlane
        self.assertAlmostEqual(abs(plane.Shape.Faces[0].normalAt(0,0).x), 1)
        self.assertAlmostEqual(plane.Shape.CenterOfMass.x, 0)

    def test_draw_three_points_and_cleanup(self):
        task = DrawPlaneTask(self.source)
        task.start()
        self.assertIsNotNone(task.callback)
        task.add_point(App.Vector(-10, -5, 0))
        task.add_point(App.Vector(10, -5, 0))
        task.add_point(App.Vector(0, -5, 0))
        self.assertEqual(len(task.points), 2)
        self.assertIn("일직선", task.status.text())
        task.add_point(App.Vector(0, 10, 0))
        self.assertTrue(task.finished)
        self.assertIsNone(task.callback)
        self.assertIsNone(task.overlay)
        self.assertIsNotNone(task.created_plane)
        self.assertAlmostEqual(task.created_plane.Shape.CenterOfMass.z, 0)
        dialog = SplitDialog(*selected_inputs())
        self.assertTrue(dialog.split_button.isEnabled())
        dialog.perform_split()
        self.assertEqual(len(self.doc.JointParameters.Shape.Solids), 2)

    def test_draw_cancel_undo_and_coordinate_entry(self):
        before = len(self.doc.Objects)
        task = DrawPlaneTask(self.source)
        task.start()
        for widget, value in zip(task.coords, (1,2,3)):
            widget.setValue(value)
        task.add_coordinates()
        self.assertEqual(task.points[0], App.Vector(1,2,3))
        self.assertIn("1/3", task.status.text())
        task.undo_point()
        self.assertFalse(task.points)
        task.on_event({"Type": "SoKeyboardEvent", "Key": "ESCAPE"})
        self.assertTrue(task.finished)
        self.assertEqual(before, len(self.doc.Objects))
        self.assertIsNone(task.callback)
        self.assertIsNone(task.overlay)

    def test_draw_mouse_surface_and_empty_space_coordinates(self):
        view = Mock()
        view.getObjectInfo.return_value = {"x":1.0,"y":2.0,"z":3.0}
        view.getPoint.return_value = App.Vector(7,8,9)
        task = DrawPlaneTask(self.source, view)
        event = {"Type":"SoMouseButtonEvent", "State":"DOWN", "Button":"BUTTON1", "Position":(100,200)}
        task.on_event(event)
        self.assertEqual(task.points[0], App.Vector(1,2,3))
        view.getObjectInfo.return_value = None
        task.on_event(event)
        self.assertEqual(task.points[1], App.Vector(7,8,9))
        task.reject()

    def test_plane_frame_rejects_degenerate_points(self):
        for points in ([App.Vector()]*3, [App.Vector(0,0,0), App.Vector(1,1,1), App.Vector(2,2,2)]):
            with self.assertRaises(JointError):
                frame_from_points(points)

    def test_preview_all_joint_types_and_dimension_guides(self):
        dialog = SplitDialog(self.source, self.plane, "Face1")
        before = [obj.Name for obj in self.doc.Objects]
        for kind in JOINT_TYPES:
            with self.subTest(kind=kind):
                dialog.kind.setCurrentText(kind)
                dialog.update_preview()
                self.assertIsNotNone(dialog.preview, dialog.error.text())
                self.assertEqual(dialog.preview.parameters.kind, kind)
                self.assertGreater(dialog.preview.parts_node.getNumChildren(), 0)
                self.assertAlmostEqual(minimum_distance(*dialog.preview.pair), 0.2, places=5)
                for key in ("width","length","depth","clearance","rotation","offset_x","offset_y","reverse","count","angle"):
                    dialog.set_active_field(key)
                    if key not in ("count","angle") or (key == "count" and kind in ("Dovetail","Finger Joint")) or (key == "angle" and kind == "Dovetail"):
                        self.assertGreater(dialog.preview.guide_node.getNumChildren(), 0)
        self.assertEqual([obj.Name for obj in self.doc.Objects], before)
        dialog.reject()

    def test_preview_focus_separation_and_cancel_restore(self):
        self.plane.Visibility = False
        dialog = SplitDialog(self.source, self.plane, "Face1")
        dialog.update_preview()
        self.assertIsNotNone(dialog.preview, dialog.error.text())
        self.assertFalse(self.source.Visibility)
        event = QtGui.QFocusEvent(QtCore.QEvent.FocusIn)
        QtWidgets.QApplication.sendEvent(dialog.fields["depth"].lineEdit(), event)
        self.assertEqual(dialog.active_field, "depth")
        self.assertEqual(dialog.preview.active, "depth")
        dialog.explode.setValue(20)
        self.assertAlmostEqual(dialog.preview.displacement(0).Length, 10)
        self.assertAlmostEqual(dialog.preview.displacement(1).Length, 10)
        renderer = dialog.preview
        dialog.reject()
        self.assertTrue(self.source.Visibility)
        self.assertFalse(self.plane.Visibility)
        self.assertIsNone(renderer.root)
        self.assertFalse(dialog.preview_timer.isActive())

    def test_preview_changes_debounce_and_invalid_result_clears(self):
        dialog = SplitDialog(self.source, self.plane, "Face1")
        dialog.update_preview()
        dialog.fields["clearance"].setValue(0.4)
        self.assertTrue(dialog.preview_timer.isActive())
        dialog.update_preview()
        self.assertAlmostEqual(minimum_distance(*dialog.preview.pair), 0.4, places=5)
        dialog.fields["depth"].setValue(100)
        dialog.update_preview()
        self.assertIsNone(dialog.preview)
        self.assertTrue(dialog.error.text())
        self.assertTrue(self.source.Visibility)
        self.assertEqual(len(self.doc.Objects), 2)
        dialog.reject()

    def test_preview_toggle_and_commit_visibility(self):
        dialog = SplitDialog(self.source, self.plane, "Face1")
        dialog.update_preview()
        dialog.preview_checkbox.setChecked(False)
        self.assertIsNone(dialog.preview)
        self.assertTrue(self.source.Visibility)
        dialog.preview_checkbox.setChecked(True)
        dialog.update_preview()
        self.assertIsNotNone(dialog.preview)
        dialog.perform_split()
        self.assertIsNone(dialog.preview)
        self.assertFalse(self.source.Visibility)
        self.assertFalse(self.plane.Visibility)
        self.assertTrue(self.doc.JointPart1.Visibility)
        self.assertTrue(self.doc.JointPart2.Visibility)

    def test_document_close_cleans_preview(self):
        dialog = SplitDialog(self.source, self.plane, "Face1")
        dialog.update_preview()
        renderer = dialog.preview
        name = self.doc.Name
        App.closeDocument(name)
        self.assertIsNone(renderer.root)
        self.assertFalse(dialog.observing)
        self.doc = App.newDocument("GuiSmokeAfterClose")

    def test_document_close_cleans_drawing(self):
        task = DrawPlaneTask(self.source)
        task.start()
        name = self.doc.Name
        App.closeDocument(name)
        self.assertTrue(task.finished)
        self.assertFalse(task.observing)
        self.assertIsNone(task.callback)
        self.assertIsNone(task.overlay)
        self.doc = App.newDocument("GuiSmokeAfterClose")


if __name__ == "__main__":
    register()
    outcome = unittest.main(exit=False, verbosity=2).result
    Gui.getMainWindow().close()
    sys.exit(0 if outcome.wasSuccessful() else 1)
