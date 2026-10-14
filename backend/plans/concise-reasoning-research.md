# Concise reasoning ("caveman talk") — research notes, 2026-10-11

Scope: can we cut hidden-thinking tokens on small/quantized local models (Bonsai 2 27B ternary, Qwen3.5/Ornith 9B Q4) without losing agentic-coding accuracy? Evidence labels: **[paper]** = read full text/tables, **[abstract]** = abstract/listing only, **[anecdotal]** = vendor/practitioner blog.

## 1. Output-side concise reasoning: what's measured

| Method | Type | Savings | Accuracy | Models / tasks |
|---|---|---|---|---|
| Chain of Draft ("≤5 words per step") | prompt, few-shot | ~80% on GSM8K | −4.3 pts GSM8K (GPT-4o 95.4→91.1); ±2-4 pts on others | GPT-4o, Claude 3.5; math/date/sports/coin-flip. No code, no agents. [paper](https://arxiv.org/html/2502.18600v2) |
| CoD zero-shot | prompt | ~70% | GPT-4o 94.8→84.4; Claude 90.4→65.5 | same [paper](https://arxiv.org/html/2502.18600v2) |
| Concise CoT ("be concise") | prompt | −49% length | negligible on MCQ, **−27.7% on math** (GPT-3.5) | GPT-3.5/4 [abstract](https://arxiv.org/abs/2401.05618v2) |
| Sketch-of-Thought | prompt + router | up to 74-84% | roughly flat, gains on math/multi-hop | 18 datasets [abstract](https://arxiv.org/abs/2503.05179v4) |
| TALE-EP (budget in prompt) | prompt | −68% (148 vs 461 tok) | 83.8→81.0 avg | GPT-4o-mini etc. [paper](https://arxiv.org/html/2412.18547v5) |
| TokenSkip | SFT/LoRA | −40% | −0.4 pts at 14B, **−9.3 pts at 3B** (ratio 0.5) | Qwen2.5 3/7/14B, GSM8K [abstract+table](https://arxiv.org/abs/2502.12067) |
| C3oT | SFT | >50% | ~par | GSM8K/ECQA etc. [abstract](https://arxiv.org/abs/2412.11664) |
| L1 / LCPO | RL | user-set length | 1.5B beats GPT-4o at equal length | math [abstract](https://arxiv.org/abs/2503.04697) |
| ThinkPrune | RL | ~−50% | −2% AIME24 | R1-Distill-Qwen-1.5B [abstract](https://arxiv.org/abs/2504.01296) |
| AdaptThink | RL (think/no-think) | −53% | +2.4% | 1.5B/7B, math only [abstract](https://arxiv.org/abs/2505.13417) |
| DEER early exit | training-free, confidence probe at "Wait" | −19 to −80% | +0.3 to +5%; code −65% len, +2.1 pass@1 (v1) | R1-distills, Qwen3-14B [abstract](https://arxiv.org/abs/2504.15895) |
| s1 budget forcing | SFT + forced `</think>` / "Wait" | controllable | extends scaling (AIME24 50→57) | Qwen2.5-32B [abstract](https://arxiv.org/abs/2501.19393v2) |
| NoThinking (prefilled empty think) | prompt | matched-budget | beats Thinking at low budgets (AMC23 51.3 vs 28.9 @700 tok); **LiveCodeBench is the exception — Thinking wins even at low budget** | R1-Distill-Qwen [abstract](https://arxiv.org/abs/2504.09858) |

**Caveman skill** (viral 2026): drops articles/filler from *visible output*, keeps code/paths/negations verbatim; claims 65% output-token cut ([repo](https://github.com/juliusbrussee/caveman)) [anecdotal]. Independent JetBrains A/B (Claude Code, 86 SkillsBench tasks, paired): **8.5% output-token saving, no detectable quality change (p=0.82)**; savings small because agent output is mostly code/tool calls ([JetBrains](https://blog.jetbrains.com/ai/2026/07/speak-to-ai-agents-like-cavemen-tosave-tokens/)) [anecdotal but well-controlled]. Neither measures thinking tokens. Elastic reports −64% on 8 MCP scenarios, self-reported ([Elastic](https://www.elastic.co/search-labs/blog/elastic-caveman-ai-token-reduction)) [anecdotal]. CAVEWOMAN (Adobe): output compression cuts cost 1.4-2.4x; **compressing the input prompt raises cost (~1.15x) and accuracy "collapses"** ([abstract](https://arxiv.org/abs/2606.24083)).

Takeaway: nearly all accuracy-neutral results are on math/QA with large or trained models. No paper found that tests CoD-style prompting on agentic coding thinking traces.

## 2. Small and quantized models

- Small models lose more under prompted compression. CoD on GSM8K: Qwen2.5-3B 59.1→43.1, Llama3.2-3B 70.7→52.5; authors say CoD needs few-shot and is weak below 3B ([paper](https://arxiv.org/html/2502.18600v2)). TokenSkip degradation shrinks with scale (3B −9.3 vs 14B −1.7 at the same ratio) ([table](https://arxiv.org/abs/2502.12067)).
- Reasoning models largely ignore instructions about their thinking: best open model <25% compliance on ReasonIF (format/length/language constraints in the trace), worse on harder tasks; Qwen3 included ([abstract](https://arxiv.org/abs/2510.15211v1)). TALE also found "token elasticity": too-tight a prompted budget (10 tok) *increased* output vs a reasonable one (157 vs 86) ([paper](https://arxiv.org/html/2412.18547v5)). Expect a "think tersely" system prompt to have weak, noisy effect on Qwen-family thinking.
- Quantization: W4A16 near-lossless, lower bits risky, harder tasks degrade more ([abstract](https://arxiv.org/abs/2504.04823)). Meta 2026: aggressive PTQ *lengthens* CoT; 52% of 3-bit failures were "overthinking" (right answer found mid-trace, then abandoned); a logit penalty on ~50 hesitation words ("wait", "alternatively") cut CoT 12-23% and raised MATH-500 47.0→61.2 on a 3-bit 1.5B ([abstract](https://www.alphaxiv.org/abs/2606.00206)). Directly relevant to a 1.58-bit 27B: cutting dithering may *help*.
- Qwen3 thinking vs non-thinking (official): LiveCodeBench v5 30B-A3B 62.6 vs 29.8; 8B 57.5 vs 22.8; BFCL v3 (tool calling) 30B-A3B 69.1 vs 58.6; but in-house ToolUse 32B 85.5 think vs 86.5 no-think ([report](https://arxiv.org/html/2505.09388v1)). So no-think is roughly free for simple tool calls, costly for code synthesis.
- Budgets on Qwen3/3.5 (Coupling Tax, preprint): with a *shared output cap* and no forced answer, thinking loses badly at low budgets (Qwen3-8B GSM8K think@512 56.9% vs nothink 93.1%); crossover ~1-2K tokens on BBH, ~4K on MATH-500; forced-answer/split-budget recovers most loss; chains that finish naturally are ~99% accurate ([preprint](https://arxiv.org/html/2605.07686)). Qwen3.5 eval reports 95-97% of 4B/9B traces never closed `</think>` at 2048 ([issue](https://github.com/allenai/olmo-eval/issues/399)) [anecdotal].
- Agentic: on SWE-bench Verified, higher "overthinking" scores correlate with lower resolve rate; picking low-overthinking trajectories gave ~+30% with −43% cost ([abstract](https://arxiv.org/abs/2502.08235)).

## 3. Prompt-side compression

- Tokenizer reality: English ≈ 0.75 words/token ([OpenAI](https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them)); common words like " the", " a" are single tokens, so stripping articles saves maybe 5-10% of prose, and the system prompt is prefix-cached in llama.cpp anyway — prompt-side savings are small on a local box.
- Risk is real: CAVEWOMAN found compressed inputs make models write *more* and score worse ([abstract](https://arxiv.org/abs/2606.24083)); Telegraph-English rewrites lose fine detail (dates, units, conditionals) with larger gaps on smaller models ([abstract](https://arxiv.org/pdf/2605.04426)). Caveman deliberately never rewrites user prompts ([repo](https://github.com/juliusbrussee/caveman)). Recommendation: don't telegraph the system prompt for the 9B; trim redundancy instead.

## 4. What is practical on llama.cpp with no training

- Training methods (TokenSkip, C3oT, L1, ThinkPrune, AdaptThink) all report the best trade-offs but require SFT/RL — out of scope.
- Practical levers: (a) `enable_thinking` per step (NoThinking); (b) `--reasoning-budget` + budget message (s1/Qwen3-style forced exit — Qwen3 itself uses "Considering the limited time by the user, I have to give the solution based on the thinking directly now." ([report](https://arxiv.org/html/2505.09388v1))); per-request `reasoning_budget_tokens` honored since b9982 ([release](https://newreleases.io/project/github/ggml-org/llama.cpp/release/b9982)); note a reported Windows bug where the message stopped appearing and the model overran ([#22717](https://github.com/ggml-org/llama.cpp/issues/22717)) [anecdotal]; (c) logit bias against hesitation tokens (Meta result above) — llama.cpp `logit_bias` can do this; (d) DEER-style probe (needs custom loop; harder).
- Forced-exit > hard truncation: the Coupling Tax data show truncation without an answer pass is catastrophic, forced answer recovers most.

## 5. Recommendation for this harness

A/B first, cheapest-to-strongest evidence:
1. **Routed thinking (highest expected gain).** No-think on routine steps (file reads, edits from a clear plan, tool-call formatting, summaries); think on planning, debugging after a failed test, and first code synthesis. Evidence: Qwen3 ToolUse ~equal without thinking, but code synthesis drops ~30 pts without it.
2. **Budget sweep on think steps:** 1K / 2K / 4K / unlimited with the budget message. Expect 2-4K to keep most accuracy (Coupling Tax crossover) and cut the 4-10 minute steps to ~2-4 min at 13-20 tok/s.
3. **Hesitation logit penalty** (small negative bias on " Wait", " Alternatively", " Hmm") — targets the exact quantization pathology; test mild values only.
4. **CoD-style thinking instruction** last — weakest evidence for thinking models (ReasonIF), may be ignored or backfire.

Metrics: task success/test pass rate (paired, same tasks, ≥3 seeds — JetBrains showed k=1 is misleading), thinking tokens per step, wall-clock per task, budget-hit rate, `</think>` natural-close rate, steps-to-completion (shorter thinking can mean more steps), and loop/repeat-tool-call count.

Expected: routing + 2-4K budgets plausibly −40 to −70% thinking time; accuracy risk concentrated in debugging and multi-file code synthesis. Evidence for agentic coding on ≤27B, sub-4-bit models is essentially absent — treat all numbers as priors to be measured, and keep changes generic (not tuned to the test task).
