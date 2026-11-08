"""Bounded disposable process transport for selected-history display reads.

The caller supplies detached JSON inputs and retains all command/canonical-write
authority. A cancelled waiter does not cancel a projection shared by other reads.
Limits bound encoded artifacts, not the expanded Python representation of JSON.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import uuid

from .history_projection_worker import (
    DESCRIPTOR_LIMIT, INPUT_LIMIT, MEMORY_LIMIT, OUTPUT_LIMIT, source_receipt,
)

from .history_projection_failure import capture_failure, sanitize_failure

REASONS = frozenset({'queue_full', 'input_limit', 'output_limit', 'spool_limit',
    'memory_limit', 'timeout', 'worker_stopped', 'source_changed', 'closed', 'worker_failed'})
_ENCODE_BATCH_BYTES = 64 * 1024


async def _blocking(call, *args, **kwargs):
    """Finish owned file operations before cancellation can reclaim their paths."""
    task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def retry(reason, failure=None):
    result = {'status': 'retry', 'reason': reason if reason in REASONS else 'worker_failed'}
    facts = sanitize_failure(failure)
    if facts is not None:
        result['failure'] = facts
    return result


class _Limit(Exception):
    pass


def _remove_paths(paths):
    for path in paths:
        path.unlink(missing_ok=True)


class ProcessProjector:
    """One lazy interpreter, eight admitted requests, 256 MiB total spool.

    64 MiB is reserved for the sole active result; pending inputs share 192 MiB.
    Admission counts duplicate waiters before their input digests are known.
    Private constructor overrides support lifecycle tests, never request input.
    """
    def __init__(self, cache_home, *, _command=None, _timeout=120, _close_timeout=3,
                 _input_limit=INPUT_LIMIT, _output_limit=OUTPUT_LIMIT,
                 _spool_limit=256 * 1024 * 1024, _max_jobs=8):
        self.cache_home = Path(cache_home)
        self._command = tuple(_command) if _command else (sys.executable, '-m',
            'amplifier_web.history_projection_worker')
        self._timeout, self._close_timeout = _timeout, _close_timeout
        self._input_limit, self._output_limit = _input_limit, _output_limit
        self._input_budget = _spool_limit - _output_limit
        self._max_jobs = _max_jobs
        self._closed = False
        self._stop = threading.Event()
        self._bytes_lock = threading.Lock()
        self._input_bytes = 0
        self._root_task = None
        self._root = None
        self._process = None
        self._worker_home = None
        self._serial = asyncio.Lock()
        self._tasks = set()
        self._shared = {}
        self._closing = None
        self._active = None

    def _make_root(self):
        self.cache_home.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix='projection-spool-', dir=self.cache_home))

    async def _home(self):
        if self._root_task is None:
            self._root_task = asyncio.create_task(asyncio.to_thread(self._make_root))
        task = self._root_task
        try:
            self._root = await asyncio.shield(task)
        except Exception:
            # A full filesystem must not poison every later explicit read.
            # Another waiter may already have started a replacement allocation.
            # Cancellation leaves the still-running thread owned by close().
            if self._root_task is task:
                self._root_task = None
            raise
        return self._root

    def _encode(self, path, inputs):
        size, charged, digest = 0, 0, hashlib.sha256()
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                def write(data):
                    nonlocal charged
                    if self._stop.is_set():
                        raise _Limit('closed')
                    with self._bytes_lock:
                        if self._input_bytes + len(data) > self._input_budget:
                            raise _Limit('spool_limit')
                        self._input_bytes += len(data)
                        charged += len(data)
                    stream.write(data)
                    digest.update(data)

                # iterencode emits many tiny punctuation/scalar fragments. Keep
                # only one bounded batch; reserve spool bytes before each write.
                pending = bytearray()
                encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(',', ':'))
                for chunk in encoder.iterencode(inputs):
                    if self._stop.is_set():
                        raise _Limit('closed')
                    data = chunk.encode('utf-8')
                    if size + len(data) > self._input_limit:
                        raise _Limit('input_limit')
                    size += len(data)
                    if len(pending) + len(data) > _ENCODE_BATCH_BYTES and pending:
                        write(pending)
                        pending.clear()
                    if len(data) >= _ENCODE_BATCH_BYTES:
                        view = memoryview(data)
                        for offset in range(0, len(data), _ENCODE_BATCH_BYTES):
                            write(view[offset:offset + _ENCODE_BATCH_BYTES])
                    else:
                        pending.extend(data)
                if pending:
                    write(pending)
            return size, digest.hexdigest()
        except BaseException:
            path.unlink(missing_ok=True)
            # Pending bytes were never reserved; partial writes were reserved.
            self._release(charged)
            raise

    def _release(self, size):
        with self._bytes_lock:
            self._input_bytes -= size

    async def project(self, inputs, *, key):
        if self._closed:
            return retry('closed')
        if not isinstance(inputs, dict) or not isinstance(key, str) or len(key) > 4096:
            return retry('worker_failed')
        if len(self._tasks) >= self._max_jobs:
            return retry('queue_full')
        task = asyncio.create_task(self._project(inputs, key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        # Shared results are detached and must be treated as immutable by callers.
        # The read-model host still verifies authority before publication.
        return await asyncio.shield(task)

    async def _project(self, inputs, key):
        path = None
        cleanup = []
        size = 0
        token = None
        owner = False
        try:
            try:
                root = await self._home()
            except OSError as exc:
                return retry('worker_failed', capture_failure('spool_setup', exc))
            identity = uuid.uuid4().hex
            path = root / (identity + '.input')
            cleanup.append(path)
            size, digest = await _blocking(self._encode, path, inputs)
            if self._closed:
                return retry('closed')
            token = (key, digest)
            shared = self._shared.get(token)
            if shared is not None:
                await _blocking(path.unlink, missing_ok=True)
                cleanup.clear()
                path = None
                self._release(size); size = 0
                return await asyncio.shield(shared)
            owner = True
            self._shared[token] = asyncio.current_task()
            async with self._serial:
                if self._closed:
                    return retry('closed')
                try:
                    self._active = asyncio.current_task()
                    return await asyncio.wait_for(self._roundtrip(path, identity, digest, cleanup), self._timeout)
                except TimeoutError:
                    await self._retire()
                    return retry('timeout')
                except (EOFError, BrokenPipeError, ConnectionError, asyncio.IncompleteReadError):
                    await self._retire()
                    return retry('worker_stopped')
                except (OSError, ValueError, TypeError, KeyError):
                    await self._retire()
                    return retry('worker_failed')
                finally:
                    self._active = None
        except asyncio.CancelledError:
            await self._retire()
            if self._closed:
                return retry('closed')
            raise
        except _Limit as exc:
            return retry(str(exc))
        except (OSError, ValueError, TypeError, RecursionError, MemoryError):
            return retry('worker_failed')
        finally:
            if owner:
                self._shared.pop(token, None)
            if cleanup:
                await _blocking(_remove_paths, tuple(cleanup))
            self._release(size)

    async def _message(self):
        line = await self._process.stdout.readline()
        if not line:
            raise EOFError()
        if len(line) > DESCRIPTOR_LIMIT or not line.endswith(b'\n'):
            raise ValueError('Invalid worker descriptor')
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError('Invalid worker descriptor')
        return value

    async def _start(self):
        if self._process is not None and self._process.returncode is None:
            return None
        await self._retire()
        self._worker_home = self._root / uuid.uuid4().hex
        await _blocking(self._worker_home.mkdir, mode=0o700)
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(*self._command,
            '--spool', str(self._worker_home), '--cache-home', str(self.cache_home),
            '--memory-limit', str(MEMORY_LIMIT), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=DESCRIPTOR_LIMIT))
        try:
            self._process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            self._process = await spawn
            raise
        message = await self._message()
        if message.get('type') == 'retry':
            result = retry(message.get('reason'), message.get('failure'))
            await self._retire()
            return result
        if message != {'type': 'ready'}:
            raise ValueError('Invalid worker readiness')
        return None

    async def _roundtrip(self, source, identity, digest, cleanup):
        failed = await self._start()
        if failed:
            return failed
        worker_home = self._worker_home
        target = worker_home / (identity + '.output')
        active = worker_home / (identity + '.input')
        cleanup.extend((active, target))
        try:
            await _blocking(os.replace, source, active)
            descriptor = json.dumps({'id': identity, 'digest': digest}, separators=(',', ':')).encode() + b'\n'
            self._process.stdin.write(descriptor)
            await self._process.stdin.drain()
            message = await self._message()
            if message.get('id') != identity:
                raise ValueError('Wrong worker identity')
            if message.get('type') == 'retry':
                result = retry(message.get('reason'), message.get('failure'))
                if result['reason'] in {'memory_limit', 'output_limit', 'worker_failed', 'worker_stopped'}:
                    await self._retire()
                return result
            if (message.get('type') != 'result' or type(message.get('bytes')) is not int
                    or not 0 <= message['bytes'] <= self._output_limit):
                raise _Limit('output_limit')
            # Complete the owned file lifecycle in one worker handoff. Separate
            # unlinks amplify event-loop congestion after an already-fast query.
            # Cancellation still settles that worker before paths/bytes retire.
            result = await _blocking(self._decode_and_remove, target, message, tuple(cleanup))
            cleanup.clear()
            if result['status'] == 'retry' and result['reason'] in {
                    'memory_limit', 'output_limit', 'worker_failed', 'worker_stopped'}:
                await self._retire()
            return result
        except _Limit:
            await self._retire()
            raise

    def _decode_and_remove(self, path, message, cleanup):
        try:
            return self._decode(path, message)
        finally:
            _remove_paths(cleanup)

    def _decode(self, path, message):
        with path.open('rb') as stream:
            data = stream.read(self._output_limit + 1)
        if len(data) > self._output_limit:
            raise _Limit('output_limit')
        if len(data) != message['bytes'] or hashlib.sha256(data).hexdigest() != message.get('digest'):
            raise ValueError('Changed output artifact')
        result = json.loads(data)
        if not isinstance(result, dict) or result.get('status') not in {'complete', 'retry'}:
            raise ValueError('Invalid projection outcome')
        if result['status'] == 'retry':
            if result.get('reason') == 'output_limit':
                return source_receipt(result) or retry('output_limit')
            value = retry(result.get('reason'))
            if 'diagnostic' in result:
                from .work_diagnostics import closed
                facts = closed(result['diagnostic'])
                value['diagnostic'] = {key: facts[key] for key in ('exceptionCategory', 'errno')}
            return value
        return result

    async def _retire(self):
        process, self._process = self._process, None
        home, self._worker_home = self._worker_home, None
        task = asyncio.create_task(self._dispose(process, home))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # close() may interrupt an already running timeout retirement. The
            # detached child handle must remain owned until wait/kill completes.
            await task
            raise

    async def _dispose(self, process, home):
        if process is not None:
            if process.stdin and not process.stdin.is_closing():
                try:
                    process.stdin.write(b'{"stop":true}\n')
                except (BrokenPipeError, ConnectionError):
                    pass
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), self._close_timeout)
            except TimeoutError:
                try: process.kill()
                except ProcessLookupError: pass
                await process.wait()
        if home is not None:
            await _blocking(shutil.rmtree, home, True)

    async def close(self):
        if self._closing is None:
            self._closed = True
            self._stop.set()
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        # Closing stdin also stops a worker whose compute thread ignores guards.
        if self._active is not None:
            self._active.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await self._retire()
        if self._root_task is not None:
            # Cleanup never allocates storage. A failed allocation owns no
            # spool; its original read already reported the failure.
            try:
                root = await asyncio.shield(self._root_task)
            except OSError:
                return
            await _blocking(shutil.rmtree, root, True)
