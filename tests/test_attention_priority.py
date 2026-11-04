"""Information is not work; observing an update failure is not recovery."""
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.attention import snapshot
from amplifier_web.update_diagnostics import UpdateDiagnostics


def fixture(tmp_path, updates=None):
    state = {'attentionRead': {}, 'updates': updates or {}, 'feedback': {'requests': []}}
    service = SimpleNamespace(state=state, _save_changes=lambda **kwargs: None)
    diagnostics = UpdateDiagnostics(SimpleNamespace(service=service, directory=tmp_path))
    return state, diagnostics


def fail(diagnostics, *, component='amplifier-module-loop-live', kind='ecosystem'):
    diagnostics.begin(kind, 'c' * 40, components=[component], application_extras=[] if kind == 'application' else None)
    return diagnostics.record(kind + '-validation', 'failed', component=component,
                              reason='runtime-resolution-mismatch', errorType='ValueError',
                              expectedRevision='a' * 40, observedRevision='b' * 40)


def active(state):
    return [item for item in snapshot(state)['items'] if item.get('actionRequired')]


def read_all(state):
    state['attentionRead'].update({i['id']: i['fingerprint'] for i in snapshot(state)['items']})


def test_news_and_success_receipts_are_quiet_but_retained(tmp_path):
    state, _ = fixture(tmp_path)
    state['updates']['application'] = {'releaseNotes': [{'version': '1.0.0', 'notices': [
        {'id': str(i), 'title': f'News {i}', 'detail': 'Optional guidance.',
         'action': 'No action is required for ordinary browser use.'} for i in range(53)]}]}
    state['feedback']['requests'] = [{'requestId': f'sent-{i}', 'status': 'submitted',
                                     'title': 'Reported', 'message': 'Feedback sent.'} for i in range(3)]
    before = copy.deepcopy(state)
    result = snapshot(state)
    assert len(result['items']) == result['unread'] == 56  # Seen state is still truthful.
    assert all(i['informational'] for i in result['items'])
    assert result['badgeUnread'] == result['settingsUnread'] == 0
    assert result['sections']['maintenance'] == result['sections']['feedback'] == 0
    assert state == before
    state['feedback']['requests'][0]['status'] = 'failed'
    assert snapshot(state)['badgeUnread'] == 1  # Not blanket badge suppression.


def test_read_repeat_distinct_recovery_and_next_failure(tmp_path):
    state, diagnostics = fixture(tmp_path)
    original = fail(diagnostics)
    first = active(state)[0]
    read_all(state)
    assert active(state)[0]['read'] is True
    assert snapshot(state)['badgeUnread'] == snapshot(state)['settingsUnread'] == 1
    repeated = fail(diagnostics)
    group = active(state)[0]
    assert group['fingerprint'] == first['fingerprint']
    assert group['read'] and group['occurrences'] == 2
    assert len(active(state)) == 1
    assert original in diagnostics.state['events'] and repeated in diagnostics.state['events']
    distinct = fail(diagnostics, component='amplifier-module-hooks-approval')
    assert len(active(state)) == 2
    assert not active(state)[0]['read']
    assert active(state)[0]['fingerprint'] != first['fingerprint']
    fail(diagnostics)  # A -> B -> A: B stays first; A retains its original read identity.
    assert active(state)[1]['fingerprint'] == first['fingerprint']
    assert active(state)[1]['occurrences'] == 3 and active(state)[1]['read']
    read_all(state)
    # Checking, preparation and clearing a transient error are NOT recovery.
    state['updates'].update(phase='checking', error=None)
    diagnostics.begin('ecosystem', components=['amplifier-module-loop-live','amplifier-module-hooks-approval'])
    diagnostics.clear_failure()
    diagnostics.record('ecosystem-stage', 'succeeded')
    assert len(active(state)) == 2
    assert snapshot(state)['badgeUnread'] == 2
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert active(state) == []
    assert snapshot(state)['badgeUnread'] == 0
    assert distinct in diagnostics.state['events']
    fail(diagnostics, component='amplifier-module-hooks-approval')
    assert len(active(state)) == 1 and not active(state)[0]['read']


async def test_source_binding_reasons_stay_distinct_exportable_and_unresolved_after_preparation(tmp_path):
    import json
    from amplifier_web.runtime_environment import ProtectedRuntimeSource
    from amplifier_web.runtime_source_alignment import verify_checked_selection
    from amplifier_web.update_diagnostics import failure_guidance
    from amplifier_web.update_report import event

    state, diagnostics = fixture(tmp_path)
    package = 'amplifier-module-hooks-approval'
    private_source = 'https://example.invalid/private-source'
    checked = {package: {'url': private_source, 'ref': 'main', 'revision': 'a' * 40}}

    async def source_failure(status):
        diagnostics.begin('ecosystem', 'c' * 40, components=[package])
        async def preflight():
            verify_checked_selection(checked, {
                'batchPaths': {package: '/private/source'},
                'packages': {package: {'status': status, 'url': private_source}},
            })
        with pytest.raises(ProtectedRuntimeSource):
            await diagnostics.run('ecosystem-source-preflight', preflight)
        return diagnostics.state['lastFailure']

    first = await source_failure('unbound')
    first_identity = active(state)[0]['fingerprint']
    read_all(state)
    conflict = await source_failure('conflicting')
    repeated = await source_failure('unbound')
    groups = {item['firstFailure']['reason']: item for item in active(state)}
    assert set(groups) == {'runtime-source-unbound', 'runtime-source-conflict'}
    unbound = groups['runtime-source-unbound']
    assert unbound['fingerprint'] == first_identity and unbound['read']
    assert unbound['firstFailure'] == first and unbound['latestFailure'] == repeated
    assert unbound['occurrences'] == 2
    assert groups['runtime-source-conflict']['occurrences'] == 1
    assert not groups['runtime-source-conflict']['read']
    assert groups['runtime-source-conflict']['fingerprint'] != first_identity
    for original in (first, conflict, repeated):
        exported = event(original)
        assert exported['reason'] == original['reason'] and exported['package'] == package
        assert exported['errorType'] == 'ProtectedRuntimeSource'
        assert 'does not establish local edits' in failure_guidance(exported)
        assert 'private' not in json.dumps(exported)
    assert failure_guidance(first) != failure_guidance(conflict)
    retained = [json.loads(line) for line in diagnostics.path.read_text().splitlines()]
    assert all(original in retained for original in (first, conflict, repeated))
    assert 'private' not in json.dumps(retained)

    read_all(state)
    before = copy.deepcopy(diagnostics.state['activeFailures'])
    diagnostics.begin('ecosystem', 'c' * 40, components=[package])
    diagnostics.clear_failure()
    for phase in ('ecosystem-prepare', 'ecosystem-validation', 'ecosystem-stage'):
        diagnostics.record(phase, 'succeeded')
        assert diagnostics.state['activeFailures'] == before
        assert len(active(state)) == snapshot(state)['badgeUnread'] == 2


@pytest.mark.parametrize('phase', ['restart-ack', 'restart-reconcile', 'restart-superseded'])
def test_application_success_does_not_resolve_ecosystem_failure(tmp_path, phase):
    state, diagnostics = fixture(tmp_path)
    fail(diagnostics)
    fail(diagnostics, kind='application')
    assert len(active(state)) == 2
    diagnostics.begin('application', 'c' * 40, application_extras=[])
    diagnostics.clear_failure()
    diagnostics.record(phase, 'succeeded')
    assert len(active(state)) == 1
    assert active(state)[0]['updateKind'] == 'ecosystem'
    assert 'Current components remain active' in active(state)[0]['detail']


def test_legacy_previously_read_failure_is_migrated_only_with_active_evidence(tmp_path):
    failure = {'id': 'old', 'kind': 'ecosystem', 'attemptId': 'old-attempt',
               'phase': 'ecosystem-validation', 'status': 'failed', 'reason': 'runtime-resolution-mismatch'}
    updates = {'phase': 'error', 'error': 'Component preparation failed.',
               'diagnostics': {'events': [failure], 'lastFailure': failure}}
    state, _ = fixture(tmp_path, updates)
    read_all(state)
    assert len(active(state)) == 2 and snapshot(state)['badgeUnread'] == 2
    # A healthy old installation may retain lastFailure solely as history.
    app_failure = {**failure, 'kind': 'application', 'phase': 'restart-request'}
    healthy, _ = fixture(tmp_path, {'phase': 'interrupted',
        'reconciliation': {'attemptId': 'old-attempt', 'verifiedAt': 10},
        'diagnostics': {'events': [app_failure], 'lastFailure': app_failure}})
    assert active(healthy) == []
    # An error without a structured receipt still needs attention.
    plain, _ = fixture(tmp_path, {'phase': 'error', 'error': 'Check failed.'})
    read_all(plain)
    assert snapshot(plain)['badgeUnread'] == 1


def test_preference_and_projection_cost_do_not_turn_history_into_badge_debt(tmp_path):
    state, diagnostics = fixture(tmp_path)
    state['sessions'] = [{'id': 'chat', 'title': 'Synthetic', 'completion': {'id': 'one'}}]
    fail(diagnostics)
    for enabled, expected in [(False, 1), (True, 2)]:
        state['notificationSettings'] = {'completionBadge': enabled}
        assert snapshot(state)['badgeUnread'] == expected
    assert snapshot(state)['sections']['chats'] == 1


def test_other_tier_and_smart_tool_skip_are_not_component_recovery(tmp_path):
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('ecosystem', tier='included', components=['component-a'])
    diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    diagnostics.begin('smart-tools')
    diagnostics.record('smart-tools-complete', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('ecosystem', tier='other', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('ecosystem', tier='included', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert active(state) == []


def test_new_check_error_is_not_hidden_by_a_reviewed_component_failure(tmp_path):
    state, diagnostics = fixture(tmp_path)
    fail(diagnostics)
    read_all(state)
    state['updates']['error'] = 'The application release could not be checked.'
    items = active(state)
    assert len(items) == 2 and items[0]['id'] == 'updates:error' and not items[0]['read']
    assert items[1]['read']


def test_repeat_keeps_original_evidence_beyond_the_recent_event_window(tmp_path):
    import json
    state, diagnostics = fixture(tmp_path)
    first = fail(diagnostics)
    read_all(state)
    for _ in range(55):
        diagnostics.record('ecosystem-check', 'succeeded')
    fail(diagnostics)
    assert active(state)[0]['firstFailure'] == first
    assert active(state)[0]['occurrences'] == 2 and active(state)[0]['read']
    assert first not in diagnostics.state['events']
    originals = [json.loads(line) for line in diagnostics.path.read_text().splitlines()]
    assert originals[0] == first


def test_matching_scope_and_order_are_required_to_settle(tmp_path):
    state, diagnostics = fixture(tmp_path)
    fail(diagnostics, component='amplifier-module-a')
    diagnostics.begin('ecosystem', components=['amplifier-module-b'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('ecosystem', components=['amplifier-module-a'])
    diagnostics.record('ecosystem-validation', 'failed', component='amplifier-module-a',
                       reason='runtime-resolution-mismatch', errorType='ValueError',
                       expectedRevision='a'*40, observedRevision='b'*40)
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1  # The completion cannot clear a newer occurrence.
    diagnostics.begin('ecosystem', components=['amplifier-module-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert active(state) == []


def test_same_cause_in_different_tiers_recovers_independently(tmp_path):
    state, diagnostics = fixture(tmp_path)
    for tier in ('included', 'other'):
        diagnostics.begin('ecosystem', tier=tier, components=['component-a'])
        diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    assert len(active(state)) == 2
    diagnostics.begin('ecosystem', tier='included', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1
    assert next(iter(diagnostics.state['activeFailures'].values()))['tier'] == 'other'


def test_unqualified_failures_in_different_scopes_do_not_merge(tmp_path):
    state, diagnostics = fixture(tmp_path)
    for scope in ['component-a', 'component-b']:
        diagnostics.begin('ecosystem', components=[scope])
        diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    assert len(active(state)) == 2
    diagnostics.begin('ecosystem', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1
    assert next(iter(diagnostics.state['activeFailures'].values()))['components'] == ['component-b']


def test_original_public_cause_survives_a_metadata_only_collector(tmp_path):
    import json
    state, diagnostics = fixture(tmp_path)
    diagnostics.manager.service.diagnostics = SimpleNamespace(record=lambda *args: None)
    diagnostics.begin('ecosystem', components=['component-a'])
    event = diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError',
                               failureLocation='amplifier_web.updates:validate:42')
    for _ in range(55):
        diagnostics.record('ecosystem-check', 'succeeded')
    assert event not in diagnostics.state['events']
    assert json.loads(diagnostics.path.read_text().splitlines()[0]) == event


@pytest.mark.parametrize('components', [[], [f'component-{i}' for i in range(6)]])
def test_unknown_or_truncated_scope_cannot_claim_recovery(tmp_path, components):
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('ecosystem', components=components)
    # Legacy receipts retained only the first five component names.
    diagnostics.state['batch']['components'] = components[:5]
    diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    diagnostics.begin('ecosystem', components=components)
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1


def test_wrong_application_target_cannot_claim_recovery(tmp_path):
    state, diagnostics = fixture(tmp_path)
    fail(diagnostics, kind='application')
    diagnostics.begin('application', 'd' * 40, application_extras=[])
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('application', 'c' * 40, application_extras=[])
    diagnostics.record('restart-ack', 'succeeded')
    assert active(state) == []


def test_migration_keeps_unrelated_current_error_and_ecosystem_failure(tmp_path):
    failure = {'id': 'old', 'kind': 'ecosystem', 'attemptId': 'old-attempt',
               'phase': 'ecosystem-validation', 'status': 'failed'}
    state, _ = fixture(tmp_path, {'phase': 'error', 'error': 'Application check failed.',
        'reconciliation': {'attemptId': 'old-attempt', 'verifiedAt': 10},
        'diagnostics': {'events': [failure], 'lastFailure': failure}})
    assert len(active(state)) == 2
    assert active(state)[0]['id'] == 'updates:error'


def test_distinct_code_location_and_bounded_unresolved_overflow(tmp_path):
    from amplifier_web.update_diagnostics import MAX_ACTIVE_FAILURES
    state, diagnostics = fixture(tmp_path)
    for i in range(MAX_ACTIVE_FAILURES + 4):
        diagnostics.begin('ecosystem', components=['component-a'])
        diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError',
                           failureLocation=f'amplifier_web.updates:validate:{i+1}')
    assert len(diagnostics.state['activeFailures']) == MAX_ACTIVE_FAILURES
    assert len(active(state)) == MAX_ACTIVE_FAILURES + 1
    assert diagnostics.state['failureOverflow']['occurrences'] == 4
    read_all(state)
    assert snapshot(state)['badgeUnread'] == MAX_ACTIVE_FAILURES + 1
    diagnostics.begin('ecosystem', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1  # Unknown overflow is not silently evicted or resolved.
    assert active(state)[0]['id'] == 'updates:failure-overflow'

@pytest.mark.parametrize('unknown', [None, 'tui', ['tui', 'tui'], ['tui', '/private/secret'], ['unknown']])
def test_unknown_application_feature_scope_never_matches_known_base(tmp_path, unknown):
    import json
    from amplifier_web.update_report import event as public_event
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('application', 'a'*40, application_extras=unknown)
    event = diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    assert 'applicationExtras' not in event
    diagnostics.begin('application', 'a'*40, application_extras=[])
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1
    assert 'applicationExtras' not in public_event({**event, 'applicationExtras': unknown})
    assert '/private/secret' not in json.dumps(diagnostics.state)


def test_legacy_updater_missing_feature_helper_keeps_absent_scope_unknown(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    from amplifier_web.update_diagnostics import sanitized_application_extras
    from amplifier_web.update_report import event as public_event

    # The systemd journey runs a historical app_updates module alongside the
    # current diagnostics owner. That module has no optional-feature validator.
    legacy = ModuleType('amplifier_web.app_updates')
    monkeypatch.setitem(sys.modules, legacy.__name__, legacy)
    assert not hasattr(legacy, 'validated_extras')
    assert sanitized_application_extras(None) is None
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('application', 'a' * 40)
    failure = diagnostics.record('service-restart', 'failed', errorType='RuntimeError')
    assert 'applicationExtras' not in failure
    assert 'applicationExtras' not in public_event(failure)
    diagnostics.begin('application', 'a' * 40)
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1  # Unknown never becomes a known base install.


def test_application_groups_keep_revision_and_exact_feature_scope(tmp_path):
    state, diagnostics = fixture(tmp_path)
    for revision, extras in [('a'*40, []), ('a'*40, ['tui']), ('b'*40, ['tui']),
                             ('a'*40, ['native-desktop', 'tui'])]:
        diagnostics.begin('application', revision, application_extras=extras)
        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    assert len(active(state)) == 4
    read_all(state)
    diagnostics.begin('application', 'a'*40, application_extras=['tui', 'native-desktop'])
    repeat = diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    assert repeat['applicationExtras'] == ['native-desktop', 'tui']
    assert len(active(state)) == 4 and active(state)[0]['occurrences'] == 2 and active(state)[0]['read']
    diagnostics.begin('application', 'a'*40, application_extras=['tui'])
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 3


def test_verified_browser_launch_and_completed_restart_recover_only_prior_browser_failures(tmp_path):
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('application', 'a'*40, application_extras=['managed-browser'])
    old = diagnostics.record('managed-browser-install', 'failed', errorType='CommandFailure')
    unrelated = diagnostics.record('candidate-install', 'failed', errorType='CommandFailure')
    diagnostics.begin('application', 'b'*40, application_extras=['decisions-jev','managed-browser'])
    assert diagnostics.state['batch']['recoveringFailures'] == {}
    diagnostics.browser_qualified()
    assert len(active(state)) == 2, 'Qualification is not activation'
    attempt = diagnostics.state['attemptId']
    # Activation re-enters this exact candidate; retain its original snapshot.
    diagnostics.begin('application', 'b'*40, attempt, application_extras=['decisions-jev','managed-browser'])
    diagnostics.record('restart-ack', 'succeeded')
    failures = list(diagnostics.state['activeFailures'].values())
    assert len(failures) == 1 and failures[0]['latest']['id'] == unrelated['id']
    assert old['id'] in diagnostics.path.read_text(), 'Keep the original failure history'


@pytest.mark.parametrize('scope', [None, [], ['managed-browser']])
def test_restart_without_verified_browser_launch_does_not_clear_old_revision(tmp_path, scope):
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('application', 'a'*40, application_extras=scope)
    diagnostics.record('managed-browser-install', 'failed', errorType='CommandFailure')
    diagnostics.begin('application', 'b'*40, application_extras=['managed-browser'])
    if scope != ['managed-browser']:
        diagnostics.browser_qualified()  # Missing/unknown old scope is never inferred.
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1


def test_browser_recovery_never_adopts_a_later_failure_or_another_attempt(tmp_path):
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('application', 'a'*40, application_extras=['managed-browser'])
    diagnostics.record('managed-browser-probe', 'failed', errorType='CommandFailure')
    diagnostics.begin('application', 'b'*40, application_extras=['managed-browser'])
    diagnostics.browser_qualified()
    # A late occurrence for the old cause is outside this attempt's evidence.
    old_group = next(iter(diagnostics.state['activeFailures'].values()))
    old_group['latest'] = {**old_group['latest'], 'id':'late-occurrence'}
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('application', 'c'*40, application_extras=['managed-browser'])
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1, 'The next attempt needs its own blank launch'


@pytest.mark.parametrize('phase', ['restart-reconcile','restart-superseded'])
def test_browser_qualification_of_superseded_candidate_is_not_current_browser_proof(tmp_path,phase):
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('application', 'a'*40, application_extras=['managed-browser'])
    diagnostics.record('managed-browser-install','failed',errorType='CommandFailure')
    diagnostics.begin('application', 'b'*40, application_extras=['managed-browser'])
    diagnostics.browser_qualified()
    diagnostics.record(phase,'succeeded')
    assert len(active(state)) == 1


async def test_real_attention_read_and_restart_preserve_unresolved_failure(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        app.update_manager = UpdateManager(app)
        fail(app.update_manager.diagnostics)
        item = active(app.state)[0]
        await app.dispatch('attention.read', {'ids': [item['id']],
                           'fingerprints': {item['id']: item['fingerprint']}})
        assert active(app.state)[0]['read']
        assert app.get_state()['attention']['badgeUnread'] == 1
        app._save_changes(globals={'updates', 'attentionRead'})
    finally:
        await app.close()
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        assert active(app.state)[0]['read']
        assert app.get_state()['attention']['badgeUnread'] == 1
    finally:
        await app.close()


@pytest.mark.parametrize('overflow', [False, True])
def test_explicit_disposition_closes_only_reviewed_uncertainty(tmp_path, overflow):
    state, diagnostics = fixture(tmp_path)
    if overflow:
        for i in range(33):
            fail(diagnostics, component=f'amplifier-component-{i}')
        diagnostics.begin('ecosystem', components=[f'amplifier-component-{i}' for i in range(33)])
        diagnostics.record('ecosystem-activation', 'succeeded')
    else:
        diagnostics.begin('application', 'a'*40)
        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
        diagnostics.begin('application', 'a'*40, application_extras=[])
        diagnostics.record('restart-ack', 'succeeded')
    item = active(state)[0]
    read_all(state)
    assert active(state)[0]['read'] and snapshot(state)['badgeUnread'] == 1
    before = copy.deepcopy(diagnostics.state['events'])
    receipt = diagnostics.dispose(item['id'], item['dispositionOccurrence'])
    assert receipt['status'] == 'acknowledged-uncertainty'
    assert receipt['recoveryVerified'] is False
    assert not active(state)
    assert all(event in diagnostics.state['events'] for event in before)
    assert diagnostics.state['failureDispositions'][-1] == receipt
    assert 'acknowledged-uncertainty' in diagnostics.path.read_text()
    # Recreated occurrences cannot inherit closure or read state.
    if overflow:
        for i in range(33):
            fail(diagnostics, component=f'amplifier-component-{i}')
    else:
        diagnostics.begin('application', 'a'*40)
        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    newer = next(i for i in active(state) if i.get('dispositionOccurrence'))
    assert newer['dispositionOccurrence'] != item['dispositionOccurrence']
    with pytest.raises(ValueError, match='changed'):
        diagnostics.dispose(newer['id'], item['dispositionOccurrence'])


@pytest.mark.parametrize('overflow', [False, True])
def test_disposition_rejects_repeat_during_confirmation_and_keeps_other_failures(tmp_path, overflow):
    state, diagnostics = fixture(tmp_path)
    if overflow:
        for i in range(33):
            fail(diagnostics, component=f'amplifier-component-{i}')
    else:
        diagnostics.begin('application', 'a'*40)
        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    item = next(i for i in active(state) if i.get('dispositionOccurrence'))
    if overflow:
        fail(diagnostics, component='amplifier-new-overflow')
    else:
        diagnostics.begin('application', 'a'*40)
        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    before = copy.deepcopy(diagnostics.state)
    with pytest.raises(ValueError, match='changed'):
        diagnostics.dispose(item['id'], item['dispositionOccurrence'])
    assert diagnostics.state == before
    current = next(i for i in active(state) if i['id'] == item['id'])
    fail(diagnostics, component='amplifier-concurrent')
    if overflow:  # This also changed overflow's reviewed occurrence.
        current = next(i for i in active(state) if i['id'] == item['id'])
    others = [i['id'] for i in active(state) if i['id'] != item['id']]
    diagnostics.dispose(current['id'], current['dispositionOccurrence'])
    assert [i['id'] for i in active(state)] == others


async def test_disposition_action_is_user_only_exact_deduplicated_and_persisted(tmp_path):
    from amplifier_web.service import AppService, AppError
    from amplifier_web.updates import UpdateManager
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        app.update_manager = UpdateManager(app)
        diagnostics = app.update_manager.diagnostics
        diagnostics.begin('application', 'a'*40)
        failure = diagnostics.record('candidate-install', 'failed', errorType='ValueError')
        item = active(app.state)[0]
        args = {'id': item['id'], 'expectedOccurrence': item['dispositionOccurrence'],
                'disposition': 'acknowledged-uncertainty'}
        with pytest.raises(AppError):
            await app.dispatch('updates.failureDisposition', args, origin='agent')
        result = await app.dispatch('updates.failureDisposition', args, origin='ui', command_id='dispose-one')
        assert result['result']['recoveryVerified'] is False
        assert (await app.dispatch('updates.failureDisposition', args, origin='ui',
                                  command_id='dispose-one'))['duplicate']
        assert not active(app.state)
        assert diagnostics.state['lastFailure'] == failure
        fail(diagnostics)  # Normal known-scope failures are not eligible.
        item = active(app.state)[0]
        assert 'dispositionOccurrence' not in item
        with pytest.raises(AppError):
            await app.dispatch('updates.failureDisposition', {**args, 'id': item['id']}, origin='ui')
    finally:
        await app.close()
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        assert len(app.state['updates']['diagnostics']['failureDispositions']) == 1
        assert len(active(app.state)) == 1
        assert active(app.state)[0]['updateKind'] == 'ecosystem'
    finally:
        await app.close()


async def test_disposition_preserves_error_owner_and_does_not_hide_next_identical_error(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        app.update_manager = UpdateManager(app)
        diagnostics = app.update_manager.diagnostics
        diagnostics.begin('application', 'a'*40)
        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
        await app.update_manager.publish(phase='error', error='Synthetic update error.')
        item = active(app.state)[0]
        assert len(active(app.state)) == 1
        await app.dispatch('updates.failureDisposition', {'id': item['id'],
            'expectedOccurrence': item['dispositionOccurrence'], 'disposition': 'acknowledged-uncertainty'}, origin='ui')
        assert active(app.state)[0]['id'] == 'updates:error'
        assert app.state['updates']['error'] == 'Synthetic update error.'
        assert app.state['updates']['phase'] == 'error'  # Not a health/restart gate bypass.
        app.state['updates']['error'] = 'Different direct startup error.'
        assert active(app.state)[0]['id'] == 'updates:error'
        await app.update_manager.publish(error='Synthetic update error.')
        assert active(app.state)[0]['id'] == 'updates:error'
        assert not app.state['updates'].get('applicationResult')
    finally:
        await app.close()


def test_disposition_legacy_idless_scope_and_bounded_sanitized_history(tmp_path):
    from amplifier_web.update_report import disposition
    legacy = {'kind': 'application', 'phase': 'candidate-install', 'status': 'failed',
              'errorType': 'ValueError', 'at': 1}
    state, diagnostics = fixture(tmp_path, {'diagnostics': {'lastFailure': legacy, 'events': [legacy]}})
    old = active(state)[0]
    receipt = diagnostics.dispose(old['id'], old['dispositionOccurrence'])
    assert receipt['firstFailure']['status'] == 'failed'
    assert not active(state) and diagnostics.state['lastFailure'] == legacy
    for i in range(25):
        diagnostics.begin('application', 'a'*40)
        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
        item = active(state)[0]
        diagnostics.dispose(item['id'], item['dispositionOccurrence'])
    assert len(diagnostics.state['failureDispositions']) == 20
    assert not active(state) and snapshot(state)['badgeUnread'] == 0
    assert diagnostics.path.read_text().count('"status":"acknowledged-uncertainty"') == 26
    clean = disposition({**receipt, 'credential': '/private/secret', 'target': '/private/secret',
                         'firstFailure': {**legacy, 'secret': '/private/secret'}})
    assert '/private/secret' not in str(clean)
    assert clean['recoveryVerified'] is False


def test_activity_projects_only_bounded_disposition_summaries():
    from amplifier_web.browser_demand import project
    diagnostics = {'events': [{'id': 'not-needed'}], 'failureDispositions': [
        {'id': str(i), 'at': i, 'target': 'updates:failure-overflow',
         'occurrences': 1, 'status': 'acknowledged-uncertainty',
         'recoveryVerified': False, 'firstFailure': {'id': 'not-needed'}} for i in range(25)]}
    state = {'view': {'panel': 'activity'}, 'updates': {'diagnostics': diagnostics}}
    project(state)
    rows = state['updates']['diagnostics']['failureDispositions']
    assert len(rows) == 20 and rows[0]['id'] == '5'
    assert 'firstFailure' not in rows[0] and 'events' not in state['updates']['diagnostics']
    state = {'view': {'panel': None}, 'updates': {'diagnostics': diagnostics}}
    project(state)
    assert 'diagnostics' not in state['updates']
