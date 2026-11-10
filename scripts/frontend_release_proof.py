"""Reuse only successful, same-repository PR frontend proofs for an exact tree.

This deliberately binds the whole tracked tree, not a hand-maintained frontend
dependency allowlist. A missed optimization runs the ordinary checks. GitHub's
run, attempt, job and immutable artifact metadata establish provenance; a JSON
file claiming success, by itself, establishes nothing.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

WORKFLOW = '.github/workflows/work-workflows.yml'
QUALIFIERS = (WORKFLOW, 'scripts/frontend_release_proof.py', 'scripts/integration_checks.py')
ARTIFACT = 'frontend-proof-'
STEPS = ('Install frontend dependencies', 'Test frontend', 'Build frontend',
         'Verify committed frontend assets', 'Record frontend proof', 'Upload frontend proof')
REJECTIONS = {
    'Proof inputs or provenance changed': 'proof_identity',
    'Qualifying workflow/helper differs from trusted release inputs': 'qualifying_source',
    'Missing runner image identity': 'runner_identity',
    'Not a trusted successful repository PR run': 'run_identity',
    'Workflow run is not associated with the merged PR': 'pr_association',
    'Tested PR merge tree is not the release tree': 'tested_merge',
    'Missing exact successful frontend job attempt': 'job_attempt',
    'Frontend proof steps did not all succeed': 'job_steps',
}


def association(run, pr, head_prs, selected, proof_base, repository):
    """Use exact-head API facts only for GitHub's explicitly empty run list."""
    linked = run.get('pull_requests')
    if isinstance(linked, list) and any(
            isinstance(item, dict) and item.get('number') == pr['number'] for item in linked):
        return 'run'
    # Missing/malformed/nonempty conflicting metadata is never repaired by this
    # fallback. The extra API request shares the ordinary lookup's deadline.
    if (linked != [] or not isinstance(head_prs, list) or len(head_prs) > 100
            or not pr.get('merged_at') or pr.get('merge_commit_sha') != selected
            or pr.get('base', {}).get('ref') != 'main'
            or pr['base'].get('sha') != proof_base):
        return None
    matching = []
    for item in head_prs:
        if not isinstance(item, dict):
            continue
        if (item.get('number') == pr['number'] and item.get('merged_at')
                and item.get('merge_commit_sha') == selected
                and item.get('head', {}).get('sha') == run['head_sha'] == pr['head']['sha']
                and item.get('head', {}).get('repo', {}).get('full_name') == repository
                and item.get('base', {}).get('repo', {}).get('full_name') == repository
                and item.get('base', {}).get('ref') == 'main'
                and item.get('base', {}).get('sha') == pr['base']['sha']):
            matching.append(item)
    return 'head_commit' if len(matching) == 1 else None


def command(*args, root=None):
    return subprocess.check_output(args, cwd=root, text=True, timeout=30).strip()


def identity(root):
    # Check actual checkout bytes, not just an unchanged HEAD label. In
    # particular, git diff alone would miss new generated/untracked assets.
    command('git', 'diff', '--exit-code', 'HEAD', '--', root=root)
    if command('git', 'ls-files', '--others', '--exclude-standard', root=root):
        raise ValueError('Untracked source/build inputs or assets')
    assets = {}
    for path in sorted((root / 'amplifier_web/static').rglob('*')):
        if path.is_file():
            assets[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    environment = {key: value for key, value in os.environ.items()
                   if key in {'CI', 'NODE_OPTIONS', 'NODE_ENV', 'TZ', 'LANG'}
                   or key.startswith(('VITE_', 'npm_config_', 'NPM_CONFIG_'))}
    env_files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for folder in (root, root / 'frontend', root / 'frontend/.client')
                 for path in folder.glob('.env*') if path.is_file()}
    return {
        'tree': command('git', 'rev-parse', 'HEAD^{tree}', root=root),
        'assets': hashlib.sha256(json.dumps(assets, sort_keys=True).encode()).hexdigest(),
        'qualifiers': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in QUALIFIERS},
        'environment': hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest(),
        'envFiles': env_files,
        'tools': {'node': command('node', '--version'), 'npm': command('npm', '--version'),
                  'system': platform.system(), 'machine': platform.machine(),
                  'image': os.environ.get('ImageOS'), 'imageVersion': os.environ.get('ImageVersion')},
    }


def create(root, env, test_scope):
    if test_scope not in {'full', 'release-smoke'}:
        raise ValueError('Unknown frontend test scope')
    event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_text())
    pr = event.get('pull_request', {})
    # Fork/manual runs still execute their normal checks, but cannot supply a
    # release proof. No token or secret is needed to produce this receipt.
    if env.get('GITHUB_EVENT_NAME') != 'pull_request' or (
            pr.get('head', {}).get('repo', {}).get('full_name') != env['GITHUB_REPOSITORY']):
        return {'schema': 2, 'eligible': False}
    tested = command('git', 'rev-parse', 'HEAD', root=root)
    if tested != env['GITHUB_SHA']:
        raise ValueError('Checkout is not the triggering PR merge commit')
    if (root / 'components/web-client.source.json').is_file():
        from web_client_build import verify
        from web_client_development import verify_view
        verify(root)
        verify_view(root, root / 'frontend/.client')
    return {'schema': 2, 'eligible': True, 'testScope': test_scope, 'inputs': identity(root),
            'repository': env['GITHUB_REPOSITORY'], 'runId': int(env['GITHUB_RUN_ID']),
            'attempt': int(env['GITHUB_RUN_ATTEMPT']), 'pr': pr['number'],
            'head': pr['head']['sha'], 'base': pr['base']['sha'],
            'tested': tested}


class GitHub:
    def __init__(self, repository):
        self.prefix = 'repos/' + repository + '/'
        self.deadline = time.monotonic() + 20

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Frontend proof lookup budget exhausted')
        return remaining

    def get(self, path):
        return json.loads(subprocess.check_output(['gh', 'api', self.prefix + path],
                                                 text=True, timeout=self.remaining()))

    def archive(self, artifact):
        if artifact.get('expired') or not 0 < artifact.get('size_in_bytes', 0) <= 1_000_000:
            raise ValueError('Expired or oversized proof artifact')
        data = subprocess.check_output(['gh', 'api', self.prefix +
                                       f"actions/artifacts/{int(artifact['id'])}/zip"], timeout=self.remaining())
        if len(data) > 1_000_000 or artifact.get('digest') != 'sha256:' + hashlib.sha256(data).hexdigest():
            raise ValueError('Proof archive digest mismatch')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.namelist() != ['frontend-proof.json']:
                raise ValueError('Unexpected proof archive contents')
            if archive.getinfo('frontend-proof.json').file_size > 64_000:
                raise ValueError('Oversized proof record')
            value = json.loads(archive.read('frontend-proof.json'))
            if not isinstance(value, dict):
                raise ValueError('Proof must be an object')
            return value


def frontend_inputs(inputs):
    # Hosted-image patch releases are recorded, not frontend compiler identity.
    # Exact OS/image family, architecture, Node/npm, source and environment stay
    # bound. This exception does not apply to any native build/cache receipt.
    if not isinstance(inputs, dict) or not isinstance(inputs.get('tools'), dict):
        raise ValueError('Proof inputs or provenance changed')
    return {**inputs, 'tools': {key: value for key, value in inputs['tools'].items()
                               if key != 'imageVersion'}}


def verify(proof, *, inputs, run, pr, jobs, commit, qualifiers, repository,
           head_prs=None, selected=None):
    """Pure verifier; every identity is checked against independent API facts."""
    expected = {'schema': 2, 'eligible': True, 'repository': repository,
                'runId': run['id'], 'attempt': run['run_attempt'], 'pr': pr['number'],
                'head': pr['head']['sha']}
    if (proof.get('testScope') not in {'full', 'release-smoke'}
            or any(proof.get(key) != value for key, value in expected.items())
            or frontend_inputs(proof.get('inputs')) != frontend_inputs(inputs)):
        raise ValueError('Proof inputs or provenance changed')
    if qualifiers != inputs['qualifiers']:
        raise ValueError('Qualifying workflow/helper differs from trusted release inputs')
    if (not inputs['tools'].get('image') or not inputs['tools'].get('imageVersion')
            or not proof['inputs']['tools'].get('imageVersion')):
        raise ValueError('Missing runner image identity')
    if (run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('event') != 'pull_request' or run.get('path') != WORKFLOW
            or run.get('head_repository', {}).get('full_name') != repository
            or run.get('head_sha') != pr['head']['sha']
            or pr.get('head', {}).get('repo', {}).get('full_name') != repository
            or pr.get('base', {}).get('repo', {}).get('full_name') != repository):
        raise ValueError('Not a trusted successful repository PR run')
    associated = association(run, pr, head_prs, selected, proof.get('base'), repository)
    if associated is None:
        raise ValueError('Workflow run is not associated with the merged PR')
    # The PR workflow checks out GitHub's synthetic merge commit. Prove the
    # tested tree/parents from GitHub, not from the uploaded receipt alone.
    parents = [parent['sha'] for parent in commit.get('parents', [])]
    if (commit.get('sha') != proof.get('tested') or commit.get('tree', {}).get('sha') != inputs['tree']
            or parents != [proof.get('base'), pr['head']['sha']]):
        raise ValueError('Tested PR merge tree is not the release tree')
    matching = [job for job in jobs if job.get('name') == 'Fast release checks'
                and job.get('run_id') == run['id'] and job.get('run_attempt') == run['run_attempt']
                and job.get('conclusion') == 'success']
    if len(matching) != 1:
        raise ValueError('Missing exact successful frontend job attempt')
    completed = {step['name'] for step in matching[0].get('steps', [])
                 if step.get('status') == 'completed' and step.get('conclusion') == 'success'}
    if not set(STEPS) <= completed:
        raise ValueError('Frontend proof steps did not all succeed')
    return associated


def find(root, env, api):
    selected = command('git', 'rev-parse', 'HEAD', root=root)
    # Historical selections keep their old qualification behavior. Also avoid
    # treating a later main commit as interchangeable with the publishing run.
    if selected != env.get('GITHUB_SHA') or env.get('GITHUB_REF') != 'refs/heads/main':
        return {'reused': False, 'reason': 'historical or non-main selection'}
    inputs = identity(root)
    repository = env['GITHUB_REPOSITORY']
    rejected = {}
    def reject(reason):
        # Fixed labels only: no raw API/exception text or unbounded identities.
        rejected[reason] = min(300, rejected.get(reason, 0) + 1)
    prs = api.get(f'commits/{selected}/pulls?per_page=100')
    for pr in prs:
        if not pr.get('merged_at') or pr.get('merge_commit_sha') != selected:
            continue
        if pr.get('head', {}).get('repo', {}).get('full_name') != repository:
            continue
        runs = api.get('actions/workflows/work-workflows.yml/runs?event=pull_request&status=success'
                       f"&head_sha={pr['head']['sha']}&per_page=3")['workflow_runs']
        for run in runs[:3]:
            try:
                attempt = int(run['run_attempt'])
                artifacts = api.get(f"actions/runs/{run['id']}/artifacts?per_page=100")['artifacts']
                candidates = [item for item in artifacts if item.get('name') == ARTIFACT + str(attempt)]
                if len(candidates) != 1:
                    reject('artifact_count')
                    continue
                artifact = candidates[0]
                if artifact.get('workflow_run', {}).get('id') != run['id']:
                    reject('artifact_run')
                    continue
                proof = api.archive(artifact)
                if not re.fullmatch('[0-9a-f]{40}', str(proof.get('tested', ''))):
                    raise ValueError('Invalid tested commit identity')
                commit = api.get(f"git/commits/{proof['tested']}")
                qualifiers = {}
                for name in QUALIFIERS:
                    content = api.get(f"contents/{name}?ref={proof['tested']}")
                    if content.get('encoding') != 'base64' or content.get('size', 0) > 64_000:
                        raise ValueError('Invalid qualifying source')
                    qualifiers[name] = hashlib.sha256(base64.b64decode(content['content'])).hexdigest()
                jobs = api.get(f"actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100")['jobs']
                head_prs = None
                if run.get('pull_requests') == []:
                    if run.get('head_sha') != pr['head']['sha'] or not re.fullmatch('[0-9a-f]{40}', run['head_sha']):
                        raise ValueError('Not a trusted successful repository PR run')
                    head_prs = api.get(f"commits/{run['head_sha']}/pulls?per_page=100")
                associated = verify(proof, inputs=inputs, run=run, pr=pr, jobs=jobs, commit=commit,
                    qualifiers=qualifiers, repository=repository, head_prs=head_prs, selected=selected)
                return {'reused': True, 'inputs': inputs, 'tested': proof['tested'],
                        'runId': run['id'], 'attempt': attempt, 'artifactId': artifact['id'],
                        'artifactDigest': artifact['digest'], 'selected': selected,
                        'association': associated, 'testScope': proof['testScope'],
                        'runnerImageVersions': {'qualification': proof['inputs']['tools']['imageVersion'],
                                                'publisher': inputs['tools']['imageVersion']},
                        'rejections': rejected}
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
                reject(REJECTIONS.get(str(exc), 'proof_lookup'))
                continue
    return {'reused': False, 'reason': 'no matching trusted proof', 'rejections': rejected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'reuse'])
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--test-scope', choices=['full', 'release-smoke'],
                        help='Actual successful Fast test scope; required when creating a proof')
    args = parser.parse_args()
    root = Path.cwd()
    if args.action == 'create':
        value = create(root, os.environ, args.test_scope)
    else:
        try:
            value = find(root, os.environ, GitHub(os.environ['GITHUB_REPOSITORY']))
        except Exception:
            # Lookup is only an optimization. Unknown API/ZIP/version errors
            # must fail closed into the complete checks, never bypass them or
            # make publication unavailable solely because proof reuse failed.
            value = {'reused': False, 'reason': 'proof unavailable or incompatible; run fresh frontend checks'}
    args.destination.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    if args.output:
        with args.output.open('a') as stream:
            stream.write('reused=' + str(value.get('reused', False)).lower() + '\n')
    print('Frontend proof reused' if value.get('reused') else 'Frontend checks will run normally')


if __name__ == '__main__':
    main()
