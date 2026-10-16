"""The launch splash: a small native card (ensō, ZAZEN, what is happening, a progress line) that appears within half a
second of the click, while the agent server starts and the interface loads behind it. app_web.py drives it.

Progress follows real milestones, not a timer: the window is up, the agent server is listening, the interface has
loaded, the app has its settings and chats. Between two milestones the line creeps towards the next one at the pace
the previous launch took (startup.json in the data folder), slowing as it nears it, so it never claims more than
has happened. Qt widgets only: no WebEngine, so it can paint before the heavy parts load.
"""
import json
import os
import re
import time

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

import paths

# milestone -> (share of the bar once reached, what the line says while waiting for the next one)
STEPS = [
    ("start", 0.04, "Starting…"),
    ("window", 0.12, "Starting the agent server…"),
    ("server", 0.62, "Loading the interface…"),
    ("ui", 0.84, "Loading your chats…"),
    ("ready", 1.0, "Ready"),
]
DEFAULT_GAP = {"window": 0.5, "server": 4.0, "ui": 1.2, "ready": 0.8}     # seconds, before any launch was timed

INK = QColor(237, 235, 230)
MUTED = QColor(150, 152, 158)
GROUND = QColor(26, 27, 30)
EDGE = QColor(255, 255, 255, 22)


def _times_path():
    return os.path.join(paths.data_dir(), "startup.json")


def _load_gaps():
    try:
        with open(_times_path(), encoding="utf-8") as f:
            saved = json.load(f)
        return {k: float(saved.get(k, v)) for k, v in DEFAULT_GAP.items()}
    except Exception:
        return dict(DEFAULT_GAP)


class Splash(QWidget):
    W, H = 380, 220

    def __init__(self, logo_png="", logo_svg=""):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)
        self.setWindowTitle("Zazen")
        self.logo = QPixmap(logo_png) if logo_png and os.path.exists(logo_png) else QPixmap()
        self.svg = None                    # the app's favicon ensō without its tile, so it sits on the card itself
        try:
            if logo_svg and os.path.exists(logo_svg):
                from PySide6.QtSvg import QSvgRenderer
                with open(logo_svg, encoding="utf-8") as f:
                    text = re.sub(r"<rect[^>]*/>", "", f.read(), count=1)
                r = QSvgRenderer(QByteArray(text.encode("utf-8")))
                self.svg = r if r.isValid() else None
        except Exception:
            self.svg = None
        self.gaps = _load_gaps()
        self.reached = {"start": time.perf_counter()}
        self.step = 0                      # index into STEPS of the last milestone reached
        self.shown_p = 0.0                 # what the bar draws (eases towards the target)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.W // 2, screen.center().y() - self.H // 2)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    # ------------------------------------------------------------------ progress
    def milestone(self, name):
        """Mark a step as done (ignored if already past it)."""
        idx = next((i for i, s in enumerate(STEPS) if s[0] == name), None)
        if idx is None or idx <= self.step:
            return
        self.step = idx
        self.reached[name] = time.perf_counter()
        self.update()

    def _target(self):
        _, at, _ = STEPS[self.step]
        if self.step + 1 >= len(STEPS):
            return 1.0
        nxt, nxt_at, _ = STEPS[self.step + 1]
        since = time.perf_counter() - self.reached[STEPS[self.step][0]]
        expect = max(0.2, self.gaps.get(nxt, 1.0))
        frac = 1 - pow(2.718, -1.6 * since / expect)       # ~80% of the gap at the expected time, never 100%
        return at + (nxt_at - at) * 0.92 * frac

    def _tick(self):
        t = self._target()
        self.shown_p += (t - self.shown_p) * 0.18
        self.update()

    def save_times(self):
        """Remember how long each step took this time, for the next launch's pacing (blended, not replaced)."""
        try:
            names = [s[0] for s in STEPS]
            gaps = dict(self.gaps)
            for a, b in zip(names, names[1:]):
                if a in self.reached and b in self.reached:
                    gaps[b] = round(0.5 * gaps.get(b, DEFAULT_GAP.get(b, 1.0)) + 0.5 * (self.reached[b] - self.reached[a]), 2)
            os.makedirs(os.path.dirname(_times_path()), exist_ok=True)
            with open(_times_path(), "w", encoding="utf-8") as f:
                json.dump(gaps, f)
        except Exception:
            pass

    def finish(self):
        """Fade out and close (the main window is already showing)."""
        self.timer.stop()
        self.shown_p = 1.0
        self.update()
        fade = QTimer(self)
        state = {"o": 1.0}

        def step():
            state["o"] -= 0.12
            if state["o"] <= 0:
                fade.stop()
                self.close()
            else:
                self.setWindowOpacity(state["o"])
        fade.timeout.connect(step)
        fade.start(16)

    # ------------------------------------------------------------------ drawing
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        card = QPainterPath()
        card.addRoundedRect(r, 14, 14)
        p.fillPath(card, GROUND)
        p.setPen(QPen(EDGE, 1))
        p.drawPath(card)
        # a lit top edge, like the app's panels
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        p.drawLine(QPointF(14, 1.5), QPointF(self.W - 14, 1.5))

        cx = self.W / 2
        if self.svg is not None:
            self.svg.render(p, QRectF(cx - 30, 40, 60, 60))
        elif not self.logo.isNull():
            size = 54
            pm = self.logo.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(int(cx - pm.width() / 2), 44, pm)

        f = QFont("Segoe UI", 13)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 135)
        p.setFont(f)
        p.setPen(INK)
        p.drawText(QRectF(0, 110, self.W, 26), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "ZAZEN")

        f2 = QFont("Segoe UI", 9)
        p.setFont(f2)
        p.setPen(MUTED)
        p.drawText(QRectF(0, 146, self.W, 20), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   STEPS[self.step][2])

        # the progress line: a faint track and an ink fill
        x0, x1, y = 70, self.W - 70, 184
        p.setPen(Qt.PenStyle.NoPen)
        track = QPainterPath()
        track.addRoundedRect(QRectF(x0, y, x1 - x0, 3), 1.5, 1.5)
        p.fillPath(track, QColor(255, 255, 255, 18))
        fill = QPainterPath()
        fill.addRoundedRect(QRectF(x0, y, (x1 - x0) * max(0.02, min(1.0, self.shown_p)), 3), 1.5, 1.5)
        p.fillPath(fill, INK)
        p.end()
