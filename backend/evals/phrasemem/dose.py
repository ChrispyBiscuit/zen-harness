"""Dose-response check: can a category vector steer the 0.6B's answer AT ALL, and does it matter where it is added?
  mode "one" : add at the phrase's last token only (the design so far)
  mode "all" : add at every token from the phrase onward (classic steering)
Reports how often the answer names the injected category (right vector) and, for the wrong vector, the wrong one.
"""
import json
import os
import random
import sys

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import category as C  # noqa: E402
import proto  # noqa: E402


class SpanInjector(proto.Injector):
    """plan = (k, start, vec, mode): mode 'one' adds at start only, 'all' at start..end of the current pass."""

    def _make(self, i):
        def hook(mod, inp, out):
            if self.plan is None or self.plan[0] != i:
                return None
            _, pos, vec, mode = self.plan
            h = out[0] if isinstance(out, tuple) else out
            h = h.clone()
            if mode == "one":
                if h.shape[1] > pos:
                    h[0, pos] += vec.to(h.dtype)
            else:
                lo = pos if h.shape[1] > pos else 0       # decode steps (1 token) get it too
                h[0, lo:] += vec.to(h.dtype)
            return (h,) + tuple(out[1:]) if isinstance(out, tuple) else h
        return hook


@torch.no_grad()
def answer(tok, m, inj, prompt, after, plan, n=12):
    ids = tok(prompt, return_tensors="pt").input_ids.cuda()
    k, vec, mode = plan
    inj.plan = (k, len(tok(after).input_ids) - 1, vec, mode)
    try:
        out = m(ids, use_cache=True)
        past, nxt, toks = out.past_key_values, out.logits[0, -1].argmax(), []
        for _ in range(n):
            t = int(nxt)
            if t == tok.eos_token_id or "\n" in tok.decode([t]):
                break
            toks.append(t)
            if mode == "one":
                inj.plan = None
            out = m(torch.tensor([[t]], device="cuda"), past_key_values=past, use_cache=True)
            past, nxt = out.past_key_values, out.logits[0, -1].argmax()
    finally:
        inj.plan = None
    return tok.decode(toks).strip()


def main():
    data = C.build_dataset()
    types = data["types"]
    tok, m = C.load()
    inj = SpanInjector(m)
    k = int(os.environ.get("PM_LAYER", "9"))
    vecs = {}
    for t in types:
        ds = []
        for x in data["by_type"][t][:C.N_TRAIN]:
            sd = proto.states(tok, m, f"{x['definition']}\n\nTopic: {x['phrase']}")
            sb = proto.states(tok, m, f"Topic: {x['phrase']}")
            ds.append(sd[k + 1] - sb[k + 1])
        vecs[t] = torch.stack(ds).mean(0)
    tests = [(t, x) for t in types for x in data["by_type"][t][C.N_TRAIN:C.N_TRAIN + C.N_TEST]]
    rng = random.Random(3)
    wrong = [rng.choice([u for u in types if u != t]) for t, _ in tests]
    res = []
    for mode in ("one", "all"):
        for scale in (1, 4, 8, 16, 32):
            r_ok = w_hit = 0
            ex = None
            for (t, x), wt in zip(tests, wrong):
                lead = f"Question: What kind of thing is {x['phrase']}"
                q = lead + "? Answer in a few words.\nAnswer:"
                ar = answer(tok, m, inj, q, lead, (k, (vecs[t] * scale).cuda(), mode))
                aw = answer(tok, m, inj, q, lead, (k, (vecs[wt] * scale).cuda(), mode))
                r_ok += C.names_type(ar, t)
                w_hit += C.names_type(aw, wt)
                ex = ex or (x["phrase"], t, ar, wt, aw)
            row = {"mode": mode, "scale": scale, "right_named": round(r_ok / len(tests), 3),
                   "wrong_vector_names_wrong": round(w_hit / len(tests), 3), "example": ex}
            res.append(row)
            print(row, flush=True)
    json.dump(res, open(os.path.join(C.OUT, "dose.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
