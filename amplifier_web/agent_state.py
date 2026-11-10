"""Bounded app-state previews with lossless, revision-aware JSON Pointer paging.

The browser receives bounded navigation pages. Agents can read the full catalog;
responses point to data they omit;
large mount plans and model catalogs must not swamp the model's context.
"""
import json
from itertools import islice


def _pointer(path, key):
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


def _size(value):
    return len(json.dumps(value, ensure_ascii=False))


def _fits_preview(value, budget):
    """Reject oversized values without encoding their omitted history.

    Count a cheap JSON character lower bound first. Only potentially fitting
    values need exact encoding (including escapes, numbers and Unicode).
    """
    def members(mapping):
        # Keep lazy display records lazy: items() may hydrate every field.
        for key in mapping:
            yield key
            yield mapping[key]

    remaining = budget
    stack = [iter((value,))]
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if isinstance(item, str):
            remaining -= len(item) + 2
        elif isinstance(item, (dict, list, tuple)):
            remaining -= 2 + max(0, len(item) - 1) * 2
            if isinstance(item, dict):
                remaining -= len(item) * 2  # Colon and space per member.
                stack.append(iter(members(item)))
            else:
                stack.append(iter(item))
        else:
            remaining -= 1
        if remaining < 0:
            return False
    return _size(value) <= budget


def _preview(value, path, budget=2500, depth=0, *, reference_key='$statePath'):
    if _fits_preview(value, budget):
        return value
    ref = {reference_key:path,'type':type(value).__name__,'size':len(value) if isinstance(value,(dict,list,str)) else None}
    if depth >= 4 or budget < 250:
        return ref
    if isinstance(value, str):
        return {**ref,'preview':value[:max(0,budget-180)]}
    if isinstance(value, list):
        count = min(5,len(value))
        return {**ref,'preview':[_preview(v,_pointer(path,i),max(100,(budget-250)//count),depth+1,reference_key=reference_key) for i,v in enumerate(value[:count])]}
    if isinstance(value, dict):
        keys=list(islice(value, 10))
        return {**ref,'preview':{k:_preview(value[k],_pointer(path,k),max(100,(budget-250)//len(keys)),depth+1,reference_key=reference_key) for k in keys}}
    return ref


def overview(state, session_id, *, positions=None):
    from .session_navigation import is_top_level
    selected = session_id or state.get('selectedSessionId')
    index = next((i for i,s in enumerate(state.get('sessions', [])) if s['id']==selected), None)
    core = {key:_preview(state[key],'/'+key,3000) for key in ['revision','selectedSessionId','selectedWorkspaceId','workspaces','workspaceExplorer','chatNavigation','headerChatNavigation','subagentNavigation','pinnedSessionIds','sharedHistory','canvas','canvasContext','view','attention','voice','computerVisual','inputOrigin'] if key in state}
    core['canvasArtifacts'] = {'items':[{**{k:r.get(k) for k in ('id','title','kind','messageId','tabOpen')},'$statePath':f'/canvasArtifacts/{i}'} for i,r in list(enumerate(state.get('canvasArtifacts',[]))) if r.get('sessionId')==selected][-20:],'total':sum(r.get('sessionId')==selected for r in state.get('canvasArtifacts',[])),'$statePath':'/canvasArtifacts'}
    from .settings_navigation import settings_state
    core['settingsUI'] = settings_state(state)
    core['diagnostics'] = _preview(state.get('diagnostics', {}), '/diagnostics', 1200)
    core['smartTools'] = _preview(state.get('smartTools', {}), '/smartTools', 2000)
    core['session'] = None
    if index is not None:
        session=state['sessions'][index];base=f'/sessions/{(positions or {}).get(selected,index)}'
        core['session']={key:session.get(key) for key in ['id','title','status','bundle','workspace','workspaceId','sessionKind','sessionPurpose','nativeParentId','historyLoaded','historyReadOnlyReason','sharedHistoryOffset','sharedHistoryUserTurnOffset','sharedHistoryTotal','recentActivityAt']}
        core['session']['pinned'] = selected in state.get('pinnedSessionIds', [])
        core['session'].update({'$statePath':base,'activity':_preview(session.get('activity',{}),base+'/activity',1500),
            'workers':_preview(session.get('workers',[]),base+'/workers',1500),
            'questions':_preview(session.get('questions',[]),base+'/questions',2500),
            'recentMessages':[_preview(message,_pointer(base+'/messages',i),1000) for i,message in list(enumerate(session.get('messages',[])))[-6:]],
            'messageCount':len(session.get('messages',[])),
            'model':_preview(state.get('runtimeControl',{}).get(selected,{}).get('configuration.providers',{}).get('effective'),_pointer('/runtimeControl',selected)+'/configuration.providers/effective',1000)})
    if state.get('devices'):
        devices = state['devices'].items()
        canvas_context = state.get('canvasContext')
        if canvas_context:
            devices = [(identity, device) for identity, device in devices
                       if identity == canvas_context.get('clientId')]
        if devices:
            device_id,device=max(devices,key=lambda pair:pair[1].get('updatedAt',0))
            core['visibleUI']={**_preview(device,_pointer('/devices',device_id),4000), 'clientId':device_id}
    from .cold_display import message_count
    core['conversations'] = [{'id':s['id'],'title':s.get('title'),'status':s.get('status'),'messageCount':s.get('sharedHistoryTotal',message_count(s) if s.get('historyLoaded',True) else None),'historyLoaded':s.get('historyLoaded',True),'workspaceId':s.get('workspaceId'),'$statePath':f'/sessions/{i}'} for i,s in enumerate(state.get('sessions',[])) if is_top_level(s)][:50]
    children = [(i,s) for i,s in enumerate(state.get('sessions',[])) if not is_top_level(s) and s.get('parentId') == selected]
    core['subagentChats'] = {'total':len(children), 'items':[{'id':s['id'],'title':s.get('title'),'$statePath':f'/sessions/{i}'} for i,s in children[:20]]}
    core['_stateAccess'] = {'note':'This overview is scoped to the calling session. Other app state and detailed resources are available by JSON Pointer; pass path, offset, limit and optional revision. Follow nextOffset. Text previews and $resource references are not the complete value.',
        'drafts':'Ordinary readback excludes unsent composer text, message edits and attachment drafts, including JSON Pointer aliases and rendered UI. Only for user-requested draft work, get_state {path:"/view/draft",clientId:"..."} reads that exact browser displaying the calling conversation. The exact /view/messageEdit pointer supports the same explicit inspection for an unfinished message edit in this conversation. An overview or parent pointer never opts in. Other chats and new-chat drafts remain private.',
        'canvas':'Canvas and selected-chat readback use the calling conversation. canvasContext lists matching clients. canvas.select accepts a caller-owned artifact id and optional clientId; specify clientId for ambiguous clients, including on get_state reads. Open the calling chat in a client before selecting when none matches. This never selects another chat or discards unsaved viewer edits.',
        'settings':'Open a live Settings artifact with canvas.show {kind:"settings"}. It uses normal chat tabs and can be closed/reopened. Navigate with view.update {patch:{settingsExpanded:[pageId],workSurface:"chat"}}. settingsUI lists global pages and current route; visibleUI reports rendered controls, validation, and non-secret values. Use existing typed providers.*, settings.update, notifications.*, shell.*, etc. actions to make authorized changes, then read state to confirm. Password/private credential values are never available through visibleUI. Ask users to enter secrets in the form. Chat-specific options use the Chat menu; do not rebuild Settings in an artifact. session.create, workspace.create, session.pin, workspace.pin and session.select/workspace.select support user-requested organization and navigation. Offer optional demonstrations, never start paid requests, grants or external actions merely as a tour.',
        'navigation':'The current input origin selects the default browser for state and navigation. Explicit navigation targets still require a connected client displaying this chat. computerVisual reports that browser\'s screen permission independently of voice. computer.visual actions require one authoritative connected input origin; another browser\'s consent cannot be borrowed, even with an explicit clientId. Missing or mixed origins require a new input from the intended browser. Opening controls never grants screen permission.',
        'shell':'Use visibleUI.clientId to address this browser. shell.inspect returns its composition, resolvedInstances, supported slots, componentCommands schemas and activation evidence; shell.query {clientId,instanceId} returns a bounded snapshot for that module profile. New component slots use shell.command {clientId,instanceId,generation,action,args} with the observed generation. Replaced components reject late actions. shell.view.update {clientId,instanceId,generation,patch} edits its own filters, pages or form without retargeting chat drafts. The default instance IDs are workspaces and chats. Legacy /workspaceExplorer, /chatNavigation and view.update navigation fields are app defaults; modules may have independent overrides. shell.packages.stage and shell.packages.validate precede shell.changes.prepare, preview, apply and revert. These shell commands do not alter runtime modules or bundles.',
        'history':'CLI workspaces and top-level chats are discovered automatically. /workspaceExplorer is the legacy default folder projection: only existing workspaces with top-level chats and their ancestor folders appear. Rows with workspaceId can be selected with workspace.select; rows with canBrowse can be opened with view.update {patch:{navWorkspacePath:path}} without switching the conversation. Search all workspace paths or aliases using navWorkspaceFilter (case-insensitive fnmatch or plain text), navigate 1-based pages with navWorkspacePage, and toggle the ancestor menu with navWorkspaceAncestorsOpen. Browsing persists until a different workspace is selected. workspace.create {path,name?} creates or chooses a folder and opens its first chat without starting model work. Missing or empty folders stay in /workspaces; history.refresh rechecks availability. Subagent histories remain in /sessions with sessionKind=worker and parentId; explicit internal jobs have sessionKind=internal and optional sessionPurpose. Both are omitted from ordinary conversation lists. Internal diagnostic history queries require include_internal:true; recall.search uses includeInternal:true. Unloaded transcripts have no message count yet. Use history.refresh, session.select, and session.history {id,before,limit} to refresh or read earlier messages. Respect historyReadOnlyReason before continuing saved worker or legacy sessions.',
        'chats':'session.naming {id,automatic:true|false} changes future automatic naming without a model call. session.naming {id,regenerate:true} generates one name from the saved conversation without sending a chat turn or changing the Auto preference. Inspect the session naming.status for completion; a newer rename or policy wins over late results. session.rename disables Auto. /chatNavigation is the legacy default bounded chat list: pins in their saved order, then recent ready activity (not intermediate progress). navSort chooses activity, created, or name; activity is the default. Use view.update {patch:{navChatScope:"all"}} for top-level chats across available workspaces or navChatScope:"workspace" for the selected workspace. navFilter searches titles, descriptions, IDs and full workspace paths or names using case-insensitive fnmatch or plain text. Browse pages with navChatPage:{...chatNavigation.scope,index} (zero-based). session.pin {id,pinned:true|false} pins or unpins a root chat; pinnedSessionIds are app preferences and never change its shared transcript. session.pinOrder {ids:[...]} saves every pinned ID in the desired order; include hidden pins too. Selecting, renaming or pinning a chat does not make it recent. /headerChatNavigation is the selected workspace menu. /subagentNavigation pages direct children: use view.update {patch:{panel:"subagent-history",subagentHistory:{sessionId,filter,index}}}; index is zero-based, pages hold 50. Browser snapshots contain only visible catalog pages; full /sessions and /workspaces remain available through JSON Pointer paging, and actions accept IDs outside the displayed page.',
        'canvasApps':'Use canvas.apps.create once for conversation-owned interactive HTML. Keep its id and use inspect/revise/restore to refine the same tab with shared typed state. Read both revisions before mutations. canvas.apps.event invokes the same declared host-side update as a user interaction; state merges validated fields. Agents can resolve user-authorized host requests with canvas.apps.resolve {clientId, requestId, approve:true, ...revisions}. Only the sandbox is prohibited from resolving requests. Events do not automatically start an agent turn. Read the amplifier-shell skill conversation-surfaces guide for the bridge contract.',
        'canvasViews':'Use canvas.views.inspect {clientId} for the intended browser. It returns the selected artifact view, validated renderer choices, and exact viewId/resourceId/resourceRevision/generation targets. Include that target and clientId in canvas.views.renderer, command, dirty or recover. Use canvas.select to open another saved artifact in this viewer. Stage and validate trusted-native-renderer-v1 packages before choosing their digest. Browser ready/error status is separate from package validation.',
        'revision':state.get('revision'),'sections':[{'name':key,'path':'/'+key} for key in state]}
    return core


def read_state(state, args, *, session_id=None, resolve=None, reference_key='$statePath', directory=None,
               configuration_projection=False):
    if not isinstance(args,dict):
        raise ValueError('State arguments must be an object.')
    path=args.get('path')
    revision=args.get('revision')
    if revision is not None and revision != state.get('revision'):
        raise ValueError('App state changed. Read the current revision before continuing pages.')
    if session_id is not None:
        from .agent_private_state import project
        state = project(state, session_id, args)
    if directory is not None:
        from .directory_state import read
        handled, result = read(directory, state, args, session_id=session_id, resolve=resolve, reference_key=reference_key)
        if handled:
            return result
    if path is None:
        return overview(state, session_id)
    if not isinstance(path,str) or (path and not path.startswith('/')):
        raise ValueError('path must be a JSON Pointer, for example /canvas or /sessions/0/messages.')
    from .configuration_state import is_configuration, is_provider_metadata, project as public_preview
    app_state = configuration_projection or session_id is not None or directory is not None
    value=state
    current_path=''
    module=None
    try:
        for token in path.split('/')[1:] if path else []:
            if resolve and isinstance(value,dict) and '$resource' in value:
                value=resolve(value['$resource'])
            if app_state and is_configuration(current_path):
                from .runtime_controls import public_config
                value=public_config(value, module=module, metadata=is_provider_metadata(current_path))
            token=token.replace('~1','/').replace('~0','~')
            if (app_state and token == '_coldFields'
                    and len(current_path.split('/')) == 3
                    and current_path.split('/')[1] in {'sessions', 'runtimeControl'}):
                raise KeyError(token)  # Internal resource aliases are not public state paths.
            module=value.get('module') if isinstance(value,dict) and token == 'config' else None
            value=value[int(token)] if isinstance(value,list) else value[token]
            current_path=_pointer(current_path,token)
    except (KeyError,IndexError,TypeError,ValueError):
        raise ValueError('That state path does not exist.') from None
    if resolve and isinstance(value,dict) and '$resource' in value:
        value=resolve(value['$resource'])
    if app_state and is_configuration(path):
        from .runtime_controls import public_config
        value=public_config(value, module=module, metadata=is_provider_metadata(path))
    offset=args.get('offset',0);limit=args.get('limit',50)
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=16000:
        raise ValueError('Use a nonnegative integer offset and a limit between 1 and 16000.')
    result={'path':path,'revision':state.get('revision'),'offset':offset,'nextOffset':None}
    if isinstance(value,str):
        end=min(len(value),offset+min(limit if 'limit' in args else 16000,16000))
        return {**result,'type':'string','value':value[offset:end],'total':len(value),'nextOffset':end if end<len(value) else None}
    if isinstance(value,(list,dict)):
        keys=list(value) if isinstance(value,dict) else range(len(value))
        selected=keys[offset:offset+min(limit,50)];rows=[];used=0
        for key in selected:
            item=value[key]
            if app_state:
                item=public_preview(item,_pointer(path,key),reference_key=reference_key)
            preview=_preview(item,_pointer(path,key),2500,reference_key=reference_key)
            row={'key':key,'path':_pointer(path,key),'value':preview};size=_size(row)
            if rows and used+size>24000:break
            rows.append(row);used+=size
        end=offset+len(rows)
        return {**result,'type':'object' if isinstance(value,dict) else 'array','items':rows,'total':len(keys),'nextOffset':end if end<len(keys) else None}
    return {**result,'type':'scalar','value':value}


def surface_context(state, session_id, clients):
    """Canvas actions already return their shared model; avoid repeating the app."""
    devices = [(identity, device) for identity, device in state.get('devices', {}).items()
               if clients.get(identity, {}).get('selectedSessionId') == session_id]
    visible = max(devices, key=lambda pair: pair[1].get('updatedAt', 0), default=None)
    return {'revision': state.get('revision'), 'sessionId': session_id,
            'visibleUI': {'clientId': visible[0]} if visible else None,
            'clients': [{'clientId': identity, 'selectedSessionId': clients.get(identity, {}).get('selectedSessionId')}
                        for identity, device in list(state.get('devices', {}).items())[-20:]],
            '_stateAccess': {'note': 'The result contains the surface state. Use get_state with a JSON Pointer for other app state; canvas.views.inspect {clientId} reports dirty views and rendering errors.',
                             'canvasApps': 'After user authorization, agents resolve a pending host request with canvas.apps.resolve {id, clientId, requestId, approve:true, expectedRevision, expectedStateRevision}. Inspect current revisions and target the intended attached browser. The sandbox cannot approve itself.'}}
