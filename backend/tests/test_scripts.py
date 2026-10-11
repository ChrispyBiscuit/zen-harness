"""Runs the end-to-end scripts in tests/scripts/ in their own processes (they boot a real backend with a stub
engine, patch module globals and set environment variables, so they must not share a process with other tests)."""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = ["headless_api.py", "headless_tray.py"]


@pytest.mark.slow
@pytest.mark.parametrize("script", SCRIPTS)
def test_script(script):
    r = subprocess.run([sys.executable, os.path.join(HERE, "scripts", script)], capture_output=True, text=True,
                       timeout=600, cwd=os.path.dirname(HERE))
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, out[-3000:]
