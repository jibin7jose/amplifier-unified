"""Requested event pages never depend on a full transcript or family graph."""
import base64
import json
import os
from pathlib import Path

import pytest

from amplifier_web import event_window as windows

KEY = b'k' * 32


@pytest.mark.parametrize('native', [False, True])
def test_descriptor_named_stat_time_semantics_remain_platform_specific(monkeypatch, native):
    from types import SimpleNamespace
    from amplifier_web import session_files, history_read_model
    monkeypatch.setattr(session_files, '_WINDOWS', native)
    fields = dict(st_dev=7, st_ino=11, st_size=150, st_mtime_ns=300, st_birthtime_ns=100)
    descriptor = SimpleNamespace(**fields, st_ctime_ns=200)
    named = SimpleNamespace(**fields, st_ctime_ns=100)
    assert (windows._stamp(descriptor) == windows._stamp(named)) is native
    # The host's cache validator must interpret the same timestamp as its
    # disposable reader; otherwise valid pages fail immediately after reading.
    monkeypatch.setattr(Path, 'stat', lambda _self: named)
    expected = windows._stamp(named)
    assert history_read_model.source_stamp('synthetic') == (
        tuple(expected[:2]), *expected[2:])


def test_windows_metadata_normalization_keeps_field_mutation_and_replacement_fences(owner, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web import session_files
    session, path = owner
    write(path, [event(1)])
    monkeypatch.setattr(session_files, '_WINDOWS', True)
    original_fstat, original_stat, original_lstat = os.fstat, Path.stat, Path.lstat
    def modeled(info, descriptor=False):
        return SimpleNamespace(**{key: getattr(info, key) for key in
            ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_mode')},
            st_birthtime_ns=100, st_ctime_ns=200 if descriptor else 100)
    monkeypatch.setattr(os, 'fstat', lambda fd: modeled(original_fstat(fd), True))
    monkeypatch.setattr(Path, 'stat', lambda self, **kwargs: modeled(original_stat(self, **kwargs)))
    monkeypatch.setattr(Path, 'lstat', lambda self, **kwargs: modeled(original_lstat(self, **kwargs)))
    page = read(session)
    reference = page['items'][0]['_eventFields']['output']
    assert windows.read_field(session, reference)['text'] == 'result-1'
    cursor = windows._cursor(KEY, windows._source(session)[2],
        'messages-latest', windows._stamp(modeled(original_stat(path))), 0)
    # Same-length content changes cannot borrow the original accepted digest.
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'result-1', b'changed1'))
    with pytest.raises(windows.StaleWindow, match='source changed'):
        windows.read_field(session, reference)
    path.write_bytes(raw)
    replacement = path.with_suffix('.replacement')
    replacement.write_bytes(raw)
    replacement.replace(path)
    with pytest.raises(windows.StaleWindow):
        read(session, before=cursor)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    monkeypatch.delenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', raising=False)
    session = {'id': 'app', 'runtimeSessionId': 'root', 'nativeProject': 'project',
               'workspace': str(tmp_path / 'workspace')}
    path, _, _ = windows._source(session)
    path.parent.mkdir(parents=True)
    return session, path


def write(path, rows):
    path.write_bytes(b''.join(json.dumps(row, separators=(',', ':')).encode() + b'\n'
                              for row in rows))


def event(number, name='tool:post', **data):
    return {'event': name, 'timestamp': number,
            'data': {'session_id': 'root', 'tool_call_id': f'call-{number}',
                     'tool_name': 'bash', 'result': f'result-{number}', **data}}


def read(session, **kwargs):
    return windows.read_window(session, window_key='messages-latest', cursor_key=KEY, **kwargs)


def test_last_page_and_explicit_older_pages_equal_source_order_without_gaps(owner):
    session, path = owner
    rows = [event(number) for number in range(237)]
    write(path, rows)
    pages, cursor = [], None
    while True:
        page = read(session, before=cursor)
        assert page['coverage']['scannedBytes'] <= windows.MAX_BYTES
        assert len(page['items']) <= 100
        assert page['coverage']['historyComplete'] is False
        assert page['coverage']['unassociatedCount'] == len(page['items'])
        pages.insert(0, page['items'])
        cursor = page['before']
        if cursor is None:
            assert page['coverage']['rootExhausted']
            break
    actual = [row for page in pages for row in page]
    assert [row['toolCallId'] for row in actual] == [f'call-{n}' for n in range(237)]
    assert [row['output'] for row in actual] == [f'result-{n}' for n in range(237)]
    assert [row['createdAt'] for row in actual] == list(range(237))
    assert len({row['id'] for row in actual}) == 237
    assert all(row['phase'] == 'recorded' and row['association'] == 'unverified' for row in actual)


def test_no_graph_transcript_children_or_accounting_reads(owner, monkeypatch):
    session, path = owner
    write(path, [event(1, 'delegate:agent_spawned', sub_session_id='child'), event(2)])
    class Poison:
        def __iter__(self):
            pytest.fail('dormant tree traversed')
    session.update(workers=Poison(), messages=Poison(), execution=Poison())
    original = os.open
    opened = []
    def guarded(value, *args, **kwargs):
        opened.append(value)
        assert value == path
        return original(value, *args, **kwargs)
    monkeypatch.setattr(os, 'open', guarded)
    page = read(session)
    assert page['coverage']['sourceCount'] == 1
    assert [row.get('childSessionId') for row in page['items']] == ['child', None]
    assert opened == [path]
    assert all('aggregateUsage' not in row and 'turnId' not in row for row in page['items'])


@pytest.mark.parametrize('action', ['message', 'steer'])
def test_followup_is_typed_request_with_lazy_exact_text_not_a_reply(owner, action):
    session, path = owner
    text = 'Follow-up café 日本語\n' * 1400 + 'FOLLOWUP-END'
    write(path, [event(1, tool_name='live_worker',
                      tool_input={'action': action, 'session_id': 'child', 'text': text},
                      result={'success': True, 'output': {'accepted': True, 'completed': False}})])
    row = read(session)['items'][0]
    assert row['association'] == 'unverified'
    assert row['delegateRequest'].startswith('Follow-up café')
    assert len(row['delegateRequest']) < len(text)
    assert 'delegateResponse' not in row and 'delegateResponseDetail' not in row
    ref = row['_eventFields']['delegateRequest']
    chunks, offset = [], 0
    while offset is not None:
        chunk = windows.read_field(session, ref, offset=offset, limit=1024)
        chunks.append(chunk['text'])
        offset = chunk['nextOffset']
    assert ''.join(chunks) == text and len(chunks) > 1
    with pytest.raises(ValueError, match='Invalid delegation field'):
        windows.read_field(session, {**row['_eventFields']['output'], 'delegateField': 'delegateResponse'})


@pytest.mark.parametrize('action', ['list', 'cancel', 'finish'])
def test_nonmessage_worker_operations_do_not_become_exchanges(owner, action):
    session, path = owner
    write(path, [event(1, tool_name='live_worker',
                      tool_input={'action': action, 'text': 'not a follow-up'},
                      result='not a child reply')])
    row = read(session)['items'][0]
    assert 'delegateRequestDetail' not in row
    assert 'delegateResponseDetail' not in row
    with pytest.raises(ValueError, match='Invalid delegation field'):
        windows.read_field(session, {**row['_eventFields']['input'], 'delegateField': 'delegateRequest'})


def test_followup_pre_and_result_only_post_remain_independent_observations(owner):
    session, path = owner
    write(path, [
        {'event': 'tool:pre', 'timestamp': 1, 'data': {'session_id': 'root',
         'tool_call_id': 'follow', 'tool_name': 'live_worker',
         'tool_input': {'action': 'message', 'session_id': 'child', 'text': 'Continue café'}}},
        {'event': 'tool:post', 'timestamp': 2, 'data': {'session_id': 'root',
         'tool_call_id': 'follow', 'tool_name': 'live_worker',
         'result': {'accepted': True, 'completed': False}}},
    ])
    before, after = read(session)['items']
    assert before['delegateRequest'] == 'Continue café'
    assert 'delegateRequest' not in after and 'delegateResponse' not in after
    assert before['association'] == after['association'] == 'unverified'


def test_error_record_cannot_be_reselected_as_successful_delegate_reply(owner):
    session, path = owner
    write(path, [event(1, 'tool:error', tool_name='delegate',
                      result={'output': {'response': 'not completed'}})])
    row = read(session)['items'][0]
    assert 'delegateResponseDetail' not in row
    with pytest.raises(ValueError, match='failed tool observation'):
        windows.read_field(session, {**row['_eventFields']['output'], 'delegateField': 'delegateResponse'})


def test_large_unrequested_prefix_does_not_change_read_cost_or_rows(owner):
    session, path = owner
    tail = b''.join(json.dumps(event(n)).encode() + b'\n' for n in range(120))
    path.write_bytes(tail)
    small = read(session)
    # A sparse, invalid dormant prefix would fail any whole-file parser; the
    # last requested records remain available without reading that prefix.
    with path.open('wb') as stream:
        stream.seek(64 * 1024 * 1024)
        stream.write(b'\n' + tail)
    large = read(session)
    assert [row['toolCallId'] for row in large['items']] == [row['toolCallId'] for row in small['items']]
    assert large['coverage']['scannedEvents'] == small['coverage']['scannedEvents'] == 100
    assert large['coverage']['scannedBytes'] <= windows.BLOCK_BYTES
    assert large['before'] is not None


def test_source_fields_are_exact_and_edit_invalidates_them(owner):
    from amplifier_web.event_log_view import read_field
    session, path = owner
    output = {'body': 'x' * 5000}
    write(path, [event(1, result=output)])
    row = read(session)['items'][0]
    assert len(row['output']) <= windows.PREVIEW_CHARS
    assert read_field(row['_eventFields']['output']) == json.dumps(output, ensure_ascii=False, indent=2)
    write(path, [event(1, result={'body': 'y' * 5000})])
    with pytest.raises(ValueError, match='changed'):
        read_field(row['_eventFields']['output'])


def test_worker_field_pages_equal_original_text_and_refuse_other_owner(owner):
    session, path = owner
    value = {'text': '界' * 200, 'nested': [1, 'a', {'ok': True}]}
    write(path, [event(1, result=value)])
    reference = read(session)['items'][0]['_eventFields']['output']
    pieces, offset = [], 0
    while True:
        result = windows.read_field(session, reference, offset=offset, limit=17)
        assert len(result['text']) <= 17
        pieces.append(result['text'])
        offset = result['nextOffset']
        if offset is None:
            break
    expected = json.dumps(value, ensure_ascii=False, indent=2)
    assert ''.join(pieces) == expected
    assert result['total'] == len(expected)
    import hashlib
    assert result['digest'] == hashlib.sha256(expected.encode()).hexdigest()
    with pytest.raises(ValueError, match='reference'):
        windows.read_field({**session, 'nativeIdentity': 'other'}, reference)
    with pytest.raises(ValueError, match='reference'):
        windows.read_field(session, {**reference, 'key': 'credentials'})
    write(path, [event(1, result={'text': 'changed'})])
    with pytest.raises(windows.StaleWindow):
        windows.read_field(session, reference)


def test_cut_prefix_does_not_pair_requests_infer_turns_or_compute_usage(owner):
    session, path = owner
    write(path, [event(1, 'llm:request', raw={'messages': [{'content': 'large'}]}),
                 event(2, 'llm:response', raw={'output': 'done'}, usage={'total_tokens': 100}),
                 event(3, 'prompt:submit', prompt='Repeated prompt')])
    page = read(session)
    assert len(page['items']) == 3
    assert all('usage' not in row and 'turnId' not in row and 'anchorMessageId' not in row
               for row in page['items'])
    assert 'request' not in page['items'][0] and 'response' not in page['items'][1]
    assert page['items'][0]['requestDetail']['digestKind'] == 'event-field-v1'
    assert page['items'][1]['responseDetail']['digestKind'] == 'event-field-v1'


@pytest.mark.parametrize('change', ['append', 'replace', 'edit', 'truncate'])
def test_cursor_rejects_every_changed_source(owner, change):
    session, path = owner
    write(path, [event(n) for n in range(3)])
    cursor = read(session, limit=1)['before']
    if change == 'append':
        with path.open('ab') as stream:
            stream.write(json.dumps(event(4)).encode() + b'\n')
    elif change == 'replace':
        replacement = path.with_suffix('.new')
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
    elif change == 'edit':
        path.write_bytes(path.read_bytes().replace(b'result-0', b'edited-0'))
    else:
        path.write_bytes(b'')
    with pytest.raises(windows.StaleWindow):
        read(session, before=cursor)


def test_cursor_is_bound_to_owner_window_and_complete_record_boundary(owner):
    session, path = owner
    write(path, [event(n) for n in range(3)])
    cursor = read(session, limit=1)['before']
    with pytest.raises(windows.StaleWindow):
        windows.read_window(session, window_key='different-window', cursor_key=KEY, before=cursor)
    with pytest.raises(windows.StaleWindow):
        read({**session, 'id': 'different-public-owner'}, before=cursor)
    decoded = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4))[32:])
    decoded['end'] -= 1
    forged = windows._cursor(KEY, decoded['owner'], decoded['window'], decoded['source'], decoded['end'])
    with pytest.raises(windows.StaleWindow, match='boundary'):
        read(session, before=forged)


def test_partial_final_line_is_not_accepted_and_next_append_is_visible(owner):
    session, path = owner
    write(path, [event(1)])
    with path.open('ab') as stream:
        stream.write(json.dumps(event(2)).encode())
    first = read(session)
    assert [row['toolCallId'] for row in first['items']] == ['call-1']
    assert first['coverage']['partialRecord']
    with path.open('ab') as stream:
        stream.write(b'\n')
    second = read(session)
    assert [row['toolCallId'] for row in second['items']] == ['call-1', 'call-2']
    assert not second['coverage']['partialRecord']
    assert second['generation'] != first['generation']


def test_oversized_record_is_explicitly_blocked_not_false_empty_complete(owner):
    session, path = owner
    write(path, [event(0), event(1, result='x' * (windows.MAX_BYTES + 1024))])
    result = read(session)
    assert result['items'] == []
    assert result['coverage']['pagingBlocked']
    assert not result['coverage']['rootExhausted']
    assert not result['coverage']['historyComplete']
    assert result['coverage']['scannedBytes'] == windows.MAX_BYTES
    assert result['before'] is not None
    older = read(session, before=result['before'])
    assert [row['toolCallId'] for row in older['items']] == ['call-0']
    assert older['coverage']['rootExhausted']
    assert older['unavailableRanges']


def test_non_work_event_limit_has_resumable_cursor(owner):
    session, path = owner
    write(path, [event(n, 'session:end') for n in range(600)])
    page = read(session)
    assert page['items'] == []
    assert page['coverage']['scannedEvents'] == 512
    assert page['before'] is not None
    last = read(session, before=page['before'])
    assert last['coverage']['scannedEvents'] == 88
    assert last['coverage']['rootExhausted']


def test_change_during_read_rejected_and_cancellation_propagates(owner):
    session, path = owner
    write(path, [event(1)])
    calls = 0
    def changed():
        nonlocal calls
        calls += 1
        if calls == 3:
            with path.open('ab') as stream:
                stream.write(b'{}\n')
    with pytest.raises(windows.StaleWindow):
        read(session, check_budget=changed)
    class Cancelled(Exception):
        pass
    def cancel():
        raise Cancelled()
    with pytest.raises(Cancelled):
        read(session, check_budget=cancel)


def test_symlink_cannot_grant_another_source(owner, tmp_path):
    session, path = owner
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.write_text('{}\n')
    path.symlink_to(elsewhere)
    with pytest.raises(ValueError, match='escaped'):
        read(session)


def test_missing_and_empty_sources_do_not_claim_family_completeness(owner):
    session, path = owner
    missing = read(session)
    assert missing['coverage']['sourceCount'] == 0
    assert missing['coverage']['historyComplete'] is False
    path.touch()
    empty = read(session)
    assert empty['coverage']['sourceCount'] == 1
    assert empty['coverage']['rootExhausted'] is True
    assert empty['coverage']['historyComplete'] is False


def test_structural_limit_is_reported_before_decoding_and_older_rows_survive(owner, monkeypatch):
    session, path = owner
    write(path, [event(1), event(2, result=[0] * 70000), event(3)])
    original = json.loads
    def bounded(raw, *args, **kwargs):
        assert len(raw) < 1000, 'oversized structure reached JSON allocation'
        return original(raw, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', bounded)
    page = read(session)
    assert [row['toolCallId'] for row in page['items']] == ['call-1', 'call-3']
    assert page['coverage']['invalidEvents'] == 1
    assert not page['coverage']['historyComplete']


def test_cursor_cannot_be_forged_and_host_key_owns_its_lifetime(owner):
    session, path = owner
    write(path, [event(n) for n in range(3)])
    cursor = read(session, limit=1)['before']
    raw = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4))
    forged = base64.urlsafe_b64encode(bytes([raw[0] ^ 1]) + raw[1:]).decode()
    with pytest.raises(windows.StaleWindow):
        read(session, before=forged)
    assert read(session, before=cursor)['items']  # Stateless/disposable reader.
    with pytest.raises(windows.StaleWindow):
        windows.read_window(session, window_key='messages-latest', cursor_key=b'x' * 32, before=cursor)


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX special-file refusal')
def test_special_file_cannot_block_the_worker(owner):
    session, path = owner
    os.mkfifo(path)
    with pytest.raises(ValueError, match='regular file'):
        read(session)


@pytest.mark.parametrize('budget', [192, 256, 320])
def test_valid_neighbors_survive_paging_across_oversized_record(owner, monkeypatch, budget):
    session, path = owner
    monkeypatch.setattr(windows, 'MAX_BYTES', budget)
    monkeypatch.setattr(windows, 'BLOCK_BYTES', 64)
    write(path, [event(0), event(1, result='x' * (budget * 4)), event(2)])
    seen, gaps, before, cursors = [], [], None, set()
    for _ in range(20):
        page = read(session, before=before)
        seen[0:0] = [row['toolCallId'] for row in page['items']]
        gaps.extend(page['unavailableRanges'])
        before = page['before']
        if before is None:
            break
        assert before not in cursors, 'older paging did not advance'
        cursors.add(before)
    else:
        pytest.fail('bounded older paging never completed')
    assert seen == ['call-0', 'call-2']
    assert gaps and all(gap['reason'] in {'read_limit', 'record_limit'} for gap in gaps)
