"""Themes shared by every document format (docx, pptx, xlsx, pdf), plus the small helpers they all use:
colour mixing, the Office theme part (so Word/PowerPoint/Excel's own colour and font pickers match), a
Markdown block parser (docx and pdf read the same Markdown) and chart images (matplotlib) for docx/pdf.

A theme is a plain dict. Slides use bg/surface/title/text (dark themes have dark slides); paper formats
(docx, pdf, xlsx) always print light and use paper(t), which swaps in ink colours for dark themes.
"""
import json
import re
import threading

THEMES = {
    "corporate": {"label": "navy and blue, light", "dark": False,
                  "bg": "FFFFFF", "surface": "F3F6FA", "band": "1F3864", "on_band": "FFFFFF", "title": "1F3864",
                  "text": "262626", "accent": "2E75B6", "accent2": "F4A259", "muted": "6B7280", "line": "D6DEE8",
                  "palette": ["2E75B6", "1F3864", "F4A259", "5BA67A", "8E7CC3", "C9D4E3"],
                  "fonts": ("Segoe UI Semibold", "Segoe UI")},
    "modern": {"label": "charcoal and teal, dark slides", "dark": True,
               "bg": "1E2329", "surface": "272D35", "band": "13171C", "on_band": "FFFFFF", "title": "FFFFFF",
               "text": "D9DEE4", "accent": "2EC4B6", "accent2": "FFB86B", "muted": "8A939E", "line": "3A424D",
               "palette": ["2EC4B6", "FFB86B", "7AA2F7", "FF6B6B", "C3E88D", "BB9AF7"],
               "fonts": ("Segoe UI Semibold", "Segoe UI")},
    "minimal": {"label": "black on white, one red accent", "dark": False,
                "bg": "FFFFFF", "surface": "F5F5F5", "band": "111111", "on_band": "FFFFFF", "title": "111111",
                "text": "333333", "accent": "E63946", "accent2": "111111", "muted": "8C8C8C", "line": "E0E0E0",
                "palette": ["111111", "E63946", "8C8C8C", "BDBDBD", "4A4A4A", "F1A7AE"],
                "fonts": ("Segoe UI Semibold", "Segoe UI")},
    "warm": {"label": "cream and coral, serif headings", "dark": False,
             "bg": "FBF6EF", "surface": "F3EADF", "band": "3D2C2E", "on_band": "FBF6EF", "title": "3D2C2E",
             "text": "4A3F3F", "accent": "E76F51", "accent2": "2A9D8F", "muted": "9C8A84", "line": "E8DCCD",
             "palette": ["E76F51", "2A9D8F", "F4A261", "264653", "E9C46A", "9C6644"],
             "fonts": ("Georgia", "Segoe UI")},
    "forest": {"label": "deep green and sand", "dark": False,
               "bg": "F6F8F4", "surface": "E9EFE6", "band": "1B4332", "on_band": "F6F8F4", "title": "1B4332",
               "text": "2D3A32", "accent": "40916C", "accent2": "D4A373", "muted": "7A8A80", "line": "D5DED2",
               "palette": ["40916C", "D4A373", "1B4332", "95D5B2", "BC6C25", "74A57F"],
               "fonts": ("Georgia", "Segoe UI")},
    "slate": {"label": "slate grey and indigo, crisp", "dark": False,
              "bg": "FFFFFF", "surface": "F1F5F9", "band": "1E293B", "on_band": "FFFFFF", "title": "0F172A",
              "text": "334155", "accent": "4F46E5", "accent2": "0EA5E9", "muted": "64748B", "line": "E2E8F0",
              "palette": ["4F46E5", "0EA5E9", "F59E0B", "10B981", "EC4899", "94A3B8"],
              "fonts": ("Segoe UI Semibold", "Segoe UI")},
}
DEFAULT = "corporate"


def theme(name):
    return THEMES.get(str(name or DEFAULT).lower().strip(), THEMES[DEFAULT])


def theme_name(name):
    n = str(name or DEFAULT).lower().strip()
    return n if n in THEMES else DEFAULT


def paper(t):
    """The theme as printed on white paper: dark themes keep their accents but get dark ink."""
    if not t["dark"]:
        return dict(t, bg="FFFFFF")
    return dict(t, bg="FFFFFF", surface="F2F4F6", title=t["band"], text="262626", muted="6B7280", line="DDE1E6",
                palette=["1A9E92", "E8963A", "4A6FD1", "D9534F", "6E9E3A", "8E6CC9"], accent="1A9E92")


def mix(hexs, other="FFFFFF", amount=0.1):
    a = [int(hexs[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(other[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(a, b))


def tint(t, amount=0.88):
    return mix(t["accent"], "FFFFFF", amount)


def theme_list():
    return ", ".join(f"{k} ({v['label']})" for k, v in THEMES.items())


# ------------------------------------------------------------------ Office theme part (theme1.xml)
def office_theme_xml(xml, t):
    """Rewrite an Office theme part's colour scheme and fonts to match the theme."""
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8")
    p = paper(t)
    colours = {"dk2": p["band"], "lt2": p["surface"], "hlink": p["accent"], "folHlink": p["muted"]}
    for i, c in enumerate(p["palette"], 1):
        colours[f"accent{i}"] = c
    for tag, c in colours.items():
        xml = re.sub(rf"(<a:{tag}>)(.*?)(</a:{tag}>)", rf'\g<1><a:srgbClr val="{c}"/>\g<3>', xml, count=1, flags=re.S)
    hfont, bfont = t["fonts"]
    for tag, f in (("majorFont", hfont.replace(" Semibold", "")), ("minorFont", bfont)):
        xml = re.sub(rf'(<a:{tag}>\s*<a:latin typeface=")[^"]*', rf"\g<1>{f}", xml, count=1, flags=re.S)
    xml = re.sub(r'(<a:clrScheme name=")[^"]*', r"\g<1>Zazen", xml, count=1)
    return xml.encode("utf-8")


def apply_office_theme(package, t):
    """docx / pptx: find every theme part in an open python-docx or python-pptx package and restyle it."""
    for part in package.iter_parts():
        if str(part.partname).startswith(("/word/theme/", "/ppt/theme/")) and hasattr(part, "_blob"):
            part._blob = office_theme_xml(part._blob, t)


# ------------------------------------------------------------------ Markdown blocks (docx + pdf)
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_NUMERIC = re.compile(r"^[-+(]?[$€£¥]?\s?-?[\d.,]+\s?(%|[kKmMbB]n?|x)?\)?$")


def is_number(s):
    s = str(s).strip().replace("−", "-")
    return bool(s) and bool(_NUMERIC.match(s)) and any(ch.isdigit() for ch in s)


def to_float(s):
    s = str(s).strip().replace("−", "-").replace(",", "").replace("$", "").replace("€", "").replace("£", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    mult = 1.0
    m = re.match(r"^(-?[\d.]+)\s*([kKmMbB]n?|%|x)?$", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    mult = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}.get(suffix, 1.0)
    return -v * mult if neg else v * mult


def is_total_row(row):
    return bool(row) and bool(re.match(r"^\**\s*(grand\s+)?(total|sum|overall|net)\b", str(row[0]).strip(), re.I))


def table_align(rows, seps=None):
    """Per column: 'right' for numeric columns, else 'left' (explicit :--: / --: markers win)."""
    ncol = max(len(r) for r in rows)
    out = []
    for ci in range(ncol):
        if seps and ci < len(seps):
            s = seps[ci].strip()
            if s.startswith(":") and s.endswith(":"):
                out.append("center")
                continue
            if s.endswith(":"):
                out.append("right")
                continue
        vals = [r[ci] for r in rows[1:] if ci < len(r) and str(r[ci]).strip() not in ("", "-", "—", "n/a")]
        vals = [re.sub(r"[*_`]", "", str(v)) for v in vals]
        out.append("right" if vals and all(is_number(v) for v in vals) else "left")
    return out


def parse_blocks(markdown):
    """Markdown -> [{'kind': ...}] for the paper formats.
    kinds: heading(level,text) para(text) bullets(items=[(level,text)]) numbered(items) table(rows,align)
           quote(text) code(text) chart(spec) image(path,alt) pagebreak"""
    lines = str(markdown or "").replace("\r\n", "\n").split("\n")
    blocks, i = [], 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            lang = s[3:].strip().lower()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            text = "\n".join(body)
            if lang == "chart":
                blocks.append({"kind": "chart", "spec": parse_chart_spec(text)})
            else:
                blocks.append({"kind": "code", "text": text})
        elif re.match(r"^#{1,6}\s", s):
            level = len(s) - len(s.lstrip("#"))
            blocks.append({"kind": "heading", "level": min(level, 4), "text": s[level:].strip().strip("#").strip()})
        elif s.startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
            rows, seps = [], None
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if _TABLE_SEP.match(lines[i].strip()):
                    seps = cells
                else:
                    rows.append(cells)
                i += 1
            i -= 1
            ncol = max(len(r) for r in rows)
            rows = [r + [""] * (ncol - len(r)) for r in rows]
            blocks.append({"kind": "table", "rows": rows, "align": table_align(rows, seps)})
        elif re.match(r"^!\[[^\]]*\]\([^)]+\)$", s):
            m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", s)
            blocks.append({"kind": "image", "alt": m.group(1), "path": m.group(2).strip().strip("<>")})
        elif re.match(r"^[-*+]\s", s):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s", lines[i]):
                raw = lines[i]
                items.append((min(2, (len(raw) - len(raw.lstrip())) // 2), raw.strip()[2:].strip()))
                i += 1
            i -= 1
            blocks.append({"kind": "bullets", "items": items})
        elif re.match(r"^\d+[.)]\s", s):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+[.)]\s", lines[i]):
                items.append((0, re.sub(r"^\d+[.)]\s", "", lines[i].strip())))
                i += 1
            i -= 1
            blocks.append({"kind": "numbered", "items": items})
        elif s.startswith(">"):
            body = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                body.append(lines[i].strip().lstrip(">").strip())
                i += 1
            i -= 1
            blocks.append({"kind": "quote", "text": " ".join(b for b in body if b)})
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            blocks.append({"kind": "pagebreak"})
        elif s:
            para = [s]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(
                    r"^(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>|```|!\[|(-{3,}|\*{3,})$)", lines[i + 1].strip()):
                i += 1
                para.append(lines[i].strip())
            blocks.append({"kind": "para", "text": " ".join(para)})
        i += 1
    # a chart block with no data of its own charts the table just before it
    last_table = None
    for b in blocks:
        if b["kind"] == "table":
            last_table = b
        elif b["kind"] == "chart" and not b["spec"].get("labels") and last_table:
            b["spec"] = chart_from_table(b["spec"], last_table["rows"])
    return blocks


def parse_chart_spec(text):
    """```chart blocks: JSON, or simple 'key: value' lines (type: column / title: Revenue)."""
    text = text.strip()
    try:
        spec = json.loads(text) if text else {}
        if isinstance(spec, dict):
            return normalize_chart(spec)
    except ValueError:
        pass
    spec = {}
    for ln in text.split("\n"):
        if ":" in ln:
            k, v = ln.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k in ("labels", "values", "columns") or v.startswith("["):
                try:
                    v = json.loads(v)
                except ValueError:
                    v = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
            spec[k] = v
    return normalize_chart(spec)


def normalize_chart(spec):
    spec = dict(spec)
    spec["type"] = str(spec.get("type") or "column").lower()
    if "values" in spec and "series" not in spec:
        spec["series"] = [{"name": spec.get("name") or spec.get("title") or "", "values": spec.pop("values")}]
    series = []
    for s in spec.get("series") or []:
        if isinstance(s, dict):
            series.append({"name": str(s.get("name") or ""), "values": [to_float(v) if isinstance(v, str) else v
                                                                         for v in s.get("values") or []]})
    spec["series"] = series
    spec["labels"] = [str(x) for x in spec.get("labels") or []]
    return spec


def chart_from_table(spec, rows):
    """First column = labels, numeric columns = series (or the ones named in spec['columns']). Total rows skipped."""
    header, body = rows[0], [r for r in rows[1:] if not is_total_row(r)]
    want = [str(c).lower() for c in spec.get("columns") or []]
    series = []
    for ci in range(1, len(header)):
        vals = [re.sub(r"[*_`]", "", r[ci]) for r in body]
        if want and header[ci].lower() not in want:
            continue
        if vals and all(is_number(v) for v in vals):
            series.append({"name": header[ci], "values": [to_float(v) for v in vals]})
    if not want and spec["type"] == "pie":
        series = series[:1]
    return dict(spec, labels=[re.sub(r"[*_`]", "", r[0]) for r in body], series=series)


# ------------------------------------------------------------------ chart images (docx / pdf)
_CHART_LOCK = threading.Lock()


def chart_image(spec, t, fmt="png", width=6.5, height=3.2):
    """Render a chart spec with matplotlib in the theme's palette. Returns bytes (png) or str (svg).
    pyplot and its rcParams are process-wide, so one chart is drawn at a time."""
    with _CHART_LOCK:
        return _chart_image(spec, t, fmt, width, height)


def _chart_image(spec, t, fmt, width, height):
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p = paper(t)
    labels, series, kind = spec.get("labels") or [], spec.get("series") or [], spec.get("type", "column")
    if not labels or not series:
        raise ValueError("chart needs labels and at least one numeric series (or a table just above it)")
    colors = ["#" + c for c in p["palette"]]
    font = p["fonts"][1]
    plt.rcParams.update({"font.family": [font, "Segoe UI", "DejaVu Sans"], "font.size": 9,
                         "axes.edgecolor": "#" + p["line"], "axes.labelcolor": "#" + p["muted"],
                         "xtick.color": "#" + p["muted"], "ytick.color": "#" + p["muted"], "svg.fonttype": "none"})
    if kind == "bar":
        height = min(height, 0.9 + 0.42 * len(labels) * max(1, len(series) * 0.7))
    elif kind in ("pie", "donut"):
        height = max(height, 3.0)
    fig, ax = plt.subplots(figsize=(width, height), dpi=200)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    n = len(series)
    if kind == "pie" or kind == "donut":
        vals = [v or 0 for v in series[0]["values"]]
        wedges, _t, autotexts = ax.pie(vals, labels=labels, colors=colors * 3, startangle=90, counterclock=False,
                                       autopct="%1.0f%%", pctdistance=0.78 if kind == "donut" else 0.62,
                                       wedgeprops={"width": 0.42 if kind == "donut" else 1, "edgecolor": "white",
                                                   "linewidth": 1.5},
                                       textprops={"color": "#" + p["text"]})
        for a in autotexts:
            a.set_color("white" if kind != "donut" else "#" + p["text"])
            a.set_fontsize(8)
        ax.axis("equal")
    else:
        import numpy as np
        x = np.arange(len(labels))
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.tick_params(length=0)
        if kind in ("line", "area"):
            for k, s in enumerate(series):
                vals = [np.nan if v is None else v for v in s["values"]]
                ax.plot(x, vals, color=colors[k % 6], linewidth=2.2, marker="o", markersize=4, label=s["name"])
                if kind == "area":
                    ax.fill_between(x, vals, color=colors[k % 6], alpha=0.12)
            ax.set_xticks(x, labels)
            ax.yaxis.grid(True, color="#" + p["line"], linewidth=0.8)
        else:
            horizontal = kind == "bar"
            w = 0.8 / n
            for k, s in enumerate(series):
                vals = [0 if v is None else v for v in s["values"]]
                pos = x - 0.4 + w / 2 + k * w
                bars = (ax.barh if horizontal else ax.bar)(pos, vals, w * 0.92, color=colors[k % 6], label=s["name"])
                if n == 1 and len(labels) <= 12:
                    ax.bar_label(bars, labels=[_short(v) for v in vals], padding=3, fontsize=8, color="#" + p["text"])
            if horizontal:
                ax.set_yticks(x, labels)
                ax.invert_yaxis()
                ax.xaxis.grid(True, color="#" + p["line"], linewidth=0.8)
                ax.spines["bottom"].set_visible(False)
            else:
                ax.set_xticks(x, labels)
                ax.yaxis.grid(True, color="#" + p["line"], linewidth=0.8)
        ax.set_axisbelow(True)
        if len(labels) > 6 and kind != "bar":
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        if spec.get("y_title"):
            ax.set_ylabel(spec["y_title"])
        if spec.get("x_title"):
            ax.set_xlabel(spec["x_title"])
        if n > 1:
            ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0, 1.12), ncol=min(n, 4), fontsize=8)
    if spec.get("title"):
        fig.suptitle(spec["title"], x=0.01, ha="left", fontsize=11, color="#" + p["title"], fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, transparent=True)
    plt.close(fig)
    return buf.getvalue() if fmt == "png" else buf.getvalue().decode("utf-8")


def _short(v):
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}k"
    return f"{v:g}"
