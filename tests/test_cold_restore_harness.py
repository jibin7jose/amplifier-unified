"""Cold installed checks copy tracked support without inheriting prepared web tools."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cold_restore_checks', ROOT / 'scripts/cold_restore_checks.py')
cold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cold)


def write(root, name, text):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    driver = write(root, 'scripts/cold_restore_checks.py', 'selected driver')
    paths = [driver, write(root, 'tests/test_case.py', 'tracked case'),
             write(root, 'tests/fixtures/state.json', 'synthetic state'),
             write(root, 'tests/web/package.json', 'tracked host tools')]
    write(root, 'amplifier_web/server.py', 'backend must stay installed')
    candidate = {'files': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in paths}}
    monkeypatch.setattr(cold, 'ROOT', root)
    monkeypatch.setattr(cold, '__file__', str(driver))
    return root, candidate


def test_prepared_dependencies_are_excluded_without_losing_tracked_support(source, tmp_path):
    root, candidate = source
    write(root, 'tests/web/node_modules/playwright/index.js', 'installed dependency')
    write(root, 'tests/__pycache__/test_case.pyc', 'compiled cache')
    write(root, 'tests/old.pyc', 'compiled cache')
    harness = tmp_path / 'installed-harness'
    copied = cold.stage_harness(harness, candidate)
    assert copied == candidate['files']
    assert not (harness / 'tests/web/node_modules').exists()
    assert not (harness / 'amplifier_web').exists()
    assert not (harness / 'frontend').exists()
    assert (root / 'tests/web/node_modules/playwright/index.js').read_text() == 'installed dependency'


@pytest.mark.parametrize('change', ['untracked-support', 'changed-tracked-support'])
def test_untracked_or_changed_support_still_refuses_installed_proof(source, tmp_path, change):
    root, candidate = source
    name = 'tests/untracked.py' if change == 'untracked-support' else 'tests/test_case.py'
    write(root, name, 'unqualified support')
    with pytest.raises(AssertionError):
        cold.stage_harness(tmp_path / 'installed-harness', candidate)
