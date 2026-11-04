"""Durable canvas snapshots. Only the active body travels with regular app state."""
import copy
import hashlib
import json
import time
import uuid

from .state_storage import resource


def restore_body(canvas, db, *, inline_documents=False):
    """Restore a client's compact saved body for its ordinary viewer.

    HTML, Babylon and PDF read their source through separate endpoints;
    keep those potentially large bodies out of browser snapshots.
    """
    reference = canvas.get('contentResource')
    if not reference or (not inline_documents and canvas.get('kind') in {'html', 'babylon', 'canvas-app', 'pdf'}):
        return
    try:
        body = resource(db, reference['$resource'])
        if not isinstance(body, dict):
            raise ValueError('Invalid saved canvas body.')
    except (KeyError, ValueError, OSError, TypeError):
        # A damaged older artifact must not prevent attaching to the client.
        # Keep the reference for recovery rather than saving an empty body.
        canvas.setdefault('renderReports', {})['stored-source'] = {
            'status': 'error', 'message': 'The saved artifact source is unavailable.'}
        return
    for key in ('content', 'surface', 'mediaReference'):
        if key in body:
            canvas[key] = copy.deepcopy(body[key])
    canvas.pop('contentResource', None)
    canvas.get('renderReports', {}).pop('stored-source', None)


def presentation(state, row):
    """Tabs and viewer controls belong to the attached client."""
    tabs = state.get('canvasTabs')
    if tabs is None:
        return row
    return tabs.setdefault(row['id'], {key: copy.deepcopy(row[key])
        for key in ('view', 'tabOpen', 'lastViewedAt') if key in row})


def remember(state, db):
    rows = state.setdefault('canvasArtifacts', [])
    canvas = state.get('canvas', {})
    if not canvas.get('kind') or canvas.get('placeholder') or canvas.get('app'):
        return
    if canvas.get('renderReports', {}).get('stored-source', {}).get('status') == 'error' and not any(key in canvas for key in ('content', 'surface', 'mediaReference')):
        return
    if 'sessionId' not in canvas:  # Migrate the one legacy preview.
        canvas['sessionId'] = state.get('selectedSessionId')
    session = next((s for s in state.get('sessions', []) if s['id'] == canvas['sessionId']), {})
    if 'messageId' not in canvas:
        canvas['messageId'] = next((m['id'] for m in reversed(session.get('messages', [])) if m.get('role')=='user' and m.get('id')), None)
    canvas.setdefault('createdAt', time.time())
    reference=canvas.get('contentResource')
    if not reference:
        # Connection/context/operation IDs change on every poll; they must never
        # become part of the static HTML content identity.
        body = {key:canvas[key] for key in ('content','surface','mediaReference') if key in canvas}
        from .resource_files import put
        reference=put(db,body)
        if canvas.get('kind') == 'pdf' or (canvas.get('kind') in {'html','babylon'} and len(canvas.get('content','').encode())>1_000_000):
            # The immutable snapshot stays in the artifact store, not SSE, action
            # receipts, the agent context, or every subsequent state write.
            canvas['contentResource']=reference
            canvas.pop('content',None)
    previous = next((r for r in rows if r['id']==canvas['id']), None)
    if canvas.get('selectedVersion') is not None:
        return  # Historical viewing can never replace the latest saved body.
    if previous and not canvas.get('_versionWrite'):
        # A passive, older client snapshot must not undo another client's edit.
        if canvas.get('revision', 1) != previous.get('revision', 1):
            return

    record = {key:copy.deepcopy(canvas[key]) for key in ('id','title','kind','path','workspacePath','url','sessionId','workspaceId','messageId','createdAt','view','events','sharedToolView','contentResource','presentationKey') if key in canvas}
    if 'mcp' in canvas:
        from .resource_files import put
        record['mcpState'] = put(db, canvas['mcp'])
    if previous:
        record['messageId'] = previous.get('messageId')
    record['body'] = copy.deepcopy(reference)
    from .canvas_versions import save
    if not previous or canvas.get('_versionWrite'):
        record['_presentationWrite'] = canvas.pop('_presentationWrite', False)
        save(record, previous, state, force=canvas.pop('_forceVersion', False),
             **({'message': None} if canvas.get('kind') == 'gif' else {}))
        record.pop('_presentationWrite', None)
    else:
        record.update({key: copy.deepcopy(previous[key]) for key in ('revision', 'versions', 'publications') if key in previous})
    canvas.pop('_versionWrite', None)
    canvas.update({key: copy.deepcopy(record[key]) for key in ('revision', 'versions', 'publications') if key in record})
    record['tabOpen'] = previous.get('tabOpen', True) if previous else True
    if state.get('canvasTabs') is not None:
        local = presentation(state, previous or record)
        local['view'] = copy.deepcopy(canvas.get('view', {}))
        if previous:
            record['view'] = copy.deepcopy(previous.get('view', {}))
    if previous:
        from .canvas_versions import FIELDS
        for key in FIELDS:
            previous.pop(key, None)
        previous.update(record)
    else:
        rows.append(record)


def scope(state, row):
    return row.get('sessionId') == state.get('selectedSessionId') and row.get('workspaceId') == state.get('selectedWorkspaceId')


def load(state, db, identity, *, open_panel=True, version=None):
    from .errors import AppError
    row = next((r for r in state.get('canvasArtifacts',[]) if r['id']==identity and scope(state,r)), None)
    if not row:
        raise AppError('This artifact belongs to another chat or is no longer available.')
    local = presentation(state, row)
    from .canvas_versions import definition, ordinary_preview
    selected = definition(row, version, db)
    canvas = {**copy.deepcopy(selected), **{key: copy.deepcopy(local[key]) for key in ('view', 'tabOpen', 'lastViewedAt') if key in local}, 'open':open_panel, 'renderReports':{}}
    if ordinary_preview(selected):
        canvas.update(runningVersion=selected.get('revision', 1), latestVersion=row.get('revision', 1), previewRetained=False)
    if not selected.get('contentResource'):
        try:
            body = resource(db, selected['body']['$resource'])
            if not isinstance(body, dict):
                raise ValueError('Invalid saved canvas body.')
            canvas.update(copy.deepcopy(body))
        except (KeyError, ValueError, OSError, TypeError):
            # Keep the original immutable reference. Opening the drawer must
            # not fail or overwrite a historical artifact with an empty body.
            canvas['contentResource'] = copy.deepcopy(selected.get('body'))
            canvas['renderReports']['stored-source'] = {
                'status': 'error', 'message': 'The saved artifact source is unavailable. Other saved artifacts are still accessible.'}
    canvas.pop('body',None)
    canvas.pop('tabOpen',None)
    mcp_state = canvas.pop('mcpState', None)
    if mcp_state:
        try:
            canvas['mcp'] = resource(db,mcp_state['$resource'])
        except (KeyError, ValueError, OSError, TypeError):
            canvas['renderReports']['stored-tool-state'] = {
                'status': 'error', 'message': 'The saved tool state is unavailable. No tool work was replayed.'}
    restore_body(canvas, db)
    local['tabOpen'] = True
    local['lastViewedAt'] = time.time()
    state['canvas'] = canvas


def empty(state, *, open_panel=False):
    state['canvas'] = {'id':uuid.uuid4().hex,'open':open_panel,'placeholder':True,'events':[],
                       'sessionId':state.get('selectedSessionId'),'workspaceId':state.get('selectedWorkspaceId')}


def open_gif(service, args, proof):
    """Under the action lock, bind one immutable reference to this client only."""
    from .errors import AppError
    # Fresh guard immediately before mutation: an earlier guarded record is
    # not authority. Canonical proof is obtained off-lock by the dispatcher.
    record = service.outputs.media.guard(args, proof=proof)
    if record.get('media', {}).get('mimeType') != 'image/gif':
        raise AppError('Open in Canvas is available only for retained GIFs.', 409)
    item = {key: copy.deepcopy(record[key]) for key in
            ('id', 'sessionId', 'title', 'filename', 'version', 'sha256', 'bytes', 'media')}
    reference = {'item': item, 'messageId': args['messageId']}
    if len(json.dumps(reference, ensure_ascii=False).encode()) >= 8000:
        raise AppError('The retained GIF reference metadata is unavailable.', 409)
    from .resource_files import put
    body = put(service.db, {'mediaReference': reference})
    state = service.state
    row = next((r for r in state.get('canvasArtifacts', [])
                if r.get('kind') == 'gif' and scope(state, r) and r.get('body') == body), None)
    if row is None:
        from .canvas_versions import save
        row = {'id': uuid.uuid4().hex, 'kind': 'gif', 'title': record['title'],
               'sessionId': args['sessionId'], 'workspaceId': state.get('selectedWorkspaceId'),
               # Do not infer a user publication or duplicate the inline GIF.
               'messageId': None, 'createdAt': time.time(), 'body': body, 'tabOpen': False, 'view': {}}
        save(row, None, state, message=None)
        state.setdefault('canvasArtifacts', []).append(row)
    load(state, service.db, row['id'])
    state['view']['settingsCanvas'] = False
    state['view'].setdefault('canvasDraft', {}).update(library=False, open=False, browser=False, overview=False)
    return {'status': 'opened', 'id': row['id'], 'version': row['revision']}


def command(state, db, action, args):
    from .errors import AppError
    remember(state,db)
    if action=='canvas.select':
        from .canvas_versions import ordinary_preview, latest
        current = state.get('canvas', {})
        row = next((r for r in state.get('canvasArtifacts', []) if r['id'] == args['id'] and scope(state, r)), None)
        version = args.get('version') or (latest(row) if row else None)
        if (row and ordinary_preview(current) and current.get('id') == row['id']
                and current.get('runningVersion', current.get('revision', 1)) == version
                and not current.get('_presentationDisposed')):
            # Pin/unpin intent can change without rerunning the same definition.
            current.update(open=True, selectedVersion=args.get('version'),
                           readOnlyVersion=args.get('version') is not None, previewRetained=False)
            presentation(state, row)['tabOpen'] = True
        else:
            load(state,db,args['id'],version=args.get('version'))
    elif action=='canvas.reopen':
        current=state.get('canvas',{})
        if scope(state,current) and not current.get('placeholder'):
            current['open']=True
            row=next((r for r in state['canvasArtifacts'] if r['id']==current['id']),None)
            if row:presentation(state, row)['tabOpen']=True
        else:
            restore(state,db,open_panel=True)
    elif action=='canvas.tabClose':
        row=next((r for r in state['canvasArtifacts'] if r['id']==args['id'] and scope(state,r)),None)
        if not row:raise AppError('Canvas tab is unavailable.')
        presentation(state, row)['tabOpen']=False
        if state['canvas'].get('id')==row['id']:
            remaining=[r for r in state['canvasArtifacts'] if scope(state,r) and presentation(state, r).get('tabOpen')]
            if remaining:load(state,db,remaining[-1]['id'])
            else:empty(state,open_panel=True)


def restore(state,db,*,open_panel=False):
    if state.get('selectedSessionId') is None:
        empty(state)
        return
    rows=[r for r in state.get('canvasArtifacts',[]) if scope(state,r) and presentation(state, r).get('tabOpen')]
    if rows:load(state,db,max(rows,key=lambda r:presentation(state, r).get('lastViewedAt',r.get('createdAt',0)))['id'],open_panel=open_panel)
    else:empty(state,open_panel=open_panel)


def fork_artifacts(state, source_id, target, db=None):
    """Carry snapshots only through retained messages; never carry future artifacts."""
    kept={m['id'] for m in target.get('messages',[]) if m.get('id')}
    mapping = {}
    for row in list(state.get('canvasArtifacts',[])):
        if row.get('sessionId')==source_id and row.get('messageId') in kept:
            from .canvas_versions import fork_definition
            definition = fork_definition(row, kept, db)
            if definition is None:
                continue
            cloned = {**definition,'id':uuid.uuid4().hex,'sessionId':target['id'],'tabOpen':False,**({'sharedToolView':True} if row.get('kind')=='mcp-app' else {})}
            if cloned.get('app'):
                cloned['app']['requests'] = []  # Forked history never replays approvals.
            state['canvasArtifacts'].append(cloned)
            mapping[row['id']] = cloned['id']
    return mapping


def image_output(state, session, output):
    """Publish a verified image snapshot through the existing version owner.

    The output and Canvas retain one immutable body. An omitted origin stays in
    the library only; finishing a long generation never infers the newest turn.
    This does not select a tab, open a panel, or alter any client draft.
    """
    from .canvas_versions import save
    workspace = next((w['id'] for w in state.get('workspaces', [])
                      if w.get('path') == session.get('workspace')), None)
    mid = output.get('origin', {}).get('messageId')
    row = {'id': output['canvasId'], 'kind': 'image', 'title': output['title'],
           'sessionId': session['id'], 'workspaceId': workspace,
           'messageId': mid, 'createdAt': output['createdAt'], 'tabOpen': False,
           'body': copy.deepcopy(output['body']),
           'imageRequestId': output['imageGeneration']['requestId'],
           'contentResource': copy.deepcopy(output['body'])}
    save(row, None, state, message=mid)
    state.setdefault('canvasArtifacts', []).append(row)
    return row


def recover_legacy(state, db, home):
    """Recover accepted inline publications from checkpoints without replaying tools."""
    if state.get('canvasLibraryMigration'):
        return
    from .host.storage import SessionStore
    from .session_store import text_content
    from .workspace_canvas import canvas_command
    remember(state,db)
    recovered=0
    for session in state.get('sessions',[]):
        if session.get('historyManaged') or not session.get('workspace'):
            continue
        try:
            store=SessionStore.for_app(home,session['workspace'])
            store._migrate(session.get('runtimeSessionId') or session['id'])
            path=store.directory(session.get('runtimeSessionId') or session['id']) / 'transcript.jsonl'
            if not path.is_file() or path.stat().st_size>64_000_000:continue
            saved=store.load(session.get('runtimeSessionId') or session['id'])
            if not saved:continue
            messages=saved[0]
        except (OSError,ValueError):
            continue
        workspace=next((w for w in state.get('workspaces',[]) if w['path']==session.get('workspace')),None)
        from .managed_chats import is_managed
        if not workspace and is_managed(session):workspace={'id':None}
        if not workspace:continue
        receipts={m.get('tool_call_id'):m for m in messages if m.get('role')=='tool'}
        visible=[m for m in session.get('messages',[]) if m.get('role')=='user']
        cursor=0;message_id=None
        for message in messages:
            if message.get('role')=='user' and cursor<len(visible):
                content=message.get('content')
                first=content[0].get('text') if isinstance(content,list) and content and isinstance(content[0],dict) else None
                if text_content(message)==visible[cursor].get('text') or (visible[cursor].get('attachments') and first==visible[cursor].get('text')):
                    message_id=visible[cursor]['id'];cursor+=1
            for call in message.get('tool_calls',[]) or []:
                function=call.get('function') or call
                if (function.get('name') or call.get('tool'))!='app_control':continue
                try:
                    args=function.get('arguments',{})
                    if isinstance(args,str):args=json.loads(args)
                    if args.get('operation') not in {'dispatch','action.dispatch'} or args.get('args',{}).get('action')!='canvas.show':continue
                    result=receipts.get(call.get('id'),{}).get('content','{}')
                    if isinstance(result,str):result=json.loads(result)
                    if not isinstance(result,dict) or result.get('error') or not result.get('output',result).get('accepted'):continue
                    payload=args['args']['args']
                    if payload.get('path'):continue  # Never invent an old file snapshot by rereading today's file.
                    temporary={**state,'selectedSessionId':session['id'],'selectedWorkspaceId':workspace['id'],'canvas':{}}
                    canvas_command(temporary,'canvas.show',payload,'migration')
                    canvas=temporary['canvas']
                    canvas.update(id=uuid.uuid5(uuid.NAMESPACE_URL,session['id']+str(call.get('id'))).hex,messageId=message_id)
                    existing=next((r for r in state['canvasArtifacts'] if r.get('sessionId')==session['id'] and r.get('title')==canvas.get('title') and r.get('messageId')==message_id and resource(db,r['body']['$resource'])=={k:canvas[k] for k in ('content','surface','mcp') if k in canvas}),None)
                    if existing:continue
                    remember(temporary,db)
                    state['canvasArtifacts'][-1]['tabOpen']=False
                    recovered+=1
                except (ValueError,TypeError,KeyError,AttributeError):
                    continue
                except Exception as exc:
                    from .errors import AppError
                    if not isinstance(exc,AppError):raise
    state['canvasLibraryMigration']={'recovered':recovered,'at':time.time()}
