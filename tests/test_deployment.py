import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from amplifier_web import deployment
from amplifier_web.deployment import config_path, load_server_config, save_server_config, validate_origin, validate_server


def assert_private_config(path, root):
    if os.name == "nt":
        from amplifier_web.windows_files import directory
        with directory(path.parent, owned_root=root) as pinned:
            assert pinned.read_private(path.name, limit=1024 * 1024)
    else:
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.fixture
def tmp_path(tmp_path_factory):
    # pytest's runner temp can have a broad inherited ACL. Do not silently
    # repair it or skip all config tests. Allocate a private profile child for
    # tests that actually consume tmp_path on native Windows.
    if os.name != "nt":
        yield tmp_path_factory.mktemp("config")
        return
    import shutil
    import uuid
    from amplifier_web.windows_files import address, directory
    root = address(Path.home() / (".unified-config-test-" + uuid.uuid4().hex))
    with directory(root, owned_root=root, create=True):
        pass
    try:
        yield root
    finally:
        shutil.rmtree(root)


def test_server_config_is_private_atomic_and_defaults_to_loopback(tmp_path):
    config = load_server_config(tmp_path)
    assert config["bind"] == ["127.0.0.1"]
    assert_private_config(config_path(tmp_path), tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows FileRenameInfoEx and local NTFS")
def test_native_config_save_with_open_private_reader_retains_old_bytes(tmp_path):
    wf = deployment.windows_files
    original = load_server_config(tmp_path)
    path = config_path(tmp_path)
    before = path.read_bytes()
    api = wf._API()
    reader = api.open(path)  # READ | DELETE sharing, identical to private readers
    try:
        save_server_config(tmp_path, {**original, "port": 9234, "extension": "complete-new"})
        assert api.read(reader, limit=1024 * 1024) == before
        new = load_server_config(tmp_path)
        assert new["port"] == 9234
        assert new["extension"] == "complete-new"
        assert [entry.name for entry in path.parent.iterdir()] == ["server.yaml"]
    finally:
        api.close(reader)


def test_public_origin_is_exact_and_remote_bind_requires_https_tls(tmp_path):
    assert validate_origin("https://host.example:8941") == "https://host.example:8941"
    assert validate_origin("https://BÜCHER.example:443") == "https://xn--bcher-kva.example"
    assert validate_origin("http://HOST.example:80") == "http://host.example"
    assert validate_origin("https://[2001:0DB8:0:0::1]:443") == "https://[2001:db8::1]"
    with pytest.raises(ValueError):
        validate_origin("https://host.example/path")
    with pytest.raises(ValueError, match="invalid port"):
        validate_origin("https://host.example:0")
    with pytest.raises(ValueError, match="IDNA"):
        validate_origin("https://bad_host.example")
    with pytest.raises(ValueError, match="Non-loopback"):
        save_server_config(tmp_path, {"schema_version": 1, "bind": ["192.0.2.1"], "port": 8941,
                                     "public_origins": ["https://host.example"], "session_ttl_seconds": 60,
                                     "tls": {"method": "none", "cert": "", "key": ""}})


@pytest.mark.parametrize(("key", "value"), [("port", True), ("session_ttl_seconds", False)])
def test_server_config_rejects_boolean_numbers(tmp_path, key, value):
    config = load_server_config(tmp_path)
    with pytest.raises(ValueError, match=key):
        save_server_config(tmp_path, {**config, key: value})


def test_server_config_preserves_extension_fields_without_rewriting_on_load(tmp_path):
    path = config_path(tmp_path)
    original = "# Private extension configuration\nport: 8941\ndebug:\n  profiling: true\n  labels: [cpu]\ncustom_extension: disabled\n"
    if os.name == "nt":
        # Ordinary mkdir/write_text can default to Administrators ownership on
        # elevated runners. Seed valid private state, not a load-time ACL repair.
        with deployment.windows_files.directory(path.parent, owned_root=tmp_path, create=True) as pinned:
            pinned.publish_private(path.name, original.encode("utf-8"))
        assert_private_config(path, tmp_path)
    else:
        path.parent.mkdir(parents=True)
        path.write_text(original)

    config = load_server_config(tmp_path, overrides={"port": 9321})
    assert config["debug"] == {"profiling": True, "labels": ["cpu"]}
    assert config["custom_extension"] == "disabled"
    assert config["port"] == 9321
    assert path.read_text() == original

    save_server_config(tmp_path, {**config, "session_ttl_seconds": 120})
    saved = load_server_config(tmp_path)
    assert saved["debug"] == config["debug"]
    assert saved["custom_extension"] == config["custom_extension"]
    assert saved["session_ttl_seconds"] == 120
    assert_private_config(path, tmp_path)


def test_server_config_validation_does_not_alias_extension_values():
    source = {"debug": {"profiling": {"enabled": True}}}
    config = validate_server(source)
    config["debug"]["profiling"]["enabled"] = False
    assert source["debug"]["profiling"]["enabled"] is True


@pytest.mark.parametrize(("key", "value", "message"), [
    ("schema_version", 2, "schema"),
    ("bind", [], "bind"),
    ("bind", ["192.0.2.5"], "Non-loopback"),
    ("port", True, "port"),
    ("session_ttl_seconds", 0, "session_ttl_seconds"),
    ("public_origins", ["https://host.example/path"], "Public origins"),
    ("tls", {"method": "invalid"}, "TLS"),
    ("runtime", {"prewarm_on_select": "true"}, "prewarm_on_select"),
])
def test_server_extension_fields_do_not_bypass_known_setting_validation(key, value, message):
    with pytest.raises(ValueError, match=message):
        validate_server({"debug": {"profiling": True}, key: value})


@pytest.mark.skipif(os.name == "nt", reason="TLS private writers are a separate unqualified Windows slice")
def test_tls_sans_use_canonical_public_origin_hostname(tmp_path):
    from cryptography import x509
    from amplifier_web.tls import ca_bytes, setup_local_ca

    config = load_server_config(tmp_path)
    setup_local_ca(tmp_path, {**config, "public_origins": ["https://BÜCHER.example:443"]})
    leaf = x509.load_pem_x509_certificate((tmp_path / "config/tls/leaf.crt").read_bytes())
    names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "xn--bcher-kva.example" in names.get_values_for_type(x509.DNSName)
    assert ca_bytes(tmp_path)


@pytest.fixture
def windows_config_io(monkeypatch, tmp_path):
    """Synthetic backend: proves integration/validation, not NTFS privacy."""
    monkeypatch.setattr(deployment, "_WINDOWS", True)
    state = {"body": None, "publications": [], "reads": [], "directories": []}

    class Pinned:
        def read_private(self, name, *, limit):
            state["reads"].append((name, limit))
            if state.get("read_error"):
                raise state["read_error"]
            if state["body"] is None:
                raise FileNotFoundError(name)
            return state["body"]

        def publish_private(self, name, content, *, replace=False):
            state["publications"].append((name, content, replace))
            if state.get("race"):
                state["body"] = state.pop("race")
                raise FileExistsError(name)
            state["body"] = content

    @contextmanager
    def directory(path, *, owned_root, create=False):
        state["directories"].append((path, owned_root, create))
        yield Pinned()

    monkeypatch.setattr(deployment.windows_files, "directory", directory)
    return state


def test_windows_config_reads_without_path_probe_or_resolve(tmp_path, windows_config_io, monkeypatch):
    state = windows_config_io
    state["body"] = b"# unchanged\nport: 9321\ncustom_extension: keep\n"
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "resolve", lambda *args, **kwargs: pytest.fail("unsafe resolve"))
        scoped.setattr(Path, "exists", lambda *args, **kwargs: pytest.fail("unsafe exists probe"))
        config = load_server_config(tmp_path)
    assert config["port"] == 9321
    assert config["custom_extension"] == "keep"
    assert not state["publications"]
    assert state["directories"] == [(tmp_path / "config", tmp_path, False)]


def test_windows_config_create_and_save_use_explicit_root(tmp_path, windows_config_io):
    state = windows_config_io
    config = load_server_config(tmp_path)
    assert state["publications"][0][2] is False
    assert state["directories"][-1] == (tmp_path / "config", tmp_path, True)
    save_server_config(tmp_path, {**config, "port": 9234})
    assert state["publications"][-1][2] is True
    assert load_server_config(tmp_path)["port"] == 9234


def test_windows_config_default_creation_race_retains_winner(tmp_path, windows_config_io):
    state = windows_config_io
    state["race"] = b"port: 9234\ncustom_extension: winner\n"
    config = load_server_config(tmp_path, overrides={"session_ttl_seconds": 120})
    assert config["port"] == 9234
    assert config["custom_extension"] == "winner"
    assert config["session_ttl_seconds"] == 120
    assert state["body"] == b"port: 9234\ncustom_extension: winner\n"
    assert len(state["publications"]) == 1


@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["clean-race", "failed-cleanup"])
def test_windows_config_real_publication_collision_cleanup_outcome(tmp_path, monkeypatch, cleanup_fails):
    """Use the real directory/publication and deployment code; inject kernel IO."""
    wf = deployment.windows_files
    monkeypatch.setattr(deployment, "_WINDOWS", True)
    state = {"winner": None, "temporary": None, "reads": 0, "closed": []}
    collision = FileExistsError("another config creator won")
    cleanup = PermissionError("exact temporary cleanup failed")
    winner = b"port: 9234\nextension: winner\n"
    parent = tmp_path / "config"

    class API:
        def GetDriveTypeW(self, path):
            return 3

        def GetVolumeInformationByHandleW(self, handle, a, b, c, d, e, filesystem, size):
            filesystem.value = "NTFS"
            return True

        ok = staticmethod(wf._API.ok)

        def open(self, path, *, directory=False, create=False, cleanup=False):
            if directory:
                return ("directory", str(path))
            if create:
                assert state["temporary"] is None
                state["temporary"] = path
                return ("temporary",)
            if cleanup:
                assert state["temporary"] == path
                return ("temporary",)
            assert path == parent / "server.yaml"
            if state["winner"] is None:
                raise FileNotFoundError(path)
            return ("winner",)

        def info(self, handle, path, *, directory):
            return wf.FILE_INFO(volume=1, links=1, index_low=10 if directory else 20)

        def validate_acl(self, handle, *, private):
            pass

        def read(self, handle, *, limit):
            assert handle == ("winner",)
            state["reads"] += 1
            return state["winner"]

        def write(self, handle, content):
            assert handle == ("temporary",)

        def flush(self, handle):
            assert handle == ("temporary",)

        def move(self, handle, destination, *, replace):
            assert not replace and handle == ("temporary",)
            assert destination == parent / "server.yaml"
            state["winner"] = winner
            raise collision

        def discard(self, handle, path, identity):
            assert handle == ("temporary",)
            assert path == state["temporary"] and identity == (1, 0, 20)
            if cleanup_fails:
                raise cleanup
            state["temporary"] = None

        def close(self, handle):
            state["closed"].append(handle)

    monkeypatch.setattr(wf, "_API", API)
    if cleanup_fails:
        with pytest.raises(OSError) as caught:
            load_server_config(tmp_path)
        error = caught.value
        assert not isinstance(error, FileExistsError)
        assert "temporary may remain" in str(error)
        assert isinstance(error.__cause__, ExceptionGroup)
        assert error.__cause__.exceptions == (collision, cleanup)
        assert state["temporary"].name.startswith(".private-")
        assert state["reads"] == 0  # not silently converted into winner success
    else:
        config = load_server_config(tmp_path)
        assert config["port"] == 9234 and config["extension"] == "winner"
        assert state["temporary"] is None
        assert state["reads"] == 1
    assert state["winner"] == winner
    assert state["closed"].count(("temporary",)) == 2


@pytest.mark.parametrize("error", [PermissionError("unsafe ACL"), OSError("sharing conflict"), ValueError("too large")])
def test_windows_config_read_failure_is_not_empty_defaults(tmp_path, windows_config_io, error):
    windows_config_io["read_error"] = error
    with pytest.raises(type(error)):
        load_server_config(tmp_path)
    assert not windows_config_io["publications"]


@pytest.mark.parametrize("body", [b"[a, b]", b"port: [invalid]", b"\xff", b"bad: ["])
def test_windows_config_rejects_bad_bytes_without_rewrite(tmp_path, windows_config_io, body):
    windows_config_io["body"] = body
    with pytest.raises((ValueError, UnicodeError, deployment.yaml.YAMLError)):
        load_server_config(tmp_path)
    assert not windows_config_io["publications"]


def test_windows_unscoped_private_writer_refuses_before_io(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment, "_WINDOWS", True)
    with pytest.raises(NotImplementedError, match="owned_root"):
        deployment.write_private(tmp_path / "secret", "not-a-real-secret")
    with pytest.raises(NotImplementedError, match="owned_root"):
        deployment.write_file(tmp_path / "secret", b"not-a-real-secret")
    assert list(tmp_path.iterdir()) == []


def test_windows_app_home_passes_original_components_to_address(monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.host import config as host_config
    calls = []
    unchecked = r"C:\untrusted\..\app"
    monkeypatch.setattr(host_config, "os", SimpleNamespace(
        name="nt", environ={"AMPLIFIER_WEB_HOME": unchecked}))

    def address(value):
        calls.append(value)
        return Path("anchored-but-not-resolved")

    monkeypatch.setattr(deployment.windows_files, "address", address)
    assert host_config.app_home() == Path("anchored-but-not-resolved")
    assert calls == [unchecked]
