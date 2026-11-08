import asyncio
from types import SimpleNamespace

import pytest

from amplifier_web import probe_diagnostics
from amplifier_web.probe_diagnostics import communicate, failure_detail, stderr_tail


async def test_stderr_retention_is_bounded_even_without_newlines():
    stream = asyncio.StreamReader()
    stream.feed_data(b'secret' * 100000 + b'final marker')
    stream.feed_eof()
    result = await stderr_tail(stream)
    assert len(result) == 32768
    assert result.endswith('final marker')


def test_unknown_stderr_never_echoes_private_details():
    result = failure_detail('Traceback /private/person/token abc-secret https://user:password@server.invalid')
    assert result == 'Check the runtime installation or update the app, then retry.'


def test_import_and_download_errors_have_distinct_next_steps():
    assert 'import a required module' in failure_detail('ModuleNotFoundError: private_name')
    assert 'DNS' in failure_detail('Could not resolve host: private.server')
    assert 'disk is full' in failure_detail('No space left on device /private/path')


class Input:
    def __init__(self, error=None):
        self.chunks = []
        self.closed = asyncio.Event()
        self.error = error
        self.drains = 0

    def write(self, chunk):
        assert len(chunk) <= 8192
        self.chunks.append(chunk)

    async def drain(self):
        self.drains += 1
        if self.error is not None:
            raise self.error
        await asyncio.sleep(0)

    def close(self):
        self.closed.set()


def process_with_output(output, *, input_error=None):
    stdin = Input(input_error)
    stdout = asyncio.StreamReader()
    stdout.feed_data(output)
    stdout.feed_eof()

    async def wait():
        await stdin.closed.wait()
        process.returncode = 0
        return 0

    process = SimpleNamespace(stdin=stdin, stdout=stdout, wait=wait, returncode=None)
    return process


async def exchange(process, payload):
    """Exercise the bounded owner exchange with ordinary binary pipe fakes."""
    output, _ = await probe_diagnostics._exchange(process, payload)
    return output


async def test_ordinary_communicate_keeps_existing_read_write_wait_seam():
    process = process_with_output(b'{}')
    calls = []

    async def read():
        calls.append('read')
        return b'{"ok":true}'

    process.stdout = SimpleNamespace(read=read)
    assert await communicate(process, b'input') == b'{"ok":true}'
    assert calls == ['read'] and process.stdin.chunks == [b'input']
    assert process.stdin.closed.is_set() and process.returncode == 0


async def test_exchange_preserves_binary_payload_and_closes_stdin_before_wait():
    process = process_with_output('{"result":"試験"}'.encode())
    payload = b'input' * 4000
    assert await exchange(process, payload) == '{"result":"試験"}'.encode()
    assert b''.join(process.stdin.chunks) == payload
    assert process.stdin.drains == 3
    assert process.stdin.closed.is_set() and process.returncode == 0


@pytest.mark.parametrize('error', [BrokenPipeError(), ConnectionResetError()])
async def test_exchange_broken_input_still_closes_and_reads_output(error):
    process = process_with_output(b'{"error":"early exit"}', input_error=error)
    assert await exchange(process, b'payload') == b'{"error":"early exit"}'
    assert process.stdin.closed.is_set()


async def test_exchange_capture_accepts_exact_limit_and_rejects_overflow(monkeypatch):
    monkeypatch.setattr(probe_diagnostics, 'MAX_PROBE_BYTES', 16384)
    process = process_with_output(b'x' * 16384)
    assert len(await exchange(process, b'')) == 16384
    process = process_with_output(b'x' * 16385)
    with pytest.raises(ValueError, match='output exceeded'):
        await exchange(process, b'')
    assert process.stdin.closed.is_set()


async def test_exchange_input_overflow_is_not_written_and_closes_stdin(monkeypatch):
    monkeypatch.setattr(probe_diagnostics, 'MAX_PROBE_BYTES', 16)
    process = process_with_output(b'{}')
    with pytest.raises(ValueError, match='input exceeded'):
        await exchange(process, b'x' * 17)
    assert process.stdin.chunks == [] and process.stdin.closed.is_set()


async def test_exchange_cancellation_settles_all_child_tasks():
    process = process_with_output(b'')
    entered, finished = asyncio.Event(), asyncio.Event()
    process.stdout = asyncio.StreamReader()  # No EOF, so capture stays pending.

    async def wait():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    process.wait = wait
    task = asyncio.create_task(exchange(process, b'input'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.stdin.closed.is_set() and finished.is_set()
    # A cancelled communicate must not leave a competing stdout reader.
    process.stdout.feed_data(b'later')
    process.stdout.feed_eof()
    assert await process.stdout.read() == b'later'


async def test_exchange_reader_failure_settles_sibling_wait():
    process = process_with_output(b'')
    process.stdout.set_exception(OSError('synthetic pipe failure'))
    finished = asyncio.Event()

    async def wait():
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    process.wait = wait
    with pytest.raises(OSError, match='synthetic pipe failure'):
        await exchange(process, b'input')
    assert finished.is_set() and process.stdin.closed.is_set()
