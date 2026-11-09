"""Lazy, lossless display fields over the existing immutable resource store.

Session identities and workflow authority never leave hot state. Only named
payloads retire, after their exact bodies and references have committed. Reads
and mutations keep the existing dict interface; no runtime is mounted to reload.
"""
from collections import OrderedDict
from collections import Counter
import copy
import hashlib
import json
import sys
import time

from .resource_files import put
from .state_storage import resource

MARKER = '_coldFields'
SESSION_FIELDS = frozenset({'messages', 'execution', 'historyActivity', 'configuration'})
CONTROL_FIELDS = frozenset({'modelCatalogs', 'catalog.inspect', 'configuration.inspect'})

def load(db, reference):
    value = resource(db, reference['$resource'])
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if hashlib.sha256(encoded.encode()).hexdigest() != reference['$resource']:
        raise ValueError('The saved display payload changed. Its files were preserved.')
    return value


def raw(row):
    return dict(dict.items(row))

def detached(row):
    """Explicit read-only snapshot wrapper, not the mutable shallow-copy API."""
    if isinstance(row, ColdRecord):
        value = raw(row)
        value[MARKER] = copy.deepcopy(value.get(MARKER, {}))
        return ColdRecord(value, row.db)
    return dict(row)


def _graph(value):
    objects, incoming, pending = {}, Counter({id(value): 1}), [value]
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in objects:
            continue
        if type(current) not in (dict, list, tuple, set, frozenset):
            continue
        objects[identity] = current
        children = [*current.keys(), *current.values()] if type(current) is dict else list(current)
        for child in children:
            incoming[id(child)] += 1
            if type(child) in (dict, list, tuple, set, frozenset):
                pending.append(child)
            elif type(child) not in (str, int, float, bool, type(None)):
                return None, None  # unknown nested ownership stays resident
    return objects, incoming


def exclusively_owned(row, key):
    """Conservatively retain a mutable graph while any issued alias exists.

    CPython reference counts include exactly the graph's incoming edges plus
    the object map, local variable and getrefcount argument at this boundary.
    Other runtimes/custom containers are retained rather than guessed safe.
    """
    if sys.implementation.name != 'cpython':
        return False
    value = dict.__getitem__(row, key)
    if type(value) not in (dict, list, tuple, set, frozenset, str, int, float, bool, type(None)):
        return False
    objects, incoming = _graph(value)
    del value
    if objects is None:
        return False
    for identity in objects:
        current = objects[identity]
        if sys.getrefcount(current) > incoming[identity] + 3:
            return False
    return True


def materialize(row, db):
    value = raw(row)
    references = value.pop(MARKER, {})
    for key, reference in references.items():
        if key not in value:
            value[key] = load(db, reference)
    return value


class ColdRecord(dict):
    """Resolve a named cold field before handing out its mutable value."""
    def __init__(self, value, db, touch=None):
        super().__init__(value)
        self.db, self.touch = db, touch

    def __contains__(self, key):
        return dict.__contains__(self, key) or key in dict.get(self, MARKER, {})

    def __getitem__(self, key):
        if not dict.__contains__(self, key):
            reference = dict.get(self, MARKER, {}).get(key)
            if reference is not None:
                # Do not replace newer metadata or hide an unreadable body.
                value = load(self.db, reference)
                dict.__setitem__(self, key, value)
                # The returned list/dict is mutable. Once handed out it must be
                # saved from live memory, never from its older frozen reference.
                dict.get(self, MARKER, {}).pop(key, None)
                if key == 'messages':
                    # Once this mutable body escapes, borrowed aliases can
                    # change it without another setter. Retire its summaries.
                    dict.pop(self, '_coldMessageCount', None)
                    dict.pop(self, '_coldNotifications', None)
                if self.touch:
                    self.touch()
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        return self[key] if key in self else default

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        if key == 'messages':
            dict.pop(self, '_coldMessageCount', None)
            dict.pop(self, '_coldNotifications', None)
        if key != MARKER:
            dict.get(self, MARKER, {}).pop(key, None)
            if self.touch and key in SESSION_FIELDS | CONTROL_FIELDS:
                self.touch()

    def __delitem__(self, key):
        references = dict.get(self, MARKER, {})
        present = dict.__contains__(self, key)
        if key not in references and not present:
            raise KeyError(key)
        references.pop(key, None)
        if present:
            dict.__delitem__(self, key)
        if key == 'messages':
            dict.pop(self, '_coldMessageCount', None)
            dict.pop(self, '_coldNotifications', None)

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = self[key]
        del self[key]
        return value

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            if key != MARKER:
                self[key] = value  # private references are never merge-patch data

    def __ior__(self, other):
        self.update(other)
        return self

    def __or__(self, other):
        return self.copy() | (other.copy() if isinstance(other, ColdRecord) else dict(other))

    def __ror__(self, other):
        return (other.copy() if isinstance(other, ColdRecord) else dict(other)) | self.copy()

    def popitem(self):
        # Ordinary mutable dict consumers must not pop the private manifest
        # while it still owns virtual fields.
        for key in tuple(dict.get(self, MARKER, {})):
            self[key]
        if not dict.get(self, MARKER):
            dict.pop(self, MARKER, None)
        return dict.popitem(self)

    def clear(self):
        dict.clear(self)

    def __iter__(self):
        return iter(dict.fromkeys([*(key for key in dict.keys(self) if key != MARKER),
                                   *dict.get(self, MARKER, {})]))

    def keys(self):
        return dict.fromkeys(iter(self)).keys()

    def __len__(self):
        return len(self.keys())

    def items(self):
        for key in tuple(dict.get(self, MARKER, {})):
            self[key]
        # Internal frozen-reference maps are not ordinary mutable patch data.
        # Once resolved, no empty manifest may escape into another row's merge.
        dict.pop(self, MARKER, None)
        return dict.items(self)

    def __copy__(self):
        return dict(self.items())

    def __deepcopy__(self, memo):
        value = copy.deepcopy(materialize(self, self.db), memo)
        memo[id(self)] = value
        return value

    def copy(self):
        return dict(self.items())


def notifications(row):
    """Only the newest 100 per-session rows can reach global last-100 output."""
    if isinstance(row, ColdRecord) and not dict.__contains__(row, 'messages'):
        return row.get('_coldNotifications', [])
    result = [{**{key: message[key] for key in ('id', 'role', 'via', 'createdAt') if key in message},
             'sessionId': row['id'], 'text': message.get('text', '')[:500]}
            for message in row.get('messages', [])
            if message.get('role') == 'assistant' and message.get('via') == 'text']
    return sorted(result, key=lambda row: row.get('createdAt', 0))[-100:]


def message_count(row):
    if isinstance(row, ColdRecord) and not dict.__contains__(row, 'messages'):
        return row.get('_coldMessageCount', 0)
    return len(row.get('messages', []))


class ColdDisplay:
    RECENT_LIMIT = 8
    IDLE_SECONDS = 60
    MIN_BYTES = 16000
    RETIRE_BATCH = 4
    PROBE_BATCH = 64
    SLICE_SECONDS = 0.025

    def __init__(self, service):
        self.service = service
        self.recent = OrderedDict()
        self.last_sweep = 0
        self.persisting = False
        self.probe_cursor = 0

    def touch(self, identity):
        self.recent[identity] = time.monotonic()
        self.recent.move_to_end(identity)

    def record(self, value):
        identity = value['id']
        if not value.get('historyManaged') or value.get('historyLoaded'):
            self.touch(identity)
        record = ColdRecord(value, self.service.db)
        record.touch = lambda sid=identity: self.touch(sid)
        return record

    def track_restored(self):
        for row in self.service._state.get('sessions', []):
            if isinstance(row, ColdRecord):
                row.touch = lambda sid=row['id']: self.touch(sid)
                if any(dict.__contains__(row, key) for key in SESSION_FIELDS):
                    self.recent.setdefault(row['id'], 0)

    @staticmethod
    def is_cold(row):
        return any(key not in dict.keys(row) for key in dict.get(row, MARKER, {}))

    def hydrate(self, row):
        if isinstance(row, ColdRecord):
            for key in tuple(dict.get(row, MARKER, {})):
                row[key]
        self.touch(row['id'])
        control = self.service._state.get('runtimeControl', {}).get(row['id'])
        if isinstance(control, ColdRecord):
            for key in tuple(dict.get(control, MARKER, {})):
                control[key]
        return row

    def _cool(self, row, fields):
        safe = {key for key in fields if dict.__contains__(row, key)
                and exclusively_owned(row, key)}
        values = raw(row)
        references = dict(values.get(MARKER, {}))
        for key in fields:
            if key not in safe:
                continue
            value = values[key]
            encoded = json.dumps(value, ensure_ascii=False)
            if len(encoded.encode()) < self.MIN_BYTES:
                continue
            if key == 'execution':
                from .session_projection import stored_execution
                value = stored_execution(value)
            reference = execution_reference(self.service.db, value) if key == 'execution' else put(self.service.db, value)
            if key == 'messages':
                from .media_saved_aliases import binding_hint
                reference = binding_hint(row, reference, value)
            references[key] = reference
        return references

    def retire(self, *, force=False):
        service, now = self.service, time.monotonic()
        if self.persisting or getattr(service, '_progress_dirty', False):
            return set()
        if not force and now - self.last_sweep < 15:
            return set()
        self.last_sweep = now
        from .history_demand import subscribed_sessions
        protected = subscribed_sessions(service) | {service._state.get('voice', {}).get('sessionId')}
        rows = service._state.get('sessions', [])
        index = None
        if not force:
            index = service.projections.sessions(service._state)
            for sid in tuple(self.recent):
                if sid not in index.by_id:
                    self.recent.pop(sid, None)  # deleted/remapped records do not starve the queue
        selected = service._state.get('selectedSessionId')
        if not service.queue_clients:
            protected.add(selected)  # legacy foreground compatibility
        protected.update(list(self.recent)[-self.RECENT_LIMIT:] if self.RECENT_LIMIT else ())
        eligible = []
        if force:
            candidates = rows
        else:
            # The complete catalog starts unloaded. Only recently touched body
            # records need retirement; idle sweeps never walk 24k cold summaries.
            ids = [sid for sid in self.recent if sid not in protected]
            start = self.probe_cursor % len(ids) if ids else 0
            ordered = ids[start:] + ids[:start]
            ids = ordered[:self.PROBE_BATCH]
            candidates = [index.by_id[sid] for sid in ids if sid in index.by_id]
        began = time.monotonic()
        for row in candidates:
            if not force:
                self.probe_cursor += 1
                if time.monotonic() - began >= self.SLICE_SECONDS:
                    break
            if (not isinstance(row, ColdRecord) or row['id'] in protected
                    or row.get('status') in {'working','running','starting','stopping','ready'}
                    or row.get('configurationBusy') or row.get('historyLoading')
                    or row.get('naming', {}).get('status') == 'working'
                    or any(a.get('status', 'pending') == 'pending' for a in row.get('approvals', []))
                    or any(w.get('status') in {'working','running','starting','queued','pending','stopping'}
                           for w in row.get('workers', []))
                    or (not force and now - self.recent.get(row['id'], 0) < self.IDLE_SECONDS)):
                continue
            control = service._state.get('runtimeControl', {}).get(row['id'])
            if control is not None and not isinstance(control, ColdRecord):
                # Do not disconnect an issued alias to the authoritative control
                # record. The map + this local + getrefcount are its only known
                # references; other owners require keeping its exact identity.
                if sys.implementation.name == 'cpython' and sys.getrefcount(control) == 3:
                    control = ColdRecord(control, service.db, lambda sid=row['id']: self.touch(sid))
                    service._state['runtimeControl'][row['id']] = control
                else:
                    control = None
            body_refs = self._cool(row, SESSION_FIELDS)
            control_refs = self._cool(control, CONTROL_FIELDS) if control is not None else {}
            if body_refs != dict.get(row, MARKER, {}) or control_refs != dict.get(control or {}, MARKER, {}):
                eligible.append((row, control, body_refs, control_refs))
            if not force and (len(eligible) >= self.RETIRE_BATCH
                              or time.monotonic() - began >= self.SLICE_SECONDS):
                break  # deadline checked between records, not inside a payload
        if not eligible:
            return set()
        # Commit resource bodies before removing mutable resident values. A
        # failed write leaves all in-memory bodies present and retryable.
        service.db.commit()
        for row, control, body_refs, control_refs in eligible:
            # Rollback restores earlier cold manifests, including fields that
            # have no resident value; it must never throw those references away.
            row._previous_cold_refs = copy.deepcopy(dict.get(row, MARKER, {}))
            if control is not None:
                control._previous_cold_refs = copy.deepcopy(dict.get(control, MARKER, {}))
            row['_coldMessageCount'] = message_count(row)
            row['_coldNotifications'] = notifications(row)
            row[MARKER] = body_refs
            if control is not None:
                control[MARKER] = control_refs
        changed = {row['id'] for row, *_ in eligible}
        self.persisting = True
        try:
            # Persist references while values still exist; persistence explicitly
            # prefers committed refs. Failure must not evict resident bodies.
            service._save(session_ids=changed, record_only=True)
        except BaseException:
            for row, control, *_ in eligible:
                row[MARKER] = row._previous_cold_refs
                if control is not None:
                    control[MARKER] = control._previous_cold_refs
            raise
        finally:
            self.persisting = False
        for row, control, body_refs, control_refs in eligible:
            for record, references in ((row, body_refs), (control, control_refs)):
                if record is not None:
                    for key in references:
                        dict.pop(record, key, None)
            from .session_projection import view_path
            service._view_cache.pop(str(view_path(service.data_dir, row)), None)
            service.projections.detail_bodies.pop(row['id'], None)
            event = getattr(service, 'event_log_view', None)
            if event:
                event.invalidate({row['id']})
                event.projected.pop(row['id'], None)
                event.read_paths.pop(row['id'], None)
                event.read_revisions.pop(row['id'], None)
            self.recent.pop(row['id'], None)
        service._browser_snapshot = None
        service._advance_client_snapshots(changed, True, service._state['revision'])
        return changed


def saved(row):
    """Cold-aware storage form: hot authority plus exact committed references."""
    value = raw(row)
    if MARKER in value:
        value[MARKER] = copy.deepcopy(value[MARKER])
    for key in value.get(MARKER, {}):
        value.pop(key, None)
    return value


def compact_execution_references(state, db):
    """Upgrade old cold trees once, before the host accepts connections.

    Only derived display rows are omitted, using the same policy as ordinary
    session saves. Old immutable bodies stay intact until the owning manifest
    commits and normal reachability collection proves them unreferenced.
    Process one payload at a time; never hydrate it into a resident session.
    """
    from .session_projection import stored_execution
    converted = {}
    for row in state.get('sessions', []):
        references = dict.get(row, MARKER, {})
        reference = references.get('execution')
        if not reference or reference.get('executionProjection') == 1:
            continue
        identity = reference['$resource']
        if identity not in converted:
            value = stored_execution(load(db, reference))
            converted[identity] = execution_reference(db, value)
        references['execution'] = dict(converted[identity])


def pending_execution_nodes(rows):
    """Small facts computed from canonical rows, including encoded fragments."""
    from .capacity import LIVE
    from .execution import pending_voice_node
    observation = voice = False
    for row in rows:
        observation |= bool(row.get('kind') == 'llm' and row.get('producerId') and row.get('phase') in LIVE)
        voice |= bool(pending_voice_node(row))
        if observation and voice:
            break
    return observation, voice


def execution_reference(db, value, *, prepared=None, resource=None, node_summary=(False, False)):
    from itertools import chain
    from .execution import LIVE_PHASES
    pending, voice = pending_execution_nodes(chain(value.get('nodes', []), value.get('retiredUsageNodes', [])))
    pending_work = any(row.get('phase') in LIVE_PHASES for row in value.get('turns', []))
    voice |= any(turn.get('lifecycle') == 'voice' and turn.get('phase') in LIVE_PHASES
                 for turn in value.get('turns', []))
    from .resource_files import put_prepared
    reference = resource if resource is not None else (
        put(db, value) if prepared is None else put_prepared(db, prepared))
    return {**reference, 'executionProjection': 1, 'pendingObservation': pending or node_summary[0],
            'pendingWork': pending_work, 'pendingVoice': voice or node_summary[1]}
