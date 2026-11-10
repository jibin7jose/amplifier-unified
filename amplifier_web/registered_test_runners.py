"""Operator-approved remote test targets; canonical jobs bind before dispatch."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import uuid
import time

from . import test_capacity_settings as capacity

FILES = ('managed_runner_isolation.py', 'incus_engine_core_binding.py', 'managed_runner_binding.py', 'incus_engine_generations.py', 'managed_runner_identity.py', 'incus_engine_probe.py',
         'managed_incus_target.py', 'test_runner_backend_policy.py', 'test_runner_controller.py', 'incus_environment_worker.py', 'test_environment_worker.py',
         'test_host_resources.py', 'test_environment_profiles.py', 'environment_retention.py',
         'test_job_transport.py', 'test_environment_preparation.py', 'test_source_transfer.py', 'test_dependency_layers.py', 'test_environment_prepare_guest.py',
         'test_environment_initialize_guest.py', 'test_job_guest.py', 'test_origin_snapshot.py', 'test_origin_files.py')
CHUNK = 512 * 1024
MAX_SOURCE = 512 * 1024**2

# Fixed installer: the user selects a known SSH connection and an existing
# operator backend config, never an executable or installation command.
INSTALL = r'''
import base64,hashlib,json,os,pathlib,pwd,stat,sys,tempfile
v=json.loads(sys.stdin.buffer.read(2097153))
if set(v)!={'files','digest','configPath'}: raise ValueError('bad manifest')
if hashlib.sha256(json.dumps(v['files'],sort_keys=True).encode()).hexdigest()!=v['digest']: raise ValueError('bad digest')
home=pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)
p=pathlib.Path(v['configPath']) if v['configPath'] else home/'.amplifier-unified/test-environments.json'
if not p.is_absolute() or any(x.is_symlink() for x in (p,*p.parents)): raise ValueError('bad config path')
if not p.exists():
 print(json.dumps({'error':'setup-required'}));sys.exit(0)
f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
with os.fdopen(f) as s:
 i=os.fstat(s.fileno())
 if not stat.S_ISREG(i.st_mode) or i.st_uid!=os.getuid() or i.st_mode&0o077 or i.st_size>65536: raise ValueError('bad config')
 c=json.load(s)
if c.get('enabled') is not True or c.get('backend')!='incus':
 print(json.dumps({'error':'setup-required'}));sys.exit(0)
config=c['incus']; config_digest=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
root=home/'.local/share/amplifier-test-runners'/v['digest']/config_digest
if any(x.is_symlink() for x in (root,*root.parents)): raise ValueError('linked package')
root.mkdir(mode=0o700,parents=True,exist_ok=True)
if root.stat().st_uid!=os.getuid() or root.stat().st_mode&0o077: raise ValueError('unsafe package')
files={**v['files'],'backend.json':base64.b64encode(json.dumps(config,sort_keys=True).encode()).decode()}
for name,encoded in files.items():
 if name!='backend.json' and not (name.startswith('amplifier_web/') and name.count('/')==1 and name.endswith('.py')): raise ValueError('bad package member')
 content=base64.b64decode(encoded,validate=True)
 if len(content)>1048576: raise ValueError('large package member')
 path=root/name; path.parent.mkdir(mode=0o700,exist_ok=True)
 if any(x.is_symlink() for x in (path,*path.parents)): raise ValueError('linked package member')
 try:
  fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o400)
 except FileExistsError:
  if path.read_bytes()!=content or path.stat().st_mode&0o077: raise ValueError('changed package')
 else:
  with os.fdopen(fd,'wb') as s: s.write(content);s.flush();os.fsync(s.fileno())
print(json.dumps({'controller':str(root/'amplifier_web/test_runner_controller.py'),'digest':v['digest']}))
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ConnectionFailure(ValueError):
    """Only fixed plain-language diagnostics, never remote stderr or secrets."""


def connection_failure(code):
    return ConnectionFailure({
        'setup-required': 'Testing is not set up on this machine. Open its Amplifier Settings, set up a native Linux Incus test runner, then connect again.',
        'trust-required': 'This machine’s SSH identity is not trusted or has changed. Verify it using your normal SSH connection before connecting here.',
        'authentication-required': 'The saved SSH connection needs sign-in. Configure non-interactive access for this account, then connect again.',
        'unavailable': 'This machine could not be reached through the saved SSH connection. Check that it is online and the connection works.',
    }[code])


def definitions(schema, string):
    return {
        'testing.runners.read': ('Read globally registered test machines. Does not start tests or reconnect to targets.', schema()),
        'testing.runners.register': ('Connect and check an approved SSH machine with testing already set up. Installs the fixed controller, waits for existing machine limits, runs a small fixed isolated check and retains its result before cleanup. Only a passed check enables a new machine. Read the saved operation; do not repeat an uncertain request. No host setup or credential copying.', schema({'expectedRevision': string(64), 'requestId': string(200), 'connection': string(200), 'name': string(80), 'configPath': string(4096)}, ['expectedRevision', 'connection', 'name'])),
        'testing.runners.refresh': ('Check a registered connection with this app’s current controller before replacing it for future work. Existing owned work retains its original controller. A failed check leaves the existing registration unchanged.', schema({'expectedRevision': string(64), 'requestId': string(200), 'id': string(64)}, ['expectedRevision', 'id'])),
        'testing.runners.enrollment.cancel': ('Cancel the saved connection check. Its original owned effects must settle and clean up before cancellation is confirmed; it never enables the target.', schema({'id': string(32)}, ['id'])),
        'testing.runners.enrollment.reconcile': ('Observe the original uncertain connection check again. Does not submit a replacement guest or repeat a test.', schema({'id': string(32)}, ['id'])),
        'testing.runners.enable': ('Enable or pause a registered test machine for future checks. Existing owned work keeps its original target and cleanup route.', schema({'expectedRevision': string(64), 'id': string(64), 'enabled': {'type': 'boolean'}}, ['expectedRevision', 'id', 'enabled'])),
        'testing.runners.capacity.read': ('Inspect registered backend limits and their source.', schema({'id': string(64)}, ['id'])),
        'testing.runners.capacity.save': ('Change registered backend limits for future admissions across cooperating controllers on the same target account. Active checks keep running.', schema({'id': string(64), 'expectedRevision': string(64), 'limits': schema({key: {'type': 'integer', 'minimum': 0, 'maximum': capacity.MAXIMUM} for key in ('maxRunning', 'maxInstances', 'minFreeBytes')}, [])}, ['id', 'expectedRevision', 'limits'])),
        'testing.runners.machine.read': ('Inspect the registered machine resource budget and current reservations.', schema({'id': string(64)}, ['id'])),
        'testing.runners.machine.save': ('Change the registered machine shared resource budget for future admissions. Active work remains running. Requires global host-defaults authority.', schema({'id': string(64), 'expectedRevision': string(64), 'limits': schema({key: {'type': 'integer', 'minimum': 0, 'maximum': capacity.MAXIMUM} for key in capacity.host_resources.FIELDS}, [])}, ['id', 'expectedRevision', 'limits'])),
    }


def store(app):
    path = Path(app.data_dir) / 'test-runners.json'
    from .test_origin_files import read
    try:
        value = read(path, 1024 * 1024)
    except FileNotFoundError:
        return {'version': 1, 'runners': []}
    if value.get('version') != 1 or not isinstance(value.get('runners'), list) or len(value['runners']) > 16:
        raise ValueError('The test machine settings are invalid.')
    if not isinstance(value.get('enrollments', []), list) or len(value.get('enrollments', [])) > 32:
        raise ValueError('The saved connection check list is invalid.')
    return value


def publish(app, value):
    from .test_origin_files import replace
    replace(Path(app.data_dir) / 'test-runners.json', value)


def snapshot(app):
    from . import test_runner_enrollment as enrollment
    value = store(app)
    return {'revision': digest(value), 'runners': [{**{key: row[key] for key in ('id', 'name', 'enabled', 'connection', 'machineId', 'profiles', 'configPath')},
            'lastCheck': {key: row.get('lastCheck', {}).get(key) for key in ('status', 'checkedAt', 'profileId', 'requestId')}} for row in value['runners']],
            'enrollments': enrollment.listing(app, value),
            'scope': 'Approved native Linux Incus machines. Each machine shares its budget across cooperating runners using the same OS account. Pausing a machine affects only new checks.'}


def ssh(connection, command, payload, timeout=60, maximum=2*1024*1024):
    if not isinstance(connection, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,199}', connection):
        raise ValueError('Choose an existing SSH connection name without command options.')
    executable = shutil.which('ssh', path=os.environ.get('PATH', os.defpath))
    if not executable:
        raise ValueError('SSH is unavailable on this computer.')
    result = subprocess.run([executable, '-oBatchMode=yes', '-oStrictHostKeyChecking=yes', '-oForwardAgent=no', '-oClearAllForwardings=yes', '-oConnectTimeout=10', connection, shlex.join(command)], input=json.dumps(payload).encode(), capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > maximum:
        diagnostic = result.stderr[:4096].lower()
        if b'host key verification failed' in diagnostic or b'remote host identification has changed' in diagnostic:
            raise connection_failure('trust-required')
        if b'permission denied' in diagnostic and result.returncode == 255:
            raise connection_failure('authentication-required')
        if result.returncode == 255:
            raise connection_failure('unavailable')
        raise ValueError('The approved test machine did not return a bounded response. Check its SSH connection and configured runner.')
    return json.loads(result.stdout)


def bundle():
    files = {'amplifier_web/__init__.py': ''}
    for name in FILES:
        files['amplifier_web/' + name] = base64.b64encode(Path(__file__).with_name(name).read_bytes()).decode()
    return {'files': files, 'digest': digest(files)}


VERIFY = Path(__file__).with_name('test_runner_bootstrap.py').read_text()


def call(runner, payload, timeout=60):
    if not re.fullmatch(r'[a-f0-9]{64}', runner.get('packageDigest', '')):
        raise ValueError('The registered controller identity is invalid.')
    controller = runner['controller']
    if not PurePosixPath(controller).is_absolute() or '..' in PurePosixPath(controller).parts or runner['packageDigest'] not in PurePosixPath(controller).parts or not controller.endswith('/amplifier_web/test_runner_controller.py'):
        raise ValueError('The registered controller path changed.')
    names = runner.get('packageFiles')
    if not isinstance(names, list) or not 1 <= len(names) <= 64 or len(set(names)) != len(names) or not {'__init__.py', 'test_runner_controller.py'} <= set(names) or any(not isinstance(name, str) or not re.fullmatch(r'[a-z_]+\.py', name) for name in names):
        raise ValueError('The retained controller manifest is invalid.')
    request = {'version': 1, 'machineId': runner.get('machineId'), 'backendIdentity': runner.get('backendIdentity'), 'payload': payload}
    return ssh(runner['connection'], ['/usr/bin/python3', '-I', '-B', '-c', VERIFY, controller, runner['packageDigest'], json.dumps(names)], request, timeout)


def register(connection, name, config_path=''):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('Give the test machine a short name.')
    package = bundle()
    installed = ssh(connection, ['/usr/bin/python3', '-I', '-B', '-c', INSTALL], {**package, 'configPath': config_path}, timeout=90)
    if installed.get('error') == 'setup-required':
        raise connection_failure('setup-required')
    runner = {'connection': connection, 'controller': installed['controller'], 'packageDigest': package['digest'], 'packageFiles': ['__init__.py', *FILES], 'configPath': config_path}
    observed = call(runner, {'action': 'describe'})
    if not observed.get('ok') or observed.get('version') != 1:
        raise ValueError('The target needs a configured native Incus runner before registration.')
    runner.update({key: observed[key] for key in ('machineId', 'backendIdentity', 'accountId', 'profiles', 'incus')})
    runner.update(id=digest([runner['machineId'], runner['accountId'], runner['backendIdentity']]), name=name.strip(), enabled=True)
    return runner


def configuration(app, base, runner):
    # Source packaging is an app capability on every origin, not a local Linux
    # runner. All execution authority remains on the registered target.
    return {'enabled': True, 'backend': 'incus', 'incus': copy.deepcopy(runner['incus']),
            'remoteRunner': copy.deepcopy(runner), 'originSnapshotVersion': 1,
            'retentionMode': (base or {}).get('retentionMode', 'files')}


def first_configuration(app):
    runner = next((item for item in store(app)['runners'] if item.get('enabled')), None)
    return configuration(app, None, runner) if runner else None


def candidates(app, base, profile):
    local = [base] if base and not base.get('remoteRunner') else []
    return local + [configuration(app, base, runner) for runner in store(app)['runners'] if runner['enabled'] and profile in runner['profiles']]


def enabled(app, config):
    runner = config.get('remoteRunner')
    if not runner:
        return True
    # Check observations do not change the executable route already retained
    # by queued work. Every other field, including Pause, remains exact.
    expected = {key: value for key, value in runner.items() if key != 'lastCheck'}
    return any(row.get('enabled') and
               {key: value for key, value in row.items() if key != 'lastCheck'} == expected
               for row in store(app)['runners'])


def qualify_retained(app, config):
    # No current registration is required for original cleanup. Qualification is
    # performed by the exact pinned controller on every operation, including a
    # backend/account/machine re-observation before effects.
    runner = config.get('remoteRunner')
    if not isinstance(runner, dict) or config.get('backend') != 'incus' or capacity.execution_key({'incus': runner.get('incus')}) != capacity.execution_key({'incus': config.get('incus')}):
        raise ValueError('The retained remote runner configuration changed.')
    if config.get('originSnapshotVersion') is not None:
        expected = configuration(app, config, runner)
        # Only admission budgets may change at guarded dispatch.
        if config.get('originSnapshotVersion') != 1 or capacity.execution_key(config) != capacity.execution_key(expected):
            raise ValueError('The origin snapshot or retained runner settings changed.')
    return config


def export_source(config, folder, source):
    from .test_source_transfer import repository_client
    if config.get('originSnapshotVersion') == 1 or repository_client(source):
        from .test_origin_snapshot import export_archive
        return export_archive(Path(source), folder / 'source.tar')
    script = "import json,dataclasses;from pathlib import Path, PurePosixPath;from digital_twin_universe import lib;s=lib.export_repository_snapshot(Path(__import__('sys').argv[1]),Path(__import__('sys').argv[2]));print(json.dumps(s.model_dump(mode='json')))"
    result = subprocess.run([config['python'], '-I', '-B', '-c', script, source, str(folder / 'source.tar')], capture_output=True, timeout=300)
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError('The exact source snapshot could not be exported.')
    return json.loads(result.stdout)


def invoke(config, folder, payload):
    runner = config['remoteRunner']
    safe = {key: value for key, value in payload.items() if key not in {'root', 'dockerHost', 'image', 'incus', 'maxRunning', 'maxInstances', 'minFreeBytes', 'admissionRoot'}}
    if safe['action'] == 'prepare':
        snapshot = export_source(config, folder, payload['source'])
        if (folder / 'source.tar').stat().st_size > MAX_SOURCE:
            raise ValueError('The source archive exceeds its transfer bound.')
        safe['snapshot'] = snapshot
        with (folder / 'source.tar').open('rb') as stream:
            offset = 0
            while content := stream.read(CHUNK):
                result = call(runner, {**safe, 'action': 'source', 'offset': offset, 'content': base64.b64encode(content).decode()})
                if not result.get('ok') or result.get('offset') != offset + len(content):
                    raise ValueError('Source transfer is unconfirmed. Keep its original target.')
                offset += len(content)
        result = call(runner, safe)
        if result.get('ok'):
            # Retain the original backend binding locally as well as on target.
            from .test_origin_storage import verify_binding
            verify_binding(payload, result['universeId'], result['backendIdentity'])
        return result
    from . import test_environment_profiles as profiles
    timeout = profiles.timing(safe.get('profileId'), safe.get('timingRequest'))['preparationTimeoutSeconds'] + 200 if safe['action'] == 'prepareDependencies' else 800 if safe['action'] in {'start', 'remove', 'finishCleanup', 'exportArtifacts'} else 180 if safe['action'] in {'stop', 'cleanupDependencies'} else safe.get('timeoutSeconds', 30) + 30
    result = call(runner, safe, timeout)
    if result.get('archives'):
        # The target's retained archive remains authoritative even when transfer
        # is interrupted. Source was retained on the origin before dispatch.
        result['retainedTarget'] = {'id': runner['id'], 'name': runner['name'], 'requestId': safe['requestId'], 'archives': result['archives']}
        try:
            result['downloadedArchives'] = fetch_archives(runner, folder, safe, result['archives'])
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            result['evidenceTransfer'] = 'The verified export remains on its original test machine; its download is incomplete.'
    return result


async def dispatch(app, action, args, authority):
    from .errors import AppError
    try:
        if action.endswith('.read') and action == 'testing.runners.read':
            return {'accepted': True, 'result': snapshot(app)}
        if action in {'testing.runners.register', 'testing.runners.refresh', 'testing.runners.enrollment.cancel', 'testing.runners.enrollment.reconcile'}:
            from . import test_runner_enrollment
            return await test_runner_enrollment.request(app, action, args, authority)
        if action.startswith(('testing.runners.machine.', 'testing.runners.capacity.')):
            runner = next((r for r in store(app)['runners'] if r['id'] == args['id']), None)
            if runner is None:
                raise ValueError('Choose a registered test machine.')
            if action.endswith('.save'):
                app.configuration_authority.check(authority)
            payload = {'action': action.removeprefix('testing.runners.'), **{k: args[k] for k in ('limits', 'expectedRevision') if k in args}}
            result = await asyncio.to_thread(call, runner, payload)
            if not result.get('ok'):
                raise ValueError(result.get('message', 'The test machine could not confirm its settings.'))
            if action.endswith('.save'):
                app.test_queue.backoff.clear()
                app.test_queue.wakeup.set()
                app.test_queue.start()
            return {'accepted': True, 'result': result['machineResources' if '.machine.' in action else 'backendPolicy']}
        async with app.lock:
            app.configuration_authority.check(authority)
            value = store(app)
            if digest(value) != args['expectedRevision']:
                raise AppError('Test machines changed. Refresh before saving.', 409)
            if action.endswith('.enable'):
                runner = next((r for r in value['runners'] if r['id'] == args['id']), None)
                if runner is None:
                    raise ValueError('Choose a registered test machine.')
                runner['enabled'] = args['enabled']
                publish(app, value)
            else:
                raise ValueError('Unsupported test machine setting.')
        app.test_queue.backoff.clear()
        app.test_queue.wakeup.set()
        app.test_queue.start()
        return {'accepted': True, 'result': snapshot(app)}
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        raise AppError(str(exc), 409) from None


def retain_candidates(app, base, profile):
    from . import test_environment_configs as configs
    registered = candidates(app, base, profile)
    # Preserve legacy semantics when there are no additional approved targets.
    return [configs.retain(app.data_dir, config) for config in registered] if len(registered) > 1 or (registered and registered[0].get('remoteRunner')) else []


def resolve_job(app, job, active):
    from . import test_environment_configs as configs
    if job['backendKey'] not in job['runnerCandidates']:
        raise ValueError('The selected test target was not approved for this check.')
    return configs.resolve(app, {'backendKey': job['backendKey'], 'backendConfigRetained': True}, active)


def has_room(observation, request, planned=None):
    if not observation.get('supported'):
        return True
    limits, reserved, observed = observation['effective'], observation['reserved'], observation['observed']
    planned = planned or {}
    return not (
        limits['maxReservedCpu'] and reserved['cpus'] + planned.get('cpus', 0) + request['cpus'] > limits['maxReservedCpu']
        or limits['maxReservedMemoryMiB'] and reserved['memoryMiB'] + planned.get('memoryMiB', 0) + request['memoryMiB'] > limits['maxReservedMemoryMiB']
        or limits['minAvailableMemoryMiB'] and observed['availableMemoryMiB'] < request['memoryMiB'] + limits['minAvailableMemoryMiB'] + reserved.get('pendingMemoryMiB', 0) + planned.get('memoryMiB', 0)
        or limits['minFreeBytes'] and observed['freeBytes'] < limits['minFreeBytes']
        or limits['maxLoadPercent'] and observed['loadPercent'] > limits['maxLoadPercent']
        or request.get('profileId') != 'linux-python' and limits.get('maxPreparing') and reserved.get('preparing', 0) + planned.get('preparing', 0) >= limits['maxPreparing'])


def select(app, job, active, observations=None, backoff=None, routing=None):
    from . import task_environments as environments
    from . import test_environment_configs as configs
    observations = {} if observations is None else observations
    backoff = {} if backoff is None else backoff
    for key in job['runnerCandidates']:
        try:
            config = configs.resolve(app, {'backendKey': key, 'backendConfigRetained': True}, active)
            if not enabled(app, config):
                continue
            if key not in observations:
                retry, delay = backoff.get(key, (0, 5))
                if retry > time.time():
                    continue
                if config.get('remoteRunner'):
                    result = call(config['remoteRunner'], {'action': 'capacity'}, timeout=20)
                    observed = {**result.get('capacity', {}), 'available': bool(result.get('ok') and result.get('available')), 'machineResources': result.get('machineResources', {})}
                else:
                    observed = environments.capacity(app, config)
                observations[key] = observed
                if not observed.get('available'):
                    backoff[key] = (time.time() + delay, min(60, delay * 2))
                else:
                    backoff.pop(key, None)
            observed = observations[key]
            available = (routing.room(key, config, observed, routing.request(job)) if routing is not None else
                         observed.get('available') and has_room(observed.get('machineResources', {}), job['resourceRequest']))
            if available:
                return config
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            _, delay = backoff.get(key, (0, 5))
            backoff[key] = (time.time() + delay, min(60, delay * 2))
            observations[key] = {'available': False}
            continue  # Read-only candidate observation; no effect has been sent.
    return None


def fetch_archives(runner, folder, payload, archives):
    if not isinstance(archives, list) or len(archives) > 16:
        raise ValueError('The retained archive list exceeds its bound.')
    destination = folder / ('remote-export-' + digest(payload['requestId']))
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError('Retained downloads cannot traverse links.')
    destination.mkdir(mode=0o700, exist_ok=True)
    info = destination.stat()
    if not stat.S_ISDIR(info.st_mode) or (hasattr(os, 'getuid') and info.st_uid != os.getuid()) or (os.name != 'nt' and info.st_mode & 0o077):
        raise ValueError('Retained downloads require private storage.')
    downloaded = []
    for index, archive in enumerate(archives):
        if type(archive.get('bytes')) is not int or not 0 <= archive['bytes'] <= 8*1024**3 or not re.fullmatch(r'[a-f0-9]{64}', archive.get('sha256', '')):
            raise ValueError('The retained archive identity is invalid.')
        path = destination / (str(index) + '.tar')
        if path.is_symlink():
            raise ValueError('The retained download was replaced.')
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), 0o600)
        with os.fdopen(fd, 'ab') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (hasattr(os, 'getuid') and info.st_uid != os.getuid()) or (os.name != 'nt' and info.st_mode & 0o077) or info.st_size > archive['bytes'] or path.is_symlink():
                raise ValueError('The retained download identity changed.')
            offset = info.st_size
            while offset < archive['bytes']:
                response = call(runner, {**payload, 'action': 'artifact', 'archiveIndex': index, 'exportRequestId': payload['requestId'], 'offset': offset})
                content = base64.b64decode(response.get('content', ''), validate=True)
                if not response.get('ok') or not content or len(content) > CHUNK or response['offset'] != offset + len(content) or response['bytes'] != archive['bytes'] or response['sha256'] != archive['sha256']:
                    raise ValueError('The retained archive transfer changed.')
                stream.write(content); offset += len(content)
            stream.flush(); os.fsync(stream.fileno())
        from .test_origin_files import file
        sha, size = hashlib.sha256(), 0
        with file(path) as stream:
            while chunk := stream.read(1024*1024):
                size += len(chunk)
                if size > archive['bytes']:
                    raise ValueError('The retained download exceeds its bound.')
                sha.update(chunk)
        if (sha.hexdigest(), size) != (archive['sha256'], archive['bytes']):
            raise ValueError('The retained archive download is incomplete.')
        downloaded.append({**archive, 'destination': str(path)})
    return downloaded
