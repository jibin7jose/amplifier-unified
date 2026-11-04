"""Issue #1279: real omitted-ref qualification, promotion and worker adoption.

Authoring this fixture is not qualification. Run once in an isolated Linux/Python
3.13 CI job. First use --prepare-supply in the existing online setup; then supply
--prepared-supply, --runtime-manifest, its exact --runtime-lock, and the dedicated
populated --uv-cache to the isolated run, all under --root's parent.
Keep canonical Git/registry source identities, including wheel-only registry
Core; only loop-live's repository-lock pin and Linux/Python bounds may differ.

Only runtime_environment.manifest_path is redirected. UpdateManager.install,
validate, update_probe.py, Foundation preparation, freeze, activate and
RuntimeManager.start/_command are production implementations. Subprocess
observers return the original bytes and process objects. The Git wrapper changes
transport configuration only for `remote get-url origin`, never its output.

The provider and lifecycle hook are synthetic. The context delegates to the real
installed context-simple. No model input is sent. Every generation installs its
own .venv; there is no --system-site-packages, interpreter substitution, success
probe stub, manual validation marker or active-pointer write.

--root and --output must be fresh, separate directories. Export only --output,
never --root or the dependency supply/cache. Raw pipes are hashed and counted,
not exported; probe records use the production sanitizer, and runtime.ready is
reduced to fixed identity/capability fields. Failures contain no exception text,
source lines or arbitrary diagnostic payloads.

Except in --prepare-supply mode, the caller must launch this process in its OS
network sandbox (unshare --net).
The fixture checks a separate Linux network namespace with only loopback before
clearing UV_OFFLINE: uv otherwise refuses the uncached synthetic Git source even
though Git redirects it to a local fixture. GIT_ALLOW_PROTOCOL=file stays set;
canonical Git sources use exact-lock local mirrors and registry dependencies use
the declared cache. No network retry,
fallback supply, host-site-packages inheritance or source-policy bypass is used.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib
from urllib.parse import parse_qs, urlsplit
import uuid

CHECKOUT = Path(__file__).resolve().parents[2]
_diagnostics_spec = importlib.util.spec_from_file_location(
    'qualification_diagnostics', CHECKOUT / 'scripts/qualification_diagnostics.py')
diagnostics = importlib.util.module_from_spec(_diagnostics_spec)
_diagnostics_spec.loader.exec_module(diagnostics)
URL = "https://example.invalid/approval"
URI = "git+" + URL  # Intentionally no @ref; the production resolver chooses main.
PROFILE = "omitted-ref-proof"
PACKAGES = {
    "amplifier-module-context-omittedfixture": ("context-omittedfixture", "context"),
    "amplifier-module-provider-omittedfixture": ("provider-omittedfixture", "provider"),
    "amplifier-module-hooks-omittedfixture": ("hooks-omittedfixture", "hooks"),
}
RECEIPTS = (
    "validated.json", "profiles.json", "profiles-qualified.json", "plan.json",
    "runtime.toml", "runtime-base.toml", "runtime.lock", "runtime-project.json",
    "runtime-installed.json", "runtime-sources.json", "runtime-install-policy.json",
    "runtime-install-overrides.txt", "selected-source-declarations.json",
)

# PEP 517 backend: stdlib only, complete RECORD, deterministic wheel payloads.
# build_editable deliberately produces a regular wheel, as in checked-cache tests;
# PEP 610 still identifies the actual source supplied to the installer.
BACKEND = r'''from pathlib import Path
import base64, csv, hashlib, io, subprocess, tomllib, zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    info = tomllib.loads(Path('pyproject.toml').read_text())['project']
    name, version = info['name'].replace('-', '_'), info['version']
    dist = name + '-' + version
    filename = dist + '-py3-none-any.whl'
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    with zipfile.ZipFile(Path(wheel_directory) / filename, 'w') as wheel:
        for path in sorted(Path(name).rglob('*.py')):
            wheel.writestr(path.as_posix(), path.read_bytes())
        wheel.writestr(name + '/_built.py', 'GIT_REVISION = ' + repr(revision) + '\n')
        wheel.writestr(dist + '.dist-info/METADATA',
            'Metadata-Version: 2.3\nName: ' + info['name'] + '\nVersion: ' + version + '\n')
        wheel.writestr(dist + '.dist-info/WHEEL',
            'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        module = info['name'].removeprefix('amplifier-module-')
        wheel.writestr(dist + '.dist-info/entry_points.txt',
            '[amplifier.modules]\n' + module + ' = ' + name + ':mount\n')
        record = io.StringIO(newline='')
        writer = csv.writer(record, lineterminator='\n')
        for member in wheel.namelist():
            payload = wheel.read(member)
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b'=').decode()
            writer.writerow((member, 'sha256=' + digest, len(payload)))
        writer.writerow((dist + '.dist-info/RECORD', '', ''))
        wheel.writestr(dist + '.dist-info/RECORD', record.getvalue())
    return filename
build_editable = build_wheel
'''

PROOF_MODULE = r'''import hashlib, json, os, subprocess, sys
from pathlib import Path
from ._payload import PAYLOAD

def mark(event, origin):
    path = Path(origin).resolve()
    source_root = next((p for p in path.parents if (p / '.git').exists()), None)
    if source_root is not None:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source_root, text=True).strip()
    else:
        from ._built import GIT_REVISION
        revision = GIT_REVISION
    home = Path(os.environ['AMPLIFIER_WEB_HOME'])
    generation = os.environ.get('AMPLIFIER_UNIFIED_RELEASE') or (
        home.name if home.parent.name == 'releases' else None)
    row = {'event': event, 'module': __package__, 'payload': PAYLOAD,
        'revision': revision, 'payloadSha256': hashlib.sha256(path.with_name('_payload.py').read_bytes()).hexdigest(),
        'pid': os.getpid(), 'prefix': sys.prefix, 'executable': sys.executable,
        'processStartTicks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
        'origin': str(path), 'generation': generation,
        'kind': 'probe' if Path(sys.argv[0]).name == 'update_probe.py' else 'worker'}
    fd = os.open(os.environ['OMITTED_REF_MOUNTS'], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        data = (json.dumps(row, sort_keys=True) + '\n').encode()
        if os.write(fd, data) != len(data):
            raise RuntimeError('Short fixture evidence write')
    finally:
        os.close(fd)
'''

CONTEXT = '''import inspect
from amplifier_module_context_simple import mount as simple_mount
from ._proof import mark
__amplifier_module_type__ = 'context'
async def mount(coordinator, config=None):
    cleanup = await simple_mount(coordinator, config)
    mark('mount', __file__)
    async def finish():
        if cleanup is not None:
            result = cleanup()
            if inspect.isawaitable(result):
                await result
        mark('cleanup', __file__)
    return finish
'''
PROVIDER = '''import os
from pathlib import Path
from amplifier_core import ProviderInfo
from ._proof import mark
__amplifier_module_type__ = 'provider'
class Provider:
    name = 'omittedfixture'
    def get_info(self):
        return ProviderInfo(id=self.name, display_name='No-call fixture', defaults={'model': 'fixture'})
    async def list_models(self): return []
    async def complete(self, request, **kwargs):
        Path(os.environ['OMITTED_REF_MODEL_CALLS']).touch()
        raise AssertionError('Qualification/adoption must not call a model')
    def parse_tool_calls(self, response): return []
async def mount(coordinator, config=None):
    await coordinator.mount('providers', Provider(), name='omittedfixture')
    mark('mount', __file__)
    async def finish(): mark('cleanup', __file__)
    return finish
'''
HOOK = '''from ._proof import mark
__amplifier_module_type__ = 'hook'
async def mount(coordinator, config=None):
    mark('mount', __file__)
    async def finish(): mark('cleanup', __file__)
    return finish
'''


def require(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def toml_value(value):
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + '=' + toml_value(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    raise ValueError('Unsupported manifest value')


def manifest_text(data):
    return '\n'.join(json.dumps(k) + '=' + toml_value(v) for k, v in data.items()) + '\n'


def requirement_name(value):
    match = re.match(r'([A-Za-z0-9][A-Za-z0-9_.-]*)', value)
    require(match is not None, 'Invalid dependency name')
    return re.sub(r'[-_.]+', '-', match[1]).lower()


class Evidence:
    """Fixed-schema evidence; raw subprocess text never reaches output files."""
    def __init__(self, output, replacements):
        self.output, self.replacements = output, sorted(replacements, key=lambda p: len(p[0]), reverse=True)
        self.stream_hashes = {}
        self.capture_failed = False
        self.report = {'schema': 1, 'status': 'running', 'phase': 'prerequisites',
                       'cycles': [], 'negativeControls': [], 'processes': [], 'cleanup': {'status': 'not-started'}}
        self.save()

    def text(self, text):
        for original, replacement in self.replacements:
            text = text.replace(original, replacement)
        return text

    def save(self):
        text = self.text(json.dumps(self.report, indent=2, sort_keys=True, default=str))
        require(len(text.encode()) <= 512 * 1024, 'Evidence report exceeded its fixed budget')
        (self.output / 'result.json').write_text(text + '\n')

    def save_observation(self):
        """Diagnostic IO cannot replace a command outcome or certify success."""
        if self.capture_failed:
            self.report['status'] = 'failed'
        try:
            self.save()
            return True
        except Exception:
            self.capture_failed = True
            self.report.update(status='failed', captureError='receipt-write-failed')
            return False

    def phase(self, name):
        self.report['phase'] = name
        self.save()

    def log(self, row, channel, payload):
        value = self.stream_hashes.setdefault((row['id'], channel), hashlib.sha256())
        value.update(payload)
        row[channel + 'ObservedBytes'] = row.get(channel + 'ObservedBytes', 0) + len(payload)
        row[channel + 'ObservedChunks'] = row.get(channel + 'ObservedChunks', 0) + 1
        row[channel + 'ObservedSha256'] = value.hexdigest()


def safe_failure(error, phase):
    """Read frame identities only; do not load traceback source lines or locals."""
    name = type(error).__name__
    safe = {'errorType': name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', name) else 'Exception',
            'phase': phase, 'frames': []}
    trace = error.__traceback__
    while trace:
        frame = trace.tb_frame
        module, function = frame.f_globals.get('__name__', ''), frame.f_code.co_name
        if Path(frame.f_code.co_filename) == Path(__file__):
            module = 'omitted_ref_worker_probe'
        if (isinstance(module, str) and re.fullmatch(
                r'(?:omitted_ref_worker_probe|amplifier_web|amplifier_foundation|amplifier_core)'
                r'(?:\.[A-Za-z_][A-Za-z0-9_]*)*', module)
                and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', function)):
            safe['frames'].append({'module': module, 'function': function, 'line': trace.tb_lineno})
        trace = trace.tb_next
    safe['frames'] = safe['frames'][-12:]
    return safe


def ready_record(value):
    """Retain only the ready pipe's identity and fixed boolean capabilities."""
    if not isinstance(value, dict) or value.get('type') != 'runtime.ready':
        return None
    identity = value.get('workerIdentity')
    if not isinstance(identity, dict):
        return None
    sid, pid = identity.get('sessionId'), identity.get('pid')
    if not (isinstance(sid, str) and re.fullmatch(r'[a-f0-9-]{32,36}', sid)
            and type(pid) is int and pid > 0):
        return None
    report = value.get('report')
    report = report if isinstance(report, dict) else {}
    return {'type': 'runtime.ready', 'workerIdentity': {'sessionId': sid, 'pid': pid},
            'report': {key: report[key] for key in ('standalone', 'cliAbsent')
                       if type(report.get(key)) is bool}}


def phase_record(value):
    """Narrow already-sanitized production diagnostics to bounded phase facts."""
    from amplifier_web.update_diagnostics import ERROR_TYPES, PROBE_PREFIX, probe_record
    value = value if isinstance(value, dict) else {}
    result = {}
    for key in ('phase', 'status'):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r'[a-z][a-z-]{0,79}', item):
            result[key] = item
    if value.get('errorType') in ERROR_TYPES:
        result['errorType'] = value['errorType']
    for key in ('durationMs', 'exitCode', 'stdoutBytes', 'stderrBytes'):
        if type(value.get(key)) is int:
            result[key] = value[key]
    if isinstance(value.get('probe'), dict):
        probe = probe_record(PROBE_PREFIX + json.dumps(value['probe']))
        if probe is not None:
            result['probe'] = probe
    return result


class ProcessObservations:
    """Tap real pipes without changing commands, environment, bytes or outcomes."""
    def __init__(self, evidence):
        self.evidence = evidence
        self.spawn = asyncio.create_subprocess_exec
        self.owned = []
        self.baseline_command = None

    def capture_stderr(self, row, payload, *, complete):
        """Only the declared baseline command gets inner failure classification."""
        if row.get('commandRole') != 'baseline-install' or row['returncode'] == 0:
            return
        try:
            if not complete:
                facts = diagnostics.capture_error_facts(row['returncode'], 'stderr-incomplete')
            else:
                facts = diagnostics.stream_facts((payload,), row['returncode'])
                require(not row.get('stderrCaptureError')
                        and facts['rawLogSha256'] == row['stderrObservedSha256']
                        and facts['rawLogBytes'] == row['stderrObservedBytes'],
                        'Diagnostic observation identity mismatch')
        except Exception:
            self.evidence.capture_failed = True
            facts = diagnostics.capture_error_facts(row['returncode'], 'stderr-capture-failed')
        row['stderrFailure'] = {'phase': 'baseline-install', 'role': 'baseline-install',
                                'channel': 'stderr', **facts}

    async def __call__(self, *args, **kwargs):
        require(len(self.owned) < 256, 'Subprocess evidence budget exhausted; no process launched')
        argv = list(map(str, args))
        kind = ('probe' if any(a.endswith('/update_probe.py') for a in argv) else
                'worker' if any(a.endswith('/runtime_worker.py') for a in argv) else 'command')
        # Bind intent before awaiting spawn. Neither a later phase nor an ordinal
        # process ID may cause another command's bytes to be classified.
        baseline = (self.evidence.report.get('phase') == 'baseline-install'
                    and self.baseline_command is not None
                    and tuple(argv) == self.baseline_command)
        process = await self.spawn(*args, **kwargs)
        row = {'id': f'process-{len(self.owned):03d}', 'pid': process.pid, 'kind': kind,
               'argvSha256': hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
               'returncode': None, 'probeReports': [], 'ready': [],
               'stdoutComplete': False, 'stderrComplete': False}
        for channel in ('stdout', 'stderr'):
            row.update({channel + 'ObservedBytes': 0, channel + 'ObservedChunks': 0,
                        channel + 'ObservedSha256': hashlib.sha256(b'').hexdigest()})
        if kind == 'probe':
            row['probeRole'] = ('collect' if '--source-policy-only' in argv else
                                'prepare' if '--checked-sources' in argv else 'compatibility')
        if baseline:
            row['commandRole'] = 'baseline-install'
        self.owned.append((process, row))
        self.evidence.report['processes'].append(row)
        communicate = process.communicate
        async def observed_communicate(*a, **kw):
            # On cancellation/timeout, communicate may not return consumed
            # bytes. Keep *Complete false; never label a partial hash complete.
            try:
                result = await communicate(*a, **kw)
            except BaseException:
                row['returncode'] = process.returncode
                self.capture_stderr(row, b'', complete=False)
                self.evidence.save_observation()
                raise
            for channel, value in zip(('stdout', 'stderr'), result):
                try:
                    if value:
                        self.evidence.log(row, channel, value)
                        if channel == 'stdout' and kind == 'probe':
                            from amplifier_web.update_diagnostics import probe_record
                            facts = probe_record(value)
                            if facts is not None:
                                row['probeReports'].append(facts)
                    row[channel + 'Complete'] = True
                except Exception:
                    self.evidence.capture_failed = True
                    row[channel + 'CaptureError'] = 'stream-capture-failed'
                    row[channel + 'Complete'] = False
            row['returncode'] = process.returncode
            self.capture_stderr(row, result[1] or b'', complete=True)
            self.evidence.save_observation()
            return result
        process.communicate = observed_communicate
        def tap_reader(reader, channel):
            readline = reader.readline
            async def observed_readline():
                value = await readline()
                try:
                    if value:
                        self.evidence.log(row, channel, value)
                        if channel == 'stdout':
                            try:
                                item = json.loads(value)
                            except (ValueError, UnicodeError):
                                item = None
                            safe = ready_record(item)
                            if safe is not None and len(row['ready']) < 4:
                                row['ready'].append(safe)
                    else:
                        row[channel + 'Complete'] = not row.get(channel + 'CaptureError', False)
                except Exception:
                    self.evidence.capture_failed = True
                    row[channel + 'CaptureError'] = 'stream-capture-failed'
                    row[channel + 'Complete'] = False
                return value
            reader.readline = observed_readline
        if kind == 'worker':
            for channel in ('stdout', 'stderr'):
                reader = getattr(process, channel)
                if reader is not None:
                    tap_reader(reader, channel)
        return process

    def settle(self):
        for process, row in self.owned:
            row['returncode'] = process.returncode
        return all(process.returncode is not None for process, _ in self.owned)


def fresh_directory(path):
    require(not path.is_symlink(), 'Refusing a symlink output/root')
    if path.exists():
        require(path.is_dir() and not any(path.iterdir()), 'Root/output must be fresh; never retry in a used directory')
    else:
        path.mkdir(parents=True, mode=0o700)
    return path.resolve()


def plain_path(path, *, tree=False):
    """Reject symlinks before resolving inputs, including their ancestors."""
    path = Path(path).absolute()
    require(not any(item.is_symlink() for item in (path, *path.parents)),
            'Supply paths must not contain symlinks')
    path = path.resolve(strict=True)
    require(path.is_file() or path.is_dir(), 'Supply input must be a regular file or directory')
    if tree:
        require(path.is_dir(), 'Supply tree must be a directory')
        for item in path.rglob('*'):
            require(not item.is_symlink() and (item.is_file() or item.is_dir()),
                    'Supply tree contains a symlink or special file')
    return path


def require_network_sandbox(parent_netns):
    """Bounded read-only prerequisite; the caller owns network isolation."""
    require(re.fullmatch(r'net:\[\d+\]', parent_netns) is not None, 'Missing caller network identity')
    require(os.readlink('/proc/self/ns/net') != parent_netns,
            'Launch under a separate Linux network namespace')
    devices = {line.split(':', 1)[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()
               if ':' in line}
    require(devices <= {'lo'}, 'Network namespace contains non-loopback interfaces')
    return {'separateNamespace': True, 'onlyLoopbackInterfaces': True,
            'owner': 'caller', 'uvOffline': False, 'gitProtocols': ['file']}


def isolated_environment(root, git_binary, uv_cache):
    """No inherited app settings, credentials, Python path or uv/Git overrides."""
    path = os.environ.get('PATH', os.defpath)
    os.environ.clear()
    os.environ.update({
        'PATH': path, 'HOME': str(root / 'home'), 'LANG': 'C.UTF-8',
        'TMPDIR': str(root / 'tmp'), 'XDG_CONFIG_HOME': str(root / 'config'),
        'XDG_CACHE_HOME': str(root / 'cache'), 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONNOUSERSITE': '1', 'UV_CACHE_DIR': str(uv_cache),
        'UV_PYTHON_DOWNLOADS': 'never', 'UV_NO_PROGRESS': '1',
        'UV_NO_GITHUB_FAST_PATH': '1',
        'UV_HTTP_TIMEOUT': '5', 'UV_HTTP_RETRIES': '0',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_COUNT': '0',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file',
        'AMPLIFIER_WEB_HOME': str(root / 'app'), 'AMPLIFIER_HOME': str(root / 'shared'),
        'AMPLIFIER_UNIFIED_IMPORT_HOME': str(root / 'legacy'),
        'AMPLIFIER_SESSION_STATE_HOME': str(root / 'ownership'),
        'AMPLIFIER_SOURCE_STORE': str(root / 'app/source-store'),
        'AMPLIFIER_UNIFIED_RELEASE': '',
        'OMITTED_REF_MOUNTS': str(root / 'mounts.jsonl'),
        'OMITTED_REF_MODEL_CALLS': str(root / 'model-calls'),
    })
    for name in ('home', 'tmp', 'workspace', 'shared', 'origin', 'bin'):
        (root / name).mkdir(mode=0o700)
    config = root / 'gitconfig'
    config.write_text('[url "' + (root / 'origin').as_uri() + '"]\n insteadOf = ' + URL + '\n'
                      '[core]\n hooksPath = /dev/null\n[credential]\n helper =\n')
    os.environ['GIT_CONFIG_GLOBAL'] = str(config)
    # Git expands insteadOf even on get-url. Identity checks need the stored
    # authored origin, not its test-only transport. All other argv/env are exact.
    wrapper = root / 'bin/git'
    wrapper.write_text('#!' + sys.executable + '\nimport os, sys\n'
        'args = sys.argv[1:]\nenv = dict(os.environ)\n'
        "if args == ['remote', 'get-url', 'origin'] or (len(args) == 5 and args[0] == '-C' and args[2:] == ['remote', 'get-url', 'origin']):\n"
        "    env['GIT_CONFIG_GLOBAL'] = os.devnull\n"
        'os.execve(' + repr(git_binary) + ', [' + repr(git_binary) + '] + args, env)\n')
    wrapper.chmod(0o700)
    os.environ['PATH'] = str(root / 'bin') + os.pathsep + path


def lock_identity(data, project_name):
    """Compare every original package entry, excluding only root/fixture rows."""
    return sorted((row for row in data.get('package', [])
                   if row['name'] not in {project_name, *PACKAGES}),
                  key=lambda row: json.dumps(row, sort_keys=True))


def prepare_manifest(args, destination, evidence):
    """Accept only the canonical setup contract, then append the fixture deps."""
    data = tomllib.loads(args.runtime_manifest.read_text())
    setup_lock = tomllib.loads(args.runtime_lock.read_text())
    canonical = tomllib.loads((CHECKOUT / 'amplifier_web/runtime_deps/pyproject.toml').read_text())
    required = {requirement_name(value) for value in canonical['project']['dependencies']}
    repository_lock = tomllib.loads((CHECKOUT / 'uv.lock').read_text())
    loop_name = 'amplifier-module-loop-live'
    loop_rows = [row for row in repository_lock['package'] if row['name'] == loop_name]
    require(len(loop_rows) == 1, 'Repository lock has ambiguous loop identity')
    loop = urlsplit(loop_rows[0]['source']['git'])
    require(re.fullmatch('[a-f0-9]{40}', loop.fragment) is not None, 'Repository loop pin is missing')
    expected = copy.deepcopy(canonical)
    expected['project']['requires-python'] = '>=3.13,<3.14'
    expected['tool']['uv']['environments'] = ["sys_platform == 'linux'"]
    # This exact structural comparison also preserves all direct dependencies,
    # constraints, registry Core policy, source URLs and original branch refs.
    normalized = copy.deepcopy(data)
    normalized['project']['requires-python'] = normalized['project']['requires-python'].replace(' ', '')
    normalized['tool']['uv']['environments'] = [
        value.replace('"', "'") for value in normalized['tool']['uv'].get('environments', [])]
    require(normalized == expected, 'Supplied manifest differs from canonical setup contract')
    require(required <= {row['name'] for row in setup_lock['package']}, 'Setup lock omits canonical dependencies')
    require(not any(row['name'] in PACKAGES for row in setup_lock['package']), 'Setup lock already contains fixture packages')
    setup_loop = [row for row in setup_lock['package'] if row['name'] == loop_name]
    require(len(setup_loop) == 1, 'Setup lock has ambiguous loop identity')
    locked_loop = urlsplit(setup_loop[0]['source']['git'])
    require(locked_loop._replace(query='', fragment='').geturl() == expected['tool']['uv']['sources'][loop_name]['git']
            and locked_loop.fragment == loop.fragment
            and parse_qs(locked_loop.query).get('rev') == ['main'], 'Setup lock changed the original loop identity')
    core = [row for row in setup_lock['package'] if row['name'] == 'amplifier-core']
    require(len(core) == 1 and core[0]['source'] == {'registry': 'https://pypi.org/simple'}
            and core[0].get('wheels'), 'Core must retain its registry-wheel identity')
    project_name = data['project']['name']
    root_rows = [row for row in setup_lock['package'] if row['name'] == project_name]
    require(len(root_rows) == 1 and root_rows[0]['source'] == {'virtual': '.'}, 'Setup lock is not the runtime project lock')
    original_graph = lock_identity(setup_lock, project_name)
    data['project']['dependencies'].extend(PACKAGES)
    selected = data.setdefault('tool', {}).setdefault('uv', {}).setdefault('sources', {})
    for name, (_, kind) in PACKAGES.items():
        selected[name] = {'git': URL, 'branch': 'main', 'subdirectory': 'modules/' + kind}
    destination.write_text(manifest_text(data))
    evidence.report['supply'] = {'manifestSha256': digest(args.runtime_manifest),
        'setupLockSha256': digest(args.runtime_lock),
        'canonicalManifestSha256': digest(CHECKOUT / 'amplifier_web/runtime_deps/pyproject.toml'),
        'repositoryLockSha256': digest(CHECKOUT / 'uv.lock'),
        'generatedManifestSha256': digest(destination), 'loopRevision': loop.fragment,
        'directDependencyCount': len(required), 'setupPackageCount': len(original_graph)}
    evidence.save()
    return required, original_graph, project_name


def fixture_source(value, kind, revision):
    parsed = urlsplit(value)
    require(parsed._replace(query='', fragment='').geturl() == URL
            and parsed.fragment == revision
            and parse_qs(parsed.query, strict_parsing=True) == {
                'branch': ['main'], 'subdirectory': ['modules/' + kind]},
            'Fixture source differs from its exact branch, subdirectory or commit')


def verify_expanded_lock(args, path, project_name, original_graph, revision):
    """Only the three fixture rows and their root declarations may be added."""
    original = tomllib.loads(args.runtime_lock.read_text())
    expanded = tomllib.loads(path.read_text())
    for data in (original, expanded):
        names = [row['name'] for row in data['package']]
        require(len(names) == len(set(names)), 'Lock contains duplicate package rows')
    require(lock_identity(expanded, project_name) == original_graph
            and len(expanded['package']) == len(original['package']) + len(PACKAGES),
            'Expanded lock changed the original package graph')
    require({k: v for k, v in original.items() if k != 'package'}
            == {k: v for k, v in expanded.items() if k != 'package'},
            'Expanded lock changed the canonical lock policy')
    for name, (_, kind) in PACKAGES.items():
        rows = [row for row in expanded['package'] if row['name'] == name]
        require(len(rows) == 1 and rows[0]['version'] == '0.1.0'
                and not rows[0].get('dependencies') and set(rows[0]['source']) == {'git'},
                'Expanded lock lacks the exact dependency-free fixture package')
        fixture_source(rows[0]['source']['git'], kind, revision)
    roots = [row for row in expanded['package'] if row['name'] == project_name]
    require(len(roots) == 1, 'Expanded lock has ambiguous root identity')
    root = copy.deepcopy(roots[0])
    for entries, metadata in ((root['dependencies'], False),
                              (root['metadata']['requires-dist'], True)):
        added = [row for row in entries if row['name'] in PACKAGES]
        require(len(added) == len(PACKAGES) and {row['name'] for row in added} == set(PACKAGES),
                'Expanded root must declare each fixture exactly once')
        for row in added:
            if metadata:
                require(set(row) == {'name', 'git'}, 'Unexpected fixture requirement')
                fixture_source(row['git'], PACKAGES[row['name']][1], '')
            else:
                require(set(row) == {'name'}, 'Unexpected fixture dependency')
        entries[:] = [row for row in entries if row['name'] not in PACKAGES]
    require([root] == [row for row in original['package'] if row['name'] == project_name],
            'Expanded lock changed an original root declaration')
    return len(expanded['package'])


async def repository_identity(repo, process):
    """Require a clean, self-contained main checkout, not a linked worktree."""
    repo = plain_path(repo, tree=True)
    git = repo / '.git'
    require(git.is_dir() and not any((git / name).exists() for name in (
        'objects/info/alternates', 'objects/info/http-alternates', 'commondir', 'shallow')),
        'Fixture Git checkout is not self-contained')
    require(Path(await process('git', 'rev-parse', '--show-toplevel', cwd=repo)) == repo
            and Path(await process('git', 'rev-parse', '--absolute-git-dir', cwd=repo)) == git,
            'Fixture Git checkout resolves outside its supply')
    require(await process('git', 'symbolic-ref', '--short', 'HEAD', cwd=repo) == 'main',
            'Fixture checkout is not on main')
    require(not await process('git', '--no-optional-locks', 'status', '--porcelain',
                              '--untracked-files=all', '--ignored=matching', cwd=repo),
            'Fixture checkout is dirty')
    await process('git', 'fsck', '--full', '--no-reflogs', cwd=repo)
    revision = str(await process('git', 'rev-parse', 'HEAD', cwd=repo))
    tree = str(await process('git', 'rev-parse', 'HEAD^{tree}', cwd=repo))
    require(all(re.fullmatch(r'[a-f0-9]{40}', value) for value in (revision, tree)),
            'Invalid fixture Git identity')
    content = hashlib.sha256()
    for path in sorted(repo.rglob('*')):
        relative = path.relative_to(repo)
        if path.is_file() and '.git' not in relative.parts:
            content.update(relative.as_posix().encode() + b'\0')
            content.update(bytes.fromhex(digest(path)))
    return {'repoCommit': revision, 'repoTree': tree, 'repoContentSha256': content.hexdigest()}


def canonical_git_transports(graph):
    """Derive only original Git identities; named refs are narrowly main-only."""
    selected = {}
    for package in graph:
        source = package.get('source', {}).get('git')
        if source is None:
            continue
        require(isinstance(source, str), 'Invalid canonical Git source')
        parsed = urlsplit(source)
        url = parsed._replace(query='', fragment='').geturl()
        require(re.fullmatch(r'https://github\.com/microsoft/amplifier-[a-z0-9-]+(?:\.git)?', url)
                and re.fullmatch(r'[a-f0-9]{40}', parsed.fragment),
                'Canonical Git transport requires an owned URL and exact commit')
        query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
        kinds = set(query) & {'rev', 'branch', 'tag'}
        require(len(kinds) == 1 and set(query) <= kinds | {'subdirectory'}
                and all(len(values) == 1 and values[0] for values in query.values()),
                'Canonical Git source has ambiguous ref policy')
        kind, = kinds
        authored_ref = query[kind][0]
        if re.fullmatch(r'[a-f0-9]{40}', authored_ref):
            require(kind == 'rev' and authored_ref == parsed.fragment,
                    'Canonical exact ref differs from its locked commit')
            ref = authored_ref
        else:
            require(authored_ref == 'main', 'Canonical transport supports only named main refs')
            ref = ('refs/tags/' if kind == 'tag' else 'refs/heads/') + authored_ref
        row = {'url': url, 'ref': ref, 'revision': parsed.fragment,
               'mirrorrelative': 'canonical-git/' + hashlib.sha256(url.encode()).hexdigest() + '.git'}
        key = (url, authored_ref)
        require(key not in selected or selected[key] == row,
                'Canonical Git ref has conflicting kinds or locked commits')
        selected[key] = row
    require(selected, 'Original graph has no canonical Git transports')
    return sorted(selected.values(), key=lambda row: (row['url'], row['ref']))


def canonical_mirror(root, row):
    mirror = plain_path(root / row['mirrorrelative'], tree=True)
    require(mirror.is_dir() and not any((mirror / name).exists() for name in (
        'objects/info/alternates', 'objects/info/http-alternates', 'info/grafts',
        'commondir', 'shallow', '.git', '.venv')),
        'Canonical Git mirror is not self-contained')
    require(all(not path.is_file() or path.stat().st_nlink == 1 for path in mirror.rglob('*')),
            'Canonical Git mirror contains shared hardlinks')
    return mirror


async def prepare_canonical_git(root, graph, process):
    """Online setup only: clone the SAME authored URLs using existing mappings."""
    rows = canonical_git_transports(graph)
    (root / 'canonical-git').mkdir(mode=0o700)
    for url in sorted({row['url'] for row in rows}):
        group = [row for row in rows if row['url'] == url]
        target = root / group[0]['mirrorrelative']
        require(not target.exists(), 'Canonical mirror destination already exists')
        await process('git', 'clone', '--bare', '--no-hardlinks', url, str(target), timeout=900)
        mirror = canonical_mirror(root, group[0])
        revisions = [row['revision'] for row in group]
        actual = await process('git', 'rev-parse', '--absolute-git-dir',
                               '--is-bare-repository', '--is-shallow-repository',
                               *(revision + '^{commit}' for revision in revisions), cwd=mirror)
        require(actual.splitlines() == [str(mirror), 'true', 'false', *revisions],
                'Canonical mirror lacks a complete exact commit')
        refs = set((await process('git', 'for-each-ref', '--format=%(refname)', cwd=mirror)).splitlines())
        for row in group:
            ref, revision = row['ref'], row['revision']
            if ref.startswith('refs/'):
                require(ref in refs, 'Authored named ref is absent from its repository')
                if ref.startswith('refs/heads/'):
                    # rev=main is accepted only as an actual, unambiguous branch.
                    # Snapshot that branch, never manufacture a HEAD/main alias.
                    require('refs/tags/main' not in refs, 'Canonical main has ambiguous ref kind')
                    await process('git', 'update-ref', ref, revision, cwd=mirror)
                else:
                    # Keep the tag object (including annotated tags), not a new branch.
                    require(await process('git', 'rev-parse', ref + '^{commit}', cwd=mirror) == revision,
                            'Canonical tag differs from its locked commit')
    receipt = root / 'canonical-transports.json'
    with receipt.open('x') as stream:
        stream.write(json.dumps(rows, indent=2, sort_keys=True) + '\n')
    receipt.chmod(0o600)


async def canonical_git_identity(root, process):
    """Verify the private exact-graph receipt, real Git objects/refs and file bytes."""
    receipt = plain_path(root / 'canonical-transports.json')
    require(receipt.is_file() and receipt.stat().st_size <= 65536, 'Invalid canonical transport receipt')
    original = tomllib.loads((root / 'original/uv.lock').read_text())
    rows = canonical_git_transports(original['package'])
    require(json.loads(receipt.read_text()) == rows, 'Canonical transport receipt differs from original graph')
    content = hashlib.sha256()
    urls = sorted({row['url'] for row in rows})
    for url in urls:
        group = [row for row in rows if row['url'] == url]
        mirror = canonical_mirror(root, group[0])
        require(await process('git', 'config', '--local', '--get', 'remote.origin.url', cwd=mirror) == url,
                'Canonical mirror changed its authored origin')
        expressions = [value + '^{commit}' for row in group for value in (row['revision'], row['ref'])]
        actual = await process('git', 'rev-parse', '--absolute-git-dir',
                               '--is-bare-repository', '--is-shallow-repository', *expressions, cwd=mirror)
        require(actual.splitlines() == [str(mirror), 'true', 'false',
                                       *(row['revision'] for row in group for _ in range(2))],
                'Canonical mirror commit or named ref changed')
        await process('git', 'fsck', '--full', '--no-reflogs', cwd=mirror)
        for path in sorted(mirror.rglob('*')):
            if path.is_file():
                content.update(path.relative_to(root).as_posix().encode() + b'\0')
                content.update(bytes.fromhex(digest(path)))
    return {'canonicalGitTransportCount': len(urls),
            'canonicalGitReceiptSha256': digest(receipt),
            'canonicalGitContentSha256': content.hexdigest()}


async def verify_canonical_transports(root, supply, rows, process):
    """Exercise the consumer's actual wrapper/mappings before baseline install."""
    require(os.environ.get('GIT_ALLOW_PROTOCOL') == 'file'
            and os.environ.get('GIT_CONFIG_COUNT') == '0'
            and os.environ.get('GIT_CONFIG_GLOBAL') == str(root / 'gitconfig')
            and shutil.which('git') == str(root / 'bin/git'),
            'Canonical transport readiness requires the isolated Git wrapper')
    for row in rows:
        mirror = plain_path(supply / row['mirrorrelative'])
        require(await process('git', 'remote', 'get-url', 'origin', cwd=mirror, timeout=15) == row['url'],
                'Git wrapper did not preserve the authored canonical origin')
        # No env substitution: this is exactly the Git command path used by the
        # isolated worker. Ask for the peeled tag too, never substitute HEAD.
        # An unadvertised exact SHA also fails readiness; object availability
        # alone cannot certify this transport, and no branch alias is invented.
        output = await process('git', 'ls-remote', '--exit-code', row['url'],
                               row['ref'], row['ref'] + '^{}', timeout=15)
        lines = [line.split('\t') for line in output.splitlines()]
        require(len(output) <= 4096 and 1 <= len(lines) <= 2
                and all(len(line) == 2 and re.fullmatch(r'[a-f0-9]{40}', line[0]) for line in lines),
                'Canonical transport returned invalid ref evidence')
        refs = {ref: revision for revision, ref in lines}
        allowed = {row['ref'], row['ref'] + '^{}'} if row['ref'].startswith('refs/tags/') else {row['ref']}
        require(len(refs) == len(lines) and row['ref'] in refs and set(refs) <= allowed
                and refs.get(row['ref'] + '^{}', refs[row['ref']]) == row['revision'],
                'Canonical transport did not advertise its exact locked commit')
    return {'verifiedTransportRefCount': len(rows)}


def canonical_lock_source(value):
    """Pure Git lock semantics; ref spelling may later normalize to its commit."""
    require(isinstance(value, str), 'Canonical Git lock source is not text')
    parsed = urlsplit(value)
    query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
    kinds = set(query) & {'rev', 'branch', 'tag'}
    require(len(kinds) == 1 and set(query) <= kinds | {'subdirectory'}
            and all(len(values) == 1 and isinstance(values[0], str) and values[0]
                    for values in query.values())
            and re.fullmatch(r'[a-f0-9]{40}', parsed.fragment),
            'Canonical Git lock source is ambiguous or lacks an exact commit')
    kind, = kinds
    return {'url': parsed._replace(query='', fragment='').geturl(),
            'ref': query[kind][0], 'refKind': kind,
            'subdirectory': query.get('subdirectory', [''])[0], 'revision': parsed.fragment}


def verify_canonical_generation(setup_graph, generation_lock, policies):
    """Pure, read-only comparison of original rows against qualified semantics.

    Inputs are original package rows, parsed generation uv.lock and parsed
    runtime-sources.json. Ignore resolver formatting/metadata, not version or
    source identity. Return only bounded counts and a semantic identity hash.
    """
    original = {row['name']: row for row in setup_graph}
    current_rows = generation_lock['package']
    current = {row['name']: row for row in current_rows}
    require(len(original) == len(setup_graph) and len(current) == len(current_rows)
            and original.keys() <= current.keys() and not original.keys() & PACKAGES.keys(),
            'Canonical generation has missing or ambiguous package identities')
    require(isinstance(policies, dict), 'Canonical generation has no source policy map')
    semantic = []
    git_count = registry_count = 0
    for name, expected in sorted(original.items()):
        observed = current[name]
        require(expected.get('version') and observed.get('version') == expected['version'],
                'An unselected canonical package changed version')
        source, actual_source = expected.get('source', {}), observed.get('source', {})
        if set(source) == {'registry'}:
            require(actual_source == source, 'An unselected registry package changed origin')
            semantic.append({'name': name, 'version': expected['version'], 'source': source})
            registry_count += 1
            continue
        require(set(source) == set(actual_source) == {'git'},
                'An unselected canonical package changed source kind')
        expected_git = canonical_lock_source(source['git'])
        actual_git = canonical_lock_source(actual_source['git'])
        identity = {key: expected_git[key] for key in ('url', 'subdirectory', 'revision')}
        require(all(actual_git[key] == value for key, value in identity.items()),
                'An unselected canonical Git package changed repository, subdirectory or commit')
        # verify_resolution accepts either the authored ref or uv's exact
        # rev=<commit> normalization. Neither permits an unrelated named ref.
        require((actual_git['ref'] == expected_git['ref']
                 and actual_git['refKind'] == expected_git['refKind'])
                or (actual_git['refKind'] == 'rev' and actual_git['ref'] == identity['revision']),
                'Canonical Git lock lost the authored ref or exact normalization')
        policy = policies.get(name)
        require(isinstance(policy, dict)
                and all(policy.get(key, '') == value for key, value in identity.items()),
                'Canonical Git policy changed repository, subdirectory or commit')
        origin = policy.get('policyOrigin')
        require(isinstance(origin, dict) and isinstance(origin.get('kind'), str) and origin['kind']
                and all(origin.get(key, '') == policy.get(key, '')
                        for key in ('url', 'ref', 'subdirectory', 'revision')),
                'Canonical Git policy origin is not bound to its source')
        authored_ref = expected_git['ref']
        if expected_git['refKind'] == 'tag':
            valid_ref = (policy.get('ref') == identity['revision']
                         and origin['kind'] == 'tag-reference' and origin.get('originalRef') == authored_ref)
        else:
            valid_ref = (policy.get('ref') == authored_ref and origin['kind'] != 'tag-reference'
                         and origin.get('originalRef', authored_ref) == authored_ref)
            # freeze_policies conservatively retains rev=main as an unresolved
            # exact pin; freeze may instead overlay the preserved staged main
            # policy. Do not manufacture branch authority or reject either form.
            valid_ref = valid_ref or (expected_git['refKind'] == 'rev'
                and policy.get('ref') == identity['revision']
                and origin['kind'] == 'unresolved-reference' and origin.get('originalRef') == authored_ref)
        require(valid_ref, 'Canonical Git policy lost its authored ref semantics')
        semantic.append({'name': name, 'version': expected['version'],
                         'source': {**identity, 'ref': authored_ref, 'refKind': expected_git['refKind']}})
        git_count += 1
    core = original.get('amplifier-core', {})
    require(core.get('source') == {'registry': 'https://pypi.org/simple'},
            'Canonical Core must retain its original registry origin')
    return {'packageCount': len(original), 'gitPackageCount': git_count,
            'registryPackageCount': registry_count,
            'semanticSha256': hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()}


async def supply_identity(root, process):
    files = {
        'originalManifestSha256': root / 'original/pyproject.toml',
        'originalLockSha256': root / 'original/uv.lock',
        'expandedManifestSha256': root / 'expanded/pyproject.toml',
        'expandedLockSha256': root / 'expanded/uv.lock',
        'settingsSha256': root / 'shared/settings.yaml',
    }
    require(all(plain_path(path).is_file() for path in files.values()),
            'Supply file is not regular')
    return {
        'schema': 1, 'fixtureSha256': digest(__file__),
        'canonicalManifestSha256': digest(CHECKOUT / 'amplifier_web/runtime_deps/pyproject.toml'),
        'repositoryLockSha256': digest(CHECKOUT / 'uv.lock'),
        **{name: digest(path) for name, path in files.items()},
        **(await repository_identity(root / 'origin', process)),
        **(await canonical_git_identity(root, process)),
    }


async def make_repository(root, process):
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.host.config import PRECONFIGURED_BUNDLES
    repo = root / 'origin'
    await process('git', 'init', '-b', 'main', cwd=repo)
    await process('git', 'config', 'user.email', 'fixture@example.invalid', cwd=repo)
    await process('git', 'config', 'user.name', 'Fixture', cwd=repo)
    await process('git', 'config', 'commit.gpgsign', 'false', cwd=repo)
    plan = {'bundle': {'name': PROFILE, 'version': SNAPSHOT_VERSION},
            'session': {'orchestrator': {'module': 'loop-live'},
                        'context': {'module': 'context-omittedfixture', 'source': './modules/context'}},
            'providers': [{'module': 'provider-omittedfixture', 'source': './modules/provider'}],
            'hooks': [{'module': 'hooks-omittedfixture', 'source': './modules/hooks'}]}
    (repo / 'bundle.yaml').write_text(json.dumps(plan))
    for name, (_, kind) in PACKAGES.items():
        directory = repo / 'modules' / kind
        package = directory / name.replace('-', '_')
        package.mkdir(parents=True)
        (directory / 'pyproject.toml').write_text(manifest_text({
            'project': {'name': name, 'version': '0.1.0'},
            'build-system': {'requires': [], 'build-backend': 'backend', 'backend-path': ['.']}}))
        (directory / 'backend.py').write_text(BACKEND)
        (package / '__init__.py').write_text({'context': CONTEXT, 'provider': PROVIDER, 'hooks': HOOK}[kind])
        (package / '_proof.py').write_text(PROOF_MODULE)
        (package / '_payload.py').write_text('PAYLOAD = 0\n')
    (root / 'shared/settings.yaml').write_text(json.dumps({
        'bundle': {'active': PROFILE, 'app': [], 'added': {PROFILE: URI}},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS), 'entries': [
            {'name': name, 'uri': uri, 'role': 'standalone', 'enabled': False}
            for name, uri in PRECONFIGURED_BUNDLES.items()]}}))
    await process('git', 'add', '.', cwd=repo)
    await process('git', 'commit', '-m', 'baseline', cwd=repo)
    return await process('git', 'rev-parse', 'HEAD', cwd=repo)


async def prepare_supply(args, evidence):
    """Resolve and install once in the adapter's online dependency-setup phase."""
    original_path, original_bytecode = list(sys.path), sys.dont_write_bytecode
    observations = ProcessObservations(evidence)
    failure = None
    try:
        require(sys.platform == 'linux' and sys.version_info[:2] == (3, 13),
                'Requires Linux and Python 3.13')
        require(int(Path('/proc/self/stat').read_text().split(' ', 1)[0]) == os.getpid(),
                'Process evidence requires namespace-local procfs')
        uv = shutil.which('uv')
        require(uv and shutil.which('git'), 'Requires installed uv and Git')
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(CHECKOUT))
        asyncio.create_subprocess_exec = observations
        from amplifier_web import updates
        from amplifier_web import runtime_qualification as qualification
        evidence.phase('prepare-supply-repository')
        for name in ('origin', 'shared', 'original', 'expanded'):
            (args.root / name).mkdir(mode=0o700)
        baseline_revision = str(await make_repository(args.root, updates.process))
        shutil.copy2(args.runtime_manifest, args.root / 'original/pyproject.toml')
        shutil.copy2(args.runtime_lock, args.root / 'original/uv.lock')
        expanded = args.root / 'expanded'
        evidence.phase('prepare-supply-manifest')
        required, setup_graph, project_name = prepare_manifest(args, expanded / 'pyproject.toml', evidence)
        shutil.copy2(args.runtime_lock, expanded / 'uv.lock')
        require(digest(expanded / 'uv.lock') == evidence.report['supply']['setupLockSha256'],
                'Original setup lock copy changed')
        evidence.phase('prepare-supply-canonical-transports')
        await prepare_canonical_git(args.root, setup_graph, updates.process)
        evidence.phase('prepare-supply-install')
        # Keep the adapter's existing transport mappings and online policy.
        # No provider, model, probe or worker is mounted during preparation.
        await updates.process(uv, 'sync', '--project', str(expanded), '--python', '3.13',
                              '--cache-dir', str(args.uv_cache), timeout=900)
        evidence.phase('prepare-supply-verify')
        package_count = verify_expanded_lock(
            args, expanded / 'uv.lock', project_name, setup_graph, baseline_revision)
        graph = qualification.installed_graph(expanded)
        require(required | set(PACKAGES) <= {row['name'] for row in graph},
                'Prepared installation lacks required packages')
        require('include-system-site-packages = false'
                in (expanded / '.venv/pyvenv.cfg').read_text().lower(),
                'Prepared installation inherits host site-packages')
        for name, (_, kind) in PACKAGES.items():
            installed = [row for row in graph if row['name'] == name]
            require(len(installed) == 1 and installed[0]['version'] == '0.1.0',
                    'Prepared fixture installation is missing or ambiguous')
            direct = installed[0].get('directUrl', {})
            require(direct.get('url') == URL and direct.get('subdirectory') == 'modules/' + kind
                    and direct.get('vcs_info', {}).get('commit_id') == baseline_revision
                    and direct['vcs_info'].get('vcs') == 'git'
                    and direct['vcs_info'].get('requested_revision') == 'main',
                    'Prepared installation differs from the locked fixture source')
        identity = await supply_identity(args.root, updates.process)
        require(identity['repoCommit'] == baseline_revision
                and identity['originalManifestSha256'] == digest(args.runtime_manifest)
                == evidence.report['supply']['manifestSha256']
                and identity['originalLockSha256'] == digest(args.runtime_lock)
                == evidence.report['supply']['setupLockSha256']
                and identity['expandedManifestSha256'] == evidence.report['supply']['generatedManifestSha256'],
                'Preparation changed its source inputs')
        receipt = args.root / 'supply.json'
        with receipt.open('x') as stream:
            stream.write(json.dumps(identity, indent=2, sort_keys=True) + '\n')
        receipt.chmod(0o600)
        evidence.report['preparedSupply'] = {
            **identity, 'receiptSha256': digest(receipt),
            'packageCount': package_count, 'installedPackageCount': len(graph)}
        evidence.phase('prepare-supply-complete')
    except BaseException as error:
        failure = error
        evidence.report['failure'] = safe_failure(error, evidence.report['phase'])
    finally:
        ended = observations.settle()
        evidence.report['cleanup'].update(
            status='completed' if ended and not failure else 'settled-after-failure' if ended else 'failed',
            allOwnedProcessesEnded=ended, retainedRoot=True)
        if not ended:
            failure = failure or AssertionError('Preparation subprocess exit was not confirmed')
        asyncio.create_subprocess_exec = observations.spawn
        sys.path[:] = original_path
        sys.dont_write_bytecode = original_bytecode
        if evidence.capture_failed:
            failure = failure or RuntimeError('Preparation diagnostic capture failed')
        evidence.report['status'] = 'failed' if failure else 'passed'
        if not evidence.save_observation():
            failure = failure or RuntimeError('Preparation receipt unavailable')
    return 1 if failure else 0


async def consume_supply(args, generated, evidence, process):
    """Copy only verified fixture source/settings; never copy its environment."""
    supply = args.prepared_supply
    receipt = plain_path(supply / 'supply.json')
    require(receipt.is_file() and receipt.stat().st_size <= 16384, 'Invalid supply receipt')
    recorded = json.loads(receipt.read_text())
    identity = await supply_identity(supply, process)
    require(recorded == identity, 'Prepared supply identity changed')
    required, setup_graph, project_name = prepare_manifest(args, generated, evidence)
    require(identity['originalManifestSha256'] == evidence.report['supply']['manifestSha256']
            and identity['originalLockSha256'] == evidence.report['supply']['setupLockSha256']
            and (supply / 'original/pyproject.toml').read_bytes() == args.runtime_manifest.read_bytes()
            and (supply / 'original/uv.lock').read_bytes() == args.runtime_lock.read_bytes()
            and (supply / 'expanded/pyproject.toml').read_bytes() == generated.read_bytes(),
            'Prepared supply differs from the exact canonical transformation')
    package_count = verify_expanded_lock(
        args, supply / 'expanded/uv.lock', project_name, setup_graph, identity['repoCommit'])
    # Identity and the original graph must agree before installing transports.
    # Reuse the verified bare mirrors in place; never copy expanded/.venv.
    transports = canonical_git_transports(setup_graph)
    require(json.loads((supply / 'canonical-transports.json').read_text()) == transports,
            'Canonical transports differ from the caller original graph')
    with (args.root / 'gitconfig').open('a') as stream:
        for url in sorted({row['url'] for row in transports}):
            row = next(row for row in transports if row['url'] == url)
            mirror = plain_path(supply / row['mirrorrelative'])
            stream.write('[url ' + json.dumps(mirror.as_uri()) + ']\n insteadOf = ' + url + '\n')
    evidence.phase('canonical-transport-readiness')
    transport_readiness = await verify_canonical_transports(args.root, supply, transports, process)
    for name in ('origin', 'shared'):
        target = plain_path(args.root / name, tree=True)
        require(target.is_dir() and not any(target.iterdir()), 'Fixture destination is not empty')
    # copytree fills the empty directory made by isolated_environment. It never
    # deletes a destination or copies expanded/.venv or a prepared interpreter.
    shutil.copytree(supply / 'origin', args.root / 'origin', dirs_exist_ok=True)
    shutil.copy2(supply / 'shared/settings.yaml', args.root / 'shared/settings.yaml')
    require(await repository_identity(args.root / 'origin', process) == {
        key: identity[key] for key in ('repoCommit', 'repoTree', 'repoContentSha256')},
        'Copied fixture repository changed')
    require(digest(args.root / 'shared/settings.yaml') == identity['settingsSha256'],
            'Copied fixture settings changed')
    evidence.report['preparedSupply'] = {
        **identity, 'receiptSha256': digest(receipt), 'packageCount': package_count, **transport_readiness}
    evidence.save()
    return identity['repoCommit'], required, setup_graph, project_name


def mounts(root):
    path = root / 'mounts.jsonl'
    if not path.exists():
        return []
    require(path.stat().st_size <= 256 * 1024, 'Mount evidence exceeded its budget')
    return [json.loads(line) for line in path.read_text().splitlines()]


def native_ended(row):
    """Check exact Linux process identity without signalling a possibly reused PID."""
    try:
        current = Path('/proc', str(row['pid']), 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return True
    return current != row['processStartTicks']


def adopted(rows, *, generation, revision, payload_hash, prefix, pid, source_root, graph):
    selected = [row for row in rows if row.get('kind') == 'worker' and row.get('event') == 'mount' and row.get('pid') == pid]
    require({r['module'] for r in selected} == {name.replace('-', '_') for name in PACKAGES}, 'Worker did not mount every fixture module')
    for row in selected:
        require(row['generation'] == generation and row['revision'] == revision, 'Worker did not adopt the activated source revision')
        require(row['payloadSha256'] == payload_hash, 'Worker mounted stale same-version payload')
        require(Path(row['prefix']) == prefix and Path(row['executable']).parent == prefix / 'bin', 'Worker used a different interpreter')
        name, (_, kind) = next((name, value) for name, value in PACKAGES.items()
                              if name.replace('-', '_') == row['module'])
        installed, = [item for item in graph if item['name'] == name]
        source = source_root / 'modules' / kind
        cache = installed.get('cacheSource', {})
        installed_source = Path(installed['path']).resolve(strict=True)
        require(cache.get('url') == URL and cache.get('revision') == revision
                and cache.get('subdirectory') == 'modules/' + kind and cache.get('dirty') is False,
                'Installed distribution is not bound to the verified source object')
        # Foundation installs from its owned build view, but loads modules from
        # the immutable object. Equality of those directory names is not promised.
        if installed_source != source.resolve(strict=True):
            build = installed_source.parent.parent
            require(build.parent == source_root.parents[2] / 'builds'
                    and installed_source == build / 'modules' / kind
                    and not build.is_symlink()
                    and json.loads((build / '.amplifier_cache_meta.json').read_text()).get('buildInput') is True,
                    'Installed source is not an owned Foundation build view')
        origin = Path(row['origin'])
        expected = source / row['module'] / '__init__.py'
        packaged = prefix / 'lib/python3.13/site-packages' / row['module'] / '__init__.py'
        require(origin.is_absolute() and origin.resolve(strict=True) in {
                    expected.resolve(strict=True), packaged.resolve(strict=True)},
                'Worker module origin is neither its verified source nor installed package')
        for filename in ('__init__.py', '_proof.py', '_payload.py'):
            require((origin.parent / filename).read_bytes()
                    == (expected.parent / filename).read_bytes()
                    == (packaged.parent / filename).read_bytes()
                    == (installed_source / row['module'] / filename).read_bytes(),
                    'Worker module bytes differ from source, build view or installation')
        require(digest(origin.with_name('_payload.py')) == payload_hash,
                'Worker origin payload differs from its mount evidence')
    return selected


def rejected(call, message):
    try:
        call()
    except AssertionError as error:
        return {'status': 'rejected', 'errorType': type(error).__name__}
    raise AssertionError(message)


async def run(args, evidence):
    root = args.root
    original_environment = dict(os.environ)
    original_path, original_bytecode = list(sys.path), sys.dont_write_bytecode
    observations = ProcessObservations(evidence)
    app = runtime = environments = None
    manifest_path = None
    leases = []
    retained = []
    failure = None
    try:
        require(sys.platform == 'linux' and sys.version_info[:2] == (3, 13), 'Requires Linux and Python 3.13')
        require(int(Path('/proc/self/stat').read_text().split(' ', 1)[0]) == os.getpid(),
                'Process evidence requires namespace-local procfs')
        uv, git_binary = shutil.which('uv'), shutil.which('git')
        require(uv and git_binary, 'Requires installed uv and Git')
        evidence.phase('network-sandbox-prerequisite')
        evidence.report['network'] = require_network_sandbox(args.parent_netns)
        isolated_environment(root, git_binary, args.uv_cache)
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(CHECKOUT))
        asyncio.create_subprocess_exec = observations
        from amplifier_web import updates, update_checks
        from amplifier_web import runtime_environment as environments
        from amplifier_web import runtime_qualification as qualification
        from amplifier_web.runtime import RuntimeManager
        from amplifier_web.service import AppService
        from amplifier_web.generation_leases import acquire
        from amplifier_web.host.config import read_config
        from amplifier_web.bundles import profile_candidates
        from amplifier_web.runtime_profiles import configuration_key
        from amplifier_web.update_sequence import classify
        generated = root / 'runtime-supply.toml'
        evidence.phase('canonical-supply-prerequisite')
        baseline_revision, required, setup_graph, project_name = await consume_supply(
            args, generated, evidence, updates.process)
        manifest_path = environments.manifest_path
        environments.manifest_path = lambda: generated
        home, workspace = root / 'app', root / 'workspace'
        runtime = RuntimeManager(startup_timeout=90)
        app = AppService(home, runtime=runtime, workspace=workspace)
        runtime.app_bridge = app.app_bridge
        manager = app.update_manager = updates.UpdateManager(app)
        require(profile_candidates(read_config(workspace, home=home)) == [PROFILE], 'Unexpected profiles would fetch unrelated dependencies')
        evidence.phase('baseline-install')
        baseline = environments.prepare_project(home)
        shutil.copy2(args.prepared_supply / 'expanded/uv.lock', baseline / 'uv.lock')
        require(digest(baseline / 'uv.lock') == evidence.report['preparedSupply']['expandedLockSha256'],
                'Baseline expanded lock copy changed')
        observations.baseline_command = (
            uv, 'sync', '--project', str(baseline), '--python', '3.13', '--locked', '--offline')
        try:
            await updates.process(*observations.baseline_command, timeout=900)
        finally:
            observations.baseline_command = None
        require(digest(baseline / 'uv.lock') == evidence.report['preparedSupply']['expandedLockSha256'],
                'Offline baseline installation changed the prepared lock bytes')
        verify_expanded_lock(args, baseline / 'uv.lock', project_name, setup_graph, baseline_revision)
        baseline_graph = qualification.installed_graph(baseline)
        require(required <= {row['name'] for row in baseline_graph}, 'Baseline lacks the canonical worker graph')
        evidence.report['baseline'] = {'revision': str(baseline_revision), 'packageCount': len(baseline_graph),
                                       'lockSha256': digest(baseline / 'uv.lock'), 'setupGraphPreserved': True}

        # Real guard, not a mocked validation failure or an altered good receipt.
        evidence.phase('negative-missing-qualification')
        pointer = updates.active_release(home)
        missing = uuid.uuid4().hex
        await manager.publish(pendingRelease=missing)
        try:
            await manager.activate()
        except ValueError as error:
            require(str(error) == 'This ecosystem release has not passed validation', 'Wrong activation rejection')
            evidence.report['negativeControls'].append({'name': 'missing-qualification', 'status': 'rejected', 'productionGuard': True})
        else:
            raise AssertionError('Production activation accepted an unqualified release')
        finally:
            await manager.publish(pendingRelease=None)
        require(updates.active_release(home) == pointer, 'Negative activation changed the pointer')

        # Detect supply/policy incompatibility before either positive cycle. This
        # is a REAL read/derivation, not filtered inventory or a patched guard.
        evidence.phase('dependency-supply-update-policy-preflight')
        await environments.update_manifest(home)
        require(not any(row.get('kind') == 'runtime environment' for row in await environments.update_inventory(home)),
                'Baseline unexpectedly requires runtime policy migration')
        source = app._new_session({'bundle': PROFILE})
        app.state['sessions'].append(source)
        events = []
        async def emit(kind, data):
            # Kernel session.ready is also normalized to status=ready. Count
            # only the manager's exact worker-ready publication, not both layers.
            if (kind == 'runtime.status' and data.get('status') == 'ready'
                    and data.get('phase') == 'ready' and data.get('runtimeOwnerId')):
                events.append({'type': kind, 'sessionId': data.get('sessionId'), 'status': 'ready'})

        for cycle in (1, 2):
            evidence.phase(f'cycle-{cycle}-check')
            for name, (_, kind) in PACKAGES.items():
                (root / 'origin/modules' / kind / name.replace('-', '_') / '_payload.py').write_text(f'PAYLOAD = {cycle}\n')
            await updates.process('git', 'commit', '-am', f'same-version payload {cycle}', cwd=root / 'origin')
            checked = str(await updates.process('git', 'rev-parse', 'HEAD', cwd=root / 'origin'))
            payload_hash = hashlib.sha256(f'PAYLOAD = {cycle}\n'.encode()).hexdigest()
            proof = await update_checks.ref_policy(URL, 'main', updates.process)
            require(proof['kind'] == 'branch' and proof['revision'] == checked, 'Checked branch identity differs')
            # Use production tier classification, retaining ALL rows. Without
            # tiers the older stage path refreshes every moving Git dependency,
            # not merely the fixture packages checked by this bounded scenario.
            inventory = classify(home, await environments.update_inventory(home))
            chosen = [row for row in inventory if row.get('package') in PACKAGES]
            require(len(chosen) == len(PACKAGES), 'Fixture package inventory is incomplete')
            for row in chosen:
                require(row['eligible'] and row['ref'] == 'main' and row['url'] == URL, 'Omitted-ref source lost moving policy')
                require(row['current'] == (retained[-1]['revision'] if retained else baseline_revision), 'Previous package identity changed')
                row.update(status='update', latest=checked)
            # Retain every observed inventory row, including protected sources.
            manager.inventory = inventory
            await manager.publish(items=inventory, available=len(chosen))
            before_processes = len(observations.owned)
            selected = [row for row in inventory if row.get('status') == 'update'
                        and row.get('eligible') and row.get('kind') != 'smart tool']
            canonical_packages = {row['name'] for row in setup_graph}
            evidence.report.setdefault('cyclePolicies', []).append({
                'cycle': cycle,
                'environmentUpdate': any(row.get('kind') == 'runtime environment' for row in selected),
                'selectedCanonicalCount': sum(row.get('package') in canonical_packages for row in selected),
                'syntheticRefreshCount': sum(row.get('package') in PACKAGES for row in selected)})
            evidence.phase(f'cycle-{cycle}-install-qualify-activate')
            require(not any(row.get('package') in canonical_packages for row in selected),
                    'This scenario must not select canonical package updates')
            await manager.install()  # Owns validate(), receipt and activation.
            state = app.state['updates']
            require(state['phase'] == 'installed' and state.get('pendingRelease') is None,
                    'Production install did not finish')
            active = updates.active_release(home)
            generation = active['current']
            require(active.get('previous') == (retained[-1]['generation'] if retained else None), 'Wrong rollback pointer')
            require(generation and generation != active.get('previous'), 'No new generation was promoted')
            leases.append(acquire(home, generation, os.getpid()))
            stage = environments.receipt_directory(home, generation)
            project = environments.project_path(home, generation)
            require(project.name.startswith('q-'), 'Adoption target is not the frozen project')
            require(json.loads((stage / 'plan.json').read_text())['mode'] == 'worker', 'Data-only reuse did not qualify a worker')
            marker = json.loads((stage / 'validated.json').read_text())
            require(marker['generationSchema'] == 1 and marker['workerProtocol'] == 1, 'No production qualification receipt')
            descriptor = json.loads((stage / 'profiles-qualified.json').read_text())
            config = read_config(workspace, home=home, session_id=source['id'], registry_home=stage / 'foundation')
            require(descriptor['profiles'] == [PROFILE] and descriptor['configuration'] == configuration_key(config.settings, home, stage),
                    'Worker would qualify a different custom generation')
            qualification.verify_recorded(project, stage)
            graph = qualification.installed_graph(project)
            require(required <= {row['name'] for row in graph}, 'Qualified graph omits canonical packages')
            require('include-system-site-packages = false' in (project / '.venv/pyvenv.cfg').read_text().lower(), 'Generation inherits host site-packages')
            policies = json.loads((stage / 'runtime-sources.json').read_text())
            canonical_identity = verify_canonical_generation(
                setup_graph, tomllib.loads((project / 'uv.lock').read_text()), policies)
            evidence.report.setdefault('canonicalGenerations', []).append({
                'cycle': cycle, **canonical_identity})
            evidence.save()
            for name in PACKAGES:
                require(policies[name]['ref'] == 'main' and policies[name]['revision'] == checked and policies[name]['url'] == URL,
                        'Recorded policy differs from checked source')
                installed = next(row for row in graph if row['name'] == name)
                require(installed['version'] == '0.1.0' and installed.get('cacheSource', {}).get('revision') == checked
                        and installed['cacheSource']['dirty'] is False,
                        'Fixture did not install from the selected cache revision')
            selection = json.loads((stage / 'selected-source-declarations.json').read_text())
            require(all(selection['packages'][name]['status'] == 'selected' and selection['packages'][name]['ref'] == 'main'
                        for name in PACKAGES), 'Omitted root was not observed by the real collector')
            probes = [row for _, row in observations.owned[before_processes:] if row['kind'] == 'probe']
            roles = [row['probeRole'] for row in probes]
            require(roles.count('collect') >= 1 and roles[-2:] == ['prepare', 'compatibility']
                    and set(roles[:-2]) == {'collect'}, 'Expected collection, preparation and compatibility subprocesses')
            facts = [fact for row in probes for fact in row['probeReports']]
            # probe_record currently omits source-policy-collected from its
            # stage allowlist. The invocation role and dependenciesPrepared
            # distinguish it without retaining unsanitized probe JSON.
            require(len(facts) == len(probes)
                    and all(fact.get('dependenciesPrepared') is False for fact in facts[:-2])
                    and [fact.get('stage') for fact in facts[-2:]] == ['prepared', 'complete']
                    and all(fact.get('ok') is True for fact in facts)
                    and all(row['returncode'] == 0 for row in probes), 'Actual subprocess qualification did not complete')
            require(facts[-1].get('standalone') and facts[-1].get('providersPresent') and facts[-1].get('cliAbsent'),
                    'Real compatibility capability checks were not satisfied')
            probe_mounts = [r for r in mounts(root) if r['kind'] == 'probe' and r['generation'] == generation]
            require({r['module'] for r in probe_mounts if r['event'] == 'mount'} == {name.replace('-', '_') for name in PACKAGES},
                    'Compatibility probe did not really mount every fixture module')
            require({(r['pid'], r['module']) for r in probe_mounts if r['event'] == 'mount'} ==
                    {(r['pid'], r['module']) for r in probe_mounts if r['event'] == 'cleanup'}, 'Probe cleanup was not observed')
            require(all(r['revision'] == checked and Path(r['prefix']) == project / '.venv' and r['payloadSha256'] == payload_hash
                        for r in probe_mounts), 'Compatibility mounted the wrong revision/interpreter')
            require(all(native_ended(r) for r in probe_mounts), 'Compatibility Python process did not exit')

            evidence.phase(f'cycle-{cycle}-worker-adoption')
            from amplifier_foundation.sources.shared import SharedSourceStore
            store = SharedSourceStore(home / 'source-store')
            source_root = store.verify(URL, checked)
            command = runtime._command(home=home)
            require(Path(runtime.project_path(command)) == project and '--no-sync' in command and '--locked' in command,
                    'Production worker command does not select the frozen environment')
            negative = rejected(lambda: adopted(mounts(root), generation=generation, revision=checked,
                payload_hash=payload_hash, prefix=project / '.venv', pid=retained[-1]['nativePid'] if retained else -1,
                source_root=source_root, graph=graph),
                'Adoption verifier accepted an absent/previous worker')
            evidence.report['negativeControls'].append({'name': f'cycle-{cycle}-non-adoption', 'verifierOnly': True, **negative})
            worker_start = len(observations.owned)
            await runtime.start(source, emit)
            row = runtime.workers[source['id']]
            native_pid = row.get('native_worker_pid')
            require(type(native_pid) is int, 'runtime.ready lacked native worker identity')
            require(row['generation'] == row['source_generation'] == generation
                    and row['ready'].done() and not row['ready'].cancelled(), 'Worker did not become ready in activated generation')
            actual = adopted(mounts(root), generation=generation, revision=checked, payload_hash=payload_hash,
                             prefix=project / '.venv', pid=native_pid, source_root=source_root, graph=graph)
            workers = [p for _, p in observations.owned[worker_start:] if p['kind'] == 'worker']
            require(len(workers) == 1 and len(workers[0]['ready']) == 1, 'Missing exact runtime.ready pipe evidence')
            require(workers[0]['ready'][0]['workerIdentity']['pid'] == native_pid, 'Mount and ready came from different workers')
            await runtime.control(source['id'], 'configuration.inspect', {})
            require(len(events) == cycle, 'Missing public ready observation')
            await runtime.stop(source['id'])
            require(all(p.returncode is not None for p, r in observations.owned[worker_start:] if r['kind'] == 'worker'), 'Worker stop did not reap its launcher')
            require(all(native_ended(r) for r in actual), 'Worker Python process did not exit')
            worker_events = [r for r in mounts(root) if r['pid'] == native_pid and r['kind'] == 'worker']
            require({r['module'] for r in worker_events if r['event'] == 'cleanup'} == {r['module'] for r in actual}, 'Worker module cleanup missing')
            require(not (root / 'model-calls').exists(), 'A model invocation was attempted')
            qualification.verify_recorded(project, stage)
            payload = qualification.package_content(project)
            receipt_hashes = {name: digest(stage / name) for name in RECEIPTS if (stage / name).exists()}
            from amplifier_foundation.sources.shared import SharedSourceStore
            store = SharedSourceStore(home / 'source-store')
            source_root = store.verify(URL, checked)
            source_hashes = {str(p.relative_to(source_root)): digest(p) for p in source_root.rglob('*')
                             if p.is_file() and not p.is_symlink() and '.git' not in p.relative_to(source_root).parts}
            for old in retained:
                require(old['receiptHashes'] == {name: digest(old['stage'] / name) for name in old['receiptHashes']}, 'First receipts changed')
                qualification.verify_recorded(old['project'], old['stage'])
                require(qualification.installed_graph(old['project']) == old['graph'], 'First installed graph changed')
                require(qualification.package_content(old['project']) == old['payload'] != payload, 'Same-version payload did not change or old payload was altered')
                old_root = store.verify(URL, old['revision'])
                require(old['sourceHashes'] == {str(p.relative_to(old_root)): digest(p) for p in old_root.rglob('*')
                        if p.is_file() and not p.is_symlink() and '.git' not in p.relative_to(old_root).parts}, 'First source object changed')
                require(old['nativePid'] != native_pid and old['project'] != project, 'Worker/environment was reused across changed payloads')
            retained.append({'generation': generation, 'revision': checked, 'stage': stage, 'project': project,
                             'graph': graph, 'payload': payload, 'receiptHashes': receipt_hashes,
                             'sourceHashes': source_hashes, 'nativePid': native_pid})
            evidence.report['cycles'].append({'cycle': cycle, 'generation': generation, 'revision': checked,
                'previous': active.get('previous'), 'packageVersion': '0.1.0', 'packageCount': len(graph),
                'installedPayloadSha256': payload, 'fixturePayloadSha256': payload_hash,
                'receiptHashes': receipt_hashes, 'probeFacts': facts, 'workerCommand': command,
                'nativePid': native_pid, 'mounts': actual, 'firstProofPreserved': cycle == 2})
            evidence.save()
        require(len(evidence.report['cycles']) == 2, 'Both adjacent cycles must finish')
        require(digest(args.runtime_manifest) == evidence.report['supply']['manifestSha256']
                and digest(args.runtime_lock) == evidence.report['supply']['setupLockSha256'],
                'Caller-owned setup identity changed during qualification')
        prepared = await supply_identity(args.prepared_supply, updates.process)
        require(all(evidence.report['preparedSupply'][key] == value for key, value in prepared.items())
                and digest(args.prepared_supply / 'supply.json') == evidence.report['preparedSupply']['receiptSha256'],
                'Prepared supply changed during qualification')
        evidence.report['modelCalls'] = 0
        evidence.report['positiveChecksComplete'] = True
    except BaseException as error:
        failure = error
        evidence.report['failure'] = safe_failure(error, evidence.report['phase'])
    finally:
        if app is not None:
            evidence.report['updateState'] = phase_record(app.state['updates'])
            diagnostics = app.update_manager.diagnostics.state if app.update_manager else {}
            evidence.report['updateDiagnostics'] = {
                'events': [phase_record(row) for row in diagnostics.get('events', [])[-50:]],
                'lastFailure': phase_record(diagnostics.get('lastFailure'))}
            try:
                await app.diagnostics.flush()
                page = app.diagnostics.read(stream='updates', limit=100)
                evidence.report['phaseReports'] = [phase_record(row.get('data')) for row in page['items']]
                evidence.report['phaseReportsCapped'] = page.get('nextBefore') is not None
            except BaseException as error:
                evidence.report['diagnosticReadFailure'] = safe_failure(error, 'cleanup-diagnostics')
                failure = failure or error
            try:
                await app.close()
                evidence.report['cleanup']['appClose'] = 'completed'
            except BaseException as error:
                evidence.report['cleanup']['appClose'] = {'status': 'failed', **safe_failure(error, 'cleanup-app')}
                failure = failure or error
                if runtime is not None:
                    try:
                        await runtime.close()
                    except BaseException as closing:
                        evidence.report['cleanup']['runtimeCloseError'] = safe_failure(closing, 'cleanup-runtime')
        else:
            evidence.report['cleanup']['appClose'] = 'not-created'
            if runtime is not None:
                try:
                    await runtime.close()
                except BaseException as error:
                    evidence.report['cleanup']['runtimeCloseError'] = safe_failure(error, 'cleanup-runtime')
                    failure = failure or error
        ended = observations.settle()
        try:
            evidence.report['mountEvents'] = mounts(root)
            native_exited = all(native_ended(row) for row in evidence.report['mountEvents'])
            evidence.report['cleanup']['allObservedNativeWorkersEnded'] = native_exited
            ended = ended and native_exited
        except BaseException as error:
            evidence.report['mountEvidenceFailure'] = safe_failure(error, 'cleanup-mount-evidence')
            failure = failure or error
            ended = False
        evidence.report['cleanup'].update(status='completed' if ended and not failure else 'failed' if not ended else 'settled-after-failure',
                                         allOwnedProcessesEnded=ended, retainedRoot=True)
        if ended:
            for lease in leases:
                if lease:
                    lease.unlink(missing_ok=True)
        else:
            failure = failure or AssertionError('Owned subprocess exit was not confirmed; evidence/root/leases retained')
        if (root / 'model-calls').exists():
            failure = failure or AssertionError('A model invocation was attempted')
            evidence.report['modelCallAttempted'] = True
        if environments is not None and manifest_path is not None:
            environments.manifest_path = manifest_path
        asyncio.create_subprocess_exec = observations.spawn
        os.environ.clear()
        os.environ.update(original_environment)
        sys.path[:] = original_path
        sys.dont_write_bytecode = original_bytecode
        if evidence.capture_failed:
            failure = failure or RuntimeError('Qualification diagnostic capture failed')
        evidence.report['status'] = 'failed' if failure else 'passed'
        if not evidence.save_observation():
            failure = failure or RuntimeError('Qualification diagnostic receipt unavailable')
    return 1 if failure else 0


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('Invalid arguments')


def main():
    parser = QuietParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--runtime-manifest', required=True, type=Path)
    parser.add_argument('--runtime-lock', required=True, type=Path)
    parser.add_argument('--uv-cache', required=True, type=Path)
    parser.add_argument('--parent-netns', default='')
    parser.add_argument('--prepared-supply', type=Path)
    parser.add_argument('--prepare-supply', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    evidence = None
    try:
        args = parser.parse_args()
        require(args.root.resolve() != args.output.resolve() and not args.output.resolve().is_relative_to(args.root.resolve())
                and not args.root.resolve().is_relative_to(args.output.resolve()), 'Evidence and disposable root must be separate')
        original_home = str(Path.home())
        args.output = fresh_directory(args.output)
        evidence = Evidence(args.output, [(str(args.root.resolve()), '<fixture-root>'),
            (str(CHECKOUT), '<checkout>'), (str(args.root.resolve().parent), '<task-root>'),
            (original_home, '<original-home>')])
        if args.prepare_supply:
            require(not args.root.exists(), 'Preparation requires a new root')
        args.root = fresh_directory(args.root)
        evidence.phase('declared-supply-paths')
        for name in ('runtime_manifest', 'runtime_lock', 'uv_cache'):
            path = plain_path(getattr(args, name))
            require(path.is_relative_to(args.root.parent) and path != args.root.parent
                    and not path.is_relative_to(args.root) and not path.is_relative_to(args.output),
                    'Supply must remain under the task parent, outside fixture root/output')
            require(path.is_dir() if name == 'uv_cache' else path.is_file(), 'Wrong supply path type')
            setattr(args, name, path)
        require(args.runtime_manifest != args.runtime_lock
                and not args.runtime_manifest.is_relative_to(args.uv_cache)
                and not args.runtime_lock.is_relative_to(args.uv_cache)
                and not args.root.is_relative_to(args.uv_cache)
                and not args.output.is_relative_to(args.uv_cache)
                and any(args.uv_cache.iterdir()), 'Supply needs distinct inputs and a populated dedicated cache')
        if args.prepare_supply:
            require(args.prepared_supply is None or plain_path(args.prepared_supply) == args.root,
                    'Preparation supply must be its new root')
            args.prepared_supply = args.root
            code = asyncio.run(prepare_supply(args, evidence))
        else:
            require(args.prepared_supply is not None, 'Run requires a prepared supply')
            args.prepared_supply = plain_path(args.prepared_supply)
            require(args.prepared_supply.is_dir() and args.prepared_supply.parent == args.root.parent
                    and args.prepared_supply != args.root
                    and not args.prepared_supply.is_relative_to(args.output)
                    and not args.output.is_relative_to(args.prepared_supply)
                    and not args.prepared_supply.is_relative_to(args.uv_cache)
                    and not args.uv_cache.is_relative_to(args.prepared_supply),
                    'Prepared supply must be a separate sibling of the run root')
            code = asyncio.run(run(args, evidence))
    except SystemExit as error:
        if error.code == 0:  # --help contains only the static fixture description.
            return 0
        raise
    except BaseException as error:
        if evidence is None:
            print(json.dumps({'status': 'failed', 'completedCycles': 0, 'phase': 'arguments'}), flush=True)
            return 1
        evidence.report.update(status='failed', launcherFailure=safe_failure(error, evidence.report['phase']))
        evidence.save_observation()
        code = 1
    print(json.dumps({'status': evidence.report['status'], 'completedCycles': len(evidence.report['cycles']),
                      'phase': evidence.report['phase'], 'evidence': 'result.json'}), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
