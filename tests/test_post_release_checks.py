"""Post-publication checks bind actual release bytes and report missing proof."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('post_release_checks', ROOT / 'scripts/post_release_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


@pytest.fixture
def publication(tmp_path, monkeypatch):
    repository = 'microsoft/amplifier-unified'
    run = {'id': 41183545409, 'run_attempt': 2, 'path': '.github/workflows/release.yml',
           'repository': {'full_name': repository}, 'head_repository': {'full_name': repository},
           'head_branch': 'main', 'head_sha': 'b' * 40, 'status': 'completed', 'conclusion': 'success'}
    exact = json.loads(json.dumps(run))
    candidate = {'version': '0.41.0', 'revision': 'a' * 40}
    release = {'tagName': 'v0.41.0', 'isDraft': False, 'isPrerelease': False, 'publishedAt': 'now'}
    upload = {'name': 'Retain selected candidate and qualified host dependency evidence',
              'status': 'completed', 'conclusion': 'success',
              'started_at': '2026-10-14T12:00:00Z', 'completed_at': '2026-10-14T12:00:10Z'}
    job = {'name': 'prepare', 'run_id': run['id'], 'run_attempt': 2, 'head_sha': run['head_sha'],
           'status': 'completed', 'conclusion': 'success', 'steps': [upload]}
    jobs = [job]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('candidate.json', json.dumps(candidate))
        archive.writestr('resolution.json', '{}')
    data = buffer.getvalue()
    artifact = {'id': 31, 'name': 'host-source-resolution', 'expired': False,
                'created_at': '2026-10-14T12:00:05Z',
                'workflow_run': {'id': run['id'], 'head_sha': run['head_sha']},
                'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
    artifacts = [artifact]
    prefix = f'repos/{repository}/actions/runs/{run["id"]}'
    responses = {
        ('api', prefix): run,
        ('api', prefix + '/attempts/2'): exact,
        ('api', '--paginate', '--slurp', prefix + '/jobs?filter=all&per_page=100'): [{'jobs': jobs}],
        ('api', '--paginate', '--slurp', prefix + '/artifacts?per_page=100'): [{'artifacts': artifacts}],
        ('release', 'view', 'v0.41.0', '--repo', repository,
         '--json', 'tagName,isDraft,isPrerelease,publishedAt'): release,
        ('api', f'repos/{repository}/git/ref/tags/v0.41.0'): {'object': {'type': 'commit', 'sha': 'a' * 40}},
    }
    calls, downloads = [], []
    def gh(*args, **kwargs):
        calls.append(args)
        assert not kwargs
        return responses[args]
    def archive_download(command, **kwargs):
        downloads.append(command)
        assert command == ['gh', 'api', f'repos/{repository}/actions/artifacts/31/zip']
        assert not kwargs
        return data
    monkeypatch.setattr(checks, 'gh', gh)
    monkeypatch.setattr(checks.subprocess, 'check_output', archive_download)
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'inputs': {'publication_run_id': str(run['id'])}}))
    output = tmp_path / 'outputs'
    for key, value in {'GITHUB_REPOSITORY': repository, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                       'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_PATH': str(event),
                       'GITHUB_SHA': 'c' * 40}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, 'argv', ['post_release_checks.py', 'resolve', '--output', str(output)])
    return {'run': run, 'exact': exact, 'job': job, 'jobs': jobs, 'upload': upload, 'artifact': artifact,
            'artifacts': artifacts, 'release': release, 'responses': responses,
            'calls': calls, 'downloads': downloads, 'event': event, 'output': output, 'prefix': prefix}


@pytest.mark.parametrize('value', ['', '0', '-1', '1.5', '1e2', ' 12', '12\n', '１２', True, None,
                                  '41183545409; echo unsafe', '../41183545409'])
def test_manual_run_id_rejected_before_any_api_or_download(publication, value):
    publication['event'].write_text(json.dumps({'inputs': {'publication_run_id': value}}))
    with pytest.raises(ValueError, match='positive integer'):
        checks.main()
    assert publication['calls'] == publication['downloads'] == []
    assert not publication['output'].exists()


@pytest.mark.parametrize(('key', 'value'), [
    ('GITHUB_REF', 'refs/heads/repair'), ('GITHUB_REF', 'refs/tags/v0.41.0'),
    ('GITHUB_REPOSITORY', 'fork/amplifier-unified'), ('GITHUB_EVENT_NAME', 'push'),
])
def test_manual_context_rejected_before_api(publication, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        checks.main()
    assert publication['calls'] == publication['downloads'] == []
    assert not publication['output'].exists()


@pytest.mark.parametrize('source', ['run', 'exact'])
@pytest.mark.parametrize(('field', 'value'), [
    ('id', 41183545410), ('id', '41183545409'), ('id', 41183545409.0),
    ('repository', {'full_name': 'other/repo'}),
    ('head_repository', {'full_name': 'fork/amplifier-unified'}), ('head_repository', None),
    ('head_branch', 'repair'), ('path', '.github/workflows/python-checks.yml'),
    ('status', 'in_progress'), ('conclusion', 'failure'), ('conclusion', 'cancelled'),
    ('conclusion', None), ('run_attempt', None), ('run_attempt', 0), ('run_attempt', True),
    ('run_attempt', '2'), ('run_attempt', 2.0), ('head_sha', 'main'),
])
def test_manual_publisher_mismatch_stops_before_artifacts(publication, source, field, value):
    publication[source][field] = value
    with pytest.raises(ValueError):
        checks.main()
    assert all(args[0] == 'api' and args[-1] in (
        publication['prefix'], publication['prefix'] + '/attempts/2') for args in publication['calls'])
    assert publication['downloads'] == []
    assert not publication['output'].exists()


@pytest.mark.parametrize(('field', 'value'), [('run_attempt', 3), ('head_sha', 'd' * 40)])
def test_attempt_lookup_cannot_silently_change_identity(publication, field, value):
    publication['exact'][field] = value
    with pytest.raises(ValueError):
        checks.main()
    assert len(publication['calls']) == 2
    assert publication['downloads'] == []
    assert not publication['output'].exists()


@pytest.mark.parametrize('event_name', ['workflow_dispatch', 'workflow_run'])
@pytest.mark.parametrize('prepare_attempt', [1, 2])
def test_both_routes_resolve_exact_attempt_then_published_candidate(
    publication, monkeypatch, event_name, prepare_attempt,
):
    # Publication attempt 2 can reuse prepare from attempt 1 after rerunning only
    # failed jobs. Its successful publication identity still stays at attempt 2.
    publication['job']['run_attempt'] = prepare_attempt
    if event_name == 'workflow_run':
        publication['event'].write_text(json.dumps({'workflow_run': publication['exact']}))
        monkeypatch.setenv('GITHUB_EVENT_NAME', event_name)
        # A newer attempt must not replace the completed triggering attempt.
        publication['run'].update(run_attempt=3, status='in_progress', conclusion=None)
    checks.main()
    assert publication['output'].read_text().splitlines() == [
        'publication_run_id=41183545409', 'publication_run_attempt=2',
        'version=0.41.0', 'tag=v0.41.0', 'revision=' + 'a' * 40]
    assert len(publication['downloads']) == 1
    assert ('api', publication['prefix'] + '/attempts/2') in publication['calls']
    assert ('api', '--paginate', '--slurp',
            publication['prefix'] + '/jobs?filter=all&per_page=100') in publication['calls']
    assert (('api', publication['prefix']) in publication['calls']) == (event_name == 'workflow_dispatch')
    assert publication['calls'][-1][-1].endswith('/git/ref/tags/v0.41.0')


@pytest.mark.parametrize('mutation', [
    'future-job-attempt', 'job-run', 'job-head', 'job-failed', 'upload-failed', 'no-upload', 'unknown-time',
    'old-artifact', 'new-artifact', 'ambiguous', 'expired', 'artifact-run', 'artifact-head',
])
def test_candidate_download_requires_exact_attempt_upload(publication, mutation):
    if mutation == 'future-job-attempt':
        publication['job']['run_attempt'] = 3
    elif mutation == 'job-run':
        publication['job']['run_id'] += 1
    elif mutation == 'job-head':
        publication['job']['head_sha'] = 'd' * 40
    elif mutation == 'job-failed':
        publication['job']['conclusion'] = 'failure'
    elif mutation == 'upload-failed':
        publication['upload']['conclusion'] = 'failure'
    elif mutation == 'no-upload':
        publication['job']['steps'] = []
    elif mutation == 'unknown-time':
        publication['upload']['started_at'] = '2026-10-14T12:00:00'
    elif mutation in ('old-artifact', 'new-artifact'):
        publication['artifact']['created_at'] = (
            '2026-10-13T12:00:05Z' if mutation == 'old-artifact' else '2026-10-15T12:00:05Z')
    elif mutation == 'ambiguous':
        publication['artifacts'].append({**publication['artifact'], 'id': 32})
    elif mutation == 'expired':
        publication['artifact']['expired'] = True
    elif mutation == 'artifact-run':
        publication['artifact']['workflow_run']['id'] += 1
    else:
        publication['artifact']['workflow_run']['head_sha'] = 'd' * 40
    with pytest.raises(ValueError):
        checks.main()
    assert publication['downloads'] == []
    assert not any(args[0] == 'release' for args in publication['calls'])
    assert publication['output'].read_text() == 'publication_run_id=41183545409\npublication_run_attempt=2\n'


@pytest.mark.parametrize('mutation', [
    'failed', 'cancelled', 'skipped', 'in-progress', 'ambiguous', 'ambiguous-failed',
    'no-upload', 'failed-upload', 'other-run', 'other-head',
])
def test_latest_prepare_cannot_fall_back_to_earlier_success(publication, mutation):
    older = json.loads(json.dumps(publication['job']))
    older['run_attempt'] = 1
    publication['jobs'].insert(0, older)
    latest = publication['job']
    if mutation in ('failed', 'cancelled', 'skipped'):
        latest['conclusion'] = 'failure' if mutation == 'failed' else mutation
    elif mutation == 'in-progress':
        latest.update(status='in_progress', conclusion=None)
    elif mutation in ('ambiguous', 'ambiguous-failed'):
        publication['jobs'].append({**latest, 'conclusion': (
            'failure' if mutation == 'ambiguous-failed' else 'success')})
    elif mutation == 'no-upload':
        latest['steps'] = []
    elif mutation == 'failed-upload':
        publication['upload']['conclusion'] = 'failure'
    elif mutation == 'other-run':
        latest['run_id'] += 1
    else:
        latest['head_sha'] = 'd' * 40
    with pytest.raises(ValueError):
        checks.main()
    assert publication['downloads'] == []
    assert not any(args[0] == 'release' for args in publication['calls'])
    assert publication['output'].read_text() == 'publication_run_id=41183545409\npublication_run_attempt=2\n'


@pytest.mark.parametrize('attempt', [None, 0, -1, True, '1', 1.0, 3])
def test_missing_or_unknown_earlier_prepare_does_not_download(publication, attempt):
    if attempt is None:
        publication['jobs'].clear()
    else:
        publication['job']['run_attempt'] = attempt
    with pytest.raises(ValueError):
        checks.main()
    assert publication['downloads'] == []
    assert not any(args[0] == 'release' for args in publication['calls'])


def test_all_job_pages_select_latest_applicable_prepare_not_future(publication):
    older = json.loads(json.dumps(publication['job']))
    older.update(run_attempt=1, conclusion='failure')
    future = json.loads(json.dumps(publication['job']))
    future['run_attempt'] = 3
    future['steps'][0].update(started_at='2026-10-15T12:00:00Z', completed_at='2026-10-15T12:00:10Z')
    # Deliberately reverse chronological order across pages. Neither first,
    # last, nor latest successful overall is a safe selection.
    publication['responses'][('api', '--paginate', '--slurp',
                              publication['prefix'] + '/jobs?filter=all&per_page=100')] = [
        {'jobs': [future]}, {'jobs': [publication['job']]}, {'jobs': [older]}]
    publication['artifacts'].append({**publication['artifact'], 'id': 32,
                                    'created_at': '2026-10-15T12:00:05Z'})
    checks.main()
    assert len(publication['downloads']) == 1
    assert publication['downloads'][0][-1].endswith('/artifacts/31/zip')
    assert 'publication_run_attempt=2\n' in publication['output'].read_text()


def test_run_wide_artifacts_cannot_substitute_another_attempt(publication):
    publication['artifacts'].insert(0, {**publication['artifact'], 'id': 30,
                                      'created_at': '2026-10-13T12:00:05Z'})
    publication['artifacts'].append({**publication['artifact'], 'id': 32,
                                    'created_at': '2026-10-15T12:00:05Z'})
    checks.main()
    assert publication['downloads'][0][-1].endswith('/artifacts/31/zip')


def test_changed_candidate_archive_does_not_reach_release_lookup(publication):
    publication['artifact']['digest'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError, match='digest'):
        checks.main()
    assert len(publication['downloads']) == 1
    assert not any(args[0] == 'release' for args in publication['calls'])
    assert 'tag=' not in publication['output'].read_text()


def test_manual_resolution_still_requires_matching_published_tag(publication):
    publication['responses'][('api', 'repos/microsoft/amplifier-unified/git/ref/tags/v0.41.0')][
        'object']['sha'] = 'b' * 40
    with pytest.raises(ValueError, match='differs'):
        checks.main()
    assert publication['output'].read_text() == 'publication_run_id=41183545409\npublication_run_attempt=2\n'


def test_published_identity_uses_candidate_not_later_workflow_head():
    candidate = {'version': '1.2.3', 'revision': 'a' * 40}
    release = {'tagName': 'v1.2.3', 'isDraft': False, 'isPrerelease': False, 'publishedAt': 'now'}
    assert checks.resolve_candidate(candidate, release, 'a' * 40)['revision'] == 'a' * 40
    with pytest.raises(ValueError, match='differs'):
        checks.resolve_candidate(candidate, release, 'b' * 40)
    for key, value in [('isDraft', True), ('isPrerelease', True), ('publishedAt', None), ('tagName', 'v1.2.4')]:
        with pytest.raises(ValueError, match='published stable'):
            checks.resolve_candidate(candidate, {**release, key: value}, 'a' * 40)


@pytest.mark.parametrize('version', ['main', '../1.2.3', '1.2.3\nrevision=main', '1.2.3-rc1', None])
def test_invalid_release_identifiers_are_rejected(version):
    with pytest.raises(ValueError):
        checks.stable_version(version)


@pytest.mark.parametrize('mutation', ['none', 'bytes', 'missing', 'duplicate', 'path'])
def test_both_published_distributions_must_match_checksums(tmp_path, mutation):
    names = ['amplifier_unified-1.2.3-py3-none-any.whl', 'amplifier_unified-1.2.3.tar.gz']
    lines = []
    for name in names:
        (tmp_path / name).write_bytes(name.encode())
        lines.append(hashlib.sha256(name.encode()).hexdigest() + '  ' + name)
    if mutation == 'bytes':
        (tmp_path / names[0]).write_bytes(b'changed')
    elif mutation == 'missing':
        lines.pop()
    elif mutation == 'duplicate':
        lines.append(lines[0])
    elif mutation == 'path':
        lines[0] = lines[0].replace(names[0], '../' + names[0])
    (tmp_path / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')
    if mutation == 'none':
        assert checks.verify_downloads(tmp_path, '1.2.3')['verified']
    else:
        with pytest.raises(ValueError):
            checks.verify_downloads(tmp_path, '1.2.3')
        assert not (tmp_path / 'verification.json').exists()


def results():
    return {name: {'result': 'success'} for name in checks.CHECKS}


@pytest.mark.parametrize('outcome', ['failure', 'cancelled', 'skipped', None])
def test_missing_or_failed_checks_are_not_reported_as_success(outcome):
    value = results()
    if outcome is None:
        value.pop('browser')
    else:
        value['browser']['result'] = outcome
    report = checks.build_report(value, 'owner/app', '12', '11')
    assert report['failed'] == ['browser']
    assert 'publication-run-11' in report['title']
    assert 'actions/runs/12' in report['body']


def test_resolver_failure_report_does_not_invent_release_or_publication():
    value = {name: {'result': 'skipped'} for name in checks.CHECKS}
    value['resolve']['result'] = 'failure'
    report = checks.build_report(value, 'owner/app', '12', '')
    assert report['title'] == 'Post-release verification: verification-run-12'
    assert report['failed'] == list(checks.CHECKS)
    assert 'Release tag: unresolved.' in report['body']
    assert 'Publication run: unverified' in report['body']
    assert '[Publication run](' not in report['body']
    assert 'actions/runs/12' in report['body']


def test_manual_report_reads_only_verified_resolver_output(monkeypatch, tmp_path):
    value = results()
    value['resolve']['outputs'] = {'tag': 'v0.41.0', 'revision': 'a' * 40,
                                 'publication_run_id': '41183545409', 'publication_run_attempt': '2'}
    for key, content in {'GITHUB_REPOSITORY': 'owner/app', 'GITHUB_RUN_ID': '12',
                         'RELEASE_RUN_ID': '41183545409', 'RELEASE_RESULTS': json.dumps(value),
                         'GITHUB_STEP_SUMMARY': str(tmp_path / 'summary')}.items():
        monkeypatch.setenv(key, content)
    monkeypatch.setattr(sys, 'argv', ['post_release_checks.py', 'report'])
    reports = []
    monkeypatch.setattr(checks, 'track_report', lambda repository, report: reports.append(report))
    checks.main()
    assert len(reports) == 1 and reports[0]['failed'] == []
    assert 'actions/runs/41183545409/attempts/2)' in reports[0]['body']
    assert (tmp_path / 'summary').read_text() == reports[0]['body'] + '\n'


@pytest.mark.parametrize('failed,existing', [(False, False), (True, False), (True, True), (False, True)])
def test_failure_tracking_is_per_release_and_recovery_closes_it(monkeypatch, failed, existing):
    value = results()
    value['resolve']['outputs'] = {'tag': 'v1.2.3', 'revision': 'a' * 40}
    if failed:
        value['readiness']['result'] = 'failure'
    report = checks.build_report(value, 'owner/app', '12', '11')
    calls = []
    def gh(*args, payload=None):
        calls.append((args, payload))
        if '--paginate' in args:
            return [[{'number': 10, 'body': report['marker']}] if existing else []]
        return {}
    monkeypatch.setattr(checks, 'gh', gh)
    checks.track_report('owner/app', report)
    writes = [(args, payload) for args, payload in calls if payload is not None]
    if not failed and not existing:
        assert writes == []
    elif existing:
        assert len(writes) == 1 and 'PATCH' in writes[0][0]
        assert writes[0][1]['state'] == ('open' if failed else 'closed')
    else:
        assert len(writes) == 1 and 'POST' in writes[0][0]
        assert writes[0][1]['title'] == 'Post-release verification: v1.2.3'


def test_fast_required_check_runs_on_every_pr_without_exhaustive_suites():
    workflow = yaml.load((ROOT / '.github/workflows/work-workflows.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['pull_request'] == ''
    job = workflow['jobs']['integration']
    assert job['name'] == 'Fast release checks'
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    for name in ('test_post_release_checks.py', 'test_browser_check_runner.py',
                 'test_active_client_diagnostics.py'):
        assert name in commands
    assert 'npm test' in commands and 'git diff --exit-code' in commands
    assert 'playwright' not in commands and 'node tests/' not in commands


def test_nightly_freezes_latest_published_release_and_verifies_its_publisher(publication, monkeypatch):
    repository = checks.REPOSITORY
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'schedule')
    publication['responses'][('release', 'view', '--repo', repository,
                              '--json', 'tagName,isDraft,isPrerelease,publishedAt')] = publication['release']
    publication['responses'][('api', f'repos/{repository}/actions/workflows/release.yml/runs'
                              '?branch=main&status=success&per_page=1')] = {'workflow_runs': [publication['run']]}
    checks.main()
    assert publication['output'].read_text().endswith('revision=' + 'a' * 40 + '\n')
    assert len(publication['downloads']) == 1
    # Frozen release, exact attempt, retained candidate and actual tag; never main HEAD.
    assert publication['calls'][0][0:2] == ('release', 'view')
    assert ('api', publication['prefix'] + '/attempts/2') in publication['calls']
    assert publication['calls'][-1][-1].endswith('/git/ref/tags/v0.41.0')


def test_nightly_refuses_a_successful_historical_publisher_for_newer_latest_release(publication, monkeypatch):
    repository = checks.REPOSITORY
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'schedule')
    publication['responses'][('release', 'view', '--repo', repository,
                              '--json', 'tagName,isDraft,isPrerelease,publishedAt')] = {
                                  **publication['release'], 'tagName': 'v0.42.0'}
    publication['responses'][('api', f'repos/{repository}/actions/workflows/release.yml/runs'
                              '?branch=main&status=success&per_page=1')] = {'workflow_runs': [publication['run']]}
    with pytest.raises(ValueError, match='does not match the latest published release'):
        checks.main()
    assert 'revision=' not in publication['output'].read_text()
    assert not any('git/ref/tags/' in str(call) for call in publication['calls'])


@pytest.mark.parametrize('ref,runs', [('refs/heads/feature', [{}]), ('refs/heads/main', [])])
def test_nightly_cannot_fall_back_to_arbitrary_main_or_missing_publisher(monkeypatch, ref, runs):
    monkeypatch.setattr(checks, 'gh', lambda *args: {'workflow_runs': runs})
    with pytest.raises(ValueError):
        checks.resolve_publication(checks.REPOSITORY, 'schedule', ref, {})


def test_smoke_skips_broad_without_claiming_or_closing_broad_success(monkeypatch):
    value = {name: {'result': 'skipped'} for name in checks.CHECKS}
    for name in checks.SMOKE_CHECKS:
        value[name]['result'] = 'success'
    value['resolve']['outputs'] = {'tag': 'v1.2.3', 'revision': 'a' * 40}
    broad = checks.build_report(value, 'owner/app', '11', '10', scope='broad')
    smoke = checks.build_report(value, 'owner/app', '12', '10', scope='smoke')
    assert smoke['failed'] == []
    assert broad['failed'] == ['readiness', 'python', 'browser', 'integrations']
    assert smoke['marker'] != broad['marker']
    assert '- browser:' not in smoke['body'] and 'separately tracked' in smoke['body']
    calls = []
    def gh(*args, payload=None):
        calls.append((args, payload))
        assert payload is None
        return [[{'number': 10, 'body': broad['body'], 'state': 'open'}]]
    monkeypatch.setattr(checks, 'gh', gh)
    checks.track_report('owner/app', smoke)
    assert len(calls) == 1  # A smoke pass leaves the legacy broad failure untouched.


@pytest.mark.parametrize('outcome', ['failure', 'cancelled', 'skipped', None])
def test_smoke_requires_actual_installed_package_success(outcome):
    value = results()
    if outcome is None:
        value.pop('installed-package')
    else:
        value['installed-package']['result'] = outcome
    assert checks.build_report(value, 'owner/app', '12', '11', scope='smoke')['failed'] == ['installed-package']


def test_unknown_scope_cannot_downgrade_required_checks():
    with pytest.raises(ValueError, match='scope'):
        checks.build_report(results(), 'owner/app', '12', '11', scope='typo')
