"""Actual storage probes keep supervision independent of host-loop delivery."""
import asyncio
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from amplifier_web import probe_process, update_retention as retention


@pytest.mark.parametrize(('size', 'admission'), [(1000, False), (196000, False), (196000, True)])
async def test_completed_reference_probe_with_parent_loop_stall(tmp_path, monkeypatch, size, admission):
    home = tmp_path / 'app'
    settings = home / 'config/settings.yaml'
    settings.parent.mkdir(parents=True)
    expected = 'source: synthetic-retained-generation\n' + '#' * size
    settings.write_text(expected)
    parent = asyncio.get_running_loop()
    create = asyncio.create_subprocess_exec
    entered = tmp_path / 'parent-stalled'
    script = ('import runpy,sys,time; from pathlib import Path\n'
              f'while not Path({str(entered)!r}).exists(): time.sleep(.001)\n'
              'sys.argv=["amplifier_web.update_retention","references"]\n'
              'runpy.run_module("amplifier_web.update_retention",run_name="__main__")')

    def stall():
        entered.touch()
        if size == 1000:
            time.sleep(3.2)
        else:
            # CPU-heavy Python work also blocks the app loop. The worker must
            # still drain a pipe-sized response before the unchanged deadline.
            until = time.monotonic() + 3.2
            while time.monotonic() < until:
                pass

    async def start(*args, **kwargs):
        process = await create(sys.executable, '-c', script, **kwargs)
        parent.call_soon_threadsafe(lambda: parent.call_later(.05, stall))
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    async def inspect(manager, *, timeout):
        return await retention._probe('references', {'home': str(home), 'workspaces': []}, timeout=timeout)
    monkeypatch.setattr(retention, 'inspect_references', inspect)
    if admission:
        with pytest.raises(TimeoutError):
            await retention.fresh_references(None, timeout=2)
    else:
        assert await inspect(None, timeout=2) == expected


async def test_cancellation_while_executor_is_queued_never_starts_operation(monkeypatch):
    released, queued = asyncio.Event(), asyncio.Event()
    to_thread = asyncio.to_thread
    started = []

    async def waiting(*args, **kwargs):
        queued.set()
        await released.wait()
        return await to_thread(*args, **kwargs)

    async def operation():
        started.append(True)

    monkeypatch.setattr(asyncio, 'to_thread', waiting)
    task = asyncio.create_task(probe_process.isolated(operation))
    await queued.wait()
    task.cancel()
    await asyncio.sleep(0)
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert started == []


@pytest.mark.parametrize('cleanup_failure', [False, True])
async def test_repeated_cancellation_waits_for_worker_cleanup(cleanup_failure):
    from amplifier_web.windows_jobs import CleanupError
    parent = asyncio.get_running_loop()
    entered, cleaning = asyncio.Event(), asyncio.Event()
    release, settled = threading.Event(), threading.Event()
    owner = object()

    async def operation():
        parent.call_soon_threadsafe(entered.set)
        try:
            await asyncio.sleep(60)
        finally:
            parent.call_soon_threadsafe(cleaning.set)
            while not release.is_set():
                await asyncio.sleep(.001)
            settled.set()
            if cleanup_failure:
                raise CleanupError(owner)

    task = asyncio.create_task(probe_process.isolated(operation))
    await entered.wait()
    task.cancel()
    await cleaning.wait()
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    release.set()
    with pytest.raises(CleanupError if cleanup_failure else asyncio.CancelledError) as error:
        await task
    assert settled.is_set()
    if cleanup_failure:
        assert error.value.owner is owner


@pytest.mark.parametrize('output_size', [0, 512 * 1024])
async def test_cancel_during_storage_probe_creation_reaps_process(tmp_path, monkeypatch, output_size):
    parent = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    create = asyncio.create_subprocess_exec
    spawned = []
    writing = tmp_path / 'writing'
    script = ('import sys,time; from pathlib import Path; '
              f'Path({str(writing)!r}).touch(); '
              f'sys.stdout.write("x" * {output_size}); sys.stdout.flush(); time.sleep(60)')

    async def start(*args, **kwargs):
        process = await create(sys.executable, '-c', script, **kwargs)
        spawned.append(process)
        while not writing.exists():
            await asyncio.sleep(.001)
        await asyncio.sleep(.1)  # Let the large writer reach pipe backpressure.
        parent.call_soon_threadsafe(entered.set)
        while not release.is_set():
            await asyncio.sleep(.001)
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    task = asyncio.create_task(retention._probe('references', {}, timeout=2))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert spawned[0].returncode is not None


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group containment')
async def test_timeout_reaps_descendant_after_root_exit(tmp_path, monkeypatch):
    marker = tmp_path / 'child-pid'
    script = ('import subprocess,sys; from pathlib import Path; '
              'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
              f'Path({str(marker)!r}).write_text(str(p.pid))')
    create = asyncio.create_subprocess_exec
    spawned = []

    async def start(*args, **kwargs):
        process = await create(sys.executable, '-c', script, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    with pytest.raises(TimeoutError):
        await retention._probe('references', {}, timeout=.5)
    assert spawned[0].returncode == 0
    # EOF after stop proves no inherited writer remains. Linux may briefly
    # retain an orphan zombie until the container init reaps it.
    child = int(marker.read_text())
    if Path('/proc').is_dir():
        stat = Path(f'/proc/{child}/stat')
        assert not stat.exists() or stat.read_text().split(') ', 1)[1].startswith('Z ')


@pytest.mark.parametrize(('script', 'reason'), [
    ('raise SystemExit(7)', 'process-failed'),
    ('print("private-unparseable-input")', 'invalid-response'),
])
async def test_probe_failure_is_bounded_and_does_not_echo_output(monkeypatch, script, reason):
    create = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await create(sys.executable, '-c', script, **kwargs)

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    with pytest.raises(retention.ProbeFailure) as error:
        await retention._probe('references', {}, timeout=2)
    assert error.value.reason == reason
    assert str(error.value) == 'Storage inspection could not be verified'
