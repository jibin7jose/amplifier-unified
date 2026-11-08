"""Minimal app-owned local CA and HTTPS support."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket
import ssl
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .deployment import canonical_host, resolve_path, save_server_config, write_file

_WINDOWS = os.name == "nt"


def tls_dir(data_dir: Path) -> Path:
    if _WINDOWS:
        from .windows_files import address
        return address(data_dir) / "config" / "tls"
    return Path(data_dir).expanduser().resolve() / "config" / "tls"


def _names(config: dict) -> tuple[list[str], list[str]]:
    hosts = ["localhost"]
    try:
        hosts.append(canonical_host(socket.gethostname()))
    except ValueError:
        pass
    ips = ["127.0.0.1", "::1"]
    for origin in config["public_origins"]:
        host = canonical_host(urlsplit(origin).hostname)
        try:
            ips.append(str(ipaddress.ip_address(host)))
        except ValueError:
            hosts.append(host)
    for bind in config["bind"]:
        try:
            ips.append(str(ipaddress.ip_address(bind)))
        except ValueError:
            if bind != "0.0.0.0" and bind != "::":
                hosts.append(canonical_host(bind))
    return list(dict.fromkeys(hosts)), list(dict.fromkeys(ips))


def setup_local_ca(data_dir: Path, config: dict, *, force: bool = False) -> dict:
    """Create a persistent CA and a renewable leaf certificate for this app."""
    if _WINDOWS:
        from .windows_tls import setup
        return setup(data_dir, config, force=force)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory = tls_dir(data_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    ca_cert, ca_key = directory / "ca.crt", directory / "ca.key"
    leaf_cert, leaf_key = directory / "leaf.crt", directory / "leaf.key"
    if force:
        # Rotating the leaf must not invalidate the CA clients already trust.
        for path in (leaf_cert, leaf_key):
            path.unlink(missing_ok=True)
    now = datetime.now(UTC)
    created_ca = not ca_cert.exists() or not ca_key.exists()
    if created_ca:
        key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Amplifier Unified Local CA")])
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False), critical=True).sign(key, hashes.SHA256()))
        write_file(ca_key, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        write_file(ca_cert, certificate.public_bytes(serialization.Encoding.PEM), mode=0o644)
    if not force and not created_ca and leaf_cert.exists() and leaf_key.exists():
        configured = {**config, "tls": {"method": "ca", "cert": "config/tls/leaf.crt", "key": "config/tls/leaf.key"}}
        save_server_config(data_dir, configured)
        return configured
    ca_certificate = x509.load_pem_x509_certificate(ca_cert.read_bytes())
    ca_private_key = serialization.load_pem_private_key(ca_key.read_bytes(), password=None)
    hosts, ips = _names(config)
    leaf_key_value = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = [x509.DNSName(host) for host in hosts] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0][:64])])
    leaf = (x509.CertificateBuilder().subject_name(leaf_name).issuer_name(ca_certificate.subject).public_key(leaf_key_value.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + timedelta(days=397))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key_value.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_certificate.public_key()), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_private_key, hashes.SHA256()))
    write_file(leaf_key, leaf_key_value.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    write_file(leaf_cert, leaf.public_bytes(serialization.Encoding.PEM), mode=0o644)
    configured = {**config, "tls": {"method": "ca", "cert": "config/tls/leaf.crt", "key": "config/tls/leaf.key"}}
    save_server_config(data_dir, configured)
    return configured


def ssl_context(data_dir: Path, config: dict) -> ssl.SSLContext | None:
    tls = config["tls"]
    if tls["method"] == "none":
        return None
    if not tls["cert"] or not tls["key"]:
        raise ValueError("HTTPS requires both tls.cert and tls.key. Configure both paths before starting the server.")
    if _WINDOWS:
        from .windows_tls import context
        return context(data_dir, config)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(resolve_path(data_dir, tls["cert"]), resolve_path(data_dir, tls["key"]))
    return context


def ca_bytes(data_dir: Path) -> bytes | None:
    """Read only the fixed, app-owned CA path and only when it is actually a CA."""
    try:
        return exported_ca_bytes(data_dir)
    except Exception:
        return None


def exported_ca_bytes(data_dir: Path) -> bytes:
    """Return the existing public CA, rejecting missing, malformed, and leaf PEMs."""
    from cryptography import x509

    if _WINDOWS:
        from .windows_tls import exported_ca
        return exported_ca(data_dir)
    path = tls_dir(data_dir) / "ca.crt"
    try:
        certificate = path.read_bytes()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Local CA certificate is not configured: {path}") from exc
    try:
        parsed = x509.load_pem_x509_certificate(certificate)
    except ValueError as exc:
        raise ValueError(f"Local CA certificate is malformed: {path}") from exc
    try:
        is_ca = parsed.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound as exc:
        raise ValueError(f"Local CA certificate has no CA constraint: {path}") from exc
    if not is_ca:
        raise ValueError(f"Local CA certificate is not a CA: {path}")
    return certificate


def ca_fingerprint(data_dir: Path) -> str | None:
    """Return the standard SHA-256 X.509 fingerprint of the served CA."""
    certificate = ca_bytes(data_dir)
    if certificate is None:
        return None
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    digest = x509.load_pem_x509_certificate(certificate).fingerprint(hashes.SHA256())
    return ":".join(f"{byte:02X}" for byte in digest)


def tls_ready(data_dir: Path, config: dict) -> bool:
    """Return whether the configured certificate/key can start HTTPS.

    A supplied pair need not have an app-owned CA. This is a listener check,
    not a claim that any client trusts the certificate or its hostname.
    """
    if config["tls"]["method"] == "none" or not config['tls'].get('cert') or not config['tls'].get('key'):
        return False
    try:
        ssl_context(data_dir, config)
    except (FileNotFoundError, OSError, ssl.SSLError, ValueError, NotImplementedError):
        return False
    return True
