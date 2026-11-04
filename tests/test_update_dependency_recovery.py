"""A qualified component generation also covers its actual dependencies."""
import copy
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from amplifier_web.attention import snapshot
from amplifier_web.update_diagnostics import UpdateDiagnostics
from amplifier_web.update_report import event


PACKAGE = 'amplifier-module-context-simple'
ROOT = 'amplifier-bundle-dot-runner'
GENERATION = 'a' * 32


def fixture(tmp_path):
    directory = tmp_path / 'updates'
    directory.mkdir()
    state = {'updates': {}, 'attentionRead': {}}
    manager = SimpleNamespace(directory=directory, service=SimpleNamespace(
        state=state, _save_changes=lambda **kwargs: None))
    return manager, UpdateDiagnostics(manager)


def failed_dependency(diagnostics):
    diagnostics.begin('ecosystem', 'b' * 32, tier='other', components=[ROOT])
    return diagnostics.record('ecosystem-source-preflight', 'failed',
        package=PACKAGE, reason='runtime-source-constraint', errorType='ProtectedRuntimeSource')


def qualified_generation(manager, diagnostics, components=None):
    diagnostics.begin('ecosystem', GENERATION, tier='other',
                      components=components or [ROOT, 'amplifier-bundle-context-managed'])
    started = diagnostics.state['batch']['startedAt']
    stage = manager.directory / 'releases' / GENERATION
    stage.mkdir(parents=True)
    records = {
        'plan.json': {'schema': 1, 'mode': 'worker'},
        'validated.json': {'generationSchema': 1, 'workerProtocol': 1,
                           'createdAt': started, 'updateTier': 'other'},
        'runtime-installed.json': [{'name': PACKAGE, 'version': '1.0.0'}],
    }
    for name, value in records.items():
        (stage / name).write_text(json.dumps(value))
    (manager.directory / 'active.json').write_text(json.dumps({'current': GENERATION, 'at': started}))
    return stage


def test_activation_recovers_dependency_failure_and_keeps_original_evidence(tmp_path):
    manager, diagnostics = fixture(tmp_path)
    failure = failed_dependency(diagnostics)
    qualified_generation(manager, diagnostics)
    # The old name-only matcher cannot relate this package to either bundle.
    assert diagnostics.state['batch']['recoveringFailures'] == {}
    diagnostics.clear_failure()
    assert diagnostics.state['activeFailures']  # Qualification alone is not adoption.
    activated = diagnostics.record('ecosystem-activation', 'succeeded')
    assert not diagnostics.state['activeFailures']
    assert snapshot(manager.service.state)['badgeUnread'] == 0
    receipt = diagnostics.state['events'][-1]
    assert receipt['phase'] == 'ecosystem-reconcile'
    assert receipt['revision'] == GENERATION and receipt['attemptId'] == activated['attemptId']
    assert receipt['recoveries'] == [{'firstFailure': failure, 'latestFailure': failure}]
    assert json.loads(diagnostics.path.read_text().splitlines()[-1]) == receipt
    clean = event({**receipt, 'secret': 'private', 'recoveries': [
        {'firstFailure': {**failure, 'secret': 'private'}, 'latestFailure': failure}]})
    assert clean['recoveries'][0]['latestFailure']['id'] == failure['id']
    assert 'private' not in json.dumps(clean)


def test_reload_reconciles_predecessor_activation_without_reinstall_or_dismissal(tmp_path):
    manager, diagnostics = fixture(tmp_path)
    failure = failed_dependency(diagnostics)
    stage = qualified_generation(manager, diagnostics)
    # Simulate the predecessor, which completed activation but did not reconcile.
    (stage / 'plan.json').write_text(json.dumps({'mode': 'data'}))
    activation = diagnostics.record('ecosystem-activation', 'succeeded')
    diagnostics.state['completedBatches'][-1].pop('revision', None)
    (stage / 'plan.json').write_text(json.dumps({'mode': 'worker'}))
    before = {path: path.read_bytes() for path in stage.iterdir()}
    reloaded = UpdateDiagnostics(manager)
    assert not reloaded.state['activeFailures']
    assert reloaded.state['lastFailure'] == failure  # Historical failure is not rewritten.
    receipt = reloaded.state['events'][-1]
    assert receipt['attemptId'] == activation['attemptId']
    assert not reloaded.state.get('failureDispositions')  # No user acknowledgement invented.
    assert {path: path.read_bytes() for path in stage.iterdir()} == before
    UpdateDiagnostics(manager)
    assert reloaded.state['events'][-1] == receipt  # Idempotent reload.


def test_full_large_batch_scope_is_retained_for_dependency_recovery(tmp_path):
    manager, diagnostics = fixture(tmp_path)
    roots = [f'amplifier-bundle-example-{i}' for i in range(7)] + [ROOT]
    diagnostics.begin('ecosystem', tier='other', components=roots)
    diagnostics.record('ecosystem-source-preflight', 'failed', package=PACKAGE,
                       reason='runtime-source-constraint')
    qualified_generation(manager, diagnostics, components=roots)
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert diagnostics.state['completedBatches'][-1]['components'] == sorted(roots)
    assert not diagnostics.state['activeFailures']


def test_large_batches_with_different_final_roots_keep_distinct_failures(tmp_path):
    _, diagnostics = fixture(tmp_path)
    common = [f'component-{i}' for i in range(5)]
    for extra in ['last-a', 'last-b']:
        diagnostics.begin('ecosystem', tier='other', components=common + [extra])
        diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    assert len(diagnostics.state['activeFailures']) == 2


def test_saved_real_activation_reconciles_transitive_failure(tmp_path):
    # Sanitized captured identities and receipt fields, not rewritten live state.
    saved = json.loads((Path(__file__).parent / 'fixtures' / 'update-recovery.json').read_text())
    manager, _ = fixture(tmp_path)
    manager.service.state['updates']['diagnostics'] = saved['diagnostics']
    stage = manager.directory / 'releases' / saved['active']['current']
    stage.mkdir(parents=True)
    (manager.directory / 'active.json').write_text(json.dumps(saved['active']))
    for name, value in saved['receipts'].items():
        (stage / name).write_text(json.dumps(value))
    files_before = {path: path.read_bytes() for path in stage.iterdir()}
    original = next(iter(saved['diagnostics']['activeFailures'].values()))['latest']
    diagnostics = UpdateDiagnostics(manager)
    assert not diagnostics.state['activeFailures']
    recovered = diagnostics.state['events'][-1]
    assert recovered['revision'] == saved['active']['current']
    assert recovered['recoveries'][0]['latestFailure'] == original
    assert diagnostics.state['lastFailure'] is None
    assert files_before == {path: path.read_bytes() for path in stage.iterdir()}
    assert snapshot(manager.service.state)['badgeUnread'] == 0


@pytest.mark.parametrize('case', [
    'unrelated-roots', 'unrelated-package', 'different-tier', 'truncated-scope',
    'unknown-scope', 'later-occurrence', 'data-reuse', 'missing-validation',
    'invalid-validation', 'missing-graph', 'malformed-graph', 'wrong-generation',
    'missing-activation', 'pointer-before-validation', 'pointer-after-activation',
    'legacy-without-activation-event', 'legacy-unrelated-event', 'unrelated-failure',
])
def test_recovery_requires_exact_qualified_activation_coverage(tmp_path, case):
    manager, diagnostics = fixture(tmp_path)
    failed_dependency(diagnostics)
    stage = qualified_generation(manager, diagnostics)
    # Hold automatic settlement while constructing a legacy saved-state fixture.
    (stage / 'plan.json').write_text(json.dumps({'mode': 'data'}))
    activated = diagnostics.record('ecosystem-activation', 'succeeded')
    (stage / 'plan.json').write_text(json.dumps({'mode': 'worker'}))
    batch = diagnostics.state['completedBatches'][-1]
    group = next(iter(diagnostics.state['activeFailures'].values()))
    if case == 'unrelated-roots': batch['components'] = ['another']; batch['componentCount'] = 1
    elif case == 'unrelated-package': (stage / 'runtime-installed.json').write_text('[]')
    elif case == 'different-tier': batch['tier'] = 'included'
    elif case == 'truncated-scope': group['componentCount'] = 6
    elif case == 'unknown-scope': group['components'] = []; group['componentCount'] = 0
    elif case == 'later-occurrence': group['latest'] = {**group['latest'], 'id': 'f' * 32, 'at': activated['at'] + 1}
    elif case == 'data-reuse': (stage / 'plan.json').write_text('{"mode":"data"}')
    elif case == 'missing-validation': (stage / 'validated.json').unlink()
    elif case == 'invalid-validation': (stage / 'validated.json').write_text('{}')
    elif case == 'missing-graph': (stage / 'runtime-installed.json').unlink()
    elif case == 'malformed-graph': (stage / 'runtime-installed.json').write_text('[null]')
    elif case == 'wrong-generation': batch['revision'] = 'c' * 32
    elif case == 'missing-activation': diagnostics.state['completedBatches'] = []
    elif case == 'pointer-before-validation': (manager.directory / 'active.json').write_text(json.dumps({'current': GENERATION, 'at': batch['startedAt'] - 1}))
    elif case == 'pointer-after-activation': (manager.directory / 'active.json').write_text(json.dumps({'current': GENERATION, 'at': activated['at'] + 1}))
    elif case == 'legacy-without-activation-event': batch.pop('revision', None); diagnostics.state['events'] = []
    elif case == 'legacy-unrelated-event': batch.pop('revision', None); activated['attemptId'] = 'c' * 32
    elif case == 'unrelated-failure': group['latest']['reason'] = 'disk-full'
    before = copy.deepcopy(diagnostics.state)
    reloaded = UpdateDiagnostics(manager)
    assert reloaded.state == before
