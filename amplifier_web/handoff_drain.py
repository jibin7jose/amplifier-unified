"""Private native identities for work already admitted before a checkout move."""
import copy
import uuid


_FIELDS = ('sessionId', 'parentSessionId', 'runId', 'callId')


def _child_identity(entry):
    return {key: entry.get(key) for key in _FIELDS}


def native_child(worker, call):
    """Stamp at request creation, including requests not yet at the host queue."""
    from .host.children import _CHILD_ADMISSION
    current = _CHILD_ADMISSION.get()
    if not current or call.get('lifecycle') == 'background':
        return None
    registry, entry = current
    if (registry is not worker.session.coordinator.get_capability('live.children')
            or registry.admissions.get(entry['runId']) is not entry
            or entry['task'].done() or entry['sessionId'] != call.get('sessionId')):
        return None
    result = _child_identity(entry)
    drain = (getattr(worker, '_handoff_boundary', None) or {}).get('drain') or {}
    for (owner, identity), (loop, job) in drain.get('jobs', {}).items():
        if (owner == entry['parentSessionId'] and job.get('call_id') == entry['callId']
                and loop.jobs.get(identity) is job and not job['task'].done()):
            result['job'] = {'ownerSessionId': owner, 'jobId': identity, 'callId': entry['callId']}
            break
    return result


def own_target(worker, args):
    """Omitted and host-supplied aliases identify this worker, never a peer."""
    if not worker.session or args.get('_runtimeSessionId') != worker.session.session_id:
        return False
    owner = (worker.start_config or {}).get('ownerSessionId')
    return args.get('args', {}).get('sessionId') in {None, worker.session.session_id, owner}


def acknowledged_receipt(worker, data):
    """Recognize only this marker's exact transport acknowledgement."""
    marker = getattr(worker, '_handoff_boundary', None) or {}
    drain = marker.get('drain') or {}
    if data.get('id') != drain.get('bridgeId') or not drain.get('bridgeId'):
        return
    result = data.get('result')
    if isinstance(data.get('_handoffDisposition'), dict):
        return data['_handoffDisposition']
    elif isinstance(result, dict) and result.get('accepted') is True:
        return result.get('result')
    elif isinstance(result, dict) and result.get('accepted') is False:
        return {'id': marker['id'], 'phase': 'absent'}


async def begin(worker, command_id):
    """Freeze new delegation before sending the action, without cancelling work.

    A known rejection releases this provisional gate. An unknown transport result
    keeps the exact command's marker for the existing receipt reconciliation path.
    New spawn attempts can briefly be refused even if the move ultimately rejects.
    """
    if not isinstance(command_id, str) or not command_id or not worker.runtime.generation:
        return None
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'worktree-handoff:' + command_id))
    from .handoff_boundary import inspect
    await inspect(worker, identity)
    try:
        return _freeze(worker)
    except BaseException:
        await inspect(worker, identity, cancel=True)
        raise


def _freeze(worker):
    marker = worker._handoff_boundary
    detail = ('Your settings change is waiting for existing workers. New delegated work can start after it finishes.'
              if marker.get('configuration') else
              'The app update is waiting for existing workers. Start new delegated work after the update finishes.'
              if marker.get('update') else 'This checkout move is waiting for existing workers. Start new delegated work after the move finishes.')
    if marker.get('drain'):
        return copy.deepcopy(marker['drain']['snapshot'])
    registry = worker.session.coordinator.get_capability('live.children')
    entries = dict(registry.admissions) if registry else {}
    active = {entry[key] for entry in entries.values() for key in ('sessionId', 'parentSessionId')}
    sessions = {sid: registry.sessions[sid] for sid in active if sid in registry.sessions} if registry else {}
    sessions[worker.session.session_id] = worker.session
    jobs = {}
    for sid, session in sessions.items():
        loop = session.coordinator.get('orchestrator')
        pending = {getattr(command, 'call_id', None) for command in getattr(loop, 'pending', [])
                   if command.kind == 'service' and command.source == 'amplifier-delegate'}
        for job_id, job in (getattr(loop, 'jobs', {}) or {}).items():
            if not job['task'].done() or job.get('status') == 'pending' or job.get('call_id') in pending:
                jobs[(sid, job_id)] = (loop, job)
    snapshot = {'children': [_child_identity(entry) for entry in entries.values()],
                'jobs': [{'ownerSessionId': sid, 'jobId': job_id, 'callId': job['call_id']}
                         for (sid, job_id), (_, job) in jobs.items()]}
    drain = marker['drain'] = {'snapshot': snapshot, 'binding': None, 'jobs': jobs,
                              'children': entries, 'inputs': {}}
    unregistrations = []
    prior = marker['unregister']
    def unregister():
        for callback in reversed(unregistrations):
            callback()
        prior()
    marker['unregister'] = unregister
    if registry:
        previous = registry.admission_gate
        def gate(entry):
            if previous:
                previous(entry)
            if marker.get('updateCurrent') and not marker['updateCurrent']():
                return  # Fresh input or task authority supersedes only this update's freeze.
            # An accepted native job may not have entered Children yet. Only
            # its exact parent/job permits that first entry, never a grandchild
            # inheriting the outer job's context variable.
            key = next((key for key, (_, job) in jobs.items()
                        if key[0] == entry['parentSessionId'] and job['call_id'] == entry['callId']
                        and not job['task'].done() and not job.get('cancel_requested')), None)
            used = any(value['parentSessionId'] == entry['parentSessionId']
                       and value['callId'] == entry['callId'] for value in drain['children'].values())
            if key is None or used:
                raise ValueError(detail)
            drain['children'][entry['runId']] = entry
        registry.admission_gate = gate
        def restore():
            if registry.admission_gate is gate:
                registry.admission_gate = previous
        unregistrations.append(restore)
    from amplifier_core import HookResult
    for sid, session in sessions.items():
        loop = session.coordinator.get('orchestrator')
        names = set(getattr(loop, 'config', {}).get('background_tools', ['delegate'])) | {'delegate'}
        async def no_new_delegation(event, data, *, owner=sid, names=names):
            if marker.get('updateCurrent') and not marker['updateCurrent']():
                return HookResult()
            if data.get('tool_name') in names and not any(
                    key[0] == owner and job['call_id'] == data.get('tool_call_id')
                    for key, (_, job) in jobs.items()):
                return HookResult(action='deny', reason=detail)
            return HookResult()
        unregistrations.append(session.coordinator.hooks.register('tool:pre', no_new_delegation,
            name='unified-checkout-drain', priority=-100))
    return copy.deepcopy(snapshot)


async def finish(worker, receipt):
    marker = getattr(worker, '_handoff_boundary', None) or {}
    if not marker.get('drain') or not isinstance(receipt, dict) or receipt.get('id') != marker.get('id'):
        return
    if (receipt.get('phase') == 'pending' and not receipt.get('managerPlacement')
            and not receipt.get('reconciles') and not receipt.get('release')
            and receipt.get('finishingTurn', {}).get('nativeDrain') == marker['drain']['snapshot']):
        marker['drain']['binding'] = copy.deepcopy(receipt['finishingTurn'])
    elif (receipt.get('phase') in {'rejected', 'applied'}
          or receipt.get('phase') == 'absent' and not marker['drain']['binding']):
        from .handoff_boundary import inspect
        await inspect(worker, marker['id'], cancel=True)


def observe(worker, event):
    marker = getattr(worker, '_handoff_boundary', None) or {}
    drain = marker.get('drain')
    if (not drain or event.get('type') != 'input.delivered'
            or event.get('source') != 'amplifier-delegate' or not event.get('input_id')
            or not event.get('call_id')):
        return
    root = worker.session.session_id
    for (sid, identity), (loop, job) in drain['jobs'].items():
        if (sid == root and loop.jobs.get(identity) is job and job.get('call_id') == event['call_id']
                and job.get('status') in {'returned', 'failed', 'cancelled'}):
            drain['inputs'][event['input_id']] = event['call_id']
            break


def witness(worker, call):
    marker = getattr(worker, '_handoff_boundary', None) or {}
    drain = marker.get('drain')
    runtime = getattr(worker, 'runtime', None)
    if (not drain or not drain['binding'] or not runtime or runtime.closed or runtime.stop_requested
            or worker.session.coordinator.cancellation.is_cancelled):
        return None
    root = worker.session.session_id
    if call.get('rootSessionId') != root or call.get('sessionId') != root or call.get('lifecycle') == 'background':
        return None
    binding, generation = drain['binding'], runtime.generation or {}
    inputs = generation.get('input_ids') or []
    allowed = set(drain['inputs'])
    if generation.get('id') == binding['generationId']:
        allowed.update(binding['inputIds'])
    if not inputs or not set(inputs) <= allowed:
        return None
    return {'handoffId': marker['id'], 'finishingTurn': binding, 'kind': 'root',
            'generationId': generation['id'], 'inputIds': list(inputs)}


def receipt_disposition(record):
    """Only the native boundary fields cross this private acknowledgement."""
    return {key: copy.deepcopy(record[key]) for key in
            ('id', 'phase', 'finishingTurn', 'managerPlacement', 'reconciles', 'release') if key in record}


def failed_disposition(service, sid, operation, args):
    """Called under app.lock after the handler fails, never while it may write."""
    from .collaboration import PRINCIPAL
    session = service._session(sid)
    if (operation not in {'dispatch', 'action.dispatch'} or args.get('action') != 'worktree.handoff'
            or not isinstance(args.get('_handoffSnapshot'), dict) or not args.get('id')
            or PRINCIPAL.get() != (session.get('runtimeSessionId') or sid)
            or args.get('args', {}).get('sessionId') not in {None, sid, session.get('runtimeSessionId')}):
        return None
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'worktree-handoff:' + args['id']))
    path = service.worktrees.receipt_dir / (identity + '.json')
    try:
        path.stat()
    except FileNotFoundError:
        # A retained command without its native receipt is uncertain, not absent.
        if not service.db.execute('SELECT 1 FROM commands WHERE id=?', (args['id'],)).fetchone():
            return {'id': identity, 'phase': 'absent'}
        return None
    return receipt_disposition(service.worktrees.read(sid, identity))
