"""Model-facing prompts say what to do, in plain case: calm, specific, positively framed instructions work better
than shouting or bare prohibitions (negated prompts are handled poorly; refusals with no way forward made a 27B
stop trying). Hard limits (payments, destructive commands) keep their 'never'."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agentic

ALLOWED = {"JSON", "HTTP", "API", "URL", "PDF", "CSV", "PRD", "SQL", "HTML", "GPU", "CPU", "RAM", "MCP", "README",
           "GUI", "USD", "CLI", "CAPTCHA", "PC", "AGENTS", "CLAUDE", "NOTES", "PROGRESS", "LaTeX", "TODO", "ID", "OK", "EXECUTOR",
           "SUBAGENT", "REALITY", "CHECK", "PROJECT", "MODE", "DATA", "IMPORTANT"}


def shouted(text):
    return [w for w in re.findall(r"\b[A-Z]{3,}\b", text) if w not in ALLOWED]


def test_system_prompt_has_no_shouting():
    for name in ("SYSTEM", "BASIC_NOTE", "PLAN_MODE_NOTE", "WEB_OFF_NOTE", "EXPLAIN_NOTE"):
        text = getattr(agentic, name, "")
        assert not shouted(text), (name, shouted(text)[:10])


def test_prompt_rules_say_what_to_do():
    s = agentic.SYSTEM
    for old in ("You are NOT a chat-only model", "never regenerate their spelling", "Never say you lack access",
                "do not repeat it", "Never open GUI programs"):
        assert old not in s
    assert "never buy anything" in s.lower() or "never enter payment" in s.lower()     # hard limits stay
