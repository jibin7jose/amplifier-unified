"""Installed browser harnesses retain test owners without backend source."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('web_test_harness', ROOT / 'scripts/web_test_harness.py')
harnesses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harnesses)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    for name in ('tests/fixtures/server.py', 'tests/web/journey.mjs', 'tests/test_support.py',
                 'tests/web/package.json', '.build/web-client/tests/oracle.mjs',
                 '.build/web-client/package.json', '.build/web-client/package-lock.json',
                 '.build/web-client/src/selected.js', '.build/web-client/src/unselected.js',
                 'amplifier_web/server.py', 'tests/web/node_modules/tool/package.json',
                 '.build/web-client/node_modules/client/package.json'):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(name)
    return root


@pytest.mark.parametrize('copy_dependencies', [False, True])
@pytest.mark.parametrize('python_tests', [False, True])
def test_installed_harness_owns_exact_oracles_and_separate_dependency_closures(
        source, tmp_path, copy_dependencies, python_tests):
    target = tmp_path / 'harness'
    proof = harnesses.stage_web_test_harness(source, target, source_helpers=('selected.js',),
                                            copy_dependencies=copy_dependencies, python_tests=python_tests)
    assert proof == {'.build/web-client/src/selected.js': hashlib.sha256(
        (source / '.build/web-client/src/selected.js').read_bytes()).hexdigest()}
    assert not (target / 'amplifier_web').exists()
    assert not (target / '.build/web-client/src/unselected.js').exists()
    assert (target / 'tests/test_support.py').exists() is python_tests
    for owner, package in [('tests/web', 'tool'), ('.build/web-client', 'client')]:
        dependencies = target / owner / 'node_modules'
        assert dependencies.is_symlink() is not copy_dependencies
        assert (dependencies / package / 'package.json').read_bytes() == (
            source / owner / 'node_modules' / package / 'package.json').read_bytes()
    assert (target / 'tests/fixtures/server.py').read_bytes() == (source / 'tests/fixtures/server.py').read_bytes()


def test_missing_oracle_and_reused_harness_are_errors(source, tmp_path):
    target = tmp_path / 'harness'
    with pytest.raises(FileNotFoundError):
        harnesses.stage_web_test_harness(source, target, source_helpers=('missing.js',))
    retained = target / 'tests/web/journey.mjs'
    original = retained.read_bytes()
    with pytest.raises(FileExistsError):
        harnesses.stage_web_test_harness(source, target)
    assert retained.read_bytes() == original
