"""Client-neutral, read-only observations over canonical session facts.

One shared public-facts index feeds bounded, leased snapshots. Semantic
revisions ignore presentation/transcript changes; cursors finish a coherent
collection even when its live facts change. No snapshot is retained per query.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict

from aiohttp import web

from .attention import session_item_at, session_item_count
from .auth import data_identity
from .errors import AppError
from .session_activity import activity_flags
from .session_navigation import is_top_level, is_agent_created

SCAN_BUDGET = 1024
MAX_PAGE = 200
COUNT_BUDGET = 4096
SNAPSHOT_SECONDS = 600
MAX_SNAPSHOTS = 8  # Per collection: one busy resource cannot starve another.
MAX_TRAVERSALS = 64  # Per collection, shared snapshots within a generation.
RETRY_SECONDS = 30
MAX_COMPLETED = 64  # Terminal-response retries, not retained catalogs.
CAPABILITIES = {
    'observations': {'version': 1, 'sessions': '/api/sessions?view=observation',
                     'attention': '/api/attention', 'activity': '/api/activity',
                     'maxPageSize': MAX_PAGE, 'conditionalRequests': True, 'incrementalCounts': True},
}


def public_notice(row, item, slot):
    prefix, identity = item['id'].split(':', 1)
    kind = {'session': 'session-error', 'unread': 'manual-unread'}.get(prefix, prefix)
    target = {'type': 'session', 'sessionId': row['id']}
    timestamp = None
    requires_action = kind in {'question', 'approval', 'session-error'}
    if prefix == 'question':
        question = row['questions'][slot]
        target['questionId'] = identity
        timestamp = question.get('updatedAt', question.get('createdAt'))
        requires_action = bool(question.get('required') or question.get('status') != 'pending')
    elif prefix == 'approval':
        approval = row['approvals'][slot - len(row.get('questions', [])) - 3]
        target['approvalId'] = identity
        timestamp = approval.get('createdAt')
    elif prefix == 'completion':
        timestamp = row['completion'].get('at')
    elif prefix == 'session':
        timestamp = row.get('errorAt')
    # Only allowlisted labels, never canonical detail/prompt/tool arguments.
    summary = 'Approval requested' if prefix == 'approval' else item['title'][:200]
    return {'id': item['id'], 'fingerprint': item['fingerprint'], 'kind': kind,
            'target': target, 'requiresAction': requires_action, 'read': item['read'],
            'summary': summary, 'title': row.get('title', 'Chat')[:200],
            'agentCreated': is_agent_created(row),
            'timestamp': timestamp if type(timestamp) in {int, float} else None}


class DirectoryFacts:
    """A leased SQLite view; retain one bounded decoded block, never all facts."""
    BLOCK = 64

    def __init__(self, owner, directory, state, *, where='1', params=()):
        self.owner, self.directory, self.state = owner, directory, state
        self.where, self.params = where, tuple(params)
        self.sql, self.base = directory.effective(state)
        self.length = None
        self.end = None
        self.block_start, self.block = -1, []
        self.raw_block = None
        self.block_length = 0
        self.query_view = None
        self.cursor = None
        self.cursor_position = -1
        self.pins = frozenset(state.get('pinnedSessionIds', ()))
        self.read = state.get('attentionRead', {})

    def __len__(self):
        if self.length is None:
            self.length = self.directory.db.execute(self.sql+' SELECT COUNT(*) FROM directory_rows WHERE '+self.where,
                                                   [*self.base,*self.params]).fetchone()[0]
        return self.length

    def has(self, index):
        """Bounded lookahead; paging does not require counting the directory."""
        if index < 0 or self.end is not None and index >= self.end:
            return False
        start = index // self.BLOCK * self.BLOCK
        if start != self.block_start:
            from .history_directory import COLUMNS
            if self.cursor is None or self.cursor_position != start:
                if self.cursor is not None:
                    self.cursor.close()
                self.cursor = self.directory.db.execute(self.sql+' SELECT '+','.join(COLUMNS)+
                    ' FROM directory_rows WHERE '+self.where+' ORDER BY ordinal,id LIMIT -1 OFFSET ?',
                    [*self.base,*self.params,start])
            selected = self.cursor.fetchmany(self.BLOCK)
            self.cursor_position = start + len(selected)
            self.raw_block, self.block = selected, []
            self.block_length = len(selected)
            self.block_start = start
            if len(selected) < self.BLOCK:
                self.end = start + len(selected)
        return index - start < self.block_length

    def __getitem__(self, index):
        if not self.has(index):
            raise IndexError(index)
        if self.raw_block is not None:
            self.block = [self.owner.row_facts(self.directory.decode(row), self.read, self.pins) for row in self.raw_block]
            self.raw_block = None
        start = self.block_start
        return self.block[index-start]

    def for_query(self, query):
        scope = query['scope']
        where = ["session_kind!='internal'"]
        params = []
        if scope != 'all':
            where.append("session_kind='root'" if scope == 'roots' else "session_kind!='root'")
        if query['collection'] == 'sessions':
            pinned = 'id IN (SELECT value FROM json_each(?))'
            activity = query['activity']
            clauses = {'execution':'execution','preparing':'preparing','loading':'loading',
                       'idle':'NOT(execution OR preparing OR loading)', 'active':'(execution OR preparing)'}
            if activity == 'active-or-pinned':
                where.append('(execution OR preparing OR '+pinned+')')
                params.append(json.dumps(list(self.pins)))
            elif activity in clauses:
                where.append(clauses[activity])
            if query['pinned'] != 'all':
                where.append(('' if query['pinned']=='true' else 'NOT ')+pinned)
                params.append(json.dumps(list(self.pins)))
            if query['workspace'] is not None:
                where.append('workspace=?')
                params.append(query['workspace'])
        else:
            # Native summaries have no app notices. Read only local rows with
            # possible notice facts; public_notice remains the canonical filter.
            where.append("notice_count>0")
        predicate = ' AND '.join(where)
        if self.query_view is None or (self.query_view.where,self.query_view.params) != (predicate,tuple(params)):
            if self.query_view is not None:
                self.query_view.discard_block()
            self.query_view = DirectoryFacts(self.owner,self.directory,self.state,where=predicate,params=params)
        return self.query_view

    def activity_counts(self):
        result = {role:dict(executionCount=0,preparingCount=0,loadingCount=0,
                            attentionCount=0,requiresActionCount=0) for role in ('roots','delegates')}
        sql, params = self.directory.effective(self.state, fields={'session_kind', 'execution', 'preparing', 'loading'})
        rows = self.directory.db.execute(sql+" SELECT session_kind,SUM(execution),SUM(preparing),SUM(loading) FROM directory_rows WHERE session_kind!='internal' AND (execution OR preparing OR loading) GROUP BY session_kind",params)
        for kind,execution,preparing,loading in rows:
            count = result['roots' if kind=='root' else 'delegates']
            for key,value in zip(('executionCount','preparingCount','loadingCount'),(execution,preparing,loading)):
                count[key] += value or 0
        return result

    def discard_block(self):
        if self.cursor is not None:
            self.cursor.close()
            self.cursor = None
        self.block.clear()
        self.raw_block = None
        self.block_length = 0
        self.block_start = self.cursor_position = -1

    def close(self):
        self.discard_block()
        if self.query_view is not None:
            self.query_view.discard_block()
            self.query_view = None
        self.directory.close()


class Observations:
    def __init__(self, service):
        self.service = service
        self.secret = secrets.token_bytes(32)
        self.counts_cache = None
        self.facts = None
        self.order = ()
        self.dirty = set()
        self.all_dirty = True
        self.pending = True
        self.generations = dict(sessions=0, attention=0, activity=0)
        self.snapshots = OrderedDict()
        self.leases = {}
        self.completed = OrderedDict()
        self.directory_revision = None
        self.counts_revision = None
        self.ready_counts_cache = None
        # No catalog work on hosts that never read an observation resource.
        service.projections.observation_listener = self.invalidate

    def invalidate(self, state=None, session_ids=None, detail_only=False):
        if detail_only and session_ids is not None:
            # The service's narrow publication contract has already compared
            # notice/lifecycle facts. Transcript deltas cannot affect this index.
            return
        self.pending = True
        if session_ids is None or self.facts is None:
            self.all_dirty = True
            self.dirty.clear()
        elif not self.all_dirty:
            self.dirty.update(session_ids)

    def row_facts(self, row, read, pins):
        role = 'internal' if row.get('sessionKind') == 'internal' else 'root' if is_top_level(row) else 'delegate'
        session = {'id': row['id'], 'title': row.get('title', 'Chat')[:200],
                   'status': row.get('status', 'idle'), 'activity': activity_flags(row),
                   'pinned': row['id'] in pins, 'role': role, 'agentCreated': is_agent_created(row)}
        notices = tuple(public_notice(row, item, slot)
                        for slot in range(session_item_count(row))
                        if (item := session_item_at(row, read, slot)) is not None) if role != 'internal' else ()
        return (session, row.get('workspace'), notices)

    def refresh(self):
        """Refresh semantic revisions from the directory's metadata owners.

        The legacy in-memory fallback retains public facts for standalone owners
        without a directory. Directory readers decode only bounded pages.
        """
        directory = getattr(self.service, 'directory', None)
        directory_revision = getattr(directory, 'observation_revision', None) if directory else None
        if directory is not None and isinstance(directory_revision, dict):
            # Directory ownership distinguishes residency from membership.
            # Admission/eviction alone cannot change a public generation.
            self.directory_revision = directory_revision
            self.saved_pins = frozenset(self.service._state.get('pinnedSessionIds', ()))
            self.saved_read = dict(self.service._state.get('attentionRead', {}))
            self.pending = self.all_dirty = False
            self.dirty.clear()
            return
        if not self.pending and directory_revision == self.directory_revision:
            return
        state = self.service._state
        pins = frozenset(state.get('pinnedSessionIds', ()))
        read = state.get('attentionRead', {})
        if self.facts is None:
            self.facts = {}
            self.saved_pins, self.saved_read = frozenset(), {}
        pins_changed = pins != self.saved_pins
        read_changed = read != self.saved_read
        if not (self.all_dirty or self.dirty or pins_changed or read_changed or directory_revision != self.directory_revision):
            self.pending = False
            return
        # Global receipts/pins affect public facts even on an otherwise scoped save.
        full = self.all_dirty or pins_changed or read_changed
        rows = state['sessions']
        if full:
            order = tuple(row['id'] for row in rows)
            candidates = rows
        else:
            index = self.service.projections.sessions(state)
            order = self.order
            candidates = [index.by_id[sid] for sid in self.dirty if sid in index.by_id]
            # Membership changes must use catalog order, not dirty-set order.
            if any(sid not in self.facts or sid not in index.by_id for sid in self.dirty):
                order = tuple(row['id'] for row in rows)
        changed = set()
        if directory is not None and directory_revision != self.directory_revision:
            if isinstance(directory_revision, dict) and isinstance(self.directory_revision, dict):
                changed.update(key for key in self.generations if directory_revision.get(key) != self.directory_revision.get(key))
            else:
                changed.update(self.generations)
        self.directory_revision = directory_revision
        if order != self.order:
            changed.update(self.generations)
            retained = set(order)
            self.facts = {sid: facts for sid, facts in self.facts.items() if sid in retained}
        for row in candidates:
            new = self.row_facts(row, read, pins)
            old = self.facts.get(row['id'])
            if old == new:
                continue
            if old is None or old[:2] != new[:2]:
                changed.add('sessions')
            if old is None or (old[0]['role'], old[2]) != (new[0]['role'], new[2]):
                changed.add('attention')
            if old is None or self.count_facts(old) != self.count_facts(new):
                changed.add('activity')
            self.facts[row['id']] = new
        for collection in changed:
            self.generations[collection] += 1
        self.order = order
        self.saved_pins, self.saved_read = pins, dict(read)
        self.all_dirty = False
        self.pending = False
        self.dirty.clear()

    @staticmethod
    def count_facts(facts):
        session, _, notices = facts
        return (session['role'], session['activity'],
                sum(not item['read'] for item in notices),
                sum(not item['read'] and item['requiresAction'] for item in notices))

    @property
    def revision(self):
        self.refresh()
        return self.revision_for('activity')

    def revision_for(self, collection):
        if isinstance(self.directory_revision, dict):
            return self.directory_key(collection,self.directory_revision)
        return f'{self.service.instance_id}:{collection}:{self.generations[collection]}'

    def directory_key(self, collection, revisions):
        digest = hashlib.sha256(json.dumps(revisions[collection],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return f'{self.service.instance_id}:{collection}:{digest}'

    def require_client(self):
        if self.service.clients.current.get() is None:
            raise AppError('Attach a client and send X-Amplifier-Client.', 400)

    def pins(self):
        self.refresh()
        return self.saved_pins

    def reclaim(self):
        now = time.monotonic()
        self.leases = {key: value for key, value in self.leases.items() if now < value['expires']}
        for key, value in list(self.completed.items()):
            if now >= value['expires']:
                del self.completed[key]
        live = {lease['revision'] for lease in self.leases.values()}
        for key, value in list(self.snapshots.items()):
            if key not in live and (isinstance(value['rows'], DirectoryFacts) or key != self.revision_for(value['collection'])):
                rows = self.snapshots.pop(key)['rows']
                if isinstance(rows, DirectoryFacts):
                    rows.close()

    def cursor(self, query, position, revision, lease_id):
        if lease_id is None:
            collection = query['collection']
            if sum(value['collection'] == collection for value in self.leases.values()) >= MAX_TRAVERSALS:
                raise AppError('Too many unfinished observation traversals.', 503, code='observation_busy')
            lease_id = secrets.token_urlsafe(16)
            self.leases[lease_id] = {'revision': revision, 'query': query, 'collection': collection,
                'owner': self.service.clients.current.get(), 'expires': time.monotonic() + SNAPSHOT_SECONDS}
        raw = json.dumps([revision, query, position, lease_id], separators=(',', ':'), sort_keys=True).encode()
        signature = hmac.digest(self.secret, raw, 'sha256')
        return base64.urlsafe_b64encode(signature + raw).decode().rstrip('=')

    def finish(self, query, token, lease_id, value):
        if lease_id is not None:
            self.leases.pop(lease_id, None)
            if token:
                # Only a terminal page/count result survives completion. Its
                # small retry grace must never reserve an entire catalog slot.
                self.completed[token] = {'query': query, 'value': value,
                    'expires': time.monotonic() + RETRY_SECONDS, 'collection': query['collection']}
                keys = [key for key, row in self.completed.items() if row['collection'] == query['collection']]
                for key in keys[:-MAX_COMPLETED]:
                    del self.completed[key]
            self.reclaim()
        revision = value.get('revision')
        if revision in self.snapshots and not any(row['revision']==revision for row in self.leases.values()):
            snapshot = self.snapshots[revision]
            if isinstance(snapshot['rows'], DirectoryFacts):
                self.snapshots.pop(revision)['rows'].close()
        return value

    def close(self):
        for snapshot in self.snapshots.values():
            if isinstance(snapshot['rows'], DirectoryFacts):
                snapshot['rows'].close()
        self.snapshots.clear()
        self.leases.clear()
        self.completed.clear()

    def snapshot(self, token, query):
        self.refresh()
        self.reclaim()
        revision = self.revision_for(query['collection'])
        position, lease_id = [0, 0], None
        if token:
            try:
                if len(token) > 4096:
                    raise ValueError()
                decoded = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
                signature, raw = decoded[:32], decoded[32:]
                if not hmac.compare_digest(signature, hmac.digest(self.secret, raw, 'sha256')):
                    raise ValueError()
                revision, saved_query, position, lease_id = json.loads(raw)
                if (query != saved_query or not isinstance(revision, str) or not isinstance(lease_id, str)
                        or len(position) != 2 or any(type(i) is not int or i < 0 for i in position)):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise AppError('Invalid cursor or cursor query mismatch.', code='invalid_cursor') from None
            completed = self.completed.get(token)
            if completed is not None:
                return None, position, lease_id, completed['value']
            if lease_id not in self.leases or revision not in self.snapshots:
                raise AppError('The traversal finished or expired; restart from the first page.',
                               409, code='snapshot_changed')
        else:
            # Revalidation/retry of the first page must not manufacture abandoned
            # readers or change its ETag. Distinct clients and queries have their
            # own leases even when sharing the same immutable snapshot.
            owner = self.service.clients.current.get()
            lease_id = next((key for key, value in self.leases.items()
                if value['owner'] == owner and value['revision'] == revision and value['query'] == query), None)
        if revision not in self.snapshots:
            collection = query['collection']
            if sum(row['collection'] == collection for row in self.snapshots.values()) >= MAX_SNAPSHOTS:
                raise AppError('Observation snapshots are busy; finish a traversal or retry after expiry.',
                               503, code='observation_busy')
            directory = getattr(self.service, 'directory', None)
            if directory is not None:
                reader = directory.read_snapshot(self.service._state)
                try:
                    rows = DirectoryFacts(self, reader, reader._frozen_state)
                    # Native discovery can commit on another thread between
                    # refresh and BEGIN. Label exactly these pinned read views.
                    from .history_directory_observations import revision as directory_revision
                    actual = self.directory_key(collection,directory_revision(reader,reader._frozen_state))
                    if actual != revision:
                        revision = actual
                        owner = self.service.clients.current.get()
                        lease_id = next((key for key,value in self.leases.items()
                            if value['owner']==owner and value['revision']==revision and value['query']==query),None)
                    if revision in self.snapshots:
                        rows.close()
                        return self.snapshots[revision],position,lease_id,None
                except BaseException:
                    reader.close()
                    raise
            else:
                rows = tuple(self.facts[sid] for sid in self.order)
            self.snapshots[revision] = {'revision': revision, 'collection': collection,
                                       'rows': rows, 'counts': None}
        return self.snapshots[revision], position, lease_id, None

    def query(self, params, collection):
        allowed = {'cursor', 'limit', 'scope', 'clientId'}
        allowed |= {'workspace', 'activity', 'pinned', 'view'} if collection == 'sessions' else {'read'}
        if set(params) - allowed or any(len(params.getall(k)) != 1 for k in params if hasattr(params, 'getall')):
            raise AppError('Unsupported or repeated observation parameter.')
        try:
            limit = int(params.get('limit', 100))
        except (TypeError, ValueError):
            raise AppError('Invalid page limit.') from None
        scope = params.get('scope', 'roots')
        if not 1 <= limit <= MAX_PAGE or scope not in {'roots', 'delegates', 'all'}:
            raise AppError('Choose limit 1–200 and scope roots, delegates or all.')
        query = {'collection': collection, 'scope': scope, 'limit': limit}
        if collection == 'sessions':
            activity = params.get('activity', 'all')
            pinned = params.get('pinned', 'all')
            if activity not in {'all', 'execution', 'preparing', 'loading', 'idle', 'active', 'active-or-pinned'} or pinned not in {'all', 'true', 'false'}:
                raise AppError('Unsupported activity or pinned filter.')
            if params.get('view', 'observation') != 'observation':
                raise AppError('Unsupported observation view.')
            workspace = params.get('workspace')
            if workspace is not None and len(workspace.encode()) > 512:
                raise AppError('Workspace filter is too long.')
            query.update(activity=activity, pinned=pinned, workspace=workspace)
        else:
            if params.get('read', 'false') not in {'all', 'true', 'false'}:
                raise AppError('Unsupported read filter.')
            query['read'] = params.get('read', 'false')
        return query

    def page(self, collection, params):
        query = self.query(params, collection)
        token = params.get('cursor')
        snapshot, (position, slot), lease_id, replay = self.snapshot(token, query)
        if replay is not None:
            return replay
        rows = snapshot['rows']
        if isinstance(rows, DirectoryFacts):
            rows = rows.for_query(query)
        has_row = rows.has if isinstance(rows, DirectoryFacts) else lambda position: position < len(rows)
        items, inspected = [], 0
        while inspected < SCAN_BUDGET and len(items) < query['limit'] and has_row(position):
            session, workspace, notices = rows[position]
            inspected += 1
            if not self.in_scope(session, query['scope']):
                position, slot = position + 1, 0
                continue
            if collection == 'sessions':
                position += 1
                flags, pinned = session['activity'], session['pinned']
                activity = query['activity']
                active = flags['execution'] or flags['preparing']
                matches = (activity == 'all' or flags.get(activity) or activity == 'idle' and not any(flags.values())
                           or activity == 'active' and active or activity == 'active-or-pinned' and (active or pinned))
                if not matches or query['pinned'] != 'all' and pinned != (query['pinned'] == 'true'):
                    continue
                if query['workspace'] is not None and workspace != query['workspace']:
                    continue
                items.append(session)
            else:
                if slot < len(notices):
                    item = notices[slot]
                    if query['read'] == 'all' or item['read'] == (query['read'] == 'true'):
                        items.append(item)
                slot += 1
                if slot >= len(notices):
                    position, slot = position + 1, 0
        value = {'revision': snapshot['revision'], 'items': items,
                 'nextCursor': self.cursor(query, [position, slot], snapshot['revision'], lease_id) if has_row(position) else None}
        return value if value['nextCursor'] else self.finish(query, token, lease_id, value)

    @staticmethod
    def in_scope(session, scope):
        return session['role'] != 'internal' and (scope == 'all' or
            session['role'] == ('root' if scope == 'roots' else 'delegate'))

    def activity(self, scope='roots', cursor=None):
        if scope not in {'roots', 'delegates', 'all'}:
            raise AppError('Unsupported activity scope.')
        query = {'collection': 'activity', 'scope': scope}
        if cursor is None:
            self.refresh()
            self.reclaim()
            if self.counts_revision == self.revision_for('activity') and self.ready_counts_cache is not None:
                self.counts_cache = self.ready_counts_cache
                counts = self.ready_counts_cache['counts']
                value = counts[scope] if scope != 'all' else {key: counts['roots'][key] + counts['delegates'][key] for key in counts['roots']}
                return {'revision': self.counts_revision, 'status': 'ready', 'nextCursor': None, **value}
        snapshot, _, lease_id, replay = self.snapshot(cursor, query)
        if replay is not None:
            return replay
        directory_rows = snapshot['rows'] if isinstance(snapshot['rows'], DirectoryFacts) else None
        if snapshot['counts'] is None:
            snapshot['counts'] = {'position': 0, 'slot': -1,
                'counts': directory_rows.activity_counts() if directory_rows is not None else
                    {role: dict(executionCount=0, preparingCount=0, loadingCount=0,
                                attentionCount=0, requiresActionCount=0) for role in ('roots', 'delegates')}}
        cache = self.counts_cache = snapshot['counts']
        rows = directory_rows.for_query({'scope':'all','collection':'attention'}) if directory_rows is not None else snapshot['rows']
        has_row = rows.has if isinstance(rows, DirectoryFacts) else lambda position: position < len(rows)
        inspected = 0
        # Reserve room for decoding canonical notice slots and one lookahead
        # block; a directory page does not have a prebuilt whole-library tuple.
        budget = COUNT_BUDGET // 2 if directory_rows is not None else COUNT_BUDGET
        while inspected < budget and has_row(cache['position']):
            session, _, notices = rows[cache['position']]
            inspected += 1
            if session['role'] == 'internal':
                cache['position'] += 1
                cache['slot'] = -1
                continue
            count = cache['counts']['roots' if session['role'] == 'root' else 'delegates']
            if cache['slot'] == -1:
                if directory_rows is None:
                    for flag, value in session['activity'].items():
                        count[flag + 'Count'] += int(value)
                cache['slot'] = 0
                continue
            if cache['slot'] < len(notices):
                item = notices[cache['slot']]
                if not item['read']:
                    count['attentionCount'] += 1
                    count['requiresActionCount'] += int(item['requiresAction'])
            cache['slot'] += 1
            if cache['slot'] >= len(notices):
                cache['position'] += 1
                cache['slot'] = -1
        if has_row(cache['position']):
            return {'revision': snapshot['revision'], 'status': 'collecting',
                    'nextCursor': self.cursor(query, [cache['position'], cache['slot'] + 1], snapshot['revision'], lease_id)}
        self.counts_revision = snapshot['revision']
        self.ready_counts_cache = cache
        counts = cache['counts']
        value = counts[scope] if scope != 'all' else {k: counts['roots'][k] + counts['delegates'][k] for k in counts['roots']}
        return self.finish(query, cursor, lease_id,
                           {'revision': snapshot['revision'], 'status': 'ready', 'nextCursor': None, **value})

    def response(self, request, value, status=200):
        etag = '"' + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() + '"'
        headers = {'ETag': etag, 'Cache-Control': 'private, no-cache', 'Vary': 'Authorization, Cookie, X-Amplifier-Client'}
        if etag in [part.strip() for part in request.headers.get('If-None-Match', '').split(',')]:
            return web.Response(status=304, headers=headers)
        return web.json_response(value, headers=headers, status=status)

    async def sessions(self, request):
        self.require_client()
        return self.response(request, self.page('sessions', request.query))

    async def attention(self, request):
        self.require_client()
        return self.response(request, self.page('attention', request.query))

    async def summary(self, request):
        self.require_client()
        if set(request.query) - {'scope', 'clientId', 'cursor'} or any(len(request.query.getall(k)) != 1 for k in request.query):
            raise AppError('Unsupported activity parameter.')
        value = self.activity(request.query.get('scope', 'roots'), request.query.get('cursor'))
        return self.response(request, value, 202 if value['status'] == 'collecting' else 200)


def discovery(service):
    return {'hostIdentity': data_identity(service.data_dir), 'capabilities': CAPABILITIES}


def setup_routes(app):
    observations = Observations(app['service'])
    app['observations'] = observations
    async def close_observations(app):
        observations.close()
    app.on_cleanup.append(close_observations)
    app.router.add_get('/api/activity', observations.summary)
    app.router.add_get('/api/attention', observations.attention)
