"""Phrase memory, ontology test: do CATEGORY shapes help a model decode instances it has never seen?

Category vectors are averages of many instances' "what the definition added" deltas (train instances only);
they are injected at UNSEEN instances' names. If the shape of "band" / "river" / "moth" helps the model decode a
new band / river / moth, the right category must beat a wrong category — on behaviour, not just loss.

  delta_i  = h_k("{definition}\n\nTopic: {phrase}") - h_k("Topic: {phrase}")     (last phrase token, layer k)
  C_t      = mean(delta_i for train instances of type t)
Tests on held-out instances (never in any C_t):
  (1) held-out loss of the intro's next sentences after "Topic: {phrase}"
  (2) "Question: What kind of thing is {phrase}? Answer in a few words.\nAnswer:" -> does it name the category?
  conditions: base / own instance delta / right category / wrong category / definition in context (oracle)

python category.py      (env: PM_MODEL dir+gguf, PM_SAMPLE articles to scan)
"""
import collections
import json
import os
import random
import re
import sys
import time

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import encyclopedia  # noqa: E402
import proto  # noqa: E402

GGUF_DIR = os.environ.get("PM_GGUF_DIR", os.path.expanduser(r"~\models\hf-qwen3-0.6b"))
GGUF = os.environ.get("PM_GGUF", "qwen3-0.6b.gguf")
HF_DIR = os.path.join(GGUF_DIR, "hf")
SAMPLE = int(os.environ.get("PM_SAMPLE", "9000"))
N_TYPES, N_TRAIN, N_TEST = 20, 30, 10
OUT = proto.OUT

_TYPE_RX = re.compile(r"\b(?:is|was|are|were)\s+(?:a|an|the|one of the)\s+(.{3,90}?)(?=\s+(?:in|from|of|located|based|that|"
                      r"who|which|by|on|for|and|with|at|near|between|whose|known|founded|written|directed|released|"
                      r"produced|built|described|endemic|native)\b|[,.;:(]|$)")
_SKIP = {"member", "part", "former", "name", "list", "type", "kind", "term", "group", "number", "series", "one", "set"}


def head_type(definition):
    m = _TYPE_RX.search(definition)
    if not m:
        return None
    words = re.findall(r"[a-z][a-z-]+", m.group(1).lower())
    if not words:
        return None
    h = words[-1]
    if h in ("species", "genus") :                 # "a species of moth" -> moth
        mm = re.search(r"\b(?:species|genus)\s+of\s+([a-z-]+)", definition.lower())
        h = mm.group(1) if mm else h
    if h in _SKIP or len(h) < 3:
        return None
    return h


def build_dataset():
    cache = os.path.join(OUT, f"category_data_{SAMPLE}.json")
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8"))
    arch = encyclopedia._archive()
    random.seed(11)
    pool, seen = [], set()
    t0 = time.time()
    for _ in range(SAMPLE):
        try:
            e = arch.get_random_entry()
            if e.is_redirect or e.path in seen:
                continue
            seen.add(e.path)
            a = encyclopedia.article(e)
        except Exception:
            continue
        phrase = re.sub(r"\s*\(.*?\)\s*", " ", a["title"]).strip()
        if not (1 <= len(phrase.split()) <= 5) or not re.fullmatch(r"[A-Za-z0-9 .,'&-]+", phrase):
            continue
        ss = proto.sentences(a["intro"])
        if len(ss) < 2:
            continue
        d, held = ss[0], " ".join(ss[1:3])
        t = head_type(d)
        if not t or not (8 <= len(d.split()) <= 60) or not (10 <= len(held.split()) <= 90):
            continue
        pool.append({"phrase": phrase, "type": t, "definition": d, "heldout": held})
    cnt = collections.Counter(x["type"] for x in pool)
    types = [t for t, c in cnt.most_common() if c >= N_TRAIN + N_TEST][:N_TYPES]
    by = {t: [x for x in pool if x["type"] == t][:N_TRAIN + N_TEST] for t in types}
    data = {"types": types, "counts": {t: cnt[t] for t in types}, "by_type": by,
            "scanned": SAMPLE, "usable": len(pool), "seconds": round(time.time() - t0)}
    json.dump(data, open(cache, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return data


def load():
    from transformers import AutoModelForCausalLM, AutoTokenizer
    if os.path.isdir(HF_DIR):
        tok = AutoTokenizer.from_pretrained(HF_DIR)
        m = AutoModelForCausalLM.from_pretrained(HF_DIR, dtype=torch.bfloat16)
    else:
        tok = AutoTokenizer.from_pretrained(GGUF_DIR, gguf_file=GGUF)
        m = AutoModelForCausalLM.from_pretrained(GGUF_DIR, gguf_file=GGUF, dtype=torch.bfloat16)
        m.save_pretrained(HF_DIR)
        tok.save_pretrained(HF_DIR)
    return tok, m.cuda().eval()


@torch.no_grad()
def answer(tok, m, inj, prompt, inject_after=None, plan=None, n=12):
    ids = tok(prompt, return_tensors="pt").input_ids.cuda()
    if plan is not None:
        inj.plan = (plan[0], len(tok(inject_after).input_ids) - 1, plan[1])
    try:
        out = m(ids, use_cache=True)
    finally:
        inj.plan = None
    past, nxt, toks = out.past_key_values, out.logits[0, -1].argmax(), []
    for _ in range(n):
        t = int(nxt)
        if t == tok.eos_token_id or "\n" in tok.decode([t]):
            break
        toks.append(t)
        out = m(torch.tensor([[t]], device="cuda"), past_key_values=past, use_cache=True)
        past, nxt = out.past_key_values, out.logits[0, -1].argmax()
    return tok.decode(toks).strip()


def names_type(ans, t):
    a = ans.lower()
    return bool(re.search(rf"\b{re.escape(t)}(e?s)?\b", a))


def main():
    t0 = time.time()
    data = build_dataset()
    types = data["types"]
    print(f"{data['usable']} usable of {data['scanned']} scanned; {len(types)} types:",
          {t: data["counts"][t] for t in types}, f"({time.time() - t0:.0f}s)", flush=True)
    tok, m = load()
    inj = proto.Injector(m)
    L = len(inj.layers)
    layers = [int(x) for x in os.environ.get("PM_LAYERS", f"{L // 4},{L // 3},{L * 4 // 9},{L // 2},{L * 2 // 3}").split(",")]
    print(f"model loaded, {L} layers; testing layers {layers} ({time.time() - t0:.0f}s)", flush=True)

    deltas = {}                                          # (type, idx) -> {k: delta}
    for t in types:
        for i, x in enumerate(data["by_type"][t]):
            sd = proto.states(tok, m, f"{x['definition']}\n\nTopic: {x['phrase']}")
            sb = proto.states(tok, m, f"Topic: {x['phrase']}")
            deltas[(t, i)] = {k: (sd[k + 1] - sb[k + 1]) for k in layers}
    cat = {t: {k: torch.stack([deltas[(t, i)][k] for i in range(N_TRAIN)]).mean(0) for k in layers} for t in types}
    inorm = {k: sum(float(d[k].norm()) for d in deltas.values()) / len(deltas) for k in layers}
    cnorm = {k: sum(float(cat[t][k].norm()) for t in types) / len(types) for k in layers}
    print("mean |instance delta|:", {k: round(v, 1) for k, v in inorm.items()},
          " mean |category|:", {k: round(v, 1) for k, v in cnorm.items()}, flush=True)

    tests = [(t, i) for t in types for i in range(N_TRAIN, N_TRAIN + N_TEST)]
    rng = random.Random(3)
    wrong = {tt: rng.choice([u for u in types if u != tt[0]]) for tt in tests}
    X = lambda tt: data["by_type"][tt[0]][tt[1]]

    base = {tt: proto.heldout_loss(tok, m, inj, f"Topic: {X(tt)['phrase']}", X(tt)["heldout"]) for tt in tests}
    orac = {tt: proto.heldout_loss(tok, m, inj, f"{X(tt)['definition']}\n\nTopic: {X(tt)['phrase']}", X(tt)["heldout"])
            for tt in tests}
    b, o = sum(base.values()) / len(tests), sum(orac.values()) / len(tests)
    print(f"held-out loss: base {b:.3f} oracle {o:.3f}", flush=True)

    rows = []
    for k in layers:
        for scale in (1.0, 2.0, 4.0):
            res = {}
            for name in ("instance", "right", "wrong"):
                ls = []
                for tt in tests:
                    v = (deltas[tt][k] if name == "instance" else
                         cat[tt[0]][k] * inorm[k] / cnorm[k] if name == "right" else
                         cat[wrong[tt]][k] * inorm[k] / cnorm[k])      # categories scaled to instance size
                    ls.append(proto.heldout_loss(tok, m, inj, f"Topic: {X(tt)['phrase']}", X(tt)["heldout"],
                                                 (k, (scale * v).cuda())))
                res[name] = sum(ls) / len(ls)
            row = {"layer": k, "scale": scale, **{n: round(v, 4) for n, v in res.items()},
                   "right_vs_wrong": round(res["wrong"] - res["right"], 4),
                   "recovered_right": round((b - res["right"]) / (b - o), 3) if b > o else None}
            rows.append(row)
            print(row, flush=True)
    best = max(rows, key=lambda r: r["right_vs_wrong"])
    k, scale = best["layer"], best["scale"]
    print("best loss setting:", best, flush=True)

    gen = {n: [] for n in ("base", "instance", "right", "wrong", "oracle")}
    samples = []
    for tt in tests:
        x = X(tt)
        lead = f"Question: What kind of thing is {x['phrase']}"
        q = lead + "? Answer in a few words.\nAnswer:"
        vr = (cat[tt[0]][k] * inorm[k] / cnorm[k] * scale).cuda()
        vw = (cat[wrong[tt]][k] * inorm[k] / cnorm[k] * scale).cuda()
        ans = {"base": answer(tok, m, inj, q),
               "instance": answer(tok, m, inj, q, lead, (k, (deltas[tt][k] * scale).cuda())),
               "right": answer(tok, m, inj, q, lead, (k, vr)),
               "wrong": answer(tok, m, inj, q, lead, (k, vw)),
               "oracle": answer(tok, m, inj, f"{x['definition']}\n\n{q}")}
        for n, a in ans.items():
            gen[n].append(names_type(a, tt[0]))
        samples.append({"phrase": x["phrase"], "type": tt[0], "wrong_type": wrong[tt], **ans,
                        "wrong_named_wrong_type": names_type(ans["wrong"], wrong[tt])})
    acc = {n: round(sum(v) / len(v), 3) for n, v in gen.items()}
    hijack = round(sum(s["wrong_named_wrong_type"] for s in samples) / len(samples), 3)
    print("NAMES THE RIGHT CATEGORY:", acc, f"| wrong vector makes it name the wrong category: {hijack}", flush=True)
    json.dump({"model": GGUF, "types": types, "layers": layers, "rows": rows, "best": best, "base": b, "oracle": o,
               "category_accuracy": acc, "wrong_hijack": hijack, "samples": samples,
               "seconds": round(time.time() - t0)},
              open(os.path.join(OUT, "category.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"done {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
