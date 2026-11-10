"""Background failures must not conceal independent checks or leak fixtures."""
import hashlib
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
import subprocess
import shutil
import pytest

import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('browser_check_runner', ROOT / 'scripts/browser_check_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def execute(tmp_path, rows, **kwargs):
    (tmp_path / 'frontend').mkdir(exist_ok=True)
    output = tmp_path / 'results'
    code = runner.run_scenarios(tmp_path, {'schemaVersion': 1, 'scenarios': rows}, output,
                                provenance={'revision': 'a' * 40}, **kwargs)
    return code, json.loads((output / 'results.json').read_text()), output


def scenario(name, source):
    return {'id': name, 'command': [sys.executable, '-c', source]}


def test_failed_check_continues_and_keeps_original_failure_and_logs(tmp_path):
    code, report, output = execute(tmp_path, [scenario('fails', 'print("original failure");raise SystemExit(7)'),
                                             scenario('passes', 'print("independent result")')])
    assert code == 1 and report['complete'] and not report['passed']
    assert [row['status'] for row in report['scenarios']] == ['failed', 'passed']
    assert report['scenarios'][0]['exitCode'] == 7
    assert 'original failure' in (output / 'fails.log').read_text()
    assert 'independent result' in (output / 'passes.log').read_text()
    assert report['source']['revision'] == 'a' * 40


@pytest.mark.parametrize('detached', [False, True])
def test_owned_orphan_is_reaped_and_reported_before_next_check(tmp_path, detached):
    pid = tmp_path / 'child.pid'
    source = ('import subprocess,sys;from pathlib import Path;'
              f'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session={detached!r});Path({str(pid)!r}).write_text(str(p.pid))')
    code, report, _ = execute(tmp_path, [scenario('leaks', source), scenario('next', 'pass')])
    assert code == 1 and report['scenarios'][0]['cleanup']['terminated'] == 1
    observed = report['scenarios'][0]['cleanup']['observedBeforeTermination']
    assert len(observed) == 1 and observed[0]['pid'] == int(pid.read_text())
    assert observed[0]['startTime'].isdigit()
    assert observed[0]['executable'] == Path(sys.executable).resolve().name
    assert set(observed[0]) == {'pid', 'startTime', 'parentPid', 'processGroup', 'executable'}
    assert report['scenarios'][1]['status'] == 'passed'
    stat = Path('/proc') / pid.read_text() / 'stat'
    assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'


def test_uncertain_cleanup_stops_and_records_unrun_without_false_cascade(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'retire_group', lambda group, token, preexisting: {'remaining': [123], 'terminated': 1})
    code, report, _ = execute(tmp_path, [scenario('first', 'pass'), scenario('must-not-run', 'raise Exception("not reached")')])
    assert code == 1 and not report['complete']
    assert report['scenarios'][1]['status'] == 'unrun'
    assert report['scenarios'][1]['reason'] == 'unsafe_cleanup'
    assert not (tmp_path / 'results/must-not-run.log').exists()


def test_cleanup_identity_details_are_bounded_without_reducing_failure_count(monkeypatch):
    identities = {-pid: '1' for pid in range(1, 41)}
    snapshots = iter([{}, identities, {}])
    monkeypatch.setattr(runner, 'owned_members', lambda *args, **kwargs: next(snapshots))
    cleanup = runner.retire_group(-1, 'synthetic')
    assert cleanup['terminated'] == 40 and cleanup['remaining'] == []
    assert len(cleanup['observedBeforeTermination']) == 32
    assert cleanup['omittedProcessDetails'] == 8


def test_last_successful_exit_with_unreadable_ownership_still_fails(tmp_path, monkeypatch):
    def unavailable(*args):
        raise PermissionError('Cannot prove process ownership')
    monkeypatch.setattr(runner, 'retire_group', unavailable)
    code, report, _ = execute(tmp_path, [scenario('last', 'pass')])
    assert code == 1 and not report['passed']
    assert report['scenarios'][0]['status'] == 'failed'
    assert report['scenarios'][0]['cleanup']['remaining'] == ['unknown']


def test_timeout_kills_only_owned_process_and_next_check_runs(tmp_path):
    code, report, _ = execute(tmp_path, [scenario('timeout', 'import time;time.sleep(60)'),
                                        scenario('after', 'pass')], case_timeout=.15)
    assert code == 1 and report['scenarios'][0]['reason'] == 'timeout'
    assert report['scenarios'][0]['cleanup']['remaining'] == []
    assert report['scenarios'][1]['status'] == 'passed'


def test_cleanup_does_not_signal_an_unrelated_live_process(tmp_path):
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
    try:
        code, report, _ = execute(tmp_path, [scenario('passes', 'pass')])
        assert code == 0 and unrelated.poll() is None
        assert report['scenarios'][0]['cleanup']['terminated'] == 0
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_preexisting_unreadable_process_does_not_block_independent_checks(tmp_path, monkeypatch):
    unrelated = subprocess.Popen([sys.executable, '-c',
        'import ctypes,time;ctypes.CDLL(None).prctl(4,0,0,0,0);print("ready",flush=True);time.sleep(60)'],
        stdout=subprocess.PIPE, start_new_session=True)
    try:
        assert unrelated.stdout.readline() == b'ready\n'
        path = Path('/proc') / str(unrelated.pid) / 'environ'
        original_open = Path.open
        attempted = []
        def protected(self, *args, **kwargs):
            if self == path:
                attempted.append(True)
                raise PermissionError('Unrelated protected process')
            return original_open(self, *args, **kwargs)
        # Kernel restrictions vary with container capabilities. This preserves
        # the real process/start identity while making the denial deterministic.
        monkeypatch.setattr(Path, 'open', protected)
        code, report, _ = execute(tmp_path, [scenario('first', 'pass'), scenario('last', 'pass')])
        assert code == 0 and report['passed'] and report['complete']
        assert not attempted and unrelated.poll() is None
        assert all(row['cleanup'] == {'remaining': [], 'terminated': 0} for row in report['scenarios'])
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_new_unreadable_identity_is_not_exempt_and_stops_coverage(tmp_path, monkeypatch):
    ready = tmp_path / 'owned.pid'
    source = ('import subprocess,sys,json;from pathlib import Path;'
              'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session=True);'
              'birth=Path(f"/proc/{p.pid}/stat").read_text().rsplit(")",1)[1].split()[19];'
              f'Path({str(ready)!r}).write_text(json.dumps({{"pid":p.pid,"birth":birth}}))')
    original_open = Path.open
    def protected(self, *args, **kwargs):
        if self.name == 'environ' and ready.exists() and self.parent.name == str(json.loads(ready.read_text())['pid']):
            raise PermissionError('New potentially owned process')
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', protected)
    try:
        code, report, _ = execute(tmp_path, [scenario('new-protected', source), scenario('not-run', 'pass')])
        assert code == 1 and not report['complete']
        assert report['scenarios'][0]['cleanup']['remaining'] == ['unknown']
        assert report['scenarios'][1]['reason'] == 'unsafe_cleanup'
    finally:
        if ready.exists():
            # Test owns this exact child; the runner correctly refused to infer
            # its ownership from an unreadable environment.
            child = json.loads(ready.read_text())
            handle = os.pidfd_open(child['pid'])
            try:
                birth = Path(f'/proc/{child["pid"]}/stat').read_text().rsplit(')', 1)[1].split()[19]
                assert birth == child['birth']
                signal.pidfd_send_signal(handle, signal.SIGKILL)
            finally:
                os.close(handle)


def test_reused_pid_requires_fresh_proof_but_known_owner_precedes_baseline(monkeypatch):
    own = Path('/proc') / str(os.getpid())
    birth = (own / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    original_open = Path.open
    def protected(self, *args, **kwargs):
        if self == own / 'environ':
            raise PermissionError('Reused PID must be inspected')
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', protected)
    baseline = runner.process_identities()
    baseline[os.getpid()] = str(int(birth) - 1)
    with pytest.raises(PermissionError):
        runner.owned_members(-1, 'unused', {}, baseline)
    assert runner.owned_members(-1, 'unused', {os.getpid(): birth}, baseline)[os.getpid()] == birth


def test_budget_never_claims_unexecuted_checks_passed(tmp_path):
    code, report, _ = execute(tmp_path, [scenario('not-started', 'pass')], budget=0)
    assert code == 1 and not report['complete']
    assert report['scenarios'][0]['status'] == 'unrun'
    assert report['scenarios'][0]['reason'] == 'budget_exhausted'


def test_running_and_unrun_coverage_is_durable_before_execution(tmp_path):
    proof = tmp_path / 'results/results.json'
    source = (f'import json;from pathlib import Path;r=json.loads(Path({str(proof)!r}).read_text());'
              'assert [x["status"] for x in r["scenarios"]] == ["running","unrun"];assert not r["complete"]')
    code, report, _ = execute(tmp_path, [scenario('reads-proof', source), scenario('last', 'pass')])
    assert code == 0 and report['complete'] and report['passed']


def test_cancellation_reaps_current_and_marks_remainder_unrun(tmp_path):
    source = 'import os,signal,time;os.kill(os.getppid(),signal.SIGTERM);time.sleep(60)'
    code, report, _ = execute(tmp_path, [scenario('cancel', source), scenario('later', 'pass')])
    assert code == 1 and not report['complete']
    assert report['scenarios'][0]['reason'] == 'cancelled'
    assert report['scenarios'][0]['cleanup']['remaining'] == []
    assert report['scenarios'][1]['status'] == 'unrun'


def test_cli_initialization_and_execution_record_exact_source_and_plan(tmp_path):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'frontend').mkdir()
    shutil.copy(ROOT / 'scripts/browser_check_runner.py', tmp_path / 'scripts/browser_check_runner.py')
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'schemaVersion': 1, 'scenarios': [scenario('control', 'pass')]}))
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, stderr=subprocess.DEVNULL).decode().strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Synthetic runner source')
    command = [sys.executable, 'scripts/browser_check_runner.py', '--plan', 'plan.json', '--output', 'evidence']
    subprocess.run([*command, '--initialize'], cwd=tmp_path, check=True)
    initial = json.loads((tmp_path / 'evidence/results.json').read_text())
    assert not initial['passed'] and not initial['complete']
    assert initial['scenarios'][0]['status'] == 'unrun'
    subprocess.run(command, cwd=tmp_path, check=True)
    result = json.loads((tmp_path / 'evidence/results.json').read_text())
    assert result['passed'] and result['source']['revision'] == git('rev-parse', 'HEAD')
    assert result['source']['tree'] == git('rev-parse', 'HEAD^{tree}')
    assert result['source']['planSha256'] == hashlib.sha256(plan.read_bytes()).hexdigest()


def test_existing_scenario_commands_environments_and_distinct_modes_are_preserved():
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    rows = runner.validate_plan(plan)
    canvas_additions = canvas_browser_additions(rows, set())
    breadbin_addition = breadbin_browser_addition(rows, set())
    question_fade_addition = question_fade_browser_addition(rows, set())
    context_addition = context_inspector_browser_addition(rows, set())
    immediate_addition = immediate_direction_browser_addition(rows, set())
    waiting_addition = navigation_waiting_browser_addition(rows, set())
    delivery_addition = delivery_warning_browser_addition(rows, set())
    resume_addition = question_resume_browser_addition(rows, set())
    historical_rows = [row for row in rows
                       if not (canvas_additions | breadbin_addition | question_fade_addition | context_addition | immediate_addition | delivery_addition | waiting_addition | resume_addition).intersection(row['command'])]
    # Expanded exact .149 workflow invocations, including compound npm scripts.
    # Deliberate coverage changes must update this migration oracle explicitly.
    additions = {
        "state-stream": {},
        "client-traffic": {"AMPLIFIER_TEST_ARTIFACTS": "{root}/output/browser-checks/client-traffic"},
        "assignment-adoption": {},
        "delegate-work-stream": {'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/delegate-work-stream'},
        "request-cancellation": {},
        'provider-selection': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/provider-selection'},
        'provider-native': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/provider-native',
                            'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python'},
        'diagram-navigation': {'DIAGRAM_EVIDENCE': '{root}/output/browser-checks/diagram-navigation/report.json'},
        'chat-shortcuts': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/chat-shortcuts'},
        'automation-hub': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/automation-hub'},
        'cross-workspace-handoff': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/cross-workspace-handoff'},
        'chat-reference': {'CHAT_REFERENCE_EVIDENCE_DIR': '{root}/output/browser-checks/chat-reference'},
        'peer-attribution': {'AMPLIFIER_TEST_BUILT_STATIC': '1'},
        'device-pairing': {},
        'settings-drafts': {},
        'testing-settings': {},
        'standard-ux-keyboard-reflow': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/standard-ux-keyboard-reflow'},
        'approval-layout': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/approval-layout'},
        'approval-lifecycle': {'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/approval-lifecycle'},
        'standard-ux-connection-controls': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/standard-ux-connection-controls'},
        'standard-ux-preferences': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/standard-ux-preferences'},
        'standard-ux-privacy-effects': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/standard-ux-privacy-effects'},
        'standard-ux-voice-settings': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/standard-ux-voice-settings'},
        'smart-tool-updates': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/smart-tool-updates-artifacts'},
        'archive-feedback': {'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/archive-feedback'},
        'update-restart': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/update-restart-artifacts'},
        'update-effects': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/update-effects'},
        'sidebar-reading': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/sidebar-reading-artifacts'},
        'navigation-details-context': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/navigation-details-context-artifacts'},
        'pin-retention': {'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/pin-retention'},
        'pin-rapid-reorder': {'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/pin-rapid-reorder'},
        'workspace-shortcuts': {'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/workspace-shortcuts'},
        'destination-history': {'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/destination-history'},
        'dormant-controls': {'AMPLIFIER_DORMANT_EVIDENCE': '{root}/output/browser-checks/dormant-controls'},
        'mcp-recovery': {'MCP_RECOVERY_SCREENSHOT': '{root}/output/browser-checks/mcp-recovery.png'},
        'mcp-clients': {'MCP_RECOVERY_SCREENSHOT': '{root}/output/browser-checks/mcp-clients.png'},
        'inline-media-transition': {'EVIDENCE_DIR': '{root}/output/browser-checks/inline-media-transition'},
        'artifact-one-surface': {'ARTIFACT_EVIDENCE_DIR': '{root}/output/browser-checks/artifact-one-surface'},
        'execution-observability': {'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/execution-observability'},
        'test-environments': {'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python',
                              'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/test-environments'},
        'access-mode': {'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/access-mode'},
        'adaptive-work': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/adaptive-work'},
        'attachment-consumption': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/attachment-consumption'},
        'attachment-experience': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/attachment-experience'},
        'home-sidebar': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/home-sidebar'},
        'tool-details': {'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/tool-details'},
        'tool-previews': {'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/tool-previews'},
        'shared-landing': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/shared-landing'},
        'focus-navigation': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/focus-navigation'},
        'shared-work': {'AMPLIFIER_TEST_COLD_TASK': '1', 'AMPLIFIER_TEST_OUTPUT': '{root}/output/browser-checks/shared-work'},
        'model-capacity-settings': {'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/model-capacity-settings'},
        'compact-composer': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/compact-composer'},
        'composer-panel-ownership': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/composer-panel-ownership'},
        'peer-preview': {'AMPLIFIER_PROOF_DIR': '{root}/output/browser-checks/peer-preview'},
        'peer-preview-header': {'AMPLIFIER_PROOF_DIR': '{root}/output/browser-checks/peer-preview-header'},
        'peer-preview-focus': {'AMPLIFIER_PROOF_DIR': '{root}/output/browser-checks/peer-preview-focus'},
        'canvas-chrome': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/canvas-chrome'},
        'canvas-close': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/canvas-close'},
        'multi-quote': {'AMPLIFIER_PROOF_DIR': '{root}/output/browser-checks/multi-quote'},
        'storage-cleanup-settings': {'AMPLIFIER_TEST_OUTPUT_DIR': '{root}/output/browser-checks/storage-cleanup-settings'},
        'chat-scroll-call-boundaries': {},
        'semantic-settings': {},
        'history-refresh': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/history-refresh'},
        'voice-work-message': {'EVIDENCE_DIR': '{root}/output/browser-checks/voice-work-message'},
        'update-message-delivery': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/update-message-delivery'},
        'ready': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/ready',
                  'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python'},
        'question-transcript-fade': {
            'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/question-transcript-fade',
            'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python'},
    }
    added = [row for row in rows if row['id'] in additions]
    assert len(added) == len(additions)
    for row in added:
        assert row['command'] == ['node', f"tests/{row['id']}-browser.mjs"]
        assert row.get('env', {}) == additions[row['id']]
    recovery_additions = [
        {'id': 'configuration-recovery-components',
         'command': ['node', '--test', 'tests/configuration-recovery.test.mjs']},
        {'id': 'configuration-recovery',
         'command': ['node', 'tests/configuration-recovery-browser.mjs'],
         'env': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/configuration-recovery'}},
    ]
    recovery_ids = {row['id'] for row in recovery_additions}
    assert [row for row in rows if row['id'] in recovery_ids] == recovery_additions
    original = [row for row in historical_rows if row['id'] not in additions and row['id'] not in recovery_ids]
    assert len(original) == 65 and len(historical_rows) == 132
    # Preserve the preceding total after validating all exact additive owners.
    assert len([row for row in rows if row['id'] not in {'context-inspector-layout', 'delivery-warning', 'delivery-reconciliation', 'delivery-new-chat', 'navigation-waiting', 'canvas-versions', 'question-resume'}]) == 138
    # Existing scenarios retain their commands; only evidence ownership changes.
    evidence = {
        'bundle': {'AMPLIFIER_BUNDLE_SCREENSHOT': '{root}/output/browser-checks/bundle-mobile.png'},
        'canonical-work': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/canonical-work'},
        'canvas-media': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/canvas-media'},
    }
    for identity, expected in evidence.items():
        assert next(row for row in original if row['id'] == identity)['env'] == expected
    original = [{key: value for key, value in row.items() if key != 'env'} if row['id'] in evidence else row for row in original]
    assert hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest() == '115d865e2934f442582f89f77bde590ebc6101cb535c841efbba8f3e2511807f'
    commands = [row['command'] for row in rows]
    assert ['node', 'tests/session-health-browser.mjs', '--context-limit', '--active-worker'] in commands
    assert ['node', 'tests/empty-host-browser.mjs', '--lost-create'] in commands
    assert all((ROOT / 'frontend' / command[2 if command[1] == '--test' else 1]).is_file()
               for command in commands)


def test_dormant_controls_has_standard_ux_and_plan_owned_evidence():
    deltas = json.loads((ROOT / 'frontend/tests/standard-ux-inventory-deltas.json').read_text())
    delta = next(row for row in deltas['changes'] if row['id'] == 'delta.1035')
    workflow, = delta['workflowsAdded']
    owner = 'frontend/tests/dormant-controls-browser.mjs'
    assert workflow['id'] == 'conversation.next-message-settings'
    assert owner in {row['path'] for row in workflow['existingTests']}
    # This is evidence ownership, not a claim that a browser run has passed.
    source = (ROOT / owner).read_text()
    assert "process.env.AMPLIFIER_DORMANT_EVIDENCE||" in source
    assert "evidenceDir+'/browser-result.json'" in source
    assert "evidenceDir+'/dormant-controls-narrow.png'" in source
    assert "evidenceDir+'/dormant-controls-desktop.png'" in source


def test_voice_refinement_has_four_additive_standard_ux_owners():
    ledger = json.loads((ROOT / 'frontend/tests/standard-ux-inventory-deltas.json').read_text())
    delta, = [row for row in ledger['changes'] if row['id'] == 'delta.1153']
    paths = [test['path'] for flow in delta['workflowsAdded'] for test in flow['existingTests']]
    assert len(paths) == len(set(paths)) == 4
    assert set(paths) == {f'frontend/tests/{name}-browser.mjs' for name in
                          ('chat-scroll-call-boundaries', 'semantic-settings', 'history-refresh', 'voice-work-message')}


def test_compact_composer_peer_preview_and_artifact_keep_automatic_inventory_owners():
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    ledger = json.loads((ROOT / 'frontend/tests/standard-ux-inventory-deltas.json').read_text())
    owners = {
        'delta.compact-composer': {'compact-composer', 'composer-panel-ownership'},
        'delta.peer-preview': {'peer-preview', 'peer-preview-header', 'peer-preview-focus'},
        'delta.canvas-chrome': {'canvas-chrome'},
        'delta.multi-quote': {'multi-quote'},
        'delta.artifact-one-surface': {'artifact-one-surface'},
    }
    for identity, names in owners.items():
        delta, = [row for row in ledger['changes'] if row['id'] == identity]
        paths = {test['path'] for flow in delta['workflowsAdded'] for test in flow['existingTests']}
        assert all(f'frontend/tests/{name}-browser.mjs' in paths for name in names)
        for name in names:
            row, = [row for row in plan['scenarios'] if row['id'] == name]
            assert row['command'] == ['node', f'tests/{name}-browser.mjs']
            assert 'AMPLIFIER_TEST_PACKAGE_ROOT' not in row.get('env', {})
            assert any(f'/output/browser-checks/{name}' in value for value in row['env'].values())
    # Register the maintained keyboard regression with its existing evidence
    # writer, not a duplicate journey or a claim that runtime checks passed.
    keyboard_path = 'frontend/tests/composer-panel-ownership-browser.mjs'
    keyboard = (ROOT / keyboard_path).read_text()
    assert "process.env.AMPLIFIER_TEST_ARTIFACTS||" in keyboard
    assert "output+'/result.json'" in keyboard
    assert '${output}/keyboard-settings-${width}.png' in keyboard
    assert "output+'/failure.png'" in keyboard
    assert sum('{root}/output/browser-checks/composer-panel-ownership' in row.get('env', {}).values()
               for row in plan['scenarios']) == 1
    assert sum(test['path'] == keyboard_path for delta in ledger['changes']
               for flow in delta.get('workflowsAdded', []) for test in flow.get('existingTests', [])) == 1
    # Only maintained positive Peer journeys are automatic. Measurement and
    # deliberate failure modes remain opt-in qualification with raw receipts.
    for name, result_name in [('peer-preview-header', 'header-geometry.json'),
                              ('peer-preview-focus', 'focus-result.json')]:
        path = f'frontend/tests/{name}-browser.mjs'
        source = (ROOT / path).read_text()
        assert 'process.env.AMPLIFIER_PROOF_DIR' in source and result_name in source
        row, = [row for row in plan['scenarios'] if row['id'] == name]
        assert row['env'] == {'AMPLIFIER_PROOF_DIR': f'{{root}}/output/browser-checks/{name}'}
        assert sum(f'{{root}}/output/browser-checks/{name}' in item.get('env', {}).values()
                   for item in plan['scenarios']) == 1
        assert sum(test['path'] == path for delta in ledger['changes']
                   for flow in delta.get('workflowsAdded', []) for test in flow.get('existingTests', [])) == 1
    assert not any({'PEER_HEADER_MEASURE_ONLY', 'PEER_HEADER_NEGATIVE'} & row.get('env', {}).keys()
                   for row in plan['scenarios'])
    # Canvas chrome joins the plan, rather than replacing dirty-editor coverage.
    canvas, = [row for row in plan['scenarios'] if row['id'] == 'canvas-chrome']
    assert canvas['env'] == {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/canvas-chrome'}
    assert sum('{root}/output/browser-checks/canvas-chrome' in row.get('env', {}).values()
               for row in plan['scenarios']) == 1
    dirty, = [row for row in plan['scenarios'] if row['id'] == 'canvas-dirty']
    assert dirty == {'id': 'canvas-dirty', 'command': ['node', 'tests/canvas-dirty-browser.mjs']}


def test_workflow_keeps_independent_artifacts_and_one_full_python_owner():
    workflows = ROOT / '.github/workflows'
    browser = yaml.load((workflows / 'browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    steps = browser['jobs']['browser']['steps']
    assert any('browser_check_runner.py' in step.get('run', '') for step in steps)
    uploads = [step for step in steps if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02']
    assert len(uploads) == 2
    by_name = {step['with']['name']: step for step in uploads}
    diagnostics_name = 'extended-browser-evidence-${{ github.run_attempt }}'
    static_name = 'browser-static-${{ steps.build-source.outputs.revision }}-${{ github.run_attempt }}'
    assert set(by_name) == {diagnostics_name, static_name}
    diagnostics, static = by_name[diagnostics_name], by_name[static_name]
    assert diagnostics['if'] == 'always()'
    assert diagnostics['with'] == {
        'name': diagnostics_name,
        'path': 'output/browser-checks/\noutput/active-client-performance/\noutput/state-transport-browser/\noutput/scoped-navigation/\noutput/selected-context/\noutput/multi-quote/\n',
        'if-no-files-found': 'warn',
    }
    assert static['if'] == "${{ steps.frontend-build.outcome == 'success' }}"
    assert static['with'] == {
        'name': static_name, 'path': 'amplifier_web/static/',
        'include-hidden-files': 'true', 'if-no-files-found': 'error',
    }
    assert set(diagnostics['with']['path'].splitlines()).isdisjoint({static['with']['path']})
    # Never replace failed-run diagnostics with a successful-build-only upload.
    # Exact names, count and paths also reject additional or overlapping uploads.
    build = next(step for step in steps if step.get('id') == 'frontend-build')
    assert build['run'] == 'npm run build --prefix frontend'
    assert steps.index(build) < steps.index(static)
    readiness = yaml.load((workflows / 'public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    readiness_commands = '\n'.join(step.get('run', '') for step in readiness['jobs']['critical']['steps'])
    assert 'pytest' not in readiness_commands and 'npm test' not in readiness_commands
    # The nightly caller keeps the immutable revision and selects the complete
    # Python owner, rather than silently losing the removed readiness subsets.
    post = yaml.load((workflows / 'post-release.yml').read_text(), Loader=yaml.BaseLoader)
    caller = post['jobs']['python']
    assert caller['uses'] == './.github/workflows/python-checks.yml'
    assert caller['with'] == {'revision': '${{ needs.resolve.outputs.revision }}'}
    assert ' '.join(caller['if'].split()) == (
        "${{ !cancelled() && needs.resolve.outputs.identity_result == 'success' && "
        "(github.event_name == 'schedule' || (github.event_name == 'workflow_dispatch' && inputs.broad)) }}")
    python = yaml.load((workflows / 'python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    job = python['jobs']['python']
    assert job['env']['CHECK_SUITE'] == "${{ inputs.suite || 'full' }}"
    complete = next(step for step in job['steps'] if step.get('name') == 'Run the complete Python suite')
    assert complete['if'] == "${{ env.CHECK_SUITE == 'full' }}"
    assert complete['run'] == 'python -u scripts/browser_check_runner.py --python-suite --output output/python-checks'
    assert runner.PYTHON_COMMAND == ['.venv/bin/python', '-u', '-m', 'pytest', '-v', '--tb=short',
                                     '--junitxml=output/python-checks/junit.pending.xml']


@pytest.mark.parametrize('fault', ['none', 'python-skip', 'frontend-skip', 'unrun-browser', 'missing-junit'])
def test_destination_history_evidence_gate_rejects_incomplete_results(tmp_path, monkeypatch, fault):
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    step = next(row for row in workflow['jobs']['destination-history']['steps']
                if row.get('name') == 'Require executed evidence without skips')
    assert step['if'] == '${{ always() && !inputs.settings_close_diagnostic }}'
    assert workflow['on']['workflow_dispatch']['inputs']['settings_close_diagnostic']['default'] == 'false'
    code = step['run'].removeprefix("python - <<'PY'\n").removesuffix('PY\n')
    output = tmp_path / 'output/destination-history'
    output.mkdir(parents=True)
    if fault != 'missing-junit':
        skipped = '<skipped/>' if fault == 'python-skip' else ''
        (output / 'python.xml').write_text(f'<testsuites><testsuite><testcase name="proof">{skipped}</testcase></testsuite></testsuites>')
    skip = int(fault == 'frontend-skip')
    (output / 'frontend.log').write_text(f'# tests 1\n# pass {1-skip}\n# fail 0\n# skipped {skip}\n# cancelled 0\n# todo 0\n')
    plan = [{'id': name, 'command': ['node', f'tests/{name}-browser.mjs']} for name in (
        'destination-history', 'conversation-navigation', 'archive-feedback', 'canvas-dirty', 'draft-switch', 'automation-hub')]
    (output / 'plan.json').write_text(json.dumps({'scenarios': plan}))
    rows = [{**row, 'status': 'passed', 'exitCode': 0, 'reason': None,
             'cleanup': {'remaining': [], 'terminated': 0}} for row in plan]
    if fault == 'unrun-browser':
        rows[-1]['status'] = 'unrun'
    browser = tmp_path / 'output/browser-checks'
    browser.mkdir()
    (browser / 'results.json').write_text(json.dumps({'scenarios': rows, 'complete': True, 'passed': True}))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        exec(compile(code, '<workflow-evidence-gate>', 'exec'), {})
    assert exit.value.code == (0 if fault == 'none' else 1)
    result = json.loads((output / 'qualification.json').read_text())
    assert result['passed'] is (fault == 'none')


@pytest.mark.parametrize('provider', ['provider-selection', 'provider-native'])
@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'different-command', 'readiness-owner'])
def test_provider_browser_additions_reject_missing_or_duplicate_ordinary_owner(provider, fault):
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    script = f'tests/{provider}-browser.mjs'
    rows = plan['scenarios']
    readiness_scripts = set()
    if fault == 'missing':
        rows = [row for row in rows if script not in row['command']]
    elif fault in {'duplicate', 'different-command'}:
        rows.append({'id': provider + '-extra', 'command': ['node', script] +
                     (['--extra'] if fault == 'different-command' else [])})
    else:
        readiness_scripts.add(script)
    with pytest.raises(AssertionError, match=re.escape(script)):
        provider_browser_additions(rows, readiness_scripts)


def provider_browser_additions(rows, readiness_scripts):
    """These two source-only owners are additive, not historical baseline scripts."""
    scripts = {f'tests/{name}-browser.mjs' for name in ('provider-selection', 'provider-native')}
    for script in scripts:
        commands = [row['command'] for row in rows if script in row['command']]
        assert commands == [['node', script]], f'{script}: expected exactly one ordinary source owner'
        assert script not in readiness_scripts, f'{script}: duplicate readiness owner'
    return scripts


def breadbin_browser_addition(rows, readiness_scripts):
    """One background source owner; never subtract missing/duplicate coverage."""
    script = 'tests/ready-browser.mjs'
    owners = [row for row in rows if script in row['command']]
    assert owners == [{
        'id': 'ready', 'command': ['node', script],
        'env': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/ready',
                'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python'},
    }], f'{script}: expected one ordinary source owner and its exact environment'
    assert script not in readiness_scripts, f'{script}: duplicate readiness owner'
    return {script}


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'different-command',
                                  'missing-python', 'wrong-artifacts', 'managed-contract',
                                  'readiness-owner'])
def test_breadbin_browser_addition_rejects_missing_duplicate_or_wrong_environment(fault):
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    rows = plan['scenarios']
    script = 'tests/ready-browser.mjs'
    owner, = [row for row in rows if row['id'] == 'ready']
    readiness_scripts = set()
    if fault == 'missing':
        rows.remove(owner)
    elif fault == 'duplicate':
        rows.append({**owner, 'id': 'ready-extra'})
    elif fault == 'different-command':
        owner['command'].append('--extra')
    elif fault == 'missing-python':
        del owner['env']['AMPLIFIER_TEST_PYTHON']
    elif fault == 'wrong-artifacts':
        owner['env']['AMPLIFIER_TEST_ARTIFACTS'] = '{root}/output/browser-checks/provider-native'
    elif fault == 'managed-contract':
        owner['env']['AMPLIFIER_TEST_CONTRACT'] = '/invented/contract.json'
    else:
        readiness_scripts.add(script)
    with pytest.raises(AssertionError, match=re.escape(script)):
        breadbin_browser_addition(rows, readiness_scripts)


@pytest.mark.parametrize('canvas', ['canvas-refresh', 'canvas-refresh-catchup'])
@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'different-command', 'readiness-owner',
                                 'missing-env', 'wrong-env', 'wrong-id', 'wrong-command'])
def test_canvas_browser_additions_reject_changed_or_duplicate_ordinary_owner(canvas, fault):
    rows = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']
    script = f'tests/{canvas}-browser.mjs'
    owner = next(row for row in rows if row['id'] == canvas)
    readiness_scripts = set()
    if fault == 'missing':
        rows.remove(owner)
    elif fault in {'duplicate', 'different-command'}:
        rows.append({**owner, 'id': canvas + '-extra', 'command': ['node', script] +
                     (['--extra'] if fault == 'different-command' else [])})
    elif fault == 'readiness-owner':
        readiness_scripts.add(script)
    elif fault == 'missing-env':
        owner.pop('env')
    elif fault == 'wrong-env':
        owner['env']['AMPLIFIER_TEST_ARTIFACTS'] = '{root}/output/browser-checks/other'
    elif fault == 'wrong-id':
        owner['id'] = canvas + '-renamed'
    else:
        owner['command'] = ['node', 'tests/canvas-dirty-browser.mjs']
    with pytest.raises(AssertionError, match=re.escape(script)):
        canvas_browser_additions(rows, readiness_scripts)


def delivery_warning_browser_addition(rows, readiness_scripts):
    scripts = set()
    for name in ('delivery-warning', 'delivery-reconciliation', 'delivery-new-chat'):
        script = f'tests/{name}-browser.mjs'
        expected = {'id': name, 'command': ['node', script],
                    'env': {'AMPLIFIER_TEST_ARTIFACTS': f'{{root}}/output/browser-checks/{name}'}}
        owners = [row for row in rows if row['id'] == name or script in row['command']]
        assert owners == [expected], f'{script}: expected exactly one ordinary owner'
        assert script not in readiness_scripts, f'{script}: duplicate readiness owner'
        scripts.add(script)
    return scripts


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'command', 'environment', 'readiness'])
@pytest.mark.parametrize('name', ['delivery-warning', 'delivery-reconciliation', 'delivery-new-chat'])
def test_delivery_warning_browser_owner_is_exact(fault, name):
    rows = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']
    owner = next(row for row in rows if row['id'] == name)
    readiness = set()
    if fault == 'missing':
        rows.remove(owner)
    elif fault == 'duplicate':
        rows.append(json.loads(json.dumps(owner)))
    elif fault == 'command':
        owner['command'].append('--extra')
    elif fault == 'environment':
        owner['env'] = {}
    else:
        readiness.add(f'tests/{name}-browser.mjs')
    with pytest.raises(AssertionError, match=name):
        delivery_warning_browser_addition(rows, readiness)


def canvas_browser_additions(rows, readiness_scripts):
    """Validate new Canvas owners before excluding them from frozen inventories."""
    scripts = set()
    for name in ('canvas-refresh', 'canvas-refresh-catchup', 'canvas-versions'):
        script = f'tests/{name}-browser.mjs'
        owners = [row for row in rows if row['id'] == name or script in row['command']]
        expected = {'id': name, 'command': ['node', script],
                    'env': {'AMPLIFIER_TEST_ARTIFACTS': f'{{root}}/output/browser-checks/{name}'}}
        assert owners == [expected], f'{script}: expected exactly one ordinary owner with its exact environment'
        assert script not in readiness_scripts, f'{script}: duplicate readiness owner'
        scripts.add(script)
    return scripts


def question_fade_browser_addition(rows, readiness_scripts):
    """Validate the additive owner before excluding it from frozen coverage."""
    script = 'tests/question-transcript-fade-browser.mjs'
    owners = [row for row in rows if script in row['command']]
    assert owners == [{
        'id': 'question-transcript-fade', 'command': ['node', script],
        'env': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/question-transcript-fade',
                'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python'},
    }], f'{script}: expected one ordinary source owner and its exact environment'
    assert script not in readiness_scripts, f'{script}: duplicate readiness owner'
    return {script}


def immediate_direction_browser_addition(rows, readiness_scripts):
    """Validate the new real-worker owner without changing frozen coverage."""
    script = 'tests/immediate-direction-browser.mjs'
    owners = [row for row in rows if row['id'] == 'immediate-direction' or script in row['command']]
    assert owners == [{
        'id': 'immediate-direction', 'command': ['node', script],
        'env': {'AMPLIFIER_DIRECTION_EVIDENCE': '{root}/output/browser-checks/immediate-direction',
                'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python'},
    }], f'{script}: expected one exact ordinary source owner and environment'
    assert script not in readiness_scripts, f'{script}: duplicate readiness owner'
    return {script}


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'command', 'identity',
                                  'python', 'evidence', 'readiness'])
def test_immediate_direction_browser_owner_rejects_invalid_coverage(fault):
    rows = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']
    owner = next(row for row in rows if row['id'] == 'immediate-direction')
    readiness = set()
    if fault == 'missing':
        rows.remove(owner)
    elif fault == 'duplicate':
        rows.append({**owner, 'id': 'immediate-direction-extra'})
    elif fault == 'command':
        owner['command'] = ['node', 'tests/message-outbox-browser.mjs']
    elif fault == 'identity':
        owner['id'] = 'immediate-direction-renamed'
    elif fault == 'python':
        del owner['env']['AMPLIFIER_TEST_PYTHON']
    elif fault == 'evidence':
        owner['env']['AMPLIFIER_DIRECTION_EVIDENCE'] = '{root}/output/browser-checks/other'
    else:
        readiness.add('tests/immediate-direction-browser.mjs')
    with pytest.raises(AssertionError, match='immediate-direction'):
        immediate_direction_browser_addition(rows, readiness)


def question_resume_browser_addition(rows, readiness_scripts):
    script = 'tests/question-resume-browser.mjs'
    assert [row for row in rows if script in row['command']] == [{
        'id': 'question-resume', 'command': ['node', script],
        'env': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/question-resume',
                'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python'},
    }], 'question-resume: expected one exact ordinary owner'
    assert script not in readiness_scripts, 'question-resume: duplicate readiness owner'
    return {script}


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'environment', 'readiness'])
def test_question_resume_owner_is_distinct(fault):
    rows = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']
    owner = next(row for row in rows if row['id'] == 'question-resume')
    readiness = set()
    if fault == 'missing': rows.remove(owner)
    elif fault == 'duplicate': rows.append(dict(owner))
    elif fault == 'environment': owner['env'] = {}
    else: readiness.add('tests/question-resume-browser.mjs')
    with pytest.raises(AssertionError, match='question-resume'):
        question_resume_browser_addition(rows, readiness)


def navigation_waiting_browser_addition(rows, readiness_scripts):
    script = 'tests/navigation-waiting-browser.mjs'
    assert [row for row in rows if script in row['command']] == [{
        'id': 'navigation-waiting', 'command': ['node', script],
        'env': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/navigation-waiting',
                'AMPLIFIER_TEST_PYTHON': '{root}/.venv/bin/python'},
    }], 'navigation-waiting: expected one exact ordinary owner'
    assert script not in readiness_scripts, 'navigation-waiting: duplicate readiness owner'
    return {script}


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'environment', 'readiness'])
def test_navigation_waiting_owner_is_distinct(fault):
    rows = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']
    owner = next(row for row in rows if row['id'] == 'navigation-waiting')
    readiness = set()
    if fault == 'missing': rows.remove(owner)
    elif fault == 'duplicate': rows.append(dict(owner))
    elif fault == 'environment': owner['env'] = {}
    else: readiness.add('tests/navigation-waiting-browser.mjs')
    with pytest.raises(AssertionError, match='navigation-waiting'):
        navigation_waiting_browser_addition(rows, readiness)


def context_inspector_browser_addition(rows, readiness_scripts):
    """New feature owner; preserve every frozen preceding coverage count."""
    scripts = set()
    for name in ('context-inspector', 'context-inspector-layout'):
        script = f'tests/{name}-browser.mjs'
        assert [row for row in rows if script in row['command']] == [{
            'id': name, 'command': ['node', script],
            'env': {'AMPLIFIER_TEST_ARTIFACTS': f'{{root}}/output/browser-checks/{name}'},
        }], f'{script}: expected one exact ordinary source owner'
        assert script not in readiness_scripts, f'{script}: duplicate readiness owner'
        scripts.add(script)
    return scripts


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'environment', 'readiness'])
@pytest.mark.parametrize('name', ['context-inspector', 'context-inspector-layout'])
def test_context_inspector_browser_owner_is_distinct(fault, name):
    rows = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']
    owner = next(row for row in rows if row['id'] == name)
    readiness = set()
    if fault == 'missing': rows.remove(owner)
    elif fault == 'duplicate': rows.append(dict(owner))
    elif fault == 'environment': owner['env'] = {}
    else: readiness.add(f'tests/{name}-browser.mjs')
    with pytest.raises(AssertionError, match='context-inspector'):
        context_inspector_browser_addition(rows, readiness)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'different-command',
                                  'missing-python', 'wrong-artifacts', 'managed-contract',
                                  'readiness-owner'])
def test_question_fade_browser_addition_rejects_missing_duplicate_or_wrong_environment(fault):
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    rows = plan['scenarios']
    script = 'tests/question-transcript-fade-browser.mjs'
    owner, = [row for row in rows if row['id'] == 'question-transcript-fade']
    readiness_scripts = set()
    if fault == 'missing':
        rows.remove(owner)
    elif fault == 'duplicate':
        rows.append({**owner, 'id': 'question-transcript-fade-extra'})
    elif fault == 'different-command':
        owner['command'].append('--extra')
    elif fault == 'missing-python':
        del owner['env']['AMPLIFIER_TEST_PYTHON']
    elif fault == 'wrong-artifacts':
        owner['env']['AMPLIFIER_TEST_ARTIFACTS'] = '{root}/output/browser-checks/provider-native'
    elif fault == 'managed-contract':
        owner['env']['AMPLIFIER_TEST_CONTRACT'] = '/invented/contract.json'
    else:
        readiness_scripts.add(script)
    with pytest.raises(AssertionError, match=re.escape(script)):
        question_fade_browser_addition(rows, readiness_scripts)


def test_source_browser_duplicates_have_one_owner_and_package_overlaps_remain():
    workflows = ROOT / '.github/workflows'
    readiness = yaml.load((workflows / 'public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    steps = readiness['jobs']['critical']['steps']
    commands = [(index, line.strip()) for index, step in enumerate(steps)
                for line in step.get('run', '').splitlines()]
    readiness_scripts = {match.group(1) for _, line in commands
                         if (match := re.search(r'node (tests/[^ ]+-browser\.mjs)', line))}
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    extended_scripts = {row['command'][2 if row['command'][1] == '--test' else 1]
                        for row in plan['scenarios']}
    source_only = {f'tests/{name}-browser.mjs' for name in (
        'interim-replies', 'draft-reload', 'draft-switch', 'activity-feedback',
        'conversation-navigation', 'workspace-shortcuts', 'inline-media-transition', 'execution-observability',
        'cross-workspace-handoff', 'chat-reference', 'automation-hub', 'settings-drafts', 'approval-layout', 'approval-lifecycle')}
    assert source_only <= extended_scripts
    assert not source_only & readiness_scripts
    installed_overlap = {f'tests/{name}-browser.mjs' for name in (
        'feedback', 'message-outbox', 'canonical-work')}
    assert readiness_scripts & extended_scripts == installed_overlap
    install = next(index for index, line in commands if 'uv pip install' in line and '--reinstall .' in line)
    for script in installed_overlap:
        assert any(index > install and f'node {script}' in line for index, line in commands)
    appearance_preview = 'tests/appearance-preview-navigation-browser.mjs'
    assert appearance_preview in readiness_scripts
    assert appearance_preview not in extended_scripts
    assert sum(f'node {appearance_preview}' in line for _, line in commands) == 1
    # All 108 original scripts, three navigation regressions, confirmed restart,
    # Smart Tool recovery, archive feedback and appearance preview navigation
    # and sequential keyboard reflow, saved MCP recovery, device pairing, and media transition
    # each retain an explicit owning lane; unsaved settings, independent MCP clients
    # and the approval reading-column regression now have owners too. The two
    # recovery additions retain distinct component and full-journey commands;
    # the existing-chat card, adaptive Home, temporary Focus passes and sidebar modes each add a journey.
    # Attachment consumption is one additional source-only owner, not a
    # duplicate readiness invocation or a replacement of any existing script.
    attachment = 'tests/attachment-consumption-browser.mjs'
    assert attachment in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', attachment] for row in plan['scenarios']) == 1
    # Shared Work is also additive: keep the pre-existing union and its single
    # source owner, rather than counting it as an original baseline script.
    shared_work = 'tests/shared-work-browser.mjs'
    assert shared_work in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', shared_work] for row in plan['scenarios']) == 1
    # Destination history adds one source-only journey without replacing an owner.
    history = 'tests/destination-history-browser.mjs'
    assert history in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', history] for row in plan['scenarios']) == 1
    cancellation = 'tests/request-cancellation-browser.mjs'
    assert cancellation in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', cancellation] for row in plan['scenarios']) == 1
    shared_landing = 'tests/shared-landing-browser.mjs'
    assert shared_landing in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', shared_landing] for row in plan['scenarios']) == 1
    tool_details = 'tests/tool-details-browser.mjs'
    assert tool_details in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', tool_details] for row in plan['scenarios']) == 1
    tool_previews = 'tests/tool-previews-browser.mjs'
    assert tool_previews in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', tool_previews] for row in plan['scenarios']) == 1
    attachment_experience = 'tests/attachment-experience-browser.mjs'
    assert attachment_experience in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', attachment_experience] for row in plan['scenarios']) == 1
    pin_reorder = 'tests/pin-rapid-reorder-browser.mjs'
    assert pin_reorder in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', pin_reorder] for row in plan['scenarios']) == 1
    approval = 'tests/approval-lifecycle-browser.mjs'
    assert approval in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', approval] for row in plan['scenarios']) == 1
    dormant = 'tests/dormant-controls-browser.mjs'
    assert dormant in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', dormant] for row in plan['scenarios']) == 1
    # New source journeys are automatic; their manual lanes add installed and
    # negative-control proof without replacing any existing ordinary owner.
    feature_additions = {f'tests/{name}-browser.mjs' for name in
                         ('compact-composer', 'composer-panel-ownership', 'peer-preview', 'peer-preview-header', 'peer-preview-focus',
                          'canvas-chrome', 'canvas-close', 'multi-quote', 'artifact-one-surface', 'delegate-work-stream', 'client-traffic')}
    voice_additions = {f'tests/{name}-browser.mjs' for name in
                       ('chat-scroll-call-boundaries', 'semantic-settings', 'history-refresh', 'voice-work-message')}
    assert voice_additions <= extended_scripts - readiness_scripts
    assert all(sum(row['command'] == ['node', name] for row in plan['scenarios']) == 1
               for name in voice_additions)
    assert feature_additions <= extended_scripts - readiness_scripts
    # Options already has an automatic installed-readiness owner.
    assert 'tests/composer-options-browser.mjs' in readiness_scripts - extended_scripts
    assert sum('node tests/composer-options-browser.mjs' in line for _, line in commands) == 1
    assert all(sum(row['command'] == ['node', name] for row in plan['scenarios']) == 1
               for name in feature_additions)
    # The stream recovery regression adds one background owner. Keep all
    # preceding coverage counts frozen rather than relabeling their baseline.
    state_stream = 'tests/state-stream-browser.mjs'
    assert state_stream in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', state_stream] for row in plan['scenarios']) == 1
    update_delivery = 'tests/update-message-delivery-browser.mjs'
    assert update_delivery in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', update_delivery] for row in plan['scenarios']) == 1
    provider_additions = provider_browser_additions(plan['scenarios'], readiness_scripts)
    breadbin_addition = breadbin_browser_addition(plan['scenarios'], readiness_scripts)
    canvas_additions = canvas_browser_additions(plan['scenarios'], readiness_scripts)
    question_fade_addition = question_fade_browser_addition(plan['scenarios'], readiness_scripts)
    context_addition = context_inspector_browser_addition(plan['scenarios'], readiness_scripts)
    immediate_addition = immediate_direction_browser_addition(plan['scenarios'], readiness_scripts)
    waiting_addition = navigation_waiting_browser_addition(plan['scenarios'], readiness_scripts)
    delivery_addition = delivery_warning_browser_addition(plan['scenarios'], readiness_scripts)
    resume_addition = question_resume_browser_addition(plan['scenarios'], readiness_scripts)
    # Freeze all three prior counts; validate each new owner before subtracting
    # the additions so missing or duplicated coverage cannot disappear.
    previous_scripts = ((readiness_scripts | extended_scripts) - {state_stream, update_delivery}
                        - provider_additions - breadbin_addition - canvas_additions - question_fade_addition - context_addition - immediate_addition - delivery_addition - waiting_addition - resume_addition)
    assert len(previous_scripts - {attachment, cancellation, shared_landing, tool_details, tool_previews, attachment_experience, pin_reorder, approval, dormant, shared_work} - feature_additions - voice_additions) == 146
    assert len(previous_scripts - {shared_work, 'tests/delegate-work-stream-browser.mjs', 'tests/client-traffic-browser.mjs'} - voice_additions) == 164
    assert len(previous_scripts) == 171
    assert 'tests/update-effects-browser.mjs' in extended_scripts - readiness_scripts
    assert 'tests/adaptive-work-browser.mjs' in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', 'tests/adaptive-work-browser.mjs']
               for row in plan['scenarios']) == 1
    assert 'tests/focus-navigation-browser.mjs' in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', 'tests/focus-navigation-browser.mjs']
               for row in plan['scenarios']) == 1
    assert 'tests/access-mode-browser.mjs' in extended_scripts - readiness_scripts
    assert sum(row['command'] == ['node', 'tests/access-mode-browser.mjs']
               for row in plan['scenarios']) == 1
    assert {'tests/configuration-recovery.test.mjs',
            'tests/configuration-recovery-browser.mjs'} <= extended_scripts - readiness_scripts
    assert 'tests/mcp-recovery-browser.mjs' in extended_scripts - readiness_scripts
    assert 'tests/device-pairing-browser.mjs' in extended_scripts - readiness_scripts

    # Keep coordination's existing direct owner; the extended manifest now also
    # runs this fixed journey exactly once with its own retained evidence folder.
    environment = 'tests/test-environments-browser.mjs'
    assert environment not in readiness_scripts and environment in extended_scripts
    assert sum(row['command'] == ['node', environment] for row in plan['scenarios']) == 1
    owners = []
    for path in workflows.glob('*.yml'):
        value = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job in value.get('jobs', {}).values():
            for step in job.get('steps', []):
                if f'node {environment}' in step.get('run', ''):
                    owners.append((path.name, step))
    assert len(owners) == 1 and owners[0][0] == 'coordination.yml'
    assert owners[0][1]['env']['AMPLIFIER_TEST_OUTPUT_DIR'] == '${{ runner.temp }}/test-environments'
    coordination = yaml.load((workflows / 'coordination.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'frontend/' + environment in coordination['on']['pull_request']['paths']
    assert any(step.get('if') == 'always()' and step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
               and step.get('with', {}).get('path', '').splitlines() == [
                   '${{ runner.temp }}/test-environments',
                   'output/coordination-runtime-readiness.json']
               for step in coordination['jobs']['contracts']['steps'])


def test_attachment_focus_selects_exact_existing_owner_without_changing_plan():
    import copy
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    before = copy.deepcopy(plan)
    focused = runner.attachment_consumption_plan(plan)
    assert plan == before
    assert focused == {'schemaVersion': 1, 'scenarios': [{
        'id': 'attachment-consumption',
        'command': ['node', 'tests/attachment-consumption-browser.mjs'],
        'env': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/attachment-consumption'},
    }]}
    for mutation in ('missing', 'duplicate', 'command', 'environment'):
        broken = copy.deepcopy(focused)
        if mutation == 'missing':
            broken['scenarios'] = []
        elif mutation == 'duplicate':
            broken['scenarios'] *= 2
        elif mutation == 'command':
            broken['scenarios'][0]['command'] = ['node', 'tests/other.mjs']
        else:
            broken['scenarios'][0]['env'] = {}
        with pytest.raises(ValueError):
            runner.attachment_consumption_plan(broken)


def test_attachment_focus_workflow_retains_failures_and_default_coverage():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['workflow_dispatch']['inputs']['attachment_consumption']['default'] == 'false'
    assert 'attachment_consumption' not in workflow['on']['workflow_call']['inputs']
    job = workflow['jobs']['browser']
    assert job['if'] == '${{ !inputs.permissions_history && !inputs.readiness_trio && !inputs.canvas_qualification && !inputs.peer_messages && !inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic && !inputs.attachment_experience }}'
    runs = [step for step in job['steps'] if 'scripts/browser_check_runner.py' in step.get('run', '')]
    assert len(runs) == 2
    flag = "${{ inputs.attachment_consumption && '--attachment-consumption' || '' }}"
    assert all(flag in step['run'] for step in runs)
    assert '--budget-seconds 1500' in runs[1]['run']
    host = next(step for step in job['steps'] if step.get('id') == 'attachment-contracts')
    assert host['if'] == '${{ inputs.attachment_consumption }}' and host['continue-on-error'] == 'true'
    targets = re.findall(r'tests/test_[\w]+\.py', host['run'])
    assert targets == ['tests/test_attachments.py', 'tests/test_message_delivery.py',
                       'tests/test_live_clients.py', 'tests/test_chat_references.py',
                       'tests/test_browser_check_runner.py', 'tests/test_release_qualification.py']
    assert '--junitxml=output/browser-checks/attachment-contracts.xml' in host['run']
    final = next(step for step in job['steps'] if step.get('name') == 'Preserve failed attachment contracts')
    assert final['run'] == 'exit 1'
    assert "always()" in final['if'] and "steps.attachment-contracts.outcome != 'success'" in final['if']
    assert "steps.attachment-client-contracts.outcome != 'success'" in final['if']
    assert any(step.get('if') == 'always()' and step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
               for step in job['steps'])


def test_reload_failure_diagnostics_have_retained_scenario_owners():
    rows = {row['id']: row for row in json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']}
    for identity in ('canonical-work', 'canvas-media'):
        assert rows[identity]['env']['AMPLIFIER_TEST_ARTIFACTS'] == '{root}/output/browser-checks/' + identity
    canvas = (ROOT / 'frontend/tests/canvas-media-browser.mjs').read_text()
    assert "process.env.AMPLIFIER_TEST_ARTIFACTS" in canvas
    assert "outputDir+'/failure.json'" in canvas
    canonical = (ROOT / 'frontend/tests/canonical-work-browser.mjs').read_text()
    assert "join(outputDir,'failure-groups.json')" in canonical


def test_performance_reports_are_distinct_and_uploaded():
    rows = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())['scenarios']
    reports = {row['id']: row['env']['AMPLIFIER_PERF_EVIDENCE'] for row in rows
               if 'AMPLIFIER_PERF_EVIDENCE' in row.get('env', {})}
    assert len(set(reports.values())) == len(reports), 'A later scenario must not overwrite prior diagnostics'
    assert reports['active-client-performance'] != reports['state-transport']
    # Demand has no performance report writer; it must not borrow another path.
    assert 'demand' not in reports
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    uploads = [step for step in workflow['jobs']['browser']['steps']
               if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02']
    directories = [line.strip().rstrip('/') + '/' for step in uploads
                   for line in step['with']['path'].splitlines() if line.strip()]
    assert all(any(path.removeprefix('{root}/').startswith(directory) for directory in directories)
               for path in reports.values())


def test_destination_history_manual_route_is_fixed_and_preserves_default_coverage():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    option = workflow['on']['workflow_dispatch']['inputs']['destination_history']
    assert option['type'] == 'boolean' and option['default'] == 'false'
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['jobs']['browser']['if'] == '${{ !inputs.permissions_history && !inputs.readiness_trio && !inputs.canvas_qualification && !inputs.peer_messages && !inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic && !inputs.attachment_experience }}'
    focused = workflow['jobs']['destination-history']
    assert focused['if'] == '${{ !inputs.permissions_history && !inputs.readiness_trio && !inputs.peer_messages && !inputs.compact_composer && (inputs.destination_history || inputs.settings_close_diagnostic) }}'
    assert workflow['on']['workflow_dispatch']['inputs']['settings_close_diagnostic']['default'] == 'false'
    assert focused['runs-on'] == 'ubuntu-latest' and focused['timeout-minutes'] == '30'
    steps = focused['steps']
    assert not any(step.get('continue-on-error') for step in steps)
    run = '\n'.join(step.get('run', '') for step in steps)
    assert 'secrets.' not in json.dumps(focused)
    assert "wanted = ('destination-history', 'conversation-navigation', 'archive-feedback', 'canvas-dirty', 'draft-switch', 'automation-hub')" in run
    assert "sum(row['id'] == name for row in rows) == 1" in run
    assert 'browser_check_runner.py --plan output/destination-history/plan.json --output output/browser-checks --initialize' in run
    assert '--budget-seconds 1200' in run
    for target in ('test_history_scope.py', 'test_auth.py', 'test_client_navigation.py',
                   'test_approachable_shell.py', 'test_automation_overview.py', 'test_schedules.py',
                   'test_schedule_new_tasks.py', 'test_schedule_policy.py'):
        assert f'tests/{target}' in run
    assert 'tests/test_browser_check_runner.py::test_destination_history_manual_route_is_fixed_and_preserves_default_coverage' in run
    assert 'tests/test_release_qualification.py' not in run
    for target in ('destination-history', 'destination-history-ui', 'api',
                   'conversation-navigation', 'pending-view', 'settings-drafts',
                   'work-shell', 'archive-action', 'archive-feedback', 'attention', 'shell-panels',
                   'automation-hub', 'schedule-editor', 'submission-reset'):
        assert f'tests/{target}.test.mjs' in run
    uploads = [step for step in steps if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02']
    assert len(uploads) == 1 and uploads[0]['if'] == 'always()'
    assert 'output/destination-history/' in uploads[0]['with']['path']
    assert 'output/browser-checks/' in uploads[0]['with']['path']


def test_attachment_experience_route_is_fixed_and_installs_the_exact_wheel():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    option = workflow['on']['workflow_dispatch']['inputs']['attachment_experience']
    assert option == {'description': 'Fixed attachment modal/drop and consumption checks, source and installed wheel',
                      'type': 'boolean', 'default': 'false'}
    job = workflow['jobs']['attachment-experience']
    assert job['if'] == '${{ !inputs.permissions_history && !inputs.readiness_trio && inputs.attachment_experience }}'
    assert job['timeout-minutes'] == '30'
    runs = '\n'.join(step.get('run', '') for step in job['steps'])
    assert "wanted = ('attachment-experience', 'attachment-consumption')" in runs
    assert "intersections = ('pin-retention', 'pin-rapid-reorder', 'conversation-navigation', 'compact-composer', 'peer-preview')" in runs
    assert "assert len(matches) == 1" in runs
    assert 'uv sync --locked --group dev' in runs
    assert 'uv pip install --python .venv/bin/python --no-deps --reinstall output/attachment-experience/wheels/*.whl' in runs
    assert "file.read_bytes() == archive.read(" in runs
    assert 'secrets.' not in json.dumps(job)
    assert all(step.get('if') == 'always()' for step in job['steps']
               if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02')
    assert "steps.installed-browser.outcome != 'success'" in json.dumps(job)
    assert "steps.intersections.outcome != 'success'" in json.dumps(job)


# Independent literal identities: do not construct the positive TAP fixture from
# the validator's allow-list, or a changed/missing identity could validate itself.
APP004_EXPECTED_GROUPS = (
    (
        'chat reference metadata is a fixed bounded data shape',
        'chat reference rejects action',
        'chat reference rejects URL',
        'chat reference rejects foreign source',
        'chat reference rejects foreign message',
        'chat reference rejects missing target',
        'chat reference rejects wrong version',
        'chat reference rejects invalid revision',
        'chat reference rejects unbounded label',
        'chat reference rejects control character',
        'chat reference rejects missing availability',
        'chat reference rejects duplicate, oversized and expanded envelopes',
    ),
    (
        'copy uses an available message without another request',
        'copy reads an explicitly addressed off-page chat without changing selection',
        'missing scoped message reports failure instead of copying unrelated content',
        'copy retains prose and appends only the host-supplied inert reference fallback',
    ),
    (
        'held route stays blocked through exercise and drains once before unregistration',
        'drain includes concurrent callbacks and waiting unroute joins a final protocol arrival',
        'callback errors become an awaited failure, not ignored or retried',
        'primary assertion survives successful release and cleanup',
        'primary error is retained with callback and teardown errors as secondary',
        'early-started action rejection stays observable until it is awaited',
        'cleanup failure alone fails and cannot replace a prior primary failure',
    ),
)
APP004_EXPECTED_NAMES = tuple(name for group in APP004_EXPECTED_GROUPS for name in group)
ABSENT = object()


def app004_tap():
    return ('TAP version 13\n'
            + ''.join(f'ok {index} - {name}\n' for index, name in enumerate(APP004_EXPECTED_NAMES, 1))
            + '1..23\n# tests 23\n# suites 0\n# pass 23\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n')


def app004_witness(token='fresh-fixture-token'):
    # Synthetic validator input, NOT evidence of a browser/card/Node execution.
    outcome = {
        'qualificationToken': token, 'negativeReload': True,
        'identityBoundary': {
            'id': 'app004-same-document', 'attachmentAccepted': True, 'cardVisible': True,
            'draftHeld': True, 'clickCompleted': True, 'attempted': True, 'verdict': 'failed',
            'targetSessionId': 'fixture-target', 'selectedSessionId': 'fixture-target',
            'before': {'document': 'before-document', 'timeOrigin': 1000, 'client': 'fixture-client'},
            'after': {'document': 'after-document', 'timeOrigin': 2000, 'client': 'fixture-client'},
        },
        'primary': {'name': 'AssertionError', 'code': 'ERR_ASSERTION', 'boundary': 'app004-same-document'},
        'browserErrors': [], 'secondary': [], 'fixtureExit': {'code': 0, 'signal': None},
    }
    routes = [
        {'route': 'held-draft', 'phase': 'registered'},
        {'route': 'held-draft', 'phase': 'started', 'id': 1},
        {'route': 'held-draft', 'phase': 'gate-released'},
        {'route': 'held-draft', 'phase': 'completed', 'id': 1},
        {'route': 'held-draft', 'phase': 'drained'},
        {'route': 'held-draft', 'phase': 'unregistered'},
    ]
    return outcome, routes


def write_app004_witness(folder, token='fresh-fixture-token'):
    folder.mkdir(parents=True, exist_ok=True)
    outcome, routes = app004_witness(token)
    (folder / 'outcome.json').write_text(json.dumps(outcome))
    (folder / 'route-lifecycle.json').write_text(json.dumps(routes))
    return outcome, routes


def change_app004_field(value, path, replacement):
    keys = path.split('.')
    for key in keys[:-1]:
        value = value[key]
    if replacement is ABSENT:
        value.pop(keys[-1])
    else:
        value[keys[-1]] = replacement


def prepare_app004_verdict(tmp_path, identity='reload-negative'):
    folder = tmp_path / 'qualification'
    write_app004_witness(folder / 'negative')
    row = {'id': identity, 'status': 'failed' if identity == 'reload-negative' else 'passed',
           'reason': None, 'exitCode': 1 if identity == 'reload-negative' else 0,
           'cleanup': {'remaining': [], 'terminated': 0}, 'executionToken': 'fresh-fixture-token',
           'log': identity + '.log'}
    (folder / row['log']).write_text(app004_tap() if identity == 'units' else 'synthetic assertion failure\n')
    return row, folder


def mock_app004_commands(monkeypatch, *, units_source=None, negative_source=None, timeout=2):
    # Only descriptors are substituted. Real scheduler, ownership, deadlines and
    # verdicts run against harmless Python children; this is not Node/Chromium QA.
    initial = runner.initial_app004
    outcome, routes = app004_witness()
    if units_source is None:
        units_source = (f'print({app004_tap()!r},end="");import os;'
                        'print("# fixture-owner "+os.environ["AMPLIFIER_BROWSER_CHECK_OWNER"])')
    if negative_source is None:
        negative_source = (
            'import json,os;from pathlib import Path;'
            'token=os.environ["APP004_QUALIFICATION_TOKEN"];'
            'assert token and token!="inherited-stale-token";'
            'assert token==os.environ["AMPLIFIER_BROWSER_CHECK_OWNER"];'
            'assert os.environ["CHAT_REFERENCE_NEGATIVE_RELOAD"]=="1";'
            f'outcome=json.loads({json.dumps(outcome)!r});outcome["qualificationToken"]=token;'
            'folder=Path(os.environ["CHAT_REFERENCE_EVIDENCE_DIR"]);folder.mkdir(parents=True,exist_ok=True);'
            '(folder/"outcome.json").write_text(json.dumps(outcome));'
            f'(folder/"route-lifecycle.json").write_text({json.dumps(routes)!r});'
            'print("synthetic negative witness");raise SystemExit(1)'
        )
    def fixed(provenance):
        report = initial(provenance)
        for row, source in zip(report['checks'], (units_source, negative_source), strict=True):
            row['command'] = [sys.executable, '-c', source]
            row['timeoutSeconds'] = timeout
        return report
    monkeypatch.setattr(runner, 'initial_app004', fixed)


def test_app004_fixed_commands_and_literal_twenty_three_identities():
    provenance = {'revision': 'b' * 40}
    report = runner.initial_app004(provenance)
    assert report == {
        'schemaVersion': 1, 'source': provenance, 'complete': False, 'passed': False,
        'checks': [
            {'id': 'units', 'status': 'unrun', 'reason': 'not_started', 'timeoutSeconds': 60,
             'command': ['node', '--test', '--test-reporter=tap', 'tests/chat-reference.test.mjs',
                         'tests/message-copy.test.mjs', 'tests/chat-reference-harness.test.mjs']},
            {'id': 'reload-negative', 'status': 'unrun', 'reason': 'not_started', 'timeoutSeconds': 300,
             'command': ['node', 'tests/chat-reference-browser.mjs']},
        ],
    }
    assert tuple(map(len, APP004_EXPECTED_GROUPS)) == (12, 4, 7)
    assert len(APP004_EXPECTED_NAMES) == len(set(APP004_EXPECTED_NAMES)) == 23
    assert tuple(runner.APP004_UNIT_NAMES) == APP004_EXPECTED_NAMES
    assert runner.APP004_BOUNDARY == 'app004-same-document'


@pytest.mark.parametrize('corruption', [
    'missing-case', 'duplicate-name', 'foreign-name', 'extra-case', 'wrong-tests', 'wrong-pass',
    'failure', 'cancelled', 'skipped', 'todo', 'skip-directive', 'todo-directive',
    'not-ok', 'bail-out', 'duplicate-summary', 'missing-summary',
])
def test_app004_units_requires_exact_unique_unskipped_tap(tmp_path, corruption):
    log = tmp_path / 'units.log'
    valid = app004_tap()
    log.write_text(valid)
    assert runner.app004_units(log) is None
    first = f'ok 1 - {APP004_EXPECTED_NAMES[0]}'
    mutations = {
        'missing-case': valid.replace(first + '\n', ''),
        'duplicate-name': valid.replace(APP004_EXPECTED_NAMES[-1], APP004_EXPECTED_NAMES[0]),
        'foreign-name': valid.replace(APP004_EXPECTED_NAMES[-1], 'unrelated unit'),
        'extra-case': valid + 'ok 24 - unrelated unit\n',
        'wrong-tests': valid.replace('# tests 23', '# tests 22'),
        'wrong-pass': valid.replace('# pass 23', '# pass 22'),
        'failure': valid.replace('# fail 0', '# fail 1'),
        'cancelled': valid.replace('# cancelled 0', '# cancelled 1'),
        'skipped': valid.replace('# skipped 0', '# skipped 1'),
        'todo': valid.replace('# todo 0', '# todo 1'),
        'skip-directive': valid.replace(first, first + ' # SKIP unavailable'),
        'todo-directive': valid.replace(first, first + ' # TODO later'),
        'not-ok': valid + 'not ok 24 - unexpected failure\n',
        'bail-out': valid + 'Bail out! stopped\n',
        'duplicate-summary': valid + '# pass 23\n',
        'missing-summary': valid.replace('# skipped 0\n', ''),
    }
    log.write_text(mutations[corruption])
    with pytest.raises(ValueError):
        runner.app004_units(log)


@pytest.mark.parametrize('path,replacement', [
    ('qualificationToken', 'stale-fixture-token'), ('qualificationToken', ABSENT),
    ('negativeReload', False), ('negativeReload', 'true'),
    ('identityBoundary', {}), ('identityBoundary.id', 'some-other-assertion'),
    ('identityBoundary.verdict', 'passed'),
    *((f'identityBoundary.{flag}', False) for flag in
      ('attachmentAccepted', 'cardVisible', 'draftHeld', 'clickCompleted', 'attempted')),
    ('identityBoundary.clickCompleted', 1),
    ('identityBoundary.targetSessionId', ''), ('identityBoundary.targetSessionId', ABSENT),
    ('identityBoundary.selectedSessionId', 'wrong-target'),
    ('primary', {}), ('primary.name', 'Error'), ('primary.name', 'TimeoutError'),
    ('primary.code', 'OTHER_ERROR'), ('primary.boundary', 'later-focus-assertion'),
    ('primary.boundary', ABSENT), ('secondary', [{'name': 'Error', 'message': 'cleanup failed'}]),
    ('browserErrors', ABSENT), ('browserErrors', ['uncaught browser error']),
    ('fixtureExit', None), ('fixtureExit.code', 1), ('fixtureExit.code', False),
    ('fixtureExit.signal', 'SIGTERM'),
])
def test_app004_negative_requires_fresh_exact_genuine_click_boundary(tmp_path, path, replacement):
    outcome, _ = write_app004_witness(tmp_path)
    assert runner.app004_negative(tmp_path, 'fresh-fixture-token') is None
    change_app004_field(outcome, path, replacement)
    (tmp_path / 'outcome.json').write_text(json.dumps(outcome))
    with pytest.raises(ValueError):
        runner.app004_negative(tmp_path, 'fresh-fixture-token')


@pytest.mark.parametrize('path,replacement', [
    *((f'identityBoundary.{side}.{key}', ABSENT)
      for side in ('before', 'after') for key in ('document', 'timeOrigin', 'client')),
    *((f'identityBoundary.{side}.timeOrigin', value)
      for side in ('before', 'after') for value in (False, 0, -1, '2000')),
    *((f'identityBoundary.{side}.{key}', '')
      for side in ('before', 'after') for key in ('document', 'client')),
    ('identityBoundary.after.document', 'before-document'),
    ('identityBoundary.after.timeOrigin', 1000),
    ('identityBoundary.after', {'document': 'before-document', 'timeOrigin': 1000, 'client': 'other-client'}),
    ('identityBoundary.after.extra', 'not-a-marker'),
    ('identityBoundary.before.timeOrigin', float('nan')),
    ('identityBoundary.after.timeOrigin', float('inf')),
])
def test_app004_negative_requires_full_markers_and_both_reload_changes(tmp_path, path, replacement):
    outcome, _ = write_app004_witness(tmp_path)
    # A retained client alone does not invalidate a proven document reload.
    assert outcome['identityBoundary']['before']['client'] == outcome['identityBoundary']['after']['client']
    assert runner.app004_negative(tmp_path, 'fresh-fixture-token') is None
    change_app004_field(outcome, path, replacement)
    (tmp_path / 'outcome.json').write_text(json.dumps(outcome))
    with pytest.raises(ValueError):
        runner.app004_negative(tmp_path, 'fresh-fixture-token')


@pytest.mark.parametrize('corruption', [
    'empty', 'foreign-route', 'error', 'null-error', 'missing-completion', 'missing-drain',
    'missing-unregister', 'duplicate-start', 'duplicate-completion', 'completion-before-start',
    'wrong-id', 'boolean-id', 'missing-id', 'unknown-phase', 'reordered-phases',
    'missing-register', 'missing-gate', 'oversized',
])
def test_app004_negative_rejects_failed_or_unfinished_routes(tmp_path, corruption):
    _, routes = write_app004_witness(tmp_path)
    assert runner.app004_negative(tmp_path, 'fresh-fixture-token') is None
    if corruption == 'empty':
        routes = []
    elif corruption == 'foreign-route':
        routes[1]['route'] = 'later-navigation'
    elif corruption in ('error', 'null-error'):
        routes[3]['error'] = 'Route is already handled!' if corruption == 'error' else None
    elif corruption.startswith('missing-') and corruption != 'missing-id':
        phase = {'missing-completion': 'completed', 'missing-drain': 'drained',
                 'missing-unregister': 'unregistered', 'missing-register': 'registered',
                 'missing-gate': 'gate-released'}[corruption]
        routes = [row for row in routes if row['phase'] != phase]
    elif corruption == 'duplicate-start':
        routes.insert(2, dict(routes[1]))
    elif corruption == 'duplicate-completion':
        routes.insert(4, dict(routes[3]))
    elif corruption == 'completion-before-start':
        routes[1], routes[3] = routes[3], routes[1]
    elif corruption == 'wrong-id':
        routes[3]['id'] = 2
    elif corruption == 'boolean-id':
        routes[1]['id'] = True
    elif corruption == 'missing-id':
        del routes[1]['id']
    elif corruption == 'unknown-phase':
        routes[3]['phase'] = 'failed'
    elif corruption == 'reordered-phases':
        routes[2], routes[4] = routes[4], routes[2]
    elif corruption == 'oversized':
        routes *= 1667
    (tmp_path / 'route-lifecycle.json').write_text(json.dumps(routes))
    with pytest.raises(ValueError):
        runner.app004_negative(tmp_path, 'fresh-fixture-token')


@pytest.mark.parametrize('corruption', [
    'missing-outcome', 'missing-routes', 'invalid-json', 'outcome-list', 'proof-list', 'primary-list',
    'marker-list', 'routes-object', 'null-route', 'oversized-outcome', 'oversized-routes',
    'missing-log', 'stale-token',
])
def test_app004_verdict_rejects_missing_or_malformed_evidence(tmp_path, corruption):
    row, folder = prepare_app004_verdict(tmp_path)
    control = dict(row)
    runner.app004_verdict(control, folder)
    assert control['status'] == 'passed' and control['verdict'] == 'expected_identity_failure'
    negative = folder / 'negative'
    outcome_path, routes_path = negative / 'outcome.json', negative / 'route-lifecycle.json'
    outcome, routes = app004_witness()
    if corruption == 'missing-outcome':
        outcome_path.unlink()
    elif corruption == 'missing-routes':
        routes_path.unlink()
    elif corruption == 'invalid-json':
        outcome_path.write_text('{')
    elif corruption in ('outcome-list', 'proof-list', 'primary-list', 'marker-list'):
        if corruption == 'outcome-list':
            outcome = []
        else:
            change_app004_field(outcome, {'proof-list': 'identityBoundary', 'primary-list': 'primary',
                                         'marker-list': 'identityBoundary.before'}[corruption], [])
        outcome_path.write_text(json.dumps(outcome))
    elif corruption == 'routes-object':
        routes_path.write_text('{}')
    elif corruption == 'null-route':
        routes[1] = None
        routes_path.write_text(json.dumps(routes))
    elif corruption in ('oversized-outcome', 'oversized-routes'):
        path = outcome_path if corruption == 'oversized-outcome' else routes_path
        # Whitespace keeps the JSON valid: rejection must be the size limit.
        path.write_text(path.read_text() + ' ' * (1024 * 1024))
    elif corruption == 'missing-log':
        (folder / row['log']).unlink()
    elif corruption == 'stale-token':
        row['executionToken'] = 'different-run-token'
    runner.app004_verdict(row, folder)
    assert row['status'] == 'failed' and row['verdict'] == 'rejected'
    assert row['verdictReason']
    assert row['exitCode'] == 1 and row['reason'] is None
    assert 'evidenceSha256' not in row


@pytest.mark.parametrize('identity', ['units', 'reload-negative'])
def test_app004_verdict_retains_raw_exit_and_hashes_exact_inputs(tmp_path, identity):
    row, folder = prepare_app004_verdict(tmp_path, identity)
    raw = {key: row[key] for key in ('exitCode', 'reason', 'cleanup', 'executionToken', 'log')}
    runner.app004_verdict(row, folder)
    assert row['status'] == 'passed'
    assert row['verdict'] == ('exact_units' if identity == 'units' else 'expected_identity_failure')
    assert {key: row[key] for key in raw} == raw
    files = ([row['log']] if identity == 'units'
             else [row['log'], 'negative/outcome.json', 'negative/route-lifecycle.json'])
    assert row['evidenceSha256'] == {
        name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in files}


@pytest.mark.parametrize('identity,field,value', [
    *(('reload-negative', 'exitCode', code) for code in (0, 2, -15, None, True)),
    *(('units', 'exitCode', code) for code in (1, 7, -15, None, False)),
    *((identity, 'reason', reason) for identity in ('units', 'reload-negative')
      for reason in ('timeout', 'cancelled', 'FileNotFoundError')),
    *((identity, 'cleanup', cleanup) for identity in ('units', 'reload-negative')
      for cleanup in ({'remaining': [], 'terminated': 1},
                      {'remaining': [123], 'terminated': 0},
                      {'remaining': ['unknown'], 'terminated': 0, 'error': 'PermissionError'})),
])
def test_app004_verdict_never_waives_raw_failure_or_unexpected_green(tmp_path, identity, field, value):
    row, folder = prepare_app004_verdict(tmp_path, identity)
    control = dict(row)
    runner.app004_verdict(control, folder)
    assert control['status'] == 'passed'
    row[field] = value
    raw = json.dumps({key: row[key] for key in ('exitCode', 'reason', 'cleanup')}, sort_keys=True)
    runner.app004_verdict(row, folder)
    assert row['status'] == 'failed' and row['verdict'] == 'rejected'
    assert row['verdictReason']
    assert json.dumps({key: row[key] for key in ('exitCode', 'reason', 'cleanup')}, sort_keys=True) == raw
    assert 'evidenceSha256' not in row


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('normal_exit', [0, 7])
def test_app004_scheduler_is_opt_in_and_never_waives_normal_failure(tmp_path, monkeypatch, enabled, normal_exit):
    mock_app004_commands(monkeypatch)
    monkeypatch.setenv('APP004_QUALIFICATION_TOKEN', 'inherited-stale-token')
    rows = [scenario('normal', f'print("normal result");raise SystemExit({normal_exit})'),
            scenario('independent', 'print("independent result")')]
    code, report, output = execute(
        tmp_path, rows, budget=30, **({'app004_qualification': True} if enabled else {}))
    assert code == (1 if normal_exit else 0)
    assert report['complete'] and report['passed'] is (normal_exit == 0)
    assert [row['id'] for row in report['scenarios']] == ['normal', 'independent']
    assert [row['command'] for row in report['scenarios']] == [row['command'] for row in rows]
    assert [row['exitCode'] for row in report['scenarios']] == [normal_exit, 0]
    assert [row['status'] for row in report['scenarios']] == ['failed' if normal_exit else 'passed', 'passed']
    assert all(row['reason'] is None for row in report['scenarios'])
    assert 'normal result' in (output / 'normal.log').read_text()
    folder = output / 'app004-qualification'
    if not enabled:
        assert not folder.exists()
        return
    qualification = json.loads((folder / 'results.json').read_text())
    assert qualification['source'] == report['source']
    assert qualification['complete'] and qualification['passed']
    assert qualification['normalPassed'] is (normal_exit == 0)
    assert qualification['combinedPassed'] is (normal_exit == 0)
    units, negative = qualification['checks']
    assert [row['id'] for row in qualification['checks']] == ['units', 'reload-negative']
    assert [row['exitCode'] for row in qualification['checks']] == [0, 1]
    assert [row['verdict'] for row in qualification['checks']] == ['exact_units', 'expected_identity_failure']
    assert units['executionToken'] != negative['executionToken']
    assert all(row['executionToken'] != 'inherited-stale-token' for row in qualification['checks'])
    assert '# fixture-owner ' + units['executionToken'] in (folder / units['log']).read_text()
    assert json.loads((folder / 'negative/outcome.json').read_text())['qualificationToken'] == negative['executionToken']


def test_app004_initial_and_running_receipts_are_durable_before_each_launch(tmp_path, monkeypatch):
    mock_app004_commands(monkeypatch)
    original = subprocess.Popen
    snapshots = []
    def launch(*args, **kwargs):
        output = tmp_path / 'results'
        snapshots.append((
            json.loads((output / 'results.json').read_text()),
            json.loads((output / 'app004-qualification/results.json').read_text()),
            kwargs['env'][runner.OWNER_ENV],
        ))
        return original(*args, **kwargs)
    monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    code, _, _ = execute(tmp_path, [scenario('normal', 'pass')], app004_qualification=True, budget=30)
    assert code == 0 and len(snapshots) == 3
    normal, initial, _ = snapshots[0]
    assert normal['scenarios'][0]['status'] == 'running' and not normal['complete']
    assert initial['source'] == normal['source']
    assert not initial['complete'] and not initial['passed']
    assert [(row['status'], row['reason']) for row in initial['checks']] == [('unrun', 'not_started')] * 2
    assert [row['id'] for row in initial['checks']] == ['units', 'reload-negative']
    for index, (_, qualification, token) in enumerate(snapshots[1:]):
        completed_normal = snapshots[index + 1][0]
        assert completed_normal['complete'] and completed_normal['passed']
        assert qualification['checks'][index]['status'] == 'running'
        assert qualification['checks'][index]['executionToken'] == token
    assert snapshots[1][1]['checks'][1]['status'] == 'unrun'
    assert snapshots[2][1]['checks'][0]['status'] == 'passed'


@pytest.mark.parametrize('enabled', [False, True])
def test_app004_cli_initialize_saves_unrun_without_launching_commands(tmp_path, monkeypatch, enabled):
    (tmp_path / 'scripts').mkdir()
    script = tmp_path / 'scripts/browser_check_runner.py'
    shutil.copy(ROOT / 'scripts/browser_check_runner.py', script)
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'schemaVersion': 1, 'scenarios': [scenario('normal', 'pass')]}))
    monkeypatch.setattr(runner, '__file__', str(script))
    def source(command, **kwargs):
        assert kwargs['cwd'] == tmp_path
        return {('git', 'rev-parse', 'HEAD'): b'a' * 40,
                ('git', 'rev-parse', 'HEAD^{tree}'): b'b' * 40,
                ('git', 'diff', '--binary', 'HEAD'): b''}[tuple(command)]
    def forbidden(*args, **kwargs):
        pytest.fail('Initialization must not launch any normal or qualification command')
    monkeypatch.setattr(runner.subprocess, 'check_output', source)
    monkeypatch.setattr(runner.subprocess, 'Popen', forbidden)
    output = tmp_path / 'evidence'
    monkeypatch.setattr(sys, 'argv', [str(script), '--plan', str(plan), '--output', str(output),
                                    '--initialize', *(['--app004-qualification'] if enabled else [])])
    assert runner.main() == 0
    report = json.loads((output / 'results.json').read_text())
    assert not report['complete'] and not report['passed']
    assert report['scenarios'][0]['status'] == 'unrun'
    assert report['scenarios'][0]['reason'] == 'not_started'
    assert report['source']['planSha256'] == hashlib.sha256(plan.read_bytes()).hexdigest()
    qualification_path = output / 'app004-qualification/results.json'
    if enabled:
        assert json.loads(qualification_path.read_text()) == runner.initial_app004(report['source'])
    else:
        assert not qualification_path.exists()


@pytest.mark.parametrize('exhaust_after', ['normal', 'units'])
def test_app004_extras_share_original_deadline_and_require_remaining_budget(tmp_path, monkeypatch, exhaust_after):
    mock_app004_commands(monkeypatch)
    original_clock, retire = runner.time.monotonic, runner.retire_group
    offset, retired = [0], []
    monkeypatch.setattr(runner.time, 'monotonic', lambda: original_clock() + offset[0])
    def cleanup(*args):
        result = retire(*args)
        retired.append(args[1])
        if len(retired) == (1 if exhaust_after == 'normal' else 2):
            offset[0] += 9  # Less than either extra's two-second budget remains.
        return result
    monkeypatch.setattr(runner, 'retire_group', cleanup)
    code, report, output = execute(tmp_path, [scenario('normal', 'pass')], app004_qualification=True, budget=10)
    assert code == 1 and report['complete'] and report['passed']
    qualification = json.loads((output / 'app004-qualification/results.json').read_text())
    assert not qualification['complete'] and not qualification['passed']
    assert [row['status'] for row in qualification['checks']] == (
        ['unrun', 'unrun'] if exhaust_after == 'normal' else ['passed', 'unrun'])
    for row in qualification['checks']:
        if row['status'] == 'unrun':
            assert row['reason'] == 'budget_exhausted'
            assert 'executionToken' not in row and 'exitCode' not in row
            assert not (output / 'app004-qualification' / (row['id'] + '.log')).exists()
    assert len(retired) == (1 if exhaust_after == 'normal' else 2)


@pytest.mark.parametrize('cancel_at', ['normal', 'units'])
def test_app004_cancellation_reaps_current_and_keeps_extras_unrun(tmp_path, monkeypatch, cancel_at):
    cancel = 'import os,signal,time;os.kill(os.getppid(),signal.SIGTERM);time.sleep(60)'
    mock_app004_commands(monkeypatch, units_source=cancel if cancel_at == 'units' else None)
    code, report, output = execute(
        tmp_path, [scenario('normal', cancel if cancel_at == 'normal' else 'pass'), scenario('later', 'pass')],
        app004_qualification=True, budget=30)
    assert code == 1
    qualification = json.loads((output / 'app004-qualification/results.json').read_text())
    active = report['scenarios'][0] if cancel_at == 'normal' else qualification['checks'][0]
    assert active['status'] == 'failed' and active['reason'] == 'cancelled'
    assert active['cleanup']['remaining'] == []
    assert not qualification['complete'] and not qualification['passed']
    assert [row['status'] for row in qualification['checks']] == (
        ['unrun', 'unrun'] if cancel_at == 'normal' else ['failed', 'unrun'])
    if cancel_at == 'normal':
        assert report['scenarios'][1]['status'] == 'unrun' and not report['complete']
    else:
        assert report['complete'] and report['passed']
    assert qualification['checks'][1]['reason'] == 'cancelled'
    assert not (output / 'app004-qualification/reload-negative.log').exists()


@pytest.mark.parametrize('stage,remaining,terminated,reason', [
    ('normal', [], 1, 'unsafe_normal_cleanup'),
    ('normal', ['unknown'], 0, 'unsafe_cleanup'),
    ('units', ['unknown'], 0, 'unsafe_cleanup'),
])
def test_app004_unsafe_cleanup_cannot_launch_remaining_extras(tmp_path, monkeypatch, stage, remaining, terminated, reason):
    mock_app004_commands(monkeypatch)
    retire, retired = runner.retire_group, []
    def cleanup(*args):
        actual = retire(*args)
        assert actual == {'remaining': [], 'terminated': 0}
        retired.append(args[1])
        if len(retired) == (1 if stage == 'normal' else 2):
            # Inject an uncertain cleanup receipt only after real child cleanup.
            return {'remaining': remaining, 'terminated': terminated}
        return actual
    monkeypatch.setattr(runner, 'retire_group', cleanup)
    code, report, output = execute(tmp_path, [scenario('normal', 'pass')], app004_qualification=True, budget=30)
    assert code == 1
    assert report['complete'] and report['passed'] is (stage == 'units')
    qualification = json.loads((output / 'app004-qualification/results.json').read_text())
    assert not qualification['complete'] and not qualification['passed']
    assert [row['status'] for row in qualification['checks']] == (
        ['unrun', 'unrun'] if stage == 'normal' else ['failed', 'unrun'])
    for row in qualification['checks']:
        if row['status'] == 'unrun':
            assert row['reason'] == reason
            assert not (output / 'app004-qualification' / (row['id'] + '.log')).exists()
    assert len(retired) == (1 if stage == 'normal' else 2)


@pytest.mark.parametrize('timeout_at', ['units', 'reload-negative'])
def test_app004_extra_timeout_is_failure_not_expected_negative(tmp_path, monkeypatch, timeout_at):
    slow = 'import time;time.sleep(60)'
    mock_app004_commands(monkeypatch, units_source=slow if timeout_at == 'units' else None,
                         negative_source=slow if timeout_at == 'reload-negative' else None)
    initial = runner.initial_app004
    def short(provenance):
        report = initial(provenance)
        next(row for row in report['checks'] if row['id'] == timeout_at)['timeoutSeconds'] = .15
        return report
    monkeypatch.setattr(runner, 'initial_app004', short)
    code, report, output = execute(tmp_path, [scenario('normal', 'pass')], app004_qualification=True, budget=30)
    assert code == 1 and report['complete'] and report['passed']
    qualification = json.loads((output / 'app004-qualification/results.json').read_text())
    assert qualification['complete'] and not qualification['passed']
    row = next(row for row in qualification['checks'] if row['id'] == timeout_at)
    assert row['status'] == 'failed' and row['reason'] == 'timeout'
    assert row['verdict'] == 'rejected' and row['exitCode'] is None
    assert row['cleanup']['remaining'] == []
    other = next(row for row in qualification['checks'] if row['id'] != timeout_at)
    assert other['status'] == 'passed'


def bounded_execute(tmp_path, source, **kwargs):
    return execute(tmp_path, [scenario('pytest', source)], cwd=tmp_path,
                   log_limit=1024, interrupt_grace=.3, **kwargs)


@pytest.mark.parametrize('exit_code', [0, 1])
def test_python_bounded_capture_pass_or_failure_keeps_root_cwd_and_exit(tmp_path, exit_code):
    code, report, output = bounded_execute(tmp_path,
        f'import os;print(os.getcwd(),flush=True);raise SystemExit({exit_code})')
    row, = report['scenarios']
    assert code == exit_code and row['exitCode'] == exit_code
    assert row['status'] == ('passed' if exit_code == 0 else 'failed')
    assert row['reason'] is None and row['cleanup'] == {'remaining': [], 'terminated': 0}
    assert (output / 'pytest.log').read_text().strip() == str(tmp_path)
    assert row['logCapture']['eof'] and not row['logCapture']['truncated']


def test_python_bounded_capture_flood_drains_binary_chunks_without_unbounded_log(tmp_path):
    source = ('import os;os.write(1,b"HEAD"+bytes([255])+b"a"*250000);'
              'os.write(1,b"z"*250000+b"TAIL"+bytes([254]))')
    code, report, output = bounded_execute(tmp_path, source)
    capture = report['scenarios'][0]['logCapture']
    assert code == 0 and capture['bytesObserved'] == 500010
    assert capture['bytesRetained'] == 1024 and capture['truncated'] and capture['eof']
    assert capture['format'] == 'head-tail' and not capture['tailCheckpointMayLag']
    assert (output / 'pytest.log').read_bytes() == b'HEAD\xff' + b'a'*507 + b'z'*507 + b'TAIL\xfe'
    assert not (output / 'pytest.log.tail').exists()


def test_python_log_ring_retains_running_head_and_cursor_then_orders_tail(tmp_path):
    path = tmp_path / 'pytest.log'
    log = runner.BoundedLog(path, 10)
    try:
        log.append(b'0123456789AB')
        evidence = log.receipt()
        assert path.read_bytes() == b'01234'
        assert path.with_suffix('.log.tail').read_bytes() == b'AB789'
        assert evidence['bytesObserved'] == 12 and evidence['tailStart'] == 2
        assert evidence['format'] == 'head-ring-tail' and evidence['tailCheckpointMayLag']
    finally:
        log.close()
    assert path.read_bytes() == b'01234789AB'


def expire_after_progress(monkeypatch, marker):
    # No startup race: the child installs its handler before marking progress.
    original = runner.time.monotonic
    monkeypatch.setattr(runner.time, 'monotonic', lambda: original() + (100 if marker.exists() else 0))


@pytest.mark.parametrize('cancelled', [False, True])
def test_python_progress_then_interruption_preserves_timeout_even_exit_zero(tmp_path, monkeypatch, cancelled):
    ready = tmp_path / 'ready'
    if not cancelled:
        expire_after_progress(monkeypatch, ready)
    source = f'''
import os,signal,time
from pathlib import Path
def finish(*args):
    print("interrupt finalized",flush=True)
    raise SystemExit(0)
signal.signal(signal.SIGINT,finish)
print("test_one PASSED",flush=True)
Path({str(ready)!r}).touch()
{'os.kill(os.getppid(),signal.SIGTERM)' if cancelled else ''}
time.sleep(60)
'''
    code, report, output = bounded_execute(tmp_path, source, budget=10, case_timeout=10)
    row, = report['scenarios']
    assert code == 1 and report['complete'] and not report['passed']
    assert row['reason'] == ('cancelled' if cancelled else 'timeout')
    assert row['exitCode'] == 0 and row['interrupt']['sent']
    assert row['cleanup'] == {'remaining': [], 'terminated': 0}
    assert row['logCapture']['eof']
    assert 'test_one PASSED' in (output / 'pytest.log').read_text()
    assert 'interrupt finalized' in (output / 'pytest.log').read_text()


def test_python_ignored_interrupt_cleans_owned_descendant_not_unrelated(tmp_path, monkeypatch):
    ready = tmp_path / 'ready'
    expire_after_progress(monkeypatch, ready)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                                  start_new_session=True)
    source = f'''
import os,signal,subprocess,sys,time
from pathlib import Path
signal.signal(signal.SIGINT,signal.SIG_IGN)
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
Path({str(ready)!r}).write_text(str(p.pid))
print("before hang",flush=True)
time.sleep(60)
'''
    try:
        code, report, output = bounded_execute(tmp_path, source, budget=10, case_timeout=10)
        row, = report['scenarios']
        assert code == 1 and row['reason'] == 'timeout' and row['interrupt']['sent']
        assert row['cleanup']['remaining'] == [] and row['cleanup']['terminated'] >= 1
        assert unrelated.poll() is None
        child = Path('/proc') / ready.read_text() / 'stat'
        assert not child.exists() or child.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
        assert b'before hang' in (output / 'pytest.log').read_bytes()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_python_ownership_unreadable_remains_unknown_with_zero_exit(tmp_path, monkeypatch):
    retire = runner.retire_group
    def unavailable(*args, **kwargs):
        assert retire(*args, **kwargs) == {'remaining': [], 'terminated': 0}
        raise PermissionError('Synthetic unavailable ownership after child exit')
    monkeypatch.setattr(runner, 'retire_group', unavailable)
    code, report, _ = bounded_execute(tmp_path, 'print("finished")')
    assert code == 1 and report['scenarios'][0]['exitCode'] == 0
    assert report['scenarios'][0]['cleanup']['remaining'] == ['unknown']


def test_python_interrupt_never_signals_unreadable_or_reused_identity(monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(runner.os, 'pidfd_open', lambda pid: calls.append(pid))
    def unavailable(*args):
        raise PermissionError('No identity evidence')
    monkeypatch.setattr(runner, 'owned_members', unavailable)
    with pytest.raises(PermissionError):
        runner.interrupt_owned(SimpleNamespace(pid=321), 'token', {})
    monkeypatch.setattr(runner, 'owned_members', lambda *args: {})
    assert not runner.interrupt_owned(SimpleNamespace(pid=321), 'token', {})
    assert calls == []


@pytest.mark.parametrize('shape', ['missing', 'malformed', 'oversized', 'entities', 'empty', 'passed', 'failed'])
def test_python_junit_is_bounded_observation_not_an_invented_pass(tmp_path, shape):
    raw, kept = tmp_path / 'junit.pending.xml', tmp_path / 'junit.xml'
    contents = {
        'malformed': b'<testsuite>', 'oversized': b'x'*1025,
        'entities': b'<!DOCTYPE testsuite [<!ENTITY x "value">]><testsuite/>',
        'empty': b'<testsuite tests="0" failures="0" errors="0" skipped="0"/>',
        'passed': b'<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite>',
        'failed': b'<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase name="one"><failure/></testcase></testsuite>',
    }
    if shape != 'missing':
        raw.write_bytes(contents[shape])
    result = runner.junit_evidence(raw, kept, limit=1024)
    expected = {'entities': 'malformed', 'oversized': 'malformed', 'empty': 'parsed',
                'passed': 'parsed', 'failed': 'parsed'}.get(shape, shape)
    assert result['status'] == expected
    if expected != 'parsed':
        assert result['counts'] is None and not kept.exists()
    else:
        assert kept.read_bytes() == contents[shape]
        assert result['counts']['testcases'] == (0 if shape == 'empty' else 1)
        assert result['counts']['failure'] == int(shape == 'failed')


@pytest.mark.parametrize('payload', ['identity', 'diagnostic'])
def test_python_oversized_junit_retains_exact_xml_not_a_count_only_summary(tmp_path, payload):
    # Representative shape, not the unavailable original oversized CI XML.
    # CI's retained log contains a 5.3 MB parameterized name. Diagnostic bodies
    # can independently exceed the retention budget even with short identities.
    large = 'x' * 100_000
    name = large if payload == 'identity' else 'case[param]'
    diagnostic = large if payload == 'diagnostic' else 'original assertion'
    xml = (f'<testsuites><testsuite tests="2" failures="1" errors="2" skipped="1">'
           f'<testcase classname="tests.example" name="{name}"><failure message="failed">'
           f'{diagnostic}</failure><error message="teardown"/></testcase>'
           f'<testcase classname="tests.example" name="{name}"><skipped/><error/>'
           '</testcase></testsuite></testsuites>').encode()
    raw, kept = tmp_path / 'junit.pending.xml', tmp_path / 'junit.xml'
    raw.write_bytes(xml)
    result = runner.junit_evidence(raw, kept, limit=1024)
    assert result['status'] == 'parsed'
    assert result['counts'] == {'testcases': 2, 'failure': 1, 'error': 2, 'skipped': 1}
    compressed = tmp_path / 'junit.xml.gz'
    assert not kept.exists() and compressed.stat().st_size <= 1024
    assert gzip.decompress(compressed.read_bytes()) == raw.read_bytes() == xml
    assert result['file'] == compressed.name and result['encoding'] == 'gzip'
    assert result['contentComplete'] is True
    assert result['sourceBytes'] == len(xml)
    assert result['sourceSha256'] == hashlib.sha256(xml).hexdigest()
    assert result['sha256'] == hashlib.sha256(compressed.read_bytes()).hexdigest()


def test_python_oversized_junit_representative_identity_workload(tmp_path, monkeypatch):
    # This is synthetic, not reconstructed original CI XML. Preserve CI-shaped
    # identities while varying ordinary case count enough to cross the old cap.
    names = ['a' * 5_333_333, '#amp-one{' + '--large:a;' * 100_000]
    cases = [f'<testcase classname="tests.synthetic" name="{name}" time="0.01"/>'
             for name in names]
    cases.extend(f'<testcase classname="tests.synthetic.representative.parameters.module" name="test_case_{i:05d}" time="0.01"/>'
                 for i in range(20_000))
    cases.append('<testcase classname="tests.synthetic" name="failed"><failure message="assertion">'
                 'original diagnostic &amp; traceback</failure></testcase>')
    xml = ('<testsuites><testsuite tests="20003" failures="1" errors="0" skipped="0">'
           + ''.join(cases) + '</testsuite></testsuites>').encode()
    assert len(xml) > runner.PYTHON_JUNIT_BYTES
    raw = tmp_path / 'junit.pending.xml'
    raw.write_bytes(xml)
    read = runner.EvidenceFile.read
    sizes = []
    def bounded(file, size):
        sizes.append(size)
        assert size <= 65536
        return read(file, size)
    monkeypatch.setattr(runner.EvidenceFile, 'read', bounded)
    result = runner.junit_evidence(raw, tmp_path / 'junit.xml')
    assert result['status'] == 'parsed' and result['counts']['testcases'] == 20003
    assert result['counts']['failure'] == 1 and sizes
    kept = tmp_path / result['file']
    assert kept.stat().st_size < runner.PYTHON_JUNIT_BYTES
    assert gzip.decompress(kept.read_bytes()) == xml
    print(json.dumps({'syntheticSourceBytes': len(xml), 'retainedBytes': kept.stat().st_size,
                      'readCalls': len(sizes), 'largestRead': max(sizes)}))


@pytest.mark.parametrize('shape,status', [
    ('truncated', 'malformed'), ('trailing', 'malformed'), ('doctype', 'malformed'),
    ('entity', 'malformed'), ('nul', 'malformed'), ('namespace', 'invalid'),
    ('counter', 'invalid'), ('nested', 'invalid'), ('missing-name', 'invalid'),
    ('root-counter', 'invalid'), ('empty-suites', 'invalid'),
])
def test_python_oversized_junit_rejects_invalid_whole_document(tmp_path, shape, status):
    prefix = b'<?xml version="1.0"?>' + b' ' * 65500
    xml = b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite></testsuites>'
    if shape == 'truncated':
        xml = xml[:-5]
    elif shape == 'trailing':
        xml += b'garbage'
    elif shape == 'doctype':
        # Split across the parser's 64 KiB reads.
        prefix = b' ' * 65532 + b'<!DOCTYPE testsuites>'
    elif shape == 'entity':
        prefix += b'<!DOCTYPE testsuites [<!ENTITY x SYSTEM "file:///private">]>'
    elif shape == 'nul':
        xml += b'\0'
    elif shape == 'namespace':
        xml = xml.replace(b'<testsuites>', b'<testsuites xmlns="private">')
    elif shape == 'counter':
        xml = xml.replace(b'tests="1"', b'tests="2"')
    elif shape == 'nested':
        xml = xml.replace(b'<testcase name="one"/>', b'<system-out><testcase name="one"/></system-out>')
    elif shape == 'missing-name':
        xml = xml.replace(b' name="one"', b'')
    elif shape == 'root-counter':
        xml = xml.replace(b'<testsuites>', b'<testsuites tests="2">')
    elif shape == 'empty-suites':
        xml = b'<testsuites/>'
    raw = tmp_path / 'junit.pending.xml'
    raw.write_bytes(prefix + xml)
    result = runner.junit_evidence(raw, tmp_path / 'junit.xml', limit=1024)
    assert result['status'] == status and result['counts'] is None
    assert not list(tmp_path.glob('junit.xml*')) and not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('bound', ['scan', 'token', 'compressed', 'time'])
def test_python_oversized_junit_limits_do_not_publish_partial_counts(tmp_path, monkeypatch, bound):
    raw = tmp_path / 'junit.pending.xml'
    text = 'x' * 200_000
    if bound == 'compressed':
        text = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(3000))
    xml = ('<testsuite tests="1" failures="0" errors="0" skipped="0">'
           '<testcase name="one"><system-out>' + text + '</system-out></testcase></testsuite>')
    raw.write_text(xml)
    if bound == 'scan':
        monkeypatch.setattr(runner, 'PYTHON_JUNIT_SCAN_BYTES', 2000)
    elif bound == 'token':
        raw.write_text('<testsuite name="' + 'x' * 200_000)
        monkeypatch.setattr(runner, 'PYTHON_JUNIT_BYTES', 65536)
    elif bound == 'time':
        monkeypatch.setattr(runner, 'PYTHON_JUNIT_SCAN_SECONDS', 0)
    result = runner.junit_evidence(raw, tmp_path / 'junit.xml', limit=1024)
    assert result['status'] == 'oversized' and result['counts'] is None
    assert result['reason'] == {'scan': 'scan_limit', 'token': 'token_limit',
                                'compressed': 'compressed_limit', 'time': 'scan_timeout'}[bound]
    assert not list(tmp_path.glob('junit.xml*')) and not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('shape', ['symlink', 'dangling', 'hardlink', 'fifo'])
def test_python_oversized_junit_uses_secure_atomic_retention(tmp_path, shape):
    raw = tmp_path / 'junit.pending.xml'
    raw.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0">'
                   '<testcase name="one"><system-out>' + 'x' * 100_000 +
                   '</system-out></testcase></testsuite>')
    outside = tmp_path / 'outside'
    outside.write_text('untouched')
    plant_evidence_entry(tmp_path / 'junit.xml.gz', shape, outside)
    result = runner.junit_evidence(raw, tmp_path / 'junit.xml', limit=1024)
    assert result['status'] == 'retention_failed' and result['counts'] is None
    assert outside.read_text() == 'untouched' and not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('mutation', ['replace', 'grow', 'disk-error'])
def test_python_oversized_junit_source_and_retention_faults(tmp_path, monkeypatch, mutation):
    raw = tmp_path / 'junit.pending.xml'
    raw.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0">'
                   '<testcase name="one"><system-out>' + 'x' * 100_000 +
                   '</system-out></testcase></testsuite>')
    original = runner.EvidenceFile.read
    changed = False
    def read(file, size):
        nonlocal changed
        data = original(file, size)
        if not changed and mutation != 'disk-error':
            changed = True
            if mutation == 'replace':
                raw.unlink()
                raw.write_text('replacement')
            else:
                # Same size, different timestamp: valid bytes are not sufficient.
                os.utime(raw, ns=(1, 1))
        return data
    monkeypatch.setattr(runner.EvidenceFile, 'read', read)
    if mutation == 'disk-error':
        def fail(*args):
            raise OSError('ENOSPC')
        monkeypatch.setattr(runner.EvidenceFile, 'write', fail)
    result = runner.junit_evidence(raw, tmp_path / 'junit.xml', limit=1024)
    assert result['status'] == ('retention_failed' if mutation == 'disk-error' else 'unreadable')
    assert result['counts'] is None and not list(tmp_path.glob('junit.xml*'))
    assert not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('exit_code,reason,complete', [(0, None, True), (1, None, True),
                                                       (2, None, False), (0, 'timeout', False)])
def test_python_compressed_junit_does_not_change_execution_verdict(tmp_path, monkeypatch, exit_code, reason, complete):
    raw = tmp_path / 'junit.pending.xml'
    raw.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0">'
                   '<testcase name="one"><system-out>' + 'x' * 100_000 +
                   '</system-out></testcase></testsuite>')
    original = runner.junit_evidence
    monkeypatch.setattr(runner, 'junit_evidence', lambda *args, **kw: original(*args, limit=1024, **kw))
    report = {'complete': True, 'scenarios': [{'status': 'passed' if exit_code == 0 else 'failed',
              'exitCode': exit_code, 'reason': reason, 'cleanup': {'remaining': [], 'terminated': 0}}]}
    assert runner.finish_python_report(tmp_path, report) == (0 if exit_code == 0 and not reason else 1)
    assert report['pytestSessionComplete'] is complete
    assert report['junit']['status'] == 'parsed' and report['junit']['encoding'] == 'gzip'
    first = (tmp_path / 'junit.xml.gz').read_bytes()
    runner.finish_python_report(tmp_path, report)
    assert (tmp_path / 'junit.xml.gz').read_bytes() == first


def test_python_compressed_junit_is_stale_evidence_and_cannot_be_reset(tmp_path):
    (tmp_path / 'junit.xml.gz').write_bytes(b'old')
    with pytest.raises(ValueError, match='already exists'):
        runner.python_suite(tmp_path, tmp_path, {}, runner.time.monotonic() + 10, initialize=True)
    assert (tmp_path / 'junit.xml.gz').read_bytes() == b'old'


@pytest.mark.parametrize('padding', [0, 100_000])
def test_python_junit_unknown_encoding_saves_nonpassing_receipt(tmp_path, monkeypatch, padding):
    (tmp_path / 'junit.pending.xml').write_bytes(
        b'<?xml version="1.0" encoding="not-an-encoding"?>' + b' ' * padding +
        b'<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite>')
    original = runner.junit_evidence
    monkeypatch.setattr(runner, 'junit_evidence', lambda *a, **kw: original(*a, limit=1024, **kw))
    report = {'complete': True, 'scenarios': [{'status': 'passed', 'exitCode': 0, 'reason': None,
              'cleanup': {'remaining': [], 'terminated': 0}}]}
    assert runner.finish_python_report(tmp_path, report) == 1
    saved = runner.read_python_report(tmp_path)
    assert saved['junit'] == {'status': 'malformed', 'counts': None}
    assert saved['pytestSessionComplete'] and not saved['passed']
    assert not list(tmp_path.glob('junit.xml*'))


@pytest.mark.parametrize('compressed', [False, True])
def test_python_junit_refuses_conflicting_retained_representation(tmp_path, compressed):
    raw = tmp_path / 'junit.pending.xml'
    raw.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one">'
                   '<system-out>' + 'x' * (100_000 if compressed else 0) +
                   '</system-out></testcase></testsuite>')
    previous = tmp_path / ('junit.xml' if compressed else 'junit.xml.gz')
    previous.write_bytes(b'prior exact evidence')
    result = runner.junit_evidence(raw, tmp_path / 'junit.xml', limit=1024)
    assert result['status'] == 'retention_failed' and result['counts'] is None
    assert previous.read_bytes() == b'prior exact evidence'
    assert len(list(tmp_path.glob('junit.xml*'))) == 1


@pytest.mark.parametrize('excess', [0, 1])
def test_python_junit_scan_limit_exact_boundary(tmp_path, monkeypatch, excess):
    raw = tmp_path / 'junit.pending.xml'
    xml = (b'<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one">'
           b'<system-out>' + b'x' * 100_000 + b'</system-out></testcase></testsuite>')
    raw.write_bytes(xml)
    monkeypatch.setattr(runner, 'PYTHON_JUNIT_SCAN_BYTES', len(xml) - excess)
    result = runner.junit_evidence(raw, tmp_path / 'junit.xml', limit=1024)
    assert result['status'] == ('oversized' if excess else 'parsed')
    assert (tmp_path / 'junit.xml.gz').exists() is (not excess)


@pytest.mark.parametrize('token', ['<!--', '<?long ', '<testsuite name="'])
def test_python_junit_unfinished_tokens_are_bounded(tmp_path, monkeypatch, token):
    (tmp_path / 'junit.pending.xml').write_text(token + 'x' * 200_000)
    monkeypatch.setattr(runner, 'PYTHON_JUNIT_BYTES', 65536)
    result = runner.junit_evidence(tmp_path / 'junit.pending.xml', tmp_path / 'junit.xml', limit=1024)
    assert result['status'] == 'oversized' and result['reason'] == 'token_limit'
    assert result['counts'] is None and not list(tmp_path.glob('junit.xml*'))


def test_python_tiny_real_pytest_oversized_emission_keeps_failure_and_identities(tmp_path):
    tests = tmp_path / 'test_emission.py'
    tests.write_text('''import pytest
@pytest.mark.parametrize("value", [1, 2], ids=["a"*5333333, "b"*1000000])
def test_large_identity(value):
    assert value
def test_failure():
    raise AssertionError("diagnostic-"*250000)
''')
    output = tmp_path / 'results'
    row = {'id': 'pytest', 'command': [sys.executable, '-u', '-m', 'pytest', '-q', '--tb=short',
           '-c', '/dev/null', str(tests), '--junitxml=output/python-checks/junit.pending.xml'],
           'env': {'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}}
    code, report, _ = execute(tmp_path, [row], budget=30, case_timeout=30, cwd=tmp_path,
                             log_limit=16384, junit_output=output / 'junit.pending.xml')
    raw = (output / 'junit.pending.xml').read_bytes()
    assert len(raw) > runner.PYTHON_JUNIT_BYTES
    assert code == 1 and runner.finish_python_report(output, report) == 1
    assert report['scenarios'][0]['exitCode'] == 1 and report['pytestSessionComplete']
    assert report['scenarios'][0]['cleanup'] == {'remaining': [], 'terminated': 0}
    assert not report['passed'] and report['junit']['status'] == 'parsed'
    assert report['junit']['counts'] == {'testcases': 3, 'failure': 1, 'error': 0, 'skipped': 0}
    assert gzip.decompress((output / 'junit.xml.gz').read_bytes()) == raw
    tree = runner.ET.fromstring(raw)
    names = [case.get('name') for case in tree.iter('testcase')]
    assert names == ['test_large_identity[' + 'a'*5333333 + ']',
                     'test_large_identity[' + 'b'*1000000 + ']', 'test_failure']
    assert 'diagnostic-' in tree.find('.//failure').text


@pytest.mark.parametrize('shape', ['missing', 'malformed', 'oversized', 'empty'])
def test_python_exit_zero_cannot_invent_junit_success(tmp_path, monkeypatch, shape):
    monkeypatch.setattr(runner, 'junit_evidence', lambda *args, **kwargs: {
        'status': 'parsed' if shape == 'empty' else shape,
        'counts': {'testcases': 0, 'failure': 0, 'error': 0} if shape == 'empty' else None})
    report = {'passed': True, 'complete': True, 'scenarios': [
        {'status': 'passed', 'exitCode': 0, 'reason': None, 'cleanup': {'remaining': [], 'terminated': 0}}]}
    assert runner.finish_python_report(tmp_path, report) == 1
    assert report['pytestSessionComplete'] and not report['passed']


def test_python_early_preparation_budget_exhaustion_never_launches(tmp_path, monkeypatch):
    output = tmp_path / 'output'
    provenance = {'revision': 'a'*40}
    deadline = runner.time.monotonic() + 10
    assert runner.python_suite(tmp_path, output, provenance, deadline, initialize=True) == 0
    monkeypatch.setattr(runner.time, 'monotonic', lambda: deadline + 1)
    def forbidden(*args, **kwargs):
        pytest.fail('Preparation consumed the original budget; no pytest launch')
    monkeypatch.setattr(runner.subprocess, 'Popen', forbidden)
    assert runner.python_suite(tmp_path, output, provenance, deadline) == 1
    report = json.loads((output / 'results.json').read_text())
    assert report['scenarios'][0]['status'] == 'unrun'
    assert report['scenarios'][0]['reason'] == 'budget_exhausted_before_pytest'
    assert report['pytestSessionStatus'] == 'not_started'
    assert not report['complete'] and not report['pytestSessionComplete'] and not report['passed']
    assert report['junit'] == {'status': 'missing', 'counts': None}
    with pytest.raises(ValueError, match='no replay'):
        # A future deadline must not be used to replay a settled preflight.
        runner.python_suite(tmp_path, output, provenance, deadline)


@pytest.mark.parametrize('state', ['unrun', 'running'])
def test_python_finalize_preparation_failure_or_lost_runner_without_replay(tmp_path, monkeypatch, state):
    output = tmp_path / 'output'
    deadline = runner.time.monotonic() + 60
    runner.python_suite(tmp_path, output, {}, deadline, initialize=True)
    report = json.loads((output / 'results.json').read_text())
    report['scenarios'][0]['status'] = state
    runner.save(output, report)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Finalize cannot execute'))
    assert runner.python_suite(tmp_path, output, {}, deadline, finalize=True) == 1
    report = json.loads((output / 'results.json').read_text())
    assert report['scenarios'][0]['reason'] == ('preparation_not_completed' if state == 'unrun' else 'execution_outcome_unknown')
    assert not report['pytestSessionComplete'] and not report['passed']
    if state == 'running':
        assert report['scenarios'][0]['cleanup']['remaining'] == ['unknown']


def test_python_full_mode_requires_original_source_deadline_and_no_replay(tmp_path, monkeypatch):
    output = tmp_path / 'output'
    deadline = runner.time.monotonic() + 60
    runner.python_suite(tmp_path, output, {'revision': 'original'}, deadline, initialize=True)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('No execution in refusal controls'))
    with pytest.raises(ValueError, match='already exists'):
        runner.python_suite(tmp_path, output, {'revision': 'original'}, deadline, initialize=True)
    for source, changed_deadline in [({'revision': 'other'}, deadline), ({'revision': 'original'}, deadline + 1)]:
        with pytest.raises(ValueError, match='changed'):
            runner.python_suite(tmp_path, output, source, changed_deadline)


@pytest.mark.parametrize('exit_code', [0, 1])
def test_python_profile_keeps_initial_deadline_and_independent_completion_fields(tmp_path, monkeypatch, exit_code):
    output = tmp_path / 'output'
    deadline = runner.time.monotonic() + 10
    xml = f'<testsuite tests="1" failures="{exit_code}" errors="0" skipped="0"><testcase name="one">' + ('<failure/>' if exit_code else '') + '</testcase></testsuite>'
    command = [sys.executable, '-c', 'import sys;from pathlib import Path;'
               f'Path(sys.argv[1].split("=",1)[1]).write_text({xml!r});'
               f'print("one test finished",flush=True);raise SystemExit({exit_code})',
               '--junitxml=output/python-checks/junit.pending.xml']
    monkeypatch.setattr(runner, 'PYTHON_COMMAND', command)
    save = runner.save
    checkpoints = []
    def record(folder, report):
        checkpoints.append(json.loads(json.dumps(report)))
        save(folder, report)
    monkeypatch.setattr(runner, 'save', record)
    runner.python_suite(tmp_path, output, {}, deadline, initialize=True)
    assert runner.python_suite(tmp_path, output, {}, deadline) == exit_code
    report = json.loads((output / 'results.json').read_text())
    assert report['deadlineMonotonic'] == deadline
    assert report['complete'] and report['pytestSessionComplete']
    assert report['pytestSessionStatus'] == 'completed'
    assert report['scenarioListComplete']
    assert report['passed'] is (exit_code == 0)
    assert report['junit']['counts'] == {'testcases': 1, 'failure': exit_code, 'error': 0, 'skipped': 0}
    assert report['scenarios'][0]['command'] == command
    assert report['scenarios'][0]['executedCommand'][-1].startswith('--junitxml=/proc/self/fd/')
    assert report['scenarios'][0]['junitFileIdentity'] == [
        (output / 'junit.pending.xml').stat().st_dev, (output / 'junit.pending.xml').stat().st_ino]
    assert all(not r['passed'] for r in checkpoints if r['junit']['status'] == 'not_started')
    assert runner.python_suite(tmp_path, output, {}, deadline, finalize=True) == exit_code
    with pytest.raises(ValueError, match='no replay'):
        runner.python_suite(tmp_path, output, {}, deadline)


@pytest.mark.parametrize('name', ['pytest.log', 'pytest.log.tail', 'junit.pending.xml', 'junit.xml'])
def test_python_initialize_refuses_stale_output_without_receipt(tmp_path, name):
    (tmp_path / name).write_text('retained old evidence')
    with pytest.raises(ValueError, match='already exists'):
        runner.python_suite(tmp_path, tmp_path, {}, runner.time.monotonic() + 10, initialize=True)
    assert (tmp_path / name).read_text() == 'retained old evidence'
    assert not (tmp_path / 'results.json').exists()


def test_python_tiny_real_pytest_interrupt_retains_partial_xml_not_session_completion(tmp_path, monkeypatch):
    ready = tmp_path / 'ready'
    expire_after_progress(monkeypatch, ready)
    tests = tmp_path / 'test_tiny.py'
    tests.write_text(f'''from pathlib import Path
import time
def test_first():
    assert True
def test_hangs():
    Path({str(ready)!r}).touch()
    time.sleep(60)
''')
    output = tmp_path / 'results'
    row = {'id': 'pytest', 'command': [sys.executable, '-u', '-m', 'pytest', '-v', '--tb=short',
           '-c', '/dev/null', str(tests), '--junitxml=output/python-checks/junit.pending.xml'],
           'env': {'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}}
    code, report, output = execute(tmp_path, [row], budget=30, case_timeout=30,
                                  cwd=tmp_path, log_limit=16384, interrupt_grace=3,
                                  junit_output=output / 'junit.pending.xml')
    assert code == 1 and report['complete'] and report['scenarios'][0]['reason'] == 'timeout'
    assert runner.finish_python_report(output, report) == 1
    assert not report['pytestSessionComplete'] and not report['passed']
    # Pytest may retain the interrupted setup-only reporter without a name/count.
    # Keep that bounded XML, but never turn partial coverage into invented counts.
    assert report['junit']['status'] in {'parsed', 'invalid'}
    if report['junit']['status'] == 'parsed':
        assert report['junit']['counts']['testcases'] >= 1
    else:
        assert report['junit']['reason'] in {'case_identity_missing', 'counter_mismatch'}
        assert report['junit']['counts'] is None
    assert report['pytestSessionStatus'] == 'incomplete'
    assert b'test_first PASSED' in (output / 'pytest.log').read_bytes()


def plant_evidence_entry(path, shape, outside):
    if shape == 'symlink':
        path.symlink_to(outside)
    elif shape == 'dangling':
        path.symlink_to(outside.with_name('missing-target'))
    elif shape == 'hardlink':
        os.link(outside, path)
    else:
        assert shape == 'fifo'
        os.mkfifo(path)


@pytest.mark.parametrize('shape', ['symlink', 'dangling', 'hardlink', 'fifo'])
@pytest.mark.parametrize('reader', ['junit', 'results'])
def test_python_evidence_reads_refuse_special_entries_without_blocking(tmp_path, shape, reader):
    output = tmp_path / 'evidence'
    output.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'private bytes never read')
    name = 'junit.pending.xml' if reader == 'junit' else 'results.json'
    plant_evidence_entry(output / name, shape, outside)
    if reader == 'junit':
        result = runner.junit_evidence(output / name, output / 'junit.xml')
        assert result['status'] == 'unreadable' and result['counts'] is None
    else:
        with pytest.raises(OSError):
            runner.read_python_report(output)
    assert outside.read_bytes() == b'private bytes never read'
    assert not (tmp_path / 'missing-target').exists()


@pytest.mark.parametrize('shape', ['symlink', 'dangling', 'hardlink', 'fifo'])
@pytest.mark.parametrize('writer', ['results', 'junit', 'head', 'tail'])
def test_python_evidence_writers_never_clobber_external_or_special_entries(tmp_path, shape, writer):
    output = tmp_path / 'evidence'
    output.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'untouched external bytes')
    name = {'results': 'results.json', 'junit': 'junit.xml', 'head': 'pytest.log', 'tail': 'pytest.log.tail'}[writer]
    plant_evidence_entry(output / name, shape, outside)
    if writer == 'results':
        with pytest.raises(OSError):
            runner.save(output, {'passed': False})
    elif writer == 'junit':
        (output / 'junit.pending.xml').write_bytes(b'<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite>')
        result = runner.junit_evidence(output / 'junit.pending.xml', output / name)
        assert result['status'] == 'retention_failed' and result['counts'] is None
    else:
        with pytest.raises(OSError):
            runner.BoundedLog(output / 'pytest.log', 1024)
    assert outside.read_bytes() == b'untouched external bytes'
    assert not (tmp_path / 'missing-target').exists()
    assert not list(output.glob('.*.tmp'))


@pytest.mark.parametrize('shape', ['symlink', 'dangling', 'hardlink', 'fifo'])
def test_python_report_does_not_use_predictable_temporary_entry(tmp_path, shape):
    outside = tmp_path / 'outside'
    outside.write_bytes(b'retained')
    output = tmp_path / 'evidence'
    output.mkdir()
    plant_evidence_entry(output / 'results.tmp', shape, outside)
    runner.save(output, {'passed': False})
    assert json.loads((output / 'results.json').read_text()) == {'passed': False}
    assert outside.read_bytes() == b'retained'
    assert not (tmp_path / 'missing-target').exists()
    assert not list(output.glob('.*.tmp'))


@pytest.mark.parametrize('shape', ['symlink', 'hardlink'])
def test_python_report_rechecks_destination_before_atomic_publish(tmp_path, monkeypatch, shape):
    output = tmp_path / 'evidence'
    output.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'retained')
    runner.save(output, {'passed': False})
    original = runner.EvidenceFile.write
    def replace_after_open(file, data):
        size = original(file, data)
        (output / 'results.json').unlink()
        plant_evidence_entry(output / 'results.json', shape, outside)
        return size
    monkeypatch.setattr(runner.EvidenceFile, 'write', replace_after_open)
    with pytest.raises(OSError):
        runner.save(output, {'passed': True})
    assert outside.read_bytes() == b'retained'
    assert not list(output.glob('.*.tmp'))


def test_python_evidence_read_rechecks_identity_after_open(tmp_path, monkeypatch):
    raw = tmp_path / 'junit.pending.xml'
    raw.write_bytes(b'<testsuite tests="0" failures="0" errors="0" skipped="0"/>')
    outside = tmp_path / 'outside'
    outside.write_bytes(b'untouched')
    read = runner.EvidenceFile.read
    def replace_after_read(file, size):
        data = read(file, size)
        raw.unlink()
        raw.symlink_to(outside)
        return data
    monkeypatch.setattr(runner.EvidenceFile, 'read', replace_after_read)
    result = runner.junit_evidence(raw, tmp_path / 'junit.xml')
    assert result['status'] == 'unreadable' and result['counts'] is None
    assert outside.read_bytes() == b'untouched'
    assert not (tmp_path / 'junit.xml').exists()


def test_python_atomic_publication_refuses_exclusive_temp_collision(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(runner.uuid, 'uuid4', lambda: SimpleNamespace(hex='collision'))
    outside = tmp_path / 'outside'
    outside.write_bytes(b'untouched')
    (tmp_path / '.results.json.collision.tmp').symlink_to(outside)
    with pytest.raises(OSError):
        runner.save(tmp_path, {'passed': False})
    assert outside.read_bytes() == b'untouched'
    assert not (tmp_path / 'results.json').exists()


@pytest.mark.parametrize('name', ['results.json', 'pytest.log', 'pytest.log.tail', 'junit.pending.xml', 'junit.xml'])
def test_python_initialize_rejects_dangling_evidence(tmp_path, name):
    (tmp_path / name).symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError, match='already exists'):
        runner.python_suite(tmp_path, tmp_path, {}, runner.time.monotonic() + 10, initialize=True)
    assert not (tmp_path / 'missing').exists()


@pytest.mark.parametrize('name', ['results.json', 'pytest.log', 'pytest.log.tail', 'junit.pending.xml'])
@pytest.mark.parametrize('shape', ['symlink', 'hardlink', 'fifo'])
def test_python_replacement_after_initialization_refuses_before_launch(tmp_path, monkeypatch, name, shape):
    output = tmp_path / 'evidence'
    deadline = runner.time.monotonic() + 10
    runner.python_suite(tmp_path, output, {}, deadline, initialize=True)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'do not overwrite')
    path = output / name
    if path.exists():
        path.unlink()
    plant_evidence_entry(path, shape, outside)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Unsafe evidence must not launch pytest'))
    if name == 'results.json':
        with pytest.raises(OSError):
            runner.python_suite(tmp_path, output, {}, deadline)
    else:
        assert runner.python_suite(tmp_path, output, {}, deadline) == 1
        assert not runner.read_python_report(output)['passed']
    assert outside.read_bytes() == b'do not overwrite'


def test_python_evidence_directory_symlink_cannot_redirect_creation(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / 'redirect').symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        runner.python_suite(tmp_path, tmp_path / 'redirect' / 'new', {},
                            runner.time.monotonic() + 10, initialize=True)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('entry', ['pytest.log', 'pytest.log.tail'])
def test_python_open_log_identity_replacement_is_capture_failure_not_external_write(tmp_path, entry):
    outside = tmp_path / 'outside'
    outside.write_bytes(b'retained')
    output = tmp_path / 'evidence'
    output.mkdir()
    log = runner.BoundedLog(output / 'pytest.log', 10)
    try:
        (output / entry).unlink()
        (output / entry).symlink_to(outside)
        with pytest.raises(OSError):
            log.append(b'0123456789')
        log.append(b'discarded now')
        assert log.receipt()['captureError']
        assert log.receipt()['bytesRetained'] is None
    finally:
        log.close()
    assert outside.read_bytes() == b'retained'


def test_python_junit_expected_writer_identity_cannot_be_replaced(tmp_path):
    raw = tmp_path / 'junit.pending.xml'
    owned = runner.EvidenceFile(raw, create=True)
    expected = list(owned.identity)
    try:
        raw.unlink()
        raw.write_bytes(b'<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="other"/></testsuite>')
        result = runner.junit_evidence(raw, tmp_path / 'junit.xml', expected=expected)
        assert result['status'] == 'unreadable' and result['counts'] is None
        assert not (tmp_path / 'junit.xml').exists()
    finally:
        owned.close()


def test_python_junit_child_uses_owned_fd_after_path_replacement(tmp_path, monkeypatch):
    output = tmp_path / 'evidence'
    raw = output / 'junit.pending.xml'
    outside = tmp_path / 'outside'
    outside.write_bytes(b'external bytes')
    xml = '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite>'
    source = ('import sys;from pathlib import Path;'
              f'p=Path({str(raw)!r});p.unlink();p.symlink_to({str(outside)!r});'
              f'Path(sys.argv[1].split("=",1)[1]).write_text({xml!r})')
    monkeypatch.setattr(runner, 'PYTHON_COMMAND', [sys.executable, '-c', source,
        '--junitxml=output/python-checks/junit.pending.xml'])
    deadline = runner.time.monotonic() + 10
    runner.python_suite(tmp_path, output, {}, deadline, initialize=True)
    assert runner.python_suite(tmp_path, output, {}, deadline) == 1
    report = runner.read_python_report(output)
    assert report['scenarios'][0]['exitCode'] == 0
    assert report['scenarios'][0]['reason'] == 'junit_identity_changed'
    assert report['junit']['status'] == 'unreadable' and report['junit']['counts'] is None
    assert outside.read_bytes() == b'external bytes'


@pytest.mark.parametrize('child_exits', [False, True])
def test_python_capture_disk_error_still_drains_and_retires_owned_child(tmp_path, monkeypatch, child_exits):
    pid = tmp_path / 'owned.pid'
    write = runner.EvidenceFile.write
    failed = []
    def disk_full(file, data):
        if file.name == 'pytest.log':
            failed.append(True)
            raise OSError('Synthetic ENOSPC')
        return write(file, data)
    monkeypatch.setattr(runner.EvidenceFile, 'write', disk_full)
    source = f'''
import os,signal,time
from pathlib import Path
signal.signal(signal.SIGINT,signal.SIG_IGN)
Path({str(pid)!r}).write_text(str(os.getpid()))
os.write(1,b"progress")
{'raise SystemExit(0)' if child_exits else 'while True: os.write(1,b"x"*65536)'}
'''
    code, report, _ = bounded_execute(tmp_path, source, budget=5, case_timeout=5)
    row, = report['scenarios']
    assert code == 1 and row['status'] == 'failed'
    assert row['reason'] == 'log_capture_failed'
    assert row['logError'] == 'OSError' and row['logCapture']['captureError'] == 'OSError'
    assert row['logCapture']['bytesRetained'] is None and len(failed) == 1
    assert row['cleanup']['remaining'] == []
    if child_exits:
        assert row['exitCode'] == 0
    child = Path('/proc') / pid.read_text() / 'stat'
    assert not child.exists() or child.read_text().rsplit(')', 1)[1].split()[0] == 'Z'


def test_python_cleanup_drain_error_does_not_skip_available_identity_inspection(monkeypatch):
    inspections = []
    monkeypatch.setattr(runner, 'owned_members', lambda *args, **kwargs: inspections.append(True) or {})
    def failed_drain():
        raise OSError('Capture failed')
    cleanup = runner.retire_group(-1, 'synthetic', drain=failed_drain)
    assert inspections
    assert cleanup == {'remaining': [], 'terminated': 0, 'drainError': 'OSError'}


@pytest.mark.parametrize('xml', [
    '<testsuite tests="2" failures="1" errors="0" skipped="0"><testcase name="one"/></testsuite>',
    '<testsuite tests="1" failures="0" errors="1" skipped="0"><testcase name="one"/></testsuite>',
    '<testsuite tests="-1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite>',
    '<testsuite tests="1" failures="-1" errors="0" skipped="0"><testcase name="one"/></testsuite>',
    '<testsuite tests="1" failures="nan" errors="0" skipped="0"><testcase name="one"/></testsuite>',
    '<testsuite tests="1" failures="0" errors="0"><testcase name="one"/></testsuite>',
    '<testsuite><testcase name="one"/></testsuite>',
    '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase/></testsuite>',
    '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"><failure/></testcase></testsuite>',
    '<testsuite tests="1" failures="0" errors="0" skipped="1"><testcase name="one"/></testsuite>',
    '<testsuite tests="1" failures="0" errors="0" skipped="0"><wrapper><testcase name="one"/></wrapper></testsuite>',
    '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"><testcase name="nested"/></testcase></testsuite>',
    '<testsuites tests="2"><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite></testsuites>',
    '<testsuites><testsuites/></testsuites>',
])
def test_python_exit_zero_with_contradictory_junit_cannot_pass(tmp_path, xml):
    (tmp_path / 'junit.pending.xml').write_text(xml)
    report = {'passed': True, 'complete': True, 'scenarios': [
        {'status': 'passed', 'exitCode': 0, 'reason': None, 'cleanup': {'remaining': [], 'terminated': 0}}]}
    assert runner.finish_python_report(tmp_path, report) == 1
    assert report['junit']['status'] == 'invalid' and report['junit']['counts'] is None
    assert report['pytestSessionComplete'] and not report['passed']
    assert (tmp_path / 'junit.xml').read_text() == xml


@pytest.mark.parametrize('outcomes,counts,exit_code', [
    ('', {'testcases': 1, 'failure': 0, 'error': 0, 'skipped': 0}, 0),
    ('<skipped type="pytest.skip"/>', {'testcases': 1, 'failure': 0, 'error': 0, 'skipped': 1}, 0),
    ('<skipped type="pytest.xfail"/>', {'testcases': 1, 'failure': 0, 'error': 0, 'skipped': 1}, 0),
    ('<error/><error/>', {'testcases': 1, 'failure': 0, 'error': 2, 'skipped': 0}, 1),
    ('<skipped/><error/>', {'testcases': 1, 'failure': 0, 'error': 1, 'skipped': 1}, 1),
])
def test_python_junit_pytest_properties_skips_and_multiple_error_reports(tmp_path, outcomes, counts, exit_code):
    # Matches pytest 9.1.1 LogXML's flat testsuites/testsuite/case form. Failure
    # counts are outcome children, which can exceed testcase count on teardown.
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" tests="1"
 failures="{counts['failure']}" errors="{counts['error']}" skipped="{counts['skipped']}" time="0.1">
 <properties><property name="platform" value="fixture"/></properties>
 <testcase classname="test_example" name="one" time="0.1"><properties><property name="x" value="y"/></properties>{outcomes}<system-out>captured</system-out></testcase>
</testsuite></testsuites>'''
    (tmp_path / 'junit.pending.xml').write_text(xml)
    report = {'passed': exit_code == 0, 'complete': True, 'scenarios': [
        {'status': 'passed' if exit_code == 0 else 'failed', 'exitCode': exit_code,
         'reason': None, 'cleanup': {'remaining': [], 'terminated': 0}}]}
    assert runner.finish_python_report(tmp_path, report) == exit_code
    assert report['junit']['status'] == 'parsed' and report['junit']['counts'] == counts
    assert report['pytestSessionComplete'] and report['passed'] is (exit_code == 0)


def test_python_nested_owner_lineage_retains_ancestors_without_reusing_local_token():
    outer = runner.child_ownership({}, '1' * 32)
    inner = runner.child_ownership(outer, '2' * 32)
    deepest = runner.child_ownership(inner, '3' * 32)
    assert outer == {runner.OWNER_ENV: '1' * 32, runner.ANCESTOR_ENV: ''}
    assert inner == {runner.OWNER_ENV: '2' * 32, runner.ANCESTOR_ENV: '1' * 32}
    assert deepest == {runner.OWNER_ENV: '3' * 32,
                       runner.ANCESTOR_ENV: '1' * 32 + ',' + '2' * 32}
    encoded = lambda env: b'\0'.join((key + '=' + value).encode() for key, value in env.items())
    assert runner.environment_owns(encoded(deepest), '1' * 32)
    assert runner.environment_owns(encoded(deepest), '2' * 32)
    assert runner.environment_owns(encoded(deepest), '3' * 32)
    assert not runner.environment_owns(encoded(outer), '2' * 32)
    assert not runner.environment_owns(encoded(inner), '3' * 32)
    sibling = runner.child_ownership(outer, '4' * 32)
    assert not runner.environment_owns(encoded(sibling), '2' * 32)
    assert runner.owner_lineage({runner.OWNER_ENV: '1' * 32}) == ['1' * 32]  # Existing direct tag.
    assert not runner.environment_owns(b'UNRELATED=value\0', '1' * 32)


def test_python_nested_owner_limit_refuses_without_truncating_outer_owner():
    environment = {}
    for index in range(runner.MAX_OWNER_ANCESTORS + 1):
        environment = runner.child_ownership(environment, f'{index:032x}')
    assert len(environment[runner.ANCESTOR_ENV]) == runner.MAX_OWNER_ANCESTORS * 33 - 1
    assert runner.owner_lineage(environment)[0] == '0' * 32
    before = dict(environment)
    with pytest.raises(OSError, match='limit reached'):
        runner.child_ownership(environment, 'f' * 32)
    assert environment == before
    with pytest.raises(OSError, match='fresh local'):
        runner.child_ownership({runner.OWNER_ENV: 'f' * 32}, 'f' * 32)


@pytest.mark.parametrize('shape', [
    'oversized', 'bad-token', 'empty-token', 'duplicate', 'self-ancestor',
    'missing-owner', 'bad-owner', 'empty-owner', 'non-ascii',
])
def test_python_nested_owner_invalid_inheritance_refuses_before_launch(tmp_path, monkeypatch, shape):
    fields = {runner.OWNER_ENV: 'a' * 32, runner.ANCESTOR_ENV: 'b' * 32}
    if shape == 'oversized':
        fields[runner.ANCESTOR_ENV] = 'b' * (runner.MAX_OWNER_ANCESTORS * 33)
    elif shape == 'bad-token':
        fields[runner.ANCESTOR_ENV] = '../not-an-owner'
    elif shape == 'empty-token':
        fields[runner.ANCESTOR_ENV] += ','
    elif shape == 'duplicate':
        fields[runner.ANCESTOR_ENV] += ',' + 'b' * 32
    elif shape == 'self-ancestor':
        fields[runner.ANCESTOR_ENV] = fields[runner.OWNER_ENV]
    elif shape == 'missing-owner':
        fields.pop(runner.OWNER_ENV)
    elif shape == 'bad-owner':
        fields[runner.OWNER_ENV] = 'not-an-owner'
    elif shape == 'empty-owner':
        fields[runner.OWNER_ENV] = ''
    else:
        fields[runner.ANCESTOR_ENV] = '\N{SNOWMAN}'
    for name in (runner.OWNER_ENV, runner.ANCESTOR_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in fields.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('Invalid lineage cannot launch'))
    # A scenario override cannot repair or hide invalid inherited ownership.
    row = scenario('invalid-owner', 'pass')
    row['env'] = {runner.OWNER_ENV: 'c' * 32, runner.ANCESTOR_ENV: ''}
    code, report, _ = execute(tmp_path, [row])
    assert code == 1 and not report['passed']
    assert report['scenarios'][0]['executionError'] == 'OSError'
    assert report['scenarios'][0]['cleanup'] == {'remaining': [], 'terminated': 0}


@pytest.mark.parametrize('shape', ['duplicate-owner', 'duplicate-ancestors', 'non-ascii'])
def test_python_nested_owner_inspection_rejects_ambiguous_fields(shape):
    owner = (runner.OWNER_ENV + '=' + 'a' * 32).encode()
    ancestor = (runner.ANCESTOR_ENV + '=' + 'b' * 32).encode()
    data = {'duplicate-owner': owner + b'\0' + owner,
            'duplicate-ancestors': owner + b'\0' + ancestor + b'\0' + ancestor,
            'non-ascii': owner + b'\0' + runner.ANCESTOR_ENV.encode() + b'=\xff'}[shape]
    with pytest.raises(OSError):
        runner.environment_owns(data, 'a' * 32)


def finish_nested_fixture_identities(paths):
    """Fallback for these tests only: kill recorded PID/births, never token scans."""
    identities = [json.loads(path.read_text()) for path in paths if path.exists()]
    for identity in identities:
        try:
            handle = os.pidfd_open(identity['pid'])
            try:
                fields = Path(f'/proc/{identity["pid"]}/stat').read_text().rsplit(')', 1)[1].split()
                if fields[19] == identity['birth'] and fields[0] != 'Z':
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
            finally:
                os.close(handle)
        except (FileNotFoundError, ProcessLookupError):
            pass
    deadline = runner.time.monotonic() + 3
    while True:
        live = []
        for identity in identities:
            try:
                fields = Path(f'/proc/{identity["pid"]}/stat').read_text().rsplit(')', 1)[1].split()
                if fields[19] == identity['birth'] and fields[0] != 'Z':
                    live.append(identity['pid'])
            except (FileNotFoundError, ProcessLookupError):
                pass
        if not live:
            return
        assert runner.time.monotonic() < deadline, f'Fixture cleanup did not settle: {live}'
        runner.time.sleep(.02)


@pytest.mark.parametrize('mode', ['timeout', 'cancelled', 'unreadable'])
def test_python_nested_detached_owner_survives_inner_loss_without_false_outer_clear(tmp_path, monkeypatch, mode):
    leader, child, detached = [tmp_path / name for name in ('leader.json', 'child.json', 'detached.json')]
    ready, held = tmp_path / 'ready', tmp_path / 'inner-cleanup-held'
    record = '''
def record(path):
    pid = os.getpid()
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
    temporary = Path(path).with_suffix('.pending')
    temporary.write_text(json.dumps({'pid':pid,'birth':fields[19],
        'group':os.getpgrp(),'owner':os.environ.get('AMPLIFIER_BROWSER_CHECK_OWNER'),
        'ancestors':os.environ.get('AMPLIFIER_BROWSER_CHECK_ANCESTORS','')}))
    temporary.replace(path)
'''
    grandchild_source = ('import os,json,signal,time;from pathlib import Path\n' + record +
                         f'\nrecord({str(detached)!r})\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(60)')
    child_source = f'''
import os,json,subprocess,sys,time
from pathlib import Path
{record}
record({str(child)!r})
p = subprocess.Popen([sys.executable,'-c',{grandchild_source!r}],start_new_session=True)
deadline = time.monotonic()+5
while not Path({str(detached)!r}).exists():
    assert time.monotonic()<deadline
    time.sleep(.01)
Path({str(ready)!r}).touch()
time.sleep(60)
'''
    inner_source = f'''
import importlib.util,os,json,signal,sys,time
from pathlib import Path
{record}
record({str(leader)!r})
spec=importlib.util.spec_from_file_location('inner_runner',{str(ROOT / 'scripts/browser_check_runner.py')!r})
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
def held_cleanup(*args,**kwargs):
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    Path({str(held)!r}).touch()
    time.sleep(60)
    raise AssertionError('Inner cleanup must not finish before forced loss')
r.retire_group=held_cleanup
r.run_scenarios(Path({str(tmp_path)!r}),{{'schemaVersion':1,'scenarios':[
    {{'id':'inner','command':[sys.executable,'-c',{child_source!r}]}}]}},
    Path({str(tmp_path / 'inner-results')!r}),cwd=Path({str(tmp_path)!r}),budget=20,case_timeout=20)
'''
    original_clock, original_open = runner.time.monotonic, Path.open
    retire, popen = runner.retire_group, runner.subprocess.Popen
    spawned, sent = [], []
    protected_path = None
    def outer_clock():
        if ready.exists():
            if mode == 'cancelled' and not sent:
                sent.append(True)
                os.kill(os.getpid(), signal.SIGTERM)  # Current outer handler only.
            elif mode != 'cancelled':
                return original_clock() + 100
        return original_clock()
    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        spawned.append(process)
        return process
    def protected(path, *args, **kwargs):
        if path == protected_path:
            raise PermissionError('Nested descendant identity unavailable')
        return original_open(path, *args, **kwargs)
    def inspect_after_inner_stop(*args, **kwargs):
        nonlocal protected_path
        until = original_clock() + 5
        while not held.exists() and original_clock() < until:
            runner.time.sleep(.01)
        assert held.exists(), 'Outer interruption must reach the inner runner before forced cleanup'
        # Inject only at cleanup, after SIGINT used readable ownership.
        if mode == 'unreadable':
            protected_path = Path('/proc') / str(json.loads(detached.read_text())['pid']) / 'environ'
            monkeypatch.setattr(Path, 'open', protected)
        return retire(*args, **kwargs)
    unrelated = popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
    try:
        monkeypatch.setattr(runner.time, 'monotonic', outer_clock)
        monkeypatch.setattr(runner.subprocess, 'Popen', track)
        monkeypatch.setattr(runner, 'retire_group', inspect_after_inner_stop)
        code, report, _ = bounded_execute(tmp_path, inner_source, budget=15, case_timeout=15)
        row, = report['scenarios']
        assert code == 1 and not report['passed']
        assert row['reason'] == ('cancelled' if mode == 'cancelled' else 'timeout')
        assert row['interrupt']['sent'] and held.exists()
        parent, nested, orphan = [json.loads(path.read_text()) for path in (leader, child, detached)]
        assert parent['owner'] != nested['owner'] == orphan['owner']
        assert parent['owner'] in nested['ancestors'].split(',') == orphan['ancestors'].split(',')
        assert parent['group'] != nested['group'] != orphan['group']
        assert unrelated.poll() is None
        if mode == 'unreadable':
            assert row['cleanup']['remaining'] == ['unknown']
        else:
            assert row['cleanup']['remaining'] == []
            assert orphan['pid'] in [item['pid'] for item in row['cleanup']['observedBeforeTermination']]
            for identity in (parent, nested, orphan):
                path = Path(f'/proc/{identity["pid"]}/stat')
                assert not path.exists() or path.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
    finally:
        monkeypatch.setattr(Path, 'open', original_open)
        monkeypatch.setattr(runner.time, 'monotonic', original_clock)
        try:
            finish_nested_fixture_identities((leader, child, detached))
        finally:
            try:
                for process in spawned:
                    if process.poll() is None:
                        process.kill()  # Exact owned Popen, including failure before leader record.
                    process.wait(timeout=3)
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=3)


@pytest.mark.parametrize('bounded', [False, True])
def test_python_inner_cleanup_preserves_outer_parent_and_sibling(tmp_path, bounded):
    sibling = tmp_path / 'sibling.json'
    source = f'''
import importlib.util,json,os,subprocess,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('inner_runner',{str(ROOT / 'scripts/browser_check_runner.py')!r})
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
outer=os.environ[r.OWNER_ENV]
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
birth=Path(f'/proc/{{p.pid}}/stat').read_text().rsplit(')',1)[1].split()[19]
Path({str(sibling)!r}).write_text(json.dumps({{'pid':p.pid,'birth':birth}}))
try:
    command=[sys.executable,'-c','import os,json;print(json.dumps({{key:os.environ[key] for key in ("AMPLIFIER_BROWSER_CHECK_OWNER","AMPLIFIER_BROWSER_CHECK_ANCESTORS")}}))']
    row={{'id':'inner','command':command,
         'env':{{r.OWNER_ENV:'invalid-row-owner',r.ANCESTOR_ENV:''}}}}
    output=Path({str(tmp_path / 'inner-isolation')!r})
    code=r.run_scenarios(Path({str(tmp_path)!r}),{{'schemaVersion':1,'scenarios':[row]}},output,
        cwd=Path({str(tmp_path)!r}),log_limit={65536 if bounded else None!r})
    assert code==0 and p.poll() is None
    receipt=json.loads((output/'results.json').read_text())
    assert receipt['scenarios'][0]['cleanup']=={{'remaining':[],'terminated':0}}
    inherited=json.loads((output/'inner.log').read_text())
    assert inherited[r.OWNER_ENV]!=outer
    assert inherited[r.ANCESTOR_ENV].split(',')[-1]==outer
    baseline=r.process_identities()
    baseline.pop(os.getpid());baseline.pop(p.pid)
    # Also inspect these actual environments without their preexisting exemption.
    members=r.owned_members(-1,inherited[r.OWNER_ENV],{{}},baseline)
    assert os.getpid() not in members and p.pid not in members
finally:
    p.terminate();p.wait(timeout=3)
'''
    try:
        code, report, _ = execute(tmp_path, [scenario('outer', source)], case_timeout=15)
        assert code == 0 and report['passed']
        assert report['scenarios'][0]['cleanup'] == {'remaining': [], 'terminated': 0}
    finally:
        finish_nested_fixture_identities((sibling,))


@pytest.fixture
def cleanup_processes(monkeypatch):
    """Synthetic /proc and pidfds; no real process, scan or signal operation."""
    import errno
    from types import SimpleNamespace
    originals = {name: getattr(Path, name) for name in ('iterdir', 'read_text', 'stat', 'open', 'readlink')}
    original_close = os.close

    def configure(fault, count=1, *, environment=None):
        pids = list(range(91001, 91001 + count))
        births = {pid: str(12300 + i) for i, pid in enumerate(pids)}
        live, handles, denied, calls = set(pids), {}, set(), []
        token = 'a' * 32
        target = pids[-1]

        def refusal():
            error = PermissionError(errno.EACCES, 'SECRET exception text', '/private/SECRET/path')
            # Exception-owned fields are not diagnostic authority.
            error._cleanup_receipt = {'remaining': [], 'terminated': 0, 'private': 'SECRET'}
            error._cleanup_failure = {'operation': 'SECRET', 'ownership': 'proven'}
            return error

        def operation(name, pid):
            calls.append((name, pid))
            if (pid == target or name == 'proc_iter') and name == fault:
                raise refusal()

        def iterdir(path):
            if path != Path('/proc'):
                return originals['iterdir'](path)
            operation('proc_iter', None)
            return iter(Path('/proc') / str(pid) for pid in pids if pid in live)

        def read_text(path, *args, **kwargs):
            if path.parent.parent != Path('/proc') or path.name != 'stat':
                return originals['read_text'](path, *args, **kwargs)
            pid = int(path.parent.name)
            name = ('identity_recheck' if pid in handles.values() else
                    'post_denial_stat' if pid in denied else 'stat')
            operation(name, pid)
            denied.discard(pid)
            fields = ['0'] * 20
            fields[0], fields[1], fields[2], fields[19] = 'S', '1', '88001', births[pid]
            return f'{pid} (SECRET command) ' + ' '.join(fields)

        def uid_stat(path, *args, **kwargs):
            if path.parent != Path('/proc') or not path.name.isdigit():
                return originals['stat'](path, *args, **kwargs)
            operation('uid_stat', int(path.name))
            return SimpleNamespace(st_uid=os.geteuid())

        def open_environ(path, *args, **kwargs):
            if path.parent.parent != Path('/proc') or path.name != 'environ':
                return originals['open'](path, *args, **kwargs)
            pid = int(path.parent.name)
            try:
                operation('environ_open', pid)
                if fault == 'post_denial_stat' and pid == target:
                    raise refusal()
            except PermissionError:
                denied.add(pid)
                raise
            class Stream:
                def __enter__(self):
                    return self
                def read(self, size):
                    assert size == 1024 * 1024 + 1
                    try:
                        operation('environ_read', pid)
                    except PermissionError:
                        denied.add(pid)
                        raise
                    owner = token if environment is None else environment[runner.OWNER_ENV]
                    return (runner.OWNER_ENV + '=' + owner + '\0PRIVATE=SECRET\0').encode()
                def __exit__(self, *args):
                    try:
                        operation('environ_close', pid)
                    except PermissionError:
                        denied.add(pid)
                        raise
            return Stream()

        def readlink(path):
            if path.parent.parent == Path('/proc') and path.name == 'exe':
                return Path('/private/SECRET/python')
            return originals['readlink'](path)

        def pidfd_open(pid):
            assert pid in pids  # Never pass through to the host.
            operation('pidfd_open', pid)
            handle = pid + 100000
            handles[handle] = pid
            return handle

        def send_signal(handle, sig):
            assert handle in handles and sig in (signal.SIGTERM, signal.SIGKILL)
            pid = handles[handle]
            operation('signal', pid)
            live.discard(pid)  # Simulated disappearance on the next existing scan.

        def close(handle):
            if handle not in handles:
                # run_scenarios writes its test-owned evidence using real FDs.
                # Only the explicitly fake pidfds above are syscall-injected.
                return original_close(handle)
            pid = handles.pop(handle)
            operation('pidfd_close', pid)

        for name, func in [('iterdir', iterdir), ('read_text', read_text), ('stat', uid_stat),
                           ('open', open_environ), ('readlink', readlink)]:
            monkeypatch.setattr(Path, name, func)
        monkeypatch.setattr(runner.os, 'pidfd_open', pidfd_open)
        monkeypatch.setattr(runner.signal, 'pidfd_send_signal', send_signal)
        monkeypatch.setattr(runner.os, 'close', close)
        ticks = iter(range(10000))
        monkeypatch.setattr(runner.time, 'monotonic', lambda: next(ticks))
        monkeypatch.setattr(runner.time, 'sleep', lambda seconds: None)
        return SimpleNamespace(pids=pids, births=births, live=live, calls=calls,
                               token=token, group=88000, handles=handles)
    return configure


@pytest.mark.parametrize('operation', [
    'proc_iter', 'stat', 'uid_stat', 'environ_open', 'environ_read', 'environ_close',
    'post_denial_stat', 'pidfd_open', 'identity_recheck', 'signal', 'pidfd_close',
])
def test_cleanup_failure_receipt_identifies_exact_denied_operation(cleanup_processes, operation):
    import errno
    fixture = cleanup_processes(operation)
    receipt = runner.retire_group(fixture.group, fixture.token, {})
    owned = operation in {'pidfd_open', 'identity_recheck', 'signal', 'pidfd_close'}
    expected = {'phase': 'termination' if owned else 'grace', 'operation': operation,
                'errorType': 'PermissionError', 'errno': errno.EACCES,
                'ownership': 'proven' if owned else 'unknown'}
    if operation != 'proc_iter':
        expected['pid'] = fixture.pids[0]
    if operation not in {'stat', 'proc_iter'}:
        expected['startTime'] = fixture.births[fixture.pids[0]]
    assert receipt['failure'] == expected
    assert receipt['remaining'] == ['unknown']
    assert receipt['terminated'] == (1 if owned else None)
    attempts = receipt['signalAttempts']
    assert attempts['attempted'] == int(operation in {'signal', 'pidfd_close'})
    assert attempts['returned'] == int(operation == 'pidfd_close')
    assert attempts['failed'] == int(operation == 'signal')
    assert attempts['disappeared'] == attempts['omitted'] == 0
    assert fixture.calls.count((operation, None if operation == 'proc_iter' else fixture.pids[0])) == 1
    if operation in {'environ_open', 'environ_read', 'environ_close', 'post_denial_stat'}:
        assert fixture.calls.count(('post_denial_stat', fixture.pids[0])) == 1
        assert fixture.calls.count(('environ_open', fixture.pids[0])) == 1
    if operation in {'identity_recheck', 'signal', 'pidfd_close'}:
        assert fixture.calls.count(('pidfd_close', fixture.pids[0])) == 1
    assert not fixture.handles
    encoded = json.dumps(receipt)
    assert 'SECRET' not in encoded and '/private' not in encoded and '/proc' not in encoded
    assert fixture.token not in encoded and runner.OWNER_ENV not in encoded


@pytest.mark.parametrize('drain_failed', [False, True])
def test_cleanup_failure_receipt_keeps_prior_signal_and_capture_error(cleanup_processes, drain_failed):
    fixture = cleanup_processes('signal', count=2)
    def drain():
        if drain_failed:
            raise OSError('SECRET disk failure')
    receipt = runner.retire_group(fixture.group, fixture.token, {}, drain=drain)
    assert receipt['remaining'] == ['unknown'] and receipt['terminated'] == 2
    assert receipt['failure']['pid'] == fixture.pids[1]
    assert receipt['signalAttempts'] == {
        'attempted': 2, 'returned': 1, 'failed': 1, 'disappeared': 0, 'omitted': 0,
        'items': [{'pid': pid, 'startTime': fixture.births[pid], 'signal': 'SIGTERM',
                   'outcome': 'returned' if index == 0 else 'failed'}
                  for index, pid in enumerate(fixture.pids)]}
    assert receipt.get('drainError') == ('OSError' if drain_failed else None)
    assert len(receipt['observedBeforeTermination']) == 2
    assert fixture.live == {fixture.pids[1]}
    assert 'SECRET' not in json.dumps(receipt)


def test_cleanup_failure_receipt_bounds_signal_details_without_losing_counts(cleanup_processes):
    fixture = cleanup_processes('signal', count=41)
    receipt = runner.retire_group(fixture.group, fixture.token, {})
    assert receipt['terminated'] == 41 and receipt['remaining'] == ['unknown']
    assert receipt['signalAttempts']['attempted'] == 41
    assert receipt['signalAttempts']['returned'] == 40 and receipt['signalAttempts']['failed'] == 1
    assert len(receipt['signalAttempts']['items']) == 32 and receipt['signalAttempts']['omitted'] == 9
    assert len(receipt['observedBeforeTermination']) == 32 and receipt['omittedProcessDetails'] == 9
    assert receipt['failure']['pid'] == fixture.pids[-1]
    assert len(json.dumps(receipt)) < 12000


def test_cleanup_failure_receipt_success_uses_existing_scan_and_signal_sequence(cleanup_processes):
    fixture = cleanup_processes(None)
    receipt = runner.retire_group(fixture.group, fixture.token, {})
    assert receipt['remaining'] == [] and receipt['terminated'] == 1
    assert 'failure' not in receipt
    assert receipt['signalAttempts'] == {
        'attempted': 1, 'returned': 1, 'failed': 0, 'disappeared': 0, 'omitted': 0,
        'items': [{'pid': fixture.pids[0], 'startTime': fixture.births[fixture.pids[0]],
                   'signal': 'SIGTERM', 'outcome': 'returned'}]}
    assert [op for op, _ in fixture.calls] == [
        'proc_iter', 'stat', 'uid_stat', 'environ_open', 'environ_read', 'environ_close',
        'proc_iter', 'stat', 'stat', 'stat',  # initial scan + two existing metadata checks
        'proc_iter', 'stat', 'pidfd_open', 'identity_recheck', 'signal', 'pidfd_close',
        'proc_iter', 'proc_iter']  # post-TERM wait and pre-KILL check; no extra scan
    assert not fixture.live and not fixture.handles


@pytest.mark.parametrize('operation', ['stat', 'signal', 'pidfd_close'])
def test_cleanup_failure_receipt_keeps_next_scenario_unrun(tmp_path, monkeypatch, cleanup_processes, operation):
    from types import SimpleNamespace
    environment = {}
    fixture = cleanup_processes(operation, environment=environment)
    monkeypatch.setattr(runner, 'process_identities', lambda: {})
    launches = []
    def wait(*, timeout):
        pytest.fail('Injected unsafe cleanup must not reach process.wait')
    def popen(*args, **kwargs):
        assert not launches and kwargs['start_new_session'] is True
        # Detached fake descendants inherit this launch's real owner token.
        # Keep only that field in memory, not the caller's whole environment.
        owner = kwargs['env'][runner.OWNER_ENV]
        assert re.fullmatch(r'[0-9a-f]{32}', owner)
        environment[runner.OWNER_ENV] = owner
        launches.append(args[0])
        return SimpleNamespace(pid=fixture.group, poll=lambda: 0, wait=wait, returncode=0)
    monkeypatch.setattr(runner.subprocess, 'Popen', popen)
    code, report, output = execute(tmp_path, [scenario('first', 'pass'), scenario('later', 'pass')])
    first, later = report['scenarios']
    assert set(environment) == {runner.OWNER_ENV} and len(launches) == 1
    assert fixture.calls.count((operation, fixture.pids[0])) == 1
    assert code == 1 and not report['passed'] and not report['complete']
    assert first['status'] == 'failed' and first['exitCode'] == 0
    assert first['cleanup']['remaining'] == ['unknown']
    assert first['cleanup']['failure']['operation'] == operation
    owned = operation != 'stat'
    assert first['cleanup']['failure']['ownership'] == ('proven' if owned else 'unknown')
    assert first['cleanup']['terminated'] == (1 if owned else None)
    attempts = first['cleanup']['signalAttempts']
    assert attempts['attempted'] == int(owned)
    assert attempts['returned'] == int(operation == 'pidfd_close')
    assert attempts['failed'] == int(operation == 'signal')
    if owned:
        assert fixture.calls.count(('environ_read', fixture.pids[0])) == 1
        assert first['cleanup']['observedBeforeTermination'][0]['processGroup'] == 88001
        assert fixture.group == 88000  # Ownership came from the token, not the group.
    assert not fixture.handles
    assert later['status'] == 'unrun' and later['reason'] == 'unsafe_cleanup'
    assert len(launches) == 1 and not (output / 'later.log').exists()
    assert 'SECRET' not in json.dumps(first['cleanup'])
    assert all(value not in json.dumps(report) for value in environment.values())


def test_cleanup_failure_receipt_fallback_does_not_trust_exception_payload():
    error = PermissionError(13, 'SECRET', '/private/SECRET')
    error._cleanup_receipt = {'remaining': [], 'terminated': 0, 'secret': 'SECRET'}
    error._cleanup_failure = {'phase': 'SECRET', 'ownership': 'proven'}
    receipt = runner.unknown_cleanup(error)
    assert receipt == {'remaining': ['unknown'], 'terminated': None, 'signalAttempts': None,
        'error': 'PermissionError', 'failure': {'phase': 'unknown', 'operation': 'retire_group',
        'errorType': 'PermissionError', 'errno': 13, 'ownership': 'unknown'}}
    assert runner.cleanup_failure(error, 'SECRET', 'SECRET', pid=-1, birth='SECRET',
                                  ownership='SECRET') == {
        'phase': 'unknown', 'operation': 'unknown', 'errorType': 'PermissionError',
        'errno': 13, 'ownership': 'unknown'}


def test_cleanup_failure_receipt_wait_timeout_preserves_signal_progress(tmp_path, monkeypatch):
    from types import SimpleNamespace
    progress = {'remaining': [], 'terminated': 1, 'drainError': 'OSError',
                'signalAttempts': {'attempted': 1, 'returned': 1, 'failed': 0,
                                  'disappeared': 0, 'omitted': 0, 'items': []}}
    def wait(*, timeout):
        assert timeout == 2
        raise subprocess.TimeoutExpired(['/private/SECRET/command'], timeout)
    monkeypatch.setattr(runner, 'process_identities', lambda: {})
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **kw:
                        SimpleNamespace(pid=91000, poll=lambda: 0, wait=wait, returncode=0))
    monkeypatch.setattr(runner, 'retire_group', lambda *a, **kw: dict(progress))
    code, report, _ = execute(tmp_path, [scenario('first', 'pass'), scenario('later', 'pass')])
    receipt = report['scenarios'][0]['cleanup']
    assert code == 1 and receipt['remaining'] == ['unknown']
    assert receipt['terminated'] == 1 and receipt['signalAttempts'] == progress['signalAttempts']
    assert receipt['drainError'] == 'OSError' and receipt['error'] == 'WaitTimeout'
    assert receipt['failure'] == {'phase': 'wait', 'operation': 'process_wait',
                                 'errorType': 'TimeoutExpired', 'pid': 91000, 'ownership': 'unknown'}
    assert report['scenarios'][1]['reason'] == 'unsafe_cleanup'
    assert 'SECRET' not in json.dumps(receipt)
