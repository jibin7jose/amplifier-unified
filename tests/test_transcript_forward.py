"""Complete raw traversal remains separate from conversation visibility."""
import asyncio
import json
import threading
import tracemalloc

import pytest

from amplifier_web import transcript_window as module
from amplifier_web.transcript_window import TranscriptWindowReader

OWNER = {'project': 'project', 'nativeId': 'session'}


def write(root, rows, name='transcript.jsonl'):
    path = root / name
    with path.open('wb') as stream:
        for row in rows:
            stream.write(json.dumps(row).encode() + b'\n')
    return path


def row(text):
    return {'role': 'user', 'content': text}


async def test_forward_exhaustion_retains_raw_order_metadata_and_multiplicity(tmp_path):
    values = [row('same'), {'role': 'system', 'content': 'not a visibility decision'},
              {'role': 'assistant', 'content': [{'type': 'image', 'source': {'data': 'image'}}]},
              {'role': 'tool', 'tool_call_id': 'effect', 'content': '{"success":false}'},
              {**row('voice'), 'metadata': {'amplifier_input': {'kind': 'service'}}}]
    values += [row('same') for _ in range(230)]
    path = write(tmp_path, values)
    original = path.read_bytes()
    pages = [page async for page in TranscriptWindowReader().stream_forward(tmp_path, OWNER, limit=30)]
    records = [item for page in pages for item in page['rows']]
    assert [item['value'] for item in records] == values
    assert [item['nativeIndex'] for item in records] == list(range(len(values)))
    assert [item['byteOffset'] for item in records] == sorted({item['byteOffset'] for item in records})
    assert len(pages) == 8 and pages[-1]['after'] is None
    assert all(len(page['rows']) <= 30 and page['coverage']['scannedBytes'] <= module.READ_LIMIT for page in pages)
    assert path.read_bytes() == original


async def test_stream_has_no_prefetch_and_early_close_leaves_no_reader(tmp_path, monkeypatch):
    write(tmp_path, (row(str(i)) for i in range(300)))
    reader = TranscriptWindowReader()
    calls = []
    original = reader.read_forward
    def observed(*args, **kwargs):
        calls.append(kwargs.get('after'))
        return original(*args, **kwargs)
    monkeypatch.setattr(reader, 'read_forward', observed)
    stream = reader.stream_forward(tmp_path, OWNER)
    first = await anext(stream)
    assert len(first['rows']) == 100
    await asyncio.sleep(.02)
    assert len(calls) == 1
    await stream.aclose()
    await asyncio.sleep(0)
    assert len(calls) == 1


async def test_consumer_metadata_changes_do_not_change_stream_continuation(tmp_path):
    write(tmp_path, [row('one'), row('two')])
    stream = TranscriptWindowReader().stream_forward(tmp_path, OWNER, limit=1)
    first = await anext(stream)
    first['after'], first['revision'] = None, 'consumer-projection'
    second = await anext(stream)
    assert second['rows'][0]['value'] == row('two')
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_cancel_joins_physical_read_and_closes_descriptor(tmp_path, monkeypatch):
    write(tmp_path, [row('retained')])
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    opening = module.os.fdopen
    class HeldFile:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args):
            self.stream.close()
            closed.set()
        def __getattr__(self, name): return getattr(self.stream, name)
        def readline(self, size):
            entered.set()
            assert release.wait(5)
            return self.stream.readline(size)
    monkeypatch.setattr(module.os, 'fdopen', lambda *a, **k: HeldFile(opening(*a, **k)))
    stream = TranscriptWindowReader().stream_forward(tmp_path, OWNER)
    request = asyncio.create_task(anext(stream))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.001)
        request.cancel()
        await asyncio.sleep(.02)
        assert not request.done() and not closed.is_set()
        request.cancel()  # A second shutdown caller must not abandon the read.
        await asyncio.sleep(.02)
        assert not request.done() and not closed.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert closed.is_set()
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_source_change_after_last_batch_prevents_successful_exhaustion(tmp_path):
    path = write(tmp_path, [row('before')])
    stream = TranscriptWindowReader().stream_forward(tmp_path, OWNER)
    assert (await anext(stream))['after'] is None
    path.write_text(json.dumps(row('after')) + '\n')
    with pytest.raises(ValueError, match='changed'):
        await anext(stream)


async def test_late_invalid_record_never_skips_or_switches_to_backup(tmp_path):
    path = write(tmp_path, [row('primary')])
    with path.open('ab') as stream:
        stream.write(b'broken\n')
    write(tmp_path, [row('backup')], 'transcript.jsonl.backup')
    reader = TranscriptWindowReader().stream_forward(tmp_path, OWNER, limit=1)
    assert (await anext(reader))['rows'][0]['value'] == row('primary')
    with pytest.raises(ValueError, match='unavailable'):
        await anext(reader)


def test_pending_append_uses_committed_backup_without_repair(tmp_path):
    path = write(tmp_path, [row('uncommitted')])
    write(tmp_path, [row('safe')], 'transcript.jsonl.backup')
    intent = tmp_path / 'transcript.jsonl.append-pending'
    intent.touch()
    original = path.read_bytes()
    page = TranscriptWindowReader().read_forward(tmp_path, OWNER)
    assert page['source'] == 'backup' and page['rows'][0]['value'] == row('safe')
    assert path.read_bytes() == original and intent.exists()
    (tmp_path / 'transcript.jsonl.backup').unlink()
    assert TranscriptWindowReader().read_forward(tmp_path, OWNER)['status'] == 'unavailable'


def test_forward_cursor_cannot_cross_owner_source_or_direction(tmp_path):
    path = write(tmp_path, [row('one'), row('two')])
    reader = TranscriptWindowReader()
    token = reader.read_forward(tmp_path, OWNER, limit=1)['after']
    assert TranscriptWindowReader().read_forward(tmp_path, OWNER, after=token)['reason'] == 'invalid_cursor'
    assert reader.read_forward(tmp_path, {**OWNER, 'nativeId': 'other'}, after=token)['reason'] == 'source_changed'
    tail = reader.read_window(tmp_path, OWNER, limit=1)['messageWindow']['before']
    assert reader.read_forward(tmp_path, OWNER, after=tail)['reason'] == 'invalid_cursor'
    assert reader.read_window(tmp_path, OWNER, before=token)['reason'] == 'invalid_cursor'
    path.write_text(json.dumps(row('changed')) + '\n')
    assert reader.read_forward(tmp_path, OWNER, after=token)['reason'] == 'source_changed'


async def test_byte_budget_retries_whole_records_without_duplicate_or_lost_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'READ_LIMIT', 1024)
    monkeypatch.setattr(module, 'RECORD_LIMIT', 512)
    values = [row(str(i) + 'x' * 330) for i in range(40)]
    write(tmp_path, values)
    records = []
    async for page in TranscriptWindowReader().stream_forward(tmp_path, OWNER):
        assert page['coverage']['scannedBytes'] <= 1024
        records.extend(item['value'] for item in page['rows'])
    assert records == values


@pytest.mark.parametrize('raw', [b'{"role":"user","content":NaN}\n',
    b'{"role":"tool","content":' + b'[' * 100 + b'0' + b']' * 100 + b'}\n',
    b'{"role":"user","content":"' + b'x' * 600 + b'"}\n'])
def test_invalid_or_oversized_record_is_not_a_complete_stream(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(module, 'RECORD_LIMIT', 512)
    (tmp_path / 'transcript.jsonl').write_bytes(raw)
    assert TranscriptWindowReader().read_forward(tmp_path, OWNER)['status'] == 'unavailable'


def test_blank_crlf_and_unterminated_final_record_preserve_native_indices(tmp_path):
    (tmp_path / 'transcript.jsonl').write_bytes(b'\n' + json.dumps(row('one')).encode() + b'\r\n\n' + json.dumps(row('two')).encode())
    result = TranscriptWindowReader().read_forward(tmp_path, OWNER)
    assert [item['value'] for item in result['rows']] == [row('one'), row('two')]
    assert [item['nativeIndex'] for item in result['rows']] == [0, 1]
    assert result['after'] is None


async def test_large_source_does_not_retain_complete_bodies_or_index(tmp_path):
    path = write(tmp_path, (row('x' * 2000) for _ in range(5000)))
    assert path.stat().st_size > module.READ_LIMIT
    count = 0
    tracemalloc.start()
    try:
        async for page in TranscriptWindowReader().stream_forward(tmp_path, OWNER, limit=25):
            count += len(page['rows'])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == 5000
    assert peak < 2 * 1024 * 1024
