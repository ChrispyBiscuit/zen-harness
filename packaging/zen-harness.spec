# PyInstaller spec for Zen Harness (onedir). Build with packaging/build.ps1, which runs:
#   backend\venv\Scripts\pyinstaller packaging\zen-harness.spec --distpath packaging\out\dist --workpath packaging\out\work
# Two programs share one bundle: ZenHarness.exe (windowed: window, server, tray service) and zenharness-cli.exe
# (console: the MCP stdio bridge, --version). See backend/zen_main.py.
import glob
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
BE = os.path.join(ROOT, "backend")

# every backend module: many are imported lazily inside functions or by name
modules = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(BE, "*.py"))
                 if not os.path.basename(p).startswith(("test_", "conftest")))

datas = [
    (os.path.join(BE, "web_dist"), "web_dist"),
    (os.path.join(BE, "guides"), "guides"),
    (os.path.join(BE, "playbooks"), "playbooks"),
    (os.path.join(BE, "assets"), "assets"),
    (os.path.join(BE, "licenses"), "licenses"),
    (os.path.join(BE, "thinking_library.json"), "."),
    (os.path.join(BE, "model_catalog.json"), "."),
    (os.path.join(ROOT, "docs"), "docs"),
    (os.path.join(ROOT, "VERSION"), "."),
    (os.path.join(ROOT, "LICENSE"), "."),
]
binaries = []
hiddenimports = list(modules)
# packages that load data files or plugins at runtime
for pkg in ("trafilatura", "justext", "courlan", "htmldate", "dateparser", "ddgs", "fastembed", "faster_whisper",
            "ctranslate2", "onnxruntime", "tokenizers", "libzim", "pptx", "docx", "openpyxl", "pint", "jedi", "parso",
            "duckdb", "playwright", "tiktoken_ext",
            # the Python tool's kernel runs from this program (ZenHarness.exe -m ipykernel_launcher)
            "ipykernel", "jupyter_client", "jupyter_core", "zmq", "tornado", "matplotlib", "comm", "debugpy"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass
hiddenimports += collect_submodules("aiohttp") + ["win32com.client", "pythoncom", "pywintypes"]

excludes = [
    # GPL-3.0 espeak-ng (via espeakng-loader) and the TTS that needs it: voice output is an optional download
    "kokoro_onnx", "espeakng_loader", "phonemizer",
    # formula OCR (torch, 4+ GB) and training libraries are not part of the app
    "torch", "torchvision", "pix2tex", "transformers", "timm",
    "tkinter", "IPython.qt", "PyQt5", "PyQt6",
]

a = Analysis(
    [os.path.join(BE, "zen_main.py")],
    pathex=[BE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(BE, "assets", "zen_harness.ico")
version_file = os.path.join(SPECPATH, "out", "version_info.txt")

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ZenHarness", console=False, icon=icon,
          version=version_file if os.path.exists(version_file) else None, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="zenharness-cli", console=True, icon=icon,
          version=version_file if os.path.exists(version_file) else None, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="ZenHarness", upx=False)
