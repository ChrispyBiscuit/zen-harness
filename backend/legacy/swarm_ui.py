"""Qt pieces for swarm mode: the member editor dialog and the worker thread."""
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTableWidget, QTableWidgetItem,
    QComboBox, QDoubleSpinBox, QSpinBox, QPushButton, QLabel, QHeaderView, QCheckBox,
    QDialogButtonBox, QAbstractItemView, QLineEdit, QScrollArea, QWidget, QFrame, QApplication,
)

import swarm
import llama_local

DEFAULT_SWARM = {
    "enabled": False,
    "members": [{"name": swarm.NAMES[i], "model": swarm.DEFAULT_MODEL, "temp": t}
                for i, t in enumerate(swarm.spread_temps(5))],
    "judge_model": "",        # "" = the members' own (most common) model; frames, rules on controversy, summarizes
    "parallel": 2,
    "max_rounds": 0,          # 0 = no cap, only unanimity or the Stop button ends it
    "num_ctx": 6144,
    "ratings": True,          # private 0-10 performance ratings (members only know their own)
    "round_cost": swarm.ROUND_COST,
    "change_cost": swarm.CHANGE_COST,
    "economy": True,          # gifts + alliances
    "gift_ratio": swarm.GIFT_RATIO,
    "form_cost": swarm.FORM_COST,
    "leave_cost": swarm.LEAVE_COST,
    "leave_harm": swarm.LEAVE_HARM,
    "ally_decay": swarm.ALLY_DECAY,
    "controversy_buff": swarm.CONTROVERSY_BUFF,
    "controversy_debuff": swarm.CONTROVERSY_DEBUFF,
    "align_buff": swarm.ALIGN_BUFF,
    "assign_chance": swarm.CONTROVERSY_ROLL,
    "resurrect_chance": swarm.RESURRECT_CHANCE,
    "resurrect_bonus": swarm.RESURRECT_BONUS,
    "alliance_open_round": swarm.ALLIANCE_OPEN_ROUND,
    "steal": True, "steal_amount": swarm.STEAL_AMOUNT, "steal_cap": swarm.STEAL_CAP, "steal_force": swarm.STEAL_FORCE,
    "temperament": True, "pacifist_chance": swarm.TEMPER_PACIFIST,
    "violent_chance": swarm.TEMPER_VIOLENT, "violent_urge": swarm.VIOLENT_URGE,
    "resilience_rounds": swarm.RESILIENCE_ROUNDS,
    "resilience_reduction": swarm.RESILIENCE_REDUCTION,
    "forced_debate": True, "comments": True, "vs_mode": False, "vs_red_model": "",
    "backstab": True, "backstab_threshold": swarm.BACKSTAB_THRESHOLD,
    "backstab_damage": swarm.BACKSTAB_DAMAGE, "lownuke_scale": swarm.LOWNUKE_SCALE, "guarantee_violent": True, "violent_convert": swarm.VIOLENT_CONVERT,
    "violent_heal": swarm.VIOLENT_HEAL, "pacifist_start": swarm.PACIFIST_START,
    "pacifist_heal": swarm.PACIFIST_HEAL, "violent_switch": True,
    "violent_switch_cost": swarm.VIOLENT_SWITCH_COST,
    "pacifist_switch_cost": swarm.PACIFIST_SWITCH_COST,
    "endgame": True, "endgame_rounds": swarm.ENDGAME_ROUNDS,
    "resurrect_player": True, "rez_cost": swarm.REZ_COST, "rez_decline": swarm.REZ_DECLINE_CHANCE,
    "nuke": True, "nuke_cost": swarm.NUKE_COST, "nuke_hit": swarm.NUKE_HIT,
    "nuke_gain": swarm.NUKE_GAIN,
    "messages": True, "message_cost": swarm.MESSAGE_COST,
    "group_cost": swarm.GROUP_MESSAGE_COST, "group_max": swarm.GROUP_MAX,
    "subvert_damage": swarm.SUBVERT_DAMAGE, "subvert_limit": swarm.SUBVERT_LIMIT,
    "subvert_min": swarm.SUBVERT_MIN_GROUP,
    "lownuke_chance": swarm.LOWNUKE_CHANCE, "lownuke_line": swarm.LOWNUKE_LINE,
    "charge": True, "charge_line": swarm.CHARGE_LINE, "charge_after": swarm.CHARGE_AFTER,
    "charge_max": swarm.CHARGE_MAX, "strike_cap": swarm.STRIKE_CAP,
    "strike_miss": swarm.STRIKE_MISS, "friendship": True,
    "group_heal": swarm.GROUP_HEAL, "group_income": swarm.GROUP_INCOME,
    "ally_bonus": swarm.ALLY_BONUS, "ally_income": swarm.ALLY_INCOME,
    "retribution": True, "retribution_cost": swarm.RETRIBUTION_COST,
    "retribution_to": swarm.RETRIBUTION_TO,
    "strategy_tokens": swarm.STRATEGY_TOKENS,
    "thinking": True,         # private strategy scratchpad before each public reply
}


def speaker_color(name):
    """Stable, readable per-member colour (dark theme) derived from the name."""
    h = sum(ord(c) * (i + 3) for i, c in enumerate(name)) % 360
    return QColor.fromHsl(h, 150, 150).name()


class SwarmWorker(QThread):
    member = Signal(dict)
    divider = Signal(str)
    status = Signal(str)
    consensus = Signal(dict)
    stopped = Signal(dict)
    failed = Signal(str)
    finished_all = Signal()
    started = Signal(dict)
    partial = Signal(dict)
    frame = Signal(str)
    died = Signal(dict)
    credit = Signal(dict)
    event = Signal(str, object)     # move / ratings / alliances / member_final
    stats = Signal(dict)

    def __init__(self, question, cfg, context="", seed_pos=None):
        super().__init__()
        self.question = question
        self.cfg = cfg
        self._stop = threading.Event()
        c = cfg
        members, teams = c["members"], None
        if c.get("vs_mode"):                  # VS mode: a same-size Red team joins after round 0
            members, teams = swarm.vs_members(c["members"], c.get("vs_red_model") or "")
        # engine is built up front so the GUI can interject while the thread runs
        self.engine = swarm.Swarm(
            members, self._emit, should_stop=self._stop.is_set,
            vs=bool(teams), teams=teams,
            num_ctx=c.get("num_ctx", 6144), parallel=c.get("parallel", 2),
            judge_model=c.get("judge_model") or None,
            max_rounds=c.get("max_rounds", 0), context=context, seed_pos=seed_pos,
            ratings=c.get("ratings", True),
            round_cost=c.get("round_cost", swarm.ROUND_COST),
            change_cost=c.get("change_cost", swarm.CHANGE_COST),
            economy=c.get("economy", True), gift_ratio=c.get("gift_ratio", swarm.GIFT_RATIO),
            form_cost=c.get("form_cost", swarm.FORM_COST),
            leave_cost=c.get("leave_cost", swarm.LEAVE_COST),
            leave_harm=c.get("leave_harm", swarm.LEAVE_HARM),
            ally_decay=c.get("ally_decay", swarm.ALLY_DECAY),
            controversy_buff=c.get("controversy_buff", swarm.CONTROVERSY_BUFF),
            controversy_debuff=c.get("controversy_debuff", swarm.CONTROVERSY_DEBUFF),
            align_buff=c.get("align_buff", swarm.ALIGN_BUFF),
            assign_chance=c.get("assign_chance", swarm.CONTROVERSY_ROLL),
            resurrect_chance=c.get("resurrect_chance", swarm.RESURRECT_CHANCE),
            resurrect_bonus=c.get("resurrect_bonus", swarm.RESURRECT_BONUS),
            ally_open_round=c.get("alliance_open_round", swarm.ALLIANCE_OPEN_ROUND),
            steal=c.get("steal", True), steal_amount=c.get("steal_amount", swarm.STEAL_AMOUNT),
            steal_cap=c.get("steal_cap", swarm.STEAL_CAP),
            steal_force=c.get("steal_force", swarm.STEAL_FORCE),
            temperament=c.get("temperament", True),
            pacifist_chance=c.get("pacifist_chance", swarm.TEMPER_PACIFIST),
            violent_chance=c.get("violent_chance", swarm.TEMPER_VIOLENT),
            violent_urge=c.get("violent_urge", swarm.VIOLENT_URGE),
            violent_heal=c.get("violent_heal", swarm.VIOLENT_HEAL),
            lownuke_scale=c.get("lownuke_scale", swarm.LOWNUKE_SCALE),
            resilience_rounds=c.get("resilience_rounds", swarm.RESILIENCE_ROUNDS),
            resilience_reduction=c.get("resilience_reduction", swarm.RESILIENCE_REDUCTION),
            backstab=c.get("backstab", True),
            forced_debate=c.get("forced_debate", True),
            comments=c.get("comments", True),
            backstab_threshold=c.get("backstab_threshold", swarm.BACKSTAB_THRESHOLD),
            backstab_damage=c.get("backstab_damage", swarm.BACKSTAB_DAMAGE),
            guarantee_violent=c.get("guarantee_violent", True),
            violent_convert=c.get("violent_convert", swarm.VIOLENT_CONVERT),
            pacifist_start=c.get("pacifist_start", swarm.PACIFIST_START),
            pacifist_heal=c.get("pacifist_heal", swarm.PACIFIST_HEAL),
            violent_switch=c.get("violent_switch", True),
            violent_switch_cost=c.get("violent_switch_cost", swarm.VIOLENT_SWITCH_COST),
            pacifist_switch_cost=c.get("pacifist_switch_cost", swarm.PACIFIST_SWITCH_COST),
            endgame=c.get("endgame", True),
            endgame_rounds=c.get("endgame_rounds", swarm.ENDGAME_ROUNDS),
            resurrect_player=c.get("resurrect_player", True),
            rez_cost=c.get("rez_cost", swarm.REZ_COST),
            rez_decline=c.get("rez_decline", swarm.REZ_DECLINE_CHANCE), nuke=c.get("nuke", True),
            nuke_cost=c.get("nuke_cost", swarm.NUKE_COST),
            nuke_hit=c.get("nuke_hit", swarm.NUKE_HIT),
            nuke_gain=c.get("nuke_gain", swarm.NUKE_GAIN),
            messages=c.get("messages", True),
            message_cost=c.get("message_cost", swarm.MESSAGE_COST),
            group_cost=c.get("group_cost", swarm.GROUP_MESSAGE_COST),
            group_max=c.get("group_max", swarm.GROUP_MAX),
            subvert_damage=c.get("subvert_damage", swarm.SUBVERT_DAMAGE),
            subvert_limit=c.get("subvert_limit", swarm.SUBVERT_LIMIT),
            subvert_min=c.get("subvert_min", swarm.SUBVERT_MIN_GROUP),
            lownuke_chance=c.get("lownuke_chance", swarm.LOWNUKE_CHANCE),
            lownuke_line=c.get("lownuke_line", swarm.LOWNUKE_LINE),
            charge=c.get("charge", True), charge_line=c.get("charge_line", swarm.CHARGE_LINE),
            charge_after=c.get("charge_after", swarm.CHARGE_AFTER),
            charge_max=c.get("charge_max", swarm.CHARGE_MAX),
            strike_cap=c.get("strike_cap", swarm.STRIKE_CAP),
            strike_miss=c.get("strike_miss", swarm.STRIKE_MISS),
            friendship=c.get("friendship", True),
            ally_bonus=c.get("ally_bonus", swarm.ALLY_BONUS),
            ally_income=c.get("ally_income", swarm.ALLY_INCOME),
            group_heal=c.get("group_heal", swarm.GROUP_HEAL),
            group_income=c.get("group_income", swarm.GROUP_INCOME),
            retribution=c.get("retribution", True),
            retribution_cost=c.get("retribution_cost", swarm.RETRIBUTION_COST),
            retribution_to=c.get("retribution_to", swarm.RETRIBUTION_TO),
            strategy_tokens=c.get("strategy_tokens", swarm.STRATEGY_TOKENS),
            thinking=c.get("thinking", True))

    def stop(self):
        self._stop.set()

    def interject(self, text):
        self.engine.interject(text)

    def run(self):
        self.engine.run(self.question)

    def _log_stats(self, d):
        """Append one line per model call (prompt/generated tokens, speeds, seconds) so a
        finished game can be analysed for where the time went."""
        try:
            import json as _json, os as _os, time as _time
            path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "swarm_stats.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(dict(d, t=round(_time.time(), 2))) + "\n")
        except Exception:
            pass

    def _emit(self, kind, data=None):
        if kind == "member":
            self.member.emit(data)
        elif kind == "start":
            self.started.emit(data)
        elif kind == "partial":
            self.partial.emit(data)
        elif kind == "frame":
            self.frame.emit(data["proposition"])
        elif kind == "died":
            self.died.emit(data)
        elif kind == "credit":
            self.credit.emit(data)
        elif kind in ("move", "ratings", "alliances", "member_final", "assigned", "message",
                      "charge", "friend", "risk", "resurrected", "temperaments", "comment", "vs"):
            self.event.emit(kind, data)
        elif kind == "stats":
            self._log_stats(data)
            self.stats.emit(data)
        elif kind == "divider":
            self.divider.emit(data)
        elif kind == "status":
            self.status.emit(data)
        elif kind == "consensus":
            self.consensus.emit(data)
        elif kind == "stopped":
            self.stopped.emit(data)
        elif kind == "error":
            self.failed.emit(data)
        elif kind == "done":
            self.finished_all.emit()


def installed_models():
    """name -> size in bytes of the chat models Ollama has installed (embedding models hidden)."""
    try:
        import ollama
        out = {m.model: int(getattr(m, "size", 0) or 0) for m in ollama.Client().list().models}
    except Exception:
        out = {}
    return {k: v for k, v in out.items() if "embed" not in k.lower()}


def base_name(model):
    """'llama:qwen3:4b' / 'openai:gpt-4.1-nano' / 'qwen3:4b' -> the bare model name."""
    m = str(model)
    for p in (swarm.LLAMA_PREFIX, swarm.API_PREFIX):
        if m.startswith(p):
            return m[len(p):]
    return m


def backend_of(model):
    m = str(model)
    return ("llama" if m.startswith(swarm.LLAMA_PREFIX) else
            "api" if m.startswith(swarm.API_PREFIX) else "ollama")


class SwarmDialog(QDialog):
    COLS = ["Name", "Model", "Temp", ""]

    def __init__(self, cfg, models, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Swarm members")
        scr = QApplication.primaryScreen()
        av = scr.availableGeometry() if scr else None
        w, h = 680, 760
        if av:                                   # never taller/wider than the screen
            w, h = min(w, av.width() - 40), min(h, av.height() - 80)
        self.resize(w, h)
        self.setMinimumSize(420, 320)
        self.sizes = installed_models()
        names = [str(m) for m in models if not str(m).startswith((swarm.API_PREFIX, swarm.LLAMA_PREFIX))]
        for n_ in self.sizes:
            if n_ not in names:
                names.append(n_)
        self.base_models = sorted(names, key=lambda n: (self.sizes.get(n) or 10 ** 12, n))
        mem0 = cfg.get("members") or []
        kinds = {backend_of(m["model"]) for m in mem0}
        self.backend = kinds.pop() if len(kinds) == 1 else "ollama"      # one backend per game
        v = QVBoxLayout(self)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(110)
        v.addWidget(self.table, 2)
        for m in cfg["members"]:
            self._add_row(m["name"], m["model"], m["temp"])

        add = QHBoxLayout()
        self.add_model = QComboBox()
        self._fill(self.add_model, self.backend)
        self._select(self.add_model, self._default_for(self.backend))
        self.add_n = QSpinBox(); self.add_n.setRange(1, 20); self.add_n.setValue(1)
        b_add = QPushButton("Add")
        b_add.clicked.connect(self._add_clicked)
        b_spread = QPushButton("Spread temps")
        b_spread.setToolTip("Distribute temperatures evenly 0.4 → 1.2 across members "
                            "so they don't all answer the same way.")
        b_spread.clicked.connect(self._spread)
        add.addWidget(QLabel("Add")); add.addWidget(self.add_n); add.addWidget(QLabel("×"))
        add.addWidget(self.add_model, 1); add.addWidget(b_add); add.addWidget(b_spread)
        v.addLayout(add)

        provrow = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.addItem("Ollama (local)", "ollama")
        self.provider.addItem("llama.cpp server (local, faster)", "llama")
        self.provider.addItem("OpenAI API (cloud)", "api")
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.backend)))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.key_lbl = QLabel()
        provrow.addWidget(QLabel("Backend"))
        provrow.addWidget(self.provider, 1)
        provrow.addWidget(self.key_lbl)
        v.addLayout(provrow)
        self.key_row = QWidget()
        keyrow = QHBoxLayout(self.key_row)
        keyrow.setContentsMargins(0, 0, 0, 0)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste OpenAI API key (saved locally when you press OK)")
        keyrow.addWidget(QLabel("API key"))
        keyrow.addWidget(self.key_edit, 1)
        v.addWidget(self.key_row)
        self.key_edit.textChanged.connect(lambda _t: self._key_label())
        self._key_label()

        allrow = QHBoxLayout()
        self.all_model = QComboBox()
        self._fill(self.all_model, self.backend)
        self._select(self.all_model, self._default_for(self.backend, prefer=[m["model"] for m in mem0]))
        b_all = QPushButton("Set all members")
        b_all.setToolTip("Switch every member in the table to the model chosen here.")
        b_all.clicked.connect(self._set_all)
        self.all_model.activated.connect(lambda _i: self._set_all())   # picking a model applies it
        allrow.addWidget(QLabel("All members use"))
        allrow.addWidget(self.all_model, 1)
        allrow.addWidget(b_all)
        v.addLayout(allrow)

        form = QFormLayout()
        self.judge = QComboBox()
        self._fill_judge(cfg.get("judge_model", ""))
        self.judge.setToolTip("Only writes the final consensus + brief summary once the members "
                              "unanimously agree. A tiny model is fine.")
        form.addRow("Judge model", self.judge)
        self.parallel = QSpinBox(); self.parallel.setRange(1, 8)
        self.parallel.setValue(cfg.get("parallel", 2))
        self.parallel.setToolTip("Simultaneous requests. Only helps if OLLAMA_NUM_PARALLEL "
                                 "is raised to match; otherwise Ollama queues them.")
        form.addRow("Parallel requests", self.parallel)
        self.maxr = QSpinBox(); self.maxr.setRange(0, 200)
        self.maxr.setSpecialValueText("no limit")
        self.maxr.setValue(cfg.get("max_rounds", 0))
        self.maxr.setToolTip("0 = run until everyone agrees or you press Stop.")
        form.addRow("Max rounds", self.maxr)
        self.ctx = QSpinBox(); self.ctx.setRange(2048, 16384); self.ctx.setSingleStep(1024)
        self.ctx.setValue(cfg.get("num_ctx", 6144))
        form.addRow("Context per member", self.ctx)
        self.ratings = QCheckBox("Private performance ratings (0-10)")
        self.ratings.setChecked(cfg.get("ratings", True))
        self.ratings.setToolTip("Each member secretly tries to keep a high rating. It drops "
                                "every round and when the member changes its stance. Members "
                                "never see each other's ratings; you see all of them.")
        form.addRow("", self.ratings)
        self.rcost = QDoubleSpinBox(); self.rcost.setRange(0.0, 3.0); self.rcost.setSingleStep(0.1); self.rcost.setDecimals(3)
        self.rcost.setValue(cfg.get("round_cost", swarm.ROUND_COST))
        self.rcost.setToolTip("Rating lost by every member each debate round.")
        form.addRow("Cost per round", self.rcost)
        self.maxr.valueChanged.connect(self._sync_round_cost)
        self._sync_round_cost()
        self.ccost = QDoubleSpinBox(); self.ccost.setRange(0.0, 9.0); self.ccost.setSingleStep(0.1)
        self.ccost.setValue(cfg.get("change_cost", swarm.CHANGE_COST))
        self.ccost.setToolTip("Extra rating lost when a member changes its stance "
                              "(YES / NO / CONDITIONAL). Adopting another member's wording "
                              "while keeping the same stance is free.")
        form.addRow("Cost of changing stance", self.ccost)
        self.economy = QCheckBox("Gifts && alliances (pairs)")
        self.economy.setChecked(cfg.get("economy", True))
        self.economy.setToolTip("Members can gift points (costly) and form allied pairs that "
                                "gift 1:1, decay slower and must vote together.")
        form.addRow("", self.economy)

        def _spin(lo, hi, step, val, tip):
            sp = QDoubleSpinBox(); sp.setRange(lo, hi); sp.setSingleStep(step); sp.setDecimals(2)
            sp.setValue(val); sp.setToolTip(tip)
            return sp
        self.gratio = _spin(1.0, 5.0, 0.1, cfg.get("gift_ratio", swarm.GIFT_RATIO),
                            "Points a giver pays per 1 point a non-ally receives.")
        form.addRow("Gift cost (per 1 received)", self.gratio)
        self.fcost = _spin(0.0, 3.0, 0.05, cfg.get("form_cost", swarm.FORM_COST),
                           "Each ally pays this to form an alliance (pairs only).")
        form.addRow("Alliance formation cost", self.fcost)
        self.lcost = _spin(0.0, 5.0, 0.25, cfg.get("leave_cost", swarm.LEAVE_COST),
                           "The leaver pays this to leave an alliance.")
        form.addRow("Leaving cost (leaver)", self.lcost)
        self.lharm = _spin(0.0, 5.0, 0.25, cfg.get("leave_harm", swarm.LEAVE_HARM),
                           "The partner left behind loses this.")
        form.addRow("Leaving injury (partner)", self.lharm)
        self.adecay = _spin(0.0, 1.0, 0.05, cfg.get("ally_decay", swarm.ALLY_DECAY),
                            "Allied members pay this share of the round cost (0.5 = half).")
        form.addRow("Allied decay (x round cost)", self.adecay)
        self.abonus = _spin(0.0, 5.0, 0.25, cfg.get("ally_bonus", swarm.ALLY_BONUS),
                            "Rating each ally gains the moment an alliance forms.")
        form.addRow("Alliance signing bonus (each)", self.abonus)
        self.aincome = _spin(0.0, 2.0, 0.05, cfg.get("ally_income", swarm.ALLY_INCOME),
                             "Rating each ally gains at the start of every round.")
        form.addRow("Alliance income / round (each)", self.aincome)
        self.gheal = _spin(0.0, 2.0, 0.05, cfg.get("group_heal", swarm.GROUP_HEAL),
                           "Per member above two: every group member heals this each round.")
        form.addRow("Group heal / extra member", self.gheal)
        self.gincome = _spin(0.0, 2.0, 0.05, cfg.get("group_income", swarm.GROUP_INCOME),
                             "Per member above two: an additional gain on top of the heal.")
        form.addRow("Group extra income / extra member", self.gincome)
        self.cbuff = _spin(0.0, 10.0, 0.5, cfg.get("controversy_buff", swarm.CONTROVERSY_BUFF),
                           "One-time rating buff when the judge rules an opinion "
                           "controversial. 0 = off.")
        form.addRow("Controversy buff (judge)", self.cbuff)
        self.cdice = _spin(0.0, 1.0, 0.05, cfg.get("assign_chance", swarm.CONTROVERSY_ROLL),
                           "Chance, rolled by code before round 0, that each member is "
                           "ASSIGNED a bold controversial stance. If nobody hits, one random "
                           "member is assigned anyway.")
        form.addRow("Controversy dice (per member)", self.cdice)
        self.cdebuff = _spin(0.0, 3.0, 0.05, cfg.get("controversy_debuff", swarm.CONTROVERSY_DEBUFF),
                             "Extra rating lost per round by a controversial member until it "
                             "flips another member. 0 = off.")
        form.addRow("Controversy debuff / round", self.cdebuff)
        self.abuff = _spin(0.0, 15.0, 0.5, cfg.get("align_buff", swarm.ALIGN_BUFF),
                           "One-time rating buff for a controversial member that gets 25% of "
                           "the group to share its stance and endorse its statement. 0 = off.")
        form.addRow("Alignment buff (25% of group)", self.abuff)
        self.rchance = _spin(0.0, 1.0, 0.05, cfg.get("resurrect_chance", swarm.RESURRECT_CHANCE),
                             "Chance that a member who dies without ever changing its stance "
                             "comes back. 0 = off.")
        form.addRow("Resurrection chance", self.rchance)
        self.rbonus = _spin(0.0, 10.0, 0.5, cfg.get("resurrect_bonus", swarm.RESURRECT_BONUS),
                            "Rating added on resurrection (it must lift the member back to the "
                            "death line).")
        form.addRow("Resurrection bonus", self.rbonus)
        self.aopen = QSpinBox(); self.aopen.setRange(1, 50)
        self.aopen.setValue(int(cfg.get("alliance_open_round", swarm.ALLIANCE_OPEN_ROUND)))
        self.aopen.setToolTip("Alliances can first be proposed in this round (earlier "
                              "proposals are ignored).")
        form.addRow("Alliances open from round", self.aopen)
        self.steal = QCheckBox("Stealing (one per player per round, never from an ally)")
        self.steal.setChecked(cfg.get("steal", True))
        form.addRow("", self.steal)
        self.sam = _spin(0.0, 3.0, 0.05, cfg.get("steal_amount", swarm.STEAL_AMOUNT),
                         "Rating moved from victim to thief per theft.")
        form.addRow("Steal amount", self.sam)
        self.scap = _spin(0.0, 5.0, 0.25, cfg.get("steal_cap", swarm.STEAL_CAP),
                          "The most any member can lose to theft in one round.")
        form.addRow("Steal cap per victim/round", self.scap)
        self.sforce = _spin(0.0, 10.0, 0.5, cfg.get("steal_force", swarm.STEAL_FORCE),
                            "Members at or below this rating MUST steal from someone each round "
                            "(they may explain themselves to the victim). 0 = never forced.")
        form.addRow("Forced theft at rating ≤", self.sforce)
        self.temp = QCheckBox("Temperaments: pacifists / violent players (public)")
        self.temp.setChecked(cfg.get("temperament", True))
        form.addRow("", self.temp)
        self.tpac = _spin(0.0, 1.0, 0.05, cfg.get("pacifist_chance", swarm.TEMPER_PACIFIST),
                          "Chance each member is rolled a pacifist (never damages anyone).")
        form.addRow("Pacifist chance", self.tpac)
        self.tvio = _spin(0.0, 1.0, 0.05, cfg.get("violent_chance", swarm.TEMPER_VIOLENT),
                          "Chance each member is rolled violent.")
        form.addRow("Violent chance", self.tvio)
        self.turge = _spin(0.0, 1.0, 0.05, cfg.get("violent_urge", swarm.VIOLENT_URGE),
                           "Per round, chance a violent member MUST try to damage someone.")
        form.addRow("Violent urge / round", self.turge)
        self.theal = _spin(0.0, 2.0, 0.05, cfg.get("violent_heal", swarm.VIOLENT_HEAL),
                           "Rating a violent member heals per point of damage it deals.")
        form.addRow("Violent heal / damage", self.theal)
        self.resr = QSpinBox(); self.resr.setRange(0, 50)
        self.resr.setValue(int(cfg.get("resilience_rounds", swarm.RESILIENCE_ROUNDS)))
        self.resr.setSpecialValueText("off")
        self.resr.setToolTip("Beginners Resilience: for rounds 1..N, all damage taken from "
                             "others' actions is reduced. 0 = off.")
        form.addRow("Beginners Resilience rounds", self.resr)
        self.resc = _spin(0.0, 1.0, 0.05, cfg.get("resilience_reduction", swarm.RESILIENCE_REDUCTION),
                          "Fraction of damage removed during Beginners Resilience.")
        form.addRow("Beginners Resilience reduction", self.resc)
        self.vsm = QCheckBox("VS mode: this is the BLUE team; a same-size RED team joins after "
                             "round 0 and defends the inverse claim")
        self.vsm.setChecked(cfg.get("vs_mode", False))
        form.addRow("", self.vsm)
        self.vsred = QComboBox()
        self._fill_red(cfg.get("vs_red_model", ""))
        form.addRow("Red team model", self.vsred)
        self.fdeb = QCheckBox("Forced debate: each round everyone must rebut an assigned member")
        self.fdeb.setChecked(cfg.get("forced_debate", True))
        form.addRow("", self.fdeb)
        self.cmts = QCheckBox("Public comments on each other's statements")
        self.cmts.setChecked(cfg.get("comments", True))
        form.addRow("", self.cmts)
        self.bstab = QCheckBox("Backstabs: members who dealt a lot of damage can be hit hard")
        self.bstab.setChecked(cfg.get("backstab", True))
        form.addRow("", self.bstab)
        self.bsthr = _spin(0.0, 100.0, 1.0, cfg.get("backstab_threshold", swarm.BACKSTAB_THRESHOLD),
                           "A member who has dealt MORE than this much damage can be backstabbed.")
        form.addRow("Backstab-able above (damage dealt)", self.bsthr)
        self.bsdmg = _spin(0.0, 20.0, 0.5, cfg.get("backstab_damage", swarm.BACKSTAB_DAMAGE),
                           "Damage one backstab deals.")
        form.addRow("Backstab damage", self.bsdmg)
        self.gviol = QCheckBox("Every game starts with at least one violent player")
        self.gviol.setChecked(cfg.get("guarantee_violent", True))
        form.addRow("", self.gviol)
        self.vconv = _spin(0.0, 1.0, 0.05, cfg.get("violent_convert", swarm.VIOLENT_CONVERT),
                           "Chance a violent member's steal/strike turns its victim violent.")
        form.addRow("Violence contagion chance", self.vconv)
        self.pstart = _spin(0.0, 10.0, 0.5, cfg.get("pacifist_start", swarm.PACIFIST_START),
                            "Pacifists start with this rating instead of 10.")
        form.addRow("Pacifist start rating", self.pstart)
        self.pheal = _spin(0.0, 3.0, 0.05, cfg.get("pacifist_heal", swarm.PACIFIST_HEAL),
                           "Rating a pacifist heals at the start of every round.")
        form.addRow("Pacifist heal / round", self.pheal)
        self.vsw = QCheckBox("Members may change disposition once (public)")
        self.vsw.setChecked(cfg.get("violent_switch", True))
        form.addRow("", self.vsw)
        self.vswc = _spin(0.0, 10.0, 0.5, cfg.get("violent_switch_cost", swarm.VIOLENT_SWITCH_COST),
                          "Rating paid to become violent.")
        form.addRow("Become-violent cost", self.vswc)
        self.pswc = _spin(0.0, 10.0, 0.5, cfg.get("pacifist_switch_cost", swarm.PACIFIST_SWITCH_COST),
                          "Rating paid to become a pacifist.")
        form.addRow("Become-pacifist cost", self.pswc)
        self.endg = QCheckBox("Endgame: no consensus → groups of any size, largest lives")
        self.endg.setChecked(cfg.get("endgame", True))
        form.addRow("", self.endg)
        self.endn = QSpinBox(); self.endn.setRange(1, 10)
        self.endn.setValue(cfg.get("endgame_rounds", swarm.ENDGAME_ROUNDS))
        self.endn.setToolTip("How many of the final rounds allow alliances of any size.")
        form.addRow("Endgame rounds", self.endn)
        self.rezp = QCheckBox("Players can resurrect dead members")
        self.rezp.setChecked(cfg.get("resurrect_player", True))
        form.addRow("", self.rezp)
        self.rezc = _spin(0.0, 10.0, 0.5, cfg.get("rez_cost", swarm.REZ_COST),
                          "Rating the resurrecting member pays (spent even if refused).")
        form.addRow("Resurrection cost", self.rezc)
        self.rezd = _spin(0.0, 1.0, 0.05, cfg.get("rez_decline", swarm.REZ_DECLINE_CHANCE),
                          "Chance a dead member who DECLINES comes back anyway (unaligned).")
        form.addRow("Return anyway chance", self.rezd)
        self.nuke = QCheckBox("Nuke (once per game per member)")
        self.nuke.setChecked(cfg.get("nuke", True))
        form.addRow("", self.nuke)
        self.ncost = _spin(0.0, 10.0, 0.5, cfg.get("nuke_cost", swarm.NUKE_COST),
                           "Rating the nuker pays.")
        form.addRow("Nuke cost (nuker pays)", self.ncost)
        self.nhit = _spin(0.0, 5.0, 0.25, cfg.get("nuke_hit", swarm.NUKE_HIT),
                          "Rating every OTHER member loses.")
        form.addRow("Nuke damage (everyone else)", self.nhit)
        self.ngain = _spin(0.0, 5.0, 0.25, cfg.get("nuke_gain", swarm.NUKE_GAIN),
                           "Rating the nuker gets back.")
        form.addRow("Nuke gain (nuker)", self.ngain)
        self.msgs = QCheckBox("Secret messages (one per player per round)")
        self.msgs.setChecked(cfg.get("messages", True))
        self.msgs.setToolTip("Delivered into the recipient's next turn; a reply takes one "
                             "more round. Only you can read them.")
        form.addRow("", self.msgs)
        self.mcost = _spin(0.0, 3.0, 0.05, cfg.get("message_cost", swarm.MESSAGE_COST),
                           "Rating the sender pays per message.")
        form.addRow("Message cost (sender)", self.mcost)
        self.gcost = _spin(0.0, 3.0, 0.05, cfg.get("group_cost", swarm.GROUP_MESSAGE_COST),
                           "Cost of a message to 2+ members at once (they all see who else "
                           "got it).")
        form.addRow("Group message cost", self.gcost)
        self.gmax = QSpinBox(); self.gmax.setRange(2, 8)
        self.gmax.setValue(int(cfg.get("group_max", swarm.GROUP_MAX)))
        form.addRow("Group message max recipients", self.gmax)
        self.sdmg = _spin(0.0, 10.0, 0.5, cfg.get("subvert_damage", swarm.SUBVERT_DAMAGE),
                          "Rating the target of a subversion loses (public but anonymous).")
        form.addRow("Subvert damage", self.sdmg)
        self.slim = QSpinBox(); self.slim.setRange(0, 20)
        self.slim.setValue(int(cfg.get("subvert_limit", swarm.SUBVERT_LIMIT)))
        self.slim.setToolTip("Subversions each member can launch per game. 0 turns subvert off.")
        form.addRow("Subverts per member per game", self.slim)
        self.smin = QSpinBox(); self.smin.setRange(2, 8)
        self.smin.setValue(int(cfg.get("subvert_min", swarm.SUBVERT_MIN_GROUP)))
        self.smin.setToolTip("A subversion rides on a group message with at least this many "
                             "recipients.")
        form.addRow("Subvert needs recipients", self.smin)
        self.lnc = _spin(0.0, 100.0, 0.5, cfg.get("lownuke_chance", swarm.LOWNUKE_CHANCE) * 100,
                         "PERCENT chance per round that a member below the line below has its "
                         "nuke go off by itself. 10 = 10%; 0.1 = a tenth of one percent.")
        form.addRow("Low-rating nuke chance (%/round)", self.lnc)
        self.lnl = _spin(1.0, 12.0, 0.5, cfg.get("lownuke_line", swarm.LOWNUKE_LINE),
                         "Members below this rating risk their nuke going off.")
        form.addRow("Low-rating nuke line", self.lnl)
        self.lnscale = _spin(0.0, 1.0, 0.05, cfg.get("lownuke_scale", swarm.LOWNUKE_SCALE),
                             "Fraction of the normal nuke damage an ACCIDENTAL nuke deals.")
        form.addRow("Accidental nuke damage", self.lnscale)
        self.charge = QCheckBox("Charge && strikes")
        self.charge.setChecked(cfg.get("charge", True))
        self.charge.setToolTip("Members above the charge line gain charge each round and can "
                               "spend it to strike another member (public).")
        form.addRow("", self.charge)
        self.cline = _spin(1.0, 12.0, 0.5, cfg.get("charge_line", swarm.CHARGE_LINE),
                           "A member must be ABOVE this rating to gain charge.")
        form.addRow("Charge line (rating above)", self.cline)
        self.cafter = QSpinBox(); self.cafter.setRange(0, 50)
        self.cafter.setValue(int(cfg.get("charge_after", swarm.CHARGE_AFTER)))
        self.cafter.setToolTip("Charge starts accruing after this round.")
        form.addRow("Charge starts after round", self.cafter)
        self.cmax = QSpinBox(); self.cmax.setRange(1, 10)
        self.cmax.setValue(int(cfg.get("charge_max", swarm.CHARGE_MAX)))
        form.addRow("Max charge (= strongest strike)", self.cmax)
        self.scap2 = QSpinBox(); self.scap2.setRange(1, 20)
        self.scap2.setValue(int(cfg.get("strike_cap", swarm.STRIKE_CAP)))
        self.scap2.setToolTip("The most any member can lose to strikes in one round.")
        form.addRow("Strike cap per victim/round", self.scap2)
        self.smiss = _spin(0.0, 100.0, 5.0, cfg.get("strike_miss", swarm.STRIKE_MISS) * 100,
                           "PERCENT chance a strike misses (charge spent, no damage).")
        form.addRow("Strike miss chance (%)", self.smiss)
        self.friendship = QCheckBox("Friendship tracker (3/3; 0/3 = must strike)")
        self.friendship.setChecked(cfg.get("friendship", True))
        self.friendship.setToolTip("Being struck or robbed lowers your friendship toward the "
                                   "attacker. At 0/3 you must strike them. Striking an ally "
                                   "ends the alliance and makes them enemies.")
        form.addRow("", self.friendship)
        self.retr = QCheckBox("Retribution votes (unanimous yes brings a target down)")
        self.retr.setChecked(cfg.get("retribution", True))
        form.addRow("", self.retr)
        self.rcost2 = _spin(0.0, 5.0, 0.5, cfg.get("retribution_cost", swarm.RETRIBUTION_COST),
                            "Rating the caller of a retribution vote pays.")
        form.addRow("Retribution call cost", self.rcost2)
        self.rto = _spin(1.0, 10.0, 0.5, cfg.get("retribution_to", swarm.RETRIBUTION_TO),
                         "A unanimous yes sets the target to this rating (only members above "
                         "it can be targeted).")
        form.addRow("Retribution brings target to", self.rto)
        self.thinking = QCheckBox("Private thinking (strategy notes)")
        self.thinking.setChecked(cfg.get("thinking", True))
        self.thinking.setToolTip("Each member writes private notes before its public reply. "
                                 "Only you see them. Costs extra tokens per reply.")
        form.addRow("", self.thinking)
        self.stok = QSpinBox(); self.stok.setRange(0, 1500); self.stok.setSingleStep(50)
        self.stok.setValue(cfg.get("strategy_tokens", swarm.STRATEGY_TOKENS))
        self.stok.setSpecialValueText("short (60 words)")
        self.stok.setToolTip("Tokens of private strategy each member writes per turn before "
                             "acting. More = better planned moves but slower turns.")
        form.addRow("Strategy tokens / turn", self.stok)
        self.death_lbl = QLabel()
        self.death_lbl.setWordWrap(True)
        form.addRow("Death", self.death_lbl)
        self.maxr.valueChanged.connect(self._death_label)
        self.rcost.valueChanged.connect(self._death_label)
        self.ratings.toggled.connect(self._death_label)
        self._death_label()
        host = QWidget()
        host.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        v.addWidget(scroll, 3)

        self.count_lbl = QLabel()
        v.addWidget(self.count_lbl)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self._count()

    def _sync_round_cost(self, *_):
        """With a round limit the per-round damage is (10 / rounds) * 0.5, computed for you."""
        R = self.maxr.value()
        if R > 0:
            self.rcost.setValue(round(swarm.START_RATING / R * swarm.ROUND_COST_FACTOR, 3))
            self.rcost.setEnabled(False)
            self.rcost.setToolTip(f"Automatic: (total health {swarm.START_RATING:g} / {R} rounds) "
                                  f"x {swarm.ROUND_COST_FACTOR:g}.")
        else:
            self.rcost.setEnabled(True)
            self.rcost.setToolTip("Rating lost by every member each debate round (used only "
                                  "when there is no round limit).")

    def _death_label(self, *_):
        """Death is always at rating 0, nothing else."""
        if not self.ratings.isChecked():
            txt = "Off (needs ratings on)."
        else:
            txt = ("A member dies when their rating hits 0 — always, whatever the number of "
                   "rounds. Each round costs rating, so members who gain nothing wear down "
                   "to zero.")
        self.death_lbl.setText(txt)
        self.death_lbl.setStyleSheet("color:#98A1AE;font-size:12px;")

    # ---- model lists: one clean list per backend (value = what the engine receives)
    def _opts(self, backend):
        """[(label, value)] for a backend: only that backend's models, smallest first."""
        if backend == "api":
            return [(f"{m}  ·  OpenAI", swarm.API_PREFIX + m) for m in swarm.API_MODELS]
        pref = swarm.LLAMA_PREFIX if backend == "llama" else ""
        out = []
        for n in self.base_models:
            size = self.sizes.get(n)
            label = n + (f"  ·  {size / 1e9:.1f} GB" if size else "")
            if size and size / 1e9 > 7.0:
                label += ("  (too big for 8 GB)" if backend == "llama"
                          else "  (spills to CPU, slow)")
            out.append((label, pref + n))
        return out

    def _fill(self, combo, backend, keep=None):
        """Replace a combo's items with the backend's models; keep `keep` if it is not listed."""
        combo.blockSignals(True)
        combo.clear()
        for label, value in self._opts(backend):
            combo.addItem(label, value)
        if keep and combo.findData(keep) < 0:
            combo.addItem(f"{base_name(keep)}  (custom)", keep)
        combo.blockSignals(False)

    def _default_for(self, backend, prefer=()):
        """The model to use when nothing sensible is chosen: the first of `prefer` that exists
        in this backend (by bare name), else the default model, else the first listed."""
        opts = [v for _l, v in self._opts(backend)]
        pref = {"llama": swarm.LLAMA_PREFIX, "api": swarm.API_PREFIX}.get(backend, "")
        for p in prefer:
            if pref + base_name(p) in opts:
                return pref + base_name(p)
        d = pref + (swarm.API_DEFAULT if backend == "api" else swarm.DEFAULT_MODEL)
        return d if d in opts else (opts[0] if opts else d)

    def _fill_judge(self, current=""):
        """Judge: the members' own model, a cloud model, or (Ollama games) any local model."""
        self.judge.blockSignals(True)
        self.judge.clear()
        self.judge.addItem("(same model as the members)", "")
        for label, value in self._opts("api"):
            self.judge.addItem(label, value)
        if self.backend == "ollama":
            for label, value in self._opts("ollama"):
                self.judge.addItem(label, value)
        if current and self.judge.findData(current) < 0:
            self.judge.addItem(f"{base_name(current)}  (custom)", current)
        self.judge.blockSignals(False)
        self.judge.setCurrentIndex(max(0, self.judge.findData(current)))

    def _fill_red(self, current=""):
        self.vsred.blockSignals(True)
        self.vsred.clear()
        self.vsred.addItem("(same as the Blue team)", "")
        for label, value in self._opts(self.backend):
            self.vsred.addItem(label, value)
        self.vsred.blockSignals(False)
        i = self.vsred.findData(current)
        self.vsred.setCurrentIndex(max(0, i))

    @staticmethod
    def _select(combo, value):
        """Select by value (falls back to the visible text)."""
        i = combo.findData(value)
        if i < 0:
            i = combo.findText(value)
        if i >= 0:
            combo.setCurrentIndex(i)

    def _names(self):
        return [self.table.item(r, 0).text().strip() for r in range(self.table.rowCount())]

    def _add_row(self, name, model, temp):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name))
        combo = QComboBox()
        self._fill(combo, self.backend, keep=model)
        self._select(combo, model)
        combo.setEnabled(self.backend != "llama")     # a llama.cpp server runs ONE model
        self.table.setCellWidget(r, 1, combo)
        sp = QDoubleSpinBox(); sp.setRange(0.0, 2.0); sp.setSingleStep(0.1)
        sp.setValue(temp)
        self.table.setCellWidget(r, 2, sp)
        rm = QPushButton("✕")
        rm.setFixedWidth(32)
        rm.setToolTip("Remove this member")
        rm.clicked.connect(lambda _=False, b=rm: self._remove(b))
        self.table.setCellWidget(r, 3, rm)
        self._count()

    def _remove(self, btn):
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 3) is btn:
                self.table.removeRow(r)
                break
        self._count()

    def _add_clicked(self):
        for _ in range(self.add_n.value()):
            self._add_row(swarm.unique_name(self._names()),
                          self.add_model.currentData() or self.add_model.currentText(), 0.8)
        self._count()

    def _ok(self):
        k = self.key_edit.text().strip()
        if k:
            try:
                swarm.save_api_key(k)
            except OSError:
                pass
        self.accept()

    def _key_label(self):
        mode = self.provider.currentData()
        self.key_row.setVisible(mode == "api")
        if mode == "llama":
            ok = llama_local.available()
            self.key_lbl.setText("llama.cpp found · one model per game" if ok else
                                 f"⚠ llama-server.exe not found in {llama_local.llama_dir()}")
            self.key_lbl.setStyleSheet("color:%s" % ("#5FB88A" if ok else "#E0A85F"))
            return
        if mode == "ollama":
            n = len(self.base_models)
            self.key_lbl.setText(f"{n} model{'s' if n != 1 else ''} installed" if self.sizes else
                                 "⚠ Ollama not reachable (is it running?)")
            self.key_lbl.setStyleSheet("color:%s" % ("#5FB88A" if self.sizes else "#E0A85F"))
            return
        ok = bool(swarm.api_key() or self.key_edit.text().strip())
        self.key_lbl.setText("API key found" if ok else
                             "⚠ no API key (set OPENAI_API_KEY or openai_key.txt)")
        self.key_lbl.setStyleSheet("color:%s" % ("#5FB88A" if ok else "#E0A85F"))

    def _provider_changed(self, *_):
        """Switch every member (and the judge) between local models and the OpenAI API."""
        mode = self.provider.currentData()
        old = [self.table.cellWidget(r, 1).currentData() or "" for r in range(self.table.rowCount())]
        was = self.all_model.currentData() or ""
        self.backend = mode
        # every list now shows only this backend's models; members keep the same bare model when
        # this backend has it, otherwise they get the backend's default
        self._fill(self.add_model, mode)
        self._select(self.add_model, self._default_for(mode, prefer=[was] + old))
        self._fill(self.all_model, mode)
        self._select(self.all_model, self._default_for(mode, prefer=[was] + old))
        valid = {v for _l, v in self._opts(mode)}
        for r, o in enumerate(old):
            combo = self.table.cellWidget(r, 1)
            want = ({"llama": swarm.LLAMA_PREFIX, "api": swarm.API_PREFIX}.get(mode, "")
                    + base_name(o))
            self._fill(combo, mode)
            self._select(combo, want if want in valid else self._default_for(mode))
            combo.setEnabled(mode != "llama")
        if mode == "llama":
            self._set_all()
        self._fill_judge("")
        self._fill_red("")
        self._key_label()

    def _set_all(self):
        m = self.all_model.currentData() or self.all_model.currentText()
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 1)
            if combo.findData(m) < 0:
                combo.addItem(f"{base_name(m)}  (custom)", m)
            self._select(combo, m)

    def _spread(self):
        n = self.table.rowCount()
        for r, t in enumerate(swarm.spread_temps(n)):
            self.table.cellWidget(r, 2).setValue(t)

    def _count(self):
        if not hasattr(self, "count_lbl"):      # rows are added before the label exists
            return
        n = self.table.rowCount()
        self.count_lbl.setText(
            f"{n} member{'s' if n != 1 else ''}"
            + ("" if n >= 2 else " — need at least 2 for a debate"))

    def result_cfg(self, enabled):
        members, seen = [], set()
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).text().strip() or swarm.unique_name(seen)
            while name in seen:               # names are how members address each other
                name += "2"
            seen.add(name)
            members.append({"name": name,
                            "model": (self.table.cellWidget(r, 1).currentData()
                                      or self.table.cellWidget(r, 1).currentText()),
                            "temp": round(self.table.cellWidget(r, 2).value(), 2)})
        return {"enabled": enabled, "members": members,
                "judge_model": self.judge.currentData() or "",
                "parallel": self.parallel.value(), "max_rounds": self.maxr.value(),
                "num_ctx": self.ctx.value(), "ratings": self.ratings.isChecked(),
                "round_cost": round(self.rcost.value(), 2),
                "change_cost": round(self.ccost.value(), 2),
                "economy": self.economy.isChecked(), "gift_ratio": round(self.gratio.value(), 2),
                "form_cost": round(self.fcost.value(), 2),
                "leave_cost": round(self.lcost.value(), 2),
                "leave_harm": round(self.lharm.value(), 2),
                "ally_decay": round(self.adecay.value(), 2),
                "controversy_buff": round(self.cbuff.value(), 2),
                "controversy_debuff": round(self.cdebuff.value(), 2),
                "align_buff": round(self.abuff.value(), 2),
                "assign_chance": round(self.cdice.value(), 2),
                "resurrect_chance": round(self.rchance.value(), 2),
                "resurrect_bonus": round(self.rbonus.value(), 2),
                "alliance_open_round": self.aopen.value(),
                "steal": self.steal.isChecked(), "steal_amount": round(self.sam.value(), 2),
                "steal_cap": round(self.scap.value(), 2), "steal_force": round(self.sforce.value(), 2),
                "temperament": self.temp.isChecked(),
                "pacifist_chance": round(self.tpac.value(), 3),
                "violent_chance": round(self.tvio.value(), 3),
                "violent_urge": round(self.turge.value(), 3),
                "violent_heal": round(self.theal.value(), 3),
                "lownuke_scale": round(self.lnscale.value(), 3),
                "resilience_rounds": self.resr.value(),
                "resilience_reduction": round(self.resc.value(), 3),
                "forced_debate": self.fdeb.isChecked(), "comments": self.cmts.isChecked(),
                "vs_mode": self.vsm.isChecked(), "vs_red_model": self.vsred.currentData() or "",
                "backstab": self.bstab.isChecked(),
                "backstab_threshold": round(self.bsthr.value(), 2),
                "backstab_damage": round(self.bsdmg.value(), 2),
                "guarantee_violent": self.gviol.isChecked(),
                "violent_convert": round(self.vconv.value(), 3),
                "pacifist_start": round(self.pstart.value(), 2),
                "pacifist_heal": round(self.pheal.value(), 3),
                "violent_switch": self.vsw.isChecked(),
                "violent_switch_cost": round(self.vswc.value(), 2),
                "pacifist_switch_cost": round(self.pswc.value(), 2),
                "endgame": self.endg.isChecked(), "endgame_rounds": self.endn.value(),
                "resurrect_player": self.rezp.isChecked(), "rez_cost": round(self.rezc.value(), 2),
                "rez_decline": round(self.rezd.value(), 2), "nuke": self.nuke.isChecked(),
                "nuke_cost": round(self.ncost.value(), 2), "nuke_hit": round(self.nhit.value(), 2),
                "nuke_gain": round(self.ngain.value(), 2), "messages": self.msgs.isChecked(),
                "message_cost": round(self.mcost.value(), 2),
                "group_cost": round(self.gcost.value(), 2), "group_max": self.gmax.value(),
                "subvert_damage": round(self.sdmg.value(), 2),
                "subvert_limit": self.slim.value(), "subvert_min": self.smin.value(),
                "lownuke_chance": round(self.lnc.value() / 100.0, 5),
                "lownuke_line": round(self.lnl.value(), 2),
                "charge": self.charge.isChecked(), "charge_line": round(self.cline.value(), 2),
                "charge_after": self.cafter.value(), "charge_max": self.cmax.value(),
                "strike_cap": self.scap2.value(),
                "strike_miss": round(self.smiss.value() / 100.0, 4),
                "friendship": self.friendship.isChecked(),
                "ally_bonus": round(self.abonus.value(), 2),
                "ally_income": round(self.aincome.value(), 3),
                "group_heal": round(self.gheal.value(), 3),
                "group_income": round(self.gincome.value(), 3),
                "retribution": self.retr.isChecked(),
                "retribution_cost": round(self.rcost2.value(), 2),
                "retribution_to": round(self.rto.value(), 2),
                "strategy_tokens": self.stok.value(),
                "thinking": self.thinking.isChecked()}
