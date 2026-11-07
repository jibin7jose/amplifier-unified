"""Static release snapshots and explicit local lifecycle operations.

Only the Python standard library is used. The source directory must already be
built; no source code, build command, or deployment command is executed here.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import stat
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from . import retention as release_retention


class PublishingError(ValueError):
    """A stable public error code, with its durable receipt when available."""

    def __init__(self, code: str, message: str, *, receipt: dict | None = None):
        super().__init__(message)
        self.code = code
        self.receipt = receipt


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value):
        raise PublishingError("invalid_argument", f"{name} must be a bounded plain identifier")
    return value


def _safe_parts(path, *, allow_sentinel=False):
    if allow_sentinel and path == ".nojekyll":
        return [path]
    parts = path.split("/")
    if not parts or any(not p or p.startswith(".") or "\\" in p or any(ord(c) < 32 for c in p) for p in parts):
        raise PublishingError("unsafe_path", "Hidden, traversal, and ambiguous paths are not publishable")
    if len(parts) > 20 or len(path.encode()) > 1024:
        raise PublishingError("unsafe_path", "Release path exceeds the supported bounds")
    return parts


def _open_directory(path):
    """Open every absolute component without following a symlink, including parents."""
    path = Path(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_file(root, path, expected_size):
    parts = _safe_parts(path, allow_sentinel=True)
    fd = _open_directory(root)
    try:
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
                raise PublishingError("integrity_error", "Release file does not match its manifest")
            data = stream.read(expected_size + 1)
        if len(data) != expected_size:
            raise PublishingError("integrity_error", "Release file does not match its manifest")
        return data
    finally:
        os.close(fd)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, release, content_root, bind_host, owner):
        self.owner = owner
        self._stopped = False
        self.release = release
        self.content_root = content_root
        self._connections = set()
        self._connections_lock = threading.Lock()
        super().__init__((bind_host, 0), _Handler)
        self.authority = f"{bind_host}:{self.server_port}"
        self.url = f"http://{self.authority}/"
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.server_close()
            raise

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        with self._connections_lock:
            self._connections.add(connection)
        return connection, address

    def close_request(self, request):
        with self._connections_lock:
            self._connections.discard(request)
        super().close_request(request)

    def stop(self):
        with self.owner._readers_idle:
            self._stopped = True
        if self.thread.is_alive():
            self.shutdown()
        self.server_close()
        with self._connections_lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.thread.join(timeout=2)


class _Handler(BaseHTTPRequestHandler):
    server_version = "StaticRelease"
    sys_version = ""

    def handle(self):
        try:
            super().handle()
        except OSError:
            # Client disconnects and explicit stop are normal lifecycle events.
            pass

    def log_message(self, *_args):
        # Do not persist user paths, queries, or referrers in an access log.
        pass

    def send_error(self, code, message=None, explain=None):
        # Keep parse/method errors under the same MIME and privacy policy.
        self._respond(code, b"Request rejected", head=getattr(self, "command", None) == "HEAD")

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _respond(self, status, body=b"", content_type="text/plain; charset=utf-8", *, head=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _serve(self, *, head):
        # Only the exact numeric loopback authority is accepted. In particular,
        # arbitrary domains resolving to loopback cannot rebind onto this origin.
        if self.headers.get_all("Host", []) != [self.server.authority]:
            self._respond(421, b"Invalid host", head=head)
            return
        try:
            target = urlsplit(self.path)
            if target.scheme or target.netloc or not target.path.startswith("/"):
                raise PublishingError("unsafe_path", "Invalid request target")
            decoded = unquote(target.path, errors="strict")
            if "\x00" in decoded:
                raise PublishingError("unsafe_path", "Invalid request target")
            path = decoded[1:]
            if not path or path.endswith("/"):
                path += "index.html"
            _safe_parts(path)
            with self.server.owner._body_reader(self.server) as release:
                entry = next((item for item in release["files"] if item["path"] == path), None)
                if entry is None:
                    self._respond(404, b"Not found", head=head)
                    return
                root = self.server.content_root / release["id"] / "files"
                data = _read_file(root, path, entry["size"])
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise PublishingError("integrity_error", "Release file does not match its immutable manifest")
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            # Executable source extensions are never served as HTML by guessing.
            self._respond(200, data, content_type, head=head)
        except (ValueError, UnicodeError):
            self._respond(404, b"Not found", head=head)
        except OSError:
            self._respond(503, b"Release unavailable", head=head)


class Publisher:
    """Manage private static releases, reviews, receipts and process-local listeners.

    A root has one active owner. Calls are serialized and can run in worker
    threads. On restart, old URLs are historical only; listeners are never
    recreated. Use an explicit new operation to resume a site.
    """

    def __init__(self, root, *, bind_host="127.0.0.1", max_files=2000, max_file_bytes=20 * 1024 * 1024, max_total_bytes=100 * 1024 * 1024):
        try:
            address = ipaddress.IPv4Address(bind_host)
        except (ipaddress.AddressValueError, TypeError) as exc:
            raise PublishingError("unsafe_bind", "Bind address must be an explicit numeric loopback or RFC1918 IPv4 address") from exc
        networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        if str(address) != bind_host or (bind_host != "127.0.0.1" and not any(address in ipaddress.IPv4Network(network) for network in networks)):
            raise PublishingError("unsafe_bind", "Public, wildcard and non-RFC1918 bind addresses are not supported")
        self._bind_host = bind_host
        self._access_policy = "loopback-only" if bind_host == "127.0.0.1" else "private-network"
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Runtime storage is a host-controlled directory, never a build input.
        if self.root.is_symlink():
            raise PublishingError("unsafe_root", "Publishing storage cannot be a symlink")
        self.root = self.root.resolve()
        self._mutex = threading.RLock()
        self._idle = threading.Condition(self._mutex)
        self._readers_idle = threading.Condition(threading.RLock())
        self._body_reads = {}
        self._released_reads = {}
        self._retirement_workers = set()
        self._closing = False
        self._closed = False
        self._sites = {}
        self._previews = {}
        self._limits = (max_files, max_file_bytes, max_total_bytes)
        if any(type(n) is not int or n <= 0 for n in self._limits):
            raise PublishingError("invalid_argument", "Publishing limits must be positive integers")
        self._lock_file = open(self.root / "owner.lock", "a+b")
        try:
            import fcntl
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError) as exc:
            self._lock_file.close()
            raise PublishingError("root_busy", "Publishing root requires one active owner on a POSIX host") from exc
        try:
            self._content = self.root / "releases"
            self._content.mkdir(exist_ok=True, mode=0o700)
            self._staging = self.root / "staging"
            self._staging.mkdir(exist_ok=True, mode=0o700)
            self._db = sqlite3.connect(self.root / "state.sqlite3", check_same_thread=False, isolation_level=None)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))")
            self._db.execute("CREATE TABLE IF NOT EXISTS receipts (session TEXT, request TEXT, fingerprint TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(session,request))")
            self._db.execute("CREATE INDEX IF NOT EXISTS publishing_resources ON records(json_extract(body,'$.sessionId'),kind,id)")
            self._db.execute("CREATE INDEX IF NOT EXISTS publishing_pins ON records(json_extract(body,'$.sessionId'),json_extract(body,'$.siteId'),id) "
                             "WHERE kind='retention' AND json_extract(body,'$.pinned')=1 AND json_extract(body,'$.availability')='available'")
            self._db.execute("CREATE INDEX IF NOT EXISTS publishing_temporary ON records(json_extract(body,'$.sessionId'),id) "
                             "WHERE kind='retention' AND json_type(body,'$.policy')='object' AND json_extract(body,'$.availability')='available'")
            self._db.execute("CREATE INDEX IF NOT EXISTS publishing_retiring ON records(id) WHERE kind='retention' AND json_type(body,'$.retirement')='object'")
            self._db.execute("CREATE INDEX IF NOT EXISTS publishing_unsettled ON receipts(session,json_extract(body,'$.siteId')) "
                             "WHERE json_extract(body,'$.state') IN ('running','unknown') AND json_extract(body,'$.action')!='retire' "
                             "AND (json_extract(body,'$.state')='running' OR json_extract(body,'$.action') NOT IN ('deploy','rollback','stop','remove'))")
            self._recover()
            self.reconcile_retirements()
        except BaseException:
            self._lock_file.close()
            if hasattr(self, "_db"):
                self._db.close()
            raise

    @property
    def capabilities(self):
        return {"bindHost": self._bind_host, "accessPolicy": self._access_policy, "authentication": "none", "previewAccessPolicy": "loopback-only", "publicPublishing": False}

    def _check_open(self):
        if self._closed or self._closing:
            raise PublishingError("closed", "Publisher is closed")
        self._settle_body_reads()

    def _consumer_released(self, identity, observed_at=None):
        state = self._get('retention', identity)
        if state and state.get('policy') and state.get('availability') == 'available':
            observed_at = observed_at or _now()
            if not state.get('unusedSince') or observed_at > state['unusedSince']:
                state.update(unusedSince=observed_at, revision=state['revision'] + 1)
                self._put('retention', state)

    def _settle_body_reads(self):
        # HTTP never waits for a long build/backup transaction. These are actual
        # completion times, not a copied policy or availability registry.
        with self._readers_idle:
            for identity, observed_at in list(self._released_reads.items()):
                self._consumer_released(identity, observed_at)
                del self._released_reads[identity]

    def _interrupted(self, kind, record):
        record.update(status='interrupted', previousUrl=record.get('url'), url=None, updatedAt=_now())
        if kind == 'site':
            record['revision'] += 1
        else:
            self._consumer_released(record['releaseId'])
        self._put(kind, record)

    @contextmanager
    def _body_reader(self, server):
        with self._readers_idle:
            if self._closing:
                raise PublishingError('closed', 'Publisher is closed')
            if server._stopped:
                raise PublishingError('release_unavailable', 'This listener stopped serving its body')
            release = server.release
            identity = release['id']
            self._body_reads[identity] = self._body_reads.get(identity, 0) + 1
        try:
            yield release
        finally:
            with self._readers_idle:
                self._body_reads[identity] -= 1
                if not self._body_reads[identity]:
                    del self._body_reads[identity]
                    if server._stopped or server.release['id'] != identity:
                        self._released_reads[identity] = _now()
                self._readers_idle.notify_all()

    def _get(self, kind, identity):
        row = self._db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, identity)).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, kind, record):
        self._db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, record["id"], _json(record)))

    def _all(self, kind):
        return [json.loads(row[0]) for row in self._db.execute("SELECT body FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def _save_receipt(self, receipt, fingerprint):
        self._db.execute("INSERT OR REPLACE INTO receipts VALUES (?,?,?,?)", (receipt["sessionId"], receipt["requestId"], fingerprint, _json(receipt)))

    def _recover(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            # Earlier prospective records have no observed unused interval.
            # Establish a fresh grace once; never backdate an inferred lifetime.
            self._db.execute("UPDATE records SET body=json_set(body,'$.unusedSince',?,'$.revision',json_extract(body,'$.revision')+1) "
                             "WHERE kind='retention' AND json_type(body,'$.policy')='object' "
                             "AND json_extract(body,'$.availability')='available' AND json_type(body,'$.unusedSince') IS NULL", (_now(),))
            for kind in ("site", "preview"):
                for record in self._all(kind):
                    if record["status"] == "running":
                        self._interrupted(kind, record)
            rows = self._db.execute("SELECT fingerprint,body FROM receipts").fetchall()
            for fingerprint, body in rows:
                receipt = json.loads(body)
                if receipt["state"] == "running":
                    receipt.update(state="unknown", completedAt=_now(), error={"code": "unknown_outcome", "message": "Process ended before the operation outcome was recorded; it was not replayed"})
                    self._save_receipt(receipt, fingerprint)
                    if receipt['action'] == 'build':
                        self._uncertain_capture(receipt)
                    if receipt["action"] in {"deploy", "rollback", "stop", "remove"}:
                        self._fence(receipt)
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def _fence(self, receipt):
        site = self._get("site", receipt["siteId"]) or self._new_site(receipt["siteId"], receipt["sessionId"])
        site.update(status="unknown", url=None, updatedAt=_now())
        site["revision"] += 1
        site["unknownRequestIds"] = sorted(set(site.get("unknownRequestIds", []) + [receipt["requestId"]]))
        self._put("site", site)

    def _new_site(self, site_id, session_id):
        return {"id": site_id, "sessionId": session_id, "status": "stopped", "revision": 0, "url": None, "releaseId": None, "previousReleaseId": None, "accessPolicy": self._access_policy, "updatedAt": _now(), "deployedReleaseIds": []}

    def _scope(self, record, session_id, noun):
        if record is None or record["sessionId"] != session_id:
            raise PublishingError("not_found", f"{noun} is not available in this session")
        return record

    def _release(self, release_id, session_id):
        _identifier(release_id, "releaseId")
        _identifier(session_id, "sessionId")
        return self._scope(self._get("release", release_id), session_id, "Release")

    def _site(self, site_id, session_id, expected_revision, *, allow_unknown=False):
        if type(expected_revision) is not int or expected_revision < 0:
            raise PublishingError("invalid_argument", "expectedRevision must be a nonnegative integer")
        site = self._get("site", site_id)
        if site:
            self._scope(site, session_id, "Site")
        else:
            site = self._new_site(site_id, session_id)
        if site["revision"] != expected_revision:
            raise PublishingError("stale_revision", f"Site changed; current revision is {site['revision']}")
        if site["status"] == "unknown" and not allow_unknown:
            raise PublishingError("unknown_outcome", "Site has an unknown operation; explicitly stop or remove it before another deployment")
        return site

    def _operation(self, action, session_id, request_id, payload, callback):
        _identifier(session_id, "sessionId")
        _identifier(request_id, "requestId")
        _identifier(payload["siteId"], "siteId")
        fingerprint = _digest({"action": action, "payload": payload})
        with self._mutex:
            self._check_open()
            self._refresh_liveness()
            previous = self._db.execute("SELECT fingerprint,body FROM receipts WHERE session=? AND request=?", (session_id, request_id)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise PublishingError("request_conflict", "requestId was already used with a different action or payload")
                receipt = json.loads(previous[1])
                return self._receipt_result(receipt, action)
            receipt = {"id": _digest([session_id, request_id]), "requestId": request_id, "sessionId": session_id, "action": action, "siteId": payload["siteId"], "releaseId": payload.get("releaseId"), "state": "running", "createdAt": _now(), "completedAt": None, "result": None, "error": None}
            self._save_receipt(receipt, fingerprint)  # FULL synchronous commit precedes side effects
            self._db.execute("BEGIN IMMEDIATE")
            try:
                result = callback(receipt)
                receipt.update(state="succeeded", result=result, completedAt=_now())
                self._save_receipt(receipt, fingerprint)
                self._db.execute("COMMIT")
            except PublishingError as exc:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                receipt.update(state="failed", error={"code": exc.code, "message": str(exc)}, completedAt=_now())
                self._save_receipt(receipt, fingerprint)
                raise PublishingError(exc.code, str(exc), receipt=receipt) from exc
            except Exception as exc:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                # An unexpected failure after an external effect cannot be called
                # a known failure. Fail closed, retain uncertainty, never retry it.
                self._stop_listeners(payload["siteId"])
                receipt.update(state="unknown", error={"code": "unknown_outcome", "message": "Operation outcome could not be recorded; it will not be replayed"}, completedAt=_now())
                self._db.execute("BEGIN IMMEDIATE")
                self._refresh_liveness()
                self._save_receipt(receipt, fingerprint)
                if action == 'build':
                    self._uncertain_capture(receipt)
                if action in {"deploy", "rollback", "stop", "remove"}:
                    self._fence(receipt)
                self._db.execute("COMMIT")
                raise PublishingError("unknown_outcome", receipt["error"]["message"], receipt=receipt) from exc
            return self._receipt_result(receipt, action)

    def _uncertain_capture(self, receipt):
        identity = receipt.get('releaseId')
        state = self._get('retention', identity) if identity else None
        if state and state.get('availability') == 'expired' and (self._content / identity).exists():
            state.update(availability='unknown', revision=state['revision'] + 1)
            self._put('retention', state)

    @staticmethod
    def _receipt_result(receipt, action):
        if receipt["state"] != "succeeded":
            error = receipt.get("error") or {"code": "unknown_outcome", "message": "Operation outcome is unknown; it will not be replayed"}
            raise PublishingError(error["code"], error["message"], receipt=receipt)
        return receipt["result"] if action == "build" else receipt

    def _snapshot(self, source):
        staging = Path(tempfile.mkdtemp(prefix="snapshot-", dir=self._staging))
        output = staging / "files"
        output.mkdir()
        manifest = []
        total = 0
        entries = 0
        max_files, max_file, max_total = self._limits

        def visit(directory_fd, relative=""):
            nonlocal total, entries
            names = []
            with os.scandir(directory_fd) as scan:
                for entry in scan:
                    entries += 1
                    if entries > max_files * 2:
                        raise PublishingError("size_limit", "Build output exceeds publishing entry limits")
                    names.append(entry.name)
            for name in sorted(names):
                path = f"{relative}/{name}" if relative else name
                _safe_parts(path, allow_sentinel=True)
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if path == ".nojekyll" and (not stat.S_ISREG(info.st_mode) or info.st_size != 0):
                    raise PublishingError("unsafe_file", "Only an empty root .nojekyll sentinel is permitted")
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                    try:
                        visit(child, path)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    if len(manifest) >= max_files or info.st_size > max_file or total + info.st_size > max_total:
                        raise PublishingError("size_limit", "Build output exceeds publishing size limits")
                    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                    with os.fdopen(file_fd, "rb") as stream:
                        before = os.fstat(stream.fileno())
                        if not stat.S_ISREG(before.st_mode):
                            raise PublishingError("unsafe_file", "Build output includes a special file")
                        data = stream.read(min(max_file, max_total - total) + 1)
                        after = os.fstat(stream.fileno())
                    if len(data) > max_file or total + len(data) > max_total:
                        raise PublishingError("size_limit", "Build output exceeds publishing size limits")
                    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != after.st_size:
                        raise PublishingError("source_changed", "Build output changed while its snapshot was being captured")
                    if path == ".nojekyll" and data:
                        raise PublishingError("unsafe_file", "Only an empty root .nojekyll sentinel is permitted")
                    target = output / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, "xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    target.chmod(0o400)
                    manifest.append({"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                    total += len(data)
                else:
                    raise PublishingError("unsafe_file", "Build output includes a symlink or special file")

        try:
            if self.root == source or self.root.is_relative_to(source) or source.is_relative_to(self.root):
                raise PublishingError("unsafe_source", "Build output and publishing storage must be separate directories")
            fd = _open_directory(source)
            try:
                visit(fd)
            finally:
                os.close(fd)
            if not manifest or not any(item["path"] == "index.html" for item in manifest):
                raise PublishingError("missing_entrypoint", "Static build output must include index.html")
            manifest.sort(key=lambda item: item["path"])
            return staging, manifest, total
        except BaseException:
            shutil.rmtree(staging)
            raise

    def build(self, source, *, site_id, session_id, request_id, retention=None):
        source = Path(source).expanduser()
        if ".." in source.parts:
            raise PublishingError("unsafe_path", "Build output cannot contain traversal components")
        source = source.absolute()

        def create(_receipt):
            try:
                settings = release_retention.policy(retention)
            except ValueError as exc:
                raise PublishingError('invalid_argument', str(exc)) from exc
            site = self._get("site", site_id)
            if site:
                self._scope(site, session_id, "Site")
            # A release reserves the site name to its owner before first deploy.
            for existing in self._all("release"):
                if existing["siteId"] == site_id and existing["sessionId"] != session_id:
                    raise PublishingError("not_found", "Site is not available in this session")
            try:
                staging, manifest, total = self._snapshot(source)
            except OSError as exc:
                raise PublishingError("unsafe_source", "Build output could not be safely read") from exc
            digest = _digest(manifest)
            identity = _digest({"siteId": site_id, "sessionId": session_id, "manifestDigest": digest})
            # Capture discovered this content identity. Commit that exact intent
            # before installing its immutable directory, including a recapture.
            _receipt['releaseId'] = identity
            fingerprint = self._db.execute("SELECT fingerprint FROM receipts WHERE session=? AND request=?",
                                           (session_id, request_id)).fetchone()[0]
            self._save_receipt(_receipt, fingerprint)
            self._db.execute('COMMIT')
            self._db.execute('BEGIN IMMEDIATE')
            release = {"id": identity, "siteId": site_id, "sessionId": session_id, "manifestDigest": digest, "createdAt": _now(), "files": manifest, "totalBytes": total}
            try:
                prior = self._get("release", identity)
                state = self._get('retention', identity)
                restoring = state is not None and state.get('availability') == 'expired'
                if prior:
                    release = prior
                    if not restoring:
                        self._require_available(release)
                destination = self._content / identity
                if restoring and destination.exists():
                    raise PublishingError('release_unavailable', 'Expired release has unsettled stored content; it was not replaced')
                if destination.exists():
                    # Verify rather than replace an existing immutable snapshot,
                    # including a possible uncommitted snapshot after a crash.
                    self._verify(release)
                else:
                    with open(staging / "manifest.json", "w") as stream:
                        stream.write(_json(manifest))
                        stream.flush()
                        os.fsync(stream.fileno())
                    for directory, _children, _files in os.walk(staging, topdown=False):
                        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    os.replace(staging, destination)
                    directory_fd = os.open(self._content, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                self._put("release", release)
                if (prior is None and settings is not None) or restoring:
                    self._put('retention', release_retention.capture(release, settings, _now(), previous=state))
                return self._project_release(release)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

        payload = {"siteId": site_id, "source": str(source)}
        if retention is not None:
            payload['retention'] = retention
        return self._operation("build", session_id, request_id, payload, create)

    def _require_available(self, release):
        state = self._get('retention', release['id'])
        if state and state.get('availability') == 'expired':
            raise PublishingError('release_expired', 'Release content expired; its manifest and receipts remain. A new deliberate build can capture it again')
        if not release_retention.available(state):
            raise PublishingError('release_unavailable', 'Release content has an unresolved retirement; it was not restored or replayed')

    def _verify(self, release):
        self._require_available(release)
        identity = _digest({"siteId": release["siteId"], "sessionId": release["sessionId"], "manifestDigest": release["manifestDigest"]})
        if identity != release["id"] or _digest(release["files"]) != release["manifestDigest"]:
            raise PublishingError("integrity_error", "Release manifest does not match its immutable digest")
        try:
            for item in release["files"]:
                data = _read_file(self._content / release["id"] / "files", item["path"], item["size"])
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise PublishingError("integrity_error", "Release content does not match its immutable manifest")
        except OSError as exc:
            raise PublishingError("integrity_error", "Release content is missing or unsafe") from exc

    def _release_operation(self, action, release_id, session_id, request_id, payload, callback):
        with self._mutex:
            self._check_open()
            release = self._release(release_id, session_id)
            return self._operation(action, session_id, request_id, {"siteId": release["siteId"], "releaseId": release_id, **payload}, lambda receipt: callback(release, receipt))

    def review(self, release_id, *, session_id, request_id, note=""):
        def record(release, receipt):
            if not isinstance(note, str) or len(note) > 4000:
                raise PublishingError("invalid_argument", "Review note must be at most 4000 characters")
            self._verify(release)
            review = {"id": release_id, "sessionId": session_id, "note": note, "reviewedAt": _now(), "receiptId": receipt["id"], "manifestDigest": release["manifestDigest"]}
            self._put("review", review)
            return self._project_release(release)
        return self._release_operation("review", release_id, session_id, request_id, {"note": note}, record)

    def retention(self, release_id, session_id):
        """Read one owned body's current policy/availability without content reads."""
        with self._mutex:
            self._check_open()
            _identifier(release_id, 'releaseId')
            _identifier(session_id, 'sessionId')
            row = self._db.execute("SELECT json_extract(body,'$.sessionId'),json_extract(body,'$.createdAt'),json_extract(body,'$.siteId') "
                                   "FROM records WHERE kind='release' AND id=?", (release_id,)).fetchone()
            release = self._scope({'id': release_id, 'sessionId': row[0], 'createdAt': row[1], 'siteId': row[2]} if row else None,
                                  session_id, 'Release')
            return release_retention.projection(self._get('retention', release_id)
                    or release_retention.capture(release, None, release['createdAt']), identity=True)

    def pin(self, release_id, *, session_id, request_id, expected_revision, pinned):
        """Choose durable protection for an available body without changing history."""
        def change(release, _receipt):
            state = self._get('retention', release_id)
            revision = state['revision'] if state else 0
            if type(expected_revision) is not int or expected_revision < 0 or type(pinned) is not bool:
                raise PublishingError('invalid_argument', 'Pin requires a boolean choice and nonnegative lifecycle revision')
            if revision != expected_revision:
                raise PublishingError('stale_revision', f'Release retention changed; current revision is {revision}')
            self._require_available(release)
            state = state or release_retention.capture(release, None, release['createdAt'])
            if state['pinned'] and not pinned and state.get('policy'):
                state['unusedSince'] = _now()
            state.update(pinned=pinned, revision=revision + 1)
            self._put('retention', state)
            return self._project_release(release)
        return self._release_operation('pin', release_id, session_id, request_id,
                                       {'expectedRevision': expected_revision, 'pinned': pinned}, change)

    def _start(self, release, *, preview=False):
        try:
            return _Server(release, self._content, "127.0.0.1" if preview else self._bind_host, self)
        except OSError as exc:
            raise PublishingError("listen_failed", "Loopback listener could not be started") from exc

    def preview(self, release_id, *, session_id, request_id):
        def start(release, _receipt):
            self._verify(release)
            server = self._previews.get(release_id)
            if server is None:
                server = self._start(release, preview=True)
                self._previews[release_id] = server
            preview = {"id": release_id, "sessionId": session_id, "siteId": release["siteId"], "releaseId": release_id, "status": "running", "url": server.url, "accessPolicy": "loopback-only", "updatedAt": _now()}
            self._put("preview", preview)
            return preview
        return self._release_operation("preview", release_id, session_id, request_id, {}, start)

    def _deploy(self, action, release_id, site_id, session_id, expected_revision, request_id):
        def deploy(_receipt):
            release = self._release(release_id, session_id)
            if release["siteId"] != site_id:
                raise PublishingError("wrong_site", "Release belongs to another site")
            site = self._site(site_id, session_id, expected_revision)
            review = self._get("review", release_id)
            if not review or review["manifestDigest"] != release["manifestDigest"]:
                raise PublishingError("review_required", "Review this exact release before deployment")
            if action == "rollback" and release_id not in site["deployedReleaseIds"]:
                raise PublishingError("not_deployed", "Rollback requires a previously deployed release of this site")
            self._verify(release)
            server = self._sites.get(site_id)
            if server is None:
                server = self._start(release)
                self._sites[site_id] = server
            else:
                with self._readers_idle:
                    server.release = release
            now = _now()
            current_retention = self._get('retention', release_id)
            if (current_retention and current_retention.get('policy')) or 'rollbackHistory' in site:
                previous_id = site['releaseId']
                history = [item for item in site.get('rollbackHistory', []) if item['releaseId'] not in {release_id, previous_id}]
                if previous_id and previous_id != release_id:
                    previous_state = self._get('retention', previous_id)
                    deadline = release_retention.rollback_deadline(previous_state, now)
                    history.insert(0, {'releaseId': previous_id, 'supersededAt': now, 'retainUntil': deadline})
                    if previous_state and previous_state.get('policy'):
                        previous_state.update(supersededAt=now, rollbackUntil=deadline, unusedSince=now, revision=previous_state['revision'] + 1)
                        self._put('retention', previous_state)
                settings = (current_retention or {}).get('policy') or release_retention.DEFAULTS
                site['rollbackHistory'] = history[:settings['rollbackKeepCount']]
            site.update(status="running", url=server.url, accessPolicy=self._access_policy, revision=site["revision"] + 1, previousReleaseId=site["releaseId"], releaseId=release_id, updatedAt=now)
            site["deployedReleaseIds"] = sorted(set(site["deployedReleaseIds"] + [release_id]))
            self._put("site", site)
            return site
        return self._operation(action, session_id, request_id, {"siteId": site_id, "releaseId": release_id, "expectedRevision": expected_revision}, deploy)

    def deploy(self, release_id, *, site_id, session_id, expected_revision, request_id):
        return self._deploy("deploy", release_id, site_id, session_id, expected_revision, request_id)

    def rollback(self, release_id, *, site_id, session_id, expected_revision, request_id):
        return self._deploy("rollback", release_id, site_id, session_id, expected_revision, request_id)

    def _stop_listeners(self, site_id):
        server = self._sites.pop(site_id, None)
        if server:
            server.stop()
        for release_id, preview in list(self._previews.items()):
            if preview.release["siteId"] == site_id:
                self._previews.pop(release_id).stop()

    def _end(self, action, site_id, session_id, expected_revision, request_id):
        def end(_receipt):
            site = self._site(site_id, session_id, expected_revision, allow_unknown=True)
            # A preview-only site is still owned by the release's session.
            for release in self._all("release"):
                if release["siteId"] == site_id:
                    self._scope(release, session_id, "Site")
            self._stop_listeners(site_id)
            if action == 'remove' and site.get('releaseId'):
                self._consumer_released(site['releaseId'])
            site.update(status="removed" if action == "remove" else "stopped", url=None, accessPolicy=self._access_policy, revision=site["revision"] + 1, updatedAt=_now())
            site.pop("unknownRequestIds", None)
            self._put("site", site)
            for preview in self._all("preview"):
                if preview["siteId"] == site_id:
                    if preview['status'] == 'running':
                        self._consumer_released(preview['releaseId'])
                    preview.update(status="stopped", url=None, updatedAt=_now())
                    self._put("preview", preview)
            return site
        return self._operation(action, session_id, request_id, {"siteId": site_id, "expectedRevision": expected_revision}, end)

    def stop(self, site_id, *, session_id, expected_revision, request_id):
        return self._end("stop", site_id, session_id, expected_revision, request_id)

    def remove(self, site_id, *, session_id, expected_revision, request_id):
        return self._end("remove", site_id, session_id, expected_revision, request_id)

    def _project_release(self, release):
        record = dict(release)
        record["review"] = self._get("review", release["id"])
        preview = self._get("preview", release["id"])
        record["previewUrl"] = preview["url"] if preview else None
        record["previewStatus"] = preview["status"] if preview else "none"
        state = self._get('retention', release['id'])
        if state is not None:
            record['retention'] = release_retention.projection(state)
        return record

    def _project_site(self, site):
        if 'rollbackHistory' not in site:
            return site
        states = {item['releaseId']: self._get('retention', item['releaseId']) for item in site['rollbackHistory']}
        candidates = release_retention.rollback_candidates(site, states, _now())
        # Explicit pins can outlive both the automatic time and count windows.
        # Read only current pins, without hydrating historical release manifests.
        pinned = self._db.execute("SELECT id FROM records WHERE kind='retention' "
                                  "AND json_extract(body,'$.sessionId')=? AND json_extract(body,'$.siteId')=? "
                                  "AND json_extract(body,'$.pinned')=1 AND json_extract(body,'$.availability')='available' ORDER BY id",
                                  (site['sessionId'], site['id']))
        deployed = set(site['deployedReleaseIds'])
        candidates.extend(row[0] for row in pinned if row[0] in deployed
                          and row[0] != site['releaseId'] and row[0] not in candidates)
        # previousReleaseId is historical. Only these current choices are offered
        # automatically; an available explicitly pinned body can outlive a window.
        return {**site, 'rollbackCandidates': candidates,
                'previousReleaseAvailable': site.get('previousReleaseId') in candidates}

    def _refresh_liveness(self):
        # Process-owned listener objects are the only authority for a live URL.
        for kind, listeners in (("site", self._sites), ("preview", self._previews)):
            for record in self._all(kind):
                if record["status"] != "running":
                    continue
                server = listeners.get(record["id"])
                if server is None or not server.thread.is_alive():
                    stale = listeners.pop(record["id"], None)
                    if stale:
                        stale.stop()
                    self._interrupted(kind, record)

    def resources(self, session_id, limit=25, before=None):
        """Observe one indexed task page through the actual listener owner.

        The cursor is [kind,id]; only selected retained listeners are inspected.
        Release manifests, receipts, and unrelated task history stay on disk.
        """
        _identifier(session_id, "session_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PublishingError("invalid_argument", "Resource page size must be between 1 and 100")
        if before is not None and (not isinstance(before, list) or len(before) != 2
                or before[0] not in {'preview', 'site'} or not isinstance(before[1], str)):
            raise PublishingError("invalid_argument", "Invalid publishing resource cursor")
        with self._mutex:
            self._check_open()
            where = "json_extract(body,'$.sessionId')=? AND kind IN ('preview','site')"
            params = [session_id]
            if before:
                where += " AND (kind,id)>(?,?)"
                params.extend(before)
            # Site/preview records contain no file manifests; still whitelist
            # the projection rather than transporting future owner-only fields.
            rows = self._db.execute("SELECT kind,id FROM records WHERE " + where +
                                    " ORDER BY kind,id LIMIT ?", (*params, limit + 1)).fetchall()
            items = []
            for kind, identity in rows[:limit]:
                record = self._get(kind, identity)
                listeners = self._previews if kind == 'preview' else self._sites
                server = listeners.get(identity)
                if record['status'] == 'running' and (server is None or not server.thread.is_alive()):
                    stale = listeners.pop(identity, None)
                    if stale:
                        stale.stop()
                    self._interrupted(kind, record)
                running = record['status'] == 'running' and server is not None and server.thread.is_alive()
                release = record.get('releaseId')
                items.append({**{key: record.get(key) for key in
                              ('id', 'sessionId', 'siteId', 'releaseId', 'status', 'updatedAt', 'accessPolicy')},
                              'kind': kind, 'owner': 'publishing', 'evidence': 'listener-owner',
                              'url': server.url if running else None,
                              'port': server.server_port if running else None,
                              'releaseDirectory': str(self._content / release / 'files') if release else None})
            return {'items': items, 'nextCursor': list(rows[limit - 1]) if len(rows) > limit else None}

    def export_release(self, release_id, session_id):
        """Return verified immutable bytes as a portable import payload.

        This read-only operation accepts an owned release identity, never a
        caller filesystem path. The transport applies its own smaller limits.
        """
        with self._mutex:
            self._check_open()
            release = self._release(release_id, session_id)
            self._verify(release)
            files = {}
            try:
                for entry in release["files"]:
                    data = _read_file(self._content / release["id"] / "files", entry["path"], entry["size"])
                    # Recheck the bytes being exported, including concurrent
                    # same-account disk changes after the initial verification.
                    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                        raise PublishingError("integrity_error", "Release bytes changed during export")
                    files[entry["path"]] = base64.b64encode(data).decode("ascii")
            except OSError as exc:
                raise PublishingError("integrity_error", "Release content is missing or unsafe") from exc
            return {"siteId": release["siteId"], "sessionId": release["sessionId"], "manifest": release["files"], "manifestDigest": release["manifestDigest"], "files": files}

    def snapshot(self, destination):
        """Copy a consistent private audit/store backup without live listeners.

        Opening this backup treats historical live URLs as interrupted. The caller
        owns encryption, retention and admission controls for the backup path.
        """
        destination = Path(destination).absolute()
        if ".." in destination.parts or destination.exists():
            raise PublishingError("invalid_destination", "Backup destination must be a new directory")
        if destination.is_relative_to(self.root) or self.root.is_relative_to(destination):
            raise PublishingError("invalid_destination", "Backup must be separate from publishing storage")
        with self._mutex:
            self._check_open()
            self._refresh_liveness()
            releases = self._all("release")
            available = []
            for release in releases:
                state = self._get('retention', release['id'])
                if state and state.get('availability') == 'expired':
                    continue
                self._verify(release)
                available.append(release)
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix="publishing-backup-", dir=destination.parent))
            try:
                backup = sqlite3.connect(staging / "state.sqlite3")
                try:
                    self._db.backup(backup)
                finally:
                    backup.close()
                for release in available:
                    target = staging / "releases" / release["id"]
                    for item in release["files"]:
                        path = target / "files" / item["path"]
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(_read_file(self._content / release["id"] / "files", item["path"], item["size"]))
                        path.chmod(0o400)
                    (target / "manifest.json").write_text(_json(release["files"]))
                metadata = {"format": "static-publishing-v1", "createdAt": _now(), "releaseCount": len(releases), "siteCount": len(self._all("site")),
                            "availableReleaseCount": len(available), "expiredReleaseCount": len(releases) - len(available)}
                (staging / "snapshot.json").write_text(_json(metadata))
                os.rename(staging, destination)
                return {**metadata, "path": str(destination)}
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def list(self, session_id):
        with self._mutex:
            self._check_open()
            self._refresh_liveness()
            return [self._project_site(site) for site in self._all("site") if site["sessionId"] == session_id]

    def status(self, site_id, session_id):
        with self._mutex:
            self._check_open()
            self._refresh_liveness()
            return self._project_site(self._scope(self._get("site", site_id), session_id, "Site"))

    def releases(self, session_id):
        with self._mutex:
            self._check_open()
            self._refresh_liveness()
            return [self._project_release(release) for release in self._all("release") if release["sessionId"] == session_id]

    def receipts(self, session_id):
        with self._mutex:
            self._check_open()
            return [json.loads(row[0]) for row in self._db.execute("SELECT body FROM receipts WHERE session=? ORDER BY rowid", (session_id,))]

    def receipt(self, session_id, request_id):
        with self._mutex:
            self._check_open()
            _identifier(session_id, 'sessionId')
            _identifier(request_id, 'requestId')
            row = self._db.execute('SELECT body FROM receipts WHERE session=? AND request=?', (session_id, request_id)).fetchone()
            return json.loads(row[0]) if row else None

    def retirement_candidates(self, session_id, *, policy, limit=25, after=None):
        from .retirement import candidates
        return candidates(self, session_id, policy=policy, limit=limit, after=after)

    def retire(self, release_id, *, session_id, request_id, expected_revision, policy, stop_event=None):
        from .retirement import retire
        return retire(self, release_id, session_id, request_id, expected_revision, policy, stop_event)

    def reconcile_retirements(self, *, limit=1, after=None, stop_event=None):
        from .retirement import reconcile
        return reconcile(self, limit=limit, after=after, stop_event=stop_event)

    def close(self):
        with self._mutex:
            if self._closed:
                return
            with self._readers_idle:
                self._closing = True
            try:
                for site_id in list(self._sites):
                    self._stop_listeners(site_id)
                for server in self._previews.values():
                    server.stop()
                self._previews.clear()
                while self._retirement_workers:
                    self._idle.wait()
                with self._readers_idle:
                    while self._body_reads:
                        self._readers_idle.wait()
                self._settle_body_reads()
                self._db.execute("BEGIN IMMEDIATE")
                for kind in ("site", "preview"):
                    for record in self._all(kind):
                        if record["status"] == "running":
                            record.update(status="stopped", previousUrl=record["url"], url=None, updatedAt=_now())
                            if kind == "site":
                                record["revision"] += 1
                            else:
                                self._consumer_released(record['releaseId'])
                            self._put(kind, record)
                self._db.execute("COMMIT")
            finally:
                self._db.close()
                self._lock_file.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
