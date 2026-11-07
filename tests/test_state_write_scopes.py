"""Scoped commits must match complete persistence without visiting other chats."""
import copy
import pytest
from amplifier_web.service import AppService
from amplifier_web.state_records import load


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path/'app', workspace=tmp_path)
    await service.dispatch('session.create', {'title':'Other'})
    await service.dispatch('session.create', {'title':'Current'})
    yield service
    await service.close()


@pytest.mark.parametrize('action,args', [
    ('view.update', {'patch':{'navExpanded':True}}),
    ('view.update', {'patch':{'draft':'A saved draft'}}),
    ('attention.read', {'ids':[]}),
    ('settings.update', {'patch':{'notifications':False}}),
    ('theme.reset', {}),
    ('session.rename', {'title':'Renamed'}),
    ('session.archive', {}),
    ('session.restore', {}),
    ('session.pinOrder', {'ids':[]}),
])
async def test_command_scope_is_durable_and_matches_complete_reconciliation(app, monkeypatch, action, args):
    sid=app.state['selectedSessionId']
    args=copy.deepcopy(args)
    if action in {'session.rename','session.archive','session.restore'}:args['id']=sid
    scopes=[]; original=app._save
    def capture():
        scope=app._publish_save_scope
        assert scope is not None, 'A narrow command attempted a whole-app save'
        assert scope <= {sid}, 'Unrelated conversation visited'
        scopes.append((set(scope),set(app._publish_global_keys)))
        original()
    with monkeypatch.context() as patch:
        patch.setattr(app,'_save',capture)
        await app.dispatch(action,args,command_id='scoped',include_state=False)
    assert scopes
    scoped=load(app.db)
    app._save()
    complete=load(app.db)
    assert scoped==complete
    duplicate=await app.dispatch(action,args,command_id='scoped',include_state=False)
    assert duplicate['duplicate']


@pytest.mark.parametrize('kind,payload', [
    ('assistant.message', {'text':'Final answer'}),
    ('runtime.error', {'error':'Synthetic failure'}),
    ('runtime.delivery', {'inputId':'none'}),
    ('runtime.status', {'status':'working'}),
    ('runtime.warmth', {'status':'warm'}),
])
async def test_single_chat_lifecycle_does_not_checkpoint_other_chats(app,monkeypatch,kind,payload):
    sid=app.state['selectedSessionId']; saved=app._save; scopes=[]
    def capture():
        scopes.append(app._publish_save_scope)
        assert app._publish_save_scope=={sid}
        saved()
    with monkeypatch.context() as patch:
        patch.setattr(app,'_save',capture)
        await app.on_runtime_event(kind,{'sessionId':sid,**payload})
    assert scopes
    before=load(app.db); app._save(); assert load(app.db)==before


async def test_joined_publication_keeps_both_scopes_and_failure_retry(app,monkeypatch):
    sid=app.state['selectedSessionId']
    app._session(sid)['title']='Changed by runtime'
    app._publish_progress(session_ids={sid},record_only=True)
    app._state['notificationError']='A global change'
    saved=app._save
    def fail(): raise OSError('fixture commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(app,'_save',fail)
        with pytest.raises(OSError): app._publish_changes(globals={'notificationError'})
    assert app._progress_session_ids=={sid}
    assert app._progress_global_keys=={'notificationError'}
    app._commit_pending_progress()
    state=load(app.db)
    assert state['notificationError']=='A global change'
    # Presentation reference and body committed together by the retried scope.
    assert app._session_projection_refs[sid] in state['sessions']
    assert not app._progress_dirty


def test_production_writers_declare_scope_or_explicit_full_reconciliation():
    """New callbacks cannot silently inherit a whole-library save."""
    import ast
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'amplifier_web'
    boundaries={'_save_changes','_publish_full','_save_full','_publish','_commit_pending_progress'}
    violations=[]
    for path in root.rglob('*.py'):
        tree=ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
            for call in ast.walk(function):
                if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute):continue
                method=call.func.attr
                keys={kw.arg for kw in call.keywords}
                if method in {'_publish','_save','_publish_progress'} and 'session_ids' not in keys:
                    if path.name=='service.py' and function.name in boundaries:continue
                    violations.append(f'{path.name}:{call.lineno}: {method} has no scope')
                if method in {'_publish_full','_save_full'} and 'reason' not in keys:
                    violations.append(f'{path.name}:{call.lineno}: full save has no reason')
    assert not violations, '\n'.join(violations)


async def test_explicit_runtime_catalog_read_persists_only_its_owner(app,monkeypatch):
    from amplifier_web.management import Management
    manager=Management(app)
    sid=app.state['selectedSessionId']
    scopes=[]; save=app._save
    def capture():
        assert app._publish_save_scope is not None and app._publish_save_scope<={sid}
        scopes.append(set(app._publish_save_scope));save()
    async def control(session_id,operation,args):
        assert session_id==sid
        return {'providers':[]} if operation=='configuration.providers' else {'models':[]}
    async def ready(*args,**kwargs):pass
    from types import SimpleNamespace
    app.runtime=SimpleNamespace(control=control,close=ready)
    with monkeypatch.context() as patch:
        patch.setattr(app,'_save',capture)
        patch.setattr(manager,'ensure_runtime',ready)
        await manager.command('runtime.control',{'sessionId':sid,'operation':'configuration.providers','args':{}},'explicit')
    saved=load(app.db)
    assert saved['runtimeControl'][sid]['configuration.providers']=={'providers':[]}
    assert 'modelCatalogs' not in saved['runtimeControl'][sid]
    assert {sid} in scopes
    app.runtime=None
    app._save()
    assert load(app.db)==saved
    await manager.provider_catalog.close()


async def test_canvas_scope_keeps_content_versions_and_private_tabs(app):
    app.clients.attach('canvas-viewer')
    with app.clients.bind('canvas-viewer'):
        await app.dispatch('canvas.show',{'kind':'markdown','content':'First version','title':'Example'},include_state=False)
        first=load(app.db)
        app._save()
        assert load(app.db)==first
        await app.dispatch('canvas.show',{'kind':'markdown','content':'Second version','title':'Another example'},include_state=False)
        second=load(app.db)
        app._save()
        assert load(app.db)==second


async def test_worktree_inspection_without_managed_checkout_saves_owner(app, monkeypatch):
    sid = app.state['selectedSessionId']
    saved = app._save
    scopes = []
    def capture():
        scopes.append(app._publish_save_scope)
        saved()
    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', capture)
        await app.dispatch('worktree.list', {'sessionId': sid})
    assert scopes == [{sid}]
    before = load(app.db)
    app._save()
    assert load(app.db) == before


@pytest.mark.parametrize('event,delivery', [
    ('steering.applied', 'applied'),
    ('steering.held', 'held'),
    ('steering.unknown', 'unknown'),
])
async def test_peer_steering_persists_both_reference_owners_only(app, monkeypatch, event, delivery):
    from amplifier_web.collaboration import PROTOCOL
    from amplifier_web.session_projection import hydrate

    source, target = app.state['sessions'][:2]
    unrelated = app._new_session({'title': 'Unrelated'})
    app.state['sessions'].append(unrelated)
    request = 'scoped-steering'
    target['collaborationGeneration'] = {
        'id': 'target-generation', 'inputIds': [], 'terminal': False,
    }
    app.collaboration.insert(request, 'fixture', {
        'requestId': request, 'inputId': request,
        'commandAction': 'coordination.send', 'protocol': PROTOCOL,
        'senderSessionId': source['id'], 'target': {'sessionId': target['id']},
        'workspace': source['workspace'], 'mode': 'steer',
        'targetGenerationId': 'target-generation', 'delivery': 'pending_steer',
    })
    app._publish_full(reason='Initialize three-root scoped-persistence fixture')
    before = load(app.db)
    unrelated_before = next(row for row in before['sessions'] if row['id'] == unrelated['id'])
    owners = {source['id'], target['id']}
    scopes = []
    original = app._save

    def capture():
        scopes.append(set(app._publish_save_scope))
        assert app._publish_save_scope == owners
        original()

    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', capture)
        await app.on_runtime_event('runtime.steering', {
            'sessionId': target['id'], 'input_id': request,
            'target_generation_id': 'target-generation', 'event': event,
        })
    assert scopes
    assert app.collaboration.receipt(request)['delivery'] == delivery
    persisted = load(app.db)
    assert next(row for row in persisted['sessions'] if row['id'] == unrelated['id']) == unrelated_before
    # Read committed projections before any full save can hide a missing owner.
    hydrate(app.data_dir, persisted, app.db)
    for row in persisted['sessions']:
        if row['id'] in owners:
            saved = next(r for r in row['coordinationReference']['requests'] if r['requestId'] == request)
            assert saved['delivery'] == delivery


async def test_sealed_result_and_continuation_persist_only_both_owners(app, monkeypatch):
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.collaboration import PROTOCOL, fingerprint
    from amplifier_web.session_projection import hydrate

    source, target = app.state['sessions'][:2]
    source['status'] = 'working'  # Keep the new continuation queued; no runtime needed.
    unrelated = app._new_session({'title': 'Unrelated'})
    app.state['sessions'].append(unrelated)
    request, continuation = 'scoped-result', 'scoped-continuation'
    generation, text = 'recipient-generation', 'Retained checked result'
    target['collaborationGeneration'] = {
        'id': generation, 'inputIds': [request], 'terminal': False,
    }
    app.collaboration.insert(request, 'fixture', {
        'requestId': request, 'inputId': request,
        'commandAction': 'coordination.send', 'protocol': PROTOCOL,
        'senderSessionId': source['id'], 'target': {'sessionId': target['id']},
        'workspace': source['workspace'], 'mode': 'queue', 'delivery': 'accepted',
        'response': {
            'status': 'staged', 'generationId': generation, 'kind': 'result',
            'outcome': 'success', 'text': text, 'references': ['result.txt'],
        },
        'subscription': {
            'status': 'waiting', 'continuationId': continuation,
            'interruptionRevision': source.get('interruptionRevision', 0),
            'taskId': None, 'taskRevision': None,
        },
    })
    app._publish_full(reason='Initialize scoped result and continuation fixture')
    before = load(app.db)
    unrelated_before = next(row for row in before['sessions'] if row['id'] == unrelated['id'])
    owners = {source['id'], target['id']}
    scopes, original = [], app._save

    def capture():
        assert app._publish_save_scope == owners
        scopes.append(set(app._publish_save_scope))
        original()

    anchor = {
        'messageId': display_identity(target, 1, 'assistant', text),
        'nativeIndex': 1, 'nativeText': text, 'textDigest': fingerprint(text),
        'rootSessionId': target.get('runtimeSessionId') or target.get('nativeIdentity') or target['id'],
        'generationId': generation,
    }
    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', capture)
        await app.on_runtime_event('runtime.generation', {
            'sessionId': target['id'], 'rootSessionId': target['id'],
            'event': 'generation.finished', 'generation_id': generation,
            'input_ids': [request], 'active_job_ids': [], 'text': text,
            'disposition': 'manager_turn_finished', 'nativeTerminal': anchor,
        })
    assert scopes == [owners, owners]  # Nested queue claim, then outer lifecycle.
    result = app.collaboration.receipt(request)
    assert result['response']['status'] == 'sealed'
    assert result['response']['qualified']
    assert result['subscription']['status'] == 'claimed'
    assert app.collaboration.receipt(continuation)['delivery'] == 'queued'
    persisted = load(app.db)
    assert next(row for row in persisted['sessions'] if row['id'] == unrelated['id']) == unrelated_before
    hydrate(app.data_dir, persisted, app.db)
    for row in persisted['sessions']:
        if row['id'] in owners:
            reference = next(r for r in row['coordinationReference']['requests']
                             if r['requestId'] == continuation)
            assert reference['delivery'] == 'queued'
    saved_source = next(row for row in persisted['sessions'] if row['id'] == source['id'])
    assert [m['inputId'] for m in saved_source['messages'] if m.get('inputId') == continuation] == [continuation]
