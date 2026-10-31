import hashlib
import json
import platform
import shutil
import sys
import zipfile
from importlib.metadata import version
from pathlib import Path

import PyInstaller.__main__

from version import APP_VERSION

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
APP_DIR = DIST / f"ClevoEcFanControl-v{APP_VERSION}"
ZIP_PATH = DIST / f"ClevoEcFanControl-v{APP_VERSION}-windows-x64.zip"

PyInstaller.__main__.run([
    str(ROOT / "clevo_fan_gui.py"),
    "--name=ClevoEcFanControl",
    "--onedir",
    "--windowed",
    "--clean",
    "--noconfirm",
    "--noupx",
    "--uac-admin",
    f"--icon={ROOT / 'assets' / 'icon.ico'}",
    f"--version-file={ROOT / 'version_info.txt'}",
    f"--add-binary={ROOT / 'inpoutx64.dll'};.",
    f"--add-data={ROOT / 'assets'};assets",
    "--hidden-import=pystray._win32",
    f"--distpath={APP_DIR.parent / ('staging-v' + APP_VERSION)}",
    f"--workpath={ROOT / 'build'}",
    f"--specpath={ROOT / 'build'}",
])

# Keep releases side by side so a running older EXE is never overwritten.
staged = APP_DIR.parent / ("staging-v" + APP_VERSION) / "ClevoEcFanControl"
shutil.copytree(staged, APP_DIR, dirs_exist_ok=True)

for filename in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md"):
    shutil.copy2(ROOT / filename, APP_DIR / filename)
for directory in ("docs", "third_party"):
    shutil.copytree(ROOT / directory, APP_DIR / directory, dirs_exist_ok=True)

# Ship the application and LGPL library sources together, with rebuild inputs.
source_dir = APP_DIR / "source"
source_dir.mkdir(parents=True, exist_ok=True)
for pattern in ("*.py", "requirements*.txt"):
    for path in ROOT.glob(pattern):
        shutil.copy2(path, source_dir / path.name)
for filename in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md",
                 "CONTRIBUTING.md", "version_info.txt", "FanControl.bat"):
    shutil.copy2(ROOT / filename, source_dir / filename)
for directory in ("assets", "docs", "third_party", "tests"):
    shutil.copytree(ROOT / directory, source_dir / directory, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

build_info = {"application_version": APP_VERSION, "python": platform.python_version(),
              "platform": "windows-x64", "dependencies": {
                  package: version(package) for package in ("Pillow", "pystray", "six", "PyInstaller")},
              "driver_sha256": hashlib.sha256((ROOT / "inpoutx64.dll").read_bytes()).hexdigest()}
(APP_DIR / "build-info.json").write_text(json.dumps(build_info, indent=2) + "\n", encoding="utf-8")
shutil.copy2(ROOT / "inpoutx64.dll", source_dir / "inpoutx64.dll")

if ZIP_PATH.exists():
    ZIP_PATH.unlink()
with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(APP_DIR.rglob("*")):
        if path.is_file():
            archive.write(path, Path(APP_DIR.name) / path.relative_to(APP_DIR))

digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
ZIP_PATH.with_suffix(".sha256").write_text(f"{digest}  {ZIP_PATH.name}\n", encoding="ascii")
print(f"Application: {APP_DIR / 'ClevoEcFanControl.exe'}")
print(f"Release ZIP: {ZIP_PATH}")
print(f"SHA256: {digest}")
