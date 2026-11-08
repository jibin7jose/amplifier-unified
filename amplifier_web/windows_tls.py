"""Windows-only app-owned TLS adapter; no trust-store or browser activation.

The fixed layout is ca.crt/ca.key/leaf.crt/leaf.key. Publication is atomic per
file, NOT per pair: failed/crashed setup can leave partial material, which later
operations reject rather than regenerate a CA. Cooperative operations serialize
on a stable private lock. Same-TokenUser processes remain trusted.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import base64
import binascii
import ctypes as C
from datetime import UTC, datetime, timedelta
import ipaddress
import re
import ssl
import time

from . import windows_files as files

_LIMIT = 65536
_LOCK = ".tls.lock"
_LAYOUT = {"cert": "config/tls/leaf.crt", "key": "config/tls/leaf.key"}


class _OVERLAPPED(C.Structure):
    _fields_ = [("Internal", C.c_size_t), ("InternalHigh", C.c_size_t),
                ("Offset", files.DWORD), ("OffsetHigh", files.DWORD), ("hEvent", files.HANDLE)]


@contextmanager
def _hold(pinned, names):
    """TLS-only pathname consumers: reuse the reviewed handle/ACL validators.

    The public private-file API intentionally closes read handles and permits
    atomic replacement. OpenSSL needs names held without DELETE/WRITE sharing.
    Keep this fixed TLS adapter internal, not a second filesystem backend.
    """
    api = pinned._api
    with ExitStack() as stack:
        handles = {}
        for name in names:
            if name not in {"ca.crt", "ca.key", "leaf.crt", "leaf.key", _LOCK}:
                raise ValueError("unsupported TLS file")
            pinned._check()
            path = pinned.path / name
            handle = api.CreateFileW(
                str(path), 0x80020080, 1, None, 3, 0x2200000, None)
            if handle == files._INVALID:
                raise C.WinError(C.get_last_error())
            stack.callback(api.close, handle)
            info = api.info(handle, path, directory=False)
            api.validate_acl(handle, private=True)
            if info.volume != pinned._pins[-1][2][0]:
                raise PermissionError("TLS file crosses volumes")
            handles[name] = handle
        yield handles


@contextmanager
def _lock_handle(pinned, deadline):
    # First publication briefly retains DELETE access after rename. Keep the
    # strict non-delete-sharing lock pin, but include that acquisition window
    # in the same bounded wait as LockFileEx. Retry ONLY sharing violation.
    with ExitStack() as stack:
        while True:
            try:
                handles = stack.enter_context(_hold(pinned, [_LOCK]))
                break
            except OSError as exc:
                if getattr(exc, "winerror", None) != 32:
                    raise
                _wait(deadline)
        yield handles[_LOCK]


def _wait(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("Windows TLS storage is busy; no TLS material was changed")
    time.sleep(0.02)


@contextmanager
def _storage(data_dir, *, create=False):
    """Pin/validate storage and take a bounded exclusive stable-file lock."""
    root = files.address(data_dir)
    with files.directory(root / "config" / "tls", owned_root=root, create=create) as pinned:
        deadline = time.monotonic() + 5
        if create:
            try:
                pinned.publish_private(_LOCK, b"Unified TLS lock\n")
            except FileExistsError:
                pass  # only a clean collision; failed temp cleanup propagates
        with _lock_handle(pinned, deadline) as handle:
            kernel = C.WinDLL("kernel32", use_last_error=True)
            lock, unlock = kernel.LockFileEx, kernel.UnlockFileEx
            lock.argtypes = [files.HANDLE, files.DWORD, files.DWORD, files.DWORD,
                             files.DWORD, C.POINTER(_OVERLAPPED)]
            unlock.argtypes = [files.HANDLE, files.DWORD, files.DWORD, files.DWORD,
                               C.POINTER(_OVERLAPPED)]
            lock.restype = unlock.restype = files.BOOL
            overlapped = _OVERLAPPED()
            while not lock(handle, 3, 0, 1, 0, C.byref(overlapped)):
                error = C.get_last_error()
                if error != 33:  # ERROR_LOCK_VIOLATION only
                    raise C.WinError(error)
                _wait(deadline)
            try:
                yield pinned
            finally:
                pinned._api.ok(unlock(handle, 0, 1, 0, C.byref(overlapped)))


@contextmanager
def _material(pinned, names):
    with _hold(pinned, names) as handles:
        yield {name: pinned._api.read(handle, limit=_LIMIT) for name, handle in handles.items()}


def _configured_paths(config):
    tls = config["tls"]
    if tls["cert"] != _LAYOUT["cert"] or tls["key"] != _LAYOUT["key"]:
        raise NotImplementedError(
            "Windows TLS supports only the app-owned config/tls/leaf.crt and leaf.key layout; "
            "external or custom certificate paths are not qualified")


def _public_key(value):
    from cryptography.hazmat.primitives import serialization
    return value.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _single_pem(content, labels):
    # OpenSSL's X509_AUX reader also accepts TRUSTED CERTIFICATE, while
    # cryptography can skip such a prefix and select a later CERTIFICATE.
    # Admit exactly one supported envelope, no ignored objects/text.
    for label in labels:
        marker = re.escape(label.encode("ascii"))
        match = re.fullmatch(
            rb"[ \t\r\n]*-----BEGIN " + marker + rb"-----\r?\n"
            rb"([A-Za-z0-9+/=\r\n]+)-----END " + marker + rb"-----[ \t\r\n]*", content)
        if match:
            try:
                base64.b64decode(match[1].replace(b"\r", b"").replace(b"\n", b""), validate=True)
            except binascii.Error as exc:
                raise ValueError("invalid single-object PEM encoding") from exc
            return
    raise ValueError("expected exactly one supported PEM object without prefix/suffix material")


def _ca(certificate_bytes, key_bytes=None):
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    try:
        _single_pem(certificate_bytes, ["CERTIFICATE"])
        certificate = x509.load_pem_x509_certificate(certificate_bytes)
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        if (not constraints.critical or not constraints.value.ca or constraints.value.path_length != 0
                or not usage.key_cert_sign or not usage.crl_sign
                or not isinstance(certificate.public_key(), rsa.RSAPublicKey)
                or certificate.public_key().key_size < 2048
                or not certificate.not_valid_before_utc <= datetime.now(UTC) < certificate.not_valid_after_utc):
            raise ValueError("invalid CA constraints, key, or validity")
        certificate.verify_directly_issued_by(certificate)
        key = None
        if key_bytes is not None:
            _single_pem(key_bytes, ["RSA PRIVATE KEY", "PRIVATE KEY"])
            key = serialization.load_pem_private_key(key_bytes, password=None)
            if not isinstance(key, rsa.RSAPrivateKey) or _public_key(key) != _public_key(certificate):
                raise ValueError("CA certificate and key do not match")
        return certificate, key
    except (ValueError, TypeError, x509.ExtensionNotFound, InvalidSignature, UnsupportedAlgorithm) as exc:
        raise ValueError("Invalid Windows TLS CA; existing material was not regenerated") from exc


def _leaf(ca, cert_bytes, key_bytes, config, *, rotating=False):
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID
    from .tls import _names
    try:
        _single_pem(cert_bytes, ["CERTIFICATE"])
        _single_pem(key_bytes, ["RSA PRIVATE KEY", "PRIVATE KEY"])
        cert = x509.load_pem_x509_certificate(cert_bytes)
        key = serialization.load_pem_private_key(key_bytes, password=None)
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if (constraints.ca or usage.key_cert_sign or not usage.digital_signature
                or ExtendedKeyUsageOID.SERVER_AUTH not in eku
                or not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048
                or _public_key(key) != _public_key(cert)):
            raise ValueError("invalid leaf constraints or mismatched key")
        cert.verify_directly_issued_by(ca)
        if not rotating:
            hosts, ips = _names(config)
            if (not cert.not_valid_before_utc <= datetime.now(UTC) < cert.not_valid_after_utc
                    or not set(hosts).issubset(san.get_values_for_type(x509.DNSName))
                    or not {ipaddress.ip_address(ip) for ip in ips}.issubset(san.get_values_for_type(x509.IPAddress))):
                raise ValueError("leaf expired or SANs do not cover configured hosts; rotate the leaf")
        return cert
    except (ValueError, TypeError, x509.ExtensionNotFound, InvalidSignature, UnsupportedAlgorithm) as exc:
        raise ValueError("Invalid Windows TLS leaf certificate/key/CA chain") from exc


def _new_ca():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    now = datetime.now(UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Amplifier Unified Local CA")])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), critical=True).sign(key, hashes.SHA256()))
    return certificate, key


def _new_leaf(ca, ca_key, config):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from .tls import _names
    now = datetime.now(UTC)
    hosts, ips = _names(config)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = [x509.DNSName(host) for host in hosts] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    leaf = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0][:64])]))
        .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(min(now + timedelta(days=397), ca.not_valid_after_utc))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256()))
    return leaf, key


def _encoded_pair(cert, key):
    from cryptography.hazmat.primitives import serialization
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()))


def _publish_pair(pinned, prefix, cert, key, *, replace=False):
    try:
        pinned.publish_private(prefix + ".key", key, replace=replace)
        pinned.publish_private(prefix + ".crt", cert, replace=replace)
    except Exception as exc:
        raise OSError(
            "Windows TLS publication failed; material may be partial. "
            "No CA regeneration, destination deletion or rollback was attempted") from exc


def setup(data_dir, config, *, force=False):
    from .deployment import save_server_config
    with _storage(data_dir, create=True) as pinned:
        contents = {}
        for name in ("ca.crt", "ca.key", "leaf.crt", "leaf.key"):
            try:
                contents[name] = pinned.read_private(name, limit=_LIMIT)
            except FileNotFoundError:
                contents[name] = None
        for prefix in ("ca", "leaf"):
            if (contents[prefix + ".crt"] is None) != (contents[prefix + ".key"] is None):
                raise ValueError(f"Partial Windows TLS {prefix} pair; refusing regeneration")
        if contents["ca.crt"] is None:
            if contents["leaf.crt"] is not None:
                raise ValueError("Orphan Windows TLS leaf; refusing CA regeneration")
            ca, ca_key = _new_ca()
            encoded = _encoded_pair(ca, ca_key)
            _publish_pair(pinned, "ca", *encoded)
        else:
            ca, ca_key = _ca(contents["ca.crt"], contents["ca.key"])
        if contents["leaf.crt"] is not None:
            _leaf(ca, contents["leaf.crt"], contents["leaf.key"], config, rotating=force)
        if contents["leaf.crt"] is None or force:
            leaf, key = _new_leaf(ca, ca_key, config)
            encoded = _encoded_pair(leaf, key)
            _leaf(ca, *encoded, config)
            _publish_pair(pinned, "leaf", *encoded, replace=contents["leaf.crt"] is not None)
        # Hold the exact named objects through OpenSSL's reads and verify the
        # just-published/retained chain before publishing server configuration.
        _context(pinned, config)
        configured = {**config, "tls": {"method": "ca", **_LAYOUT}}
        save_server_config(data_dir, configured)
        return configured


def _context(pinned, config):
    with _material(pinned, ["ca.crt", "leaf.crt", "leaf.key"]) as content:
        ca, _ = _ca(content["ca.crt"])
        _leaf(ca, content["leaf.crt"], content["leaf.key"], config)
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(pinned.path / "leaf.crt", pinned.path / "leaf.key")
        return context


def context(data_dir, config):
    _configured_paths(config)  # refuse unsupported paths BEFORE any file IO
    with _storage(data_dir) as pinned:
        return _context(pinned, config)


def exported_ca(data_dir):
    # Public export reads no key, creates no lock/directory, and follows no path.
    root = files.address(data_dir)
    with files.directory(root / "config" / "tls", owned_root=root) as pinned:
        content = pinned.read_private("ca.crt", limit=_LIMIT)
    _ca(content)
    return content