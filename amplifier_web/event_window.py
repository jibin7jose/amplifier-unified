"""Bounded, requested event observations; never an execution/accounting tree.

This reader runs in the disposable history worker. It does not construct an
EventIndex, read a transcript, or follow a delegation. A cut prefix cannot prove
global call/prompt uniqueness, so observations retain their exact source identity
and do not acquire inferred turn bindings or complete usage totals.
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import stat

from .session_files import amplifier_home, project_slug, source_change_time

MAX_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 512
MAX_ITEMS = 100
BLOCK_BYTES = 64 * 1024
PREVIEW_CHARS = 512
MAX_CURSOR_BYTES = 2048
FIELD_KEYS = frozenset({'tool_input', 'arguments', 'input', 'prompt', 'result',
                        'tool_result', 'error', 'error_message', 'raw', 'raw_request', 'raw_response'})


class StaleWindow(ValueError):
    """The exact owner, requested window or canonical source changed."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'), sort_keys=True).encode()


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _identity(value):
    return (isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 512
            and value not in {'.', '..'} and not any(c in value for c in '/\\\0'))


def _source(session):
    public = session.get('id')
    identity = session.get('nativeIdentity') or session.get('runtimeSessionId') or public
    project = session.get('nativeProject')
    if project is None:
        workspace = session.get('workspace')
        if not isinstance(workspace, str) or not workspace:
            raise ValueError('Missing canonical workspace')
        project = project_slug(workspace)
    if not all(_identity(value) for value in (public, identity, project)):
        raise ValueError('Invalid canonical source identity')
    configured = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', '').strip()
    base = Path(configured).expanduser() if configured and '${' not in configured else None
    base = base if base is not None and base.is_absolute() else amplifier_home() / 'projects'
    base = base.resolve()
    path = base / project / 'sessions' / identity / 'context-intelligence' / 'events.jsonl'
    if not path.resolve().is_relative_to(base):
        raise ValueError('Canonical events escaped their configured owner')
    return path, identity, _digest([public, identity, project, str(base)])


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, source_change_time(info)]


def _cursor(key, owner, window, stamp, end, aligned=True):
    raw = _json({'v': 1, 'owner': owner, 'window': window, 'source': stamp,
                 'end': end, 'aligned': aligned})
    return base64.urlsafe_b64encode(hmac.digest(key, raw, 'sha256') + raw).decode().rstrip('=')


def _end(before, key, owner, window, stamp):
    if before is None:
        return stamp[2], True
    if not isinstance(before, str) or not 0 < len(before) <= MAX_CURSOR_BYTES:
        raise StaleWindow('Invalid event window cursor')
    try:
        decoded = base64.b64decode(before + '=' * (-len(before) % 4), altchars=b'-_', validate=True)
        signature, raw = decoded[:32], decoded[32:]
        if not hmac.compare_digest(signature, hmac.digest(key, raw, 'sha256')):
            raise ValueError('Invalid signature')
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise StaleWindow('Invalid event window cursor') from exc
    if (not isinstance(value, dict) or set(value) != {'v', 'owner', 'window', 'source', 'end', 'aligned'}
            or type(value['v']) is not int or value['v'] != 1
            or value['owner'] != owner or value['window'] != window
            or value['source'] != stamp or type(value['end']) is not int
            or type(value['aligned']) is not bool
            or not 0 <= value['end'] <= stamp[2]):
        raise StaleWindow('The requested event window changed')
    return value['end'], value['aligned']


def _bounded_json(raw, guard):
    """Bound structural expansion before Python allocates decoded containers."""
    quoted = escaped = False
    depth = values = 0
    for position, byte in enumerate(raw):
        if position % 16384 == 0:
            guard()
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            values += 1
            if depth > 64:
                raise ValueError('Record shape exceeds this display window')
        elif byte in (93, 125):
            depth -= 1
        elif byte in (44, 58):
            values += 1
        if values > 65536:
            raise ValueError('Record shape exceeds this display window')
    def reject(_value):
        raise ValueError('Non-finite JSON value')
    return json.loads(raw, parse_constant=reject)


def _preview(value):
    if isinstance(value, str):
        return value[:PREVIEW_CHARS]
    pieces, left = [], PREVIEW_CHARS
    for piece in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
        pieces.append(piece[:left])
        left -= len(piece)
        if left <= 0:
            break
    return ''.join(pieces)


def _scalar(value):
    return value if isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 512 else None


def _time(value):
    if type(value) in (int, float):
        return value if math.isfinite(value) else None
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


from .tool_observations import outcome_summary as _outcome_summary


def _observation(event, raw, offset, path, identity):
    if not isinstance(event, dict) or not isinstance(event.get('data'), dict):
        return None
    name, data = event.get('event'), event['data']
    if not isinstance(name, str) or len(name) > 128:
        return None
    if name.startswith('tool:'):
        kind = 'tool'
    elif name.startswith(('llm:', 'provider:')):
        kind = 'llm'
    elif name in {'delegate:agent_spawned', 'delegate:agent_resumed'}:
        kind = 'worker'
    elif name == 'prompt:submit':
        kind = 'prompt'
    else:
        return None
    sha = hashlib.sha256(raw).hexdigest()
    reference = {'path': str(path), 'offset': offset, 'bytes': len(raw), 'sha256': sha}
    row = {'id': f'event:{identity}:{offset}:{sha[:16]}', 'kind': kind,
           'event': name, 'sourceSessionId': identity,
           'sessionId': _scalar(data.get('session_id') or event.get('session_id')) or identity,
           'eventOrder': offset, 'phase': 'recorded', 'observation': True,
           'association': 'unverified', '_eventFields': {}}
    for target, keys in {
        'toolCallId': ('tool_call_id', 'call_id'), 'requestId': ('request_id',),
        'messageId': ('message_id',), 'inputId': ('input_id',),
        'childSessionId': ('sub_session_id',), 'parentSessionId': ('parent_session_id',),
        'provider': ('provider',), 'model': ('model',),
        'label': ('tool_name', 'tool', 'agent'),
    }.items():
        value = next((_scalar(data[key]) for key in keys if _scalar(data.get(key))), None)
        if value is not None:
            row[target] = value
    row.setdefault('label', {'llm': 'Model observation', 'tool': 'Tool observation',
                            'worker': 'Worker observation', 'prompt': 'Saved input'}[kind])
    if kind == 'llm' and name in {'llm:request', 'llm:response'}:
        row['label'] = 'Provider request' if name == 'llm:request' else 'Provider response'
        # Exact host context is stamped before canonical capture. A cut window
        # cannot establish call identity from timing or apparent uniqueness.
        call = _scalar(data.get('unified_call_id'))
        if call is not None:
            row['_hostCallId'] = call
    at = _time(event.get('timestamp') if event.get('timestamp') is not None else data.get('timestamp'))
    if at is not None:
        row['createdAt'] = at
    for field, keys in {
        'input': ('tool_input', 'arguments', 'input', 'prompt'),
        'output': ('result', 'tool_result'), 'error': ('error', 'error_message'),
        'request': ('raw', 'raw_request') if name == 'llm:request' else (),
        'response': ('raw', 'raw_response') if name == 'llm:response' else (),
    }.items():
        key = next((key for key in keys if key in data), None)
        if key is None:
            continue
        row['_eventFields'][field] = {**reference, 'key': key}
        row[field + 'Detail'] = {'part': 'nodes', 'id': row['id'], 'field': field,
            'digest': hashlib.sha256(('event-field-v1:' + sha + ':' + key).encode()).hexdigest(),
            'digestKind': 'event-field-v1'}
        if field in {'request', 'response'}:
            capture = data.get(field + '_capture', data.get('request_capture', {}))
            if isinstance(capture, dict):
                capture = {name: value for name, value in capture.items()
                    if name in {'redacted', 'truncated'} and type(value) is bool
                    or name == 'limit_chars' and type(value) is int}
                if capture:
                    row[field + 'Capture'] = capture
            from .provider_payloads import metadata
            files = data.get('provider_payloads')
            recording = metadata(files.get(key)) if isinstance(files, dict) else None
            if recording:
                row[field + 'Recording'] = recording
        if field not in {'request', 'response'}:
            row[field] = _preview(data[key])
        if field == 'input' and kind == 'tool':
            # Only header hints from the accepted input, never reconstructed
            # arguments or evidence that the operation completed.
            from .tool_observations import input_hints
            row.update(input_hints(data.get('tool_name') or data.get('tool'), data[key]))
    if kind == 'tool':
        from .tool_observations import identity as tool_identity
        token = tool_identity(data.get('unified_tool_observation_id'))
        if token:
            row['_hostToolId'] = token
        outcome = _outcome_summary(name, data)
        if outcome:
            row['outcomeSummary'] = outcome
        from .delegate_exchange import exchange_fields, select, source_field, tool_name
        input_ref = row['_eventFields'].get('input')
        fields = exchange_fields(tool_name(row), data.get(input_ref['key']) if input_ref else None)
        if fields:
            for field in fields:
                source = row['_eventFields'].get(source_field(field))
                if source is None or field == 'delegateResponse' and (name == 'tool:error'
                        or data.get('error') or data.get('error_message')):
                    continue
                value = select(data[source['key']], field)
                if not isinstance(value, str):
                    continue
                row[field] = _preview(value)
                row['_eventFields'][field] = {**source, 'delegateField': field}
                row[field + 'Detail'] = {'part': 'nodes', 'id': row['id'], 'field': field,
                    'digest': hashlib.sha256(('event-field-v1:' + sha + ':' + source['key'] + ':' + field).encode()).hexdigest(),
                    'digestKind': 'event-field-v1', 'length': len(value)}
    # Neither usage nor lifecycle aggregates are derived from a partial range.
    return row


class _ReverseLines:
    def __init__(self, stream, end, guard, *, aligned=True, initial_bytes=0):
        self.stream, self.position, self.guard = stream, end, guard
        self.buffer, self.buffer_end = b'', end
        self.bytes_read = initial_bytes
        self.partial = False
        self.blocked = False
        self.initial = True
        self.discard = not aligned
        self.gaps = []
        self.continuation = None
        self.progress = False

    def __iter__(self):
        while True:
            self.guard()
            if self.discard and self.buffer:
                boundary = self.buffer.rfind(b'\n')
                if boundary >= 0 or self.position == 0:
                    start = self.position + boundary + 1
                    self.gaps.append({'start': start, 'end': self.buffer_end, 'reason': 'record_limit'})
                    self.buffer = self.buffer[:boundary + 1]
                    self.buffer_end = start
                    self.continuation = (start, True)
                    self.progress = True
                    self.discard = self.initial = False
            if self.initial and self.buffer:
                # An unfinished final record belongs to the next append. It is
                # never decoded or mistaken for a complete empty history.
                if self.buffer.endswith(b'\n'):
                    self.initial = False
                else:
                    self.partial = True
                    boundary = self.buffer.rfind(b'\n')
                    if boundary >= 0:
                        self.buffer = self.buffer[:boundary + 1]
                        self.buffer_end = self.position + boundary + 1
                        self.initial = False
                        self.partial = True
            if not self.initial and not self.discard and self.buffer:
                boundary = self.buffer.rfind(b'\n', 0, len(self.buffer) - 1)
                if boundary >= 0 or self.position == 0:
                    start = boundary + 1
                    raw = self.buffer[start:]
                    offset = self.buffer_end - len(raw)
                    self.buffer, self.buffer_end = self.buffer[:start], offset
                    self.progress = True
                    yield offset, raw
                    continue
            if self.position == 0:
                self.partial = self.partial or bool(self.buffer)
                self.continuation = (0, True)
                return
            size = min(BLOCK_BYTES, self.position, MAX_BYTES - self.bytes_read)
            if size == 0:
                if self.progress and not self.discard and not self.initial:
                    # This request spent part of its budget on newer rows or a
                    # huge-record continuation. Retry the next whole record on
                    # the next explicit page instead of dropping its suffix.
                    self.continuation = (self.buffer_end, True)
                    return
                self.blocked = True
                self.gaps.append({'start': self.position, 'end': self.buffer_end, 'reason': 'read_limit'})
                self.continuation = (self.position, False)
                return
            self.position -= size
            self.stream.seek(self.position)
            data = self.stream.read(size)
            self.bytes_read += len(data)
            if len(data) != size:
                raise StaleWindow('The event source changed during its read')
            self.buffer = data + self.buffer


def read_window(session, *, window_key, cursor_key, before=None, limit=MAX_ITEMS, check_budget=None):
    """Read one explicit recent/older event page without loading its prefix.

    Returned references are private owner facts, never client-supplied paths.
    A caller must revalidate the owner/window and source receipt before publishing.
    Even an exhausted root file does not certify descendant or transcript coverage.
    """
    if not isinstance(window_key, str) or not 0 < len(window_key.encode('utf-8')) <= 256:
        raise ValueError('Invalid message window identity')
    if type(limit) is not int or not 1 <= limit <= MAX_ITEMS:
        raise ValueError('Choose 1 to 100 event observations')
    if not isinstance(cursor_key, bytes) or len(cursor_key) != 32:
        raise ValueError('A host-owned 32-byte cursor key is required')
    path, identity, owner = _source(session)
    guard = check_budget or (lambda: None)
    guard()
    items, scanned, invalid, earliest = [], 0, 0, None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
                             | getattr(os, 'O_NOFOLLOW', 0))
        stream = os.fdopen(descriptor, 'rb')
    except FileNotFoundError:
        if before is not None:
            raise StaleWindow('The event source is no longer available')
        return {'status': 'ready', 'windowKey': window_key,
                'generation': _digest([owner, window_key, None]), 'items': [], 'before': None,
                'coverage': {'kind': 'event-window', 'historyComplete': False,
                    'rootExhausted': True, 'sourceCount': 0, 'rangeStart': 0, 'rangeEnd': 0,
                    'scannedEvents': 0, 'scannedBytes': 0, 'unassociatedCount': 0,
                    'partialRecord': False, 'pagingBlocked': False, 'invalidEvents': 0},
                'unavailableRanges': [], 'revisions': [[str(path), None]]}
    with stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Canonical events must be a regular file')
        stamp = _stamp(info)
        end, aligned = _end(before, cursor_key, owner, window_key, stamp)
        boundary_bytes = 0
        if before is not None and end and aligned:
            stream.seek(end - 1)
            boundary_bytes = 1
            if stream.read(1) != b'\n':
                raise StaleWindow('The event cursor is not a record boundary')
        lines = _ReverseLines(stream, end, guard, aligned=aligned, initial_bytes=boundary_bytes)
        for offset, raw in lines:
            scanned += 1
            earliest = offset
            try:
                row = _observation(_bounded_json(raw, guard), raw, offset, path, identity)
            except (ValueError, TypeError, OverflowError, RecursionError):
                invalid += 1
                lines.gaps.append({'start': offset, 'end': offset + len(raw),
                                   'reason': 'record_validation'})
                row = None
            if row is not None:
                items.append(row)
            if scanned >= MAX_EVENTS or len(items) >= limit:
                break
        if _stamp(os.fstat(stream.fileno())) != stamp or _stamp(path.stat()) != stamp:
            raise StaleWindow('The event source changed during its read')
    start = earliest if earliest is not None else end
    next_aligned = True
    if lines.continuation is not None and lines.continuation[0] < start:
        start, next_aligned = lines.continuation
    exhausted = start == 0 or (end == 0 and not lines.blocked)
    # Oversized records leave explicit gaps and a signed unaligned continuation;
    # older requests advance without decoding a suffix as a complete record.
    cursor = _cursor(cursor_key, owner, window_key, stamp, start, next_aligned) if start > 0 else None
    items.reverse()
    coverage = {'kind': 'event-window', 'historyComplete': False,
                'rootExhausted': exhausted, 'sourceCount': 1,
                'rangeStart': start, 'rangeEnd': end,
                'scannedEvents': scanned, 'scannedBytes': lines.bytes_read,
                'unassociatedCount': len(items), 'partialRecord': lines.partial,
                'pagingBlocked': lines.blocked, 'invalidEvents': invalid}
    return {'status': 'ready', 'windowKey': window_key,
            'generation': _digest([owner, window_key, stamp, start, end]),
            'items': items, 'coverage': coverage, 'before': cursor,
            'unavailableRanges': list(reversed(lines.gaps)),
            'revisions': [[str(path), [[stamp[0], stamp[1]], *stamp[2:]]]]}


def read_field(session, reference, *, offset=0, limit=4096, check_budget=None):
    """Read a bounded text slice from an accepted private observation reference.

    The host resolves a row/field in its accepted window; HTTP callers never
    provide this reference. Exact canonical bytes remain the detail authority.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16384:
        raise ValueError('Invalid event field page')
    path, _, _ = _source(session)
    keys = {'path', 'offset', 'bytes', 'sha256', 'key'}
    delegate_fields = {'delegateRequest': {'tool_input', 'arguments', 'input', 'prompt'},
                       'delegateResponse': {'result', 'tool_result'}}
    if (not isinstance(reference, dict) or set(reference) not in (keys, keys | {'delegateField'})
            or reference['path'] != str(path) or not isinstance(reference['key'], str)
            or reference['key'] not in FIELD_KEYS
            or 'delegateField' in reference and (not isinstance(reference['delegateField'], str)
                or reference['key'] not in delegate_fields.get(reference['delegateField'], set()))
            or type(reference['offset']) is not int or reference['offset'] < 0
            or type(reference['bytes']) is not int or not 0 < reference['bytes'] <= MAX_BYTES
            or not isinstance(reference['sha256'], str) or len(reference['sha256']) != 64):
        raise ValueError('Invalid owned event field reference')
    guard = check_budget or (lambda: None)
    guard()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb', buffering=0) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Canonical events must be a regular file')
        stamp = _stamp(info)
        if reference['offset'] + reference['bytes'] > stamp[2]:
            raise StaleWindow('The event field source changed')
        stream.seek(reference['offset'])
        raw = stream.read(reference['bytes'])
        if hashlib.sha256(raw).hexdigest() != reference['sha256'] or not raw.endswith(b'\n'):
            raise StaleWindow('The event field source changed')
        event = _bounded_json(raw, guard)
        if not isinstance(event, dict) or not isinstance(event.get('data'), dict):
            raise ValueError('Invalid canonical event field')
        value = event['data'][reference['key']]
        if 'delegateField' in reference:
            from .delegate_exchange import exchange_fields, select, tool_name
            data = event['data']
            inputs = next((data[key] for key in ('tool_input', 'arguments', 'input', 'prompt') if key in data), None)
            name = tool_name({'tool': data.get('tool_name') or data.get('tool')})
            if reference['delegateField'] not in exchange_fields(name, inputs):
                raise ValueError('Invalid delegation field for recorded tool')
            if reference['delegateField'] == 'delegateResponse' and (
                    event.get('event') == 'tool:error' or data.get('error') or data.get('error_message')):
                raise ValueError('A failed tool observation is not a delegate reply')
            value = select(value, reference['delegateField'])
            if value is None:
                raise StaleWindow('The delegation field is no longer available')
        chunks = (value,) if isinstance(value, str) else json.JSONEncoder(
            ensure_ascii=False, indent=2, allow_nan=False).iterencode(value)
        parts, total, digest = [], 0, hashlib.sha256()
        for chunk in chunks:
            guard()
            digest.update(chunk.encode())
            start, end = max(0, offset - total), min(len(chunk), offset + limit - total)
            if start < end:
                parts.append(chunk[start:end])
            total += len(chunk)
        current = _stamp(os.fstat(stream.fileno()))
        named = path.lstat()
        named_stamp = _stamp(named)
        if not stat.S_ISREG(named.st_mode) or named_stamp[:2] != stamp[:2] or current[:2] != stamp[:2]:
            raise StaleWindow('The event field source changed during its read')
        if named_stamp != current:
            # Descriptor and pathname stats are separate syscalls. Reconcile an
            # append in that gap before the existing exact-record revalidation;
            # matching inode/size alone never accepts the formatted snapshot.
            if named_stamp[2] <= current[2]:
                raise StaleWindow('The event field source changed during its read')
            current = named_stamp
        if current != stamp:
            # Other calls may append while a large accepted field is formatted.
            # A growing same-inode log is safe only after rechecking this exact
            # bounded record from the file, not from a buffered read snapshot.
            if current[2] <= stamp[2]:
                raise StaleWindow('The event field source changed during its read')
            guard()
            stream.seek(reference['offset'])
            if hashlib.sha256(stream.read(reference['bytes'])).hexdigest() != reference['sha256']:
                raise StaleWindow('The event field source changed during its read')
            if _stamp(os.fstat(stream.fileno())) != current or _stamp(path.lstat()) != current:
                raise StaleWindow('The event field source changed during its read')
    text = ''.join(parts)
    return {'text': text, 'offset': offset, 'total': total,
            'nextOffset': offset + len(text) if offset + len(text) < total else None,
            'digest': digest.hexdigest()}
