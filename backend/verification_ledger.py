"""Project contracts and version-bound execution receipts for ordinary tool execution.
This is a workflow policy, not an adversarial sandbox or proof of test sufficiency.
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

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def enabled(ctx): return bool(getattr(ctx,"build_test_record",False))

def for_ctx(ctx):
    if not enabled(ctx): return None
    root=getattr(ctx,"verification_root",None) or getattr(ctx,"cwd",None)
    if not root: return None
    import agent_tools
    return Ledger(root,Path(agent_tools.TRACKERS).parent/"verification_ledgers")

class Ledger:
    def __init__(self, root, storage):
        self.adopted=None
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
        return self.state

    def save(self):
        from context_checkpoint import atomic_save
        atomic_save(str(self.path),self.state)

    def confined(self, name):
        if not isinstance(name,str) or not name or Path(name).is_absolute() or ".." in Path(name).parts: raise PolicyError("Use project-relative paths/patterns")
        p=(self.root/name).resolve()
        if not p.is_relative_to(self.root): raise PolicyError("Path escapes project")
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
            self.refresh();mid=args["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",mid):raise PolicyError("Use a stable module ID")
            for key in ("purpose","inputs","outputs","errors","effects"):
                if not isinstance(args[key],str) or not args[key].strip():raise PolicyError("Describe "+key)
            for key in ("sources","tests","checks","dependencies","tracker_ids"):
                if not isinstance(args[key],list) or any(not isinstance(x,str) or not x.strip() for x in args[key]):raise PolicyError("Use string lists for "+key)
            if not args["sources"] or not args["tests"] or not args["checks"]:raise PolicyError("Declare sources, test paths and behavioral checks")
            if args["framework"] not in ("node","pytest"):raise PolicyError("Use node or pytest; these are the supported receipt adapters")
            for p in args["sources"]+args["tests"]:self.confined(p)
            if any(d not in self.state["modules"] or d==mid for d in args["dependencies"]):raise PolicyError("Declare prerequisite modules first; no self dependency")
            old=self.state["modules"].get(mid)
            candidate=dict(copy.deepcopy(args),state="needs_verification")
            graph={k:list(v["dependencies"]) for k,v in self.state["modules"].items()};graph[mid]=candidate["dependencies"]
            def visit(n,seen):
                if n in seen:raise PolicyError("Dependency cycle; define a coherent change cohort or explicit interface")
                for d in graph[n]:visit(d,seen|{n})
            visit(mid,set())
            if old and all(old.get(k)==v for k,v in args.items()):return "Contract unchanged; "+old["state"]
            self.state["modules"][mid]=candidate
            self.state["unowned"]=[p for p in self.state["unowned"] if not self.owners(p)]
            self.save();return "Contract recorded; begin this module before building. Existing code is unverified."

    def begin(self,mid):
        with self.lock:
            self.refresh()
            if mid not in self.state["modules"]:raise PolicyError("Declare module_contract first")
            active=self.state["active"]
            if active and active!=mid and self.state["modules"][active]["state"]!="verified":raise PolicyError("Test and record active module "+active+" before switching")
            m=self.state["modules"][mid]
            if self.state["unowned"]:raise PolicyError("Declare ownership of changed sources: "+", ".join(self.state["unowned"][:8]))
            if any(self.state["modules"][d]["state"]!="verified" for d in m["dependencies"]):raise PolicyError("Verify prerequisite modules before dependent work")
            self.state["active"]=mid
            if m["state"]=="verified":m["state"]="building"
            self.save();return "Build active module "+mid+", run verify_module, then record is automatic."

    def gate_message(self,rel,active):
        """The refusal for a write outside the active module, saying exactly which step unblocks it: small models read
        the generic 'declare/begin a module owning X' as a dead end (a 9B wrote store.py into a module that owned
        data.py, was refused, never wrote data.py, then spent 35 steps fixing the imports of its tests)."""
        owners=self.owners(rel)
        lines=["Build gate: "+rel+" was NOT written."]
        if active:
            m=self.state["modules"][active]
            own=[p for p in m["sources"]+m["tests"]]
            missing=[p for p in m["sources"] if not any(self.root.glob(p))]
            lines.append("The active module '"+active+"' owns only: "+", ".join(own)+".")
            if missing:lines.append("Not written yet: "+", ".join(missing)+" — if "+rel+" was meant to be that file, write "+missing[0]+" instead.")
            if owners:lines.append(rel+" belongs to module '"+owners[0]+"': verify_module('"+active+"') first, then begin_module('"+owners[0]+"').")
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
                if any(self.state["modules"][d]["state"]!="verified" for d in self.state["modules"][active]["dependencies"]):raise PolicyError("Build gate: prerequisite verification changed; reverify before dependent edits")
        return self.inventory()

    def record_changes(self,before):
        after=self.inventory();changed={p for p in before.keys()|after.keys() if before.get(p)!=after.get(p)}
        for p in changed:(self.state.get("drafts") or {}).pop(p,None)
        for p in changed:
            owners=self.owners(p)
            if not owners and p in after:self.state["unowned"]=sorted(set(self.state["unowned"]+[p]))
            for mid in owners:self.state["modules"][mid]["state"]="needs_verification"
        self.save();self.refresh()
        return changed

    def completion_message(self,iid,linked):
        """Why checklist item `iid` can't be marked done yet, and the one step that does it. The old generic refusal
        ('requires current verify_module receipts') made a 27B stop ticking items for the rest of the build."""
        todo=[m["id"] for m in linked if m["state"]!="verified"]
        msg="Build gate: "+iid+" was NOT marked done yet."
        if self.state["unowned"]:
            msg+=(" Changed files no module owns: "+", ".join(self.state["unowned"][:6])+". Add them to a module "
                  "(module_contract with them in sources or tests), then verify it.")
        if todo:
            msg+=(" "+("Module '"+todo[0]+"' is" if len(todo)==1 else "Modules "+", ".join("'"+t+"'" for t in todo)+" are")
                  +" not verified at the current code: run verify_module('"+todo[0]+"').")
        msg+=(" When verification passes, the checklist items those modules are linked to (tracker_ids) are marked "
              "done automatically: there is no need to mark them again.")
        return msg

    def check_tracker(self,updates,tracker):
        self.refresh()
        for u in updates or []:
            if not isinstance(u,dict):continue
            iid=str(u.get("id",""));status=str(u.get("status","")).lower()
            if status in ("done","complete","completed","finished","in_progress","started"):
                linked=[m for m in self.state["modules"].values() if any(t==iid or t.startswith(iid+".") or iid.startswith(t+".") for t in m["tracker_ids"])]
                if status in ("done","complete","completed","finished"):
                    if linked and (self.state["unowned"] or any(m["state"]!="verified" for m in linked)):
                        raise PolicyError(self.completion_message(iid,linked))
                elif linked and any(any(self.state["modules"][d]["state"]!="verified" for d in m["dependencies"]) for m in linked):raise PolicyError("Build gate: prerequisite checks are not verified")

    def summary(self):
        self.refresh()
        return {"root":str(self.root),"active":self.state["active"],"unowned_changes":self.state["unowned"],"modules":[{k:m.get(k) for k in ("id","purpose","inputs","outputs","errors","effects","checks","sources","tests","dependencies","tracker_ids","state","receipt")} for m in self.state["modules"].values()],"latest_receipts":[{k:r.get(k) for k in ("id","module","status","exit","tests","failures","uncovered_checks","report","output","time","scope")} for r in self.state["receipts"][-6:]]}

    def verify(self,mid,ctx):
        with self.lock:
            self.refresh()
            if mid not in self.state["modules"]:raise PolicyError("Declare module_contract first")
            m=self.state["modules"][mid]
            if any(self.state["modules"][d]["state"]!="verified" for d in m["dependencies"]):raise PolicyError("Prerequisite verification is stale or missing")
            paths=[]
            for pattern in m["tests"]:
                paths.extend(str(p) for p in self.root.glob(pattern) if p.is_file() and p.resolve().is_relative_to(self.root))
            if not paths:raise PolicyError("No declared test files exist; build behavioral tests before verify_module")
            rid=uuid.uuid4().hex;report=self.folder/(rid+".xml");output=self.folder/(rid+".log")
            before=self.fingerprint(m);deps={d:self.state["modules"][d].get("receipt") for d in m["dependencies"]}
            dependency_versions={d:self.fingerprint(self.state["modules"][d]) for d in m["dependencies"]}
            if m["framework"]=="node":
                node=shutil.which("node")
                if not node:raise PolicyError("Node is unavailable; install/prepare the real runtime")
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
            proc=subprocess.Popen(argv,cwd=str(self.root),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",creationflags=0x08000000 if os.name=="nt" else 0)
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
            try:cases=list(ET.parse(report).getroot().iter("testcase"))
            except (OSError,ET.ParseError):invalid=True
            tests=[c for c in cases if c.find("skipped") is None]
            # Node treats a plain script as a passing file subtest: it is not a behavioral test.
            if m["framework"]=="node":tests=[c for c in tests if not c.get("name","").endswith((".js",".cjs",".mjs",".ts"))]
            failures=sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
            stable=before==self.fingerprint(m) and all(self.fingerprint(self.state["modules"][d])==v for d,v in dependency_versions.items())
            uncovered=[check for check in m["checks"] if not any(check.casefold() in c.get("name","").casefold() for c in tests)]
            passed=not uncovered and proc.returncode==0 and not stopped and not invalid and bool(tests) and failures==0 and stable
            status="passed" if passed else "uncovered_checks" if uncovered and tests else "interrupted" if stopped else "stale_during_test" if not stable else "no_tests" if not invalid and not tests else "failed" if failures else "execution_error"
            receipt={"id":rid,"module":mid,"status":status,"exit":proc.returncode,"tests":len(tests),"failures":failures,"test_names":[c.get("name") for c in tests],"uncovered_checks":uncovered,"command":argv,"report":str(report),"output":str(output),"time":time.time(),"fingerprint":before,"dependency_receipts":deps,"runtime":argv[0],"scope":"Executed declared tests; semantic adequacy and full gameplay not independently established"}
            self.state["receipts"].append(receipt);m["state"]="verified" if passed else "needs_verification" if stopped else "failing"
            if passed:m.update(receipt=rid,fingerprint=before,dependency_receipts=deps)
            self.save();self.refresh()
            ctx.display={"type":"terminal","shell":"verification","cwd":str(self.root),"command":" ".join(argv),"output":text[-20000:],"exit":proc.returncode}
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
        try:
            out=registry._call_unchecked(name,args)
            if ledger.adopted and isinstance(out,tuple) and out and isinstance(out[0],str):
                rel,mid,key=ledger.adopted          # results are (text, image)
                out=(out[0]+"\n(Build gate: "+rel+" was added to module '"+mid+"' "+key+"; verify_module('"+mid+"') covers it.)",)+tuple(out[1:])
            return out
        finally:ledger.record_changes(before)


def ctx_summary(ctx):
    ledger=for_ctx(ctx)
    if ledger is None:return None
    with ledger.lock:return ledger.summary()


def ctx_brief(ctx):
    """Small recurring status view; module_status retrieves the full contracts."""
    state=ctx_summary(ctx)
    if state is None:return None
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
