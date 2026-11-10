"""No-listener protocol checks; native-only tests exercise real HTTPS separately."""
from argparse import Namespace
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from amplifier_web import __version__, auth, browser_entry as entry, cli, server
from amplifier_web.deployment import load_server_config

SID = "S-1-5-21-100-200-300-1001"
REVISION = "a" * 40
ORIGIN = "https://127.0.0.1:8941"


@pytest.fixture
def tmp_path(tmp_path, request):
    # Windows tests select a new private profile child, not the runner TEMP ACL.
    return request.getfixturevalue("native_root") if os.name == "nt" else tmp_path


def binding():
    return entry.Binding("b" * 32, "c" * 64, ORIGIN, SID)


def test_hash_only_binding_expiry_capacity_and_atomic_replay():
    now = [10]
    store = entry.GrantStore(capacity=1, clock=lambda: now[0])
    ticket = store.issue(binding(), "/safe?tab=one")
    assert list(store._grants) == [hashlib.sha256(ticket.encode()).digest()]
    assert ticket not in repr(store._grants)
    with pytest.raises(entry.BrowserEntryUnavailable, match="busy"):
        store.issue(binding())
    for key in ("instance_id", "data_identity", "owner_sid", "origin"):
        assert store.consume(ticket, replace(binding(), **{key: "wrong"})) is None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.consume(ticket, binding()), range(32)))
    assert results.count("/safe?tab=one") == 1 and results.count(None) == 31
    expired = store.issue(binding())
    assert entry.GrantStore().consume(expired, binding()) is None
    now[0] += 60
    assert store.consume(expired, binding()) is None
    assert store.consume(store.issue(binding(), "//attacker.example"), binding()) == "/"


@pytest.mark.parametrize("value", [None, "", "x" * 42, "x" * 44, "?" * 43, "é" * 43, ["x"]])
def test_malformed_tickets(value):
    assert entry.GrantStore().consume(value, binding()) is None


@pytest.mark.parametrize("options", [{"ttl": 61}, {"ttl": 0}, {"capacity": 0}, {"capacity": 129}])
def test_invalid_store_bounds(options):
    with pytest.raises(ValueError):
        entry.GrantStore(**options)


async def request(app, path, *, method="POST", headers=None, body=None, secure=True, remote=None):
    """Real aiohttp Request/body/parser, real middleware/handlers, no socket."""
    headers = {"Host": "127.0.0.1:8941", "Origin": ORIGIN, "Sec-Fetch-Site": "same-origin", **(headers or {})}
    headers = {key: value for key, value in headers.items() if value is not None}
    if isinstance(body, dict):
        raw = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    else:
        raw = (body or "").encode()
    headers["Content-Length"] = str(len(raw))
    protocol = Mock(_reading_paused=False)
    payload = aiohttp.StreamReader(protocol, 8192)
    payload.feed_data(raw)
    payload.feed_eof()
    def extra(name):
        if name in {"peername", "sockname"}:
            return ("192.0.2.20" if name == remote else "127.0.0.1", 8941)
    req = make_mocked_request(method, path, headers=headers, app=app, payload=payload,
                             transport=SimpleNamespace(get_extra_info=extra),
                             sslcontext=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER) if secure else None)
    match = await app.router.resolve(req)
    match.add_app(app)
    req._match_info = match
    async def authorized(req):
        return await auth.auth_required(req, match.handler)
    try:
        return await server.boundaries(req, authorized)
    except web.HTTPException as response:
        return response


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from amplifier_web import windows_files
    from test_windows_auth import PrivateFiles
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    # Portable filesystem API model, not native storage qualification. Keep the
    # actual public reader/validation in the protocol path.
    files = PrivateFiles()
    files.content[tmp_path / "config/auth/session-secret"] = b"s" * 64
    files.content[tmp_path / "config/auth/control-token"] = b"c" * 64
    monkeypatch.setattr(windows_files, "directory", files.directory)
    app = web.Application()
    app["control_token"], app["session_secret"] = "c" * 64, "s" * 64
    app["server_config"] = {"session_ttl_seconds": 120}
    app["allowed_origins"] = frozenset({ORIGIN, ORIGIN.replace("https:", "http:")})
    app["permitted_hosts"] = frozenset({"127.0.0.1", "localhost", "::1"})
    app["service"] = SimpleNamespace(data_dir=tmp_path, update_manager=SimpleNamespace(
        running_identity={"instanceId": "b" * 32, "version": __version__, "revision": REVISION}))
    entry.initialize(app)
    app.router.add_post(entry.ISSUE_PATH, entry.issue)
    app.router.add_post(entry.REDEEM_PATH, entry.redeem)
    app.router.add_get("/login", auth.login_page)
    app.router.add_post("/login", auth.post_login)
    async def protected(req):
        return web.json_response({"authenticated": True})
    app.router.add_get("/api/protected", protected)
    return app


def issue_body(app, **changes):
    return {"instanceId": "b" * 32, "version": __version__, "revision": REVISION,
            "dataIdentity": auth.data_identity(app["service"].data_dir), "ownerSid": SID, **changes}


async def issue(app, **changes):
    response = await request(app, entry.ISSUE_PATH, headers={"Authorization": "Bearer " + app["control_token"]},
                             body=issue_body(app, **changes))
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    return json.loads(response.body)["ticket"]


async def redeem(app, ticket, *, csrf=None, headers=None, body=None, **kwargs):
    csrf = csrf or auth.new_csrf(app["session_secret"])
    return await request(app, entry.REDEEM_PATH, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Cookie": f"{auth.CSRF_COOKIE}={csrf}",
        **(headers or {})}, body=body or urlencode({"csrf": csrf, "ticket": ticket}), **kwargs)


async def test_grant_cookie_protects_actual_auth_middleware_and_replay(owner):
    assert (await request(owner, "/api/protected", method="GET")).status == 401
    ticket = await issue(owner, next="/safe?tab=one")
    results = await asyncio.gather(*(redeem(owner, ticket) for _ in range(8)))
    assert sorted(r.status for r in results) == [303] + [403] * 7
    response = next(r for r in results if r.status == 303)
    assert response.headers["Location"] == "/safe?tab=one"
    cookie = response.cookies[auth.SESSION_COOKIE]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict" and cookie["path"] == "/"
    assert (await request(owner, "/api/protected", method="GET", headers={
        "Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"})).status == 200
    assert (await redeem(owner, ticket)).status == 403


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
    {"Authorization": "Bearer amt_" + "x" * 64}, {"Authorization": "Bearer amc_" + "x" * 64},
    {"Authorization": "Bearer amd_" + "x" * 64},
    {"Cookie": f"{auth.SESSION_COOKIE}=" + auth.new_session("s" * 64)}])
async def test_issue_requires_explicit_control_not_session_or_device(owner, headers):
    assert (await request(owner, entry.ISSUE_PATH, headers=headers, body=issue_body(owner))).status == 403
    assert not owner["browser_entry"]._grants


@pytest.mark.parametrize("key", ["instanceId", "version", "revision", "dataIdentity", "ownerSid"])
async def test_wrong_health_identity_never_issues(owner, key):
    response = await request(owner, entry.ISSUE_PATH, headers={"Authorization": "Bearer " + owner["control_token"]},
                             body=issue_body(owner, **{key: "wrong"}))
    assert response.status == 409 and not owner["browser_entry"]._grants


@pytest.mark.parametrize("local,submitted,drop,expected", [
    (None, None, None, 200),
    (None, REVISION, None, 409),
    (REVISION, None, None, 409),
    (REVISION, "f" * 40, None, 409),
    (None, None, "revision", 409),
    (None, None, "version", 409),
    ("", "", None, 503),
])
async def test_issue_wheel_identity_is_explicit_not_a_provenance_fallback(owner, local, submitted, drop, expected):
    owner["service"].update_manager.running_identity["revision"] = local
    body = issue_body(owner, revision=submitted)
    if drop:
        del body[drop]
    response = await request(owner, entry.ISSUE_PATH,
                             headers={"Authorization": "Bearer " + owner["control_token"]}, body=body)
    assert response.status == expected
    assert len(owner["browser_entry"]._grants) == (1 if expected == 200 else 0)


@pytest.mark.parametrize("identity", [
    None, {}, {"version": __version__}, {"version": "wrong", "revision": None},
    *({"version": __version__, "revision": bad} for bad in ("", False, 0, [], "x" * 40, "a" * 39, "A" * 40)),
])
def test_package_identity_does_not_invent_or_normalize_source(identity):
    assert entry._package_identity(identity) is None


@pytest.mark.parametrize("headers", [{"Origin": "https://attacker.example"}, {"Origin": "null"},
    {"Origin": "chrome-extension://" + "a" * 32},
    {"Origin": None}, {"Host": "attacker.example"}, {"Sec-Fetch-Site": "cross-site"}])
async def test_cross_origin_host_csrf_rejection_preserves_ticket(owner, headers):
    ticket = await issue(owner)
    assert (await redeem(owner, ticket, headers=headers)).status == 403
    assert (await redeem(owner, ticket)).status == 303


async def test_connection_extension_transport_cannot_issue_owner_ticket(owner):
    # Device enrollment/self CORS exemptions must not expand the Windows
    # owner-browser boundary when both routes share the authentication middleware.
    response = await request(owner, entry.ISSUE_PATH, headers={
        "Origin": "chrome-extension://" + "a" * 32,
        "Authorization": "Bearer " + owner["control_token"],
    }, body=issue_body(owner))
    assert response.status == 403
    assert "Access-Control-Allow-Origin" not in response.headers
    assert not owner["browser_entry"]._grants


@pytest.mark.parametrize("remote", ["peername", "sockname"])
async def test_both_real_socket_ends_must_be_local(owner, remote):
    ticket = await issue(owner)
    assert (await redeem(owner, ticket, remote=remote)).status == 403
    assert (await redeem(owner, ticket)).status == 303


async def test_http_bad_csrf_duplicate_form_and_oversize(owner):
    ticket = await issue(owner)
    assert (await redeem(owner, ticket, secure=False, headers={"Origin": ORIGIN.replace("https:", "http:")})).status == 403
    assert (await redeem(owner, ticket, body=urlencode({"csrf": "wrong", "ticket": ticket}))).status == 403
    csrf = auth.new_csrf(owner["session_secret"])
    body = urlencode({"csrf": csrf, "ticket": ticket}) + "&ticket=" + ticket
    assert (await redeem(owner, ticket, csrf=csrf, body=body)).status == 403
    assert (await redeem(owner, ticket, body="csrf=" + "x" * 4096 + "&ticket=" + ticket)).status == 413
    response = await request(owner, entry.ISSUE_PATH, headers={"Authorization": "Bearer " + owner["control_token"]},
                             body=issue_body(owner, next="/" + "x" * 4096))
    assert response.status == 413
    assert (await redeem(owner, ticket)).status == 303


async def test_expiry_capacity_and_restart_owner_root_binding(owner, monkeypatch, tmp_path):
    now = [0]
    owner["browser_entry"].clock = lambda: now[0]
    owner["browser_entry"].capacity = 1
    ticket = await issue(owner)
    assert (await request(owner, entry.ISSUE_PATH, headers={"Authorization": "Bearer " + owner["control_token"]},
                          body=issue_body(owner))).status == 503
    monkeypatch.setattr(entry, "current_owner_sid", lambda: "S-1-5-21-999")
    assert (await redeem(owner, ticket)).status == 403
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    original = owner["service"].data_dir
    owner["service"].data_dir = tmp_path / "other"
    assert (await redeem(owner, ticket)).status == 403
    owner["service"].data_dir = original
    owner["service"].update_manager.running_identity["instanceId"] = "e" * 32
    assert (await redeem(owner, ticket)).status == 403
    owner["service"].update_manager.running_identity["instanceId"] = "b" * 32
    now[0] = 60
    assert (await redeem(owner, ticket)).status == 403


async def test_windows_page_no_password_and_posix_login_preserved(owner, monkeypatch):
    response = await request(owner, "/login", method="GET")
    assert 'src="/browser-entry.js"' in response.text and 'name="ticket"' in response.text
    assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
    assert "form-action 'self'" in response.headers["Content-Security-Policy"]
    monkeypatch.setattr(auth, "authenticate_pam", lambda *a: pytest.fail("Windows PAM"))
    csrf = response.cookies[auth.CSRF_COOKIE].value
    assert (await request(owner, "/login", body=urlencode({"csrf": csrf}), headers={
        "Content-Type": "application/x-www-form-urlencoded", "Cookie": f"{auth.CSRF_COOKIE}={csrf}"})).status == 403
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    monkeypatch.setattr(auth, "authenticate_pam", lambda u, p: (u, p) == ("owner", "synthetic"))
    response = await request(owner, "/login", body=urlencode({"csrf": csrf, "username": "owner", "password": "synthetic"}),
                             headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": f"{auth.CSRF_COOKIE}={csrf}"})
    assert response.status == 303 and response.cookies[auth.SESSION_COOKIE]["httponly"]


async def test_real_app_routes_and_static_parity_without_listener(tmp_path, monkeypatch):
    from test_service import Runtime
    # This portable route/static check starts with POSIX entry policy, then
    # selects the Windows page. Native public entry is qualified separately.
    monkeypatch.setattr(entry, "is_windows", lambda: False)
    app = await server.create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                                  background_updates=False, background_work=False, preload_providers=False)
    try:
        assert app["browser_entry"] is None
        assert (await request(app, entry.ISSUE_PATH, body={})).status == 403
        assert (await request(app, entry.REDEEM_PATH, body={})).status == 403
        monkeypatch.setattr(entry, "is_windows", lambda: True)
        assert 'src="/browser-entry.js"' in (await request(app, "/login", method="GET")).text
        response = await request(app, "/browser-entry.js", method="GET")
        assert response.status == 200
        root = Path(entry.__file__).parent.parent
        delivery = json.loads((root / "components/web-client.source.json").read_text())["delivery"]
        client = root / delivery["path"] if delivery["type"] == "directory" else root / ".build/web-client"
        for name in ("browser-entry.js", "windows-login.html"):
            assert (client / "public" / name).read_bytes() == (root / "amplifier_web/static" / name).read_bytes()
    finally:
        await app["service"].close()


async def test_http_no_pin_or_unsafe_pin_precedes_credential_read(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: pytest.fail("unverified credential read"))
    with pytest.raises(entry.BrowserEntryUnavailable, match="leaf-pinned HTTPS"):
        await entry.owner_browser_url(tmp_path, load_server_config(tmp_path))
    for pin in (None, False, True):
        monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([ORIGIN + "/api/health"], pin))
        with pytest.raises(entry.BrowserEntryUnavailable, match="exact SHA-256"):
            await entry.owner_browser_url(tmp_path, {})


async def test_public_launcher_existing_read_issue_cookie_and_pin_order_model(owner, monkeypatch):
    """Real public reader and handlers; only filesystem/HTTPS transport are fake."""
    from amplifier_web import update_readiness, windows_files
    events = []
    original_directory = windows_files.directory
    @contextmanager
    def directory(path, *, owned_root, create=False):
        events.append("existing-private-read")
        assert events[0] == "private-leaf-pin" and create is False
        with original_directory(path, owned_root=owned_root, create=create) as pinned:
            yield pinned
    monkeypatch.setattr(windows_files, "directory", directory)
    pin = aiohttp.Fingerprint(bytes(range(32)))
    def targets(*args):
        events.append("private-leaf-pin")
        return [ORIGIN + "/api/health"], pin
    monkeypatch.setattr(entry, "_owner_probe_targets", targets)
    identity = owner["service"].update_manager.running_identity
    monkeypatch.setattr(update_readiness, "running_identity", lambda: identity)
    class Response:
        async def __aenter__(self):
            self.response = await self.operation()
            self.status = self.response.status
            return self
        async def __aexit__(self, *a):
            pass
        async def json(self):
            return json.loads(self.response.body)
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        def check(self, kwargs):
            assert events[:2] == ["private-leaf-pin", "existing-private-read"]
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            assert kwargs["headers"]["Authorization"] == "Bearer " + owner["control_token"]
        def get(self, url, **kwargs):
            self.check(kwargs)
            assert url == ORIGIN + "/api/health"
            events.append("pinned-health")
            async def health():
                return web.json_response({"ok": True, "app": "amplifier-unified", **identity,
                    "dataIdentity": auth.data_identity(owner["service"].data_dir)})
            result = Response()
            result.operation = health
            return result
        def post(self, url, **kwargs):
            self.check(kwargs)
            assert url == ORIGIN + entry.ISSUE_PATH
            events.append("pinned-issue")
            result = Response()
            result.operation = lambda: request(owner, entry.ISSUE_PATH,
                                                headers=kwargs["headers"], body=kwargs["json"])
            return result
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    url = await entry.owner_browser_url(owner["service"].data_dir, {})
    assert owner["control_token"] not in url
    ticket = url.split("#ticket=")[1]
    response = await redeem(owner, ticket)
    assert response.status == 303
    cookie = response.cookies[auth.SESSION_COOKIE]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
    assert (await redeem(owner, ticket)).status == 403
    assert events.index("existing-private-read") < events.index("pinned-health") < events.index("pinned-issue")


async def test_launcher_unknown_post_is_not_replayed(tmp_path, monkeypatch):
    from amplifier_web import __version__, update_readiness
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([ORIGIN + "/api/health", "https://[::1]:8941/api/health"], pin))
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "c" * 64)
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": REVISION})
    calls = []
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def json(self):
            return {"ok": True, "app": "amplifier-unified", "version": __version__, "revision": REVISION,
                    "instanceId": "b" * 32, "dataIdentity": auth.data_identity(tmp_path)}
    class Client(Response):
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        def get(self, url, **kwargs):
            calls.append(("get", url))
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            assert kwargs["headers"]["Authorization"] == "Bearer " + "c" * 64
            return Response()
        def post(self, url, **kwargs):
            calls.append(("post", url))
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            assert kwargs["headers"]["Origin"] == ORIGIN
            raise TimeoutError("Unknown POST; never retry")
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    with pytest.raises(entry.BrowserEntryUnavailable, match="no retry"):
        await entry.owner_browser_url(tmp_path, {})
    assert calls == [("get", ORIGIN + "/api/health"), ("post", ORIGIN + entry.ISSUE_PATH)]


@pytest.mark.parametrize("local,remote,change", [
    (None, REVISION, {}),
    (REVISION, None, {}),
    (REVISION, "f" * 40, {}),
    (None, None, {"version": "0.0.0"}),
    (None, None, {"drop": "revision"}),
    (None, None, {"drop": "version"}),
    (None, None, {"revision": ""}),
    (None, None, {"revision": False}),
    (None, None, {"instanceId": "not-an-instance"}),
    (None, None, {"dataIdentity": "wrong"}),
])
async def test_launcher_package_mismatch_never_issues(tmp_path, monkeypatch, local, remote, change):
    from amplifier_web import update_readiness
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([ORIGIN + "/api/health"], pin))
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(entry, "read_owner_control_token", lambda *a: "c" * 64)
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": local})
    health = {"ok": True, "app": "amplifier-unified", "version": __version__, "revision": remote,
              "instanceId": "b" * 32, "dataIdentity": auth.data_identity(tmp_path)}
    health.update({key: value for key, value in change.items() if key != "drop"})
    if "drop" in change:
        del health[change["drop"]]
    calls = []
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def json(self):
            return health
    class Client(Response):
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        def get(self, url, **kwargs):
            calls.append("get")
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            return Response()
        def post(self, *a, **kw):
            pytest.fail("Mismatched package/host identity must never issue")
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    with pytest.raises(entry.BrowserEntryUnavailable, match="identity"):
        await entry.owner_browser_url(tmp_path, {})
    assert calls == ["get"]


@pytest.mark.parametrize("source", [
    {"url": "https://example.test/source.git", "vcs_info": {"vcs": "git", "commit_id": REVISION}},
    {"url": "file:///candidate.whl", "archive_info": {}},
    {"url": "file:///candidate.whl", "archive_info": {"hashes": {"sha256": "c" * 64}}},
])
async def test_launcher_success_uses_real_app_health_issue_cookie_in_memory(tmp_path, monkeypatch, source):
    from amplifier_web import __version__, update_readiness
    from test_service import Runtime
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    monkeypatch.setattr(entry, "read_owner_control_token",
                        lambda home, sid: (home / "config/auth/control-token").read_text().strip())
    # Exercise the real canonical metadata reader. A wheel has no VCS revision;
    # do not fabricate one in either the host or launcher to make this pass.
    monkeypatch.setattr(update_readiness.metadata, "distribution", lambda name: SimpleNamespace(
        locate_file=lambda path: Path(entry.__file__).parent,
        read_text=lambda name: json.dumps(source)))
    app = await server.create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                                  background_updates=False, background_work=False, preload_providers=False)
    identity = app["service"].update_manager.running_identity
    assert identity["revision"] == source.get("vcs_info", {}).get("commit_id")
    assert identity["version"] == __version__
    pin = aiohttp.Fingerprint(bytes(range(32)))
    monkeypatch.setattr(entry, "_owner_probe_targets", lambda *a: ([ORIGIN + "/api/health"], pin))
    calls = []
    class Response:
        async def __aenter__(self):
            self.response = await self.operation()
            self.status = self.response.status
            return self
        async def __aexit__(self, *a):
            pass
        async def json(self):
            return json.loads(self.response.body)
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        def operation(self, method, url, kwargs):
            assert kwargs["ssl"] is pin and kwargs["allow_redirects"] is False
            calls.append((method, url))
            response = Response()
            response.operation = lambda: request(app, url.removeprefix(ORIGIN), method=method,
                                                  headers=kwargs["headers"], body=kwargs.get("json"))
            return response
        def get(self, url, **kwargs):
            return self.operation("GET", url, kwargs)
        def post(self, url, **kwargs):
            assert kwargs["json"]["revision"] == identity["revision"]
            assert kwargs["json"]["version"] == __version__
            return self.operation("POST", url, kwargs)
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    try:
        url = await entry.owner_browser_url(tmp_path, {})
        assert url.startswith(ORIGIN + "/login#ticket=") and app["control_token"] not in url
        response = await redeem(app, url.split("#ticket=")[1])
        assert response.status == 303 and response.cookies[auth.SESSION_COOKIE]["secure"]
        assert calls == [("GET", ORIGIN + "/api/health"), ("POST", ORIGIN + entry.ISSUE_PATH)]
    finally:
        await app["service"].close()


@pytest.mark.parametrize("no_open", [True, False])
def test_foreground_opens_only_after_all_sockets_bound(tmp_path, monkeypatch, no_open):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr("amplifier_web.settings_migration.migrate_settings", lambda *a: None)
    monkeypatch.setattr("amplifier_web.host.config.load_config", lambda *a: None)
    monkeypatch.setattr(server, "create_app", lambda *a, **kw: object())
    calls = []
    monkeypatch.setattr(cli, "_open_browser_safely", lambda *a: calls.append("open"))
    class Timer:
        def __init__(self, delay, callback):
            assert calls == ["listening"]
            self.callback = callback
        def start(self):
            self.callback()
        def cancel(self):
            pass
    monkeypatch.setattr(cli.threading, "Timer", Timer)
    def run(app, **kwargs):
        assert calls == []
        calls.append("listening")
        kwargs["print"]("sockets bound")
    monkeypatch.setattr(web, "run_app", run)
    args = Namespace(port=None, public_origin=None, session_ttl_seconds=None, bind=None, host=None,
                     tls_cert=None, tls_key=None, workspace=str(tmp_path), no_open=no_open)
    cli._serve_unlocked(args, tmp_path)
    assert calls == (["listening"] if no_open else ["listening", "open"])


async def test_expired_csrf_preserves_grant(owner):
    ticket = await issue(owner)
    csrf = auth._signed(owner["session_secret"], {"kind": "csrf", "issued": 1})
    assert (await redeem(owner, ticket, csrf=csrf)).status == 403
    assert (await redeem(owner, ticket)).status == 303


def test_private_tls_read_failure_precedes_control_and_generic_probe(tmp_path, monkeypatch):
    from amplifier_web import windows_files
    def refuse(*a, **kw):
        raise PermissionError("unsafe private TLS storage")
    monkeypatch.setattr(windows_files, "directory", refuse)
    config = {"tls": {"method": "ca", "cert": "config/tls/leaf.crt", "key": "config/tls/leaf.key"},
              "bind": ["127.0.0.1"], "port": 8941}
    with pytest.raises(entry.BrowserEntryUnavailable, match="private SHA-256 TLS leaf pin"):
        entry._owner_probe_targets(tmp_path, config)


@pytest.mark.parametrize("no_open", [True, False])
def test_explicit_cli_open_no_secret_output(tmp_path, monkeypatch, capsys, no_open):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    calls = []
    url = ORIGIN + "/login#ticket=" + "a" * 43
    async def grant(*a):
        calls.append("issue")
        return url
    monkeypatch.setattr(entry, "owner_browser_url", grant)
    monkeypatch.setattr(cli.webbrowser, "open", lambda value: calls.append(value) or True)
    monkeypatch.setattr(sys, "argv", ["amplifier-unified", "--data-dir", str(tmp_path),
                                    *(["--no-open"] if no_open else []), "open"])
    cli.main()
    assert calls == ([] if no_open else ["issue", url])
    captured = capsys.readouterr()
    assert "ticket=" not in captured.out + captured.err


def test_backend_exception_never_displays_grant(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    async def grant(*a):
        return ORIGIN + "/login#ticket=" + "a" * 43
    monkeypatch.setattr(entry, "owner_browser_url", grant)
    def broken(value):
        raise OSError(value)
    monkeypatch.setattr(cli.webbrowser, "open", broken)
    with pytest.raises(entry.BrowserEntryUnavailable) as error:
        cli._open_browser(Namespace(no_open=False), tmp_path, {})
    assert "ticket=" not in str(error.value)


def test_shipped_script_strips_fragment_first_and_requires_https():
    source = Path(entry.__file__).parent / "static/browser-entry.js"
    harness = r'''
const fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync(process.argv[1], "utf8");
for (const [origin, action, valid] of [
  ["https://127.0.0.1:8941", "https://127.0.0.1:8941/api/browser-entry/redeem", true],
  ["http://127.0.0.1:8941", "http://127.0.0.1:8941/api/browser-entry/redeem", false],
  ["https://127.0.0.1:8941", "https://attacker.example/api/browser-entry/redeem", false],
  ["https://remote.example", "https://remote.example/api/browser-entry/redeem", false],
]) {
  const effects = [], url = new URL(origin + "/login#ticket=" + "a".repeat(43));
  const form = {action, elements:{ticket:{value:""}}, submit:()=>effects.push("submit")};
  vm.runInNewContext(source, {URL, URLSearchParams,
    window:{location:url, history:{replaceState:(_, __, value)=>{
      if(value.includes("ticket")) throw Error("fragment retained"); effects.push("strip");
    }}}, document:{getElementById:id=>id==="browser-entry"?form:{textContent:""}},
  });
  if(JSON.stringify(effects)!==JSON.stringify(valid?["strip","submit"]:["strip"])) throw Error(effects);
}
'''
    subprocess.run(["node", "-e", harness, str(source)], check=True, capture_output=True, timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows HTTPS/private-storage combined boundary")
async def test_native_actual_app_tls_grant_cookie_and_replay(native_root, monkeypatch, aiohttp_client, unused_tcp_port):
    """Actual public owner entry with native private files and HTTPS."""
    from aiohttp.test_utils import TestClient, TestServer
    from amplifier_web import tls, windows_files
    from test_service import Runtime
    home = native_root / "host"
    for name in ("host", "workspace", "shared", "amplifier", "terminal"):
        with windows_files.directory(native_root / name, owned_root=native_root, create=True):
            pass
    for name, folder in (("AMPLIFIER_WEB_HOME", "host"), ("AMPLIFIER_HOME", "amplifier"),
                         ("AMPLIFIER_SESSION_STATE_HOME", "shared"), ("AMPLIFIER_TERMINAL_HOME", "terminal")):
        monkeypatch.setenv(name, str(native_root / folder))
    config = load_server_config(home, overrides={"port": unused_tcp_port, "bind": ["127.0.0.1"]})
    config = tls.setup_local_ca(home, config)
    app = await server.create_app(home, workspace=native_root / "workspace", runtime=Runtime(), voice=False,
                                  background_updates=False, background_work=False, preload_providers=False, server_config=config)
    listener = client = None
    try:
        assert isinstance(app["browser_entry"], entry.GrantStore)
        listener = TestServer(app, port=unused_tcp_port)
        await listener.start_server(ssl=tls.ssl_context(home, config))
        _, pin = entry._owner_probe_targets(home, config)
        client = TestClient(listener, connector=aiohttp.TCPConnector(ssl=pin))
        await client.start_server()
        url = await entry.owner_browser_url(home, config)
        assert app["control_token"] not in url
        response = await client.get("/login")
        csrf = response.cookies[auth.CSRF_COOKIE].value
        ticket = url.split("#ticket=")[1]
        headers = {"Origin": f"https://127.0.0.1:{unused_tcp_port}", "Cookie": f"{auth.CSRF_COOKIE}={csrf}"}
        response = await client.post(entry.REDEEM_PATH, data={"csrf": csrf, "ticket": ticket}, headers=headers, allow_redirects=False)
        assert response.status == 303 and response.cookies[auth.SESSION_COOKIE]["secure"]
        assert response.cookies[auth.SESSION_COOKIE]["httponly"]
        cookie = response.cookies[auth.SESSION_COOKIE].value
        response = await client.get("/", headers={"Cookie": f"{auth.SESSION_COOKIE}={cookie}"}, allow_redirects=False)
        assert response.status == 200
        assert (await client.post(entry.REDEEM_PATH, data={"csrf": csrf, "ticket": ticket}, headers=headers, allow_redirects=False)).status == 403
    finally:
        primary = sys.exception()
        cleanup_errors = []
        for resource in (client, listener, app["service"]):
            if resource is not None:
                try:
                    await resource.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if cleanup_errors:
            raise BaseExceptionGroup("Public owner TLS fixture cleanup failed",
                                     ([primary] if primary is not None else []) + cleanup_errors)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows TLS listener-confusion controls")
@pytest.mark.parametrize("mode", ["wrong-leaf", "redirect"])
async def test_native_unrelated_listener_and_proxy_never_receive_bearer(
        native_root, monkeypatch, aiohttp_client, aiohttp_server, unused_tcp_port, mode):
    from aiohttp.test_utils import TestClient, TestServer
    from amplifier_web import tls, update_readiness, windows_files
    home, other = native_root / "owner", native_root / "other"
    for path in (home, other):
        with windows_files.directory(path, owned_root=native_root, create=True):
            pass
    config = load_server_config(home, overrides={"port": unused_tcp_port, "bind": ["127.0.0.1"]})
    config = tls.setup_local_ca(home, config)
    auth.session_secret(home)
    token = auth.control_token(home)
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": __version__, "revision": REVISION})
    received, trusted_received = [], []
    async def catcher(req):
        received.append(req.headers.get("Authorization"))
        return web.json_response({"ok": True, "app": "unrelated"})
    sink = web.Application()
    sink.router.add_route("*", "/{path:.*}", catcher)
    # Keep the fixture parameters (and maintained pyloop node IDs), but own
    # transport lifetime here. Legacy aiohttp fixtures finalize on a different
    # loop after pytest-asyncio has closed this coroutine's loop.
    target = TestServer(sink)
    listener = client = None
    async def redirect(req):
        trusted_received.append(req.headers.get("Authorization"))
        raise web.HTTPFound(str(target.make_url("/api/health")))
    try:
        await target.start_server()
        listener_app = web.Application()
        listener_app.router.add_get("/api/health", catcher if mode == "wrong-leaf" else redirect)
        if mode == "wrong-leaf":
            other_config = tls.setup_local_ca(other, load_server_config(other))
            context = tls.ssl_context(other, other_config)
        else:
            context = tls.ssl_context(home, config)
        listener = TestServer(listener_app, port=unused_tcp_port)
        await listener.start_server(ssl=context)
        # Retain before awaiting startup so partial client initialization also
        # gets cleanup. Never send an unpinned request through this client.
        client = TestClient(listener)
        await client.start_server()
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(name, str(target.make_url("/")))
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        with pytest.raises(entry.BrowserEntryUnavailable, match="identity"):
            await entry.owner_browser_url(home, config)
        assert received == []
        assert trusted_received == (["Bearer " + token] if mode == "redirect" else [])
    finally:
        primary = sys.exception()
        cleanup_errors = []
        for resource in (client, listener, target):
            if resource is not None:
                try:
                    await resource.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if cleanup_errors:
            # Attempt every owned close. Retain the body failure as the first
            # child rather than replacing it with the last cleanup exception.
            raise BaseExceptionGroup("TLS negative fixture cleanup failed",
                                     ([primary] if primary is not None else []) + cleanup_errors)


@pytest.mark.parametrize("scenario", [
    "legacy", "fixed", "partial-sink", "partial-listener", "partial-client",
    "cleanup-only", "body-and-cleanup",
])
def test_tls_negative_lifetime_on_actual_pytest_fixtures_without_listeners(scenario):
    """Execute the maintained body on fake transports, not a duplicate cleanup."""
    script = r'''
import ast, asyncio, contextlib, os, pathlib, sys, types
# Fail closed even when a dependency catches an unexpected effect.
denied = []
def audit(event, args):
    if event == "open":
        mode, flags = args[1:3]
        if ((isinstance(mode, str) and any(c in mode for c in "wax+")) or
            (isinstance(flags, int) and flags &
             (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))):
            denied.append(event)
            raise PermissionError("No model file writes")
    if event in {"socket.bind", "socket.connect", "socket.getaddrinfo", "socket.sendto",
                 "os.mkdir", "os.remove", "os.rename", "subprocess.Popen"}:
        denied.append(event)
        raise PermissionError("No model network or resource effects")
sys.addaudithook(audit)
import pytest
from _pytest.config import _prepareconfig
from _pytest.main import Session
from _pytest.python import Module
from aiohttp import web
import aiohttp.pytest_plugin as plugin
import aiohttp.test_utils as utilities
# No real self-pipe socket on Windows either; only same-thread coroutine work.
class InertLoop(asyncio.SelectorEventLoop):
    def _make_self_pipe(self): pass
    def _close_self_pipe(self): pass
    def _write_to_self(self): pass
class Policy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self): return InertLoop()
asyncio.set_event_loop_policy(Policy())
source, scenario = pathlib.Path(sys.argv[1]), sys.argv[2]
events, reports, failures, servers = [], [], [], []
class FakeServer(utilities.TestServer):
    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self.kind = "listener" if any(r.resource.canonical == "/api/health"
                                    for r in app.router.routes()) else "sink"
        servers.append(self)
    async def start_server(self, *args, **kwargs):
        if self.runner is not None: return
        self.owner = asyncio.get_running_loop()
        self.runner = object()
        if scenario == "partial-" + self.kind:
            raise ValueError("partial-" + self.kind)
    async def close(self):
        if self.closed: return
        events.append((self.kind, self.owner.is_closed(),
                       asyncio.get_running_loop() is self.owner))
        self.owner.call_soon(lambda: None)
        self._closed = True
        if scenario in {"cleanup-only", "body-and-cleanup"}: raise OSError("close-" + self.kind)
    def make_url(self, path): return "http://127.0.0.1:25000" + path
class FakeClient:
    @classmethod
    def __class_getitem__(cls, item): return cls
    def __init__(self, server, **kwargs):
        self.server, self.closed = server, False
    async def start_server(self):
        await self.server.start_server()
        if scenario == "partial-client": raise ValueError("partial-client")
    async def close(self):
        if self.closed: return
        events.append(("client", self.server.owner.is_closed(),
                       asyncio.get_running_loop() is self.server.owner))
        self.closed = True
        if scenario in {"cleanup-only", "body-and-cleanup"}: raise OSError("close-client")
        await self.server.close()
utilities.TestServer = plugin.TestServer = FakeServer
utilities.TestClient = plugin.TestClient = FakeClient
class Unavailable(RuntimeError): pass
async def owner_url(home, config):
    if scenario == "body-and-cleanup": raise AssertionError("primary-body")
    listener = servers[-1]
    handler = next(iter(listener.app.router.routes())).handler
    if handler.__name__ == "redirect":
        try:
            await handler(types.SimpleNamespace(headers={"Authorization": "Bearer synthetic"}))
        except web.HTTPFound:
            pass
    raise Unavailable("identity mismatch")
@contextlib.contextmanager
def directory(*args, **kwargs): yield
entry = types.SimpleNamespace(BrowserEntryUnavailable=Unavailable, owner_browser_url=owner_url,
    read_owner_control_token=None, _read_private_owner_control_token=None)
package = types.ModuleType("amplifier_web")
package.tls = types.SimpleNamespace(setup_local_ca=lambda home, config: config,
                                   ssl_context=lambda *args: object())
package.update_readiness = types.SimpleNamespace(running_identity=None)
package.windows_files = types.SimpleNamespace(directory=directory)
sys.modules["amplifier_web"] = package
tree = ast.parse(source.read_bytes())
name = "test_native_unrelated_listener_and_proxy_never_receive_bearer"
function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
function.decorator_list = []  # portable fake branch; keep both exact mode values
assert {n.name for n in function.body if isinstance(n, ast.AsyncFunctionDef)} == {"catcher", "redirect"}
if scenario == "legacy":
    # Reproduce only the prior fixture lifetime in memory; never edit source.
    body = []
    for statement in function.body:
        if isinstance(statement, ast.Try) and statement.finalbody:
            body.extend(statement.body)
        elif (isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name)
              and statement.targets[0].id == "target"):
            body.extend(ast.parse("target = await aiohttp_server(sink)").body)
        else:
            body.append(statement)
    function.body = body
    class LegacyClient(ast.NodeTransformer):
        def visit_Assign(self, node):
            if isinstance(node.targets[0], ast.Name) and node.targets[0].id == "client":
                return ast.copy_location(ast.parse("client = await aiohttp_client(listener)").body[0], node)
            return node
    function = LegacyClient().visit(function)
module = types.ModuleType("tls_lifetime_model")
module.__file__ = str(source)
module.__dict__.update(web=web, sys=sys, pytest=pytest, entry=entry, __version__="0.0.0",
    REVISION="a"*40, auth=types.SimpleNamespace(session_secret=lambda *a: None,
    control_token=lambda *a: "synthetic"), load_server_config=lambda *a, **kw: {})
exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
             str(source), "exec"), module.__dict__)
module.__dict__[name] = pytest.mark.parametrize("mode", ["wrong-leaf", "redirect"])(module.__dict__[name])
module.native_root = pytest.fixture(lambda: source.parent)
module.unused_tcp_port = pytest.fixture(lambda: 25000)
sys.modules[module.__name__] = module
class MemoryModule(Module):
    def _getobj(self): return module
def leaves(error):
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in leaves(child)]
    return [(type(error).__name__, str(error))]
class Results:
    def pytest_runtest_logreport(self, report):
        reports.append((report.when, report.outcome))
    def pytest_runtest_makereport(self, item, call):
        if call.excinfo: failures.append((call.when, leaves(call.excinfo.value)))
config = _prepareconfig(["-q", "-s", "--noconftest", "-p", "no:cacheprovider",
    "-p", "no:faulthandler", "-p", "no:logging", "-o", "asyncio_mode=auto"],
    plugins=["pytest_asyncio.plugin", "aiohttp.pytest_plugin", Results()])
config._do_configure()
session = Session.from_config(config)
config.hook.pytest_sessionstart(session=session)
try:
    items = list(MemoryModule.from_parent(session, path=source).collect())
    assert [item.name for item in items] == [
        name + "[pyloop-wrong-leaf]", name + "[pyloop-redirect]"]
    for index, item in enumerate(items):
        item.ihook.pytest_runtest_protocol(item=item, nextitem=items[index+1] if index+1 < len(items) else None)
    if scenario == "legacy":
        assert reports == [("setup", "passed"), ("call", "passed"), ("teardown", "failed")]*2
        assert all(phase == "teardown" and children == [
            ("RuntimeError", "Event loop is closed")]*2 for phase, children in failures)
        assert len([e for e in events if e[0] != "client"]) == 4
        assert all(closed and not same for _, closed, same in events)
    else:
        assert all(not closed and same for _, closed, same in events)
        assert all(server.closed for server in servers)
        assert all(outcome == "passed" for phase, outcome in reports if phase != "call")
        if scenario == "fixed":
            assert reports == [("setup", "passed"), ("call", "passed"), ("teardown", "passed")]*2
            assert [kind for kind, _, _ in events] == ["client", "listener", "sink"]*2
        elif scenario in {"cleanup-only", "body-and-cleanup"}:
            primary = [("AssertionError", "primary-body")] if scenario == "body-and-cleanup" else []
            assert all(phase == "call" and children == primary + [("OSError", "close-client"),
                ("OSError", "close-listener"), ("OSError", "close-sink")]
                for phase, children in failures)
            assert [kind for kind, _, _ in events] == ["client", "listener", "sink"]*2
        else:
            assert all(phase == "call" and children == [("ValueError", scenario)]
                       for phase, children in failures)
        assert len(failures) == (0 if scenario == "fixed" else 2)
    assert not denied, denied
finally:
    config._ensure_unconfigure()
print("LIFETIME_MODEL_PASS", scenario)
'''
    child = subprocess.run([sys.executable, "-I", "-B", "-c", script,
                            str(Path(__file__).resolve()), scenario],
                           env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                           capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    assert "LIFETIME_MODEL_PASS " + scenario in child.stdout


# Share only the native fixture definition, not any test's production overrides.
from test_windows_auth import native_root  # noqa: E402,F401
