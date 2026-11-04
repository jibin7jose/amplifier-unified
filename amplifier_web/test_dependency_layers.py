"""Source-free prepared dependencies, owned by the existing Storage policy.

Only a qualified immutable image is reusable. Each task keeps its own source
finalizer and image. An unfinished native attempt is retained, never replayed as
an ordinary cache miss. These records are host-owned, not agent arguments.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import binascii
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import tarfile
import time
import tomllib
from urllib.parse import urlsplit
import uuid

from . import test_environment_preparation as preparation

KIND = 'test-dependencies'
VERSION = 1
MAX_RECORD = 2 * 1024 * 1024
IDENTITY = re.compile(r'[a-f0-9]{64}-[a-f0-9]{32}')


def private(path):
    path = Path(path)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('The dependency store cannot traverse links.')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('The dependency store must be private to its owner.')
    return path


def read(path):
    if any(part.is_symlink() for part in (Path(path), *Path(path).parents)):
        raise ValueError('The dependency record cannot traverse links.')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > MAX_RECORD):
            raise ValueError('The dependency record is not a bounded private file.')
        return json.load(stream)


def store(home):
    return Path(home) / 'test-dependency-layers'


@contextmanager
def locked(directory, key, *, task=None, deadline=None):
    private(directory)
    if not re.fullmatch(r'[a-f0-9]{64}', key) or preparation.fcntl is None:
        raise ValueError('This dependency store cannot establish an exact lock.')
    descriptor = os.open(directory / (key + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('The dependency lock changed.')
        while True:
            if task is not None and preparation.cancelled(task):
                raise preparation.PreparationCancelled('Dependency preparation was cancelled.')
            try:
                preparation.fcntl.flock(descriptor, preparation.fcntl.LOCK_EX | preparation.fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if deadline is None or time.monotonic() >= deadline:
                    raise TimeoutError('The dependency layer is in use.')
                time.sleep(0.05)
        yield
    finally:
        os.close(descriptor)


def public_url(value, *, git=False):
    if not isinstance(value, str):
        return False
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        return False
    # No private query credentials. uv's Git ref selector is part of the exact
    # lock policy; the full resolved commit fragment establishes reuse identity.
    if git:
        return bool(re.fullmatch(r'[a-f0-9]{40}', url.fragment)
                    and (not url.query or re.fullmatch(r'(?:rev|tag|branch)=[A-Za-z0-9._%/-]+(?:&subdirectory=[A-Za-z0-9._%/-]+)?|subdirectory=[A-Za-z0-9._%/-]+(?:&(?:rev|tag|branch)=[A-Za-z0-9._%/-]+)?', url.query)))
    return not url.query and not url.fragment


def npm_integrity(value):
    """Accept npm's recorded SRI formats; npm verifies the locked tarball itself."""
    if not isinstance(value, str) or not value or len(value) > 1024:
        return False
    lengths = {'sha1': 20, 'sha256': 32, 'sha384': 48, 'sha512': 64}
    try:
        for token in value.split():
            algorithm, encoded = token.split('-', 1)
            decoded = base64.b64decode(encoded, validate=True)
            if len(decoded) != lengths.get(algorithm) or base64.b64encode(decoded).decode() != encoded:
                return False
        return bool(value.split())
    except (ValueError, binascii.Error):
        return False


def browser_inputs(files, expected):
    """Only locked public registry packages; never copy npm configuration or source."""
    prefix = expected['nodeDirectory'] + '/' if expected['nodeDirectory'] else ''
    try:
        package = json.loads(files[prefix + 'package.json'])
        lock = json.loads(files[prefix + 'package-lock.json'])
        if (not isinstance(package, dict) or not isinstance(lock, dict) or package.get('workspaces')
                or lock.get('lockfileVersion') not in {2, 3}
                or not isinstance(lock.get('packages'), dict)):
            return False
        for field in ('dependencies', 'devDependencies', 'optionalDependencies'):
            dependencies = package.get(field, {})
            if (not isinstance(dependencies, dict)
                    or any(not isinstance(value, str) or ':' in value or '/' in value
                           for value in dependencies.values())):
                return False
        for name, entry in lock['packages'].items():
            if not isinstance(entry, dict) or entry.get('link'):
                return False
            if name == '':
                if entry.get('workspaces'):
                    return False
                continue
            if (not name.startswith('node_modules/') or any(part in {'', '.', '..'} for part in name.split('/'))
                    or '\\' in name or not public_url(entry.get('resolved'))
                    or not npm_integrity(entry.get('integrity'))):
                return False
        return 'node_modules/playwright' in lock['packages']
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False


def prepared_directories(profile):
    directories = {'venv', 'bootstrap', 'python'}
    if profile == 'browser-test':
        directories.update({'node-runtime', 'node', 'browsers', 'browser-wheels'})
    return directories


def inputs(archive, expected):
    """Conservative eligibility; an unsupported project keeps the existing path."""
    if expected['profileId'] not in {'python-project', 'browser-test'} or not expected['pythonProject']:
        return None
    required = {row['path'] for row in expected['lockfiles']}
    files = {}
    with tarfile.open(archive) as source:
        for item in source:
            if expected['profileId'] == 'browser-test' and Path(item.name).name in {'.npmrc', 'npm-shrinkwrap.json'}:
                # npm configuration and overriding locks can change the cold
                # install. Do not silently omit them from a manifest-only seed.
                return None
            if item.name in required:
                if not item.isfile() or item.size > preparation.MAX_INPUT or item.name in files:
                    return None
                files[item.name] = source.extractfile(item).read()
    if set(files) != required or not {'pyproject.toml', 'uv.lock'} <= required:
        return None
    if (preparation.digest(archive) != expected['sourceSha256']
            or any(hashlib.sha256(files[row['path']]).hexdigest() != row['sha256'] for row in expected['lockfiles'])):
        raise ValueError('The frozen dependency inputs changed.')
    if expected['profileId'] == 'browser-test' and not browser_inputs(files, expected):
        return None
    try:
        project = tomllib.loads(files['pyproject.toml'].decode())
        lock = tomllib.loads(files['uv.lock'].decode())
        metadata = project['project']
        name = re.sub(r'[-_.]+', '-', metadata['name'].lower())
        if metadata.get('dynamic') or not metadata.get('version') or project.get('tool', {}).get('uv', {}).get('workspace'):
            return None
        roots = []
        for package in lock['package']:
            source = package['source']
            if source in ({'editable': '.'}, {'virtual': '.'}):
                roots.append(re.sub(r'[-_.]+', '-', package['name'].lower()))
            elif set(source) == {'registry'} and public_url(source['registry']):
                artifacts = [*package.get('wheels', []), *([package['sdist']] if package.get('sdist') else [])]
                if not artifacts or any(not public_url(item.get('url')) or not re.fullmatch(r'sha256:[a-f0-9]{64}', str(item.get('hash'))) for item in artifacts):
                    return None
            elif set(source) == {'git'} and public_url(source['git'], git=True):
                pass
            else:
                return None
        if roots != [name]:
            return None
        # Backend local paths, authenticated indexes, build constraints and local
        # source hooks need source-specific preparation. They stay cold.
        uv = project.get('tool', {}).get('uv', {})
        if project.get('build-system', {}).get('backend-path') or any(key in uv for key in ('index-url', 'extra-index-url', 'find-links', 'build-constraint-dependencies')):
            return None
        indexes = uv.get('index', [])
        if not isinstance(indexes, list) or any(not isinstance(row, dict) or not public_url(row.get('url')) for row in indexes):
            return None
        index_names = {row.get('name') for row in indexes if row.get('name')}
        for value in uv.get('sources', {}).values():
            for row in value if isinstance(value, list) else [value]:
                if not isinstance(row, dict) or any(key in row for key in ('path', 'workspace', 'url')):
                    return None
                if row.get('index') and row['index'] not in index_names:
                    return None
                if row.get('git') and not public_url(row['git']):
                    return None
        # The editable/virtual root is deliberately absent from the dependency
        # image. Its version belongs to each task's exact-source finalizer, not
        # the reusable dependency identity. Preserve every other parsed input.
        metadata.pop('version')
        for package in lock['package']:
            if package['source'] in ({'editable': '.'}, {'virtual': '.'}):
                package.pop('version', None)
        dependency_files = dict(files)
        for path, value in (('pyproject.toml', project), ('uv.lock', lock)):
            dependency_files[path] = json.dumps(value, sort_keys=True).encode()
    except (KeyError, TypeError, ValueError, UnicodeError):
        return None
    return {'project': name, 'files': files,
            'lockfiles': [{'path': path, 'sha256': hashlib.sha256(body).hexdigest()}
                          for path, body in sorted(dependency_files.items())]}


def key_inputs(expected, material):
    # The daemon/project prevents cross-machine reuse; immutable base identity
    # and recipe bind its platform, Python ABI, uv and validation implementation.
    value = {field: expected[field] for field in ('backendIdentity', 'baseImage', 'profileId', 'recipeSha256')}
    value['lockfiles'] = material['lockfiles']
    value.update(version=VERSION, ownerSha256=preparation.digest(__file__))
    return value


def key_for(expected, material):
    return hashlib.sha256(json.dumps(key_inputs(expected, material), sort_keys=True).encode()).hexdigest()


def native(config, folder, receipt, *, unused=False, deadline=None):
    """Observe identity, ownership and size; never trust a mutable image alias."""
    env = preparation._environment(folder, config)
    remaining = 3 if deadline is None else min(3, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError('Dependency inspection reached its time limit.')
    if preparation.backend_identity(config, folder, env, timeout=remaining) != receipt['intent']['backendIdentity']:
        raise ValueError('The dependency layer backend changed.')
    image, name = receipt['image'], receipt['intent']['resourceName']
    def call(args):
        remaining = 3 if deadline is None else min(3, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError('Dependency inspection reached its time limit.')
        return preparation.run(args, env, folder / 'inspection.log', timeout=remaining, watch_cancel=False, require_complete=True)
    def objects(args):
        rows = json.loads(call(args))
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('The native dependency observation is malformed.')
        return rows
    if receipt['backend'] == 'docker':
        rows = objects(['docker', '--host', config['dockerHost'], 'image', 'inspect', image])
        if (len(rows) != 1 or rows[0].get('Id') != image
                or not isinstance(rows[0].get('Config'), dict)
                or not isinstance(rows[0]['Config'].get('Labels'), dict)
                or rows[0]['Config']['Labels'].get('io.amplifier.preparation') != name
                or not isinstance(rows[0].get('RepoTags'), list)
                or any(not isinstance(tag, str) for tag in rows[0]['RepoTags'])
                or set(rows[0]['RepoTags']) != {name + ':latest'}):
            raise ValueError('The reusable image identity or owner changed.')
        if unused and call(['docker', '--host', config['dockerHost'], 'ps', '-a', '--filter', 'ancestor=' + image, '--format', '{{.ID}}']).strip():
            raise ValueError('A native container still references this dependency layer.')
        # Docker reports cumulative content including the immutable base. Only
        # this image's added content participates in the unused-layer budget.
        base = objects(['docker', '--host', config['dockerHost'], 'image', 'inspect', receipt['intent']['baseImage']])
        if len(base) != 1 or type(base[0].get('Size')) is not int or type(rows[0].get('Size')) is not int:
            raise ValueError('The reusable image size is unavailable.')
        return max(0, rows[0]['Size'] - base[0]['Size'])
    settings = config['incus']
    rows = objects([settings['incusExecutable'], '--project', settings['project'], 'image', 'list', image, '--format', 'json'])
    if (len(rows) != 1 or rows[0].get('fingerprint') != image
            or not isinstance(rows[0].get('properties'), dict)
            or rows[0]['properties'].get('user.amplifier.preparation') != name
            or not isinstance(rows[0].get('aliases'), list)
            or any(not isinstance(row, dict) or not isinstance(row.get('name'), str) for row in rows[0]['aliases'])
            or {row['name'] for row in rows[0]['aliases']} != {name}):
        raise ValueError('The reusable image identity or owner changed.')
    if unused:
        instances = objects([settings['incusExecutable'], '--project', settings['project'], 'list', '--format', 'json'])
        if any(not isinstance(row.get('config'), dict) for row in instances):
            raise ValueError('The native dependency observation is malformed.')
        if any(row['config'].get('volatile.base_image') == image for row in instances):
            raise ValueError('A native instance still references this dependency layer.')
    return int(rows[0]['size'])


def receipt(entry):
    value = read(entry / 'preparation/receipt.json')
    owner = owner_record(entry)
    profile = owner['inputs']['profileId']
    if profile not in {'python-project', 'browser-test'}:
        raise ValueError('The dependency profile changed.')
    preparation.validate_receipt(value, {'archive_sha256': value['intent']['sourceSha256']}, profile)
    intent = value['intent']
    inventory = read(entry / 'preparation/inventory.json')
    if (any(intent.get(field) != owner['inputs'][field] for field in ('backendIdentity', 'baseImage', 'profileId', 'recipeSha256'))
            or intent.get('lockfiles') != owner.get('sourceLockfiles', owner['inputs']['lockfiles'])
            or intent.get('preparationId') != owner['key'] or intent.get('dependencyKey') != owner['key']
            or intent.get('resourceName') != 'au-deps-' + entry.name[-32:]
            or inventory.get('dependencyKey') != owner['key'] or inventory.get('sourceSha256') != intent['sourceSha256']
            or inventory.get('executionContract')
            or preparation.digest(entry / 'preparation/inventory.json') != value['inventorySha256']):
        raise ValueError('The dependency layer inventory changed.')
    return value


def owner_record(entry):
    owner = read(entry / 'owner.json')
    expected = owner.get('inputs', {})
    config = owner.get('config', {})
    backend = config.get('backend', 'docker')
    base = config.get('image') if backend == 'docker' else config.get('incus', {}).get('image')
    if (not IDENTITY.fullmatch(entry.name) or owner.get('version') != VERSION
            or owner.get('key') != entry.name[:64]
            or hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest() != owner['key']
            or expected.get('baseImage') != base
            or expected.get('backendIdentity', {}).get('backend') != backend):
        raise ValueError('The dependency owner key or configuration changed.')
    used = owner.get('lastUsedAt')
    if type(used) not in (int, float) or not math.isfinite(used) or used < 0:
        raise ValueError('The dependency usage observation changed.')
    return owner


def seed(directory, key, config, expected, material, deadline, task):
    """No task source can enter this builder: its archive contains manifests only."""
    entry = private(directory / (key + '-' + uuid.uuid4().hex))
    folder = private(entry / 'preparation')
    context = private(folder / 'context')
    origin = {'task': str(task), 'requestId': expected['requestId'], 'resourceName': expected['resourceName']}
    preparation.write(entry / 'owner.json', {'version': VERSION, 'key': key, 'inputs': key_inputs(expected, material),
                                           'sourceLockfiles': expected['lockfiles'], 'config': config, 'origin': origin,
                                           'lastUsedAt': time.time()})
    with tarfile.open(context / 'source.tar', 'w') as archive:
        for name, body in sorted(material['files'].items()):
            item = tarfile.TarInfo(name)
            item.size = len(body)
            item.mode = 0o600
            archive.addfile(item, io.BytesIO(body))
    source = preparation.digest(context / 'source.tar')
    plan = {name: expected[name] for name in ('backend', 'backendIdentity', 'baseImage', 'profileId', 'lockfiles', 'recipeSha256')}
    plan.update(preparationId=key, sourceSha256=source, pythonProject=True,
                dependencyMode='layer', dependencyProject=material['project'], dependencyKey=key,
                resourceName='au-deps-' + entry.name[-32:])
    if expected['profileId'] == 'browser-test':
        plan.update({name: expected[name] for name in ('nodeDirectory', 'nodeKey', 'playwrightVersion')})
    for option in ('resourceRequest', 'timingRequest'):
        if option in expected:
            plan[option] = expected[option]
    for src, dst in zip(preparation.RECIPE_FILES, ('prepare.py', 'initialize.py')):
        (context / dst).write_bytes(Path(preparation.__file__).with_name(src).read_bytes())
    preparation.write(context / 'plan.json', plan)
    preparation.write(folder / 'intent.json', plan)
    # The task's existing recovery/compute owner must know this exact builder
    # before any native effect. A cache-only intent cannot hold its reservation.
    preparation.write(task / 'dependency-seed.json', {'version': VERSION, 'store': str(directory), 'entry': entry.name, **origin})
    preparation.write(directory / (key + '.json'), {'entry': entry.name})
    env = preparation._environment(folder, config)
    adapter = preparation._docker if plan['backend'] == 'docker' else preparation._incus
    try:
        image, inventory = adapter(config, folder, {**plan, '_deadline': deadline, '_cancelFolder': task}, env)
    except preparation.PreparationCancelled:
        stopped = preparation.stop_builder(config, folder, plan, env)
        preparation.write(folder / 'status.json', {'status': 'cancelled' if stopped else 'unknown', 'builderStopped': stopped})
        if not stopped:
            raise ValueError('The dependency seed cancellation is unconfirmed; its owned resource is retained.') from None
        raise
    inventory = json.loads(inventory)
    if (inventory.get('dependencyKey') != key or inventory.get('sourceSha256') != source
            or inventory.get('preparationId') != key or inventory.get('executionContract')
            or set(inventory.get('preparedFiles', {})) != prepared_directories(plan['profileId'])):
        raise ValueError('The source-free dependency inventory did not qualify.')
    preparation.write(folder / 'inventory.json', inventory)
    value = {'version': 1, 'status': 'ready', 'intent': plan, 'backend': plan['backend'],
             'profileId': plan['profileId'], 'sourceSha256': source, 'image': image,
             'inventorySha256': preparation.digest(folder / 'inventory.json'), 'preparedAt': time.time()}
    preparation.write(folder / 'receipt.json', value)
    preparation.write(task / 'dependency-seed-settled.json', {'entry': entry.name, 'settledAt': time.time()})
    # Manifests and bounded metadata remain as provenance. No full source was sent.
    import shutil
    shutil.rmtree(context)
    return entry, value


def cleanup_seed(task):
    """Called only by the settled task-preparation owner, never a cache sweep."""
    path = Path(task) / 'dependency-seed.json'
    if not path.exists():
        return {'complete': True, 'status': 'not-seeded'}
    saved = read(path)
    name = saved.get('entry', '')
    if not IDENTITY.fullmatch(name) or saved.get('task') != str(task):
        raise ValueError('The dependency seed owner changed.')
    directory = Path(saved['store'])
    with locked(directory, name[:64], deadline=time.monotonic() + 30):
        entry = private(directory / name)
        owner = owner_record(entry)
        original = read(Path(task) / 'intent.json')
        if (owner.get('origin') != {key: saved[key] for key in ('task', 'requestId', 'resourceName')}
                or any(original.get(key) != saved[key] for key in ('requestId', 'resourceName'))):
            raise ValueError('The dependency seed no longer belongs to this preparation.')
        if (entry / 'preparation/receipt.json').exists():
            # A ready receipt is written only after exact native builder removal.
            receipt(entry)
            result = {'complete': True, 'status': 'ready'}
        else:
            result = preparation.cleanup(owner['config'], entry)
        if result.get('complete'):
            preparation.write(Path(task) / 'dependency-seed-settled.json', {'entry': name, 'settledAt': time.time()})
        return result


def acquire(directory, config, task, expected, archive, deadline):
    """Record the exact borrow; no native finalizer runs under the key lock."""
    # File locking is part of this optional owner's custody contract. Hosts
    # without it keep their already-supported ordinary preparation path.
    material = inputs(archive, expected) if directory is not None and preparation.fcntl is not None else None
    if material is None:
        return None
    directory = Path(directory)
    key = key_for(expected, material)
    with locked(directory, key, task=task, deadline=deadline):
        pointer = directory / (key + '.json')
        value = None
        hit = False
        if pointer.exists():
            target = read(pointer).get('entry', '')
            if not IDENTITY.fullmatch(target) or not target.startswith(key + '-'):
                raise ValueError('The dependency layer reference changed.')
            entry = private(directory / target)
            if (entry / 'preparation/cleanup.json').exists():
                if read(entry / 'preparation/cleanup.json').get('complete') is not True:
                    raise ValueError('The previous dependency removal is unconfirmed.')
            elif not (entry / 'preparation/receipt.json').exists():
                # An interrupted first build is not authorization to replay it.
                return None
            else:
                value = receipt(entry)
                try:
                    native(config, entry / 'preparation', value)
                except (OSError, ValueError, TimeoutError, KeyError, TypeError, AttributeError):
                    # An unavailable, removed or changed native image is not
                    # reusable, but optional reuse must not block an otherwise
                    # supported source preparation. Preserve the uncertain
                    # entry; do not replay its seed or acquire its borrow.
                    return None
                hit = True
        if value is None:
            entry, value = seed(directory, key, config, expected, material, deadline, task)
        borrowers = private(entry / 'borrowers')
        identity = hashlib.sha256((str(task) + expected['requestId']).encode()).hexdigest()
        lease = {'version': VERSION, 'entry': entry.name, 'image': value['image'],
                 'task': str(task), 'requestId': expected['requestId'], 'resourceName': expected['resourceName']}
        preparation.write(task / 'dependency-borrow.json', {**lease, 'store': str(directory), 'leaseId': identity})
        preparation.write(borrowers / (identity + '.json'), lease)
        preparation.write(entry / 'owner.json', {**owner_record(entry), 'lastUsedAt': time.time()})
    return {'image': value['image'], 'key': key, 'entry': entry.name, 'hit': hit}


@contextmanager
def borrow(directory, config, task, expected, archive, deadline):
    result = acquire(directory, config, task, expected, archive, deadline)
    # The task's prepared image may still refer to this parent even after its
    # finalizer container exits. Its ordinary image cleanup settles the lease;
    # neither successful preparation nor a failed/unknown reply releases it.
    yield result


def release(task):
    path = Path(task) / 'dependency-borrow.json'
    if not path.exists():
        return
    value = read(path)
    if not (Path(task) / 'cleanup.json').exists() or read(Path(task) / 'cleanup.json').get('complete') is not True:
        raise ValueError('The task prepared image is not confirmed removed.')
    name = value['entry']
    if not IDENTITY.fullmatch(name) or not re.fullmatch(r'[a-f0-9]{64}', value['leaseId']):
        raise ValueError('The dependency borrower identity changed.')
    directory = Path(value['store'])
    with locked(directory, name[:64], deadline=time.monotonic() + 30):
        target = directory / name / 'borrowers' / (value['leaseId'] + '.json')
        if target.exists():
            saved = read(target)
            if saved != {key: value[key] for key in ('version', 'entry', 'image', 'task', 'requestId', 'resourceName')} or value['task'] != str(task):
                raise ValueError('The dependency borrower changed.')
            target.unlink()
            entry = private(directory / name)
            preparation.write(entry / 'owner.json', {**owner_record(entry), 'lastUsedAt': time.time()})
        preparation.write(Path(task) / 'dependency-borrow-settled.json', {'entry': name, 'settledAt': time.time()})


def inspect(home, *, limit=8):
    directory = store(home)
    if not directory.exists():
        return {'candidates': [], 'protectedIds': [], 'complete': True}
    private(directory)
    entries = sorted(path for path in directory.iterdir() if IDENTITY.fullmatch(path.name))
    cursor = directory / 'inspection-cursor.json'
    after = read(cursor).get('after', '') if cursor.exists() else ''
    entries = [entry for entry in entries if entry.name > after] + [entry for entry in entries if entry.name <= after]
    rows, protected = [], []
    deadline = time.monotonic() + 10
    visited = 0
    for entry in entries[:limit]:
        if time.monotonic() >= deadline:
            break
        visited += 1
        try:
            with locked(directory, entry.name[:64]):
                if (entry / 'preparation/cleanup.json').exists():
                    continue
                if not (entry / 'preparation/receipt.json').exists() or list((entry / 'borrowers').iterdir()):
                    protected.append(entry.name)
                    continue
                value = receipt(entry)
                owner = owner_record(entry)
                config = owner['config']
                size = native(config, entry / 'preparation', value, unused=True, deadline=deadline)
                rows.append({'kind': KIND, 'id': entry.name, 'label': 'Reusable test dependencies', 'fileBytes': size,
                             'lastUsedAt': owner['lastUsedAt']})
        except (OSError, ValueError, KeyError, TypeError, TimeoutError):
            protected.append(entry.name)
    if visited:
        preparation.write(cursor, {'after': entries[visited - 1].name})
    return {'candidates': rows, 'protectedIds': protected, 'complete': len(entries) <= visited}


def retire(home, selected, before_remove):
    directory = store(home)
    for name in sorted(selected):
        if not IDENTITY.fullmatch(name):
            continue
        try:
            with locked(directory, name[:64]):
                entry = private(directory / name)
                if list((entry / 'borrowers').iterdir()) or (entry / 'preparation/cleanup.json').exists():
                    continue
                value = receipt(entry)
                owner = owner_record(entry)
                if isinstance(selected, dict) and owner['lastUsedAt'] != selected[name].get('lastUsedAt'):
                    continue
                config = owner['config']
                size = native(config, entry / 'preparation', value, unused=True)
                item = {'kind': KIND, 'id': name, 'fileBytes': size}
                before_remove('attempting', item)
                result = preparation.cleanup(config, entry, cleanup_guard=lambda: before_remove('attempting', item))
                if result.get('complete'):
                    before_remove('completed', item)
        except (OSError, ValueError, KeyError, TypeError, TimeoutError):
            continue
