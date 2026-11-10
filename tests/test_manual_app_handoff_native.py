"""Portable fixture-contract checks; actual qualification is the Windows job."""
import ast
import importlib.util
import json
from pathlib import Path
import pytest


@pytest.fixture
def fixture_module():
    path = Path(__file__).parent / 'fixtures/manual_app_handoff_native.py'
    spec = importlib.util.spec_from_file_location('manual_fixture_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_fixture_compiles_and_keeps_production_verifiers_real():
    path = Path(__file__).parent / 'fixtures/manual_app_handoff_native.py'
    source = path.read_text()
    ast.parse(source)
    assert 'WindowsOwnedProcess.start' in source and 'trusted_python=sys._base_executable' in source
    assert "artifacts._get = local_acquisition" in source
    for name in ('_native_binding', '_payload', '_health', '_pinned_reader', 'installed_target'):
        assert name + ' =' not in source
    assert "'publicAcceptance': False" in source
    assert "'UV_LINK_MODE': 'copy'" in source


def test_native_fixture_is_a_separate_narrow_workflow():
    path = Path(__file__).parents[1] / '.github/workflows/manual-app-handoff-native.yml'
    source = path.read_text()
    assert 'runs-on: windows-latest' in source and 'matrix:' not in source
    assert 'workflow_dispatch:' in source
    assert 'platform-mechanisms' not in source and 'release.yml' not in source
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in source


@pytest.mark.parametrize('settled', [False, True])
def test_cleanup_preserves_child_checkpoint_and_requires_settlement(tmp_path, monkeypatch, fixture_module, settled):
    root = tmp_path / 'manual-handoff-fixture'
    root.mkdir()
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (root / 'child.json').write_text('{"status":"FAIL","stage":"payload"}')
    (evidence / 'ownership.json').write_text(json.dumps({'root': str(root), 'jobsSettled': settled}))
    monkeypatch.setattr(fixture_module, 'evidence', lambda: evidence)
    monkeypatch.setattr(fixture_module, 'canonical_temp', lambda: tmp_path)
    assert fixture_module.cleanup() is settled
    assert root.exists() is not settled
    assert json.loads((evidence / 'child-progress.json').read_text())['stage'] == 'payload'
    assert json.loads((evidence / 'cleanup.json').read_text())['removed'] is settled


def test_outer_failure_retains_terminal_fact_and_partial_cases(tmp_path, monkeypatch, fixture_module):
    root, evidence = tmp_path / 'root', tmp_path / 'evidence'
    root.mkdir()
    (root / 'child.json').write_text('{"status":"FAIL","stage":"payload","checks":{"owner":"PASS"}}')
    monkeypatch.setenv('HANDOFF_ROOT', str(root))
    monkeypatch.setattr(fixture_module, 'evidence', lambda: evidence)
    async def timed_out():
        raise TimeoutError()
    monkeypatch.setattr(fixture_module, 'contained', timed_out)
    assert fixture_module.run() is False
    result = json.loads((evidence / 'native.json').read_text())
    assert result['checks'] == {'owner': 'PASS'}
    assert result['status'] == 'FAIL' and result['failure']['type'] == 'TimeoutError'


def test_preparation_uv_operations_use_owned_tree_not_raw_timeout(fixture_module):
    import inspect
    tree = ast.parse(inspect.getsource(fixture_module.prepare))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert sum(isinstance(node.func, ast.Name) and node.func.id == 'owned_command' for node in calls) == 2
    assert not any(isinstance(node.func, ast.Attribute) and node.func.attr == 'run'
                   and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess' for node in calls)
    assert 'jobsSettled' in inspect.getsource(fixture_module.owned_command)