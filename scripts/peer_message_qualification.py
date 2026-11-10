"""Fixed peer-message source, negative-control and installed-wheel qualification."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile

from browser_check_runner import run_scenarios
from web_test_harness import stage_web_test_harness

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/peer-messages'
CHECKS = ('python', 'node', 'build', 'sourceBrowser', 'whitespaceNegative', 'wheel', 'installedBrowser')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    paths = list(root.rglob('*'))
    assert not any(path.is_symlink() for path in paths)
    return {str(path.relative_to(root)): digest(path) for path in paths if path.is_file()}


def run(name, argv, *, cwd=ROOT, env=None, timeout=180, expected=0):
    log = OUTPUT / (name + '.log')
    with log.open('w') as stream:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f'Stopping exact owned qualification process group {process.pid}', flush=True)
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            raise
    assert code == expected, f'{name}: exit {code}, expected {expected}; see {log.name}'
    return log.read_text()


def initialize():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    git = lambda *args: subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()
    assert not git('status', '--porcelain', '--untracked-files=no')
    source = {'revision': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}'),
              'files': {name: digest(ROOT / name) for name in git('ls-files').splitlines()},
              'staticBefore': inventory(ROOT / 'amplifier_web/static'),
              'buildSystem': tomllib.loads((ROOT / 'pyproject.toml').read_text())['build-system']}
    assert source['staticBefore'], 'Source static inventory is empty'
    (OUTPUT / 'source.json').write_text(json.dumps(source, indent=2))
    (OUTPUT / 'qualification.json').write_text(json.dumps({
        'passed': False, 'checks': {name: {'status': 'not_run'} for name in CHECKS}}, indent=2))


def browser(root, name, python, *, installed=False):
    evidence = OUTPUT / name
    row = {'id': 'peer-preview', 'command': ['node', 'tests/web/peer-preview-browser.mjs'],
           'env': {'AMPLIFIER_TEST_PYTHON': str(python), 'AMPLIFIER_PROOF_DIR': str(evidence),
                   'AMPLIFIER_SHELL_NODE_MODULES': str(ROOT / '.build/web-client/node_modules'),
                   'AMPLIFIER_TEST_INSTALLED_STATIC': '1' if installed else '0'}}
    plan = {'schemaVersion': 1, 'scenarios': [row]}
    (OUTPUT / (name + '-plan.json')).write_text(json.dumps(plan, indent=2))
    code = run_scenarios(root, plan, OUTPUT / (name + '-runner'), budget=180, case_timeout=150)
    report = json.loads((OUTPUT / (name + '-runner/results.json')).read_text())
    item, = report['scenarios']
    assert code == 0 and report['passed'] and report['complete']
    assert item['status'] == 'passed' and item['exitCode'] == 0 and item['reason'] is None
    assert item['cleanup'] == {'remaining': [], 'terminated': 0}
    proof, = json.loads((evidence / 'peer-browser-result.json').read_text())
    assert all(proof[key] is True for key in ('semanticMarkdown', 'exactCopy', 'noReload', 'noRuntime'))
    assert proof['draftAttachmentsRetained'] == 1
    assert proof['draftTextAfterReturn'] is True
    assert proof['refreshEarlierOrder'] is True
    assert proof['lateDirtyPreservation'] == {
        'canvasRefused': True, 'rendererMountAndLeaseRetained': True,
        'settingsCancelled': True, 'settingsEditorRetained': True,
        'canonicalSourceRetained': True, 'cleanOpenAccepted': True}
    assert proof['narrow'] == [320, 390]
    assert proof['mode'] == ('installed-static' if installed else 'built-static')
    return proof


def main():
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'Run only in the owned fixed hosted job'
    if sys.argv[1:] == ['--initialize']:
        initialize()
        return
    assert not sys.argv[1:], 'No arbitrary commands or selections'
    source = json.loads((OUTPUT / 'source.json').read_text())
    result = json.loads((OUTPUT / 'qualification.json').read_text())
    result['revision'] = source['revision']

    def unchanged():
        assert all(digest(ROOT / name) == value for name, value in source['files'].items()
                   if not name.startswith('amplifier_web/static/'))

    def check(name, operation):
        result['checks'][name] = {'status': 'running'}
        try:
            result['checks'][name] = {'status': 'passed', 'evidence': operation()}
        except Exception as exc:
            result['checks'][name] = {'status': 'failed', 'error': str(exc)}
            raise

    def python_checks():
        run('python', [sys.executable, '-m', 'pytest', '-q', '-ra', '--tb=short',
            'tests/test_peer_preview.py', 'tests/test_peer_attribution.py',
            'tests/test_peer_qualification.py', 'tests/test_browser_check_runner.py',
            'tests/test_release_qualification.py::test_app004_opt_in_is_fixed_manual_only_and_keeps_default_browser_budget',
            f'--junitxml={OUTPUT / "python.xml"}'])
        cases = ET.parse(OUTPUT / 'python.xml').getroot().findall('.//testcase')
        assert cases and all(not any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for case in cases)
        return {'cases': len(cases), 'failed': 0, 'skipped': 0}

    def node_checks():
        run('browser-syntax', ['node', '--check', 'tests/web/peer-preview-browser.mjs'])
        text = run('node', ['node', '--test', '--test-reporter=tap',
            'tests/peer-groups.test.mjs', 'tests/artifact-placement.test.mjs',
            'tests/peer-preview-pages.test.mjs'], cwd=ROOT / '.build/web-client')
        counts = dict(re.findall(r'^# (tests|pass|fail|cancelled|skipped|todo) (\d+)$', text, re.M))
        assert counts == {'tests': '10', 'pass': '10', 'fail': '0', 'cancelled': '0', 'skipped': '0', 'todo': '0'}
        return counts

    def build():
        run('build', [sys.executable, 'scripts/web_client_build.py', 'build'])
        files = inventory(ROOT / 'amplifier_web/static')
        assert set(files) == set(source['staticBefore']), 'Built static members differ from the selected source'
        (OUTPUT / 'static-after.json').write_text(json.dumps(files, indent=2))
        with tarfile.open(OUTPUT / 'built-static.tar.gz', 'w:gz') as archive:
            archive.add(ROOT / 'amplifier_web/static', arcname='static')
        return {'files': len(files), 'archiveSha256': digest(OUTPUT / 'built-static.tar.gz'),
                'build': json.loads((ROOT / 'amplifier_web/static/build.json').read_text())}

    def negative():
        env = {**os.environ, 'AMPLIFIER_TEST_PYTHON': sys.executable,
               'AMPLIFIER_PROOF_DIR': str(OUTPUT / 'negative'), 'PEER_NEGATIVE_FLATTEN': '1'}
        text = run('negative', ['node', 'tests/web/peer-preview-browser.mjs'], env=env, expected=1)
        assert 'PEER_NEGATIVE_CONTROL_CONFIRMED' in text and 'def sample():' in text
        unchanged()
        return {'expectedExit': 1, 'mutation': 'Whitespace-only text-node flattening; original element structure retained',
                'failure': 'Exact fenced-code line breaks and indentation', 'sourceUnchanged': True}

    try:
        unchanged()
        check('python', python_checks)
        check('node', node_checks)
        check('build', build)
        check('sourceBrowser', lambda: browser(ROOT, 'source-browser', sys.executable))
        check('whitespaceNegative', negative)
        run('locked-dependencies-before', ['uv', 'pip', 'freeze', '--python', sys.executable])

        def wheel_build():
            run('wheel-build', ['uv', 'build', '--wheel', '-v', '--out-dir', str(OUTPUT / 'wheel')])
            wheel, = (OUTPUT / 'wheel').glob('*.whl')
            with zipfile.ZipFile(wheel) as archive:
                files = {name.removeprefix('amplifier_web/static/'): hashlib.sha256(archive.read(name)).hexdigest()
                         for name in archive.namelist() if name.startswith('amplifier_web/static/') and not name.endswith('/')}
            assert files == inventory(ROOT / 'amplifier_web/static') and set(files) == set(source['staticBefore'])
            return {'sha256': digest(wheel), 'staticFiles': len(files), 'declaredBuildSystem': source['buildSystem']}

        check('wheel', wheel_build)
        wheel, = (OUTPUT / 'wheel').glob('*.whl')
        run('wheel-install', ['uv', 'pip', 'install', '--python', sys.executable, '--no-deps', '--reinstall', str(wheel)])
        run('locked-dependencies-after', ['uv', 'pip', 'freeze', '--python', sys.executable])
        with tempfile.TemporaryDirectory(prefix='peer-installed-') as temporary:
            harness = Path(temporary)
            support = stage_web_test_harness(ROOT, harness, python_tests=True,
                                             source_helpers=('settings-navigation.js',))
            (OUTPUT / 'installed-test-support.json').write_text(json.dumps(support, indent=2))
            env = dict(os.environ)
            env.pop('PYTHONPATH', None)
            origins = json.loads(run('wheel-origins', [sys.executable, '-c',
                'import amplifier_web,amplifier_operations,json; print(json.dumps({"amplifier_web":amplifier_web.__file__,"amplifier_operations":amplifier_operations.__file__}))'], cwd=harness, env=env))
            package = Path(origins['amplifier_web']).resolve().parent.parent
            assert all('site-packages' in Path(value).parts
                       and not Path(value).resolve().is_relative_to((ROOT / name).resolve())
                       and not Path(value).resolve().is_relative_to(harness)
                       and Path(value).resolve().parent.parent == package
                       for name, value in origins.items())
            with zipfile.ZipFile(wheel) as archive:
                expected = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                            if name.startswith(('amplifier_web/', 'amplifier_operations/')) and not name.endswith('/')}
            def verify():
                assert all(digest(package / name) == value for name, value in expected.items())
            verify()
            prior = os.environ.pop('PYTHONPATH', None)
            try:
                check('installedBrowser', lambda: browser(harness, 'installed-browser', sys.executable, installed=True))
            finally:
                if prior is not None:
                    os.environ['PYTHONPATH'] = prior
            verify()
            result['installedIdentity'] = {'origins': origins, 'payloadsVerifiedBeforeAfter': len(expected),
                                           'staticFiles': 311, 'applicationOutsideHarness': True}
        unchanged()
        result['passed'] = all(row['status'] == 'passed' for row in result['checks'].values())
    except Exception as exc:
        result.update(passed=False, error=str(exc))
        raise
    finally:
        (OUTPUT / 'qualification.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
