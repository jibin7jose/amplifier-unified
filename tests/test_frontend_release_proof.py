"""Frontend proof reuse is fail-closed and never substitutes a PR green badge."""
import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import zipfile

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('frontend_proof', ROOT / 'scripts/frontend_release_proof.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def facts():
    repository = 'owner/app'
    head, base, tested, selected = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
    qualifiers = {name: hashlib.sha256(name.encode()).hexdigest() for name in module.QUALIFIERS}
    inputs = {'tree': 'e' * 40, 'assets': 'assets-digest', 'qualifiers': qualifiers,
              'tools': {'node': 'v22.0.0', 'npm': '10.0.0', 'image': 'ubuntu24', 'imageVersion': '20261014'}}
    proof = {'schema': 2, 'testScope': 'full', 'eligible': True, 'inputs': copy.deepcopy(inputs), 'repository': repository,
             'runId': 101, 'attempt': 2, 'pr': 15, 'head': head, 'base': base, 'tested': tested}
    run = {'id': 101, 'run_attempt': 2, 'status': 'completed', 'conclusion': 'success',
           'event': 'pull_request', 'path': module.WORKFLOW, 'head_sha': head,
           'head_repository': {'full_name': repository}, 'pull_requests': [{'number': 15}]}
    pr = {'number': 15, 'merged_at': '2026-10-14T00:00:00Z', 'merge_commit_sha': selected,
          'head': {'sha': head, 'repo': {'full_name': repository}},
          'base': {'sha': base, 'ref': 'main', 'repo': {'full_name': repository}}}
    jobs = [{'name': 'Fast release checks', 'run_id': 101, 'run_attempt': 2, 'conclusion': 'success',
             'steps': [{'name': name, 'status': 'completed', 'conclusion': 'success'} for name in module.STEPS]}]
    commit = {'sha': tested, 'tree': {'sha': inputs['tree']}, 'parents': [{'sha': base}, {'sha': head}]}
    return dict(proof=proof, inputs=inputs, run=run, pr=pr, jobs=jobs, commit=commit,
                qualifiers=qualifiers.copy(), repository=repository)


def test_success_requires_exact_external_facts(facts):
    module.verify(**facts)


@pytest.mark.parametrize('change', [
    'inputs', 'assets', 'tool', 'image-family', 'machine', 'system', 'missing-image', 'missing-proof-image', 'workflow-bytes', 'helper-bytes',
    'run', 'attempt', 'pr', 'head', 'fork', 'event', 'workflow', 'failure', 'running',
    'association', 'tested', 'tree', 'parents', 'job-attempt', 'job-failed', 'step-skipped',
    'ineligible', 'unknown-scope', 'missing-scope', 'old-schema', 'classifier-bytes',
])
def test_every_identity_or_success_mismatch_refuses_reuse(facts, change):
    if change == 'inputs': facts['proof']['inputs']['tree'] = 'changed'
    elif change == 'assets': facts['proof']['inputs']['assets'] = 'changed'
    elif change == 'tool': facts['proof']['inputs']['tools']['node'] = 'v23'
    elif change in {'image-family', 'machine', 'system'}:
        facts['proof']['inputs']['tools'][{'image-family': 'image', 'machine': 'machine', 'system': 'system'}[change]] = 'changed'
    elif change == 'missing-proof-image': del facts['proof']['inputs']['tools']['imageVersion']
    elif change == 'missing-image':
        facts['inputs']['tools']['image'] = facts['proof']['inputs']['tools']['image'] = None
    elif change in {'workflow-bytes', 'helper-bytes'}:
        facts['qualifiers'][module.QUALIFIERS[change == 'helper-bytes']] = 'different-code'
    elif change == 'run': facts['proof']['runId'] += 1
    elif change == 'attempt': facts['proof']['attempt'] -= 1
    elif change == 'pr': facts['proof']['pr'] += 1
    elif change == 'head': facts['run']['head_sha'] = 'old-head'
    elif change == 'fork': facts['run']['head_repository']['full_name'] = 'someone/fork'
    elif change == 'event': facts['run']['event'] = 'workflow_dispatch'
    elif change == 'workflow': facts['run']['path'] = '.github/workflows/untrusted.yml'
    elif change == 'failure': facts['run']['conclusion'] = 'failure'
    elif change == 'running': facts['run']['status'] = 'in_progress'
    elif change == 'association': facts['run']['pull_requests'] = []
    elif change == 'tested': facts['commit']['sha'] = 'other'
    elif change == 'tree': facts['commit']['tree']['sha'] = 'different'
    elif change == 'parents': facts['commit']['parents'].reverse()
    elif change == 'job-attempt': facts['jobs'][0]['run_attempt'] = 1
    elif change == 'job-failed': facts['jobs'][0]['conclusion'] = 'failure'
    elif change == 'step-skipped': facts['jobs'][0]['steps'][1]['conclusion'] = 'skipped'
    elif change == 'ineligible': facts['proof']['eligible'] = False
    elif change == 'unknown-scope': facts['proof']['testScope'] = 'skipped'
    elif change == 'missing-scope': del facts['proof']['testScope']
    elif change == 'old-schema': facts['proof']['schema'] = 1
    elif change == 'classifier-bytes': facts['qualifiers'][module.QUALIFIERS[2]] = 'changed'
    with pytest.raises(ValueError):
        module.verify(**facts)


class FakeAPI:
    def __init__(self, facts):
        self.facts, self.calls = facts, []
        self.artifact = {'id': 201, 'name': 'frontend-proof-2', 'workflow_run': {'id': 101},
                         'digest': 'sha256:archive', 'expired': False, 'size_in_bytes': 500}

    def get(self, path):
        self.calls.append(path)
        f = self.facts
        if path == f"commits/{f['run']['head_sha']}/pulls?per_page=100":
            return getattr(self, 'head_prs', [f['pr']])
        if path.startswith('commits/'): return [f['pr']]
        if path.startswith('actions/workflows/'): return {'workflow_runs': [f['run']]}
        if '/artifacts?' in path: return {'artifacts': [self.artifact]}
        if path.startswith('git/commits/'): return f['commit']
        if path.startswith('contents/'):
            name = path[len('contents/'):].split('?')[0]
            return {'encoding': 'base64', 'size': len(name), 'content': base64.b64encode(name.encode()).decode()}
        if '/jobs?' in path: return {'jobs': f['jobs']}
        raise AssertionError(path)

    def archive(self, artifact):
        return self.facts['proof']


@pytest.fixture
def lookup(facts, monkeypatch, tmp_path):
    monkeypatch.setattr(module, 'identity', lambda root: facts['inputs'])
    monkeypatch.setattr(module, 'command', lambda *args, **kwargs: 'd' * 40)
    env = {'GITHUB_SHA': 'd' * 40, 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': 'owner/app'}
    return tmp_path, env, FakeAPI(facts)


def test_success_receipt_records_release_and_artifact_identity(lookup):
    result = module.find(*lookup)
    assert result['reused'] and result['selected'] == 'd' * 40
    assert result['artifactId'] == 201 and result['runId'] == 101 and result['attempt'] == 2
    assert result['association'] == 'run' and result['rejections'] == {}
    assert not any(path.startswith('commits/' + 'a' * 40) for path in lookup[2].calls)


def test_explicit_empty_run_association_uses_exact_merged_head_commit(lookup):
    root, env, api = lookup
    api.facts['run']['pull_requests'] = []
    result = module.find(root, env, api)
    assert result['reused'] and result['association'] == 'head_commit'
    assert api.calls.count('commits/' + 'a' * 40 + '/pulls?per_page=100') == 1


@pytest.mark.parametrize('linked', [None, {}, [{'number': 99}], 'missing'])
def test_fallback_never_replaces_missing_malformed_or_conflicting_run_association(lookup, linked):
    root, env, api = lookup
    if linked == 'missing':
        del api.facts['run']['pull_requests']
    else:
        api.facts['run']['pull_requests'] = linked
    result = module.find(root, env, api)
    assert not result['reused'] and result['rejections'] == {'pr_association': 1}
    assert not any(path.startswith('commits/' + 'a' * 40) for path in api.calls)


@pytest.mark.parametrize('change', ['number', 'unmerged', 'merge', 'head', 'fork',
    'base-repo', 'base', 'branch', 'empty', 'duplicate', 'oversized', 'malformed'])
def test_exact_head_fallback_refuses_any_association_mismatch(lookup, change):
    root, env, api = lookup
    api.facts['run']['pull_requests'] = []
    item = copy.deepcopy(api.facts['pr'])
    api.head_prs = [item]
    if change == 'number': item['number'] += 1
    elif change == 'unmerged': item['merged_at'] = None
    elif change == 'merge': item['merge_commit_sha'] = 'f' * 40
    elif change == 'head': item['head']['sha'] = 'f' * 40
    elif change == 'fork': item['head']['repo']['full_name'] = 'other/fork'
    elif change == 'base-repo': item['base']['repo']['full_name'] = 'other/app'
    elif change == 'base': item['base']['sha'] = 'f' * 40
    elif change == 'branch': item['base']['ref'] = 'release'
    elif change == 'empty': api.head_prs = []
    elif change == 'duplicate': api.head_prs.append(copy.deepcopy(item))
    elif change == 'oversized': api.head_prs = [None] * 101
    elif change == 'malformed': api.head_prs = None
    result = module.find(root, env, api)
    assert not result['reused'] and result['rejections'] == {'pr_association': 1}


@pytest.mark.parametrize('change', ['canonical-branch', 'proof-base', 'tree', 'step',
                                  'artifact', 'tool', 'helper', 'attempt'])
def test_head_commit_fallback_keeps_every_other_qualification(lookup, change):
    root, env, api = lookup
    f = api.facts
    f['run']['pull_requests'] = []
    if change == 'canonical-branch': f['pr']['base']['ref'] = 'release'
    elif change == 'proof-base':
        f['proof']['base'] = f['commit']['parents'][0]['sha'] = 'f' * 40
    elif change == 'tree': f['commit']['tree']['sha'] = 'f' * 40
    elif change == 'step': f['jobs'][0]['steps'][0]['conclusion'] = 'skipped'
    elif change == 'artifact': api.artifact['workflow_run']['id'] += 1
    elif change == 'tool': f['proof']['inputs']['tools']['node'] = 'v99'
    elif change == 'helper': f['inputs']['qualifiers'][module.QUALIFIERS[1]] = 'different'
    elif change == 'attempt': f['jobs'][0]['run_attempt'] += 1
    assert not module.find(root, env, api)['reused']


def test_rejection_diagnostics_do_not_emit_exception_text_or_identity(lookup, monkeypatch):
    root, env, api = lookup
    def unavailable(artifact):
        raise ValueError('private URL, token and arbitrary label')
    monkeypatch.setattr(api, 'archive', unavailable)
    result = module.find(root, env, api)
    assert result == {'reused': False, 'reason': 'no matching trusted proof',
                      'rejections': {'proof_lookup': 1}}


@pytest.mark.parametrize('change', ['historical', 'unmerged', 'different-merge', 'fork',
                                    'old-attempt-artifact', 'other-run-artifact', 'bad-tested', 'tampered'])
def test_unavailable_or_untrusted_proof_falls_back(lookup, change):
    root, env, api = lookup
    if change == 'historical': env['GITHUB_SHA'] = 'f' * 40
    elif change == 'unmerged': api.facts['pr']['merged_at'] = None
    elif change == 'different-merge': api.facts['pr']['merge_commit_sha'] = 'f' * 40
    elif change == 'fork': api.facts['pr']['head']['repo']['full_name'] = 'other/app'
    elif change == 'old-attempt-artifact': api.artifact['name'] = 'frontend-proof-1'
    elif change == 'other-run-artifact': api.artifact['workflow_run']['id'] = 99
    elif change == 'bad-tested': api.facts['proof']['tested'] = '../../other'
    elif change == 'tampered': api.facts['proof']['inputs']['assets'] = 'tampered'
    assert module.find(root, env, api)['reused'] is False
    if change == 'historical': assert api.calls == []


def archive_bytes(name='frontend-proof.json', content=b'{"schema":1}'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    return stream.getvalue()


@pytest.mark.parametrize('change', ['digest', 'expired', 'large-archive', 'large-record', 'path', 'bad-json'])
def test_artifact_transport_and_content_tampering_refused(monkeypatch, change):
    data = archive_bytes('../frontend-proof.json' if change == 'path' else 'frontend-proof.json',
                         b'x' * 64_001 if change == 'large-record' else b'broken' if change == 'bad-json' else b'{}')
    artifact = {'id': 1, 'expired': change == 'expired', 'size_in_bytes': len(data),
                'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
    if change == 'digest': artifact['digest'] = 'sha256:wrong'
    if change == 'large-archive': artifact['size_in_bytes'] = 1_000_001
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **kw: data)
    with pytest.raises(ValueError): module.GitHub('owner/app').archive(artifact)


def test_create_refuses_fork_or_manual_even_if_checkout_would_match(monkeypatch, tmp_path):
    path = tmp_path / 'event.json'
    path.write_text(json.dumps({'pull_request': {'head': {'repo': {'full_name': 'fork/app'}}}}))
    monkeypatch.setattr(module, 'identity', lambda root: pytest.fail('must not qualify untrusted source'))
    env = {'GITHUB_EVENT_PATH': str(path), 'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_REPOSITORY': 'owner/app'}
    assert module.create(tmp_path, env, 'full')['eligible'] is False
    env['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
    assert module.create(tmp_path, env, 'full')['eligible'] is False


def test_fingerprint_covers_all_tracked_inputs_and_detects_dirty_or_extra_assets(tmp_path, monkeypatch):
    paths = [*module.QUALIFIERS, 'components/web-client/src/app.js', 'components/web-client/tests/app.test.mjs',
             'tests/web/package.json', 'tests/web/package-lock.json', 'components/web-client.source.json',
             'scripts/web_client_build.py', 'components/web-client/vite.config.js',
             'pyproject.toml', 'amplifier_web/__init__.py', 'amplifier_web/static/app.js',
             'scripts/build.py', 'assets/logo.svg']
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    def git(*args):
        return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                        *args], cwd=tmp_path, stderr=subprocess.DEVNULL, text=True)
    git('init', '-q'); git('add', '.'); git('commit', '-qm', 'fixture')
    original = module.command
    monkeypatch.setattr(module, 'command', lambda *args, **kw:
                        'v22' if args[0] == 'node' else '10' if args[0] == 'npm' else original(*args, **kw))
    baseline = module.identity(tmp_path)
    monkeypatch.setenv('NODE_OPTIONS', '--max-old-space-size=4096')
    assert module.identity(tmp_path)['environment'] != baseline['environment']
    baseline = module.identity(tmp_path)
    for name in paths:
        (tmp_path / name).write_text(name + ' changed')
        with pytest.raises(subprocess.CalledProcessError): module.identity(tmp_path)
        git('add', name); git('commit', '-qm', name)
        changed = module.identity(tmp_path)
        assert changed['tree'] != baseline['tree'], name
        if name.startswith('amplifier_web/static/'):
            assert changed['assets'] != baseline['assets']
        baseline = changed
    (tmp_path / 'amplifier_web/static/new-chunk.js').write_text('untracked output')
    with pytest.raises(ValueError, match='Untracked'): module.identity(tmp_path)


def test_unexpected_lookup_error_writes_explicit_fallback(monkeypatch, tmp_path):
    destination, output = tmp_path / 'receipt.json', tmp_path / 'output'
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/app')
    monkeypatch.setattr(module, 'find', lambda *args: (_ for _ in ()).throw(RuntimeError('unexpected ZIP/API error')))
    monkeypatch.setattr('sys.argv', ['proof', 'reuse', '--destination', str(destination), '--output', str(output)])
    module.main()
    assert json.loads(destination.read_text())['reused'] is False
    assert output.read_text() == 'reused=false\n'


def test_lookup_network_budget_is_shared_between_requests(monkeypatch):
    now = [100]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    api = module.GitHub('owner/app')
    timeouts = []
    def request(*args, **kwargs):
        timeouts.append(kwargs['timeout'])
        now[0] += 12
        return '{}'
    monkeypatch.setattr(subprocess, 'check_output', request)
    api.get('first'); api.get('second')
    with pytest.raises(TimeoutError): api.get('third')
    assert timeouts == [20, 8]


def test_workflows_keep_full_fallback_and_fresh_package_runtime():
    load = lambda name: yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)
    fast, release = load('work-workflows.yml'), load('release.yml')
    steps = fast['jobs']['integration']['steps']
    names = [s.get('name') for s in steps]
    assert [name for name in names if name in module.STEPS] == list(module.STEPS)
    frontend = release['jobs']['prepare']['steps']
    fallback = next(s for s in frontend if s.get('name') == 'Validate frontend and committed assets')
    assert fallback['if'] == "steps.frontend-proof.outputs.reused != 'true'"
    assert 'historical_web_layout.py" validate' in fallback['run']
    assert 'git diff --exit-code -- amplifier_web/static' in fallback['run']
    # The copied adapter owns explicit current and historical full-check orders.
    import historical_web_layout
    current = historical_web_layout.commands(ROOT, 'validate')
    assert ['npm', 'test', '--prefix', '.build/web-client'] in current
    assert ['npm', 'test', '--prefix', 'tests/web'] in current
    assert current[-1] == ['npm', 'run', 'test:artifact-assets', '--prefix', '.build/web-client']
    assert release['jobs']['release']['needs'] == 'prepare'
    for name in ('Build and verify distributions', 'Verify surface context and recipe children through Core and loop-live',
                 'Require native goal and peer continuation regressions without skips'):
        check = next(s for s in frontend if s.get('name') == name)
        assert 'if' not in check  # Reusing frontend proof never skips package/native checks.
    receipt = next(s for s in frontend if s.get('name') == 'Record frontend qualification for this candidate')
    assert 'if' not in receipt and 'verify' in receipt['run'] and '--candidate' in receipt['run']


def test_workflow_retains_successful_build_before_committed_asset_failure():
    workflow = yaml.load((ROOT / '.github/workflows/work-workflows.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['integration']
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '30'
    steps = job['steps']
    source = next(step for step in steps if step.get('id') == 'build-source')
    assert steps[0] == {'uses': 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
                        'with': {'fetch-depth': '2'}} and steps[1] == source
    assert source['run'].splitlines() == [
        'echo "revision=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"',
        'echo "tree=$(git rev-parse HEAD^{tree})" >> "$GITHUB_OUTPUT"',
    ]
    build = next(step for step in steps if step.get('id') == 'frontend-build')
    assert build == {'name': 'Build frontend', 'id': 'frontend-build',
                     'run': 'python scripts/web_client_build.py build\n'}
    uploads = [step for step in steps if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02']
    assert len(uploads) == 2
    retained, proof = uploads
    create = next(step for step in steps if step.get('name') == 'Retain frontend build output')
    assert create == {
        'name': 'Retain frontend build output',
        'if': "${{ !cancelled() && steps.frontend-build.outcome == 'success' }}",
        'run': 'python scripts/frontend_build_artifact.py create --destination "$RUNNER_TEMP/frontend-build-output"',
    }
    assert retained == {
        'name': 'Upload frontend build output',
        'if': "${{ !cancelled() && steps.frontend-build.outcome == 'success' }}",
        'uses': 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
        'with': {
            'name': 'frontend-build-output-${{ github.run_attempt }}',
            'path': '${{ runner.temp }}/frontend-build-output/',
            'if-no-files-found': 'error', 'retention-days': '7',
        },
    }
    verify = next(step for step in steps if step.get('name') == 'Verify committed frontend assets')
    assert verify == {'name': 'Verify committed frontend assets',
                      'run': 'npm run test:artifact-assets --prefix .build/web-client\ngit diff --exit-code -- amplifier_web/static\n'}
    # Retention runs before the comparison; its failure cannot be turned into
    # qualification or prevent saving an already successful build after the fact.
    assert steps.index(build) < steps.index(create) < steps.index(retained) < steps.index(verify) < steps.index(proof)
    assert proof == {'name': 'Upload frontend proof', 'uses': 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
                     'with': {'name': 'frontend-proof-${{ github.run_attempt }}',
                              'path': '${{ runner.temp }}/frontend-proof.json',
                              'if-no-files-found': 'error', 'retention-days': '7'}}


@pytest.mark.parametrize('scope', ['full', 'release-smoke'])
def test_create_records_exact_executed_scope(tmp_path, monkeypatch, scope):
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': {'number': 1,
        'head': {'sha': 'a' * 40, 'repo': {'full_name': 'owner/app'}}, 'base': {'sha': 'b' * 40}}}))
    env = {'GITHUB_EVENT_PATH': str(event), 'GITHUB_EVENT_NAME': 'pull_request',
           'GITHUB_REPOSITORY': 'owner/app', 'GITHUB_SHA': 'c' * 40,
           'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1'}
    monkeypatch.setattr(module, 'command', lambda *args, **kwargs: 'c' * 40)
    monkeypatch.setattr(module, 'identity', lambda root: {'tree': 'd' * 40})
    assert module.create(tmp_path, env, scope)['testScope'] == scope
    with pytest.raises(ValueError, match='scope'):
        module.create(tmp_path, env, None)


def test_lookup_preserves_smoke_scope_without_claiming_full_tests(lookup):
    root, env, api = lookup
    api.facts['proof']['testScope'] = 'release-smoke'
    result = module.find(root, env, api)
    assert result['reused'] and result['testScope'] == 'release-smoke'


def test_fast_scope_selection_and_single_full_nightly_owner():
    load = lambda name: yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)
    fast = load('work-workflows.yml')['jobs']['integration']['steps']
    checkout = next(step for step in fast if step.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['fetch-depth'] == '2'
    test = next(step for step in fast if step.get('name') == 'Test frontend')
    assert test['env'] == {'PR_BASE': '${{ github.event.pull_request.base.sha }}',
                           'PR_HEAD': '${{ github.event.pull_request.head.sha }}'}
    assert 'frontend-test --base "$PR_BASE" --head "$PR_HEAD" --output "$GITHUB_OUTPUT"' in test['run']
    proof = next(step for step in fast if step.get('name') == 'Record frontend proof')
    assert '--test-scope "${{ steps.frontend-tests.outputs.scope }}"' in proof['run']
    for name in ('Install frontend dependencies', 'Build frontend', 'Verify committed frontend assets'):
        assert 'if' not in next(step for step in fast if step.get('name') == name)
    browser = load('browser-checks.yml')['jobs']['browser']['steps']
    full, = [step for step in browser if 'npm test --prefix .build/web-client' in step.get('run', '')]
    assert full['if'] == '${{ !inputs.multi_quote && !inputs.attachment_consumption && !inputs.app004_qualification && !inputs.mcp_boundary }}'
    assert 'pipefail' in full['run'] and 'output/browser-checks/frontend-units.log' in full['run']
    assert full['run'].count('npm test --prefix .build/web-client') == 1
    assert full['run'].count('npm test --prefix tests/web') == 1
    assert browser.index(full) > next(i for i, step in enumerate(browser) if 'npm ci --prefix .build/web-client' in step.get('run', ''))


def test_hosted_image_patch_reuse_records_both_without_relaxing_other_inputs(lookup):
    root, env, api = lookup
    original = copy.deepcopy(api.facts['proof'])
    api.facts['inputs']['tools']['imageVersion'] = '20261101.360.1'
    result = module.find(root, env, api)
    assert result['reused']
    assert result['runnerImageVersions'] == {'qualification': '20261014', 'publisher': '20261101.360.1'}
    assert api.facts['proof'] == original
    api.facts['inputs']['tools']['npm'] = 'changed'
    assert not module.find(root, env, api)['reused']


def test_manual_historical_release_carries_layout_adapter_before_selecting_source():
    release = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
    steps = release['jobs']['prepare']['steps']
    copied = next(i for i, step in enumerate(steps) if 'cp scripts/release.py' in step.get('run', ''))
    selected = next(i for i, step in enumerate(steps) if step.get('name') == 'Pin the verified selected commit')
    layout = next(i for i, step in enumerate(steps) if step.get('id') == 'web-layout')
    prepared = next(i for i, step in enumerate(steps) if 'historical_web_layout.py" prepare' in step.get('run', ''))
    proof = next(i for i, step in enumerate(steps) if step.get('id') == 'frontend-proof')
    assert copied < selected < layout < prepared < proof
    assert 'scripts/historical_web_layout.py' in steps[copied]['run']
    node = next(step for step in steps if step.get('uses', '').startswith('actions/setup-node@'))
    assert node['with']['cache-dependency-path'] == '${{ steps.web-layout.outputs.cache-files }}'
    assert 'if' not in steps[prepared]  # Adapter selects known source ownership, not an absent-helper guess.


def test_revision_targeted_workflows_keep_explicit_historical_browser_layout():
    load = lambda name: yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)
    for name, job in (('browser-checks.yml', 'browser'), ('public-readiness.yml', 'critical')):
        steps = load(name)['jobs'][job]['steps']
        install = next(step for step in steps if 'npx playwright install' in step.get('run', ''))
        assert 'working-directory' not in install
        assert 'if test -f tests/web/package.json; then' in install['run']
        assert 'cd tests/web' in install['run'] and 'cd frontend' in install['run']
    steps = load('browser-checks.yml')['jobs']['browser']['steps']
    scenario = next(step for step in steps if step.get('name') == 'Run all extended browser scenarios serially')
    assert '--plan tests/web/browser-checks.json' in scenario['run']
    assert '--plan frontend/tests/browser-checks.json' in scenario['run']
