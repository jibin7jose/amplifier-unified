"""Existing Full authority retained by feedback, independent of a browser draft."""


def enabled(service, origin, caller):
    return bool(origin in {'agent', 'system'} and caller
                and service.access_mode.full(service._session(caller, hydrate=False)))


def capture(service, origin, caller):
    if origin not in {'agent', 'system'} or not caller:
        return None
    source = service._session(caller, hydrate=False)
    authority = service.access_mode.capture_full(source, origin)
    if authority is None:
        return None
    return {**authority,
        'workspace': source['workspace'],
        'nativeSessionId': source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id'],
        'executionRevision': source.get('executionRevision', 0),
        'interruptionRevision': source.get('interruptionRevision', 0),
    }


def check(service, authority):
    """Accepted background delivery outlives a turn, but never its Stop boundary."""
    from .errors import AppError
    source = service._session(authority['sessionId'], hydrate=False)
    if (not service.access_mode.full(source, authority['accessRevision'])
            or source['workspace'] != authority['workspace']
            or (source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']) != authority['nativeSessionId']
            or any(source.get(key, 0) != authority[key]
                   for key in ('executionRevision', 'interruptionRevision'))):
        raise AppError('Feedback authority changed or its work was stopped. No further writes were started.', 409)


def rows(service, owner):
    """Only the bounded selected initial files; never hydrate another draft."""
    import json
    return [json.loads(row[0]) for row in service.db.execute(
        "SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$._sessionId')=? "
        "AND json_extract(metadata,'$._selected')=1 AND json_extract(metadata,'$._feedbackId') IS NULL",
        (owner,))]


def public_file(row):
    return {key: value for key, value in row.items() if not key.startswith('_')}
