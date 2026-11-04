"""Retained catalogs do not require one app/worker handoff per immutable file."""
import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from amplifier_web import resource_files as files, storage_migration


def setup(tmp_path, count):
    db = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    roots = [files.put(db, {'id': i, 'payload': 'synthetic' * 64}) for i in range(count)]
    nested = files.put(db, {'nested': True})
    parent = files.put(db, {'payload': 'x' * 2_000_000, 'nested': nested})
    garbage = files.put(db, {'obsolete': True})
    db.commit()
    # Duplicate roots must not consume extra reads within or between batches.
    state = {'roots': roots + roots + [parent]}
    return SimpleNamespace(db=db, _state=state, closed=False, lock=asyncio.Lock()), roots + [parent, nested], garbage


def close(app):
    if getattr(app, '_resource_scan', None):
        app._resource_scan.close()
    app.db.close()


@pytest.mark.parametrize('count,max_handoffs', [(1, 2), (2048, 40)])
async def test_cold_and_warm_catalogs_use_bounded_worker_handoffs(tmp_path, monkeypatch, count, max_handoffs):
    app, retained, garbage = setup(tmp_path, count)
    original = files.settle_worker
    handoffs = []
    owner = threading.get_ident()
    threads = []
    read = files.ReferenceEdges.read

    def inspect(cache, source):
        assert not app.lock.locked() and not app.db.in_transaction
        threads.append(threading.get_ident())
        return read(cache, source)

    async def settle(function, *args):
        if function.__name__ == 'inspect':
            handoffs.append(1)
        return await original(function, *args)

    monkeypatch.setattr(files.ReferenceEdges, 'read', inspect)
    monkeypatch.setattr(files, 'settle_worker', settle)
    try:
        for iteration in range(2):
            before = len(handoffs)
            await storage_migration._maintenance_async(app)
            assert len(handoffs) - before <= max_handoffs
            assert not app._resource_scan_status['blocked']
            assert {row[0] for row in app.db.execute('SELECT id FROM state_resources')} == {ref['$resource'] for ref in retained}
        assert app._resource_edges.misses == count + 2
        assert app._resource_edges.hits == count + 2
        assert threads and owner not in threads
        assert not (files.root(app.db) / (garbage['$resource'] + '.json')).exists()
    finally:
        close(app)


async def test_cancelled_batch_settles_while_application_and_writer_remain_available(tmp_path, monkeypatch):
    app, retained, garbage = setup(tmp_path, 96)
    original = files.ReferenceEdges.read
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def inspect(cache, source):
        entered.set()
        assert release.wait(5)
        try:
            return original(cache, source)
        finally:
            finished.set()

    monkeypatch.setattr(files.ReferenceEdges, 'read', inspect)
    task = asyncio.create_task(storage_migration._maintenance_async(app))
    competitor = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with asyncio.timeout(1):
            async with app.lock:
                competitor.execute('BEGIN IMMEDIATE')
                competitor.rollback()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert app.db.execute('SELECT COUNT(*) FROM state_resources').fetchone()[0] == len(retained) + 1
        assert (files.root(app.db) / (garbage['$resource'] + '.json')).exists()
        assert not app.lock.locked() and not app.db.in_transaction
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        competitor.close()
        close(app)
