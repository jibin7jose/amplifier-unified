"""One-time, rollback-preserving storage upgrade and bounded receipt retention."""
import asyncio
import json
import logging
import sqlite3
import time

from .resource_files import collect, put, remove_files, root
from .state_storage import resource

RESULT_LIMIT = 200
RESULT_BYTES = 32_000_000
RESULT_DAYS = 30
BACKGROUND_PRUNE_LIMIT = 1024
BACKGROUND_ROOT_CATCHUPS = 3
BACKGROUND_EDGE_BATCH = 64


def trim_operations(db, state):
    """Keep small at-most-once receipts forever, recent full results within budget."""
    retained = size = 0
    expired = set()
    # Stream rows; an old installation can have gigabytes of full results.
    cursor = db.execute("SELECT id FROM smart_tool_operations WHERE json_extract(value,'$.resultExpired') IS NOT 1 ORDER BY json_extract(value,'$.updatedAt') DESC")
    for (identity,) in cursor:
        text = db.execute('SELECT value FROM smart_tool_operations WHERE id=?', (identity,)).fetchone()[0]
        operation = json.loads(text)
        if operation.get('status') in {'running', 'queued'}:
            continue
        fields = {key: operation[key] for key in ('result', 'arguments') if key in operation}
        if not fields:
            continue
        cost = sum(value.get('bytes', 0) if isinstance(value, dict) and '$resource' in value
                   else len(json.dumps(value).encode()) for value in fields.values())
        if (retained < RESULT_LIMIT and size + cost <= RESULT_BYTES
                and operation.get('updatedAt', time.time()) >= time.time() - RESULT_DAYS * 86400):
            retained += 1
            size += cost
            # Older receipts embedded full results. Externalize only what survives.
            for key, value in fields.items():
                if not (isinstance(value, dict) and '$resource' in value) and len(json.dumps(value)) > 16_000:
                    operation[key] = put(db, value)
        else:
            for key in fields:
                operation.pop(key, None)
            operation['resultExpired'] = True
            operation['detail'] = 'Full result retention expired. The execution receipt remains; work is never replayed.'
            expired.add(identity)
        encoded = json.dumps(operation)
        if encoded != text:
            db.execute('UPDATE smart_tool_operations SET value=? WHERE id=?', (encoded, identity))
    smart = state.get('smartTools', {})
    for item in [*smart.get('operations', []), smart.get('inspectedOperation', {})]:
        row = db.execute('SELECT value FROM smart_tool_operations WHERE id=?', (item.get('id'),)).fetchone()
        if row:
            item.clear()
            item.update(json.loads(row[0]))


def upgrade(service):
    """Runs before accepting connections; keep the complete old DB for rollback."""
    db, state = service.db, service.state
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_layout'").fetchone():
        return
    existing = db.execute('SELECT 1 FROM state WHERE id=1').fetchone()
    if existing:
        folder = service.data_dir / 'backups' / 'shared-storage-v1'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = folder / 'app.sqlite3'
        if not backup.exists():
            temporary = folder / 'app.sqlite3.pending'
            with sqlite3.connect(temporary) as target:
                db.backup(target)
            temporary.chmod(0o600)
            temporary.replace(backup)
    from .session_projection import migrate
    migrate(service.data_dir, state)
    for item in state.get('canvasArtifacts', []):
        reference = item.get('body', {}).get('$resource')
        if not reference:
            continue
        body = resource(db, reference)
        if 'mcp' in body:
            item['mcpState'] = put(db, body['mcp'])
            item['body'] = put(db, {key: value for key, value in body.items() if key != 'mcp'})
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='smart_tool_operations'").fetchone():
        trim_operations(db, state)
    from .state_storage import normalize_state
    normalize_state(state, db)
    stale = collect(db, state)
    # Preserve existing IDs referenced by history, even when their original
    # serialization used different whitespace/key order.
    directory = root(db)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    from .host.storage import SessionStore
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    for identity, text in db.execute('SELECT id,value FROM state_resources'):
        value = json.loads(text)
        if isinstance(value, dict) and set(value) == {'$blob'}:
            continue
        SessionStore._atomic(directory / (identity + '.json'), text)
        db.execute('UPDATE state_resources SET value=? WHERE id=?', (json.dumps({'$blob': identity}), identity))
    from .session_projection import persist
    saved = persist(service.data_dir, state, service._view_cache)
    from .state_records import checkpoint
    checkpoint(db, saved)
    db.execute('CREATE TABLE storage_layout(version INTEGER NOT NULL)')
    db.execute('INSERT INTO storage_layout VALUES(1)')
    db.commit()
    remove_files(db, stale)
    # Startup migration is offline. Do not shrink multi-GB databases while the
    # server is accepting requests; ordinary retention reuses freed pages.
    if existing:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('VACUUM')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')


def maintenance(service):
    """Admit at most one owned sweep after committing the state snapshot.

    Synchronous callers retain the old synchronous boundary. Live services defer
    body traversal to workers and revalidate before taking deletion authority.
    """
    now = time.monotonic()
    if getattr(service, 'closed', False) or getattr(service, 'backup_in_progress', False):
        return
    task = getattr(service, '_storage_maintenance_task', None)
    if task is not None and not task.done():
        return
    if now - getattr(service, '_last_storage_sweep', now - 61) < 60:
        return
    service._last_storage_sweep = now
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _maintenance_sync(service)
    else:
        service._storage_maintenance_task = loop.create_task(_maintenance_async(service))


def _roots(service):
    # A bound client is only a projection: it hides shared presentation fields
    # and all other clients. Retention must see every retained in-memory root.
    clients = getattr(service, 'clients', None)
    return [service._state, *clients.records.values()] if clients else service._state


def _prune(service, marked, *, limit=None):
    from .resource_files import prune_marked, sweep_unindexed_locked
    if marked is None:
        scan = getattr(service, '_resource_scan', None)
        if scan is not None:
            scan.close()
            service._resource_scan = None
        service._resource_scan_status = {'blocked': True, 'complete': False, 'inspected': 0, 'removed': 0}
        service.db.rollback()
        return
    service._resource_scan, service._resource_scan_status = sweep_unindexed_locked(
        service.db, getattr(service, '_resource_scan', None))
    stale = prune_marked(service.db, marked, limit=limit)
    service.db.commit()
    return stale


def _finish(service, marked):
    stale = _prune(service, marked)
    if stale is not None:
        remove_files(service.db, stale)


async def _finish_async(service, marked):
    from .resource_files import remove_files_async
    stale = _prune(service, marked, limit=BACKGROUND_PRUNE_LIMIT)
    if stale is not None:
        removed = await remove_files_async(service.db, stale)
        service._resource_scan_status['indexedRemoved'] = len(stale)
        return {'indexedPruned': len(stale),
                'indexedFilesRemoved': (removed or {}).get('filesRemoved', 0),
                'indexedBytesRemoved': (removed or {}).get('bytesRemoved', 0),
                'unindexedFilesRemoved': service._resource_scan_status.get('removed', 0)}
    return {}


def _maintenance_sync(service):
    from .resource_files import marked_references
    service.db.execute('BEGIN IMMEDIATE')
    try:
        _finish(service, marked_references(service.db, _roots(service)))
    except BaseException:
        service.db.rollback()
        raise


async def _maintenance_async(service):
    """Optimistic immutable-resource walk; only capture and prune hold app.lock.

    No writer transaction or application lock spans durable JSON/body reading.
    Before deletion, recheck every root and source index under the writer boundary.
    Bounded file deletion retains exclusion until its worker has settled.
    A bounded catch-up retains newly added roots as well as earlier roots. A
    still-changing, unavailable or uncertain graph defers the entire sweep.
    """
    from .resource_files import references, detach, ReferenceEdges, settle_worker
    from . import resource_roots
    from .performance_diagnostics import measure, count

    edges = getattr(service, '_resource_edges', None)
    if edges is None:
        edges = service._resource_edges = ReferenceEdges()
    before = (edges.hits, edges.misses, edges.bytes_read)
    deferred = None
    reason = None
    catchups = 0
    removed = {}
    started = time.monotonic()
    outcome = 'interrupted'
    stage = 'root-capture-unavailable'

    def live_roots():
        state = _roots(service)
        rows = state if isinstance(state, list) else [state]
        if any(isinstance(row, dict) and row.get('_viewRecoveryPending') for row in rows):
            return None
        # Mutable objects stay on the event-loop owner. This walks references
        # without decoding JSON or hydrating ColdRecords. A short time/field
        # admission limit here made otherwise valid large installations defer
        # forever; the canonical cycle/traversal guard still fails closed.
        with measure(service, 'storage.gc.roots.live', synchronous=True):
            return set(references(state))

    def durable_roots(snapshot):
        with measure(service, 'storage.gc.roots.durable', synchronous=True):
            return resource_roots.references(snapshot)

    def inspect(batch):
        with measure(service, 'storage.gc.body', synchronous=True):
            return {child for source in batch.values() for child in edges.read(source)}

    def unavailable():
        return service.closed or getattr(service, 'backup_in_progress', False)

    try:
        async with service.lock:
            if unavailable():
                return
            with measure(service, 'storage.gc.roots', synchronous=True):
                initial = live_roots()
                snapshot = resource_roots.capture(service.db)
        stage = 'durable-roots-unavailable'
        saved = await settle_worker(durable_roots, snapshot) if initial is not None else set()
        if initial is not None:
            initial.update(saved)
        pending, sources = list(initial or ()), {}
        valid = initial is not None
        if not valid:
            reason = 'presentation-recovery-pending'
        while valid:
            while pending and valid:
                identities = set()
                while pending and len(identities) < BACKGROUND_EDGE_BATCH:
                    identity = pending.pop()
                    if identity not in sources:
                        identities.add(identity)
                if not identities:
                    continue
                try:
                    async with service.lock:
                        if unavailable():
                            return
                        batch = {identity: detach(service.db, identity) for identity in identities}
                    # One handoff for a small metadata batch, not one per retained
                    # file. Bodies still stay outside the app/writer boundary;
                    # the complete mark is revalidated before any deletion.
                    pending.extend(await settle_worker(inspect, batch))
                    sources.update(batch)
                except (OSError, ValueError, TypeError, KeyError, RecursionError):
                    valid, reason = False, 'resource-body-unavailable'
            if not valid:
                break
            # A save can replace a large immutable session snapshot while we
            # walk. Extend the conservative mark, including the new closure,
            # instead of requiring an idle interval for the entire first walk.
            stage = 'root-capture-unavailable'
            async with service.lock:
                if unavailable():
                    return
                current = live_roots()
                final_snapshot = resource_roots.capture(service.db)
            if current is None:
                valid, reason = False, 'presentation-recovery-pending'
                break
            if final_snapshot != snapshot:
                stage = 'durable-roots-unavailable'
                saved = await settle_worker(durable_roots, final_snapshot)
                snapshot = final_snapshot
            pending = list((current | saved) - sources.keys())
            if not pending:
                break
            if catchups >= BACKGROUND_ROOT_CATCHUPS:
                valid, reason = False, 'root-catchup-limit'
                break
            catchups += 1
        async with service.lock:
            if unavailable():
                return
            service.db.execute('BEGIN IMMEDIATE')
            try:
                with measure(service, 'storage.gc.roots', synchronous=True):
                    current = live_roots() if valid else None
                    if valid and current is None:
                        valid, reason = False, 'presentation-recovery-pending'
                    if valid and not (current | saved) <= sources.keys():
                        valid, reason = False, 'live-roots-changed'
                    if valid and resource_roots.capture(service.db) != snapshot:
                        valid, reason = False, 'durable-roots-changed'
                if valid:
                    try:
                        valid = all(detach(service.db, key) == value for key, value in sources.items())
                    except ValueError:
                        valid = False
                    if not valid:
                        reason = 'resource-index-changed'
                stage = 'file-cleanup-unavailable'
                removed = await _finish_async(service, set(sources) if valid else None)
                if not valid:
                    deferred = {'deferred': True, 'reason': reason}
                    service._resource_scan_status.update(deferred)
                else:
                    service._storage_maintenance_error = None
                    outcome = 'completed'
            except BaseException:
                service.db.rollback()
                raise
    except asyncio.CancelledError:
        outcome = 'cancelled'
        raise  # Detached readers have settled; no writer boundary is retained.
    except Exception as exc:
        service._storage_maintenance_error = type(exc).__name__
        scan = getattr(service, '_resource_scan', None)
        if scan is not None:
            scan.close()
            service._resource_scan = None
        deferred = {'deferred': True, 'reason': stage}
        service._resource_scan_status = {'blocked': True, 'complete': False,
                                        'inspected': 0, 'removed': 0, **deferred}
        logging.getLogger(__name__).warning('Storage maintenance deferred.', exc_info=True)
    finally:
        if deferred is not None:
            outcome = 'deferred'
            count(service, 'storage.gc.rootsDeferred')
        if outcome == 'completed':
            count(service, 'storage.gc.passesCompleted')
            for key in ('indexedPruned', 'indexedFilesRemoved', 'indexedBytesRemoved', 'unindexedFilesRemoved'):
                count(service, 'storage.gc.' + key, removed.get(key, 0))
        count(service, 'storage.gc.rootCatchups', catchups)
        diagnostics = getattr(service, 'performance_diagnostics', None)
        if diagnostics is not None:
            diagnostics.record_storage_collection(outcome=outcome,
                reason=deferred['reason'] if deferred else None,
                elapsed_ms=(time.monotonic() - started) * 1000,
                catchups=catchups, **removed)
        count(service, 'storage.gc.edgeHits', edges.hits - before[0])
        count(service, 'storage.gc.edgeMisses', edges.misses - before[1])
        count(service, 'storage.gc.bodyBytesRead', edges.bytes_read - before[2])


async def close_maintenance(service):
    task = getattr(service, '_storage_maintenance_task', None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        service._storage_maintenance_task = None
