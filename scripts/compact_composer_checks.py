"""Fixed compact-composer qualification using the existing serial browser owner.

Run only in the admitted GitHub-hosted check. No model calls, arbitrary commands,
source fallback for installed checks, retries or hidden success-on-error.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from browser_check_runner import run_scenarios, initial_report, save
from release_qualification import core_wheel
from web_test_harness import stage_web_test_harness

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/compact-composer'
BROWSERS = ('compact-composer', 'composer-options', 'composer-primary-action',
            'composer-readiness', 'message-delivery', 'draft-switch',
            'destination-history', 'message-interactions', 'mobile-navigation', 'shell-components')
# Destination history imports a source-side oracle; shell-components builds an
# example package. Keep those source-only rather than copying app sources into
# the installed harness. Mobile/reaction journeys have test-only dependencies.
INSTALLED = ('compact-composer', 'composer-options', 'composer-primary-action',
             'message-delivery', 'message-interactions', 'mobile-navigation')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def plan(output, installed=False):
    names = INSTALLED if installed else BROWSERS
    rows = [] if installed else [{'id': 'composer-units', 'command': ['node', '--test',
        'tests/composer.test.mjs', 'tests/composer-ownership.test.mjs',
        'tests/markdown-composer.test.mjs', 'tests/message-delivery.test.mjs',
        'tests/chat-controls.test.mjs', 'tests/bundle-controls.test.mjs'], 'cwd': '.build/web-client'},
        {'id': 'navigation-reaction-units', 'command': ['node', '--test',
         'tests/destination-history.test.mjs', 'tests/destination-history-ui.test.mjs',
         'tests/conversation-navigation.test.mjs', 'tests/message-reactions.test.mjs',
         'tests/message-reaction-feedback.test.mjs'], 'cwd': '.build/web-client'}]
    rows += [{'id': name, 'command': ['node', f'tests/web/{name}-browser.mjs']} for name in names]
    for row in rows:
        folder = str(output / row['id'])
        row['env'] = {'AMPLIFIER_TEST_ARTIFACTS': folder,
                      'AMPLIFIER_TEST_OUTPUT': folder, 'AMPLIFIER_PROOF_DIR': folder,
                      'AMPLIFIER_SCREENSHOT_DIR': folder}
    return {'schemaVersion': 1, 'scenarios': rows}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def command(args, log, cwd=ROOT, env=None):
    with log.open('w') as stream:
        result = subprocess.run(args, cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise RuntimeError(f'{log.name} failed with exit {result.returncode}')


def provenance():
    git = lambda *args: subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    tracked = git('ls-files', '-z').split('\0')
    return {'revision': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}'),
            'runId': os.environ.get('GITHUB_RUN_ID'), 'runAttempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
            'inputs': {p: sha(ROOT / p) for p in tracked if p and not p.startswith('amplifier_web/static/') and (ROOT / p).is_file()},
            'limits': ['Synthetic runtime; no real provider, physical audio or mobile keyboard',
                       'Immutable multi-quote interface has separate ownership; Markdown shape is not provenance proof']}


def readiness():
    python = ROOT / '.venv/bin/python'
    command([str(python), '-c', 'import sys,amplifier_core._engine,amplifier_foundation,aiohttp,amplifier_web.server;assert sys.version_info>=(3,13);print(sys.version)'], OUTPUT / 'python-readiness.log')
    write(OUTPUT / 'native-wheel.json', core_wheel(python))
    probe = """import {createServer} from 'node:http';import {chromium} from '@playwright/test';
const server=createServer((q,r)=>r.end('<button onclick=\"this.textContent=123\">check</button>'));
await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
try{browser=await chromium.launch({headless:true});const page=await browser.newPage();await page.goto('http://127.0.0.1:'+server.address().port);await page.getByRole('button').click();if(await page.getByRole('button').textContent()!=='123')throw Error('click failed');console.log(JSON.stringify({node:process.version,chromium:browser.version(),loopbackClick:true}));}finally{await browser?.close();await new Promise(r=>server.close(r));}"""
    command(['node', '--input-type=module', '-e', probe], OUTPUT / 'browser-readiness.log', ROOT / 'tests/web')
    write(OUTPUT / 'readiness.json', {'ready': True, 'nativeWheel': sha(OUTPUT / 'native-wheel.json'),
                                    'python': sha(OUTPUT / 'python-readiness.log'), 'browser': sha(OUTPUT / 'browser-readiness.log')})


def installed(source):
    # A detached harness contains only tests; fixture sys.path insertions cannot find app source.
    work = Path(os.environ['RUNNER_TEMP']) / f'compact-composer-{os.environ["GITHUB_RUN_ID"]}-{os.environ["GITHUB_RUN_ATTEMPT"]}'
    work.mkdir(exist_ok=False)
    venv, harness, wheels = work / 'venv', work / 'harness', work / 'wheels'
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    command(['uv', 'build', '--wheel', '--out-dir', str(wheels)], OUTPUT / 'wheel-build.log', env=env)
    wheel, = wheels.glob('*.whl')
    shutil.copy2(wheel, OUTPUT / wheel.name)
    requirements = work / 'requirements.txt'
    command(['uv', 'export', '--locked', '--no-emit-project', '--no-dev', '--format', 'requirements-txt', '--output-file', str(requirements)], OUTPUT / 'wheel-export.log', env=env)
    command(['uv', 'venv', '--python', sys.executable, str(venv)], OUTPUT / 'wheel-venv.log', env=env)
    python = venv / 'bin/python'
    # Export is the complete resolved lock. Re-resolving a dependency's original
    # Git @main declaration conflicts with its exact, already locked source.
    command(['uv', 'pip', 'install', '--python', str(python), '--no-deps',
             '--only-binary', 'amplifier-core', '-r', str(requirements)],
            OUTPUT / 'wheel-dependencies.log', env=env)
    command(['uv', 'pip', 'install', '--python', str(python), '--no-deps', str(wheel)], OUTPUT / 'wheel-install.log', env=env)
    command(['uv', 'pip', 'check', '--python', str(python)], OUTPUT / 'wheel-dependency-check.log', env=env)
    actual = Path(subprocess.check_output([str(python), '-I', '-c', 'import amplifier_web;from pathlib import Path;print(Path(amplifier_web.__file__).resolve().parent)'], cwd=work, env=env, text=True).strip())
    assert actual.is_relative_to(venv) and not actual.is_relative_to(ROOT)
    with zipfile.ZipFile(wheel) as archive:
        expected = {p.removeprefix('amplifier_web/static/'): hashlib.sha256(archive.read(p)).hexdigest()
                    for p in archive.namelist() if p.startswith('amplifier_web/static/') and not p.endswith('/')}
    assert expected == manifest(actual / 'static') == source['static'], 'Built/wheel/installed static bytes differ'
    write(OUTPUT / 'installed-identity.json', {'wheel': wheel.name, 'sha256': sha(wheel),
          'static': expected, 'source': source['revision'], 'outsideCheckout': True,
          'requirementsSha256': sha(requirements), 'nativeWheel': core_wheel(python)})
    stage_web_test_harness(ROOT, harness)
    (harness / '.venv').symlink_to(venv, target_is_directory=True)
    selected = plan(OUTPUT / 'installed', installed=True)
    for row in selected['scenarios']:
        row['env'].update(AMPLIFIER_TEST_PACKAGE_ROOT=str(actual), AMPLIFIER_TEST_PYTHON=str(python), PYTHONPATH='')
    result = run_scenarios(harness, selected, OUTPUT / 'installed', budget=600, provenance=source)
    if result == 0:
        for name in ('message-interactions', 'mobile-navigation'):
            lines = (OUTPUT / 'installed' / f'{name}.log').read_text().splitlines()
            identities = []
            for line in lines:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get('installedPackageVerified'):
                    identities.append(value)
            assert any(row.get('packageRoot') == str(actual) for row in identities), f'{name}: installed fixture origin proof missing'
        proof = json.loads((OUTPUT / 'installed/compact-composer/compact-composer.json').read_text())
        served = proof.get('installedServedAssets')
        assert isinstance(served, dict) and served, 'Installed served-asset proof did not execute'
        assert all(expected.get(name) == value for name, value in served.items()), 'Served assets are not the installed bytes'
        write(OUTPUT / 'installed-served-proof.json', {'verified': True, 'assets': served})
    # Hosted job retires its workspace; retain explicit harness/process state, not an invented destroy receipt.
    write(OUTPUT / 'installed-layout.json', {'testOnlyHarness': True, 'sourceDirectoriesCopied': False,
          'harnessTests': manifest(harness / 'tests'), 'browserTests': manifest(harness / 'tests/web'),
          'clientTestSupport': manifest(harness / '.build/web-client/tests'),
          'processCleanup': json.loads((OUTPUT / 'installed/results.json').read_text())['scenarios']})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true')
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('This fixed qualification requires its admitted GitHub-hosted job')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = provenance()
    write(OUTPUT / 'source.json', source)
    if args.initialize:
        (OUTPUT / 'source').mkdir(exist_ok=True)
        (OUTPUT / 'installed').mkdir(exist_ok=True)
        save(OUTPUT / 'source', initial_report(plan(OUTPUT / 'source'), source))
        save(OUTPUT / 'installed', initial_report(plan(OUTPUT / 'installed', installed=True), source))
        return 0
    readiness()
    source['static'] = manifest(ROOT / 'amplifier_web/static')
    source['build'] = json.loads((ROOT / 'amplifier_web/static/build.json').read_text())
    write(OUTPUT / 'source.json', source)
    source_code = run_scenarios(ROOT, plan(OUTPUT / 'source'), OUTPUT / 'source', budget=600, provenance=source)
    # Installed execution is meaningful only after the same source journeys pass.
    try:
        installed_code = installed(source) if source_code == 0 else None
    except Exception as error:
        write(OUTPUT / 'result.json', {'sourceExit': source_code, 'installedExit': 1,
              'installedNotStartedReason': None, 'installedError': str(error),
              'passed': False})
        raise
    write(OUTPUT / 'result.json', {'sourceExit': source_code, 'installedExit': installed_code,
          'installedNotStartedReason': 'source_failed' if installed_code is None else None,
          'passed': source_code == 0 and installed_code == 0})
    return 0 if source_code == 0 and installed_code == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
