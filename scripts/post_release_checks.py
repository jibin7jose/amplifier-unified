"""Verify published release identity/bytes and track asynchronous check failures.

Only the reporting job receives issue-write permission. This script never
publishes, replaces assets, changes tags, or installs anything on a shared host.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

CHECKS = ('resolve', 'installed-package', 'readiness', 'python', 'browser', 'integrations')
SMOKE_CHECKS = ('resolve', 'installed-package')
REPOSITORY = 'microsoft/amplifier-unified'
PUBLISHER = '.github/workflows/release.yml'
CANDIDATE_ARTIFACT = 'host-source-resolution'
CANDIDATE_UPLOAD = 'Retain selected candidate and qualified host dependency evidence'


def gh(*args, payload=None):
    command = ['gh', *args]
    if payload is not None:
        command += ['--input', '-']
    result = subprocess.check_output(command, input=None if payload is None else json.dumps(payload), text=True)
    return json.loads(result) if result.strip() else None


def stable_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Expected a stable release version')
    return value


def positive_id(value):
    if not isinstance(value, (str, int)) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise ValueError('Expected a positive integer run, attempt or artifact ID')
    return int(value)


def verify_publication_run(run, repository, run_id, attempt=None):
    if (repository != REPOSITORY or type(run.get('id')) is not int or run.get('id') != run_id
            or (run.get('repository') or {}).get('full_name') != repository
            or (run.get('head_repository') or {}).get('full_name') != repository
            or run.get('head_branch') != 'main' or run.get('path') != PUBLISHER
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or not re.fullmatch(r'[0-9a-f]{40}', str(run.get('head_sha', '')))):
        raise ValueError('Not the requested successful canonical main publisher run')
    if type(run.get('run_attempt')) is not int:
        raise ValueError('Unknown publisher attempt')
    actual_attempt = positive_id(run.get('run_attempt'))
    if attempt is not None and actual_attempt != attempt:
        raise ValueError('Publisher attempt differs from the requested attempt')
    return actual_attempt


def resolve_publication(repository, event_name, ref, event):
    """Pin an API-confirmed attempt; never silently switch an automatic event."""
    if repository != REPOSITORY:
        raise ValueError('Post-release verification requires the canonical repository')
    if event_name == 'workflow_dispatch':
        if ref != 'refs/heads/main':
            raise ValueError('Manual post-release verification requires main')
        run_id = positive_id(event.get('inputs', {}).get('publication_run_id'))
        run = gh('api', f'repos/{repository}/actions/runs/{run_id}')
    elif event_name == 'schedule':
        if ref != 'refs/heads/main':
            raise ValueError('Nightly verification requires main')
        # One immutable publisher snapshot, then the same attempt/artifact/tag
        # verification as automatic release smoke. Never test a moving main.
        runs = gh('api', f'repos/{repository}/actions/workflows/release.yml/runs'
                  '?branch=main&status=success&per_page=1')['workflow_runs']
        if len(runs) != 1:
            raise ValueError('No successful publisher is available for nightly verification')
        run = runs[0]
        run_id = positive_id(run.get('id'))
    elif event_name == 'workflow_run':
        run = event['workflow_run']
        run_id = positive_id(run.get('id'))
    else:
        raise ValueError('Unsupported post-release event')
    attempt = verify_publication_run(run, repository, run_id)
    exact = gh('api', f'repos/{repository}/actions/runs/{run_id}/attempts/{attempt}')
    verify_publication_run(exact, repository, run_id, attempt)
    if exact['head_sha'] != run['head_sha']:
        raise ValueError('Publisher head changed between run and attempt')
    return exact


def candidate_artifact(run, jobs, artifacts):
    # A successful partial rerun can reuse an earlier prepare. Select the latest
    # prepare at/before the publication attempt BEFORE checking its outcome:
    # a failed newer prepare must never fall back to an older successful one.
    applicable = []
    for job in jobs:
        if job.get('name') != 'prepare':
            continue
        attempt = job.get('run_attempt')
        if (job.get('run_id') != run['id'] or job.get('head_sha') != run['head_sha']
                or type(attempt) is not int or attempt < 1):
            raise ValueError('Unknown prepare job publisher identity or attempt')
        if attempt <= run['run_attempt']:
            applicable.append(job)
    if not applicable:
        raise ValueError('Missing applicable prepare job attempt')
    prepare_attempt = max(job['run_attempt'] for job in applicable)
    matching = [job for job in applicable if job['run_attempt'] == prepare_attempt]
    if (len(matching) != 1 or matching[0].get('status') != 'completed'
            or matching[0].get('conclusion') != 'success'):
        raise ValueError(f'Ambiguous or unsuccessful prepare job attempt {prepare_attempt}')
    # The publisher's artifact name predates attempt suffixes. GitHub's artifact
    # list is run-wide, not attempt-scoped. Bind its creation to the successful
    # upload step from the selected prepare attempt, then download only that ID.
    uploads = [step for step in matching[0].get('steps', []) if step.get('name') == CANDIDATE_UPLOAD
               and step.get('status') == 'completed' and step.get('conclusion') == 'success']
    if len(uploads) != 1:
        raise ValueError('Missing successful candidate upload step')
    start = datetime.fromisoformat(uploads[0]['started_at'])
    end = datetime.fromisoformat(uploads[0]['completed_at'])
    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ValueError('Unknown candidate upload window')
    candidates = []
    for artifact in artifacts:
        if artifact.get('name') != CANDIDATE_ARTIFACT:
            continue
        source = artifact.get('workflow_run') or {}
        if source.get('id') != run['id'] or source.get('head_sha') != run['head_sha']:
            raise ValueError('Candidate artifact belongs to a different publisher')
        created = datetime.fromisoformat(artifact['created_at'])
        if created.tzinfo is None:
            raise ValueError('Unknown candidate artifact creation time')
        if start <= created <= end:
            candidates.append(artifact)
    if len(candidates) != 1:
        raise ValueError('Missing or ambiguous candidate artifact for publisher attempt')
    artifact = candidates[0]
    positive_id(artifact.get('id'))
    if artifact.get('expired') is not False:
        raise ValueError('Candidate artifact is expired or unavailable')
    return artifact


def load_candidate(repository, run):
    prefix = f'repos/{repository}/actions'
    # filter=all includes earlier executions; the default latest and the
    # attempt-specific endpoint can omit prepare after a failed-jobs-only rerun.
    pages = gh('api', '--paginate', '--slurp',
               f'{prefix}/runs/{run["id"]}/jobs?filter=all&per_page=100')
    jobs = [job for page in pages for job in page['jobs']]
    pages = gh('api', '--paginate', '--slurp', f'{prefix}/runs/{run["id"]}/artifacts?per_page=100')
    artifact = candidate_artifact(run, jobs, [item for page in pages for item in page['artifacts']])
    data = subprocess.check_output(['gh', 'api', f'{prefix}/artifacts/{positive_id(artifact["id"])}/zip'])
    if artifact.get('digest') != 'sha256:' + hashlib.sha256(data).hexdigest():
        raise ValueError('Candidate artifact digest mismatch')
    # Read only the record; never extract archive paths into the checkout.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist().count('candidate.json') != 1 or archive.getinfo('candidate.json').file_size > 1_000_000:
            raise ValueError('Missing, duplicate or oversized candidate record')
        candidate = json.loads(archive.read('candidate.json'))
    if not isinstance(candidate, dict):
        raise ValueError('Candidate must be an object')
    return candidate


def resolve_candidate(candidate, release, tag_revision):
    version = stable_version(candidate.get('version'))
    revision = candidate.get('revision', '')
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Expected an immutable release revision')
    if (release.get('tagName') != 'v' + version or release.get('isDraft') is not False
            or release.get('isPrerelease') is not False or not release.get('publishedAt')):
        raise ValueError('Candidate has no matching published stable release')
    if tag_revision != revision:
        raise ValueError('Published tag differs from the qualified candidate')
    return {'version': version, 'tag': 'v' + version, 'revision': revision}


def published_revision(repository, tag):
    obj = gh('api', f'repos/{repository}/git/ref/tags/{tag}')['object']
    for _ in range(4):
        if obj['type'] == 'commit':
            return obj['sha']
        if obj['type'] != 'tag':
            break
        obj = gh('api', f'repos/{repository}/git/tags/{obj["sha"]}')['object']
    raise ValueError('Release tag does not resolve to a commit')


def verify_downloads(destination, version):
    version = stable_version(version)
    expected = {f'amplifier_unified-{version}-py3-none-any.whl', f'amplifier_unified-{version}.tar.gz'}
    entries = {}
    for line in (destination / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split()
        if name not in expected or name in entries or not re.fullmatch(r'[0-9a-f]{64}', checksum):
            raise ValueError('Unexpected or duplicate release checksum entry')
        entries[name] = checksum
    if set(entries) != expected:
        raise ValueError('Published checksums do not cover both distributions')
    for name, checksum in entries.items():
        with (destination / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                raise ValueError(f'Published asset checksum mismatch: {name}')
    report = {'version': version, 'assets': entries, 'verified': True}
    (destination / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def build_report(results, repository, run_id, publication_id, scope='broad'):
    if scope not in {'smoke', 'broad'}:
        raise ValueError('Unknown verification scope')
    expected = SMOKE_CHECKS if scope == 'smoke' else CHECKS
    outputs = results.get('resolve', {}).get('outputs', {})
    # Identity resolution and installation now share one read-only job. Keep
    # their individual outcomes in reports, including a failed/skipped install.
    if 'install_result' in outputs:
        results = {**results,
                   'resolve': {'result': outputs.get('identity_result'), 'outputs': outputs},
                   'installed-package': {'result': (outputs['install_result']
                       if outputs['install_result'] != 'success' else results['resolve'].get('result'))}}
    publication_id = str(positive_id(publication_id)) if publication_id else ''
    tag = outputs.get('tag') or (
        f'publication-run-{publication_id}' if publication_id else f'verification-run-{run_id}')
    revision = outputs.get('revision') or 'Unresolved; see identity verification failure'
    attempt = ('/attempts/' + str(positive_id(outputs['publication_run_attempt']))
               if outputs.get('publication_run_attempt') else '')
    publication = (f'[Publication run](https://github.com/{repository}/actions/runs/{publication_id}{attempt})'
                   if publication_id else 'Publication run: unverified (resolution failed)')
    failed = [name for name in expected if results.get(name, {}).get('result') != 'success']
    marker = f'<!-- amplifier-post-release{"-smoke" if scope == "smoke" else ""}:{tag} -->'
    title = f'{"Release smoke" if scope == "smoke" else "Post-release verification"}: {tag}'
    body = '\n'.join([
        marker, f'{"Release smoke" if scope == "smoke" else "Broad verification"} **{"failed" if failed else "passed"}** for `{tag}`.',
        f'Release tag: `{outputs["tag"]}`.' if outputs.get('tag') else 'Release tag: unresolved.',
        f'Published revision: `{revision}`.', '',
        f'[Verification run](https://github.com/{repository}/actions/runs/{run_id}) · {publication}', '',
        *[f'- {name}: {results.get(name, {}).get("result", "missing")}' for name in expected], '',
        ('Broad suites were not run in this smoke check; their failures remain separately tracked.'
         if scope == 'smoke' else 'Full configured broad coverage was requested.'), '',
        'These checks run after publication. This report does not withdraw the release, '
        'move a tag, replace assets, or change installed applications.',
    ])
    return {'failed': failed, 'marker': marker, 'title': title, 'body': body}


def track_report(repository, report):
    # Read all pages so reruns update the existing per-release issue, including
    # a previously closed issue. No issue is created for an all-green release.
    pages = gh('api', '--paginate', '--slurp', f'repos/{repository}/issues?state=all&per_page=100')
    issue = next((row for page in pages for row in page
                  if 'pull_request' not in row and report['marker'] in (row.get('body') or '')), None)
    if issue:
        gh('api', '--method', 'PATCH', f'repos/{repository}/issues/{issue["number"]}', payload={
            'body': report['body'], 'state': 'open' if report['failed'] else 'closed',
            'state_reason': 'reopened' if report['failed'] else 'completed',
        })
    elif report['failed']:
        gh('api', '--method', 'POST', f'repos/{repository}/issues', payload={
            'title': report['title'], 'body': report['body'],
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    resolve = commands.add_parser('resolve')
    resolve.add_argument('--output', type=Path, required=True)
    download = commands.add_parser('download')
    download.add_argument('--version', required=True)
    download.add_argument('--destination', type=Path, required=True)
    report = commands.add_parser('report')
    report.add_argument('--summary-only', action='store_true',
                        help='Write the run summary without issue-write API calls')
    args = parser.parse_args()
    repository = os.environ['GITHUB_REPOSITORY']
    if args.command == 'resolve':
        # Freeze the actual latest published release before selecting its publisher.
        # A historical publisher rerun must not silently qualify an older release.
        latest = (gh('release', 'view', '--repo', repository,
                     '--json', 'tagName,isDraft,isPrerelease,publishedAt')
                  if os.environ['GITHUB_EVENT_NAME'] == 'schedule' else None)
        run = resolve_publication(repository, os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REF'],
                                  json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()))
        # Retain only verified publisher identity even if later artifact/tag
        # resolution fails. Never send raw dispatch input to the report.
        with args.output.open('a') as stream:
            stream.write(f'publication_run_id={run["id"]}\npublication_run_attempt={run["run_attempt"]}\n')
        candidate = load_candidate(repository, run)
        tag = 'v' + stable_version(candidate.get('version'))
        if latest is not None and latest.get('tagName') != tag:
            raise ValueError('Latest successful publisher does not match the latest published release; '
                             'dispatch the matching publication run explicitly')
        release = gh('release', 'view', tag, '--repo', repository,
                     '--json', 'tagName,isDraft,isPrerelease,publishedAt')
        result = resolve_candidate(candidate, release, published_revision(repository, tag))
        with args.output.open('a') as stream:
            stream.write(''.join(f'{key}={value}\n' for key, value in result.items()))
    elif args.command == 'download':
        version = stable_version(args.version)
        args.destination.mkdir(parents=True, exist_ok=False)
        subprocess.run(['gh', 'release', 'download', 'v' + version, '--repo', repository,
                        '--pattern', f'amplifier_unified-{version}-py3-none-any.whl',
                        '--pattern', f'amplifier_unified-{version}.tar.gz', '--pattern', 'SHA256SUMS',
                        '--dir', str(args.destination)], check=True)
        verify_downloads(args.destination, version)
    else:
        report = build_report(json.loads(os.environ['RELEASE_RESULTS']), repository,
                              os.environ['GITHUB_RUN_ID'], os.environ['RELEASE_RUN_ID'],
                              os.environ.get('VERIFICATION_SCOPE', 'broad'))
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(report['body'] + '\n')
        if not args.summary_only:
            track_report(repository, report)
        if report['failed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
