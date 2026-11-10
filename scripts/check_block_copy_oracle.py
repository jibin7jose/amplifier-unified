"""Focused TEST-ONLY block-copy oracle qualification in the prepared guest.

Uses unchanged committed release assets, not a host build or a live provider.
Retains the original blockquote expectation as a failing browser control.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from browser_check_runner import run_scenarios

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('/artifacts/block-copy-oracle')


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    contract = json.loads(Path(os.environ['AMPLIFIER_TEST_CONTRACT']).read_text())
    assert contract['profileId'] == 'browser-test'
    (OUT / 'execution-contract.json').write_text(json.dumps(contract, indent=2))
    names = ['tests/block-copy-browser.mjs', '.build/web-client/tests/markdown-composer.test.mjs',
             '.build/web-client/src/markdown-composer.js', '.build/web-client/src/block-copy.js',
             'tests/fixtures/empty_host_ui_server.py', 'tests/web/package-lock.json', 'uv.lock',
             'scripts/check_block_copy_oracle.py', 'amplifier_web/static/build.json']
    inputs = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    (OUT / 'inputs.json').write_text(json.dumps(inputs, indent=2))
    commands = [
        ('readiness', ['node', '--input-type=module', '-e', "import {chromium} from '@playwright/test';const b=await chromium.launch({headless:true});const p=await b.newPage();await p.setContent('<button>Ready</button>');await p.getByRole('button').click();console.log(process.version,await b.version());await b.close();"], ROOT / 'tests/web'),
        ('parser-contrasts', ['node', '--test', 'tests/markdown-composer.test.mjs'], ROOT / '.build/web-client'),
    ]
    for name, command, cwd in commands:
        with (OUT / (name + '.log')).open('w') as log:
            completed = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=90)
        if completed.returncode:
            print((OUT / (name + '.log')).read_text()[-12000:])
            return completed.returncode
    plan = {'schemaVersion': 1, 'scenarios': [
        {'id': 'old-oracle', 'command': ['node', 'tests/block-copy-browser.mjs'],
         'env': {'BLOCK_COPY_OLD_ORACLE': '1', 'AMPLIFIER_TEST_PYTHON': sys.executable,
                 'AMPLIFIER_TEST_ARTIFACTS': str(OUT / 'old-oracle')}},
        {'id': 'full-journey', 'command': ['node', 'tests/block-copy-browser.mjs'],
         'env': {'AMPLIFIER_TEST_PYTHON': sys.executable,
                 'AMPLIFIER_TEST_ARTIFACTS': str(OUT / 'full-journey')}},
    ]}
    run_scenarios(ROOT, plan, OUT / 'browser', budget=300, case_timeout=140, provenance={'inputs': inputs})
    # Expected negative outcome stays FAILED in the original runner report.
    negative = json.loads((OUT / 'old-oracle/block-copy-result.json').read_text())
    current = json.loads((OUT / 'full-journey/block-copy-result.json').read_text())
    original = json.loads((OUT / 'browser/results.json').read_text())
    clean = all(not row['cleanup']['remaining'] and not row['cleanup']['terminated'] for row in original['scenarios'])
    old_process, full_process = original['scenarios']
    negative_ok = (old_process['status'] == 'failed' and old_process['exitCode'] == 1
                   and old_process['reason'] is None
                   and negative['status'] == 'failed' and negative['stage'] == 'reference before reload'
                   and "locator('blockquote')" in negative['error']
                   and negative['reference']['before']['draft'] == negative['reference']['expected']
                   and negative['reference']['before']['text'] == negative['reference']['expected'])
    passed = (original['complete'] and negative_ok and current['status'] == 'passed' and clean
              and full_process['status'] == 'passed' and full_process['exitCode'] == 0
              and full_process['reason'] is None)
    result = {'passed': passed, 'negativeRejectedObsoleteAssertion': negative_ok,
              'fullJourney': current['status'], 'fullJourneyStage': current['stage'],
              'fixtureCleanupComplete': clean, 'productChanged': False,
              'limits': ['Synthetic release fixture, Chromium only; no provider/native-device acceptance',
                         'No product fix authorized by an obsolete representation assertion']}
    (OUT / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
