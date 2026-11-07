"""Private view writes and command receipts share one durable outcome."""
from copy import deepcopy
import json
import sqlite3

import pytest

from amplifier_web.service import AppService


@pytest.mark.parametrize('action', ['view.update', 'session.select', 'canvas.visibility'])
async def test_failed_commit_keeps_private_view_receipt_and_publication_consistent(tmp_path, monkeypatch, action):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    observer = None
    queues = []
    try:
        await app.dispatch('session.create', {'title': 'First'})
        first = app._state['selectedSessionId']
        await app.dispatch('session.create', {'title': 'Second'})
        app.clients.attach('one')
        app.clients.attach('two')
        with app.clients.bind('one'):
            await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Keep this artifact'})
            app.browser_state()
            own = app.subscribe()
            queues.append(own)
        with app.clients.bind('two'):
            other = app.subscribe()
            queues.append(other)
        app.clients.save()
        app.db.commit()
        previous = deepcopy(app.clients.records['one'])
        untouched = deepcopy(app.clients.records['two'])
        revision = app._state['revision']
        args = {
            'view.update': {'patch': {'navWidth': 420}},
            'session.select': {'id': first},
            'canvas.visibility': {'open': False, 'sessionId': previous['selectedSessionId'],
                                  'canvasId': previous['canvas']['id']},
        }[action]
        database = app.db
        observer = sqlite3.connect(database.execute('PRAGMA database_list').fetchone()[2])
        stored = observer.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0]

        class FailCommit:
            def __getattr__(self, name):
                return getattr(database, name)

            def commit(self):
                # Fail after both writes, before either becomes durable.
                assert database.execute('SELECT receipt FROM commands WHERE id=?', ('retry-view',)).fetchone()
                assert database.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0] != stored
                assert own.empty() and other.empty()
                raise OSError('Fixture commit unavailable')

        with monkeypatch.context() as patch:
            patch.setattr(app, 'db', FailCommit())
            with app.clients.bind('one'), pytest.raises(OSError, match='commit unavailable'):
                await app.dispatch(action, args, command_id='retry-view', include_state=False)
        assert not database.in_transaction
        assert app.clients.records['one'] == previous
        assert app.clients.records['two'] == untouched
        assert app._state['revision'] == revision
        assert observer.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0] == stored
        assert observer.execute('SELECT receipt FROM commands WHERE id=?', ('retry-view',)).fetchone() is None
        assert own.empty() and other.empty()

        with app.clients.bind('one'):
            receipt = await app.dispatch(action, args, command_id='retry-view', include_state=False)
        assert receipt['accepted'] and receipt['revision'] == revision + 1
        assert own.get_nowait()['revision'] == receipt['revision']
        assert other.empty() and app.clients.records['two'] == untouched
        committed = observer.execute('SELECT value FROM client_views WHERE id=?', ('one',)).fetchone()[0]
        assert committed != stored
        assert app.clients.saved['one'] == committed
        durable_receipt = json.loads(observer.execute('SELECT receipt FROM commands WHERE id=?', ('retry-view',)).fetchone()[0])
        assert durable_receipt == receipt
        with app.clients.bind('one'):
            duplicate = await app.dispatch(action, args, command_id='retry-view', include_state=False)
        assert duplicate['duplicate'] and duplicate['revision'] == receipt['revision']
        assert own.empty() and other.empty()
    finally:
        for queue in queues:
            app.unsubscribe(queue)
        if observer is not None:
            observer.close()
        await app.close()
