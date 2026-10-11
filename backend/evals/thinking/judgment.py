"""Thinking-library A/B on open-ended judgment: does a selected+adapted reasoning frame make the 9B decide better?

20 decision scenarios (written for this test, domain-neutral, unrelated to the other evals).
  A: plain answer            B: frame first (thinking.py select -> adapt), then the answer with the frame
Same model, server, system prompt and settings. A blind judge (gpt-6-luna) compares the two answers for judgment
quality in BOTH orders (position bias cancels); "frame wins" counts only consistent wins.

python judgment.py            (GPU must be free: starts its own llama-server with the production Qwen profile)
"""
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, APP)
import swarm  # noqa: E402
import thinking  # noqa: E402

OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)
PORT = 8133
LLAMA = r"C:\Users\user\llama.cpp\llama-server.exe"
MODEL_FILE = os.environ.get("TJ_MODEL", r"C:\Users\user\models\Qwen3.5-9B-IQ4_XS.gguf")
DRAFT = os.environ.get("TJ_DRAFT", r"C:\Users\user\models\originals\mtp-qwen3.5-9b-q4head.gguf")
JUDGE = os.environ.get("TJ_JUDGE", "gpt-6-luna")
SYSTEM = "You are a helpful, careful assistant. Answer the user's message directly and practically."

SCENARIOS = [
    "I've been offered a job at a startup paying 20% more than my stable government job, but with equity instead of a pension. I have a mortgage and a 2-year-old. Should I take it?",
    "Our small team's app is slow. My colleague wants to rewrite it in Rust; I think we should profile first. How should we decide?",
    "Clean up my Downloads folder.",
    "My landlord wants to raise the rent 15% at renewal. Local rents went up about 8% this year. What should I do?",
    "A study says people who drink coffee live longer. Should I start drinking coffee?",
    "We have budget for one hire: a senior engineer or two juniors. We're a 6-person startup shipping our first product in 4 months.",
    "My elderly father insists on driving even though he had two minor accidents this year. How do I handle this?",
    "Our website traffic dropped 40% last week. What happened?",
    "Should I pay off my 4% car loan early or invest the money?",
    "I need to choose between two roofers: one is cheaper and can start now, the other has better reviews but can't start for 3 months. The roof is leaking slightly.",
    "Write a reply declining my friend's request to borrow $2,000, without damaging the friendship.",
    "We're thinking of deleting old customer records to save storage costs. Any concerns?",
    "Two sources disagree on a city's population by 30%. How do I decide which one to use in my report?",
    "I'm moving to Madrid in 6 months and want to learn Spanish before then. I can study about 5 hours a week. Make me a plan.",
    "A vendor promises their tool will cut our support tickets in half. How should we evaluate this before buying?",
    "Is it better to rent or buy a home?",
    "My code works on my machine but fails in production now and then. Where do I start?",
    "Our team keeps missing deadlines. Fix this.",
    "I found a wallet with $300 and an ID on the train. What should I do?",
    "Should our town build a new road to reduce traffic congestion?",
]

JUDGE_PROMPT = """You compare two AI answers to the same user message for the quality of their JUDGMENT.

User message:
{q}

Answer 1:
{a1}

Answer 2:
{a2}

Which answer shows better judgment? Consider: does it identify what really matters for this person; weigh realistic \
alternatives and trade-offs; handle uncertainty and ambiguity well (asks, or states assumptions, when the message is \
underspecified); anticipate consequences and risks; give practical, actionable guidance; stay honest and \
proportionate without padding. Length is not quality.
Reply as JSON only: {{"better": "1" or "2" or "tie", "why": "one sentence"}}"""


def start_server():
    args = [LLAMA, "-m", MODEL_FILE, "-c", "32768", "-ngl", "99", "-fa", "on", "--port", str(PORT), "--host",
            "127.0.0.1", "--parallel", "1", "--jinja", "-ctk", "q8_0", "-ctv", "q4_0", "-md", DRAFT,
            "--spec-type", "draft-mtp,ngram-simple", "--spec-draft-n-max", os.environ.get("TJ_SPEC_N", "3"),
            "-ngld", "99", "-ub", "128", "-ctkd", "q4_0", "-ctvd", "q4_0", "--reasoning-budget", "1024",
            "--reasoning-budget-message", "\n\nI have thought about this enough. Time to act on it now.\n"]
    log = open(os.path.join(OUT, "server.log"), "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
                if json.load(r).get("status") == "ok":
                    return p
        except Exception:
            time.sleep(2)
    p.kill()
    raise RuntimeError("server did not start (see results/server.log)")


def chat(messages, max_tokens=int(os.environ.get('TJ_MAXTOK', '3200')), think=True, schema=None):
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0.6, "top_p": 0.95,
            "chat_template_kwargs": {"enable_thinking": think}}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "frame", "schema": schema}}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    msg = d["choices"][0]["message"]
    text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
    return text, d.get("usage", {}).get("completion_tokens", 0)


def frame_for(q):
    ask = thinking.SELECT_PROMPT.format(index=thinking.index_text())
    raw, ntok = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": q},
                      {"role": "user", "content": ask}], max_tokens=400, think=False, schema=thinking.SCHEMA)
    try:
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        use, frame = [str(x) for x in d.get("use") or []][:4], str(d.get("frame") or "")
    except (AttributeError, ValueError):
        use, frame = [], ""
    return use, frame, thinking.render(use, frame), ntok


def judge(q, a1, a2):
    body = {"model": JUDGE, "messages": [{"role": "user", "content": JUDGE_PROMPT.format(q=q, a1=a1[:6000], a2=a2[:6000])}],
            "max_completion_tokens": 400}
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {swarm.api_key()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    raw = d["choices"][0]["message"]["content"] or ""
    try:
        j = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        return str(j.get("better", "tie")), str(j.get("why", ""))[:300], d.get("usage", {})
    except (AttributeError, ValueError):
        return "tie", raw[:200], d.get("usage", {})


def main():
    t0 = time.time()
    srv = start_server()
    print(f"server up ({time.time() - t0:.0f}s)", flush=True)
    rows = []
    try:
        for i, q in enumerate(SCENARIOS):
            ta = time.time()
            a, na = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}])
            ta = time.time() - ta
            tb = time.time()
            use, frame, block, nf = frame_for(q)
            b, nb = chat([{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": q + (f"\n\n<system-reminder>\n{block}\n</system-reminder>" if block else "")}])
            tb = time.time() - tb
            rows.append({"q": q, "plain": a, "framed": b, "use": use, "frame": frame,
                         "sec_plain": round(ta, 1), "sec_framed": round(tb, 1), "tok_plain": na, "tok_framed": nb + nf})
            print(f"[{i + 1}/{len(SCENARIOS)}] use={use} plain {ta:.0f}s framed {tb:.0f}s", flush=True)
            json.dump({"partial": True, "rows": rows}, open(os.path.join(OUT, "judgment_partial.json"), "w",
                      encoding="utf-8"), indent=1, ensure_ascii=False)        # a stopped run keeps its answers
    finally:
        srv.terminate()
    wins = losses = ties = 0
    for r in rows:
        v1, w1, _ = judge(r["q"], r["plain"], r["framed"])        # framed = "2"
        v2, w2, _ = judge(r["q"], r["framed"], r["plain"])        # framed = "1"
        f1, f2 = v1 == "2", v2 == "1"
        p1, p2 = v1 == "1", v2 == "2"
        r["verdict"] = "framed" if (f1 and f2) else "plain" if (p1 and p2) else "tie/split"
        r["why"] = [w1, w2]
        wins += r["verdict"] == "framed"
        losses += r["verdict"] == "plain"
        ties += r["verdict"] == "tie/split"
        print(f"  {r['verdict']:9}  {r['q'][:70]}", flush=True)
    summ = {"framed_wins": wins, "plain_wins": losses, "ties_or_split": ties, "n": len(rows),
            "avg_sec_plain": round(sum(r["sec_plain"] for r in rows) / len(rows), 1),
            "avg_sec_framed": round(sum(r["sec_framed"] for r in rows) / len(rows), 1),
            "avg_tok_plain": round(sum(r["tok_plain"] for r in rows) / len(rows)),
            "avg_tok_framed": round(sum(r["tok_framed"] for r in rows) / len(rows))}
    json.dump({"summary": summ, "rows": rows, "seconds": round(time.time() - t0)},
              open(os.path.join(OUT, "judgment.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("SUMMARY", summ, flush=True)


if __name__ == "__main__":
    main()
