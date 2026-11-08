"""Bounded asynchronous, best-effort discovery writes, separate from execution."""
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from .schema import MAX_BYTES, MAX_SESSIONS, SCHEMA_VERSION, name, snapshot


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
        raise OSError("Status directory is not owned by this user")
    if os.name != "nt" and info.st_mode & 0o077:
        raise OSError("Status directory must be private (0700)")


def atomic(path, value):
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_BYTES:
        raise ValueError("Status snapshot exceeds its byte limit")
    fd, temporary = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Publisher:
    """One bounded writer per producer instance, no filesystem work in submit().

    Close is bounded even if a filesystem stalls. Failure means snapshots become
    stale; it never becomes a model/tool failure and never schedules a replay.
    """
    def __init__(self, directory, *, host, version, instance_id=None, include_text=False,
                 heartbeat=2.0, stale_after=10.0, coalesce=.1, max_sessions=MAX_SESSIONS):
        self.root = Path(directory).expanduser()
        self.instance_id = instance_id or uuid.uuid4().hex
        # Host supplied IDs never become paths.
        self.directory = self.root / hashlib.sha256(self.instance_id.encode()).hexdigest()[:32]
        self.producer = {"instanceId": self.instance_id, "host": name(host), "version": name(version),
                         "pid": os.getpid(), "hostname": socket.gethostname()[:200]}
        self.include_text = include_text
        self.heartbeat = max(.05, float(heartbeat))
        self.heartbeat = min(10, self.heartbeat)
        self.stale_after = min(60, max(self.heartbeat * 3, float(stale_after)))
        self.coalesce = max(.01, float(coalesce))
        self.max_sessions = min(MAX_SESSIONS, max(1, int(max_sessions)))
        self._rows, self._dirty, self._ended = {}, set(), set()
        self._retained = OrderedDict()
        self._suspended = set()
        self.adapter_coverage = {}
        self._condition = threading.Condition()
        self._closing = False
        self.failures = 0
        self.writes = 0
        self.omitted = 0
        self._thread = threading.Thread(target=self._run, name="amplifier-status", daemon=True)
        self._thread.start()

    def submit(self, session_id, value):
        try:
            if not isinstance(session_id, str) or not 0 < len(session_id) <= 200:
                return False
            clean = snapshot(value, include_text=self.include_text)
            clean["where"]["sessionId"] = session_id
            with self._condition:
                if self._closing or session_id in self._ended:
                    return False
                if session_id not in self._rows and len(self._rows) >= self.max_sessions:
                    self.omitted += 1
                    return False  # never evict a live entry and pretend it ended
                if self._rows.get(session_id) != clean:
                    self._rows[session_id] = clean
                    self._retained.pop(session_id, None)
                    self._dirty.add(session_id)
                    self._condition.notify()
            return True
        except (TypeError, ValueError, AttributeError):
            return False

    def end(self, session_id):
        with self._condition:
            if session_id in self._rows:
                self._ended.add(session_id)
                self._dirty.add(session_id)
                self._condition.notify()

    def suspend(self, session_id):
        """Stop observing a capacity-evicted session; never claim its work ended."""
        with self._condition:
            if session_id in self._rows:
                self._suspended.add(session_id)
                self._ended.add(session_id)
                self._dirty.add(session_id)
                self._condition.notify()

    def close(self, timeout=2.0):
        with self._condition:
            self._closing = True
            self._ended.update(self._rows)
            self._dirty.update(self._rows)
            self._condition.notify()
        self._thread.join(timeout=timeout)

    def _run(self):
        next_heartbeat = time.monotonic()
        sequence = 0
        while True:
            with self._condition:
                delay = max(0, next_heartbeat - time.monotonic())
                if not self._dirty and not self._closing:
                    self._condition.wait(timeout=delay)
                if self._dirty and not self._closing:
                    deadline = time.monotonic() + self.coalesce
                    while not self._closing and time.monotonic() < deadline:
                        self._condition.wait(timeout=max(0, deadline - time.monotonic()))
                heartbeat = time.monotonic() >= next_heartbeat
                # Ended rows remain here only until their final write succeeds.
                # Retry them on the existing heartbeat, never a dirty-work loop.
                ids = set(self._rows) if heartbeat else set()
                ids.update(self._dirty)
                values = [(key, self._rows[key], key in self._ended) for key in ids]
                self._dirty.clear()
                closing = self._closing
                if heartbeat:
                    next_heartbeat = time.monotonic() + self.heartbeat
            for key, value, ended in values:
                try:
                    private_directory(self.root)
                    private_directory(self.directory)
                    sequence += 1
                    record = {"schemaVersion": SCHEMA_VERSION, "producer": self.producer,
                              "sequence": sequence, "observedAt": time.time(),
                              "staleAfterSeconds": self.stale_after,
                              "lifecycle": ("unobserved" if key in self._suspended else "ended") if ended else "live", **value}
                    record["coverage"] = {"scope": "observed_sessions", "maxLiveSessions": self.max_sessions,
                                          "omittedSubmissions": self.omitted, "complete": self.omitted == 0,
                                          "maxRetainedEndedSessions": self.max_sessions,
                                          "adapter": dict(self.adapter_coverage)}
                    if self.adapter_coverage.get("complete") is False:
                        record["coverage"]["complete"] = False
                    path = self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")
                    atomic(path, record)
                    self.writes += 1
                    if ended and not closing:
                        with self._condition:
                            self._rows.pop(key, None)
                            self._ended.discard(key)
                            self._suspended.discard(key)
                            self._retained[key] = path
                            expired = []
                            while len(self._retained) > self.max_sessions:
                                expired.append(self._retained.popitem(last=False)[1])
                        for old in expired:
                            old.unlink(missing_ok=True)
                except (OSError, ValueError, TypeError):
                    self.failures += 1
            if closing:
                return
