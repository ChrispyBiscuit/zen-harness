"""The project's code graph from tree-sitter (plans/code-graph-prd.md). Local and deterministic: no model, no network.

P1  broken_refs(root, changed): names that resolve to nothing across files, reported only when certain:
      - an imported name a project file does not export (`import { Screen } from './ui.js'`, ui.js exports UIScreen)
      - a member read of an imported object literal / dict that lacks the key (`ARENA.center`, config.js's ARENA has
        size, wallHeight, … — run 71a2ec62: the page crashed on load after 20/20 unit tests passed)
      - a module attribute that does not exist (`ns.foo`, `config.SPEED`)
      - a relative import of a file that does not exist (`./confg.js`, `from .utlis import x`)
    Anything dynamic is skipped, never guessed: spreads and computed keys, `export *` / star imports, module
    __getattr__, a name shadowed in the file, optional chaining, reads guarded by `||` / `??` / `typeof` / `in`.
P2  callers / callees / impact / path / explain (symbol level) — see the functions below.

Python, JavaScript, TypeScript, JSX/TSX. Facts are cached per file by (mtime, size); a whole project is parsed once
per process and then only changed files are.
"""
import difflib
import os
import re
import threading
from collections import defaultdict, deque

try:
    from tree_sitter import Language, Parser
except ImportError:                                     # the check is skipped without tree-sitter
    Language = Parser = None

LANG_OF = {".py": "python", ".pyw": "python", ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "jsx",
           ".ts": "ts", ".mts": "ts", ".cts": "ts", ".tsx": "tsx"}
JS_EXTS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".json")
IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target",
               "coverage", ".zen", ".mypy_cache", ".pytest_cache", ".tox", ".cache", "site-packages", "web_dist",
               "vendor", "third_party", "agent_trash", "backups"}
MAX_FILES = 3000
MAX_BYTES = 600_000
OBJECT_PROTO = {"hasOwnProperty", "toString", "valueOf", "constructor", "isPrototypeOf", "propertyIsEnumerable",
                "toLocaleString", "__proto__", "length", "prototype", "name", "call", "apply", "bind"}
DICT_METHODS = {"get", "items", "keys", "values", "update", "pop", "setdefault", "copy", "clear", "popitem",
                "fromkeys"}

_parsers, _lock = {}, threading.Lock()
_cache = {}                                             # abs path -> (stamp, facts)


def _parser(lang):
    if Parser is None:
        return None
    with _lock:
        if lang not in _parsers:
            try:
                if lang == "python":
                    import tree_sitter_python as m
                    lg = m.language()
                elif lang in ("js", "jsx"):
                    import tree_sitter_javascript as m
                    lg = m.language()
                elif lang == "ts":
                    import tree_sitter_typescript as m
                    lg = m.language_typescript()
                else:
                    import tree_sitter_typescript as m
                    lg = m.language_tsx()
                _parsers[lang] = Parser(Language(lg))
            except Exception:
                _parsers[lang] = None
        return _parsers[lang]


def available():
    return _parser("python") is not None


def _t(src, n):
    return src[n.start_byte:n.end_byte].decode("utf-8", "replace")


def _walk(n):
    stack = [n]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(reversed(x.children))


def _str_val(src, n):
    """The text of a string literal node (no interpolation), else None."""
    if n is None or n.type not in ("string", "template_string"):
        return None
    frags = [c for c in n.children if c.is_named and c.type not in ("string_start", "string_end")]
    if any(c.type not in ("string_fragment", "string_content", "escape_sequence") for c in frags):
        return None                                     # an f-string / template with ${…}: not a constant
    return "".join(_t(src, c) for c in frags)


# ------------------------------------------------------------------------------------------------ JavaScript / TS
def _js_facts(src, tree):
    f = {"lang": "js", "exports": {}, "open_exports": False, "star_exports": [], "members": {}, "open_members": set(),
         "imports": [], "reads": [], "writes": set(), "declared": defaultdict(int), "defs": {}, "calls": []}
    root = tree.root_node

    def obj_keys(o):
        keys = set()
        for ch in o.named_children:
            if ch.type == "pair":
                k = ch.child_by_field_name("key")
                if k is None or k.type == "computed_property_name":
                    return None
                keys.add(_str_val(src, k) if k.type == "string" else _t(src, k))
            elif ch.type in ("shorthand_property_identifier", "shorthand_property"):
                keys.add(_t(src, ch))
            elif ch.type == "method_definition":
                k = ch.child_by_field_name("name")
                if k is None or k.type == "computed_property_name":
                    return None
                keys.add(_t(src, k))
            elif ch.type == "comment":
                continue
            else:                                       # spread, anything unusual: not certain
                return None
        return {k for k in keys if k is not None}

    def declare_from_decl(d, exported):
        """Names a declaration introduces (and their object-literal members)."""
        names = []
        if d.type in ("lexical_declaration", "variable_declaration"):
            for vd in d.named_children:
                if vd.type != "variable_declarator":
                    continue
                nm, val = vd.child_by_field_name("name"), vd.child_by_field_name("value")
                if nm is None:
                    continue
                if nm.type == "identifier":
                    names.append(_t(src, nm))
                    if val is not None and val.type == "object":
                        keys = obj_keys(val)
                        if keys is not None:
                            f["members"][_t(src, nm)] = keys
                    if val is not None and val.type in ("arrow_function", "function_expression", "function"):
                        f["defs"][_t(src, nm)] = {"line": vd.start_point[0] + 1, "node": (val.start_byte, val.end_byte),
                                                  "sig": _t(src, vd).split("=>")[0].split("{")[0][:120]}
                else:                                   # destructuring: the names, no members
                    names += [_t(src, x) for x in _walk(nm) if x.type in ("identifier",
                                                                          "shorthand_property_identifier_pattern")]
        else:
            nm = d.child_by_field_name("name")
            if nm is not None:
                names.append(_t(src, nm))
                if d.type in ("function_declaration", "generator_function_declaration", "class_declaration"):
                    f["defs"][_t(src, nm)] = {"line": d.start_point[0] + 1, "node": (d.start_byte, d.end_byte),
                                              "sig": _t(src, d).split("{")[0].strip()[:120]}
        for n in names:
            f["declared"][n] += 1
            if exported:
                f["exports"][n] = d.start_point[0] + 1
        return names

    for st in root.named_children:
        if st.type == "export_statement":
            src_node = st.child_by_field_name("source")
            decl = st.child_by_field_name("declaration")
            is_default = any(not c.is_named and _t(src, c) == "default" for c in st.children)
            if is_default:                              # export default function App() {} / export default x
                f["exports"]["default"] = st.start_point[0] + 1
            if decl is not None:
                declare_from_decl(decl, not is_default)
                continue
            clause = next((c for c in st.named_children if c.type == "export_clause"), None)
            if src_node is not None and clause is None:  # export * from './x'
                f["star_exports"].append(_str_val(src, src_node) or "")
                continue
            if clause is not None:
                for sp in clause.named_children:
                    if sp.type == "export_specifier":
                        nm, al = sp.child_by_field_name("name"), sp.child_by_field_name("alias")
                        f["exports"][_t(src, al or nm)] = sp.start_point[0] + 1
                if src_node is not None:                # export { a } from './x': a re-export, trust it
                    pass
                continue
        elif st.type in ("lexical_declaration", "variable_declaration", "function_declaration",
                         "class_declaration", "generator_function_declaration"):
            declare_from_decl(st, False)

    for n in _walk(root):
        t = n.type
        if t == "import_statement":
            so = n.child_by_field_name("source")
            spec = _str_val(src, so)
            clause = next((c for c in n.named_children if c.type == "import_clause"), None)
            if spec is None:
                continue
            if clause is None:
                f["imports"].append({"spec": spec, "kind": "side", "line": n.start_point[0] + 1})
                continue
            for c in clause.named_children:
                if c.type == "identifier":
                    f["imports"].append({"spec": spec, "kind": "default", "name": "default", "local": _t(src, c),
                                         "line": n.start_point[0] + 1})
                elif c.type == "namespace_import":
                    idn = next((x for x in c.named_children if x.type == "identifier"), None)
                    if idn is not None:
                        f["imports"].append({"spec": spec, "kind": "namespace", "local": _t(src, idn),
                                             "line": n.start_point[0] + 1})
                elif c.type == "named_imports":
                    for sp in c.named_children:
                        if sp.type != "import_specifier":
                            continue
                        nm, al = sp.child_by_field_name("name"), sp.child_by_field_name("alias")
                        if nm is None:
                            continue
                        f["imports"].append({"spec": spec, "kind": "named", "name": _t(src, nm),
                                             "local": _t(src, al or nm), "line": sp.start_point[0] + 1})
        elif t == "variable_declarator":                # const x = require('./m') / const {a} = require('./m')
            val, nm = n.child_by_field_name("value"), n.child_by_field_name("name")
            if val is not None and val.type == "call_expression" and nm is not None:
                fn = val.child_by_field_name("function")
                args = val.child_by_field_name("arguments")
                if fn is not None and _t(src, fn) == "require" and args is not None and args.named_child_count == 1:
                    spec = _str_val(src, args.named_children[0])
                    if spec is not None:
                        if nm.type == "identifier":
                            f["imports"].append({"spec": spec, "kind": "namespace", "local": _t(src, nm),
                                                 "line": n.start_point[0] + 1, "cjs": True})
                        elif nm.type == "object_pattern":
                            for p in nm.named_children:
                                if p.type == "shorthand_property_identifier_pattern":
                                    f["imports"].append({"spec": spec, "kind": "named", "name": _t(src, p),
                                                         "local": _t(src, p), "line": p.start_point[0] + 1,
                                                         "cjs": True})
                                elif p.type == "pair_pattern":
                                    k, v = p.child_by_field_name("key"), p.child_by_field_name("value")
                                    if k is not None and v is not None and v.type == "identifier":
                                        f["imports"].append({"spec": spec, "kind": "named", "name": _t(src, k),
                                                             "local": _t(src, v), "line": p.start_point[0] + 1,
                                                             "cjs": True})
        elif t == "assignment_expression":
            left = n.child_by_field_name("left")
            if left is not None and left.type in ("member_expression", "subscript_expression"):
                obj = left.child_by_field_name("object")
                if obj is not None and obj.type == "identifier":
                    if left.type == "member_expression":
                        prop = left.child_by_field_name("property")
                        f["writes"].add((_t(src, obj), _t(src, prop) if prop is not None else ""))
                    f["open_members"].add(_t(src, obj)) if left.type == "subscript_expression" else None
                txt = _t(src, left)
                if txt == "module.exports":             # CommonJS: an object literal is exact, anything else is open
                    right = n.child_by_field_name("right")
                    keys = obj_keys(right) if right is not None and right.type == "object" else None
                    if keys is None:
                        f["open_exports"] = True
                    else:
                        for k in keys:
                            f["exports"][k] = n.start_point[0] + 1
                elif re.match(r"^(module\.)?exports\.[\w$]+$", txt):
                    f["exports"][txt.rsplit(".", 1)[1]] = n.start_point[0] + 1
        elif t == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None:
                ft = _t(src, fn)
                if ft in ("Object.assign", "Object.defineProperty", "Object.defineProperties", "Object.setPrototypeOf"):
                    args = n.child_by_field_name("arguments")
                    if args is not None and args.named_child_count and args.named_children[0].type == "identifier":
                        f["open_members"].add(_t(src, args.named_children[0]))
                f["calls"].append((ft.split("(")[0][-80:], n.start_point[0] + 1, n.start_byte))
        elif t == "member_expression":
            obj, prop = n.child_by_field_name("object"), n.child_by_field_name("property")
            if obj is None or prop is None or obj.type != "identifier" or prop.type != "property_identifier":
                continue
            if any(c.type == "optional_chain" for c in n.children):
                continue
            par = n.parent
            guarded = False
            if par is not None:
                if par.type == "assignment_expression" and par.child_by_field_name("left") == n:
                    continue                            # a write, recorded above
                if par.type == "binary_expression":
                    op = next((c for c in par.children if not c.is_named), None)
                    if op is not None and _t(src, op) in ("||", "??", "in", "&&") \
                            and par.child_by_field_name("left") == n:
                        guarded = True
                if par.type == "unary_expression" and _t(src, par).startswith("typeof"):
                    guarded = True
                if par.type in ("parenthesized_expression",) and par.parent is not None and \
                        par.parent.type in ("if_statement", "ternary_expression"):
                    guarded = True
                if par.type == "ternary_expression" and par.child_by_field_name("condition") == n:
                    guarded = True
            if not guarded:
                f["reads"].append((_t(src, obj), _t(src, prop), n.start_point[0] + 1))
        elif t in ("formal_parameters", "catch_clause"):
            for x in _walk(n):
                if x.type in ("identifier", "shorthand_property_identifier_pattern"):
                    f["declared"][_t(src, x)] += 1
        elif t in ("function_declaration", "class_declaration") and n.parent is not None and n.parent != root \
                and n.parent.type != "export_statement":
            nm = n.child_by_field_name("name")
            if nm is not None:
                f["declared"][_t(src, nm)] += 1
        elif t == "variable_declarator" and n.parent is not None and n.parent.parent is not None \
                and n.parent.parent != root and n.parent.parent.type != "export_statement":
            nm = n.child_by_field_name("name")
            if nm is not None:
                for x in _walk(nm):
                    if x.type in ("identifier", "shorthand_property_identifier_pattern"):
                        f["declared"][_t(src, x)] += 1
    # function-level defs inside classes (methods), for P2
    for n in _walk(root):
        if n.type == "method_definition":
            nm = n.child_by_field_name("name")
            cls = n.parent.parent if n.parent is not None else None
            cn = cls.child_by_field_name("name") if cls is not None and cls.type == "class_declaration" else None
            if nm is not None:
                key = (_t(src, cn) + "." if cn is not None else "") + _t(src, nm)
                f["defs"][key] = {"line": n.start_point[0] + 1, "node": (n.start_byte, n.end_byte),
                                  "sig": _t(src, n).split("{")[0].strip()[:120]}
    return f


# ------------------------------------------------------------------------------------------------------- Python
def _py_facts(src, tree):
    f = {"lang": "python", "exports": {}, "open_exports": False, "star_exports": [], "members": {},
         "open_members": set(), "imports": [], "reads": [], "subscripts": [], "writes": set(),
         "declared": defaultdict(int), "defs": {}, "calls": [], "all": None, "guarded": set()}
    root = tree.root_node

    def bind(target, line):
        for x in _walk(target):
            if x.type == "identifier" and (x.parent is None or x.parent.type not in ("attribute", "subscript")):
                f["exports"].setdefault(_t(src, x), line)

    def top(block):
        for st in block.named_children:
            t = st.type
            if t == "decorated_definition":
                st = st.child_by_field_name("definition") or st
                t = st.type
            line = st.start_point[0] + 1
            if t in ("function_definition", "class_definition"):
                nm = st.child_by_field_name("name")
                if nm is not None:
                    f["exports"][_t(src, nm)] = line
                    f["defs"][_t(src, nm)] = {"line": line, "node": (st.start_byte, st.end_byte),
                                              "sig": _t(src, st).split(":\n")[0].split("\n")[0][:120]}
                    if _t(src, nm) == "__getattr__":
                        f["open_exports"] = True
                if t == "class_definition":
                    body = st.child_by_field_name("body")
                    for m in (body.named_children if body is not None else []):
                        m2 = m.child_by_field_name("definition") if m.type == "decorated_definition" else m
                        if m2 is not None and m2.type == "function_definition":
                            mn = m2.child_by_field_name("name")
                            f["defs"][f"{_t(src, nm)}.{_t(src, mn)}"] = {
                                "line": m2.start_point[0] + 1, "node": (m2.start_byte, m2.end_byte),
                                "sig": _t(src, m2).split("\n")[0][:120]}
            elif t == "expression_statement":
                for a in st.named_children:
                    if a.type in ("assignment", "augmented_assignment"):
                        left, right = a.child_by_field_name("left"), a.child_by_field_name("right")
                        if left is not None:
                            bind(left, line)
                            if left.type == "identifier" and _t(src, left) == "__all__" and right is not None \
                                    and right.type in ("list", "tuple"):
                                vals = [_str_val(src, x) for x in right.named_children]
                                if all(v is not None for v in vals):
                                    f["all"] = set(vals)
                            if left.type == "identifier" and right is not None and right.type == "dictionary":
                                keys = set()
                                for p in right.named_children:
                                    if p.type == "pair":
                                        k = _str_val(src, p.child_by_field_name("key"))
                                        if k is None:
                                            keys = None
                                            break
                                        keys.add(k)
                                    elif p.type != "comment":
                                        keys = None
                                        break
                                if keys is not None:
                                    f["members"][_t(src, left)] = keys
            elif t in ("import_statement", "import_from_statement", "future_import_statement"):
                for x in st.named_children:
                    if x.type == "dotted_name" and t == "import_statement":
                        f["exports"].setdefault(_t(src, x).split(".")[0], line)
                    elif x.type == "aliased_import":
                        al = x.child_by_field_name("alias")
                        if al is not None:
                            f["exports"].setdefault(_t(src, al), line)
                    elif x.type == "dotted_name" and t == "import_from_statement" and x != st.child_by_field_name(
                            "module_name"):
                        f["exports"].setdefault(_t(src, x).split(".")[-1], line)
                    elif x.type == "wildcard_import":
                        f["open_exports"] = True
            elif t in ("if_statement", "try_statement", "with_statement", "for_statement", "while_statement"):
                for b in _walk(st):
                    if b.type == "block" and b.parent is not None and b.parent.type not in (
                            "function_definition", "class_definition"):
                        top(b)
                        break
                for clause in st.named_children:
                    if clause.type in ("else_clause", "elif_clause", "except_clause", "finally_clause"):
                        for b in clause.named_children:
                            if b.type == "block":
                                top(b)
            elif t == "global_statement":
                f["open_exports"] = True

    top(root)
    if f["all"] is not None:                            # __all__ narrows `import *`, not attribute access
        pass

    def module_name(n):
        return _t(src, n) if n is not None else ""

    for n in _walk(root):
        t = n.type
        line = n.start_point[0] + 1
        if t == "import_statement":
            for x in n.named_children:
                if x.type == "dotted_name":
                    nm = _t(src, x)
                    f["imports"].append({"spec": nm, "kind": "module", "local": nm.split(".")[0], "full": nm,
                                         "line": line})
                elif x.type == "aliased_import":
                    nm, al = x.child_by_field_name("name"), x.child_by_field_name("alias")
                    f["imports"].append({"spec": module_name(nm), "kind": "module", "local": _t(src, al),
                                         "full": module_name(nm), "line": line})
        elif t == "import_from_statement":
            mod = n.child_by_field_name("module_name")
            spec = module_name(mod)
            for x in n.named_children:
                if x == mod:
                    continue
                if x.type == "dotted_name":
                    nm = _t(src, x)
                    f["imports"].append({"spec": spec, "kind": "named", "name": nm, "local": nm, "line": line})
                elif x.type == "aliased_import":
                    nm, al = x.child_by_field_name("name"), x.child_by_field_name("alias")
                    f["imports"].append({"spec": spec, "kind": "named", "name": _t(src, nm), "local": _t(src, al),
                                         "line": line})
        elif t == "attribute":
            obj, attr = n.child_by_field_name("object"), n.child_by_field_name("attribute")
            if obj is None or attr is None or obj.type != "identifier":
                continue
            par = n.parent
            if par is not None and par.type == "assignment" and par.child_by_field_name("left") == n:
                f["writes"].add((_t(src, obj), _t(src, attr)))
                continue
            guarded = par is not None and par.type == "call" and False
            if par is not None and par.type == "boolean_operator":
                guarded = True
            if not guarded:
                f["reads"].append((_t(src, obj), _t(src, attr), line))
        elif t == "subscript":                          # config.ARENA["center"] / ARENA["center"]
            val = n.child_by_field_name("value")
            subs = [c for c in n.named_children if c != val]
            key = _str_val(src, subs[0]) if len(subs) == 1 else None
            if val is None or key is None:
                continue
            par = n.parent
            if par is not None and par.type in ("assignment", "augmented_assignment") \
                    and par.child_by_field_name("left") == n:
                if val.type == "identifier":
                    f["open_members"].add(_t(src, val))
                continue
            if val.type == "attribute":
                o, a = val.child_by_field_name("object"), val.child_by_field_name("attribute")
                if o is not None and a is not None and o.type == "identifier":
                    f["subscripts"].append((_t(src, o), _t(src, a), key, line))
            elif val.type == "identifier":
                f["subscripts"].append((None, _t(src, val), key, line))
        elif t == "call":
            fn = n.child_by_field_name("function")
            if fn is not None:
                ft = _t(src, fn)
                f["calls"].append((ft[-80:], line, n.start_byte))
                if ft in ("hasattr", "getattr"):        # hasattr(mod, "X"): X is optional there
                    args = n.child_by_field_name("arguments")
                    a = [x for x in (args.named_children if args is not None else [])]
                    if len(a) >= 2 and a[0].type == "identifier" and _str_val(src, a[1]) is not None:
                        f["guarded"].add((_t(src, a[0]), _str_val(src, a[1])))
                if fn.type == "attribute":
                    o, a = fn.child_by_field_name("object"), fn.child_by_field_name("attribute")
                    if o is not None and a is not None and _t(src, a) in ("update", "setdefault", "pop", "clear"):
                        f["open_members"].add(_t(src, o))
        elif t in ("parameters", "lambda_parameters"):
            for x in _walk(n):
                if x.type == "identifier":
                    f["declared"][_t(src, x)] += 1
        elif t in ("function_definition", "class_definition") and n.parent is not None and n.parent != root:
            for x in _walk(n.child_by_field_name("body") or n):
                if x.type == "assignment":
                    left = x.child_by_field_name("left")
                    if left is not None and left.type == "identifier":
                        f["declared"][_t(src, left)] += 1
    return f


# ------------------------------------------------------------------------------------------------------- facts
def facts(path):
    """Parsed facts of one file (cached by mtime and size), or None (not a supported language, too big, unreadable)."""
    lang = LANG_OF.get(os.path.splitext(path)[1].lower())
    if not lang:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    if st.st_size > MAX_BYTES:
        return None
    p = _parser(lang)
    if p is None:
        return None
    try:
        with open(path, "rb") as fh:
            src = fh.read()
        if src.count(b"\n") < len(src) / 400:          # minified / bundled: not the project's own code
            _cache[path] = (stamp, None)
            return None
        tree = p.parse(src)
        f = _py_facts(src, tree) if lang == "python" else _js_facts(src, tree)
        f["parse_errors"] = tree.root_node.has_error
        f["src"] = src
    except Exception:
        return None
    _cache[path] = (stamp, f)
    return f


def project_files(root):
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith((".", "_backup"))]
        for n in files:
            if os.path.splitext(n)[1].lower() in LANG_OF:
                out.append(os.path.join(base, n))
                if len(out) >= MAX_FILES:
                    return out
    return out


def project_root(path, roots=()):
    """The nearest folder above `path` holding a project marker, not above the allowed roots."""
    markers = ("package.json", "pyproject.toml", "setup.py", "setup.cfg", ".git", "requirements.txt", "tsconfig.json",
               "index.html", "deno.json")
    d = os.path.dirname(os.path.abspath(path))
    stops = {os.path.normcase(os.path.abspath(r)) for r in roots or ()}
    first = d
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in markers):
            return d
        if os.path.normcase(d) in stops:
            return first
        up = os.path.dirname(d)
        if up == d:
            return first
        d = up


# ----------------------------------------------------------------------------------------------- resolution
def resolve_js(spec, from_file):
    """A relative/absolute JS import -> the project file, None (not found), or '' (a package or URL: not checked)."""
    if not spec or not (spec.startswith(".") or spec.startswith("/")) or "://" in spec:
        return ""
    base = os.path.normpath(os.path.join(os.path.dirname(from_file), spec)) if spec.startswith(".") else \
        os.path.normpath(spec)
    cands = [base] + [base + e for e in JS_EXTS] + [os.path.join(base, "index" + e) for e in JS_EXTS]
    stem, ext = os.path.splitext(base)                  # TS: './x.js' that is really './x.ts'
    if ext in (".js", ".mjs", ".cjs", ".jsx"):
        cands += [stem + e for e in (".ts", ".tsx", ".mts", ".cts")]
    return next((c for c in cands if os.path.isfile(c)), None)


def resolve_py(spec, from_file, root):
    """A Python module name -> its file in the project, None (relative import that does not exist), or ''
    (absolute and not in the project: a package, not checked)."""
    if spec.startswith("."):
        dots = len(spec) - len(spec.lstrip("."))
        d = os.path.dirname(os.path.abspath(from_file))
        for _ in range(dots - 1):
            d = os.path.dirname(d)
        rest = spec[dots:]
        bases = [os.path.join(d, *rest.split(".")) if rest else d]
        rel = True
    else:
        parts = spec.split(".")
        bases = [os.path.join(b, *parts) for b in (os.path.dirname(os.path.abspath(from_file)), root,
                                                   os.path.join(root, "src"))]
        rel = False
    for b in bases:
        for c in (b + ".py", os.path.join(b, "__init__.py")):
            if os.path.isfile(c):
                return c
        if os.path.isdir(b) and rel:
            return b                                    # a namespace package folder
    return None if rel else ""


def _close(name, options):
    m = difflib.get_close_matches(name, sorted(options), n=3, cutoff=0.6)
    return (" Did you mean " + " or ".join(f"'{x}'" for x in m) + "?") if m else ""


def _listing(options, n=8):
    o = sorted(options)
    return ", ".join(o[:n]) + (f" and {len(o) - n} more" if len(o) > n else "")


def _exports_of(target):
    f = facts(target)
    if f is None or f.get("parse_errors") or f["open_exports"] or f["star_exports"]:
        return None
    return f


def _check_file(path, root, written=frozenset()):
    """Certain broken references made BY one file -> [(file, line, message)]."""
    f = facts(path)
    if f is None:
        return []
    out = []
    rel = lambda p: os.path.relpath(p, root).replace("\\", "/")
    if f["lang"] == "python":
        missing = set()
        for im in f["imports"]:
            target = resolve_py(im["spec"], path, root)
            if target is None:
                if im["spec"] in missing:
                    continue
                missing.add(im["spec"])
                out.append((path, im["line"], f"`{im['spec']}` is imported but no such module exists in the project "
                                              f"(relative import)."))
                continue
            if not target:
                continue
            if im["kind"] == "named":
                if os.path.isdir(target) or os.path.basename(target) == "__init__.py":
                    sub = os.path.join(os.path.dirname(target) if not os.path.isdir(target) else target, im["name"])
                    if os.path.isfile(sub + ".py") or os.path.isdir(sub):
                        continue                        # a submodule of the package
                    if os.path.isdir(target):
                        continue
                tf = _exports_of(target)
                if tf is not None and im["name"] not in tf["exports"]:
                    out.append((path, im["line"], f"`from {im['spec']} import {im['name']}`: {rel(target)} defines no "
                                                  f"`{im['name']}`.{_close(im['name'], tf['exports'])}"))
        mods = {im["local"]: im for im in f["imports"] if im["kind"] == "module" and im["local"] == im.get("full")}
        named = {im["local"]: im for im in f["imports"] if im["kind"] == "named"}
        for obj, attr, line in f["reads"]:
            im = mods.get(obj)
            if im is None or f["declared"].get(obj) or attr.startswith("__"):
                continue
            target = resolve_py(im["spec"], path, root)
            if not target or os.path.isdir(target):
                continue
            tf = _exports_of(target)
            if tf is None or attr in tf["exports"]:
                continue
            if os.path.basename(target) == "__init__.py":
                sub = os.path.join(os.path.dirname(target), attr)
                if os.path.isfile(sub + ".py") or os.path.isdir(sub):
                    continue
            if any(o == obj and a == attr for o, a in f["writes"]) or (obj, attr) in f["guarded"]:
                continue
            out.append((path, line, f"`{obj}.{attr}`: {rel(target)} defines no `{attr}`.{_close(attr, tf['exports'])}"))
        for mod_local, name, key, line in f["subscripts"]:
            if mod_local is not None:
                im = mods.get(mod_local)
                if im is None:
                    continue
                target, cname = resolve_py(im["spec"], path, root), name
            else:
                im = named.get(name)
                if im is None or f["declared"].get(name) or name in f["open_members"]:
                    continue
                target, cname = resolve_py(im["spec"], path, root), im["name"]
            if not target or os.path.isdir(target):
                continue
            tf = facts(target)
            if tf is None or tf.get("parse_errors") or cname in tf["open_members"]:
                continue
            keys = tf["members"].get(cname)
            if keys is None or key in keys:
                continue
            out.append((path, line, f"`{name}[{key!r}]`: {cname} in {rel(target)} has no key {key!r} (it has "
                                    f"{_listing(keys)}).{_close(key, keys)}"))
        return out

    # JavaScript / TypeScript
    bound, missing = {}, set()
    for im in f["imports"]:
        target = resolve_js(im["spec"], path)
        if target is None:
            if im["spec"] in missing:
                continue
            missing.add(im["spec"])
            out.append((path, im["line"], f"`{im['spec']}` is imported but that file does not exist (from "
                                          f"{rel(os.path.dirname(path) or root) or '.'}/)."))
            continue
        if not target or target.endswith(".json") or im["kind"] == "side":
            continue
        tf = _exports_of(target)
        if tf is None:
            continue
        if im["kind"] == "named" and im["name"] not in tf["exports"] and not (im.get("cjs") and not tf["exports"]):
            out.append((path, im["line"], f"`{im['name']}` is imported from {rel(target)}, which does not export it "
                                          f"(it exports {_listing(tf['exports'])}).{_close(im['name'], tf['exports'])}"))
            continue
        if im["kind"] == "default" and "default" not in tf["exports"] and tf["lang"] == "js" and \
                not im.get("cjs") and tf["exports"]:
            out.append((path, im["line"], f"`import {im['local']} from '{im['spec']}'`: {rel(target)} has no default "
                                          f"export (it exports {_listing(tf['exports'])})."))
            continue
        if im.get("local") and f["declared"].get(im["local"]):
            continue                                    # shadowed somewhere in this file: not certain
        bound[im["local"]] = (im, target, tf)
    all_writes = set(f["writes"])
    for obj, prop, line in f["reads"]:
        b = bound.get(obj)
        if b is None or prop in OBJECT_PROTO:
            continue
        im, target, tf = b
        if (obj, prop) in all_writes:
            continue
        if im["kind"] == "namespace":
            if im.get("cjs") and not tf["exports"]:
                continue
            if prop not in tf["exports"]:
                out.append((path, line, f"`{obj}.{prop}`: {rel(target)} exports no `{prop}` (it exports "
                                        f"{_listing(tf['exports'])}).{_close(prop, tf['exports'])}"))
        else:
            name = im.get("name") if im["kind"] == "named" else None
            if name is None or name in tf["open_members"]:
                continue
            keys = tf["members"].get(name)
            if keys is None or prop in keys:
                continue
            # a key added from any other project file (CONFIG.extra = …) is defined
            if any((loc, prop) in (facts(p) or {}).get("writes", ()) for p in written for loc in (name, obj)):
                continue
            out.append((path, line, f"`{obj}.{prop}` reads a key {name} does not have: {rel(target)} defines {name} "
                                    f"with {_listing(keys)}.{_close(prop, keys)} (Reading it gives undefined, which "
                                    f"crashes as soon as it is used.)"))
    return out


def _importers(root, target, files):
    """Project files that import `target`."""
    out = []
    t = os.path.normcase(os.path.abspath(target))
    for p in files:
        f = facts(p)
        if f is None:
            continue
        for im in f["imports"]:
            r = resolve_js(im["spec"], p) if f["lang"] != "python" else resolve_py(im["spec"], p, root)
            if r and os.path.normcase(os.path.abspath(r)) == t:
                out.append(p)
                break
    return out


def broken_refs(root, changed, limit=8):
    """Certain broken cross-file references made by the changed files, and made into them by the files that import
    them. -> [(file, line, message)], at most `limit`."""
    if not available():
        return []
    root = os.path.abspath(root)
    changed = [os.path.abspath(p) for p in changed if LANG_OF.get(os.path.splitext(p)[1].lower())]
    if not changed:
        return []
    files = project_files(root)
    check = set(changed)
    for p in changed:
        check.update(_importers(root, p, files))
    found, seen = [], set()
    for p in sorted(check):
        for item in _check_file(p, root, frozenset(files)):
            if item[2] not in seen:
                seen.add(item[2])
                found.append(item)
    return found[:limit]


def format_refs(found, root):
    rel = lambda p: os.path.relpath(p, root).replace("\\", "/")
    return "\n".join(f"- {rel(p)}:{line}: {msg}" for p, line, msg in found)


# --------------------------------------------------------------------------------------------- P2: questions
def _symbols(files):
    """{name: [(file, line, sig)]} for every function/class/method (methods as Class.method and method)."""
    out = defaultdict(list)
    for p in files:
        f = facts(p)
        if f is None:
            continue
        for name, d in f["defs"].items():
            out[name].append((p, d["line"], d["sig"]))
            if "." in name:
                out[name.split(".", 1)[1]].append((p, d["line"], d["sig"]))
    return out


def _enclosing(f, byte):
    """The def (name) whose span holds `byte`, innermost first."""
    best, size = None, None
    for name, d in f["defs"].items():
        s, e = d["node"]
        if s <= byte < e and (size is None or e - s < size):
            best, size = name, e - s
    return best


def _call_name(text):
    """'self.board.move' -> 'move'; 'ui.Screen' -> 'Screen'."""
    return re.split(r"[.\s]", text.strip().rstrip(")"))[-1] if text else ""


def call_graph(root):
    """Edges caller -> callee by name, over the project: ({(file, def)}: {callee names}), and callers per name."""
    files = project_files(root)
    callers = defaultdict(set)                          # callee name -> {(file, caller def or '<module>', line)}
    callees = defaultdict(set)                          # (file, def) -> {callee names}
    for p in files:
        f = facts(p)
        if f is None:
            continue
        for text, line, byte in f["calls"]:
            name = _call_name(text)
            if not name or not re.match(r"^[A-Za-z_$][\w$]*$", name):
                continue
            who = _enclosing(f, byte) or "<module>"
            callers[name].add((p, who, line))
            callees[(p, who)].add(name)
    return files, callers, callees


def question(root, action, target, target2="", cap=40):
    """P2: callers / callees / impact / path / explain for a symbol, as short text with file:line."""
    root = os.path.abspath(root)
    if not available():
        return "The code graph needs tree-sitter, which isn't installed here."
    files, callers, callees = call_graph(root)
    syms = _symbols(files)
    rel = lambda p: os.path.relpath(p, root).replace("\\", "/")
    short = target.split(".")[-1]
    defs = syms.get(target) or syms.get(short) or []
    lines = []
    if action == "callers":
        hits = sorted(callers.get(short, ()))
        if not hits:
            return f"No calls to `{target}` found in {len(files)} files." + (_close(short, syms) if not defs else "")
        lines = [f"{rel(p)}:{line} in {who}" for p, who, line in hits]
        head = f"{len(hits)} call(s) to `{target}`" + (f" (defined at {rel(defs[0][0])}:{defs[0][1]})" if defs else "")
    elif action == "callees":
        if not defs:
            return f"No function `{target}` found.{_close(short, syms)}"
        hits = []
        for p, line, _sig in defs:
            key = next((k for k in callees if k[0] == p and k[1].split(".")[-1] == short), None)
            for name in sorted(callees.get(key, ())):
                where = syms.get(name)
                hits.append(f"{name}" + (f"  ({rel(where[0][0])}:{where[0][1]})" if where else "  (library/builtin)"))
        head = f"`{target}` calls {len(hits)} function(s)"
        lines = hits
    elif action == "impact":
        if not defs:
            return f"No function or class `{target}` found.{_close(short, syms)}"
        hits = sorted(callers.get(short, ()))
        importers = []
        for p, _line, _sig in defs:
            for q in files:
                fq = facts(q)
                if fq and any(im.get("name") == short or im.get("local") == short for im in fq["imports"]) and q != p:
                    importers.append(q)
        tests = sorted({p for p, _w, _l in hits if re.search(r"(^|[/\\])(tests?|__tests__)[/\\]|test_|_test\.|\.test\.|"
                                                             r"\.spec\.", p)})
        head = (f"Changing `{target}` ({rel(defs[0][0])}:{defs[0][1]}, {defs[0][2]}) affects {len(hits)} call site(s) "
                f"in {len({p for p, _w, _l in hits})} file(s)")
        lines = [f"call: {rel(p)}:{line} in {who}" for p, who, line in hits]
        lines += [f"imports it: {rel(q)}" for q in sorted(set(importers))]
        lines += [f"test: {rel(t)}" for t in tests]
    elif action == "path":
        if not target2:
            return "path needs two names: target and target2."
        start, goal = short, target2.split(".")[-1]
        names_calls = defaultdict(set)
        for (p, who), cs in callees.items():
            names_calls[who.split(".")[-1]].update(cs)
        prev, q = {start: None}, deque([start])
        while q:
            cur = q.popleft()
            if cur == goal:
                break
            for nx in names_calls.get(cur, ()):
                if nx not in prev:
                    prev[nx] = cur
                    q.append(nx)
        if goal not in prev:
            return f"No call path from `{target}` to `{target2}` found."
        chain, cur = [], goal
        while cur is not None:
            chain.append(cur)
            cur = prev[cur]
        chain.reverse()
        out = []
        for n in chain:
            w = syms.get(n)
            out.append(n + (f" ({rel(w[0][0])}:{w[0][1]})" if w else ""))
        return f"Call path ({len(chain) - 1} step(s)): " + " → ".join(out)
    elif action == "explain":
        if not defs:
            return f"No function or class `{target}` found.{_close(short, syms)}"
        p, line, sig = defs[0]
        f = facts(p)
        s, e = f["defs"][next(k for k in f["defs"] if k == target or k.split(".")[-1] == short)]["node"]
        body = f["src"][s:e].decode("utf-8", "replace")
        doc = ""
        m = re.search(r'^\s*(?:def|class)[^\n]*\n\s*("""|\'\'\')(.*?)\1', body, re.S) or \
            re.search(r"/\*\*?(.*?)\*/", body[:600], re.S)
        if m:
            doc = " ".join((m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)).split())[:300]
        cs = sorted(callers.get(short, ()))
        key = next((k for k in callees if k[0] == p and k[1].split(".")[-1] == short), None)
        head = f"`{target}` — {rel(p)}:{line}"
        lines = [f"signature: {sig}"] + ([f"doc: {doc}"] if doc else []) + \
                [f"length: {body.count(chr(10)) + 1} lines"] + \
                [f"called from ({len(cs)}): " + ", ".join(f"{rel(a)}:{b}" for a, _w, b in cs[:8]) +
                 (" …" if len(cs) > 8 else "")] + \
                [f"calls: " + ", ".join(sorted(callees.get(key, ()))[:15])] + \
                ([f"also defined at: " + ", ".join(f"{rel(a)}:{b}" for a, b, _s in defs[1:4])] if len(defs) > 1 else [])
    else:
        return "action must be callers, callees, impact, path or explain."
    more = len(lines) - cap
    return head + ":\n" + "\n".join(lines[:cap]) + (f"\n… and {more} more" if more > 0 else "")


# ------------------------------------------------------------------------------------------ P3: definitions
# node type -> kind, per language family; "parent" = the class/impl/type a method belongs to
_DEF_KINDS = {
    "js": {"function_declaration": "function", "generator_function_declaration": "function",
           "class_declaration": "class", "abstract_class_declaration": "class", "method_definition": "method",
           "interface_declaration": "interface", "type_alias_declaration": "type", "enum_declaration": "enum"},
    "c": {"function_definition": "function", "struct_specifier": "class", "class_specifier": "class",
          "union_specifier": "class", "enum_specifier": "enum", "type_definition": "type", "preproc_def": "const",
          "preproc_function_def": "function", "namespace_definition": "namespace"},
    "go": {"function_declaration": "function", "method_declaration": "method", "type_spec": "type"},
    "rust": {"function_item": "function", "function_signature_item": "method", "struct_item": "class",
             "enum_item": "enum", "trait_item": "interface", "union_item": "class", "type_item": "type",
             "const_item": "const", "static_item": "const", "mod_item": "namespace"},
    "csharp": {"class_declaration": "class", "struct_declaration": "class", "record_declaration": "class",
               "interface_declaration": "interface", "enum_declaration": "enum", "method_declaration": "method",
               "constructor_declaration": "method"},
    "java": {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
             "record_declaration": "class", "method_declaration": "method", "constructor_declaration": "method"},
}
_CLASSY = {"class_declaration", "abstract_class_declaration", "class_specifier", "struct_specifier", "impl_item",
           "struct_declaration", "record_declaration", "interface_declaration", "trait_item", "enum_declaration"}
_EXTRA_LANGS = {"c": "tree_sitter_c", "cpp": "tree_sitter_cpp", "go": "tree_sitter_go", "rust": "tree_sitter_rust",
                "csharp": "tree_sitter_c_sharp", "java": "tree_sitter_java"}


def _any_parser(lang):
    """A parser for the code map's language names (js, jsx, ts, tsx, c, cpp, go, rust, csharp, java)."""
    if lang in ("js", "jsx", "ts", "tsx", "python"):
        return _parser(lang)
    if Parser is None or lang not in _EXTRA_LANGS:
        return None
    with _lock:
        if lang not in _parsers:
            try:
                import importlib
                _parsers[lang] = Parser(Language(importlib.import_module(_EXTRA_LANGS[lang]).language()))
            except Exception:
                _parsers[lang] = None
        return _parsers[lang]


def _c_name(src, n):
    """The name a C/C++ function definition declares -> (name, scope): int *foo(void) -> foo;
    void Game::tick() -> (tick, Game)."""
    d = n.child_by_field_name("declarator")
    while d is not None and d.type not in ("identifier", "field_identifier", "qualified_identifier",
                                            "destructor_name", "operator_name"):
        nxt = d.child_by_field_name("declarator")
        if nxt is None:
            nxt = next((c for c in d.named_children if c.type in ("identifier", "field_identifier",
                                                                   "qualified_identifier")), None)
        d = nxt
    if d is None:
        return None, None
    if d.type == "qualified_identifier":
        scope, nm = d.child_by_field_name("scope"), d.child_by_field_name("name")
        return (_t(src, nm) if nm is not None else _t(src, d).split("::")[-1]), \
            (_t(src, scope) if scope is not None else None)
    return _t(src, d), None


def ts_defs(text, lang):
    """Definitions of one file from tree-sitter -> [{"name", "kind", "line", "sig"} (+ "parent")], or None when the
    language has no grammar here or the file doesn't parse (the code map then keeps its regex extractor)."""
    p = _any_parser(lang)
    if p is None:
        return None
    src = text.encode("utf-8", "replace")
    try:
        root = p.parse(src).root_node
    except Exception:
        return None
    if root.has_error and sum(1 for n in _walk(root) if n.type == "ERROR") > 3:
        return None
    family = {"js": "js", "jsx": "js", "ts": "js", "tsx": "js", "c": "c", "cpp": "c"}.get(lang, lang)
    kinds = _DEF_KINDS.get(family, {})
    lines = text.splitlines()
    out = []

    def sig(n):
        ln = lines[n.start_point[0]] if n.start_point[0] < len(lines) else ""
        return " ".join(ln.split())[:160]

    def obj_methods(val, owner):
        """Functions held in object literals of a top-level value (`export const api = { about: () => … }`,
        `create((set) => ({ init: async () => … }))`), without going into function bodies' statements."""
        stack = [val]
        while stack:
            x = stack.pop()
            if x.type == "statement_block":
                continue
            if x.type == "object":
                for ch in x.named_children:
                    if ch.type == "pair":
                        k, v = ch.child_by_field_name("key"), ch.child_by_field_name("value")
                        if k is not None and v is not None and k.type in ("property_identifier", "string") and \
                                v.type in ("arrow_function", "function_expression", "function"):
                            out.append({"name": _str_val(src, k) if k.type == "string" else _t(src, k),
                                        "kind": "method", "line": ch.start_point[0] + 1, "sig": sig(ch),
                                        "parent": owner})
                    elif ch.type == "method_definition":
                        k = ch.child_by_field_name("name")
                        if k is not None:
                            out.append({"name": _t(src, k), "kind": "method", "line": ch.start_point[0] + 1,
                                        "sig": sig(ch), "parent": owner})
                continue
            stack.extend(x.named_children)

    def visit(n, parent, top):
        t = n.type
        kind = kinds.get(t)
        name = scope = None
        if kind:
            if family == "c" and t == "function_definition":
                name, scope = _c_name(src, n)
            elif t == "type_definition":
                d = n.child_by_field_name("declarator")
                name = _t(src, d) if d is not None and d.type == "type_identifier" else None
            elif t in ("struct_specifier", "class_specifier", "union_specifier", "enum_specifier") \
                    and n.child_by_field_name("body") is None:
                kind = None                             # a use (struct Board *b), not a definition
            else:
                nm = n.child_by_field_name("name")
                name = _t(src, nm) if nm is not None else None
            if kind and t == "type_spec":
                ty = n.child_by_field_name("type")
                kind = {"struct_type": "class", "interface_type": "interface"}.get(ty.type if ty is not None else "",
                                                                                  "type")
            if kind and t == "method_declaration" and family == "go":
                rcv = n.child_by_field_name("receiver")
                tid = next((x for x in _walk(rcv) if x.type == "type_identifier"), None) if rcv is not None else None
                scope = _t(src, tid) if tid is not None else None
        if kind and name:
            par = scope or parent
            if family == "js" and kind == "function" and name[:1].isupper():
                kind = "component"
            if par and kind in ("function", "component") and family != "js":
                kind = "method"
            d = {"name": name, "kind": kind, "line": n.start_point[0] + 1, "sig": sig(n)}
            if par and kind == "method":
                d["parent"] = par
            out.append(d)
        if family == "js" and top and t in ("lexical_declaration", "variable_declaration"):
            for vd in n.named_children:                 # top-level consts and arrow functions
                if vd.type != "variable_declarator":
                    continue
                nm, val = vd.child_by_field_name("name"), vd.child_by_field_name("value")
                if nm is None or nm.type != "identifier":
                    continue
                v = val.type if val is not None else ""
                k = "function" if v in ("arrow_function", "function_expression", "function") else "const"
                if _t(src, nm)[:1].isupper() and (k == "function" or (v == "call_expression" and re.search(
                        r"\b(forwardRef|memo|styled|create\w*)\b", _t(src, val)[:60]))):
                    k = "component"
                out.append({"name": _t(src, nm), "kind": k, "line": vd.start_point[0] + 1, "sig": sig(vd)})
                if val is not None:                     # functions in an object it holds: api.about, a store's init
                    obj_methods(val, _t(src, nm))
        if family == "js" and t in ("function_declaration", "arrow_function", "function_expression",
                                    "generator_function_declaration", "method_definition"):
            return                                      # a function's locals are not the module's API
        new_parent = parent
        if t in _CLASSY:
            ty = n.child_by_field_name("type") if t == "impl_item" else n.child_by_field_name("name")
            new_parent = _t(src, ty) if ty is not None else parent
        child_top = top and t in ("program", "export_statement", "source_file", "translation_unit")
        for ch in n.named_children:
            visit(ch, new_parent, child_top or (top and t == "program"))

    visit(root, None, True)
    return out
