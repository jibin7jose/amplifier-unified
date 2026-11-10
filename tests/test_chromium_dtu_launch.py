"""The optional DTU preload forwards options to both independently installed owners."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


def fixture(root):
    preload = root / 'tests/fixtures/chromium_dtu_launch.mjs'
    preload.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'tests/fixtures/chromium_dtu_launch.mjs', preload)
    owners = []
    for directory in ('tests/web', '.build/web-client'):
        module = root / directory / 'node_modules/playwright/index.mjs'
        module.parent.mkdir(parents=True)
        module.write_text("export const chromium={launch:async(options={})=>({options}),"
                          "launchPersistentContext:async(directory,options={})=>({directory,options})};")
        owners.append(module.as_uri())
    return preload, owners


@pytest.mark.parametrize('arguments', [[], ['--no-sandbox', '--disable-gpu']])
def test_preload_preserves_launch_and_persistent_context_options_for_both_owners(tmp_path, arguments):
    preload, owners = fixture(tmp_path)
    script = '''
import assert from 'node:assert/strict';
for(const uri of JSON.parse(process.env.TEST_OWNERS)){
 const {chromium}=await import(uri),extra=JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS);
 const original={headless:false,args:['--caller-flag','--no-sandbox']};
 const expected={headless:false,args:[...new Set([...original.args,...extra])]};
 assert.deepEqual(await chromium.launch(original),{options:expected});
 assert.deepEqual(await chromium.launchPersistentContext('owned-profile',original),{directory:'owned-profile',options:expected});
 assert.deepEqual(original,{headless:false,args:['--caller-flag','--no-sandbox']});
 assert.deepEqual(await chromium.launch(),{options:extra.length?{args:extra}:{}});
}
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script], env={**os.environ,
        'NODE_OPTIONS': '--import=' + str(preload), 'AMPLIFIER_CHROMIUM_ARGS': json.dumps(arguments),
        'TEST_OWNERS': json.dumps(owners)}, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('arguments', ['{}', '[1]', 'invalid JSON'])
def test_invalid_preload_arguments_fail_before_a_browser_can_start(tmp_path, arguments):
    preload, _ = fixture(tmp_path)
    result = subprocess.run(['node', '-e', 'process.stdout.write("should not run")'],
        env={**os.environ, 'NODE_OPTIONS': '--import=' + str(preload), 'AMPLIFIER_CHROMIUM_ARGS': arguments},
        text=True, capture_output=True, timeout=10)
    assert result.returncode != 0 and result.stdout == ''
