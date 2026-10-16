"""Proof that software changes were tested, in one of two modes per project folder (state["mode"]):

  evidence  (the default: existing repos, quick fixes, any language) — writes are never blocked. The ledger tracks
            which code files this chat changed since a passing test run covered them, and a checklist item can't be
            marked done (and the answer is labelled) while such untested changes remain. Any test runner counts:
            run_tests, or the project's own command through the shell (pytest, cargo test, go test, npm test, …).
  modules   (only for a project built from scratch in an empty folder) — the contract → build → verify_module
            ceremony: code files are written inside an active module, and verify_module's receipts gate completion.
            It keeps a small model organised on a large build; on ordinary work it only got in the way (every write
            needed a 12-field contract, and only pytest/node projects could declare one).

Every refusal names the exact call or change that clears it. This is a workflow policy, not an adversarial sandbox or
proof of test sufficiency.
"""
import copy
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
IGNORED = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build"}
MANIFESTS={"package.json","package-lock.json","npm-shrinkwrap.json","requirements.txt","pyproject.toml","uv.lock","poetry.lock","Cargo.toml","Cargo.lock","go.mod","go.sum"}
CODE = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".cs", ".cpp", ".c", ".h", ".java", ".rs", ".go", ".html", ".css", ".json", ".toml", ".yaml", ".yml"}

class PolicyError(ValueError): pass

# files a program or its tests write while running (its data, not its code): a command creating one doesn't need a
# module to own it. Manifests (package.json, …) are code, not data.
RUNTIME_DATA = {".json", ".yaml", ".yml", ".toml", ".csv", ".jsonl"}


def is_runtime_data(rel):
    name=rel.rsplit("/",1)[-1]
    return name not in MANIFESTS and os.path.splitext(name)[1].lower() in RUNTIME_DATA


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def enabled(ctx): return bool(getattr(ctx,"build_test_record",False))

# checklist items about documents or planning, not code: untested code changes don't block marking them done
DOCS_ITEM_RX=re.compile(r"\b(readme|docs?|documentation|prd|design|plan|research|notes?|write-?up|report|changelog)\b",re.I)
# a shell command that runs tests (any common runner)
TEST_CMD_RX=re.compile(r"\b(pytest|unittest|npm(\s+run)?\s+test|pnpm\s+test|yarn\s+test|node\s+--test|jest|vitest|mocha|"
                       r"go\s+test|cargo\s+(test|nextest)|dotnet\s+test|mvn\s+(-\S+\s+)*test|gradlew?\s+(\S+\s+)*test|"
                       r"ctest|make\s+(check|test)|rspec|phpunit|deno\s+test|bun\s+test|swift\s+test)\b",re.I)
# a test command aimed at particular files (a partial run): file names, pytest node ids, -k/-run filters
PARTIAL_RX=re.compile(r"(\S+\.(py|js|mjs|cjs|ts|tsx|go|rs|rb|php)\b|::|\s-k\s|\s-run\s|--filter\b)",re.I)


def _tracker_items(t):
    import agent_tools
    try:return agent_tools.tracker_items(t or {})
    except Exception:return []


def for_ctx(ctx):
    if not enabled(ctx): return None
    root=getattr(ctx,"verification_root",None) or getattr(ctx,"cwd",None)
    if not root: return None
    import agent_tools
    ledger=Ledger(root,Path(agent_tools.TRACKERS).parent/"verification_ledgers")
    ledger.decide_mode(ctx)
    return ledger

class Ledger:
    def __init__(self, root, storage):
        self.adopted=None
        self.adopted_by_command=[]
        self.refused_rel=None
        self.root=Path(root).resolve()
        key=hashlib.sha256(os.path.normcase(str(self.root)).encode()).hexdigest()[:24]
        self.folder=Path(storage)/key; self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/"ledger.json"
        with _LOCKS_GUARD: self.lock=_LOCKS.setdefault(str(self.path),threading.RLock())
        self.state=None
        self.load()

    def load(self):
        if self.path.exists():
            self.state=json.loads(self.path.read_text(encoding="utf-8"))
            if self.state.get("version")!=1 or self.state.get("root")!=str(self.root): raise PolicyError("Invalid verification ledger; preserve and reconcile it")
        else:self.state={"version":1,"root":str(self.root),"modules":{},"active":None,"receipts":[],"unowned":[]}
        self.state["unowned"]=[p for p in self.state.get("unowned") or [] if not is_runtime_data(p)]
        return self.state

    def save(self):
        from context_checkpoint import atomic_save
        atomic_save(str(self.path),self.state)

    # ---------------------------------------------------------------- mode
    @property
    def mode(self):
        return self.state.get("mode") or ("modules" if self.state.get("modules") else "evidence")

    def decide_mode(self,ctx):
        """Once per project folder: the module ceremony only for a from-scratch build (a build request and a folder with
        no code yet); everything else — existing repos, fixes, refactors, scripts — gets evidence mode. A ledger that
        already has modules keeps them."""
        if self.state.get("mode"):return self.state["mode"]
        if self.state.get("modules"):mode="modules"
        elif getattr(ctx,"project_build",False) and not self.inventory():mode="modules"
        else:mode="evidence"
        self.state["mode"]=mode;self.save()
        return mode

    # ---------------------------------------------------------------- evidence mode
    def pending(self,conv=None):
        """Code files changed (in this chat, when conv is given) that no passing test run has covered since."""
        ch=self.state.get("changed") or {}
        return sorted(p for p,v in ch.items() if conv is None or v.get("conv")==conv)

    def note_changes(self,changed,after,conv):
        ch=self.state.setdefault("changed",{})
        for p in changed:
            if p not in after:
                ch.pop(p,None)                        # deleted: nothing left to test
            elif not is_runtime_data(p):
                ch[p]={"t":time.time(),"conv":conv or ""}

    def note_test_run(self,conv,command,passed,failed,full,started,targets=()):
        """A test run finished. A passing run covers what changed before it started: everything for a full run; for a
        run aimed at particular test files, those files and the project modules they import."""
        runs=self.state.setdefault("test_runs",[])
        runs.append({"t":time.time(),"conv":conv or "","command":str(command)[:200],"passed":int(passed or 0),
                     "failed":int(failed or 0),"full":bool(full)})
        del runs[:-10]
        if failed or not passed:
            self.save();return []
        ch=self.state.setdefault("changed",{})
        if full:
            covered=[p for p,v in ch.items() if v.get("t",0)<=started]
        else:
            scope=self._covered_by(targets)
            covered=[p for p,v in ch.items() if v.get("t",0)<=started and p in scope]
        for p in covered:ch.pop(p,None)
        self.save()
        return covered

    def _covered_by(self,targets):
        """Project files a partial test run covers: the test files it named and the modules they import."""
        out=set();inv=None
        for t in targets or ():
            rel=str(t).replace("\\","/").split("::")[0]
            p=(self.root/rel) if not Path(rel).is_absolute() else Path(rel)
            try:rel=p.resolve().relative_to(self.root).as_posix()
            except (ValueError,OSError):continue
            out.add(rel)
            # pytest loads conftest.py files from the test's folder up to the root without an import: a run covers
            # them too (run b178328a: 15/15 green, every tick refused over tests/conftest.py)
            d=Path(rel).parent
            while True:
                cf=(d/"conftest.py").as_posix().lstrip("./") if str(d)!="." else "conftest.py"
                if (self.root/cf).is_file():out.add(cf)
                if str(d) in (".",""):break
                d=d.parent
            try:src=p.read_text(encoding="utf-8",errors="replace")
            except OSError:continue
            names=set(re.findall(r"^\s*(?:from|import)\s+([\w.]+)",src,re.M))
            names|={n.lstrip("./") for n in re.findall(r"(?:require\(|from\s+)['\"](\.{1,2}/[^'\"]+)['\"]",src)}
            inv=inv if inv is not None else list(self.inventory())
            for n in names:
                stem=n.replace(".","/") if "/" not in n else n
                for cand in inv:
                    base=os.path.splitext(cand)[0]
                    if base==stem or base.endswith("/"+stem) or base.rsplit("/",1)[-1]==stem.rsplit("/",1)[-1]:
                        out.add(cand)
        return out

    def evidence_message(self,iid,files):
        shown=", ".join(files[:6])+(" and "+str(len(files)-6)+" more" if len(files)>6 else "")
        last=(self.state.get("test_runs") or [{}])[-1]
        if last.get("passed") and not last.get("failed") and not last.get("full"):
            # the last green run was aimed at particular files: say how to make it cover everything
            return ("Not marked done: "+iid+" — your last passing run covered only the test files it named, and "+shown
                    +" changed since a run covered "+("it" if len(files)==1 else "them")+". Run the whole suite once: "
                    "run_tests(path='"+str(self.root)+"') (the project folder, not a single test file). Once it "
                    "passes, mark "+iid+" done again.")
        return ("Not marked done: "+iid+" — "+shown+" changed since a passing test run last covered "
                +("it" if len(files)==1 else "them")+". Run the project's tests (run_tests, or its own command such "
                "as pytest, npm test, cargo test or go test); once they pass, mark "+iid+" done again. If there is no "
                "test for this yet, add one that exercises the change and run it. If it truly can't be tested "
                "automatically, set "+iid+" to 'blocked' with the reason instead.")

    def confined(self, name):
        if not isinstance(name,str) or not name or Path(name).is_absolute() or ".." in Path(name).parts: raise PolicyError("Not recorded: "+repr(name)[:80]+" must be a path relative to the project folder (no drive letter, no '..'), e.g. 'src/app.py' or 'tests/test_*.py'.")
        p=(self.root/name).resolve()
        if not p.is_relative_to(self.root): raise PolicyError("Not recorded: "+repr(name)[:80]+" points outside the project folder "+str(self.root)+"; use a path inside it.")
        return p

    def inventory(self):
        result={}
        if not self.root.is_dir(): return result
        for base,dirs,files in os.walk(self.root,followlinks=False):
            dirs[:]=[d for d in dirs if d not in IGNORED and not (Path(base)/d).is_symlink()]
            for name in files:
                p=Path(base)/name
                rel=p.relative_to(self.root).as_posix()
                if p.is_symlink() or (p.suffix.lower() not in CODE and p.name not in MANIFESTS and not self.owners(rel)):continue
                result[rel]=sha(p)
        return result

    def fingerprint(self, module, inventory=None):
        inv=inventory if inventory is not None else self.inventory()
        patterns=module["sources"]+module["tests"]
        files={p:h for p,h in inv.items() if any(fnmatch.fnmatchcase(p,pat) for pat in patterns)}
        # Pin project configuration/lockfiles even if not declared as owned source.
        for p,h in inv.items():
            if Path(p).name in MANIFESTS:files[p]=h
        return {"files":files,"contract":hashlib.sha256(json.dumps({k:module[k] for k in ("inputs","outputs","errors","effects","checks","sources","tests","dependencies","framework")},sort_keys=True).encode()).hexdigest()}

    def refresh(self):
        self.load(); inv=self.inventory()
        for m in self.state["modules"].values():
            if m["state"]=="verified" and m.get("fingerprint")!=self.fingerprint(m,inv):m["state"]="stale"
        changed=True
        while changed:
            changed=False
            for m in self.state["modules"].values():
                if m["state"]=="verified" and any(self.state["modules"][d]["state"]!="verified" or self.state["modules"][d].get("receipt")!=m.get("dependency_receipts",{}).get(d) for d in m["dependencies"]):
                    m["state"]="stale";changed=True
        self.state["unowned"]=[p for p in self.state.get("unowned",[]) if p in inv and not self.owners(p)]
        self.save();return inv

    def owners(self, path):
        return [mid for mid,m in self.state["modules"].items() if any(fnmatch.fnmatchcase(path,pat) for pat in m["sources"]+m["tests"])]

    def blocking(self, exclude=None):
        return [mid for mid,m in self.state["modules"].items() if mid!=exclude and m["state"]!="verified"]

    def declare(self,args):
        with self.lock:
            self.refresh();args=self._normal_contract(args);mid=args["id"]
            for p in args["sources"]+args["tests"]:self.confined(p)
            bad=[d for d in args["dependencies"] if d not in self.state["modules"] or d==mid]
            if bad:raise PolicyError("Not recorded: dependencies "+", ".join("'"+d+"'" for d in bad)+" are not declared modules"
                                     +(" (a module can't depend on itself)" if mid in bad else "")+". Declared modules: "
                                     +(", ".join(self.state["modules"]) or "none")+". Remove them from dependencies, or "
                                     "declare those modules first with module_contract.")
            old=self.state["modules"].get(mid)
            candidate=dict(copy.deepcopy(args),state="needs_verification")
            graph={k:list(v["dependencies"]) for k,v in self.state["modules"].items()};graph[mid]=candidate["dependencies"]
            def visit(n,seen):
                if n in seen:raise PolicyError("Not recorded: dependencies form a cycle through '"+n+"'. Remove one of those dependencies (a module may only depend on modules declared before it), or merge the modules into one.")
                for d in graph[n]:visit(d,seen|{n})
            visit(mid,set())
            if old and all(old.get(k)==v for k,v in args.items()):return "Contract unchanged; "+old["state"]
            self.state["modules"][mid]=candidate
            self.state["unowned"]=[p for p in self.state["unowned"] if not self.owners(p)]
            self.save();return "Contract recorded; begin this module before building. Existing code is unverified."

    def _normal_contract(self,a):
        """A contract with only id, sources and tests required (the other fields are optional notes). The framework is
        inferred from the test files; other languages give test_command (e.g. 'cargo test'). Each refusal says what
        to send instead."""
        a=dict(a or {})
        mid=a.get("id")
        if not isinstance(mid,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",mid):
            raise PolicyError("Not recorded: id must be a short name of letters, digits, _ or - (no spaces), e.g. '"
                              +(re.sub(r"[^A-Za-z0-9_-]+","_",str(mid or "module")).strip("_")[:40] or "module")
                              +"'; you sent "+repr(mid)[:60]+".")
        for key in ("purpose","inputs","outputs","errors","effects"):
            a[key]=str(a.get(key) or "").strip()
        for key in ("sources","tests","checks","dependencies","tracker_ids"):
            v=a.get(key)
            if v in (None,""):v=[]
            if isinstance(v,str):v=[v]
            if not isinstance(v,list) or any(not isinstance(x,str) or not x.strip() for x in v):
                raise PolicyError("Not recorded: "+key+" must be a list of strings, e.g. "+key+"=['"
                                  +("app.py" if key=="sources" else "tests/test_app.py" if key=="tests" else "...")
                                  +"']; you sent "+repr(v)[:80]+".")
            a[key]=[x.strip() for x in v]
        if not a["sources"]:
            raise PolicyError("Not recorded: list the files this module builds in sources, e.g. sources=['app.py'].")
        if not a["tests"]:
            raise PolicyError("Not recorded: list its test files in tests, e.g. tests=['tests/test_"
                              +os.path.splitext(os.path.basename(a["sources"][0]))[0]+".py'] (they may not exist yet).")
        fw=str(a.get("framework") or "").strip().lower()
        if not fw:
            exts={os.path.splitext(t)[1].lower() for t in a["tests"]}
            fw="pytest" if ".py" in exts else "node" if exts & {".js",".cjs",".mjs",".ts"} else ("command" if a.get("test_command") else "")
        if fw=="command" and not str(a.get("test_command") or "").strip():
            raise PolicyError("Not recorded: framework 'command' needs test_command, the shell command that runs this "
                              "module's tests (e.g. 'cargo test', 'go test ./...', 'dotnet test').")
        if fw not in ("pytest","node","command"):
            raise PolicyError("Not recorded: can't tell how to run "+", ".join(a["tests"][:3])+". Use Python test files "
                              "(pytest), JavaScript ones (node --test), or give test_command='<the shell command that "
                              "runs them>' for any other language.")
        a["framework"]=fw
        a["test_command"]=str(a.get("test_command") or "").strip()
        return a

    def begin(self,mid):
        with self.lock:
            self.refresh()
            if mid not in self.state["modules"]:raise PolicyError("No module '"+mid+"' yet. Declared: "+(", ".join(self.state["modules"]) or "none")+". Declare it first: module_contract(id='"+mid+"', sources=[...], tests=[...]).")
            active=self.state["active"]
            if active and active!=mid and not self.may_switch(active,mid):
                raise PolicyError("Build gate: module '"+active+"' is not verified, so you can't switch to '"+mid+"' yet. "
                                  "Run verify_module('"+active+"') and fix what fails. You can switch while it fails only "
                                  "to a module that is already verified or that '"+active+"' depends on (to fix it).")
            m=self.state["modules"][mid]
            if self.state["unowned"]:raise PolicyError("Not started: changed files no module owns: "+", ".join(self.state["unowned"][:8])+". Add them to a module's sources or tests with module_contract (same id, the files added), then call begin_module('"+mid+"') again.")
            if any(self.state["modules"][d]["state"]!="verified" for d in m["dependencies"]):raise PolicyError("Not started: '"+mid+"' depends on "+", ".join("'"+d+"'" for d in m["dependencies"] if self.state["modules"][d]["state"]!="verified")+", not verified at the current code. Run verify_module on "+("it" if sum(self.state["modules"][d]["state"]!="verified" for d in m["dependencies"])==1 else "each")+" first (begin_module on it to fix what fails).")
            self.state["active"]=mid
            if m["state"]=="verified":m["state"]="building"
            self.save();return "Build active module "+mid+", run verify_module, then record is automatic."

    def may_switch(self,active,mid):
        """Leave the active module for `mid`? Always once the active one is verified. While it fails, only to a module
        that is already verified (reopening it to fix something the active module needs) or one it depends on: a 27B's
        client failed because the verified server returned too little, and 'verify the client first' could never pass.
        The failing module stays unverified and still blocks completion of its checklist items."""
        a=self.state["modules"][active]
        if a["state"]=="verified":return True
        return self.state["modules"][mid]["state"]=="verified" or mid in (a.get("dependencies") or [])

    def gate_message(self,rel,active):
        """The refusal for a write outside the active module, saying exactly which step unblocks it: small models read
        the generic 'declare/begin a module owning X' as a dead end (a 9B wrote store.py into a module that owned
        data.py, was refused, never wrote data.py, then spent 35 steps fixing the imports of its tests)."""
        owners=self.owners(rel)
        lines=["Build gate: "+rel+" was not written yet."]
        if active:
            m=self.state["modules"][active]
            own=[p for p in m["sources"]+m["tests"]]
            missing=[p for p in m["sources"] if not any(self.root.glob(p))]
            lines.append("The active module '"+active+"' owns only: "+", ".join(own)+".")
            if missing:lines.append("Not written yet: "+", ".join(missing)+" — if "+rel+" was meant to be that file, write "+missing[0]+" instead.")
            if owners:
                o=owners[0]
                if self.may_switch(active,o):
                    lines.append(rel+" belongs to module '"+o+"'. To change it, either call begin_module('"+o+"'), fix it "
                                 "there and verify_module('"+o+"'), then begin_module('"+active+"') again; or, if this "
                                 "change is part of '"+active+"''s work, call module_contract again with id '"+active+
                                 "' and the same fields, adding '"+rel+"' to sources, then write it.")
                else:
                    lines.append(rel+" belongs to module '"+o+"'. Either verify_module('"+active+"') and fix what fails, "
                                 "then begin_module('"+o+"'); or, if this change is part of '"+active+"''s work, call "
                                 "module_contract again with id '"+active+"' and the same fields, adding '"+rel+
                                 "' to sources, then write it.")
            else:lines.append("To build "+rel+" as part of '"+active+"', call module_contract again with id '"+active+"' and the same fields, adding '"+rel+"' to sources, then write it.")
        elif owners:
            lines.append(rel+" belongs to module '"+owners[0]+"': call begin_module('"+owners[0]+"'), then write it.")
        else:
            lines.append("No module owns it yet: call module_contract(id=..., sources=['"+rel+"', ...], tests=[...]) and begin_module(id), then write it.")
        lines.append("Read and test tools remain available.")
        return " ".join(lines)

    def adopt(self,mid,rel):
        """A new file no module owns, written while a module is active, joins that module (a test file to its tests,
        anything else to its sources) instead of being refused. A 9B run spent 9 of 29 calls on refusals for helper
        and test files the contract hadn't listed, and looped re-declaring contracts. Files that another module owns
        stay refused: that boundary is real."""
        m=self.state["modules"][mid]
        name=rel.rsplit("/",1)[-1]
        key="tests" if (name.startswith("test_") or name.endswith(("_test.py",".test.js",".test.ts",".spec.js",".spec.ts"))
                        or "/tests/" in "/"+rel or "/test/" in "/"+rel) else "sources"
        m[key]=list(m[key])+[rel]
        if m["state"]=="verified":m["state"]="needs_verification"
        self.state["unowned"]=[x for x in self.state["unowned"] if x!=rel]
        self.adopted=(rel,mid,key)
        self.save()

    def keep_draft(self,rel,name,args):
        """A write the gate refused is kept, not lost: a 27B generated server.py and client.py in full before declaring
        a module, both were refused, and it had to generate them again (minutes of output on an 8 GB card). The latest
        refused call per file is kept and replayed when a module that owns the file begins."""
        drafts=self.state.setdefault("drafts",{})
        drafts.pop(rel,None)
        drafts[rel]={"name":name,"args":args}
        while len(drafts)>8:drafts.pop(next(iter(drafts)))
        self.save()

    def due_drafts(self):
        """-> [(rel, name, args)] kept writes that the active module now owns (removed from the store)."""
        active=self.state.get("active");drafts=self.state.get("drafts") or {}
        if not active:return []
        due=[(rel,d["name"],d["args"]) for rel,d in drafts.items() if active in self.owners(rel)]
        for rel,_n,_a in due:drafts.pop(rel,None)
        if due:self.save()
        return due

    def preflight(self,name,args,kind):
        if self.mode=="evidence":
            return self.inventory()                   # writes are never blocked: changes are tracked afterwards
        self.refresh()
        if kind=="write" and args.get("path"):
            p=Path(args["path"])
            if not p.is_absolute():p=self.root/p
            p=p.resolve()
            if p.is_relative_to(self.root) and (p.suffix.lower() in CODE or p.name in MANIFESTS or self.owners(p.relative_to(self.root).as_posix())):
                rel=p.relative_to(self.root).as_posix();active=self.state["active"]
                if active and not self.owners(rel):self.adopt(active,rel)
                if not active or active not in self.owners(rel):
                    self.refused_rel=rel
                    raise PolicyError(self.gate_message(rel,active))
                if any(self.state["modules"][d]["state"]!="verified" for d in self.state["modules"][active]["dependencies"]):raise PolicyError("Build gate: not written — module '"+active+"' depends on "+", ".join("'"+d+"'" for d in self.state["modules"][active]["dependencies"] if self.state["modules"][d]["state"]!="verified")+", which changed since it was verified. Run verify_module on it again, then write this.")
        return self.inventory()

    def record_changes(self,before,kind="write",conv=None):
        """Files changed by a call. A new file no module owns becomes 'unowned' (it blocks begin_module and checklist
        completion until a module claims it) — except when a COMMAND made it: runtime data a program or its tests
        write (expenses.json) is ignored, and code a command creates (a downloaded library, scaffolding) joins the
        active module. Downloaded three.js files and an npm package.json blocked whole builds as 'unowned'."""
        # the state as saved now, not as loaded before the call: a test run's receipt is written by another Ledger
        # object during the call, and saving this one's stale copy erased it (run 2405352c: 'Not marked done' after a
        # passing 39-test run; three checklist updates refused, then the model stopped updating it)
        self.load()
        after=self.inventory();changed={p for p in before.keys()|after.keys() if before.get(p)!=after.get(p)}
        if self.mode=="evidence":
            self.note_changes(changed,after,conv)
            self.save()
            return changed
        for p in changed:(self.state.get("drafts") or {}).pop(p,None)
        made=kind not in ("write",None)
        for p in sorted(changed):
            owners=self.owners(p)
            if not owners and p in after:
                if made and is_runtime_data(p):
                    continue
                if made and self.state.get("active"):
                    self.adopt(self.state["active"],p)
                    self.adopted_by_command.append(p)
                    continue
                self.state["unowned"]=sorted(set(self.state["unowned"]+[p]))
            for mid in owners:self.state["modules"][mid]["state"]="needs_verification"
        self.save();self.refresh()
        return changed

    def completion_message(self,iid,linked):
        """Why checklist item `iid` can't be marked done yet, and the one step that does it. The old generic refusal
        ('requires current verify_module receipts') made a 27B stop ticking items for the rest of the build."""
        todo=[m["id"] for m in linked if m["state"]!="verified"]
        msg="Build gate: "+iid+" was not marked done yet."
        if self.state["unowned"]:
            msg+=(" Changed files no module owns: "+", ".join(self.state["unowned"][:6])+". Add them to a module "
                  "(module_contract with them in sources or tests), then verify it.")
        if todo:
            msg+=(" "+("Module '"+todo[0]+"' is" if len(todo)==1 else "Modules "+", ".join("'"+t+"'" for t in todo)+" are")
                  +" not verified at the current code: run verify_module('"+todo[0]+"').")
        msg+=(" When verification passes, the checklist items those modules are linked to (tracker_ids) are marked "
              "done automatically: there is no need to mark them again.")
        return msg

    def check_tracker(self,updates,tracker,conv=None):
        if self.mode=="evidence":
            files=self.pending(conv)
            if not files:return
            items={i:text for i,text,_s,_m in _tracker_items(tracker)}
            for u in updates or []:
                if not isinstance(u,dict):continue
                iid=str(u.get("id",""));status=str(u.get("status","")).lower()
                if status in ("done","complete","completed","finished") and not DOCS_ITEM_RX.search(str(items.get(iid,""))):
                    raise PolicyError(self.evidence_message(iid,files))
            return
        self.refresh()
        for u in updates or []:
            if not isinstance(u,dict):continue
            iid=str(u.get("id",""));status=str(u.get("status","")).lower()
            if status in ("done","complete","completed","finished","in_progress","started"):
                linked=[m for m in self.state["modules"].values() if any(t==iid or t.startswith(iid+".") or iid.startswith(t+".") for t in m["tracker_ids"])]
                if status in ("done","complete","completed","finished"):
                    if linked and (self.state["unowned"] or any(m["state"]!="verified" for m in linked)):
                        raise PolicyError(self.completion_message(iid,linked))
                elif linked and any(any(self.state["modules"][d]["state"]!="verified" for d in m["dependencies"]) for m in linked):raise PolicyError("Build gate: "+iid+" can't start yet: its module depends on modules not verified at the current code ("+", ".join(sorted({d for m in linked for d in m["dependencies"] if self.state["modules"][d]["state"]!="verified"}))+"). Run verify_module on them first.")

    def summary(self,conv=None):
        if self.mode=="evidence":
            return {"root":str(self.root),"mode":"evidence","untested_changes":self.pending(conv),
                    "last_test_runs":[{k:r.get(k) for k in ("command","passed","failed","full")}
                                      for r in (self.state.get("test_runs") or [])[-3:]]}
        self.refresh()
        return {"root":str(self.root),"mode":"modules","active":self.state["active"],"unowned_changes":self.state["unowned"],"modules":[{k:m.get(k) for k in ("id","purpose","inputs","outputs","errors","effects","checks","sources","tests","dependencies","tracker_ids","state","receipt")} for m in self.state["modules"].values()],"latest_receipts":[{k:r.get(k) for k in ("id","module","status","exit","tests","failures","uncovered_checks","report","output","time","scope")} for r in self.state["receipts"][-6:]]}

    def verify(self,mid,ctx):
        with self.lock:
            self.refresh()
            if mid not in self.state["modules"]:raise PolicyError("No module '"+mid+"' to verify. Declared: "+(", ".join(self.state["modules"]) or "none")+". Declare it with module_contract first.")
            m=self.state["modules"][mid]
            stale=[d for d in m["dependencies"] if self.state["modules"][d]["state"]!="verified"]
            if stale:raise PolicyError("Not run: '"+mid+"' depends on "+", ".join("'"+d+"'" for d in stale)+", not verified at the current code. Run verify_module('"+stale[0]+"') first.")
            paths=[]
            for pattern in m["tests"]:
                paths.extend(str(p) for p in self.root.glob(pattern) if p.is_file() and p.resolve().is_relative_to(self.root))
            if not paths:raise PolicyError("Not run: none of '"+mid+"'s test files exist yet ("+", ".join(m["tests"][:4])+"). Write the tests there — or, if they live elsewhere, call module_contract again with their real paths in tests — then verify_module('"+mid+"').")
            rid=uuid.uuid4().hex;report=self.folder/(rid+".xml");output=self.folder/(rid+".log")
            before=self.fingerprint(m);deps={d:self.state["modules"][d].get("receipt") for d in m["dependencies"]}
            dependency_versions={d:self.fingerprint(self.state["modules"][d]) for d in m["dependencies"]}
            shell=False
            if m.get("framework")=="command":
                argv=m["test_command"];shell=True      # any runner: exit code + counts parsed from its output
            elif m["framework"]=="node":
                node=shutil.which("node")
                if not node:raise PolicyError("Not run: Node.js isn't installed on this PC, so node --test can't run "
                                              "these tests. Install Node.js, or give this module a test_command that "
                                              "runs its tests another way.")
                argv=[node,"--test","--test-reporter=junit","--test-reporter-destination="+str(report),*sorted(set(paths))]
            else:
                if getattr(sys,"frozen",False):
                    import paths as runtime
                    py=runtime.python_cmd()
                else:
                    exe=Path(sys.executable)
                    py=[str(exe.with_name("python.exe")) if exe.name.lower()=="pythonw.exe" else str(exe)]
                argv=[*py,"-m","pytest",*sorted(set(paths)),"--junitxml="+str(report),"-q","-p","no:cacheprovider"]
            import subprocess
            proc=subprocess.Popen(argv,cwd=str(self.root),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",shell=shell,creationflags=0x08000000 if os.name=="nt" else 0)
            stopped=False
            while True:
                try:text,_=proc.communicate(timeout=.5);break
                except subprocess.TimeoutExpired:
                    if ctx.should_stop():
                        stopped=True;proc.terminate()
                        try:text,_=proc.communicate(timeout=5)
                        except subprocess.TimeoutExpired:proc.kill();text,_=proc.communicate()
                        break
            output.write_text(text,encoding="utf-8")
            cases=[];invalid=False
            if m.get("framework")=="command":
                import agent_tools
                n_pass,n_fail=agent_tools.test_counts(text)
                # no per-test report: stand-in cases from the counts, and checks are matched against the output
                cases=[ET.Element("testcase",name="test "+str(i)) for i in range(max(n_pass,1 if proc.returncode==0 else 0))]
                failures=n_fail or (0 if proc.returncode==0 else 1)
                tests=cases
                uncovered=[check for check in m["checks"] if check.casefold() not in text.casefold()]
            else:
                try:cases=list(ET.parse(report).getroot().iter("testcase"))
                except (OSError,ET.ParseError):invalid=True
                tests=[c for c in cases if c.find("skipped") is None]
                # Node treats a plain script as a passing file subtest: it is not a behavioral test.
                if m["framework"]=="node":tests=[c for c in tests if not c.get("name","").endswith((".js",".cjs",".mjs",".ts"))]
                failures=sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
                uncovered=[check for check in m["checks"] if not any(check.casefold() in c.get("name","").casefold() for c in tests)]
            stable=before==self.fingerprint(m) and all(self.fingerprint(self.state["modules"][d])==v for d,v in dependency_versions.items())
            passed=not uncovered and proc.returncode==0 and not stopped and not invalid and bool(tests) and failures==0 and stable
            status="passed" if passed else "uncovered_checks" if uncovered and tests else "interrupted" if stopped else "stale_during_test" if not stable else "no_tests" if not invalid and not tests else "failed" if failures else "execution_error"
            receipt={"id":rid,"module":mid,"status":status,"exit":proc.returncode,"tests":len(tests),"failures":failures,"test_names":[c.get("name") for c in tests],"uncovered_checks":uncovered,"command":argv,"report":str(report),"output":str(output),"time":time.time(),"fingerprint":before,"dependency_receipts":deps,"runtime":(argv if isinstance(argv,str) else argv[0]),"scope":"Executed declared tests; semantic adequacy and full gameplay not independently established"}
            self.state["receipts"].append(receipt);m["state"]="verified" if passed else "needs_verification" if stopped else "failing"
            if passed:m.update(receipt=rid,fingerprint=before,dependency_receipts=deps)
            self.save();self.refresh()
            ctx.display={"type":"terminal","shell":"verification","cwd":str(self.root),"command":(argv if isinstance(argv,str) else " ".join(argv)),"output":text[-20000:],"exit":proc.returncode}
            return json.dumps({k:v for k,v in receipt.items() if k!="fingerprint"},ensure_ascii=False)+"\n"+text[-7000:]


DRAFT_TOOLS={"write_file","edit_file"}


def _replay_drafts(registry,ledger,out):
    """After begin_module: write the kept drafts this module owns, through the normal tool path (code check, backup,
    diff), and say what happened in the begin_module result."""
    with ledger.lock:
        ledger.refresh();due=ledger.due_drafts()
    if not due or not (isinstance(out,tuple) and out and isinstance(out[0],str)):return out
    notes=[]
    for rel,name,args in due:
        try:
            text,_img=registry.call(name,args)
            notes.append("Wrote your kept "+name+" for "+rel+": "+str(text).split("\n",1)[0])
        except Exception as exc:          # a stale edit, a changed file: say so; the model writes it again
            notes.append("Your kept "+name+" for "+rel+" could not be applied ("+str(exc)[:200]+"); write it again.")
    return (out[0]+"\n"+"\n".join(notes),)+tuple(out[1:])


def invoke(registry,name,args):
    ledger=for_ctx(registry.ctx)
    if ledger is None:return registry._call_unchecked(name,args)
    t=registry.get(name) or {};kind=t.get("kind")
    # One shared policy around every actual mutation, including native batches/prefetch.
    if name=="begin_module":
        out=registry._call_unchecked(name,args)
        return _replay_drafts(registry,ledger,out)
    if name in {"module_contract","verify_module","module_status"} or kind not in {"write","exec","input","mcp"}:
        return registry._call_unchecked(name,args)
    with ledger.lock:
        ledger.adopted=None;ledger.refused_rel=None
        try:before=ledger.preflight(name,args,kind)
        except PolicyError as exc:
            rel=ledger.refused_rel
            if kind=="write" and rel and isinstance(args,dict) and name in DRAFT_TOOLS:
                ledger.keep_draft(rel,name,args)
                raise PolicyError(str(exc)+" Your "+name+" for "+rel+" is kept: when a module that owns it begins "
                                  "(begin_module), it is written then automatically, so there is no need to write it again.") from exc
            raise
        ledger.adopted_by_command=[]
        out=None
        try:
            out=registry._call_unchecked(name,args)
            if ledger.adopted and isinstance(out,tuple) and out and isinstance(out[0],str):
                rel,mid,key=ledger.adopted          # results are (text, image)
                out=(out[0]+"\n(Build gate: "+rel+" was added to module '"+mid+"' "+key+"; verify_module('"+mid+"') covers it.)",)+tuple(out[1:])
        finally:ledger.record_changes(before,kind,getattr(registry.ctx,"conv_id",None))
        made=ledger.adopted_by_command
        if made and isinstance(out,tuple) and out and isinstance(out[0],str):
            mid=ledger.state.get("active")
            out=(out[0]+"\n(Build gate: "+", ".join(made[:8])+(" and "+str(len(made)-8)+" more" if len(made)>8 else "")
                 +" made by this command "+("was" if len(made)==1 else "were")+" added to module '"+str(mid)
                 +"'; verify_module('"+str(mid)+"') covers "+("it" if len(made)==1 else "them")+".)",)+tuple(out[1:])
        return out


def ctx_summary(ctx):
    ledger=for_ctx(ctx)
    if ledger is None:return None
    with ledger.lock:return ledger.summary(getattr(ctx,"conv_id",None))


def ctx_brief(ctx):
    """Small recurring status view; module_status retrieves the full contracts."""
    state=ctx_summary(ctx)
    if state is None:return None
    if state.get("mode")=="evidence":return state
    modules=state.get("modules") or []
    return {"active":state.get("active"),
            "unowned_changes":state.get("unowned_changes") or [],
            "modules":[{"id":m.get("id"),"state":m.get("state"),
                        "receipt":m.get("receipt"),"dependencies":m.get("dependencies") or []}
                       for m in modules],
            "latest_receipts":[{"id":r.get("id"),"module":r.get("module"),
                                "status":r.get("status"),"failures":r.get("failures"),
                                "uncovered_checks":r.get("uncovered_checks")}
                               for r in state.get("latest_receipts",[])[-2:]],
            "full_details":"module_status"}
