"""Discoverable presentation contributions over the existing shell protocol.

The native profile is trusted code. These are SDK data/action contracts, not
an isolation boundary. Session and canvas ownership stay with the host.
"""
from __future__ import annotations

import copy

PROFILE = 'trusted-native-component-v1'
CAPABILITIES = ['shell.read', 'conversation.summary', 'canvas.summary', 'attention.summary',
                'panels.open', 'presentation.update', 'conversation.manage']
SLOTS = {
    'app.actions': {'label': 'App actions', 'placement': 'Built-ins first; additional controls use an overflow group on narrow screens', 'maximum': 8, 'default': 'builtin.app-actions'},
    'app.status': {'label': 'App status', 'placement': 'Built-ins first; additional status uses an overflow group on narrow screens', 'maximum': 8, 'default': 'builtin.app-status'},
    'conversation.header': {'label': 'Conversation header', 'maximum': 1, 'default': 'builtin.conversation-header'},
    'composer.actions': {'label': 'Composer actions', 'maximum': 8, 'default': 'builtin.composer-actions'},
    'canvas.toolbar': {'label': 'Canvas toolbar', 'maximum': 8, 'default': 'builtin.canvas-toolbar'},
    'settings.appearance': {'label': 'Appearance settings', 'maximum': 1, 'default': 'builtin.settings-appearance'},
    'settings.section': {'label': 'Additional settings sections', 'maximum': 12, 'default': None},
}
BUILTINS = {slot['default']: {'id': slot['default'], 'label': slot['label'], 'version': '1.0.0',
    'apiVersion': '1.0', 'profile': PROFILE, 'stateSchema': 'shell-component-v1',
    'slots': [name], 'capabilities': CAPABILITIES}
    for name, slot in SLOTS.items() if slot['default']}


def resolved(composition):
    """Old navigation compositions inherit new built-ins without being rewritten."""
    instances = copy.deepcopy(composition['instances'])
    used = {item['slot'] for item in instances} | set(composition.get('disabledSlots', []))
    for name, slot in SLOTS.items():
        if name not in used and slot['default']:
            instances.append({'id': 'core.' + name, 'slot': name, 'package': slot['default']})
    return instances


def snapshot(shell, client, instance):
    service = shell.service
    state = service.state
    manifest = shell.manifest(instance['package'], validated=False)
    caps = set(manifest['capabilities'])
    session = next((row for row in state['sessions'] if row['id'] == state.get('selectedSessionId')), None)
    composition = client['preview']['composition'] if client.get('preview') else client['composition']
    result = {'revision': state['revision'], 'compositionRevision': client['revision'], 'slot': instance['slot'],
              'view': copy.deepcopy(client['views'].get(instance['id'], {}).get('view', {}))}
    if 'shell.read' in caps:
        result['presentation'] = copy.deepcopy(composition['presentation'])
        result['runtime'] = {'available': bool(state.get('runtime', {}).get('available'))}
        result['selectedWorkspaceId'] = state.get('selectedWorkspaceId')
        result['selectedSessionId'] = state.get('selectedSessionId')
    if 'conversation.summary' in caps:
        result['conversation'] = ({key: copy.deepcopy(session[key]) for key in
            ('id', 'title', 'status', 'autoName', 'bundle', 'workspaceId', 'naming') if key in session} if session else None)
    if 'canvas.summary' in caps:
        canvas = state.get('canvas', {})
        result['canvas'] = {key: copy.deepcopy(canvas[key]) for key in ('id', 'kind', 'title', 'open') if key in canvas}
    if 'attention.summary' in caps:
        counts = service.projections.attention(state, session_ids=())
        result['attention'] = {key: copy.deepcopy(counts[key]) for key in ('total', 'unread', 'badgeUnread', 'sections') if key in counts}
    return result


def commands():
    from .service import ACTION_DEFINITIONS, schema
    from .shell_modules import COMPOSITION
    result = {name: {'capability': 'conversation.manage', 'description': ACTION_DEFINITIONS[name][0],
                     'schema': copy.deepcopy(ACTION_DEFINITIONS[name][1])}
              for name in ('session.naming', 'session.rename', 'conversation.stop')}
    result['panel.open'] = {'capability': 'panels.open', 'description': 'Open a supported panel, optionally a contributed settings section.',
        'schema': schema({'panel': {'enum': ['settings', 'activity', 'session-details', 'runtime']},
                          'section': {'type': 'string', 'maxLength': 100}}, ['panel'])}
    result['presentation.update'] = {'capability': 'presentation.update', 'description': 'Apply a presentation patch at the observed composition revision.',
        'schema': schema({'expectedRevision': {'type': 'integer', 'minimum': 0},
                          'patch': copy.deepcopy(COMPOSITION['properties']['presentation'])})}
    return result


async def command(shell, client, instance, args, origin, command_id):
    """Small semantic operations; raw view patches never escape module state."""
    from .errors import AppError
    from jsonschema import validate, ValidationError
    from .shell_modules import COMPOSITION
    action, values = args['action'], args.get('args', {})
    caps = shell.manifest(instance['package'])['capabilities']
    contract = commands().get(action)
    required = contract['capability'] if contract else None
    if not required or required not in caps:
        raise AppError('Module has not declared this capability.', 403)
    try:
        validate(values, contract['schema'])
    except ValidationError as exc:
        raise AppError('Invalid component command: ' + exc.message)
    if action == 'presentation.update':
        if set(values) != {'patch', 'expectedRevision'} or values['expectedRevision'] != client['revision']:
            raise AppError('Inspect the current composition revision before updating presentation.', 409)
        try:
            validate(values['patch'], COMPOSITION['properties']['presentation'])
        except ValidationError as exc:
            raise AppError('Invalid presentation patch: ' + exc.message)
        composition = client['preview']['composition'] if client.get('preview') else client['composition']
        composition = {**composition, 'presentation': {**composition['presentation'], **values['patch']}}
        target = {'clientId': args['clientId'], 'expectedRevision': client['revision']}
        prepared = await shell.dispatch('shell.changes.prepare', {**target, 'composition': composition}, origin, None)
        return await shell.dispatch('shell.changes.apply', {**target, 'changeId': prepared['result']['id']}, origin, None)
    if action == 'panel.open':
        patch = {'panel': values['panel']}
        if 'section' in values:
            composition = client['preview']['composition'] if client.get('preview') else client['composition']
            if values['panel'] != 'settings' or not any(row['id'] == values['section'] and row['slot'] == 'settings.section' for row in resolved(composition)):
                raise AppError('This settings section is unavailable.', 409)
            patch.update(settingsSection='setup', settingsExpanded=['shell:' + values['section']])
        return await shell.service.dispatch('view.update', {'patch': patch}, origin=origin, command_id=command_id, include_state=False)
    field = 'sessionId' if action == 'conversation.stop' else 'id'
    if not values.get(field) or values[field] != shell.service.state.get('selectedSessionId'):
        raise AppError('The selected conversation changed. Read the component snapshot again.', 409)
    return await shell.service.dispatch(action, values, origin=origin, command_id=command_id, include_state=False)
