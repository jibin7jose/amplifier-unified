"""Bounded snapshot transport through DTU's supported file/directory upload.

The source remains one immutable archive. Chunks are transport-only and never
become readiness evidence; the guest verifies the complete archive first.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile

MAX_ARCHIVE = 512 * 1024 * 1024
SINGLE_FILE_LIMIT = 128 * 1024 * 1024
CHUNK_BYTES = 64 * 1024 * 1024
BUFFER_BYTES = 1024 * 1024


def repository_client(source):
    path = Path(source) / 'components/web-client.source.json'
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        content = stream.read(16 * 1024 + 1)
    if len(content) > 16 * 1024:
        raise ValueError('The selected client source record exceeds its bound.')
    return json.loads(content).get('delivery', {}).get('type') == 'repository'


def export_snapshot(source, destination, ordinary_export):
    """Use the existing raw snapshot owner when ignored client inputs are required."""
    if not repository_client(source):
        return ordinary_export()
    result = subprocess.run([sys.executable, '-I', '-B',
                             str(Path(__file__).with_name('test_origin_snapshot.py')),
                             str(source), str(destination)], capture_output=True, timeout=300)
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError('Selected client source export failed. Run '
                         'python scripts/web_client_build.py prepare before transferring source; '
                         'keep the source stable and inspect its prepared selection.')
    return json.loads(result.stdout)


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def scan(archive, snapshot, parts=None):
    expected = snapshot.get('archive_sha256') if isinstance(snapshot, dict) else None
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('The source snapshot identity changed or is invalid.')
    original = os.stat(archive, follow_symlinks=False)
    if not stat.S_ISREG(original.st_mode):
        raise ValueError('The source archive must be a regular file.')
    descriptor = os.open(archive, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        if identity(before) != identity(original) or not stat.S_ISREG(before.st_mode) or before.st_size > MAX_ARCHIVE:
            raise ValueError('The source archive is unavailable or exceeds the 512 MiB limit.')
        if snapshot.get('archive_bytes', before.st_size) != before.st_size:
            raise ValueError('The source archive size changed.')
        digest, count = hashlib.sha256(), 0
        while count < before.st_size:
            size = min(CHUNK_BYTES, before.st_size - count)
            with ((parts / f'{count // CHUNK_BYTES:04d}').open('xb') if parts else nullcontext()) as target:
                remaining = size
                while remaining:
                    data = source.read(min(BUFFER_BYTES, remaining))
                    if not data:
                        raise ValueError('The source archive changed while being read.')
                    digest.update(data)
                    if parts:
                        target.write(data)
                    remaining -= len(data)
                    count += len(data)
        if (source.read(1) or identity(os.fstat(source.fileno())) != identity(before)
                or identity(os.stat(archive, follow_symlinks=False)) != identity(before)
                or digest.hexdigest() != expected):
            raise ValueError('The frozen source archive changed before transfer.')
    return count


def preflight(archive, snapshot):
    """Called before dependencies or guest admission; never mutates source."""
    return scan(Path(archive), snapshot)


@contextmanager
def staged(archive, snapshot):
    archive = Path(archive)
    # Check all sources again before admission, including already-prepared ones.
    if archive.stat().st_size <= SINGLE_FILE_LIMIT:
        size = preflight(archive, snapshot)
        yield archive, '/artifacts/.source.tar', size, False
        return
    with tempfile.TemporaryDirectory(prefix='.source-transfer-', dir=archive.parent) as directory:
        parts = Path(directory)
        size = scan(archive, snapshot, parts)
        yield parts, '/artifacts/.source-parts', size, True


GUEST = r'''
import hashlib,json,os,shutil,stat,sys,tarfile,tempfile
v=json.loads(sys.argv[1]); path=v['path']; expected=v['sha256']; size=v['bytes']
if not 0<=size<=512*1024*1024: raise ValueError('Invalid source size')
def verified(stream, output=None):
    digest=hashlib.sha256(); total=0
    for chunk in iter(lambda:stream.read(1024*1024),b''):
        total+=len(chunk)
        if total>size: raise ValueError('Source exceeds its recorded size')
        digest.update(chunk)
        if output is not None: output.write(chunk)
    return total,digest
if v['chunked']:
    if not stat.S_ISDIR(os.lstat(path).st_mode): raise ValueError('Invalid source parts')
    names=[f'{i:04d}' for i in range((size+64*1024*1024-1)//(64*1024*1024))]
    found=[]
    with os.scandir(path) as entries:
        for entry in entries:
            found.append(entry.name)
            if len(found)>len(names): raise ValueError('Unexpected source parts')
    if sorted(found)!=names: raise ValueError('Source parts are incomplete')
    with tempfile.TemporaryFile(dir='/artifacts') as archive:
        digest=hashlib.sha256(); total=0
        for index,name in enumerate(names):
            fd=os.open(path+'/'+name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as part:
                info=os.fstat(part.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size!=min(64*1024*1024,size-total):
                    raise ValueError('Source part size changed')
                for data in iter(lambda:part.read(1024*1024),b''):
                    total+=len(data)
                    if total>size: raise ValueError('Source exceeds its recorded size')
                    digest.update(data); archive.write(data)
        if total!=size or digest.hexdigest()!=expected: raise ValueError('Source hash changed')
        archive.seek(0)
        with tarfile.open(fileobj=archive) as source: source.extractall('/workspace',filter='data')
    shutil.rmtree(path)
else:
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as archive:
        if not stat.S_ISREG(os.fstat(archive.fileno()).st_mode): raise ValueError('Invalid source file')
        total,digest=verified(archive)
        if total!=size or digest.hexdigest()!=expected: raise ValueError('Source hash changed')
        archive.seek(0)
        with tarfile.open(fileobj=archive) as source: source.extractall('/workspace',filter='data')
    os.unlink(path)
'''


def command(destination, size, snapshot, chunked):
    value = {'path': destination, 'bytes': size, 'sha256': snapshot['archive_sha256'], 'chunked': chunked}
    return shlex.join(['python', '-c', GUEST, json.dumps(value, sort_keys=True)])
