"""Selected historical checkouts retain their own explicit web build commands."""
import importlib.util
from pathlib import Path
import sys

import pytest

SPEC = importlib.util.spec_from_file_location('historical_web_layout', Path(__file__).parents[1] / 'scripts/historical_web_layout.py')
layout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layout)


def touch(root, name):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{}')


def selected(root):
    for name in ('tests/web/package.json', 'components/web-client.source.json', 'scripts/web_client_build.py'):
        touch(root, name)


def test_current_checks_have_one_canonical_and_one_host_test_owner(tmp_path):
    selected(tmp_path)
    assert layout.layout(tmp_path) == 'independent-client'
    assert layout.commands(tmp_path, 'prepare') == [[sys.executable, 'scripts/web_client_build.py', 'prepare']]
    assert layout.commands(tmp_path, 'validate') == [
        ['npm', 'ci', '--prefix', '.build/web-client'], ['npm', 'ci', '--prefix', 'tests/web'],
        ['npm', 'test', '--prefix', '.build/web-client'], ['npm', 'test', '--prefix', 'tests/web'],
        [sys.executable, 'scripts/web_client_build.py', 'build'],
        ['npm', 'run', 'test:artifact-assets', '--prefix', '.build/web-client'],
    ]
    assert layout.environment_directories(tmp_path) == (tmp_path, tmp_path / 'tests/web', tmp_path / '.build/web-client')


def test_historical_layouts_do_not_gain_current_source_or_test_owners(tmp_path):
    touch(tmp_path, 'frontend/package.json')
    assert layout.layout(tmp_path) == 'historical-bundled'
    assert layout.commands(tmp_path, 'prepare') == []
    assert layout.commands(tmp_path, 'validate') == [
        ['npm', 'ci', '--prefix', 'frontend'], ['npm', 'test', '--prefix', 'frontend'],
        ['npm', 'run', 'build', '--prefix', 'frontend'],
    ]
    for name in ('components/web-client.source.json', 'scripts/web_client_build.py', 'scripts/web_client_development.py'):
        touch(tmp_path, name)
    assert layout.layout(tmp_path) == 'historical-component-view'
    assert layout.commands(tmp_path, 'prepare') == [[sys.executable, 'scripts/web_client_development.py', 'prepare-view']]
    assert layout.environment_directories(tmp_path) == (tmp_path, tmp_path / 'frontend', tmp_path / 'frontend/.client')


@pytest.mark.parametrize('files', [[], ['tests/web/package.json'], ['frontend/package.json', 'components/web-client.source.json']])
def test_unknown_or_incomplete_layout_fails_without_creating_paths(tmp_path, files):
    for name in files:
        touch(tmp_path, name)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*'))
    with pytest.raises(ValueError):
        layout.commands(tmp_path, 'prepare')
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*')) == before


def test_cli_cache_keys_and_order_use_selected_checkout(tmp_path, monkeypatch, capsys):
    selected(tmp_path)
    output = tmp_path / 'github-output'
    layout.main(['describe', '--root', str(tmp_path), '--output', str(output)])
    assert 'independent-client' in capsys.readouterr().out
    assert output.read_text() == 'cache-files<<WEB_CLIENT_LAYOUT\ntests/web/package-lock.json\ncomponents/web-client.source.json\nWEB_CLIENT_LAYOUT\n'
    calls = []
    monkeypatch.setattr(layout.subprocess, 'run', lambda command, **kw: calls.append((command, kw)))
    layout.main(['validate', '--root', str(tmp_path)])
    assert calls == [(command, {'cwd': tmp_path, 'check': True}) for command in layout.commands(tmp_path, 'validate')]
