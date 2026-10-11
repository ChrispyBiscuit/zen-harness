"""Scheduled agent tasks: run a prompt on a schedule, each run in its own new conversation.

  when:  "daily 08:00" · "weekdays 8:30am" · "every monday at 9am" · "mon,wed,fri 09:00" · "daily 08:00 and 17:30"
         · "every 3 days at 07:00" · "monthly 1 09:00" · "15th of every month at 9am" · "monthly last 17:00"
         · "every 2h" · "every 45m" · "hourly" · "once 2026-10-09 14:00"
Stored in <data>/schedules.json. A checker thread starts due tasks through the start(task) callback.
"""
import datetime as dt
import json
import os
import re
import threading
import time
import uuid

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(os.environ.get("LWS_DATA_DIR") or APP_DIR, "schedules.json")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_lock = threading.RLock()


class ScheduleError(Exception):
    pass


_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
_EXAMPLES = ("use a schedule like 'daily 08:00', 'weekdays 8:30am', 'every monday at 9am', 'mon,thu 18:00', "
             "'daily 08:00 and 17:30', 'every 3 days at 07:00', 'monthly 1 09:00', '15th of every month at 9am', "
             "'every 2h', 'every 45m' or 'once 2026-10-09 14:00'")


def _times(text):
    """'8am, 12:30 and 5pm' -> ['08:00', '12:30', '17:00'] (sorted, unique); raises ScheduleError."""
    out = []
    for part in re.split(r"\s*(?:,|\band\b|&)\s*", text.strip()):
        if not part:
            continue
        m = re.fullmatch(_TIME, part)
        if not m:
            raise ScheduleError(f"can't read the time '{part}'; " + _EXAMPLES)
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and h < 12:
            h += 12
        if m.group(3) == "am" and h == 12:
            h = 0
        if not (0 <= h < 24 and 0 <= mi < 60):
            raise ScheduleError("times must be like 08:00, 8:30am or 5pm")
        out.append(f"{h:02d}:{mi:02d}")
    if not out:
        raise ScheduleError(_EXAMPLES)
    return sorted(set(out))


def _days(spec):
    spec = spec.strip()
    if spec in ("daily", "every day", "each day", "day", "days"):
        return list(range(7))
    if spec in ("weekdays", "weekday", "every weekday", "workdays"):
        return list(range(5))
    if spec in ("weekends", "weekend", "every weekend"):
        return [5, 6]
    days = []
    for tok in re.split(r"[,\s]+|\band\b", spec):
        tok = tok.strip()
        if not tok or tok in ("every", "each", "on"):
            continue
        if tok[:3] not in DAYS:
            raise ScheduleError(f"unknown day '{tok}'; " + _EXAMPLES)
        days.append(DAYS.index(tok[:3]))
    if not days:
        raise ScheduleError(_EXAMPLES)
    return sorted(set(days))


def parse(when):
    """-> normalized rule dict; raises ScheduleError with examples on bad input.
    kinds: interval {minutes} · once {at} · weekly {days, times} · ndays {every, times, start} · monthly {day, times}
    (day -1 = last day of the month)."""
    w = " ".join(str(when or "").lower().replace(" at ", " ").replace("o'clock", "").split())
    w = re.sub(r"^(?:at|on)\s+", "", w)
    m = re.fullmatch(r"(?:every|each)\s+(\d+)?\s*(m|min|mins|minutes?|h|hr|hrs|hours?)", w) or \
        re.fullmatch(r"(hourly)()", w)
    if m:
        if m.group(1) == "hourly":
            return {"kind": "interval", "minutes": 60}
        n = int(m.group(1) or 1) * (60 if m.group(2).startswith("h") else 1)
        if n < 5:
            raise ScheduleError("the shortest interval is every 5 minutes")
        return {"kind": "interval", "minutes": n}
    m = re.fullmatch(r"once (\d{4}-\d{2}-\d{2})[ t](\d{1,2}):(\d{2})", w)
    if m:
        return {"kind": "once", "at": f"{m.group(1)} {int(m.group(2)):02d}:{m.group(3)}"}
    m = re.fullmatch(r"(?:every|each) (\d+) days? (.+)", w)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ScheduleError("every N days needs N of 1 or more")
        return {"kind": "ndays", "every": n, "times": _times(m.group(2)), "start": dt.date.today().isoformat()}
    m = re.fullmatch(r"monthly (?:on )?(?:the )?(\d{1,2}|last)(?:st|nd|rd|th)?(?: day)? (.+)", w) or \
        re.fullmatch(r"(?:on )?(?:the )?(\d{1,2}|last)(?:st|nd|rd|th)?(?: day)? of (?:every|each|the) month (.+)", w)
    if m:
        day = -1 if m.group(1) == "last" else int(m.group(1))
        if day != -1 and not 1 <= day <= 31:
            raise ScheduleError("the day of the month must be 1-31 or 'last'")
        return {"kind": "monthly", "day": day, "times": _times(m.group(2))}
    m = re.fullmatch(r"(daily|every day|each day|(?:every )?weekdays?|workdays|(?:every )?weekends?|[a-z,\s]+?) "
                     r"(" + _TIME + r"(?:\s*(?:,|and|&)\s*" + _TIME + r")*)", w)
    if m:
        return {"kind": "weekly", "days": _days(m.group(1)), "times": _times(m.group(2))}
    # time first: "7am daily", "9:00 on weekdays", "6pm every monday and thursday"
    m = re.fullmatch(r"(" + _TIME + r"(?:\s*(?:,|and|&)\s*" + _TIME + r")*) (?:on |every |each )?(.+)", w)
    if m and not re.search(r"\d", m.group(m.lastindex)):
        return {"kind": "weekly", "days": _days(m.group(m.lastindex)), "times": _times(m.group(1))}
    raise ScheduleError(_EXAMPLES)


def _rule_times(rule):
    return rule.get("times") or ([rule["time"]] if rule.get("time") else [])


def _month_day(year, month, day):
    import calendar
    last = calendar.monthrange(year, month)[1]
    return last if day == -1 else min(day, last)


def next_run(rule, after=None):
    now = after or dt.datetime.now()
    kind = rule["kind"]
    if kind == "interval":
        return now + dt.timedelta(minutes=rule["minutes"])
    if kind == "once":
        t = dt.datetime.strptime(rule["at"], "%Y-%m-%d %H:%M")
        return t if t > now else None
    times = [tuple(map(int, t.split(":"))) for t in _rule_times(rule)]
    cands = []
    if kind == "weekly":
        for add in range(8):
            d = now.date() + dt.timedelta(days=add)
            if d.weekday() in rule["days"]:
                cands += [dt.datetime.combine(d, dt.time(h, mi)) for h, mi in times]
    elif kind == "ndays":
        start = dt.date.fromisoformat(rule.get("start") or now.date().isoformat())
        for add in range(rule["every"] + 2):
            d = now.date() + dt.timedelta(days=add)
            if (d - start).days % rule["every"] == 0:
                cands += [dt.datetime.combine(d, dt.time(h, mi)) for h, mi in times]
    elif kind == "monthly":
        y, mo = now.year, now.month
        for _ in range(3):
            d = dt.date(y, mo, _month_day(y, mo, rule["day"]))
            cands += [dt.datetime.combine(d, dt.time(h, mi)) for h, mi in times]
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    later = sorted(c for c in cands if c > now)
    return later[0] if later else None


def _ordinal(n):
    return "last day" if n == -1 else f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def describe(rule):
    kind = rule["kind"]
    if kind == "interval":
        m = rule["minutes"]
        return "hourly" if m == 60 else f"every {m // 60}h" if m % 60 == 0 else f"every {m} min"
    if kind == "once":
        return f"once at {rule['at']}"
    at = " and ".join(_rule_times(rule))
    if kind == "ndays":
        return f"every {rule['every']} days at {at}"
    if kind == "monthly":
        return f"monthly on the {_ordinal(rule['day'])} at {at}"
    d = rule["days"]
    days = "daily" if len(d) == 7 else "weekdays" if d == [0, 1, 2, 3, 4] else "weekends" if d == [5, 6] \
        else ",".join(DAYS[i] for i in d)
    return f"{days} at {at}"


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(items):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)
    os.replace(tmp, PATH)


def all_tasks():
    with _lock:
        return _load()


def add(name, prompt, when, created_by="user"):
    rule = parse(when)
    nxt = next_run(rule)
    if nxt is None:
        raise ScheduleError("that time is already in the past")
    item = {"id": uuid.uuid4().hex[:8], "name": (name or prompt[:40]).strip()[:80], "prompt": prompt.strip(),
            "when": describe(rule), "rule": rule, "enabled": True, "created": time.time(), "created_by": created_by,
            "next_run": nxt.timestamp(), "last_run": None, "last_conv": None, "runs": 0}
    with _lock:
        items = _load()
        items.append(item)
        _save(items)
    return item


def update(task_id, **changes):
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == task_id:
                if "when" in changes:
                    it["rule"] = parse(changes.pop("when"))
                    it["when"] = describe(it["rule"])
                    nxt = next_run(it["rule"])
                    it["next_run"] = nxt.timestamp() if nxt else None
                it.update({k: v for k, v in changes.items() if k in ("name", "prompt", "enabled")})
                if it.get("enabled") and not it.get("next_run"):
                    nxt = next_run(it["rule"])
                    it["next_run"] = nxt.timestamp() if nxt else None
                _save(items)
                return it
    raise ScheduleError(f"no scheduled task '{task_id}'")


def delete(task_id):
    with _lock:
        items = _load()
        keep = [i for i in items if i["id"] != task_id]
        if len(keep) == len(items):
            raise ScheduleError(f"no scheduled task '{task_id}'")
        _save(keep)


def loop(start, busy=lambda: False):
    """start(task) -> conv id. Checks every 20 s; a task due while the app was closed runs once when it opens."""
    while True:
        time.sleep(20)
        try:
            now = time.time()
            due = []
            with _lock:
                items = _load()
                for it in items:
                    if it.get("enabled") and it.get("next_run") and it["next_run"] <= now:
                        due.append(dict(it))
                        nxt = next_run(it["rule"])
                        it["next_run"] = nxt.timestamp() if nxt else None
                        if it["rule"]["kind"] == "once":
                            it["enabled"] = False
                        it["last_run"] = now
                        it["runs"] = it.get("runs", 0) + 1
                if due:
                    _save(items)
            for it in due:
                try:
                    conv_id = start(it)
                    update_last_conv(it["id"], conv_id)
                except Exception as e:
                    print("scheduled task failed to start:", e, flush=True)
        except Exception as e:
            print("scheduler error:", e, flush=True)


def update_last_conv(task_id, conv_id):
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == task_id:
                it["last_conv"] = conv_id
        _save(items)
