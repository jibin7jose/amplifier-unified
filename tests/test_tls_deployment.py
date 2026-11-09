import io
import stat
from types import SimpleNamespace

import pytest

from amplifier_web.cli import _doctor_details, _setup_tls, main
from cryptography.hazmat.primitives import hashes


def test_setup_local_ca_is_servable_has_ca_constraint_and_prints_ca_fingerprint(tmp_path, capsys):
    from cryptography import x509
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import ca_bytes, ca_fingerprint, setup_local_ca

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    assert stat.S_IMODE((tmp_path / "config/tls").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "config/tls/ca.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "config/tls/leaf.key").stat().st_mode) == 0o600
    certificate = ca_bytes(tmp_path)
    assert config["tls"]["method"] == "ca"
    assert certificate
    assert x509.load_pem_x509_certificate(certificate).extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    expected_fingerprint = ":".join(
        f"{byte:02X}" for byte in x509.load_pem_x509_certificate(certificate).fingerprint(hashes.SHA256())
    )
    assert ca_fingerprint(tmp_path) == expected_fingerprint
    _doctor_details(tmp_path)
    assert f"CA SHA-256 fingerprint: {expected_fingerprint}" in capsys.readouterr().out
    _setup_tls(tmp_path, "status")
    assert f"CA SHA-256 fingerprint: {expected_fingerprint}" in capsys.readouterr().out


def test_public_tls_status_command_prints_existing_ca_fingerprint_without_mutation(tmp_path, monkeypatch, capsys):
    from cryptography import x509
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca

    setup_local_ca(tmp_path, load_server_config(tmp_path))
    before = {path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
              for path in tmp_path.rglob('*') if path.is_file()}
    certificate = x509.load_pem_x509_certificate((tmp_path / 'config/tls/ca.crt').read_bytes())
    expected = ':'.join(f'{byte:02X}' for byte in certificate.fingerprint(hashes.SHA256()))
    monkeypatch.setattr('sys.argv', ['amplifier-unified', '--data-dir', str(tmp_path), 'setup-tls', 'status'])

    main()

    output = capsys.readouterr().out
    assert f'CA SHA-256 fingerprint: {expected}' in output
    assert 'PRIVATE KEY' not in output
    assert {path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in tmp_path.rglob('*') if path.is_file()} == before


def test_setup_tls_reuses_leaf_until_force_is_requested(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca

    config = load_server_config(tmp_path)
    setup_local_ca(tmp_path, config)
    ca_before = (tmp_path / "config/tls/ca.crt").read_bytes()
    leaf_before = (tmp_path / "config/tls/leaf.crt").read_bytes()
    setup_local_ca(tmp_path, config)
    assert (tmp_path / "config/tls/ca.crt").read_bytes() == ca_before
    assert (tmp_path / "config/tls/leaf.crt").read_bytes() == leaf_before
    setup_local_ca(tmp_path, config, force=True)
    assert (tmp_path / "config/tls/ca.crt").read_bytes() == ca_before
    assert (tmp_path / "config/tls/leaf.crt").read_bytes() != leaf_before


def test_tls_status_rejects_a_missing_leaf(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca, tls_ready

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    assert tls_ready(tmp_path, config)
    (tmp_path / "config/tls/leaf.crt").unlink()
    assert not tls_ready(tmp_path, config)


@pytest.mark.parametrize("existing_directory", [False, True])
def test_tls_status_reads_do_not_create_or_change_tls_state(tmp_path, existing_directory):
    from amplifier_web.tls import ca_bytes, ca_fingerprint, tls_ready

    directory = tmp_path / "config" / "tls"
    if existing_directory:
        directory.mkdir(parents=True)
        directory.chmod(0o750)
    assert ca_bytes(tmp_path) is None
    assert ca_fingerprint(tmp_path) is None
    assert not tls_ready(tmp_path, {"tls": {"method": "ca"}})
    if existing_directory:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o750
        assert not list(directory.iterdir())
    else:
        assert not directory.exists()


def test_tls_setup_restricts_existing_directory_even_when_reusing_leaf(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    directory = tmp_path / "config" / "tls"
    directory.chmod(0o750)
    leaf = (directory / "leaf.crt").read_bytes()
    setup_local_ca(tmp_path, config)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert (directory / "leaf.crt").read_bytes() == leaf


def test_setup_tls_export_emits_only_the_existing_public_ca_without_writing(tmp_path, monkeypatch):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca

    setup_local_ca(tmp_path, load_server_config(tmp_path))
    directory = tmp_path / "config/tls"
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    output = io.BytesIO()
    monkeypatch.setattr("sys.stdout", SimpleNamespace(buffer=output))

    _setup_tls(tmp_path, "export")

    assert output.getvalue() == before["ca.crt"]
    assert b"PRIVATE KEY" not in output.getvalue()
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


@pytest.mark.parametrize(
    "contents,error",
    [
        (None, FileNotFoundError),
        (b"not a certificate", ValueError),
    ],
)
def test_setup_tls_export_rejects_missing_or_malformed_ca_without_creating_tls_state(tmp_path, contents, error):
    directory = tmp_path / "config/tls"
    if contents is not None:
        directory.mkdir(parents=True)
        (directory / "ca.crt").write_bytes(contents)

    with pytest.raises(error, match="Local CA certificate"):
        _setup_tls(tmp_path, "export")

    if contents is None:
        assert not directory.exists()
        assert not (tmp_path / "config").exists()
    else:
        assert list(directory.iterdir()) == [directory / "ca.crt"]
        assert not (tmp_path / "config" / "server.yaml").exists()


def test_setup_tls_export_rejects_a_leaf_certificate(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca

    setup_local_ca(tmp_path, load_server_config(tmp_path))
    directory = tmp_path / "config/tls"
    (directory / "ca.crt").write_bytes((directory / "leaf.crt").read_bytes())

    with pytest.raises(ValueError, match="not a CA"):
        _setup_tls(tmp_path, "export")


def test_doctor_finds_missing_lan_and_tailscale_sans_without_dns_or_mutation(tmp_path, monkeypatch):
    import socket
    from amplifier_web import installation_health as health
    from amplifier_web.deployment import load_server_config, save_server_config
    from amplifier_web.tls import setup_local_ca

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    save_server_config(tmp_path, {**config, 'bind': ['127.0.0.1', '192.168.10.20', '::1'],
        'public_origins': ['https://192.168.10.20:8941', 'https://spark-2.tail.example:8941',
                           'https://100.93.134.115:8941', 'https://[fd7a:115c:a1e0::123]:8941']})
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob('*') if p.is_file()}
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: pytest.fail('doctor must not resolve DNS'))
    result = health.doctor(tmp_path)
    assert not result['ok']
    messages = '\n'.join(row['message'] for row in result['checks'])
    for name in ['192.168.10.20', 'spark-2.tail.example', '100.93.134.115', 'fd7a:115c:a1e0::123']:
        assert 'Certificate SAN is missing ' + name in messages
    assert 'Certificate SAN covers ::1' in messages
    assert 'setup-tls force' in messages and 'preserving the CA' in messages
    assert 'remote client trust is not checked' in messages
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob('*') if p.is_file()} == before


def _replace_test_certificate(home, name, *, start_days=-1, end_days=30, sans=None, wrong_signer=False):
    """Keep a loadable matching key while varying public certificate evidence."""
    from datetime import UTC, datetime, timedelta
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    directory = home / 'config/tls'
    original = x509.load_pem_x509_certificate((directory / name).read_bytes())
    key_name = 'ca.key' if name == 'ca.crt' else 'leaf.key'
    key = serialization.load_pem_private_key((directory / key_name).read_bytes(), None)
    ca = x509.load_pem_x509_certificate((directory / 'ca.crt').read_bytes())
    issuer_key = serialization.load_pem_private_key((directory / 'ca.key').read_bytes(), None)
    if wrong_signer:
        issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    builder = (x509.CertificateBuilder().subject_name(original.subject).issuer_name(ca.subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now + timedelta(days=start_days)).not_valid_after(now + timedelta(days=end_days)))
    for extension in original.extensions:
        if sans is not None and isinstance(extension.value, x509.SubjectAlternativeName):
            builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=extension.critical)
        else:
            builder = builder.add_extension(extension.value, critical=extension.critical)
    certificate = builder.sign(issuer_key, hashes.SHA256())
    (directory / name).write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


@pytest.mark.parametrize('name,start,end', [('leaf.crt', -30, -1), ('leaf.crt', 1, 30),
                                          ('ca.crt', -30, -1), ('leaf.crt', -1, 20), ('ca.crt', -1, 20)])
def test_certificate_dates_and_ca_rotation_guidance(tmp_path, name, start, end):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import certificate_checks, setup_local_ca
    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    _replace_test_certificate(tmp_path, name, start_days=start, end_days=end)
    rows = certificate_checks(tmp_path, config)
    invalid = [row['message'] for row in rows if not row['ok']]
    assert any(('expires within 30 days' if end == 20 else 'expired or not yet valid') in message for message in invalid)
    if name == 'ca.crt':
        assert any('explicit CA rotation' in message and 'cannot renew it' in message for message in invalid)
    else:
        assert any('setup-tls force' in message and 'preserving the CA' in message for message in invalid)


def test_certificate_chain_signature_is_checked_not_just_pair_loadability(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import certificate_checks, setup_local_ca, ssl_context
    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    _replace_test_certificate(tmp_path, 'leaf.crt', wrong_signer=True)
    assert ssl_context(tmp_path, config)  # This alone does not verify issuance.
    rows = certificate_checks(tmp_path, config)
    assert any(not row['ok'] and 'chain could not be verified' in row['message'] for row in rows)


def test_san_wildcards_match_one_dns_label_never_ip_or_deeper_name(tmp_path):
    from cryptography import x509
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import certificate_checks, setup_local_ca
    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    _replace_test_certificate(tmp_path, 'leaf.crt', sans=[x509.DNSName('*.tail.example'),
                                                       x509.DNSName('127.0.0.1')])
    config = {**config, 'bind': ['0.0.0.0', '::', '127.0.0.1'],
              'public_origins': ['https://spark.tail.example', 'https://deep.spark.tail.example',
                                 'https://tail.example']}
    messages = '\n'.join(row['message'] for row in certificate_checks(tmp_path, config))
    assert 'SAN covers spark.tail.example' in messages
    for host in ['127.0.0.1', 'deep.spark.tail.example', 'tail.example']:
        assert 'SAN is missing ' + host in messages
    assert 'SAN is missing 0.0.0.0' not in messages and 'SAN is missing ::' not in messages


def test_supplied_certificate_diagnostics_do_not_recommend_overwriting_with_app_ca(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import certificate_checks, setup_local_ca
    source = tmp_path / 'supplier'
    config = setup_local_ca(source, load_server_config(source))
    host = tmp_path / 'host'
    supplied = {**config, 'tls': {'method': 'ca', 'cert': str(source / 'config/tls/leaf.crt'),
                                 'key': str(source / 'config/tls/leaf.key')},
                'public_origins': ['https://missing.example']}
    messages = '\n'.join(row['message'] for row in certificate_checks(host, supplied))
    assert 'SAN is missing missing.example' in messages
    assert 'supplied certificate/chain' in messages and 'setup-tls force' not in messages
    assert not host.exists()


def test_scoped_ipv6_uses_unscoped_san_identity_without_changing_transport_config(tmp_path):
    import ipaddress
    from cryptography import x509
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import certificate_checks, setup_local_ca
    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    _replace_test_certificate(tmp_path, 'leaf.crt',
                              sans=[x509.IPAddress(ipaddress.ip_address('fe80::123'))])
    config = {**config, 'bind': ['fe80::123%7'],
              'public_origins': ['https://[fe80::123%7]:8941']}
    messages = '\n'.join(row['message'] for row in certificate_checks(tmp_path, config))
    assert 'Certificate SAN covers fe80::123%7.' in messages
    assert 'SAN is missing' not in messages
    assert config['bind'] == ['fe80::123%7']
    assert config['public_origins'] == ['https://[fe80::123%7]:8941']


@pytest.mark.parametrize('missing', ['ca.crt', 'ca.key'])
def test_missing_ca_preserves_other_certificate_findings_and_does_not_regenerate(tmp_path, missing):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import certificate_checks, setup_local_ca
    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    (tmp_path / 'config/tls' / missing).unlink()
    config = {**config, 'public_origins': ['https://missing.example']}
    messages = '\n'.join(row['message'] for row in certificate_checks(tmp_path, config))
    assert 'SAN is missing missing.example' in messages
    assert 'Recover the existing CA from backup' in messages
    assert 'Run amplifier-unified setup-tls force' not in messages
    assert 'do not run setup-tls force' in messages
    assert not (tmp_path / 'config/tls' / missing).exists()
