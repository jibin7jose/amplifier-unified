"""The peer opt-in is fixed, isolated, and does not replace normal coverage."""
import ast
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_peer_hosted_route_is_fixed_and_separate():
    workflow = yaml.load((ROOT / '.github/workflows/browser-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['on']['workflow_dispatch']['inputs']['peer_messages'] == {
        'description': 'Fixed peer groups, formatting negative control and installed-wheel browser proof',
        'type': 'boolean', 'default': 'false'}
    assert set(workflow['jobs']) == {'browser', 'mcp-boundary', 'destination-history', 'peer-messages', 'compact-composer', 'canvas-qualification', 'attachment-experience', 'readiness-trio', 'permissions-history'}
    for name in ('browser', 'mcp-boundary', 'destination-history'):
        assert '!inputs.peer_messages' in workflow['jobs'][name]['if']
    job = workflow['jobs']['peer-messages']
    assert job['if'] == "${{ !inputs.permissions_history && !inputs.readiness_trio && github.event_name == 'workflow_dispatch' && inputs.peer_messages }}"
    assert job['runs-on'] == 'ubuntu-latest' and job['timeout-minutes'] == '30'
    steps = job['steps']
    reject = steps[0]
    assert reject['run'] == 'exit 1'
    for selection in ('canvas_qualification', 'multi_quote', 'attachment_experience', 'compact_composer', 'destination_history', 'settings_close_diagnostic', 'mcp_boundary', 'app004_qualification', 'attachment_consumption'):
        assert 'inputs.' + selection in reject['if']
    assert not any(step.get('continue-on-error') for step in steps)
    assert 'secrets.' not in json.dumps(job)
    commands = [step.get('run', '') for step in steps]
    assert 'uv sync --locked --group dev --group artifacts' in commands
    assert any('npm ci --prefix tests/web' in command for command in commands)
    assert any('npm ci --prefix .build/web-client' in command for command in commands)
    assert 'uv run --no-sync python scripts/peer_message_qualification.py' in commands
    assert commands.index('python scripts/peer_message_qualification.py --initialize') < commands.index('uv sync --locked --group dev --group artifacts')
    upload, = [step for step in steps if step.get('uses') == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02']
    assert upload['if'] == 'always()' and upload['with']['path'] == 'output/peer-messages/'


def test_peer_script_requires_real_counts_bytes_and_negative_failure():
    source = (ROOT / 'scripts/peer_message_qualification.py').read_text()
    tree = ast.parse(source)
    names = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'CHECKS' for target in node.targets))
    assert names == ('python', 'node', 'build', 'sourceBrowser', 'whitespaceNegative', 'wheel', 'installedBrowser')
    for required in ('PEER_NEGATIVE_CONTROL_CONFIRMED', "'failure', 'error', 'skipped'", "'site-packages'",
                     "env.pop('PYTHONPATH', None)", "'--no-deps'", "'--reinstall'",
                     "'payloadsVerifiedBeforeAfter'", "set(files) == set(source['staticBefore'])", "'status': 'not_run'"):
        assert required in source
    assert "'peer-preview'" in source and "'tests/web/peer-preview-browser.mjs'" in source
    assert "assert not sys.argv[1:]" in source
    assert 'stage_web_test_harness(ROOT, harness, python_tests=True,' in source
    assert "source_helpers=('settings-navigation.js',)" in source
    assert "cwd=ROOT / '.build/web-client'" in source
