"""Supporting offline expansion of the canonical lock, not adoption.

Source authoring is not execution evidence. The caller owns the admitted guest,
NET/PID namespaces, nonroot setpriv/capability drop and timeout supervisor.
Launch absolute Python 3.13 with -I -S -B and env -i
PATH=<python-directory>:/usr/bin:/bin LANG=C.UTF-8. Never run on the host.

Supply the original preparation's normalized manifest, verbatim lock and
populated job-owned uv cache under root.parent, plus an absolute uv executable.
Only three dependency-free synthetic packages are installed, into a minimal
project, to warm that SAME cache. The expanded project runs only
uv lock --offline --project ... --python 3.13, without upgrades or lock edits.
No product imports, worker/probe startup, canonical install or model calls.

Export ONLY output/result.json. All command streams stay in root/private-logs.
The root, cache, manifests and both generated locks are private retained work.
No exception text, argv, paths, package names, URLs or lock rows are exported.
Timeout or uncertain process settlement is UNKNOWN, never success or replay.
"""
from __future__ import annotations

import os
import sys

# Match the public experiment before importing anything beyond startup modules.
STARTUP_PATH = os.path.dirname(sys.executable) + ':/usr/bin:/bin'
if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
        and sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.safe_path
        and 'site' not in sys.modules and os.path.isabs(sys.executable)):
    raise SystemExit('Requires absolute guest Python with -I -S -B')
if (set(os.environ) - {'PATH', 'LANG', 'LC_CTYPE'}
        or os.environ.get('PATH') != STARTUP_PATH
        or os.environ.get('LANG') != 'C.UTF-8'
        or os.environ.get('LC_CTYPE', 'C.UTF-8') != 'C.UTF-8'):
    raise SystemExit('Requires the declared clean startup environment')

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tomllib
import types
from urllib.parse import parse_qs, urlsplit

CHECKOUT = Path(__file__).resolve().parents[2]
FORMS_SHA256 = 'bdae0ba56eaee027ec09823561b2a6dea3204c2b20f861ba9f03c278ce23d5a1'
EXPERIMENT_SHA256 = '8c161f07fa37d16de145e7ef94d077ce77bb7e1a1e1175adb396674fc164f912'
CANONICAL_SHA256 = 'a533406d51eb2877b08d1be6dac163c3108d6526db4a066dc905de7837eaa179'
REPOSITORY_LOCK_SHA256 = 'd819bbe081b1c292f0914456a31962458df5fc8a04ef288b079ce60393d4db8f'
PHASES = ('readiness', 'synthetic-repository', 'synthetic-cache',
          'expanded-manifest', 'expanded-lock', 'verify-lock', 'finalization')
COMMANDS = (
    ('readiness', 'uv-version'),
    ('synthetic-repository', 'repo-init'),
    ('synthetic-repository', 'config-email'),
    ('synthetic-repository', 'config-name'),
    ('synthetic-repository', 'config-sign'),
    ('synthetic-repository', 'repo-add'),
    ('synthetic-repository', 'repo-commit'),
    ('synthetic-repository', 'repo-revision'),
    ('synthetic-cache', 'uv-sync'),
    ('expanded-lock', 'uv-lock'),
)


def require(value):
    if not value:
        raise AssertionError('Check failed')


def serialized(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


def object_hash(value):
    return hashlib.sha256(serialized(value)).hexdigest()


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_experiment():
    """Compile verified source without consulting/writing caches or calling main."""
    path = CHECKOUT / 'tests/fixtures/omitted_ref_acquisition_experiment.py'
    source = path.read_bytes()
    require(hashlib.sha256(source).hexdigest() == EXPERIMENT_SHA256)
    module = types.ModuleType('expanded_lock_acquisition_helpers')
    module.__file__ = str(path)
    exec(compile(source, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module, path


def load_forms():
    """Load only the task's pure recognizer; no product or resolver execution."""
    path = CHECKOUT / 'scripts/uv_diagnostic_forms.py'
    source = path.read_bytes()
    require(hashlib.sha256(source).hexdigest() == FORMS_SHA256)
    module = types.ModuleType('uv_diagnostic_forms')
    module.__file__ = str(path)
    exec(compile(source, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module, path, hashlib.sha256(source).hexdigest()


def observe_form(path, facts, returncode, forms, *, uv_version):
    """Add a closed-form observation without replacing the process outcome."""
    # This failure envelope must not call the parser that may itself be failing.
    supported = type(uv_version) is str and uv_version == '0.13.6'
    unknown = {'grammarVersion': 1, 'uvVersion': '0.13.6' if supported else 'unsupported',
               'parseHealth': 'incomplete' if supported else 'unsupported-version',
               'kind': 'unknown', 'form': 'unknown',
               'flags': {'resolutionProof': None, 'offlineAbsence': None, 'tomlDiagnostic': None}}
    try:
        require(type(facts) is dict and type(facts['rawLogBytes']) is int and facts['rawLogBytes'] >= 0)
        require(type(facts['rawLogSha256']) is str
                and re.fullmatch('[a-f0-9]{64}', facts['rawLogSha256']) is not None)
        require(facts['complete'] is True)
        with path.open('rb') as stream:
            payload = stream.read(65537)
        if facts['rawLogBytes'] <= 65536:
            require(len(payload) == facts['rawLogBytes']
                    and hashlib.sha256(payload).hexdigest() == facts['rawLogSha256'])
        else:
            require(len(payload) == 65537)
        parsed = forms.parse_form(payload, returncode, complete=True, uv_version=uv_version)
        require(forms.validate_form(parsed))
        return {'captureHealth': 'complete', 'form': parsed}
    except BaseException:
        return {'captureHealth': 'failed', 'form': unknown}


def durable_report(output, report, forms):
    """Commit a bounded receipt before teardown; IO failure never means success.

    The saved receipt contains only a write intent. Actual durability is returned
    after file AND directory fsync; it cannot truthfully attest to its own future
    write. Callers retain the returned health and fail on any failed write.
    A stored status alone is never acceptance: the caller must observe exit 0.
    In particular, a directory-fsync failure can follow a successful replace.
    """
    fd = None
    try:
        for row in report.get('commands', []):
            observation = row.get('diagnostic')
            if observation is not None:
                require(type(observation) is dict and set(observation) == {'captureHealth', 'form'})
                require(type(observation['captureHealth']) is str
                        and observation['captureHealth'] in ('complete', 'failed'))
                require(forms is not None and forms.validate_form(observation['form']))
                if observation['captureHealth'] == 'failed':
                    require(observation['form']['kind'] == 'unknown'
                            and observation['form']['parseHealth'] != 'recognized')
        payload = serialized({**report, 'receiptHealth': 'write-intent'}) + b'\n'
        require(len(payload) <= 65536)
        temporary = output / 'result.json.pending'
        with temporary.open('wb') as stream:
            require(stream.write(payload) == len(payload))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output / 'result.json')
        fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
        return True
    except BaseException:
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


class SupplyEvidence:
    """The original helper writes to a PRIVATE report; save copies only facts."""

    def __init__(self, public, save):
        self.report = {}
        self.public, self.persist = public, save

    def save(self):
        raw = self.report['supply']
        clean = {}
        for key in ('manifestSha256', 'setupLockSha256', 'canonicalManifestSha256',
                    'repositoryLockSha256', 'generatedManifestSha256'):
            require(isinstance(raw.get(key), str) and re.fullmatch('[a-f0-9]{64}', raw[key]))
            clean[key] = raw[key]
        for key in ('directDependencyCount', 'setupPackageCount'):
            require(type(raw.get(key)) is int and raw[key] >= 0)
            clean[key] = raw[key]
        self.public['supply'] = clean
        require(self.persist())


def acquisition_bindings(rows, synthetic_url):
    """Private exact bindings, never fuzzy package-name or transport inference."""
    bindings = {}

    def add(token, kind):
        if isinstance(token, str) and token:
            bindings.setdefault(token, set()).add(kind)

    add(synthetic_url, 'synthetic')
    for row in rows:
        source = row.get('source', {})
        if 'git' in source:
            value = source['git']
            base = urlsplit(value)._replace(query='', fragment='').geturl()
            kind = 'synthetic' if base == synthetic_url else 'canonical-Git'
            add(value, kind)
            add(base, kind)
        elif 'registry' in source:
            # The shared registry endpoint alone does NOT bind a package.
            kind = 'registry'
            for artifact in [row.get('sdist', {}), *row.get('wheels', [])]:
                add(artifact.get('url'), kind)
        else:
            continue
        add(row['name'] + '==' + row['version'], kind)
    return bindings


def acquisition_failure(path, facts, returncode, bindings):
    """One complete UTF-8 failure line, one quoted exact binding, or UNKNOWN."""
    unknown = {'class': 'unknown', 'stage': 'unknown', 'version': 1}
    if not (facts['complete'] and type(returncode) is int and returncode > 0
            and facts['rawLogBytes'] <= 65536):
        return unknown
    with path.open('rb') as stream:
        payload = stream.read(65537)
    if (len(payload) != facts['rawLogBytes']
            or hashlib.sha256(payload).hexdigest() != facts['rawLogSha256']):
        return unknown
    try:
        text = payload.decode('utf-8')
    except UnicodeDecodeError:
        return unknown
    prefix = r'^[ \t]*(?:(?:error:|Caused by:|[×╰─]+)[ \t]+)?'
    verbs = r'(fetch|clone|download|build|read metadata for|prepare metadata for|generate package metadata for)'
    # Generic "Failed to fetch" is counted as a failure line, but cannot bind.
    lines = [line for line in text.splitlines()
             if re.match(prefix + r'Failed to ' + verbs + r'\b', line, re.IGNORECASE)]
    if len(lines) != 1:
        return unknown
    match = re.fullmatch(prefix + r'Failed to ' + verbs
                         + r':? ([`\'"])([^`\'"\r\n]+)\2\.?', lines[0], re.IGNORECASE)
    if match is None:
        return unknown
    kinds = bindings.get(match[3], set())
    if len(kinds) != 1:
        return unknown
    verb = match[1].lower()
    stage = 'build' if verb == 'build' else 'metadata' if 'metadata' in verb else 'object'
    return {'class': next(iter(kinds)), 'stage': stage, 'version': 1}


def fixture_rows(fixture, lock, revision):
    """Require all three exact branch/subdirectory identities without exports."""
    selected = []
    for name, (_, kind) in fixture.PACKAGES.items():
        rows = [row for row in lock['package'] if row['name'] == name]
        require(len(rows) == 1 and rows[0]['version'] == '0.1.0'
                and not rows[0].get('dependencies') and set(rows[0]['source']) == {'git'})
        parsed = urlsplit(rows[0]['source']['git'])
        require(parsed._replace(query='', fragment='').geturl() == fixture.URL
                and parsed.fragment == revision
                and parse_qs(parsed.query, strict_parsing=True) == {
                    'branch': ['main'], 'subdirectory': ['modules/' + kind]})
        selected.append(rows[0])
    return selected


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal error includes arbitrary user-supplied paths.
        raise ValueError('Invalid arguments')


def main():
    parser = QuietParser(prog='omitted-ref-expanded-lock', add_help=False)
    for name in ('root', 'output', 'runtime-manifest', 'runtime-lock', 'uv-cache', 'uv'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--parent-netns', required=True)
    parser.add_argument('--parent-pidns', required=True)
    args = parser.parse_args()
    root, output = args.root, args.output
    paths = (root, output, args.runtime_manifest, args.runtime_lock, args.uv_cache, args.uv)
    require(all(path.is_absolute() and not path.is_symlink() and path.resolve() == path for path in paths))
    require(not root.exists() and not output.exists() and root.parent.is_dir())
    require(root != output and not root.is_relative_to(output) and not output.is_relative_to(root))
    require(not root.is_relative_to(CHECKOUT) and not output.is_relative_to(CHECKOUT))
    for path in (args.runtime_manifest, args.runtime_lock, args.uv_cache):
        require(path.is_relative_to(root.parent) and path != root.parent
                and not path.is_relative_to(root) and not path.is_relative_to(output))
    require(args.runtime_manifest.is_file() and args.runtime_lock.is_file()
            and args.uv.is_file() and os.access(args.uv, os.X_OK))
    require(args.uv_cache.is_dir() and any(args.uv_cache.iterdir())
            and not root.is_relative_to(args.uv_cache) and not output.is_relative_to(args.uv_cache))
    require(not any(path.is_relative_to(args.uv_cache)
                    for path in (args.runtime_manifest, args.runtime_lock, args.uv)))
    output.mkdir(parents=True, mode=0o700)
    report = {
        'schema': 1, 'supportingCheck': 15, 'scope': 'expanded-offline-lock-only',
        'status': 'not-run', 'phase': 'readiness', 'finalized': False,
        'phases': {phase: 'unrun' for phase in PHASES},
        'commands': [{'phase': phase, 'role': role, 'status': 'unrun',
                      'argvSha256': None, 'returncode': None, 'cleanup': 'not-started',
                      'stdout': {'complete': False}, 'stderr': {'complete': False}}
                     for phase, role in COMMANDS],
        'workerStarts': 0, 'probeStarts': 0, 'modelCalls': 0, 'canonicalInstalls': 0,
        'cleanup': {'processes': 'unrun', 'fixtureSymlinks': 'unrun',
                    'work': 'retained', 'cache': 'retained'},
        'immutableInputs': 'unrun',
    }
    immutable = {}
    experiment = fixture = forms = None
    root_created = False
    deadline = False
    capture_failed = False
    previous_term = signal.getsignal(signal.SIGTERM)

    def terminate(signum, frame):
        nonlocal deadline
        deadline = True
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise TimeoutError('Supervisor deadline')

    def save():
        nonlocal capture_failed
        if not durable_report(output, report, forms):
            capture_failed = True
            report['receiptHealth'] = 'failed'
            return False
        report['receiptHealth'] = 'durable'
        return True

    def phase(name):
        current = report['phase']
        if report['phases'][current] == 'running':
            report['phases'][current] = 'passed'
        report['phase'] = name
        report['phases'][name] = 'running'
        require(save())

    def bind(role, path, expected=None):
        digest = file_hash(path)
        require(expected is None or digest == expected)
        immutable[role] = (path, digest)
        report.setdefault('inputs', {})[role] = {'sha256': digest, 'bytes': path.stat().st_size}

    def command(role, argv, cwd, bindings=None):
        row = next(row for row in report['commands'] if row['role'] == role)
        require(row['status'] == 'unrun' and row['phase'] == report['phase'])
        row.update(status='starting', argvSha256=object_hash(argv))
        require(save())  # Record intent before launch. No retry in this root.
        stdout, stderr = (root / 'private-logs' / (role + '.' + suffix)
                          for suffix in ('stdout', 'stderr'))
        process, attempted, absent, normal = None, False, False, False
        failed, uncertain = False, False
        try:
            with stdout.open('xb') as out, stderr.open('xb') as err:
                row.update(status='running', cleanup='unknown')
                require(save())
                attempted = True
                process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                           stdout=out, stderr=err, start_new_session=True, close_fds=True)
                process.wait(timeout=90)
                absent = experiment.wait_group_absent(process, 2)
                require(absent)
                normal = True
        except BaseException as error:
            failed = True
            uncertain = isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) or attempted
        finally:
            if process is not None:
                if not absent:
                    # Cleanup helper diagnostics stay private, including error types.
                    absent = experiment.stop_owned_group(process, {})
                row['returncode'] = process.returncode
            elif not attempted:
                absent = True
            # Interrupted Popen may have spawned without returning a handle.
            row['cleanup'] = 'not-started' if not attempted else 'confirmed-absent' if absent else 'unknown'
            row['status'] = 'unknown' if uncertain or not absent or deadline else 'failed' if failed else 'completed'
            for channel, path in (('stdout', stdout), ('stderr', stderr)):
                if not absent:
                    continue  # No final hash while descendants might still write.
                try:
                    with path.open('rb') as stream:
                        facts = fixture.diagnostics.stream_facts(
                            iter(lambda: stream.read(65536), b''), row['returncode'],
                            complete=normal and not deadline)
                    row[channel] = {key: facts[key] for key in (
                        'complete', 'rawLogBytes', 'rawLogSha256', 'classifierVersion')}
                    if channel == 'stderr':
                        row['diagnostic'] = observe_form(
                            path, facts, row['returncode'], forms,
                            uv_version=report.get('versions', {}).get('uv', 'unsupported'))
                        if row['diagnostic']['captureHealth'] != 'complete':
                            failed = True
                            row['capture'] = 'failed'
                    if channel == 'stderr' and bindings is not None and row['returncode'] != 0:
                        row['acquisition'] = acquisition_failure(path, facts, row['returncode'], bindings)
                except BaseException:
                    failed = True
                    row[channel] = {'complete': False}
                    row['capture'] = 'failed'
            if deadline:
                row['status'] = 'unknown'
            elif (failed or row['returncode'] != 0) and row['status'] != 'unknown':
                row['status'] = 'failed'
            if not save():
                failed = True
        if row['status'] == 'unknown' or deadline:
            raise TimeoutError('Command outcome unknown')
        require(not failed and row['returncode'] == 0)
        return stdout

    signal.signal(signal.SIGTERM, terminate)
    try:
        phase('readiness')
        require(sys.platform == 'linux' and sys.version_info[:2] == (3, 13))
        identity = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                        if ':' in line)
        require(os.getuid() != 0 and os.geteuid() == os.getuid() and os.getgid() != 0
                and os.getegid() == os.getgid() and 0 not in os.getgroups())
        require(identity['NoNewPrivs'].strip() == '1'
                and all(int(identity[key].strip(), 16) == 0
                        for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')))
        experiment, experiment_path = load_experiment()
        forms, forms_path, forms_hash = load_forms()
        bind('diagnostic-forms', forms_path, forms_hash)
        grammar = forms.parse_form(b'', 1, uv_version='0.13.6')
        require(forms.validate_form(grammar) and grammar['grammarVersion'] == 1
                and grammar['uvVersion'] == '0.13.6' and grammar['kind'] == 'unknown')
        report['observer'] = {'grammarVersion': grammar['grammarVersion'],
                              'sourceSha256': forms_hash, 'verifiedBeforeCommands': True}
        fixture_path = CHECKOUT / 'tests/fixtures/omitted_ref_worker_probe.py'
        helper_path = CHECKOUT / 'scripts/qualification_diagnostics.py'
        bind('experiment', experiment_path, EXPERIMENT_SHA256)
        bind('fixture', fixture_path, experiment.FIXTURE_SHA256)
        bind('diagnostics', helper_path, experiment.HELPER_SHA256)
        bind('canonical-manifest', CHECKOUT / 'amplifier_web/runtime_deps/pyproject.toml', CANONICAL_SHA256)
        bind('repository-lock', CHECKOUT / 'uv.lock', REPOSITORY_LOCK_SHA256)
        bind('supplied-manifest', args.runtime_manifest)
        bind('supplied-lock', args.runtime_lock)
        bind('expanded-lock-fixture', Path(__file__).resolve())
        bind('uv', args.uv)
        fixture = experiment.load_fixture(fixture_path, helper_path)
        fixture.require_network_sandbox(args.parent_netns)
        require(re.fullmatch(r'pid:\[\d+\]', args.parent_pidns) is not None
                and os.readlink('/proc/self/ns/pid') != args.parent_pidns)
        report['sandbox'] = 'separate-net-pid-nonroot-capzero-nnp'
        git = shutil.which('git', path=STARTUP_PATH)
        require(git and Path(git).is_absolute())
        root.mkdir(mode=0o700)
        root_created = True
        (root / 'private-logs').mkdir(mode=0o700)
        fixture.isolated_environment(root, git, args.uv_cache)
        require(os.environ.get('UV_CACHE_DIR') == str(args.uv_cache)
                and 'UV_OFFLINE' not in os.environ and os.environ.get('GIT_ALLOW_PROTOCOL') == 'file')
        uv = str(args.uv)
        version_log = command('uv-version', [uv, '--version'], root)
        require(version_log.stat().st_size <= 512)
        require(version_log.read_text(encoding='utf-8').split()[:2] == ['uv', '0.13.6'])
        report['versions'] = {'uv': '0.13.6', 'python': '.'.join(map(str, sys.version_info[:3]))}
        report['status'] = 'running'

        phase('synthetic-repository')
        repo = root / 'origin'
        command('repo-init', ['git', 'init', '-b', 'main'], repo)
        for role, key, value in (('config-email', 'user.email', 'fixture@example.invalid'),
                                 ('config-name', 'user.name', 'Fixture'),
                                 ('config-sign', 'commit.gpgsign', 'false')):
            command(role, ['git', 'config', key, value], repo)
        # Exactly the public experiment's 15 package files; never make_repository.
        inputs = []
        for name, (_, kind) in fixture.PACKAGES.items():
            directory = repo / 'modules' / kind
            package = directory / name.replace('-', '_')
            package.mkdir(parents=True)
            (directory / 'pyproject.toml').write_text(fixture.manifest_text({
                'project': {'name': name, 'version': '0.1.0'},
                'build-system': {'requires': [], 'build-backend': 'backend', 'backend-path': ['.']}}))
            (directory / 'backend.py').write_text(fixture.BACKEND)
            (package / '__init__.py').write_text(
                {'context': fixture.CONTEXT, 'provider': fixture.PROVIDER, 'hooks': fixture.HOOK}[kind])
            (package / '_proof.py').write_text(fixture.PROOF_MODULE)
            (package / '_payload.py').write_text('PAYLOAD = 0\n')
            for path in sorted(directory.rglob('*')):
                if path.is_file():
                    inputs.append({'path': str(path.relative_to(repo)), 'sha256': file_hash(path)})
        require(len(inputs) == 15)
        report['syntheticInputs'] = {'count': len(inputs), 'sha256': object_hash(inputs)}
        command('repo-add', ['git', 'add', '.'], repo)
        command('repo-commit', ['git', 'commit', '-m', 'baseline'], repo)
        revision_log = command('repo-revision', ['git', 'rev-parse', 'HEAD'], repo)
        require(revision_log.stat().st_size <= 64)
        revision = revision_log.read_text(encoding='ascii').strip()
        require(re.fullmatch('[a-f0-9]{40}', revision))
        report['syntheticCommit'] = revision

        phase('synthetic-cache')
        project = root / 'synthetic-project'
        project.mkdir()
        data = {'project': {'name': 'omitted-acquisition-only', 'version': '0.0.0',
                            'requires-python': '>=3.13,<3.14', 'dependencies': list(fixture.PACKAGES)},
                'tool': {'uv': {'package': False, 'environments': ["sys_platform == 'linux'"],
                               'sources': {name: {'git': fixture.URL, 'branch': 'main',
                                                  'subdirectory': 'modules/' + kind}
                                           for name, (_, kind) in fixture.PACKAGES.items()}}}}
        (project / 'pyproject.toml').write_text(fixture.manifest_text(data))
        synthetic_bindings = {fixture.URL: {'synthetic'},
                              **{name + '==0.1.0': {'synthetic'} for name in fixture.PACKAGES}}
        command('uv-sync', [uv, 'sync', '--project', str(project), '--python', '3.13'],
                root, synthetic_bindings)
        warm_lock = tomllib.loads((project / 'uv.lock').read_text())
        require(len(warm_lock['package']) == 4 and {row['name'] for row in warm_lock['package']}
                == {'omitted-acquisition-only', *fixture.PACKAGES})
        warm_rows = fixture_rows(fixture, warm_lock, revision)
        site = project / '.venv/lib/python3.13/site-packages'
        require({path.name for path in site.glob('*.dist-info')}
                == {name.replace('-', '_') + '-0.1.0.dist-info' for name in fixture.PACKAGES})
        for name, (_, kind) in fixture.PACKAGES.items():
            package = site / name.replace('-', '_')
            source = repo / 'modules' / kind / package.name
            for filename in ('__init__.py', '_proof.py', '_payload.py'):
                require((package / filename).read_bytes() == (source / filename).read_bytes())
            built = ast.parse((package / '_built.py').read_text())
            require(len(built.body) == 1 and isinstance(built.body[0], ast.Assign)
                    and ast.literal_eval(built.body[0].value) == revision)
        report['syntheticInstalledCount'] = 3

        phase('expanded-manifest')
        expanded = root / 'expanded-project'
        expanded.mkdir()
        evidence = SupplyEvidence(report, save)
        _, original_graph, project_name = fixture.prepare_manifest(
            args, expanded / 'pyproject.toml', evidence)
        original_serialized = serialized(original_graph)
        report['canonicalRows'] = {'count': len(original_graph),
                                   'sha256': hashlib.sha256(original_serialized).hexdigest()}
        shutil.copyfile(args.runtime_lock, expanded / 'uv.lock')
        require(file_hash(expanded / 'uv.lock') == immutable['supplied-lock'][1])
        manifest_hash = file_hash(expanded / 'pyproject.toml')
        report['startingLockSha256'] = file_hash(expanded / 'uv.lock')
        bindings = acquisition_bindings(original_graph + warm_rows, fixture.URL)

        phase('expanded-lock')
        # No sync, refresh, upgrades, frozen/locked mode, ref changes or lock splice.
        command('uv-lock', [uv, 'lock', '--offline', '--project', str(expanded), '--python', '3.13'],
                root, bindings)

        phase('verify-lock')
        require(file_hash(expanded / 'pyproject.toml') == manifest_hash and not (expanded / '.venv').exists())
        lock = tomllib.loads((expanded / 'uv.lock').read_text())
        require(serialized(fixture.lock_identity(lock, project_name)) == original_serialized)
        rows = fixture_rows(fixture, lock, revision)
        roots = [row for row in lock['package'] if row['name'] == project_name]
        require(len(roots) == 1 and roots[0]['source'] == {'virtual': '.'}
                and len(lock['package']) == len(original_graph) + 4)
        require(serialized(rows) == serialized(warm_rows))
        report['fixtureLockRows'] = [
            {'role': kind, 'sha256': object_hash(row)}
            for (_, kind), row in zip(fixture.PACKAGES.values(), rows)]
        report['expandedLock'] = {'sha256': file_hash(expanded / 'uv.lock'),
                                  'packageCount': len(lock['package']), 'canonicalRowsPreserved': True}
        report['status'] = 'checks-complete'
    except BaseException as error:
        unknown = deadline or isinstance(error, (TimeoutError, subprocess.TimeoutExpired))
        report['status'] = 'unknown' if unknown else 'failed'
        report['phases'][report['phase']] = 'unknown' if unknown else 'failed'
        report['failure'] = {'phase': report['phase'], 'class': 'deadline-or-uncertain' if unknown else 'check-failed'}
    finally:
        # Keep the primary failure phase; finalization has its own explicit state.
        if report['phases'][report['phase']] == 'running':
            report['phases'][report['phase']] = 'passed'
        report['phases']['finalization'] = 'running'
        settled = all(row['cleanup'] in ('not-started', 'confirmed-absent') for row in report['commands'])
        report['cleanup']['processes'] = 'confirmed-absent' if settled else 'unknown'
        report['cleanup']['guestStopRequired'] = not settled
        try:
            if not settled:
                report['status'] = 'unknown'
                report['immutableInputs'] = 'unknown'
                report['cleanup']['fixtureSymlinks'] = 'unrun-unsettled'
            else:
                report['immutableInputs'] = 'incomplete'
                require(len(immutable) == 10)
                checks = [not path.is_symlink() and file_hash(path) == digest
                          for path, digest in immutable.values()]
                unchanged = all(checks)
                report['immutableInputs'] = 'unchanged' if unchanged else 'changed'
                require(unchanged)
                removed = 0
                if root_created:
                    require(not root.is_symlink())
                    for path in root.rglob('*', recurse_symlinks=False):
                        if path.is_symlink():
                            path.unlink()
                            removed += 1
                report['cleanup'].update(
                    fixtureSymlinks='completed' if root_created else 'root-not-created',
                    removedSymlinkCount=removed)
            report['phases']['finalization'] = 'passed' if settled else 'unknown'
        except BaseException:
            report['phases']['finalization'] = 'unknown' if deadline else 'failed'
            if report['status'] != 'unknown':
                report['status'] = 'failed'
            report['finalizationFailure'] = 'check-failed'
        if capture_failed:
            report['status'] = 'failed' if report['status'] != 'unknown' else 'unknown'
            report['evidenceWriteFailure'] = True
        if deadline:
            report['status'] = 'unknown'
        if report['status'] == 'checks-complete':
            report['status'] = 'passed'
        report['finalized'] = True
        if not save():
            report['status'] = 'unknown' if deadline or report['status'] == 'unknown' else 'failed'
        signal.signal(signal.SIGTERM, previous_term)
    print(json.dumps({'status': report['status'], 'phase': report['phase'],
                      'receiptHealth': report.get('receiptHealth', 'failed')}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    try:
        result = main()
    except BaseException:
        # Includes parse/readiness/write failures before a safe output exists.
        # Never print a traceback or exception supplied by a dependency.
        print('{"status":"failed","phase":"readiness","class":"check-failed"}')
        result = 1
    raise SystemExit(result)