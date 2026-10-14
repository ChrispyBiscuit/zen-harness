"""One owner for stalled-run recovery; tool validation and evidence storage stay separate."""
import hashlib
import json
from collections import Counter


class RunPolicy:
    def __init__(self, repeat_limit=6, failure_limit=6, stall_limit=24):
        self.repeat_limit = max(3, repeat_limit)
        self.failure_limit = max(3, failure_limit)
        self.stall_limit = max(self.repeat_limit, stall_limit)
        self.reset()

    def reset(self):
        self.actions = Counter()
        self.evidence = set()
        self.failures = self.stalled = 0
        self.version = None

    def observe(self, name, args, result, *, failed=False, refused=False, changed=False,
                polling=False, version=0):
        if polling:
            return ''
        if changed or (self.version is not None and version != self.version):
            self.reset()
        self.version = version
        action = name + json.dumps(args, sort_keys=True, default=str)
        digest = hashlib.sha256(str(result).encode()).digest()
        novel = not refused and (action, digest) not in self.evidence
        self.evidence.add((action, digest))
        self.actions[action] += 1
        self.failures = self.failures + 1 if failed else 0
        self.stalled = 0 if changed or (novel and not failed) else self.stalled + 1
        count = self.actions[action]
        if changed or (novel and not failed):
            self.actions[action] = 1
            count = 1
        if count >= self.repeat_limit:
            return f'{count} unchanged attempts at {name}'
        if self.failures >= self.failure_limit:
            return f'{self.failures} failed actions in succession'
        if self.stalled >= self.stall_limit:
            return f'{self.stalled} actions without new evidence'
        return ''


def step_budget(value=None):
    """Zero explicitly disables the step cap; absent values retain the normal default."""
    result = 40 if value is None else int(value)
    if result < 0:
        raise ValueError("max_steps must be nonnegative; use 0 for no step cap")
    return result


def step_allowed(step, budget):
    return budget == 0 or step < budget
