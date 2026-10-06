# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds dist/ydiskarc.app on macOS (dist/ydiskarc elsewhere).
#
#   pyinstaller --noconfirm --clean packaging/macos/ydiskarc.spec
#
# Environment:
#   TARGET_ARCH        arm64 (default on Apple Silicon), x86_64 or universal2
#   CODESIGN_IDENTITY  "Developer ID Application: ..." to sign; ad-hoc signing otherwise
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
sys.path.insert(0, ROOT)
import ydiskarc  # noqa: E402

VERSION = ydiskarc.__version__
IDENTITY = os.environ.get("CODESIGN_IDENTITY") or None

a = Analysis(
    [os.path.join(ROOT, "packaging", "ydiskarc_launcher.py")],
    pathex=[ROOT],
    hiddenimports=collect_submodules("ydiskarc") + collect_submodules("rich"),
    excludes=["pytest", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ydiskarc",
    console=False,
    target_arch=os.environ.get("TARGET_ARCH") or None,
    codesign_identity=IDENTITY,
    entitlements_file=os.path.join(SPECPATH, "entitlements.plist") if IDENTITY else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ydiskarc")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ydiskarc.app",
        icon=os.path.join(SPECPATH, "icon.icns"),
        bundle_identifier="io.github.sharindog.ydiskarc",
        version=VERSION,
        info_plist={
            "CFBundleName": "ydiskarc",
            "CFBundleDisplayName": "ydiskarc",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # follow the system dark mode
            "NSHumanReadableCopyright": "MIT License",
        },
    )
