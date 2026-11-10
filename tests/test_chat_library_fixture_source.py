"""Browser fixture helper processes must use the selected source checkout."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('fixture_name', ['chat_library_server.py', 'client_paired_server.py'])
def test_fixture_binds_parent_and_child_source_outside_repository(tmp_path, fixture_name):
    root = Path(__file__).resolve().parents[1]
    older = tmp_path / 'older-install'
    package = older / 'amplifier_web'
    package.mkdir(parents=True)
    for name in ('__init__', 'shell_query_worker', 'history_directory_query'):
        (package / f'{name}.py').write_text("__version__ = 'older-fixture'\n")
    extra = tmp_path / 'extra-search-path'
    extra.mkdir()
    (extra / 'retained_dependency.py').write_text('available = True\n')
    outside = tmp_path / 'outside-repository'
    outside.mkdir()
    static = root / 'amplifier_web/static'
    assets = {str(path.relative_to(static)): {'bytes': path.stat().st_size,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in static.rglob('*') if path.is_file()}
    assembly = tmp_path / 'assembly.json'
    assembly.write_text(json.dumps({'server': {'revision': 'probe', 'files': {}, 'version': 'stop-before-app'},
        'client': {'manifest': {'pairedServer': {'revision': 'probe'}, 'assets': assets}}}))
    inherited = os.pathsep.join((str(older), str(extra), os.environ.get('PYTHONPATH', '')))
    probe = '''
import json, os
import amplifier_web, amplifier_web.shell_query_worker, amplifier_web.history_directory_query
import retained_dependency
print(json.dumps({"paths": [amplifier_web.__file__, amplifier_web.shell_query_worker.__file__,
    amplifier_web.history_directory_query.__file__], "pythonpath": os.environ["PYTHONPATH"],
    "dependency": retained_dependency.available}))
'''
    parent = '''
import asyncio, importlib.util, json, runpy, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
before = importlib.util.find_spec("amplifier_web").origin
fixture = runpy.run_path(sys.argv[1])
if "serve" in fixture:
    # Stop at the version check before the fixture starts an application.
    serve = fixture["serve"]
    try:
        asyncio.run(serve(SimpleNamespace(server_root=Path(sys.argv[1]).parents[2],
            assembly_record=Path(sys.argv[3]))))
    except ValueError as error:
        assert str(error) == "Actual server version differs from the recorded source"
    else:
        raise AssertionError("Source probe unexpectedly started the application")
exec(sys.argv[2])
print(subprocess.check_output([sys.executable, "-c", sys.argv[2]], text=True).strip())
print(json.dumps({"before": before}))
'''
    result = subprocess.run(
        [sys.executable, '-c', parent, str(root / 'tests/fixtures' / fixture_name), probe, str(assembly)],
        cwd=outside, env={**os.environ, 'PYTHONPATH': inherited},
        capture_output=True, text=True, check=True, timeout=30,
    )
    current, child, before = map(json.loads, result.stdout.splitlines())
    assert Path(before['before']) == package / '__init__.py'
    expected = [root / 'amplifier_web' / f'{name}.py'
                for name in ('__init__', 'shell_query_worker', 'history_directory_query')]
    assert [list(map(Path, observed['paths'])) for observed in (current, child)] == [expected, expected]
    for observed in (current, child):
        assert observed['pythonpath'] == str(root) + os.pathsep + inherited
        assert observed['dependency'] is True
