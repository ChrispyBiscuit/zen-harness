"""Training-free steering test: can a "careful condition-reading" control vector make a model pick the right item?

Benchmark (generated, random names/numbers so nothing comes from memory): a passage lists several similar items;
the question asks for exactly one (year-end vs peak vs debut, set vs costume designer, abstained vs against…).
Control vector (Contrastive Activation Addition): mean over TRAIN pairs of
    h_L(prompt + correct answer) - h_L(prompt + distractor answer)     at the answer's last token
Steering: add s * v at layer L to EVERY token (prompt and generated), no training, no extra context.
TRAIN templates and TEST templates are different (does it carry "read the condition" in general?).

python condvec.py     (env PM_MODEL=Qwen/Qwen3-4B, CV_LAYERS, CV_SCALES)
"""
import json
import os
import random
import re
import sys
import time

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)
MODEL = os.environ.get("PM_MODEL", "Qwen/Qwen3-4B")

FIRST = "Mara Tomas Ilse Joran Petra Kofi Anika Lucan Oriel Dalia Henrik Sabine Viktor Noemi Rafe Celia Bram Ines".split()
LAST = "Vell Okafor Brandt Sorensen Ruiz Haldane Mbeki Castell Ferro Lindqvist Duarte Achebe Kowal Renner Sato".split()
WORDS = "Amber Hollow Signal Northern Glass Quiet Harbor Iron Paper Silver Winter Echo Velvet Distant Copper".split()


def name(r):
    return f"{r.choice(FIRST)} {r.choice(LAST)}"


def uniq_names(r, n):
    out = set()
    while len(out) < n:
        out.add(name(r))
    return list(out)


def nums(r, n, lo, hi):
    return r.sample(range(lo, hi), n)


# ---- templates: each returns (passage, question, correct, [distractors]) ----
def t_chart(r):                                    # TRAIN
    alb = f"{r.choice(WORDS)} {r.choice(WORDS)}"
    d, p, e = nums(r, 3, 2, 199)
    y = r.randint(1975, 2020)
    psg = (f"{alb} by {name(r)} debuted at number {d} on the national albums chart in {y}. It peaked at number {p} "
           f"later that year, and on the {y} year-end albums chart it ranked number {e}.")
    which = r.choice([("year-end position", e, [d, p]), ("debut position", d, [p, e]), ("peak position", p, [d, e])])
    return psg, f"What was the album's {which[0]} in {y}?", str(which[1]), [str(x) for x in which[2]]


def t_career(r):                                   # TRAIN
    who = name(r)
    y1, y2, y3 = sorted(nums(r, 3, 1950, 2022))
    psg = (f"{who} joined the ministry as a junior analyst in {y1}. {who.split()[0]} was promoted to deputy director "
           f"in {y2} and retired in {y3}.")
    which = r.choice([("promoted to deputy director", y2, [y1, y3]), ("join the ministry", y1, [y2, y3]),
                      ("retire", y3, [y1, y2])])
    q = (f"In what year was {who} promoted to deputy director?" if which[0].startswith("promoted")
         else f"In what year did {who} {which[0]}?")
    return psg, q, str(which[1]), [str(x) for x in which[2]]


def t_vote(r):                                     # TRAIN
    a, b, c = uniq_names(r, 3)
    n = r.randint(9, 31)
    psg = (f"All {n} council members voted for the budget except {a} and {b}, who abstained, and {c}, who voted "
           f"against it.")
    if r.random() < 0.5:
        return psg, "Which council member voted against the budget?", c, [a, b]
    return psg, f"Did {c} abstain or vote against the budget?", "against", ["abstain"]


def t_stage(r):                                    # TEST
    d, s, c, l = uniq_names(r, 4)
    play = f"The {r.choice(WORDS)} {r.choice(['Hour', 'Garden', 'Crossing', 'Letters', 'Tide'])}"
    y = r.randint(1960, 2024)
    psg = (f"The {y} production of {play} at the city theatre was directed by {d}, with sets designed by {s} and "
           f"costumes by {c}. Lighting was by {l}.")
    which = r.choice([("Who designed the sets", s, [d, c, l]), ("Who designed the costumes", c, [d, s, l]),
                      ("Who did the lighting", l, [d, s, c])])
    return psg, f"{which[0]} for the {y} production of {play}?", which[1], which[2]


def t_population(r):                               # TEST
    town = f"{r.choice(WORDS)}ford"
    y1 = r.randint(1900, 1990)
    y2 = y1 + r.choice([10, 20, 30])
    a, b = sorted(nums(r, 2, 1200, 90000))
    c = r.randint(300, a - 1)
    psg = (f"In {y1}, {town} had a population of {a:,}. By {y2} it had grown to {b:,}, of whom {c:,} lived in the "
           f"old town centre.")
    which = r.choice([(f"What was {town}'s population in {y2}?", b, [a, c]),
                      (f"What was {town}'s population in {y1}?", a, [b, c]),
                      (f"How many people lived in {town}'s old town centre in {y2}?", c, [a, b])])
    return psg, which[0], f"{which[1]:,}", [f"{x:,}" for x in which[2]]


def t_race(r):                                     # TEST
    a, b, c, d = uniq_names(r, 4)
    psg = (f"{a} crossed the line first, {b} second, {c} third and {d} fourth. {b} was later disqualified, and "
           f"the official results moved everyone behind {b.split()[0]} up one place.")
    which = r.choice([("Who is officially in second place?", c, [b, a, d]),
                      ("Who is officially in third place?", d, [c, b]),
                      ("Who crossed the line second?", b, [c, a])])
    return psg, which[0], which[1], which[2]


TRAIN_T = [t_chart, t_career, t_vote]
TEST_T = [t_stage, t_population, t_race]


def make(templates, n, seed):
    r = random.Random(seed)
    return [templates[i % len(templates)](r) + (templates[i % len(templates)].__name__,) for i in range(n)]


def prompt(psg, q):
    return f"Passage: {psg}\nQuestion: {q}\nAnswer (just the answer):"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def correct(ans, gold, distract):
    a = norm(ans.split("\n")[0])
    return norm(gold) in a and not any(norm(d) in a for d in distract if norm(d) not in norm(gold))


def load():
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tok = AutoTokenizer.from_pretrained(MODEL)
    if "0.6" in MODEL or os.path.isdir(MODEL):
        m = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.bfloat16).cuda()
    else:
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
        m = AutoModelForCausalLM.from_pretrained(MODEL, quantization_config=q, device_map="cuda:0", dtype=torch.bfloat16)
    return tok, m.eval()


class Steer:
    def __init__(self, m):
        self.plan = None                                   # (layer, vec)
        for i, l in enumerate(m.model.layers):
            l.register_forward_hook(self._mk(i))

    def _mk(self, i):
        def hook(mod, inp, out):
            if not self.plan or self.plan[0] != i:
                return None
            h = out[0] if isinstance(out, tuple) else out
            h = h + self.plan[1].to(h.dtype)
            return (h,) + tuple(out[1:]) if isinstance(out, tuple) else h
        return hook


@torch.no_grad()
def gen(tok, m, st, text, plan=None, n=12):
    st.plan = plan
    try:
        ids = tok(text, return_tensors="pt").input_ids.cuda()
        out = m.generate(ids, max_new_tokens=n, do_sample=False, pad_token_id=tok.eos_token_id)
    finally:
        st.plan = None
    return tok.decode(out[0, ids.shape[1]:], skip_special_tokens=True).strip()


@torch.no_grad()
def last_states(tok, m, text):
    ids = tok(text, return_tensors="pt").input_ids.cuda()
    return [h[0, -1].float() for h in m(ids, output_hidden_states=True).hidden_states]


def evaluate(tok, m, st, items, plan=None):
    ok, rows = 0, []
    for psg, q, gold, dis, tname in items:
        a = gen(tok, m, st, prompt(psg, q), plan)
        c = correct(a, gold, dis)
        ok += c
        rows.append({"t": tname, "q": q, "gold": gold, "ans": a, "ok": c})
    return ok / len(items), rows


def main():
    t0 = time.time()
    train = make(TRAIN_T, 150, 1)
    test = make(TEST_T, 120, 2)
    test_in = make(TRAIN_T, 60, 3)                         # same templates as the vector, new fills
    tok, m = load()
    st = Steer(m)
    L = len(m.model.layers)
    layers = [int(x) for x in os.environ.get("CV_LAYERS", f"{L // 3},{L * 4 // 9},{L // 2},{L * 2 // 3}").split(",")]
    scales = [float(x) for x in os.environ.get("CV_SCALES", "0.5,1,2,4").split(",")]
    print(f"{MODEL}: {L} layers; layers {layers}; scales {scales} ({time.time() - t0:.0f}s)", flush=True)

    base_out, rows_out = evaluate(tok, m, st, test)
    base_in, _ = evaluate(tok, m, st, test_in)
    per_t = {}
    for r in rows_out:
        per_t.setdefault(r["t"], []).append(r["ok"])
    print(f"baseline: new templates {base_out:.3f} {({k: round(sum(v) / len(v), 2) for k, v in per_t.items()})} | "
          f"vector's own templates {base_in:.3f} ({time.time() - t0:.0f}s)", flush=True)

    diffs = {k: [] for k in layers}                        # contrast pairs from TRAIN templates only
    for psg, q, gold, dis, _ in train:
        p = prompt(psg, q)
        hp = last_states(tok, m, p + " " + gold)
        hn = last_states(tok, m, p + " " + random.Random(hash(q) % 1000).choice(dis))
        for k in layers:
            diffs[k].append(hp[k + 1] - hn[k + 1])
    vecs = {k: torch.stack(v).mean(0) for k, v in diffs.items()}
    print("vector norms:", {k: round(float(v.norm()), 2) for k, v in vecs.items()}, flush=True)

    rows = []
    for k in layers:
        for s in scales:
            acc_out, _ = evaluate(tok, m, st, test, (k, (s * vecs[k]).cuda()))
            acc_in, _ = evaluate(tok, m, st, test_in, (k, (s * vecs[k]).cuda()))
            neg_out, _ = evaluate(tok, m, st, test, (k, (-s * vecs[k]).cuda()))
            row = {"layer": k, "scale": s, "new_templates": round(acc_out, 3), "own_templates": round(acc_in, 3),
                   "reversed_vector": round(neg_out, 3)}
            rows.append(row)
            print(row, flush=True)
    best = max(rows, key=lambda r: r["new_templates"])
    json.dump({"model": MODEL, "baseline_new": base_out, "baseline_own": base_in, "rows": rows, "best": best,
               "baseline_rows": rows_out, "seconds": round(time.time() - t0)},
              open(os.path.join(OUT, f"condvec_{MODEL.replace('/', '_')}.json"), "w", encoding="utf-8"), indent=1)
    print(f"BEST {best}  (baseline new {base_out:.3f}, own {base_in:.3f})  {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
