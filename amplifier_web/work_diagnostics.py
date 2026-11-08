"""Bounded, closed diagnostics for Work reads; never payloads or exception text."""
from collections import OrderedDict
from copy import deepcopy
import secrets

STATUSES = {'unknown', 'loading', 'ready', 'retrying', 'unavailable'}
STAGES = {'unknown', 'owner', 'source', 'projector', 'cold_resource', 'group', 'complete'}
REASONS = {'unknown', 'none', 'worker_failed', 'source_changed', 'queue_full',
           'closed', 'timeout', 'owner_changed', 'superseded', 'not_observed',
           'memory_limit', 'input_limit', 'output_limit', 'worker_stopped'}
CATEGORIES = {'unknown', 'none', 'permission', 'missing', 'timeout', 'io',
              'invalid', 'import', 'cancelled'}


def failure(error):
    # Class membership, not a dynamic class name, message, repr or traceback.
    category = next((name for cls, name in (
        (PermissionError, 'permission'), (FileNotFoundError, 'missing'),
        (TimeoutError, 'timeout'), (OSError, 'io'), (ValueError, 'invalid'),
        (ImportError, 'import')) if isinstance(error, cls)), 'unknown')
    number = error.errno if isinstance(error, OSError) else None
    return {'exceptionCategory': category,
            'errno': number if type(number) is int and 0 <= number <= 4095 else None}


def closed(value):
    """Fresh explicit output shape; unknown keys and unbounded values never pass."""
    if not isinstance(value, dict):
        value = {}
    def enum(key, choices, default='unknown'):
        item = value.get(key)
        return item if type(item) is str and item in choices else default
    number = value.get('errno')
    return {'status': enum('status', STATUSES), 'stage': enum('stage', STAGES),
            'reason': enum('reason', REASONS),
            'exceptionCategory': enum('exceptionCategory', CATEGORIES),
            'errno': number if type(number) is int and 0 <= number <= 4095 else None}


class WorkDiagnostics:
    """Only the latest read per owner; private bindings never enter the export."""
    def __init__(self):
        self.records = OrderedDict()

    def begin(self, identity, owner, source, fence):
        from .cold_display import raw
        record = {'attempt': secrets.token_hex(16), 'ownerToken': id(owner),
                  'source': source, 'fence': fence, 'status': 'loading',
                  'stage': 'owner', 'reason': 'none', 'exceptionCategory': 'none',
                  'errno': None, 'coldReference': deepcopy(raw(owner).get('_coldFields', {}).get('execution'))}
        self.records.pop(identity, None)
        self.records[identity] = record
        while len(self.records) > 8:
            self.records.popitem(last=False)
        return record

    def snapshot(self, session, source, fence, projection):
        from .cold_display import raw
        identity = session['id']
        record = self.records.get(identity)
        valid = (record is not None and record['ownerToken'] == id(session)
                 and record['source'] == source and record['fence'] == fence
                 and record['coldReference'] == raw(session).get('_coldFields', {}).get('execution'))
        value = closed(record if valid else {'status': 'unknown',
            'reason': 'owner_changed' if record else 'not_observed'})
        state = projection.get('status')
        return {'version': 1, 'sessionId': identity,
                'attempt': record['attempt'] if valid else None,
                'current': bool(valid), **value,
                # A nodes HTTP 200 is not a projection attempt or its success.
                'projectionStatus': state if valid and type(state) is str and state in STATUSES else 'unknown',
                'projectionCurrent': bool(valid and projection.get('current') is True)}


def export_session(service, session):
    """Existing JSON body, detached from hot state, with explicit missing fields.

    Unreadable cold payloads must not hide the small diagnostic. Do not replace
    a missing field with empty messages/execution or pretend the export is full.
    """
    from .cold_display import raw, load, SESSION_FIELDS
    value = raw(session)
    references = value.pop('_coldFields', {})
    missing = []
    for field in sorted(SESSION_FIELDS):
        if field in value or field not in references:
            continue
        try:
            value[field] = load(service.db, references[field])
        except (OSError, ValueError, KeyError, TypeError):
            missing.append(field)
    value['exportAvailability'] = {
        'status': 'partial' if missing else 'complete', 'unavailableFields': missing}
    value['workDiagnostics'] = service.event_log_view.windows.diagnostic(session)
    return value