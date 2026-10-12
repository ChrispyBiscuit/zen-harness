# Agent context compression: state of the art vs. our harness (researched 2026-10-11)

Scope: long, multi-step tool-use runs, mostly coding agents. Every claim below has a source. Many 2026 arXiv items were read only at abstract level, and that is marked where it applies.

## (a) Taxonomy and the best evidence for each

1. **Observation masking.** Old tool outputs are replaced with a placeholder, and reasoning and actions are kept. *The Complexity Trap* (Lindenbauer et al., JetBrains, NeurIPS 2025, arXiv 2508.21433) tested SWE-agent on SWE-bench Verified with five model configs. Masking everything older than M=10 turns roughly halved cost and matched or beat LLM summarization in 4 of 5 configs. Example: Qwen3-Coder-480B went from 53.4% raw to 54.8% with masking and 53.8% with summary. The exception was Qwen3-32B, at 17.0% raw, 15.0% masked and 16.0% summarized; none of those differences are significant. https://arxiv.org/abs/2508.21433 , https://arxiv.org/html/2508.21433v3
   - Follow-up from 2026: masking gains form an "inverted U". They peak for mid-capacity models and collapse for saturated ones. The trade is fewer tokens for more turns, and it hurts when masking removes evidence the model would have used. This was tested on search agents, 4B–284B. https://arxiv.org/abs/2606.00408
2. **LLM summarization / compaction.** Examples are the OpenHands LLMSummarizingCondenser (54% vs 53% on a SWE-bench Verified subset, up to 2x lower per-turn cost; https://openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents), Claude server-side compaction (https://platform.claude.com/docs/en/build-with-claude/compaction), OpenAI `/responses/compact` (https://developers.openai.com/api/docs/guides/compaction) and ReSum (+4.5% over ReAct on web search; https://arxiv.org/abs/2509.13313v2).
   - Known side effect: summaries *lengthen trajectories*, by about 13–15% more turns, which eats the savings. Summary calls cost up to 7.2% and get almost no cache reuse. https://arxiv.org/html/2508.21433v3
3. **Hybrid.** Mask first and summarize rarely, in big chunks (N=43). This came out 7% cheaper than masking and 11% cheaper than summary-only. With small chunks (N=21) it was *less* efficient, because of KV-cache misses. https://arxiv.org/html/2508.21433v3
4. **Optimized or learned compressors.**
   - ACON optimizes the compression *guidelines* using paired runs where the full context succeeds and the compressed one fails. It cut peak tokens 26–54%. Its compressor distills into Qwen3-8B/14B and keeps more than 95% of performance. It raised a Qwen3-14B agent on AppWorld from 25.6% to 33.9%. In the same table, LLMLingua scored 39.3% against 56.0% with no compression. https://arxiv.org/abs/2510.00615
   - Task-conditioned *extractive* pruning of a single tool output:
     - Squeez: a 2B LoRA model reaches 0.86 recall and removes 92% of tokens. It reports pruning quality only, not task success. https://arxiv.org/abs/2604.04979
     - CoACT keeps compressions only if they preserve the next action, for 33% fewer tokens on SWE-bench Verified at near-parity. https://arxiv.org/abs/2607.02911
5. **Agent-managed memory, trained with RL or SFT.** These all need training, so we cannot use them directly:
   - MEM1: https://arxiv.org/abs/2506.15841
   - AgentFold (30B-A3B, context 92% smaller than ReAct at turn 100): https://arxiv.org/abs/2510.24699
   - CAT / SWE-Compressor (57.6% on SWE-bench Verified): https://arxiv.org/abs/2512.22087
   - SWE-MeM (4B 43.4%, 30B 60.2% on SWE-bench Verified; abstract only): https://arxiv.org/abs/2606.28434
6. **Token-level prompt compression (LLMLingua-2).** It was trained and evaluated on meeting transcripts, QA and reasoning, not code (https://arxiv.org/abs/2403.12968). The code-specific work says LLMLingua-style pruning "overlooks code-specific structures" (LongCodeZip; https://arxiv.org/abs/2510.00446). In agent settings it lost 17 points on AppWorld (ACON, above).
7. **External memory and recitation** (production practice):
   - Manus: file system as memory, *restorable* compression (keep the path or URL), todo.md recitation, keep errors in context. https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
   - Anthropic: just-in-time retrieval, NOTES.md, and sub-agents that return 1–2K-token summaries. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
   - Anthropic's memory tool together with context editing gave +39%, and context editing alone gave +29%. Both figures come from an internal agentic-search eval, so this is vendor evidence. https://claude.com/blog/context-management

On KV-cache cost:
- Manus calls cache hit rate "the single most important metric" for an agent. Its rules are an append-only context, a stable prefix and deterministic serialization (link above).
- Anthropic's context editing "invalidates cached prompt prefixes", so it recommends clearing in big batches (`clear_at_least`). Its defaults are trigger 100K and keep 3. https://platform.claude.com/docs/en/build-with-claude/context-editing
- SWE-agent's `LastNObservations` has a `polling` option for exactly this reason, and its docs warn that it otherwise "will break prompt caching". https://swe-agent.com/latest/reference/history_processor_config/

## (b) Where we already match SOTA

- **Masking before summarizing, and summarizing only on overflow** is the hybrid the evidence favours (Complexity Trap).
- **Batched recompute every 12 results** is the same mechanism as SWE-agent `polling` and Anthropic `clear_at_least`.
- **Stubs that keep a reference, plus an archive retrievable by id** is Manus's "restorable compression".
- **Replacing write/edit arguments with a size note** is Anthropic `clear_tool_inputs`.
- **Pinned verbatim items and a structured summary** cover the same items that Claude Code compaction preserves (decisions, bugs, files) and that OpenHands keeps (goals, files, failing tests).
- **A per-step work state and checklist** is Manus todo.md recitation plus Anthropic structured notes.
- **Dropping thinking from history** is the official Qwen3 practice: "historical model output should only include the final output part" (https://huggingface.co/Qwen/Qwen3-30B-A3B).
- **Rejecting LLMLingua** is supported by ACON's AppWorld numbers and LongCodeZip.

Our supersession-based masking is *more conservative* than the papers. They mask all observations older than a window, not only superseded ones.

## (c) Gaps and improvements, ranked by expected benefit (small model, 60K context, prefix cache)

1. **Bound each large observation when it arrives.** This matters most because 17K of the 60K goes to the prompt.
   - Observations make up about 84% of SWE-agent turn tokens (Complexity Trap).
   - ACON compresses any single observation over 1,024 tokens, and that is what lifted its small 14B agent.
   - Do it *extractively*: head and tail, plus error and failure lines, plus a stub with the archive id. Never rewrite with an LLM, so identifiers survive (Squeez returns verbatim blocks).
   - A learned pruner (Squeez-style, 2B) is a later option. It would cost VRAM or CPU time, and its effect on end-to-end task success is unproven.
2. **Add age-based masking of non-superseded bulky results outside the window, with exclusions.**
   - The papers mask regardless of supersession at M=10 with no loss (Complexity Trap).
   - Exclude errors and failed results, which Manus says to keep ("erasing failure removes evidence"), and the latest test output.
   - The window must account for retries: OpenHands needed M=58 instead of 10 because it keeps retry turns.
   - Make the window measurable, because the 2026 regime map shows masking can hurt.
3. **Check where the per-step work-state reminder sits.** This is the main cache risk.
   - If it is rebuilt every step anywhere *before* the history (for example inside the system prompt), it invalidates roughly 17K+ tokens of prefix every step. That would wipe out the benefit of the batched masking.
   - It belongs at the *tail*, after the last tool result, as Manus does with todo.md recitation at the end. "A single-token difference" breaks the cache from that point on.
   - The same applies to timestamps and to non-deterministic JSON ordering in tool schemas.
4. **Make summaries rarer and larger, with a structured template.**
   - Small or frequent summaries cost cache and lengthen trajectories (hybrid N=21 vs N=43 result).
   - Keep the summary trigger strictly at overflow.
   - Consider ACON-style *offline* tuning of the summary prompt from pairs where the full context passed and the compacted one failed. Our memory rule says not to tailor guides to the task being debugged, so this needs a broad trajectory set, not one task.
5. **Use llama.cpp `--cache-reuse` (KV shifting)** to recover part of the cache after a mid-prompt rewrite (masking batch or compaction). The default is 0, meaning off. https://github.com/ggml-org/llama.cpp/discussions/13606 . Measure quality after shifting; I found no evaluation of it.
6. **Vary the stub format a little.** Manus reports "few-shot ruts" from uniform, repetitive context. This is anecdotal and low priority.
7. **Sub-agents for noisy exploration**, returning 1–2K tokens (Anthropic). On a serial 8 GB GPU this costs wall-clock time, and Cognition warns that split context loses implicit decisions (https://cognition.ai/blog/multi-agents-working). This is a low priority.

## (d) Possibly counterproductive

- **The work-state reminder**, *if* it is in the prefix (see gap 3). Otherwise it is fine.
- **Re-summarizing an existing summary repeatedly.** This is untested here. OpenHands does it, but there is no direct evidence on drift for small models.
- **Retrieval-based restore as the main recall path.** Retrieval scored 27.4% on AppWorld against 56.0% with no compression (ACON). The `history_search` restore is fine as a backup, but the stubs must carry enough to act on without a lookup.
- Nothing else we do contradicts the evidence.

## Weak or conflicting evidence (be honest)

- Masking vs. summarization differences are mostly *not significant*. For our size class (Qwen3-32B), both hurt slightly. Most of the gain is cost, not success.
- Most results come from SWE-bench with frontier or 480B models. Small-model evidence is thin: ACON's Qwen3-14B, the regime map, and SWE-MeM (abstract only).
- Anthropic's 29% and 39% figures are vendor-internal. The details of Codex's encrypted compaction are not public.
