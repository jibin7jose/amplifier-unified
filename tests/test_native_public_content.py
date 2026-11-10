"""Public text visibility must not rewrite canonical history or its anchors."""
import copy
import json

import pytest

from amplifier_web.automatic_history import display_message, read_transcript
from amplifier_web.session_store import _native_anchor


def mixed():
    return {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'Public first'},
        {'type': 'text', 'text': 'Internal detail', 'visibility': 'internal'},
        {'type': 'output_text', 'text': 'Developer detail', 'visibility': 'developer'},
        {'type': 'thinking', 'thinking': 'Private reasoning', 'visibility': 'user'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'YWJj'}},
        {'type': 'text', 'text': 'Public last', 'visibility': 'user'},
    ]}


def test_public_text_filters_private_blocks_without_changing_raw_identity():
    raw = mixed()
    before = copy.deepcopy(raw)
    scope = {'id': 'conversation'}
    internal = display_message(raw, 0, scope, include_internal=True)
    public = display_message(raw, 0, scope)
    assert public['text'] == 'Public first\nPublic last'
    assert public['id'] == internal['id']
    assert public['nativeMessageId'] == internal['id']
    assert internal['text'] == 'Public first\nInternal detail\nDeveloper detail\nPublic last'
    assert _native_anchor([raw], public, scope) == 0
    assert raw == before
    changed = copy.deepcopy(raw)
    changed['content'][1]['text'] = 'Different internal detail'
    with pytest.raises(ValueError, match='saved transcript changed'):
        _native_anchor([changed], public, scope)


@pytest.mark.parametrize('role', ['user', 'assistant'])
@pytest.mark.parametrize('visibility', ['internal', 'developer'])
def test_private_only_text_has_no_public_message_but_retains_internal_anchor(role, visibility):
    raw = {'role': role, 'content': [{'type': 'text', 'text': 'Private', 'visibility': visibility}]}
    assert display_message(raw, 2, {'id': 'conversation'}) is None
    assert display_message(raw, 2, {'id': 'conversation'}, include_internal=True)['text'] == 'Private'


@pytest.mark.parametrize('content', [
    'Literal {"visibility":"internal","text":"example"}',
    [{'type': 'text', 'text': 'Unmarked text'}],
    [{'type': 'output_text', 'text': 'Public output', 'visibility': 'user'}],
    [{'type': 'text', 'text': 'Unmarked text', 'visibility': None}],
])
def test_existing_public_text_and_literal_markup_keep_exact_view(content):
    raw = {'role': 'user', 'content': content}
    assert display_message(raw, 0, {'id': 'conversation'}) == display_message(
        raw, 0, {'id': 'conversation'}, include_internal=True)


@pytest.mark.parametrize('marker', ['ephemeral', 'amplifier_visible_reference', 'amplifier_recovery_reference'])
def test_hidden_reference_rows_do_not_become_public(marker):
    raw = {**mixed(), 'metadata': {marker: True}}
    assert display_message(raw, 0, {'id': 'conversation'}) is None
    assert display_message(raw, 0, {'id': 'conversation'}, include_internal=True) is not None


def test_media_only_and_tool_records_do_not_gain_a_new_public_api():
    raw = {'role': 'assistant', 'content': [{'type': 'image', 'source': {'type': 'url', 'url': 'https://example.invalid/image'}}]}
    assert display_message(raw, 0, {'id': 'conversation'}) is None
    assert display_message({'role': 'tool', 'content': 'Tool result'}, 0, {'id': 'conversation'}) is None


def test_indexed_read_and_navigation_ignore_old_text_projection_caches(tmp_path, monkeypatch):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web.conversation_navigation import source
    from amplifier_web.shared_state_probe import text_content
    from amplifier_web.automatic_history import display_identity

    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    directory = tmp_path / 'projects/project/sessions/native'
    directory.mkdir(parents=True)
    raw = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Private only', 'visibility': 'internal'}]},
           mixed(), {'role': 'user', 'content': 'Public question'}]
    path = directory / 'transcript.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in raw))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    scope = {'id': 'conversation', 'nativeIdentity': 'native', 'nativeProject': 'project', 'historyManaged': True}
    index = SessionHistoryStore(directory).indexed_messages()
    # Seed the prior text-only policy through the real cached-index interface.
    # Its keys must not supply facts after the public visibility policy changes.
    def identity(row, number):
        return display_identity(scope, number, row['role'], text_content(row))
    index.project('unified-display-v4:native', lambda row, number:
                  (identity(row, number), identity(row, number), row['role'], None))
    index.project('unified-navigation-v4:conversation', lambda row, number:
                  (identity(row, number), row['role'], True, number, None))
    page = read_transcript(scope, include_activity=False)
    facts, read = source(scope)
    assert page['total'] == 2 and page['userOffset'] == 0
    assert [row['nativeIndex'] for row in page['messages']] == [1, 2]
    assert [row['text'] for row in page['messages']] == ['Public first\nPublic last', 'Public question']
    assert page['hiddenMessages'] == {0: identity(raw[0], 0)}
    assert [fact[3] for fact in facts] == [1, 2]
    assert read(range(2)) == page['messages']
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize('visibility', ['internal', 'developer'])
def test_private_input_copy_still_makes_canonical_input_identity_ambiguous(tmp_path, monkeypatch, visibility):
    from amplifier_web.automatic_history import alias_input_originals
    from amplifier_web.conversation_navigation import source

    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    directory = tmp_path / 'projects/project/sessions/native'
    directory.mkdir(parents=True)
    provenance = {'amplifier_input': {'version': 1, 'kind': 'user', 'id': 'same-input'}}
    raw = [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Private copy', 'visibility': visibility}], 'metadata': provenance},
        {'role': 'user', 'content': 'Public input', 'metadata': provenance},
    ]
    (directory / 'transcript.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in raw))
    original = {'id': 'retained', 'role': 'user', 'text': 'Original', 'inputId': 'same-input'}
    scope = {'id': 'conversation', 'nativeIdentity': 'native', 'nativeProject': 'project',
             'historyManaged': True, 'messages': [original]}
    page = read_transcript(scope, include_activity=False)
    facts, read = source(scope)
    assert page['total'] == 1 and len(facts) == 1
    assert page['messages'][0]['nativeInputAmbiguous'] is True
    assert read([0]) == page['messages']
    assert alias_input_originals(scope, page['messages']) == page['messages']
    assert scope['messages'] == [original]
