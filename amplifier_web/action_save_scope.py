"""Persistence ownership for ordinary app commands.

This is a write contract, not a heuristic based on the action's prefix. Commands
that can add/remove sessions or affect several owners retain full reconciliation
until their own affected identities are known. Independent domain controllers
use the same _publish_changes boundary directly.
"""

SESSION_ACTIONS = {
    'session.markUnread', 'session.rename', 'session.naming', 'session.inspect', 'session.warm',
    'session.history', 'session.takeover', 'conversation.send', 'conversation.configure',
    'conversation.delivery', 'conversation.retry', 'conversation.stop',
    'worker.spawn', 'worker.stop', 'worker.steer', 'worker.message',
    'approval.respond', 'attachment.add', 'attachment.remove',
    'question.create', 'question.answer', 'question.cancel', 'question.supersede',
    'message.reply', 'message.replyClear', 'message.reaction', 'message.reveal',
    'configuration.operation.reconcile', 'configuration.operation.resume',
}
GLOBAL_ACTIONS = {
    'view.update': set(),
    'session.archive': {'conversationOrganization'},
    'session.restore': {'conversationOrganization'},
    'session.pinOrder': {'pinnedSessionIds', 'pinnedItemIds', 'pinOrderCustomized'},
    'navigation.pinOrder': {'pinnedSessionIds', 'pinnedWorkspaceIds', 'pinnedItemIds', 'pinOrderCustomized'},
    'attention.read': {'attentionRead'},
    'settings.update': {'settings', 'workspaceDefaults', 'bundleDefaults'},
    'bundle.default': {'settings', 'bundleDefaults'},
    'theme.save': set(), 'theme.import': set(), 'theme.update': set(), 'theme.delete': set(),
    'theme.apply': {'theme'}, 'theme.preview': set(),
    'theme.revert': {'theme'}, 'theme.reset': {'theme'},
    'notification.request': set(),
    'runtime.retention.update': {'runtime'},
    'message.copy': set(), 'message.copyResult': set(),
    'state.export': set(), 'theme.export': set(),
    'session.export': {'conversationExports'},
    'history.refresh': {'sharedHistory'},
    'session.exportDeliver': {'conversationExports'}, 'session.exportResult': {'conversationExports'},
    'call.mute': {'voice'}, 'call.keepAwake': {'voice'},
    'call.end': {'voice'}, 'call.resumeAudio': {'voice'},
}
# Common command bookkeeping and passive Canvas presentation saved by dispatch.
COMMON = {'events', 'deviceCommands', 'view', 'canvas', 'canvasArtifacts'}


def scope(service, action, args, *, previous_session=None):
    if action in SESSION_ACTIONS:
        if action.startswith('question.'):
            service.questions.sync({args['sessionId']})
        identity = (args.get('id') if action.startswith('session.') else args.get('sessionId')) or service.state.get('selectedSessionId')
        return {identity} if identity else set(), set(COMMON)
    if action in GLOBAL_ACTIONS:
        sessions = {args['id']} if action in {'session.archive', 'session.restore'} else set()
        if action == 'view.update' and 'draft' in args['patch'] and service.clients.record() is None:
            identity = args.get('sessionId') or service.state.get('selectedSessionId')
            if identity: sessions.add(identity)
        return sessions, COMMON | GLOBAL_ACTIONS[action]
    if action in {'session.draft', 'session.select'} or action.startswith('workspace.') and action != 'workspace.remove':
        ids = {previous_session, service.state.get('selectedSessionId')}
        ids.discard(None)
        return ids, COMMON | {'canvasTabs', 'selectedSessionId', 'selectedWorkspaceId',
            'workspaces', 'workspaceStarters', 'settings', 'hiddenNativeWorkspaces'}
    if action.startswith('session.share'):
        return set(), set(COMMON)
    if action.startswith('diagnostics.'):
        return set(), COMMON | {'diagnostics'}
    if action.startswith(('bundle.', 'bundles.', 'configuration.', 'runtime.',
                          'permissions.', 'history.', 'maintenance.', 'notifications.',
                          'providers.', 'routing.', 'modules.', 'sources.', 'locations.')):
        # This dispatch branch only queues work. The management controller owns
        # the later result and publishes its declared owners separately.
        return set(), set(COMMON)
    if action.startswith('updates.'):
        return set(), COMMON | {'updates'}
    if action.startswith('feedback.'):
        return set(), COMMON | {'feedback'}
    if action in {'settings.open', 'settings.close', 'settings.connection.start', 'settings.connection.select', 'settings.connection.services'}:
        return set(), COMMON | {'canvasTabs'}
    if action.startswith('canvas.'):
        return set(), COMMON | {'canvasTabs'}
    if action.startswith('smartTools.'):

        return set(), COMMON | {'smartTools'}
    if action == 'call.start':
        identity = args.get('sessionId') or service.state.get('selectedSessionId')
        return {identity} if identity else set(), COMMON | {'voice'}
    return None
