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


def certificate_checks(data_dir: Path, config: dict) -> list[dict]:
    """Offline configured-certificate diagnostics, not listener/client trust proof.

    No DNS, remote certificate downloads, credential reads or trust-store writes.
    ssl_context remains the owner of certificate/private-key matching.
    """
    if config['tls']['method'] == 'none':
        return []
    if _WINDOWS:
        return [{'ok': False, 'message': 'Certificate SAN/chain diagnostics on native Windows remain unqualified.'}]
    from cryptography import x509
    from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

    chain = x509.load_pem_x509_certificates(resolve_path(data_dir, config['tls']['cert']).read_bytes())
    leaf = chain[0]
    now = datetime.now(UTC)
    app_owned = (config['tls']['cert'] == 'config/tls/leaf.crt'
                 and config['tls']['key'] == 'config/tls/leaf.key')
    repair = ('Run amplifier-unified setup-tls force to renew the leaf while preserving the CA, then restart the service.'
              if app_owned else 'Replace the supplied certificate/chain with your certificate issuer, then restart the service.')
    checks = []

    def validity(certificate, label, remediation):
        valid = certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc
        expiring = valid and certificate.not_valid_after_utc - now <= timedelta(days=30)
        checks.append({'ok': valid and not expiring, 'message': (
            f'{label} validity: {certificate.not_valid_before_utc.isoformat()} to {certificate.not_valid_after_utc.isoformat()}.'
            + (' Certificate expires within 30 days. ' + remediation if expiring else
               '' if valid else ' Certificate is expired or not yet valid. ' + remediation))})
        return valid and not expiring

    ca = None
    if app_owned:
        from cryptography.hazmat.primitives import serialization
        ca_repair = 'Recover the existing CA from backup or plan an explicit CA rotation and redistribute client trust; do not run setup-tls force with an incomplete CA pair.'
        try:
            ca = x509.load_pem_x509_certificate(exported_ca_bytes(data_dir))
            ca_key = serialization.load_pem_private_key((tls_dir(data_dir) / 'ca.key').read_bytes(), None)
            if ca_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != ca.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
                raise ValueError('Local CA certificate/key do not match.')
        except (OSError, ValueError, TypeError) as error:
            checks.append({'ok': False, 'message': 'Local CA pair is missing or invalid. ' + ca_repair, 'detail': str(error)})
            repair = ca_repair
            ca = None
        else:
            ca_repair = 'Plan an explicit CA rotation and redistribute trust to clients; setup-tls force preserves this CA and cannot renew it.'
            if not validity(ca, 'Local CA certificate', ca_repair):
                repair = ca_repair

    validity(leaf, 'TLS leaf certificate', repair)
    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns = san.get_values_for_type(x509.DNSName)
        ips = san.get_values_for_type(x509.IPAddress)
    except x509.ExtensionNotFound:
        dns, ips = [], []

    hosts = list(config['bind']) + [urlsplit(origin).hostname for origin in config['public_origins']]
    for host in dict.fromkeys(hosts):
        if host in {'0.0.0.0', '::'}:
            continue  # A wildcard bind is not a client identity.
        host = canonical_host(host)
        try:
            # Zones select a local interface for transport, not a certificate
            # identity: X.509 IP SANs encode only the address bytes.
            address = ipaddress.ip_address(host)
            covered = type(address)(int(address)) in ips
        except ValueError:
            # Only a complete leftmost label may be a wildcard, never a suffix
            # match, partial label, IP literal, or multi-level substitution.
            covered = any(host == name.lower().rstrip('.') or (
                name.startswith('*.') and host.count('.') == name.count('.')
                and host.split('.', 1)[1] == name[2:].lower().rstrip('.'))
                for name in dns)
        checks.append({'ok': covered, 'message': (
            f'Certificate SAN covers {host}.' if covered
            else f'Certificate SAN is missing {host}. ' + repair)})

    # Validate against the app CA for its fixed leaf layout. Supplied chains
    # use this machine's system roots, not an unrelated app CA lying on disk.
    if app_owned:
        if ca is None:
            return checks
        roots = [ca]
    else:
        roots = [x509.load_der_x509_certificate(value)
                 for value in ssl.create_default_context().get_ca_certs(binary_form=True)]
    try:
        # A SAN identity is required by the server verifier. Coverage of ALL
        # configured identities is reported independently above.
        name = dns[0].replace('*.', 'diagnostic.', 1) if dns else 'localhost'
        identity = x509.IPAddress(ips[0]) if ips else x509.DNSName(name)
        PolicyBuilder().store(Store(roots)).time(now).build_server_verifier(identity).verify(leaf, chain[1:])
        checks.append({'ok': True, 'message': 'Certificate chain verifies against the local CA or this computer’s system roots; remote client trust is not checked.'})
    except (VerificationError, ValueError) as error:
        checks.append({'ok': False, 'message': 'Certificate chain could not be verified locally. ' + repair,
                       'detail': str(error)})
    checks.append({'ok': True, 'message': 'Certificate checks inspect configured files only; DNS, live TLS and trust on other devices are not verified.'})
    return checks
