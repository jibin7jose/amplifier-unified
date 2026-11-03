"""Real spawned-process boundary tests; all files are synthetic and test owned."""
import asyncio
import errno
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from amplifier_web.history_projection_process import ProcessProjector


class FakeModel:
    def __init__(self, home):
        self.home = Path(home)

    def project(self, inputs, *, check_budget):
        with (self.home / 'calls').open('a') as stream:
            stream.write(json.dumps({'pid': os.getpid(), 'value': inputs.get('value')}) + '\n')
        if inputs.get('die'):
            os._exit(23)
        if inputs.get('noisy'):
            print('unexpected output' * 400, flush=True)
        if inputs.get('memory'):
            from amplifier_web.history_projection_worker import MemoryLimit
            raise MemoryLimit()
        if inputs.get('allocate'):
            bytearray(1024 * 1024 * 1024)
        time.sleep(inputs.get('sleep', 0))
        check_budget()
        if inputs.get('retry'):
            return {'status': 'retry', 'reason': inputs['retry']}
        return {'status': 'complete', 'tree': {'value': inputs.get('value'),
                'payload': 'x' * inputs.get('output', 0)},
                'paths': inputs.get('paths', []), 'revisions': inputs.get('revisions', []), 'diagnostics': {'pid': os.getpid()},
                'delta': inputs.get('delta')}


def projector(tmp_path, **kwargs):
    return ProcessProjector(tmp_path, _command=(sys.executable, str(Path(__file__).resolve()), 'worker'), **kwargs)


async def called(home, count=1):
    for _ in range(300):
        path = home / 'calls'
        if path.exists():
            lines = path.read_text().splitlines()
            if len(lines) >= count:
                return [json.loads(line) for line in lines]
        await asyncio.sleep(.01)
    raise AssertionError('Worker never reached requested phase')


async def test_lazy_spawn_result_reuse_digest_and_cleanup(tmp_path):
    owner = projector(tmp_path)
    assert owner._process is None and not list(tmp_path.iterdir())
    try:
        one, two = await asyncio.gather(
            owner.project({'value': 1, 'sleep': .15, 'delta': {'revision': 3}}, key='same'),
            owner.project({'delta': {'revision': 3}, 'sleep': .15, 'value': 1}, key='same'))
        assert one == two and one['delta'] == {'revision': 3}
        assert len(await called(tmp_path)) == 1
        three = await owner.project({'value': 2}, key='same')
        assert three['tree']['value'] == 2
        assert three['diagnostics']['pid'] == one['diagnostics']['pid'] != os.getpid()
        assert owner._input_bytes == 0
        assert not list(owner._root.rglob('*.input'))
        assert not list(owner._root.rglob('*.output'))
    finally:
        root = owner._root
        await owner.close()
    assert not root.exists()
    assert await owner.project({}, key='closed') == {'status': 'retry', 'reason': 'closed'}


async def test_cancelled_waiter_does_not_cancel_shared_projection(tmp_path):
    owner = projector(tmp_path)
    try:
        first = asyncio.create_task(owner.project({'value': 1, 'sleep': .3}, key='same'))
        await called(tmp_path)
        second = asyncio.create_task(owner.project({'sleep': .3, 'value': 1}, key='same'))
        await asyncio.sleep(.02)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second)['tree']['value'] == 1
        assert len(await called(tmp_path)) == 1
    finally:
        await owner.close()


async def test_warm_read_does_not_schedule_each_artifact_cleanup_separately(tmp_path, monkeypatch):
    from amplifier_web import history_projection_process as transport
    owner = projector(tmp_path)
    try:
        await owner.project({'value': 'warm'}, key='warm')
        calls = []
        blocking = transport._blocking
        async def counted(call, *args, **kwargs):
            calls.append(call)
            return await blocking(call, *args, **kwargs)
        monkeypatch.setattr(transport, '_blocking', counted)
        result = await owner.project({'value': 'next'}, key='next')
        assert result['tree']['value'] == 'next'
        # Encode, move into the existing child, consume result plus cleanup.
        # Each extra handoff pays busy-host scheduling delay even for tiny I/O.
        assert len(calls) <= 3
        assert owner._input_bytes == 0
        assert not list(owner._root.rglob('*.input'))
        assert not list(owner._root.rglob('*.output'))
    finally:
        await owner.close()


async def test_close_settles_result_cleanup_before_releasing_spool(tmp_path, monkeypatch):
    from amplifier_web import history_projection_process as transport
    owner = projector(tmp_path)
    entered, release = threading.Event(), threading.Event()
    remove = transport._remove_paths
    def held(paths):
        entered.set()
        assert release.wait(5)
        remove(paths)
    monkeypatch.setattr(transport, '_remove_paths', held)
    waiter = asyncio.create_task(owner.project({'value': 'held'}, key='held'))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.001)
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(.02)
        assert not closing.done() and owner._input_bytes > 0
        release.set()
        await closing
        assert await waiter == {'status': 'retry', 'reason': 'closed'}
        assert owner._input_bytes == 0 and not owner._root.exists()
    finally:
        release.set()
        await owner.close()


async def test_spool_allocation_failure_recovers_on_next_request(tmp_path, monkeypatch):
    owner = projector(tmp_path)
    make_root = owner._make_root
    attempts = 0

    def fail_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(errno.ENOSPC, 'private storage path', str(tmp_path))
        return make_root()

    monkeypatch.setattr(owner, '_make_root', fail_once)
    try:
        failed = await owner.project({'value': 'private input'}, key='first')
        assert failed['status'] == 'retry' and failed['reason'] == 'worker_failed'
        assert owner._process is None and not list(tmp_path.iterdir())
        recovered = await owner.project({'value': 'recovered'}, key='second')
        assert recovered['status'] == 'complete' and recovered['tree']['value'] == 'recovered'
        assert attempts == 2 and len(await called(tmp_path)) == 1
        facts = failed['failure']
        assert (facts['stage'], facts['exceptionType'], facts['errno']) == ('spool_setup', 'OSError', errno.ENOSPC)
        assert 'private' not in json.dumps(failed) and str(tmp_path) not in json.dumps(failed)
        process, spool = owner._process, owner._root
    finally:
        await owner.close()
    assert process.returncode == 0 and not spool.exists()


@pytest.mark.parametrize('allocation_fails', [False, True])
async def test_cancelled_waiter_and_close_settle_pending_spool_allocation(tmp_path, monkeypatch, allocation_fails):
    owner = projector(tmp_path)
    entered, release = threading.Event(), threading.Event()
    make_root = owner._make_root
    roots = []

    def held_allocation():
        entered.set()
        assert release.wait(5)
        if allocation_fails:
            raise OSError(errno.ENOSPC, 'private allocation error')
        root = make_root()
        roots.append(root)
        return root

    monkeypatch.setattr(owner, '_make_root', held_allocation)
    waiter = asyncio.create_task(owner.project({'value': 'never sent'}, key='held'))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.001)
        allocation = owner._root_task
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(.01)
        assert not closing.done() and owner._root_task is allocation
        release.set()
        await asyncio.wait_for(closing, 5)
        await owner.close()  # completed close remains idempotent
        assert allocation.done() and not owner._tasks and owner._process is None
        assert not list(tmp_path.iterdir()) and all(not root.exists() for root in roots)
        assert await owner.project({}, key='closed') == {'status': 'retry', 'reason': 'closed'}
    finally:
        release.set()
        await owner.close()


async def test_bounded_queue_and_private_artifacts(tmp_path):
    owner = projector(tmp_path, _max_jobs=2)
    try:
        first = asyncio.create_task(owner.project({'sleep': .3}, key='one'))
        await called(tmp_path)
        second = asyncio.create_task(owner.project({'value': 2}, key='two'))
        await asyncio.sleep(.02)
        assert await owner.project({}, key='three') == {'status': 'retry', 'reason': 'queue_full'}
        if os.name == 'posix':
            assert owner._root.stat().st_mode & 0o777 == 0o700
            assert all(p.stat().st_mode & 0o777 == 0o600 for p in owner._root.rglob('*.input'))
        assert all(v['status'] == 'complete' for v in await asyncio.gather(first, second))
    finally:
        await owner.close()


@pytest.mark.parametrize(('options', 'inputs', 'reason'), [
    ({'_input_limit': 128}, {'value': 'x' * 129}, 'input_limit'),
    ({'_spool_limit': 256, '_output_limit': 128}, {'value': 'x' * 129}, 'spool_limit'),
    ({'_output_limit': 128}, {'output': 256}, 'output_limit'),
])
async def test_encoded_limits_leave_no_artifacts(tmp_path, options, inputs, reason):
    owner = projector(tmp_path, **options)
    try:
        assert await owner.project(inputs, key='limit') == {'status': 'retry', 'reason': reason}
        assert owner._input_bytes == 0
        assert not list(owner._root.rglob('*.input'))
        assert not list(owner._root.rglob('*.output'))
    finally:
        await owner.close()


async def test_aggregate_spool_limit_counts_active_and_waiting_inputs(tmp_path):
    owner = projector(tmp_path, _spool_limit=1200, _output_limit=256)
    try:
        active = asyncio.create_task(owner.project({'sleep': .3, 'padding': 'x' * 700}, key='active'))
        await called(tmp_path)
        charged = owner._input_bytes
        assert charged >= 700
        assert await owner.project({'padding': 'y' * 700}, key='other') == {
            'status': 'retry', 'reason': 'spool_limit'}
        assert owner._input_bytes == charged
        assert (await active)['status'] == 'complete'
        assert owner._input_bytes == 0
    finally:
        await owner.close()


@pytest.mark.parametrize(('inputs', 'reason'), [
    ({'die': True}, 'worker_stopped'), ({'sleep': 10}, 'timeout'),
    ({'memory': True}, 'memory_limit'), ({'allocate': True}, 'memory_limit'),
    ({'noisy': True}, 'worker_failed'),
])
async def test_failure_retires_and_next_request_respawns(tmp_path, inputs, reason):
    owner = projector(tmp_path, _timeout=1, _close_timeout=.15)
    try:
        result = await owner.project(inputs, key='failed')
        failure = result.pop('failure', None)
        if reason == 'memory_limit':
            assert failure['stage'] == 'model'
        assert result == {'status': 'retry', 'reason': reason}
        old_pid = (await called(tmp_path))[0]['pid']
        result = await owner.project({'value': 'recovered'}, key='next')
        assert result['status'] == 'complete'
        assert result['diagnostics']['pid'] != old_pid
    finally:
        await owner.close()


async def test_close_stops_blocked_worker_and_drains_queued_reads(tmp_path):
    owner = projector(tmp_path, _close_timeout=.15)
    active = asyncio.create_task(owner.project({'sleep': 20}, key='blocked'))
    await called(tmp_path)
    process = owner._process
    waiting = asyncio.create_task(owner.project({'value': 2}, key='queued'))
    before = time.monotonic()
    await owner.close()
    assert time.monotonic() - before < 2
    assert process.returncode is not None
    assert await active == {'status': 'retry', 'reason': 'closed'}
    assert await waiting == {'status': 'retry', 'reason': 'closed'}
    assert not owner._root.exists()
    await owner.close()


async def test_close_during_timeout_retirement_still_waits_for_owned_child(tmp_path):
    owner = projector(tmp_path, _timeout=.3, _close_timeout=.4)
    task = asyncio.create_task(owner.project({'sleep': 20}, key='blocked'))
    await called(tmp_path)
    process = owner._process
    for _ in range(100):
        if owner._process is None:
            break  # Timeout has detached the child and is waiting to retire it.
        await asyncio.sleep(.01)
    assert owner._process is None and process.returncode is None
    await owner.close()
    try:
        assert process.returncode is not None
        assert (await task)['reason'] == 'closed'
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def test_worker_stdin_eof_stops_even_uncooperative_model(tmp_path):
    owner = projector(tmp_path, _close_timeout=.15)
    task = asyncio.create_task(owner.project({'sleep': 20}, key='blocked'))
    await called(tmp_path)
    process = owner._process
    process.stdin.close()
    await asyncio.wait_for(process.wait(), 2)
    assert (await task)['reason'] == 'worker_stopped'
    await owner.close()


async def test_abrupt_host_death_reclaims_worker_and_queued_spool(tmp_path):
    host = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()),
        'host-death', str(tmp_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    receipt = json.loads(await asyncio.wait_for(host.stdout.readline(), 4))
    assert receipt['pending'] >= 1
    assert await asyncio.wait_for(host.wait(), 2) == 0
    root = Path(receipt['root'])
    for _ in range(300):
        if not root.exists():
            break
        await asyncio.sleep(.01)
    assert not root.exists()


async def test_encoding_and_decoding_are_off_loop_and_retries_are_bounded(tmp_path, monkeypatch):
    owner = projector(tmp_path)
    encode, decode = owner._encode, owner._decode
    loop_thread = threading.get_ident()
    def slow_encode(*args):
        assert threading.get_ident() != loop_thread
        time.sleep(.12)
        return encode(*args)
    def slow_decode(*args):
        assert threading.get_ident() != loop_thread
        time.sleep(.12)
        return decode(*args)
    monkeypatch.setattr(owner, '_encode', slow_encode)
    monkeypatch.setattr(owner, '_decode', slow_decode)
    task = asyncio.create_task(owner.project({'value': 1}, key='one'))
    ticks = 0
    try:
        while not task.done():
            ticks += 1
            await asyncio.sleep(.01)
        assert (await task)['status'] == 'complete' and ticks >= 20
        assert await owner.project({'retry': 'not-a-public-reason'}, key='retry') == {
            'status': 'retry', 'reason': 'worker_failed'}
        assert await owner.project({'retry': 'source_changed'}, key='retry') == {
            'status': 'retry', 'reason': 'source_changed'}
    finally:
        await owner.close()



async def test_output_overflow_preserves_only_bounded_source_receipt_and_retires(tmp_path):
    owner = ProcessProjector(tmp_path, _command=(sys.executable, str(Path(__file__).resolve()), 'worker-small-output'))
    revisions = [['source', [[1, 2], 3, 4, 5]], ['missing', None]]
    try:
        result = await owner.project({'output': 2048, 'paths': ['source', 'missing'], 'revisions': revisions}, key='large')
        assert result == {'status': 'retry', 'reason': 'output_limit', 'revisions': revisions}
        assert owner._process is None
        assert not list(owner._root.rglob('*.output'))
        assert not list(owner._root.rglob('*.writing'))
        assert (await owner.project({'value': 'small'}, key='recovered'))['status'] == 'complete'
        assert len({row['pid'] for row in await called(tmp_path, 2)}) == 2
    finally:
        await owner.close()


@pytest.mark.parametrize('references', [
    {'paths': ['source'], 'revisions': [['different', None]]},
    {'paths': ['source'], 'revisions': [['source', 'not-a-stamp']]},
    {'paths': ['source'] * 2049, 'revisions': [['source', None]] * 2049},
    {'paths': ['x' * 4096] * 100, 'revisions': [['x' * 4096, None]] * 100},
])
async def test_output_overflow_rejects_invalid_or_oversized_receipt(tmp_path, references):
    owner = ProcessProjector(tmp_path, _command=(sys.executable, str(Path(__file__).resolve()), 'worker-small-output'))
    try:
        assert await owner.project({'output': 2048, **references}, key='large') == {'status': 'retry', 'reason': 'output_limit'}
        assert owner._process is None
        assert not list(owner._root.rglob('*.writing'))
    finally:
        await owner.close()

if __name__ == '__main__' and sys.argv[1:2] in (['worker'], ['worker-small-output']):
    import argparse
    from amplifier_web.history_projection_worker import run
    if sys.argv[1] == 'worker-small-output':
        import amplifier_web.history_projection_worker as worker
        worker.OUTPUT_LIMIT = 1024
    parser = argparse.ArgumentParser()
    parser.add_argument('--spool', required=True)
    parser.add_argument('--cache-home', required=True)
    parser.add_argument('--memory-limit', type=int, required=True)
    args = parser.parse_args(sys.argv[2:])
    run(args.spool, args.cache_home, model_factory=FakeModel, memory_limit=args.memory_limit)
elif __name__ == '__main__' and sys.argv[1:2] == ['host-death']:
    async def abrupt_exit():
        home = Path(sys.argv[2])
        owner = projector(home)
        asyncio.create_task(owner.project({'sleep': 20}, key='active'))
        await called(home)
        asyncio.create_task(owner.project({'value': 'pending'}, key='queued'))
        while not list(owner._root.glob('*.input')):
            await asyncio.sleep(.01)
        print(json.dumps({'root': str(owner._root), 'pending': len(list(owner._root.glob('*.input')))}), flush=True)
        os._exit(0)
    asyncio.run(abrupt_exit())
