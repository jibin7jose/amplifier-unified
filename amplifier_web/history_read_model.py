"""Requested, disposable Work projections; native logs remain the authority.

This module runs in the history projector process. It has no service, runtime,
credential or command admission object. A conservative incremental path advances
known non-Work receipts without rebuilding unchanged descendants. Other changes
use the exact canonical reader until their smaller dependency closure is proven.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import copy
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace

from .event_log_view import EventIndex, EventLogView, event_path
from .performance_diagnostics import PerformanceDiagnostics
from .session_files import project_slug, source_change_time

SCHEMA = 1
MAX_MODELS = 6
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_MEMORY_BYTES = 96 * 1024 * 1024
MAX_DISK_BYTES = 256 * 1024 * 1024
MAX_APPEND_BYTES = 256 * 1024
MAX_APPEND_EVENTS = 256

# These have no branch in EventIndex.ingest and no transcript association role.
# Unknown event names deliberately take the reference path, even if an older
# version of the reader happened to ignore them.
NON_WORK_EVENTS = frozenset({
    'session:start', 'session:end', 'session:resume',
    'context:compaction_started', 'context:compaction_progress',
    'context:compaction_finished',
})


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()


def input_digest(value):
    """Exact detached inputs, not a presentation revision or source identity."""
    return hashlib.sha256(encoded(value)).hexdigest()


def tuple_tree(value):
    return tuple(tuple_tree(item) for item in value) if isinstance(value, list) else value


def source_stamp(path):
    try:
        info = Path(path).stat()
    except FileNotFoundError:
        return None
    return ((info.st_dev, info.st_ino), info.st_size, info.st_mtime_ns, source_change_time(info))


def stable_sources(revisions):
    return all(source_stamp(path) == tuple_tree(stamp) for path, stamp in revisions)


def materialize_result(session, result):
    """Apply only a revision-fenced display delta; never execute its contents."""
    previous = session.get('execution')
    if result.get('unchanged'):
        return previous
    delta = result.get('delta')
    if delta is None:
        return result.get('tree')
    if not isinstance(previous, dict) or len(previous.get('nodes', ())) != delta.get('nodeCount'):
        raise ValueError('History projection changed before its update was applied')
    nodes = list(previous['nodes'])
    seen = set()
    for position, node in delta['nodes']:
        if (type(position) is not int or not 0 <= position < len(nodes) or position in seen
                or nodes[position].get('id') != node.get('id')):
            raise ValueError('History projection no longer matches its update')
        seen.add(position)
        nodes[position] = node
    return {**previous, 'nodes': nodes, 'turns': delta['turns']}


def retained_size(value, limit):
    """Logical Python payload charge, not a claim about process RSS."""
    seen, pending, total = set(), [value], 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if total > limit:
            return total
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (tuple, list, set, frozenset)):
            pending.extend(item)
    return total


class ModelStore:
    """Bounded row-oriented sidecar, private to this projector instance.

    One transaction replaces a cold generation. Source-only updates touch just
    the small model receipt; unchanged node JSON is neither read nor rewritten.
    The database is disposable and never grants command or recording authority.
    """
    def __init__(self, home):
        folder = Path(home) / 'cache' / 'selected-work-v1'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / 'models.sqlite'
        self.db = sqlite3.connect(path, timeout=.1)
        path.chmod(0o600)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA max_page_count=131072')  # 512 MiB at default4KiB pages.
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS models (
                identity TEXT PRIMARY KEY, receipt TEXT NOT NULL, body TEXT NOT NULL,
                bytes INTEGER NOT NULL, touched REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS nodes (
                identity TEXT NOT NULL, position INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(identity, position));
        ''')

    def _prune(self):
        total, count = 0, 0
        for identity, length in self.db.execute('SELECT identity,bytes FROM models ORDER BY touched DESC').fetchall():
            total += length
            count += 1
            if total > MAX_DISK_BYTES or count > MAX_MODELS:
                self.db.execute('DELETE FROM nodes WHERE identity=?', (identity,))
                self.db.execute('DELETE FROM models WHERE identity=?', (identity,))

    def load(self, key, digest):
        # Reject known input misses before reading either the tree or its nodes.
        # Bound the receipt in SQLite before returning its text to Python.
        size = self.db.execute('SELECT length(CAST(receipt AS BLOB)),bytes FROM models WHERE identity=?', (key,)).fetchone()
        if size is None or size[0] > MAX_RECEIPT_BYTES or size[1] > MAX_MODEL_BYTES:
            return None
        row = self.db.execute('SELECT receipt FROM models WHERE identity=?', (key,)).fetchone()
        if row is None:
            return None
        receipt = json.loads(row[0])
        if (not isinstance(receipt, dict)
                or not isinstance(receipt.get('inputDigest'), str)
                or not isinstance(receipt.get('paths'), list)
                or not isinstance(receipt.get('revisions'), list)
                or not isinstance(receipt.get('proofs'), dict)):
            raise ValueError('Invalid cached model receipt')
        if receipt.get('schema') != SCHEMA:
            self.remove(key)
            return None
        if receipt['inputDigest'] != digest:
            return None
        actual = self.db.execute('SELECT length(CAST(receipt AS BLOB))+length(CAST(body AS BLOB)) + (SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM nodes WHERE identity=?) FROM models WHERE identity=?', (key, key)).fetchone()
        if actual is None or actual[0] > MAX_MODEL_BYTES:
            return None
        row = self.db.execute('SELECT body,bytes FROM models WHERE identity=?', (key,)).fetchone()
        if row is None or row[1] > MAX_MODEL_BYTES:
            return None
        body = json.loads(row[0])
        if not isinstance(body, dict):
            raise ValueError('Invalid cached model body')
        body['nodes'] = [json.loads(row[0]) for row in self.db.execute(
            'SELECT body FROM nodes WHERE identity=? ORDER BY position', (key,))]
        if not all(isinstance(node, dict) and isinstance(node.get('id'), str) for node in body['nodes']):
            raise ValueError('Invalid cached model nodes')
        for proof in receipt['proofs'].values():
            if (not isinstance(proof, dict) or type(proof.get('offset')) is not int
                    or proof['offset'] < 0 or not isinstance(proof.get('identity'), str)
                    or not isinstance(proof.get('sha256'), str) or len(proof['sha256']) != 64):
                raise ValueError('Invalid cached source proof')
        return {**receipt, 'tree': body, 'bytes': row[1]}

    def save(self, key, value, *, source_only=False, positions=None):
        receipt = {name: value[name] for name in ('schema', 'inputDigest', 'paths', 'revisions', 'proofs')}
        receipt_bytes = encoded(receipt)
        if len(receipt_bytes) > MAX_RECEIPT_BYTES:
            self.remove(key)
            return
        receipt_text = receipt_bytes.decode()
        if source_only:
            with self.db:
                self.db.execute('UPDATE models SET bytes=bytes-length(CAST(receipt AS BLOB))+?,receipt=?,touched=? WHERE identity=?',
                                (len(receipt_bytes), receipt_text, time.time(), key))
                self._prune()
            return
        tree = value['tree']
        if tree is None:
            return
        body = {name: item for name, item in tree.items() if name != 'nodes'}
        if positions is not None:
            prior = self.db.execute('SELECT receipt,body,bytes FROM models WHERE identity=?', (key,)).fetchone()
            if prior is not None:
                changes = [(position, encoded(tree['nodes'][position]).decode()) for position in positions]
                size = prior[2] - len(prior[0].encode()) - len(prior[1].encode()) + len(receipt_bytes) + len(encoded(body))
                for position, row in changes:
                    old = self.db.execute('SELECT length(CAST(body AS BLOB)) FROM nodes WHERE identity=? AND position=?', (key, position)).fetchone()
                    size += len(row.encode()) - (old[0] if old else 0)
                if size > MAX_MODEL_BYTES:
                    self.remove(key)
                    return
                with self.db:
                    self.db.executemany('UPDATE nodes SET body=? WHERE identity=? AND position=?',
                                        ((row, key, position) for position, row in changes))
                    self.db.execute('UPDATE models SET receipt=?,body=?,bytes=?,touched=? WHERE identity=?',
                                    (receipt_text, encoded(body).decode(), size, time.time(), key))
                    self._prune()
                return
        rows = [encoded(row).decode() for row in tree.get('nodes', ())]
        size = len(receipt_bytes) + len(encoded(body)) + sum(len(row.encode()) for row in rows)
        if size > MAX_MODEL_BYTES:
            self.remove(key)
            return
        with self.db:
            self.db.execute('DELETE FROM nodes WHERE identity=?', (key,))
            self.db.executemany('INSERT INTO nodes VALUES (?,?,?)',
                                ((key, position, row) for position, row in enumerate(rows)))
            self.db.execute('INSERT OR REPLACE INTO models VALUES (?,?,?,?,?)',
                            (key, receipt_text, encoded(body).decode(), size, time.time()))
            self._prune()

    def remove(self, key):
        with self.db:
            self.db.execute('DELETE FROM nodes WHERE identity=?', (key,))
            self.db.execute('DELETE FROM models WHERE identity=?', (key,))

    def close(self):
        self.db.close()


class SelectedHistoryReadModel:
    def __init__(self, cache_home):
        self.diagnostics = PerformanceDiagnostics()
        self.service = SimpleNamespace(data_dir=Path(cache_home), performance_diagnostics=self.diagnostics)
        self.reader = EventLogView(self.service)
        self.models = OrderedDict()
        self.model_bytes = 0
        try:
            self.store = ModelStore(cache_home)
        except (OSError, sqlite3.Error):
            self.store = None  # Cache failure cannot make canonical data disappear.

    def _count(self, name, amount=1):
        self.diagnostics.increment('events.model.' + name, amount)

    def _remember(self, key, value):
        previous = self.models.pop(key, None)
        if previous:
            self.model_bytes -= previous['bytes']
        value['bytes'] = retained_size(value, MAX_MEMORY_BYTES)
        if value['bytes'] > MAX_MEMORY_BYTES:
            self._count('oversized')
            self._prune_reader_receipts()
            return
        self.models[key] = value
        self.model_bytes += value['bytes']
        while len(self.models) > MAX_MODELS or self.model_bytes > MAX_MEMORY_BYTES:
            _, retired = self.models.popitem(last=False)
            self.model_bytes -= retired['bytes']
            self._count('evicted')
        self._prune_reader_receipts()

    def _prune_reader_receipts(self):
        # The reference reader has bounded indexes but standalone reads do not
        # use its async publication LRU. Do not retain all previously read roots.
        for name in ('read_paths', 'read_revisions', 'read_proofs'):
            mapping = getattr(self.reader, name)
            for identity in tuple(mapping):
                if identity not in self.models:
                    mapping.pop(identity, None)

    def _load(self, key, digest):
        value = self.models.get(key)
        if value is not None:
            if value['inputDigest'] == digest:
                self.models.move_to_end(key)
                return value
            self.models.pop(key)
            self.model_bytes -= value['bytes']
            del value
            self._prune_reader_receipts()
        value = None
        if self.store is not None:
            try:
                value = self.store.load(key, digest)
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                self._count('cache_invalid')
                return None
            if value is not None:
                self._remember(key, value)
                self._count('cache_hit')
        return value

    @staticmethod
    def _valid_sources(inputs, value):
        """A disposable cache cannot grant a different filesystem read scope."""
        from .automatic_history import directory
        source = {**inputs, 'nativeProject': inputs.get('nativeProject') or
                  project_slug(inputs['workspace'])}
        transcripts = {directory(source) / name for name in
                       ('transcript.jsonl', 'transcript.jsonl.backup', 'transcript.jsonl.append-pending')}
        try:
            for name in value['paths']:
                path = Path(name)
                if path in transcripts:
                    continue
                if path != event_path(inputs, path.parent.parent.name):
                    return False
            return list(value['paths']) == [row[0] for row in value['revisions']]
        except (ValueError, KeyError, TypeError, IndexError):
            return False

    def _save_model(self, key, value, *, source_only=False, positions=None):
        if self.store is not None:
            try:
                self.store.save(key, value, source_only=source_only, positions=positions)
            except (OSError, sqlite3.Error, ValueError, TypeError):
                self._count('cache_write_failed')

    def _append(self, inputs, value, current, check_budget):
        """Validate a bounded append and the existing-node dependency closure."""
        changed = [(path, tuple_tree(old), stamp) for (path, old), (_, stamp)
                   in zip(value['revisions'], current) if tuple_tree(old) != stamp]
        if not changed:
            return ()
        replacements = {}
        updates = {}
        nodes = value['tree'].get('nodes', ()) if value['tree'] is not None else ()
        by_canonical = {}
        for position, node in enumerate(nodes):
            if node.get('kind') == 'tool':
                by_canonical.setdefault((node.get('sessionId'), node.get('_canonicalId', node['id'])), []).append(position)
        root = inputs.get('nativeIdentity') or inputs.get('runtimeSessionId') or inputs['id']
        transcript_present = any(stamp is not None and Path(path).name != 'events.jsonl' for path, stamp in current)
        for path, old, stamp in changed:
            proof = value['proofs'].get(path)
            if (Path(path).name != 'events.jsonl' or old is None or stamp is None or not proof
                    or old[0] != stamp[0] or stamp[1] <= old[1]
                    or stamp[1] - proof['offset'] > MAX_APPEND_BYTES):
                return None
            hasher = hashlib.sha256()
            with Path(path).open('rb') as stream:
                remaining = proof['offset']
                while remaining:
                    check_budget()
                    chunk = stream.read(min(remaining, 65536))
                    if not chunk:
                        return None
                    hasher.update(chunk)
                    remaining -= len(chunk)
                    self._count('validated_prefix_bytes', len(chunk))
                if hasher.hexdigest() != proof['sha256']:
                    return None
                offset = proof['offset']
                last = proof.get('last')
                count = 0
                while offset < stamp[1]:
                    raw = stream.readline(MAX_APPEND_BYTES + 1)
                    if not raw.endswith(b'\n'):
                        break  # Partial final writes remain outside the committed offset.
                    count += 1
                    if count > MAX_APPEND_EVENTS:
                        return None
                    try:
                        event = json.loads(raw)
                        data = event.get('data', {})
                    except (ValueError, AttributeError):
                        return None
                    if not isinstance(data, dict) or data.get('kind') in {'tool', 'llm'}:
                        return None
                    name = event.get('event')
                    if not isinstance(name, str):
                        return None
                    if name not in NON_WORK_EVENTS:
                        # Appending a completion to an existing child tool cannot
                        # create a model call, child admission or root transcript
                        # association. Root association changes remain conservative.
                        sid = data.get('session_id') or event.get('session_id') or proof['identity']
                        call = data.get('tool_call_id') or data.get('call_id')
                        if not isinstance(sid, str) or not isinstance(call, str):
                            return None
                        candidates = by_canonical.get((sid, f'tool:{sid}:{call}'), ())
                        if (name not in {'tool:post', 'tool:error'} or len(candidates) != 1
                                or sid in {root, inputs['id']} and transcript_present
                                or any(field in data for field in ('tool_input', 'arguments', 'input'))):
                            return None
                        position = candidates[0]
                        original = updates.get(position, nodes[position])
                        references = original.get('_eventFields', {}).values()
                        from .delegate_exchange import tool_name
                        if (not original.get('_eventFields') or any(row.get('path') != path for row in references)
                                or not isinstance(original.get('startedAt'), (int, float))
                                or tool_name(original) in {'todo', 'delegate', 'live_worker'}):
                            return None
                        index = EventIndex(Path(path), proof['identity'])
                        canonical = f'tool:{sid}:{call}'
                        index.nodes[canonical] = {**copy.deepcopy(original), 'id': canonical}
                        reference = {'path': path, 'offset': offset, 'bytes': len(raw),
                                     'sha256': hashlib.sha256(raw).hexdigest()}
                        index.ingest(event, reference)
                        replacement = index.nodes[canonical]
                        replacement['id'] = original['id']
                        for field in ('input', 'output', 'error', 'request', 'response', 'delegateRequest', 'delegateResponse'):
                            if replacement.get(field + 'Detail'):
                                replacement[field + 'Detail'].update(id=original['id'], sessionId=inputs['id'])
                        updates[position] = replacement
                    hasher.update(raw)
                    last = {'path': path, 'offset': offset, 'bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest()}
                    offset += len(raw)
                replacements[path] = {**proof, 'offset': offset, 'last': last, 'sha256': hasher.hexdigest()}
            if source_stamp(path) != stamp:
                return None
        if not stable_sources(current):
            return None
        if updates:
            tree = {**value['tree'], 'nodes': list(nodes), 'turns': copy.deepcopy(value['tree']['turns'])}
            for position, node in updates.items():
                tree['nodes'][position] = node
            # Native worker completion can fill a previously unknown end, but
            # saved host worker metadata remains the owner, as in the oracle.
            by_id = {node['id']: (position, node) for position, node in enumerate(tree['nodes'])}
            parents = {node.get('parentId') for node in updates.values()}
            for identity in parents:
                pair = by_id.get(identity)
                if pair is None or pair[1].get('kind') != 'worker' or 'endedAt' in pair[1]:
                    continue
                position, worker = pair
                ends = [node['endedAt'] for node in tree['nodes'] if node.get('parentId') == identity
                        and isinstance(node.get('endedAt'), (int, float))]
                if ends:
                    replacement = {**worker, 'endedAt': max(ends)}
                    tree['nodes'][position] = replacement
                    updates[position] = replacement
            from .event_log_view import refresh_turn_lifecycle
            refresh_turn_lifecycle(tree, inputs)
            value['tree'] = tree
            value['inputDigest'] = input_digest(EventLogView.projection_input({**inputs, 'execution': tree}))
            self._count('changed_nodes', len(updates))
        value['proofs'].update(replacements)
        value['revisions'] = current
        self._count('incremental' if updates else 'source_only', len(changed))
        return tuple(updates)

    def project(self, inputs, *, check_budget=lambda: None):
        self.diagnostics = PerformanceDiagnostics()
        self.service.performance_diagnostics = self.diagnostics
        def stage(name):
            if label := getattr(check_budget, 'stage', None):
                label(name)
            check_budget()
        stage('model_cache_load')
        key = inputs['id']
        digest = input_digest(inputs)
        value = self._load(key, digest)
        if value is not None and value['inputDigest'] == digest and self._valid_sources(inputs, value):
            # A failed source fence must not mutate the retained generation.
            # Node rows are immutable; only the receipt is copied here.
            value = {**value, 'proofs': dict(value['proofs'])}
            current = [(path, source_stamp(path)) for path in value['paths']]
            positions = self._append(inputs, value, current, check_budget)
            if positions is not None and stable_sources(current):
                stage('model_store')
                self._save_model(key, value, source_only=not positions, positions=positions or None)
                if positions:
                    stage('model_retain')
                    self._remember(key, value)
                elif key in self.models:
                    self.models[key] = value
                self._count('reused')
                payload = {'delta': {'nodeCount': len(value['tree']['nodes']),
                           'nodes': [(position, value['tree']['nodes'][position]) for position in positions],
                           'turns': value['tree']['turns']}} if positions else {'unchanged': True}
                return {**{name: value[name] for name in ('paths', 'revisions', 'inputDigest')}, **payload,
                        'status': 'complete', 'diagnostics': self.diagnostics.snapshot()}
        self._count('rebuilt')
        self.reader._budget_guard = check_budget
        tree = self.reader.read(inputs)
        check_budget()
        revisions = self.reader.read_revisions.get(key, ())
        if not stable_sources(revisions):
            self._count('source_changed')
            return {'status': 'retry', 'reason': 'source_changed'}
        value = {'schema': SCHEMA, 'inputDigest': input_digest(EventLogView.projection_input(
            {**inputs, **({'execution': tree} if tree is not None else {})})), 'tree': tree,
            'paths': list(map(str, self.reader.read_paths.get(key, ()))),
            'revisions': revisions, 'proofs': self.reader.read_proofs.get(key, {})}
        stage('model_store')
        self._save_model(key, value)
        stage('model_retain')
        self._remember(key, value)
        return {**{name: value[name] for name in ('tree', 'paths', 'revisions', 'inputDigest')},
                'status': 'complete', 'diagnostics': self.diagnostics.snapshot()}

    def close(self):
        if self.store is not None:
            self.store.close()
