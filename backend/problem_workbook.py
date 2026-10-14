"""Durable questions and executed evidence, shared by normal Registry calls.

This controller verifies provenance and freshness, not the truth of model
interpretations. It neither starts workers nor imposes a run/step budget.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from context_checkpoint import atomic_save

TOOLS = {"problem_plan", "problem_experiment", "problem_record", "problem_status"}
CONTROL = TOOLS | {"ask_user", "available_tools", "module_status", "module_contract", "begin_module"}
_locks = {}
_guard = threading.Lock()
_local = threading.local()


class PolicyError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PolicyError(f"{label} must be nonempty text, at most {limit} characters")
    return value.strip()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise PolicyError("Use a stable ID of 1-64 letters, digits, underscores or hyphens")
    return value


def for_ctx(ctx):
    if not getattr(ctx, "problem_workbook", False) or not getattr(ctx, "conv_id", None):
        return None
    import agent_tools
    return Workbook(ctx.conv_id, Path(agent_tools.TRACKERS).parent / "problem_workbooks")


class Workbook:
    def __init__(self, conversation, storage):
        self.folder = Path(storage) / digest(conversation)[:24]
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "state.json"
        with _guard:
            self.lock = _locks.setdefault(str(self.path), threading.RLock())
        self.load()

    def load(self):
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {
            "version": 1, "goal": None, "constraints": [], "acceptance": {},
            "questions": {}, "active": None, "pending": None, "gate": None,
            "recent": [], "failed_approaches": [], "events": [], "evidence": [],
            "requests": [],
        }
        if self.state.get("version") != 1:
            raise PolicyError("Unknown workbook version; preserve it for migration")

    def save(self):
        atomic_save(str(self.path), self.state)

    def event(self, kind, **fields):
        self.state["events"].append(dict(kind=kind, time=time.time(), **fields))

    def evidence(self, eid):
        identifier(eid)
        if eid not in self.state["evidence"]:
            raise PolicyError("Unknown controller evidence ID")
        return json.loads((self.folder / (eid + ".json")).read_text(encoding="utf-8"))

    @staticmethod
    def versions(paths):
        return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).is_file() else None for p in paths}

    def fresh(self, evidence):
        if evidence["before"] != evidence["after"] or evidence["after"] != self.versions(evidence["after"]):
            return False
        binding = (evidence.get("execution") or {}).get("ledger_binding")
        if binding:
            from verification_ledger import Ledger
            ledger = Ledger(binding["root"], binding["storage"])
            with ledger.lock:
                ledger.refresh()
                module = ledger.state["modules"].get(binding["module"])
                if not module or ledger.fingerprint(module) != binding["fingerprint"]:
                    return False
                if any(ledger.state["modules"][dep]["state"] != "verified" or
                       ledger.state["modules"][dep].get("receipt") != rid
                       for dep, rid in binding["dependencies"].items()):
                    return False
        return True

    def refresh(self):
        self.load()
        changed = True
        while changed:
            changed = False
            for q in self.state["questions"].values():
                if q["status"] != "resolved":
                    continue
                stale = any(not self.fresh(self.evidence(e)) for e in q["evidence"])
                stale |= any(self.state["questions"][d]["status"] != "resolved" for d in q["dependencies"])
                if stale:
                    q["status"] = "open"
                    self.event("reopened", question=q["id"], reason="Evidence inputs or prerequisites changed")
                    changed = True
        for item in self.state["acceptance"].values():
            if item.get("evidence") and (any(not self.fresh(self.evidence(e)) for e in item["evidence"]) or
                    any(q["status"] != "resolved" for q in self.state["questions"].values())):
                item["evidence"] = []
        self.save()

    def plan(self, a):
        with self.lock:
            self.refresh()
            if self.state["pending"]:
                raise PolicyError("Execute and record the pending experiment before revising the plan")
            goal = text(a["goal"], "goal")
            if self.state["goal"] and goal != self.state["goal"]:
                raise PolicyError("Preserve the original goal; a new goal requires a new user task")
            constraints = a["constraints"]
            if not isinstance(constraints, list):
                raise PolicyError("constraints must be a list")
            constraints = [text(v, "constraint") for v in constraints]
            if any(v not in constraints for v in self.state["constraints"]):
                raise PolicyError("Do not drop existing constraints")
            acceptance = copy.deepcopy(self.state["acceptance"])
            for item in a["acceptance"]:
                iid = identifier(item["id"])
                value = text(item["condition"], "acceptance condition")
                if iid in acceptance and acceptance[iid]["condition"] != value:
                    raise PolicyError("Existing acceptance conditions cannot be silently rewritten")
                acceptance.setdefault(iid, {"condition": value, "evidence": []})
            if not acceptance:
                raise PolicyError("Define observable acceptance conditions")
            questions = copy.deepcopy(self.state["questions"])
            for item in a["questions"]:
                qid = identifier(item["id"])
                old = questions.get(qid)
                q = {"id": qid, "question": text(item["question"], "question"),
                     "why": text(item["why"], "connection to goal"),
                     "hypotheses": item["hypotheses"], "dependencies": item["dependencies"],
                     "acceptance": item.get("acceptance", [])}
                if not isinstance(q["acceptance"], list) or any(v not in acceptance for v in q["acceptance"]):
                    raise PolicyError("Integration questions must reference existing acceptance condition IDs")
                if not isinstance(q["hypotheses"], list) or len(q["hypotheses"]) < 2:
                    raise PolicyError("Provide at least two distinguishable explanations, including an unknown alternative if needed")
                q["hypotheses"] = [text(h, "hypothesis") for h in q["hypotheses"]]
                if len(set(q["hypotheses"])) != len(q["hypotheses"]):
                    raise PolicyError("Hypotheses must differ")
                if not isinstance(q["dependencies"], list):
                    raise PolicyError("dependencies must be question IDs")
                if old and any(old[k] != v for k, v in q.items()):
                    if old["status"] == "resolved":
                        raise PolicyError("Reopen with contradictory evidence before revising a resolved question")
                    self.event("question_revised", previous=old)
                questions[qid] = dict(q, status=old["status"] if old else "open",
                                     evidence=old["evidence"] if old else [],
                                     conclusion=old.get("conclusion", "") if old else "")
            def visit(qid, seen):
                if qid not in questions or qid in seen:
                    raise PolicyError("Unknown or cyclic question dependency")
                for dep in questions[qid]["dependencies"]:
                    visit(dep, seen | {qid})
            for qid in questions:
                visit(qid, set())
            if not questions:
                raise PolicyError("Define the question blocking progress")
            self.state.update(goal=goal, constraints=constraints, acceptance=acceptance, questions=questions)
            self.event("plan", goal=goal)
            self.save()
            return self.summary()

    def experiment(self, a, registry):
        with self.lock:
            self.refresh()
            if self.state["pending"]:
                raise PolicyError("Execute then record the current experiment first")
            q = self.state["questions"].get(a["question"])
            if not q or q["status"] == "resolved":
                raise PolicyError("Select an open question; resolved questions need changed inputs or contradictory evidence")
            if any(self.state["questions"][d]["status"] != "resolved" for d in q["dependencies"]):
                raise PolicyError("Resolve prerequisites or revise the dependency model with evidence")
            name = registry.canonical(a["tool"])
            if name in CONTROL or not registry.get(name):
                raise PolicyError("Choose an actual observation/action tool, not a workbook/control tool")
            if not isinstance(a["arguments"], dict):
                raise PolicyError("arguments must be a JSON object")
            args = registry.normalize_args(name, a["arguments"])
            predictions = a["predictions"]
            if not isinstance(predictions, list) or len(predictions) < 2:
                raise PolicyError("Specify at least two possible outcomes and how they change the decision")
            predictions = [text(v, "prediction") for v in predictions]
            if len(set(predictions)) != len(predictions):
                raise PolicyError("Predicted outcomes must differ")
            inputs = a["inputs"]
            if not isinstance(inputs, list):
                raise PolicyError("inputs must be artifact paths, or [] for external observations")
            paths = [str(Path(registry.ctx.resolve(text(p, "input path"))).resolve()) for p in inputs]
            if any(Path(p).is_dir() for p in paths):
                raise PolicyError("Declare relevant files, including tests/configuration, rather than directories")
            sig = digest([name, args])
            gate = self.state["gate"]
            if gate and gate["signature"] == sig and gate["inputs"] == self.versions(gate["inputs"]):
                raise PolicyError("This repeats the unchanged experiment. Change the observation/action or relevant inputs; rewording is not progress")
            self.state["active"] = q["id"]
            self.state["pending"] = {"id": uuid.uuid4().hex, "question": q["id"], "tool": name,
                "arguments": args, "signature": sig, "inputs": paths, "predictions": predictions,
                "rationale": text(a["rationale"], "why this distinguishes alternatives"), "status": "ready"}
            self.event("experiment", experiment=self.state["pending"])
            self.save()
            return "Experiment declared. Execute the specified normal tool call, then problem_record its controller evidence ID."

    def before(self, name, args):
        with self.lock:
            self.refresh()
            pending = self.state["pending"]
            signature = digest([name, args])
            if pending:
                if pending["status"] != "ready" or pending["signature"] != signature:
                    raise PolicyError("One experiment is pending. Execute its declared tool/arguments, then record the actual result; problem_status retrieves it")
                pending["status"] = "executing"
                paths = pending["inputs"]
                experiment = copy.deepcopy(pending)
                self.save()
            else:
                if self.state["gate"]:
                    raise PolicyError("Repeated unchanged evidence: use problem_plan/problem_experiment to choose a different discriminating observation, or ask_user for a concrete blocker")
                paths = []
                experiment = None
            return {"signature": signature, "before": self.versions(paths), "experiment": experiment}

    def observe(self, name, args, ticket, result, error=False, image=None, execution=None):
        with self.lock:
            self.load()
            eid = uuid.uuid4().hex
            payload = str(result)
            after = self.versions(ticket["before"])
            observation = {"id": eid, "tool": name, "arguments": args,
                "before": ticket["before"], "after": after, "error": bool(error),
                "result_hash": digest(execution["repeat_key"]) if execution and "repeat_key" in execution else
                               digest([payload, hashlib.sha256(image).hexdigest() if image else None]),
                "signature": ticket["signature"], "experiment": ticket["experiment"],
                "execution": execution,
                "time": time.time(), "output": str(self.folder / (eid + ".txt")),
                "scope": "Recorded tool attempt output; execution errors, interpretations and test adequacy are not independent proof"}
            (self.folder / (eid + ".txt")).write_text(payload, encoding="utf-8")
            atomic_save(str(self.folder / (eid + ".json")), observation)
            self.state["evidence"].append(eid)
            recent = self.state["recent"]
            repeat = {"id": eid, "signature": ticket["signature"], "result": observation["result_hash"], "inputs": after}
            recent.append(repeat)
            self.state["recent"] = recent[-12:]
            matches = [r for r in self.state["recent"] if all(r[k] == repeat[k] for k in ("signature", "result", "inputs"))]
            if len(matches) >= 3:
                self.state["gate"] = {"signature": repeat["signature"], "inputs": after,
                    "evidence": [r["id"] for r in matches], "reason": "Three identical actions/results in the last twelve executed observations"}
                self.event("loop_detected", evidence=self.state["gate"]["evidence"])
            if ticket["experiment"]:
                self.state["pending"].update(status="observed", evidence=eid)
            self.save()
            return eid

    def record(self, a):
        with self.lock:
            self.refresh()
            action = a["action"]
            if action == "cancel":
                pending = self.state["pending"]
                if not pending or pending["status"] != "ready":
                    raise PolicyError("Only an unexecuted experiment can be cancelled; observed results must be recorded")
                self.event("experiment_cancelled", experiment=pending, reason=text(a["interpretation"], "reason"))
                self.state["pending"] = None
                self.save()
                return self.summary()  # Cancelling is not evidence and does not clear a loop gate.
            evidence = self.evidence(a["evidence"])
            conclusion = text(a["interpretation"], "interpretation")
            if action == "reopen":
                q = self.state["questions"].get(a["target"])
                if not q or q["status"] != "resolved" or a["evidence"] in q["evidence"] or evidence["error"] or not self.fresh(evidence):
                    raise PolicyError("Reopening requires new, current contradictory evidence for a resolved question")
                q.update(status="open", conclusion=conclusion)
                self.event("reopened", question=q["id"], evidence=a["evidence"], reason=conclusion)
            elif action == "accept":
                item = self.state["acceptance"].get(a["target"])
                if not item or self.state["pending"] or self.state["gate"] or any(q["status"] != "resolved" for q in self.state["questions"].values()):
                    raise PolicyError("Resolve open questions and record experiments before integration acceptance")
                experiment = evidence["experiment"]
                if not self.fresh(evidence) or evidence["error"] or not experiment:
                    raise PolicyError("Acceptance requires a current executed experiment, not a narration or control-tool result")
                execution = evidence.get("execution") or {}
                if execution.get("exit") not in (None, 0) or execution.get("status") not in (None, "passed"):
                    raise PolicyError("Failed, empty, uncovered or interrupted execution cannot establish acceptance")
                if a["target"] not in self.state["questions"][experiment["question"]]["acceptance"]:
                    raise PolicyError("Use evidence from an explicit integration question for this acceptance condition")
                item.update(evidence=[a["evidence"]], interpretation=conclusion,
                            scope="Model-assessed acceptance supported by executed evidence")
                self.event("acceptance", target=a["target"], evidence=a["evidence"], interpretation=conclusion)
            elif action in {"supports", "refutes", "inconclusive", "resolve"}:
                pending = self.state["pending"]
                if not pending or pending.get("evidence") != a["evidence"] or pending["question"] != a["target"]:
                    raise PolicyError("Record the pending experiment's actual evidence against its question")
                q = self.state["questions"][a["target"]]
                if action == "resolve" and (evidence["error"] or not self.fresh(evidence)):
                    raise PolicyError("Failed execution or changed inputs cannot resolve a question")
                next_step = text(a["next"], "next experiment or integration step")
                q.update(conclusion=conclusion, next=next_step)
                if action == "resolve":
                    q["evidence"] = [a["evidence"]]
                    q["status"] = "resolved"
                else:
                    self.state["failed_approaches"].append({"question": q["id"], "evidence": a["evidence"],
                        "outcome": action, "interpretation": conclusion, "next": next_step})
                gate = self.state["gate"]
                if gate and (evidence["signature"] != gate["signature"] or evidence["after"] != gate["inputs"]):
                    self.event("loop_revised", evidence=a["evidence"])
                    self.state["gate"] = None
                self.event("interpretation", question=q["id"], action=action, evidence=a["evidence"], text=conclusion)
                self.state["pending"] = None
            else:
                raise PolicyError("Unknown record action")
            self.save()
            self.refresh()
            return self.summary()

    def summary(self):
        # Bounded prompt view; complete records remain retrievable with problem_status.
        questions = list(self.state["questions"].values())
        open_q = [q for q in questions if q["status"] != "resolved"]
        open_q.sort(key=lambda q: q["id"] != self.state["active"])
        pending = copy.deepcopy(self.state["pending"])
        if pending:
            pending["arguments"] = {"retrieval": "problem_status(experiment=true) retrieves exact arguments",
                                    "sha256": digest(pending["arguments"])}
        return {"goal": self.state["goal"], "constraints": self.state["constraints"],
            "user_request_records": [{"path": r["path"], "sha256": r["sha256"]} for r in self.state.get("requests", [])[-3:]],
            "acceptance": self.state["acceptance"], "active": self.state["active"],
            "pending": pending, "gate": self.state["gate"],
            "top_open_questions": open_q[:5], "open_count": len(open_q),
            "settled": [q for q in questions if q["status"] == "resolved"][-5:],
            "recent_interpretations": self.state["failed_approaches"][-5:],
            "recent_evidence": self.state["evidence"][-5:],
            "retrieval": "problem_status(question=ID/evidence=ID) retrieves full records; offset pages all questions",
            "scope": "Settled conclusions and acceptance are model interpretations, conditional on declared input freshness"}


def ctx_summary(ctx):
    workbook = for_ctx(ctx)
    if workbook is None:
        return None
    with workbook.lock:
        workbook.refresh()
        return workbook.summary() if workbook.state["goal"] or workbook.state["gate"] else None


def prompt_view(state, budget=6000):
    """Bound repeated prompt cost; full state/output is retrieved, never destroyed."""
    if state is None:
        return None
    result = copy.deepcopy(state)
    def preview(value):
        if isinstance(value, str) and len(value) > 240:
            return value[:240] + "… [preview; retrieve full record with problem_status]"
        if isinstance(value, dict):
            return {k: preview(v) for k, v in value.items()}
        if isinstance(value, list):
            return [preview(v) for v in value[:5]] + ([{"omitted_items":len(value)-5}] if len(value)>5 else [])
        return value
    result = preview(result)
    result["full_state_retrieval"] = "problem_status() for complete goal/constraints/acceptance; question=ID, evidence=ID, experiment=true and offset retrieve details"
    # Never splice JSON or silently pretend this is the complete workbook.
    for key in ("settled", "recent_interpretations", "top_open_questions"):
        while len(json.dumps(result, ensure_ascii=False)) > budget and len(result[key]) > (1 if key == "top_open_questions" else 0):
            result[key].pop()
    if len(json.dumps(result, ensure_ascii=False)) > budget:
        result = {k: result[k] for k in ("goal", "active", "open_count", "gate", "full_state_retrieval")}
        result["details"] = "Workbook exceeds inline budget; retrieve problem_status before acting. Complete durable state is retained."
    return result


def user_turn(ctx, user_text):
    """Pin actual user input separately from the model's interpretation of it."""
    workbook = for_ctx(ctx)
    if workbook is None:
        return
    with workbook.lock:
        workbook.load()
        rid = uuid.uuid4().hex
        path = workbook.folder / ("request-" + rid + ".txt")
        path.write_text(str(user_text), encoding="utf-8")
        workbook.state.setdefault("requests", []).append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "time": time.time()})
        workbook.save()


def incomplete(ctx):
    state = ctx_summary(ctx)
    return bool(state and (state["gate"] or state["pending"] or state["open_count"] or
                          any(not a["evidence"] for a in state["acceptance"].values())))


def tool(a, ctx, action):
    workbook = for_ctx(ctx)
    if workbook is None:
        raise PolicyError("Problem workbook requires an enabled normal conversation")
    if action == "plan":
        result = workbook.plan(a)
    elif action == "experiment":
        import agent_tools
        # Schema/name access only. Execution always uses the original Registry,
        # so its permissions, enabled tools, Stop and existing policy still apply.
        result = workbook.experiment(a, agent_tools.Registry(ctx))
    elif action == "record":
        result = workbook.record(a)
    else:
        with workbook.lock:
            workbook.refresh()
            if a.get("experiment"):
                result = workbook.state["pending"]
            elif a.get("evidence"):
                result = workbook.evidence(a["evidence"])
                output = Path(result["output"]).read_text(encoding="utf-8")
                offset = max(0, int(a.get("offset", 0)))
                result.update(exact_output=output[offset:offset+8000], output_chars=len(output), offset=offset)
            elif a.get("question"):
                if a["question"] not in workbook.state["questions"]:
                    raise PolicyError("Unknown question ID")
                result = workbook.state["questions"][a["question"]]
            elif "offset" in a:
                offset = max(0, int(a["offset"]))
                result = list(workbook.state["questions"].values())[offset:offset+5]
            else:
                result = workbook.summary()
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def capture(ctx, result, image):
    ticket = getattr(_local, "capture", None)
    if ticket is not None and ticket["ctx"] is ctx:
        ticket.update(result=str(result), image=image)


def invoke(registry, name, args, execute):
    workbook = for_ctx(registry.ctx)
    if workbook is None or name in CONTROL or (registry.get(name) or {}).get("kind") in {"note", "control"}:
        return execute()
    if not isinstance(args, dict):
        return execute()  # Normal schema validation remains authoritative.
    args = registry.normalize_args(name, args)
    ticket = workbook.before(name, args)
    previous = getattr(_local, "capture", None)
    captured = {"ctx": registry.ctx}
    _local.capture = captured
    try:
        try:
            result = execute()
        except Exception as exc:
            eid = workbook.observe(name, args, ticket, str(exc), error=True)
            raise PolicyError(f"{exc}\nController evidence: {eid}") from exc
        raw = captured.get("result", result[0])
        execution = None
        display = getattr(registry.ctx, "display", None)
        if isinstance(display, dict) and "exit" in display:
            execution = {"exit": display["exit"]}
        if name == "verify_module":
            try:
                receipt = json.loads(raw.split("\n", 1)[0])
                execution = {k: receipt.get(k) for k in ("id", "status", "exit", "scope", "report", "output")}
                import verification_ledger as vl
                ledger = vl.for_ctx(registry.ctx)
                if ledger is not None:
                    with ledger.lock:
                        ledger.load()
                        stored = next(r for r in ledger.state["receipts"] if r["id"] == receipt["id"])
                        # Use the executed adapter's versions, never model-supplied
                        # "pass" text. UUIDs and durations are not new evidence.
                        ticket["before"].update({str(ledger.root / p): h for p, h in stored["fingerprint"]["files"].items()})
                        execution["repeat_key"] = {k: stored.get(k) for k in
                            ("status", "exit", "tests", "failures", "test_names", "uncovered_checks", "fingerprint")}
                        execution["ledger_binding"] = {"root": str(ledger.root), "storage": str(ledger.folder.parent),
                            "module": stored["module"], "fingerprint": stored["fingerprint"],
                            "dependencies": stored["dependency_receipts"]}
            except (ValueError, AttributeError):
                execution = {"status": "missing_receipt"}
        eid = workbook.observe(name, args, ticket, raw, error=raw.startswith("[the tool reported an error]"),
                               image=captured.get("image", result[1]), execution=execution)
        return str(result[0]) + "\nController evidence: " + eid, result[1]
    finally:
        _local.capture = previous
