"""Shared task authority and durable DTU admission, with a controlled backend."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from filelock import FileLock
import pytest

from amplifier_web import task_environments as owner
from amplifier_web import task_resources as storage
from amplifier_web.service import AppError
from test_configuration_authority import app, grant, revoke
from test_coordination_generation import agent


def scope(app):
    source = app.state['sessions'][0]
    workspace = next(row for row in app.state['workspaces'] if row['path'] == source['workspace'])
    return {'workspaceId': workspace['id'], 'sessionId': source['id']}


@pytest.fixture
def backend(monkeypatch):
    config = {'python': '/managed/python', 'dockerHost': 'unix:///owned/docker.sock', 'image': owner.APPROVED_IMAGE}
    monkeypatch.setattr(owner, 'configuration', lambda app: (config, 'ready', 'Qualified test backend'))
    monkeypatch.setattr(owner.setup, 'read', AsyncMock(return_value={'status': 'ready', 'backends': [], 'operation': None}))
    calls = []
    def invoke(config, home, row, action, args):
        calls.append((action, dict(args)))
        value = {'ok': True, 'universeId': 'owned-universe',
                'backendIdentity': {'backend': 'docker', 'daemon': 'owned-daemon'},
                'snapshot': {'snapshot_commit': 'snapshot', 'archive_sha256': 'archive'},
                'status': {'start': 'running', 'read': row.get('environmentState', 'running'), 'stop': 'stopped', 'remove': 'removed'}.get(action, 'running'),
                'result': {'stdout': 'check passed\n', 'stderr': '', 'exit_code': 0}}
        if args.get('durableJob') or action in {'job_read', 'job_cancel'}:
            value['job'] = {'version': 1, 'id': args['requestId'], 'state': 'passed', 'terminal': True}
        return value
    monkeypatch.setattr(owner, 'invoke', invoke)
    return calls


async def start(app, request='start'):
    return (await app.dispatch('workspace.environments.start', {**scope(app), 'profileId': owner.PROFILE,
        'requestId': request}, command_id='outer-' + request))['result']


async def act(app, action, row, request, **extra):
    return (await app.dispatch('workspace.environments.' + action, {**scope(app), 'id': row['id'],
        'expectedRevision': row['revision'], 'requestId': request, **extra}, command_id='outer-' + request))['result']


async def test_disabled_read_is_passive_and_does_not_hydrate(app, monkeypatch):
    monkeypatch.setattr(app.directory, 'cold_display', lambda *a, **k: pytest.fail('cold history hydration'), raising=False)
    result = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert result['capability']['status'] == 'disabled' and not result['items']
    with pytest.raises(AppError, match='not been enabled'):
        await start(app)
    assert app.operations.requests.list(scope(app)['sessionId']) == []


async def test_start_execute_stop_remove_retain_storage_and_exact_requests(app, backend):
    first = await start(app)
    row = first['resource']
    path = Path(row['retention']['path'])
    (path / 'evidence').write_text('retained')
    assert first['request']['state'] == 'completed' and row['status'] == 'running'
    assert (await start(app))['resource']['id'] == row['id']
    run = await act(app, 'execute', row, 'run', command='python -V')
    assert run['request']['result']['result']['exit_code'] == 0
    assert (await act(app, 'execute', row, 'run', command='python -V'))['request'] == run['request']
    stopped = await act(app, 'stop', run['resource'], 'stop')
    assert stopped['resource']['status'] == 'stopped'
    removed = await act(app, 'remove', stopped['resource'], 'remove')
    assert removed['resource']['status'] == 'removed' and (path / 'evidence').read_text() == 'retained'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']
    assert (await start(app, 'second'))['resource']['id'] != row['id']


@pytest.mark.parametrize('key,value', [
    ('env', {'HOME': '/root'}), ('environment', {'PATH': '/caller'}),
    ('jobEnvironment', {'HOME': '/root'}), ('profileId', 'browser-test'),
    ('uid', 0), ('gid', 0), ('user', 'root'), ('durableJob', True),
])
async def test_manual_execute_schema_refuses_workload_policy_overrides(app, backend, key, value):
    row = (await start(app))['resource']
    with pytest.raises(AppError):
        await act(app, 'execute', row, 'override', command='true', **{key: value})
    assert [action for action, _ in backend] == ['prepare', 'start']


async def test_one_environment_per_root_and_no_storage_retirement_bypass(app, backend):
    current=(await app.dispatch('testing.execution.read',{}))['result']
    await app.dispatch('testing.execution.save',{'expectedRevision':current['revision'],'queue':{},'profiles':{},'concurrency':{'maxEnvironmentsPerChat':1}})
    row = (await start(app))['resource']
    with pytest.raises(AppError, match='already owns'):
        await start(app, 'competing')
    with pytest.raises(AppError, match='dedicated controls'):
        await app.dispatch('workspace.resources.retire', {**scope(app), 'id': row['id'],
            'expectedRevision': row['revision']}, command_id='retire-live')
    page = (await app.dispatch('workspace.resources.read', scope(app)))['result']
    assert not page['items']
    assert len(backend) == 2


async def test_artifact_recovery_is_separate_from_removal_and_never_replays(app, backend, monkeypatch):
    current=(await app.dispatch('testing.execution.read',{}))['result']
    await app.dispatch('testing.execution.save',{'expectedRevision':current['revision'],'queue':{},'profiles':{},'concurrency':{'maxEnvironmentsPerChat':1}})
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    original = owner.invoke
    def export(config, home, current, action, args):
        if action == 'exportArtifacts':
            backend.append((action, dict(args)))
            return {'ok': True, 'status': 'stopped', 'universeId': 'owned-universe',
                    'artifactExport': {'verified': True, 'selection': ['/artifacts'], 'archiveBytes': 100}}
        return original(config, home, current, action, args)
    monkeypatch.setattr(owner, 'invoke', export)
    result = await act(app, 'exportArtifacts', row, 'recover-artifacts')
    assert result['request']['state'] == 'completed'
    recovered = result['resource']
    assert recovered['status'] == 'stopped'
    assert recovered['lastOperation'] == row['lastOperation']
    assert recovered['artifactExport']['requestId'] == 'recover-artifacts'
    assert 'accounting' not in recovered['retention']
    held = owner.get(app.data_dir, scope(app)['sessionId'], row['id'])
    assert held['status'] != 'retired' and not held.get('guestRemoved')
    assert (await act(app, 'exportArtifacts', row, 'recover-artifacts'))['request'] == result['request']
    assert sum(action == 'exportArtifacts' for action, _ in backend) == 1
    with pytest.raises(AppError, match='already owns'):
        await start(app, 'not-a-new-slot')


async def test_artifact_recovery_requires_stopped_state_before_backend(app, backend):
    row = (await start(app))['resource']
    result = await act(app, 'exportArtifacts', row, 'running-export')
    assert result['request']['state'] == 'failed'
    assert not any(action == 'exportArtifacts' for action, _ in backend)


async def test_artifact_recovery_requires_owner_and_revision_without_extra_approval(app, backend):
    source, peer = app.state['sessions']
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'], 'requestId': 'protected-export'}
    with pytest.raises(AppError, match='calling conversation'):
        await agent(app, source, 'workspace.environments.exportArtifacts', {**args, 'sessionId': peer['id']}, 'peer')
    with pytest.raises(AppError, match='environment changed'):
        await act(app, 'exportArtifacts', {**row, 'revision': row['revision'] - 1}, 'stale')
    await agent(app, source, 'workspace.environments.exportArtifacts', args, 'owned-export')
    assert any(action == 'exportArtifacts' for action, _ in backend)
    assert not source['approvals']


async def test_unknown_artifact_export_keeps_old_operation_and_never_replays(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    def lost(*args):
        raise TimeoutError('export acknowledgement lost')
    monkeypatch.setattr(owner, 'invoke', lost)
    result = await act(app, 'exportArtifacts', row, 'lost-export')
    assert result['request']['state'] == 'outcome_unknown'
    assert result['resource']['lastOperation'] == row['lastOperation']
    assert result['resource']['status'] == 'stopped'
    monkeypatch.setattr(owner, 'invoke', lambda *args: pytest.fail('export replayed'))
    assert (await act(app, 'exportArtifacts', row, 'lost-export'))['request'] == result['request']


@pytest.mark.parametrize('lost_read', [False, True])
async def test_committed_artifact_receipt_repairs_metadata_without_export(app, backend, monkeypatch, lost_read):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    invoke, save, read = owner.invoke, owner.save, app.operations.requests.read
    rereads = []
    def transient_read(sid, identity):
        if identity == 'committed-export' and lost_read and not rereads:
            rereads.append(identity)
            raise OSError('journal read temporarily unavailable')
        return read(sid, identity)
    def export(config, home, resource, action, args):
        if action == 'exportArtifacts':
            backend.append((action, args))
            return {'ok': True, 'status': 'stopped', 'artifactExport': {'verified': True, 'selection': ['/artifacts']}}
        return invoke(config, home, resource, action, args)
    def fail_projection(home, resource, **changes):
        if changes.get('lastArtifactExport'):
            raise OSError('metadata unavailable')
        return save(home, resource, **changes)
    monkeypatch.setattr(owner, 'invoke', export)
    monkeypatch.setattr(owner, 'save', fail_projection)
    monkeypatch.setattr(app.operations.requests, 'read', transient_read)
    result = await act(app, 'exportArtifacts', row, 'committed-export')
    assert result['request']['state'] == 'completed'
    monkeypatch.setattr(app.operations.requests, 'read', read)
    assert read(scope(app)['sessionId'], 'committed-export')['state'] == 'completed'
    monkeypatch.setattr(owner, 'save', save)
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    recovered = next(item for item in page['items'] if item['id'] == row['id'])
    assert recovered['artifactExport']['requestId'] == 'committed-export'
    assert recovered['lastOperation'] == row['lastOperation']
    assert sum(action == 'exportArtifacts' for action, _ in backend) == 1


async def test_agent_tests_need_no_extra_approval_and_peer_never_borrows_owner(app, backend):
    source, peer = app.state['sessions']
    args = {**scope(app), 'profileId': owner.PROFILE, 'requestId': 'agent-start'}
    result = await agent(app, source, 'workspace.environments.start', args, 'agent-start')
    assert result['result']['resource']['status'] == 'running'
    with pytest.raises(AppError, match='calling conversation'):
        await agent(app, source, 'workspace.environments.read', {**scope(app), 'sessionId': peer['id']}, 'peer-read')
    row = result['result']['resource']
    await agent(app, source, 'workspace.environments.stop', {**scope(app), 'id': row['id'],
        'expectedRevision': row['revision'], 'requestId': 'owned-stop'}, 'stop')
    assert [action for action, _ in backend] == ['prepare', 'start', 'stop']
    assert not source['approvals']


async def test_timeout_is_retained_unknown_and_same_request_never_runs_again(app, backend, monkeypatch):
    row = (await start(app))['resource']
    def fail(*args):
        raise TimeoutError('lost command acknowledgement')
    monkeypatch.setattr(owner, 'invoke', fail)
    result = await act(app, 'execute', row, 'uncertain', command='long-running-check')
    assert result['request']['state'] == 'outcome_unknown' and result['resource']['status'] == 'unknown'
    monkeypatch.setattr(owner, 'invoke', lambda *args: pytest.fail('replayed unknown command'))
    again = await act(app, 'execute', row, 'uncertain', command='long-running-check')
    assert again['request'] == result['request']
    with pytest.raises(AppError, match='different operation arguments'):
        await act(app, 'execute', row, 'uncertain', command='different-command')


async def test_refused_command_preserves_existing_environment_ownership(app, backend, monkeypatch):
    current=(await app.dispatch('testing.execution.read',{}))['result']
    await app.dispatch('testing.execution.save',{'expectedRevision':current['revision'],'queue':{},'profiles':{},'concurrency':{'maxEnvironmentsPerChat':1}})
    row = (await start(app))['resource']
    monkeypatch.setattr(owner, 'invoke', lambda *args: {
        'ok': False, 'effectAdmitted': False, 'status': 'unknown',
        'message': 'Source readiness changed; no command was admitted.'})
    result = await act(app, 'execute', row, 'refused', command='check')
    assert result['request']['state'] == 'failed'
    assert owner.get(app.data_dir, scope(app)['sessionId'], row['id'])['backendAdmitted']
    with pytest.raises(AppError, match='already owns'):
        await start(app, 'competing-after-refusal')


async def test_unready_source_observation_blocks_host_execution(app, backend, monkeypatch):
    await start(app)
    reads = []
    def incomplete(config, home, row, action, args):
        reads.append(action)
        assert action == 'read'
        return {'ok': True, 'universeId': row['universeId'], 'status': 'unknown', 'sourceReady': False,
                'message': 'Source preparation is not confirmed for this exact snapshot.'}
    monkeypatch.setattr(owner, 'invoke', incomplete)
    row = (await app.dispatch('workspace.environments.read', scope(app)))['result']['items'][0]
    assert row['status'] == 'unknown' and row['sourceReady'] is False
    result = await act(app, 'execute', row, 'must-not-run', command='python -m unittest')
    assert result['request']['state'] == 'failed' and reads == ['read']


async def test_start_failure_and_cleanup_failure_keep_ownership(app, backend, monkeypatch):
    current=(await app.dispatch('testing.execution.read',{}))['result']
    await app.dispatch('testing.execution.save',{'expectedRevision':current['revision'],'queue':{},'profiles':{},'concurrency':{'maxEnvironmentsPerChat':1}})
    def fail(config, home, row, action, args):
        return {'ok': action == 'prepare', 'status': 'unknown', 'universeId': 'maybe-created', 'snapshot': {},
                'backendIdentity': {'backend': 'docker', 'daemon': 'owned-daemon'}}
    monkeypatch.setattr(owner, 'invoke', fail)
    failed = await start(app)
    assert failed['resource']['universeId'] == 'maybe-created' and failed['request']['state'] == 'outcome_unknown'
    with pytest.raises(AppError, match='already owns'):
        await start(app, 'do-not-duplicate')
    row = owner.get(app.data_dir, scope(app)['sessionId'], failed['resource']['id'])
    row = owner.save(app.data_dir, row, environmentState='stopped')
    before = Path(storage.directory(app.data_dir, row))
    (before / 'saved-output').write_text('keep')
    result = await act(app, 'remove', owner.public(app.data_dir, row), 'cleanup')
    assert result['request']['state'] == 'outcome_unknown'
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
    assert (before / 'saved-output').read_text() == 'keep'


async def test_stale_revision_and_active_owner_lock_cannot_clobber_state(app, backend):
    row = (await start(app))['resource']
    with pytest.raises(AppError, match='changed'):
        await act(app, 'stop', {**row, 'revision': 1}, 'stale')
    lock = app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')
    with FileLock(str(lock)):
        blocked = await act(app, 'stop', row, 'blocked')
    assert blocked['request']['state'] == 'failed'
    current = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert current['environmentState'] == 'running' and current['revision'] == row['revision']
    assert len(backend) == 2


@pytest.mark.parametrize('preparation_pending', [False, True])
async def test_execute_lock_refusal_is_not_admitted_and_preserves_owner(app, backend, preparation_pending):
    row = (await start(app))['resource']
    sid = row['sessionId']
    prior, _ = app.operations.requests.begin(sid, 'workspace.environments.start',
        {'requestId': 'competing-owner'}, {})
    prior = app.operations.requests.finish(prior, 'admitting', resourceId=row['id'])
    before = owner.save(app.data_dir, owner.get(app.data_dir, sid, row['id']),
        preparationPending=preparation_pending, pendingRequest=prior['requestId'])
    calls = list(backend)
    lock = app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')
    with FileLock(str(lock)):
        result = await act(app, 'execute', owner.public(app.data_dir, before),
                           'blocked-execute', command='python -V')
        saved = app.operations.requests.read(sid, 'blocked-execute')
        assert saved == result['request']
        assert owner.get(app.data_dir, sid, row['id']) == before
        assert app.operations.requests.read(sid, prior['requestId']) == prior
    assert saved['state'] == 'failed'
    assert saved['result']['status'] == 'not-started'
    assert saved['result']['effectAdmitted'] is False
    assert saved['result']['code'] == 'environment_lock_contended'
    assert result['resource'] == owner.public(app.data_dir, before)
    assert backend == calls
    again = await act(app, 'execute', owner.public(app.data_dir, before),
                      'blocked-execute', command='python -V')
    assert again['request'] == saved
    assert owner.get(app.data_dir, sid, row['id']) == before
    assert backend == calls


@pytest.mark.parametrize('exception', [TimeoutError, owner.LockTimeout])
async def test_execute_post_admission_timeout_stays_unknown(app, backend, monkeypatch, exception):
    row = (await start(app))['resource']
    row = owner.save(app.data_dir, owner.get(app.data_dir, row['sessionId'], row['id']),
                     preparationPending=True)
    def lost(*args):
        backend.append(('execute', {}))
        raise exception('lost after backend admission')
    monkeypatch.setattr(owner, 'invoke', lost)
    result = await act(app, 'execute', owner.public(app.data_dir, row),
                       'admitted-timeout', command='python -V')
    saved = app.operations.requests.read(row['sessionId'], 'admitted-timeout')
    assert saved == result['request'] and saved['state'] == 'outcome_unknown'
    assert saved['result']['status'] == 'unknown'
    assert 'effectAdmitted' not in saved['result']
    assert [action for action, _ in backend].count('execute') == 1


@pytest.mark.parametrize('state', ['outcome_unknown', 'running'])
async def test_execute_lock_refusal_cannot_reclassify_retained_request(app, backend, state):
    row = (await start(app))['resource']
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    args = {'requestId': 'old-execute', 'command': 'true', 'expectedRevision': row['revision']}
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.execute', args, {})
    retained = app.operations.requests.finish(request, state, resourceId=row['id'],
        result={'ok': False, 'status': 'not-started'}, message='Historical outcome remains unresolved.')
    lock = app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')
    with FileLock(str(lock)), pytest.raises(owner.LockTimeout):
        await asyncio.to_thread(owner.effect, app, before, 'execute', args,
            owner.configuration(app)[0], request, lambda: pytest.fail('admission ran'))
    assert app.operations.requests.read(row['sessionId'], 'old-execute') == retained
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert [action for action, _ in backend] == ['prepare', 'start']


@pytest.mark.parametrize('exception', [ValueError, owner.LockTimeout])
async def test_execute_later_admission_error_does_not_borrow_initial_lock_proof(app, backend, exception):
    row = (await start(app))['resource']
    before = owner.save(app.data_dir, owner.get(app.data_dir, row['sessionId'], row['id']),
                        preparationPending=True)
    args = {'requestId': 'later-error', 'command': 'true', 'expectedRevision': before['revision']}
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.execute', args, {})
    def refuse():
        raise exception('later admission error')
    result = await asyncio.to_thread(owner.effect, app, before, 'execute', args,
        owner.configuration(app)[0], request, refuse)
    assert result['request']['state'] == 'outcome_unknown'
    assert 'effectAdmitted' not in result['request']['result']
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert [action for action, _ in backend] == ['prepare', 'start']


async def test_ownerless_pending_request_becomes_unknown_without_replay(app, backend, monkeypatch):
    row = (await start(app))['resource']
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.execute', {'requestId': 'interrupted'}, {})
    app.operations.requests.finish(request, 'admitting', resourceId=row['id'])
    owner.save(app.data_dir, raw, pendingRequest='interrupted')
    monkeypatch.setattr(owner, 'configuration', lambda app: (None, 'disabled', 'disabled'))
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert page['items'][0]['status'] == 'unknown'
    assert page['items'][0]['lastOperation']['state'] == 'outcome_unknown'
    assert len(backend) == 2


async def test_failed_preparation_can_be_removed_without_backend_teardown(app, backend, monkeypatch):
    monkeypatch.setattr(owner, 'invoke', lambda *args: {'ok': False, 'code': 'not-a-repository'})
    result = await start(app)
    assert result['resource']['status'] == 'not-started' and result['request']['state'] == 'failed'
    monkeypatch.setattr(owner, 'invoke', lambda *args: pytest.fail('never-launched environment called backend'))
    removed = await act(app, 'remove', result['resource'], 'retire-preparation')
    assert removed['resource']['status'] == 'removed'
    assert Path(removed['resource']['retention']['path']).exists()


async def test_stopped_task_during_reservation_leaves_recoverable_local_owner(app, backend, monkeypatch):
    source = app.state['sessions'][0]
    original = owner.prepare
    entered, release = threading.Event(), threading.Event()
    def waiting(*args):
        row = original(*args)
        entered.set()
        assert release.wait(5)
        return row
    monkeypatch.setattr(owner, 'prepare', waiting)
    task = asyncio.create_task(start(app, 'delayed-reservation'))
    assert await asyncio.to_thread(entered.wait, 5)
    source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    release.set()
    with pytest.raises(AppError, match='moved or stopped'):
        await task
    result = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    row = result['items'][0]
    assert row['status'] == 'not-started'
    assert (await act(app, 'remove', row, 'retire-reservation'))['resource']['status'] == 'removed'
    assert backend == []


async def test_managed_uninstall_preserves_backend_until_all_resources_retired(app):
    from amplifier_web.smart_tools import SmartToolsManager
    manager = app.smart_tools = SmartToolsManager(app)
    identity = 'd' * 24
    directory = manager.root / 'installs' / identity
    directory.mkdir(parents=True)
    (directory / 'retained-core').write_text('needed for recovery')
    manager.state['installations'] = [{'id': identity}]
    row = owner.prepare(app.data_dir, 'owner', 'workspace', {'sessionId': 'owner'}, '/source', 'key', 'request', identity)
    with pytest.raises(ValueError, match='still owns test environments'):
        await manager.uninstall(identity)
    assert (directory / 'retained-core').read_text() == 'needed for recovery'
    owner.save(app.data_dir, row, status='retired', environmentState='removed')
    assert (await manager.uninstall(identity))['status'] == 'uninstalled'
    assert not directory.exists()


async def test_stop_while_waiting_prevents_backend_start(app, backend, monkeypatch):
    source = app.state['sessions'][0]
    entered, release = threading.Event(), threading.Event()
    original = storage.allocate
    def waiting(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(storage, 'allocate', waiting)
    args = {**scope(app), 'profileId': owner.PROFILE, 'requestId': 'delayed'}
    task = asyncio.create_task(agent(app, source, 'workspace.environments.start', args, 'delayed'))
    assert await asyncio.to_thread(entered.wait, 5)
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    release.set()
    result = await task
    assert result['result']['request']['state'] == 'failed' and backend == []


def test_operator_config_is_exact_private_managed_core(app, tmp_path, monkeypatch):
    pin = owner.DTU_COMMIT
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    app.smart_tools = SimpleNamespace(state={'installations': []}, close=AsyncMock())
    identity = 'b' * 24
    python = app.data_dir / 'smart-tools' / 'installs' / identity / 'venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_text('managed interpreter')
    app.smart_tools.state['installations'].append({'id': identity, 'commit': pin, 'repository': owner.REPOSITORY,
        'status': 'installed', 'extras': [], 'python': str(python)})
    target = app.data_dir / 'test-environments.json'
    value = {'enabled': True, 'installationId': identity, 'commit': pin, 'dockerHost': 'unix:///var/run/docker.sock'}
    target.write_text(json.dumps(value)); target.chmod(0o600)
    assert owner.configuration(app)[1] == 'ready'
    from amplifier_web import incus_environment_worker
    incus = {'qualified': 'explicit fixture'}
    monkeypatch.setattr(incus_environment_worker, 'validate_configuration', lambda v: {**v, 'validated': True})
    alternate = {key: item for key, item in value.items() if key != 'dockerHost'}
    target.write_text(json.dumps({**alternate, 'backend': 'incus', 'incus': incus}))
    configured, status, _ = owner.configuration(app)
    assert status == 'ready' and configured['incus']['validated'] and configured['python'] == str(python)
    target.write_text(json.dumps({**value, 'backend': 'incus', 'incus': incus}))
    assert owner.configuration(app)[1] == 'unavailable'  # No mixed/implicit backend selection.
    for bad in [{**value, 'retentionMode': 'full'}, {**value, 'retentionMode': 'invalid'}, {**value, 'image': 'arbitrary-image'}, {**value, 'commit': 'c' * 40}, {**value, 'dockerHost': 'tcp://remote:2375'}]:
        target.write_text(json.dumps(bad))
        assert owner.configuration(app)[1] == 'unavailable'
    target.write_text(json.dumps(value)); target.chmod(0o644)
    assert owner.configuration(app)[1] == 'unavailable'
    target.chmod(0o600)
    target.write_text('[]')
    assert owner.configuration(app)[1] == 'unavailable'
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', False)
    monkeypatch.delattr(owner.os, 'getuid')
    assert owner.configuration(app)[1] == 'unavailable'


@pytest.mark.parametrize('action', ['start', 'execute', 'stop', 'remove', 'execute-failed'])
@pytest.mark.parametrize('persistent', [False, True])
async def test_committed_effect_survives_projection_failure_and_read_repairs(app, backend, monkeypatch, action, persistent):
    operation = action.removesuffix('-failed')
    row = None if operation == 'start' else (await start(app))['resource']
    if operation == 'remove':
        row = (await act(app, 'stop', row, 'before-remove'))['resource']
    request_id = 'projection-' + action
    save, failures = owner.save, 0
    invoke = owner.invoke
    if action == 'execute-failed':
        def fail_check(config, home, resource, effect, args):
            result = invoke(config, home, resource, effect, args)
            if effect == 'execute':
                result['result']['exit_code'] = 7
            return result
        monkeypatch.setattr(owner, 'invoke', fail_check)
    expected_state = 'failed' if action == 'execute-failed' else 'completed'

    def fail_projection(home, resource, **changes):
        nonlocal failures
        exact = changes.get('lastOperation', {})
        if exact.get('requestId') == request_id and (persistent or failures == 0):
            failures += 1
            assert exact['state'] == expected_state
            assert app.operations.requests.read(scope(app)['sessionId'], request_id) == exact
            raise OSError('resource projection unavailable after operation commit')
        return save(home, resource, **changes)

    monkeypatch.setattr(owner, 'save', fail_projection)
    before = len(backend)
    result = (await start(app, request_id) if operation == 'start' else
              await act(app, operation, row, request_id, **({'command': 'check'} if operation == 'execute' else {})))
    exact = result['request']
    assert exact['state'] == expected_state
    assert [effect for effect, _ in backend[before:]] == (['prepare', 'start'] if operation == 'start' else [operation])
    stored = owner.get(app.data_dir, scope(app)['sessionId'], result['resource']['id'])
    assert stored.get('pendingRequest') == (request_id if persistent else None)
    if persistent:
        assert failures == 2  # Original projection and one metadata-only retry.
        assert result['resource']['observation']['status'] == 'unverified'
        monkeypatch.setattr(owner, 'save', save)
        calls = len(backend)
        page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
        repaired = next(item for item in page['items'] if item['id'] == stored['id'])
        assert [effect for effect, _ in backend[calls:]] == ([] if operation == 'remove' else ['read'])
    else:
        repaired = result['resource']
        assert failures == 1
    assert repaired['status'] == {'start': 'running', 'execute': 'running', 'stop': 'stopped', 'remove': 'removed'}[operation]
    assert repaired['lastOperation'] == exact
    assert app.operations.requests.read(scope(app)['sessionId'], request_id) == exact
    assert owner.get(app.data_dir, scope(app)['sessionId'], stored['id']).get('pendingRequest') is None
    if operation == 'remove':
        assert owner.get(app.data_dir, scope(app)['sessionId'], stored['id'])['status'] == 'retired'
        assert (await start(app, 'next-check'))['resource']['id'] != stored['id']


async def test_next_effect_repairs_prior_confirmed_projection_before_admission(app, backend, monkeypatch):
    row = (await start(app))['resource']
    save = owner.save

    def fail_stop_projection(home, resource, **changes):
        if changes.get('lastOperation', {}).get('action') == 'workspace.environments.stop':
            raise OSError('stop projection unavailable')
        return save(home, resource, **changes)

    monkeypatch.setattr(owner, 'save', fail_stop_projection)
    stopped = await act(app, 'stop', row, 'saved-stop')
    assert stopped['request']['state'] == 'completed'
    assert stopped['resource']['status'] == 'stopping'
    monkeypatch.setattr(owner, 'save', save)
    removed = await act(app, 'remove', stopped['resource'], 'after-saved-stop')
    assert removed['request']['state'] == 'completed' and removed['resource']['status'] == 'removed'
    assert [action for action, _ in backend] == ['prepare', 'start', 'stop', 'remove']
