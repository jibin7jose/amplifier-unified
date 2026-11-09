"""A waiting update follows newer releases before its single guarded restart."""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, application_generations
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager, work_paused
from test_service import Runtime
from test_app_updates import synthetic_release_artifact, synthetic_artifact_graph, synthetic_tool_install


def release(letter='a', version='99.0.0'):
    return {'id': 'application', 'kind': 'app', 'status': 'update', 'canInstall': True,
            'revision': letter * 40, 'latest': 'v' + version}


@pytest.fixture
async def waiting(tmp_path, monkeypatch):
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    await service.dispatch('session.create', {'title': 'Uninterrupted work'})
    service._session()['status'] = 'working'
    manager = service.update_manager = UpdateManager(service)
    state = service.state['updates']
    state.update(application=release(), items=[release()], appAvailable=True,
                 lastCheck=time.time()-7200, sequence={'stage': 'application', 'install': True})
    service.state['settings']['updates'].update(autoCheck=True, autoInstall=False, intervalHours=1)
    fixture = SimpleNamespace(service=service, manager=manager, state=state, latest=release('b', '99.0.1'),
                              checks=0, installs=[], promotions=[], restarts=[], fail_probe=False)
    host = [{'name': 'amplifier-core', 'version': '1.6.1'}]
    def revision_of(python):
        return next(part for part in Path(python).parts if part in {'a'*40, 'b'*40})
    def version_of(python):
        return '99.0.0' if revision_of(python) == 'a'*40 else '99.0.1'
    async def check():
        fixture.checks += 1
        return copy.deepcopy(fixture.latest)
    async def process(*args, env=None, **kwargs):
        if 'tool' in args:
            fixture.installs.append(str(args[-1]))
            synthetic_tool_install(args, {'env': env})
            python = Path(env['UV_TOOL_DIR'])/'amplifier-unified/bin/python'
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
            return ''
        if fixture.fail_probe and revision_of(args[0]) == 'b'*40:
            raise RuntimeError('Synthetic candidate validation failure')
        return version_of(args[0])
    async def graph(runner, python, *, artifact=None):
        return synthetic_artifact_graph([*host, {'name': 'amplifier-unified', 'version': version_of(python),
                       'url': app_updates.SOURCE, 'revision': revision_of(python)}], artifact)
    async def target():
        return '/synthetic/uv', '/synthetic/launcher', tmp_path/'bootstrap/bin/python', {'installation': str(tmp_path/'bootstrap')}
    def promote(home, validated, bootstrap):
        assert work_paused(service.state)
        fixture.promotions.append(validated['revision'])
        return {'prefix': application_generations.directory(home, validated['revision'], validated['generation'])/'tools/amplifier-unified'}
    async def restart(owner):
        fixture.restarts.append(copy.deepcopy(state['pendingRestart']))
    async def no_inventory(**kwargs):
        pytest.fail('Waiting app must not inspect the old host component inventory')
    monkeypatch.setattr(app_updates, 'check', check)
    monkeypatch.setattr(app_updates.artifacts, 'prepare', synthetic_release_artifact)
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr(app_updates.shutil, 'which', lambda _: '/synthetic/uv')
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: [])
    monkeypatch.setattr(app_updates.components, 'installed_graph', lambda: host)
    monkeypatch.setattr(app_updates.components, 'read_graph', graph)
    monkeypatch.setattr(app_updates, 'installed_target', target)
    monkeypatch.setattr(application_generations, 'supports', lambda _: True)
    monkeypatch.setattr(application_generations, 'can_read_candidate', AsyncMock(return_value=True))
    monkeypatch.setattr(application_generations, 'promote', promote)
    monkeypatch.setattr(app_updates, 'request_managed_restart', restart)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda _: True)
    monkeypatch.setattr(manager, 'inventory_sources', no_inventory)
    await app_updates.stage(manager)
    fixture.original = copy.deepcopy(state['pendingApp'])
    yield fixture
    await service.close()


async def test_busy_host_refreshes_and_validates_newest_then_restarts_once(waiting):
    w = waiting
    await w.manager.tick()
    assert w.checks == 1 and len(w.installs) == 2
    assert w.state['pendingApp']['revision'] == 'b'*40
    assert not w.promotions and not w.restarts and not work_paused(w.service.state)
    assert w.service._session()['status'] == 'working'
    w.service._session()['status'] = 'idle'
    await w.manager.tick()
    assert w.promotions == ['b'*40]
    assert [r['version'] for r in w.restarts] == ['99.0.1']
    assert not w.service.runtime.sent
    await w.manager.tick()
    assert len(w.restarts) == 1


@pytest.mark.parametrize('auto_check', [False, True])
async def test_idle_window_refreshes_even_between_scheduled_checks(waiting, auto_check):
    w = waiting
    w.state['lastCheck'] = time.time()
    w.service.state['settings']['updates']['autoCheck'] = auto_check
    w.service._session()['status'] = 'idle'
    await w.manager.tick()
    assert w.checks == 1
    assert [r['version'] for r in w.restarts] == ['99.0.1']


async def test_manual_check_is_read_only_and_keeps_validated_receipt(waiting):
    w = waiting
    before = copy.deepcopy(w.state['sequence'])
    await w.manager.command('check')
    assert w.state['application']['revision'] == 'b'*40
    assert w.state['pendingApp'] == w.original
    assert w.state['phase'] == 'app-staged' and w.state['sequence'] == before
    assert len(w.installs) == 1 and not w.promotions
    assert not w.state['applicationCheckPending']


async def test_idle_and_install_during_refresh_cannot_activate_old_target(waiting, monkeypatch):
    w = waiting
    entered, finish = asyncio.Event(), asyncio.Event()
    async def delayed_check():
        entered.set()
        await finish.wait()
        return release('b', '99.0.1')
    monkeypatch.setattr(app_updates, 'check', delayed_check)
    task = asyncio.create_task(w.manager.command('check'))
    await entered.wait()
    w.service._session()['status'] = 'idle'
    install = asyncio.create_task(w.manager.command('install'))
    tick = asyncio.create_task(w.manager.tick())
    try:
        await asyncio.sleep(0)
        assert not w.promotions and w.state['pendingApp'] == w.original
    finally:
        finish.set()
        await asyncio.gather(task, install, tick)
    assert w.promotions == ['b'*40] and len(w.restarts) == 1


async def test_failed_newer_validation_keeps_old_receipt_but_requires_retry(waiting):
    w = waiting
    w.fail_probe = True
    with pytest.raises(RuntimeError, match='Synthetic'):
        await w.manager.tick()
    assert w.state['pendingApp'] == w.original and w.state['phase'] == 'error'
    w.service._session()['status'] = 'idle'
    await w.manager.tick()
    assert not w.promotions and not w.restarts
    w.fail_probe = False
    await w.manager.command('install')
    assert w.promotions == ['b'*40] and len(w.restarts) == 1


async def test_offline_refresh_keeps_prepared_update_usable_without_poll_spin(waiting):
    w = waiting
    w.latest = {'status': 'check_failed'}
    await w.manager.tick()
    assert w.state['pendingApp'] == w.original and w.state['phase'] == 'app-staged'
    assert w.state['applicationCheckWarning'] and not w.state.get('error')
    await w.manager.tick()
    assert w.checks == 1
    w.service._session()['status'] = 'idle'
    await w.manager.tick()
    assert w.promotions == ['a'*40] and len(w.restarts) == 1


@pytest.mark.parametrize('latest', [release('b', '98.0.0'), release('b', '99.0.0'),
                                   release('a', '99.0.1'), release('x', '99.0.1')])
async def test_older_moved_or_invalid_release_cannot_replace_qualified_target(waiting, latest):
    w = waiting
    w.latest = latest
    await w.manager.tick()
    assert w.state['pendingApp'] == w.original and len(w.installs) == 1


async def test_late_old_metadata_does_not_discard_known_newer_target(waiting):
    w = waiting
    await w.manager.check()
    w.latest = release()
    await w.manager.check()
    assert w.state['application']['revision'] == 'b'*40
    await w.manager.tick()
    assert w.state['pendingApp']['revision'] == 'b'*40


async def test_newer_check_while_waiting_for_runtime_lock_prevents_old_activation(waiting):
    w = waiting
    w.service._session()['status'] = 'idle'
    # The real lifecycle owner can be occupied independently of the update
    # owner. A successful newer check in that interval must win before adoption.
    async with w.service.runtime_lifecycle():
        activation = asyncio.create_task(app_updates.activate(w.manager))
        await asyncio.sleep(0)
        await w.manager.check()
        assert w.state['application']['revision'] == 'b'*40
    await activation
    assert not w.promotions and not w.restarts
    assert w.state['pendingApp'] == w.original
    await w.manager.tick()
    assert w.promotions == ['b'*40] and len(w.restarts) == 1


async def test_confirmed_force_restart_keeps_exact_reviewed_candidate(waiting):
    from amplifier_web.update_restart import target
    w = waiting
    await w.manager.check()
    confirmed = {**target(w.manager), 'confirm': True}
    await w.manager.command('forceRestart', confirmed, 'force-reviewed')
    assert w.promotions == ['a'*40]
    assert w.restarts[0]['forceRestart']['revision'] == 'a'*40
    assert len(w.installs) == 1


@pytest.mark.parametrize('boundary', ['featureSelection', 'pendingRestart', 'pendingReplacement'])
async def test_selected_features_and_started_handoffs_are_not_retargeted(waiting, boundary):
    w = waiting
    if boundary == 'featureSelection':
        w.state['pendingApp'][boundary] = {'requestId': 'explicit-feature'}
    else:
        w.state[boundary] = {'version': '99.0.0'}
    await w.manager.check()
    assert w.checks == 0 and len(w.installs) == 1
