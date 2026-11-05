"""Missing prerequisites must not turn designated integration jobs green."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('integration_checks', ROOT / 'scripts/integration_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


@pytest.mark.parametrize('problem', ['skipped', 'error', 'failure', 'empty', 'missing', 'counter'])
def test_incomplete_or_skipped_execution_cannot_pass(tmp_path, problem):
    case = '<testcase classname="tests.test_example" name="test_required"/>'
    if problem in {'skipped', 'error', 'failure'}:
        case = f'<testcase classname="tests.test_example" name="test_required"><{problem}/></testcase>'
    if problem == 'empty':
        case = ''
    if problem == 'missing':
        case = case.replace('test_required', 'test_unrelated')
    counters = ' skipped="1"' if problem == 'counter' else ''
    path = tmp_path / 'results.xml'
    path.write_text(f'<testsuites><testsuite{counters}>{case}</testsuite></testsuites>')
    with pytest.raises(ValueError):
        checks.verify_junit(path, ['tests/test_example.py::test_required'])


def test_successful_parameterized_execution_and_module_collection(tmp_path):
    path = tmp_path / 'results.xml'
    path.write_text('<testsuites><testsuite><testcase classname="tests.test_example" '
                    'name="test_required[first]"/></testsuite></testsuites>')
    assert checks.verify_junit(path, ['tests/test_example.py::test_required', 'tests/test_example.py']) == 1


def test_changed_feature_selects_its_owner_without_full_matrix():
    selected = checks.select_lanes(['tests/test_ci_live.py'])
    assert selected == [{'lane': 'context-intelligence', 'os': 'ubuntu-latest'}]
    assert checks.select_lanes(['README.md']) == []
    all_jobs = checks.select_lanes()
    assert checks.select_lanes(['scripts/integration_checks.py']) == all_jobs
    assert checks.select_lanes(['scripts/run_owned_integration.py']) == all_jobs
    assert checks.select_lanes(['tests/conftest.py']) == all_jobs
    assert not {'release-runtime', 'browser-installed'} & {job['lane'] for job in all_jobs}


@pytest.fixture
def release_repository(tmp_path, monkeypatch):
    """Actual Git comparisons, including complete metadata and merge-base semantics."""
    coverage = checks.manifest()
    monkeypatch.setattr(checks, 'manifest', lambda: coverage)
    monkeypatch.setattr(checks, 'ROOT', tmp_path)
    def git(*args):
        return subprocess.check_output(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                                        *args], cwd=tmp_path, text=True).strip()
    def write(path, value):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
    def commit():
        git('add', '-A')
        git('commit', '-qm', 'fixture')
        return git('rev-parse', 'HEAD')
    git('init', '-q')
    write('pyproject.toml', '[project]\nname="amplifier-unified"\nversion="1.0.0"\n'
          'dependencies=["provider>=1"]\n[build-system]\nrequires=["hatchling>=1"]\n')
    write('uv.lock', 'version=1\nrevision=3\n[[package]]\nname="amplifier-unified"\n'
          'version="1.0.0"\nsource={editable="."}\ndependencies=[{name="provider"}]\n'
          '[[package]]\nname="provider"\nversion="2.0.0"\n'
          'source={git="https://example.invalid/provider?rev=main#abc"}\n'
          'wheels=[{url="https://example.invalid/provider.whl",hash="sha256:abc"}]\n')
    write('amplifier_web/__init__.py', '"""Host."""\n__version__ = "1.0.0"\n')
    write('amplifier_web/service.py', '# existing runtime owner\n')
    base = commit()
    for path in ('pyproject.toml', 'uv.lock', 'amplifier_web/__init__.py'):
        target = tmp_path / path
        target.write_text(target.read_text().replace('1.0.0', '1.0.1'))
    write('amplifier_web/release-notes.json', '[{"version":"1.0.1"}]\n')
    write('amplifier_web/static/assets/app-new.js', '// rebuilt version\n')
    write('docs/release.md', 'Release notes\n')
    return SimpleNamespace(base=base, root=tmp_path, git=git, write=write, commit=commit)


def test_proven_release_preparation_skips_broad_lanes_but_nightly_and_manual_stay_full(release_repository):
    repo = release_repository
    head = repo.commit()
    assert checks.select_revision_lanes(repo.base, head) == []
    assert checks.frontend_test_scope(repo.base, head) == 'release-smoke'
    assert checks.select_revision_lanes(repo.base, head, include_manual=True) == []
    assert len(checks.select_lanes()) == 6
    assert len(checks.select_lanes(include_manual=True)) > 6
    # A caller supplying only filenames has no content proof and keeps old behavior.
    assert checks.select_lanes(['pyproject.toml', 'uv.lock']) == checks.select_lanes()


def test_plan_command_uses_exact_pr_comparison_only_when_supplied(release_repository, monkeypatch, capsys):
    repo = release_repository
    monkeypatch.setattr(checks.sys, 'argv', ['integration_checks.py', 'plan', '--base', repo.base,
                                         '--head', repo.commit()])
    assert checks.main() == 0
    assert json.loads(capsys.readouterr().out) == {'include': []}
    monkeypatch.setattr(checks.sys, 'argv', ['integration_checks.py', 'plan'])
    assert checks.main() == 0
    assert json.loads(capsys.readouterr().out) == {'include': checks.select_lanes()}


@pytest.mark.parametrize('path,before,after', [
    ('pyproject.toml', 'provider>=1', 'provider>=2'),
    ('pyproject.toml', 'hatchling>=1', 'hatchling>=2'),
    ('pyproject.toml', '', '\n[tool.uv.sources]\nprovider={git="https://example.invalid/other"}\n'),
    ('uv.lock', 'version="2.0.0"', 'version="2.0.1"'),
    ('uv.lock', '#abc', '#def'),
    ('uv.lock', 'sha256:abc', 'sha256:def'),
    ('uv.lock', 'revision=3', 'revision=4'),
    ('uv.lock', '', '\n[manifest]\nmembers=["unexpected"]\n'),
    ('amplifier_web/__init__.py', '', '\nimport another_runtime\n'),
])
def test_release_version_does_not_hide_dependency_build_source_or_other_metadata_changes(
        release_repository, path, before, after):
    repo = release_repository
    target = repo.root / path
    text = target.read_text()
    target.write_text(text.replace(before, after) if before else text + after)
    head = repo.commit()
    assert checks.select_revision_lanes(repo.base, head) == checks.select_lanes()
    assert checks.frontend_test_scope(repo.base, head) == 'full'


@pytest.mark.parametrize('problem', ['malformed', 'missing', 'duplicate-local', 'version-mismatch',
                                    'runtime-source', 'runtime-moved-to-docs', 'configuration'])
def test_unproven_release_changes_retain_ordinary_affected_owners(release_repository, problem):
    repo = release_repository
    if problem == 'malformed':
        repo.write('uv.lock', 'not toml')
    elif problem == 'missing':
        (repo.root / 'uv.lock').unlink()
    elif problem == 'duplicate-local':
        with (repo.root / 'uv.lock').open('a') as f:
            f.write('\n[[package]]\nname="amplifier-unified"\nversion="1.0.1"\nsource={editable="."}\n')
    elif problem == 'version-mismatch':
        repo.write('amplifier_web/__init__.py', '__version__ = "9.9.9"\n')
    elif problem == 'runtime-source':
        repo.write('amplifier_web/service.py', '# actual runtime change\n')
    elif problem == 'runtime-moved-to-docs':
        (repo.root / 'amplifier_web/service.py').rename(repo.root / 'docs/moved.py')
    else:
        repo.write('bundle.yaml', 'bundle: changed\n')
    head = repo.commit()
    assert checks.select_revision_lanes(repo.base, head) == checks.select_lanes()
    assert checks.frontend_test_scope(repo.base, head) == 'full'


def test_missing_git_comparison_cannot_establish_release_exemption(release_repository):
    repo = release_repository
    for base, head in [(repo.base, '0' * 40), ('not-a-sha', repo.commit()), (repo.base, None)]:
        assert checks.select_revision_lanes(base, head) == checks.select_lanes()
        assert checks.frontend_test_scope(base, head) == 'full'


def test_asset_only_change_and_diverged_base_do_not_hide_runtime_work(release_repository):
    repo = release_repository
    release = repo.commit()
    repo.write('amplifier_web/static/assets/app-new.js', '// a different client\n')
    assets = repo.commit()
    assert checks.select_revision_lanes(release, assets) == [{'lane': 'browser-headless', 'os': 'ubuntu-latest'}]
    assert checks.frontend_test_scope(release, assets) == 'full'
    repo.git('checkout', '-q', repo.base)
    repo.write('amplifier_web/service.py', '# new accepted base runtime\n')
    advanced_base = repo.commit()
    # Compare against the actual merge base, not unrelated later base contents.
    assert checks.select_revision_lanes(advanced_base, release) == []
    assert checks.frontend_test_scope(advanced_base, release) == 'full'


@pytest.mark.parametrize('base_changed', [False, True])
def test_frontend_scope_uses_exact_shallow_pr_trees_without_history_fetch(
        release_repository, monkeypatch, base_changed):
    repo = release_repository
    repo.commit()
    repo.write('docs/release.md', 'Second release preparation commit\n')
    head = repo.commit()
    repo.git('checkout', '-qb', 'pr-merge', repo.base)
    if base_changed:
        repo.write('amplifier_web/service.py', '# changed base runtime\n')
        base = repo.commit()
    else:
        base = repo.base
    repo.git('merge', '--no-ff', '-qm', 'tested merge', head)
    clone = repo.root.parent / (repo.root.name + '-shallow')
    subprocess.check_call(['git', 'clone', '-q', '--depth=2', repo.root.as_uri(), str(clone)])
    monkeypatch.setattr(checks, 'ROOT', clone)
    # Depth two has both merge parents, but not their full common ancestry.
    assert subprocess.run(['git', 'merge-base', base, head], cwd=clone).returncode != 0
    assert checks.frontend_test_scope(base, head) == ('full' if base_changed else 'release-smoke')
    assert checks.frontend_test_scope(base, '0' * 40) == 'full'


@pytest.mark.parametrize('path', ['frontend/src/app.js', 'frontend/package-lock.json',
                                  'frontend/tests/assets.test.mjs', 'scripts/integration_checks.py'])
def test_version_with_frontend_or_classifier_change_keeps_full_tests(release_repository, path):
    repo = release_repository
    repo.write(path, 'changed source')
    assert checks.frontend_test_scope(repo.base, repo.commit()) == 'full'


@pytest.mark.parametrize('scope,exit_code', [('full', 0), ('release-smoke', 0),
                                           ('full', 1), ('release-smoke', 1)])
def test_frontend_runner_reports_actual_successful_scope_only(monkeypatch, tmp_path, scope, exit_code):
    monkeypatch.setattr(checks, 'frontend_test_scope', lambda base, head: scope)
    calls = []
    def execute(command, cwd):
        calls.append((command, cwd))
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(checks.subprocess, 'run', execute)
    output = tmp_path / 'github-output'
    monkeypatch.setattr(checks.sys, 'argv', ['checks', 'frontend-test', '--output', str(output)])
    assert checks.main() == exit_code
    expected = (['npm', 'test', '--prefix', 'frontend'] if scope == 'full' else
                ['node', '--test', 'frontend/tests/assets.test.mjs', 'frontend/tests/build-artifacts.test.mjs'])
    assert calls == [(expected, checks.ROOT)]
    if exit_code == 0:
        assert output.read_text() == 'scope=' + scope + '\n'
    else:
        assert not output.exists()


@pytest.mark.parametrize('probe', ['warm_worker_probe.py', 'history_edit_probe.py', 'attachments_runtime_probe.py',
                                  'live_terminal_probe.py', 'standalone_controls_probe.py', 'computation_runtime.py', 'bundle_switch_probe.py',
                                  'provider_isolation_native.py'])
def test_runtime_probe_changes_select_native_owner(probe):
    assert 'native' in {row['lane'] for row in checks.select_lanes([f'tests/fixtures/{probe}'])}


def test_filesystem_process_probe_selects_mac_owner():
    assert {'lane': 'filesystem-aliases', 'os': 'macos-latest'} in checks.select_lanes(
        ['tests/fixtures/file_source_process_probe.py'])


def test_every_audited_skip_has_an_owner_and_its_test_selects_that_owner():
    coverage = checks.manifest()
    records = coverage['baselineRecords']
    assert len(records) == coverage['baseline']['skipRecords'] == 87
    assert len({record['node'] for record in records}) == 87
    assert sum(record['kind'] == 'module_collection' for record in records) == 3
    for record in records:
        lane = coverage['lanes'][record['owner']]
        assert lane['selectors']
        if lane['mode'] == 'automatic':
            selected = checks.select_lanes([record['node'].split('::')[0]])
            assert record['owner'] in {job['lane'] for job in selected}


def test_publication_stays_short_and_background_owns_integration():
    import yaml
    release = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'integration' not in release['jobs']
    post = yaml.load((ROOT / '.github/workflows/post-release.yml').read_text(), Loader=yaml.BaseLoader)
    assert post['jobs']['integrations']['uses'] == './.github/workflows/integration-checks.yml'
    assert 'integrations' in post['jobs']['report']['needs']
    workflow = yaml.load((ROOT / '.github/workflows/integration-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert {'pull_request', 'workflow_call', 'workflow_dispatch'} <= set(workflow['on'])
    assert 'pull_request_target' not in workflow['on']
    planner = next(step for step in workflow['jobs']['plan']['steps'] if step.get('id') == 'plan')
    assert planner['env']['BASE'] == '${{ github.event.pull_request.base.sha }}'
    assert planner['env']['HEAD'] == '${{ github.event.pull_request.head.sha }}'
    assert "if os.environ['EVENT'] == 'pull_request':" in planner['run']
    assert "['--base', os.environ['BASE'], '--head', os.environ['HEAD']]" in planner['run']


@pytest.mark.parametrize('changed', ['pyproject.toml', 'uv.lock', '.github/workflows/integration.yml',
    'frontend/src/main.jsx', 'frontend/tests/browser.mjs', 'frontend/package.json',
    'frontend/package-lock.json', 'frontend/vite.config.js', 'frontend/index.html',
    'amplifier_web/static/assets/main.js', 'amplifier_web/static/index.html',
    'amplifier_web/static/assets/main.css', 'amplifier_web/static/assets/viewer.wasm'])
def test_dependency_and_workflow_inputs_change_source_identity(tmp_path, monkeypatch, changed):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    (tmp_path / 'uv.lock').write_text('version=1\n')
    (tmp_path / '.github/workflows').mkdir(parents=True)
    (tmp_path / '.github/workflows/integration.yml').write_text('name: fixture\n')
    path = tmp_path / changed
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('original input\n')
    monkeypatch.setattr(checks, 'ROOT', tmp_path)
    before = checks.source_identity('fixture')
    path.write_text(path.read_text() + '\n# changed\n')
    assert checks.source_identity('fixture')['testedSourceDigest'] != before['testedSourceDigest']


def test_generated_caches_and_output_are_not_source_inputs(tmp_path, monkeypatch):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    (tmp_path / 'uv.lock').write_text('version=1\n')
    monkeypatch.setattr(checks, 'ROOT', tmp_path)
    before = checks.source_identity('fixture')
    for name in ('frontend/tests/output/result.json', 'frontend/src/node_modules/lib.js',
                 'amplifier_web/__pycache__/service.pyc'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('generated\n')
    assert checks.source_identity('fixture') == before


@pytest.mark.parametrize('outcome', ['success', 'source-changed', 'launch-error', 'pytest-failed'])
def test_run_is_source_bound_and_cannot_reuse_stale_success(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(checks, 'manifest', lambda: {'lanes': {'fixture': {
        'selectors': ['tests/test_example.py::test_required']}}})
    identities = iter([{'baseRevision': 'fixture', 'testedSourceDigest': 'before'},
                       {'baseRevision': 'fixture', 'testedSourceDigest': 'after' if outcome == 'source-changed' else 'before'}])
    monkeypatch.setattr(checks, 'source_identity', lambda _: next(identities))
    junit = tmp_path / 'junit.xml'
    receipt = tmp_path / 'result.json'
    receipt.write_text('{"passed": true}')
    junit.write_text('<stale/>')
    def execute(*args, **kwargs):
        assert not junit.exists() and not receipt.exists()
        if outcome == 'launch-error':
            raise FileNotFoundError('interpreter missing')
        junit.write_text('<testsuites><testsuite><testcase classname="tests.test_example" '
                         'name="test_required"/></testsuite></testsuites>')
        return SimpleNamespace(returncode=2 if outcome == 'pytest-failed' else 0)
    monkeypatch.setattr(checks.subprocess, 'run', execute)
    code = checks.run_lane('fixture', 'python', tmp_path, revision='fixture')
    result = json.loads(receipt.read_text())
    assert result['passed'] is (outcome == 'success')
    assert code == (0 if outcome == 'success' else 1)
    if outcome == 'source-changed':
        assert result['sourceAfter']['testedSourceDigest'] == 'after'
        assert 'changed' in result['error']
    if outcome == 'launch-error':
        assert not junit.exists() and result['exitCode'] is None
        assert 'interpreter missing' in result['error']


def test_new_runtime_owners_are_additive_to_frozen_skip_baseline():
    import hashlib
    coverage = checks.manifest()
    # Do not rewrite the historical audit when later features add optional cases.
    frozen = json.dumps({'baseline': coverage['baseline'],
                         'records': coverage['baselineRecords']}, sort_keys=True).encode()
    assert hashlib.sha256(frozen).hexdigest() == '8356817d31275554bf00c526c4f76b467646d2146f06907cd0ed564ac455b9f0'
    records = coverage['additionalRecords']
    expected = {
        'tests/test_runtime.py::test_worker_parking_releases_the_real_shared_handle_and_reacquires_unchanged': 'native',
        'tests/test_session_handoff.py::test_unified_failed_save_retains_lock_and_blocks_more_input': 'native',
        'tests/test_worktrees.py::test_runtime_cooperative_release_is_confirmed_without_replay': 'native',
        'tests/test_handoff_native_admission.py::test_actual_native_turn_completes_handoff_without_self_wait': 'native',
        'tests/test_work_origin.py': 'native',
        'tests/test_work_native_effects.py': 'native',
        **{f'tests/test_host_components.py::test_dynamic_and_resumed_child_plan_reuses_host_sources_and_instances[{resumed}]': 'native'
           for resumed in ('False', 'True')},
        'tests/test_search_read_policy.py': 'native',
        'tests/test_default_capability_policy.py': 'native',
        'tests/test_default_export_literals.py::test_literal_examples_survive_foundation_export_roundtrip': 'native',
        **{f'tests/test_{name}.py': 'default-installed' for name in (
            'default_behaviors_integration', 'default_runtime_safety',
            'default_mount_runtime', 'default_routing_runtime')},
        'tests/fixtures/status_emitter_probe.py': 'status-installed',
        'tests/fixtures/status_package_probe.py': 'status-installed',
        'tests/test_default_desktop_safety.py': 'default-desktop-safety',
        'tests/test_browser_auth.py::test_installed_pwa_launch': 'browser-installed',
        **{'tests/test_durable_guest_jobs.py::test_' + name: 'root-guest' for name in (
            'lost_response_repeated_identity_and_fresh_observer_do_not_replay',
            'cancel_is_prompt_and_terminal_requires_actual_exit',
            'timeout_is_enforced_in_guest_and_output_is_bounded',
            'worker_cannot_modify_supervisor_receipts_and_profile_environment_survives',
            'two_roots_execute_independently_and_cannot_adopt_identity',
            'dead_supervisor_is_unknown_and_same_request_never_restarts',
            'crash_after_reservation_does_not_launch_on_observe_or_repeat',
            'background_pipe_inheritance_does_not_hold_finished_check_until_deadline')},
    }
    assert {row['node']: row['owner'] for row in records} == expected
    assert len(records) == len(expected)
    for row in records:
        lane = coverage['lanes'][row['owner']]
        assert row['node'] in lane['selectors'] and row['prerequisite']
        if lane['mode'] == 'automatic':
            assert row['owner'] in {job['lane'] for job in checks.select_lanes([row['node'].split('::')[0]])}
    assert coverage['lanes']['default-installed']['mode'] == 'manual'
    assert 'default-installed' not in {job['lane'] for job in checks.select_lanes()}


def test_status_installed_owner_is_explicit_and_affected_changes_are_discoverable(tmp_path):
    coverage = checks.manifest()
    lane = coverage['lanes']['status-installed']
    assert lane['mode'] == 'manual'
    assert lane['command'] == ['python', 'scripts/qualify_status_emitter.py']
    assert 'status-installed' not in {job['lane'] for job in checks.select_lanes()}
    for path in ['amplifier_web/status_emitter.py', 'amplifier_web/status_protocol/hook.py',
                 'amplifier_web/execution_events.py', 'tests/fixtures/status_package_probe.py']:
        assert 'status-installed' in {job['lane'] for job in checks.select_lanes([path], include_manual=True)}
    assert 'status-installed' not in {job['lane'] for job in checks.select_lanes(['README.md'], include_manual=True)}
    with pytest.raises(ValueError, match='explicit qualification runner'):
        checks.run_lane('status-installed', 'python', tmp_path)


def test_desktop_safety_owner_uses_its_manual_runner_without_changing_ic13(tmp_path):
    coverage = checks.manifest()
    lane = coverage['lanes']['default-desktop-safety']
    assert lane['runner'] == 'owned-container'
    assert lane['mode'] == 'manual'
    assert lane['command'] == [
        'python', 'scripts/qualify_default_installed.py', 'run', '--desktop-safety']
    assert lane['selectors'] == ['tests/test_default_desktop_safety.py']
    assert 'default-desktop-safety' not in {job['lane'] for job in checks.select_lanes()}
    for path in ['tests/test_default_desktop_safety.py', 'amplifier_web/host/children.py',
                 'scripts/qualify_default_installed.py', 'scripts/default-installed-pins.json']:
        assert 'default-desktop-safety' in {
            job['lane'] for job in checks.select_lanes([path], include_manual=True)}
        assert 'default-desktop-safety' not in {
            job['lane'] for job in checks.select_lanes([path])}
    assert 'default-desktop-safety' not in {
        job['lane'] for job in checks.select_lanes(['README.md'], include_manual=True)}
    assert coverage['lanes']['default-installed']['selectors'] == [
        'tests/test_default_behaviors_integration.py',
        'tests/test_default_runtime_safety.py',
        'tests/test_default_mount_runtime.py',
        'tests/test_default_routing_runtime.py',
    ]
    output = tmp_path / 'not-generic-pytest'
    with pytest.raises(ValueError, match='explicit qualification runner'):
        checks.run_lane('default-desktop-safety', 'python', output)
    assert not output.exists()


def test_new_default_and_search_skips_cannot_be_ownerless():
    import ast
    coverage = checks.manifest()
    registered = {row['node'] for row in coverage['additionalRecords']}
    paths = [*ROOT.glob('tests/test_default_*.py'), ROOT / 'tests/test_search_read_policy.py']
    for path in paths:
        tree = ast.parse(path.read_text())
        file = path.relative_to(ROOT).as_posix()
        for statement in tree.body:
            skipped = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                          and isinstance(node.func.value, ast.Name) and node.func.value.id == 'pytest'
                          and node.func.attr in {'skip', 'importorskip'} for node in ast.walk(statement))
            if not skipped:
                continue
            function = statement.name if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) else ''
            assert file in registered or (function.startswith('test_') and file + '::' + function in registered), (
                f'New optional runtime check has no additive execution owner: {file}:{function}')


def test_native_search_owner_prepares_actual_ripgrep():
    import yaml
    workflow = yaml.load((ROOT / '.github/workflows/integration-checks.yml').read_text(), Loader=yaml.BaseLoader)
    setup = next(step for step in workflow['jobs']['integration']['steps']
                 if step.get('name') == 'Prepare actual search backend')
    assert setup['if'] == "matrix.lane == 'native'"
    assert 'apt-get install -y ripgrep' in setup['run']


def test_root_and_installed_pwa_have_separate_fixed_manual_routes(tmp_path):
    coverage = checks.manifest()
    root = coverage['lanes']['root-guest']
    assert root['mode'] == 'manual' and root['runner'] == 'owned-container'
    assert root['command'] == ['python', 'scripts/qualify_default_installed.py', 'run', '--root-guest']
    assert len(root['selectors']) == len(set(root['selectors'])) == 8
    pwa = coverage['lanes']['browser-installed']
    assert pwa['mode'] == 'manual' and pwa['selectors'] == [
        'tests/test_browser_auth.py::test_installed_pwa_launch']
    assert pwa['qualificationCommand'] == [
        'python', 'scripts/qualify_default_installed.py', 'run', '--installed-pwa']
    assert not {'root-guest', 'browser-installed'} & {row['lane'] for row in checks.select_lanes()}
    assert {'root-guest', 'browser-installed'} <= {
        row['lane'] for row in checks.select_lanes(['scripts/qualify_default_installed.py'], include_manual=True)}
    with pytest.raises(ValueError, match='explicit qualification runner'):
        checks.run_lane('root-guest', 'python', tmp_path / 'not-a-host-root-run')
    assert not (tmp_path / 'not-a-host-root-run').exists()


def test_handoff_native_admission_has_an_executing_owner_and_coordination_guard():
    import yaml
    coverage = checks.manifest()
    selector = 'tests/test_handoff_native_admission.py::test_actual_native_turn_completes_handoff_without_self_wait'
    assert selector in coverage['lanes']['native']['selectors']
    for path in (
        'amplifier_web/worktrees.py', 'amplifier_web/handoff_boundary.py',
        'amplifier_web/handoff_admission.py', 'amplifier_web/capacity.py',
        'amplifier_web/capacity_wait.py', 'amplifier_web/execution_events.py',
        'tests/test_handoff_native_admission.py', 'tests/test_handoff_model_admission.py',
        'tests/test_handoff_boundary.py', 'tests/test_handoff_refusal.py',
        'tests/test_model_admission_wait.py',
    ):
        assert 'native' in {job['lane'] for job in checks.select_lanes([path])}
    workflow = yaml.load((ROOT / '.github/workflows/coordination.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'tests/test_handoff_model_admission.py' in workflow['on']['pull_request']['paths']
    steps = workflow['jobs']['contracts']['steps']
    assert any('uv run --no-sync pytest' in step.get('run', '') and
               'tests/test_handoff_model_admission.py' in step['run'] for step in steps)


def test_shared_work_native_modules_have_one_additive_execution_owner():
    coverage = checks.manifest()
    native = coverage['lanes']['native']
    assert native['mode'] == 'automatic'
    for selector in ('tests/test_work_origin.py', 'tests/test_work_native_effects.py'):
        assert native['selectors'].count(selector) == 1
        assert [name for name, lane in coverage['lanes'].items()
                if selector in lane['selectors']] == ['native']
        record, = [row for row in coverage['additionalRecords'] if row['node'] == selector]
        assert record['owner'] == 'native' and record['kind'] == 'test_module'
        assert record['introducedBy'] == '#894' and record['prerequisite']


@pytest.mark.parametrize('path', [
    'amplifier_web/workspace_work.py',
    'amplifier_web/workspace_work_coverage.py',
    'amplifier_web/workspace_work_evidence.py',
    'amplifier_web/workspace_work_invocation.py',
    'amplifier_web/workspace_work_effects.py',
    'amplifier_web/coordination_delivery.py',
    'amplifier_web/app_guidance.py',
    'tests/test_work_origin.py',
    'tests/test_work_native_effects.py',
    'tests/test_work_invocation.py',
    'tests/test_work_effects.py',
    'tests/test_workspace_work.py',
    'tests/test_coordination_delivery.py',
    'tests/test_collaborative_workspaces.py',
    'tests/test_peer_creation_operation.py',
])
def test_shared_work_changes_select_only_existing_native_owner(path):
    # Checking membership alone would also pass an accidental all-lanes rule.
    assert checks.select_lanes([path]) == [{'lane': 'native', 'os': 'ubuntu-latest'}]


@pytest.mark.parametrize('path', [
    'amplifier_web/workspace_navigation.py',
    'tests/test_workspace_navigation.py',
    'frontend/src/shared-work-browser.jsx',
    'docs/validation/integration-coverage.md',
])
def test_shared_work_registration_does_not_expand_native_to_unrelated_paths(path):
    assert 'native' not in {row['lane'] for row in checks.select_lanes([path])}
