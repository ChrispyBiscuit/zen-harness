"""Start a llama-server for one model profile, for benchmarking (separate from the app's own server).

The profile comes from the app's settings.json (agent.model_overrides.<name>), falling back to the repo's
built-in presets: model file, KV cache types, MoE offload, its own llama.cpp build (llama_dir).

Safety: refuses to start while the app's model server (port 8099) is listening — one 8 GB GPU cannot hold two
models, and the app may be mid-run. Only the process this module started is ever stopped (by PID).
"""
import json
import os
import socket
import subprocess
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
HOME = os.path.expanduser("~")
PORT = 8111
APP_MODEL_PORT = 8099
THINK_MSG = "\n\nI have thought about this enough. Time to act on it now.\n"


LOCAL = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
INSTALLED_DATA = os.path.join(LOCAL, "ZenHarness")                    # the installed app's data folder
INSTALLED_APP = os.path.join(LOCAL, "Programs", "Zen Harness")        # the installer's default folder
EXTRA = {}                                   # run.py --gguf: profiles made on the fly for a bare model file


def profiles():
    """Model profiles by name: the built-in presets, overlaid with the user's own (settings.json) entries.
    Works from the repo (backend/evals/standard), the dev copy, or next to an installed app."""
    out = {}
    for preset in (os.path.join(APP, "model_profiles.json"),                       # repo: backend/
                   os.path.join(os.path.dirname(HERE), "model_profiles.json"),      # tester kit: next to standard/
                   os.path.join(HOME, "dev", "zen-harness", "backend", "model_profiles.json")):
        if os.path.isfile(preset):
            out.update(json.load(open(preset, encoding="utf-8")).get("profiles") or {})
            break
    for st in (os.environ.get("LWS_DATA_DIR") and os.path.join(os.environ["LWS_DATA_DIR"], "settings.json"),
               os.path.join(APP, "settings.json"), os.path.join(INSTALLED_DATA, "settings.json")):
        if st and os.path.isfile(st):
            ov = (json.load(open(st, encoding="utf-8")).get("agent") or {}).get("model_overrides") or {}
            for k, v in ov.items():
                out[k] = {**out.get(k, {}), **v}
            break
    out.update(EXTRA)
    return out


def models_dirs():
    """Where a profile's relative model path may live: LWS_MODELS_DIR, the installed app's, the dev default."""
    return [d for d in (os.environ.get("LWS_MODELS_DIR"), os.path.join(INSTALLED_DATA, "models"),
                        os.path.join(HOME, "models")) if d]


def _model_path(p):
    p = os.path.expanduser(str(p or ""))
    if not p or os.path.isabs(p):
        return p
    for d in models_dirs():
        if os.path.isfile(os.path.join(d, p)):
            return os.path.join(d, p)
    return os.path.join(models_dirs()[0], p)


def _llama_dir(prof):
    """The llama.cpp build for this profile: its own llama_dir (e.g. PrismML's for Bonsai), LLAMA_CPP_DIR, the
    installed app's bundled build, or ~/llama.cpp."""
    if prof.get("llama_dir"):
        p = os.path.expanduser(prof["llama_dir"])
        if os.path.isabs(p):
            return p
        for base in (INSTALLED_APP, HOME):             # relative: next to the installed app (llama-prism), or ~
            if os.path.isfile(os.path.join(base, p, "llama-server.exe")):
                return os.path.join(base, p)
        return os.path.join(INSTALLED_APP, p)
    for d in (os.environ.get("LLAMA_CPP_DIR"), os.path.join(INSTALLED_APP, "llama"), os.path.join(HOME, "llama.cpp")):
        if d and os.path.isfile(os.path.join(d, "llama-server.exe")):
            return d
    return os.path.join(HOME, "llama.cpp")


def gguf_profile(path, ctx=32768):
    """A plain profile for a model file with no saved profile: q8_0 cache, everything on the GPU."""
    name = os.path.splitext(os.path.basename(path))[0]
    EXTRA[name] = {"model_file": os.path.abspath(path), "num_ctx": int(ctx), "kv_cache": "q8_0"}
    return name


def listening(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def command(name, ctx, think_budget):
    """The llama-server command line for profile `name` at context `ctx` (capped by the profile's num_ctx)."""
    prof = profiles().get(name)
    if not prof:
        raise SystemExit(f"no profile named {name!r}; known: {', '.join(sorted(profiles()))}")
    model = _model_path(prof.get("model_file"))
    if not os.path.isfile(model):
        raise SystemExit(f"{name}: model file not found ({model or 'profile has no model_file'}); looked in "
                         + ", ".join(models_dirs()) + " — or pass the file with --gguf")
    exe_dir = _llama_dir(prof)
    exe = os.path.join(exe_dir, "llama-server.exe")
    if not os.path.isfile(exe):
        raise SystemExit(f"llama-server.exe not found in {exe_dir} (set LLAMA_CPP_DIR, or the profile's llama_dir)")
    ctx = min(int(ctx), int(prof.get("num_ctx") or ctx))
    cmd = [exe, "-m", model, "-c", str(ctx), "-ngl", "99", "-fa", "on", "--host", "127.0.0.1",
           "--port", str(PORT), "--parallel", "1", "--jinja"]
    kv = str(prof.get("kv_cache") or "f16")
    if kv not in ("f16", "auto"):
        cmd += ["-ctk", kv, "-ctv", str(prof.get("kv_cache_v") or kv)]
    if prof.get("n_cpu_moe") is not None:
        cmd += ["--n-cpu-moe", str(int(prof["n_cpu_moe"]))]
    if prof.get("ubatch"):
        cmd += ["-ub", str(int(prof["ubatch"]))]
    if think_budget:
        cmd += ["--reasoning-budget", str(int(think_budget)), "--reasoning-budget-message", THINK_MSG]
    return cmd, exe_dir, prof, ctx


class Server:
    """with Server(name, ctx, think_budget) as base_url: ... — starts, waits for /health, stops by PID."""

    def __init__(self, name, ctx, think_budget=0, log_path=None):
        self.cmd, self.cwd, self.profile, self.ctx = command(name, ctx, think_budget)
        self.log_path = log_path or os.path.join(HERE, "logs", f"server_{name.replace(':', '_')}.log")
        self.proc = None

    def __enter__(self):
        if listening(APP_MODEL_PORT):
            raise SystemExit(f"The app's model server is running on port {APP_MODEL_PORT}. Benchmarks need the whole "
                             "GPU: let its current run finish, then close the app (or unload its model) first.")
        if listening(PORT):
            raise SystemExit(f"port {PORT} is already in use (an earlier benchmark server?) — stop it first")
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        t0 = time.time()
        while time.time() - t0 < 600:                  # big models on a throttled laptop load slowly
            if self.proc.poll() is not None:
                raise SystemExit(f"llama-server exited with code {self.proc.returncode}; see {self.log_path}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    if r.status == 200:
                        return f"http://127.0.0.1:{PORT}/v1"
            except Exception:
                pass                                   # 503 while loading
            time.sleep(2)
        self.__exit__()
        raise SystemExit("llama-server did not become healthy within 10 minutes")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()                      # this PID only — never by image name
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
