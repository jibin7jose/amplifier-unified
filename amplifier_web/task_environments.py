"""Opt-in, task-owned test environments using a pinned managed DTU core.

The resource ledger owns environments; the existing operation request journal
owns command admission. Neither a reconnect nor an uncertain result replays an
effect. The isolated helper imports DTU from its managed installation only.
"""
from __future__ import annotations

import asyncio
from contextlib import closing, nullcontext
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

from filelock import FileLock, Timeout as LockTimeout

from . import task_resources as storage
from . import test_environment_configs as backend_configs
from . import test_environment_preparation as dependencies
from . import test_environment_profiles as profiles
from . import test_execution_settings as execution_settings
from . import test_host_resources as host_resources
from . import test_environment_recovery as recovery
from . import test_environment_setup as setup
from . import test_capacity_settings as capacity_settings
from . import registered_test_runners as registered_runners

REPOSITORY = 'https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe'
DTU_COMMIT = '5235833098d9a630791452a46558d92f58af0389'
# New setup selects DTU_COMMIT. Existing owners retain their exact installation
# so qualifying a new snapshot never strands its predecessor's cleanup.
from .managed_runner_binding import STATIC_CORE_COMMITS as QUALIFIED_DTU_COMMITS
APPROVED_IMAGE = 'docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f'
KIND = 'test-environment'
PROFILE = 'linux-python'
OUTPUT_LIMIT = 128 * 1024
SUPPORTED_HOST = sys.platform.startswith('linux')


def definitions(schema, string):
    scope = {'workspaceId': string(100), 'sessionId': string(200)}
    request = {**scope, 'requestId': {**string(200), 'minLength': 1}}
    owned = {**request, 'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1}}
    return {
        **setup.definitions(schema, string),
        'workspace.environments.read': ('Inspect this task’s test environments, retained results and current backend availability. Does not launch, resume or replay work.', schema({**scope, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}, list(scope))),
        'workspace.environments.removal.read': ('Read this root’s exact retained removal binding at the current resource revision. Strictly passive: no backend probe, configuration lookup, repair, operation admission or continuation. Missing original evidence remains held.', schema({
            **scope, 'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1},
            'originalRequestId': {**string(200), 'minLength': 1}})),
        'workspace.environments.removal.list': ('List bounded original removal identities for this exact owned resource without probing, repairing or replaying work. Unbound same-root requests remain explicitly unassociated. Choose an original explicitly before reading its held result.', schema({
            **scope, 'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1},
            'offset': {'type': 'integer', 'minimum': 0},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}},
            [*scope, 'id', 'expectedRevision'])),
        'workspace.environments.start': ('Copy this task’s current Git working area into one approved isolated test environment and prepare locked dependencies for the selected profile. No original writable mounts or injected credentials. Keep requestId stable; unknown launches never replay.', schema({**request, 'profileId': {'enum': [item['id'] for item in profiles.catalog()]}})),
        'workspace.environments.execute': ('Run an approved command in this task’s environment, retaining bounded output. Use its executionContract for Python, offline build, artifact paths and source identities; the copied source has no Git metadata. Keep requestId stable. Timeout means the command may still be running, not cancelled.', schema({**owned, 'command': {**string(16384), 'minLength': 1}, 'timeoutSeconds': {'type': 'integer', 'minimum': 1, 'maximum': profiles.MAX_SECONDS}}, [*owned, 'command'])),
        'workspace.environments.stop': ('Stop this task’s containers at an exact revision and observe the result. Keeps source, artifacts, containers and volumes; never implies removal.', schema(owned)),
        'workspace.environments.remove': ('Remove an already stopped owned environment after preserving its source and artifacts. Optional full retention preserves the complete Incus environment; Docker supports files only. Unknown export or teardown never replays.', schema({**owned, 'retentionMode': {'enum': ['files', 'full']}}, list(owned))),
        'workspace.environments.finishCleanup': ('Finish cleanup of an exact stopped owned environment after a failed or uncertain removal. Preserve complete bounded archives without following guest links, then verify removal and release ownership. The original request and test result stay unchanged. For interrupted local Docker cleanup, the host first verifies that the previous local executor is settled and the exact owner is stopped. This is a new cleanup attempt, not a replay or a claim about the old outcome. Requires its previousRequestId; keep the new requestId stable.', schema({**owned, 'previousRequestId': {**string(200), 'minLength': 1}})),
        'workspace.environments.reconcile': ('Inspect this exact owned environment and repair its metadata without starting, stopping, deleting or replaying backend work. Retire ownership only after the original backend proves physical absence; earlier uncertain operations stay uncertain.', schema(owned)),
        'workspace.environments.exportArtifacts': ('Recover a bounded, verified artifacts archive from this exact stopped owned environment. No workspace export, restart, command execution or deletion. Does not satisfy preservation required for removal. Unknown exports never replay.', schema(owned)),
    }


def configuration(app):
    local, status, reason = local_configuration(app)
    if local:
        return local, status, reason
    try:
        remote = registered_runners.first_configuration(app)
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'unavailable', 'The saved test machine settings need attention. Open Settings → Testing.'
    if remote:
        return remote, 'ready', 'Checks run on a connected Linux test machine. This computer does not need Docker or a local test environment.'
    return None, status, 'Test environments have not been enabled. Connect a test machine in Settings → Testing, or set up local testing on Linux.'


def local_configuration(app):
    """Operator-owned opt-in, never a request-provided executable or profile."""
    target = app.data_dir / 'test-environments.json'
    if not target.exists():
        return None, 'disabled', 'Test environments have not been enabled. A managed Linux test host needs setup.'
    if not SUPPORTED_HOST:
        return None, 'unavailable', 'This version needs a managed Linux test host. Local setup is not available on this platform.'
    try:
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('The operator configuration must be a private regular file.')
        value = json.loads(target.read_text())
        return validate_configuration_value(app, value)
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'unavailable', 'The managed Linux test host needs setup, or its approved backend configuration changed.'


def validate_configuration_value(app, value, *, _qualification=False):
    """Qualify operator-owned fields independently of the currently selected file."""
    if not isinstance(value, dict):
        raise ValueError('The operator configuration must be an object.')
    if value.get('enabled') is not True:
        return None, 'disabled', 'Test environments are disabled on this host.'
    if set(value) - {'enabled', 'installationId', 'commit', 'backend', 'incus', 'dockerHost', 'image', 'maxRunning', 'maxInstances', 'minFreeBytes', 'retentionMode', 'capacityPolicyId', 'runnerQualificationRequired'}:
        raise ValueError('The operator configuration contains unsupported settings.')
    if 'retentionMode' in value and value['retentionMode'] not in {'files', 'full'}:
        raise ValueError('Choose files or full environment retention.')
    if 'capacityPolicyId' in value and (not isinstance(value['capacityPolicyId'], str)
            or not re.fullmatch('[a-f0-9]{64}', value['capacityPolicyId'])):
        raise ValueError('The retained capacity policy identity is invalid.')
    commit = value.get('commit')
    if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('The managed backend has no exact revision.')
    manager = getattr(app, 'smart_tools', None)
    installation = next((row for row in manager.state['installations'] if row['id'] == value.get('installationId')), None) if manager else None
    if not installation or installation.get('status') != 'installed' or installation.get('commit') != commit or installation.get('repository', '').removesuffix('.git') != REPOSITORY or installation.get('extras'):
        raise ValueError('Install the qualified DTU core without provider extras before enabling it.')
    dynamic = installation.get('runnerCandidate') is True
    if value.get('runnerQualificationRequired') is not None and value['runnerQualificationRequired'] is not True:
        raise ValueError('The retained runner qualification requirement changed.')
    if value.get('runnerQualificationRequired') and not dynamic:
        raise ValueError('The retained runner installation provenance changed.')
    if (dynamic or commit not in QUALIFIED_DTU_COMMITS) and not _qualification:
        from .managed_runner_updates import trusted_installation
        if not trusted_installation(app, value):
            raise ValueError('The managed backend has not passed this machine’s owned qualification.')
    if dynamic:
        value = {**value, 'runnerQualificationRequired': True}
    identity = installation['id']
    if not re.fullmatch(r'[a-f0-9]{24}', identity):
        raise ValueError('The managed installation identity is invalid.')
    root = app.data_dir / 'smart-tools' / 'installs' / identity
    python = root / 'venv' / 'bin' / 'python'
    if installation.get('python') != str(python) or not python.is_file() or root.is_symlink() or (root / 'venv').is_symlink():
        raise ValueError('The exact managed backend interpreter is unavailable.')
    backend = value.get('backend', 'docker')
    if backend == 'incus':
        if any(key in value for key in ('dockerHost', 'image', 'maxRunning', 'maxInstances', 'minFreeBytes')):
            raise ValueError('Incus settings belong in its explicit backend configuration.')
        from .incus_environment_worker import validate_configuration
        incus = validate_configuration(value.get('incus'))
        return {**value, 'backend': 'incus', 'incus': incus, 'python': str(python)}, 'ready', 'Isolated Linux environment on the configured Incus host; offline network and no host source mounts.'
    if backend != 'docker' or 'incus' in value:
        raise ValueError('Choose one explicitly configured test backend.')
    if value.get('retentionMode') == 'full':
        raise ValueError('Docker supports source and artifact retention; full environment retention requires Incus.')
    docker = value.get('dockerHost', '')
    if not isinstance(docker, str) or not docker.startswith('unix:///') or '\x00' in docker or '..' in Path(docker[7:]).parts:
        raise ValueError('Only an explicitly configured local Docker Unix socket is supported.')
    image = value.get('image', APPROVED_IMAGE)
    if image != APPROVED_IMAGE:
        raise ValueError('Only the app’s approved Python image digest is supported.')
    for key in ('maxRunning', 'maxInstances', 'minFreeBytes'):
        if key in value and (type(value[key]) is not int or value[key] < 0):
            raise ValueError('Capacity policy values must be nonnegative integers.')
    return {**value, 'image': image, 'python': str(python),
        'admissionRoot': str(Path.home() / '.cache' / 'amplifier-test-admission')}, 'ready', 'Isolated Linux Python environment; offline network and no host source mounts.'


def config_key(value):
    return backend_configs.key(value)


def resolve_configuration(app, row, active):
    return backend_configs.resolve(app, row, active)


def public(home, row):
    return {key: value for key, value in {
        'id': row['id'], 'sessionId': row['sessionId'], 'workspaceId': row['workspaceId'],
        'testMachine': row.get('testMachine'),
        'revision': row['revision'], 'profileId': row['profileId'],
        'resourceRequest': row.get('resourceRequest'), 'timingRequest': row.get('timingRequest'),
        'machineResources': {key: value for key, value in row.get('hostReservation', {}).items() if key not in {'binding', 'version', 'phase'}},
        'status': row.get('environmentState', 'unknown'), 'universeId': row.get('universeId'),
        'source': row['source'], 'snapshot': row.get('snapshot'), 'sourceReady': row.get('sourceReady'), 'services': row.get('services', []),
        'lastOperation': row.get('lastOperation'), 'lastRecovery': row.get('lastRecovery'),
        'reservationRecovery': row.get('reservationRecovery'),
        'previousCleanup': row.get('previousCleanup'),
        'artifactExport': row.get('artifactExport'), 'lastArtifactExport': row.get('lastArtifactExport'),
        'preparation': ({'status': 'ready' if row.get('preparedDependencies') else 'pending', 'remote': True} if row.get('testMachine') else dependencies.status(storage.directory(home, row))) if row.get('preparationPending') or row.get('preparedDependencies') else None,
        'preparationCleanup': row.get('preparationCleanup'),
        'executionContract': row.get('preparedDependencies', {}).get('executionContract'),
        'reason': row.get('reason'), 'observation': row.get('observation'),
        'recovery': row.get('recovery', {'available': False, 'reason': 'Refresh to inspect the retained recovery backend.'}),
        'retention': {'path': str(storage.directory(home, row)), 'preserved': not bool(row.get('workspaceArchiveExpiry')),
                      'workspacePolicy': row.get('workspaceArchivePolicy'), 'workspaceExpiry': row.get('workspaceArchiveExpiry'),
                      'fullAvailable': row.get('backendType') == 'incus',
                      **({'mode': row['retentionMode']} if row.get('retentionMode') is not None else {}),
                      'policy': 'Stop retains container data. Remove exports source and artifacts before deleting owned containers and volumes. Storage may later expire explicitly temporary workspace copies; source, results and receipts stay.',
                      **({'accounting': row['retainedFiles']} if row.get('retainedFiles') is not None else {})},
    }.items() if value is not None}


def get(home, sid, identity):
    with closing(storage.connect(home)) as db:
        row = storage.get(db, sid, identity)
    if row['kind'] != KIND:
        raise ValueError('This resource is not an owned test environment.')
    return row


def save(home, row, **changes):
    row = {**row, **changes, 'revision': row['revision'] + 1}
    with closing(storage.connect(home)) as db, db:
        storage.save(db, row)
    return row


def installation_lock(home, identity):
    if not re.fullmatch(r'[a-f0-9]{16,64}', identity):
        raise ValueError('The managed installation identity is invalid.')
    with closing(storage.connect(home)):
        pass
    return FileLock(str(home / 'workspace-resources' / ('backend-' + identity + '.lock')), timeout=5)


def remove_installation(home, identity, folder, *, ignore_errors=False, cleanup_inventory=None, cleanup_guard=None):
    """Existing install removal cannot orphan a resource's recovery backend."""
    with installation_lock(home, identity), closing(storage.connect(home)) as db:
        from .managed_runner_updates import referenced
        if referenced(home, identity):
            raise ValueError('This runner is retained for an update, rollback, or an existing test owner.')
        if db.execute("SELECT 1 FROM allocations WHERE status!='retired' AND json_extract(body,'$.kind')=? AND json_extract(body,'$.backendInstallation')=? LIMIT 1", (KIND, identity)).fetchone():
            raise ValueError('This backend still owns test environments. Remove those environments before removing or replacing its installation.')
        if cleanup_inventory is not None:
            from .storage_cleanup_measure import measure
            if (cleanup_inventory.get("id") != identity or cleanup_inventory.get("paths") != [str(folder)]
                    or measure([str(folder)])["stamp"] != cleanup_inventory.get("stamp")):
                raise ValueError("The unused runner files changed. Existing files were kept.")
        if cleanup_guard is not None:
            cleanup_guard()
        # The guard can yield to its event loop for a fresh process/policy
        # observation. Recheck contents after that wait, adjacent to removal.
        if cleanup_inventory is not None and measure([str(folder)])["stamp"] != cleanup_inventory.get("stamp"):
            raise ValueError("The unused runner files changed. Existing files were kept.")
        shutil.rmtree(folder, ignore_errors=ignore_errors)


def prepare(home, sid, workspace, actor, source, backend_key, request_id, installation=None, backend_config=None, profile_id=PROFILE, resource_request=None, timing_request=None, workspace_archive_policy=None, *, _qualification=False):
    profile = profiles.resources(profile_id, resource_request)
    resource_request = resource_request or profiles.freeze(profile_id)
    timing_request = timing_request or profiles.freeze_timing(profile_id)
    profiles.timing(profile_id, timing_request)
    with installation_lock(home, installation) if installation else nullcontext(), closing(storage.connect(home)) as db, db:
        if backend_config is not None and backend_configs.retain(home, backend_config) != backend_key:
            raise ValueError('The approved backend configuration identity changed before allocation.')
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute("SELECT body FROM allocations WHERE root=? AND status!='retired' AND json_extract(body,'$.kind')=? AND json_extract(body,'$.commandId')=? LIMIT 1", (sid, KIND, request_id)).fetchone()
        if prior:
            row = json.loads(prior[0])
            # Only the same unadmitted intent can recover the allocation-before-link gap.
            if (not row.get('backendAdmitted') and row['owner'] == actor and row['source'] == source
                    and row.get('profileId', PROFILE) == profile_id
                    and row['backendKey'] == backend_key and row['workspaceId'] == workspace
                    and row.get('workspaceArchivePolicy') == workspace_archive_policy):
                return row
            raise ValueError('This environment request already has a different or admitted owner. Inspect its exact receipt.')
        limits = execution_settings.concurrency(home)
        count = db.execute("SELECT COUNT(*) FROM allocations WHERE root=? AND status!='retired' AND json_extract(body,'$.kind')=?", (sid, KIND)).fetchone()[0]
        if not _qualification and limits['maxEnvironmentsPerChat'] and count >= limits['maxEnvironmentsPerChat']:
            raise ValueError('This task already owns its allowed environments. Inspect or remove one, or adjust Advanced execution limits in Settings.')
        total, owned = db.execute("SELECT COUNT(*), coalesce(sum(root=?),0) FROM allocations WHERE status!='retired'", (sid,)).fetchone()
        if not _qualification and ((limits['maxOwnedResources'] and total >= limits['maxOwnedResources']) or (limits['maxOwnedResourcesPerChat'] and owned >= limits['maxOwnedResourcesPerChat'])):
            raise ValueError('The resource owner capacity has been reached. Inspect retained owners or adjust Advanced execution limits in Settings.')
        row = {'id': str(uuid.uuid4()), 'kind': KIND, 'workspaceId': workspace, 'sessionId': sid,
               'owner': actor, 'source': source, 'profileId': profile_id, 'backendKey': backend_key,
               'workspaceArchivePolicy': workspace_archive_policy,
               'resourceRequest': resource_request, 'timingRequest': timing_request, 'preparationRequired': profile['preparationRequired'],
               'backendInstallation': installation,
               'backendConfigRetained': backend_config is not None,
               'originSnapshotVersion': backend_config.get('originSnapshotVersion') if backend_config else None,
               'testMachine': ({key: backend_config['remoteRunner'][key] for key in ('id', 'name', 'machineId')} if backend_config and backend_config.get('remoteRunner') else None),
               **({'retentionMode': backend_config.get('retentionMode', 'files'),
                   'backendType': backend_config.get('backend', 'docker')} if backend_config is not None else {}),
               'status': 'prepared', 'environmentState': 'preparing', 'revision': 1,
               'createdAt': time.time(), 'commandId': request_id,
               'launchOperationId': str(uuid.uuid4())}
        storage.save(db, row)
    return row


def cleanup_not_started(row):
    """Host preflight failed before launching any cleanup helper."""
    return {'ok': False, 'effectAdmitted': False, 'status': 'stopped',
            'universeId': row.get('universeId'), 'phase': 'preparing',
            'code': 'cleanup_helper_not_started',
            'message': 'Cleanup could not start. The stopped environment and its files are unchanged. Inspect the test machine configuration, then continue cleanup with a new request.'}


def cleanup_refusal_code(exc):
    """Expose known admission boundaries without publishing arbitrary diagnostics."""
    return {
        'The environment changed. Inspect its current revision.': 'resource_revision_changed',
        'The previous local cleanup process may still be active. Keep its original receipt and inspect again after it finishes.': 'previous_cleanup_active',
        'Fresh cleanup requires the exact original stopped environment. No removal was started.': 'stopped_owner_unverified',
        'The fresh cleanup container identities are unavailable. No removal was started.': 'container_identity_unavailable',
        'The local cleanup executor changed during inspection. No removal was started.': 'cleanup_executor_changed',
        'The fresh cleanup inspection changed before admission.': 'cleanup_qualification_changed',
        'The original cleanup owner or receipt changed.': 'original_cleanup_changed',
        'The original removal remains unresolved. Inspect its retained evidence; a new request cannot repeat it.': 'original_cleanup_unresolved',
        'The original removal admission cannot be rebound or replayed.': 'cleanup_admission_changed',
        'Choose this environment’s confirmed pre-removal failure.': 'prior_cleanup_unverified',
        'The previous cleanup does not establish a safe pre-removal failure.': 'prior_cleanup_unverified',
        'The task moved or stopped before environment admission.': 'task_authority_changed',
        'The approved test environment configuration changed.': 'backend_configuration_changed',
        'The approved backend changed. Restore its exact configuration before managing this environment.': 'backend_configuration_changed',
        'The retained environment directory changed; no external action was attempted.': 'environment_directory_changed',
    }.get(str(exc), 'cleanup_admission_refused')


def invoke(config, home, row, action, args, *, _qualification=False):
    """Bound helper output and lifetime; killing a helper is not command cancellation."""
    try:
        if not _qualification and action not in {'capacity', 'read', 'reconcile', 'job_read'}:
            from .managed_runner_updates import validate_execution
            if config.get('originSnapshotVersion') == 1:
                registered_runners.qualify_retained(None, config)
            else:
                validate_execution(home, config)
    except (OSError, ValueError, KeyError, TypeError):
        # This is local validation, before either the local helper or remote
        # runner is invoked. It cannot establish an unknown removal effect.
        if action in {'remove', 'finishCleanup'}:
            return cleanup_not_started(row)
        raise
    if action == 'execute' and args.get('durableJob') is True:
        action = 'job_start'
    folder = (home / 'workspace-resources' / 'backend-probes' / config_key(config)
              if action == 'capacity' else storage.directory(home, row))
    if action == 'capacity':
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {'action': action, 'root': str(folder), 'source': row.get('source'),
               'backendKey': row.get('backendKey'),
               'expectedBackendIdentity': row.get('backendIdentity'),
               'retentionMode': args.get('retentionMode', config.get('retentionMode', 'files')) if action in {'remove', 'finishCleanup'} else config.get('retentionMode', 'files'),
               'snapshot': row.get('snapshot'),
               'operationId': row.get('launchOperationId'), 'universeId': row.get('universeId'),
               'dockerHost': config.get('dockerHost'), 'image': config.get('image'),
               'rootOwner': row.get('sessionId'),
               'resourceId': row.get('id'), 'resourceRevision': row.get('revision'),
               'admissionRevision': args.get('expectedRevision'),
               'requestId': args.get('requestId'), 'command': args.get('command'),
               **({'effectAction': args['effectAction']} if 'effectAction' in args else {}),
               'jobId': args.get('jobId', args.get('requestId')),
               'profileId': row.get('profileId', PROFILE), 'resourceRequest': row.get('resourceRequest'), 'timingRequest': row.get('timingRequest'),
               'timeoutSeconds': args.get('timeoutSeconds', 60),
               **{key: config[key] for key in ('maxRunning', 'maxInstances', 'minFreeBytes', 'admissionRoot') if key in config}}
    if config.get('remoteRunner'):
        return registered_runners.invoke(config, folder, payload)
    try:
        helper = Path(__file__).with_name('test_environment_worker.py')
        if config.get('backend') == 'incus':
            helper = Path(__file__).with_name('incus_environment_worker.py')
            payload['incus'] = config['incus']
        if row.get('preparedDependencies'):
            prepared = dependencies.validate_receipt(row['preparedDependencies'], row['snapshot'], row['profileId'])
            if prepared['backend'] != config.get('backend', 'docker'):
                raise ValueError('The prepared dependencies belong to a different backend.')
            if prepared['backend'] == 'incus':
                payload['incus'] = {**config['incus'], 'image': prepared['image']}
            else:
                payload['image'] = prepared['image']
        environment = {'PATH': os.defpath, 'HOME': str(folder), 'LANG': 'C.UTF-8'}
        process = subprocess.Popen([config['python'], '-I', '-B', str(helper)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=folder, env=environment, start_new_session=True)
    except (OSError, ValueError, KeyError, TypeError):
        # Popen raises only after its failed child was reaped; no helper ran.
        # Once Popen returns, transport/process failures remain uncertain.
        if action in {'remove', 'finishCleanup'}:
            return cleanup_not_started(row)
        raise
    output, errors = bytearray(), bytearray()
    drained = False
    deadline = time.monotonic() + (450 if action == 'exportArtifacts' else 650 if action == 'start' else 750 if action == 'finishCleanup' else 750 if action == 'remove' and row.get('preparedDependencies', {}).get('executionContract') else 650 if action == 'remove' and config.get('backend') == 'incus' else 300 if action == 'prepare' else 30 if action in {'read', 'capacity', 'reconcile', 'job_start', 'job_read', 'job_cancel'} else args.get('timeoutSeconds', 60) + 150)
    try:
        process.stdin.write(json.dumps(payload).encode())
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, errors)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError('The backend response timed out. The effect may still be running; inspect it without replay.')
                for selected, _ in selector.select(min(1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(selected.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(selected.fileobj)
                    else:
                        selected.data.extend(chunk[:max(0, OUTPUT_LIMIT - len(selected.data))])
                        if selected.data is output and len(output) >= OUTPUT_LIMIT:
                            raise ValueError('The backend response exceeded its bound; the outcome is unknown.')
        drained = True
        process.wait(timeout=max(1, deadline - time.monotonic()))
        if process.returncode:
            raise ValueError('The managed backend exited without a valid result; inspect the retained environment.')
        value = json.loads(output)
        if not isinstance(value, dict) or 'ok' not in value:
            raise ValueError('The managed backend returned an invalid receipt.')
        return value
    finally:
        if process.poll() is None or not drained:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def capacity(app, config, *, _qualification=False):
    try:
        result = invoke(capacity_settings.apply(app, config, _qualification=_qualification),
                        app.data_dir, {}, 'capacity', {}, **({'_qualification': True} if _qualification else {}))
        if not result.get('ok'):
            return {'available': False, 'blockers': [{'code': 'backend_unavailable'}]}
        observed = result['capacity']
        if result.get('machineResources'):
            observed = {**observed, 'machineResources': result['machineResources']}
        elif config.get('backend') == 'incus':
            from . import incus_environment_worker as incus_worker
            target = incus_worker.host_target(config['incus'], incus_worker.environment(config['incus'], app.data_dir))
            if target.get('supported'):
                observed = {**observed, 'machineResources': host_resources.snapshot()}
        elif host_resources.local_target(config.get('dockerHost'), {'dockerd'}).get('supported'):
            observed = {**observed, 'machineResources': host_resources.snapshot()}
        return observed
    except (OSError, ValueError, TimeoutError):
        return {'available': False, 'blockers': [{'code': 'capacity_unknown'}]}


def confirmed_result(row, request):
    return (request.get('state') in {'completed', 'failed'}
            and request.get('resourceId') == row['id']
            and request.get('sessionId') == row['sessionId']
            and request.get('action') in {'workspace.environments.' + action for action in ('start', 'execute', 'stop', 'remove', 'finishCleanup', 'exportArtifacts')}
            and isinstance(request.get('result'), dict))


def legacy_cleanup_qualification(app, row, action, args):
    """Derive an exception from trusted retained evidence, never caller flags."""
    if action != 'finishCleanup':
        return None
    from .test_environment_legacy import returned_preservation_failure
    prior = app.operations.requests.read(row['sessionId'], args['previousRequestId'])
    return returned_preservation_failure(row, prior)


def require_resolved_removal(app, row, exclude_request_id=None, legacy=None, fresh_cleanup=None):
    """A new identity cannot escape any retained unresolved removal.

    lastOperation can be a later stop/read; the bounded request-list page is not
    the history. An unbound removal cannot be assigned to a resource after a
    crash, so it holds this root only; another root's history cannot hold it.
    """
    qualified = None
    if legacy is not None:
        from .test_environment_legacy import returned_preservation_failure, completed_preservation_failure
        prior = app.operations.requests.read(row['sessionId'], legacy['originalRequestId'])
        if row.get('guestRemoved'):
            removal, _ = confirmed_guest_removal(app, row)
            verified = completed_preservation_failure(row, prior, removal)
        else:
            verified = returned_preservation_failure(row, prior)
        if verified != legacy:
            raise ValueError('The retained preservation result changed.')
        qualified = legacy['originalRequestId']
    if fresh_cleanup is not None:
        # Current executor/owner proof admits a new desired-state cleanup even
        # after multiple interrupted attempts. Their UNKNOWN receipts are not
        # rewritten; unbound and still-admitting/running work remains held.
        from .test_environment_fresh_cleanup import binding
        prior = app.operations.requests.read(row['sessionId'], fresh_cleanup['originalRequestId'])
        if binding(row, prior) != fresh_cleanup:
            raise ValueError('The original cleanup owner or receipt changed.')
        if row.get('guestRemoved'):
            removal, _ = confirmed_guest_removal(app, row)
            if removal.get('cleanupRecoveryQualification') != fresh_cleanup:
                raise ValueError('The completed cleanup qualification changed.')
        qualified = fresh_cleanup['originalRequestId']
    journal = app.operations.requests.journal
    with journal.lock:
        found = journal.db.execute(
            "SELECT id,value FROM operation_requests WHERE session_id=? "
            "AND (json_extract(value,'$.resourceId')=? "
            "OR COALESCE(json_type(value,'$.resourceId'),'null')!='text' "
            "OR json_extract(value,'$.resourceId')='') "
            "AND json_extract(value,'$.action') IN "
            "('workspace.environments.remove','workspace.environments.finishCleanup') "
            "AND COALESCE(json_extract(value,'$.state'),'outcome_unknown') NOT IN ('completed','failed') "
            "AND (? IS NULL OR id!=?) AND (? IS NULL OR id!=?) "
            "AND NOT (?=1 AND COALESCE(json_extract(value,'$.state'),'')='outcome_unknown' "
            "AND COALESCE(json_extract(value,'$.resourceId'),'')=? "
            "AND COALESCE(json_extract(value,'$.sessionId'),'')=?) ORDER BY rowid LIMIT 1",
            (row['sessionId'], row['id'], exclude_request_id, exclude_request_id,
             qualified, qualified, int(fresh_cleanup is not None), row['id'], row['sessionId'])).fetchone()
    if found:
        raise ValueError('The original removal remains unresolved. Inspect its retained evidence; a new request cannot repeat it.')


def begin_removal(app, row, action, args, actor, *, attribution=None, config=None):
    """Serialize fresh admission with the physical owner without awaiting."""
    lock = FileLock(str(app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0)
    try:
        lock.acquire()
    except LockTimeout:
        # Preserve existing concurrent-admission receipts: effect() will refuse
        # under the same lock without modifying the active owner's resource.
        return app.operations.requests.begin(row['sessionId'], action, args, actor, resource_id=row['id'], attribution=attribution)
    try:
        current = get(app.data_dir, row['sessionId'], row['id'])
        if current['revision'] != args['expectedRevision']:
            raise ValueError('The environment changed. Inspect its current revision.')
        legacy = legacy_cleanup_qualification(app, current, action.rsplit('.', 1)[-1], args)
        fresh_cleanup = None
        if not legacy and action.endswith('.finishCleanup') and config is not None:
            from .test_environment_fresh_cleanup import qualify
            original = app.operations.requests.read(row['sessionId'], args['previousRequestId'])
            fresh_cleanup = qualify(app, current, original, config, args)
        require_resolved_removal(app, current, args['requestId'], legacy, fresh_cleanup)
        request, fresh = app.operations.requests.begin(row['sessionId'], action, args, actor, resource_id=row['id'], attribution=attribution)
        if fresh:
            request = app.operations.requests.finish(request, 'admitting', resourceId=row['id'],
                **({'legacyCleanupQualification': legacy} if legacy else {}),
                **({'cleanupRecoveryQualification': fresh_cleanup} if fresh_cleanup else {}))
        return request, fresh
    finally:
        lock.release()


def project_result(home, row, request):
    """Rebuild resource metadata from its exact receipt without another effect."""
    result = request['result']
    action = request['action'].rsplit('.', 1)[-1]
    if action == 'exportArtifacts':
        # Keep the prior failed/unknown removal visible and preserve ownership.
        changes = {'pendingRequest': None, 'lastArtifactExport': request}
        if result.get('ok') and result.get('artifactExport', {}).get('verified') is True:
            changes['artifactExport'] = {**result['artifactExport'], 'requestId': request['requestId'],
                                         'archives': result['archives'] if 'archives' in result else []}
        return save(home, row, **changes)
    changes = {key: result[key] for key in ('universeId', 'snapshot', 'services', 'sourceReady') if result.get(key) is not None}
    if action == 'start' and (result.get('admissionDenied') or result.get('effectAdmitted') is False):
        changes['backendAdmitted'] = False
    environment_state = ('running' if result.get('ok') else 'unknown') if action == 'execute' else result.get('status', 'unknown')
    if action in {'remove', 'finishCleanup'} and result.get('ok') and environment_state == 'removed':
        changes['status'] = 'retired'
        if row.get('workspaceArchivePolicy'):
            changes['workspaceArchiveRetiredAt'] = row.get('workspaceArchiveRetiredAt', time.time())
        if isinstance(result.get('retention'), dict) and result['retention'].get('verified') is True:
            changes['retainedFiles'] = result['retention']
    if action == 'execute' and isinstance(result.get('job'), dict):
        changes['jobObservation'] = result['job']
        environment_state = 'running'
    changes.update(pendingRequest=request['requestId'] if request['state'] == 'running' else None,
                   environmentState=environment_state, lastOperation=request, reason=result.get('message'))
    updated = save(home, row, **changes)
    if ((result.get('ok') and environment_state in {'stopped', 'removed'}
         and (not updated.get('preparationPending') or updated.get('preparedDependencies')))
            or (action == 'start' and (result.get('admissionDenied') or result.get('effectAdmitted') is False)
                and not updated.get('preparationPending'))):
        host_resources.settle(updated, release=True)
    return updated


def cleanup_preparation(home, row, config):
    try:
        if config.get('remoteRunner'):
            result = invoke(config, home, row, 'cleanupDependencies', {'requestId': str(row.get('pendingRequest') or row['launchOperationId']) + ':dependencies'})
        else:
            result = dependencies.cleanup(config, storage.directory(home, row))
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        result = {'complete': False, 'status': 'unknown', 'reason': 'The owned preparation cleanup could not be verified.'}
    result = {**result, 'complete': result.get('complete') is True}
    row = save(home, row, preparationCleanup=result, preparationPending=not result['complete'])
    return row, result


def guest_removal_binding(row, request_id):
    return {'requestId': request_id, 'resourceId': row['id'],
            **{key: row.get(key) for key in ('sessionId', 'backendKey', 'backendIdentity',
                                           'universeId', 'launchOperationId', 'snapshot')}}


def confirmed_guest_removal(app, row):
    """Require the original committed guest result, never a boolean or inventory."""
    binding = row.get('guestRemovalBinding')
    if not isinstance(binding, dict) or not isinstance(binding.get('requestId'), str):
        raise ValueError('The original guest removal completion is not bound to its owner.')
    if binding != guest_removal_binding(row, binding['requestId']):
        raise ValueError('The original guest removal completion owner changed.')
    request = app.operations.requests.read(row['sessionId'], binding['requestId'])
    if (request.get('action') not in {'workspace.environments.remove', 'workspace.environments.finishCleanup'}
            or request.get('resourceId') != row['id'] or request.get('sessionId') != row['sessionId']
            or request.get('requestId') != binding['requestId']
            or request.get('guestRemovalBinding') != binding
            or request.get('state') not in {'admitting', 'outcome_unknown', 'failed', 'completed'}):
        raise ValueError('The guest removal receipt does not match its cleanup owner.')
    result = row.get('guestRemoval')
    if (not isinstance(result, dict) or request.get('guestRemoval') != result
            or result.get('ok') is not True or result.get('status') != 'removed'
            or result.get('universeId') != row.get('universeId')):
        raise ValueError('The retained guest removal is unverified.')
    return request, result


def resume_preparation_removal(app, row, config, *, expected_revision=None, admit=None,
                               previous_request_id=None):
    """Finish a recorded guest removal without repeating any guest operation."""
    home, sid = app.data_dir, row['sessionId']
    with FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0):
        row = get(home, sid, row['id'])
        if expected_revision is not None and row['revision'] != expected_revision:
            raise ValueError('The environment changed. Inspect its current revision.')
        if admit:
            admit()
        if not row.get('guestRemoved'):
            return row
        if config_key(config) != row['backendKey'] or storage.observe(home, row)['observation'] != 'present':
            raise ValueError('The retained preparation cleanup owner changed.')
        request, result = confirmed_guest_removal(app, row)
        if previous_request_id is not None and previous_request_id != request['requestId']:
            raise ValueError('Choose the exact confirmed guest removal request.')
        require_resolved_removal(app, row, request['requestId'], request.get('legacyCleanupQualification'),
                                 request.get('cleanupRecoveryQualification'))
        if admit:
            admit()
        # A successful cleanup save may precede a failed journal/projection
        # commit. Settle from that evidence without repeating external cleanup.
        cleanup = row.get('preparationCleanup')
        if not row.get('preparationPending'):
            if not isinstance(cleanup, dict) or cleanup.get('complete') is not True:
                raise ValueError('The retained preparation completion is unverified.')
        else:
            row, cleanup = cleanup_preparation(home, row, config)
        result = {**result, 'preparationCleanup': cleanup}
        if not cleanup['complete']:
            result.update(ok=False, status='not-started', message='The guest is absent; dependency cleanup remains unverified.')
        request = app.operations.requests.finish(request, 'completed' if cleanup['complete'] else 'outcome_unknown',
            resourceId=row['id'], result=result, message='Observed the retained guest removal and completed only its dependency cleanup.')
        return project_result(home, row, request)


def effect(app, row, action, args, config, request, admit, policy_admit=None):
    home, sid = app.data_dir, row['sessionId']
    lock = FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0)
    locked = admitted = binding_pending = False
    result = None
    try:
        lock.acquire()
        locked = True
        admit()
        row = get(home, sid, row['id'])
        if action != 'start' and row['revision'] != args['expectedRevision']:
            raise ValueError('The environment changed. Inspect its current revision.')
        if action in {'remove', 'finishCleanup'}:
            # The queue calls this common route without public begin_removal.
            # Persist the trusted row binding before any projection or helper,
            # not merely in the final result after an effect may have occurred.
            binding_pending = True
            recorded = app.operations.requests.read(sid, request['requestId'])
            if (recorded.get('sessionId') != sid or recorded.get('requestId') != request['requestId']
                    or recorded.get('action') != 'workspace.environments.' + action
                    or recorded.get('state') != 'admitting'
                    or recorded.get('resourceId') not in (None, row['id'])):
                raise ValueError('The original removal admission cannot be rebound or replayed.')
            request = recorded
            binding_pending = False
            # Also covers the queue's direct route, before projection/config
            # writes. Never let this refusal rewrite an already-retained UNKNOWN.
            legacy = legacy_cleanup_qualification(app, row, action, args)
            fresh_cleanup = None
            if action == 'finishCleanup' and not legacy:
                from .test_environment_fresh_cleanup import qualify
                original = app.operations.requests.read(sid, args['previousRequestId'])
                fresh_cleanup = qualify(app, row, original, config, args)
            if recorded.get('cleanupRecoveryQualification') != fresh_cleanup:
                raise ValueError('The fresh cleanup inspection changed before admission.')
            if (recorded.get('legacyCleanupQualification') is not None
                    and recorded['legacyCleanupQualification'] != legacy):
                raise ValueError('The retained legacy preservation result changed.')
            require_resolved_removal(app, row, request['requestId'], legacy, fresh_cleanup)
            binding_pending = True
            if recorded.get('resourceId') is None or legacy:
                request = app.operations.requests.finish(recorded, 'admitting', resourceId=row['id'],
                    **({'legacyCleanupQualification': legacy} if legacy else {}))
            binding_pending = False
        if row.get('pendingRequest') and row['pendingRequest'] != request['requestId']:
            prior = app.operations.requests.read(sid, row['pendingRequest'])
            if confirmed_result(row, prior):
                row = project_result(home, row, prior)
        if row['backendKey'] != config_key(config):
            raise ValueError('The approved backend changed. Restore its exact configuration before managing this environment.')
        if action != 'start':
            row = recovery.bind_configuration(app, row, config)
        if action == 'start':
            if not row.get('resourceRequest'):
                row = save(home, row, resourceRequest=profiles.freeze(row['profileId']),
                           preparationRequired=profiles.get(row['profileId'])['preparationRequired'])
            row = storage.allocate(home, row, admit)
        elif not (action in {'remove', 'finishCleanup'} and not row.get('backendAdmitted')) and storage.observe(home, row)['observation'] != 'present':
            raise ValueError('The retained environment directory changed; no external action was attempted.')
        if action == 'execute' and row.get('environmentState') != 'running':
            raise ValueError('Inspect a running environment before executing a command.')
        if action == 'execute':
            seconds = args.get('timeoutSeconds')
            if seconds is None and not row.get('timingRequest'):
                seconds = 60  # Preserve the historical direct-execution default.
            args = {**args, 'timeoutSeconds': profiles.validate_timeout(row['profileId'], seconds, row.get('timingRequest'))}
        if action == 'execute' and row.get('durableJob'):
            raise ValueError('This environment already owns a durable check. Observe or cancel that exact job.')
        if action in {'remove', 'finishCleanup'} and row.get('environmentState') not in {'stopped', 'not-started'}:
            raise ValueError('Stop and inspect this environment before removing it.')
        if action == 'exportArtifacts' and (not row.get('backendAdmitted') or row.get('guestRemoved')
                                           or row.get('environmentState') != 'stopped'):
            raise ValueError('Artifact recovery requires a stopped owned environment.')
        if action == 'finishCleanup':
            prior = app.operations.requests.read(sid, args['previousRequestId'])
            failure = prior.get('result', {})
            if legacy or fresh_cleanup:
                # Qualification is separately committed on this continuation.
                # Do not rewrite the old UNKNOWN receipt or fabricate phases.
                pass
            elif (prior.get('action') not in {'workspace.environments.remove', 'workspace.environments.finishCleanup'}
                    or prior.get('state') != 'failed'
                    or prior.get('resourceId') != row['id'] or prior.get('sessionId') != sid):
                raise ValueError('Choose this environment’s confirmed pre-removal failure.')
            known_refusal = isinstance(failure, dict) and (
                failure.get('phase') in {'preserving', 'verifying'}
                and failure.get('code') in {'preservation_failed', 'archive_verification_failed'}
                or failure.get('phase') == 'preserving' and failure.get('code') == 'retention_storage'
                or failure.get('phase') == 'preparing' and failure.get('code') == 'cleanup_helper_not_started')
            if not legacy and not fresh_cleanup and (not known_refusal or failure.get('effectAdmitted') is not False):
                raise ValueError('The previous cleanup does not establish a safe pre-removal failure.')
            row = save(home, row, previousCleanup=row.get('previousCleanup') or prior)
            if fresh_cleanup:
                args = {**args, 'effectAction': fresh_cleanup['kind']}
        if row['status'] == 'retired':
            raise ValueError('This environment has already been removed.')
        admit()
        row = save(home, row, pendingRequest=request['requestId'],
                   environmentState={'start': 'preparing', 'execute': 'running', 'stop': 'stopping',
                                     'remove': 'removing', 'finishCleanup': 'removing', 'exportArtifacts': 'stopped'}[action])
        if action == 'start':
            prepared = invoke(config, home, row, 'prepare', args)
            if not prepared.get('ok'):
                result = {**prepared, 'status': 'not-started'}
            else:
                identity = recovery.backend_identity(prepared.get('backendIdentity'))
                if identity['backend'] != config.get('backend', 'docker'):
                    raise ValueError('The prepared environment belongs to a different backend.')
                row = save(home, row, universeId=prepared['universeId'], snapshot=prepared['snapshot'], backendIdentity=identity)
                admit()
                profiles.resources(row['profileId'], row.get('resourceRequest'))
                reservation = host_resources.reserve(row, prepared.get('hostTarget'))
                row = save(home, row, hostReservation=reservation)
                if reservation.get('supported') and not reservation.get('admitted'):
                    result = {'ok': False, 'admissionDenied': True, 'status': 'not-started',
                              'machineResources': reservation, 'message': 'Waiting for room on the test machine. No test or dependency build started.'}
                else:
                    if profiles.resources(row['profileId'], row.get('resourceRequest'))['preparationRequired']:
                        # Persist ownership before the networked builder can exist.
                        row = save(home, row, preparationPending=True)
                        if config.get('remoteRunner'):
                            prepared_result = invoke(config, home, row, 'prepareDependencies', args)
                            if prepared_result.get('admissionDenied'):
                                row, cleanup = cleanup_preparation(home, row, config)
                                result = {**prepared_result, 'preparationCleanup': cleanup}
                                if not cleanup.get('complete'):
                                    result.update(admissionDenied=False, effectAdmitted=True, status='unknown')
                                request = app.operations.requests.finish(request, 'failed' if cleanup.get('complete') else 'outcome_unknown', resourceId=row['id'], result=result, message=result.get('message'))
                                row = project_result(home, row, request)
                                return {'resource': public(home, row), 'request': request}
                            if not prepared_result.get('ok'):
                                raise ValueError(prepared_result.get('message', 'The target preparation is unconfirmed.'))
                            prepared_dependencies = prepared_result['preparedDependencies']
                        else:
                            prepared_dependencies = dependencies.prepare(config, storage.directory(home, row),
                                row['snapshot'], row['profileId'], request['requestId'],
                                resource_request=row.get('resourceRequest'), host_reservation=reservation, timing_request=row.get('timingRequest'),
                                dependency_store=Path(home) / 'test-dependency-layers')
                        row = save(home, row, preparedDependencies=prepared_dependencies)
                        host_resources.settle(row, phase='launching')
                    if policy_admit:
                        launch_config = policy_admit()
                    else:
                        admit()
                        launch_config = config
                    # Identity and exact source provenance precede any Docker effect.
                    row = save(home, row, backendAdmitted=True, environmentState='starting')
                    admitted = True
                    result = invoke(launch_config, home, row, 'start', args)
                    if result.get('ok'):
                        host_resources.settle(row)
        elif action in {'remove', 'finishCleanup'} and (not row.get('backendAdmitted') or row.get('guestRemoved')):
            result = row.get('guestRemoval') or {'ok': True, 'status': 'removed', 'universeId': row.get('universeId'),
                'message': 'The test guest is absent. All local source and evidence files were retained.'}
        else:
            admitted = True
            if action == 'execute' and args.get('durableJob') is True:
                # Save the exact identity/command before admission. A crash in
                # either commit gap may be observed, but never re-admitted.
                row = save(home, row, durableJob={key: args[key] for key in ('requestId', 'command', 'timeoutSeconds')})
                request = app.operations.requests.finish(request, 'running', resourceId=row['id'],
                    message='The durable guest admission is being observed; it will not be replayed.')
                result = invoke(config, home, row, action, args)
            else:
                result = invoke(config, home, row, action, args)
        if action in {'remove', 'finishCleanup'} and result.get('ok') and result.get('status') == 'removed':
            # Commit the guest result before removing its prepared image. A
            # restart can finish image cleanup without repeating guest teardown.
            binding = guest_removal_binding(row, request['requestId'])
            request = app.operations.requests.finish(request, request['state'], resourceId=row['id'],
                guestRemoval=result, guestRemovalBinding=binding)
            row = save(home, row, guestRemoved=True, guestRemoval=result, guestRemovalBinding=binding)
            if row.get('preparationPending'):
                row, cleanup = cleanup_preparation(home, row, config)
                result = {**result, 'preparationCleanup': cleanup}
                if not cleanup['complete']:
                    result.update(ok=False, status='not-started', message='The test guest was removed; its owned dependency preparation still needs cleanup.')
        not_admitted = result.get('admissionDenied') or result.get('effectAdmitted') is False
        if action == 'start' and not_admitted and row.get('preparationPending'):
            row, cleanup = cleanup_preparation(home, row, config)
            result = {**result, 'preparationCleanup': cleanup}
            if not cleanup['complete']:
                result.update(admissionDenied=False, status='not-started', message='Launch was declined; owned dependency preparation cleanup is still unverified.')
                result.pop('effectAdmitted', None)
                not_admitted = False
        state = 'completed' if result.get('ok') else 'failed' if not_admitted else 'outcome_unknown' if admitted else 'failed'
        if action == 'execute' and result.get('ok') and result.get('result', {}).get('exit_code', 0) != 0:
            state = 'failed'
        if action == 'execute' and args.get('durableJob') is True:
            state = durable_state(result)
        request = app.operations.requests.finish(request, state, resourceId=row['id'], result=result,
            message=result.get('message', 'The result is retained; this request will not replay.'))
        row = project_result(home, row, request)
        return {'resource': public(home, row), 'request': request}
    except BaseException as exc:
        if binding_pending:
            # A failed binding publication or non-admitting retained request
            # must not be rewritten by the generic failure-result handler.
            # Restart leaves uncertainty held, including legacy unbound rows.
            raise
        # Hold the physical owner lock through final persistence. A rejected
        # competing request must never change the active owner's resource.
        # A returned terminal commit is already proof. A subsequent read fault
        # cannot authorize overwriting that receipt with an unknown outcome.
        recorded = request if confirmed_result(row, request) else {}
        if not recorded:
            try:
                recorded = app.operations.requests.read(sid, request['requestId'])
            except (OSError, ValueError, sqlite3.Error):
                recorded = {}
        if action in {'remove', 'finishCleanup'} and result is None and (
                recorded.get('state') != 'admitting'
                or recorded.get('sessionId') != sid
                or recorded.get('action') != 'workspace.environments.' + action
                or recorded.get('resourceId') not in (None, row['id'])):
            # Lock/admission/revision can fail before the binding check above.
            # They cannot terminalize or attach an older uncertain request.
            raise
        if confirmed_result(row, recorded):
            # The receipt and resource projection have separate commit points.
            # Retry only the projection; never downgrade or replay a known effect.
            request = recorded
            try:
                if locked:
                    row = project_result(home, get(home, sid, row['id']), request)
            except (OSError, ValueError, sqlite3.Error):
                row = {**row, 'observation': {'status': 'unverified'},
                       'reason': 'The operation result is saved, but environment metadata could not be updated. Inspect again to repair the metadata; the operation will not replay.'}
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {'resource': public(home, row), 'request': request}
        if (locked and action in {'remove', 'finishCleanup'} and isinstance(result, dict)
                and result.get('effectAdmitted') is False):
            # A returned helper/preflight refusal establishes that removal was
            # not dispatched. A later journal/projection fault must not erase
            # that evidence and manufacture a permanent unknown-effect hold.
            request = app.operations.requests.finish(request, 'failed', resourceId=row['id'],
                result=result, message=result.get('message'))
            try:
                row = project_result(home, get(home, sid, row['id']), request)
            except (OSError, ValueError, sqlite3.Error):
                row = {**row, 'observation': {'status': 'unverified'},
                       'reason': 'Cleanup did not start. Its saved result is available; inspect again to repair the environment metadata.'}
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {'resource': public(home, row), 'request': request}
        preparation_cleanup = None
        if locked and action == 'start' and not admitted:
            row = get(home, sid, row['id'])
            if row.get('preparationPending'):
                row, preparation_cleanup = cleanup_preparation(home, row, config)
        confirmation = (action in {'remove', 'finishCleanup'} and isinstance(result, dict) and (
            result.get('ok') is True and result.get('status') == 'removed'
            or isinstance(result.get('removal'), dict) and result['removal'].get('destroyEffect') == 'confirmed'
            or result.get('code') == 'receipt_unavailable' and result.get('destroyEffect') == 'confirmed'))
        # Retained preparation belongs to the environment's earlier start. It
        # cannot make a new, locally refused cleanup an unknown removal. Once
        # a helper or guest-result cleanup begins, preserve existing uncertainty.
        cleanup_not_started = action in {'remove', 'finishCleanup'} and not admitted and result is None
        uncertain = admitted or (row.get('preparationPending') and not cleanup_not_started)
        request = app.operations.requests.finish(request, 'outcome_unknown' if uncertain else 'failed', resourceId=row['id'],
            result={'ok': False, 'status': 'not-started' if not admitted else 'unknown',
                    'preparationCleanup': preparation_cleanup,
                    **({'effectAdmitted': False, 'code': cleanup_refusal_code(exc)} if cleanup_not_started else {}),
                    **({'removal': {'version': 1, 'phase': 'host-persistence',
                                    'code': 'metadata_unconfirmed', 'destroyEffect': 'confirmed'}} if confirmation else {}),
                    'message': 'Removal did not complete; inspect the original retained evidence.' if action in {'remove', 'finishCleanup'} else str(exc)[:500]},
            message=('The operation did not finish with a confirmed result. Inspect retained state; do not repeat it automatically.'
                     if admitted else 'The operation was not admitted. Read the current environment and authority before trying a new request.'))
        if locked and (admitted or action == 'start'):
            try:
                row = get(home, sid, row['id'])
                if action == 'exportArtifacts':
                    row = save(home, row, pendingRequest=None, lastArtifactExport=request)
                else:
                    row = save(home, row, pendingRequest=None, environmentState='unknown' if row.get('backendAdmitted') else 'not-started',
                               lastOperation=request, reason=request['message'])
                if action == 'start' and not admitted and not row.get('preparationPending'):
                    host_resources.settle(row, release=True)
            except (OSError, ValueError, sqlite3.Error):
                pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {'resource': public(home, row), 'request': request}
    finally:
        if locked:
            lock.release()


def durable_state(result):
    job = result.get('job', {})
    if not result.get('ok') or job.get('state') == 'unknown' or not job:
        return 'outcome_unknown'
    if job.get('terminal'):
        return {'passed': 'completed', 'failed': 'failed', 'timed_out': 'failed', 'cancelled': 'cancelled'}.get(job['state'], 'outcome_unknown')
    return 'running'


def observe_job(app, row, config, *, cancel=False):
    """Observe/cancel one admitted guest identity, without replay or long locks."""
    home, sid = app.data_dir, row['sessionId']
    with FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0):
        row = get(home, sid, row['id'])
        job = row.get('durableJob')
        if not job or row['status'] == 'retired':
            raise ValueError('No live durable job is recorded for this owned environment.')
        request = app.operations.requests.read(sid, job['requestId'])
        if request.get('resourceId') != row['id'] or request.get('action') != 'workspace.environments.execute':
            raise ValueError('The durable operation belongs to a different environment.')
        if request.get('result', {}).get('job', {}).get('terminal') is True:
            return request
        if row['backendKey'] != config_key(config):
            raise ValueError('The original backend configuration is required for job observation.')
        row = recovery.bind_configuration(app, row, config)
        if storage.observe(home, row)['observation'] != 'present':
            raise ValueError('The retained environment directory changed.')
        result = invoke(config, home, row, 'job_cancel' if cancel else 'job_read', job)
        state = durable_state(result)
        if request.get('state') == state and request.get('result') == result:
            return request
        request = app.operations.requests.finish(request, state, resourceId=row['id'], result=result,
            message='Observed the exact durable guest job; no command was repeated.')
        project_result(home, row, request)
        return request


def cancel_preparation(app, row):
    """Publish cancellation intent without waiting on the builder's long lock."""
    row = get(app.data_dir, row['sessionId'], row['id'])
    if row.get('profileId', PROFILE) == PROFILE or row.get('backendAdmitted'):
        return
    if storage.observe(app.data_dir, row)['observation'] != 'present':
        return  # Admission checks the saved queue cancellation before allocation.
    if row.get('testMachine'):
        config = resolve_configuration(app, row, None)
        return invoke(config, app.data_dir, row, 'cancelPreparation', {'requestId': row['launchOperationId'] + ':cancel-preparation'})
    folder = storage.directory(app.data_dir, row) / 'preparation'
    folder.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('The owned preparation directory changed.')
        try:
            fd = os.open('cancel.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        except FileExistsError:
            return
        with os.fdopen(fd, 'w') as stream:
            json.dump({'cancelRequested': True}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read(app, sid, config, offset=0, limit=10):
    home = app.data_dir
    with closing(storage.connect(home)) as db:
        rows = [json.loads(value[0]) for value in db.execute(
            "SELECT body FROM allocations WHERE root=? AND json_extract(body,'$.kind')=? ORDER BY rowid DESC LIMIT ? OFFSET ?", (sid, KIND, limit, offset))]
    result, configurations = [], {}
    reservation_probe = recovery.reservation_candidate(app, rows)
    for row in rows:
        selected = None
        availability = {'available': False, 'reason': 'This environment is already retired.'}
        if row['status'] != 'retired':
            identity = (row['backendKey'], bool(row.get('backendConfigRetained')))
            if identity not in configurations:
                try:
                    configurations[identity] = (resolve_configuration(app, row, config), {'available': True})
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    configurations[identity] = (None, {'available': False, 'reason': str(exc)})
            selected, availability = configurations[identity]
        if row['status'] != 'retired':
            try:
                with FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0):
                    row = get(home, sid, row['id'])
                    try:
                        row = recovery.repair(app, row)
                    except (OSError, ValueError, sqlite3.Error):
                        row = {**row, 'observation': {'status': 'unverified'},
                               'reason': 'The saved recovery result is retained, but its metadata could not be repaired. Refresh to inspect it again.'}
                        result.append(public(home, {**row, 'recovery': availability}))
                        continue
                    if row['status'] == 'retired':
                        result.append(public(home, {**row, 'recovery': {'available': False, 'reason': 'Physical absence has been confirmed.'}}))
                        continue
                    if not row.get('backendAdmitted') and row.get('environmentState') == 'preparing':
                        preparation = app.operations.requests.read(sid, row['commandId'])
                        if preparation['state'] in {'failed', 'outcome_unknown'}:
                            row = save(home, row, environmentState='not-started', lastOperation=preparation,
                                reason='Preparation ended before any environment launch. Local source and evidence are retained.')
                    if row.get('pendingRequest'):
                        request = app.operations.requests.read(sid, row['pendingRequest'])
                        if confirmed_result(row, request):
                            row = project_result(home, row, request)
                        elif (request['state'] == 'running'
                              and row.get('durableJob', {}).get('requestId') == request['requestId']):
                            # The guest, not this short host helper, owns the
                            # running process. A passive list is not a crash.
                            pass
                        else:
                            if request['state'] == 'admitting':
                                request = app.operations.requests.finish(request, 'outcome_unknown',
                                    message='The operation owner stopped before recording completion. Nothing was replayed.')
                            if request['action'] == 'workspace.environments.exportArtifacts':
                                row = save(home, row, pendingRequest=None, lastArtifactExport=request)
                            else:
                                row = save(home, row, pendingRequest=None, environmentState='unknown' if row.get('backendAdmitted') else 'not-started',
                                    lastOperation=request,
                                    reason='The operation owner stopped before recording completion. Nothing was replayed.')
                    if row['status'] == 'retired':
                        result.append(public(home, {**row, 'recovery': {'available': False, 'reason': 'This environment is already retired.'}}))
                        continue
                    if selected and row.get('backendAdmitted') and storage.observe(home, row)['observation'] == 'present':
                        try:
                            row = recovery.bind_configuration(app, row, selected)
                            observed = invoke(selected, home, row, 'read', {})
                            # An absent owner can make the ordinary read fail.
                            # Only the selected bounded native proof can recover it.
                            if row['id'] == reservation_probe:
                                recovered = recovery.recover_reservation(app, row, selected, observed)
                                if recovered is not row:
                                    physical = recovered['reservationRecovery']['environmentStatus']
                                    observed = {**(observed if observed.get('ok') is True else {}),
                                                'ok': True, 'universeId': row['universeId'],
                                                'status': 'removed' if physical == 'absent' else physical}
                                row = recovered
                            if observed.get('ok'):
                                changes = {key: observed[key] for key in ('universeId', 'services', 'snapshot', 'sourceReady') if key in observed and observed[key] != row.get(key)}
                                if observed.get('status') != row.get('environmentState'):
                                    changes['environmentState'] = observed.get('status', 'unknown')
                                if observed.get('message') and observed['message'] != row.get('reason'):
                                    changes['reason'] = observed['message']
                                if changes:
                                    row = save(home, row, **changes)
                                row = {**row, 'observation': {'status': 'observed', 'observedAt': time.time()}}
                            else:
                                row = {**row, 'observation': {'status': 'unverified'}, 'reason': observed.get('message', 'Current backend state could not be verified.')}
                        except (OSError, ValueError, TimeoutError):
                            row = {**row, 'observation': {'status': 'unverified'}, 'reason': 'Current backend state could not be verified. Retained results are historical.'}
                    elif row.get('backendAdmitted'):
                        row = {**row, 'observation': {'status': 'unverified'}, 'reason': 'The approved backend is unavailable. Retained results are historical.'}
            except LockTimeout:
                row = {**row, 'observation': {'status': 'operation-in-progress'}}
        result.append(public(home, {**row, 'recovery': availability}))
    return result


async def dispatch_full_setup(app, action, args, origin, caller, authority):
    """Accept owned setup once; native turn completion is not revocation."""
    from .service import AppError
    # Reconciliation is observational in Ask. Full still captures its existing
    # setup authority so a late observation cannot settle a revoked operation.
    authority = authority or app.configuration_authority.capture(
        'workspace.environments.setup', args, origin, caller)
    source = app._session(args['sessionId'], hydrate=False)
    if not authority or 'accessRevision' not in authority or authority['sessionId'] != source['id']:
        raise AppError('Setup requires this conversation’s current Full access.', 403)
    workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
    if not workspace or source['workspace'] != workspace['path']:
        raise AppError('Choose this conversation’s current workspace.', 403)
    captured = tuple(source.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision'))
    actor = {'sessionId': source['id'], 'principal': authority.get('runtimeSessionId'),
             'origin': origin, 'generationId': authority.get('generationId')}

    async def guard():
        async with app.lock:
            current = app._session(source['id'], hydrate=False)
            if captured != tuple(current.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')):
                raise AppError('The task moved or stopped before environment admission.', 409)
            app.configuration_authority.check(authority)
            if blocked := app.assignments.blocked(current):
                raise AppError(blocked, 409)

    try:
        await guard()
        operation = {'workspace.environments.setup': setup.request,
                     'workspace.environments.setup.cleanup': setup.cleanup,
                     'workspace.environments.setup.reconcile': setup.reconcile}[action]
        value = await operation(app, args, actor, guard=guard, authority=authority)
        return {'accepted': True, 'result': value}
    except (OSError, ValueError, sqlite3.Error, LockTimeout) as exc:
        raise AppError(str(exc), 409) from None


async def dispatch(app, action, args, origin, caller, authority):
    if action == 'workspace.environments.removal.read':
        return {'accepted': True, 'result': recovery.read_removal(app, args, origin, caller)}
    if action == 'workspace.environments.removal.list':
        return {'accepted': True, 'result': recovery.read_removal(app, args, origin, caller, listing=True)}
    from .service import AppError
    from .collaboration import BINDING, PRINCIPAL
    sid, user = args['sessionId'], origin in {'ui', 'user'}
    if not user and sid != caller:
        raise AppError('Test environments belong to the calling conversation.', 403)
    source = app._session(sid, hydrate=False)
    if (not user and app.access_mode.full(source) and action in {
            'workspace.environments.setup', 'workspace.environments.setup.reconcile', 'workspace.environments.setup.cleanup'}):
        return await dispatch_full_setup(app, action, args, origin, caller, authority)
    principal = source.get('runtimeSessionId') or source.get('nativeIdentity') or sid
    full_revision = None
    attribution = None
    if not user and app.access_mode.full(source):
        app.configuration_authority.source(args, origin, caller,
            active=not action.endswith('.read') or origin == 'system', allow_descendant=True)
        full_revision = app.access_mode.snapshot(source)['revision']
        attribution = {'sessionId': sid, 'origin': origin, 'principal': PRINCIPAL.get(),
                       'generationId': (BINDING.get() or {}).get('_generationId')}
    elif source.get('sessionKind', 'root') != 'root' or not user and PRINCIPAL.get() != principal:
        raise AppError('Test environments require their host-bound root owner.', 403)
    workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
    if not workspace or source['workspace'] != workspace['path']:
        raise AppError('Choose this conversation’s current workspace.', 403)
    captured = tuple(source.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision'))
    config, availability, reason = configuration(app)
    historical = action.rsplit('.', 1)[-1] in {'stop', 'remove', 'finishCleanup', 'reconcile', 'exportArtifacts'}
    row = None
    def guard():
        current = app._session(sid, hydrate=False)
        if captured != tuple(current.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')):
            raise AppError('The task moved or stopped before environment admission.', 409)
        if not user:
            if full_revision is not None:
                if (not app.access_mode.full(current, full_revision)
                        or (current.get('runtimeSessionId') or current.get('nativeIdentity') or sid) != principal):
                    raise AppError('Full access or its execution owner changed before the next environment effect.', 409)
            else:
                app.collaboration.active_binding(current)
            if blocked := app.assignments.blocked(current):
                raise AppError(blocked, 409)
        app.configuration_authority.check(authority)
        current_config, _, _ = configuration(app)
        if historical and row is not None:
            current_config = resolve_configuration(app, row, current_config)
        if current_config != config:
            raise AppError('The approved test environment configuration changed.', 409)
        if action.endswith('.start'):
            capacity_settings.load(app)
    try:
        actor = {'sessionId': sid, 'principal': principal}
        if action in {'workspace.environments.setup', 'workspace.environments.setup.reconcile', 'workspace.environments.setup.cleanup'}:
            async def setup_guard():
                async with app.lock:
                    guard()
            if action == 'workspace.environments.setup.reconcile':
                value = await setup.reconcile(app, args, actor, origin=origin if user else None)
            else:
                guard()
                operation = setup.request if action == 'workspace.environments.setup' else setup.cleanup
                value = await operation(app, args, actor, guard=setup_guard, origin=origin if user else None)
            return {'accepted': True, 'result': value}
        if action.endswith('.read'):
            offset, limit = args.get('offset', 0), args.get('limit', 10)
            items = await asyncio.to_thread(read, app, sid, config, offset, limit)
            with closing(storage.connect(app.data_dir)) as db:
                count = db.execute("SELECT COUNT(*) FROM allocations WHERE root=? AND json_extract(body,'$.kind')=?", (sid, KIND)).fetchone()[0]
                owned = db.execute("SELECT COUNT(*) FROM allocations WHERE root=? AND status!='retired' AND json_extract(body,'$.kind')=?", (sid, KIND)).fetchone()[0]
            setup_state = await setup.read(app)
            if captured != tuple(app._session(sid, hydrate=False).get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')):
                raise AppError('The task moved; refresh its environments.', 409)
            return {'accepted': True, 'result': {'capability': {'status': availability, 'reason': reason,
                'profiles': execution_settings.snapshot(app)['effectiveProfiles'],
                'source': source.get('workingDirectory') or source['workspace'],
                'maxEnvironmentsPerChat': execution_settings.concurrency(app)['maxEnvironmentsPerChat'], 'ownedEnvironments': owned},
                'items': items, 'offset': offset, 'nextOffset': offset+limit if offset+limit<count else None,
                'setup': setup_state,
                'checks': [app.test_queue.public(item) for item in app.test_queue.rows(sid=sid, limit=20)]}}
        guard()
        # The conversation remains the resource/request owner across native actors.
        # Keep its historical fingerprint; actual Full attribution is separate.
        actor = {'sessionId': sid, 'principal': principal}
        previous = app.operations.requests.existing(sid, action, args, actor)
        if previous:
            row = get(app.data_dir, sid, previous['resourceId']) if previous.get('resourceId') else None
            return {'accepted': True, 'duplicate': True, 'result': {'resource': public(app.data_dir, row) if row else None, 'request': previous}}
        if not action.endswith('.start'):
            row = await asyncio.to_thread(get, app.data_dir, sid, args['id'])
            if row['workspaceId'] != workspace['id'] or not user and row['owner']['principal'] != principal:
                raise AppError('The environment belongs to another execution owner.', 403)
            if row['revision'] != args['expectedRevision']:
                raise AppError('The environment changed. Inspect its current revision.', 409)
            if historical:
                config = await asyncio.to_thread(resolve_configuration, app, row, config)
        if config is None:
            raise AppError(reason, 409)
        if action.endswith('.remove') and args.get('retentionMode') == 'full' and config.get('backend', 'docker') != 'incus':
            raise AppError('Full environment retention is available only for this environment’s original Incus backend.', 409)
        guard()
        loop = asyncio.get_running_loop()
        async def locked_guard(capacity=False):
            async with app.lock:
                guard()
                if capacity:
                    return capacity_settings.apply(app, config)
        def admit():
            asyncio.run_coroutine_threadsafe(locked_guard(), loop).result(timeout=10)
        def policy_admit():
            return asyncio.run_coroutine_threadsafe(locked_guard(capacity=True), loop).result(timeout=10)
        if (action.rsplit('.', 1)[-1] in {'remove', 'finishCleanup'}
                and row.get('guestRemoved')
                and (row.get('preparationPending') or isinstance(row.get('preparationCleanup'), dict))):
            # This is completion of the exact original preparation cleanup, not
            # admission of a second guest removal under the caller's new ID.
            row = await app.worktrees.finish_thread(resume_preparation_removal, app, row, config,
                expected_revision=args['expectedRevision'], admit=admit,
                previous_request_id=args.get('previousRequestId'))
            app.operations.notify()
            return {'accepted': True, 'result': {'resource': public(app.data_dir, row),
                    'request': row['lastOperation'], 'preparationRecovery': True}}
        if action.rsplit('.', 1)[-1] in {'remove', 'finishCleanup'}:
            request, fresh = await app.worktrees.finish_thread(begin_removal, app, row, action, args,
                actor, attribution=attribution, config=config)
        else:
            request, fresh = app.operations.requests.begin(sid, action, args, actor, attribution=attribution)
        if not fresh:
            return {'accepted': True, 'duplicate': True, 'result': {'request': request, 'resource': None}}
        try:
            if action.endswith('.start'):
                _, resource_request, timing_request = execution_settings.accepted(app, args.get('profileId', PROFILE))
                row = await asyncio.to_thread(prepare, app.data_dir, sid, workspace['id'], actor,
                    source.get('workingDirectory') or source['workspace'], config_key(config), args['requestId'], config.get('installationId'), config, args.get('profileId', PROFILE), resource_request, timing_request)
            request = app.operations.requests.finish(request, 'admitting', resourceId=row['id'])
            guard()
        except BaseException:
            app.operations.requests.finish(request, 'failed', message='Admission did not complete; no backend command was started.')
            raise
        if action.endswith('.reconcile'):
            result = await app.worktrees.finish_thread(recovery.reconcile, app, row, args, config, request, admit)
        else:
            result = await app.worktrees.finish_thread(effect, app, row, action.rsplit('.', 1)[1], args, config, request, admit, policy_admit)
        app.operations.notify()
        return {'accepted': True, 'result': result}
    except (OSError, ValueError, sqlite3.Error, LockTimeout) as exc:
        raise AppError(str(exc), 409) from None
