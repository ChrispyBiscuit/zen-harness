"""Settings > Model explanations: the system prompt asks for a one-line 'what and why' beside each tool call, or not."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offline_suite import engine
import agent_config


def test_explanations_on_by_default_and_switchable():
    assert agent_config.DEFAULT_AGENT["model_explanations"] is True
    assert "Keep the user informed as you work" in engine().system_prompt()
    assert "Keep the user informed as you work" not in engine(model_explanations=False).system_prompt()


def test_non_build_chat_does_not_crash_on_code_scoping():
    # 00:26 regression: the focus_pack scoping read _build_profile before any build request had set it
    e = engine()
    assert not getattr(e, "_build_profile", False)
    assert getattr(e, "_build_profile", False) or not e._CODE_REQ_RX.search("best ice cream in detroit")
