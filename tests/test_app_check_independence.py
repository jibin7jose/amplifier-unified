"""App availability is independent of component preparation, not its guards.

These tests use the real manager, app release parser, component inventory, local
Git remote, source copy/preflight and activation pointer. Only the external
metadata read fails. Component validation is an explicit test boundary: reaching
it and promoting the fixture is NOT native worker or installed-graph qualification.
The pending-app case reuses its existing synthetic staging fixture.
"""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, updates
from amplifier_web.app_updates import check as real_application_check
from amplifier_web.runtime_environment import ProtectedRuntimeSource
from amplifier_web.updates import active_release, foundation_home
from test_pending_app_updates import waiting  # noqa: F401
from test_updates import app, git, prepare, repo  # noqa: F401


SOURCE = 'https://example.invalid/repo'


def metadata_failure(monkeypatch, reason='unavailable'):
    """Leave app_updates.check real; inject only its external metadata result."""
    if reason == 'invalid_response':
        reader = AsyncMock(return_value='{"tag_name":null}')
    else:
        error = TimeoutError if reason == 'timeout' else RuntimeError
        reader = AsyncMock(side_effect=error('SECRET metadata endpoint'))
    monkeypatch.setattr(app_updates, 'github_read', reader)
    return reader


@pytest.fixture
async def component(app, repo, monkeypatch):
    manager, root = await prepare(app, repo)
    # prepare supplies the local cache, but its preselected inventory must not
    # satisfy the regression: check must rediscover and check the source itself.
    manager.inventory = []
    monkeypatch.setattr(app_updates, 'check', real_application_check)
    process = updates.process

    async def local_git(*args, **kwargs):
        assert args[0] == 'git', 'This fixture does not install dependencies'
        assert not any(str(arg).startswith(('https://', 'http://', 'ssh://'))
                       and arg != SOURCE for arg in args), 'Unexpected external source'
        args = tuple(str(repo[0]) if arg == SOURCE else arg for arg in args)
        return await process(*args, **kwargs)

    monkeypatch.setattr(updates, 'process', local_git)
    inventory = AsyncMock(wraps=manager.inventory_sources)
    stage_sources = AsyncMock(wraps=manager.stage_cache_sources)
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(manager, 'stage_cache_sources', stage_sources)
    return SimpleNamespace(service=app, manager=manager, root=root, repo=repo,
                           inventory=inventory, stage_sources=stage_sources)


def source_row(rows):
    return next(row for row in rows if row.get('path') == 'cache/repository')


def release_directories(manager):
    return set((manager.directory / 'releases').glob('*'))


def validation_boundary(component, monkeypatch, *, fail=False):
    """Inspect actual copied bytes, then stand in for worker qualification."""
    c = component

    async def validate(stage, release):
        assert stage.name == release
        assert git(stage / 'foundation/cache/repository', 'rev-parse', 'HEAD') == c.repo[2]
        assert (stage / 'foundation/cache/repository/bundle.md').read_text() == 'second'
        assert git(c.root, 'rev-parse', 'HEAD') == c.repo[1]
        assert not active_release(c.service.data_dir)
        # The real planner must not mistake this unknown bundle for safe data.
        assert json.loads((stage / 'plan.json').read_text())['mode'] == 'worker'
        if fail:
            # Use the diagnostic owner's public package-name allowlist.
            raise ProtectedRuntimeSource('amplifier-fixture-component', 'runtime-source-constraint')

    observer = AsyncMock(side_effect=validate)
    monkeypatch.setattr(c.manager, 'validate', observer)
    return observer


@pytest.mark.parametrize('reason', ['unavailable', 'invalid_response', 'timeout'])
async def test_failed_app_metadata_still_discovers_stages_and_activates_component(
        component, monkeypatch, reason):
    c = component
    reader = metadata_failure(monkeypatch, reason)
    notes = {'version': '99.0.0', 'title': 'Saved release notes',
             'changes': ['Keep this previously fetched history.'], 'notices': []}
    application_result = {'phase': 'restart-ack', 'attemptId': 'a' * 32,
                          'revision': 'b' * 40, 'version': '1.2.3', 'verifiedAt': 123}
    await c.manager.publish(application={'id': 'application', 'status': 'current',
                                        'latest': 'v99.0.0', 'releaseNotes': [notes]},
                            applicationResult=copy.deepcopy(application_result))
    before = release_directories(c.manager)
    await c.manager.check()

    # RED oracle on 48c006: the old application-status return prevents this call.
    c.inventory.assert_awaited_once_with(indexed=True)
    reader.assert_awaited_once()
    row = source_row(c.manager.inventory)
    assert row['status'] == 'update' and row['eligible']
    assert (row['current'], row['latest'], row['ref']) == (c.repo[1], c.repo[2], 'main')
    state = c.service.state['updates']
    failed_app = copy.deepcopy(state['application'])
    assert failed_app['status'] == 'check_failed'
    assert failed_app['checkStage'] == 'release_metadata'
    assert failed_app['checkReason'] == reason
    assert 'SECRET' not in json.dumps(failed_app)
    assert notes in failed_app['releaseNotes'] and failed_app['releaseNotesWarning']
    assert state['phase'] == 'available' and not state['error']
    assert not state['appAvailable']
    assert state['applicationResult'] == application_result
    assert state['currentStatus']['action'] == 'install'
    assert release_directories(c.manager) == before
    assert git(c.root, 'rev-parse', 'HEAD') == c.repo[1]

    validate = validation_boundary(c, monkeypatch)
    c.service.state['sessions'][0]['status'] = 'working'
    await c.manager.install()
    validate.assert_awaited_once()
    c.stage_sources.assert_awaited_once()
    assert state['phase'] == 'staged' and state['pendingRelease']
    assert state['currentStatus']['status'] == 'waiting'
    assert state['application'] == failed_app
    assert state['applicationResult'] == application_result
    assert next(row for row in state['items'] if row['id'] == 'application') == failed_app
    candidate = state['pendingRelease']
    assert not active_release(c.service.data_dir)

    c.service.state['sessions'][0]['status'] = 'idle'
    await c.manager.tick()
    assert state['phase'] == 'installed' and not state['pendingRelease']
    assert active_release(c.service.data_dir)['current'] == candidate
    assert git(foundation_home(c.service.data_dir) / 'cache/repository',
               'rev-parse', 'HEAD') == c.repo[2]
    assert git(c.root, 'rev-parse', 'HEAD') == c.repo[1]
    assert state['application'] == failed_app
    assert state['applicationResult'] == application_result
    assert next(row for row in state['items'] if row['id'] == 'application') == failed_app
    assert state['currentStatus']['actionRequired']
    assert state['currentStatus']['action'] == 'check'
    assert state['currentStatus']['component'] == 'Amplifier Unified'
    assert reader.await_count == validate.await_count == c.stage_sources.await_count == 1


async def test_current_app_still_discovers_components_with_real_release_check(component, monkeypatch):
    c = component
    # The installed-ahead path is also status=current. Resolve the tag against a
    # real local remote, without consulting the host's installed dependency graph.
    git(c.repo[0], 'tag', 'v0.0.0', c.repo[1])
    monkeypatch.setattr(app_updates, 'github_read',
                        AsyncMock(return_value='{"tag_name":"v0.0.0"}'))
    process = app_updates.process

    async def local_release(*args, **kwargs):
        assert args[:3] == ('git', 'ls-remote', app_updates.SOURCE)
        return await process(*(str(c.repo[0]) if arg == app_updates.SOURCE else arg
                               for arg in args), **kwargs)

    monkeypatch.setattr(app_updates, 'process', local_release)
    await c.manager.check()
    c.inventory.assert_awaited_once_with(indexed=True)
    state = c.service.state['updates']
    assert state['application']['status'] == 'current'
    assert state['application']['revision'] == c.repo[1]
    assert source_row(c.manager.inventory)['latest'] == c.repo[2]
    assert state['phase'] == 'available' and not state['error']
    c.stage_sources.assert_not_called()


async def test_component_failure_stays_actionable_and_unchanged_automatic_retry_does_not_copy(
        component, monkeypatch):
    c = component
    metadata_failure(monkeypatch)
    validate = validation_boundary(c, monkeypatch, fail=True)
    await c.manager.check()
    await c.manager.tick()  # Real automatic install, through source copy to validation.
    validate.assert_awaited_once()
    state = c.service.state['updates']
    assert state['phase'] == 'error' and state['error']
    assert state['application']['status'] == 'check_failed'
    assert state['currentStatus']['status'] == 'protected'
    assert state['currentStatus']['action'] == 'sources'
    assert state['currentStatus']['component'] == 'amplifier-fixture-component'
    failure = copy.deepcopy(c.manager.diagnostics.state['lastFailure'])
    retry = copy.deepcopy(c.manager.diagnostics.state['batch']['retry'])
    assert retry['mode'] == 'changed-inputs'
    candidates = release_directories(c.manager)
    assert len(candidates) == 1

    for _ in range(3):
        # A fresh check can report available again; it must not bypass the retry
        # witness and replay expensive preparation on the same source inputs.
        await c.manager.check(automatic=True)
        await c.manager.tick()
    assert release_directories(c.manager) == candidates
    assert c.manager.diagnostics.state['lastFailure'] == failure
    assert c.manager.diagnostics.state['batch']['retry'] == retry
    assert state['currentStatus']['actionRequired']
    assert state['currentStatus']['action'] == 'sources'
    validate.assert_awaited_once()
    c.stage_sources.assert_awaited_once()
    assert not active_release(c.service.data_dir)
    assert git(c.root, 'rev-parse', 'HEAD') == c.repo[1]


@pytest.mark.parametrize('boundary', ['pendingRelease', 'pendingRestart', 'pendingReplacement'])
async def test_pending_handoff_never_prepares_a_second_component_candidate(
        component, monkeypatch, boundary):
    c = component
    reader = metadata_failure(monkeypatch)
    pending = 'c' * 32 if boundary == 'pendingRelease' else {'revision': 'c' * 40}
    await c.manager.publish(**{boundary: pending})
    # A pending component may route to its existing activation owner; this test
    # proves routing/no new preparation, not the seeded receipt's qualification.
    activate = AsyncMock()
    monkeypatch.setattr(c.manager, 'activate', activate)
    before = release_directories(c.manager)
    await c.manager.check()
    await c.manager.install()
    c.inventory.assert_not_called()
    c.stage_sources.assert_not_called()
    reader.assert_not_called()
    assert release_directories(c.manager) == before
    assert c.service.state['updates'][boundary] == pending
    assert activate.await_count == int(boundary == 'pendingRelease')


async def test_unknown_component_preparation_survives_app_failure_and_blocks_replay(
        component, monkeypatch):
    c = component
    reader = metadata_failure(monkeypatch)
    c.manager.diagnostics.begin('ecosystem', 'd' * 32)
    c.manager.diagnostics.state['batch']['retry'] = {'mode': 'inspect', 'inputKey': 'unknown'}
    prior = copy.deepcopy(c.manager.diagnostics.state['batch'])
    before = release_directories(c.manager)
    await c.manager.check()
    reader.assert_awaited_once()
    assert c.service.state['updates']['application']['status'] == 'check_failed'
    await c.manager.install(automatic=True)
    with pytest.raises(ValueError, match='interrupted'):
        await c.manager.install()
    c.inventory.assert_not_called()
    c.stage_sources.assert_not_called()
    assert c.manager.diagnostics.state['batch'] == prior
    assert release_directories(c.manager) == before
    assert c.service.state['updates']['currentStatus']['action'] == 'diagnostics'


@pytest.mark.parametrize('reason', ['unavailable', 'invalid_response', 'timeout'])
async def test_prepared_app_failed_refresh_keeps_exact_receipt_and_never_checks_components(
        waiting, monkeypatch, reason):
    w = waiting
    monkeypatch.setattr(app_updates, 'check', real_application_check)
    reader = metadata_failure(monkeypatch, reason)
    before = copy.deepcopy(w.state['application'])
    sequence = copy.deepcopy(w.state['sequence'])
    installs = list(w.installs)
    # The real manager dispatches to the canonical app_updates.check_staged.
    # waiting's inventory guard fails if it ever reaches old-host components.
    await w.manager.check()
    assert w.state['pendingApp'] == w.original
    assert w.state['application'] == before
    assert w.state['sequence'] == sequence and w.state['phase'] == 'app-staged'
    assert w.state['applicationCheckWarning'] and not w.state.get('error')
    assert not w.state['applicationCheckPending']
    for _ in range(3):
        await w.manager.tick()  # Still busy: no repeated refresh or replacement.
    reader.assert_awaited_once()
    assert w.state['pendingApp'] == w.original
    assert w.installs == installs and not w.promotions and not w.restarts


async def test_cancelled_app_metadata_propagates_without_inventory_or_preparation(
        component, monkeypatch):
    c = component
    reader = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(app_updates, 'github_read', reader)
    before = release_directories(c.manager)
    with pytest.raises(asyncio.CancelledError):
        await c.manager.check()
    reader.assert_awaited_once()
    c.inventory.assert_not_called()
    c.stage_sources.assert_not_called()
    assert release_directories(c.manager) == before
    assert not active_release(c.service.data_dir)
    assert c.service.state['updates']['application']['status'] != 'current'


@pytest.mark.parametrize('policy,status,eligible', [
    ('exact', 'pinned', False),
    ('tag', 'pinned', False),
    ('ambiguous', 'pinned', False),
    ('custom-branch', 'update', True),
    ('missing-branch', 'check_failed', True),
])
async def test_app_failure_does_not_relax_component_reference_policy(
        component, monkeypatch, policy, status, eligible):
    c = component
    metadata_failure(monkeypatch)
    ref = c.repo[1] if policy == 'exact' else policy
    if policy in {'tag', 'ambiguous'}:
        git(c.repo[0], 'tag', ref, c.repo[2])
    if policy in {'ambiguous', 'custom-branch'}:
        git(c.repo[0], 'branch', ref, c.repo[2])
    (c.root / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'git_url': SOURCE, 'ref': ref, 'commit': c.repo[1]}))
    await c.manager.check()
    c.inventory.assert_awaited_once_with(indexed=True)
    row = source_row(c.manager.inventory)
    assert row['ref'] == ref and row['current'] == c.repo[1]
    assert row['status'] == status and row['eligible'] is eligible
    if status == 'update':
        assert row['latest'] == c.repo[2]
    else:
        assert not row.get('latest')
        await c.manager.install()
        c.stage_sources.assert_not_called()
        assert not release_directories(c.manager)
    assert git(c.root, 'rev-parse', 'HEAD') == c.repo[1]