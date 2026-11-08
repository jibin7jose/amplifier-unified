"""Owner-to-browser handoff, not remote pairing or a fresh Windows Hello check.

The foreground owner launcher reads existing private credentials only.
Listener authentication and grant exchange remain separate from that local read.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit

ISSUE_PATH = "/api/browser-entry/issue"
REDEEM_PATH = "/api/browser-entry/redeem"
REOPEN = "Open from the owner launcher again: amplifier-unified open."


class BrowserEntryUnavailable(RuntimeError):
    pass


def is_windows() -> bool:
    return sys.platform == "win32"


def current_owner_sid() -> str:
    """Use the public process TokenUser seam, not a username or another API."""
    if not is_windows():
        raise BrowserEntryUnavailable("Windows process-token identity is unavailable.")
    from .windows_files import current_process_sid
    try:
        sid = current_process_sid()
        if (not isinstance(sid, str) or not re.fullmatch(r"S-1-(?:\d+-)+\d+", sid)
                or sid in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}):
            raise ValueError("An owner user token is required")
        return sid
    except (OSError, ValueError, RuntimeError, AttributeError):
        raise BrowserEntryUnavailable("Cannot verify the Windows owner process-token SID.") from None


def read_owner_control_token(data_dir: Path, owner_sid: str) -> str:
    """Read the existing owner credential through the native private boundary."""
    return _read_private_owner_control_token(data_dir, owner_sid)


def _read_private_owner_control_token(data_dir: Path, owner_sid: str) -> str:
    """Existing-only read of both credentials under one directory pin."""
    from . import windows_files
    from .auth import _windows_credential_value
    try:
        if not hmac.compare_digest(owner_sid.encode(), current_owner_sid().encode()):
            raise ValueError("Owner SID mismatch")
        root = windows_files.address(data_dir)
        with windows_files.directory(root / "config" / "auth", owned_root=root, create=False) as pinned:
            _windows_credential_value(pinned.read_private("session-secret", limit=65))
            return _windows_credential_value(pinned.read_private("control-token", limit=65))
    except (OSError, ValueError, RuntimeError, AttributeError, TypeError):
        raise BrowserEntryUnavailable("Cannot verify the existing private owner credentials.") from None


def _owner_probe_targets(data_dir: Path, config: dict):
    """Build HTTPS loopback targets and pin a privately read app-owned leaf.

    Do not use the generic probe's pathname read or a listener-supplied cert.
    The Windows TLS adapter supports exactly this app-owned fixed layout.
    """
    import aiohttp
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from . import windows_files
    tls = config.get("tls", {})
    if (tls.get("method") != "ca" or tls.get("cert") != "config/tls/leaf.crt"
            or tls.get("key") != "config/tls/leaf.key"):
        raise BrowserEntryUnavailable("Windows owner browser entry requires app-owned leaf-pinned HTTPS.")
    try:
        root = windows_files.address(data_dir)
        with windows_files.directory(root / "config" / "tls", owned_root=root, create=False) as pinned:
            certificate = x509.load_pem_x509_certificate(pinned.read_private("leaf.crt", limit=65536))
            pin = aiohttp.Fingerprint(certificate.fingerprint(hashes.SHA256()))
        urls = []
        for bind in config["bind"]:
            host = {"0.0.0.0": "127.0.0.1", "::": "::1", "localhost": "127.0.0.1"}.get(bind, bind)
            if host not in {"127.0.0.1", "::1"}:
                continue
            authority = f"[{host}]" if ":" in host else host
            urls.append(f"https://{authority}:{config['port']}/api/health")
        if not urls:
            raise ValueError("No HTTPS loopback bind")
        return list(dict.fromkeys(urls)), pin
    except (OSError, ValueError, RuntimeError, AttributeError, TypeError, KeyError):
        raise BrowserEntryUnavailable("Cannot verify a private SHA-256 TLS leaf pin and HTTPS loopback listener.") from None


def _control_token(data_dir: Path, owner_sid: str) -> str:
    try:
        token = read_owner_control_token(data_dir, owner_sid)
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{64}", token):
            raise ValueError("Malformed credential")
        return token
    except (OSError, ValueError):
        raise BrowserEntryUnavailable("Cannot read the verified private owner credential. " + REOPEN) from None


def _package_identity(identity):
    """Browser-entry comparison only; not updater/release or wheel-byte proof.

    The canonical running_identity owner reports revision=None for wheels and
    unknown source. Require the exact package version and explicit unknown on
    BOTH ends, or identical valid VCS revisions. Never turn missing/malformed
    provenance or a known/unknown mismatch into an unknown-source match.
    Private leaf-pinned HTTPS authenticates the owned listener independently.
    """
    from . import __version__
    if (not isinstance(identity, dict) or identity.get("version") != __version__
            or "revision" not in identity):
        return None
    revision = identity["revision"]
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch("[a-f0-9]{40}", revision)):
        return None
    return __version__, revision


@dataclass(frozen=True)
class Binding:
    instance_id: str
    data_identity: str
    origin: str
    owner_sid: str


class GrantStore:
    """Bounded hash-only verifiers; lock the whole consume operation."""

    def __init__(self, *, ttl=60, capacity=32, clock=time.monotonic):
        if not 0 < ttl <= 60 or not 1 <= capacity <= 128:
            raise ValueError("Choose a TTL up to 60 seconds and capacity up to 128.")
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self._grants = {}
        self._lock = threading.Lock()

    def issue(self, binding: Binding, next_path="/") -> str:
        from .auth import validate_next_path
        with self._lock:
            now = self.clock()
            self._grants = {key: record for key, record in self._grants.items() if record[0] > now}
            if len(self._grants) >= self.capacity:
                raise BrowserEntryUnavailable("Browser entry is busy. Wait 60 seconds. " + REOPEN)
            ticket = secrets.token_urlsafe(32)
            self._grants[hashlib.sha256(ticket.encode("ascii")).digest()] = (
                now + self.ttl, binding, validate_next_path(next_path))
            return ticket

    def consume(self, ticket: str, binding: Binding) -> str | None:
        if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
            return None
        key = hashlib.sha256(ticket.encode("ascii")).digest()
        with self._lock:
            record = self._grants.get(key)
            if record is None:
                return None
            if record[0] <= self.clock():
                del self._grants[key]
                return None
            if record[1] != binding:
                return None
            del self._grants[key]
            return record[2]


def loopback_host(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return host == "localhost"


def local_origin(request) -> str | None:
    """Require HTTPS, both real socket ends local, and the explicit exact Origin."""
    from .auth import allowed_origin
    from .deployment import validate_origin
    if not request.secure or request.transport is None:
        return None
    for name in ("peername", "sockname"):
        address = request.transport.get_extra_info(name)
        if not address or not loopback_host(address[0]) or address[0] == "localhost":
            return None
    try:
        origin = validate_origin(request.headers.get("Origin", ""))
        if (origin != f"{request.scheme}://{request.host}" or not allowed_origin(request)
                or not loopback_host(urlsplit(origin).hostname)):
            return None
        return origin
    except ValueError:
        return None


def initialize(app) -> None:
    app["browser_entry"] = None
    if not is_windows():
        return
    try:
        token = _control_token(app["service"].data_dir, current_owner_sid())
        if not hmac.compare_digest(token.encode(), app["control_token"].encode()):
            return
    except BrowserEntryUnavailable:
        return
    app["browser_entry"] = GrantStore()


def _binding(request, origin):
    from .auth import data_identity
    service = request.app["service"]
    sid = current_owner_sid()
    if not hmac.compare_digest(_control_token(service.data_dir, sid).encode(), request.app["control_token"].encode()):
        raise BrowserEntryUnavailable("The owner control credential changed. " + REOPEN)
    try:
        identity = data_identity(service.data_dir)
    except (OSError, ValueError):
        raise BrowserEntryUnavailable("The private owner data identity is unavailable. " + REOPEN) from None
    return Binding(service.update_manager.running_identity["instanceId"], identity, origin, sid)


async def issue(request):
    from aiohttp import web
    authorization = request.headers.get("Authorization", "")
    if not (authorization.startswith("Bearer ") and
            hmac.compare_digest(authorization[7:].encode(), request.app["control_token"].encode())):
        return web.json_response({"error": "The owner control credential is required."}, status=403)
    origin = local_origin(request)
    if not origin:
        return web.json_response({"error": "Use the exact HTTPS local owner origin."}, status=403)
    store = request.app["browser_entry"]
    if not is_windows() or store is None:
        return web.json_response({"error": "Windows owner browser entry is not available. " + REOPEN}, status=503)
    try:
        if request.content_type != "application/json":
            return web.json_response({"error": "Use a JSON owner entry request."}, status=400)
        body = await request.clone(client_max_size=4096).json()
        # Observe credential/host changes after the body-read await as well.
        if not hmac.compare_digest(authorization[7:].encode(), request.app["control_token"].encode()):
            return web.json_response({"error": "The owner control credential changed."}, status=403)
        binding = _binding(request, origin)
        identity = request.app["service"].update_manager.running_identity
        package = _package_identity(identity)
        if package is None:
            raise BrowserEntryUnavailable("Cannot verify the running package identity for browser entry.")
        if (not isinstance(body, dict) or set(body) - {"instanceId", "dataIdentity", "version", "revision", "ownerSid", "next"}
                or _package_identity(body) != package
                or any(body.get(key) != value for key, value in {
                    "instanceId": binding.instance_id, "dataIdentity": binding.data_identity,
                    "ownerSid": binding.owner_sid}.items())):
            return web.json_response({"error": "The owner host identity changed. " + REOPEN}, status=409)
        ticket = store.issue(binding, body.get("next"))
    except BrowserEntryUnavailable as exc:
        return web.json_response({"error": str(exc)}, status=503)
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid owner entry request. " + REOPEN}, status=400)
    return web.json_response({"ticket": ticket, "expiresIn": store.ttl},
                             headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


async def redeem(request):
    from aiohttp import web
    from .auth import CSRF_COOKIE, SESSION_COOKIE, _verified, new_session
    origin = local_origin(request)
    store = request.app["browser_entry"]
    if not origin or not is_windows() or store is None or request.content_type != "application/x-www-form-urlencoded":
        return web.Response(text=REOPEN, status=403)
    form = await request.clone(client_max_size=4096).post()
    csrf = request.cookies.get(CSRF_COOKIE)
    try:
        valid_csrf = (csrf and hmac.compare_digest(str(form.get("csrf", "")).encode(), csrf.encode())
                      and _verified(request.app["session_secret"], csrf, 900, "csrf"))
    except (TypeError, ValueError):
        valid_csrf = False
    if (not valid_csrf or set(form) != {"csrf", "ticket"}
            or any(len(form.getall(key)) != 1 for key in form)):
        return web.Response(text=REOPEN, status=403)
    try:
        target = store.consume(form.get("ticket"), _binding(request, origin))
    except BrowserEntryUnavailable:
        target = None
    if target is None:
        return web.Response(text=REOPEN, status=403)
    response = web.HTTPSeeOther(target)
    response.set_cookie(SESSION_COOKIE, new_session(request.app["session_secret"]),
                        max_age=request.app["server_config"]["session_ttl_seconds"],
                        httponly=True, samesite="Strict", secure=True, path="/")
    response.del_cookie(CSRF_COOKIE, path="/")
    raise response


async def owner_browser_url(data_dir: Path, config: dict) -> str:
    """Pinned owner request; no redirect/proxy/HTTP fallback or uncertain reissue.

    The returned URL contains a single-use grant, never the control bearer.
    Callers must not print it or include browser backend exceptions in output.
    """
    import aiohttp
    from .auth import data_identity
    from .update_readiness import running_identity
    urls, pin = _owner_probe_targets(data_dir, config)
    if not isinstance(pin, aiohttp.Fingerprint) or len(pin.fingerprint) != 32:
        raise BrowserEntryUnavailable("Windows owner browser entry requires an exact SHA-256 TLS leaf pin.")
    urls = [url for url in urls if urlsplit(url).scheme == "https"
            and urlsplit(url).hostname in {"127.0.0.1", "::1"}]
    if not urls:
        raise BrowserEntryUnavailable("No pinned HTTPS loopback listener is configured.")
    sid = current_owner_sid()
    token = _control_token(data_dir, sid)
    package = _package_identity(running_identity())
    if package is None:
        raise BrowserEntryUnavailable("Cannot verify the installed package identity for browser entry.")
    version, revision = package
    headers = {"Authorization": "Bearer " + token}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as client:
        for url in urls:
            origin = url.removesuffix("/api/health")
            try:
                async with client.get(url, ssl=pin, allow_redirects=False, headers=headers) as response:
                    if response.status != 200:
                        continue
                    health = await response.json()
                if (not isinstance(health, dict) or health.get("ok") is not True
                        or health.get("app") != "amplifier-unified" or _package_identity(health) != package
                        or health.get("dataIdentity") != data_identity(data_dir)
                        or not isinstance(health.get("instanceId"), str)
                        or not re.fullmatch("[a-f0-9]{32}", health["instanceId"])):
                    continue
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                continue
            try:
                async with client.post(origin + ISSUE_PATH, ssl=pin, allow_redirects=False,
                                       headers={**headers, "Origin": origin}, json={
                                           "instanceId": health["instanceId"], "dataIdentity": health["dataIdentity"],
                                           "version": version, "revision": revision, "ownerSid": sid}) as response:
                    if response.status != 200:
                        raise BrowserEntryUnavailable("The owner host refused browser entry. " + REOPEN)
                    result = await response.json()
                ticket = result.get("ticket") if isinstance(result, dict) else None
                if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
                    raise ValueError("Invalid ticket")
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                raise BrowserEntryUnavailable("Browser entry response was not confirmed; no retry was made. " + REOPEN) from None
            return origin + "/login#ticket=" + ticket
    raise BrowserEntryUnavailable("The owned local host is not reachable or its identity does not match. " + REOPEN)
