import json
import os
import stat

import pytest

from amplifier_web.voice import VoiceCall, VoiceService


class Service:
    def __init__(self, directory):
        self.data_dir = directory
        self.default_workspace = str(directory)
        self.state = {'sessions': []}
        self.statuses = []

    async def set_voice_status(self, value):
        self.statuses.append(value)


class Socket:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_json(self, event):
        self.sent.append(event)

    async def close(self):
        self.closed = True


def make_call(tmp_path, provider='live'):
    service = Service(tmp_path)
    manager = VoiceService(service, api_key='sk-private-fixture-key', http=object())
    call = VoiceCall(manager, 'session_1')
    call.id, call.provider, call.socket = 'call_1', provider, Socket()
    return call


def records(tmp_path, name='events.jsonl'):
    return [json.loads(line) for line in (tmp_path / 'voice-diagnostics' / name).read_text().splitlines()]


@pytest.mark.parametrize('provider', ['live', 'realtime'])
async def test_provider_error_retains_correlation_without_payload_or_state_changes(tmp_path, provider):
    call = make_call(tmp_path, provider)
    command = {'type': 'session.thinking.append', 'event_id': 'client_1',
               'delegation_id': None, 'content': 'private context'}
    await call.send(command)
    await call.handle({'type': 'session.thinking.appended', 'event_id': 'ack_1', 'client_event_id': 'client_1'})
    error = {'type': 'error', 'event_id': 'error_1', 'client_event_id': 'client_top',
             'error': {'code': 'context_injection_incomplete', 'type': 'server_error',
                       'param': 'session.instructions', 'client_event_id': 'client_1',
                       'message': 'private context sk-private-fixture-key', 'request': command}}
    await call.handle(error)
    await call.handle(error)  # Same provider event remains deduplicated.
    rows = records(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row['provider'] == provider and row['callId'] == 'call_1' and row['sessionId'] == 'session_1'
    assert row['error'] == {'code': 'context_injection_incomplete', 'type': 'server_error',
                            'param': 'session.instructions', 'client_event_id': 'client_1', 'messageOmitted': True}
    assert row['event_id'] == 'error_1' and row['client_event_id'] == 'client_top'
    assert [(item['direction'], item['type']) for item in row['trace']] == [
        ('send', 'session.thinking.append'), ('sent', 'session.thinking.append'),
        ('receive', 'session.thinking.appended')]
    assert [item['sequence'] for item in row['trace']] == [1, 2, 3]
    text = (tmp_path / 'voice-diagnostics' / 'events.jsonl').read_text()
    assert all(value not in text for value in ('private context', 'sk-private-fixture-key', 'request', 'content'))
    assert not call.closed and not call.closing and not call.final.is_set()
    assert call.socket.sent == [command]
    assert call.service.statuses == [{'id': 'call_1', 'attemptId': None,
        'error': 'Voice could not accept an update. Reconnect the call for current context; Amplifier work continues.'}]


async def test_terminal_reason_and_local_finalization_are_retained_without_transcripts(tmp_path):
    call = make_call(tmp_path)
    call.closing = True  # Existing close owns completion; do not schedule another.
    await call.handle({'type': 'session.closed', 'event_id': 'end_1', 'client_event_id': 'close_1',
                       'reason': 'remote_hangup', 'session': {'instructions': 'private prompt'},
                       'usage': {'audio': 'unrelated payload'}})
    result = await call.close()
    rows = records(tmp_path)
    assert [row['type'] for row in rows] == ['session.closed', 'local.closed']
    assert rows[0]['reason'] == 'remote_hangup' and rows[0]['client_event_id'] == 'close_1'
    assert rows[0]['finalized'] is True and rows[0]['closing'] is True
    assert rows[1]['closed'] is True and result['finalized'] is True
    assert 'private prompt' not in json.dumps(rows) and 'unrelated payload' not in json.dumps(rows)


async def test_storage_failure_does_not_mask_provider_error_or_closure(tmp_path):
    (tmp_path / 'voice-diagnostics').write_text('preserve this file')
    call = make_call(tmp_path)
    await call.handle({'type': 'error', 'error': {'code': 'server_error'}})
    call.final.set()
    result = await call.close()
    assert result == {'closed': True, 'finalized': True, 'workContinues': True}
    assert 'Reconnect the call' in call.service.statuses[0]['error']
    assert 'server_error' not in call.service.statuses[0]['error']
    assert call.manager.protocol_diagnostics.storage_error is True
    assert (tmp_path / 'voice-diagnostics').read_text() == 'preserve this file'


async def test_bounded_trace_structured_values_and_private_rotation(tmp_path, monkeypatch):
    from amplifier_web import voice_diagnostics
    monkeypatch.setattr(voice_diagnostics, 'MAX_BYTES', 12_000)
    call = make_call(tmp_path)
    for index in range(80):
        await call.send({'type': 'session.thinking.append', 'event_id': f'client_{index}', 'content': 'hidden'})
    for index in range(8):
        await call.handle({'type': 'error', 'event_id': f'error_{index}', 'error': {
            'code': 'server_error', 'type': 'sk-private-fixture-key', 'param': 'user text\n' * 1000,
            'message': 'hidden' * 10000, 'client_event_id': 'sk-other-secret-value'}})
    directory = tmp_path / 'voice-diagnostics'
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in directory.iterdir():
        assert path.name in {'events.jsonl', 'events.previous.jsonl'}
        assert path.stat().st_size <= 12_000
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        for row in (json.loads(line) for line in path.read_text().splitlines()):
            assert len(row['trace']) == 32
            assert row['error']['type'] == '[REDACTED]'
            assert row['error']['param'] == '[OMITTED]'
            assert row['error']['client_event_id'] == '[REDACTED]'
            assert 'hidden' not in json.dumps(row) and 'sk-' not in json.dumps(row)
    assert len(list(directory.iterdir())) == 2


async def test_symlink_log_is_not_written(tmp_path):
    outside = tmp_path / 'original'; outside.write_text('unchanged')
    directory = tmp_path / 'voice-diagnostics'; directory.mkdir()
    (directory / 'events.jsonl').symlink_to(outside)
    call = make_call(tmp_path)
    await call.handle({'type': 'error', 'error': {'code': 'server_error'}})
    assert outside.read_text() == 'unchanged'
    assert call.manager.protocol_diagnostics.storage_error is True


async def test_realtime_local_close_retains_facts_and_uses_same_hangup(tmp_path):
    from unittest.mock import AsyncMock
    call = make_call(tmp_path, 'realtime')
    call.manager.request = AsyncMock(return_value=({}, '', {}))
    result = await call.close()
    assert result['finalized'] is True
    call.manager.request.assert_awaited_once_with('POST', '/realtime/calls/call_1/hangup')
    row = records(tmp_path)[0]
    assert row['provider'] == 'realtime' and row['type'] == 'local.closed'
    assert row['closed'] is True and row['finalized'] is True


async def test_windows_diagnostics_use_private_publication_without_posix_flags(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from amplifier_web import voice_diagnostics, windows_files

    # Windows has neither O_NOFOLLOW nor fchmod. Do not patch the process-wide
    # os.name: pathlib and pytest must retain their real platform semantics.
    monkeypatch.setattr(voice_diagnostics, 'os', SimpleNamespace(
        name='nt', open=os.open, O_WRONLY=os.O_WRONLY, O_CREAT=os.O_CREAT, O_APPEND=os.O_APPEND))
    monkeypatch.setattr(voice_diagnostics, 'MAX_BYTES', 1800)
    files, publications = {}, []

    @contextmanager
    def private_directory(path, *, owned_root, create):
        assert path == tmp_path / 'voice-diagnostics'
        assert owned_root == tmp_path and create is True

        class Pinned:
            def read_private(self, name, *, limit):
                assert limit == 1800
                if name not in files:
                    raise FileNotFoundError(name)
                assert len(files[name]) <= limit
                return files[name]

            def publish_private(self, name, content, *, replace):
                assert replace is True and len(content) <= 1800
                publications.append(name)
                files[name] = content

        yield Pinned()

    monkeypatch.setattr(windows_files, 'directory', private_directory)
    call = make_call(tmp_path)
    for index in range(20):
        await call.handle({'type': 'error', 'event_id': f'error_{index}',
                           'error': {'code': 'server_error', 'message': 'private context'}})
    call.final.set()
    await call.close()
    assert call.closed and not call.manager.protocol_diagnostics.storage_error
    assert set(files) == {'events.jsonl', 'events.previous.jsonl'}
    assert publications[-1] == 'events.jsonl'
    assert any(name == 'events.previous.jsonl' for name in publications)
    rows = [json.loads(line) for line in files['events.jsonl'].splitlines()]
    assert rows[-1]['type'] == 'local.closed'
    assert all(b'private context' not in data and b'sk-private' not in data for data in files.values())


async def test_windows_private_storage_refusal_does_not_interrupt_close(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from amplifier_web import voice_diagnostics, windows_files

    monkeypatch.setattr(voice_diagnostics, 'os', SimpleNamespace(
        name='nt', open=os.open, O_WRONLY=os.O_WRONLY, O_CREAT=os.O_CREAT, O_APPEND=os.O_APPEND))

    @contextmanager
    def refused(*args, **kwargs):
        raise PermissionError('Untrusted diagnostic directory')
        yield

    monkeypatch.setattr(windows_files, 'directory', refused)
    call = make_call(tmp_path)
    call.final.set()
    result = await call.close()
    assert result == {'closed': True, 'finalized': True, 'workContinues': True}
    assert call.manager.protocol_diagnostics.storage_error is True
