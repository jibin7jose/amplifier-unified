"""Persist Unified's presentation beside, never instead of, shared transcripts."""
import json
import copy
import hashlib
import uuid
from pathlib import Path

from .host.storage import SessionStore


def view_path(home, session):
    store = SessionStore.for_app(home, session['workspace'])
    return store.directory(session.get('runtimeSessionId') or session['id']) / 'unified' / 'view.json'


def hydrate(home, state, db):
    from .cold_display import ColdRecord, MARKER
    canvas = state.get('canvas', {})
    reference = canvas.pop('$body', None)
    if reference and not canvas.get('contentResource'):
        from .canvas_library import restore_body
        canvas['contentResource'] = reference
        # Global legacy snapshots kept small document bodies inline. Preserve
        # that contract; explicitly compact large documents stay indirect.
        restore_body(canvas, db, inline_documents=True)
    for index, session in enumerate(state.get('sessions', [])):
        if session.pop('$native', False):
            session.update(workers=[], approvals=[], status='idle', historyLoaded=False, historyLoading=False)
            if 'messages' not in session.get(MARKER, {}):
                session['messages'] = []
            state['sessions'][index] = ColdRecord(session, db)
            continue
        if not session.get('$view'):
            state['sessions'][index] = ColdRecord(session, db)
            continue
        path = view_path(home, session)
        # Fail visibly rather than silently replacing lost chat history.
        if session.get('$viewPayload'):
            from .cold_display import load
            value = load(db, session['$viewPayload'])
        else:
            value = json.loads(path.read_text())
        if value.get('id') != session['id'] or not (isinstance(value.get('messages'), list)
                or isinstance(value.get(MARKER, {}).get('messages'), dict)):
            raise ValueError('A saved conversation view is invalid; its files were preserved.')
        if value.get('nativeProject'):
            # Reconcile older UI copies with the current display policy on open,
            # even when the canonical transcript has not changed since restart.
            value['historyLoaded'] = False
        state['sessions'][index] = ColdRecord(value, db)
    state['runtimeControl'] = {identity: ColdRecord(value, db)
                               for identity, value in state.get('runtimeControl', {}).items()}


def migrate(home, state):
    """Migrate legacy roots and children once, without overwriting shared files."""
    workspaces = {s.get('runtimeSessionId') or s['id']: s['workspace'] for s in state.get('sessions', [])}
    for path in (Path(home) / 'sessions').glob('*/checkpoint.json'):
        payload = json.loads(path.read_text())
        meta = payload.get('metadata', {})
        workspace = meta.get('working_dir') or meta.get('workspace') or workspaces.get(path.parent.name) or workspaces.get(meta.get('parent_id'))
        if workspace:
            SessionStore.for_app(home, workspace)._migrate(path.parent.name)


# Admission accounting must survive even when display observations are rebuilt
# from native events. Keep metadata in the existing accounting projection only;
# never persist action bodies or lazy event-file references here.
ACCOUNTING_FIELDS = {'id', 'revision', 'producerId', 'budgetRevision', 'admittedAt',
    'parentId', 'turnId', 'sessionId', 'rootSessionId', 'kind', 'phase', 'provider',
    'model', 'startedAt', 'endedAt', 'usage', 'lifecycle',
    'assignmentId', 'assignmentRevision', 'assignmentReservation'}
ACCOUNTING_USAGE_FIELDS = {'inputTokens', 'outputTokens', 'totalTokens',
    'cacheReadTokens', 'cacheWriteTokens', 'reasoningTokens', 'grossInputTokens',
    'grossTotalTokens', 'costUsd', 'costType', 'costSource'}


def _accounting_copy(value):
    """Detach simple receipts without invoking deepcopy for every scalar.

    Exact builtins only: custom mappings, keys and values retain their existing
    deepcopy hooks and nested containers retain deep isolation.
    """
    scalar_types = (str, int, float, bool, type(None))
    if type(value) in scalar_types:
        return value
    if type(value) is dict and all(type(key) is str and type(item) in scalar_types
                                  for key, item in value.items()):
        return value.copy()
    return copy.deepcopy(value)


def _receipt_copy(receipt):
    """Copy ordinary flat receipt fields without recursively copying scalars.

    Keep one memo for shared child dictionaries. Unusual keys/values use the
    original deepcopy contract, including hooks and deeper/cyclic structures.
    """
    scalar_types = (str, int, float, bool, type(None))
    result, children = {}, {}
    for key, value in receipt.items():
        if type(key) is not str:
            return copy.deepcopy(receipt)
        if type(value) in scalar_types:
            result[key] = value
        elif type(value) is dict and all(type(k) is str and type(v) in scalar_types
                                        for k, v in value.items()):
            if id(value) not in children:
                children[id(value)] = value.copy()
            result[key] = children[id(value)]
        else:
            return copy.deepcopy(receipt)
    return result


def accounting_projection(tree, *, _retired=None):
    scalar_types = (str, int, float, bool, type(None))
    rows = [(row, False) for row in tree.get('retiredUsageNodes', [])]
    retired_count = len(rows)
    rows.extend((row, True) for row in tree.get('nodes', [])
                if row.get('liveObservation') and row.get('kind') in {'llm', 'worker'}
                and not row.get('nativeHistory'))
    # Preserve the existing membership snapshot and live lookup order before
    # witnessing retired values: custom live hooks can edit the older receipts.
    prepared = (_retired([row for row, _ in rows[:retired_count]])
                if _retired is not None and type(tree) is dict else None)
    if prepared is not None:
        rows = rows[retired_count:]
    saved = ({(row.get('sessionId'), row['id']): row for row in prepared}
             if prepared is not None else {})
    for row, display in rows:
        if row.get('kind') not in {'llm', 'worker'} or not row.get('id'):
            continue
        key = (row.get('sessionId'), row['id'])
        prior = saved.get(key)
        if display and row.get('canonicalHistory') and (
            prior is None or row.get('rootSessionId') != prior.get('rootSessionId')
            or row.get('producerId') != prior.get('producerId')
            or row.get('revision', 0) <= prior.get('revision', 0)
        ):
            # A display row may acquire a later live observation, but a lazy
            # canonical read cannot mint admission or replace saved accounting.
            continue
        if prior and (row.get('revision', 0), bool(row.get('endedAt'))) < (prior.get('revision', 0), bool(prior.get('endedAt'))):
            continue
        # Scalar fields are already immutable. Retain deep isolation for any
        # structured/custom value, but avoid copying every primitive in every
        # saved accounting row on each publication.
        record = {}
        for field, value in row.items():
            if field not in ACCOUNTING_FIELDS:
                continue
            if field == 'usage':
                if type(value) is dict:
                    usage = {}
                    for name, amount in value.items():
                        # One scan both checks the plain-value fast path and
                        # filters it. A custom value still takes the original
                        # deepcopy-before-filter path, including its hooks.
                        if type(name) is not str or type(amount) not in scalar_types:
                            break
                        if name in ACCOUNTING_USAGE_FIELDS and type(amount) in (str, int, float):
                            usage[name] = amount
                    else:
                        record[field] = usage
                        continue
                usage = copy.deepcopy(value)
                if isinstance(usage, dict):
                    record[field] = {name: amount for name, amount in usage.items()
                                   if name in ACCOUNTING_USAGE_FIELDS and type(amount) in (str, int, float)}
            else:
                record[field] = value if type(value) in scalar_types else _accounting_copy(value)
        if prior:
            for field in ('assignmentId', 'assignmentRevision', 'assignmentReservation'):
                if field in prior:
                    record[field] = _accounting_copy(prior[field])
        saved[key] = record
    return list(saved.values())


def _observed_work(row, turns, accounting):
    """Retain host placement, never a second copy of canonical action content.

    This consumes the host's execution tree, not event-file or tool-result data.
    A canonical observation alone cannot establish a turn. Exact field lookup
    still needs its separately validated canonical source and observation token.
    """
    from .tool_observations import identity
    if (row.get('liveObservation') is not True
            or row.get('canonicalHistory') or row.get('nativeHistory')
            or row.get('observation') or row.get('observationWindow')
            or row.get('association') == 'unverified'):
        return None
    if any(not isinstance(row.get(key), str) or not 0 < len(row[key]) <= 200
           for key in ('id', 'turnId', 'sessionId', 'rootSessionId')):
        return None
    if row['turnId'] not in turns:
        return None
    kind = row.get('kind')
    if kind == 'tool':
        if (not identity(row.get('toolObservationId'))
                or not isinstance(row.get('toolCallId'), str)
                or not 0 < len(row['toolCallId']) <= 200):
            return None
        result = {}
    elif kind in {'llm', 'worker'}:
        # Use the same already-owned receipt as accounting, not model payloads.
        receipt = accounting.get((row['sessionId'], row['id']))
        if not receipt or not receipt.get('producerId'):
            return None
        result = _receipt_copy(receipt)
    else:
        return None
    fields = {'id', 'parentId', 'turnId', 'sessionId', 'rootSessionId', 'kind',
              'phase', 'label', 'tool', 'toolCallId', 'toolObservationId',
              'revision', 'startedAt', 'endedAt', 'runId', 'lifecycle'}
    result.update({key: value for key, value in row.items() if key in fields
                   and (value is None or type(value) in (bool, int, float)
                        or type(value) is str and len(value) <= 200)})
    result['liveObservation'] = True
    return result


def stored_execution(tree, *, _retired=None):
    """Keep owned placement/receipts and legacy history, not canonical bodies."""
    accounting = accounting_projection(tree, _retired=_retired)
    receipts = {(row.get('sessionId'), row['id']): row for row in accounting}
    turns = [row for row in tree.get('turns', []) if not any(
        row.get(key) for key in ('nativeHistory', 'canonicalHistory'))]
    turn_ids = {row['id'] for row in turns}
    nodes = []
    for row in tree.get('nodes', []):
        if not any(row.get(key) for key in ('nativeHistory', 'canonicalHistory', 'liveObservation')):
            nodes.append(row)  # Existing app-only data retains its original policy.
        elif (observed := _observed_work(row, turn_ids, receipts)) is not None:
            nodes.append(observed)
    return {**{key: value for key, value in tree.items() if key != 'plan'},
        'retiredUsageNodes': accounting, 'nodes': nodes, 'turns': turns}


def persist(home, state, cache, *, session_ids=None, by_id=None, references=None, scoped_result=False,
            undo=None, db=None, retain_native=None, message_preparation=None, pending_messages=None,
            execution_preparation=None, pending_execution=None):
    """SQLite keeps only the session list; presentation files change on demand."""
    from .automatic_history import INDEX_FIELDS
    result = dict(state)
    canvas = state.get('canvas', {})
    artifact = next((a for a in state.get('canvasArtifacts', []) if a['id'] == canvas.get('id')), None)
    if artifact and artifact.get('body'):
        result['canvas'] = {key: value for key, value in canvas.items() if key not in {'content', 'surface'}}
        from .canvas_versions import definition
        result['canvas']['$body'] = definition(artifact, canvas.get('selectedVersion'))['body']
    result['sessions'] = []
    retained = set(state.get('pinnedSessionIds', [])) | {state.get('selectedSessionId')}
    library = state.get('conversationOrganization', {})
    retained.update(library.get('archived', {}))
    retained.update(sid for row in library.get('collections', []) for sid in row['sessionIds'])
    if references is not None:
        if session_ids is None:
            references.clear()
    sessions = (state.get('sessions', []) if session_ids is None else
                (by_id[identity] for identity in session_ids if identity in by_id))
    for session in sessions:
        from .cold_display import MARKER
        if pending_execution is not None:
            pending_execution[session['id']] = None
        if pending_messages is not None:
            # Inline/native/cold-reference saves must retire older preparation
            # only when this save commits, including cold_display.retire's save
            # before it removes the resident field.
            pending_messages[session['id']] = None
        if session.get('historyManaged'):
            # Rebuild native catalog rows from their source. Persist only local
            # presentation overrides and startup selection/pin references.
            native_id = session.get('_catalogId') or uuid.uuid5(uuid.NAMESPACE_URL, f"amplifier-session:{session.get('nativeProject')}/{session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']}").hex
            revision = session.get('nativeRevision') or [0]
            baseline = session.get('_catalogRecentAt', revision[0] / 1e9)
            customized = (session['id'] in retained or session['id'] != native_id
                or retain_native is not None and retain_native(session)
                or session.get('titleSource') not in {None, 'native'}
                or bool(session.get('draftAttachments')) or bool(session.get('draft'))
                or bool(session.get('messageAnnotations')) or bool(session.get('messageQuotes'))
                or session.get('recentActivityAt', 0) > baseline
                or bool(dict.get(session, MARKER)))
            if customized:
                # Keep an explicit Stop revision in this saved reference only;
                # native index/default projections are not its authority.
                result['sessions'].append({**{key: session[key] for key in (*INDEX_FIELDS, 'interruptionRevision') if key in session}, '$native': True,
                    **({MARKER: copy.deepcopy(dict.get(session, MARKER)),
                        '_coldMessageCount': session.get('_coldMessageCount', 0),
                        '_coldNotifications': copy.deepcopy(session.get('_coldNotifications', []))}
                       if dict.get(session, MARKER) else {})})
            continue
        path = view_path(home, session)
        # Canonical content remains a lazy view, never another event/body cache.
        # Retain only the host's compact placement and exact lookup identities,
        # alongside pre-existing legacy records and owned accounting receipts.
        from .cold_display import saved as saved_cold, MARKER
        value = {key: item for key, item in saved_cold(session).items()
                 if key not in {'historyActivity', 'questions', 'workProjection'}}
        if 'execution' in value:
            raw_tree = value['execution']
            if execution_preparation is not None and db is not None and pending_execution is not None:
                tree, reference = execution_preparation.stored(db, session, raw_tree, pending_execution)
            else:
                tree, reference = stored_execution(raw_tree), None
                if db is not None and len(tree.get('retiredUsageNodes', ())) >= 128:
                    from .cold_display import execution_reference
                    reference = execution_reference(db, tree)
            if reference is not None:
                value[MARKER] = {**value.get(MARKER, {}), 'execution': reference}
                value.pop('execution')
            else:
                value['execution'] = tree
        messages = value.get('messages')
        if (db is not None and isinstance(messages, list)
                and sum(len(message.get('text', '')) for message in messages
                        if isinstance(message, dict) and isinstance(message.get('text'), str)) >= 16000):
            from .cold_display import notifications
            from .resource_files import put, put_prepared
            # Storage projection only: live lists and borrowed nested aliases
            # remain mutable. Only exact type-aware content equality against a
            # detached committed snapshot may reuse prepared bytes. Resource
            # adoption, file validation, cold readers and GC remain unchanged.
            from .media_saved_aliases import binding_hint
            message_reference = (put_prepared(db, message_preparation.prepare(session, messages, pending_messages))
                                 if message_preparation is not None else put(db, messages))
            value[MARKER] = {**value.get(MARKER, {}),
                             'messages': binding_hint(session, message_reference, messages)}
            value['_coldMessageCount'] = len(messages)
            value['_coldNotifications'] = notifications(session)
            value.pop('messages')
        # The compatible view and immutable resource represent the same value.
        # Encode once in the existing resource format, including exact Unicode.
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        prior = references.get(session['id'], {}) if references is not None else {}
        payload = prior.get('$viewPayload') if cache.get(str(path)) == text else None
        if payload is None and db is not None:
            from .resource_files import Prepared, put_prepared
            # SQLite's committed pointer owns the exact immutable projection.
            # view.json remains a compatible presentation, never the authority
            # for a newer transaction that failed before its pointer committed.
            encoded = text.encode()
            payload = put_prepared(db, Prepared(text, hashlib.sha256(encoded).hexdigest(), len(encoded)))
        if cache.get(str(path)) != text:
            if undo is not None:
                # Restore the pre-transaction presentation if SQLite rejects its
                # matching manifest. Canonical transcript/event files are never
                # part of this rollback list.
                undo.append((path, path.read_text() if path.exists() else None))
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            SessionStore._atomic(path, text)
            cache[str(path)] = text
        # Passive owner checks need present scalar facts without loading the view
        # body. Do not invent defaults or backfill older sparse references.
        result['sessions'].append({**{key: session[key] for key in (
            'id', 'workspace', 'runtimeSessionId', 'sessionKind', 'workingDirectory',
            'executionRevision', 'interruptionRevision') if key in session}, '$view': 1,
                                   **({'$viewPayload': payload} if payload else {}),
                                   **({MARKER: copy.deepcopy(value[MARKER])} if value.get(MARKER) else {})})
    if references is not None:
        if session_ids is not None:
            retained_ids = {row['id'] for row in result['sessions']}
            for identity in session_ids - retained_ids:
                references.pop(identity, None)
        references.update((row['id'], row) for row in result['sessions'])
        if not scoped_result:
            result['sessions'] = list(references.values())
    return result
