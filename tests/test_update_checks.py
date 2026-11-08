import asyncio
import json
import pytest

from amplifier_web.update_checks import CheckCache, checking, cached, git_revision


async def test_cache_writes_use_the_same_explicit_owned_root_on_success_and_failure(tmp_path,monkeypatch):
    from amplifier_web import deployment
    original=deployment.write_private
    calls=[]
    directory=tmp_path/'updates'
    def private(path,text,*,owned_root=None):
        calls.append(owned_root)
        assert owned_root==directory, 'Windows private writes require an explicit owned_root'
        original(path,text,owned_root=owned_root)
    monkeypatch.setattr(deployment,'write_private',private)
    cache=CheckCache(directory)
    async def success():return 'public value'
    async def failed():raise ValueError('original lookup failure')
    try:
        assert await cache.get(['release'],success,fresh=True,ttl=60,scope='offline')=='public value'
        with pytest.raises(ValueError,match='original lookup failure'):
            await cache.get(['release'],failed,fresh=True,ttl=60,scope='offline')
        assert calls==[directory,directory]
        assert json.loads(cache.path.read_text())['entries']=={}
        assert cache.tasks=={}
    finally:await cache.close()


async def test_update_manager_cache_uses_its_data_identity_not_ambient_home(tmp_path,monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from test_service import Runtime
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(tmp_path/'unrelated'))
    service=AppService(tmp_path/'actual',Runtime(),workspace=tmp_path/'workspace')
    try:
        manager=service.update_manager=UpdateManager(service)
        assert manager.check_cache.owned_root==service.data_dir
    finally:await service.close()


async def test_fresh_checks_join_refs_and_warm_checks_reuse_across_restart(tmp_path):
    calls = []
    async def process(*args, **kwargs):
        calls.append(args)
        return '\n'.join('a' * 40 + '\t' + ref for ref in args[3:])
    cache = CheckCache(tmp_path)
    async with checking(cache):
        values = await asyncio.gather(*(git_revision('https://example.invalid/repo', ref, process) for ref in ['main', 'dev', 'main']))
        assert values == ['a' * 40] * 3
        assert len(calls) == 1
        assert set(calls[0][3:]) == {'refs/heads/main', 'refs/heads/dev'}
        await git_revision('https://example.invalid/repo.git', 'main', process)
        assert len(calls) == 1  # Host and worker use identical upstream evidence.
    restored = CheckCache(tmp_path)
    async with checking(restored, fresh=False):
        await git_revision('https://example.invalid/repo', 'main', process)
        assert len(calls) == 1
    async with checking(restored, fresh=True):
        await git_revision('https://example.invalid/repo', 'main', process)
        assert len(calls) == 2
    await cache.close()
    await restored.close()


async def test_failed_fresh_check_invalidates_success_and_access_changes_invalidate(tmp_path, monkeypatch):
    cache = CheckCache(tmp_path)
    calls = []
    async def success():
        calls.append('success')
        return 'saved'
    async with checking(cache):
        assert await cached(['item'], success) == 'saved'
    monkeypatch.setenv('GH_TOKEN', 'test-token-not-a-real-secret')
    async with checking(cache, fresh=False):
        assert await cached(['item'], success) == 'saved'
    assert len(calls) == 2
    async def fail():
        raise ValueError('unavailable')
    async with checking(cache):
        with pytest.raises(ValueError):
            await cached(['item'], fail)
    async with checking(cache, fresh=False):
        await cached(['item'], success)
    assert len(calls) == 3
    assert 'test-token-not-a-real-secret' not in cache.path.read_text()
    await cache.close()


async def test_ttl_and_cancelled_waiter_never_cancel_shared_request(tmp_path):
    now = [10]
    cache = CheckCache(tmp_path, clock=lambda: now[0])
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def load():
        calls.append(1)
        entered.set()
        await release.wait()
        return 'value'
    async with checking(cache):
        first = asyncio.create_task(cached(['item'], load))
        await entered.wait()
        second = asyncio.create_task(cached(['item'], load))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        assert await second == 'value'
    now[0] += 11
    async with checking(cache, fresh=False, ttl=10):
        assert await cached(['item'], load) == 'value'
    assert len(calls) == 2
    await cache.close()


def test_repository_roles_group_without_combining_revisions_or_policies():
    from amplifier_web.updates import group_sources
    base = {'id': 'cache', 'label': 'example.invalid/repo', 'ref': 'main', 'current': 'old', 'latest': 'new', 'status': 'update', 'kind': 'bundle / module', 'usage': 'unknown', 'usageEvidence': []}
    rows = [base, {**base, 'id': 'runtime:a', 'kind': 'runtime dependency', 'package': 'package-a', 'subdirectory': 'modules/a', 'usage': 'configured', 'usageEvidence': ['Worker']}, {**base, 'id': 'runtime:b', 'kind': 'runtime dependency', 'package': 'package-b', 'subdirectory': 'modules/b'}, {**base, 'id': 'pin', 'ref': 'fixed'}, {**base, 'id': 'old', 'current': 'older'}]
    grouped = group_sources(rows)
    assert len(grouped) == 3
    assert grouped[0]['packageCount'] == 2
    assert grouped[0]['cacheCopies'] == 1
    assert {member['id'] for member in grouped[0]['members']} == {'cache', 'runtime:a', 'runtime:b'}
    assert group_sources(grouped) == grouped


def test_staging_copy_has_distinct_inodes_and_preserves_symlinks(tmp_path):
    from amplifier_web.update_storage import copy_snapshot
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'file').write_text('live')
    (source / 'link').symlink_to('file')
    target = tmp_path / 'stage'
    copy_snapshot(source, target)
    assert (source / 'file').stat().st_ino != (target / 'file').stat().st_ino
    (target / 'file').write_text('candidate')
    assert (source / 'file').read_text() == 'live'
    assert (target / 'link').is_symlink()


async def test_corrupt_disk_cache_is_rebuildable_and_close_stops_batched_io(tmp_path):
    (tmp_path / 'check-cache.json').write_text(json.dumps({'version': 1, 'entries': {'invalid': {'checkedAt': 'yesterday'}, 'other': None}}))
    cache = CheckCache(tmp_path)
    assert cache.entries == {}
    entered = asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    async with checking(cache):
        caller = asyncio.create_task(git_revision('https://example.invalid/repo', 'main', blocked))
        await entered.wait()
        await cache.close()
        result = await asyncio.gather(caller, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert not cache.batch_tasks


async def test_distinct_git_access_environments_do_not_share_lookup(tmp_path):
    cache = CheckCache(tmp_path)
    calls = []
    async def runner(*args, env, **kwargs):
        calls.append(env)
        return ('a' if env['GIT_CONFIG_GLOBAL'] == 'first' else 'b') * 40 + '\trefs/heads/main'
    async with checking(cache):
        values = await asyncio.gather(*(git_revision('https://example.invalid/repo', 'main', runner, {'GIT_CONFIG_GLOBAL': value}) for value in ('first', 'second')))
    assert values == ['a' * 40, 'b' * 40]
    assert len(calls) == 2
    await cache.close()
