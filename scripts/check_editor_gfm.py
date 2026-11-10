"""Finite editor qualification inside a prepared, root-bound managed browser guest.

No Git metadata, credentials or live provider needed. Each run retains its primary
failure. The baseline is a tracked fixture, not an omitted task-storage transfer.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from browser_check_runner import run_scenarios

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('/artifacts/editor-gfm')
BASE_HASH = 'f37ff900fb723161684799a47442022efdd955457f29972b0c2fe4c31c990341'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    contract = json.loads(Path(os.environ['AMPLIFIER_TEST_CONTRACT']).read_text())
    assert contract['profileId'] == 'browser-test'
    report = {'status': 'running', 'checks': [], 'limits': ['Synthetic runtime; no real provider', 'Linux Chromium, not physical IME/mobile/native-platform qualification']}
    def save():
        (OUT / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    def run(name, command, timeout=180, env=None, expected_failure=None, cwd=ROOT):
        start = time.monotonic()
        with (OUT / (name + '.log')).open('w') as stream:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        log = (OUT / (name + '.log')).read_text()
        passed = result.returncode == 0 if expected_failure is None else result.returncode != 0 and expected_failure in log
        report['checks'].append({'id': name, 'command': command, 'returncode': result.returncode, 'passed': passed, 'seconds': round(time.monotonic()-start, 2), 'expectedFailure': expected_failure})
        save()
        print(name, 'passed' if passed else 'FAILED', 'exit', result.returncode, flush=True)
        if not passed:
            print(log[-18000:], flush=True)
        return passed
    save()
    try:
        inputs = {}
        for name in ('.build/web-client/src', '.build/web-client/tests', 'tests/web', 'tests/fixtures', 'amplifier_web'):
            for path in sorted((ROOT / name).rglob('*')):
                if path.is_file() and not {'static', '__pycache__', 'node_modules'}.intersection(path.parts):
                    inputs[str(path.relative_to(ROOT))] = digest(path)
        for name in ('pyproject.toml', 'uv.lock', '.build/web-client/package.json', '.build/web-client/package-lock.json', '.build/web-client/vite.config.js', 'tests/web/package-lock.json', 'scripts/check_editor_gfm.py', 'scripts/browser_check_runner.py', 'tests/test_message_delivery.py', 'tests/test_multi_quote.py'):
            inputs[name] = digest(ROOT / name)
        (OUT / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
        (OUT / 'execution-contract.json').write_text(json.dumps(contract, indent=2) + '\n')
        ready = run('python-readiness', [sys.executable, '-c', 'import sys,amplifier_core._engine,amplifier_foundation,aiohttp;print(sys.version);print(amplifier_core._engine.__file__)'])
        ready &= run('browser-readiness', ['node', '--input-type=module', '-e', "import {chromium} from '@playwright/test';const b=await chromium.launch({headless:true});const p=await b.newPage();await p.setContent('<button>Ready</button>');await p.getByRole('button').click();console.log(process.version,await b.version());await b.close();"], cwd=ROOT / 'tests/web')
        if not ready:
            raise RuntimeError('Prepared interpreter/browser readiness failed')
        units = run('composer-units', ['node', '--test', 'tests/markdown-composer.test.mjs', 'tests/multi-quote.test.mjs', 'tests/markdown.test.mjs', 'tests/draft-journal.test.mjs'], cwd=ROOT / '.build/web-client')
        units &= run('send-quote-units', [sys.executable, '-m', 'pytest', '-q', 'tests/test_message_delivery.py', 'tests/test_multi_quote.py'], timeout=240)
        units &= run('checkpoint-boundary', ['node', 'tests/web/markdown-checkpoint-boundary.mjs'])
        units &= run('candidate-control', ['node', '.build/web-client/tests/markdown-gfm-falsifier.mjs'])
        baseline = ROOT / '.build/web-client/tests/fixtures-markdown-composer-baseline.txt'
        assert digest(baseline) == BASE_HASH, 'Wrong negative-control source'
        baseline_module = ROOT / '.build/web-client/src/markdown-composer.baseline.js'
        assert not baseline_module.exists()
        baseline_module.write_bytes(baseline.read_bytes())
        try:
            units &= run('baseline-control', ['node', '.build/web-client/tests/markdown-gfm-falsifier.mjs'], env={**os.environ, 'GFM_COMPOSER_MODULE': '../src/markdown-composer.baseline.js'}, expected_failure='GFM_TABLE_MUST_BE_RICH')
        finally:
            baseline_module.unlink()
        if not units:
            raise RuntimeError('Focused units/control failed; build and browser remain unrun')
        if not run('frontend-build', [sys.executable, 'scripts/web_client_build.py', 'build'], timeout=240):
            raise RuntimeError('Production build failed')
        static = ROOT / 'amplifier_web/static'
        shutil.copytree(static, OUT / 'static')
        report['static'] = {str(p.relative_to(static)): digest(p) for p in sorted(static.rglob('*')) if p.is_file()}
        (OUT / 'static-manifest.json').write_text(json.dumps({'inputs': inputs, 'static': report['static']}, indent=2) + '\n')
        selected = ['markdown-composer', 'multi-quote', 'draft-reload', 'compact-composer']
        plan = {'schemaVersion': 1, 'scenarios': [{'id': name, 'command': ['node', f'tests/web/{name}-browser.mjs'], 'env': {'AMPLIFIER_TEST_PYTHON': sys.executable, 'AMPLIFIER_TEST_ARTIFACTS': str(OUT / name), 'AMPLIFIER_TEST_OUTPUT': str(OUT / name), 'AMPLIFIER_PROOF_DIR': str(OUT / name)}} for name in selected]}
        browser = run_scenarios(ROOT, plan, OUT / 'browser', budget=360, case_timeout=120, provenance={'inputs': inputs})
        report['checks'].append({'id': 'source-browser', 'returncode': browser, 'passed': browser == 0, 'selection': selected})
        save()
        installed = run('installed-editor', [sys.executable, 'tests/web/composer-installed-check.py', '--markdown-only'], timeout=420)
        report['status'] = 'passed' if browser == 0 and installed else 'failed'
        save()
        return 0 if report['status'] == 'passed' else 1
    except Exception as error:
        report.update(status='failed', error=str(error))
        save()
        print(str(error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
