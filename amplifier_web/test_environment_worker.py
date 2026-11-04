"""One bounded DTU library call in its managed core interpreter.

Invoked by the host with private stdin, never registered as a generic tool/MCP
server. Only fixed profiles and owner-derived paths cross this boundary.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager, ExitStack
from pathlib import Path

_retention_spec = importlib.util.spec_from_file_location('_environment_retention', Path(__file__).with_name('environment_retention.py'))
retention = importlib.util.module_from_spec(_retention_spec)
_retention_spec.loader.exec_module(retention)

_jobs_spec = importlib.util.spec_from_file_location('_environment_jobs', Path(__file__).with_name('test_job_transport.py'))
jobs = importlib.util.module_from_spec(_jobs_spec)
_jobs_spec.loader.exec_module(jobs)


_host_spec = importlib.util.spec_from_file_location('_host_resources', Path(__file__).with_name('test_host_resources.py'))
host_resources = importlib.util.module_from_spec(_host_spec)
_host_spec.loader.exec_module(host_resources)

_transfer_spec = importlib.util.spec_from_file_location('_source_transfer', Path(__file__).with_name('test_source_transfer.py'))
source_transfer = importlib.util.module_from_spec(_transfer_spec)
_transfer_spec.loader.exec_module(source_transfer)


# Both codes are emitted synchronously by DTU's repository exporter, before
# dependency preparation or guest admission. Keep unknown backend failures held.
PRELAUNCH_SOURCE_REFUSALS = frozenset({'repository-source-changed', 'repository-file-unsupported'})


def source_refusal_message(code):
    reason = ('The source changed while its snapshot was being prepared.'
              if code == 'repository-source-changed' else
              'The source snapshot contains a selected path that is not a regular file or symlink.')
    correction = ('Keep the source stable' if code == 'repository-source-changed' else
                  'Use only regular files or symlinks in the selected source')
    return reason + ' No environment or test command was started. ' + correction + ' and submit a new check.'


def selected_profile(payload):
    spec = importlib.util.spec_from_file_location('_environment_profiles', Path(__file__).with_name('test_environment_profiles.py'))
    profiles = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profiles)
    return profiles.resources(payload.get('profileId', 'linux-python'), payload.get('resourceRequest'))


def read_private_record(path, *, max_bytes=4096):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > max_bytes:
            raise ValueError('A retained backend proof is unsafe.')
        return json.load(stream)


def write_private_record(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.backend-proof-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Atomic publication, never replace earlier proof.
        Path(temporary).unlink()
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def binding(payload, universe, backend):
    return {'backendIdentity': backend, 'rootOwner': payload['rootOwner'],
            'operationId': payload['operationId'], 'universeId': universe}


def verify_binding(payload, universe, backend, prepare=False):
    """Bind effects to the actual daemon, independent of socket/config spelling."""
    expected = binding(payload, universe, backend)
    path = Path(payload['root']) / 'backend-binding.json'
    recorded = read_private_record(path)
    if payload.get('expectedBackendIdentity') is not None and payload['expectedBackendIdentity'] != backend:
        raise ValueError('The actual backend identity changed.')
    if recorded is not None and recorded != expected:
        raise ValueError('The retained backend owner or identity changed.')
    if prepare and recorded is None:
        write_private_record(path, expected)
        recorded = expected
    if recorded is None and payload.get('expectedBackendIdentity') != backend:
        raise ValueError('The original backend identity is unverified.')
    return 'retained-launch' if recorded is not None else 'host'


def retain_settlement(payload, universe, backend, state):
    value = {**binding(payload, universe, backend), 'state': state}
    path = Path(payload['root']) / ('backend-' + state + '.json')
    previous = read_private_record(path)
    if previous is None:
        write_private_record(path, value)
    elif previous != value:
        raise ValueError('The retained backend settlement changed.')


def settled(payload, universe, backend):
    for state in ('removed', 'launched'):
        try:
            recorded = read_private_record(Path(payload['root']) / ('backend-' + state + '.json'))
        except (OSError, ValueError, TypeError):
            # Incomplete historical proof prevents absence retirement. It must
            # not prevent stopping/preserving a currently observed owned guest.
            return False
        if recorded == {**binding(payload, universe, backend), 'state': state}:
            return True
    return False


def observation(payload, universe, backend, source, ownership='unknown', admission_settled=False, absent=False):
    return {**binding(payload, universe, backend), 'backendIdentitySource': source,
            'ownership': ownership, 'admissionSettled': admission_settled, 'absenceConfirmed': absent}


def docker_backend(payload):
    daemon = docker_json(payload, 'info', '--format', '{{json .}}')
    if not isinstance(daemon.get('ID'), str) or not daemon['ID']:
        raise ValueError('The actual Docker daemon is unavailable.')
    return {'backend': 'docker', 'daemon': daemon['ID']}


def docker_reconcile(payload, universe, backend, source):
    # A successful daemon query, not the library's cached status, proves presence.
    result = subprocess.run(['docker', '--host', payload['dockerHost'], 'ps', '-a',
        '--filter', 'label=com.docker.compose.project=' + universe, '--format', '{{json .}}'],
        check=True, capture_output=True, text=True, timeout=20)
    if len(result.stdout) > 1024 * 1024:
        raise ValueError('The backend observation exceeded its bound.')
    containers = [json.loads(line) for line in result.stdout.splitlines()]
    def check_owner(item):
        labels = item.get('Labels', {})
        if isinstance(labels, str):
            labels = dict(label.split('=', 1) for label in labels.split(',') if '=' in label)
        if any(labels.get(key) != value for key, value in {
            'com.docker.compose.project': universe, 'io.amplifier.root-owner': payload['rootOwner'],
            'io.amplifier.operation-id': payload['operationId']}.items()):
            raise ValueError('A backend resource does not match this operation owner.')
    for item in containers:
        check_owner(item)
    remaining = False
    for kind in ('volume', 'network'):
        resources = subprocess.run(['docker', '--host', payload['dockerHost'], kind, 'ls',
            '--filter', 'label=com.docker.compose.project=' + universe, '--format', '{{json .}}'],
            check=True, capture_output=True, text=True, timeout=20)
        if len(resources.stdout) > 1024 * 1024:
            raise ValueError('The backend storage observation exceeded its bound.')
        rows = [json.loads(line) for line in resources.stdout.splitlines()]
        for item in rows:
            check_owner(item)
        remaining |= bool(rows)
    directory = Path(payload['admissionRoot']) / hashlib.sha256(backend['daemon'].encode()).hexdigest()
    try:
        if any(path.is_symlink() for path in (directory, *directory.parents)):
            raise ValueError('The retained admission directory traverses a symlink.')
        if directory.exists():
            # Reconciliation must not create or modify admission state.
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise ValueError('The retained admission directory is unsafe.')
            pending = any(row['universeId'] == universe for row in pending_admissions(directory))
        else:
            pending = False
        complete = settled(payload, universe, backend) and not pending
    except (OSError, ValueError, TypeError):
        complete = False
    absent = not containers and not remaining and complete
    status = ('stopped' if all(item['State'] in {'exited', 'dead'} for item in containers) else 'running') if containers else 'absent' if absent else 'unknown'
    cleanup = {}
    if payload.get('effectAction') == 'settled-local-cleanup' and containers:
        identities, stopped = [], True
        for item in containers:
            identity = item.get('ID')
            if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{12,64}', identity):
                raise ValueError('The cleanup container identity is unavailable.')
            detail = docker_json(payload, 'inspect', identity, '--format', '{{json .State}}')
            stopped &= (detail.get('Status') == 'exited' and type(detail.get('Pid')) is int and detail['Pid'] == 0
                        and all(detail.get(key) is False for key in ('Running', 'Paused', 'Restarting', 'Dead'))
                        and not detail.get('RemovalInProgress'))
            identities.append(identity)
        status = 'stopped' if stopped else 'unknown'
        cleanup = {'cleanupStoppedVerified': stopped, 'containerIds': identities}
    return {'ok': status != 'unknown', 'status': status, 'universeId': universe,
            'observation': {**observation(payload, universe, backend, source,
                'matched' if containers else 'absent' if absent else 'unknown', complete, absent), **cleanup}}


def docker_owner(payload, universe, backend, source):
    result = docker_reconcile(payload, universe, backend, source)
    if result['observation']['ownership'] != 'matched' or result['status'] not in {'running', 'stopped'}:
        raise ValueError('The actual owned environment is not confirmed for this effect.')


def docker_json(payload, *args):
    value = subprocess.run(['docker', '--host', payload['dockerHost'], *args],
        check=True, capture_output=True, text=True, timeout=20)
    if len(value.stdout) > 1024 * 1024:
        raise ValueError('Docker observation exceeded its bound.')
    return json.loads(value.stdout)


def admission_directory(payload, daemon):
    root = Path(payload['admissionRoot'])
    if not root.is_absolute() or '..' in root.parts or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('The admission directory cannot traverse symlinks.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths = [root, root / hashlib.sha256(daemon.encode()).hexdigest()]
    for path in paths:
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('The admission directory must be private and owner-controlled.')
    return paths[-1]


def pending_admissions(directory):
    for path in directory.glob('*.json'):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 4096:
                raise ValueError('An admission receipt is unsafe.')
            row = json.load(stream)
        if not isinstance(row.get('universeId'), str) or path.name != hashlib.sha256(row['universeId'].encode()).hexdigest()+'.json':
            raise ValueError('An admission receipt identity is invalid.')
        yield row


def reserve_admission(payload, capacity):
    directory = admission_directory(payload, capacity['scope']['daemon'])
    identity = payload['universeId']
    path = directory / (hashlib.sha256(identity.encode()).hexdigest()+'.json')
    value = {'universeId': identity, 'root': payload['root'], 'operationId': payload['operationId']}
    # An existing uncertain launch is never re-admitted, even under the same ID.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def release_admission(payload, identity):
    daemon = docker_json(payload, 'info', '--format', '{{json .}}')['ID']
    directory = admission_directory(payload, daemon)
    for row in pending_admissions(directory):
        if row['universeId'] == identity:
            if row.get('root') != payload['root'] or row.get('operationId') != payload['operationId']:
                raise ValueError('The uncertain admission belongs to a different owner.')
            (directory / (hashlib.sha256(identity.encode()).hexdigest()+'.json')).unlink()
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def docker_capacity(payload):
    """Observe the actual daemon, including stopped and externally launched DTUs."""
    daemon = docker_json(payload, 'info', '--format', '{{json .}}')
    if not daemon.get('ID') or not daemon.get('DockerRootDir'):
        raise ValueError('The daemon identity or storage location is unavailable.')
    result = subprocess.run(['docker', '--host', payload['dockerHost'], 'ps', '-a',
        '--filter', 'label=com.docker.compose.project', '--format', '{{json .}}'],
        check=True, capture_output=True, text=True, timeout=20)
    if len(result.stdout) > 1024 * 1024:
        raise ValueError('Docker inventory exceeded its bound.')
    projects = {}
    for line in result.stdout.splitlines():
        row = json.loads(line)
        labels = dict(label.split('=', 1) for label in row.get('Labels', '').split(',') if '=' in label)
        identity = labels.get('com.docker.compose.project', '')
        if identity.startswith('dtu-'):
            projects.setdefault(identity, []).append(row['State'])
    pending = sum(row['universeId'] not in projects for row in pending_admissions(admission_directory(payload, daemon['ID'])))
    retained_count = sum(all(state in {'exited', 'dead'} for state in states) for states in projects.values())
    active = len(projects)-retained_count+pending
    total = len(projects)+pending
    disk = os.statvfs(daemon['DockerRootDir'])
    free = disk.f_bavail * disk.f_frsize
    maximum, retained, minimum = payload.get('maxRunning', 0), payload.get('maxInstances', 0), payload.get('minFreeBytes', 10*1024**3)
    blockers = []
    if maximum and active >= maximum:
        blockers.append({'code': 'execution_capacity'})
    if retained and total >= retained:
        blockers.append({'code': 'retained_capacity'})
    if free < minimum:
        blockers.append({'code': 'storage_pressure'})
    return {'schema_version': 1, 'scope': {'daemon': daemon['ID'], 'backend': 'docker'},
        'coordination': 'same_host_user', 'counts': {'active': active, 'stopped_retained': retained_count, 'pending_unobserved': pending, 'total': total},
        'limits': {'max_running': maximum, 'max_instances': retained, 'min_free_bytes': minimum},
        'storage': {'free_bytes': free}, 'blockers': blockers, 'available': not blockers}


@contextmanager
def docker_admission(payload):
    """Serialize cooperating local callers by daemon identity, not socket alias."""
    import fcntl
    observed = docker_capacity(payload)
    root = admission_directory(payload, observed['scope']['daemon'])
    fd = os.open(root/'admission.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError('The admission lock must be a private regular file.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield docker_capacity(payload)
    finally:
        os.close(fd)


def serialize(value):
    return value.model_dump(mode='json')


def source_identity(identity, snapshot):
    if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(key), str) or not snapshot[key]
            for key in ('archive_sha256', 'snapshot_commit', 'snapshot_tree')):
        raise ValueError('The exact source snapshot identity is unavailable.')
    return {'version': 1, 'universeId': identity, 'archiveSha256': snapshot['archive_sha256'],
            'snapshotCommit': snapshot['snapshot_commit'], 'snapshotTree': snapshot['snapshot_tree']}


def source_ready(root, identity, snapshot):
    """A running container alone is not proof that its source was installed."""
    try:
        expected = source_identity(identity, snapshot)
        descriptor = os.open(root / 'source-ready.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 2048:
                return False
            return json.load(stream) == expected
    except (OSError, ValueError, TypeError):
        return False


def retain_source_ready(root, identity, snapshot):
    """Publish exact readiness atomically, only after successful extraction."""
    value = source_identity(identity, snapshot)
    descriptor, temporary = tempfile.mkstemp(prefix='.source-ready-', dir=root)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, root / 'source-ready.json')
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def retain_manifest(directory, archives):
    """Persist archive names and their receipt before the volumes can disappear."""
    retention.publish_record(directory / 'manifest.json', archives)


def removal_binding(payload, identity, backend):
    value = {**binding(payload, identity, backend), **{key: payload.get(key) for key in (
        'resourceId', 'resourceRevision', 'admissionRevision', 'requestId', 'backendKey', 'snapshot')}}
    if (any(not isinstance(value[k], str) or not value[k] or len(value[k]) > 512 for k in
            ('rootOwner', 'resourceId', 'requestId', 'operationId', 'universeId', 'backendKey'))
            or any(type(value[k]) is not int or value[k] < 1 for k in
                   ('resourceRevision', 'admissionRevision'))
            or value['resourceRevision'] < value['admissionRevision']
            or not isinstance(value['snapshot'], dict) or not value['snapshot']
            or not isinstance(backend, dict) or set(backend) != {'backend', 'daemon'}
            or backend['backend'] != 'docker' or not isinstance(backend['daemon'], str)
            or not backend['daemon'] or len(json.dumps(value).encode()) > 2048):
        raise ValueError('The original removal binding is incomplete.')
    return value


def prepared_archive_custody(payload):
    """Only a new, exact prepared receipt selects first-path archive custody."""
    folder = Path(payload['root']) / 'preparation'
    if folder.is_symlink():
        raise ValueError('The prepared retention directory changed.')
    receipt = read_private_record(folder / 'receipt.json', max_bytes=65536)
    if not receipt or not receipt.get('executionContract'):
        return False
    contract = receipt['executionContract']
    if (receipt.get('status') != 'ready' or receipt.get('profileId') != payload.get('profileId')
            or receipt.get('sourceSha256') != (payload.get('snapshot') or {}).get('archive_sha256')
            or contract.get('version') != 1 or contract.get('profileId') != receipt.get('profileId')
            or contract.get('source', {}).get('archiveSha256') != receipt['sourceSha256']
            or contract.get('retention') != {'mode': 'archive-only', 'maxBytesPerSelection': retention.RECOVERY_LIMIT}):
        raise ValueError('The prepared retention contract does not match this source.')
    return True


def removal_directory(payload):
    root = Path(payload['root'])
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('The removal owner directory is unsafe.')
    request = payload['requestId']
    if not isinstance(request, str) or not request or len(request) > 200:
        raise ValueError('The removal request identity is invalid.')
    return root / ('retained-' + hashlib.sha256(request.encode()).hexdigest())


class RemovalFailure(OSError):
    """Direct callers retain failure semantics; the helper publishes safe enums."""
    def __init__(self, receipt):
        super().__init__('Managed removal did not complete; retained evidence must be inspected.')
        self.receipt = receipt


def remove_owned(payload, identity, backend, config, lib, backend_error, binding_source):
    phase, effect, destination, started = 'observe-stopped', 'not_dispatched', None, False
    recovering, cleanup = payload.get('action') == 'finishCleanup', None
    archive_rejected = False
    verification = None
    try:
        archive_only = recovering or prepared_archive_custody(payload)
        universe = lib.status(identity, config=config)
        if universe.state != 'stopped' or any(item.state == 'running' for item in universe.services):
            return {'ok': False, 'universeId': identity, 'status': 'unknown',
                    'message': 'Removal requires observed stopped containers. No data was removed.'}
        if archive_only:
            cleanup = 'rechecking'
            if docker_backend(payload) != backend:
                raise ValueError('The actual backend changed before cleanup.')
            observed = docker_reconcile(payload, identity, backend, binding_source)
            if (observed.get('status') != 'stopped'
                    or observed.get('observation', {}).get('ownership') != 'matched'):
                raise ValueError('The stopped cleanup owner is unverified.')
        phase = 'prepare-retention'
        destination = removal_directory(payload)
        original = removal_binding(payload, identity, backend)
        cleanup = 'preserving'
        if archive_only:
            cleanup_phase(payload, cleanup)
            if shutil.disk_usage(Path(payload['root'])).free < 2 * retention.RECOVERY_LIMIT + 1024 ** 3:
                return {**cleanup_failure(payload, cleanup, identity, 'retention_storage'),
                        'removal': {'version': 1, 'phase': phase, 'code': 'phase_failed',
                                    'destroyEffect': effect}}
        destination.mkdir(mode=0o700)  # Existing/partial exports are never reused by removal.
        write_private_record(destination / 'removal-started.json',
            {'version': 1, 'phase': 'started', 'binding': original})
        started = True
        archives = []
        options = {'max_bytes': retention.RECOVERY_LIMIT, 'timeout_seconds': 300} if archive_only else {}
        for source, name in [('/workspace', 'workspace.tar'), ('/artifacts', 'artifacts.tar')]:
            phase = 'export-' + source.strip('/')
            archives.append(serialize(lib.export_files(identity, source, destination / name, config=config, **options)))
        phase = 'verify-exports'
        cleanup = 'verifying'
        if archive_only:
            cleanup_phase(payload, cleanup)
        for archive, source, name in zip(archives, ('/workspace', '/artifacts'), ('workspace.tar', 'artifacts.tar')):
            if (archive.get('id') != identity or archive.get('source') != source
                    or archive.get('destination') != str(destination / name)):
                raise ValueError('The exported selection does not match the original removal.')
        try:
            retained = retention.verify_retention(Path(payload['root']), archives, 'archive-only' if archive_only else 'files')
        except (ValueError, retention.tarfile.TarError) as exc:
            # A synchronous validation rejection precedes destroy. Do not turn
            # filesystem/publication errors or helper loss into this receipt.
            archive_rejected = True
            verification = retention.validation_failure_diagnostic(exc)
            raise
        phase = 'publish-manifest'
        retain_manifest(destination, archives)
        if archive_only:
            phase, cleanup = 'recheck-stopped', 'rechecking'
            cleanup_phase(payload, cleanup)
            if docker_backend(payload) != backend:
                raise ValueError('The actual backend changed before removal.')
            observed = docker_reconcile(payload, identity, backend, binding_source)
            if (observed.get('status') != 'stopped'
                    or observed.get('observation', {}).get('ownership') != 'matched'):
                raise ValueError('The stopped cleanup owner changed before removal.')
        phase = 'record-dispatch'
        if archive_only:
            cleanup = 'removing'
            cleanup_phase(payload, cleanup)
        intent = {'version': 1, 'phase': 'destroy-dispatched', 'binding': original,
            'startedSha256': retention.digest(destination / 'removal-started.json')[0],
            'manifestSha256': retention.digest(destination / 'manifest.json')[0],
            'retentionSha256': retention.digest(destination / 'retention.json')[0],
            'archives': [{key: item[key] for key in ('source', 'sha256', 'bytes')} for item in archives]}
        write_private_record(destination / 'destroy-dispatched.json', intent)
        # A dispatch intent can survive a crash before/after the actual call.
        # Its presence never proves completion or authorizes another destroy.
        phase, effect = 'destroy', 'unknown'
        result = lib.destroy(identity, config=config)
        complete = bool(result.complete)
        if complete:
            effect = 'confirmed'
        if archive_only:
            phase, cleanup = 'observe-absence', 'observing'
            cleanup_phase(payload, cleanup)
            if docker_backend(payload) != backend:
                raise ValueError('The actual backend changed while observing removal.')
            observed = docker_reconcile(payload, identity, backend, binding_source)
            if (observed.get('status') != 'absent'
                    or observed.get('observation', {}).get('absenceConfirmed') is not True):
                return {**cleanup_failure(payload, cleanup, identity),
                        'archives': archives, 'retention': retained,
                        'removal': {'version': 1, 'phase': phase, 'code': 'unconfirmed',
                                    'destroyEffect': effect}}
            complete = True
        if complete:
            phase, effect = 'settle', 'confirmed'
            try:
                write_private_record(destination / 'destroy-confirmed.json', {
                    'version': 1, 'phase': 'destroy-confirmed', 'binding': original,
                    'intentSha256': retention.digest(destination / 'destroy-dispatched.json')[0]})
            finally:
                # A diagnostic write failure cannot skip the existing bounded
                # admission cleanup/settlement after confirmed destruction.
                release_admission(payload, identity)
                retain_settlement(payload, identity, backend, 'removed')
        return {'ok': complete, 'universeId': identity,
                'status': 'removed' if complete else 'unknown',
                'result': {'complete': complete, 'outcome': 'removed' if complete else 'unknown'},
                'archives': archives, 'retention': retained,
                'removal': {'version': 1, 'phase': phase, 'code': 'complete' if complete else 'unconfirmed',
                            'destroyEffect': effect},
                'message': 'Source and artifacts were retained before removal.' if complete
                           else 'Artifacts were retained; teardown is not confirmed.'}
    except Exception as exc:
        diagnostic = {'version': 1, 'phase': phase, 'code': 'phase_failed', 'destroyEffect': effect}
        receipt = {'ok': False, 'status': 'unknown', 'universeId': identity, 'removal': diagnostic,
                   'message': 'Removal did not complete. Preserve the original request and inspect retained evidence; do not replay.'}
        if effect == 'not_dispatched':
            if isinstance(exc, backend_error) and phase in {'export-workspace', 'export-artifacts'}:
                # Only a synchronous library export rejection is a known
                # preservation failure. Publication/filesystem OSErrors remain
                # unknown under the original removal boundary.
                receipt = {**cleanup_failure(payload, 'preserving', identity, getattr(exc, 'code', None)),
                           'removal': diagnostic}
            elif archive_rejected:
                receipt = {**cleanup_failure(payload, 'verifying', identity, 'archive_verification_failed'),
                           'removal': diagnostic}
            elif recovering and cleanup == 'rechecking':
                receipt = {**cleanup_failure(payload, cleanup, identity), 'removal': diagnostic}
        if verification is not None:
            receipt['verification'] = verification
        if started:
            try:
                write_private_record(destination / 'removal-failed.json', {
                    **diagnostic, 'binding': original,
                    **({'verification': verification} if verification is not None else {}),
                    'startedSha256': retention.digest(destination / 'removal-started.json')[0]})
            except Exception:
                pass  # Diagnostic persistence cannot change the known external outcome.
        if isinstance(exc, backend_error) or archive_rejected or (recovering and cleanup == 'rechecking'):
            return receipt
        raise RemovalFailure(receipt) from None


def inspect_removal(payload):
    """Inspect exact retained bytes only. No continuation is admitted here.

    Private records and hashes establish consistency, not cryptographic origin,
    executor quiescence or the current physical backend state.
    """
    held = {'version': 1, 'status': 'held', 'continuationAvailable': False,
            'code': 'removal_evidence_unverified'}
    try:
        directory = removal_directory(payload)
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            return held
        expected = removal_binding(payload, payload['universeId'], payload['expectedBackendIdentity'])
        started = read_private_record(directory / 'removal-started.json')
        intent = read_private_record(directory / 'destroy-dispatched.json')
        if (started != {'version': 1, 'phase': 'started', 'binding': expected}
                or not isinstance(intent, dict)
                or intent.get('version') != 1 or intent.get('phase') != 'destroy-dispatched'
                or intent.get('binding') != expected
                or intent.get('startedSha256') != retention.digest(directory / 'removal-started.json')[0]):
            return held
        for name in ('manifest', 'retention'):
            if intent.get(name + 'Sha256') != retention.digest(directory / (name + '.json'))[0]:
                return held
        archives = read_private_record(directory / 'manifest.json')
        if (not isinstance(archives, list) or len(archives) != 2
                or any(not isinstance(a, dict) for a in archives)
                or [a.get('source') for a in archives] != ['/workspace', '/artifacts']):
            return held
        for a, name in zip(archives, ('workspace.tar', 'artifacts.tar')):
            # Never follow a receipt's arbitrary path, foreign guest or source.
            if (a.get('destination') != str(directory / name) or a.get('id') != expected['universeId']
                    or a.get('verified') is not True):
                return held
        if intent.get('archives') != [{k: a[k] for k in ('source', 'sha256', 'bytes')} for a in archives]:
            return held
        accounting = read_private_record(directory / 'retention.json')
        mode = accounting.get('policy') if isinstance(accounting, dict) else None
        if mode not in {'files', 'archive-only'}:
            return held
        checked = retention.verify_retention(Path(payload['root']), archives, mode, publish=False)
        if checked != accounting:
            return held
        confirmed = read_private_record(directory / 'destroy-confirmed.json')
        if confirmed is None:
            return {**held, 'code': 'destroy_outcome_unknown'}
        if confirmed != {'version': 1, 'phase': 'destroy-confirmed', 'binding': expected,
                         'intentSha256': retention.digest(directory / 'destroy-dispatched.json')[0]}:
            return held
        return {**held, 'code': 'destroy_confirmation_recorded'}
    except (OSError, ValueError, TypeError, KeyError, retention.tarfile.TarError):
        return held


def cleanup_phase(payload, phase):
    """Immutable phase evidence distinguishes preservation from destructive work."""
    root = Path(payload['root']) / ('cleanup-' + hashlib.sha256(payload['requestId'].encode()).hexdigest())
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('The cleanup evidence directory is unsafe.')
    write_private_record(root / (phase + '.json'), {
        'requestId': payload['requestId'], 'operationId': payload['operationId'],
        'rootOwner': payload['rootOwner'], 'phase': phase})


def cleanup_failure(payload, phase, identity, code='preservation_failed'):
    if not isinstance(code, str) or code not in {
            'preservation_failed', 'retention_storage', 'archive_verification_failed',
            'owner_unverified', 'owner_changed', 'ownership_conflict', 'backend_changed'}:
        code = 'preservation_failed'
    before_removal = phase in {'preserving', 'verifying', 'rechecking'}
    changed_owner = phase == 'rechecking' or code in {'owner_unverified', 'owner_changed', 'ownership_conflict', 'backend_changed'}
    if phase == 'rechecking' and code == 'preservation_failed':
        code = 'owner_unverified'
    if phase == 'verifying' and code == 'preservation_failed':
        code = 'archive_verification_failed'
    return {'ok': False, 'status': 'stopped' if before_removal and not changed_owner else 'unknown',
            'universeId': identity, 'code': code, 'phase': phase,
            'effectAdmitted': not before_removal,
            'message': ('The environment owner or backend could not be verified. No removal was attempted; inspect the exact environment before continuing cleanup.'
                        if before_removal and changed_owner else
                        'Files could not be completely preserved. The stopped environment is unchanged; no removal was attempted.'
                        if before_removal else 'Files were preserved, but removal is not confirmed. Inspect the exact environment before continuing cleanup.')}


def profile(image, payload=None):
    selected = selected_profile(payload or {})
    value = {
        'name': 'unified-test',
        'services': {'twin': {
            'image': image, 'command': ['sleep', 'infinity'],
            'read_only': True, 'cap_drop': ['ALL'],
            'cap_add': ['CHOWN', 'SETUID', 'SETGID', 'KILL'],
            'security_opt': ['no-new-privileges:true'],
            'pids_limit': selected['pids'], 'mem_limit': str(selected['memoryMiB'])+'m', 'cpus': selected['cpus'],
            'shm_size': str(selected['shmMiB'])+'m',
            'healthcheck': {'test': ['CMD', 'true'], 'interval': '2s'},
            'tmpfs': ['/tmp:rw,noexec,nosuid,size='+str(selected['tmpMiB']*1024**2)],
            'volumes': ['workspace:/workspace', 'artifacts:/artifacts', 'control:/var/lib/unified-jobs'],
            'networks': ['default'], 'working_dir': '/workspace',
            # Root only inside this container, with no capabilities or host mounts.
            # Docker copy initializes the fresh owned volumes before commands run.
            'user': '0:0',
        }},
        'volumes': {'workspace': {}, 'artifacts': {}, 'control': {}},
        'networks': {'default': {'internal': True}},
        'x-dtu': {'twin_machine': 'twin'},
    }
    if payload is not None:
        labels = {'io.amplifier.root-owner': payload['rootOwner'], 'io.amplifier.operation-id': payload['operationId']}
        value['services']['twin']['labels'] = dict(labels)
        for definition in [*value['volumes'].values(), *value['networks'].values()]:
            definition['labels'] = dict(labels)
    return value


def execute_call(payload):
    from digital_twin_universe import lib
    from digital_twin_universe.schemas import BackendConfig, DigitalTwinUniverseError
    root = Path(payload['root'])
    config = BackendConfig(state_root=root / 'backend', docker_host=payload['dockerHost'])
    action = payload['action']
    identity = payload.get('universeId')
    snapshot = payload.get('snapshot')
    try:
        if action == 'capacity':
            return {'ok': True, 'capacity': docker_capacity(payload)}
        if not identity:
            identity = lib.launch_id(payload['operationId'], config=config)
        backend = docker_backend(payload)
        try:
            binding_source = verify_binding(payload, identity, backend, prepare=action == 'prepare')
        except ValueError:
            return {'ok': False, 'status': 'unknown', 'universeId': identity, 'code': 'backend_unverified',
                    'observation': observation(payload, identity, backend, 'unverified')}
        if action == 'reconcile':
            return docker_reconcile(payload, identity, backend, binding_source)
        if action == 'prepare':
            # Source snapshot is immutable and retained even when launch fails.
            snapshot = serialize(lib.export_repository_snapshot(Path(payload['source']), root / 'source.tar'))
            (root / 'snapshot.json').write_text(json.dumps(snapshot))
            try:
                source_transfer.preflight(root / 'source.tar', snapshot)
            except (OSError, ValueError):
                return {'ok': False, 'status': 'not-started', 'effectAdmitted': False,
                        'universeId': identity, 'snapshot': snapshot, 'code': 'source-transfer-invalid',
                        'message': 'The source snapshot changed or exceeds the 512 MiB transfer limit. No dependency preparation, environment or test command was started.'}
            return {'ok': True, 'universeId': identity, 'snapshot': snapshot, 'backendIdentity': backend,
                    'hostTarget': host_resources.local_target(payload.get('dockerHost'), {'dockerd'})}
        if action == 'start':
            source_identity(identity, snapshot)
            with ExitStack() as transfer:
                try:
                    upload, destination, size, chunked = transfer.enter_context(source_transfer.staged(root / 'source.tar', snapshot))
                except (OSError, ValueError):
                    return {'ok': False, 'status': 'not-started', 'effectAdmitted': False,
                            'universeId': identity, 'snapshot': snapshot, 'code': 'source-transfer-invalid',
                            'message': 'The frozen source could not be prepared for transfer. No environment or test command was started; source and preparation evidence are retained.'}
                compose = root / 'compose.json'
                compose.write_text(json.dumps(profile(payload['image'], payload)))
                try:
                    with docker_admission(payload) as capacity:
                        if capacity['scope']['daemon'] != backend['daemon']:
                            return {'ok': False, 'effectAdmitted': False, 'status': 'not-started', 'code': 'backend_changed',
                                    'message': 'The actual daemon changed before admission. Nothing was launched.'}
                        if not capacity['available']:
                            return {'ok': False, 'admissionDenied': True, 'status': 'not-started', 'capacity': capacity,
                                'message': 'Capacity changed before launch. No backend effect was admitted.'}
                        reservation = reserve_admission(payload, capacity)
                        universe = lib.launch(compose, operation_id=payload['operationId'], timeout_seconds=600, config=config)
                        retain_settlement(payload, identity, backend, 'launched')
                        reservation.unlink()
                except BlockingIOError:
                    return {'ok': False, 'admissionDenied': True, 'status': 'not-started',
                        'message': 'Another cooperating launch owns admission. No environment was started.'}
                identity = universe.id
                lib.push_files(identity, upload, destination, config=config)
                unpack = lib.execute(identity, source_transfer.command(destination, size, snapshot, chunked),
                    workdir='/', timeout_seconds=60, max_output_bytes=32768, config=config)
                if unpack.exit_code != 0:
                    return {'ok': False, 'universeId': identity, 'snapshot': snapshot, 'status': 'unknown',
                            'message': 'The environment launched but source preparation did not complete. Inspect it; launch was not replayed.', 'result': serialize(unpack)}
                initialized = lib.execute(identity, jobs.initialize_command(selected_profile(payload)['preparationRequired']),
                    workdir='/', timeout_seconds=120, max_output_bytes=32768, config=config)
                if initialized.exit_code != 0:
                    return {'ok': False, 'universeId': identity, 'sourceReady': False, 'status': 'unknown',
                            'message': 'The owned source and dependency initialization did not complete. It was not repeated.'}
                retain_source_ready(root, identity, snapshot)
                return {'ok': True, 'universeId': identity, 'sourceReady': True,
                        'status': universe.state, 'services': [serialize(item) for item in universe.services]}
        if not identity:
            # One resource has its own backend root. Exact launch identity is the
            # recovery key when the host lost the original helper response.
            identity = lib.launch_id(payload['operationId'], config=config)
        if action in {'read', 'execute', 'stop', 'remove', 'finishCleanup', 'exportArtifacts'} | jobs.JOB_ACTIONS:
            docker_owner(payload, identity, backend, binding_source)
        if action in jobs.JOB_ACTIONS:
            if not source_ready(root, identity, snapshot):
                return {'ok': False, 'universeId': identity, 'sourceReady': False, 'status': 'unknown',
                        'message': 'The exact source preparation is not confirmed; no job was started.'}
            payload = {**payload, 'universeId': identity, 'jobEnvironment': selected_profile(payload)['environment']}
            result = lib.execute(identity, jobs.command(payload), workdir='/', timeout_seconds=15,
                max_output_bytes=262144, config=config)
            return jobs.result(payload, serialize(result))
        if action == 'read':
            universe = lib.status(identity, config=config)
            ready = source_ready(root, identity, snapshot)
            return {'ok': True, 'universeId': identity, 'sourceReady': ready,
                    'status': universe.state if ready or universe.state == 'stopped' else 'unknown',
                    'services': [serialize(item) for item in universe.services],
                    'message': None if ready else 'Source preparation is not confirmed for this exact snapshot. Checks are unavailable. Stop and remove the environment to retain any partial files; inspection does not install source again.'}
        if action == 'execute':
            if not source_ready(root, identity, snapshot):
                return {'ok': False, 'universeId': identity, 'sourceReady': False, 'status': 'unknown',
                        'message': 'Source preparation is not confirmed for this exact snapshot. No check was started and source was not installed again.'}
            command = jobs.command({**payload, 'jobEnvironment': selected_profile(payload)['environment']})
            result = lib.execute(identity, command, workdir='/workspace',
                timeout_seconds=payload['timeoutSeconds'], max_output_bytes=32768, config=config)
            return {'ok': True, 'universeId': identity, 'result': serialize(result)}
        if action == 'stop':
            result = lib.stop(identity, config=config)
            if result.complete:
                release_admission(payload, identity)
            return {'ok': result.complete, 'universeId': identity,
                    'status': 'stopped' if result.complete else 'unknown', 'result': serialize(result),
                    'message': 'Containers stopped; source and artifacts are retained.' if result.complete else 'Stop was requested but not confirmed. Inspect current state.'}
        if action in {'remove', 'finishCleanup'}:
            recovering = action == 'finishCleanup'
            if not recovering and payload.get('retentionMode', 'files') != 'files':
                return {'ok': False, 'status': 'unknown', 'universeId': identity, 'code': 'retention_unsupported',
                        'message': 'Full environment retention is unavailable for this backend. The stopped environment was preserved.'}
            return remove_owned(payload, identity, backend, config, lib, DigitalTwinUniverseError, binding_source)
        if action == 'exportArtifacts':
            universe = lib.status(identity, config=config)
            if universe.state != 'stopped' or any(item.state == 'running' for item in universe.services):
                return {'ok': False, 'effectAdmitted': False, 'universeId': identity, 'status': 'unknown',
                        'message': 'Artifact recovery requires observed stopped containers. Nothing was exported.'}
            destination = root / ('artifacts-' + hashlib.sha256(payload['requestId'].encode()).hexdigest())
            destination.mkdir(mode=0o700)
            # The qualified library bounds the producer's export. Verification
            # additionally checks complete tar structure, links and selected size.
            archives = [serialize(lib.export_files(identity, '/artifacts', destination / 'artifacts.tar', config=config))]
            retained = retention.verify_artifacts(archives)
            retain_manifest(destination, archives)
            return {'ok': True, 'universeId': identity, 'status': 'stopped', 'archives': archives,
                    'artifactExport': retained, 'message': 'Artifacts retained. The stopped environment and workspace are unchanged; removal is not authorized.'}
        raise ValueError('Unsupported managed test environment action.')
    except RemovalFailure:
        raise  # Preserve the inner bounded receipt, including confirmed destruction.
    except DigitalTwinUniverseError as exc:
        if action == 'prepare' and exc.code in PRELAUNCH_SOURCE_REFUSALS:
            # This synchronous source export returned before dependency or
            # guest admission. Never describe it as an uncertain running check.
            return {'ok': False, 'status': 'not-started', 'effectAdmitted': False,
                    'universeId': identity, 'snapshot': None, 'code': exc.code,
                    'message': source_refusal_message(exc.code)}
        if action in {'remove', 'finishCleanup'}:
            return {'ok': False, 'status': 'unknown', 'code': 'backend_unconfirmed',
                    'message': 'The original removal could not be confirmed. Inspect retained evidence; do not replay.'}
        return {'ok': False, 'status': 'unknown', 'universeId': identity or exc.universe_id,
                'snapshot': snapshot, 'code': exc.code,
                'message': 'The backend did not confirm this operation. A command may still be running; inspect the retained environment without replaying the request.'}


def main():
    value = None
    try:
        payload = json.loads(sys.stdin.buffer.read(65537))
        value = execute_call(payload)
    except RemovalFailure as exc:
        value = exc.receipt
    except Exception:  # noqa: BLE001 - subprocess boundary returns uncertainty, never raw private diagnostics.
        value = {'ok': False, 'status': 'unknown',
                 'message': 'The managed backend failed before returning a confirmed result. Inspect retained state; this request will not replay.'}
    try:
        encoded = json.dumps(value)
    except Exception:
        # No raw error or broken diagnostic object in the fallback. Preserve
        # confirmed destruction as distinct from unconfirmed metadata/transport.
        effect = value.get('removal', {}).get('destroyEffect') if isinstance(value, dict) else None
        effect = effect if effect in {'not_dispatched', 'unknown', 'confirmed'} else 'unknown'
        encoded = ('{"ok":false,"status":"unknown","code":"receipt_unavailable",'
                   '"destroyEffect":"' + effect + '","message":"Inspect retained evidence; do not replay."}')
    sys.stdout.write(encoded)


if __name__ == '__main__':
    main()
