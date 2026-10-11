"""Exact calculator for the agent: arithmetic, algebra, calculus, units and dates.

calculate(expression, digits=15) -> dict(result, decimal, latex, kind)

  2^100 / 3                      exact rational + decimal
  sqrt(2)*pi  (digits=50)        arbitrary precision
  x^2 - 5x + 6 = 0               an equation: solved for its variable
  solve([x+y-3, x-y-1], [x,y])   systems
  diff(sin(x)*x^2, x) · integrate(exp(-x^2), (x, -oo, oo)) · limit(sin(x)/x, x, 0) · series(cos(x), x, 0, 6)
  Matrix([[1,2],[3,4]]).det()    matrices
  mean([3,5,9]) · median(...) · stdev(...)
  65 mph to m/s · 3 kg * 9.81 m/s^2 to N · 72 degF to degC     units (Pint)
  days between 2026-03-03 and 2026-06-09 · business days between ... · 2026-10-07 + 90 days · now in Tokyo
"""
import datetime as dt
import re
import statistics

import sympy as sp
from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application, parse_expr,
                                        standard_transformations)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
_FORBIDDEN = re.compile(r"__|\b(import|exec|eval|open|compile|globals|locals|getattr|setattr|delattr|lambda|os|sys|"
                        r"subprocess|input|breakpoint)\b|[;`\\]")


class CalcError(Exception):
    pass


def _ns():
    ns = {k: getattr(sp, k) for k in dir(sp) if not k.startswith("_")}
    for name in "abcdfghjklmnpqrstuvwxyz":       # single letters are symbols (E, I, N, O, S, Q are sympy names)
        ns[name] = sp.Symbol(name)
    ns.update(e=sp.E, i=sp.I, inf=sp.oo, infinity=sp.oo, ln=sp.log, arcsin=sp.asin, arccos=sp.acos,
              arctan=sp.atan, mean=_stat(statistics.mean), median=_stat(statistics.median),
              stdev=_stat(statistics.stdev), pstdev=_stat(statistics.pstdev), variance=_stat(statistics.variance),
              mode=_stat(statistics.mode), percent=lambda v: sp.Rational(1, 100) * v)
    return ns


def _stat(fn):
    def f(*vals):
        data = list(vals[0]) if len(vals) == 1 and isinstance(vals[0], (list, tuple, sp.Tuple)) else list(vals)
        return sp.nsimplify(fn([sp.Rational(str(v)) if not isinstance(v, sp.Basic) else v for v in data]))
    return f


def _fmt(value, digits):
    """-> (exact text, decimal text or None, latex)."""
    if isinstance(value, dict):
        exact = ", ".join(f"{k} = {v}" for k, v in value.items())
        dec = ", ".join(f"{k} ≈ {_num(v, digits)}" for k, v in value.items() if _num(v, digits))
        latex = r",\; ".join(f"{sp.latex(k)} = {sp.latex(v)}" for k, v in value.items())
        return exact, dec or None, latex
    if isinstance(value, (list, tuple)):
        items = [_fmt(v, digits) for v in value]
        exact = "[" + ", ".join(i[0] for i in items) + "]"
        decs = [i[1] for i in items if i[1]]
        dec = ("[" + ", ".join(decs) + "]") if decs and len(decs) == len(items) else None
        latex = r"\left[" + r",\; ".join(i[2] for i in items) + r"\right]"
        return exact, dec, latex
    if isinstance(value, bool) or value in (sp.true, sp.false):
        return str(bool(value)), None, str(bool(value))
    if not isinstance(value, sp.Basic):
        try:
            value = sp.sympify(value)
        except Exception:
            return str(value), None, str(value)
    return str(value), _num(value, digits), sp.latex(value)


def _num(v, digits):
    try:
        if isinstance(v, sp.Basic) and v.is_number and not v.is_Integer:
            n = sp.N(v, digits)
            s = str(n)
            return s if s != str(v) else None
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ units
_UREG = None
_UNIT_RX = re.compile(r"^(?P<expr>.+?)\s+(?:to|in|as|into)\s+(?P<unit>[A-Za-z°µ/*^·\s\d.\-()]+)$")


def _ureg():
    global _UREG
    if _UREG is None:
        import pint
        _UREG = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
        _UREG.formatter.default_format = "~P"
    return _UREG


def _units(expr, digits):
    u = _ureg()
    m = _UNIT_RX.match(expr.strip())
    try:
        q = u.parse_expression((m.group("expr") if m else expr).replace("°", "deg").replace("^", "**"))
        if m:
            q = q.to(m.group("unit").strip().replace("°", "deg").replace("^", "**"))
        elif hasattr(q, "to_compact"):
            q = q.to_compact()
    except Exception as e:
        raise CalcError(f"unit conversion failed: {e}")
    if not hasattr(q, "magnitude"):
        raise CalcError("not a quantity with units")
    mag = q.magnitude
    txt = f"{float(mag):.{min(digits, 12)}g} {q.units:~P}".strip()
    return {"kind": "units", "result": txt, "decimal": None, "latex": f"{sp.latex(sp.Float(mag, min(digits, 15)))}\\ \\mathrm{{{q.units:~}}}"}


# ------------------------------------------------------------------ dates
_DATE = r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?|today|now|tomorrow|yesterday)"


def _date(s):
    s = s.strip().lower()
    today = dt.date.today()
    if s in ("today", "now"):
        return dt.datetime.now() if s == "now" else dt.datetime.combine(today, dt.time())
    if s == "tomorrow":
        return dt.datetime.combine(today + dt.timedelta(days=1), dt.time())
    if s == "yesterday":
        return dt.datetime.combine(today - dt.timedelta(days=1), dt.time())
    return dt.datetime.fromisoformat(s.replace(" ", "T"))


def _dates(expr):
    e = expr.strip()
    low = e.lower()
    m = re.match(rf"^(business |working |week)?days? (?:between|from) {_DATE} (?:and|to|until) {_DATE}$", low)
    if m:
        a, b = _date(m.group(2)), _date(m.group(3))
        if m.group(1):
            import numpy as np
            n = int(np.busday_count(a.date(), b.date()))
            return {"kind": "dates", "result": f"{n} business days (Mon–Fri, end date excluded)", "decimal": None, "latex": str(n)}
        d = b - a
        weeks = d.days / 7
        return {"kind": "dates", "result": f"{d.days} days ({weeks:.2f} weeks)" + (f", {d}" if d.seconds else ""),
                "decimal": None, "latex": str(d.days)}
    m = re.match(rf"^{_DATE}\s*([+-])\s*(\d+)\s*(business days?|days?|weeks?|months?|years?|hours?|minutes?)$", low)
    if m:
        base, sign, n, unit = _date(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        n = -n if sign == "-" else n
        if unit.startswith("business"):
            import numpy as np
            out = dt.datetime.combine(np.busday_offset(base.date(), n, roll="forward").astype(dt.date), dt.time())
        elif unit.startswith(("month", "year")):
            months = n * (12 if unit.startswith("year") else 1)
            y, mo = divmod(base.month - 1 + months, 12)
            import calendar
            day = min(base.day, calendar.monthrange(base.year + y, mo + 1)[1])
            out = base.replace(year=base.year + y, month=mo + 1, day=day)
        else:
            key = {"d": "days", "w": "weeks", "h": "hours", "m": "minutes"}[unit[0]]
            out = base + dt.timedelta(**{key: n})
        txt = out.strftime("%A %Y-%m-%d" + (" %H:%M" if out.time() != dt.time() else ""))
        return {"kind": "dates", "result": txt, "decimal": None, "latex": out.strftime("%Y\\text{-}%m\\text{-}%d")}
    m = re.match(r"^(?:time |now )?(?:time )?in ([\w/ _-]+)$|^now in ([\w/ _-]+)$", low)
    if m:
        place = (m.group(1) or m.group(2)).strip()
        tz = _tz(place)
        now = dt.datetime.now(tz)
        return {"kind": "dates", "result": now.strftime(f"%A %Y-%m-%d %H:%M ({tz.key}, UTC%z)"), "decimal": None,
                "latex": now.strftime("%H{:}%M")}
    m = re.match(rf"^(?:weekday of |day of week (?:of|for) )?{_DATE}$", low)
    if m and m.group(1) not in ("now",):
        d = _date(m.group(1))
        return {"kind": "dates", "result": d.strftime("%A %Y-%m-%d (day %j of the year, ISO week %V)"),
                "decimal": None, "latex": d.strftime("%Y\\text{-}%m\\text{-}%d")}
    return None


_TZ_ALIASES = {"tokyo": "Asia/Tokyo", "london": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
               "new york": "America/New_York", "nyc": "America/New_York", "chicago": "America/Chicago",
               "denver": "America/Denver", "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
               "seattle": "America/Los_Angeles", "san francisco": "America/Los_Angeles", "sydney": "Australia/Sydney",
               "singapore": "Asia/Singapore", "hong kong": "Asia/Hong_Kong", "beijing": "Asia/Shanghai",
               "shanghai": "Asia/Shanghai", "mumbai": "Asia/Kolkata", "india": "Asia/Kolkata", "dubai": "Asia/Dubai",
               "utc": "UTC", "gmt": "UTC", "mexico city": "America/Mexico_City", "toronto": "America/Toronto",
               "sao paulo": "America/Sao_Paulo", "moscow": "Europe/Moscow", "seoul": "Asia/Seoul",
               "austin": "America/Chicago", "dallas": "America/Chicago", "houston": "America/Chicago",
               "phoenix": "America/Phoenix", "honolulu": "Pacific/Honolulu", "anchorage": "America/Anchorage"}


def _tz(place):
    from zoneinfo import ZoneInfo, available_timezones
    key = _TZ_ALIASES.get(place.lower())
    if not key:
        want = place.replace(" ", "_").lower()
        key = next((z for z in available_timezones() if z.lower() == want or z.lower().endswith("/" + want)), None)
    if not key:
        raise CalcError(f"unknown time zone or city '{place}' (use an IANA name like Europe/Madrid)")
    return ZoneInfo(key)


# ------------------------------------------------------------------ main
def calculate(expression, digits=15):
    expr = " ".join(str(expression or "").split())
    digits = max(5, min(int(digits or 15), 1000))
    if not expr:
        raise CalcError("empty expression")
    if len(expr) > 2000:
        raise CalcError("expression too long")
    if _FORBIDDEN.search(expr):
        raise CalcError("that expression contains something that is not math")
    try:
        d = _dates(expr)
    except CalcError:
        raise
    except Exception as e:
        raise CalcError(f"date calculation failed: {e}")
    if d:
        return d
    if _UNIT_RX.match(expr) or re.search(r"\d\s*(?:mph|km/h|kph|kg|lbs?|ft|feet|inch(?:es)?|mi|miles?|°[CF]|deg[CF]|"
                                         r"psi|kWh|kW|J|N|Pa|bar|L|mL|gal(?:lons?)?|oz|cups?)\b", expr):
        try:
            return _units(expr, digits)
        except CalcError:
            if _UNIT_RX.match(expr) and " to " in expr.lower():
                raise
    ns = _ns()
    expr = re.sub(r"%\s+of\s+", "% * ", expr)                          # 15% of 240
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%",r"(\1/100)", expr)          # 15% -> (15/100)
    explicit = bool(re.search(r"[A-Za-z_]\w*\(", expr))                 # factor(...), expand(...): keep their form
    try:
        if re.search(r"(?<![<>=!])=(?!=)", expr) and not re.match(r"^\s*(solve|Eq)\b", expr):
            lhs, rhs = expr.split("=", 1)
            eq = sp.Eq(parse_expr(lhs, local_dict=ns, transformations=_TRANSFORMS),
                       parse_expr(rhs, local_dict=ns, transformations=_TRANSFORMS))
            syms = sorted(eq.free_symbols, key=lambda s: s.name)
            if not syms:
                return {"kind": "check", "result": str(bool(sp.simplify(eq.lhs - eq.rhs) == 0)), "decimal": None,
                        "latex": sp.latex(eq)}
            var = next((s for s in syms if s.name == "x"), syms[0])
            sols = sp.solve(eq, var, dict=False)
            exact, dec, latex = _fmt(sols, digits)
            return {"kind": "solve", "result": f"{var} = {exact}", "decimal": dec, "latex": f"{sp.latex(var)} \\in {latex}",
                    "equation": sp.latex(eq)}
        value = parse_expr(expr, local_dict=ns, transformations=_TRANSFORMS, evaluate=True)
        if isinstance(value, sp.Basic) and not isinstance(value, (sp.Matrix, sp.ImmutableMatrix)):
            if value.is_number:
                value = sp.nsimplify(value) if isinstance(value, sp.Float) and len(str(value)) < 18 else value
            elif not explicit and len(str(value)) < 400:
                value = sp.simplify(value)
    except CalcError:
        raise
    except Exception as e:
        raise CalcError(f"could not evaluate: {type(e).__name__}: {str(e)[:200]}")
    exact, dec, latex = _fmt(value, digits)
    if isinstance(value, sp.Rational) and value.q > 10**6 and dec:
        # decimals in, an unreadable fraction out: lead with the decimal, keep the exact value second
        return {"kind": "math", "result": dec, "decimal": None, "latex": sp.latex(sp.Float(value, digits)),
                "exact": exact}
    return {"kind": "math", "result": exact, "decimal": dec, "latex": latex}


def render(res):
    """Text for the model."""
    out = res["result"]
    if res.get("decimal"):
        out += f"\n≈ {res['decimal']}"
    if res.get("exact"):
        out += f"\nexact: {res['exact']}"
    if res.get("latex") and res["latex"] != res["result"]:
        out += f"\nLaTeX: {res['latex']}"
    return out


if __name__ == "__main__":
    import sys
    for q in sys.argv[1:]:
        print(q, "->", render(calculate(q)))
