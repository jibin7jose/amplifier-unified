"""Keep source ownership explicit before changing display-page preparation."""
import asyncio
import copy
import sqlite3
import threading
from types import SimpleNamespace

import pytest
import pytest_asyncio

from amplifier_web import automatic_history, cold_display, transcript_windows
from amplifier_web.resource_files import put
from test_transcript_windows import service_for, transcript


@pytest_asyncio.fixture(params=['native', 'cold', 'resident'])
async def window_source(request, tmp_path, monkeypatch):
    kind = request.param
    monkeypatch.setattr(automatic_history, 'amplifier_home', lambda: tmp_path)
    monkeypatch.setattr(automatic_history, 'read_transcript',
                        lambda *a, **k: pytest.fail('Display must not load canonical history'))
    monkeypatch.setattr(cold_display, 'load',
                        lambda *a, **k: pytest.fail('Display must not hydrate cold fields'))
    service, session = service_for(tmp_path, managed=kind == 'native')
    rows = [{'id': str(i), 'role': 'user', 'text': f'Message {i} ☃',
             'attachments': [{'id': f'a{i}', 'details': {'labels': ['original']}}]}
            for i in range(3)]
    session['messages'] = rows
    db = None
    native_path = None
    if kind == 'native':
        native_path = transcript(tmp_path, [{'role': row['role'], 'content': row['text']}
                                           for row in rows]) / 'transcript.jsonl'
    elif kind == 'cold':
        db = sqlite3.connect(tmp_path / 'state.sqlite3')
        db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT)')
        reference = put(db, rows)
        session = cold_display.ColdRecord({**session, '_coldFields': {
            'messages': reference, 'execution': {'$resource': 'unread'}}}, db)
        dict.pop(session, 'messages')
        service.db = db

    def lookup(identity, *, hydrate=True):
        assert identity == 'app' and hydrate is False
        return session

    service._session = lookup
    source = SimpleNamespace(kind=kind, service=service, session=session, rows=rows,
                             windows=service.history.windows, db=db, native_path=native_path)
    try:
        yield source
    finally:
        await source.windows.close()
        if db is not None:
            db.close()


@pytest.mark.asyncio
async def test_source_variants_preserve_their_display_contract(window_source):
    source = window_source
    original = copy.deepcopy(dict(dict.items(source.session)))
    native_bytes = source.native_path.read_bytes() if source.native_path else None
    first = await source.windows.read('app', limit=2)
    older = await source.windows.read('app', before=first['before'], limit=2)
    assert [row['text'] for row in older['items'] + first['items']] == [row['text'] for row in source.rows]
    assert first['hasEarlier'] and not older['hasEarlier']
    for page in (first, older):
        assert page['mode'] == 'cursor' and page['total'] is None and page['offset'] is None
        assert not page['unavailableRanges']
        assert not any(key.startswith('_') for key in page)
    if source.kind == 'native':
        assert first['coverage']['kind'] == 'native-saved'
        assert first['validation'] == 'window' and first['globalInputUniqueness'] == 'unknown'
        assert all(row['displayOnly'] and row['id'].startswith('native-window:')
                   and 'attachments' not in row for row in first['items'])
        restored = await source.windows.read('app', before=first['restoreCursor'], limit=2)
        assert restored == first
        assert source.native_path.read_bytes() == native_bytes
    else:
        assert older['items'] + first['items'] == source.rows
        assert first['coverage']['kind'] == 'saved-web'
        assert first['coverage']['historyComplete'] is False
        if source.kind == 'cold':
            assert first['coverage']['validation'] == 'window'
            assert not dict.__contains__(source.session, 'messages')
        else:
            assert 'validation' not in first['coverage']
    assert dict(dict.items(source.session)) == original


@pytest.mark.asyncio
async def test_changed_owner_during_preparation_never_retains_or_returns_old_page(window_source, monkeypatch):
    source = window_source
    entered, release = threading.Event(), threading.Event()
    to_thread = asyncio.to_thread

    async def hold_preparation(function, *args, **kwargs):
        # Hold the completed page before returning it to the owner thread.
        # Native/cold physical reads still run normally, as does real copying/sizing.
        if function.__name__ != 'prepare':
            return await to_thread(function, *args, **kwargs)

        def held():
            result = function(*args, **kwargs)
            entered.set()
            assert release.wait(5), 'Owner mutation did not release preparation'
            return result

        return await to_thread(held)

    monkeypatch.setattr(transcript_windows.asyncio, 'to_thread', hold_preparation)
    task = asyncio.create_task(source.windows.read('app', limit=2))
    try:
        assert await asyncio.wait_for(to_thread(entered.wait, 5), 6)
        async with source.service.lock:
            if source.kind == 'native':
                source.session['nativeIdentity'] = 'rebound'
            elif source.kind == 'cold':
                replacement = copy.deepcopy(source.rows)
                replacement[-1]['text'] = 'Current saved source'
                dict.get(source.session, '_coldFields')['messages'] = put(source.db, replacement)
            else:
                # Same list length and IDs; only the existing owner fence detects this.
                source.rows[-1]['attachments'][0]['details']['labels'].append('current')
                source.windows.invalidate({'app'})
        release.set()
        with pytest.raises(ValueError, match='source changed'):
            await task
        assert not source.windows.pages and source.windows.bytes == 0
        assert not source.windows.initial
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_resident_nested_mutation_does_not_rewrite_retained_page(tmp_path):
    service, session = service_for(tmp_path, managed=False)
    windows = service.history.windows
    original = copy.deepcopy(session['messages'])
    try:
        first = await windows.read('app')
        async with service.lock:
            session['messages'][0]['attachments'][0]['id'] = 'changed'
            windows.invalidate({'app'})
        retained, _ = windows.pages[('app', first['windowKey'])]
        assert retained['items'] == original
        assert first['items'] == original
        current = await windows.read('app')
        assert current['items'] == session['messages']
        assert current['windowKey'] != first['windowKey']
    finally:
        await windows.close()
