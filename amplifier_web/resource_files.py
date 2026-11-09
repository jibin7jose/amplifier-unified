"""Immutable, content-addressed artifact files; SQLite contains only an index."""
import asyncio
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
from itertools import islice
import json
import os
import re
import stat
import time
from pathlib import Path


def root(db):
    path = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
    return Path(path).parent / 'artifacts' if path else None


@dataclass(frozen=True)
class Prepared:
    text: str
    identity: str
    size: int


def prepare(value):
    """Pure canonical preparation, safe to run without a database in a worker."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    encoded = text.encode()
    return Prepared(text, hashlib.sha256(encoded).hexdigest(), len(encoded))


def write_prepared(directory, prepared):
    """Existing file protocol; caller must retain SQLite writer exclusion."""
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (prepared.identity + '.json')
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != prepared.size:
            raise ValueError('Existing artifact file conflicts with its content identity; file preserved.')
    else:
        from .host.storage import SessionStore
        SessionStore._atomic(path, prepared.text)


def _stage(db, prepared):
    directory = root(db)
    indexed = prepared.text if directory is None else json.dumps({'$blob': prepared.identity})
    owns_transaction = not db.in_transaction
    if owns_transaction:
        db.execute('BEGIN IMMEDIATE')
    db.execute('SAVEPOINT resource_put')
    return directory, indexed, owns_transaction


def _rollback_put(db, owns_transaction):
    db.execute('ROLLBACK TO resource_put')
    db.execute('RELEASE resource_put')
    if owns_transaction:
        db.rollback()


def put(db, value):
    prepared = prepare(value)
    return put_prepared(db, prepared)


def put_prepared(db, prepared):
    """Adopt prepared bytes through the same transaction and file checks."""
    directory, indexed, owns_transaction = _stage(db, prepared)
    try:
        db.execute('INSERT OR IGNORE INTO state_resources VALUES (?, ?)', (prepared.identity, indexed))
        write_prepared(directory, prepared)
        db.execute('RELEASE resource_put')
    except BaseException:
        _rollback_put(db, owns_transaction)
        raise
    return {'$resource': prepared.identity, 'bytes': prepared.size}


async def settle_worker(function, *args):
    """Cancellation cannot release an owned write while its worker still runs."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            break
    if cancelled:
        # Retrieve any worker failure, but preserve the caller's cancellation.
        try:
            task.result()
        except BaseException:
            pass
        raise asyncio.CancelledError()
    return task.result()


async def put_prepared_async(db, prepared):
    """Owner-thread DB, detached file worker; caller holds its app lock.

    Like put(), successful return leaves commit to the caller. No DB or cursor
    crosses into the worker. Keep the caller lock until commit/rollback so GC on
    the same connection cannot enter; other connections use BEGIN IMMEDIATE.
    """
    directory, indexed, owns_transaction = _stage(db, prepared)
    try:
        db.execute('INSERT OR IGNORE INTO state_resources VALUES (?, ?)', (prepared.identity, indexed))
        await settle_worker(write_prepared, directory, prepared)
        db.execute('RELEASE resource_put')
    except BaseException:
        _rollback_put(db, owns_transaction)
        raise
    return {'$resource': prepared.identity, 'bytes': prepared.size}


async def put_async(db, value):
    prepared = await settle_worker(prepare, value)
    return await put_prepared_async(db, prepared)


@dataclass(frozen=True)
class Detached:
    directory: Path | None
    identity: str
    indexed: str


def detach(db, identity):
    """Capture an indexed read on the database owner thread, without file I/O."""
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
        raise ValueError('Invalid state resource.')
    row = db.execute('SELECT value FROM state_resources WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise ValueError('State resource is unavailable.')
    return Detached(root(db), identity, row[0])


def read_detached(source, *, max_bytes=None):
    """File, JSON and identity work without a live connection or cursor."""
    text = source.indexed
    if max_bytes is not None and len(text.encode()) > max_bytes:
        raise ValueError('Oversized artifact index.')
    value = json.loads(text)
    if isinstance(value, dict) and set(value) == {'$blob'}:
        if value['$blob'] != source.identity or source.directory is None:
            raise ValueError('Invalid artifact index')
        path = source.directory / (source.identity + '.json')
        with _open_blob(path) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or (max_bytes is not None and before.st_size > max_bytes):
                raise ValueError('Invalid or oversized artifact file.')
            encoded = stream.read(before.st_size + 1 if max_bytes is None else max_bytes + 1)
            after = os.fstat(stream.fileno())
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if len(encoded) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
                raise ValueError('Artifact changed while reading.')
        if hashlib.sha256(encoded).hexdigest() != source.identity:
            raise ValueError('Artifact content conflicts with its identity.')
        value = json.loads(encoded)
    elif hashlib.sha256(text.encode()).hexdigest() != source.identity:
        raise ValueError('Artifact content conflicts with its identity.')
    return value


_EVIDENCE = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')


def _open_blob(path):
    if os.name == 'nt':
        from .windows_read_files import open_file
        return open_file(path)
    return os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb')


def _stream_signature(stream):
    if os.name == 'nt':
        from .windows_read_files import stream_signature
        return stream_signature(stream)
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Artifact backing file is not regular.')
    return tuple(getattr(info, key) for key in _EVIDENCE)


def _blob_index(source):
    # A file pointer is tiny. Never decode an inline resource just to decide
    # whether it is a pointer (inline display bodies can be arbitrarily large).
    if (not isinstance(source.indexed, str) or not isinstance(source.identity, str)
            or not re.fullmatch('[a-f0-9]{64}', source.identity)):
        raise ValueError('Invalid artifact index.')
    if len(source.indexed) <= 128:
        def unique(pairs):
            value = {}
            for name, item in pairs:
                if name in value:
                    raise ValueError('Invalid artifact index.')
                value[name] = item
            return value
        value = json.loads(source.indexed, object_pairs_hook=unique)
        if isinstance(value, dict) and '$blob' in value:
            if value != {'$blob': source.identity} or source.directory is None:
                raise ValueError('Invalid artifact index.')
            return True
    return False


def detached_signature(source):
    """Worker-only evidence for a previously validated exact Detached read."""
    if not _blob_index(source):
        return ('inline', source.identity)
    if os.name == 'nt':
        with _open_blob(source.directory / (source.identity + '.json')) as stream:
            return _stream_signature(stream)
    info = (source.directory / (source.identity + '.json')).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Artifact backing file is not regular.')
    return tuple(getattr(info, key) for key in _EVIDENCE)


class _InlineStream:
    """Seekable bytes over an existing inline index without a full UTF-8 copy."""
    def __init__(self, text):
        self.text = text
        self.character = self.position = 0
        self.pending = b''

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.pending = b''

    def tell(self):
        return self.position

    def read(self, size):
        if not 0 <= size <= 64 * 1024:
            raise ValueError('Projection reads must be bounded.')
        chunks, remaining = [], size
        while remaining:
            if not self.pending:
                if self.character == len(self.text):
                    break
                end = min(len(self.text), self.character + 8192)
                self.pending = self.text[self.character:end].encode()
                self.character = end
            count = min(remaining, len(self.pending))
            chunks.append(self.pending[:count])
            self.pending = self.pending[count:]
            remaining -= count
            self.position += count
        return b''.join(chunks)

    def seek(self, offset):
        if type(offset) is not int or offset < 0:
            raise ValueError('Invalid projection offset.')
        if offset < self.position:
            self.character = self.position = 0
            self.pending = b''
        while self.position < offset:
            if not self.read(min(64 * 1024, offset - self.position)):
                raise ValueError('Projection offset exceeds the resource.')
        return self.position


def project_detached(source, projector):
    """Generic bounded bytes projection; no JSON/body policy or live SQLite.

    The callback receives a seekable binary stream and must bound its own
    projection memory. Its result cannot escape until the COMPLETE resource
    hash, regular/no-follow file and before/after descriptor AND path evidence
    agree. Seeking by the callback does not omit bytes from hash validation.
    """
    blob = _blob_index(source)
    path = source.directory / (source.identity + '.json') if blob else None
    signature = detached_signature(source)
    stream = _open_blob(path) if blob else _InlineStream(source.indexed)
    with stream as stream:
        if blob:
            if signature != _stream_signature(stream):
                raise ValueError('Artifact changed before projection.')
        projection = projector(stream)
        stream.seek(0)
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
            size += len(chunk)
        if digest.hexdigest() != source.identity:
            raise ValueError('Artifact content conflicts with its identity.')
        if blob:
            if (size != signature[2] or signature != _stream_signature(stream)
                    or detached_signature(source) != signature):
                raise ValueError('Artifact changed during projection.')
    return projection, signature


@dataclass(frozen=True)
class ValidatedReference:
    source: Detached
    signature: tuple
    size: int


def validate_reference(db, reference):
    """Compact evidence for an adopted file; the caller still owns commit.

    Existing-file adoption checks size, not content. Validate the COMPLETE hash
    before retaining evidence, including when this save found a pre-existing
    same-size file. Inline resources keep their original preparation path.
    """
    if not db.in_transaction:
        raise ValueError('Resource validation requires writer exclusion.')
    source = detach(db, reference['$resource'])
    if not _blob_index(source):
        return None
    _, signature = project_detached(source, lambda stream: None)
    if signature[2] != reference['bytes']:
        raise ValueError('Artifact size conflicts with its reference.')
    return ValidatedReference(source, signature, reference['bytes'])


def reuse_reference(db, evidence):
    """Recheck committed evidence under the ordinary resource writer boundary.

    A missing index/file can be prepared and adopted again. Changed file
    evidence requires full validation on that path; a conflicting index or
    non-regular file must never become a successful cached save.
    """
    owns_transaction = not db.in_transaction
    if owns_transaction:
        db.execute('BEGIN IMMEDIATE')
    try:
        if not owns_transaction:
            # A caller may have opened a deferred/read-only transaction. As
            # ordinary adoption's INSERT does, acquire writer exclusion before
            # trusting the index/file pair, without changing any resource row.
            db.execute('UPDATE state_resources SET value=value WHERE 0')
        indexed = db.execute('SELECT value FROM state_resources WHERE id=?',
                             (evidence.source.identity,)).fetchone()
        if indexed is None:
            return None
        source = Detached(root(db), evidence.source.identity, indexed[0])
        if source != evidence.source:
            raise ValueError('Saved artifact index changed; original file preserved.')
        try:
            signature = detached_signature(source)
        except FileNotFoundError:
            return None
        if signature != evidence.signature:
            return None
        if signature[2] != evidence.size:
            raise ValueError('Artifact size conflicts with its reference.')
        return {'$resource': source.identity, 'bytes': evidence.size}
    except BaseException:
        if owns_transaction:
            db.rollback()
        raise


def resolve(db, identity, value):
    if isinstance(value, dict) and set(value) == {'$blob'}:
        if value['$blob'] != identity:
            raise ValueError('Invalid artifact index')
        if os.name == 'nt':
            return read_detached(Detached(root(db), identity, json.dumps(value)))
        return json.loads((root(db) / (identity + '.json')).read_text())
    return value


class ReferenceBudgetExceeded(ValueError):
    def __init__(self, reason):
        super().__init__('Background resource roots exceeded the ' + reason + ' budget.')
        self.reason = reason


class ReferenceBudget:
    """Cooperative background admission, never a partial reachability proof.

    Check fields as well as containers so one wide list cannot escape the work
    limit. JSON is indivisible; refuse oversized records before decoding and
    check time again afterwards. Explicit/offline collection has no such budget.
    """
    def __init__(self, *, seconds=0.020, fields=50_000, json_chars=262_144, clock=None):
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + seconds
        self.max_fields, self.max_json_chars = fields, json_chars
        self.fields = 0

    def check(self):
        if self.clock() >= self.deadline:
            raise ReferenceBudgetExceeded('deadline')

    def step(self):
        self.fields += 1
        if self.fields > self.max_fields:
            raise ReferenceBudgetExceeded('fields')
        if self.fields % 64 == 0:
            self.check()

    def decode(self, text):
        self.check()
        if len(text) > self.max_json_chars:
            raise ReferenceBudgetExceeded('json-size')
        value = json.loads(text)
        self.check()
        return value


def references(value, *, budget=None):
    """Walk containers, not every scalar, without hydrating cold payloads.

    Shared projections can expose the same mutable graph several times. Retain
    each visited container until this walk ends, within the traversal limit:
    stopping duplicate suppression early can revisit a large shared graph until
    valid roots exceed that limit. This is not a cache across mutable reads.
    """
    from .cold_display import ColdRecord
    pending, seen = [value], {}
    scalars = {str, int, float, bool, type(None)}
    while pending:
        if budget is not None:
            budget.step()
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        if isinstance(current, dict):
            if type(current) is dict or isinstance(current, ColdRecord):
                reference, children = dict.get(current, '$resource'), dict.values(current)
            else:
                reference, children = current.get('$resource'), current.values()
        elif isinstance(current, list):
            reference, children = None, current
        elif isinstance(current, Mapping):
            reference, children = current.get('$resource'), current.values()
        else:
            continue
        if len(seen) >= 1_000_000:
            raise ValueError('Resource reference graph exceeds the traversal limit.')
        seen[identity] = current  # Strong reference prevents temporary ID reuse.
        if isinstance(reference, str):
            yield reference
        for child in children:
            if budget is not None:
                budget.step()
            # Most fields are strings/numbers. Avoid a recursive generator and
            # Mapping ABC checks for ordinary JSON scalars on this hot path.
            kind = type(child)
            if kind is dict or kind is list or (
                    kind not in scalars
                    and isinstance(child, (Mapping, list))):
                pending.append(child)


def retained_references(db, state, *, budget=None, phase=None):
    """References from complete state and durable client/operation records."""
    with phase('live') if phase else nullcontext():
        if budget is not None:
            budget.check()
        pending = list(references(state, budget=budget))
        if budget is not None:
            budget.check()
    # A hydrated mutable field can drop its in-memory cold reference before its
    # next save. The last committed global manifest still owns that exact blob.
    with phase('durable') if phase else nullcontext():
        from .state_records import load
        decode = budget.decode if budget is not None else json.loads
        saved = load(db, decode=decode) if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'").fetchone() else None
        if saved:
            pending.extend(references(saved, budget=budget))
        # Persisted client records remain roots even before ClientViews is loaded
        # at startup, and when their browser is disconnected or another is bound.
        for table in ('smart_tool_operations', 'client_views', 'conversation_shares', 'output_records'):
            if budget is not None:
                budget.check()
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                for (text,) in db.execute('SELECT value FROM ' + table):
                    pending.extend(references(decode(text), budget=budget))
        if budget is not None:
            budget.check()
    return pending


def restore(db, state, identity, value):
    """Restore an exact missing, directly retained resource without replay."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if hashlib.sha256(encoded.encode()).hexdigest() != identity:
        raise ValueError('Recovery content does not match the saved resource identity.')
    if identity not in retained_references(db, state):
        raise ValueError('Recovery requires an existing saved reference to this resource.')
    from .state_storage import resource
    if db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
        try:
            existing = resource(db, identity)
        except FileNotFoundError:
            pass
        else:
            if existing != value:
                raise ValueError('Existing resource content conflicts with recovery content.')
            return {'id': identity, 'restored': False}
    directory = root(db)
    path = directory / (identity + '.json') if directory else None
    if path and path.exists() and path.read_text() != encoded:
        raise ValueError('Existing resource file conflicts with recovery content.')
    put(db, value)
    return {'id': identity, 'restored': True}


def marked_references(db, state):
    """Return retained graph, or None when any reachability is uncertain."""
    states = state if isinstance(state, list) else [state]
    if any(isinstance(row, dict) and row.get('_viewRecoveryPending') for row in states):
        return None  # a failed presentation rollback can retain unknown blob roots
    from .state_storage import resource
    pending = retained_references(db, state)
    marked = set()
    while pending:
        identity = pending.pop()
        if identity in marked:
            continue
        marked.add(identity)
        if not db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
            return None  # Its unavailable body may retain other sources.
        else:
            try:
                pending.extend(references(resource(db, identity)))
            except (OSError, ValueError, TypeError, KeyError):
                # Unknown nested references cannot be proven unreferenced. Keep
                # all sources until the damaged retained artifact is recovered.
                return None
    return marked


def detached_references(source):
    """Validate the actual body and walk all nested edges away from SQLite."""
    return list(references(read_detached(source)))


class ReferenceEdges:
    """Bounded derived edges for validated immutable files, never body values.

    An entry is usable only for the same exact Detached index and unchanged
    device/inode/size/mtime/ctime. Changed, missing, or non-regular files must be
    revalidated or refuse collection; a cached graph is never deletion authority.
    Inline resources are deliberately not cached: their index is their payload.
    """
    def __init__(self, *, max_entries=65_536, max_edges=262_144):
        self.entries = OrderedDict()
        self.max_entries, self.max_edges = max_entries, max_edges
        self.edge_count = 0
        self.hits = self.misses = self.bytes_read = 0

    def _drop(self, key):
        prior = self.entries.pop(key, None)
        if prior is not None:
            self.edge_count -= len(prior[1])

    def read(self, source):
        blob = _blob_index(source)
        try:
            signature = detached_signature(source)
        except (OSError, ValueError):
            self._drop(source)
            raise
        prior = self.entries.get(source) if blob else None
        if prior is not None and prior[0] == signature:
            self.entries.move_to_end(source)
            self.hits += 1
            return prior[1]
        self._drop(source)
        self.misses += 1
        edges = tuple(set(detached_references(source)))
        if detached_signature(source) != signature:
            raise ValueError('Artifact changed while inspecting its reference edges.')
        # Malformed references already make the following detach fail closed;
        # reject here too so an arbitrary string cannot expand metadata storage.
        if any(not re.fullmatch('[a-f0-9]{64}', edge) for edge in edges):
            raise ValueError('Invalid retained resource reference.')
        self.bytes_read += signature[2] if blob else len(source.indexed.encode())
        if blob and self.max_entries > 0 and len(edges) <= self.max_edges:
            while self.entries and (len(self.entries) >= self.max_entries
                                    or self.edge_count + len(edges) > self.max_edges):
                self._drop(next(iter(self.entries)))
            self.entries[source] = (signature, edges)
            self.edge_count += len(edges)
        return edges


async def marked_references_async(db, state):
    """Same conservative graph, with file/JSON work detached from the owner.

    The caller retains its app lock AND writer transaction across every await.
    No kind-based leaf assumption: even imported media bodies can contain edges.
    """
    if not db.in_transaction:
        raise ValueError('Resource marking requires writer exclusion.')
    states = state if isinstance(state, list) else [state]
    if any(isinstance(row, dict) and row.get('_viewRecoveryPending') for row in states):
        return None
    initial_roots = set(retained_references(db, state))
    pending = list(initial_roots)
    sources = {}
    while pending:
        identity = pending.pop()
        if identity in sources:
            continue
        try:
            source = detach(db, identity)
            pending.extend(await settle_worker(detached_references, source))
            sources[identity] = source
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None  # Uncertain reachability must never authorize deletion.
    # Defensive against a synchronous save hook that failed to honor app.lock.
    # Never apply an old mark to changed roots or a released writer boundary.
    if (not db.in_transaction or set(retained_references(db, state)) != initial_roots
            or any(isinstance(row, dict) and row.get('_viewRecoveryPending') for row in states)):
        return None
    try:
        if any(detach(db, identity) != source for identity, source in sources.items()):
            return None
    except ValueError:
        return None
    return set(sources)


def collect(db, state):
    """Mark all retained state/receipt references, including nested references."""
    owns_transaction = not db.in_transaction
    if owns_transaction:
        db.execute('BEGIN IMMEDIATE')
    marked = marked_references(db, state)
    if marked is None:
        if owns_transaction:
            db.rollback()
        return []
    return prune_marked(db, marked)


def prune_marked(db, marked, *, limit=None):
    """Prune using a mark computed under the same caller-owned writer lock."""
    if not db.in_transaction:
        raise ValueError('Resource pruning requires writer exclusion.')
    candidates = (row[0] for row in db.execute('SELECT id FROM state_resources') if row[0] not in marked)
    stale = list(candidates if limit is None else islice(candidates, limit))
    db.executemany('DELETE FROM state_resources WHERE id=?', ((identity,) for identity in stale))
    # Files are deleted only after the caller commits the pruned references.
    return stale


def remove_files(db, identities):
    if db.in_transaction:
        raise ValueError('Commit index pruning before removing resource files.')
    directory = root(db)
    if directory:
        db.execute('BEGIN IMMEDIATE')
        try:
            for identity in identities:
                if not re.fullmatch('[a-f0-9]{64}', identity):
                    raise ValueError('Invalid resource identity.')
                # Another writer may have re-adopted this hash since collection.
                if not db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
                    (directory / (identity + '.json')).unlink(missing_ok=True)
        finally:
            db.rollback()  # only a lock; no data changes in this transaction


async def remove_files_async(db, identities):
    """Recheck re-adoption on the owner; settle bounded file work before unlock."""
    if db.in_transaction:
        raise ValueError('Commit index pruning before removing resource files.')
    directory = root(db)
    if directory is None or not identities:
        return {'filesRemoved': 0, 'bytesRemoved': 0}
    db.execute('BEGIN IMMEDIATE')
    try:
        paths = []
        for identity in identities:
            if not re.fullmatch('[a-f0-9]{64}', identity):
                raise ValueError('Invalid resource identity.')
            if not db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
                paths.append(directory / (identity + '.json'))

        def unlink():
            removed = {'filesRemoved': 0, 'bytesRemoved': 0}
            for path in paths:
                try:
                    size = path.lstat().st_size
                    path.unlink()
                except FileNotFoundError:
                    continue
                removed['filesRemoved'] += 1
                removed['bytesRemoved'] += size
            return removed

        return await settle_worker(unlink)
    finally:
        db.rollback()


def sweep_unindexed(db, state, iterator=None, *, limit=128):
    """Bound file inspection; exclude writers and refuse uncertain reachability.

    Return a live scandir cursor and counts. Callers close the cursor on shutdown.
    Only hash-named regular files and our atomic writer's temporary files qualify.
    """
    if db.in_transaction:
        raise ValueError('Orphan reconciliation requires a committed boundary.')
    db.execute('BEGIN IMMEDIATE')
    try:
        if marked_references(db, state) is None:
            if iterator is not None:
                iterator.close()
            return None, {'blocked': True, 'complete': False, 'inspected': 0, 'removed': 0}
        return sweep_unindexed_locked(db, iterator, limit=limit)
    finally:
        db.rollback()


def sweep_unindexed_locked(db, iterator=None, *, limit=128):
    """Caller holds writer lock and has established a complete retained graph."""
    if not db.in_transaction:
        raise ValueError('Resource sweep requires writer exclusion.')
    try:
        directory = root(db)
        if directory is None or not directory.exists():
            return None, {'blocked': False, 'complete': True, 'inspected': 0, 'removed': 0}
        iterator = iterator or os.scandir(directory)
        inspected = removed = 0
        for _ in range(limit):
            entry = next(iterator, None)
            if entry is None:
                iterator.close()
                return None, {'blocked': False, 'complete': True, 'inspected': inspected, 'removed': removed}
            inspected += 1
            if not entry.is_file(follow_symlinks=False):
                continue
            identity = entry.name[:-5] if re.fullmatch('[a-f0-9]{64}\\.json', entry.name) else None
            temporary = entry.name.startswith('.checkpoint-')
            if identity and db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
                continue
            if identity or temporary:
                try:
                    Path(entry.path).unlink()
                except FileNotFoundError:
                    # A retained scandir cursor can still yield an entry that
                    # an earlier indexed-pruning batch already removed.
                    continue
                removed += 1
        return iterator, {'blocked': False, 'complete': False, 'inspected': inspected, 'removed': removed}
    except BaseException:
        if iterator is not None:
            iterator.close()
        raise
