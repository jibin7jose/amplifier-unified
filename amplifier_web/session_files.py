"""CLI-compatible paths and Context Intelligence's public JSONL disk contract.

No dependency on a host application. Foundation owns execution locking and the native history reader/writer; these
remain the existing shared session files.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re

from filelock import FileLock

_WINDOWS = os.name == 'nt'


def source_change_time(info):
    """Compare the same metadata across Windows descriptor and named stats.

    CPython 3.13 Windows fstat can expose change time as st_ctime_ns while
    stat exposes creation time there. Birth time is explicit and consistent.
    POSIX retains its change-time fence; callers still check inode/size/mtime
    and exact accepted record bytes independently.
    """
    return info.st_birthtime_ns if _WINDOWS else info.st_ctime_ns


def amplifier_home():
    return Path(os.environ.get('AMPLIFIER_HOME') or Path.home() / '.amplifier').expanduser().resolve()


def project_slug(workspace):
    # Exact app-cli convention: spaces, periods and underscores are significant.
    slug = str(Path(workspace).expanduser().resolve()).replace('/', '-').replace('\\', '-').replace(':', '')
    return slug if slug.startswith('-') else '-' + slug


def validate_id(identity):
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,220}', identity):
        raise ValueError('Invalid session identifier')
    return identity


def sessions_dir(workspace):
    return amplifier_home() / 'projects' / project_slug(workspace) / 'sessions'


def capture_sessions_dir(workspace):
    # Match CI's supported relocation setting; transcript paths do not relocate.
    root = Path(os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH') or amplifier_home() / 'projects').expanduser().resolve()
    return root / project_slug(workspace) / 'sessions'


def capture_dir(workspace, identity):
    return capture_sessions_dir(workspace) / validate_id(identity) / 'context-intelligence'


def validate_capture(directory):
    meta = json.loads((directory / 'metadata.json').read_text())
    if meta.get('format') != 'context-intelligence' or meta.get('version') != '1.0.0':
        raise ValueError('Unsupported Context Intelligence capture format/version; original files were preserved.')
    return meta


def append_event(workspace, identity, event, data):
    """Append a CI record once; return a small, verifiable index reference.

    One O_APPEND write keeps this compatible with the community logging hook.
    Never rewrite an events file, replay an operation, or infer remote consent.
    """
    from .host.storage import SessionStore
    directory = capture_dir(workspace, identity)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / 'events.jsonl'
    record = {'event': event, 'workspace': project_slug(workspace),
              'timestamp': data['timestamp'], 'data': data}
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    with FileLock(str(directory / '.unified-append.lock')):
        metadata = directory / 'metadata.json'
        if metadata.exists():
            validate_capture(directory)
        else:
            SessionStore._atomic(metadata, json.dumps({
                'format': 'context-intelligence', 'version': '1.0.0',
                'session_id': identity, 'workspace': project_slug(workspace),
                'working_dir': str(workspace), 'parent_id': data.get('parent_id', ''),
                'started_at': data['timestamp'], 'last_event_at': data['timestamp'], 'status': 'running',
            }))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(fd, raw) != len(raw):
                raise OSError('Incomplete event append')
            offset = os.lseek(fd, 0, os.SEEK_CUR) - len(raw)
        finally:
            os.close(fd)
        # Existing metadata belongs to the shared hook. Do not race its lifecycle
        # updates or replace its optional fields with an app-only projection.
    return {'$event': str(path), 'offset': offset, 'bytes': len(raw), 'eventId': data.get('event_id'), 'sha256': hashlib.sha256(raw).hexdigest()}


def read_event(reference):
    path = Path(reference['$event'])
    validate_capture(path.parent)
    with path.open('rb') as stream:
        stream.seek(reference['offset'])
        raw = stream.read(reference['bytes'])
    if reference.get('sha256') and reference['sha256'] != hashlib.sha256(raw).hexdigest():
        raise ValueError('The event index is stale; the original capture was changed.')
    row = json.loads(raw)
    if reference.get('eventId') != row.get('data', {}).get('event_id'):
        raise ValueError('The event index is stale; the original capture was changed.')
    return row


def event_index(directory):
    """Migration-only scan: make a resumed JSONL export idempotent after a crash."""
    path = directory / 'events.jsonl'
    if not path.exists():
        return {}
    validate_capture(directory)
    result = {}
    with path.open('rb') as stream:
        while True:
            offset = stream.tell()
            line = stream.readline()
            if not line:
                break
            row = json.loads(line)
            identity = row.get('data', {}).get('event_id')
            if identity:
                result[identity] = {'$event': str(path), 'offset': offset, 'bytes': len(line), 'eventId': identity}
    return result
