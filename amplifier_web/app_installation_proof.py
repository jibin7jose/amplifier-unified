"""Read-only wheel/installed-payload comparison for manual app handoffs.

An authenticated release download owns the expected bytes. RECORD, a source
stamp or direct_url alone is not that authority. No installation is performed.
"""
import base64
import configparser
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import zipfile


def _require(condition):
    if not condition:
        raise ValueError('The installed application does not match its release wheel.')


def _rows(raw, record):
    rows = {}
    for row in csv.reader(io.StringIO(raw.decode('utf-8')), strict=True):
        _require(len(row) == 3)
        name, fingerprint, size = row
        _require(name and '\\' not in name and '\0' not in name
                 and name not in rows and str(PurePosixPath(name)) == name)
        rows[name] = (fingerprint, size)
    _require(rows.get(record) == ('', ''))
    return rows


def _matches(raw, row):
    _require(row == ('sha256=' + base64.urlsafe_b64encode(
        hashlib.sha256(raw).digest()).decode().rstrip('='), str(len(raw))))


def verify_payload(wheel, distribution, prefix, scripts, *, read_bytes, sha256=None):
    """Compare all shipped bytes and narrowly allow uv-generated metadata/scripts.

    Based on the maintained native trial's candidate_members check. The caller
    supplies the platform's safe reader; this function never resolves a path
    before that reader can reject a reparse point or alias.
    """
    prefix, scripts = Path(prefix), Path(scripts)
    root = Path(distribution.locate_file(''))
    _require(root.is_relative_to(prefix) and scripts.is_relative_to(prefix))
    info = 'amplifier_unified-' + distribution.version + '.dist-info'
    record = info + '/RECORD'
    # Read the archive through the same guarded reader as installed files.
    wheel_bytes = read_bytes(Path(wheel))
    if sha256 is not None:
        _require(hashlib.sha256(wheel_bytes).hexdigest() == sha256)
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
        names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        _require(len(names) == len(set(names)))
        _require(all(not PurePosixPath(name).is_absolute()
                     and '..' not in PurePosixPath(name).parts and '\\' not in name
                     and ':' not in name and str(PurePosixPath(name)) == name for name in names))
        _require({name.split('/')[0] for name in names if '.dist-info/' in name} == {info})
        wheel_rows = _rows(archive.read(record), record)
        installed_rows = _rows(read_bytes(root / record), record)
        _require(set(wheel_rows) == set(names) and set(wheel_rows) <= set(installed_rows))
        for name in names:
            if name == record:
                continue  # Installer rewrites only this shipped member.
            raw = archive.read(name)
            _matches(raw, wheel_rows[name])
            actual = read_bytes(root / name)
            _require(actual == raw)
            _matches(actual, installed_rows[name])
        generated = {info + '/' + name for name in
                     ('direct_url.json', 'INSTALLER', 'REQUESTED', 'uv_cache.json', 'uv_build.json')}
        _require(not generated & set(wheel_rows))
        _require({info + '/direct_url.json', info + '/INSTALLER'} <= set(installed_rows))
        entries = configparser.ConfigParser(interpolation=None)
        entries.optionxform = str
        entries.read_string(archive.read(info + '/entry_points.txt').decode('utf-8'))
        _require(entries.sections() == ['console_scripts'])
        _require(dict(entries['console_scripts']) == {
            'amplifier-unified': 'amplifier_web.cli:main',
            'amplifier-unified-status': 'amplifier_web.status_protocol.cli:main'})
        launchers = {os.path.relpath(scripts / (name + '.exe'), root).replace(os.sep, '/'):
                     scripts / (name + '.exe') for name in entries['console_scripts']}
        _require(set(launchers) <= set(installed_rows))
        _require(set(installed_rows) - set(wheel_rows) <= generated | set(launchers))
        for name in set(installed_rows) - set(wheel_rows):
            path = launchers[name] if name in launchers else root / name
            raw = read_bytes(path)
            _matches(raw, installed_rows[name])
            if name == info + '/INSTALLER':
                _require(raw in {b'uv', b'uv\n'})
            elif name == info + '/REQUESTED':
                _require(raw == b'')
            elif name not in launchers:
                _require(len(raw) <= 65536 and isinstance(json.loads(raw), dict))
        actual_metadata = {info + '/' + path.relative_to(root / info).as_posix()
                           for path in (root / info).rglob('*') if path.is_file()}
        _require(actual_metadata == {name for name in installed_rows if name.startswith(info + '/')})
        # Reject unregistered code/assets in this wheel's package roots. Python
        # caches are not payload, but only the normal __pycache__/*.pyc spelling.
        package_roots = {name.split('/')[0] for name in names if '/' in name and not name.startswith(info + '/')}
        for package in package_roots:
            for path in (root / package).rglob('*'):
                if path.is_file():
                    relative = path.relative_to(root).as_posix()
                    _require(relative in wheel_rows or
                             path.parent.name == '__pycache__' and path.suffix == '.pyc')
        return {'members': len(names) - 1,
                'recordDigest': hashlib.sha256(read_bytes(root / record)).hexdigest()}