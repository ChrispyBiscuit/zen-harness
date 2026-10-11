"""Generate a frozen long-tail fact set (AA-Omniscience style) from random offline-Wikipedia articles.

  venv\\Scripts\\python.exe evals\\agent_eval\\gen_longtail.py [N]        -> evals\\agent_eval\\longtail.json

Questions come from infobox fields of RANDOM articles (obscure people, small towns, minor films and companies): the
long tail where a small model's recall fails. The answer key is the infobox value. Note when reading results: the
grounded run uses the same Wikipedia, so this measures "does grounding work when the fact is in the source".
"""
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import encyclopedia as E  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
YEAR = re.compile(r"\b(1[5-9]\d\d|20[0-2]\d)\b")


def first_year(v):
    m = YEAR.search(v or "")
    return int(m.group(1)) if m else None


def person(v):
    """First person-looking name in a value: 'Jane Smith Tom Lee' -> ('Jane Smith', 'Smith')."""
    m = re.match(r"\s*([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){1,3})", v or "")
    if not m:
        return None
    name = m.group(1)
    return name, name.split()[-1]


def make(art):
    t, box = art["title"], dict((k.lower(), v) for k, v in art["infobox"])
    if len(t) > 60 or t.lower().startswith(("list of", "index of")):
        return None
    opts = []
    for k, v in box.items():
        if k.startswith("born") and first_year(v):
            opts.append((f"In what year was {t} born?", {"year": first_year(v)}))
        elif k.startswith("died") and first_year(v):
            opts.append((f"In what year did {t} die?", {"year": first_year(v)}))
        elif k.startswith(("founded", "established", "formed", "incorporated")) and first_year(v):
            opts.append((f"In what year was {t} founded?", {"year": first_year(v)}))
        elif k.startswith(("directed by", "director")) and person(v):
            opts.append((f"Who directed {t}?", {"name": person(v)[1], "full": person(v)[0]}))
        elif k.startswith(("written by", "author")) and person(v):
            opts.append((f"Who wrote {t}?", {"name": person(v)[1], "full": person(v)[0]}))
        elif "population" in k and "total" in k:
            m = re.search(r"\d[\d,]{2,}", v)
            if m:
                pop = int(m.group(0).replace(",", ""))
                if pop >= 200:
                    opts.append((f"What is the population of {t}?", {"number": pop}))
    return random.choice(opts) if opts else None


def main():
    a = E._archive()
    out, seen, tries = [], set(), 0
    while len(out) < N and tries < N * 60:
        tries += 1
        e = a.get_random_entry()
        while e.is_redirect:
            e = e.get_redirect_entry()
        if e.title in seen:
            continue
        seen.add(e.title)
        try:
            art = E.article(e)
        except Exception:
            continue
        q = make(art)
        if q:
            out.append({"id": f"lt_{len(out):02d}", "q": q[0], "answer": q[1], "source": art["url"]})
    path = os.path.join(HERE, "longtail.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"{len(out)} questions -> {path}")
    for x in out:
        print(f"  {x['q']}  =>  {x['answer']}")


if __name__ == "__main__":
    main()
