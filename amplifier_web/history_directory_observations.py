"""Semantic public-fact revisions, updated at each directory owner's boundary.

Only changed/admitted rows are decoded. Native revisions come from its scalar
journal; global pins/read receipts join indexed identities and fingerprints.
"""
import hashlib
import json

COLLECTIONS = ('sessions', 'attention', 'activity', 'navigation', 'workspace', 'root_navigation')
NAVIGATION_FIELDS = ('id','title','description','status','workspace','workspaceId','location',
    'titleSource','nativeNameSource','autoName','naming','configurationBusy',
    'runtimeSessionId','nativeIdentity','createdAt','sessionKind','parentId',
    'nativeParentId','historyManaged','workspaceAvailable','nativeAvailable')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS history_observation_rows '
               '(id TEXT PRIMARY KEY,sessions TEXT,attention TEXT,activity TEXT,navigation TEXT)')
    if 'navigation' not in {row[1] for row in db.execute('PRAGMA table_info(history_observation_rows)')}:
        db.execute('ALTER TABLE history_observation_rows ADD COLUMN navigation TEXT')
    if 'workspace' not in {row[1] for row in db.execute('PRAGMA table_info(history_observation_rows)')}:
        # Old rows derive this small fact from their existing public facts on a
        # keyed read. No upgrade-wide summary/body decode or rewrite is needed.
        db.execute('ALTER TABLE history_observation_rows ADD COLUMN workspace TEXT')
    if 'root_navigation' not in {row[1] for row in db.execute('PRAGMA table_info(history_observation_rows)')}:
        db.execute('ALTER TABLE history_observation_rows ADD COLUMN root_navigation TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS history_observation_revisions '
               '(collection TEXT PRIMARY KEY,revision INTEGER NOT NULL)')
    db.executemany('INSERT OR IGNORE INTO history_observation_revisions VALUES(?,0)',
                   [(key,) for key in COLLECTIONS])


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def membership_part(session, navigation, native_project=None):
    row = dict(zip(NAVIGATION_FIELDS, navigation[0]))
    from .session_identity import native_id, project
    return [session['id'], session['role'], native_id(row),
            project({**row, 'nativeProject': native_project}), bool(row.get('historyManaged'))]


def workspace_part(session, workspace, notices, navigation, native_project=None):
    row = dict(zip(NAVIGATION_FIELDS, navigation[0]))
    return encoded([membership_part(session, navigation, native_project),
        [workspace, row.get('workspaceId'), navigation[2], navigation[3],
         [(item['id'], item['fingerprint']) for item in notices]] if session['role'] == 'root' else None])


def root_navigation_part(session, notices, navigation, native_project=None):
    return encoded([membership_part(session, navigation, native_project),
        [navigation, [(item['id'], item['fingerprint']) for item in notices]]
        if session['role'] == 'root' else None])


def complete(directory, saved, identity):
    if saved is None:
        return None
    if all(value is not None for value in saved[4:]):
        return tuple(saved)
    if saved[3] is None:
        return (*saved[:4], *(value if value is not None else encoded(saved[:4]) for value in saved[4:]))
    session, workspace = json.loads(saved[0])
    _, notices = json.loads(saved[1])
    navigation = json.loads(saved[3])
    owner = directory.db.execute('SELECT native_project FROM history_local_rows WHERE id=?', (identity,)).fetchone()
    project = owner[0] if owner else None
    return (*saved[:4],
        saved[4] if saved[4] is not None else workspace_part(session, workspace, notices, navigation, project),
        saved[5] if saved[5] is not None else root_navigation_part(session, notices, navigation, project))


def parts(row, *, blocked=False):
    from .client_observations import Observations
    facts = Observations.row_facts(None,row,{},frozenset())
    session,workspace,notices = facts
    from .chat_navigation import navigation_activity, timestamp
    from .navigation_summary import activity
    defaults = {'description':'','status':'idle','sessionKind':
                'internal' if session['role']=='internal' else 'root' if session['role']=='root' else 'worker'}
    navigation = ([bool(row.get(key,True)) if key=='workspaceAvailable' else
                   timestamp(row.get(key)) if key=='createdAt' and row.get(key) is not None else
                   row.get(key) if row.get(key) is not None else defaults.get(key)
                   for key in NAVIGATION_FIELDS],session['agentCreated'],
                  navigation_activity(row),activity(row,False,blocked=blocked))
    return (*tuple(encoded(value) for value in ((session,workspace),
        (session['role'],notices),Observations.count_facts(facts),navigation)),
        workspace_part(session, workspace, notices, navigation, row.get('nativeProject')),
        root_navigation_part(session, notices, navigation, row.get('nativeProject')))


def native_parts(directory, row):
    """The prior native public row for an overlay, without admitting its body."""
    if not directory._native_path:
        return None
    from .session_identity import native_id, project
    saved = directory.db.execute('SELECT identity,project,public_id,kind,title,workspace_path,workspace_id,recent,parent_id '
        'FROM history_native.native_rows WHERE namespace=? AND project=? AND identity=?',
        (directory._native_namespace,project(row),native_id(row))).fetchone()
    if saved is None:
        return None
    identity,project_id,public,kind,title,workspace,workspace_id,recent,parent = saved
    duplicated = directory.db.execute('SELECT COUNT(*) FROM history_native.native_rows '
        'WHERE namespace=? AND identity=?',(directory._native_namespace,identity)).fetchone()[0]>1
    effective_id = public if duplicated else identity
    if row['id'] != effective_id:
        return None
    baseline = {'id':effective_id,'title':title,'sessionKind':kind,'status':'idle','workspace':workspace}
    return (*parts(baseline)[:4], *parts({**baseline, 'workspaceId': workspace_id,
        'nativeIdentity': identity, 'nativeProject': project_id, 'historyManaged': True,
        'parentId': parent, 'nativeParentId': parent,
        'recentActivityAt': recent})[4:])


def saved_parts(directory,row):
    saved = directory.db.execute('SELECT sessions,attention,activity,navigation,workspace,root_navigation FROM history_observation_rows WHERE id=?',
                                  (row['id'],)).fetchone()
    return complete(directory, saved, row['id']) if saved else native_parts(directory,row)


def sync(directory,row,prior_summary_or_none=None,state=None,*,deleted=False):
    identity = row if isinstance(row,str) else row['id']
    previous = complete(directory, directory.db.execute('SELECT sessions,attention,activity,navigation,workspace,root_navigation FROM history_observation_rows WHERE id=?',
                                     (identity,)).fetchone(), identity)
    if previous is None and not isinstance(row,str):
        previous = native_parts(directory,row)
    from .navigation_summary import task_blocked
    blocked = False
    if not deleted:
        blocked = task_blocked(state or {}, identity)
        if identity not in (state or {}).get('runtimeControl', {}):
            saved = directory.db.execute("SELECT json_extract(value, '$.\"task.get\".task.status') FROM state_records WHERE kind='runtime' AND id=?", (identity,)).fetchone()
            blocked = bool(saved and saved[0] == 'blocked')
    current = None if deleted else parts(row, blocked=blocked)
    if deleted:
        directory.db.execute('DELETE FROM history_observation_rows WHERE id=?',(identity,))
    else:
        directory.db.execute('INSERT OR REPLACE INTO history_observation_rows (id,sessions,attention,activity,navigation,workspace,root_navigation) VALUES(?,?,?,?,?,?,?)',
                             (identity,*current))
    for index,key in enumerate(COLLECTIONS):
        if (previous[index] if previous else None)!=(current[index] if current else None):
            directory.db.execute('UPDATE history_observation_revisions SET revision=revision+1 WHERE collection=?',(key,))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def visible_ids(directory, state, identities, *, notices=False, roots=False):
    """Resolve only requested public owners, retaining canonical suppression."""
    from .history_directory import PUBLIC_ID_BATCH
    identities = sorted(set(identities))
    result = set()
    for start in range(0, len(identities), PUBLIC_ID_BATCH):
        # Membership checks need only identity and role. Projecting full rows
        # makes SQLite materialize summaries and notice/activity joins for every
        # read-receipt owner on a changed browser snapshot.
        fields = {'id', 'session_kind'} if notices or roots else {'id'}
        sql, params = directory.effective(state, public_ids=identities[start:start + PUBLIC_ID_BATCH],
                                        fields=fields)
        result.update(row[0] for row in directory.db.execute(sql +
            " SELECT id FROM directory_rows" + (" WHERE session_kind='root'" if roots else
                " WHERE session_kind!='internal'" if notices else ''), params))
    return result


def revision(directory,state):
    # effective freezes each admitted row once; a snapshot uses its already
    # frozen overlay and pinned DB views, never mutable service state.
    sql,params = directory.effective(state, fields={'id'})
    native = (0,0,0,0,0)
    if directory._native_path:
        saved = directory.db.execute('SELECT observation_sessions,observation_membership,navigation,workspace,root_navigation '
            'FROM history_native.native_revisions WHERE namespace=?',(directory._native_namespace,)).fetchone()
        native = tuple(saved) if saved else native
    local = dict(directory.db.execute('SELECT collection,revision FROM history_observation_revisions'))
    # Read the small owner generations before consulting this one-entry memo.
    # Selection and unrelated app saves do not change directory facts. The SQL
    # inputs include current live overlays, read receipts and deletion filters;
    # in-place changes therefore cannot hide behind an unchanged app revision.
    # data_version also catches external writes/rebuilt native generations.
    layout = directory.db.execute('SELECT revision FROM history_directory_layout WHERE version=1').fetchone()
    versions = (directory.db.execute('PRAGMA main.data_version').fetchone()[0],
                directory.db.execute('PRAGMA history_native.data_version').fetchone()[0]
                if directory._native_path else None)
    # The scalar SQL no longer carries retained summary bodies. Their prepared
    # overlay generation still fences public facts present only in the summary
    # (for example location/naming), including in-place edits without a save.
    stamp = (directory._native_path, native, tuple(sorted(local.items())), directory._live_revision,
             layout[0] if layout else None, versions, sql, tuple(params),
             tuple(state.get('pinnedSessionIds', ())))
    reusable = directory.snapshot or not directory.db.in_transaction
    memo = getattr(directory, '_observation_revision_memo', None)
    if reusable and memo is not None and memo[0] == stamp:
        return dict(memo[1])
    from .native_query_receipts import receipt as native_query_receipt
    effective_native = {key: native_query_receipt(directory.db, directory._native_namespace, key, directory._live_values)
                        for key in ('root_navigation', 'workspace')} if directory._native_path else {}
    overlays = {key:[] for key in COLLECTIONS}
    from .history_directory import COLUMNS
    cached = getattr(directory, '_observation_parts', {})
    retained = {}
    for identity,record in directory._live_values.items():
        raw = (record[COLUMNS.index('summary')],record[COLUMNS.index('base_activity_label')])
        prior = cached.get(identity)
        if prior is not None and prior[0] == raw:
            current = prior[1]
            # Identity fields suffice for an indexed native baseline lookup.
            row = {'id':identity,'nativeProject':record[COLUMNS.index('native_project')],
                   'nativeIdentity':record[COLUMNS.index('native_id')]}
        else:
            row = directory.decode(record[:len(COLUMNS)])
            current = parts(row, blocked=raw[1]=='Work blocked')
        retained[identity] = (raw,current)
        previous = saved_parts(directory,row)
        for index,key in enumerate(COLLECTIONS):
            if previous is None or current[index]!=previous[index]:
                overlays[key].append((row['id'],current[index]))
    directory._observation_parts = retained
    pins = sorted(visible_ids(directory, state, state.get('pinnedSessionIds', ())))
    read, workspace_read = [], []
    if state.get('attentionRead'):
        # Notices belong to local owners. Resolve their canonical visibility and
        # role in the same scalar join, using the already materialized receipts.
        # Repeating exact-ID queries for every batch reparses the live overlay
        # and receipts, twice, even though both answers come from the same rows.
        notice_sql, notice_params = directory.effective(state, fields={'id', 'session_kind'}, local_only=True)
        for owner, key, fingerprint, kind in directory.db.execute(notice_sql + '''
                SELECT n.owner,n.key,n.fingerprint,d.session_kind FROM directory_notices n
                JOIN read_receipts r ON r.key=n.key AND r.value=n.fingerprint
                JOIN directory_rows d ON d.id=n.owner WHERE d.session_kind!='internal'
                ORDER BY n.owner,n.key,n.fingerprint''', notice_params):
            item = (owner, key, fingerprint)
            read.append(item)
            if kind == 'root':
                workspace_read.append(item)
    from .managed_deletion import CatalogSuppression
    suppression = digest([sorted(set(state.get('hiddenNativeSessions',[]))),
                          sorted(set(state.get('hiddenNativeWorkspaces',[]))),
                          CatalogSuppression(directory.db).signature()])
    result = {key:(directory._native_namespace,tuple(effective_native[key]) if key in effective_native else native[2 if key=='navigation' else 0 if key=='sessions' else 1],local.get(key,0),
                 digest(sorted(overlays[key])),digest(pins if key=='sessions' else workspace_read if key in {'workspace','root_navigation'} else read),suppression)
            for key in COLLECTIONS}
    # Never remember uncommitted mutable facts: rollback followed by another
    # transaction may reuse the same numeric generation for different content.
    if reusable:
        directory._observation_revision_memo = (stamp, dict(result))
    return result
