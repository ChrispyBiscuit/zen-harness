"""
Design tokens + QSS loader for the Local Web Search desktop app.

Per the redesign spec: QSS has no variables, so tokens live here once and get
substituted into app.qss at startup. Use string.Template ($token) — NOT str.format —
because QSS is full of literal { } braces. Never hardcode a hex anywhere else.
"""
from string import Template
from pathlib import Path

# ---- design tokens (single source of truth) -------------------------------
T = dict(
    # surfaces: one step per depth, near-neutral cool greys
    bg_canvas="#0C0E12", bg_surface="#101318", bg_elev="#13161B",
    bg_raised="#1A1E25", border="#232833", border_hi="#2A303B",
    # text
    text_hi="#E6E8EC", text_body="#DDE1E7", text_mid="#98A1AE", text_dim="#5A6472",
    # accents
    accent="#3FB8A4", accent_hi="#4ECBB6",
    accent_08="rgba(63,184,164,0.08)", accent_12="rgba(63,184,164,0.12)",
    accent_18="rgba(63,184,164,0.18)", accent_30="rgba(63,184,164,0.30)",
    reason="#9B95E0", reason_bg="rgba(155,149,224,0.10)",
    warn="#E0A85F", danger="#E0665F",
    # type
    font_ui='"Segoe UI Variable Text","Segoe UI","Noto Sans",sans-serif',
    font_mono='"JetBrains Mono","Cascadia Mono","Consolas",monospace',
    # radii
    r_ctl="6px", r_ctl_lg="7px", r_card="10px", r_field="12px",
    # asset dir (forward slashes for QSS url())
    icon_dir=(Path(__file__).parent / "assets").as_posix(),
)

# transcript HTML colours (QTextBrowser can't read QSS) — derived from the same tokens
HTML = dict(
    user_fill="rgba(63,184,164,0.14)", user_border="rgba(63,184,164,0.30)",
    user_text="#E6E8EC", bot_text="#DDE1E7", link="#3FB8A4",
    reason_text="#9B95E0", reason_bg="#15151F", reason_rail="#9B95E0",
    dim="#5A6472",
)


def stylesheet() -> str:
    src = Path(__file__).with_name("app.qss").read_text(encoding="utf-8")
    return Template(src).substitute(T)
