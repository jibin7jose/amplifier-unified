"""Bounded receipt accounting for saved tests; no archive reads or cleanup."""
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3
import stat
import time

PAGE = 32
LIMIT = 2**53 - 1
NOTE = ('Sizes were recorded when tests saved their evidence. They are not a current disk '
        'measurement or an estimate of space cleanup would free. Original source, final '
        'workspace and test results serve different purposes. Temporary workspace availability is reported separately; source and results stay protected. '
        'Each test counts one evidence set, not every local and remote copy.')


def scalar(path, kind='text'):
    """Only fixed internal paths enter SQL; preserve types and bound strings."""
    field = "json_extract(body,'$." + path + "')"
    valid = "json_type(body,'$." + path + "')='" + kind + "'"
    if kind == 'true':
        return "json(CASE WHEN " + valid + " THEN 'true' ELSE 'false' END)"
    if kind == 'text':
        valid += ' AND length(' + field + ')<=200'
    return 'CASE WHEN ' + valid + ' THEN ' + field + ' END'


def object_fields(fields):
    return 'json_object(' + ','.join("'" + key + "'," + value for key, value in fields.items()) + ')'


def projection():
    base = 'lastOperation.result.'
    retention = object_fields({key: scalar(base + 'retention.' + key, kind) for key, kind in {
        'verified': 'true', 'sourceBytes': 'integer', 'archiveBytes': 'integer', 'totalBytes': 'integer',
        'policy': 'text', 'sourceSha256': 'text'}.items()})
    def archives(name):
        return 'json_array(' + ','.join(object_fields({key: scalar(base + name + f'[{i}].' + key, kind)
            for key, kind in {'id': 'text', 'source': 'text', 'bytes': 'integer', 'sha256': 'text', 'verified': 'true'}.items()}) for i in range(3)) + ')'
    result = object_fields({'ok': scalar(base + 'ok', 'true'), 'status': scalar(base + 'status'),
        'universeId': scalar(base + 'universeId'), 'retention': retention,
        **{name: archives(name) for name in ('archives', 'downloadedArchives')},
        'downloadedArchivesPresent': "json_type(body,'$." + base + "downloadedArchives') IS NOT NULL",
        **{name + 'Count': "json_array_length(body,'$." + base + name + "')" for name in ('archives', 'downloadedArchives')}})
    request = object_fields({**{key: scalar('lastOperation.' + key) for key in ('resourceId', 'sessionId', 'state', 'action')}, 'result': result})
    return object_fields({**{key: scalar(key) for key in ('id', 'sessionId', 'status', 'profileId', 'universeId')},
        'createdAt': "CASE WHEN json_type(body,'$.createdAt') IN ('integer','real') THEN json_extract(body,'$.createdAt') END",
        'sourceHash': scalar('snapshot.archive_sha256'), 'remote': "json_type(body,'$.testMachine')='object'",
        'lastOperation': request, 'workspaceArchiveExpiry': object_fields({key: scalar('workspaceArchiveExpiry.' + key) for key in ('status', 'requestId', 'sha256')}),
        'removalRequestId': scalar('lastOperation.requestId')})


def number(value):
    return value if type(value) is int and 0 <= value <= LIMIT else None


def archive_selection(value, name, universe, policy):
    """Bind each bounded receipt to its environment, role and recorded bytes."""
    count = value.get(name + 'Count')
    if type(count) is not int or not 1 <= count <= 3:
        raise ValueError('Incomplete archive accounting')
    roles = {}
    for archive in value[name][:count]:
        role, size = archive.get('source'), number(archive.get('bytes'))
        checksum = archive.get('sha256')
        if (archive.get('id') != universe or role not in {'/workspace', '/artifacts', '/'}
                or role in roles or size is None or archive.get('verified') is not True
                or not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}', checksum)):
            raise ValueError('Incomplete archive accounting')
        roles[role] = (size, checksum)
    if (policy in {'files', 'archive-only'} and set(roles) != {'/workspace', '/artifacts'}
            or policy == 'full' and set(roles) != {'/'} or policy not in {'files', 'archive-only', 'full'}):
        raise ValueError('The archive selection is incomplete')
    return roles


def project(position, identity, root, status, encoded):
    """Interpret only the exact resource's completed retention receipt."""
    result = {'id': identity or 'Unavailable reference', 'position': position, 'status': status,
              'label': 'Saved test', 'sourceBytes': None, 'workspaceBytes': None,
              'resultBytes': None, 'otherArchiveBytes': None, 'totalBytes': None}
    try:
        row = json.loads(encoded)
        if (not identity or not root or not status or row['id'] != identity
                or row['sessionId'] != root or row['status'] != status):
            raise ValueError('Changed allocation binding')
        result['label'] = {'python-project': 'Python project check', 'browser-test': 'Browser check',
                           'linux-python': 'Python check'}.get(str(row.get('profileId')), 'Saved test')
        created = row.get('createdAt')
        if type(created) in (int, float) and math.isfinite(created) and 0 <= created <= 253402300799:
            result['createdAt'] = created
        request = row.get('lastOperation') or {}
        value = request.get('result') or {}
        retained = value.get('retention') or {}
        if (status != 'retired' or request.get('resourceId') != identity
                or request.get('sessionId') != root or request.get('state') != 'completed'
                or request.get('action') not in {'workspace.environments.remove', 'workspace.environments.finishCleanup'}
                or value.get('ok') is not True or value.get('status') != 'removed'
                or retained.get('verified') is not True):
            raise ValueError('No completed retention receipt')
        if (not row.get('universeId') or row['universeId'] != value.get('universeId')
                or not re.fullmatch('[a-f0-9]{64}', row.get('sourceHash') or '')
                or row['sourceHash'] != retained.get('sourceSha256')):
            raise ValueError('The saved source or environment identity does not match')
        source = number(retained.get('sourceBytes'))
        if source is None:
            raise ValueError('Incomplete accounting')
        policy = retained.get('policy')
        original = archive_selection(value, 'archives', row['universeId'], policy)
        downloaded = bool(value.get('downloadedArchivesPresent'))
        if downloaded and archive_selection(value, 'downloadedArchives', row['universeId'], policy) != original:
            raise ValueError('The downloaded archives do not match the original exports')
        roles = {role: size for role, (size, _) in original.items()}
        total = source + sum(roles.values())
        if number(total) is None or number(retained.get('archiveBytes')) != sum(roles.values()) or number(retained.get('totalBytes')) != total:
            raise ValueError('Inconsistent accounting')
        expiry = row.get('workspaceArchiveExpiry') or {}
        if (expiry.get('status') == 'expired' and isinstance(row.get('removalRequestId'), str)
                and expiry.get('requestId') == row['removalRequestId']
                and expiry.get('sha256') == original.get('/workspace', (None, None))[1]):
            result['workspaceAvailability'] = 'expired'
        result.update(sourceBytes=source, workspaceBytes=roles.get('/workspace'),
                      resultBytes=roles.get('/artifacts'), otherArchiveBytes=roles.get('/', 0), totalBytes=total,
                      combinedArchive=policy == 'full',
                      custody=('Remote results downloaded; additional copies may remain on the test machine.' if downloaded else
                               'Recorded on the test machine; local download is not confirmed.' if row.get('remote') else
                               'Recorded by this installation.'))
    except (KeyError, ValueError, TypeError, AttributeError):
        result['detail'] = 'Complete saved-size accounting is unavailable. Its files remain protected.'
    return result


def read(app, before=None):
    """Read a bounded canonical ledger page without creating or migrating it."""
    if before is not None and (type(before) is not int or not 1 <= before <= LIMIT):
        raise ValueError('Choose a valid saved-test page.')
    folder = Path(app.data_dir) / 'workspace-resources'
    path = folder / 'reservations.sqlite3'
    empty = {'items': [], 'nextBefore': None, 'recordedBytes': 0, 'accountedCount': 0,
             'unaccountedCount': 0, 'pageSize': PAGE, 'note': NOTE}
    try:
        if folder.is_symlink() or path.is_symlink():
            raise ValueError('The saved-test ledger changed.')
        if not path.exists():
            return empty
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('The saved-test ledger is unavailable.')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            # Select small metadata, not command output, source paths or history.
            deadline = time.monotonic() + 1
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            columns = ','.join(f"CASE WHEN typeof({name})='text' AND length({name}) BETWEEN 1 AND 200 THEN {name} END"
                               for name in ('id', 'root', 'status'))
            rows = db.execute('SELECT rowid,' + columns + ',' + projection() +
                " FROM allocations WHERE rowid<? AND json_valid(body) AND json_extract(body,'$.kind')='test-environment' ORDER BY rowid DESC LIMIT ?",
                (before or LIMIT, PAGE + 1)).fetchall()
        items = [project(*row) for row in rows[:PAGE]]
        return {**empty, 'items': items, 'nextBefore': rows[PAGE-1][0] if len(rows) > PAGE else None,
                'recordedBytes': sum(item['totalBytes'] or 0 for item in items),
                'accountedCount': sum(item['totalBytes'] is not None for item in items),
                'unaccountedCount': sum(item['totalBytes'] is None for item in items)}
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise ValueError('Saved test sizes could not be read. Existing files are unchanged; try again later.') from exc
