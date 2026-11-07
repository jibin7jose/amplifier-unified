"""Publisher-owned local body retirement, with one exact private-body identity.

No scheduler, app authority or remote effects live here. A saved detached intent
owns its remaining private cleanup; an untouched original needs fresh admission.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
import stat

from . import retention
from .publisher import PublishingError, _digest, _identifier, _now, _open_directory


def _stopped(event):
    if event is not None and event.is_set():
        raise PublishingError('cancelled', 'Publishing retirement was stopped; its actual disposition remains available')


def _identity(info):
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]


def _stat(fd, name):
    try:
        return _identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
    except FileNotFoundError:
        return None


def _hold(message):
    raise PublishingError('retirement_held', message)


def _settings(value):
    try:
        return retention.policy(value)
    except ValueError as exc:
        raise PublishingError('invalid_argument', str(exc)) from exc


def _eligibility(owner, state, policy):
    if policy is None:
        return 'policy-disabled', None
    if state is None or state.get('policy') is None:
        return 'durable', None
    if state.get('availability') != 'available':
        return 'body-unavailable', None
    if state['pinned']:
        return 'pinned', None
    identity = state['id']
    # Do not hydrate deployedReleaseIds/rollback history merely to test a live
    # reference. SQLite parses that row, but Python receives only these facts.
    site = owner._db.execute("SELECT json_extract(body,'$.status'),json_extract(body,'$.releaseId'), "
                            "json_array_length(body,'$.unknownRequestIds') FROM records WHERE kind='site' AND id=?",
                            (state['siteId'],)).fetchone()
    if site and (site[0] == 'unknown' or site[2]):
        return 'unresolved-site', None
    if site and site[1] == identity and site[0] != 'removed':
        return 'current-or-resumable', None
    preview = owner._get('preview', identity)
    if preview and preview['status'] == 'running':
        return 'live-or-unsettled-preview', None
    with owner._readers_idle:
        if owner._body_reads.get(identity):
            return 'active-reader', None
    unresolved = owner._db.execute("SELECT 1 FROM receipts INDEXED BY publishing_unsettled WHERE session=? AND json_extract(body,'$.siteId')=? "
                                   "AND json_extract(body,'$.state') IN ('running','unknown') "
                                   "AND json_extract(body,'$.action')!='retire' "
                                   "AND (json_extract(body,'$.state')='running' OR json_extract(body,'$.action') "
                                   "NOT IN ('deploy','rollback','stop','remove')) LIMIT 1",
                                   (state['sessionId'], state['siteId'])).fetchone()
    if unresolved:
        return 'unresolved-effect', None
    if not state.get('unusedSince'):
        return 'unused-grace-not-established', None
    days = max(state['policy']['keepDays'], policy['keepDays'])
    deadline = datetime.fromisoformat(state['unusedSince']) + timedelta(days=days)
    if state.get('rollbackUntil'):
        deadline = max(deadline, datetime.fromisoformat(state['rollbackUntil']))
    if state.get('supersededAt'):
        deadline = max(deadline, datetime.fromisoformat(state['supersededAt']) + timedelta(days=policy['rollbackKeepDays']))
    return ('grace' if datetime.fromisoformat(_now()) < deadline else None), deadline.isoformat()


def candidates(owner, session_id, *, policy, limit=25, after=None):
    _identifier(session_id, 'sessionId')
    settings = _settings(policy)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise PublishingError('invalid_argument', 'Choose a page of 1 to 100 release bodies')
    if after is not None:
        _identifier(after, 'after')
    with owner._mutex:
        owner._check_open()
        rows = owner._db.execute("SELECT body FROM records INDEXED BY publishing_temporary WHERE kind='retention' AND json_type(body,'$.policy')='object' "
                                 "AND json_extract(body,'$.availability')='available' AND json_extract(body,'$.sessionId')=? "
                                 "AND id>? ORDER BY id LIMIT ?", (session_id, after or '', limit + 1)).fetchall()
        items = []
        for (body,) in rows[:limit]:
            state = json.loads(body)
            reason, deadline = _eligibility(owner, state, settings)
            items.append({'releaseId': state['id'], 'revision': state['revision'], 'payloadGeneration': state['payloadGeneration'],
                          'eligible': reason is None, 'hold': reason, 'notBefore': deadline})
        return {'items': items, 'inspected': len(items), 'nextAfter': items[-1]['releaseId'] if len(rows) > limit else None}


@contextmanager
def _directory_at(parent, name, identity=None):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        if identity is not None and _identity(os.fstat(fd)) != identity:
            _hold('A retirement path was replaced; no other directory was selected')
        yield fd
    finally:
        os.close(fd)


def _unlink_tree(fd, event):
    """The detached root is wholly private; never follow a link outside it."""
    for name in os.listdir(fd):
        _stopped(event)
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with _directory_at(fd, name, _identity(info)) as child:
                _unlink_tree(child, event)
            if _stat(fd, name) != _identity(info):
                _hold('An owned directory changed before removal')
            os.rmdir(name, dir_fd=fd)
        else:
            # This removes an owned link itself, never its external target.
            os.unlink(name, dir_fd=fd)
    os.fsync(fd)


def _save_state(owner, state):
    state['revision'] += 1
    owner._put('retention', state)


def _finish(owner, state, phase):
    state.pop('retirement', None)
    state.update(availability='expired' if phase == 'complete' else 'available', retirementPhase=phase)
    if phase == 'complete':
        state['expiredAt'] = _now()
    _save_state(owner, state)


def _resume(owner, identity, event):
    """Observe original-present intent, or finish a proven detached private tree."""
    with owner._mutex:
        state = owner._get('retention', identity)
        intent = state['retirement']
    parent = _open_directory(owner._content)
    retired = None
    try:
        retired = _open_directory(owner.root / 'retiring')
        if _identity(os.fstat(parent)) != intent['contentParent'] or _identity(os.fstat(retired)) != intent['retiredParent']:
            _hold('A publishing storage parent was replaced')
        original = _stat(parent, identity)
        container = _stat(retired, intent['directory'])
        if container is not None and container != intent['container']:
            _hold('A private retirement container was replaced')
        if container is None:
            if original == intent['bodyIdentity'] and intent['phase'] == 'prepared':
                # A previous recovery may have removed the empty control
                # directory before committing its restored availability.
                with owner._mutex:
                    _finish(owner, state, 'admission-required')
                return 'admission-required'
            if original is not None or intent['phase'] != 'detached':
                _hold('Retirement location is ambiguous; no missing path was inferred expired')
        else:
            with _directory_at(retired, intent['directory'], container) as private:
                names = os.listdir(private)
                if set(names) - {'body'}:
                    _hold('Private retirement container contains an unrecognized entry')
                detached = _stat(private, 'body')
                if original is not None:
                    if detached is not None or original != intent['bodyIdentity'] or intent['phase'] != 'prepared':
                        _hold('Original and retirement paths do not prove one owned body')
                    os.rmdir(intent['directory'], dir_fd=retired)
                    os.fsync(retired)
                    with owner._mutex:
                        _finish(owner, state, 'admission-required')
                    return 'admission-required'
                if detached is not None:
                    if detached != intent['bodyIdentity']:
                        _hold('Detached release body was replaced')
                    # Rename may have completed before the phase commit. Its exact
                    # inode proves the one admitted detach; no fresh detach occurs.
                    with owner._mutex:
                        state['retirement']['phase'] = 'detached'
                        state['retirementPhase'] = 'detached'
                        _save_state(owner, state)
                    with _directory_at(private, 'body', detached) as body:
                        _unlink_tree(body, event)
                    if _stat(private, 'body') != detached:
                        _hold('Detached release changed before final removal')
                    os.rmdir('body', dir_fd=private)
                    os.fsync(private)
                elif intent['phase'] != 'detached':
                    _hold('An unproven missing original cannot be classified expired')
            os.rmdir(intent['directory'], dir_fd=retired)
        os.fsync(retired)
        with owner._mutex:
            _finish(owner, state, 'complete')
        return 'complete'
    finally:
        os.close(parent)
        if retired is not None:
            os.close(retired)


def _record_hold(owner, identity, error):
    with owner._mutex:
        state = owner._get('retention', identity)
        if state and state.get('retirement'):
            state.update(availability='unknown', retirementPhase='held')
            state['retirement']['hold'] = {'code': getattr(error, 'code', 'unknown_outcome'), 'message': str(error)[:300]}
            _save_state(owner, state)


def reconcile(owner, *, limit=1, after=None, stop_event=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise PublishingError('invalid_argument', 'Choose a recovery page of 1 to 100 intents')
    if after is not None:
        _identifier(after, 'after')
    with owner._mutex:
        owner._check_open()
        rows = owner._db.execute("SELECT id FROM records INDEXED BY publishing_retiring WHERE kind='retention' AND json_type(body,'$.retirement')='object' AND id>? ORDER BY id LIMIT ?", (after or '', limit + 1)).fetchall()
    results = []
    for (identity,) in rows[:limit]:
        _stopped(stop_event)
        with owner._mutex:
            owner._check_open()
            if identity in owner._retirement_workers or not owner._get('retention', identity).get('retirement'):
                continue
            owner._retirement_workers.add(identity)
        try:
            phase = _resume(owner, identity, stop_event)
            results.append({'releaseId': identity, 'phase': phase})
        except (OSError, PublishingError) as exc:
            _record_hold(owner, identity, exc)
            results.append({'releaseId': identity, 'phase': 'held', 'reason': getattr(exc, 'code', 'filesystem-error')})
        finally:
            with owner._mutex:
                owner._retirement_workers.remove(identity)
                owner._idle.notify_all()
    return {'items': results, 'nextAfter': rows[limit - 1][0] if len(rows) > limit else None}


def retire(owner, identity, session_id, request_id, expected_revision, policy, stop_event):
    _identifier(identity, 'releaseId')
    _identifier(session_id, 'sessionId')
    _identifier(request_id, 'requestId')
    fingerprint = _digest({'action': 'retire', 'releaseId': identity, 'expectedRevision': expected_revision, 'policy': policy})
    with owner._mutex:
        owner._check_open()
        previous = owner._db.execute('SELECT fingerprint,body FROM receipts WHERE session=? AND request=?', (session_id, request_id)).fetchone()
        if previous:
            if previous[0] != fingerprint:
                raise PublishingError('request_conflict', 'requestId was already used with a different action or payload')
            return owner._receipt_result(json.loads(previous[1]), 'retire')
        row = owner._db.execute("SELECT json_extract(body,'$.sessionId'),json_extract(body,'$.siteId') FROM records WHERE kind='release' AND id=?", (identity,)).fetchone()
        release = owner._scope({'id': identity, 'sessionId': row[0], 'siteId': row[1]} if row else None, session_id, 'Release')
        receipt = {'id': _digest([session_id, request_id]), 'sessionId': session_id, 'requestId': request_id,
                   'action': 'retire', 'siteId': release['siteId'], 'releaseId': identity, 'state': 'running',
                   'createdAt': _now(), 'completedAt': None, 'result': None, 'error': None}
        owner._save_receipt(receipt, fingerprint)
        if identity in owner._retirement_workers:
            receipt.update(state='failed', completedAt=_now(), error={'code': 'retirement_busy', 'message': 'This body has an active retirement owner'})
            owner._save_receipt(receipt, fingerprint)
            return owner._receipt_result(receipt, 'retire')
        owner._retirement_workers.add(identity)
    content = body = retired = private = None
    admitted = False
    try:
        settings = _settings(policy)
        with owner._mutex:
            state = owner._get('retention', identity)
            if type(expected_revision) is not int or expected_revision < 0:
                raise PublishingError('invalid_argument', 'Choose a nonnegative lifecycle revision')
            if (state['revision'] if state else 0) != expected_revision:
                raise PublishingError('stale_revision', 'Release retention changed; inspect its current revision')
            reason, _deadline = _eligibility(owner, state, settings)
            if reason:
                _hold('Release is retained: ' + reason)
        _stopped(stop_event)
        content = _open_directory(owner._content)
        body = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=content)
        body_identity = _identity(os.fstat(body))
        with owner._mutex, owner._readers_idle:
            owner._check_open()
            current = owner._get('retention', identity)
            if current['revision'] != expected_revision:
                raise PublishingError('stale_revision', 'Release changed during retirement inspection')
            reason, _deadline = _eligibility(owner, current, settings)
            if reason:
                _hold('Release became retained: ' + reason)
            if _stat(content, identity) != body_identity:
                _hold('The inspected body path changed')
            _stopped(stop_event)
            root = _open_directory(owner.root)
            try:
                if _stat(root, 'releases') != _identity(os.fstat(content)):
                    _hold('The publishing content parent changed during inspection')
                try:
                    os.mkdir('retiring', mode=0o700, dir_fd=root)
                    os.fsync(root)
                except FileExistsError:
                    pass
                retired = os.open('retiring', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            finally:
                os.close(root)
            name = _digest([session_id, request_id, current['payloadGeneration']])
            os.mkdir(name, mode=0o700, dir_fd=retired)
            private = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=retired)
            intent = {'version': 1, 'requestId': request_id, 'payloadGeneration': current['payloadGeneration'],
                      'policy': settings, 'phase': 'prepared',
                      'directory': name, 'container': _identity(os.fstat(private)), 'bodyIdentity': body_identity,
                      'contentParent': _identity(os.fstat(content)), 'retiredParent': _identity(os.fstat(retired))}
            os.fsync(retired)
            current.update(availability='retiring', retirement=intent, retirementPhase='prepared', retirementRequestId=request_id)
            owner._db.execute('BEGIN IMMEDIATE')
            try:
                _save_state(owner, current)
                owner._save_receipt(receipt, fingerprint)
                owner._db.execute('COMMIT')
            except BaseException:
                if owner._db.in_transaction:
                    owner._db.execute('ROLLBACK')
                raise
            admitted = True
            _stopped(stop_event)
            os.rename(identity, 'body', src_dir_fd=content, dst_dir_fd=private)
            os.fsync(content)
            os.fsync(private)
            current['retirement']['phase'] = 'detached'
            current['retirementPhase'] = 'detached'
            _save_state(owner, current)
        _resume(owner, identity, stop_event)
        with owner._mutex:
            receipt.update(state='succeeded', completedAt=_now(), result=retention.projection(owner._get('retention', identity), identity=True))
            owner._save_receipt(receipt, fingerprint)
        return receipt
    except Exception as exc:
        if admitted:
            _record_hold(owner, identity, exc)
        with owner._mutex:
            code = getattr(exc, 'code', 'unknown_outcome')
            receipt.update(state='unknown' if admitted else 'failed', completedAt=_now(),
                           error={'code': code, 'message': str(exc)[:300]})
            owner._save_receipt(receipt, fingerprint)
        raise PublishingError(code, str(exc), receipt=receipt) from exc
    finally:
        try:
            if not admitted and private is not None and not os.listdir(private):
                if _stat(retired, name) == _identity(os.fstat(private)):
                    os.rmdir(name, dir_fd=retired)
                    os.fsync(retired)
        finally:
            for fd in (private, retired, body, content):
                if fd is not None:
                    os.close(fd)
            with owner._mutex:
                owner._retirement_workers.remove(identity)
                owner._idle.notify_all()
