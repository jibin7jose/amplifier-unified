"""Live native child scope; a parent turn boundary is not child cancellation.

This is private worker provenance, not a model-provided permission or a durable
lease. The worker checks its actual child coordinator/run before sending it; the
host binds it to the still-current process and checks current execution controls.
"""
from .collaboration import BINDING, PRINCIPAL
from .task_binding import dependency_blocked


def live_assignment(service, source, *, child_id=None, explain=False):
    """Authenticate the actual assigned run and its cancellation boundaries.

    This is identity, not readiness for ordinary work. Configuration recovery
    may inspect its own held outcome while that outcome blocks execution.
    """
    binding = BINDING.get() or {}
    origin = binding.get('_childOrigin')
    if not isinstance(origin, dict):
        return 'No authenticated child execution was supplied.' if explain else None
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
    identity = origin.get('runtimeSessionId')
    if (not identity or identity == native or child_id is not None and identity != child_id
            or origin.get('rootSessionId') != native
            or PRINCIPAL.get() != (native if child_id is not None else identity)
            or not isinstance(origin.get('generationId'), str) or not origin['generationId']
            or not isinstance(origin.get('runId'), str) or not origin['runId']):
        return 'The child identity, root, principal, generation, or run binding changed.' if explain else None
    inputs = origin.get('inputIds')
    if (not isinstance(inputs, list) or not inputs
            or any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in inputs)
            or len(set(inputs)) != len(inputs)
            or binding.get('_generationId') != origin['generationId']):
        return 'The child input or generation binding changed.' if explain else None
    workers = getattr(service.runtime, 'workers', None)
    owner = workers.get(source['id']) if isinstance(workers, dict) else None
    if (not owner or owner.get('runtimeOwnerId') != binding.get('_runtimeOwnerId')
            or not owner.get('runtimeOwnerId') or owner['process'].returncode is not None
            or any(owner.get(key) for key in ('closing', 'yielded', 'yielding'))):
        return 'The runtime owner changed or the owning worker is no longer live.' if explain else None
    if (source.get('status') in {'stopped', 'stopping', 'interrupted'}
            or origin.get('interruptionRevision') != source.get('interruptionRevision', 0)):
        return 'The conversation was stopped or interrupted.' if explain else None
    if source.get('fileAccessPending'):
        return 'File access changes are still pending.' if explain else None
    if any(origin.get(key) != source.get(key, 0) for key in
           ('executionRevision', 'configurationPendingRevision')):
        return 'The conversation execution or configuration revision changed.' if explain else None
    from .updates import work_paused
    if work_paused(service.state):
        return 'Work is paused for an application or ecosystem update.' if explain else None
    if (source.get('failure') or {}).get('category') in {'content_filter', 'computer_capture_stop'}:
        return 'A safety halt is active for this conversation.' if explain else None
    if service.assignments.blocked(source):
        return 'The conversation assignment is blocked.' if explain else None
    task = service.coordination.task(source['id'])
    # A completed task is retained history. Fresh work in the same conversation
    # can have a new, valid child execution without reopening that record.
    if task and (task.get('status') == 'paused'
                 or task.get('status') == 'blocked' and not dependency_blocked(task)):
        return 'The assigned task is paused or blocked without a dependency wait.' if explain else None
    # This observation is an additional rejection fence. Its asynchronous
    # publication is not the producer: only the authenticated live worker can
    # attest its exact run before the first lifecycle row reaches the host.
    children = source.get('execution', {}).get('nodes', [])
    child = next((row for row in reversed(children) if row.get('kind') == 'worker'
                  and row.get('sessionId') == identity), None)
    if (child and child.get('runId') == origin['runId']
            and child.get('phase') in {'completed', 'cancelled', 'error', 'interrupted'}):
        return 'The child run has already finished or failed.' if explain else None
    return origin


def current(service, source, *, child_id=None, explain=False):
    origin = live_assignment(service, source, child_id=child_id, explain=explain)
    if explain and isinstance(origin, str):
        return None, origin
    if origin is None or source.get('status') == 'error':
        reason = ('The conversation is in an error state.' if source.get('status') == 'error'
                  else 'The child execution is no longer current.')
        return (None, reason) if explain else None
    if source.get('configurationBusy'):
        from .handoff_admission import finishing_turn
        call = {'rootSessionId': origin['rootSessionId'],
                'sessionId': origin['runtimeSessionId'], 'admissionInputId': origin['inputIds'][0]}
        if not finishing_turn(service, source, call, child=(BINDING.get() or {}).get('_nativeChild')):
            return (None, 'The child is not authorized to finish the active configuration handoff.') if explain else None
    from .management import Management
    if Management.unresolved_configuration(source):
        return (None, 'Conversation configuration is unresolved.') if explain else None
    return (origin, None) if explain else origin


def generation(service, source):
    origin = current(service, source)
    return ({'id': origin['generationId'], 'inputIds': list(origin['inputIds']),
             'interruptionRevision': origin['interruptionRevision'], 'terminal': False}
            if origin else source.get('collaborationGeneration') or {})
