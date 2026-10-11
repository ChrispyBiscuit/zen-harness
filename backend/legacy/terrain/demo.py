"""
Terrain end-to-end slice — proof that map -> steer -> changed output works on the
real substrate (Gemma-2-2B + Gemma Scope).

Run AFTER `huggingface-cli login` (Gemma + Gemma Scope licenses accepted) and with a
free GPU (not while a fine-tune is training):

    python terrain/demo.py
    python terrain/demo.py --probe "medicine, symptoms, diagnosis" --weight 10
    python terrain/demo.py --feature 4231 --weight 8

Picks the most-active feature on a probe phrase (unless --feature is given), then shows
baseline vs. amplified vs. suppressed generation for the same prompt.
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import TerrainEngine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", type=int, default=12)
    ap.add_argument("--prompt", default="I want to tell you about")
    ap.add_argument("--probe", default="dogs, puppies, golden retrievers, wagging tails")
    ap.add_argument("--feature", type=int, default=None)
    ap.add_argument("--weight", type=float, default=8.0)
    ap.add_argument("--tokens", type=int, default=60)
    args = ap.parse_args()

    print(f"loading Gemma-2-2b + Gemma Scope SAE (layer {args.layer})…", flush=True)
    eng = TerrainEngine(layer=args.layer)
    print(f"dictionary: {eng.n_features} features · d_model {eng.d_model}\n", flush=True)

    feature = args.feature
    if feature is None:
        top = eng.top_features(args.probe, k=8)
        print(f"top features on probe {args.probe!r}:")
        for i, v in top:
            print(f"   feature {i:>6}   activation {v:6.2f}")
        feature = top[0][0]

    print(f"\n>>> steering feature {feature} at ±{args.weight}\n")
    base = None
    for w, label in [(0.0, "BASELINE"), (args.weight, "AMPLIFY"),
                     (-args.weight, "SUPPRESS")]:
        if w == 0.0:
            eng.clear_steer()
            text = eng.generate(args.prompt, args.tokens)
            base = text
        else:
            eng.set_steer(feature, w)
            text = eng.generate(args.prompt, args.tokens)
            eng.clear_steer()
        print("=" * 72)
        print(f"{label:9} (weight {w:+.1f})   prompt: {args.prompt!r}")
        print("  " + text.strip()[:320].replace("\n", "\n  "))
        print()


if __name__ == "__main__":
    main()
