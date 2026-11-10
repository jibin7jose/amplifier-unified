"""Read-only, cached discovery of Amplifier's shared project/session files.

The directory names are storage identifiers, not reversible encodings of paths.
Only an explicit working directory whose CLI slug matches the project is trusted.
No transcript, event stream, runtime, or CLI host is loaded to build this index.
Call ``scan`` from a worker thread: older CLI metadata can contain large configs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from .naming import automatic_metadata
import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections import OrderedDict, deque

from .session_files import amplifier_home, project_slug

_MAX_METADATA = 8 * 1024 * 1024
_METADATA_FIELDS_VERSION = 2
_STAMP_FILES = ('metadata.json', 'metadata.json.backup', 'transcript.jsonl',
                'transcript.jsonl.backup', 'naming.json', os.path.join('context-intelligence', 'metadata.json'))
_FIELDS = (
    'name', 'title', 'description', 'name_source', 'name_revision', 'name_policy_revision',
    'name_auto', 'name_auto_revision', 'bundle', 'bundle_name',
    'parent_id', 'parent_session_id', 'agent_name', 'created', 'created_at', 'started_at', 'updated_at',
    'last_updated', 'last_event_at', 'ended_at', 'turn_count', 'working_dir',
    'cwd', 'project_dir', 'workspace', 'model', 'status', 'forked_from_turn', 'forked_at',
    'session_visibility', 'session_purpose',
)


def _text(value):
    return value[:8192] if isinstance(value, str) and value.strip() else None


def _timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()
        except (ValueError, OverflowError):
            return None
    return None


def _signature(path):
    value = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('Shared metadata must be a regular file')
    return (value.st_ino, value.st_mtime_ns, value.st_size)


def _small_metadata(value):
    if not isinstance(value, dict):
        raise ValueError('Shared metadata must be an object')
    result = {key: value[key] for key in _FIELDS if isinstance(value.get(key), (str, int, float))}
    for key in ('parent_id', 'parent_session_id'):
        if key in value and value[key] is None:
            result[key] = None  # Explicit root identity must survive compaction.
    fork = value.get('fork')
    if isinstance(fork, dict) and _text(fork.get('source_session_id')) and type(fork.get('through_user_turn')) is int:
        result['_independent_fork'] = True
    # Some CLI versions only persisted the session cwd inside its config.
    config = value.get('config')
    if isinstance(config, dict):
        for key in ('working_dir', 'cwd', 'project_dir', 'workspace'):
            if not result.get(key) and isinstance(config.get(key), str):
                result[key] = config[key]
    # The cache-format marker is not canonical metadata. In particular it may
    # not turn a missing/empty metadata read into positive resume evidence.
    if result:
        result['_nativeFieldsVersion'] = _METADATA_FIELDS_VERSION
    return result


def classify_session(identity, *metadata_sources):
    """Distinguish internal jobs, runtime children and independent roots.

    Foundation CLI forks are independent roots with parent_id plus the documented
    forked_from_turn/forked_at fields. Other native parent identities indicate
    runtime children. Explicit native root metadata wins over stale CI metadata.
    Only missing lineage falls back to app-cli's is_top_level_session convention
    (child IDs contain '_'); span IDs are never mistaken for full parent IDs.
    """
    for metadata in metadata_sources:
        parent_keys = [key for key in ('parent_id', 'parent_session_id')
                       if key in metadata and (metadata[key] is None or isinstance(metadata[key], str))]
        parent_key = next((key for key in parent_keys if _text(metadata[key])),
                          parent_keys[0] if parent_keys else None)
        parent = _text(metadata.get(parent_key)) if parent_key else None
        forked = (metadata.get('_independent_fork') is True or
                  (type(metadata.get('forked_from_turn')) is int and metadata['forked_from_turn'] >= 0
                   and bool(_text(metadata.get('forked_at')))))
        if forked:
            return 'root', parent
        # Visibility is a producer declaration, not inferred from a prompt,
        # title, invocation mode, TTY, or generic agent/recipe origin. Those
        # also occur in user-requested standalone conversations.
        if metadata.get('session_visibility') == 'internal':
            return 'internal', parent
        if parent_key is not None:
            return ('worker' if parent else 'root'), parent
        if metadata.get('session_visibility') == 'chat':
            return 'root', None
    return ('worker' if '_' in identity else 'root'), None


class NativeHistory:
    """Index existing native files without creating or changing any of them.

    ``home`` defaults to session_files.amplifier_home(), including AMPLIFIER_HOME.
    ``known_workspaces`` may contain registered absolute folder paths (or objects
    with a ``path`` field); their exact CLI slugs can resolve older metadata.
    Returned values are detached from the cache and contain no conversation text.
    """

    RECONCILE_INTERVAL = 15 * 60
    UNWATCHED_RECONCILE_INTERVAL = 60
    RECOVERY_SLICE_SECONDS = .025

    def __init__(self, home=None, *, known_workspaces=(), watch=False, cache_path=None, directory=False, diagnostics=None):
        self.diagnostics = diagnostics
        self.home = Path(home).expanduser().resolve() if home is not None else amplifier_home()
        self.known_workspaces = known_workspaces
        self._files = {}
        self._file_projects = set()
        self._projects = {}
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._reads = 0
        self._project_inputs = {}
        self._working_dirs = {}
        self._working_dir_slugs = {}
        self._totals = None
        self._reconcile_at = 0
        self._watch_enabled = watch
        self._watch = None
        self._watch_root = None
        self._watch_retry_at = 0
        self._project_paths = {}
        self._known_input = None
        self._known_paths = {}
        self._snapshot_projects = ()
        self._snapshot_issues = []
        self._snapshot_revision = None
        self._change_bases = OrderedDict()
        self._cache_path = cache_path
        self._catalog = None
        self._cache_loaded = False
        self._cache_error = False
        self._dirty_files = set()
        self._cache_retry_at = 0
        self._cache_retry_delay = 5
        self._cache_rejected_stamp = None
        self._recovery = None
        self._recovery_status = {'phase': 'not-started', 'steps': 0, 'completedAt': None}
        self._recovery_budget = 64
        self._availability_cursor = 0
        self._recovery_urgent = deque()
        self._recovery_queued = set()
        self._pending_invalidations = set()
        self._cached_start = False
        self._scan_original_projects = None
        self._recovery_modified = set()
        self._recovery_issues_changed = False
        # Consumer cutover is explicit: legacy scan/load still return complete
        # detached snapshots. The shared directory consumes metadata pages.
        self.directory = bool(directory and cache_path is not None)
        self._directory_journal = OrderedDict()
        self._directory_pending = {'sessions': {}, 'removed': set()}
        self._directory_unsaved = set()
        self._directory_recovery_project = None
        self._directory_recovery_changes = {}
        self._directory_recovery_staged = False

    def _load_catalog(self):
        # Called only from the discovery worker, never in host construction.
        if self._cache_loaded and (self._catalog is not None or self._cache_path is None):
            return
        if time.monotonic() < self._cache_retry_at:
            return
        if self._cache_rejected_stamp is not None:
            try:
                if _signature(Path(self._cache_path)) == self._cache_rejected_stamp:
                    return  # Preserve rejected evidence until repaired/replaced.
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                return
        was_loaded = self._cache_loaded
        self._cache_loaded = True
        if self._cache_path is None:
            return
        try:
            from .native_catalog import NativeCatalog
            self._catalog = NativeCatalog(self._cache_path, self.home, diagnostics=self.diagnostics)
            self._catalog.stop = self._closing
            if self.directory:
                cached_projects, cached_files = self._catalog.load_projects(), {}
            else:
                cached_projects, cached_files = self._catalog.load()
            if not was_loaded or (self.directory and not self._projects and not self._directory_unsaved
                                  and self._recovery_status['phase'] == 'not-started'):
                self._projects, self._files = cached_projects, cached_files
                self._file_projects = set(self._projects)
                self._cached_start = bool(cached_projects)
            else:
                # Live memory contains newer canonical reads after a failed
                # cache save. Reconnect may not replace it with stale disk rows.
                if self.directory:
                    names = self._directory_unsaved
                    saved = self._catalog.save(
                        {key: self._projects[key] for key in names if key in self._projects},
                        {key: cached_projects[key] for key in names if key in cached_projects},
                        self._files, self._dirty_files)
                    self._directory_record(saved)
                    self._directory_compact(names)
                    self._directory_unsaved.clear()
                    self._dirty_files.clear()
                else:
                    self._catalog.save(self._projects, cached_projects, self._files, self._files)
            self._cache_error = False
            self._cache_rejected_stamp = None
            self._cache_retry_delay = 5
        except OSError:
            self._catalog = None
            self._cache_error = True
        except (ValueError, json.JSONDecodeError):
            self._catalog = None
            self._cache_error = True
            try:
                self._cache_rejected_stamp = _signature(Path(self._cache_path))
            except (OSError, ValueError):
                pass
        except Exception as exc:
            import sqlite3
            if not isinstance(exc, sqlite3.Error):
                raise
            self._catalog = None
            self._cache_error = True
            if isinstance(exc, sqlite3.DatabaseError) and not isinstance(exc, sqlite3.OperationalError):
                try:
                    self._cache_rejected_stamp = _signature(Path(self._cache_path))
                except (OSError, ValueError):
                    pass
        if self._catalog is None:
            self._cache_retry_at = time.monotonic() + self._cache_retry_delay
            self._cache_retry_delay = min(60, self._cache_retry_delay * 2)

    @staticmethod
    def _known_signature(known):
        return frozenset(str(item.get('path') if isinstance(item, dict) else item) for item in known)

    def needs_scan(self, known_workspaces):
        """Cheap idle check; a manual refresh still always performs a scan."""
        if self._cache_error and self._cache_path is not None and self._catalog is None:
            if time.monotonic() >= self._cache_retry_at:
                if self._cache_rejected_stamp is None:
                    return True
                try:
                    if _signature(Path(self._cache_path)) != self._cache_rejected_stamp:
                        return True
                except (OSError, ValueError):
                    return True
        if (self._recovery is not None or self._recovery_urgent or self._pending_invalidations
                or time.monotonic() >= self._reconcile_at or not self._watch
                or not self._watch.unchanged()
                or self._known_signature(known_workspaces) != self._known_input):
            return True
        try:
            info = (self.home / 'projects').stat()
            if (info.st_dev, info.st_ino) != self._watch_root:
                return True
            # Availability is reconciliation too; inspect a bounded rotating
            # slice rather than stat every registered folder on each idle tick.
            total = len(known_workspaces)
            cursor = self._availability_cursor % max(1, total)
            sampled = (known_workspaces[cursor:cursor + 64] if total else [])
            self._availability_cursor = (cursor + len(sampled)) % max(1, total)
            for workspace in sampled:
                if isinstance(workspace, dict) and bool(workspace.get('path') and Path(workspace['path']).is_dir()) != workspace.get('available'):
                    return True
        except (OSError, ValueError):
            return True
        return False

    def close(self):
        # asyncio cancellation does not stop a to_thread scan. Close only after
        # that bounded slice releases ownership, including reentrant watch repair.
        self._closing.set()
        with self._lock:
            self._close_resources()

    def _close_resources(self):
        if self._recovery is not None:
            self._recovery.close()
            self._recovery = None
        if self._watch:
            self._watch.close()
            self._watch = None

    def _invalidations(self):
        if not self._watch_enabled:
            return False, set()
        root = self.home / 'projects'
        try:
            info = root.stat()
            identity = (info.st_dev, info.st_ino)
        except OSError:
            identity = None
        if self._watch and (identity != self._watch_root or not self._watch.thread.is_alive()):
            self._close_resources()
        if not self._watch and identity and time.monotonic() >= self._watch_retry_at:
            from .history_watch import HistoryWatch
            self._watch_root = identity
            self._watch_retry_at = time.monotonic() + 60
            self._watch = HistoryWatch(root)
        return self._watch.take() if self._watch else (False, set())

    def _read(self, path, issues, project, identity=None):
        previous = self._cached_file(path)
        try:
            signature = _signature(path)
        except FileNotFoundError:
            # Atomic replacement or an incremental save can briefly hide a file.
            return previous[1] if previous else {}
        except (OSError, ValueError):
            issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': identity})
            return previous[1] if previous else {}
        if previous and previous[0] == signature and (previous[2] or previous[1].get('_nativeFieldsVersion') == _METADATA_FIELDS_VERSION):
            if previous[2]:
                issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': identity})
            return previous[1]
        failed = False
        try:
            if signature[2] > _MAX_METADATA:
                raise ValueError('Shared metadata is too large')
            with path.open('rb') as source:
                self._reads += 1
                raw = source.read(_MAX_METADATA + 1)
            if len(raw) > _MAX_METADATA:
                raise ValueError('Shared metadata is too large')
            value = _small_metadata(json.loads(raw))
        except (OSError, ValueError, RecursionError):
            failed = True
            issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': identity})
            # Cache the failed stat too: keep the last valid summary and retry
            # when the writer actually changes the file, not on every poll.
            value = previous[1] if previous else {}
        self._files[path] = (signature, value, failed)
        self._dirty_files.add(path)
        self._file_projects.add(project)
        return value

    def _native_metadata(self, directory, issues, project):
        """Canonical parsing/backup recovery belongs to Foundation."""
        from amplifier_foundation.session.history import SessionHistoryStore
        path = directory / 'metadata.json'
        previous = self._cached_file(path)
        try:
            stamps = []
            for candidate in (path, directory / 'metadata.json.backup'):
                try:
                    stamp = _signature(candidate)
                    if stamp[2] > _MAX_METADATA:
                        raise ValueError('Shared metadata is too large')
                    stamps.append(stamp)
                except FileNotFoundError:
                    stamps.append(None)
            if not any(stamps) and previous:
                return previous[1]
            signature = tuple(stamps)
            if previous and previous[0] == signature and (previous[2] or previous[1].get('_nativeFieldsVersion') == _METADATA_FIELDS_VERSION):
                if previous[2]:
                    issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': directory.name})
                return previous[1]
            self._reads += 1
            reader = SessionHistoryStore(directory, session_id=directory.name)
            value = _small_metadata(reader.load_metadata())
            failed = False
            if reader.diagnostics:
                issues.append({'kind': 'recovered', 'nativeProject': project, 'nativeIdentity': directory.name})
        except (OSError, ValueError, RecursionError):
            failed = True
            signature = locals().get('signature')
            value = previous[1] if previous else {}
            issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': directory.name})
        self._files[path] = (signature, value, failed)
        self._dirty_files.add(path)
        self._file_projects.add(project)
        return value

    def _cached_file(self, path):
        previous = self._files.get(path)
        if previous is None and self.directory and self._catalog is not None:
            try:
                previous = self._catalog.file(path)
            except (OSError, ValueError, sqlite3.Error):
                # Corrupt disposable file metadata cannot prevent a fresh
                # canonical read; it is replaced by the next successful save.
                self._cache_error = True
            if previous is not None:
                self._files[path] = previous
        return previous

    def _trim_files(self):
        if self.directory and len(self._files) > 1024:
            for path in tuple(self._files):
                if path not in self._dirty_files:
                    self._files.pop(path, None)
                    if len(self._files) <= 1024:
                        break

    def _directory_record(self, saved):
        if not saved:
            return
        for project, identity in saved['removed']:
            key = project, identity
            self._directory_pending['sessions'].pop(key, None)
            self._directory_pending['removed'].add(key)
            if project == self._directory_recovery_project and not self._directory_recovery_staged:
                self._directory_recovery_changes[identity] = None
        for row in saved['sessions']:
            key = row['nativeProject'], row['nativeIdentity']
            self._directory_pending['sessions'][key] = row
            self._directory_pending['removed'].discard(key)
            if key[0] == self._directory_recovery_project and not self._directory_recovery_staged:
                self._directory_recovery_changes[key[1]] = row

    def _directory_compact(self, names):
        from .native_catalog import CatalogRows
        for name in names:
            current = self._projects.get(name)
            if current is not None:
                self._projects[name] = {'workspace': current['workspace'],
                                        'sessions': CatalogRows(self._catalog, name)}
        self._trim_files()

    def _directory_result(self, since, force, previous, changed, issues, watching, reconcile):
        """Bounded changes plus a directory invalidation, never a library reset body."""
        old_token = self._snapshot_revision
        pending = self._directory_pending
        workspace_changes = [self._projects[name]['workspace'] for name in changed if name in self._projects
                             and self._projects[name]['workspace'] != previous.get(name, {}).get('workspace')]
        removed_projects = sorted(set(previous) - self._projects.keys())
        altered = bool(old_token is None or changed or pending['sessions'] or pending['removed']
                       or issues != self._snapshot_issues)
        if altered:
            next_token = object()
            overflow = len(pending['sessions']) + len(pending['removed']) > 512
            delta = {'sessions': [] if overflow else list(pending['sessions'].values()),
                     'removed': [] if overflow else sorted(pending['removed']),
                     'workspaces': workspace_changes, 'removedProjects': removed_projects,
                     'invalidate': overflow}
            if old_token is not None:
                self._directory_journal[old_token] = (next_token, copy.deepcopy(delta))
                while len(self._directory_journal) > 4:
                    self._directory_journal.popitem(last=False)
            self._snapshot_revision = next_token
            self._snapshot_issues = copy.deepcopy(issues)
            self._directory_pending = {'sessions': {}, 'removed': set()}
            self._snapshot_projects = tuple(self._projects.items())
        reset = force or since is None
        sessions, removed, workspaces, retired = {}, set(), {}, set()
        cursor = since
        while cursor is not self._snapshot_revision and not reset:
            entry = self._directory_journal.get(cursor)
            if entry is None:
                reset = True
                break
            cursor, delta = entry
            if delta['invalidate']:
                reset = True
                break
            for name, identity in delta['removed']:
                sessions.pop((name, identity), None); removed.add((name, identity))
            for row in delta['sessions']:
                key = row['nativeProject'], row['nativeIdentity']
                sessions[key] = row; removed.discard(key)
            for name in delta['removedProjects']:
                retired.add(name); workspaces.pop(name, None)
            for row in delta['workspaces']:
                name = row['nativeProject']
                workspaces[name] = row; retired.discard(name)
            if len(sessions) + len(removed) > 512:
                reset = True
        if reset:
            # Invalidation directs consumers to requery their requested pages
            # and admitted IDs. It never means the on-disk directory is empty.
            sessions, removed, retired = {}, set(), set()
            workspaces = {name: project['workspace'] for name, project in self._projects.items()}
        result = copy.deepcopy({'reset': reset, 'directoryReady': self._catalog is not None,
            'workspaces': list(workspaces.values()), 'sessions': list(sessions.values()),
            'removed': sorted(removed), 'removedProjects': sorted(retired),
            'issues': issues, 'projectCount': len(self._projects), **self._totals,
            'metadataReads': self._reads, 'reconciled': reconcile,
            'notificationsReady': watching, 'reconciliation': self._recovery_status})
        result['base'] = since
        return self._snapshot_revision, result

    @staticmethod
    def _directories(directory):
        with os.scandir(directory) as entries:
            return sorted((Path(entry.path) for entry in entries
                           if not entry.name.startswith('.') and entry.is_dir(follow_symlinks=False)),
                          key=lambda path: path.name)

    def _working_dir(self, metadata, slug):
        for key in ('working_dir', 'cwd', 'project_dir', 'workspace'):
            value = _text(metadata.get(key))
            if not value:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                continue
            try:
                self._project_paths.setdefault(slug, set()).add(str(candidate))
                key = str(candidate)
                if key not in self._working_dirs:
                    self._working_dirs[key] = str(candidate.resolve())
                resolved = self._working_dirs[key]
                if resolved not in self._working_dir_slugs:
                    self._working_dir_slugs[resolved] = project_slug(resolved)
                if self._working_dir_slugs[resolved] == slug:
                    return resolved
            except (OSError, ValueError, RuntimeError):
                continue
        return None

    def _project_stamp(self, project, known):
        """Probe metadata, not bodies or derived rows, before rebuilding a project.

        Include absent files so new metadata/transcripts are discovered. A
        bounded full reconciliation also re-resolves symlinks and permissions.
        """
        if self.directory:
            steps = self._project_stamp_steps(project, known)
            while True:
                try:
                    next(steps)
                except StopIteration as done:
                    return done.value
        paths = [project / 'metadata.json', project / 'sessions']
        paths.extend(known.get(project.name, ()))
        paths.extend(self._project_paths.get(project.name, ()))
        try:
            with os.scandir(project / 'sessions') as entries:
                directories = sorted((entry for entry in entries
                                      if not entry.name.startswith('.') and entry.is_dir(follow_symlinks=False)),
                                     key=lambda entry: entry.name)
        except FileNotFoundError:
            directories = []
        for directory in directories:
            # Stamps need only a string prefix. Keep the normalized scandir
            # path instead of constructing and converting a temporary Path.
            prefix = directory.path + os.sep
            paths.extend(prefix + name for name in _STAMP_FILES)
        result = [tuple(sorted(known.get(project.name, ())))]
        result.append(tuple((path, str(Path(path).resolve()))
                            for path in sorted(self._project_paths.get(project.name, ()))))
        for path in paths:
            try:
                info = os.stat(path, follow_symlinks=False)
                result.append((str(path), info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            except FileNotFoundError:
                result.append((str(path), None))
        return tuple(result)

    def _project_stamp_steps(self, project, known):
        """Compare canonical inputs cooperatively before rebuilding summaries."""
        paths = [project / 'metadata.json', project / 'sessions']
        paths.extend(known.get(project.name, ()))
        paths.extend(self._project_paths.get(project.name, ()))
        result = [tuple(sorted(known.get(project.name, ())))]
        result.append(tuple((path, str(Path(path).resolve()))
                            for path in sorted(self._project_paths.get(project.name, ()))))
        yield
        if not self._safe_project(project):
            raise OSError('Native project changed during reconciliation')
        for path in paths:
            try:
                info = os.stat(path, follow_symlinks=False)
                result.append((str(path), info.st_dev, info.st_ino, info.st_mode,
                               info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            except FileNotFoundError:
                result.append((str(path), None))
        leaves = {}
        for number, directory in enumerate(self._recovery_directories(project / 'sessions')):
            # At most two canonical session probes per step; a 64-step slice
            # still admits no more than 128 distinct session probes.
            if number % 2 == 0:
                yield
                if not self._safe_project(project):
                    raise OSError('Native project changed during reconciliation')
            if directory is None:
                continue
            # Suspended iterators are hints, never authority to follow a
            # substituted ancestor or a session symlink.
            if not stat.S_ISDIR(os.stat(str(directory), follow_symlinks=False).st_mode):
                raise OSError('Native session changed during reconciliation')
            values = []
            prefix = str(directory) + os.sep
            for name in _STAMP_FILES:
                path = prefix + name
                try:
                    info = os.stat(path, follow_symlinks=False)
                    values.append((path, info.st_dev, info.st_ino, info.st_mode,
                                   info.st_size, info.st_mtime_ns, info.st_ctime_ns))
                except FileNotFoundError:
                    values.append((path, None))
            leaves[directory.name] = (hashlib.sha256(json.dumps(values).encode()).digest()
                                      if self.directory else values)
        if self.directory:
            # Keep one digest per project, not every dormant file's Path/stamp
            # tuple. Temporary leaf digests make scandir ordering immaterial.
            digest = hashlib.sha256(json.dumps(result).encode())
            for name in sorted(leaves):
                digest.update(name.encode()); digest.update(b'\0'); digest.update(leaves[name])
            return (*result[:2], ('directory-digest-v1', digest.hexdigest()))
        for name in sorted(leaves):
            result.extend(leaves[name])
        return tuple(result)

    def _scan_project(self, project, known, issues, *, transcript_ids=None, target_only=False):
        steps = self._scan_project_steps(project, known, issues, transcript_ids=transcript_ids,
                                        target_only=target_only)
        while True:
            try:
                next(steps)
            except StopIteration as done:
                return done.value

    def _scan_project_steps(self, project, known, issues, *, transcript_ids=None, bounded=False, target_only=False):
        """One session per yield; recovery can suspend without reading bodies."""
        slug = project.name
        issue_start = len(issues)
        prior = self._projects.get(slug)
        if not self._safe_project(project):
            issues.append({'kind': 'unreadable', 'nativeProject': slug})
            return prior
        partial = bool(transcript_ids and prior and prior['workspace'].get('path'))
        if partial:
            # A transcript notification is not proof of directory membership.
            # Do not follow a substituted session/project ancestor.
            try:
                root = self.home / 'projects'
                sessions_root = project / 'sessions'
                safe = (stat.S_ISDIR(project.stat(follow_symlinks=False).st_mode)
                        and stat.S_ISDIR(sessions_root.stat(follow_symlinks=False).st_mode)
                        and sessions_root.resolve().is_relative_to(root.resolve()))
                for identity in transcript_ids:
                    candidate = sessions_root / identity
                    safe = (safe and Path(identity).name == identity
                            and identity not in {'', '.', '..'}
                            and stat.S_ISDIR(candidate.stat(follow_symlinks=False).st_mode)
                            and candidate.resolve().parent == sessions_root.resolve())
                if not safe:
                    partial = False
            except (OSError, ValueError, RuntimeError):
                partial = False
        if target_only and not partial:
            return None  # A full rebuild must be admitted through recovery.
        try:
            directories = ([project / 'sessions' / identity for identity in sorted(transcript_ids)]
                           if partial else (self._recovery_directories(project / 'sessions')
                                            if bounded else self._directories(project / 'sessions')))
        except FileNotFoundError:
            directories = []
        except OSError:
            issues.append({'kind': 'unreadable', 'nativeProject': slug})
            return self._projects.get(slug)
        from .native_catalog import CatalogRows, CatalogPatchRows
        lazy = self.directory and isinstance((prior or {}).get('sessions'), (CatalogRows, CatalogPatchRows))
        rows = ([row for row in prior['sessions'] if row['nativeIdentity'] not in transcript_ids]
                if partial and not lazy else [])
        candidates = set(known.get(slug, ()))
        if partial:
            candidates.add(prior['workspace']['path'])
        project_metadata = self._read(project / 'metadata.json', issues, slug)
        candidate = self._working_dir(project_metadata, slug)
        if candidate:
            candidates.add(candidate)
        previous_rows = prior['sessions'] if lazy else {
            row['nativeIdentity']: row for row in self._projects.get(slug, {}).get('sessions', [])}
        for directory in directories:
            yield
            if directory is None:
                continue
            # Iterators survive between slices. Recheck every ancestor and leaf
            # after resumption; an earlier DirEntry is not current authority.
            try:
                safe = (self._safe_project(project)
                        and stat.S_ISDIR(directory.stat(follow_symlinks=False).st_mode)
                        and directory.parent == project / 'sessions')
            except (OSError, ValueError, RuntimeError):
                safe = False
            if not safe:
                issues.append({'kind': 'unreadable', 'nativeProject': slug,
                               'nativeIdentity': directory.name})
                previous_row = previous_rows.get(directory.name)
                if previous_row is not None:
                    # Workspace resolution below may change this summary.
                    # Retained revision bases and SQLite comparisons must keep
                    # the old row immutable even while a leaf is unreadable.
                    rows.append(dict(previous_row))
                continue
            # CLI worker IDs can contain ':' and '_'. All existing basenames
            # are safe to index; root execution has a narrower ID contract.
            native = self._native_metadata(directory, issues, slug)
            capture = self._read(directory / 'context-intelligence' / 'metadata.json', issues, slug, directory.name)
            naming = self._read(directory / 'naming.json', issues, slug, directory.name)
            meta = {**capture, **({key: value for key, value in naming.items()
                        if key in {'name', 'description', 'name_source'}} if not native.get('name') else {}), **native}
            for source in (native, capture):
                candidate = self._working_dir(source, slug)
                if candidate:
                    candidates.add(candidate)
            try:
                transcript = _signature(directory / 'transcript.jsonl')
            except FileNotFoundError:
                try:
                    transcript = _signature(directory / 'transcript.jsonl.backup')
                except (OSError, ValueError):
                    transcript = None
            except (OSError, ValueError):
                transcript = None
                issues.append({'kind': 'unreadable', 'nativeProject': slug, 'nativeIdentity': directory.name})
            turns = meta.get('turn_count')
            turns = turns if isinstance(turns, int) and not isinstance(turns, bool) and turns >= 0 else None
            # CI diagnostics and runtime probes also make session directories.
            # A real saved transcript or a positive native turn count is needed.
            previous_row = previous_rows.get(directory.name)
            if not (transcript and transcript[2]) and not (turns and turns > 0) and previous_row is None:
                continue
            updated = max([_timestamp(meta.get(key)) or 0 for key in
                           ('updated_at', 'last_updated', 'last_event_at', 'ended_at')]
                          + [transcript[1] / 1e9 if transcript else (previous_row or {}).get('updatedAt', 0)])
            created = next((_timestamp(meta.get(key)) for key in ('created', 'created_at', 'started_at')
                            if _timestamp(meta.get(key)) is not None), updated)
            # Renaming or re-saving metadata must not make an old conversation
            # recent. The transcript's write is conversation activity; without
            # it retain the prior activity or an explicit captured event time.
            recent = (transcript[1] / 1e9 if transcript and transcript[2] else
                      max([_timestamp(meta.get('last_event_at')) or 0,
                           (previous_row or {}).get('recentActivityAt', 0)] +
                          [_timestamp(meta.get(key)) or 0 for key in ('created', 'created_at', 'started_at')]))
            bundle = _text(meta.get('bundle_name')) or _text(meta.get('bundle'))
            if bundle:
                bundle = bundle.removeprefix('bundle:')
                if bundle == 'unknown':
                    bundle = None
            name = _text(meta.get('name')) or _text(meta.get('title')) or _text(meta.get('agent_name'))
            kind, parent = classify_session(directory.name, native, capture)
            rows.append({
                'id': uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{slug}/{directory.name}').hex,
                'nativeIdentity': directory.name, 'nativeProject': slug,
                'name': name, 'title': name or f'Conversation {directory.name[:8]}', 'description': _text(meta.get('description')) or '',
                'bundle': bundle, 'parentId': parent, 'sessionKind': kind,
                **({'sessionPurpose': meta['session_purpose']} if kind == 'internal'
                   and isinstance(meta.get('session_purpose'), str)
                   and re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}', meta['session_purpose']) else {}),
                'createdAt': created, 'updatedAt': updated, 'recentActivityAt': recent, 'turnCount': turns,
                'transcriptAvailable': bool(transcript and transcript[2]),
                'transcriptRevision': list(transcript[1:]) if transcript else None,
                'nameSource': _text(meta.get('name_source')), 'autoName': automatic_metadata(meta),
                'nameRevision': meta['name_revision'] if type(meta.get('name_revision')) is int and meta['name_revision'] >= 0 else 0,
                'namePolicyRevision': meta['name_policy_revision'] if type(meta.get('name_policy_revision')) is int and meta['name_policy_revision'] >= 0 else 0,
            })
        if partial and candidates != {prior['workspace']['path']}:
            # A concurrent metadata save can change workspace resolution even
            # when the observed notification named only a transcript. That
            # legitimately invalidates all summaries in this one project.
            del issues[issue_start:]
            if target_only:
                return None
            return (yield from self._scan_project_steps(project, known, issues, bounded=bounded))
        # A matching slug is necessary, but not sufficient when distinct paths
        # collide (e.g. /a-b/c and /a/b-c). Never silently choose one.
        path = next(iter(candidates)) if len(candidates) == 1 else None
        if path is None:
            issues.append({'kind': 'unresolved-workspace', 'nativeProject': slug})
        workspace_id = uuid.uuid5(uuid.NAMESPACE_URL, path or f'amplifier-project:{slug}').hex
        workspace = {
            'id': workspace_id, 'nativeProject': slug, 'path': path,
            'name': (Path(path).name or path) if path else slug,
            'available': bool(path and Path(path).is_dir()),
            'sessionCount': sum(row['sessionKind'] == 'root' for row in rows),
            'workerSessionCount': sum(row['sessionKind'] == 'worker' for row in rows),
            'internalSessionCount': sum(row['sessionKind'] == 'internal' for row in rows),
        }
        if partial and lazy:
            for field, kind in (('sessionCount', 'root'), ('workerSessionCount', 'worker'),
                                ('internalSessionCount', 'internal')):
                workspace[field] += prior['workspace'].get(field, 0) - sum(
                    bool((previous_rows.get(identity) or {}).get('sessionKind') == kind)
                    for identity in transcript_ids)
        for row in rows:
            if partial and row['nativeIdentity'] not in transcript_ids:
                continue  # Old immutable summaries are not modified in place.
            row.update(workspace=path, workspaceId=workspace_id)
            reason = None
            if row['sessionKind'] == 'internal':
                reason = 'Internal job history is read-only; it is not an ordinary conversation.'
            elif row['sessionKind'] == 'worker':
                reason = 'Worker sessions are read-only; continuing them as root chats would lose their worker configuration.'
            elif not re.fullmatch(r'[A-Za-z0-9-]{1,128}', row['nativeIdentity']):
                reason = 'This legacy session identifier is not supported for shared root execution.'
            elif not row['transcriptAvailable']:
                reason = 'The saved transcript is not available yet.'
            elif not path:
                reason = 'The original workspace folder could not be determined from its saved metadata.'
            elif not workspace['available']:
                reason = 'The original workspace folder is no longer available.'
            elif not row['bundle']:
                reason = 'This session does not record a resumable bundle.'
            row.update(canResume=reason is None, readOnlyReason=reason)
        if not lazy:
            rows = [previous_rows[row['nativeIdentity']] if row == previous_rows.get(row['nativeIdentity']) else row
                    for row in rows]
        if bounded:
            rows.sort(key=lambda row: row['nativeIdentity'])
        if partial and lazy:
            absent = transcript_ids - {row['nativeIdentity'] for row in rows}
            # Diagnostic directories may never have entered the chat catalog.
            # Their continued absence is not a removal or a client change.
            removed = {identity for identity in absent if previous_rows.get(identity) is not None}
            rows = CatalogPatchRows(prior['sessions'], rows, removed)
        return {'workspace': workspace, 'sessions': rows}

    def _safe_project(self, project):
        root = self.home / 'projects'
        try:
            # self.home was resolved at construction. Recheck each direct
            # directory with lstat, without repeatedly resolving every ancestor
            # for every session in every recovery slice.
            root_path, project_path = str(root), str(project)
            if (not stat.S_ISDIR(os.stat(root_path, follow_symlinks=False).st_mode)
                    or project.parent != root
                    or not stat.S_ISDIR(os.stat(project_path, follow_symlinks=False).st_mode)):
                return False
            try:
                return stat.S_ISDIR(os.stat(project_path + os.sep + 'sessions',
                                           follow_symlinks=False).st_mode)
            except FileNotFoundError:
                return True
        except (OSError, ValueError, RuntimeError):
            return False

    @staticmethod
    def _recovery_directories(path):
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    yield (Path(entry.path) if not entry.name.startswith('.')
                           and entry.is_dir(follow_symlinks=False) else None)
        except FileNotFoundError:
            return

    def _recover_projects(self, known):
        """Bounded, resumable canonical discovery; notifications are only hints."""
        root = self.home / 'projects'
        seen = set()
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    # One urgent project per discovery entry: continuous writes
                    # cannot monopolize missed-event coverage for other projects.
                    if self._recovery_urgent:
                        name = self._recovery_urgent.popleft()
                        self._recovery_queued.discard(name)
                        yield from self._recover_project(root / name, known)
                    yield
                    if entry.name.startswith('.') or not entry.is_dir(follow_symlinks=False):
                        continue
                    seen.add(entry.name)
                    yield from self._recover_project(Path(entry.path), known)
            for name in set(self._projects) - seen:
                yield
                # A new project may have been admitted by the watcher after
                # directory iteration started. Check absence before retirement.
                if not (root / name).exists():
                    self._set_recovered_project(name, None)
                    self._project_inputs.pop(name, None)
                    self._project_paths.pop(name, None)
            self._recovery_status.update(phase='complete', completedAt=time.time())
        except (OSError, RuntimeError):
            # An unavailable directory is not proof that retained rows vanished.
            self._recovery_status['phase'] = 'unavailable'

    def _recover_project(self, project, known):
        if self.directory:
            self._directory_recovery_project = project.name
            self._directory_recovery_changes = {}
        try:
            yield from self._recover_project_steps(project, known)
        finally:
            if self.directory:
                self._directory_recovery_project = None
                self._directory_recovery_changes = {}

    def _recover_project_steps(self, project, known):
        yield
        if not project.exists():
            self._set_recovered_project(project.name, None)
            self._project_inputs.pop(project.name, None)
            return
        if not self._safe_project(project):
            value = (None, [{'kind': 'unreadable', 'nativeProject': project.name}])
            self._recovery_issues_changed |= self._project_inputs.get(project.name, (None, []))[1] != value[1]
            self._project_inputs[project.name] = value
            return
        before = self._projects.get(project.name)
        if self.directory and before is not None and before['workspace'].get('path'):
            if (yield from self._recover_cached_project_steps(project, known)):
                return
        cached = self._project_inputs.get(project.name)
        try:
            stamp = yield from self._project_stamp_steps(project, known)
        except (OSError, RuntimeError):
            stamp = None
        unchanged = bool(stamp is not None and cached and cached[0] == stamp)
        if (not self.directory and not unchanged and before is not None and stamp is not None and cached
                and cached[0] and not cached[1] and before['workspace'].get('path')):
            # Automatic discovery registers an already resolved workspace.
            # That registration changes header inputs, not canonical rows.
            # Compare every native file stamp and revalidate all resolved paths;
            # a new ambiguity or substituted symlink must still rebuild.
            path = before['workspace']['path']
            prefix = str(project) + os.sep
            unchanged = (
                set(cached[0][0]) <= set(stamp[0])
                and
                set(stamp[0]) <= {path}
                and all(resolved == path for _, resolved in stamp[1])
                and tuple(value for value in stamp[2:] if value[0].startswith(prefix)) ==
                    tuple(value for value in cached[0][2:] if value[0].startswith(prefix)))
        if (before is not None and stamp is not None and cached
                and unchanged and not cached[1]
                and before['workspace']['available'] ==
                    bool(before['workspace'].get('path') and Path(before['workspace']['path']).is_dir())):
            # No row interpretation changed. Preserve any newer hot update
            # rather than replacing it with this pass's earlier reference.
            self._project_inputs[project.name] = (stamp, cached[1])
            return
        issues = []
        result = yield from self._scan_project_steps(project, known, issues, bounded=True)
        # Another invalidation owns newer published rows when its work completed
        # while this generator was suspended. Do not replace those rows.
        current = self._projects.get(project.name)
        if before is not None and current is None:
            # A confirmed canonical removal supersedes the suspended scan.
            # Recreated storage must enter through a fresh invalidation/recovery.
            return
        if current is not before and result is not None and current is not None:
            merged = {row['nativeIdentity']: row for row in result['sessions']}
            # Accept cold independently reconciled rows while keeping newer
            # watched rows. A hot session cannot starve the rest of its project.
            if self.directory:
                for identity, row in self._directory_recovery_changes.items():
                    if row is not None:
                        merged[identity] = row
                    else:
                        merged.pop(identity, None)
                # This slice's targeted updates precede the recovery step but
                # are committed only after it. They are newer than the saved
                # row journal too, and must win before the single transaction.
                from .native_catalog import CatalogPatchRows
                if isinstance(current['sessions'], CatalogPatchRows):
                    merged.update(current['sessions'].changed)
                    for identity in current['sessions'].removed:
                        merged.pop(identity, None)
            else:
                baseline_rows = {row['nativeIdentity']: row for row in before['sessions']} if before else {}
                current_rows = {row['nativeIdentity']: row for row in current['sessions']}
                for identity in baseline_rows.keys() | current_rows.keys():
                    if current_rows.get(identity) is not baseline_rows.get(identity):
                        if identity in current_rows:
                            merged[identity] = current_rows[identity]
                        else:
                            merged.pop(identity, None)
            workspace_fields = ('path', 'id', 'available')
            if before is not None and any(current['workspace'].get(key) != before['workspace'].get(key)
                                          for key in workspace_fields):
                # Workspace identity changes affect every row's interpretation.
                if project.name not in self._recovery_queued:
                    self._recovery_urgent.append(project.name)
                    self._recovery_queued.add(project.name)
                return
            rows = sorted(merged.values(), key=lambda row: row['nativeIdentity'])
            workspace = dict(result['workspace'])
            workspace.update({field: sum(row['sessionKind'] == kind for row in rows)
                              for field, kind in (('sessionCount', 'root'),
                                                  ('workerSessionCount', 'worker'),
                                                  ('internalSessionCount', 'internal'))})
            result = {'workspace': workspace, 'sessions': rows}
        if result is not None:
            self._set_recovered_project(project.name, before if result == before else result)
            self._recovery_issues_changed |= self._project_inputs.get(project.name, (None, []))[1] != issues
            self._project_inputs[project.name] = (stamp, issues)

    @staticmethod
    def _directory_stamp(directory):
        info = directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise OSError('Native sessions directory is unavailable or substituted')
        return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns

    @staticmethod
    def _canonical_absent(path):
        try:
            path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False  # Permission/IO failures propagate; uncertainty is not absence.

    @staticmethod
    def _bound_recovery_issues(issues, project):
        if len(issues) > 64:
            count = sum(item.get('additionalCount', 1) for item in issues[63:])
            issues[63:] = [{'kind': 'unreadable', 'nativeProject': project, 'additionalCount': count}]

    @staticmethod
    def _cached_source_matches(facts, signatures):
        if facts is None:
            return False
        files = facts['files']
        sources = {'metadata.json': (signatures['metadata.json'], signatures['metadata.json.backup']),
                   'naming.json': signatures['naming.json'],
                   os.path.join('context-intelligence', 'metadata.json'):
                       signatures[os.path.join('context-intelligence', 'metadata.json')]}
        from .native_catalog import tuples
        for name, signature in sources.items():
            cached, valid = files[name]
            if not valid or tuples(cached) != signature:
                return False
        transcript = signatures['transcript.jsonl'] or signatures['transcript.jsonl.backup']
        return (tuples(facts['transcriptRevision']) == (transcript[1:] if transcript else None)
                and facts['transcriptAvailable'] == bool(transcript and transcript[2]))

    def _recover_cached_project_steps(self, project, known):
        """Probe warm membership without restoring unchanged metadata payloads.

        The disposable SQL generation owns the seen set. Python retains only a
        64-ID batch; canonical parsing and cache mutation use the same targeted
        path as watcher changes. Unresolved/new workspace repair remains the
        separate cold recovery path above.
        """
        from .native_catalog import CatalogPatchRows
        slug = project.name
        prior = self._projects[slug]
        workspace = prior['workspace']
        path = workspace['path']
        # A changed registration or path interpretation needs cold canonical
        # resolution. An unchanged saved path is never execution admission.
        if (set(known.get(slug, ())) - {path} or str(Path(path).resolve()) != path
                or project_slug(path) != slug):
            return False
        issues = []
        project_metadata = self._read(project / 'metadata.json', issues, slug)
        candidate = self._working_dir(project_metadata, slug)
        if candidate not in {None, path}:
            return False
        catalog = self._catalog
        token = None
        previous_staged = self._directory_recovery_staged
        self._directory_recovery_staged = True
        try:
            initial_directory = self._directory_stamp(project / 'sessions')
            token = catalog.recovery_begin(slug)
            available = Path(path).is_dir()
            if workspace['available'] != available:
                updated = {**workspace, 'available': available}
                self._set_recovered_project(slug, {'workspace': updated,
                    'sessions': CatalogPatchRows(prior['sessions'], [])})
            batch = []
            for directory in self._recovery_directories(project / 'sessions'):
                yield
                if not self._safe_project(project):
                    raise OSError('Native project changed during recovery')
                if directory is None:
                    continue
                identity = directory.name
                try:
                    self._directory_stamp(directory)
                    signatures = {}
                    for name in _STAMP_FILES:
                        try:
                            signatures[name] = _signature(directory / name)
                        except FileNotFoundError:
                            signatures[name] = None
                except (OSError, ValueError):
                    issues.append({'kind': 'unreadable', 'nativeProject': slug, 'nativeIdentity': identity})
                    self._bound_recovery_issues(issues, slug)
                    signatures = None
                batch.append((identity, signatures))
                if len(batch) == 64:
                    if not (yield from self._recover_cached_batch(project, known, token, batch, issues)):
                        return False
                    batch = []
            if batch and not (yield from self._recover_cached_batch(project, known, token, batch, issues)):
                return False
            if (not self._safe_project(project)
                    or self._directory_stamp(project / 'sessions') != initial_directory):
                issues.append({'kind': 'unstable-project', 'nativeProject': slug})
            current = self._projects.get(slug)
            if current is None:
                return True  # A newer canonical project removal won.
            if current['workspace'].get('path') != path:
                return False
            if not issues:
                # Targeted updates collected earlier in this slice have not yet
                # reached save(); protect them alongside committed watched rows.
                if isinstance(current['sessions'], CatalogPatchRows):
                    changed = iter(current['sessions'].changed)
                    while group := list(itertools.islice(changed, 64)):
                        catalog.recovery_seen(token, group)
                after = ''
                while True:
                    yield
                    if (not self._safe_project(project)
                            or self._directory_stamp(project / 'sessions')[:2] != initial_directory[:2]):
                        raise OSError('Native project changed during recovery')
                    current = self._projects.get(slug)
                    if current is None:
                        break
                    pending = current['sessions'] if isinstance(current['sessions'], CatalogPatchRows) else None
                    if pending:
                        changed = iter(pending.changed)
                        while group := list(itertools.islice(changed, 64)):
                            catalog.recovery_seen(token, group)
                    missing = catalog.recovery_missing(token, after=after, limit=64)
                    if not missing:
                        break
                    # A newly written session can arrive while the generator is
                    # suspended. Probe current membership and physical absence
                    # without yielding between evidence and the patch/counts.
                    removed = [(identity, kind) for identity, kind in missing
                               if not (pending and identity in pending.removed)
                               and self._canonical_absent(project / 'sessions' / identity)]
                    after = missing[-1][0]
                    if removed:
                        updated = dict(current['workspace'])
                        for field, kind in (('sessionCount', 'root'), ('workerSessionCount', 'worker'),
                                            ('internalSessionCount', 'internal')):
                            updated[field] -= sum(row_kind == kind for _, row_kind in removed)
                        self._set_recovered_project(slug, {'workspace': updated,
                            'sessions': CatalogPatchRows(current['sessions'], [], [identity for identity, _ in removed])})
            old_issues = self._project_inputs.get(slug, (None, []))[1]
            self._project_inputs[slug] = ((tuple(sorted(known.get(slug, ()))),), issues)
            self._recovery_issues_changed |= old_issues != issues
            return True
        except (OSError, ValueError, sqlite3.Error):
            old_issues = self._project_inputs.get(slug, (None, []))[1]
            issues.append({'kind': 'unreadable', 'nativeProject': slug})
            self._bound_recovery_issues(issues, slug)
            self._project_inputs[slug] = (None, issues)
            self._recovery_issues_changed |= old_issues != issues
            return True
        finally:
            self._directory_recovery_staged = previous_staged
            if token is not None:
                try:
                    catalog.recovery_abort(token)
                except (OSError, ValueError, sqlite3.Error):
                    pass  # The next project generation replaces stale scratch rows.

    def _recover_cached_batch(self, project, known, token, batch, issues):
        catalog = self._catalog
        identities = [identity for identity, _ in batch]
        catalog.recovery_seen(token, identities)
        cached = catalog.recovery_facts(project.name, identities)
        for identity, signatures in batch:
            if signatures is None or self._cached_source_matches(cached.get(identity), signatures):
                continue
            # A fresh targeted notification may have already replaced this row.
            # Re-read canonical metadata; never publish the batch's older facts.
            yield
            # Capture the current patch after the suspension boundary. Driving
            # this one-ID reader without another yield cannot replace a watched
            # update that arrived while an earlier prior object was captured.
            result = self._scan_project(project, known, issues,
                transcript_ids={identity}, target_only=True)
            self._bound_recovery_issues(issues, project.name)
            if result is None:
                return False  # Workspace ambiguity requires cold resolution.
            self._set_recovered_project(project.name, result)
        return True

    def _set_recovered_project(self, name, result):
        """Copy live membership once only when recovery publishes a change."""
        current = self._projects.get(name)
        if result is current:
            return
        if self._projects is self._scan_original_projects:
            self._projects = dict(self._projects)
        if result is None:
            self._projects.pop(name, None)
        else:
            self._projects[name] = result
        self._recovery_modified.add(name)
        self._recovery_issues_changed = True

    def _recovery_slice(self, known, *, watching=True):
        if self._recovery is None:
            self._recovery = self._recover_projects(known)
            self._recovery_status.update(phase='running', steps=0)
        start = time.monotonic()
        steps = 0
        while steps < self._recovery_budget and time.monotonic() - start < self.RECOVERY_SLICE_SECONDS:
            try:
                next(self._recovery)
                steps += 1
            except StopIteration:
                self._recovery = None
                # A failed pass is not fresh discovery. Retry unavailable
                # storage sooner, while completed cold coverage stays cheap.
                delay = (self.RECONCILE_INTERVAL
                         if watching and self._recovery_status['phase'] == 'complete'
                         else self.UNWATCHED_RECONCILE_INTERVAL)
                self._reconcile_at = time.monotonic() + delay
                break
        self._recovery_status['steps'] = steps

    def scan(self, *, known_workspaces=None, force=False):
        """Refresh changed metadata and return all projects and saved sessions."""
        return self.scan_if_changed(known_workspaces=known_workspaces, force=force)[1]

    def validate_resume(self, source):
        """Fresh admission evidence; cached positive metadata is display only."""
        project, identity = source['nativeProject'], source.get('nativeIdentity') or source['id']
        if (Path(project).name != project or project in {'', '.', '..'}
                or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', identity)):
            raise ValueError('Unsupported native session identity; saved history was preserved.')
        root = self.home / 'projects'
        project_dir = root / project
        directory = project_dir / 'sessions' / identity
        try:
            for path in (project_dir, directory.parent, directory):
                if not stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode):
                    raise ValueError('Native session directory is unavailable or substituted.')
            if not directory.resolve().is_relative_to(root.resolve()):
                raise ValueError('Native session directory is outside shared history.')
            # This independent reader has no persisted or in-memory positive cache.
            verifier = NativeHistory(self.home)
            issues = []
            native = verifier._native_metadata(directory, issues, project)
            capture = verifier._read(directory / 'context-intelligence' / 'metadata.json',
                                     issues, project, identity)
            if issues or not (native or capture):
                raise ValueError('Current native metadata could not be verified; refresh after repair.')
            kind, _ = classify_session(identity, native, capture)
            if kind != 'root':
                raise ValueError('Worker and internal histories cannot be continued as root chats.')
            workspace = source.get('workspace')
            if not workspace or not Path(workspace).is_dir() or project_slug(workspace) != project:
                raise ValueError('Current native workspace could not be verified.')
            for metadata in (native, capture):
                for key in ('working_dir', 'cwd', 'project_dir', 'workspace'):
                    path = metadata.get(key)
                    if isinstance(path, str) and Path(path).is_absolute():
                        if Path(path).expanduser().resolve() != Path(workspace).resolve():
                            raise ValueError('Native workspace metadata changed; refresh before continuing.')
            if not any((directory / name).is_file() and _signature(directory / name)[2]
                       for name in ('transcript.jsonl', 'transcript.jsonl.backup')):
                raise ValueError('The saved native transcript is unavailable.')
        except OSError as exc:
            raise ValueError('Current native files could not be verified; original history was preserved.') from exc

    def scan_changes(self, *, known_workspaces=None, force=False, since=None):
        """Detached changed metadata only; an unknown base returns a reset.

        A failed consumer can retry its old token. The bounded journal retains
        project identities, not mutable copies of the complete library.
        Canonical revalidation still runs before any cached result is returned.
        """
        return self.scan_if_changed(known_workspaces=known_workspaces,
                                    force=force, since=since, _changes=True)

    def scan_if_changed(self, *, known_workspaces=None, force=False, since=None, _changes=False):
        """Return an opaque revision and a detached snapshot, or None if unchanged.

        Discovery, file stamps, watcher recovery and availability checks still run.
        A consumer can retain its own detached snapshot and present its revision
        to avoid sorting/copying the entire library again. Manual refresh always
        returns a snapshot. Revisions belong to this index, not persisted state.
        """
        with self._lock:
            if self._closing.is_set():
                raise InterruptedError('Native discovery is closing')
            self._load_catalog()
            if self._closing.is_set():
                raise InterruptedError('Native discovery is closing')
            if self.directory and _changes and self._catalog is None:
                # A broken disposable directory must not turn background
                # discovery into a growing in-memory copy of canonical history.
                # Retain the last bounded failed slice for retry; selected
                # canonical readers and explicit complete scan stay independent.
                self._reads = 0
                self._recovery_status['steps'] = 0
                if self._totals is None:
                    self._totals = {field: sum(project['workspace'].get(field, 0)
                                              for project in self._projects.values())
                                    for field in ('sessionCount', 'workerSessionCount', 'internalSessionCount')}
                token, result = self._directory_result(since, force, self._projects, set(),
                    [{'kind': 'unavailable-catalog-cache'}], False, False)
                result['reconciliation'].update(phase='unavailable', steps=0)
                return token, result
            previous_projects = self._projects
            self._scan_original_projects = previous_projects
            self._recovery_modified = set()
            self._recovery_issues_changed = False
            self._recovery_status['steps'] = 0
            self._reads = 0
            self._working_dirs = {}
            self._working_dir_slugs = {}
            watching, dirty = self._invalidations()
            now = time.monotonic()
            if not watching:
                # A lost watch must shorten a healthy-watch deadline, never
                # continually postpone fallback discovery while it is absent.
                self._reconcile_at = min(self._reconcile_at,
                                         now + self.UNWATCHED_RECONCILE_INTERVAL)
            reconcile = force or now >= self._reconcile_at
            if reconcile:
                self._reconcile_at = now + (self.RECONCILE_INTERVAL if watching
                                            else self.UNWATCHED_RECONCILE_INTERVAL)
            issues = [{'kind': 'unavailable-catalog-cache'}] if self._cache_error else []
            inputs = list(self.known_workspaces if known_workspaces is None else known_workspaces)
            signature = self._known_signature(inputs)
            if not force and signature == self._known_input:
                known = self._known_paths
            else:
                known = {}
                for item in inputs:
                    value = item.get('path') if isinstance(item, dict) else item
                    if not isinstance(value, (str, Path)) or not Path(value).expanduser().is_absolute():
                        continue
                    path = str(Path(value).expanduser().resolve())
                    known.setdefault(project_slug(path), set()).add(path)
                self._known_paths = known
            self._known_input = signature
            dirty_projects = {item[0] if isinstance(item, tuple) else item for item in dirty}
            wildcard = '*' in dirty_projects
            # Missing caches and unavailable watches still use bounded
            # discovery. Neither condition admits an eager cold-library scan.
            incremental = _changes and (self.directory or (self._watch_enabled and not force))
            root_issue = None
            if incremental:
                # The previous detached journal retains old project objects.
                # Discovery and missed-notification coverage advance separately.
                dirty = set(dirty) | self._pending_invalidations
                session_dirty = sorted(item for item in dirty if isinstance(item, tuple))
                admitted = set(session_dirty[:64])
                self._pending_invalidations = set(session_dirty[64:])
                for name in sorted(item for item in dirty if isinstance(item, str) and item != '*'):
                    if name not in self._recovery_queued and name not in {'', '.', '..'} and Path(name).name == name:
                        self._recovery_urgent.append(name)
                        self._recovery_queued.add(name)
                dirty = admitted
                dirty_projects = {item[0] for item in dirty}
                projects = [self.home / 'projects' / name for name in sorted(dirty_projects)
                            if name not in {'', '.', '..'} and Path(name).name == name]
                # Watch loss must not leave an earlier complete generation
                # advertised as fresh until the next periodic sweep. This is
                # one root probe, not a directory or session enumeration.
                try:
                    if not stat.S_ISDIR((self.home / 'projects').stat(follow_symlinks=False).st_mode):
                        raise OSError('Native history root is substituted')
                except FileNotFoundError:
                    if self._projects:
                        root_issue = 'unavailable-root'
                except OSError:
                    root_issue = 'unreadable-root'
                if root_issue:
                    projects = None
                    if self._recovery is not None:
                        self._recovery.close()
                        self._recovery = None
                    self._recovery_status.update(phase='unavailable', steps=0)
                    self._reconcile_at = now  # Resume bounded recovery after repair.
            else:
                projects = None
            try:
                if not incremental:
                    projects = self._directories(self.home / 'projects')
            except FileNotFoundError:
                projects = None if self._projects else []
                if self._projects:
                    issues.append({'kind': 'unavailable-root'})
            except OSError:
                projects = None
                issues.append({'kind': 'unreadable-root'})
            if projects is not None and (projects or not incremental):
                current = dict(self._projects) if incremental else {}
                for project in projects:
                    if incremental and not project.exists():
                        # Verify at the canonical root; a missing entry is not
                        # discovered by dropping every unvisited cached project.
                        current.pop(project.name, None)
                        continue
                    if not self._safe_project(project):
                        if project.name in self._projects:
                            current[project.name] = self._projects[project.name]
                        self._project_inputs[project.name] = (None, [
                            {'kind': 'unreadable', 'nativeProject': project.name}])
                        continue
                    cached = self._project_inputs.get(project.name)
                    workspace = self._projects.get(project.name, {}).get('workspace', {})
                    available = bool(workspace.get('path') and Path(workspace['path']).is_dir())
                    known_paths = tuple(sorted(known.get(project.name, ())))
                    if watching and not reconcile and '*' not in dirty_projects and project.name not in dirty_projects and cached and cached[0] and cached[0][0] == known_paths and workspace and workspace['available'] == available:
                        current[project.name] = self._projects[project.name]
                        issues.extend(cached[1])
                        continue
                    transcript_ids = ({item[1] for item in dirty if isinstance(item, tuple)
                                       and len(item) == 3 and item[0] == project.name and item[2] in {'transcript', 'metadata'}}
                                      if watching and (incremental or not reconcile) and '*' not in dirty_projects
                                      and project.name not in dirty else set())
                    partial = bool(transcript_ids and workspace.get('path')
                                   and workspace.get('available') == available
                                   and ((self.directory and cached is None) or
                                        (cached and cached[0] and cached[0][0] == known_paths and not cached[1])))
                    if incremental and not partial:
                        if project.name not in self._recovery_queued:
                            self._recovery_urgent.append(project.name)
                            self._recovery_queued.add(project.name)
                        continue
                    try:
                        stamp = (known_paths,) if partial else self._project_stamp(project, known)
                    except (OSError, RuntimeError):
                        stamp = None
                    if not partial and stamp is not None and cached and cached[0] == stamp and workspace and workspace['available'] == available:
                        current[project.name] = self._projects[project.name]
                        issues.extend(cached[1])
                        continue
                    start = len(issues)
                    result = (self._scan_project(project, known, issues, transcript_ids=transcript_ids,
                                                 target_only=incremental)
                              if partial else self._scan_project(project, known, issues))
                    if result is None and incremental and project.name not in self._recovery_queued:
                        self._recovery_urgent.append(project.name)
                        self._recovery_queued.add(project.name)
                    if result is not None:
                        previous = self._projects.get(project.name)
                        # Unreadable or unstable stamps can require a rebuild
                        # whose visible metadata is unchanged. Retain its prior
                        # identity only after comparing the complete result.
                        current[project.name] = previous if result == previous else result
                        self._project_inputs[project.name] = (stamp, issues[start:])
                self._projects = current
                existing = set(current)
                self._project_inputs = {key:value for key,value in self._project_inputs.items() if key in existing}
                self._project_paths = {key:value for key,value in self._project_paths.items() if key in existing}
                # File ownership changes only when a project disappears. Avoid
                # parsing tens of thousands of unchanged paths at every timer
                # reconciliation; metadata stamps and discovery still run.
                if self._file_projects - existing:
                    self._files = {path: value for path, value in self._files.items()
                                   if path.relative_to(self.home / 'projects').parts[0] in existing}
                    self._file_projects.intersection_update(existing)
            if incremental and not root_issue and (reconcile or wildcard or self._recovery is not None or self._recovery_urgent):
                self._recovery_slice(known, watching=watching)
                reconcile = False  # A slice is not a completed full-library merge.
            if incremental:
                if projects or self._recovery_issues_changed:
                    issues = ([{'kind': 'unavailable-catalog-cache'}] if self._cache_error else [])
                    issues.extend(issue for _, project_issues in self._project_inputs.values()
                                  for issue in project_issues)
                else:
                    issues = ([{'kind': 'unavailable-catalog-cache'}] if self._cache_error else [])
                    issues.extend(issue for issue in self._snapshot_issues
                                  if issue.get('kind') not in {'unavailable-catalog-cache', 'unavailable-root', 'unreadable-root'})
                if root_issue:
                    issues.append({'kind': root_issue})
            candidates = ((dirty_projects | self._recovery_modified) if incremental else
                          self._projects.keys() | previous_projects.keys())
            changed_projects = {key for key in candidates
                                if self._projects.get(key) is not previous_projects.get(key)}
            count_fields = ('sessionCount', 'workerSessionCount', 'internalSessionCount')
            if self._totals is None:
                self._totals = {field: sum(project['workspace'].get(field, 0)
                                          for project in self._projects.values())
                                for field in count_fields}
            else:
                for name in changed_projects:
                    before = previous_projects.get(name, {}).get('workspace', {})
                    after = self._projects.get(name, {}).get('workspace', {})
                    for field in count_fields:
                        self._totals[field] += after.get(field, 0) - before.get(field, 0)
            if self._catalog is not None and (changed_projects or self._dirty_files):
                try:
                    saved = self._catalog.save(
                        {key: self._projects[key] for key in changed_projects if key in self._projects},
                        {key: previous_projects[key] for key in changed_projects if key in previous_projects},
                        self._files, self._dirty_files)
                    self._dirty_files.clear()
                    if self.directory:
                        self._directory_record(saved)
                        effective = {row['nativeProject'] for row in (saved or {}).get('sessions', [])}
                        effective.update(name for name, _ in (saved or {}).get('removed', []))
                        effective.update(name for name in changed_projects
                            if self._projects.get(name, {}).get('workspace') != previous_projects.get(name, {}).get('workspace'))
                        for name in changed_projects - effective:
                            if name in previous_projects:
                                self._projects[name] = previous_projects[name]
                        changed_projects = effective
                        self._directory_compact(changed_projects)
                        self._directory_unsaved.difference_update(changed_projects)
                except Exception as exc:
                    import sqlite3
                    if not isinstance(exc, (OSError, ValueError, sqlite3.Error)):
                        raise
                    self._catalog = None
                    if self.directory:
                        self._directory_unsaved.update(changed_projects)
                    self._cache_error = True
                    self._cache_retry_at = time.monotonic() + self._cache_retry_delay
                    issues.append({'kind': 'unavailable-catalog-cache'})
            self._trim_files()
            if self.directory and _changes:
                return self._directory_result(since, force, previous_projects, changed_projects,
                                              issues, watching, reconcile)
            projects = (tuple(self._projects.items()) if changed_projects or self._snapshot_revision is None
                        else self._snapshot_projects)
            if (self._snapshot_revision is None
                    or changed_projects
                    or issues != self._snapshot_issues):
                # Hold the project references, not only their ids: rebuilt rows
                # must never compare unchanged after an old object is collected.
                if self._snapshot_revision is not None:
                    self._change_bases[self._snapshot_revision] = self._snapshot_projects
                    while len(self._change_bases) > 4:
                        self._change_bases.popitem(last=False)
                self._snapshot_projects = projects
                self._snapshot_issues = copy.deepcopy(issues)
                self._snapshot_revision = object()
            if _changes:
                reset = force or (since is not self._snapshot_revision and since not in self._change_bases)
                unchanged = since is self._snapshot_revision and not force
                old = {} if reset or unchanged else dict(self._change_bases.get(since, projects))
                upserts, workspaces, removed = [], [], []
                for name, project in (() if unchanged else projects):
                    prior = old.get(name)
                    if project is prior:
                        continue
                    if prior is None or prior['workspace'] != project['workspace']:
                        workspaces.append(project['workspace'])
                    old_rows = {row['nativeIdentity']: row for row in prior['sessions']} if prior else {}
                    new_rows = {row['nativeIdentity']: row for row in project['sessions']}
                    upserts.extend(row for key, row in new_rows.items() if row != old_rows.get(key))
                    removed.extend((name, key) for key in old_rows.keys() - new_rows.keys())
                removed_projects = old.keys() - self._projects.keys()
                removed.extend((name, row['nativeIdentity']) for name in removed_projects for row in old[name]['sessions'])
                changes = copy.deepcopy({
                    'reset': reset, 'workspaces': workspaces, 'sessions': upserts,
                    'removed': removed, 'removedProjects': sorted(removed_projects),
                    'issues': issues, 'projectCount': len(projects), **self._totals,
                    'metadataReads': self._reads, 'reconciled': reconcile,
                    'notificationsReady': watching})
                changes['reconciliation'] = copy.deepcopy(self._recovery_status)
                changes['base'] = since
                return self._snapshot_revision, changes
            if not force and since is self._snapshot_revision:
                return self._snapshot_revision, None
            workspaces = [project['workspace'] for project in self._projects.values()]
            sessions = [row for project in self._projects.values() for row in project['sessions']]
            sessions.sort(key=lambda row: (row['updatedAt'], row['id']), reverse=True)
            return self._snapshot_revision, copy.deepcopy({'workspaces': workspaces, 'sessions': sessions,
                                  'sessionCount': sum(row['sessionKind'] == 'root' for row in sessions),
                                  'workerSessionCount': sum(row['sessionKind'] == 'worker' for row in sessions),
                                  'internalSessionCount': sum(row['sessionKind'] == 'internal' for row in sessions),
                                  'issues': issues, 'metadataReads': self._reads})
