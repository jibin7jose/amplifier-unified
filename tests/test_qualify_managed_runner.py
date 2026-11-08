"""No-replay and private storage controls for the opt-in native qualifier."""
import importlib.util
import asyncio
import json
import multiprocessing
from multiprocessing.connection import wait
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import test_environment_setup as setup

spec = importlib.util.spec_from_file_location('qualifier', Path(__file__).parents[1] / 'scripts/qualify_managed_runner.py')
qualifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualifier)


def test_private_home_refuses_public_existing_directory_and_symlink(tmp_path):
    folder = tmp_path / 'public'
    folder.mkdir(mode=0o755)
    with pytest.raises(ValueError, match='private owned'):
        qualifier.private_home(folder)
    assert not list(folder.iterdir())
    link = tmp_path / 'link'
    link.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        qualifier.private_home(link)
    assert qualifier.private_home(tmp_path / 'private').stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('recorded', [False, True])
async def test_interrupted_staging_never_reinstalls(tmp_path, monkeypatch, recorded):
    app = SimpleNamespace(data_dir=tmp_path)
    identity = 'a' * 32
    attempt = {'runnerUpdate': identity, 'previous': {'old': True}, 'previousNormalized': {'normalized': True}}
    setup.write_json(tmp_path / 'qualification-update.json', attempt)
    path = qualifier.updates.record_path(app, identity)
    if recorded:
        setup.write_json(path, {'state': 'installing', 'previous': attempt['previous']})
    install = AsyncMock()
    monkeypatch.setattr(qualifier.updates, 'stage', install)
    with pytest.raises(ValueError, match='without replay'):
        await qualifier.stage_once(app, 'b' * 40)
    install.assert_not_called()


async def test_staging_intent_precedes_dispatch_and_keeps_predecessor_on_reentry(tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path)
    old, new = {'installationId': 'old'}, {'installationId': 'new'}
    selected = old
    source = {'runnerSelection': 'selection', 'eligible': True, 'runnerPolicy': {'origin': 'app-setup'}}
    monkeypatch.setattr(qualifier.updates, 'raw_configuration', lambda _: selected)
    monkeypatch.setattr(qualifier.updates, 'update_sources', lambda _: [dict(source)])
    monkeypatch.setattr(qualifier.environments, 'validate_configuration_value', lambda app, config: ({**config, 'normalized': True}, 'ready', ''))
    async def stage(app, source):
        identity = qualifier.updates.intent_id(source)
        assert setup.read_json(tmp_path / 'qualification-update.json')['runnerUpdate'] == identity
        setup.write_json(qualifier.updates.record_path(app, identity), {
            'previous': old, 'revision': 'b' * 40, 'candidate': new,
            'normalized': new, 'runtime': {'hash': 'retained'}})
        return {'runnerUpdate': identity}
    dispatch = AsyncMock(side_effect=stage)
    monkeypatch.setattr(qualifier.updates, 'stage', dispatch)
    first = await qualifier.stage_once(app, 'b' * 40)
    selected = new
    assert await qualifier.stage_once(app, 'b' * 40) == first
    assert first['previous'] == old and first['previousNormalized']['installationId'] == 'old'
    dispatch.assert_awaited_once()


@pytest.fixture
def isolated_driver(tmp_path, monkeypatch):
    """Real setup coordinator; controlled installs/daemon effects, no containers."""
    home = tmp_path / 'driver'
    home.mkdir(mode=0o700)
    calls = []
    def choice(name='a'):
        return {'id': 'docker', 'available': True, 'executable': '/usr/bin/docker',
                'dockerHost': 'unix:///' + name + '.sock'}
    monkeypatch.setattr(setup, 'discover', lambda: [choice()])
    monkeypatch.setattr(setup, 'command', lambda argv, **kw:
                        json.dumps({'ID': 'a' if argv[argv.index('--host') + 1] == 'unix:///a.sock' else 'b'}))
    class Manager:
        def __init__(self, app):
            self.state = {'installations': []}
            self.install = AsyncMock(return_value={'id': 'a' * 24,
                'commit': qualifier.environments.DTU_COMMIT, 'extras': []})
            self.close = AsyncMock()
    monkeypatch.setattr(qualifier, 'SmartToolsManager', Manager)
    monkeypatch.setattr(qualifier.environments, 'configuration', lambda _: (None, 'disabled', ''))
    monkeypatch.setattr(qualifier.environments, 'validate_configuration_value',
                        lambda app, config, **kw: (config, 'ready', ''))
    async def backend(base, selected, owned, record, step):
        calls.append(selected['dockerHost'])
        return {**base, 'dockerHost': selected['dockerHost']}, {
            'backend': 'docker', 'daemon': 'a' if selected['dockerHost'] == 'unix:///a.sock' else 'b',
            'probeRemoved': True}
    monkeypatch.setattr(setup, 'setup_docker', backend)
    return home, choice, calls


async def test_changed_setup_discovery_refused_before_native_effect(isolated_driver, monkeypatch):
    home, choice, calls = isolated_driver
    discoveries = iter([[choice('a')], [choice('b')]])
    monkeypatch.setattr(setup, 'discover', lambda: next(discoveries))
    stage = AsyncMock(side_effect=ValueError('Stopped after otherwise-valid B setup'))
    monkeypatch.setattr(qualifier, 'stage_once', stage)
    with pytest.raises(ValueError):
        await qualifier.qualify(home, 'b' * 40, 'a', 2)
    assert calls == [], 'The second discovery dispatched native setup on unexpected daemon B'
    stage.assert_not_called()


def retained_setup(home, *, daemon='a'):
    app = SimpleNamespace(data_dir=home)
    identity = 'c' * 32
    record = {'id': identity, 'state': 'completed', 'backend': 'docker',
              'intent': {'sessionId': 'native-qualification', 'workspaceId': 'native-qualification'},
              'proof': {'backend': 'docker', 'daemon': daemon, 'probeRemoved': True},
              'candidateConfiguration': {'enabled': True, 'backend': 'docker', 'dockerHost': 'unix:///a.sock'}}
    setup.write_json(home / 'qualification-intent.json', {
        'version': 1, 'commit': 'b' * 40, 'daemon': 'a', 'owner': 'native-runner-qualification'})
    setup.write_json(setup.root(app) / (identity + '.json'), record)
    setup.write_json(setup.root(app) / 'latest.json', {'id': identity})
    setup.write_json(home / 'test-environments.json', record['candidateConfiguration'])
    return app


async def test_retained_wrong_setup_proof_refused_before_staging(isolated_driver, monkeypatch):
    home, _, calls = isolated_driver
    retained_setup(home, daemon='b')
    stage = AsyncMock(side_effect=ValueError('Staging reached with wrong proof'))
    monkeypatch.setattr(qualifier, 'stage_once', stage)
    with pytest.raises(ValueError):
        await qualifier.qualify(home, 'b' * 40, 'a', 2)
    assert calls == []
    stage.assert_not_called()


@pytest.mark.skipif(os.name != 'posix', reason='Native qualifier is Linux-only')
def test_two_processes_same_home_dispatch_once_and_loser_busy(isolated_driver, monkeypatch):
    home, _, _ = isolated_driver
    app = retained_setup(home)
    original = qualifier.updates.raw_configuration(app)
    monkeypatch.setattr(qualifier.updates, 'update_sources', lambda _: [{
        'runnerSelection': 'selection', 'eligible': True, 'runnerPolicy': {'origin': 'app-setup'}}])
    context = multiprocessing.get_context('fork')
    release_read, release_stage = context.Event(), context.Event()
    stage_count = context.Value('i', 0)
    original_read = setup.read_json
    processes, connections = [], []

    def child(channel):
        read_signalled = False
        def read(path):
            nonlocal read_signalled
            value = original_read(path)
            if Path(path).name == 'qualification-update.json' and value is None and not read_signalled:
                read_signalled = True
                channel.send('read-missing')
                assert release_read.wait(10)
            return value
        setup.read_json = read
        async def stage(app, source):
            with stage_count.get_lock():
                stage_count.value += 1
            identity = qualifier.updates.intent_id(source)
            setup.write_json(qualifier.updates.record_path(app, identity), {
                'previous': original, 'revision': 'b' * 40,
                'candidate': original, 'normalized': original, 'runtime': {'fixture': True}})
            channel.send('stage')
            assert await asyncio.to_thread(release_stage.wait, 10)
            raise ValueError('Controlled staging stop')
        qualifier.updates.stage = stage
        try:
            asyncio.run(qualifier.qualify(home, 'b' * 40, 'a', 2))
        except BaseException as exc:
            channel.send(str(exc))
        finally:
            channel.close()

    try:
        for index in range(2):
            parent, child_end = context.Pipe()
            process = context.Process(target=child, args=(child_end,))
            process.start()
            child_end.close()
            processes.append(process)
            connections.append(parent)
            assert wait([parent], timeout=10), 'Worker did not reach the controlled boundary'
            message = parent.recv()
            if index == 0:
                assert message == 'read-missing'
                before = {p.relative_to(home): p.read_bytes() for p in home.rglob('*.json')}
            else:
                loser_message = message
                journal_unchanged = {p.relative_to(home): p.read_bytes() for p in home.rglob('*.json')} == before
        release_read.set()
        assert wait([connections[0]], timeout=10)
        assert connections[0].recv() == 'stage'
        release_stage.set()
        assert wait([connections[0]], timeout=10)
        assert connections[0].recv() == 'Controlled staging stop'
    finally:
        release_read.set()
        release_stage.set()
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
        for connection in connections:
            connection.close()
    assert stage_count.value == 1, f'{stage_count.value} staging dispatches; loser said {loser_message!r}'
    assert loser_message == 'Qualification is already running for this private home.'
    assert journal_unchanged


@pytest.mark.parametrize('boundary', ['selected-daemon', 'step-daemon', 'returned-proof'])
async def test_selected_daemon_is_rechecked_around_setup_effects(isolated_driver, monkeypatch, boundary):
    home, choice, calls = isolated_driver
    observed = 'b' if boundary == 'selected-daemon' else 'a'
    monkeypatch.setattr(setup, 'command', lambda *a, **kw: json.dumps({'ID': observed}))
    async def backend(base, selected, owned, record, step):
        nonlocal observed
        if boundary == 'step-daemon':
            observed = 'b'
        await step('checking_profile', 'Before effect')
        calls.append('effect')
        return {'dockerHost': selected['dockerHost']}, {'backend': 'docker', 'daemon': 'b'}
    monkeypatch.setattr(setup, 'setup_docker', backend)
    original = setup.setup_docker
    with qualifier.bound_setup(choice(), 'a'):
        with pytest.raises(ValueError):
            await setup.setup_docker({}, choice(), home, {}, AsyncMock())
    assert setup.setup_docker is original
    assert calls == (['effect'] if boundary == 'returned-proof' else [])


@pytest.mark.parametrize('boundary', ['candidate', 'prepared', 'after-prepare', 'final-proof'])
async def test_wrong_qualification_binding_blocks_further_effects(isolated_driver, monkeypatch, boundary):
    home, _, calls = isolated_driver
    app = retained_setup(home)
    original = qualifier.updates.raw_configuration(app)
    identity = 'd' * 32
    row = {'backendIdentity': {'backend': 'docker', 'daemon': 'b'}}
    record = {'id': identity, 'previous': original, 'candidate': original,
              'normalized': original, 'revision': 'b' * 40, 'runtime': {'fixture': True}}
    if boundary == 'candidate':
        record['candidate'] = {**original, 'dockerHost': 'unix:///b.sock'}
    if boundary == 'prepared':
        record.update(resourceId='owned', probeState='capacity')
    if boundary == 'final-proof':
        record.update(proof={'backendIdentity': {'backend': 'docker', 'daemon': 'b'}})
    path = qualifier.updates.record_path(app, identity)
    setup.write_json(path, record)
    monkeypatch.setattr(qualifier.environments, 'get', lambda *a: row)
    monkeypatch.setattr(qualifier, 'stage_once', AsyncMock(return_value={
        'runnerUpdate': identity, 'previous': original, 'previousNormalized': original}))
    async def advance(app, identity):
        record.update(resourceId='owned', probeState='capacity')
        setup.write_json(path, record)
        return {'complete': False, 'waiting': 'waiting'}
    dispatch = AsyncMock(side_effect=advance)
    monkeypatch.setattr(qualifier.updates, 'advance', dispatch)
    with pytest.raises(ValueError):
        await qualifier.qualify(home, 'b' * 40, 'a', 2)
    assert calls == []
    assert dispatch.await_count == (1 if boundary == 'after-prepare' else 0)


@pytest.mark.skipif(os.name != 'posix', reason='Native qualifier is Linux-only')
def test_process_lock_covers_final_persistence_and_releases_after_error(isolated_driver, monkeypatch):
    home, _, _ = isolated_driver
    retained_setup(home)
    monkeypatch.setattr(qualifier, 'stage_once', AsyncMock(side_effect=ValueError('Controlled failure')))
    context = multiprocessing.get_context('fork')
    release = context.Event()
    parent, child_end = context.Pipe()
    original_write = setup.write_json
    def child(channel):
        def write(path, value):
            if Path(path).name == 'qualification-result.json':
                channel.send('persisting')
                assert release.wait(10)
            return original_write(path, value)
        setup.write_json = write
        try:
            asyncio.run(qualifier.qualify(home, 'b' * 40, 'a', 2))
        except ValueError:
            pass
        finally:
            channel.close()
    process = context.Process(target=child, args=(child_end,))
    process.start()
    child_end.close()
    try:
        assert wait([parent], timeout=10) and parent.recv() == 'persisting'
        before = {p.relative_to(home): p.read_bytes() for p in home.rglob('*.json')}
        with pytest.raises(ValueError, match='already running'):
            asyncio.run(qualifier.qualify(home, 'b' * 40, 'a', 2))
        assert {p.relative_to(home): p.read_bytes() for p in home.rglob('*.json')} == before
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
    assert process.exitcode == 0
    with qualifier.FileLock(str(home / 'qualification.lock'), timeout=0):
        assert setup.read_json(home / 'qualification-result.json')['error'] == 'Controlled failure'


async def test_lock_file_does_not_make_an_unrelated_home_eligible(isolated_driver):
    home, _, calls = isolated_driver
    (home / 'unrelated.txt').write_text('preserve')
    with pytest.raises(ValueError, match='empty task-private'):
        await qualifier.qualify(home, 'b' * 40, 'a', 2)
    assert (home / 'unrelated.txt').read_text() == 'preserve'
    assert not list(home.glob('*.json')) and calls == []
