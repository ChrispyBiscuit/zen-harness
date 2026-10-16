# Context packing benchmark — results (2026-10-15)

**Question:** can a packer put ALL the code pieces a question needs into one small window together?
50 gold questions (`gold_*.jsonl`), mostly cross-file (bug across layers, change impact, failing test + code, shared
state), over three codebases: Zen's backend at commit 98af6e9 (68 files, ~573K tokens), the 27B's expense tracker
`bonsai8` (14 files, ~21K tokens) and the original `FPS Arena` game (15 files, ~29K tokens). 219 gold pieces in total.
A question is solved only when every gold piece is >= 80% inside the packed lines. Embeddings: the app's
bge-small-en-v1.5 on the CPU. Reproduce: `venv\Scripts\python.exe evals\context_pack\run.py` (raw rows in
`results.json`).

## The window is not the limit
A perfect packer (exactly the units that hold the gold pieces) fits every question into 8K tokens:

| Codebase | Pieces per question | Tokens needed, median / max | Fits in 2K / 4K / 8K |
|---|---|---|---|
| Zen backend | 5.3 | 4,403 / 7,624 | 1 / 9 / 20 of 20 |
| bonsai8 | 3.9 | 1,278 / 1,957 | 15 / 15 / 15 |
| FPS Arena | 3.7 | 868 / 2,854 | 12 / 15 / 15 |

## What the packers achieve (all 50 questions)
"Set" = all gold pieces in the window; "pieces" = share of gold pieces in the window.

| Packer | 2K set / pieces | 4K set / pieces | 8K set / pieces |
|---|---|---|---|
| Whole files ranked by hybrid search (today's read_file habit) | 4% / 16% | 10% / 28% | 18% / 45% |
| Function-level chunks, BM25 | 4% / 32% | 6% / 40% | 20% / 58% |
| Function-level chunks, embeddings | 8% / 35% | 16% / 48% | **42%** / 65% |
| Function-level chunks, hybrid (BM25 + embeddings, RRF) | 6% / 37% | **18%** / **54%** | 34% / **69%** |
| Structural: named seeds + top-3 hits, then callees/callers, then hybrid fill | 4% / 33% | 12% / 47% | 26% / 64% |
| Structural, top-5 hits as seeds | 4% / 34% | 14% / 52% | 22% / 64% |
| Structural only (named seeds + one hop, no fill) | 0% / 19% | 0% / 23% | 0% / 24% |

By codebase at 8K (set): Zen 0-2 of 20 for every packer; bonsai8 3/15 whole files vs 10/15 embedding chunks;
FPS 6/15 whole files vs 10/15 hybrid chunks.

## Verdict
1. **Function-level slices instead of whole files roughly double success** (18% -> 34-42% at 8K) — the clear win.
   `backend/context_pack.py` (`focus_pack`) packages it; on the two small gold sets it scores 4-5/15 at 4K and
   5-8/15 at 8K, in line with the hybrid row.
2. **Selection is now the bottleneck**: the best rankers put about two thirds of the needed pieces in the window,
   and a question needs 4-5 pieces, so one miss loses it. On the large backend nothing works yet even though a
   perfect packer fits all 20 questions.
3. **Naive structural expansion does not help**: adding every callee and caller crowds out better pieces.
   Structure should be used selectively (e.g. only definitions the top hits call, within a small share of the budget).
4. Not measured: a code-specific embedding model (jina-embeddings-v2-base-code took 45+ min and ~2 GB of RAM to
   index the backend on this CPU — dropped; worth retrying only on the GPU in an idle-time batch), query rewriting by
   the model, and LLM reranking.

## Round 2: smaller slices and link-following (`measure_focus.py`, `focus_results.json`)
`context_pack.focus_pack` itself, on the same 50 questions. Slices = the window size for long definitions; links =
boost what the top hits call and what calls them (seeds, boost added to the RRF score).

| Slices | Links | 2K all / pieces | 4K all / pieces | 8K all / pieces | Zen 8K all |
|---|---|---|---|---|---|
| 120 | off | 3/50 / 35% | 9/50 / 54% | 16/50 / 68% | 3/20 |
| 120 | 5, 0.005 | 3/50 / 42% | 6/50 / 53% | 15/50 / 70% | 2/20 |
| 120 | 5, 0.02 | 3/50 / 28% | 6/50 / 39% | 15/50 / 62% | 2/20 |
| **40** | **off** | **4/50 / 40%** | 7/50 / 53% | **20/50 / 68%** | 3/20 |
| 40 | 3, 0.01 | 3/50 / 38% | 8/50 / 52% | 18/50 / 70% | 4/20 |
| 40 | 5, 0.005 | 4/50 / 41% | **9/50 / 56%** | 17/50 / 68% | 3/20 |
| 40 | 5, 0.02 | 3/50 / 25% | 6/50 / 41% | 15/50 / 64% | 2/20 |

- **40-line windows help a little** (+4 questions at 8K, all on the small projects) and are now the default.
- **Link-following does not help; a strong boost hurts.** A miss analysis found 47 of the 68 pieces the best packer
  missed were one call away from a packed piece, but every top hit calls many definitions and a flat boost lifts all
  of them: the right neighbour is reachable, not selectable. Left in the code, off by default (LINK_SEEDS = 0).
- **Zen's backend stays at 2-4 of 20** (about 60% of pieces): ~5 pieces per question from ~570K tokens.
- Next candidates need a signal that says which neighbour matters for this question: rank linked definitions by their
  own match to the question instead of a flat boost (offline, cheap), or a second round where the model reads the
  first pack and names what is missing (needs the model; most promising for the large codebase).

## Round 3: a model-driven second round (`second_round.py`, `second_round.json`)
Same total budget either way. Two rounds = a first pack of 60% of the budget; Ternary Bonsai 2 9B (GPU, thinking off)
reads it with the question and names what is still missing (exact names or short phrases, JSON); a second pack fills
the remaining 40% with the named definitions first, then a search on its request. Median model call 3.9 s.

| Budget | Single pack: all / pieces | First pack alone (60%) | Two rounds: all / pieces |
|---|---|---|---|
| 4K | 7/50 / 53% | 6/50 / 42% | **11/50 / 60%** |
| 8K | 20/50 / 68% | 10/50 / 56% | **21/50 / 76%** |

| Codebase | 4K single -> two rounds (pieces) | 8K single -> two rounds (pieces) | Questions better / worse at 8K |
|---|---|---|---|
| bonsai8 | 53% -> 72% | 79% -> 88% | 6 / 2 |
| FPS Arena | 65% -> 65% | 84% -> 78% | 2 / 4 |
| Zen backend | 45% -> 51% | 54% -> **69%** | **10 / 0** |

- The second round helps most where single packs do worst: Zen's backend at 8K gains 15 points of piece recall and is
  never worse. The model reads names in the first pack (`_STUB_TOOLS`, `_slim_call_args`) and asks for them exactly.
- Where the first pack is already good (FPS at 8K, 84%), the fixed 60/40 split displaces good slices: worse on 4 of 15.
- "All pieces together" moves little (20 -> 21 of 50 at 8K): questions need 4-5 pieces and one miss still loses them.
- Fixes to try: let the model say nothing is missing and keep the full single pack; replace the lowest-ranked slices
  instead of splitting the budget up front; allow a third round when it still names missing code.
