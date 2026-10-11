"""Swarm board.

  left   one card per member: its own scrolling pane with its answer history, stance chip,
         public rating, alliance colour, a controversial badge and (optionally) its private
         thoughts. Strategic actions are NOT shown here.
  right  the public STRATEGIC MOVES panel: alliances, gifts, leaves, credits, bound votes,
         judge rulings, buffs and deaths, grouped by round.
  top    question + proposition, round, tally.     bottom   the consensus panel.
"""
import html
import re

from PySide6.QtCore import Qt, QTimer, QVariantAnimation, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient, QFont
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QTextBrowser,
    QScrollArea, QProgressBar, QGraphicsColorizeEffect, QSplitter, QCheckBox,
)

import theme
from swarm_ui import speaker_color

T = theme.T
CHIPS = {   # state -> (label, foreground, background)
    "wait":  ("waiting",      T["text_dim"], T["bg_raised"]),
    "think": ("thinking…",    T["accent"],   "rgba(63,184,164,0.12)"),
    "pos":   ("position set", T["text_mid"], T["bg_raised"]),
    "yes":   ("YES",          "#0C0E12",     T["accent"]),
    "no":    ("NO",           "#0C0E12",     T["danger"]),
    "cond":  ("⚠ CONDITIONAL", "#0C0E12",    T["warn"]),
    "write": ("writing…",     T["accent"],   "rgba(63,184,164,0.12)"),
    "dead":  ("☠ ELIMINATED", "#cfd3da",     "#3a3f4a"),
}
ALLY_COLORS = ["#4F9DDE", "#E0A85F", "#B779E0", "#E07A9E", "#6BCB77", "#E0665F",
               "#4FC9C9", "#C9C94F"]
MOVE_STYLE = {   # move type -> (icon, colour)
    "alliance": ("🤝", "#4F9DDE"), "left": ("💔", "#E0665F"), "gift": ("🎁", "#E07A9E"),
    "credit": ("★", "#3FB8A4"), "flip": ("↔", "#98A1AE"), "bound": ("⛓", "#B779E0"),
    "death": ("☠", "#E0665F"), "ruling": ("🔥", "#E0A85F"), "buff": ("⬆", "#E0A85F"),
    "refused": ("⛔", "#98A1AE"), "cleared": ("✔", "#3FB8A4"), "aligned": ("🏆", "#E0A85F"),
    "revived": ("✨", "#3FB8A4"), "resurrect": ("✨", "#3FB8A4"), "coalition": ("🏁", "#3FB8A4"), "heal": ("🩸", "#E0665F"), "kill": ("💀", "#E0665F"), "backstab": ("🗡", "#E0665F"), "debate": ("🗣", "#5FB8E0"), "vs": ("⚔", "#B779E0"), "temper": ("🎭", "#B58AE0"), "steal": ("🗡", "#E0665F"), "nuke": ("☢", "#E0665F"),
    "subvert": ("⚔", "#E0665F"),
    "enemy": ("🚫", "#E0665F"), "strike": ("⚡", "#E0A85F"), "friend": ("💢", "#E0A85F"),
    "retribution": ("🗳", "#E0665F"), "nukerisk": ("⚠", "#E0A85F"), "forced": ("🫳", "#E0A85F"),
}


def _rating_color(r):
    return T["accent"] if r >= 7 else (T["warn"] if r >= 4 else T["danger"])


def _rgba(hexcolor, a):
    c = QColor(hexcolor)
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


class NukeOverlay(QWidget):
    """A one-shot animation painted over a tile. kind='nuke': a flash, three expanding
    shockwave rings and big '☢ NUKE' text over the nuker. kind='hit': a short red flash over
    everyone else. It ignores the mouse and deletes itself when done."""

    def __init__(self, parent, kind="nuke", label="NUKE", accidental=False):
        super().__init__(parent)
        self.kind, self.label, self.accidental = kind, label, accidental
        self.t = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(parent.rect())
        self.raise_()
        self.show()
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(2000 if kind == "nuke" else 800)
        self.anim.valueChanged.connect(self._tick)
        self.anim.finished.connect(self.deleteLater)
        self.anim.start()

    def _tick(self, v):
        self.t = float(v)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        c = QPointF(w / 2, h / 2)
        t = self.t
        if self.kind == "hit":
            a = int(max(0.0, 1 - t) * 140)
            p.fillRect(self.rect(), QColor(224, 80, 70, a))
            p.setPen(QPen(QColor(255, 140, 120, int(max(0.0, 1 - t) * 255)), 3))
            p.drawRect(self.rect().adjusted(2, 2, -2, -2))
            return
        # flash
        fa = max(0.0, 1 - t * 2.0)
        r = max(w, h) * (0.35 + t * 0.9)
        g = QRadialGradient(c, r)
        g.setColorAt(0.0, QColor(255, 245, 190, int(235 * fa)))
        g.setColorAt(0.45, QColor(255, 130, 40, int(190 * fa)))
        g.setColorAt(1.0, QColor(255, 60, 20, 0))
        p.fillRect(self.rect(), g)
        # a dim red wash that lingers a little
        p.fillRect(self.rect(), QColor(200, 40, 30, int(max(0.0, 0.55 - t) * 150)))
        # three shockwave rings
        for i in range(3):
            rt = t * 1.25 - i * 0.2
            if 0 < rt < 1:
                rad = rt * max(w, h) * 0.8
                pen = QPen(QColor(255, 190 - i * 40, 60, int(230 * (1 - rt))), 1 + 5 * (1 - rt))
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, rad, rad)
        # the label: pops in, holds, fades
        ta = 255 if t < 0.65 else int(255 * max(0.0, 1 - (t - 0.65) / 0.35))
        scale = 0.6 + 0.5 * min(1.0, t / 0.25)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(14, int(h * 0.30 * scale)))
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, ta))
        p.drawText(QRectF(0, h * 0.12, w, h * 0.45), Qt.AlignmentFlag.AlignCenter, "☢")
        f.setPixelSize(max(12, int(h * 0.11 * scale)))
        p.setFont(f)
        p.setPen(QColor(255, 235, 170, ta))
        p.drawText(QRectF(0, h * 0.52, w, h * 0.2), Qt.AlignmentFlag.AlignCenter, self.label)


class _Stick:
    """Keeps a QTextBrowser scrolled to the BOTTOM as its content grows (new replies, live
    text). If you scroll up to read, it stays put; scroll back to the bottom to resume.
    "Scrolled by the user" is detected with actionTriggered, which Qt emits for the wheel,
    dragging and arrow clicks but never for our own setValue calls."""

    def __init__(self, browser):
        self.b = browser
        self.stick = True
        sb = browser.verticalScrollBar()
        sb.actionTriggered.connect(self._user)
        sb.rangeChanged.connect(self._range)

    def _user(self, _action):
        QTimer.singleShot(0, self._check)          # read the position after Qt applied it

    def _check(self):
        sb = self.b.verticalScrollBar()
        self.stick = sb.value() >= sb.maximum() - 6

    def _range(self, lo, hi):                      # layout finished / content grew
        if self.stick:
            self.b.verticalScrollBar().setValue(hi)

    def to_bottom(self):
        sb = self.b.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_html(self, doc):
        self.b.setHtml(doc)
        if self.stick:
            self.to_bottom()
            QTimer.singleShot(0, self._later)      # layout may finish after this call returns

    def _later(self):
        if self.stick:
            self.to_bottom()


class MemberCard(QFrame):
    """One member's own pane: name, model, badges, vote chip, and its scrolling answers."""

    def __init__(self, name, model, temp=None):
        super().__init__()
        self.name = name
        self.setObjectName("SwarmCard")
        self._ally_key = None
        self._style_card(None)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        name_lbl = QLabel(f"<b>{html.escape(name)}</b>")
        name_lbl.setStyleSheet(f"color:{speaker_color(name)};font-size:14px;")
        short = (model.split(":")[0] + ":" + model.split(":")[-1][:12]) if ":" in model else model
        t = f" · t={temp}" if temp is not None else ""
        model_lbl = QLabel(html.escape(short + t))
        model_lbl.setStyleSheet(f"color:{T['text_dim']};font-size:11px;")
        self.chip = QLabel()
        self.tps_lbl = QLabel()
        self.tps_lbl.setStyleSheet(f"color:{T['text_dim']};font-size:11px;")
        self.end_lbl = QLabel()
        self.end_lbl.setStyleSheet(f"color:{T['text_mid']};font-size:11px;")
        self.rating_lbl = QLabel()
        self.rating_lbl.setToolTip("Public performance rating.")
        self.risk_lbl = QLabel("☢ NUKE RISK")
        self.risk_lbl.setToolTip("Below the danger line with the nuke unused: each round it has "
                                 "a chance to go off by itself. Announced publicly.")
        self.risk_lbl.setStyleSheet(
            f"color:#0C0E12;background:{T['danger']};border-radius:9px;padding:2px 8px;"
            f"font-size:10px;font-weight:700;")
        self.risk_lbl.setVisible(False)
        self.team_lbl = QLabel()
        self.team_lbl.setVisible(False)
        self.temper_lbl = QLabel()
        self.temper_lbl.setVisible(False)
        self.friend_lbl = QLabel()
        self.friend_lbl.setStyleSheet(f"color:{T['text_dim']};font-size:10.5px;")
        self.friend_lbl.setToolTip("This member's friendship toward each other member (3/3 "
                                   "until struck or robbed by them; 0/3 = it must strike them).")
        self.friend_lbl.setVisible(False)
        self.charge_lbl = QLabel()
        self.charge_lbl.setToolTip("Charge (public): earned each round while above the charge "
                                   "line, spent to strike another member.")
        self.charge_lbl.setStyleSheet(f"color:{T['warn']};font-size:12px;font-weight:700;")
        self.ally_lbl = QLabel()
        self.badge = QLabel("🔥 CONTROVERSIAL")
        self.badge.setToolTip("The judge ruled this member's opinion controversial "
                              "(one-time rating buff; controversial opinions are risky).")
        self.badge.setStyleSheet(
            f"color:#0C0E12;background:{T['warn']};border-radius:9px;padding:2px 8px;"
            f"font-size:10px;font-weight:700;")
        self.badge.setVisible(False)
        self.assign_lbl = QLabel("🎲 assigned a bold stance")
        self.assign_lbl.setToolTip("The dice assigned this member the controversial role. "
                                   "Only you can see this; other members learn only from "
                                   "the judge's public ruling.")
        self.assign_lbl.setStyleSheet(f"color:{T['reason']};font-size:10.5px;")
        self.assign_lbl.setVisible(False)
        head.addWidget(name_lbl)
        head.addWidget(model_lbl)
        head.addWidget(self.ally_lbl)
        head.addStretch(1)
        head.addWidget(self.tps_lbl)
        head.addWidget(self.end_lbl)
        head.addWidget(self.charge_lbl)
        head.addWidget(self.rating_lbl)
        head.addWidget(self.chip)
        v.addLayout(head)
        self.ally_banner = QLabel()                 # "🤝 Allied with Bram" — shown when allied
        self.ally_banner.setWordWrap(True)
        self.ally_banner.setVisible(False)
        v.addWidget(self.ally_banner)
        badges = QHBoxLayout()
        badges.setContentsMargins(0, 0, 0, 0)
        badges.addWidget(self.badge)
        badges.addWidget(self.team_lbl)
        badges.addWidget(self.temper_lbl)
        badges.addWidget(self.risk_lbl)
        badges.addWidget(self.assign_lbl)
        badges.addWidget(self.friend_lbl)
        badges.addStretch(1)
        v.addLayout(badges)
        self.body = QTextBrowser()
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setStyleSheet(
            f"QTextBrowser{{background:transparent;color:{T['text_body']};font-size:13px;}}")
        v.addWidget(self.body, 1)
        self._stick = _Stick(self.body)
        self.setMinimumHeight(230)
        self._html = None
        self._hist = ""
        self._live = None
        self._dead = False
        self._events = []
        self.show_thoughts = True
        self.set_state("wait")

    # ---- look
    def _style_card(self, color):
        if color:
            self.setStyleSheet(
                f"#SwarmCard{{background:{_rgba(color, 0.10)};border:2px solid {color};"
                f"border-radius:{T['r_card']};}}")
        else:
            self.setStyleSheet(
                f"#SwarmCard{{background:{T['bg_elev']};border:1px solid {T['border']};"
                f"border-radius:{T['r_card']};}}")

    def set_alliance(self, label, color, partner=None):
        """Allied members' cards share a colour (border, tint, a ◆ tag) and show a banner
        naming the partner."""
        key = (label, color, partner)
        if key == self._ally_key:
            return
        self._ally_key = key
        self._style_card(color if label else None)
        if label:
            self.ally_lbl.setText(f"◆ Alliance {label}")
            self.ally_lbl.setStyleSheet(f"color:{color};font-size:11px;font-weight:700;")
            who = f"with {partner}" if partner else ""
            self.ally_banner.setText(f"🤝 Allied {who} · Alliance {label}")
            self.ally_banner.setStyleSheet(
                f"color:{color};background:{_rgba(color, 0.16)};border-radius:6px;"
                f"padding:3px 8px;font-size:11.5px;font-weight:700;")
            self.ally_banner.setToolTip(
                "Allies: 1:1 gifts, a signing bonus, income every round, a lower round cost, "
                "safe from each other's theft. They must vote together; leaving costs the "
                "leaver and injures the partner; striking an ally ends it and makes them enemies.")
            self.ally_banner.setVisible(True)
        else:
            self.ally_lbl.setText("")
            self.ally_banner.setVisible(False)

    def play_nuke(self, label="NUKE", accidental=False):
        """Animation over this tile: it fired a nuke."""
        NukeOverlay(self, "nuke", label, accidental)

    def play_hit(self):
        """Animation over this tile: someone else's nuke hit it."""
        NukeOverlay(self, "hit")

    def set_team(self, team, dormant=False):
        """VS mode badge: BLUE / RED (a Red member shows 'joins after round 0' until it does)."""
        if team not in ("blue", "red"):
            self.team_lbl.setVisible(False)
            return
        col = "#4F9DDE" if team == "blue" else "#E0665F"
        self.team_lbl.setText(("🔵 BLUE" if team == "blue" else "🔴 RED")
                              + (" · joins after round 0" if dormant else ""))
        self.team_lbl.setStyleSheet(
            f"color:#0C0E12;background:{col};border-radius:9px;padding:2px 8px;"
            f"font-size:10px;font-weight:700;")
        self.team_lbl.setVisible(True)

    def set_temper(self, kind):
        """Public temperament badge: pacifist / violent (normal shows nothing)."""
        if kind == "pacifist":
            self.temper_lbl.setText("🕊 PACIFIST")
            col = "#3FB8A4"
        elif kind == "violent":
            self.temper_lbl.setText("⚔ VIOLENT")
            col = "#E0665F"
        else:
            self.temper_lbl.setVisible(False)
            return
        self.temper_lbl.setStyleSheet(
            f"color:#0C0E12;background:{col};border-radius:9px;padding:2px 8px;"
            f"font-size:10px;font-weight:700;")
        self.temper_lbl.setVisible(True)

    def set_risk(self, on):
        self.risk_lbl.setVisible(bool(on))

    def set_friend(self, mine):
        """mine: {other: score}. Only scores below 3 are shown, e.g. '💛 Ava 2/3 · Bram 0/3'."""
        low = [f"{o} {v}/3" for o, v in (mine or {}).items() if v < 3]
        self.friend_lbl.setVisible(bool(low))
        self.friend_lbl.setText("💛 " + " · ".join(low) if low else "")

    def set_charge(self, n):
        self.charge_lbl.setText(f"⚡{n}" if n else "")

    def set_assigned(self, on):
        self.assign_lbl.setVisible(bool(on))

    def set_controversial(self, on, debuff=None, aligned=False):
        """Badge for members the judge ruled controversial. debuff = extra decay per round
        while it still applies; None/0 once the member has flipped someone (cleared)."""
        self.badge.setVisible(bool(on))
        if not on:
            return
        tail = " · 🏆 aligned" if aligned else ""
        if debuff:
            self.badge.setText(f"🔥 CONTROVERSIAL · -{debuff:g}/round{tail}")
            self.badge.setStyleSheet(
                f"color:#0C0E12;background:{T['warn']};border-radius:9px;padding:2px 8px;"
                f"font-size:10px;font-weight:700;")
            self.badge.setToolTip("Ruled controversial: one-time buff received, but it loses "
                                  f"an extra {debuff:g} rating every round until it flips "
                                  "another member (someone changes stance and credits it).")
        else:
            self.badge.setText(f"🔥 controversial · debuff cleared{tail}")
            self.badge.setStyleSheet(
                f"color:{T['warn']};background:{_rgba(T['warn'], 0.15)};border-radius:9px;"
                f"padding:2px 8px;font-size:10px;font-weight:700;")
            self.badge.setToolTip("Ruled controversial, but it flipped another member, so "
                                  "the extra decay no longer applies.")

    def set_dead(self, dead):
        """Eliminated members' tiles go gray (a monochrome colorize over the whole card)."""
        if dead == self._dead:
            return
        self._dead = dead
        if dead:
            fx = QGraphicsColorizeEffect(self)
            fx.setColor(QColor(150, 150, 150))
            fx.setStrength(1.0)
            self.setGraphicsEffect(fx)
        else:
            self.setGraphicsEffect(None)

    def set_rating(self, r):
        if r is None:
            self.rating_lbl.setText("")
            return
        self.rating_lbl.setText(f"★ {r:.1f}")
        self.rating_lbl.setStyleSheet(f"color:{_rating_color(r)};font-size:12px;font-weight:700;")

    def set_state(self, state):
        label, fg, bg = CHIPS[state]
        self.chip.setText(label)
        self.chip.setStyleSheet(
            f"color:{fg};background:{bg};border-radius:9px;padding:2px 10px;"
            f"font-size:11px;font-weight:600;")

    # ---- history (the ANSWERS only; strategic moves live in the public panel)
    def set_show_thoughts(self, on):
        if on != self.show_thoughts:
            self.show_thoughts = on
            self.set_history(self._events)

    def set_history(self, events):
        """events: this member's replies, the user's messages and its death, in order."""
        self._events = events
        parts = []
        for m in events:
            if m.get("role") == "user":
                tag = "YOU INTERJECTED" if m.get("interject") else "YOU"
                parts.append(
                    f"<div style='margin:10px 0 2px 0;padding:4px 8px;"
                    f"border-left:3px solid {T['accent']};color:{T['text_hi']}'>"
                    f"<span style='color:{T['accent']};font-size:11px'>{tag}</span><br>"
                    f"{html.escape(m.get('content', ''))}</div>")
                continue
            if m.get("kind") == "comment":
                parts.append(
                    f"<div style='margin:4px 0;padding:3px 8px;border-left:3px solid "
                    f"{T['accent']};color:{T['text_mid']};font-size:12px'>💬 "
                    f"<b style='color:{speaker_color(m.get('frm', '?'))}'>"
                    f"{html.escape(str(m.get('frm', '?')))}</b> (R{m.get('round', 0)}): "
                    f"{html.escape(m.get('content', ''))}</div>")
                continue
            if m.get("kind") == "death":
                parts.append(
                    f"<div style='margin:10px 0;padding:6px 8px;border-left:3px solid "
                    f"{T['danger']};color:{T['text_hi']}'>☠ ELIMINATED in round "
                    f"{m.get('round')} — "
                    + ("not in the largest group (final purge)." if not m.get("line") else
                       f"rating {m.get('rating')} fell below {m.get('line', 0):g}.")
                    + " No longer taking part.</div>")
                continue
            r = m.get("round", 0)
            head = "Independent position" if r == 0 else f"Round {r}"
            if m.get("stance"):
                head += f" · {m['stance']}"
            parts.append(f"<div style='color:{T['text_dim']};font-size:11px;margin-top:8px'>"
                         f"{head.upper()}</div>")
            if m.get("bloc"):
                parts.append(f"<div style='color:{T['reason']};font-size:11px'>⛓ bound by the "
                             f"alliance vote (their reply said {m.get('raw_stance') or '?'})</div>")
            if m.get("rating") is not None:
                note = ""
                if m.get("changed"):
                    note = f" · changed stance {m.get('was') or ''} → {m.get('stance')}"
                parts.append(f"<div style='color:{_rating_color(m['rating'])};font-size:11px'>"
                             f"★ {m['rating']:.1f}{note}</div>")
            th = (m.get("thoughts") or "").strip()
            if th and self.show_thoughts:
                parts.append(f"<div style='margin:3px 0;padding:3px 6px;background:{T['reason_bg']};"
                             f"color:{T['reason']};font-size:12px;font-style:italic'>"
                             f"💭 private: {html.escape(th).replace(chr(10), '<br>')}</div>")
            pos = html.escape(m.get("position") or "")
            if pos:
                parts.append(f"<div style='color:{T['text_hi']};margin:2px 0'><b>{pos}</b></div>")
            if m.get("debate_with"):
                claim = (m.get("claim") or "").strip()
                parts.append(
                    f"<div style='color:{T['accent']};font-size:11px;margin:2px 0'>↪ answering "
                    f"<b>{html.escape(str(m['debate_with']))}</b>"
                    + (f": “{html.escape(claim[:140])}”" if claim else "")
                    + ("" if m.get("engaged", True) else " · ⚠ did not engage them")
                    + "</div>")
            body = (m.get("content") or "").strip()
            if body and body != (m.get("position") or "").strip():
                parts.append(f"<div style='color:{T['text_mid']}'>"
                             f"{html.escape(body).replace(chr(10), '<br>')}</div>")
        self._hist = "".join(parts)
        self._render()

    def set_live(self, text, tps):
        """Text streaming in right now (not yet a finished reply)."""
        self._live = text
        self.tps_lbl.setText(f"{tps:.1f} t/s")
        self._render()

    def clear_live(self):
        if self._live is not None or self.tps_lbl.text():
            self._live = None
            self.tps_lbl.setText("")
            self._render()

    def _render(self):
        doc = self._hist
        if self._live is not None:
            if self._live.startswith("💭"):           # the private strategy session, streaming
                body = html.escape(self._live[1:].strip()).replace(chr(10), "<br>")
                if self.show_thoughts:
                    doc += (f"<div style='color:{T['accent']};font-size:11px;margin-top:8px'>"
                            f"💭 THINKING (private)…</div>"
                            f"<div style='margin:3px 0;padding:3px 6px;background:{T['reason_bg']};"
                            f"color:{T['reason']};font-size:12px;font-style:italic'>"
                            f"{body} ▌</div>")
                else:
                    doc += (f"<div style='color:{T['accent']};font-size:11px;margin-top:8px'>"
                            f"💭 thinking…</div>")
            else:
                doc += (f"<div style='color:{T['accent']};font-size:11px;margin-top:8px'>"
                        f"WRITING…</div><div style='color:{T['text_mid']}'>"
                        f"{html.escape(self._live).replace(chr(10), '<br>')} ▌</div>")
        if doc == self._html:                 # nothing new for this member
            return
        self._html = doc
        self._stick.set_html(doc)


class SwarmBoard(QWidget):
    CARD_MIN_W = 300

    def __init__(self):
        super().__init__()
        self.cards = {}
        self._order = []
        self.live = {}                       # name -> streaming text right now
        self._live_pending = {}              # name -> (text, tps) not yet painted
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._flush_live)
        self._moves_html = None
        self._nuke_seen = 0                  # nuke moves already shown/animated
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)
        self.alert_lbl = QLabel()            # a transient banner when a nuke goes off
        self.alert_lbl.setVisible(False)
        self.alert_lbl.setStyleSheet(
            f"color:#fff;background:{T['danger']};border-radius:8px;padding:6px 12px;"
            f"font-size:14px;font-weight:700;")
        self._alert_timer = QTimer(self)
        self._alert_timer.setSingleShot(True)
        self._alert_timer.timeout.connect(lambda: self.alert_lbl.setVisible(False))
        v.addWidget(self.alert_lbl)
        self.q_lbl = QLabel()
        self.q_lbl.setWordWrap(True)
        self.q_lbl.setStyleSheet(f"color:{T['text_hi']};font-size:16px;font-weight:600;")
        v.addWidget(self.q_lbl)
        row = QHBoxLayout()
        self.round_lbl = QLabel()
        self.round_lbl.setStyleSheet(f"color:{T['text_mid']};font-size:12px;")
        self.cb_thoughts = QCheckBox("Show private thoughts")
        self.cb_thoughts.setChecked(True)
        self.cb_thoughts.setToolTip("Each member's private strategy notes (only you see these; "
                                    "other members never do).")
        self.cb_thoughts.toggled.connect(self._thoughts_toggled)
        self.tally_lbl = QLabel()
        self.tally_lbl.setStyleSheet(f"color:{T['accent']};font-size:12px;font-weight:600;")
        row.addWidget(self.round_lbl)
        row.addSpacing(12)
        row.addWidget(self.cb_thoughts)
        row.addStretch(1)
        row.addWidget(self.tally_lbl)
        v.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        v.addWidget(self.bar)

        # cards (left) | public strategic moves (right)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.inner)

        self.moves_panel = QFrame()
        self.moves_panel.setObjectName("MovesPanel")
        self.moves_panel.setStyleSheet(
            f"#MovesPanel{{background:{T['bg_surface']};border:1px solid {T['border']};"
            f"border-radius:{T['r_card']};}}")
        mv = QVBoxLayout(self.moves_panel)
        mv.setContentsMargins(12, 10, 12, 10)
        mv.setSpacing(6)
        title = QLabel("STRATEGIC MOVES · public")
        title.setStyleSheet(f"color:{T['text_mid']};font-size:11px;font-weight:700;"
                            f"letter-spacing:1px;")
        mv.addWidget(title)
        self.moves = QTextBrowser()
        self.moves.setFrameShape(QFrame.Shape.NoFrame)
        self.moves.setStyleSheet(
            f"QTextBrowser{{background:transparent;color:{T['text_body']};font-size:12.5px;}}")
        mv.addWidget(self.moves, 1)
        self.moves_panel.setMinimumWidth(220)
        self._moves_stick = _Stick(self.moves)

        self.msgs_panel = QFrame()
        self.msgs_panel.setObjectName("MsgsPanel")
        self.msgs_panel.setStyleSheet(
            f"#MsgsPanel{{background:{T['bg_surface']};border:1px solid {T['border']};"
            f"border-radius:{T['r_card']};}}")
        mp = QVBoxLayout(self.msgs_panel)
        mp.setContentsMargins(12, 10, 12, 10)
        mp.setSpacing(6)
        mtitle = QLabel("SECRET MESSAGES · only you see these")
        mtitle.setStyleSheet(f"color:{T['reason']};font-size:11px;font-weight:700;"
                             f"letter-spacing:1px;")
        mp.addWidget(mtitle)
        self.msgs = QTextBrowser()
        self.msgs.setFrameShape(QFrame.Shape.NoFrame)
        self.msgs.setStyleSheet(
            f"QTextBrowser{{background:transparent;color:{T['text_body']};font-size:12.5px;}}")
        mp.addWidget(self.msgs, 1)
        self.msgs_panel.setMinimumWidth(220)
        self._msgs_stick = _Stick(self.msgs)
        self._msgs_html = None
        self.right = QSplitter(Qt.Orientation.Vertical)
        self.right.addWidget(self.moves_panel)
        self.right.addWidget(self.msgs_panel)
        self.right.setChildrenCollapsible(False)
        self.right.setSizes([430, 230])

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(self.scroll)
        self.split.addWidget(self.right)
        self.split.setStretchFactor(0, 4)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([760, 280])
        self.split.setChildrenCollapsible(False)
        v.addWidget(self.split, 1)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.setVisible(False)
        v.addWidget(self.result)

        self.empty = QLabel("Ask a question below — each swarm member forms a position, "
                            "argues, and appears here as its own card.")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color:{T['text_dim']};font-size:14px;padding:40px;")
        v.addWidget(self.empty, 1)
        self._show_run(False)

    # ---- layout
    def _cols(self):
        return max(1, self.scroll.viewport().width() // self.CARD_MIN_W)

    def _relayout(self):
        while self.grid.count():
            self.grid.takeAt(0)
        cols = self._cols()
        for i, name in enumerate(self._order):
            self.grid.addWidget(self.cards[name], i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._order:
            self._relayout()

    def _show_run(self, on):
        for w in (self.q_lbl, self.round_lbl, self.tally_lbl, self.bar, self.split,
                  self.cb_thoughts):
            w.setVisible(on)
        self.empty.setVisible(not on)
        if not on:
            self.result.setVisible(False)

    def _thoughts_toggled(self, on):
        for c in self.cards.values():
            c.set_show_thoughts(bool(on))

    # ---- live streaming
    def set_live(self, name, text, tps):
        """Streaming text arrives far faster than it is worth repainting (an API model writes
        100+ tokens/s): keep only the latest per member and paint ~6 times a second."""
        self.live[name] = text
        self._live_pending[name] = (text, tps)
        if not self._live_timer.isActive():
            self._live_timer.start(160)

    def _flush_live(self):
        pend, self._live_pending = self._live_pending, {}
        for name, (text, tps) in pend.items():
            card = self.cards.get(name)
            if card and name in self.live:
                card.set_live(text, tps)
                card.set_state("write")

    def clear_live(self, name=None):
        for n in ([name] if name else list(self.live)):
            self.live.pop(n, None)
            self._live_pending.pop(n, None)
            card = self.cards.get(n)
            if card:
                card.clear_live()

    # ---- the public moves feed
    def _moves_to_html(self, moves, names):
        if not moves:
            return (f"<div style='color:{T['text_dim']};font-size:12px'>No strategic moves yet — "
                    f"gifts, alliances, leaves, credits and judge rulings appear here.</div>")
        pat = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                         + r")\b") if names else None
        out, cur = [], None
        for m in moves:
            r = m.get("round", 0)
            if r != cur:
                cur = r
                out.append(f"<div style='color:{T['text_dim']};font-size:10.5px;"
                           f"letter-spacing:1px;margin:10px 0 3px 0'>"
                           f"{'OPENING' if r == 0 else f'ROUND {r}'}</div>")
            icon, col = MOVE_STYLE.get(m.get("type"), ("•", T["text_mid"]))
            txt = html.escape(m.get("content", ""))
            if pat:
                txt = pat.sub(lambda mo: f"<b style='color:{speaker_color(mo.group(1))}'>"
                                         f"{mo.group(1)}</b>", txt)
            if m.get("type") == "nuke":      # a nuke must be unmissable in the strategy bar
                out.append(f"<div style='margin:6px 0;padding:6px 8px;background-color:#3A1D1D;"
                           f"border-left:4px solid {T['danger']};color:{T['text_hi']};"
                           f"font-weight:700'><span style='color:{col};font-size:15px'>{icon}</span> "
                           f"{txt}</div>")
                continue
            out.append(f"<div style='margin:3px 0'><span style='color:{col}'>{icon}</span> "
                       f"{txt}</div>")
        return "".join(out)

    def _fire_nuke(self, m, dead):
        """A nuke went off (chosen or accidental): play the tile animations + a banner."""
        who = m.get("who")
        accidental = bool(m.get("auto")) or "ON ITS OWN" in (m.get("content") or "")
        card = self.cards.get(who)
        if card:
            card.play_nuke("ACCIDENTAL NUKE" if accidental else "NUKE", accidental)
        for name, c in self.cards.items():
            if name != who and name not in dead:
                c.play_hit()
        self.alert_lbl.setText(
            f"☢ {who}'s nuke went off ON ITS OWN!" if accidental else f"☢ {who} FIRED THE NUKE!")
        self.alert_lbl.setVisible(True)
        self._alert_timer.start(5000)

    def _messages_to_html(self, msgs, names):
        if not msgs:
            return (f"<div style='color:{T['text_dim']};font-size:12px'>No secret messages yet. "
                    f"A message sent in round N is read by its recipient at the start of round "
                    f"N+1; a reply reaches the sender one round after that.</div>")
        out, cur = [], None
        for m in msgs:
            r = m.get("round", 0)
            if r != cur:
                cur = r
                out.append(f"<div style='color:{T['text_dim']};font-size:10.5px;"
                           f"letter-spacing:1px;margin:10px 0 3px 0'>SENT IN ROUND {r}</div>")
            a = m.get("frm", "?")
            to = m.get("to", "?")
            to = to if isinstance(to, list) else [to]          # older saves stored one name
            dest = ", ".join(f"<b style='color:{speaker_color(x)}'>{html.escape(str(x))}</b>"
                             for x in to)
            tags = ""
            if len(to) > 1:
                tags += (f" <span style='color:{T['reason']};font-size:11px'>👥 group of "
                         f"{len(to)} — all see each other</span>")
            if m.get("subvert"):
                tags += (f" <span style='color:{T['danger']};font-size:11px;font-weight:700'>"
                         f"⚔ SUBVERT → {html.escape(str(m['subvert']))} (public, but anonymous "
                         f"to the players)</span>")
            out.append(
                f"<div style='margin:4px 0;padding:4px 8px;background:{T['reason_bg']};"
                f"border-left:3px solid {T['reason']}'>"
                f"<b style='color:{speaker_color(a)}'>{html.escape(str(a))}</b> → {dest} "
                f"<span style='color:{T['text_dim']};font-size:11px'>(read in round {r + 1}, "
                f"-{m.get('cost', 0):g})</span>{tags}<br>"
                f"<span style='color:{T['text_hi']}'>{html.escape(m.get('content', ''))}</span>"
                f"</div>")
        return "".join(out)

    # ---- data
    def refresh(self, messages, members, thinking=(), running=False):
        has_swarm = any(m.get("kind") for m in messages)
        starts = [i for i, m in enumerate(messages)
                  if m.get("role") == "user" and not m.get("interject")]
        if not starts or not (has_swarm or running):
            self._nuke_seen = 0
            self._show_run(False)
            return
        self._show_run(True)
        qi = starts[-1]                                  # latest question that started a run
        seg = messages[qi:]
        notes = [m["content"] for m in messages[qi + 1:]
                 if m.get("role") == "user" and m.get("interject")]
        prop = next((m.get("content", "") for m in messages[qi + 1:] if m.get("kind") == "frame"), "")
        self.q_lbl.setText(messages[qi].get("content", "")
                           + (f"\nProposition: {prop}" if prop else "")
                           + "".join(f"\n↳ you added: {n}" for n in notes))

        # members: this run's snapshot, else derive them from the saved messages
        mem = [dict(m) for m in members] if members else []
        if not mem:
            seen = {}
            for m in messages:
                sp = m.get("speaker")
                if sp and sp not in seen:
                    seen[sp] = {"name": sp, "model": m.get("model", ""), "temp": None}
            mem = list(seen.values())
        names = [m["name"] for m in mem]
        if names != self._order:
            for c in self.cards.values():
                c.setParent(None)
                c.deleteLater()
            self.cards = {m["name"]: MemberCard(m["name"], m["model"], m.get("temp"))
                          for m in mem}
            for c in self.cards.values():
                c.show_thoughts = self.cb_thoughts.isChecked()
            self._order = names
            self._relayout()

        last_div, cur = -1, 0                            # the round currently in progress
        for i, m in enumerate(messages):
            if m.get("kind") == "divider":
                c = m.get("content", "")
                mt = re.match(r"Round (\d+)", c)
                if mt or "Independent" in c:
                    last_div, cur = i, (int(mt.group(1)) if mt else 0)

        # public state: ratings, alliances, controversy rulings, deaths
        dead = {}
        for m in seg:                                    # in order: a revival cancels a death
            if m.get("kind") == "death":
                dead[m["speaker"]] = m
            elif m.get("kind") == "revive":
                dead.pop(m["speaker"], None)
        snap = next((m.get("values") or {} for m in reversed(seg) if m.get("kind") == "ratings"), {})
        rating_now = dict(snap)
        if not snap:                                     # older saves: fall back to reply ratings
            for m in seg:
                if m.get("kind") in ("position", "rebuttal") and m.get("rating") is not None:
                    rating_now[m["speaker"]] = m["rating"]
                elif m.get("kind") == "death" and m.get("rating") is not None:
                    rating_now[m["speaker"]] = m["rating"]
        groups = next((m.get("groups") or {} for m in reversed(seg) if m.get("kind") == "alliances"), {})
        ally = {}
        for label, members_ in groups.items():
            color = ALLY_COLORS[(ord(label[0]) - 65) % len(ALLY_COLORS)]
            for x in members_:
                ally[x] = (label, color)
        contro, debuffed, aligned = set(), {}, set()
        for m in seg:
            if m.get("kind") != "move" or not m.get("who"):
                continue
            if m.get("type") in ("ruling", "buff"):
                contro.add(m["who"])
                if m.get("type") == "ruling":
                    debuffed[m["who"]] = m.get("debuff") or 0
            elif m.get("type") == "cleared":
                debuffed[m["who"]] = 0
            elif m.get("type") == "aligned":
                aligned.add(m["who"])
        moves = [m for m in seg if m.get("kind") == "move"]
        nukes = [m for m in moves if m.get("type") == "nuke"]
        if running:                          # live: animate nukes we haven't shown yet
            for m in nukes[self._nuke_seen:]:
                self._fire_nuke(m, dead)
        self._nuke_seen = len(nukes)         # idle refreshes (old chats) just sync, no replay
        charge_now = next((m.get("values") or {} for m in reversed(seg)
                           if m.get("kind") == "charge"), {})
        friend_now = next((m.get("values") or {} for m in reversed(seg)
                           if m.get("kind") == "friend"), {})
        risk_now = next((m.get("values") or {} for m in reversed(seg)
                         if m.get("kind") == "risk"), {})
        temper_now = next((m.get("values") or {} for m in reversed(seg)
                           if m.get("kind") == "temperaments"), {})
        vs_now = next((m.get("values") or {} for m in reversed(seg)
                       if m.get("kind") == "vs"), {})
        vs_teams = vs_now.get("teams") or {}
        dormant_names = ({n for n, t in vs_teams.items() if t == "red"}
                         if vs_teams and not vs_now.get("active", True) else set())
        assigned = set(next((m.get("names") or [] for m in reversed(seg)
                             if m.get("kind") == "assignment"), []))

        posted = 0
        latest = {}                                      # name -> that member's last reply
        for name in names:
            idxs = [i for i, m in enumerate(messages)
                    if m.get("speaker") == name and m.get("kind") in ("position", "rebuttal")]
            events = [m for m in messages
                      if m.get("role") == "user" or
                      (m.get("speaker") == name and m.get("kind") in ("position", "rebuttal", "death"))
                      or (m.get("kind") == "comment" and m.get("to") == name)]
            card = self.cards[name]
            card.set_history(events)
            last = messages[idxs[-1]] if idxs else None
            answered = bool(idxs) and idxs[-1] > last_div
            if name in dormant_names:
                st = "wait"
            elif name in dead:
                st = "dead"
            elif name in self.live:
                st = "write"
            elif name in thinking:
                st = "think"
            elif last is None or (running and not answered):
                st = "wait"
            else:
                st = {"YES": "yes", "NO": "no", "CONDITIONAL": "cond"}.get(last.get("stance"), "pos")
            card.set_state(st)
            card.set_dead(name in dead)
            card.set_rating(rating_now.get(name))
            lab, col = ally.get(name, (None, None))
            card.set_alliance(lab, col, ", ".join(x for x in groups.get(lab, []) if x != name)
                              if lab else None)
            card.set_assigned(name in assigned)
            card.set_charge(0 if name in dead else charge_now.get(name, 0))
            card.set_friend({} if name in dead else friend_now.get(name, {}))
            card.set_risk(False if name in dead else bool(risk_now.get(name)))
            card.set_temper(temper_now.get(name, "normal"))
            card.set_team(vs_teams.get(name), name in dormant_names)
            card.set_controversial(name in contro, debuffed.get(name) if name not in dead else 0,
                                   name in aligned)
            if last is not None:
                latest[name] = last
                e = last.get("endorse")
                card.end_lbl.setText("" if (not e or name in dead) else
                                     ("own statement" if e == name else f"→ {e}"))
            if answered:
                posted += 1

        # the public moves panel
        doc = self._moves_to_html(moves, names)
        if doc != self._moves_html:
            self._moves_html = doc
            self._moves_stick.set_html(doc)

        mdoc = self._messages_to_html([m for m in seg if m.get("kind") == "message"], names)
        if mdoc != self._msgs_html:
            self._msgs_html = mdoc
            self._msgs_stick.set_html(mdoc)

        alive = [x for x in names if x not in dead]
        n = len(alive)
        stances = [latest[x].get("stance") for x in alive if x in latest]
        cond = sum(1 for s in stances if s == "CONDITIONAL")
        counts = {}
        for x in alive:
            e = latest.get(x, {}).get("endorse")
            if e and e not in dead:
                counts[e] = counts.get(e, 0) + 1
        lead = max(counts.items(), key=lambda kv: kv[1]) if counts else None
        rts = [rating_now[x] for x in alive if x in rating_now]
        avg = f" · avg ★ {sum(rts) / len(rts):.1f}" if rts else ""
        skull = f" · ☠ {len(dead)} of {len(names)} eliminated" if dead else ""
        pairs = f" · {len(groups)} alliance(s)" if groups else ""
        self.bar.setRange(0, max(1, n))
        if cur > 0:
            self.round_lbl.setText(f"Round {cur}")
            parts = [f"YES {stances.count('YES')}", f"NO {stances.count('NO')}",
                     f"⚠ {cond} conditional" if cond else "0 conditional"]
            if lead:
                parts.append(f"{lead[1]}/{n} endorse {lead[0]}'s statement")
            self.tally_lbl.setText(" · ".join(parts) + avg + pairs + skull)
            self.bar.setValue(lead[1] if lead and not cond else 0)
        else:
            self.round_lbl.setText("Independent positions")
            self.tally_lbl.setText(f"{posted} / {n} positions in"
                                   + (f" · YES {stances.count('YES')} · NO {stances.count('NO')}"
                                      f" · ⚠ {cond} conditional" if stances else "") + skull)
            self.bar.setValue(posted)

        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
        di = max((i for i, m in enumerate(messages) if m.get("kind") in ("consensus", "stopped")),
                 default=-1)
        done = messages[di] if di > last_user else None   # only if nothing was said since
        if done and done["kind"] == "consensus":
            s = html.escape(done.get("summary", ""))
            self.result.setText(
                f"<div style='color:{T['accent']};font-weight:600'>"
                + ("✓ Last member standing — its statement stands" if done.get("votes") == 1 else
                   f"✓ All {done.get('votes', '?')} living members endorse this identical statement")
                + f"{' (' + done['author'] + ' ' + (done.get('stance') or '') + ')' if done.get('author') else ''}"
                f" · after {done.get('rounds', '?')} round(s)</div>"
                f"<div style='color:{T['text_hi']};font-size:15px'>"
                f"{html.escape(done.get('content', ''))}</div>"
                + (f"<div style='color:{T['text_mid']}'>{s}</div>" if s else ""))
            border = T["accent"]
        elif done:
            self.result.setText(f"<span style='color:{T['warn']}'>"
                                f"{html.escape(done.get('content', ''))}</span>")
            border = T["border"]
        else:
            self.result.setVisible(False)
            return
        self.result.setStyleSheet(f"background:{T['bg_elev']};border:1px solid {border};"
                                  f"border-radius:{T['r_card']};padding:10px 14px;")
        self.result.setVisible(True)
