"""Playbook edits from the battery findings — applied only after the baseline run stops (playbooks are read at runtime,
so editing them earlier would change the baseline's behaviour mid-run)."""
import os

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "playbooks")


def patch(fn, pairs):
    p = os.path.join(BASE, fn)
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert s.count(a) == 1, (fn, a[:60])
        s = s.replace(a, b)
    open(p, "w", encoding="utf-8", newline="").write(s)
    print("patched", fn)


# finding 16: comparisons put the table in an artifact, then the fact check re-opened research → loops / time limit
patch("compare-options.md", [
    ("**Done looks like:** a short table of the options against the criteria that matter to the user, then a clear "
     "recommendation with the trade-off stated.",
     "**Done looks like:** a short comparison table IN THE ANSWER (the options against the criteria that matter to the "
     "user), then a clear recommendation with the trade-off stated. An artifact or document only when the user asks for "
     "one or there are 5+ options."),
    ("**Tools:** web_search; web_fetch(urls=[official pages], question=the criteria); artifact_create (markdown table) "
     "when there are 3+ options; calculate for cost comparisons.",
     "**Tools:** web_search (one call, the options as `queries`); web_fetch(urls=[official pages], question=the "
     "criteria) only for a figure the search didn't give; calculate for cost comparisons."),
    ("**Pitfalls:** \"best X\" listicles are affiliate marketing;",
     "**Pitfalls:** the table only in an artifact (the user reads the chat); re-opening research after the answer is "
     "written to polish cells; \"best X\" listicles are affiliate marketing;"),
])
# finding 22: a one-page document became a research project
patch("write-document.md", [
    ("**Pitfalls:** inventing facts to fill gaps (mark unknowns instead);",
     "**Pitfalls:** turning a short document (a page, a few slides) into a research project — no web research unless "
     "the user asks for sources or current figures; inventing facts to fill gaps (mark unknowns instead);"),
])
