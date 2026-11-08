"""Cancellation must retain ownership while a discovery process is starting."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import draft_defaults

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux real-process lifetime observation')


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1][0] != 'Z'
    except FileNotFoundError:
        return False


async def wait_for_file(path):
    async with asyncio.timeout(3):
        while not path.exists():
            await asyncio.sleep(.01)
    return json.loads(path.read_text())


@pytest.mark.parametrize('owner', ['defaults', 'provider', 'validator'])
@pytest.mark.parametrize('cancellations', [1, 3])
async def test_cancel_during_pipe_connection_closes_discovery_descendants(monkeypatch, tmp_path, owner, cancellations):
    marker = tmp_path / 'owned-pids.json'
    code = ('import json,os,pathlib,subprocess,sys,time\n'
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
        'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
        f'pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid]))\n'
        'time.sleep(60)\n')
    monkeypatch.setattr('amplifier_web.runtime.RuntimeManager._command',
        lambda self, **kwargs: [sys.executable, '-c', code, 'unused'])
    loop = asyncio.get_running_loop()
    connect = loop.connect_write_pipe
    release = asyncio.Event()
    async def delayed(*args, **kwargs):
        await release.wait()
        return await connect(*args, **kwargs)
    monkeypatch.setattr(loop, 'connect_write_pipe', delayed)
    if owner == 'defaults':
        request = draft_defaults.resolve_defaults(tmp_path, str(tmp_path), bundle='synthetic')
    elif owner == 'provider':
        from amplifier_web.setup import SetupManager
        manager = SetupManager(tmp_path)
        monkeypatch.setattr(manager, 'config', lambda _: SimpleNamespace(configured_connections=[], module_sources={}))
        request = manager.probe('providers.schema', {'module': 'provider-fixture'}, str(tmp_path))
    else:
        from amplifier_web.registry import RegistryManager
        request = RegistryManager(tmp_path)._validate_row({'id': 'tool-fixture', 'module': 'tool-fixture', 'section': 'tools'}, {})
    task = asyncio.create_task(request)
    pids = []
    try:
        pids = await wait_for_file(marker)
        assert all(alive(pid) for pid in pids)
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(.01)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        await asyncio.sleep(.05)
        assert not any(alive(pid) for pid in pids), 'Cancellation left a discovery descendant running'
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Synthetic group only; do not let the deliberately failing baseline leak.
        if pids and any(alive(pid) and os.getpgid(pid) == pids[0] for pid in pids):
            os.killpg(pids[0], signal.SIGKILL)
        async with asyncio.timeout(2):
            while any(alive(pid) for pid in pids):
                await asyncio.sleep(.01)


async def test_repeated_cancellation_waits_for_process_and_owned_readers(monkeypatch):
    from amplifier_web.probe_process import spawn, stop
    process = await spawn(sys.executable, '-c', 'import time; time.sleep(60)',
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    entered, release = asyncio.Event(), asyncio.Event()
    wait = process.wait
    async def delayed_wait():
        entered.set()
        await release.wait()
        return await wait()
    monkeypatch.setattr(process, 'wait', delayed_wait)
    reader = asyncio.create_task(release.wait())
    task = asyncio.create_task(stop(process, reader))
    try:
        await entered.wait()
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None and reader.done() and not reader.cancelled()
    finally:
        release.set()
        await asyncio.gather(task, reader, return_exceptions=True)


async def test_cancelled_spawn_failure_keeps_cancellation(monkeypatch):
    from amplifier_web import probe_process
    pending = asyncio.get_running_loop().create_future()
    started = asyncio.Event()
    async def failing(*args, **kwargs):
        started.set()
        return await pending
    monkeypatch.setattr(probe_process.asyncio, 'create_subprocess_exec', failing)
    task = asyncio.create_task(probe_process.spawn('/missing'))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    pending.set_exception(FileNotFoundError('synthetic missing executable'))
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_validator_grace_escalates_when_term_is_ignored(tmp_path):
    from amplifier_web.probe_process import spawn, stop
    marker = tmp_path / 'ready'
    code = ('import pathlib,signal,time\n'
        'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
        f'pathlib.Path({str(marker)!r}).write_text("true")\n'
        'time.sleep(60)\n')
    process = await spawn(sys.executable, '-c', code,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await wait_for_file(marker)
        await asyncio.wait_for(stop(process, grace=.02), 2)
        assert process.returncode == -signal.SIGKILL
    finally:
        if process.returncode is None:
            await stop(process)


async def test_defaults_payload_environment_output_and_error_contract(monkeypatch, tmp_path):
    code = ('import json,os,sys\nrequest=json.load(sys.stdin)\n'
        'assert os.environ["AMPLIFIER_WEB_HOME"] == request["home"]\n'
        'assert os.getcwd() == request["workspace"]\n'
        'print(json.dumps({"request":request}))\n')
    monkeypatch.setattr('amplifier_web.runtime.RuntimeManager._command',
        lambda self, **kwargs: [sys.executable, '-c', code, 'unused'])
    result = await draft_defaults.resolve_defaults(tmp_path, str(tmp_path), bundle='synthetic',
        global_only=True, session_id='exact', refresh=True, replacement={'model': 'example'})
    assert result == {'bundle': 'synthetic', 'globalOnly': True, 'request': {
        'home': str(tmp_path), 'workspace': str(tmp_path), 'bundle': 'synthetic',
        'globalOnly': True, 'sessionId': 'exact', 'catalog': False, 'refresh': True,
        'replacement': {'model': 'example'}, 'desiredBundle': False}}
    monkeypatch.setattr('amplifier_web.runtime.RuntimeManager._command',
        lambda self, **kwargs: [sys.executable, '-c', 'print(\'{"error":"synthetic"}\')', 'unused'])
    with pytest.raises(ValueError, match='Draft default resolution failed'):
        await draft_defaults.resolve_defaults(tmp_path, str(tmp_path), bundle='synthetic')
