"""Ordinary-root browser fixture: real Foundation/Core and production workers.

Run with the prepared interpreter: python <this-file> <new-artifact-root>.
The first stdout line is the listening HTTP URL. Login uses the production
control-token flow with fixture-browser-control-token. No sessions or mounted
controls are seeded. The browser owns creation, selection and all five sends.

GET /fixture/native-proof is strictly observational: it never calls a runtime
control, starts a worker or reads a transcript/events.jsonl. configuration.providers
receipts are captured only if an ordinary client operation requested them; the
service's browsing catalog is reported separately. Missing observations stay absent.

Disable automatic naming through session.naming before each chat's first send
(or rename it through session.rename, which disables Auto). Naming calls, if any,
are retained too, not filtered out to manufacture five calls.
Only the provider, prepared-interpreter command selection, isolated metadata
bootstrap, finite probe gate and owned proof sinks are fixture code. Releasing
the gate executes the original production probe. The audit guard refuses external networking and unlisted child
commands; it is not a replacement preparation, mount, selector or worker.

The account catalog retains -I and the production credential-free environment.
An exact-command fixture bootstrap installs the guard that -I excludes from
sitecustomize, then executes the unchanged, hashed production helper. Metadata
is never supplied by the fixture. Its child permits no further processes; the
helper's original offline guard also remains active. Parent spawn/reap records,
child origin/guard records and /proc absence join the ordinary cleanup proof.
Both fork/exec and posix_spawn retain that same exact helper launch boundary.

Installed mode: AMPLIFIER_TEST_INSTALLED=1, AMPLIFIER_TEST_INSTALLED_IDENTITY
points to {files: {wheel-relative-path: sha256}} (all wheel entries except RECORD).
AMPLIFIER_TEST_PYTHON must be the launching fresh venv interpreter. Dependencies
must resolve inside that venv or AMPLIFIER_TEST_DEPENDENCY_PREFIX, an explicitly
prepared prefix. Launch this externally copied file with PYTHONPATH unset and
cwd outside the checkout. No missing installed import falls back to source.
"""
from __future__ import annotations

import asyncio
import atexit
import contextvars
import copy
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

MODULES = {
    'amplifier_core': 'amplifier-core',
    'amplifier_core._engine': 'amplifier-core',
    'amplifier_foundation': 'amplifier-foundation',
    'amplifier_module_loop_live': 'amplifier-module-loop-live',
    'amplifier_module_loop_live.orchestrator': 'amplifier-module-loop-live',
    'amplifier_module_loop_streaming': 'amplifier-module-loop-streaming',
    'amplifier_module_context_simple': 'amplifier-module-context-simple',
    'amplifier_module_hook_context_intelligence': 'amplifier-module-hook-context-intelligence',
}
PROVIDER = r'''
import json, os, time
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
__amplifier_module_type__ = 'provider'

class NativeFixtureProvider:
    name = 'native-fixture'
    def __init__(self, config=None, coordinator=None):
        self.config = config or {}
        self.coordinator = coordinator
        self.priority = self.config.get('priority', 100)
        self.default_model = self.config.get('default_model', 'native-model')
    def get_info(self):
        return ProviderInfo(id=self.name,
            display_name=self.config.get('display_name', 'Native fixture'),
            defaults={'model':self.default_model,
                      'reasoning_effort':self.config.get('reasoning_effort')})
    def get_config_schema(self):
        return {'fields':[{'id':'reasoning_effort','field_type':'choice',
                          'choices':['low','high','xhigh'],'required':False}]}
    async def list_models(self):
        return [{'id':'native-model','display_name':'Native model'}]
    def parse_tool_calls(self, response):
        return response.tool_calls or []
    async def complete(self, request, **kwargs):
        if self.coordinator is None:
            raise RuntimeError('Metadata-only provider must not execute inference')
        identities = [name for name, provider in
                      (self.coordinator.get('providers') or {}).items() if provider is self]
        if len(identities) != 1:
            raise RuntimeError('Cannot identify the actual mounted provider instance')
        row = {'sessionId':self.coordinator.session_id, 'instance':identities[0],
            'model':kwargs.get('model') or request.model or self.default_model,
            'effort':kwargs.get('reasoning_effort') or request.reasoning_effort
                     or self.config.get('reasoning_effort'),
            'inputIds':[(m.metadata or {}).get('amplifier_input',{}).get('id')
                        for m in request.messages if m.role == 'user'],
            'pid':os.getpid(), 'atNs':time.time_ns(), 'origin':__file__,
            'requestFields':{'model':request.model, 'reasoning_effort':request.reasoning_effort},
            'keywordOverrides':{key:kwargs.get(key) for key in ('model','reasoning_effort')},
            'providerDefaults':{'model':self.default_model,
                                'reasoning_effort':self.config.get('reasoning_effort')}}
        # One append syscall per request; retain every call, including auxiliaries.
        payload = (json.dumps(row)+'\n').encode()
        fd = os.open(self.config['proof_sink'], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if os.write(fd, payload) != len(payload):
                raise RuntimeError('Incomplete provider proof append')
        finally:
            os.close(fd)
        from pathlib import Path
        from provider_native_ui_server import capture_loaded_sources
        capture_loaded_sources(Path(self.config['proof_sink']).parent, 'provider-entry', call=row)
        return ChatResponse(content=[TextBlock(text='native-fixture-response')])

async def mount(coordinator, config=None):
    await coordinator.mount('providers', NativeFixtureProvider(config, coordinator),
                            name='native-fixture')
'''


def save(path, value):
    # Initial fixture settings and proof only; never a saved native control/plan.
    path.write_text(json.dumps(value, indent=2) + '\n')


def append(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(value) + '\n')


def file_identity(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def package_origin(root):
    """Select application origin once, independently of the test's location."""
    installed = os.environ.get('AMPLIFIER_TEST_INSTALLED') == '1'
    if installed:
        if os.environ.get('PYTHONPATH'):
            raise RuntimeError('Installed fixture must start with PYTHONPATH unset')
        manifest_path = Path(os.environ['AMPLIFIER_TEST_INSTALLED_IDENTITY']).resolve(strict=True)
        manifest = read_json(manifest_path)
        files = manifest.get('files')
        if not isinstance(files, dict) or not files:
            raise ValueError('Installed identity requires a nonempty wheel files map')
        distribution = importlib.metadata.distribution('amplifier-unified')
        prefix = Path(sys.prefix).resolve(strict=True)
        if prefix == Path(sys.base_prefix).resolve():
            raise RuntimeError('Installed qualification requires a fresh virtual environment')
        verified = {}
        for relative, expected in files.items():
            path = Path(relative)
            if path.is_absolute() or '..' in path.parts or path.name == 'RECORD':
                raise ValueError(f'Invalid wheel manifest entry: {relative}')
            actual = file_identity(distribution.locate_file(relative))
            if not Path(actual['path']).is_relative_to(prefix) or actual['sha256'] != expected:
                raise RuntimeError(f'Installed wheel identity mismatch: {relative}')
            verified[relative] = actual
        spec = importlib.util.find_spec('amplifier_web')
        if spec is None or not spec.origin:
            raise RuntimeError('Installed amplifier_web is missing; no source fallback')
        application = Path(spec.origin).resolve(strict=True).parent
        if not application.is_relative_to(prefix):
            raise RuntimeError('Application did not resolve inside the installed prefix')
        # A partial manifest cannot attest an application tree. Ignore only
        # interpreter-created bytecode, never extra authored/static files.
        for path in application.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                key = str(Path('amplifier_web') / path.relative_to(application))
                if key not in verified or verified[key]['path'] != str(path.resolve()):
                    raise RuntimeError(f'Unmanifested installed application file: {key}')
        if verified.get('amplifier_web/__init__.py', {}).get('path') != str(application / '__init__.py'):
            raise RuntimeError('Wheel identity does not match the imported application')
        dependency = Path(os.environ.get('AMPLIFIER_TEST_DEPENDENCY_PREFIX', sys.prefix)).resolve(strict=True)
        policy = {'mode': 'installed', 'applicationRoot': str(application),
                  'applicationInstallRoot': str(Path(distribution.locate_file('')).resolve()),
                  'applicationPackages': sorted({Path(name).parts[0] for name in files
                      if len(Path(name).parts) > 1 and Path(name).parts[0].startswith('amplifier_')
                      and not Path(name).parts[0].endswith('.dist-info')}),
                  'applicationPrefix': str(prefix), 'dependencyPrefixes': list(dict.fromkeys(
                      (str(prefix), str(dependency)))), 'wheelFiles': verified}
        save(root / 'installed-identity.json', {'manifest': file_identity(manifest_path),
             'version': distribution.version, 'files': verified})
    else:
        source = Path(__file__).resolve().parents[2]
        application = source / 'amplifier_web'
        if not (application / 'runtime_worker.py').is_file():
            raise RuntimeError('Source fixture must reside in tests/fixtures of the checkout')
        sys.path.insert(0, str(source))
        policy = {'mode': 'source', 'applicationRoot': str(application), 'sourceRoot': str(source)}
    policy['fixture'] = file_identity(__file__)
    policy['accountCatalog'] = file_identity(application / 'decision_account_catalog.py')
    policy['providerRoot'] = str(root / 'provider')
    save(root / 'origin-policy.json', policy)
    return application, policy


def check_origin(name, location, policy):
    path = Path(location).resolve(strict=True)
    if policy['mode'] != 'installed':
        return
    if name == 'amplifier_module_provider_native_fixture' or name.startswith('amplifier_module_provider_native_fixture.'):
        if not path.is_relative_to(policy['providerRoot']):
            raise RuntimeError(f'Unexpected synthetic provider origin: {path}')
        return
    # App modules must match the wheel, dependencies the prepared prefixes.
    application = Path(policy['applicationRoot'])
    if name.split('.')[0] in policy['applicationPackages'] or path.is_relative_to(application):
        install_root = Path(policy['applicationInstallRoot'])
        if not path.is_relative_to(install_root):
            raise RuntimeError(f'Checkout application import refused: {name}: {path}')
        key = str(path.relative_to(install_root))
        expected = policy['wheelFiles'].get(key)
        if expected != file_identity(path):
            raise RuntimeError(f'Unmanifested application import: {name}: {path}')
    elif not any(path.is_relative_to(prefix) for prefix in policy['dependencyPrefixes']):
        raise RuntimeError(f'Dependency outside prepared prefixes: {name}: {path}')


def capture_loaded_sources(root, boundary, *, call=None):
    """Hash the actually loaded native binary and Python implementation files."""
    policy = read_json(root / 'origin-policy.json')
    modules, violations = {}, []
    for name, module in sorted(tuple(sys.modules.items())):
        location = getattr(module, '__file__', None)
        if not location:
            continue
        # Directly executed worker/probe scripts also import bare sibling names.
        if not name.startswith('amplifier_') and not Path(location).resolve().is_relative_to(policy['applicationRoot']):
            continue
        try:
            modules[name] = file_identity(location)
            check_origin(name, location, policy)
        except (OSError, RuntimeError) as exc:
            violations.append({'module': name, 'path': str(location), 'error': str(exc)})
    row = {'pid': os.getpid(), 'python': sys.executable, 'boundary': boundary,
           'atNs': time.time_ns(), 'modules': modules, 'violations': violations,
           **({'call': call} if call is not None else {})}
    append(root / f'loaded-boundaries-{os.getpid()}.jsonl', row)
    if violations:
        raise RuntimeError('Loaded source origin validation failed; see retained boundary evidence')
    return row


def process_identity(pid):
    # Linux-only qualification: start ticks distinguish PID reuse, not just liveness.
    stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'processGroup': int(stat[2]), 'startTicks': stat[19]}


CATALOG_ENVIRONMENT = {'PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'TZ',
                       'TMPDIR', 'TEMP', 'TMP'}


def observe_account_catalog(root, policy):
    """Instrument only the exact isolated helper; never replace its metadata."""
    original = subprocess.Popen
    requested = [sys.executable, '-I', policy['accountCatalog']['path']]
    launched = [sys.executable, '-I', policy['fixture']['path'], '--account-catalog', str(root)]
    authorized = contextvars.ContextVar('observed_account_catalog_launch', default=None)

    class ObservedPopen(original):
        def __init__(self, args, *positional, **kwargs):
            self.catalog_process = None
            self.catalog_reaped = False
            catalog = isinstance(args, (list, tuple)) and list(args) == requested
            if catalog:
                # Preserve the production environment, pipes and isolation. A
                # changed launch contract needs review, not a permissive fallback.
                if (positional or set(kwargs) != {'stdout', 'stderr', 'env'}
                        or kwargs['stdout'] != subprocess.PIPE or kwargs['stderr'] != subprocess.DEVNULL
                        or not isinstance(kwargs['env'], dict)
                        or not set(kwargs['env']).issubset(CATALOG_ENVIRONMENT)):
                    raise RuntimeError('Unexpected isolated account catalog launch contract')
                for identity in (policy['fixture'], policy['accountCatalog']):
                    if file_identity(identity['path']) != identity:
                        raise RuntimeError('Isolated account catalog source identity changed')
                args = launched
            token = authorized.set(dict(kwargs['env']) if catalog else None)
            try:
                super().__init__(args, *positional, **kwargs)
            finally:
                authorized.reset(token)
            if catalog:
                identity = {'pid': self.pid}
                try:
                    identity = process_identity(self.pid)
                except OSError as exc:
                    identity['identityError'] = f'{type(exc).__name__}: {exc}'
                self.catalog_process = {**identity,
                    'parent': process_identity(os.getpid()), 'kind': 'decision_account_catalog',
                    'requestedCommand': requested, 'command': launched,
                    'script': policy['accountCatalog'], 'spawnedAtNs': time.time_ns(),
                    'environmentNames': sorted(kwargs['env'])}
                append(root / f'account-catalog-{os.getpid()}.jsonl',
                       {'event': 'spawned', **self.catalog_process})

        def wait(self, *args, **kwargs):
            code = super().wait(*args, **kwargs)
            if self.catalog_process is not None and not self.catalog_reaped:
                append(root / f'account-catalog-{os.getpid()}.jsonl',
                       {'event': 'reaped', **self.catalog_process, 'returncode': code})
                self.catalog_reaped = True
            return code

    subprocess.Popen = ObservedPopen
    return tuple(launched), authorized.get


def isolated_account_catalog(root):
    """Explicit bootstrap because -I deliberately ignores fixture sitecustomize."""
    import runpy
    policy = read_json(root / 'origin-policy.json')
    # Python itself may add LC_CTYPE while coercing an unset locale.
    if not sys.flags.isolated or not set(os.environ).issubset(CATALOG_ENVIRONMENT | {'LC_CTYPE'}):
        raise RuntimeError('Account catalog must retain isolated, credential-free execution')
    for identity in (policy['fixture'], policy['accountCatalog']):
        if file_identity(identity['path']) != identity:
            raise RuntimeError('Isolated account catalog source identity changed')
    helper = policy['accountCatalog']['path']
    try:
        check_origin('amplifier_web.decision_account_catalog', helper, policy)
    except (OSError, RuntimeError) as exc:
        append(root / f'origin-refused-{os.getpid()}.jsonl',
               {'module': 'amplifier_web.decision_account_catalog', 'origin': helper, 'error': str(exc)})
        raise
    # Run the actual __main__ path, including its stricter no-network audit hook.
    # No source/provider paths or test environment variables enter sys.path/env.
    sys.argv = [helper]
    install_guard(root, account_catalog=True)
    runpy.run_path(helper, run_name='__main__')


def account_catalog_processes(root):
    records = [row for path in sorted(root.glob('account-catalog-*.jsonl')) for row in calls_at(path)]
    spawned = [row for row in records if row['event'] == 'spawned']
    reaped = {(row['pid'], row.get('startTicks')): row for row in records if row['event'] == 'reaped'}
    return [{**row, 'returncode': reaped.get((row['pid'], row.get('startTicks')), {}).get('returncode')}
            for row in spawned]


def install_guard(root, commands=(), *, account_catalog=False):
    """Inherited via sitecustomize; fail before any installer or remote transport."""
    allowed = {tuple(command) for command in commands}
    script_identity = file_identity(sys.argv[0])
    policy = read_json(root / 'origin-policy.json')
    catalog_command = catalog_authorized = None
    if not account_catalog:
        catalog_command, catalog_authorized = observe_account_catalog(root, policy)
    if policy['mode'] == 'installed':
        if script_identity != policy['fixture']:
            check_origin('amplifier_web.__main__', script_identity['path'], policy)
        app_module_names = {Path(name).stem for name in policy['wheelFiles']
                            if Path(name).parent == Path('amplifier_web') and name.endswith('.py')}
        class InstalledOriginFinder:
            def find_spec(self, fullname, path=None, target=None):
                if not fullname.startswith('amplifier_') and fullname not in app_module_names:
                    return None
                spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
                if spec is not None and spec.origin and spec.origin not in {'built-in', 'frozen'}:
                    if not fullname.startswith('amplifier_') and not Path(spec.origin).resolve().is_relative_to(policy['applicationRoot']):
                        return None  # Do not mistake a similarly named stdlib module for an app sibling.
                    try:
                        check_origin(fullname if fullname.startswith('amplifier_') else
                                     'amplifier_web.' + fullname, spec.origin, policy)
                    except (OSError, RuntimeError) as exc:
                        append(root / f'origin-refused-{os.getpid()}.jsonl',
                               {'module': fullname, 'origin': spec.origin, 'error': str(exc)})
                        raise
                return spec
        # Select the same Python path result, but refuse it before loading when
        # outside the attested application / explicitly prepared dependency roots.
        sys.meta_path.insert(0, InstalledOriginFinder())
    def audit(event, args):
        refused = False
        if event == 'subprocess.Popen':
            command = tuple(args[1]) if isinstance(args[1], (tuple, list)) else None
            refused = command not in allowed
            if command == catalog_command and catalog_authorized is not None:
                # The bootstrap is never an independently allowed command: each
                # invocation must retain the original launch and parent custody.
                refused = catalog_authorized() is None
        elif event == 'os.posix_spawn':
            # CPython may launch the same validated Popen through posix_spawn.
            # Admit that backend only inside its exact observed launch scope;
            # direct calls, changed arguments/env and child spawns still fail.
            environment = catalog_authorized() if catalog_authorized is not None else None
            command = tuple(args[1]) if isinstance(args[1], (tuple, list)) else None
            refused = (environment is None or command != catalog_command
                       or args[0] != catalog_command[0] or args[2] != environment)
        elif event in {'os.system', 'os.exec'}:
            refused = True
        elif event in {'socket.connect', 'socket.getaddrinfo'}:
            address = args[1] if event == 'socket.connect' else args[0]
            # Unix sockets are local. TCP/DNS is restricted to numeric loopback.
            host = address[0] if isinstance(address, tuple) else address
            if event == 'socket.connect' and isinstance(address, (str, bytes)):
                return
            try:
                refused = host != 'localhost' and not ipaddress.ip_address(host).is_loopback
            except ValueError:
                refused = True
        if refused:
            append(root / f'guard-{os.getpid()}.jsonl', {'event': event, 'atNs': time.time_ns(),
                'detail': str(args)[:1000]})
            raise RuntimeError('Native fixture forbids external acquisition/transport or an unlisted subprocess')
    sys.addaudithook(audit)
    save(root / f'guard-ready-{os.getpid()}.json', {
        'pid': os.getpid(), 'script': sys.argv[0], 'allowedCommands': [list(c) for c in sorted(allowed)],
        'observedAccountCatalogCommand': catalog_command,
        'isolated': bool(sys.flags.isolated),
        **({'bootstrap': policy['fixture'], 'environmentNames': sorted(os.environ),
            'pythonPath': list(sys.path)} if account_catalog else {})})
    def loaded_sources():
        try:
            row = capture_loaded_sources(root, 'exit')
            save(root / f'loaded-{os.getpid()}.json', {**row, 'script': script_identity})
        except BaseException as exc:
            save(root / f'loaded-{os.getpid()}.json',
                 {'pid': os.getpid(), 'error': f'{type(exc).__name__}: {exc}', 'script': script_identity})
    atexit.register(loaded_sources)


def read_json(path):
    if not path.exists():
        return None
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError(f'Fixture evidence exceeded its bound: {path.name}')
    return json.loads(path.read_text())


def calls_at(path):
    if not path.exists():
        return []
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Provider proof exceeded its bound')
    # Never silently drop an incomplete append or malformed call.
    data = path.read_text()
    if data and not data.endswith('\n'):
        raise ValueError('Provider proof append is incomplete')
    rows = [json.loads(line) for line in data.splitlines()]
    if len(rows) > 64:
        raise ValueError('Unexpectedly many provider requests')
    return rows


async def main(root):
    root.mkdir(parents=True, exist_ok=True)
    # Refuse reuse; a new attempt must not overwrite retained execution evidence.
    for name in ('app', 'workspace', 'shared', 'bootstrap', 'provider', 'user-home'):
        (root / name).mkdir()
    url_stream = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    interpreter = os.environ.get('AMPLIFIER_TEST_PYTHON', sys.executable)
    if os.path.abspath(interpreter) != os.path.abspath(sys.executable):
        raise RuntimeError('Launch the fixture with AMPLIFIER_TEST_PYTHON itself; no interpreter fallback')
    if os.name != 'posix' or not Path('/proc/self/stat').is_file():
        raise RuntimeError('This fixture requires Linux process identity evidence')
    try:
        application, origin_policy = package_origin(root)
    except BaseException as exc:
        save(root / 'failure.json', {'stage': 'package-origin', 'type': type(exc).__name__, 'message': str(exc)})
        raise
    command = [sys.executable, '-u', str(application / 'runtime_worker.py')]
    commands = [command, *[command[:-1] + [str(application / name)]
                         for name in ('draft_defaults_probe.py', 'provider_probe.py')]]
    # Inherited credentials/source-store selectors cannot turn this into a user run.
    for name in list(os.environ):
        if (name.startswith(('AMPLIFIER_', 'COPILOT_', 'AZURE_', 'AWS_', 'OTEL_'))
                or name.endswith(('_API_KEY', '_ACCESS_TOKEN', '_AUTH_TOKEN'))
                or name in {'OPENAI_KEY', 'GH_TOKEN', 'GITHUB_TOKEN'}):
            os.environ.pop(name, None)
    os.environ.update(AMPLIFIER_HOME=str(root / 'shared'), AMPLIFIER_WEB_HOME=str(root / 'app'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(root / 'ownership'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / 'capture'), AMPLIFIER_RUNTIME_IMMUTABLE='1',
        HOME=str(root / 'user-home'), XDG_CONFIG_HOME=str(root / 'user-home/.config'),
        XDG_CACHE_HOME=str(root / 'user-home/.cache'), PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / 'provider'))
    inherited_paths = [str(root / 'bootstrap'), str(root / 'provider')]
    if origin_policy['mode'] == 'source':
        inherited_paths.append(origin_policy['sourceRoot'])
    os.environ['PYTHONPATH'] = os.pathsep.join(inherited_paths)
    (root / 'bootstrap/sitecustomize.py').write_text(
        'import os, sys, traceback, importlib.util\ntry:\n'
        '    from pathlib import Path\n'
        f'    spec = importlib.util.spec_from_file_location("provider_native_ui_server", {str(Path(__file__).resolve())!r})\n'
        '    fixture = importlib.util.module_from_spec(spec)\n'
        '    sys.modules[spec.name] = fixture\n'
        '    spec.loader.exec_module(fixture)\n'
        f'    fixture.install_guard(Path({str(root)!r}))\n'
        'except BaseException:\n'
        '    traceback.print_exc()\n'
        '    os._exit(78)\n')  # site normally swallows import errors; fail closed.
    install_guard(root, commands)
    os.chdir(root / 'workspace')
    processes, watchers, ready_events, control_reads, lifecycle, routing_reads = [], [], [], [], [], []
    runtime = runner = app = None
    primary_failure = None
    original_spawn = asyncio.create_subprocess_exec
    original_command = original_setup_perform = original_defaults = None
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        # Import readiness, not a mount/prepare dry-run. No missing package fallback.
        modules = {}
        paths = {}
        for name, distribution in MODULES.items():
            module = importlib.import_module(name)
            direct_url = importlib.metadata.distribution(distribution).read_text('direct_url.json')
            modules[name] = {**file_identity(module.__file__),
                             'version': importlib.metadata.version(distribution),
                             'directUrl': json.loads(direct_url) if direct_url else None}
            if name.startswith('amplifier_module_') and '.' not in name:
                paths[name.removeprefix('amplifier_module_').replace('_', '-')] = str(Path(module.__file__).parent)
        from aiohttp import web
        from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
        from amplifier_web.runtime import RuntimeManager
        from amplifier_web.server import create_app
        from amplifier_web.setup import SetupManager
        from amplifier_web.auth import control_token_path
        from amplifier_web.session_files import validate_id
        from amplifier_web import draft_defaults
        package = root / 'provider/amplifier_module_provider_native_fixture'
        package.mkdir()
        (package / '__init__.py').write_text(PROVIDER)
        importlib.import_module('amplifier_module_provider_native_fixture')
        paths['provider-native-fixture'] = str(package.parent)
        bundle = root / 'root.yaml'
        alternate = root / 'alternate.yaml'
        root_definition = {'bundle': {'name': 'provider-native-ui', 'version': '1.0.0'},
            'session': {'orchestrator': {'module': 'loop-live', 'source': paths['loop-live'],
                                        'config': {'use_streaming': False}},
                        'context': {'module': 'context-simple', 'source': paths['context-simple'],
                                    'config': {'max_tokens': 1000000}}}}
        save(bundle, root_definition)
        alternate_definition = copy.deepcopy(root_definition)
        alternate_definition['bundle']['name'] = 'provider-native-alternate'
        # A distinct root-local connection leaves shared A/B priorities intact.
        # Shared settings override matching IDs, so do not pretend a root B=0
        # would override the shared B=20 declaration.
        alternate_definition['providers'] = [{
            'module': 'provider-native-fixture', 'id': 'native-alt',
            'source': paths['provider-native-fixture'],
            'config': {'display_name': 'Native alternate', 'priority': 0,
                       'default_model': 'native-model', 'reasoning_effort': 'high',
                       'proof_sink': str(root / 'calls.jsonl')}}]
        save(alternate, alternate_definition)
        settings = root / 'shared/settings.yaml'
        save(settings, {'bundle': {'active': str(bundle), 'app': []},
            'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)}, 'sources': {'modules': paths},
            'config': {'providers': [
                {'module': 'provider-native-fixture', 'id': identity, 'config': {
                    'display_name': label, 'priority': priority, 'default_model': 'native-model',
                    'reasoning_effort': effort, 'proof_sink': str(root / 'calls.jsonl')}}
                for identity, label, priority, effort in (
                    ('native-a', 'Native A', 1, 'low'), ('native-b', 'Native B', 20, 'high'))]}})
        settings_identity = file_identity(settings)
        control_token_path(root / 'app').write_text('fixture-browser-control-token\n')
        # Same local prepared-interpreter seam as bundle_switch_probe; real probes.
        original_command = RuntimeManager._command
        RuntimeManager._command = lambda self, *args, **kwargs: list(self.command or command)
        gate = asyncio.Event()
        delayed = {'phase': 'unarmed', 'bundle': str(alternate), 'timeoutSeconds': 45, 'probes': []}
        def gate_record(event, **values):
            append(root / 'delayed-probe.jsonl', {'event': event, 'atNs': time.time_ns(), **values})
        original_defaults = draft_defaults.resolve_defaults
        async def gated_defaults(home, workspace, bundle=None, app_bundle=None, **kwargs):
            if bundle != str(alternate):
                return await original_defaults(home, workspace, bundle, app_bundle, **kwargs)
            row = {'index': len(delayed['probes']), 'home': str(home), 'workspace': str(workspace),
                   'bundle': bundle, 'arguments': copy.deepcopy(kwargs), 'enteredAtNs': time.time_ns(),
                   'phase': 'held'}
            delayed['probes'].append(row)
            gate_record('entered', **row)
            try:
                if delayed['phase'] == 'unarmed' or len(delayed['probes']) > 8:
                    raise RuntimeError('Unexpected alternate probe outside the bounded delayed case')
                if not gate.is_set():
                    delayed['phase'] = 'held'
                    await asyncio.wait_for(gate.wait(), delayed['timeoutSeconds'])
                row.update(phase='production-probe', releasedAtNs=time.time_ns())
                gate_record('production-probe', **row)
                before_processes = len(processes)
                # Never synthesize or modify the resolved providers/effective tuple.
                result = await original_defaults(home, workspace, bundle, app_bundle, **kwargs)
                row.update(phase='ready', finishedAtNs=time.time_ns(), result=copy.deepcopy(result),
                           probeProcesses=[{**entry['identity'], 'command': entry['command'],
                                            'spawnedAtNs': entry['spawnedAtNs']}
                               for entry in processes[before_processes:] if entry['kind'] == 'draft_defaults_probe'])
                gate_record('ready', **row)
                delayed['phase'] = 'ready'
                return result
            except BaseException as exc:
                row.update(phase='error', error=f'{type(exc).__name__}: {exc}')
                delayed['phase'] = 'error'
                gate_record('error', **row)
                raise
        draft_defaults.resolve_defaults = gated_defaults
        original_setup_perform = SetupManager.perform
        async def observed_setup(manager, action, arguments):
            # Observe the real returned optional diagnostic before another setup
            # owner replaces the shared view. Never synthesize success or retry.
            result = await original_setup_perform(manager, action, arguments)
            if action == 'routing.list':
                row = {'action': action, 'arguments': copy.deepcopy(arguments),
                       'atNs': time.time_ns(), 'result': copy.deepcopy(result)}
                routing_reads.append(row)
                append(root / 'routing-discovery.jsonl', row)
            return result
        SetupManager.perform = observed_setup
        async def observed_spawn(*argv, **kwargs):
            if list(argv) not in commands:
                raise RuntimeError(f'Unexpected fixture subprocess: {argv!r}')
            process = await original_spawn(*argv, **kwargs)
            entry = {'process': process, 'identity': {'pid': process.pid},
                     'command': list(argv), 'kind': Path(argv[-1]).stem, 'spawnedAtNs': time.time_ns()}
            processes.append(entry)
            try:
                entry['identity'] = process_identity(process.pid)
            except OSError as exc:
                # Retain custody even if a failed probe exits before /proc is read.
                entry['identity']['identityError'] = f'{type(exc).__name__}: {exc}'
            append(root / 'processes.jsonl', {'event': 'spawned', **entry['identity'],
                'command': entry['command'], 'kind': entry['kind'], 'spawnedAtNs': entry['spawnedAtNs']})
            async def exited():
                code = await process.wait()
                append(root / 'processes.jsonl', {'event': 'reaped', **entry['identity'], 'returncode': code})
            watchers.append(asyncio.create_task(exited()))
            return process
        asyncio.create_subprocess_exec = observed_spawn
        runtime = RuntimeManager(command=command, startup_timeout=90)
        app = await create_app(root / 'app', workspace=str(root / 'workspace'), runtime=runtime,
                               voice=False, background_updates=False, preload_providers=False)
        service = app['service']
        runtime.app_bridge = service.app_bridge
        original_event, original_control = service.on_runtime_event, runtime.control
        async def observed_event(kind, payload):
            observed_at = time.time_ns()
            ready = None
            if kind == 'runtime.status' and payload.get('status') == 'ready' and payload.get('report'):
                row = runtime.workers.get(payload['sessionId'], {})
                ready = {'event': 'runtime.ready', 'source': 'RuntimeManager pipe reduction',
                    'pid': row.get('native_worker_pid'), 'atNs': observed_at, **copy.deepcopy(payload)}
            await original_event(kind, payload)
            if kind in {'runtime.nextMessageApplied', 'runtime.modelSelectionApplied', 'runtime.generation'}:
                event = {'kind': kind, 'atNs': observed_at, **copy.deepcopy(payload)}
                lifecycle.append(event)
                append(root / 'lifecycle.jsonl', event)
            if ready is not None:
                ready_events.append(ready)
                append(root / 'ready.jsonl', ready)
        async def observed_control(sid, operation, arguments=None, **kwargs):
            result = await original_control(sid, operation, arguments, **kwargs)
            if operation == 'configuration.providers':
                row = runtime.workers.get(sid, {})
                record = {'sessionId': sid, 'pid': row.get('native_worker_pid'),
                          'atNs': time.time_ns(), 'operation': operation, 'result': copy.deepcopy(result)}
                control_reads.append(record)
                append(root / 'provider-controls.jsonl', record)
            return result
        service.on_runtime_event, runtime.control = observed_event, observed_control
        static = application / 'static'
        if not (static / 'index.html').is_file():
            raise RuntimeError('Prepared frontend assets are missing; fixture never builds them')
        readiness = {'python': sys.executable, 'prefix': sys.prefix, 'modules': modules,
            'mode': origin_policy['mode'], 'applicationRoot': str(application),
            'originPolicy': file_identity(root / 'origin-policy.json'),
            'modulePaths': paths, 'settings': file_identity(settings), 'rootBundle': file_identity(bundle),
            'alternateBundle': file_identity(alternate),
            'fixture': file_identity(__file__), 'provider': file_identity(package / '__init__.py'),
            'sources': {name: file_identity(application / name) for name in (
                'server.py', 'service.py', 'runtime.py', 'runtime_worker.py', 'runtime_controls.py',
                'host/session.py', 'host/config.py', 'draft_defaults.py', 'draft_defaults_probe.py', 'provider_probe.py',
                'decision_account_catalog.py')},
            'static': {str(p.relative_to(static)): file_identity(p)['sha256']
                       for p in sorted(static.rglob('*')) if p.is_file()},
            'importsReady': True, 'startupGate': 'imports-and-assets-only',
            'externalAcquisitionAllowed': False}
        capture_loaded_sources(root, 'server-readiness')
        save(root / 'readiness.json', readiness)
        def proof():
            sessions = list(service.state.get('sessions', []))
            if len(sessions) > 16:
                raise ValueError('Fixture session bound exceeded')
            result = []
            for session in sessions:
                sid = session['id']
                native = session.get('runtimeSessionId') or session.get('nativeIdentity') or sid
                validate_id(native)
                controls = read_json(root / 'app/sessions' / native / 'control-state.json')
                result.append({'id': sid, 'nativeId': native, 'selection': session.get('selection'),
                    'bundle': session.get('bundle'),
                    'status': session.get('status'), 'report': session.get('runtimeReport'),
                    'error': session.get('error'), 'failure': session.get('failure'),
                    'nextMessageRevision': session.get('nextMessageRevision'),
                    'appliedConfigurationRevision': session.get('appliedConfigurationRevision'),
                    'mountedReport': read_json(root / 'app/runtime-reports' / native / 'mounted.json'),
                    'savedSelection': controls.get('selection') if controls is not None else None,
                    'controlsPresent': controls is not None,
                    'catalog': service.state.get('runtimeControl', {}).get(sid, {})})
            setup = service.state.get('setup', {})
            discovery = {'revision': service.state.get('revision'),
                'configurationRevision': service.state.get('configurationRevision', 0),
                'hostInstanceId': service.instance_id,
                'setup': {key: copy.deepcopy(setup.get(key)) for key in (
                    'configurationTarget', 'operations', 'providers', 'providerCatalogs',
                    'providersWorkspace', 'providersRequestedWorkspace', 'providersLocation',
                    'providersLoadedAt', 'routingCatalogError')},
                'draftDefaults': copy.deepcopy(service.state.get('draftDefaults', {}))}
            return {'calls': calls_at(root / 'calls.jsonl'), 'sessions': result,
                'workers': [{**entry['identity'], 'kind': entry['kind'],
                    'command': entry['command'], 'spawnedAtNs': entry['spawnedAtNs'],
                    'returncode': entry['process'].returncode} for entry in processes],
                'accountCatalogProcesses': account_catalog_processes(root),
                'workerReady': ready_events, 'providerControls': control_reads,
                'lifecycle': lifecycle, 'readiness': readiness, 'discovery': discovery,
                'routingDiscovery': routing_reads, 'delayedProbe': copy.deepcopy(delayed),
                'originRefusals': [row for path in sorted(root.glob('origin-refused-*.jsonl')) for row in calls_at(path)],
                'guardRefusals': [row for path in sorted(root.glob('guard-*.jsonl')) for row in calls_at(path)],
                'sharedSettings': {'initial': settings_identity, 'current': file_identity(settings)}}
        async def inspect(request):
            return web.json_response(proof(), headers={'Cache-Control': 'no-store'})
        async def arm(request):
            if delayed['phase'] != 'unarmed' or len(calls_at(root / 'calls.jsonl')) != 4:
                raise web.HTTPConflict(text='Arm only once, after the original four sends')
            delayed.update(phase='armed', armedAtNs=time.time_ns())
            gate_record('armed', bundle=str(alternate))
            return web.json_response(copy.deepcopy(delayed))
        async def release(request):
            if delayed['phase'] != 'held' or not delayed['probes'] or len(calls_at(root / 'calls.jsonl')) != 4:
                raise web.HTTPConflict(text='Release requires an observed held probe and no fifth call')
            delayed.update(phase='released', releasedAtNs=time.time_ns())
            gate_record('released', bundle=str(alternate))
            gate.set()
            return web.json_response(copy.deepcopy(delayed))
        app.router.add_get('/fixture/native-proof', inspect)
        app.router.add_post('/fixture/native-delay/arm', arm)
        app.router.add_post('/fixture/native-delay/release', release)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        url_stream.write(url + '\n')
        await stopped.wait()
        save(root / 'final-proof.json', proof())
    except BaseException as exc:
        primary_failure = exc
        try:
            save(root / 'failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        except OSError as capture:
            print(f'Failure receipt write failed: {capture}', file=sys.stderr)
        raise
    finally:
        cleanup_errors = []
        try:
            if runner is not None:
                await asyncio.wait_for(runner.cleanup(), 12)  # Production service.close -> runtime.close.
            elif app is not None:
                await asyncio.wait_for(app['service'].close(), 12)
        except BaseException as exc:
            cleanup_errors.append(f'runner cleanup: {type(exc).__name__}: {exc}')
        try:
            if runtime is not None:
                await asyncio.wait_for(runtime.close(), 8)
        except BaseException as exc:
            cleanup_errors.append(f'runtime close: {type(exc).__name__}: {exc}')
        try:
            capture_loaded_sources(root, 'server-cleanup')
            if any(root.glob('origin-refused-*.jsonl')):
                raise RuntimeError('Installed-origin refusal occurred; see retained origin-refused records')
        except BaseException as exc:
            cleanup_errors.append(f'loaded origins: {type(exc).__name__}: {exc}')
        # Only retained subprocess handles; fallback never kills a reused PID.
        fallback = []
        async def reap_owned(entry):
            process = entry['process']
            if process.returncode is None:
                from amplifier_web.probe_process import stop
                try:
                    current = process_identity(process.pid)
                except OSError as exc:
                    cleanup_errors.append(f'Identity unreadable: {process.pid}: {exc}')
                    return
                if current != entry['identity'] or current['processGroup'] != process.pid:
                    cleanup_errors.append(f'Identity changed: {process.pid}')
                    return
                fallback.append(process.pid)
                try:
                    await asyncio.wait_for(stop(process, grace=3), 10)
                except BaseException as exc:
                    cleanup_errors.append(f'reap {process.pid}: {type(exc).__name__}: {exc}')
        complete = False
        try:
            try:
                await asyncio.gather(*(reap_owned(entry) for entry in processes))
                if all(entry['process'].returncode is not None for entry in processes):
                    await asyncio.wait_for(asyncio.gather(*watchers), 3)
            except BaseException as exc:
                cleanup_errors.append(f'cleanup watcher: {type(exc).__name__}: {exc}')
            identities = [{**entry['identity'], 'kind': entry['kind'],
                           'returncode': entry['process'].returncode,
                           'guardReady': (root / f'guard-ready-{entry["identity"]["pid"]}.json').is_file(),
                           'loadedSources': read_json(root / f'loaded-{entry["identity"]["pid"]}.json'),
                           'procAbsent': not Path(f'/proc/{entry["identity"]["pid"]}').exists()}
                          for entry in processes]
            identities.extend({**row,
                'guardReady': (root / f'guard-ready-{row["pid"]}.json').is_file(),
                'loadedSources': read_json(root / f'loaded-{row["pid"]}.json'),
                'procAbsent': not Path(f'/proc/{row["pid"]}').exists()}
                for row in account_catalog_processes(root))
            complete = not cleanup_errors and all(row['returncode'] is not None and row['procAbsent']
                                                  and row['guardReady'] and 'identityError' not in row
                                                  and row['loadedSources'] is not None
                                                  and not row['loadedSources'].get('error')
                                                  and not row['loadedSources'].get('violations')
                                                  for row in identities)
            save(root / 'cleanup.json', {'complete': complete, 'errors': cleanup_errors,
                                        'fallbackPids': fallback, 'processes': identities})
        except BaseException as exc:
            complete = False
            cleanup_errors.append(f'cleanup receipt: {type(exc).__name__}: {exc}')
            print(json.dumps({'cleanupErrors': cleanup_errors}), file=sys.stderr)
        finally:
            asyncio.create_subprocess_exec = original_spawn
            if original_command is not None:
                RuntimeManager._command = original_command
            if original_setup_perform is not None:
                SetupManager.perform = original_setup_perform
            if original_defaults is not None:
                draft_defaults.resolve_defaults = original_defaults
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(signum)
            try:
                url_stream.close()
            except OSError as exc:
                complete = False
                print(f'URL stream close failed: {exc}', file=sys.stderr)
        if (not complete or fallback) and primary_failure is None:
            raise RuntimeError('Native fixture cleanup did not pass the ordinary-close qualification gate')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--account-catalog':
        isolated_account_catalog(Path(sys.argv[2]).resolve(strict=True))
        raise SystemExit(0)
    if len(sys.argv) != 2:
        raise SystemExit('Usage: provider_native_ui_server.py <new-artifact-root>')
    asyncio.run(main(Path(sys.argv[1]).expanduser().resolve()))
