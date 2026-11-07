"""Bounded navigation queries over the shared effective history directory.

Filtering, ordering and counts run in SQLite. Only the requested page is decoded;
querying history never admits a session or starts its runtime.
"""
import json
from typing import Any

from .chat_navigation import ChatPageResult, PAGE_SIZE, valid_recent_limit, _matches
from .navigation_summary import path_labels


def _rows(directory, sql, params):
    cursor = directory.db.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return (dict(zip(names, row)) for row in cursor)


def _registry(state, archive=None):
    rows = [row for row in state.get('workspaces', [])
            if isinstance(row.get('path'), str) and row['path']
            and (row.get('available') is True if archive is None else
                 (row.get('available') is True or row.get('archived'))
                 and (archive == 'all' or bool(row.get('archived')) == (archive == 'archived')))]
    if archive is not None:
        from .workspace_navigation import _path
        rows = [row for row in rows if _path(row['path']) is not None]
    by_path = {row['path']: row for row in rows}
    return rows, by_path


def _base(directory, state, *, archive=None, public_ids=None, root_workspace=False):
    rows, by_path = _registry(state, archive)
    native_workspace = None
    if root_workspace and public_ids is None:
        available_ids = {row['id'] for row in rows}
        selected = state.get('selectedWorkspaceId')
        selected = selected if selected in available_ids else None
        native_workspace = (selected, [path for path, row in by_path.items() if row['id'] == selected])
        directory.db.create_function('directory_known_workspace', 1,
                                     lambda identity: int(identity in available_ids), deterministic=True)
    sql, params = directory.effective(state, public_ids=public_ids, native_root_workspace=native_workspace)
    directory.db.create_function('directory_lower', 1, lambda text: str(text or '').lower(), deterministic=True)
    directory.db.create_function('directory_match', 7,
        lambda query, *fields: int(_matches(fields, query)), deterministic=True)
    # These are host-global registry facts, already resident independently of
    # chat history. Resolve them once, rather than joining json_each for every
    # catalog row (catalog size × registry size, including repeated JSON parse).
    # The maps have the same ID/path precedence as the in-memory navigation
    # owner; all filtering, ordering, counting and paging remain in SQLite.
    registry = rows
    path_key = lambda value: value
    if archive is not None:
        from .workspace_navigation import _path
        from pathlib import PureWindowsPath
        from functools import lru_cache
        @lru_cache(maxsize=512)
        def normalized(value):
            path = _path(value)
            return ('windows:' + str(path).casefold() if isinstance(path,PureWindowsPath) else 'posix:' + str(path)) if path is not None else None
        paths = {}
        for row in rows:
            paths.setdefault(normalized(row['path']),row['path'])
        registry = [{**row, 'path':paths[normalized(row['path'])]} for row in rows]
        path_key = normalized
    by_id = {row['id']: row for row in registry}
    path_owners = {path_key(row['path']): row for row in registry}
    def workspace(field, identity, path, managed):
        row = by_id.get(identity)
        if row is None:
            row = path_owners.get(path_key(path))
        if field == 'id':
            return row['id'] if row else None
        if field == 'path':
            return row['path'] if row else path or ''
        return (row.get('name') or '') if row else 'No workspace' if managed else ''
    pins = {identity: position for position, identity in reversed(list(enumerate(state.get('pinnedSessionIds', []))))}
    archived = set(state.get('conversationOrganization', {}).get('archived', {}))
    directory.db.create_function('directory_workspace', 4, workspace, deterministic=True)
    directory.db.create_function('directory_pin', 1, pins.get, deterministic=True)
    directory.db.create_function('directory_archived', 1, lambda identity: int(identity in archived), deterministic=True)
    sql += """, navigation_rows AS (
      SELECT d.*,
        directory_workspace('id',workspace_id,workspace,managed) resolved_workspace_id,
        directory_workspace('path',workspace_id,workspace,managed) resolved_workspace,
        directory_workspace('name',workspace_id,workspace,managed) workspace_name,
        directory_pin(id) pin_position, directory_archived(id) archived
      FROM directory_rows d
    )"""
    return sql, params, rows


def _chat_query(directory, state, section, *, activity_counts=True):
    from .history_directory import PUBLIC_ID_BATCH
    pins = state.get('pinnedSessionIds', [])
    # Exact public IDs preserve canonical overlays and suppression. Larger pin
    # sets keep the original globally ordered query rather than page per batch.
    public_ids = pins if section == 'pinned' and len(pins) <= PUBLIC_ID_BATCH else None
    sql, params, workspaces = _base(directory, state, public_ids=public_ids,
                                   root_workspace=state.get('view', {}).get('navChatScope') != 'all')
    view = state.get('view', {})
    by_id = {row['id']: row for row in workspaces}
    selected = by_id.get(state.get('selectedWorkspaceId'))
    mode = 'all' if view.get('navChatScope') == 'all' else 'workspace'
    query = view.get('navFilter', '')
    query = query if isinstance(query, str) else ''
    scope = {'mode': mode, 'workspaceId': selected['id'] if mode == 'workspace' and selected else None,
             'filter': query, 'selectedSessionId': state.get('selectedSessionId')}
    sort = view.get('navSort', 'activity')
    if sort != 'activity': scope['sort'] = sort
    location = view.get('navLocationFilter', 'all') if mode == 'all' else 'all'
    if location != 'all': scope['locationFilter'] = location
    archive = view.get('navArchive', 'active')
    if archive != 'active': scope['archive'] = archive
    status = view.get('navStatusFilter', 'all')
    if status != 'all': scope['statusFilter'] = status
    where = ["session_kind='root'", '(resolved_workspace_id IS NOT NULL OR managed)']
    if mode == 'workspace':
        where += ['NOT managed', 'resolved_workspace_id=?']
        params.append(selected['id'] if selected else None)
    if location == 'managed': where.append('managed')
    if archive != 'all': where.append('archived' if archive == 'archived' else 'NOT archived')
    if query:
        where.append('directory_match(?,title,description,id,COALESCE(native_id,id),resolved_workspace,workspace_name)')
        params.append(query)
    if section:
        scope['section'] = section
        if section == 'pinned':
            where.append('pin_position IS NOT NULL')
        if section in {'recent', 'shortcuts', 'search'}:
            scope['showAgentCreated'] = view.get('navShowAgentCreated') is True
            if not scope['showAgentCreated']:
                where.append('(NOT agent_created OR id=?)')
                params.append(state.get('selectedSessionId'))
    sql += ', eligible AS (SELECT * FROM navigation_rows WHERE ' + ' AND '.join(where) + ')'
    counts = dict.fromkeys(('attention', 'working', 'unread', 'idle'), 0)
    total = 0
    if activity_counts:
        for kind, count in directory.db.execute(sql + ' SELECT activity_kind,COUNT(*) FROM eligible GROUP BY activity_kind', params):
            if kind in counts: counts[kind] = count
            # Include every group, even an unfamiliar activity kind. The public
            # four counters and the complete filtered count have distinct jobs.
            if status == 'all' or kind == status: total += count
    if status != 'all':
        sql += ', filtered AS (SELECT * FROM eligible WHERE activity_kind=?)'
        params.append(status)
    else:
        sql += ', filtered AS (SELECT * FROM eligible)'
    ordering = 'directory_lower(title)' if sort == 'name' else '-COALESCE(created,0)' if sort == 'created' else '-COALESCE(recent,0)'
    order = f'pin_position IS NULL,CASE WHEN pin_position IS NOT NULL THEN pin_position END,CASE WHEN pin_position IS NULL THEN {ordering} END,ordinal,id'
    if section in {'recent', 'shortcuts'}:
        order = f'{ordering},ordinal,id'
    return sql, params, scope, counts, order, workspaces, total


def _chat_row(directory, row, labels):
    row = dict(row)
    metadata = directory.decode(row)
    managed = bool(row['managed'])
    result = {'id': row['id'], 'title': row['title'] or 'Untitled conversation',
              'description': row['description'] or '',
              **{key: metadata[key] for key in ('titleSource','nativeNameSource','autoName','naming','configurationBusy') if key in metadata},
              'status': row['status'] or 'idle', 'workspace': row['resolved_workspace'],
              'workspaceId': row['resolved_workspace_id'], 'workspaceName': row['workspace_name'],
              'workspaceLabel': 'No workspace' if managed else labels[row['resolved_workspace']],
              'activity': {'kind': row['activity_kind'], 'label': row['activity_label']},
              'runtimeSessionId': metadata.get('runtimeSessionId') or metadata.get('nativeIdentity'),
              'createdAt': row['created'], 'pinned': row['pin_position'] is not None,
              'agentCreated': bool(row['agent_created']), 'recentActivityAt': row['recent'] or 0}
    if row['archived']: result['archived'] = True
    if managed: result['location'] = {'kind': 'managed'}
    return result


def chats(directory, state: dict[str, Any], *, section: str | None = None) -> ChatPageResult:
    sql, params, scope, counts, order, workspaces, total = _chat_query(directory, state, section)
    view = state.get('view', {})
    result: ChatPageResult
    if section == 'shortcuts':
        limit = view.get('navRecentLimit', 20)
        limit = limit if valid_recent_limit(limit) else 20
        scope.update(limit=limit, viewRevision=view.get('navRecentRevision', 0))
        start, end = 0, min(total, limit)
        result = {'total': total, 'remaining': total-end, 'limit': limit, 'start': start, 'end': end,
                  'scope': scope, 'dataRevision': state.get('revision', 0), 'activityCounts': counts, 'items': []}
    else:
        size = (40 if total > 50 else 50) if section else PAGE_SIZE
        saved = view.get('navChatPage')
        matched = isinstance(saved, dict) and saved.get('sort', 'activity') == scope.get('sort', 'activity') and all(saved.get(k) == v for k,v in scope.items())
        saved_page = matched and type(saved.get('index')) is int
        inferred = 0
        if not saved_page and scope['mode'] == 'workspace' and section not in {'recent', 'pinned'} and scope['selectedSessionId']:
            found = directory.db.execute(sql + f', ranked AS (SELECT id,ROW_NUMBER() OVER (ORDER BY {order})-1 position FROM filtered) SELECT position FROM ranked WHERE id=?', [*params, scope['selectedSessionId']]).fetchone()
            if found: inferred = found[0] // size
        requested = saved['index'] if saved_page else inferred
        if section == 'pinned': requested = view.get('navPinnedPage', 0)
        pages = max(1, (total+size-1)//size)
        index = max(0, min(pages-1, requested))
        start, end = index*size, min(total, (index+1)*size)
        result = {'total':total,'index':index,'pages':pages,'start':start,'end':end,'scope':scope,'activityCounts':counts,'items':[]}
    labels = path_labels(row['path'] for row in workspaces)
    rows = _rows(directory, sql + f' SELECT * FROM filtered ORDER BY {order} LIMIT ? OFFSET ?', [*params,end-start,start])
    result['items'] = [_chat_row(directory,row,labels) for row in rows]
    return result


def workspace_counts(directory, state, archive='active'):
    sql, params, _ = _base(directory, state, archive=archive)
    sql += " SELECT resolved_workspace,COUNT(*),SUM(unread),MAX(recent),activity_kind,activity_label FROM navigation_rows WHERE session_kind='root' AND NOT archived AND resolved_workspace_id IS NOT NULL GROUP BY resolved_workspace,activity_kind,activity_label"
    result = {}
    for path, count, unread, recent, kind, label in directory.db.execute(sql, params):
        entry = result.setdefault(path, {'chatCount':0,'unread':0,'recentActivityAt':0,
                                         'activityCounts':dict.fromkeys(('attention','working','unread','idle'),0)})
        entry['chatCount'] += count
        entry['unread'] += unread or 0
        entry['recentActivityAt'] = max(entry['recentActivityAt'],recent or 0)
        entry['activityCounts'][kind] = entry['activityCounts'].get(kind,0)+count
        if kind == 'attention':
            category = {'Work blocked':'blocked','Approval requested':'decision','Answer requested':'decision'}.get(label,'error')
            entry['activityCounts'][category] = entry['activityCounts'].get(category,0)+count
    return result


def root_count(directory, state):
    sql, params = directory.effective(state)
    return directory.db.execute(sql + " SELECT COUNT(*) FROM directory_rows WHERE session_kind='root'", params).fetchone()[0]


def child_count(directory, state, parent):
    """Count canonical direct children without constructing a hidden page."""
    sql, params = directory.effective(state, children_of=parent)
    return directory.db.execute(sql+' SELECT COUNT(*) FROM directory_rows',params).fetchone()[0]


def children(directory, state, parent, query='', index=0):
    sql, params = directory.effective(state, children_of=parent)
    sql += ', child_rows AS (SELECT * FROM directory_rows)'
    unfiltered = directory.db.execute(sql+' SELECT COUNT(*) FROM child_rows',params).fetchone()[0]
    if query:
        directory.db.create_function('directory_child_match',5,lambda q,*fields:int(_matches(fields,q)),deterministic=True)
        sql += ', matched AS (SELECT * FROM child_rows WHERE directory_child_match(?,title,description,id,native_id))'
        params.append(query)
    else: sql += ', matched AS (SELECT * FROM child_rows)'
    total = (directory.db.execute(sql+' SELECT COUNT(*) FROM matched',params).fetchone()[0]
             if query else unfiltered)
    pages = max(1,(total+49)//50)
    index = max(0,min(pages-1,index)) if type(index) is int else 0
    start,end = index*50,min(total,(index+1)*50)
    from .browser_state import summary
    rows = _rows(directory, sql+' SELECT * FROM matched ORDER BY recent DESC,ordinal,id LIMIT ? OFFSET ?',[*params,end-start,start])
    return {'items':[summary(directory.decode(row)) for row in rows],'total':total,'unfilteredTotal':unfiltered,
            'index':index,'pages':pages,'start':start,'end':end,'scope':{'sessionId':parent.get('id'),'filter':query}}


def pinned_navigation(directory, state, workspaces):
    """Combine typed pin identities in SQL before decoding the visible page."""
    from .pinned_navigation import ordered_ids
    scoped = {**state, 'view': {**state.get('view', {}), 'navStatusFilter': 'all'}}
    sql, params, _, _, _, registrations, _ = _chat_query(directory, scoped, 'pinned', activity_counts=False)
    workspace_rows = {f'workspace:{row["workspaceId"]}': row for row in workspaces}
    sql += """, pin_order AS (SELECT value id,CAST(key AS INTEGER) position FROM json_each(?)),
      pin_members AS (SELECT 'session:'||id id FROM filtered UNION ALL SELECT value id FROM json_each(?)),
      pin_rows AS (SELECT o.id,o.position FROM pin_order o JOIN pin_members m ON m.id=o.id)"""
    params += [json.dumps(ordered_ids(state)), json.dumps(list(workspace_rows))]
    total = directory.db.execute(sql+' SELECT COUNT(*) FROM pin_rows',params).fetchone()[0]
    pages = max(1,(total+49)//50)
    index = max(0,min(pages-1,state.get('view',{}).get('navPinnedPage',0)))
    start,end = index*50,min(total,(index+1)*50)
    ids = [row[0] for row in directory.db.execute(sql+' SELECT id FROM pin_rows ORDER BY position LIMIT ? OFFSET ?',[*params,end-start,start])]
    chat_ids = [key[len('session:'):] for key in ids if key.startswith('session:')]
    labels = path_labels(row['path'] for row in registrations)
    rows = _rows(directory, sql+' SELECT * FROM filtered WHERE id IN (SELECT value FROM json_each(?))',[*params,json.dumps(chat_ids)])
    chats = {f'session:{row["id"]}':_chat_row(directory,row,labels) for row in rows}
    return {'items':[{'id':key,'kind':key.split(':',1)[0],'row':chats[key] if key in chats else workspace_rows[key]} for key in ids],
            'total':total,'index':index,'pages':pages,'start':start,'end':end}


def archived_count(directory, state):
    archived = state.get('conversationOrganization',{}).get('archived',{})
    if not archived:
        return 0
    from .history_directory import PUBLIC_ID_BATCH
    identities = list(archived)
    excluded = 0
    for start in range(0, len(identities), PUBLIC_ID_BATCH):
        sql, params = directory.effective(state, public_ids=identities[start:start+PUBLIC_ID_BATCH])
        excluded += directory.db.execute(sql+" SELECT COUNT(*) FROM directory_rows WHERE session_kind!='root'", params).fetchone()[0]
    return len(archived)-excluded
