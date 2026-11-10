"""Portable byte-comparison fixtures, not native NTFS or live-host proof."""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from amplifier_web.app_installation_proof import verify_payload

VERSION = '0.71.4'
INFO = f'amplifier_unified-{VERSION}.dist-info'
RECORD = INFO + '/RECORD'


def record(files):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator='\n')
    for name, raw in files.items():
        writer.writerow([name, 'sha256=' + base64.urlsafe_b64encode(
            hashlib.sha256(raw).digest()).decode().rstrip('='), str(len(raw))])
    writer.writerow([RECORD, '', ''])
    return stream.getvalue().encode()


@pytest.fixture
def installed(tmp_path):
    prefix = tmp_path / 'uv-tool'
    root, scripts = prefix / 'Lib/site-packages', prefix / 'Scripts'
    files = {
        'amplifier_web/__init__.py': b'__version__="0.71.4"\n',
        'amplifier_web/static/index.html': b'<title>fixture</title>',
        INFO + '/METADATA': b'Name: amplifier-unified\nVersion: 0.71.4\n',
        INFO + '/entry_points.txt': b'[console_scripts]\namplifier-unified = amplifier_web.cli:main\n'
                                  b'amplifier-unified-status = amplifier_web.status_protocol.cli:main\n',
    }
    wheel = tmp_path / f'amplifier_unified-{VERSION}-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, raw in {**files, RECORD: record(files)}.items():
            archive.writestr(name, raw)
    files.update({INFO + '/INSTALLER': b'uv\n', INFO + '/direct_url.json': b'{}',
                  '../../Scripts/amplifier-unified.exe': b'MZfixture-one',
                  '../../Scripts/amplifier-unified-status.exe': b'MZfixture-two'})
    for name, raw in {**files, RECORD: record(files)}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    dist = SimpleNamespace(version=VERSION, locate_file=lambda name: root / name)
    return SimpleNamespace(wheel=wheel, dist=dist, prefix=prefix, root=root, scripts=scripts, files=files)


def verify(value):
    return verify_payload(value.wheel, value.dist, value.prefix, value.scripts, read_bytes=Path.read_bytes)


def test_exact_installed_payload_and_uv_records(installed):
    assert verify(installed)['members'] == 4
    assert len(verify(installed)['recordDigest']) == 64


@pytest.mark.parametrize('name', ['amplifier_web/__init__.py', 'amplifier_web/static/index.html',
                                 INFO + '/METADATA', '../../Scripts/amplifier-unified.exe'])
def test_changed_installed_bytes_refuse_even_with_rewritten_record(installed, name):
    (installed.root / name).write_bytes(b'changed')
    installed.files[name] = b'changed'
    (installed.root / RECORD).write_bytes(record(installed.files))
    # Generated launcher bytes have their own native ownership check. They may
    # change consistently with RECORD here, unlike any shipped member.
    if name.startswith('../../Scripts'):
        assert verify(installed)
    else:
        with pytest.raises(ValueError):
            verify(installed)


@pytest.mark.parametrize('name', ['amplifier_web/extra.py', INFO + '/unknown.json'])
def test_unregistered_or_registered_extra_payload_refuses(installed, name):
    (installed.root / name).write_bytes(b'{}')
    with pytest.raises(ValueError):
        verify(installed)
    installed.files[name] = b'{}'
    (installed.root / RECORD).write_bytes(record(installed.files))
    with pytest.raises(ValueError):
        verify(installed)


def test_only_python_cache_is_ignored(installed):
    cache = installed.root / 'amplifier_web/__pycache__'
    cache.mkdir()
    (cache / '__init__.cpython-313.pyc').write_bytes(b'cache')
    assert verify(installed)
    (cache / 'injected.py').write_bytes(b'code')
    with pytest.raises(ValueError):
        verify(installed)


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'wrong-hash', 'unknown-installer'])
def test_invalid_installed_record_refuses(installed, mutation):
    path = installed.root / RECORD
    raw = path.read_bytes()
    if mutation == 'duplicate':
        raw += raw.splitlines()[0] + b'\n'
    elif mutation == 'missing':
        raw = b'\n'.join(raw.splitlines()[1:]) + b'\n'
    elif mutation == 'wrong-hash':
        raw = raw.replace(b'sha256=', b'sha512=', 1)
    else:
        installed.files[INFO + '/INSTALLER'] = b'pip'
        (installed.root / INFO / 'INSTALLER').write_bytes(b'pip')
        raw = record(installed.files)
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        verify(installed)


def test_reader_refusal_is_not_bypassed(installed):
    def refused(path):
        raise PermissionError('fixture native boundary refused')
    with pytest.raises(PermissionError):
        verify_payload(installed.wheel, installed.dist, installed.prefix, installed.scripts, read_bytes=refused)