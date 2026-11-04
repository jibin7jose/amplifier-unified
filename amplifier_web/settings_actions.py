"""Shared Settings navigation and connection drafts; never configuration writes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import uuid

from .settings_navigation import GLOBAL_PAGES, open_canvas, settings_state

ACTIONS = {'settings.open', 'settings.close', 'settings.connection.services',
           'settings.connection.start', 'settings.connection.select'}
NAVIGATION_ACTIONS = ACTIONS - {'settings.connection.services'}


def services():
    from .decision_settings import describe
    choices = json.loads(Path(__file__).with_name('ai_services.json').read_text())
    known = {row['module'] for row in choices}
    # Optional installed catalogs own their connection metadata. Discovering a
    # service does not save an account or activate a decision model.
    choices.extend(row for row in describe({})['services'] if row['module'] not in known)
    return choices


def definitions(schema, string):
    client = {'clientId': string(100)}
    owner = {**client, 'configurationTarget': {'oneOf': [
        schema({'kind': {'const': 'global'}}),
        schema({'kind': {'const': 'workspace'}, 'workspaceId': string(200)}),
    ]}, 'scope': {'enum': ['global', 'local', 'project']}}
    return {
        'settings.open': ('Open a named global Settings page on the calling browser. Reuses the live Settings surface; does not save configuration.',
            schema({**client, 'page': {'enum': list(GLOBAL_PAGES)}, 'surface': {'enum': ['canvas', 'dialog']}}, [])),
        'settings.close': ('Close visible global Settings on the calling browser; preserve other Canvas tabs and saved settings. Browser private drafts may require the user to confirm closing.', schema(client, [])),
        'settings.connection.services': ('List supported connection choices, not saved or installed providers. No configuration or browser changes.', schema()),
        'settings.connection.start': ('Open Connect AI service choices. Defaults to global Settings; explicit workspace configurationTarget opens that workspace’s settings. No connection is saved.', schema(owner, [])),
        'settings.connection.select': ('Choose a supported service in Connect AI and check existing credentials for the same explicit configuration owner. Returns a draft and pending read-only credential check, never a saved connection. Keys must be entered privately.',
            schema({**owner, 'module': string(100)}, ['module'])),
    }


def _open(service, page='ai-connections', surface=None):
    from .service import AppError
    state = service.state
    current = settings_state(state)['surface']
    surface = surface or (current if current != 'closed' else 'canvas' if state.get('selectedSessionId') else 'dialog')
    if surface == 'canvas':
        if not state.get('selectedSessionId'):
            raise AppError('Start a chat before opening Settings in Canvas.', 409)
        service.canvas_views.guard_transition('canvas.show', {})
        open_canvas(state, service.db)
        # Canvas is rendered on the chat surface. Browsing a workspace must not
        # leave the Settings receipt claiming an open but hidden editor.
        state['view'].update(panel=None, settingsCanvas=True, canvasDraft={}, workSurface='chat')
    else:
        state['view'].update(panel='settings', settingsCanvas=False)
    state['view']['settingsExpanded'] = [page]


def command(service, action, args, pending, command_id):
    """Called under dispatch's lock, after authorization and command replay."""
    from .service import AppError
    state, view = service.state, service.state['view']
    if action == 'settings.connection.services':
        return {'services': services(), 'kind': 'supported-services', 'savedConnections': False}
    if action == 'settings.open':
        _open(service, args.get('page', 'ai-connections'), args.get('surface'))
    elif action == 'settings.close':
        before = settings_state(state)['surface']
        canvas = state.get('canvas', {})
        close_canvas = canvas.get('kind') == 'settings' and canvas.get('open') and not view.get('canvasDraft', {}).get('library')
        if close_canvas:
            # Refuse the whole transition before changing a dialog or draft.
            service.canvas_views.guard_transition('canvas.tabClose', {'id': canvas['id']})
        if view.get('panel') in {'settings', 'appearance'}:
            view['panel'] = None
        if close_canvas:
            from .canvas_library import command as canvas_command
            canvas_command(state, service.db, 'canvas.tabClose', {'id': canvas['id']})
        view['settingsCanvas'] = False
        return {'surface': settings_state(state)['surface'], 'changed': before != 'closed',
                'browserConfirmation': 'Private unsaved drafts may keep Settings visible until the user decides.'}
    else:
        target = copy.deepcopy(args.get('configurationTarget', {'kind': 'global'}))
        scope = args.get('scope', 'global' if target['kind'] == 'global' else 'local')
        target_args = {'configurationTarget': target, 'scope': scope}
        from .configuration_target import resolve
        try:
            resolve(service, target_args)
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
        selected = next((row for row in services() if row['module'] == args.get('module')), None)
        if action == 'settings.connection.select' and not selected:
            raise AppError('Choose a supported service from settings.connection.services.')
        if selected and selected['auth'] != 'signin' and service.management is None:
            raise AppError('Connection checks are unavailable on this host.', 409)
        # Resolve and validate every owner/module before changing any draft.
        if target['kind'] == 'global':
            _open(service)
            drafts = view
        else:
            view.update(panel=None, settingsCanvas=False,
                        workSurface='workspace', workWorkspaceId=target['workspaceId'],
                        workWorkspaceTab='details', workWorkspaceSettings='ai-connections')
            drafts = view.setdefault('configurationDrafts', {}).setdefault('workspace:' + target['workspaceId'], {})
        if selected is None:
            draft = {**drafts.get('aiConnectionEditor', {}), 'step': 'services', 'scope': scope}
        else:
            draft = {'step': 'connect', 'settingDefault': False, 'module': selected['module'],
                'id': selected['module'] + '-' + str(uuid.uuid4()), 'model': '', 'baseUrl': '',
                'previousLoginId': None, 'scope': scope, 'authMode': 'chatgpt_codex',
                'saved': False, 'credentialMode': 'auto', 'initializeRouting': False}
            # A scoped provider list is required before suggesting initial routing.
            setup = state.get('setup', {})
            if (not selected.get('decisionOnly') and setup.get('configurationTarget') == target
                    and setup.get('providersLoadedAt')):
                draft['initializeRouting'] = not any(p.get('enabled') is not False for p in setup.get('providers', []))
        drafts['aiConnectionEditor'] = draft
        result = {'configurationTarget': target, 'scope': scope, 'step': draft['step'],
                  'editor': {key: draft[key] for key in ('id', 'module') if key in draft}, 'saved': False}
        if selected and selected['auth'] != 'signin':
            operation_id = (command_id or str(uuid.uuid4())) + ':credentials'
            pending.append((service.management.command, ('providers.credentials',
                {**target_args, 'module': selected['module']}, operation_id, None)))
            result['credentialCheck'] = {'action': 'providers.credentials', 'operationId': operation_id, 'status': 'pending'}
        return result
    actual = settings_state(state)
    return {'surface': actual['surface'], 'page': actual['page'], 'configurationTarget': {'kind': 'global'}}
