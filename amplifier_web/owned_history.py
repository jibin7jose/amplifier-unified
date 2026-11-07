"""One selector for the authenticated user's existing registered history.

This is a workspace/library read scope, not a new permission or tenant system.
Mutation, request-participant and output-owner authority remain independent.
"""

SCOPE = {'enum': ['workspace', 'all']}


def selected(args, default='workspace'):
    value = args.get('scope', default)
    if value not in {'task', 'workspace', 'all'}:
        raise ValueError('Choose task, workspace or all registered-history scope.')
    return value


def includes(owner, target, scope='workspace'):
    return (scope == 'all' or scope == 'task' and target['id'] == owner['id']
            or scope == 'workspace' and target.get('workspace') == owner.get('workspace'))


def metadata(service, identity):
    """Read current scope facts without admitting or decoding a dormant owner."""
    from .errors import AppError
    identity = identity or service.state.get('selectedSessionId')
    # Match _session's exact admitted identity and in-progress deletion guard.
    resident = next((row for row in service.state.get('sessions', ()) if row['id'] == identity), None)
    if resident is not None:
        if resident.get('_deleting'):
            raise AppError('This chat is being deleted. No new work was started.', 409)
        return {'id': resident['id'], 'workspace': resident.get('workspace')}
    directory = getattr(service, 'directory', None)
    if directory is None:
        row = service._session(identity, hydrate=False)
        return {'id': row['id'], 'workspace': row.get('workspace')}
    sql, params = directory.effective(identity=identity)
    rows = directory.db.execute(sql +
        ' SELECT id,workspace FROM directory_rows WHERE id=? OR native_id=? OR alias_id=? LIMIT 2',
        [*params, identity, identity, identity]).fetchall()
    if len(rows) > 1:
        raise AppError('This native session ID exists in more than one project. Supply nativeProject.', 409)
    if not rows:
        raise AppError('Select or create a conversation first.', 404)
    return {'id': rows[0][0], 'workspace': rows[0][1]}


def require(service, owner_id, target_id, scope='workspace'):
    owner = metadata(service, owner_id)
    target = metadata(service, target_id)
    if not includes(owner, target, scope):
        from .errors import AppError
        raise AppError('Conversation is outside the requested history scope. Use explicit scope=all for registered own-history.', 403)
    return target


# These peer-read actions expose registered conversation text or identity.
# Other peer-read policies (settings, diagnostics, live capabilities) retain
# their own domain checks. Exports/output libraries retain narrower ownership.
SESSION_READS = {
    'session.inspect': 'id', 'session.history': 'id',
    'session.titlePreview': 'sessionId', 'message.copy': 'sessionId',
    'conversation.delivery': 'sessionId', 'history.export': 'sessionId',
}


def guard_action(service, action, args, origin, caller):
    key = SESSION_READS.get(action)
    if key is None or origin in {'ui', 'user'} and 'scope' not in args:
        return
    if origin in {'ui', 'user'}:
        caller = service.state.get('selectedSessionId') or args.get(key)
    if not caller:
        from .errors import AppError
        raise AppError('History reads require a registered calling conversation.', 403)
    require(service, caller, args.get(key) or caller, selected(args))
