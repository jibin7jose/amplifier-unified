"""The composer qualification is a fixed selection, not an arbitrary runner."""
import importlib.util
from pathlib import Path
import sys
import json
import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_composer_checks as checks


def test_fixed_source_and_installed_selections(tmp_path):
    source = checks.plan(tmp_path)
    assert [r['id'] for r in source['scenarios']] == [
        'composer-units', 'navigation-reaction-units', 'compact-composer', 'composer-options',
        'composer-primary-action', 'composer-readiness', 'message-delivery', 'draft-switch',
        'destination-history', 'message-interactions', 'mobile-navigation', 'shell-components']
    installed = checks.plan(tmp_path, installed=True)
    assert [r['id'] for r in installed['scenarios']] == [
        'compact-composer', 'composer-options', 'composer-primary-action', 'message-delivery',
        'message-interactions', 'mobile-navigation']
    assert all(r['command'][0] == 'node' for r in source['scenarios'])
    assert all(r['cwd'] == '.build/web-client' for r in source['scenarios'][:2])
    assert all(r['command'][1] == f"tests/web/{r['id']}-browser.mjs"
               and 'cwd' not in r for r in source['scenarios'][2:])
    assert source['scenarios'][0]['command'][-2:] == [
        'tests/chat-controls.test.mjs', 'tests/bundle-controls.test.mjs']
    assert all('AMPLIFIER_TEST_ARTIFACTS' in r['env'] for r in installed['scenarios'])
    assert all(r['env']['AMPLIFIER_TEST_OUTPUT'] == r['env']['AMPLIFIER_PROOF_DIR']
               for r in source['scenarios'])


def test_manifest_binds_every_static_byte(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'build.json').write_text('{"id":"synthetic"}')
    (tmp_path / 'assets/app.js').write_text('original')
    first = checks.manifest(tmp_path)
    (tmp_path / 'assets/app.js').write_text('changed')
    assert first != checks.manifest(tmp_path)
    assert set(first) == {'build.json', 'assets/app.js'}


def test_installed_failure_is_retained_without_success(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setattr(sys, 'argv', ['compact_composer_checks.py'])
    monkeypatch.setattr(checks, 'OUTPUT', tmp_path / 'evidence')
    monkeypatch.setattr(checks, 'provenance', lambda: {'revision': 'synthetic'})
    monkeypatch.setattr(checks, 'readiness', lambda: None)
    monkeypatch.setattr(checks, 'run_scenarios', lambda *args, **kwargs: 0)
    def broken_install(source):
        raise RuntimeError('Synthetic wheel preparation failure')
    monkeypatch.setattr(checks, 'installed', broken_install)
    with pytest.raises(RuntimeError, match='Synthetic wheel preparation failure'):
        checks.main()
    result = json.loads((checks.OUTPUT / 'result.json').read_text())
    assert result == {'sourceExit': 0, 'installedExit': 1,
                      'installedNotStartedReason': None,
                      'installedError': 'Synthetic wheel preparation failure',
                      'passed': False}
