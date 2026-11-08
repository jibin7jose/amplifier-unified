"""Private credential controls. Portable API models are not native ACL proof."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from amplifier_web import auth, browser_entry as entry, cli, server, windows_files

SID = "S-1-5-21-100-200-300-1001"


class PrivateFiles:
    def __init__(self):
        self.content, self.events = {}, []
        self.active = False
        self.collision = False
        self.read_error = self.publish_error = None

    @contextmanager
    def directory(self, path, *, owned_root, create=False):
        self.events.append(("directory", path, owned_root, create))
        assert path.is_relative_to(owned_root) and not self.active
        self.active = True
        fixture = self

        class Pinned:
            def read_private(self, name, *, limit):
                assert fixture.active
                fixture.events.append(("read", name, limit))
                if fixture.read_error:
                    raise fixture.read_error
                if path / name not in fixture.content:
                    raise FileNotFoundError()
                value = fixture.content[path / name]
                if len(value) > limit:
                    raise ValueError("bounded read refused")
                return value

            def publish_private(self, name, content, *, replace=False):
                assert fixture.active and replace is False
                fixture.events.append(("publish", name, replace))
                if fixture.publish_error:
                    raise fixture.publish_error
                if fixture.collision:
                    fixture.content[path / name] = b"w" * 64 + b"\n"
                    raise FileExistsError()
                assert path / name not in fixture.content
                fixture.content[path / name] = content
        try:
            yield Pinned()
        finally:
            self.active = False
            self.events.append(("close",))


@pytest.fixture
def private_files(monkeypatch):
    fixture = PrivateFiles()
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(windows_files, "directory", fixture.directory)
    monkeypatch.setattr(entry, "current_owner_sid", lambda: SID)
    return fixture


def test_bounded_private_no_replace_creation_and_repeat_reads(tmp_path, private_files, monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda *a, **kw: pytest.fail("Windows must not resolve links"))
    monkeypatch.setattr(auth.os, "chmod", lambda *a: pytest.fail("chmod is not Windows privacy"))
    monkeypatch.setattr(auth, "FileLock", lambda *a: pytest.fail("Unprotected Windows lock sidecar"))
    token, secret = auth.control_token(tmp_path), auth.session_secret(tmp_path)
    assert len(token) == len(secret) == 64 and token != secret
    assert auth.control_token(tmp_path) == token
    assert auth.session_secret(tmp_path) == secret
    assert all(event[2] == 65 for event in private_files.events if event[0] == "read")
    assert [e[1] for e in private_files.events if e[0] == "publish"] == ["control-token", "session-secret"]
    assert list(tmp_path.iterdir()) == []


def test_collision_reads_complete_winner_without_retry(tmp_path, private_files):
    private_files.collision = True
    assert auth.control_token(tmp_path) == "w" * 64
    assert len([e for e in private_files.events if e[0] == "publish"]) == 1
    assert private_files.events[-1] == ("close",)


@pytest.mark.parametrize("contents", [b"", b"\n", b"x" * 63, b"x" * 66, b" " + b"x" * 64,
                                    b"x" * 64 + b"\r\n", b"\xff" * 64])
def test_invalid_existing_never_regenerates(tmp_path, private_files, contents):
    path = tmp_path / "config/auth/control-token"
    private_files.content[path] = contents
    with pytest.raises(ValueError):
        auth.control_token(tmp_path)
    assert private_files.content[path] == contents
    assert not any(e[0] == "publish" for e in private_files.events)


@pytest.mark.parametrize("error", [PermissionError("unsafe ACL"), OSError("sharing failure")])
def test_unsafe_existing_never_creates_or_repairs(tmp_path, private_files, error):
    private_files.read_error = error
    with pytest.raises(type(error)):
        auth.session_secret(tmp_path)
    assert not any(e[0] == "publish" for e in private_files.events)


def test_uncertain_publication_is_not_replayed(tmp_path, private_files):
    private_files.publish_error = OSError("published; verification failed")
    with pytest.raises(OSError):
        auth.session_secret(tmp_path)
    assert [e[0] for e in private_files.events] == ["directory", "read", "publish", "close"]


def test_owner_reader_existing_only_both_credentials_one_pin(tmp_path, private_files):
    private_files.content[tmp_path / "config/auth/session-secret"] = b"s" * 64 + b"\n"
    private_files.content[tmp_path / "config/auth/control-token"] = b"c" * 64 + b"\n"
    assert entry.read_owner_control_token(tmp_path, SID) == "c" * 64
    assert private_files.events == [
        ("directory", tmp_path / "config/auth", tmp_path, False),
        ("read", "session-secret", 65), ("read", "control-token", 65), ("close",)]
    private_files.events.clear()
    with pytest.raises(entry.BrowserEntryUnavailable):
        entry.read_owner_control_token(tmp_path, "S-1-5-21-999")
    assert private_files.events == []


@pytest.mark.parametrize("which", ["session-secret", "control-token"])
def test_owner_reader_refuses_missing_or_invalid_either(tmp_path, private_files, which):
    private_files.content[tmp_path / "config/auth/session-secret"] = b"s" * 64
    private_files.content[tmp_path / "config/auth/control-token"] = b"c" * 64
    private_files.content[tmp_path / "config/auth" / which] = b""
    with pytest.raises(entry.BrowserEntryUnavailable):
        entry.read_owner_control_token(tmp_path, SID)
    del private_files.content[tmp_path / "config/auth" / which]
    with pytest.raises(entry.BrowserEntryUnavailable):
        entry.read_owner_control_token(tmp_path, SID)
    assert not any(e[0] == "publish" for e in private_files.events)


def test_public_owner_read_initializes_only_matching_existing_credentials(tmp_path, private_files, monkeypatch):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    private_files.content[tmp_path / "config/auth/session-secret"] = b"s" * 64
    private_files.content[tmp_path / "config/auth/control-token"] = b"c" * 64
    assert entry.read_owner_control_token(tmp_path, SID) == "c" * 64
    app = {"service": type("Service", (), {"data_dir": tmp_path})(), "control_token": "c" * 64}
    entry.initialize(app)
    assert isinstance(app["browser_entry"], entry.GrantStore)
    app["control_token"] = "different-control"
    entry.initialize(app)
    assert app["browser_entry"] is None
    assert not any(e[0] == "publish" for e in private_files.events)


@pytest.mark.parametrize("failure", [PermissionError("unsafe ACL"), OSError("sharing failure")])
def test_public_owner_read_never_repairs_or_falls_back(tmp_path, private_files, monkeypatch, failure):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setenv("AMPLIFIER_UNIFIED_TOKEN", "c" * 64)
    monkeypatch.setattr(auth.os, "chmod", lambda *a: pytest.fail("No ACL repair"))
    private_files.read_error = failure
    with pytest.raises(entry.BrowserEntryUnavailable, match="private owner credentials"):
        entry.read_owner_control_token(tmp_path, SID)
    app = {"service": type("Service", (), {"data_dir": tmp_path})(), "control_token": "c" * 64}
    entry.initialize(app)
    assert app["browser_entry"] is None
    assert not any(e[0] == "publish" for e in private_files.events)


def test_original_cli_and_identity_address_preserved(tmp_path, private_files, monkeypatch):
    original = str(tmp_path / "not-resolved")
    calls = []
    def address(value):
        calls.append(value)
        return Path(value)
    monkeypatch.setattr(windows_files, "address", address)
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr(Path, "resolve", lambda *a, **kw: pytest.fail("Windows resolve"))
    assert cli._data_dir(original) == Path(original)
    assert auth.data_identity(original) == hashlib.sha256(original.encode()).hexdigest()
    assert calls == [original, original]
    assert private_files.events == [("directory", Path(original), Path(original), False), ("close",)]


def test_windows_partition_uses_token_sid(tmp_path, private_files, monkeypatch):
    monkeypatch.setattr(auth.getpass, "getuser", lambda: pytest.fail("username is not SID"))
    first = auth.history_scope("synthetic-secret", "synthetic-session", tmp_path)
    monkeypatch.setattr(entry, "current_owner_sid", lambda: "S-1-5-21-999")
    assert auth.history_scope("synthetic-secret", "synthetic-session", tmp_path) != first


async def test_server_validates_root_before_config_or_service(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_WINDOWS", True)
    def refuse(path, *, owned_root, create):
        assert path == tmp_path and owned_root == tmp_path and create
        raise PermissionError("synthetic unsafe root")
    monkeypatch.setattr(windows_files, "directory", refuse)
    monkeypatch.setattr(server, "load_server_config", lambda *a: pytest.fail("unvalidated config read"))
    monkeypatch.setattr(server, "AppService", lambda *a, **kw: pytest.fail("unvalidated service"))
    for config in (None, {}):
        with pytest.raises(PermissionError):
            await server.create_app(tmp_path, server_config=config)


@pytest.mark.parametrize("sid", [None, "", "username", "S-1-5-18", "S-1-5-19", "S-1-5-20"])
def test_owner_sid_public_seam_and_policy(monkeypatch, sid):
    monkeypatch.setattr(entry, "is_windows", lambda: True)
    monkeypatch.setattr(windows_files, "current_process_sid", lambda: sid)
    with pytest.raises(entry.BrowserEntryUnavailable):
        entry.current_owner_sid()
    monkeypatch.setattr(windows_files, "current_process_sid", lambda: SID)
    assert entry.current_owner_sid() == SID


@pytest.fixture
def native_root():
    if os.name != "nt":
        pytest.skip("Requires Windows TokenUser/NTFS/private DACL")
    # Canonicalize the existing profile only, never an unvalidated app address.
    root = Path.home().resolve() / (".unified-auth-check-" + uuid.uuid4().hex)
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    try:
        yield root
    finally:
        shutil.rmtree(root)


@pytest.fixture
def tmp_path(tmp_path, request):
    # The native boundary must not trust the runner's ordinary temp DACL or an
    # 8.3 TEMP spelling. Do not repair it; select a new private profile child.
    return request.getfixturevalue("native_root") if os.name == "nt" else tmp_path


def test_native_credentials_converge_and_existing_read(native_root):
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(auth.control_token, [native_root] * 8))
    assert len(set(tokens)) == 1
    auth.session_secret(native_root)
    assert entry.current_owner_sid() == windows_files.current_process_sid()
    assert entry.read_owner_control_token(native_root, entry.current_owner_sid()) == tokens[0]
    assert {p.name for p in (native_root / "config/auth").iterdir()} == {"session-secret", "control-token"}


def test_native_empty_existing_refused_without_replacement(native_root):
    with windows_files.directory(native_root / "config/auth", owned_root=native_root, create=True) as pinned:
        pinned.publish_private("control-token", b"")
    with pytest.raises(ValueError):
        auth.control_token(native_root)
    with windows_files.directory(native_root / "config/auth", owned_root=native_root) as pinned:
        assert pinned.read_private("control-token", limit=65) == b""


def test_native_foreign_read_acl_never_repaired(native_root):
    token = auth.control_token(native_root)
    path = native_root / "config/auth/control-token"
    result = subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:(R)"], capture_output=True, timeout=10)
    assert result.returncode == 0
    before = subprocess.run(["icacls", str(path)], capture_output=True, check=True, timeout=10).stdout
    with pytest.raises(PermissionError):
        auth.control_token(native_root)
    assert path.read_text().strip() == token
    assert subprocess.run(["icacls", str(path)], capture_output=True, check=True, timeout=10).stdout == before


def test_native_case_alias_refused(native_root):
    alias = native_root.with_name(native_root.name.upper())
    with pytest.raises(PermissionError):
        auth.data_identity(alias)


def test_native_junction_not_resolved_before_refusal(native_root):
    outside, app = native_root / "outside", native_root / "app"
    for path in (outside, app):
        with windows_files.directory(path, owned_root=native_root, create=True):
            pass
    link = app / "config"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, timeout=10)
    assert result.returncode == 0
    try:
        with pytest.raises(PermissionError):
            auth.control_token(app)
        assert not (outside / "auth").exists()
    finally:
        link.rmdir()
