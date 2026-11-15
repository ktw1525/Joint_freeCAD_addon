"""Normal imports supply __file__; FreeCAD's InitGui.py execution does not."""
import os
import FreeCADGui as Gui


class JointSplitterWorkbench(Gui.Workbench):
    MenuText = "Joint Splitter"
    ToolTip = "절단면으로 모델을 맞물리는 두 파트로 분할합니다"
    Icon = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "joint_splitter.svg")

    def Initialize(self):
        from . import commands
        commands.register()
        actions = ["JointSplitter_Plane", "JointSplitter_DrawPlane", "JointSplitter_Split"]
        self.appendToolbar("Joint Splitter", actions)
        self.appendMenu("Joint Splitter", actions)

    def GetClassName(self):
        return "Gui::PythonWorkbench"
