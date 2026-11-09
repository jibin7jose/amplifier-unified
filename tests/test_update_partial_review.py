"""Review regressions for partial-source scheduling and interrupted preparation.

These are orchestration/unit tests, not installed-runtime qualification. They
reuse real Git copies and source guards from test_update_partial, including its
explicit synthetic qualified-baseline and collect-only seams. Cancellation is
injected at the dependency-preparation boundary; no installer or profile mount
is needed to prove that the real manager retains the unknown-operation fence.
"""

import asyncio
import copy
import json
import shutil
import time

import pytest

from amplifier_web import session_files, update_health, update_plan, updates
from amplifier_web import update_partial as partial
from amplifier_web.host import config as host_config
from amplifier_web.update_diagnostics import CommandTimeout, UpdateDiagnostics
from amplifier_web.update_health import pointer_witness
from amplifier_web.update_sequence import app_selection

# Pytest must see these fixtures explicitly; importing only a helper does not
# register fixtures from another test module.
from test_update_partial import git, preparation  # noqa: F401
from test_update_sequence import manager  # noqa: F401


@pytest.fixture
async def review(preparation, manager, monkeypatch, tmp_path):
    p = preparation
    assert manager.home == p.manager.home
    p.manager = manager
    shared = tmp_path / 'review-shared'
    monkeypatch.setattr(host_config, 'amplifier_home', lambda: shared)
    monkeypatch.setattr(session_files, 'amplifier_home', lambda: shared)
    # Restore the actual pointer digest; only working-baseline qualification is
    # synthetic. The source-selection and owned-checkout guards stay unmocked.
    monkeypatch.setattr(update_health, 'pointer_witness', pointer_witness)
    baseline = manager.directory / 'releases' / ('a' * 32)
    shutil.copytree(p.stage / 'foundation', baseline / 'foundation')
    witness = app_selection(manager.home)['witness']
    for row in p.rows:
        row['appSelection'] = witness
    manager.inventory = p.rows
    manager._storage_last_pass = time.time()
    manager._storage_policy_due = False
    manager._update_health_initial = False
    return p


@pytest.fixture
async def prior_partial(review, monkeypatch):
    """Produce the prior receipt, then model its qualified A-old/B-new/C-old graph."""
    p = review
    p.manager.diagnostics.begin('ecosystem', p.stage.name, tier='included')
    await partial.stage_sources(p.manager, p.stage, p.rows)
    p.prior = partial.read(p.stage)
    assert p.prior['results']['a']['status'] == 'failed'
    assert p.prior['results']['c']['status'] == 'dependency-held'
    assert p.prior['results']['b']['status'] == 'awaiting-adoption'
    policies = copy.deepcopy(p.policies)
    policies['fixture-b']['revision'] = p.new['b']
    (p.stage / 'runtime-sources.json').write_text(json.dumps(policies))
    (p.stage / 'runtime-installed.json').write_text(json.dumps([
        {'name': 'fixture-' + name, 'version': '2.0' if name == 'b' else '1.0'}
        for name in ('a', 'b', 'c')
    ]))
    # This pointer/baseline seam is NOT actual activation or qualification.
    p.pointer.write_text(json.dumps({'current': p.stage.name, 'previous': 'a' * 32}))
    monkeypatch.setattr(update_health, 'active_evidence',
                        lambda manager: {'generation': p.stage.name, 'verifiedAt': 1})
    p.current_rows = partial.adopted_inventory(p.rows, p.prior)
    for row in p.current_rows:
        row['status'] = 'not_checked'
        row.pop('availabilityFailure', None)
        row.pop('sourceUpdate', None)
    return p


def other_source(p, *, status='not_checked'):
    return {
        'id': 'd', 'kind': 'runtime dependency', 'package': 'fixture-d',
        'label': 'Source d', 'url': 'https://example.invalid/d', 'ref': 'main',
        'current': 'd' * 40, 'latest': 'e' * 40, 'eligible': True,
        'status': status, 'updateTier': 'other',
        'appSelection': app_selection(p.manager.home)['witness'],
    }


def checked_inventory(p, monkeypatch, *, include_other=True, unknown_a=False):
    """Only availability I/O and the inventory observation are synthetic."""
    rows = copy.deepcopy(p.current_rows)
    if include_other:
        rows.append(other_source(p))
    calls = []
    desired = {row['url']: (row['latest'], row['ref']) for row in rows}
    original = updates.process

    async def inventory(*, indexed=False):
        assert indexed
        return copy.deepcopy(rows)

    async def process(*args, **kwargs):
        if args[:2] == ('git', 'ls-remote'):
            url = args[2]
            calls.append(url)
            revision, ref = desired[url]
            if unknown_a and url == 'https://example.invalid/a':
                raise CommandTimeout(1)
            return revision + ' refs/heads/' + ref
        return await original(*args, **kwargs)

    monkeypatch.setattr(p.manager, 'inventory_sources', inventory)
    monkeypatch.setattr(updates, 'process', process)
    return calls


def next_candidate(p):
    stage = p.manager.directory / 'releases' / ('d' * 32)
    stage.mkdir()
    shutil.copytree(p.stage / 'foundation', stage / 'foundation')
    p.manager.diagnostics.begin('ecosystem', stage.name, tier='other')
    return stage


async def test_handled_fetch_then_cancelled_install_keeps_inspection_fence(review, monkeypatch):
    p = review
    m = p.manager
    releases = m.directory / 'releases'
    before = set(releases.iterdir())
    pointer_before = p.pointer.read_bytes()
    validations = []

    async def build(*args):
        return {'mode': 'worker', 'reason': 'review fixture', 'activation': 'per-worker'}

    async def cancelled_dependency_preparation():
        raise asyncio.CancelledError()

    async def validate(stage, release):
        validations.append(release)
        receipt = partial.read(stage)
        assert receipt['results']['a']['status'] == 'failed'
        assert receipt['results']['c']['status'] == 'dependency-held'
        assert receipt['results']['b']['status'] == 'awaiting-adoption'
        assert git(stage / 'foundation/cache/b', 'rev-parse', 'HEAD') == p.new['b']
        await m.diagnostics.run('ecosystem-prepare', cancelled_dependency_preparation)

    monkeypatch.setattr(update_plan, 'build', build)
    monkeypatch.setattr(m, 'validate', validate)
    with pytest.raises(asyncio.CancelledError):
        await m.install(automatic=True)

    created = set(releases.iterdir()) - before
    assert len(created) == 1
    candidate = created.pop()
    assert validations == [candidate.name]
    assert p.fetched == ['https://example.invalid/' + name for name in ('a', 'b', 'c')]
    diagnostics = m.diagnostics.state
    failure = copy.deepcopy(diagnostics['lastFailure'])
    assert failure['phase'] == 'ecosystem-fetch' and failure['status'] == 'failed'
    assert failure['exitCode'] == 128
    assert diagnostics['batch']['retry']['mode'] == 'inspect'
    fence = copy.deepcopy(diagnostics['componentPreparation'])
    assert fence['retry']['mode'] == 'inspect'
    assert fence['preparation']['candidate'] == candidate.name
    assert fence['preparation']['boundary'] == 'preparing'
    assert fence['preparation']['interruptedAt'] > 0
    assert fence['terminal']['status'] == 'interrupted'
    assert fence['terminal']['phase'] == 'ecosystem-stage'
    assert fence['terminal']['attemptId'] == failure['attemptId']
    assert not (candidate / 'validated.json').exists()
    assert not m.service.state['updates'].get('pendingRelease')
    assert p.pointer.read_bytes() == pointer_before

    # Reconstruct diagnostics after a JSON round trip of the published owner
    # state, not a hand-made retry record. This is not a host-restart test.
    # Neither fresh automatic checking nor changed desired inputs may create
    # another candidate while the preparation outcome is unknown.
    await m.publish()
    m.service.state['updates']['diagnostics'] = json.loads(json.dumps(m.diagnostics.state))
    m.diagnostics = UpdateDiagnostics(m)
    assert update_health.pending_preparation(m.diagnostics.state) == fence
    inventory_reads = []

    async def inventory(**kwargs):
        inventory_reads.append(kwargs)
        return copy.deepcopy(p.rows)

    monkeypatch.setattr(m, 'inventory_sources', inventory)
    m.service.state['settings']['updates'].update(autoCheck=True, autoInstall=True)
    await m.check(tier='included', fresh=True, automatic=True)
    m.service.state['updates'].update(lastCheck=0, lastAttempt=0)
    await m.tick()
    m.inventory = [dict(row, latest='e' * 40) for row in p.rows]
    await m.install(automatic=True)
    with pytest.raises(ValueError, match='not replayed'):
        await m.install()
    assert inventory_reads == []
    assert set(releases.iterdir()) == before | {candidate}
    assert validations == [candidate.name]
    assert len(p.fetched) == 3
    assert p.pointer.read_bytes() == pointer_before
    assert m.diagnostics.state['lastFailure'] == failure
    assert m.diagnostics.state['componentPreparation'] == fence


async def test_actual_check_passes_retained_included_tier_and_discovers_d(prior_partial, monkeypatch):
    p = prior_partial
    calls = checked_inventory(p, monkeypatch)
    before = set((p.manager.directory / 'releases').iterdir())
    await p.manager.check(fresh=False, automatic=True)

    state = p.manager.service.state['updates']
    by_id = {row['id']: row for row in p.manager.inventory}
    assert set(calls) == {'https://example.invalid/' + name for name in ('a', 'b', 'c', 'd')}
    for name, status in [('a', 'failed'), ('a-duplicate', 'failed'), ('c', 'dependency-held')]:
        assert by_id[name]['status'] == 'check_failed'
        assert by_id[name]['sourceUpdate'] == status
        assert by_id[name]['availabilityFailure'] is True
    assert by_id['b']['status'] == 'current'
    assert by_id['d']['status'] == 'update'
    assert state['sequence']['stage'] == 'other'
    assert state['sequence']['included']['available'] == 0
    assert state['sequence']['included']['issues'] == 2
    assert state['sequence']['other']['available'] == state['available'] == 1
    assert state['phase'] == 'available' and state['includedCanProceed'] is True
    assert not state.get('error')
    assert set((p.manager.directory / 'releases').iterdir()) == before


@pytest.mark.parametrize('tier', ['included', 'other'])
async def test_fresh_automatic_tier_continuation_does_not_mean_deliberate_retry(
        prior_partial, monkeypatch, tier):
    p = prior_partial
    m = p.manager
    for row in p.current_rows:
        row['updateTier'] = tier
    calls = checked_inventory(p, monkeypatch, include_other=False)
    m.service.state['settings']['updates'].update(autoCheck=False, autoInstall=False)
    m.service.state['updates'].update(
        phase='installed',
        sequence={'stage': 'application' if tier == 'included' else 'included',
                  'nextStage': tier, 'install': False},
    )
    requests = []
    actual_check = m.check

    async def check(**kwargs):
        requests.append(kwargs)
        return await actual_check(**kwargs)

    monkeypatch.setattr(m, 'check', check)
    before = set((m.directory / 'releases').iterdir())
    await m.tick()
    assert requests == [{'tier': tier, 'install': False, 'fresh': True, 'automatic': True}]
    assert set(calls) == {'https://example.invalid/' + name for name in ('a', 'b', 'c')}
    assert m.service.state['updates']['available'] == 0
    assert m.service.state['updates']['sequence']['stage'] == 'complete'
    assert all(row['status'] == 'check_failed' for row in m.inventory
               if row['url'].endswith(('/a', '/c')))
    assert set((m.directory / 'releases').iterdir()) == before

    # Freshness alone is not intent. A genuinely manual fresh check can expose
    # the same desired revisions for a new guarded installation.
    await actual_check(tier=tier, fresh=True, automatic=False)
    assert m.service.state['updates']['available'] == 2
    assert all(row['status'] == 'update' for row in m.inventory
               if row['url'].endswith(('/a', '/c')))


async def test_new_unknown_check_failure_is_not_authorized_by_prior_retention(prior_partial, monkeypatch):
    p = prior_partial
    calls = checked_inventory(p, monkeypatch, unknown_a=True)
    await p.manager.check(fresh=True, automatic=True)

    state = p.manager.service.state['updates']
    failed = [row for row in p.manager.inventory if row['url'].endswith('/a')]
    assert failed and all(row['status'] == 'check_failed' for row in failed)
    assert all(row['availabilityFailure'] is False for row in failed)
    assert all(not partial.availability(row) for row in failed)
    assert 'https://example.invalid/d' not in calls
    assert state['sequence']['stage'] == 'included'
    assert state['phase'] == 'error'
    assert state['includedCanProceed'] is False


async def test_unrelated_candidate_inherits_exact_prior_retention_from_full_inventory(
        prior_partial, monkeypatch):
    p = prior_partial
    m = p.manager
    stage = next_candidate(p)
    d = other_source(p, status='update')
    m.inventory = [d]  # Only this tier was checked; A/C are absent, not current.
    reads = []

    async def inventory(*, indexed=False):
        reads.append(indexed)
        return copy.deepcopy(p.current_rows + [d])

    monkeypatch.setattr(m, 'inventory_sources', inventory)
    fetched_before = list(p.fetched)
    pointer_before = p.pointer.read_bytes()
    plan = await partial.stage_sources(m, stage, [d])
    receipt = partial.read(stage)
    retained_ids = {'a', 'a-duplicate', 'runtime:a', 'c', 'runtime:c'}
    assert reads == [True]
    assert receipt['retained'] == p.prior['retained']
    assert receipt['baseline'] == p.stage.name
    assert receipt['selection'] == p.prior['selection']
    assert set(receipt['results']) == retained_ids | {'d'}
    for identity in retained_ids:
        assert receipt['results'][identity] == p.prior['results'][identity]
    assert receipt['results']['d'] == {
        'status': 'awaiting-adoption', 'revision': d['latest'], 'revisionKind': 'git',
    }
    for name in ('a', 'a-duplicate', 'b', 'c'):
        expected = p.new['b'] if name == 'b' else p.old[name[0]]
        assert git(stage / 'foundation/cache' / name, 'rev-parse', 'HEAD') == expected
    assert p.fetched == fetched_before
    assert p.pointer.read_bytes() == pointer_before
    await plan.recheck()
    # Receipt persistence, not promotion: this test does not validate or mount D.
    assert partial.read(stage) == receipt


@pytest.mark.parametrize('case', ['no-prior-receipt', 'different-selection', 'already-checked'])
async def test_inherit_unchecked_does_not_scan_without_matching_missing_retention(
        prior_partial, monkeypatch, case):
    p = prior_partial
    stage = next_candidate(p)
    d = other_source(p, status='update')
    p.manager.inventory = [d]
    if case == 'no-prior-receipt':
        (p.stage / partial.RECEIPT).unlink()
    elif case == 'different-selection':
        prior = copy.deepcopy(p.prior)
        prior['selection'] = ['0' * 64]
        (p.stage / partial.RECEIPT).write_text(json.dumps(prior))
    else:
        p.manager.inventory += copy.deepcopy(p.current_rows)

    async def inventory(**kwargs):
        pytest.fail('Full inventory is only needed for matching unchecked retained groups')

    monkeypatch.setattr(p.manager, 'inventory_sources', inventory)
    plan = partial.Preparation(p.manager, stage, p.manager.inventory)
    await plan.inherit_unchecked()
    assert plan.retained == plan.results == {}
    assert not (stage / partial.RECEIPT).exists()


@pytest.mark.parametrize('case', ['missing-source', 'duplicate-drift', 'dirty-copy'])
async def test_inherited_retention_rechecks_full_current_owned_source_identity(
        prior_partial, monkeypatch, case):
    p = prior_partial
    stage = next_candidate(p)
    d = other_source(p, status='update')
    p.manager.inventory = [d]
    rows = copy.deepcopy(p.current_rows)
    if case == 'missing-source':
        rows = [row for row in rows if not row['url'].endswith('/a')]
    elif case == 'duplicate-drift':
        next(row for row in rows if row['id'] == 'a-duplicate')['current'] = p.new['a']
    else:
        (stage / 'foundation/cache/a-duplicate/pyproject.toml').write_text('user edit')

    async def inventory(*, indexed=False):
        assert indexed
        return copy.deepcopy(rows + [d])

    monkeypatch.setattr(p.manager, 'inventory_sources', inventory)
    fetched_before = list(p.fetched)
    pointer_before = p.pointer.read_bytes()
    with pytest.raises(ValueError, match='identity|content changed'):
        await partial.stage_sources(p.manager, stage, [d])
    assert not (stage / partial.RECEIPT).exists()
    assert p.fetched == fetched_before
    assert p.pointer.read_bytes() == pointer_before
