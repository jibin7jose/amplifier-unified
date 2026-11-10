"""Fixed manual permissions history proof. Synthetic fixtures; no full campaign.

Reuse the maintained process runner, build identity and wheel RECORD verifier.
One build, one wheel, no retries, and no context-managed supplement. The existing
Questions journey runs in full, including its held New chat/dirty-preview probe.
"""
from __future__ import annotations

import ast
from collections import Counter
import importlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/permissions-history'
PYTHON_TESTS = (
    'tests/test_approval_recovery.py',
    'tests/test_approval_feedback.py',
    'tests/test_host_approvals.py',
    'tests/test_conversation_stop_status.py',
)
NODE_TESTS = (
    'tests/approval-card.test.mjs',
    'tests/settings-action-queue.test.mjs',
    'tests/pending-view.test.mjs',
    'tests/conversation-navigation.test.mjs',
    'tests/fixture-lifecycle.test.mjs',
)
FIXTURES = ('approval_lifecycle_server.py', 'questions_ui_server.py', 'settings_ui_server.py')
# Four successful closeHistory calls are separate evidence, not duplicate cases.
APPROVAL_CHECKS = (
    'Close accepted', 'Close accepted', 'Close accepted', 'Close accepted',
    'Pending approve completes during held history', 'Pending deny completes during held history',
    'Held history/newer document wins', 'Dirty edit retained and replacement refused',
    'Newer Close hides immediately before either receipt', 'Held history/newer Close accepted',
    'Stop fences later-allow', 'Stop fences pending-preflight',
    'Unknown lost-ack stays fenced', 'Passive lost-ack recovery permits only fresh dismissal',
    'Unknown malformed-ack stays fenced', 'Recovering preflight cannot send its decision',
    'Passive malformed-ack recovery permits only fresh dismissal',
)
# The one structured loop is deliberately bound to its exact authored matrix.
# Do not evaluate its reply lambdas or silently ignore a new registration form.
STOP_LOOKUP_MATRIX = """for(const [name,reply] of [
 ['missing',()=>null],['empty',()=>({})],['unknown',body=>({...body,status:'unknown'})],
 ['wrong status',body=>({...body,status:'completed'})],['boolean acceptance',body=>({...body,accepted:true})],
 ...['sessionId','commandId','clientId'].map(key=>['wrong '+key,body=>({...body,status:'accepted',[key]:'other'})]),
 ...['sessionId','commandId','clientId'].map(key=>['missing '+key,body=>{
  const receipt={...body,status:'accepted'};delete receipt[key];return receipt;
 }]),
 ['non-object',()=>('accepted')],
 ['transport failure',()=>{throw Error('Disconnected')}],
 ['malformed JSON',()=>{throw SyntaxError('Malformed JSON')}],
])test(`passive Stop lookup stays fenced on ${name}`,"""
STOP_LOOKUP_NAMES = (
    'missing', 'empty', 'unknown', 'wrong status', 'boolean acceptance',
    'wrong sessionId', 'wrong commandId', 'wrong clientId',
    'missing sessionId', 'missing commandId', 'missing clientId',
    'non-object', 'transport failure', 'malformed JSON',
)


def qualification_environment(environ):
    """Keep CI identity and tool/certificate discovery, never credentials/overrides."""
    keep = ('PATH', 'LD_LIBRARY_PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT',
            'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
            'NODE_EXTRA_CA_CERTS', 'GIT_SSL_CAINFO',
            'GITHUB_ACTIONS', 'GITHUB_EVENT_NAME', 'GITHUB_SHA', 'GITHUB_RUN_ID',
            'GITHUB_RUN_ATTEMPT', 'GITHUB_REPOSITORY', 'RUNNER_TEMP')
    return {key: environ[key] for key in keep if key in environ}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def python_selection(root):
    """Derive current parametrized cases without importing tests or product code."""
    expected = {}
    for path in PYTHON_TESTS:
        tree = ast.parse((root / path).read_text())
        functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name.startswith('test_')]
        assert functions and not any(isinstance(n, ast.ClassDef) and n.name.startswith('Test')
                                     for n in tree.body), path
        for node in functions:
            count = 1
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == 'pytest.mark.parametrize':
                    values = decorator.args[1]
                    # Count authored rows without evaluating nested values such
                    # as CLIENT in an invalid-envelope case. Starred/computed
                    # matrices need an explicit review, not a guessed count.
                    assert isinstance(values, (ast.List, ast.Tuple)) and values.elts, node.name
                    assert not any(isinstance(value, ast.Starred) for value in values.elts), node.name
                    count *= len(values.elts)
            key = path + '::' + node.name
            assert key not in expected
            expected[key] = count
    return expected


def node_names(text):
    """Expand the selected literal/loop registrations without executing JavaScript."""
    literals = re.findall(r'''^test\(((?:'(?:[^'\\]|\\.)*')|(?:"(?:[^"\\]|\\.)*")),''', text, re.M)
    names = [ast.literal_eval(value) for value in literals]
    registrations = len(literals)
    loops = re.findall(r'^for\(const (\w+) of (\[[^\n]+\])\)test\(`([^`]+)`,', text, re.M)
    for variable, values, template in loops:
        values = ast.literal_eval(values)
        assert isinstance(values, list) and values and all(isinstance(v, str) for v in values)
        marker = '${' + variable + '}'
        assert template.count(marker) == 1 and '${' not in template.replace(marker, '')
        names.extend(template.replace(marker, value) for value in values)
        registrations += 1
    concatenated = re.findall(
        r"^for\(const (\w+) of (\[[^\n]+\])\)test\(('(?:[^'\\]|\\.)*')\+(\w+)\+('(?:[^'\\]|\\.)*'),",
        text, re.M)
    for variable, values, prefix, reference, suffix in concatenated:
        values = ast.literal_eval(values)
        assert variable == reference and isinstance(values, list) and values
        assert all(isinstance(value, str) for value in values)
        names.extend(ast.literal_eval(prefix) + value + ast.literal_eval(suffix) for value in values)
        registrations += 1
    if STOP_LOOKUP_MATRIX in text:
        assert text.count(STOP_LOOKUP_MATRIX) == 1
        names.extend('passive Stop lookup stays fenced on ' + name for name in STOP_LOOKUP_NAMES)
        registrations += 1
    # Counts every bare test(...) including loop/nested registrations. Unknown
    # forms must be reviewed instead of disappearing behind a /^test/ filter.
    assert registrations == len(re.findall(r'(?<![\w.])test\(', text)), 'Unrecognized Node test registration'
    assert names and len(names) == len(set(names)), 'Duplicate Node test names'
    return names


def node_selection(root):
    selected = {}
    for path in NODE_TESTS:
        owner = root / 'tests/web/fixture-lifecycle.test.mjs' if path == 'tests/fixture-lifecycle.test.mjs' else root / '.build/web-client' / path
        selected[path] = node_names(owner.read_text())
    names = [name for group in selected.values() for name in group]
    assert len(names) == len(set(names)), 'TAP names must be unique across the fixed selection'
    return selected


def verify_tap(text, selected):
    pairs = re.findall(r'^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$', text, re.M)
    counts = {key: int(value) for key, value in pairs}
    assert len(pairs) == len(counts) == 6, counts
    names = re.findall(r'^ok \d+ - (.+)$', text, re.M)
    expected = [name for group in selected.values() for name in group]
    assert len(names) == len(set(names)) and Counter(names) == Counter(expected), names
    assert counts == {'tests': len(names), 'pass': len(names), 'fail': 0,
                      'cancelled': 0, 'skipped': 0, 'todo': 0}, counts
    assert re.findall(r'^1\.\.(\d+)$', text, re.M) == [str(len(names))]
    assert not re.search(r'^\s*not ok\b|^\s*ok\s+\d+[^\n]*#\s*(?:SKIP|TODO)\b', text, re.M | re.I)
    return {'counts': counts, 'names': names, 'selectedFiles': list(selected)}


def source_pytest():
    """Retain collection and product origins in the process running pytest."""
    assert sys.version_info[:2] == (3, 13) and not sys.flags.optimize
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
    import pytest
    expected = python_selection(ROOT)
    packages = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']['packages']

    class Collection:
        def pytest_collection_finish(self, session):
            nodes = [item.nodeid for item in session.items]
            save(OUTPUT / 'python-collection.json', nodes)
            assert len(nodes) == len(set(nodes)) == sum(expected.values()), nodes
            assert Counter(node.split('[', 1)[0] for node in nodes) == Counter(expected), nodes

    args = ['-c', str(ROOT / 'pyproject.toml'), '--rootdir=' + str(ROOT), '-p', 'no:cacheprovider',
            '-v', '-ra', '--tb=short', '--junitxml=' + str(OUTPUT / 'python.xml'),
            *[str(ROOT / path) for path in PYTHON_TESTS]]
    save(OUTPUT / 'python-command.json', {'call': 'pytest.main', 'args': args, 'expected': expected})
    code = pytest.main(args, plugins=[Collection()])
    origins = {}
    for name, module in tuple(sys.modules.items()):
        file = getattr(module, '__file__', None)
        if file and name.split('.')[0] in packages:
            path = Path(file).resolve()
            assert path.is_relative_to(ROOT / name.split('.')[0]), (name, path)
            origins[name] = str(path)
    assert origins and 'amplifier_web' in origins
    save(OUTPUT / 'source-imports.json', {'modules': origins, 'executable': sys.executable})
    raise SystemExit(code)


def installed_probe():
    """-I probe outside the checkout; fixtures additionally check their own roots."""
    candidate = json.loads((OUTPUT / 'candidate.json').read_text())
    names = [*candidate['packages'], 'amplifier_core', 'amplifier_foundation',
             'amplifier_module_loop_live', 'amplifier_module_context_simple']
    modules = {name: str(Path(importlib.import_module(name).__file__).resolve()) for name in names}
    assert not Path.cwd().is_relative_to(ROOT)
    assert all(not Path(path).resolve().is_relative_to(ROOT) for path in sys.path)
    assert all(Path(path).is_relative_to(Path(sys.prefix)) for path in modules.values())
    print(json.dumps({'modules': modules, 'python': sys.version, 'executable': sys.executable,
        'cwd': str(Path.cwd()), 'sysPath': sys.path, 'distributions': sorted([
            {'name': d.metadata['Name'], 'version': d.version,
             'direct': json.loads(d.read_text('direct_url.json') or 'null')}
            for d in metadata.distributions()], key=lambda row: row['name'])}))


def verify_approval(folder, package, node_modules):
    from cold_restore_checks import manifest, sha
    directory, = folder.glob('approval-lifecycle-*')
    proof = json.loads((directory / 'report.json').read_text())
    cleanup = json.loads((directory / 'cleanup.json').read_text())
    readiness = json.loads((directory / 'validation-readiness.json').read_text())
    assert proof['passed'] is True and proof['incomplete'] is False and proof['error'] is None
    names = [row['name'] for row in proof['checks']]
    assert Counter(names) == Counter(APPROVAL_CHECKS), 'Require all 17 original/recovery checks by name'
    assert proof['stopAcknowledgementRecovery'] is True and proof['overwrittenStopRecovery'] is True
    for row in proof['checks']:
        if row['name'].startswith('Unknown ') or row['name'] == 'Recovering preflight cannot send its decision':
            assert row['decisionRequests'] == [], row['name']
        if row['name'].startswith('Passive '):
            assert row['samePage'] is True and row['stopReceipt']['accepted'] is True
            assert len(row['decisionRequests']) == 1 and row['decisionRequests'][0]['action'] == 'capability.revoke'
            assert row['dismissalReceipt']['accepted'] is True
            binding = {'sessionId': row['stopCommand']['args']['sessionId'],
                       'commandId': row['stopCommand']['id'], 'clientId': row['clientId']}
            assert any(lookup.get('httpStatus') == 200 and lookup['body'] == binding
                       and lookup['response'] == {**binding, 'status': 'accepted'} for lookup in row['lookups'])
            if row['name'] == 'Passive lost-ack recovery permits only fresh dismissal':
                assert row['supersedingStop']['id'] != row['stopCommand']['id']
                assert row['supersedingStop']['receipt']['accepted'] is True
    assert cleanup['exitCode'] == 0 and cleanup['signal'] is None and cleanup['observed'] is True
    assert cleanup['graceful'] is True and cleanup['error'] is None
    assert proof['cleanup'] == cleanup
    assert Path(proof['source']).resolve() == package / '__init__.py'
    assert proof['authoritySha256'] == sha(package / 'configuration_authority.py')
    assert Path(readiness['source']).resolve() == package
    assert Path(readiness['nodeModules']).resolve() == node_modules
    assert readiness['node'] and readiness['playwright'] and readiness['validationHtml']
    identity = readiness['packageIdentity']
    assert proof['packageIdentity'] == identity, 'Serving fixture and readiness must identify the same package'
    assert identity['installedPackageVerified'] is True
    assert identity['expectedPackageRoot'] == identity['packageRoot'] == str(package)
    assert identity['server'] == str(package / 'server.py') and identity['staticRoot'] == str(package / 'static')
    assert identity['staticSha256'] == manifest(package / 'static')
    return {'directory': str(directory), 'checks': len(names), 'checkNames': names, 'cleanup': cleanup,
            'packageRoot': str(package), 'rendererNodeModules': str(node_modules)}


def verify_questions(folder, log, package):
    origins = [json.loads(line) for line in log.splitlines() if line.startswith('{')]
    assert any(row.get('installedPackageVerified') is True and
               Path(row['packageRoot']).resolve() == package for row in origins)
    assert 'Durable questions browser passed:' in log
    cleanup = json.loads((folder / 'fixture-exit.json').read_text())
    assert cleanup['code'] == 0 and cleanup['signal'] is None
    review = json.loads((folder / 'question-review-continuity.json').read_text())
    assert review['failure'] is None and review['errors'] == [] and review['droppedErrors'] == 0
    assert review['dom']['errors'] == []
    draft = review['draftNavigation']
    assert draft['heldDrafts'] == 1 and draft['sendRequests'] == 0 and draft['expired'] is False
    assert draft['error'] is None and draft['routeErrors'] == []
    assert draft['result']['accepted'] is False and draft['result']['code'] == 'canvas_view_dirty'
    labels = {row['label'] for row in review['snapshots']}
    assert {'draft-navigation-held', 'draft-navigation-after-enter', 'draft-navigation-refused',
            'before-dirty-clear', 'after-dirty-clear', 'before-answer', 'after-answer-click',
            'after-continuity-assertions'} <= labels
    return {'cleanup': cleanup, 'draftNavigation': draft, 'snapshotLabels': sorted(labels)}


def main():
    from browser_check_runner import run_scenarios
    from cold_restore_checks import git, manifest, sha, verify_wheel
    from frontend_build_artifact import create, source_identity, verify
    from web_test_harness import stage_web_test_harness
    assert sys.version_info[:2] == (3, 13) and not sys.flags.optimize
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    # run_scenarios merges os.environ with each phase's fixed env. Filter the
    # parent first so credentials, injection flags and user configuration cannot
    # leak through that merge. Phase PYTHONPATH is explicit, never inherited.
    retained = qualification_environment(os.environ)
    os.environ.clear()
    os.environ.update(retained)
    OUTPUT.mkdir(parents=True, exist_ok=False)  # No replay or overwrite of earlier evidence.
    work = Path(tempfile.mkdtemp(prefix='permissions-history-', dir=os.environ['RUNNER_TEMP'])).resolve()
    assert not work.is_relative_to(ROOT)
    started = time.monotonic()
    report = {'passed': False, 'complete': False, 'normalBudgetSeconds': 300, 'maximumSeconds': 600,
              'phases': {}, 'limits': ['Synthetic runtime only; no real-provider acceptance or full campaign.']}
    save(OUTPUT / 'report.json', report)
    def expired(*_):
        raise TimeoutError('Permissions history exceeded its fixed 600 second ceiling')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(600)
    phase = None
    try:
        revision = git('rev-parse', 'HEAD', root=ROOT)
        assert revision == os.environ['GITHUB_SHA']
        assert not git('status', '--porcelain', '--untracked-files=no', root=ROOT)
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        loop, = [p for p in tomllib.loads((ROOT / 'uv.lock').read_text())['package'] if p['name'] == 'amplifier-module-loop-live']
        assert git('rev-parse', 'HEAD', root=ROOT / '.ci/loop-live') == loop['source']['git'].rsplit('#', 1)[1]
        files = [p for p in git('ls-files', '-z', root=ROOT).split('\0') if p]
        assert all((ROOT / p).is_file() and not (ROOT / p).is_symlink() for p in files)
        source = {'root': str(ROOT), 'revision': revision, 'tree': git('rev-parse', 'HEAD^{tree}', root=ROOT),
                  'version': project['project']['version'], 'identity': source_identity(ROOT),
                  'files': {p: sha(ROOT / p) for p in files}, 'loopSource': loop['source'],
                  'runId': os.environ['GITHUB_RUN_ID'], 'runAttempt': os.environ['GITHUB_RUN_ATTEMPT']}
        save(OUTPUT / 'source.json', source)
        selected = {'python': python_selection(ROOT), 'node': node_selection(ROOT)}
        save(OUTPUT / 'selection.json', selected)
        host, installed = work / 'host/bin/python', work / 'installed/bin/python'
        harness = work / 'harness'
        package = work / 'installed/lib/python3.13/site-packages/amplifier_web'
        wheel = OUTPUT / 'wheel' / ('amplifier_unified-' + source['version'] + '-py3-none-any.whl')
        common = {'UV_PROJECT_ENVIRONMENT': str(work / 'host'), 'UV_NO_BUILD_PACKAGE': 'amplifier-core',
                  'UV_CACHE_DIR': str(work / 'uv-cache'), 'GIT_CONFIG_NOSYSTEM': '1',
                  'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
                  'NPM_CONFIG_USERCONFIG': str(work / 'npm-user.conf'),
                  'NPM_CONFIG_GLOBALCONFIG': str(work / 'npm-global.conf'),
                  'NPM_CONFIG_CACHE': str(work / 'npm-cache'), 'XDG_CONFIG_HOME': str(work / 'config'),
                  'XDG_CACHE_HOME': str(work / 'cache'),
                  'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'url.' + (ROOT / '.ci/loop-live').as_uri() + '.insteadOf',
                  'GIT_CONFIG_VALUE_0': 'https://github.com/microsoft/amplifier-module-loop-live',
                  'HOME': str(work / 'home'), 'TMPDIR': str(work / 'tmp'),
                  'PLAYWRIGHT_BROWSERS_PATH': str(work / 'browsers'), 'PYTHONPATH': '', 'NODE_PATH': '',
                  'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        for folder in ('home', 'tmp'):
            (work / folder).mkdir()
        source_env = {**common, 'PYTHONPATH': str(ROOT), 'AMPLIFIER_TEST_ROOT': str(ROOT),
                      'AMPLIFIER_TEST_PYTHON': str(host), 'UNIFIED_RUNTIME_PYTHON': str(host),
                      'AMPLIFIER_TEST_PACKAGE_ROOT': str(ROOT / 'amplifier_web'),
                      'AMPLIFIER_SHELL_NODE_MODULES': str(ROOT / '.build/web-client/node_modules'),
                      'AMPLIFIER_TEST_OUTPUT_DIR': str(OUTPUT / 'source-approval-evidence')}
        installed_env = {**common, 'AMPLIFIER_TEST_ROOT': str(harness), 'AMPLIFIER_TEST_PYTHON': str(installed),
                         'AMPLIFIER_TEST_PACKAGE_ROOT': str(package), 'AMPLIFIER_TEST_INSTALLED_STATIC': '1',
                         'AMPLIFIER_SHELL_NODE_MODULES': str(harness / '.build/web-client/node_modules'),
                         'AMPLIFIER_TEST_OUTPUT_DIR': str(OUTPUT / 'installed-approval-evidence'),
                         'AMPLIFIER_QUESTIONS_FIXTURE': str(harness / 'tests/fixtures/questions_ui_server.py'),
                         'AMPLIFIER_QUESTIONS_OUTPUT': str(OUTPUT / 'questions-evidence')}
        plans = [
            ('locked-host', 150, ['uv', 'sync', '--project', str(ROOT), '--locked', '--group', 'dev', '--group', 'artifacts', '--no-install-project', '--python', sys.executable], common, ROOT),
            ('locked-freeze', 15, ['uv', '--quiet', 'pip', 'freeze', '--python', str(host)], common, ROOT),
            ('host-dependencies', 15, ['uv', 'pip', 'check', '--python', str(host)], common, ROOT),
            ('npm-ci', 90, ['npm', 'ci', '--prefix', 'tests/web', '--no-audit', '--no-fund'], common, ROOT),
            ('client-npm-ci', 90, ['npm', 'ci', '--prefix', '.build/web-client', '--no-audit', '--no-fund'], common, ROOT),
            ('chromium', 120, ['node', 'tests/web/node_modules/playwright/cli.js', 'install', '--with-deps', 'chromium'], common, ROOT),
            ('python', 90, [str(host), '-I', str(Path(__file__).resolve()), '--source-pytest'], source_env, ROOT),
            ('node', 30, ['node', '--test', '--test-reporter=tap', *['../../tests/web/fixture-lifecycle.test.mjs' if p == 'tests/fixture-lifecycle.test.mjs' else p for p in NODE_TESTS]], common, ROOT),
            ('build', 90, [sys.executable, 'scripts/web_client_build.py', 'build'], common, ROOT),
            ('source-approval', 120, ['node', 'tests/web/approval-lifecycle-browser.mjs'], source_env, ROOT),
            ('wheel', 30, ['uv', 'build', '--wheel', '--no-build-isolation', '--python', str(host), '--out-dir', str(wheel.parent), str(ROOT)], common, ROOT),
            ('fresh-runtime', 15, ['uv', 'venv', '--python', sys.executable, str(work / 'installed')], common, ROOT),
            ('install-dependencies', 90, ['uv', 'pip', 'install', '--python', str(installed), '--no-deps', '-r', str(OUTPUT / 'locked-requirements.txt')], common, ROOT),
            ('install-wheel', 20, ['uv', 'pip', 'install', '--python', str(installed), '--no-deps', str(wheel)], common, ROOT),
            ('installed-dependencies', 15, ['uv', 'pip', 'check', '--python', str(installed)], common, ROOT),
            ('installed-freeze', 15, ['uv', '--quiet', 'pip', 'freeze', '--python', str(installed)], common, ROOT),
            ('installed-imports', 20, [str(installed), '-I', str(Path(__file__).resolve()), '--probe-installed'], common, harness),
            ('installed-approval', 120, ['node', 'tests/web/approval-lifecycle-browser.mjs'], installed_env, harness),
            ('questions', 120, ['node', 'tests/web/questions-browser.mjs'], installed_env, harness),
        ]
        save(OUTPUT / 'plan.json', [{'id': n, 'timeoutSeconds': cap, 'command': cmd, 'env': env,
                                   'cwd': str(root / '.build/web-client' if n == 'node' else root)} for n, cap, cmd, env, root in plans])
        report['phases'] = {name: {'status': 'not_run'} for name, *_ in plans}
        save(OUTPUT / 'report.json', report)
        harness.mkdir(parents=True)
        for folder in ('source-approval-evidence', 'installed-approval-evidence', 'questions-evidence'):
            (OUTPUT / folder).mkdir()
        for phase, cap, command, env, root in plans:
            remaining = 600 - (time.monotonic() - started)
            assert remaining > 1, 'Overall qualification budget exhausted'
            report['phases'][phase] = {'status': 'running'}
            save(OUTPUT / 'report.json', report)
            plan = {'schemaVersion': 1, 'scenarios': [{'id': phase, 'command': command, 'env': env,
                'cwd': '.build/web-client' if phase == 'node' else '.'}]}
            code = run_scenarios(root, plan, OUTPUT / phase, budget=min(cap, remaining), case_timeout=cap,
                                 provenance={'commit': revision, 'tree': source['tree']})
            result = json.loads((OUTPUT / phase / 'results.json').read_text())
            row, = result['scenarios']
            report['phases'][phase] = row
            save(OUTPUT / 'report.json', report)
            assert row['id'] == phase and row['command'] == command and row['env'] == env
            assert code == 0 and result['complete'] and result['passed'] and row['status'] == 'passed', phase
            assert row['exitCode'] == 0 and row['reason'] is None and row['cleanup'] == {'remaining': [], 'terminated': 0}, phase
            log = (OUTPUT / phase / (phase + '.log')).read_text()
            if phase == 'locked-freeze':
                assert log.strip() and not any(line.startswith(('-e ', 'amplifier-unified')) for line in log.splitlines())
                (OUTPUT / 'locked-requirements.txt').write_text(log)
            elif phase == 'python':
                cases = ET.parse(OUTPUT / 'python.xml').getroot().findall('.//testcase')
                nodes = json.loads((OUTPUT / 'python-collection.json').read_text())
                assert len(cases) == len(nodes) == sum(selected['python'].values())
                assert all(not any(c.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for c in cases)
                assert Counter(c.attrib['name'] for c in cases) == Counter(n.rsplit('::', 1)[1] for n in nodes)
                report['pythonCases'] = len(cases)
            elif phase == 'node':
                (OUTPUT / 'node.tap').write_text(log)
                save(OUTPUT / 'node-results.json', verify_tap(log, selected['node']))
            elif phase == 'build':
                build = create(ROOT, OUTPUT / 'built-frontend', os.environ)
                assert build['source'] == source['identity']
                build_info = json.loads((ROOT / 'amplifier_web/static/build.json').read_text())
                assert build_info['version'] == source['version']
                static = {'amplifier_web/static/' + p: row['sha256'] for p, row in build['assets'].items()}
                candidate = {**source, 'packages': project['tool']['hatch']['build']['targets']['wheel']['packages'],
                             'wheel': str(wheel), 'static': static,
                             'files': {**{p: v for p, v in source['files'].items() if not p.startswith('amplifier_web/static/')}, **static}}
                save(OUTPUT / 'candidate.json', candidate)
                report['build'] = build_info
            elif phase in ('source-approval', 'installed-approval'):
                report[phase] = verify_approval(Path(env['AMPLIFIER_TEST_OUTPUT_DIR']),
                    Path(env['AMPLIFIER_TEST_PACKAGE_ROOT']), Path(env['AMPLIFIER_SHELL_NODE_MODULES']))
            elif phase == 'wheel':
                assert list(wheel.parent.glob('*.whl')) == [wheel], 'Exactly one newly built wheel'
                save(OUTPUT / 'wheel.json', verify_wheel(wheel, candidate))
            elif phase == 'installed-freeze':
                lines = log.splitlines()
                app, = [line for line in lines if line.startswith('amplifier-unified')]
                assert sorted(line for line in lines if line != app) == sorted((OUTPUT / 'locked-requirements.txt').read_text().splitlines())
                (OUTPUT / 'installed-requirements.txt').write_text(log)
            elif phase == 'installed-imports':
                origins = json.loads(log)
                assert Path(origins['modules']['amplifier_web']) == package / '__init__.py'
                assert all(Path(origins['modules'][p]).parent == package.parent / p for p in candidate['packages'])
                dist, = [d for d in origins['distributions'] if d['name'] == 'amplifier-unified']
                assert dist['version'] == source['version'] and dist['direct']['url'] == wheel.as_uri()
                assert not dist['direct'].get('dir_info', {}).get('editable')
                save(OUTPUT / 'installed.json', origins)
                save(OUTPUT / 'installed-wheel.json', verify_wheel(wheel, candidate, package.parent))
                stage_web_test_harness(ROOT, harness, copy_dependencies=True)
                copied = manifest(harness)
                assert all(sha(ROOT / p) == value for p, value in copied.items())
                assert not any((harness / p).exists() for p in (*candidate['packages'], '.build/web-client/src'))
                save(OUTPUT / 'harness.json', {'path': str(harness), 'nodeModules': 'real copy', 'files': copied})
            elif phase == 'questions':
                report['questions'] = verify_questions(OUTPUT / 'questions-evidence', log, package)
        assert verify(ROOT, OUTPUT / 'built-frontend')['source'] == source['identity']
        assert manifest(ROOT / 'amplifier_web/static') == {p.removeprefix('amplifier_web/static/'): v for p, v in candidate['static'].items()}
        assert all(sha(ROOT / p) == value for p, value in candidate['files'].items())
        assert manifest(harness) == copied, 'Installed checks must preserve the copied harness'
        assert all(sha(ROOT / p) == value for p, value in copied.items()), 'Source harness/dependencies changed'
        save(OUTPUT / 'installed-wheel-final.json', verify_wheel(wheel, candidate, package.parent))
        report.update(passed=True, complete=True)
    except BaseException as exc:
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        if phase in report['phases']:
            report['phases'][phase].update(status='failed', verificationFailure=str(exc))
        raise
    finally:
        signal.alarm(0)
        report['durationSeconds'] = round(time.monotonic() - started, 3)
        report['workDirectory'] = str(work)
        save(OUTPUT / 'report.json', report)  # Disposable CI owns retirement; retain every failed/unrun phase.


if __name__ == '__main__':
    if sys.argv[1:] == ['--source-pytest']:
        source_pytest()
    elif sys.argv[1:] == ['--probe-installed']:
        installed_probe()
    else:
        assert not sys.argv[1:], 'No arbitrary selectors, commands or budget overrides'
        main()
