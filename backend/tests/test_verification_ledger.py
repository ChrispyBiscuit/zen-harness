import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import pytest
from verification_ledger import Ledger,PolicyError,invoke

@pytest.fixture
def ledger(tmp_path):
 root=tmp_path/'project';root.mkdir()
 (root/'logic.cjs').write_text('exports.add=(a,b)=>a+b;')
 (root/'logic.test.cjs').write_text("const test=require('node:test');const assert=require('node:assert/strict');const {add}=require('./logic.cjs');test('adds two inputs',()=>assert.equal(add(2,3),5));")
 return Ledger(root,tmp_path/'store')

def contract(mid='math',dependencies=None,**kwargs):
 a={'id':mid,'purpose':'Add numbers','inputs':'Two finite numbers','outputs':'Their sum','errors':'Reject invalid inputs','effects':'None','sources':['logic.cjs'],'tests':['logic.test.cjs'],'checks':['adds two inputs'],'dependencies':dependencies or [],'tracker_ids':['1.1'],'framework':'node'}
 a.update(kwargs);return a

def ctx():return SimpleNamespace(should_stop=lambda:False,display=None)

def verified(l):
 l.declare(contract());l.begin('math');l.verify('math',ctx());assert l.state['modules']['math']['state']=='verified'

def test_real_runner_receipt_and_source_test_invalidation(ledger):
 l=ledger;verified(l);receipt=l.state['receipts'][-1]
 assert receipt['tests']==1 and Path(receipt['report']).exists()
 l.check_tracker([{'id':'1.1','status':'done'}],{})
 (l.root/'logic.cjs').write_text('exports.add=(a,b)=>a-b;')
 l.refresh();assert l.state['modules']['math']['state']=='stale'
 with pytest.raises(PolicyError):l.check_tracker([{'id':'1.1','status':'done'}],{})
 l.verify('math',ctx());assert l.state['receipts'][-1]['status']=='failed'
 assert l.state['modules']['math']['state']!='verified'
 assert 'adds two inputs' in Path(l.state['receipts'][-1]['output']).read_text() or Path(l.state['receipts'][-1]['report']).exists()


def test_empty_plain_script_and_uncovered_tests_cannot_verify(ledger):
 l=ledger;l.declare(contract());l.begin('math')
 (l.root/'logic.test.cjs').write_text("console.log('PASS: adds two inputs');")
 l.verify('math',ctx());assert l.state['receipts'][-1]['status']=='no_tests'
 (l.root/'logic.test.cjs').write_text("const test=require('node:test');test('unrelated trivial check',()=>{});")
 l.verify('math',ctx());assert l.state['receipts'][-1]['status']=='uncovered_checks'
 assert l.state['modules']['math']['state']=='failing'


def test_dependent_integration_cannot_start_until_prerequisite_passes(ledger):
 l=ledger;l.declare(contract());l.declare(contract('integration',['math'],tracker_ids=['1.2']))
 with pytest.raises(PolicyError):l.begin('integration')
 l.begin('math');l.verify('math',ctx());l.begin('integration');l.verify('integration',ctx())
 assert l.state['modules']['integration']['state']=='verified'
 (l.root/'logic.cjs').write_text('exports.add=(a,b)=>a+b+1;');l.refresh()
 assert all(m['state']=='stale' for m in l.state['modules'].values())


def test_switching_and_scope_enforced_but_related_repairs_allowed(ledger):
 l=ledger;l.declare(contract());l.declare(contract('other',sources=['other.cjs'],tests=['other.test.cjs']))
 l.begin('math')
 with pytest.raises(PolicyError):l.begin('other')
 with pytest.raises(PolicyError):l.preflight('write_file',{'path':'other.cjs'},'write')
 before=l.preflight('write_file',{'path':'logic.cjs'},'write')
 (l.root/'logic.cjs').write_text('exports.add=(a,b)=>a+b+1;');l.record_changes(before)
 assert l.state['modules']['math']['state']=='needs_verification'
 l.preflight('write_file',{'path':'logic.test.cjs'},'write')


def test_shell_changes_unknown_ownership_and_restart_persistence(ledger):
 l=ledger;verified(l)
 before=l.preflight('powershell',{'command':'arbitrary command'},'exec')
 (l.root/'new.cjs').write_text('exports.x=2;');l.record_changes(before)
 assert l.state['unowned']==['new.cjs']
 restored=Ledger(l.root,l.folder.parent)
 assert restored.state['modules']['math']['state']=='verified'
 with pytest.raises(PolicyError):restored.check_tracker([{'id':'1','status':'done'}],{})
 restored.declare(contract(sources=['logic.cjs','new.cjs']))
 restored.begin('math');assert not restored.state['unowned']


def test_test_modification_config_dependency_and_contract_changes_invalidate(ledger):
 l=ledger;verified(l)
 (l.root/'package.json').write_text('{"version":"1.0.0"}');l.refresh();assert l.state['modules']['math']['state']=='stale'
 l.verify('math',ctx());assert l.state['modules']['math']['state']=='verified'
 (l.root/'logic.test.cjs').write_text((l.root/'logic.test.cjs').read_text()+'\n// changed');l.refresh();assert l.state['modules']['math']['state']=='stale'
 l.verify('math',ctx());l.declare(contract(outputs='Changed contract'))
 assert l.state['modules']['math']['state']=='needs_verification'


def test_cyclic_dependency_and_path_escape_rejected(ledger):
 l=ledger;l.declare(contract());l.declare(contract('child',['math']))
 with pytest.raises(PolicyError):l.declare(contract(dependencies=['child']))
 with pytest.raises(PolicyError):l.declare(contract(sources=['../escape.cjs']))


def test_pytest_adapter_and_zero_collection(ledger):
 l=ledger;(l.root/'test_example.py').write_text('def test_adds_two_inputs():\n    assert 2+3==5\n')
 l.declare(contract(framework='pytest',tests=['test_example.py'],checks=['test_adds_two_inputs']))
 l.begin('math');l.verify('math',ctx());assert l.state['receipts'][-1]['status']=='passed'
 (l.root/'test_example.py').write_text('answer=5\n')
 l.verify('math',ctx());assert l.state['receipts'][-1]['status']=='no_tests'


def test_no_false_pass_when_code_changes_during_run(ledger):
 l=ledger
 (l.root/'logic.test.cjs').write_text("const fs=require('fs');const test=require('node:test');test('adds two inputs',()=>{fs.appendFileSync('logic.cjs','\\n// mutation');});")
 l.declare(contract());l.begin('math');l.verify('math',ctx())
 assert l.state['receipts'][-1]['status']=='stale_during_test'
 assert l.state['modules']['math']['state']!='verified'


def test_registry_shared_guard_blocks_direct_and_batched_style_calls(ledger,monkeypatch):
 import verification_ledger as vl
 from agent_tools import Registry,ToolError
 c=SimpleNamespace(build_test_record=True)
 r=object.__new__(Registry);r.ctx=c;r.by_name={'write_file':{'kind':'write'}}
 r.canonical=lambda n:n;r.get=lambda n:r.by_name.get(n);ran=[]
 r._call_unchecked=lambda name,args:ran.append(name) or ('ok',None)
 monkeypatch.setattr(vl,'for_ctx',lambda c:ledger)
 with pytest.raises(ToolError):r.call('write_file',{'path':'logic.cjs'})
 assert not ran
 ledger.declare(contract());ledger.begin('math');r.call('write_file',{'path':'logic.cjs'})
 assert ran==['write_file']


def test_skipped_checks_and_syntax_errors_never_verify(ledger):
 l=ledger;l.declare(contract());l.begin('math')
 (l.root/'logic.test.cjs').write_text("require('node:test')('adds two inputs',{skip:true},()=>{});")
 l.verify('math',ctx());assert l.state['receipts'][-1]['status']=='no_tests'
 (l.root/'logic.test.cjs').write_text('this is invalid javascript !!!')
 l.verify('math',ctx());assert l.state['modules']['math']['state']=='failing'
 assert l.state['receipts'][-1]['exit']!=0


def test_tracker_restructure_cannot_fabricate_done(ledger,monkeypatch):
 import verification_ledger as vl
 import agent_tools as at
 l=ledger;l.declare(contract());l.begin('math')
 c=SimpleNamespace(tracker={'goal':'Build','milestones':[{'title':'Math','status':'pending','subtasks':[{'step':'Implement and verify','status':'pending'}]}],'notes':[]},tracker_path=str(l.root/'PROGRESS.md'),conv_id='fixture',title='fixture',display=None)
 monkeypatch.setattr(vl,'for_ctx',lambda ctx:l)
 with pytest.raises(PolicyError):at.t_update_tracker({'milestones':[{'title':'Math','status':'done'}]},c)
 assert not Path(c.tracker_path).exists()


def test_unlinked_documentation_can_finish_while_game_module_is_unverified(ledger,monkeypatch):
 import verification_ledger as vl
 import agent_tools as at
 l=ledger;l.declare(contract('game',tracker_ids=['2'],sources=['logic.cjs'],tests=['logic.test.cjs']))
 c=SimpleNamespace(tracker={'goal':'Build game','milestones':[{'title':'Documentation','status':'pending','subtasks':[{'step':'Write design','status':'pending'}]},{'title':'Game','status':'pending','subtasks':[{'step':'Implement game','status':'pending'}]}],'notes':[]},tracker_path=str(l.root/'PROGRESS.md'),conv_id='fixture',title='fixture',display=None)
 monkeypatch.setattr(vl,'for_ctx',lambda ctx:l)
 at.t_update_tracker({'updates':[{'id':'1.1','status':'done','evidence':'DESIGN.md exists'}]},c)
 assert c.tracker['milestones'][0]['subtasks'][0]['status']=='done'
 assert Path(c.tracker_path).exists()
 with pytest.raises(PolicyError):at.t_update_tracker({'updates':[{'id':'2.1','status':'done','evidence':'Unverified claim'}]},c)
 assert c.tracker['milestones'][1]['subtasks'][0]['status']=='pending'


def test_stop_interrupts_test_and_never_creates_green_receipt(ledger):
 l=ledger;l.declare(contract());l.begin('math')
 (l.root/'logic.test.cjs').write_text("const test=require('node:test');test('adds two inputs',async()=>new Promise(()=>{}));setInterval(()=>{},1000);")
 l.verify('math',SimpleNamespace(should_stop=lambda:True,display=None))
 assert l.state['receipts'][-1]['status']=='interrupted'
 assert l.state['modules']['math']['state']!='verified'


def test_normal_registry_contract_verify_and_tracker_roundtrip(ledger,monkeypatch):
 import agent_tools as at
 import verification_ledger as vl
 c=at.Ctx([str(ledger.root)]);c.cwd=str(ledger.root);c.build_test_record=True;c.verification_root=str(ledger.root)
 c.conv_id='verification-fixture';c.tracker_path=str(ledger.root/'PROGRESS.md')
 monkeypatch.setattr(vl,'for_ctx',lambda ctx:ledger)
 r=at.Registry(c,enabled={'module_contract','begin_module','verify_module','module_status','update_tracker'})
 r.call('update_tracker',{'new':True,'goal':'Build addition','milestones':[{'title':'Addition','subtasks':[{'step':'Implement and verify'}]}]})
 r.call('module_contract',contract());r.call('begin_module',{'id':'math'})
 with pytest.raises(at.ToolError):r.call('update_tracker',{'updates':[{'id':'1.1','status':'done'}]})
 text,_=r.call('verify_module',{'id':'math'});assert '"status": "passed"' in text
 r.call('update_tracker',{'updates':[{'id':'1.1','status':'done'}]})
 assert c.tracker['milestones'][0]['subtasks'][0]['status']=='done'
 report,_=r.call('module_status',{});assert json.loads(report)['modules'][0]['state']=='verified'
 receipt,_=r.call('module_status',{'receipt':ledger.state['receipts'][-1]['id']})
 assert json.loads(receipt)['fingerprint']['files']['logic.cjs']


def test_gate_adopts_new_files_into_the_active_module(ledger):
 # the 9B loops (e42759a0, 32d1c242): module owns data.py, model writes store.py / tests/test_store.py and was refused
 l=ledger;l.declare(contract('storage',sources=['data.py'],tests=['test_data.py'],framework='pytest'));l.begin('storage')
 l.preflight('write_file',{'path':str(l.root/'store.py')},'write')
 assert l.adopted==('store.py','storage','sources') and 'store.py' in l.state['modules']['storage']['sources']
 l.preflight('write_file',{'path':str(l.root/'tests'/'test_store.py')},'write')
 assert 'tests/test_store.py' in l.state['modules']['storage']['tests']
 l.preflight('write_file',{'path':str(l.root/'data.py')},'write')        # the owned file is still allowed


def test_gate_still_refuses_a_file_another_module_owns(ledger):
 l=ledger;l.declare(contract());l.declare(contract('storage',sources=['data.py'],tests=['test_data.py'],framework='pytest'))
 l.begin('storage')
 with pytest.raises(PolicyError) as e:l.preflight('write_file',{'path':str(l.root/'logic.cjs')},'write')
 msg=str(e.value)
 assert 'logic.cjs was NOT written' in msg and "belongs to module 'math'" in msg
 assert 'logic.cjs' not in l.state['modules']['storage']['sources']


def test_adoption_is_reported_in_the_write_result(ledger,monkeypatch):
 import agent_tools as at
 import verification_ledger as vl
 c=at.Ctx([str(ledger.root)]);c.cwd=str(ledger.root);c.build_test_record=True;c.verification_root=str(ledger.root)
 c.conv_id='verification-fixture'
 monkeypatch.setattr(vl,'for_ctx',lambda ctx:ledger)
 r=at.Registry(c,enabled={'module_contract','begin_module','write_file'})
 r.call('module_contract',contract('storage',sources=['data.py'],tests=['test_data.py'],framework='pytest'))
 r.call('begin_module',{'id':'storage'})
 text,_=r.call('write_file',{'path':'helpers.py','content':'X = 1'})
 assert "helpers.py was added to module 'storage' sources" in text
 assert (ledger.root/'helpers.py').exists()


def test_gate_refusal_without_active_module_says_how_to_start(ledger):
 with pytest.raises(PolicyError) as e:ledger.preflight('write_file',{'path':str(ledger.root/'app.py')},'write')
 assert 'No module owns it yet' in str(e.value) and 'begin_module' in str(e.value)


def test_a_refused_write_is_kept_and_written_when_its_module_begins(ledger,monkeypatch):
 # 27B run 95fc5c3e: server.py and client.py generated in full before any module, refused, generated again
 import agent_tools as at
 import verification_ledger as vl
 c=at.Ctx([str(ledger.root)]);c.cwd=str(ledger.root);c.build_test_record=True;c.verification_root=str(ledger.root)
 c.conv_id='verification-fixture'
 monkeypatch.setattr(vl,'for_ctx',lambda ctx:ledger)
 r=at.Registry(c,enabled={'module_contract','begin_module','write_file'})
 with pytest.raises(at.ToolError) as e:r.call('write_file',{'path':'server.py','content':'PORT = 8000'})
 assert 'is kept' in str(e.value) and not (ledger.root/'server.py').exists()
 with pytest.raises(at.ToolError):r.call('write_file',{'path':'client.py','content':'URL = 1'})
 r.call('module_contract',contract('server',sources=['server.py'],tests=['test_server.py'],framework='pytest'))
 text,_=r.call('begin_module',{'id':'server'})
 assert 'Wrote your kept write_file for server.py' in text
 assert (ledger.root/'server.py').read_text()=='PORT = 8000'
 assert not (ledger.root/'client.py').exists() and 'client.py' in ledger.state['drafts']   # not this module's file


def test_writing_the_file_directly_drops_its_draft(ledger):
 l=ledger;l.keep_draft('app.py','write_file',{'path':'app.py','content':'x'})
 before=l.inventory();(l.root/'app.py').write_text('y');l.record_changes(before)
 assert 'app.py' not in l.state.get('drafts',{})


def test_a_passing_verify_ticks_the_linked_checklist_items(ledger,monkeypatch):
 # 27B run 95fc5c3e: completion refused once (stale receipt), module verified later, item never ticked (2/14)
 import agent_tools as at
 import verification_ledger as vl
 c=at.Ctx([str(ledger.root)]);c.cwd=str(ledger.root);c.build_test_record=True;c.verification_root=str(ledger.root)
 c.conv_id='verification-fixture';c.tracker_path=str(ledger.root/'PROGRESS.md')
 monkeypatch.setattr(vl,'for_ctx',lambda ctx:ledger)
 r=at.Registry(c,enabled={'module_contract','begin_module','verify_module','update_tracker'})
 r.call('update_tracker',{'new':True,'goal':'Build addition','milestones':[{'title':'Addition','subtasks':[
  {'step':'Implement add'},{'step':'Test add'},{'step':'Write the README'}]}]})
 r.call('module_contract',contract(tracker_ids=['1.1','1.2']));r.call('begin_module',{'id':'math'})
 with pytest.raises(at.ToolError) as e:r.call('update_tracker',{'updates':[{'id':'1.1','status':'done'}]})
 msg=str(e.value)
 assert "run verify_module('math')" in msg and 'marked done automatically' in msg
 text,_=r.call('verify_module',{'id':'math'})
 assert '"status": "passed"' in text and 'marked done' in text
 st=[s['status'] for s in c.tracker['milestones'][0]['subtasks']]
 assert st==['done','done','pending']                 # the README is not this module's: untouched
 assert c.display and c.display.get('type')=='terminal'   # the verification output stays the call's card
