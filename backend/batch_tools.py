"""BTE adapter: reuse Zazen's registry, permissions, tool dispatch, UI events and recovery."""
import copy
import json
import re
import time
import uuid

from batch_execution import PARALLEL_READS, compact, execute, settings, validate_args
from batch_store import BatchStore

OP_SCHEMA = {'type': 'object', 'properties': {
    'id': {'type': 'string', 'maxLength': 32}, 'tool': {'type': 'string'},
    'args': {'type': 'object'}, 'depends_on': {'type': 'array', 'items': {'type': 'string'}},
    'condition': {'type': 'string', 'enum': ['always', 'all_success', 'any_failure']}},
    'required': ['id', 'tool', 'args'], 'additionalProperties': False}
BATCH_SCHEMA = {'type': 'object', 'properties': {'batch': {'type': 'object', 'properties': {
    'version': {'type': 'integer', 'enum': [1]},
    'operations': {'type': 'array', 'minItems': 1, 'items': OP_SCHEMA}},
    'required': ['version', 'operations'], 'additionalProperties': False}}, 'required': ['batch']}


def attach(engine):
    import agent_tools as at
    c = settings(engine.cfg.get('batch_execution'))
    if not c['enabled']: return
    adapter = Adapter(engine, c)
    engine._batch_adapter = adapter
    engine.reg._add('batch_execute', at.CONTROL,
        'Run a small fixed batch of known tools without another model turn. Independent certified file reads '
        'can overlap; mutations are serialized. Use depends_on and all_success/any_failure for conditional '
        'build/test chains. Each operation gets its own validation and approval. No loops or nested batches.',
        BATCH_SCHEMA, lambda args, ctx: (adapter.run(args['batch']), None))
    engine.reg._add('get_batch_result', at.READ,
        'Retrieve preserved raw output by batch_id and operation_id, including after context compaction. '
        'Use offset for subsequent pages.',
        {'type': 'object', 'properties': {'batch_id': {'type': 'string'}, 'operation_id': {'type': 'string'},
         'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8000}},
         'required': ['batch_id', 'operation_id']},
        lambda args, ctx: adapter.store.get(args['batch_id'], args['operation_id'], args.get('offset', 0), args.get('limit', 8000)))
    engine.enabled_tools.update(('batch_execute', 'get_batch_result'))


class Adapter:
    def __init__(self, engine, config):
        import paths
        self.e = engine; self.config = config
        conv = re.sub(r'[^A-Za-z0-9_-]', '_', str(engine.ctx.conv_id))[:64]
        self.store = BatchStore(paths.data('agent_attachments', conv, 'batches'), config)

    def run(self, batch, native=None):
        import agent_tools as at
        e = self.e
        if not isinstance(batch, dict): raise ValueError('batch must be an object')
        global_stop = e.should_stop
        parent_id = e.ctx.call_id
        old_display = e.ctx.display
        calls = {o['id']: {'id': native[i]['id'] if native else 'bte_'+uuid.uuid4().hex,
                          'type': 'function', 'function': {'name': o.get('tool', ''),
                          'arguments': json.dumps(o.get('args', {}))}}
                 for i, o in enumerate(batch.get('operations', [])) if isinstance(o, dict) and 'id' in o}
        before = len(e.history)
        grants = dict(getattr(e, '_batch_grants', {}))

        def check(op):
            t = e.reg.get(op['tool'])
            if not t: raise ValueError('Unknown tool: '+op['tool'])
            name = e.reg.canonical(op['tool'])
            if t['kind'] == at.CLOUD:
                raise ValueError('Model/advisor calls are not supported inside deterministic batches')
            if name in ('batch_execute', 'get_batch_result', 'gather', 'delegate_task', 'consult_advisor', 'go_extensive', 'bg_start'):
                raise ValueError('Nested orchestration is not supported')
            args = e.reg.normalize_args(name, op['args'])
            args = dict(args); progress = args.pop('progress', None)
            validate_args(args, t.get('parameters', {}))
            try:
                for field in ('path', 'root', 'src', 'dst', 'cwd'):
                    if isinstance(args.get(field), str) and args[field]: e.ctx.resolve(args[field])
                files = args.get('files')
                for path in (files.values() if isinstance(files, dict) else files if isinstance(files, list) else []):
                    if isinstance(path, str): e.ctx.resolve(path)
            except at.ToolError as ex: raise ValueError(str(ex)) from ex
            if name in ('powershell', 'bg_start'):
                reason = at.protected_reason(str(args.get('command', '')))
                if reason: raise ValueError(reason)
            if e._offered is not None and name not in e._offered:
                raise ValueError('Tool is not eligible in the current execution phase: '+name)
            if e._basic: raise ValueError('Use single calls on the basic path, or request the full toolset')
            if getattr(e, '_fan_due', False) or getattr(e, '_gather_job', None) is not None:
                raise ValueError('Finish worker coordination before starting a batch')
            op['tool'] = name; op['args'] = args
            calls[op['id']]['function'] = {'name': name, 'arguments': json.dumps(args)}
            if progress: calls[op['id']]['function']['arguments'] = json.dumps(dict(args, progress=progress))

        def authorize(op):
            tc = calls[op['id']]; t = e.reg.get(op['tool'])
            denied = e._authorize_tool(t, op['tool'], op['args'], tc['id'], e.reg.describe(op['tool'], op['args']))
            if denied: raise ValueError(denied)
            e._batch_grants[tc['id']] = (op['tool'], json.dumps(op['args'], sort_keys=True))

        def committed(op, pre=None, stop=None):
            tc = calls[op['id']]
            old_stop = e.should_stop; old_capture = getattr(e.ctx, 'capture_result', None)
            old_batch = getattr(e.ctx, 'batch_mode', False); old_deadline = getattr(e.ctx, 'batch_deadline', None)
            old_call_id = e.ctx.call_id
            raw = {}
            if not native:
                e.history.append({'role': 'assistant', 'content': '', 'tool_calls': [tc]})
            mark = len(e.history)-1 if not native else None
            if pre is not None: e._prefetched[tc['id']] = pre
            e.ctx.capture_result = lambda text, image: raw.update(output=text, image=image)
            e.ctx.batch_mode = True
            e.ctx.batch_deadline = getattr(stop, 'deadline', None)
            if stop: e.should_stop = stop
            try:
                r = e._run_tool(tc)
                return {**r, 'raw': raw}
            finally:
                e.should_stop = old_stop; e.ctx.should_stop = old_stop
                e.ctx.capture_result = old_capture
                e.ctx.batch_mode = old_batch; e.ctx.batch_deadline = old_deadline
                e.ctx.call_id = old_call_id
                e._prefetched.pop(tc['id'], None)
                if not native:
                    ledger = getattr(e, '_batch_calls', {})
                    ledger.setdefault(parent_id, []).append(tc)
                    e._batch_calls = ledger
                    e._batch_evidence = getattr(e, '_batch_evidence', [])+e.history[mark:]
                    del e.history[mark:]

        def failed(name, r):
            text = str(r.get('text', ''))
            return not r.get('ok') or e._run_broken(name, text) is True or text.startswith((
                'Error:', 'Not run:', 'The user denied', '[the tool reported an error]'))

        def run(op, stop, deadline):
            stop.deadline = deadline
            if op['tool'] in PARALLEL_READS:
                tc = calls[op['id']]
                ctx = copy.copy(e.ctx); ctx.display = None; ctx.call_id = tc['id']; ctx.should_stop = stop
                reg = copy.copy(e.reg); reg.ctx = ctx
                raw = {}; ctx.capture_result = lambda text, image: raw.update(output=text, image=image)
                try:
                    repeat = e._repeat_of(op['tool'], e.reg.get(op['tool']), op['args'])
                    cached = e._unchanged_read(op['args']) if op['tool'] == 'read_file' else ''
                    if repeat:
                        text = repeat; pre = (False, text, None, None)
                    elif cached:
                        text = cached; pre = (True, text, None, None)
                    else:
                        text, image = reg.call(op['tool'], op['args'])
                        pre = (True, text, image, ctx.display)
                except Exception as ex:
                    text = f'Error: {type(ex).__name__}: {ex}'; pre = (False, text, None, None)
                return {'status': 'success' if pre[0] else 'failed', 'output': raw.get('output', text),
                        '_pre': pre, '_image': raw.get('image'), '_raw': raw}
            r = committed(op, stop=stop)
            return {'status': 'failed' if failed(op['tool'], r) else 'success',
                    'output': r['raw'].get('output', r['text']), '_image': r['raw'].get('image')}

        def finish(op, result):
            if '_pre' in result:
                pre = result['_pre']
                if result['status'] in ('cancelled', 'timed_out'):
                    pre = (False, result['status']+': '+str(pre[1]), pre[2], pre[3])
                r = committed(op, pre)
                if result['status'] == 'success' and failed(op['tool'], r): result['status'] = 'failed'

        try:
            e._batch_grants = {}
            result = execute(batch, self.config, check, authorize, run, cancel=lambda: global_stop() or e._force_answer,
                             parallel_safe=lambda o: o['tool'] in PARALLEL_READS and e.reg.get(o['tool'])['kind'] == at.READ
                             and e.reg.get(o['tool'])['fn'] is getattr(at, 't_'+o['tool'], None),
                             finish=finish)
            result['operation_count'] = len(result['operations'])
            for state in ('success', 'failed', 'skipped', 'cancelled', 'timed_out'):
                result[state+'_operations'] = sum(r['status'] == state for r in result['operations'])
            result['tool_output_bytes'] = sum(len(str(r.get('output', '')).encode('utf-8'))+
                                               len(r.get('_image') or b'') for r in result['operations'])
            result['retained'] = self.config['preserve_full_outputs']
            text = compact(result, self.config)
            result['result_tokens_upper_bound'] = len(text.encode('utf-8'))
            try: self.store.put(result)
            except (OSError, ValueError) as ex:
                result['retained'] = False
                result['storage_error'] = str(ex)
                result['status'] = 'completed_with_errors'
            text = compact(result, self.config)
            result['result_tokens_upper_bound'] = len(text.encode('utf-8'))
            e.turn['batches'] = e.turn.get('batches', 0)+1
            e.turn.setdefault('batch_metrics', []).append({k: v for k, v in result.items() if k != 'operations'})
            if native:
                # The protocol still needs one response per call ID; the first carries the logical group.
                e.history[before:] = [{'role': 'tool', 'name': tc['function']['name'], 'tool_call_id': tc['id'],
                                      'content': text if i == 0 else ''} for i, tc in enumerate(native)]
            return text
        finally:
            e._batch_grants = grants
            e.ctx.display = old_display

    def native(self, calls):
        batch = {'version': 1, 'operations': []}
        try:
            for i, tc in enumerate(calls):
                fn = tc['function']; args = fn.get('arguments') or '{}'
                args = json.loads(args) if isinstance(args, str) else args
                batch['operations'].append({'id': 'op'+str(i+1), 'tool': fn['name'], 'args': args})
            return self.run(batch, native=calls)
        except (ValueError, TypeError, KeyError) as ex:
            text = ('Batch rejected before execution: '+str(ex)).encode('utf-8')[:self.config['max_result_tokens']].decode('utf-8','ignore')
            self.e.history.extend({'role': 'tool', 'name': tc['function']['name'],
                                   'tool_call_id': tc['id'], 'content': text if i == 0 else ''}
                                  for i, tc in enumerate(calls))
            for tc in calls:
                self.e.emit('tool_call', id=tc['id'], name=tc['function']['name'], args={}, desc='Batch rejected', kind='read')
                self.e.emit('tool_result', id=tc['id'], text=text, ok=False, image=None, display=None)
            self.e.turn['batch_rejections'] = self.e.turn.get('batch_rejections', 0)+1
            return text
