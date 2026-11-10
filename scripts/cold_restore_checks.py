"""Fixed manual cold-control restore proof, not full-suite or frontend qualification.

Requires committed canonical assets (prepared/qualified separately by Fast release
checks). No live-host dependencies, paid providers, arbitrary commands or retries.
"""
from __future__ import annotations

import base64
from collections import Counter
import csv
import hashlib
import importlib
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/cold-control-restore'
SOURCE_SELECTORS = [
    'tests/test_assignment_task_limits.py',
    'tests/test_assignment_task_limits_native.py::test_native_exhausted_member_receives_fresh_work_after_manager_control',
    'tests/test_capacity.py::test_running_cas_duplicate_restart_ui_agent_parity_and_wrong_session',
    'tests/test_runtime_controls.py::test_cancelled_restore_retains_controls_and_releases_persistence_guard',
]
INSTALLED_SELECTORS = [
    'tests/test_assignment_task_limits.py::test_cold_limit_correction_preserves_saved_budget_at_every_write',
    'tests/test_assignment_task_limits.py::test_failed_cold_limit_preparation_keeps_previously_saved_mode',
    'tests/test_runtime_controls.py::test_cancelled_restore_retains_controls_and_releases_persistence_guard[False]',
    'tests/test_runtime_controls.py::test_cancelled_restore_retains_controls_and_releases_persistence_guard[True]',
]
NOT_SELECTED = 'tests/test_runtime_controls.py::test_real_core_controls_and_tool_approval'
MODULES = ('amplifier_web.runtime_controls', 'amplifier_web.assignment_task_limits',
           'amplifier_web.task_continuity', 'amplifier_web.management', 'amplifier_web.service',
           'amplifier_web.capacity', 'amplifier_core', 'amplifier_foundation',
           'amplifier_module_loop_live.orchestrator', 'amplifier_module_context_simple')


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args, root=ROOT):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, timeout=10).strip()


def manifest(folder):
    paths = sorted(folder.rglob('*'))
    assert not any(p.is_symlink() for p in paths), folder
    return {p.relative_to(folder).as_posix(): sha(p) for p in paths
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def verify_wheel(wheel, candidate, package_root=None):
    """Reconcile every RECORD entry, committed module/asset and installed byte."""
    project = tomllib.loads((Path(candidate['root']) / 'pyproject.toml').read_text())
    build = project['tool']['hatch']['build']['targets']['wheel']
    prefixes = tuple(p + '/' for p in build['packages'])
    source_files = candidate['files']
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        record, = [name for name in names if name.endswith('.dist-info/RECORD')]
        entries = list(csv.reader(io.StringIO(archive.read(record).decode())))
        assert len(entries) == len(names) and {row[0] for row in entries} == set(names)
        required = {p for p in source_files if p.startswith(prefixes)
                    and (p.endswith('.py') or p.startswith('amplifier_web/static/'))}
        assert required <= set(names), sorted(required - set(names))
        assets, payload = {}, {}
        for path, encoded, size in entries:
            assert not Path(path).is_absolute() and '..' not in Path(path).parts
            if path == record:
                assert not encoded and not size
                continue
            data = archive.read(path)
            digest = hashlib.sha256(data).hexdigest()
            assert encoded == 'sha256=' + base64.urlsafe_b64encode(bytes.fromhex(digest)).decode().rstrip('=')
            assert int(size) == len(data), path
            payload[path] = digest
            if path.startswith(prefixes):
                source = path
                for original, target in build.get('force-include', {}).items():
                    if path == target or path.startswith(target + '/'):
                        source = original + path[len(target):]
                        break
                assert source_files.get(source) == digest, path
            if path.startswith('amplifier_web/static/'):
                assets[path] = digest
            if package_root is not None:
                assert sha(package_root / path) == digest, path
        assert assets == candidate['static'], 'Wheel must contain exactly the committed assets'
        if package_root is not None:
            installed_record = list(csv.reader(io.StringIO((package_root / record).read_text())))
            installed = {row[0]: row[1:] for row in installed_record}
            assert len(installed) == len(installed_record)
            for path, encoded, size in entries:
                if path != record:
                    assert installed[path] == [encoded, size], path
    return {'path': str(wheel), 'sha256': sha(wheel), 'recordEntries': len(entries),
            'payload': payload, 'staticFiles': len(assets)}


def pytest_process(installed):
    """Record import identity in the very process that executes pytest.main."""
    assert sys.version_info[:2] == (3, 13) and not sys.flags.optimize
    evidence = Path(os.environ['COLD_RESTORE_EVIDENCE'])
    candidate = json.loads((evidence / 'candidate.json').read_text())
    source = Path(candidate['root'])
    selectors = INSTALLED_SELECTORS if installed else SOURCE_SELECTORS
    phase, expected = ('installed', 4) if installed else ('source', 34)
    # -I ignores ambient Python paths. Only the copied helpers are added for the
    # installed run; never the source checkout or an editable application.
    sys.path.insert(0, str(ROOT / 'tests'))
    if not installed:
        sys.path.insert(0, str(ROOT))
    for name in MODULES:
        importlib.import_module(name)  # Missing real loop/context is a failure, not an importorskip pass.
    packages = candidate['packages']
    package_root = Path(importlib.import_module('amplifier_web').__file__).resolve().parent.parent

    def origins():
        result = {}
        for name, module in tuple(sys.modules.items()):
            file = getattr(module, '__file__', None)
            if not file:
                continue
            path = Path(file).resolve()
            if installed:
                assert not path.is_relative_to(source), (name, path)
            if name.split('.')[0] in packages:
                relative = path.relative_to(package_root).as_posix()
                assert sha(path) == candidate['files'][relative], (name, path)
                result[name] = {'file': str(path), 'sha256': sha(path)}
        if installed:
            assert not Path.cwd().is_relative_to(source)
            assert all(not Path(p).resolve().is_relative_to(source) for p in sys.path)
            assert package_root.is_relative_to(Path(sys.prefix)) and 'site-packages' in package_root.parts
        return result

    if installed:
        dist = metadata.distribution('amplifier-unified')
        direct = json.loads(dist.read_text('direct_url.json'))
        assert not direct.get('dir_info', {}).get('editable')
        wheel = Path(candidate['wheel'])
        assert direct['url'] == wheel.as_uri()
        save(evidence / 'installed-wheel.json', verify_wheel(wheel, candidate, package_root))
    record = {'python': sys.version, 'executable': sys.executable, 'prefix': sys.prefix,
              'cwd': str(Path.cwd()), 'sysPath': sys.path.copy(), 'PYTHONPATH': os.environ.get('PYTHONPATH'),
              'modules': origins(), 'dependencies': {
                  name: {'file': str(Path(importlib.import_module(name).__file__).resolve()),
                         'sha256': sha(Path(importlib.import_module(name).__file__).resolve())}
                  for name in MODULES if name.split('.')[0] not in packages},
              'distributions': sorted([{'name': d.metadata['Name'], 'version': d.version,
                  'direct': json.loads(d.read_text('direct_url.json') or 'null')}
                  for d in metadata.distributions()], key=lambda d: d['name'])}
    save(evidence / (phase + '-imports-before.json'), record)
    import pytest

    class Collected:
        nodes = []

        def pytest_collection_finish(self, session):
            self.nodes = [item.nodeid for item in session.items]
            save(evidence / (phase + '-collection.json'), self.nodes)
            assert len(self.nodes) == expected and len(set(self.nodes)) == expected
            assert all(any(node == s or node.startswith(s + '::') or node.startswith(s + '[')
                           for s in selectors) for node in self.nodes), self.nodes
            assert NOT_SELECTED not in self.nodes

    collected = Collected()
    args = ['-c', '/dev/null', '-p', 'no:cacheprovider', '--rootdir=' + str(ROOT), '--asyncio-mode=auto',
            '--import-mode=importlib', '-v', '--tb=short',
            '--junitxml=' + str(evidence / (phase + '.xml')), *[str(ROOT / s) for s in selectors]]
    save(evidence / (phase + '-pytest-command.json'), {'call': 'pytest.main', 'args': args})
    code = pytest.main(args, plugins=[collected])
    save(evidence / (phase + '-imports-after.json'), {'modules': origins(), 'sysPath': sys.path.copy()})
    if installed:
        verify_wheel(wheel, candidate, package_root)
    raise SystemExit(code)


def junit(evidence, phase, expected):
    root = ET.parse(evidence / (phase + '.xml')).getroot()
    cases = root.findall('.//testcase')
    nodes = json.loads((evidence / (phase + '-collection.json')).read_text())
    counts = {'tests': len(cases), **{tag: len(root.findall('.//' + tag))
                                    for tag in ('failure', 'error', 'skipped')}}
    save(evidence / (phase + '-counts.json'), {'counts': counts, 'nodeids': nodes})
    assert counts == {'tests': expected, 'failure': 0, 'error': 0, 'skipped': 0}, counts
    assert Counter(c.attrib['name'] for c in cases) == Counter(n.rsplit('::', 1)[1] for n in nodes)
    return counts


def main():
    from browser_check_runner import run_scenarios
    assert sys.version_info[:2] == (3, 13) and not sys.flags.optimize
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    OUTPUT.mkdir(parents=True, exist_ok=False)
    work = Path(tempfile.mkdtemp(prefix='cold-control-restore-', dir=os.environ['RUNNER_TEMP'])).resolve()
    assert not work.is_relative_to(ROOT)
    started = time.monotonic()
    report = {'passed': False, 'complete': False, 'normalBudgetSeconds': 300, 'maximumSeconds': 600,
              'sourceSelectors': SOURCE_SELECTORS, 'installedSelectors': INSTALLED_SELECTORS,
              'notSelected': [NOT_SELECTED], 'limits': [
                  'Scripted providers only; no paid-provider or full worker-transport acceptance.',
                  'Canonical assets must be committed before admission; Fast owns frontend build/tests/parity.',
                  'The separate opt-in Core controls probe is not selected and is not counted as passed.']}
    save(OUTPUT / 'report.json', report)
    def expired(*_):
        raise TimeoutError('Cold control restore exceeded its fixed 600 second ceiling')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(600)
    try:
        revision = git('rev-parse', 'HEAD')
        assert revision == os.environ['GITHUB_SHA']
        assert not git('status', '--porcelain', '--untracked-files=no')
        files = git('ls-files', '-z').split('\0')
        files = [p for p in files if p]
        assert all((ROOT / p).is_file() and not (ROOT / p).is_symlink() for p in files)
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        loop, = [p for p in tomllib.loads((ROOT / 'uv.lock').read_text())['package']
                 if p['name'] == 'amplifier-module-loop-live']
        assert git('rev-parse', 'HEAD', root=ROOT / '.ci/loop-live') == loop['source']['git'].rsplit('#', 1)[1]
        wheel = OUTPUT / 'wheel' / ('amplifier_unified-' + project['project']['version'] + '-py3-none-any.whl')
        candidate = {'root': str(ROOT), 'revision': revision, 'tree': git('rev-parse', 'HEAD^{tree}'),
                     'files': {p: sha(ROOT / p) for p in files}, 'loopSource': loop['source'],
                     'packages': project['tool']['hatch']['build']['targets']['wheel']['packages'],
                     'wheel': str(wheel), 'runId': os.environ['GITHUB_RUN_ID'],
                     'runAttempt': os.environ['GITHUB_RUN_ATTEMPT']}
        candidate['static'] = {p: value for p, value in candidate['files'].items()
                               if p.startswith('amplifier_web/static/')}
        assert candidate['static'] and {('amplifier_web/static/' + p): value
            for p, value in manifest(ROOT / 'amplifier_web/static').items()} == candidate['static']
        build = json.loads((ROOT / 'amplifier_web/static/build.json').read_text())
        component = json.loads((ROOT / 'amplifier_web/static/client-component.json').read_text())
        selection = json.loads((ROOT / 'components/web-client.source.json').read_text())
        assert component['build'] == build and component['source'] == selection['source'], \
            'Commit the selected client artifact first'
        save(OUTPUT / 'candidate.json', candidate)
        # Do not inherit developer credentials, environment overrides or host packages.
        keep = ('PATH', 'LD_LIBRARY_PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'SSL_CERT_FILE',
                'GITHUB_ACTIONS', 'GITHUB_EVENT_NAME', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'RUNNER_TEMP')
        retained = {k: os.environ[k] for k in keep if k in os.environ}
        os.environ.clear()
        os.environ.update(retained)
        for folder in ('home', 'tmp', 'harness/frontend'):
            (work / folder).mkdir(parents=True)
        host, installed = work / 'host/bin/python', work / 'installed/bin/python'
        harness = work / 'harness'
        shutil.copytree(ROOT / 'tests', harness / 'tests', symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (harness / 'scripts').mkdir()
        shutil.copy2(Path(__file__), harness / 'scripts/cold_restore_checks.py')
        copied = manifest(harness)
        assert copied and all(candidate['files'].get(p) == value for p, value in copied.items())
        save(OUTPUT / 'harness.json', {'root': str(harness), 'files': copied})
        common = {'UV_PROJECT_ENVIRONMENT': str(work / 'host'), 'UV_NO_BUILD_PACKAGE': 'amplifier-core',
                  'UV_CACHE_DIR': str(work / 'uv-cache'), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                  'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'url.' + (ROOT / '.ci/loop-live').as_uri() + '.insteadOf',
                  'GIT_CONFIG_VALUE_0': 'https://github.com/microsoft/amplifier-module-loop-live',
                  'HOME': str(work / 'home'), 'TMPDIR': str(work / 'tmp'), 'PYTHONPATH': '',
                  'COLD_RESTORE_EVIDENCE': str(OUTPUT)}
        plans = [
            ('locked-host', 180, ['uv', 'sync', '--project', str(ROOT), '--locked', '--group', 'dev', '--group', 'artifacts', '--no-install-project', '--python', sys.executable], ROOT),
            ('source-dependencies', 15, ['uv', 'pip', 'check', '--python', str(host)], ROOT),
            ('source-freeze', 15, ['uv', '--quiet', 'pip', 'freeze', '--python', str(host)], ROOT),
            ('source', 120, [str(host), '-I', str(ROOT / 'scripts/cold_restore_checks.py'), '--source-tests'], ROOT),
            ('wheel', 30, ['uv', 'build', '--wheel', '--no-build-isolation', '--python', str(host), '--out-dir', str(wheel.parent), str(ROOT)], ROOT),
            ('fresh-runtime', 15, ['uv', 'venv', '--python', sys.executable, str(work / 'installed')], ROOT),
            ('install-dependencies', 120, ['uv', 'pip', 'install', '--python', str(installed), '--no-deps', '-r', str(OUTPUT / 'requirements.txt')], ROOT),
            ('install-wheel', 20, ['uv', 'pip', 'install', '--python', str(installed), '--no-deps', str(wheel)], ROOT),
            ('installed-dependencies', 15, ['uv', 'pip', 'check', '--python', str(installed)], ROOT),
            ('installed-freeze', 15, ['uv', '--quiet', 'pip', 'freeze', '--python', str(installed)], ROOT),
            ('installed', 90, [str(installed), '-I', str(harness / 'scripts/cold_restore_checks.py'), '--installed-tests'], harness),
        ]
        save(OUTPUT / 'plan.json', [{'id': n, 'timeoutSeconds': cap, 'command': cmd, 'env': common,
                                   'cwd': str(root / 'frontend')} for n, cap, cmd, root in plans])
        report['phases'] = {name: {'status': 'not_run'} for name, *_ in plans}
        save(OUTPUT / 'report.json', report)
        for name, cap, command, root in plans:
            remaining = 600 - (time.monotonic() - started)
            assert remaining > 1, 'Overall budget exhausted'
            report['phases'][name] = {'status': 'running'}
            save(OUTPUT / 'report.json', report)
            plan = {'schemaVersion': 1, 'scenarios': [{'id': name, 'command': command, 'env': common}]}
            code = run_scenarios(root, plan, OUTPUT / name, budget=min(cap, remaining), case_timeout=cap,
                                 provenance={'revision': revision, 'candidateSha256': sha(OUTPUT / 'candidate.json')})
            result = json.loads((OUTPUT / name / 'results.json').read_text())
            row, = result['scenarios']
            report['phases'][name] = row
            save(OUTPUT / 'report.json', report)
            # Preserve even failing JUnit counts before rejecting a process result.
            if name in ('source', 'installed'):
                report[name + 'Counts'] = junit(OUTPUT, name, 34 if name == 'source' else 4)
            assert code == 0 and result['complete'] and result['passed'] and row['status'] == 'passed', name
            assert row['exitCode'] == 0 and row['reason'] is None and row['cleanup'] == {'remaining': [], 'terminated': 0}, name
            log = OUTPUT / name / (name + '.log')
            if name in ('source-freeze', 'installed-freeze'):
                lines = log.read_text().splitlines()
                if name == 'installed-freeze':
                    app, = [line for line in lines if line.startswith('amplifier-unified @ ')]
                    assert app == 'amplifier-unified @ ' + wheel.as_uri()
                    lines.remove(app)
                assert lines and all(re.fullmatch(r'[A-Za-z0-9_.-]+==[^\s]+', line)
                    or re.fullmatch(r'[A-Za-z0-9_.-]+ @ git\+https://[^\s]+@[0-9a-f]{40}(?:#[^\s]+)?', line)
                    for line in lines), 'Frozen dependencies must be exact, noneditable versions/commits'
                if name == 'source-freeze':
                    (OUTPUT / 'requirements.txt').write_text('\n'.join(lines) + '\n')
                else:
                    assert sorted(lines) == sorted((OUTPUT / 'requirements.txt').read_text().splitlines())
            elif name == 'wheel':
                assert list(wheel.parent.glob('*.whl')) == [wheel], 'Build exactly one wheel'
                report['wheel'] = verify_wheel(wheel, candidate)
                save(OUTPUT / 'wheel.json', report['wheel'])
        assert git('rev-parse', 'HEAD') == revision and not git('status', '--porcelain', '--untracked-files=no')
        assert candidate['files'] == {p: sha(ROOT / p) for p in files}, 'Candidate inputs changed'
        assert manifest(harness) == copied, 'Copied harness changed'
        assert sha(wheel) == report['wheel']['sha256'], 'Built wheel changed'
        report.update(passed=True, complete=True)
    except BaseException as exc:
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        if 'name' in locals() and name in report.get('phases', {}):
            report['phases'][name].update(status='failed', verificationFailure=str(exc))
        raise
    finally:
        signal.alarm(0)
        report['durationSeconds'] = round(time.monotonic() - started, 3)
        report['workDirectory'] = str(work)
        # The disposable CI runner owns filesystem retirement, not a shared host.
        save(OUTPUT / 'report.json', report)


if __name__ == '__main__':
    if sys.argv[1:] in (['--source-tests'], ['--installed-tests']):
        pytest_process(sys.argv[1] == '--installed-tests')
    else:
        assert not sys.argv[1:], 'No arbitrary selection, budget or command overrides'
        main()
