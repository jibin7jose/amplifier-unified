"""Managed helper protocol, constrained profile and preservation boundaries."""
import json
import io
import tarfile
import hashlib
import os
import shlex
import subprocess
import time
from pathlib import Path
import sys
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_worker as worker
from amplifier_web import task_resources as storage
from amplifier_web import test_environment_profiles as profiles
from amplifier_web import test_job_guest as guest

real_docker_json = worker.docker_json
real_docker_owner = worker.docker_owner


class Value:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, mode=None):
        return self.__dict__.copy()


class Failure(Exception):
    code = 'fixture-failure'
    universe_id = 'owned'


@pytest.fixture
def library(monkeypatch):
    monkeypatch.setattr(worker, 'docker_capacity', lambda payload: {'available': True, 'scope': {'daemon': 'fixture-daemon'}})
    monkeypatch.setattr(worker, 'docker_json', lambda *args: {'ID': 'fixture-daemon'})
    monkeypatch.setattr(worker, 'docker_owner', lambda *args: None)
    calls = []
    def record(name, value):
        def call(*args, **kwargs):
            calls.append((name, args, kwargs))
            return value
        return call
    lib = SimpleNamespace(
        export_repository_snapshot=record('snapshot', Value(snapshot_commit='exact', snapshot_tree='tree', archive_sha256=hashlib.sha256(b'source').hexdigest())),
        launch_id=record('identity', 'owned'),
        launch=record('launch', Value(id='owned', state='running', services=[])),
        push_files=record('push', Value()),
        execute=record('execute', Value(stdout='ok', stderr='', exit_code=0)),
        status=record('status', Value(id='owned', state='stopped', services=[])),
        stop=record('stop', Value(complete=True, outcome='stopped')),
        export_files=None,
        destroy=record('destroy', Value(complete=True, outcome='removed')),
    )
    def export(identity, source, destination, **kwargs):
        calls.append(('export', (identity, source, destination), kwargs))
        with tarfile.open(destination, 'w') as archive:
            item = tarfile.TarInfo(source.strip('/')); item.type = tarfile.DIRTYPE; archive.addfile(item)
        return Value(id=identity, source=source, destination=str(destination), bytes=destination.stat().st_size, sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
    lib.export_files = export
    monkeypatch.setitem(sys.modules, 'digital_twin_universe', SimpleNamespace(lib=lib))
    monkeypatch.setitem(sys.modules, 'digital_twin_universe.schemas',
        SimpleNamespace(BackendConfig=lambda **args: args, DigitalTwinUniverseError=Failure))
    return lib, calls


def payload(tmp_path, action):
    (tmp_path/'source.tar').write_bytes(b'source')
    return {'rootOwner': 'parent-root', 'expectedBackendIdentity': {'backend': 'docker', 'daemon': 'fixture-daemon'}, 'action': action, 'root': str(tmp_path), 'source': '/approved/task/worktree',
            'resourceId': 'fixture-resource', 'resourceRevision': 9, 'admissionRevision': 8, 'backendKey': 'fixture-config-key',
            'snapshot': {'snapshot_commit': 'exact', 'snapshot_tree': 'tree', 'archive_sha256': hashlib.sha256(b'source').hexdigest()},
            'operationId': 'durable-launch', 'universeId': 'owned', 'dockerHost': 'unix:///approved/docker.sock',
            'image': owner.APPROVED_IMAGE, 'requestId': 'stable-request', 'timeoutSeconds': 12, 'command': 'python -V',
            'admissionRoot': str(tmp_path / 'admission')}


def test_fixed_profile_has_no_host_mount_ports_or_privilege_escalation():
    profile = worker.profile(owner.APPROVED_IMAGE)
    twin = profile['services']['twin']
    assert twin['volumes'] == ['workspace:/workspace', 'artifacts:/artifacts', 'control:/var/lib/unified-jobs']
    assert set(twin['cap_add']) == {'CHOWN', 'SETUID', 'SETGID', 'KILL'}
    assert 'ports' not in twin and 'privileged' not in twin and 'build' not in twin
    assert twin['read_only'] and twin['cap_drop'] == ['ALL']
    assert twin['security_opt'] == ['no-new-privileges:true']
    assert profile['networks'] == {'default': {'internal': True}}
    assert 'environment' not in twin and 'env_file' not in twin


def test_prepare_preserves_source_before_launch_and_uses_pure_identity(library, tmp_path):
    lib, calls = library
    result = worker.execute_call(payload(tmp_path, 'prepare'))
    assert result['universeId'] == 'owned' and result['snapshot']['snapshot_commit'] == 'exact'
    assert [name for name, _, _ in calls] == ['snapshot']
    assert json.loads((tmp_path / 'snapshot.json').read_text())['snapshot_tree'] == 'tree'


def test_start_copies_exact_snapshot_without_host_mount(library, tmp_path):
    lib, calls = library
    result = worker.execute_call(payload(tmp_path, 'start'))
    assert result['ok']
    launch = next(item for item in calls if item[0] == 'launch')
    assert launch[2]['operation_id'] == 'durable-launch'
    assert launch[2]['config']['state_root'] == tmp_path / 'backend'
    assert next(item for item in calls if item[0] == 'push')[1] == ('owned', tmp_path / 'source.tar', '/artifacts/.source.tar')
    assert next(item for item in calls if item[0] == 'execute')[2]['max_output_bytes'] == 32768
    assert "filter='data'" in shlex.split(next(item for item in calls if item[0] == 'execute')[1][1])[2]
    assert worker.source_ready(tmp_path, 'owned', payload(tmp_path, 'read')['snapshot'])


def test_failed_unpack_never_promotes_running_container_to_ready(library, tmp_path):
    lib, calls = library
    executions = []
    def unpack(*args, **kwargs):
        executions.append(args[1])
        return Value(stdout='', stderr='outside symlink refused', exit_code=1)
    lib.execute = unpack
    lib.status = lambda *a, **k: Value(state='running', services=[])
    assert not worker.execute_call(payload(tmp_path, 'start'))['ok']
    observed = worker.execute_call(payload(tmp_path, 'read'))
    assert observed['status'] == 'unknown' and not observed['sourceReady']
    assert not worker.execute_call(payload(tmp_path, 'execute'))['ok']
    assert len(executions) == 1  # Only the original unpack attempt, never a check or replay.
    assert not (tmp_path / 'source-ready.json').exists()


def test_helper_loss_after_launch_cannot_enable_checks_or_reinstall_source(library, tmp_path):
    lib, calls = library
    def lost(*args, **kwargs):
        raise Failure()
    lib.push_files = lost
    lib.status = lambda *a, **k: Value(state='running', services=[])
    assert not worker.execute_call(payload(tmp_path, 'start'))['ok']
    assert worker.execute_call(payload(tmp_path, 'read'))['status'] == 'unknown'
    assert not worker.execute_call(payload(tmp_path, 'execute'))['ok']
    assert not any(name == 'execute' for name, _, _ in calls)


@pytest.mark.parametrize('change', ['snapshot', 'universe', 'missing'])
def test_readiness_receipt_requires_exact_snapshot_and_universe(library, tmp_path, change):
    lib, calls = library
    assert worker.execute_call(payload(tmp_path, 'start'))['ok']
    lib.status = lambda *a, **k: Value(state='running', services=[])
    altered = payload(tmp_path, 'read')
    if change == 'snapshot':
        altered['snapshot']['archive_sha256'] = 'another-digest'
    elif change == 'universe':
        altered['universeId'] = 'another-universe'
    else:
        (tmp_path / 'source-ready.json').unlink()
    assert worker.execute_call(altered)['status'] == 'unknown'
    assert not worker.execute_call({**altered, 'action': 'execute'})['ok']
    assert sum(name == 'execute' for name, _, _ in calls) == 2


def test_confirmed_source_survives_lost_host_response_without_reextraction(library, tmp_path):
    lib, calls = library
    worker.execute_call(payload(tmp_path, 'start'))  # Simulate a lost response after the durable source receipt.
    lib.status = lambda *a, **k: Value(state='running', services=[])
    observed = worker.execute_call(payload(tmp_path, 'read'))
    assert observed['status'] == 'running' and observed['sourceReady']
    assert sum(name == 'execute' for name, _, _ in calls) == 2


def test_partial_source_can_still_be_stopped_and_preserved(library, tmp_path):
    lib, calls = library
    assert not (tmp_path / 'source-ready.json').exists()
    assert worker.execute_call(payload(tmp_path, 'stop'))['status'] == 'stopped'
    observed = worker.execute_call(payload(tmp_path, 'read'))
    assert observed['status'] == 'stopped' and not observed['sourceReady']
    assert worker.execute_call(payload(tmp_path, 'remove'))['status'] == 'removed'


@pytest.mark.parametrize('failure_at', [1, 2])
def test_uncertain_export_never_destroys_environment(library, tmp_path, failure_at):
    lib, calls = library
    exports = []
    def export(*args, **kwargs):
        exports.append(args[1])
        if len(exports) == failure_at:
            raise Failure()
        return Value(bytes=100)
    lib.export_files = export
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert not result['ok'] and not any(name == 'destroy' for name, _, _ in calls)
    assert exports == ['/workspace', '/artifacts'][:failure_at]


def test_remove_exports_both_roots_before_destroy_and_retains_manifest(library, tmp_path):
    lib, calls = library
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['ok'] and result['status'] == 'removed'
    assert [name for name, _, _ in calls] == ['status', 'export', 'export', 'destroy']
    assert len(list(tmp_path.glob('retained-*/manifest.json'))) == 1


def test_retention_persistence_failure_never_destroys(library, tmp_path, monkeypatch):
    lib, calls = library
    def fail(directory, archives):
        raise OSError('cannot persist archive receipt')
    monkeypatch.setattr(worker, 'retain_manifest', fail)
    with pytest.raises(worker.RemovalFailure) as failure:
        worker.execute_call(payload(tmp_path, 'remove'))
    result = failure.value.receipt
    assert result['status'] == 'unknown'
    assert result['removal'] == {
        'version': 1, 'phase': 'publish-manifest', 'code': 'phase_failed', 'destroyEffect': 'not_dispatched'}
    assert [name for name, _, _ in calls] == ['status', 'export', 'export']


def test_remove_never_stops_or_removes_running_environment(library, tmp_path):
    lib, calls = library
    lib.status = lambda *a, **k: Value(state='running', services=[])
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert not result['ok'] and calls == []


def test_finish_cleanup_never_stops_or_removes_running_environment(library, tmp_path):
    lib, calls = library
    lib.status = lambda *a, **k: Value(state='running', services=[])
    result = worker.execute_call(payload(tmp_path, 'finishCleanup'))
    assert not result['ok'] and calls == []


def test_incomplete_stop_is_never_labeled_stopped(library, tmp_path):
    lib, calls = library
    lib.stop = lambda *a, **k: Value(complete=False, outcome='unknown')
    result = worker.execute_call(payload(tmp_path, 'stop'))
    assert not result['ok'] and result['status'] == 'unknown'


def allocated(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    row = owner.prepare(home, 'root', 'workspace', {'sessionId': 'root'}, '/source', 'key', 'request')
    return home, storage.allocate(home, row)


def test_real_helper_subprocess_does_not_inherit_host_secrets(tmp_path, monkeypatch):
    home, row = allocated(tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'never-forward-this')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'never-forward-this-either')
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'test_environment_worker.py').write_text(
        "import json,os,sys\njson.load(sys.stdin)\nprint(json.dumps({'ok':True,'leaked':any(key in os.environ for key in ['OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY','PYTHONPATH'])}))\n")
    value = owner.invoke({'python': sys.executable, 'image': owner.APPROVED_IMAGE, 'dockerHost': 'unix:///socket'}, home, row, 'read', {})
    assert value == {'ok': True, 'leaked': False}


def test_real_helper_output_is_bounded_and_stopped_on_overflow(tmp_path, monkeypatch):
    home, row = allocated(tmp_path)
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'test_environment_worker.py').write_text("import sys\nsys.stdout.write('x' * 1048576)\nsys.stdout.flush()\n")
    with pytest.raises(ValueError, match='exceeded its bound'):
        owner.invoke({'python': sys.executable, 'image': owner.APPROVED_IMAGE, 'dockerHost': 'unix:///socket'}, home, row, 'read', {})


def test_incus_helper_selection_preserves_exact_root_and_request(tmp_path, monkeypatch):
    home, row = allocated(tmp_path)
    monkeypatch.setattr(owner, '__file__', str(tmp_path/'owner.py'))
    (tmp_path/'test_environment_worker.py').write_text("raise RuntimeError('wrong backend')\n")
    (tmp_path/'incus_environment_worker.py').write_text(
        "import json,sys\np=json.load(sys.stdin)\nprint(json.dumps({'ok':True,'owner':p['rootOwner'],'incus':p['incus'],'request':p['requestId']}))\n")
    value = owner.invoke({'python': sys.executable, 'backend': 'incus', 'incus': {'project':'owned'}},
        home, row, 'read', {'requestId':'same-request'})
    assert value == {'ok':True, 'owner':'root', 'incus':{'project':'owned'}, 'request':'same-request'}


def test_docker_full_retention_is_rejected_without_export(library, tmp_path):
    lib, calls = library
    result = worker.execute_call({**payload(tmp_path, 'remove'), 'retentionMode': 'full'})
    assert result['code'] == 'retention_unsupported'
    assert not any(name in {'export', 'destroy'} for name, _, _ in calls)


def test_artifact_recovery_exports_fixed_selection_without_other_effects(library, tmp_path):
    lib, calls = library
    result = worker.execute_call(payload(tmp_path, 'exportArtifacts'))
    assert result['ok'] and result['status'] == 'stopped'
    assert result['artifactExport']['selection'] == ['/artifacts']
    assert [name for name, _, _ in calls] == ['status', 'export']
    assert calls[-1][1][1] == '/artifacts'
    assert len(list(tmp_path.glob('artifacts-*/manifest.json'))) == 1
    assert not list(tmp_path.glob('retained-*'))


def test_artifact_recovery_refuses_running_or_foreign_backend(library, tmp_path):
    lib, calls = library
    lib.status = lambda *a, **k: Value(state='running', services=[])
    result = worker.execute_call(payload(tmp_path, 'exportArtifacts'))
    assert not result['ok'] and result['effectAdmitted'] is False
    assert not calls
    value = payload(tmp_path, 'exportArtifacts')
    value['expectedBackendIdentity']['daemon'] = 'replacement'
    assert worker.execute_call(value)['code'] == 'backend_unverified'
    assert not calls


@pytest.mark.parametrize('has_settlement,has_pending,confirmed', [(False, False, False), (True, True, False), (True, False, True)])
def test_docker_reconcile_does_not_manufacture_absence(library, tmp_path, monkeypatch, has_settlement, has_pending, confirmed):
    value = payload(tmp_path, 'reconcile')
    backend = value['expectedBackendIdentity']
    if has_settlement:
        worker.retain_settlement(value, 'owned', backend, 'launched')
    if has_pending:
        worker.reserve_admission(value, {'scope': {'daemon': backend['daemon']}})
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=''))
    result = worker.execute_call(value)
    assert result['observation']['absenceConfirmed'] is confirmed
    assert result['status'] == ('absent' if confirmed else 'unknown')
    if has_pending:
        assert len(list((tmp_path/'admission').glob('*/*.json'))) == 1


def test_docker_reconcile_wrong_owner_does_not_adopt(library, tmp_path, monkeypatch):
    value = payload(tmp_path, 'reconcile')
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps({'State':'running', 'Labels':'com.docker.compose.project=owned,io.amplifier.root-owner=someone-else,io.amplifier.operation-id=durable-launch'})))
    with pytest.raises(ValueError, match='owner'):
        worker.execute_call(value)


def test_prepare_retains_backend_identity_and_rejects_another_daemon(library, tmp_path):
    value = payload(tmp_path, 'prepare')
    result = worker.execute_call(value)
    assert result['backendIdentity'] == value['expectedBackendIdentity']
    value['action'] = 'reconcile'
    value['expectedBackendIdentity'] = {'backend': 'docker', 'daemon': 'another'}
    result = worker.execute_call(value)
    assert result['code'] == 'backend_unverified'
    assert not result['observation']['absenceConfirmed']


def test_profile_binds_all_project_resources_to_root_and_operation(tmp_path):
    value = payload(tmp_path, 'start')
    profile = worker.profile(owner.APPROVED_IMAGE, value)
    labels = {'io.amplifier.root-owner':'parent-root', 'io.amplifier.operation-id':'durable-launch'}
    assert all(item['labels'] == labels for item in [profile['services']['twin'], *profile['volumes'].values(), *profile['networks'].values()])


def test_reconcile_refuses_foreign_project_volume(library, tmp_path, monkeypatch):
    value = payload(tmp_path, 'reconcile')
    def query(args, **kwargs):
        labels = 'com.docker.compose.project=owned,io.amplifier.root-owner=parent-root,io.amplifier.operation-id=durable-launch'
        if 'volume' in args:
            labels = labels.replace('parent-root', 'foreign-root')
        return SimpleNamespace(stdout=json.dumps({'State':'exited','Labels':labels}))
    monkeypatch.setattr(worker.subprocess, 'run', query)
    with pytest.raises(ValueError, match='owner'):
        worker.execute_call(value)


def test_admission_rechecks_actual_daemon_before_launch(library, tmp_path, monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def admission(payload):
        yield {'available':True,'scope':{'daemon':'replacement'}}
    monkeypatch.setattr(worker, 'docker_admission', admission)
    result = worker.execute_call(payload(tmp_path, 'start'))
    assert result['code'] == 'backend_changed' and result['effectAdmitted'] is False
    assert not any(name == 'launch' for name, _, _ in library[1])


def test_partial_proof_write_never_publishes_final_marker(tmp_path,monkeypatch):
    path=tmp_path/'backend-launched.json'
    def partial(value,stream):
        stream.write('{"state":')
        stream.flush()
        raise OSError('interrupted write')
    monkeypatch.setattr(worker.json,'dump',partial)
    with pytest.raises(OSError):
        worker.write_private_record(path,{'state':'launched'})
    assert not path.exists() and not list(tmp_path.glob('.backend-proof-*'))


def test_private_proof_publication_never_replaces_existing(tmp_path):
    path=tmp_path/'proof.json'
    worker.write_private_record(path,{'exact':1})
    with pytest.raises(FileExistsError):
        worker.write_private_record(path,{'exact':2})
    assert worker.read_private_record(path)=={'exact':1}
    assert not list(tmp_path.glob('.backend-proof-*'))


@pytest.mark.parametrize('action', ['read','stop','remove'])
def test_present_owner_cleanup_survives_corrupt_settlement(library,tmp_path,monkeypatch,action):
    value=payload(tmp_path,action)
    path=tmp_path/'backend-launched.json';path.write_text('{"partial":');path.chmod(0o600)
    def actual_owner(payload,identity,backend,source):
        result=worker.docker_reconcile(payload,identity,backend,source)
        assert result['observation']['ownership']=='matched'
        assert result['observation']['admissionSettled'] is False
    monkeypatch.setattr(worker,'docker_owner',actual_owner)
    labels='com.docker.compose.project=owned,io.amplifier.root-owner=parent-root,io.amplifier.operation-id=durable-launch'
    def query(args,**kwargs):
        return SimpleNamespace(stdout=json.dumps({'State':'exited','Labels':labels}) if 'ps' in args else '')
    monkeypatch.setattr(worker.subprocess,'run',query)
    result=worker.execute_call(value)
    assert result['ok'] and result['status'] in {'stopped','removed'}
    assert path.read_text()=='{"partial":'


def test_corrupt_settlement_never_proves_absence(library,tmp_path,monkeypatch):
    value=payload(tmp_path,'reconcile')
    path=tmp_path/'backend-launched.json';path.write_text('{');path.chmod(0o600)
    monkeypatch.setattr(worker.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=''))
    result=worker.execute_call(value)
    assert result['status']=='unknown' and not result['observation']['absenceConfirmed']


def removal_main(monkeypatch, value):
    """Exercise the actual sanitized JSON boundary, not a fixture error adapter."""
    output = io.StringIO()
    with monkeypatch.context() as streams:
        streams.setattr(worker.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(value).encode())))
        streams.setattr(worker.sys, 'stdout', output)
        worker.main()
    return json.loads(output.getvalue())


@pytest.mark.parametrize('fault,phase,effect', [
    ('workspace-export', 'export-workspace', 'not_dispatched'),
    ('artifacts-export', 'export-artifacts', 'not_dispatched'),
    ('archive-validation', 'verify-exports', 'not_dispatched'),
    ('retention-publication', 'verify-exports', 'not_dispatched'),
    ('manifest-publication', 'publish-manifest', 'not_dispatched'),
    ('destroy-acknowledgement', 'destroy', 'unknown'),
    ('settlement-publication', 'settle', 'confirmed'),
])
def test_removal_boundary_reports_bounded_phase_not_exception_text(library, tmp_path, monkeypatch, fault, phase, effect):
    """Proposed receipt fields are deliberately absent in the frozen baseline."""
    lib, calls = library
    value = payload(tmp_path, 'remove')
    marker = 'secret-fixture-token=/private/fixture/settings.yaml'
    original_export = lib.export_files
    original_dump = worker.json.dump
    fired, destroyed = [], []

    def export(identity, source, destination, **kwargs):
        if fault == source.strip('/') + '-export':
            destination.write_bytes(b'partial export')
            fired.append(fault)
            raise OSError(marker)
        receipt = original_export(identity, source, destination, **kwargs)
        if fault == 'archive-validation' and source == '/artifacts':
            destination.write_bytes(b'invalid archive')
            receipt.bytes = destination.stat().st_size
            receipt.sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
            fired.append(fault)  # Hash matches; real archive structure must fail.
        return receipt

    def dump(data, stream, *args, **kwargs):
        is_retention = isinstance(data, dict) and 'archiveBytes' in data
        is_manifest = isinstance(data, list)
        is_settlement = isinstance(data, dict) and data.get('state') == 'removed'
        if ((fault == 'retention-publication' and is_retention)
                or (fault == 'manifest-publication' and is_manifest)
                or (fault == 'settlement-publication' and is_settlement)):
            fired.append(fault)
            stream.write('{"partial":')
            stream.flush()
            raise OSError(marker)
        return original_dump(data, stream, *args, **kwargs)

    def destroy(*args, **kwargs):
        destroyed.append(args[0])
        if fault == 'destroy-acknowledgement':
            fired.append(fault)
            error = Failure(marker)
            error.code = marker  # Backend-defined codes are not a safe enum.
            raise error
        return Value(complete=True, outcome='removed')

    lib.export_files, lib.destroy = export, destroy
    monkeypatch.setattr(worker.json, 'dump', dump)
    result = removal_main(monkeypatch, value)
    assert fired == [fault], 'The intended boundary must actually be reached'
    assert not result['ok']
    if fault == 'archive-validation':
        assert result['status'] == 'stopped' and result['effectAdmitted'] is False
        assert result['phase'] == 'verifying' and result['code'] == 'archive_verification_failed'
    else:
        assert result['status'] == 'unknown'
    assert destroyed == (['owned'] if effect != 'not_dispatched' else [])
    serialized = json.dumps(result)
    assert marker not in serialized and str(tmp_path) not in serialized
    assert len(serialized) < 4096
    assert result.get('removal') == {
        'version': 1, 'phase': phase, 'code': 'phase_failed', 'destroyEffect': effect}
    # A diagnostic describes future execution; it is not a retrospective
    # authority receipt permitting continuation of an older UNKNOWN.


def test_destroy_sees_verified_manifest_and_original_export_bytes(library, tmp_path):
    lib, calls = library
    seen = []
    def destroy(identity, **kwargs):
        directory, = tmp_path.glob('retained-*')
        manifest = json.loads((directory / 'manifest.json').read_text())
        accounting = json.loads((directory / 'retention.json').read_text())
        assert identity == 'owned' and accounting['verified']
        assert {item['source'] for item in manifest} == {'/workspace', '/artifacts'}
        for item in manifest:
            raw = Path(item['destination']).read_bytes()
            assert item['id'] == identity and item['verified']
            assert item['sha256'] == hashlib.sha256(raw).hexdigest()
            assert item['bytes'] == len(raw)
            worker.retention.inspect_archive(item['destination'], item['source'].strip('/'))
        seen.append(manifest)
        return Value(complete=True, outcome='removed')
    lib.destroy = destroy
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['ok'] and len(seen) == 1
    assert [name for name, _, _ in calls] == ['status', 'export', 'export']


def test_destroy_dispatch_has_private_bound_intent_before_backend_call(library, tmp_path):
    """Proposed durable intent is a no-replay fence, never proof of completion."""
    value = {**payload(tmp_path, 'remove'), 'resourceId':'fixture-resource',
        'resourceRevision':9, 'admissionRevision':8, 'backendKey':'fixture-config-key'}
    seen = []
    def destroy(identity, **kwargs):
        directory, = tmp_path.glob('retained-*')
        proof = worker.read_private_record(directory / 'destroy-dispatched.json')
        assert proof is not None, 'Destroy must not start before its private intent is durable'
        assert proof['version'] == 1 and proof['phase'] == 'destroy-dispatched'
        assert proof['binding'] == {
            'rootOwner':value['rootOwner'], 'resourceId':value['resourceId'],
            'resourceRevision':9, 'admissionRevision':8, 'requestId':value['requestId'],
            'operationId':value['operationId'], 'universeId':identity,
            'backendKey':value['backendKey'], 'backendIdentity':value['expectedBackendIdentity'],
            'snapshot':value['snapshot']}
        for name in ('manifest', 'retention'):
            assert proof[name + 'Sha256'] == hashlib.sha256(
                (directory / (name + '.json')).read_bytes()).hexdigest()
        manifest = json.loads((directory / 'manifest.json').read_text())
        assert proof['archives'] == [
            {key:item[key] for key in ('source', 'sha256', 'bytes')} for item in manifest]
        seen.append(proof)
        return Value(complete=True, outcome='removed')
    library[0].destroy = destroy
    assert worker.execute_call(value)['ok'] and len(seen) == 1


@pytest.mark.parametrize('filename', ['manifest.json', 'retention.json'])
def test_interrupted_retention_publication_has_no_complete_filename(library, tmp_path, monkeypatch, filename):
    """A prefix in the final filename is not an atomic complete receipt."""
    value = payload(tmp_path, 'remove')
    original = worker.json.dump
    injected = []
    def interrupted(data, stream, *args, **kwargs):
        selected = (isinstance(data, list) if filename == 'manifest.json'
                    else isinstance(data, dict) and 'archiveBytes' in data)
        if selected:
            injected.append(True)
            stream.write('{"partial":')
            stream.flush()
            raise OSError('synthetic interrupted publication')
        return original(data, stream, *args, **kwargs)
    monkeypatch.setattr(worker.json, 'dump', interrupted)
    result = removal_main(monkeypatch, value)
    directory, = tmp_path.glob('retained-*')
    assert injected == [True] and not result['ok']
    assert not any(name == 'destroy' for name, _, _ in library[1])
    assert sorted(p.name for p in directory.glob('*.tar')) == ['artifacts.tar', 'workspace.tar']
    assert not (directory / filename).exists(), 'Incomplete publication escaped under its final name'


@pytest.mark.parametrize('field,replacement', [
    ('rootOwner', 'foreign-root'), ('operationId', 'foreign-launch'),
    ('universeId', 'foreign-universe'),
    ('expectedBackendIdentity', {'backend':'docker', 'daemon':'foreign-daemon'}),
])
def test_removal_rechecks_original_private_backend_binding_before_exports(library, tmp_path, field, replacement):
    value = payload(tmp_path, 'prepare')
    assert worker.execute_call(value)['ok']
    library[1].clear()
    result = worker.execute_call({**value, 'action':'remove', field:replacement})
    assert result['code'] == 'backend_unverified'
    assert not library[1] and not list(tmp_path.glob('retained-*'))


def test_diagnostic_serialization_failure_keeps_removal_unknown_without_secret(library, tmp_path, monkeypatch):
    """Diagnostics must not make a failed remove unparseable or disclose text."""
    value = payload(tmp_path, 'remove')
    marker = 'private-diagnostic-fixture'
    def fail(*args, **kwargs):
        raise OSError(marker)
    library[0].export_files = fail
    original, intercepted = worker.json.dumps, []
    def cannot_serialize_diagnostic(data, *args, **kwargs):
        if isinstance(data, dict) and 'removal' in data:
            intercepted.append(True)
            raise ValueError(marker)
        return original(data, *args, **kwargs)
    monkeypatch.setattr(worker.json, 'dumps', cannot_serialize_diagnostic)
    result = removal_main(monkeypatch, value)
    assert intercepted == [True], 'The new bounded diagnostic must be exercised'
    assert not result['ok'] and result['status'] == 'unknown'
    assert marker not in original(result)
    assert not any(name == 'destroy' for name, _, _ in library[1])


def test_removal_helper_binds_resource_revision_and_original_request(tmp_path, monkeypatch):
    home, row = allocated(tmp_path)
    row.update(universeId='owned', backendIdentity={'backend':'docker', 'daemon':'original-daemon'})
    admission_revision = row['revision']
    row = {**row, 'revision':admission_revision + 1}  # effect() saved pending state.
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'test_environment_worker.py').write_text(
        "import json,sys\np=json.load(sys.stdin)\n"
        "keys=('rootOwner','resourceId','resourceRevision','admissionRevision',"
        "'operationId','universeId','expectedBackendIdentity','backendKey','requestId')\n"
        "print(json.dumps({'ok':True,'binding':{k:p.get(k) for k in keys}}))\n")
    # This child echoes the real host payload only; it creates no phase proof.
    result = owner.invoke({'python':sys.executable, 'dockerHost':'unix:///socket'},
        home, row, 'remove', {'requestId':'original-remove', 'expectedRevision':admission_revision})
    assert result['binding'] == {
        'rootOwner':row['sessionId'], 'resourceId':row['id'], 'resourceRevision':row['revision'],
        'admissionRevision':admission_revision, 'operationId':row['launchOperationId'],
        'universeId':'owned', 'expectedBackendIdentity':row['backendIdentity'],
        'backendKey':row['backendKey'], 'requestId':'original-remove'}


@pytest.mark.parametrize('evidence', ['missing', 'forged', 'stale', 'destroy-dispatched'])
def test_original_removal_inspection_never_continues_without_complete_bound_evidence(library, tmp_path, monkeypatch, evidence):
    """Proposed read-only consumer; absence must fail, not skip or fake success."""
    inspect = getattr(worker, 'inspect_removal', None)
    assert callable(inspect), 'Missing owner-bound read-only removal evidence inspection'
    value = {**payload(tmp_path, 'remove'), 'resourceId':'fixture-resource',
        'resourceRevision':9, 'admissionRevision':8, 'backendKey':'fixture-config-key'}
    directory = tmp_path / ('retained-' + hashlib.sha256(value['requestId'].encode()).hexdigest())
    if evidence == 'destroy-dispatched':
        def lost(*args, **kwargs):
            raise Failure('synthetic lost destroy acknowledgement')
        library[0].destroy = lost
        assert not worker.execute_call(value)['ok']
        assert worker.read_private_record(directory / 'destroy-dispatched.json') is not None
    elif evidence != 'missing':
        directory.mkdir(mode=0o700)
        # Private file shape and plausible hashes alone do not establish the
        # original phase chain, export bytes, or exact request binding.
        worker.write_private_record(directory / 'destroy-dispatched.json', {
            'version':1, 'phase':'manifest-complete', 'destroyEffect':'not_dispatched',
            'requestId':value['requestId'] if evidence == 'forged' else 'stale-request',
            'manifestSha256':'a' * 64, 'retentionSha256':'b' * 64})
    before = {str(p.relative_to(tmp_path)):(p.read_bytes(), p.stat().st_mtime_ns)
              for p in tmp_path.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only removal inspection contacted an effectful backend')
    for name in ('export_files', 'destroy', 'stop', 'launch', 'execute'):
        monkeypatch.setattr(library[0], name, forbidden)
    result = inspect(value)
    assert result == {
        'version':1, 'status':'held', 'continuationAvailable':False,
        'code':'destroy_outcome_unknown' if evidence == 'destroy-dispatched'
               else 'removal_evidence_unverified'}
    assert before == {str(p.relative_to(tmp_path)):(p.read_bytes(), p.stat().st_mtime_ns)
                      for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('change', ['none', 'root', 'resource', 'revision', 'request', 'backend',
                                  'snapshot', 'manifest', 'archive', 'intent', 'symlink'])
def test_produced_removal_chain_inspection_is_exact_and_never_authorizes_replay(library, tmp_path, monkeypatch, change):
    value = payload(tmp_path, 'remove')
    def lost(*args, **kwargs):
        raise Failure('synthetic acknowledgement loss')
    library[0].destroy = lost
    assert not worker.execute_call(value)['ok']
    directory, = tmp_path.glob('retained-*')
    assert worker.read_private_record(directory / 'destroy-dispatched.json')['binding']['resourceId'] == value['resourceId']
    inspected = dict(value)
    if change in {'root', 'resource', 'revision', 'request', 'backend', 'snapshot'}:
        field, replacement = {
            'root':('rootOwner','other-root'), 'resource':('resourceId','other-resource'),
            'revision':('resourceRevision',10), 'request':('requestId','other-remove'),
            'backend':('expectedBackendIdentity',{'backend':'docker','daemon':'other-daemon'}),
            'snapshot':('snapshot',{'archive_sha256':'other-source'}),
        }[change]
        inspected[field] = replacement
    elif change in {'manifest', 'intent'}:
        name = 'manifest.json' if change == 'manifest' else 'destroy-dispatched.json'
        with (directory / name).open('ab') as stream:
            stream.write(b'changed')
    elif change == 'archive':
        with (directory / 'workspace.tar').open('ab') as stream:
            stream.write(b'changed')
    elif change == 'symlink':
        original = directory / 'destroy-dispatched.json'
        saved = directory / 'kept-intent.json'
        original.rename(saved)
        original.symlink_to(saved.name)
    before = {str(p.relative_to(tmp_path)):(p.read_bytes(), p.stat().st_mtime_ns)
              for p in tmp_path.rglob('*') if p.is_file()}
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('Inspection called a backend')
    for name in ('destroy', 'export_files', 'execute', 'stop', 'status'):
        monkeypatch.setattr(library[0], name, forbidden)
    result = worker.inspect_removal(inspected)
    assert result == {'version':1, 'status':'held', 'continuationAvailable':False,
        'code':'destroy_outcome_unknown' if change == 'none' else 'removal_evidence_unverified'}
    assert not calls
    assert before == {str(p.relative_to(tmp_path)):(p.read_bytes(), p.stat().st_mtime_ns)
                      for p in tmp_path.rglob('*') if p.is_file()}


def test_confirmed_destroy_metadata_failure_is_not_unknown_external_destruction(library, tmp_path, monkeypatch):
    value = payload(tmp_path, 'remove')
    original = worker.retain_settlement
    def fail(payload, identity, backend, state):
        if state == 'removed':
            raise OSError('private synthetic settlement failure')
        return original(payload, identity, backend, state)
    monkeypatch.setattr(worker, 'retain_settlement', fail)
    result = removal_main(monkeypatch, value)
    assert not result['ok'] and result['status'] == 'unknown'
    assert result['removal'] == {'version':1, 'phase':'settle', 'code':'phase_failed', 'destroyEffect':'confirmed'}
    inspected = worker.inspect_removal(value)
    assert inspected == {'version':1, 'status':'held', 'continuationAvailable':False,
                         'code':'destroy_confirmation_recorded'}
    assert sum(c[0] == 'destroy' for c in library[1]) == 1


@pytest.mark.parametrize('boundary', ['started', 'dispatch', 'failure-diagnostic'])
def test_phase_record_persistence_failures_never_admit_destructive_replay(library, tmp_path, monkeypatch, boundary):
    value = payload(tmp_path, 'remove')
    write = worker.write_private_record
    injected = []
    if boundary == 'failure-diagnostic':
        def broken_export(*args, **kwargs):
            raise OSError('private original export failure')
        library[0].export_files = broken_export
    name = {'started':'removal-started.json', 'dispatch':'destroy-dispatched.json',
            'failure-diagnostic':'removal-failed.json'}[boundary]
    def fail(path, record):
        if path.name == name:
            injected.append(path.name)
            raise OSError('private phase persistence failure')
        return write(path, record)
    monkeypatch.setattr(worker, 'write_private_record', fail)
    result = removal_main(monkeypatch, value)
    assert injected == [name] and not result['ok']
    assert result['removal']['destroyEffect'] == 'not_dispatched'
    assert 'private' not in json.dumps(result)
    assert not any(c[0] == 'destroy' for c in library[1])
    assert worker.inspect_removal(value)['continuationAvailable'] is False


@pytest.mark.parametrize('field', ['resourceId', 'resourceRevision', 'admissionRevision', 'snapshot'])
def test_remove_missing_original_binding_refuses_before_export(library, tmp_path, monkeypatch, field):
    value = payload(tmp_path, 'remove'); value.pop(field)
    result = removal_main(monkeypatch, value)
    assert result['removal']['phase'] == 'prepare-retention' and result['removal']['destroyEffect'] == 'not_dispatched'
    assert not any(c[0] in {'export','destroy'} for c in library[1])


def test_serialization_loss_after_destroy_retains_confirmed_external_outcome(library, tmp_path, monkeypatch):
    value = payload(tmp_path, 'remove')
    original = worker.json.dumps
    def fail(data, *args, **kwargs):
        if isinstance(data, dict) and 'removal' in data:
            raise ValueError('private synthetic serialization failure')
        return original(data, *args, **kwargs)
    monkeypatch.setattr(worker.json, 'dumps', fail)
    result = removal_main(monkeypatch, value)
    assert not result['ok'] and result['status'] == 'unknown'
    assert result['destroyEffect'] == 'confirmed' and result['code'] == 'receipt_unavailable'
    assert 'private' not in original(result)
    assert sum(c[0] == 'destroy' for c in library[1]) == 1
    assert worker.inspect_removal(value)['code'] == 'destroy_confirmation_recorded'


@pytest.mark.parametrize('field,replacement', [
    ('id', 'other-guest'), ('source', '/other-root'), ('destination', '/other/receipt.tar')])
def test_removal_export_receipt_must_match_requested_guest_and_selection(library, tmp_path, monkeypatch, field, replacement):
    value = payload(tmp_path, 'remove')
    export = library[0].export_files
    def changed(*args, **kwargs):
        result = export(*args, **kwargs)
        setattr(result, field, replacement)
        return result
    library[0].export_files = changed
    result = removal_main(monkeypatch, value)
    assert result['removal'] == {
        'version':1, 'phase':'verify-exports', 'code':'phase_failed', 'destroyEffect':'not_dispatched'}
    assert sum(c[0] == 'export' for c in library[1]) == 2
    assert not any(c[0] == 'destroy' for c in library[1])
    assert not list(tmp_path.glob('retained-*/manifest.json'))


def test_confirmation_record_failure_does_not_skip_existing_admission_cleanup(library, tmp_path, monkeypatch):
    value = payload(tmp_path, 'remove')
    original_write, original_release, original_settle = worker.write_private_record, worker.release_admission, worker.retain_settlement
    calls = []
    def write(path, record):
        if path.name == 'destroy-confirmed.json':
            calls.append('failed-confirmation-record')
            raise OSError('private synthetic confirmation failure')
        return original_write(path, record)
    def release(*args):
        calls.append('release')
        return original_release(*args)
    def settle(*args):
        calls.append('settle')
        return original_settle(*args)
    monkeypatch.setattr(worker, 'write_private_record', write)
    monkeypatch.setattr(worker, 'release_admission', release)
    monkeypatch.setattr(worker, 'retain_settlement', settle)
    result = removal_main(monkeypatch, value)
    assert calls == ['failed-confirmation-record', 'release', 'settle']
    assert not result['ok'] and result['removal']['destroyEffect'] == 'confirmed'
    assert worker.read_private_record(tmp_path / 'backend-removed.json')['state'] == 'removed'
    assert worker.inspect_removal(value)['code'] == 'destroy_outcome_unknown'
    assert sum(c[0] == 'destroy' for c in library[1]) == 1


def test_cleanup_recovery_preserves_all_files_and_verifies_absence(library, tmp_path, monkeypatch):
    lib, calls = library
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    observations = iter(['stopped', 'stopped', 'absent'])
    def observed(*args):
        state = next(observations)
        return {'status': state, 'observation': {
            'ownership': 'absent' if state == 'absent' else 'matched', 'absenceConfirmed': state == 'absent'}}
    monkeypatch.setattr(worker, 'docker_reconcile', observed)
    original = lib.export_files
    def export(identity, source, destination, **kwargs):
        if source == '/artifacts':
            calls.append(('export', (identity, source, destination), kwargs))
            with tarfile.open(destination, 'w') as archive:
                item = tarfile.TarInfo('artifacts'); item.type = tarfile.DIRTYPE; archive.addfile(item)
                item = tarfile.TarInfo('artifacts/current'); item.type = tarfile.SYMTYPE
                item.linkname = '/artifacts/test-1'; archive.addfile(item)
            return Value(id=identity, source=source, destination=str(destination),
                         bytes=destination.stat().st_size, sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
        return original(identity, source, destination, **kwargs)
    lib.export_files = export
    value = payload(tmp_path, 'finishCleanup')
    result = worker.execute_call(value)
    assert result['ok'] and result['status'] == 'removed'
    assert result['retention']['archiveOnly']
    assert [name for name, *_ in calls if name in {'execute','stop','export','destroy'}] == ['export','export','destroy']
    assert all(kwargs['max_bytes'] == worker.retention.RECOVERY_LIMIT for name, _, kwargs in calls if name == 'export')
    assert all(kwargs['timeout_seconds'] == 300 for name, _, kwargs in calls if name == 'export')
    assert {p.stem for p in next(tmp_path.glob('cleanup-*')).glob('*.json')} == {'preserving','verifying','rechecking','removing','observing'}
    before = {str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in tmp_path.rglob('*') if p.is_file()}
    assert worker.inspect_removal(value) == {
        'version': 1, 'status': 'held', 'continuationAvailable': False, 'code': 'destroy_confirmation_recorded'}
    assert before == {str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in tmp_path.rglob('*') if p.is_file()}


def test_unsafe_selected_link_can_be_retained_without_extraction_before_cleanup(library, tmp_path, monkeypatch):
    lib, calls = library
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    def no_extract(*a, **k):
        pytest.fail('Cleanup must never extract retained archives or follow their links')
    monkeypatch.setattr(tarfile.TarFile, 'extractall', no_extract)
    monkeypatch.setattr(tarfile.TarFile, 'extract', no_extract)
    original = lib.export_files
    def export(identity, source, destination, **kwargs):
        if source != '/artifacts':
            return original(identity, source, destination, **kwargs)
        calls.append(('export', (identity, source, destination), kwargs))
        with tarfile.open(destination, 'w') as archive:
            item = tarfile.TarInfo('artifacts'); item.type = tarfile.DIRTYPE; archive.addfile(item)
            item = tarfile.TarInfo('artifacts/python'); item.type = tarfile.SYMTYPE
            item.linkname = '/usr/local/bin/python3.13'; archive.addfile(item)
        return Value(id=identity, source=source, destination=str(destination),
                     bytes=destination.stat().st_size, sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
    lib.export_files = export
    first = removal_main(monkeypatch, payload(tmp_path, 'remove'))
    assert first['status'] == 'stopped' and first['effectAdmitted'] is False
    assert first['phase'] == 'verifying' and first['code'] == 'archive_verification_failed'
    assert not any(name == 'destroy' for name, *_ in calls)
    retained = next(tmp_path.glob('retained-*'))
    before = {p.name: p.read_bytes() for p in retained.iterdir()}
    observations = iter(['stopped', 'stopped', 'absent'])
    monkeypatch.setattr(worker, 'docker_reconcile', lambda *a: {
        'status': (state := next(observations)),
        'observation': {'ownership': 'matched', 'absenceConfirmed': state == 'absent'}})
    retry = {**payload(tmp_path, 'finishCleanup'), 'requestId': 'archive-only-continuation'}
    result = removal_main(monkeypatch, retry)
    assert result['ok'] and result['retention']['archiveOnly'] is True
    assert sum(name == 'destroy' for name, *_ in calls) == 1
    assert sum(name == 'export' for name, *_ in calls) == 4  # Fresh custody, no partial export reuse.
    assert before == {p.name: p.read_bytes() for p in retained.iterdir()}


def test_failed_preservation_reports_known_phase_and_never_destroys(library, tmp_path):
    lib, calls = library
    def fail(*args, **kwargs):
        raise Failure('export too large')
    lib.export_files = fail
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['phase'] == 'preserving'
    assert result['status'] == 'stopped' and result['effectAdmitted'] is False
    assert not any(name == 'destroy' for name, *_ in calls)


@pytest.mark.parametrize('last_state', ['stopped', 'absent'])
def test_cleanup_recovery_keeps_owner_until_physical_absence(library, tmp_path, monkeypatch, last_state):
    _, calls = library
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    observations = iter(['stopped', 'stopped', last_state])
    monkeypatch.setattr(worker, 'docker_reconcile', lambda *args: {
        'status': next(observations), 'observation': {'ownership': 'matched', 'absenceConfirmed': False}})
    released = []
    monkeypatch.setattr(worker, 'release_admission', lambda *args: released.append(args))
    result = worker.execute_call(payload(tmp_path, 'finishCleanup'))
    assert any(name == 'destroy' for name, *_ in calls)
    assert result['status'] == 'unknown' and result['phase'] == 'observing'
    assert result['retention']['archiveOnly']
    assert not released and not (tmp_path / 'backend-removed.json').exists()


@pytest.mark.parametrize('code', ['owner_unverified', 'owner_changed', 'ownership_conflict', 'backend_changed'])
def test_cleanup_changed_owner_does_not_claim_stopped(code):
    result = worker.cleanup_failure({}, 'verifying', 'owned', code)
    assert result['status'] == 'unknown' and result['effectAdmitted'] is False
    assert 'No removal was attempted' in result['message']
    assert 'unchanged' not in result['message']


def test_cleanup_owner_recheck_failure_is_not_preservation_failure(library, tmp_path, monkeypatch):
    _, calls = library
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    observed = []
    def inspect(*args):
        observed.append(args)
        if len(observed) == 1:
            return {'status': 'stopped', 'observation': {'ownership': 'matched'}}
        raise ValueError('The actual owner changed')
    monkeypatch.setattr(worker, 'docker_reconcile', inspect)
    result = worker.execute_call(payload(tmp_path, 'finishCleanup'))
    assert result['status'] == 'unknown' and result['effectAdmitted'] is False
    assert result['phase'] == 'rechecking' and result['code'] == 'owner_unverified'
    assert len(observed) == 2
    assert not any(name == 'destroy' for name, *_ in calls)


def test_cleanup_recovery_retains_private_bound_intent_before_destroy(library, tmp_path, monkeypatch):
    lib, calls = library
    value = payload(tmp_path, 'finishCleanup')
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    order = []
    states = iter(['stopped', 'stopped', 'absent'])
    def observe(*args):
        state = next(states)
        order.append(state)
        return {'status': state, 'observation': {
            'ownership': 'absent' if state == 'absent' else 'matched', 'absenceConfirmed': state == 'absent'}}
    monkeypatch.setattr(worker, 'docker_reconcile', observe)
    original_destroy = lib.destroy
    def destroy(identity, **kwargs):
        assert order == ['stopped', 'stopped']
        directory = worker.removal_directory(value)
        started = worker.read_private_record(directory / 'removal-started.json')
        intent = worker.read_private_record(directory / 'destroy-dispatched.json')
        expected = worker.removal_binding(value, identity, value['expectedBackendIdentity'])
        assert started == {'version': 1, 'phase': 'started', 'binding': expected}
        assert intent['version'] == 1 and intent['phase'] == 'destroy-dispatched'
        assert intent['binding'] == expected
        assert intent['startedSha256'] == hashlib.sha256((directory / 'removal-started.json').read_bytes()).hexdigest()
        for name in ('manifest', 'retention'):
            assert intent[name + 'Sha256'] == hashlib.sha256((directory / (name + '.json')).read_bytes()).hexdigest()
        manifest = worker.read_private_record(directory / 'manifest.json')
        assert intent['archives'] == [{key: item[key] for key in ('source', 'sha256', 'bytes')} for item in manifest]
        for item in manifest:
            raw = Path(item['destination']).read_bytes()
            assert item['id'] == identity and item['verified'] is True
            assert item['sha256'] == hashlib.sha256(raw).hexdigest() and item['bytes'] == len(raw)
        assert worker.read_private_record(directory / 'retention.json')['archiveOnly'] is True
        order.append('destroy')
        return original_destroy(identity, **kwargs)
    lib.destroy = destroy
    original_release, original_settle = worker.release_admission, worker.retain_settlement
    def release(*args):
        assert order[-1] == 'absent'
        order.append('release')
        return original_release(*args)
    def settle(*args):
        assert order[-1] == 'release'
        order.append('settle')
        return original_settle(*args)
    monkeypatch.setattr(worker, 'release_admission', release)
    monkeypatch.setattr(worker, 'retain_settlement', settle)
    result = worker.execute_call(value)
    assert result['ok'] and result['removal']['destroyEffect'] == 'confirmed'
    assert order == ['stopped', 'stopped', 'destroy', 'absent', 'release', 'settle']
    assert sum(name == 'destroy' for name, *_ in calls) == 1


@pytest.mark.parametrize('entry', ['direct', 'json'])
@pytest.mark.parametrize('filename,phase', [
    ('observing.json', 'observe-absence'),
    ('destroy-confirmed.json', 'settle'),
    ('backend-removed.json', 'settle'),
])
def test_cleanup_confirmed_destroy_write_failure_keeps_failure_class_and_effect(
        library, tmp_path, monkeypatch, entry, filename, phase):
    value = payload(tmp_path, 'finishCleanup')
    marker = 'private-fixture-token=/private/fixture/cleanup'
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    states = iter(['stopped', 'stopped', 'absent'])
    observed = []
    def observe(*args):
        state = next(states)
        observed.append(state)
        return {'status': state, 'observation': {
            'ownership': 'absent' if state == 'absent' else 'matched', 'absenceConfirmed': state == 'absent'}}
    monkeypatch.setattr(worker, 'docker_reconcile', observe)
    original_write, original_release = worker.write_private_record, worker.release_admission
    failures, released = [], []
    def write(path, record):
        if path.name == filename:
            failures.append(filename)
            raise OSError(marker)
        return original_write(path, record)
    def release(*args):
        assert observed[-1] == 'absent'
        released.append(True)
        return original_release(*args)
    monkeypatch.setattr(worker, 'write_private_record', write)
    monkeypatch.setattr(worker, 'release_admission', release)
    if entry == 'direct':
        with pytest.raises(worker.RemovalFailure) as failure:
            worker.execute_call(value)
        result = failure.value.receipt
    else:
        result = removal_main(monkeypatch, value)
    assert failures == [filename] and sum(name == 'destroy' for name, *_ in library[1]) == 1
    assert not result['ok'] and result['status'] == 'unknown'
    assert result['removal'] == {
        'version': 1, 'phase': phase, 'code': 'phase_failed', 'destroyEffect': 'confirmed'}
    assert marker not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert released == ([] if filename == 'observing.json' else [True])
    assert observed == (['stopped', 'stopped'] if filename == 'observing.json' else ['stopped', 'stopped', 'absent'])
    assert (tmp_path / 'backend-removed.json').exists() is (filename == 'destroy-confirmed.json')


@pytest.mark.parametrize('action', ['remove', 'finishCleanup'])
def test_known_export_failure_has_safe_code_and_nested_removal_diagnostic(library, tmp_path, monkeypatch, action):
    lib, calls = library
    value = payload(tmp_path, action)
    marker = 'private-fixture-token=/private/fixture/backend-error'
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    monkeypatch.setattr(worker, 'docker_reconcile', lambda *args: {
        'status': 'stopped', 'observation': {'ownership': 'matched'}})
    def export(*args, **kwargs):
        error = Failure(marker)
        error.code = marker
        raise error
    lib.export_files = export
    result = removal_main(monkeypatch, value)
    assert result['code'] == 'preservation_failed' and result['phase'] == 'preserving'
    assert result['status'] == 'stopped' and result['effectAdmitted'] is False
    assert result['removal'] == {
        'version': 1, 'phase': 'export-workspace', 'code': 'phase_failed', 'destroyEffect': 'not_dispatched'}
    assert marker not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert not any(name == 'destroy' for name, *_ in calls)


@pytest.mark.parametrize('code', ['private arbitrary backend code', {'private': 'backend code'}, None])
def test_cleanup_failure_rejects_arbitrary_backend_codes(code):
    result = worker.cleanup_failure({}, 'verifying', 'owned', code)
    assert result['code'] == 'archive_verification_failed'
    assert result['status'] == 'stopped' and result['effectAdmitted'] is False
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('action', ['remove', 'finishCleanup'])
@pytest.mark.parametrize('diagnostic_write_fails', [False, True])
def test_archive_verification_reason_survives_without_destroy(library, tmp_path, monkeypatch, action, diagnostic_write_fails):
    lib, calls = library
    value = payload(tmp_path, action)
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    monkeypatch.setattr(worker, 'docker_reconcile', lambda *args: {
        'status': 'stopped', 'observation': {'ownership': 'matched'}})
    original_export = lib.export_files
    def export(identity, source, destination, **kwargs):
        receipt = original_export(identity, source, destination, **kwargs)
        if source == '/artifacts':
            with tarfile.open(destination, 'a') as archive:
                item = tarfile.TarInfo('artifacts/fixture-pipe')
                # Ordinary FIFO rejection stays covered; archive-only now permits it.
                item.type = tarfile.FIFOTYPE if action == 'remove' else tarfile.CHRTYPE
                archive.addfile(item)
            receipt.bytes = destination.stat().st_size
            receipt.sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
        return receipt
    lib.export_files = export
    original_write = worker.write_private_record
    def write(path, record):
        if diagnostic_write_fails and path.name == 'removal-failed.json':
            raise OSError('private persistence failure')
        return original_write(path, record)
    monkeypatch.setattr(worker, 'write_private_record', write)
    result = removal_main(monkeypatch, value)
    assert result['verification'] == {'version': 1, 'reasonCode': 'unsupported_type',
        'memberType': 'fifo' if action == 'remove' else 'character', 'relativePath': 'artifacts/fixture-pipe'}
    assert result['code'] == 'archive_verification_failed' and result['phase'] == 'verifying'
    assert result['status'] == 'stopped' and result['effectAdmitted'] is False
    assert result['removal'] == {'version': 1, 'phase': 'verify-exports',
        'code': 'phase_failed', 'destroyEffect': 'not_dispatched'}
    assert not any(name == 'destroy' for name, *_ in calls)
    directory = worker.removal_directory(value)
    assert (directory / 'artifacts.tar').is_file()
    assert not (directory / 'retention.json').exists()
    assert not (directory / 'destroy-dispatched.json').exists()
    if not diagnostic_write_fails:
        saved = json.loads((directory / 'removal-failed.json').read_text())
        assert saved['verification'] == result['verification']
        assert saved['binding'] == worker.removal_binding(value, 'owned', value['expectedBackendIdentity'])
    else:
        assert not (directory / 'removal-failed.json').exists()
    assert str(tmp_path) not in json.dumps(result) and 'private persistence' not in json.dumps(result)


def retain_prepared_archive_contract(value):
    """Bind first-path archive custody to this fixture's exact source/profile."""
    value['profileId'] = 'python-project'
    digest = value['snapshot']['archive_sha256']
    directory = Path(value['root']) / 'preparation'; directory.mkdir(mode=0o700)
    worker.write_private_record(directory / 'receipt.json', {
        'status': 'ready', 'profileId': value['profileId'], 'sourceSha256': digest,
        'executionContract': {'version': 1, 'profileId': value['profileId'],
            'source': {'archiveSha256': digest},
            'retention': {'mode': 'archive-only', 'maxBytesPerSelection': worker.retention.RECOVERY_LIMIT}}})


@pytest.mark.parametrize('action', ['finishCleanup', 'remove'])
@pytest.mark.parametrize('fault,reason', [
    (None, None), ('nonzero', 'invalid_size'), ('device', 'unsupported_type'), ('header', 'invalid_header'),
])
def test_fifo_archive_custody_precedes_docker_destroy(library, tmp_path, monkeypatch, action, fault, reason):
    from test_environment_retention import fifo_selection, guard_fifo_io
    guard_fifo_io(monkeypatch)
    lib, calls = library
    value = payload(tmp_path, action)
    if action == 'remove':
        retain_prepared_archive_contract(value)
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    directory = worker.removal_directory(value)
    exported, order = {}, []
    original_export, original_destroy = lib.export_files, lib.destroy
    def export(identity, source, destination, **kwargs):
        receipt = original_export(identity, source, destination, **kwargs)
        if source == '/artifacts':
            destination.write_bytes(fifo_selection('artifacts', fault=fault) + bytes(1024))
            receipt.bytes = destination.stat().st_size
            receipt.sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
        exported[source] = destination.read_bytes()
        order.append(source)
        return receipt
    def verify_complete():
        assert set(exported) == {'/workspace', '/artifacts'}
        retained = json.loads((directory / 'retention.json').read_text())
        assert retained['verified'] and retained['archiveOnly']
        manifest = json.loads((directory / 'manifest.json').read_text())
        assert {row['source'] for row in manifest} == set(exported)
        for row in manifest:
            raw = Path(row['destination']).read_bytes()
            assert raw == exported[row['source']]
            assert row['verified'] and row['bytes'] == len(raw)
            assert row['sha256'] == hashlib.sha256(raw).hexdigest()
        with tarfile.open(directory / 'artifacts.tar') as archive:
            assert archive.getnames() == ['artifacts', 'artifacts/pipe', 'artifacts/after']
            assert archive.getmember('artifacts/pipe').type == tarfile.FIFOTYPE
            assert archive.extractfile('artifacts/after').read() == b'kept'
    def observe(*args):
        if not order:
            order.append('stopped')
            return {'status': 'stopped', 'observation': {'ownership': 'matched'}}
        if order[-1] == '/artifacts':
            verify_complete()  # Both publications precede the second owner check.
            order.append('rechecked')
            return {'status': 'stopped', 'observation': {'ownership': 'matched'}}
        assert order[-1] == 'destroy'
        order.append('absent')
        return {'status': 'absent', 'observation': {'ownership': 'absent', 'absenceConfirmed': True}}
    def destroy(*args, **kwargs):
        assert order[-1] == 'rechecked'
        verify_complete()
        order.append('destroy')
        return original_destroy(*args, **kwargs)
    lib.export_files, lib.destroy = export, destroy
    monkeypatch.setattr(worker, 'docker_reconcile', observe)
    result = removal_main(monkeypatch, value)
    if fault:
        assert result['verification']['reasonCode'] == reason
        assert result['phase'] == 'verifying' and result['effectAdmitted'] is False
        assert result['removal']['destroyEffect'] == 'not_dispatched'
        assert not (directory / 'retention.json').exists()
        assert not any(name == 'destroy' for name, *_ in calls)
        assert order == ['stopped', '/workspace', '/artifacts']
    else:
        assert result['ok'] and result['status'] == 'removed'
        assert result['retention']['archiveOnly'] and result['removal']['destroyEffect'] == 'confirmed'
        assert order == ['stopped', '/workspace', '/artifacts', 'rechecked', 'destroy', 'absent']
        assert sum(name == 'destroy' for name, *_ in calls) == 1
    assert all((directory / (root.strip('/') + '.tar')).read_bytes() == raw for root, raw in exported.items())
    assert not any(name in {'execute', 'stop', 'launch'} for name, *_ in calls)


@pytest.mark.parametrize('profile_id', ['linux-python', 'python-project', 'browser-test'])
def test_manual_execution_uses_selected_profile_not_caller_overrides(library, tmp_path, profile_id):
    lib, calls = library
    value = {**payload(tmp_path, 'execute'), 'profileId': profile_id,
             'command': "printf '%s\\n' 'quotes \" and ; $HOME'; exit 7",
             'environment': {'HOME': '/root'}, 'jobEnvironment': {'PATH': '/caller'},
             'uid': 0, 'gid': 0, 'user': 'root'}
    worker.retain_source_ready(tmp_path, 'owned', value['snapshot'])
    expected = Value(stdout='partial\n', stderr='diagnostic\n', exit_code=7, truncated=True)
    lib.execute = lambda *args, **kwargs: (calls.append(('execute', args, kwargs)) or expected)
    result = worker.execute_call(value)
    assert result == {'ok': True, 'universeId': 'owned', 'result': expected.model_dump()}
    assert len(calls) == 1
    _, args, options = calls[0]
    assert args[0] == 'owned'
    argv = shlex.split(args[1])
    assert argv[:3] == ['python3', '-I', '-c']
    assert argv[4] == 'execute'
    assert json.loads(argv[5]) == {
        'command': value['command'], 'environment': profiles.get(profile_id)['environment']}
    assert options['workdir'] == '/workspace'
    assert options['timeout_seconds'] == 12 and options['max_output_bytes'] == 32768
    assert not (tmp_path / 'source-attempt.json').exists()


@pytest.mark.parametrize('profile_id', ['linux-python', 'python-project', 'browser-test'])
def test_manual_and_durable_transport_share_environment(library, tmp_path, profile_id):
    lib, calls = library
    value = {**payload(tmp_path, 'execute'), 'profileId': profile_id, 'jobId': 'job-exact'}
    worker.retain_source_ready(tmp_path, 'owned', value['snapshot'])
    worker.execute_call(value)
    manual = json.loads(shlex.split(calls[-1][1][1])[5])
    # The durable observer receipt is irrelevant here; inspect its one dispatch.
    with pytest.raises(json.JSONDecodeError):
        worker.execute_call({**value, 'action': 'job_start'})
    durable = json.loads(shlex.split(calls[-1][1][1])[5])
    assert manual['environment'] == durable['environment'] == profiles.get(profile_id)['environment']
    assert manual['command'] == durable['command'] == value['command']
    assert len(calls) == 2
    assert calls[-1][2]['timeout_seconds'] == 15  # Observer, not manual timeout.


def test_manual_guest_reuses_fixed_identity_and_execs_without_login_shell(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(guest.os, 'setgroups', lambda groups: events.append(('groups', groups)))
    monkeypatch.setattr(guest.os, 'setgid', lambda gid: events.append(('gid', gid)))
    monkeypatch.setattr(guest.os, 'setuid', lambda uid: events.append(('uid', uid)))
    monkeypatch.setattr(guest.os, 'chdir', lambda cwd: events.append(('cwd', cwd)))
    monkeypatch.setattr(guest.os, 'execve', lambda *args: events.append(('exec', args)))
    monkeypatch.setattr(guest, 'SCRATCH', tmp_path / 'scratch')
    monkeypatch.setenv('PRIVATE_HOST_SECRET', 'not-forwarded')
    value = {'command': 'python -V; exit 7', 'environment': profiles.get('browser-test')['environment']}
    guest.execute(value)
    assert events[:4] == [('groups', []), ('gid', 65534), ('uid', 65534), ('cwd', '/workspace')]
    _, (executable, argv, environment) = events[4]
    assert executable == '/bin/sh' and argv == ['/bin/sh', '-c', value['command']]
    assert environment == {'LANG': 'C.UTF-8', **profiles.get('browser-test')['environment']}
    assert 'PRIVATE_HOST_SECRET' not in environment
    assert len(events) == 5


@pytest.mark.parametrize('failure', ['setgroups', 'setgid', 'setuid'])
def test_manual_privilege_failure_never_runs_command(monkeypatch, failure):
    for name in ('setgroups', 'setgid', 'setuid'):
        monkeypatch.setattr(guest.os, name, lambda value: None)
    def refuse(value):
        raise PermissionError('cannot drop identity')
    monkeypatch.setattr(guest.os, failure, refuse)
    monkeypatch.setattr(guest.os, 'chdir', lambda *args: pytest.fail('changed cwd after identity failure'))
    monkeypatch.setattr(guest.os, 'execve', lambda *args: pytest.fail('root fallback'))
    with pytest.raises(PermissionError, match='cannot drop identity'):
        guest.execute({'command': 'true'})


def test_disposable_home_cache_restart_and_explicit_report_paths(monkeypatch, tmp_path):
    scratch, artifacts = tmp_path / 'temporary', tmp_path / 'artifacts'
    artifacts.mkdir()
    (artifacts / 'report.xml').write_text('<testsuite/>')
    monkeypatch.setattr(guest, 'SCRATCH', scratch)
    for name in ('setgroups', 'setgid', 'setuid'):
        monkeypatch.setattr(guest.os, name, lambda value: None)
    guest.become_worker()
    environment = guest.worker_environment({})
    for key in ('HOME', 'XDG_CACHE_HOME'):
        directory = Path(environment[key])
        assert directory.is_relative_to(scratch)
        assert directory.stat().st_mode & 0o777 == 0o700
        (directory / 'generated-cache').write_text('disposable data')
    # The unchanged exporter retains selected source/results roots, not scratch.
    with tarfile.open(tmp_path / 'artifacts.tar', 'w') as archive:
        archive.add(artifacts, arcname='artifacts')
    with tarfile.open(tmp_path / 'artifacts.tar') as archive:
        assert archive.getnames() == ['artifacts', 'artifacts/report.xml']
    import shutil
    shutil.rmtree(scratch)  # A stopped/restarted guest may lose temporary files.
    guest.become_worker()
    assert Path(environment['HOME']).is_dir()
    assert Path(environment['XDG_CACHE_HOME']).is_dir()
    assert (artifacts / 'report.xml').read_text() == '<testsuite/>'
    overrides = {'HOME': '/custom-home', 'XDG_CACHE_HOME': '/custom-cache',
                 'AMPLIFIER_TEST_ARTIFACTS': '/custom-reports'}
    assert all(guest.worker_environment({'environment': overrides})[k] == v for k, v in overrides.items())


def test_manual_host_payload_selects_resource_profile_without_caller_environment(tmp_path, monkeypatch):
    home, row = allocated(tmp_path)
    row['profileId'] = 'browser-test'
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'test_environment_worker.py').write_text(
        "import json,sys\np=json.load(sys.stdin)\nprint(json.dumps({'ok': True, **p}))\n")
    args = {'requestId': 'manual-exact', 'command': 'python -V', 'profileId': 'linux-python',
            'environment': {'HOME': '/root'}, 'jobEnvironment': {'PATH': '/caller'}, 'uid': 0}
    result = owner.invoke({'python': sys.executable, 'image': owner.APPROVED_IMAGE, 'dockerHost': 'unix:///socket'},
                          home, row, 'execute', args)
    assert result['action'] == 'execute' and result['profileId'] == 'browser-test'
    assert result['requestId'] == 'manual-exact' and result['command'] == 'python -V'
    assert not {'environment', 'jobEnvironment', 'uid'} & result.keys()


def test_manual_backend_uncertainty_dispatches_once(library, tmp_path):
    lib, calls = library
    value = payload(tmp_path, 'execute')
    worker.retain_source_ready(tmp_path, 'owned', value['snapshot'])
    def lost(*args, **kwargs):
        calls.append(('execute', args, kwargs))
        raise Failure()
    lib.execute = lost
    result = worker.execute_call(value)
    assert result['status'] == 'unknown' and not result['ok']
    assert result['code'] == 'fixture-failure' and len(calls) == 1


@pytest.mark.parametrize('fault', ['backend', 'owner', 'snapshot'])
def test_manual_refusal_never_dispatches(library, tmp_path, monkeypatch, fault):
    lib, calls = library
    value = payload(tmp_path, 'execute')
    worker.retain_source_ready(tmp_path, 'owned', value['snapshot'])
    if fault == 'backend':
        value['expectedBackendIdentity']['daemon'] = 'changed'
    elif fault == 'snapshot':
        value['snapshot']['archive_sha256'] = 'changed'
    else:
        def refuse(*args):
            raise ValueError('owner changed')
        monkeypatch.setattr(worker, 'docker_owner', refuse)
    if fault == 'owner':
        with pytest.raises(ValueError, match='owner changed'):
            worker.execute_call(value)
    else:
        assert not worker.execute_call(value)['ok']
    assert calls == []


@contextmanager
def owned_smoke_guest(docker, argv, name, labels):
    """Reconcile after any launch response, including a lost acknowledgement."""
    try:
        docker(*argv)
        yield
    finally:
        # Successful listing is required; an unavailable daemon stays a failure.
        ids = docker('ps', '-a', '--no-trunc', '--filter', 'name=^/' + name + '$',
                     '--format', '{{.ID}}').stdout.split()
        assert len(ids) <= 1
        if ids:
            observed = json.loads(docker('inspect', ids[0]).stdout)[0]
            assert observed['Id'] == ids[0] and observed['Name'] == '/' + name
            assert all(observed['Config']['Labels'].get(key) == val for key, val in labels.items())
            docker('rm', '-f', ids[0])
            assert not docker('ps', '-a', '--filter', 'id=' + ids[0], '--format', '{{.ID}}').stdout.strip()
        # An empty listing after a lost launch response does not settle that
        # launch; its original exception still fails the check, without replay.


def test_smoke_cleanup_checks_identity_after_lost_launch_response():
    calls = []
    labels = {'io.amplifier.root-owner': 'fixture-owner'}
    def docker(*args):
        calls.append(args)
        if args[0] == 'run':
            raise subprocess.TimeoutExpired('docker run', 1)
        if args[0] == 'ps':
            return SimpleNamespace(stdout='exact-id\n' if '--no-trunc' in args else '')
        if args[0] == 'inspect':
            return SimpleNamespace(stdout=json.dumps([{
                'Id': 'exact-id', 'Name': '/owned-name', 'Config': {'Labels': labels}}]))
        return SimpleNamespace(stdout='')
    with pytest.raises(subprocess.TimeoutExpired):
        with owned_smoke_guest(docker, ['run'], 'owned-name', labels):
            pytest.fail('unknown launch cannot execute')
    assert [args[0] for args in calls] == ['run', 'ps', 'inspect', 'rm', 'ps']
    assert calls[3] == ('rm', '-f', 'exact-id')


@pytest.mark.skipif(os.environ.get('GITHUB_ACTIONS') != 'true'
                    or os.environ.get('CHECK_SUITE') != 'managed-removal-recovery',
                    reason='Real Docker smoke is owned by the fixed hosted repair suite.')
def test_manual_profile_real_owned_docker_workload(library, tmp_path, monkeypatch):
    """Real Docker owner/guest boundary; library execution transport is controlled.

    No host source mounts, credentials, existing guests or prepared dependencies.
    Prepared profile environment is real, but package installation is not claimed.
    """
    lib, _ = library
    monkeypatch.setattr(worker, 'docker_json', real_docker_json)
    monkeypatch.setattr(worker, 'docker_owner', real_docker_owner)
    value = payload(tmp_path, 'execute')
    name = 'unified-profile-' + uuid.uuid4().hex
    value.update(universeId=name, dockerHost='unix:///var/run/docker.sock', profileId='browser-test')
    base = ['docker', '--host', value['dockerHost']]
    def docker(*args, timeout=60):
        return subprocess.run([*base, *args], capture_output=True, text=True, timeout=timeout, check=True)
    value['expectedBackendIdentity'] = worker.docker_backend(value)
    docker('pull', owner.APPROVED_IMAGE, timeout=180)
    labels = {'com.docker.compose.project': name, 'io.amplifier.root-owner': value['rootOwner'],
              'io.amplifier.operation-id': value['operationId']}
    argv = ['run', '-d', '--name', name, '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--network', 'none',
            '--pids-limit', '128', '--memory', '256m', '--cpus', '1']
    for cap in ('CHOWN', 'SETUID', 'SETGID', 'KILL'):
        argv += ['--cap-add', cap]
    for key, val in labels.items():
        argv += ['--label', key + '=' + val]
    for path in ('/workspace', '/artifacts', '/var/lib/unified-jobs', '/tmp'):
        argv += ['--tmpfs', path + ':rw,nosuid,size=16777216']
    argv += [owner.APPROVED_IMAGE, 'sleep', 'infinity']
    with owned_smoke_guest(docker, argv, name, labels):
        docker('exec', name, '/bin/sh', '-c', worker.jobs.initialize_command())
        worker.retain_source_ready(tmp_path, name, value['snapshot'])
        dispatched = []
        def execute(identity, command, *, workdir, timeout_seconds, max_output_bytes, config):
            assert identity == name and config['docker_host'] == value['dockerHost']
            dispatched.append(command)
            process = subprocess.run([*base, 'exec', '--workdir', workdir, name,
                                      '/bin/sh', '-c', command],
                                     capture_output=True, text=True, timeout=timeout_seconds)
            return Value(stdout=process.stdout[:max_output_bytes], stderr=process.stderr[:max_output_bytes],
                         exit_code=process.returncode,
                         truncated=max(len(process.stdout), len(process.stderr)) > max_output_bytes)
        lib.execute = execute
        script = """import json, os
from pathlib import Path
for root in ('/workspace', '/artifacts'):
    path = Path(root) / 'proof.txt'
    path.write_text('owned workload')
    assert path.read_text() == 'owned workload'
    assert path.stat().st_uid == 65534
for key in ('HOME', 'XDG_CACHE_HOME'):
    path = Path(os.environ[key]) / 'generated-cache'
    path.write_text('disposable cache')
    assert path.is_relative_to('/tmp/unified-test')
    assert path.stat().st_uid == 65534
assert sorted(p.name for p in Path('/artifacts').iterdir()) == ['proof.txt']
assert not os.access('/var/lib/unified-jobs', os.R_OK)
assert not os.access('/var/lib/unified-jobs', os.W_OK)
print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), 'groups': os.getgroups(),
                  'cwd': os.getcwd(), 'environment': dict(os.environ)}))
"""
        value['command'] = shlex.join(['python', '-c', script])
        result = worker.execute_call(value)
        assert result['ok'] and result['result']['exit_code'] == 0, result
        proof = json.loads(result['result']['stdout'])
        assert (proof['uid'], proof['gid'], proof['groups'], proof['cwd']) == (65534, 65534, [], '/workspace')
        for key, val in profiles.get('browser-test')['environment'].items():
            assert proof['environment'][key] == val
        assert len(dispatched) == 1
        # A cold invocation of the same worker reads previous writes; no job or
        # initialization is needed and the caller still owns cleanup.
        value['command'] = 'cat /workspace/proof.txt /artifacts/proof.txt'
        second = worker.execute_call(value)
        assert second['result']['stdout'] == 'owned workloadowned workload'
        assert second['result']['exit_code'] == 0 and len(dispatched) == 2
        durable = {**value, 'action': 'job_start', 'jobId': 'profile-parity',
                   'command': shlex.join(['python', '-c', script])}
        observed = worker.execute_call(durable)
        deadline = time.monotonic() + 10
        while not observed['job']['terminal'] and time.monotonic() < deadline:
            time.sleep(.05)
            observed = worker.execute_call({**durable, 'action': 'job_read'})
        assert observed['job']['state'] == 'passed', observed
        assert json.loads(observed['result']['stdout']) == proof
        assert sum(shlex.split(command)[4] == 'job_start' for command in dispatched) == 1


@pytest.mark.parametrize('action', ['remove', 'finishCleanup'])
def test_retention_storage_refusal_precedes_exports_and_removal(library, tmp_path, monkeypatch, action):
    _, calls = library
    value = payload(tmp_path, action)
    if action == 'remove':
        retain_prepared_archive_contract(value)
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=0))
    monkeypatch.setattr(worker, 'docker_reconcile', lambda *a: {
        'status': 'stopped', 'observation': {'ownership': 'matched'}})
    result = removal_main(monkeypatch, value)
    assert result['code'] == 'retention_storage' and result['phase'] == 'preserving'
    assert result['effectAdmitted'] is False and result['status'] == 'stopped'
    assert result['removal']['destroyEffect'] == 'not_dispatched'
    assert result['removal']['phase'] == 'prepare-retention'
    assert not worker.removal_directory(value).exists()
    assert not any(name in {'export', 'destroy'} for name, *_ in calls)
