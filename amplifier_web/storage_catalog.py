"""Content-first storage accounting. Scans are bounded and never follow links."""
from pathlib import Path
import hashlib
import os
import time

from .managed_deletion import _safe
from .session_files import amplifier_home, sessions_dir, validate_id

CATEGORIES = (
    ('workspaces', 'Workspace files', 'Registered projects under your workspace root and app-managed workspace files'),
    ('sessions', 'Session data', 'Chats, attachments, saved outputs and shared history'),
    ('runtime', 'App & runtime', 'Installed application, components and runtime environments'),
    ('backups', 'Backups', 'Private backups and recovery copies'),
    ('cache', 'Caches & logs', 'Downloaded release metadata, rebuildable caches and logs'),
    ('other', 'Settings & other data', 'Configuration, credentials and application records'),
)


def workspace_selection(home, workspaces, workspace_root):
    """A remembered chat cwd does not make its entire filesystem app storage.

    Do not enumerate the configured root: only registered strict descendants
    are candidates. Catalog separately accounts for app-owned leftovers.
    """
    normalize = lambda value: Path(os.path.abspath(os.path.expanduser(str(value))))
    roots = {normalize(workspace_root), normalize(home) / 'workspaces'}
    protected = {normalize(Path.home()), normalize(home), normalize(amplifier_home())}
    selected = []
    for row in workspaces:
        if not row.get('path'):
            continue
        path = normalize(row['path'])
        if path in roots or any(folder == path or folder.is_relative_to(path) for folder in protected):
            continue
        if any(path.is_relative_to(root) for root in roots):
            selected.append({**row, 'path': str(path)})
    return selected


def catalog(home, workspaces, sessions=(), installation=None):
    home = Path(home).absolute()
    roots = {}

    def add(path, category, label, **extra):
        path = Path(path).absolute()
        # Ownership rules are explicit, not inferred from a display name.
        roots[path] = {'id': hashlib.sha256(str(path).encode()).hexdigest()[:24],
                       'category': category, 'label': label, 'absolute': path, **extra}

    for row in workspaces:
        if row.get('path'):
            add(row['path'], 'workspaces', row.get('name') or Path(row['path']).name,
                workspaceId=row['id'], archived=bool(row.get('archived')))
    add(home, 'other', 'Other application data')
    # Exclude CLI configuration from any workspace that contains the CLI home.
    add(amplifier_home(), None, 'CLI configuration')
    add(amplifier_home() / 'projects', 'sessions', 'Shared CLI and Unified history', historyRoot=True)
    for name, category, label in (
        ('workspaces', 'workspaces', 'Other app-managed workspace files'),
        ('chats', 'sessions', 'Other managed chat files'),
        ('sessions', 'sessions', 'Conversation records'),
        ('artifacts', 'sessions', 'Saved outputs'),
        ('attachments', 'sessions', 'Attachments'),
        ('diagnostics', 'sessions', 'Recorded session events'),
        ('app.sqlite3', 'sessions', 'Conversation database'),
        ('app.sqlite3-wal', 'sessions', 'Recent database changes'),
        ('app.sqlite3-shm', 'sessions', 'Database coordination'),
        ('recall.sqlite3', 'sessions', 'Conversation recall index'),
        ('runtime', 'runtime', 'Worker environments'),
        ('foundation', 'runtime', 'Included components'),
        ('source-store', 'runtime', 'Component sources'),
        ('updates', 'runtime', 'Application updates'),
        ('backups', 'backups', 'Private backups'),
        ('reset-backups', 'backups', 'Reset recovery copies'),
        ('cache', 'cache', 'Application cache'),
        ('logs', 'cache', 'Application logs'),
        ('foundation/cache', 'cache', 'Component cache'),
        ('updates/releases', 'cache', 'Release metadata'),
    ):
        add(home / name, category, label)
    from .storage_backup_cleanup import candidates
    try:
        backups = candidates(home)
    except (OSError, ValueError):
        backups = []
    for index, path in enumerate(backups):
        from datetime import datetime
        stamp = datetime.fromtimestamp((path / 'private-state.tar.gz').stat().st_mtime).strftime('%b %d, %Y %H:%M')
        add(path, 'backups', 'Private backup · ' + stamp,
            backupId=path.name, protected=index == 0)
    from .reset_retention import recovery_members
    for member in recovery_members(home):
        add(member['path'], 'backups', 'Reset recovery · ' + member['role'],
            resetOperationId=member['operationId'], protected=True)
    if installation:
        add(installation, 'runtime', 'Installed application', protected=True)
    # Named managed chats can be reviewed through the existing deletion contract.
    # The category total also includes shared history, which stays read-only.
    for row in sessions:
        if row.get('location', {}).get('kind') != 'managed':
            continue
        try:
            sid = validate_id(row['id'])
            native = validate_id(row.get('runtimeSessionId') or sid)
            workspace = Path(row['workspace']).absolute()
            if workspace != home / 'chats' / sid / 'files':
                continue
        except (ValueError, KeyError, TypeError):
            continue
        for path in (workspace.parent, workspace, home / 'sessions' / native, sessions_dir(workspace) / native):
            add(path, 'sessions', row.get('title') or 'Untitled chat', sessionId=sid, locationLabel=('Chat files' if path == workspace else 'Chat metadata' if path == workspace.parent else 'Conversation records' if path.is_relative_to(home / 'sessions') else 'Native conversation history'))
    return roots


def measure(path, boundaries, seen, budget, *, deadline=None, stopped=None, cache=None, walks=None):
    """Advance a retained traversal, reusing completed subtree measurements."""
    from .storage_walk import Walk
    cache = cache if cache is not None else {}
    walks = walks if walks is not None else {}
    if path in cache:
        prior = walks.pop(path, None)
        if prior:
            prior.close()
        return dict(cache[path])
    def expired():
        return (deadline is not None and time.monotonic() >= deadline) or (stopped is not None and stopped.is_set())
    if path not in walks:
        if expired():
            return {'bytes': None, 'files': 0, 'incomplete': True, 'directory': True, 'pending': True}
        try:
            _safe(path)
        except ValueError:
            return {'bytes': None, 'files': 0, 'incomplete': True, 'directory': False,
                    'measurementNote': 'Symbolic link excluded'}
        walks[path] = Walk(path, boundaries, seen, cache)
    walk = walks[path]
    result = walk.advance(budget, expired)
    if walk.done:
        walks.pop(path)
    return result


def history_selection(root, relative, directory):
    if not root.get('historyRoot') or not directory:
        return {}
    parts = relative.parts
    if len(parts) == 1:
        return {'historySelection': {'project': parts[0]}}
    if len(parts) == 2 and parts[1] == 'sessions':
        return {'historySelection': {'project': parts[0]}}
    if len(parts) == 3 and parts[1] == 'sessions' and not parts[2].startswith('.'):
        return {'historySelection': {'project': parts[0], 'nativeId': parts[2]}}
    return {}


def summary(measured, pending=()):
    rows = []
    for identity, label, description in CATEGORIES:
        members = [r for r in measured if r['category'] == identity]
        waiting = identity in pending or any(r.get('pending') for r in members)
        rows.append({'id': identity, 'label': label, 'description': description,
                     'bytes': (sum(r['bytes'] or 0 for r in members) if any(r['bytes'] is not None for r in members) or not members and not waiting else None),
                     'incomplete': waiting or any(r['incomplete'] for r in members),
                     'pending': waiting, 'items': len(members)})
    return {'rows': sorted(rows, key=lambda r: r['bytes'] or 0, reverse=True),
            'bytes': sum(r['bytes'] or 0 for r in rows), 'incomplete': any(r['incomplete'] for r in rows),
            'measuring': any(r.get('pending') for r in rows),
            'description': 'File content on this host, counted once across categories. Shared CLI history is included; CLI configuration is excluded. Linked and unreadable files are not counted.'}


def inspect(home, workspaces, args, sessions=(), installation=None, limit=200000, *, seconds=2, stopped=None, progress=None, observation=None):
    observation = observation if observation is not None else {}
    cache = observation.setdefault('subtrees', {})
    walks = observation.setdefault('walks', {})
    measured_roots = observation.setdefault('rootsMeasured', {})
    deadline = time.monotonic() + seconds
    roots = observation.get('roots')
    if roots is None:
        roots = observation['roots'] = catalog(home, workspaces, sessions, installation)
    boundaries = set(roots)
    category = args.get('category')
    if category and category not in {r[0] for r in CATEGORIES}:
        raise ValueError('Choose a storage category.')
    selected = next((r for r in roots.values() if r['id'] == args.get('rootId')), None)
    if args.get('rootId'):
        if not selected or selected['category'] != category:
            raise ValueError('This storage item is no longer available.')
        relative = Path(args.get('path') or '.')
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Choose a path within this storage category.')
        path = _safe(selected['absolute'] / relative)
        if not path.is_dir():
            raise ValueError('This folder is no longer available.')
        rows = []; seen = observation.setdefault('inodes', {}); truncated = False
        def folder_result():
            return {'rows': sorted((dict(row) for row in rows), key=lambda r: r['bytes'] or 0, reverse=True),
                    'label': selected['label'] if relative == Path('.') else path.name, 'path': str(path),
                    **history_selection(selected, relative, True),
                    'parent': str(relative.parent) if relative != Path('.') else None,
                    'bytes': sum(r['bytes'] or 0 for r in rows),
                    'incomplete': truncated or any(r['incomplete'] for r in rows),
                    'measuring': any(r.get('pending') for r in rows),
                    **{k: selected[k] for k in ('workspaceId', 'sessionId', 'backupId', 'protected') if k in selected}}
        listings = observation.setdefault('listings', {})
        if path in listings:
            rows, truncated = listings[path]
        else:
            with os.scandir(path) as entries:
                for entry in entries:
                    if len(rows) >= 2000 or time.monotonic() >= deadline or (stopped and stopped.is_set()):
                        truncated = True
                        break
                    child = Path(entry.path)
                    owner = roots.get(child)
                    if owner and owner['category'] is None:
                        continue
                    usage = cache.get(child, {**(walks[child].result if child in walks else
                        {'bytes': None, 'files': 0, 'directory': True}), 'incomplete': True, 'pending': True})
                    rows.append({'id': entry.name, 'label': entry.name,
                                 'path': str(child.relative_to(selected['absolute'])), **usage,
                                 **({'destination': {'category': owner['category'], 'rootId': owner['id']}} if owner else {})})
            if len(listings) >= 32:
                listings.pop(next(iter(listings)))
            listings[path] = (rows, truncated)
        if progress:
            progress(folder_result())
        last_progress = time.monotonic()
        for row in rows:
            child = selected['absolute'] / row['path']
            if row.get('pending'):
                usage = measure(child, boundaries, seen, [limit], deadline=min(deadline, time.monotonic() + .15), stopped=stopped, cache=cache, walks=walks)
                row.update(usage, pending=bool(usage.get('pending')))
            row.update(history_selection(selected, child.relative_to(selected['absolute']), row['directory']))
            if progress and time.monotonic() - last_progress >= .1:
                last_progress = time.monotonic()
                progress(folder_result())
        return folder_result()
    measured = []; seen = observation.setdefault('inodes', {})
    remaining = list(roots.values())
    last_progress = 0
    for index, root in enumerate(remaining):
        if root['category'] is None or (category and root['category'] != category):
            continue
        path = root['absolute']
        usage = measured_roots.get(path)
        if usage is None or usage.get('pending'):
            usage = measure(path, boundaries, seen, [limit], deadline=min(deadline, time.monotonic() + .15), stopped=stopped, cache=cache, walks=walks)
            if usage['bytes'] is not None:
                measured_roots[path] = usage
        # Empty known roots do not need a drill-down row. Inaccessible roots do.
        if not usage['bytes'] and not usage['incomplete']:
            continue
        measured.append({k: v for k, v in root.items() if k != 'absolute'} |
                        usage)
        if progress and not category and time.monotonic() - last_progress >= .1:
            last_progress = time.monotonic()
            # Retain earlier slices' totals for roots we have not advanced yet.
            later = remaining[index + 1:]
            previous = [{k: v for k, v in r.items() if k != 'absolute'} | measured_roots[r['absolute']]
                        for r in later if r['category'] and r['absolute'] in measured_roots]
            progress(summary(measured + previous, {r['category'] for r in later
                                                   if r['absolute'] not in measured_roots}))
    if category:
        rows = [r for r in measured if r['category'] == category]
        # Each chat may own several locations. Present one logical row and let
        # the deletion preview account for all its locations together.
        if args.get('sessionId'):
            rows = [dict({k:v for k,v in r.items() if k != 'sessionId'}, label=r.get('locationLabel', r['label'])) for r in rows if r.get('sessionId') == args['sessionId']]
            return {'rows': sorted(rows, key=lambda r: r['bytes'] or 0, reverse=True),
                    'label': 'Chat storage locations', 'sessionId': args['sessionId'],
                    'bytes': sum(r['bytes'] or 0 for r in rows), 'incomplete': any(r['incomplete'] for r in rows),
                    'measuring': any(r.get('pending') for r in rows)}
        combined = {}
        for row in rows:
            key = row.get('sessionId') or row['id']
            if key not in combined:
                combined[key] = row.copy()
                if row.get('sessionId'):
                    combined[key]['directory'] = True
                    combined[key]['destination'] = {'category': 'sessions', 'sessionId': row['sessionId']}
            else:
                prior = combined[key]
                prior['bytes'] = (prior['bytes'] or 0) + (row['bytes'] or 0)
                prior['incomplete'] |= row['incomplete']
                prior['pending'] = prior.get('pending', False) or row.get('pending', False)
        rows = sorted(combined.values(), key=lambda r: r['bytes'] or 0, reverse=True)
        return {'rows': rows, 'label': next(c[1] for c in CATEGORIES if c[0] == category),
                'bytes': sum(r['bytes'] or 0 for r in rows), 'incomplete': any(r['incomplete'] for r in rows),
                    'measuring': any(r.get('pending') for r in rows)}
    return summary(measured)
