"""Shared-action authority and preparation/guest cleanup commit boundaries."""
# ruff: noqa: F401, F811
import asyncio
import threading
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web import task_environments as owner
from amplifier_web.service import AppError
from test_configuration_authority import app, grant
from test_coordination_generation import agent, until
from test_task_environments import backend, scope, act
from test_test_queue import queue, record, submit, drive


@pytest.fixture
def prepared(app, monkeypatch):
    calls = []
    def prepare(config, root, snapshot, profile_id, request_id, *,
                resource_request=None, host_reservation=None, timing_request=None, dependency_store=None):
        assert dependency_store == app.data_dir / 'test-dependency-layers'
        assert resource_request == owner.profiles.freeze(profile_id)
        assert timing_request == owner.profiles.freeze_timing(profile_id)
        assert host_reservation['supported'] is False  # Controlled backend has no host target.
        calls.append((snapshot, profile_id, request_id))
        return {'version': 1, 'status': 'ready', 'profileId': profile_id,
                'sourceSha256': snapshot['archive_sha256'], 'backend': 'docker', 'image': 'sha256:'+'a'*64,
                'resourceRequest': resource_request, 'timingRequest': timing_request}
    monkeypatch.setattr(owner.dependencies, 'prepare', prepare)
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': True, 'status': 'removed'})
    return calls


async def start_prepared(app, identity='prepared'):
    return (await app.dispatch('workspace.environments.start', {**scope(app), 'profileId': 'python-project',
        'requestId': identity}, command_id='outer-'+identity))['result']


async def pending_preparation_cleanup(app, monkeypatch):
    """Reach the split commit through public actions, not fabricated success."""
    started = await start_prepared(app)
    stopped = await act(app, 'stop', started['resource'], 'stop')
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': False, 'status': 'unknown'})
    removed = await act(app, 'remove', stopped['resource'], 'remove')
    sid = scope(app)['sessionId']
    row = owner.get(app.data_dir, sid, started['resource']['id'])
    original = app.operations.requests.read(sid, 'remove')
    assert original == removed['request'] and original['state'] == 'outcome_unknown'
    assert row['status'] != 'retired' and row['guestRemoved'] and row['preparationPending']
    assert row['guestRemoval'] == original['guestRemoval']
    assert row['guestRemoval']['ok'] is True and row['guestRemoval']['status'] == 'removed'
    assert row['guestRemoval']['universeId'] == row['universeId']
    assert row['guestRemovalBinding'] == original['guestRemovalBinding'] == {
        'requestId': 'remove', 'resourceId': row['id'], 'sessionId': sid,
        'backendKey': row['backendKey'], 'backendIdentity': row['backendIdentity'],
        'universeId': row['universeId'], 'launchOperationId': row['launchOperationId'],
        'snapshot': row['snapshot'],
    }
    return row, original


async def test_shared_setup_uses_narrow_host_authority_and_passive_reconcile(app, monkeypatch):
    source = app.state['sessions'][0]
    setup = AsyncMock(return_value={'operation': {'state': 'accepted'}})
    reconcile = AsyncMock(return_value={'operation': {'state': 'outcome_unknown'}})
    monkeypatch.setattr(owner.setup, 'request', setup)
    monkeypatch.setattr(owner.setup, 'reconcile', reconcile)
    args = {**scope(app), 'requestId': 'setup', 'backend': 'docker'}
    with pytest.raises(AppError, match='authority is required'):
        await agent(app, source, 'workspace.environments.setup', args, 'blocked')
    await grant(app, ['test_environments'], ['session'])
    with pytest.raises(AppError, match='authority is required'):
        await agent(app, source, 'workspace.environments.setup', args, 'still-blocked')
    await grant(app, ['test_host_setup'], ['global'], identity='host-grant')
    assert (await agent(app, source, 'workspace.environments.setup', args, 'agent-setup'))['accepted']
    assert (await app.dispatch('workspace.environments.setup', {**args, 'requestId': 'user-setup'}))['accepted']
    assert setup.await_count == 2
    await agent(app, source, 'workspace.environments.setup.reconcile',
                {**scope(app), 'requestId': 'setup'}, 'read-setup')
    reconcile.assert_awaited_once()


async def test_preparation_is_bound_to_exact_snapshot_and_removed_after_guest(app, backend, prepared, monkeypatch):
    started = await start_prepared(app)
    assert started['request']['state'] == 'completed'
    assert prepared == [({'snapshot_commit': 'snapshot', 'archive_sha256': 'archive'}, 'python-project', 'prepared')]
    row = started['resource']
    stopped = await act(app, 'stop', row, 'stop')
    def cleanup(*args):
        assert backend[-1][0] == 'remove'
        return {'complete': True, 'status': 'removed'}
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    removed = await act(app, 'remove', stopped['resource'], 'remove')
    assert removed['resource']['status'] == 'removed'
    saved = owner.get(app.data_dir, scope(app)['sessionId'], row['id'])
    assert saved['status'] == 'retired' and not saved['preparationPending']


async def test_failed_preparation_stops_owned_builder_before_retiring(app, backend, prepared, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('Dependency download failed')
    cleanup = []
    monkeypatch.setattr(owner.dependencies, 'prepare', fail)
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: cleanup.append(True) or {'complete': True, 'status': 'removed'})
    result = await start_prepared(app)
    assert result['request']['state'] == 'failed' and cleanup == [True]
    assert result['request']['result']['message'] == 'Dependency download failed'
    assert [action for action, _ in backend] == ['prepare']
    assert not owner.get(app.data_dir, scope(app)['sessionId'], result['resource']['id'])['preparationPending']


async def test_unknown_preparation_cleanup_keeps_owner_and_recovery(app, backend, prepared, monkeypatch):
    started = await start_prepared(app)
    stopped = await act(app, 'stop', started['resource'], 'stop')
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': False, 'status': 'unknown'})
    removed = await act(app, 'remove', stopped['resource'], 'remove')
    saved = owner.get(app.data_dir, scope(app)['sessionId'], started['resource']['id'])
    assert removed['request']['state'] == 'outcome_unknown'
    assert saved['status'] != 'retired' and saved['guestRemoved'] and saved['preparationPending']
    sid = scope(app)['sessionId']
    original = app.operations.requests.read(sid, 'remove')
    cleanup = Mock(return_value={'complete': True, 'status': 'removed'})
    invoke = Mock(wraps=owner.invoke)
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    monkeypatch.setattr(owner, 'invoke', invoke)
    recovered = await act(app, 'remove', removed['resource'], 'finish-images')
    assert recovered['resource']['status'] == 'removed'
    assert [action for action, _ in backend].count('remove') == 1
    assert recovered['preparationRecovery'] is True
    assert recovered['request']['requestId'] == 'remove'
    assert recovered['request']['state'] == 'completed'
    assert recovered['request'] == app.operations.requests.read(sid, 'remove')
    for key in ('requestId', 'action', 'createdAt', 'resourceId', 'guestRemoval', 'guestRemovalBinding'):
        assert recovered['request'][key] == original[key]
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(sid, 'finish-images')
    fixed = owner.get(app.data_dir, sid, started['resource']['id'])
    assert fixed['status'] == 'retired' and not fixed['preparationPending']
    cleanup.assert_called_once()
    invoke.assert_not_called()


async def test_restart_after_guest_removal_finishes_only_preparation_cleanup(app, backend, prepared, monkeypatch):
    started = await start_prepared(app)
    stopped = await act(app, 'stop', started['resource'], 'stop')
    original = owner.save
    def crash(home, row, **changes):
        updated = original(home, row, **changes)
        if changes.get('guestRemoved') and 'guestRemoval' in changes:
            raise OSError('host disappeared after guest removal commit')
        return updated
    monkeypatch.setattr(owner, 'save', crash)
    interrupted = await act(app, 'remove', stopped['resource'], 'remove')
    assert interrupted['request']['state'] == 'outcome_unknown'
    monkeypatch.setattr(owner, 'save', original)
    sid = scope(app)['sessionId']
    row = owner.get(app.data_dir, sid, started['resource']['id'])
    config, _, _ = owner.configuration(app)
    fixed = await asyncio.to_thread(owner.resume_preparation_removal, app, row, config)
    assert fixed['status'] == 'retired' and not fixed['preparationPending']
    assert app.operations.requests.read(sid, 'remove')['state'] == 'completed'
    assert [action for action, _ in backend].count('remove') == 1


async def test_finish_cleanup_public_action_completes_original_preparation_request(app, backend, prepared, monkeypatch):
    row, original = await pending_preparation_cleanup(app, monkeypatch)
    cleanup = Mock(return_value={'complete': True, 'status': 'removed'})
    invoke = Mock(wraps=owner.invoke)
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    monkeypatch.setattr(owner, 'invoke', invoke)
    recovered = await act(app, 'finishCleanup', row, 'finish-images', previousRequestId='remove')
    assert recovered['preparationRecovery'] is True
    assert recovered['resource']['status'] == 'removed'
    assert recovered['request']['requestId'] == 'remove'
    assert recovered['request']['state'] == 'completed'
    assert recovered['request'] == app.operations.requests.read(row['sessionId'], 'remove')
    for key in ('requestId', 'action', 'createdAt', 'resourceId', 'guestRemoval', 'guestRemovalBinding'):
        assert recovered['request'][key] == original[key]
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(row['sessionId'], 'finish-images')
    fixed = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert fixed['status'] == 'retired' and not fixed['preparationPending']
    cleanup.assert_called_once()
    invoke.assert_not_called()
    assert [action for action, _ in backend].count('remove') == 1


@pytest.mark.parametrize('change', [
    'boolean-only', 'missing-request', 'foreign-resource', 'backend-key',
    'backend-identity', 'stale-revision', 'altered-guest-result',
    'other-unbound-remove', 'other-unbound-finishCleanup', 'wrong-previous-request',
])
async def test_public_preparation_recovery_rejects_unbound_or_changed_proof(app, backend, prepared, monkeypatch, change):
    row, original = await pending_preparation_cleanup(app, monkeypatch)
    sid = row['sessionId']
    if change == 'boolean-only':
        row = owner.save(app.data_dir, row, guestRemoval=None, guestRemovalBinding=None)
    elif change == 'missing-request':
        row = owner.save(app.data_dir, row,
            guestRemovalBinding={**row['guestRemovalBinding'], 'requestId': 'missing-removal'})
    elif change == 'foreign-resource':
        row = owner.save(app.data_dir, row,
            guestRemovalBinding={**row['guestRemovalBinding'], 'resourceId': 'another-resource'})
    elif change == 'backend-key':
        row = owner.save(app.data_dir, row, backendKey='another-backend')
    elif change == 'backend-identity':
        row = owner.save(app.data_dir, row,
            backendIdentity={'backend': 'docker', 'daemon': 'another-daemon'})
    elif change == 'altered-guest-result':
        # Still reports success/removal: equality with the original evidence matters.
        row = owner.save(app.data_dir, row,
            guestRemoval={**row['guestRemoval'], 'message': 'Altered saved completion'})
    elif change.startswith('other-unbound-'):
        other, fresh = app.operations.requests.begin(sid,
            'workspace.environments.' + change.removeprefix('other-unbound-'),
            {'requestId': 'other-unbound-removal'}, {})
        assert fresh
        app.operations.requests.finish(other, 'outcome_unknown')
    before = app.operations.requests.list(sid)
    cleanup = Mock(return_value={'complete': True, 'status': 'removed'})
    invoke = Mock(wraps=owner.invoke)
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    monkeypatch.setattr(owner, 'invoke', invoke)
    action, extra = ('finishCleanup', {'previousRequestId': 'stop'}) if change == 'wrong-previous-request' else ('remove', {})
    expected = {**row, 'revision': row['revision'] - 1} if change == 'stale-revision' else row
    with pytest.raises(AppError):
        await act(app, action, expected, 'rejected-recovery', **extra)
    cleanup.assert_not_called()
    invoke.assert_not_called()
    assert [action for action, _ in backend].count('remove') == 1
    assert owner.get(app.data_dir, sid, row['id']) == row
    assert app.operations.requests.read(sid, 'remove') == original
    assert app.operations.requests.list(sid) == before
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(sid, 'rejected-recovery')


@pytest.mark.parametrize('change', ['moved', 'stopped'])
async def test_public_preparation_recovery_rechecks_owner_before_dependency_cleanup(app, backend, prepared, monkeypatch, change):
    row, original = await pending_preparation_cleanup(app, monkeypatch)
    source = app.state['sessions'][0]
    before = app.operations.requests.list(row['sessionId'])
    entered, release = threading.Event(), threading.Event()
    require_resolved = Mock(wraps=owner.require_resolved_removal)
    def waiting(current_app, current_row, exclude_request_id=None, legacy=None, fresh_cleanup=None):
        require_resolved(current_app, current_row, exclude_request_id, legacy, fresh_cleanup)
        assert require_resolved.call_args.args[4] is fresh_cleanup
        assert fresh_cleanup == original.get('cleanupRecoveryQualification')
        assert exclude_request_id == 'remove'
        entered.set()
        assert release.wait(5)
    cleanup = Mock(return_value={'complete': True, 'status': 'removed'})
    invoke = Mock(wraps=owner.invoke)
    monkeypatch.setattr(owner, 'require_resolved_removal', waiting)
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    monkeypatch.setattr(owner, 'invoke', invoke)
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'],
            'requestId': 'guarded-recovery', 'previousRequestId': 'remove'}
    task = asyncio.create_task(agent(app, source, 'workspace.environments.finishCleanup', args, 'guarded-recovery'))
    try:
        await until(lambda: entered.is_set() or task.done())
        if task.done():
            await task  # Surface setup/validator failure instead of a blind five-second wait.
            pytest.fail('Recovery completed before the held owner recheck')
        assert entered.is_set()
        if change == 'moved':
            source['workingDirectory'] = str(app.data_dir / 'moved-source')
        else:
            # Same Stop revision boundary used by the existing environment tests.
            source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
        release.set()
        with pytest.raises(AppError, match='moved or stopped'):
            await asyncio.wait_for(task, 5)
    finally:
        release.set()
        if not task.done():
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
    cleanup.assert_not_called()
    invoke.assert_not_called()
    require_resolved.assert_called_once()
    assert [action for action, _ in backend].count('remove') == 1
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == row
    assert app.operations.requests.read(row['sessionId'], 'remove') == original
    assert app.operations.requests.list(row['sessionId']) == before
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(row['sessionId'], 'guarded-recovery')


async def test_preparation_cleanup_commit_gap_settles_without_repeating_cleanup(app, backend, prepared, monkeypatch):
    row, original = await pending_preparation_cleanup(app, monkeypatch)
    cleanup = Mock(return_value={'complete': True, 'status': 'removed'})
    invoke = Mock(wraps=owner.invoke)
    finish = app.operations.requests.finish
    def fail_completion(record, state, **fields):
        if record['requestId'] == 'remove' and state == 'completed':
            raise OSError('synthetic completion commit interruption')
        return finish(record, state, **fields)
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    monkeypatch.setattr(owner, 'invoke', invoke)
    monkeypatch.setattr(app.operations.requests, 'finish', fail_completion)
    with pytest.raises(AppError, match='completion commit interruption'):
        await act(app, 'remove', row, 'finish-images')
    saved = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert not saved['preparationPending'] and saved['preparationCleanup']['complete']
    assert app.operations.requests.read(row['sessionId'], 'remove') == original
    monkeypatch.setattr(app.operations.requests, 'finish', finish)
    recovered = await act(app, 'remove', saved, 'settle-images')
    assert recovered['resource']['status'] == 'removed'
    assert recovered['request']['requestId'] == 'remove'
    assert recovered['request']['state'] == 'completed'
    cleanup.assert_called_once()
    invoke.assert_not_called()
    assert [action for action, _ in backend].count('remove') == 1
    for identity in ('finish-images', 'settle-images'):
        with pytest.raises(ValueError, match='not found'):
            app.operations.requests.read(row['sessionId'], identity)


async def test_never_launched_preparation_cleanup_recovers_bound_original(app, backend, prepared, monkeypatch):
    def fail_prepare(*args, **kwargs):
        raise ValueError('synthetic preparation failure before launch')
    monkeypatch.setattr(owner.dependencies, 'prepare', fail_prepare)
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': False, 'status': 'unknown'})
    started = await start_prepared(app)
    assert started['request']['result']['message'] == 'synthetic preparation failure before launch'
    removed = await act(app, 'remove', started['resource'], 'remove')
    assert removed['request']['state'] == 'failed'
    saved = owner.get(app.data_dir, scope(app)['sessionId'], started['resource']['id'])
    assert saved['guestRemoved'] and saved['preparationPending'] and not saved.get('backendAdmitted')
    cleanup = Mock(return_value={'complete': True, 'status': 'removed'})
    invoke = Mock(wraps=owner.invoke)
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    monkeypatch.setattr(owner, 'invoke', invoke)
    recovered = await act(app, 'finishCleanup', saved, 'finish-images', previousRequestId='remove')
    assert recovered['resource']['status'] == 'removed'
    assert recovered['request']['requestId'] == 'remove' and recovered['request']['state'] == 'completed'
    assert [action for action, _ in backend] == ['prepare']
    cleanup.assert_called_once()
    invoke.assert_not_called()
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(saved['sessionId'], 'finish-images')


async def test_long_preparation_does_not_hold_queue_admission(app, queue, backend, prepared, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = owner.dependencies.prepare
    def wait(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner.dependencies, 'prepare', wait)
    await submit(app, profileId='python-project', timeoutSeconds=1200)
    await queue.tick()
    assert await asyncio.to_thread(entered.wait, 5)
    assert not queue.admission.locked()
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
    release.set()
    await asyncio.wait_for(asyncio.gather(*queue.tasks.values()), 5)
    assert record(queue, app)['job']['status'] == 'cancelled'
    assert [action for action, _ in backend] == ['prepare']
