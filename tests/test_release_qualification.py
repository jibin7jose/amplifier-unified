"""Release checks must qualify and publish one immutable candidate."""
import importlib.util
import json
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('qualification', ROOT / 'scripts/release_qualification.py')
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


@pytest.fixture
def selected(tmp_path, monkeypatch):
    evidence = tmp_path / 'host'
    evidence.mkdir()
    qualification.write(evidence / 'resolution.json', {'ok': True, 'resolved': [{'name': 'example', 'version': '1.0'}]})
    (evidence / 'qualified-overrides.txt').write_text('example==1.0\n')
    (evidence / 'development.lock').write_text('development inputs')
    app = {'revision': 'a' * 40, 'version': '1.2.3'}
    monkeypatch.setattr(qualification, 'identity', lambda root: app.copy())
    expected = qualification.candidate(tmp_path, evidence)
    return tmp_path, evidence, expected, app


@pytest.mark.parametrize('change', ['app', 'host', 'candidate'])
def test_candidate_refuses_changed_commit_graph_or_receipt(selected, change):
    root, evidence, expected, app = selected
    qualification.verify_candidate(root, evidence, expected)
    if change == 'app':
        app['revision'] = 'b' * 40
    elif change == 'host':
        (evidence / 'qualified-overrides.txt').write_text('example==2.0\n')
    else:
        value = json.loads((evidence / 'candidate.json').read_text())
        value['version'] = '9.9.9'
        qualification.write(evidence / 'candidate.json', value)
    with pytest.raises(ValueError):
        qualification.verify_candidate(root, evidence, expected)


@pytest.fixture
def qualified(selected, monkeypatch):
    root, evidence, expected, app = selected
    dist = root / 'dist'
    dist.mkdir()
    for name in ['app.whl', 'app.tar.gz', 'SHA256SUMS']:
        (dist / name).write_text(name)
    runtime = root / 'runtime'
    runtime.mkdir()
    snapshot = {'build': 'build-hash', 'recipes': 'b' * 40, 'graph': [{'name': 'core', 'version': '2.0'}]}
    qualification.write(runtime / 'qualified-runtime.json', snapshot)
    monkeypatch.setattr(qualification, 'runtime_snapshot', lambda *args: snapshot.copy())
    receipts = root / 'receipts'
    receipts.mkdir()
    qualification.write(receipts / 'qualified-runtime.json', snapshot)
    for lane in qualification.LANES:
        qualification.receipt(root, evidence, expected, lane, receipts / (lane + '.json'), runtime, dist)
    return root, evidence, expected, receipts, dist, runtime, snapshot


def test_all_three_receipts_and_unchanged_distributions_are_required(qualified):
    root, evidence, expected, receipts, dist, *_ = qualified
    qualification.verify_receipts(root, evidence, expected, receipts, dist)
    (receipts / 'frontend.json').unlink()
    with pytest.raises(FileNotFoundError):
        qualification.verify_receipts(root, evidence, expected, receipts, dist)


def test_old_browser_receipt_cannot_replace_frontend_qualification(qualified):
    root, evidence, expected, receipts, dist, *_ = qualified
    (receipts / 'frontend.json').rename(receipts / 'browser.json')
    with pytest.raises(FileNotFoundError):
        qualification.verify_receipts(root, evidence, expected, receipts, dist)


def test_old_python_receipt_cannot_replace_package_qualification(qualified):
    root, evidence, expected, receipts, dist, *_ = qualified
    (receipts / 'package.json').rename(receipts / 'python.json')
    with pytest.raises(FileNotFoundError):
        qualification.verify_receipts(root, evidence, expected, receipts, dist)


@pytest.mark.parametrize('change', ['other-candidate', 'wrong-lane', 'wheel', 'extra-artifact', 'runtime'])
def test_promotion_rejects_mixed_or_tampered_evidence(qualified, change):
    root, evidence, expected, receipts, dist, *_ = qualified
    if change in {'other-candidate', 'wrong-lane'}:
        value = json.loads((receipts / 'frontend.json').read_text())
        value['candidate' if change == 'other-candidate' else 'lane'] = 'other'
        qualification.write(receipts / 'frontend.json', value)
    elif change == 'wheel':
        (dist / 'app.whl').write_text('different bytes')
    elif change == 'extra-artifact':
        (dist / 'unexpected.whl').write_text('different artifact')
    else:
        qualification.write(receipts / 'qualified-runtime.json', {'graph': []})
    with pytest.raises(ValueError):
        qualification.verify_receipts(root, evidence, expected, receipts, dist)


@pytest.mark.parametrize('change', ['graph', 'native-binary'])
def test_runtime_changes_after_tests_cannot_produce_a_receipt(qualified, change):
    root, evidence, expected, receipts, dist, runtime, snapshot = qualified
    if change == 'graph':
        snapshot['graph'] = [{'name': 'core', 'version': 'new-untested-version'}]
    else:
        snapshot['coreWheel'] = {'native': [{'sha256': 'changed-binary'}]}
    with pytest.raises(ValueError, match='changed during qualification'):
        qualification.receipt(root, evidence, expected, 'runtime', receipts / 'runtime.json', runtime, dist)


@pytest.fixture
def runtime_build(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    for name in ['uv.lock', 'pyproject.toml', 'build-constraints.txt']:
        (runtime / name).write_text(name)
    monkeypatch.setattr(qualification, 'checkout_identity', lambda path: 'a' * 40)
    monkeypatch.setattr(qualification, 'run', lambda *args: ' '.join(args))
    monkeypatch.setattr(qualification.importlib.metadata, 'version', lambda name: '1.9.0')
    return tmp_path, runtime


@pytest.mark.parametrize('change', ['resolved-source', 'loop-live', 'recipes', 'python-abi', 'architecture', 'rustc', 'maturin', 'build-flags'])
def test_cache_identity_changes_with_every_build_input(runtime_build, monkeypatch, change):
    root, runtime = runtime_build
    before = qualification.digest(qualification.build_identity(root, runtime))
    if change == 'resolved-source':
        (runtime / 'uv.lock').write_text('same main declaration, new resolved commit')
    elif change in {'loop-live', 'recipes'}:
        monkeypatch.setattr(qualification, 'checkout_identity', lambda path: ('b' if path.name == change else 'a') * 40)
    elif change == 'python-abi':
        monkeypatch.setattr(qualification.sysconfig, 'get_config_var', lambda name: 'different-abi')
    elif change == 'architecture':
        monkeypatch.setattr(qualification.platform, 'machine', lambda: 'different-architecture')
    elif change == 'rustc':
        monkeypatch.setattr(qualification, 'run', lambda *args: 'changed toolchain' if args[0] == 'rustc' else ' '.join(args))
    elif change == 'maturin':
        monkeypatch.setattr(qualification.importlib.metadata, 'version', lambda name: '2.0.0')
    else:
        monkeypatch.setenv('RUSTFLAGS', '-C opt-level=1')
    assert qualification.digest(qualification.build_identity(root, runtime)) != before


def test_cache_identity_does_not_include_credentials(runtime_build, monkeypatch):
    root, runtime = runtime_build
    before = qualification.build_identity(root, runtime)
    monkeypatch.setenv('LOOP_LIVE_CI_READ_KEY', 'private-key-never-export')
    monkeypatch.setenv('GH_TOKEN', 'token-never-export')
    after = qualification.build_identity(root, runtime)
    assert before == after
    assert 'never-export' not in json.dumps(after)
    monkeypatch.setenv('CFLAGS', '-DPRIVATE_TOKEN=private-definition-never-export')
    with_flags = qualification.build_identity(root, runtime)
    assert with_flags != after
    assert 'never-export' not in json.dumps(with_flags)


@pytest.mark.parametrize('change', ['none', 'source-install', 'missing-wheel', 'pure-python', 'missing-native', 'cli'])
def test_core_wheel_receipt_rejects_source_or_cli_installations(monkeypatch, change):
    value = {'version': '2.0.1', 'direct': None, 'wheel': 'Root-Is-Purelib: false\nTag: cp311-abi3-manylinux_2_17_x86_64',
             'native': [{'name': 'amplifier_core/_engine.abi3.so', 'sha256': 'a' * 64}], 'cliInstalled': False}
    if change == 'source-install':
        value['direct'] = {'url': 'https://github.com/microsoft/amplifier-core', 'vcs_info': {'vcs': 'git'}}
    elif change == 'missing-wheel':
        value['wheel'] = None
    elif change == 'pure-python':
        value['wheel'] = 'Root-Is-Purelib: true'
    elif change == 'missing-native':
        value['native'] = []
    elif change == 'cli':
        value['cliInstalled'] = True
    monkeypatch.setattr(qualification, 'run', lambda *args: json.dumps(value))
    if change == 'none':
        assert qualification.core_wheel(Path('python')) == value
    else:
        with pytest.raises(ValueError, match='registry native wheel'):
            qualification.core_wheel(Path('python'))


def test_runtime_project_constrains_the_recorded_build_backend(tmp_path, monkeypatch):
    source = tmp_path / 'amplifier_web/runtime_deps'
    source.mkdir(parents=True)
    (source / 'pyproject.toml').write_text('[project]\nname="fixture"\n[tool.uv]\noverride-dependencies=[]\n')
    monkeypatch.setattr(qualification.importlib.metadata, 'version', lambda name: '1.9.0')
    runtime = tmp_path / 'runtime'
    qualification.runtime_project(tmp_path, runtime)
    assert 'build-constraint-dependencies = ["maturin==1.9.0"]' in (runtime / 'pyproject.toml').read_text()
    assert (runtime / 'build-constraints.txt').read_text() == 'maturin==1.9.0\n'


def test_workflow_consolidates_checks_without_sharing_publication_credentials():
    workflow = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
    jobs = workflow['jobs']
    assert set(jobs) == {'prepare', 'release'}
    assert workflow['permissions'] == {'contents': 'read'}
    assert jobs['prepare']['permissions'] == {'contents': 'read', 'actions': 'read'}
    assert jobs['release']['needs'] == 'prepare'
    assert jobs['release']['permissions'] == {'contents': 'write'}
    steps = jobs['prepare']['steps']
    commands = '\n'.join(step.get('run', '') for step in steps)
    assert commands.count('uv sync --locked --group dev --group artifacts') == 1
    assert 'Restore and verify the exact candidate host graph' not in str(steps)
    for lane in qualification.LANES:
        assert f'--lane {lane}' in commands
    # Distribution bytes and their receipt are frozen before isolated native setup.
    assert commands.index('uv build') < commands.index('--lane package') < commands.index('runtime-project')
    assert commands.index('--lane frontend') < commands.index('runtime-project')
    assert commands.count('qualify_host_dependencies.py --verify') == 3
    assert steps[0]['with']['fetch-depth'] == '0'
    private = next(s for s in steps if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    assert private['with']['persist-credentials'] == 'false'
    assert private['with']['ssh-key'] == '${{ secrets.LOOP_LIVE_CI_READ_KEY }}'
    steps = jobs['release']['steps']
    assert steps[0]['with'] == {
        'ref': '${{ needs.prepare.outputs.revision }}', 'fetch-depth': '1'}
    assert all('pytest' not in s.get('run', '') and 'uv pip install' not in s.get('run', '') for s in steps)
    verify = next(i for i, step in enumerate(steps) if 'verify-receipts' in step.get('run', ''))
    publish = next(i for i, step in enumerate(steps) if 'publish --tag' in step.get('run', ''))
    assert verify < publish


def test_broad_checks_are_nightly_or_explicit_and_keep_distinct_package_boundaries():
    workflows = ROOT / '.github/workflows'
    manual = yaml.load((workflows / 'browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(manual['on']) == {'workflow_dispatch', 'workflow_call'}
    assert manual['permissions'] == {'contents': 'read'}
    commands = '\n'.join(step.get('run', '') for step in manual['jobs']['browser']['steps'])
    assert 'playwright install' in commands
    assert 'scripts/browser_check_runner.py' in commands
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    invocations = [row['command'] for row in plan['scenarios']]
    assert ['node', 'tests/session-health-browser.mjs'] in invocations
    assert ['node', 'tests/session-health-browser.mjs', '--context-limit', '--active-worker'] in invocations
    assert ['node', 'tests/connectors-browser.mjs'] in invocations
    critical = yaml.load((workflows / 'public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(critical['on']) == {'workflow_dispatch', 'workflow_call'}
    assert critical['permissions'] == {'contents': 'read'}
    steps = critical['jobs']['critical']['steps']
    assert steps[0]['with']['ref'] == '${{ inputs.revision || github.sha }}'
    commands = '\n'.join(step.get('run', '') for step in steps)
    # Unit subsets duplicate the full Python lane / qualified frontend graph.
    assert 'pytest' not in commands and 'npm test' not in commands
    for check in ('recovery-group-browser.mjs', 'chat-menu-browser.mjs', 'public-first-chat-browser.mjs',
                  'inline-media-browser.mjs', 'uv pip install'):
        assert check in commands
    installed_media = next(step for step in steps if step.get('name') == 'Check retained inline media in installed package')
    assert installed_media['env']['INLINE_MEDIA_MODE'] == 'installed'
    release = yaml.load((workflows / 'release.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'public-readiness' not in release['jobs']
    post = yaml.load((workflows / 'post-release.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(post['on']) == {'workflow_run', 'workflow_dispatch', 'schedule'}
    assert post['on']['schedule'] == [{'cron': '0 1 * * *', 'timezone': 'America/Los_Angeles'}]
    # One broad schedule; unrelated maintenance schedules remain permitted.
    broad_owners = {'public-readiness.yml', 'python-checks.yml', 'browser-checks.yml', 'integration-checks.yml'}
    scheduled = []
    for path in workflows.glob('*.yml'):
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        callees = {Path(job.get('uses', '')).name for job in workflow.get('jobs', {}).values()}
        if 'schedule' in workflow.get('on', {}) and (path.name in broad_owners or callees & broad_owners):
            scheduled.append(path.name)
    assert scheduled == ['post-release.yml']
    assert post['on']['workflow_run'] == {'workflows': ['Publish application release'], 'types': ['completed']}
    assert post['on']['workflow_dispatch']['inputs'] == {'publication_run_id': {
        'description': 'Successful main publisher run whose existing release will be verified',
        'required': 'true', 'type': 'string'}, 'broad': {
        'description': 'Explicit broad investigation; ordinary releases run installed-package smoke only',
        'required': 'false', 'type': 'boolean', 'default': 'false'}}
    assert post['permissions'] == {'contents': 'read'}
    for lane in ('readiness', 'python', 'browser', 'integrations'):
        # An independently verified identity permits nightly diagnostics even if
        # the installed-package smoke fails; cancelled/unresolved runs do not.
        assert ' '.join(post['jobs'][lane]['if'].split()) == (
            "${{ !cancelled() && needs.resolve.outputs.identity_result == 'success' && "
            "(github.event_name == 'schedule' || (github.event_name == 'workflow_dispatch' && inputs.broad)) }}")
        assert post['jobs'][lane]['needs'] == 'resolve'
        assert post['jobs'][lane]['with']['revision'] == '${{ needs.resolve.outputs.revision }}'
        if lane in ('python', 'integrations'):
            if lane == 'python':
                assert post['jobs'][lane]['permissions'] == {'contents': 'read', 'actions': 'read'}
            assert post['jobs'][lane]['secrets'] == {
                'LOOP_LIVE_CI_READ_KEY': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}'}
        else:
            assert 'secrets' not in post['jobs'][lane]
    resolve = post['jobs']['resolve']
    assert "conclusion == 'success'" in resolve['if']
    assert 'head_repository.full_name == github.repository' in resolve['if']
    assert 'head_branch' in resolve['if']
    identity = next(s for s in resolve['steps'] if s.get('id') == 'release')
    assert identity['run'] == 'python scripts/post_release_checks.py resolve --output "$GITHUB_OUTPUT"'
    assert identity['env'] == {'GH_TOKEN': '${{ github.token }}'}
    assert resolve['steps'][-1]['run'] == 'python scripts/post_release_checks.py report --summary-only'
    assert any(s.get('id') == 'install' for s in resolve['steps'])
    assert 'installed-package' not in post['jobs']
    assert release['jobs']['prepare']['steps'][-1]['with']['name'] == 'host-source-resolution'
    assert post['jobs']['report']['permissions'] == {'contents': 'read', 'issues': 'write'}
    assert 'always()' in post['jobs']['report']['if']
    assert set(post['jobs']['report']['needs']) == {'resolve', 'readiness', 'python', 'browser', 'integrations'}
    frontend = '\n'.join(step.get('run', '') for step in release['jobs']['prepare']['steps'])
    assert 'npm test --prefix frontend' in frontend
    assert 'npm run build --prefix frontend' in frontend
    assert 'git diff --exit-code -- amplifier_web/static' in frontend


def test_post_release_python_caller_covers_callee_permissions_without_widening_other_jobs():
    workflows = ROOT / '.github/workflows'
    post = yaml.load((workflows / 'post-release.yml').read_text(), Loader=yaml.BaseLoader)
    python = yaml.load((workflows / 'python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert post['permissions'] == python['permissions'] == {'contents': 'read'}
    assert {name: job['permissions'] for name, job in post['jobs'].items() if 'permissions' in job} == {
        'resolve': {'contents': 'read', 'actions': 'read'},
        'python': {'contents': 'read', 'actions': 'read'},
        'report': {'contents': 'read', 'issues': 'write'}}
    caller = post['jobs']['python']
    assert caller['uses'] == './.github/workflows/python-checks.yml'
    assert caller['with'] == {'revision': '${{ needs.resolve.outputs.revision }}'}
    for job in python['jobs'].values():
        for scope, level in job.get('permissions', python['permissions']).items():
            assert caller['permissions'].get(scope) == level
    # GitHub validates the permission ceiling even for this skipped manual job.
    guarded = python['jobs']['goal-peer-publisher-0410']
    assert guarded['permissions'] == {'contents': 'read', 'actions': 'read'}
    assert guarded['if'] == "${{ github.event_name == 'workflow_dispatch' && !inputs.core_wheels && inputs.suite == 'goal-peer-publisher-0410' }}"
    assert python['on']['workflow_call']['inputs'] == {'revision': {'type': 'string', 'required': 'true'}}


def test_post_release_manual_guard_and_report_share_verified_identity():
    post = yaml.load((ROOT / '.github/workflows/post-release.yml').read_text(), Loader=yaml.BaseLoader)
    resolve, report = post['jobs']['resolve'], post['jobs']['report']
    automatic = ("github.event_name == 'workflow_run' && "
                 "github.event.workflow_run.conclusion == 'success' && "
                 "github.event.workflow_run.head_branch == 'main' && "
                 "github.event.workflow_run.head_repository.full_name == github.repository")
    manual = "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'"
    nightly = "github.event_name == 'schedule' && github.ref == 'refs/heads/main'"
    assert ' '.join(resolve['if'].split()) == f'({nightly}) || ({manual}) || ({automatic})'
    assert ' '.join(report['if'].split()) == (
        f"always() && (needs.resolve.result != 'success' || github.event_name != 'workflow_run') && "
        f'(({nightly}) || ({manual}) || ({automatic}))')
    assert resolve['outputs'] == {
        **{name: '${{ steps.release.outputs.' + name + ' }}'
           for name in ('publication_run_id', 'publication_run_attempt', 'revision', 'tag', 'version')},
        'identity_result': '${{ steps.release.outcome }}',
        'install_result': '${{ steps.install.outcome }}'}
    assert post['concurrency'] == {
        'group': "post-release-${{ github.event_name == 'schedule' && 'nightly' || github.event.workflow_run.id || github.run_id }}", 'cancel-in-progress': 'false'}
    assert report['concurrency'] == {
        'group': 'post-release-report-${{ needs.resolve.outputs.tag || needs.resolve.outputs.publication_run_id || github.run_id }}',
        'cancel-in-progress': 'false'}
    assert report['steps'][-1]['env']['VERIFICATION_SCOPE'] == (
        "${{ (github.event_name == 'schedule' || inputs.broad) && 'broad' || 'smoke' }}")
    assert report['steps'][-1]['env']['RELEASE_RUN_ID'] == '${{ needs.resolve.outputs.publication_run_id }}'
    assert report['steps'][-1]['env']['RELEASE_RESULTS'] == '${{ toJSON(needs) }}'
    assert report['steps'][-1]['run'] == 'python scripts/post_release_checks.py report'
    for job in post['jobs'].values():
        for step in job.get('steps', []):
            assert '${{' not in step.get('run', '')
    assert 'inputs.publication_run_id' not in str(post['jobs'])


def test_installed_media_diagnostic_is_explicit_full_scenario_and_cannot_replace_readiness():
    workflow = yaml.load((ROOT / '.github/workflows/public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    choice = workflow['on']['workflow_dispatch']['inputs']['installed_media_diagnostic']
    assert choice['type'] == 'boolean' and choice['default'] == 'false'
    assert set(workflow['on']['workflow_call']['inputs']) == {'revision'}
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['jobs']['critical']['if'] == '${{ !inputs.installed_media_diagnostic }}'
    job = workflow['jobs']['installed-media-diagnostic']
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && inputs.installed_media_diagnostic }}"
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '30'
    assert job['env'] == {'INLINE_MEDIA_MODE': 'installed'}
    assert 'INLINE_MEDIA_PHASES' not in job['env']
    steps = job['steps']
    assert steps[0]['with'] == {'ref': '${{ github.sha }}', 'fetch-depth': '0', 'persist-credentials': 'false'}
    assert "'git', 'diff', '--name-only', '-z'" in steps[1]['run']
    assert "'a5d1ab2397a00f76495c591835b5c9194e09f5a8', 'HEAD'" in steps[1]['run']
    assert 'assert changed <= allowed' in steps[1]['run']
    assert "'git', 'status', '--porcelain'" in steps[1]['run']
    # runner context is unavailable in job-level env. Use the runner's shell
    # variables in an ordinary step, before any path consumer executes.
    initialize = next(i for i, step in enumerate(steps)
                      if step.get('name') == 'Initialize owned diagnostic paths')
    assert initialize == 2 and steps[initialize]['shell'] == 'bash'
    assert steps[initialize]['run'].splitlines() == [
        "printf '%s\\n' \\",
        '  "INLINE_MEDIA_EVIDENCE_DIR=$RUNNER_TEMP/inline-media-diagnostic" \\',
        '  "INLINE_MEDIA_MP4=$RUNNER_TEMP/inline-media-diagnostic/synthetic.mp4" >> "$GITHUB_ENV"',
    ]
    assert '${{' not in steps[initialize]['run']
    commands = '\n'.join(step.get('run', '') for step in steps)
    assert 'uv sync --locked --group dev --group artifacts' in commands
    assert 'npm run build --prefix frontend' in commands
    assert 'e4a71c46fd41ecf6' in commands and 'staticSha256' in commands
    assert 'assert len(files) == 311' in commands
    assert "assert payload.hexdigest() == '1b4c114ffe44d4c7a5dcf8a2136fb363847feb4ec4b27aa5bbc5fe2435deb337'" in commands
    assert 'uv pip install --python .venv/bin/python --no-deps --reinstall .' in commands
    assert commands.count('node frontend/tests/inline-media-browser.mjs') == 1
    assert "result['fullScenario'] and result['coreChecks'] == 'PASS'" in commands
    assert 'INLINE_MEDIA_PHASES' not in commands and 'secrets.' not in str(job)
    upload = next(step for step in steps if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02')
    assert upload['if'] == "always() && steps.diagnostic-evidence.outcome == 'success'"
    assert upload['with']['path'].splitlines() == [
        '${{ runner.temp }}/inline-media-diagnostic/*.json',
        '${{ runner.temp }}/inline-media-diagnostic/*.png']
    assert upload['with']['if-no-files-found'] == 'error'


@pytest.mark.parametrize('extra', [None, 'amplifier_outputs/__init__.py',
                                  'frontend/index.html', 'assets/logo.svg', 'bundle.yaml'])
def test_media_diagnostic_product_guard_rejects_unlisted_changes(monkeypatch, extra):
    import subprocess
    workflow = yaml.load((ROOT / '.github/workflows/public-readiness.yml').read_text(), Loader=yaml.BaseLoader)
    guard = workflow['jobs']['installed-media-diagnostic']['steps'][1]['run']
    script = compile('\n'.join(guard.splitlines()[1:-1]), 'diagnostic-source-guard', 'exec')
    changed = ['.github/workflows/public-readiness.yml',
               'frontend/tests/inline-media-browser.mjs', 'tests/test_release_qualification.py']
    if extra:
        changed.append(extra)
    def git(args, **kwargs):
        assert args[:2] in (['git', 'diff'], ['git', 'status'])
        return '\0'.join(changed) + '\0' if args[1] == 'diff' else ''
    monkeypatch.setattr(subprocess, 'check_output', git)
    if extra:
        with pytest.raises(AssertionError):
            exec(script, {})
    else:
        exec(script, {})


@pytest.mark.parametrize(('click_attempted', 'primary_failed', 'click_settled', 'allowed'), [
    (False, False, None, True),
    (False, True, None, True),
    (True, False, None, False),
    (True, False, False, False),
    (True, False, True, True),
    (True, True, False, False),
    (True, True, True, False),
])
def test_media_diagnostic_cleanup_refuses_unknown_ui_command(
    click_attempted, primary_failed, click_settled, allowed,
):
    import re
    import subprocess
    source = (ROOT / 'frontend/tests/inline-media-browser.mjs').read_text()
    helper = re.search(r'function gifCanvasCleanupAllowed\([^)]*\)\{\n.*?\n\}', source, re.S)
    assert helper, 'Exercise the actual harness cleanup decision, not a copied predicate'
    args = json.dumps([click_attempted, primary_failed, click_settled])
    result = subprocess.run(
        ['node', '--input-type=module', '-e',
         helper.group(0) + '\nconsole.log(JSON.stringify(gifCanvasCleanupAllowed(...' + args + ')));'],
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert json.loads(result.stdout) is allowed


def test_media_diagnostic_applies_ui_guard_before_cleanup_mutations():
    source = (ROOT / 'frontend/tests/inline-media-browser.mjs').read_text()
    phase = source.split("await phase('gif-canvas-errors',async()=>{", 1)[1].split(
        "await phase('endpoints'", 1)[0]
    click = "await inline.getByRole('button',{name:'Open in Canvas',exact:true}).click();"
    assert phase.count(click) == 3
    assert phase.count('clickAttempted=true;clickSettled=null;\n    ' + click) == 3
    assert 'clickOwned:{attempted:clickAttempted,settled:clickSettled,primaryFailed:!!primaryError}' in phase
    cleanup = phase.split('}finally{', 1)[1]
    guard = cleanup.index('if(!gifCanvasCleanupAllowed(clickAttempted,!!primaryError,clickSettled))')
    clear = cleanup.index("if(settled)settled=await cleanup('clear owned dirty view'")
    unmount = cleanup.index("if(settled)settled=await cleanup('unmount and close owned client'")
    assert guard < cleanup.index('blockedApplicationCleanup.add(page)') < clear < unmount
    assert 'if(clickAttempted&&!primaryError)' in cleanup
    assert "getByRole('button',{name:'Open in Canvas',exact:true})).toBeEnabled()" in cleanup
    final = source.split("await phase('cleanup',async()=>{", 1)[1]
    assert final.index('blockedApplicationCleanup.has(page)') < final.index("await action(page,'session.create'")


def test_full_python_suite_is_post_release_or_manual_but_fast_proofs_remain():
    workflows = ROOT / '.github/workflows'
    manual = yaml.load((workflows / 'python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(manual['on']) == {'workflow_dispatch', 'workflow_call'}
    assert manual['permissions'] == {'contents': 'read'}
    python = manual['jobs']['python']
    assert python['timeout-minutes'] == '90'
    complete = next(step for step in python['steps']
                    if step.get('name') == 'Run the complete Python suite')
    # Keep the entire suite and immediate node identities: a timeout must not
    # conceal which test failed or was still running behind quiet progress dots.
    assert complete['run'] == 'python -u scripts/browser_check_runner.py --python-suite --output output/python-checks'
    runner = (ROOT / 'scripts/browser_check_runner.py').read_text()
    assert "PYTHON_COMMAND = ['.venv/bin/python', '-u', '-m', 'pytest', '-v', '--tb=short'," in runner
    assert "'--junitxml=output/python-checks/junit.pending.xml']" in runner
    assert 'tee ' not in complete['run']
    assert '--maxfail' not in complete['run'] and ' tests/' not in complete['run']
    upload = next(step for step in python['steps'] if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02')
    assert upload['if'] == 'always()'
    assert upload['with']['path'].splitlines() == ['output/python-checks/', '!output/python-checks/junit.pending.xml']
    for filename in ('release.yml', 'work-workflows.yml'):
        jobs = yaml.load((workflows / filename).read_text(), Loader=yaml.BaseLoader)['jobs']
        for job in jobs.values():
            for step in job.get('steps', []):
                for line in step.get('run', '').splitlines():
                    if ' pytest -' in line or ' -m pytest ' in line:
                        assert 'tests/' in line, (filename, line)
    release = yaml.load((workflows / 'release.yml').read_text(), Loader=yaml.BaseLoader)
    package = '\n'.join(step.get('run', '') for step in release['jobs']['prepare']['steps'])
    assert 'uv build' in package and 'release.py" verify' in package
    assert 'dist/*.whl' in package and 'from amplifier_web.app_updates import PROBE' in package
    runtime = '\n'.join(step.get('run', '') for step in release['jobs']['prepare']['steps'])
    assert 'tests/test_runtime_module_cache.py' in runtime


def test_full_python_evidence_budget_precedes_setup_and_always_finalizes_before_upload():
    import ast
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['python']
    steps = job['steps']
    assert job['timeout-minutes'] == '90'
    early = steps[0]
    assert early['name'] == 'Start the full Python evidence deadline before preparation'
    assert early['if'] == "${{ env.CHECK_SUITE == 'full' }}"
    assert 'time.monotonic() + 75 * 60' in early['run'] and '"$GITHUB_ENV"' in early['run']
    assert steps[1]['uses'].startswith('actions/checkout@')
    initialize = next(i for i, row in enumerate(steps) if row.get('name') == 'Retain unstarted full Python evidence')
    setup = next(i for i, row in enumerate(steps) if row.get('uses', '').startswith('actions/setup-python@'))
    execute = next(i for i, row in enumerate(steps) if row.get('name') == 'Run the complete Python suite')
    finalize = next(i for i, row in enumerate(steps) if row.get('name') == 'Finalize available full Python evidence without replay')
    upload = next(i for i, row in enumerate(steps) if row.get('uses', '').startswith('actions/upload-artifact@'))
    assert initialize < setup < execute < finalize < upload
    assert '--initialize' in steps[initialize]['run']
    assert steps[finalize]['if'] == "${{ always() && env.CHECK_SUITE == 'full' }}"
    assert '--finalize' in steps[finalize]['run']
    assert steps[upload]['if'] == 'always()'
    assert '!output/python-checks/junit.pending.xml' in steps[upload]['with']['path']
    source = (ROOT / 'scripts/browser_check_runner.py').read_text()
    module = ast.parse(source)
    command = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'PYTHON_COMMAND' for t in node.targets))
    assert command == ['.venv/bin/python', '-u', '-m', 'pytest', '-v', '--tb=short',
                       '--junitxml=output/python-checks/junit.pending.xml']
    assert 'log_limit=PYTHON_LOG_BYTES, interrupt_grace=30' in source
    assert 'PYTHON_LOG_BYTES = 16 * 1024 * 1024' in source
    assert 'PYTHON_JUNIT_BYTES = 8 * 1024 * 1024' in source
    assert "junit_output=output / 'junit.pending.xml'" in source
    assert "'--junitxml=/proc/self/fd/'" in source
    assert "'pass_fds': (junit_file.fileno(),)" in source
    assert 'not an enforced upload timeout' in source
    assert 'timeout-minutes' not in steps[upload]
    assert "float(os.environ['PYTHON_CHECK_DEADLINE'])" in source
    assert 'no replay' in source and "'pytestSessionComplete'" in source
    lock = __import__('tomllib').loads((ROOT / 'uv.lock').read_text())
    assert next(row for row in lock['package'] if row['name'] == 'pytest')['version'] == '9.1.1'


def test_manual_python_selection_is_fixed_and_preserves_full_and_core_modes():
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    selection = workflow['on']['workflow_dispatch']['inputs']['suite']
    assert selection['type'] == 'choice' and selection['default'] == 'full'
    assert selection['options'] == ['full', 'inline-chat-card', 'queued-configuration', 'security-redaction', 'patch-placement-recovery', 'patch-placement-native', 'managed-removal-recovery', 'fresh-cleanup-native', 'shared-work-continuity', 'manager-exception-boundary', 'remote-test-origins', 'shared-work-native', 'cold-control-restore', 'goal-peer-publisher-0410', 'updater-omitted-ref', 'updater-synthetic-acquisition', 'updater-expanded-lock', 'native-child-fixtures']
    assert workflow['on']['workflow_call']['inputs'] == {'revision': {'type': 'string', 'required': 'true'}}
    assert workflow['on']['workflow_call']['secrets'] == {'LOOP_LIVE_CI_READ_KEY': {'required': 'true'}}
    assert workflow['permissions'] == {'contents': 'read'}
    jobs = workflow['jobs']
    assert jobs['python']['if'] == "${{ !inputs.core_wheels && inputs.suite != 'remote-test-origins' && inputs.suite != 'cold-control-restore' && inputs.suite != 'goal-peer-publisher-0410' && inputs.suite != 'updater-omitted-ref' && inputs.suite != 'updater-synthetic-acquisition' && inputs.suite != 'updater-expanded-lock' && inputs.suite != 'fresh-cleanup-native' }}"
    assert jobs['python']['env']['CHECK_SUITE'] == "${{ inputs.suite || 'full' }}"
    assert jobs['core-wheels']['if'] == "${{ inputs.core_wheels && (!inputs.suite || inputs.suite == 'full') }}"
    remote = jobs['remote-test-origins']
    assert remote['if'] == "${{ !inputs.core_wheels && inputs.suite == 'remote-test-origins' }}"
    assert remote['uses'] == './.github/workflows/remote-test-origins.yml'
    updater = jobs['updater-omitted-ref']
    assert updater['if'] == "${{ !inputs.core_wheels && inputs.suite == 'updater-omitted-ref' }}"
    assert updater['uses'] == './.github/workflows/updater-omitted-ref.yml'
    assert updater['secrets'] == {'LOOP_LIVE_CI_READ_KEY': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}'}
    assert 'with' not in updater
    assert jobs['updater-synthetic-acquisition'] == {
        'if': "${{ !inputs.core_wheels && inputs.suite == 'updater-synthetic-acquisition' }}",
        'uses': './.github/workflows/updater-synthetic-acquisition.yml',
    }
    assert jobs['updater-expanded-lock'] == {
        'if': "${{ !inputs.core_wheels && inputs.suite == 'updater-expanded-lock' }}",
        'uses': './.github/workflows/updater-omitted-ref.yml',
        'with': {'offline_lock_preflight': 'true'},
        'secrets': {'LOOP_LIVE_CI_READ_KEY': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}'},
    }
    rejected = jobs['incompatible-selection']
    assert rejected['if'] == "${{ inputs.core_wheels && inputs.suite && inputs.suite != 'full' }}"
    assert 'exit 1' in rejected['steps'][0]['run']
    steps = jobs['python']['steps']
    assert steps[2]['name'] == 'Record and validate the fixed Python selection'
    native = next(step for step in steps if step.get('name') == 'Select the prepared native-controls interpreter')
    assert native['if'] == "${{ env.CHECK_SUITE == 'patch-placement-native' }}"
    assert native['run'] == 'echo "UNIFIED_RUNTIME_PYTHON=$GITHUB_WORKSPACE/.venv/bin/python" >> "$GITHUB_ENV"'
    complete = next(step for step in steps if step.get('name') == 'Run the complete Python suite')
    focused = next(step for step in steps if step.get('name') == 'Run the selected repair suite')
    assert complete['if'] == "${{ env.CHECK_SUITE == 'full' }}"
    assert focused['if'] == "${{ env.CHECK_SUITE != 'full' && env.CHECK_SUITE != 'native-child-fixtures' }}" and focused['shell'] == 'bash'
    assert 'mapfile -t targets < output/python-checks/targets.txt' in focused['run']
    assert 'test "${#targets[@]}" -gt 0' in focused['run']
    assert '"${targets[@]}"' in focused['run'] and 'set -o pipefail' in focused['run']
    assert '--junitxml=output/python-checks/junit.xml' in focused['run']
    assert 'tee output/python-checks/pytest.log' in focused['run']
    assert '${{' not in focused['run'] and '${{' not in steps[2]['run']
    receipts = next(step for step in steps if step.get('name') == 'Prepare pinned native receipt dependencies')
    installed = next(step for step in steps if step.get('name') == 'Verify installed native receipt boundary outside checkout')
    assert receipts['if'] == "${{ env.CHECK_SUITE == 'full' || env.CHECK_SUITE == 'shared-work-native' }}"
    assert installed['if'] == "${{ env.CHECK_SUITE == 'shared-work-native' }}"
    assert '2b35fa334fb2fb2c5c948842e8c7bd691ce5e834' in receipts['run']
    assert 'NativeSendAdmission, NativeOutboundEnvelope' in receipts['run']
    assert 'unset PYTHONPATH' in installed['run']
    assert '--no-deps -r output/python-checks/installed-requirements.txt' in installed['run']
    assert 'uv pip check --python "$RUNNER_TEMP/native-receipt-installed/bin/python"' in installed['run']
    assert 'assert not actual.is_relative_to(source)' in installed['run']
    assert "('failure', 'error', 'skipped')" in installed['run']
    assert installed['run'].index('uv pip check') < installed['run'].index(' -m pytest')
    assert "result.returncode == 1 and len(cases) == 1" in installed['run']
    assert 'negative-control.json' in installed['run']
    assert 'origin-negative.json' in installed['run']
    assert 'test_effect_origin_joins_actual_admission_and_full_input_scope[new_input]' in installed['run']
    manager = next(step for step in steps if step.get('name') == 'Build the exact manager-boundary candidate wheel')
    assert manager['if'] == "${{ env.CHECK_SUITE == 'manager-exception-boundary' }}"
    assert 'assert path.is_relative_to(root)' in manager['run']
    assert 'if [ "$CHECK_SUITE" = manager-exception-boundary ]; then' in focused['run']


def test_goal_peer_publisher_is_exclusive_fixed_manual_and_preserves_failure_evidence():
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['goal-peer-publisher-0410']
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && !inputs.core_wheels && inputs.suite == 'goal-peer-publisher-0410' }}"
    assert job['runs-on'] == 'ubuntu-24.04' and job['timeout-minutes'] == '30'
    assert job['permissions'] == {'contents': 'read', 'actions': 'read'} and 'needs' not in job
    steps = job['steps']
    assert steps[0]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    assert steps[1]['run'] == 'python scripts/goal_peer_publisher_checks.py --initialize'
    assert steps[2]['with'] == {'python-version': '3.13.17'}
    assert steps[3]['with'] == {'version': '0.13.6', 'enable-cache': 'false'}
    fetch = steps[4]
    assert fetch['timeout-minutes'] == '2' and fetch['env'] == {'GH_TOKEN': '${{ github.token }}'}
    assert 'actions/runs/41179992422/attempts/1' in fetch['run']
    assert 'actions/artifacts/13033939559/zip' in fetch['run']
    assert fetch['run'].endswith('python scripts/goal_peer_publisher_checks.py --inputs\n')
    assert steps[5]['with'] == {
        'repository': 'microsoft/amplifier-module-loop-live', 'ref': 'ccc4aa8a0bb0efdbc9e4023c2feab47d19c4bcaf',
        'path': '.ci/loop-live', 'ssh-key': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}', 'persist-credentials': 'false'}
    assert steps[6]['with'] == {
        'repository': 'microsoft/amplifier-bundle-recipes', 'ref': '7afaba7455743fb26b8d8a11cd1394eb7a01628a',
        'path': '.ci/recipes', 'persist-credentials': 'false'}
    assert str(job).count('secrets.LOOP_LIVE_CI_READ_KEY') == 1
    assert 'set -o pipefail' in steps[7]['run'] and ' --run ' in steps[7]['run']
    assert steps[-2]['if'] == 'always()' and steps[-2]['run'].endswith(' --finalize')
    assert steps[-1]['if'] == 'always()'
    assert steps[-1]['with']['path'] == 'output/goal-peer-publisher0410/'
    assert steps[-1]['with']['if-no-files-found'] == 'error'
    assert all('continue-on-error' not in step for step in steps)
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        if path.name != 'python-checks.yml':
            assert 'goal-peer-publisher-0410' not in path.read_text()


@pytest.fixture
def publisher_checks(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('publisher_checks', ROOT / 'scripts/goal_peer_publisher_checks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('mutation', [None, 'attempt', 'head', 'digest', 'artifact', 'expired', 'run'])
def test_goal_peer_publisher_rejects_wrong_api_identity(publisher_checks, mutation):
    module = publisher_checks
    run = {'id': 41179992422, 'run_attempt': 1, 'head_sha': module.PUBLISHER,
           'status': 'completed', 'conclusion': 'failure', 'event': 'push', 'head_branch': 'main',
           'repository': {'full_name': 'microsoft/amplifier-unified'}, 'path': '.github/workflows/release.yml'}
    artifact = {'id': 13033939559, 'name': 'runtime-source-resolution', 'expired': False,
                'digest': 'sha256:' + module.ARCHIVE_SHA,
                'workflow_run': {'id': run['id'], 'head_sha': module.PUBLISHER}}
    if mutation == 'attempt': run['run_attempt'] = 2
    elif mutation == 'head': run['head_sha'] = '0' * 40
    elif mutation == 'digest': artifact['digest'] = 'sha256:' + '0' * 64
    elif mutation == 'artifact': artifact['id'] += 1
    elif mutation == 'expired': artifact['expired'] = True
    elif mutation == 'run': artifact['workflow_run']['id'] += 1
    if mutation:
        with pytest.raises(AssertionError):
            module.verify_api(run, artifact)
    else:
        module.verify_api(run, artifact)


def test_goal_peer_publisher_fixed_cases_environment_and_no_resolve(publisher_checks):
    import ast
    module = publisher_checks
    assert module.SELECTORS == {
        'native': ['tests/test_goal_peer_continuation.py', 'tests/test_task_goal_runtime.py'],
        'auto': ['tests/test_peer_auto_delivery.py']}
    assert sum(map(len, module.EXPECTED.values())) == 43
    # Expand only this file's literal single-axis parameterizations, without
    # importing the product or running collection to define our expected oracle.
    names = set()
    for node in ast.parse((ROOT / 'tests/test_peer_auto_delivery.py').read_text()).body:
        if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith('test_'):
            continue
        params = [d for d in node.decorator_list if isinstance(d, ast.Call)
                  and isinstance(d.func, ast.Attribute) and d.func.attr == 'parametrize']
        assert len(params) <= 1
        names.update({f'{node.name}[{value}]' for value in ast.literal_eval(params[0].args[1])}
                     if params else {node.name})
    assert names == module.AUTO_NAMES and len(names) == 20
    permitted = {'PATH': '/tools/bin', 'LANG': 'C.UTF-8', 'RUNNER_TEMP': '/owned'}
    refused = dict.fromkeys(('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN',
                            'LOOP_LIVE_CI_READ_KEY', 'SSH_AUTH_SOCK', 'PYTHONPATH', 'PYTHONOPTIMIZE',
                            'PYTEST_ADDOPTS', 'UV_PROJECT_ENVIRONMENT', 'UV_INDEX_URL',
                            'GIT_CONFIG_COUNT', 'HOME'), 'do-not-inherit')
    assert module.clean_environment({**permitted, **refused}) == permitted
    source = (ROOT / 'scripts/goal_peer_publisher_checks.py').read_text()
    assert "['uv', 'sync', '--project', str(runtime), '--locked', '--python', sys.executable]" in source
    assert "['uv', 'export', '--project', str(runtime), '--frozen'" in source
    assert all(value not in source for value in ("['uv', 'add'", "['uv', 'lock'", '--upgrade', '--refresh'))
    assert 'time.monotonic() + 900' in source and 'time.monotonic() + 300' in source
    assert 'run_scenarios(ROOT' in source and "row['cleanup'] == {'remaining': [], 'terminated': 0}" in source
    assert "assert check_native(OUTPUT / 'native.xml') == 43" in source
    assert "restored-input-hashes.json" in source and 'candidate-source.tar' in source
    assert "for key in ('graph', 'files', 'coreWheel')" in source
    assert 'source(ROOT)' in source and "git(root, 'ls-files', '--stage', '-z')" in source


@pytest.mark.parametrize('state', ['active', 'blocked', 'completed'])
def test_goal_peer_publisher_rejects_substituting_an_idle_state(publisher_checks, monkeypatch, state):
    # Preserve the count while substituting an obsolete identity: each state
    # must be required, rather than accepting any collection of twenty cases.
    name = 'test_idle_auto_uses_same_id_and_saved_envelope'
    assert f'{name}[{state}]' in publisher_checks.AUTO_NAMES
    monkeypatch.setattr(publisher_checks, 'AUTO_NAMES',
                        (publisher_checks.AUTO_NAMES - {f'{name}[{state}]'}) | {name})
    with pytest.raises(AssertionError):
        test_goal_peer_publisher_fixed_cases_environment_and_no_resolve(publisher_checks)


@pytest.mark.parametrize('mutation', [None, 'missing', 'extra', 'version', 'commit', 'url'])
def test_goal_peer_publisher_installed_graph_must_match_locked_export(publisher_checks, tmp_path, monkeypatch, mutation):
    module = publisher_checks
    monkeypatch.setattr(module, 'OUTPUT', tmp_path)
    (tmp_path / 'original').mkdir()
    commit = 'a' * 40
    (tmp_path / 'original/uv.lock').write_text(
        '[[package]]\nname="amplifier-core"\nversion="2.0.2"\nsource={registry="https://pypi.org/simple"}\n'
        '[[package]]\nname="amplifier-foundation"\nversion="1.0.0"\n'
        f'source={{git="https://github.com/microsoft/amplifier-foundation?rev=main#{commit}"}}\n')
    (tmp_path / 'resolved-requirements.txt').write_text(
        'amplifier-core==2.0.2\n'
        f'amplifier-foundation @ git+https://github.com/microsoft/amplifier-foundation@{commit}\n')
    (tmp_path / 'candidate.json').write_text('{"files": {}}')
    (tmp_path / 'dependency-sources.json').write_text('{}')
    value = {'graph': [
        {'name': 'amplifier-core', 'version': '2.0.2', 'direct': None},
        {'name': 'amplifier-foundation', 'version': '1.0.0', 'direct': {
            'url': 'https://github.com/microsoft/amplifier-foundation',
            'vcs_info': {'vcs': 'git', 'commit_id': commit}}}],
        'files': {}, 'origins': {}, 'coreWheel': {'version': '2.0.2'}}
    if mutation == 'missing': value['graph'].pop()
    elif mutation == 'extra': value['graph'].append({'name': 'unexpected'})
    elif mutation == 'version': value['graph'][0]['version'] = '9.0'
    elif mutation == 'commit': value['graph'][1]['direct']['vcs_info']['commit_id'] = 'b' * 40
    elif mutation == 'url': value['graph'][1]['direct']['url'] = 'https://example.com/other'
    if mutation:
        with pytest.raises(AssertionError):
            module.verify_inventory(value)
    else:
        module.verify_inventory(value)


def test_cold_control_restore_is_exclusive_manual_locked_and_bounded():
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    assert set(workflow['on']['workflow_dispatch']['inputs']) == {'suite', 'core_wheels'}
    assert workflow['on']['workflow_dispatch']['inputs']['suite']['default'] == 'full'
    assert workflow['on']['workflow_call']['inputs'] == {'revision': {'type': 'string', 'required': 'true'}}
    assert workflow['permissions'] == {'contents': 'read'}
    jobs = workflow['jobs']
    assert set(jobs) == {'python', 'core-wheels', 'remote-test-origins', 'incompatible-selection',
                         'cold-control-restore', 'goal-peer-publisher-0410', 'fresh-cleanup-native',
                         'updater-omitted-ref', 'updater-synthetic-acquisition', 'updater-expanded-lock'}
    job = jobs['cold-control-restore']
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && !inputs.core_wheels && inputs.suite == 'cold-control-restore' }}"
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '15'
    assert 'needs' not in job and 'permissions' not in job and 'continue-on-error' not in job
    assert all('needs' not in other for other in jobs.values())
    steps = job['steps']
    assert steps[0]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    assert steps[1]['run'].splitlines()[0] == 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"'
    assert 'cp scripts/cold_restore_checks.py scripts/browser_check_runner.py .github/workflows/python-checks.yml pyproject.toml uv.lock' in steps[1]['run']
    assert steps[2]['with'] == {'python-version': '3.13'}
    resolve = next(i for i, s in enumerate(steps) if s.get('id') == 'runtime')
    checkout = next(i for i, s in enumerate(steps)
                    if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    assert resolve < checkout < len(steps) - 2
    assert "tomllib.load(open('uv.lock', 'rb'))" in steps[resolve]['run']
    assert "re.fullmatch('[0-9a-f]{40}', revision)" in steps[resolve]['run']
    assert steps[checkout]['with'] == {
        'repository': 'microsoft/amplifier-module-loop-live', 'ref': '${{ steps.runtime.outputs.revision }}',
        'path': '.ci/loop-live', 'ssh-key': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}', 'persist-credentials': 'false'}
    assert steps[-2] == {
        'name': 'Qualify fixed source and installed cold restore once', 'timeout-minutes': '11',
        'run': 'set -o pipefail\npython -u scripts/cold_restore_checks.py 2>&1 | tee output/cold-control-restore-run.log\n'}
    commands = '\n'.join(step.get('run', '') for step in steps)
    assert commands.count('python -u scripts/cold_restore_checks.py') == 1
    assert all(value not in commands for value in ('npm ', 'playwright', 'uv pip install', '@main'))
    assert all('continue-on-error' not in step for step in steps)
    assert steps[-1] == {
        'name': 'Retain all evidence including failed and unrun phases', 'if': 'always()',
        'uses': 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
        'with': {'name': 'cold-control-restore-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}',
                 'path': 'output/cold-control-restore-inputs/\noutput/cold-control-restore-run.log\noutput/cold-control-restore/\n',
                 'if-no-files-found': 'error', 'retention-days': '14'}}
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        if path.name != 'python-checks.yml':
            assert 'cold-control-restore' not in path.read_text()
            # Other fixed checks can retain/reuse its wheel verifier without
            # invoking the cold-control qualification entrypoint.
            for line in path.read_text().splitlines():
                if 'cold_restore_checks.py' in line:
                    assert path.name == 'browser-checks.yml'
                    assert line.strip().startswith(('cp ', 'sha256sum '))


def test_cold_control_restore_keeps_exact_cases_import_proof_and_cleanup_contract():
    import ast
    source = (ROOT / 'scripts/cold_restore_checks.py').read_text()
    tree = ast.parse(source)
    constants = {node.targets[0].id: ast.literal_eval(node.value)
                 for node in tree.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in {'SOURCE_SELECTORS', 'INSTALLED_SELECTORS', 'NOT_SELECTED'}}
    assert constants['SOURCE_SELECTORS'] == [
        'tests/test_assignment_task_limits.py',
        'tests/test_assignment_task_limits_native.py::test_native_exhausted_member_receives_fresh_work_after_manager_control',
        'tests/test_capacity.py::test_running_cas_duplicate_restart_ui_agent_parity_and_wrong_session',
        'tests/test_runtime_controls.py::test_cancelled_restore_retains_controls_and_releases_persistence_guard']
    assert constants['INSTALLED_SELECTORS'] == [
        'tests/test_assignment_task_limits.py::test_cold_limit_correction_preserves_saved_budget_at_every_write',
        'tests/test_assignment_task_limits.py::test_failed_cold_limit_preparation_keeps_previously_saved_mode',
        'tests/test_runtime_controls.py::test_cancelled_restore_retains_controls_and_releases_persistence_guard[False]',
        'tests/test_runtime_controls.py::test_cancelled_restore_retains_controls_and_releases_persistence_guard[True]']
    assert constants['NOT_SELECTED'] == 'tests/test_runtime_controls.py::test_real_core_controls_and_tool_approval'
    assert "'--locked', '--group', 'dev', '--group', 'artifacts', '--no-install-project'" in source
    assert "'.insteadOf'" in source and "'GIT_CONFIG_VALUE_0': 'https://github.com/microsoft/amplifier-module-loop-live'" in source
    assert source.count("['uv', 'build', '--wheel'") == 1
    assert "'--no-build-isolation'" in source and "'--no-deps', '-r'" in source
    assert "sorted(lines) == sorted((OUTPUT / 'requirements.txt').read_text().splitlines())" in source
    assert 'signal.alarm(600)' in source and "remaining = 600 - (time.monotonic() - started)" in source
    assert "assert not sys.argv[1:]" in source
    assert "'scenarios': [{'id': name, 'command': command, 'env': common}]" in source
    assert "row['cleanup'] == {'remaining': [], 'terminated': 0}" in source
    assert 'run_scenarios(root, plan' in source and 'subprocess.Popen' not in source
    assert "shutil.copytree(ROOT / 'tests', harness / 'tests'" in source
    assert "'-I', str(harness / 'scripts/cold_restore_checks.py'), '--installed-tests'" in source
    assert source.index("save(evidence / (phase + '-imports-before.json'), record)") < source.index('pytest.main(args')
    for field in ("'executable': sys.executable", "'cwd': str(Path.cwd())",
                  "'sysPath': sys.path.copy()", "'PYTHONPATH': os.environ.get('PYTHONPATH')"):
        assert field in source
    assert 'assert not path.is_relative_to(source)' in source
    assert 'assert all(not Path(p).resolve().is_relative_to(source) for p in sys.path)' in source
    assert "('installed', 4) if installed else ('source', 34)" in source
    assert "'failure': 0, 'error': 0, 'skipped': 0" in source
    assert "verify_wheel(wheel, candidate, package_root)" in source
    assert "assets == candidate['static']" in source and "installed[path] == [encoded, size]" in source
    assert "candidate['files'] == {p: sha(ROOT / p) for p in files}" in source
    assert all(value not in source for value in ("'npm'", "'node'", "'--upgrade'", "'--refresh'", '@main'))


def test_expanded_lock_preflight_reuses_private_checkout_without_running_worker():
    workflow = yaml.load(
        (ROOT / '.github/workflows/updater-omitted-ref.yml').read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow['on'] == {'workflow_call': {
        'inputs': {'offline_lock_preflight': {'type': 'boolean', 'default': 'false'}},
        'secrets': {'LOOP_LIVE_CI_READ_KEY': {'required': 'true'}},
    }}
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['jobs']) == {'qualify'}
    job = workflow['jobs']['qualify']
    assert set(job) == {'runs-on', 'timeout-minutes', 'steps'}
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '30'
    steps = job['steps']
    assert len(steps) == 10
    checkouts = [s for s in steps if s.get('uses', '').startswith('actions/checkout@')]
    assert checkouts == [
        {
            'uses': 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
            'with': {'persist-credentials': 'false'},
        },
        {
            'uses': 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
            'with': {
                'repository': 'microsoft/amplifier-module-loop-live',
                'ref': '${{ steps.pin.outputs.revision }}', 'fetch-depth': '0',
                'path': '.ci/omitted-ref-loop',
                'ssh-key': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}',
                'persist-credentials': 'false',
            },
        },
    ]
    pin = next(s for s in steps if s.get('id') == 'pin')
    assert 'if' not in pin and "Path('uv.lock').read_text()" in pin['run']
    assert "pin = source.rsplit('#', 1)[1]" in pin['run']
    uv_steps = [s for s in steps if s.get('uses', '').startswith('astral-sh/setup-uv@')]
    assert uv_steps == [
        {
            'uses': 'astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e',
            'if': '${{ !inputs.offline_lock_preflight }}', 'with': {'version': '0.13.6'},
        },
        {
            'uses': 'astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e',
            'if': '${{ inputs.offline_lock_preflight }}', 'with': {'version': '0.13.6'},
        },
    ]
    host = next(s for s in steps if s.get('name') == 'Prepare locked host dependencies using only the approved private checkout')
    worker = next(s for s in steps if s.get('name') == 'Prepare the independent worker graph and run the fixed no-account fixture')
    preflight = next(s for s in steps if s.get('name') == 'Resolve the expanded graph with the fixed offline lock preflight')
    assert host['if'] == worker['if'] == '${{ !inputs.offline_lock_preflight }}'
    assert preflight['if'] == '${{ inputs.offline_lock_preflight }}'
    assert "['uv', 'sync', '--locked', '--group', 'dev', '--group', 'artifacts']" in host['run']
    assert worker['run'].splitlines() == [
        'set -euo pipefail',
        'python scripts/qualify_omitted_ref.py \\',
        '  --loop-source "$GITHUB_WORKSPACE/.ci/omitted-ref-loop" \\',
        '  --work "$RUNNER_TEMP/omitted-ref-work" \\',
        '  --output "$RUNNER_TEMP/omitted-ref-evidence" \\',
        '  2>&1 | tee "$RUNNER_TEMP/omitted-ref-evidence/adapter.log"',
    ]
    assert preflight['run'] == worker['run'].replace(
        'python scripts/qualify_omitted_ref.py ',
        'python scripts/qualify_omitted_ref.py --offline-lock-preflight ', 1,
    )
    assert [s for s in steps if 'run' in s] == [pin, host, worker, preflight]
    assert all('${{' not in s['run'] for s in (host, worker, preflight))
    assert all('continue-on-error' not in s for s in steps)
    assert steps.index(checkouts[1]) < steps.index(host) < steps.index(worker) < steps.index(preflight)
    assert steps[-1] == {
        'name': 'Retain bounded proof even after setup or qualification failure',
        'if': 'always()',
        'uses': 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
        'with': {
            'name': 'omitted-ref-${{ github.run_id }}-${{ github.run_attempt }}',
            'path': '${{ runner.temp }}/omitted-ref-evidence/',
            'if-no-files-found': 'error',
        },
    }


def test_synthetic_acquisition_workflow_has_only_fixed_public_steps():
    workflow = yaml.load(
        (ROOT / '.github/workflows/updater-synthetic-acquisition.yml').read_text(),
        Loader=yaml.BaseLoader,
    )
    # Exact allowlists exclude inputs, secrets, env, private checkouts,
    # dependency setup and arbitrary commands at every workflow level.
    assert set(workflow) == {'name', 'on', 'permissions', 'jobs'}
    assert workflow['on'] == {'workflow_call': {}}
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['jobs']) == {'acquisition'}
    job = workflow['jobs']['acquisition']
    assert set(job) == {'runs-on', 'timeout-minutes', 'steps'}
    assert job['runs-on'] == 'ubuntu-24.04'
    assert job['timeout-minutes'] == '15'
    assert job['steps'] == [
        {
            'uses': 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
            'with': {'ref': '${{ github.sha }}', 'persist-credentials': 'false'},
        },
        {
            'uses': 'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
            'with': {'python-version': '3.13'},
        },
        {
            'name': 'Run fixed public synthetic acquisition',
            'run': 'python -I -S -B scripts/run_omitted_ref_acquisition.py',
        },
        {
            'name': 'Retain complete and partial acquisition evidence',
            'if': 'always()',
            'uses': 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
            'with': {
                'name': 'omitted-acquisition-evidence-${{ github.run_id }}-${{ github.run_attempt }}',
                'path': '${{ runner.temp }}/omitted-acquisition-evidence/',
                'if-no-files-found': 'error',
            },
        },
    ]


def test_offline_preflight_does_not_run_import_or_independent_install_probes():
    import ast
    tree = ast.parse((ROOT / 'scripts/qualify_omitted_ref.py').read_text())
    guarded_labels = set()
    all_labels = set()
    excluded = {'worker-readiness', 'independent-offline-install', 'independent-offline-readiness'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'run':
            if node.args and isinstance(node.args[0], ast.Constant):
                all_labels.add(node.args[0].value)
        if isinstance(node, ast.If) and ast.unparse(node.test) == 'not args.offline_lock_preflight':
            for statement in node.body:
                for child in ast.walk(statement):
                    if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                            and child.func.id == 'run' and child.args and isinstance(child.args[0], ast.Constant)):
                        guarded_labels.add(child.args[0].value)
    assert excluded <= all_labels
    assert excluded <= guarded_labels


@pytest.mark.parametrize(('suite', 'targets'), [
    ('full', []),
    ('inline-chat-card', ['tests/test_chat_references.py']),
    ('queued-configuration', ['tests/test_queued_configuration_boundary.py', 'tests/test_configuration_recovery_ui.py']),
    ('security-redaction', ['tests/test_agent_state.py', 'tests/test_provider_environment.py', 'tests/test_runtime_controls.py']),
    ('patch-placement-recovery', ['tests/test_patch_placement_recovery.py', 'tests/test_file_source_guard.py', 'tests/test_file_source_native_children.py', 'tests/test_workspace_writer_modules.py']),
    ('patch-placement-native', ['tests/test_patch_placement_native.py', 'tests/test_patch_placement_recovery.py', 'tests/test_configuration_authority.py', 'tests/test_queued_configuration_boundary.py', 'tests/test_runtime_controls.py']),
    ('managed-removal-recovery', ['tests/test_dependency_layers.py', 'tests/test_stopped_reservation_recovery.py', 'tests/test_prepared_execution_contract.py', 'tests/test_environment_preparation.py', 'tests/test_test_environment_worker.py', 'tests/test_source_transfer.py', 'tests/test_task_environments.py', 'tests/test_environment_recovery.py', 'tests/test_prelaunch_source_failure.py', 'tests/test_environment_retention.py', 'tests/test_incus_environment_worker.py', 'tests/test_release_qualification.py']),
    ('shared-work-continuity', ['tests/test_task_continuity.py', 'tests/test_coordination_delivery.py', 'tests/test_work_invocation.py', 'tests/test_schedules.py', 'tests/test_collaboration_input.py', 'tests/test_coordination_revisions.py', 'tests/test_release_qualification.py', 'tests/test_voice_visual.py', 'tests/test_surface_context.py', 'tests/test_output_image_pairs.py', 'tests/test_memory_personalization.py', 'tests/test_capacity.py']),
    ('shared-work-native', ['tests/test_native_provider.py', 'tests/test_coordination_delivery.py', 'tests/test_work_native_effects.py', 'tests/test_work_origin.py', 'tests/test_capacity.py', 'tests/test_release_qualification.py']),
    ('manager-exception-boundary', ['tests/test_manager_exception_boundary.py', 'tests/test_manager_failure_details.py', 'tests/test_session_health.py', 'tests/test_retained_failure_recovery.py', 'tests/test_runtime.py']),
    ('native-child-fixtures', ['tests/test_child_app_assignment_capture.py', 'tests/test_history_scope_native.py', 'tests/test_file_source_native_children.py']),
])
@pytest.mark.parametrize('missing', [False, True])
def test_python_selection_records_exact_source_and_refuses_missing_files(tmp_path, monkeypatch, suite, targets, missing):
    import subprocess
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    selection, = (step for step in workflow['jobs']['python']['steps']
                  if step.get('name') == 'Record and validate the fixed Python selection')
    run = selection['run']
    lines = run.splitlines()
    assert lines[0] == "python - <<'PY'" and lines[-1] == 'PY'
    script = compile('\n'.join(lines[1:-1]), 'workflow-python-selection', 'exec')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CHECK_SUITE', suite)
    monkeypatch.setenv('GITHUB_RUN_ID', 'synthetic-run')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    def git(args, **kwargs):
        assert args[:2] == ['git', 'rev-parse']
        assert args[2] in ('HEAD', 'HEAD^{tree}')
        return ('a' if args[2] == 'HEAD' else 'b') * 40 + '\n'
    monkeypatch.setattr(subprocess, 'check_output', git)
    for target in targets[1:] if missing else targets:
        path = tmp_path / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    if missing and targets:
        with pytest.raises(SystemExit, match='Selected tests are missing: ' + targets[0]):
            exec(script, {})
    else:
        exec(script, {})
    record = json.loads((tmp_path / 'output/python-checks/selection.json').read_text())
    assert record == {'suite': suite, 'fullSuite': suite == 'full', 'targets': targets,
                      'revision': 'a' * 40, 'tree': 'b' * 40, 'runId': 'synthetic-run', 'runAttempt': '1',
                      'missing': targets[:1] if missing else []}
    assert (tmp_path / 'output/python-checks/targets.txt').read_text().splitlines() == targets


@pytest.mark.parametrize('suite', ['unknown', 'tests/test_service.py', 'full; echo unexpected'])
def test_python_selection_rejects_arbitrary_input_before_commands(tmp_path, monkeypatch, suite):
    import subprocess
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    selection, = (step for step in workflow['jobs']['python']['steps']
                  if step.get('name') == 'Record and validate the fixed Python selection')
    script = '\n'.join(selection['run'].splitlines()[1:-1])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CHECK_SUITE', suite)
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **kw: pytest.fail('Invalid selection ran a command'))
    with pytest.raises(SystemExit, match='Unknown fixed Python suite:'):
        exec(compile(script, 'workflow-python-selection', 'exec'), {})
    assert not (tmp_path / 'output').exists()


def native_child_workflow():
    workflow = yaml.load((ROOT / '.github/workflows/python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    step, = [row for row in workflow['jobs']['python']['steps']
             if row.get('name') == 'Run and retain exact native child fixture evidence']
    lines = step['run'].splitlines()
    assert lines[:2] == ['set -euo pipefail',
                        "uv run --no-sync python -u - <<'PY' 2>&1 | tee output/python-checks/pytest.log"]
    assert lines[-1] == 'PY'
    return workflow, step, '\n'.join(lines[2:-1])


def native_child_validation():
    """Exercise the actual inline validators, without importing native packages."""
    import ast
    from urllib.parse import urlsplit
    _, _, code = native_child_workflow()
    module = ast.parse(code)
    nodes = [row for row in module.body if isinstance(row, ast.FunctionDef)
             and row.name in {'expected_cases', 'validate_selection', 'resolved_source', 'validate_results'}]
    targets, = [row for row in module.body if isinstance(row, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'TARGETS' for target in row.targets)]
    namespace = {'Path': Path, 're': re, 'urlsplit': urlsplit}
    exec(compile(ast.Module(body=[targets, *nodes], type_ignores=[]), 'native-child-inline-validation', 'exec'), namespace)
    return namespace


def test_native_child_ci_uses_existing_prerequisites_one_pytest_and_partial_upload():
    import ast
    workflow, step, code = native_child_workflow()
    steps = workflow['jobs']['python']['steps']
    assert workflow['jobs']['python']['timeout-minutes'] == '90'
    assert step['if'] == "${{ env.CHECK_SUITE == 'native-child-fixtures' }}"
    assert step['timeout-minutes'] == '10' and step['shell'] == 'bash'
    install, = [row for row in steps if row.get('name') == 'Install runtime prerequisites for host and child tests']
    mount, = [row for row in steps if row.get('name') == 'Require native file-policy modules and Core entry points']
    assert steps.index(install) < steps.index(mount) < steps.index(step)
    assert 'amplifier-bundle-filesystem@main#subdirectory=modules/tool-apply-patch' in install['run']
    assert 'mount is not module.mount' in mount['run']
    assert 'native-child-fixtures' not in install.get('if', '')
    assert 'continue-on-error' not in step
    assert steps[-1]['if'] == 'always()' and 'output/python-checks/' in steps[-1]['with']['path']
    module = ast.parse(code)
    calls = [node for node in ast.walk(module) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
             and node.func.value.id == 'pytest' and node.func.attr == 'main']
    assert len(calls) == 1
    assert ast.unparse(calls[0].args[0]) == "['-v', '--tb=short', '--junitxml=output/python-checks/junit.xml', *TARGETS]"
    assert ast.unparse(calls[0].keywords[0].value) == '[Evidence()]'
    for required in ('pytest_sessionstart', 'pytest_collection_finish', 'sys.executable', 'sys.version',
                     'sys.flags.optimize', '__debug__', 'direct_url.json', 'commit_id', 'hashlib.file_digest',
                     'amplifier_core._engine', 'amplifier_foundation.bundle', 'amplifier_module_loop_live.runtime',
                     'amplifier_module_loop_streaming', 'amplifier_module_context_simple',
                     'amplifier_module_tool_filesystem', 'amplifier_module_tool_apply_patch.engines.native',
                     'entry.load() is module.mount', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        assert required in code
    assert 'os.environ.items' not in code and 'pip' not in code and 'collect-only' not in code
    expected = native_child_validation()['expected_cases']()
    actual = []
    for path in native_child_validation()['TARGETS']:
        for node in ast.parse((ROOT / path).read_text()).body:
            if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith('test_'):
                continue
            parameters = [row for row in node.decorator_list if isinstance(row, ast.Call)
                          and isinstance(row.func, ast.Attribute) and row.func.attr == 'parametrize']
            assert len(parameters) <= 1
            if parameters:
                actual.extend(path + '::' + node.name + '[' + value + ']'
                              for value in ast.literal_eval(parameters[0].args[1]))
            else:
                actual.append(path + '::' + node.name)
    assert len(actual) == 17 and set(actual) == set(expected)


@pytest.mark.parametrize('change', ['none', 'suite', 'targets', 'target-file', 'revision', 'tree', 'runId', 'runAttempt'])
def test_native_child_ci_refuses_wrong_selection_identity(change):
    ns = native_child_validation()
    record = {'suite': 'native-child-fixtures', 'fullSuite': False, 'targets': ns['TARGETS'],
              'revision': 'a' * 40, 'tree': 'b' * 40, 'runId': '1', 'runAttempt': '2', 'missing': []}
    targets = list(ns['TARGETS'])
    if change == 'target-file':
        targets[0] = 'tests/test_service.py'
    elif change == 'targets':
        record['targets'] = targets[::-1]
    elif change != 'none':
        record[change] = 'wrong'
    arguments = (record, targets, 'a' * 40, 'b' * 40, '1', '2')
    if change == 'none':
        ns['validate_selection'](*arguments)
    else:
        with pytest.raises(AssertionError):
            ns['validate_selection'](*arguments)


@pytest.mark.parametrize('change', [
    'none', 'collection-missing', 'collection-duplicate', 'collection-foreign', 'exit-code',
    'case-missing', 'case-duplicate', 'case-foreign', 'failure', 'error', 'skipped',
    'tests-counter', 'failure-counter', 'error-counter', 'skip-counter', 'missing-counter', 'root-counter', 'extra-suite',
])
def test_native_child_ci_requires_exact_collection_and_successful_junit(change):
    import xml.etree.ElementTree as ET
    ns = native_child_validation()
    collected = ns['expected_cases']()
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', tests='17', failures='0', errors='0', skipped='0')
    for node in collected:
        path, name = node.split('::')
        ET.SubElement(suite, 'testcase', classname=path.removesuffix('.py').replace('/', '.'), name=name)
    code = 0
    if change == 'collection-missing': collected.pop()
    elif change == 'collection-duplicate': collected[-1] = collected[0]
    elif change == 'collection-foreign': collected[-1] = 'tests/test_service.py::test_other'
    elif change == 'exit-code': code = 1
    elif change == 'case-missing': suite.remove(suite[-1])
    elif change == 'case-duplicate': suite[-1].attrib.update(suite[0].attrib)
    elif change == 'case-foreign': suite[-1].set('classname', 'tests.test_other')
    elif change in {'failure', 'error', 'skipped'}: ET.SubElement(suite[0], change)
    elif change == 'tests-counter': suite.set('tests', '18')
    elif change == 'failure-counter': suite.set('failures', '1')
    elif change == 'error-counter': suite.set('errors', '1')
    elif change == 'skip-counter': suite.set('skipped', '1')
    elif change == 'missing-counter': del suite.attrib['tests']
    elif change == 'root-counter': root.set('failures', '1')
    elif change == 'extra-suite': ET.SubElement(root, 'testsuite')
    if change == 'none':
        ns['validate_results'](collected, root, code)
    else:
        with pytest.raises(AssertionError):
            ns['validate_results'](collected, root, code)


@pytest.mark.parametrize('change', [
    'none', 'token', 'query', 'foreign-host', 'moving-ref', 'subdirectory', 'foreign-file-host',
    'missing-subdirectory', 'wrong-repository', 'wrong-checkout-distribution', 'missing-commit',
    'missing:amplifier-foundation', 'missing:amplifier-module-loop-live',
    'missing:amplifier-module-loop-streaming', 'missing:amplifier-module-context-simple',
    'missing:amplifier-module-tool-filesystem', 'missing:amplifier-module-tool-apply-patch',
    'missing:amplifier-module-tool-delegate',
])
def test_native_child_ci_records_only_actual_public_vcs_provenance(change, tmp_path):
    ns = native_child_validation()
    name = 'amplifier-module-tool-apply-patch'
    value = {'url': 'https://github.com/microsoft/amplifier-bundle-filesystem',
             'vcs_info': {'vcs': 'git', 'commit_id': 'c' * 40, 'requested_revision': 'main'},
             'subdirectory': 'modules/tool-apply-patch'}
    if change == 'token': value['url'] = 'https://credential@github.com/microsoft/amplifier-bundle-filesystem'
    elif change == 'query': value['url'] += '?token=private'
    elif change == 'foreign-host': value['url'] = 'https://other.invalid/microsoft/module'
    elif change == 'moving-ref': value['vcs_info']['commit_id'] = 'main'
    elif change == 'subdirectory': value['subdirectory'] = '../../private'
    elif change == 'foreign-file-host':
        name, value = 'amplifier-module-loop-live', {'url': 'file://other-host' + str(tmp_path / '.ci/loop-live')}
    elif change == 'missing-subdirectory': del value['subdirectory']
    elif change == 'wrong-repository': value['url'] = 'https://github.com/microsoft/amplifier-foundation'
    elif change == 'wrong-checkout-distribution': value = {'url': (tmp_path / '.ci/loop-live').as_uri()}
    elif change == 'missing-commit': del value['vcs_info']['commit_id']
    elif change.startswith('missing:'): name, value = change.split(':', 1)[1], None
    if change == 'none':
        assert ns['resolved_source'](name, value, tmp_path) == {
            'url': value['url'], 'vcs': 'git', 'commitId': 'c' * 40, 'subdirectory': 'modules/tool-apply-patch'}
        for registry in ('amplifier-core', 'pytest'):
            assert ns['resolved_source'](registry, None, tmp_path) is None
        for distribution, repository, subdirectory in [
            ('amplifier-foundation', 'amplifier-foundation', None),
            ('amplifier-module-loop-live', 'amplifier-module-loop-live', None),
            ('amplifier-module-loop-streaming', 'amplifier-module-loop-streaming', None),
            ('amplifier-module-context-simple', 'amplifier-module-context-simple', None),
            ('amplifier-module-tool-filesystem', 'amplifier-module-tool-filesystem', None),
            ('amplifier-module-tool-delegate', 'amplifier-foundation', 'modules/tool-delegate'),
        ]:
            direct = {'url': 'https://github.com/microsoft/' + repository,
                      'vcs_info': value['vcs_info'], **({'subdirectory': subdirectory} if subdirectory else {})}
            assert ns['resolved_source'](distribution, direct, tmp_path) == {
                'url': direct['url'], 'vcs': 'git', 'commitId': 'c' * 40,
                **({'subdirectory': subdirectory} if subdirectory else {})}
        def checkout_commit(*args):
            assert args == ('-C', '.ci/loop-live', 'rev-parse', 'HEAD')
            return 'd' * 40
        ns['git'] = checkout_commit
        assert ns['resolved_source']('amplifier-module-loop-live',
                {'url': (tmp_path / '.ci/loop-live').as_uri()}, tmp_path) == {
            'url': 'file:.ci/loop-live', 'checkoutCommit': 'd' * 40}
    else:
        with pytest.raises((AssertionError, KeyError)):
            ns['resolved_source'](name, value, tmp_path)


@pytest.mark.parametrize('change', [
    'none', 'missing-native-engine', 'wrong-mount', 'duplicate-entry',
    'missing:amplifier-foundation', 'missing:amplifier-module-loop-live',
    'missing:amplifier-module-loop-streaming', 'missing:amplifier-module-context-simple',
    'missing:amplifier-module-tool-filesystem', 'missing:amplifier-module-tool-apply-patch',
    'missing:amplifier-module-tool-delegate',
])
def test_native_child_ci_import_hook_retains_partial_provenance(change, tmp_path):
    import ast
    from copy import deepcopy
    from types import SimpleNamespace
    _, _, code = native_child_workflow()
    module = ast.parse(code)
    evidence, = [node for node in ast.walk(module) if isinstance(node, ast.ClassDef) and node.name == 'Evidence']
    mounts = {name: lambda: None for name in ('loop-live', 'context-simple', 'tool-filesystem', 'tool-apply-patch')}
    entries = [SimpleNamespace(name=name, load=lambda mount=mount: mount) for name, mount in mounts.items()]
    if change == 'wrong-mount':
        entries[-1].load = lambda: object()
    elif change == 'duplicate-entry':
        entries.append(entries[-1])
    def identity(name):
        if change == 'missing-native-engine' and name == 'amplifier_module_tool_apply_patch.engines.native':
            raise ModuleNotFoundError('missing fixture native engine')
        return {'origin': str(tmp_path / (name + ('.so' if name == 'amplifier_core._engine' else '.py'))),
                'sha256': 'a' * 64}
    provenance = native_child_validation()
    provenance['git'] = lambda *args: 'd' * 40
    missing = change.split(':', 1)[1] if change.startswith('missing:') else None
    def distribution(name):
        direct = None
        if name not in {'amplifier-core', 'pytest', missing}:
            if name == 'amplifier-module-loop-live':
                direct = {'url': (Path.cwd() / '.ci/loop-live').as_uri()}
            else:
                repository = {'amplifier-module-tool-apply-patch': 'amplifier-bundle-filesystem',
                              'amplifier-module-tool-delegate': 'amplifier-foundation'}.get(name, name)
                direct = {'url': 'https://github.com/microsoft/' + repository,
                          'vcs_info': {'vcs': 'git', 'commit_id': 'c' * 40, 'requested_revision': 'main'}}
                if name in {'amplifier-module-tool-apply-patch', 'amplifier-module-tool-delegate'}:
                    direct['subdirectory'] = 'modules/' + name.removeprefix('amplifier-module-')
        def read_text(path):
            assert path == 'direct_url.json'
            return json.dumps(direct) if direct is not None else None
        return SimpleNamespace(version='1.2.3', read_text=read_text)
    record = {'passed': False, 'distributions': {}, 'modules': {}}
    saved = []
    namespace = {
        'record': record, 'save': lambda: saved.append(deepcopy(record)),
        'module_identity': identity, 'resolved_source': provenance['resolved_source'],
        'md': SimpleNamespace(distribution=distribution, entry_points=lambda group: entries),
        'importlib': SimpleNamespace(machinery=SimpleNamespace(EXTENSION_SUFFIXES=['.so']),
            import_module=lambda name: SimpleNamespace(mount=mounts[name.removeprefix('amplifier_module_').replace('_', '-')])),
        'json': json, 'Path': Path,
    }
    exec(compile(ast.Module(body=[evidence], type_ignores=[]), 'native-child-import-hook', 'exec'), namespace)
    hook = namespace['Evidence']().pytest_sessionstart
    if change == 'none':
        hook(None)
        assert record['canonicalMounts'] is True
        assert 'amplifier_module_tool_apply_patch.engines.native' in record['modules']
    else:
        with pytest.raises((ModuleNotFoundError, AssertionError, ValueError)):
            hook(None)
        assert 'canonicalMounts' not in record
    assert saved == [record] and record['phase'] == 'pytest-imports' and record['passed'] is False
    assert record['distributions']['amplifier-core'] == {
        'version': '1.2.3', 'directSourceStatus': 'registry', 'directSource': None}
    if missing:
        assert record['provenanceFailureDistribution'] == missing
        assert record['distributions'][missing] == {'version': '1.2.3', 'directSourceStatus': 'refused'}
        assert not record['modules']  # Refused before native imports or collection.
    else:
        assert 'provenanceFailureDistribution' not in record
        assert 'amplifier_core._engine' in record['modules']
        assert record['distributions']['amplifier-module-tool-apply-patch']['directSource']['commitId'] == 'c' * 40


def test_browser_retains_only_the_successfully_built_public_web_payload():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    steps = workflow['jobs']['browser']['steps']
    identity = next(index for index, step in enumerate(steps) if step.get('id') == 'build-source')
    assert steps[identity - 1]['uses'] == 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262'
    assert steps[identity]['run'] == 'echo "revision=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"'
    build = next(index for index, step in enumerate(steps) if step.get('id') == 'frontend-build')
    assert identity < build
    assert steps[build]['run'] == 'npm run build --prefix frontend'
    upload = steps[build + 1]
    assert upload['if'] == "${{ steps.frontend-build.outcome == 'success' }}"
    assert upload['uses'] == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
    assert upload['with'] == {
        'name': 'browser-static-${{ steps.build-source.outputs.revision }}-${{ github.run_attempt }}',
        'path': 'amplifier_web/static/', 'include-hidden-files': 'true', 'if-no-files-found': 'error'}
    assert steps[build + 2]['name'] == 'Install browser dependencies'
    # The artifact includes the builder's metadata and is retained before later
    # scenario failure; an unrun/failed build cannot upload the old tracked tree.
    vite = (ROOT / 'frontend/vite.config.js').read_text()
    assert 'emptyOutDir:true' in vite and 'static/build.json' in vite


def browser_runner_commands(steps):
    invocation = r'(?m)^(?:uv run --no-sync )?python scripts/browser_check_runner\.py(?:[ \t]|$)'
    return [step['run'] for step in steps if re.search(invocation, step.get('run', ''))]


def test_browser_runner_matcher_excludes_test_names_without_hiding_extra_invocations():
    initialize = 'python scripts/browser_check_runner.py --initialize'
    execute = 'uv run --no-sync python scripts/browser_check_runner.py --budget-seconds 1500'
    decoys = [
        'set -o pipefail\nuv run --no-sync pytest tests/test_browser_check_runner.py tests/test_release_qualification.py',
        'echo python scripts/browser_check_runner.py',
        'python scripts/browser_check_runner.py.backup',
    ]
    steps = [{'run': text} for text in (initialize, *decoys, execute)]
    assert browser_runner_commands(steps) == [initialize, execute]
    # A duplicate remains visible to the caller's exact two-command assertions.
    assert browser_runner_commands([*steps, {'run': execute}]) == [initialize, execute, execute]
    joined = initialize + '\n' + execute
    assert browser_runner_commands([{'run': joined}]) == [joined]


def test_canvas_qualification_is_fixed_manual_and_installed_not_a_release_gate():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['canvas-qualification']
    assert job['if'] == "${{ !inputs.permissions_history && !inputs.readiness_trio && github.event_name == 'workflow_dispatch' && inputs.canvas_qualification }}"
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '20'
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'secrets.' not in str(job)
    assert job['steps'][0]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'uv sync --locked --group dev --group artifacts --no-build-package amplifier-core' in commands
    assert 'npm ci --prefix frontend' in commands
    assert 'npx playwright install --with-deps chromium' in commands
    assert 'python scripts/canvas_qualification.py run' in commands
    upload = next(step for step in job['steps'] if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02')
    assert 'always()' in upload['if'] and upload['with']['if-no-files-found'] == 'error'
    source = (ROOT / 'scripts/canvas_qualification.py').read_text()
    assert 'from browser_check_runner import run_scenarios' in source
    assert "BROWSERS = ('canvas-chrome-browser.mjs', 'canvas-dirty-browser.mjs'," in source
    assert "'destination-history-browser.mjs', 'automation-hub-browser.mjs'," in source
    assert job['steps'][1]['env']['MIXED'] == (
        '${{ inputs.attachment_experience || inputs.multi_quote || inputs.peer_messages || inputs.compact_composer || inputs.mcp_boundary || inputs.app004_qualification || inputs.attachment_consumption '
        '|| inputs.destination_history || inputs.settings_close_diagnostic }}')
    assert "raise ValueError('Qualification already started; no replay')" in source
    assert "'installed-harness'" in source and "'installed-identity.json'" in source
    assert "assert static == json.loads((OUT / 'static.json').read_text())" in source


def test_app004_opt_in_is_fixed_manual_only_and_keeps_default_browser_budget():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert set(inputs) == {'app004_qualification', 'mcp_boundary', 'attachment_consumption',
                           'canvas_qualification', 'destination_history', 'settings_close_diagnostic',
                           'multi_quote', 'attachment_experience', 'compact_composer', 'peer_messages',
                           'readiness_trio', 'permissions_history'}
    assert inputs['canvas_qualification']['type'] == 'boolean'
    assert inputs['canvas_qualification']['default'] == 'false'
    assert inputs['multi_quote']['type'] == 'boolean'
    assert inputs['multi_quote']['default'] == 'false'
    assert workflow['permissions'] == {'contents': 'read'}
    assert inputs['attachment_experience']['type'] == 'boolean'
    assert inputs['attachment_experience']['default'] == 'false'
    assert inputs['peer_messages']['type'] == 'boolean'
    assert inputs['peer_messages']['default'] == 'false'
    assert inputs['settings_close_diagnostic']['type'] == 'boolean'
    assert inputs['settings_close_diagnostic']['default'] == 'false'
    assert inputs['destination_history']['type'] == 'boolean'
    assert inputs['destination_history']['default'] == 'false'
    assert inputs['attachment_consumption']['type'] == 'boolean'
    assert inputs['attachment_consumption']['default'] == 'false'
    assert inputs['app004_qualification']['type'] == 'boolean'
    assert inputs['app004_qualification']['default'] == 'false'
    assert inputs['mcp_boundary']['type'] == 'boolean'
    assert inputs['mcp_boundary']['default'] == 'false'
    assert inputs['compact_composer']['type'] == 'boolean'
    assert inputs['compact_composer']['default'] == 'false'
    assert workflow['on']['workflow_call'] == {
        'inputs': {'revision': {'type': 'string', 'required': 'true'}}}
    job = workflow['jobs']['browser']
    assert job['if'] == '${{ !inputs.permissions_history && !inputs.readiness_trio && !inputs.canvas_qualification && !inputs.peer_messages && !inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic && !inputs.attachment_experience }}'
    assert job['timeout-minutes'] == '45'
    mcp = workflow['jobs']['mcp-boundary']
    assert mcp['if'] == "${{ !inputs.permissions_history && !inputs.readiness_trio && github.event_name == 'workflow_dispatch' && !inputs.canvas_qualification && !inputs.peer_messages && inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic }}"
    history = workflow['jobs']['destination-history']
    assert history['if'] == '${{ !inputs.permissions_history && !inputs.readiness_trio && !inputs.peer_messages && !inputs.compact_composer && (inputs.destination_history || inputs.settings_close_diagnostic) }}'
    assert history['steps'][0] == {
        'name': 'Require the fixed navigation selection for diagnostic mode',
        'if': '${{ inputs.settings_close_diagnostic && !inputs.destination_history }}',
        'run': 'exit 1',
    }
    assert history['steps'][1] == {
        'name': 'Reject mixed navigation selections',
        'if': '${{ inputs.mcp_boundary || inputs.app004_qualification || inputs.attachment_consumption || inputs.canvas_qualification || inputs.multi_quote || inputs.attachment_experience }}',
        'run': 'exit 1',
    }
    # Both opt-ins must fail visibly before setup, not silently drop APP004.
    incompatible = mcp['steps'][1]
    assert incompatible['name'] == 'Reject incompatible browser proof selections'
    assert incompatible['if'] == '${{ inputs.app004_qualification }}'
    assert incompatible['run'].splitlines() == [
        "echo 'MCP boundary and APP004 qualification are separate selections; neither proof was run.'",
        'exit 1',
    ]
    steps = job['steps']
    assert next(s for s in steps if s.get('name') == 'Reject mixed multi-quote selections') == {
        'name': 'Reject mixed multi-quote selections',
        'if': '${{ inputs.multi_quote && (inputs.attachment_consumption || inputs.app004_qualification || inputs.mcp_boundary) }}',
        'run': 'exit 1',
    }
    assert next(s for s in mcp['steps'] if s.get('name') == 'Reject mixed MCP and attachment selection') == {
        'name': 'Reject mixed MCP and attachment selection',
        'if': '${{ inputs.attachment_consumption || inputs.multi_quote || inputs.attachment_experience }}',
        'run': 'exit 1',
    }
    commands = browser_runner_commands(steps)
    assert len(commands) == 2
    flag = "${{ inputs.app004_qualification && '--app004-qualification' || '' }}"
    attachment_flag = "${{ inputs.attachment_consumption && '--attachment-consumption' || '' }}"
    assert all(command.count(flag) == 1 for command in commands)
    assert all(command.count(attachment_flag) == 1 for command in commands)
    # Empty/default selection executes exactly the preexisting plan and budget.
    assert commands[0].replace(flag, '').replace(attachment_flag, '').strip() == (
        'python scripts/browser_check_runner.py --plan frontend/tests/browser-checks.json '
        '--output output/browser-checks --initialize')
    assert commands[1].replace(flag, '').replace(attachment_flag, '').strip() == (
        'uv run --no-sync python scripts/browser_check_runner.py '
        '--plan frontend/tests/browser-checks.json --output output/browser-checks --budget-seconds 1500')
    assert next(i for i, s in enumerate(steps) if '--initialize' in s.get('run', '')) < next(
        i for i, s in enumerate(steps) if s.get('uses') == 'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065')
    contracts = [s for s in steps if s.get('name') == 'Verify fixed APP004 qualification contracts']
    assert len(contracts) == 1
    contract = contracts[0]
    assert contract['if'] == '${{ inputs.app004_qualification }}'
    assert contract['run'].splitlines() == [
        'set -o pipefail',
        'uv run --no-sync pytest -v --tb=short tests/test_browser_check_runner.py '
        'tests/test_release_qualification.py --junitxml=output/browser-checks/app004-contracts.xml '
        '2>&1 | tee output/browser-checks/app004-contracts.log',
    ]
    before = steps[:steps.index(contract)]
    assert any(s.get('uses') == 'actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020' for s in before)
    assert any(s.get('run') == 'npm ci --prefix frontend' for s in before)
    assert any(s.get('run') == 'uv sync --locked --group dev --group artifacts' for s in before)
    assert any(s.get('with', {}).get('path') == 'amplifier_web/static/' for s in before)
    # The fixed attachment-only contracts are inactive for APP004/default.
    after = [step for step in steps[steps.index(contract) + 1:]
             if step.get('if') != '${{ inputs.attachment_consumption }}']
    next_step = after[0]
    assert next_step['name'] == 'Run all extended browser scenarios serially'
    # Multi-quote replaces only this explicitly selected run; default/APP004
    # retain the existing plan and implicit success gate on earlier failures.
    assert next_step['if'] == '${{ !inputs.multi_quote }}'
    assert next(s for s in steps if '--initialize' in s.get('run', ''))['if'] == '${{ !inputs.multi_quote }}'
    assert next(s for s in steps if s.get('name') == 'Run fixed multi-quote source and installed proof') == {
        'name': 'Run fixed multi-quote source and installed proof',
        'if': '${{ inputs.multi_quote }}',
        'run': 'uv run --no-sync python scripts/multi_quote_checks.py --output output/multi-quote --installed',
    }
    # Opt-in can neither mask a failed step nor bypass the existing always upload.
    continued = [step for step in steps if 'continue-on-error' in step]
    assert {step['id'] for step in continued} == {'attachment-contracts', 'attachment-client-contracts'}
    assert all(step['if'] == '${{ inputs.attachment_consumption }}' for step in continued)
    failures = next(step for step in steps if step.get('name') == 'Preserve failed attachment contracts')
    assert failures['run'] == 'exit 1' and 'always()' in failures['if']
    diagnostics = steps[-1]
    assert diagnostics['if'] == 'always()'
    assert diagnostics['uses'] == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
    assert 'output/browser-checks/' in diagnostics['with']['path']


def test_readiness_trio_is_exclusive_manual_fixed_and_not_a_default_gate():
    workflows = ROOT / '.github/workflows'
    workflow = yaml.load((workflows / 'browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert inputs['readiness_trio'] == {
        'description': 'Only fixed question delivery, scheduler and compaction diagnostics qualification',
        'type': 'boolean', 'default': 'false',
    }
    assert workflow['on']['workflow_call'] == {
        'inputs': {'revision': {'type': 'string', 'required': 'true'}}}
    assert workflow['permissions'] == {'contents': 'read'}
    # Exact old predicates remain intact behind one additional conjunct.
    previous_conditions = {
        'browser': '!inputs.canvas_qualification && !inputs.peer_messages && !inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic && !inputs.attachment_experience',
        'attachment-experience': 'inputs.attachment_experience',
        'mcp-boundary': "github.event_name == 'workflow_dispatch' && !inputs.canvas_qualification && !inputs.peer_messages && inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic",
        'canvas-qualification': "github.event_name == 'workflow_dispatch' && inputs.canvas_qualification",
        'compact-composer': "github.event_name == 'workflow_dispatch' && inputs.compact_composer",
        'destination-history': '!inputs.peer_messages && !inputs.compact_composer && (inputs.destination_history || inputs.settings_close_diagnostic)',
        'peer-messages': "github.event_name == 'workflow_dispatch' && inputs.peer_messages",
    }
    jobs = workflow['jobs']
    assert set(jobs) == {*previous_conditions, 'readiness-trio', 'permissions-history'}
    for name, condition in previous_conditions.items():
        assert jobs[name]['if'] == '${{ !inputs.permissions_history && !inputs.readiness_trio && ' + condition + ' }}'
        assert 'needs' not in jobs[name], 'Manual qualification cannot gate existing jobs'
    job = jobs['readiness-trio']
    assert job['if'] == "${{ !inputs.permissions_history && github.event_name == 'workflow_dispatch' && inputs.readiness_trio == true }}"
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '15'
    assert 'needs' not in job and 'continue-on-error' not in job
    steps = job['steps']
    mixed = ('attachment_experience', 'peer_messages', 'destination_history', 'settings_close_diagnostic',
             'mcp_boundary', 'app004_qualification', 'attachment_consumption', 'canvas_qualification',
             'multi_quote', 'compact_composer')
    assert set(inputs) == {'readiness_trio', 'permissions_history', *mixed}
    assert steps[0] == {
        'name': 'Reject mixed readiness trio selections before setup',
        'if': '${{ ' + ' || '.join('inputs.' + name for name in mixed) + ' }}',
        'run': "echo 'Readiness trio requires its own explicit manual selection; no qualification was run.'\nexit 1\n",
    }
    assert steps[1] == {
        'uses': 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
        'with': {'ref': '${{ github.sha }}', 'persist-credentials': 'false'},
    }
    assert steps[2]['name'] == 'Retain intended checks before dependency preparation'
    assert steps[2]['run'].splitlines()[0] == 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"'
    assert 'cp .github/workflows/browser-checks.yml output/readiness-trio-inputs/' in steps[2]['run']
    execute = {
        'name': 'Qualify once with fail-stop cleanup and a ten-minute overall budget',
        'timeout-minutes': '11',
        'run': 'set -o pipefail\npython -u scripts/readiness_trio_checks.py 2>&1 | tee output/readiness-trio-run.log\n',
    }
    assert steps[-2] == execute
    assert sum('python -u scripts/readiness_trio_checks.py' in step.get('run', '') for step in steps) == 1
    assert all('continue-on-error' not in step for step in steps)
    assert 'signal.alarm(600)' in (ROOT / 'scripts/readiness_trio_checks.py').read_text()
    uploads = [step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@')]
    assert uploads == [steps[-1]]
    assert uploads[0] == {
        'name': 'Make all evidence downloadable, including failed and unrun phases',
        'if': 'always()',
        'uses': 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
        'with': {
            'name': 'readiness-trio-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}',
            'path': 'output/readiness-trio-inputs/\noutput/readiness-trio-run.log\noutput/readiness-trio/\ntest-results/\n',
            'if-no-files-found': 'error', 'retention-days': '14',
        },
    }
    # No PR/push trigger, reusable opt-in, external caller or publication gate.
    for path in workflows.glob('*.yml'):
        if path.name != 'browser-checks.yml':
            assert 'readiness_trio' not in path.read_text()
            assert 'readiness-trio' not in path.read_text()


def test_permissions_history_is_exclusive_manual_fixed_and_preserves_default_jobs():
    workflows = ROOT / '.github/workflows'
    workflow = yaml.load((workflows / 'browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert inputs['permissions_history'] == {
        'description': 'Only fixed permissions history source and installed-wheel qualification',
        'type': 'boolean', 'default': 'false',
    }
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    assert workflow['on']['workflow_call'] == {
        'inputs': {'revision': {'type': 'string', 'required': 'true'}}}
    assert workflow['permissions'] == {'contents': 'read'}
    jobs = workflow['jobs']
    job = jobs['permissions-history']
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && inputs.permissions_history == true }}"
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '15'
    assert 'needs' not in job and 'continue-on-error' not in job
    for name, other in jobs.items():
        if name != 'permissions-history':
            assert other['if'].startswith('${{ !inputs.permissions_history && ')
            assert 'needs' not in other
    steps = job['steps']
    assert set(re.findall(r'inputs\.([a-z_0-9]+)', steps[0]['if'])) == set(inputs) - {'permissions_history'}
    assert steps[0]['run'].endswith('exit 1\n')
    assert steps[1]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    assert steps[2]['run'].startswith('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"\n')
    assert 'scripts/permission_history_checks.py' in steps[2]['run']
    actions = {step['uses'] for step in steps if 'uses' in step}
    assert actions == {
        'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
        'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
        'actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020',
        'astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e',
        'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
    }
    private = next(s for s in steps if s.get('with', {}).get('repository'))
    assert private['with'] == {
        'repository': 'microsoft/amplifier-module-loop-live',
        'ref': '${{ steps.runtime.outputs.revision }}', 'path': '.ci/loop-live',
        'ssh-key': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}', 'persist-credentials': 'false'}
    resolve = next(s['run'] for s in steps if s.get('id') == 'runtime')
    assert "tomllib.load(open('uv.lock', 'rb'))" in resolve and "rsplit('#', 1)" in resolve
    assert steps[-2]['timeout-minutes'] == '11'
    assert steps[-2]['run'] == (
        'set -o pipefail\npython -u scripts/permission_history_checks.py '
        '2>&1 | tee output/permissions-history-run.log\n')
    assert sum('python -u scripts/permission_history_checks.py' in s.get('run', '') for s in steps) == 1
    assert all('continue-on-error' not in s for s in steps)
    assert steps[-1]['if'] == 'always()'
    assert steps[-1]['with']['include-hidden-files'] == 'true'
    assert steps[-1]['with']['if-no-files-found'] == 'error'
    assert steps[-1]['with']['path'].splitlines() == [
        'output/permissions-history-inputs/', 'output/permissions-history-run.log',
        'output/permissions-history/', 'test-results/']
    for path in workflows.glob('*.yml'):
        if path.name != 'browser-checks.yml':
            assert 'permissions_history' not in path.read_text()
            assert 'permissions-history' not in path.read_text()


@pytest.fixture
def permissions_checks(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('permissions_checks', ROOT / 'scripts/permission_history_checks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_permissions_history_plan_has_only_fixed_checks_and_strict_evidence():
    import ast
    source = (ROOT / 'scripts/permission_history_checks.py').read_text()
    tree = ast.parse(source)
    plans, = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'plans' for t in n.targets)]
    assert [ast.literal_eval(row.elts[0]) for row in plans.elts] == [
        'locked-host', 'locked-freeze', 'host-dependencies', 'npm-ci', 'chromium', 'python', 'node',
        'build', 'source-approval', 'wheel', 'fresh-runtime', 'install-dependencies', 'install-wheel',
        'installed-dependencies', 'installed-freeze', 'installed-imports', 'installed-approval', 'questions']
    assert source.count("['npm', 'run', 'build']") == 1
    assert source.count("['uv', 'build', '--wheel'") == 1
    assert source.count("['node', 'tests/approval-lifecycle-browser.mjs']") == 2
    assert source.count("['node', 'tests/questions-browser.mjs']") == 1
    assert "'--locked', '--group', 'dev', '--group', 'artifacts', '--no-install-project'" in source
    assert "'--no-build-isolation'" in source and "'--no-deps'" in source
    assert 'CONTEXT_REQUIREMENT' not in source and 'context-dependency' not in source
    assert 'signal.alarm(600)' in source and "'normalBudgetSeconds': 300" in source
    assert 'from browser_check_runner import run_scenarios' in source
    assert "'scenarios': [{'id': phase, 'command': command, 'env': env}]" in source
    assert "row['cleanup'] == {'remaining': [], 'terminated': 0}" in source
    assert "report['phases'] = {name: {'status': 'not_run'}" in source
    assert 'verify_wheel(wheel, candidate, package.parent)' in source
    assert 'source_identity(ROOT)' in source and 'create(ROOT,' in source and 'verify(ROOT,' in source
    assert "harness / 'frontend/node_modules', symlinks=False" in source
    assert 'symlink_to' not in source and 'manifest(harness) == copied' in source
    assert source.count("'AMPLIFIER_SHELL_NODE_MODULES':") == 2
    assert source.count("'AMPLIFIER_TEST_PACKAGE_ROOT':") == 2
    assert "source['version']" in source and "dist['direct']['url'] == wheel.as_uri()" in source
    assert source.index('retained = qualification_environment(os.environ)') < source.index('os.environ.clear()')
    assert source.index('os.environ.clear()') < source.index('code = run_scenarios(')
    assert "'GIT_CONFIG_GLOBAL': '/dev/null'" in source and "'GIT_CONFIG_NOSYSTEM': '1'" in source
    assert "'UV_CACHE_DIR': str(work / 'uv-cache')" in source
    assert "'NPM_CONFIG_USERCONFIG': str(work / 'npm-user.conf')" in source


def test_permissions_history_selection_comes_from_actual_source(permissions_checks):
    module = permissions_checks
    assert module.PYTHON_TESTS == ('tests/test_approval_recovery.py', 'tests/test_approval_feedback.py',
                                   'tests/test_host_approvals.py', 'tests/test_conversation_stop_status.py')
    assert module.NODE_TESTS == ('tests/approval-card.test.mjs', 'tests/settings-action-queue.test.mjs',
                                'tests/pending-view.test.mjs', 'tests/conversation-navigation.test.mjs',
                                'tests/fixture-lifecycle.test.mjs')
    python = module.python_selection(ROOT)
    assert python['tests/test_host_approvals.py::test_real_worker_canonical_decision_returns_exact_offered_label_with_note'] == 6
    assert python['tests/test_approval_feedback.py::test_overlapping_approval_retries_share_delivery_and_settle_waiter'] == 5
    assert sum(value for name, value in python.items() if name.startswith('tests/test_conversation_stop_status.py::')) == 42
    assert python['tests/test_conversation_stop_status.py::test_identifiers_are_nonempty_bounded_strings'] == 18
    assert sum(python.values()) == 73
    node = module.node_selection(ROOT)
    assert set(node) == set(module.NODE_TESTS)
    assert {name: len(names) for name, names in node.items()} == {
        'tests/approval-card.test.mjs': 27, 'tests/settings-action-queue.test.mjs': 9,
        'tests/pending-view.test.mjs': 9, 'tests/conversation-navigation.test.mjs': 16,
        'tests/fixture-lifecycle.test.mjs': 5}
    approval = node['tests/approval-card.test.mjs']
    assert {name.removeprefix('passive Stop lookup stays fenced on ') for name in approval
            if name.startswith('passive Stop lookup stays fenced on ')} == {
        'missing', 'empty', 'unknown', 'wrong status', 'boolean acceptance',
        'wrong sessionId', 'wrong commandId', 'wrong clientId',
        'missing sessionId', 'missing commandId', 'missing clientId',
        'non-object', 'transport failure', 'malformed JSON'}
    assert all('Stop during preflight invalidates it after ' + value + '; no automatic replay' in approval
               for value in ('accepted', 'not-applied'))
    assert all('note accompanies ' + value + ', targets the rendered chat and survives failure' in approval
               for value in ('approve', 'deny'))
    assert all('opening ' + value + ' from a workspace paints the chat before the server responds'
               in node['tests/conversation-navigation.test.mjs'] for value in ('a', 'b'))
    assert all('delayed ' + value + ' cannot overtake later browsing, while later drawer intent still wins'
               in node['tests/conversation-navigation.test.mjs'] for value in ('navExpanded', 'navPinned'))
    assert 'unexpected fixture exit is never reported as successful cleanup' in node['tests/fixture-lifecycle.test.mjs']


def test_permissions_python_selection_counts_rows_without_evaluating_fixture_constants(
    permissions_checks, tmp_path, monkeypatch,
):
    path = tmp_path / 'selected.py'
    path.write_text(
        "class WaitingRuntime:\n    pass\n"
        "@pytest.mark.parametrize('body', [{'clientId': CLIENT}, {}])\n"
        "@pytest.mark.parametrize('field,limit', [('sessionId', 200), ('clientId', 100)])\n"
        "def test_identity(body, field, limit):\n    pass\n")
    monkeypatch.setattr(permissions_checks, 'PYTHON_TESTS', ('selected.py',))
    assert permissions_checks.python_selection(tmp_path) == {'selected.py::test_identity': 4}
    path.write_text(path.read_text().replace("[{'clientId': CLIENT}, {}]", '[*EXTRA]'))
    with pytest.raises(AssertionError):
        permissions_checks.python_selection(tmp_path)


def test_permissions_node_names_expand_literals_and_refuse_unknown_loops(permissions_checks):
    module = permissions_checks
    text = ("test('literal',()=>{});\nfor(const value of ['one','two'])test(`case ${value}`,()=>{});\n"
            "for(const value of ['a','b'])test('prefix '+value+' suffix',()=>{});\n")
    assert module.node_names(text) == ['literal', 'case one', 'case two', 'prefix a suffix', 'prefix b suffix']
    with pytest.raises(AssertionError, match='Unrecognized'):
        module.node_names("test('literal',()=>{});\nfor(const [name,fn] of cases)test(`case ${name}`,fn);\n")
    with pytest.raises(AssertionError):
        module.node_names(text.replace('${value}', '${other}'))
    with pytest.raises(AssertionError, match='Unrecognized'):
        module.node_names((ROOT / 'frontend/tests/approval-card.test.mjs').read_text().replace(
            "['missing',()=>null]", "['renamed',()=>null]"))


def test_permissions_environment_keeps_only_named_ci_and_tool_certificate_inputs(permissions_checks):
    permitted = {'PATH': '/tools/bin', 'LD_LIBRARY_PATH': '/tools/lib', 'LANG': 'C.UTF-8',
                 'SSL_CERT_FILE': '/certs/root.pem', 'NODE_EXTRA_CA_CERTS': '/certs/node.pem',
                 'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': 'a' * 40, 'RUNNER_TEMP': '/ci/temp'}
    refused = {key: 'must-not-inherit' for key in (
        'HOME', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONOPTIMIZE', 'PYTEST_ADDOPTS',
        'NODE_PATH', 'NODE_OPTIONS', 'UV_PROJECT_ENVIRONMENT', 'UV_INDEX_URL', 'PIP_INDEX_URL',
        'NPM_TOKEN', 'NPM_CONFIG_USERCONFIG', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS',
        'GIT_SSH_COMMAND', 'SSH_AUTH_SOCK', 'AWS_ACCESS_KEY_ID', 'AZURE_CLIENT_SECRET',
        'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GITHUB_TOKEN', 'GH_TOKEN',
        'ACTIONS_RUNTIME_TOKEN', 'LOOP_LIVE_CI_READ_KEY', 'AMPLIFIER_TEST_PACKAGE_ROOT')}
    assert permissions_checks.qualification_environment({**permitted, **refused}) == permitted


@pytest.mark.parametrize('mutation', [None, 'duplicate', 'missing', 'wrong', 'skip', 'fail', 'cancel', 'todo', 'skip-directive', 'todo-directive'])
def test_permissions_tap_rejects_partial_or_substituted_execution(permissions_checks, mutation):
    text = ('TAP version 13\nok 1 - first\nok 2 - second\n1..2\n'
            '# tests 2\n# pass 2\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n')
    if mutation == 'duplicate':
        text = text.replace('ok 2 - second', 'ok 2 - first')
    elif mutation == 'missing':
        text = text.replace('ok 2 - second\n', '')
    elif mutation == 'wrong':
        text = text.replace('second', 'unselected')
    elif mutation in ('skip-directive', 'todo-directive'):
        directive = mutation.split('-', 1)[0].upper()
        text = text.replace('ok 2 - second', 'ok 2 - second # ' + directive + ' blocked')
    elif mutation:
        key = {'skip': 'skipped', 'cancel': 'cancelled'}.get(mutation, mutation)
        text = text.replace(f'# {key} 0', f'# {key} 1')
    if mutation:
        with pytest.raises(AssertionError):
            permissions_checks.verify_tap(text, {'fixed.test.mjs': ['first', 'second']})
    else:
        assert permissions_checks.verify_tap(text, {'fixed.test.mjs': ['first', 'second']})['names'] == ['first', 'second']


@pytest.mark.parametrize('mutation', [None, 'sigterm', 'failed', 'incomplete', 'checks', 'wrong-root',
                                     'renderer', 'fixture-origin', 'report-origin', 'substitute-check',
                                     'recovery-flag', 'preflight-replay', 'wrong-command', 'old-stop'])
def test_permissions_browser_requires_full_report_and_normal_fixture_exit(permissions_checks, tmp_path, mutation):
    import hashlib
    package, modules = tmp_path / 'package', tmp_path / 'node_modules'
    package.mkdir()
    (package / 'configuration_authority.py').write_text('exact authority')
    (package / 'static').mkdir()
    (package / 'static/index.html').write_text('exact static')
    folder = tmp_path / 'evidence'
    output = folder / 'approval-lifecycle-owned'
    output.mkdir(parents=True)
    cleanup = {'exitCode': 0, 'signal': None, 'observed': True, 'graceful': True, 'error': None}
    # Independent names retain all original checks (four Close receipts) and
    # the five added unknown/recovery assertions, not merely a minimum count.
    names = [
        'Close accepted', 'Close accepted', 'Close accepted', 'Close accepted',
        'Pending approve completes during held history', 'Pending deny completes during held history',
        'Held history/newer document wins', 'Dirty edit retained and replacement refused',
        'Newer Close hides immediately before either receipt', 'Held history/newer Close accepted',
        'Stop fences later-allow', 'Stop fences pending-preflight', 'Unknown lost-ack stays fenced',
        'Passive lost-ack recovery permits only fresh dismissal', 'Unknown malformed-ack stays fenced',
        'Recovering preflight cannot send its decision', 'Passive malformed-ack recovery permits only fresh dismissal']
    checks = [{'name': name, 'decisionRequests': []} for name in names]
    binding = {'sessionId': 'chat-a', 'commandId': 'original', 'clientId': 'client-a'}
    for row in checks:
        if row['name'].startswith('Passive '):
            row.update(samePage=True, stopReceipt={'accepted': True}, dismissalReceipt={'accepted': True},
                       stopCommand={'id': 'original', 'args': {'sessionId': 'chat-a'}}, clientId='client-a',
                       supersedingStop={'id': 'newer', 'receipt': {'accepted': True}},
                       decisionRequests=[{'action': 'capability.revoke'}],
                       lookups=[{'body': binding, 'response': {**binding, 'status': 'accepted'}, 'httpStatus': 200}])
    proof = {'passed': True, 'incomplete': False, 'error': None, 'checks': checks,
             'stopAcknowledgementRecovery': True, 'overwrittenStopRecovery': True,
             'source': str(package / '__init__.py'), 'cleanup': cleanup,
             'authoritySha256': hashlib.sha256(b'exact authority').hexdigest()}
    readiness = {'source': str(package), 'nodeModules': str(modules),
                 'node': '/node', 'playwright': True, 'validationHtml': True,
                 'packageIdentity': {'installedPackageVerified': True,
                     'expectedPackageRoot': str(package), 'packageRoot': str(package),
                     'server': str(package / 'server.py'), 'staticRoot': str(package / 'static'),
                     'staticSha256': {'index.html': hashlib.sha256(b'exact static').hexdigest()}}}
    proof['packageIdentity'] = json.loads(json.dumps(readiness['packageIdentity']))
    if mutation == 'sigterm':
        cleanup.update(exitCode=None, signal='SIGTERM')
    elif mutation == 'failed':
        proof['passed'] = False
    elif mutation == 'incomplete':
        proof['incomplete'] = True
    elif mutation == 'checks':
        proof['checks'].pop()
    elif mutation == 'wrong-root':
        proof['source'] = str(tmp_path / 'other/__init__.py')
    elif mutation == 'renderer':
        readiness['nodeModules'] = str(tmp_path / 'source/node_modules')
    elif mutation == 'fixture-origin':
        readiness['packageIdentity']['expectedPackageRoot'] = str(tmp_path / 'source')
    elif mutation == 'report-origin':
        proof['packageIdentity']['server'] = str(tmp_path / 'source/server.py')
    elif mutation == 'substitute-check':
        checks[-1]['name'] = 'Unrelated passing check'
    elif mutation == 'recovery-flag':
        proof['stopAcknowledgementRecovery'] = False
    elif mutation == 'preflight-replay':
        next(row for row in checks if row['name'].startswith('Recovering '))['decisionRequests'] = [{}]
    elif mutation == 'wrong-command':
        checks[-1]['lookups'][0]['response']['commandId'] = 'different'
    elif mutation == 'old-stop':
        next(row for row in checks if row['name'].startswith('Passive lost-ack'))['supersedingStop']['id'] = 'original'
    for name, value in [('report', proof), ('cleanup', cleanup), ('validation-readiness', readiness)]:
        (output / (name + '.json')).write_text(json.dumps(value))
    if mutation:
        with pytest.raises(AssertionError):
            permissions_checks.verify_approval(folder, package, modules)
    else:
        assert permissions_checks.verify_approval(folder, package, modules)['checks'] == 17


def test_compact_composer_is_fixed_manual_only_with_independent_failure_evidence():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'compact_composer' not in workflow['on']['workflow_call']['inputs']
    job = workflow['jobs']['compact-composer']
    assert job['if'] == "${{ !inputs.permissions_history && !inputs.readiness_trio && github.event_name == 'workflow_dispatch' && inputs.compact_composer }}"
    assert job['timeout-minutes'] == '30'
    steps = job['steps']
    assert steps[0]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    assert steps[1] == {'name': 'Reject mixed selections',
                       'if': '${{ inputs.canvas_qualification || inputs.multi_quote || inputs.attachment_experience || inputs.peer_messages || inputs.mcp_boundary || inputs.attachment_consumption || inputs.app004_qualification || inputs.destination_history || inputs.settings_close_diagnostic }}',
                       'run': 'exit 1'}
    assert steps[2]['run'] == 'python scripts/compact_composer_checks.py --initialize'
    commands = [step['run'] for step in steps if 'run' in step]
    assert commands.count('uv run --no-sync python scripts/compact_composer_checks.py') == 1
    assert 'uv sync --locked --group dev --group artifacts' in commands
    assert 'npm ci --prefix frontend' in commands
    assert 'npm run build --prefix frontend' in commands
    assert all('${{' not in command for command in commands)
    assert all('continue-on-error' not in step for step in steps)
    uploads = [step for step in steps if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02']
    assert len(uploads) == 2
    assert uploads[0]['if'] == "${{ steps.compact-build.outcome == 'success' }}"
    assert uploads[0]['with']['path'] == 'amplifier_web/static/'
    assert uploads[0]['with']['include-hidden-files'] == 'true'
    assert uploads[1]['if'] == 'always()'
    assert uploads[1]['with']['path'] == 'output/compact-composer/'
    assert all(step['with']['if-no-files-found'] == 'error' for step in uploads)
    assert 'secrets.' not in str(job)


def test_runtime_cache_is_selected_after_fresh_resolution_without_fallback():
    jobs = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)['jobs']
    steps = jobs['prepare']['steps']
    resolve = next(i for i, step in enumerate(steps) if 'uv lock ' in step.get('run', ''))
    key = next(i for i, step in enumerate(steps) if ' cache-key ' in step.get('run', ''))
    restore = next(i for i, step in enumerate(steps) if step.get('uses') == 'actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830')
    assert resolve < key < restore
    assert '--refresh --upgrade' in steps[resolve]['run']
    assert 'restore-keys' not in steps[restore]['with']
    assert 'runtime-build-cache' in steps[restore]['with']['path']
    install = next(i for i, step in enumerate(steps) if 'uv sync --project' in step.get('run', ''))
    save = next(i for i, step in enumerate(steps) if step.get('uses') == 'actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830')
    assert install < save
    assert 'uv cache clean amplifier-module-loop-live' in steps[install]['run']


def test_native_core_qualification_is_manual_and_cannot_publish():
    workflows = ROOT / '.github/workflows'
    entry = yaml.load((workflows / 'python-checks.yml').read_text(), Loader=yaml.BaseLoader)
    native = yaml.load((workflows / 'core-wheels.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(entry['on']) == {'workflow_dispatch', 'workflow_call'}
    assert entry['on']['workflow_dispatch']['inputs']['core_wheels']['default'] == 'false'
    assert entry['jobs']['core-wheels']['uses'] == './.github/workflows/core-wheels.yml'
    assert set(native['on']) == {'workflow_call'}
    assert native['permissions'] == {'contents': 'read'}
    job = native['jobs']['runtime']
    assert job['if'] == "github.event_name == 'workflow_dispatch'"
    assert set(job['strategy']['matrix']['os']) == {'ubuntu-latest', 'ubuntu-24.04-arm'}
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'tests/test_runtime_module_cache.py' in commands
    assert 'runtime-snapshot' in commands and 'cmp ' in commands
    assert 'publish' not in commands
    assert job['timeout-minutes'] == '20'
    steps = job['steps']
    host = next(step['run'] for step in steps if step.get('name') == 'Install and record the released host Core wheel')
    runtime = next(step['run'] for step in steps if step.get('name') == 'Qualify real runtime behavior without model calls')
    assert host.count('--junitxml="$RUNNER_TEMP/host-core-junit.xml"') == 1
    assert runtime.count('--junitxml="$RUNNER_TEMP/runtime-core-junit.xml"') == 1
    assert runtime.index('cp ') < runtime.index('runtime-snapshot') < runtime.index('cmp ') < runtime.index('git rev-parse HEAD')
    retained = next(step for step in steps if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02')
    assert retained['if'] == 'always()'
    assert set(retained['with']['path'].splitlines()) == {
        '${{ runner.temp }}/' + name for name in (
            'app-revision.txt', 'host-core-wheel.json', 'host-core-junit.xml',
            'runtime-core-junit.xml', 'before-tests.json', 'core-runtime/pyproject.toml',
            'core-runtime/uv.lock', 'core-runtime/build-constraints.txt',
            'core-runtime/build-identity.json', 'core-runtime/qualified-runtime.json')
    }


@pytest.mark.parametrize('workflow', ['release.yml', 'core-wheels.yml'])
def test_recipe_probe_installs_its_declared_module_before_freezing_runtime(workflow):
    jobs = yaml.load((ROOT / '.github/workflows' / workflow).read_text(), Loader=yaml.BaseLoader)['jobs']
    commands = '\n'.join(step.get('run', '') for step in jobs['prepare' if workflow == 'release.yml' else 'runtime']['steps'])
    recipe = next(line for line in commands.splitlines()
                  if 'uv add ' in line and '.ci/recipes/modules/tool-recipes' in line)
    assert '--frozen --editable' in recipe
    assert commands.index(recipe) < commands.index('uv lock ') < commands.index(' cache-key ')
    assert commands.index('uv sync --project') < commands.index('WARM_RECIPES_PATH=')
    # Dependencies stay owned by the checked-out module, not a stale hand list.
    assert 'tree-sitter' not in commands


def test_coordination_prepares_real_loop_before_first_contract_suite():
    workflow = yaml.load((ROOT / '.github/workflows/coordination.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['on']) == {'pull_request'}
    steps = workflow['jobs']['contracts']['steps']
    resolve = next(i for i, s in enumerate(steps) if s.get('id') == 'runtime-dependency')
    checkout = next(i for i, s in enumerate(steps)
                    if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    install = next(i for i, s in enumerate(steps) if s.get('name') == 'Prepare the declared runtime for mounted coordination tests')
    verify = next(i for i, s in enumerate(steps) if s.get('name') == 'Verify and retain coordination runtime readiness')
    contracts = next(i for i, s in enumerate(steps) if 'tests/test_coordination_delivery.py' in s.get('run', ''))
    assert resolve < checkout < install < verify < contracts
    assert 'runtime_deps/pyproject.toml' in steps[resolve]['run']
    assert steps[checkout]['with'] == {
        'repository': 'microsoft/amplifier-module-loop-live',
        'ref': '${{ steps.runtime-dependency.outputs.revision }}',
        'path': '.ci/loop-live', 'ssh-key': '${{ secrets.LOOP_LIVE_CI_READ_KEY }}',
        'persist-credentials': 'false'}
    assert '--python .venv/bin/python' in steps[install]['run'] and '.ci/loop-live' in steps[install]['run']
    assert 'amplifier-module-context-simple' in steps[install]['run']
    assert 'BundleLiveOrchestrator' in steps[verify]['run'] and 'selectedLoopRevision' in steps[verify]['run']
    assert 'importorskip' not in steps[verify]['run']
    assert not any('continue-on-error' in s for s in steps)
    assert any(s.get('name') == 'Verify mounted child identity and file boundaries' for s in steps[contracts + 1:])
    assert any(s.get('run') == 'node tests/coordination-browser.mjs' for s in steps)
    assert steps[-1]['if'] == 'always()'
    assert '${{ runner.temp }}/test-environments' in steps[-1]['with']['path']
    assert 'output/coordination-runtime-readiness.json' in steps[-1]['with']['path']


def test_remote_machine_queue_routing_has_a_focused_ci_owner():
    workflow = yaml.load((ROOT / '.github/workflows/remote-test-origins.yml').read_text(), Loader=yaml.BaseLoader)
    paths = workflow['on']['pull_request']['paths']
    assert all(path in paths for path in (
        'amplifier_web/registered_test_runners.py', 'amplifier_web/test_runner_routing.py',
        'amplifier_web/test_queue.py', 'tests/test_runner_routing.py',
        'tests/test_registered_test_runner_queue.py', 'amplifier_web/test_runner_enrollment.py',
        'amplifier_web/managed_runner_qualification.py', 'amplifier_web/test_runner_controller.py',
        'tests/test_runner_enrollment*.py'))
    steps = workflow['jobs']['queue-routing']['steps']
    run = next(step['run'] for step in steps if step.get('name') == 'Check queued routing across approved machines')
    assert run == ('uv run --no-sync pytest -q tests/test_runner_routing.py '
                   'tests/test_registered_test_runner_queue.py tests/test_runner_enrollment.py '
                   'tests/test_registered_test_runner_recovery.py --junitxml=routing-results.xml')
    assert any(step.get('if') == 'always()' and step.get('with', {}).get('path') == 'routing-results.xml' for step in steps)
