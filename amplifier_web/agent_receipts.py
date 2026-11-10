"""Compact bundled-tool receipts without changing public dispatch defaults."""
from __future__ import annotations


def domain_receipt(result, *, action, session_id, revision):
    """Add the established compact domain context without reading app state."""
    access = {
        'note': 'The domain receipt preserves admission and the full action result when available. A pending operation is not completion. Use get_state with a JSON Pointer for saved results or other state.',
    }
    if action.startswith('permissions.') and result.get('operationId'):
        access['resultPaths'] = ['/permissions', '/managementResults/' + result['operationId']]
    return {**result, 'state': {
        'revision': revision, 'sessionId': session_id, '_stateAccess': access}}


def compact_receipt(result, *, action, session_id, revision, client_id=None, command_id=None):
    """Keep domain evidence intact; omit the unrelated application overview.

    Admission is not completion. Readbacks are requests for current state, not
    claims that an asynchronous effect has finished or a browser has rendered.
    The host applies its normal caller/client scoping when they are read.
    """
    paths = []
    operation_id = result.get('operationId')
    management_action = action.startswith(('permissions.', 'providers.', 'configuration.',
                                          'bundles.', 'modules.', 'sources.', 'locations.'))
    if management_action and isinstance(operation_id, str) and operation_id:
        pointer = operation_id.replace('~', '~0').replace('/', '~1')
        paths.append('/managementResults/' + pointer)
    if action == 'settings.connection.select':
        domain = result.get('result')
        check = domain.get('credentialCheck') if isinstance(domain, dict) else None
        check_id = check.get('operationId') if isinstance(check, dict) else None
        if isinstance(check_id, str) and check_id:
            paths.append('/managementResults/' + check_id.replace('~', '~0').replace('/', '~1'))
    if action == 'view.update' or action.startswith('settings.'):
        paths.append('/view')
    elif action.startswith('canvas.'):
        paths.append('/canvas')
    elif action.startswith('call.'):
        paths.append('/voice')
    elif action.startswith('permissions.'):
        paths.append('/permissions')

    access = {
        'note': 'Admission and the exact action result are preserved. Pending or accepted does not prove completion or rendering. Read only the relevant state if further verification is needed; do not replay an uncertain action.',
        'readback': [
            {'operation': 'get_state', 'args': {'path': path,
                **({'clientId': client_id} if client_id is not None else {})}}
            for path in dict.fromkeys(paths)
        ],
    }
    context = {'revision': revision, 'sessionId': session_id, '_stateAccess': access}
    if client_id is not None:
        context['clientId'] = client_id
    receipt = {**result, 'state': context}
    if action.startswith('smartTools.') and operation_id:
        receipt['read'] = {'action': 'smartTools.readResult', 'args': {'operationId': operation_id}}
    # This is the existing agent bridge's effect summary, not the browser's
    # independently delivered effect payload or a replacement for result data.
    if 'effects' in result:
        receipt['effects'] = [{'id': item.get('id'), 'type': item.get('type')}
                              for item in result['effects']]
    if command_id is not None:
        receipt.setdefault('commandId', command_id)
    return receipt
