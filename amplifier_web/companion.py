"""Temporary preview adapter. Public clients use the observation resources."""
from .auth import data_identity
from .errors import AppError


def snapshot(service, observations, params):
    if set(params) - {'sessionsCursor', 'itemsCursor', 'clientId'}:
        raise AppError('Use sessionsCursor/itemsCursor or the advertised observation resources.')
    sessions = observations.page('sessions', {'limit': '50', 'activity': 'active-or-pinned',
                                             'cursor': params.get('sessionsCursor')})
    notices = observations.page('attention', {'limit': '200', 'cursor': params.get('itemsCursor')})
    pins = observations.pins()
    items = [{**item, 'sessionId': item['target']['sessionId'],
              'kind': {'session-error': 'session', 'manual-unread': 'unread'}.get(item['kind'], item['kind']),
              'pinned': item['target']['sessionId'] in pins} for item in notices['items']]
    activity = observations.activity()
    if activity['status'] != 'ready':
        raise AppError('Observation counts are being collected; retry without replaying commands.',
                       503, code='observation_pending')
    return {'protocolVersion': 1, 'revision': service._state['revision'],
            'observationRevision': activity['revision'],
            'workingCount': activity['executionCount'], 'attentionCount': activity['attentionCount'],
            'sessions': sessions['items'], 'items': items,
            'sessionsTruncated': sessions['nextCursor'] is not None,
            'itemsTruncated': notices['nextCursor'] is not None,
            'sessionsNextCursor': sessions['nextCursor'], 'itemsNextCursor': notices['nextCursor'],
            'resources': {'sessions': '/api/sessions?view=observation',
                          'attention': '/api/attention', 'activity': '/api/activity'}}


def setup_routes(app):
    service, observations = app['service'], app['observations']
    identity = data_identity(service.data_dir)

    async def observe(request):
        observations.require_client()
        value = snapshot(service, observations, request.query)
        return observations.response(request, {**value, 'hostIdentity': identity,
                                               'hostInstanceId': service.instance_id})

    app.router.add_get('/api/companion', observe)
