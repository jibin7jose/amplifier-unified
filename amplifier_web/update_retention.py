"""Retire proven update generations; unknown storage and user work are retained.

Only successful app-owned ecosystem generations are eligible. Current,
rollback, pending, process-referenced and configuration-referenced generations
remain. This is not a global Amplifier/uv cache cleaner or a history migration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


MAX_CANDIDATES = 4
PASS_SECONDS = 30
INSPECTION_SECONDS = 5
REFERENCE_SECONDS = 2


class ProbeFailure(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Storage inspection could not be verified')


async def _probe(operation, values, *, timeout):
    """Run owned storage work in a process we can actually stop.

    Qualification invokes Git and hashes installed sources. Cancelling a
    to_thread await does not stop that work (or its subprocesses). Removal
    operates only on a detached retirement payload, outside admission.
    Private reference text is returned only to this caller, never diagnostics.
    """
    from .probe_process import isolated

    payload = json.dumps(values).encode()
    return await isolated(lambda: _probe_process(operation, payload, timeout))


async def _probe_process(operation, payload, timeout):
    from .probe_process import spawn, stop

    proc = await spawn(
        sys.executable, '-m', __name__, operation,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # Keep draining during stop, including a full pipe or an exited root with
    # live descendants. Cancelling the caller must not cancel this reader first.
    exchange = asyncio.create_task(proc.communicate(payload))
    try:
        output, _ = await asyncio.wait_for(
            asyncio.shield(exchange), timeout,
        )
        if proc.returncode:
            raise ProbeFailure('process-failed')
        try:
            return json.loads(output)
        except (ValueError, UnicodeError):
            raise ProbeFailure('invalid-response') from None
    finally:
        await stop(proc, exchange)


def workspace_paths(manager):
    """Distinct known settings locations, including dormant and hidden owners.

    Retention is explicit maintenance: it needs complete reference coverage,
    but never needs conversation metadata JSON or presentation bodies.
    """
    service = manager.service
    state = getattr(service, '_state', service.state)
    paths = {row['workspace'] for row in state.get('sessions', []) if row.get('workspace')}
    paths.update(row['path'] for row in state.get('workspaces', []) if row.get('path'))
    if state.get('settings', {}).get('workspace'):
        paths.add(state['settings']['workspace'])
    directory = getattr(service, 'directory', None)
    if directory is not None:
        import sqlite3
        try:
            paths.update(row[0] for row in directory.db.execute(
                "SELECT DISTINCT workspace FROM history_local_rows WHERE workspace IS NOT NULL AND workspace!=''"))
            if directory._native_path:
                paths.update(row[0] for row in directory.db.execute(
                    "SELECT DISTINCT workspace_path FROM history_native.native_rows "
                    "WHERE namespace=? AND workspace_path IS NOT NULL AND workspace_path!=''",
                    (directory._native_namespace,)))
        except sqlite3.Error as exc:
            raise ValueError('Saved workspace references could not be verified') from exc
    return sorted(paths)


async def inspect_references(manager, *, timeout):
    # Copy only placement metadata; never load conversation bodies or events.
    workspaces = workspace_paths(manager)
    return await _probe('references', {'home': str(manager.home), 'workspaces': workspaces}, timeout=timeout)


async def fresh_references(manager, *, timeout):
    """Removal admission also bounds delivery, not only child inspection.

    An initial inventory may use a completed result after host-loop delay. A
    final removal check must defer if that delay consumes its existing budget.
    """
    started = time.monotonic()
    references = await inspect_references(manager, timeout=timeout)
    if time.monotonic() - started >= timeout:
        raise TimeoutError('Reference admission budget exhausted')
    return references


async def verify_generation(project, folder, *, timeout):
    await _probe('generation', {'project': str(project), 'folder': str(folder)}, timeout=timeout)


def maintenance_blocked(manager):
    state = manager.service.state['updates']
    return (getattr(manager, 'closed', False) or getattr(manager.service, 'closed', False)
            or manager.awaiting_restart()
            or bool(getattr(manager, 'lock', None) and manager.lock.locked())
            or bool(getattr(manager, 'check_task', None) and not manager.check_task.done())
            or any(state.get(key) for key in ('pendingRelease', 'pendingRollback', 'pendingApp', 'pendingSmartTools'))
            or state.get('phase') in {'checking', 'staging', 'validating', 'activating', 'app-staged'})


def process_references(home):
    """Keep command lines private; inability to inspect means no reclamation."""
    result = subprocess.run(
        ["ps", "-axo", "command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise ValueError("Process references could not be checked")
    root = str(Path(home).resolve())
    return "\n".join(line for line in result.stdout.splitlines() if root in line)


def configuration_paths(manager):
    # Settings paths, registry receipts and worker locks may explicitly select
    # an older local source. A display "usage unknown" must never delete it.
    from .session_files import amplifier_home

    home = Path(manager.home)
    paths = list((home / "config").rglob("*.yaml"))
    shared = amplifier_home()
    paths.append(shared / "settings.yaml")
    for workspace in workspace_paths(manager):
        paths.extend(
            Path(workspace) / ".amplifier" / name
            for name in ("settings.yaml", "settings.local.yaml")
        )
    paths.extend((shared / "projects").glob("*/sessions/*/settings.yaml"))
    return set(paths)


def configuration_references(manager):
    text = []
    for path in configuration_paths(manager):
        if path.exists():
            text.append(path.read_text())
    return "\n".join(text)


def protected_generations(manager):
    """Fresh pointer, pending intent, worker and transitive source protection."""
    from .updates import active_release
    from .generation_leases import references as worker_references

    state = manager.service.state['updates']
    pointer = active_release(manager.home)
    protected = {
        pointer.get("current"),
        pointer.get("previous"),
        state.get("pendingRelease"),
        state.get("pendingRollback"),
    }
    protected.update(worker_references(manager.home))
    for path in (Path(manager.home)/'updates/profiles').glob('*.json'):
        entry = json.loads(path.read_text())
        if entry.get('parentGeneration') in protected:
            protected.add(entry['generation'])
    root = Path(manager.directory) / "releases"
    # A retained environment may contain an explicit editable source from an
    # earlier generation. Follow those edges before deleting any source tree.
    # Shared immutable store objects are outside generation deletion entirely.
    changed = True
    while changed:
        changed = False
        for identity in tuple(protected):
            if not identity:
                continue
            graph = root / identity / 'runtime-installed.json'
            if not graph.exists():
                continue
            text = graph.read_text()
            for source in re.findall(re.escape(str(root)) + r'/([a-f0-9]{32})/', text):
                if source not in protected:
                    protected.add(source)
                    changed = True
    return protected


def _folders(manager):
    root = Path(manager.directory) / 'releases'
    return sorted(folder for folder in root.iterdir()
                  if re.fullmatch('[a-f0-9]{32}', folder.name)
                  and not folder.is_symlink() and folder.is_dir()) if root.exists() else []


def _verified_cache(source, graph):
    """Only sources covered by the already verified tracked-content graph."""
    # installed_graph hashes all tracked files at the cache root before
    # recording the distribution's subdirectory; the proof is repo-wide.
    for row in graph:
        proof = row.get('cacheSource') or {}
        relative = Path(proof.get('subdirectory', ''))
        if (not relative.is_absolute() and '..' not in relative.parts
                and re.fullmatch('[a-f0-9]{64}', proof.get('trackedContentSha256', ''))
                and proof.get('dirty') is False and row.get('path')
                and Path(row['path']).resolve() == (source / relative).resolve()):
            return True
    return False



def _recorded_cache(source, tracked):
    """Prove an uninstalled bundle against its immutable cache receipt.

    Git status alone misses assume-unchanged/skip-worktree edits. Compare the
    index and actual bytes with the recorded tree, including non-Python files.
    No network access, checkout, import or cache repair is performed.
    """
    def git(*args):
        return subprocess.check_output(['git', '--no-optional-locks', *args],
                                       cwd=source, stderr=subprocess.DEVNULL)
    try:
        metadata = json.loads((source / '.amplifier_cache_meta.json').read_text())
        commit = metadata.get('commit', '')
        if (not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', commit)
                or git('rev-parse', 'HEAD').decode().strip() != commit):
            return False
        if not git('for-each-ref', '--format=%(refname)', '--contains', commit, 'refs/remotes'):
            # The updater fetches URL + immutable SHA then checks out detached;
            # Git records that download in FETCH_HEAD without a remote ref.
            fetch = source / '.git/FETCH_HEAD'
            url = metadata.get('git_url')
            if (not isinstance(url, str) or not url or any(c in url for c in '\t\r\n')
                    or not (source / '.git').is_dir() or (source / '.git').is_symlink()
                    or not fetch.is_file() or fetch.is_symlink() or fetch.stat().st_size > 16384):
                return False
            # Git's fetch receipt removes these suffixes from its display URL.
            fetched_url = url.rstrip('/').removesuffix('.git')
            if f"{commit}\t\t'{commit}' of {fetched_url}" not in fetch.read_text().splitlines():
                return False
        tree = {}
        for entry in git('ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
            if not entry:
                continue
            metadata, name = entry.split(b'\t', 1)
            mode, kind, identity = metadata.split()
            if kind != b'blob' or mode not in {b'100644', b'100755'}:
                return False
            tree[name] = (mode, identity)
        index = {}
        for entry in git('ls-files', '--stage', '-z').split(b'\0'):
            if not entry:
                continue
            metadata, name = entry.split(b'\t', 1)
            mode, identity, stage = metadata.split()
            if stage != b'0':
                return False
            index[name] = (mode, identity)
        if index != tree or {os.fsencode(name) for name in tracked} != tree.keys():
            return False
        for raw, (mode, identity) in tree.items():
            relative = Path(os.fsdecode(raw))
            if relative.is_absolute() or '..' in relative.parts:
                return False
            file = source / relative
            if not file.is_file():
                return False
            for part in (file, *file.parents):
                if part == source:
                    break
                if part.is_symlink():
                    return False
            if os.name != 'nt' and bool(file.stat().st_mode & 0o111) != (mode == b'100755'):
                return False
            digest = hashlib.new('sha256' if len(identity) == 64 else 'sha1')
            digest.update(b'blob ' + str(file.stat().st_size).encode() + b'\0')
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest().encode() != identity:
                return False
        return True
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return False


def _build_source(source, name, tracked):
    """A build/lib file is disposable only as an exact tracked-source copy."""
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or relative.parts[:2] != ('build', 'lib'):
        return False
    suffix = Path(*relative.parts[2:])
    for candidate in (suffix, Path('src') / suffix):
        if candidate.as_posix() not in tracked:
            continue
        paths = (source / relative, source / candidate)
        if not all(path.is_file() and not any(part.is_symlink() for part in (path, *path.parents)
                                             if part != source and source in part.parents) for path in paths):
            continue
        if paths[0].stat().st_size != paths[1].stat().st_size:
            continue
        with paths[0].open('rb') as copy, paths[1].open('rb') as original:
            while chunk := copy.read(1024 * 1024):
                if chunk != original.read(len(chunk)):
                    break
            else:
                return True
    return False


def _generated_bytecode(source, name, tracked):
    """Classify disposable import caches, never load or execute their payload.

    These stable CPython release headers cover legacy 3.12 caches, the 3.13
    worker and the 3.14 app. Unknown producers remain protected.
    """
    magic = {'cpython-312': 3531, 'cpython-313': 3571, 'cpython-314': 3627}
    relative = Path(name)
    if (relative.is_absolute() or '..' in relative.parts
            or relative.parent.name != '__pycache__'):
        return False
    match = re.fullmatch(r'(.+)\.(cpython-[0-9]+)(?:\.opt-([12]))?\.pyc', relative.name)
    if match is None or match[2] not in magic:
        return False
    cached = source / relative
    original = cached.parent.parent / (match[1] + '.py')
    try:
        original_name = original.relative_to(source).as_posix()
        if original_name not in tracked and not _build_source(source, original_name, tracked):
            return False
        for file in (cached, original):
            if not file.is_file():
                return False
            for part in (file, *file.parents):
                if part == source:
                    break
                if part.is_symlink():
                    return False
        # Source was independently verified. A canonical untracked cache is
        # disposable even if stale or damaged; arbitrary files are not.
        if not 16 < cached.stat().st_size <= 16 + 16 * 1024 * 1024:
            return False
        with cached.open('rb') as stream:
            header = stream.read(16)
        return (len(header) == 16
                and header[:4] == magic[match[2]].to_bytes(2, 'little') + b'\r\n'
                and int.from_bytes(header[4:8], 'little') in {0, 1, 3})
    except (OSError, ValueError):
        return False


def _retirement_paths(manager, folder, project):
    """Fresh sharing determines the exact tree that an inventory must fence."""
    from .runtime_environment import project_path

    root = Path(manager.directory) / 'releases'
    surviving_projects = {project_path(manager.home, row.name).resolve()
                          for row in root.iterdir() if row.is_dir() and row != folder}
    remove_project = (project.resolve() not in surviving_projects and project.exists()
                      and project.parent.resolve() == (Path(manager.home) / 'runtime').resolve())
    return [str(folder)] + ([str(project)] if remove_project else [])


def retire(manager, folder, project, *, remove_project, measurement=None):
    """Only metadata renames hold admission; partial retirement is preserved."""
    from .deployment import write_private

    root = Path(manager.directory) / 'retired'
    if root.is_symlink():
        raise ValueError('Unknown retirement storage')
    root.mkdir(exist_ok=True)
    entry = root / uuid.uuid4().hex
    entry.mkdir(mode=0o700)
    payload = entry / 'payload'
    payload.mkdir()
    record = {'schema': 1, 'phase': 'retiring', 'generation': folder.name,
              'project': project.name if remove_project else None}
    write_private(entry / 'retirement.json', json.dumps(record))
    # These renames stay on the app-owned filesystem. If either fails, retain
    # the incomplete receipt and both locations; do not guess a rollback.
    folder.rename(payload / 'release')
    if remove_project:
        project.rename(payload / 'runtime')
    if measurement is not None:
        from .storage_cleanup_measure import measure
        paths = [str(payload / 'release')] + ([str(payload / 'runtime')] if remove_project else [])
        if measure(paths)['stamp'] != measurement['stamp']:
            raise ValueError('Files changed during retirement. The detached copies were retained for inspection.')
    record['phase'] = 'retired'
    write_private(entry / 'retirement.json', json.dumps(record))
    return entry


def retire_runtime(manager, project, measurement):
    """Detach only an orphan's generated venv; leave source and receipts intact."""
    from .deployment import write_private
    from .runtime_environment_retention import KIND

    root = Path(manager.directory) / 'retired'
    if root.is_symlink():
        raise ValueError('Unknown retirement storage')
    root.mkdir(exist_ok=True)
    entry = root / uuid.uuid4().hex
    entry.mkdir(mode=0o700)
    payload = entry / 'payload'
    payload.mkdir()
    record = {'schema': 1, 'phase': 'retiring', 'kind': KIND, 'project': project.name,
              'stamp': measurement['stamp']}
    write_private(entry / 'retirement.json', json.dumps(record))
    (project / '.venv').rename(payload / 'runtime')
    return entry


async def finish_runtime_retirement(entry, *, timeout):
    """Full inventory runs after detach, outside runtime admission."""
    from .deployment import write_private
    record = json.loads((entry / 'retirement.json').read_text())
    measured = await _probe('measure', {'paths': [str(entry / 'payload/runtime')]}, timeout=timeout)
    if measured['stamp'] != record['stamp']:
        raise ValueError('Runtime files changed during retirement; detached evidence kept')
    record['phase'] = 'retired'
    write_private(entry / 'retirement.json', json.dumps(record))


async def remove_retired(manager, entry, *, timeout, resolved=None):
    """Reclaim only a completed, owned retirement; resume interrupted removal."""
    root = Path(manager.directory) / 'retired'
    if (root.is_symlink() or entry.parent != root or entry.is_symlink()
            or not re.fullmatch('[a-f0-9]{32}', entry.name)):
        raise ValueError('Unknown retirement storage')
    record = json.loads((entry / 'retirement.json').read_text())
    from .runtime_environment_retention import KIND, PROJECT
    orphan = record.get('kind') == KIND
    if (record.get('schema') != 1 or record.get('phase') not in ({'retiring', 'retired'} if orphan else {'retired'})
            or (orphan and (not PROJECT.fullmatch(record.get('project', '')) or record.get('generation') is not None))
            or (not orphan and (not re.fullmatch('[a-f0-9]{32}', record.get('generation', ''))
                or (record.get('project') is not None and not re.fullmatch(
                    '(?:q-[a-f0-9]{32}(?:-[a-f0-9]{8})?|[a-f0-9]{16})', record['project']))))):
        raise ValueError('Incomplete retirement retained')
    payload = entry / 'payload'
    if payload.is_symlink():
        raise ValueError('Unknown retirement storage')
    disposition = record.get('disposition', 'removed')
    if payload.exists():
        if any(row.name not in ({'runtime'} if orphan else {'release', 'runtime'}) or row.is_symlink() for row in payload.iterdir()):
            raise ValueError('Unknown retirement storage')
        if orphan and record['phase'] == 'retiring':
            if not re.fullmatch('[a-f0-9]{64}', record.get('stamp', '')):
                raise ValueError('Unknown retirement identity retained')
            if (payload / 'runtime').exists():
                # A cancelled inventory can resume only this detached payload.
                # It never reads or renames a recreated live .venv.
                await finish_runtime_retirement(entry, timeout=timeout)
            else:
                # The receipt was saved before rename. Settle this empty
                # attempt; a future policy pass must independently inspect any
                # still-live environment rather than replaying the rename.
                disposition = 'not-detached'
                # Persist settlement before removing the empty payload. A
                # cancellation or failed receipt callback can safely resume it.
                from .deployment import write_private
                record.update(phase='retired', disposition=disposition)
                write_private(entry / 'retirement.json', json.dumps(record))
        await _probe('remove', {'path': str(payload)}, timeout=timeout)
    elif orphan and record['phase'] == 'retiring':
        raise ValueError('Incomplete retirement storage retained')
    if orphan and resolved:
        resolved({**record, 'retirementId': entry.name, 'disposition': disposition})
    # Keep the receipt outside recursive removal so a cancelled subprocess is
    # safely resumable on a later pass or restart.
    (entry / 'retirement.json').unlink()
    entry.rmdir()


async def reclaim(manager, *, inspect_only=False, selected=None, evidence=None):
    """Inspect a bounded batch without holding runtime admission hostage."""
    from .runtime_environment import project_path
    from .updates import process

    removed, retained, checked = 0, 0, 0
    candidates = []
    inspected_protected = set()
    retention_reasons = {}
    complete = True
    deadline = time.monotonic() + PASS_SECONDS
    def remaining(limit):
        value = min(limit, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('Storage maintenance budget exhausted')
        return value
    def result(reason=None, reference_failure=None):
        cursors = {key: getattr(manager, attribute) for key, attribute in (
            ('generationCursor', '_retention_cursor'), ('retiredCursor', '_retired_cursor'))
            if getattr(manager, attribute, None)}
        return {'removed': removed, 'retained': retained, **cursors,
                **({'retentionReasons': retention_reasons} if retention_reasons else {}),
                **({'candidates': candidates, 'protectedIds': sorted(inspected_protected), 'complete': complete and not reason} if inspect_only else {}),
                **({'referenceFailure': reference_failure} if reference_failure else {}),
                **({'reason': reason} if reason else {})}
    prior = manager.service.state['updates'].get('storage', {})
    if maintenance_blocked(manager):
        return result('Storage maintenance deferred while an update is pending.')
    retired = Path(manager.directory) / 'retired'
    if retired.is_symlink():
        return result('Unknown retirement storage was kept.')
    entries = sorted(retired.iterdir()) if retired.exists() and not inspect_only else []
    cursor = getattr(manager, '_retired_cursor', prior.get('retiredCursor', ''))
    entries = [p for p in entries if p.name > cursor] + [p for p in entries if p.name <= cursor]
    for entry in entries:
        if maintenance_blocked(manager):
            return result('Storage maintenance deferred while an update is pending.')
        if time.monotonic() >= deadline:
            return result('Storage maintenance reached its batch limit; remaining storage was kept.')
        # Unknown/partial retirements may never become eligible. Reserve at
        # least half the ordinary batch for generations still in releases/.
        if checked >= max(1, MAX_CANDIDATES // 2):
            break
        checked += 1
        manager._retired_cursor = entry.name
        if selected is not None:
            # Preserve ordinary generation selection semantics. Orphan venv
            # recovery only settles an already-authorized exact retirement;
            # it does not sweep unselected generation payloads.
            try:
                if json.loads((entry / 'retirement.json').read_text()).get('kind') != 'runtime-environment':
                    continue
            except (OSError, ValueError, TypeError):
                continue
        try:
            await remove_retired(manager, entry, timeout=remaining(INSPECTION_SECONDS),
                **({'resolved': lambda row: evidence('reconciled', row)} if evidence else {}))
            removed += 1
        except (OSError, ValueError, TypeError, TimeoutError):
            retained += 1
    reference_stage = 'inspection'
    reference_started = time.monotonic()
    try:
        references = await inspect_references(manager, timeout=remaining(REFERENCE_SECONDS))
        reference_stage = 'protected-generations'
        protected = protected_generations(manager)
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.SubprocessError) as exc:
        reason = ('timeout' if isinstance(exc, TimeoutError) else
                  exc.reason if isinstance(exc, ProbeFailure) else 'unavailable')
        return result('References could not be verified; all storage was kept.', {
            'stage': reference_stage, 'reason': reason,
            'elapsedMs': round((time.monotonic() - reference_started) * 1000, 2)})
    folders = _folders(manager)
    cursor = getattr(manager, '_retention_cursor', prior.get('generationCursor', ''))
    folders = [p for p in folders if p.name > cursor] + [p for p in folders if p.name <= cursor]
    for folder in folders:
        if maintenance_blocked(manager):
            return result('Storage maintenance deferred while an update is pending.')
        if checked >= MAX_CANDIDATES or time.monotonic() >= deadline:
            return result('Storage maintenance reached its batch limit; remaining storage was kept.')
        if selected is None:
            manager._retention_cursor = folder.name
        if selected is not None and folder.name not in selected:
            continue
        marker = folder / "validated.json"
        if (
            folder.name in protected
            or str(folder) in references
            or not marker.is_file()
        ):
            retained += 1
            inspected_protected.add(folder.name)
            continue
        reason_code = 'unverified_generation'
        try:
            marker_text = marker.read_text()
            data = json.loads(marker_text)
            if not isinstance(data.get("hostVersion"), str) or not isinstance(
                data.get("sources"), int
            ):
                raise TypeError("Unknown generation receipt")
            project = project_path(manager.home, folder.name)
            modern = data.get("generationSchema") == 1
            if (not modern and manager.busy()) or str(project) in references or project.is_symlink():
                raise ValueError("Runtime remains referenced")
            # Exact qualification still has to hold before retiring its files.
            graph = folder / 'runtime-installed.json'
            if not graph.exists():
                raise ValueError("No installed graph proof")
            graph_text = graph.read_text()
            checked += 1
            # Qualification must describe this inventory, not a later tree
            # that acquired unknown work after its source/cache proof passed.
            paths = _retirement_paths(manager, folder, project)
            measured = await _probe('measure', {'paths': paths}, timeout=remaining(INSPECTION_SECONDS))
            await verify_generation(project, folder, timeout=remaining(INSPECTION_SECONDS))
            verified_graph = json.loads(graph_text)
            reason_code = 'unverified_source'
            caches = []
            for meta in (folder / "foundation/cache").rglob(
                ".amplifier_cache_meta.json"
            ):
                source = meta.parent
                if source.is_symlink() or not source.resolve().is_relative_to(
                    folder.resolve()
                ):
                    raise ValueError("External source reference")
                changes = await process(
                    "git",
                    "--no-optional-locks",
                    "status",
                    "--porcelain",
                    "--untracked-files=no",
                    cwd=source,
                    timeout=remaining(INSPECTION_SECONDS),
                )
                extras = await process(
                    "git", "ls-files", "--others", '-z', cwd=source,
                    timeout=remaining(INSPECTION_SECONDS), raw=True,
                )
                local_commits = await process(
                    'git', 'rev-list', '--max-count=1', '--all', '--not', 'HEAD', '--remotes',
                    cwd=source, timeout=remaining(INSPECTION_SECONDS))
                extra_names = [os.fsdecode(name) for name in extras.split(b'\0')
                               if name and name != b'.amplifier_cache_meta.json']
                if local_commits or changes:
                    reason_code = 'local_source_changes'
                    raise ValueError("Local changes retained")
                if extra_names:
                    tracked_paths = await process('git', 'ls-files', '-z', cwd=source,
                                                  timeout=remaining(INSPECTION_SECONDS), raw=True)
                    tracked = [os.fsdecode(name) for name in tracked_paths.split(b'\0') if name]
                    caches.append({'source': str(source), 'names': extra_names, 'tracked': tracked,
                                   'installed': _verified_cache(source, verified_graph)})
            if caches:
                proof = await _probe('bytecode', {'caches': caches}, timeout=remaining(INSPECTION_SECONDS))
                if proof is not True:
                    reason_code = (proof.get('reasonCode') if isinstance(proof, dict) else None)
                    if reason_code not in {'unverified_source', 'unverified_generated_files'}:
                        reason_code = 'unverified_generated_files'
                    raise ValueError("Unknown generated files retained")
            reason_code = 'changed_during_inspection'
            # Activation may have won while inspection yielded. No cleanup
            # lock is held during qualification, Git inspection or hashing.
            if maintenance_blocked(manager):
                return result('Storage maintenance deferred while an update is pending.')
            async with manager.service.runtime_lifecycle():
                if maintenance_blocked(manager):
                    return result('Storage maintenance deferred while an update is pending.')
                # Refresh external process/configuration references at the
                # deletion boundary. This read-only probe has a short timeout;
                # incomplete inspection always retains the candidate.
                fresh = await fresh_references(manager, timeout=remaining(REFERENCE_SECONDS))
                if maintenance_blocked(manager):
                    return result('Storage maintenance deferred while an update is pending.')
                if (folder.name in protected_generations(manager)
                        or str(folder) in fresh or str(project) in fresh or (not modern and manager.busy())
                        or folder.is_symlink() or project.is_symlink()
                        or project_path(manager.home, folder.name) != project
                        or marker.read_text() != marker_text or graph.read_text() != graph_text):
                    retained += 1
                    inspected_protected.add(folder.name)
                    continue
                # Never replace the pre-proof baseline. Changed sharing also
                # changes the deletion set, so defer the whole candidate.
                if _retirement_paths(manager, folder, project) != paths:
                    raise ValueError('Runtime sharing changed during inspection')
                admission = await _probe('measure', {'paths': paths}, timeout=remaining(INSPECTION_SECONDS))
                if admission['stamp'] != measured['stamp']:
                    raise ValueError('Files changed during qualification')
                # Measuring yielded; preserve newly active work, settings and
                # receipts before either publishing eligibility or retiring.
                latest_references = await fresh_references(manager, timeout=remaining(REFERENCE_SECONDS))
                if (maintenance_blocked(manager) or folder.name in protected_generations(manager)
                        or str(folder) in latest_references or str(project) in latest_references
                        or (not modern and manager.busy()) or folder.is_symlink() or project.is_symlink()
                        or project_path(manager.home, folder.name) != project
                        or marker.read_text() != marker_text or graph.read_text() != graph_text
                        or _retirement_paths(manager, folder, project) != paths):
                    inspected_protected.add(folder.name)
                    continue
                if inspect_only:
                    candidates.append({'kind': 'ecosystem', 'id': folder.name,
                        'label': 'Unused component version', 'paths': paths, **measured})
                    continue
                if evidence:
                    evidence('attempting', {'kind': 'ecosystem', 'id': folder.name, **measured})
                entry = retire(manager, folder, project, remove_project=len(paths) == 2, measurement=measured)
            # Recursive deletion may be slow or cancelled. The generation is
            # already detached, so it cannot delay activation or shutdown.
            if maintenance_blocked(manager):
                return result('Retired storage was kept while an update is pending.')
            await remove_retired(manager, entry, timeout=remaining(INSPECTION_SECONDS))
            removed += 1
            if evidence:
                evidence('completed', {'kind': 'ecosystem', 'id': folder.name})
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            TimeoutError,
            subprocess.SubprocessError,
        ):
            retained += 1
            inspected_protected.add(folder.name)
            retention_reasons[folder.name] = reason_code
    return result()


if __name__ == '__main__':
    values = json.load(sys.stdin)
    if sys.argv[1] == 'references':
        from types import SimpleNamespace
        manager = SimpleNamespace(home=values['home'], service=SimpleNamespace(state={
            'sessions': [{'workspace': value} for value in values['workspaces']]}))
        print(json.dumps(process_references(manager.home) + configuration_references(manager)))
    elif sys.argv[1] == 'runtime-references':
        from .runtime_environment_retention import reference_snapshot
        print(json.dumps(reference_snapshot(values['home'], values['workspaces'])))
    elif sys.argv[1] in {'runtime-payload', 'runtime-identity'}:
        from .runtime_environment_retention import inspect_payload, payload_identity
        operation = inspect_payload if sys.argv[1] == 'runtime-payload' else payload_identity
        print(json.dumps(operation(values['project'])))
    elif sys.argv[1] == 'generation':
        from .runtime_qualification import verify_recorded
        verify_recorded(Path(values['project']), Path(values['folder']))
        print('null')
    elif sys.argv[1] == 'bytecode':
        result = True
        for row in values['caches']:
            source, tracked = Path(row['source']), set(row['tracked'])
            if not (row['installed'] or _recorded_cache(source, tracked)):
                result = {'reasonCode': 'unverified_source'}
                break
            if not all(_generated_bytecode(source, name, tracked) or _build_source(source, name, tracked)
                       for name in row['names']):
                result = {'reasonCode': 'unverified_generated_files'}
                break
        print(json.dumps(result))
    elif sys.argv[1] == 'runner':
        from .managed_runner_updates import runtime_identity
        if runtime_identity(values['installation']) != values['runtime']:
            raise ValueError('Runner runtime changed')
        print('null')
    elif sys.argv[1] == 'measure':
        from .storage_cleanup_measure import measure
        print(json.dumps(measure(values['paths'])))
    elif sys.argv[1] == 'remove':
        shutil.rmtree(values['path'])
        print('null')
    else:
        raise ValueError('Unknown storage inspection')
