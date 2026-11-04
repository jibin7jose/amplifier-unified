"""Assemble public projections of retained evidence; never rerun a check.

Usage: python collect_evidence.py ORIGINAL_TRIAGE RESOURCE_ROOT PRIVATE_RUN_BINDINGS
The private JSON maps baseline/candidate/final to resourceId/requestId/operationId.
Keep that map and unmodified receipts outside Git. Public labels are not locators.
Raw hashes identify original bytes; sanitized file hashes identify public copies.
Refuses overwriting any generated file. Archives and original evidence are read-only.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
SOURCE_COMMIT = '6e20192d6fd07127ac5478897e1b68abcbc28f7d'
SOURCE_TREE = 'a48e1b2f975189ba28e311187e1bfd7bf9fcde0e'
ORIGINAL_SHA = '9708bb72ebfb6721f935b0c3b9da697229d6f06ebe11c23c6ca6c14632196abb'
RUNS = {
    'baseline': 'failed',
    'candidate': 'passed',
    'final': 'passed',
}
PUBLIC_EVIDENCE = {
    'projection': 'Private host paths, operational mappings and guest hostnames replaced by neutral labels.',
    'labelsAreLocators': False,
    'originals': 'Unmodified receipts and published files retained privately outside Git.',
    'hashes': 'Archive, inventory and cleanup hashes refer to private originals. retainedFiles hashes refer to public copies; originalSha256 records any changed raw file.',
    'history': 'The earlier public commit remains in Git history; current-tree sanitation does not remove it.',
}
BASE_TESTS = 'tests/test_uv_diagnostic_forms.py tests/test_directory_live_materialization.py tests/test_observation_input.py tests/test_peer_inbox_policy.py tests/test_peer_qualification.py tests/test_provider_recording.py tests/test_provider_removal_composition.py tests/test_recovery_edges.py tests/test_retained_failure_recovery.py tests/test_runtime_import_isolation.py::test_apply_settings_does_not_require_server_dependencies tests/test_runtime_progress_backpressure.py tests/test_shared_runtime_config.py tests/test_shared_settings.py tests/test_test_queue_notifications.py'
FINAL_TESTS = 'tests/test_uv_diagnostic_forms.py tests/test_uv_diagnostic_fixture_cleanup.py tests/test_directory_live_materialization.py tests/test_history_directory_navigation.py::test_workspace_counts_archive_and_restore_without_decoding_history tests/test_observation_input.py tests/test_peer_inbox_policy.py tests/test_peer_qualification.py tests/test_provider_recording.py tests/test_provider_removal_composition.py tests/test_host_provider_sources.py tests/test_decision_accounts.py tests/test_recovery_edges.py tests/test_retained_failure_recovery.py tests/test_assignment_recovery.py tests/test_peer_resume.py tests/test_runtime_import_isolation.py::test_apply_settings_does_not_require_server_dependencies tests/test_runtime_progress_backpressure.py tests/test_shared_runtime_config.py tests/test_shared_settings.py tests/test_test_queue_notifications.py'
EXCLUDE = " -k 'not test_real_core_child_decision_hook_and_resume and not test_cold_worker_catalog_discovery'"
CAUSES = {
    'test_uv_diagnostic_forms.py': ('isolated-diagnostic-harness', 'fixture', 'Ordinary pytest violates the expanded fixture startup guard. Run each unchanged unittest case under clean absolute Python -I -S -B, keep each original node, reject empty/skipped/failed child reports, preserve startup refusal and process cleanup.', ['tests/test_uv_diagnostic_forms.py', 'tests/test_uv_diagnostic_fixture_cleanup.py', 'tests/fixtures/omitted_ref_expanded_lock.py']),
    'test_directory_live_materialization.py': ('inherited-archive-state', 'fixture', 'The reused navigation fixture archives s5/s6. Workspace totals deliberately exclude archived chats; clear those inherited archives for the 500-chat work-count workload, retaining archive/restore sibling checks.', ['tests/test_directory_live_materialization.py', 'tests/test_history_directory_navigation.py', 'amplifier_web/history_directory_query.py']),
    'test_observation_input.py': ('uninitialized-worker', 'fixture', 'Worker.__new__ skipped production initialization including start_config/activation_gate. Construct Worker normally; retain forged-provenance and frozen-child assertions.', ['tests/test_observation_input.py', 'amplifier_web/runtime_worker.py']),
    'test_peer_inbox_policy.py': ('explicit-busy-boundary', 'fixture', 'Host-created returns use auto delivery and can enter busy model-request boundaries. Model a positive no-effect unavailable boundary when testing queued notification state; preserve unknown creation and one-attempt assertions.', ['tests/test_peer_inbox_policy.py', 'tests/test_collaborative_workspaces.py', 'amplifier_web/collaboration.py']),
    'test_peer_qualification.py': ('current-workflow-inventory', 'fixture', 'The fixed-route oracle predates readiness-trio and permissions-history. Match the maintained inventory and exact isolation predicate; retain negative-selection, upload, permission and no-secret checks. No workflow changed.', ['tests/test_peer_qualification.py', '.github/workflows/browser-checks.yml']),
    'test_provider_recording.py': ('agent-selector-traversal', 'product', 'logical_plan confused supported child inheritance selectors (none/list) with nested declaration mappings. Recurse only mappings, preserving selectors and exact account/source validation.', ['amplifier_web/decision_accounts.py', 'amplifier_web/host/children.py', 'tests/test_decision_accounts.py', 'contracts/agent-protocol.v1.md', 'contracts/ui-use.v1.md']),
    'test_provider_removal_composition.py': ('partial-bundle-double', 'fixture', 'The partial bundle double omitted agents required by the maintained bundle interface. Use the real Foundation Bundle; no production fallback.', ['tests/test_provider_removal_composition.py', 'amplifier_web/host/session.py']),
    'test_recovery_edges.py': ('fresh-admission-epoch', 'fixture', 'Fresh synthetic generations after Stop omitted their admitted interruption epoch and were correctly rejected as stale. Supply the captured epoch and assert exact generation/input/terminal state. Keep stale epochs, safety halts, unknown Stop and budget negatives.', ['tests/test_recovery_edges.py', 'tests/test_collaborative_workspaces.py', 'tests/test_assignment_recovery.py', 'amplifier_web/collaboration.py', 'amplifier_web/runtime_worker.py']),
    'test_retained_failure_recovery.py': ('explicit-busy-boundary', 'fixture', 'A subscribed commission failure also sends an automatic manager return. Hold that unrelated manager input at a modeled unavailable busy boundary while asserting one exact target recovery and unchanged historical failure.', ['tests/test_retained_failure_recovery.py', 'tests/test_collaborative_workspaces.py', 'amplifier_web/collaboration.py']),
    'test_runtime_import_isolation.py': ('partial-bundle-double', 'fixture', 'Complete the dependency-isolation bundle double with agents; preserve absent aiohttp and host/server import assertions in the clean child interpreter.', ['tests/test_runtime_import_isolation.py', 'amplifier_web/host/session.py']),
    'test_runtime_progress_backpressure.py': ('runtime-owner-witness', 'fixture', 'Bridge fixtures omitted runtimeOwnerId now required by the host bridge. Supply one exact owner and assert its stamped argument; retain yield, ordering, serialization, cancellation and timing assertions.', ['tests/test_runtime_progress_backpressure.py', 'amplifier_web/runtime.py']),
    'test_shared_runtime_config.py': ('shared-model-bundle', 'product', 'The production shared model adapter constructs a partial bundle without agents before calling _apply_settings. Add the explicit empty model-only agent mapping; caller-owned domain agents remain unchanged and tested.', ['amplifier_web/host/shared_runtime_config.py', 'amplifier_web/host/session.py', 'tests/test_shared_runtime_config.py', 'contracts/agent-protocol.v1.md']),
    'test_shared_settings.py': ('partial-bundle-double', 'fixture', 'Use the real Foundation Bundle for settings composition instead of an incomplete double; preserve module override and shared routing assertions.', ['tests/test_shared_settings.py', 'amplifier_web/host/session.py']),
    'test_test_queue_notifications.py': ('explicit-busy-boundary', 'fixture', 'Busy status is not an unavailable auto-input boundary. Add one opt-in no-effect boundary transport for queue/commit/Stop checkpoints, plus a positive busy-boundary test; preserve exactly-once notification/backend execution and recovery.', ['tests/test_test_queue_notifications.py', 'amplifier_web/collaboration.py', 'docs/QUEUED-TESTS.md', 'docs/workspaces/contracts/execution.v2-candidate.md']),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(name, data):
    # The reviewed logs contain only synthetic fixture IDs and generic guest
    # paths. Refuse new host-path material instead of exporting it silently.
    if re.search(rb'/(?:home|Users)/|[A-Za-z]:[\\/]+Users[\\/]|workspace-resources|managed-worktrees', data):
        raise ValueError('Private host location in public evidence')
    path = OUT / name
    with path.open('xb') as stream:
        stream.write(data)
    return {'path': name, 'bytes': len(data), 'sha256': digest(data)}


def save_json(name, value):
    return save(name, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())


def cases(raw):
    root = ET.fromstring(raw)
    rows = {}
    for case in root.iter('testcase'):
        cls = case.attrib['classname'].split('.')
        assert cls[:1] == ['tests']
        node = 'tests/' + cls[1] + '.py::' + '::'.join([*cls[2:], case.attrib['name']])
        status = next((kind for kind in ('failure', 'error', 'skipped') if case.find(kind) is not None), 'passed')
        assert node not in rows
        rows[node] = status
    return rows, [suite.attrib for suite in root.iter('testsuite')]


def public_junit(raw, label):
    """Change only host metadata, preserving every result and assertion byte."""
    return re.sub(rb'\bhostname="[^"]*"', f'hostname="{label}-guest"'.encode(), raw)


def public_check(label, value):
    # Allowlist receipt fields so future private metadata is not published.
    result = {key: copy.deepcopy(value[key]) for key in
              ('outcome', 'profile', 'command', 'timeoutSeconds', 'retainedFiles', 'cleanupReceiptSha256')}
    result['source'] = public_source(label, value['source'])
    result['artifactsArchive'] = {
        **{key: value['artifactsArchive'][key] for key in
           ('bytes', 'logicalBytes', 'sha256', 'source', 'verified')},
        'destination': f'private-artifacts/{label}/artifacts.tar', 'id': f'{label}-guest'}
    cleanup = value['cleanup']
    binding = cleanup['binding']
    result['cleanup'] = {
        **{key: cleanup[key] for key in ('intentSha256', 'phase', 'version')},
        'binding': {
            **{key: binding[key] for key in ('admissionRevision', 'resourceRevision')},
            'backendIdentity': {'backend': binding['backendIdentity']['backend'], 'daemon': 'test-daemon'},
            'backendKey': 'test-backend', 'operationId': f'{label}-cleanup',
            'requestId': f'{label}-remove', 'resourceId': f'{label}-resource',
            'rootOwner': 'repair-owner', 'universeId': f'{label}-guest',
            'snapshot': public_source(label, binding['snapshot'])}}
    result.update(operationId=f'{label}-check', requestId=f'{label}-request',
                  resourceId=f'{label}-resource')
    result['junitSuites'] = [{
        **{key: suite[key] for key in
           ('errors', 'failures', 'name', 'skipped', 'tests', 'time', 'timestamp')},
        'hostname': f'{label}-guest'} for suite in value['junitSuites']]
    return result


def public_source(label, source):
    return {**{key: source[key] for key in
               ('archive_bytes', 'archive_sha256', 'files', 'shallow_source',
                'snapshot_commit', 'snapshot_tree', 'source_branch', 'source_head')},
            'path': f'private-artifacts/{label}/source.tar'}


def public_preparation(label, inventory, raw):
    return {'resourceId': f'{label}-resource',
            'inventoryPath': f'private-artifacts/{label}/preparation/inventory.json',
            'inventorySha256': digest(raw),
            **{key: inventory[key] for key in
               ('python', 'lockfiles', 'pythonPackages', 'executionContract') if key in inventory}}


def collect(triage_path, resource_root, run_bindings):
    original_bytes = triage_path.read_bytes()
    assert digest(original_bytes) == ORIGINAL_SHA
    original = json.loads(original_bytes)
    runs, node_maps, preparation = {}, {}, {}
    for label, outcome in RUNS.items():
        resource_id, request, operation = (
            run_bindings[label][key] for key in ('resourceId', 'requestId', 'operationId'))
        resource = resource_root / resource_id
        snapshot = json.loads((resource / 'snapshot.json').read_text())
        assert digest((resource / 'source.tar').read_bytes()) == snapshot['archive_sha256']
        if label == 'baseline':
            assert snapshot['snapshot_tree'] == 'c9451a0eb4405b16a79a504e8a883ccfa72624fc'
        if label == 'final':
            assert snapshot['snapshot_tree'] == SOURCE_TREE
        manifests = list(resource.glob('retained-*/manifest.json'))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text())
        selected, = [entry for entry in manifest if entry['source'] == '/artifacts']
        archive = Path(selected['destination'])
        assert selected['verified'] and digest(archive.read_bytes()) == selected['sha256']
        retained = []
        with tarfile.open(archive) as tar:
            for suffix in ('log', 'xml'):
                payload = tar.extractfile(f'artifacts/{label}.{suffix}').read()
                public = public_junit(payload, label) if suffix == 'xml' else payload
                retained.append({**save(f'{label}.{suffix}', public),
                                 **({'originalBytes': len(payload), 'originalSha256': digest(payload)}
                                    if public != payload else {})})
                if suffix == 'xml':
                    node_maps[label], counts = cases(payload)
        cleanup_path = manifests[0].parent / 'destroy-confirmed.json'
        cleanup = json.loads(cleanup_path.read_text())
        test_args = BASE_TESTS if label == 'baseline' else FINAL_TESTS + EXCLUDE
        prefix = 'mkdir -p /artifacts && ' if label == 'baseline' else ''
        command = prefix + '/opt/unified-prepared/venv/bin/python -m pytest -q --tb=short ' + test_args
        command += f' --junitxml=/artifacts/{label}.xml > /artifacts/{label}.log 2>&1; result=$?; tail -100 /artifacts/{label}.log; exit "$result"'
        runs[label] = public_check(label, {'requestId': request, 'operationId': operation, 'resourceId': resource_id,
                       'outcome': outcome, 'profile': 'python-project', 'command': command,
                       'timeoutSeconds': 300, 'source': snapshot, 'artifactsArchive': selected,
                       'retainedFiles': retained, 'junitSuites': counts, 'cleanup': cleanup,
                       'cleanupReceiptSha256': digest(cleanup_path.read_bytes())})
        inventory_raw = (resource / 'preparation/inventory.json').read_bytes()
        preparation[label] = public_preparation(label, json.loads(inventory_raw), inventory_raw)
    dispositions = []
    for group in original['python']['failureGroups']:
        cause, kind, rationale, refs = CAUSES[Path(group['file']).name]
        for case in group['cases']:
            node = case['node']
            expected = 'error' if group['kind'] == 'ERROR' else 'failure'
            assert node_maps['baseline'][node] == expected, node
            assert node_maps['candidate'][node] == node_maps['final'][node] == 'passed', node
            dispositions.append({'node': node, 'originalOutcome': expected, 'originalMessage': case['message'],
                                 'baseline': expected, 'candidate': 'passed', 'final': 'passed',
                                 'cause': cause, 'classification': kind, 'disposition': 'repaired-focused-verified',
                                 'rationale': rationale, 'sourceReferences': refs})
    assert len(dispositions) == 77
    assert all(value == 'passed' for value in node_maps['final'].values())
    changed = subprocess.check_output(['git', 'diff', '--name-only', original['commit'], SOURCE_COMMIT], text=True).splitlines()
    for name in changed:
        assert Path(name).read_bytes() == subprocess.check_output(['git', 'show', f'{SOURCE_COMMIT}:{name}'])
    report = {
        'schemaVersion': 1, 'publicEvidence': PUBLIC_EVIDENCE, 'issue': original['existingIssue'],
        'originalRun': {'id': original['runId'], 'url': original['url'], 'commit': original['commit'],
                        'tree': runs['baseline']['source']['snapshot_tree'], 'outcome': 'failure',
                        'counts': original['python']['logReportedCounts'], 'triageSha256': ORIGINAL_SHA,
                        'pythonArtifactSha256': '3b6dafa073c5cf04722d9c189fb2bf0a0a1cd043735fd892ff67b6049e2a8614'},
        'qualifiedSource': {'commit': SOURCE_COMMIT, 'tree': SOURCE_TREE, 'changedFiles': changed,
                            'fileHashes': {name: digest(Path(name).read_bytes()) for name in changed}},
        'checks': runs, 'cases': dispositions, 'unresolvedOriginalCases': [],
        'coverage': {'originalCases': 77, 'baselineCollected': len(node_maps['baseline']),
                     'finalPassed': len(node_maps['final']), 'finalSkipped': 0,
                     'deselectedOptionalInstalledRuntimeProbes': [
                         'tests/test_decision_accounts.py::test_real_core_child_decision_hook_and_resume',
                         'tests/test_decision_accounts.py::test_cold_worker_catalog_discovery_does_not_import_provider_packages'],
                     'limits': ['Focused synthetic checks, not full-suite or live-provider qualification.',
                                'Original full run remains failed, including separate browser and JUnit reporting groups.',
                                'No merge, publication, adoption, provider-selection change, or production compatibility fallback.',
                                'No assertion of recurrence linkage to issues 1554/1555/1560 without exact matching evidence.']},
    }
    save_json('case-disposition.json', report)
    save_json('preparation.json', preparation)
    print(json.dumps({'cases': len(dispositions), 'finalPassed': len(node_maps['final']),
                      'sourceTree': SOURCE_TREE, 'report': 'case-disposition.json'}))


if __name__ == '__main__':
    assert len(sys.argv) == 4
    collect(Path(sys.argv[1]), Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text()))
