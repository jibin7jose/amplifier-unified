"""Rebuildable native metadata cache. Canonical history is never written here."""
from pathlib import Path
import hashlib
import json
import sqlite3
from contextlib import contextmanager
import fnmatch
import uuid
from itertools import islice
from collections import Counter


SCALAR_COLUMNS = ('public_id', 'workspace_id', 'workspace_path', 'title', 'description',
                  'title_fold', 'description_fold', 'created', 'recent', 'updated',
                  'parent_id', 'kind', 'purpose', 'available', 'ordinal', 'hidden_id',
                  'has_name', 'name_source', 'auto_name', 'name_revision', 'name_policy_revision',
                  'transcript_mtime', 'transcript_size', 'transcript_available')
MEMBERSHIP_FIELDS = ('id', 'nativeIdentity', 'nativeProject', 'sessionKind', 'parentId', 'workspaceId', 'workspace')
OBSERVATION_FIELDS = (*MEMBERSHIP_FIELDS, 'title', 'name', 'agentCreated', 'nameRevision')
NAVIGATION_FIELDS = (*OBSERVATION_FIELDS, 'description', 'createdAt', 'recentActivityAt',
                     'bundle', 'sessionPurpose', 'readOnlyReason', 'canResume', 'nameSource', 'autoName',
                     'namePolicyRevision')


def normalize_availability(row, available):
    """Apply the current workspace fact to one decoded canonical summary."""
    missing = 'The original workspace folder is no longer available.'
    if (row.get('sessionKind', 'root') == 'root' and row.get('workspace')
            and row.get('readOnlyReason') in (None, missing)):
        reason = missing if not available else (
            None if row.get('bundle') else 'This session does not record a resumable bundle.')
        row = {**row, 'readOnlyReason': reason, 'canResume': reason is None}
    return row


def row_scalars(row, workspace):
    title, description = row.get('title') or row.get('name') or '', row.get('description') or ''
    revision = row.get('transcriptRevision')
    if not (isinstance(revision, (list, tuple)) and len(revision) == 2
            and all(type(value) is int and value >= 0 for value in revision)):
        revision = (None, None)
    return (row.get('id'), row.get('workspaceId'), row.get('workspace'), title, description,
            title.casefold(), description.casefold(), row.get('createdAt', 0),
            row.get('recentActivityAt', 0), row.get('updatedAt', 0), row.get('parentId'),
            row.get('sessionKind', 'root'), row.get('sessionPurpose'),
            int(bool(workspace.get('available'))), row['nativeProject'] + '\0' + row['nativeIdentity'],
            uuid.uuid5(uuid.NAMESPACE_URL, f"amplifier-native:{row['nativeProject']}/{row['nativeIdentity']}").hex,
            int(bool(row.get('name'))), row.get('nameSource'),
            int(bool(row.get('autoName', row.get('nameSource') != 'manual'))),
            row.get('nameRevision', 0), row.get('namePolicyRevision', 0),
            *revision, int(bool(row.get('transcriptAvailable'))))


def tuples(value):
    if isinstance(value, list):
        return tuple(tuples(item) for item in value)
    return value


class NativeCatalog:
    VERSION = 2

    def __init__(self, path, home, *, diagnostics=None):
        self.path = Path(path)
        self.home = Path(home)
        self.namespace = hashlib.sha256(str(self.home).encode()).hexdigest()
        self.stop = None
        self.diagnostics = diagnostics
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            file_sources_missing = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_file_sources'").fetchone() is None
            db.executescript('''
                CREATE TABLE IF NOT EXISTS native_cache_version(version INTEGER);
                CREATE TABLE IF NOT EXISTS native_projects(namespace TEXT, project TEXT, value TEXT,
                    PRIMARY KEY(namespace,project));
                CREATE TABLE IF NOT EXISTS native_rows(namespace TEXT, project TEXT, identity TEXT, value TEXT,
                    PRIMARY KEY(namespace,project,identity));
                CREATE TABLE IF NOT EXISTS native_files(namespace TEXT, path TEXT, value TEXT,
                    PRIMARY KEY(namespace,path));
                CREATE TABLE IF NOT EXISTS native_file_sources(namespace TEXT, path TEXT, signature TEXT, valid INTEGER,
                    PRIMARY KEY(namespace,path));
                CREATE TABLE IF NOT EXISTS native_revisions(namespace TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL DEFAULT 0, observation_sessions INTEGER NOT NULL DEFAULT 0,
                    observation_membership INTEGER NOT NULL DEFAULT 0, navigation INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS native_recovery_stages(namespace TEXT, project TEXT, token TEXT,
                    PRIMARY KEY(namespace,project), UNIQUE(namespace,token));
                CREATE TABLE IF NOT EXISTS native_recovery_seen(namespace TEXT, token TEXT, identity TEXT,
                    PRIMARY KEY(namespace,token,identity));
            ''')
            if 'navigation' not in {column[1] for column in db.execute('PRAGMA table_info(native_revisions)')}:
                db.execute('ALTER TABLE native_revisions ADD COLUMN navigation INTEGER NOT NULL DEFAULT 0')
            if 'workspace' not in {column[1] for column in db.execute('PRAGMA table_info(native_revisions)')}:
                db.execute('ALTER TABLE native_revisions ADD COLUMN workspace INTEGER NOT NULL DEFAULT 0')
            if 'root_navigation' not in {column[1] for column in db.execute('PRAGMA table_info(native_revisions)')}:
                db.execute('ALTER TABLE native_revisions ADD COLUMN root_navigation INTEGER NOT NULL DEFAULT 0')
            db.execute('INSERT OR IGNORE INTO native_revisions(namespace) VALUES(?)', (self.namespace,))
            from .native_query_receipts import initialize as initialize_query_receipts
            initialize_query_receipts(db)
            row = db.execute('SELECT version FROM native_cache_version').fetchone()
            if row is None:
                db.execute('INSERT INTO native_cache_version VALUES(?)', (self.VERSION,))
            elif row[0] != self.VERSION:
                if row[0] != 1:
                    raise ValueError('Unsupported native metadata cache; original files were preserved')
            columns = {row[1] for row in db.execute('PRAGMA table_info(native_rows)')}
            if any(name not in columns for name in SCALAR_COLUMNS) or (row is not None and row[0] == 1):
                for name in SCALAR_COLUMNS:
                    kind = 'REAL' if name in {'created', 'recent', 'updated'} else 'INTEGER' if name in {'available', 'has_name', 'auto_name', 'name_revision', 'name_policy_revision', 'transcript_mtime', 'transcript_size', 'transcript_available'} else 'TEXT'
                    if name not in columns:
                        db.execute(f'ALTER TABLE native_rows ADD COLUMN {name} {kind}')
                if any(name not in columns for name in SCALAR_COLUMNS[:-3]) or (row is not None and row[0] == 1):
                    # A one-time version migration builds SQL query facts. Ordinary
                    # starts never decode every cached row into Python objects.
                    db.create_function('casefold', 1, lambda value: (value or '').casefold())
                    db.create_function('hidden_identity', 2, lambda project, identity:
                        uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-native:{project}/{identity}').hex)
                    db.execute('''UPDATE native_rows SET
                        public_id=json_extract(value,'$.id'), workspace_id=json_extract(value,'$.workspaceId'),
                        workspace_path=json_extract(value,'$.workspace'),
                        title=coalesce(json_extract(value,'$.title'),json_extract(value,'$.name'),''),
                        description=coalesce(json_extract(value,'$.description'),''),
                        title_fold=casefold(coalesce(json_extract(value,'$.title'),json_extract(value,'$.name'),'')),
                        description_fold=casefold(json_extract(value,'$.description')),
                        created=coalesce(json_extract(value,'$.createdAt'),0),
                        recent=coalesce(json_extract(value,'$.recentActivityAt'),0),
                        updated=coalesce(json_extract(value,'$.updatedAt'),0),
                        parent_id=json_extract(value,'$.parentId'), kind=coalesce(json_extract(value,'$.sessionKind'),'root'),
                        purpose=json_extract(value,'$.sessionPurpose'), ordinal=project||char(0)||identity,
                        hidden_id=hidden_identity(project,identity),
                        has_name=length(coalesce(json_extract(value,'$.name'),''))>0,
                        name_source=json_extract(value,'$.nameSource'),
                        auto_name=coalesce(json_extract(value,'$.autoName'),json_extract(value,'$.nameSource') IS NOT 'manual'),
                        name_revision=coalesce(json_extract(value,'$.nameRevision'),0),
                        name_policy_revision=coalesce(json_extract(value,'$.namePolicyRevision'),0),
                        transcript_mtime=CASE WHEN json_type(value,'$.transcriptRevision[0]')='integer'
                            THEN json_extract(value,'$.transcriptRevision[0]') END,
                        transcript_size=CASE WHEN json_type(value,'$.transcriptRevision[1]')='integer'
                            THEN json_extract(value,'$.transcriptRevision[1]') END,
                        transcript_available=coalesce(json_extract(value,'$.transcriptAvailable'),0),
                        available=coalesce((SELECT json_extract(p.value,'$.workspace.available') FROM native_projects p
                            WHERE p.namespace=native_rows.namespace AND p.project=native_rows.project),0)''')
                else:
                    # The additive source-fact upgrade does not recompute titles,
                    # identity UUIDs or any unrelated existing navigation fact.
                    db.execute("""UPDATE native_rows SET
                        transcript_mtime=CASE WHEN json_type(value,'$.transcriptRevision[0]')='integer'
                            THEN json_extract(value,'$.transcriptRevision[0]') END,
                        transcript_size=CASE WHEN json_type(value,'$.transcriptRevision[1]')='integer'
                            THEN json_extract(value,'$.transcriptRevision[1]') END,
                        transcript_available=coalesce(json_extract(value,'$.transcriptAvailable'),0)""")
            if file_sources_missing:
                # Explicit one-time cache migration. Warm reconciliation reads
                # these small facts without parsing saved metadata payloads.
                db.execute('''INSERT INTO native_file_sources
                    SELECT namespace,path,json_extract(value,'$[0]'),
                        coalesce(json_extract(value,'$[2]')=0 AND
                        (json_extract(value,'$[1]._nativeFieldsVersion')=2 OR json_extract(value,'$[1]')='{}'),0)
                    FROM native_files''')
            db.executescript('''
                CREATE INDEX IF NOT EXISTS native_rows_public ON native_rows(namespace,public_id);
                CREATE INDEX IF NOT EXISTS native_rows_identity ON native_rows(namespace,identity,project);
                CREATE INDEX IF NOT EXISTS native_rows_hidden ON native_rows(namespace,hidden_id);
                CREATE INDEX IF NOT EXISTS native_rows_workspace ON native_rows(namespace,workspace_id,kind,recent DESC,public_id DESC);
                CREATE INDEX IF NOT EXISTS native_rows_workspace_path ON native_rows(namespace,workspace_path,kind);
                CREATE INDEX IF NOT EXISTS native_rows_recent ON native_rows(namespace,kind,recent DESC,public_id DESC);
                CREATE INDEX IF NOT EXISTS native_rows_parent ON native_rows(namespace,project,parent_id);
                CREATE INDEX IF NOT EXISTS native_rows_parent_workspace ON native_rows(namespace,parent_id,workspace_path);
            ''')
            db.execute('UPDATE native_cache_version SET version=?', (self.VERSION,))
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.create_function('native_match', 2, lambda value, pattern: fnmatch.fnmatchcase(value or '', pattern))
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _workspace(project, raw):
        value = json.loads(raw)
        workspace = value.get('workspace') if isinstance(value, dict) else None
        if (not isinstance(workspace, dict) or workspace.get('nativeProject') != project
                or type(workspace.get('available')) is not bool
                or (workspace.get('path') is not None and
                    (not isinstance(workspace['path'], str) or not Path(workspace['path']).is_absolute()))):
            raise ValueError('Invalid native metadata cache')
        return workspace

    @staticmethod
    def _row(project, identity, raw):
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get('nativeProject') != project
                or value.get('nativeIdentity') != identity):
            raise ValueError('Invalid native metadata cache')
        return value

    def load_projects(self):
        """Small project summaries only; session/file metadata stays in SQLite."""
        with self.connect() as db:
            result = {}
            for project, raw in db.execute('SELECT project,value FROM native_projects WHERE namespace=?', (self.namespace,)):
                self._check_running()
                result[project] = {'workspace': self._workspace(project, raw),
                                   'sessions': CatalogRows(self, project)}
            return result

    def get(self, project, identity):
        with self.connect() as db:
            row = db.execute('SELECT value,available FROM native_rows WHERE namespace=? AND project=? AND identity=?',
                             (self.namespace, project, identity)).fetchone()
        return normalize_availability(self._row(project, identity, row[0]), row[1]) if row else None

    def lookup(self, identity):
        """All native/scoped aliases; callers must reject unscoped ambiguity."""
        with self.connect() as db:
            rows = db.execute('SELECT project,identity,value,available FROM native_rows WHERE namespace=? AND (identity=? OR public_id=?) ORDER BY project,identity',
                              (self.namespace, identity, identity)).fetchall()
        return [normalize_availability(self._row(*row[:3]), row[3]) for row in rows]

    def _where(self, *, project=None, workspace_id=None, kinds=None, query=None):
        clauses, args = ['namespace=?'], [self.namespace]
        for key, value in (('project', project), ('workspace_id', workspace_id)):
            if value is not None:
                clauses.append(key + '=?'); args.append(value)
        if kinds is not None:
            kinds = tuple(kinds)
            clauses.append('kind IN (' + ','.join('?' for _ in kinds) + ')' if kinds else '0')
            args.extend(kinds)
        if query:
            pattern = query.casefold()
            if not any(char in pattern for char in '*?['):
                pattern = '*' + pattern + '*'
            clauses.append("native_match(title_fold||' '||description_fold,?)")
            args.append(pattern)
        return ' AND '.join(clauses), args

    def count(self, **filters):
        where, args = self._where(**filters)
        with self.connect() as db:
            return db.execute('SELECT count(*) FROM native_rows WHERE ' + where, args).fetchone()[0]

    def count_identities(self, project, identities):
        """Count exact native/public aliases through scalar indexes, once each."""
        with self.connect() as db:
            return dict(db.execute('''WITH ids AS (SELECT value FROM json_each(?)), matched AS (
                SELECT rowid,kind FROM native_rows WHERE namespace=? AND project=?
                    AND identity IN (SELECT value FROM ids)
                UNION
                SELECT rowid,kind FROM native_rows WHERE namespace=? AND project=?
                    AND public_id IN (SELECT value FROM ids))
                SELECT kind,count(*) FROM matched GROUP BY kind''',
                (json.dumps(sorted(identities)), self.namespace, project, self.namespace, project)))

    def generations(self):
        """Scalar committed invalidations; transcript stamps do not hash a library."""
        with self.connect() as db:
            row = db.execute('SELECT revision,observation_sessions,observation_membership,navigation FROM native_revisions WHERE namespace=?',
                             (self.namespace,)).fetchone()
        return dict(zip(('revision', 'observation_sessions', 'observation_membership', 'navigation'), row))

    def page(self, *, offset=0, limit=100, order='recent', **filters):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('Native directory pages require a nonnegative offset and limit from 1 to 500')
        ordering = {'recent': 'recent DESC,public_id DESC', 'updated': 'updated DESC,public_id DESC',
                    'identity': 'project,identity'}.get(order)
        if ordering is None:
            raise ValueError('Unsupported native directory order')
        where, args = self._where(**filters)
        with self.connect() as db:
            rows = db.execute('SELECT project,identity,value,available FROM native_rows WHERE ' + where +
                              ' ORDER BY ' + ordering + ' LIMIT ? OFFSET ?', (*args, limit, offset)).fetchall()
        return [normalize_availability(self._row(*row[:3]), row[3]) for row in rows]

    def file(self, path):
        path = Path(path)
        if not path.is_relative_to(self.home / 'projects'):
            raise ValueError('Invalid native metadata cache path')
        with self.connect() as db:
            row = db.execute('SELECT value FROM native_files WHERE namespace=? AND path=?',
                             (self.namespace, str(path))).fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        if (not isinstance(value, list) or len(value) != 3
                or not isinstance(value[1], dict) or type(value[2]) is not bool):
            raise ValueError('Invalid native metadata cache file')
        return tuples(value[0]), value[1], value[2]

    @staticmethod
    def _recovery_ids(identities):
        values = tuple(islice(identities, 65))
        if len(values) > 64 or any(not isinstance(value, str) or not value for value in values):
            raise ValueError('Native recovery batches require at most 64 nonempty identities')
        return tuple(dict.fromkeys(values))

    def recovery_begin(self, project):
        """Start an incomplete membership proof; existing rows remain authoritative."""
        token = uuid.uuid4().hex
        with self.connect() as db:
            self._check_running()
            db.execute('DELETE FROM native_recovery_seen WHERE namespace=? AND token IN '
                       '(SELECT token FROM native_recovery_stages WHERE namespace=? AND project=?)',
                       (self.namespace, self.namespace, project))
            db.execute('INSERT OR REPLACE INTO native_recovery_stages VALUES(?,?,?)',
                       (self.namespace, project, token))
        return token

    def _recovery_project(self, db, token):
        row = db.execute('SELECT project FROM native_recovery_stages WHERE namespace=? AND token=?',
                         (self.namespace, token)).fetchone()
        if row is None:
            raise ValueError('Native recovery stage is no longer current')
        return row[0]

    def recovery_seen(self, token, identities):
        identities = self._recovery_ids(identities)
        with self.connect() as db:
            self._check_running()
            self._recovery_project(db, token)
            db.executemany('INSERT OR IGNORE INTO native_recovery_seen VALUES(?,?,?)',
                           ((self.namespace, token, identity) for identity in identities))

    def recovery_missing(self, token, after='', limit=64):
        """Bounded scalar removals for the caller's completed, current scan only."""
        if type(limit) is not int or not 1 <= limit <= 64 or not isinstance(after, str):
            raise ValueError('Native recovery pages require a string cursor and limit from 1 to 64')
        with self.connect() as db:
            self._check_running()
            project = self._recovery_project(db, token)
            return db.execute('SELECT identity,kind FROM native_rows WHERE namespace=? AND project=? '
                'AND identity>? AND identity NOT IN (SELECT identity FROM native_recovery_seen WHERE namespace=? AND token=?) '
                'ORDER BY identity LIMIT ?',
                (self.namespace, project, after, self.namespace, token, limit)).fetchall()

    def recovery_abort(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM native_recovery_seen WHERE namespace=? AND token=?', (self.namespace, token))
            db.execute('DELETE FROM native_recovery_stages WHERE namespace=? AND token=?', (self.namespace, token))

    def recovery_facts(self, project, identities):
        """Small source stamps only; unchanged cached summaries are never decoded."""
        identities = self._recovery_ids(identities)
        if not identities:
            return {}
        names = ('metadata.json', 'naming.json', 'context-intelligence/metadata.json')
        with self.connect() as db:
            self._check_running()
            rows = db.execute('SELECT identity,transcript_mtime,transcript_size,transcript_available '
                'FROM native_rows WHERE namespace=? AND project=? '
                'AND identity IN (' + ','.join('?' for _ in identities) + ')',
                (self.namespace, project, *identities)).fetchall()
            facts = {identity: {'transcriptRevision': [mtime, size] if mtime is not None and size is not None else None,
                               'transcriptAvailable': bool(available),
                               'files': {name: (None, name != 'metadata.json') for name in names}}
                     for identity, mtime, size, available in rows}
            paths = {str(self.home / 'projects' / project / 'sessions' / identity / name): (identity, name)
                     for identity in facts for name in names}
            if paths:
                rows = db.execute('SELECT path,signature,valid '
                    'FROM native_file_sources WHERE namespace=? AND path IN (' + ','.join('?' for _ in paths) + ')',
                    (self.namespace, *paths)).fetchall()
                for path, stamp, valid in rows:
                    identity, name = paths[path]
                    facts[identity]['files'][name] = (tuples(json.loads(stamp)) if stamp is not None else None,
                                                       bool(valid))
        return facts

    def load(self):
        projects, files = {}, {}
        with self.connect() as db:
            for project, raw in db.execute('SELECT project,value FROM native_projects WHERE namespace=?', (self.namespace,)):
                self._check_running()
                value = json.loads(raw)
                workspace = value.get('workspace') if isinstance(value, dict) else None
                if (not isinstance(workspace, dict)
                        or workspace.get('nativeProject') != project
                        or type(workspace.get('available')) is not bool
                        or (workspace.get('path') is not None
                            and (not isinstance(workspace['path'], str)
                                 or not Path(workspace['path']).is_absolute()))):
                    raise ValueError('Invalid native metadata cache')
                # Reject the whole cache before handing any project to discovery.
                # A malformed rebuildable view must not poison repeated refreshes.
                projects[project] = {'workspace': workspace, 'sessions': []}
            for project, identity, raw in db.execute('SELECT project,identity,value FROM native_rows WHERE namespace=?', (self.namespace,)):
                self._check_running()
                value = json.loads(raw)
                if (project not in projects or not isinstance(value, dict)
                        or value.get('nativeProject') != project or value.get('nativeIdentity') != identity):
                    raise ValueError('Invalid native metadata cache')
                projects[project]['sessions'].append(value)
            for raw_path, raw in db.execute('SELECT path,value FROM native_files WHERE namespace=?', (self.namespace,)):
                self._check_running()
                path = Path(raw_path)
                if not path.is_relative_to(self.home / 'projects'):
                    raise ValueError('Invalid native metadata cache path')
                value = json.loads(raw)
                if (not isinstance(value, list) or len(value) != 3
                        or not isinstance(value[1], dict) or type(value[2]) is not bool):
                    raise ValueError('Invalid native metadata cache file')
                files[path] = (tuples(value[0]), value[1], value[2])
        return projects, files

    def _check_running(self):
        if self.stop is not None and self.stop.is_set():
            raise InterruptedError('Native catalog work stopped for shutdown')

    def save(self, projects, previous, files, dirty_files):
        """Commit only changed metadata; no transcript, event or app view bodies."""
        changed, removed = [], []
        reasons = Counter()
        revision = observation = membership = navigation = workspace_changed = root_navigation = False
        with self.connect() as db:
            from .native_query_receipts import Writer
            db.execute('BEGIN IMMEDIATE')
            query_receipt = Writer(db, self.namespace)
            for name in previous.keys() - projects.keys():
                self._check_running()
                revision = True
                removed.extend((name, row[0]) for row in db.execute(
                    'SELECT identity FROM native_rows WHERE namespace=? AND project=?', (self.namespace, name)))
                deleted = db.execute('DELETE FROM native_rows WHERE namespace=? AND project=?', (self.namespace, name))
                if deleted.rowcount:
                    observation = membership = navigation = True
                    workspace_changed = root_navigation = True
                    query_receipt.structure()
                db.execute('DELETE FROM native_query_recency WHERE namespace=? AND project=?', (self.namespace, name))
                reasons['project.remove.occupied' if deleted.rowcount else 'project.remove.empty'] += 1
                db.execute('DELETE FROM native_projects WHERE namespace=? AND project=?', (self.namespace, name))
                db.execute('DELETE FROM native_recovery_seen WHERE namespace=? AND token IN '
                           '(SELECT token FROM native_recovery_stages WHERE namespace=? AND project=?)',
                           (self.namespace, self.namespace, name))
                db.execute('DELETE FROM native_recovery_stages WHERE namespace=? AND project=?',
                           (self.namespace, name))
                prefix = str(self.home / 'projects' / name) + '/'
                db.execute('DELETE FROM native_files WHERE namespace=? AND substr(path,1,?)=?',
                           (self.namespace, len(prefix), prefix))
                db.execute('DELETE FROM native_file_sources WHERE namespace=? AND substr(path,1,?)=?',
                           (self.namespace, len(prefix), prefix))
            for name, project in projects.items():
                self._check_running()
                old = previous.get(name)
                if project is old:
                    continue
                if old is None or old['workspace'] != project['workspace']:
                    revision = True
                    db.execute('INSERT OR REPLACE INTO native_projects VALUES(?,?,?)',
                               (self.namespace, name, json.dumps({'workspace': project['workspace']})))
                    # Empty-project discovery changes coverage, not session
                    # facts. Added rows invalidate below; existing rows still
                    # inherit every workspace change before this transaction
                    # commits. The UPSERT starts the writer transaction before
                    # this keyed existence check; no summaries are decoded.
                    occupied = db.execute('SELECT 1 FROM native_rows WHERE namespace=? AND project=? LIMIT 1',
                                          (self.namespace, name)).fetchone() is not None
                    reasons['project.' + ('add' if old is None else 'change') +
                            ('.occupied' if occupied else '.empty')] += 1
                    navigation |= occupied
                    workspace_changed |= occupied
                    root_navigation |= occupied
                    if occupied:
                        query_receipt.structure()
                    if occupied and (old is None or any(old['workspace'].get(key) != project['workspace'].get(key)
                                          for key in ('id', 'path'))):
                        observation = membership = True
                patch = project['sessions'] if isinstance(project['sessions'], CatalogPatchRows) else None
                old_rows = ({identity: patch.base.get(identity) for identity in patch.changed}
                            if patch else {row['nativeIdentity']: row for row in old['sessions']} if old else {})
                new_rows = patch.changed if patch else {row['nativeIdentity']: row for row in project['sessions']}
                for identity in (patch.removed if patch else old_rows.keys() - new_rows.keys()):
                    revision = True
                    deleted = db.execute('DELETE FROM native_rows WHERE namespace=? AND project=? AND identity=?',
                                         (self.namespace, name, identity))
                    if deleted.rowcount:
                        observation = membership = navigation = True
                        workspace_changed = root_navigation = True
                        query_receipt.structure()
                    query_receipt.remove(name, identity)
                    reasons['row.remove' if deleted.rowcount else 'row.absent'] += 1
                    # A repeated absence can still repair a caller's stale
                    # projection, but cannot change committed session facts.
                    removed.append((name, identity))
                for identity, row in new_rows.items():
                    self._check_running()
                    if row != old_rows.get(identity):
                        prior = old_rows.get(identity)
                        query_receipt.row(row, prior)
                        revision = True
                        reasons['row.upsert'] += 1
                        membership |= prior is None or any(row.get(key) != prior.get(key) for key in MEMBERSHIP_FIELDS)
                        observation |= prior is None or any(row.get(key) != prior.get(key) for key in OBSERVATION_FIELDS)
                        navigation |= prior is None or any(row.get(key) != prior.get(key) for key in NAVIGATION_FIELDS)
                        root_navigation |= (prior is None or
                            any(row.get(key) != prior.get(key) for key in MEMBERSHIP_FIELDS) or
                            row.get('sessionKind', 'root') == 'root' and
                            any(row.get(key) != prior.get(key) for key in NAVIGATION_FIELDS))
                        # Titles and child progress never change workspace totals.
                        # Membership changes remain conservative: duplicate native
                        # IDs can alter the public owner used by suppression/read.
                        workspace_changed |= (prior is None or
                            any(row.get(key) != prior.get(key) for key in MEMBERSHIP_FIELDS) or
                            row.get('sessionKind', 'root') == 'root' and
                            row.get('recentActivityAt') != prior.get('recentActivityAt'))
                        columns = ','.join(('namespace', 'project', 'identity', 'value', *SCALAR_COLUMNS))
                        values = (self.namespace, name, identity, json.dumps(row), *row_scalars(row, project['workspace']))
                        db.execute('INSERT OR REPLACE INTO native_rows (' + columns + ') VALUES(' + ','.join('?' for _ in values) + ')', values)
                        # A targeted writer may add a row after a recovery
                        # iterator passed its directory. That newer observation
                        # must survive this older membership sweep.
                        db.execute('INSERT OR IGNORE INTO native_recovery_seen '
                                   'SELECT namespace,token,? FROM native_recovery_stages WHERE namespace=? AND project=?',
                                   (identity, self.namespace, name))
                        changed.append(row)
                if old is None or old['workspace'] != project['workspace']:
                    workspace = project['workspace']
                    db.execute('UPDATE native_rows SET available=?,workspace_id=?,workspace_path=? WHERE namespace=? AND project=?',
                               (int(bool(workspace.get('available'))), workspace.get('id'), workspace.get('path'), self.namespace, name))
            for path in dirty_files:
                self._check_running()
                value = files.get(path)
                if value is not None:
                    db.execute('INSERT OR REPLACE INTO native_files VALUES(?,?,?)',
                               (self.namespace, str(path), json.dumps(value)))
                    signature, metadata, failed = value
                    valid = not failed and isinstance(metadata, dict) and (not metadata or metadata.get('_nativeFieldsVersion') == 2)
                    db.execute('INSERT OR REPLACE INTO native_file_sources VALUES(?,?,?,?)',
                               (self.namespace, str(path), json.dumps(signature), int(valid)))
            if revision:
                db.execute('UPDATE native_revisions SET revision=revision+1,observation_sessions=observation_sessions+?,observation_membership=observation_membership+?,navigation=navigation+?,workspace=workspace+?,root_navigation=root_navigation+? WHERE namespace=?',
                           (int(observation), int(membership), int(navigation), int(workspace_changed), int(root_navigation), self.namespace))
            query_receipt.commit()
        # The connection context committed successfully. Fixed categories only:
        # never record an identity/path/value, and never count rolled-back work.
        if self.diagnostics is not None:
            for reason, amount in reasons.items():
                self.diagnostics.increment('catalog.write.' + reason, amount)
        return {'sessions': changed, 'removed': removed}


class CatalogRows:
    """Nonresident project membership. Iteration is deliberate full enumeration."""
    def __init__(self, catalog, project):
        self.catalog, self.project = catalog, project

    def __len__(self):
        return self.catalog.count(project=self.project)

    def __iter__(self):
        offset = 0
        while True:
            rows = self.catalog.page(project=self.project, offset=offset, limit=256, order='identity')
            yield from rows
            if len(rows) < 256:
                return
            offset += len(rows)

    def get(self, identity):
        return self.catalog.get(self.project, identity)


class CatalogPatchRows:
    """A bounded targeted edit over nonresident canonical-cache membership."""
    def __init__(self, base, changed, removed=()):
        self.changed = {row['nativeIdentity']: row for row in changed}
        self.removed = set(removed)
        if isinstance(base, CatalogPatchRows):
            self.changed = {**{key: row for key, row in base.changed.items() if key not in self.removed}, **self.changed}
            self.removed = (base.removed | self.removed) - self.changed.keys()
            base = base.base
        self.base = base

    def __iter__(self):
        yield from (row for row in self.base if row['nativeIdentity'] not in self.changed
                    and row['nativeIdentity'] not in self.removed)
        yield from self.changed.values()

    def get(self, identity):
        if identity in self.removed:
            return None
        return self.changed.get(identity) or self.base.get(identity)
