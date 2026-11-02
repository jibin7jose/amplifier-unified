"""Scope reviewed deletion to native session records, never project working files."""
import hashlib
import json
from pathlib import Path

from .managed_deletion import _safe, shared_identity
from .session_files import amplifier_home, project_slug, validate_id, capture_sessions_dir


def scope(app, selection):
    project = selection.get('project', '')
    if not project or project in {'.', '..'} or '/' in project or '\\' in project:
        raise ValueError('Choose a project from shared session history.')
    directory = _safe(amplifier_home() / 'projects' / project / 'sessions')
    if not directory.is_dir():
        raise ValueError('This project has no saved sessions.')
    records = {}
    for path in directory.iterdir():
        if path.name.startswith('.'):
            continue  # stable locks are never deletion targets
        validate_id(path.name)
        _safe(path)
        if not path.is_dir():
            raise ValueError('Unexpected files in the session directory. Its data was preserved.')
        try:
            metadata = _safe(path / 'metadata.json')
            if not metadata.is_file() or metadata.stat().st_size > 1024 * 1024:
                raise ValueError('Oversized session metadata')
            meta = json.loads(metadata.read_text())
            if not isinstance(meta, dict):
                raise ValueError('Invalid session metadata')
        except (OSError, ValueError):
            raise ValueError('Session metadata could not be verified. Its data was preserved.') from None
        workspace = meta.get('working_dir') or meta.get('workspace')
        if not isinstance(workspace, str) or not Path(workspace).is_absolute() or project_slug(workspace) != project:
            raise ValueError('The session project could not be verified. Its data was preserved.')
        records[path.name] = meta
    chosen = selection.get('nativeId')
    if chosen:
        validate_id(chosen)
        if chosen not in records:
            raise ValueError('This saved session is no longer available.')
        if records[chosen].get('parent_id'):
            raise ValueError('Review the top-level conversation instead of deleting worker history separately. Its files were preserved.')
        native = {chosen}
        while True:
            children = {sid for sid, meta in records.items() if meta.get('parent_id') in native}
            if children <= native:
                break
            native |= children
    else:
        native = set(records)
    if not native:
        raise ValueError('This project has no saved sessions.')
    # This path has not qualified native-worker ancestor execution exclusion.
    # Do not reinterpret a metadata lock as that missing ownership authority.
    if any(not shared_identity(sid) for sid in native):
        raise ValueError('This history contains native worker identities that cannot be reviewed here. Delete their owning managed chat, or keep this history.')
    for sid in native:
        seen = {sid}
        parent = records[sid].get('parent_id')
        while parent:
            if parent not in native or parent in seen:
                raise ValueError('Worker ancestry could not be verified. Its files were preserved.')
            seen.add(parent)
            parent = records[parent].get('parent_id')
    workspaces = {records[sid].get('working_dir') or records[sid].get('workspace') for sid in native}
    if len(workspaces) != 1:
        raise ValueError('Several project paths share this history name. Review individual sessions instead.')
    workspace = workspaces.pop()
    owner = getattr(app, 'directory', None)
    sessions = ([app._admit_session(row) for row in owner.select(
        '(id IN (SELECT value FROM json_each(?)) OR native_id IN (SELECT value FROM json_each(?)))',
        (json.dumps(sorted(native)), json.dumps(sorted(native))), limit=-1)] if owner is not None else
        [row for row in app.state['sessions'] if {row['id'], row.get('nativeIdentity'), row.get('runtimeSessionId')} & native])
    if any(row.get('workspace') != workspace for row in sessions):
        raise ValueError('Another project references these sessions. Its data was preserved.')
    ids = native | {row['id'] for row in sessions}
    children = (owner.select('parent_id IN (SELECT value FROM json_each(?))', (json.dumps(sorted(ids)),), limit=-1)
                if owner is not None else app.state['sessions'])
    if any(row.get('parentId') in ids and row['id'] not in ids for row in children):
        raise ValueError('A worker uses separate history. Review it before removing this session.')
    from amplifier_foundation.session.shared_state import SharedSessionStore
    paths = [directory / sid for sid in sorted(native)]
    for sid in sorted(native):
        store = SharedSessionStore.from_identity({'version':1, 'workspace':workspace, 'sessionId':sid})
        _safe(store.checkpoint_path)
        if store.checkpoint_path.exists():
            store.read()
            paths.append(store.checkpoint_path)
        capture = capture_sessions_dir(workspace) / sid
        if capture != directory / sid:
            paths.append(_safe(capture))
    paths.extend(app.data_dir / 'sessions' / validate_id(sid) for sid in sorted(ids))
    identity = 'history-' + hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()[:32]
    title = (records[chosen].get('title') or chosen) if chosen else f'All sessions for {workspace}'
    if not isinstance(title, str):
        title = chosen or workspace
    marker = {'id': identity, 'workspace': workspace}
    from .native_history import _timestamp
    activity = _timestamp(records[chosen].get('last_event_at')) if chosen else None
    return {'id': identity, 'title': title, 'reviewActivityAt': activity}, marker, sessions, ids, paths, project, native
