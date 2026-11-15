"""Build a self-contained add-on ZIP, excluding development caches."""
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]
out = root / "dist"
out.mkdir(exist_ok=True)
version = ET.parse(root / "package.xml").getroot().findtext("version")
archive = out / ("JointSplitter-" + version + ".zip")
files = [root / name for name in ("Init.py", "InitGui.py", "package.xml", "LICENSE", "README.md", "Install.FCMacro", "JointSplitter.FCMacro")]
for folder in ("joint_splitter", "resources", "docs", "examples"):
    files.extend(p for p in (root / folder).rglob("*") if p.is_file()
                 and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".FCStd1", ".FCBak"))
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(files):
        z.write(path, "JointSplitter/" + path.relative_to(root).as_posix())
print(archive)
