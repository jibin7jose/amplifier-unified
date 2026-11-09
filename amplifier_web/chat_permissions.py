"""Chat write overrides in Foundation's canonical native-session settings."""
import copy
from pathlib import Path
from types import SimpleNamespace

from .configuration_authority import fingerprint
from .session_identity import native_id
from .shared_settings import overlay, read_settings

WRITE_KEYS = ('allowed_write_paths', 'denied_write_paths')
PENDING_DETAIL = ('Write settings are saved or being saved. The live tool boundary has not confirmed the refresh. '
                  'Finish or stop existing child work if refresh remains unavailable; no new turn has started.')


class _Unchanged(Exception):
    """Abort Foundation's write while retaining its locked read/check."""


def _values(settings):
    return settings.get('overrides', {}).get('tool-filesystem', {}).get('config', {})


def _defaults(settings, session):
    # Use the same policy owner as actual root, snapshot and runtime-plan mounts.
    # No bundle activation, module loading, worker start or settings write.
    from .host.session import _apply_host_policy
    bundle = SimpleNamespace(tools=[{'module': 'tool-filesystem'}], agents={})
    _apply_host_policy(bundle, SimpleNamespace(settings=settings, workspace=session['workspace']),
                       execution_workspace=session.get('workingDirectory'))
    policy = bundle.tools[0]['config']
    return {'allowed': policy.get(WRITE_KEYS[0], []), 'denied': policy.get(WRITE_KEYS[1], [])}


def target(management, args):
    if not args.get('sessionId'):
        raise ValueError('Choose an explicit chat for session file settings.')
    session = management.service._session(args['sessionId'])
    if session.get('sessionKind', 'root') != 'root':
        raise ValueError('Child file access inherits its parent boundaries. Choose the owning root chat.')
    from .session_files import project_slug
    if session.get('nativeProject', project_slug(session['workspace'])) != project_slug(session['workspace']):
        raise ValueError('The native settings workspace does not match this chat.')
    return session


def snapshot(management, session, *, current=None):
    store = management.settings
    inherited = read_settings(session['workspace'], shared_home=store.shared_home)
    if current is None:
        current = store.read(session['workspace'], 'session', session_id=native_id(session))
    values = _values(current)
    effective = overlay(inherited, current)
    # Bind the complete canonical configuration, not only the visible lists:
    # a module/declaration or inherited policy change can also change access.
    revision = fingerprint([session['workspace'], native_id(session), session.get('workingDirectory'),
                            inherited, current])
    defaults = _defaults(effective, session)
    return {'scope': 'session', 'sessionId': session['id'], 'workspace': session['workspace'],
            'revision': revision, 'overridden': [key for key in WRITE_KEYS if key in values],
            'allowed': values.get(WRITE_KEYS[0], defaults['allowed']),
            'denied': values.get(WRITE_KEYS[1], defaults['denied']),
            'current': defaults,
            'inherited': _defaults(inherited, session)}


def change(management, args, *, reset=False, before_write=None):
    session = target(management, args)
    if session.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
        raise ValueError('Continue this chat here before changing its file settings.')
    before = snapshot(management, session)
    if not args.get('expectedRevision'):
        raise ValueError('Read this chat’s file settings and provide expectedRevision before changing them.')
    if args['expectedRevision'] != before['revision']:
        raise ValueError('File settings or workspace defaults changed. Reload the preview before changing them.')
    identity = (session['id'], session['workspace'], native_id(session), session.get('workingDirectory'))
    def edit(value):
        current_session = target(management, args)
        if identity != (current_session['id'], current_session['workspace'],
                        native_id(current_session), current_session.get('workingDirectory')):
            raise ValueError('The chat’s settings location changed. Reload the preview.')
        current = snapshot(management, current_session, current=value)
        if current['revision'] != args['expectedRevision']:
            raise ValueError('File settings or workspace defaults changed. Reload the preview before changing them.')
        prior = copy.deepcopy(value)
        if reset:
            values = _values(value)
            for key in WRITE_KEYS:
                values.pop(key, None)
            # Prune only ancestors emptied by this reset, never unrelated values.
            if prior != value:
                parents = [('overrides',), ('overrides', 'tool-filesystem'),
                           ('overrides', 'tool-filesystem', 'config')]
                for path in reversed(parents):
                    parent = value
                    for key in path[:-1]:
                        parent = parent[key]
                    if parent.get(path[-1]) == {}:
                        parent.pop(path[-1])
        else:
            values = value.setdefault('overrides', {}).setdefault('tool-filesystem', {}).setdefault('config', {})
            # Relative entries follow the chat's execution directory, never the
            # app server cwd. The mounted host policy retains its workspace base.
            root = Path(session.get('workingDirectory') or session['workspace'])
            for key, field in zip(WRITE_KEYS, ('allowed', 'denied')):
                values[key] = list(dict.fromkeys(str((p if p.is_absolute() else root / p).resolve())
                    for item in args[field] for p in (Path(item).expanduser(),)))
        if prior == value:
            raise _Unchanged()
        if before_write:
            before_write()
    try:
        management.settings.update(session['workspace'], 'session', edit, session_id=native_id(session))
        changed = True
    except _Unchanged:
        changed = False
    result = snapshot(management, session)
    result.update(changed=changed, detail=(
        'Workspace defaults restored for this chat.' if reset and changed else
        'This chat already inherits workspace defaults.' if reset else
        'Chat write overrides saved.' if changed else 'Chat write overrides are unchanged.'))
    return result


async def refresh_active(management, session, saved_revision, execution_epoch):
    """Release only this write-change fence after the same worker confirms it."""
    service = management.service
    refresh = getattr(service.runtime, 'refresh_file_access', None)
    if not callable(refresh):
        return
    async with service.lock:
        if (not session.get('fileAccessPending') or session.get('configurationBusy')
                or session.get('configurationPendingRevision', 0) != execution_epoch
                or snapshot(management, session)['revision'] != saved_revision):
            return
        revision = session.get('configurationPendingRevision', 0)
        settings = read_settings(session['workspace'], shared_home=management.settings.shared_home,
                                 session_id=native_id(session))
        settings_revision = fingerprint(settings)
    if not await refresh(session['id'], settings_revision):
        return
    async with service.lock:
        if (session.get('configurationBusy') or not session.get('fileAccessPending')
                or session.get('configurationPendingRevision', 0) != revision
                or snapshot(management, session)['revision'] != saved_revision):
            return
        session['configurationPending'] = False
        session.pop('fileAccessPending', None)
        session.pop('configurationRefresh', None)
        service._publish_changes(sessions={session['id']})
