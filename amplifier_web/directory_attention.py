"""Current notice reads from the directory's existing derived notice records.

Global counts visit scalar notice/read facts. SQL checks approval titles against
canonical names; Python decodes demanded items and changed-title approvals only.
Session summaries, resolved questions and native transcript metadata never enter
this read. Results have the same generation lifetime as other state projections.
"""
import json

from .performance_diagnostics import count


def read(directory, operation):
    """Pin all component SELECTs, without committing an outer writer transaction."""
    def project():
        directory.db.execute('SAVEPOINT unified_attention_read')
        try:
            result = operation()
        except BaseException:
            directory.db.execute('ROLLBACK TO unified_attention_read')
            raise
        finally:
            directory.db.execute('RELEASE unified_attention_read')
        return result
    return directory.read_frame(project)


def snapshot(directory, state, session_ids=None):
    return read(directory, lambda: DirectoryAttention(directory, state).project(state, session_ids))


def receipts(directory, state, identities):
    if not identities:
        return []
    return read(directory, lambda: DirectoryAttention(directory, state, correct_approvals=False).receipts(identities))


class DirectoryAttention:
    def __init__(self, directory, state, *, correct_approvals=True):
        self.directory = directory
        sql, params = directory.effective(state, fields={'id', 'title', 'workspace', 'ordinal'},
                                         notice_values=True, local_only=True)
        sql += ''', visible_notices AS (
            SELECT n.*,d.title,d.workspace,d.ordinal FROM directory_notices n
            JOIN directory_rows d ON d.id=n.owner)'''
        # Native metadata can rename a dormant owner without rewriting local
        # notice records. Approval titles participate in their fingerprint;
        # other notice titles/versions are independent of the owner's name.
        changed = []
        from .attention import make_item
        if correct_approvals:
            for owner, slot, key, value, title in directory.db.execute(sql + '''
                    SELECT owner,slot,key,value,title FROM visible_notices
                    WHERE key GLOB 'approval:*'
                    AND json_extract(value,'$.title') IS NOT 'Approval requested · ' || title''', params):
                item = self.decode(value)
                current = make_item({}, key, 'Approval requested · ' + title,
                                    'chats', 'chats', item['detail'])
                changed.append([owner, slot, current['fingerprint']])
        # Keep exact review facts scalar and materialize their joins once per
        # SELECT. Older SQLite planners otherwise flatten this through the
        # directory UNION and scan every retained receipt for each notice.
        # Item bodies are joined only by the demanded-items query below.
        sql += ''', corrected_fingerprints AS (
            SELECT json_extract(value,'$[0]') owner,json_extract(value,'$[1]') slot,
                   json_extract(value,'$[2]') fingerprint FROM json_each(?)),
            current_notices AS MATERIALIZED (
                SELECT n.owner,n.slot,n.key,COALESCE(c.fingerprint,n.fingerprint) fingerprint,
                       n.is_error,n.title,n.workspace,n.ordinal,
                       COALESCE(r.value=COALESCE(c.fingerprint,n.fingerprint),0) is_read
                FROM visible_notices n
                LEFT JOIN corrected_fingerprints c ON c.owner=n.owner AND c.slot=n.slot
                LEFT JOIN read_receipts r ON r.key=n.key)'''
        self.sql, self.params = sql, (*params, json.dumps(changed, separators=(',', ':')))

    def decode(self, value):
        count(self.directory.service, 'directory.notices.decoded')
        return json.loads(value)

    def receipts(self, identities=None, *, errors=False):
        where = 'is_error' if errors else "(is_error OR key GLOB 'completion:*' OR key GLOB 'unread:*')"
        params = self.params
        if identities is not None:
            if not identities:
                return []
            where += ' AND owner IN (SELECT value FROM json_each(?))'
            params += (json.dumps(sorted(identities), separators=(',', ':')),)
        return [{'id': key, 'sessionId': owner, 'fingerprint': fingerprint, 'read': bool(read)}
                for owner, key, fingerprint, read in self.directory.db.execute(self.sql +
                    ' SELECT owner,key,fingerprint,is_read FROM current_notices WHERE ' + where +
                    ' ORDER BY ordinal,slot', params)]

    def items(self, identities=None):
        params, where = self.params, ''
        if identities is not None:
            if not identities:
                return []
            where = ' WHERE c.owner IN (SELECT value FROM json_each(?))'
            params += (json.dumps(sorted(identities), separators=(',', ':')),)
        result = []
        for owner, key, value, title, workspace, fingerprint, read in self.directory.db.execute(self.sql +
                ' SELECT c.owner,c.key,n.value,c.title,c.workspace,c.fingerprint,c.is_read'
                ' FROM current_notices c JOIN directory_notices n ON n.owner=c.owner AND n.slot=c.slot' +
                where + ' ORDER BY c.ordinal,c.slot', params):
            item = self.decode(value)
            item.update(sessionId=owner, workspace=workspace, fingerprint=fingerprint, read=bool(read))
            if key.startswith(('completion:', 'unread:')):
                item['label'] = title
            elif key.startswith('approval:'):
                item['title'] = 'Approval requested · ' + title
            result.append(item)
        return result

    def project(self, state, session_ids=None):
        from .attention import snapshot
        result = snapshot(state, session_items=[])
        items = self.items(session_ids)
        result['items'].extend(items)
        if session_ids is not None:
            # Internal navigation consumers need exact reviewed-error receipts,
            # not error bodies. Browser demand removes off-page receipt headers.
            result['items'].extend(item for item in self.receipts(errors=True) if item['sessionId'] not in session_ids)
        completion_badge = state.get('notificationSettings', {}).get('completionBadge', False)
        workspace_ids = {}
        for row in state.get('workspaces', []):
            workspace_ids.setdefault(row['path'], []).append(row['id'])
        # Acknowledged owners need no Python row allocation. Presence still
        # preserves the legacy zero-valued chats page when all are reviewed.
        if self.directory.db.execute(self.sql + ' SELECT 1 FROM visible_notices LIMIT 1', self.params).fetchone():
            result['pages'].setdefault('chats', 0)
        for owner, workspace, unread, badge in self.directory.db.execute(self.sql + '''
                SELECT owner,workspace,count(*),sum(key NOT GLOB 'completion:*')
                FROM current_notices WHERE NOT is_read GROUP BY owner,workspace''', self.params):
            result['unread'] += unread
            result['badgeUnread'] += unread if completion_badge else badge
            result['sections']['chats'] += unread
            result['pages']['chats'] = result['pages'].get('chats', 0) + unread
            if unread:
                result['sessions'][owner] = 1
                for identity in workspace_ids.get(workspace, ()):
                    result['workspaces'][identity] += unread
        return result
