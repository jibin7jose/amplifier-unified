"""Modeled public Windows-owner boundary; no native processes or providers."""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from amplifier_web import probe_process, windows_jobs
from amplifier_web.probe_diagnostics import communicate, stderr_tail


# Modeled POSIX signals, including when these fake-boundary tests run on NT.
SIGKILL, SIGTERM = 9, 15


class Input:
    def __init__(self):
        self.chunks = []
        self.closed = asyncio.Event()

    def write(self, data):
        assert not self.closed.is_set()
        self.chunks.append(data)

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.closed.set()


class Owner:
    """Only the documented pipe/wait/close shape, deliberately no communicate."""

    def __init__(self, *, code=0, output=b'{"ok":true}', logs=b''):
        self.stdin = Input()
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdout.feed_data(output)
        self.stderr.feed_data(logs)
        self.result = windows_jobs.ProcessResult(code, 0, 0)
        self.tree_empty = self.io_finalized = False
        self.wait_entered, self.close_entered = asyncio.Event(), asyncio.Event()
        self.wait_release, self.close_release = asyncio.Event(), asyncio.Event()
        self.wait_release.set()
        self.close_release.set()
        self.close_calls = 0
        self.close_error = None

    def finish(self):
        self.stdin.close()
        self.tree_empty = self.io_finalized = True
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        return self.result

    async def wait(self):
        self.wait_entered.set()
        try:
            await self.stdin.closed.wait()
            await self.wait_release.wait()
            return self.finish()
        except asyncio.CancelledError:
            await self.close()
            raise

    async def close(self):
        self.close_calls += 1
        self.close_entered.set()
        await self.close_release.wait()
        if self.close_error is not None:
            raise self.close_error
        return self.finish()


@pytest.fixture
async def windows(monkeypatch):
    # Patch the module's OS view, not global os.name/pathlib or the event loop.
    monkeypatch.setattr(probe_process, 'os', SimpleNamespace(
        name='nt', fspath=os.fspath, getcwd=lambda: 'C:\\host cwd',
        environ={'PARENT': 'inherited'}))
    monkeypatch.setattr(probe_process, 'sys', SimpleNamespace(
        _base_executable='C:\\trusted host\\python.exe'))
    calls = []
    owner = Owner()

    async def start(argv, **kwargs):
        calls.append((argv, kwargs))
        return owner

    async def forbidden(*args, **kwargs):
        pytest.fail('Native probes must not fall back to asyncio subprocesses')

    monkeypatch.setattr(windows_jobs, 'WindowsOwnedProcess', SimpleNamespace(start=start))
    monkeypatch.setattr(probe_process.asyncio, 'create_subprocess_exec', forbidden)
    return owner, calls


async def native_spawn(**kwargs):
    return await probe_process.spawn(
        'C:\\payload venv\\python.exe', '-I', 'probe.py',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=kwargs.pop('stderr', asyncio.subprocess.PIPE), **kwargs)


async def test_windows_settings_exchange_uses_owner_payload_code_and_exact_inputs(windows):
    owner, calls = windows
    owner.result = windows_jobs.ProcessResult(7, 0, 0)
    env = {'AMPLIFIER_WEB_HOME': 'C:\\private\\試験', 'KEY': 'synthetic'}
    process = await native_spawn(cwd='C:\\workspace with spaces\\試験', env=env)
    assert calls == [(
        ['C:\\payload venv\\python.exe', '-I', 'probe.py'],
        {'cwd': 'C:\\workspace with spaces\\試験', 'env': env,
         'trusted_python': 'C:\\trusted host\\python.exe'})]
    assert calls[0][1]['env'] is not env
    assert process.returncode is None
    logs = asyncio.create_task(stderr_tail(process.stderr))
    payload = '{"action":"providers.models","label":"試験"}'.encode()
    output, stderr = await asyncio.gather(communicate(process, payload), logs)
    assert output == b'{"ok":true}' and stderr == ''
    assert b''.join(owner.stdin.chunks) == payload and owner.stdin.closed.is_set()
    assert owner.tree_empty and owner.io_finalized
    assert process.returncode == 7  # Never the successful guard's zero.
    await probe_process.stop(process, logs)
    assert owner.close_calls == 1


@pytest.mark.parametrize('stderr', [asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL])
async def test_windows_ordinary_communicate_drains_both_pipes(windows, stderr):
    owner, calls = windows
    owner.stderr.feed_data(b'log without newlines' * 10000)
    process = await native_spawn(stderr=stderr)
    output, logs = await process.communicate(b'payload')
    assert output == b'{"ok":true}'
    assert logs == (None if stderr == asyncio.subprocess.DEVNULL
                    else b'log without newlines' * 10000)
    assert process.stderr is (None if stderr == asyncio.subprocess.DEVNULL else owner.stderr)
    assert process.returncode == 0
    assert owner.tree_empty and owner.io_finalized
    assert calls[0][1]['cwd'] == 'C:\\host cwd'
    assert calls[0][1]['env'] == {'PARENT': 'inherited'}
    await probe_process.stop(process)


@pytest.fixture
async def settings_probe(windows, monkeypatch, tmp_path):
    from amplifier_web import provider_health, setup

    owner, _ = windows
    configured = SimpleNamespace(configured_connections=[{
        'id': 'one', 'module': 'provider-fixture',
        'config': {'api_key': 'synthetic-private', 'base_url': 'https://fixture.invalid'},
    }], module_sources={})
    manager = SimpleNamespace(
        home=tmp_path / 'home', probe_command=['C:\\payload venv\\python.exe', 'probe.py'],
        config=lambda _: configured)
    manager.probe_snapshot = lambda *args: setup.SetupManager.probe_snapshot(manager, *args)
    records = []
    monkeypatch.setattr(setup, 'os', SimpleNamespace(environ={'BOUND': 'fixture'}))
    monkeypatch.setattr(provider_health, 'record',
                        lambda *args, **kwargs: records.append((args, kwargs)))
    owner.stdout = asyncio.StreamReader()
    owner.stdout.feed_data(json.dumps({
        'info': {'id': 'fixture'}, 'configSchema': {'fields': []},
        'models': [{'id': 'model-fixture'}], 'modelsProvenance': 'provider-api',
        'test': {'reachable': True},
    }).encode())

    async def probe(action='providers.schema'):
        return await setup.SetupManager.probe(
            manager, action, {'id': 'one', 'module': 'provider-fixture'}, str(tmp_path))

    return probe, records


@pytest.mark.parametrize('action', ['providers.schema', 'providers.models', 'providers.test'])
async def test_windows_settings_probe_uses_shared_owner_for_normal_onboarding(
        windows, settings_probe, action):
    owner, calls = windows
    probe, records = settings_probe
    result = await probe(action)
    request = json.loads(b''.join(owner.stdin.chunks))
    assert request['action'] == action and request['module'] == 'provider-fixture'
    assert request['config'] == (
        {'base_url': 'https://fixture.invalid'} if action == 'providers.schema'
        else {'api_key': 'synthetic-private', 'base_url': 'https://fixture.invalid'})
    assert result['providerMetadata']['configSchema'] == {'fields': []}
    if action == 'providers.models':
        assert result['models'] == [{'id': 'model-fixture'}]
    if action == 'providers.test':
        assert result['test']['reachable'] is True
    assert len(records) == (0 if action == 'providers.schema' else 1)
    assert calls[0][1]['env']['BOUND'] == 'fixture'
    assert owner.close_calls == 1 and owner.tree_empty and owner.io_finalized


async def test_windows_settings_cancellation_preserves_cleanup_failure_owner(
        windows, settings_probe):
    owner, _ = windows
    probe, records = settings_probe
    owner.wait_release.clear()
    failure = owner.close_error = windows_jobs.CleanupError(owner)
    task = asyncio.create_task(probe())
    await owner.wait_entered.wait()
    task.cancel()
    with pytest.raises(windows_jobs.CleanupError) as error:
        await task
    assert error.value is failure and error.value.owner is owner
    assert owner.stdin.closed.is_set() and not owner.io_finalized
    assert records == []


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
async def test_windows_communicate_overflow_reclaims_owner(windows, monkeypatch, stream):
    from amplifier_web import probe_diagnostics

    owner, _ = windows
    monkeypatch.setattr(probe_diagnostics, 'MAX_PROBE_BYTES', 16)
    getattr(owner, stream).feed_data(b'x' * 17)
    owner.wait_release.clear()
    process = await native_spawn()
    with pytest.raises(ValueError, match='output exceeded'):
        await process.communicate(b'input')
    await probe_process.stop(process)
    assert owner.stdin.closed.is_set() and owner.tree_empty and owner.io_finalized


async def test_windows_wait_does_not_report_success_before_owner_completion(windows):
    owner, _ = windows
    owner.wait_release.clear()
    process = await native_spawn()
    process.stdin.close()
    waiter = asyncio.create_task(process.wait())
    await owner.wait_entered.wait()
    assert not waiter.done() and process.returncode is None
    assert not owner.tree_empty and not owner.io_finalized
    owner.wait_release.set()
    assert await waiter == 0
    await probe_process.stop(process, grace=3)
    assert owner.close_calls == 1  # Even after payload code zero.


@pytest.mark.parametrize('result', [
    windows_jobs.ProcessResult(None, 0, 0),
    windows_jobs.ProcessResult(0, 0, 0, output_discarded=True),
])
async def test_windows_wait_rejects_missing_or_discarded_payload_result(windows, result):
    owner, _ = windows
    owner.result = result
    process = await native_spawn()
    process.stdin.close()
    with pytest.raises(RuntimeError, match='complete payload result'):
        await process.wait()
    assert process.returncode is None
    await probe_process.stop(process)


@pytest.mark.parametrize('cleanup_failure', [False, True])
async def test_windows_spawn_failure_preserves_owner_exception_without_fallback(
        windows, monkeypatch, cleanup_failure):
    owner, _ = windows
    failure = (windows_jobs.CleanupError(owner) if cleanup_failure
               else FileNotFoundError('synthetic spawn failure'))

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(windows_jobs, 'WindowsOwnedProcess', SimpleNamespace(start=fail))
    with pytest.raises(type(failure)) as error:
        await native_spawn()
    assert error.value is failure
    if cleanup_failure:
        assert error.value.owner is owner


async def test_windows_creation_cancellation_reaches_public_owner_and_settles(
        windows, monkeypatch):
    owner, _ = windows
    entered, cancelled = asyncio.Event(), asyncio.Event()
    owner.close_release.clear()

    async def start(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            # Model the public start contract's cancellation-safe finalizer.
            await probe_process._settle(asyncio.create_task(owner.close()))
            raise

    monkeypatch.setattr(windows_jobs, 'WindowsOwnedProcess', SimpleNamespace(start=start))
    task = asyncio.create_task(native_spawn())
    await entered.wait()
    task.cancel()
    await cancelled.wait()
    await owner.close_entered.wait()
    for _ in range(2):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    owner.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.close_calls == 1 and owner.tree_empty and owner.io_finalized


async def test_windows_exchange_cancellation_closes_stdin_and_owned_tree(windows):
    owner, _ = windows
    owner.wait_release.clear()
    process = await native_spawn()
    task = asyncio.create_task(communicate(process, b'payload'))
    await owner.wait_entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await probe_process.stop(process)
    assert owner.stdin.closed.is_set() and owner.tree_empty and owner.io_finalized


async def test_windows_exchange_cancellation_does_not_hide_cleanup_failure(windows):
    owner, _ = windows
    owner.wait_release.clear()
    failure = owner.close_error = windows_jobs.CleanupError(owner)
    process = await native_spawn()
    task = asyncio.create_task(communicate(process, b'payload'))
    await owner.wait_entered.wait()
    task.cancel()
    with pytest.raises(windows_jobs.CleanupError) as error:
        await task
    assert error.value is failure and error.value.owner is owner
    assert owner.stdin.closed.is_set() and not owner.io_finalized
    with pytest.raises(windows_jobs.CleanupError):
        await probe_process.stop(process)


async def test_windows_repeated_stop_cancellation_waits_for_owner_and_readers(windows):
    owner, _ = windows
    owner.close_release.clear()
    process = await native_spawn(stderr=asyncio.subprocess.DEVNULL)
    reader = asyncio.create_task(process.stdout.read())
    task = asyncio.create_task(probe_process.stop(process, reader))
    await owner.close_entered.wait()
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not owner.io_finalized
    owner.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.tree_empty and owner.io_finalized
    assert reader.done() and not reader.cancelled()
    assert process._discard_task.done()


async def test_windows_cleanup_failure_retains_owner_and_settles_blocked_readers(windows):
    owner, _ = windows
    failure = owner.close_error = windows_jobs.CleanupError(owner)
    process = await native_spawn(stderr=asyncio.subprocess.DEVNULL)
    reader = asyncio.create_task(process.stdout.read())
    with pytest.raises(windows_jobs.CleanupError) as error:
        await probe_process.stop(process, reader)
    assert error.value is failure and error.value.owner is owner
    assert not owner.tree_empty and not owner.io_finalized
    assert reader.done() and process._discard_task.done()
    assert process.returncode is None
    # A second close must not invent success or abandon the retained owner.
    with pytest.raises(windows_jobs.CleanupError) as repeated:
        await probe_process.stop(process)
    assert repeated.value is failure


@pytest.mark.parametrize('kwargs', [
    {'start_new_session': True}, {'limit': 1024}, {'executable': 'other.exe'},
])
async def test_windows_unsupported_subprocess_flags_refuse_before_admission(windows, kwargs):
    _, calls = windows
    with pytest.raises(ValueError, match='Native probes require'):
        await native_spawn(**kwargs)
    assert calls == []


async def test_windows_inherited_stdin_and_unowned_cleanup_are_refused(windows):
    _, calls = windows
    with pytest.raises(ValueError, match='Native probes require'):
        await probe_process.spawn('probe.exe', stdout=asyncio.subprocess.PIPE,
                                  stderr=asyncio.subprocess.PIPE)
    with pytest.raises(RuntimeError, match='Windows Job owner'):
        await probe_process.stop(SimpleNamespace(returncode=None))
    assert calls == []


@pytest.fixture
def posix(monkeypatch):
    signals = []
    monkeypatch.setattr(probe_process, 'os', SimpleNamespace(
        name='posix', killpg=lambda pid, sig: signals.append((pid, sig))))
    monkeypatch.setattr(probe_process, 'signal', SimpleNamespace(SIGTERM=SIGTERM, SIGKILL=SIGKILL))
    return signals


async def test_posix_spawn_preserves_subprocess_object_and_kwargs(posix, monkeypatch):
    process = SimpleNamespace(communicate=object())
    calls = []

    async def start(*command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(probe_process.asyncio, 'create_subprocess_exec', start)
    assert await probe_process.spawn('probe', stdin=-1, stdout=-1, env={'A': 'B'}) is process
    assert calls == [(('probe',), {
        'stdin': -1, 'stdout': -1, 'env': {'A': 'B'}, 'start_new_session': True})]
    assert posix == []


@pytest.mark.parametrize('cancellations', [1, 3])
async def test_posix_creation_cancellation_still_shields_spawn_and_finalizer(
        posix, monkeypatch, cancellations):
    pending = asyncio.get_running_loop().create_future()
    entered, waited, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def start(*command, **kwargs):
        entered.set()
        return await pending

    async def wait():
        waited.set()
        await release.wait()
        process.returncode = -SIGKILL

    process = SimpleNamespace(pid=12345, returncode=None, wait=wait)
    async def communicate():
        await wait()
        return b'', b''
    process.communicate = communicate
    monkeypatch.setattr(probe_process.asyncio, 'create_subprocess_exec', start)
    task = asyncio.create_task(probe_process.spawn('probe'))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not pending.cancelled()
    pending.set_result(process)
    await waited.wait()
    for _ in range(cancellations):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert posix == [(12345, SIGKILL)]
    assert process.returncode == -SIGKILL


async def test_posix_cancelled_spawn_oserror_keeps_cancellation(posix, monkeypatch):
    pending = asyncio.get_running_loop().create_future()
    entered = asyncio.Event()

    async def start(*args, **kwargs):
        entered.set()
        return await pending

    monkeypatch.setattr(probe_process.asyncio, 'create_subprocess_exec', start)
    task = asyncio.create_task(probe_process.spawn('missing'))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    pending.set_exception(FileNotFoundError('synthetic'))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert posix == []


async def test_posix_stop_keeps_group_grace_escalation(posix, monkeypatch):
    exited = asyncio.Event()
    process = SimpleNamespace(pid=12345, returncode=None)

    def send(pid, sig):
        posix.append((pid, sig))
        if sig == SIGKILL:
            exited.set()

    async def wait():
        await exited.wait()
        process.returncode = -SIGKILL

    process.wait = wait
    monkeypatch.setattr(probe_process.os, 'killpg', send)
    await probe_process.stop(process, grace=.001)
    assert posix == [(12345, SIGTERM), (12345, SIGKILL)]
    assert process.returncode == -SIGKILL
