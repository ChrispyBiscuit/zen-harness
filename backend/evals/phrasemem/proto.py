"""Phrase-memory prototype (train-free, Engram-style sidecar), step 1: does one stored vector carry an entity's meaning?

For each entity (random offline-Wikipedia article):
  value v_k = h_k("{definition}\n\nTopic: {phrase}") - h_k("Topic: {phrase}")   at the phrase's last token, layer k
  i.e. what reading the definition added to the model's state of the phrase, in the model's OWN space (no training).
Test on held-out text (the intro's next sentences, never used to build v):
  base    : "Topic: {phrase}\n{held-out}"                       loss on held-out tokens
  inject  : same, + alpha * v_k added at the phrase's last token, layer k
  oracle  : "{definition}\n\nTopic: {phrase}\n{held-out}"      (= what retrieval would give)
  control : inject ANOTHER entity's vector (must not help)
recovered = (base - inject) / (base - oracle): the share of the definition's benefit one vector carries.

python proto.py [n_entities]     (GPU must be free: unload the app's model first)
"""
import json
import os
import random
import re
import sys
import time

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import encyclopedia  # noqa: E402

MODEL = "Qwen/Qwen3-4B"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 120
LAYERS = [int(x) for x in os.environ.get('PM_LAYERS', '6,8,10,12,14,16').split(',')]
ALPHAS = [float(x) for x in os.environ.get('PM_ALPHAS', '0.5,0.75,1.0,1.5').split(',')]
OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


def sample_entities(n, seed=7):
    cache = os.path.join(OUT, f"entities_{n}.json")
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8"))
    arch = encyclopedia._archive()
    random.seed(seed)
    out, tries = [], 0
    while len(out) < n and tries < n * 60:
        tries += 1
        try:
            e = arch.get_random_entry()
            if e.is_redirect:
                continue
            a = encyclopedia.article(e)
        except Exception:
            continue
        phrase = re.sub(r"\s*\(.*?\)\s*", " ", a["title"]).strip()
        if not (2 <= len(phrase.split()) <= 5) or not re.fullmatch(r"[A-Za-z0-9 .,'&-]+", phrase):
            continue
        ss = sentences(a["intro"])
        if len(ss) < 3:
            continue
        d, held = ss[0], " ".join(ss[1:3])
        if not (8 <= len(d.split()) <= 60) or not (15 <= len(held.split()) <= 90):
            continue
        out.append({"title": a["title"], "phrase": phrase, "definition": d, "heldout": held})
    json.dump(out, open(cache, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return out


def load():
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tok = AutoTokenizer.from_pretrained(MODEL)
    q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    m = AutoModelForCausalLM.from_pretrained(MODEL, quantization_config=q, device_map="cuda:0", dtype=torch.bfloat16)
    m.eval()
    return tok, m


class Injector:
    """Adds alpha*v to the output of decoder layer k at one token position (prefill only)."""

    def __init__(self, model):
        self.layers = model.model.layers
        self.plan = None          # (k, pos, vec)
        self.hooks = [l.register_forward_hook(self._make(i)) for i, l in enumerate(self.layers)]

    def _make(self, i):
        def hook(mod, inp, out):
            if self.plan is None or self.plan[0] != i:
                return None
            _, pos, vec = self.plan
            h = out[0] if isinstance(out, tuple) else out
            h = h.clone()
            h[0, pos] += vec.to(h.dtype)
            return (h,) + tuple(out[1:]) if isinstance(out, tuple) else h
        return hook


@torch.no_grad()
def states(tok, m, text):
    ids = tok(text, return_tensors="pt").input_ids.cuda()
    out = m(ids, output_hidden_states=True)
    return [hs[0, -1].float() for hs in out.hidden_states]   # [embeddings, layer0 out, ..., layerL out]


@torch.no_grad()
def heldout_loss(tok, m, inj, prefix, held, plan=None):
    """mean NLL (nats/token) of `held` after `prefix`; plan=(k, vec) injects at the prefix's last token."""
    p_ids = tok(prefix, return_tensors="pt").input_ids[0]
    h_ids = tok("\n" + held, return_tensors="pt").input_ids[0]
    ids = torch.cat([p_ids, h_ids]).unsqueeze(0).cuda()
    inj.plan = (plan[0], len(p_ids) - 1, plan[1]) if plan else None
    try:
        logits = m(ids).logits[0].float()
    finally:
        inj.plan = None
    tgt = ids[0, len(p_ids):]
    lp = torch.log_softmax(logits[len(p_ids) - 1:-1], -1)
    return float(-lp.gather(1, tgt.unsqueeze(1)).mean())


def main():
    t0 = time.time()
    ents = sample_entities(N)
    print(f"{len(ents)} entities sampled ({time.time() - t0:.0f}s)", flush=True)
    tok, m = load()
    inj = Injector(m)
    print(f"model loaded ({time.time() - t0:.0f}s), {len(inj.layers)} layers", flush=True)

    vecs = []                                   # per entity: {k: v_k}
    for e in ents:
        sd = states(tok, m, f"{e['definition']}\n\nTopic: {e['phrase']}")
        sb = states(tok, m, f"Topic: {e['phrase']}")
        vecs.append({k: (sd[k + 1] - sb[k + 1]) for k in LAYERS})
    norms = {k: sum(float(v[k].norm()) for v in vecs) / len(vecs) for k in LAYERS}
    print("mean |v| per layer:", {k: round(x, 1) for k, x in norms.items()}, flush=True)

    base, orac = [], []
    for e in ents:
        base.append(heldout_loss(tok, m, inj, f"Topic: {e['phrase']}", e["heldout"]))
        orac.append(heldout_loss(tok, m, inj, f"{e['definition']}\n\nTopic: {e['phrase']}", e["heldout"]))
    b, o = sum(base) / len(base), sum(orac) / len(orac)
    print(f"base {b:.3f}  oracle {o:.3f}  (definition in context saves {b - o:.3f} nats/token)", flush=True)

    perm = list(range(len(ents)))
    random.Random(1).shuffle(perm)
    rows = []
    for k in LAYERS:
        for a in ALPHAS:
            inj_l, ctl_l = [], []
            for i, e in enumerate(ents):
                pre = f"Topic: {e['phrase']}"
                inj_l.append(heldout_loss(tok, m, inj, pre, e["heldout"], (k, a * vecs[i][k].cuda())))
                j = perm[i] if perm[i] != i else (i + 1) % len(ents)
                ctl_l.append(heldout_loss(tok, m, inj, pre, e["heldout"], (k, a * vecs[j][k].cuda())))
            il, cl = sum(inj_l) / len(inj_l), sum(ctl_l) / len(ctl_l)
            rec = (b - il) / (b - o) if b - o > 1e-6 else 0.0
            wins = sum(1 for x, y in zip(inj_l, base) if x < y) / len(base)
            spec = (cl - il) / (b - o) if b - o > 1e-6 else 0.0      # beyond what any entity's vector does
            row = {"layer": k, "alpha": a, "inject": round(il, 4), "control": round(cl, 4),
                   "recovered": round(rec, 3), "specific": round(spec, 3), "win_rate": round(wins, 3)}
            rows.append(row)
            print(row, flush=True)
    best = max(rows, key=lambda r: r["recovered"])
    res = {"model": MODEL, "n": len(ents), "base": b, "oracle": o, "norms": norms, "rows": rows, "best": best,
           "per_entity": [{"phrase": e["phrase"], "base": x, "oracle": y} for e, x, y in zip(ents, base, orac)],
           "seconds": round(time.time() - t0)}
    json.dump(res, open(os.path.join(OUT, "step1.json"), "w", encoding="utf-8"), indent=1)
    print("BEST:", best, f"total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
