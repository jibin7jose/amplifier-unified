"""Bounded file capture and reads through the existing immutable resource store.

Large files use a flat resource manifest. Chunks are ordinary canonical JSON
resources, so the existing output ownership, transactions and GC remain owners.
Temporary spools are preparation/download scratch, never another retained store.
"""
import asyncio
import base64
import codecs
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import shutil
import tempfile

from . import resource_files

FORMAT = 'file-chunks-v1'
CHUNK = 1024 * 1024
CHUNK_JSON = 4 * ((CHUNK + 2) // 3) + 128
EVIDENCE = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')


class Batch(list):
    def __init__(self):
        super().__init__()
        self.scratch = None

    @property
    def directory(self):
        if self.scratch is None:
            self.scratch = tempfile.TemporaryDirectory(prefix='amplifier-retained-')
        return self.scratch.name

    def close(self):
        if self.scratch is not None:
            self.scratch.cleanup()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.close()


@dataclass(frozen=True)
class Piece:
    path: Path
    identity: str
    size: int

    def load(self):
        with self.path.open('rb') as stream:
            encoded = stream.read(CHUNK_JSON + 1)
        if len(encoded) != self.size or hashlib.sha256(encoded).hexdigest() != self.identity:
            raise ValueError('Prepared file chunk changed before retention.')
        return resource_files.Prepared(encoded.decode(), self.identity, self.size)


def capture(root, path, expected, batch):
    from .outputs import workspace_file
    with workspace_file(root, path) as (stream, actual, name):
        before = os.fstat(stream.fileno())
        # Allow for the temporary encoded copy and its retained adoption. This
        # is current free-space admission, not a fixed file/declaration quota.
        needed = 2 * (4 * ((before.st_size + 2) // 3) + 128)
        if before.st_size > CHUNK and needed > shutil.disk_usage(batch.directory).free:
            raise OSError('Not enough free disk for file retention.')
        digest, size, pieces = hashlib.sha256(), 0, []
        small = before.st_size <= CHUNK
        data = b''
        while block := stream.read(CHUNK):
            size += len(block)
            if size > before.st_size:
                raise ValueError('Source changed while reading.')
            digest.update(block)
            if small:
                data += block
            else:
                prepared = resource_files.prepare({'encoding': 'base64', 'data': base64.b64encode(block).decode()})
                target = Path(batch.directory) / (prepared.identity + '.json')
                target.write_text(prepared.text)
                pieces.append(Piece(target, prepared.identity, prepared.size))
        after = os.fstat(stream.fileno())
        if size != before.st_size or any(getattr(before, key) != getattr(after, key) for key in EVIDENCE):
            raise ValueError('Source changed while reading.')
        if digest.hexdigest() != expected:
            return {'status': 'changed', 'detail': 'Source bytes do not match the declared SHA-256.'}
    if small:
        try:
            body = {'encoding': 'utf-8', 'data': data.decode('utf-8')}
        except UnicodeDecodeError:
            body = {'encoding': 'base64', 'data': base64.b64encode(data).decode()}
    else:
        body = {'format': FORMAT, 'bytes': size, 'sha256': expected,
                'chunks': [{'$resource': piece.identity, 'bytes': piece.size} for piece in pieces]}
    return {'status': 'retained', 'resource': resource_files.prepare(body), 'pieces': pieces,
            'bodyFormat': FORMAT if pieces else None, 'bytes': size, 'path': actual, 'filename': name}


async def prepared(function, *args):
    completed = []
    def work():
        completed.append(function(*args))
        return completed[0]
    try:
        return await resource_files.settle_worker(work)
    except BaseException:
        # settle_worker waits for capture to finish before cleaning scratch.
        for batch in completed:
            batch.close()
        raise


async def adopt(db, item):
    def verify(source, maximum):
        # Existing same-size files are preserved by put(). Establish the actual
        # adopted identity before a retained report can claim verification.
        # The same verifier also bounds the small {$blob: hash} index envelope.
        resource_files.read_detached(source, max_bytes=max(128, maximum))

    async def store(value):
        reference = await resource_files.put_prepared_async(db, value)
        source = resource_files.detach(db, reference['$resource'])
        await resource_files.settle_worker(verify, source, value.size)
        return reference

    directory = resource_files.root(db)
    pieces = item.get('pieces', [])
    if directory and pieces and sum(piece.size for piece in pieces) > shutil.disk_usage(directory.parent).free:
        raise OSError('Not enough free disk for retained file chunks.')
    for piece in pieces:
        value = await resource_files.settle_worker(piece.load)
        await store(value)
    return await store(item['resource'])


def manifest(source, record):
    if type(record.get('bytes')) is not int or record['bytes'] <= CHUNK:
        raise ValueError('Invalid retained file size.')
    count = math.ceil(record['bytes'] / CHUNK)
    body = resource_files.read_detached(source, max_bytes=256 + count * 160)
    if (not isinstance(body, dict) or body.get('format') != FORMAT
            or body.get('bytes') != record['bytes'] or body.get('sha256') != record['sha256']
            or not isinstance(body.get('chunks'), list) or len(body['chunks']) != count):
        raise ValueError('Invalid retained file manifest.')
    if any(not isinstance(item, dict) or set(item) != {'$resource', 'bytes'}
           or type(item['bytes']) is not int or not 0 < item['bytes'] <= CHUNK_JSON
           for item in body['chunks']):
        raise ValueError('Invalid retained file chunk reference.')
    return body['chunks']


async def plan(app, record):
    async with app.lock:
        source = resource_files.detach(app.db, record['body']['$resource'])
    chunks = await asyncio.to_thread(manifest, source, record)
    async with app.lock:
        return [resource_files.detach(app.db, item['$resource']) for item in chunks]


def chunks_verified(sources, record, consume):
    digest, size = hashlib.sha256(), 0
    for index, source in enumerate(sources):
        body = resource_files.read_detached(source, max_bytes=CHUNK_JSON)
        if not isinstance(body, dict) or body.get('encoding') != 'base64' or not isinstance(body.get('data'), str):
            raise ValueError('Invalid retained file chunk.')
        data = base64.b64decode(body['data'], validate=True)
        if not 0 < len(data) <= CHUNK or index + 1 < len(sources) and len(data) != CHUNK:
            raise ValueError('Invalid retained file chunk length.')
        size += len(data)
        digest.update(data)
        consume(data)
    if size != record['bytes'] or digest.hexdigest() != record['sha256']:
        raise ValueError('Retained file content no longer matches its evidence hash.')


def materialize(sources, record, maximum):
    if record['bytes'] > maximum:
        raise ValueError('This file is larger than the preview limit. Download the exact saved bytes.')
    data = bytearray()
    chunks_verified(sources, record, data.extend)
    return bytes(data)


def preview(sources, record, offset, limit):
    decoder = codecs.getincrementaldecoder('utf-8')()
    length, values, binary = 0, [], False
    def consume(data, final=False):
        nonlocal length, binary
        if binary:
            return
        try:
            text = decoder.decode(data, final=final)
        except UnicodeDecodeError:
            binary = True
            values.clear()
            return
        start, end = max(0, offset - length), min(len(text), offset + limit - length)
        if start < end:
            values.append(text[start:end])
        length += len(text)
    chunks_verified(sources, record, consume)
    consume(b'', final=True)
    return ({'binary': True} if binary else {'text': ''.join(values), 'offset': offset,
            'nextOffset': offset + limit if offset + limit < length else None})


@asynccontextmanager
async def download(app, record):
    sources = await plan(app, record)
    with tempfile.TemporaryFile(prefix='amplifier-download-') as stream:
        if record['bytes'] > shutil.disk_usage(tempfile.gettempdir()).free:
            raise OSError('Not enough free disk to prepare the verified download.')
        await resource_files.settle_worker(chunks_verified, sources, record, stream.write)
        stream.seek(0)
        # Nothing is sent until every chunk and the whole file are verified.
        yield stream
