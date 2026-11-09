"""Canonical session writes, authorization, stale previews and actual file tools."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.chat_permissions import snapshot, WRITE_KEYS
from amplifier_web.host.session import _apply_host_policy
from amplifier_web.shared_settings import read_settings
from amplifier_web.session_identity import native_id
from amplifier_web.service import AppError
from test_configuration_authority import app, grant, revoke, finished
from test_coordination_generation import agent, until
from amplifier_web.management import Management
from amplifier_web.service import AppService


def read(app, session=None):
    return snapshot(app.management, session or app.state['sessions'][0])


def args(app, **extra):
    source = app.state['sessions'][0]
    return {'sessionId':source['id'], 'scope':'session', 'expectedRevision':read(app)['revision'], **extra}


async def command(app, action, values, identity, *, as_agent=False):
    source = app.state['sessions'][0]
    result = (await agent(app, source, action, values, identity) if as_agent else
              await app.dispatch(action, values, command_id=identity))
    completion = await finished(app, identity)
    return result, completion


def seed(app, scope, value, session=None):
    source = session or app.state['sessions'][0]
    app.management.settings.update(source['workspace'], scope, lambda _:copy.deepcopy(value),
                                   session_id=native_id(source) if scope == 'session' else None)


async def test_user_read_save_reset_preserves_scopes_and_unrelated_settings(app):
    source, peer = app.state['sessions']
    inherited = {'overrides':{'tool-filesystem':{'config':{'allowed_write_paths':['workspace-extra'],
        'denied_write_paths':['private'], 'denied_read_paths':['secrets']}}}}
    seed(app, 'local', inherited)
    initial = {'providers':{'unrelated':True}, 'overrides':{'tool-filesystem':{'enabled':True,
        'config':{'denied_read_paths':['chat-secrets'], 'max_file_size':42}}}}
    seed(app, 'session', initial)
    seed(app, 'session', {'unrelated':'peer'}, peer)
    paths = [app.management.settings.path(source['workspace'], 'local'),
             app.management.settings.path(peer['workspace'], 'session', session_id=native_id(peer))]
    unchanged = {path:path.read_bytes() for path in paths}
    receipt, done = await command(app, 'permissions.get', {'sessionId':source['id'], 'scope':'session'}, 'read')
    assert done['phase'] == 'ready' and receipt['operationId'] == 'read'
    preview = app.state['permissions']
    assert preview['overridden'] == [] and preview['inherited']['denied'] == [source['workspace']+'/private']
    _, done = await command(app, 'permissions.save', args(app, allowed=[], denied=['restricted']), 'save')
    assert done['phase'] == 'ready'
    assert read(app)['overridden'] == list(WRITE_KEYS)
    reset_args = args(app)
    _, done = await command(app, 'permissions.reset', reset_args, 'reset')
    assert done['phase'] == 'ready' and read(app)['overridden'] == []
    assert app.management.settings.read(source['workspace'], 'session', session_id=native_id(source)) == initial
    assert all(path.read_bytes() == data for path,data in unchanged.items())
    assert (await app.dispatch('permissions.reset', reset_args, command_id='reset'))['duplicate']
    assert not any(call[0] == 'start' for call in app.runtime.calls)


async def test_noop_reset_does_not_create_or_rewrite_session_settings(app):
    source = app.state['sessions'][0]
    path = app.management.settings.path(source['workspace'], 'session', session_id=native_id(source))
    await command(app, 'permissions.reset', args(app), 'absent')
    assert not path.exists() and app.state['permissions']['changed'] is False
    seed(app, 'session', {'overrides':{'another-tool':{'enabled':True}}})
    before = path.read_bytes(), path.stat().st_mtime_ns
    await command(app, 'permissions.reset', args(app), 'unchanged')
    assert (path.read_bytes(),path.stat().st_mtime_ns) == before
    assert not app.runtime.calls


async def test_browser_permission_completion_uses_streamed_action_status_not_full_history(app):
    """Exercise the same demand projection consumed by the chat settings pane."""
    source = app.state['sessions'][0]
    app.clients.attach('permission-browser')
    with app.clients.bind('permission-browser'):
        await app.dispatch('view.update', {'patch': {'panel':'chat-write-files'}})
        for action, extra in [('permissions.get', {}),
                              ('permissions.save', {'allowed':[], 'denied':['private']}),
                              ('permissions.reset', {})]:
            identity = action+'-browser'
            values = {'sessionId':source['id'], 'scope':'session'}
            if action != 'permissions.get':
                values.update(expectedRevision=read(app)['revision'], **extra)
            receipt, done = await command(app, action, values, identity)
            state = app.browser_state()
            assert done['phase'] == 'ready'
            assert 'managementResults' not in state
            assert state['actionStatus'][action]['commandId'] == receipt['operationId']
            assert state['actionStatus'][action]['phase'] == 'ready'
            assert state['permissions']['commandId'] == receipt['operationId']
            assert state['permissions']['sessionId'] == source['id']
            assert state['permissions']['scope'] == 'session'
        _, done = await command(app, 'permissions.reset', {
            'sessionId':source['id'], 'scope':'session', 'expectedRevision':'stale'}, 'stale-browser')
        state = app.browser_state()
        assert done['phase'] == 'error'
        assert 'managementResults' not in state
        assert state['actionStatus']['permissions.reset']['commandId'] == 'stale-browser'
        assert state['actionStatus']['permissions.reset']['phase'] == 'error'
        assert 'Reload' in state['actionStatus']['permissions.reset']['error']


@pytest.mark.parametrize('own_phase', ['ready', 'error'])
async def test_two_http_clients_recover_exact_permission_receipt_after_status_superseded(
        authenticated_client, tmp_path, monkeypatch, own_phase):
    from amplifier_web.server import create_app
    from test_configuration_authority import Runtime
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    host = await create_app(tmp_path/'app', workspace=tmp_path, runtime=Runtime(),
        voice=False, background_updates=False, preload_providers=False)
    service = host['service']
    source, peer = [service._new_session({'title':name}) for name in ('Owner', 'Peer')]
    service.state['sessions'] = [source, peer]
    service.state['selectedSessionId'] = source['id']
    service._publish()
    client = await authenticated_client(host)
    for identity in ('owner-browser', 'peer-browser'):
        attached = await client.post('/api/clients/attach', json={'clientId':identity})
        assert attached.status == 200
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.management.perform
    calls = []
    async def held(action, values, **kwargs):
        calls.append((action, kwargs.get('command_id')))
        if kwargs.get('command_id') == 'own-save':
            entered.set()
            await release.wait()
        return await original(action, values, **kwargs)
    monkeypatch.setattr(service.management, 'perform', held)
    async def send(browser, identity, action, values):
        response = await client.post('/api/actions', headers={
            'X-Amplifier-Client':browser, 'X-Amplifier-State-Transport':'delta-v1'},
            json={'id':identity, 'action':action, 'args':values})
        assert response.status == 200
        return await response.json()
    def save_values(session):
        return {'sessionId':session['id'], 'scope':'session',
            'expectedRevision':snapshot(service.management, session)['revision'],
            'allowed':[], 'denied':[session['id']]}
    own = save_values(source)
    if own_phase == 'error':
        own['expectedRevision'] = 'stale'
    receipt = await send('owner-browser', 'own-save', 'permissions.save', own)
    await entered.wait()
    await send('peer-browser', 'peer-save', 'permissions.save', save_values(peer))
    await until(lambda: service.state['actionStatus']['permissions.save']['commandId'] == 'peer-save')
    release.set()
    assert (await finished(service, 'own-save'))['phase'] == own_phase
    assert (await finished(service, 'peer-save'))['phase'] == 'ready'
    headers = {'X-Amplifier-Client':'owner-browser'}
    browser = await (await client.get('/api/state', headers=headers)).json()
    assert 'managementResults' not in browser
    assert browser['actionStatus']['permissions.save']['commandId'] == 'peer-save'
    assert browser['permissions']['sessionId'] == peer['id']
    # Existing JSON Pointer API reads one immutable completion, not history.
    result = await client.get('/api/state/detail', headers=headers,
        params={'path':'/managementResults/'+receipt['operationId'], 'limit':2})
    assert result.status == 200
    detail = await result.json()
    assert detail['path'] == '/managementResults/own-save' and detail['nextOffset'] is None
    exact = {row['key']:row['value'] for row in detail['items']}
    assert exact['phase'] == own_phase
    assert 'peer-save' not in json.dumps(detail)
    # Recovery reads current policy through the same serialized owner; no write
    # is repeated and no peer preview is accepted as this chat's settings.
    await send('owner-browser', 'recover-read', 'permissions.get', {'sessionId':source['id'], 'scope':'session'})
    assert (await finished(service, 'recover-read'))['phase'] == 'ready'
    current = await (await client.get('/api/state', headers=headers)).json()
    assert current['permissions']['sessionId'] == source['id']
    assert current['permissions']['commandId'] == 'recover-read'
    assert current['permissions']['denied'] == ([str(tmp_path/source['id'])] if own_phase == 'ready' else [])
    assert calls == [('permissions.save','own-save'), ('permissions.save','peer-save'), ('permissions.get','recover-read')]


async def test_reset_prunes_only_emptied_ancestors_and_session_native_alias(app):
    source = app.state['sessions'][0]
    source['nativeIdentity'] = 'native-settings-owner'
    seed(app, 'session', {'overrides':{'tool-filesystem':{'config':{'allowed_write_paths':[]}}}})
    path = app.management.settings.path(source['workspace'], 'session', session_id='native-settings-owner')
    await command(app, 'permissions.reset', args(app), 'reset-native')
    assert path.exists() and app.management.settings.read(source['workspace'], 'session', session_id='native-settings-owner') == {}
    assert not app.management.settings.path(source['workspace'], 'session', session_id=source['id']).exists()


@pytest.mark.parametrize('mutation', ['session', 'inherited', 'inherited-module', 'workspace'])
async def test_preview_binds_current_and_inherited_configuration_even_when_lists_unchanged(app, mutation):
    preview = args(app)
    if mutation == 'workspace':
        app.state['sessions'][0]['workingDirectory'] = app.state['sessions'][0]['workspace']+'/other'
    else:
        scope = 'session' if mutation == 'session' else 'project'
        value = ({'config':{'tools':[{'module':'tool-filesystem','config':{'denied_write_paths':['new']}}]}}
                 if mutation == 'inherited-module' else {'overrides':{'unrelated':{'enabled':True}}})
        seed(app, scope, value)
    _, done = await command(app, 'permissions.reset', preview, 'stale-'+mutation)
    assert done['phase'] == 'error' and 'changed' in done['error']


async def test_preview_is_rechecked_inside_canonical_locked_mutation(app, monkeypatch):
    original = app.management.settings.update
    def raced(workspace, scope, mutator, **kwargs):
        original(workspace, 'project', lambda _: {'overrides':{'tool-filesystem':{'config':{'allowed_write_paths':['new']}}}})
        return original(workspace, scope, mutator, **kwargs)
    monkeypatch.setattr(app.management.settings, 'update', raced)
    _, done = await command(app, 'permissions.reset', args(app), 'locked-stale')
    assert done['phase'] == 'error' and 'workspace defaults changed' in done['error']
    assert app.management.settings.read(app.default_workspace, 'session', session_id=app.state['sessions'][0]['id']) == {}


@pytest.mark.parametrize('action', ['permissions.save', 'permissions.reset'])
async def test_agent_needs_session_grant_and_cannot_borrow_peer_or_child_authority(app, action):
    source, peer = app.state['sessions']
    values = args(app, **({'allowed':[], 'denied':[]} if action.endswith('save') else {}))
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, action, values, 'ungranted')
    await grant(app, ['file_access'], ['local'], 'wrong-scope')
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, action, values, 'wrong')
    row = await grant(app, ['file_access'], ['session'], 'right-scope')
    for identity, overrides, transport in [('peer', {'sessionId':peer['id']}, {}),
        ('child', {}, {'_runtimeSessionId':'native-child'}), ('stale', {}, {'_generationId':'old'})]:
        with pytest.raises(AppError):
            await agent(app, source, action, {**values, **overrides}, identity, **transport)
    _, done = await command(app, action, values, 'authorized', as_agent=True)
    assert done['phase'] == 'ready'
    await revoke(app, row)
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, action, args(app, **({'allowed':[], 'denied':[]} if action.endswith('save') else {})), 'revoked')


@pytest.mark.parametrize('boundary', ['management-wait', 'settings-lock'])
async def test_delayed_reset_rechecks_revocation_at_write_owner(app, monkeypatch, boundary):
    source = app.state['sessions'][0]
    seed(app, 'session', {'overrides':{'tool-filesystem':{'config':{'denied_write_paths':['private']}}}})
    row = await grant(app, ['file_access'], ['session'])
    before = app.management.settings.path(source['workspace'], 'session', session_id=native_id(source)).read_bytes()
    values = args(app)
    if boundary == 'management-wait':
        await app.management.lock.acquire()
        await agent(app, source, 'permissions.reset', values, 'delayed')
        await until(lambda: app.state.get('actionStatus',{}).get('permissions.reset',{}).get('phase') == 'queued')
        await revoke(app, row)
        app.management.lock.release()
    else:
        import amplifier_web.preferences as preferences
        original = preferences.update_settings
        def locked(path, mutator):
            current = app.configuration_authority.record(row['id'])
            app.configuration_authority.save({**current, 'status':'revoked', 'revision':current['revision']+1})
            return original(path, mutator)
        monkeypatch.setattr(preferences, 'update_settings', locked)
        await agent(app, source, 'permissions.reset', values, 'delayed')
    done = await finished(app, 'delayed')
    assert done['phase'] == 'error' and 'revoked' in done['error']
    assert app.management.settings.path(source['workspace'], 'session', session_id=native_id(source)).read_bytes() == before


async def test_lost_result_after_commit_reconciles_by_read_and_never_replays(app, monkeypatch):
    values = args(app, allowed=[], denied=['private'])
    original = app.management.publish
    async def lost(**kwargs):
        if kwargs.get('permissions',{}).get('changed'):
            raise RuntimeError('Result publication interrupted')
        await original(**kwargs)
    monkeypatch.setattr(app.management, 'publish', lost)
    _, done = await command(app, 'permissions.save', values, 'lost')
    assert done['phase'] == 'error'
    saved = read(app)
    assert saved['overridden'] == list(WRITE_KEYS)
    assert (await app.dispatch('permissions.save', values, command_id='lost'))['duplicate']
    assert read(app)['revision'] == saved['revision']
    monkeypatch.setattr(app.management, 'publish', original)
    await command(app, 'permissions.get', {'sessionId':app.state['sessions'][0]['id'],'scope':'session'}, 'reconcile')
    assert app.state['permissions']['revision'] == saved['revision']


async def test_chat_change_refreshes_only_owner_and_defers_active_work(app):
    source, peer = app.state['sessions']
    source['status'] = 'working'
    _, done = await command(app, 'permissions.save', args(app, allowed=[], denied=['private']), 'active-save')
    assert done['phase'] == 'ready' and source['configurationPending']
    assert not peer.get('configurationPending') and not app.runtime.calls
    source['status'] = 'idle'
    await app.refresh_configuration(source['id'])
    assert app.runtime.calls == [('stop',source['id'])]
    assert not source['configurationPending'] and not peer.get('configurationPending')
    assert not source.get('fileAccessPending')


@pytest.mark.parametrize('after_write', [False, True])
async def test_durable_fence_precedes_file_write_and_survives_failure_and_restart(app, monkeypatch, after_write):
    import amplifier_foundation.settings as foundation
    from test_configuration_authority import Runtime
    source = app.state['sessions'][0]
    source['status'] = 'working'
    source['workers'] = [{'id':'persistent','persistent':True,'status':'idle'}]
    path = app.management.settings.path(source['workspace'], 'session', session_id=native_id(source))
    original = foundation.atomic_write
    def fail(path_arg, contents, **kwargs):
        if path_arg == path:
            saved = json.loads(app.db.execute("SELECT value FROM state_records WHERE kind='session' AND id=?", (source['id'],)).fetchone()[0])
            from amplifier_web.session_projection import hydrate
            state = {'sessions':[saved]}
            hydrate(app.data_dir, state, app.db)
            assert state['sessions'][0]['fileAccessPending'] and state['sessions'][0]['configurationPending']
            if after_write: original(path_arg, contents, **kwargs)
            raise OSError('Controlled settings-write interruption')
        return original(path_arg, contents, **kwargs)
    monkeypatch.setattr(foundation, 'atomic_write', fail)
    _, done = await command(app, 'permissions.save', args(app,allowed=[],denied=['private']), 'interrupted')
    assert done['phase'] == 'error' and done['error'] == 'Controlled settings-write interruption'
    assert source['fileAccessPending']
    assert bool(read(app)['overridden']) is after_write
    monkeypatch.setattr(foundation, 'atomic_write', original)
    await app.close()
    runtime = Runtime()
    runtime.workers = {}  # A new host never adopts the old process.
    restored = AppService(app.data_dir, runtime, workspace=app.default_workspace)
    try:
        restored.management = Management(restored)
        await until(lambda:not restored._session(source['id']).get('fileAccessPending'))
        assert not runtime.calls  # No silent turn/start, even with old child rows.
        assert bool(read(restored, restored._session(source['id']))['overridden']) is after_write
    finally:
        await restored.close()


@pytest.mark.parametrize('action,extra', [
    ('conversation.send',{'text':'new work'}), ('worker.spawn',{'instruction':'new child'}),
    ('worker.message',{'id':'persistent','text':'new child turn'}),
    ('worker.steer',{'id':'persistent','text':'new instruction'}), ('call.start',{})])
async def test_persistent_child_pending_policy_fences_new_input_until_retirement(app, action, extra):
    source = app.state['sessions'][0]
    source['workers'] = [{'id':'persistent','persistent':True,'status':'idle'}]
    await command(app, 'permissions.save', args(app,allowed=[],denied=['private']), 'pending')
    assert source['fileAccessPending'] and source['configurationPending'] and not app.runtime.calls
    with pytest.raises(AppError, match='Finish or stop existing child'):
        await app.dispatch(action, {'sessionId':source['id'], **extra}, command_id='new-input')
    assert not app.runtime.calls
    app.runtime.stop_worker = AsyncMock(return_value={'stopped':True})
    await app.dispatch('worker.stop', {'sessionId':source['id'],'id':'persistent'}, command_id='stop-child')
    await until(lambda:app.runtime.stop_worker.await_count == 1)
    source['workers'][0]['status'] = 'stopped'
    await app.refresh_configuration(source['id'])
    assert not source.get('fileAccessPending') and app.runtime.calls == [('stop',source['id'])]


async def test_pending_policy_allows_answers_approvals_and_stop_but_fences_voice(app):
    from test_questions import create_question
    source = app.state['sessions'][0]
    source['fileAccessPending'] = True
    app.runtime.send = AsyncMock(return_value={})
    question = await create_question(app, source['id'])
    answer = await app.dispatch('question.answer', {'sessionId':source['id'],'id':question['id'],
        'expectedRevision':question['revision'],'optionId':'full'}, command_id='answer-existing')
    assert answer['result']['status'] == 'answered'
    request = (await agent(app, source, 'capability.request', {'sessionId':source['id'],
        'effects':['file_access'],'scopes':['session'],'reason':'User requested write reset'}, 'request'))['result']
    assert (await app.dispatch('approval.respond', {'sessionId':source['id'],'id':request['id'],
        'decision':'allow'}, command_id='approve-existing'))['result']['status'] == 'active'
    with pytest.raises(AppError, match='Finish or stop existing child'):
        await app.voice_delegate('new voice turn', 'new-voice', source['id'])
    await app.dispatch('conversation.stop', {'sessionId':source['id']}, command_id='stop-root')
    await until(lambda:('stop',source['id']) in app.runtime.calls)


async def test_queued_peers_wait_and_inflight_guard_refuses_pending_policy(app):
    source, peer = app.state['sessions']
    peer['fileAccessPending'] = True
    app.runtime.collaboration_input = AsyncMock(return_value={'accepted':True})
    receipt = await agent(app, source, 'coordination.send',
        {'sessionId':peer['id'], 'text':'bounded peer work'}, 'peer-work')
    await asyncio.sleep(.02)
    assert app.collaboration.receipt('peer-work')['delivery'] == 'queued'
    app.runtime.collaboration_input.assert_not_awaited()
    assert 'pending file-access' in app.collaboration.guard(app.collaboration.receipt('peer-work'))
    peer.pop('fileAccessPending')
    await app.collaboration.drain(peer['id'])
    app.runtime.collaboration_input.assert_awaited_once()
    assert app.collaboration.receipt('peer-work')['delivery'] == 'accepted'


@pytest.mark.parametrize('late', [False, True])
async def test_schedule_admission_cannot_use_old_policy(tmp_path, monkeypatch, late):
    from test_schedules import fixture, schedule
    service, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        await schedule(service, sid, now[0])
        if late:
            runtime.before_admission = lambda:service._session(sid).update(fileAccessPending=True)
        else:
            service._session(sid)['fileAccessPending'] = True
        now[0] += 61
        await service.schedules.tick()
        assert not runtime.inputs
        if late:
            assert service.schedules.store.runs(sid)[0]['phase'] == 'skipped'
        else:
            assert not service.schedules.store.runs(sid)
    finally:
        await service.close()


async def test_observation_preview_and_handoff_recheck_pending_policy(tmp_path, monkeypatch):
    from test_observations import fixture, create, record
    service, runtime, now, sid, values, data, _, _ = await fixture(tmp_path, monkeypatch)
    try:
        watch = await create(service, values)
        service._session(sid)['fileAccessPending'] = True
        with pytest.raises(AppError, match='unresolved configuration'):
            await service.dispatch('observation.preview', values)
        assert service.observations.reason(watch) == 'The execution configuration is changing'
        service._session(sid).pop('fileAccessPending')
        async def handoff(target, arguments, guard):
            service._session(sid)['fileAccessPending'] = True
            reason = guard()
            assert reason == 'The execution configuration is changing'
            return {'accepted':False,'reason':reason}
        runtime.observation_input = AsyncMock(side_effect=handoff)
        record(data/'record.json', 'actionable', 'new')
        await service.observations.tick()
        runtime.observation_input.assert_awaited_once()
        assert service.observations.store.rows('outbox')[0]['phase'] == 'suppressed'
    finally:
        await service.close()


@pytest.mark.parametrize('engine', ['native', 'function'])
async def test_actual_mounted_tools_narrow_and_reset_restores_inherited_with_child_ceilings(app, tmp_path, engine):
    pytest.importorskip('amplifier_module_tool_filesystem')
    pytest.importorskip('amplifier_module_tool_apply_patch')
    from test_file_source_guard import mounted
    from amplifier_web.host.children import child_plan
    from amplifier_web.host.config import load_config
    from amplifier_web.resource_access import observe
    source, peer = app.state['sessions']
    external = tmp_path.parent / (tmp_path.name+'-'+engine+'-files')
    external.mkdir()
    seed(app, 'local', {'overrides':{'tool-filesystem':{'config':{'allowed_write_paths':[str(external)]}}}})
    shared = app.management.settings.path(source['workspace'], 'local')
    shared_bytes = shared.read_bytes()
    async def mount_current():
        # The actual root load_config and host policy owners, followed by the
        # actual native filesystem and patch mounts. No provider turn or server.
        config = load_config(source['workspace'], session_id=native_id(source))
        bundle = SimpleNamespace(tools=[{'module':'tool-filesystem'}, {'module':'tool-apply-patch'}], agents={})
        _apply_host_policy(bundle, config)
        coordinator, tools = await mounted(tmp_path, tmp_path/'locks', engine, **bundle.tools[0]['config'])
        return bundle, coordinator, tools
    initial, _, tools = await mount_current()
    assert (await tools['write_file'].execute({'file_path':str(external/'before.txt'),'content':'before'})).success
    await command(app, 'permissions.save', args(app, allowed=[], denied=[]), 'narrow')
    narrow, coordinator, tools = await mount_current()
    assert not (await tools['write_file'].execute({'file_path':str(external/'blocked.txt'),'content':'blocked'})).success
    assert (await observe(SimpleNamespace(coordinator=coordinator),[str(external)]))['paths'][str(external)]['status'] == 'denied'
    await command(app, 'permissions.reset', args(app), 'restore')
    restored, coordinator, tools = await mount_current()
    assert (await tools['write_file'].execute({'file_path':str(external/'after.txt'),'content':'after'})).success
    assert (await observe(SimpleNamespace(coordinator=coordinator),[str(external)]))['paths'][str(external)]['status'] == 'allowed'
    retained = child_plan({'tools':narrow.tools}, {'tools':restored.tools}, working_dir=tmp_path)
    _, children = await mounted(tmp_path, tmp_path/'child-locks', engine, **retained['tools'][0]['config'])
    assert not (await children['write_file'].execute({'file_path':str(external/'child.txt'),'content':'child'})).success
    # A declared read-only ceiling survives reset for filesystem/patch/image.
    bundle = SimpleNamespace(tools=[{'module':'tool-filesystem','config':{'allowed_write_paths':[]}},
                                   {'module':'tool-apply-patch'}, {'module':'tool-image'}], agents={})
    _apply_host_policy(bundle, SimpleNamespace(workspace=tmp_path,settings=read_settings(tmp_path,session_id=native_id(source))))
    assert all(row['config']['allowed_write_paths'] == [] for row in bundle.tools)
    assert restored.tools == initial.tools and shared.read_bytes() == shared_bytes
    assert not app.management.settings.path(peer['workspace'],'session',session_id=native_id(peer)).exists()


async def test_native_core_coordinator_mount_obeys_save_and_reset_without_provider_calls(app, tmp_path, monkeypatch):
    from amplifier_core import AmplifierSession
    import amplifier_module_tool_filesystem as filesystem
    from amplifier_web.host.config import load_config
    async def deny_network(*unused, **kwargs):
        raise AssertionError('File-policy acceptance must not send provider requests')
    monkeypatch.setattr('httpx.AsyncClient.send', deny_network)
    source = app.state['sessions'][0]
    external = tmp_path.parent/(tmp_path.name+'-native-files')
    external.mkdir()
    seed(app, 'local', {'overrides':{'tool-filesystem':{'config':{'allowed_write_paths':[str(external)]}}}})
    async def write_on_new_mount(name, target=external):
        config = load_config(source['workspace'], session_id=native_id(source))
        bundle = SimpleNamespace(tools=[{'module':'tool-filesystem'}], agents={})
        _apply_host_policy(bundle, config)
        native = AmplifierSession({'session':{
            'orchestrator':{'module':'loop-live','config':{'configured_bundle':True,'native_provider':True}},
            'context':{'module':'context-simple'}}, 'providers':[]})
        try:
            await native.initialize()
            native.coordinator.register_capability('session.working_dir', source['workspace'])
            await filesystem.mount(native.coordinator, bundle.tools[0]['config'])
            tools = native.coordinator.get('tools')
            return await tools['write_file'].execute({'file_path':str(target/name),'content':name})
        finally:
            await native.cleanup()
    assert (await write_on_new_mount('initial.txt')).success
    await command(app, 'permissions.save', args(app,allowed=[],denied=[]), 'native-narrow')
    assert not (await write_on_new_mount('denied.txt')).success
    # The #712 positive case: exact approved session authority and saved policy
    # become effective on the next native mount without granting a sibling path.
    await grant(app, ['file_access'], ['session'], 'native-exact-directory-grant')
    _, saved = await command(app, 'permissions.save',
        args(app, allowed=[str(external)], denied=[]), 'native-allow', as_agent=True)
    assert saved['phase'] == 'ready'
    assert (await write_on_new_mount('approved.txt')).success
    sibling = external.with_name(external.name + '-unapproved')
    sibling.mkdir()
    assert not (await write_on_new_mount('sibling.txt', sibling)).success
    assert not (sibling/'sibling.txt').exists()
    assert read(app)['allowed'] == [str(external)]
    await command(app, 'permissions.reset', args(app), 'native-reset')
    assert (await write_on_new_mount('restored.txt')).success
    assert not (external/'denied.txt').exists()


async def test_pending_access_fences_native_validation_and_configuration_inheritance(app):
    from amplifier_web.integration_validation import authority
    from amplifier_web.voice import VoiceCall, VoiceService, VoiceError
    source = app.state['sessions'][0]
    source['fileAccessPending'] = True
    # Passive evidence remains available; only command admission is refused.
    assert authority(app, source['id'])['binding']
    with pytest.raises(ValueError, match='configuration changes'):
        authority(app, source['id'], admission=True)
    call = VoiceCall(VoiceService(app), source['id'])
    with pytest.raises(VoiceError, match='no longer available'):
        await call.require_admission()
    voice = VoiceService(app, api_key='synthetic', http=object())
    with pytest.raises(VoiceError, match='changing execution ownership or configuration'):
        await voice.connect('v=0', session_id=source['id'])
    with pytest.raises(AppError, match='file settings'):
        await agent(app, source, 'coordination.create', {'title':'child','text':'inherited work'}, 'no-new-task')
    with pytest.raises(AppError, match='configuration changes'):
        await app.dispatch('session.fork', {'id':source['id']}, command_id='no-fork')
    with pytest.raises(AppError, match='Source configuration is changing'):
        await app.dispatch('session.create', {'workspace':source['workspace'],'bundle':source['bundle'],
            'inheritConfiguration':{'sessionId':source['id'],'configurationHash':'not-used'}}, command_id='no-inheritance')


async def test_approved_session_save_updates_active_mounted_tools_without_turn_or_retirement(app):
    from test_write_policy_refresh import root, create
    from pathlib import Path
    source, peer = app.state['sessions']
    source['status'] = 'working'
    source['workers'] = [{'id': 'existing-child', 'persistent': True, 'status': 'idle'}]
    coordinator, tools, policy = await root(Path(source['workspace']), session_id=native_id(source))
    before = copy.deepcopy(source['collaborationGeneration'])
    epoch = source.get('configurationPendingRevision', 0)
    async def refresh(sid, revision):
        assert sid == source['id'] and source['fileAccessPending']
        return policy.refresh(expected=revision) == {
            'settingsRevision': revision, 'boundary': 'next-tool-call'}
    app.runtime.refresh_file_access = AsyncMock(side_effect=refresh)
    await grant(app, ['file_access'], ['session'])
    external = Path(source['workspace']).parent / 'approved'
    assert not (await tools['apply_patch'].execute(create(external / 'next'))).success
    values = args(app, allowed=[str(external)], denied=[])
    _, done = await command(app, 'permissions.save', values, 'live-save', as_agent=True)
    assert done['phase'] == 'ready'
    assert not source.get('fileAccessPending') and not source['configurationPending']
    assert source['status'] == 'working' and source['collaborationGeneration'] == before
    assert source.get('configurationPendingRevision', 0) == epoch
    assert source['workers'][0]['status'] == 'idle'
    assert not peer.get('configurationPending') and not app.runtime.calls
    assert (await tools['apply_patch'].execute(create(external / 'next'))).success
    assert (await agent(app, source, 'permissions.save', values, 'live-save'))['duplicate']
    assert app.runtime.refresh_file_access.await_count == 1


@pytest.mark.parametrize('race', ['changed-settings', 'new-revision', 'lost-reply', 'other-pending'])
async def test_live_refresh_never_clears_an_unknown_or_superseded_fence(app, race):
    source = app.state['sessions'][0]
    source['status'] = 'working'
    async def refresh(sid, revision):
        if race == 'changed-settings':
            seed(app, 'session', {'overrides': {'tool-filesystem': {'config': {'denied_write_paths': ['new']}}}})
        elif race == 'new-revision':
            source['configurationPendingRevision'] = source.get('configurationPendingRevision', 0) + 1
        return race != 'lost-reply'
    app.runtime.refresh_file_access = AsyncMock(side_effect=refresh)
    if race == 'other-pending':
        source['configurationPending'] = True
    _, done = await command(app, 'permissions.save', args(app, allowed=[], denied=['private']), 'race-save')
    assert done['phase'] == 'ready'
    assert source['fileAccessPending'] and source['configurationPending']
    assert app.runtime.refresh_file_access.await_count == (0 if race == 'other-pending' else 1)
    assert not app.runtime.calls


async def test_live_write_refresh_preserves_authenticated_existing_child_scope(app):
    from amplifier_web.child_execution import current
    from amplifier_web.collaboration import BINDING, PRINCIPAL
    source = app.state['sessions'][0]
    source['status'] = 'working'
    app.runtime.workers = {source['id']: {'runtimeOwnerId': 'same-process',
        'process': SimpleNamespace(returncode=None)}}
    origin = {'rootSessionId': native_id(source), 'runtimeSessionId': 'native-child',
        'generationId': 'assigned-generation', 'runId': 'assigned-run', 'inputIds': ['user-input'],
        **{key: source.get(key, 0) for key in ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')}}
    app.runtime.refresh_file_access = AsyncMock(return_value=True)
    await command(app, 'permissions.save', args(app, allowed=[], denied=['private']), 'child-live-save')
    binding = BINDING.set({'_childOrigin': origin, '_runtimeOwnerId': 'same-process',
                          '_generationId': 'assigned-generation'})
    principal = PRINCIPAL.set('native-child')
    try:
        assert current(app, source) == origin
    finally:
        PRINCIPAL.reset(principal)
        BINDING.reset(binding)


async def test_unrelated_invalidation_before_live_dispatch_is_not_erased(app, monkeypatch):
    source = app.state['sessions'][0]
    source['status'] = 'working'
    app.runtime.refresh_file_access = AsyncMock(return_value=True)
    original = app.management.invalidate_configuration
    async def raced(**kwargs):
        # An ordinary invalidation takes ownership between canonical save and
        # write-only invalidation. Its epoch must remain a hard child fence.
        await original(warm=False, session_ids={source['id']})
        await original(**kwargs)
    monkeypatch.setattr(app.management, 'invalidate_configuration', raced)
    await command(app, 'permissions.save', args(app, allowed=[], denied=['private']), 'raced-invalidation')
    assert source['fileAccessPending'] and source['configurationPending']
    assert app.runtime.refresh_file_access.await_count == 0
