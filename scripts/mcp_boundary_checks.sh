#!/usr/bin/env bash
# Fixed opt-in proof using the maintained browser runner; never the full suite.
# Setup/build/browser installation belong to the existing owned CI workflow.
set -euo pipefail
: "${EXPECTED_REVISION:?Supply the reviewed candidate SHA}"
export EXPECTED_REVISION
test "${GITHUB_ACTIONS:-}" = true
cd "${GITHUB_WORKSPACE:?Use the owned checkout}"
test "$(git rev-parse HEAD)" = "$EXPECTED_REVISION"
export AMPLIFIER_TEST_PYTHON="$PWD/.venv/bin/python"
export MCP_BOUNDARY_PLAN_DIR="$PWD/output/browser-checks/mcp-boundary/plans"
mkdir -p "$(dirname "$MCP_BOUNDARY_PLAN_DIR")"
mkdir "$MCP_BOUNDARY_PLAN_DIR"
python - <<'PY'
import hashlib, json, os, subprocess
from pathlib import Path
root = Path.cwd()
folder = Path(os.environ['MCP_BOUNDARY_PLAN_DIR'])
# Only generated production assets may differ. Pre-setup evidence has its own
# exact output root, not a blanket exemption for untracked source files.
changed = subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], text=True).splitlines()
assert all(name.startswith('amplifier_web/static/') for name in changed), changed
untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], text=True).splitlines()
assert all(name.startswith(('output/browser-checks/mcp-boundary/', 'amplifier_web/static/'))
           for name in untracked), untracked
static = root / 'amplifier_web/static'
assert static.is_dir() and not static.is_symlink()
assert not any(path.is_symlink() for path in static.rglob('*'))
static_hashes = {str(path.relative_to(static)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(static.rglob('*')) if path.is_file()}
assert 'build.json' in static_hashes
source_path = root / 'tests/web/mcp-recovery-browser.mjs'
source = source_path.read_text()
assert hashlib.sha256(source_path.read_bytes()).hexdigest() == 'f5146f8d74e8fb5cd1b9b11951cb81ce8c4b613599704fc0afb2cc2773888044'
positive = []
for number in range(1, 4):
    name = f'mcp-recovery-repeat-{number}'
    directory = root / 'output/browser-checks/mcp-boundary' / name
    directory.mkdir(parents=True, exist_ok=False)
    positive.append({'id': name, 'command': ['node', 'tests/web/mcp-recovery-browser.mjs'],
                     'env': {'MCP_RECOVERY_SCREENSHOT': '{root}/' + str(directory.relative_to(root) / 'journey.png')}})
adjacent = root / 'output/browser-checks/mcp-boundary/mcp-clients-adjacent'
adjacent.mkdir(parents=True, exist_ok=False)
positive.append({'id': 'mcp-clients-adjacent', 'command': ['node', 'tests/web/mcp-clients-browser.mjs'],
                 'env': {'MCP_RECOVERY_SCREENSHOT': '{root}/' + str(adjacent.relative_to(root) / 'journey.png')}})
(folder / 'positive.json').write_text(json.dumps({'schemaVersion': 1, 'scenarios': positive}, indent=2))
# Omit only Close plus its waiter/receipt. Keep loaded empty setup and the
# original absence assertion. Preserve its failure before the original cleanup.
start = source.index(' const saved=page.waitForResponse(response=>{', source.index('async function dismissFixtureSetup'))
end = source.index(' await expect(setup).toHaveCount(0);', start)
omitted = source[start:end]
assert omitted.count("getByRole('button',{name:/^Close (?:settings|panel)$/}).click()") == 1
assert 'receipt.accepted,true' in omitted
variant = source[:start] + ' // Deliberate omit-close negative control.\n' + source[end:]
assert variant.count(' await expect(setup).toHaveCount(0);') == 1
variant = variant.replace(' await expect(setup).toHaveCount(0);', ''' try{await expect(setup).toHaveCount(0)}catch(error){
  const screenshot=process.env.MCP_RECOVERY_SCREENSHOT;
  await page.screenshot({path:screenshot});
  await writeFile(process.env.MCP_NEGATIVE_PROOF,JSON.stringify({
   boundary:'mcp-setup-rendered-absence',omittedClose:true,boundaryName:boundary,
   visible:await setup.isVisible(),count:await setup.count(),
   screenshotSaved:true,screenshotName:screenshot.split('/').pop(),
   assertionMentions:String(error.message).includes('toHaveCount'),
   assertionExpectedZero:/Expected[^\\n]*0/.test(String(error.message)),
   assertionReceivedOne:/Received[^\\n]*1/.test(String(error.message))
  }));
  throw error;
 }''', 1)
finally_text = "}finally{\n try{\n  await finishFixture({label:'MCP recovery fixture',close:[\n   ['second browser',async()=>{await secondBrowser?.close()}],\n   ['browser',async()=>{await browser?.close()}],\n  ],stop:async()=>{await stop();await rm(directory,{recursive:true,force:true})}});\n  cleanupComplete=true;\n }catch(error){if(originalError)console.error(error);else throw error}\n}"
assert variant.count(finally_text) == 1
variant = variant.replace(finally_text, finally_text[:-1] + ";await writeFile(process.env.MCP_NEGATIVE_CLEANUP,JSON.stringify({completed:cleanupComplete}))}", 1)
negative_script = root / 'tests/web/.mcp-omit-close-negative.mjs'
with negative_script.open('x') as handle:
    handle.write(variant)
negative_dir = root / 'output/browser-checks/mcp-boundary/negative'
negative_dir.mkdir(parents=True, exist_ok=False)
negative = {'schemaVersion': 1, 'scenarios': [{
    'id': 'mcp-open-overlay-negative',
    'command': ['node', 'tests/web/.mcp-omit-close-negative.mjs'],
    'env': {'MCP_RECOVERY_SCREENSHOT': '{root}/' + str(negative_dir.relative_to(root) / 'overlay-before-cleanup.png'),
            'MCP_NEGATIVE_PROOF': '{root}/' + str(negative_dir.relative_to(root) / 'boundary.json'),
            'MCP_NEGATIVE_CLEANUP': '{root}/' + str(negative_dir.relative_to(root) / 'cleanup.json')},
}]}
(folder / 'negative.json').write_text(json.dumps(negative, indent=2))
(folder / 'omit-close-negative.mjs').write_text(variant)
(folder / 'source.json').write_text(json.dumps({
    'revision': os.environ['EXPECTED_REVISION'],
    'canonicalSha256': hashlib.sha256(source.encode()).hexdigest(),
    'variantSha256': hashlib.sha256(variant.encode()).hexdigest(),
    'omittedBlockSha256': hashlib.sha256(omitted.encode()).hexdigest(),
    'staticSha256': static_hashes,
    'untrackedStaticFiles': [name for name in untracked if name.startswith('amplifier_web/static/')],
    'generatedStaticDiffSha256': hashlib.sha256(subprocess.check_output(['git', 'diff', '--binary', 'HEAD', '--', 'amplifier_web/static'])).hexdigest(),
    'mutation': 'Omit helper Close/waiter/receipt; retain failed absence assertion; capture before original cleanup and confirm cleanup completion',
}, indent=2))
PY
# Fresh process/browser/fixture per row, unchanged 300-second per-case ceiling.
# Stop before the separate negative if positives or process cleanup fail.
uv run --no-sync python scripts/browser_check_runner.py \
  --plan "$MCP_BOUNDARY_PLAN_DIR/positive.json" \
  --output output/browser-checks/mcp-boundary/positive --budget-seconds 1200
python - <<'PY'
import json
from pathlib import Path
folder = Path('output/browser-checks/mcp-boundary/positive')
report = json.loads((folder / 'results.json').read_text())
assert report['complete'] and report['passed'] and len(report['scenarios']) == 4
assert [row['id'] for row in report['scenarios']] == [
    'mcp-recovery-repeat-1', 'mcp-recovery-repeat-2', 'mcp-recovery-repeat-3', 'mcp-clients-adjacent']
for row in report['scenarios']:
    assert row['status'] == 'passed' and row['exitCode'] == 0 and row['reason'] is None
    assert row['cleanup'] == {'remaining': [], 'terminated': 0}
    if row['id'].startswith('mcp-recovery-repeat-'):
        text = (folder / row['log']).read_text()
        assert text.count('MCP recovery setup boundary: ') == 5
        assert 'MCP recovery browser passed:' in text
PY
set +e
uv run --no-sync python scripts/browser_check_runner.py \
  --plan "$MCP_BOUNDARY_PLAN_DIR/negative.json" \
  --output output/browser-checks/mcp-boundary/negative-run --budget-seconds 300
negative_rc=$?
set -e
test "$negative_rc" -eq 1
python - <<'PY'
import json
from pathlib import Path
folder = Path('output/browser-checks/mcp-boundary')
report = json.loads((folder / 'negative-run/results.json').read_text())
assert report['complete'] and report['passed'] is False and len(report['scenarios']) == 1
row = report['scenarios'][0]
assert row['id'] == 'mcp-open-overlay-negative' and row['status'] == 'failed'
assert row['exitCode'] == 1 and row['reason'] is None
assert row['cleanup'] == {'remaining': [], 'terminated': 0}
proof = json.loads((folder / 'negative/boundary.json').read_text())
assert proof == {'boundary': 'mcp-setup-rendered-absence', 'omittedClose': True,
                 'boundaryName': 'initial client', 'visible': True, 'count': 1,
                 'screenshotSaved': True, 'screenshotName': 'overlay-before-cleanup.png',
                 'assertionMentions': True, 'assertionExpectedZero': True, 'assertionReceivedOne': True}
assert (folder / 'negative/overlay-before-cleanup.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
assert json.loads((folder / 'negative/cleanup.json').read_text()) == {'completed': True}
# Never rewrite the raw failed runner receipt into a passing scenario.
(folder / 'negative-verdict.json').write_text(json.dumps({
    'verdict': 'EXPECTED_BOUNDARY_REJECTION', 'rawRunnerPassed': False,
    'rawExitCode': 1, 'boundary': proof['boundary'], 'cleanupConfirmed': True,
}, indent=2))
PY
