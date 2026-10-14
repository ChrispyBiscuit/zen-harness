import json
import sys
import threading
import time
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batch_execution import compact, execute, settings, validate_args
from batch_store import BatchStore


def op(oid, tool='read_file', deps=(), condition='always', **args):
    return dict(id=oid, tool=tool, args=args, depends_on=list(deps), condition=condition)


def run(ops, callback=None, config=None, **kw):
    return execute({'version': 1, 'operations': ops}, config, lambda o: None, lambda o: None,
                   callback or (lambda o, stop, deadline: {'status': 'success', 'output': o['id']}), **kw)


@pytest.mark.parametrize('ops', [
    [op('a'), op('a')], [op('a', deps=['unknown'])],
    [op('a', deps=['b']), op('b', deps=['a'])], [op('a', condition='any_failure')],
    [op('../escape')], [op('a', condition='maybe')]])
def test_invalid_graph_rejected_before_authorization(ops):
    called=[]
    with pytest.raises(ValueError):
        execute({'version': 1, 'operations': ops}, None, lambda o: None,
                lambda o: called.append(o), lambda *a: called.append(a))
    assert called == []


def test_parallel_reads_and_global_mutation_barriers():
    barrier=threading.Barrier(4); active=0; peak=0; lock=threading.Lock(); events=[]
    def callback(o, stop, deadline):
        nonlocal active,peak
        with lock: active+=1;peak=max(peak,active);events.append(('start',o['id'],active))
        if o['id'].startswith('r'): barrier.wait(timeout=1)
        time.sleep(.005)
        with lock: active-=1;events.append(('end',o['id'],active))
        return {'status':'success','output':'ok'}
    result=run([op('r'+str(i)) for i in range(4)]+[op('w1','write_file'),op('w2','powershell')],callback)
    assert peak == 4 and result['parallel_operation_count'] == 4
    assert [x for x in events if x[0]=='start' and x[1].startswith('w')] == [('start','w1',1),('start','w2',1)]
    assert [r['id'] for r in result['operations']] == ['r0','r1','r2','r3','w1','w2']


def test_failed_prerequisites_skip_chain_but_run_failure_handler():
    called=[]
    def callback(o,*a):
        called.append(o['id'])
        return {'status':'failed' if o['id']=='write' else 'success','output':'exact error'}
    result=run([op('write','write_file'),op('compile','powershell',['write'],'all_success'),
                op('test','run_tests',['compile'],'all_success'),op('inspect','read_file',['write'],'any_failure')],callback)
    assert called == ['write','inspect']
    assert [r['status'] for r in result['operations']] == ['failed','skipped','skipped','success']


def test_forward_dependencies_and_empty_all_success():
    called=[]
    run([op('second',deps=['first']),op('first',condition='all_success')],
        lambda o,*a: (called.append(o['id']) or {'status':'success','output':''}))
    assert called == ['first','second']


def test_timeout_cancels_cooperative_operation_and_waits_for_cleanup():
    exited=[];called=[]
    def callback(o,stop,deadline):
        called.append(o['id'])
        while not stop(): time.sleep(.001)
        time.sleep(.005);exited.append(True)
        return {'status':'success','output':'partial output retained'}
    result=run([op('slow','powershell'),op('next','write_file',['slow'],'all_success')],callback,
               {'max_operation_duration_seconds':.005})
    assert called == ['slow'] and exited == [True]
    assert result['operations'][0]['status']=='timed_out'
    assert result['operations'][1]['status']=='skipped'
    assert result['operations'][0]['output']=='partial output retained'


def test_user_cancel_prevents_undispatched_operations():
    stop=threading.Event();called=[]
    def callback(o,*a):
        called.append(o['id']);stop.set()
        return {'status':'success','output':'first action finished before cancellation'}
    result=run([op('first','write_file'),op('second','write_file')],callback,cancel=stop.is_set)
    assert called == ['first'] and all(r['status']=='cancelled' for r in result['operations'])


def test_disabled_and_operation_limits():
    with pytest.raises(ValueError): run([op('x')],config={'enabled':False})
    with pytest.raises(ValueError): run([op('x'+str(i)) for i in range(9)])
    with pytest.raises(ValueError): settings({'max_parallel_reads':5})
    with pytest.raises(ValueError): settings({'max_operations':1.5})


def test_compaction_is_bounded_and_preserves_error_tail_and_references(tmp_path):
    raw='start\n'+('こんにちは'*2000)+'\nFAILED test_player.py:42: expected 10, got 0'
    result=run([op('failure')],lambda *a:{'status':'failed','output':raw})
    text=compact(result,None)
    assert len(text.encode('utf-8')) <= 2000
    assert 'FAILED test_player.py:42' in text and result['batch_id']+'/failure' in text
    assert '[truncated' in text
    store=BatchStore(tmp_path,settings(None));store.put(result)
    restored=BatchStore(tmp_path,settings(None));chunks=[];offset=0
    while offset<len(raw):
        page,_=restored.get(result['batch_id'],'failure',offset,7000)
        chunks.append(page.split('\n',1)[1].split('\n[truncated; next offset=')[0]);offset+=7000
    assert ''.join(chunks)==raw
    with pytest.raises(ValueError): restored.get('../outside','failure')


def test_retention_only_removes_own_results(tmp_path):
    unrelated=tmp_path/'keep.txt';unrelated.write_text('keep')
    store=BatchStore(tmp_path,settings({'retention_batches':1}))
    a=run([op('a')]);b=run([op('b')]);store.put(a);store.put(b)
    assert unrelated.exists()
    with pytest.raises(ValueError):store.get(a['batch_id'],'a')
    assert 'success' in store.get(b['batch_id'],'b')[0]


def test_argument_schema_nested_types_and_bounds():
    schema={'type':'object','properties':{'n':{'type':'integer','minimum':1},
            'files':{'type':'object','additionalProperties':{'type':'string'}}},'required':['n']}
    validate_args({'n':1,'files':{'x':'x.csv'}},schema)
    for value in ({'n':True},{'n':0},{'n':1,'files':{'x':42}},{}):
        with pytest.raises(ValueError):validate_args(value,schema)


def engine(tmp_path, monkeypatch, **config):
    import offline_suite as suite
    import paths
    from batch_tools import attach
    monkeypatch.setenv('LWS_DATA_DIR',str(tmp_path/'data'))
    e=suite.engine(str(tmp_path),permissions='bypass',**config)
    e._basic=False;e._need_plan=False;e._fan_due=False;e._gather_job=None;e._offered=None
    e._since_tracker=0;e._turn_request='Build';e._batch_calls={};e._batch_evidence=[];e._batch_grants={}
    e.ctx.call_id='parent';attach(e)
    return e,suite


def test_existing_dispatch_permissions_and_raw_results(tmp_path,monkeypatch):
    e,suite=engine(tmp_path,monkeypatch)
    batch={'version':1,'operations':[op('write','write_file',path=str(tmp_path/'hello.txt'),content='hello'),
           op('read','read_file',['write'],'all_success',path=str(tmp_path/'hello.txt'))]}
    tc=suite.tc('batch_execute',{'batch':batch},'parent')
    e.history.append({'role':'assistant','tool_calls':[tc]})
    r=e._run_tool(tc)
    assert r['ok'] and (tmp_path/'hello.txt').read_text()=='hello'
    assert len(e.history)==2 and e.history[-1]['name']=='batch_execute'
    assert [name for name,args in e._calls_this_turn()] == ['write_file','read_file']
    bid=e.turn['batch_metrics'][0]['batch_id']
    assert 'hello' in e.reg.call('get_batch_result',{'batch_id':bid,'operation_id':'read'})[0]


def test_denied_later_operation_cannot_leave_partial_write(tmp_path,monkeypatch):
    e,_=engine(tmp_path,monkeypatch)
    e.cfg['permissions']='ask';asked=[]
    def ask(r):
        asked.append(not (tmp_path/'a.txt').exists());return 'once' if len(asked)==1 else 'deny'
    e.ask=ask
    batch={'version':1,'operations':[op('a','write_file',path=str(tmp_path/'a.txt'),content='a'),
                                    op('b','write_file',path=str(tmp_path/'b.txt'),content='b')]}
    with pytest.raises(ValueError):e._batch_adapter.run(batch)
    assert asked==[True,True] and not (tmp_path/'a.txt').exists()


def test_invalid_later_schema_rejects_before_approval(tmp_path,monkeypatch):
    e,_=engine(tmp_path,monkeypatch)
    e.ask=lambda r:pytest.fail('approval requested before schema validation finished')
    with pytest.raises(ValueError):e._batch_adapter.run({'version':1,'operations':[
        op('a','write_file',path=str(tmp_path/'a.txt'),content='a'),op('b','write_file',content='b')]})
    assert not (tmp_path/'a.txt').exists()


def test_native_messages_remain_protocol_compatible(tmp_path,monkeypatch):
    e,suite=engine(tmp_path,monkeypatch)
    for f in ('a.txt','b.txt'):(tmp_path/f).write_text(f)
    calls=[suite.tc('read_file',{'path':str(tmp_path/f)},f) for f in ('a.txt','b.txt')]
    e.history.append({'role':'assistant','tool_calls':calls})
    e._batch_adapter.native(calls)
    assert len(e.history)==3
    assert [m['tool_call_id'] for m in e.history[1:]]==['a.txt','b.txt']
    assert 'BATCH' in e.history[1]['content'] and e.history[2]['content']==''


def test_native_unauthorized_path_rejects_all_operations(tmp_path,monkeypatch):
    e,suite=engine(tmp_path,monkeypatch)
    calls=[suite.tc('write_file',{'path':str(tmp_path/'a.txt'),'content':'a'},'a'),
           suite.tc('read_file',{'path':str(tmp_path.parent/'outside.txt')},'b')]
    e.history.append({'role':'assistant','tool_calls':calls})
    text=e._batch_adapter.native(calls)
    assert 'rejected before execution' in text and not (tmp_path/'a.txt').exists()
    assert len(e.history)==3


def test_runtime_failure_skips_dependent_mutation_and_keeps_full_error(tmp_path,monkeypatch):
    e,_=engine(tmp_path,monkeypatch)
    e.reg.by_name['run_tests']['fn']=lambda a,c:('tests: FAILED\nFAILED test_player.py:42 - expected 10, got 0',None)
    text=e._batch_adapter.run({'version':1,'operations':[
        op('test','run_tests',path=str(tmp_path)),
        op('write','write_file',['test'],'all_success',path=str(tmp_path/'incorrect.txt'),content='bad')]})
    assert 'run_tests: failed' in text and 'write_file: skipped' in text
    assert not (tmp_path/'incorrect.txt').exists()
    bid=e.turn['batch_metrics'][0]['batch_id']
    raw,_=e.reg.call('get_batch_result',{'batch_id':bid,'operation_id':'test'})
    assert 'FAILED test_player.py:42' in raw
