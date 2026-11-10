"""The update safety gate and its user-visible reasons share one source."""

import hashlib
import json
import time


def _hold(reason, label, scope='session', *, quiescent=False):
    return {'quiescent': quiescent, 'reasonCode': reason, 'label': label, 'scope': scope}


def operation_snapshot(service, session):
    """Private observation, not authority to recover or clear this input fence."""
    from .configuration_authority import fingerprint
    from .configuration_recovery import evidence, latest_attempt
    management, runtime = service.management, service.runtime
    pending = session.get('pendingConfiguration') or {}
    operation = pending.get('operationId')
    if (not operation or pending.get('phase') != 'error'
            or session.get('status') not in {'idle', 'stopped', 'interrupted', 'error'}
            or not session.get('configurationBusy')
            or session.get('configurationOperationFence') != operation
            or session.get('configurationPending') or session.get('configurationRefresh')
            or any(row.get('phase') in {'pending', 'unknown'} for row in session.get('worktreeHandoffs', []))
            or any(row.get('phase') == 'running' for row in session.get('execution', {}).get('turns', []))
            or any(row.get('status') in {'queued', 'starting', 'running', 'working', 'stopping'}
                   or row.get('persistent') and row.get('status') == 'idle'
                   for row in session.get('workers', []))):
        raise ValueError('Configuration hold is not quiescent.')
    workers = getattr(runtime, 'workers', None)
    observe = getattr(runtime, 'has_pending_operations', None)
    tasks = getattr(management, 'pending_config_tasks', None)
    lock = getattr(management, 'lock', None)
    if (not isinstance(workers, dict) or session['id'] in workers
            or getattr(runtime, '_preparations', None) is None or runtime._preparations
            or not callable(observe) or observe() is not False
            or tasks is None or any(not task.done() for task in tasks.values())
            or lock is None or lock.locked()
            or not callable(getattr(runtime, 'stopped_boundary', None))):
        raise ValueError('Configuration activity cannot be proven settled.')
    record = management.configuration_record(session['id'], operation)
    request, state = record['request'], record['state']
    target = management.configuration_target_binding(session)
    if (any(request.get(key) != value for key, value in target.items())
            or request['source'].get('target') != target
            or request['source'].get('action', 'configuration.apply') != 'configuration.apply'
            or record['revision'] != pending.get('revision')
            or state.get('phase') != 'error' or state.get('effect') not in {'none', 'saved_not_mounted'}
            or any(pending.get(key) != value for key, value in state.items())
            or pending.get('commandId') != request['commandId']
            or pending.get('payloadHash') != request['payloadHash']
            or fingerprint(request['config']) != request['payloadHash']
            or fingerprint(json.loads(management.queued_path(session['id']).read_text())) != request['payloadHash']):
        raise ValueError('Configuration hold binding changed.')
    attempts = record.get('attempts') or []
    if not attempts or len(attempts) > 32:
        raise ValueError('Bounded terminal native evidence is required.')
    latest = latest_attempt(record)
    receipts = []
    native = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
    for number, attempt in enumerate(attempts, 1):
        if (attempt.get('sessionId') != session['id'] or attempt.get('operationId') != operation
                or attempt.get('requestHash') != record['requestHash']
                or attempt.get('payloadHash') != request['payloadHash']
                or attempt.get('nativeSessionId') != native or attempt.get('number') != number
                or attempt.get('action', 'configuration.apply') != 'configuration.apply'):
            raise ValueError('Native effect binding changed.')
        receipt = evidence(attempt, home=service.data_dir)
        expected = state['effect'] if attempt == latest else 'none'
        if not receipt or receipt.get('terminal') is not True or receipt.get('effect') != expected:
            raise ValueError('Native effect is not proven terminal.')
        receipts.append(receipt)
    saved_hash = None
    if state['effect'] == 'saved_not_mounted':
        from .session_files import validate_id
        validate_id(native)
        saved_bytes = (service.data_dir / 'sessions' / native / 'configuration.json').read_bytes()
        saved = json.loads(saved_bytes)
        if not management.configuration_readback_matches(request['config'], saved):
            raise ValueError('The saved configuration evidence changed.')
        saved_hash = hashlib.sha256(saved_bytes).hexdigest()
    return fingerprint({'record': record, 'native': receipts, 'pending': pending,
                        'target': target, 'nativeSessionId': native,
                        'configurationRevision': service.state.get('configurationRevision', 0),
                        'accessMode': service.access_mode.snapshot(session),
                        'savedHash': saved_hash,
                        'configurationPendingRevision': session.get('configurationPendingRevision', 0)})


def operation_holds(service):
    """Capture exact proven holds across activation's external validation awaits."""
    return {row['id']: operation_snapshot(service, row) for row in service.state.get('sessions', [])
            if row.get('configurationBusy') and (row.get('pendingConfiguration') or {}).get('operationId')}


def retain_operation_holds(service, expected, stack):
    """Under final activation lock, keep absent native writers absent until handoff."""
    if operation_holds(service) != expected:
        raise ValueError('The held configuration changed during activation.')
    for sid in expected:
        stack.enter_context(service.runtime.stopped_boundary(service._session(sid)))
    if operation_holds(service) != expected:
        raise ValueError('The held configuration changed before activation admission.')


def _settings_hold(service, session, *, application=False):
    """Explain the first failing predicate of the existing legacy exception.

    Keep the predicate order and admission decision. This observes only: it
    never clears a fence, resolves unknown effects, or permits a chat turn.
    Later predicates are not claimed observed after an earlier one fails.
    """
    pending = session.get('pendingConfiguration', {})
    if session.get('status') not in {'idle', 'stopped', 'interrupted', 'error'}:
        return _hold('session-not-quiescent', 'Conversation has not reached a quiescent state')
    if pending.get('phase') != 'error':
        if pending.get('phase') == 'queued':
            return _hold('configuration-queued', 'Conversation settings are queued')
        if pending.get('phase') == 'applying':
            return _hold('configuration-applying', 'Conversation settings are being applied')
        return _hold('configuration-phase-unverified', 'Conversation settings state needs verification')
    if pending.get('operationId'):
        if not application:
            return _hold('operation-bound-recovery', 'Conversation configuration operation remains held for recovery')
        try:
            operation_snapshot(service, session)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
            return _hold('operation-bound-recovery', 'Conversation configuration operation remains held for recovery')
        return _hold('terminal-operation-quiescent',
                     'Terminal configuration hold is quiescent; its conversation fence remains', quiescent=True)
    if pending.get('recoveryRequired') is not True:
        return _hold('recovery-hold-unverified', 'Conversation settings recovery state needs verification')
    if session.get('configurationOperationFence'):
        return _hold('configuration-operation-fence', 'Conversation configuration operation fence remains held')
    if session.get('configurationPending'):
        return _hold('configuration-pending', 'Conversation configuration changes are pending')
    if session.get('configurationRefresh', {}).get('phase') == 'applying':
        return _hold('configuration-refresh-applying', 'Conversation configuration refresh is being applied')
    if any(turn.get('phase') == 'running' for turn in session.get('execution', {}).get('turns', [])):
        return _hold('running-turn', 'Conversation has a running turn')
    for worker in session.get('workers', []):
        if worker.get('status') in {'queued', 'starting', 'running', 'working', 'stopping'}:
            return _hold('active-worker', 'Conversation has active or queued background work')
        if worker.get('persistent') and worker.get('status') == 'idle':
            return _hold('persistent-worker', 'Conversation has an open persistent background worker')
    management = getattr(service, 'management', None)
    tasks = getattr(management, 'pending_config_tasks', None)
    lock = getattr(management, 'lock', None)
    runtime_pending = getattr(getattr(service, 'runtime', None), 'has_pending_operations', None)
    if tasks is None:
        return _hold('management-tasks-unavailable', 'Update cannot verify configuration task activity', 'global')
    if lock is None:
        return _hold('management-lock-unavailable', 'Update cannot observe the configuration management lock', 'global')
    if lock.locked():
        return _hold('management-lock-held', 'Update waits for global configuration management to settle', 'global')
    if not callable(runtime_pending):
        return _hold('runtime-observer-unavailable', 'Update cannot verify global runtime activity', 'global')
    task = tasks.get(session['id'])
    if task is not None and not task.done():
        return _hold('configuration-task-pending', 'Conversation configuration task has not settled')
    if runtime_pending():
        return _hold('runtime-operations-pending', 'Update waits for global runtime activity to settle', 'global')
    return _hold('legacy-hold-quiescent', 'Legacy recovery hold is quiescent; its conversation fence remains',
                 quiescent=True)


def assess(service, *, application=False):
    """One synchronous gate evaluation; diagnostic detail never grants recovery."""
    state = service.state
    observed_at = time.time()
    revision = state.get('revision')
    result = []
    holds = []
    hold_count = 0
    hold_reasons = {}
    sessions = {row['id']: row for row in state.get('sessions', [])}

    def add(kind, label, sid=None, **detail):
        session = sessions.get(sid, {})
        target = session.get('parentId') or sid
        parent = sessions.get(target, session)
        row = {'kind': kind, 'label': label, **detail}
        if target:
            row.update(sessionId=target, title=parent.get('title') or 'Open conversation')
        if row not in result:
            result.append(row)

    schedules = getattr(service, 'schedules', None)
    if schedules:
        for (sid,) in schedules.store.db.execute(
                "SELECT DISTINCT session_id FROM schedule_runs WHERE phase IN ('claimed','creating','submitting')"):
            add('schedule', 'An already claimed scheduled occurrence is finishing admission', sid)
    drain = state.get('updates', {}).get('drain') or {}
    if drain.get('phase') in {'waiting', 'draining'}:
        for sid, row in drain.get('workers', {}).items():
            if not row.get('ready') and not row.get('suppressed'):
                add('update-boundary', 'Finishing existing work before the app update', sid,
                    waitingFor=row.get('waitingFor', ['native-boundary']))
    if state.get('voicePreviewBusy'):
        add('voice', 'Voice preview is playing')
    runtime = getattr(service, 'runtime', None)
    pending = getattr(runtime, 'has_pending_operations', None)
    runtime_observation = 'unavailable'
    runtime_active = pending and pending()
    if pending:
        runtime_observation = 'pending' if runtime_active else 'clear'
    if runtime_active:
        identify = getattr(runtime, 'pending_session_ids', None)
        ids = identify() if identify else []
        for sid in ids:
            add('runtime', 'Preparing or finishing conversation work', sid)
        if not ids:
            add('runtime', 'Preparing or finishing background runtime work')
    operations = [op for op in state.get('smartTools', {}).get('operations', []) if op.get('status') in {'queued', 'running'}]
    if operations or any(not task.done() for task in getattr(service, 'smart_tool_tasks', ())):
        add('smart-tools', 'A Smart Tool operation is still running')
    for key in ('requests', 'followups'):
        if any(row.get('status') in {'queued', 'sending'} for row in state.get('feedback', {}).get(key, [])):
            add('feedback', 'Feedback delivery is in progress')
    if state.get('voice', {}).get('status') not in {None, 'disconnected', 'idle', 'ended', 'error'}:
        add('voice', 'A voice call is active', state['voice'].get('sessionId'))
    for session in sessions.values():
        sid = session['id']
        if session.get('configurationBusy'):
            unknown = any(row.get('phase') == 'unknown' for row in session.get('worktreeHandoffs', []))
            if unknown:
                hold = _hold('checkout-handoff-unknown', 'Checkout handoff needs inspection; restarting will not resolve it')
                add('checkout-handoff', 'Checkout handoff needs inspection; restarting will not resolve it', sid)
            else:
                hold = _settings_hold(service, session, application=application)
                if not hold['quiescent']:
                    add('configuration', hold['label'], sid if hold['scope'] == 'session' else None,
                        reasonCode=hold['reasonCode'], scope=hold['scope'])
            hold_count += 1
            code = hold['reasonCode']
            hold_reasons[code] = hold_reasons.get(code, 0) + 1
            if len(holds) < 100:
                # Support reports never include chat names, IDs, paths or values.
                holds.append({'sessionRef': hashlib.sha256(sid.encode()).hexdigest()[:16],
                              **{key: hold[key] for key in ('quiescent', 'reasonCode', 'scope')}})
        if session.get('status') in {'working', 'starting', 'stopping'} or (session.get('status') == 'ready' and
                any(turn.get('phase') == 'running' for turn in session.get('execution', {}).get('turns', []))):
            add('conversation', 'Conversation work is running or finishing', sid)
        for worker in session.get('workers', []):
            if worker.get('status') in {'starting', 'running', 'stopping', 'queued'}:
                add('worker', 'Background worker work is running or queued', sid)
            elif worker.get('persistent') and worker.get('status') == 'idle':
                add('worker', 'A persistent background worker is still open', sid)
    observation = {'schemaVersion': 1, 'observedAt': observed_at, 'basis': 'first-failing-predicate',
                   'initialRuntimeObservation': runtime_observation,
                   'blocked': bool(result), 'configurationHolds': holds,
                   'configurationHoldCount': hold_count, 'configurationReasonCounts': hold_reasons,
                   'omittedConfigurationHolds': hold_count - len(holds)}
    if type(revision) is int and 0 <= revision < 10**15:
        observation['stateRevision'] = revision
    return {'blockers': result, 'observation': observation}


def blockers(service, *, application=False):
    return assess(service, application=application)['blockers']
