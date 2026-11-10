"""Shared, bounded chat navigation over existing workspace/session summaries."""
from fnmatch import fnmatchcase
import copy
import math
import time
import uuid
from typing import Any, TypedDict
from contextlib import contextmanager
from contextvars import ContextVar

from .session_navigation import is_top_level, is_agent_created
from .managed_chats import is_managed
from .navigation_summary import activity, path_labels, task_blocked

class ChatPage(TypedDict):
    """Common navigation envelope; row and scope payloads keep their own owners."""

    items: list[dict[str, Any]]
    total: int
    start: int
    end: int
    scope: dict[str, Any]
    activityCounts: dict[str, int]


class PagedChatPage(ChatPage):
    index: int
    pages: int


class ShortcutChatPage(ChatPage):
    remaining: int
    limit: int
    dataRevision: int


ChatPageResult = PagedChatPage | ShortcutChatPage


PAGE_SIZE = 100
RECENT_LIMITS = (20, 40, 60, 80, 100)
MAX_POST_ADMISSIONS = 16
SIDEBAR_FILTER_KEYS = {'navSort', 'navArchive', 'navCollection', 'navLocationFilter',
                       'navStatusFilter', 'navFilter', 'navChatPage', 'navGroup', 'navAge'}
AGE_SECONDS = {'24h': 86400, '7d': 7*86400, '14d': 14*86400, '30d': 30*86400,
               '90d': 90*86400, '1y': 365*86400}
DATE_SECONDS = (86400, 7*86400, 30*86400, 90*86400, 365*86400)
DATE_GROUPS = ('Last 24 hours', '1–7 days ago', '7–30 days ago', '30–90 days ago',
               '90 days–1 year ago', 'Over 1 year ago', 'Activity time unavailable')
STATUS_GROUPS = {'attention': 'Needs you', 'unread': 'Ready for you', 'working': 'Working',
                 'waiting': 'Waiting', 'paused': 'Paused', 'idle': 'Pick up again'}
_list_clock = ContextVar('navigation_list_clock', default=None)


def list_clock():
    # Relative filters and date buckets share a minute witness in query caches.
    saved = _list_clock.get()
    return saved if saved is not None else int(time.time() // 60) * 60


def age_scope(state):
    """One age/clock witness for discovery, not transcript or registry membership."""
    age = state.get('view', {}).get('navAge', 'all')
    return age, state.get('_navigationListClock', list_clock()) if age in AGE_SECONDS else None


def within_age(session, state, *, witness=None, pins=None):
    age, now = age_scope(state) if witness is None else witness
    return (age not in AGE_SECONDS or session['id'] in (state.get('pinnedSessionIds', ()) if pins is None else pins)
            or 0 < navigation_activity(session) >= now - AGE_SECONDS[age])


@contextmanager
def frozen_list_clock(now):
    token = _list_clock.set(now)
    try:
        yield
    finally:
        _list_clock.reset(token)


def list_group(group, pinned, recent, workspace_id, workspace_name, kind, now):
    """Scalar-only grouping: callers order the complete catalog before paging."""
    if pinned:
        return ('pin', 'Pinned', -1)
    if group == 'workspace':
        return ('workspace:' + (workspace_id or ''), workspace_name or 'No workspace', 0)
    if group == 'status':
        keys = tuple(STATUS_GROUPS)
        kind = kind if kind in STATUS_GROUPS else 'idle'
        return ('status:' + kind, STATUS_GROUPS[kind], keys.index(kind))
    at = timestamp(recent)
    index = (next((i for i, seconds in enumerate(DATE_SECONDS) if at >= now-seconds), 5)
             if at is not None and at > 0 else 6)
    return ('date:' + str(index), DATE_GROUPS[index], index)


def group_row(row, group, now):
    identity, title, _ = list_group(group, row['pinned'], row['recentActivityAt'],
        row.get('workspaceId'), row.get('workspaceName'), row['activity']['kind'], now)
    return {**row, 'group': {'id': identity, 'title': title}}


def valid_recent_limit(value):
    # JavaScript safe integers, cumulative 20-row requests. The scoped handler
    # owns the live catalog bound; a generic view validator has no catalog.
    return type(value) is int and 20 <= value <= 2**53 - 1 and value % 20 == 0


def timestamp(value):
    return float(value) if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def recent_activity(session):
    """Migrate old views from conversation timestamps, never UI/lifecycle edits."""
    saved = timestamp(session.get('recentActivityAt'))
    if saved is not None:
        return saved
    # Refused steering can remain visible history without owning fallback recency.
    messages = [timestamp(row.get('createdAt')) or 0 for row in session.get('messages', [])
                if (row.get('navigationPost') or {}).get('disposition') != 'rejected']
    native = session.get('nativeRevision')
    if isinstance(native, (list, tuple)) and native and timestamp(native[0]) is not None:
        messages.append(native[0] / 1e9)
    return max(messages + [timestamp(session.get('createdAt')) or 0])


def touch(session):
    # Preserve the last ready position while a turn makes progress. Raw recency
    # remains available to history/reconciliation and diagnostics.
    session.setdefault('navigationActivityAt', recent_activity(session))
    session['navigationActivityPending'] = True
    session['recentActivityAt'] = max(recent_activity(session), time.time())


def navigation_activity(session):
    saved = timestamp(session.get('navigationActivityAt'))
    return saved if saved is not None else recent_activity(session)


def touch_independent_activity(session):
    """Keep separate input/progress truth inside the existing admission chain."""
    touch(session)
    chain = session.get('navigationPostAdmissions')
    if chain:
        chain['progress'] = True
        chain['progressAt'] = session['recentActivityAt']


def prepare_human_post(session):
    """Capture presence as well as values, before the durable input touches activity."""
    chain = session.get('navigationPostAdmissions', {})
    if len(chain.get('posts', [])) >= MAX_POST_ADMISSIONS:
        from .errors import AppError
        raise AppError('Wait for a pending message admission to settle before posting again.', 409,
                       code='post_admission_pending')
    return {'fields': {key: copy.deepcopy(session[key]) for key in
            ('navigationActivityAt', 'navigationActivityPending', 'recentActivityAt',
             'navigationPostActivity') if key in session},
            'activityAt': navigation_activity(session)}


def promote_human_post(session, message, previous):
    """Called only by ordinary send after host-trusted UI input insertion.

    The bounded chain contains unresolved admissions, not message history.
    Terminal retained/accepted posts compact its prefix; rejected posts disappear
    so rejecting a successor cannot resurrect a rejected predecessor.
    """
    at = max(navigation_activity(session), recent_activity(session))
    post = {'sessionId': session['id'], 'inputId': message['inputId'],
            'fence': str(uuid.uuid4()), 'previous': previous['fields'],
            'activityAt': at, 'disposition': 'pending'}
    message['navigationPost'] = post
    chain = session.setdefault('navigationPostAdmissions', {
        'previous': copy.deepcopy(previous['fields']), 'activityAt': previous['activityAt'],
        'posts': []})
    chain['posts'].append({'inputId': message['inputId'], 'fence': post['fence'],
                           'activityAt': at, 'rawAt': session.get('recentActivityAt')})
    session['navigationActivityAt'] = at
    session['navigationPostActivity'] = {key: post[key] for key in
                                         ('sessionId', 'inputId', 'fence', 'activityAt')}
    return post


def finish_human_post(session, message, disposition):
    """Resolve exactly this session/input/fence, independent of equal clocks."""
    post = message.get('navigationPost') if message else None
    if not post or post.get('sessionId') != session['id'] or post.get('inputId') != message.get('inputId'):
        return
    prior = post['disposition']
    if prior == 'rejected' or disposition == 'rejected' and prior != 'pending':
        return  # Retained uncertainty/positive admission is never absence.
    if prior == 'accepted' and disposition != 'accepted':
        return
    post['disposition'] = disposition
    chain = session.get('navigationPostAdmissions')
    if not chain:
        return  # Ready/attention or a newer terminal post already owns activity.
    posts = chain['posts']
    index = next((i for i, row in enumerate(posts)
                  if row['inputId'] == post['inputId'] and row['fence'] == post['fence']), None)
    if index is None:
        return
    row = posts[index]
    if disposition in {'accepted', 'retained'}:
        chain.update(previous={'navigationActivityAt': row['activityAt'],
                               'navigationActivityPending': True, 'recentActivityAt': row['rawAt'],
                               'navigationPostActivity': {key: post[key] for key in
                                   ('sessionId', 'inputId', 'fence', 'activityAt')}},
                     activityAt=row['activityAt'], posts=posts[index + 1:])
    elif disposition == 'rejected':
        owner = session.get('navigationPostActivity') or {}
        owns_position = all(owner.get(key) == post[key] for key in ('sessionId', 'inputId', 'fence'))
        # The bounded fence owns provisional raw activity, not the last bubble:
        # a refused steering bubble remains history after it stops owning recency.
        progress_at = timestamp(chain.get('progressAt'))
        raw_owned = (owns_position and session.get('recentActivityAt') == row['rawAt'] and
                     (not chain.get('progress') or progress_at is not None))
        posts.pop(index)
        if index == len(posts) and owns_position:
            previous = chain['previous']
            if raw_owned:
                if posts:
                    session['recentActivityAt'] = posts[-1]['rawAt']
                elif 'recentActivityAt' in previous:
                    session['recentActivityAt'] = copy.deepcopy(previous['recentActivityAt'])
                else:
                    session.pop('recentActivityAt', None)
                if progress_at is not None:
                    session['recentActivityAt'] = max(timestamp(session.get('recentActivityAt')) or 0, progress_at)
            if posts:
                session['navigationActivityAt'] = posts[-1]['activityAt']
                session['navigationActivityPending'] = True
                session['navigationPostActivity'] = {'sessionId': session['id'], **{
                    key: posts[-1][key] for key in ('inputId', 'fence', 'activityAt')}}
            else:
                for key in ('navigationActivityAt', 'navigationActivityPending', 'navigationPostActivity'):
                    if key in previous:
                        session[key] = copy.deepcopy(previous[key])
                    else:
                        session.pop(key, None)
                if chain.get('progress') or not raw_owned:
                    # A separate live input/progress owns raw activity. Retain its
                    # pending settlement, but not this rejected post's position.
                    session.setdefault('navigationActivityAt', chain['activityAt'])
                    session['navigationActivityPending'] = True
    if not chain['posts']:
        session.pop('navigationPostAdmissions', None)


def settle_activity(session):
    """Commit activity once the conversation is ready for the user's attention."""
    session.pop('navigationPostAdmissions', None)  # A settlement supersedes every admission fence.
    session.pop('navigationPostActivity', None)
    if session.pop('navigationActivityPending', False):
        session['navigationActivityAt'] = max(navigation_activity(session), recent_activity(session), time.time())


def runtime_activity(session, kind, payload):
    """Activity for sorting excludes lifecycle/setup and background naming."""
    active = False
    if kind == 'assistant.delta':
        active = bool(payload.get('text') or payload.get('delta'))
    elif kind == 'execution.event':
        active = bool(payload.get('id')) and payload.get('kind') in {'llm', 'tool', 'worker'} and payload.get('phase') != 'interrupted' and payload.get('label') != 'Session naming'
    elif kind == 'runtime.status':
        active = payload.get('status') == 'working' and (not payload.get('activityOnly') or session.get('status') in {'working', 'starting'})
    elif kind == 'runtime.generation':
        active = payload.get('event') in {'generation.started', 'generation.finished', 'generation.failed', 'generation.detached'}
    elif kind == 'worker.updated':
        active = payload.get('status') in {'running', 'working', 'queued', 'completed', 'error'} and payload.get('event') != 'job.recovered'
    elif kind == 'runtime.tool':
        active = payload.get('phase') in {'pre', 'post', 'error'}
    elif kind == 'runtime.error':
        active = session.get('status') == 'working'
    elif kind == 'approval.requested':
        active = True
    elif kind == 'approval.resolved':
        active = payload.get('decision') in {'allow', 'deny', 'approve', 'reject'}
    if active:
        touch_independent_activity(session)


def initialize(state):
    workers = {row['id'] for row in state.get('sessions', []) if not is_top_level(row)}
    pins = state.get('pinnedSessionIds', [])
    state['pinnedSessionIds'] = list(dict.fromkeys(identity for identity in pins
        if isinstance(identity, str) and identity and identity not in workers)) if isinstance(pins, list) else []
    if state.setdefault('view', {}).get('navChatScope') not in ('workspace', 'all'):
        state['view']['navChatScope'] = 'workspace'
    for session in state.get('sessions', []):
        session['recentActivityAt'] = recent_activity(session)
        if (session.get('navigationActivityPending') and session.get('status') == 'interrupted'
                and not session.get('navigationPostActivity')):
            session['navigationActivityAt'] = recent_activity(session)
            session.pop('navigationActivityPending', None)


def view_patch(patch):
    if 'navMode' in patch and patch['navMode'] not in ('browse', 'status'):
        raise ValueError('Choose Browse or Status.')
    for key in ('navStatusCollapsed', 'navStatusExpanded'):
        if key in patch:
            value = patch[key]
            if not isinstance(value, list) or len(value) > 6 or any(
                    item not in ('attention', 'unread', 'working', 'idle', 'waiting', 'paused') for item in value):
                raise ValueError('Choose valid sidebar status groups.')
    if 'navStatusPages' in patch:
        value = patch['navStatusPages']
        if not isinstance(value, dict) or any(
                key not in ('attention', 'unread', 'working', 'idle', 'waiting', 'paused')
                or type(index) is not int or not 0 <= index <= 1_000_000 for key, index in value.items()):
            raise ValueError('Choose valid sidebar page indexes.')
    # Browsing is client presentation, never a change to the active chat/folder.
    if 'workSurface' in patch and patch['workSurface'] not in ('chat', 'workspace', 'workspaces', 'chats', 'home'):
        raise ValueError('Choose home, chat, workspace, workspaces or chats.')
    if 'workWorkspaceChats' in patch:
        request = patch['workWorkspaceChats']
        if not isinstance(request, dict) or set(request) != {'workspaceId', 'query', 'pages'}:
            raise ValueError('Supply the active workspace chat query.')
        view_patch({'workHomeScope': request['workspaceId'], 'workHomeQuery': request['query'],
                    'workHomePages': request['pages']})
    if 'workHomeView' in patch and patch['workHomeView'] not in ('focus', 'brief', 'studio'):
        raise ValueError('Choose Focus, Brief or Studio.')
    if 'workHomeFilter' in patch and patch['workHomeFilter'] not in ('all', 'attention', 'unread', 'working', 'idle', 'waiting', 'paused'):
        raise ValueError('Choose a work status.')
    if 'workHomeScope' in patch and (not isinstance(patch['workHomeScope'], str) or not 1 <= len(patch['workHomeScope']) <= 100):
        raise ValueError('Choose a work scope.')
    if 'workHomeQuery' in patch and (not isinstance(patch['workHomeQuery'], str) or len(patch['workHomeQuery']) > 500):
        raise ValueError('Search text must be 500 characters or fewer.')
    if 'workHomePages' in patch:
        value = patch['workHomePages']
        if not isinstance(value, dict) or any(key not in ('attention', 'unread', 'working', 'idle', 'waiting', 'paused') or type(index) is not int or not 0 <= index <= 1_000_000 for key, index in value.items()):
            raise ValueError('Choose valid work page indexes.')
    if 'workWorkspaceTab' in patch and patch['workWorkspaceTab'] not in ('chats', 'work', 'files', 'details'):
        raise ValueError('Choose chats, work, files or details.')
    if 'workWorkspaceId' in patch and patch['workWorkspaceId'] is not None and (not isinstance(patch['workWorkspaceId'], str) or not 1 <= len(patch['workWorkspaceId']) <= 100):
        raise ValueError('Choose a registered workspace.')

    if 'navSectionsCollapsed' in patch:
        value = patch['navSectionsCollapsed']
        if not isinstance(value, list) or len(value) > 3 or any(
                item not in ('pinned', 'workspaces', 'recent') for item in value):
            raise ValueError('Choose sidebar sections to collapse.')
    if 'navPinnedPage' in patch and (type(patch['navPinnedPage']) is not int or not 0 <= patch['navPinnedPage'] <= 1_000_000):
        raise ValueError('Pinned page index must be a nonnegative integer.')
    if 'navShowAgentCreated' in patch and type(patch['navShowAgentCreated']) is not bool:
        raise ValueError('Show agent-created chats must be a boolean.')
    if 'navRecentLimit' in patch and not valid_recent_limit(patch['navRecentLimit']):
        raise ValueError('Recent limit must be a safe integer multiple of 20 chats.')
    if 'navRecentView' in patch:
        value = patch['navRecentView']
        if not isinstance(value, dict) or set(value) - SIDEBAR_FILTER_KEYS:
            raise ValueError('Invalid Recent chat filters.')
        view_patch(value)
    if 'navSort' in patch and patch['navSort'] not in ('activity', 'created', 'name'):
        raise ValueError('Choose recent activity, newest created, or name.')
    if 'navGroup' in patch and patch['navGroup'] not in ('recent', 'workspace', 'status'):
        raise ValueError('Choose Recent, Workspace or Status grouping.')
    if 'navAge' in patch and patch['navAge'] not in (*AGE_SECONDS, 'all'):
        raise ValueError('Choose 24h, 7d, 14d, 30d, 90d, 1y or All.')
    if 'navArchive' in patch and patch['navArchive'] not in ('active', 'archived', 'all'):
        raise ValueError('Choose active, archived or all conversations.')
    if 'navCollection' in patch and patch['navCollection'] is not None and (not isinstance(patch['navCollection'], str) or len(patch['navCollection']) > 200):
        raise ValueError('Choose a valid collection ID or null.')
    if 'subagentHistory' in patch:
        value = patch['subagentHistory']
        if not isinstance(value, dict) or set(value) - {'sessionId', 'filter', 'index'}:
            raise ValueError('Invalid worker history page.')
        if 'sessionId' in value and (not isinstance(value['sessionId'], str) or not 1 <= len(value['sessionId']) <= 200):
            raise ValueError('Choose a conversation for worker history.')
        if 'filter' in value and (not isinstance(value['filter'], str) or len(value['filter']) > 500):
            raise ValueError('Worker history search must be text of at most 500 characters.')
        if 'index' in value and (type(value['index']) is not int or not 0 <= value['index'] <= 1_000_000):
            raise ValueError('Worker history page index must be a nonnegative integer.')
    if 'navWorkspaceList' in patch and type(patch['navWorkspaceList']) is not bool:
        raise ValueError('navWorkspaceList must be a boolean.')
    if 'navLocationFilter' in patch and patch['navLocationFilter'] not in ('all', 'managed'):
        raise ValueError('Choose all locations or no workspace.')
    if 'navStatusFilter' in patch and patch['navStatusFilter'] not in ('all', 'attention', 'working', 'unread', 'idle', 'waiting', 'paused'):
        raise ValueError('Choose all, attention, working, unread, or idle conversations.')
    if 'navChatScope' in patch and patch['navChatScope'] not in ('workspace', 'all'):
        raise ValueError('navChatScope must be workspace or all.')
    if 'navFilter' in patch and (not isinstance(patch['navFilter'], str) or len(patch['navFilter']) > 500):
        raise ValueError('Chat search must be text of at most 500 characters.')
    if 'navChatPage' in patch:
        page = patch['navChatPage']
        if not isinstance(page, dict) or type(page.get('index')) is not int or not 0 <= page['index'] <= 1_000_000:
            raise ValueError('Chat page index must be a nonnegative integer.')
        if page.get('mode') not in ('workspace', 'all'):
            raise ValueError('Chat page scope must be workspace or all.')
        for key in ('workspaceId', 'selectedSessionId'):
            if page.get(key) is not None and (not isinstance(page[key], str) or len(page[key]) > 200):
                raise ValueError('Invalid chat page scope.')
        if not isinstance(page.get('filter'), str) or len(page['filter']) > 500:
            raise ValueError('Invalid chat page search.')
    return patch


def _matches(terms, query):
    query = query.strip().casefold()
    if any(character in query for character in '*?['):
        return any(fnmatchcase(str(term).casefold(), query) for term in terms)
    return any(query in str(term).casefold() for term in terms)


def registry(state):
    """Shared workspace resolution and root grouping for navigation queries."""
    workspaces = [row for row in state.get('workspaces', [])
                  if row.get('available') is True and isinstance(row.get('path'), str) and row['path']]
    by_id = {row['id']: row for row in workspaces}
    by_path = {row['path']: row for row in workspaces}
    labels = path_labels(by_path)
    roots, grouped = [], {}
    for row in state.get('sessions', []):
        if is_top_level(row):
            roots.append(row)
            workspace = by_id.get(row.get('workspaceId')) or by_path.get(row.get('workspace'))
            if workspace is not None:
                grouped.setdefault(workspace['id'], []).append(row)
    return by_id, by_path, labels, roots, grouped


def catalog(state, *, indexed=None, pin_first=True, section=None):
    """Filter/sort once; selected chat and pagination belong to each client."""
    by_id, by_path, labels, roots, grouped = registry(state) if indexed is None else indexed
    selected = by_id.get(state.get('selectedWorkspaceId'))
    view = state.get('view', {})
    mode = 'all' if view.get('navChatScope') == 'all' else 'workspace'
    query = view.get('navFilter', '')
    query = query if isinstance(query, str) else ''
    scope = {'mode': mode, 'workspaceId': selected['id'] if mode == 'workspace' and selected else None,
             'filter': query, 'selectedSessionId': state.get('selectedSessionId')}
    sort = view.get('navSort', 'activity')
    grouped_catalog = section in {'search', 'workspace'}
    group, age = view.get('navGroup', 'recent'), view.get('navAge', 'all')
    now = state.get('_navigationListClock', list_clock())
    scope['age'] = age
    if grouped_catalog:
        scope['group'] = group
    if sort != 'activity':
        scope['sort'] = sort
    location_filter = view.get('navLocationFilter', 'all') if mode == 'all' else 'all'
    if location_filter != 'all':
        scope['locationFilter'] = location_filter
    status_filter = view.get('navStatusFilter', 'all')
    if status_filter != 'all':
        scope['statusFilter'] = status_filter
    counts = dict.fromkeys(('attention', 'working', 'unread', 'idle'), 0)
    from .attention import reviewed_session_errors
    attention = state.get('attention', {})
    unread = attention.get('sessions', {})
    reviewed_errors = reviewed_session_errors(attention)
    pins = set(state.get('pinnedSessionIds', []))
    pin_order = {sid: i for i, sid in enumerate(state.get('pinnedSessionIds', []))}
    organization = state.get('conversationOrganization', {})
    archived = organization.get('archived', {})
    archive_filter = view.get('navArchive', 'active')
    if archive_filter != 'active':
        scope['archive'] = archive_filter
    rows = []
    for session in roots if mode == 'all' else grouped.get(selected['id'], []) if selected else []:
        is_archived = session['id'] in archived
        if (archive_filter == 'active' and is_archived) or (archive_filter == 'archived' and not is_archived):
            continue
        workspace = by_id.get(session.get('workspaceId')) or by_path.get(session.get('workspace'))
        managed = is_managed(session)
        if location_filter == 'managed' and not managed:
            continue
        if mode == 'workspace' and (managed or selected is None or workspace is None or workspace['id'] != selected['id']):
            continue
        if workspace is None:
            if not managed:
                continue
            workspace = {'id': None, 'path': session.get('workspace', ''), 'name': 'No workspace'}
        title = session.get('title') or 'Untitled conversation'
        description = session.get('description') or ''
        shared_id = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
        if not _matches((title, description, session['id'], shared_id, workspace['path'], workspace.get('name', '')), query):
            continue
        if age in AGE_SECONDS and session['id'] not in pins:
            at = navigation_activity(session)
            if at <= 0 or at < now - AGE_SECONDS[age]:
                continue
        summary = activity(session, bool(unread.get(session['id'])),
                           error_reviewed=session['id'] in reviewed_errors,
                           blocked=task_blocked(state, session['id']))
        counts[summary['kind']] = counts.get(summary['kind'], 0) + 1
        if status_filter != 'all' and summary['kind'] != status_filter:
            continue
        rows.append({'id': session['id'], 'title': title, 'description': description,
                     **{key: session[key] for key in ('titleSource', 'nativeNameSource', 'autoName', 'naming', 'configurationBusy') if key in session},
                     'status': session.get('status', 'idle'), 'workspace': workspace['path'],
                     'workspaceId': workspace['id'], 'workspaceName': workspace.get('name', ''),
                     'workspaceLabel': 'No workspace' if managed else labels[workspace['path']], 'activity': summary,
                     **({'location': {'kind': 'managed'}} if managed else {}),
                     'runtimeSessionId': session.get('runtimeSessionId') or session.get('nativeIdentity'),
                     'createdAt': timestamp(session.get('createdAt')), 'pinned': session['id'] in pins,
                     'agentCreated': bool(is_agent_created(session)),
                     **({'archived': True} if is_archived else {}),
                     'recentActivityAt': navigation_activity(session)})
    # Python's stable sort preserves source-array order for equal timestamps.
    rows.sort(key=lambda row: (not row['pinned'] if pin_first else False,
        pin_order.get(row['id'], 0) if pin_first and row['pinned'] else
        (row['title'].lower() if sort == 'name' else
         -(row['createdAt'] or 0) if sort == 'created' else -row['recentActivityAt'])))
    if grouped_catalog:
        rows = [group_row(row, group, now) for row in rows]
        # Stable sort retains saved pin order and the chosen sort within groups.
        rows.sort(key=lambda row: (
            list_group(group, row['pinned'], row['recentActivityAt'], row['workspaceId'],
                       row['workspaceName'], row['activity']['kind'], now)[2],
            row['group']['title'].lower() if group == 'workspace' and not row['pinned'] else '',
            row['group']['id'] if group == 'workspace' and not row['pinned'] else ''))
    return rows, scope, counts


def snapshot(state: dict[str, Any], *, indexed=None, section: str | None = None) -> ChatPageResult:
    view = state.get('view', {})
    # Section counts are computed before the activity filter, so switching from
    # Attention to Working still reports the full matching count for each.
    catalog_state = {**state, 'view': {**view, 'navStatusFilter': 'all'}} if section else state
    rows, scope, counts = catalog(catalog_state, pin_first=section not in ('recent', 'shortcuts'), section=section) if indexed is None else indexed
    scope = {**scope, 'selectedSessionId': state.get('selectedSessionId')}
    if section:
        scope['section'] = section
        if section == 'pinned':
            rows = [row for row in rows if row['pinned']]
        if section in ('recent', 'shortcuts', 'search'):
            scope['showAgentCreated'] = view.get('navShowAgentCreated') is True
            rows = [row for row in rows if scope['showAgentCreated']
                    or not row['agentCreated'] or row['id'] == scope['selectedSessionId']]
        counts = {kind: sum(row['activity']['kind'] == kind for row in rows)
                  for kind in counts}
        status = view.get('navStatusFilter', 'all')
        scope.pop('statusFilter', None)
        if status != 'all':
            scope['statusFilter'] = status
            rows = [row for row in rows if row['activity']['kind'] == status]
    if section == 'shortcuts':
        # Quiet Recent owns a cumulative row limit, not any full-browser page.
        limit = view.get('navRecentLimit', 20)
        limit = limit if valid_recent_limit(limit) else 20
        scope.update(limit=limit, viewRevision=view.get('navRecentRevision', 0))
        end = min(len(rows), limit)
        return {'items': rows[:end], 'total': len(rows), 'remaining': len(rows) - end,
                'limit': limit, 'start': 0, 'end': end, 'scope': scope,
                'dataRevision': state.get('revision', 0), 'activityCounts': counts}
    page_size = (40 if len(rows) > 50 else 50) if section else PAGE_SIZE
    mode = scope['mode']
    saved = view.get('navChatPage')
    matched = isinstance(saved, dict) and saved.get('sort', 'activity') == scope.get('sort', 'activity') and all(saved.get(key) == value for key, value in scope.items())
    inferred = next((i // page_size for i, row in enumerate(rows) if row['id'] == scope['selectedSessionId']), 0) if mode == 'workspace' and section != 'recent' else 0
    requested = saved['index'] if matched and type(saved.get('index')) is int else inferred
    if section == 'pinned':
        requested = view.get('navPinnedPage', 0)
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    index = max(0, min(pages - 1, requested))
    start, end = index * page_size, min(len(rows), (index + 1) * page_size)
    return {'items': rows[start:end], 'total': len(rows), 'index': index, 'pages': pages,
            'start': start, 'end': end, 'scope': scope, 'activityCounts': counts}
