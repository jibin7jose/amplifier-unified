"""Bounded local voice protocol facts, never conversation or media payloads.

Separate from routed diagnostics: errors do not enroll a conversation in capture
or send records to configured destinations.
"""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import re
import stat
import time

MAX_BYTES = 256_000  # Current file and one previous file, each bounded.
TRACE_TYPES = {
    'session.thinking.append', 'session.commentary.append', 'session.close',
    'session.thinking.appended', 'session.commentary.appended',
    'conversation.item.create', 'response.create',
}


def identifier(value, secret):
    if not isinstance(value, str):
        return None
    if (secret and secret in value) or re.search(r'(?i)sk-|bearer\s', value):
        return '[REDACTED]'
    if not re.fullmatch(r'[A-Za-z0-9_.:\[\]-]{1,160}', value):
        return '[OMITTED]'
    return value


class VoiceTrace:
    def __init__(self):
        self.events = deque(maxlen=32)
        self.sequence = 0

    def note(self, direction, event, secret):
        if event.get('type') not in TRACE_TYPES:
            return
        self.sequence += 1
        self.events.append({'at': time.time(), 'sequence': self.sequence, 'direction': direction,
                            **{key: identifier(event[key], secret) for key in
                               ('type', 'event_id', 'client_event_id') if key in event}})


class VoiceDiagnostics:
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / 'voice-diagnostics' if data_dir is not None else None
        self.storage_error = False

    def record(self, call, event):
        if self.directory is None:
            return
        secret = call.manager.api_key
        fields = {key: identifier(event[key], secret) for key in
                  ('type', 'event_id', 'client_event_id', 'reason') if key in event}
        row = {'at': time.time(), 'provider': call.provider,
               'callId': identifier(call.id, secret), 'sessionId': identifier(call.session_id, secret),
               'closing': call.closing, 'closed': call.closed, 'finalized': call.final.is_set(),
               **fields, 'trace': list(call.protocol_trace.events)}
        error = event.get('error')
        if isinstance(error, dict):
            row['error'] = {key: identifier(error[key], secret) for key in
                            ('code', 'type', 'param', 'client_event_id') if key in error}
            # Even redacted free text can echo user context. Do not retain it.
            row['error']['messageOmitted'] = 'message' in error
        line = (json.dumps(row, separators=(',', ':')) + '\n').encode('utf-8')
        try:
            if len(line) > MAX_BYTES:
                raise ValueError('Voice diagnostic exceeds retention bound')
            if os.name == 'nt':
                self._record_windows(line)
                return
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.directory.is_symlink():
                raise ValueError('Voice diagnostic directory is a symlink')
            self.directory.chmod(0o700)
            path = self.directory / 'events.jsonl'
            if path.is_symlink():
                raise ValueError('Voice diagnostic log is a symlink')
            if path.exists() and not path.is_file():
                raise ValueError('Voice diagnostic log is not a regular file')
            if path.exists() and path.stat().st_size + len(line) > MAX_BYTES:
                path.chmod(0o600)
                os.replace(path, path.with_name('events.previous.jsonl'))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('Voice diagnostic log is not a regular file')
                os.fchmod(stream.fileno(), 0o600)
                stream.write(line)
        except (OSError, ValueError):
            # Never mask the provider error or prevent ordinary call cleanup.
            self.storage_error = True

    def _record_windows(self, line):
        # chmod/POSIX open flags do not establish Windows privacy. Reuse the
        # owner/SID/ACL/reparse-point boundary used for other private app files.
        # These rare error/close records have a fixed bound, not a growing read.
        from .windows_files import directory
        with directory(self.directory, owned_root=self.directory.parent, create=True) as pinned:
            try:
                current = pinned.read_private('events.jsonl', limit=MAX_BYTES)
            except FileNotFoundError:
                current = b''
            if len(current) + len(line) > MAX_BYTES:
                pinned.publish_private('events.previous.jsonl', current, replace=True)
                current = b''
            pinned.publish_private('events.jsonl', current + line, replace=True)
