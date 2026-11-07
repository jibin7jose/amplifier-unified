"""Passive, bounded review derived from existing workspace authorities.

No new ledger: directory metadata owns the page, assignment receipts own claims,
and command receipts own saved source observations. Never hydrate a transcript or
admit a dormant owner to discover whether it needs attention.
"""
import base64
import json
import hashlib
import time

from .coordination import ACTIVE
from .history_directory import COLUMNS


def _cursor(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()


def _after(value, source):
    try:
        item = json.loads(base64.b64decode(value, altchars=b'-_', validate=True))
        if (not isinstance(item, list) or len(item) != 4
                or item[:2] != [source['id'], hashlib.sha256(source['workspace'].encode()).hexdigest()]
                or not all(isinstance(part, str) for part in item[2:])):
            raise ValueError()
        return item[2:]
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('This review page belongs to a different chat or workspace. Refresh the review.') from None


def _authority(app, directory, residents, sid):
    """Reuse scalar authority; old unrefreshed projections stay unavailable."""
    row = directory.resolve(sid)
    if row is None:
        return None
    live = residents.get(row['id'])
    if live is not None:
        row.update(assignmentId=live.get('assignmentId'),
                   interruptionRevision=live.get('interruptionRevision', 0))
    return row


def _claims(app, source, lookup):
    if 'interruptionRevision' not in source:
        return {'status': 'unavailable', 'items': [], 'total': None}
    value = app.assignments.current(source)
    if not value:
        return {'status': 'unassigned', 'items': [], 'total': 0}
    if app.assignments.tasks.member(value, source['id'], lookup=lookup) is None:
        return {'status': 'unavailable', 'items': [], 'total': None}
    items = []
    for task in value.get('tasks', {}).values():
        if task.get('status') != 'claimed':
            continue
        claim = app.assignments.tasks.claim_status(value, task, lookup=lookup)
        if claim and claim['status'] != 'current':
            items.append({'taskId': task['id'], 'title': task['title'][:200],
                          'revision': task['revision'], 'holderSessionId': claim['sessionId'],
                          'claimId': claim['id'], 'status': claim['status'], 'detail': claim['detail']})
    return {'status': value['status'], 'assignmentId': value['id'], 'revision': value['revision'],
            'items': items[:8], 'total': len(items), 'truncated': len(items) > 8}


def _chat(directory, row, live):
    status = row.get('status', 'unknown')
    pending = row.get('pendingConfiguration') or {}
    approvals = [item for item in row.get('approvals', []) if item.get('status', 'pending') in {None, 'pending'}]
    # A saved ordinary approval refers to a native waiter, not a durable grant.
    # Exact owner admission reconciles it; discovery must not promise it is live.
    unverified = [item for item in approvals if live is None and not item.get('id', '').startswith('capability:')]

    return {'sessionId': row['id'], 'title': row.get('title', 'Untitled chat')[:200],
            'status': status, 'activityObserved': live is not None,
            'executionUnverified': live is None and status in ACTIVE,
            'commissioned': bool(row.get('collaboration')),
            'approvals': len(approvals) - len(unverified), 'unverifiedApprovals': len(unverified),
            'expiredApprovals': sum(item.get('status') == 'expired' and item.get('resolutionReason') == 'host_restarted' for item in row.get('approvals', [])),
            'questions': sum(item.get('status') == 'pending' for item in row.get('questions', [])),
            'configurationPending': bool(row.get('configurationPending') or row.get('fileAccessPending')
                                         or pending.get('phase') in {'queued', 'applying'}),
            'configurationBusy': bool(row.get('configurationBusy')),
            'historyReadOnlyReason': row.get('historyReadOnlyReason'),
            'workspaceAvailable': row.get('workspaceAvailable'),
            'executionDirectory': row.get('workingDirectory') or row.get('workspace'),
            'executionRevision': row.get('executionRevision'),
            'responseAt': (row.get('completion') or {}).get('at'),
            'handoffs': row.get('reviewHandoffs', {'items': [], 'truncated': False, 'available': False})}


def review(app, args, origin, caller):
    from .errors import AppError
    if origin not in {'ui', 'user'} and (not caller or args['sessionId'] != caller):
        raise AppError('Review work from the calling conversation.', 403)
    residents = {row['id']: row for row in app.state['sessions']}
    directory = app.directory.read_snapshot(app.state)
    try:
        source = _authority(app, directory, residents, args['sessionId'])
        if source is None or source.get('sessionKind') != 'root':
            raise AppError('Choose a saved root conversation to review its workspace.', 404)
        if origin not in {'ui', 'user'}:
            from .collaboration import PRINCIPAL
            actor = PRINCIPAL.get()
            native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
            if actor and actor != native:
                from .coordination_guidance import ROOT_SCOPE_MESSAGE
                raise AppError(ROOT_SCOPE_MESSAGE, 403)
        if not source.get('workspace'):
            raise AppError('This chat has no available workspace scope to review.', 409)
        where, params = "session_kind='root' AND workspace=?", [source['workspace']]
        if args.get('after'):
            ordinal, identity = _after(args['after'], source)
            where += ' AND (ordinal>? OR (ordinal=? AND id>?))'
            params += [ordinal, ordinal, identity]
        limit = args.get('limit', 20)
        # This action scopes by canonical path, unlike sidebar registry-ID
        # selection. Reuse its indexed native candidate pruning with every ID
        # eligible for exact-path fallback; local overlays still shadow natives.
        directory.db.create_function('directory_known_workspace', 1, lambda identity: 0, deterministic=True)
        sql, bound = directory.effective(native_root_workspace=(None, [source['workspace']]))
        raw = directory.db.execute(sql + ' SELECT ' + ','.join(COLUMNS) + ' FROM directory_rows WHERE '
            + where + ' ORDER BY ordinal,id LIMIT ?', (*bound, *params, limit + 1)).fetchall()

        rows = [directory.decode(item) for item in raw[:limit]]
        lookup_cache = {source['id']: source}
        def lookup(sid):
            if sid not in lookup_cache:
                row = _authority(app, directory, residents, sid)
                lookup_cache[sid] = row if row and 'interruptionRevision' in row else None
            return lookup_cache[sid]
        try:
            claims = _claims(app, source, lookup)
        except (ValueError, AppError):
            claims = {'status': 'unavailable', 'items': [], 'total': None}
        # Only the source chat's own receipts, never a peer's private result body.
        observations = [{key: value for key, value in zip(('id', 'kind', 'status', 'capturedAt'), row)}
            for row in directory.db.execute("""SELECT id,json_extract(receipt,'$.result.kind'),
                json_extract(receipt,'$.result.status'),json_extract(receipt,'$.result.capturedAt')
                FROM commands WHERE json_extract(receipt,'$.commandAction') IN
                ('integration.capture','integration.check','integration.collect')
                AND json_extract(receipt,'$.result.sessionId')=? ORDER BY rowid DESC LIMIT 4""", (source['id'],))]
        next_page = None
        if len(raw) > limit:
            last = raw[limit - 1]
            next_page = _cursor([source['id'], hashlib.sha256(source['workspace'].encode()).hexdigest(), last[COLUMNS.index('ordinal')], last[0]])
        return {'sessionId': source['id'], 'workspace': source['workspace'], 'capturedAt': time.time(),
                'items': [_chat(directory, row, residents.get(row['id'])) for row in rows],
                'nextAfter': next_page, 'claims': claims, 'sourceObservations': observations,
                'installedApp': app.installed_build.project(), 'workerAdoption': 'not_inspected',
                'sourceFreshness': 'not_inspected',
                'detail': 'Saved observations do not establish current source, external PR status, worker adoption or safe replay. Independent chats remain independent.'}
    finally:
        directory.close()
