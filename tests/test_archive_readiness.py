"""Archive source facts and receipt-backed eligibility have distinct owners."""
from copy import deepcopy
from importlib import metadata
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import __version__, app_features, app_updates, application_generations, update_readiness
from amplifier_web.auth import data_identity
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager
from test_service import Runtime

REVISION = 'a' * 40
GENERATION = 'b' * 32
ATTEMPT = 'c' * 32


@pytest.fixture
def installation(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    folder = home / 'updates/applications' / REVISION / GENERATION
    prefix = folder / 'tools/amplifier-unified'
    site = prefix / 'lib/python3.13/site-packages'
    package = site / 'amplifier_web'
    info = site / f'amplifier_unified-{__version__}.dist-info'
    package.mkdir(parents=True)
    info.mkdir()
    (info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: amplifier-unified\nVersion: {__version__}\n')
    stamp = {'schema': 1, 'repository': 'microsoft/amplifier-unified', 'version': __version__, 'revision': REVISION}
    (package / 'release-source.json').write_text(json.dumps(stamp))
    wheel = folder / f'amplifier_unified-{__version__}-py3-none-any.whl'
    direct = {'url': wheel.as_uri(), 'archive_info': {}}
    (info / 'direct_url.json').write_text(json.dumps(direct))
    distribution = metadata.Distribution.at(info)
    original_distribution = metadata.distribution
    monkeypatch.setattr(metadata, 'distribution', lambda name: distribution if name == 'amplifier-unified' else original_distribution(name))
    monkeypatch.setattr(update_readiness, '__file__', str(package / 'update_readiness.py'))
    monkeypatch.setattr(app_features, '__file__', str(package / 'app_features.py'))
    monkeypatch.setattr(update_readiness.sys, 'prefix', str(prefix))
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: [])
    monkeypatch.setattr(app_updates.components, 'installed_graph', lambda: [])
    artifact = {'url': app_updates.SOURCE + f'/releases/download/v{__version__}/' + wheel.name,
                'sha256': 'd' * 64, 'path': str(wheel)}
    app = {'name': 'amplifier-unified', 'version': __version__, 'url': app_updates.SOURCE,
           'revision': REVISION, 'delivery': {key: artifact[key] for key in ('url', 'sha256')}}
    proof = {'app': app, 'artifact': artifact, 'extras': [], 'dependencyDigest': app_updates.components.digest([])}
    target = {'version': __version__, 'revision': REVISION, 'attemptId': ATTEMPT,
              'sourceInstanceId': 'old-process', 'qualification': proof}
    return SimpleNamespace(home=home, folder=folder, prefix=prefix, info=info, package=package,
                           stamp=stamp, direct=direct, artifact=artifact, app=app, proof=proof, target=target)


def manager_for(installation, state):
    service = AppService(installation.home, Runtime(), workspace=installation.home)
    service.state['updates'] = deepcopy(state)
    manager = UpdateManager(service)
    service.update_manager = manager
    return service, manager


def health(manager):
    return {'ok': True, 'app': 'amplifier-unified', 'dataIdentity': data_identity(manager.home),
            **manager.running_identity}


async def test_first_installer_stamp_is_source_fact_not_feature_eligibility(installation):
    identity = update_readiness.running_identity()
    assert set(identity) == {'version', 'revision', 'instanceId'}
    assert identity['revision'] == REVISION
    service, manager = manager_for(installation, {'phase': 'idle'})
    try:
        assert manager.running_identity['revision'] == REVISION
        assert update_readiness.artifact_context(manager) is None
        assert app_features.status(manager)['supported'] is False
    finally:
        await service.close()


@pytest.mark.parametrize('marker', ['pendingRestart', 'pendingReplacement'])
async def test_new_host_captures_exact_pending_archive_without_effect_replay(installation, monkeypatch, marker):
    def forbidden(*args, **kwargs):
        raise AssertionError('Read-only archive eligibility must not run a process or contact a release server')
    monkeypatch.setattr(app_updates, 'process', forbidden)
    monkeypatch.setattr(app_updates, 'github_read', forbidden)
    service, manager = manager_for(installation, {'phase': 'activating', marker: installation.target})
    try:
        assert update_readiness.artifact_context(manager) == installation.artifact
        assert app_features.running_application(manager) == installation.app
        assert app_features.status(manager)['supported'] is True
        assert service.state['updates'][marker] == installation.target
        assert 'applicationResult' not in service.state['updates']
        assert set(manager.running_identity) == {'version', 'revision', 'instanceId'}
        returned = update_readiness.artifact_context(manager)
        returned['sha256'] = 'e' * 64
        assert update_readiness.artifact_context(manager) == installation.artifact
    finally:
        await service.close()


async def test_real_ack_persists_observed_binding_before_pending_receipts_retire(installation):
    service, manager = manager_for(installation, {'phase': 'activating', 'pendingRestart': installation.target})
    try:
        assert not await manager.confirm_readiness({**health(manager), 'instanceId': 'other-process'})
        assert 'applicationResult' not in service.state['updates']
        assert await manager.confirm_readiness(health(manager))
        state = service.state['updates']
        result = deepcopy(state['applicationResult'])
        assert result['phase'] == 'restart-ack'
        assert result['qualification'] == installation.proof
        assert result['installation'] == application_generations.bootstrap_identity(installation.prefix)
        assert state['pendingRestart'] is None and state['pendingReplacement'] is None
        assert not await manager.confirm_readiness(health(manager))
        assert state['applicationResult'] == result
    finally:
        await service.close()
    restored = AppService(installation.home, Runtime(), workspace=installation.home)
    try:
        restored.update_manager = UpdateManager(restored)
        assert restored.state['updates']['applicationResult'] == result
        assert update_readiness.artifact_context(restored.update_manager) == installation.artifact
        assert app_features.running_application(restored.update_manager) == installation.app
    finally:
        await restored.close()


async def test_same_app_feature_keeps_exact_delivery_in_full_row_equality(installation):
    service, manager = manager_for(installation, {'phase': 'activating', 'pendingRestart': installation.target})
    try:
        selected = app_features.selection(manager, 'native-desktop', 'feature-request')
        assert selected['hostApp'] == installation.app
        assert app_features.validate_selection(manager, selected, ['native-desktop']) == ['native-desktop']
        altered = deepcopy(selected)
        altered['hostApp']['delivery']['sha256'] = 'e' * 64
        with pytest.raises(ValueError, match='serving app'):
            app_features.validate_selection(manager, altered, ['native-desktop'])
    finally:
        await service.close()


@pytest.mark.parametrize('changed', ['origin', 'metadata', 'stamp', 'prefix'])
async def test_current_binding_changes_invalidate_private_eligibility_without_relabeling_history(installation, monkeypatch, changed):
    result = {'attemptId': ATTEMPT, 'phase': 'restart-ack', 'version': __version__, 'revision': REVISION,
              'verifiedAt': 1, 'qualification': installation.proof,
              'installation': application_generations.bootstrap_identity(installation.prefix)}
    service, manager = manager_for(installation, {'phase': 'installed', 'applicationResult': result})
    try:
        assert update_readiness.artifact_context(manager) == installation.artifact
        old_identity = deepcopy(manager.running_identity)
        if changed == 'origin':
            (installation.info / 'direct_url.json').write_text(json.dumps({**installation.direct, 'url': 'file:///manual/different.whl'}))
        elif changed == 'metadata':
            path = installation.info / 'METADATA'
            path.write_text(path.read_text() + 'Summary: manual replacement\n')
        elif changed == 'stamp':
            (installation.package / 'release-source.json').write_text(json.dumps({**installation.stamp, 'revision': 'f' * 40}))
            assert update_readiness.running_identity()['revision'] == 'f' * 40
        else:
            monkeypatch.setattr(update_readiness.sys, 'prefix', str(installation.prefix.parent / 'another-install'))
        assert update_readiness.artifact_context(manager) is None
        assert app_features.status(manager)['supported'] is False
        assert manager.running_identity == old_identity
        assert service.state['updates']['applicationResult'] == result
    finally:
        await service.close()


@pytest.mark.parametrize('receipt', ['desired-only', 'missing-binding', 'wrong-binding', 'same-process', 'wrong-artifact'])
async def test_unobserved_or_stale_receipts_cannot_grant_eligibility(installation, monkeypatch, receipt):
    target = deepcopy(installation.target)
    if receipt == 'desired-only':
        state = {'pendingApp': target}
    elif receipt in {'missing-binding', 'wrong-binding'}:
        result = {**target, 'phase': 'restart-ack', 'verifiedAt': 1}
        if receipt == 'wrong-binding':
            result['installation'] = {'prefix': str(installation.prefix), 'digest': '0' * 64}
        state = {'applicationResult': result}
    else:
        if receipt == 'same-process':
            monkeypatch.setattr(update_readiness, 'running_identity', lambda: {'version': __version__, 'revision': REVISION, 'instanceId': 'old-process'})
        else:
            target['qualification']['artifact']['path'] = str(installation.folder / 'other' / Path(installation.artifact['path']).name)
        state = {'pendingRestart': target}
    service, manager = manager_for(installation, {'phase': 'activating', **state})
    try:
        assert update_readiness.artifact_context(manager) is None
        assert app_features.status(manager)['supported'] is False
        assert 'verifiedAt' not in service.state['updates'].get('applicationResult', {}) or receipt in {'missing-binding', 'wrong-binding'}
    finally:
        await service.close()


@pytest.mark.parametrize('mismatch', [None, 'prefix', 'binding', 'graph'])
async def test_active_generation_requires_actual_prefix_graph_and_recorded_binding(installation, monkeypatch, mismatch):
    graph = [installation.app]
    validated = {'revision': REVISION, 'generation': GENERATION, 'version': __version__,
                 'componentGraph': graph, 'componentDigest': app_updates.components.digest(graph),
                 'extras': [], 'artifact': installation.artifact,
                 'installation': application_generations.bootstrap_identity(installation.prefix)}
    if mismatch == 'binding':
        validated['installation']['digest'] = '0' * 64
    elif mismatch == 'graph':
        validated['componentGraph'] = [{**installation.app, 'revision': 'e' * 40}]
    elif mismatch == 'prefix':
        monkeypatch.setattr(update_readiness.sys, 'prefix', str(installation.prefix.parent / 'bootstrap'))
    (installation.folder / 'validated.json').write_text(json.dumps(validated))
    pointer = {'schema': 1, **{key: validated[key] for key in ('revision', 'generation', 'version', 'componentDigest')}}
    (installation.home / 'updates/application-active.json').write_text(json.dumps(pointer))
    service, manager = manager_for(installation, {'phase': 'idle'})
    try:
        assert (update_readiness.artifact_context(manager) == installation.artifact) is (mismatch is None)
        assert app_features.status(manager)['supported'] is (mismatch is None)
    finally:
        await service.close()
