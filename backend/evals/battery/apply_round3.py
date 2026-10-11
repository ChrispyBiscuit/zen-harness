"""Round-3 fixes (findings 41-44) — applied only after after3 reaches 200 tasks, so the final measurement stays clean."""
import os

APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def patch(fn, pairs):
    p = os.path.join(APP, fn)
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert s.count(a) == 1, (fn, s.count(a), a[:70])
        s = s.replace(a, b)
    open(p, "w", encoding="utf-8", newline="").write(s)
    print("patched", fn)


# 41/44: list_dir — names first and unambiguous, plus counts by type (the model read "f a.pdf" as a file name,
# and miscounted six .csv files as seven)
patch("agent_tools.py", [(
    '''    rows = []
    for name in sorted(os.listdir(p), key=str.lower)[:400]:
        fp = os.path.join(p, name)
        try:
            st = os.stat(fp)
            rows.append(("d " if os.path.isdir(fp) else "f ") + name
                        + ("" if os.path.isdir(fp) else f"  ({st.st_size} bytes)"))
        except OSError:
            rows.append("? " + name)
    return f"{p}\\n" + "\\n".join(rows)''',
    '''    rows, kinds, nfiles, ndirs = [], {}, 0, 0
    names = sorted(os.listdir(p), key=str.lower)
    for name in names[:400]:
        fp = os.path.join(p, name)
        try:
            if os.path.isdir(fp):
                rows.append(f"{name}/    (folder)")
                ndirs += 1
            else:
                rows.append(f"{name}    ({os.stat(fp).st_size:,} bytes)")
                nfiles += 1
                ext = os.path.splitext(name)[1].lower() or "(no extension)"
                kinds[ext] = kinds.get(ext, 0) + 1
        except OSError:
            rows.append(f"{name}    (unreadable)")
    summary = (f"{nfiles} file{'s' if nfiles != 1 else ''}"
               + (" (" + ", ".join(f"{n} {e}" for e, n in sorted(kinds.items(), key=lambda x: -x[1])) + ")" if kinds else "")
               + f", {ndirs} folder{'s' if ndirs != 1 else ''}"
               + (f" — showing the first 400 of {len(names)}" if len(names) > 400 else ""))
    return f"{p}\\n{summary}\\n" + "\\n".join(rows)''')])

patch("agentic.py", [
    # 42: the basic budget counts model STEPS (parallel calls in one step = 1), not calls — moving 6 files is one step
    ('''        elif (self._basic and name not in BASIC_FREE_TOOLS and self._basic_calls >= self._basic_budget()):''',
     '''        elif (self._basic and name not in BASIC_FREE_TOOLS and self._basic_calls >= self._basic_budget()
              and getattr(self, "_basic_step_mark", None) != self.turn.get("steps")):'''),
    ('''            basic_block = (f"{name} did not run: the basic path's {self._basic_budget()} tool calls are used up. Answer "''',
     '''            basic_block = (f"{name} did not run: the basic path's {self._basic_budget()} steps are used up. Answer "'''),
    ('''                if self._basic and name not in BASIC_FREE_TOOLS:
                    self._basic_calls += 1''',
     '''                if self._basic and name not in BASIC_FREE_TOOLS and \\
                        getattr(self, "_basic_step_mark", None) != self.turn.get("steps"):
                    self._basic_calls += 1                  # one per step: parallel calls in a step count once
                    self._basic_step_mark = self.turn.get("steps")'''),
    ('''"parallel workers (gather is not available here). At most {budget} tool calls; {left} left. Independent "''',
     '''"parallel workers (gather is not available here). At most {budget} steps; {left} left (several tool calls in "
              "one step count once). Independent "'''),
    # 43: different targets are different attempts (make_dir of four different folders was "the same attempt")
    ('''        toks = set(self._TOKEN_RX.findall((name + " " + json.dumps(args, default=str)).lower()))
        win = self.__dict__.setdefault("_attempts", collections.deque(maxlen=16))
        same = [w for w in win if w[0] == name and w[2] == seq and toks and
                len(toks & w[1]) / max(1, len(toks | w[1])) >= 0.7]
        win.append((name, toks, seq))''',
     '''        toks = set(self._TOKEN_RX.findall((name + " " + json.dumps(args, default=str)).lower()))
        target = ""                                   # what the call acts on: a different one = a different attempt
        if isinstance(args, dict):
            target = " ".join(str(args.get(k) or "") for k in ("path", "src", "dst", "url", "urls", "file", "id")).lower()
        win = self.__dict__.setdefault("_attempts", collections.deque(maxlen=16))
        same = [w for w in win if w[0] == name and w[2] == seq and toks and (w[3] == target or not target) and
                len(toks & w[1]) / max(1, len(toks | w[1])) >= 0.7]
        win.append((name, toks, seq, target))'''),
])
print("round 3 applied")
