"""Hosted Windows candidate verification, never public/live acceptance.

Uses the maintained installed trial's source stamp, private directory and owned
job patterns. Only artifact HTTP acquisition is substituted in controlled flow.
No production native verifier, owner identity, uv target or listener is mocked.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
import ctypes
import hashlib
import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import sys
import sysconfig
import threading
import time
import tomllib
import types
import uuid


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def require_ci():
    assert sys.platform == 'win32' and os.environ.get('GITHUB_ACTIONS') == 'true'
    assert sys.implementation.name == 'cpython' and sys.version_info[:2] == (3, 13)
    assert struct.calcsize('P') == 8 and not sysconfig.get_config_var('Py_GIL_DISABLED')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def evidence():
    return Path(os.environ['RUNNER_TEMP']).resolve() / 'manual-handoff-evidence'


def canonical_temp():
    # Same existing-ancestor check as windows_user_trial_probe.canonical_temp.
    original = Path(os.environ['TEMP']).absolute()
    canonical = original.resolve(strict=True)
    for path in (original, canonical):
        for component in (*reversed(path.parents), path):
            info = component.lstat()
            assert stat.S_ISDIR(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400)
    assert (original.stat().st_dev, original.stat().st_ino) == (canonical.stat().st_dev, canonical.stat().st_ino)
    return canonical


@contextmanager
def token_user_default_owner():
    """Elevated CI fixture: create new files as TokenUser, never repair files.

    Restore this process token's default owner after its contained installer
    exits. This changes neither account policy nor existing filesystem ACLs.
    """
    c = ctypes
    from ctypes import wintypes as w
    adv, kernel = c.WinDLL('advapi32', use_last_error=True), c.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    adv.GetTokenInformation.restype = w.BOOL
    adv.SetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    adv.SetTokenInformation.restype = w.BOOL
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
    token = w.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x8 | 0x80, c.byref(token)):
        raise c.WinError(c.get_last_error())
    def info(kind):
        size = w.DWORD()
        adv.GetTokenInformation(token, kind, None, 0, c.byref(size))
        value = c.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, kind, value, size, c.byref(size)):
            raise c.WinError(c.get_last_error())
        return value
    try:
        original, user = info(4), info(1)
        sid = c.cast(user, c.POINTER(c.c_void_p))[0]
        owner = c.c_void_p(sid)
        if not adv.SetTokenInformation(token, 4, c.byref(owner), c.sizeof(owner)):
            raise c.WinError(c.get_last_error())
        try:
            yield
        finally:
            if not adv.SetTokenInformation(token, 4, original, len(original)):
                raise c.WinError(c.get_last_error())
    finally:
        kernel.CloseHandle(token)


def failure(report, error):
    report['status'] = 'FAIL'
    report['failure'] = {'type': type(error).__name__}
    for key in ('errno', 'winerror'):
        if type(getattr(error, key, None)) is int:
            report['failure'][key] = getattr(error, key)
    # Only this maintained fixture's function/line, not paths or error prose.
    trace = error.__traceback__
    while trace:
        if trace.tb_frame.f_code.co_filename == __file__:
            report['failure']['fixtureLine'] = trace.tb_lineno
        module = trace.tb_frame.f_globals.get('__name__', '')
        if module.startswith('amplifier_web.'):
            report['failure']['producer'] = module + ':' + trace.tb_frame.f_code.co_name + ':' + str(trace.tb_lineno)
            report['failure'].setdefault('producerFrames', []).append(report['failure']['producer'])
        trace = trace.tb_next


async def owned_command(argv, root, *, env, cwd, timeout):
    """Contain setup descendants too; a timeout never proves tree settlement."""
    try:
        from amplifier_web.windows_jobs import WindowsOwnedProcess
    except ImportError:
        source = Path(__file__).resolve().parents[2] / 'amplifier_web'
        package = types.ModuleType('handoff_setup')
        package.__path__ = [str(source)]
        sys.modules['handoff_setup'] = package
        module = load('handoff_setup.windows_jobs', source / 'windows_jobs.py')
        WindowsOwnedProcess = module.WindowsOwnedProcess
    ownership = json.loads((evidence() / 'ownership.json').read_text())
    ownership['jobsSettled'] = False
    save(evidence() / 'ownership.json', ownership)
    job = await WindowsOwnedProcess.start(argv, cwd=str(cwd), env=env, trusted_python=sys._base_executable)
    observed = bytearray()
    async def drain(stream):
        while chunk := await stream.read(65536):
            if len(observed) < 65536:
                observed.extend(chunk[:65536-len(observed)])
    readers = [asyncio.create_task(drain(stream)) for stream in (job.stdout, job.stderr)]
    try:
        await asyncio.wait_for(job.wait(), timeout)
    finally:
        result = await job.close()
        await asyncio.gather(*readers)
        ownership['jobsSettled'] = result.active_processes == 0 and job.tree_empty and job.io_finalized
        save(evidence() / 'ownership.json', ownership)
        save(evidence() / 'preparation-command.json', {
            'settled': ownership['jobsSettled'], 'exitCode': result.payload_returncode,
            'unexpectedArgument': b'unexpected argument' in observed,
            'dependencyResolutionFailure': b'No solution found' in observed,
            'networkFailure': any(value in observed for value in (b'Failed to fetch', b'failed to download'))})
    assert ownership['jobsSettled'] and result.payload_returncode == 0


def prepare():
    require_ci()
    source = Path(__file__).resolve().parents[2]
    report = {'status': 'FAIL', 'stage': 'candidate', 'publicAcceptance': False}
    try:
        git = lambda *args: subprocess.check_output(['git', *args], cwd=source, text=True).strip()
        assert not git('status', '--porcelain')
        report['candidate'] = {'head': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}')}
        root = canonical_temp() / ('manual-handoff-' + uuid.uuid4().hex)
        assert not root.exists()
        save(evidence() / 'ownership.json', {'root': str(root), 'jobsSettled': True})
        # Standalone standard-library native boundary before installing packages.
        wf = load('native_handoff_private_setup', source / 'amplifier_web/windows_files.py')
        for path in (root, root / 'tools', root / 'bin', root / 'app', root / 'shared', root / 'workspace'):
            with wf.directory(path, owned_root=root, create=True):
                pass
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as stream:
            stream.write('HANDOFF_ROOT=' + str(root) + '\n')
        release = load('native_handoff_release_source', source / 'scripts/release.py')
        version = tomllib.loads((source / 'pyproject.toml').read_text())['project']['version']
        release.stage_source(source, 'microsoft/amplifier-unified', report['candidate']['head'], version)
        report['stage'] = 'wheel'
        uv = shutil.which('uv')
        assert uv
        asyncio.run(owned_command([uv, 'build', '--wheel', '--out-dir', str(root / 'wheels')],
                                  root, cwd=source, env=dict(os.environ), timeout=120))
        wheels = list((root / 'wheels').glob('*.whl'))
        assert len(wheels) == 1
        wheel = wheels[0]
        report['wheel'] = {'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(), 'version': version}
        env = {**os.environ, 'UV_TOOL_DIR': str(root / 'tools'), 'UV_TOOL_BIN_DIR': str(root / 'bin'),
               'UV_LINK_MODE': 'copy'}
        report['stage'] = 'uv-tool-install'
        with token_user_default_owner():
            asyncio.run(owned_command([uv, 'tool', 'install', '--python', sys.executable,
                '--no-build-package', 'amplifier-core', str(wheel)], root, cwd=root, env=env, timeout=420))
        report['creationPolicy'] = 'process-token-default-owner-TokenUser-restored'
        report.update(status='PASS', stage='prepared')
    except BaseException as error:
        failure(report, error)
    finally:
        save(evidence() / 'preparation.json', report)
    return report['status'] == 'PASS'


def private(path, root):
    from amplifier_web.windows_files import directory
    with directory(path, owned_root=root, create=True):
        pass


def set_acl(path, root, foreign=False):
    """Fixture-owned negative only, using the maintained native ACL test API."""
    from amplifier_web import windows_files as wf
    assert path.is_relative_to(root)
    api, descriptor = wf._API(), wf.PVOID()
    grant = '(A;;FR;;;WD)' if foreign else ''
    api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f'D:P(A;OICI;FA;;;{api.sid})(A;OICI;FA;;;SY){grant}', 1, ctypes.byref(descriptor), None))
    function = ctypes.WinDLL('advapi32', use_last_error=True).SetFileSecurityW
    function.argtypes = [ctypes.c_wchar_p, wf.DWORD, wf.PVOID]
    function.restype = wf.BOOL
    try:
        api.ok(function(str(path), 0x80000004, descriptor))
    finally:
        api.LocalFree(descriptor)


async def refused(operation):
    import aiohttp
    try:
        value = operation()
        if hasattr(value, '__await__'):
            await value
    except (ValueError, OSError, RuntimeError, aiohttp.ClientError):
        return
    raise AssertionError('Negative control unexpectedly passed')


async def exercise(root, wheel, report):
    from aiohttp import web
    from amplifier_web import manual_app_handoff as handoff, app_updates, app_release_artifacts as artifacts
    from amplifier_web import server, tls, browser_entry, app_component_graph as components
    from amplifier_web.deployment import load_server_config, write_private
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.app_feature_probe import DEPENDENCY_PROBE
    from amplifier_web.update_diagnostics import probe_record
    from amplifier_web.windows_read_files import open_file, stream_signature

    started = time.monotonic()
    def passed(name):
        report['checks'][name] = 'PASS'
        report.setdefault('elapsedSeconds', {})[name] = round(time.monotonic() - started, 3)
        save(root / 'child.json', report)

    report['stage'] = 'uv-owner'
    target = await app_updates.installed_target()
    assert Path(target[2]).parent.parent == Path(sys.prefix)
    assert Path(target[1]) == root / 'bin/amplifier-unified.exe'
    passed('actualUvToolOwner')
    home = root / 'app'
    config = load_server_config(home, overrides={'bind': ['127.0.0.1'], 'port': 8941})
    # Reserve the actual numeric listener before constructing the service.
    reservation = socket.socket()
    reservation.bind(('127.0.0.1', 0))
    reservation.listen(32)
    reservation.setblocking(False)
    config['port'] = reservation.getsockname()[1]
    config = tls.setup_local_ca(home, config)
    runtime = RuntimeManager(retention={'prewarm_on_select': False})
    runtime.home = home
    runner = app = None
    try:
        report['stage'] = 'actual-app'
        app = await server.create_app(home, workspace=root / 'workspace', runtime=runtime, voice=False,
            background_updates=False, background_work=False, preload_providers=False, server_config=config)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.SockSite(runner, reservation, ssl_context=tls.ssl_context(home, config)).start()
        manager = app['service'].update_manager
        binding = handoff._native_binding(manager)
        archive_info = (binding.get('direct') or {}).get('archive_info')
        report['archiveInfo'] = {
            'isObject': isinstance(archive_info, dict),
            'empty': archive_info == {},
            'hasHash': isinstance(archive_info, dict) and 'hash' in archive_info,
            'hasSha256': isinstance(archive_info, dict) and 'sha256' in (archive_info.get('hashes') or {})}
        assert binding['revision'] == report['candidate']['head']
        assert binding['ownerSid'] == browser_entry.current_owner_sid()
        passed('actualTokenUserDataAndInstallation')
        artifact = {'url': artifacts.SOURCE + '/releases/download/v' + binding['version'] + '/' + wheel.name,
                    'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(), 'path': str(wheel)}
        proof = {'binding': binding, 'artifact': artifact}
        report['stage'] = 'payload'
        payload = await handoff._offloop(handoff._payload, proof)
        assert payload['members'] > 100
        report['payloadMembers'] = payload['members']
        report['dependencyCount'] = len(binding['graph'])
        passed('actualWheelRecordAndPinnedPayload')
        report['stage'] = 'isolated-probes'
        output = await app_updates.process(target[2], '-I', '-c', app_updates.PROBE, *binding['extras'], timeout=30)
        assert probe_record(output)['ok']
        await app_updates.process(target[2], '-I', '-c', DEPENDENCY_PROBE, *binding['extras'], timeout=30)
        passed('actualIsolatedAppAndDependencyProbes')
        assert await handoff._health(manager, binding)
        passed('actualPinnedListener')
        from types import SimpleNamespace
        wrong = SimpleNamespace(home=manager.home, service=manager.service,
            running_identity={**manager.running_identity, 'instanceId': '0' * 32})
        await refused(lambda: handoff._health(wrong, binding))
        await refused(lambda: handoff._health(manager, {**binding, 'dataIdentity': '0' * 64}))
        passed('wrongHostAndDataIdentityRefused')
        token_path = home / 'config/auth/control-token'
        token = token_path.read_bytes()
        try:
            write_private(token_path, b'x' * 64, owned_root=home)
            await refused(lambda: handoff._health(manager, binding))
            write_private(token_path, b'malformed', owned_root=home)
            await refused(lambda: handoff._health(manager, binding))
        finally:
            write_private(token_path, token, owned_root=home)
        set_acl(token_path, root, True)
        try:
            await refused(lambda: handoff._health(manager, binding))
        finally:
            set_acl(token_path, root)
        assert await handoff._health(manager, binding)
        passed('wrongMalformedAndForeignAclCredentialRefused')
        received = []
        async def catcher(request):
            received.append(True)
            return web.json_response({'ok': True})
        wrong_app = web.Application()
        wrong_app.router.add_get('/api/health', catcher)
        wrong_runner = web.AppRunner(wrong_app, access_log=None)
        other = root / 'other-tls'
        private(other, root)
        other_config = tls.setup_local_ca(other, load_server_config(other))
        try:
            await wrong_runner.setup()
            wrong_site = web.TCPSite(wrong_runner, '127.0.0.1', 0, ssl_context=tls.ssl_context(other, other_config))
            await wrong_site.start()
            wrong_port = wrong_site._server.sockets[0].getsockname()[1]
            wrong = SimpleNamespace(home=home, running_identity=manager.running_identity,
                service=SimpleNamespace(server_config={**config, 'port': wrong_port}))
            await refused(lambda: handoff._health(wrong, binding))
            assert received == []
        finally:
            await wrong_runner.cleanup()
        passed('wrongLeafNoBearerDisclosure')

        report['stage'] = 'native-negatives'
        public = Path(target[1])
        original = public.read_bytes()
        try:
            public.write_bytes(b'MZwrong')
            await refused(app_updates.installed_target)
        finally:
            public.write_bytes(original)
        passed('actualLauncherCorruptionRefused')
        package = Path(metadata.distribution('amplifier-unified').locate_file('amplifier_web/static/index.html'))
        original = package.read_bytes()
        try:
            package.write_bytes(original + b'changed')
            await refused(lambda: handoff._payload(proof))
        finally:
            package.write_bytes(original)
        passed('actualPayloadCorruptionRefused')
        extra = package.parent / 'unregistered-handoff-fixture.txt'
        try:
            extra.write_bytes(b'not in RECORD')
            await refused(lambda: handoff._payload(proof))
        finally:
            extra.unlink(missing_ok=True)
        passed('unregisteredPayloadRefused')
        await refused(lambda: handoff._payload({**proof, 'artifact': {**artifact, 'sha256': '0' * 64}}))
        passed('wrongWheelHashRefused')
        record = Path(metadata.distribution('amplifier-unified')._path) / 'RECORD'
        original = record.read_bytes()
        try:
            record.write_bytes(original.replace(b'sha256=', b'sha512=', 1))
            await refused(lambda: handoff._payload(proof))
        finally:
            record.write_bytes(original)
        passed('actualRecordCorruptionRefused')
        receipt = Path(sys.prefix) / 'uv-receipt.toml'
        set_acl(receipt, root, True)
        try:
            await refused(app_updates.installed_target)
        finally:
            set_acl(receipt, root)
        passed('foreignLauncherReceiptAclRefused')
        specimen = root / 'native-leaf.txt'
        specimen.write_bytes(b'original')
        with handoff._pinned_reader() as read:
            assert read(specimen) == b'original'
            await refused(lambda: specimen.write_bytes(b'changed'))
            await refused(lambda: specimen.rename(root / 'renamed.txt'))
        specimen.write_bytes(b'released')
        passed('nativeSharingAndRelease')
        alias = root / 'leaf-alias.txt'
        os.link(specimen, alias)
        try:
            await refused(lambda: handoff._read(specimen))
        finally:
            alias.unlink()
        junction = root / 'junction'
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(root / 'workspace')],
                       check=True, capture_output=True, timeout=10)
        (root / 'workspace/sentinel').write_bytes(b'unchanged')
        try:
            await refused(lambda: handoff._read(junction / 'sentinel'))
        finally:
            junction.rmdir()
        passed('nativeHardlinkAndReparseRefused')
        # Native ChangeTime must observe metadata change with same last-write.
        with open_file(specimen) as stream:
            before = stream_signature(stream)
        stamp = specimen.stat()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        attributes = kernel.SetFileAttributesW
        attributes.argtypes, attributes.restype = [ctypes.c_wchar_p, ctypes.c_uint32], ctypes.c_int
        assert attributes(str(specimen), 0x20 | 0x2)  # archive + hidden
        os.utime(specimen, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with open_file(specimen) as stream:
            after = stream_signature(stream)
        assert before[3] == after[3] and before[4] != after[4]
        assert attributes(str(specimen), 0x20)
        passed('nativeChangeTime')
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def read_until_released():
            with handoff._pinned_reader() as read:
                read(specimen)
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(10)
        audit = asyncio.create_task(handoff._offloop(read_until_released))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            audit.cancel()
            await asyncio.sleep(0)
            assert not audit.done()
        finally:
            release.set()
        try:
            await audit
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('Cancelled audit was not cancelled')
        specimen.write_bytes(b'after-cancel')
        passed('cancelSettlesNativeHandles')

        report['stage'] = 'acquisition-refusal'
        # Candidate has a real source stamp but is not the published version.
        # Expected refusal is evidence of non-qualification, not release access.
        try:
            async with asyncio.timeout(20):
                await refused(lambda: artifacts.prepare(root / 'official-refusal', binding['version'], binding['revision']))
            passed('liveAcquisitionDidNotQualifyCandidate')
        except TimeoutError:
            report['liveAcquisition'] = 'deadline-no-qualification-not-a-pass'
        report['stage'] = 'controlled-flow'
        save(root / 'child.json', report)
        await controlled_flow(manager, wheel, proof, report)
        passed('controlledCandidateReconciliation')
    finally:
        report['listenerClosed'] = False
        if runner is not None:
            await runner.cleanup()
        elif app is not None:
            await app['service'].close()
        reservation.close()
        report['listenerClosed'] = True


async def controlled_flow(manager, wheel, proof, report):
    """Only HTTP acquisition is substituted; real on-disk fixture state is local."""
    from amplifier_web import app_release_artifacts as artifacts, app_updates, manual_app_handoff as handoff
    from amplifier_web import app_component_graph as components
    from amplifier_web.deployment import write_private
    from aiohttp import web
    import httpx
    binding, artifact = proof['binding'], proof['artifact']
    pending = {'revision': binding['revision'], 'generation': uuid.uuid4().hex,
               'attemptId': uuid.uuid4().hex, 'latest': 'v' + binding['version'], 'extras': binding['extras']}
    folder = manager.directory / 'applications' / pending['revision'] / pending['generation']
    private(folder, manager.home)
    staged_wheel = folder / wheel.name
    shutil.copyfile(wheel, staged_wheel)
    graph = components.validate_graph(sorted([*binding['graph'], {'name': 'amplifier-unified',
        'version': binding['version'], 'url': artifacts.SOURCE, 'revision': binding['revision'],
        'delivery': {key: artifact[key] for key in ('url', 'sha256')}}], key=lambda row: row['name']))
    write_private(folder / 'validated.json', json.dumps({**pending, 'version': binding['version'],
        'componentGraph': graph, 'componentDigest': components.digest(graph),
        'artifact': {**artifact, 'path': str(staged_wheel)}}), owned_root=manager.home)
    manager.diagnostics.begin('application', pending['revision'], pending['attemptId'], application_extras=binding['extras'])
    manager.diagnostics.record('restart-capability', 'failed', errorType='RuntimeError')
    state = manager.service.state['updates']
    state.update(pendingApp=pending, phase='error', error='Controlled fixture pre-effect refusal',
                 sequence={'stage': 'application', 'install': False})
    expected = handoff.require_target(manager, {'hostInstanceId': manager.service.instance_id,
        **{key: pending[key] for key in ('revision', 'generation', 'attemptId')}})
    async def payload(request):
        if request.path == '/SHA256SUMS':
            return web.Response(text=artifact['sha256'] + '  ' + wheel.name + '\n')
        assert request.path == '/' + wheel.name
        return web.Response(body=wheel.read_bytes())
    transport = web.Application()
    transport.router.add_get('/{name}', payload)
    runner = web.AppRunner(transport, access_log=None)
    original = artifacts._get
    try:
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        @asynccontextmanager
        async def local_acquisition(client, url):
            assert url in {artifact['url'], artifact['url'].rsplit('/', 1)[0] + '/SHA256SUMS'}
            # Restrict transport substitution to the two exact acquisition URLs.
            async with httpx.AsyncClient(trust_env=False) as local:
                async with local.stream('GET', 'http://127.0.0.1:' + str(port) + '/' + url.rsplit('/', 1)[1]) as response:
                    yield response
        artifacts._get = local_acquisition
        args = {'hostInstanceId': manager.service.instance_id,
                **{key: pending[key] for key in ('revision', 'generation', 'attemptId')}}
        # Deterministic acquisition mismatch, separate from live lookup refusal.
        await refused(lambda: artifacts.prepare(manager.directory / 'wrong-source',
                      binding['version'], '0' * 40))
        report['checks']['acquiredWrongSourceRefused'] = 'PASS'
        save(Path(os.environ['HANDOFF_ROOT']) / 'child.json', report)
        receipt = await manager.service.dispatch('updates.reconcileManualHandoff', args, command_id='native-controlled')
        assert receipt['accepted']
        async with asyncio.timeout(240):
            while state.get(handoff.RESULTS, {}).get('native-controlled', {}).get('status') == 'queued':
                await asyncio.sleep(.05)
        result = state[handoff.RESULTS]['native-controlled']
        assert result['status'] == 'succeeded', result['status']
        assert state['pendingApp'] is None and state['sequence']['install'] is False
        assert state['manualHandoff']['refusal'] == expected['failure']
        assert await handoff.verify_current(manager, full=True)
        duplicate = await manager.service.dispatch('updates.reconcileManualHandoff', args, command_id='native-controlled')
        assert duplicate['duplicate']
        assert not (manager.directory / 'active.json').exists()
        assert not (manager.directory / 'application-active.json').exists()
        assert handoff._native_binding(manager) == binding
        assert state.get('pendingRestart') is None and state.get('pendingReplacement') is None
        report['controlledFlow'] = {'acquisition': 'exact-two-URL-local-HTTP-substitution',
            'pending': 'synthetic-same-version-local-candidate', 'publicAcceptance': False,
            'result': result['phase'], 'installIntent': False, 'bothPointersAbsent': True,
            'installationBindingUnchanged': True, 'restartReplacementAbsent': True}
    finally:
        artifacts._get = original
        await runner.cleanup()


def child():
    require_ci()
    root = Path(os.environ['HANDOFF_ROOT'])
    prepared = json.loads((evidence() / 'preparation.json').read_text())
    report = {'status': 'FAIL', 'stage': 'start', 'candidate': prepared['candidate'], 'checks': {},
              'publicAcceptance': False, 'qualification': 'native-installed-primitives-and-controlled-candidate-flow',
              'python': list(sys.version_info[:3]), 'windowsBuild': sys.getwindowsversion().build}
    try:
        asyncio.run(exercise(root, next((root / 'wheels').glob('*.whl')), report))
        report.update(status='PASS', stage='complete')
    except BaseException as error:
        failure(report, error)
    finally:
        save(root / 'child.json', report)
    return report['status'] == 'PASS'


async def contained():
    from amplifier_web.windows_jobs import WindowsOwnedProcess
    root = Path(os.environ['HANDOFF_ROOT'])
    keep = {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'COMSPEC', 'TEMP', 'TMP',
            'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'RUNNER_TEMP', 'GITHUB_ACTIONS', 'HANDOFF_ROOT'}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update({'HOME': str(root / 'home'), 'USERPROFILE': str(root / 'home'),
           'APPDATA': str(root / 'home/roaming'), 'LOCALAPPDATA': str(root / 'home/local'),
           'AMPLIFIER_UNIFIED_IMPORT_HOME': str(root / 'legacy'),
           'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH': str(root / 'capture'),
           'UV_CACHE_DIR': str(root / 'cache'), 'UV_LINK_MODE': 'copy', 'UV_OFFLINE': '1',
           'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
           'UV_TOOL_DIR': str(root / 'tools'), 'UV_TOOL_BIN_DIR': str(root / 'bin'),
           'AMPLIFIER_WEB_HOME': str(root / 'app'), 'AMPLIFIER_HOME': str(root / 'shared'),
           'AMPLIFIER_SESSION_STATE_HOME': str(root / 'session-state'),
           'AMPLIFIER_TERMINAL_HOME': str(root / 'terminal'),
           'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH']})
    for folder in ('home/roaming', 'home/local', 'cache'):
        private(root / folder, root)
    ownership = json.loads((evidence() / 'ownership.json').read_text())
    ownership['jobsSettled'] = False
    save(evidence() / 'ownership.json', ownership)
    job = await WindowsOwnedProcess.start([sys.executable, '-I', '-B', str(Path(__file__).resolve()), 'child'],
        cwd=str(root), env=env, trusted_python=sys._base_executable)
    counts = [0, 0]
    async def drain(stream, index):
        while chunk := await stream.read(65536):
            counts[index] += len(chunk)
    readers = [asyncio.create_task(drain(stream, i)) for i, stream in enumerate((job.stdout, job.stderr))]
    try:
        # 939 shipped files each receive retained native path/handle checks;
        # qualification, final commit and reload audit are distinct passes.
        await asyncio.wait_for(job.wait(), 480)
    finally:
        result = await job.close()
        await asyncio.gather(*readers)
        ownership['jobsSettled'] = result.active_processes == 0 and job.tree_empty and job.io_finalized
        ownership['processIdentity'] = {'pid': job.identity.payload_pid,
                                        'created': str(job.identity.payload_created)}
        save(evidence() / 'ownership.json', ownership)
    report = json.loads((root / 'child.json').read_text())
    report['job'] = {'settled': ownership['jobsSettled'], 'returncode': result.payload_returncode,
                     'outputBytes': counts}
    save(evidence() / 'native.json', report)
    return report['status'] == 'PASS' and ownership['jobsSettled'] and result.payload_returncode == 0


def run():
    try:
        return asyncio.run(contained())
    except BaseException as error:
        root = Path(os.environ['HANDOFF_ROOT'])
        report = json.loads((root / 'child.json').read_text()) if (root / 'child.json').exists() else {
            'status': 'FAIL', 'stage': 'outer-job', 'checks': {}, 'publicAcceptance': False}
        failure(report, error)
        save(evidence() / 'native.json', report)
        return False


def cleanup():
    owner = evidence() / 'ownership.json'
    if not owner.exists():
        save(evidence() / 'cleanup.json', {'status': 'no-owned-root'})
        return True
    ownership = json.loads(owner.read_text())
    root = Path(ownership['root'])
    assert root.parent == canonical_temp()
    assert root.name.startswith('manual-handoff-') and not root.is_symlink()
    if (root / 'child.json').is_file():
        # Preserve completed partial facts even if the outer job timed out.
        shutil.copyfile(root / 'child.json', evidence() / 'child-progress.json')
    if not ownership['jobsSettled']:
        save(evidence() / 'cleanup.json', {'status': 'held-unsettled-job', 'removed': False})
        return False
    if root.exists():
        shutil.rmtree(root)
    save(evidence() / 'cleanup.json', {'status': 'removed', 'removed': not root.exists()})
    return not root.exists()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('prepare', 'run', 'child', 'cleanup'))
    args = parser.parse_args()
    require_ci()
    result = globals()[args.operation]()
    raise SystemExit(0 if result else 1)