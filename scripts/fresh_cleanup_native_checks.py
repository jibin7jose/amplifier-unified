"""Fixed, test-only native cleanup qualification on a disposable hosted runner."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/fresh-cleanup-native'
SOURCE_FILES = (
    'amplifier_web/task_environments.py',
    'amplifier_web/task_resources.py',
    'amplifier_web/test_environment_fresh_cleanup.py',
    'amplifier_web/test_environment_recovery.py',
    'amplifier_web/test_environment_worker.py',
    'amplifier_web/test_environment_configs.py',
    'amplifier_web/test_environment_profiles.py',
    'amplifier_web/test_job_transport.py',
    'amplifier_web/test_source_transfer.py',
    'amplifier_web/environment_retention.py',
    'amplifier_web/test_host_resources.py',
    'amplifier_web/managed_runner_updates.py',
    'amplifier_web/managed_runner_binding.py',
    'tests/test_environment_fresh_cleanup_native.py',
    'scripts/fresh_cleanup_native_checks.py',
    '.github/workflows/python-checks.yml',
    'pyproject.toml', 'uv.lock',
)


def run(args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(args, cwd=ROOT, check=True, timeout=kwargs.pop('timeout', 180), **kwargs)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def record(name, value):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def constants():
    result = {}
    for node in ast.parse((ROOT / 'amplifier_web/task_environments.py').read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {'DTU_COMMIT', 'APPROVED_IMAGE'}:
                result[node.targets[0].id] = ast.literal_eval(node.value)
    return result


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    expected = os.environ['GITHUB_SHA']
    assert git('rev-parse', 'HEAD') == expected
    assert os.environ['GITHUB_ACTIONS'] == 'true'
    assert os.environ['CHECK_SUITE'] == 'fresh-cleanup-native'
    assert sys.platform == 'linux'
    root = Path(os.environ['CLEANUP_NATIVE_ROOT']).resolve()
    assert root == Path(os.environ['RUNNER_TEMP']).resolve() / 'unified-cleanup-native'
    root.mkdir(mode=0o700)
    fixed = constants()
    core = ROOT / '.ci/dtu-core'
    assert git('-C', str(core), 'rev-parse', 'HEAD') == fixed['DTU_COMMIT']
    record('source.json', {'revision': expected, 'tree': git('rev-parse', 'HEAD^{tree}'),
        'coreCommit': fixed['DTU_COMMIT'], 'image': fixed['APPROVED_IMAGE'],
        'runId': os.environ['GITHUB_RUN_ID'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
        'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES},
        'coreLockSha256': hashlib.sha256((core / 'uv.lock').read_bytes()).hexdigest(),
        'native': 'unrun'})
    run(['docker', 'version'])
    run(['docker', 'compose', 'version'])
    # App and managed core retain independent locked environments, as in product.
    run(['uv', 'sync', '--locked', '--no-dev'], timeout=600)
    packages = tomllib.loads((ROOT / 'uv.lock').read_text())['package']
    names = ('pytest', 'pytest-asyncio', 'iniconfig', 'pluggy', 'pygments')
    requirements = []
    for name in names:
        entry, = [row for row in packages if row['name'] == name]
        requirements.append(name + '==' + entry['version'])
    app_python = ROOT / '.venv/bin/python'
    run(['uv', 'pip', 'install', '--python', str(app_python), '--no-deps', *requirements], timeout=180)
    identity = hashlib.sha256(fixed['DTU_COMMIT'].encode()).hexdigest()[:24]
    core_environment = root / 'app/smart-tools/installs' / identity / 'venv'
    run(['uv', 'sync', '--locked', '--no-dev', '--no-default-groups', '--project', str(core)],
        env={**os.environ, 'UV_PROJECT_ENVIRONMENT': str(core_environment)}, timeout=600)
    for name, python in [('app', app_python), ('core', core_environment / 'bin/python')]:
        code = ('import importlib.metadata as m,json; print(json.dumps(sorted('
                '[{"name":d.metadata["Name"],"version":d.version,"source":json.loads(d.read_text("direct_url.json") or "null")} '
                'for d in m.distributions()],key=lambda x:x["name"]),indent=2))')
        with (OUTPUT / (name + '-dependencies.json')).open('w') as stream:
            run([str(python), '-I', '-c', code], stdout=stream)
    run(['docker', 'pull', fixed['APPROVED_IMAGE']], timeout=180)
    result = subprocess.run([str(app_python), '-m', 'pytest', '-v', '--tb=short',
        'tests/test_environment_fresh_cleanup_native.py', '--junitxml=' + str(OUTPUT / 'junit.xml')],
        cwd=ROOT, timeout=600)
    cases = ET.parse(OUTPUT / 'junit.xml').getroot().findall('.//testcase')
    passed = (result.returncode == 0 and len(cases) == 1
              and all(not any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for case in cases))
    record('result.json', {'source': expected, 'returncode': result.returncode,
        'cases': len(cases), 'passed': passed})
    assert passed, 'Native cleanup qualification failed or did not execute its one fixed case'


if __name__ == '__main__':
    main()
