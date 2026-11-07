"""Work assurance policy, never ordinary tool permission grants.

The authenticated worker supplies its single-use originating-call witness. This
module does not mint, consume or reconstruct that witness from model arguments.
Full accepts incomplete Work assurance; native scope and effect owners still
govern execution. A check result is not a lease across an await.
"""
from __future__ import annotations

import hashlib
import json
import copy
from contextvars import ContextVar

from .collaboration import BINDING
from .child_execution import current as child_execution, generation as execution_generation

APP_EFFECT = ContextVar('workspace_work_app_effect', default=None)


def _full_access(service, source):
    """Reuse standing policy and the actual native owner/descendant boundary."""
    if not service.access_mode.full(source):
        return False
    service.collaboration.active_binding(source, allow_descendant=True)
    return True


# Explicit recovery surface, including attributed reports and own-request
# cancellation. Reporting is not a read, but must remain possible when stuck.
# None of these reports publishes, verifies a result or grants execution.
# Do not infer read-only behavior from action suffixes
# or ACTION_POLICIES (which describes ownership, not effect safety).
SAFE_ACTIONS = frozenset({
    'workspace.work.list', 'workspace.work.read', 'workspace.work.source',
    'workspace.work.history', 'workspace.work.context', 'workspace.work.wait',
    'workspace.work.invocation', 'workspace.work.promise',
    'workspace.work.report', 'workspace.work.resource', 'workspace.work.publication',
    'coordination.context', 'coordination.list', 'coordination.read',
    'coordination.result', 'coordination.artifact', 'coordination.review',
    'coordination.awareness', 'coordination.intent.read', 'coordination.diagnostics',
    'coordination.reply', 'coordination.cancel', 'coordination.acknowledge', 'coordination.wait',
    'task.get', 'task.history', 'task.detail', 'task.receipt',
    'operations.list', 'operations.read', 'operations.wait',
    'workspace.tests.read', 'workspace.tests.result', 'workspace.tests.cancel',
    'workspace.environments.read', 'workspace.environments.reconcile',
    'question.list', 'question.read', 'capacity.read',
    'outputs.list', 'outputs.read', 'outputs.image', 'outputs.images',
    'coordination.recap', 'workspace.resources.list', 'workspace.resources.read',
    'workspace.resources.managed', 'session.inspect', 'access.get',
    'schedule.list', 'schedule.read', 'schedule.overview', 'session.handoff.read',
    'worktree.inspect', 'worktree.status',
    'memory.status', 'memory.list', 'memory.read', 'memory.source',
    'recall.status', 'recall.search', 'recall.read',
    'permissions.get', 'runtime.dependencies', 'canvas.versions.inspect',
    'history.list', 'history.shared.list', 'history.shared.view',
    'assignment.brief', 'assignment.read', 'assignment.tasks.list', 'assignment.tasks.read',
    'assignment.tasks.wait', 'workspace.environments.removal.read',
    'workspace.environments.removal.list', 'settings.connection.services',
})
SAFE_TOOLS = frozenset({'read_file', 'grep', 'glob'})


def recovery_read(tool_name, tool_input):
    if tool_name in SAFE_TOOLS:
        return True
    if tool_name != 'app_control':
        return False
    operation = tool_input.get('operation')
    if operation in {'get_state', 'list_actions', 'history', 'context.read'}:
        return True
    arguments = tool_input.get('args')
    if (operation == 'dispatch' and isinstance(arguments, dict)
            and arguments.get('action') == 'runtime.control'):
        values = arguments.get('args')
        return isinstance(values, dict) and values.get('operation') in {
            'task.get', 'task.history', 'task.detail', 'task.receipt'}
    return operation == 'dispatch' and isinstance(arguments, dict) and arguments.get('action') in SAFE_ACTIONS


def app_request(operation, arguments, sid):
    """Capture the original AppControl body before aliases/defaults change it."""
    if operation not in {'dispatch', 'action.dispatch'}:
        return None
    witness = (BINDING.get() or {}).get('_workOrigin') or {}
    return {'sessionId': sid, 'toolName': 'app_control',
            'toolInput': {'operation': operation, 'args': copy.deepcopy(arguments)},
            'toolCallId': witness.get('toolCallId') if isinstance(witness, dict) else None}


def guard(service, action, arguments, origin, caller, *, final=False):
    """Apply current Work policy before the ordinary effect owner.

    Full accepts Work assurance risk; Ask requires the qualified owners below.
    Delayed effects retain their own authority, cancellation and receipt checks.
    Reads, reporting and unrelated roots retain their existing domain checks.
    """
    request = APP_EFFECT.get()
    if request is None or origin != 'agent':
        return
    from .errors import AppError
    if caller != request['sessionId']:
        raise AppError('Work effect calling root changed.', 409)
    query = {key: copy.deepcopy(request[key]) for key in ('toolName', 'toolInput', 'toolCallId')}
    if recovery_read(query['toolName'], query['toolInput']):
        # Let the existing read/report owner enforce its exact principal, scope,
        # status code and currentness. Work coverage adds no read authority.
        return
    # Missing origin must deny a bound mutation, not accidentally bypass policy.
    query['toolCallId'] = query['toolCallId'] or 'unavailable-origin'
    result = check(service.work_invocation, caller, query)
    if not result['allowed']:
        raise AppError('Work precondition: ' + result['reason'], 409, code='work_coverage_unavailable')
    if not result['applicable'] or _full_access(service, service._session(caller, hydrate=False)):
        return
    supported = (action in {
        'workspace.work.create', 'workspace.work.revise', 'workspace.work.link',
        'workspace.work.disposition', 'session.pin'}
        or action in {'coordination.send', 'conversation.send'}
        and arguments.get('mode') == 'notify'
        and arguments.get('sessionId') not in (None, caller))
    if not supported:
        raise AppError('This Work-dependent action has no qualified final effect owner. '
                       'Read current Work or report the blocked action; no effect was admitted.',
                       409, code='work_effect_owner_unavailable')


def _receipt(invocation, identity):
    row = invocation.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def applicability(invocation, target):
    """Inspect exact host-retained input/creation owners, never a model Work list."""
    active = execution_generation(invocation.service, target)
    inputs = active.get('inputIds', [])
    if not isinstance(inputs, list) or any(not isinstance(i, str) for i in inputs):
        return True, 'Host delivered-input identity is unavailable.'
    creation_id = (target.get('collaboration') or {}).get('requestId')
    creation = _receipt(invocation, creation_id) if creation_id else None
    if creation_id and (not creation or creation.get('commandAction') != 'coordination.create'
                        or creation.get('sessionId') != target['id']):
        return True, 'Commissioning receipt is unavailable; Work applicability is unknown.'
    if creation and creation.get('workReferences') and (
            creation.get('initialInputId') in inputs or not active.get('id')):
        return True, None
    for identity in inputs:
        row = _receipt(invocation, identity)
        if not row:
            if creation and creation.get('workReferences'):
                return True, 'Delivered input receipt is unavailable; Work applicability is unknown.'
            continue
        if row.get('workAssociationRef'):
            return True, None
        operation_id = row.get('creationOperationId')
        if operation_id:
            operation = _receipt(invocation, operation_id)
            if not operation:
                return True, 'Input creation receipt is unavailable; Work applicability is unknown.'
            if operation.get('workReferences') or operation.get('workAssociation'):
                return True, 'Delivered Work association is missing or incomplete.'
    return False, None


def check(invocation, sid, args):
    """Return a current Work precondition only; no I/O, admission, or permission."""
    from .errors import AppError
    if (set(args) != {'toolName', 'toolInput', 'toolCallId'}
            or not isinstance(args['toolName'], str) or not 1 <= len(args['toolName']) <= 200
            or not isinstance(args['toolInput'], dict)
            or not isinstance(args['toolCallId'], str) or not 1 <= len(args['toolCallId']) <= 200):
        raise AppError('Work effect checks require the exact tool name, input and executing call ID.')
    result = {'applicable': True, 'allowed': False, 'reason': None,
              'requiredVersion': None, 'effectAuthorized': False}
    try:
        # Applicability is a host fact about the root's actual delivered inputs.
        # An ordinary delegated child must still reach its existing permission
        # owner; root-only Work authority is checked only when Work applies.
        target = invocation.service._session(sid, hydrate=False)
        if '_childOrigin' in (BINDING.get() or {}) and child_execution(invocation.service, target) is None:
            from .configuration_recovery import recovery_child
            if recovery_child(invocation.service, target, args) is not None:
                return {**result, 'allowed': True,
                        'reason': 'Current Full child may reach its exact settled configuration recovery owner; no effect was authorized.'}
            return {**result, 'reason': 'The admitted native child execution is no longer current.'}
        applicable, reason = applicability(invocation, target)
        result.update(applicable=applicable, reason=reason)
        if not applicable:
            return {**result, 'allowed': True, 'reason': 'No commissioned Work association in this host input set; ordinary authority still applies.'}
        if recovery_read(args['toolName'], args['toolInput']):
            # Reading grants no Work authority; the actual read tool/domain
            # still enforces the delegated caller's ordinary permissions.
            return {**result, 'allowed': True, 'reason': 'Exact read or safe reporting; ordinary authority still applies.'}
        if _full_access(invocation.service, target):
            # Missing or stale Work observations remain missing or stale. This
            # decision neither resolves all required sources nor certifies that
            # they reached the model. Domains still own facts and exact effects.
            return {**result, 'allowed': True,
                    'reason': 'Current Full access accepts incomplete Work coverage; ordinary effect-owner checks still apply.'}
        target = invocation.bound_root(sid)
        if args['toolName'] == 'tool_exec' and (BINDING.get() or {}).get('_workDispatchGuarded') is True:
            # Only the installed ProgrammaticTool under the worker's active
            # BoundDispatch supplies this fact. Each inner call stays guarded.
            return {**result, 'allowed': True,
                    'reason': 'Host-verified guarded programmatic container; each inner effect must pass its own check.'}
        if reason:
            return result
        required, _ = invocation.required(sid)
        result['requiredVersion'] = required.get('version')
        if required.get('status') != 'available' or not required.get('coverage', {}).get('complete'):
            return {**result, 'reason': required.get('reason') or 'Current Work requirements are unavailable.'}
        witness = (BINDING.get() or {}).get('_workOrigin')
        if not isinstance(witness, dict):
            return {**result, 'reason': 'No authenticated originating-call coverage at this effect boundary.'}
        origin = witness.get('origin')
        active = execution_generation(invocation.service, target)
        exact_inputs = sorted(set(active.get('inputIds', [])))
        if (not isinstance(origin, dict) or origin.get('schemaVersion') != 1
                or origin.get('rootSessionId') != sid
                or origin.get('runtimeSessionId') != (BINDING.get() or {}).get('_runtimeSessionId')
                or origin.get('generationId') != required.get('generationId')
                # This counter is worker-local; the persistent host Stop
                # boundary is independently bound by required.version.
                or type(origin.get('stopRevision')) is not int or origin['stopRevision'] < 0
                or origin.get('inputCoverage') != 'complete'
                or origin.get('inputIds') != exact_inputs):
            return {**result, 'reason': 'Originating call scope no longer matches this root, generation, Stop or complete input set.'}
        encoded = json.dumps([args['toolName'], args['toolInput']], sort_keys=True,
                             ensure_ascii=False, allow_nan=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        if (witness.get('toolCallId') != args['toolCallId']
                or witness.get('toolName') != args['toolName'] or witness.get('argsSha256') != digest
                or witness.get('rootSessionId') != sid or witness.get('generationId') != required['generationId']
                or witness.get('inputIds') != exact_inputs or witness.get('version') != required['version']
                or witness.get('status') != 'available' or witness.get('complete') is not True
                or witness.get('effectAuthorized') is not False
                or witness.get('requirementCount') != required['requirementCount']
                or witness.get('coveredCount') != required['requirementCount']):
            return {**result, 'reason': 'Exact call, arguments or current required-content coverage does not match.'}
        for key in ('admittedCallId', 'attemptId', 'fittedSha256'):
            if not isinstance(origin.get(key), str) or not origin[key]:
                return {**result, 'reason': 'Host admission and fitted-request identity are unavailable.'}
        if (origin['attemptId'] != witness.get('attemptId')
                or origin['fittedSha256'] != witness.get('requestSha256')):
            return {**result, 'reason': 'Admission, request and originating attempt are not joined.'}
        return {**result, 'allowed': True,
                'reason': 'Current Work precondition checked; ordinary effect-owner permissions and admission still apply.'}
    except (AppError, KeyError, TypeError, ValueError):
        return {**result, 'allowed': False, 'reason': 'Work effect precondition is unavailable; no effect was authorized.'}
