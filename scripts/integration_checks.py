"""Select owned integration checks and require execution, including collection.

The ordinary host suite may lack optional dependencies. Its designated integration
owner may not silently skip them. This runner never installs dependencies or retries
work; lane preparation is separate and its receipts are retained alongside JUnit.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'scripts/integration-coverage.json'


def manifest():
    return json.loads(MANIFEST.read_text())


def select_lanes(changed=None, coverage=None, *, include_manual=False):
    coverage = coverage or manifest()
    return [{'lane': name, 'os': lane['runner']} for name, lane in coverage['lanes'].items()
            if lane['mode'] in ({'automatic', 'manual'} if include_manual else {'automatic'}) and (changed is None or any(
                fnmatch.fnmatchcase(path, pattern)
                for path in changed for pattern in coverage['commonPaths'] + lane['paths']))]


def release_only_changes(changed, base, head):
    """Prove a version-only preparation, not a branch/label or evidence waiver.

    Fast checks still validate release identity and rebuild/compare every static
    asset. Compare all dependency/build metadata, ignoring only this app's version.
    """
    metadata = {'pyproject.toml', 'uv.lock', 'amplifier_web/__init__.py'}
    if not metadata <= set(changed) or any(
        path not in metadata and path != 'amplifier_web/release-notes.json'
        and not path.startswith(('amplifier_web/static/', 'docs/'))
        and not ('/' not in path and path.endswith('.md')) for path in changed
    ):
        return False

    def read(revision, path):
        return subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT).decode()

    def normalized(revision):
        project = tomllib.loads(read(revision, 'pyproject.toml'))
        version = project['project'].pop('version')
        if project['project']['name'] != 'amplifier-unified' or not isinstance(version, str):
            raise ValueError('Not the application project')
        lock = tomllib.loads(read(revision, 'uv.lock'))
        local, = [row for row in lock['package'] if row['name'] == 'amplifier-unified']
        if local['source'] != {'editable': '.'} or local.pop('version') != version:
            raise ValueError('Not the matching local application lock record')
        init = read(revision, 'amplifier_web/__init__.py')
        init, count = re.subn(r'^__version__ = "' + re.escape(version) + r'"$',
                              '__version__ = "<release>"', init, flags=re.MULTILINE)
        if count != 1:
            raise ValueError('Not the matching application version declaration')
        return version, (project, lock, init)

    try:
        old_version, old = normalized(base)
        new_version, new = normalized(head)
        return old_version != new_version and old == new
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError):
        return False  # Missing/malformed comparison keeps the ordinary owners.


def revision_changes(base, head, *, merge_base=True):
    """Return a proven PR comparison, or None when it is unavailable."""
    if not all(re.fullmatch(r'[0-9a-f]{40}', value or '') for value in (base, head)):
        return None
    try:
        ancestor = (subprocess.check_output(['git', 'merge-base', base, head], cwd=ROOT, text=True).strip()
                    if merge_base else base)
        changed = subprocess.check_output(
            ['git', 'diff', '--no-renames', '--name-only', '-z', ancestor, head], cwd=ROOT).decode().split('\0')
        changed = [path for path in changed if path]
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        return None
    return changed, ancestor


def select_revision_lanes(base, head, *, include_manual=False):
    """PR selection; absent/unavailable comparisons retain the full matrix."""
    comparison = revision_changes(base, head)
    if comparison is None:
        return select_lanes(include_manual=include_manual)
    changed, ancestor = comparison
    if release_only_changes(changed, ancestor, head):
        return []
    return select_lanes(changed, include_manual=include_manual)


def frontend_test_scope(base, head):
    # Exact trees suffice in a shallow PR merge checkout. A changed newer base
    # conservatively keeps full tests instead of requiring historical ancestry.
    comparison = revision_changes(base, head, merge_base=False)
    if comparison is not None and release_only_changes(*comparison, head):
        return 'release-smoke'
    return 'full'


def test_frontend(base, head, output=None):
    scope = frontend_test_scope(base, head)
    print('Frontend test scope: ' + scope, flush=True)
    selected = ROOT / '.build/web-client'
    commands = ([(['node', '--test', 'tests/build-identity.test.mjs',
                   'tests/build-discovery.test.mjs', 'tests/update-release-summary.test.mjs'], selected)]
                if scope == 'release-smoke' else
                [(['npm', 'test', '--prefix', '.build/web-client'], ROOT),
                 (['npm', 'test', '--prefix', 'tests/web'], ROOT)])
    for command, cwd in commands:
        result = subprocess.run(command, cwd=cwd)
        if result.returncode:
            return result.returncode
    if output:
        with output.open('a') as stream:
            stream.write('scope=' + scope + '\n')
    return 0


def verify_junit(path, selectors):
    root = ET.parse(path).getroot()
    if any(n.tag in {'failure', 'error', 'skipped'} for n in root.iter()):
        raise ValueError('Integration results contain a failure, error, or unexpected skip')
    for suite in root.iter('testsuite'):
        if any(int(suite.get(key, '0')) for key in ('failures', 'errors', 'skipped')):
            raise ValueError('Integration suite counters report a failure, error, or skip')
    cases = list(root.iter('testcase'))
    if not cases:
        raise ValueError('Integration report contains no executed cases')
    identities = [(c.get('classname', ''), c.get('name', '')) for c in cases]
    for selector in selectors:
        file, _, function = selector.partition('::')
        module = file.removesuffix('.py').replace('/', '.')
        if not any((cls == module or cls.startswith(module + '.')) and
                   (not function or name == function or name.startswith(function + '['))
                   for cls, name in identities):
            raise ValueError('Required integration selector did not execute: ' + selector)
    return len(cases)


def source_identity(revision=None):
    # Include served assets and browser fixtures as well as Python. Hash all
    # file types in source directories so an image/WASM/HTML dependency cannot
    # silently escape provenance merely because it is not executable code.
    excluded = {'__pycache__', 'node_modules', '.pytest_cache', '.git', 'output'}
    directories = [*ROOT.glob('amplifier_*'), ROOT / 'scripts', ROOT / 'tests',
                   ROOT / '.github/workflows', ROOT / 'packages', ROOT / 'components']
    files = sorted({p for parent in directories if parent.is_dir() for p in parent.rglob('*')
                    if p.is_file() and not excluded.intersection(p.relative_to(ROOT).parts)} |
                   {ROOT / 'pyproject.toml', ROOT / 'uv.lock'} |
                   {p for p in (ROOT / 'bundle.yaml',) if p.is_file()})
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes() + b'\0')
    if revision is None:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True)
        revision = result.stdout.strip() if result.returncode == 0 else 'unversioned-archive'
    return {'baseRevision': revision, 'testedSourceDigest': digest.hexdigest()}


def run_lane(lane, python, output, environment=None, revision=None):
    spec = manifest()['lanes'][lane]
    if spec.get('command'):
        raise ValueError('This lane has an explicit qualification runner: ' + ' '.join(spec['command']))
    output.mkdir(parents=True, exist_ok=True)
    junit = output / 'junit.xml'
    # Never accept a stale successful report after an interrupted or empty run.
    junit.unlink(missing_ok=True)
    receipt = output / 'result.json'
    receipt.unlink(missing_ok=True)
    env = os.environ.copy()
    env.update(environment or {})
    command = [python, '-m', 'pytest', *spec['selectors'], '-q', '--tb=short',
               '--junitxml=' + str(junit)]
    result = {'lane': lane, 'selectors': spec['selectors'], 'exitCode': None,
              **source_identity(revision),
              'passed': False}
    try:
        completed = subprocess.run(command, cwd=ROOT, env=env)
        result['exitCode'] = completed.returncode
        after = source_identity(revision)
        result['sourceUnchanged'] = all(result[key] == value for key, value in after.items())
        if not result['sourceUnchanged']:
            result['sourceAfter'] = after
            raise ValueError('Integration source changed during test execution')
        result['tests'] = verify_junit(junit, spec['selectors'])
        if completed.returncode:
            raise ValueError('pytest exited unsuccessfully')
        result['passed'] = True
    except (OSError, ValueError, ET.ParseError) as error:
        result['error'] = str(error)
    receipt.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--changed', type=Path, help='newline-separated changed paths; absent means all automated lanes')
    plan.add_argument('--base', help='Exact PR base SHA for content-aware release preparation selection')
    plan.add_argument('--head', help='Exact PR head SHA; unavailable comparisons retain ordinary coverage')
    plan.add_argument('--include-manual', action='store_true', help='Also report affected manual owners; does not execute them')
    scope = commands.add_parser('fast-scope', help='Prove version-only preparation before selecting unchanged Fast checks')
    scope.add_argument('--base')
    scope.add_argument('--head')
    scope.add_argument('--output', type=Path)
    frontend = commands.add_parser('frontend-test')
    frontend.add_argument('--base', help='Exact PR base; absent means full frontend tests')
    frontend.add_argument('--head', help='Exact PR head; absent means full frontend tests')
    frontend.add_argument('--output', type=Path, help='Append successful test scope to GitHub outputs')
    run = commands.add_parser('run')
    run.add_argument('--lane', choices=manifest()['lanes'], required=True)
    run.add_argument('--python', default=sys.executable)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--env-json', type=Path)
    run.add_argument('--source-revision', help='Base revision for an exported source tree; exact tested bytes are also hashed')
    check = commands.add_parser('check')
    check.add_argument('--lane', choices=manifest()['lanes'], required=True)
    check.add_argument('--junit', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'fast-scope':
        scope = frontend_test_scope(args.base, args.head)
        if args.output:
            with args.output.open('a') as stream:
                stream.write('scope=' + scope + '\n')
        print(json.dumps({'scope': scope}))
        return 0
    if args.command == 'frontend-test':
        return test_frontend(args.base, args.head, args.output)
    if args.command == 'plan':
        lanes = (select_revision_lanes(args.base, args.head, include_manual=args.include_manual)
                 if args.base is not None or args.head is not None else
                 select_lanes(args.changed.read_text().splitlines() if args.changed else None,
                              include_manual=args.include_manual))
        print(json.dumps({'include': lanes}))
        return 0
    if args.command == 'check':
        print(f"{verify_junit(args.junit, manifest()['lanes'][args.lane]['selectors'])} passed; no skips")
        return 0
    return run_lane(args.lane, args.python, args.output.resolve(),
                    json.loads(args.env_json.read_text()) if args.env_json else None, args.source_revision)


if __name__ == '__main__':
    raise SystemExit(main())
