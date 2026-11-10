"""Latest app-state records, committed together; not a replay log.

Routine working-set saves visit admitted keys without deleting dormant owners.
Explicit checkpoints alone reconcile a complete legacy snapshot. Unchanged values
stay untouched; load() remains the complete authority for retention/export, while
load_working() and load_owner() serve bounded admission. Canonical events and
command receipts are separate.
"""
import json

EMPTY_BASE = json.dumps({'revision': 0, 'sessions': [], 'runtimeControl': {}})


UPSERT = '''INSERT INTO state_records VALUES(?,?,?)
    ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value
    WHERE state_records.value != excluded.value'''


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS state_records (
        kind TEXT NOT NULL CHECK(kind IN ('session','runtime','revision','global')),
        id TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,id))''')


def load(db, *, decode=json.loads):
    row = db.execute('SELECT value FROM state WHERE id=1').fetchone()
    if row is None:
        return None
    records = (db.execute('SELECT kind,id,value FROM state_records') if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='state_records'").fetchone() else None)
    return restore(row[0], records, decode=decode)


def restore(base, records, *, decode=json.loads):
    """Apply the same validated overlays to live SQL or detached immutable rows."""
    state = decode(base)
    if records is None:
        return state
    sessions = {row['id']: row for row in state.get('sessions', [])}
    controls = state.setdefault('runtimeControl', {})
    for kind, identity, text in records:
        value = decode(text)
        if kind == 'revision':
            if identity != '' or type(value) is not int or value < state['revision']:
                raise ValueError('Invalid saved state revision; original records were preserved.')
            state['revision'] = value
        elif kind == 'session':
            if value is not None and (not isinstance(value, dict) or value.get('id') != identity):
                raise ValueError('Invalid saved session record; original records were preserved.')
            if value is None:
                sessions.pop(identity, None)
            else:
                sessions[identity] = value
        elif kind == 'runtime':
            if value is not None and not isinstance(value, dict):
                raise ValueError('Invalid saved runtime record; original records were preserved.')
            if value is None:
                controls.pop(identity, None)
            else:
                controls[identity] = value
        elif kind == 'global':
            if identity in {'sessions', 'runtimeControl', 'revision'} or not isinstance(value, dict) or set(value) != {'present', 'value'} or type(value['present']) is not bool:
                raise ValueError('Invalid saved global record; original records were preserved.')
            if value['present']:
                state[identity] = value['value']
            else:
                state.pop(identity, None)
    state['sessions'] = list(sessions.values())
    return state


def checkpoint(db, state):
    """Reconcile a complete snapshot without rewriting unchanged records.

    The caller commits this alongside view pointers and private client state. No
    in-memory acknowledgement/cache can get ahead of a failed SQLite transaction.
    Replacing a legacy full base and all of its overlays is likewise atomic. Older
    code that reads only the base row cannot read this layout; use load().
    """
    base = {'revision': 0, 'sessions': [{'id': row['id']} for row in state.get('sessions', [])],
            'runtimeControl': {}}
    db.execute('''INSERT INTO state VALUES(1,?)
        ON CONFLICT(id) DO UPDATE SET value=excluded.value
        WHERE state.value != excluded.value''', (json.dumps(base),))
    remaining = set(db.execute('SELECT kind,id FROM state_records'))

    def records():
        yield 'revision', '', state['revision']
        for row in state.get('sessions', []):
            yield 'session', row['id'], row
        for identity, value in state.get('runtimeControl', {}).items():
            yield 'runtime', identity, value
        for key, value in state.items():
            if key not in {'sessions', 'runtimeControl', 'revision'}:
                yield 'global', key, {'present': True, 'value': value}

    def encoded():
        for kind, identity, value in records():
            remaining.discard((kind, identity))
            yield kind, identity, json.dumps(value)

    db.executemany(UPSERT, encoded())
    db.executemany('DELETE FROM state_records WHERE kind=? AND id=?', remaining)


def save(db, state, references, session_ids, global_keys=()):
    """Replace only the latest changed references and runtime records.

    The owning service commits these with its revision and dirty private client
    records. Full saves reconcile unknown scopes; all readers use load(), even
    after a clean shutdown. Unchanged runtime/configuration records stay put.
    """
    from .cold_display import saved
    values = [('revision', '', state['revision'])]
    controls = state.get('runtimeControl', {})
    for identity in session_ids:
        values.append(('session', identity, references.get(identity)))
        values.append(('runtime', identity, saved(controls[identity]) if identity in controls else None))
    for key in global_keys:
        if key in {'sessions', 'runtimeControl', 'revision'}:
            raise ValueError('Global records cannot replace scoped session authority.')
        values.append(('global', key, {'present': key in state, 'value': state.get(key)}))
    # The canonical conditional write already knows whether a global actually
    # changed. Return those owner keys for post-commit read-model invalidation;
    # readers need neither a second registry scan nor a serialized-value hash.
    changed_globals = set()
    for kind, identity, value in values:
        changed = db.execute(UPSERT, (kind, identity, json.dumps(value))).rowcount
        if kind == 'global' and changed:
            changed_globals.add(identity)
    return changed_globals


def load_working(db, identities=()):
    """Load globals and explicitly admitted owners, never dormant records.

    Only available after the directory migration has atomically converted the
    legacy full base. Complete load() remains the durable retention/export owner.
    """
    from .history_directory import ready
    if not ready(db):
        return load(db)
    state = {'revision': 0, 'sessions': [], 'runtimeControl': {}}
    for kind, identity, raw in db.execute("SELECT kind,id,value FROM state_records WHERE kind IN ('revision','global')"):
        value = json.loads(raw)
        if kind == 'revision':
            if identity != '' or type(value) is not int or value < 0:
                raise ValueError('Invalid saved state revision; original records were preserved.')
            state['revision'] = value
        elif (identity in {'sessions', 'runtimeControl', 'revision'} or not isinstance(value, dict)
              or set(value) != {'present', 'value'} or type(value['present']) is not bool):
            raise ValueError('Invalid saved global record; original records were preserved.')
        elif value['present']:
            state[identity] = value['value']
    requested = set(identities)
    requested.update(identity for identity, in db.execute('SELECT id FROM history_local_rows WHERE startup=1'))
    if state.get('selectedSessionId'):
        requested.add(state['selectedSessionId'])
    for identity in requested:
        session, runtime = load_owner(db, identity)
        if session is not None:
            state['sessions'].append(session)
        if runtime is not None:
            state['runtimeControl'][identity] = runtime
    return state


def load_owner(db, identity):
    """Exact durable presentation/runtime pointers; no other owner's JSON."""
    session = runtime = None
    for kind, raw in db.execute("SELECT kind,value FROM state_records WHERE id=? AND kind IN ('session','runtime')", (identity,)):
        value = json.loads(raw)
        if value is not None and (not isinstance(value, dict) or kind == 'session' and value.get('id') != identity):
            raise ValueError('Invalid saved owner record; original records were preserved.')
        if kind == 'session':
            session = value
        else:
            runtime = value
    return session, runtime


def save_working(db, state, references, identities, *, global_keys=None, deleted=(), preserve_globals=()):
    """Save the working set without inferring deletion from non-residency.

    Explicit owner deletion is the only operation which clears an absent durable
    record. This transaction also contains the scalar directory projection and
    private client pointers; eviction itself never writes a tombstone.
    """
    identities = set(identities)
    deleted = set(deleted)
    if global_keys is None:
        global_keys = set(state) - {'sessions', 'runtimeControl', 'revision'}
        old = {key for key, in db.execute("SELECT id FROM state_records WHERE kind='global'")}
        global_keys.update(old)
    # An owner with an unreadable commit must not overwrite its durable value
    # from speculative memory, including during a full/shutdown checkpoint.
    global_keys = set(global_keys) - set(preserve_globals)
    if deleted:
        references = {identity: value for identity, value in references.items() if identity not in deleted}
        state = {**state, 'runtimeControl': {identity: value for identity, value in state.get('runtimeControl', {}).items()
                                            if identity not in deleted}}
    changed_globals = save(db, state, references, identities | deleted, global_keys)
    # Membership now lives in keyed records, not a complete ID array which must
    # be decoded at every restart. Explicit load() already enumerates records.
    db.execute('INSERT INTO state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value '
               'WHERE state.value != excluded.value', (EMPTY_BASE,))
    return changed_globals
