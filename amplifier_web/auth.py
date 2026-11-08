"""aiohttp-native PAM authentication and independent control credentials."""
from __future__ import annotations

import base64
import getpass
import hashlib
import html
import hmac
import json
import os
import re
from pathlib import Path
import secrets
import time
from urllib.parse import quote, urlsplit

from aiohttp import web
from filelock import FileLock

from .deployment import validate_origin, write_private
from . import browser_entry

SESSION_COOKIE = "amplifier_unified_session"
CSRF_COOKIE = "amplifier_unified_csrf"
_WINDOWS = os.name == "nt"


def validated_data_dir(data_dir: Path, *, create: bool = False) -> Path:
    """Keep the original Windows spelling until the native boundary checks it."""
    if not _WINDOWS:
        return Path(data_dir).expanduser().resolve()
    from . import windows_files
    root = windows_files.address(data_dir)
    with windows_files.directory(root, owned_root=root, create=create):
        return root


def auth_dir(data_dir: Path) -> Path:
    if _WINDOWS:
        from . import windows_files
        root = windows_files.address(data_dir)
        path = root / "config" / "auth"
        with windows_files.directory(path, owned_root=root, create=True):
            return path
    path = Path(data_dir).expanduser().resolve() / "config" / "auth"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _secret_path(data_dir: Path) -> Path:
    return auth_dir(data_dir) / "session-secret"


def control_token_path(data_dir: Path) -> Path:
    return auth_dir(data_dir) / "control-token"


def _read_or_create(path: Path) -> str:
    # Concurrent startup must converge on one credential rather than letting
    # two processes generate and retain different in-memory values.
    with FileLock(str(path) + ".lock"):
        try:
            os.chmod(path, 0o600)
            value = path.read_text().strip()
        except FileNotFoundError:
            value = secrets.token_urlsafe(48)
            write_private(path, value + "\n")
        if value:
            return value
        raise ValueError(f"Private credential is empty: {path}")


def _windows_credential_value(content: bytes) -> str:
    # The existing token_urlsafe(48) format, with one optional final LF.
    if not re.fullmatch(rb"[A-Za-z0-9_-]{64}\n?", content):
        raise ValueError("Existing Windows private credential is empty or malformed.")
    return content.removesuffix(b"\n").decode("ascii")


def _windows_credential(data_dir: Path, name: str) -> str:
    from . import windows_files
    root = windows_files.address(data_dir)
    with windows_files.directory(root / "config" / "auth", owned_root=root, create=True) as pinned:
        try:
            return _windows_credential_value(pinned.read_private(name, limit=65))
        except FileNotFoundError:
            value = secrets.token_urlsafe(48)
            try:
                pinned.publish_private(name, (value + "\n").encode("ascii"), replace=False)
            except FileExistsError:
                # Only a clean no-replace collision converges. An uncertain
                # publication/cleanup failure must propagate, not be replayed.
                pass
            return _windows_credential_value(pinned.read_private(name, limit=65))


def session_secret(data_dir: Path) -> str:
    if _WINDOWS:
        return _windows_credential(data_dir, "session-secret")
    return _read_or_create(_secret_path(data_dir))


def control_token(data_dir: Path) -> str:
    """Return the process-owner's local control token without displaying it."""
    if _WINDOWS:
        return _windows_credential(data_dir, "control-token")
    return _read_or_create(control_token_path(data_dir))


def data_identity(data_dir: Path) -> str:
    """Return the stable, private-data-directory identity used by local clients."""
    if _WINDOWS:
        return hashlib.sha256(str(validated_data_dir(data_dir)).encode()).hexdigest()
    return hashlib.sha256(str(Path(data_dir).expanduser().resolve()).encode()).hexdigest()


def history_scope(secret: str, credential: str, data_dir: Path) -> str:
    """Opaque presentation partition, not a credential or a navigation record.

    Only call after authentication. Reload/reconnect keep the same partition;
    a new sign-in, process owner, data root or secret gets a different one.
    Domain separation prevents this value from authenticating as a session.
    """
    owner = (browser_entry.current_owner_sid() if _WINDOWS else
             os.geteuid() if hasattr(os, "geteuid") else getpass.getuser())
    payload = json.dumps(["navigation-history-v1", owner,
                          data_identity(data_dir), credential], separators=(",", ":"))
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def with_history_scope(request: web.Request, snapshot: dict) -> dict:
    """Decorate this request's browser response, never shared/persisted state."""
    scope = request.get("history_scope")
    if scope and snapshot.get("client", {}).get("kind") == "web":
        return {**snapshot, "client": {**snapshot["client"], "historyScope": scope}}
    return snapshot


def validate_next_path(value: str | None) -> str:
    if not value or not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return "/"
    if "\\" in value or not value.startswith("/") or value.startswith("//"):
        return "/"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or ".." in parsed.path.split("/"):
        return "/"
    if "://" in value.lower() or any(scheme in value.lower() for scheme in ("javascript:", "data:", "file:")):
        return "/"
    return value


def login_url(next_path: str | None) -> str:
    target = validate_next_path(next_path)
    return "/login" if target == "/" else "/login?next=" + quote(target, safe="")


def _signed(secret: str, payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def _verified(secret: str, value: str | None, ttl: int, kind: str) -> bool:
    if not value or "." not in value:
        return False
    encoded, signature = value.rsplit(".", 1)
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        issued = payload["issued"]
        if type(issued) is not int:
            return False
        age = time.time() - issued
        return payload["kind"] == kind and 0 <= age <= ttl
    except (OverflowError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def new_session(secret: str) -> str:
    return _signed(secret, {"kind": "session", "issued": int(time.time()), "nonce": secrets.token_urlsafe(16)})


def new_csrf(secret: str) -> str:
    return _signed(secret, {"kind": "csrf", "issued": int(time.time()), "nonce": secrets.token_urlsafe(16)})


def authenticate_pam(username: str, password: str) -> bool:
    """PAM is deliberately restricted to the account owning this process."""
    try:
        import pwd
        import pam
        if username != pwd.getpwuid(os.geteuid()).pw_name:
            return False
        return bool(pam.authenticate(username, password, service="login"))
    except Exception:
        return False


def allowed_origin(request: web.Request) -> bool:
    """Require exact configured origins; never trust proxy forwarding headers."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        origin = validate_origin(origin)
        current = validate_origin(f"{request.scheme}://{request.host}")
    except ValueError:
        return False
    return origin == current and origin in request.app["allowed_origins"]


def is_api_request(request: web.Request) -> bool:
    return request.path.startswith("/api/")


def is_entry_navigation(request: web.Request) -> bool:
    """Allow external links to entry pages, never cross-site API or form calls.

    A browser link and its /login redirect can both carry cross-site Fetch
    Metadata. They still pass the normal Host, Origin, and authentication gates.
    Chromium clears destination to the literal "empty" when our service worker
    forwards a document navigation with fetch(event.request). Accept that only
    for the two entry paths the shipped worker forwards, not APIs or callbacks.
    Missing destination is not "empty"; ordinary script fetches cannot choose
    navigate mode. The worker excludes frames, and CSP still blocks embedding.
    """
    return (
        request.method in {"GET", "HEAD"}
        and request.path in {"/", "/login", "/setup", "/oauth/mcp/callback", "/oauth/mcp/complete", "/devices/pair", "/devices",
                             "/connect", "/connect/verify", "/connect/code"}
        and "Origin" not in request.headers
        and request.headers.get("Sec-Fetch-Mode") == "navigate"
        and (
            request.headers.get("Sec-Fetch-Dest") == "document"
            or (request.path in {"/", "/login"} and request.headers.get("Sec-Fetch-Dest") == "empty")
        )
    )


@web.middleware
async def auth_required(request: web.Request, handler):
    from .device_connections import PREFIX, extension_request, permits_device
    extension = extension_request(request)

    async def authenticated(credential):
        scope = history_scope(request.app["session_secret"], credential,
                              request.app["service"].data_dir)
        request["history_scope"] = scope
        response = await handler(request)
        if not response.prepared:
            response.headers["X-Amplifier-History-Scope"] = scope
        return response

    cross_site = request.headers.get("Sec-Fetch-Site") == "cross-site"
    if not extension and ((cross_site and not is_entry_navigation(request)) or not allowed_origin(request)):
        return web.json_response({"error": "Cross-origin requests are not permitted."}, status=403)
    if request.path == browser_entry.ISSUE_PATH:
        authorization = request.headers.get("Authorization", "")
        if (request.method != "POST" or not authorization.startswith("Bearer ")
                or not hmac.compare_digest(authorization[7:].encode(), request.app["control_token"].encode())):
            return web.json_response({"error": "The owner control credential is required."}, status=403)
        return await handler(request)
    if request.path == browser_entry.REDEEM_PATH and request.method == "POST":
        # Only this exact public mutation. The handler requires HTTPS, Origin
        # and signed form CSRF before atomically consuming the one-use ticket.
        return await handler(request)
    if extension and request.method == 'OPTIONS':
        return await handler(request)
    if ((request.path in {PREFIX, PREFIX + '/qr'} and request.method in {'GET', 'HEAD'})
            or (request.path in {PREFIX + '/enroll', PREFIX + '/token'} and request.method == 'POST')):
        return await handler(request)
    if extension and not request.headers.get('Authorization', '').lower().startswith('bearer amd_'):
        return web.json_response({'error': 'An app credential is required.'}, status=401)
    # Public, immutable UI resources only. Authentication still gates the SPA,
    # every API, and all user-authored files/canvas documents.
    public = {"/oauth/mcp/callback", "/oauth/mcp/complete", "/login", "/setup", "/api/ca", "/ca.crt", "/api/health",
              "/manifest.webmanifest", "/sw.js", "/pwa.js", "/offline.html", "/app-pages.css", "/favicon.ico", "/browser-entry.js",
              "/branding/favicons/favicon.ico", "/branding/favicons/favicon-32.png",
              "/branding/favicons/apple-touch-icon.png", "/branding/icons/amplifier-icon-128.png",
              "/branding/pwa/pwa-192.png", "/branding/pwa/pwa-512.png"}
    if request.path in public and request.method in {"GET", "HEAD"}:
        return await handler(request)
    # Deliberately published snapshots have their own unguessable bearer link.
    # This exemption grants no app/API/file authority; the handler checks expiry
    # and revocation on every request and serves escaped inert snapshot content.
    if request.method in {'GET', 'HEAD'} and re.fullmatch(r'/share/[A-Za-z0-9_-]{43}', request.path):
        return await handler(request)
    if request.path in {'/api/terminal/redeem', '/api/devices/pair', '/api/devices/pair/token'} and request.method == 'POST':
        # This narrowly scoped endpoint verifies a short-lived, single-use setup
        # grant in its handler. It is never an alternate general API credential.
        return await handler(request)
    if request.path == "/login" and request.method == "POST":
        csrf = request.cookies.get(CSRF_COOKIE)
        submitted = (await request.post()).get("csrf", "")
        secret = request.app["session_secret"]
        if not csrf or not hmac.compare_digest(str(submitted), csrf) or not _verified(secret, csrf, 900, "csrf"):
            return web.Response(text="Login failed.", status=403)
        return await handler(request)
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer ") and hmac.compare_digest(authorization[7:], request.app["control_token"]):
        return await authenticated(authorization[7:])
    if authorization.lower().startswith(('bearer amt_', 'bearer amc_', 'bearer amd_')):
        devices = request.app['terminal_setup'].devices
        identity = devices.identify(authorization[7:])
        if identity:
            device = devices.read()['devices'].get(identity)
            if device is None:
                return web.json_response({'error': 'Device access was removed.'}, status=401)
            # Observe revocation before any await, including the JSON body read.
            with devices.connection(identity):
                scope = device.get('scope')
                if scope == 'device-v1':
                    request['connection_device'] = identity
                    if not permits_device(request):
                        return web.json_response({'error': 'This connection cannot access that operation.'}, status=403)
                elif scope is not None:
                    request['companion_device'] = identity
                    from .device_pairing import SCOPE, permits_companion
                    if scope != SCOPE or not await permits_companion(request):
                        return web.json_response({'error': 'This device is not permitted to perform that operation.'}, status=403)
                if devices.identify(authorization[7:]) != identity:
                    return web.json_response({'error': 'Device access was removed.'}, status=401)
                return await handler(request)
        return web.json_response({'error': 'Device access was removed or is invalid.'}, status=401)
    if _verified(request.app["session_secret"], request.cookies.get(SESSION_COOKIE), request.app["server_config"]["session_ttl_seconds"], "session"):
        return await authenticated(request.cookies[SESSION_COOKIE])
    if is_api_request(request):
        return web.json_response({"error": "Authentication required."}, status=401)
    requested = request.path + (("?" + request.query_string) if request.query_string else "")
    raise web.HTTPTemporaryRedirect(login_url(requested))


async def login_page(request: web.Request) -> web.Response:
    secret = request.app["session_secret"]
    csrf = request.cookies.get(CSRF_COOKIE)
    # Preserve valid issuance for favicon redirects and concurrent login forms.
    if not _verified(secret, csrf, 900, "csrf"):
        csrf = new_csrf(secret)
    filename = "windows-login.html" if browser_entry.is_windows() else "login.html"
    page = (Path(__file__).parent / "static" / filename).read_text(encoding="utf-8")
    next_path = html.escape(validate_next_path(request.query.get("next")), quote=True)
    error = "Could not sign in. Check your host username and password and try again." if request.query.get("error") else ""
    response = web.Response(text=page.replace("__CSRF__", csrf).replace("__NEXT__", next_path).replace("__ERROR__", error), content_type="text/html")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, samesite="Strict", secure=request.secure, path="/")
    return response


async def post_login(request: web.Request) -> web.Response:
    if browser_entry.is_windows():
        return web.Response(text=browser_entry.REOPEN, status=403)
    form = await request.post()
    if not authenticate_pam(str(form.get("username", "")), str(form.get("password", ""))):
        url = login_url(request.query.get("next"))
        raise web.HTTPSeeOther(url + ("&" if "?" in url else "?") + "error=1")
    response = web.HTTPSeeOther(validate_next_path(request.query.get("next")))
    response.set_cookie(SESSION_COOKIE, new_session(request.app["session_secret"]), max_age=request.app["server_config"]["session_ttl_seconds"],
                        httponly=True, samesite="Strict", secure=request.secure, path="/")
    response.del_cookie(CSRF_COOKIE, path="/")
    raise response
