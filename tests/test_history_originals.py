"""Passive history keeps participant originals, not expanded model inputs."""
import copy
import json

import pytest

from amplifier_web.history_query import _rows


def fixture(tmp_path, monkeypatch, *, caption='Inspect this image.', kind='user'):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    directory = tmp_path / 'projects/project/sessions/native'
    directory.mkdir(parents=True)
    original = {'id': 'original', 'role': 'user', 'text': caption, 'inputId': 'input',
                'via': 'chat', 'createdAt': 42, 'delivery': {'status': 'delivered'},
                'attachments': [{'id': 'image', 'name': 'image.png', 'mime': 'image/png'}]}
    native = {'role': 'user', 'content': [
        {'type': 'text', 'text': caption},
        {'type': 'text', 'text': 'Model attachment context: /private/path'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'YWJj'}},
    ], 'metadata': {'amplifier_input': {'version': 1, 'kind': kind, 'id': 'input', 'source': 'chat'}}}
    session = {'id': 'web', 'nativeIdentity': 'native', 'nativeProject': 'project',
               'messages': [original], 'historyManaged': False}
    return directory, session, native


def write(directory, rows):
    path = directory / 'transcript.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return path


@pytest.mark.parametrize('caption', ['Inspect this image.', ''])
@pytest.mark.parametrize('kind', ['user', 'steer'])
def test_exact_original_retains_identity_content_and_order(tmp_path, monkeypatch, caption, kind):
    directory, session, native = fixture(tmp_path, monkeypatch, caption=caption, kind=kind)
    one = copy.deepcopy(session['messages'][0])
    two = {**copy.deepcopy(one), 'id': 'second', 'inputId': 'second-input',
           'attachments': [{'id': 'second-image', 'name': 'second.png'}]}
    uncheckpointed = {'id': 'pending', 'role': 'assistant', 'text': 'Retained reply',
                      'attachments': [{'id': 'notes', 'name': 'notes.txt'}]}
    session['messages'] += [two, uncheckpointed]
    second = {**native, 'metadata': {'amplifier_input': {
        'version': 1, 'kind': kind, 'id': 'second-input', 'source': 'chat'}}}
    path = write(directory, [native, {'role': 'assistant', 'content': 'Native reply'}, second])
    original, before = copy.deepcopy(session), (path.read_bytes(), path.stat().st_mtime_ns)
    rows, _ = _rows(session)
    assert [row['text'] for row in rows] == [caption, 'Native reply', caption, 'Retained reply']
    assert [{key: rows[index][key] for key in original} for index, original in ((0, one), (2, two))] == [one, two]
    assert [rows[index]['nativeIndex'] for index in (0, 2)] == [0, 2]
    assert all(rows[index]['nativeMessageId'] != rows[index]['id'] for index in (0, 2))
    assert rows[3] == uncheckpointed
    assert 'Model attachment context' not in json.dumps(rows)
    assert session == original
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize('ambiguity', ['native', 'original'])
def test_ambiguous_input_keeps_all_originals_and_native_occurrences(tmp_path, monkeypatch, ambiguity):
    directory, session, native = fixture(tmp_path, monkeypatch)
    native_rows = [native]
    if ambiguity == 'native':
        native_rows += [{'role': 'assistant', 'content': 'Between'}, copy.deepcopy(native)]
    else:
        session['messages'].append({**copy.deepcopy(session['messages'][0]), 'id': 'other-original'})
    write(directory, native_rows)
    before = copy.deepcopy(session)
    rows, _ = _rows(session)
    for original in session['messages']:
        assert original in rows and 'nativeMessageId' not in original
    assert len([row for row in rows if row.get('source') == 'native']) == len(native_rows)
    assert session == before


@pytest.mark.parametrize('change', [
    {'nativeIndex': 7}, {'nativeMessageId': 'different-source'},
])
def test_stale_original_binding_fails_without_rewriting_or_dropping_it(tmp_path, monkeypatch, change):
    directory, session, native = fixture(tmp_path, monkeypatch)
    session['messages'][0].update(change)
    write(directory, [native])
    before = copy.deepcopy(session)
    with pytest.raises(ValueError, match='rewritten'):
        _rows(session)
    assert session == before


@pytest.mark.parametrize('identity', [None, {'id': 'input'}, ['input'], True, 'different'])
def test_unmatched_or_malformed_input_keeps_original(tmp_path, monkeypatch, identity):
    directory, session, native = fixture(tmp_path, monkeypatch)
    session['messages'][0]['inputId'] = identity
    write(directory, [native])
    before = copy.deepcopy(session)
    rows, _ = _rows(session)
    assert rows[-1] == session['messages'][0]
    assert len(rows) == 2 and rows[0].get('source') == 'native'
    assert session == before
