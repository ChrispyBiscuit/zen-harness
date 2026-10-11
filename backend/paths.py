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


def program_command(*args):
    """Command line that runs this app in another role (server, headless, MCP stdio bridge): the frozen exe takes
    a mode flag; a checkout runs the matching script with this Python."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, *args]
