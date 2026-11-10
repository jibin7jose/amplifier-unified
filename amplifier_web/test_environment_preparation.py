"""Prepare exact-source dependency images in owned guests, never on the host.

The durable parent owns admission and runs this blocking adapter in a background
thread. Intent precedes effects; an interrupted preparation is inspectable, not
automatically replayed. All returned image identities are local immutable IDs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tarfile
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path, PurePosixPath
from urllib.parse import quote

try:
    import fcntl
except ImportError:  # Capability inspection must remain usable on Windows.
    fcntl = None

from . import test_environment_profiles as profiles
from . import test_source_transfer as transfer

MAX_ARCHIVE = transfer.MAX_ARCHIVE
MAX_INPUT = 16 * 1024 * 1024
PREPARATION_SECONDS = 1800
LOG_BYTES = 128 * 1024
RECIPE_FILES = ('test_environment_prepare_guest.py', 'test_environment_initialize_guest.py')
CLIENT_SELECTION = 'components/web-client.source.json'
CLIENT_DIRECTORY = '.build/web-client'


class PreparationCancelled(Exception):
    """Cancellation was requested; the caller still has to confirm builder stop."""


def cancelled(folder):
    try:
        descriptor = os.open(folder / 'cancel.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 1024:
            raise ValueError('The preparation cancellation record is not a private regular file.')
        return json.load(stream) == {'cancelRequested': True}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(part)
    return value.hexdigest()


def write(path, value):
    temporary = path.with_name('.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def node_projects(value):
    """The existing browser project plus the optional selected client build."""
    return value.get('nodeProjects', [{'directory': value['nodeDirectory'],
                                      'key': value.get('nodeKey', value['nodeDirectory'] or 'root'),
                                      'manifestDirectory': value['nodeDirectory']}])


def browser_projects(archive, files):
    if 'tests/web/package-lock.json' in files:
        directory, key = 'tests/web', 'tests-web'
    elif 'frontend/package-lock.json' in files:
        # Historical target adapter: an explicitly selected older checkout may
        # still own this project. Current Unified has no such directory/view.
        directory = key = 'frontend'
    else:
        directory, key = '', 'root'
    projects = [{'directory': directory, 'key': key, 'manifestDirectory': directory}]
    if CLIENT_SELECTION not in files or directory != 'tests/web':
        return projects
    try:
        selection = json.loads(files[CLIENT_SELECTION])
        delivery = selection['delivery']
        if delivery['type'] == 'directory':
            source = delivery['path']
        elif delivery['type'] == 'repository':
            source = CLIENT_DIRECTORY
        else:
            raise ValueError('Unknown selected client delivery')
        path = PurePosixPath(source)
        if not source or path.is_absolute() or '..' in path.parts or str(path) != source:
            raise ValueError('Invalid selected client manifest directory')
        if source == directory:
            raise ValueError('Client and browser tools must own separate dependency projects')
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('The selected client dependency source is invalid.') from error
    required = {source + '/' + name for name in ('package.json', 'package-lock.json')}
    if delivery['type'] == 'repository':
        required.add(source + '/.client-source.json')
    with tarfile.open(archive) as stream:
        for item in stream:
            if item.name in required:
                if not item.isfile() or item.size > MAX_INPUT:
                    raise ValueError('Selected client manifests must be bounded ordinary files.')
                files[item.name] = stream.extractfile(item).read()
    if not required <= files.keys():
        raise ValueError('Prepare the selected client before transferring this source: '
                         'python scripts/web_client_build.py prepare')
    if delivery['type'] == 'repository' and json.loads(files[source + '/.client-source.json']) != selection:
        raise ValueError('Prepared client manifests belong to another selection; run '
                         'python scripts/web_client_build.py prepare before transferring source.')
    projects.append({'directory': CLIENT_DIRECTORY, 'key': 'web-client', 'manifestDirectory': source,
                     **({'preparedManifestsOnly': True} if delivery['type'] == 'repository' else {})})
    return projects


def plan(archive, snapshot, profile_id):
    profile = profiles.get(profile_id)
    if not profile['preparationRequired']:
        raise ValueError('The small Python profile does not require dependency preparation.')
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError('The source archive is unavailable or exceeds the preparation limit.')
    transfer.preflight(archive, snapshot)
    source_hash = snapshot['archive_sha256']
    files = {}
    names = set()
    total_bytes = 0
    with tarfile.open(archive) as stream:
        for item in stream:
            total_bytes += item.size
            if total_bytes > MAX_ARCHIVE:
                raise ValueError('The expanded source archive exceeds the preparation limit.')
            path = PurePosixPath(item.name)
            name = str(path)
            if path.is_absolute() or '..' in path.parts or name in names:
                raise ValueError('The source archive contains an unsafe or duplicate path.')
            names.add(name)
            if any(part in {'node_modules', '.venv', '.unified-prepared-dependencies.json'} for part in path.parts):
                raise ValueError('Prepare dependencies from lockfiles, not a copied environment.')
            if name in {'pyproject.toml', 'uv.lock', 'package.json', 'package-lock.json',
                        'tests/web/package.json', 'tests/web/package-lock.json', CLIENT_SELECTION,
                        'frontend/package.json', 'frontend/package-lock.json'}:
                if not item.isfile() or item.size > MAX_INPUT:
                    raise ValueError('Dependency manifests must be bounded ordinary files.')
                files[name] = stream.extractfile(item).read()
    python_project = profile_id == 'python-project' or bool({'pyproject.toml', 'uv.lock'} & files.keys())
    required = {'pyproject.toml', 'uv.lock'} if python_project else set()
    source = {'archiveSha256': source_hash}
    for key, target in (('source_head', 'sourceHead'), ('snapshot_commit', 'snapshotCommit'), ('snapshot_tree', 'snapshotTree')):
        if key in snapshot:
            if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', str(snapshot[key])):
                raise ValueError('The source snapshot identity is invalid.')
            source[target] = snapshot[key]
    value = {'version': 1, 'profileId': profile_id, 'sourceSha256': source_hash, 'pythonProject': python_project,
             'executionContract': profiles.execution_contract(profile_id, source)}
    if profile_id == 'browser-test':
        projects = browser_projects(archive, files)
        directory = projects[0]['directory']
        prefix = directory + '/' if directory else ''
        for project in projects:
            manifest_prefix = project['manifestDirectory'] + '/' if project['manifestDirectory'] else ''
            required.update({manifest_prefix + 'package.json', manifest_prefix + 'package-lock.json'})
        try:
            lock = json.loads(files[prefix + 'package-lock.json'])
            version = lock['packages']['node_modules/playwright']['version']
            if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?', version):
                raise ValueError('Invalid locked Playwright version.')
        except (KeyError, TypeError, ValueError):
            raise ValueError('Browser preparation needs a package-lock.json containing Playwright.') from None
        value.update(nodeDirectory=directory, nodeKey=projects[0]['key'], playwrightVersion=version,
                     nodeProjects=projects)
    if not required <= files.keys():
        raise ValueError('Project preparation requires committed pyproject.toml and uv.lock; browser checks also require package.json and package-lock.json.')
    value['lockfiles'] = [{'path': name, 'sha256': hashlib.sha256(files[name]).hexdigest()} for name in sorted(required)]
    recipe = hashlib.sha256()
    for name in RECIPE_FILES:
        recipe.update(name.encode())
        recipe.update(Path(__file__).with_name(name).read_bytes())
    recipe.update(Path(__file__).read_bytes())
    value['recipeSha256'] = recipe.hexdigest()
    value['preparationId'] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def validate_execution_contract(value, expected):
    """Guest inventory cannot substitute arbitrary paths or commands."""
    if (not isinstance(value, dict)
            or {key: item for key, item in value.items() if key != 'pythonBuild'}
               != {key: item for key, item in expected.items() if key != 'pythonBuild'}):
        raise ValueError('The prepared execution contract does not match its admitted source.')
    build = value.get('pythonBuild')
    if not isinstance(build, dict) or type(build.get('available')) is not bool:
        raise ValueError('Python build readiness was not confirmed.')
    if build['available']:
        if (set(build) != {'available', 'python', 'backend', 'command'}
                or build['python'] != '/opt/unified-prepared/build-venv/bin/python'
                or build['command'] != expected['pythonBuild']['command']
                or not isinstance(build['backend'], str) or not 1 <= len(build['backend']) <= 512):
            raise ValueError('The prepared build invocation changed.')
    elif set(build) != {'available', 'reason'} or build['reason'] not in {
            'This source declares no Python build backend.',
            'Python packaging prerequisites are unavailable. Source checks can run; inspect the retained preparation log before building.'}:
        raise ValueError('The unavailable build contract changed.')
    return value


def run(arguments, env, log, *, timeout=PREPARATION_SECONDS, watch_cancel=True, require_complete=False, cancel_folder=None):
    """Bound host memory/log size while draining noisy dependency installers."""
    cancel_folder = cancel_folder or log.parent
    if watch_cancel and cancelled(cancel_folder):
        raise PreparationCancelled('Dependency preparation was cancelled.')
    process = subprocess.Popen(arguments, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    tail = bytearray()
    truncated = False
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            if watch_cancel and cancelled(cancel_folder):
                raise PreparationCancelled('Dependency preparation was cancelled.')
            if time.monotonic() > deadline:
                raise TimeoutError('Dependency preparation exceeded its time limit; inspect the owned build resource before retrying.')
            for key, _ in selector.select(min(0.2, max(0.01, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 16384)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                tail.extend(chunk)
                truncated |= len(tail) > LOG_BYTES
                del tail[:-LOG_BYTES]
        if process.wait(timeout=max(1, deadline - time.monotonic())):
            raise ValueError('Dependency preparation failed. Inspect its retained log; private or authenticated dependencies are not supplied automatically.')
        if require_complete and truncated:
            raise ValueError('The preparation control response exceeded its observation limit.')
        return tail.decode(errors='replace')
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        process.stdout.close()
        with log.open('ab') as stream:
            stream.write(tail)
        if log.stat().st_size > LOG_BYTES:
            retained = log.read_bytes()[-LOG_BYTES:]
            log.write_bytes(retained)
        log.chmod(0o600)


def _environment(folder, config):
    result = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'HOME': str(folder),
              'DOCKER_CONFIG': str(folder / 'docker-config')}
    (folder / 'docker-config').mkdir(mode=0o700, exist_ok=True)
    if config.get('backend') == 'incus':
        result['INCUS_CONF'] = config['incus']['configDir']
    return result


def backend_identity(config, folder, env, *, timeout=30):
    log = folder / 'inspection.log'
    if config.get('backend', 'docker') == 'docker':
        value = json.loads(run(['docker', '--host', config['dockerHost'], 'info', '--format', '{{json .}}'], env, log, timeout=timeout, watch_cancel=False, require_complete=True))
        if not isinstance(value, dict) or not isinstance(value.get('ID'), str) or not value['ID']:
            raise ValueError('The Docker daemon identity is unavailable.')
        return {'backend': 'docker', 'daemon': value['ID']}
    settings = config['incus']
    reply = json.loads(run(_incus_query(settings, '/1.0'), env, log, timeout=timeout, watch_cancel=False, require_complete=True))
    if not isinstance(reply, dict) or not isinstance(reply.get('environment'), dict):
        raise ValueError('The Incus preparation daemon identity is unavailable.')
    value = reply['environment']
    if value.get('project') != settings['project'] or (settings.get('daemonFingerprint') and value.get('certificate_fingerprint') != settings['daemonFingerprint']):
        raise ValueError('The Incus preparation daemon or project changed.')
    return {'backend': 'incus', 'daemon': value['certificate_fingerprint'], 'project': value['project']}


def _incus_query(settings, path):
    # `incus query` rejects the CLI's global --project flag. Scope raw API
    # observations through the request URI, including identity verification.
    return [settings['incusExecutable'], 'query', path + '?project=' + quote(settings['project'], safe='')]


def memory_available(config, folder, profile_id, resource_request=None):
    """Observe headroom before building; unrelated host activity can still change.

    This is not a global reservation over processes outside the managed runner.
    Both actual host availability and daemon usage are considered on small hosts.
    """
    required = profiles.resources(profile_id, resource_request)['memoryMiB'] * 1024 * 1024
    env = _environment(folder, config)
    log = folder / 'capacity.log'
    if config.get('backend', 'docker') == 'docker':
        if not Path('/proc/meminfo').exists():
            return True  # Desktop VM authority is not established by host memory.
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        available = int(memory['MemAvailable'].split()[0]) * 1024
        command = ['docker', '--host', config['dockerHost']]
        info = json.loads(run([*command, 'info', '--format', '{{json .}}'], env, log, timeout=30, require_complete=True))
        total = int(info['MemTotal'])
        if total <= 16 * 1024 ** 3:
            samples = run([*command, 'stats', '--no-stream', '--format', '{{.MemUsage}}'], env, log, timeout=30, require_complete=True)
            used = 0
            for sample in samples.splitlines():
                match = re.fullmatch(r'([0-9.]+)\s*(B|kB|MB|GB|KiB|MiB|GiB)\s*/.*', sample)
                if not match:
                    raise ValueError('The test host memory usage could not be inspected.')
                units = {'B': 1, 'kB': 1000, 'MB': 1000 ** 2, 'GB': 1000 ** 3,
                         'KiB': 1024, 'MiB': 1024 ** 2, 'GiB': 1024 ** 3}
                used += float(match[1]) * units[match[2]]
            available = min(available, total - used)
    else:
        settings = config['incus']
        memory = json.loads(run(_incus_query(settings, '/1.0/resources'), env, log, timeout=30, require_complete=True))['memory']
        total = int(memory['total'])
        available = total - int(memory['used'])
    return available >= required + max(1024 ** 3, min(2 * 1024 ** 3, total // 10))


@contextmanager
def preparation_slot(config, folder, profile_id, *, seconds=PREPARATION_SECONDS, resource_request=None):
    """At most one dependency build per configured admission authority."""
    if fcntl is None:
        raise ValueError('Dependency preparation needs a supported Linux test host.')
    directory = Path(config['incus']['admissionDir'] if config.get('backend') == 'incus' else config['admissionRoot'])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError('The dependency preparation admission directory changed.')
    descriptor = os.open(directory / 'dependency-preparation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + seconds
        while True:
            if cancelled(folder):
                raise PreparationCancelled('Dependency preparation was cancelled.')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                write(folder / 'status.json', {'status': 'preparing', 'phase': 'waiting-for-builder', 'profileId': profile_id,
                                               'reason': 'Another dependency preparation is using this host’s build slot.'})
                if time.monotonic() > deadline:
                    raise TimeoutError('Another dependency preparation still owns this host’s build slot.')
                time.sleep(0.2)
        while not memory_available(config, folder, profile_id, resource_request):
            write(folder / 'status.json', {'status': 'preparing', 'phase': 'waiting-for-memory', 'profileId': profile_id,
                                           'reason': 'Waiting for enough observed memory for this test profile.'})
            if cancelled(folder):
                raise PreparationCancelled('Dependency preparation was cancelled.')
            if time.monotonic() > deadline:
                raise TimeoutError('The host did not have enough observed memory for dependency preparation.')
            time.sleep(0.2)
        write(folder / 'status.json', {'status': 'preparing', 'phase': 'installing-dependencies', 'profileId': profile_id})
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def prepare(config, root, snapshot, profile_id, request_id, *, resource_request=None, host_reservation=None, timing_request=None, dependency_store=None):
    root = Path(root)
    folder = root / 'preparation'
    if any(path.is_symlink() for path in (folder, *folder.parents)):
        raise ValueError('The dependency preparation directory cannot traverse symlinks.')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = _environment(folder, config)
    expected = plan(root / 'source.tar', snapshot, profile_id)
    expected.update(backend=config.get('backend', 'docker'), requestId=request_id)
    seconds = profiles.timing(profile_id, timing_request)['preparationTimeoutSeconds']
    if timing_request is not None:
        expected['timingRequest'] = timing_request
    if resource_request is not None:
        profiles.resources(profile_id, resource_request)
        expected['resourceRequest'] = resource_request
    expected['baseImage'] = config.get('image') if expected['backend'] == 'docker' else config['incus']['image']
    expected['resourceName'] = 'au-prepare-' + hashlib.sha256((str(root) + request_id).encode()).hexdigest()[:24]
    expected['backendIdentity'] = backend_identity(config, folder, env)
    binding = json.loads((root / 'backend-binding.json').read_text())
    if binding.get('backendIdentity') != expected['backendIdentity']:
        raise ValueError('The backend changed after the task source was frozen.')
    receipt_path = folder / 'receipt.json'
    if (folder / 'cleanup.json').exists():
        raise ValueError('This dependency preparation was removed. Submit a new check with a fresh source snapshot.')
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('intent') != expected:
            raise ValueError('Dependency preparation belongs to a different frozen source or request.')
        validate_receipt(receipt, snapshot, profile_id)
        if digest(folder / 'inventory.json') != receipt.get('inventorySha256'):
            raise ValueError('The retained dependency inventory changed.')
        return receipt
    intent_path = folder / 'intent.json'
    try:
        with intent_path.open('x') as stream:
            json.dump(expected, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        intent_path.chmod(0o600)
    except FileExistsError:
        raise ValueError('This dependency preparation has an unfinished retained attempt. Inspect and clean up its owned build resource; it will not be replayed.') from None
    write(folder / 'status.json', {'status': 'preparing', 'profileId': profile_id, 'resourceName': expected['resourceName'], 'startedAt': time.time()})
    context = folder / 'context'
    context.mkdir(mode=0o700)
    shutil.copyfile(root / 'source.tar', context / 'source.tar')
    for source, target in zip(RECIPE_FILES, ('prepare.py', 'initialize.py')):
        shutil.copyfile(Path(__file__).with_name(source), context / target)
    write(context / 'plan.json', expected)
    try:
        deadline = time.monotonic() + seconds
        with (nullcontext() if (host_reservation or {}).get('admitted') else preparation_slot(config, folder, profile_id, seconds=seconds, resource_request=resource_request)):
            # The monotonic deadline is process-local, never part of the saved
            # dependency recipe identity. Waiting and building share one budget.
            from . import test_dependency_layers as layers
            with layers.borrow(dependency_store, config, folder, expected, root / 'source.tar', deadline) as borrowed:
                build = {**expected, '_deadline': deadline}
                if borrowed:
                    build['_dependencyBase'] = borrowed['image']
                    write(context / 'plan.json', {**expected, 'dependencyMode': 'finalize'})
                    write(folder / 'dependency-reuse.json', borrowed)
                image, inventory = (_docker(config, folder, build, env) if expected['backend'] == 'docker'
                                    else _incus(config, folder, build, env))
        if cancelled(folder):
            raise PreparationCancelled('Dependency preparation was cancelled.')
        inventory_value = json.loads(inventory)
        if inventory_value.get('preparationId') != expected['preparationId'] or inventory_value.get('sourceSha256') != expected['sourceSha256']:
            raise ValueError('The built dependency inventory does not match its frozen source.')
        validate_execution_contract(inventory_value.get('executionContract'), expected['executionContract'])
        write(folder / 'inventory.json', inventory_value)
        receipt = {'version': 1, 'status': 'ready', 'intent': expected, 'profileId': profile_id,
                   'sourceSha256': expected['sourceSha256'], 'recipeSha256': expected['recipeSha256'],
                   'lockfiles': expected['lockfiles'], 'image': image, 'backend': expected['backend'],
                   'inventorySha256': digest(folder / 'inventory.json'), 'preparedAt': time.time(),
                   **({'executionContract': inventory_value['executionContract']} if inventory_value.get('executionContract') else {})}
        write(receipt_path, receipt)
        write(folder / 'status.json', {'status': 'ready', 'profileId': profile_id, 'image': image})
        return receipt
    except PreparationCancelled:
        stopped = stop_builder(config, folder, expected, env)
        write(folder / 'status.json', {'status': 'cancelled' if stopped else 'unknown',
                                      'profileId': profile_id, 'resourceName': expected['resourceName'],
                                      'builderStopped': stopped})
        raise ValueError('Dependency preparation was cancelled and its builder was stopped.' if stopped else
                         'Cancellation was requested, but the owned dependency builder stop is unconfirmed.') from None
    except Exception:
        write(folder / 'status.json', {'status': 'unknown', 'profileId': profile_id,
                                      'resourceName': expected['resourceName'], 'reason': 'Preparation did not return a confirmed image. Inspect the retained log and owned resource before a new attempt.'})
        raise
    finally:
        # The original source archive and provenance remain retained; do not keep
        # a second disposable build-context copy after the engine consumed it.
        shutil.rmtree(context)


def validate_receipt(receipt, snapshot, profile_id):
    if (not isinstance(receipt, dict) or receipt.get('version') != 1 or receipt.get('status') != 'ready'
            or receipt.get('profileId') != profile_id or receipt.get('sourceSha256') != snapshot.get('archive_sha256')
            or receipt.get('backend') not in {'docker', 'incus'}):
        raise ValueError('The prepared dependencies do not match this test source and profile.')
    pattern = r'sha256:[a-f0-9]{64}' if receipt['backend'] == 'docker' else r'[a-f0-9]{64}'
    if not re.fullmatch(pattern, str(receipt.get('image', ''))):
        raise ValueError('Prepared tests require an immutable local image identity.')
    return receipt


def status(root):
    """Small retained observation suitable for UI and agent progress surfaces."""
    path = Path(root) / 'preparation/status.json'
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {'status': 'not-started'}
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8192 or info.st_uid != os.getuid():
            return {'status': 'unknown', 'reason': 'The retained preparation status is unavailable.'}
        value = json.load(stream)
    return {key: value[key] for key in ('status', 'phase', 'profileId', 'startedAt', 'builderStopped', 'reason') if key in value}


def _docker(config, folder, expected, env):
    docker = ['docker', '--host', config['dockerHost']]
    context = folder / 'context'
    image = expected.get('_dependencyBase', expected['baseImage'])
    pattern = r'sha256:[a-f0-9]{64}' if '_dependencyBase' in expected else r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}'
    if not re.fullmatch(pattern, image):
        raise ValueError('Preparation requires an immutable qualified base image.')
    name = expected['resourceName']
    budget = profiles.resources(expected['profileId'], expected.get('resourceRequest'))
    log = folder / 'build.log'
    deadline = expected.get('_deadline')
    if deadline is None:
        deadline = time.monotonic() + profiles.timing(expected['profileId'], expected.get('timingRequest'))['preparationTimeoutSeconds']
    def call(*args, timeout=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('The saved dependency preparation time limit was reached.')
        return run([*docker, *args], env, log, timeout=min(remaining, timeout) if timeout is not None else remaining,
                   cancel_folder=expected.get('_cancelFolder'))
    # A separate ordinary container avoids unowned BuildKit caches. The only
    # network-enabled phase has no source mounts, runtime secrets or Docker socket.
    call('create', '--name', name, '--label', 'io.amplifier.preparation=' + name,
         '--memory', str(budget['memoryMiB']) + 'm', '--cpus', str(budget['cpus']), '--pids-limit', str(budget['pids']), '--network', 'bridge',
         '--security-opt', 'no-new-privileges:true', '--cap-drop', 'ALL',
         '--cap-add', 'CHOWN', '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'FOWNER',
         '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--user', '0:0', image, 'sleep', 'infinity')
    call('start', name)
    call('cp', str(context), name + ':/prepare')
    call('exec', name, 'python3', '-c', "import pathlib,tarfile; pathlib.Path('/workspace').mkdir(exist_ok=True); tarfile.open('/prepare/source.tar').extractall('/workspace', filter='data')")
    call('exec', name, 'python3', '/prepare/prepare.py')
    inventory = call('exec', name, 'cat', '/opt/unified-prepared/inventory.json', timeout=30)
    call('exec', name, 'rm', '-rf', '/prepare', '/workspace', '/tmp/build-home')
    identity = call('commit', '--change', 'WORKDIR /workspace', name, name).strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', identity):
        raise ValueError('Docker did not return an immutable prepared image identity.')
    write(folder / 'published-image.json', {'image': identity, 'resourceName': name})
    call('stop', '--time', '5', name)
    call('rm', name)
    return identity, inventory


def stop_builder(config, folder, intent, env=None):
    """Observe exact ownership before stopping; never infer stop from host timeout."""
    env = env or _environment(folder, config)
    name = intent['resourceName']
    log = folder / 'cleanup.log'
    def call(command):
        return run(command, env, log, timeout=60, watch_cancel=False)
    try:
        if backend_identity(config, folder, env) != intent['backendIdentity']:
            return False
        if intent['backend'] == 'docker':
            command = ['docker', '--host', config['dockerHost']]
            rows = [json.loads(line) for line in call([*command, 'ps', '-a', '--filter', 'name=^/' + name + '$', '--format', '{{json .}}']).splitlines()]
            if not rows:
                return True
            values = json.loads(call([*command, 'inspect', name]))
            if len(values) != 1 or values[0].get('Config', {}).get('Labels', {}).get('io.amplifier.preparation') != name:
                return False
            call([*command, 'stop', '--time', '5', name])
            values = json.loads(call([*command, 'inspect', name]))
            return len(values) == 1 and values[0].get('State', {}).get('Running') is False
        settings = config['incus']
        command = [settings['incusExecutable'], '--project', settings['project']]
        rows = json.loads(call([*command, 'list', '^' + name + '$', '--format', 'json']))
        if not rows:
            return True
        if len(rows) != 1 or rows[0].get('name') != name or rows[0].get('config', {}).get('user.amplifier.preparation') != name:
            return False
        if rows[0].get('status') != 'Stopped':
            call([*command, 'stop', name, '--force'])
        rows = json.loads(call([*command, 'list', '^' + name + '$', '--format', 'json']))
        return len(rows) == 1 and rows[0].get('status') == 'Stopped'
    except (OSError, ValueError, TimeoutError):
        return False


def _incus(config, folder, expected, env):
    settings = config['incus']
    for field in ('preparationNetwork', 'preparationStorage'):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}', str(settings.get(field, ''))):
            raise ValueError('Project dependency preparation needs the supported host setup with its owned build network and storage.')
    incus = [settings['incusExecutable'], '--project', settings['project']]
    name = expected['resourceName']
    budget = profiles.resources(expected['profileId'], expected.get('resourceRequest'))
    log = folder / 'build.log'
    deadline = expected.get('_deadline')
    if deadline is None:
        deadline = time.monotonic() + profiles.timing(expected['profileId'], expected.get('timingRequest'))['preparationTimeoutSeconds']
    def call(*args, timeout=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('The saved dependency preparation time limit was reached.')
        return run([*incus, *args], env, log, timeout=min(remaining, timeout) if timeout is not None else remaining,
                   cancel_folder=expected.get('_cancelFolder'))
    call('init', 'local:' + expected.get('_dependencyBase', settings['image']), name, '--no-profiles', '--storage', settings['preparationStorage'],
         '-c', 'security.privileged=false', '-c', 'security.nesting=false', '-c', 'limits.memory=' + str(budget['memoryMiB']) + 'MiB',
         '-c', 'limits.cpu=' + str(budget['cpus']), '-c', 'limits.processes=' + str(budget['pids']), '-c', 'user.amplifier.preparation=' + name)
    call('config', 'device', 'add', name, 'prepare-network', 'nic', 'nictype=bridged', 'parent=' + settings['preparationNetwork'], 'name=eth0')
    call('start', name)
    call('exec', name, '--', 'mkdir', '-p', '/prepare', '/workspace')
    for path in (folder / 'context').iterdir():
        call('file', 'push', str(path), name + '/prepare/' + path.name)
    # Ubuntu's initial Python may be 3.12, which also supports the data filter.
    call('exec', name, '--', 'python3', '-c', "import tarfile; tarfile.open('/prepare/source.tar').extractall('/workspace', filter='data')")
    call('exec', name, '--', 'python3', '/prepare/prepare.py')
    inventory = call('exec', name, '--', 'cat', '/opt/unified-prepared/inventory.json', timeout=30)
    call('exec', name, '--', 'rm', '-rf', '/prepare', '/workspace', '/tmp/build-home')
    call('stop', name, '--timeout', '30')
    call('config', 'device', 'remove', name, 'prepare-network')
    call('publish', name, '--alias', name, 'user.amplifier.preparation=' + name)
    images = json.loads(call('image', 'list', name, '--format', 'json', timeout=30))
    if len(images) != 1 or not re.fullmatch(r'[a-f0-9]{64}', str(images[0].get('fingerprint', ''))):
        raise ValueError('Incus did not return a single immutable prepared image.')
    identity = images[0]['fingerprint']
    # Save image identity before removal so an interrupted cleanup remains owned.
    write(folder / 'published-image.json', {'image': identity, 'resourceName': name})
    call('delete', name)
    return identity, inventory


def cleanup(config, root, *, cleanup_guard=None):
    """Return bounded incomplete recovery for malformed or unavailable records."""
    try:
        from .test_dependency_layers import cleanup_seed, release
        seeded = cleanup_seed(Path(root) / 'preparation')
        if not seeded.get('complete'):
            return {'complete': False, 'status': 'unknown', 'dependencySeed': seeded,
                    'reason': 'The source-free dependency builder is not confirmed settled; its reservation and recovery records remain held.'}
        result = _cleanup(config, root, cleanup_guard=cleanup_guard)
        if result.get('complete'):
            release(Path(root) / 'preparation')
        return result
    except Exception:  # noqa: BLE001 - preserve uncertain external-effect recovery at the host boundary.
        return {'complete': False, 'status': 'unknown',
                'reason': 'The owned dependency preparation could not be inspected or removed; retained source and recovery records are unchanged.'}


def _cleanup(config, root, *, cleanup_guard=None):
    """Remove only this completed preparation's image, after test guest removal.

    Interrupted builds need explicit recovery; absence of a final receipt is not
    evidence that a Docker daemon cannot still complete its accepted build.
    """
    folder = Path(root) / 'preparation'
    if not folder.exists():
        return {'complete': True, 'status': 'not-prepared'}
    if (folder / 'cleanup.json').exists():
        return json.loads((folder / 'cleanup.json').read_text())
    if not (folder / 'receipt.json').is_file():
        return cleanup_interrupted(config, folder)
    receipt = json.loads((folder / 'receipt.json').read_text())
    intent = receipt['intent']
    validate_receipt(receipt, {'archive_sha256': intent['sourceSha256']}, intent['profileId'])
    env = _environment(folder, config)
    name, identity = intent['resourceName'], receipt['image']
    log = folder / 'cleanup.log'
    try:
        if backend_identity(config, folder, env) != intent['backendIdentity']:
            raise ValueError('The prepared image daemon or project changed.')
        if receipt['backend'] == 'docker':
            command = ['docker', '--host', config['dockerHost']]
            present = run([*command, 'image', 'ls', '--all', '--no-trunc', '--format', '{{.ID}}'], env, log, timeout=30, watch_cancel=False, require_complete=True).splitlines()
            if identity in present:
                images = json.loads(run([*command, 'image', 'inspect', identity], env, log, timeout=30, watch_cancel=False, require_complete=True))
                if len(images) != 1 or images[0].get('Id') != identity or images[0].get('Config', {}).get('Labels', {}).get('io.amplifier.preparation') != name:
                    raise ValueError('The prepared image ownership changed.')
                # No force: Docker refuses to remove an image retained by a test
                # container. Tags are checked rather than deleting unrelated aliases.
                if set(images[0].get('RepoTags') or []) != {name + ':latest'}:
                    raise ValueError('The prepared image has additional owners or aliases.')
                if cleanup_guard:
                    cleanup_guard()
                run([*command, 'image', 'rm', name + ':latest'], env, log, timeout=120, watch_cancel=False)
        else:
            settings = config['incus']
            command = [settings['incusExecutable'], '--project', settings['project']]
            images = json.loads(run([*command, 'image', 'list', identity, '--format', 'json'], env, log, timeout=30, watch_cancel=False))
            if images:
                if len(images) != 1 or images[0].get('fingerprint') != identity or images[0].get('properties', {}).get('user.amplifier.preparation') != name:
                    raise ValueError('The prepared image ownership changed.')
                if {item.get('name') for item in images[0].get('aliases', [])} != {name}:
                    raise ValueError('The prepared image has additional owners or aliases.')
                if cleanup_guard:
                    cleanup_guard()
                run([*command, 'image', 'delete', identity], env, log, timeout=120, watch_cancel=False)
        result = {'complete': True, 'status': 'removed', 'image': identity,
                  'inventoryRetained': True, 'removedAt': time.time()}
        write(folder / 'cleanup.json', result)
        return result
    except (OSError, ValueError, TimeoutError):
        return {'complete': False, 'status': 'unknown', 'image': identity,
                'reason': 'The owned dependency image could not be confirmed removed; its provenance remains retained.'}


def cleanup_interrupted(config, folder):
    """Called only once the owning preparation thread/process has settled."""
    if not (folder / 'intent.json').is_file():
        return {'complete': True, 'status': 'not-started'}
    intent = json.loads((folder / 'intent.json').read_text())
    env = _environment(folder, config)
    if not stop_builder(config, folder, intent, env):
        return {'complete': False, 'status': 'unknown', 'reason': 'The owned dependency builder could not be confirmed stopped.'}
    name = intent['resourceName']
    log = folder / 'cleanup.log'
    def call(command):
        return run(command, env, log, timeout=120, watch_cancel=False)
    try:
        if intent['backend'] == 'docker':
            command = ['docker', '--host', config['dockerHost']]
            rows = call([*command, 'ps', '-a', '--filter', 'name=^/' + name + '$', '--format', '{{json .}}'])
            if rows.strip():
                call([*command, 'rm', name])
            images = [json.loads(line) for line in call([*command, 'image', 'ls', '--no-trunc', '--filter', 'label=io.amplifier.preparation=' + name, '--format', '{{json .}}']).splitlines()]
            if images:
                # Commit may complete before its response or local receipt. The
                # immutable image must still carry our exact unique owner label.
                for row in images:
                    if row.get('Repository') != name or row.get('Tag') != 'latest':
                        raise ValueError('A prepared image has an unexpected alias.')
                call([*command, 'image', 'rm', name + ':latest'])
        else:
            settings = config['incus']
            command = [settings['incusExecutable'], '--project', settings['project']]
            rows = json.loads(call([*command, 'list', '^' + name + '$', '--format', 'json']))
            if rows:
                call([*command, 'delete', name])
            images = json.loads(call([*command, 'image', 'list', name, '--format', 'json']))
            for image in images:
                if image.get('properties', {}).get('user.amplifier.preparation') != name or {item.get('name') for item in image.get('aliases', [])} != {name}:
                    raise ValueError('A prepared image has an unexpected owner or alias.')
                call([*command, 'image', 'delete', image['fingerprint']])
        result = {'complete': True, 'status': 'removed', 'interruptedPreparation': True,
                  'sourceRetained': True, 'removedAt': time.time()}
        write(folder / 'cleanup.json', result)
        return result
    except (OSError, ValueError, TimeoutError):
        return {'complete': False, 'status': 'unknown', 'reason': 'The interrupted dependency builder or image could not be confirmed removed.'}
