"""Unreferenced generated environments, through the existing Storage policy.

Only .venv is disposable here. Project sources, locks, qualification and failed
attempt evidence survive. A missing release alias is not deletion authority.
"""
from __future__ import annotations

import base64
import csv
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import time
import tomllib

from . import update_retention as retention

KIND = 'runtime-environment'
PROJECT = re.compile(r'(?:q-[a-f0-9]{32}(?:-[a-f0-9]{8})?|[a-f0-9]{16}|prepare-[a-f0-9]{32})')


def projects(manager):
    root = Path(manager.home) / 'runtime'
    if root.is_symlink():
        raise ValueError('Runtime storage is redirected')
    return sorted((p for p in root.iterdir() if PROJECT.fullmatch(p.name)
                   and p.is_dir() and not p.is_symlink() and (p / '.venv').exists()),
                  key=lambda p: p.name) if root.exists() else []


def referenced_projects(manager):
    """All remaining aliases, including failed/old generations, remain owners."""
    from .runtime_environment import project_path
    referenced = {project_path(manager.home).name}
    generations = {p.name for p in retention._folders(manager)}
    generations.update(retention.protected_generations(manager))
    for generation in generations:
        if generation:
            referenced.add(project_path(manager.home, generation).name)
            referenced.add('prepare-' + generation)
    return referenced


def stat_witness(path):
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(value.st_mode):
        raise ValueError('Reference location is redirected')
    return [value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns]


def references_unchanged(snapshot):
    # Only stat previously observed metadata locations. No discovery, body reads,
    # subprocesses or installed-file inventory belongs in runtime admission.
    return all(stat_witness(path) == stamp for path, stamp in snapshot['witnesses'])


def reference_snapshot(home, workspaces):
    """Inspect canonical source settings once, outside runtime admission.

    File witnesses detect replacement or newly created settings; only discovery
    directories need their own witnesses. Event/transcript writes in a known
    session cannot invalidate an otherwise unchanged source-reference snapshot.
    """
    from .session_files import amplifier_home
    home, shared = Path(home), amplifier_home()
    witnesses, paths = {}, set()
    def remember(path):
        # Current long-lived host has ~55k canonical settings/receipt locations,
        # mostly absent settings files belonging to old native sessions.
        if str(path) not in witnesses and len(witnesses) >= 128000:
            raise ValueError('Runtime reference inspection reached its limit')
        witnesses[str(path)] = stat_witness(path)
    def file(path):
        remember(path)
        paths.add(path)
    def directories(root):
        remember(root)
        if not root.exists():
            return []
        return [p for p in root.iterdir() if p.is_dir()]
    def settings(root, depth=16):
        remember(root)
        if not root.exists():
            return
        for path in root.iterdir():
            if path.is_dir():
                if not depth:
                    raise ValueError('Nested settings inspection reached its limit')
                settings(path, depth - 1)
            elif path.suffix == '.yaml':
                file(path)
    settings(home / 'config')
    for session in directories(home / 'sessions'):
        file(session / 'configuration.json')
    updates = home / 'updates'
    file(updates / 'active.json')
    releases = directories(updates / 'releases') + [updates / 'baseline-runtime']
    for release in releases:
        for name in ('runtime-installed.json', 'runtime-project.json', 'runtime.toml'):
            file(release / name)
    for name in ('profiles', 'worker-leases'):
        root = updates / name
        remember(root)
        for path in root.glob('*.json'):
            file(path)
    for project in directories(shared / 'projects'):
        for session in directories(project / 'sessions'):
            file(session / 'settings.yaml')
    file(shared / 'settings.yaml')
    for workspace in workspaces:
        for name in ('settings.yaml', 'settings.local.yaml'):
            file(Path(workspace) / '.amplifier' / name)
    snapshot = {'witnesses': list(witnesses.items())}
    pattern = re.compile(re.escape(str(home / 'runtime')) + '/(' + PROJECT.pattern + r')(?=/|[\s\"\']|$)')
    referenced = {match[1] for match in pattern.finditer(retention.process_references(home))}
    size = 0
    for path in paths:
        if not path.exists():
            continue
        size += path.stat().st_size
        # Measured long-lived installation: ~50 MiB of canonical generation
        # source receipts. Bodies stay in this bounded maintenance subprocess.
        if size > 128 * 1024 * 1024:
            raise ValueError('Runtime reference inspection reached its limit')
        referenced.update(match[1] for match in pattern.finditer(path.read_text()))
    if not references_unchanged(snapshot):
        raise ValueError('Runtime references changed during inspection')
    return {**snapshot, 'referenced': sorted(referenced)}


def inspect_payload(project):
    """Prove generated install ownership and preserve modified/untracked source."""
    project = Path(project)
    venv = project / '.venv'
    if (not PROJECT.fullmatch(project.name) or project.is_symlink() or venv.is_symlink()
            or not venv.is_dir() or (project / '.git').exists()):
        raise ValueError('Unknown runtime environment')
    manifest = project / 'pyproject.toml'
    lock = project / 'uv.lock'
    if manifest.is_symlink() or lock.is_symlink():
        raise ValueError('Runtime metadata is redirected')
    if tomllib.loads(manifest.read_text()).get('project', {}).get('name') != 'amplifier-web-runtime':
        raise ValueError('Unknown runtime project')
    tomllib.loads(lock.read_text())
    config = venv / 'pyvenv.cfg'
    if config.is_symlink() or not any(line.startswith('uv = ') for line in config.read_text().splitlines()):
        raise ValueError('Not a generated uv environment')
    # A copied checkout or locally changed installed module is useful source,
    # even when placed in a normally disposable environment.
    if any(p.name not in {'bin', 'lib', 'lib64', 'include', 'share', 'Lib', 'Scripts', 'pyvenv.cfg', 'CACHEDIR.TAG', '.gitignore', '.lock'} for p in venv.iterdir()):
        raise ValueError('Untracked environment content is retained')
    roots = list(venv.glob('lib/python*/site-packages')) + list(venv.glob('Lib/site-packages'))
    if not roots:
        raise ValueError('Incomplete installed environment')
    known = set()
    for root in roots:
        records = list(root.glob('*.dist-info/RECORD'))
        if not records:
            raise ValueError('No installed file ownership')
        for record in records:
            for name, digest, _ in csv.reader(record.read_text().splitlines()):
                path = Path(os.path.abspath(root / name))
                # RECORD also owns generated console scripts and data outside
                # site-packages. Verify them rather than discarding those rows.
                if not path.is_relative_to(venv.absolute()):
                    raise ValueError('Installed file ownership escapes environment')
                known.add(path)
                if path.is_symlink():
                    raise ValueError('Installed source is redirected')
                if digest and not name.endswith('.pyc'):
                    algorithm, encoded = digest.split('=', 1)
                    if algorithm != 'sha256' or base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).decode().rstrip('=') != encoded:
                        raise ValueError('Installed source was changed')
    scaffold = {venv / name for name in ('pyvenv.cfg', 'CACHEDIR.TAG', '.gitignore', '.lock')}
    for directory in ('bin', 'Scripts'):
        scaffold.update(venv / directory / name for name in (
            'activate', 'activate.bat', 'activate.csh', 'activate.fish', 'activate.nu',
            'activate.ps1', 'activate.xsh', 'activate_this.py', 'deactivate.bat', 'pydoc.bat'))
    scaffold.update(root / name for root in roots for name in ('_virtualenv.py', '_virtualenv.pth'))
    for path in venv.rglob('*'):
        if path.name == '.git':
            raise ValueError('A source checkout is retained')
        if path.is_symlink():
            if (path == venv / 'lib64' and os.readlink(path) == 'lib') or (
                    path.parent == venv / 'bin' and re.fullmatch(r'python(?:3(?:\.\d+)?)?', path.name)):
                continue
            raise ValueError('Unknown environment link is retained')
        if path.is_file() and path.suffix != '.pyc' and path not in known and path not in scaffold:
            raise ValueError('Untracked installed source is retained')
    return payload_identity(project)


def payload_identity(project):
    from .storage_cleanup_measure import measure
    project = Path(project)
    paths = (project / 'pyproject.toml', project / 'uv.lock', project / '.venv/pyvenv.cfg')
    if project.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError('Runtime metadata is redirected')
    metadata = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {**measure([str(project / '.venv')]), 'metadata': metadata}


@contextmanager
def install_lock(project):
    # Use uv's existing flock without truncating it or creating a missing one.
    # Unsupported hosts retain the copy rather than assuming no installer.
    import fcntl
    descriptor = os.open(project / '.venv/.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


async def reclaim(manager, *, inspect_only=False, selected=None, evidence=None):
    """Bounded orphan discovery shares policy, admission and retirement receipts."""
    from filelock import AsyncFileLock, Timeout
    candidates, protected, removed = [], [], 0
    complete = True
    deadline = time.monotonic() + retention.PASS_SECONDS
    def remaining():
        value = min(retention.INSPECTION_SECONDS, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('Runtime maintenance budget exhausted')
        return value
    result = {'candidates': candidates, 'protectedIds': protected, 'complete': complete, 'removed': removed}
    if selected is not None and not selected:
        return result
    state_revision = manager.service.state.get('configurationRevision')
    if retention.maintenance_blocked(manager):
        return {**result, 'complete': False}
    try:
        rows = projects(manager)
        referenced = referenced_projects(manager)
        if not any(p.name not in referenced for p in rows):
            return {**result, 'protectedIds': [p.name for p in rows], 'presentIds': [p.name for p in rows]}
        snapshot = await retention._probe('runtime-references', {
            'home': str(manager.home), 'workspaces': retention.workspace_paths(manager)}, timeout=remaining())
        # Capture aliases after the snapshot. Its stat witnesses fence any
        # pointer/lease/alias change while this mapping or payload work runs.
        referenced = referenced_projects(manager) | set(snapshot['referenced'])
    except (OSError, ValueError, TypeError, KeyError, TimeoutError):
        return {**result, 'complete': False}
    result['presentIds'] = [p.name for p in rows]
    cursor = getattr(manager, '_runtime_environment_retention_cursor', '')
    rows = [p for p in rows if p.name > cursor] + [p for p in rows if p.name <= cursor]
    checked = 0
    for project in rows:
        if selected is not None and project.name not in selected:
            continue
        if project.name in referenced:
            protected.append(project.name)
            continue
        if checked >= retention.MAX_CANDIDATES or time.monotonic() >= deadline:
            result['complete'] = False
            break
        checked += 1
        if selected is None:
            manager._runtime_environment_retention_cursor = project.name
        try:
            measured = await retention._probe('runtime-payload', {'project': str(project)}, timeout=remaining())
            item = {'kind': KIND, 'id': project.name, 'label': 'Unused worker dependencies',
                    'paths': [str(project / '.venv')], **{k: v for k, v in measured.items() if k != 'metadata'}}
            if inspect_only:
                # Preview has no deletion authority and must not create lock
                # files. The policy run repeats current eligibility checks.
                candidates.append(item)
                continue
            # The qualifier holds this same project lock when creating/reusing
            # q-* environments. uv holds .venv/.lock during installation.
            lock = project.with_name('.' + project.name + '.lock')
            if lock.is_symlink():
                raise ValueError('Runtime installation lock is redirected')
            async with AsyncFileLock(lock, timeout=0):
                with install_lock(project):
                    admission = await retention._probe('runtime-identity', {'project': str(project)}, timeout=remaining())
                    if measured != admission:
                        protected.append(project.name)
                        continue
                    roots = [(str(p), stat_witness(p)) for p in
                             (project, project / '.venv', project / 'pyproject.toml', project / 'uv.lock', project / '.venv/pyvenv.cfg')]
                    async with manager.service.runtime_lifecycle():
                        if (retention.maintenance_blocked(manager)
                                or manager.service.state.get('configurationRevision') != state_revision
                                or not references_unchanged(snapshot)
                                or any(stat_witness(path) != stamp for path, stamp in roots)):
                            protected.append(project.name)
                            continue
                        if evidence:
                            evidence('attempting', item)
                        entry = retention.retire_runtime(manager, project, measured)
                await retention.finish_runtime_retirement(entry, timeout=remaining())
                # Recursive deletion does not hold runtime admission or uv.
                # Save completion before discarding the retirement receipt so
                # a failed evidence write can reconcile on a later policy pass.
                await retention.remove_retired(manager, entry, timeout=remaining(),
                    resolved=(lambda _: evidence('completed', {'kind': KIND, 'id': project.name})) if evidence else None)
                result['removed'] += 1
        except (OSError, ValueError, TypeError, KeyError, TimeoutError, Timeout, ImportError):
            protected.append(project.name)
    if getattr(manager, '_runtime_environment_retention_cursor', None):
        result['runtimeCursor'] = manager._runtime_environment_retention_cursor
    return result
