"""Automatic navigation over native sessions; transcripts stay in their files."""
from __future__ import annotations

import asyncio
from collections import Counter, deque
import copy
import hashlib
import json
import sqlite3
from pathlib import Path
import time
import uuid

from .history_read_retry import retry_indexed_read
from .performance_diagnostics import measured, measure, count, TimedLock
from .session_files import amplifier_home, project_slug
from .managed_chats import catalog_locations
from amplifier_foundation.session.history import SessionHistoryStore
from .shared_state_probe import text_content

BUSY = {'starting', 'working', 'running', 'stopping', 'ready'}
INDEX_FIELDS = ('location', 'draft', 'id', 'title', 'titleSource', 'nativeNameSource', 'nativeNameRevision', 'nativeNamePolicyRevision', 'autoName', 'naming', 'description', 'bundle', 'workspace',
                'workspaceId', 'workspaceAvailable', 'createdAt', 'updatedAt', 'recentActivityAt', 'navigationActivityAt', 'navigationActivityPending',
                'workingDirectory', 'executionRevision', 'executionHost', 'executionHandoffId',
                'runtimeSessionId', 'nativeIdentity', 'nativeProject', 'parentId', 'nativeParentId',
                'nativeRevision', 'nativeBoundary', 'nativeBoundaryId', 'turnCount', 'shared',
                'historyManaged', 'historyReadOnlyReason', 'draftAttachments', 'sessionKind', 'sessionPurpose',
                'messageAnnotations', 'messageAnnotationRevision', 'messageReactionOrder', 'messageQuotes', 'nativeAvailable', 'collaboration', 'coordinationReference')


def resident_catalog_facts(row):
    # Only this merge's mutable metadata, including presence, parent aliases and
    # private native-reference inputs. Never traverse messages or execution just
    # to decide whether a metadata journal changed an app-owned resident.
    return copy.deepcopy({key: row[key] for key in (
        'title', 'titleSource', 'nativeNameSource', 'nativeNameRevision',
        'nativeNamePolicyRevision', 'autoName', 'description', 'bundle',
        'workspace', 'workspaceId', 'workspaceAvailable', 'createdAt', 'updatedAt',
        'recentActivityAt', 'navigationActivityAt', 'nativeProject', 'nativeIdentity',
        'nativeRevision', 'parentId', 'nativeParentId', 'turnCount', 'sessionKind',
        'sessionPurpose', 'historyReadOnlyReason', 'location', 'nativeAvailable',
        '_catalogRecentAt', '_catalogId') if key in row})


def workspace_inputs(state):
    # Discovery needs identity/availability, not arbitrary presentation bodies.
    return [{key: row.get(key) for key in ('id', 'path', 'nativeProject', 'available')}
            for row in state['workspaces']]


def identity(project, session):
    return uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-native:{project}/{session}').hex


def directory(session):
    project = session['nativeProject']
    if not isinstance(project, str) or project in {'', '.', '..'} or Path(project).name != project:
        raise ValueError('Invalid native project identity.')
    root = amplifier_home() / 'projects'
    session_id = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
    if not isinstance(session_id, str) or session_id in {'', '.', '..'} or '/' in session_id or '\\' in session_id or '\x00' in session_id:
        raise ValueError('Invalid native session identity.')
    path = root / project / 'sessions' / session_id
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Session storage must remain inside the shared projects folder.')
    return path


def revision(session):
    for name in ('transcript.jsonl', 'transcript.jsonl.backup'):
        try:
            info = (directory(session) / name).stat()
            pending = directory(session) / 'transcript.jsonl.append-pending'
            return [info.st_mtime_ns, info.st_size, 'append'] if pending.exists() else [info.st_mtime_ns, info.st_size]
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return None


def display_identity(session, index, role, text):
    key = json.dumps([session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id'], index, role, text], ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def display_message(row, index, session, *, include_internal=False):
    if not isinstance(row, dict) or row.get('role') not in {'user', 'assistant'}:
        return None
    metadata = row.get('metadata') or {}
    if not include_internal and any(metadata.get(key) for key in
            ('ephemeral', 'amplifier_visible_reference', 'amplifier_recovery_reference')):
        return None
    text = text_content(row)
    if not text:
        return None
    from .session_store import message_time
    metadata=row.get('metadata') or {}
    provenance=metadata.get('amplifier_input',{})
    recovery=metadata.get('live_recovery_job')
    if 'amplifier_input' not in metadata and isinstance(recovery,str) and 0<len(recovery)<=128:
        # Earlier loop-live versions recorded recovery ownership separately.
        # Read that host metadata; matching text alone is never provenance.
        provenance={'version':1,'kind':'service','id':recovery,'source':'local-job-recovery'}
    observation={}
    input_identity = {}
    # Request-boundary steering persists the same canonical input ID under a
    # different delivery kind. Keep it for exact web/native reconciliation;
    # neither the role nor a host-looking text envelope proves peer authorship.
    if (row['role'] == 'user' and isinstance(provenance, dict)
            and provenance.get('version') == 1 and provenance.get('kind') in ('user', 'steer')
            and isinstance(provenance.get('id'), str) and 0 < len(provenance['id']) <= 200):
        input_identity = {'nativeInputId': provenance['id']}
    if isinstance(provenance,dict) and provenance.get('version')==1 and provenance.get('kind')=='service' and all(isinstance(provenance.get(key),str) and 0<len(provenance[key])<=128 for key in ('id','source')):
        observation={'observation':{key:provenance[key] for key in ('id','source','call_id') if key in provenance}}
        from .recovery_observations import recovery_facts
        facts = recovery_facts(metadata, text, provenance)
        if facts:
            observation['observation']['recovery'] = facts
    identity = display_identity(session, index, row['role'], text)
    voice = None
    if row['role'] == 'user' and not include_internal:
        from .voice_input_projection import public_input
        voice = public_input(text, provenance)
    voice_fields = ({'voiceDelegation': True, 'voiceCallId': voice['callId'],
                     'voiceInputId': provenance['id'],
                     'nativeMessageId': identity, 'inputOrigin': 'voice'} if voice else {})
    return {'id': identity, 'role': row['role'],
            'text': voice['text'] if voice else text, 'via': 'call' if voice else 'chat', 'source': 'native', 'nativeIndex': index,
            'createdAt': message_time(row) or session.get('createdAt', 0), 'timestampKnown': message_time(row) is not None,
            **input_identity, **observation, **voice_fields}




@retry_indexed_read
def read_transcript(session, *, before=None, limit=100, diagnostics=None, include_activity=True):
    """Read display messages, optionally with their saved execution activity.

    Text-only retrieval omits activity entirely; browser pages retain it by
    default. Neither path changes canonical history or its display identities.
    """
    from types import SimpleNamespace
    # The reader receives only the thread-safe metrics owner, never app state.
    service = SimpleNamespace(performance_diagnostics=diagnostics)
    root = directory(session)
    native_id = session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id']
    # Relocation is CI policy, independent of transcript storage. It also works
    # for read-only historical child IDs that cannot start a new manager.
    import os
    raw_root = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', '').strip()
    event_root = Path(raw_root).expanduser() if raw_root and '${' not in raw_root else None
    events = (event_root / session['nativeProject'] / 'sessions' / native_id /
              'context-intelligence' / 'events.jsonl') if event_root is not None and event_root.is_absolute() else None
    reader = SessionHistoryStore(root, events_path=events, session_id=native_id)
    start = revision(session)
    with measure(service, 'history.transcript.index', synchronous=True, session_id=session['id']):
        messages = reader.indexed_messages()
    diagnostics = list(reader.diagnostics)
    # A live runtime can append while someone is reading older pages. Verify
    # the loaded native anchors instead of requiring the whole file's stamp to
    # remain the same since the last tail refresh. A rewrite that moves or
    # replaces those anchors still requires a refresh before paging.
    check_anchors = before is not None and 'nativeRevision' in session and session['nativeRevision'] != start
    anchors = {message['nativeIndex']: message.get('nativeMessageId') or display_identity(session, message['nativeIndex'],
                message['role'], message.get('text', ''))
               for message in session.get('messages', []) if type(message.get('nativeIndex')) is int} if check_anchors else {}
    first_anchor = min(anchors) if anchors else None
    matched = set()
    rows = deque(maxlen=limit)
    total = users = 0
    hidden = {}
    from amplifier_foundation.session.jsonl import TranscriptIndex

    def project(value, index):
        row = display_message(value, index, session)
        internal = row or display_message(value, index, session, include_internal=True)
        return (row['id'] if row else None, internal['id'] if internal else None,
                value.get('role'), row.get('nativeInputId') if row else None)

    with measure(service, 'history.transcript.facts', synchronous=True, session_id=session['id']):
        facts = (messages.project('unified-display-v4:' + str(native_id), project)
                 if isinstance(messages, TranscriptIndex)
                 else [project(value, index) for index, value in enumerate(messages)])
    input_counts = Counter(fact[3] for fact in facts if fact[3])
    for index, (identity, internal_identity, role, _) in enumerate(facts):
        if check_anchors and index in anchors:
            if (identity != anchors[index]
                    or (index == first_anchor and total != session.get('sharedHistoryOffset', 0))):
                raise ValueError('The saved chat changed. Refresh it before loading earlier messages.')
            matched.add(index)
        if identity is None:
            if internal_identity is not None:
                hidden[index] = internal_identity
            continue
        if before is None or total < before:
            rows.append((total, users, index))
        total += 1
        users += role == 'user'
    if check_anchors and (not anchors or matched != anchors.keys()):
        raise ValueError('The saved chat changed. Refresh it before loading earlier messages.')
    if revision(session) != start or any(d.code == 'changed_during_read' for d in diagnostics):
        raise ValueError('The CLI is saving this chat. Its history will refresh shortly.')
    selected = [row[2] for row in rows]
    bodies = messages.read_positions(selected) if isinstance(messages, TranscriptIndex) else [messages[i] for i in selected]
    visible = [display_message(value, index, session) for index, value in zip(selected, bodies)]
    for row in visible:
        if input_counts[row.get('nativeInputId')] > 1:
            row['nativeInputAmbiguous'] = True
    result = {'messages': visible, 'offset': rows[0][0] if rows else 0,
              'userOffset': rows[0][1] if rows else 0, 'total': total, 'revision': start,
              'hiddenMessages': hidden}
    if include_activity:
        from .native_activity import activity_page
        with measure(service, 'history.activity', synchronous=True, session_id=session['id']):
            activity = activity_page(reader, messages, visible, service=service)
        activity['diagnostics'] = [dict(code=d.code, source=d.source, line=d.line, severity=d.severity)
                                   for d in diagnostics] + activity['diagnostics']
        result['activity'] = activity
    return result


def remove_internal_copies(session, hidden):
    """Remove old UI copies only when native index, role and full text agree.

    The reader classified these rows from canonical metadata. Tags or matching
    text alone are never evidence that a user's message should be hidden.
    Canonical messages and their resume context are not changed.
    """
    session['messages'] = [message for message in session['messages']
        if type(message.get('nativeIndex')) is not int
        or hidden.get(message['nativeIndex']) != display_identity(
            session, message['nativeIndex'], message.get('role'), message.get('text', ''))]
    boundary = session.get('nativeBoundary')
    if type(boundary) is int and boundary in hidden and hidden[boundary] == session.get('nativeBoundaryId'):
        # A prior reader could anchor its append cursor on a trailing reminder.
        # Re-establish it from the last retained, verified native message.
        session.pop('nativeBoundary', None)
        session.pop('nativeBoundaryId', None)


def align_expanded_inputs(current, incoming, *, peer_only=False):
    """Use canonical input metadata, never an expanded-text prefix.

    The native prompt includes attachment or mention context, so its text differs
    from the web bubble. Bind its exact native identity while retaining the
    bubble and its attachment metadata. Repair an earlier display copy only
    when its native index, ID and text all still match the canonical row.
    Passive peer repair leaves unrelated mention/attachment export policy alone;
    all inputs still participate in uniqueness checks before that filter.
    """
    from .peer_attribution import input_identity
    native_by_input = {}
    web_by_input = {}
    cached_by_index = {}
    for row in incoming:
        identity = input_identity({'nativeInputId': row.get('nativeInputId')})
        if identity is not None:
            native_by_input.setdefault(identity, []).append(row)
    for position, row in enumerate(current):
        identity = input_identity({'inputId': row.get('inputId')})
        if row.get('role') == 'user' and identity is not None and row.get('source') != 'native':
            web_by_input.setdefault(identity, []).append(position)
        if row.get('source') == 'native':
            cached_by_index.setdefault(row.get('nativeIndex'), []).append(position)
    replacements, copies = {}, set()
    for identity, positions in web_by_input.items():
        natives = native_by_input.get(identity, [])
        if len(positions) != 1 or len(natives) != 1 or natives[0].get('nativeInputAmbiguous'):
            continue
        position, native = positions[0], natives[0]
        message = current[position]
        if peer_only and not isinstance(message.get('peerEnvelope'), dict):
            continue
        if 'peerEnvelope' in message:
            from amplifier_operations.coordination import peer_input
            if (not isinstance(message['peerEnvelope'], dict)
                    or native.get('text') != peer_input(message['peerEnvelope'], message.get('text', ''))):
                continue
        if ((message.get('nativeIndex') is not None and message['nativeIndex'] != native['nativeIndex'])
                or (message.get('nativeMessageId') and message['nativeMessageId'] != native['id'])):
            raise ValueError('The saved conversation was rewritten; existing web messages were kept.')
        replacements[position] = {**message, 'nativeMessageId': native['id']}
        for number in cached_by_index.get(native['nativeIndex'], []):
            saved = current[number]
            if (saved.get('id') == native['id'] and saved.get('text') == native['text']
                    and saved.get('role') == native['role']):
                copies.add(number)
                # Replace the old display copy's anchor. A newly matched input
                # must instead enter normal ordered alignment below, so native
                # replies between it and other inputs are still inserted.
                replacements[position]['nativeIndex'] = native['nativeIndex']
    return [replacements.get(number, row) for number, row in enumerate(current) if number not in copies]


def alias_peer_inputs(session, native):
    """Reuse one retained web bubble at its exact unique native input identity.

    This is display alignment, not attribution. The owning service still needs
    its retained receipt and exact envelope binding before deriving a caption.
    No role/text multiplicity can transfer an input's identity.
    """
    from amplifier_operations.coordination import peer_input
    from .peer_attribution import input_identity
    by_input, web = {}, {}
    for row in native:
        identity = input_identity({'nativeInputId': row.get('nativeInputId')})
        if identity is not None:
            by_input.setdefault(identity, []).append(row)
    for row in session.get('messages', []):
        identity = input_identity({'inputId': row.get('inputId')})
        if row.get('role') == 'user' and row.get('source') != 'native' and identity is not None:
            web.setdefault(identity, []).append(row)
    replacements = {}
    for identity, candidates in by_input.items():
        if len(candidates) != 1 or len(web.get(identity, [])) != 1:
            continue
        original, saved = web[identity][0], candidates[0]
        envelope = original.get('peerEnvelope')
        if (not isinstance(envelope, dict) or saved.get('nativeInputAmbiguous')
                or saved['text'] != peer_input(envelope, original.get('text', ''))
                or original.get('nativeMessageId') not in (None, saved['id'])
                or original.get('nativeIndex') not in (None, saved['nativeIndex'])):
            continue
        replacements[saved['id']] = {**original, 'nativeIndex': saved['nativeIndex'],
                                     'nativeMessageId': saved['id']}
    return [replacements.get(row['id'], row) for row in native]


def same_native_message(native, visible):
    if native.get('role') != visible.get('role'):
        return False
    if visible.get('nativeMessageId'):
        return visible['nativeMessageId'] == native['id']
    if native.get('voiceDelegation'):
        return False  # Only the verified binding above may link spoken rows.
    return native.get('text') == visible.get('text')


def merge_web_history(session, incoming):
    """Append native messages after an ordered boundary, preserving web IDs.

    Native indexes disambiguate repeated prompts. Initial alignment is ordered,
    and every subsequent read verifies the boundary before appending anything.
    The full native text view is provided so a long CLI continuation cannot
    fall outside a fixed-size tail window and disappear from the web view.
    Legacy UI rows without indexes use ordered text alignment; distinguishing
    an identical failed prompt from a saved prompt would require an input ledger.
    """
    from .voice_input_projection import align as align_voice_inputs
    current, _ = align_voice_inputs(session, incoming)
    current = align_expanded_inputs(current, incoming)
    indexed = {message['nativeIndex']: (number, message) for number, message in enumerate(incoming)}
    boundary = session.get('nativeBoundary')
    boundary_id = session.get('nativeBoundaryId')
    cursor = start = 0
    if boundary is None:
        # A just-promoted native chat already has stable native indexes.
        anchored = [(number, message) for number, message in enumerate(current)
                    if isinstance(message.get('nativeIndex'), int)]
        if anchored:
            number, message = max(anchored, key=lambda item: item[1]['nativeIndex'])
            match = indexed.get(message['nativeIndex'])
            if not match or not same_native_message(match[1], message):
                raise ValueError('The saved conversation was rewritten; existing web messages were kept.')
            cursor, start = match[0] + 1, number + 1
    elif boundary >= 0:
        match = indexed.get(boundary)
        if not match or match[1]['id'] != boundary_id:
            raise ValueError('The saved conversation was rewritten; existing web messages were kept.')
        cursor = match[0] + 1
        start = next((number + 1 for number in range(len(current) - 1, -1, -1)
                      if current[number].get('nativeIndex') == boundary), len(current))
    # Steering inputs are displayed as soon as submitted, but enter the native
    # transcript at the next model boundary. User/assistant order can therefore
    # cross. Align each role one-to-one before inserting native-only messages;
    # otherwise a later user match inserts a second copy of a live reply.
    matches = {}
    claimed = set()
    role_cursors = {}
    for position, message in enumerate(current[start:], start):
        role = message.get('role')
        begin = role_cursors.get(role, cursor)
        anchor = indexed.get(message.get('nativeIndex'))
        if anchor and not same_native_message(anchor[1], message):
            raise ValueError('The saved conversation was rewritten; existing web messages were kept.')
        match = anchor[0] if anchor else next((number for number in range(begin, len(incoming))
                      if same_native_message(incoming[number], message)), None)
        if match is not None:
            matches[position] = match
            claimed.add(match)
            role_cursors[role] = max(begin, match + 1)
    merged = current[:start]
    for position, message in enumerate(current[start:], start):
        match = matches.get(position)
        if match is not None:
            merged.extend(incoming[number] for number in range(cursor, match) if number not in claimed)
            message['nativeIndex'] = incoming[match]['nativeIndex']
            cursor = max(cursor, match + 1)
        merged.append(message)
    merged.extend(incoming[number] for number in range(cursor, len(incoming)) if number not in claimed)
    for message in merged:
        match=indexed.get(message.get('nativeIndex'))
        if match and (match[1]['role'],match[1]['text'])==(message.get('role'),message.get('text')) and match[1].get('observation'):
            # Upgrade already-saved display copies without changing identities,
            # canonical history, or similarly worded user messages.
            message['observation']=copy.deepcopy(match[1]['observation'])
    session['messages'] = merged
    session['nativeBoundary'] = incoming[-1]['nativeIndex'] if incoming else -1
    session['nativeBoundaryId'] = incoming[-1]['id'] if incoming else None


class AutomaticHistory:
    def __init__(self, service):
        from .native_history import NativeHistory
        self.service = service
        self.naming = None
        self.index = NativeHistory(watch=True, directory=hasattr(service, 'directory'), cache_path=(
            service.data_dir / 'native-catalog.sqlite3' if hasattr(service, 'data_dir') else None),
            diagnostics=getattr(service, 'performance_diagnostics', None))
        self.lock = asyncio.Lock()
        self.loads = {}
        from .transcript_windows import TranscriptWindows
        self.windows = TranscriptWindows(service)
        self.task = None
        self.last_scan = None
        self._native_snapshot = None
        self._native_revision = None
        self._catalog_rows = {}
        self._catalog_workspaces = {}
        self._catalog_counts = None
        self._existing = None
        self._public_ids = set()
        self._local_revision = None
        self._availability_cursor = 0
        service.state.setdefault('sharedHistory', {}).update(loading=True, error=None)

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.windows.close()
        await asyncio.to_thread(self.index.close)

    async def loop(self):
        while not self.service.closed:
            known = workspace_inputs(self.service.state)
            if await asyncio.to_thread(self.index.needs_scan, known):
                await self.refresh(force=False)
            if hasattr(self.service, 'cold_display'):
                async with self.service.lock:
                    try:
                        self.service.cold_display.retire()
                    except (OSError, ValueError, sqlite3.Error):
                        # Exact fields remain resident on failed storage. This
                        # optional retirement must not stop canonical discovery.
                        pass
            try:
                if self.naming is None:
                    from .automatic_naming import AutomaticNaming
                    async with self.service.lock:
                        self.naming = AutomaticNaming(self.service)
                await self.naming.schedule()
            except (OSError, ValueError, sqlite3.Error):
                # Optional naming cannot stop native catalog discovery.
                pass
            # Prioritize targeted watcher backlog. Ordinary cold discovery
            # gets one cooperative 64-step/25ms slice per second; it must not
            # keep the host busy reconciling thousands of dormant histories.
            targeted = (bool(getattr(self.index, '_recovery_urgent', ()))
                        or bool(getattr(self.index, '_pending_invalidations', ())))
            recovering = getattr(self.index, '_recovery', None) is not None
            await asyncio.sleep(.1 if targeted else 1 if recovering else 15)

    def hide_session(self, session):
        project = session.get('nativeProject') or project_slug(session['workspace'])
        key = identity(project, session.get('nativeIdentity') or session.get('runtimeSessionId') or session['id'])
        hidden = self.service.state.setdefault('hiddenNativeSessions', [])
        if key not in hidden:
            hidden.append(key)

    def hide_workspace(self, workspace):
        hidden = self.service.state.setdefault('hiddenNativeWorkspaces', [])
        if workspace['id'] not in hidden:
            hidden.append(workspace['id'])

    @measured('catalog.refresh')
    async def refresh(self, *, force=True):
        if await self.refresh_catalog(force=force):
            try:
                # Catalog metadata is independent of connected clients' bodies.
                # Empty metadata is not proof of an unchanged open transcript;
                # keep probing it, under its own per-session load lock.
                await self._refresh_selected_history()
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._refresh_error()

    @measured('catalog.refresh.metadata')
    async def refresh_catalog(self, *, force=True):
        async with self.lock:
            try:
                known = workspace_inputs(self.service.state)
                from .workspace_canvas import refresh_workspace_availability
                cursor = self._availability_cursor % max(1, len(known))
                availability = known if force else known[cursor:cursor + 64]
                self._availability_cursor = (cursor + len(availability)) % max(1, len(known))
                availability_before = [row.get('available') for row in availability]
                await asyncio.to_thread(refresh_workspace_availability, availability)
                availability_changed = availability_before != [row.get('available') for row in availability]
                delta_mode = (hasattr(self.service, '_history_local_revision')
                          and 'scan_if_changed' not in self.index.__dict__)
                directory_mode = bool(delta_mode and getattr(self.index, 'directory', False)
                                      and getattr(self.service, 'directory', None) is not None)
                if delta_mode:
                    revision_token, incoming = await asyncio.to_thread(self.index.scan_changes,
                        known_workspaces=known, force=force, since=self._native_revision)
                    from collections import Counter
                    if incoming['reset']:
                        self._catalog_rows.clear()
                        self._catalog_workspaces.clear()
                        self._catalog_counts = Counter()
                    if not directory_mode:
                        for key in incoming['removed']:
                            old = self._catalog_rows.pop(tuple(key), None)
                            if old is not None:
                                self._catalog_counts[old['nativeIdentity']] -= 1
                        for row in incoming['sessions']:
                            key = (row['nativeProject'], row['nativeIdentity'])
                            if key not in self._catalog_rows:
                                self._catalog_counts[row['nativeIdentity']] += 1
                            self._catalog_rows[key] = row
                    for row in incoming['workspaces']:
                        self._catalog_workspaces[row['nativeProject']] = row
                    for project in incoming['removedProjects']:
                        self._catalog_workspaces.pop(project, None)
                    self._native_snapshot = {**incoming, 'sessions': self._catalog_rows.values(),
                                             'workspaces': self._catalog_workspaces.values()}
                    full_merge = (incoming['reset'] or force
                                  or self._local_revision != self.service._history_local_revision)
                    # Local overlays are independently invalidated by unscoped
                    # commits. A full recovery/manual pass retains their checks.
                    if full_merge:
                        self._existing = None
                    snapshot = dict(self._native_snapshot if full_merge else incoming)
                    if directory_mode:
                        owner = self.service.directory
                        owner.bind(self.index._catalog)
                        owner.changed(self.index._catalog.generations() if self.index._catalog is not None else None)
                        # Changed dormant metadata already lives in SQL. Admit
                        # nothing merely because discovery observed its files.
                        admitted = {(row.get('nativeProject') or (project_slug(row['workspace']) if row.get('workspace') else None),
                                     row.get('nativeIdentity') or row.get('runtimeSessionId') or row['id'])
                                    for row in self.service._state['sessions']}
                        workspace_projects = {row['nativeProject'] for row in incoming['workspaces']}
                        refresh_keys = admitted if full_merge else {
                            key for key in admitted if key[0] in workspace_projects}
                        if full_merge or refresh_keys:
                            def selected_rows():
                                return [row for project, identity in refresh_keys
                                        if project and self.index._catalog is not None and (row := self.index._catalog.get(project, identity)) is not None]
                            updates = await asyncio.to_thread(selected_rows)
                            if not full_merge:
                                # A workspace-only delta updates typed native
                                # availability without rewriting dormant rows.
                                # Reconcile its admitted owners, including their
                                # read-only reason, through current point reads.
                                other_keys = admitted - refresh_keys
                                updates.extend(row for row in incoming['sessions']
                                    if (row['nativeProject'], row['nativeIdentity']) in other_keys)
                        else:
                            updates = [row for row in incoming['sessions']
                                       if (row['nativeProject'], row['nativeIdentity']) in admitted]
                        self._catalog_counts = Counter(owner.native_counts(row['nativeIdentity'] for row in updates))
                        snapshot = {**incoming, 'sessions': updates,
                                    'workspaces': list(self._catalog_workspaces.values()) if full_merge else incoming['workspaces']}
                        self._native_snapshot = snapshot
                        self._existing = None  # bounded admitted ownership, not the dormant directory
                else:
                    revision_token, incoming = await asyncio.to_thread(self.index.scan_if_changed,
                        known_workspaces=known, force=force, since=self._native_revision)
                    if incoming is not None:
                        self._native_snapshot = incoming
                        self._native_revision = revision_token
                    snapshot = dict(self._native_snapshot)
                if delta_mode:
                    count(self.service, 'catalog.delta.rows', len(incoming['sessions']))
                    count(self.service, 'catalog.metadata.reads', incoming.get('metadataReads', 0))
                    count(self.service, 'catalog.recovery.steps', incoming.get('reconciliation', {}).get('steps', 0))
                    if not incoming.get('notificationsReady', False):
                        count(self.service, 'catalog.scan.unwatched')
                    empty_delta = not any(incoming[key] for key in
                                          ('sessions', 'workspaces', 'removed', 'removedProjects'))
                    if empty_delta:
                        count(self.service, 'catalog.scan.empty')
                    skipped = False
                    if empty_delta and not full_merge and not availability_changed:
                        async with self.service.lock:
                            if self.service.closed:
                                return
                            # A local commit can win while canonical probing runs
                            # off-thread. Never acknowledge its epoch without the
                            # existing full overlay/ownership reconciliation.
                            if (self._local_revision == self.service._history_local_revision
                                    and self._catalog_facts_unchanged(incoming)):
                                self.last_scan = snapshot
                                self._native_revision = revision_token
                                skipped = True
                        if skipped:
                            count(self.service, 'catalog.merge.skipped')
                            return True
                # Reconcile local changes even when native files are unchanged:
                # hidden rows, tombstones, managed markers and selected views
                # have independent invalidation. Filtering must not alter the
                # retained unfiltered catalog.
                managed_paths = await asyncio.to_thread(catalog_locations, snapshot)
                naming_proofs = {}
                if directory_mode:
                    # A reset can invalidate changed-row journals. Recheck only the
                    # admitted snapshot plus bounded changed overlays, not the catalog.
                    naming_rows = {(row['nativeProject'], row['nativeIdentity']): row
                                   for row in [*incoming['sessions'], *snapshot['sessions']]}
                    pending_names = self.service.directory.naming_conflicts(naming_rows.values(), self.service._state)
                    if pending_names:
                        naming_proofs = await asyncio.to_thread(self.service.directory.verify_naming_conflicts, pending_names)
                if self.service.closed:
                    return
                async with self.service.lock:
                    state = self.service.state
                    touched = set()
                    resident_before = {}
                    locations_changed = False
                    if directory_mode:
                        locations_changed = self.service.directory.sync_locations(managed_paths,
                            [row.get('path') for row in snapshot['workspaces']] + [row.get('workspace') for row in snapshot['sessions']])
                        self.service.directory.refresh_native(incoming['sessions'], incoming['removed'], self.service._state, naming_proofs=naming_proofs)
                    if delta_mode and self._local_revision != self.service._history_local_revision and not full_merge:
                        # Local actions can commit during the detached marker
                        # read above. Do not acknowledge an epoch we did not merge.
                        snapshot = dict(self._native_snapshot)
                        full_merge = True
                        self._existing = None
                        managed_paths = await asyncio.to_thread(catalog_locations, snapshot)
                    count(self.service, 'catalog.merge.full' if not delta_mode or full_merge
                          else 'catalog.merge.delta')
                    from .managed_deletion import CatalogSuppression
                    deleted = CatalogSuppression(self.service.db)
                    snapshot['workspaces'] = [row for row in snapshot['workspaces'] if row.get('nativeProject') not in deleted.projects]
                    snapshot['sessions'] = [row for row in snapshot['sessions'] if not deleted.contains(row)]
                    count(self.service, 'catalog.merge.rows', len(snapshot['sessions']))
                    changed = bool(locations_changed or state.get('sharedHistory', {}).get('loading') or state.get('sharedHistory', {}).get('error')
                                   or directory_mode and (incoming['reset'] or incoming['sessions'] or incoming['removed'] or incoming['removedProjects']))
                    globals_changed = ({'sharedHistory'} if state.get('sharedHistory', {}).get('loading')
                                       or state.get('sharedHistory', {}).get('error') else set())
                    resident_detail_only = (delta_mode and not full_merge
                        and not incoming['workspaces'] and not incoming['removed']
                        and not incoming['removedProjects']
                        and bool(snapshot['sessions'])
                        and (all(
                            (row['nativeProject'], row['nativeIdentity']) in admitted
                            for row in incoming['sessions']) if directory_mode else
                            all(row.get('sessionKind') == 'worker' for row in incoming['sessions']))
                        and not locations_changed)
                    hidden_workspaces = set(state.get('hiddenNativeWorkspaces', []))
                    workspaces = {row['id']: row for row in state['workspaces']}
                    # Most refreshes contain no unresolved folders. Index the
                    # exceptions once instead of rescanning every registration
                    # for every discovered workspace while holding the app lock.
                    unresolved_by_project = {}
                    for item in state['workspaces']:
                        if not item.get('path'):
                            unresolved_by_project.setdefault(item.get('nativeProject'), {})[item['id']] = item
                    for row in known:
                        previous = workspaces.get(row['id'])
                        if previous and previous.get('path') == row.get('path') and previous.get('available') != row['available']:
                            previous['available'] = row['available']
                            changed = True
                            globals_changed.add('workspaces')
                    for row in snapshot['workspaces']:
                        if row.get('path') in managed_paths:
                            continue
                        unresolved_id = (uuid.uuid5(uuid.NAMESPACE_URL, f"amplifier-project:{row['nativeProject']}").hex
                                         if hidden_workspaces and row.get('path') else None)
                        if unresolved_id is not None and unresolved_id in hidden_workspaces and row['id'] not in hidden_workspaces:
                            hidden_workspaces.add(row['id'])
                            state.setdefault('hiddenNativeWorkspaces', []).append(row['id'])
                            changed = True
                            globals_changed.add('hiddenNativeWorkspaces')
                        if row['id'] in hidden_workspaces:
                            continue
                        previous = workspaces.get(row['id'])
                        previous_project = previous.get('nativeProject') if previous else None
                        unresolved = [item for identity, item in unresolved_by_project.get(row['nativeProject'], {}).items()
                                      if identity != row['id']]
                        if previous is None:
                            state['workspaces'].append(dict(row)); workspaces[row['id']] = state['workspaces'][-1]
                            previous = workspaces[row['id']]
                            changed = True
                            globals_changed.add('workspaces')
                        else:
                            for key in ('nativeProject', 'available', 'sessionCount', 'workerSessionCount'):
                                if key in row and previous.get(key) != row[key]:
                                    previous[key] = row[key]; changed = True
                                    globals_changed.add('workspaces')
                        if not previous.get('path'):
                            # Later rows in this same scan must see additions
                            # and identity changes, just as a fresh list scan did.
                            project = previous.get('nativeProject')
                            if previous_project != project:
                                unresolved_by_project.get(previous_project, {}).pop(previous['id'], None)
                            unresolved_by_project.setdefault(project, {})[previous['id']] = previous
                        for old in unresolved:
                            # Migrate the generated placeholder, preserving any
                            # user customization and every reference to it.
                            if old.get('name') != old.get('nativeProject'):
                                if previous.get('name') == row.get('name'):
                                    previous['name'] = old['name']
                                elif old.get('name') != previous.get('name'):
                                    aliases = previous.setdefault('aliases', [])
                                    if old['name'] not in aliases:
                                        aliases.append(old['name'])
                            for key, value in old.items():
                                if key not in {'id', 'name', 'nativeProject', 'path', 'available', 'sessionCount', 'workerSessionCount'}:
                                    previous.setdefault(key, copy.deepcopy(value))
                            if state.get('selectedWorkspaceId') == old['id']:
                                state['selectedWorkspaceId'] = row['id']
                                globals_changed.add('selectedWorkspaceId')
                                if row.get('path'):
                                    state['settings']['workspace'] = row['path']
                                    globals_changed.add('settings')
                            pins = state.get('pinnedWorkspaceIds', [])
                            if isinstance(pins, list) and old['id'] in pins:
                                # Replace identity in place; availability and
                                # unrelated stored values are not migration policy.
                                remapped, seen = [], set()
                                for value in pins:
                                    value = row['id'] if value == old['id'] else value
                                    if not isinstance(value, str) or value not in seen:
                                        remapped.append(value)
                                    if isinstance(value, str):
                                        seen.add(value)
                                if remapped != pins:
                                    state['pinnedWorkspaceIds'] = remapped
                                    globals_changed.add('pinnedWorkspaceIds')
                                    if isinstance(state.get('pinnedItemIds'), list):
                                        items = list(dict.fromkeys(
                                            'workspace:' + row['id'] if key == 'workspace:' + old['id'] else key
                                            for key in state['pinnedItemIds']))
                                        if items != state['pinnedItemIds']:
                                            state['pinnedItemIds'] = items
                                            globals_changed.add('pinnedItemIds')
                            for scoped in state['sessions']:
                                if scoped.get('workspaceId') == old['id']:
                                    scoped['workspaceId'] = row['id']
                                    touched.add(scoped['id'])
                            canvas_remaps = [(key, scoped) for key, values in (
                                ('canvasArtifacts', state.get('canvasArtifacts', [])),
                                ('canvas', [state.get('canvas', {})])) for scoped in values
                                if scoped.get('workspaceId') == old['id']]
                            for key, scoped in canvas_remaps:
                                scoped['workspaceId'] = row['id']
                                globals_changed.add(key)
                            state['workspaces'].remove(old)
                            workspaces.pop(old['id'], None)
                            unresolved_by_project.get(old.get('nativeProject'), {}).pop(old['id'], None)
                            changed = True
                            globals_changed.add('workspaces')
                    if not delta_mode or self._existing is None:
                        existing = {(s.get('nativeProject') or (project_slug(s['workspace']) if s.get('workspace') else None),
                                     s.get('nativeIdentity') or s.get('runtimeSessionId') or s['id']): s for s in state['sessions']}
                        if delta_mode:
                            self._existing = existing
                            self._public_ids = {row['id'] for row in state['sessions']}
                    else:
                        existing = self._existing
                    if delta_mode:
                        # Retain customized overlays, drafts and selections for
                        # removed native records, but make their unavailability
                        # explicit. A removal never authorizes deleting history.
                        for key in incoming['removed']:
                            previous = existing.get(tuple(key))
                            if previous and previous.get('historyManaged'):
                                previous.update(nativeAvailable=False,
                                    historyReadOnlyReason='The canonical saved chat is no longer available. Restore its files before continuing.')
                                touched.add(previous['id'])
                                changed = True
                    from collections import Counter
                    native_counts = self._catalog_counts if delta_mode else Counter(row['nativeIdentity'] for row in snapshot['sessions'])
                    catalog_ids = self._public_ids if delta_mode else {row['id'] for row in state['sessions']}
                    hidden = set(state.get('hiddenNativeSessions', []))
                    for row in snapshot['sessions']:
                        managed = row.get('workspace') in managed_paths
                        if managed:
                            row = {**row, 'workspaceId': None}
                        key = (row['nativeProject'], row['nativeIdentity'])
                        if (hidden and identity(*key) in hidden) or row['workspaceId'] in hidden_workspaces:
                            continue
                        previous = existing.get(key)
                        if previous is None and directory_mode:
                            continue  # selection/action admission owns the working set
                        if previous is not None:
                            resident_before[previous['id']] = resident_catalog_facts(previous)
                        if previous is None:
                            public_id = row['nativeIdentity']
                            if native_counts[public_id] > 1 or public_id in catalog_ids:
                                public_id = row['id']  # Duplicate native IDs require project scope.
                            previous = {'id': public_id, 'title': row.get('name') or row.get('title') or 'Conversation ' + row['nativeIdentity'][:8],
                                        'titleSource': 'native', 'nativeNameSource': row.get('nameSource'), 'autoName': row.get('autoName', row.get('nameSource') != 'manual'), 'bundle': row.get('bundle') or state['settings']['bundle'],
                                        'workspace': row.get('workspace'), 'workspaceId': row['workspaceId'],
                                        'workspaceAvailable': managed_paths[row['workspace']] if managed else workspaces.get(row['workspaceId'], {}).get('available', False),
                                        'createdAt': row.get('createdAt', 0), 'updatedAt': row.get('updatedAt', 0), 'recentActivityAt': row.get('recentActivityAt', 0),
                                        'status': 'idle', 'messages': [], 'workers': [], 'approvals': [],
                                        'runtimeSessionId': row['nativeIdentity'], 'nativeIdentity': row['nativeIdentity'],
                                        'nativeProject': row['nativeProject'], 'nativeRevision': copy.deepcopy(row.get('transcriptRevision')),
                                        'parentId': row.get('parentId'), 'nativeParentId': row.get('parentId'), 'turnCount': row.get('turnCount'),
                                        'sessionKind': row['sessionKind'],
                                        **({'sessionPurpose': row['sessionPurpose']} if row.get('sessionPurpose') else {}),
                                        'description': row.get('description', ''), 'shared': True,
                                        'historyReadOnlyReason': row.get('readOnlyReason'),
                                        'historyManaged': True, 'historyLoaded': False}
                            if hasattr(self.service, 'cold_display'):
                                previous = self.service.cold_display.record(previous)
                            state['sessions'].append(previous); existing[key] = previous; catalog_ids.add(public_id); changed = True
                            touched.add(public_id)
                        else:
                            from .chat_navigation import recent_activity, navigation_activity
                            previous.setdefault('navigationActivityAt', navigation_activity(previous))
                            recent = max(recent_activity(previous), row.get('recentActivityAt', 0))
                            if previous.get('recentActivityAt') != recent:
                                previous['recentActivityAt'] = recent; changed = True
                                if previous.get('historyManaged') and previous.get('status') not in BUSY:
                                    previous['navigationActivityAt'] = recent
                            confirmed_name = (previous.get('nativeNameRevision', 0), previous.get('nativeNamePolicyRevision', 0), previous.get('title')) in naming_proofs.get((row['nativeProject'], row['nativeIdentity']), ())
                            name_current = confirmed_name or row.get('nameRevision', 0) >= previous.get('nativeNameRevision', 0)
                            policy_current = confirmed_name or name_current and row.get('namePolicyRevision', 0) >= previous.get('nativeNamePolicyRevision', 0)
                            for key_name, value in {'nativeProject': row['nativeProject'], 'nativeIdentity': row['nativeIdentity'],
                                                    **({'nativeNameSource': row.get('nameSource'), 'nativeNameRevision': row.get('nameRevision', 0)} if name_current else {}),
                                                    **({'autoName': row.get('autoName', row.get('nameSource') != 'manual'), 'nativeNamePolicyRevision': row.get('namePolicyRevision', 0)} if policy_current else {}),
                                                    'workspaceId': row['workspaceId'],
                                                    # An app-declared internal job (e.g. terminal
                                                    # tool calls) is never promoted to a chat.
                                                    **({} if previous.get('sessionKind') == 'internal' and not previous.get('historyManaged')
                                                       else {'sessionKind': row['sessionKind'], 'sessionPurpose': row.get('sessionPurpose')}),
                                                    'workspaceAvailable': managed_paths[row['workspace']] if managed else workspaces.get(row['workspaceId'], {}).get('available', False)}.items():
                                if previous.get(key_name) != value:
                                    previous[key_name] = value; changed = True
                            if name_current and (row.get('name') or (previous.get('historyManaged') and previous.get('titleSource') != 'manual')):
                                title = row.get('name') or row.get('title') or previous['title']
                                if title != previous['title']:
                                    previous['title'] = title; changed = True
                                # Native catalog names are not local UI overrides.
                                # Keeping this provenance avoids persisting the
                                # entire discovered library after each refresh.
                                source = ('manual' if previous.get('titleSource') == 'manual' else 'native') if previous.get('historyManaged') else row.get('nameSource') or 'manual'
                                if row.get('name') and previous.get('titleSource') != source:
                                    previous['titleSource'] = source; changed = True
                            if previous.get('historyManaged') and previous.get('historyReadOnlyReason') != row.get('readOnlyReason'):
                                previous['historyReadOnlyReason'] = row.get('readOnlyReason'); changed = True
                            if previous.get('historyManaged'):
                                metadata = {'workspace': row.get('workspace'),
                                            **({'description': row.get('description', ''), 'nativeNameSource': row.get('nameSource')} if name_current else {}),
                                            'createdAt': row.get('createdAt', 0), 'updatedAt': row.get('updatedAt', 0),
                                            'turnCount': row.get('turnCount'), 'nativeParentId': row.get('parentId')}
                                if not row.get('parentId'):
                                    metadata['parentId'] = None
                                if row.get('bundle'):
                                    metadata['bundle'] = row['bundle']
                                for key_name, value in metadata.items():
                                    if previous.get(key_name) != value:
                                        previous[key_name] = value; changed = True
                            if (not previous.get('historyLoaded') and previous.get('historyManaged')) or 'nativeRevision' not in previous:
                                native_revision = row.get('transcriptRevision')
                                # Keep an already detached, equal revision. Compare
                                # values on every refresh so in-place edits are seen.
                                if ('nativeRevision' not in previous or previous['nativeRevision'] != native_revision
                                        or (isinstance(native_revision, (dict, list)) and previous['nativeRevision'] is native_revision)):
                                    previous['nativeRevision'] = copy.deepcopy(native_revision)
                        if managed and previous.get('location') != {'kind': 'managed'}:
                            previous['location'] = {'kind': 'managed'}; changed = True
                        previous['_catalogRecentAt'] = row.get('recentActivityAt', 0)
                        if previous.get('nativeAvailable') is False:
                            previous['nativeAvailable'] = True
                            changed = True
                        previous['_catalogId'] = row['nativeIdentity'] if native_counts[row['nativeIdentity']] == 1 else row['id']
                    # Parent identities belong to their native project. UI IDs
                    # are aliases and can differ even when a web root predated
                    # automatic discovery or another project reused the ID.
                    for row in snapshot['sessions']:
                        session = existing.get((row['nativeProject'], row['nativeIdentity']))
                        if session is None or not row.get('parentId'):
                            continue
                        if session['id'] not in resident_before:
                            resident_before[session['id']] = resident_catalog_facts(session)
                        parent = existing.get((row['nativeProject'], row['parentId']))
                        if parent is None and directory_mode:
                            parent = self.service.directory.resolve(row['parentId'], row['nativeProject'])
                        parent_id = parent['id'] if parent else row['parentId']
                        for key_name, value in {'parentId': parent_id, 'nativeParentId': row['parentId']}.items():
                            if session.get(key_name) != value:
                                session[key_name] = value; changed = True
                    touched.update(session['id'] for session in existing.values()
                                   if session['id'] in resident_before and
                                   resident_catalog_facts(session) != resident_before[session['id']])
                    issues = snapshot.get('issues', [])
                    if (state['sharedHistory'].get('issues', []) != issues[:100]
                            or state['sharedHistory'].get('issueCount', 0) != len(issues)):
                        changed = True
                        globals_changed.add('sharedHistory')
                    counts = (self._public_catalog_counts(incoming, deleted)
                              if delta_mode else {
                                  'projectCount': len(snapshot['workspaces']),
                                  'sessionCount': sum(row['sessionKind'] == 'root' for row in snapshot['sessions']),
                                  'workerSessionCount': sum(row['sessionKind'] == 'worker' for row in snapshot['sessions']),
                                  'internalSessionCount': sum(row['sessionKind'] == 'internal' for row in snapshot['sessions'])})
                    if delta_mode and any(state['sharedHistory'].get(key) != value for key, value in counts.items()):
                        changed = True
                        globals_changed.add('sharedHistory')
                    state['sharedHistory'].update(loading=False, issues=copy.deepcopy(issues[:100]),
                        issueCount=len(issues), error=None, **counts)
                    if delta_mode:
                        # Slice counters are diagnostic work evidence, not a
                        # reason to save/project every client hundreds of times.
                        # Public freshness changes on phase/completion only.
                        reconciliation = {key: incoming.get('reconciliation', {}).get(key)
                                          for key in ('phase', 'completedAt')}
                        if state['sharedHistory'].get('reconciliation') != reconciliation:
                            changed = True
                            globals_changed.add('sharedHistory')
                        state['sharedHistory']['reconciliation'] = reconciliation
                    self.last_scan = snapshot
                    if changed:
                        if delta_mode:
                            # A full metadata reconciliation still tracks every
                            # changed owner and global above; it does not require
                            # checkpointing unrelated conversation histories.
                            # Pending progress joins this scope in the service.
                            self.service._publish_changes(sessions=touched, globals=globals_changed,
                                                          detail_only=resident_detail_only and not globals_changed)
                        else:
                            self.service._publish_full(reason='Native catalog membership and ownership reconciliation')
                    if delta_mode:
                        # Advance only after the durable publication succeeded.
                        self._native_revision = revision_token
                        self._local_revision = self.service._history_local_revision
                return True
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._refresh_error()

    async def _refresh_error(self):
        if not self.service.closed:
            async with self.service.lock:
                self.service.state['sharedHistory'].update(loading=False, error='Could not refresh shared chat history. Existing chats are kept; try Refresh.')
                try:
                    self.service._publish_changes(globals={'sharedHistory'})
                except (OSError, ValueError, sqlite3.Error):
                    # The error report shares the failed persistence boundary.
                    # Keep the error and dirty union for retry; a second disk
                    # error must not kill discovery.
                    pass

    def _public_catalog_counts(self, incoming, deleted):
        counts = {key: incoming[key] for key in
                  ('projectCount', 'sessionCount', 'workerSessionCount', 'internalSessionCount')}
        if deleted.native and incoming.get('directoryReady') is False:
            # The scanner reports its existing unavailable-cache issue/freshness.
            # Raw totals cannot prove which reviewed rows are still indexed;
            # retain last public counts until that same catalog owner recovers.
            shared = self.service.state.get('sharedHistory', {})
            return {key: shared.get(key, 0) for key in counts}
        # Retained tombstones can make public totals differ from native totals.
        # Recheck their compact receipts even when the scanner returns no rows.
        for project in deleted.projects:
            summary = self._catalog_workspaces.get(project)
            if summary is not None:
                counts['projectCount'] -= 1
                for key in ('sessionCount', 'workerSessionCount', 'internalSessionCount'):
                    counts[key] -= summary.get(key, 0)
        for project, identities in deleted.native.items():
            if project in deleted.projects:
                continue
            # Cleanup can be pending or discovery can lag the confirmed delete.
            # Count only still-indexed reviewed rows, without loading bodies.
            for kind, amount in self.index._catalog.count_identities(project, identities).items():
                counts[{'root': 'sessionCount', 'worker': 'workerSessionCount',
                        'internal': 'internalSessionCount'}[kind]] -= amount
        return counts

    def _catalog_facts_unchanged(self, incoming):
        from .managed_deletion import CatalogSuppression
        shared = self.service.state.get('sharedHistory', {})
        if shared.get('loading') or shared.get('error'):
            return False
        deleted = CatalogSuppression(self.service.db)
        counts = self._public_catalog_counts(incoming, deleted)
        issues = incoming.get('issues', [])
        reconciliation = {key: incoming.get('reconciliation', {}).get(key)
                          for key in ('phase', 'completedAt')}
        return (all(shared.get(key) == value for key, value in counts.items())
                and shared.get('issues', []) == issues[:100]
                and shared.get('issueCount', 0) == len(issues)
                and shared.get('reconciliation') == reconciliation)

    async def _refresh_selected_history(self):
        # Browsers have independent selections. Refresh only connected
        # views, not every historical client record retained on disk.
        from .history_demand import subscribed_sessions
        selected_ids = subscribed_sessions(self.service)
        by_id = (self.service.projections.sessions(self.service.state).by_id
                 if hasattr(self.service, 'projections') else
                 {row['id']: row for row in self.service.state['sessions']})
        for selected in [by_id[key] for key in selected_ids if key in by_id]:
            if self.windows.observed(selected['id']):
                await self.load(selected['id'], only_if_changed=True, display=True)
                continue
            if not selected.get('nativeProject') or selected.get('status') in BUSY or selected.get('configurationBusy'):
                continue
            current = await asyncio.to_thread(revision, selected)
            if not selected.get('historyLoaded', True) or current != selected.get('nativeRevision'):
                # Selection/reconnect may already own the reader. Recheck the
                # canonical stamp after its load lock, so a queued catalog poll
                # consumes that result while still observing a later append.
                await self.load(selected['id'], only_if_changed=True,
                                display=self.windows.observed(selected['id']))

    async def refresh_session(self, session_id):
        await self.load(session_id, only_if_changed=True)

    @measured('history.load', session=True)
    async def load(self, session_id, *, before=None, limit=100, only_if_changed=False, display=False,
                   canonical=False, publish_loading=True):
        paging = before is not None
        async with self.loads.setdefault(session_id, TimedLock(self.service, 'history.lock.wait')):
            if not canonical and not paging and (display or self.windows.bound_web()):
                await self.windows.load(session_id, only_if_changed=only_if_changed)
                return
            if getattr(self.service, 'directory', None) is not None:
                from .errors import AppError
                async with self.service.lock:
                    try:
                        session_id = self.service._session(session_id, hydrate=False)['id']
                    except AppError as exc:
                        if exc.status == 404:
                            return
                        raise
            if only_if_changed:
                candidate = self.service.projections.sessions(self.service.state).by_id.get(session_id)
                if candidate is None:
                    return
                if (candidate.get('historyLoaded', True) and not candidate.get('historyError')
                        and not candidate.get('historyLoading')):
                    # Checking the disk stamp needs identity only. Do not load
                    # cold display bodies or copy an entire execution history.
                    source = {key: candidate.get(key) for key in
                              ('id', 'nativeProject', 'nativeIdentity', 'runtimeSessionId')}
                    stamp = await asyncio.to_thread(revision, source)
                    if stamp == candidate.get('nativeRevision'):
                        return
            async with self.service.lock:
                session = next((s for s in self.service.state['sessions'] if s['id'] == session_id), None)
                if not session or not session.get('nativeProject') or (not paging and session.get('status') in BUSY) or session.get('configurationBusy'):
                    return
                session.update(historyLoading=True, historyError=None)
                # The file reader needs identity and paging anchors, not the
                # execution graph, configuration, or retained tool bodies.
                # Copy only its inputs before crossing the worker boundary.
                source = {key: copy.deepcopy(session[key]) for key in (
                    'id', 'nativeProject', 'nativeIdentity', 'runtimeSessionId',
                    'createdAt', 'historyManaged', 'nativeRevision',
                    'sharedHistoryOffset', 'sharedHistoryUserTurnOffset') if key in session}
                if paging:
                    source['messages'] = [{key: message[key] for key in
                        ('nativeIndex', 'nativeMessageId', 'role', 'text') if key in message}
                        for message in session.get('messages', [])]
                window = (max(limit, len(session.get('messages', [])))
                          if session.get('historyManaged') else None) if not paging else limit
                if publish_loading:
                    self.service._publish_changes(sessions={session_id}, detail_only=True)
            try:
                with measure(self.service, 'history.reader.wait', session_id=session_id):
                    result = await asyncio.to_thread(read_transcript, source, before=before, limit=window, diagnostics=getattr(self.service, 'performance_diagnostics', None))
                async with self.service.lock:
                    session = next((s for s in self.service.state['sessions'] if s['id'] == session_id), None)
                    if session is None:
                        return
                    if (paging or session.get('status') not in BUSY) and not session.get('configurationBusy'):
                        if paging:
                            # Only prepend. Current live messages, streaming
                            # text, execution ownership and draft stay intact.
                            known = {m['id'] for m in session['messages']}
                            known_indexes = {m['nativeIndex'] for m in session['messages'] if 'nativeIndex' in m}
                            session['messages'] = [m for m in result['messages']
                                                   if m['id'] not in known and m['nativeIndex'] not in known_indexes] + session['messages']
                        else:
                            remove_internal_copies(session, result['hiddenMessages'])
                            if session.get('historyManaged'):
                                # Keep the loaded window when another host appends.
                                session['messages'] = result['messages']
                            else:
                                # Preserve web-owned message IDs, execution anchors,
                                # voice-only bubbles, attachments and draft state.
                                merge_web_history(session, result['messages'])
                            session['nativeRevision'] = result['revision']
                        from .native_activity import apply_activity
                        apply_activity(session, result['activity'], append=before is not None)
                        offset = source.get('sharedHistoryOffset', 0)
                        user_offset = source.get('sharedHistoryUserTurnOffset', 0)
                        if (not paging and session.get('historyManaged')) or (paging and result['offset'] <= offset):
                            offset, user_offset = result['offset'], result['userOffset']
                        # A page does not refresh the tail. Keep its old stamp
                        # so a later refresh still discovers any new appends.
                        session.update(historyLoaded=True,
                            sharedHistoryOffset=offset, sharedHistoryUserTurnOffset=user_offset,
                            sharedHistoryTotal=result['total'])
                    session['historyLoading'] = False
                    self.service._publish_changes(sessions={session_id}, detail_only=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self.service.lock:
                    session = next((s for s in self.service.state['sessions'] if s['id'] == session_id), None)
                    if session:
                        session.update(historyLoading=False, historyError='Could not read the saved chat. Its original files are unchanged. Try Refresh.')
                        self.service._publish_changes(sessions={session_id}, detail_only=True)

    async def ensure_loaded(self, session_id, *, hydrate=True):
        # Passive append owners still verify the canonical native revision, but
        # may resolve individual cold fields when used instead of loading every
        # saved execution/configuration/catalog payload up front.
        session = self.service._session(session_id, hydrate=hydrate)
        if session.get('nativeProject'):
            if session.get('historyReadOnlyReason'):
                # A retained negative display classification can refuse work;
                # it cannot admit it. Keep the specific historical explanation,
                # then require fresh canonical evidence for every positive case.
                raise ValueError(session['historyReadOnlyReason'])
            if session.get('historyManaged'):
                # Admission verifies native identity and workspace, not the
                # browser's retained messages or potentially huge activity tree.
                source = {key: session[key] for key in
                          ('id', 'nativeProject', 'nativeIdentity', 'workspace')
                          if key in session}
                await asyncio.to_thread(self.index.validate_resume, source)
            if session.get('workspaceAvailable') is False or not session.get('workspace'):
                raise ValueError('This project folder is unavailable. Its saved chats can be read, but the folder must be restored before continuing work.')
            source = {key: session[key] for key in
                      ('id', 'nativeProject', 'nativeIdentity', 'runtimeSessionId')
                      if key in session}
            current_revision = await asyncio.to_thread(revision, source)
            if not session.get('historyLoaded', True) or session.get('historyLoading') or current_revision != session.get('nativeRevision'):
                # Admission needs the completed read, not a durable intermediate
                # loading notice that serializes the same retained execution twice.
                # Keep the in-memory loading fence and publish success/errors below.
                await self.load(session_id, only_if_changed=True, canonical=True, publish_loading=False)
                session = self.service._session(session_id, hydrate=hydrate)
            if session.get('historyError'):
                raise ValueError(session['historyError'])
