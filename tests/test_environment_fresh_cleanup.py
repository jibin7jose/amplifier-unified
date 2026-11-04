"""Fresh cleanup proves current ownership and quiescence, without rewriting history."""
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_fresh_cleanup as fresh
from amplifier_web import test_environment_worker as worker
from amplifier_web import test_environment_recovery as recovery
from amplifier_web.service import AppError
from test_configuration_authority import app
from test_environment_recovery import observation
from test_task_environments import act, backend, start
from test_test_environment_worker import library, payload as worker_payload


async def interrupted_cleanup(app, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'original-stop'))['resource']
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    raw = owner.save(app.data_dir, raw, backendType='docker', backendConfigRetained=True,
        backendInstallation='original-installation', snapshot={'snapshot_commit': 'a' * 40,
        'snapshot_tree': 'b' * 40, 'archive_sha256': hashlib.sha256(b'source').hexdigest()})
    directory = owner.storage.directory(app.data_dir, raw)
    (directory / 'source.tar').write_bytes(b'source')
    monkeypatch.setattr(owner, 'invoke', lambda *a: {'ok': False, 'status': 'unknown'})
    failed = await act(app, 'remove', owner.public(app.data_dir, raw), 'interrupted-remove')
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    raw = owner.save(app.data_dir, raw, environmentState='stopped')
    return raw, failed['request']


def local_machine(monkeypatch):
    monkeypatch.setattr(owner.host_resources, 'local_target', lambda *a: {
        'supported': True, 'machineId': 'owned-local-machine'})


def backend_cleanup(monkeypatch, library, app, *, wrong_owner=False):
    lib, calls = library
    invoked = []
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))

    def invoke(config, home, current, action, args):
        invoked.append(action)
        if action == 'reconcile':
            return observation(current, 'stopped', cleanupStoppedVerified=True, containerIds=['c' * 64],
                               **({'rootOwner': 'another-owner'} if wrong_owner else {}))
        assert action == 'finishCleanup'
        identity, backend = current['universeId'], current['backendIdentity']
        monkeypatch.setattr(worker, 'docker_backend', lambda *a: backend)
        monkeypatch.setattr(worker, 'docker_reconcile', lambda *a: {
            'status': 'absent' if any(c[0] == 'destroy' for c in calls) else 'stopped',
            'observation': {'ownership': 'matched', 'absenceConfirmed': any(c[0] == 'destroy' for c in calls)}})
        payload = {'action': action, 'root': str(owner.storage.directory(home, current)),
            'rootOwner': current['sessionId'], 'resourceId': current['id'],
            'resourceRevision': current['revision'], 'admissionRevision': args['expectedRevision'],
            'backendKey': current['backendKey'], 'snapshot': current['snapshot'],
            'operationId': current['launchOperationId'], 'universeId': identity,
            'expectedBackendIdentity': backend, 'requestId': args['requestId'],
            'source': current['source'], 'admissionRoot': str(home / 'owned-admission'),
            'effectAction': args.get('effectAction')}
        return worker.remove_owned(payload, identity, backend, {}, lib, RuntimeError, 'retained-launch')

    monkeypatch.setattr(owner, 'invoke', invoke)
    return invoked, calls


async def test_live_old_executor_blocks_then_fresh_cleanup_preserves_exports_and_unknown(
        app, backend, library, monkeypatch):
    raw, original = await interrupted_cleanup(app, monkeypatch)
    directory = owner.storage.directory(app.data_dir, raw)
    old_retained = directory / ('retained-' + hashlib.sha256(original['requestId'].encode()).hexdigest())
    old_retained.mkdir()
    (old_retained / 'partial-export.tar').write_bytes(b'original partial export')
    local_machine(monkeypatch)
    invoked, calls = backend_cleanup(monkeypatch, library, app)
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], cwd=directory)
    try:
        assert not fresh.local_executor_quiescent(directory)
        with pytest.raises(AppError, match='previous local cleanup process'):
            await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'blocked-by-executor',
                      previousRequestId=original['requestId'])
        assert invoked == [] and calls == []
        assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert fresh.local_executor_quiescent(directory)
    completed = await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'fresh-after-settlement',
                          previousRequestId=original['requestId'])
    assert completed['request']['state'] == 'completed', completed['request']
    assert completed['resource']['status'] == 'removed'
    assert completed['request']['cleanupRecoveryQualification'] == fresh.binding(raw, original)
    assert completed['request']['result']['retention']['verified'] is True
    assert [name for name, *_ in calls if name in {'export', 'destroy'}] == ['export', 'export', 'destroy']
    assert (directory / 'source.tar').read_bytes() == b'source'
    retained = [path for path in directory.glob('retained-*') if path != old_retained]
    assert len(retained) == 1
    assert {p.name for p in retained[0].glob('*.tar')} == {'workspace.tar', 'artifacts.tar'}
    assert (old_retained / 'partial-export.tar').read_bytes() == b'original partial export'
    assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original
    before = list(invoked), list(calls)
    duplicate = await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'fresh-after-settlement',
                          previousRequestId=original['requestId'])
    assert duplicate['request'] == completed['request']
    assert (invoked, calls) == before


async def test_fresh_cleanup_refuses_foreign_current_owner(app, backend, library, monkeypatch):
    raw, original = await interrupted_cleanup(app, monkeypatch)
    local_machine(monkeypatch)
    invoked, calls = backend_cleanup(monkeypatch, library, app, wrong_owner=True)
    with pytest.raises(AppError, match='does not match this environment owner'):
        await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'foreign-owner-refused',
                  previousRequestId=original['requestId'])
    assert invoked == ['reconcile'] and calls == []
    assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('preparation_pending', [False, True])
async def test_fresh_cleanup_rechecks_executor_at_effect_boundary(
        app, backend, library, monkeypatch, preparation_pending):
    raw, original = await interrupted_cleanup(app, monkeypatch)
    local_machine(monkeypatch)
    invoked, calls = backend_cleanup(monkeypatch, library, app)
    if preparation_pending:
        raw = owner.save(app.data_dir, raw, preparationPending=True)
    before = owner.get(app.data_dir, raw['sessionId'], raw['id'])
    checks = iter([True, True, False])
    monkeypatch.setattr(fresh, 'local_executor_quiescent', lambda *a: next(checks))
    failed = await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'executor-changed',
                       previousRequestId=original['requestId'])
    assert failed['request']['state'] == 'failed'
    assert failed['request']['result']['code'] == 'previous_cleanup_active'
    assert failed['request']['result']['effectAdmitted'] is False
    assert owner.get(app.data_dir, raw['sessionId'], raw['id']) == before
    assert invoked == ['reconcile'] and calls == []
    assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('change', ['remote-daemon', 'original-resource', 'missing-owner', 'missing-snapshot'])
async def test_uninspectable_or_unbound_old_cleanup_stays_held(app, backend, monkeypatch, change):
    raw, original = await interrupted_cleanup(app, monkeypatch)
    local_machine(monkeypatch)
    if change == 'remote-daemon':
        monkeypatch.setattr(owner.host_resources, 'local_target', lambda *a: {'supported': False})
    elif change == 'original-resource':
        original = app.operations.requests.finish(original, 'outcome_unknown', resourceId='another-resource')
    elif change == 'missing-owner':
        raw = owner.save(app.data_dir, raw, owner={'sessionId': raw['sessionId']})
    else:
        raw = owner.save(app.data_dir, raw, snapshot={})
    monkeypatch.setattr(owner, 'invoke', lambda *a: pytest.fail('unqualified backend probe/effect'))
    if change == 'original-resource':
        refused = await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'unqualified-cleanup',
                            previousRequestId=original['requestId'])
        assert refused['request']['state'] == 'failed'
    else:
        with pytest.raises(AppError):
            await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'unqualified-cleanup',
                      previousRequestId=original['requestId'])
    assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original


def test_executor_inspection_does_not_accept_unreadable_or_missing_process_state(tmp_path):
    directory = tmp_path / 'owner'; directory.mkdir()
    proc = tmp_path / 'proc'; proc.mkdir()
    child = proc / '123'; child.mkdir()
    (child / 'stat').write_text('malformed')
    assert not fresh.local_executor_quiescent(directory, proc=proc)


@pytest.mark.parametrize('command,quiescent', [
    (b'/usr/bin/gpg-agent\0--daemon\0', True),
    (b'/usr/bin/python3\0-I\0-B\0/owned/test_environment_worker.py\0', False),
    (b'/usr/bin/docker\0compose\0down\0', False),
    (b'', False),
])
def test_unreadable_executor_is_distinguished_from_protected_unrelated_service(
        tmp_path, monkeypatch, command, quiescent):
    directory = tmp_path / 'owner'; directory.mkdir()
    proc = tmp_path / 'proc'; proc.mkdir()
    child = proc / '123'; child.mkdir()
    (child / 'stat').write_text('123 (process) S ' + '0 ' * 18 + '42')
    (child / 'cmdline').write_bytes(command)

    def denied(*args):
        raise PermissionError('process cwd is protected')

    monkeypatch.setattr(fresh.os, 'readlink', denied)
    assert fresh.local_executor_quiescent(directory, proc=proc) is quiescent


async def test_fresh_cleanup_keeps_original_unknown_when_current_export_fails(
        app, backend, library, monkeypatch):
    raw, original = await interrupted_cleanup(app, monkeypatch)
    local_machine(monkeypatch)
    invoked, calls = backend_cleanup(monkeypatch, library, app)

    def export_failed(*args, **kwargs):
        calls.append(('failed-export', args, kwargs))
        raise RuntimeError('private export diagnostic')

    library[0].export_files = export_failed
    failed = await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'fresh-export-refused',
                       previousRequestId=original['requestId'])
    assert failed['request']['state'] == 'failed'
    assert failed['request']['result']['effectAdmitted'] is False
    assert not any(name == 'destroy' for name, *_ in calls)
    assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('changes,stopped', [
    ({}, True),
    ({'RemovalInProgress': True}, False),
    ({'Status': 'dead', 'Dead': True}, False),
    ({'Restarting': True}, False),
    ({'Running': True}, False),
    ({'Paused': True}, False),
    ({'Pid': 123}, False),
    ({'Status': 'created'}, False),
])
def test_fresh_cleanup_requires_native_exited_docker_state(tmp_path, monkeypatch, changes, stopped):
    value = worker_payload(tmp_path, 'reconcile')
    value['effectAction'] = fresh.KIND
    labels = {'com.docker.compose.project': value['universeId'],
              'io.amplifier.root-owner': value['rootOwner'],
              'io.amplifier.operation-id': value['operationId']}
    container = {'ID': 'c' * 64, 'State': 'exited', 'Labels': labels}

    def run(arguments, **kwargs):
        return SimpleNamespace(stdout=json.dumps(container) if 'ps' in arguments else '')

    state = {'Status': 'exited', 'Pid': 0, 'Running': False, 'Paused': False,
             'Restarting': False, 'Dead': False, **changes}
    monkeypatch.setattr(worker.subprocess, 'run', run)
    monkeypatch.setattr(worker, 'docker_json', lambda *a: state)
    monkeypatch.setattr(worker, 'settled', lambda *a: True)
    result = worker.docker_reconcile(value, value['universeId'], value['expectedBackendIdentity'], 'retained-launch')
    assert result['status'] == ('stopped' if stopped else 'unknown')
    assert result['observation']['cleanupStoppedVerified'] is stopped
    assert result['observation']['containerIds'] == ['c' * 64]


def test_changed_cwd_executor_is_detected_by_exact_managed_object(tmp_path):
    directory = tmp_path / 'owner'; directory.mkdir()
    identity = 'dtu-exact-cleanup-object-12345'
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', identity], cwd=tmp_path)
    try:
        assert not fresh.local_executor_quiescent(directory, (identity,))
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert fresh.local_executor_quiescent(directory, (identity,))


async def test_passive_unknown_receipt_points_to_new_cleanup_without_claiming_proof(app, backend, monkeypatch):
    raw, original = await interrupted_cleanup(app, monkeypatch)
    before = owner.get(app.data_dir, raw['sessionId'], raw['id'])
    monkeypatch.setattr(fresh, 'local_executor_quiescent', lambda *a: pytest.fail('passive executor scan'))
    monkeypatch.setattr(owner, 'invoke', lambda *a: pytest.fail('passive backend probe'))
    value = recovery.read_removal(app, {'workspaceId': raw['workspaceId'], 'sessionId': raw['sessionId'],
        'id': raw['id'], 'expectedRevision': raw['revision'], 'originalRequestId': original['requestId']}, 'ui', None)
    assert value['nextAction'] == 'workspace.environments.finishCleanup'
    assert value['original']['state'] == 'outcome_unknown'
    assert value['executor'] == 'unobserved' and value['physical'] == 'not_observed'
    assert value['continuationAvailable'] is False
    assert owner.get(app.data_dir, raw['sessionId'], raw['id']) == before
    assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('state,bound,allowed', [
    ('outcome_unknown', True, True),
    ('outcome_unknown', False, False),
    ('running', True, False),
    ('admitting', True, False),
])
async def test_fresh_proof_handles_repeated_unknowns_but_not_unbound_or_inflight_work(
        app, backend, library, monkeypatch, state, bound, allowed):
    raw, original = await interrupted_cleanup(app, monkeypatch)
    other, _ = app.operations.requests.begin(raw['sessionId'], 'workspace.environments.finishCleanup',
        {'requestId': 'another-interrupted-attempt'}, {'sessionId': raw['sessionId']})
    other = app.operations.requests.finish(other, state,
        **({'resourceId': raw['id']} if bound else {}), result={'ok': False, 'status': 'unknown'})
    local_machine(monkeypatch)
    invoked, calls = backend_cleanup(monkeypatch, library, app)
    if allowed:
        completed = await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'fresh-after-two-attempts',
                              previousRequestId=original['requestId'])
        assert completed['request']['state'] == 'completed'
        assert sum(name == 'destroy' for name, *_ in calls) == 1
    else:
        with pytest.raises(AppError, match='original removal remains unresolved'):
            await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'still-held',
                      previousRequestId=original['requestId'])
        assert not calls
    assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original
    assert app.operations.requests.read(raw['sessionId'], other['requestId']) == other


async def test_dependency_cleanup_continues_from_fresh_guest_receipt_without_new_teardown(
        app, backend, library, monkeypatch):
    raw, original = await interrupted_cleanup(app, monkeypatch)
    raw = owner.save(app.data_dir, raw, preparationPending=True)
    local_machine(monkeypatch)
    invoked, calls = backend_cleanup(monkeypatch, library, app)
    remaining = iter([{'complete': False}, {'complete': True}])
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: next(remaining))
    partial = await act(app, 'finishCleanup', owner.public(app.data_dir, raw), 'guest-cleaned',
                        previousRequestId=original['requestId'])
    assert partial['request']['state'] == 'outcome_unknown'
    assert owner.get(app.data_dir, raw['sessionId'], raw['id'])['guestRemoved'] is True
    before = list(invoked), list(calls)
    done = await act(app, 'finishCleanup', partial['resource'], 'finish-dependencies',
                     previousRequestId='guest-cleaned')
    assert done['request']['state'] == 'completed' and done['preparationRecovery'] is True
    assert (invoked, calls) == before
    assert app.operations.requests.read(raw['sessionId'], original['requestId']) == original
