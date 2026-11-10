"""Disposable, bounded transcript display windows; never a canonical merge index.

Only the scanned window is validated. Ordinals, totals and global input identity
uniqueness are deliberately unknown. Callers retain canonical resume authority.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

READ_LIMIT = 8 * 1024 * 1024
RECORD_LIMIT = 4 * 1024 * 1024
RAW_LIMIT = 512
_OBJECT_LIMIT = object()
BLOCK = 64 * 1024


class _Changed(Exception):
    pass


class _Invalid(Exception):
    pass


def _stamp(path):
    try:
        info = path.stat()
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
    except FileNotFoundError:
        return None


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False, sort_keys=True).encode()


def _reject_constant(_value):
    raise ValueError('Invalid JSON constant')


def _bounded_json(raw):
    # Reject pathological container expansion before allocating a decoded graph.
    # Strings are bounded by RECORD_LIMIT; this caps aggregate elements/depth.
    quoted = escaped = False
    depth = punctuation = 0
    for byte in raw:
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
            punctuation += 1
            if depth > 64:
                return _OBJECT_LIMIT
        elif byte in (93, 125):
            depth -= 1
        elif byte in (44, 58):
            punctuation += 1
        if punctuation > 100000:
            return _OBJECT_LIMIT
    return json.loads(raw, parse_constant=_reject_constant)


class TranscriptWindowReader:
    """One host-owned cursor signer. No file handles, rows or history are retained."""

    def __init__(self):
        self._key = os.urandom(32)

    @staticmethod
    def source_revision(directory, owner):
        root = Path(directory).resolve()
        paths = [root / name for name in
                 ('transcript.jsonl', 'transcript.jsonl.backup', 'transcript.jsonl.append-pending')]
        if any(path.resolve().parent != root for path in paths):
            raise ValueError('Transcript source escaped its canonical owner.')
        return hashlib.sha256(_encode([owner, [_stamp(path) for path in paths]])).hexdigest()

    def _token(self, payload):
        raw = _encode(payload)
        return base64.urlsafe_b64encode(hmac.digest(self._key, raw, 'sha256') + raw).decode().rstrip('=')

    def _parse(self, token):
        if not isinstance(token, str) or not 0 < len(token) <= 4096:
            raise _Invalid()
        try:
            raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
            signature, body = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(self._key, body, 'sha256')):
                raise _Invalid()
            value = json.loads(body)
            if not isinstance(value, dict):
                raise _Invalid()
            return value
        except (ValueError, TypeError):
            raise _Invalid() from None

    async def stream_forward(self, directory, owner, *, limit=100):
        """Pull raw transcript batches in order, without projecting visibility.

        Awaiting the consumer supplies backpressure; there is no prefetch. A
        cancelled reader joins its one bounded physical read before returning.
        Only successful exhaustion establishes an unchanged complete source;
        closing early or failing after a prefix does not establish completeness.
        """
        after = None
        while True:
            task = asyncio.create_task(asyncio.to_thread(
                self.read_forward, directory, owner, after=after, limit=limit))
            try:
                page = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Cancelling the waiter cannot cancel filesystem I/O. Retain
                # ownership until the finite read has closed its descriptor.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except (asyncio.CancelledError, Exception):
                        pass
                if not task.cancelled():
                    task.exception()
                raise
            if page['status'] != 'ready':
                raise ValueError('Native transcript stream unavailable: ' + page['reason'])
            after, revision = page['after'], page['revision']
            yield page
            if self.source_revision(directory, owner) != revision:
                raise ValueError('Native transcript changed during streaming.')
            if after is None:
                return

    def read_forward(self, directory, owner, *, after=None, limit=100):
        """One bounded raw batch after an authenticated validated prefix.

        This is not a public-conversation projection. Records retain their
        original roles/content/metadata for the existing visibility owner.
        Unlike a recent display window, a complete traversal cannot skip gaps.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Message batch limit must be between 1 and 100.')
        if (not isinstance(owner, dict) or set(owner) != {'project', 'nativeId'}
                or any(not isinstance(v, str) or not 0 < len(v) <= 512 for v in owner.values())):
            raise ValueError('A canonical transcript owner is required.')
        root = Path(directory).resolve()
        paths = [root / name for name in
                 ('transcript.jsonl', 'transcript.jsonl.backup', 'transcript.jsonl.append-pending')]
        try:
            if any(path.resolve().parent != root for path in paths):
                return {'status': 'unavailable', 'reason': 'source_unreadable'}
            stamps = [_stamp(path) for path in paths]
            revision = hashlib.sha256(_encode([owner, stamps])).hexdigest()
            cursor = self._parse(after) if after is not None else None
            if cursor and cursor.get('kind') != 'forward':
                raise _Invalid()
            if cursor and (cursor.get('owner') != owner or cursor.get('source') != revision):
                return {'status': 'retry', 'reason': 'source_changed'}
            # Preserve the existing committed-source rule. Once any prefix was
            # delivered, never switch to a backup and mix two source histories.
            candidates = [1] if stamps[2] is not None else [0, 1]
            if cursor:
                if cursor.get('file') not in candidates:
                    raise _Invalid()
                candidates = [cursor['file']]
            budget = {'read': 0}
            for selected in candidates:
                if stamps[selected] is None:
                    continue
                start = cursor.get('start') if cursor else 0
                index = cursor.get('index') if cursor else 0
                if (type(start) is not int or not 0 <= start <= stamps[selected][2]
                        or type(index) is not int or index < 0):
                    raise _Invalid()
                try:
                    rows, position, index = self._read_forward(
                        paths[selected], stamps[selected], start, index, limit, budget)
                except (OSError, ValueError, UnicodeError, RecursionError, _Invalid):
                    continue
                if [_stamp(path) for path in paths] != stamps:
                    raise _Changed()
                token = self._token({'kind': 'forward', 'owner': owner, 'source': revision,
                                     'file': selected, 'start': position, 'index': index}) if position < stamps[selected][2] else None
                return {'status': 'ready', 'rows': rows, 'after': token, 'revision': revision,
                        'source': 'backup' if selected else 'primary',
                        'coverage': {'scannedBytes': budget['read'], 'rangeStart': start,
                                     'rangeEnd': position, 'validation': 'prefix'}}
            if [_stamp(path) for path in paths] != stamps:
                raise _Changed()
            return {'status': 'unavailable', 'reason': 'source_unreadable'}
        except _Invalid:
            return {'status': 'retry', 'reason': 'invalid_cursor'}
        except _Changed:
            return {'status': 'retry', 'reason': 'source_changed'}
        except OSError:
            return {'status': 'unavailable', 'reason': 'source_unreadable'}

    def _read_forward(self, path, expected, start, index, limit, budget):
        rows, position = [], start
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise _Invalid()
            if [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns] != expected:
                raise _Changed()
            stream.seek(start)
            while position < expected[2] and len(rows) < limit and budget['read'] < READ_LIMIT:
                raw = stream.readline(min(RECORD_LIMIT + 1, READ_LIMIT - budget['read']))
                budget['read'] += len(raw)
                if not raw:
                    raise _Changed()
                if len(raw) > RECORD_LIMIT:
                    raise _Invalid()
                if not raw.endswith(b'\n') and stream.tell() < expected[2]:
                    break  # Retry the intact record with the next batch's budget.
                if raw.strip():
                    value = _bounded_json(raw)
                    if (value is _OBJECT_LIMIT or not isinstance(value, dict)
                            or not isinstance(value.get('role'), str) or not value['role']):
                        raise _Invalid()
                    rows.append({'value': value, 'byteOffset': position, 'nativeIndex': index})
                    index += 1
                position = stream.tell()
            if position == start and position < expected[2]:
                raise _Invalid()
        return rows, position, index

    def read_window(self, directory, owner, *, before=None, limit=100, visible=None):
        """Read newest visible rows; ``visible`` is a bounded, read-only predicate.

        The trusted caller resolves ``directory`` from canonical owner identity.
        Failed window validation may recover the backup; unread prefix validity
        is never asserted. Cursors fail closed after any source/intent change.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Message window limit must be between 1 and 100.')
        if (not isinstance(owner, dict) or set(owner) != {'project', 'nativeId'}
                or any(not isinstance(v, str) or not 0 < len(v) <= 512 for v in owner.values())):
            raise ValueError('A canonical transcript owner is required.')
        root = Path(directory).resolve()
        names = ('transcript.jsonl', 'transcript.jsonl.backup', 'transcript.jsonl.append-pending')
        paths = [root / name for name in names]
        try:
            if any(path.resolve().parent != root for path in paths):
                return {'status': 'unavailable', 'reason': 'source_unreadable'}
            stamps = [_stamp(path) for path in paths]
            source = hashlib.sha256(_encode([owner, stamps])).hexdigest()
            cursor = self._parse(before) if before is not None else None
            if cursor and (cursor.get('owner') != owner or cursor.get('source') != source):
                return {'status': 'retry', 'reason': 'source_changed'}
            candidates = [1] if stamps[2] is not None else [0, 1]
            if cursor:
                if cursor.get('file') not in candidates:
                    raise _Invalid()
                candidates = [cursor['file']]
            invalid = False
            budget = {'read': 0}
            for selected in candidates:
                if stamps[selected] is None:
                    continue
                if budget['read'] >= READ_LIMIT:
                    return {'status': 'unavailable', 'reason': 'read_limit'}
                end = cursor.get('end') if cursor else stamps[selected][2]
                aligned = cursor.get('aligned') if cursor else True
                if type(end) is not int or not 0 <= end <= stamps[selected][2] or type(aligned) is not bool:
                    raise _Invalid()
                try:
                    result = self._read(paths[selected], owner, stamps[selected], end, aligned, limit, visible, budget)
                except (OSError, ValueError, UnicodeError, RecursionError, _Invalid):
                    invalid = True
                    continue
                if [_stamp(path) for path in paths] != stamps:
                    raise _Changed()
                result['coverage']['scannedBytes'] = budget['read']
                next_end, next_aligned = result.pop('_before')
                next_cursor = self._token({'owner': owner, 'source': source, 'file': selected,
                                           'end': next_end, 'aligned': next_aligned}) if next_end else None
                # Replay this exact bounded page without retaining its body or
                # scanning/indexing intervening history. `before` below instead
                # starts the preceding page. Both use the same owner/source fence.
                restore_cursor = self._token({'owner': owner, 'source': source, 'file': selected,
                                             'end': end, 'aligned': aligned})
                window_key = hashlib.sha256(_encode([source, selected, end, next_end])).hexdigest()
                return {'status': 'ready', **result, 'messageWindow': {
                    'mode': 'cursor', 'before': next_cursor, 'hasEarlier': bool(next_end),
                    'restoreCursor': restore_cursor,
                    'total': None, 'offset': None, 'scope': 'recent', 'revision': source,
                    'windowKey': window_key, 'validation': 'window',
                    'globalInputUniqueness': 'unknown', 'source': 'backup' if selected else 'primary',
                    'recoveredBackup': bool(selected),
                }}
            if [_stamp(path) for path in paths] != stamps:
                raise _Changed()
            if invalid or any(stamp is not None for stamp in stamps):
                return {'status': 'unavailable', 'reason': 'source_unreadable'}
            return {'status': 'ready', 'rows': [], 'unavailableRanges': [],
                    'coverage': {'scannedBytes': 0, 'scannedRecords': 0, 'rangeStart': 0, 'rangeEnd': 0},
                    'messageWindow': {'mode': 'cursor', 'before': None, 'hasEarlier': False,
                        'restoreCursor': None,
                        'total': None, 'offset': None, 'scope': 'recent', 'revision': source,
                        'windowKey': source, 'validation': 'window', 'globalInputUniqueness': 'unknown',
                        'source': 'primary', 'recoveredBackup': False}}
        except _Invalid:
            return {'status': 'retry', 'reason': 'invalid_cursor'}
        except _Changed:
            return {'status': 'retry', 'reason': 'source_changed'}
        except OSError:
            return {'status': 'unavailable', 'reason': 'source_unreadable'}

    def _read(self, path, owner, expected, end, aligned, limit, visible, budget):
        rows, gaps = [], []
        scanned = records = 0
        position = end
        row_end = end
        pending = b''
        oversized = not aligned
        next_end, next_aligned = end, aligned
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise _Invalid()
            if [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns] != expected:
                raise _Changed()
            # A cursor's exclusive end is a line start; skip its preceding LF.
            if position:
                stream.seek(position - 1)
                if stream.read(1) == b'\n':
                    position -= 1
                    row_end = position
                scanned += 1
                budget['read'] += 1
            stop = False
            while position > 0 and not stop and budget['read'] < READ_LIMIT:
                size = min(BLOCK, position, READ_LIMIT - budget['read'])
                position -= size
                stream.seek(position)
                chunk = stream.read(size)
                if len(chunk) != size:
                    raise _Changed()
                scanned += size
                budget['read'] += size
                right = len(chunk)
                while right >= 0:
                    boundary = chunk.rfind(b'\n', 0, right)
                    left = boundary + 1
                    segment = chunk[left:right]
                    if not oversized:
                        if len(segment) + len(pending) > RECORD_LIMIT:
                            oversized = True
                            pending = b''
                        else:
                            pending = segment + pending
                    complete = boundary >= 0 or position == 0
                    if not complete:
                        break
                    start = position + left
                    if oversized:
                        gaps.append({'start': start, 'end': row_end, 'reason': 'record_limit'})
                    elif pending.strip():
                        value = _bounded_json(pending)
                        if value is _OBJECT_LIMIT:
                            gaps.append({'start': start, 'end': row_end, 'reason': 'object_limit'})
                        elif not isinstance(value, dict) or not isinstance(value.get('role'), str) or not value['role']:
                            raise _Invalid()
                        records += 1
                        if value is not _OBJECT_LIMIT and (visible is None or visible(value)):
                            digest = hashlib.sha256(_encode([owner, start]) + hashlib.sha256(pending).digest()).hexdigest()[:32]
                            rows.append({'value': value, 'byteOffset': start, 'displayId': 'native-window:' + digest})
                    pending = b''
                    oversized = False
                    next_end, next_aligned = start, True
                    row_end = position + boundary
                    if len(rows) >= limit or records + len(gaps) >= RAW_LIMIT:
                        stop = True
                        break
                    if boundary < 0:
                        break
                    right = boundary
            if not stop and position > 0 and oversized:
                # Budget ended inside an unvalidated record. Report it and make
                # explicit older paging progress without ever decoding a suffix.
                gaps.append({'start': position, 'end': row_end, 'reason': 'read_limit'})
                next_end, next_aligned = position, False
            elif not stop and position == 0:
                next_end, next_aligned = 0, True
        rows.reverse()
        gaps.reverse()
        return {'rows': rows, 'unavailableRanges': gaps,
                'coverage': {'scannedBytes': scanned, 'scannedRecords': records,
                             'rangeStart': next_end, 'rangeEnd': end},
                '_before': (next_end, next_aligned)}
