"""Explicit host-owned setup for an already installed local test daemon.

The public action chooses a backend, never code, paths, images, or credentials.
Each accepted setup keeps a private intent/result and never replays uncertain
side effects. Ordinary inspection does not install or configure anything.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import time

from filelock import FileLock, Timeout

LEGACY_REPOSITORY = 'https://github.com/microsoft/amplifier-bundle-digital-twin-universe'
LEGACY_COMMIT = 'ce9219d8dac2770efe0b8270acd8ec017af625c7'
INCUS_IMAGE_SERVER = 'https://images.linuxcontainers.org'
INCUS_IMAGE_ALIAS = 'ubuntu/24.04/cloud'
ACTIVE = {'accepted', 'running'}


async def settled_thread(function, *args, **kwargs):
    # Cancellation cannot release setup ownership while a thread still drives
    # a daemon operation. Each subprocess is bounded by its own timeout.
    callback = functools.partial(function, *args, **kwargs)
    context = contextvars.copy_context()
    work = asyncio.get_running_loop().run_in_executor(None, context.run, callback)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(work)
            break
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


def definitions(schema, string):
    fields = {'workspaceId': string(100), 'sessionId': string(200),
              'requestId': {**string(200), 'minLength': 1},
              'backend': {'enum': ['docker', 'incus'], 'default': 'docker'}}
    return {'workspace.environments.setup': (
        'Set up this app’s managed Smart Tool runner on an already installed local Docker daemon by default. '
        'Choose Incus explicitly for specialized native or registered-machine workflows. Existing bindings never migrate. '
        'Downloads the approved runner and base image, verifies a disposable offline check, and saves private app configuration. '
        'Does not install a daemon, change system settings, or adopt other environments. Keep requestId stable; uncertain effects never replay.',
        schema(fields, [key for key in fields if key != 'backend'])),
        'workspace.environments.setup.reconcile': ('Inspect the original setup request and settle proven saved configuration or cleanup without rerunning setup.', schema({key: value for key, value in fields.items() if key != 'backend'}, [key for key in fields if key != 'backend'])),
        'workspace.environments.setup.cleanup': ('Clean up only this interrupted setup’s verified disposable probe and unused owned network. Preserve installed packages, base images and private receipts. Never touch a ready runner or active task environment.', schema({key: value for key, value in fields.items() if key != 'backend'}, [key for key in fields if key != 'backend']))}


def root(app, *, create=True):
    folder = Path(app.data_dir) / 'test-runner-setup'
    if any(path.is_symlink() for path in (folder, *folder.parents)):
        raise ValueError('The private test setup location was replaced.')
    if not folder.exists() and not create:
        return folder
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = folder.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Test setup requires a private app-owned directory.')
    return folder


def read_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 65536:
            raise ValueError('The retained setup record is not a private regular file.')
        return json.load(stream)


def write_json(path, value):
    read_json(path)  # Refuse replacing someone else’s file or a symlink.
    handle, temporary = tempfile.mkstemp(prefix='.setup-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def public(record):
    return {**{key: record[key] for key in ('id', 'state', 'phase', 'detail', 'backend', 'createdAt', 'updatedAt', 'proof') if key in record},
            **({'cleanup': {key: value for key, value in record['cleanup'].items()
                            if key not in {'actor', 'authority'}}} if 'cleanup' in record else {}),
            **{key: record['intent'][key] for key in ('sessionId', 'workspaceId')},
            'canComplete': record.get('state') in {'outcome_unknown', 'failed'} and bool(record.get('candidateConfiguration') and record.get('proof', {}).get('probeRemoved')) and not record.get('cleanup')}


def executable(name):
    # The helper also resolves these names with os.defpath. Do not qualify a
    # user-selected binary which the eventual helper would not actually use.
    value = shutil.which(name, path=os.defpath)
    if not value:
        return None
    path = Path(value).resolve()
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or not os.access(path, os.X_OK):
        return None
    return str(path)


def command(argv, *, env=None, timeout=20):
    from .incus_environment_worker import run
    result = run(argv, env or {'PATH': os.defpath, 'LANG': 'C.UTF-8'}, timeout=timeout, output_bytes=65536)
    if result['exit_code'] or result['truncated']:
        detail = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', result.get('stderr', '').strip())[-1200:]
        raise ValueError('The test backend did not confirm the requested setup step.' + (' ' + detail if detail else ''))
    return result['stdout'].strip()


def capacity_defaults(*, resource_admission=False):
    if resource_admission:
        # Native host reservations and current pressure govern parallelism.
        # Stopped retained guests consume disk, not an arbitrary instance slot.
        return {'maxRunning': 0, 'maxInstances': 0, 'minFreeBytes': 4 * 1024 ** 3}
    try:
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (OSError, ValueError):
        memory = 0
    running = 2 if memory >= 16 * 1024 ** 3 else 1
    return {'maxRunning': running, 'maxInstances': running + 2, 'minFreeBytes': 4 * 1024 ** 3}


def tracked_command(path, kind, argv, **kwargs):
    """Persist command settlement inside the executing thread, before reply."""
    if read_json(path):
        raise ValueError('This setup effect already has a receipt and cannot be replayed.')
    row = {'kind': kind, 'state': 'started', 'createdAt': time.time()}
    write_json(path, row)
    try:
        result = command(argv, **kwargs)
    except Exception as exc:
        # ValueError is a completely drained, exited client with a known failure.
        # Timeouts and interrupted clients leave daemon admission uncertain.
        row.update(state='completed' if isinstance(exc, ValueError) else 'outcome_unknown', success=False, updatedAt=time.time())
        write_json(path, row)
        raise
    row.update(state='completed', success=True, updatedAt=time.time())
    write_json(path, row)
    return result


def discover():
    if not sys.platform.startswith('linux'):
        reason = 'Automatic local test setup is currently qualified on Linux. Use a supported Linux host; macOS and Windows setup have not been qualified.'
        return [{'id': name, 'label': label, 'available': False, 'reason': reason} for name, label in [('docker', 'Docker'), ('incus', 'Incus')]]
    rows = []
    docker = executable('docker')
    endpoint = None
    if docker:
        for path in [Path('/var/run/docker.sock'), Path('/run/user') / str(os.getuid()) / 'docker.sock', Path.home() / '.docker/run/docker.sock']:
            try:
                if not stat.S_ISSOCK(path.stat().st_mode):
                    continue
                candidate = 'unix://' + str(path)
                info = json.loads(command([docker, '--host', candidate, 'info', '--format', '{{json .}}'], timeout=5))
                command([docker, '--host', candidate, 'compose', 'version', '--short'], timeout=5)
                if info.get('ID') and info.get('OSType') == 'linux':
                    endpoint = candidate
                    break
            except (OSError, ValueError, RuntimeError):
                pass
    rows.append({'id': 'docker', 'label': 'Docker', 'available': endpoint is not None,
                 'reason': 'A local Linux Docker daemon and Compose are available.' if endpoint else 'Start an installed local Docker daemon with Compose and allow this app’s user to access it. Setup does not install Docker.',
                 **({'dockerHost': endpoint, 'executable': docker} if endpoint else {})})
    incus = executable('incus')
    available = False
    if incus:
        try:
            info = json.loads(command([incus, '--force-local', 'query', '/1.0'], timeout=5))
            available = bool(info.get('environment', {}).get('certificate_fingerprint'))
        except (OSError, ValueError, RuntimeError):
            pass
    rows.append({'id': 'incus', 'label': 'Incus', 'available': available,
                 'reason': 'A local Incus daemon is available.' if available else 'Start and initialize an installed local Incus daemon, then allow this app’s user to access it. Setup does not install Incus.',
                 **({'executable': incus} if available else {})})
    return rows


async def readiness(app, *, force=False):
    from .task_environments import configuration, invoke
    from . import test_capacity_settings
    config, state, reason = await settled_thread(configuration, app)
    if config and config.get('remoteRunner'):
        return {'status': 'ready', 'reason': reason, 'remote': True, 'backend': 'incus'}
    if not sys.platform.startswith('linux'):
        return {'status': state, 'reason': reason}
    raw = read_json(Path(app.data_dir) / 'test-environments.json')
    installs = [{'id': row.get('id'), 'status': row.get('status'), 'commit': row.get('commit'), 'python': row.get('python')} for row in getattr(getattr(app, 'smart_tools', None), 'state', {}).get('installations', [])]
    try:
        effective = test_capacity_settings.apply(app, config) if config else None
    except (OSError, ValueError, TypeError):
        return {'status': 'unavailable', 'reason': 'The saved testing capacity settings need attention. Open Settings → Testing.'}
    key = hashlib.sha256(json.dumps([raw, installs, effective], sort_keys=True).encode()).hexdigest()
    cached = getattr(app, '_test_setup_readiness', None)
    if not force and cached and cached['key'] == key and time.monotonic() - cached['at'] < 30:
        return cached['value']
    config, status, reason = await settled_thread(configuration, app)
    if config is None:
        return {'status': status, 'reason': reason}
    if read_json(Path(app.data_dir) / 'test-environments.json') != raw:
        return {'status': 'unavailable', 'reason': 'The runner configuration changed during inspection. Refresh its status.'}
    try:
        if test_capacity_settings.apply(app, config) != effective:
            return {'status': 'unavailable', 'reason': 'The runner or its capacity settings changed during inspection. Refresh its status.'}
        # Setup reports the same policy as guarded launch admission. The retained
        # backend configuration remains the exact identity for cleanup owners.
        observed = await settled_thread(invoke, effective, app.data_dir, {}, 'capacity', {})
        if not observed.get('ok') or not isinstance(observed.get('capacity'), dict):
            raise ValueError('The configured daemon did not answer.')
        capacity = observed['capacity']
        blockers = capacity.get('blockers', [])
        await settled_thread(inspect_base_image, config)
        if (read_json(Path(app.data_dir) / 'test-environments.json') != raw
                or test_capacity_settings.apply(app, config) != effective):
            return {'status': 'unavailable', 'reason': 'The runner or its capacity settings changed during inspection. Refresh its status.'}
        proof_path = root(app, create=False) / ('verified-' + hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest() + '.json')
        proof = read_json(proof_path)
        verified = bool(proof and proof.get('configuration') == raw and proof.get('proof', {}).get('probeRemoved'))
        value = {'status': 'ready' if verified else 'configured', 'reason': 'The configured runner and approved base image are reachable. Each project still needs its own dependency preparation.' if verified else 'The runner is reachable, but this configuration still needs its isolated readiness check. Run setup to verify it.', 'backendReachable': True,
                 'capacity': capacity, 'available': not blockers, 'backend': config.get('backend', 'docker'), 'observedAt': time.time()}
        app._test_setup_readiness = {'key': key, 'at': time.monotonic(), 'value': value}
        return value
    except (OSError, ValueError, RuntimeError, TypeError):
        return {'status': 'unavailable', 'reason': 'The saved runner could not be verified. Check that its local daemon is running and the approved installation is still available.'}


async def read(app):
    from .task_environments import configuration
    config, state, reason = await settled_thread(configuration, app)
    if config and config.get('remoteRunner'):
        return {'status': 'ready', 'reason': reason, 'remote': True, 'backend': 'incus',
                'requestId': None, 'backends': [], 'operation': None}
    if not sys.platform.startswith('linux'):
        return {'status': state, 'reason': reason, 'requestId': None, 'backends': [], 'operation': None}
    cached = getattr(app, '_test_setup_discovery', None)
    if cached and time.monotonic() - cached['at'] < 60:
        choices = cached['value']
    else:
        choices = await settled_thread(discover)
        app._test_setup_discovery = {'at': time.monotonic(), 'value': choices}
    observed = await readiness(app)
    folder = root(app, create=False)
    latest = read_json(folder / 'latest.json')
    record = read_json(folder / (latest['id'] + '.json')) if latest else None
    if record and record['state'] in ACTIVE:
        lock = FileLock(str(folder / 'setup.lock'), timeout=0)
        try:
            lock.acquire()
        except Timeout:
            pass
        else:
            try:
                if record['id'] not in getattr(app, '_test_setup_tasks', {}):
                    record.update(state='outcome_unknown', detail='Setup was interrupted. Its retained resources were not replayed. Inspect the saved setup result before starting again.', updatedAt=time.time())
                    # Passive observation reports uncertainty without rewriting the original receipt.
            finally:
                lock.release()
    return {**observed, 'requestId': record['intent']['requestId'] if record else None, 'backends': [{key: item[key] for key in ('id', 'label', 'available', 'reason')} for item in choices],
            'operation': public(record) if record else None}


def same_owner(record, args, actor, authority, origin=None):
    # A native actor is attribution, not a second lifetime owner. The domain
    # dispatcher alone supplies checked Full authority or trusted UI origin.
    # Neither value comes from the public action arguments.
    return (record['intent']['sessionId'] == args['sessionId']
            and record['intent']['workspaceId'] == args['workspaceId']
            and (record['actor'] == actor or origin in {'ui', 'user'}
                 or authority is not None and 'accessRevision' in authority))


async def request(app, args, actor, guard=None, *, authority=None, origin=None):
    folder = root(app)
    identity = hashlib.sha256(json.dumps([args['sessionId'], args['requestId']], separators=(',', ':')).encode()).hexdigest()[:32]
    intent = {key: args[key] for key in ('workspaceId', 'sessionId', 'requestId')}
    intent['backend'] = args.get('backend', 'docker')
    if intent['backend'] not in {'docker', 'incus'}:
        raise ValueError('Choose an available local test backend.')
    path = folder / (identity + '.json')
    existing = read_json(path)
    if existing:
        if existing['intent'] != intent or not same_owner(existing, args, actor, authority, origin):
            raise ValueError('This setup request identity already belongs to another intent.')
        if existing['state'] in {'outcome_unknown', 'failed'} and not existing.get('cleanup') and existing.get('candidateConfiguration') and existing.get('proof', {}).get('probeRemoved') and identity not in getattr(app, '_test_setup_tasks', {}):
            existing = await complete_retained(app, path, existing, guard, actor=actor, authority=authority, origin=origin)
        observed = await readiness(app, force=True)
        if authority is not None and guard:
            await guard()
        # A crash after atomic publication can be settled by the exact retained
        # configuration and earlier successful disposable probe, without effects.
        if existing.get('candidateConfiguration') and existing.get('proof', {}).get('probeRemoved') and read_json(Path(app.data_dir) / 'test-environments.json') == existing['candidateConfiguration'] and observed['status'] == 'ready':
            existing.update(state='completed', phase='ready', detail='The exact verified setup was already saved. No setup effects were repeated.', updatedAt=time.time())
            write_json(path, existing)
        return {'operation': public(existing), 'readiness': observed, 'duplicate': True}
    if not sys.platform.startswith('linux'):
        raise ValueError('Automatic local test setup has only been qualified on Linux.')
    lock = FileLock(str(folder / 'setup.lock'), timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise ValueError('Runner setup is already in progress. Inspect its existing operation.') from exc
    try:
        latest = read_json(folder / 'latest.json')
        previous = read_json(folder / (latest['id'] + '.json')) if latest else None
        if previous and previous['state'] in ACTIVE | {'outcome_unknown'}:
            raise ValueError('A previous setup has an uncertain result. Its saved operation must be reconciled before new setup effects.')
        if guard:
            await guard()
        record = {'id': identity, 'intent': intent, 'actor': actor, 'backend': intent['backend'], 'state': 'accepted',
                  'phase': 'checking', 'detail': 'Checking the local test runner.', 'createdAt': time.time(), 'updatedAt': time.time()}
        if authority is not None:
            record['authority'] = authority
        write_json(path, record)
        write_json(folder / 'latest.json', {'id': identity})
        tasks = getattr(app, '_test_setup_tasks', None)
        if tasks is None:
            tasks = app._test_setup_tasks = {}
        task = asyncio.create_task(_run(app, record, lock, guard), name='test-runner-setup-' + identity)
        tasks[identity] = task
        task.add_done_callback(lambda _: tasks.pop(identity, None))
        return {'operation': public(record), 'duplicate': False}
    except BaseException:
        lock.release()
        raise


async def complete_retained(app, path, record, guard, *, actor=None, authority=None, origin=None):
    """Explicit same-intent retry may publish its already verified candidate."""
    from .task_environments import validate_configuration_value, invoke
    lock = FileLock(str(root(app) / 'setup.lock'), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return record
    try:
        candidate = record['candidateConfiguration']
        selected = read_json(Path(app.data_dir) / 'test-environments.json')
        if selected == candidate:
            return record
        if selected != record.get('priorConfiguration'):
            raise ValueError('Another runner configuration was selected. The previous setup will not replace it.')
        if guard:
            await guard()
        config, status, _ = await settled_thread(validate_configuration_value, app, candidate)
        if status != 'ready' or not config:
            raise ValueError('The retained runner still needs qualification; no setup effects were repeated.')
        await settled_thread(inspect_base_image, config)
        value = await settled_thread(invoke, config, app.data_dir, {}, 'capacity', {})
        scope = (value.get('capacity') or {}).get('scope', {})
        if not value.get('ok') or scope.get('daemon') != record['proof'].get('daemon'):
            raise ValueError('The retained setup backend identity could not be confirmed.')
        if guard:
            await guard()
        if read_json(Path(app.data_dir) / 'test-environments.json') != selected:
            raise ValueError('The runner selection changed before publication.')
        retain_verification(app, candidate, record['proof'])
        write_json(Path(app.data_dir) / 'test-environments.json', candidate)
        if authority is not None or origin in {'ui', 'user'}:
            record['completion'] = {'actor': {**actor, **({'origin': origin} if origin else {})},
                                    'authority': authority, 'completedAt': time.time()}
        record.update(state='completed', phase='ready', detail='The earlier verified setup is now saved. Its install, image preparation and check were not repeated.', updatedAt=time.time())
        write_json(path, record)
        return record
    finally:
        lock.release()


async def close(app):
    tasks = list(getattr(app, '_test_setup_tasks', {}).values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _run(app, record, lock, guard):
    from . import task_environments as owner
    folder = root(app)
    path = folder / (record['id'] + '.json')
    async def step(phase, detail):
        if guard:
            await guard()
        record.update(state='running', phase=phase, detail=detail, updatedAt=time.time())
        write_json(path, record)
    try:
        await step('checking', 'Checking the local test backend.')
        existing, _, _ = await settled_thread(owner.configuration, app)
        if existing:
            if existing.get('backend', 'docker') != record['backend']:
                raise ValueError('A different backend is already configured. Existing resources keep their original runner; setup does not silently replace it.')
            state = await readiness(app, force=True)
            if not state.get('backendReachable'):
                raise ValueError(state['reason'])
            if state['status'] == 'ready':
                if guard:
                    await guard()
                record.update(state='completed', phase='ready', detail=state['reason'])
                return
            if not state.get('available'):
                raise ValueError('The runner is busy or low on storage. Wait for capacity before verifying its base environment.')
            owned = folder / record['id']
            owned.mkdir(mode=0o700, exist_ok=False)
            raw = read_json(Path(app.data_dir) / 'test-environments.json')
            normalized, _, _ = await settled_thread(owner.validate_configuration_value, app, raw)
            if normalized != existing:
                raise ValueError('The runner configuration changed before its readiness check.')
            proof = await verify_existing(existing, raw, owned, record, step)
            record.update(candidateConfiguration=raw, proof=proof)
            await step('saving', 'Saving the isolated readiness check for this existing runner.')
            if read_json(Path(app.data_dir) / 'test-environments.json') != raw:
                raise ValueError('The runner configuration changed during its readiness check.')
            retain_verification(app, raw, proof)
            record.update(state='completed', phase='ready', detail='The existing runner passed an isolated offline check. Its configuration is unchanged.')
            return
        config_before = read_json(Path(app.data_dir) / 'test-environments.json')
        record['priorConfiguration'] = config_before
        write_json(path, record)
        selected = next(row for row in await settled_thread(discover) if row['id'] == record['backend'])
        if not selected['available']:
            raise ValueError(selected['reason'])
        await step('installing_runner', 'Preparing the app’s approved test runner. This may download its package dependencies.')
        installed = await app.smart_tools.install({'repository': owner.REPOSITORY, 'ref': owner.DTU_COMMIT, 'path': '.', 'extras': []})
        if installed.get('commit') != owner.DTU_COMMIT or installed.get('extras'):
            raise ValueError('The managed runner did not match the approved core.')
        record['installationId'] = installed['id']
        record['runnerSourcePolicy'] = {'version': 1, 'repository': owner.REPOSITORY,
                                        'ref': 'main', 'origin': 'app-setup', 'setupId': record['id']}
        write_json(path, record)
        base = {'enabled': True, 'installationId': installed['id'], 'commit': owner.DTU_COMMIT, 'backend': record['backend']}
        owned = folder / record['id']
        owned.mkdir(mode=0o700, exist_ok=False)
        if record['backend'] == 'docker':
            config, proof = await setup_docker(base, selected, owned, record, step)
        else:
            config, proof = await setup_incus(base, selected, owned, record, step)
        record.update(candidateConfiguration=config, proof=proof)
        await step('saving', 'Saving the verified runner for this app.')
        normalized, status, _ = await settled_thread(owner.validate_configuration_value, app, config)
        if status != 'ready' or not normalized:
            raise ValueError('The prepared runner did not pass host qualification.')
        if guard:
            await guard()
        if read_json(Path(app.data_dir) / 'test-environments.json') != config_before:
            raise ValueError('The runner configuration changed during setup. The new setup result was retained without replacing it.')
        retain_verification(app, config, proof)
        write_json(Path(app.data_dir) / 'test-environments.json', config)
        record.update(state='completed', phase='ready', detail='Isolated tests are ready. Queue a check to prepare this project’s dependencies automatically.', proof=proof)
    except asyncio.CancelledError:
        record.update(state='outcome_unknown', detail='Setup was interrupted. Its retained resources were not repeated or removed.')
    except Exception as exc:
        # Every external effect is preceded by a durable phase. A failed command
        # may have completed remotely; never turn uncertainty into a retry.
        uncertain = record.get('phase') in {'preparing_image', 'checking_profile', 'creating_backend', 'installing_runner', 'saving'}
        record.update(state='outcome_unknown' if uncertain else 'failed', detail=str(exc)[:1000])
    finally:
        record['updatedAt'] = time.time()
        write_json(path, record)
        lock.release()


def retain_verification(app, config, proof):
    identity = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    write_json(root(app) / ('verified-' + identity + '.json'), {'configuration': config, 'proof': proof})
    app._test_setup_readiness = None


def inspect_base_image(config):
    if config.get('backend', 'docker') == 'docker':
        binary = executable('docker')
        if not binary:
            raise ValueError('The Docker client is unavailable.')
        info = json.loads(command([binary, '--host', config['dockerHost'], 'image', 'inspect', config['image'], '--format', '{{json .}}']))
        if not info.get('Id'):
            raise ValueError('The approved base image is unavailable.')
    else:
        current = config['incus']
        env = {'PATH': os.defpath, 'HOME': current['configDir'], 'LANG': 'C.UTF-8', 'INCUS_CONF': current['configDir']}
        rows = json.loads(command([current['incusExecutable'], '--force-local', '--project', current['project'], 'image', 'list', 'local:', current['image'], '--format', 'json'], env=env))
        if not any(row.get('fingerprint') == current['image'] for row in rows):
            raise ValueError('The approved base image is unavailable.')


async def verify_existing(config, raw, owned, record, step):
    if config.get('backend', 'docker') == 'docker':
        _, proof = await setup_docker(raw, {'executable': executable('docker'), 'dockerHost': config['dockerHost']}, owned, record, step)
        return proof
    current = config['incus']
    env = {'PATH': os.defpath, 'HOME': str(owned), 'LANG': 'C.UTF-8', 'INCUS_CONF': current['configDir']}
    prefix = [current['incusExecutable'], '--force-local', '--project', current['project']]
    name = 'setup-' + record['id'][:16]
    write_json(owned / 'probe.json', {'backend': 'incus', 'name': name, 'project': current['project'], 'configDir': current['configDir'], 'daemon': current.get('daemonFingerprint'), 'existingProject': True})
    await step('checking_profile', 'Running a disposable offline readiness check without changing the existing runner.')
    pools = json.loads(await settled_thread(command, [current['incusExecutable'], '--force-local', 'storage', 'list', '--format', 'json'], env=env))
    pool = next((item['name'] for item in pools if item.get('status') == 'Created'), None)
    if not pool:
        raise ValueError('The existing Incus runner needs an initialized storage pool.')
    async def effect(args):
        await step(record['phase'], record['detail'])
        identity = hashlib.sha256(json.dumps(args).encode()).hexdigest()[:20]
        return await settled_thread(tracked_command, owned / ('effect-' + identity + '.json'), 'probe', [*prefix, *args], env=env, timeout=120)
    await effect(['init', 'local:' + current['image'], name, '--no-profiles', '--storage', pool, '-c', 'user.amplifier.setup=' + record['id'], '-c', 'security.privileged=false', '-c', 'security.nesting=false', '-c', 'limits.memory=512MiB', '-c', 'limits.cpu=1'])
    await effect(['start', name])
    probe = json.loads(await settled_thread(command, [*prefix, 'exec', name, '--', 'python3', '-c', "import json,sys,tarfile; assert hasattr(tarfile,'data_filter'); print(json.dumps({'python':sys.version.split()[0]}))"], env=env, timeout=60))
    instance = json.loads(await settled_thread(command, [current['incusExecutable'], '--force-local', 'query', '/1.0/instances/' + name + '?project=' + current['project']], env=env))
    if any(device.get('type') == 'nic' for device in instance.get('expanded_devices', {}).values()):
        raise ValueError('The readiness probe unexpectedly has network access.')
    await effect(['stop', name])
    await effect(['delete', name])
    remaining = json.loads(await settled_thread(command, [*prefix, 'list', name, '--format', 'json'], env=env))
    if remaining:
        raise ValueError('The readiness probe absence was not confirmed.')
    return {'backend': 'incus', 'daemon': current.get('daemonFingerprint'), 'image': current['image'], 'probe': probe, 'offline': True, 'probeRemoved': True, 'checkedAt': time.time()}


async def setup_docker(base, selected, owned, record, step):
    from .task_environments import APPROVED_IMAGE
    from . import test_host_resources
    binary, endpoint = selected['executable'], selected['dockerHost']
    target = await settled_thread(test_host_resources.local_target, endpoint, {'dockerd'})
    config = {**base, 'dockerHost': endpoint, 'image': APPROVED_IMAGE,
              **capacity_defaults(resource_admission=target.get('supported') is True)}
    await step('preparing_image', 'Downloading the approved Python base image. Project dependencies are prepared separately.')
    await settled_thread(command, [binary, '--host', endpoint, 'pull', APPROVED_IMAGE], timeout=600)
    await step('checking_profile', 'Running a disposable offline Python check in the approved image.')
    name = 'unified-setup-' + record['id'][:20]
    daemon = json.loads(await settled_thread(command, [binary, '--host', endpoint, 'info', '--format', '{{json .}}']))
    write_json(owned / 'probe.json', {'backend': 'docker', 'name': name, 'dockerHost': endpoint, 'daemon': daemon['ID']})
    await step('checking_profile', 'Running a disposable offline Python check in the approved image.')
    result = await settled_thread(tracked_command, owned / 'effect-docker-probe.json', 'probe', [binary, '--host', endpoint, 'run', '--rm', '--name', name,
        '--label', 'io.amplifier.setup=' + record['id'], '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--memory', '256m', '--pids-limit', '64',
        APPROVED_IMAGE, 'python', '-c', "import json,sys,tarfile; assert hasattr(tarfile,'data_filter'); print(json.dumps({'python':sys.version.split()[0],'offline':True}))"], timeout=60)
    proof = json.loads(result)
    containers = await settled_thread(command, [binary, '--host', endpoint, 'ps', '-aq', '--filter', 'name=^/' + name + '$'])
    if containers:
        raise ValueError('The setup check finished but its container absence was not confirmed.')
    daemon = json.loads(await settled_thread(command, [binary, '--host', endpoint, 'info', '--format', '{{json .}}']))
    return config, {'backend': 'docker', 'daemon': daemon['ID'], 'image': APPROVED_IMAGE, 'probe': proof, 'probeRemoved': True, 'checkedAt': time.time()}


async def setup_incus(base, selected, owned, record, step):
    from .incus_environment_worker import file_digest, package_digest, QUALIFIED_PACKAGES, CURRENT_QUALIFIED_VERSION
    record['engineSourcePolicy'] = {'version': 1, 'repository': LEGACY_REPOSITORY,
                                    'ref': 'main', 'origin': 'app-setup', 'setupId': record['id']}
    binary = selected['executable']
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('The app’s managed package installer is unavailable. Install the supported app package and try setup again.')
    await step('installing_runner', 'Preparing a private copy of the approved Incus adapter. Existing tools are unchanged.')
    venv = owned / 'adapter'
    await settled_thread(command, [uv, 'venv', '--python', sys.executable, str(venv)], timeout=120)
    python = venv / 'bin/python'
    await settled_thread(command, [uv, 'pip', 'install', '--python', str(python), 'git+' + LEGACY_REPOSITORY + '@' + LEGACY_COMMIT], timeout=600)
    package = json.loads(await settled_thread(command, [str(python), '-I', '-c', 'import importlib.util,importlib.metadata,json; print(json.dumps({"directory":next(iter(importlib.util.find_spec("amplifier_bundle_digital_twin_universe").submodule_search_locations)),"version":importlib.metadata.version("amplifier-bundle-digital-twin-universe")}))']))
    if package.get('version') != CURRENT_QUALIFIED_VERSION or package_digest(package['directory']) != QUALIFIED_PACKAGES[CURRENT_QUALIFIED_VERSION]:
        raise ValueError('The installed Incus adapter did not match the approved package bytes.')
    client, admission = owned / 'client', owned / 'admission'
    client.mkdir(mode=0o700)
    admission.mkdir(mode=0o700)
    # This private client starts with a fixed local default and one official
    # HTTPS image remote. No user credentials or remote aliases are inherited.
    (client / 'config.yml').write_text('default-remote: local\nremotes:\n  local:\n    addr: unix://\n    public: false\n  unified-images:\n    addr: ' + INCUS_IMAGE_SERVER + '\n    protocol: simplestreams\n    public: true\n')
    (client / 'config.yml').chmod(0o600)
    env = {'PATH': os.defpath, 'HOME': str(owned), 'LANG': 'C.UTF-8', 'INCUS_CONF': str(client)}
    project, bridge = 'unified-' + record['id'][:20], 'auf' + record['id'][:12]
    async def incus(args, timeout=30, project_scope=True):
        await step(record['phase'], record['detail'])
        if args[0] == 'query' and project_scope:
            args = [args[0], args[1] + ('&' if '?' in args[1] else '?') + 'project=' + project]
        remote_image = any(item.startswith('unified-images:') for item in args)
        # --force-local discards client remotes. The only remote request uses
        # our private fixed HTTPS image source and an explicit local target.
        argv = [binary, *([] if remote_image else ['--force-local']), *(['--project', project] if project_scope and args[0] != 'query' else []), *args]
        kind = {'project': 'project', 'profile': 'project', 'network': 'network', 'init': 'probe', 'start': 'probe', 'stop': 'probe', 'delete': 'probe'}.get(args[0])
        mutation = args[0] in {'init', 'start', 'stop', 'delete'} or args[:2] in [['project', 'create'], ['network', 'create'], ['profile', 'device']]
        if mutation:
            identity = hashlib.sha256(json.dumps(args).encode()).hexdigest()[:20]
            return await settled_thread(tracked_command, owned / ('effect-' + identity + '.json'), kind, argv, env=env, timeout=timeout)
        return await settled_thread(command, argv, env=env, timeout=timeout)
    await step('creating_backend', 'Creating a private test project and its dependency-download network.')
    server = json.loads(await incus(['query', '/1.0'], project_scope=False))['environment']
    initial_daemon = server['certificate_fingerprint']
    write_json(owned / 'resources.json', {'backend': 'incus', 'project': project, 'network': bridge, 'owner': record['id'], 'daemon': initial_daemon})
    await incus(['project', 'create', project, '-c', 'features.images=true', '-c', 'features.profiles=true', '-c', 'features.networks=false', '-c', 'user.amplifier.setup=' + record['id']], project_scope=False)
    # Incus bridges live in the default network project. Only this unique owned
    # bridge is created; no shared profile, network, storage, or daemon is edited.
    await incus(['network', 'create', bridge, 'ipv4.address=auto', 'ipv4.nat=true', 'ipv6.address=none', 'user.amplifier.setup=' + record['id']], project_scope=False)
    await step('preparing_image', 'Downloading the approved Ubuntu base image into the private test project.')
    alias = 'unified-base-' + record['id'][:12]
    remote_images = json.loads(await incus(['image', 'list', 'unified-images:' + INCUS_IMAGE_ALIAS, '--format', 'json'], timeout=60, project_scope=False))
    matches = [image for image in remote_images if image.get('type') == 'container' and image.get('architecture') == server['architectures'][0]]
    if len(matches) != 1 or not re.fullmatch(r'[a-f0-9]{64}', matches[0].get('fingerprint', '')):
        raise ValueError('The approved Ubuntu image is not available for this host architecture.')
    approved_fingerprint = matches[0]['fingerprint']
    await incus(['image', 'copy', 'unified-images:' + approved_fingerprint, 'local:', '--target-project', project, '--alias', alias], timeout=600, project_scope=False)
    images = json.loads(await incus(['image', 'list', alias, '--format', 'json']))
    if not isinstance(images, list) or len(images) != 1:
        raise ValueError('The approved image alias did not resolve to one exact local image.')
    info = images[0]
    image = info.get('fingerprint', '')
    if image != approved_fingerprint or info.get('auto_update') or info.get('cached'):
        raise ValueError('The downloaded base image is not a retained exact image.')
    daemon = json.loads(await incus(['query', '/1.0']))['environment']['certificate_fingerprint']
    launcher = venv / 'bin/amplifier-digital-twin'
    config = {**base, 'incus': {'executable': str(launcher), 'executableSha256': file_digest(launcher, 65536)[0],
        'packageSha256': QUALIFIED_PACKAGES[CURRENT_QUALIFIED_VERSION], 'incusExecutable': binary, 'configDir': str(client),
        'admissionDir': str(admission), 'project': project, 'image': image,
        'daemonFingerprint': daemon, 'preparationNetwork': bridge, **capacity_defaults()}}
    from .incus_environment_worker import host_target
    target = await settled_thread(host_target, config['incus'], env)
    config['incus'].update(capacity_defaults(resource_admission=target.get('supported') is True))
    await step('checking_profile', 'Running a disposable offline check in the private test project.')
    name = 'setup-' + record['id'][:16]
    write_json(owned / 'probe.json', {'backend': 'incus', 'name': name, 'project': project})
    # A root disk is required even without inherited profiles. Pick only an
    # already configured pool; never create or reconfigure host storage.
    pools = json.loads(await incus(['storage', 'list', '--format', 'json'], project_scope=False))
    pool = next((item['name'] for item in pools if item.get('status') == 'Created'), None)
    if not pool:
        raise ValueError('Incus needs an existing initialized storage pool. Setup does not configure host disks.')
    config['incus']['preparationStorage'] = pool
    # The qualified DTU adapter discovers admission storage from this private
    # project's default profile. Add only a root disk; no inherited host NIC.
    await incus(['profile', 'device', 'add', 'default', 'root', 'disk', 'path=/', 'pool=' + pool])
    await incus(['init', 'local:' + image, name, '--no-profiles', '--storage', pool,
                 '-c', 'user.amplifier.setup=' + record['id'], '-c', 'security.privileged=false', '-c', 'security.nesting=false', '-c', 'limits.memory=512MiB', '-c', 'limits.cpu=1'], timeout=120)
    await incus(['start', name], timeout=120)
    probe = json.loads(await incus(['exec', name, '--', 'python3', '-c', "import json,sys,tarfile; assert hasattr(tarfile,'data_filter'); print(json.dumps({'python':sys.version.split()[0]}))"], timeout=60))
    instance = json.loads(await incus(['query', '/1.0/instances/' + name]))
    if any(item.get('type') == 'nic' for item in instance.get('expanded_devices', {}).values()):
        raise ValueError('The readiness check unexpectedly inherited network access.')
    await incus(['stop', name], timeout=120)
    await incus(['delete', name], timeout=120)
    remaining = json.loads(await incus(['list', name, '--format', 'json']))
    if remaining:
        raise ValueError('The setup check did not confirm its disposable instance was removed.')
    return config, {'backend': 'incus', 'daemon': daemon, 'image': image, 'imageSource': INCUS_IMAGE_SERVER + '/' + INCUS_IMAGE_ALIAS,
                    'probe': probe, 'offline': True, 'probeRemoved': True, 'checkedAt': time.time()}


def original(app, args, actor, *, authority=None, origin=None):
    identity = hashlib.sha256(json.dumps([args['sessionId'], args['requestId']], separators=(',', ':')).encode()).hexdigest()[:32]
    path = root(app, create=False) / (identity + '.json')
    row = read_json(path)
    if not row or not same_owner(row, args, actor, authority, origin):
        raise ValueError('This setup result belongs to another root or workspace.')
    return path, row


def cleanup_allowed(app, record):
    if record['state'] == 'completed':
        raise ValueError('A ready runner cannot be removed by interrupted-setup cleanup.')
    selected = read_json(Path(app.data_dir) / 'test-environments.json')
    if selected and (selected == record.get('candidateConfiguration') or selected.get('incus', {}).get('project') == 'unified-' + record['id'][:20]):
        raise ValueError('This setup is the selected runner. Inspect it instead of removing its resources.')
    target = Path(app.data_dir) / 'workspace-resources/reservations.sqlite3'
    if target.exists():
        if target.is_symlink():
            raise ValueError('The resource owner ledger was replaced.')
        with sqlite3.connect(target.as_uri() + '?mode=ro', uri=True) as db:
            for (body,) in db.execute("SELECT body FROM allocations WHERE status!='retired' AND json_extract(body,'$.kind')='test-environment'"):
                row = json.loads(body)
                candidate = record.get('candidateConfiguration')
                if candidate:
                    from .task_environments import validate_configuration_value, config_key
                    normalized, _, _ = validate_configuration_value(app, candidate)
                    if normalized and row.get('backendKey') == config_key(normalized):
                        raise ValueError('An active task still references this setup. Its resources are preserved.')


def observe_partial(app, record):
    """Read-only inventory of this exact setup, independent of cached status."""
    owned = root(app, create=False) / record['id']
    resources, probe = read_json(owned / 'resources.json'), read_json(owned / 'probe.json')
    unsettled = {row['kind'] for item in owned.glob('effect-*.json') if (row := read_json(item)) and row['state'] != 'completed'}
    matched = set(record.get('cleanup', {}).get('observedResources', []))
    if record['backend'] == 'docker':
        if not probe:
            return {'absent': True, 'backend': 'docker'}
        binary = executable('docker')
        if not binary:
            raise ValueError('Docker is unavailable; the old setup probe cannot be observed.')
        prefix = [binary, '--host', probe['dockerHost']]
        daemon = json.loads(command([*prefix, 'info', '--format', '{{json .}}']))
        if daemon['ID'] != probe['daemon']:
            raise ValueError('The Docker daemon changed. The original setup remains unresolved.')
        value = command([*prefix, 'ps', '-a', '--filter', 'name=^/' + probe['name'] + '$', '--format', '{{json .}}'])
        rows = [json.loads(line) for line in value.splitlines()]
        for item in rows:
            labels = item.get('Labels', {})
            if isinstance(labels, str):
                labels = dict(value.split('=', 1) for value in labels.split(',') if '=' in value)
            if labels.get('io.amplifier.setup') != record['id']:
                raise ValueError('The Docker probe owner does not match this setup.')
        if not rows and 'probe' in unsettled and 'probe' not in matched:
            raise ValueError('The original probe launch has not settled. Absence alone cannot close a pending launch; keep this setup receipt and inspect it again after backend recovery.')
        return {'absent': not rows, 'backend': 'docker', 'probe': probe, 'containers': rows, 'prefix': prefix, 'observedResources': ['probe'] if rows else []}
    if not resources and probe and probe.get('existingProject'):
        binary = executable('incus')
        if not binary:
            raise ValueError('Incus is unavailable; the readiness probe cannot be inspected.')
        env = {'PATH': os.defpath, 'HOME': str(owned), 'LANG': 'C.UTF-8', 'INCUS_CONF': probe['configDir']}
        prefix = [binary, '--force-local']
        daemon = json.loads(command([*prefix, 'query', '/1.0'], env=env))['environment']['certificate_fingerprint']
        if probe.get('daemon') and probe['daemon'] != daemon:
            raise ValueError('The original readiness daemon identity changed.')
        rows = json.loads(command([*prefix, '--project', probe['project'], 'list', probe['name'], '--format', 'json'], env=env))
        if any(row['name'] != probe['name'] or row.get('config', {}).get('user.amplifier.setup') != record['id'] for row in rows):
            raise ValueError('The readiness probe owner does not match this setup.')
        if not rows and 'probe' in unsettled and 'probe' not in matched:
            raise ValueError('The original readiness launch remains unsettled; absence is not proof of cancellation.')
        return {'absent': not rows, 'backend': 'incus', 'resources': {'project': probe['project']}, 'probe': probe, 'instances': rows, 'project': None, 'images': [], 'network': None, 'prefix': prefix, 'env': env, 'observedResources': ['probe'] if rows else []}
    if not resources:
        return {'absent': True, 'backend': 'incus'}
    binary = executable('incus')
    if not binary:
        raise ValueError('Incus is unavailable; the old setup cannot be observed.')
    env = {'PATH': os.defpath, 'HOME': str(owned), 'LANG': 'C.UTF-8', 'INCUS_CONF': str(owned / 'client')}
    prefix = [binary, '--force-local']
    daemon = json.loads(command([*prefix, 'query', '/1.0'], env=env))['environment']['certificate_fingerprint']
    if daemon != resources['daemon']:
        raise ValueError('The Incus daemon changed. The original setup remains unresolved.')
    projects = json.loads(command([*prefix, 'project', 'list', '--format', 'json'], env=env))
    project = next((row for row in projects if row['name'] == resources['project']), None)
    if project and project.get('config', {}).get('user.amplifier.setup') != record['id']:
        raise ValueError('The Incus project owner does not match this setup.')
    instances = json.loads(command([*prefix, '--project', resources['project'], 'list', '--format', 'json'], env=env)) if project else []
    for row in instances:
        if not probe or row['name'] != probe['name'] or row.get('config', {}).get('user.amplifier.setup') != record['id']:
            raise ValueError('The setup project contains another environment. Cleanup will not touch it.')
    networks = json.loads(command([*prefix, 'network', 'list', '--format', 'json'], env=env))
    network = next((row for row in networks if row['name'] == resources['network']), None)
    if network and (network.get('config', {}).get('user.amplifier.setup') != record['id'] or network.get('used_by')):
        raise ValueError('The setup network has another owner or is still in use.')
    present = {name for name, value in [('project', project), ('network', network), ('probe', instances)] if value}
    if unsettled - present - matched:
        raise ValueError('A prior setup admission has not settled. Resource absence alone cannot prove the pending request will not complete; retain this receipt for backend recovery.')
    images = json.loads(command([*prefix, '--project', resources['project'], 'image', 'list', '--format', 'json'], env=env)) if project else []
    return {'absent': not instances and not network, 'backend': 'incus', 'resources': resources, 'probe': probe,
            'instances': instances, 'project': project, 'images': images, 'network': network, 'prefix': prefix, 'env': env, 'observedResources': sorted(present)}


async def reconcile(app, args, actor, guard=None, *, authority=None, origin=None):
    path, record = original(app, args, actor, authority=authority, origin=origin)
    if guard:
        await guard()
    tasks = getattr(app, '_test_setup_tasks', {})
    if record['id'] in tasks:
        return {'operation': public(record), 'active': True}
    observed = await readiness(app, force=True)
    if record.get('candidateConfiguration') and record.get('proof', {}).get('probeRemoved') and read_json(Path(app.data_dir) / 'test-environments.json') == record['candidateConfiguration'] and observed['status'] == 'ready':
        record.update(state='completed', phase='ready', detail='The exact verified setup is saved. No setup effects were repeated.', updatedAt=time.time())
    elif record['state'] != 'completed':
        cleanup_allowed(app, record)
        partial = await settled_thread(observe_partial, app, record)
        if partial['absent']:
            record.setdefault('originalState', record['state'])
            record.update(state='recovered', phase='retained', detail='No disposable setup resources remain. Installed packages, base images and receipts are kept; a new setup request can proceed.', updatedAt=time.time())
            record.setdefault('cleanup', {}).update(state='completed', observedAt=time.time(), resourcesAbsent=True)
        else:
            record.update(detail='The original setup still owns a disposable probe or unused network. Use Clean up interrupted setup before starting again.', updatedAt=time.time())
    if guard:
        await guard()
    if authority is not None or origin in {'ui', 'user'}:
        record['reconciliation'] = {'actor': {**actor, **({'origin': origin} if origin else {})},
                                    'authority': authority, 'observedAt': time.time()}
    write_json(path, record)
    return {'operation': public(record), 'readiness': observed}


async def cleanup(app, args, actor, guard=None, *, authority=None, origin=None):
    path, record = original(app, args, actor, authority=authority, origin=origin)
    if record.get('cleanup'):
        return {'operation': public(record), 'duplicate': True}
    cleanup_allowed(app, record)
    lock = FileLock(str(root(app) / 'setup.lock'), timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise ValueError('A setup operation is still running. Inspect it before cleanup.') from exc
    try:
        if guard:
            await guard()
        record['cleanup'] = {'state': 'accepted', 'createdAt': time.time()}
        if authority is not None or origin in {'ui', 'user'}:
            record['cleanup'].update(actor={**actor, **({'origin': origin} if origin else {})}, authority=authority)
        write_json(path, record)
        tasks = getattr(app, '_test_setup_tasks', None)
        if tasks is None:
            tasks = app._test_setup_tasks = {}
        task = asyncio.create_task(_cleanup(app, record, path, lock, guard))
        tasks[record['id']] = task
        task.add_done_callback(lambda _: tasks.pop(record['id'], None))
        return {'operation': public(record)}
    except BaseException:
        lock.release()
        raise


async def _cleanup(app, record, path, lock, guard):
    async def effect(argv, **kwargs):
        if guard:
            await guard()
        cleanup_allowed(app, record)
        return await settled_thread(command, argv, **kwargs)
    try:
        partial = await settled_thread(observe_partial, app, record)
        record['cleanup'].update(state='running', observedResources=partial.get('observedResources', []))
        write_json(path, record)
        if partial['backend'] == 'docker' and not partial['absent']:
            prefix, name = partial['prefix'], partial['probe']['name']
            await effect([*prefix, 'stop', name], timeout=120)
            await effect([*prefix, 'rm', name], timeout=120)
        elif partial['backend'] == 'incus' and partial.get('resources'):
            prefix, env, project = partial['prefix'], partial['env'], partial['resources']['project']
            for item in partial['instances']:
                if item.get('status') != 'Stopped':
                    await effect([*prefix, '--project', project, 'stop', item['name']], env=env, timeout=120)
                await effect([*prefix, '--project', project, 'delete', item['name']], env=env, timeout=120)
            if partial['network']:
                await effect([*prefix, 'network', 'delete', partial['network']['name']], env=env)
            # An image-bearing project is deliberately retained. Empty projects
            # may be removed, but never delete the approved downloaded image.
            if partial['project'] and not partial['images']:
                await effect([*prefix, 'project', 'delete', project], env=env)
        after = await settled_thread(observe_partial, app, record)
        if not after['absent']:
            raise ValueError('Disposable setup resource absence was not confirmed.')
        if guard:
            await guard()
        record.setdefault('originalState', record['state'])
        record.update(state='recovered', phase='retained', detail='Interrupted setup resources were removed. Packages, base images and private receipts are kept. A new setup request can proceed.')
        record['cleanup'].update(state='completed', resourcesAbsent=True)
    except BaseException as exc:
        record['cleanup'].update(state='outcome_unknown', detail='Cleanup was interrupted; inspect its existing result.' if isinstance(exc, asyncio.CancelledError) else str(exc)[:1000])
    finally:
        record['cleanup']['updatedAt'] = time.time()
        record['updatedAt'] = time.time()
        write_json(path, record)
        lock.release()
