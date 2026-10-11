"""Agent-mode defaults (shared by the web backend and the legacy Qt window)."""
import os

DEFAULT_AGENT = {
    "enabled": False, "backend": "llama", "model": "", "num_ctx": 131072, "kv_cache": "auto",
    "max_steps": 40, "think": False, "vision": True,
    # sampling: Qwen's "precise / coding" preset (thinking mode), the best fit for tool use. Greedy (0) loops.
    "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0,
    "think_level": "standard",        # (older setting; Effort below replaces it)
    # "effort" (set from the message box): the cap — minimal (no thinking) / low / medium / high / max. The agent
    # picks its own effort per request under it. Unset: derived from think_level (off→minimal, standard→medium).
    "require_checklist": True,        # every request starts with a milestone/subtask checklist
    "plan_tokens": 0,                 # tokens spent writing an approach before acting (0 = skip; the checklist
                                      # still gets made — PROGRAM P2: 30% faster, same pass rate)
    "permissions": "ask",             # composer's permission tier: ask (default) / edits / auto / bypass
                                      # (read only = plan_mode). Damaging commands stay blocked in every tier.
    "web": True,                      # composer's Web switch: off = no web tools, no web lookups (hard off)
    "verify": True,                   # composer's Verify switch: background research + answer checks (off = fastest)
    "interpret_first": True,          # first step of every request: a short structured reading of it (goal, quoted
                                      # constraints, conditions, ambiguities, irreversibility), shown as a card
    "think_frame": False,            # before working, pick 2-4 entries from thinking_library.json and adapt them
                                      # into a reasoning frame for the request (thinking.py; one short extra call)
    "checkin_every": 99,             # tool calls without a checklist update before a check-in is forced
    "playbook_notes": False,          # no "write a playbook note" step before the final answer (P2)
    "tracker_with_work": True,        # checklist updates ride with the next real call, never a step alone (P2)
    "verify_before_commit": True,     # wrap-up: facts from the text read, rankings ordered by source figures (P3)
    "lean_reference": True,           # lean pass: offline Wikipedia (+ web when time-sensitive) as Reference
    "facts_need_source": True,        # lean pass: specific facts only from the Reference / encyclopedia
    "auto_verify": True,              # fact check looks up unsupported claims (Wikipedia + web) itself
    "roots": [os.path.expanduser("~\\Documents"), os.path.expanduser("~\\Desktop")],
    "auto_read": True, "auto_net": True, "auto_safe_cmds": True, "allow_rules": [],
    "screen_control": False, "plan_mode": False, "delegation": True, "max_delegates": 3,
    "auto_continue": True, "advisor": {"enabled": False, "model": "gpt-6.1-sol"},
    "max_failures": 6, "mcp_servers": [],
    # hybrid: a small fast model makes the routine tool calls; the main model plans, checks in, recovers
    # from failures and writes the final answer. Both run on the GPU at once.
    "hybrid": {"enabled": False, "model": "", "num_ctx": 32768},
    # fan-out: the main model hands independent research briefs to small CPU workers (gather tool).
    # 4 workers; 16 threads was fastest on an idle CPU, but with the main model, browser and tools running too it
    # collapsed to 2.7 tok/s per worker (lockstep threads stall when preempted). Benchmark under background load: high
    # process priority fixes it (12 threads: 0.6 -> 6.7 tok/s per worker); 12 leaves 4 hardware threads for the rest.
    "fanout": {"enabled": False, "model": "", "workers": 2, "threads": 8, "priority": "high", "num_ctx": 16384, "max_steps": 4},
    # tools load on demand through available_tools (small prompts for local models); off = every tool always offered
    "tool_gate": True,
    "file_index": True,               # index the allowed folders for search_documents (CPU only, while idle)
    "playbooks": True,                # match each new request to a task playbook (playbooks/*.md) and inject it
    "lean_first": True,               # answer from a short prompt first; the get_tools escape loads the full harness
    "basic_calls": 6,                 # tool-call budget of the basic path (short tasks: no plan, checklist or workers)
    "spec_ngram": "",                 # n-gram speculative decoding for the main llama.cpp model ("" = off, e.g. "ngram-mod")
    "custom_instructions": "",        # the user's own system-prompt text (Settings → Instructions), added to every agent chat
}
