"""Explicit, single-attempt transport to a separately managed private service."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException
import ipaddress
import json
import math
import os
from pathlib import PurePosixPath
import re
import selectors
import shlex
import socket
import subprocess
import time
import threading
import uuid
from urllib.parse import urlsplit

from .publisher import PublishingError
from .retention import valid_projection

MAX_MESSAGE_BYTES = 16 * 1024 * 1024
MUTATIONS = {"import", "preview", "review", "deploy", "rollback", "stop", "remove"}
MAX_STDERR_BYTES = 64 * 1024


def _run_bounded(command, data, timeout):
    """Multiplex all pipes without unbounded communicate()/capture_output."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, bufsize=0)
    selector = selectors.DefaultSelector()
    output = bytearray()
    error_bytes = 0
    sent = 0
    deadline = time.monotonic() + timeout
    try:
        for stream, events, name in ((process.stdin, selectors.EVENT_WRITE, "in"), (process.stdout, selectors.EVENT_READ, "out"), (process.stderr, selectors.EVENT_READ, "err")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, events, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _events in selector.select(min(remaining, 0.2)):
                stream = key.fileobj
                if key.data == "in":
                    try:
                        sent += os.write(stream.fileno(), memoryview(data)[sent:sent + 65536])
                    except BrokenPipeError:
                        sent = len(data)
                    except BlockingIOError:
                        continue
                    if sent == len(data):
                        selector.unregister(stream)
                        stream.close()
                    continue
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                elif key.data == "out":
                    if len(output) + len(chunk) > MAX_MESSAGE_BYTES:
                        raise ValueError("SSH output exceeds protocol bound")
                    output.extend(chunk)
                else:
                    error_bytes += len(chunk)
                    if error_bytes > MAX_STDERR_BYTES:
                        raise ValueError("SSH diagnostic output exceeds bound")
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(command, process.returncode, bytes(output), b"")
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def private_bind(value):
    """Accept only explicit numeric IPv4 loopback or RFC1918 interfaces."""
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise PublishingError("invalid_bind", "Bind must be a numeric IPv4 loopback or RFC1918 address") from exc
    if str(address) == "127.0.0.1":
        return str(address), "loopback-only"
    if any(address in ipaddress.IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        return str(address), "private-network"
    raise PublishingError("invalid_bind", "Bind must be a numeric IPv4 loopback or RFC1918 address")


def service_identity(value):
    """Require a canonical UUID instead of accepting arbitrary target labels."""
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError("not a canonical UUID")
    except (ValueError, AttributeError) as exc:
        raise PublishingError("invalid_service_id", "Service identity must be a canonical UUID") from exc
    return value


def timeout_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 120:
        raise PublishingError("invalid_argument", "Timeout must be greater than zero and at most 120 seconds")
    return float(value)


def encode_message(value):
    try:
        data = canonical(value).encode() + b"\n"
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PublishingError("invalid_message", "Request must be JSON data") from exc
    if len(data) > MAX_MESSAGE_BYTES:
        raise PublishingError("size_limit", "RPC message exceeds its byte limit")
    return data


def decode_message(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("nonfinite number")

    if len(data) > MAX_MESSAGE_BYTES:
        raise PublishingError("size_limit", "RPC message exceeds its byte limit")
    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PublishingError("invalid_message", "RPC message must be one unambiguous JSON object") from exc


def unknown_outcome(request):
    """A reconciliation reference; this does not assert the server received it."""
    session_id = request.get("sessionId")
    request_id = request.get("requestId")
    return PublishingError(
        "unknown_outcome", "Transport ended without a verified response; inspect the receipt before any further mutation. Nothing was replayed.",
        receipt={"id": digest([session_id, request_id]) if session_id and request_id else None,
                 "sessionId": session_id, "requestId": request_id, "serviceId": request.get("expectedServiceId"), "state": "unknown", "remoteReceiptVerified": False},
    )


def response_result(response):
    if response.get("ok") is True and set(response) == {"ok", "result"}:
        return response["result"]
    error = response.get("error")
    if response.get("ok") is False and isinstance(error, dict) and isinstance(error.get("code"), str) and isinstance(error.get("message"), str):
        raise PublishingError(error["code"], error["message"], receipt=error.get("receipt"))
    raise PublishingError("invalid_response", "Service returned an invalid response")


def unix_request(socket_path, request, *, timeout=20):
    """Send once to an owner-controlled Unix socket; never retry on loss."""
    data = encode_message(request)
    timeout = timeout_value(timeout)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(socket_path))
            connection.sendall(data)
            with connection.makefile("rb") as stream:
                raw = stream.readline(MAX_MESSAGE_BYTES + 1)
        if not raw.endswith(b"\n"):
            raise ValueError("incomplete response")
        response = decode_message(raw)
    except (OSError, ValueError, PublishingError) as exc:
        raise unknown_outcome(request) from exc
    try:
        return response_result(response)
    except PublishingError as exc:
        if exc.code == "invalid_response":
            raise unknown_outcome(request) from exc
        raise


class SSHClient:
    """Configured transport only: no install, SSH consent, forwarding or replay.

    The target account must already have this package and a running service.
    Host keys must already be trusted. Shell quoting is applied to each remote
    argv item because OpenSSH executes the remote command using a shell.
    """

    def __init__(self, *, hostname, python, socket_path, expected_bind, expected_service_id=None, username=None, timeout=30):
        if not isinstance(hostname, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}", hostname):
            raise PublishingError("invalid_target", "An explicit SSH hostname or address is required")
        if username is not None and (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", username)):
            raise PublishingError("invalid_target", "SSH username is invalid")
        for value in (python, socket_path):
            if not isinstance(value, str) or not PurePosixPath(value).is_absolute() or any(ord(c) < 32 for c in value) or ".." in PurePosixPath(value).parts:
                raise PublishingError("invalid_target", "Remote Python and socket paths must be explicit absolute paths")
        self.hostname, self.python, self.socket_path = hostname, python, socket_path
        self.username = username
        self.bind, self.access_policy = private_bind(expected_bind)
        self.timeout = timeout_value(timeout)
        self.expected_service_id = service_identity(expected_service_id) if expected_service_id is not None else None
        self.service_id = self.expected_service_id
        self._mutex = threading.RLock()

    def command(self):
        remote = shlex.join([self.python, "-m", "amplifier_publishing.service", "request", "--socket", self.socket_path, "--timeout", str(self.timeout)])
        target = f"{self.username}@{self.hostname}" if self.username else self.hostname
        return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "--", target, remote]

    def _send(self, request):
        data = encode_message(request)
        try:
            completed = _run_bounded(self.command(), data, self.timeout)
            if completed.returncode != 0:
                raise ValueError("transport failed")
            response = decode_message(completed.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired, PublishingError) as exc:
            raise unknown_outcome(request) from exc
        try:
            return response_result(response)
        except PublishingError as exc:
            if exc.code == "invalid_response":
                raise unknown_outcome(request) from exc
            raise

    def verify_target(self):
        """Discover once or verify the already pinned durable service identity."""
        with self._mutex:
            request = {"method": "target"}
            if self.service_id is not None:
                request["expectedServiceId"] = self.service_id
            target = self._send(request)
            if not isinstance(target, dict) or target.get("protocol") != "static-publishing-v1" or target.get("adminTransport") != "owner-unix-socket" or target.get("bind") != self.bind or target.get("accessPolicy") != self.access_policy or target.get("authentication") != "none" or target.get("publicPublishing") is not False or target.get("previewAccessPolicy") != "loopback-only":
                raise PublishingError("target_mismatch", "Service bind, access policy or protocol differs from the explicit target configuration")
            try:
                discovered = service_identity(target.get("serviceId"))
            except PublishingError as exc:
                raise PublishingError("target_mismatch", "Service did not report a valid durable identity") from exc
            if self.service_id is not None and discovered != self.service_id:
                raise PublishingError("target_mismatch", "Service identity differs from the inspected target")
            self.service_id = discovered
            return target

    def request(self, request):
        if not isinstance(request, dict):
            raise PublishingError("invalid_message", "Request must be a JSON object")
        with self._mutex:
            supplied_identity = request.get("expectedServiceId")
            if "expectedServiceId" in request:
                service_identity(supplied_identity)
                if self.service_id is not None and supplied_identity != self.service_id:
                    raise PublishingError("target_mismatch", "Request identity differs from this client's pinned service")
                if self.service_id is None:
                    self.service_id = supplied_identity
            if request.get("method") == "target":
                if set(request) - {"method", "expectedServiceId"}:
                    raise PublishingError("invalid_argument", "Target discovery does not accept extra request fields")
                return self.verify_target()
            if self.service_id is None or request.get("method") in MUTATIONS:
                self.verify_target()
            # The server checks this identity on the actual operation request,
            # not merely on a separate preflight susceptible to a target swap.
            guarded = {**request, "expectedServiceId": self.service_id}
            result = self._send(guarded)
            method = request.get("method")
            # Receipt reads must reach the caller's reconciliation layer intact:
            # one rejected row must not hide its locally retained unknown intent.
            if method == "receipt":
                if result is not None and not isinstance(result, dict):
                    raise PublishingError("invalid_response", "Receipt lookup must return an object or null")
                return result
            if method == "receipts":
                if not isinstance(result, list):
                    raise PublishingError("invalid_response", "Receipt list must return an array")
                return result
            try:
                validated = self.validate_result(method, result)
                if method in MUTATIONS - {"import"} and result.get("state") != "succeeded":
                    raise PublishingError("invalid_response", "Mutation success response has no successful receipt")
                return validated
            except PublishingError as exc:
                if method in MUTATIONS and (exc.code == "invalid_response" or exc.receipt is None):
                    # Neither a malformed success nor a rejected raw import
                    # result proves that the mutation had no effect.
                    raise unknown_outcome(guarded) from exc
                raise

    def validate_result(self, method, result):
        """Validate URL-bearing results or receipt projections without IO.

        Live and historical deployment URLs use the configured target. Preview
        URLs always use loopback. Receipt reads defer this check to their caller
        so rejected rows can remain visible as local unknown diagnostics.
        """
        def invalid(message, receipt=None):
            raise PublishingError("invalid_response", message, receipt=receipt)

        def url(value, bind, receipt=None):
            if value is None:
                return
            try:
                parsed = urlsplit(value) if isinstance(value, str) else None
                port = parsed.port if parsed else None
                valid = (type(value) is str and type(port) is int and 0 < port <= 65535
                         and value == f"http://{bind}:{port}/")
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise PublishingError("target_mismatch", "Returned URL differs from the configured private target", receipt=receipt)

        def site(record, *, preview=False, receipt=None, successful_action=None):
            if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
                invalid("Site or preview result must be an identified object", receipt)
            allowed = {"running", "stopped", "removed", "interrupted", "unknown"}
            if not isinstance(record.get("status"), str) or record["status"] not in allowed or "url" not in record:
                invalid("Site or preview result has an invalid status or missing URL field", receipt)
            if not preview and (type(record.get("revision")) is not int or record["revision"] < 0):
                invalid("Site result has an invalid revision", receipt)
            bind, policy = ("127.0.0.1", "loopback-only") if preview else (self.bind, self.access_policy)
            # Older publishers labeled never-deployed sites loopback-only even
            # on a private target. Retain those inactive audit records verbatim:
            # no current or historical endpoint or deployment may use this case.
            legacy_unused_private = (
                not preview and policy == "private-network"
                and record.get("accessPolicy") == "loopback-only"
                and record["status"] in {"stopped", "removed"}
                and record["url"] is None and record.get("previousUrl") is None
                and "releaseId" in record and record["releaseId"] is None
                and "previousReleaseId" in record and record["previousReleaseId"] is None
                and record.get("deployedReleaseIds") == []
            )
            if record.get("accessPolicy") != policy and not legacy_unused_private:
                raise PublishingError("target_mismatch", "Returned access policy differs from the configured private target", receipt=receipt)
            url(record["url"], bind, receipt)
            if "previousUrl" in record:
                url(record["previousUrl"], bind, receipt)
            if record["status"] == "running" and record["url"] is None:
                invalid("Running site or preview result has no URL", receipt)
            expected = {"preview": "running", "deploy": "running", "rollback": "running", "stop": "stopped", "remove": "removed"}.get(successful_action)
            if expected is not None and record["status"] != expected:
                invalid("Successful lifecycle receipt has an inconsistent result status", receipt)

        def release(record, receipt=None, *, require_retention=False):
            if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
                invalid("Release result must be an identified object", receipt)
            if not isinstance(record.get("files"), list) or not isinstance(record.get("manifestDigest"), str):
                invalid("Release result must include its manifest and digest", receipt)
            if not isinstance(record.get("previewStatus"), str) or record["previewStatus"] not in {"none", "running", "stopped", "interrupted"} or "previewUrl" not in record:
                invalid("Release result has invalid preview metadata", receipt)
            url(record["previewUrl"], "127.0.0.1", receipt)
            if record["previewStatus"] == "running" and record["previewUrl"] is None:
                invalid("Running release preview has no URL", receipt)
            if (require_retention or 'retention' in record) and not valid_projection(record.get('retention')):
                invalid("Release result has invalid retention metadata", receipt)

        def receipt(row, expected_action=None):
            if not isinstance(row, dict) or not isinstance(row.get("requestId"), str) or not row["requestId"]:
                invalid("Receipt must be an identified object")
            action = row.get("action")
            if not isinstance(action, str) or action not in MUTATIONS | {"build", "pin", "retire"} or not isinstance(row.get("state"), str) or row["state"] not in {"running", "unknown", "succeeded", "failed"}:
                invalid("Receipt has an invalid action or outcome", row)
            if expected_action is not None and action != expected_action and {action, expected_action} != {"build", "import"}:
                invalid("Receipt action differs from the requested operation", row)
            body = row.get("result")
            if row["state"] != "succeeded" and body is None:
                return
            if not isinstance(body, dict):
                invalid("Successful receipt must contain an object result", row)
            if action == 'retire':
                projection = {key: value for key, value in body.items() if key not in {'id', 'siteId', 'sessionId'}}
                if (body.get('id') != row.get('releaseId') or body.get('sessionId') != row.get('sessionId')
                        or body.get('siteId') != row.get('siteId') or not valid_projection(projection)
                        or (row['state'] == 'succeeded' and body['availability'] != 'expired')):
                    invalid('Retirement receipt has invalid body disposition', row)
            elif action in {"import", "build", "review", "pin"}:
                release(body, row, require_retention=action == 'pin')
            else:
                site(body, preview=action == "preview", receipt=row, successful_action=action if row["state"] == "succeeded" else None)

        if method == "receipt":
            if result is not None:
                receipt(result)
        elif method == "receipts":
            if not isinstance(result, list):
                invalid("Receipt list must return an array")
            for row in result:
                receipt(row)
        elif method == "list":
            if not isinstance(result, list):
                invalid("Site list must return an array")
            for row in result:
                site(row)
        elif method == "releases":
            if not isinstance(result, list):
                invalid("Release list must return an array")
            for row in result:
                release(row)
        elif method == "status":
            site(result)
        elif method == "import":
            release(result)
        elif method == "build" and isinstance(result, dict) and "state" not in result:
            release(result)
        elif method in MUTATIONS | {"build"}:
            receipt(result, method)
        return result

    def verify_site(self, *, session_id, site_id):
        """Verify exact served entrypoint bytes from this client's network.

        This is a read-only, bounded HTTP probe, not browser acceptance. A remote
        loopback URL cannot identify that host from this client's network; no
        forwarding or alternate hostname is invented to make it reachable.
        """
        self.verify_target()
        if self.bind == "127.0.0.1" and self.hostname != "127.0.0.1":
            raise PublishingError("url_unreachable", "Remote loopback URL is host-local; client reachability cannot be verified without an explicitly configured serving route")
        status = self.request({"method": "status", "sessionId": session_id, "siteId": site_id})
        if not isinstance(status, dict) or status.get("status") != "running" or not status.get("url"):
            raise PublishingError("url_unreachable", "Site has no current running URL")
        releases = self.request({"method": "releases", "sessionId": session_id})
        release = next((item for item in releases if isinstance(item, dict) and item.get("id") == status.get("releaseId") and item.get("siteId") == site_id), None) if isinstance(releases, list) else None
        if not release or not isinstance(release.get("files"), list) or digest(release["files"]) != release.get("manifestDigest"):
            raise PublishingError("integrity_error", "Current release manifest could not be verified")
        entry = next((item for item in release["files"] if isinstance(item, dict) and item.get("path") == "index.html"), None)
        if not entry or type(entry.get("size")) is not int or not 0 <= entry["size"] <= MAX_MESSAGE_BYTES:
            raise PublishingError("integrity_error", "Current release entrypoint is missing or exceeds verification limits")
        parsed = urlsplit(status["url"])
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=self.timeout)
        try:
            connection.request("GET", "/", headers={"Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status != 200:
                raise PublishingError("url_unreachable", "Configured site URL did not return a successful direct response")
            body = response.read(entry["size"] + 1)
            if len(body) != entry["size"] or hashlib.sha256(body).hexdigest() != entry["sha256"]:
                raise PublishingError("integrity_error", "Served entrypoint bytes differ from the current immutable release")
        except (OSError, HTTPException) as exc:
            raise PublishingError("url_unreachable", "Configured site URL could not be reached from this client") from exc
        finally:
            connection.close()
        latest = self.request({"method": "status", "sessionId": session_id, "siteId": site_id})
        if any(latest.get(key) != status.get(key) for key in ("revision", "releaseId", "url", "status")):
            raise PublishingError("site_changed", "Site changed during URL verification; inspect the current release")
        return {"siteId": site_id, "releaseId": release["id"], "manifestDigest": release["manifestDigest"], "revision": status["revision"], "url": status["url"], "accessPolicy": self.access_policy, "authentication": "none", "clientReachabilityVerified": True, "verifiedAt": datetime.now(timezone.utc).isoformat()}
