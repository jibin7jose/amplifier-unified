"""Windows TLS policy/real OpenSSL tests and native-only NTFS qualification.

Portable injected storage is NOT proof of Windows ACLs, sharing or locking.
No trust stores, sockets/listeners or live credentials are used.
"""
from contextlib import contextmanager
import ctypes as C
import os
from pathlib import Path
import shutil
import ssl
from types import SimpleNamespace
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from amplifier_web import deployment, tls, windows_files as wf, windows_tls as wt


def handshake(server_context, ca):
    """Real OpenSSL verification/handshake with MemoryBIO, no network listener."""
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.load_verify_locations(cadata=ca.decode("ascii"))
    ci, co, si, so = [ssl.MemoryBIO() for _ in range(4)]
    client = client_context.wrap_bio(ci, co, server_side=False, server_hostname="localhost")
    server = server_context.wrap_bio(si, so, server_side=True)
    completed = set()
    for _ in range(20):
        for label, peer in (("client", client), ("server", server)):
            try:
                peer.do_handshake()
                completed.add(label)
            except ssl.SSLWantReadError:
                pass
        if co.pending:
            si.write(co.read())
        if so.pending:
            ci.write(so.read())
        if len(completed) == 2:
            return client.getpeercert(binary_form=True)
    pytest.fail("bounded in-memory TLS handshake did not complete")


@pytest.fixture(scope="module")
def sample():
    config = deployment.validate_server({})
    ca, key = wt._new_ca()
    leaf, leaf_key = wt._new_leaf(ca, key, config)
    a, b = wt._encoded_pair(ca, key)
    c, d = wt._encoded_pair(leaf, leaf_key)
    return config, {"ca.crt": a, "ca.key": b, "leaf.crt": c, "leaf.key": d}


@pytest.fixture
def simulated(tmp_path, monkeypatch, sample):
    """Use real TLS logic/OpenSSL with explicit POSIX test storage."""
    state = {"publications": [], "saves": [], "held": False, "locked": False}
    path = tmp_path / "config/tls"
    path.mkdir(parents=True)

    class Pinned:
        def __init__(self):
            self.path = path

        def read_private(self, name, *, limit):
            content = (path / name).read_bytes()
            if len(content) > limit:
                raise ValueError("bounded read")
            return content

        def publish_private(self, name, content, *, replace=False):
            assert state["locked"]
            assert not state["held"], "write while OpenSSL holds files"
            state["publications"].append((name, replace))
            if state.get("fail") == name:
                raise OSError("injected publication failure")
            with (path / name).open("wb" if replace else "xb") as stream:
                stream.write(content)

    pinned = Pinned()

    @contextmanager
    def storage(root, *, create=False):
        assert not state["locked"]
        state["locked"] = True
        try:
            yield pinned
        finally:
            state["locked"] = False

    @contextmanager
    def material(actual, names):
        assert actual is pinned and state["locked"]
        state["held"] = True
        try:
            yield {name: pinned.read_private(name, limit=wt._LIMIT) for name in names}
        finally:
            state["held"] = False

    monkeypatch.setattr(tls, "_WINDOWS", True)
    monkeypatch.setattr(wt, "_storage", storage)
    monkeypatch.setattr(wt, "_material", material)
    monkeypatch.setattr(deployment, "save_server_config",
                        lambda root, config: state["saves"].append(config))
    return tmp_path, pinned, state


def seed(simulated, sample):
    root, pinned, state = simulated
    for name, content in sample[1].items():
        (pinned.path / name).write_bytes(content)
    return root, pinned, state


def test_windows_tls_paths_are_lexical(monkeypatch):
    monkeypatch.setattr(tls, "_WINDOWS", True)
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("resolved before private boundary"))
    assert tls.tls_dir(Path("relative")) == wf.address("relative") / "config/tls"


def test_create_then_read_then_force_keeps_ca_and_real_handshake(simulated, sample):
    root, pinned, state = simulated
    configured = tls.setup_local_ca(root, sample[0])
    assert configured["tls"] == {"method": "ca", **wt._LAYOUT}
    assert state["publications"] == [
        ("ca.key", False), ("ca.crt", False), ("leaf.key", False), ("leaf.crt", False)]
    before = {p.name: p.read_bytes() for p in pinned.path.iterdir()}
    context = tls.ssl_context(root, configured)
    peer = handshake(context, before["ca.crt"])
    assert peer == x509.load_pem_x509_certificate(before["leaf.crt"]).public_bytes(serialization.Encoding.DER)
    tls.setup_local_ca(root, configured)
    assert {p.name: p.read_bytes() for p in pinned.path.iterdir()} == before
    assert len(state["publications"]) == 4
    tls.setup_local_ca(root, configured, force=True)
    after = {p.name: p.read_bytes() for p in pinned.path.iterdir()}
    assert after["ca.crt"] == before["ca.crt"] and after["ca.key"] == before["ca.key"]
    assert after["leaf.crt"] != before["leaf.crt"] and after["leaf.key"] != before["leaf.key"]
    assert state["publications"][-2:] == [("leaf.key", True), ("leaf.crt", True)]
    handshake(tls.ssl_context(root, configured), after["ca.crt"])


@pytest.mark.parametrize("missing", ["ca.crt", "ca.key", "leaf.crt", "leaf.key"])
@pytest.mark.parametrize("force", [False, True])
def test_partial_existing_pair_never_regenerates_or_overwrites(simulated, sample, missing, force):
    root, pinned, state = seed(simulated, sample)
    (pinned.path / missing).unlink()
    before = {p.name: p.read_bytes() for p in pinned.path.iterdir()}
    with pytest.raises(ValueError, match="Partial"):
        tls.setup_local_ca(root, sample[0], force=force)
    assert not state["publications"] and not state["saves"]
    assert {p.name: p.read_bytes() for p in pinned.path.iterdir()} == before


def test_orphan_leaf_does_not_authorize_a_new_ca(simulated, sample):
    root, pinned, state = seed(simulated, sample)
    (pinned.path / "ca.crt").unlink()
    (pinned.path / "ca.key").unlink()
    with pytest.raises(ValueError, match="Orphan"):
        tls.setup_local_ca(root, sample[0], force=True)
    assert not state["publications"] and not state["saves"]


@pytest.mark.parametrize("bad_name", ["ca.crt", "ca.key", "leaf.crt", "leaf.key"])
def test_corrupt_existing_material_fails_unchanged(simulated, sample, bad_name):
    root, pinned, state = seed(simulated, sample)
    (pinned.path / bad_name).write_bytes(b"invalid PEM")
    before = {p.name: p.read_bytes() for p in pinned.path.iterdir()}
    with pytest.raises(ValueError):
        tls.setup_local_ca(root, sample[0])
    assert not state["publications"] and not state["saves"]
    assert {p.name: p.read_bytes() for p in pinned.path.iterdir()} == before


def test_mismatched_key_and_foreign_ca_chain_fail_before_openssl(simulated, sample, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    root, pinned, state = seed(simulated, sample)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key = wt._encoded_pair(x509.load_pem_x509_certificate(sample[1]["leaf.crt"]), key)[1]
    (pinned.path / "leaf.key").write_bytes(other_key)
    config = {**sample[0], "tls": {"method": "ca", **wt._LAYOUT}}
    monkeypatch.setattr(wt.ssl, "create_default_context", lambda *a: pytest.fail("invalid chain reached OpenSSL"))
    with pytest.raises(ValueError, match="leaf"):
        tls.ssl_context(root, config)
    (pinned.path / "leaf.key").write_bytes(sample[1]["leaf.key"])
    other_ca, other_ca_key = wt._new_ca()
    (pinned.path / "ca.crt").write_bytes(wt._encoded_pair(other_ca, other_ca_key)[0])
    with pytest.raises(ValueError, match="leaf"):
        tls.ssl_context(root, config)


def test_trusted_certificate_prefix_cannot_change_which_leaf_openssl_loads(simulated, sample, monkeypatch):
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID
    from datetime import UTC, datetime, timedelta
    root, pinned, state = seed(simulated, sample)
    ca, ca_key = wt._new_ca()
    actual = x509.load_pem_x509_certificate(sample[1]["leaf.crt"])
    # Same public key, wrong CA. OpenSSL's X509_AUX loader accepts TRUSTED
    # CERTIFICATE; cryptography's certificate loader can skip that prefix.
    foreign = (x509.CertificateBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "different.example")]))
        .issuer_name(ca.subject).public_key(actual.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1)).sign(ca_key, hashes.SHA256()))
    prefix = foreign.public_bytes(serialization.Encoding.PEM).replace(
        b"CERTIFICATE", b"TRUSTED CERTIFICATE")
    body = prefix + sample[1]["leaf.crt"]
    (pinned.path / "leaf.crt").write_bytes(body)
    config = {**sample[0], "tls": {"method": "ca", **wt._LAYOUT}}
    monkeypatch.setattr(wt.ssl, "create_default_context", lambda *a: pytest.fail("ambiguous PEM reached OpenSSL"))
    with pytest.raises(ValueError, match="leaf"):
        tls.ssl_context(root, config)
    with pytest.raises(ValueError, match="leaf"):
        tls.setup_local_ca(root, config)
    assert not state["saves"] and not state["publications"]


@pytest.mark.parametrize("name,transform", [
    ("ca.crt", lambda b: b + b),
    ("leaf.crt", lambda b: b + b),
    ("leaf.crt", lambda b: b"ignored prefix\n" + b),
    ("leaf.crt", lambda b: b + b"ignored suffix\n"),
    ("ca.key", lambda b: b + b),
    ("leaf.key", lambda b: b"ignored prefix\n" + b),
    ("leaf.key", lambda b: b + b),
])
def test_multiple_or_ignored_pem_objects_refused_before_save(simulated, sample, name, transform):
    root, pinned, state = seed(simulated, sample)
    (pinned.path / name).write_bytes(transform(sample[1][name]))
    with pytest.raises(ValueError):
        tls.setup_local_ca(root, sample[0])
    assert not state["saves"] and not state["publications"]

def test_missing_sans_fail_unless_explicit_leaf_rotation(simulated, sample):
    root, pinned, state = seed(simulated, sample)
    changed = {**sample[0], "public_origins": ["https://192.0.2.45:8941"]}
    with pytest.raises(ValueError, match="leaf"):
        tls.setup_local_ca(root, changed)
    assert not state["publications"]
    configured = tls.setup_local_ca(root, changed, force=True)
    handshake(tls.ssl_context(root, configured), sample[1]["ca.crt"])


@pytest.mark.parametrize("name", ["ca.crt", "leaf.crt"])
def test_failed_multifile_publication_is_explicit_and_not_success(simulated, sample, name):
    root, pinned, state = simulated
    state["fail"] = name
    with pytest.raises(OSError, match="may be partial"):
        tls.setup_local_ca(root, sample[0])
    assert not state["saves"]
    retained = {p.name: p.read_bytes() for p in pinned.path.iterdir()}
    state.pop("fail")
    with pytest.raises(ValueError, match="Partial"):
        tls.setup_local_ca(root, sample[0], force=True)
    assert {p.name: p.read_bytes() for p in pinned.path.iterdir()} == retained


def test_rotation_failure_retains_ca_and_exposes_mismatch_not_fake_success(simulated, sample):
    root, pinned, state = seed(simulated, sample)
    state["fail"] = "leaf.crt"
    with pytest.raises(OSError, match="may be partial"):
        tls.setup_local_ca(root, sample[0], force=True)
    assert (pinned.path / "ca.crt").read_bytes() == sample[1]["ca.crt"]
    assert (pinned.path / "ca.key").read_bytes() == sample[1]["ca.key"]
    assert not state["saves"]
    with pytest.raises(ValueError, match="leaf"):
        tls.ssl_context(root, {**sample[0], "tls": {"method": "ca", **wt._LAYOUT}})


def test_external_windows_paths_refuse_before_storage(monkeypatch, sample):
    monkeypatch.setattr(tls, "_WINDOWS", True)
    monkeypatch.setattr(wt, "_storage", lambda *a, **k: pytest.fail("external path caused IO"))
    config = {**sample[0], "tls": {"method": "ca", "cert": "elsewhere.crt", "key": "elsewhere.key"}}
    with pytest.raises(NotImplementedError, match="app-owned"):
        tls.ssl_context(Path("unchecked"), config)
    assert not tls.tls_ready(Path("unchecked"), config)


def test_context_keeps_material_held_during_openssl_path_reads(simulated, sample, monkeypatch):
    root, pinned, state = seed(simulated, sample)
    real = ssl.create_default_context
    events = []

    def factory(*args):
        actual = real(*args)

        def load(cert, key):
            assert state["locked"] and state["held"]
            assert cert == pinned.path / "leaf.crt" and key == pinned.path / "leaf.key"
            events.append("openssl")
            actual.load_cert_chain(cert, key)

        return SimpleNamespace(load_cert_chain=load)

    monkeypatch.setattr(wt.ssl, "create_default_context", factory)
    tls.ssl_context(root, {**sample[0], "tls": {"method": "ca", **wt._LAYOUT}})
    assert events == ["openssl"] and not state["held"] and not state["locked"]
    assert not state["publications"] and not state["saves"]


def test_hold_denies_write_delete_and_uses_reviewed_validators(monkeypatch):
    events = []
    api = SimpleNamespace()

    def opened(path, access, share, attributes, disposition, flags, template):
        assert access == 0x80020080 and share == 1 and disposition == 3
        assert attributes is None and flags == 0x2200000
        events.append(("open", path))
        return len(events)

    api.CreateFileW = opened
    api.close = lambda handle: events.append(("close", handle))
    api.info = lambda handle, path, **kw: events.append(("info", handle)) or wf.FILE_INFO(volume=7)
    api.validate_acl = lambda handle, **kw: events.append(("acl", handle, kw))
    pinned = SimpleNamespace(
        path=Path("private"), _api=api, _pins=[(None, None, (7, 0, 1), True)],
        _check=lambda: events.append(("pins",)))
    with wt._hold(pinned, ["leaf.crt", "leaf.key"]) as handles:
        assert len(handles) == 2
        assert not any(e[0] == "close" for e in events)
    assert [e[0] for e in events] == [
        "pins", "open", "info", "acl", "pins", "open", "info", "acl", "close", "close"]


def test_hold_refuses_wrong_volume_and_closes_owned_handle():
    closed = []
    api = SimpleNamespace(
        CreateFileW=lambda *a: 42, close=closed.append,
        info=lambda *a, **k: wf.FILE_INFO(volume=8), validate_acl=lambda *a, **k: None)
    pinned = SimpleNamespace(path=Path("private"), _api=api,
                             _pins=[(None, None, (7, 0, 1), True)], _check=lambda: None)
    with pytest.raises(PermissionError, match="volume"):
        with wt._hold(pinned, ["leaf.crt"]):
            pytest.fail("unsafe handle escaped")
    assert closed == [42]


@pytest.mark.parametrize("mode", ["success", "busy", "denied", "cleanup-failed", "initial-sharing", "sharing-timeout"])
def test_storage_lock_order_timeout_and_error_propagation(tmp_path, monkeypatch, mode):
    events = []
    error = [0]

    def winerror(code):
        exc = OSError(f"WinError {code}")
        exc.winerror = code
        return exc

    class Function:
        def __init__(self, function):
            self.function = function

        def __call__(self, *args):
            return self.function(*args)

    def lock(handle, flags, reserved, low, high, pointer):
        assert handle == 42 and flags == 3 and reserved == high == 0 and low == 1
        assert C.cast(pointer, C.POINTER(wt._OVERLAPPED)).contents.Offset == 0
        events.append("lock")
        error[0] = 33 if mode == "busy" else 5 if mode == "denied" else 0
        return not error[0]

    def unlock(handle, reserved, low, high, pointer):
        assert handle == 42 and reserved == high == 0 and low == 1
        events.append("unlock")
        return True

    class Pinned:
        _api = SimpleNamespace(ok=lambda result: None if result else pytest.fail("unlock failed"))

        def publish_private(self, name, content, *, replace=False):
            assert name == wt._LOCK and not replace
            events.append("private-no-replace-lock")
            if mode == "cleanup-failed":
                raise OSError("lock collision plus failed exact cleanup")
            raise FileExistsError("stable lock already exists")

    @contextmanager
    def directory(path, *, owned_root, create=False):
        assert path == tmp_path / "config/tls" and owned_root == tmp_path and create
        events.append("pins")
        try:
            yield Pinned()
        finally:
            events.append("unpin")

    @contextmanager
    def hold(pinned, names):
        assert names == [wt._LOCK]
        events.append("hold")
        if mode in {"initial-sharing", "sharing-timeout"} and events.count("hold") == 1:
            raise winerror(32)  # fresh publication still holds DELETE access
        try:
            yield {wt._LOCK: 42}
        finally:
            events.append("close-lock")

    monkeypatch.setattr(wt.files, "directory", directory)
    monkeypatch.setattr(wt, "_hold", hold)
    monkeypatch.setattr(wt.C, "WinDLL", lambda *a, **k: SimpleNamespace(
        LockFileEx=Function(lock), UnlockFileEx=Function(unlock)), raising=False)
    monkeypatch.setattr(wt.C, "get_last_error", lambda: error[0], raising=False)
    monkeypatch.setattr(wt.C, "WinError", winerror, raising=False)
    times = iter([0, 1 if mode == "initial-sharing" else 6])
    monkeypatch.setattr(wt.time, "monotonic", lambda: next(times))
    def sleep(value):
        assert mode == "initial-sharing" and value == 0.02
        events.append("wait-sharing")
    monkeypatch.setattr(wt.time, "sleep", sleep)
    if mode in {"success", "initial-sharing"}:
        with wt._storage(tmp_path, create=True):
            events.append("TLS-operation")
        before_lock = ["hold", "wait-sharing"] if mode == "initial-sharing" else []
        assert events == ["pins", "private-no-replace-lock", *before_lock, "hold", "lock",
                          "TLS-operation", "unlock", "close-lock", "unpin"]
    else:
        with pytest.raises(TimeoutError if mode in {"busy", "sharing-timeout"} else OSError):
            with wt._storage(tmp_path, create=True):
                pytest.fail("operation ran without lock")
        assert "TLS-operation" not in events and "unlock" not in events
        assert events[-1] == "unpin"
        if mode == "cleanup-failed":
            assert "hold" not in events and "lock" not in events


def test_ca_export_uses_bounded_private_read_not_path_read(tmp_path, monkeypatch, sample):
    events = []

    @contextmanager
    def directory(path, *, owned_root):
        assert path == tmp_path / "config/tls" and owned_root == tmp_path

        class Pinned:
            def read_private(self, name, *, limit):
                assert name == "ca.crt" and limit == 65536
                events.append("read-private")
                return sample[1]["ca.crt"]
        yield Pinned()

    monkeypatch.setattr(tls, "_WINDOWS", True)
    monkeypatch.setattr(wt.files, "directory", directory)
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("unchecked Path read"))
    assert tls.exported_ca_bytes(tmp_path) == sample[1]["ca.crt"]
    assert events == ["read-private"]


def test_private_boundary_refusal_precedes_generation_or_publication(tmp_path, monkeypatch, sample):
    monkeypatch.setattr(tls, "_WINDOWS", True)

    @contextmanager
    def denied(*args, **kwargs):
        raise PermissionError("unsafe ancestor")
        yield

    monkeypatch.setattr(wt.files, "directory", denied)
    monkeypatch.setattr(wt, "_new_ca", lambda: pytest.fail("generated before ACL validation"))
    with pytest.raises(PermissionError, match="ancestor"):
        tls.setup_local_ca(tmp_path, sample[0])
    assert list(tmp_path.iterdir()) == []


def test_lock_win64_abi():
    assert C.sizeof(wf.DWORD) == 4
    if C.sizeof(C.c_void_p) == 8:
        assert C.sizeof(wt._OVERLAPPED) == 32
        assert wt._OVERLAPPED.Offset.offset == 16
        assert wt._OVERLAPPED.hEvent.offset == 24


@pytest.fixture
def native_root():
    if os.name != "nt":
        pytest.skip("requires native Windows local NTFS/profile ACLs, file sharing and LockFileEx")
    root = wf.address(Path.home() / (".unified-tls-test-" + uuid.uuid4().hex))
    # No broad CI temp ACL rewrite. An unsafe profile is a real failure.
    with wf.directory(root, owned_root=root, create=True):
        pass
    try:
        yield root
    finally:
        shutil.rmtree(root)


def test_native_creation_privacy_before_bytes_handshake_and_force(native_root, monkeypatch):
    real = wf._API.write
    writes = []

    def checked(api, handle, content):
        api.validate_acl(handle, private=True)
        writes.append(len(content))
        real(api, handle, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(wf._API, "write", checked)
        config = tls.setup_local_ca(native_root, deployment.load_server_config(native_root))
    assert writes
    path = tls.tls_dir(native_root)
    old_ca = tls.exported_ca_bytes(native_root)
    old_key = (path / "ca.key").read_bytes()
    handshake(tls.ssl_context(native_root, config), old_ca)
    tls.setup_local_ca(native_root, config, force=True)
    assert tls.exported_ca_bytes(native_root) == old_ca
    assert (path / "ca.key").read_bytes() == old_key
    handshake(tls.ssl_context(native_root, config), old_ca)


def test_native_openssl_files_cannot_be_replaced_while_loading(native_root, monkeypatch):
    config = tls.setup_local_ca(native_root, deployment.load_server_config(native_root))
    path = tls.tls_dir(native_root)
    original_factory = wt.ssl.create_default_context

    def factory(*args):
        actual = original_factory(*args)

        def load(cert, key):
            with wf.directory(path, owned_root=native_root) as pinned:
                with pytest.raises(OSError):
                    pinned.publish_private("leaf.crt", b"must-not-publish", replace=True)
                with pytest.raises(OSError):
                    pinned.publish_private("leaf.key", b"must-not-publish", replace=True)
            api = wf._API()
            for target in (cert, key):
                write = api.CreateFileW(str(target), 0x40000000, 7, None, 3, 0x200000, None)
                if write != wf._INVALID:
                    api.close(write)
                    pytest.fail("OpenSSL-held material permitted a concurrent writer")
            actual.load_cert_chain(cert, key)
        return SimpleNamespace(load_cert_chain=load)

    monkeypatch.setattr(wt.ssl, "create_default_context", factory)
    tls.ssl_context(native_root, config)


def test_native_tls_junction_rejected_and_target_unchanged(native_root):
    import subprocess
    target = native_root / "unrelated target"
    config = tls.setup_local_ca(target, deployment.load_server_config(target))
    before = {p.name: p.read_bytes() for p in tls.tls_dir(target).iterdir()}
    alias = native_root / "alias with spaces"
    # Do not embed quoted cmd syntax in one list argument: list2cmdline would
    # backslash-escape those quotes rather than quote the path arguments.
    result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    try:
        with pytest.raises(PermissionError, match="reparse point"):
            tls.setup_local_ca(alias, config, force=True)
        with pytest.raises(PermissionError, match="reparse point"):
            tls.ssl_context(alias, config)
        assert {p.name: p.read_bytes() for p in tls.tls_dir(target).iterdir()} == before
    finally:
        alias.rmdir()


def test_native_hardlinked_tls_material_refused_without_change(native_root):
    config = tls.setup_local_ca(native_root, deployment.load_server_config(native_root))
    path = tls.tls_dir(native_root) / "leaf.key"
    before = path.read_bytes()
    os.link(path, native_root / "unrelated-sentinel")
    with pytest.raises(PermissionError):
        tls.ssl_context(native_root, config)
    with pytest.raises(PermissionError):
        tls.setup_local_ca(native_root, config, force=True)
    assert path.read_bytes() == before
    assert (native_root / "unrelated-sentinel").read_bytes() == before


def test_native_foreign_read_acl_is_refused_not_repaired(native_root):
    config = tls.setup_local_ca(native_root, deployment.load_server_config(native_root))
    path = tls.tls_dir(native_root) / "leaf.key"
    before = path.read_bytes()
    api = wf._API()
    descriptor = wf.PVOID()
    api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"D:P(A;;FA;;;{api.sid})(A;;FA;;;SY)(A;;FR;;;WD)", 1, C.byref(descriptor), None))
    apply = C.WinDLL("advapi32", use_last_error=True).SetFileSecurityW
    apply.argtypes, apply.restype = [C.c_wchar_p, wf.DWORD, wf.PVOID], wf.BOOL
    try:
        api.ok(apply(str(path), 0x80000004, descriptor))
        with pytest.raises(PermissionError, match="foreign access"):
            tls.ssl_context(native_root, config)
        with pytest.raises(PermissionError, match="foreign access"):
            tls.setup_local_ca(native_root, config, force=True)
        assert path.read_bytes() == before
        # Successful repair would remove the foreign grant. Check that the
        # reviewed validator continues to reject the same unchanged descriptor.
        handle = api.open(path)
        try:
            with pytest.raises(PermissionError, match="foreign access"):
                api.validate_acl(handle, private=True)
        finally:
            api.close(handle)
    finally:
        api.LocalFree(descriptor)


def test_native_concurrent_initializers_converge_one_ca_and_leaf(native_root):
    import subprocess
    import sys
    # One task venv, two independent processes; no installed/global credentials.
    script = """
import pathlib, sys
from amplifier_web import tls, deployment
root = pathlib.Path(sys.argv[1])
config = tls.setup_local_ca(root, deployment.validate_server({}))
tls.ssl_context(root, config)
print(tls.ca_fingerprint(root))
"""
    processes = [subprocess.Popen([sys.executable, "-B", "-c", script, str(native_root)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        results = [process.communicate(timeout=45) for process in processes]
        for process, (_, error) in zip(processes, results):
            assert process.returncode == 0, error.decode(errors="replace")
        assert results[0][0] == results[1][0] and results[0][0].strip()
        assert sorted(p.name for p in tls.tls_dir(native_root).iterdir()) == [
            ".tls.lock", "ca.crt", "ca.key", "leaf.crt", "leaf.key"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def test_native_lock_initialization_waits_for_retained_publication_handle(native_root):
    """Force the DELETE-source-handle window, rather than hope a race hits it."""
    import subprocess
    import sys
    import time
    publisher = """
import pathlib, sys, time
from amplifier_web import tls, deployment, windows_files as wf
root = pathlib.Path(sys.argv[1])
real = wf._API.move
def move(api, handle, destination, *, replace):
    real(api, handle, destination, replace=replace)
    if destination.name == ".tls.lock":
        (root / "publisher-retains-delete").write_text("ready")
        deadline = time.monotonic() + 20
        while not (root / "release-publisher").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("publication barrier was not released")
            time.sleep(.01)
wf._API.move = move
tls.setup_local_ca(root, deployment.validate_server({}))
print(tls.ca_fingerprint(root))
"""
    contender = """
from contextlib import contextmanager
import pathlib, sys
from amplifier_web import tls, deployment, windows_tls as wt
root = pathlib.Path(sys.argv[1])
real = wt._hold
@contextmanager
def hold(pinned, names):
    try:
        with real(pinned, names) as handles:
            yield handles
    except OSError as exc:
        if getattr(exc, "winerror", None) == 32:
            (root / "contender-observed-sharing").write_text("ready")
        raise
wt._hold = hold
tls.setup_local_ca(root, deployment.validate_server({}))
print(tls.ca_fingerprint(root))
"""
    processes = []

    def launch(script):
        process = subprocess.Popen([sys.executable, "-B", "-c", script, str(native_root)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes.append(process)
        return process

    def wait_for(name):
        deadline = time.monotonic() + 10
        while not (native_root / name).exists():
            assert all(p.poll() is None for p in processes), "child exited before the native barrier"
            assert time.monotonic() < deadline, f"native barrier absent: {name}"
            time.sleep(.01)

    try:
        launch(publisher)
        wait_for("publisher-retains-delete")
        launch(contender)
        wait_for("contender-observed-sharing")
        (native_root / "release-publisher").write_text("release")
        results = [p.communicate(timeout=45) for p in processes]
        for process, (_, error) in zip(processes, results):
            assert process.returncode == 0, error.decode(errors="replace")
        assert results[0][0] == results[1][0] and results[0][0].strip()
    finally:
        (native_root / "release-publisher").write_text("release")
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)