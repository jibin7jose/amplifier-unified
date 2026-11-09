import copy
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.service import AppService
from amplifier_web.runtime import normalize_event


@pytest.mark.asyncio
async def test_search_and_read_are_passive_scoped_and_paginated(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier"))
    app = AppService(tmp_path / "app", workspace=tmp_path)
    try:
        root = app._new_session({"title": "Earlier", "workspace": str(tmp_path)})
        app.state["sessions"].append(root)
        root["messages"] = [{"id": "message", "role": "user", "text": "decision needle " + "x" * 6000}]
        root["title"] = "Earlier decision"
        other = {**copy.deepcopy(root), "id": "other", "workspace": "/another", "title": "Other workspace"}
        app.state["sessions"].append(other)
        await app.history.refresh()  # Discovery may update the derived index, never the selected chat.
        before = copy.deepcopy(app.state)
        result = await app.app_bridge("history", {"action": "search", "query": "needle"}, root["id"])
        assert [row["id"] for row in result["items"]] == [root["id"]]
        assert result["items"][0]["matches"][0]["message_id"] == "message"
        result = await app.app_bridge("history", {"action": "list", "scope": "all", "limit": 1}, root["id"])
        assert result["next_offset"] == 1
        result = await app.app_bridge("history", {"action": "read", "session_id": root["id"], "text_limit": 100}, root["id"])
        assert result["messages"][0]["next_text_offset"] == 100
        with pytest.raises(ValueError, match="scope"):
            await app.app_bridge("history", {"action": "read", "session_id": "other"}, root["id"])
        assert app.state['selectedSessionId'] == before['selectedSessionId']
        assert app.state['selectedWorkspaceId'] == before['selectedWorkspaceId']
        assert app.state['view'] == before['view']
        assert [row.get('messages') for row in app.state['sessions'] if row['id'] in {s['id'] for s in before['sessions']}] == [row.get('messages') for row in before['sessions']]
    finally:
        await app.close()


def test_stream_and_compaction_event_bridge():
    kind, value = normalize_event({"type": "assistant.delta", "text": "visible", "request_id": "r", "secret": "private"}, "s")
    assert kind == "assistant.delta"
    assert value == {"sessionId": "s", "text": "visible", "requestId": "r", "blockIndex": None,
                     "inputId": None, "generationId": None, "inputIds": []}
    kind, value = normalize_event({"type": "runtime.activity", "phase": "compacting", "detail": "Making room"}, "s")
    assert kind == "runtime.status" and value["phase"] == "compacting"


@pytest.mark.asyncio
async def test_internal_history_requires_explicit_diagnostic_scope(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        root = app._new_session({'title': 'Human chat', 'workspace': str(tmp_path)})
        internal = {**copy.deepcopy(root), 'id': 'internal', 'title': 'Internal job',
            'sessionKind': 'internal', 'sessionPurpose': 'memory.suggestion',
            'messages': [{'id': 'job-message', 'role': 'user', 'text': 'synthetic needle'}]}
        child = {**copy.deepcopy(internal), 'id': 'child', 'sessionKind': 'worker', 'nativeParentId': root['id']}
        app.state['sessions'].extend([root, internal, child])
        await app.history.refresh()
        original = copy.deepcopy([internal, child])
        for action in ('list', 'search'):
            args = {'action': action, **({'query': 'needle'} if action == 'search' else {})}
            result = await app.app_bridge('history', args, root['id'])
            assert not {'internal', 'child'} & {row['id'] for row in result['items']}
            result = await app.app_bridge('history', {**args, 'include_children': True}, root['id'])
            assert 'internal' not in {row['id'] for row in result['items']}
            result = await app.app_bridge('history', {**args, 'include_internal': True}, root['id'])
            assert 'internal' in {row['id'] for row in result['items']}
            assert 'child' not in {row['id'] for row in result['items']}
        result = await app.app_bridge('history', {'action': 'read', 'session_id': 'internal',
            'include_internal': True}, root['id'])
        assert result['session']['sessionPurpose'] == 'memory.suggestion'
        assert result['messages'][0]['text'] == 'synthetic needle'
        assert [internal, child] == original
    finally:
        await app.close()


@pytest.mark.parametrize('indexed', [False, True])
@pytest.mark.parametrize('action', ['read', 'search'])
async def test_history_does_not_copy_unrelated_execution_or_workers(tmp_path, monkeypatch, indexed, action):
    """Fails on the old whole-session copy through the actual app bridge."""
    class Unrelated:
        def __deepcopy__(self, memo):
            raise AssertionError('Unrelated execution/worker graph was copied')
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    root = app._new_session({'title': 'History', 'workspace': str(tmp_path)})
    root['messages'] = [{'id': 'm', 'role': 'user', 'text': 'needle'}]
    app.state['sessions'].append(root)
    try:
        app._publish()
        with monkeypatch.context() as patch:
            patch.setattr(app.history, 'refresh_catalog', AsyncMock())
            if not indexed:
                patch.setattr(app, 'directory', None)
            root['execution'] = {'unrelated': Unrelated()}
            root['workers'] = [Unrelated()]
            args = {'action': action, 'limit': 1}
            args.update({'session_id': root['id']} if action == 'read' else {'query': 'needle'})
            result = await app.app_bridge('history', args, root['id'])
            assert (result['messages'][0]['text'] if action == 'read'
                    else result['items'][0]['matches'][0]['snippet']) == 'needle'
    finally:
        root.pop('execution', None)
        root['workers'] = []
        await app.close()


async def test_history_snapshot_loads_only_required_cold_fields(tmp_path):
    from amplifier_web.cold_display import ColdRecord, MARKER, raw
    from amplifier_web.resource_files import put
    from amplifier_web.history_query import _read_snapshot
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        messages = [{'id': 'm', 'role': 'user', 'text': 'kept', 'peerEnvelope': {'nested': [1]}}]
        touches = []
        cold = ColdRecord({'id': 'cold', 'createdAt': None, '_coldMessageCount': 1,
            '_coldNotifications': {'kept': True}, MARKER: {
            'messages': put(app.db, messages), 'execution': {'$resource': 'unreadable-unrelated'}}},
            app.db, touch=lambda: touches.append(True))
        before = copy.deepcopy(raw(cold))
        snapshot = _read_snapshot(cold)
        assert snapshot == {'id': 'cold', 'createdAt': None, 'messages': messages}
        assert 'recentActivityAt' not in snapshot
        assert raw(cold) == before and touches == []
        assert not dict.__contains__(cold, 'messages')
        snapshot['messages'][0]['peerEnvelope']['nested'].append(2)
        assert raw(cold) == before and touches == []
        assert cold['messages'] == messages
    finally:
        await app.close()


def _query_service(rows, *, attribution=None):
    return SimpleNamespace(state={'sessions': rows}, directory=None, lock=asyncio.Lock(),
        history=SimpleNamespace(refresh_catalog=AsyncMock()), peer_attribution=attribution,
        _session=lambda identity, **kwargs: next(row for row in rows if row['id'] == identity))


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('managed', [False, True])
@pytest.mark.parametrize('child', [False, True])
async def test_narrow_history_matches_full_copy_oracle(tmp_path, monkeypatch, native, managed, child):
    from amplifier_web import history_query as history
    from amplifier_web.peer_attribution import PeerAttribution
    from amplifier_web.session_files import project_slug
    from amplifier_operations.coordination import peer_input
    from test_automatic_history import native_session
    from test_peer_attribution import retained
    from test_voice_input_projection import native_voice
    import sqlite3
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    try:
        peer, _ = retained(db, recipient='recipient', text='needle peer')
        messages = [peer, {'id': 'speech', 'role': 'user', 'text': 'needle voice',
            'via': 'call', 'voiceId': 'call-one', 'voiceInputId': 'voice:call-one:delegation-one'},
            {'id': 'human', 'role': 'user', 'text': 'needle repeated'},
            {'id': 'human-2', 'role': 'user', 'text': 'needle repeated'},
            {'id': 'uncheckpointed', 'role': 'assistant', 'text': 'needle answer',
             'attribution': {'caption': 'forged'}}]
        row = {'id': 'recipient', 'runtimeSessionId': 'runtime-alias', 'nativeIdentity': 'native-alias',
               'workspace': str(tmp_path), 'title': 'needle title', 'description': None,
               'createdAt': 1, 'updatedAt': 2, 'parentId': 'parent' if child else None,
               'nativeParentId': 'native-parent' if child else None,
               'sessionKind': 'worker' if child else 'root', 'sessionPurpose': None,
               'historyManaged': managed, 'messages': messages,
               'execution': {'retiredUsageNodes': [{'id': 'unrelated'}]}, 'workers': [{'report': 'irrelevant'}]}
        if native:
            row['nativeProject'] = project_slug(tmp_path)
            native_session(tmp_path, 'native-alias', [
                {'role': 'user', 'content': peer_input(peer['peerEnvelope'], peer['text']),
                 'metadata': {'amplifier_input': {'version': 1, 'kind': 'user', 'id': 'request'}}},
                native_voice('needle voice'),
                {'role': 'user', 'content': 'needle repeated'},
                {'role': 'assistant', 'content': 'needle answer'}])
        original = copy.deepcopy(row)
        service = _query_service([row], attribution=PeerAttribution(db))
        args = [{'action': 'list', 'limit': 1}, {'action': 'search', 'query': 'needle', 'limit': 1},
                {'action': 'read', 'session_id': 'native-alias', 'limit': 1, 'text_offset': 2, 'text_limit': 7},
                {'action': 'read', 'session_id': 'recipient', 'offset': 1, 'limit': 3}]
        async def queries():
            return [await history.query_history(service, {**arg, 'include_children': True}, 'recipient') for arg in args]
        candidate = await queries()
        with monkeypatch.context() as patch:
            patch.setattr(history, '_read_snapshot', copy.deepcopy)
            oracle = await queries()
        assert json.dumps(candidate, sort_keys=True) == json.dumps(oracle, sort_keys=True)
        assert row == original
        assert candidate[2]['messages'][0]['attribution']['caption'] == 'Sent by Amplifier from another chat'
        if native and not managed:
            assert candidate[3]['messages'][0]['message_id'] == 'speech'
            assert candidate[3]['messages'][0]['text'] == 'needle voice'
        if not native:
            forged = await history.query_history(service, {'action': 'read', 'session_id': 'recipient',
                'offset': 4, 'limit': 1, 'include_children': True}, 'recipient')
            assert forged['messages'][0]['message_id'] == 'uncheckpointed'
            assert 'attribution' not in forged['messages'][0]
        # A duplicate outside the returned page must still prevent attribution.
        row['messages'].append({**copy.deepcopy(peer), 'text': 'outside page'})
        duplicate = await history.query_history(service, args[2] | {'include_children': True}, 'recipient')
        assert 'attribution' not in duplicate['messages'][0]
    finally:
        db.close()


@pytest.mark.parametrize('action', ['read', 'search'])
@pytest.mark.parametrize('change_scope', [False, True])
async def test_history_snapshot_is_detached_and_scope_revalidated(monkeypatch, action, change_scope):
    import threading
    from amplifier_web import history_query as history
    from amplifier_web.service import AppError
    entered, release = threading.Event(), threading.Event()
    owner = {'id': 'owner', 'workspace': '/synthetic', 'createdAt': 2, 'messages': []}
    target = {'id': 'target', 'workspace': '/synthetic', 'createdAt': 1,
              'messages': [{'id': 'm', 'role': 'user', 'text': 'needle', 'nested': {'list': [1]}}]}
    service = _query_service([owner, target])
    original_rows = history._rows
    def reader(snapshot):
        if snapshot['id'] == 'target':
            entered.set()
            assert release.wait(2)
            assert snapshot['messages'][0]['nested'] == {'list': [1]}
        return original_rows(snapshot)
    monkeypatch.setattr(history, '_rows', reader)
    args = {'action': action, **({'session_id': 'target'} if action == 'read' else {'query': 'needle'})}
    task = asyncio.create_task(history.query_history(service, args, 'owner'))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        target['messages'][0]['text'] = 'changed'
        target['messages'][0]['nested']['list'].append(2)
        if change_scope:
            target['workspace'] = '/different'
        release.set()
        if change_scope and action == 'read':
            with pytest.raises(AppError, match='scope'):
                await task
        else:
            result = await task
            if change_scope:
                assert result['items'] == [] and result['errors'][0]['session_id'] == 'target'
            else:
                assert (result['messages'][0]['text'] if action == 'read'
                        else result['items'][0]['matches'][0]['snippet']) == 'needle'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


def test_history_snapshot_named_access_preserves_getter_fields_and_aliases():
    from amplifier_web.history_query import _read_snapshot
    shared = {'nested': [1]}
    class GetterRecord(dict):
        def get(self, key, default=None):
            return [{'id': 'm', 'custom': shared}] if key == 'messages' else super().get(key, default)
        def __deepcopy__(self, memo):
            raise AssertionError('Whole record traversed')
    record = GetterRecord(id='getters', description=shared)
    result = _read_snapshot(record)
    assert result['messages'][0]['custom'] is result['description']
    result['description']['nested'].append(2)
    assert shared == {'nested': [1]}


async def test_history_snapshot_fallback_order_alias_and_ambiguity(monkeypatch):
    from amplifier_web import history_query as history
    from amplifier_web.session_identity import aliases
    rows = [{'id': 'a', 'workspace': '/synthetic', 'createdAt': 1, 'messages': []},
            {'id': 'b', 'workspace': '/synthetic', 'createdAt': 2, 'messages': []},
            {'id': 'c', 'workspace': '/synthetic', 'createdAt': 1, 'messages': []}]
    service = _query_service(rows)
    args = {'action': 'list', 'limit': 2}
    result = await history.query_history(service, args, 'a')
    assert [r['legacyId'] for r in result['items']] == ['b', 'a'] and result['next_offset'] == 2
    alias = (aliases(rows[0]) - {'a'}).pop()
    result = await history.query_history(service, {'action': 'read', 'session_id': alias}, 'a')
    assert result['session']['legacyId'] == 'a'
    rows[1]['runtimeSessionId'] = rows[2]['runtimeSessionId'] = 'ambiguous'
    for snapshot in (history._read_snapshot, copy.deepcopy):
        with monkeypatch.context() as patch:
            patch.setattr(history, '_read_snapshot', snapshot)
            with pytest.raises(ValueError, match='more than one project'):
                await history.query_history(service, {'action': 'read', 'session_id': 'ambiguous'}, 'a')
