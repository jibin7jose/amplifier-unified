"""Exact-owner read-only recovery keeps uncertain effects and backend boundaries."""
import asyncio
import copy
import json
from pathlib import Path
import threading

from filelock import FileLock
import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_recovery as recovery
from amplifier_web.service import AppError
from test_configuration_authority import app, grant, revoke
from test_coordination_generation import agent
from test_coordination_generation import app as collaboration_app, until
from test_task_environments import backend, scope, start, act
from test_test_queue import queue, submit, drive, record
from test_test_environment_worker import library, Failure, worker


def observation(row, status='absent', **changes):
    proof = {'backendIdentity': row.get('backendIdentity', {'backend': 'docker', 'daemon': 'owned-daemon'}),
             'rootOwner': row['sessionId'], 'operationId': row['launchOperationId'],
             'universeId': row['universeId'], 'ownership': 'absent' if status == 'absent' else 'matched',
             'absenceConfirmed': status == 'absent', 'admissionSettled': True, **changes}
    return {'ok': True, 'status': status, 'universeId': row['universeId'], 'observation': proof}


def observer(monkeypatch, status='absent', **changes):
    calls = []
    def inspect(config, home, row, action, args):
        calls.append(action)
        assert action == 'reconcile'
        return observation(row, status, **changes)
    monkeypatch.setattr(owner, 'invoke', inspect)
    return calls


async def uncertain(app, monkeypatch):
    row = (await start(app))['resource']
    def lost(*args):
        raise TimeoutError('lost acknowledgement')
    monkeypatch.setattr(owner, 'invoke', lost)
    result = await act(app, 'execute', row, 'uncertain-command', command='check-once')
    return result['resource'], result['request']


async def test_absence_releases_owner_but_preserves_original_unknown_and_never_replays(app, backend, monkeypatch):
    original = owner.invoke
    row, unknown = await uncertain(app, monkeypatch)
    calls = observer(monkeypatch)
    result = await act(app, 'reconcile', row, 'inspect-absence')
    assert result['request']['state'] == 'completed'
    assert result['resource']['status'] == 'removed'
    assert result['resource']['lastOperation'] == unknown
    assert result['resource']['lastRecovery'] == result['request']
    assert app.operations.requests.read(row['sessionId'], unknown['requestId']) == unknown
    duplicate = await act(app, 'reconcile', row, 'inspect-absence')
    assert duplicate['request'] == result['request'] and calls == ['reconcile']
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert page['items'][0]['status'] == 'removed' and page['items'][0]['lastOperation'] == unknown
    assert calls == ['reconcile']
    monkeypatch.setattr(owner, 'invoke', original)
    assert (await start(app, 'after-confirmed-absence'))['resource']['id'] != row['id']


@pytest.mark.parametrize('state', ['running', 'stopped', 'unknown'])
async def test_inspection_never_stops_removes_or_repeats_command(app, backend, monkeypatch, state):
    row, unknown = await uncertain(app, monkeypatch)
    calls = observer(monkeypatch, state)
    result = await act(app, 'reconcile', row, 'inspect-'+state)
    assert result['request']['state'] == 'completed'
    assert result['resource']['status'] == state
    assert result['resource']['lastOperation'] == unknown
    assert calls == ['reconcile']
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


@pytest.mark.parametrize('changes', [
    {'rootOwner': 'foreign-root'}, {'operationId': 'foreign-launch'}, {'universeId': 'foreign-physical'},
    {'backendIdentity': {'backend': 'docker', 'daemon': 'another-daemon'}},
    {'ownership': 'matched'}, {'absenceConfirmed': False}, {'admissionSettled': False},
])
async def test_foreign_or_unsettled_absence_never_retires_owner(app, backend, monkeypatch, changes):
    row, unknown = await uncertain(app, monkeypatch)
    calls = observer(monkeypatch, **changes)
    result = await act(app, 'reconcile', row, 'invalid-absence')
    assert result['request']['state'] == 'failed'
    assert result['resource']['lastOperation'] == unknown
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
    assert calls == ['reconcile']


async def test_permission_denied_is_not_absence(app, backend, monkeypatch):
    row = (await start(app))['resource']
    monkeypatch.setattr(owner, 'invoke', lambda *a: {'ok': False, 'code': 'permission_denied', 'status': 'unknown'})
    result = await act(app, 'reconcile', row, 'denied-inspection')
    assert result['request']['state'] == 'failed'
    assert result['resource']['status'] == 'running'
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


@pytest.mark.parametrize('proof', [False, True])
async def test_legacy_identity_needs_durable_launch_binding(app, backend, monkeypatch, proof):
    public = (await start(app))['resource']
    row = owner.get(app.data_dir, public['sessionId'], public['id'])
    row = owner.save(app.data_dir, row, backendIdentity=None)
    observer(monkeypatch, backendIdentity={'backend': 'docker', 'daemon': 'owned-daemon'},
             **({'backendIdentitySource': 'retained-launch'} if proof else {}))
    result = await act(app, 'reconcile', owner.public(app.data_dir, row), 'legacy-inspect')
    assert result['request']['state'] == ('completed' if proof else 'failed')
    assert (result['resource']['status'] == 'removed') is proof


async def test_current_generation_and_owner_lock_guard_recovery(app, backend, monkeypatch):
    row = (await start(app))['resource']
    calls = observer(monkeypatch)
    with pytest.raises(AppError, match='changed'):
        await act(app, 'reconcile', {**row, 'revision': 1}, 'stale-inspection')
    lock = app.data_dir / 'workspace-resources' / ('environment-'+row['id']+'.lock')
    with FileLock(str(lock)):
        denied = await act(app, 'reconcile', row, 'locked-inspection')
    assert denied['request']['state'] == 'failed' and not calls
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['revision'] == row['revision']
    entered, release = threading.Event(), threading.Event()
    def paused(config, home, raw, action, args):
        assert action == 'reconcile'
        entered.set()
        assert release.wait(5)
        return observation(raw)
    monkeypatch.setattr(owner, 'invoke', paused)
    task = asyncio.create_task(act(app, 'reconcile', row, 'stopped-inspection'))
    assert await asyncio.to_thread(entered.wait, 5)
    app.state['sessions'][0]['interruptionRevision'] = 88
    release.set()
    result = await task
    assert result['request']['state'] == 'failed'
    assert result['resource']['status'] == 'running'
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


@pytest.mark.parametrize('persistent', [False, True])
async def test_committed_recovery_survives_projection_failure_then_read_repairs(app, backend, monkeypatch, persistent):
    row, unknown = await uncertain(app, monkeypatch)
    calls = observer(monkeypatch)
    save, failures = owner.save, 0
    def broken_projection(home, resource, **changes):
        nonlocal failures
        if changes.get('lastRecovery', {}).get('state') == 'completed' and (persistent or failures == 0):
            failures += 1
            raise OSError('projection unavailable after durable inspection')
        return save(home, resource, **changes)
    monkeypatch.setattr(owner, 'save', broken_projection)
    result = await act(app, 'reconcile', row, 'durable-inspection')
    assert result['request']['state'] == 'completed'
    assert result['resource']['lastOperation'] == unknown and calls == ['reconcile']
    assert failures == (2 if persistent else 1)
    if persistent:
        pending = (await app.dispatch('workspace.environments.read', scope(app)))['result']['items'][0]
        assert pending['observation']['status'] == 'unverified' and pending['lastOperation'] == unknown
        assert calls == ['reconcile']
    monkeypatch.setattr(owner, 'save', save)
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert page['items'][0]['status'] == 'removed' and page['items'][0]['lastOperation'] == unknown
    assert page['items'][0]['lastRecovery'] == result['request']
    assert calls == ['reconcile']


async def test_snapshot_precedes_effect_and_missing_prepare_identity_never_launches(app, backend, monkeypatch):
    original = owner.invoke
    def inspect(config, home, raw, action, args):
        assert raw['backendConfigRetained']
        assert owner.resolve_configuration(app, raw, config) == config
        result = original(config, home, raw, action, args)
        if action == 'prepare':
            result.pop('backendIdentity')
        return result
    monkeypatch.setattr(owner, 'invoke', inspect)
    result = await start(app)
    assert result['request']['state'] == 'failed'
    assert result['resource']['retention']['mode'] == 'files'
    assert [action for action, _ in backend] == ['prepare']
    raw = owner.get(app.data_dir, result['resource']['sessionId'], result['resource']['id'])
    assert not raw.get('backendAdmitted')


async def test_changed_backend_cleanup_uses_retained_config_but_new_execution_does_not(app, backend, monkeypatch):
    row = (await start(app))['resource']
    old = owner.configuration(app)[0]
    new = {**old, 'dockerHost': 'unix:///new/docker.sock'}
    monkeypatch.setattr(owner, 'configuration', lambda app: (new, 'ready', 'new backend'))
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda app, value: (old, 'ready', 'old qualified'))
    seen, invoke = [], owner.invoke
    def exact(config, home, resource, action, args):
        seen.append((action, config))
        return invoke(config, home, resource, action, args)
    monkeypatch.setattr(owner, 'invoke', exact)
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    row = page['items'][0]
    assert row['recovery']['available'] is True
    rejected = await act(app, 'execute', row, 'wrong-backend-command', command='do-not-run')
    assert rejected['request']['state'] == 'failed'
    assert seen == [('read', old)]
    stopped = await act(app, 'stop', row, 'stop-old')
    removed = await act(app, 'remove', stopped['resource'], 'remove-old')
    assert removed['resource']['status'] == 'removed'
    assert seen == [('read', old), ('stop', old), ('remove', old)]


@pytest.mark.parametrize('verified', [False, True])
async def test_only_verified_removal_publishes_retained_file_accounting(app, backend, monkeypatch, verified):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'before-retention'))['resource']
    invoke = owner.invoke
    accounting = {'archiveBytes': 24, 'sourceBytes': 100, 'totalBytes': 124,
                  'format': 'files-tar', 'policy': 'files', 'verified': verified}
    def export(config, home, resource, action, args):
        return {**invoke(config, home, resource, action, args), 'retention': accounting}
    monkeypatch.setattr(owner, 'invoke', export)
    result = await act(app, 'remove', row, 'retained-removal')
    assert (result['resource']['retention'].get('accounting') == accounting) is verified
    if not verified:
        assert 'accounting' not in result['resource']['retention']


async def test_reconcile_agent_requires_current_owner_without_extra_approval(app, backend, monkeypatch):
    row = (await start(app))['resource']
    source, peer = app.state['sessions']
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'], 'requestId': 'agent-inspect'}
    calls = observer(monkeypatch, 'running')
    with pytest.raises(AppError, match='calling conversation'):
        await agent(app, source, 'workspace.environments.reconcile', {**args, 'sessionId': peer['id']}, 'peer-inspect')
    result = await agent(app, source, 'workspace.environments.reconcile', args, 'no-extra-grant')
    assert result['result']['request']['state'] == 'completed' and calls == ['reconcile']
    assert not source['approvals'] and not app.configuration_authority.rows(source['id'])
    source['collaborationGeneration']['terminal'] = True
    with pytest.raises(AppError, match='current transport-bound root generation'):
        await agent(app, source, 'workspace.environments.reconcile', {**args, 'requestId': 'retired'}, 'retired')
    assert calls == ['reconcile']


async def test_queue_cleanup_survives_backend_change_without_replaying_check(app, queue, backend, monkeypatch):
    old, invoke = owner.configuration(app)[0], owner.invoke
    new = {**old, 'dockerHost': 'unix:///next/backend.sock'}
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda *args: (old, 'ready', 'old qualified'))
    seen = []
    def change_after_check(config, home, row, action, args):
        seen.append((action, config))
        result = invoke(config, home, row, action, args)
        if action == 'execute':
            monkeypatch.setattr(owner, 'configuration', lambda app: (new, 'ready', 'new backend'))
        return result
    monkeypatch.setattr(owner, 'invoke', change_after_check)
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert [action for action, _ in seen] == ['prepare', 'start', 'execute', 'stop', 'remove']
    assert all(config == (owner.capacity_settings.apply(app, old) if action == 'start' else old)
               for action, config in seen)


async def test_queue_restart_uses_retained_backend_and_preserves_saved_check(app, queue, backend, monkeypatch):
    old = owner.configuration(app)[0]
    cleanup = queue.cleanup
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(queue, 'cleanup', interrupted)
    await submit(app)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values(), return_exceptions=True)
    receipt = record(queue, app)
    assert receipt['job']['check']['state'] == 'completed'
    monkeypatch.setattr(queue, 'cleanup', cleanup)
    monkeypatch.setattr(owner, 'configuration', lambda app: (None, 'disabled', 'new config disabled'))
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda *args: (old, 'ready', 'old qualified'))
    await queue.recover(receipt['requestId'])
    assert record(queue, app)['job']['status'] == 'passed'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'read', 'stop', 'remove']


@pytest.mark.parametrize('pinned', [False, True])
async def test_older_incus_explicit_config_pin_establishes_identity_without_guessing(app, backend, monkeypatch, pinned):
    public = (await start(app))['resource']
    raw = owner.get(app.data_dir, public['sessionId'], public['id'])
    incus = {'project': 'original-project', **({'daemonFingerprint': 'a' * 64} if pinned else {})}
    old = {'backend': 'incus', 'incus': incus, 'python': '/old/python'}
    raw = owner.save(app.data_dir, raw, backendIdentity=None, backendConfigRetained=False, backendKey=owner.config_key(old))
    monkeypatch.setattr(owner, 'configuration', lambda app: (old, 'ready', 'exact old config'))
    def inspect(config, home, row, action, args):
        assert action == 'reconcile'
        if pinned:
            assert row['backendIdentity'] == {'backend': 'incus', 'daemon': 'a' * 64, 'project': 'original-project'}
            return observation(row)
        assert not row.get('backendIdentity')
        return observation(row, backendIdentity={'backend': 'incus', 'daemon': 'a' * 64, 'project': 'original-project'})
    monkeypatch.setattr(owner, 'invoke', inspect)
    result = await act(app, 'reconcile', owner.public(app.data_dir, raw), 'legacy-pinned-inspection')
    assert result['request']['state'] == ('completed' if pinned else 'failed')
    assert (result['resource']['status'] == 'removed') is pinned


async def test_queued_roots_survive_real_cold_restart_and_cancel_stays_cancelled(app, queue, backend, monkeypatch):
    from amplifier_web.service import AppService
    from test_configuration_authority import Runtime
    roots = [*app.state['sessions'], app._new_session({'title': 'Third root'})]
    app.state['sessions'] = roots
    app._publish()
    for source in roots:
        workspace = next(w for w in app.state['workspaces'] if w['path'] == source['workspace'])
        await app.dispatch('workspace.tests.submit', {'workspaceId': workspace['id'], 'sessionId': source['id'],
            'requestId': 'restart-pending', 'profileId': owner.PROFILE, 'command': 'true', 'notify': False})
        # Old releases saved missing fields before cold metadata materialized them.
        saved = queue.get(queue.identity(source['id'], 'restart-pending'))
        saved['job']['binding'] = [source['workspace'], None, None, None]
        queue.save(saved)
    last = roots[-1]
    await app.dispatch('workspace.tests.cancel', {'workspaceId': workspace['id'], 'sessionId': last['id'], 'requestId': 'restart-pending'})
    home, default = app.data_dir, app.default_workspace
    await app.close()
    restored = AppService(home, Runtime(), workspace=default)
    monkeypatch.setattr(restored.test_queue, 'start', lambda: None)
    try:
        from amplifier_web.history_directory import summary
        # Native/cold directory owners use this production metadata projection;
        # selected app-owned records can still be resident after this restart.
        for source in roots:
            cold = restored._session(source['id'], hydrate=False)
            cold.update(summary(cold))
        cold = restored._session(roots[0]['id'], hydrate=False)
        assert cold['workingDirectory'] == roots[0]['workspace'] and cold['executionRevision'] == 0
        await drive(restored.test_queue)
        await drive(restored.test_queue)  # The controlled backend admits one per observation.
        statuses = [restored.test_queue.get(restored.test_queue.identity(row['id'], 'restart-pending'))['job']['status'] for row in roots]
        assert statuses == ['passed', 'passed', 'cancelled']
        assert [action for action, _ in backend].count('execute') == 2
    finally:
        await restored.close()


@pytest.mark.parametrize('field,value', [('workingDirectory', '/different'), ('workspace', '/other'), ('executionRevision', 1), ('interruptionRevision', 1)])
async def test_normalized_defaults_never_hide_real_move_or_stop(app, queue, backend, field, value):
    await submit(app)
    receipt = record(queue, app)
    source = app.state['sessions'][0]
    receipt['job']['binding'] = [source['workspace'], None, None, None]
    queue.save(receipt)
    source[field] = value
    assert queue.continuation_guard(receipt)
    await drive(queue)
    assert queue.get(receipt['requestId'])['job']['status'] == 'cancelled' and not backend


async def test_remove_full_choice_is_exact_and_never_substitutes_original_backend(app, backend, monkeypatch):
    config = {'python': '/managed/python', 'backend': 'incus',
              'incus': {'project': 'original', 'daemonFingerprint': 'a' * 64}}
    monkeypatch.setattr(owner, 'configuration', lambda app: (config, 'ready', 'qualified Incus fixture'))
    invoke = owner.invoke
    def incus(configured, home, row, action, args):
        assert configured == config
        result = invoke(configured, home, row, action, args)
        if action == 'prepare':
            result['backendIdentity'] = {'backend': 'incus', 'daemon': 'a' * 64, 'project': 'original'}
        return result
    monkeypatch.setattr(owner, 'invoke', incus)
    row = (await start(app))['resource']
    assert row['retention']['fullAvailable'] and row['retention']['mode'] == 'files'
    row = (await act(app, 'stop', row, 'stop-before-full'))['resource']
    removed = await act(app, 'remove', row, 'full-removal', retentionMode='full')
    assert removed['request']['state'] == 'completed'
    again = await act(app, 'remove', row, 'full-removal', retentionMode='full')
    assert again['request'] == removed['request']
    with pytest.raises(AppError, match='different operation arguments'):
        await act(app, 'remove', row, 'full-removal', retentionMode='files')
    removals = [args for action, args in backend if action == 'remove']
    assert len(removals) == 1 and removals[0]['retentionMode'] == 'full'
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert raw['backendKey'] == owner.config_key(config)


async def test_docker_full_override_is_rejected_before_receipt_or_backend_effect(app, backend):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop-before-unsupported-full'))['resource']
    assert row['retention']['fullAvailable'] is False
    with pytest.raises(AppError, match='only.*Incus'):
        await act(app, 'remove', row, 'unsupported-full', retentionMode='full')
    with pytest.raises(ValueError):
        app.operations.requests.read(row['sessionId'], 'unsupported-full')
    assert [action for action, _ in backend] == ['prepare', 'start', 'stop']


@pytest.mark.parametrize('action,expected', [('remove', 'full'), ('read', 'files'), ('start', 'files')])
def test_real_helper_receives_identity_and_retention_override_only_for_remove(tmp_path, monkeypatch, action, expected):
    import sys
    from test_test_environment_worker import allocated
    home, row = allocated(tmp_path)
    row['backendIdentity'] = {'backend': 'incus', 'daemon': 'original-daemon', 'project': 'owned'}
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'incus_environment_worker.py').write_text(
        "import json,sys\np=json.load(sys.stdin)\nprint(json.dumps({'ok':True,'mode':p['retentionMode'],'identity':p['expectedBackendIdentity'],'owner':p['rootOwner'],'operationId':p['operationId']}))\n")
    result = owner.invoke({'python': sys.executable, 'backend': 'incus', 'incus': {'project': 'owned'}},
                          home, row, action, {'requestId': 'exact-request', 'retentionMode': 'full'})
    assert result == {'ok': True, 'mode': expected, 'identity': row['backendIdentity'],
                      'owner': row['sessionId'], 'operationId': row['launchOperationId']}


@pytest.mark.parametrize('evidence', ['missing', 'forged', 'stale'])
async def test_legacy_remove_host_journal_preserves_unknown_with_untrusted_files(app, backend, library, monkeypatch, evidence):
    current=(await app.dispatch('testing.execution.read',{}))['result']
    await app.dispatch('testing.execution.save',{'expectedRevision':current['revision'],'queue':{},'profiles':{},'concurrency':{'maxEnvironmentsPerChat':1}})
    """Host preservation only, NOT a phase-proof consumer/validator test."""
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'legacy-stop'))['resource']
    lib, effects = library
    folder = Path(row['retention']['path'])
    (folder / 'source.tar').write_bytes(b'synthetic snapshot')
    original = owner.invoke
    destroyed = []
    def lost_destroy(identity, **kwargs):
        destroyed.append(identity)  # External effect may have happened.
        raise Failure('lost acknowledgement')
    lib.destroy = lost_destroy
    monkeypatch.setattr(worker, 'docker_json', lambda *args: {'ID':'owned-daemon'})
    def invoke(config, home, raw, action, args):
        if action == 'remove':
            value = worker.execute_call({
                'action':'remove', 'root':str(folder), 'rootOwner':raw['sessionId'],
                'operationId':raw['launchOperationId'], 'universeId':raw['universeId'],
                'expectedBackendIdentity':raw['backendIdentity'],
                'dockerHost':config['dockerHost'], 'requestId':args['requestId'],
                'resourceId':raw['id'], 'resourceRevision':raw['revision'],
                'admissionRevision':args['expectedRevision'],
                'backendKey':raw['backendKey'], 'snapshot':raw['snapshot']})
            # Model the older helper's published envelope, not a new phase proof.
            return {key:item for key, item in value.items() if key != 'removal'}
        if action == 'reconcile':
            return observation(raw, 'stopped')  # Presence is not effect history.
        return original(config, home, raw, action, args)
    monkeypatch.setattr(owner, 'invoke', invoke)
    failed = await act(app, 'remove', row, 'legacy-remove')
    unknown = copy.deepcopy(failed['request'])
    assert unknown['state'] == 'outcome_unknown' and destroyed == ['owned-universe']
    directory, = folder.glob('retained-*')
    if evidence != 'missing':
        (directory / 'untrusted-phase.json').write_text(json.dumps({
            'phase':'manifest-complete', 'destroyEffect':'not_dispatched', 'verified':True,
            'requestId':'legacy-remove' if evidence == 'forged' else 'older-request',
            'resourceRevision':row['revision'] if evidence == 'forged' else 1}))
    before = {p.name:(p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    assert len(list(directory.glob('*.tar'))) == 2
    result = await act(app, 'reconcile', failed['resource'], 'legacy-observe')
    assert result['request']['state'] == 'completed' and result['resource']['status'] == 'stopped'
    assert result['resource']['lastOperation'] == unknown
    assert 'accounting' not in result['resource']['retention']
    assert app.operations.requests.read(row['sessionId'], 'legacy-remove') == unknown
    assert (await act(app, 'remove', row, 'legacy-remove'))['request'] == unknown
    assert destroyed == ['owned-universe'] and sum(c[0] == 'export' for c in effects) == 2
    assert before == {p.name:(p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
    with pytest.raises(AppError, match='already owns its allowed environments'):
        await start(app, 'not-a-new-removal-attempt')


@pytest.mark.parametrize('change', ['root', 'launch', 'physical', 'backend', 'revision'])
async def test_legacy_remove_reconciliation_cannot_change_bound_owner(app, backend, monkeypatch, change):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'bound-stop'))['resource']
    def lost(*args):
        raise TimeoutError('synthetic original removal unknown')
    monkeypatch.setattr(owner, 'invoke', lost)
    failed = await act(app, 'remove', row, 'bound-remove')
    unknown = copy.deepcopy(failed['request'])
    changes = {
        'root': {'rootOwner':'other-root'}, 'launch': {'operationId':'other-launch'},
        'physical': {'universeId':'other-guest'},
        'backend': {'backendIdentity':{'backend':'docker', 'daemon':'other-daemon'}},
        'revision': {},
    }[change]
    calls = observer(monkeypatch, 'stopped', **changes)
    if change == 'revision':
        with pytest.raises(AppError, match='changed'):
            await act(app, 'reconcile', row, 'stale-remove-observation')
        assert calls == []
    else:
        result = await act(app, 'reconcile', failed['resource'], 'wrong-remove-observation')
        assert result['request']['state'] == 'failed' and calls == ['reconcile']
    assert app.operations.requests.read(row['sessionId'], 'bound-remove') == unknown
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


async def test_duplicate_concurrent_remove_and_reconcile_do_not_repeat_destroy(app, backend, library, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'concurrent-stop'))['resource']
    entered, release = threading.Event(), threading.Event()
    lib, effects = library
    folder = Path(row['retention']['path'])
    (folder / 'source.tar').write_bytes(b'synthetic snapshot')
    calls, destroyed = [], []
    def destroy(identity, **kwargs):
        destroyed.append(identity)
        entered.set()
        assert release.wait(5)
        raise Failure('synthetic lost destroy acknowledgement')
    lib.destroy = destroy
    monkeypatch.setattr(worker, 'docker_json', lambda *args: {'ID':'owned-daemon'})
    def invoke(config, home, raw, action, args):
        calls.append((action, args['requestId']))
        assert action == 'remove'
        return worker.execute_call({
            'action':'remove', 'root':str(folder), 'rootOwner':raw['sessionId'],
            'operationId':raw['launchOperationId'], 'universeId':raw['universeId'],
            'expectedBackendIdentity':raw['backendIdentity'], 'dockerHost':config['dockerHost'],
            'requestId':args['requestId'], 'resourceId':raw['id'],
            'resourceRevision':raw['revision'], 'admissionRevision':args['expectedRevision'],
            'backendKey':raw['backendKey'], 'snapshot':raw['snapshot']})
    monkeypatch.setattr(owner, 'invoke', invoke)
    task = asyncio.create_task(act(app, 'remove', row, 'once-remove'))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        duplicate = await act(app, 'remove', row, 'once-remove')
        assert duplicate['request']['state'] == 'admitting'
        current = owner.get(app.data_dir, row['sessionId'], row['id'])
        competing = await act(app, 'reconcile', owner.public(app.data_dir, current), 'competing-observation')
        assert competing['request']['state'] == 'failed'
        assert owner.get(app.data_dir, row['sessionId'], row['id']) == current
        competing_remove = await act(app, 'remove', owner.public(app.data_dir, current), 'competing-remove')
        assert competing_remove['request']['state'] == 'failed'
        assert owner.get(app.data_dir, row['sessionId'], row['id']) == current
    finally:
        release.set()
        settled = await task
    unknown = settled['request']
    assert unknown['state'] == 'outcome_unknown'
    assert (await act(app, 'remove', row, 'once-remove'))['request'] == unknown
    assert calls == [('remove', 'once-remove')]
    assert destroyed == ['owned-universe'] and sum(c[0] == 'export' for c in effects) == 2
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


async def test_new_remove_identity_cannot_bypass_a_legacy_unknown_removal(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'held-stop'))['resource']
    def lost(*args):
        raise TimeoutError('synthetic removal may have run')
    monkeypatch.setattr(owner, 'invoke', lost)
    failed = await act(app, 'remove', row, 'held-original-remove')
    unknown = copy.deepcopy(failed['request'])
    observed = observer(monkeypatch, 'stopped')
    reconciled = await act(app, 'reconcile', failed['resource'], 'held-inspection')
    assert reconciled['resource']['status'] == 'stopped' and observed == ['reconcile']
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    helpers = []
    def no_effect(*args):
        helpers.append(args)
        return {'ok':False, 'status':'unknown'}
    monkeypatch.setattr(owner, 'invoke', no_effect)
    with pytest.raises(AppError, match='original removal'):
        await act(app, 'remove', reconciled['resource'], 'not-a-recovery-token')
    assert helpers == []
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert app.operations.requests.read(row['sessionId'], 'held-original-remove') == unknown
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(row['sessionId'], 'not-a-recovery-token')


@pytest.mark.parametrize('later_receipts', [0, 105])
async def test_original_unknown_survives_later_stop_and_bounded_history_page(app, backend, monkeypatch, later_receipts):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'original-stop'))['resource']
    original_invoke = owner.invoke
    def lost(*args):
        raise TimeoutError('synthetic uncertain removal')
    monkeypatch.setattr(owner, 'invoke', lost)
    failed = await act(app, 'remove', row, 'original-unknown-remove')
    unknown = copy.deepcopy(failed['request'])
    observer(monkeypatch, 'stopped')
    observed = await act(app, 'reconcile', failed['resource'], 'original-observe')
    monkeypatch.setattr(owner, 'invoke', original_invoke)
    later = await act(app, 'stop', observed['resource'], 'later-stop')
    assert later['resource']['lastOperation']['action'] == 'workspace.environments.stop'
    for index in range(later_receipts):
        request, _ = app.operations.requests.begin(row['sessionId'], 'fixture.observation',
            {'requestId':f'later-{index}'}, {})
        app.operations.requests.finish(request, 'completed')
    if later_receipts:
        assert all(r['requestId'] != unknown['requestId'] for r in app.operations.requests.list(row['sessionId']))
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    helpers = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: helpers.append(args))
    with pytest.raises(AppError, match='original removal'):
        await act(app, 'remove', later['resource'], 'new-id-cannot-escape')
    assert not helpers and owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert app.operations.requests.read(row['sessionId'], unknown['requestId']) == unknown
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(row['sessionId'], 'new-id-cannot-escape')
    assert (await act(app, 'remove', row, 'original-unknown-remove'))['request'] == unknown


async def test_late_stop_before_removal_effect_keeps_resource_and_calls_unchanged(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'before-late-stop'))['resource']
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    entered, release = threading.Event(), threading.Event()
    original = recovery.bind_configuration
    def pause(*args):
        result = original(*args)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(recovery, 'bind_configuration', pause)
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: calls.append(args))
    task = asyncio.create_task(act(app, 'remove', row, 'late-stopped-remove'))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        source = app.state['sessions'][0]
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    finally:
        release.set()
        result = await task
    assert result['request']['state'] == 'failed' and calls == []
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before


async def test_direct_effect_route_cannot_bypass_original_unknown(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'direct-stop'))['resource']
    config = owner.configuration(app)[0]
    def lost(*args):
        raise TimeoutError('synthetic uncertain removal')
    monkeypatch.setattr(owner, 'invoke', lost)
    failed = await act(app, 'remove', row, 'direct-original')
    observer(monkeypatch, 'stopped')
    observed = await act(app, 'reconcile', failed['resource'], 'direct-observe')
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    args = {**scope(app), 'id':row['id'], 'expectedRevision':raw['revision'], 'requestId':'direct-new'}
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove', args, {})
    request = app.operations.requests.finish(request, 'admitting', resourceId=row['id'])
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: calls.append(args))
    result = await asyncio.to_thread(owner.effect, app, raw, 'remove', args, config, request, lambda: None)
    assert result['request']['state'] == 'failed' and not calls
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == raw
    assert app.operations.requests.read(row['sessionId'], 'direct-original') == failed['request']


@pytest.mark.parametrize('boundary', ['guest-record', 'fallback-journal'])
async def test_confirmed_destroy_survives_host_guest_removed_persistence_failure(app, backend, monkeypatch, boundary):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'metadata-stop'))['resource']
    original_invoke, original_save = owner.invoke, owner.save
    original_finish = app.operations.requests.finish
    calls, injected = [], []
    def invoke(config, home, raw, action, args):
        calls.append(action)
        result = original_invoke(config, home, raw, action, args)
        if action == 'remove':
            result['removal'] = {'version':1, 'phase':'settle', 'code':'complete', 'destroyEffect':'confirmed'}
            if boundary == 'fallback-journal':
                return {'ok':False, 'status':'unknown', 'code':'receipt_unavailable',
                        'destroyEffect':'confirmed'}
        return result
    def save(home, raw, **changes):
        if changes.get('guestRemoved'):
            injected.append(True)
            raise OSError('private synthetic metadata failure')
        return original_save(home, raw, **changes)
    def finish(record, state, **fields):
        if boundary == 'fallback-journal' and fields.get('result', {}).get('code') == 'receipt_unavailable':
            injected.append(True)
            raise OSError('private synthetic journal failure')
        return original_finish(record, state, **fields)
    monkeypatch.setattr(owner, 'invoke', invoke)
    monkeypatch.setattr(owner, 'save', save)
    monkeypatch.setattr(app.operations.requests, 'finish', finish)
    result = await act(app, 'remove', row, 'metadata-remove')
    assert injected == [True] and calls == ['remove']
    assert result['request']['state'] == 'outcome_unknown'
    assert result['request']['result']['removal'] == {
        'version':1, 'phase':'host-persistence', 'code':'metadata_unconfirmed', 'destroyEffect':'confirmed'}
    assert 'private' not in json.dumps(result['request'])
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
    duplicate = await act(app, 'remove', row, 'metadata-remove')
    assert duplicate['request'] == result['request'] and calls == ['remove']


async def queued_removal_fixture(app, queue):
    """Use the real queue effect entry; only its already-owned job is seeded."""
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'queue-binding-stop'))['resource']
    await submit(app, 'queue-binding-check')
    receipt = record(queue, app, 'queue-binding-check')
    receipt['job']['resourceId'] = row['id']
    queue.save(receipt)
    return row, receipt, owner.configuration(app)[0]


@pytest.mark.parametrize('boundary', ['before-effect', 'binding-save', 'after-helper'])
async def test_queue_origin_interruption_restart_holds_original_removal(app, queue, backend, monkeypatch, boundary):
    from amplifier_web.operation_requests import OperationRequests
    row, receipt, config = await queued_removal_fixture(app, queue)
    request_id = receipt['requestId'] + ':remove'
    requests = app.operations.requests
    begin, finish, thread = requests.begin, requests.finish, app.worktrees.finish_thread
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    begun, faults, helper_calls = [], [], []

    def observe_begin(sid, action, args, actor, **bindings):
        result, fresh = begin(sid, action, args, actor, **bindings)
        if action == 'workspace.environments.remove':
            assert fresh and result['resourceId'] == row['id']
            assert requests.read(sid, args['requestId'])['resourceId'] == row['id']
            begun.append(copy.deepcopy(result))  # Resource identity is atomic with admission.
        return result, fresh

    def interrupted_finish(saved, state, **fields):
        if saved['requestId'] == request_id and (
                boundary == 'binding-save' and state == 'admitting' and 'resourceId' in fields
                or boundary == 'after-helper' and state != 'admitting'):
            faults.append(state)
            raise OSError('synthetic queue interruption before receipt publication')
        return finish(saved, state, **fields)

    async def interrupted_thread(function, *args, **kwargs):
        if boundary == 'before-effect' and function is owner.effect:
            faults.append('before-effect')
            raise OSError('synthetic queue interruption before common effect')
        return await thread(function, *args, **kwargs)

    def invoke(configured, home, raw, action, args):
        assert action == 'remove'
        saved = requests.read(row['sessionId'], request_id)
        assert saved['state'] == 'admitting' and saved['resourceId'] == row['id']
        helper_calls.append(copy.deepcopy(saved))
        return {'ok':False, 'status':'unknown'}  # An effect, not a completed removal.

    with monkeypatch.context() as fault:
        fault.setattr(requests, 'begin', observe_begin)
        fault.setattr(requests, 'finish', interrupted_finish)
        fault.setattr(app.worktrees, 'finish_thread', interrupted_thread)
        fault.setattr(owner, 'invoke', invoke)
        with pytest.raises(OSError, match='synthetic queue interruption'):
            await queue.effect(receipt, config, 'remove', cleanup=True)
    assert len(begun) == 1 and faults
    assert len(helper_calls) == (1 if boundary == 'after-helper' else 0)
    saved = requests.read(row['sessionId'], request_id)
    assert saved['state'] == 'admitting' and saved['requestId'] == begun[0]['requestId']
    assert saved['createdAt'] == begun[0]['createdAt']
    assert saved['resourceId'] == row['id']
    if boundary != 'after-helper':
        assert owner.get(app.data_dir, row['sessionId'], row['id']) == before

    # Execute the actual journal startup sweep, not a fixture-written UNKNOWN.
    reopened = OperationRequests(requests.journal)
    unknown = reopened.read(row['sessionId'], request_id)
    assert unknown['state'] == 'outcome_unknown'
    assert {k:v for k,v in unknown.items() if k not in {'state','message','updatedAt'}} == {
        k:v for k,v in saved.items() if k not in {'state','message','updatedAt'}}
    observed = observer(monkeypatch, 'stopped')
    current = owner.get(app.data_dir, row['sessionId'], row['id'])
    reconciled = await act(app, 'reconcile', owner.public(app.data_dir, current), 'queue-restart-observe')
    assert observed == ['reconcile'] and reconciled['resource']['status'] == 'stopped'
    assert requests.read(row['sessionId'], request_id) == unknown
    before_new = owner.get(app.data_dir, row['sessionId'], row['id'])
    new_calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: new_calls.append(args))
    with pytest.raises(AppError, match='original removal'):
        await act(app, 'remove', reconciled['resource'], 'new-id-after-queue-restart')
    assert new_calls == []
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before_new
    assert requests.read(row['sessionId'], request_id) == unknown
    with pytest.raises(ValueError, match='not found'):
        requests.read(row['sessionId'], 'new-id-after-queue-restart')
    assert await queue.effect(receipt, config, 'remove', cleanup=True) == unknown
    assert not new_calls and requests.read(row['sessionId'], request_id) == unknown


async def test_queue_removal_persists_binding_before_helper_and_keeps_completion(app, queue, backend, monkeypatch):
    row, receipt, config = await queued_removal_fixture(app, queue)
    requests = app.operations.requests
    begin, invoke = requests.begin, owner.invoke
    begun, calls = [], []
    request_id = receipt['requestId'] + ':remove'
    def observe_begin(sid, action, args, actor, **bindings):
        result, fresh = begin(sid, action, args, actor, **bindings)
        if action == 'workspace.environments.remove':
            assert fresh and result['resourceId'] == row['id']
            begun.append(copy.deepcopy(result))
        return result, fresh
    def inspect(configured, home, raw, action, args):
        saved = requests.read(raw['sessionId'], request_id)
        assert saved['resourceId'] == row['id'] and saved['state'] == 'admitting'
        assert saved['requestId'] == begun[0]['requestId'] and saved['createdAt'] == begun[0]['createdAt']
        calls.append((action, copy.deepcopy(saved)))
        return invoke(configured, home, raw, action, args)
    monkeypatch.setattr(requests, 'begin', observe_begin)
    monkeypatch.setattr(owner, 'invoke', inspect)
    result = await queue.effect(receipt, config, 'remove', cleanup=True)
    assert result['state'] == 'completed' and result['resourceId'] == row['id']
    assert len(begun) == len(calls) == 1 and calls[0][0] == 'remove'
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] == 'retired'
    assert await queue.effect(receipt, config, 'remove', cleanup=True) == result
    assert len(calls) == 1


@pytest.mark.parametrize('scope_kind,state,binding,blocked', [
    ('same', 'outcome_unknown', None, True),
    ('same', 'outcome_unknown', '', True),
    ('same', 'outcome_unknown', 7, True),
    ('other-root', 'outcome_unknown', None, False),
    ('same', 'outcome_unknown', 'other-resource', False),
    ('same', 'completed', None, False),
    ('same', 'failed', None, False),
])
async def test_unbound_removal_hold_is_same_root_only_and_never_infers_owner(app, backend, scope_kind, state, binding, blocked):
    row = (await start(app))['resource']
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    sid = row['sessionId'] if scope_kind == 'same' else app.state['sessions'][1]['id']
    request, _ = app.operations.requests.begin(sid, 'workspace.environments.remove',
        {'requestId':'historical-unbound'}, {})
    request = app.operations.requests.finish(request, state, **({'resourceId':binding} if binding is not None else {}))
    if blocked:
        with pytest.raises(ValueError, match='original removal'):
            owner.require_resolved_removal(app, raw)
    else:
        owner.require_resolved_removal(app, raw)
    assert app.operations.requests.read(sid, request['requestId']) == request
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == raw


@pytest.mark.parametrize('change', ['unknown', 'completed', 'different-resource'])
async def test_common_removal_never_rebinds_retained_request(app, backend, monkeypatch, change):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'no-rebind-stop'))['resource']
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    config = owner.configuration(app)[0]
    args = {'requestId':'no-rebind', 'expectedRevision':raw['revision']}
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove', args, {})
    if change == 'different-resource':
        request = app.operations.requests.finish(request, 'admitting', resourceId='different-resource')
    else:
        request = app.operations.requests.finish(request, 'outcome_unknown' if change == 'unknown' else 'completed')
    other, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove',
        {'requestId':'another-unbound-original'}, {})
    other = app.operations.requests.finish(other, 'outcome_unknown')
    helpers = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: helpers.append(args))
    with pytest.raises(ValueError, match='cannot be rebound or replayed'):
        await asyncio.to_thread(owner.effect, app, raw, 'remove', args, config, request, lambda: None)
    assert not helpers and owner.get(app.data_dir, row['sessionId'], row['id']) == raw
    assert app.operations.requests.read(row['sessionId'], request['requestId']) == request
    assert app.operations.requests.read(row['sessionId'], other['requestId']) == other


@pytest.mark.parametrize('boundary', ['stale-revision', 'revoked-admission', 'locked'])
async def test_early_common_refusal_never_rewrites_original_unknown(app, backend, monkeypatch, boundary):
    from contextlib import nullcontext
    from filelock import Timeout as LockTimeout
    from amplifier_web.operation_requests import OperationRequests
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'early-stop'))['resource']
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    args = {'requestId':'early-original', 'expectedRevision':raw['revision']}
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove', args, {})
    reopened = OperationRequests(app.operations.requests.journal)
    unknown = reopened.read(row['sessionId'], request['requestId'])
    assert unknown['state'] == 'outcome_unknown' and 'resourceId' not in unknown
    config = owner.configuration(app)[0]
    current = row
    if boundary == 'stale-revision':
        observer(monkeypatch, 'stopped')
        current = (await act(app, 'reconcile', row, 'early-observe'))['resource']
        assert current['revision'] != args['expectedRevision']
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    helpers = []
    monkeypatch.setattr(owner, 'invoke', lambda *values: helpers.append(values))
    def admit():
        if boundary == 'revoked-admission':
            raise ValueError('synthetic original authority revoked')
    lock = FileLock(str(app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')))
    error = LockTimeout if boundary == 'locked' else ValueError
    match = 'lock' if boundary == 'locked' else 'changed' if boundary == 'stale-revision' else 'authority revoked'
    with lock if boundary == 'locked' else nullcontext():
        with pytest.raises(error, match=match):
            await asyncio.to_thread(owner.effect, app, raw, 'remove', args, config, unknown, admit)
    assert helpers == []
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert reopened.read(row['sessionId'], request['requestId']) == unknown
    with pytest.raises(AppError, match='original removal'):
        await act(app, 'remove', current, 'early-new-request')
    assert helpers == []
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert reopened.read(row['sessionId'], request['requestId']) == unknown
    with pytest.raises(ValueError, match='not found'):
        reopened.read(row['sessionId'], 'early-new-request')


# Test-first public boundary. A missing action must fail visibly: do not register
# it in a fixture or substitute environments.read/reconcile, which can observe
# and repair resources rather than merely inspect the retained removal binding.
REMOVAL_READ = 'workspace.environments.removal.read'


def require_removal_read_contract():
    from amplifier_web.service import ACTION_DEFINITIONS
    assert REMOVAL_READ in ACTION_DEFINITIONS, 'Public passive removal inspection is not implemented'
    return ACTION_DEFINITIONS[REMOVAL_READ][1]


async def removal_read_fixture(app, monkeypatch):
    """Produce a legacy-shaped UNKNOWN through real public removal admission."""
    row = (await start(app, 'public-read-start'))['resource']
    row = (await act(app, 'stop', row, 'public-read-stop'))['resource']
    entered = []
    def incomplete(config, home, raw, action, args):
        entered.append(action)
        return {'ok': False, 'status': 'unknown',
                'message': 'SYNTHETIC_PRIVATE_BACKEND_DETAIL',
                'privateConfig': {'key': 'SYNTHETIC_UNRELATED_SECRET'}}
    with monkeypatch.context() as fault:
        fault.setattr(owner, 'invoke', incomplete)
        failed = await act(app, 'remove', row, 'public-read-original')
    assert entered == ['remove'] and failed['request']['state'] == 'outcome_unknown'
    return failed['resource'], failed['request']


def removal_read_args(app, row, original):
    return {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'],
            'originalRequestId': original['requestId']}


def removal_read_snapshot(app, row):
    folder = Path(row['retention']['path'])
    return (owner.get(app.data_dir, row['sessionId'], row['id']),
            app.operations.requests.list(row['sessionId']),
            {str(p.relative_to(folder)): (p.read_bytes(), p.stat().st_mtime_ns)
             for p in folder.rglob('*') if p.is_file()})


def forbid_removal_read_effects(app, monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('unexpected external/configuration/persistence call')
        raise AssertionError('Passive removal inspection must not probe, repair, persist or prepare')
    for name in ('invoke', 'configuration', 'resolve_configuration', 'save', 'read'):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(app.operations.requests, 'begin', forbidden)
    monkeypatch.setattr(app.operations.requests, 'finish', forbidden)
    monkeypatch.setattr(app.runtime, 'start', forbidden)
    monkeypatch.setattr(app.runtime, 'control', forbidden)
    return calls


def assert_unverified_removal_read(value, row, original, code='original_binding_unverified'):
    # No source paths, raw results, settings, archive names or exception strings.
    assert value == {
        'version': 1, 'sessionId': row['sessionId'], 'id': row['id'],
        'revision': row['revision'],
        'original': {'requestId': original['requestId'], 'state': original['state']},
        'status': 'held', 'code': code,
        'executor': 'unobserved', 'physical': 'not_observed',
        'continuationAvailable': False, 'riskDecisionAvailable': False,
        'verification': {'status': 'not_retained'},
    }
    assert len(json.dumps(value).encode()) < 4096
    assert 'SYNTHETIC_' not in json.dumps(value)


@pytest.mark.parametrize('legacy_creation_shape', [False, True])
async def test_ordinary_creation_runtime_binding_and_cold_removal_read(app, backend, monkeypatch, legacy_creation_shape):
    """Positive case never supplies placement proof; old producer shape fails."""
    from contextlib import closing
    from amplifier_web import state_records, session_projection
    peer = app.state['sessions'][0]
    if legacy_creation_shape:
        create = app._new_session
        def old_producer(args, **kwargs):
            row = create(args, **kwargs)
            for field in ('sessionKind', 'workingDirectory', 'executionRevision', 'interruptionRevision'):
                row.pop(field, None)
            return row
        monkeypatch.setattr(app, '_new_session', old_producer)
    receipt = await app.dispatch('session.create', {
        'title': 'Ordinary removal owner', 'workspace': peer['workspace'], 'select': False},
        command_id='ordinary-owner-create', include_state=False)
    source = app._session(receipt['result']['sessionId'])
    source['collaborationGeneration'] = {'id': 'ordinary-generation', 'inputIds': ['ordinary-input'], 'terminal': False}
    await app.on_runtime_event('runtime.status', {
        'sessionId': source['id'], 'status': 'working', 'runtimeSessionId': 'ordinary-runtime'})
    assert source['runtimeSessionId'] == 'ordinary-runtime'
    fields = ('sessionKind', 'workingDirectory', 'executionRevision', 'interruptionRevision')
    expected = {'sessionKind': 'root', 'workingDirectory': peer['workspace'],
                'executionRevision': 0, 'interruptionRevision': 0}
    produced = {key: source[key] for key in fields if key in source}
    row = (await start(app, 'ordinary-start'))['resource']
    row = (await act(app, 'stop', row, 'ordinary-stop'))['resource']
    with monkeypatch.context() as fault:
        fault.setattr(owner, 'invoke', lambda *args: {
            'ok': False, 'status': 'stopped', 'universeId': row['universeId'],
            'code': 'archive_verification_failed', 'phase': 'verifying', 'effectAdmitted': False})
        failure = await act(app, 'remove', row, 'ordinary-remove')
    row, original = failure['resource'], failure['request']
    assert original['state'] == 'failed'
    args = removal_read_args(app, row, original)
    assert owner.get(app.data_dir, source['id'], row['id'])['owner'] == {
        'sessionId': source['id'], 'principal': 'ordinary-runtime'}
    for residency in ('resident', 'cold'):
        if residency == 'cold':
            app._save()
            with closing(recovery.sqlite3.connect((app.data_dir / 'app.sqlite3').as_uri() + '?mode=ro',
                                                 uri=True, timeout=0)) as reopened:
                saved, _ = state_records.load_owner(reopened, source['id'])
            assert {key: saved[key] for key in fields if key in saved} == ({} if legacy_creation_shape else expected)
            app._state['sessions'].remove(source)
            app._state['selectedSessionId'] = peer['id']
            app.projections.invalidate()
        before = removal_read_snapshot(app, row)
        overlay = [item['id'] for item in app._state['sessions']]
        selected, changes = app._state['selectedSessionId'], app.db.total_changes
        def forbidden(*args, **kwargs):
            pytest.fail('Passive owner inspection hydrated, admitted or wrote')
        with monkeypatch.context() as guard:
            calls = forbid_removal_read_effects(app, guard)
            for method in ('_session', '_admit_session', '_save'):
                guard.setattr(app, method, forbidden)
            guard.setattr(app.history, 'refresh', forbidden)
            guard.setattr(app.cold_display, 'hydrate', forbidden)
            guard.setattr(session_projection, 'hydrate', forbidden)
            guard.setattr(state_records, 'load_owner', forbidden)
            if legacy_creation_shape:
                with pytest.raises(AppError, match='Owner placement metadata is unavailable') as caught:
                    await agent(app, source, REMOVAL_READ, args, 'ordinary-read-' + residency,
                                _runtimeSessionId='ordinary-runtime')
                assert caught.value.status == 403
            else:
                result = await agent(app, source, REMOVAL_READ, args, 'ordinary-read-' + residency,
                                     _runtimeSessionId='ordinary-runtime')
                assert_unverified_removal_read(result['result'], row, original)
        assert calls == [] and removal_read_snapshot(app, row) == before
        assert app.db.total_changes == changes
        assert [item['id'] for item in app._state['sessions']] == overlay
        assert app._state['selectedSessionId'] == selected
    # On the old producer the positive case fails at the actual owner guard,
    # not just at a fixture-shape assertion before the consumer is exercised.
    assert produced == ({} if legacy_creation_shape else expected)


@pytest.mark.parametrize('kind', ['workspace', 'managed', 'internal'])
async def test_new_owner_placement_uses_actual_creation_folder(app, kind):
    import uuid
    explicit = str(uuid.uuid4())
    args = {'title': 'Placement', 'id': explicit, 'select': False}
    if kind == 'managed':
        args['location'] = {'kind': 'managed'}
    else:
        args['workspace'] = str(app.default_workspace)
    if kind == 'internal':
        args['purpose'] = 'terminal-tool'
    result = await app.dispatch('session.create', args, command_id='placement-create', include_state=False)
    source = app._session(result['result']['sessionId'])
    assert source['id'] == explicit
    if kind == 'internal':
        assert source['sessionKind'] == 'internal'
        assert 'executionRevision' not in source and 'workingDirectory' not in source
    else:
        assert source['sessionKind'] == 'root'
        assert source['workingDirectory'] == source['workspace']
        assert Path(source['workingDirectory']).is_dir()
        assert source['executionRevision'] == source['interruptionRevision'] == 0
        if kind == 'managed':
            assert source['workspace'] != str(app.default_workspace)
            assert source['location'] == {'kind': 'managed'}


def test_import_view_constructor_does_not_invent_root_placement(app):
    source = app._new_session({'title': 'Historical import view', 'workspace': str(app.default_workspace)})
    assert not {'sessionKind', 'workingDirectory', 'executionRevision', 'interruptionRevision'} & source.keys()


async def test_commissioned_owner_placement_preserves_inherited_execution_folder(collaboration_app):
    app = collaboration_app
    source = app.state['sessions'][0]
    folder = Path(source['workspace']) / 'separate-execution'
    folder.mkdir()
    source.update(workingDirectory=str(folder), executionRevision=7, interruptionRevision=3)
    # This fixture represents fresh work at the current Stop boundary, not the
    # earlier peer-woken generation carried across it.
    source['collaborationGeneration'].update(id='placement-current', interruptionRevision=3)
    receipt = await agent(app, source, 'coordination.create',
        {'title': 'Placement child', 'text': 'Synthetic placement check'}, 'placement-child')
    await until(lambda: len(app.runtime.inputs) == 1)
    child = app._session(receipt['sessionId'])
    assert child['workspace'] == source['workspace']
    assert child['workingDirectory'] == str(folder) != child['workspace']
    assert child['sessionKind'] == 'root'
    assert child['executionRevision'] == child['interruptionRevision'] == 0
    assert child['selection'] == source['selection']


@pytest.mark.parametrize('caller', ['owner', 'child', 'foreign'])
async def test_incomplete_owner_reason_requires_authenticated_identity_before_resource_lookup(app, monkeypatch, caller):
    from amplifier_web import task_resources
    source, peer = app.state['sessions']
    await app.on_runtime_event('runtime.status', {
        'sessionId': source['id'], 'status': 'working', 'runtimeSessionId': 'metadata-runtime'})
    source.pop('executionRevision', None)
    before = dict(source)
    def forbidden(*args, **kwargs):
        pytest.fail('An incomplete owner must not inspect resource existence')
    monkeypatch.setattr(task_resources, 'get', forbidden)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Removal inspection requires the recorded root owner') as caught:
        await agent(app, source, REMOVAL_READ, {
            **scope(app), 'sessionId': peer['id'] if caller == 'foreign' else source['id'],
            'id': '00000000-0000-0000-0000-000000000001', 'expectedRevision': 1,
            'originalRequestId': 'does-not-exist'}, 'metadata-refusal',
            _runtimeSessionId='metadata-runtime' if caller != 'child' else 'unrelated-child')
    assert caught.value.status == 403
    assert ('Owner placement metadata is unavailable' in str(caught.value)) is (caller == 'owner')
    assert dict(source) == before and not calls


@pytest.mark.parametrize('cause', ['recorded', 'old', 'malformed', 'unsafe-name'])
async def test_retained_verification_projection_is_passive_not_cleanup_authority(app, backend, monkeypatch, cause):
    row = (await start(app, 'diagnostic-start'))['resource']
    row = (await act(app, 'stop', row, 'diagnostic-stop'))['resource']
    diagnostic = {'version': 1, 'reasonCode': 'unsupported_type',
                  'memberType': 'fifo', 'relativePath': 'artifacts/fixture-pipe'}
    if cause == 'malformed':
        diagnostic['reasonCode'] = 'private exception'
    if cause == 'unsafe-name':
        diagnostic['relativePath'] = '/host/private-secret'
    result = {'ok': False, 'status': 'stopped', 'universeId': row['universeId'],
              'code': 'archive_verification_failed', 'phase': 'verifying', 'effectAdmitted': False,
              'removal': {'version': 1, 'phase': 'verify-exports', 'code': 'phase_failed',
                          'destroyEffect': 'not_dispatched'},
              **({'verification': diagnostic} if cause != 'old' else {})}
    with monkeypatch.context() as fault:
        fault.setattr(owner, 'invoke', lambda *args: result)
        failed = await act(app, 'remove', row, 'diagnostic-remove')
    assert failed['request']['state'] == 'failed'
    row, original = failed['resource'], failed['request']
    args = removal_read_args(app, row, original)
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    source = app.state['sessions'][0]
    read = (await agent(app, source, REMOVAL_READ, args, 'diagnostic-read'))['result']
    expected = ({'status': 'not_retained'} if cause == 'old' else {'status': 'unverified'}
                if cause == 'malformed' else {'status': 'recorded', 'version': 1,
                    'reasonCode': 'unsupported_type', 'memberType': 'fifo',
                    **({'relativePath': 'artifacts/fixture-pipe'} if cause == 'recorded' else {})})
    assert read['verification'] == expected
    assert read['status'] == 'held' and read['executor'] == 'unobserved'
    assert read['physical'] == 'not_observed'
    assert read['continuationAvailable'] is read['riskDecisionAvailable'] is False
    assert 'private' not in json.dumps(read) and len(json.dumps(read).encode()) < 4096
    assert calls == [] and removal_read_snapshot(app, row) == before


async def test_verification_projection_does_not_accept_cleanup_as_original(app, backend, monkeypatch):
    row = (await start(app, 'original-start'))['resource']
    row = (await act(app, 'stop', row, 'original-stop'))['resource']
    with monkeypatch.context() as fault:
        fault.setattr(owner, 'invoke', lambda *args: {'ok': False, 'status': 'stopped',
            'universeId': row['universeId'], 'code': 'archive_verification_failed',
            'phase': 'verifying', 'effectAdmitted': False})
        removed = await act(app, 'remove', row, 'original-remove')
        cleanup = await act(app, 'finishCleanup', removed['resource'], 'original-cleanup',
                            previousRequestId='original-remove')
    source = app.state['sessions'][0]
    before = removal_read_snapshot(app, cleanup['resource'])
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='not bound to this resource'):
        await agent(app, source, REMOVAL_READ,
            removal_read_args(app, cleanup['resource'], cleanup['request']), 'wrong-original')
    assert calls == [] and removal_read_snapshot(app, cleanup['resource']) == before


@pytest.mark.parametrize('backend_kind', ['docker', 'incus'])
async def test_worker_validation_receipt_survives_host_save_and_passive_read(app, backend, library, monkeypatch, backend_kind):
    """Public native root -> real rejection -> closed/reopened host -> saved read."""
    import hashlib
    import tarfile
    from types import SimpleNamespace
    from amplifier_web import incus_environment_worker as incus_worker
    from amplifier_web import session_projection, state_records
    from amplifier_web.service import AppService
    from test_configuration_authority import Runtime

    peer = app.state['sessions'][0]
    created = await app.dispatch('session.create', {
        'title': 'Fresh diagnostic owner', 'workspace': peer['workspace'], 'select': False},
        command_id='receipt-owner-create', include_state=False)
    source = app._session(created['result']['sessionId'])
    source['collaborationGeneration'] = {'id': 'receipt-generation', 'inputIds': ['receipt-input'], 'terminal': False}
    await app.on_runtime_event('runtime.status', {
        'sessionId': source['id'], 'status': 'working', 'runtimeSessionId': 'receipt-native-runtime'})
    # These facts come only from the public producer/event, not fixture updates.
    placement = {'sessionKind': 'root', 'workingDirectory': peer['workspace'],
                 'executionRevision': 0, 'interruptionRevision': 0}
    assert {key: source[key] for key in placement} == placement
    assert source['runtimeSessionId'] == 'receipt-native-runtime'
    transport = copy.deepcopy(source)
    if backend_kind == 'incus':
        config = {'python': '/managed/python', 'backend': 'incus', 'incus': {
            'executable': '/qualified/fixture', 'incusExecutable': '/usr/bin/incus',
            'project': 'tests', 'configDir': '/fixture/incus', 'admissionDir': '/fixture/admission',
            'image': 'a' * 64}}
        monkeypatch.setattr(owner, 'configuration', lambda app: (config, 'ready', 'Controlled Incus fixture'))
        original_invoke = owner.invoke
        def prepare_incus(configured, home, raw, action, args):
            assert action in {'prepare', 'start', 'stop'}
            value = original_invoke(configured, home, raw, action, args)
            return {**value, 'universeId': incus_worker.identity({
                'rootOwner': raw['sessionId'], 'operationId': raw['launchOperationId']}),
                'backendIdentity': {'backend': 'incus', 'daemon': 'owned-daemon', 'project': 'tests'}}
        monkeypatch.setattr(owner, 'invoke', prepare_incus)
    row = (await start(app, 'receipt-start'))['resource']
    row = (await act(app, 'stop', row, 'receipt-stop'))['resource']
    lib, effects = library
    incus_calls = []
    folder = Path(row['retention']['path'])
    (folder / 'source.tar').write_bytes(b'synthetic snapshot')
    def append_fifo(path):
        # Inert tar metadata only; never create a physical FIFO in the guest.
        with tarfile.open(path, 'a') as archive:
            member = tarfile.TarInfo('artifacts/fixture-pipe')
            member.type = tarfile.FIFOTYPE
            archive.addfile(member)
    original_export = lib.export_files
    def export(identity, source, destination, **kwargs):
        receipt = original_export(identity, source, destination, **kwargs)
        if source == '/artifacts':
            append_fifo(destination)
            receipt.bytes = destination.stat().st_size
            receipt.sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
        return receipt
    lib.export_files = export
    monkeypatch.setattr(worker, 'docker_json', lambda *args: {'ID': 'owned-daemon'})
    if backend_kind == 'incus':
        monkeypatch.setattr(incus_worker, 'validate_configuration', lambda value: value)
        monkeypatch.setattr(incus_worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
        raw = owner.get(app.data_dir, row['sessionId'], row['id'])
        def cli(config, env, args, timeout=30):
            incus_calls.append(('cli', args))
            if args == ['capacity']:
                return {'scope': {'daemon': 'owned-daemon', 'project': 'tests'},
                        'storage': {'free_bytes': 20 * 1024**3}}
            assert args == ['launch-status', raw['launchOperationId']], 'No launch or destroy is allowed'
            return {'state': 'stopped', 'name': raw['universeId'],
                    'owner_root': source['id'], 'instance_status': 'Stopped'}
        def incus(config, env, args, timeout=30):
            incus_calls.append(('incus', args))
            if args == ['query', '/1.0']:
                value = {'environment': {'project': 'tests', 'certificate_fingerprint': 'owned-daemon'}}
            else:
                assert args == ['query', '/1.0/instances/' + raw['universeId']], 'No guest effects are allowed'
                value = {'status': 'Stopped', 'config': {'user.dtu.managed-by': 'amplifier-digital-twin',
                    'user.dtu.owner-root': source['id'], 'user.dtu.request-id': raw['launchOperationId']}}
            return {'exit_code': 0, 'stdout': json.dumps(value), 'stderr': '', 'truncated': False}
        def export_selected(config, env, identity, destination, **kwargs):
            from test_environment_retention import backup, ROOTS
            incus_calls.append(('export', identity))
            archives = worker.retention.filter_incus_stream(backup(ROOTS), destination, identity,
                                                            archive_only=kwargs.get('archive_only', False))
            append_fifo(destination / 'artifacts.tar')
            return archives
        monkeypatch.setattr(incus_worker, 'cli', cli)
        monkeypatch.setattr(incus_worker, 'incus', incus)
        monkeypatch.setattr(incus_worker, 'export_selected', export_selected)
    def invoke(config, home, raw, action, args):
        assert action == 'remove'
        payload = {
            'action': action, 'root': str(folder), 'rootOwner': raw['sessionId'],
            'operationId': raw['launchOperationId'], 'universeId': raw['universeId'],
            'expectedBackendIdentity': raw['backendIdentity'],
            'requestId': args['requestId'], 'resourceId': raw['id'], 'resourceRevision': raw['revision'],
            'admissionRevision': args['expectedRevision'], 'backendKey': raw['backendKey'],
            'snapshot': raw['snapshot']}
        if backend_kind == 'incus':
            return incus_worker.execute_call({**payload, 'incus': config['incus']})
        return worker.execute_call({**payload, 'dockerHost': config['dockerHost']})
    monkeypatch.setattr(owner, 'invoke', invoke)
    failed = await act(app, 'remove', row, 'receipt-remove')
    row, original = failed['resource'], failed['request']
    assert original['state'] == 'failed'
    diagnostic = {'version': 1, 'reasonCode': 'unsupported_type',
                  'memberType': 'fifo', 'relativePath': 'artifacts/fixture-pipe'}
    assert original['result']['verification'] == diagnostic
    assert original['result']['code'] == 'archive_verification_failed'
    assert original['result']['phase'] == 'verifying' and original['result']['effectAdmitted'] is False
    if backend_kind == 'docker':
        assert original['result']['removal']['destroyEffect'] == 'not_dispatched'
    else:
        assert sum(kind == 'export' for kind, _ in incus_calls) == 1
        assert not any(args[0] == 'destroy' for kind, args in incus_calls if kind == 'cli')
    assert not any(name == 'destroy' for name, *_ in effects)
    assert row['status'] == 'stopped'
    owned = owner.get(app.data_dir, source['id'], row['id'])
    assert owned['owner'] == {'sessionId': source['id'], 'principal': source['runtimeSessionId']}
    assert owned['backendType'] == backend_kind and owned['status'] != 'retired'
    args = removal_read_args(app, row, original)
    await app.on_runtime_event('runtime.status', {'sessionId': source['id'], 'status': 'idle'})
    app._state['selectedSessionId'] = peer['id']
    app._save()
    before = removal_read_snapshot(app, row)
    home, workspace = app.data_dir, app.default_workspace
    old_db, old_requests, old_journal = app.db, app.operations.requests, app.operations.requests.journal
    await app.close()
    with pytest.raises(recovery.sqlite3.ProgrammingError):
        old_db.execute('SELECT 1')
    with pytest.raises(recovery.sqlite3.ProgrammingError):
        old_journal.db.execute('SELECT 1')
    restored = AppService(home, Runtime(), workspace=workspace)
    try:
        assert restored.db is not old_db and restored.operations.requests is not old_requests
        assert restored.operations.requests.journal is not old_journal
        assert restored.operations.requests.journal.db is not old_journal.db
        assert restored.operations.requests.read(source['id'], original['requestId']) == original
        assert owner.get(home, source['id'], row['id']) == owned
        saved, controls = state_records.load_owner(restored.db, source['id'])
        assert {key: saved[key] for key in placement} == placement
        assert saved['runtimeSessionId'] == source['runtimeSessionId']
        # Force the saved-state path even if startup kept this owner resident.
        restored._state['sessions'][:] = [item for item in restored._state['sessions'] if item['id'] != source['id']]
        restored._state['selectedSessionId'] = peer['id']
        restored.projections.invalidate()
        assert removal_read_snapshot(restored, row) == before
        overlay = [item['id'] for item in restored._state['sessions']]
        db_changes = restored.db.total_changes
        journal_changes = restored.operations.requests.journal.db.total_changes
        visits = []
        read_request = restored.operations.requests.read
        ledger_connections = []
        connect = recovery.sqlite3.connect
        def readonly_ledger(database, *values, **options):
            assert database.endswith('reservations.sqlite3?mode=ro')
            assert not values and options == {'uri': True, 'timeout': 0}
            connection = connect(database, **options)
            ledger_connections.append(connection)
            return connection
        def reopened_read(sid, identity):
            visits.append((sid, identity))
            return read_request(sid, identity)
        def forbidden(*args, **kwargs):
            pytest.fail('Saved native diagnostic read must not hydrate, admit, persist or call a backend')
        with monkeypatch.context() as guard:
            calls = forbid_removal_read_effects(restored, guard)
            guard.setattr(old_requests, 'read', forbidden)
            guard.setattr(restored.operations.requests, 'read', reopened_read)
            guard.setattr(recovery.sqlite3, 'connect', readonly_ledger)
            for method in ('_session', '_admit_session', '_save'):
                guard.setattr(restored, method, forbidden)
            guard.setattr(restored.history, 'refresh', forbidden)
            guard.setattr(restored.cold_display, 'hydrate', forbidden)
            guard.setattr(session_projection, 'hydrate', forbidden)
            guard.setattr(state_records, 'load_owner', forbidden)
            guard.setattr(restored.test_queue, 'start', forbidden)
            guard.setattr(worker, 'execute_call', forbidden)
            guard.setattr(incus_worker, 'execute_call', forbidden)
            observed = (await agent(restored, transport, REMOVAL_READ, args, 'reopened-native-read',
                                    _runtimeSessionId=source['runtimeSessionId']))['result']
            assert observed == {'version': 1, 'sessionId': source['id'], 'id': row['id'],
                'revision': row['revision'], 'original': {'requestId': original['requestId'], 'state': 'failed'},
                'status': 'held', 'code': 'original_binding_unverified', 'executor': 'unobserved',
                'physical': 'not_observed', 'continuationAvailable': False, 'riskDecisionAvailable': False,
                'verification': {'status': 'recorded', **diagnostic}}
            assert visits == [(source['id'], original['requestId'])] * 2
            assert len(ledger_connections) == 1
            for altered, principal, status in [
                (args, 'unrelated-child', 403),
                ({**args, 'sessionId': peer['id']}, source['runtimeSessionId'], 403),
                ({**args, 'expectedRevision': row['revision'] - 1}, source['runtimeSessionId'], 409),
            ]:
                with pytest.raises(AppError) as caught:
                    await agent(restored, transport, REMOVAL_READ, altered, 'reopened-refusal',
                                _runtimeSessionId=principal)
                assert caught.value.status == status
            assert visits == [(source['id'], original['requestId'])] * 2
            assert len(ledger_connections) == 2  # Exact read and stale revision; neither foreign principal.
            assert ledger_connections[0] is not ledger_connections[1]
        assert not calls and not restored.runtime.calls
        assert restored.db.total_changes == db_changes
        assert restored.operations.requests.journal.db.total_changes == journal_changes
        assert [item['id'] for item in restored._state['sessions']] == overlay
        assert restored._state['selectedSessionId'] == peer['id']
        assert state_records.load_owner(restored.db, source['id']) == (saved, controls)
        assert removal_read_snapshot(restored, row) == before
    finally:
        await restored.close()


def test_public_removal_read_declares_only_host_resolved_identity_and_revision():
    schema = require_removal_read_contract()
    assert set(schema['properties']) == {
        'workspaceId', 'sessionId', 'id', 'expectedRevision', 'originalRequestId'}
    assert set(schema['required']) == set(schema['properties'])
    assert schema['additionalProperties'] is False


@pytest.mark.parametrize('state', ['active-ungranted', 'terminal', 'stopped', 'revoked'])
async def test_public_removal_read_is_passive_even_after_generation_or_authority_end(app, backend, monkeypatch, state):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    source, peer = app.state['sessions']
    if state == 'terminal':
        source['collaborationGeneration']['terminal'] = True
    elif state == 'stopped':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    elif state == 'revoked':
        permission = await grant(app, ['test_environments'], ['session'], identity='read-only-old-grant')
        await revoke(app, permission)
    app.state['selectedSessionId'] = peer['id']  # Selection cannot retarget this read.
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    args = removal_read_args(app, row, original)
    result = await agent(app, source, REMOVAL_READ, args, 'passive-original-read')
    assert result['accepted'] is True
    assert_unverified_removal_read(result['result'], row, original)
    again = await agent(app, source, REMOVAL_READ, args, 'passive-original-read')
    assert again['result'] == result['result']
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('lookalike', ['stopped-observation', 'archives', 'phase-claim'])
async def test_public_removal_read_does_not_infer_history_from_current_state_or_files(app, backend, monkeypatch, lookalike):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    if lookalike == 'stopped-observation':
        observer(monkeypatch, 'stopped')
        row = (await act(app, 'reconcile', row, 'public-stopped-observation'))['resource']
        assert row['status'] == 'stopped'
    else:
        folder = Path(row['retention']['path'])
        (folder / 'workspace.tar').write_bytes(b'synthetic immutable export, not proof')
        (folder / 'artifacts.tar').write_bytes(b'synthetic immutable artifact, not proof')
        if lookalike == 'phase-claim':
            (folder / 'removal-failed.json').write_text(json.dumps({
                'requestId': original['requestId'], 'destroyEffect': 'not_dispatched',
                'executorTerminal': True, 'verified': True,
                'privateConfig': 'SYNTHETIC_UNRELATED_SECRET'}))
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    value = await app.dispatch(REMOVAL_READ, removal_read_args(app, row, original), include_state=False)
    assert_unverified_removal_read(value['result'], row, original)
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('intruder', ['peer', 'child-principal', 'child-root', 'workspace'])
async def test_public_removal_read_requires_the_exact_transport_root(app, backend, monkeypatch, intruder):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    source, peer = app.state['sessions']
    peer['collaborationGeneration'] = {
        'id': 'peer-generation', 'inputIds': ['peer-input'], 'terminal': False}
    args, transport = removal_read_args(app, row, original), {}
    caller = peer if intruder == 'peer' else source
    if intruder == 'child-principal':
        transport['_runtimeSessionId'] = 'synthetic-child-runtime'
    elif intruder == 'child-root':
        source['sessionKind'] = 'child'
    elif intruder == 'workspace':
        args['workspaceId'] = 'another-workspace'
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Removal inspection requires the recorded root owner') as caught:
        await agent(app, caller, REMOVAL_READ, args, 'foreign-removal-read', **transport)
    assert caught.value.status == 403
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('binding', ['missing', 'wrong-resource', 'wrong-action'])
async def test_public_removal_read_refuses_to_attach_or_reinterpret_original_requests(app, backend, monkeypatch, binding):
    require_removal_read_contract()
    from amplifier_web.operation_requests import OperationRequests
    row, original = await removal_read_fixture(app, monkeypatch)
    if binding == 'wrong-action':
        original = app.operations.requests.read(row['sessionId'], 'public-read-start')
    else:
        original, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove',
            {'requestId': 'unattached-original'}, {})
        if binding == 'wrong-resource':
            original = app.operations.requests.finish(original, 'admitting', resourceId='other-resource')
        original = OperationRequests(app.operations.requests.journal).read(row['sessionId'], original['requestId'])
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    if binding == 'missing':
        value = await app.dispatch(REMOVAL_READ, removal_read_args(app, row, original), include_state=False)
        assert_unverified_removal_read(value['result'], row, original, 'original_binding_missing')
    else:
        with pytest.raises(AppError, match='Original removal is not bound to this resource') as caught:
            await app.dispatch(REMOVAL_READ, removal_read_args(app, row, original), include_state=False)
        assert caught.value.status == 409
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('refusal', ['stale-revision', 'busy'])
async def test_public_removal_read_does_not_repair_or_write_when_snapshot_is_unavailable(app, backend, monkeypatch, refusal):
    require_removal_read_contract()
    from contextlib import nullcontext
    row, original = await removal_read_fixture(app, monkeypatch)
    args = removal_read_args(app, row, original)
    if refusal == 'stale-revision':
        observer(monkeypatch, 'stopped')
        row = (await act(app, 'reconcile', row, 'public-read-advance'))['resource']
        assert row['revision'] != args['expectedRevision']
    lock = FileLock(str(app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')))
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with lock if refusal == 'busy' else nullcontext():
        with pytest.raises(AppError, match='Removal inspection changed or is busy') as caught:
            await app.dispatch(REMOVAL_READ, args, include_state=False)
    assert caught.value.status == 409
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('extra', [
    {'path': '/untrusted/archive'}, {'backend': 'different-backend'},
    {'executorTerminal': True}, {'continue': True}, {'operatorRiskAccepted': True},
])
async def test_public_removal_read_accepts_no_caller_proof_path_or_risk_choice(app, backend, monkeypatch, extra):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Additional properties') as caught:
        await app.dispatch(REMOVAL_READ, {**removal_read_args(app, row, original), **extra}, include_state=False)
    assert caught.value.status == 400
    assert calls == [] and removal_read_snapshot(app, row) == before


async def test_public_removal_read_keeps_ledger_lock_and_original_revisions_unchanged(app, backend, monkeypatch):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    original = app.operations.requests.finish(original, original['state'],
        resourceRevision=2, admissionRevision=1)
    before = removal_read_snapshot(app, row)
    folder = app.data_dir / 'workspace-resources'
    paths = [folder / 'reservations.sqlite3', folder / ('environment-' + row['id'] + '.lock')]
    def metadata():
        return [(p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ctime_ns, p.stat().st_mode)
                for p in paths]
    ledger_before = metadata()
    connect, opened, prohibited = recovery.sqlite3.connect, [], []
    def observed_connect(database, **kwargs):
        opened.append((database, kwargs))
        assert database.endswith('reservations.sqlite3?mode=ro') and kwargs == {'uri': True, 'timeout': 0}
        return connect(database, **kwargs)
    def forbidden(*args, **kwargs):
        prohibited.append('write-or-phase-scan')
        raise AssertionError('No initializing connection or phase inference is permitted')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        guard.setattr(owner.storage, 'connect', forbidden)
        guard.setattr(owner.storage, 'save', forbidden)
        guard.setattr(worker, 'inspect_removal', forbidden)
        guard.setattr(recovery.sqlite3, 'connect', observed_connect)
        result = await app.dispatch(REMOVAL_READ, removal_read_args(app, row, original), include_state=False)
    assert len(opened) == 1 and prohibited == calls == []
    assert_unverified_removal_read(result['result'], row, original)
    assert metadata() == ledger_before and removal_read_snapshot(app, row) == before
    retained = app.operations.requests.read(row['sessionId'], original['requestId'])
    assert retained['resourceRevision'] == 2 and retained['admissionRevision'] == 1
    assert result['result']['revision'] == row['revision'] != retained['resourceRevision']


async def test_public_removal_read_missing_owner_storage_does_not_initialize_it(app, monkeypatch):
    require_removal_read_contract()
    folder = app.data_dir / 'workspace-resources'
    assert not folder.exists()
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Removal inspection changed or is busy') as caught:
        await app.dispatch(REMOVAL_READ, {**scope(app),
            'id': '00000000-0000-4000-8000-000000000001',
            'expectedRevision': 1, 'originalRequestId': 'absent-original'}, include_state=False)
    assert caught.value.status == 409 and not folder.exists() and calls == []


async def test_public_removal_read_missing_request_is_bounded_and_does_not_admit(app, backend, monkeypatch):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Original removal record is unavailable') as caught:
        await app.dispatch(REMOVAL_READ, {
            **removal_read_args(app, row, original), 'originalRequestId': 'absent-original'}, include_state=False)
    assert caught.value.status == 404
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('target,field,value,status', [
    ('row', 'owner', {'sessionId': 'other-root', 'principal': 'other-root'}, 403),
    ('row', 'sessionId', 'other-root', 403),
    ('row', 'workspaceId', 'other-workspace', 403),
    ('row', 'kind', 'scratch-directory', 403),
    ('original', 'sessionId', 'other-root', 409),
    ('original', 'requestId', 'other-request', 409),
    ('original', 'state', 'SYNTHETIC_PRIVATE_UNSUPPORTED_STATE', 409),
])
async def test_public_removal_read_rejects_malformed_retained_identity_without_echo(app, backend, monkeypatch, target, field, value, status):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    count = []
    get, read = owner.storage.get, app.operations.requests.read
    with monkeypatch.context() as fault:
        calls = forbid_removal_read_effects(app, fault)
        if target == 'row':
            def altered(db, sid, identity):
                count.append('row')
                return {**get(db, sid, identity), field: value}
            fault.setattr(owner.storage, 'get', altered)
        else:
            def altered(sid, identity):
                count.append('original')
                return {**read(sid, identity), field: value}
            fault.setattr(app.operations.requests, 'read', altered)
        with pytest.raises(AppError) as caught:
            await app.dispatch(REMOVAL_READ, removal_read_args(app, row, original), include_state=False)
    assert count == [target] and calls == []
    assert caught.value.status == status and 'SYNTHETIC_' not in str(caught.value)
    assert str(caught.value) in {
        'Removal inspection requires the recorded root owner.',
        'Original removal is not bound to this resource.',
        'Removal inspection changed or is busy.'}
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('drift', ['resource', 'request', 'execution', 'interruption'])
async def test_public_removal_read_rechecks_snapshot_before_returning(app, backend, monkeypatch, drift):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    source = app.state['sessions'][0]
    get, read = owner.storage.get, app.operations.requests.read
    observations = []
    with monkeypatch.context() as fault:
        calls = forbid_removal_read_effects(app, fault)
        if drift == 'resource':
            def changing_row(db, sid, identity):
                result = get(db, sid, identity)
                observations.append('resource')
                return {**result, 'revision': result['revision'] + 1} if len(observations) == 2 else result
            fault.setattr(owner.storage, 'get', changing_row)
        else:
            def changing_request(sid, identity):
                result = read(sid, identity)
                observations.append('request')
                if len(observations) == 1 and drift != 'request':
                    key = 'executionRevision' if drift == 'execution' else 'interruptionRevision'
                    fault.setitem(source, key, source.get(key, 0) + 1)
                return {**result, 'updatedAt': result['updatedAt'] + 1} if drift == 'request' and len(observations) == 2 else result
            fault.setattr(app.operations.requests, 'read', changing_request)
        with pytest.raises(AppError, match='Removal inspection changed or is busy') as caught:
            await app.dispatch(REMOVAL_READ, removal_read_args(app, row, original), include_state=False)
    assert caught.value.status == 409 and len(observations) == 2 and calls == []
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('boundary', ['ledger', 'journal'])
async def test_public_removal_read_storage_errors_stay_secret_free(app, backend, monkeypatch, boundary):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    entered = []
    def fail(*args, **kwargs):
        entered.append(boundary)
        raise OSError('SYNTHETIC_PRIVATE_STORAGE_PATH_OR_CREDENTIAL')
    with monkeypatch.context() as fault:
        calls = forbid_removal_read_effects(app, fault)
        if boundary == 'ledger':
            fault.setattr(recovery.sqlite3, 'connect', fail)
        else:
            fault.setattr(app.operations.requests, 'read', fail)
        with pytest.raises(AppError, match='Removal inspection changed or is busy') as caught:
            await app.dispatch(REMOVAL_READ, removal_read_args(app, row, original), include_state=False)
    assert caught.value.status == 409 and 'SYNTHETIC_' not in str(caught.value)
    assert entered == [boundary] and calls == []
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('alias', ['ledger', 'lock', 'directory'])
async def test_public_removal_read_rejects_storage_aliases_without_following_or_repair(app, backend, monkeypatch, alias):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    folder = app.data_dir / 'workspace-resources'
    path = (folder if alias == 'directory' else folder / 'reservations.sqlite3'
            if alias == 'ledger' else folder / ('environment-' + row['id'] + '.lock'))
    moved = path.with_name(path.name + '.synthetic-original')
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=alias == 'directory')
    try:
        calls = forbid_removal_read_effects(app, monkeypatch)
        with pytest.raises(AppError, match='Removal inspection changed or is busy') as caught:
            await app.dispatch(REMOVAL_READ, removal_read_args(app, row, original), include_state=False)
        assert caught.value.status == 409 and calls == [] and path.is_symlink()
    finally:
        path.unlink()
        moved.rename(path)
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('transport', ['ui', 'agent'])
async def test_public_removal_read_dormant_root_never_admits_repairs_or_persists(app, backend, monkeypatch, transport):
    """Expose the upstream admission seam; resident-only tests cannot prove this."""
    require_removal_read_contract()
    from amplifier_web import state_records
    row, original = await removal_read_fixture(app, monkeypatch)
    source, peer = app.state['sessions']
    args = removal_read_args(app, row, original)
    app._save()
    saved, controls = state_records.load_owner(app.db, source['id'])
    assert saved is not None
    app._state['sessions'].remove(source)  # Evict only, retain the real saved owner.
    app._state['selectedSessionId'] = peer['id']
    app.projections.invalidate()
    assert app.directory.resolve(source['id']) is not None
    assert source['id'] not in [s['id'] for s in app._state['sessions']]
    before = removal_read_snapshot(app, row)
    overlay = [s['id'] for s in app._state['sessions']]
    changes = app.db.total_changes
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append('dormant-admission-or-persistence')
        raise AssertionError('Passive removal read attempted dormant owner admission or persistence')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        guard.setattr(app, '_admit_session', forbidden)
        guard.setattr(app, '_save', forbidden)
        guard.setattr(app.cold_display, 'hydrate', forbidden)
        if transport == 'agent':
            result = await agent(app, source, REMOVAL_READ, args, 'cold-original-read')
        else:
            result = await app.dispatch(REMOVAL_READ, args, include_state=False)
    assert_unverified_removal_read(result['result'], row, original)
    assert attempted == calls == []
    assert app.db.total_changes == changes
    assert [s['id'] for s in app._state['sessions']] == overlay
    assert app._state['selectedSessionId'] == peer['id']
    assert state_records.load_owner(app.db, source['id']) == (saved, controls)
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('transport', ['ui', 'agent'])
async def test_public_removal_read_unknown_root_never_refreshes_or_admits(app, backend, monkeypatch, transport):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    source = app.state['sessions'][0]
    unknown = {**source, 'id': 'unknown-original-root'}
    args = {**removal_read_args(app, row, original), 'sessionId': unknown['id']}
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append('session-or-history-effect')
        raise AssertionError('Passive missing-owner inspection must not refresh or admit')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        guard.setattr(app, '_session', forbidden)
        guard.setattr(app, '_admit_session', forbidden)
        guard.setattr(app, '_save', forbidden)
        guard.setattr(app.history, 'refresh', forbidden)
        with pytest.raises(AppError, match='Removal inspection requires the recorded root owner') as caught:
            if transport == 'agent':
                await agent(app, unknown, REMOVAL_READ, args, 'unknown-root-read')
            else:
                await app.dispatch(REMOVAL_READ, args, include_state=False)
    assert caught.value.status == 403 and attempted == calls == []
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('transport', ['ui', 'agent'])
async def test_public_removal_read_does_not_retarget_a_native_alias(app, backend, monkeypatch, transport):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    source = app.state['sessions'][0]
    source.update(nativeIdentity='synthetic-native-alias', runtimeSessionId='synthetic-native-alias',
                  sessionKind='root', workingDirectory=source['workspace'],
                  executionRevision=0, interruptionRevision=0)
    app._publish()
    assert app.directory.resolve('synthetic-native-alias')['id'] == source['id']
    before = removal_read_snapshot(app, row)
    args = {**removal_read_args(app, row, original), 'sessionId': 'synthetic-native-alias'}
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Removal inspection requires the recorded root owner') as caught:
        if transport == 'agent':
            await agent(app, source, REMOVAL_READ, args, 'alias-read',
                        _runtimeSessionId='synthetic-native-alias')
        else:
            await app.dispatch(REMOVAL_READ, args, include_state=False)
    assert caught.value.status == 403 and calls == []
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('change', [
    'missing-id', 'missing-workspace', 'wrong-id', 'empty-workspace',
    'wrong-kind', 'native-parent', 'bad-execution', 'bad-interruption',
    'empty-working-directory', 'invalid-principal', 'native-missing-placement',
])
async def test_public_removal_read_requires_valid_passive_owner_metadata(app, backend, monkeypatch, change):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    source = app.state['sessions'][0]
    read_sources = app._session_read_sources
    visits = []
    def altered(identities, fields):
        assert identities == [source['id']]
        rows = read_sources(identities, fields)
        value = dict(rows[0])
        visits.append(change)
        if change in {'missing-id', 'missing-workspace'}:
            value.pop(change.removeprefix('missing-'), None)
        elif change == 'native-missing-placement':
            value['runtimeSessionId'] = 'synthetic-native'
            value.pop('executionRevision', None)
        else:
            key, item = {
                'wrong-id': ('id', 'other-root'),
                'empty-workspace': ('workspace', ''),
                'wrong-kind': ('sessionKind', 'worker'),
                'native-parent': ('nativeParentId', 'original-parent'),
                'bad-execution': ('executionRevision', True),
                'bad-interruption': ('interruptionRevision', -1),
                'empty-working-directory': ('workingDirectory', ''),
                'invalid-principal': ('runtimeSessionId', 7),
            }[change]
            value[key] = item
            if change == 'native-parent':
                value.pop('sessionKind', None)
        return [value]
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    monkeypatch.setattr(app, '_session_read_sources', altered)
    with pytest.raises(AppError, match='Removal inspection requires the recorded root owner') as caught:
        await agent(app, source, REMOVAL_READ, removal_read_args(app, row, original), 'metadata-read')
    assert caught.value.status == 403 and visits == [change] and calls == []
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('residency', ['resident', 'dormant'])
async def test_public_removal_read_uses_only_bounded_owner_fields_without_session_lookup(app, backend, monkeypatch, residency):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    source, peer = app.state['sessions']
    args = removal_read_args(app, row, original)
    if residency == 'dormant':
        app._save()
        app._state['sessions'].remove(source)
        app._state['selectedSessionId'] = peer['id']
        app.projections.invalidate()
        assert app.directory.resolve(source['id']) is not None
    before = removal_read_snapshot(app, row)
    read_sources, fields_read, attempted = app._session_read_sources, [], []
    def observed(identities, fields):
        assert identities == [source['id']]
        fields_read.append(tuple(fields))
        return read_sources(identities, fields)
    def forbidden(*args, **kwargs):
        attempted.append('admission')
        raise AssertionError('A passive owner field read must not admit a session')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        guard.setattr(app, '_session_read_sources', observed)
        guard.setattr(app, '_session', forbidden)
        guard.setattr(app, '_admit_session', forbidden)
        guard.setattr(app, '_save', forbidden)
        guard.setattr(app.history, 'refresh', forbidden)
        result = await agent(app, source, REMOVAL_READ, args, 'bounded-metadata-read')
    assert_unverified_removal_read(result['result'], row, original)
    expected_fields = ('id', 'sessionKind', 'workspace', 'workingDirectory', 'executionRevision',
                       'interruptionRevision', 'runtimeSessionId', 'nativeIdentity', 'nativeProject',
                       'nativeParentId')
    assert fields_read == [expected_fields, expected_fields]
    assert attempted == calls == [] and removal_read_snapshot(app, row) == before


async def test_public_removal_read_router_exception_does_not_bypass_mutation_admission(app, backend, monkeypatch):
    require_removal_read_contract()
    row = (await start(app, 'ordinary-admission-start'))['resource']
    source, peer = app.state['sessions']
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'],
            'requestId': 'ordinary-cold-stop'}
    app._save()
    app._state['sessions'].remove(source)
    app._state['selectedSessionId'] = peer['id']
    app.projections.invalidate()
    assert app.directory.resolve(source['id']) is not None
    attempted = []
    def observe_admission(metadata):
        attempted.append(metadata['id'])
        raise RuntimeError('synthetic ordinary mutation admission witness')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        guard.setattr(app, '_admit_session', observe_admission)
        with pytest.raises(RuntimeError, match='synthetic ordinary mutation admission witness'):
            await app.dispatch('workspace.environments.stop', args, include_state=False)
    assert attempted == [source['id']] and calls == []
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(source['id'], args['requestId'])


@pytest.mark.parametrize('residency', ['resident', 'dormant'])
async def test_public_removal_read_preserves_explicit_native_root_placement(app, backend, monkeypatch, residency):
    require_removal_read_contract()
    source, peer = app.state['sessions']
    source.update(sessionKind='root', runtimeSessionId='synthetic-recorded-runtime',
                  nativeIdentity='synthetic-recorded-runtime', nativeProject='synthetic-project',
                  workingDirectory=source['workspace'], executionRevision=7, interruptionRevision=3)
    row, original = await removal_read_fixture(app, monkeypatch)
    args = removal_read_args(app, row, original)
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['owner']['principal'] == source['runtimeSessionId']
    if residency == 'dormant':
        app._save()
        app._state['sessions'].remove(source)
        app._state['selectedSessionId'] = peer['id']
        app.projections.invalidate()
    before = removal_read_snapshot(app, row)
    fields = ('id', 'sessionKind', 'workspace', 'workingDirectory', 'executionRevision',
              'interruptionRevision', 'runtimeSessionId', 'nativeIdentity', 'nativeProject')
    observed_before = app._session_read_sources([source['id']], fields)
    calls = forbid_removal_read_effects(app, monkeypatch)
    result = await agent(app, source, REMOVAL_READ, args, 'native-original-read',
                         _runtimeSessionId=source['runtimeSessionId'])
    assert_unverified_removal_read(result['result'], row, original)
    assert app._session_read_sources([source['id']], fields) == observed_before
    assert observed_before[0]['executionRevision'] == 7 and observed_before[0]['interruptionRevision'] == 3
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('missing', ['workingDirectory', 'executionRevision', 'interruptionRevision', 'sessionKind'])
async def test_public_removal_read_cold_native_defaults_do_not_supply_missing_saved_facts(app, backend, monkeypatch, missing):
    require_removal_read_contract()
    from amplifier_web import state_records
    source, peer = app.state['sessions']
    source.update(sessionKind='root', runtimeSessionId='synthetic-recorded-runtime',
                  nativeIdentity='synthetic-recorded-runtime', workingDirectory=source['workspace'],
                  executionRevision=0, interruptionRevision=0)
    row, original = await removal_read_fixture(app, monkeypatch)
    args = removal_read_args(app, row, original)
    source.pop(missing)
    app._save()
    saved, _ = state_records.load_owner(app.db, source['id'])
    assert missing not in saved
    app._state['sessions'].remove(source)
    app._state['selectedSessionId'] = peer['id']
    app.projections.invalidate()
    metadata = app._session_read_sources([source['id']], (missing,))[0]
    assert missing in metadata  # Existing directory defaults must not fill proof gaps.
    before = removal_read_snapshot(app, row)
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append('admission')
        raise AssertionError('Incomplete saved owner cannot be admitted to repair metadata')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        guard.setattr(app, '_session', forbidden)
        guard.setattr(app, '_admit_session', forbidden)
        guard.setattr(app, '_save', forbidden)
        with pytest.raises(AppError, match='Removal inspection requires the recorded root owner') as caught:
            await agent(app, source, REMOVAL_READ, args, 'missing-native-facts',
                        _runtimeSessionId=source['runtimeSessionId'])
    assert caught.value.status == 403 and attempted == calls == []
    assert state_records.load_owner(app.db, source['id'])[0] == saved
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('transport', ['ui', 'agent'])
async def test_public_removal_read_rejects_project_retarget_before_owner_lookup(app, backend, monkeypatch, transport):
    require_removal_read_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    source = app.state['sessions'][0]
    args = {**removal_read_args(app, row, original), 'nativeProject': 'caller-selected-project'}
    before = removal_read_snapshot(app, row)
    looked_up = []
    def forbidden(*args, **kwargs):
        looked_up.append('owner')
        raise AssertionError('Invalid passive schema must fail before owner lookup')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        guard.setattr(app, '_session', forbidden)
        guard.setattr(app, '_session_read_sources', forbidden)
        with pytest.raises(AppError, match='Additional properties') as caught:
            if transport == 'agent':
                await agent(app, source, REMOVAL_READ, args, 'project-retarget-read')
            else:
                await app.dispatch(REMOVAL_READ, args, include_state=False)
    assert caught.value.status == 400 and looked_up == calls == []
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('form', ['view', 'native'])
@pytest.mark.parametrize('facts', [
    'complete', 'zero', 'sessionKind', 'workingDirectory',
    'executionRevision', 'interruptionRevision', 'null-interruption',
])
async def test_removal_owner_save_reopen_preserves_only_explicit_scalar_facts(app, backend, monkeypatch, form, facts):
    """Exercise the real ordinary save/reference producer, not a fixture serializer."""
    from contextlib import closing
    from amplifier_web import session_projection, state_records
    source, peer = app.state['sessions']
    source.update(sessionKind='root', runtimeSessionId='synthetic-saved-runtime',
                  nativeIdentity='synthetic-saved-runtime', nativeProject='synthetic-project',
                  workingDirectory=source['workspace'], executionRevision=7,
                  interruptionRevision=3, privateSetting='SYNTHETIC_PRIVATE_BODY_ONLY')
    if form == 'native':
        source['historyManaged'] = True
    row, original = await removal_read_fixture(app, monkeypatch)
    args = removal_read_args(app, row, original)
    fields = ('sessionKind', 'workingDirectory', 'executionRevision', 'interruptionRevision')
    app._save()
    first, _ = state_records.load_owner(app.db, source['id'])
    assert {key: first[key] for key in fields} == {key: source[key] for key in fields}
    if facts == 'zero':
        source.update(executionRevision=0, interruptionRevision=0)
    elif facts == 'null-interruption':
        source['interruptionRevision'] = None
    elif facts != 'complete':
        source.pop(facts)
    # A second ordinary save must neither retain an old omitted scalar nor add
    # a directory default. Reopen the durable ledger without admission/hydration.
    app._save()
    with closing(recovery.sqlite3.connect((app.data_dir / 'app.sqlite3').as_uri() + '?mode=ro',
                                         uri=True, timeout=0)) as reopened:
        saved, controls = state_records.load_owner(reopened, source['id'])
    assert saved == state_records.load_owner(app.db, source['id'])[0]
    assert {key: saved[key] for key in fields if key in saved} == {
        key: source[key] for key in fields if key in source}
    assert 'SYNTHETIC_PRIVATE_BODY_ONLY' not in json.dumps(saved)
    if form == 'view':
        assert saved['$view'] == 1 and saved.get('$viewPayload') and '$native' not in saved
        assert set(saved) == {'id', 'workspace', 'runtimeSessionId', '$view', '$viewPayload'} | {
            key for key in fields if key in source}
    else:
        assert saved['$native'] is True and '$view' not in saved and '$viewPayload' not in saved
    app._state['sessions'].remove(source)
    app._state['selectedSessionId'] = peer['id']
    app.projections.invalidate()
    assert app.directory.resolve(source['id']) is not None
    before = removal_read_snapshot(app, row)
    overlay = [item['id'] for item in app._state['sessions']]
    changes = app.db.total_changes
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append('admission-hydration-or-save')
        raise AssertionError('Inspecting saved scalars must not load the body or admit an owner')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        for name in ('_session', '_admit_session', '_save'):
            guard.setattr(app, name, forbidden)
        guard.setattr(app.cold_display, 'hydrate', forbidden)
        guard.setattr(session_projection, 'hydrate', forbidden)
        guard.setattr(state_records, 'load_owner', forbidden)
        if facts in {'complete', 'zero'}:
            result = await agent(app, source, REMOVAL_READ, args, 'saved-scalars-read',
                                 _runtimeSessionId=source['runtimeSessionId'])
            assert_unverified_removal_read(result['result'], row, original)
        else:
            with pytest.raises(AppError, match='Removal inspection requires the recorded root owner') as caught:
                await agent(app, source, REMOVAL_READ, args, 'missing-saved-scalars-read',
                            _runtimeSessionId=source['runtimeSessionId'])
            assert caught.value.status == 403
    assert attempted == calls == [] and app.db.total_changes == changes
    assert [item['id'] for item in app._state['sessions']] == overlay
    assert app._state['selectedSessionId'] == peer['id']
    assert state_records.load_owner(app.db, source['id']) == (saved, controls)
    assert removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('form', ['view', 'native'])
async def test_removal_read_never_backfills_a_preexisting_sparse_owner_reference(app, backend, monkeypatch, form):
    from amplifier_web import session_projection, state_records
    source, peer = app.state['sessions']
    source.update(sessionKind='root', runtimeSessionId='synthetic-legacy-runtime',
                  nativeIdentity='synthetic-legacy-runtime', workingDirectory=source['workspace'],
                  executionRevision=7, interruptionRevision=3)
    if form == 'native':
        source['historyManaged'] = True
    row, original = await removal_read_fixture(app, monkeypatch)
    args = removal_read_args(app, row, original)
    app._save()
    saved, controls = state_records.load_owner(app.db, source['id'])
    # Seed exactly the older private reference shape in this synthetic store.
    # It is historical test data, not a migration or recovery implementation.
    omitted = {'interruptionRevision'} if form == 'native' else {
        'sessionKind', 'workingDirectory', 'executionRevision', 'interruptionRevision'}
    legacy = {key: value for key, value in saved.items() if key not in omitted}
    assert legacy != saved
    app.db.execute("UPDATE state_records SET value=? WHERE kind='session' AND id=?",
                   (json.dumps(legacy), source['id']))
    app.db.commit()
    app._state['sessions'].remove(source)
    app._state['selectedSessionId'] = peer['id']
    app.projections.invalidate()
    metadata = app._session_read_sources([source['id']], tuple(omitted))[0]
    assert all(key in metadata for key in omitted)  # Summaries still have apparent facts.
    assert state_records.load_owner(app.db, source['id']) == (legacy, controls)
    before = removal_read_snapshot(app, row)
    changes = app.db.total_changes
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append('backfill-or-hydration')
        raise AssertionError('Legacy sparse references remain held; never backfill on read')
    with monkeypatch.context() as guard:
        calls = forbid_removal_read_effects(app, guard)
        for name in ('_session', '_admit_session', '_save'):
            guard.setattr(app, name, forbidden)
        guard.setattr(app.cold_display, 'hydrate', forbidden)
        guard.setattr(session_projection, 'hydrate', forbidden)
        guard.setattr(state_records, 'load_owner', forbidden)
        with pytest.raises(AppError, match='Removal inspection requires the recorded root owner') as caught:
            await agent(app, source, REMOVAL_READ, args, 'legacy-sparse-read',
                        _runtimeSessionId=source['runtimeSessionId'])
    assert caught.value.status == 403 and attempts == calls == []
    assert app.db.total_changes == changes
    assert state_records.load_owner(app.db, source['id']) == (legacy, controls)
    assert source['id'] not in [item['id'] for item in app._state['sessions']]
    assert removal_read_snapshot(app, row) == before


# Discovery contract. It reads existing journal rows; it never creates
# an operation or infers a resource binding for an older unbound request.
REMOVAL_LIST = 'workspace.environments.removal.list'


def require_removal_list_contract():
    from amplifier_web.service import ACTION_DEFINITIONS
    assert REMOVAL_LIST in ACTION_DEFINITIONS, 'Passive original-removal discovery is not implemented'
    return ACTION_DEFINITIONS[REMOVAL_LIST][1]


def removal_list_args(app, row, **page):
    return {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'], **page}


def test_removal_discovery_schema_contains_only_scope_resource_cas_and_bounded_page():
    schema = require_removal_list_contract()
    assert set(schema['properties']) == {
        'workspaceId', 'sessionId', 'id', 'expectedRevision', 'offset', 'limit'}
    assert set(schema['required']) == {'workspaceId', 'sessionId', 'id', 'expectedRevision'}
    assert schema['properties']['offset']['minimum'] == 0
    assert schema['properties']['limit']['minimum'] == 1
    assert schema['properties']['limit']['maximum'] == 20
    assert schema['additionalProperties'] is False


@pytest.mark.parametrize('lifecycle', ['active', 'terminal', 'stopped'])
async def test_removal_discovery_finds_original_after_later_stop_and_receipt_page(app, backend, monkeypatch, lifecycle):
    require_removal_list_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    row = (await act(app, 'stop', row, 'later-stop-not-the-removal'))['resource']
    assert row['lastOperation']['requestId'] == 'later-stop-not-the-removal'
    sid = row['sessionId']
    for index in range(105):
        request, _ = app.operations.requests.begin(sid, 'workspace.environments.stop',
            {'requestId': f'later-unrelated-{index}'}, {})
        app.operations.requests.finish(request, 'completed', resourceId=row['id'])
    assert original['requestId'] not in {r['requestId'] for r in app.operations.requests.list(sid)}
    source, peer = app.state['sessions']
    if lifecycle == 'terminal':
        source['collaborationGeneration']['terminal'] = True
    elif lifecycle == 'stopped':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    app.state['selectedSessionId'] = peer['id']
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    result = await agent(app, source, REMOVAL_LIST, removal_list_args(app, row), 'list-original')
    assert result['accepted'] is True
    assert result['result'] == {
        'version': 1, 'sessionId': sid, 'id': row['id'], 'revision': row['revision'],
        'items': [{'originalRequestId': original['requestId'],
                   'state': 'outcome_unknown', 'binding': 'resource'}],
        'nextOffset': None,
    }
    selected = result['result']['items'][0]
    read = await agent(app, source, REMOVAL_READ, {
        **removal_list_args(app, row), 'originalRequestId': selected['originalRequestId']}, 'read-discovered')
    assert_unverified_removal_read(read['result'], row, original)
    # Losing the passive response does not enqueue another operation. Re-reading
    # the same exact snapshot returns the same projection without a write.
    again = await agent(app, source, REMOVAL_LIST, removal_list_args(app, row), 'list-original')
    assert again['result'] == result['result']
    assert app.operations.requests.read(sid, original['requestId']) == original
    assert calls == [] and removal_read_snapshot(app, row) == before


async def test_removal_discovery_separates_unbound_rows_and_never_associates_other_resources(app, backend, monkeypatch):
    require_removal_list_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    sid = row['sessionId']
    peer = app.state['sessions'][1]
    seeded = []
    for request_id, root, binding in [
        ('unbound-original', sid, None),
        ('other-resource-original', sid, 'other-resource'),
        ('peer-original', peer['id'], row['id']),
    ]:
        request, _ = app.operations.requests.begin(root, 'workspace.environments.remove',
            {'requestId': request_id}, {})
        request = app.operations.requests.finish(request, 'outcome_unknown',
            **({'resourceId': binding} if binding is not None else {}),
            message='SYNTHETIC_PRIVATE_RESULT', result={'key': 'SYNTHETIC_PRIVATE_CONFIG'})
        seeded.append(request)
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    result = (await app.dispatch(REMOVAL_LIST, removal_list_args(app, row), include_state=False))['result']
    assert result['items'] == [
        {'originalRequestId': original['requestId'], 'state': 'outcome_unknown', 'binding': 'resource'},
        {'originalRequestId': 'unbound-original', 'state': 'outcome_unknown', 'binding': 'unbound'},
    ]
    assert result['nextOffset'] is None and len(json.dumps(result).encode()) < 16384
    assert 'SYNTHETIC_' not in json.dumps(result)
    for request in seeded:
        assert app.operations.requests.read(request['sessionId'], request['requestId']) == request
    assert calls == [] and removal_read_snapshot(app, row) == before


async def test_removal_discovery_is_bounded_and_pages_relevant_records_before_unrelated_noise(app, backend, monkeypatch):
    require_removal_list_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    sid = row['sessionId']
    # Historical data can contain multiple admissions. Discovery must enumerate,
    # not choose the latest or silently manufacture a single original.
    names = [original['requestId']]
    for index in range(22):
        name = f'historical-removal-{index:02}'
        request, _ = app.operations.requests.begin(sid, 'workspace.environments.remove', {'requestId': name}, {})
        app.operations.requests.finish(request, 'outcome_unknown', resourceId=row['id'])
        names.append(name)
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    first = (await app.dispatch(REMOVAL_LIST, removal_list_args(app, row), include_state=False))['result']
    assert len(first['items']) == 20 and first['nextOffset'] == 20
    second = (await app.dispatch(REMOVAL_LIST, removal_list_args(app, row, offset=20, limit=20),
                                 include_state=False))['result']
    assert len(second['items']) == 3 and second['nextOffset'] is None
    assert [x['originalRequestId'] for x in first['items'] + second['items']] == names
    assert all(set(x) == {'originalRequestId', 'state', 'binding'}
               and x['binding'] == 'resource' for x in first['items'] + second['items'])
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('refusal', ['peer', 'child', 'foreign-resource', 'stale-revision'])
async def test_removal_discovery_refuses_wrong_owner_or_changed_resource_before_effects(app, backend, monkeypatch, refusal):
    require_removal_list_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    source, peer = app.state['sessions']
    peer['collaborationGeneration'] = {'id': 'peer-generation', 'inputIds': ['peer-input'], 'terminal': False}
    args, transport = removal_list_args(app, row), {}
    caller = source
    if refusal == 'peer':
        caller = peer
    elif refusal == 'child':
        transport['_runtimeSessionId'] = 'synthetic-child'
    elif refusal == 'foreign-resource':
        args['id'] = '00000000-0000-4000-8000-000000000002'
    else:
        args['expectedRevision'] -= 1
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError) as caught:
        await agent(app, caller, REMOVAL_LIST, args, 'refused-discovery', **transport)
    assert (caught.value.status, str(caught.value)) in {
        (403, 'Removal inspection requires the recorded root owner.'),
        (409, 'Removal inspection changed or is busy.'),
    }
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original
    assert calls == [] and removal_read_snapshot(app, row) == before


@pytest.mark.parametrize('malformed', ['root', 'request-id', 'state', 'long-id'])
async def test_removal_discovery_rejects_malformed_retained_scalars_without_echo(app, backend, monkeypatch, malformed):
    require_removal_list_contract()
    row, original = await removal_read_fixture(app, monkeypatch)
    args = {'requestId': 'x' * 201 if malformed == 'long-id' else 'malformed-removal'}
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove', args, {})
    request = app.operations.requests.finish(request, 'outcome_unknown', resourceId=row['id'],
        message='SYNTHETIC_PRIVATE_MESSAGE', result={'key': 'SYNTHETIC_PRIVATE_CONFIG'})
    if malformed != 'long-id':
        key, value = {'root': ('sessionId', 'other-root'),
                      'request-id': ('requestId', 'other-request'),
                      'state': ('state', 'SYNTHETIC_PRIVATE_STATE')}[malformed]
        with app.operations.requests.journal.lock, app.operations.requests.journal.db:
            app.operations.requests.journal.db.execute(
                'UPDATE operation_requests SET value=? WHERE session_id=? AND id=?',
                (json.dumps({**request, key: value}), row['sessionId'], args['requestId']))
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Removal inspection changed or is busy') as caught:
        await app.dispatch(REMOVAL_LIST, removal_list_args(app, row), include_state=False)
    assert caught.value.status == 409 and 'SYNTHETIC_' not in str(caught.value)
    assert calls == [] and removal_read_snapshot(app, row) == before
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


async def test_removal_discovery_rejects_oversized_encoded_page_without_exposing_records(app, backend, monkeypatch):
    require_removal_list_contract()
    row, _ = await removal_read_fixture(app, monkeypatch)
    # Each ID is individually within 200 characters; the encoded page is not.
    for index in range(20):
        request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove',
            {'requestId': '\U00010400' * 190 + str(index)}, {})
        app.operations.requests.finish(request, 'outcome_unknown', resourceId=row['id'])
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Removal inspection changed or is busy') as caught:
        await app.dispatch(REMOVAL_LIST, removal_list_args(app, row), include_state=False)
    assert caught.value.status == 409 and calls == []
    assert removal_read_snapshot(app, row) == before


async def test_removal_discovery_rejects_unrepresentable_page_offset_without_effects(app, backend, monkeypatch):
    require_removal_list_contract()
    row, _ = await removal_read_fixture(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    calls = forbid_removal_read_effects(app, monkeypatch)
    with pytest.raises(AppError, match='Removal inspection changed or is busy') as caught:
        await app.dispatch(REMOVAL_LIST, removal_list_args(app, row, offset=10**100), include_state=False)
    assert caught.value.status == 409 and calls == []
    assert removal_read_snapshot(app, row) == before


async def test_finish_cleanup_preserves_original_unknown_and_keeps_owner(app, backend, monkeypatch):
    # Integration contract deliberately differs from the upstream positive
    # UNKNOWN continuation: stopped inventory cannot settle an old executor.
    # Make the retained owner consume a configured slot; counts default to unlimited.
    current = (await app.dispatch('testing.execution.read', {}))['result']
    await app.dispatch('testing.execution.save', {
        'expectedRevision': current['revision'], 'queue': {}, 'profiles': {},
        'concurrency': {'maxEnvironmentsPerChat': 1},
    })
    original = owner.invoke
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    monkeypatch.setattr(owner, 'invoke', lambda *args: {'ok': False, 'status': 'unknown'})
    failed = await act(app, 'remove', row, 'lost-removal')
    calls = observer(monkeypatch, 'stopped')
    row = (await act(app, 'reconcile', failed['resource'], 'inspect-stopped'))['resource']
    def finish(config, home, current, action, args):
        assert action == 'finishCleanup'
        calls.append(action)
        return {'ok': True, 'status': 'removed', 'retention': {'verified': True, 'archiveOnly': True}}
    monkeypatch.setattr(owner, 'invoke', finish)
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    with pytest.raises(AppError, match='original removal remains unresolved'):
        await act(app, 'finishCleanup', row, 'finish-cleanup', previousRequestId='lost-removal')
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert app.operations.requests.read(row['sessionId'], 'lost-removal') == failed['request']
    assert calls == ['reconcile']
    monkeypatch.setattr(owner, 'invoke', original)
    with pytest.raises(AppError, match='already owns'):
        await start(app, 'next-check')


@pytest.mark.parametrize('preparation_pending', [False, True])
async def test_cleanup_recovery_rejects_unrelated_request_before_effect(app, backend, preparation_pending):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    if preparation_pending:
        row = owner.save(app.data_dir, owner.get(app.data_dir, row['sessionId'], row['id']),
                         preparationPending=True)
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    result = await act(app, 'finishCleanup', row, 'bad-recovery', previousRequestId='stop')
    assert result['request']['state'] == 'failed'
    assert result['request']['result']['effectAdmitted'] is False
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert [name for name, _ in backend] == ['prepare', 'start', 'stop']
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


@pytest.mark.parametrize('phase,code', [
    ('verifying', 'archive_verification_failed'),
    ('preserving', 'retention_storage'),
])
async def test_finish_cleanup_after_known_preservation_failure_keeps_original_receipt(
        app, backend, monkeypatch, phase, code):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    monkeypatch.setattr(owner, 'invoke', lambda *a: {
        'ok': False, 'status': 'stopped', 'phase': phase,
        'effectAdmitted': False, 'code': code})
    failed = await act(app, 'remove', row, 'known-export-failure')
    assert failed['request']['state'] == 'failed'
    calls = []
    def finish(config, home, current, action, args):
        calls.append(action)
        bound = app.operations.requests.read(current['sessionId'], args['requestId'])
        assert bound['state'] == 'admitting' and bound['resourceId'] == current['id']
        return {'ok': True, 'status': 'removed', 'universeId': current['universeId'],
                'retention': {'verified': True, 'archiveOnly': True}}
    monkeypatch.setattr(owner, 'invoke', finish)
    result = await act(app, 'finishCleanup', failed['resource'], 'finish-known',
                       previousRequestId='known-export-failure')
    assert result['request']['state'] == 'completed' and result['resource']['status'] == 'removed'
    assert result['resource']['previousCleanup'] == failed['request']
    assert app.operations.requests.read(row['sessionId'], 'known-export-failure') == failed['request']
    duplicate = await act(app, 'finishCleanup', failed['resource'], 'finish-known',
                          previousRequestId='known-export-failure')
    assert duplicate['request'] == result['request'] and calls == ['finishCleanup']


@pytest.mark.parametrize('original_action', ['remove', 'finishCleanup'])
@pytest.mark.parametrize('binding', ['resource', 'unbound'])
@pytest.mark.parametrize('route', ['public', 'common'])
async def test_cleanup_alias_cannot_escape_full_journal_hold(app, backend, monkeypatch, original_action, binding, route):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'alias-stop'))['resource']
    prior, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.' + original_action,
        {'requestId': 'original-alias'}, {})
    prior = app.operations.requests.finish(prior, 'outcome_unknown',
        **({'resourceId': row['id']} if binding == 'resource' else {}))
    for n in range(105):
        later, _ = app.operations.requests.begin(row['sessionId'], 'fixture.later', {'requestId': f'later-{n}'}, {})
        app.operations.requests.finish(later, 'completed')
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: calls.append(args))
    args = {**scope(app), 'id': row['id'], 'expectedRevision': raw['revision'],
            'requestId': 'new-cleanup-alias', 'previousRequestId': prior['requestId']}
    if route == 'public':
        with pytest.raises(AppError, match='original removal remains unresolved'):
            await app.dispatch('workspace.environments.finishCleanup', args)
    else:
        request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.finishCleanup', args, {})
        request = app.operations.requests.finish(request, 'admitting', resourceId=row['id'])
        result = await asyncio.to_thread(owner.effect, app, raw, 'finishCleanup', args,
            owner.configuration(app)[0], request, lambda: None)
        assert result['request']['state'] == 'failed'
    assert not calls and owner.get(app.data_dir, row['sessionId'], row['id']) == raw
    assert app.operations.requests.read(row['sessionId'], prior['requestId']) == prior
