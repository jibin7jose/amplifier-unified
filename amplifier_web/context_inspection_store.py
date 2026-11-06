"""Small native-adapter diagnostic ring. It is never a context/recovery source.

Index/manifest reads never open content. Selected text reads touch one bounded
span. Session deletion already owns the containing native capture directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import time

from filelock import FileLock

MAX_REQUESTS = 4
MAX_MANIFEST_BYTES = 512_000
MAX_BODY_BYTES = 4_100_000
PAGE_CHARS = 6000


def preferences(home):
    try:
        path = Path(home) / 'diagnostics' / 'config.json'
        if path.stat().st_size > 100_000:
            return {'enabled': False, 'bodies': False, 'days': 0}
        value = json.loads(path.read_text())
        return {'enabled': value.get('enabled', True) is True,
                'bodies': value.get('providerRequests') is True,
                'days': max(0, min(30, int(value.get('retentionDays', 30))))}
    except FileNotFoundError:
        return {'enabled': True, 'bodies': False, 'days': 30}
    except (OSError, ValueError, TypeError):
        return {'enabled': False, 'bodies': False, 'days': 0}


def key(call_id):
    if not isinstance(call_id, str) or not call_id.startswith('llm:') or len(call_id) > 100:
        raise ValueError('Invalid observed request identity.')
    return hashlib.sha256(call_id.encode()).hexdigest()


class InspectionStore:
    def __init__(self, directory):
        self.directory = Path(directory) / 'context-inspection'

    def _path(self, name):
        # Reject existing links in the capture chain, not only the leaf.
        if any(p.is_symlink() for p in (self.directory, *self.directory.parents)):
            raise ValueError('Request observation is unavailable on this host.')
        path = self.directory / name
        if path.is_symlink():
            raise ValueError('Request observation is unavailable on this host.')
        return path

    def _read(self, name, maximum):
        path = self._path(name)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Invalid request observation.')
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError('Request observation exceeded its bound.')
        return json.loads(raw)

    def _write(self, name, value):
        from .host.config import write_private
        self._path(name)
        write_private(self.directory / name, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))

    def index(self, *, days=30):
        try:
            values = self._read('index.json', 16000)
        except FileNotFoundError:
            return []
        if not isinstance(values, list) or len(values) > MAX_REQUESTS:
            raise ValueError('Invalid request index.')
        cutoff = time.time() - days * 86400 if days is not None else 0
        return [v for v in values if v.get('startedAt', 0) >= cutoff]

    def _settle_transaction(self):
        """Recover only the one declared write; never scan native capture."""
        try:
            transaction = self._read('transaction.json', 2000)
        except FileNotFoundError:
            return
        pending = transaction['new']
        retired = transaction['retired']
        if not isinstance(retired, list) or len(retired) > MAX_REQUESTS + 1:
            raise ValueError('Invalid observation cleanup transaction.')
        for value in [pending, *retired]:
            key(value)  # Validate before any deletion.
        committed = any(row['callId'] == pending for row in self.index(days=None))
        for call_id in retired if committed else [pending]:
            for suffix in ('.json', '.txt'):
                self._path(key(call_id) + suffix).unlink(missing_ok=True)
        self._path('transaction.json').unlink()

    def begin(self, header, manifest, texts, *, days=30):
        self._path('index.json')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = key(header['callId'])
        with FileLock(str(self._path('.lock')), timeout=2):
            self._settle_transaction()
            rows = self.index(days=None)
            if any(r['callId'] == header['callId'] for r in rows):
                raise ValueError('A captured request is immutable.')
            # One immutable text body per request; manifest carries bounded byte
            # spans. Labels/paths never become file names.
            offset, parts = 0, []
            for item in manifest.get('items', []):
                text = texts.get(item['id'])
                if text is not None:
                    size = len(text.encode())
                    item['_span'] = [offset, size]
                    offset += size
                    parts.append(text)
            if offset > MAX_BODY_BYTES:
                raise ValueError('Observation text exceeded its bound.')
            rows = rows + [header]
            rows.sort(key=lambda r: r['startedAt'], reverse=True)
            eligible = [r for r in rows if r['startedAt'] >= time.time() - days * 86400]
            # Keep the last completed request even during many new failures.
            completed = next((r for r in eligible if r['phase'] == 'completed'), None)
            keep = eligible[:MAX_REQUESTS]
            if completed and completed not in keep:
                keep[-1] = completed
            if header not in keep:
                return
            # Journal before the first body. Failed/unknown atomic writes are
            # reconciled by exact index membership, never by blindly replaying.
            # If cleanup itself fails, the next capture must settle it first:
            # at most the ring plus this one declared candidate can exist.
            self._write('transaction.json', {'new': header['callId'],
                'retired': [r['callId'] for r in rows if r not in keep]})
            try:
                if parts:
                    self._write(identity + '.txt', ''.join(parts))
                self._write(identity + '.json', {**manifest, **header})
                self._write('index.json', keep)
            finally:
                self._settle_transaction()

    def finish(self, call_id, phase, usage):
        with FileLock(str(self._path('.lock')), timeout=2):
            self._settle_transaction()
            rows = self.index(days=None)
            row = next((r for r in rows if r['callId'] == call_id), None)
            if row is None:
                return
            row.update(phase=phase, usage=usage, endedAt=time.time())
            self._write('index.json', rows)

    def read(self, *, call_id=None, item_id=None, offset=0, bodies=False, days=30):
        rows = self.index(days=days)
        selected = (next((r for r in rows if r['callId'] == call_id), None) if call_id else
                    next((r for r in rows if r['phase'] == 'completed'), None))
        if call_id and selected is None:
            raise ValueError('This request observation expired or is unavailable. Refresh the inspector.')
        result = {'version': 1, 'boundary': 'host-final ChatRequest; not provider wire or hidden state',
                  'requests': rows, 'selected': selected, 'latest': rows[0] if rows else None,
                  'retention': f'Up to {MAX_REQUESTS} observations readable for {days} days. Expired files are removed on the next capture or by session deletion; there is no quiet-session expiry timer.',
                  'status': 'available' if selected else 'no_completed_request'}
        if selected is None:
            return result
        identity = key(selected['callId'])
        manifest = self._read(identity + '.json', MAX_MANIFEST_BYTES)
        if manifest.get('callId') != selected['callId']:
            raise ValueError('Request observation identity changed.')
        items = manifest.get('items', [])
        if item_id is not None:
            if type(offset) is not int or not 0 <= offset <= 120_000:
                raise ValueError('Invalid captured-text offset.')
            item = next((r for r in items if r['id'] == item_id), None)
            if item is None:
                raise ValueError('This item is not part of the selected request.')
            if not bodies or not item.get('bodyAvailable'):
                return {**result, 'itemId': item_id, 'text': None,
                        'reason': item.get('bodyUnavailable') or 'Recording consent is off; captured content is unavailable.'}
            start, size = item['_span']
            if not (type(start) is int and type(size) is int and 0 <= start <= MAX_BODY_BYTES and 0 <= size <= 480_000):
                raise ValueError('Invalid captured text span.')
            path = self._path(identity + '.txt')
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('Captured content is unavailable.')
                stream.seek(start)
                raw = stream.read(size)
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Captured content changed; no current source was substituted.')
            text = raw.decode('utf-8')
            end = min(offset + PAGE_CHARS, len(text))
            return {**result, 'itemId': item_id, 'text': text[offset:end], 'offset': offset, 'endOffset': end,
                    'nextOffset': end if end < len(text) else None, 'characters': len(text), 'sha256': item['sha256']}
        visible = []
        for item in items:
            row = {k: v for k, v in item.items() if not k.startswith('_')}
            if not bodies:
                row.update(label=f'{row["category"].title()} item {int(row["id"]) + 1}', bodyAvailable=False,
                           bodyUnavailable='Request text is not available under current recording consent.')
                for name in ('sourceLabel', 'sourceKind', 'sha256', 'note'):
                    row.pop(name, None)
            visible.append(row)
        return {**result, 'manifest': {k: v for k, v in manifest.items() if k not in {'items', 'usage', 'phase'}},
                'items': visible}
