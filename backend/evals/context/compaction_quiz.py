"""How much does a compaction keep? Replay a saved pre-compaction snapshot through two compaction styles and quiz
a model on the facts of the work, using only what each style leaves in context.

  python compaction_quiz.py --conv <chat id> --base-url http://127.0.0.1:8099/v1 [--model x] [--summarizer-url ...]
  python compaction_quiz.py --snapshot <path to compaction_N.json> --base-url ...

Styles:
  old  the previous summary prompt; nothing pinned besides the summary
  new  the template built on the recorded turning points, plus the timeline pinned word for word
Facts quizzed come from the snapshot's turning-point timeline (files created, decisions, test results, errors),
scored by recall of their key terms in a free-recall answer. Needs a model endpoint (OpenAI-compatible): run it
with the GPU free, or against evals/standard/mock_server.py for a dry run of the pipeline.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import context_memory as cm  # noqa: E402

OLD_PROMPT = """Summarize the conversation so far for an agent that will continue the work without seeing the original \
messages. Keep, in this order:
1. The user's requests, goals, constraints and preferences (quote exact requirements).
2. What has been done: files created/edited (full paths), commands run and their key results, facts discovered, \
decisions made.
3. What failed or was ruled out, and why.
4. The current state: what you were in the middle of, the working hypothesis or finding (with file paths and line \
numbers), the last thing tried and its result, and the next steps.
Be specific and factual; at most 450 words. Do not add anything that is not in the conversation."""

QUIZ = ("Without using any tools, answer from what is in this conversation only. List, as short lines:\n"
        "1. every file created so far (paths)\n2. every decision settled\n3. the latest test result (passed/failed "
        "counts, failing test names)\n4. every error seen and whether it was fixed\n5. what you were doing last. "
        "Write 'unknown' for anything you can't recall.")


def chat(base, model, messages, max_tokens=900):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return (json.load(r)["choices"][0]["message"].get("content") or "").strip()


def flatten(history):
    lines = []
    for m in history:
        c = m.get("content")
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict)) if isinstance(c, list) else (c or "")
        calls = " ".join(f"{(t.get('function') or {}).get('name')}({str((t.get('function') or {}).get('arguments'))[:300]})"
                         for t in m.get("tool_calls") or [])
        lines.append(f"[{m.get('role')}] {str(c)[:2500]} {calls}".strip())
    return "\n".join(lines)


def key_terms(event):
    """The words that show a fact was kept: file names, numbers with their unit, error types, decision nouns."""
    t = event["text"]
    terms = set(re.findall(r"[\w.-]+\.(?:py|md|json|js|ts|csv|txt|toml|yaml|html)\b", t))
    terms |= set(re.findall(r"\b\d+ (?:passed|failed)\b", t))
    terms |= set(re.findall(r"\b\w*(?:Error|Exception)\b", t))
    if event["kind"] == "decided":
        terms |= {w.lower() for w in re.findall(r"[A-Za-z_][\w-]{4,}", t)[:4]}
    return {x for x in terms if x}


def score(answer, events):
    a = answer.lower()
    facts = [(e, key_terms(e)) for e in events if e["kind"] in ("created", "decided", "tests", "error")]
    facts = [(e, k) for e, k in facts if k]
    kept = [e for e, k in facts if any(x.lower() in a for x in k)]
    return len(kept), len(facts), [e for e, _k in facts if e not in kept]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conv")
    ap.add_argument("--snapshot")
    ap.add_argument("--base-url", required=True, help="model answering the quiz (OpenAI-compatible /v1)")
    ap.add_argument("--model", default="local")
    ap.add_argument("--summarizer-url", help="model writing the summaries (default: the same)")
    a = ap.parse_args()
    path = a.snapshot or (cm.snapshots(a.conv)[-1:] or [None])[0]
    if not path:
        raise SystemExit("no snapshot: pass --snapshot, or --conv of a chat that has compacted at least once")
    snap = json.load(open(path, encoding="utf-8"))
    hist, cut, events = snap["history"], int(snap["cut"]), snap.get("timeline") or []
    old_part, kept = flatten(hist[:cut]), hist[cut:]
    summ = a.summarizer_url or a.base_url
    import agentic                                             # the current template, as the app uses it
    timeline = "\n".join(f"- [{e['kind']}] {e['text']}" for e in events)
    results = {}
    for style in ("old", "new"):
        if style == "old":
            prompt = OLD_PROMPT + "\n\nCONVERSATION:\n" + old_part
            summary = chat(summ, a.model, [{"role": "user", "content": prompt}], 1600)
            head = "[Summary of the earlier part of this conversation]\n" + summary
        else:
            prompt = agentic.SUMMARY_PROMPT + ("\n\nTurning points the app recorded:\n" + timeline if timeline else "") \
                + "\n\nCONVERSATION:\n" + old_part
            summary = chat(summ, a.model, [{"role": "user", "content": prompt}], 1600)
            head = ("[Summary of the earlier part of this conversation]\n" + summary
                    + ("\n\n[Pinned]\nTurning points, recorded by the app as they happened:\n" + timeline if timeline else ""))
        msgs = [{"role": "system", "content": "You are an agent continuing a software task."},
                {"role": "user", "content": head}]
        for m in kept:                                            # the verbatim recent part, as text
            role = "assistant" if m.get("role") == "assistant" else "user"
            msgs.append({"role": role, "content": flatten([m])[:3000]})
        msgs.append({"role": "user", "content": QUIZ})
        answer = chat(a.base_url, a.model, msgs, 900)
        k, n, lost = score(answer, events)
        results[style] = {"kept": k, "facts": n, "summary_words": len(summary.split()),
                          "lost": [f"[{e['kind']}] {e['text'][:90]}" for e in lost][:8]}
        print(f"{style}: {k}/{n} facts recalled ({len(summary.split())}-word summary)", flush=True)
    out = os.path.splitext(path)[0] + "_quiz.json"
    json.dump(results, open(out, "w", encoding="utf-8"), indent=1)
    print("saved", out)


if __name__ == "__main__":
    main()
