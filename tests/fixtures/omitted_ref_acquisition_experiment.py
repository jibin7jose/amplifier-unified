"""One task-owned synthetic acquisition experiment; never worker qualification.

Run only in the admitted offline managed guest. No dependency installation is
performed until source, Python, Git, exact uv and network readiness all pass.
The only installed distributions are the three dependency-free fixture wheels.
Their module bodies are retained verbatim, but never imported or mounted.

The caller must start the absolute guest Python with -I -S -B, using /usr/bin/env
-i PATH=<absolute-python-directory>:/usr/bin:/bin LANG=C.UTF-8.
The caller also supplies the host-parent network namespace to the original
sandbox helper. These checks do not create or authorize a guest. Never launch
this file, or the extracted uv binary, on the host.

--wheel must name the supplied, hash-bound wheel below; nothing downloads uv.
Only its fixed uv member is extracted, into the fresh guest root/tools/uv.
Each launched command owns a session/process group, including its descendants.
An unsettled group fails the experiment and defers final teardown to the guest
owner; its logs are not hashed and its working tree is not cleaned.
"""
from __future__ import annotations

import os
import sys

# Reject an unsafe launch before loading anything outside the built-in startup
# modules. This cannot undo startup side effects: env -i and the flags must be
# supplied by the caller, not set later inside this script.
STARTUP_PATH = os.path.dirname(sys.executable) + ':/usr/bin:/bin'
if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
        and sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.safe_path
        and 'site' not in sys.modules and os.path.isabs(sys.executable)):
    raise SystemExit('Requires an absolute guest Python launched with -I -S -B')
if (set(os.environ) - {'PATH', 'LANG', 'LC_CTYPE'}
        or os.environ.get('PATH') != STARTUP_PATH
        or os.environ.get('LANG') != 'C.UTF-8'
        or os.environ.get('LC_CTYPE', 'C.UTF-8') != 'C.UTF-8'):
    raise SystemExit('Requires the declared env -i startup allowlist and controlled PATH')

import argparse
import ast
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import stat
import subprocess
import time
import tomllib
from urllib.parse import parse_qs, urlsplit
import zipfile

FIXTURE_SHA256 = 'ee88ac215191cf511a600f492083a765465dd3839d749a90505e1d4ba7b41aec'
HELPER_SHA256 = '76967354992ef6f60376d730afd9bb1518c624aef53d09e29f30c6fc6df5c316'
WHEEL_SHA256 = '22759b8895e1103578de6fb4cd27e94b4399d021023378d29f01333717751fba'
WHEEL_BYTES = 17333810
WHEEL_NAME = 'uv-0.13.6-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
UV_MEMBER = 'uv-0.13.6.data/scripts/uv'
UV_BYTES = 37784096
UV_SHA256 = '23803452c0fbe125033054cd9ff245f1d649fb65b4b73f68429f4abda432436d'
CHECKOUT = Path(__file__).resolve().parents[2]


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def require(value, message):
    if not value:
        raise RuntimeError(message)


class VerifiedSourceLoader(importlib.machinery.SourceFileLoader):
    """Compile the exact verified bytes, never consulting or writing a pyc."""

    def __init__(self, name, path, expected):
        super().__init__(name, str(path))
        self.expected = expected

    def get_code(self, fullname):
        require(fullname == self.name, 'Unexpected source-loader module')
        source = Path(self.path).read_bytes()
        require(hashlib.sha256(source).hexdigest() == self.expected, 'Source binding changed at load')
        return compile(source, self.path, 'exec', dont_inherit=True)


def load_fixture(fixture_path, helper_path):
    original_spec = importlib.util.spec_from_file_location
    helper_loads = []

    def source_spec(name, location=None, **kwargs):
        # Only the exact sibling helper path is redirected. Other imports keep
        # their usual behavior under the already isolated, no-site interpreter.
        if location is not None and Path(location) == helper_path:
            require(name == 'qualification_diagnostics' and not kwargs, 'Unexpected helper import shape')
            helper_loads.append(name)
            return original_spec(name, location, loader=VerifiedSourceLoader(name, location, HELPER_SHA256))
        return original_spec(name, location, **kwargs)

    spec = original_spec('omitted_acquisition_source', fixture_path, loader=VerifiedSourceLoader(
        'omitted_acquisition_source', fixture_path, FIXTURE_SHA256))
    fixture = importlib.util.module_from_spec(spec)
    importlib.util.spec_from_file_location = source_spec
    try:
        spec.loader.exec_module(fixture)  # Full verified source; never run/main/make_repository.
    finally:
        importlib.util.spec_from_file_location = original_spec
    require(helper_loads == ['qualification_diagnostics'], 'Verified helper route was not used exactly once')
    return fixture


def extract_uv(wheel_path, root):
    """Validate the whole bound archive before writing only the fixed binary."""
    require(wheel_path == root.parent / 'supply' / WHEEL_NAME and not wheel_path.is_symlink(),
            'Only the declared supplied wheel path is allowed')
    with wheel_path.open('rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size == WHEEL_BYTES, 'Wrong supplied wheel type/size')
        payload = stream.read(WHEEL_BYTES + 1)
    require(len(payload) == WHEEL_BYTES and hashlib.sha256(payload).hexdigest() == WHEEL_SHA256,
            'Supplied uv wheel identity differs')
    binary = bytearray()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        require(len(members) == 12, 'Unexpected uv wheel member count')
        names = set()
        total = 0
        for member in members:
            name = member.filename
            require(name == member.orig_filename and re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name)
                    and all(part not in ('.', '..') for part in name.split('/')),
                    'Unsafe wheel member name')
            require(name not in names, 'Duplicate wheel member')
            names.add(name)
            require(not member.is_dir() and stat.S_ISREG(member.external_attr >> 16),
                    'Wheel members must be regular files, never links')
            require(not member.flag_bits & 1 and member.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                    'Unsupported wheel member encoding')
            require(0 <= member.file_size <= 48 * 1024 * 1024
                    and 0 <= member.compress_size <= WHEEL_BYTES
                    and 0 <= member.header_offset
                    and member.header_offset + 30 + member.compress_size <= WHEEL_BYTES,
                    'Wheel member exceeds bounds')
            total += member.file_size
        require(total <= 64 * 1024 * 1024 and UV_MEMBER in names, 'Wheel contents exceed declared bounds')
        require(all(str(parent) not in names for name in names for parent in PurePosixPath(name).parents),
                'Wheel file/directory name collision')
        require(archive.getinfo(UV_MEMBER).file_size == UV_BYTES, 'Wrong uv member size')
        # Reading all members checks their local headers, overlap checks and CRCs.
        # No member name becomes an extraction destination.
        for member in members:
            count = 0
            with archive.open(member) as stream:
                while chunk := stream.read(1024 * 1024):
                    count += len(chunk)
                    require(count <= member.file_size, 'Expanded wheel member exceeds bounds')
                    if member.filename == UV_MEMBER:
                        binary.extend(chunk)
            require(count == member.file_size, 'Incomplete wheel member')
    require(len(binary) == UV_BYTES and hashlib.sha256(binary).hexdigest() == UV_SHA256,
            'uv binary identity differs')
    require(binary[:7] == b'\x7fELF\x02\x01\x01' and int.from_bytes(binary[18:20], 'little') == 62,
            'uv must be ELF64 little-endian x86-64')
    tools = root / 'tools'
    tools.mkdir(mode=0o700)
    uv = tools / 'uv'
    with uv.open('xb') as stream:
        require(stream.write(binary) == UV_BYTES, 'Incomplete uv extraction')
    uv.chmod(0o700)
    require(sha(uv) == UV_SHA256 and stat.S_IMODE(uv.stat().st_mode) == 0o700, 'Extracted uv differs')
    return str(uv), {'wheelName': WHEEL_NAME, 'wheelSha256': WHEEL_SHA256, 'wheelBytes': WHEEL_BYTES,
                     'archiveMembers': len(members), 'expandedBytes': total, 'member': UV_MEMBER,
                     'binarySha256': UV_SHA256, 'binaryBytes': UV_BYTES, 'binaryPath': str(uv),
                     'elfMachine': 62, 'mode': '0700'}


def group_present(pgid):
    require(pgid > 1 and pgid != os.getpgrp(), 'Refusing a non-owned process group')
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_group_absent(process, seconds):
    deadline = time.monotonic() + seconds
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return False
    while group_present(process.pid):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True


def stop_owned_group(process, row):
    """Best-effort bounded termination; never replace the original failure."""
    row['termination'] = []
    for sig, seconds in ((signal.SIGTERM, 2), (signal.SIGKILL, 3)):
        step = {'signal': sig.name}
        row['termination'].append(step)
        try:
            if group_present(process.pid):
                try:
                    os.killpg(process.pid, sig)
                    step['sent'] = True
                except ProcessLookupError:
                    step['sent'] = False
            else:
                step['sent'] = False
            if wait_group_absent(process, seconds):
                return True
        except BaseException as error:
            step['errorType'] = type(error).__name__
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parent-netns', required=True)
    parser.add_argument('--parent-pidns', required=True)
    parser.add_argument('--wheel', type=Path, required=True)
    args = parser.parse_args()
    require(args.root.is_absolute() and args.output.is_absolute(), 'Absolute guest root/output required')
    require(not args.root.is_symlink() and not args.output.is_symlink(), 'Root/output cannot be symlinks')
    root, output = args.root.resolve(), args.output.resolve()
    require(not root.exists() and not output.exists(), 'Fresh root/output required; no replay')
    require(root != output and not root.is_relative_to(output) and not output.is_relative_to(root),
            'Separate work and evidence required')
    require(not root.is_relative_to(CHECKOUT) and not output.is_relative_to(CHECKOUT), 'Guest artifacts only')
    output.mkdir(parents=True, mode=0o700)
    report = {'status': 'not-run', 'phase': 'readiness', 'commands': [],
              'scope': 'synthetic-acquisition-only', 'historicalCause': 'UNKNOWN',
              'workerStarts': 0, 'canonicalDependenciesInstalled': 0}
    root_created = False
    previous_term = signal.getsignal(signal.SIGTERM)

    def terminate(signum, frame):
        # One deadline enters the normal owned-group cleanup. Repeated TERM
        # cannot interrupt that cleanup; the PID namespace is the outer bound.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise TimeoutError('Hosted experiment deadline reached')

    signal.signal(signal.SIGTERM, terminate)

    def save():
        if report.get('evidenceWriteFailure'):
            report['status'] = 'failed'
        try:
            (output / 'result.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        except BaseException as error:
            report['status'] = 'failed'
            report.setdefault('evidenceWriteFailure', {'errorType': type(error).__name__, 'message': str(error)})
            return False
        return True

    def command(label, argv, *, cwd, env=None, negative=False):
        row = {'label': label, 'argv': list(argv), 'cwd': str(cwd), 'status': 'starting',
               'returncode': None, 'expectedRefusal': negative, 'processGroupAbsent': None,
               'launchAttempted': False, 'cleanup': 'not-started'}
        report['commands'].append(row)
        require(save(), 'Cannot record command admission; no command started')
        stdout, stderr = output / (label + '.stdout'), output / (label + '.stderr')
        process, failure, absent = None, None, False
        try:
            with stdout.open('xb') as out, stderr.open('xb') as err:
                row['launchAttempted'] = True
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                           stdout=out, stderr=err, start_new_session=True, close_fds=True)
                row.update(pid=process.pid, processGroup=process.pid)
                process.wait(timeout=90)
                require(wait_group_absent(process, 2), 'Command exited with an unsettled process group')
                absent = True
                row.update(returncode=process.returncode, status='completed')
                require((process.returncode > 0) if negative else process.returncode == 0,
                        label + ' did not reach its declared outcome')
        except BaseException as error:
            failure = error
            row.update(status='failed', errorType=type(error).__name__, error=str(error))
            if process is not None and not absent:
                absent = stop_owned_group(process, row)
        finally:
            if process is not None:
                row.update(pid=process.pid, processGroup=process.pid, returncode=process.returncode,
                           processGroupAbsent=absent)
            else:
                # A constructor interrupted after spawning may not return a
                # handle. Never guess a PID or claim that nothing was launched.
                absent = not row['launchAttempted']
                if not absent:
                    row['launchOutcome'] = 'unknown-no-process-handle'
            row['cleanup'] = ('not-started' if not row['launchAttempted'] else
                              'confirmed-absent' if absent else 'unknown')
            for name, path, bound in (('stdout', stdout, 65536), ('stderr', stderr, 262144)):
                row[name] = {'path': path.name, 'complete': False}
                if not absent:
                    row[name]['reason'] = 'process-group-unsettled; not hashed'
                    continue
                try:
                    if path.exists():
                        size = path.stat().st_size
                        row[name]['bytes'] = size
                        require(size < bound, 'Synthetic output exceeded bound')
                        row[name].update(complete=True, sha256=sha(path))
                except BaseException as error:
                    row[name]['captureError'] = type(error).__name__
                    failure = failure or error
            if failure is not None:
                row['status'] = 'failed'
                row.setdefault('errorType', type(failure).__name__)
                row.setdefault('error', str(failure))
            if not save():
                failure = failure or RuntimeError('Command evidence could not be saved')
        if failure is not None:
            row['status'] = 'failed'
            raise failure
        return stdout.read_text().strip(), stderr.read_text()

    try:
        require(sys.platform == 'linux' and sys.version_info[:2] == (3, 13), 'Requires Linux/Python3.13')
        require(platform.machine() == 'x86_64', 'Supplied uv requires an x86_64 runner')
        libc = os.confstr('CS_GNU_LIBC_VERSION')
        match = re.fullmatch(r'glibc (\d+)\.(\d+)(?:\.\d+)?', libc or '')
        require(match is not None and (int(match[1]), int(match[2])) >= (2, 17), 'Requires guest glibc >=2.17')
        identity = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                        if ':' in line)
        require(os.getuid() != 0 and os.geteuid() == os.getuid() and os.getgid() != 0,
                'Workload must run as an unprivileged identity')
        require(identity['NoNewPrivs'].strip() == '1' and
                all(int(identity[key].strip(), 16) == 0 for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')),
                'Workload privilege drop is incomplete')
        report['launch'] = {'isolated': True, 'noSite': True, 'noBytecodeWrites': True,
                            'startupPath': STARTUP_PATH, 'environmentKeys': sorted(os.environ),
                            'machine': platform.machine(), 'libc': libc,
                            'uid': os.getuid(), 'gid': os.getgid(), 'noNewPrivileges': True,
                            'capabilities': 'all-zero'}
        fixture_path = CHECKOUT / 'tests/fixtures/omitted_ref_worker_probe.py'
        helper_path = CHECKOUT / 'scripts/qualification_diagnostics.py'
        require(sha(fixture_path) == FIXTURE_SHA256 and sha(helper_path) == HELPER_SHA256, 'Source binding changed')
        report['source'] = {'fixtureSha256': FIXTURE_SHA256, 'helperSha256': HELPER_SHA256,
                            'experimentSha256': sha(Path(__file__)), 'parentCommit': '346cf82bbcf9eab84c409896d77bda493b3b4f7e',
                            'loading': 'verified-full-source; fixture-and-helper-pyc-bypassed'}
        fixture = load_fixture(fixture_path, helper_path)
        report['network'] = fixture.require_network_sandbox(args.parent_netns)
        require(re.fullmatch(r'pid:\[\d+\]', args.parent_pidns) is not None
                and os.readlink('/proc/self/ns/pid') != args.parent_pidns,
                'Workload requires a separate PID namespace')
        report['pidNamespace'] = {'parent': args.parent_pidns, 'current': os.readlink('/proc/self/ns/pid')}
        git = shutil.which('git', path=STARTUP_PATH)
        require(git and Path(git).is_absolute(), 'Managed guest must already supply Git on controlled PATH')
        # The original helper clears all inherited variables and installs the exact
        # original Git wrapper/config. No canonical source rewrite/cache is added.
        root.mkdir(mode=0o700)
        root_created = True
        cache = root / 'uv-cache'
        fixture.isolated_environment(root, git, cache)
        require(not cache.exists(), 'Synthetic uv cache must be fresh')
        require(not args.wheel.is_symlink(), 'Supplied wheel cannot be a symlink')
        uv, report['uvSupply'] = extract_uv(args.wheel.resolve(strict=True), root)
        report['environment'] = {k: os.environ.get(k) for k in (
            'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_COUNT', 'GIT_ALLOW_PROTOCOL', 'GIT_CONFIG_GLOBAL',
            'UV_CACHE_DIR', 'UV_OFFLINE', 'UV_NO_INDEX', 'UV_NO_GITHUB_FAST_PATH', 'UV_PYTHON_DOWNLOADS')}
        report['wrapperSha256'] = sha(root / 'bin/git')
        report['gitconfig'] = (root / 'gitconfig').read_text()
        version, _ = command('uv-version', [uv, '--version'], cwd=root)
        report['uv'] = version
        require(version.split()[:2] == ['uv', '0.13.6'], 'Exact uv0.13.6 unavailable; no install or substitute')
        report['git'], _ = command('git-version', ['git', '--version'], cwd=root)
        report['python'] = {'version': sys.version, 'executable': sys.executable}
        require(save(), 'Cannot retain readiness evidence; no experiment started')
        report['status'] = 'running'
        report['phase'] = 'synthetic-repository'
        repo = root / 'origin'
        command('repo-init', ['git', 'init', '-b', 'main'], cwd=repo)
        for label, key, value in [('email', 'user.email', 'fixture@example.invalid'),
                                  ('name', 'user.name', 'Fixture'), ('sign', 'commit.gpgsign', 'false')]:
            command('config-' + label, ['git', 'config', key, value], cwd=repo)
        # Same per-distribution files and layout as make_repository at the bound
        # fixture revision. Bundle/settings files are unnecessary for acquisition;
        # omitting them avoids importing any canonical app or worker dependencies.
        inputs = []
        for name, (_, kind) in fixture.PACKAGES.items():
            directory = repo / 'modules' / kind
            package = directory / name.replace('-', '_')
            package.mkdir(parents=True)
            (directory / 'pyproject.toml').write_text(fixture.manifest_text({
                'project': {'name': name, 'version': '0.1.0'},
                'build-system': {'requires': [], 'build-backend': 'backend', 'backend-path': ['.']}}))
            (directory / 'backend.py').write_text(fixture.BACKEND)
            (package / '__init__.py').write_text({'context': fixture.CONTEXT, 'provider': fixture.PROVIDER,
                                                'hooks': fixture.HOOK}[kind])
            (package / '_proof.py').write_text(fixture.PROOF_MODULE)
            (package / '_payload.py').write_text('PAYLOAD = 0\n')
            metadata = tomllib.loads((directory / 'pyproject.toml').read_text())
            require(not metadata['project'].get('dependencies') and metadata['build-system']['requires'] == [],
                    'Fixture is not dependency-free')
            for path in sorted(directory.rglob('*')):
                if path.is_file():
                    inputs.append({'path': str(path.relative_to(repo)), 'sha256': sha(path)})
        report['syntheticInputs'] = inputs
        command('repo-add', ['git', 'add', '.'], cwd=repo)
        command('repo-commit', ['git', 'commit', '-m', 'baseline'], cwd=repo)
        revision, _ = command('repo-revision', ['git', 'rev-parse', 'HEAD'], cwd=repo)
        report['syntheticCommit'] = revision
        refspec = '+refs/heads/main:refs/remotes/origin/main'
        report['phase'] = 'git-positive'
        positive = root / 'git-positive'; positive.mkdir()
        command('positive-init', ['git', 'init'], cwd=positive)
        command('positive-fetch', ['git', 'fetch', '--force', '--update-head-ok', fixture.URL, refspec], cwd=positive)
        actual, _ = command('positive-revision', ['git', 'rev-parse', 'origin/main^0'], cwd=positive)
        require(actual == revision, 'Rewritten main did not resolve exact local commit')
        report['phase'] = 'git-disabled-rewrite-negative'
        negative = root / 'git-negative'; negative.mkdir()
        command('negative-init', ['git', 'init'], cwd=negative)
        _, error = command('negative-fetch', ['git', 'fetch', '--force', '--update-head-ok', fixture.URL, refspec],
                           cwd=negative, env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull}, negative=True)
        require("transport 'https' not allowed" in error, 'Disabled rewrite did not refuse at file-only boundary')
        command('negative-no-ref', ['git', 'rev-parse', '--verify', 'origin/main^0'], cwd=negative, negative=True)
        report['phase'] = 'uv-synthetic-install'
        project = root / 'project'; project.mkdir()
        data = {'project': {'name': 'omitted-acquisition-only', 'version': '0.0.0',
                            'requires-python': '>=3.13,<3.14', 'dependencies': list(fixture.PACKAGES)},
                'tool': {'uv': {'package': False, 'environments': ["sys_platform == 'linux'"],
                               'sources': {name: {'git': fixture.URL, 'branch': 'main', 'subdirectory': 'modules/' + kind}
                                           for name, (_, kind) in fixture.PACKAGES.items()}}}}
        manifest = fixture.manifest_text(data)
        (project / 'pyproject.toml').write_text(manifest)
        (output / 'synthetic-pyproject.toml').write_text(manifest)
        command('uv-sync', [uv, 'sync', '--project', str(project), '--python', '3.13'], cwd=root)
        lock_text = (project / 'uv.lock').read_text()
        (output / 'synthetic-uv.lock').write_text(lock_text)
        lock = tomllib.loads(lock_text)
        rows = {r['name']: r for r in lock['package']}
        require(set(rows) == {'omitted-acquisition-only', *fixture.PACKAGES}, 'Unexpected dependency in synthetic lock')
        site = project / '.venv/lib/python3.13/site-packages'
        installed = []
        for name, (_, kind) in fixture.PACKAGES.items():
            parsed = urlsplit(rows[name]['source']['git'])
            require(parsed._replace(query='', fragment='').geturl() == fixture.URL and parsed.fragment == revision,
                    'Locked synthetic source identity changed')
            require(parse_qs(parsed.query).get('subdirectory') == ['modules/' + kind], 'Wrong locked subdirectory')
            package = site / name.replace('-', '_')
            source_package = repo / 'modules' / kind / package.name
            for filename in ('__init__.py', '_proof.py', '_payload.py'):
                require((package / filename).read_bytes() == (source_package / filename).read_bytes(), 'Installed bytes differ')
            built = ast.parse((package / '_built.py').read_text())
            require(ast.literal_eval(built.body[0].value) == revision, 'Built wheel used another commit')
            installed.append({'name': name, 'prefix': str(project / '.venv'),
                              'payloadSha256': sha(package / '_payload.py'), 'gitCommit': revision,
                              'builtSha256': sha(package / '_built.py')})
        require(sha(fixture_path) == FIXTURE_SHA256 and sha(helper_path) == HELPER_SHA256,
                'Bound original sources changed during experiment')
        report.update(status='checks-complete', phase='complete', installed=installed,
                      lockSha256=sha(project / 'uv.lock'), actualSyntheticWheelInstall=True)
    except BaseException as error:
        report.update(status='blocked-readiness' if report['phase'] == 'readiness' else 'failed',
                      failure={'errorType': type(error).__name__, 'message': str(error)})
    finally:
        # No canonical modules/workers were started. Remove fixture-owned venv
        # links only after every launched command group was confirmed absent.
        # Never let cleanup replace the primary failure or turn unknown into pass.
        settled = all(row['cleanup'] in ('not-started', 'confirmed-absent') for row in report['commands'])
        report['processCleanup'] = {'status': 'confirmed-absent' if settled else 'unknown',
                                    'allOwnedGroupsAbsent': settled, 'managedGuestStopRequired': not settled}
        removed = []
        if not settled:
            report['status'] = 'failed'
            report.setdefault('failure', {'errorType': 'RuntimeError',
                                         'message': 'Process cleanup unknown; managed guest stop required'})
            report['fixtureSymlinkCleanupStatus'] = 'skipped-unsettled-process-group'
        else:
            try:
                if root_created:
                    require(not root.is_symlink(), 'Owned root became a symlink; cleanup refused')
                    for path in sorted(root.rglob('*', recurse_symlinks=False)):
                        if path.is_symlink():
                            item = {'path': str(path.relative_to(root)), 'target': os.readlink(path)}
                            path.unlink()
                            removed.append(item)
                report['fixtureSymlinkCleanupStatus'] = 'completed' if root_created else 'root-not-created'
            except BaseException as error:
                report['status'] = 'failed'
                report['fixtureSymlinkCleanupStatus'] = 'failed'
                report['cleanupFailure'] = {'errorType': type(error).__name__, 'message': str(error)}
                report.setdefault('failure', report['cleanupFailure'])
        report['fixtureSymlinkCleanup'] = removed
        if report['status'] == 'checks-complete' and not report.get('evidenceWriteFailure'):
            report['status'] = 'passed'
        save()
        signal.signal(signal.SIGTERM, previous_term)
    print(json.dumps({'status': report['status'], 'phase': report['phase'],
                      'failure': report.get('failure'), 'evidenceWriteFailure': report.get('evidenceWriteFailure')}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
