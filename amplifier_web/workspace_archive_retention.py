"""Expire explicitly temporary, retired local test workspace copies.

The existing Storage pass owns scheduling and effect receipts. Original source,
results and removal receipts are never targets. Legacy/manual/uncertain work
has no temporary disposition and cannot become eligible merely through age.
"""
from contextlib import closing
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import stat
import time

from filelock import FileLock, Timeout

from . import task_resources as resources
from . import saved_test_storage

KIND = 'test-workspace'
PAGE = 8
MAX_RECORD = 256 * 1024
MAX_BYTES = 8 * 1024**3


def capture(settings, keep=False):
    days = settings.get('workspaceArchiveKeepDays')
    if keep or type(days) is not int or not 1 <= days <= 3650:
        return None
    return {'version': 1, 'disposition': 'temporary', 'keepDays': days,
            'policyRevision': settings['revision']}


def stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def checked_file(path, checksum, size, *, verify=False):
    if (type(size) is not int or not 0 <= size <= MAX_BYTES
            or not isinstance(checksum, str) or len(checksum) != 64):
        raise ValueError('Incomplete archive identity')
    parent = resources._open_root(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                raise ValueError('Saved file changed')
            if verify:
                digest, total, deadline = hashlib.sha256(), 0, time.monotonic() + 60
                while block := stream.read(1024 * 1024):
                    total += len(block)
                    if total > size or time.monotonic() > deadline:
                        raise ValueError('Saved file verification exceeded its bound')
                    digest.update(block)
                if digest.hexdigest() != checksum or stamp(os.fstat(stream.fileno())) != stamp(before):
                    raise ValueError('Saved file changed')
            return stamp(before)
    finally:
        os.close(parent)


def candidate(home, row, settings, now, *, verify=False):
    if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not re.fullmatch('[A-Za-z0-9-]{1,100}', row['id']):
        raise ValueError('Invalid resource identity')
    disposition = row.get('workspaceArchivePolicy')
    if not isinstance(disposition, dict):
        raise ValueError('No temporary workspace disposition')
    days, retired = disposition.get('keepDays'), row.get('workspaceArchiveRetiredAt')
    if (disposition.get('version') != 1 or disposition.get('disposition') != 'temporary'
            or type(days) is not int or not 1 <= days <= 3650
            or type(settings.get('workspaceArchiveKeepDays')) is not int
            or row.get('workspaceArchiveExpiry') or row.get('testMachine') or row.get('storageRoot')
            or row.get('pendingRequest') or row.get('preparationPending')
            or type(retired) not in (int, float) or not math.isfinite(retired) or retired < 0):
        raise ValueError('The saved workspace remains protected')
    # Changing the global duration may extend an earlier promise, never shorten
    # it. Disabling the policy pauses expiration even for previously opted-in work.
    expires = retired + max(days, settings['workspaceArchiveKeepDays']) * 86400
    request, snapshot = row.get('lastOperation'), row.get('snapshot')
    if not isinstance(request, dict) or not isinstance(snapshot, dict):
        raise ValueError('Incomplete saved workspace identity')
    result = request.get('result')
    if (not isinstance(result, dict) or not isinstance(result.get('archives'), list)
            or len(result['archives']) != 2 or any(not isinstance(a, dict) for a in result['archives'])):
        raise ValueError('Incomplete local retention receipt')
    compact = {**row, 'sourceHash': (row.get('snapshot') or {}).get('archive_sha256'),
               'lastOperation': {**request, 'result': {**result,
                   'archivesCount': len(result.get('archives', [])),
                   'downloadedArchivesPresent': 'downloadedArchives' in result}}}
    accounting = saved_test_storage.project(0, row['id'], row['sessionId'], row['status'], json.dumps(compact))
    if (accounting['totalBytes'] is None or accounting.get('combinedArchive')
            or result.get('downloadedArchives') is not None or row.get('environmentState') != 'removed'
            or result.get('retention') != row.get('retainedFiles')
            or not isinstance(request.get('requestId'), str) or not request['requestId']):
        raise ValueError('No exact completed local file retention')
    root = resources.directory(Path(home), row)
    folder = root / ('retained-' + hashlib.sha256(request['requestId'].encode()).hexdigest())
    files = [(root / 'source.tar', result['retention']['sourceSha256'], accounting['sourceBytes'])]
    workspace = None
    for archive in result['archives']:
        role = archive['source']
        path = folder / ('workspace.tar' if role == '/workspace' else 'artifacts.tar')
        if archive.get('destination') != str(path):
            raise ValueError('The archive destination changed')
        files.append((path, archive['sha256'], archive['bytes']))
        if role == '/workspace':
            workspace = archive
    stamps = [checked_file(path, checksum, size, verify=verify) for path, checksum, size in files]
    return {'kind': KIND, 'id': row['id'], 'sessionId': row['sessionId'],
            'label': 'Temporary test workspace', 'fileBytes': workspace['bytes'],
            'sha256': workspace['sha256'], 'requestId': request['requestId'],
            'resourceRevision': row['revision'], 'expiresAt': expires,
            'selected': now >= expires,
            'reason': 'Temporary workspace retention ended; source and results stay' if now >= expires else 'Kept until its temporary workspace retention ends',
            'paths': [str(folder / 'workspace.tar')], 'stamp': stamps}


def inspect(home, settings, after=0):
    """One small allocation page; no archive contents, filesystem walk or effects."""
    empty = {'candidates': [], 'complete': True, 'workspaceArchiveCursor': 0}
    if settings.get('workspaceArchiveKeepDays') is None:
        return empty
    path = Path(home) / 'workspace-resources' / 'reservations.sqlite3'
    if not path.exists():
        return empty
    fd = resources._open_root(path.parent)
    os.close(fd)
    if path.is_symlink():
        raise ValueError('Resource ledger changed')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        deadline = time.monotonic() + 1
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        rows = db.execute("SELECT rowid,id,root,CASE WHEN length(body)<=? THEN body END FROM allocations "
            "WHERE rowid>? AND status='retired' AND json_valid(body) "
            "AND json_extract(body,'$.kind')='test-environment' "
            "AND json_extract(body,'$.workspaceArchivePolicy.disposition')='temporary' "
            "AND json_type(body,'$.workspaceArchiveExpiry') IS NULL ORDER BY rowid LIMIT ?",
            (MAX_RECORD, after, PAGE + 1)).fetchall()
    candidates = []
    for position, identity, sid, encoded in rows[:PAGE]:
        try:
            row = json.loads(encoded)
            if row['id'] != identity or row['sessionId'] != sid:
                raise ValueError('Changed allocation identity')
            candidates.append(candidate(home, row, settings, time.time()))
        except (OSError, ValueError, TypeError, KeyError):
            pass  # No disposition/complete custody proof means no candidate.
    return {'candidates': candidates, 'complete': len(rows) <= PAGE,
            'workspaceArchiveCursor': rows[PAGE-1][0] if len(rows) > PAGE else 0}


def retire(home, settings, selected, evidence, guard):
    """Reuse the environment lock and Storage receipt at the unlink boundary."""
    for identity, expected in list(selected.items())[:2]:
        try:
            lock = FileLock(str(Path(home) / 'workspace-resources' / ('environment-' + identity + '.lock')), timeout=0)
            with lock, closing(resources.connect(Path(home))) as db:
                row = resources.get(db, expected['sessionId'], identity)
                actual = candidate(home, row, settings, time.time())
                if not actual['selected'] or any(actual[key] != expected[key] for key in ('id', 'sessionId', 'requestId', 'resourceRevision', 'sha256', 'fileBytes', 'expiresAt')):
                    continue
                guard(row)  # Skip unresolved work before reading any archive contents.
                verified = candidate(home, row, settings, time.time(), verify=True)
                if verified != actual:
                    continue
                item = {key: actual[key] for key in ('kind', 'id', 'fileBytes', 'sha256', 'requestId')}
                evidence('attempting', item)  # Existing policy/Stop gate + durable intent.
                # The final policy callback can yield. Keep all three exact files
                # unchanged and the allocation at the same revision afterward.
                current = resources.get(db, expected['sessionId'], identity)
                final = candidate(home, current, settings, time.time())
                if final != actual:
                    raise ValueError('Retained workspace changed before expiry')
                path = Path(actual['paths'][0])
                directory = resources._open_root(path.parent)
                try:
                    os.unlink(path.name, dir_fd=directory)
                    os.fsync(directory)
                finally:
                    os.close(directory)
                row['workspaceArchiveExpiry'] = {**item, 'status': 'expired', 'expiredAt': time.time()}
                row['revision'] += 1
                with db:
                    resources.save(db, row)
                evidence('completed', item)
        except (OSError, ValueError, TypeError, KeyError, Timeout, sqlite3.Error):
            # Before intent: kept. After intent: existing Storage receipt remains
            # uncertain and blocks replay, including a crash after unlink.
            continue
