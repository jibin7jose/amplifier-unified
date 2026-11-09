import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from urllib.parse import urlsplit
import zipfile

from aiohttp import web
import pytest

from amplifier_web import app_release_artifacts as artifacts

VERSION = '99.0.0'
REVISION = 'a' * 40
STAMP = {'schema': 1, 'repository': artifacts.REPOSITORY, 'version': VERSION, 'revision': REVISION}
URL = artifacts.SOURCE + '/releases/download/v' + VERSION + '/' + artifacts.filename(VERSION)


def wheel(stamp=STAMP):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('amplifier_web/__init__.py', '__version__="99.0.0"')
        if stamp is not None:
            archive.writestr(artifacts.STAMP, json.dumps(stamp))
    return output.getvalue()


async def serve(monkeypatch, aiohttp_server, payload, *, checksum=None):
    requested = []
    checksum = checksum if checksum is not None else hashlib.sha256(payload).hexdigest()
    async def handler(request):
        if request.path.endswith('/SHA256SUMS'):
            return web.Response(text=checksum + '  ' + artifacts.filename(VERSION) + '\n')
        return web.Response(body=payload)
    app = web.Application()
    app.router.add_get('/{path:.*}', handler)
    server = await aiohttp_server(app)
    @asynccontextmanager
    async def get(client, url):
        requested.append(url)
        async with client.stream('GET', str(server.make_url(urlsplit(url).path))) as response:
            yield response
    monkeypatch.setattr(artifacts, '_get', get)
    return requested


async def test_prepare_checks_download_and_stamp_before_returning_install_input(tmp_path, monkeypatch, aiohttp_server):
    payload = wheel()
    requested = await serve(monkeypatch, aiohttp_server, payload)
    descriptor = await artifacts.prepare(tmp_path / 'candidate', 'v' + VERSION, REVISION)
    assert requested == [URL.rsplit('/', 1)[0] + '/SHA256SUMS', URL]
    assert descriptor == {'url': URL, 'sha256': hashlib.sha256(payload).hexdigest(),
                          'path': str(tmp_path / 'candidate' / artifacts.filename(VERSION))}
    assert Path(descriptor['path']).read_bytes() == payload
    assert not list(tmp_path.rglob('*.part'))
    assert artifacts.verify(descriptor, VERSION, REVISION) == descriptor
    assert artifacts.requirement(descriptor, ['tui']) == 'amplifier-unified[tui] @ ' + Path(descriptor['path']).as_uri()
    Path(descriptor['path']).write_bytes(b'changed before fallback install')
    with pytest.raises(ValueError, match='checksum'):
        artifacts.verify(descriptor, VERSION, REVISION)


async def test_feature_uses_selected_delivery_without_latest_or_checksum_lookup(tmp_path, monkeypatch, aiohttp_server):
    payload = wheel()
    requested = await serve(monkeypatch, aiohttp_server, payload)
    delivery = {'url': URL, 'sha256': hashlib.sha256(payload).hexdigest()}
    descriptor = await artifacts.prepare(tmp_path, VERSION, REVISION, delivery=delivery)
    assert requested == [URL]
    assert {key: descriptor[key] for key in delivery} == delivery


@pytest.mark.parametrize('stamp', [None, {}, {**STAMP, 'schema': True},
    {**STAMP, 'repository': 'someone/fork'}, {**STAMP, 'revision': 'b' * 40},
    {**STAMP, 'version': '98.0.0'}])
async def test_wrong_or_missing_source_never_publishes_install_input(tmp_path, monkeypatch, aiohttp_server, stamp):
    await serve(monkeypatch, aiohttp_server, wheel(stamp))
    with pytest.raises(ValueError):
        await artifacts.prepare(tmp_path, VERSION, REVISION)
    assert list(tmp_path.iterdir()) == []


async def test_wrong_checksum_cleans_partial_input(tmp_path, monkeypatch, aiohttp_server):
    await serve(monkeypatch, aiohttp_server, wheel(), checksum='b' * 64)
    with pytest.raises(ValueError, match='checksum'):
        await artifacts.prepare(tmp_path, VERSION, REVISION)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('manifest', ['not-a-checksum  unrelated.whl\n',
    'a' * 64 + '  ' + artifacts.filename(VERSION) + '\n' + 'b' * 64 + '  ' + artifacts.filename(VERSION),
    'x' * (artifacts.CHECKSUM_LIMIT + 1)])
async def test_missing_duplicate_or_oversize_manifest_never_starts_wheel(tmp_path, monkeypatch, manifest):
    import httpx
    calls = []
    @asynccontextmanager
    async def get(client, url):
        calls.append(url)
        yield httpx.Response(200, content=manifest.encode(), request=httpx.Request('GET', url))
    monkeypatch.setattr(artifacts, '_get', get)
    with pytest.raises(ValueError, match='manifest'):
        await artifacts.prepare(tmp_path, VERSION, REVISION)
    assert calls == [URL.rsplit('/', 1)[0] + '/SHA256SUMS']
    assert list(tmp_path.iterdir()) == []


async def test_cancellation_waits_for_read_only_verifier_before_cleanup(tmp_path, monkeypatch, aiohttp_server):
    await serve(monkeypatch, aiohttp_server, wheel())
    started, finish = threading.Event(), threading.Event()
    checked = []
    original = artifacts._verify_archive
    def verify(path, *args):
        started.set()
        assert finish.wait(2)
        checked.append(path.exists())
        original(path, *args)
    monkeypatch.setattr(artifacts, '_verify_archive', verify)
    task = asyncio.create_task(artifacts.prepare(tmp_path, VERSION, REVISION))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and list(tmp_path.glob('*.part'))
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        finish.set()
    assert checked == [True] and list(tmp_path.iterdir()) == []


async def test_cancellation_discards_only_its_incomplete_download(tmp_path, monkeypatch, aiohttp_server):
    sent, finish = asyncio.Event(), asyncio.Event()
    async def handler(request):
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b'incomplete wheel')
        sent.set()
        await finish.wait()
        return response
    app = web.Application()
    app.router.add_get('/wheel', handler)
    server = await aiohttp_server(app)
    @asynccontextmanager
    async def get(client, url):
        async with client.stream('GET', str(server.make_url('/wheel'))) as response:
            yield response
    monkeypatch.setattr(artifacts, '_get', get)
    sentinel = tmp_path / 'keep.txt'
    sentinel.write_text('other retained evidence')
    task = asyncio.create_task(artifacts.prepare(tmp_path, VERSION, REVISION,
        delivery={'url': URL, 'sha256': 'a' * 64}))
    try:
        await asyncio.wait_for(sent.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        finish.set()
    assert list(tmp_path.iterdir()) == [sentinel]


async def test_network_failure_is_sanitized_and_preserves_existing_input(tmp_path, monkeypatch):
    @asynccontextmanager
    async def fail(*args):
        raise asyncio.TimeoutError('private endpoint credential details')
        yield
    monkeypatch.setattr(artifacts, '_get', fail)
    with pytest.raises(ValueError, match='download failed') as error:
        await artifacts.prepare(tmp_path, VERSION, REVISION)
    assert 'private' not in str(error.value)
    target = tmp_path / artifacts.filename(VERSION)
    target.write_text('owned by another attempt')
    with pytest.raises(ValueError, match='already in use'):
        await artifacts.prepare(tmp_path, VERSION, REVISION)
    assert target.read_text() == 'owned by another attempt'


async def test_release_redirect_cannot_downgrade_to_http():
    calls, released = [], []
    @asynccontextmanager
    async def stream(method, url, **options):
        calls.append((url, options))
        try:
            yield SimpleNamespace(status_code=302, headers={'Location': 'http://example.invalid/asset'})
        finally:
            released.append(True)
    with pytest.raises(ValueError, match='HTTPS'):
        async with artifacts._get(SimpleNamespace(stream=stream), URL):
            pytest.fail('Downgraded response must not be yielded')
    assert len(calls) == 1 and calls[0][1] == {'follow_redirects': False}
    assert released == [True]


def test_source_facts_keep_git_fast_path_and_do_not_claim_official_delivery(tmp_path):
    git = SimpleNamespace(read_text=lambda _: json.dumps({'vcs_info': {'commit_id': REVISION}}))
    assert artifacts.source_revision(git) == REVISION  # No version or package lookup on old Git path.
    path = tmp_path / artifacts.STAMP
    path.parent.mkdir()
    path.write_text(json.dumps({**STAMP, 'repository': 'someone/fork'}))
    distribution = SimpleNamespace(metadata={'Name': 'amplifier-unified'}, version=VERSION,
        read_text=lambda _: json.dumps({'url': 'file:///manual.whl', 'archive_info': {}}),
        locate_file=lambda name: tmp_path / name)
    assert artifacts.source_revision(distribution) == REVISION
    assert artifacts.distribution_row(distribution)['stamp']['repository'] == 'someone/fork'
    path.write_text(json.dumps({**STAMP, 'version': '98.0.0'}))
    assert artifacts.source_revision(distribution) is None
    distribution.read_text = git.read_text
    distribution.locate_file = lambda _: pytest.fail('Git must not read a source stamp')
    assert 'stamp' not in artifacts.distribution_row(distribution)


@pytest.mark.parametrize('change', [{'url': URL + '?token=secret'}, {'sha256': 'A' * 64},
    {'path': 'relative.whl'}, {'path': '/tmp/other.whl'}, {'unexpected': True}])
def test_descriptor_rejects_noncanonical_delivery_and_path(tmp_path, change):
    value = {'url': URL, 'sha256': 'a' * 64, 'path': str(tmp_path / artifacts.filename(VERSION))}
    with pytest.raises(ValueError):
        artifacts.validate_descriptor({**value, **change}, VERSION)
