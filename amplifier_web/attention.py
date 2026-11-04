"""Shared, acknowledgeable attention state derived from currently actionable facts."""
import hashlib
import json
from collections import Counter


def connection_failure_key(operation):
    """Group host pre-dispatch refusals, never tool results or uncertain work.

    Older receipts lack a reason code. Their exact host-generated exception
    and absence of a result identify the same pre-transport refusal. This is
    display grouping only: no receipt is edited, retried or reclassified.
    """
    from .smart_tool_lifecycle import CONNECTION_UNAVAILABLE
    if (operation.get('status') != 'failed' or operation.get('origin') != 'app'
            or operation.get('action') != 'smartTools.call'
            or operation.get('result') is not None):
        return None
    typed = (operation.get('failureReason') == 'connection_unavailable'
             and operation.get('requestState') == 'not_sent')
    legacy = not operation.get('failureReason') and operation.get('error') == CONNECTION_UNAVAILABLE
    server = operation.get('target', {}).get('id')
    configuration = operation.get('configuration')
    return (server, configuration) if server and configuration and (typed or legacy) else None


def make_item(read, key, title, section, page, detail='', version=None, previous_titles=(), **target):
    fingerprints = [hashlib.sha256(json.dumps([key, label, detail, version], sort_keys=True,
                    default=str).encode()).hexdigest()[:24] for label in (title, *previous_titles)]
    return {'id': key, 'title': title, 'detail': detail, 'section': section, 'page': page,
            'fingerprint': fingerprints[0], 'read': read.get(key) in fingerprints, **target}


def session_item_count(session):
    """Addressable slots, including empty ones: pagination never rescans a prefix."""
    return len(session.get('questions', [])) + 3 + len(session.get('approvals', []))


def session_item_at(session, read, position):
    """Canonical session notice used by both the web projection and HTTP readers."""
    questions = session.get('questions', [])
    target = {'sessionId': session['id'], 'workspace': session.get('workspace')}
    def item(key, title, detail='', version=None, **extra):
        return make_item(read, key, title, 'chats', 'chats', detail, version, **target, **extra)
    if position < len(questions):
        question = questions[position]
        if question['status'] == 'pending':
            return item('question:' + question['id'], 'Answer requested' if question['required'] else 'Optional question',
                        question['prompt'], question['revision'])
        if (question.get('delivery') or {}).get('status') in {'unknown', 'rejected'}:
            return item('question:' + question['id'], 'Answer saved; delivery needs attention',
                        question['prompt'], question['delivery'])
        return None
    position -= len(questions)
    if position == 0:
        if session.get('manualUnread'):
            return item('unread:' + session['id'], 'Marked unread', version=session['manualUnread'],
                        visit=session['manualUnread'], label=session.get('title', 'Conversation'))
    elif position == 1:
        completion = session.get('completion')
        if completion:
            return item('completion:' + session['id'], 'Response ready', version=completion['id'],
                        label=session.get('title', 'Conversation'), completedAt=completion.get('at'))
    elif position == 2:
        if session.get('error'):
            return item('session:' + session['id'], 'Conversation needs attention', session['error'],
                        version=session.get('errorAt'))
    else:
        approval = session.get('approvals', [])[position - 3]
        if approval.get('status') in {None, 'pending'}:
            return item('approval:' + approval['id'], 'Approval requested · ' + session.get('title', 'Conversation'),
                        approval.get('title') or approval.get('tool', ''))
    return None


def snapshot(state, *, session_items=None):
    items=[]
    read=state.get('attentionRead',{})
    def add(key,title,section,page,detail='',version=None,previous_titles=(),**target):
        items.append(make_item(read, key, title, section, page, detail, version, previous_titles, **target))
    for receipt in state.get('feedback',{}).get('requests',[]):
        status=receipt.get('status')
        if status in {'submitted','failed','unknown'}:
            add('feedback:'+receipt['requestId'],{'submitted':'Feedback sent','failed':'Feedback could not be sent','unknown':'Check feedback delivery'}[status],
                'feedback','feedback',receipt.get('message',''),status,requestId=receipt['requestId'],label=receipt.get('title','Feedback'),url=receipt.get('url'),status=status)
            items[-1]['informational'] = status == 'submitted'
    for provider in state.get('setup',{}).get('providers',[]):
        if provider.get('enabled',True) and provider.get('authenticationRequired'):
            add('provider-auth:'+provider['id'],'AI connection needs sign-in','setup','ai-connections',
                'Automatic credential renewal failed. Open this connection to sign in again.',provider['id'],providerId=provider['id'])
            items[-1].update(read=False, reviewable=False)  # Actionable until this account recovers.
    updates=state.get('updates',{})
    from .release_notes import notice_id, version
    try:
        notice_start = version(state.get('releaseNoticeStartVersion'))
    except ValueError:
        notice_start = None  # Legacy state keeps all existing notices.
    for release in updates.get('application',{}).get('releaseNotes',[]):
        if notice_start is not None and version(release['version']) < notice_start:
            continue  # Historical notes remain available in the changelog.
        for notice in release.get('notices',[]):
            add(notice_id(release,notice),notice['title'],'maintenance','updates',
                notice['detail']+' '+notice['action'],release['version'],
                previous_titles=notice.get('previousTitles',()),releaseVersion=release['version'])
            items[-1]['informational'] = True
    automatic = state.get('settings', {}).get('updates', {}).get('autoInstall', True) or updates.get('sequence', {}).get('install')
    preparing = automatic and not updates.get('error') and updates.get('phase') not in {'error', 'interrupted'}
    for row in updates.get('items', []):
        if row.get('status') == 'update' and preparing and (row.get('eligible', True) and not (row.get('kind') == 'app' and row.get('canInstall') is False)):
            continue  # Routine automatic work is progress, not an unread task.
        if row.get('status') in {'update','check_failed','local_changes'}:
            status=row['status'];label={'update':'Update available','check_failed':'Could not check','local_changes':'Local edits preserved'}[status]
            add('source:'+row['id'],label+' · '+row['label'],'maintenance','updates',row.get('detail',''),[status,row.get('latest'),row.get('current')])
    failures = updates.get('diagnostics', {}).get('activeFailures', {})
    overflow = updates.get('diagnostics', {}).get('failureOverflow')
    from .update_diagnostics import disposition_occurrence
    if overflow:
        add('updates:failure-overflow', 'Additional update failures need review', 'maintenance', 'updates',
            'More distinct failures occurred than the active summary can retain. Review the bounded update diagnostic history; recovery of these additional failures is unverified.' +
            (' Some original receipts could not be stored.' if updates.get('diagnostics', {}).get('storageUnavailable') else ''),
            overflow.get('firstAt'), actionRequired=True, occurrences=overflow['occurrences'],
            dispositionOccurrence=disposition_occurrence('updates:failure-overflow', overflow))
    for key, failure in sorted(failures.items(), key=lambda pair: pair[1]['first'].get('at', 0), reverse=True):
        kind = failure['kind']
        event = failure['first']
        label = {'ecosystem': 'Component update', 'application': 'Application update',
                 'smart-tools': 'Smart Tool update'}.get(kind, 'Update')
        detail = event.get('reason') or event.get('probe', {}).get('reason') or event.get('errorType') or 'Update did not finish'
        if event.get('component'):
            detail += ' · ' + event['component']
        if kind == 'ecosystem' and event.get('phase') != 'ecosystem-activation':
            detail += '. Current components remain active.'
        detail += ' Open Updates to review details and recovery options.'
        add('updates:failure:' + key, label + ' needs attention', 'maintenance', 'updates',
            detail, failure['id'], actionRequired=True, updateKind=kind,
            occurrences=failure['occurrences'], firstFailure=event, latestFailure=failure['latest'])
        token = disposition_occurrence(items[-1]['id'], failure)
        if token:
            items[-1]['dispositionOccurrence'] = token
    if updates.get('error') and not any(group.get('displayError') == updates['error'] for group in failures.values()):
        add('updates:error','Update needs attention','maintenance','updates',updates['error'],actionRequired=True)
        items.insert(0, items.pop())  # A distinct current check error precedes older incidents.
    if not preparing and (updates.get('pendingApp') or updates.get('pendingRelease')):add('updates:pending','Update ready; waiting for idle','maintenance','updates',version=updates.get('pendingRelease') or updates.get('pendingApp'))
    routes={'providers':('setup','providers'),'routing':('setup','routing'),'configuration':('capabilities','loaded-modules'),'bundle':('capabilities','add-bundles'),'bundles':('capabilities','app-bundles'),'modules':('capabilities','registries'),'sources':('capabilities','registries'),'history':('maintenance','history'),'permissions':('maintenance','permissions'),'notifications':('maintenance','notifications'),'maintenance':('maintenance','repair')}
    for action,op in state.get('actionStatus',{}).items():
        if op.get('phase') not in {'error','failed'} or not op.get('error'):continue
        section,page=routes.get(action.split('.')[0],('maintenance','repair'))
        target = op.get('target', {}).get('sessionId') if action.split('.')[0] in {'configuration', 'runtime', 'conversation', 'session'} else None
        add('action:'+action,'Action needs attention','chats' if target else section,'chats' if target else page,op['error'],op.get('commandId'),**({'sessionId':target} if target else {}))
    connection_groups = {}
    for op in state.get('smartTools',{}).get('operations',[]):
        if op.get('status') in {'failed','interrupted'}:
            group = connection_failure_key(op)
            if group:
                connection_groups.setdefault(group, []).append(op)
            else:
                add('smart-tool:'+op['id'],'Smart Tool needs attention','capabilities','smart-tools',op.get('error','The tool did not finish.'),op.get('updatedAt'))
    for (server_id, configuration), operations in connection_groups.items():
        group_id = hashlib.sha256(json.dumps([server_id, configuration]).encode()).hexdigest()[:24]
        count = len(operations)
        add('smart-tool-connection:'+group_id, 'Smart Tool connection needs attention',
            'capabilities', 'smart-tools',
            f'{count} recent request'+('s' if count != 1 else '')+' could not use this connection. Reconnect in Settings; individual records remain in tool activity.',
            sorted((op['id'], op.get('updatedAt')) for op in operations),
            operationIds=[op['id'] for op in operations], serverId=server_id)
        # A projection upgrade must not resurrect already reviewed receipts.
        if all(read.get('smart-tool:'+op['id']) == hashlib.sha256(json.dumps(
                ['smart-tool:'+op['id'], 'Smart Tool needs attention',
                 op.get('error', 'The tool did not finish.'), op.get('updatedAt')],
                sort_keys=True, default=str).encode()).hexdigest()[:24] for op in operations):
            items[-1]['read'] = True
    for dest in state.get('diagnostics',{}).get('destinations',[]):
        if dest.get('error'):add('diagnostics:'+dest['id'],'Context Intelligence delivery needs attention','maintenance','diagnostics',dest['error'].get('type','Delivery failed'))
    local=state.get('diagnostics',{}).get('local',{})
    health = {key: local.get(key, False if key.endswith('Error') else 0) for key in
              ('storageError', 'configurationError', 'dropped', 'outboxDropped', 'expiredPending')}
    if any(health.values()):add('diagnostics:capture','Some diagnostic records could not be retained','maintenance','diagnostics',version=health)
    if state.get('notificationError'):add('notifications:error','Notification delivery failed','maintenance','notifications',state['notificationError'])
    if session_items is None:
        session_items = (item for session in state.get('sessions', [])
                         for position in range(session_item_count(session))
                         if (item := session_item_at(session, read, position)) is not None)
    items.extend(session_items)
    items.sort(key=lambda item: not item.get('actionRequired', False))
    unread=[item for item in items if not item['read']]
    attention=[item for item in items if item.get('actionRequired') or (not item['read'] and not item.get('informational'))]
    # Count once: many saved workspaces and unread outcomes must not produce
    # a workspaces × unread-items scan on every streamed publication.
    workspace_counts = Counter(i.get('workspace') for i in unread if i.get('sessionId'))
    return {'items':items,'unread':len(unread),'badgeUnread':sum(state.get('notificationSettings',{}).get('completionBadge',False) or not i['id'].startswith('completion:') for i in attention),'settingsUnread':sum(i['section'] in {'setup','capabilities','maintenance'} for i in attention),
            'sessions':{i['sessionId']:1 for i in unread if i.get('sessionId')},
            'workspaces':{w['id']:workspace_counts.get(w['path'], 0) for w in state.get('workspaces',[])},
            'sections':{key:sum(i['section']==key or (key=='setup' and i['page']=='loaded-modules') for i in attention) for key in {'setup','capabilities','maintenance','chats','feedback'}},'pages':{key:sum(i['page']==key for i in attention) for key in {i['page'] for i in items}}}


def reviewed_session_errors(attention):
    """Use the current snapshot's exact fingerprint receipts, not saved status.

    Reading an approval, completion or question never acknowledges an error.
    A different occurrence is unread again even if its message is identical.
    """
    return {item['sessionId'] for item in attention.get('items', [])
            if item.get('sessionId') and item.get('read')
            and item.get('id') == 'session:' + item['sessionId']}


def completed(session, generation):
    """Only manager completion with no delegated work outstanding is ready.

    Text blocks, tool results, idle/startup notices and child-session events do
    not establish this boundary. Keep one durable marker per conversation.
    """
    if generation.get('active_job_ids') or not generation.get('text','').strip():
        return
    identity=[generation.get('generation_id'),generation.get('input_ids',[])]
    session['completion']={'id':hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),
                           'at':generation['at']}
