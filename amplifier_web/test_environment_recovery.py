"""Explicit owner-scoped reconciliation; backend inspection never repeats effects."""
from __future__ import annotations

from contextlib import closing
import json
import os
import re
import sqlite3
import stat
import time
import uuid

from filelock import FileLock

ACTION = 'workspace.environments.reconcile'


def _retained_verification(row, original):
    """Explain an exact saved rejection, never qualify or reconstruct an effect."""
    absent = {'status': 'not_retained'}
    result = original.get('result')
    if not isinstance(result, dict) or 'verification' not in result:
        return absent
    if (original.get('resourceId') != row['id'] or original.get('state') != 'failed'
            or result.get('ok') is not False
            or result.get('universeId') != row.get('universeId')
            or result.get('effectAdmitted') is not False
            or result.get('phase') not in {'preserving', 'verifying'}
            or result.get('code') not in {'preservation_failed', 'archive_verification_failed'}):
        return {'status': 'unverified'}
    from .environment_retention import validation_diagnostic
    diagnostic = validation_diagnostic(result['verification'])
    return {'status': 'recorded', **diagnostic} if diagnostic else {'status': 'unverified'}


def _removal_page(app, sid, rid, offset, limit):
    """Project only bounded journal scalars, filtering before pagination."""
    journal = app.operations.requests.journal
    with journal.lock:
        rows = journal.db.execute(
            "SELECT rowid,id,json_extract(value,'$.sessionId'),"
            "json_extract(value,'$.requestId'),json_extract(value,'$.state'),"
            "json_type(value,'$.resourceId'),json_extract(value,'$.resourceId') "
            "FROM operation_requests WHERE session_id=? "
            "AND json_extract(value,'$.action')='workspace.environments.remove' "
            "AND (json_extract(value,'$.resourceId')=? OR ("
            "(COALESCE(json_type(value,'$.resourceId'),'null')!='text' "
            "OR json_extract(value,'$.resourceId')='') "
            "AND COALESCE(json_extract(value,'$.state'),'outcome_unknown') NOT IN ('completed','failed'))) "
            "ORDER BY rowid LIMIT ? OFFSET ?", (sid, rid, limit + 1, offset)).fetchall()
    items = []
    for _, identity, root, request, state, resource_type, resource in rows:
        if (root != sid or request != identity or not isinstance(identity, str)
                or not identity or len(identity) > 200
                or state not in ('admitting', 'running', 'completed', 'failed', 'outcome_unknown')):
            raise ValueError('invalid retained removal metadata')
        items.append({'originalRequestId': identity, 'state': state,
                      'binding': 'resource' if resource_type == 'text' and resource == rid else 'unbound'})
    return rows, items[:limit], offset + limit if len(rows) > limit else None


def read_removal(app, args, origin, caller, *, listing=False):
    """Read one retained pair, never infer a historical binding or repair it.

    No await or mutation admission occurs here. The existing physical lock and
    a read-only ledger connection delimit the snapshot; duplicate reads do not
    create operation records. A mutation grant is irrelevant to this read.
    """
    from . import task_resources as storage
    from .collaboration import BINDING, PRINCIPAL
    from .service import AppError

    def root_binding():
        sid = args['sessionId']
        if origin not in {'ui', 'user'} and sid != caller:
            raise AppError('Removal inspection requires the recorded root owner.', 403)
        fields = ('id', 'sessionKind', 'workspace', 'workingDirectory', 'executionRevision',
                  'interruptionRevision', 'runtimeSessionId', 'nativeIdentity', 'nativeProject',
                  'nativeParentId')
        placement_unavailable = False
        try:
            source, = app._session_read_sources([sid], fields)
            if (not isinstance(source, dict) or source.get('id') != sid or not isinstance(source.get('workspace'), str)
                    or not source['workspace']
                    or source.get('sessionKind') not in (None, 'root')
                    or source.get('sessionKind') is None and source.get('nativeParentId')
                    or any(k in source and (not isinstance(source[k], str) or not source[k])
                           for k in ('runtimeSessionId', 'nativeIdentity', 'nativeProject', 'workingDirectory'))
                    or any(k in source and (type(source[k]) is not int or source[k] < 0)
                           for k in ('executionRevision', 'interruptionRevision'))):
                raise ValueError('incomplete owner metadata')
            # Local app roots legitimately start without native/placement fields.
            # Keep absent fields absent in the snapshot, not inferred proof of
            # revision zero. A native owner must supply its full placement facts.
            if source.get('runtimeSessionId') or source.get('nativeIdentity'):
                required = ('id', 'sessionKind', 'workspace', 'workingDirectory',
                            'executionRevision', 'interruptionRevision')
                if any(k not in source for k in required):
                    placement_unavailable = True
                    raise ValueError('native placement metadata unavailable')
                if not any(row['id'] == sid for row in app.state['sessions']):
                    # Saved directory summaries default missing revisions to 0.
                    # Confirm native-owner facts in the exact canonical record;
                    # select only these scalars, never runtime/config/history.
                    columns = ','.join(f"json_type(value,'$.{k}'),json_extract(value,'$.{k}')"
                                       for k in required)
                    saved = app.db.execute(f"SELECT {columns} FROM state_records "
                                           "WHERE kind='session' AND id=?", (sid,)).fetchone()
                    if saved is None or any(saved[2*i] != ('integer' if k.endswith('Revision') else 'text')
                                            or saved[2*i+1] != source[k] for i, k in enumerate(required)):
                        placement_unavailable = True
                        raise ValueError('native saved placement facts unavailable')
        except (AppError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            # Explain only this authenticated caller's incomplete metadata.
            # Still refuse before touching the resource ledger. No field values,
            # existence signal, defaults or restoration authority are disclosed.
            if (placement_unavailable and origin not in {'ui', 'user'} and sid == caller
                    and not source.get('nativeParentId')
                    and PRINCIPAL.get() == (source.get('runtimeSessionId') or source.get('nativeIdentity'))
                    and any(w['id'] == args['workspaceId'] and w['path'] == source['workspace']
                            for w in app.state['workspaces'])):
                raise AppError('Removal inspection requires the recorded root owner. '
                               'Owner placement metadata is unavailable; no resource was inspected.', 403) from None
            raise AppError('Removal inspection requires the recorded root owner.', 403) from None
        principal = source.get('runtimeSessionId') or source.get('nativeIdentity') or sid
        workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
        if not workspace or source['workspace'] != workspace['path']:
            raise AppError('Removal inspection requires the recorded root owner.', 403)
        if origin not in {'ui', 'user'} and (PRINCIPAL.get() != principal or origin == 'system'):
            if not app.access_mode.full(source):
                raise AppError('Removal inspection requires the recorded root owner.', 403)
            if not (origin == 'system' and not PRINCIPAL.get() and not BINDING.get()):
                # Mounted descendants already have a resident owner. Use its
                # current binding without hydrating a cold root or its history.
                live = next((row for row in app.state['sessions'] if row['id'] == sid), None)
                if live is None:
                    raise AppError('Removal inspection requires current native work.', 403)
                app.collaboration.active_binding(live, allow_descendant=True)
        return (principal, *((k in source, source.get(k)) for k in fields))

    captured = root_binding()
    sid, rid = args['sessionId'], args['id']
    try:
        import fcntl  # Same POSIX lock as the managed Linux environment owner.
        if str(uuid.UUID(rid)) != rid:
            raise ValueError('invalid resource identity')
        folder = app.data_dir / 'workspace-resources'
        target = folder / 'reservations.sqlite3'
        if folder.is_symlink() or not stat.S_ISDIR(folder.stat().st_mode) or not stat.S_ISREG(target.lstat().st_mode):
            raise ValueError('unavailable ledger')
        # FileLock's acquire can create/truncate its file. Open the already-owned
        # lock read-only instead; closing releases the same flock without writes.
        descriptor = os.open(folder / ('environment-' + rid + '.lock'),
                             os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise ValueError('invalid lock')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # storage.connect initializes tables/chmods even on a read. Reuse its
            # exact row reader, but never its writable connection initializer.
            with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
                row = storage.get(db, sid, rid)
                if (not isinstance(row, dict) or row.get('id') != rid or row.get('sessionId') != sid
                        or row.get('workspaceId') != args['workspaceId']
                        or row.get('kind') != 'test-environment'
                        or row.get('owner') != {'sessionId': sid, 'principal': captured[0]}):
                    raise AppError('Removal inspection requires the recorded root owner.', 403)
                if type(row.get('revision')) is not int or row['revision'] != args['expectedRevision']:
                    raise AppError('Removal inspection changed or is busy.', 409)
                if listing:
                    offset, limit = args.get('offset', 0), args.get('limit', 20)
                    if type(offset) is not int or not 0 <= offset <= 2**53-21 or type(limit) is not int or not 1 <= limit <= 20:
                        raise ValueError('invalid removal page')
                    snapshot, items, next_offset = _removal_page(app, sid, rid, offset, limit)
                    result = {'version': 1, 'sessionId': sid, 'id': rid, 'revision': row['revision'],
                              'items': items, 'nextOffset': next_offset}
                    if len(json.dumps(result).encode()) > 16384:
                        raise ValueError('removal page exceeds bound')
                    if (storage.get(db, sid, rid) != row
                            or _removal_page(app, sid, rid, offset, limit)[0] != snapshot
                            or root_binding() != captured):
                        raise AppError('Removal inspection changed or is busy.', 409)
                    return result
                try:
                    original = app.operations.requests.read(sid, args['originalRequestId'])
                except ValueError:
                    raise AppError('Original removal record is unavailable.', 404) from None
                if (not isinstance(original, dict) or original.get('sessionId') != sid or original.get('requestId') != args['originalRequestId']
                        or original.get('action') != 'workspace.environments.remove'
                        or original.get('resourceId') not in (None, rid)):
                    raise AppError('Original removal is not bound to this resource.', 409)
                state = original.get('state')
                if state not in ('admitting', 'running', 'completed', 'failed', 'outcome_unknown'):
                    raise ValueError('unsupported retained state')
                if (storage.get(db, sid, rid) != row
                        or app.operations.requests.read(sid, args['originalRequestId']) != original
                        or root_binding() != captured):
                    raise AppError('Removal inspection changed or is busy.', 409)
                from .test_environment_legacy import returned_preservation_failure
                legacy = returned_preservation_failure(row, original)
                available = bool(legacy and row.get('environmentState') == 'stopped')
                if available:
                    from .task_environments import require_resolved_removal
                    try:
                        require_resolved_removal(app, row, legacy=legacy)
                    except ValueError:
                        available = False
                result = {
                    'version': 1, 'sessionId': sid, 'id': rid, 'revision': row['revision'],
                    'original': {'requestId': args['originalRequestId'], 'state': state},
                    'status': 'qualified' if legacy else 'held',
                    'code': legacy['kind'].replace('-', '_') if legacy else
                            'original_binding_missing' if original.get('resourceId') is None
                            else 'original_binding_unverified',
                    'executor': 'returned_and_reaped' if legacy else 'unobserved',
                    'physical': 'not_observed',
                    'continuationAvailable': available,
                    'riskDecisionAvailable': False,
                    'verification': _retained_verification(row, original),
                }
                if not legacy and row.get('environmentState') == 'stopped':
                    from .test_environment_fresh_cleanup import binding
                    if binding(row, original):
                        # A passive discovery hint, not executor/physical proof
                        # or permission to replay the original unknown effect.
                        result['nextAction'] = 'workspace.environments.finishCleanup'
                if len(json.dumps(result).encode()) > 4096:
                    raise ValueError('projection exceeds bound')
                return result
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, ImportError):
        raise AppError('Removal inspection changed or is busy.', 409) from None


def backend_identity(value):
    if not isinstance(value, dict) or value.get('backend') not in {'docker', 'incus'}:
        raise ValueError('The original backend identity is unavailable.')
    expected = {'backend', 'daemon', 'project'} if value['backend'] == 'incus' else {'backend', 'daemon'}
    if set(value) != expected or any(not isinstance(value[key], str) or not value[key] or len(value[key]) > 512 for key in expected):
        raise ValueError('The original backend identity is invalid.')
    return value


def bind_configuration(app, row, config):
    """Retain backend kind and an original daemon pin, never infer an endpoint."""
    from . import task_environments as owner
    if not config or owner.config_key(config) != row['backendKey']:
        return row
    backend = config.get('backend', 'docker')
    changes = {} if row.get('backendType') == backend else {'backendType': backend}
    if row.get('backendIdentity') or backend != 'incus':
        return owner.save(app.data_dir, row, **changes) if changes else row
    incus = config.get('incus', {})
    fingerprint = incus.get('daemonFingerprint')
    project = incus.get('project')
    if (owner.config_key(config) != row['backendKey'] or not isinstance(fingerprint, str)
            or not re.fullmatch(r'[a-f0-9]{64}', fingerprint) or not project):
        return owner.save(app.data_dir, row, **changes) if changes else row
    identity = backend_identity({'backend': 'incus', 'daemon': fingerprint, 'project': project})
    return owner.save(app.data_dir, row, **changes, backendIdentity=identity, backendIdentitySource='retained-config')


def verify(row, result):
    """Absence also requires settled admission: an empty inventory is insufficient."""
    observation = result.get('observation')
    if not isinstance(observation, dict):
        raise ValueError('The backend did not provide an exact recovery observation.')
    if (observation.get('rootOwner') != row['sessionId']
            or observation.get('operationId') != row['launchOperationId']
            or not isinstance(observation.get('universeId'), str) or not observation['universeId']
            or row.get('universeId') and observation['universeId'] != row['universeId']):
        raise ValueError('The recovery observation does not match this environment owner.')
    status = result.get('status')
    if status == 'unknown':
        return False
    if status not in {'running', 'stopped', 'absent'}:
        raise ValueError('The backend did not provide a supported physical state.')
    actual = backend_identity(observation.get('backendIdentity'))
    if row.get('backendIdentity'):
        if actual != backend_identity(row['backendIdentity']):
            raise ValueError('The recovery observation came from a different backend.')
    elif observation.get('backendIdentitySource') != 'retained-launch':
        raise ValueError('No retained receipt proves this older environment’s original backend.')
    if status == 'absent':
        if (observation.get('ownership') != 'absent' or observation.get('absenceConfirmed') is not True
                or observation.get('admissionSettled') is not True):
            raise ValueError('Physical absence is not proven while a launch may still be outstanding.')
    elif observation.get('ownership') != 'matched':
        raise ValueError('The physical environment does not match its recorded owner.')
    return True


def prelaunch_source_failure(app, row):
    """Inspect an exact returned source refusal, never infer physical absence.

    Older helpers used generic UNKNOWN prose, but the host journal records the
    failed prepare before admission and the queue retires that logical owner.
    Missing flags alone, an inventory, or a caller-supplied result is not proof.
    """
    from . import task_environments as owner
    from .test_environment_worker import read_private_record, binding, PRELAUNCH_SOURCE_REFUSALS, source_refusal_message
    if (row.get('status') != 'retired' or row.get('environmentState') != 'not-started'
            or row.get('backendType') != 'docker' or not row.get('backendConfigRetained')
            or row.get('owner', {}).get('sessionId') != row.get('sessionId')
            or any(row.get(key) for key in ('backendAdmitted', 'preparationPending',
                'preparedDependencies', 'hostReservation', 'snapshot', 'durableJob', 'pendingRequest'))):
        return None
    try:
        original = app.operations.requests.read(row['sessionId'], row['commandId'])
        result = original.get('result', {})
        if (original != row.get('lastOperation')
                or original.get('requestId') != row['commandId']
                or original.get('sessionId') != row['sessionId']
                or original.get('resourceId') != row['id']
                or original.get('action') != 'workspace.environments.start'
                or original.get('state') != 'failed' or result.get('ok') is not False
                or result.get('effectAdmitted') is True
                or result.get('status') != 'not-started' or result.get('snapshot') is not None
                or result.get('code') not in PRELAUNCH_SOURCE_REFUSALS
                or not row.get('universeId') or result.get('universeId') != row['universeId']
                or not row.get('launchOperationId')
                or owner.storage.observe(app.data_dir, row)['observation'] != 'present'):
            return None
        recorded = read_private_record(owner.storage.directory(app.data_dir, row) / 'backend-binding.json')
        actual = backend_identity(recorded.get('backendIdentity') if isinstance(recorded, dict) else None)
        if (actual['backend'] != 'docker'
                or row.get('backendIdentity') is not None and row['backendIdentity'] != actual
                or recorded != binding({'rootOwner': row['sessionId'], 'operationId': row['launchOperationId']},
                                       row['universeId'], actual)):
            return None
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return None
    return {'ok': True, 'status': 'not-started', 'effectAdmitted': False,
            'preLaunchFailure': {'originalRequestId': original['requestId'],
                'code': result['code'], 'resourceId': row['id'], 'rootOwner': row['sessionId'],
                'operationId': row['launchOperationId'], 'backendKey': row['backendKey'],
                'backendIdentity': actual, 'universeId': row['universeId']},
            # Existing completed receipts are compared exactly during projection repair.
            'message': ('The original source snapshot failed before launch. No environment or test command was started. The failed request is retained; use stable source for a new check.'
                        if result['code'] == 'repository-source-changed' else
                        source_refusal_message(result['code']) + ' The original failed request is retained.')}


def project(app, row, request):
    from . import task_environments as owner
    if (request.get('action') != ACTION or request.get('state') != 'completed'
            or request.get('resourceId') != row['id'] or request.get('sessionId') != row['sessionId']):
        raise ValueError('The recovery receipt does not match this environment.')
    result = request['result']
    if 'preLaunchFailure' in result:
        if prelaunch_source_failure(app, row) != result:
            raise ValueError('The original pre-launch failure binding changed.')
        return owner.save(app.data_dir, row, pendingRecovery=None, lastRecovery=request,
            observation={'status': 'observed', 'observedAt': time.time(), 'source': 'original-launch-receipt'},
            reason=result['message'])
    verified = verify(row, result)
    changes = {'pendingRecovery': None, 'lastRecovery': request,
               'observation': {'status': 'observed' if verified else 'unverified', 'observedAt': time.time()},
               'reason': result.get('message', 'Current state was inspected without replaying prior operations.')}
    if verified:
        changes.update(environmentState='removed' if result['status'] == 'absent' else result['status'],
                       backendIdentity=result['observation']['backendIdentity'])
        if result['status'] == 'absent':
            if row.get('preparationPending'):
                # Guest absence does not prove the owned dependency image or
                # interrupted builder was removed. Keep its cleanup reachable.
                changes.update(environmentState='not-started', guestRemoved=True,
                               reason='The test guest is absent. Its owned dependency preparation still needs cleanup.')
            else:
                changes['status'] = 'retired'
    updated = owner.save(app.data_dir, row, **changes)
    if (verified and result['status'] in {'stopped', 'absent'}
            and result['observation'].get('admissionSettled') is True
            and (not updated.get('preparationPending') or updated.get('preparedDependencies'))):
        owner.host_resources.settle(updated, release=True)
    return updated


def reservation_candidate(app, rows):
    """Rotate one extra native probe across this bounded page, without authority."""
    from . import task_environments as owner
    candidates = []
    for row in rows:
        if (row.get('status') == 'retired' or row.get('testMachine')
                or row.get('preparationPending') and not row.get('preparedDependencies')):
            continue
        try:
            if owner.host_resources.held(row):
                candidates.append(row['id'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not candidates:
        return None
    previous = getattr(app, '_last_reservation_probe', None)
    index = (candidates.index(previous) + 1) % len(candidates) if previous in candidates else 0
    app._last_reservation_probe = candidates[index]
    return candidates[index]


def recover_reservation(app, row, config, observed, *, guard=None, blocking=True):
    """The caller holds the environment lock; release compute, never effects.

    Ordinary inventory is only a trigger. Exact native reconciliation must also
    prove the original launch settled. Retain a separate observation before
    releasing the lease; an uncertain check/removal remains uncertain.
    """
    from . import task_environments as owner
    if (config.get('remoteRunner')
            or (observed.get('status') not in {'stopped', 'removed', 'absent', 'unknown'}
                and row.get('environmentState') != 'unknown')
            or row.get('pendingRequest')
            or row.get('preparationPending') and not row.get('preparedDependencies')
            or not owner.host_resources.held(row, blocking=blocking)):
        return row
    if observed.get('universeId', row.get('universeId')) != row.get('universeId'):
        raise ValueError('The observed environment identity changed.')
    if guard:
        guard()
    result = owner.invoke(config, app.data_dir, row, 'reconcile', {})
    if (result.get('ok') is not True or not verify(row, result)
            or result.get('status') not in {'stopped', 'absent'}
            or result['observation'].get('admissionSettled') is not True):
        return row
    if owner.get(app.data_dir, row['sessionId'], row['id']) != row:
        raise ValueError('The environment changed during capacity inspection.')
    if guard:
        guard()
    proof = {'version': 1, 'state': 'observed', 'environmentStatus': result['status'],
             'observation': result['observation'], 'observedAt': time.time()}
    updated = owner.save(app.data_dir, row, reservationRecovery=proof)
    if guard:
        guard()
    owner.host_resources.settle(updated, release=True, blocking=blocking)
    return owner.save(app.data_dir, updated, reservationRecovery={**proof, 'state': 'released'})


def repair(app, row):
    if row.get('pendingRecovery'):
        request = app.operations.requests.read(row['sessionId'], row['pendingRecovery'])
        if request.get('state') == 'completed':
            return project(app, row, request)
    return row


def reconcile(app, row, args, config, request, admit):
    from . import task_environments as owner
    home, sid = app.data_dir, row['sessionId']
    lock = FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0)
    locked = observed = False
    result = None
    try:
        lock.acquire()
        locked = True
        admit()
        row = owner.get(home, sid, row['id'])
        if row['revision'] != args['expectedRevision']:
            raise ValueError('The environment changed. Inspect its current revision.')
        if row['backendKey'] != owner.config_key(config):
            raise ValueError('Recovery requires the exact original backend configuration.')
        row = bind_configuration(app, row, config)
        row = repair(app, row)
        if row.get('pendingRequest'):
            prior = app.operations.requests.read(sid, row['pendingRequest'])
            if owner.confirmed_result(row, prior):
                row = owner.project_result(home, row, prior)
        admit()
        row = owner.save(home, row, pendingRecovery=request['requestId'])
        result = prelaunch_source_failure(app, row)
        if result is None:
            result = owner.invoke(config, home, row, 'reconcile', args)
        observed = True
        admit()
        if result.get('ok'):
            if 'preLaunchFailure' not in result:
                verify(row, result)
            elif result != prelaunch_source_failure(app, row):
                raise ValueError('The original pre-launch failure binding changed.')
            request = app.operations.requests.finish(request, 'completed', resourceId=row['id'], result=result,
                message=result.get('message', 'Current state was inspected; earlier uncertain operations are unchanged.'))
            row = project(app, row, request)
        else:
            request = app.operations.requests.finish(request, 'failed', resourceId=row['id'], observationResult=result,
                message=result.get('message', 'The original backend could not verify this environment. Ownership remains retained.'))
            row = owner.save(home, row, pendingRecovery=None, lastRecovery=request)
        return {'resource': owner.public(home, {**row, 'recovery': {'available': row['status'] != 'retired'}}), 'request': request}
    except BaseException as exc:
        try:
            recorded = app.operations.requests.read(sid, request['requestId'])
        except (OSError, ValueError, sqlite3.Error):
            recorded = {}
        if recorded.get('state') == 'completed':
            request = recorded
            try:
                if locked:
                    row = project(app, owner.get(home, sid, row['id']), request)
            except (OSError, ValueError, sqlite3.Error):
                row = {**row, 'reason': 'The recovery observation is saved; refresh to repair its metadata. Earlier uncertain operations are unchanged.'}
        else:
            request = app.operations.requests.finish(request, 'failed' if observed or not locked else 'outcome_unknown',
                resourceId=row['id'], observationResult=result,
                message='Recovery did not establish a safe state: ' + str(exc)[:500] + '. No backend effect was replayed.')
            if locked:
                try:
                    row = owner.save(home, owner.get(home, sid, row['id']), pendingRecovery=None, lastRecovery=request)
                except (OSError, ValueError, sqlite3.Error):
                    pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {'resource': owner.public(home, row), 'request': request}
    finally:
        if locked:
            lock.release()
