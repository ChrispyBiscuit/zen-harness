"""Start the tray service at sign-in: a shortcut in the current user's Startup folder.

Per-user, no admin rights, no registry or policy changes; it shows (and can be disabled) in Task Manager →
Startup apps, and deleting the one .lnk file removes it. LWS_STARTUP_DIR points tests at another folder.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "Zen Headless.lnk"
LEGACY = "Zen Headless Harness.lnk"     # the name before the rename to Zen: still honoured, replaced on refresh


def startup_dir():
    return os.environ.get("LWS_STARTUP_DIR") or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming")),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def shortcut_path():
    return os.path.join(startup_dir(), NAME)


def pythonw():
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.exists(w) else sys.executable


def is_registered():
    return os.path.exists(shortcut_path()) or os.path.exists(os.path.join(startup_dir(), LEGACY))


def register():
    """Create (or refresh) the shortcut: pythonw.exe headless.py, started in this folder."""
    import paths
    icon = paths.resource("assets", "zen_harness.ico")
    cmd = paths.role_command("headless")
    vals = {"LNK": shortcut_path(), "TGT": cmd[0], "ARGS": " ".join(f'"{a}"' for a in cmd[1:]),
            "WD": paths.work_dir(), "ICO": icon if os.path.exists(icon) else ""}
    # values go through the environment, never into the script text (no quoting/injection issues)
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:ZH_LNK);"
              "$s.TargetPath=$env:ZH_TGT;$s.Arguments=$env:ZH_ARGS;$s.WorkingDirectory=$env:ZH_WD;"
              "$s.Description='Zen Headless (tray service)';"
              "if($env:ZH_ICO){$s.IconLocation=$env:ZH_ICO};$s.WindowStyle=7;$s.Save()")
    os.makedirs(startup_dir(), exist_ok=True)
    try:
        os.remove(os.path.join(startup_dir(), LEGACY))
    except FileNotFoundError:
        pass
    env = {**os.environ, **{f"ZH_{k}": v for k, v in vals.items()}}
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                       capture_output=True, text=True, timeout=30, creationflags=0x08000000)
    if r.returncode != 0 or not is_registered():
        raise OSError(f"could not create the Startup shortcut: {(r.stderr or r.stdout).strip()[:300]}")
    return shortcut_path()


def unregister():
    for n in (NAME, LEGACY):
        try:
            os.remove(os.path.join(startup_dir(), n))
        except FileNotFoundError:
            pass


def stdio_command():
    """How an MCP client starts the stdio bridge."""
    import paths
    cmd = paths.role_command("mcp-stdio")
    return {"command": cmd[0], "args": cmd[1:]}
