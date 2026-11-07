"""On-demand bounded disk accounting and reviewed workspace-file cleanup.

History and configuration are never cleanup targets. A durable attempt receipt
precedes rename/removal; an interrupted attempt is inspected, never replayed.
"""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

from .managed_deletion import _safe, _inventory, _idle, _hold
from .session_files import amplifier_home, sessions_dir, validate_id

LIMIT = 200000


def definitions(schema, string):
    return {
        'storage.historyDeletePreview': ('Review deletion of all saved sessions for a project, or one session and its workers. Preserves project working files and CLI configuration. No deletion occurs.', schema({'project':string(4000),'nativeId':string(220)}, ['project'])),
        'storage.historyDelete': ('Permanently delete only the reviewed session data after explicit user confirmation. Requires the unexpired confirmationToken.', schema({'project':string(4000),'nativeId':string(220),'confirmationToken':string(100)}, ['project','confirmationToken'])),
        'storage.inspect': ('Read content categories and disk usage on the owning host. category drills into a type, rootId into a returned item, path into a folder. Legacy scope selects app, history or workspace. Counts are bounded and never follow links.', schema({'scope':{'enum':['app','history','workspace']},'workspaceId':string(100),'path':string(4000),'category':{'enum':['workspaces','sessions','runtime','backups','cache','other']},'rootId':string(100),'sessionId':string(100),'progressive':{'type':'boolean'},'scanId':string(100),'fromScanId':string(100),'cancel':{'type':'boolean'}}, [])),
        'storage.backupDeletePreview': ('Review removal of one older completed private backup. Keeps the newest backup and current data.', schema({'id':string(100)}, ['id'])),
        'storage.backupDelete': ('Permanently remove the reviewed older backup only after explicit user confirmation. Never retry an interrupted removal with a new token.', schema({'id':string(100),'confirmationToken':string(100)}, ['id','confirmationToken'])),
        'workspace.deleteFilesPreview': ('Review deletion of a workspace folder while retaining conversation history. Refuses active, overlapping, linked, or configuration-bearing folders. Does not delete anything.', schema({'id':string(100)}, ['id'])),
        'workspace.deleteFiles': ('Only after explicit user confirmation of the exact preview: permanently delete workspace files, preserve chats/history, archive its registration. Requires confirmationToken and exact confirmationPath. Never retry an interrupted filesystem mutation with a new token.', schema({'id':string(100),'confirmationToken':string(100),'confirmationPath':string(4000)}, ['id','confirmationToken','confirmationPath'])),
    }


def _workspace(app, identity):
    row = next((row for row in app.state['workspaces'] if row['id']==identity), None)
    if not row or not row.get('path'):
        raise ValueError('This workspace has no local folder.')
    return row


def _scan(path, seen, budget):
    total=0; files=0; incomplete=False
    try:
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode):return {'bytes':0,'files':0,'incomplete':True,'linked':True}
        if budget[0]<=0:return {'bytes':0,'files':0,'incomplete':True}
        budget[0]-=1
        if stat.S_ISREG(info.st_mode):
            key=(info.st_dev,info.st_ino)
            if key not in seen:total=info.st_size;files=1;seen.add(key)
        elif stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                for entry in entries:
                    if budget[0]<=0:incomplete=True;break
                    child=_scan(Path(entry.path),seen,budget)
                    total+=child['bytes'];files+=child['files'];incomplete|=child['incomplete']
        else:incomplete=True
    except (OSError,RecursionError):incomplete=True
    return {'bytes':total,'files':files,'incomplete':incomplete}


def inspect(home, workspaces, args):
    home=Path(home).absolute();history=amplifier_home()/'projects'
    scope=args.get('scope')
    if not scope:
        from .storage_catalog import inspect as categorized
        return categorized(home, workspaces, args)
    workspace=next((r for r in workspaces if r['id']==args.get('workspaceId')),None)
    root=home if scope=='app' else history if scope=='history' else Path(workspace['path']) if workspace and workspace.get('path') else None
    if root is None:raise ValueError('Choose a workspace with a folder.')
    relative=Path(args.get('path') or '.')
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('Choose a path within this storage category.')
    path=_safe(root/relative)
    if not path.is_dir():raise ValueError('This folder is no longer available.')
    seen=set();budget=[LIMIT];rows=[]
    for entry in sorted(path.iterdir(),key=lambda p:p.name.casefold()):
        usage=_scan(entry,seen,budget)
        rows.append({'id':entry.name,'label':entry.name,'path':str(entry.relative_to(root)),
                     'directory':not entry.is_symlink() and entry.is_dir(),**usage})
        if len(rows)>=2000:break
    return {'rows':sorted(rows,key=lambda row:row['bytes'],reverse=True),'path':str(path),
            'parent':str(relative.parent) if relative!=Path('.') else None,
            'bytes':sum(row['bytes'] for row in rows),'incomplete':len(rows)>=2000 or any(row['incomplete'] for row in rows),
            'workspaceId':workspace['id'] if workspace else None}


def _history_scope(app, path):
    """Explicit cleanup proof over durable placement, without live admission."""
    directory = getattr(app, 'directory', None)
    if directory is None:
        owners = list(app.state['sessions'])
    else:
        residents = {row['id']: row for row in app.state['sessions']}
        owners = list(residents.values())
        from .state_records import load_owner
        from .session_projection import hydrate
        # Read only scalar placement for unrelated dormant owners. Retained
        # summary placement is written in the same transaction as its pointer.
        query = """SELECT r.id,json_extract(r.value,'$.workspace'),l.workspace,
                   json_extract(l.summary,'$.workingDirectory')
            FROM state_records r LEFT JOIN history_local_rows l ON l.id=r.id
            WHERE r.kind='session' AND r.value!='null'"""
        for identity, saved_workspace, indexed_workspace, execution in app.db.execute(query):
            if identity in residents:
                continue
            workspace = indexed_workspace or saved_workspace
            if workspace is None:
                raise ValueError('Saved chat placement is unavailable. Its files were preserved.')
            if execution is None:
                metadata = app.worktrees.saved_owner(identity)
                if metadata is None:
                    raise ValueError('Saved chat placement is unavailable. Its files were preserved.')
                execution = metadata.get('workingDirectory') or metadata.get('workspace')
            candidate = {'id': identity, 'workspace': workspace, 'workingDirectory': execution}
            _check_overlap(candidate, path)
            if Path(workspace).absolute() == path:
                saved, _ = load_owner(app.db, identity)
                if saved is None:
                    raise ValueError('A saved chat record is unavailable. Its files were preserved.')
                context = {'sessions': [saved], 'runtimeControl': {}}
                hydrate(app.data_dir, context, app.db)
                candidate = context['sessions'][0]
                metadata = app.db.execute('SELECT summary FROM history_local_rows WHERE id=?', (identity,)).fetchone()
                if metadata:
                    candidate.update(json.loads(metadata[0]))
                owners.append(candidate)
        # An orphaned local directory row must not disappear from the proof.
        for identity, in app.db.execute("""SELECT l.id FROM history_local_rows l
                LEFT JOIN state_records r ON r.kind='session' AND r.id=l.id
                WHERE r.value IS NULL OR r.value='null'"""):
            if identity not in residents:
                # A missing pointer cannot prove all former execution locations.
                raise ValueError('A saved chat record is unavailable. Its files were preserved.')
        if directory._native_path:
            # Hidden navigation rows still own their history and working files.
            for workspace, in app.db.execute('SELECT DISTINCT workspace_path FROM history_native.native_rows WHERE namespace=?',
                                             (directory._native_namespace,)):
                if workspace is None:
                    raise ValueError('Native chat placement is unavailable. Its files were preserved.')
                _check_overlap({'workspace': workspace}, path)
            known = {(row.get('nativeProject'), row.get('nativeIdentity') or row.get('runtimeSessionId') or row['id'])
                     for row in owners}
            for project, identity, raw in app.db.execute('SELECT project,identity,value FROM history_native.native_rows '
                    'WHERE namespace=? AND workspace_path=?', (directory._native_namespace, str(path))):
                if (project, identity) in known:
                    continue
                metadata = json.loads(raw)
                owners.append({**metadata, 'id': metadata['id'], 'workspace': str(path),
                               'nativeProject': project, 'nativeIdentity': identity,
                               'runtimeSessionId': identity, 'historyManaged': True, 'status': 'idle'})
    for owner in owners:
        _check_overlap(owner, path)
    return [owner for owner in owners if owner.get('workspace') and Path(owner['workspace']).absolute() == path]


def _overlaps(candidate, path):
    return candidate == path or candidate.is_relative_to(path) or path.is_relative_to(candidate)


def _check_overlap(owner, path):
    workspace = Path(owner['workspace']).absolute() if owner.get('workspace') else None
    for value in (owner.get('workspace'), owner.get('workingDirectory')):
        if not value:
            continue
        candidate = Path(value).expanduser().resolve()
        if workspace != path and _overlaps(candidate, path):
            raise ValueError('Another chat uses an overlapping folder. Its files were preserved.')


def _scope(app,identity):
    if (not shutil.rmtree.avoids_symlink_attacks or os.rename not in os.supports_dir_fd
            or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')):
        raise ValueError('Safe workspace-file cleanup is unavailable on this host. Use its file manager; storage inspection and archive remain available.')
    row=_workspace(app,identity);path=_safe(row['path'])
    if not path.is_dir():raise ValueError('The workspace folder is no longer available.')
    # Preserve configuration, CLI state, application state, and all independent
    # working trees. App-created workspace roots may live below app data.
    for protected in (Path.home(),app.data_dir.absolute(),amplifier_home(),Path('/')):
        if protected==path or protected.is_relative_to(path):
            raise ValueError('This folder contains application, home, or CLI state. Its files cannot be deleted here.')
    if path.is_relative_to(amplifier_home()) or (path.is_relative_to(app.data_dir.absolute()) and not path.is_relative_to(app.data_dir.absolute()/'workspaces')):
        raise ValueError('Application and CLI data are protected. Only workspace files can be cleared here.')
    if (path/'.amplifier').exists() or (path/'.amplifier').is_symlink():
        raise ValueError('This folder contains CLI configuration. Preserve that configuration and clean up files individually outside the app.')
    for other in app.state['workspaces']:
        if other['id']==identity or not other.get('path'):continue
        candidate=Path(other['path']).absolute()
        if candidate==path or candidate.is_relative_to(path) or path.is_relative_to(candidate):
            raise ValueError('Another registered workspace overlaps this folder. Its files were preserved.')
    sessions=_history_scope(app,path)
    ids={value for s in sessions for value in (s['id'],s.get('runtimeSessionId'),s.get('nativeIdentity')) if value}
    _idle(app,sessions,ids)
    from .workspace_resources import command
    resources=command(app.data_dir,identity,'workspace.resources.list',{})
    if any(r['status']=='active' for r in resources['resources']):
        raise ValueError('This workspace has active external resources. Retire them before deleting files.')
    from amplifier_foundation.session.shared_state import SharedSessionStore
    native=set()
    history=sessions_dir(path)
    if history.exists():
        for directory in history.iterdir():
            if directory.is_dir():native.add(validate_id(directory.name))
    for s in sessions:
        identity=validate_id(s.get('runtimeSessionId') or s.get('nativeIdentity') or s['id']);native.add(identity)
        for retained in (history/identity,app.data_dir/'sessions'/identity,SharedSessionStore(path,identity).checkpoint_path):
            if _safe(retained).is_relative_to(path):raise ValueError('Saved history is inside this folder. Move it safely before deleting files.')
        if s.get('historyManaged') and not (history/identity/'transcript.jsonl').is_file():
            raise ValueError('This imported chat’s retained history could not be verified. Its files were preserved.')
    return row,path,sessions,{'workspace':str(path),'nativeIds':sorted(native)},ids


def _remove(path,inventory,quarantine):
    # Rename beneath the held, non-linked parent, then compare the full tree
    # before deleting. A changed tree is retained at its recorded quarantine.
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    fd=os.open(_safe(path.parent),flags)
    try:
        if _inventory(path)!=inventory:raise ValueError('The folder changed. Review its contents again.')
        os.rename(path.name,quarantine.name,src_dir_fd=fd,dst_dir_fd=fd)
        moved=_inventory(quarantine)
        if not moved or {**moved,'path':str(path)}!=inventory:
            raise ValueError('The folder changed during cleanup. Its remaining files were preserved in the recorded cleanup folder.')
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError('Safe folder removal is unavailable on this host. The cleanup folder was preserved.')
        shutil.rmtree(quarantine.name,dir_fd=fd)
    finally:os.close(fd)


async def dispatch(app,action,args,include_state):
    from .errors import AppError
    try:
        if action=='storage.inspect':
            from .workspace_placement import defaults
            from .storage_catalog import workspace_selection
            workspace_root = defaults(app)
            workspace_scope = {'root': workspace_root,
                               'description': 'Workspace totals include registered project folders inside your workspace root and app-managed workspace files. The root itself and folders outside it are not scanned. Chats from other folders remain in Session data.'}
            if args.get('scanId'):
                scans=getattr(app, '_storage_scans', None)
                if not scans:
                    raise ValueError('Storage measurement expired. Refresh storage to measure again.')
                result=await scans.read(app.data_dir,[],args,[],None,workspace_scope=workspace_root)
                return {'accepted':True,'result':{**result,'workspaceScope':workspace_scope},'effects':[]}
            rows=[{k:r[k] for k in ('id','path','name','archived') if k in r} for r in app.state['workspaces']]
            rows=workspace_selection(app.data_dir,rows,workspace_root)
            if args.get('scope'):
                result=await asyncio.to_thread(inspect,app.data_dir,rows,args)
            else:
                import sys
                from .storage_catalog import inspect as categorized
                # This explicit disk review needs named managed-chat locations,
                # including dormant owners. It never admits their saved bodies.
                sessions=[{k:copy.deepcopy(r[k]) for k in ('id','title','workspace','runtimeSessionId','location') if k in r}
                          for r in app.directory.select('managed=1', limit=-1)]
                installation=Path(sys.prefix) if sys.prefix != sys.base_prefix else None
                if args.get('progressive') or args.get('scanId'):
                    from .storage_scan import StorageScans
                    if not getattr(app, '_storage_scans', None):
                        app._storage_scans = StorageScans()
                    result=await app._storage_scans.read(app.data_dir,rows,args,sessions,installation,workspace_scope=workspace_root)
                else:
                    result=await asyncio.to_thread(categorized,app.data_dir,rows,args,sessions,installation)
            return {'accepted':True,'result':{**result,'workspaceScope':workspace_scope},'effects':[]}
        async with app.lock:
            app.db.execute('CREATE TABLE IF NOT EXISTS workspace_cleanups (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
            saved=app.db.execute('SELECT value FROM workspace_cleanups WHERE id=?',(args['id'],)).fetchone()
            value=json.loads(saved[0]) if saved else None
            if action=='workspace.deleteFiles' and value and value['token']==args['confirmationToken'] and value['status']!='preview':
                return {'accepted':True,'result':value,'effects':[]}
            if action=='workspace.deleteFilesPreview' and value and value['status'] in {'attempting','interrupted'}:
                raise ValueError('A previous cleanup needs inspection. Remaining files may be at '+value['cleanupPath']+'. No deletion was retried.')
            row,path,sessions,locks,ids=_scope(app,args['id'])
            if action=='workspace.deleteFilesPreview':
                inventory=await asyncio.to_thread(_inventory,path)
                value={'id':row['id'],'name':row['name'],'path':str(path),'inventory':inventory,'status':'preview',
                       'token':uuid.uuid4().hex,'expiresAt':time.time()+600,'chatCount':len(sessions),
                       'managed':path.is_relative_to(app.data_dir/'workspaces'),
                       'description':'Permanently delete all files, including Git history and uncommitted work. Chats and saved artifacts remain; links to these original files will stop working. Archive hides a workspace without deleting files. Stop any work outside Amplifier before confirming.'}
                app.db.execute('INSERT OR REPLACE INTO workspace_cleanups VALUES (?,?)',(row['id'],json.dumps(value)));app.db.commit()
                return {'accepted':True,'result':value,'effects':[]}
            if not value or value['token']!=args['confirmationToken'] or value['expiresAt']<time.time() or args['confirmationPath']!=value['path'] or str(path)!=value['path']:
                raise ValueError('Review this workspace again and confirm its exact folder path.')
            # Only idle workers may hold locks; refuse instead of stopping work.
            held=await asyncio.to_thread(_hold,locks)
            try:
                if await asyncio.to_thread(_inventory,path)!=value['inventory']:raise ValueError('Files changed since review. Review the folder again.')
                quarantine=path.with_name('.unified-cleanup-'+value['token'])
                if quarantine.exists() or quarantine.is_symlink():raise ValueError('The cleanup destination already exists. Nothing was changed.')
                value.update(status='attempting',cleanupPath=str(quarantine))
                app.db.execute('UPDATE workspace_cleanups SET value=? WHERE id=?',(json.dumps(value),row['id']));app.db.commit()
                try:
                    await asyncio.to_thread(_remove,path,value['inventory'],quarantine)
                    value.update(status='completed',deletedBytes=value['inventory']['fileBytes'])
                except Exception:
                    value.update(status='interrupted',message='Cleanup did not complete. Inspect the original and cleanup folders; no deletion will be replayed.',deletedBytes=None)
                if not path.exists():
                    row.update(archived=True,available=False)
                    from .conversation_library import organization
                    from .session_navigation import is_top_level
                    archived = organization(app.state)['archived']
                    for session in sessions:
                        if is_top_level(session):archived.setdefault(session['id'],time.time())
                app.db.execute('UPDATE workspace_cleanups SET value=? WHERE id=?',(json.dumps(value),row['id']))
                app._publish_changes(globals={'workspaces','conversationOrganization'},sessions={s['id'] for s in sessions})
                app.db.commit()
                return {'accepted':True,'result':value,'effects':[],**({'state':app.browser_state()} if include_state else {})}
            finally:
                for lock in reversed(held):lock.release()
    except (ValueError,OSError) as error:
        raise AppError(str(error),409) from None
