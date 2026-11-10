"""Stage test owners for installed-wheel browser checks, without backend source."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil


def stage_web_test_harness(root: Path, harness: Path, *, source_helpers=(), python_tests=False,
                           copy_dependencies=False):
    """Keep Node-only source oracles explicit; browsers still load installed assets."""
    ignored = shutil.ignore_patterns('node_modules', '__pycache__', '.pytest_cache')
    if python_tests:
        shutil.copytree(root / 'tests', harness / 'tests', ignore=ignored)
    else:
        for name in ('fixtures', 'web'):
            shutil.copytree(root / 'tests' / name, harness / 'tests' / name, ignore=ignored)
    client = Path('.build/web-client')
    shutil.copytree(root / client / 'tests', harness / client / 'tests', ignore=ignored)
    for name in ('package.json', 'package-lock.json'):
        shutil.copy2(root / client / name, harness / client / name)
    support = {}
    for name in source_helpers:
        relative = client / 'src' / name
        target = harness / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
        expected = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected
        support[relative.as_posix()] = expected
    for owner in (Path('tests/web'), client):
        if copy_dependencies:
            shutil.copytree(root / owner / 'node_modules', harness / owner / 'node_modules')
        else:
            (harness / owner / 'node_modules').symlink_to(
                root / owner / 'node_modules', target_is_directory=True)
    assert not (harness / 'amplifier_web').exists()
    return support
