"""Entry point of the installed app (PyInstaller): one program, several roles chosen by a flag.

  ZenHarness.exe                  the desktop window (starts its server process if none is running)
  ZenHarness.exe --server         the backend server (started by the window; no UI)
  ZenHarness.exe --headless       the tray service for other apps (REST API / MCP), started at sign-in if enabled
  zenharness-cli.exe --mcp-stdio  the MCP stdio bridge MCP clients launch (console build of the same program)
  zenharness-cli.exe --version
  zenharness-cli.exe --python <script.py | -c code | -m module> ...   the bundled Python (agent scripts, tests)
  ZenHarness.exe -m ipykernel_launcher -f <conn>                       the Python tool's kernel (jupyter_client)

In a checkout the same roles run from their scripts (app_web.py, headless.py, harness_mcp.py --stdio).
"""
import multiprocessing
import os
import sys


def run_python(args):
    """Behave like `python` with the bundled interpreter: `--python script.py ...`, `-c code`, `-m module`, or
    `-m ipykernel_launcher` (jupyter_client starts the Python tool's kernel as `<sys.executable> -m ...`)."""
    import runpy
    if not args:
        import code
        code.interact()
        return
    if args[0] == "-c":
        sys.argv = ["-c"] + args[2:]
        exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__"})
    elif args[0] == "-m":
        sys.argv = [args[1]] + args[2:]
        runpy.run_module(args[1], run_name="__main__", alter_sys=True)
    else:
        sys.argv = args
        sys.path.insert(0, os.path.dirname(os.path.abspath(args[0])))
        runpy.run_path(args[0], run_name="__main__")


def main():
    multiprocessing.freeze_support()
    args = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    if args[:1] == ["--python"]:
        run_python(args[1:])
        return
    if args[:1] in (["-m"], ["-c"]):           # called as an interpreter (kernel launch, multiprocessing helpers)
        run_python(args)
        return
    if "--version" in args:
        import help_api
        print(f"Zazen {help_api.version()}")
        return
    if "--mcp-stdio" in args:
        import harness_mcp
        harness_mcp.stdio_main()
        return
    if "--headless" in args:
        import headless
        sys.argv = [sys.argv[0]] + [a for a in args if a != "--headless"]
        headless.main()
        return
    import app_web                     # window, or --server (app_web.main reads sys.argv itself)
    app_web.main()


if __name__ == "__main__":
    main()
