"""CLI transcript/metadata compatibility without importing the CLI host.

Production stores share project session directories. Explicit base_dir stores
remain available for reading/migrating pre-0.8 Unified checkpoints.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import tempfile
import time

from amplifier_foundation.session.history import SessionHistoryStore

_SECRET_KEYS = {"api_key", "apikey", "access_token", "refresh_token", "secret", "password", "authorization", "client_secret"}


def _metadata(value):
    """Persist routing/config, never literal credential fields."""
    if isinstance(value, dict):
        return {key: _metadata(item) for key, item in value.items()
                if key.lower() not in _SECRET_KEYS or item == {"$hostProviderCredential": True}}
    if isinstance(value, list):
        return [_metadata(item) for item in value]
    return value


class SessionStore:
    def __init__(self, base_dir=None, *, legacy_home=None, shared=False, previous=None):
        self.base_dir = Path(base_dir or Path.home() / ".amplifier-unified" / "sessions").expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.legacy_home = Path(legacy_home or os.environ.get("AMPLIFIER_UNIFIED_IMPORT_HOME", Path.home() / ".amplifier")).expanduser()
        self.shared = shared
        self.previous = Path(previous) if previous else None

    @classmethod
    def for_app(cls, home, workspace):
        from ..session_files import sessions_dir, amplifier_home
        return cls(sessions_dir(workspace), legacy_home=amplifier_home(), shared=True,
                   previous=Path(home) / 'sessions')

    @classmethod
    def find(cls, home, identity, default_workspace):
        """Locate a native session without making another host-owned copy."""
        from ..session_files import amplifier_home, validate_id
        validate_id(identity)
        candidates = sorted({path.parent for name in ('transcript.jsonl', 'transcript.jsonl.backup')
                             for path in (amplifier_home() / 'projects').glob(f'*/sessions/{identity}/{name}')})
        if len(candidates) > 1:
            raise ValueError('This session ID exists in more than one workspace. Open it through shared workspace history.')
        if candidates:
            return cls(candidates[0].parent, shared=True, legacy_home=amplifier_home()).load(identity)
        legacy = cls(Path(home) / 'sessions').load(identity)
        if legacy:
            workspace = legacy[1].get('working_dir') or default_workspace
            return cls.for_app(home, workspace).load(identity) or legacy
        return None

    def _migrate(self, session_id):
        """Publish a missing legacy transcript once; never overwrite CLI work.

        Originals stay in place for rollback. The shared transcript wins from
        this point forward, including changes made subsequently by the CLI.
        """
        if not self.previous:
            return
        directory = self.directory(session_id)
        source = self.previous / session_id
        if self.has_transcript(session_id) or not (source / 'checkpoint.json').is_file():
            return
        old = SessionStore(self.previous).load(session_id)
        if old is None:
            return
        # Exclusive directory creation prevents a competing migration from
        # overwriting a newer transcript. Existing shared directories may already
        # contain CI events or naming before the first transcript checkpoint.
        from filelock import FileLock
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(directory / '.unified-migration.lock')):
            if self.has_transcript(session_id):
                return
            self.save(session_id, *old, preserve_system=True)
            # Job evidence is never replayed, and process ownership is not copied.
            for original in (source / 'live-jobs').glob('job-*.json'):
                target = directory / 'live-jobs' / original.name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    self._atomic(target, original.read_text())

    def directory(self, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}", session_id):
            raise ValueError("Invalid session identifier")
        path = self.base_dir / session_id
        if path.is_symlink():
            raise ValueError("Session directories cannot be symbolic links")
        return path

    @staticmethod
    def _atomic(path, text):
        fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
        failure = None
        try:
            binary = isinstance(text, bytes)
            with os.fdopen(fd, "wb" if binary else "w", encoding=None if binary else "utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            # Brief Windows reader pins can deny rename sharing. Retry only
            # that definite refusal, with the same fully flushed temporary.
            # Five attempts add at most 300ms of delay; all other errors and
            # persistent contention retain the existing failure/cleanup path.
            for attempt in range(5):
                try:
                    os.replace(temporary, path)
                    break
                except OSError as error:
                    if os.name != "nt" or getattr(error, "winerror", None) != 32 or attempt == 4:
                        raise
                    time.sleep(0.02 * 2 ** attempt)
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                if failure is None:
                    raise
                failure.add_note("Temporary cleanup failed; an unpublished temporary may remain.")

    def history(self, session_id, *, events_path=None):
        return SessionHistoryStore(self.directory(session_id), events_path=events_path,
                                   session_id=session_id)

    def has_transcript(self, session_id):
        directory = self.directory(session_id)
        return any((directory / name).exists() for name in
                   ("transcript.jsonl", "transcript.jsonl.backup"))

    def save(self, session_id, messages, metadata, *, preserve_system=False):
        """Synchronously persist owned context; returned rows borrow its values.

        Foundation validates and encodes without mutating messages. Callers keep
        execution ownership through this call; no detached history snapshot is
        needed for the immediately consumed checkpoint anchors.
        """
        history = self.history(session_id)
        rows = [m if isinstance(m, dict) else m.model_dump() for m in messages]
        from ..naming import adopt, initial_name
        previous = history.load_metadata()
        saved_metadata = _metadata({**previous, **copy.deepcopy(metadata or {})})
        # Runtime checkpoints may carry an old naming policy. Exclude it from
        # the incoming patch so Foundation merges the latest fields under its
        # metadata lock, including changes from another host during this save.
        for key in ('name_auto', 'name_auto_revision', 'name_policy_revision', 'naming_completed_inputs', 'naming_refinement'):
            saved_metadata.pop(key, None)
        initial_policy = None
        if not previous.get('name'):
            title, source, legacy, view = initial_name(history.session_dir)
            if isinstance(title, str) and title.strip():
                saved_metadata.update(name=title.strip()[:200], name_source=source)
                if 'autoName' in view:
                    initial_policy = bool(view['autoName'])
                description = legacy.get('description') or view.get('description')
                if isinstance(description, str) and description.strip():
                    saved_metadata.setdefault('description', description)
        saved_metadata.update({"session_id": session_id, "updated_at": time.time(), "host": "amplifier-unified"})
        if saved_metadata.get('bundle_name'):
            saved_metadata['bundle'] = saved_metadata['bundle_name']
        keep_system = preserve_system or bool(saved_metadata.get('preserve_system'))
        # Preparing a saved session checkpoints its unchanged context. Keep the
        # transcript's activity timestamp stable for that metadata-only save.
        # Worker generations can retain an older Foundation across an app
        # update. Use its portable writer until that generation is requalified;
        # new generations opt into intent-aware incremental persistence.
        canonical = [row for row in rows if keep_system or not isinstance(row, dict) or row.get('role') not in {'system', 'developer'}]
        history.save(rows, saved_metadata,
                     preserve_system=keep_system, merge_metadata=True,
                     **({'incremental': True} if hasattr(history, 'indexed_messages') else {}))
        adopt(history.session_dir)
        self._initial_naming_policy(history.session_dir, initial_policy)
        return canonical

    @staticmethod
    def _initial_naming_policy(directory, enabled):
        if enabled is None:
            return
        from amplifier_foundation.session.metadata import SessionMetadataStore, metadata_lock
        store = SessionMetadataStore(directory)
        with metadata_lock(directory):
            current = store.read()
            if 'name_auto' not in current:
                current.update(name_auto=enabled, name_auto_revision=current.get('name_revision', 0), name_policy_revision=1)
                store.history._save_metadata_unlocked(current)

    def load(self, session_id):
        """Read native history first, without migrating or writing while browsing."""
        directory = self.directory(session_id)
        from ..naming import read
        if self.has_transcript(session_id):
            value = self.history(session_id).load(include_events=False)
            changed = {item.source for item in value.diagnostics if item.code == 'changed_during_read'}
            # A title/status writer can replace metadata during a long history
            # read. Re-read once, but never retry a changing transcript or use
            # the inconsistent first snapshot. Edit callers still validate
            # their expected transcript revision before and after this load.
            if changed and changed <= {'metadata', 'metadata_backup'}:
                value = self.history(session_id).load(include_events=False)
            if any(item.code == 'changed_during_read' for item in value.diagnostics):
                raise ValueError('The saved session changed while it was being read.')
            return value.messages, {**value.metadata, **read(directory)}
        # Explicit legacy fallback only: once a native transcript exists, even
        # an empty one, this private file can never override it.
        path = directory / "checkpoint.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("messages"), list) or not isinstance(payload.get("metadata"), dict):
            raise ValueError("Unsupported or corrupted session checkpoint")
        if any(not isinstance(row, dict) or not isinstance(row.get("role"), str) for row in payload["messages"]):
            raise ValueError("Malformed legacy session checkpoint")
        return payload["messages"], {**payload["metadata"], **read(directory)}

    def import_cli(self, session_id, workspace=None):
        """Copy one selected legacy transcript. Never execute or mutate source records."""
        existing = self.load(session_id)
        if existing is not None:
            return existing
        if self.shared:
            # A session in another workspace must be opened there, not copied
            # into this workspace while keeping the same root identity.
            return None
        self.directory(session_id)  # Validate before interpolating a glob.
        candidates = []
        if workspace:
            slug = re.sub(r"[^a-zA-Z0-9]", "-", str(Path(workspace).expanduser().resolve()))
            candidates.append(self.legacy_home / "projects" / slug / "sessions" / session_id)
        candidates.append(self.legacy_home / "sessions" / session_id)
        projects = self.legacy_home / "projects"
        if projects.is_dir():
            candidates.extend(projects.glob(f"*/sessions/{session_id}"))
        seen = set()
        for source in candidates:
            if source in seen:
                continue
            seen.add(source)
            transcript = source / "transcript.jsonl"
            if not transcript.is_file() and not transcript.with_suffix(".jsonl.backup").is_file():
                continue
            history = SessionHistoryStore(source, session_id=session_id).load(include_events=False)
            rows, metadata = history.messages, history.metadata
            # Checkpoints are conversation evidence only. Pending/unfinished tool
            # calls remain historical; loop-live receives no imported job tasks.
            metadata["legacy_import"] = {"source": str(source), "imported_at": time.time(), "read_only_source": True, "jobs_replayed": False}
            metadata["interrupted_jobs"] = True
            # Carry evidence, not process ownership. The host's JobStore.recover
            # marks any pending dispatch interrupted before mounting execution.
            destination = self.directory(session_id) / "live-jobs"
            for original in (source / "live-jobs").glob("job-*.json"):
                value = json.loads(original.read_text(encoding="utf-8"))
                if value.get("version") != 1 or not isinstance(value.get("call_id"), str):
                    raise ValueError("Legacy job ledger contains an invalid record")
                destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._atomic(destination / original.name, json.dumps(value, ensure_ascii=False))
            self.save(session_id, rows, metadata)
            return self.load(session_id)
        return None
