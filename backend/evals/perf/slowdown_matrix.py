"""Slowdown matrix: which condition makes the main model slow — GPU-assisted workers, CPU workers, low system RAM?

  venv\\Scripts\\python.exe evals\\perf\\slowdown_matrix.py                 all conditions, 1 round
  venv\\Scripts\\python.exe evals\\perf\\slowdown_matrix.py --rounds 2      conditions repeated in a shuffled order
  venv\\Scripts\\python.exe evals\\perf\\slowdown_matrix.py --only C0,C1

Every condition gets a FRESH main server with the app's exact flags (taken from the engine's own prepare(), so a
leftover eviction from one condition cannot leak into the next), then:
  under load : 3 x (unique ~4k-token prompt + 300 tokens, temperature 0)  -> prompt tok/s, generation tok/s, acceptance
  after load : the load is removed, 2 more requests                          -> does speed come back (persistent damage?)
A 1-second sampler records available RAM, commit charge vs limit, hard page-ins/s, GPU memory and the main server's
GPU 'shared' memory (WDDM moved part of its GPU memory to system RAM) and its committed/resident RAM.

Conditions
  C0 baseline            nothing else running
  C1 gpu_workers         2 MiniCPM workers (llama-server -ngl 0, GPU-assisted prompt reading) reading big prompts
  C2 cpu_workers         the same workers with -dev none (no GPU at all)
  C3 low_ram             a ballast process holds RAM until ~600 MB is available (re-touched so it stays resident)
  C4 low_ram+cpu_workers C3 and C2 together

Preflight refuses to run when the machine is already starved (commit headroom or available RAM too low), when the
app is busy, or when another llama-server is running — the result would measure the starting state, not the
condition. Results: evals\\perf\\runs\\<stamp>\\results.json and report.md.
"""
import argparse
import ctypes
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
LS = os.path.expanduser(r"~\llama.cpp\llama-server.exe")
MAIN_PORT, WORK_PORT = 8196, 8195
WORKER_MODEL = os.path.expanduser(r"~\models\MiniCPM5-2B-Q4_K_M.gguf")

ap = argparse.ArgumentParser()
ap.add_argument("--rounds", type=int, default=1)
ap.add_argument("--only", default="")
ap.add_argument("--model", default="qwen3.5:9b")
ap.add_argument("--ram-target-mb", type=int, default=600, help="available RAM the ballast leaves (C3/C4)")
ap.add_argument("--force", action="store_true", help="run even if preflight fails")
args = ap.parse_args()

RUN = os.path.join(HERE, "runs", time.strftime("%Y%m%d-%H%M%S"))
os.makedirs(RUN, exist_ok=True)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("LWS_DATA_DIR", os.path.join(RUN, "data"))


# ------------------------------------------------------------------ system telemetry
def _counters(paths):
    ps = "(Get-Counter " + ",".join(f"'{p}'" for p in paths) + ").CounterSamples | % { $_.CookedValue }"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.split()
    try:
        return [float(x) for x in out]
    except ValueError:
        return [float("nan")] * len(paths)


def gpu_used_mib():
    o = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True).stdout.strip().split(",")
    return int(o[0]), int(o[1])


def gpu_shared_mib(pid):
    ps = (f"(Get-Counter '\\GPU Process Memory(pid_{pid}_*)\\Shared Usage' -ErrorAction SilentlyContinue).CounterSamples"
          " | Measure-Object CookedValue -Maximum | % { [int]($_.Maximum/1MB) }")
    o = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    try:
        return int(o.splitlines()[-1])
    except (ValueError, IndexError):
        return -1


def snapshot(main_pid=None):
    avail, committed, limit, pagein = _counters(["\\Memory\\Available MBytes", "\\Memory\\Committed Bytes",
                                                  "\\Memory\\Commit Limit", "\\Memory\\Pages Input/sec"])
    g, gt = gpu_used_mib()
    s = {"t": round(time.time(), 1), "avail_mb": int(avail), "commit_gb": round(committed / 2**30, 2),
         "limit_gb": round(limit / 2**30, 2), "pagein_s": round(pagein, 1), "gpu_mib": g, "gpu_total_mib": gt}
    if main_pid:
        try:
            mi = psutil.Process(main_pid).memory_info()
            s.update(main_commit_gb=round(mi.private / 2**30, 2), main_rss_gb=round(mi.rss / 2**30, 2),
                     main_gpu_shared_mib=gpu_shared_mib(main_pid))
        except psutil.Error:
            s["main_dead"] = True
    return s


class Sampler:
    def __init__(self, pid):
        self.pid, self.rows, self._stop = pid, [], threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        while not self._stop.is_set():
            self.rows.append(snapshot(self.pid))
            self._stop.wait(1.0)

    def stop(self):
        self._stop.set()
        self.th.join(5)
        return self.rows


# ------------------------------------------------------------------ servers
def app_main_args():
    """The exact main-server flags the app builds (engine.prepare with ensure captured), with fan-out ON so the
    -t 4 --poll 0 the app used while workers ran are included — identical flags in every condition."""
    import agentic
    import llama_local
    got = {}
    llama_local.ensure = lambda model, par, ctx, st, extra_args=(), slot="main", **k: got.update(
        {slot: (model, par, ctx, list(extra_args))}) or "http://x"
    with open(os.path.join(APP, "settings.json"), encoding="utf-8") as f:
        cfg = dict(json.load(f).get("agent") or {})
    from agent_config import DEFAULT_AGENT
    cfg = dict(DEFAULT_AGENT, **cfg)
    fan = dict(cfg.get("fanout") or {}, enabled=True, model=WORKER_MODEL)
    cfg.update(model=args.model, backend="llama", fanout=fan, roots=[RUN], hybrid={"enabled": False},
               advisor={"enabled": False}, mcp_servers=[])
    agentic.AgentEngine(cfg, lambda k, /, **d: None).prepare()
    model, par, ctx, extra = got["main"]
    path = llama_local.ollama_blob(model) if not str(model).lower().endswith(".gguf") else model
    return [LS, "-m", path, "-c", str(ctx), "-ngl", "99", "-fa", "on", "--port", str(MAIN_PORT), "--host", "127.0.0.1",
            "--parallel", str(par)] + extra


def start(argv, log, high=False):
    p = subprocess.Popen(argv, stdout=open(log, "w"), stderr=subprocess.STDOUT, creationflags=0x08000000)
    if high:
        try:
            psutil.Process(p.pid).nice(psutil.HIGH_PRIORITY_CLASS)
        except psutil.Error:
            pass
    port = argv[argv.index("--port") + 1]
    for _ in range(180):
        if p.poll() is not None:
            raise RuntimeError(f"server exited while loading (code {p.returncode}) — see {log}")
        try:
            if json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read()).get("status") == "ok":
                return p
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"server did not become ready — see {log}")


def stop(p):
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()


def post(port, body, timeout=600):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


FILLER = " ".join(f"Item {k}: region {k % 7} sold {k * 13 % 997} units in week {k % 52}." for k in range(260))


def measure(tag):
    """One unique ~4k-token prompt (forces a full prompt read) + 300 generated tokens at temperature 0."""
    body = {"messages": [{"role": "user", "content": f"[{tag} {time.time_ns()}] {FILLER}\nDescribe the pattern in "
                                                     "these figures in detail, region by region."}],
            "max_tokens": 300, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
    t = post(MAIN_PORT, body)["timings"]
    acc = t["draft_n_accepted"] / t["draft_n"] if t.get("draft_n") else None
    return {"pp": round(t["prompt_per_second"], 1), "tg": round(t["predicted_per_second"], 1),
            "acc": round(acc, 3) if acc is not None else None, "n_prompt": t["prompt_n"]}


# ------------------------------------------------------------------ loads
class Workers:
    """Two slots reading big prompts continuously, like fan-out workers do after each tool result."""
    def __init__(self, gpu):
        argv = [LS, "-m", WORKER_MODEL, "-c", "32768", "-ngl", "0", "-fa", "on", "--port", str(WORK_PORT), "--host",
                "127.0.0.1", "--parallel", "2", "--jinja", "-ctk", "q8_0", "-ctv", "q8_0", "-t", "8"]
        if not gpu:
            argv += ["-dev", "none"]
        self.p = start(argv, os.path.join(RUN, f"workers_{'gpu' if gpu else 'cpu'}.log"), high=True)
        self.stop_ev, self.reads = threading.Event(), []
        self.ths = [threading.Thread(target=self._loop, daemon=True) for _ in range(2)]
        for th in self.ths:
            th.start()
        time.sleep(6)                      # let both slots get into a prompt read

    def _loop(self):
        page = "The survey reports base pay, bonus and equity for program managers across regions. " * 260
        while not self.stop_ev.is_set():
            try:
                t = post(WORK_PORT, {"messages": [{"role": "user", "content": f"{time.time_ns()} {page}\nList the "
                                                   "figures."}], "max_tokens": 40,
                                     "chat_template_kwargs": {"enable_thinking": False}}, timeout=900)["timings"]
                self.reads.append(round(t["prompt_per_second"], 1))
            except Exception:
                if not self.stop_ev.is_set():
                    time.sleep(1)

    def close(self):
        self.stop_ev.set()
        stop(self.p)
        return {"worker_prompt_read_tok_s": statistics.median(self.reads) if self.reads else None,
                "worker_reads": len(self.reads)}


class Ballast:
    """Holds RAM until only ~target MB is available; a thread re-touches the pages so Windows cannot quietly page
    them out (that would hand the RAM back and the pressure would vanish). Stays clear of the commit limit."""
    def __init__(self, target_mb):
        s = snapshot()
        room_commit_mb = (s["limit_gb"] - s["commit_gb"]) * 1024 - 3072           # never within 3 GB of the limit
        want = max(0, min(s["avail_mb"] - target_mb, room_commit_mb))
        self.blocks, self.stop_ev = [], threading.Event()
        chunk = 256 * 2**20
        for _ in range(int(want * 2**20 // chunk)):
            b = bytearray(chunk)
            for i in range(0, chunk, 4096):
                b[i] = 1
            self.blocks.append(b)
        self.mb = len(self.blocks) * 256
        self.th = threading.Thread(target=self._touch, daemon=True)
        self.th.start()
        time.sleep(3)

    def _touch(self):
        while not self.stop_ev.is_set():
            for b in self.blocks:
                for i in range(0, len(b), 4096 * 16):
                    b[i] = (b[i] + 1) & 0xFF
                if self.stop_ev.is_set():
                    return
            self.stop_ev.wait(0.5)

    def close(self):
        self.stop_ev.set()
        self.th.join(10)
        self.blocks.clear()
        return {"ballast_mb": self.mb}


CONDITIONS = {
    "C0": ("baseline", []),
    "C1": ("gpu_workers", ["gpu_workers"]),
    "C2": ("cpu_workers", ["cpu_workers"]),
    "C3": ("low_ram", ["ballast"]),
    "C4": ("low_ram+cpu_workers", ["ballast", "cpu_workers"]),
}


def run_condition(cid, main_argv, rnd):
    name, loads = CONDITIONS[cid]
    s0 = snapshot()
    if s0["limit_gb"] - s0["commit_gb"] < 9 and not args.force:
        raise RuntimeError(f"commit headroom {s0['limit_gb'] - s0['commit_gb']:.1f} GB < 9 GB before this condition — "
                           "stopping rather than measuring a starved machine")
    print(f"\n[{cid} {name}] round {rnd}: fresh main server…", flush=True)
    main = start(main_argv, os.path.join(RUN, f"main_{cid}_r{rnd}.log"))
    res = {"cond": cid, "name": name, "round": rnd, "under": [], "after": [], "errors": []}
    sampler = Sampler(main.pid)
    held = []
    try:
        measure("warmup")
        res["idle_before"] = snapshot(main.pid)
        t_load = time.time()
        if "ballast" in loads:
            held.append(Ballast(args.ram_target_mb))
        if "gpu_workers" in loads:
            held.append(Workers(gpu=True))
        if "cpu_workers" in loads:
            held.append(Workers(gpu=False))
        for i in range(3):
            try:
                res["under"].append(measure(f"{cid}u{i}"))
            except Exception as e:
                res["errors"].append(f"under {i}: {type(e).__name__}: {e}"[:200])
        t_unload = time.time()
        for h in held:
            res.update(h.close())
        held = []
        time.sleep(3)
        for i in range(2):
            try:
                res["after"].append(measure(f"{cid}a{i}"))
            except Exception as e:
                res["errors"].append(f"after {i}: {type(e).__name__}: {e}"[:200])
    finally:
        for h in held:
            h.close()
        rows = sampler.stop()
        res["main_alive"] = main.poll() is None
        stop(main)
        time.sleep(6)
        s1 = snapshot()
        # committed memory left behind after every server of this condition has exited (no process owns it)
        res["commit_leaked_gb"] = round(s1["commit_gb"] - s0["commit_gb"], 2)
    win = [r for r in rows if t_load <= r["t"] <= t_unload] if "t_load" in dir() else rows
    pick = lambda k, f: f([r[k] for r in win if isinstance(r.get(k), (int, float)) and r[k] >= 0]) if win else None
    res["telemetry"] = {"avail_mb_min": pick("avail_mb", min), "commit_gb_max": pick("commit_gb", max),
                        "limit_gb": pick("limit_gb", max), "pagein_s_max": pick("pagein_s", max),
                        "gpu_mib_max": pick("gpu_mib", max), "main_gpu_shared_mib_max": pick("main_gpu_shared_mib", max),
                        "main_commit_gb_max": pick("main_commit_gb", max)}
    res["samples"] = rows
    med = lambda xs, k: statistics.median([x[k] for x in xs if x.get(k) is not None]) if xs else None
    res["tg_under"], res["pp_under"] = med(res["under"], "tg"), med(res["under"], "pp")
    res["tg_after"], res["pp_after"] = med(res["after"], "tg"), med(res["after"], "pp")
    tm = res["telemetry"]
    print(f"  under: tg {res['tg_under']} tok/s, pp {res['pp_under']} tok/s | after: tg {res['tg_after']}, pp "
          f"{res['pp_after']} | avail RAM min {tm['avail_mb_min']} MB, page-ins max {tm['pagein_s_max']}/s, GPU max "
          f"{tm['gpu_mib_max']} MiB, main GPU-shared max {tm['main_gpu_shared_mib_max']} MiB"
          + f" | commit left behind {res['commit_leaked_gb']} GB"
          + (f" | errors: {res['errors']}" if res["errors"] else ""), flush=True)
    return res


def preflight():
    problems = []
    s = snapshot()
    if s["limit_gb"] - s["commit_gb"] < 12:
        problems.append(f"commit headroom {s['limit_gb'] - s['commit_gb']:.1f} GB < 12 GB (restart the app / reboot)")
    if s["avail_mb"] < 4000:
        problems.append(f"available RAM {s['avail_mb']} MB < 4000 MB")
    others = [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "llama-server.exe"]
    if others:
        problems.append(f"other llama-server processes running: {others} (they share the GPU)")
    try:
        if json.loads(urllib.request.urlopen("http://127.0.0.1:8765/api/build", timeout=3).read()).get("busy"):
            problems.append("the app is busy with a query")
    except Exception:
        pass
    try:
        st = ctypes.c_ubyte * 12
        buf = st()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(buf)) and buf[0] == 0:
            problems.append("on battery (the GPU is ~3.5x slower on battery)")
    except Exception:
        pass
    return s, problems


def verdict(results):
    by = {}
    for r in results:
        by.setdefault(r["cond"], []).append(r)
    agg = {}
    for cid, rs in by.items():
        vals = lambda k: [r[k] for r in rs if r.get(k) is not None]
        agg[cid] = {k: (statistics.median(vals(k)) if vals(k) else None) for k in ("tg_under", "pp_under", "tg_after", "pp_after")}
        agg[cid]["shared_max"] = max((r["telemetry"]["main_gpu_shared_mib_max"] or 0) for r in rs)
        agg[cid]["avail_min"] = min((r["telemetry"]["avail_mb_min"] or 0) for r in rs)
        agg[cid]["pagein_max"] = max((r["telemetry"]["pagein_s_max"] or 0) for r in rs)
        agg[cid]["crashed"] = any(not r["main_alive"] or r["errors"] for r in rs)
        agg[cid]["leaked_gb"] = round(sum(r.get("commit_leaked_gb") or 0 for r in rs) / len(rs), 2)
    base = agg.get("C0", {}).get("tg_under")
    lines = []
    for cid, a in agg.items():
        if base and a["tg_under"]:
            a["tg_ratio"] = round(a["tg_under"] / base, 2)
            a["after_ratio"] = round(a["tg_after"] / base, 2) if a.get("tg_after") else None
            a["slow"] = a["tg_ratio"] < 0.6
            a["persistent"] = a["after_ratio"] is not None and a["after_ratio"] < 0.85
    if base:
        slow = [c for c, a in agg.items() if a.get("slow")]
        pers = [c for c, a in agg.items() if a.get("persistent")]
        lines.append(f"Conditions that cut generation below 60% of baseline: {', '.join(slow) or 'none'}.")
        lines.append(f"Conditions that left the main model slow AFTER the load was removed: {', '.join(pers) or 'none'}.")
        if "C1" in agg and "C3" in agg:
            gpu_bad, ram_bad = agg["C1"].get("slow"), agg["C3"].get("slow")
            if gpu_bad and not ram_bad:
                lines.append("=> The slowdown follows GPU-assisted workers, not low system RAM: different cause from the "
                             "commit-exhaustion crash.")
            elif ram_bad and not gpu_bad:
                lines.append("=> The slowdown follows low system RAM, not GPU-assisted workers: same family as the crash.")
            elif gpu_bad and ram_bad:
                lines.append("=> Both reproduce it: each is sufficient on its own.")
            else:
                lines.append("=> Neither reproduces it here: the original slowdown needed something else.")
    return agg, lines


def main():
    s, problems = preflight()
    print("preflight:", json.dumps(s), flush=True)
    if problems:
        print("PREFLIGHT FAILED:\n- " + "\n- ".join(problems), flush=True)
        if not args.force:
            sys.exit(2)
    main_argv = app_main_args()
    print("main server flags:", " ".join(a for a in main_argv[1:] if "\n" not in a), flush=True)
    order = [c for c in CONDITIONS if not args.only or c in args.only.split(",")]
    results = []
    for rnd in range(1, args.rounds + 1):
        seq = order[:1] + random.sample(order[1:], len(order) - 1) if rnd > 1 else order   # baseline first, then shuffled
        for cid in seq:
            try:
                results.append(run_condition(cid, main_argv, rnd))
            except Exception as e:
                print(f"  {cid} failed: {type(e).__name__}: {e}", flush=True)
                results.append({"cond": cid, "name": CONDITIONS[cid][0], "round": rnd, "error": str(e)[:300],
                                "under": [], "after": [], "errors": [str(e)[:200]], "main_alive": False,
                                "telemetry": {k: None for k in ("avail_mb_min", "commit_gb_max", "limit_gb", "pagein_s_max",
                                                                "gpu_mib_max", "main_gpu_shared_mib_max", "main_commit_gb_max")}})
    ok = [r for r in results if r.get("under")]
    agg, lines = verdict(ok)
    with open(os.path.join(RUN, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"preflight": s, "problems": problems, "main_argv": main_argv, "results": results, "summary": agg},
                  f, indent=1, default=str)
    rep = ["# Slowdown matrix", "", f"Run {os.path.basename(RUN)} · model {args.model} · rounds {args.rounds}", "",
           "| cond | load | gen tok/s under | gen after | prompt tok/s under | prompt after | gen vs baseline | "
           "after vs baseline | min avail RAM MB | max page-ins/s | main GPU→RAM (shared) MiB | crash/errors | "
           "commit left behind GB |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cid, a in agg.items():
        rep.append(f"| {cid} | {CONDITIONS[cid][0]} | {a['tg_under']} | {a['tg_after']} | {a['pp_under']} | "
                   f"{a['pp_after']} | {a.get('tg_ratio')} | {a.get('after_ratio')} | {a['avail_min']} | "
                   f"{a['pagein_max']} | {a['shared_max']} | {'yes' if a['crashed'] else 'no'} | {a['leaked_gb']} |")
    rep += [""] + lines
    with open(os.path.join(RUN, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rep) + "\n")
    print("\n" + "\n".join(rep), flush=True)
    print("\nresults:", RUN, flush=True)


if __name__ == "__main__":
    main()
