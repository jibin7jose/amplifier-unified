"""The fixed MCP proof must retain an actual failure, never accept any exit one."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import zlib

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'scripts/mcp_boundary_checks.sh'


def block(index):
    blocks = re.findall(r"python - <<'PY'\n(.*?)\nPY", SCRIPT.read_text(), re.S)
    assert len(blocks) == 3
    return compile(blocks[index], f'mcp-proof-block-{index}', 'exec')


@pytest.mark.parametrize('change', ['none', 'new-static', 'wrong-witness',
                                  'tracked-source', 'untracked-source', 'canonical-source'])
def test_mcp_plan_generation_preserves_source_and_fixed_rows(tmp_path, monkeypatch, change):
    source = (ROOT / 'frontend/tests/mcp-recovery-browser.mjs').read_bytes()
    target = tmp_path / 'frontend/tests/mcp-recovery-browser.mjs'
    target.parent.mkdir(parents=True)
    target.write_bytes(source + (b'\n' if change == 'canonical-source' else b''))
    folder = tmp_path / 'output/browser-checks/mcp-boundary/plans'
    folder.mkdir(parents=True)
    static = tmp_path / 'amplifier_web/static'
    static.mkdir(parents=True)
    (static / 'build.json').write_text('{"id":"synthetic"}')
    if change == 'new-static':
        (static / 'new-hashed.js').write_text('/* synthetic build */')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('MCP_BOUNDARY_PLAN_DIR', str(folder))
    monkeypatch.setenv('EXPECTED_REVISION', 'a' * 40)

    def git(args, **kwargs):
        if args == ['git', 'diff', '--name-only', 'HEAD']:
            return 'amplifier_web/service.py\n' if change == 'tracked-source' else 'amplifier_web/static/build.json\n'
        if args == ['git', 'ls-files', '--others', '--exclude-standard']:
            return ('frontend/tests/unowned.mjs\n' if change == 'untracked-source' else
                    'amplifier_web/static/new-hashed.js\n' if change == 'new-static' else
                    'output/browser-checks/mcp-boundary/revision.txt\n')
        assert args == ['git', 'diff', '--binary', 'HEAD', '--', 'amplifier_web/static']
        return b'synthetic generated static delta'

    monkeypatch.setattr(subprocess, 'check_output', git)
    if change in {'tracked-source', 'untracked-source', 'canonical-source'}:
        with pytest.raises(AssertionError):
            exec(block(0), {})
        assert not (folder / 'negative.json').exists()
        return
    exec(block(0), {})
    assert target.read_bytes() == source
    positive = json.loads((folder / 'positive.json').read_text())['scenarios']
    assert [row['id'] for row in positive] == [
        'mcp-recovery-repeat-1', 'mcp-recovery-repeat-2', 'mcp-recovery-repeat-3', 'mcp-clients-adjacent']
    assert [row['command'] for row in positive] == [
        ['node', 'tests/mcp-recovery-browser.mjs']] * 3 + [['node', 'tests/mcp-clients-browser.mjs']]
    negative = json.loads((folder / 'negative.json').read_text())['scenarios']
    assert len(negative) == 1 and negative[0]['command'] == ['node', 'tests/.mcp-omit-close-negative.mjs']
    assert all(value.startswith('{root}/output/browser-checks/mcp-boundary/')
               for row in positive + negative for value in row['env'].values())
    variant = (target.parent / '.mcp-omit-close-negative.mjs').read_text()
    assert variant == (folder / 'omit-close-negative.mjs').read_text()
    assert ' const saved=page.waitForResponse' not in variant
    assert "name:/^Close (?:settings|panel)$/}).click()" not in variant
    assert 'await expect(setup).toHaveCount(0)' in variant
    assert variant.index('await page.screenshot({path:screenshot})') < variant.index('throw error;')
    assert variant.index('await secondBrowser?.close()') < variant.index('MCP_NEGATIVE_CLEANUP')
    identity = json.loads((folder / 'source.json').read_text())
    assert identity['revision'] == 'a' * 40
    assert identity['canonicalSha256'] == hashlib.sha256(source).hexdigest()
    assert identity['variantSha256'] == hashlib.sha256(variant.encode()).hexdigest()
    assert identity['staticSha256'] == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in static.iterdir()}
    assert identity['untrackedStaticFiles'] == (
        ['amplifier_web/static/new-hashed.js'] if change == 'new-static' else [])
    # Check the actual generated source without launching browser or fixture.
    subprocess.run(['node', '--check', str(target.parent / '.mcp-omit-close-negative.mjs')],
                   check=True, capture_output=True, timeout=10)
    # Execute the generated capture/rethrow and finally bodies, not a copied
    # witness. Controlled stubs stand in for the browser; no fixture is launched.
    start = variant.index(' try{await expect(setup).toHaveCount(0)}catch(error){')
    end = variant.index(' await expect.poll(', start)
    capture = variant[start:end]
    if change == 'wrong-witness':
        assert '/Received[^\\n]*1/' in capture
        capture = capture.replace('/Received[^\\n]*1/', '/Received[^\\n]*2/')
    cleanup_body = variant.rsplit('}finally{', 1)[1].rsplit('}', 1)[0]
    negative_dir = folder.parent / 'negative'
    env = {**os.environ, 'MCP_RECOVERY_SCREENSHOT': str(negative_dir / 'overlay-before-cleanup.png'),
           'MCP_NEGATIVE_PROOF': str(negative_dir / 'boundary.json'),
           'MCP_NEGATIVE_CLEANUP': str(negative_dir / 'cleanup.json')}
    stub = "import {finishFixture} from " + json.dumps((ROOT / 'frontend/tests/fixture-lifecycle.mjs').as_uri()) + ";\n" + r"""
import assert from 'node:assert/strict';
import {writeFile as save} from 'node:fs/promises';
const events=[],boundary='initial client',directory='synthetic-directory';
let cleanupComplete=false;
const originalError=undefined;
const failure=new Error('expect(locator).toHaveCount(expected)\nExpected: 0\nReceived: 1');
const writeFile=async(path,body)=>{events.push(path===process.env.MCP_NEGATIVE_CLEANUP?'cleanup-proof':'witness');await save(path,body)};
const setup={isVisible:async()=>true,count:async()=>1};
const expect=()=>({toHaveCount:async value=>{assert.equal(value,0);events.push('assertion');throw failure}});
const page={screenshot:async({path})=>{events.push('capture');await save(path,Buffer.from('89504e470d0a1a0a','hex'))}};
const secondBrowser={close:async()=>events.push('second-close')},browser={close:async()=>events.push('close')};
const stop=async()=>events.push('stop');
const rm=async(path,options)=>{assert.equal(path,directory);assert.deepEqual(options,{recursive:true,force:true});events.push('remove')};
try{
 try{
""" + capture + "\n }finally{\n" + cleanup_body + r"""
 }
 assert.fail('Negative assertion did not reject');
}catch(error){
 assert.equal(error,failure,'Original assertion must survive');
 assert.deepEqual(events,['assertion','capture','witness','second-close','close','stop','remove','cleanup-proof']);
 process.exitCode=1;
}
"""
    completed = subprocess.run(['node', '--input-type=module', '-e', stub], env=env,
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 1 and completed.stderr == ''
    receipt = folder.parent / 'negative-run/results.json'
    write_json(receipt, {'complete': True, 'passed': False, 'scenarios': [{
        'id': 'mcp-open-overlay-negative', 'status': 'failed', 'exitCode': completed.returncode,
        'reason': None, 'cleanup': {'remaining': [], 'terminated': 0}}]})
    before = receipt.read_bytes()
    if change == 'wrong-witness':
        with pytest.raises(AssertionError):
            exec(block(2), {})
        assert not (folder.parent / 'negative-verdict.json').exists()
    else:
        exec(block(2), {})
        assert json.loads((folder.parent / 'negative-verdict.json').read_text())['verdict'] == 'EXPECTED_BOUNDARY_REJECTION'
    assert receipt.read_bytes() == before


@pytest.mark.parametrize('has_original', [False, True])
def test_mcp_cleanup_failure_cannot_certify_complete(tmp_path, has_original):
    # Exercise the canonical finally body when the lifecycle owner reports a
    # timeout/failure. The original assertion survives and completion stays false.
    source = (ROOT / 'frontend/tests/mcp-recovery-browser.mjs').read_text()
    cleanup = source.rsplit('}finally{', 1)[1].rsplit('}', 1)[0]
    program = r"""
import assert from 'node:assert/strict';
const initial=new Error('original assertion'),failed=new Error('browser close timed out');
let cleanupComplete=false;
const originalError=HAS_ORIGINAL?initial:undefined;
const reports=[];console.error=error=>reports.push(error);
const finishFixture=async()=>{throw failed};
const secondBrowser=undefined,browser=undefined,directory='synthetic';
const stop=async()=>{},rm=async()=>{};
let thrown;
try{
 try{if(originalError)throw originalError}finally{
""".replace('HAS_ORIGINAL', json.dumps(has_original)) + cleanup + r"""
 }
}catch(error){thrown=error}
assert.equal(cleanupComplete,false,'Failed cleanup must never certify completion');
assert.equal(thrown,originalError||failed);
assert.deepEqual(reports,originalError?[failed]:[]);
"""
    result = subprocess.run(['node', '--input-type=module', '-e', program],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.mark.parametrize('change', ['none', 'missing-repeat', 'duplicate-id', 'failed',
                                  'timeout', 'cleanup', 'missing-boundary'])
def test_positive_gate_requires_every_fresh_boundary_and_clean_exit(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    folder = Path('output/browser-checks/mcp-boundary/positive')
    names = ['mcp-recovery-repeat-1', 'mcp-recovery-repeat-2', 'mcp-recovery-repeat-3', 'mcp-clients-adjacent']
    rows = [{'id': name, 'status': 'passed', 'exitCode': 0, 'reason': None,
             'cleanup': {'remaining': [], 'terminated': 0}, 'log': name + '.log'} for name in names]
    report = {'complete': True, 'passed': True, 'scenarios': rows}
    if change == 'missing-repeat':
        rows.pop(1)
    elif change == 'duplicate-id':
        rows[1]['id'] = rows[0]['id']
    elif change == 'failed':
        rows[0]['exitCode'] = 1
    elif change == 'timeout':
        rows[0]['reason'] = 'timeout'
    elif change == 'cleanup':
        rows[0]['cleanup']['terminated'] = 1
    write_json(folder / 'results.json', report)
    for row in rows:
        (folder / row['log']).write_text(
            'MCP recovery setup boundary: synthetic\n' * (4 if change == 'missing-boundary' else 5)
            + 'MCP recovery browser passed: synthetic\n')
    if change == 'none':
        exec(block(1), {})
    else:
        with pytest.raises(AssertionError):
            exec(block(1), {})


@pytest.mark.parametrize('change', ['none', 'exit-zero', 'wrong-exit', 'timeout', 'unfinished',
                                  'cleanup', 'wrong-id', 'wrong-boundary', 'hidden-overlay',
                                  'wrong-assertion', 'no-screenshot', 'incomplete-finally'])
def test_negative_gate_requires_exact_failure_witness_and_keeps_raw_receipt(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    folder = Path('output/browser-checks/mcp-boundary')
    row = {'id': 'mcp-open-overlay-negative', 'status': 'failed', 'exitCode': 1,
           'reason': None, 'cleanup': {'remaining': [], 'terminated': 0}}
    report = {'complete': True, 'passed': False, 'scenarios': [row]}
    proof = {'boundary': 'mcp-setup-rendered-absence', 'omittedClose': True,
             'boundaryName': 'initial client', 'visible': True, 'count': 1,
             'screenshotSaved': True, 'screenshotName': 'overlay-before-cleanup.png',
             'assertionMentions': True, 'assertionExpectedZero': True, 'assertionReceivedOne': True}
    cleanup = {'completed': True}
    if change == 'exit-zero':
        row['exitCode'] = 0
    elif change == 'wrong-exit':
        row['exitCode'] = 2
    elif change == 'timeout':
        row['reason'] = 'timeout'
    elif change == 'unfinished':
        report['complete'] = False
    elif change == 'cleanup':
        row['cleanup']['remaining'] = ['unknown']
    elif change == 'wrong-id':
        row['id'] = 'another-negative'
    elif change == 'wrong-boundary':
        proof['boundaryName'] = 'later client'
    elif change == 'hidden-overlay':
        proof['visible'] = False
    elif change == 'wrong-assertion':
        proof['assertionReceivedOne'] = False
    elif change == 'incomplete-finally':
        cleanup['completed'] = False
    receipt = folder / 'negative-run/results.json'
    write_json(receipt, report)
    write_json(folder / 'negative/boundary.json', proof)
    write_json(folder / 'negative/cleanup.json', cleanup)
    (folder / 'negative/overlay-before-cleanup.png').write_bytes(
        b'not an image' if change == 'no-screenshot' else b'\x89PNG\r\n\x1a\n')
    before = receipt.read_bytes()
    if change == 'none':
        exec(block(2), {})
        verdict = json.loads((folder / 'negative-verdict.json').read_text())
        assert verdict['verdict'] == 'EXPECTED_BOUNDARY_REJECTION' and verdict['rawExitCode'] == 1
        assert verdict['rawRunnerPassed'] is False
    else:
        with pytest.raises(AssertionError):
            exec(block(2), {})
        assert not (folder / 'negative-verdict.json').exists()
    assert receipt.read_bytes() == before


def test_mcp_workflow_is_fixed_default_off_with_separate_bounded_job():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert inputs['mcp_boundary']['type'] == 'boolean' and inputs['mcp_boundary']['default'] == 'false'
    assert workflow['on']['workflow_call']['inputs'] == {'revision': {'type': 'string', 'required': 'true'}}
    normal = workflow['jobs']['browser']
    assert normal['if'] == '${{ !inputs.permissions_history && !inputs.readiness_trio && !inputs.canvas_qualification && !inputs.peer_messages && !inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic && !inputs.attachment_experience }}'
    assert normal['timeout-minutes'] == '45'
    normal_runs = '\n'.join(step.get('run', '') for step in normal['steps'])
    assert '--budget-seconds 1500' in normal_runs and 'mcp_boundary_checks' not in normal_runs
    job = workflow['jobs']['mcp-boundary']
    assert job['if'] == "${{ !inputs.permissions_history && !inputs.readiness_trio && github.event_name == 'workflow_dispatch' && !inputs.canvas_qualification && !inputs.peer_messages && inputs.mcp_boundary && !inputs.compact_composer && !inputs.destination_history && !inputs.settings_close_diagnostic }}"
    assert job['timeout-minutes'] == '30' and job['runs-on'] == 'ubuntu-latest'
    steps = job['steps']
    assert steps[0]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    commands = '\n'.join(step.get('run', '') for step in steps)
    assert 'uv sync --locked --group dev --group artifacts' in commands
    assert 'npm ci --prefix frontend' in commands and 'npm run build --prefix frontend' in commands
    assert 'npx playwright install --with-deps chromium' in commands
    assert commands.count('bash scripts/mcp_boundary_checks.sh') == 1
    assert not any('continue-on-error' in step for step in steps)
    check = next(step for step in steps if step.get('name') == 'Run the fixed MCP boundary proof')
    assert check['env'] == {'EXPECTED_REVISION': '${{ github.sha }}'}
    upload = next(step for step in steps if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02')
    assert upload['uses'] == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
    assert upload['if'] == "always() && steps.mcp-evidence.outcome == 'success'"
    assert upload['with']['path'] == 'output/browser-checks/mcp-boundary-public/'
    assert upload['with']['if-no-files-found'] == 'error'
    script = SCRIPT.read_text()
    assert script.count('uv run --no-sync python scripts/browser_check_runner.py') == 2
    assert '--budget-seconds 1200' in script and '--budget-seconds 300' in script
    assert 'test "$negative_rc" -eq 1' in script and 'set -euo pipefail' in script
    assert steps[-1]['if'] == 'always()'
    assert "not record['rejected']" in steps[-1]['run']


def evidence_block(name):
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    step = next(step for step in workflow['jobs']['mcp-boundary']['steps'] if step.get('name') == name)
    return compile('\n'.join(step['run'].splitlines()[1:-1]), 'mcp-evidence-step', 'exec')


def synthetic_png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\0\0\0')) + chunk(b'IEND', b''))


@pytest.mark.parametrize('damage', ['none', 'temporary', 'truncated-png', 'bad-json', 'symlink'])
def test_safe_evidence_survives_rejected_file_without_false_success(tmp_path, monkeypatch, damage):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_WORKSPACE', str(tmp_path))
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path / 'runner-temp'))
    raw = Path('output/browser-checks/mcp-boundary')
    plan = {'schemaVersion': 1, 'scenarios': [{
        'id': 'mcp-recovery-repeat-1', 'command': ['node', 'tests/mcp-recovery-browser.mjs'],
        'env': {'MCP_RECOVERY_SCREENSHOT': '{root}/output/browser-checks/mcp-boundary/journey.png'}}]}
    write_json(raw / 'plans/positive.json', plan)
    plan_bytes = (raw / 'plans/positive.json').read_bytes()
    assert not plan_bytes.endswith(b'\n')
    plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
    write_json(raw / 'positive/results.json', {
        'complete': True, 'path': str(tmp_path / 'synthetic'), 'source': {'planSha256': plan_sha256}})
    (raw / 'positive/step.log').write_text(str(tmp_path) + '\noriginal failure\n')
    (raw / 'positive/screen.png').write_bytes(synthetic_png())
    if damage == 'temporary':
        (raw / 'results.tmp').write_text('{"complete":')
    elif damage == 'truncated-png':
        (raw / 'broken.png').write_bytes(synthetic_png()[:-6])
    elif damage == 'bad-json':
        (raw / 'broken.json').write_text('{"complete":')
    elif damage == 'symlink':
        (raw / 'alias.log').symlink_to(raw.resolve() / 'positive/step.log')
    originals = {path: path.read_bytes() for path in raw.rglob('*') if path.is_file() and not path.is_symlink()}
    exec(evidence_block('Stage safe owned evidence before upload'), {})
    public = Path('output/browser-checks/mcp-boundary-public')
    retained = json.loads((public / 'retention.json').read_text())
    assert {row['path'] for row in retained['retained']} == {
        'plans/positive.json', 'positive/results.json', 'positive/step.log', 'positive/screen.png'}
    published_report = json.loads((public / 'positive/results.json').read_text())
    assert (public / 'plans/positive.json').read_bytes() == plan_bytes
    assert hashlib.sha256((public / 'plans/positive.json').read_bytes()).hexdigest() == published_report['source']['planSha256']
    for row in retained['retained']:
        assert row['originalSha256'] == hashlib.sha256((raw / row['path']).read_bytes()).hexdigest()
        assert row['publishedSha256'] == hashlib.sha256((public / row['path']).read_bytes()).hexdigest()
        assert row['redacted'] == (row['path'] in {'positive/results.json', 'positive/step.log'})
    assert str(tmp_path) not in (public / 'positive/results.json').read_text()
    assert 'original failure' in (public / 'positive/step.log').read_text()
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert len(retained['rejected']) == (0 if damage == 'none' else 1)
    if damage == 'none':
        exec(evidence_block('Reject incomplete evidence after retaining safe diagnostics'), {})
    else:
        assert not (public / retained['rejected'][0]['path']).exists()
        with pytest.raises(AssertionError, match='evidence incomplete'):
            exec(evidence_block('Reject incomplete evidence after retaining safe diagnostics'), {})
