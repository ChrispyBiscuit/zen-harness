"""Math accuracy: the agent with the calculate tool vs the same model answering directly (no tools).

  python evals\\math_eval.py               both modes, all problems
  python evals\\math_eval.py --mode tools  only the agent with tools
  python evals\\math_eval.py --limit 5     quick run

Uses the agent model from settings.json. If the app's llama.cpp server is already running that model it is reused
(nothing is reloaded); otherwise one is started. Results go to evals\\math_results.json.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
os.chdir(APP)

# (question, expected answer as a number or list of numbers, relative tolerance)
PROBLEMS = [
    ("What is 48,723 × 9,187?", 447618201, 0),
    ("What is 2^64?", 18446744073709551616, 0),
    ("What is 123456789 × 987654321?", 121932631112635269, 0),
    ("Compute 17! (17 factorial).", 355687428096000, 0),
    ("What is 7,919 divided by 37, to 6 decimal places?", 214.027027, 1e-6),
    ("What is the square root of 2, to 10 decimal places?", 1.4142135624, 1e-10),
    ("What is 15.5% of 2,340?", 362.7, 1e-9),
    ("A $1,250 item is discounted 18% and then taxed 8.25%. What is the final price in dollars, to the cent?",
     1109.56, 1e-4),
    ("$10,000 invested at 5% annual interest compounded monthly: what is it worth after 10 years, to the cent?",
     16470.09, 1e-6),
    ("Monthly payment on a $300,000 30-year mortgage at 6.5% APR, to the cent?", 1896.20, 1e-5),
    ("Solve 3x^2 - 7x - 20 = 0. Give both roots.", [-5 / 3, 4], 1e-4),
    ("Solve the system 2x + 3y = 13 and 5x - y = 7. Give x and y.", [2, 3], 1e-9),
    ("What is the derivative of x^3·sin(x) evaluated at x = 2? Give 6 decimal places.", 7.582394, 1e-6),
    ("Compute the integral of x·e^x from 0 to 3, to 6 decimal places.", 41.171074, 1e-6),
    ("What is the determinant of the matrix [[2, -1, 3], [0, 4, 5], [1, 2, -2]]?", -53, 0),
    ("How many ways can you choose 6 numbers from 49?", 13983816, 0),
    ("What is the sample standard deviation of 12, 15, 18, 22, 25, 31? Give 4 decimal places.", 6.9498, 1e-4),
    ("What is the mean of 3.7, 4.2, 5.9, 6.1, 8.4?", 5.66, 1e-9),
    ("Convert 65 miles per hour to meters per second, 4 decimal places.", 29.0576, 1e-5),
    ("Convert 98.6 degrees Fahrenheit to Celsius.", 37.0, 1e-6),
    ("How many kilometers is a marathon of 26.2188 miles? 3 decimal places.", 42.195, 1e-4),
    ("How many days are there between 2026-03-03 and 2026-11-19?", 261, 0),
    ("How many business days (Mon–Fri) are there from 2026-01-05 up to but not including 2026-03-02?", 40, 0),
    ("What is the sum of the integers from 1 to 12,345?", 76205685, 0),
    ("What is 0.1 + 0.2, exactly, as a decimal?", 0.3, 1e-12),
    ("Compute 3^200 mod 1000.", 1, 0),
    ("A car uses 7.4 liters per 100 km. How many liters for a 1,350 km trip?", 99.9, 1e-9),
    ("What is log base 2 of 1,000,000, to 6 decimal places?", 19.931569, 1e-6),
    ("A recipe for 4 needs 350 g of flour. How much flour for 7 people, in grams?", 612.5, 1e-9),
    ("Train A leaves at 60 mph, train B leaves the same station 1.5 hours later at 85 mph on the same track. "
     "How many hours after B leaves does it catch A? 4 decimal places.", 3.6, 1e-6),
]

NUM_RX = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")


def numbers(text):
    text = re.sub(r"\\frac\{(-?[\d.]+)\}\{(-?[\d.]+)\}", lambda m: str(float(m.group(1)) / float(m.group(2))), text)
    out = []
    for s in NUM_RX.findall(text.replace("−", "-")):
        try:
            out.append(float(s.replace(",", "")))
        except ValueError:
            pass
    return out


def correct(answer, expected, tol):
    found = numbers(answer)
    want = expected if isinstance(expected, list) else [expected]

    def hit(w):
        return any((abs(f - w) <= max(abs(w) * tol, 1e-9 if tol else 0.5e-9 * max(1, abs(w)))) for f in found)
    return all(hit(w) for w in want)


def server_for(model):
    """Reuse the app's llama.cpp server if it is up with this model."""
    import llama_local
    for port in (8099, 8100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=3) as r:
                ids = [m.get("id", "") for m in json.load(r).get("data", [])]
            if any(os.path.basename(model).lower() in i.lower() or i.lower() in model.lower() for i in ids):
                return f"http://127.0.0.1:{port}"
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["both", "tools", "direct"], default="both")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    import agentic
    import llama_local
    settings = json.load(open(os.path.join(APP, "settings.json"), encoding="utf-8"))
    cfg = dict(settings["agent"])
    box = tempfile.mkdtemp(prefix="matheval_")
    cfg.update(roots=[box], plan_tokens=0, require_checklist=False, max_steps=8, delegation=False,
               hybrid={"enabled": False}, memory_recall=False, tracker_path=os.path.join(box, "t.md"))
    reuse = server_for(cfg.get("model", "")) if cfg.get("backend") == "llama" else None
    if reuse:
        llama_local.ensure = lambda *a, **k: reuse
        print("reusing the running llama.cpp server at", reuse)
    probs = PROBLEMS[:args.limit] if args.limit else PROBLEMS
    modes = ["tools", "direct"] if args.mode == "both" else [args.mode]
    results = {m: [] for m in modes}
    eng = agentic.AgentEngine(dict(cfg), lambda k, /, **d: None, ask=lambda r: "deny")
    eng.prepare()
    for mode in modes:
        ok = 0
        t_mode = time.time()
        for i, (q, want, tol) in enumerate(probs, 1):
            t = time.time()
            if mode == "tools":
                e = agentic.AgentEngine(dict(cfg), lambda k, /, **d: None, ask=lambda r: "deny")
                e.prepare()
                ans = e.run(q + " Give the final numeric answer.")
                used = sum(1 for m in e.history if m.get("role") == "tool" and m.get("name") == "calculate")
            else:
                msgs = [{"role": "system", "content": "Answer the math question. Work it out, then give the final "
                                                       "numeric answer."}, {"role": "user", "content": q}]
                ans = eng._plain(msgs, 1500)
                used = 0
            good = correct(ans, want, tol)
            ok += good
            results[mode].append({"q": q, "expected": want, "answer": ans[-400:], "correct": good,
                                  "calculate_calls": used, "seconds": round(time.time() - t, 1)})
            print(f"[{mode}] {i:2d}/{len(probs)} {'✓' if good else '✗'} {time.time() - t:5.1f}s  {q[:60]}", flush=True)
        print(f"== {mode}: {ok}/{len(probs)} correct in {time.time() - t_mode:.0f}s\n", flush=True)
    out = os.path.join(HERE, "math_results.json")
    json.dump(results, open(out, "w", encoding="utf-8"), indent=1)
    for m in modes:
        print(f"{m:7s} {sum(r['correct'] for r in results[m])}/{len(probs)}")
    print("details:", out)


if __name__ == "__main__":
    main()
