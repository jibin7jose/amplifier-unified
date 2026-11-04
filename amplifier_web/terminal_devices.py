"""Private one-use enrollment grants and revocable native-client credentials."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from contextlib import contextmanager

from filelock import FileLock

from .auth import auth_dir
from .deployment import write_private

GRANT_TTL = 30 * 60
MAX_DEVICES = 100


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class TerminalDevices:
    def __init__(self, data_dir):
        self.path = auth_dir(data_dir) / 'terminal-devices.json'
        self.streams = {}

    def read(self):
        if not self.path.exists():
            return {'grants': {}, 'devices': {}}
        return json.loads(self.path.read_text())

    @contextmanager
    def transaction(self):
        with FileLock(str(self.path) + '.lock'):
            state = self.read()
            yield state
            write_private(self.path, json.dumps(state))

    def grant(self, name, identity):
        secret = secrets.token_urlsafe(32)
        expires = int(time.time()) + GRANT_TTL
        with self.transaction() as state:
            state['grants'] = {k: v for k, v in state['grants'].items() if v['expiresAt'] > time.time()}
            if len(state['grants']) >= 20:
                raise ValueError('Too many pending installers. Wait for one to expire before preparing another.')
            if len(state['devices']) >= MAX_DEVICES:
                raise ValueError('Remove an unused terminal connection before adding another.')
            state['grants'][identity] = {'digest': digest(secret), 'name': name, 'expiresAt': expires}
        return identity + '.' + secret, expires

    def redeem(self, grant):
        if not isinstance(grant, str) or not re.fullmatch(r'[a-f0-9]{32}\.[A-Za-z0-9_-]{43}', grant):
            raise ValueError('Setup expired or was already used. Download a new setup file.')
        identity, secret = grant.split('.')
        # Enrollment retries can reuse a preparation ID after its receipt expires.
        # A newly installed device must never replace an older device's credential.
        device_id = secrets.token_hex(16)
        token = 'amt_' + device_id + '.' + secrets.token_urlsafe(48)
        with self.transaction() as state:
            row = state['grants'].get(identity)
            if not row or row['expiresAt'] <= time.time() or not hmac.compare_digest(row['digest'], digest(secret)):
                raise ValueError('Setup expired or was already used. Download a new setup file.')
            if len(state['devices']) >= MAX_DEVICES:
                raise ValueError('Remove an unused terminal connection before adding another.')
            state['devices'][device_id] = {'id': device_id, 'name': row['name'], 'digest': digest(token), 'createdAt': int(time.time()), 'setupId': identity, 'setupExpiresAt': row['expiresAt']}
            del state['grants'][identity]
        return {'id': device_id, 'token': token}

    def identify(self, token):
        if not re.fullmatch(r'(?:amt|amc|amd)_[a-f0-9]{32}\.[A-Za-z0-9_-]{64}', token):
            return None
        identity = token[4:].split('.')[0]
        row = self.read()['devices'].get(identity)
        # Distinct token families keep restricted credentials fail-closed on older hosts.
        expected_scope = {'amc_': 'companion-v1', 'amd_': 'device-v1'}.get(token[:4])
        if not row or row.get('scope') != expected_scope:
            return None
        current = hmac.compare_digest(row['digest'], digest(token))
        previous = (expected_scope == 'device-v1' and row.get('previousUntil', 0) > time.time()
                    and hmac.compare_digest(row.get('previousDigest', ''), digest(token)))
        return identity if current or previous else None

    def listing(self):
        return [{k: row[k] for k in ('id', 'name', 'createdAt', 'setupId', 'setupExpiresAt', 'scope', 'appId', 'grants') if k in row}
                for row in self.read()['devices'].values()]

    def revoke(self, identity):
        with self.transaction() as state:
            state['devices'].pop(identity, None)
            state['grants'].pop(identity, None)
        # Cancel the HTTP/SSE observers. Accepted commands have their own shielded
        # service task; removing a client credential must not stop session work.
        for task in tuple(self.streams.get(identity, ())):
            if task is not asyncio.current_task():
                task.cancel()

    @contextmanager
    def connection(self, identity):
        task = asyncio.current_task()
        self.streams.setdefault(identity, set()).add(task)
        try:
            yield
        finally:
            self.streams[identity].discard(task)
            if not self.streams[identity]:
                del self.streams[identity]
