"""Deterministic, bounded DAG execution. No model calls, retries, scripts or dynamic graphs."""
import json
import copy
import math
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

DEFAULTS = dict(enabled=True, max_operations=8, max_parallel_reads=4,
                max_batch_duration_seconds=120, max_operation_duration_seconds=60,
                max_result_tokens=2000, max_result_tokens_per_operation=800,
                preserve_full_outputs=True, retention_batches=100, retention_bytes=64*1024*1024)
PARALLEL_READS = frozenset(('read_file', 'list_dir', 'find_files', 'search_text'))
TERMINAL = frozenset(('success', 'failed', 'skipped', 'cancelled', 'timed_out'))


def settings(config):
    c = {**DEFAULTS, **(config or {})}
    if not isinstance(c['enabled'], bool) or not isinstance(c['preserve_full_outputs'], bool):
        raise ValueError('enabled and preserve_full_outputs must be booleans')
    for k in ('max_operations', 'max_parallel_reads', 'max_batch_duration_seconds',
              'max_operation_duration_seconds', 'max_result_tokens',
              'max_result_tokens_per_operation', 'retention_batches', 'retention_bytes'):
        if isinstance(c[k], bool) or not isinstance(c[k], (int, float)) or not math.isfinite(c[k]) or c[k] <= 0:
            raise ValueError(f'{k} must be positive')
        if 'seconds' not in k and not isinstance(c[k], int): raise ValueError(f'{k} must be an integer')
    if c['max_operations'] > 64 or c['max_parallel_reads'] > 4:
        raise ValueError('Batch safety caps: at most 64 operations and four read workers')
    if c['max_result_tokens'] < 256 + 192*c['max_operations']:
        raise ValueError('Result budget must fit every operation status and retrieval reference')
    return c


def validate_args(value, schema, path='args'):
    """Validate the JSON-schema vocabulary used by Zazen tools, without a new dependency."""
    if schema is True:return
    if schema is False:raise ValueError(f'{path}: forbidden by schema')
    if not isinstance(schema, dict):raise ValueError(f'{path}: invalid tool schema')
    supported = {'type', 'anyOf', 'oneOf', 'properties', 'additionalProperties', 'required', 'items',
                 'enum', 'const', 'minItems', 'maxItems', 'minLength', 'maxLength', 'minimum', 'maximum',
                 'pattern', 'description', 'title', 'default', 'examples', '$schema', '$id', 'deprecated',
                 'readOnly', 'writeOnly'}
    if set(schema)-supported:
        raise ValueError(f'{path}: unsupported schema constraints; use a single tool call')
    types = {'object': dict, 'array': list, 'string': str, 'boolean': bool,
             'integer': int, 'number': (int, float), 'null': type(None)}
    choices = schema.get('anyOf') or schema.get('oneOf')
    if choices:
        matches = 0
        for choice in choices:
            try: validate_args(value, choice, path); matches += 1
            except ValueError: pass
        if not matches or ('oneOf' in schema and matches != 1):
            raise ValueError(f'{path}: invalid alternative')
    kind = schema.get('type')
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    if kinds and not any(isinstance(value, types[k]) and not
                        (k in ('integer', 'number') and isinstance(value, bool)) for k in kinds):
        raise ValueError(f'{path}: expected {kind}')
    if 'enum' in schema and value not in schema['enum']: raise ValueError(f'{path}: invalid enum')
    if 'const' in schema and value != schema['const']: raise ValueError(f'{path}: invalid constant')
    if isinstance(value, dict):
        missing = set(schema.get('required', []))-value.keys()
        if missing: raise ValueError(f'{path}: missing {sorted(missing)}')
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False and set(value)-props.keys():
            raise ValueError(f'{path}: unknown arguments')
        for k, v in value.items():
            extra = schema.get('additionalProperties', {})
            validate_args(v, props.get(k, extra if isinstance(extra, dict) else {}), path+'.'+k)
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', float('inf')):
            raise ValueError(f'{path}: array size')
        for v in value: validate_args(v, schema.get('items', {}), path+'[]')
    elif isinstance(value, str):
        if 'pattern' in schema and not re.search(schema['pattern'], value): raise ValueError(f'{path}: pattern mismatch')
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', float('inf')):
            raise ValueError(f'{path}: string size')
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value): raise ValueError(f'{path}: nonfinite number')
        if value < schema.get('minimum', -float('inf')) or value > schema.get('maximum', float('inf')):
            raise ValueError(f'{path}: numeric bound')


def validate(batch, config, check):
    if (not isinstance(batch, dict) or set(batch)-{'version', 'operations'} or
            isinstance(batch.get('version'), bool) or batch.get('version') != 1):
        raise ValueError('batch.version must be 1')
    ops = batch.get('operations')
    if not isinstance(ops, list) or not 1 <= len(ops) <= config['max_operations']:
        raise ValueError('Operation count outside batch limit')
    ids = set()
    for op in ops:
        if not isinstance(op, dict) or set(op)-{'id', 'tool', 'args', 'depends_on', 'condition'}:
            raise ValueError('Invalid operation shape')
        oid = op.get('id')
        if not isinstance(oid, str) or not oid or len(oid) > 32 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in oid):
            raise ValueError('Operation IDs must be 1–32 letters, digits, underscores or hyphens')
        if oid in ids: raise ValueError('Duplicate operation ID: '+oid)
        ids.add(oid)
        if not isinstance(op.get('tool'), str) or not isinstance(op.get('args'), dict):
            raise ValueError('Each operation needs tool and args')
        if len(op['tool'].encode('utf-8')) > 64: raise ValueError('Tool identifier too long for bounded batch metadata')
        deps = op.get('depends_on', [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps) or len(deps) != len(set(deps)):
            raise ValueError('Invalid dependencies')
        condition = op.get('condition', 'always')
        if condition not in ('always', 'all_success', 'any_failure') or (condition == 'any_failure' and not deps):
            raise ValueError('Invalid condition')
    pending = {o['id']: set(o.get('depends_on', [])) for o in ops}
    if any(ds-ids for ds in pending.values()): raise ValueError('Unknown dependency')
    done = set()
    while pending:
        ready = {oid for oid, ds in pending.items() if ds <= done}
        if not ready: raise ValueError('Dependency cycle')
        done |= ready
        pending = {oid: ds for oid, ds in pending.items() if oid not in ready}
    # Finish structural and schema validation before requesting authorization or executing anything.
    for op in ops: check(op)
    return [dict(o, args=dict(o['args']), depends_on=o.get('depends_on', []),
                 condition=o.get('condition', 'always')) for o in ops]


def execute(batch, config, check, authorize, run, *, cancel=lambda: False, parallel_safe=None, finish=None):
    c = settings(config)
    if not c['enabled']: raise ValueError('Batch execution is disabled')
    ops = validate(batch, c, check)
    for op in ops:
        if cancel(): raise ValueError('Cancelled before dispatch')
        authorize(op)  # every permission decision finishes before any operation starts
    bid = 'b_'+uuid.uuid4().hex
    start = time.monotonic(); deadline = start+c['max_batch_duration_seconds']
    done = {}; parallel_count = 0
    safe = parallel_safe or (lambda op: op['tool'] in PARALLEL_READS)

    def one(op):
        t = time.monotonic(); stop = threading.Event()
        limit = min(deadline, t+c['max_operation_duration_seconds'])
        timer = threading.Timer(max(0, limit-t), stop.set); timer.daemon = True; timer.start()
        interrupted = lambda: stop.is_set() or cancel()
        try:
            if interrupted(): result = {'status': 'cancelled', 'output': 'Cancelled before dispatch'}
            else: result = run(copy.deepcopy(op), interrupted, limit)
            result = dict(result)
            if cancel(): result['status'] = 'cancelled'
            elif stop.is_set() or time.monotonic() >= limit: result['status'] = 'timed_out'
        except Exception as ex:
            result = {'status': 'cancelled' if cancel() else 'timed_out' if interrupted() else 'failed',
                      'output': f'{type(ex).__name__}: {ex}'}
        finally: timer.cancel()
        if result.get('status') not in TERMINAL: result['status'] = 'failed'
        return {**result, 'id': op['id'], 'tool': op['tool'],
                'elapsed_ms': round((time.monotonic()-t)*1000, 3)}

    while len(done) < len(ops):
        ready = [o for o in ops if o['id'] not in done and set(o['depends_on']) <= done.keys()]
        for o in ready:
            states = [done[d]['status'] for d in o['depends_on']]
            allowed = o['condition'] == 'always' or (o['condition'] == 'all_success' and all(s == 'success' for s in states)) or (
                o['condition'] == 'any_failure' and any(s in ('failed', 'timed_out') for s in states))
            if cancel() or time.monotonic() >= deadline:
                done[o['id']] = dict(id=o['id'], tool=o['tool'], status='cancelled' if cancel() else 'timed_out',
                                      output='Not dispatched', elapsed_ms=0)
            elif not allowed:
                done[o['id']] = dict(id=o['id'], tool=o['tool'], status='skipped',
                                      output='Dependency condition was not met', elapsed_ms=0)
        ready = [o for o in ready if o['id'] not in done]
        if not ready: continue
        # Reads are the only concurrency class. A mutation is a global barrier.
        group = []
        for op in ready:
            if not safe(op):
                if not group: group.append(op)
                break
            group.append(op)
            if len(group) >= c['max_parallel_reads']: break
        if len(group) > 1:
            parallel_count += len(group)
            with ThreadPoolExecutor(max_workers=len(group), thread_name_prefix='zen-bte-read') as pool:
                results = list(pool.map(one, group))
        else: results = [one(group[0])]
        # Workers have quiesced before any subsequent mutation or result is returned.
        for op, result in zip(group, results):
            if finish: finish(op, result)
        done.update({r['id']: r for r in results})
    rows = [dict(done[o['id']], depends_on=o['depends_on'], condition=o['condition']) for o in ops]
    return {'batch_id': bid, 'status': 'completed' if all(r['status'] == 'success' for r in rows) else 'completed_with_errors',
            'operations': rows, 'execution_elapsed_ms': round((time.monotonic()-start)*1000, 3),
            'parallel_operation_count': parallel_count}


def compact(result, config):
    """UTF-8 byte budgets conservatively bound token counts; never synthesize summaries."""
    c = settings(config)
    headers = [f"BATCH {result['batch_id']} {result['status']} elapsed_ms={result['execution_elapsed_ms']}"]
    if result.get('storage_error'): headers.append('Full output storage failed; references are unavailable.')
    retained = result.get('retained', True)
    headers += [f"[{r['id']}] {r['tool']}: {r['status']} ref="+
                (f"{result['batch_id']}/{r['id']}" if retained else 'not-retained') for r in result['operations']]
    base = '\n'.join(headers)
    room = max(0, c['max_result_tokens']-len(base.encode('utf-8')))
    chunks = []
    for i, r in enumerate(result['operations']):
        raw = str(r.get('output', ''))
        budget = min(c['max_result_tokens_per_operation'], room//max(1,len(result['operations'])-i))
        prefix = f"\n[{r['id']}] output: "
        marker = '\n[truncated; retrieve the reference above]' if retained else '\n[truncated; full output not retained]'
        available = max(0, budget-len((prefix+marker).encode()))
        data = raw.encode('utf-8')
        if len(data) > available and r['status'] in ('failed', 'timed_out') and available > 16:
            half = (available-5)//2
            snippet = data[:half].decode('utf-8', 'ignore')+'\n...\n'+data[-half:].decode('utf-8', 'ignore')
        else:
            snippet = data[:available].decode('utf-8', 'ignore')
        chunk = prefix+snippet+(marker if len(data) > available else '')
        if len(chunk.encode()) <= budget:
            chunks.append(chunk); room -= len(chunk.encode())
    return base+''.join(chunks)
