"""Grade a SimpleQA run (run_eval.py with SIMPLEQA_N set) the official way: CORRECT / INCORRECT / NOT_ATTEMPTED,
graded by an OpenAI model (default gpt-6-luna, ~$0.0002 per answer).

  set SIMPLEQA_N=100 && venv\\Scripts\\python.exe evals\\agent_eval\\grade_simpleqa.py runs\\<run>

Metrics as in simple-evals: correct %, incorrect %, not attempted %, correct given attempted, F-score (harmonic mean
of correct and correct-given-attempted).
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
os.environ.setdefault("SIMPLEQA_N", "100")
import swarm  # noqa: E402
import tasks as T  # noqa: E402

MODEL = os.environ.get("GRADER", "gpt-6-luna")

# condensed from simple-evals' GRADER_TEMPLATE (same three grades and the same rules)
TEMPLATE = """Your job is to look at a question, a gold target, and a predicted answer, and then assign a grade of either \
["CORRECT", "INCORRECT", "NOT_ATTEMPTED"].

CORRECT: the predicted answer fully contains the important information of the gold target and contains no information \
that contradicts it. Hedging or extra detail is fine as long as the gold target is there and nothing contradicts it. \
Minor spelling differences of names, and numbers correct to the last significant figure of the gold target, are fine.
INCORRECT: any statement in the predicted answer contradicts the gold target (even if hedged, e.g. "I think it's X \
but not sure" where X is wrong), or it gives several candidate answers of which one is wrong.
NOT_ATTEMPTED: the important information of the gold target is not in the answer and nothing contradicts it (e.g. "I \
don't know", "I couldn't find it", or an answer about something else without a wrong claim).

Question: {question}
Gold target: {target}
Predicted answer: {predicted}

Grade the predicted answer of this new question as one of:
A: CORRECT
B: INCORRECT
C: NOT_ATTEMPTED
Just return the letters "A", "B", or "C", with no text around it."""


def _clean(s):
    """Drop characters that make the request invalid (lone surrogates, control chars) — one bad answer must not
    stop the grading."""
    s = (s or "").encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return "".join(ch for ch in s if ch in "\n\t" or ord(ch) >= 32)


def grade(q, target, predicted):
    import urllib.error
    body = {"model": MODEL, "messages": [{"role": "user", "content": _clean(TEMPLATE.format(
        question=q, target=target, predicted=(predicted or "")[:4000]))}],
            "max_completion_tokens": 300}                # the grader may spend a few reasoning tokens first
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
                                 data=json.dumps(body, ensure_ascii=True).encode(),
                                 headers={"Authorization": f"Bearer {swarm.api_key()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"  grader error {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        return "ERROR", 0, 0
    txt = (d["choices"][0]["message"]["content"] or "").strip().upper()
    u = d.get("usage") or {}
    return {"A": "CORRECT", "B": "INCORRECT", "C": "NOT_ATTEMPTED"}.get(txt[:1], "NOT_ATTEMPTED"), \
        u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def main(run):
    d = json.load(open(os.path.join(run, "results.json"), encoding="utf-8"))
    rows = [r for r in d["results"] if r["id"].startswith("sqa_")]
    out, pt, ct = [], 0, 0
    for r in rows:
        ref = T.SIMPLEQA[r["id"]]
        g, p, c = grade(ref["problem"], ref["answer"], r["answer"])
        pt, ct = pt + p, ct + c
        out.append({"id": r["id"], "q": ref["problem"], "gold": ref["answer"], "answer": r["answer"][:600],
                    "grade": g, "seconds": r["seconds"], "steps": r["steps"]})
    n = len(out)
    cor = sum(x["grade"] == "CORRECT" for x in out)
    inc = sum(x["grade"] == "INCORRECT" for x in out)
    err = sum(x["grade"] == "ERROR" for x in out)
    out_ok = n - err
    na = out_ok - cor - inc
    n = max(1, out_ok)
    cga = cor / max(1, cor + inc)
    f = 2 * (cor / n) * cga / max(1e-9, (cor / n) + cga)
    summary = {"n": n, "correct": cor / n, "incorrect": inc / n, "not_attempted": na / n, "correct_given_attempted": cga,
               "f_score": f, "grader_errors": err, "median_seconds": sorted(x["seconds"] for x in out)[n // 2],
               "grader": MODEL, "grader_tokens": [pt, ct]}
    json.dump({"summary": summary, "graded": out}, open(os.path.join(run, "simpleqa_graded.json"), "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
