"""Shared live roots cost one visit per container without weakening collection."""
import asyncio
import threading
from types import SimpleNamespace
import sqlite3

import pytest

from amplifier_web import resource_files as files, storage_migration


class ObservedRow(dict):
    reads = 0

    def values(self):
        self.reads += 1
        return super().values()


def projections(count):
    rows = [ObservedRow(status='completed', text='retained body', values=[i, i + 1])
            for i in range(count)]
    # Different presentation containers borrow the same nested mutable bodies.
    return rows, [[{'result': row} for row in rows] for _ in range(4)]


@pytest.mark.parametrize('count', [200, 70_000])
def test_shared_live_containers_are_read_once_above_the_old_dedup_boundary(count):
    rows, views = projections(count)
    rows[0]['keep'] = {'$resource': 'a' * 64}
    rows[-1]['cycle'] = rows[0]
    assert set(files.references(views)) == {'a' * 64}
    assert sum(row.reads for row in rows) == count


def test_distinct_container_limit_still_fails_closed():
    with pytest.raises(ValueError, match='traversal limit'):
        set(files.references([{} for _ in range(1_000_000)]))


@pytest.mark.parametrize('count', [200, 70_000])
async def test_stable_collection_reads_live_roots_only_for_mark_and_prune(count, tmp_path):
    db = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    child = files.put(db, {'retained': 'child'})
    parent = files.put(db, {'child': child})
    orphan = files.put(db, {'obsolete': True})
    db.commit()
    rows, views = projections(count)
    rows[0]['keep'] = parent
    app = SimpleNamespace(db=db, _state={'views': views}, closed=False, lock=asyncio.Lock())
    try:
        await storage_migration._maintenance_async(app)
        assert {row[0] for row in db.execute('SELECT id FROM state_resources')} == {
            parent['$resource'], child['$resource']}
        assert storage_migration.resource(db, parent['$resource']) == {'child': child}
        assert storage_migration.resource(db, child['$resource']) == {'retained': 'child'}
        assert not (files.root(db) / (orphan['$resource'] + '.json')).exists()
        assert not app._resource_scan_status['blocked']
        assert not app.lock.locked() and not db.in_transaction
        # Shared bodies need an initial mark and a fresh prune-boundary read.
        # Rewalking without yielding between those final reads adds no witness.
        assert sum(row.reads for row in rows) == 2 * count
    finally:
        if getattr(app, '_resource_scan', None):
            app._resource_scan.close()
        db.close()


async def test_large_shared_live_graph_collects_while_nested_roots_change(tmp_path, monkeypatch):
    db = sqlite3.connect(tmp_path / 'app.sqlite3', timeout=0)
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    old = files.put(db, {'old': 'snapshot'})
    child = files.put(db, {'adopted': 'transitive child'})
    adopted = files.put(db, {'child': child})
    orphan = files.put(db, {'obsolete': True})
    db.commit()
    rows, views = projections(100_000)
    mutable = {'nested': [dict(old)]}
    app = SimpleNamespace(db=db, _state={'views': views, 'selected': mutable},
                          closed=False, lock=asyncio.Lock())
    entered, release = threading.Event(), threading.Event()
    original = files.detached_references

    def inspect(source):
        assert not app.lock.locked(), 'Immutable bodies must not hold the mutation lock'
        if source.identity == old['$resource']:
            entered.set()
            assert release.wait(5)
        return original(source)

    monkeypatch.setattr(files, 'detached_references', inspect)
    task = asyncio.create_task(storage_migration._maintenance_async(app))
    waiting = asyncio.create_task(asyncio.to_thread(entered.wait, 5))
    try:
        await asyncio.wait({task, waiting}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        assert entered.is_set(), 'A valid large shared graph must reach body inspection'
        # Simulate ordinary progress while the worker reads immutable bodies.
        # Identities and list lengths stay fixed; no cross-capture cache is safe.
        identity = id(mutable['nested'][0])
        async with asyncio.timeout(1):
            async with app.lock:
                mutable['nested'][0].clear()
                mutable['nested'][0].update(adopted)
                assert id(mutable['nested'][0]) == identity
                assert not db.in_transaction
        release.set()
        await task
        retained = {row[0] for row in db.execute('SELECT id FROM state_resources')}
        assert retained == {ref['$resource'] for ref in (old, adopted, child)}
        assert not app._resource_scan_status['blocked']
        assert not (files.root(db) / (orphan['$resource'] + '.json')).exists()
        for ref in (old, adopted, child):
            assert (files.root(db) / (ref['$resource'] + '.json')).is_file()
        # The old root was retained conservatively for one pass, not forever.
        await storage_migration._maintenance_async(app)
        assert {row[0] for row in db.execute('SELECT id FROM state_resources')} == {
            adopted['$resource'], child['$resource']}
        assert not (files.root(db) / (old['$resource'] + '.json')).exists()
        assert not app.lock.locked() and not db.in_transaction
    finally:
        release.set()
        await asyncio.gather(task, waiting, return_exceptions=True)
        if getattr(app, '_resource_scan', None):
            app._resource_scan.close()
        db.close()
