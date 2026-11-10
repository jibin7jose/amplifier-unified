#!/usr/bin/env python3
"""Manual IC-13 qualification; never installs into the host interpreter.

run requires a GitHub-hosted Linux runner with Docker already available. It
copies only committed candidate bytes and a credential-free Git object bundle
of the exact private loop checkout. Preparation has network access; a separate
unprivileged, network-none container executes the unchanged installed suites.
A fresh output/work directory is mandatory. An uncertain operation is recorded,
never retried or reported as cleaned up. Use the retained IDs to reconcile it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from email.parser import Parser

ROOT = Path(__file__).resolve().parents[1]
GUEST = Path('/opt/defaults')
LABEL = 'org.amplifier.default-installed'
SUITES = ('test_default_behaviors_integration.py', 'test_default_runtime_safety.py',
          'test_default_mount_runtime.py', 'test_default_routing_runtime.py')
DESKTOP_CASES = {
    'preserved-22': {
        'test_expanded_desktop_restriction_survives_child_alias': 8,
        'test_resume_preserves_saved_desktop_ceiling_and_history': 6,
        'test_runtime_desktop_ceiling_controls': 4,
        'test_runtime_inherited_desktop_ceiling_survives_representation': 4,
    },
    'remote-defaults': {
        'test_runtime_remote_implicit_restriction_cannot_be_disabled_by_child': 2,
        'test_runtime_remote_explicit_write_opt_in_control': 1,
    },
}
ROOT_GUEST_CASES = (
    'test_lost_response_repeated_identity_and_fresh_observer_do_not_replay',
    'test_cancel_is_prompt_and_terminal_requires_actual_exit',
    'test_timeout_is_enforced_in_guest_and_output_is_bounded',
    'test_worker_cannot_modify_supervisor_receipts_and_profile_environment_survives',
    'test_two_roots_execute_independently_and_cannot_adopt_identity',
    'test_dead_supervisor_is_unknown_and_same_request_never_restarts',
    'test_crash_after_reservation_does_not_launch_on_observe_or_repeat',
    'test_background_pipe_inheritance_does_not_hold_finished_check_until_deadline',
)
FIXED_CASES = {
    'root-guest': ('test_durable_guest_jobs.py', ROOT_GUEST_CASES),
    'installed-pwa': ('test_browser_auth.py', ('test_installed_pwa_launch',)),
}


def selected_stage(desktop_safety=False, root_guest=False, installed_pwa=False):
    require(sum(map(bool, (desktop_safety, root_guest, installed_pwa))) <= 1,
            'Select only one fixed qualification stage')
    return ('root-guest' if root_guest else 'installed-pwa' if installed_pwa
            else 'desktop-safety' if desktop_safety else 'ic13')


def client_helpers():
    path = GUEST / 'candidate/scripts/integration_clients.py'
    spec = importlib.util.spec_from_file_location('installed_client_checks', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pwa_preparation_environment(env):
    # dpkg helpers need sbin; do not widen the execution container's PATH.
    return {**env, 'PATH': env['PATH'] + ':/usr/sbin:/sbin',
            'PLAYWRIGHT_BROWSERS_PATH': str(GUEST / 'browsers')}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def in_container():
    return Path('/.dockerenv').is_file()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def clean_env():
    return {'PATH': '/opt/defaults/bin:/opt/defaults/.venv/bin:/usr/bin:/bin',
            'HOME': str(GUEST / 'home'), 'LANG': 'C.UTF-8',
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'TMPDIR': str(GUEST / 'tmp'),
            'UV_CACHE_DIR': str(GUEST / 'cache'), 'UV_NO_CONFIG': '1',
            'UV_PYTHON_INSTALL_DIR': str(GUEST / 'python'),
            'UV_NO_BUILD_PACKAGE': 'amplifier-core',
            'UV_CONCURRENT_BUILDS': '1', 'UV_CONCURRENT_DOWNLOADS': '4',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0',
            'AMPLIFIER_HOME': str(GUEST / 'home/amplifier'),
            'AMPLIFIER_WEB_HOME': str(GUEST / 'home/unified'),
            'AMPLIFIER_SESSION_STATE_HOME': str(GUEST / 'home/session-state'),
            'AMPLIFIER_TERMINAL_HOME': str(GUEST / 'home/terminal'),
            'XDG_CONFIG_HOME': str(GUEST / 'home/config'),
            'XDG_CACHE_HOME': str(GUEST / 'cache'), 'XDG_DATA_HOME': str(GUEST / 'home/data')}


def command(argv, *, cwd=None, env=None, timeout=120, capture=False):
    print('+', ' '.join(map(str, argv)), flush=True)
    return subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          check=True, timeout=timeout, text=True,
                          stdout=subprocess.PIPE if capture else None)


def pins(path):
    data = json.loads(Path(path).read_text())
    require(data['schemaVersion'] == 1 and data['platform'] == 'linux/amd64', 'Unsupported pin format/platform')
    names = set()
    for row in data['repositories']:
        require(re.fullmatch(r'amplifier[-a-z0-9]+', row['name']) and row['name'] not in names, 'Invalid/duplicate repository')
        names.add(row['name'])
        require(re.fullmatch(r'[a-f0-9]{40}', row['commit']), 'Exact repository revision required')
        require(row.get('private', False) == (row['name'] == 'amplifier-module-loop-live'), 'Unexpected private source')
        for package in row['packages']:
            require(not Path(package).is_absolute() and '..' not in Path(package).parts, 'Package path escapes source')
    for key in ('uv', 'core'):
        parsed = urllib.parse.urlsplit(data[key]['url'])
        require(parsed.scheme == 'https' and parsed.netloc == 'files.pythonhosted.org', 'Unexpected binary source')
        require(re.fullmatch(r'[a-f0-9]{64}', data[key]['sha256']), 'Binary hash required')
    return data


def inventory(root, *, boundary=None):
    """Hash contents, including non-Python assets; ignore only Git and bytecode."""
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if '.git' in relative.parts or '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            require(path.resolve().is_relative_to((boundary or root).resolve()), 'Source symlink escapes root: ' + str(relative))
            result[str(relative)] = {'link': os.readlink(path)}
        elif path.is_file():
            result[str(relative)] = digest(path)
    return result


def wheel_integrity_failure(expected, actual, evidence, boundary):
    """Retain bounded names/types/hashes, never artifact contents or link targets."""
    def metadata_only(row):
        if row is None:
            return None
        if not isinstance(row, dict) or not isinstance(row.get('kind'), str):
            return {'kind': 'invalid'}
        kind = row['kind']
        if kind not in {'file', 'symlink', 'directory', 'special', 'missing', 'unreadable'}:
            return {'kind': 'invalid'}
        if set(row) - {'kind', 'sha256'}:
            return {'kind': 'invalid'}
        if 'sha256' in row:
            value = row['sha256']
            if kind != 'file' or not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
                return {'kind': 'invalid'}
            return {'kind': kind, 'sha256': value}
        return {'kind': kind}

    if not isinstance(actual, dict) or any(not isinstance(name, str) for name in actual):
        actual = {'<manifest>': {'kind': 'invalid'}}
    differences = []
    count = 0
    for name in sorted(set(expected) | set(actual)):
        before, after = expected.get(name), actual.get(name)
        if before == after:
            continue
        before, after = metadata_only(before), metadata_only(after)
        count += 1
        if len(differences) == 32:
            continue
        change = ('extra' if before is None else 'missing' if after is None else
                  'type' if before.get('kind') != after.get('kind') else 'changed')
        safe_name = name if Path(name).name == name and '/' not in name and '\\' not in name else '<invalid-name>'
        differences.append({
            'name': safe_name[:256], 'nameTruncated': len(safe_name) > 256,
            'nameSha256': hashlib.sha256(name.encode('utf-8', 'surrogatepass')).hexdigest(),
            'nameHashEncoding': 'utf-8-surrogatepass',
            'change': change, 'expected': before, 'actual': after})
    save(evidence / 'wheel-integrity-failure.json', {
        'boundary': boundary, 'expectedEntries': len(expected), 'actualEntries': len(actual),
        'differenceCount': count, 'differences': differences, 'truncated': count > len(differences)})
    raise ValueError('Wheel directory integrity differs; inspect bounded failure evidence')


def wheel_entry(path):
    """Do not traverse links, directories or special files in the flat wheel output."""
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            return {'kind': 'symlink'}
        if stat.S_ISDIR(mode):
            return {'kind': 'directory'}
        if not stat.S_ISREG(mode):
            return {'kind': 'special'}
        return {'kind': 'file', 'sha256': digest(path)}
    except FileNotFoundError:
        return {'kind': 'missing'}
    except OSError:
        return {'kind': 'unreadable'}


def wheel_archive_hashes(wheels, evidence):
    """Freeze the selected archives before metadata reads or installation."""
    actual = {p.name: wheel_entry(p) for p in wheels}
    require(len(actual) == len(wheels) and actual, 'Duplicate or empty wheel selection')
    if any(row['kind'] != 'file' for row in actual.values()):
        expected = {name: row if row['kind'] == 'file' else {'kind': 'file'}
                    for name, row in actual.items()}
        wheel_integrity_failure(expected, actual, evidence, 'selected-wheels')
    return {name: row['sha256'] for name, row in actual.items()}


def verify_wheel_directory(root, wheel_hashes, evidence, *, baseline=None):
    """Validate exact archives plus pinned uv's one known auxiliary file."""
    require(wheel_hashes and all(
        Path(name).name == name and name.endswith('.whl')
        and isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value)
        for name, value in wheel_hashes.items()), 'Invalid original wheel identities')
    expected = {name: {'kind': 'file', 'sha256': value} for name, value in wheel_hashes.items()}
    # uv 0.13.1 (62faed3351cfee2121b3c28b937b1f4b93233a93):
    # prepare_output_directory creates exactly b'*' with create-gitignore=true.
    # Do not baseline arbitrary extras or rewrite an existing sidecar.
    expected['.gitignore'] = {'kind': 'file', 'sha256': hashlib.sha256(b'*').hexdigest()}
    if baseline is not None and baseline != expected:
        wheel_integrity_failure(expected, baseline, evidence, 'baseline')
    try:
        mode = root.lstat().st_mode
        if not stat.S_ISDIR(mode):
            actual = {'<root>': {'kind': 'symlink' if stat.S_ISLNK(mode) else 'special'}}
        else:
            actual = {p.name: wheel_entry(p) for p in sorted(root.iterdir())}
    except OSError:
        actual = {'<root>': {'kind': 'unreadable'}}
    if actual != expected:
        wheel_integrity_failure(expected, actual, evidence, 'directory')
    # A flat directory of these exact regular files is its complete inventory.
    # This independently preserves archive hashes as well as auxiliary identity.
    return actual


def browser_access_tree(root, *, check_access=False):
    """Enumerate every entry without following links or hiding traversal errors."""
    files, directories, entries = {}, [], {}
    pending, path, operation = [root], root, 'inspect'
    failure = None
    try:
        while pending:
            path = pending.pop()
            operation = 'inspect'
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            kind = ('directory' if stat.S_ISDIR(info.st_mode) else
                    'file' if stat.S_ISREG(info.st_mode) else 'unsafe')
            entry = {'kind': kind, 'mode': stat.S_IMODE(info.st_mode),
                     'uid': info.st_uid, 'gid': info.st_gid}
            entries[relative] = entry
            require(len(entries) <= 4096, 'Browser entry limit exceeded')
            require(kind != 'unsafe' and not info.st_mode & 0o7000, 'Unsafe browser entry')
            if kind == 'directory':
                operation = 'enumerate'
                if check_access and not os.access(path, os.R_OK | os.X_OK, effective_ids=True):
                    raise PermissionError(13, 'Browser directory access denied')
                directories.append(relative)
                children = []
                with os.scandir(path) as scan:
                    for child in scan:
                        require(len(entries) + len(pending) + len(children) < 4096,
                                'Browser entry limit exceeded')
                        children.append(path / child.name)
                pending.extend(reversed(sorted(children)))
            else:
                operation = 'read'
                files[relative] = digest(path)
        require(files, 'Empty browser installation')
    except (OSError, ValueError) as error:
        relative = path.relative_to(root).as_posix()
        failure = {
            'path': relative[:256], 'pathTruncated': len(relative) > 256,
            'pathSha256': hashlib.sha256(relative.encode('utf-8', 'surrogatepass')).hexdigest(),
            **entries.get(relative, {'kind': 'unobserved'}),
            'operation': operation, 'errno': error.errno if isinstance(error, OSError) else None}
    return {'files': files, 'directories': sorted(directories), 'entries': entries, 'failure': failure}


def browser_readiness_probe(expected):
    """Read-only probe executed by the prepared interpreter as the test identity."""
    identity = {'uid': os.getuid(), 'euid': os.geteuid(), 'gid': os.getgid(),
                'egid': os.getegid(), 'groups': os.getgroups()}
    require(in_container(), 'Browser readiness requires the owned container')
    target = {'uid': 65532, 'euid': 65532, 'gid': 65532, 'egid': 65532, 'groups': []}
    if identity != target:
        return {
            'passed': False, 'identity': identity, 'failure': {'operation': 'identity'},
            'fileCount': 0, 'directoryCount': 0,
            'filesSha256': hashlib.sha256(b'{}').hexdigest(),
            'directoriesSha256': hashlib.sha256(b'[]').hexdigest()}
    actual = browser_access_tree(GUEST / 'browsers', check_access=True)
    files, directories = actual['files'], actual['directories']
    passed = (actual['failure'] is None and files == expected['files']
              and directories == expected['directories'])
    return {
        'passed': passed, 'identity': identity, 'failure': actual['failure'],
        'fileCount': len(files), 'directoryCount': len(directories),
        'filesSha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        'directoriesSha256': hashlib.sha256(json.dumps(directories).encode()).hexdigest()}


def validated_browser_receipt(value):
    """Refuse unexpected nested data instead of retaining arbitrary child output."""
    def number(item, maximum):
        return type(item) is int and 0 <= item <= maximum

    def sha256(item):
        return isinstance(item, str) and re.fullmatch('[a-f0-9]{64}', item) is not None

    if not isinstance(value, dict) or set(value) != {
            'passed', 'identity', 'failure', 'fileCount', 'directoryCount',
            'filesSha256', 'directoriesSha256'}:
        return None
    identity = value['identity']
    if (type(value['passed']) is not bool or not isinstance(identity, dict)
            or set(identity) != {'uid', 'euid', 'gid', 'egid', 'groups'}
            or not all(number(identity[key], 2**32 - 1) for key in ('uid', 'euid', 'gid', 'egid'))
            or not isinstance(identity['groups'], list) or len(identity['groups']) > 32
            or not all(number(group, 2**32 - 1) for group in identity['groups'])
            or not number(value['fileCount'], 4096) or not number(value['directoryCount'], 4096)
            or not sha256(value['filesSha256']) or not sha256(value['directoriesSha256'])):
        return None
    failure = value['failure']
    if failure is not None and failure != {'operation': 'identity'}:
        required = {'path', 'pathTruncated', 'pathSha256', 'kind', 'operation', 'errno'}
        if not isinstance(failure, dict) or not required <= set(failure) <= required | {'mode', 'uid', 'gid'}:
            return None
        path = failure['path']
        if (not isinstance(path, str) or len(path) > 256 or Path(path).is_absolute()
                or '..' in Path(path).parts or '\\' in path
                or type(failure['pathTruncated']) is not bool or not sha256(failure['pathSha256'])
                or failure['kind'] not in ('file', 'directory', 'unsafe', 'unobserved')
                or failure['operation'] not in ('inspect', 'enumerate', 'read')
                or (failure['errno'] is not None and not number(failure['errno'], 4095))
                or any(not number(failure[key], 0o7777 if key == 'mode' else 2**32 - 1)
                       for key in ('mode', 'uid', 'gid') if key in failure)):
            return None
    return value


def prepare_pwa_browser_access(python):
    """Change only owned browser metadata, then prove reads as UID/GID 65532."""
    require(in_container() and os.getuid() == 0, 'Browser preparation requires owned root container')
    root, evidence = GUEST / 'browsers', GUEST / 'evidence'
    report = {'passed': False, 'targetUid': 65532, 'targetGid': 65532, 'changes': [],
              'changedCount': 0, 'changesTruncated': False}
    try:
        before = browser_access_tree(root)
        report['beforeFailure'] = before['failure']
        require(before['failure'] is None, 'Cannot inspect complete browser tree')
        expected_files = json.loads((evidence / 'before.json').read_text())['browsers']
        require(before['files'] == expected_files, 'Browser bytes changed before ownership preparation')
        report.update(fileCount=len(before['files']), directoryCount=len(before['directories']))
        # Freeze diagnostics before any mutation. No path outside this tree is
        # adjusted; descriptors refuse links and retain the inspected inode.
        save(evidence / 'browser-readability.json', report)
        for relative, entry in before['entries'].items():
            path = root / relative
            mode = entry['mode']
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if entry['kind'] == 'directory':
                flags |= os.O_DIRECTORY
            info = path.lstat()
            fd = os.open(path, flags)
            try:
                opened = os.fstat(fd)
                require((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino)
                        and stat.S_IMODE(opened.st_mode) == mode
                        and (opened.st_uid, opened.st_gid) == (entry['uid'], entry['gid']),
                        'Browser entry changed during preparation')
                new_mode = mode | (0o500 if entry['kind'] == 'directory' else 0o400)
                os.fchown(fd, 65532, 65532)
                os.fchmod(fd, new_mode)
                current = os.fstat(fd)
                require(stat.S_IMODE(current.st_mode) == new_mode
                        and (entry['kind'] != 'file' or current.st_mode & 0o111 == mode & 0o111),
                        'Browser executable bits changed')
            finally:
                os.close(fd)
            report['changedCount'] += 1
            if len(report['changes']) < 32:
                report['changes'].append({
                    'path': relative[:256], 'pathTruncated': len(relative) > 256,
                    'pathSha256': hashlib.sha256(relative.encode('utf-8', 'surrogatepass')).hexdigest(),
                    'kind': entry['kind'], 'beforeMode': mode, 'beforeUid': entry['uid'],
                    'beforeGid': entry['gid'], 'afterMode': new_mode,
                    'afterUid': current.st_uid, 'afterGid': current.st_gid})
        report['changesTruncated'] = report['changedCount'] > len(report['changes'])
        after = browser_access_tree(root)
        require(after['failure'] is None and after['files'] == before['files']
                and after['directories'] == before['directories'], 'Browser content changed during preparation')
        require(all(row['uid'] == row['gid'] == 65532 for row in after['entries'].values()),
                'Browser ownership did not match target')
        expected = {'files': before['files'], 'directories': before['directories']}
        # No full guest-attest here: its evidence writes are intentionally root
        # owned during preparation. This child only reads browsers and stdout.
        probe = subprocess.run(
            [str(python), '-I', '-B', __file__, 'guest-browser-readiness'],
            input=json.dumps(expected), text=True, capture_output=True, check=False,
            user=65532, group=65532, extra_groups=(), cwd=GUEST / 'run',
            env=clean_env(), timeout=120)
        report['probeExit'] = probe.returncode
        require(len(probe.stdout) <= 16384, 'Oversized browser readiness receipt')
        observed = validated_browser_receipt(json.loads(probe.stdout))
        if observed is None:
            report['probe'] = {'valid': False, 'reason': 'malformed-receipt'}
            raise ValueError('Malformed browser readiness receipt')
        report['probe'] = observed
        require(probe.returncode == 0 and observed == {
            'passed': True,
            'identity': {'uid': 65532, 'euid': 65532, 'gid': 65532, 'egid': 65532, 'groups': []},
            'failure': None, 'fileCount': len(expected['files']),
            'directoryCount': len(expected['directories']),
            'filesSha256': hashlib.sha256(json.dumps(expected['files'], sort_keys=True).encode()).hexdigest(),
            'directoriesSha256': hashlib.sha256(json.dumps(expected['directories']).encode()).hexdigest()},
            'Browser tree is not readable by the actual test identity')
        report['passed'] = True
    except BaseException as error:
        report['errorType'] = type(error).__name__
        raise
    finally:
        save(evidence / 'browser-readability.json', report)


def download(spec, destination):
    with urllib.request.urlopen(spec['url'], timeout=60) as response, destination.open('xb') as output:
        shutil.copyfileobj(response, output)
    require(digest(destination) == spec['sha256'], 'Downloaded binary differs from pinned SHA256')


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def wheel_metadata(path):
    with zipfile.ZipFile(path) as wheel:
        names = [n for n in wheel.namelist() if n.endswith('.dist-info/METADATA')]
        require(len(names) == 1, 'Ambiguous wheel metadata')
        return Parser().parsestr(wheel.read(names[0]).decode())


def check_direct_requirements(wheels, names, python_version):
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    environment = {**default_environment(), 'python_version': '.'.join(python_version.split('.')[:2]),
                   'python_full_version': python_version}
    for path in wheels:
        info = wheel_metadata(path)
        extras = ('', 'client') if canonical(info['Name']) == 'amplifier-bundle-context-intelligence' else ('',)
        for text in info.get_all('Requires-Dist', []):
            requirement = Requirement(text)
            active = not requirement.marker or any(
                requirement.marker.evaluate({**environment, 'extra': extra}) for extra in extras)
            if active and requirement.url:
                require(canonical(requirement.name) in names, 'Undeclared active direct dependency: ' + text)


def verify_candidate_wheel(wheel_path, candidate):
    """Match every packaged payload byte and the complete declared source set."""
    build = tomllib.loads((candidate / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']
    mappings = {name: name for name in build['packages']}
    mappings.update({dest: source for source, dest in build.get('force-include', {}).items()})
    expected = {}
    for destination, source in mappings.items():
        path = candidate / source
        for item in ([path] if path.is_file() else path.rglob('*')):
            if item.is_file() and '__pycache__' not in item.parts and item.suffix != '.pyc':
                relative = '' if item == path else '/' + item.relative_to(path).as_posix()
                expected[destination + relative] = digest(item)
    with zipfile.ZipFile(wheel_path) as wheel:
        actual = {n: hashlib.sha256(wheel.read(n)).hexdigest() for n in wheel.namelist()
                  if not n.endswith('/') and '.dist-info/' not in n}
    require(actual == expected, 'Candidate wheel payload differs from declared source/resources')
    return actual


def verify_installed():
    """Validate every hashed RECORD entry, reject editables and foreign origins."""
    records = {}
    for dist in metadata.distributions():
        name = canonical(dist.metadata['Name'])
        require(name != 'amplifier-app-cli', 'CLI must not enter this runtime')
        direct = json.loads(dist.read_text('direct_url.json') or '{}')
        require(not direct.get('dir_info', {}).get('editable'), 'Editable distribution: ' + name)
        require(dist.read_text('WHEEL') and dist.read_text('RECORD'), 'Non-wheel distribution: ' + name)
        for row in dist.files or []:
            if row.hash is None:
                continue  # RECORD itself is covered by the before/after inventory.
            path = Path(dist.locate_file(row)).resolve()
            require(path.is_relative_to(GUEST / '.venv'), 'Installed RECORD escapes owned venv')
            require(row.hash.mode == 'sha256', 'Unsupported RECORD digest')
            raw = path.read_bytes()
            actual = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
            require(actual == row.hash.value and len(raw) == row.size, 'Changed installed payload: ' + str(row))
        records[name] = {'version': dist.version, 'direct': direct}
    return records


def extract_candidate(archive, destination):
    destination.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter='data')


def extract_evidence(archive, destination):
    """Only bounded regular evidence files; never restore links or devices."""
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        require(sum(member.size for member in members) <= 64 * 1024 * 1024, 'Evidence exceeds 64 MiB')
        for member in members:
            path = Path(member.name)
            require(not path.is_absolute() and '..' not in path.parts
                    and (member.isfile() or member.isdir()), 'Unsafe evidence member')
        destination.mkdir()
        tar.extractall(destination, members=members, filter='data')


def guest_prepare():
    require(in_container() and os.getuid() == 0, 'Preparation requires the owned container')
    env = clean_env()
    for folder in ('home', 'tmp', 'cache', 'bin', 'wheels', 'repos', 'run', 'evidence'):
        (GUEST / folder).mkdir(exist_ok=True)
    candidate = GUEST / 'candidate'
    extract_candidate(GUEST / 'candidate.tar', candidate)
    data = pins(candidate / 'scripts/default-installed-pins.json')
    identity = json.loads((GUEST / 'input.json').read_text())
    require(digest(GUEST / 'candidate.tar') == identity['archiveSha256'], 'Candidate archive mismatch')
    source_before = inventory(candidate)
    uv_wheel = GUEST / 'uv.whl'
    download(data['uv'], uv_wheel)
    with zipfile.ZipFile(uv_wheel) as wheel:
        members = [n for n in wheel.namelist() if n.endswith('.data/scripts/uv')]
        require(len(members) == 1, 'uv binary absent')
        (GUEST / 'bin/uv').write_bytes(wheel.read(members[0]))
    (GUEST / 'bin/uv').chmod(0o755)
    uv = GUEST / 'bin/uv'
    command([uv, 'python', 'install', data['python']], env=env, timeout=180)
    command([uv, 'venv', '--python', data['python'], GUEST / '.venv'], env=env)
    python = GUEST / '.venv/bin/python'
    packages = [candidate]
    sources = {}
    for row in data['repositories']:
        target = GUEST / 'repos' / row['name']
        remote = 'https://github.com/microsoft/' + row['name']
        if row.get('private'):
            require(digest(GUEST / 'loop.bundle') == identity['loopBundleSha256'], 'Loop bundle mismatch')
            command(['git', 'clone', '--quiet', GUEST / 'loop.bundle', target], env=env)
            command(['git', '-C', target, 'remote', 'set-url', 'origin', remote], env=env)
        else:
            command(['git', 'init', '--quiet', target], env=env)
            command(['git', '-C', target, 'remote', 'add', 'origin', remote], env=env)
            command(['git', '-C', target, 'fetch', '--quiet', '--depth=1', 'origin', row['commit']], env=env)
        command(['git', '-C', target, 'checkout', '--quiet', '--detach', row['commit']], env=env)
        head = command(['git', '-C', target, 'rev-parse', 'HEAD'], env=env, capture=True).stdout.strip()
        require(head == row['commit'], 'Repository pin mismatch')
        sources[row['name']] = {'commit': head, 'files': inventory(target)}
        for relative in row['packages']:
            package = target / relative
            require(package.resolve().is_relative_to(target) and (package / 'pyproject.toml').is_file(), 'Missing pinned package')
            packages.append(package)
    constraints = {}
    for package in tomllib.loads((candidate / 'uv.lock').read_text())['package']:
        if 'registry' in package['source']:
            constraints.setdefault(package['name'], set()).add(package['version'])
    constraint_file = GUEST / 'constraints.txt'
    constraint_file.write_text('\n'.join(f'{name}=={next(iter(values))}' for name, values in sorted(constraints.items()) if len(values) == 1) + '\n')
    build_constraint = GUEST / 'build-constraints.txt'
    build_constraint.write_text(data['buildRequirement'] + '\n')
    for package in packages:
        command([uv, 'build', '--wheel', '--no-sources', '--python', python,
                 '--build-constraint', build_constraint, '--out-dir', GUEST / 'wheels', package], env=env, timeout=120)
    core = GUEST / 'wheels' / Path(urllib.parse.urlsplit(data['core']['url']).path).name
    download(data['core'], core)
    wheels = sorted((GUEST / 'wheels').glob('*.whl'))
    stage = identity.get('stage', 'ic13')
    require(stage in ('ic13', 'desktop-safety', *FIXED_CASES), 'Unknown fixed stage')
    original_wheels = wheel_archive_hashes(wheels, GUEST / 'evidence') if stage in FIXED_CASES else None
    wheel_directory = (verify_wheel_directory(GUEST / 'wheels', original_wheels, GUEST / 'evidence')
                       if stage in FIXED_CASES else None)
    names = [canonical(wheel_metadata(p)['Name']) for p in wheels]
    require(len(names) == len(set(names)) == len(packages) + 1, 'Duplicate/missing distribution build')
    check_direct_requirements(wheels, names, data['python'])
    overrides = GUEST / 'overrides.txt'
    overrides.write_text('\n'.join(f'{name} @ {path.as_uri()}' for name, path in zip(names, wheels)) + '\n')
    app_wheel = wheels[names.index('amplifier-unified')]
    payload = verify_candidate_wheel(app_wheel, candidate)
    command([uv, 'pip', 'install', '--python', python, '--link-mode=copy', '--only-binary', 'amplifier-core',
             '--constraint', constraint_file, '--override', overrides, *wheels, *data['testRequirements'],
             'amplifier-bundle-context-intelligence[client]'], env=env, timeout=240)
    if stage == 'installed-pwa':
        preparation_env = pwa_preparation_environment(env)
        command(['apt-get', 'install', '-y', '--no-install-recommends', 'xvfb', 'xauth', 'x11-utils'],
                env=preparation_env, timeout=180)
        # Reuse the browser preparation, not uv sync / an editable app install.
        for args in client_helpers().browser_prepare_commands(candidate, str(uv), str(python),
                                                               install_dependencies=True):
            command(args, env=preparation_env, timeout=180)
    command([uv, 'pip', 'check', '--python', python], env=env)
    freeze = command([uv, 'pip', 'freeze', '--python', python], env=env, capture=True).stdout
    (GUEST / 'evidence/packages.txt').write_text(freeze)
    # Find installed resource path without importing the routing hook in this process.
    query = 'import importlib.util; from pathlib import Path; print(Path(importlib.util.find_spec("amplifier_module_hooks_routing").origin).parents[3] / "routing")'
    resource_path = Path(command([python, '-I', '-c', query], env=env, capture=True).stdout.strip())
    require(resource_path.is_relative_to(GUEST / '.venv') and not resource_path.exists(), 'Unexpected routing resource destination')
    routing_source = GUEST / 'repos/amplifier-bundle-routing-matrix/routing'
    shutil.copytree(routing_source, resource_path)
    require(inventory(resource_path) == inventory(routing_source), 'Routing resource copy mismatch')
    shutil.copytree(candidate / 'tests', GUEST / 'qualification/tests')
    require(inventory(candidate) == source_before, 'Build changed candidate source')
    for row in data['repositories']:
        require(inventory(GUEST / 'repos' / row['name']) == sources[row['name']]['files'], 'Build changed upstream source')
    save(GUEST / 'evidence/preparation.json', {
        **identity, 'pinsSha256': digest(candidate / 'scripts/default-installed-pins.json'),
        'candidatePayload': payload, 'sources': sources,
        'wheels': original_wheels if stage in FIXED_CASES else {p.name: digest(p) for p in wheels},
        **({'wheelDirectory': wheel_directory} if stage in FIXED_CASES else {}),
        'routingResources': {'path': str(resource_path), 'files': inventory(resource_path)},
        'candidateFiles': source_before, 'constraintsSha256': digest(constraint_file),
        'buildConstraintsSha256': digest(build_constraint), 'uvSha256': digest(uv),
        'status': 'prepared_not_accepted'})
    command([python, '-I', __file__, 'guest-attest'], env=env)
    # Git identity reads require matching ownership. The test root filesystem is
    # read-only, so owning a source directory cannot make it writable.
    if stage != 'root-guest':
        command(['chown', '-R', '65532:65532', GUEST / 'repos'], env=env)
    else:
        (GUEST / 'evidence').chmod(0o700)
    if stage == 'installed-pwa':
        prepare_pwa_browser_access(python)
    (GUEST / 'evidence').rename(GUEST / 'prepared-evidence')
    (GUEST / 'evidence').mkdir()


def guest_attest():
    require(in_container(), 'Attestation requires owned container')
    require(Path(sys.prefix) == GUEST / '.venv', 'Wrong interpreter')
    data = pins(GUEST / 'candidate/scripts/default-installed-pins.json')
    require(sys.version_info[:2] == (3, 13), 'Wrong Python')
    require(metadata.version('amplifier-core') == data['core']['version'], 'Wrong Core')
    from amplifier_core import AmplifierSession
    from amplifier_core._engine import RustSession
    require(AmplifierSession is RustSession, 'Native Core required')
    installed = verify_installed()
    preparation = json.loads((GUEST / 'evidence/preparation.json' if (GUEST / 'evidence/preparation.json').exists()
                              else GUEST / 'prepared-evidence/preparation.json').read_text())
    app = metadata.distribution('amplifier-unified')
    for relative, expected in preparation['candidatePayload'].items():
        require(digest(app.locate_file(relative)) == expected, 'Installed candidate differs from built payload: ' + relative)
    snapshot = {'installed': installed, 'venv': inventory(GUEST / '.venv', boundary=GUEST),
                'python': inventory(GUEST / 'python'),
                'candidate': inventory(GUEST / 'candidate'), 'repos': inventory(GUEST / 'repos'),
                'copiedTests': inventory(GUEST / 'qualification')}
    if preparation.get('stage') in FIXED_CASES:
        # Keep wheels and browser/system inputs in the same before/after fence.
        snapshot['wheels'] = verify_wheel_directory(
            GUEST / 'wheels', preparation['wheels'], GUEST / 'evidence',
            baseline=preparation['wheelDirectory'])
        snapshot['system'] = prerequisite_identity(preparation['stage'])
        if preparation['stage'] == 'installed-pwa':
            snapshot['browsers'] = inventory(GUEST / 'browsers')
    before = GUEST / 'prepared-evidence/before.json'
    if not before.exists():
        before = GUEST / 'evidence/before.json'
    if before.exists():
        require(snapshot == json.loads(before.read_text()), 'Installed/source/resource bytes changed during qualification')
        save(GUEST / 'evidence/after.json', snapshot)
    else:
        save(before, snapshot)


def desktop_result(path, group, exit_code):
    """Count the fixed safety discriminators separately, retaining failed cases."""
    expected = DESKTOP_CASES[group]
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    rows, counts, identities = [], {}, set()
    for case in cases:
        name = case.get('name', '')
        function = name.split('[', 1)[0]
        require(case.get('classname') == 'tests.test_default_desktop_safety'
                and function in expected and name not in identities, 'Unexpected or duplicate desktop selector')
        identities.add(name)
        counts[function] = counts.get(function, 0) + 1
        outcome = next((kind for kind in ('error', 'failure', 'skipped') if case.find(kind) is not None), 'passed')
        rows.append({'name': name, 'outcome': outcome})
    require(counts == expected, 'Missing desktop cases: ' + repr(counts))
    clean = not any(n.tag in {'error', 'failure', 'skipped'} for n in root.iter())
    clean = clean and all(not int(suite.get(key, '0')) for suite in root.iter('testsuite')
                          for key in ('errors', 'failures', 'skipped'))
    return {'group': group, 'exitCode': exit_code, 'passed': exit_code == 0 and clean, 'cases': rows}


def run_desktop_safety(python, env, evidence):
    """Fixed optional continuation; never reruns or inflates the IC-13 baseline."""
    report = {'baselineExecuted': False, 'groups': {}, 'passed': False}
    for group, functions in DESKTOP_CASES.items():
        junit = evidence / ('desktop-' + group + '.xml')
        require(not junit.exists(), 'Existing desktop evidence; reconcile, never replay')
        try:
            test = subprocess.run([str(python), '-I', '-B', '-m', 'pytest', '-c', '/dev/null',
                '-o', 'asyncio_mode=auto', '--import-mode=prepend', '--rootdir=' + str(GUEST / 'qualification'),
                '-p', 'pytest_asyncio.plugin', '-p', 'pytest_aiohttp.plugin', '-p', 'no:cacheprovider',
                '--basetemp=' + str(GUEST / 'tmp' / group), '--junitxml=' + str(junit), '-q', '-s', '-rA',
                *(str(GUEST / 'qualification/tests/test_default_desktop_safety.py') + '::' + name
                  for name in functions)], env=env, cwd=GUEST / 'run', stdin=subprocess.DEVNULL, timeout=120)
            report['groups'][group] = desktop_result(junit, group, test.returncode)
        except (subprocess.TimeoutExpired, OSError, ValueError, ET.ParseError) as error:
            report['groups'][group] = {'passed': False, 'error': type(error).__name__ + ': ' + str(error)}
        finally:
            save(evidence / 'desktop-safety-result.json', report)
    report['passed'] = all(row['passed'] for row in report['groups'].values())
    save(evidence / 'desktop-safety-result.json', report)
    require(report['passed'], 'Desktop safety check failed or incomplete; see separate group evidence')


def prerequisite_identity(stage):
    """Identity only; readiness is checked inside the actual execution container."""
    names = ['python3', 'sh', 'sleep', 'touch']
    if stage == 'installed-pwa':
        names += ['Xvfb', 'xdpyinfo', 'xauth']
    paths = {}
    for name in names:
        path = shutil.which(name, path='/usr/bin:/bin')
        require(path is not None, 'Required system executable missing: ' + name)
        resolved = Path(path).resolve()
        paths[name] = {'path': str(resolved), 'sha256': digest(resolved)}
    require(Path('/usr/bin/python3').is_file(), 'Workload requires actual /usr/bin/python3')
    return {'executables': paths, 'dpkgStatusSha256': digest('/var/lib/dpkg/status'),
            'workloadPython': command(['/usr/bin/python3', '--version'], capture=True).stdout.strip()}


def root_guest_preflight(env):
    require(in_container() and os.getuid() == os.geteuid() == 0, 'Owned Linux root stage required')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    # CAP_KILL=5, CAP_SETGID=6, CAP_SETUID=7. No extra capabilities.
    require(all(int(status[key].strip(), 16) == 0xe0 for key in ('CapEff', 'CapPrm', 'CapBnd'))
            and int(status['CapAmb'].strip(), 16) == 0, 'Root stage capability mismatch')
    require(status['NoNewPrivs'].strip() == '1', 'no-new-privileges required')
    with tempfile.TemporaryDirectory(dir=GUEST / 'tmp', prefix='preflight-') as folder:
        control = Path(folder)
        control.chmod(0o700)
        probe = subprocess.run(['/usr/bin/python3', '-I', '-c',
            'import os,sys; print(os.getuid(),os.getgid(),os.access(sys.argv[1],os.W_OK))', str(control)],
            user=65534, group=65534, extra_groups=(), env=env, cwd=GUEST / 'tmp',
            capture_output=True, text=True, check=True, timeout=5)
        require(probe.stdout.strip() == '65534 65534 False', 'Workload UID or receipt isolation failed')
    child = subprocess.Popen(['/usr/bin/python3', '-I', '-c', 'import time; time.sleep(30)'],
                             user=65534, group=65534, extra_groups=(), env=env, cwd=GUEST / 'tmp')
    try:
        identity = Path(f'/proc/{child.pid}/stat').read_text()
        os.kill(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
        require(child.returncode == -signal.SIGTERM, 'Cross-UID signal did not settle the workload')
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    return {'uid': 0, 'workloadUid': 65534, 'capabilities': ['KILL', 'SETGID', 'SETUID'],
            'procObserved': bool(identity), 'crossUidTermination': child.returncode,
            'privateControlDeniedToWorkload': True, 'system': prerequisite_identity('root-guest')}


def fixed_result(path, stage, exit_code):
    """No extra, duplicate, parameter-suffixed or absent case can satisfy 8/1."""
    file, functions = FIXED_CASES[stage]
    expected = {('tests.' + file.removesuffix('.py'), name) for name in functions}
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    identities = [(c.get('classname', ''), c.get('name', '')) for c in cases]
    require(len(identities) == len(set(identities)) and set(identities) == expected,
            'Missing, unexpected or duplicate fixed-stage identities')
    clean = not any(n.tag in {'error', 'failure', 'skipped'} for n in root.iter())
    clean = clean and all(not int(s.get(key, '0')) for s in root.iter('testsuite')
                          for key in ('errors', 'failures', 'skipped'))
    return {'stage': stage, 'exitCode': exit_code, 'passed': exit_code == 0 and clean,
            'cases': [{'classname': c.get('classname'), 'name': c.get('name'),
                       'outcome': next((k for k in ('error', 'failure', 'skipped')
                                        if c.find(k) is not None), 'passed')} for c in cases]}


def retain_pwa_artifacts(base, evidence):
    """Only the two synthetic test outputs; never the browser profile or app home."""
    result = {'retained': [], 'missing': []}
    for name, maximum in (('installed-pwa.json', 1024 * 1024), ('installed-pwa.png', 8 * 1024 * 1024)):
        matches = list(base.rglob(name))
        require(len(matches) <= 1, 'Ambiguous PWA evidence: ' + name)
        if not matches:
            result['missing'].append(name)
            continue
        path, = matches
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base.resolve()),
                'Unsafe PWA evidence: ' + name)
        require(path.stat().st_size <= maximum, 'PWA evidence exceeds bound: ' + name)
        data = path.read_bytes()
        if name.endswith('.json'):
            value = json.loads(data)
            require(isinstance(value, dict) and set(value) == {
                'browser', 'os', 'window', 'requests', 'nativeDragging', 'physicalAndroid', 'macOS'},
                'Unexpected PWA evidence schema')
        else:
            require(data.startswith(b'\x89PNG\r\n\x1a\n') and data.endswith(b'IEND\xaeB`\x82'),
                    'Incomplete PWA screenshot')
        target = evidence / name
        with target.open('xb') as output:
            output.write(data)
        result['retained'].append({'name': name, 'bytes': len(data), 'sha256': digest(target)})
    save(evidence / 'pwa-artifacts.json', result)
    return result


def pwa_preflight_receipt(preflight, env, python):
    require(Path(preflight['actual_interpreter']) == python, 'PWA preflight used another interpreter')
    expected = json.loads((GUEST / 'candidate/tests/web/package-lock.json').read_text())[
        'packages']['node_modules/playwright']['version']
    require(preflight['prerequisites']['playwright_version'] == expected, 'PWA browser package is not lock-matched')
    preflight['interpreter'] = preflight['actual_interpreter']
    preflight['environment'] = {key: env[key] for key in
        (*preflight['environment'], 'DISPLAY', 'XDG_RUNTIME_DIR')}
    require(Path(preflight['prerequisites']['chromium']).is_relative_to(Path(env['PLAYWRIGHT_BROWSERS_PATH'])),
            'PWA preflight browser is outside the prepared browser tree')
    return preflight


def copy_prepared_evidence(evidence, stage):
    shutil.copytree(GUEST / 'prepared-evidence', evidence, dirs_exist_ok=True)
    if stage == 'root-guest':
        # copytree copies directory metadata even with dirs_exist_ok.
        evidence.chmod(0o700)
        info = evidence.stat()
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
                'Root evidence directory is not private')


def run_fixed_stage(python, env, evidence, stage):
    report = {'stage': stage, 'passed': False, 'cleanup': 'not_started'}
    display = None
    base = GUEST / 'tmp' / stage
    try:
        if stage == 'root-guest':
            save(evidence / 'root-preflight.json', root_guest_preflight(env))
        else:
            state = GUEST / 'run/pwa-state'
            state.mkdir(mode=0o700)
            for name in ('home', 'tmp', 'cache', 'bin'):
                (state / name).mkdir(mode=0o700)
            env = {**env, **client_helpers().describe('browser-installed', state)['environment'],
                   'DISPLAY': ':99', 'PLAYWRIGHT_BROWSERS_PATH': str(GUEST / 'browsers'),
                   'XDG_RUNTIME_DIR': str(GUEST / 'run')}
            with (evidence / 'xvfb.log').open('wb') as log:
                display = subprocess.Popen(['/usr/bin/Xvfb', ':99', '-screen', '0', '1280x900x24',
                                            '-nolisten', 'tcp', '-noreset', '-ac'],
                                           env=env, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                require(display.poll() is None, 'Private Xvfb exited before readiness')
                ready = subprocess.run(['/usr/bin/xdpyinfo', '-display', ':99'], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
                if ready.returncode == 0:
                    break
                time.sleep(0.1)
            else:
                raise ValueError('Private Xvfb display did not become ready')
            command([python, '-I', GUEST / 'candidate/scripts/integration_clients.py', 'preflight',
                     '--lane', 'browser-installed', '--state-dir', state,
                     '--output', evidence / 'pwa-preflight.json'], env=env)
            preflight = json.loads((evidence / 'pwa-preflight.json').read_text())
            save(evidence / 'pwa-preflight.json', pwa_preflight_receipt(preflight, env, python))
        file, functions = FIXED_CASES[stage]
        junit = evidence / 'junit.xml'
        require(not junit.exists(), 'Existing fixed-stage result; do not replay')
        test = subprocess.run([str(python), '-I', '-B', '-m', 'pytest', '-c', '/dev/null',
            '-o', 'asyncio_mode=auto', '--import-mode=prepend', '--rootdir=' + str(GUEST / 'qualification'),
            '-p', 'pytest_asyncio.plugin', '-p', 'pytest_aiohttp.plugin', '-p', 'no:cacheprovider',
            '--basetemp=' + str(base), '--junitxml=' + str(junit), '-q', '-s', '-rA',
            *(str(GUEST / 'qualification/tests' / file) + '::' + name for name in functions)],
            env=env, cwd=GUEST / 'run', stdin=subprocess.DEVNULL, timeout=300)
        report['exitCode'] = test.returncode
        report.update(fixed_result(junit, stage, test.returncode))
        save(evidence / 'fixed-stage-result.json', report)
        lane = 'browser-installed' if stage == 'installed-pwa' else 'root-guest'
        command([python, '-I', GUEST / 'candidate/scripts/integration_checks.py', 'check',
                 '--lane', lane, '--junit', junit], env=env)
        require(report['passed'], 'Fixed-stage pytest failed or skipped')
    except BaseException as error:
        report.update(passed=False, error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        try:
            if stage == 'installed-pwa':
                artifacts = retain_pwa_artifacts(base, evidence)
                require(not report['passed'] or not artifacts['missing'], 'Successful PWA check lacks evidence')
        except BaseException as error:
            report.update(passed=False, artifactError=type(error).__name__ + ': ' + str(error))
            raise
        finally:
            try:
                if display is not None:
                    display.terminate()
                    display.wait(timeout=5)
                report['cleanup'] = 'display_closed' if stage == 'installed-pwa' else 'container_containment_pending'
            except BaseException as error:
                report.update(passed=False, cleanup='unresolved', cleanupError=type(error).__name__ + ': ' + str(error))
                if display is not None and display.poll() is None:
                    display.kill()
                    display.wait(timeout=5)
                raise
            finally:
                save(evidence / 'fixed-stage-result.json', report)


RESIDUAL_HELPER_COUNTS = {
    'tests.test_integration_clients': 16,
    'tests.test_integration_checks': 115,
}


def residual_helper_result(path, exit_code):
    """The two changed helper suites have their own receipt, never product counts."""
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    identities = [(case.get('classname', ''), case.get('name', '')) for case in cases]
    counts = {}
    rows = []
    for case, (module, name) in zip(cases, identities):
        counts[module] = counts.get(module, 0) + 1
        rows.append({'classname': module, 'name': name, 'outcome': next(
            (key for key in ('error', 'failure', 'skipped') if case.find(key) is not None), 'passed')})
    clean = not any(node.tag in {'error', 'failure', 'skipped'} for node in root.iter())
    clean = clean and all(not int(suite.get(key, '0')) for suite in root.iter('testsuite')
                          for key in ('errors', 'failures', 'skipped'))
    identities_valid = len(identities) == len(set(identities)) and counts == RESIDUAL_HELPER_COUNTS
    return {'passed': exit_code == 0 and clean and identities_valid,
            'exitCode': exit_code, 'counts': counts, 'expectedCounts': RESIDUAL_HELPER_COUNTS,
            'identitiesValid': identities_valid, 'cases': rows}


def run_residual_helpers(python, env, evidence):
    junit = evidence / 'helper-contracts.xml'
    require(not junit.exists(), 'Existing helper evidence; reconcile, never replay')
    report = {'passed': False, 'exitCode': None, 'expectedCounts': RESIDUAL_HELPER_COUNTS}
    try:
        result = subprocess.run([str(python), '-I', '-B', '-m', 'pytest', '--noconftest',
            '--import-mode=importlib', '-c', '/dev/null', '-p', 'no:cacheprovider', '-q',
            '--rootdir=' + str(GUEST / 'candidate'),
            # Only these source-helper contracts import scripts.*. The separate
            # installed-product pytest process receives no pythonpath override.
            '-o', 'pythonpath=' + str(GUEST / 'candidate'),
            '--basetemp=' + str(GUEST / 'tmp/helper-pytest'), '--junitxml=' + str(junit),
            str(GUEST / 'candidate/tests/test_integration_clients.py'),
            str(GUEST / 'candidate/tests/test_integration_checks.py')],
            env=env, cwd=GUEST / 'run', stdin=subprocess.DEVNULL, timeout=120)
        report['exitCode'] = result.returncode
        report.update(residual_helper_result(junit, result.returncode))
        require(report['passed'], 'Client/owner helper contracts failed or incomplete')
        return report
    except BaseException as error:
        report.update(passed=False, error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        save(evidence / 'helper-contracts-result.json', report)


def guest_test(desktop_safety=False, root_guest=False, installed_pwa=False):
    stage = selected_stage(desktop_safety, root_guest, installed_pwa)
    require(in_container() and os.getuid() == (0 if root_guest else 65532),
            'Stage-specific owned test container required')
    env = clean_env()
    env.update(UNIFIED_RUNTIME_PYTHON=str(GUEST / '.venv/bin/python'),
               UNIFIED_BEHAVIOR_REPOS=str(GUEST / 'repos'),
               WARM_FOUNDATION_PATH=str(GUEST / 'repos/amplifier-foundation'),
               WARM_ROUTING_PATH=str(GUEST / 'repos/amplifier-bundle-routing-matrix'))
    python = GUEST / '.venv/bin/python'
    evidence = GUEST / 'evidence'
    copy_prepared_evidence(evidence, stage)
    junit = evidence / 'junit.xml'
    require(not junit.exists(), 'Existing result; reconcile, never replay')
    result = {'passed': False, 'adapterExit': None, 'testExit': None, 'checkExit': None, 'attestation': False,
              'stage': stage, 'baselineExecuted': stage == 'ic13'}
    try:
        command([python, '-I', __file__, 'guest-attest'], env=env)
        command([python, '-I', '-B', '-m', 'pytest', '--noconftest', '--import-mode=importlib',
                 '-c', '/dev/null', '-p', 'no:cacheprovider', '-q',
                 '--basetemp=' + str(GUEST / 'tmp/adapter-pytest'),
                 '--junitxml=' + str(evidence / 'adapter.xml'),
                 GUEST / 'candidate/tests/test_default_installed_adapter.py'],
                env=env, cwd=GUEST / 'run')
        result['adapterExit'] = 0
        if stage in FIXED_CASES:
            result['helperContracts'] = run_residual_helpers(python, env, evidence)
            run_fixed_stage(python, env, evidence, stage)
            result.update(passed=True, testExit=0, checkExit=0)
            return
        if desktop_safety:
            run_desktop_safety(python, env, evidence)
            result['passed'] = True
            return  # finally still attests and saves; no IC-13 execution/count change.
        test = subprocess.run([str(python), '-I', '-B', '-m', 'pytest', '-c', '/dev/null',
            '-o', 'asyncio_mode=auto', '--import-mode=prepend', '--rootdir=' + str(GUEST / 'qualification'),
            '-p', 'pytest_asyncio.plugin', '-p', 'pytest_aiohttp.plugin', '-p', 'no:cacheprovider',
            '--basetemp=' + str(GUEST / 'tmp/pytest'), '--junitxml=' + str(junit), '-q', '-s', '-rA',
            *(str(GUEST / 'qualification/tests' / name) for name in SUITES)],
            env=env, cwd=GUEST / 'run', stdin=subprocess.DEVNULL, timeout=300)
        result['testExit'] = test.returncode
        command([python, '-I', GUEST / 'candidate/scripts/integration_checks.py', 'check',
                 '--lane', 'default-installed', '--junit', junit], env=env)
        result['checkExit'] = 0
        import xml.etree.ElementTree as ET
        require(len(list(ET.parse(junit).iter('testcase'))) == 12, 'Expected all 12 unchanged baseline cases')
        require(test.returncode == 0, 'pytest failed')
        result['passed'] = True
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        # An error here cannot turn a failed/incomplete run into acceptance.
        try:
            command([python, '-I', __file__, 'guest-attest'], env=env)
            result['attestation'] = True
        except BaseException as error:
            result.update(passed=False, attestationError=type(error).__name__ + ': ' + str(error))
            raise
        finally:
            save(evidence / 'result.json', result)


def git(root, *args):
    return command(['git', '-C', root, *args], capture=True).stdout.strip()


def checked_checkout(root, revision=None):
    require(Path(git(root, 'rev-parse', '--show-toplevel')).resolve() == root.resolve(), 'Checkout root required')
    require(not git(root, 'status', '--porcelain', '--untracked-files=all'), 'Clean committed source required')
    head = git(root, 'rev-parse', 'HEAD')
    require(re.fullmatch('[0-9a-f]{40}', head), 'Invalid source revision')
    require(revision is None or head == revision, 'Wrong checkout revision')
    return head


def test_container_options(stage):
    root = stage == 'root-guest'
    require(stage in ('ic13', 'desktop-safety', *FIXED_CASES), 'Unknown fixed stage')
    options = ['--network=none', '--user=0:0' if root else '--user=65532:65532',
               '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=4g',
               '--pids-limit=256', '--read-only']
    if root:
        options += ['--cap-add=SETUID', '--cap-add=SETGID', '--cap-add=KILL']
    for folder in ('home', 'tmp', 'run', 'cache', 'evidence'):
        owner = '0' if root else '65532'
        mode = '0755' if root and folder == 'tmp' else '0700'
        options += ['--tmpfs', str(GUEST / folder) + f':rw,uid={owner},gid={owner},mode={mode},size=512m']
    if stage == 'installed-pwa':
        options += ['--tmpfs', '/tmp:rw,uid=0,gid=0,mode=1777,size=512m']
    return options


def verify_test_isolation(info, stage):
    root = stage == 'root-guest'
    config = info['HostConfig']
    caps = {name.removeprefix('CAP_') for name in config.get('CapAdd') or []}
    expected_caps = {'SETUID', 'SETGID', 'KILL'} if root else set()
    options = test_container_options(stage)
    expected_tmpfs = dict(options[index + 1].split(':', 1)
                          for index, option in enumerate(options) if option == '--tmpfs')
    # Docker records --tmpfs in HostConfig.Tmpfs. Pre-start Mounts may be empty;
    # it is not the authoritative destination/options map for these flags.
    mounts = info.get('Mounts', [])
    require(config['NetworkMode'] == 'none' and config['ReadonlyRootfs']
            and not config.get('Binds') and not config['Privileged']
            and config.get('PidMode', '') in ('', 'private')
            and info['Config']['User'] == ('0:0' if root else '65532:65532')
            and config['CapDrop'] == ['ALL'] and caps == expected_caps
            and config['Memory'] == 4 * 1024 ** 3 and config['NanoCpus'] == 2 * 10 ** 9
            and config['PidsLimit'] == 256
            and 'no-new-privileges' in config['SecurityOpt']
            and config.get('Tmpfs') == expected_tmpfs
            and not config.get('Mounts') and not config.get('VolumesFrom')
            and len(mounts) == len({m.get('Destination') for m in mounts})
            and all(m.get('Type') == 'tmpfs' and m.get('Destination') in expected_tmpfs
                    and not m.get('Source') for m in mounts),
            'Unexpected test container isolation')
    return {key: config.get(key) for key in
            ('NetworkMode', 'ReadonlyRootfs', 'Memory', 'NanoCpus', 'PidsLimit',
             'CapDrop', 'CapAdd', 'PidMode', 'SecurityOpt', 'Tmpfs')}


def hosted_run(work, output, loop_source, desktop_safety=False, root_guest=False, installed_pwa=False):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
            'This manual adapter requires an ordinary GitHub-hosted runner; it cannot provision a host')
    require(sys.platform == 'linux', 'Linux hosted runner required')
    stage = selected_stage(desktop_safety, root_guest, installed_pwa)
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    require(work.is_relative_to(temporary) and output.is_relative_to(temporary)
            and work != temporary and output != temporary
            and not work.is_relative_to(output) and not output.is_relative_to(work),
            'Distinct fresh paths below the hosted runner temporary directory required')
    require(not work.exists() and not output.exists(), 'Fresh work/output directories required; inspect existing receipts')
    data = pins(ROOT / 'scripts/default-installed-pins.json')
    revision = checked_checkout(ROOT)
    loop = next(r for r in data['repositories'] if r.get('private'))
    checked_checkout(loop_source, loop['commit'])
    origin = git(loop_source, 'remote', 'get-url', 'origin').removesuffix('.git')
    require(origin in ('https://github.com/microsoft/amplifier-module-loop-live',
                       'git@github.com:microsoft/amplifier-module-loop-live'), 'Wrong private source origin')
    work.mkdir(parents=True)
    output.mkdir(parents=True)
    operation = uuid.uuid4().hex
    state = {'operation': operation, 'candidate': revision, 'phase': 'source', 'passed': False,
             'stage': stage,
             'containers': [], 'image': None, 'cleanup': 'not_started', 'effects': []}
    uncertain = False

    def effect(argv, timeout=120, capture=True):
        nonlocal uncertain
        state['effects'].append({'argv': list(map(str, argv)), 'state': 'attempting'})
        save(output / 'operation.json', state)
        try:
            result = command(argv, timeout=timeout, capture=capture)
        except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit, OSError):
            uncertain = True
            state['effects'][-1]['state'] = 'unknown'
            raise
        except subprocess.CalledProcessError as error:
            # Only a completed exec with an ordinary guest exit establishes
            # process failure. Docker transport/create/commit errors may leave
            # daemon effects; retain their identity and do not remove/replay.
            settled = len(argv) > 1 and argv[1] == 'exec' and 0 < error.returncode < 125
            uncertain = not settled
            state['effects'][-1]['state'] = 'failed' if settled else 'unknown'
            raise
        except BaseException:
            state['effects'][-1]['state'] = 'failed'
            raise
        else:
            state['effects'][-1]['state'] = 'completed'
            return result.stdout.strip() if capture else None
        finally:
            save(output / 'operation.json', state)

    def owned(kind, identity):
        info = json.loads(command(['docker', kind, 'inspect', identity], capture=True).stdout)[0]
        labels = info.get('Config', {}).get('Labels') or {}
        require(labels.get(LABEL) == operation, 'Refusing unrelated Docker resource')
        return info

    try:
        archive = work / 'candidate.tar'
        command(['git', '-C', ROOT, 'archive', '--format=tar', '--output', archive, revision])
        bundle = work / 'loop.bundle'
        command(['git', '-C', loop_source, 'bundle', 'create', bundle, 'HEAD'])
        identity = {'candidateRevision': revision, 'archiveSha256': digest(archive), 'stage': stage,
                    'loopRevision': loop['commit'], 'loopBundleSha256': digest(bundle)}
        save(work / 'input.json', identity)
        effect(['docker', 'pull', '--platform', data['platform'], data['image']], timeout=180, capture=False)
        base = json.loads(command(['docker', 'image', 'inspect', data['image']], capture=True).stdout)[0]
        state['baseImage'] = {'id': base['Id'], 'repoDigests': base.get('RepoDigests'),
                              'retention': 'Shared Docker cache; not owned teardown'}
        prep_name = 'default-prep-' + operation
        prep = effect(['docker', 'create', '--name', prep_name, '--label', LABEL + '=' + operation,
                       '--platform', data['platform'], '--cpus=2', '--memory=4g', '--pids-limit=256',
                       '--security-opt=no-new-privileges', base['Id'], 'sleep', 'infinity'])
        state['containers'].append(prep)
        effect(['docker', 'start', prep])
        effect(['docker', 'exec', prep, 'mkdir', '-p', str(GUEST)])
        for file in (archive, bundle, work / 'input.json'):
            effect(['docker', 'cp', file, prep + ':' + str(GUEST / file.name)])
        # Script bytes come from the same committed archive; no working-tree copy.
        effect(['docker', 'exec', prep, 'sh', '-ec',
                'mkdir -p /opt/defaults/evidence && '
                'apt-get update && apt-get install -y --no-install-recommends python3 python3-packaging git ca-certificates libpam0g && '
                'mkdir /opt/defaults/bootstrap && tar -xf /opt/defaults/candidate.tar -C /opt/defaults/bootstrap '
                'scripts/qualify_default_installed.py && '
                'python3 /opt/defaults/bootstrap/scripts/qualify_default_installed.py guest-prepare'],
               timeout=900, capture=False)
        effect(['docker', 'cp', prep + ':' + str(GUEST / 'prepared-evidence'), output / 'preparation-evidence'])
        state['preparationEvidenceRetained'] = True
        effect(['docker', 'stop', '--time=10', prep])
        image = effect(['docker', 'commit', '--change', 'LABEL ' + LABEL + '=' + operation, prep])
        state['image'] = image
        owned('image', image)
        require(not owned('container', prep)['State']['Running'], 'Preparation must stop before tests')
        test = effect(['docker', 'create', '--name', 'default-test-' + operation,
                       '--label', LABEL + '=' + operation, *test_container_options(stage),
                       image, 'sh', '-c',
                       '/opt/defaults/.venv/bin/python -I /opt/defaults/candidate/scripts/qualify_default_installed.py guest-test'
                       + ('' if stage == 'ic13' else ' --' + stage) + '; '
                       'code=$?; printf "%s\\n" "$code" > /opt/defaults/evidence/exit-code.tmp; '
                       'mv /opt/defaults/evidence/exit-code.tmp /opt/defaults/evidence/exit-code; exec sleep infinity'])
        state['containers'].append(test)
        info = owned('container', test)
        state['testIsolation'] = verify_test_isolation(info, stage)
        state['phase'] = 'testing'
        effect(['docker', 'start', test])
        # Keep the container alive until evidence has been copied: tmpfs data
        # disappears on stop. This waits only for this invocation's exit record.
        exit_code = effect(['docker', 'exec', test, 'sh', '-c',
                            'while [ ! -f /opt/defaults/evidence/exit-code ]; do sleep 1; done; '
                            'cat /opt/defaults/evidence/exit-code'], timeout=420)
        state['testExit'] = exit_code
        require(not owned('container', test)['State']['OOMKilled'], 'Test container exceeded its memory limit')
        with (output / 'tests.log').open('w') as log:
            subprocess.run(['docker', 'logs', test], stdout=log, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, check=True, timeout=30)
        # Docker archive-copy does not read runtime tmpfs mounts. Stream from
        # the still-running process namespace before stopping the container.
        state['evidenceTransfer'] = 'attempting'
        save(output / 'operation.json', state)
        try:
            with (output / 'evidence.tar').open('xb') as archive_output:
                subprocess.run(['docker', 'exec', test, 'tar', '-C', str(GUEST / 'evidence'), '-cf', '-', '.'],
                               stdout=archive_output, stdin=subprocess.DEVNULL, check=True, timeout=60)
            extract_evidence(output / 'evidence.tar', output / 'evidence')
            state['evidenceTransfer'] = 'retained'
        except BaseException:
            uncertain = True
            state['evidenceTransfer'] = 'unknown_or_incomplete'
            raise
        require(exit_code == '0', 'Installed-default container failed; inspect retained evidence')
        require(json.loads((output / 'evidence/result.json').read_text())['passed'], 'No successful qualification receipt')
        state['passed'] = True
        state['phase'] = 'qualified'
    except BaseException as error:
        state.update(phase='unknown' if uncertain else 'failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        if uncertain:
            state['cleanup'] = 'unresolved; inspect operation labels/names before any replay or removal'
        else:
            try:
                for container in reversed(state['containers']):
                    owned('container', container)
                    # Retain partial preparation evidence before any removal.
                    if container == state['containers'][0] and not state.get('preparationEvidenceRetained'):
                        require(owned('container', container)['State']['Running'],
                                'Stopped preparation without retained evidence; preserve for reconciliation')
                        for folder in ('evidence', 'prepared-evidence'):
                            found = subprocess.run(['docker', 'exec', container, 'test', '-d', str(GUEST / folder)],
                                stdin=subprocess.DEVNULL, timeout=30)
                            if found.returncode == 0:
                                effect(['docker', 'cp', container + ':' + str(GUEST / folder),
                                        output / ('preparation-' + folder)])
                            else:
                                require(found.returncode == 1, 'Cannot inspect preparation evidence')
                    # Capture preparation diagnostics even when no test container exists.
                    with (output / (container[:12] + '.log')).open('w') as log:
                        subprocess.run(['docker', 'logs', container], stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, timeout=30, check=True)
                    effect(['docker', 'rm', '--force', container])
                if state['image']:
                    owned('image', state['image'])
                    effect(['docker', 'image', 'rm', state['image']])
                remaining = command(['docker', 'ps', '-aq', '--filter', 'label=' + LABEL + '=' + operation], capture=True).stdout.strip()
                images = command(['docker', 'images', '-q', '--filter', 'label=' + LABEL + '=' + operation], capture=True).stdout.strip()
                require(not remaining and not images, 'Owned Docker resources remain')
                state['cleanup'] = 'verified_absent'
            except BaseException as error:
                state.update(cleanup='unresolved', cleanupError=type(error).__name__ + ': ' + str(error), passed=False)
        save(output / 'operation.json', state)
    require(state['cleanup'] == 'verified_absent' and state['passed'], 'Qualification or cleanup incomplete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'guest-prepare', 'guest-attest', 'guest-test',
                                           'guest-browser-readiness'])
    parser.add_argument('--work', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--loop-source', type=Path)
    stages = parser.add_mutually_exclusive_group()
    stages.add_argument('--desktop-safety', action='store_true',
                        help='Run only the separate fixed 22+3 desktop proof, not the IC-13 baseline')
    stages.add_argument('--root-guest', action='store_true', help='Run only eight isolated UID-separated guest checks')
    stages.add_argument('--installed-pwa', action='store_true', help='Run only the headed installed-PWA check')
    args = parser.parse_args()
    if args.command == 'run':
        require(args.work and args.output and args.loop_source, 'run requires --work, --output and --loop-source')
        hosted_run(args.work.resolve(), args.output.resolve(), args.loop_source.resolve(),
                   args.desktop_safety, args.root_guest, args.installed_pwa)
    elif args.command == 'guest-test':
        guest_test(args.desktop_safety, args.root_guest, args.installed_pwa)
    elif args.command == 'guest-browser-readiness':
        require(not (args.desktop_safety or args.root_guest or args.installed_pwa),
                'No stage override for the read-only browser probe')
        raw = sys.stdin.read(2 * 1024 * 1024 + 1)
        require(len(raw) <= 2 * 1024 * 1024, 'Oversized browser readiness input')
        result = browser_readiness_probe(json.loads(raw))
        print(json.dumps(result))
        raise SystemExit(0 if result['passed'] else 1)
    else:
        require(not (args.desktop_safety or args.root_guest or args.installed_pwa),
                'Stage flags apply only to run/guest-test')
        {'guest-prepare': guest_prepare, 'guest-attest': guest_attest}[args.command]()


if __name__ == '__main__':
    main()
