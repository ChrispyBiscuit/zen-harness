"""Where everything lives — the one place that decides paths (refactor plan, stage 1).

  resource(...)  files that ship with the app (guides, playbooks, assets, web_dist, docs, thinking library).
                 Read-only once installed: inside the PyInstaller bundle when frozen, the backend folder in a checkout.
  data(...)      everything the app writes: settings, chats, memory, logs, attachments, trackers, trash, browser
                 profiles, background-process state. LWS_DATA_DIR wins; an installed app uses
                 %LOCALAPPDATA%\\ZenHarness; a dev checkout uses the backend folder (git-ignored files).
  models_dir()   GGUF and speech/embedding models. LWS_MODELS_DIR, else <data>\\models when installed, else ~\\models.
  llama_dir()    llama.cpp binaries. LLAMA_CPP_DIR, else <install>\\llama when installed, else ~\\llama.cpp.
"""
import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
# bundled resources: PyInstaller unpacks them under sys._MEIPASS (the _internal folder of a onedir build)
APP_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
# the folder of the program itself (where an installer puts llama\ next to ZenHarness.exe)
EXE_DIR = os.path.dirname(sys.executable) if FROZEN else APP_DIR
HOME = os.path.expanduser("~")


def _local_appdata(*parts):
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local"), *parts)


def resource(*parts):
    return os.path.join(APP_DIR, *parts)


def data_dir():
    d = os.environ.get("LWS_DATA_DIR") or (_local_appdata("ZenHarness") if FROZEN else APP_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def data(*parts):
    return os.path.join(data_dir(), *parts)


def models_dir():
    return os.environ.get("LWS_MODELS_DIR") or (data("models") if FROZEN else os.path.join(HOME, "models"))


def llama_dir():
    return os.environ.get("LLAMA_CPP_DIR") or (os.path.join(EXE_DIR, "llama") if FROZEN
                                                else os.path.join(HOME, "llama.cpp"))


def resolve_model(p):
    """A profile's model path: absolute as is; relative = inside the models dir (portable settings)."""
    p = str(p or "").strip()
    if not p:
        return ""
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(models_dir(), p)


def knowledge_dir():
    """Offline knowledge (Wikipedia .zim files)."""
    return os.environ.get("LWS_KIWIX_DIR") or (data("kiwix") if FROZEN else os.path.join(HOME, "kiwix"))


def logs_dir():
    d = data("logs") if FROZEN else data_dir()       # a checkout keeps its old log locations
    os.makedirs(d, exist_ok=True)
    return d


ROLES = {                       # role -> (script in a checkout, its arguments, flag for the frozen exe, console?)
    "window": ("app_web.py", [], [], False),
    "server": ("app_web.py", ["--server"], ["--server"], False),
    "headless": ("headless.py", [], ["--headless"], False),
    "mcp-stdio": ("harness_mcp.py", ["--stdio"], ["--mcp-stdio"], True),
}
GUI_EXE, CLI_EXE = "ZenHarness.exe", "zenharness-cli.exe"


def role_command(role):
    """Command line that starts this app in another role. Installed: ZenHarness.exe (no console) or
    zenharness-cli.exe (console, for the MCP stdio bridge) with a mode flag; a checkout: the script with this
    Python (pythonw for windowless roles)."""
    script, args, flag, console = ROLES[role]
    if FROZEN:
        return [os.path.join(EXE_DIR, CLI_EXE if console else GUI_EXE), *flag]
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    py = os.path.join(os.path.dirname(exe), "python.exe")
    exe = (py if os.path.exists(py) else exe) if console else (pyw if os.path.exists(pyw) else exe)
    return [exe, os.path.join(APP_DIR, script), *args]


def system_python():
    """A real Python on PATH (not the Microsoft Store alias stub), or ''."""
    import shutil
    p = shutil.which("python") or ""
    return "" if (not p or "windowsapps" in p.lower()) else p


def python_cmd():
    """How to run a Python script: the user's Python if installed, else the app's bundled interpreter
    (installed builds), else this interpreter (checkout)."""
    p = system_python()
    if p:
        return [p]
    if FROZEN:
        return [os.path.join(EXE_DIR, CLI_EXE), "--python"]
    return [sys.executable]


def shim_dir():
    """A folder with python.cmd that runs the bundled interpreter — put on PATH for commands the agent runs when
    the PC has no Python, so `python script.py` works on a fresh machine (pip does not)."""
    if not FROZEN or system_python():
        return ""
    d = data("shims")
    os.makedirs(d, exist_ok=True)
    cmd = os.path.join(d, "python.cmd")
    body = f'@"{os.path.join(EXE_DIR, CLI_EXE)}" --python %*\r\n'
    try:
        if not os.path.exists(cmd) or open(cmd, encoding="utf-8").read() != body:
            with open(cmd, "w", encoding="utf-8", newline="") as f:
                f.write(body)
    except OSError:
        return ""
    return d


def work_dir():
    """Working folder for processes the app starts (never the read-only install folder for writes)."""
    return EXE_DIR if FROZEN else APP_DIR
