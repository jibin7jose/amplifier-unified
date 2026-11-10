"""Canonical input multiplicity includes records outside the text view."""
import base64
import copy
import json

import pytest

from amplifier_web.automatic_history import alias_input_originals, display_message, read_transcript
from amplifier_web.conversation_navigation import source
from amplifier_web.history_query import _rows
from amplifier_web.voice_input_projection import align
from test_output_images import png
from test_voice_input_projection import native_voice


def retained_input(kind):
    if kind == 'voice':
        return native_voice('Spoken request'), {
            'id': 'original', 'role': 'user', 'text': 'Spoken request', 'via': 'call',
            'voiceId': 'call-one', 'voiceInputId': 'voice:call-one:delegation-one'}
    return {'role': 'user', 'content': 'Expanded model input', 'metadata': {
        'amplifier_input': {'version': 1, 'kind': kind, 'id': 'same-input'}}}, {
        'id': 'original', 'role': 'user', 'text': 'Exact user original', 'inputId': 'same-input',
        'attachments': [{'id': 'retained-file', 'name': 'notes.txt', 'mime': 'text/plain', 'size': 3}]}


def saved_history(tmp_path, monkeypatch, raw, original):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    directory = tmp_path / 'projects/project/sessions/native'
    directory.mkdir(parents=True)
    path = directory / 'transcript.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in raw))
    scope = {'id': 'conversation', 'nativeIdentity': 'native', 'nativeProject': 'project',
             'historyManaged': False, 'messages': [original]}
    return scope, path


def seed_display_dependent_input_caches(path, scope):
    from amplifier_foundation.session.history import SessionHistoryStore

    index = SessionHistoryStore(path.parent).indexed_messages()

    def old_fact(value, number):
        row = display_message(value, number, scope)
        internal = row or display_message(value, number, scope, include_internal=True)
        return row, internal

    def display(value, number):
        row, internal = old_fact(value, number)
        return (row['id'] if row else None, internal['id'] if internal else None,
                value.get('role'), internal.get('nativeInputId') if internal else None)

    def navigation(value, number):
        row, internal = old_fact(value, number)
        return (row['id'] if row else None, internal['role'] if internal else None,
                not bool(row.get('observation')) if row else False, number,
                internal.get('nativeInputId') if internal else None, None)

    # Seed the actual previous cache keys. Their incomplete multiplicity must
    # not survive an upgrade even when the transcript bytes stay unchanged.
    index.project('unified-display-v5:native', display)
    index.project('unified-navigation-v6:conversation', navigation)


@pytest.mark.parametrize('kind', ['user', 'steer', 'voice'])
@pytest.mark.parametrize('hidden', [None, 'image', 'empty'])
@pytest.mark.parametrize('old_cache', [False, True])
def test_hidden_input_occurrences_do_not_bind_retained_originals(
        tmp_path, monkeypatch, kind, hidden, old_cache):
    native, original = retained_input(kind)
    raw = [native]
    if hidden:
        content = [] if hidden == 'empty' else [{'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(png()).decode()}}]
        raw.insert(0, {'role': 'user', 'content': content, 'metadata': copy.deepcopy(native['metadata'])})
    scope, path = saved_history(tmp_path, monkeypatch, raw, original)
    before = (copy.deepcopy(scope), path.read_bytes(), path.stat().st_mtime_ns)
    if old_cache:
        seed_display_dependent_input_caches(path, scope)

    page = read_transcript(scope, include_activity=False)
    facts, read = source({**scope, 'historyManaged': True})
    navigation = read(range(len(facts)))
    rows, _ = _rows(scope)
    retained = next(row for row in rows if row['id'] == original['id'])

    # No new media-only message, edit anchor or visible ordinal is introduced.
    expected_native = display_message(native, len(raw) - 1, scope)
    assert page['total'] == len(navigation) == 1
    assert page['messages'][0]['id'] == expected_native['id']
    assert page['messages'][0]['nativeIndex'] == len(raw) - 1
    assert page['messages'][0]['text'] == expected_native['text']
    assert navigation == page['messages']
    if hidden:
        assert len(rows) == 2
        assert retained == original
        assert rows[0]['id'] == expected_native['id']
        assert page['messages'][0]['nativeInputAmbiguous'] is True
        # The independently consumed navigation path must also refuse aliasing.
        current, incoming = align(scope, navigation)
        assert current == [original] and incoming == navigation
        assert alias_input_originals(scope, navigation) == navigation
    else:
        assert len(rows) == 1
        assert retained == {**original, 'nativeMessageId': expected_native['id'], 'nativeIndex': 0}
        assert not page['messages'][0].get('nativeInputAmbiguous')
    assert (scope, path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize('changes', [
    {'role': 'assistant'}, {'metadata': None}, {'metadata': {}},
    {'metadata': {'amplifier_input': None}},
    {'metadata': {'amplifier_input': {'version': 2, 'kind': 'user', 'id': 'same-input'}}},
    {'metadata': {'amplifier_input': {'version': 1, 'kind': 'service', 'id': 'same-input'}}},
])
def test_non_input_metadata_does_not_make_a_unique_original_ambiguous(tmp_path, monkeypatch, changes):
    native, original = retained_input('user')
    other = {'role': 'user', 'content': [], 'metadata': copy.deepcopy(native['metadata']), **changes}
    scope, _ = saved_history(tmp_path, monkeypatch, [other, native], original)
    page = read_transcript(scope, include_activity=False)
    facts, read = source({**scope, 'historyManaged': True})
    assert page['total'] == len(facts) == 1
    assert not page['messages'][0].get('nativeInputAmbiguous')
    assert read([0]) == page['messages']
    rows, _ = _rows(scope)
    assert rows == [{**original, 'nativeMessageId': page['messages'][0]['id'], 'nativeIndex': 1}]
