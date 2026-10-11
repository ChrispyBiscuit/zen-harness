"""Phrase-memory prototype, step 2: does the injected vector change what the model UNDERSTANDS, in other wording?

Uses step 1's entities and its best (layer, alpha) unless PM_LAYER / PM_ALPHA are set.
  (a) explain : "Question: What is {phrase}? Answer in one sentence.\nAnswer:"  greedy 48 tokens
                base / inject (at the phrase in the question) / oracle (definition in context) / control (other vector)
                score = content-word F1 against the Wikipedia definition (phrase words and stop words removed)
  (b) wording : held-out loss with a different lead-in than the one the vector was built with:
                "Here is what I know about {phrase}." — does the vector transfer to new wording?
python step2.py
"""
import json
import os
import random
import re
import sys
import time

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proto  # noqa: E402

OUT = proto.OUT
STOP = set("""a an the of in on at to for and or is are was were be been being it its this that these those as by with
from which who whom whose what when where why how not no but also has have had he she they his her their there here
one two three into than then such can could would should will may might about over after before during between
under all any some more most other only own same so very i you we our your answer question sentence""".split())


def words(s):
    return [w for w in re.findall(r"[a-z][a-z0-9'-]+", s.lower()) if w not in STOP]


def f1(pred, ref, drop):
    p = [w for w in words(pred) if w not in drop]
    r = set(w for w in words(ref) if w not in drop)
    if not p or not r:
        return 0.0
    hit = sum(1 for w in set(p) if w in r)
    if not hit:
        return 0.0
    prec, rec = hit / len(set(p)), hit / len(r)
    return 2 * prec * rec / (prec + rec)


@torch.no_grad()
def generate(tok, m, inj, prompt, inject_after=None, plan=None, n=48):
    """greedy; plan=(k, vec) is added at the last token of `inject_after` (a prefix of the prompt), prefill only."""
    ids = tok(prompt, return_tensors="pt").input_ids.cuda()
    if plan is not None:
        pos = len(tok(inject_after).input_ids) - 1
        inj.plan = (plan[0], pos, plan[1])
    try:
        out = m(ids, use_cache=True)
    finally:
        inj.plan = None
    past, nxt, toks = out.past_key_values, out.logits[0, -1].argmax(), []
    for _ in range(n):
        t = int(nxt)
        if t == tok.eos_token_id:
            break
        toks.append(t)
        if "\n" in tok.decode([t]):
            break
        out = m(torch.tensor([[t]], device="cuda"), past_key_values=past, use_cache=True)
        past, nxt = out.past_key_values, out.logits[0, -1].argmax()
    return tok.decode(toks).strip()


def main():
    t0 = time.time()
    s1 = json.load(open(os.path.join(OUT, "step1.json"), encoding="utf-8"))
    k = int(os.environ.get("PM_LAYER") or s1["best"]["layer"])
    a = float(os.environ.get("PM_ALPHA") or s1["best"]["alpha"])
    ents = proto.sample_entities(s1["n"])
    tok, m = proto.load()
    inj = proto.Injector(m)
    vec = []
    for e in ents:
        sd = proto.states(tok, m, f"{e['definition']}\n\nTopic: {e['phrase']}")
        sb = proto.states(tok, m, f"Topic: {e['phrase']}")
        vec.append((sd[k + 1] - sb[k + 1]).cuda() * a)
    perm = list(range(len(ents)))
    random.Random(1).shuffle(perm)
    print(f"layer {k}, alpha {a}, {len(ents)} entities", flush=True)

    rows, sc = [], {"base": [], "inject": [], "oracle": [], "control": []}
    for i, e in enumerate(ents):
        lead = f"Question: What is {e['phrase']}"
        q = lead + "? Answer in one sentence.\nAnswer:"
        drop = set(words(e["phrase"]))
        j = perm[i] if perm[i] != i else (i + 1) % len(ents)
        ans = {"base": generate(tok, m, inj, q),
               "inject": generate(tok, m, inj, q, lead, (k, vec[i])),
               "oracle": generate(tok, m, inj, f"{e['definition']}\n\n{q}"),
               "control": generate(tok, m, inj, q, lead, (k, vec[j]))}
        r = {"phrase": e["phrase"], "definition": e["definition"]}
        for name, txt in ans.items():
            s = f1(txt, e["definition"], drop)
            sc[name].append(s)
            r[name] = txt
            r[name + "_f1"] = round(s, 3)
        rows.append(r)
        if i % 20 == 0:
            print(i, {n: round(sum(v) / len(v), 3) for n, v in sc.items()}, flush=True)
    explain = {n: round(sum(v) / len(v), 4) for n, v in sc.items()}
    better = sum(1 for r in rows if r["inject_f1"] > r["base_f1"])
    worse = sum(1 for r in rows if r["inject_f1"] < r["base_f1"])
    print("EXPLAIN F1:", explain, f"inject better {better} / worse {worse} / {len(rows)}", flush=True)

    wb, wi, wc = [], [], []
    for i, e in enumerate(ents):
        pre = f"Here is what I know about {e['phrase']}"
        held = e["heldout"]
        j = perm[i] if perm[i] != i else (i + 1) % len(ents)
        wb.append(proto.heldout_loss(tok, m, inj, pre, held))
        wi.append(proto.heldout_loss(tok, m, inj, pre, held, (k, vec[i])))
        wc.append(proto.heldout_loss(tok, m, inj, pre, held, (k, vec[j])))
    wording = {"base": sum(wb) / len(wb), "inject": sum(wi) / len(wi), "control": sum(wc) / len(wc),
               "win_rate": sum(1 for x, y in zip(wi, wb) if x < y) / len(wb)}
    print("WORDING (held-out loss, new lead-in):", {x: round(y, 4) for x, y in wording.items()}, flush=True)
    json.dump({"layer": k, "alpha": a, "explain": explain, "inject_better": better, "inject_worse": worse,
               "wording": wording, "rows": rows, "seconds": round(time.time() - t0)},
              open(os.path.join(OUT, "step2.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"done {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
