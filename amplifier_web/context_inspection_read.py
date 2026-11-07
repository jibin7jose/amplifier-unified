"""Authorized passive reads of the optional native request observation."""
import asyncio

from .context_inspection_store import InspectionStore, preferences


def definitions(schema, string):
    return {'context.inspect': (
        'Inspect one retained host-final model request, not current source or full history. '
        'Read-only: never starts an agent, counts tokens, or enables recording. '
        'Omit callId for the last completed foreground call. Item content is paged and subject to current recording consent.',
        schema({'sessionId': string(200), 'callId': string(100), 'itemId': string(20),
                'offset': {'type': 'integer', 'minimum': 0, 'maximum': 120000}}, ['sessionId']))}


async def read(service, args):
    from .session_files import capture_dir
    from .errors import AppError
    session = service._session(args['sessionId'], hydrate=False)
    identity = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
    workspace = session.get('workspace')
    if not workspace:
        return {'version': 1, 'status': 'unavailable', 'reason': 'This agent has no native request observation capability.'}
    binding = (identity, workspace)
    policy = preferences(service.data_dir)
    if not policy['enabled']:
        return {'version': 1, 'status': 'unavailable', 'reason': 'Diagnostic capture is disabled.'}
    if args.get('itemId') is not None and not args.get('callId'):
        raise AppError('Select an exact request before reading an item.')
    store = InspectionStore(capture_dir(workspace, identity))
    try:
        result = await asyncio.to_thread(store.read, call_id=args.get('callId'), item_id=args.get('itemId'),
            offset=args.get('offset', 0), bodies=policy['bodies'], days=policy['days'])
    except (ValueError, OSError, KeyError, TypeError):
        raise AppError('This request observation is unavailable or changed. Refresh the inspector.', 409) from None
    current = service._session(args['sessionId'], hydrate=False)
    current_binding = (current.get('runtimeSessionId') or current.get('nativeIdentity') or current['id'], current.get('workspace'))
    if current_binding != binding or preferences(service.data_dir) != policy:
        raise AppError('Request access changed during the read. Refresh the inspector.', 409)
    return {'sessionId': session['id'], **result}
