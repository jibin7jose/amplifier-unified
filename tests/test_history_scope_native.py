"""Actual Core/Foundation app-control mounts use the shared history policy."""
import importlib.util
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')
pytest.importorskip('amplifier_module_context_simple')


@pytest.mark.parametrize('mode',['finite','resumed','persistent','nested','recipe'])
async def test_native_root_and_child_history_scope(tmp_path,monkeypatch,mode):
    from amplifier_foundation.bundle import Bundle,BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.shared_state import ActivationGate
    from amplifier_web.host.children import install_children,_PERSISTENT
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.service import AppService

    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'native'))
    spec=importlib.util.spec_from_file_location('history_scope_child_fixture',Path(__file__).parent/'fixtures/standalone_children_probe.py')
    fixture=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,fixture)
    spec.loader.exec_module(fixture)
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},'agents':{'worker':{'instruction':'Read history'}}}
    paths={name:Path(importlib.util.find_spec('amplifier_module_'+name.replace('-','_')).origin).parent for name in ('loop-live','context-simple')}
    prepared=fixture.ProbePrepared(plan,BundleModuleResolver(paths),Bundle(name='fixture',session=plan['session'],agents=plan['agents']))
    approvals=Approvals(None,AsyncMock(return_value='deny'))
    manager=RuntimeManager()
    parent=app=root=token=None
    seen,failures=[],[]
    try:
        parent=await prepared.create_session(session_id='root',approval_system=approvals)
        app=AppService(tmp_path/'app',runtime=manager,workspace=tmp_path)
        assert app.runtime is manager
        root=app._new_session({'title':'Root'})
        root.update(runtimeSessionId='root',collaborationGeneration={
            'id':'generation','inputIds':['history-input'],'interruptionRevision':0,'terminal':False})
        (tmp_path/'other').mkdir()
        other=app._new_session({'title':'Other','workspace':str(tmp_path/'other')})
        other['messages']=[{'id':'retained','role':'assistant','text':'Other own-history'}]
        app.state['sessions']=[root,other]
        app.state['selectedSessionId']=root['id']
        app._publish()
        worker=Worker()
        worker.session=parent
        worker.runtime=SimpleNamespace(generation={'id':'generation','input_ids':['history-input']},
                                       closed=False,stop_requested=False)
        worker.activation_gate=ActivationGate()
        worker.activation_gate.activate()
        worker.start_config={key:root.get(key,0) for key in
            ('interruptionRevision','executionRevision','configurationPendingRevision')}
        worker.context_inputs=['history-input']
        worker.context_bindings={'history-input':{'clientId':None,'targets':[]}}
        owner={'process':SimpleNamespace(returncode=None),'runtimeOwnerId':uuid.uuid4().hex}
        manager.workers[root['id']]=owner
        assert app.runtime.workers[root['id']] is owner
        replies,actors={},[]

        async def host_bridge(operation,args,sid):
            assert sid==root['id'] and args['_runtimeOwnerId']==owner['runtimeOwnerId']
            actors.append(args['_runtimeSessionId'])
            return await app.app_bridge(operation,args,sid)

        manager.app_bridge=host_bridge
        async def write(row,reply):
            assert row is owner
            replies[reply['id']]=reply
        manager._write=write
        async def bridge(operation,args):
            identity=str(uuid.uuid4())
            await manager._bridge(root['id'],owner,{'id':identity,'operation':operation,'args':args})
            reply=replies.pop(identity)
            if reply.get('error'):
                raise RuntimeError(reply['error'])
            return reply['result']
        worker.bridge=bridge
        await install_app_access(parent.coordinator,worker.app_access_bridge(parent.coordinator))
        registry=await install_children(parent,prepared,Runtime('root'),SessionStore(tmp_path/'children'),approvals)
        async def inspect(control):
            denied=await control.execute({'operation':'history','args':{'action':'read','session_id':other['id']}})
            assert not denied.success and 'scope' in str(denied.error)
            allowed=await control.execute({'operation':'history','args':{'action':'read','session_id':other['id'],'scope':'all'}})
            assert allowed.success,allowed.error
            assert allowed.output['messages'][0]['text']=='Other own-history'
            exact={'action':'coordination.read','args':{'sessionId':other['id'],'messageId':'retained'}}
            denied=await control.execute({'operation':'dispatch','args':exact})
            assert not denied.success and 'scope' in str(denied.error)
            allowed=await control.execute({'operation':'dispatch','args':{**exact,'args':{**exact['args'],'scope':'all'}}})
            assert allowed.success,allowed.error
            assert allowed.output['result']['message']['text']=='Other own-history'
        async def execute(self,prompt,context,providers,tools,hooks,coordinator):
            await inspect(tools['app_control'])
            seen.append(coordinator.session_id)
            if mode=='nested' and coordinator.session_id=='child':
                await registry.spawn('worker','nested',registry.sessions['child'],sub_session_id='grandchild')
            return 'done'
        async def checked(*args,**kwargs):
            try:
                return await execute(*args,**kwargs)
            except AssertionError as exc:
                failures.append(exc)
                raise
        monkeypatch.setattr(fixture.FiniteOrchestrator,'execute',checked)
        token=_PERSISTENT.set(mode=='persistent')
        await inspect(parent.coordinator.get('tools')['app_control'])
        await registry.spawn('worker','read',parent,sub_session_id='child',**({'use_subprocess':False} if mode=='recipe' else {}))
        if mode=='resumed':
            await registry.resume('child','read again',parent)
        assert len(seen)==(2 if mode in {'resumed','nested'} else 1)
        assert actors==['root']*4+(['child']*4+['grandchild']*4 if mode=='nested'
                                  else ['child']*(8 if mode=='resumed' else 4))
        assert not replies
        assert app.state['selectedSessionId']==root['id']
    except Exception:
        if failures:
            raise failures[0] from None
        raise
    finally:
        original=sys.exception()
        cleanup_errors=[]
        if token is not None:
            _PERSISTENT.reset(token)
        if parent is not None:
            try:
                await parent.cleanup()
            except BaseException as exc:
                cleanup_errors.append(exc)
        # Always remove the synthetic row before AppService closes its real
        # RuntimeManager, even if native parent cleanup or earlier setup failed.
        if root is not None:
            manager.workers.pop(root['id'],None)
        if app is not None:
            try:
                await app.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            if original is None:
                raise cleanup_errors[0]
            for exc in cleanup_errors:
                original.add_note('History fixture cleanup also failed: '+type(exc).__name__)
