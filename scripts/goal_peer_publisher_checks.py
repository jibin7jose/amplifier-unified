"""Fixed manual repair proof; never a publisher or a general environment runner.

Interface (no path, revision or command overrides): --initialize retains the
candidate before setup; --inputs verifies the workflow's fixed GitHub downloads;
--run restores that lock and executes 43 native + 20 auto cases; --finalize records
workflow status without executing work. --native/--auto are internal child modes.

output/goal-peer-publisher0410/ contains report.json (phase/action status and limits),
candidate.json + candidate-source.tar (all tracked bytes/Git modes), raw API/ZIP
and original/ inputs, dependency-sources.json (checkout hashes, not private code),
resolved-requirements.txt, per-phase process results/commands/logs, collections,
JUnit, traces, and before/after inventories (distribution bytes, sources, imported
module origins, Core binary). A phase passes only with its owned-process receipt.
The recreated installed bytes are recorded, NOT asserted identical to the old
machine: original build-backend inventories and installed binaries were not saved.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import time
import tomllib
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET
import zipfile

# Internal children use -I; only these maintained sibling helpers are added.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_check_runner import run_scenarios
from check_goal_peer_junit import EXPECTED, check as check_native
from release_qualification import core_wheel, files, graph, write

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/goal-peer-publisher0410'
PUBLISHER = '24e1bba76a75a971c56f872f0d2cbac540247540'
ARCHIVE_SHA = '570ffa8de07da1b3d4232f0ba6a7bfec16368dbe787e0990c1dc6287b74c26d1'
INPUTS = {
    'uv.lock': '5e7f786dee894c0365b4442617eb0a7f6890b51cac32720a817e0af3f6fd8948',
    'pyproject.toml': 'a32c8924515dbda5dfcb8142f4d6f28f29d91dad201663817a8f81cc5895c956',
    'build-constraints.txt': 'f2211a5d5985a3193eccbd993f7bad6241a593b2b1014f63ea197729078f15bf',
    'build-identity.json': 'dd75f398c3ebd700ab99dd23f99a2d013744cb4427a173697497c8320f825195',
}
CHECKOUTS = {'loop-live': 'ccc4aa8a0bb0efdbc9e4023c2feab47d19c4bcaf',
             'recipes': '7afaba7455743fb26b8d8a11cd1394eb7a01628a'}
SELECTORS = {'native': ['tests/test_goal_peer_continuation.py', 'tests/test_task_goal_runtime.py'],
             'auto': ['tests/test_peer_auto_delivery.py']}
AUTO_NAMES = {
    'test_default_auto_skips_idle_only_rows_while_manager_busy',
    'test_terminal_event_before_no_effect_ack_reconsiders_same_receipt',
    'test_only_unadmitted_canonical_host_returns_upgrade',
    'test_no_effect_wait_is_not_a_polling_loop',
    'test_busy_selection_work_does_not_grow_with_receipt_history',
} | {f'test_idle_auto_uses_same_id_and_saved_envelope[{v}]'
     for v in ('active', 'blocked', 'completed')} | {
    f'test_native_disposition_beats_lost_ack_and_never_retargets[{v}]'
     for v in ('accepted', 'applied', 'held', 'unknown')} | {
    f'test_native_auto_selects_supported_exact_boundary[{v}]'
    for v in ('none', 'closed-boundary', 'unsupported', 'host-lag', 'stop', 'task', 'completed', 'blocked')}
PHASES = ('inputs', 'prepare', 'dependencies', 'export', 'native', 'auto', 'verify')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, timeout=20).strip()


def source(root):
    assert not git(root, 'status', '--porcelain', '--untracked-files=no'), root
    result = {}
    for entry in git(root, 'ls-files', '--stage', '-z').split('\0'):
        if not entry:
            continue
        info, name = entry.split('\t', 1)
        mode, blob, stage = info.split()
        assert stage == '0' and mode in ('100644', '100755', '120000')
        path = root / name
        data = os.readlink(path).encode() if mode == '120000' else path.read_bytes()
        result[name] = {'mode': mode, 'blob': blob, 'sha256': hashlib.sha256(data).hexdigest()}
    return {'revision': git(root, 'rev-parse', 'HEAD'), 'tree': git(root, 'rev-parse', 'HEAD^{tree}'), 'files': result}


def phase(name, status, **details):
    report = json.loads((OUTPUT / 'report.json').read_text())
    report['phases'][name] = {'status': status, **details}
    write(OUTPUT / 'report.json', report)


def verify_api(run, artifact):
    assert (run['id'], run['run_attempt'], run['head_sha'], run['status'], run['conclusion']) == (
        41179992422, 1, PUBLISHER, 'completed', 'failure')
    assert run['repository']['full_name'] == 'microsoft/amplifier-unified'
    assert run['path'] == '.github/workflows/release.yml' and run['event'] == 'push' and run['head_branch'] == 'main'
    assert (artifact['id'], artifact['name'], artifact['expired'], artifact['digest']) == (
        13033939559, 'runtime-source-resolution', False, 'sha256:' + ARCHIVE_SHA)
    assert artifact['workflow_run']['id'] == run['id'] and artifact['workflow_run']['head_sha'] == PUBLISHER


def inputs():
    verify_api(json.loads((OUTPUT / 'run.json').read_text()), json.loads((OUTPUT / 'artifact.json').read_text()))
    archive = OUTPUT / 'runtime-source-resolution.zip'
    assert archive.stat().st_size < 4 * 1024 * 1024 and sha(archive) == ARCHIVE_SHA
    original = OUTPUT / 'original'
    original.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as stream:
        assert Counter(stream.namelist()) == Counter(INPUTS.keys())
        for entry in stream.infolist():
            assert entry.file_size < 4 * 1024 * 1024
            data = stream.read(entry)
            assert hashlib.sha256(data).hexdigest() == INPUTS[entry.filename]
            (original / entry.filename).write_bytes(data)
    assert files(original, INPUTS) == INPUTS


def clean_environment(environ):
    # Called only after checkout actions finish; never edits their credential state.
    return {key: environ[key] for key in ('PATH', 'LD_LIBRARY_PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'RUNNER_TEMP')
            if key in environ}


def inventory():
    distributions = graph(Path(sys.executable))
    payload = {}
    for dist in metadata.distributions():
        payload[dist.metadata['Name']] = {'root': str(dist.locate_file('')), 'files': {
            str(p): sha(dist.locate_file(p)) for p in dist.files or []
            if p.suffix != '.pyc' and '__pycache__' not in p.parts}}
    origins = {name: {'path': str(Path(m.__file__).resolve()), 'sha256': sha(Path(m.__file__))}
               for name, m in list(sys.modules.items()) if getattr(m, '__file__', None) and Path(m.__file__).is_file()}
    return {'graph': distributions, 'files': payload, 'origins': origins, 'coreWheel': core_wheel(Path(sys.executable)),
            'python': sys.version, 'abi': sysconfig.get_config_var('SOABI'), 'executable': sys.executable,
            'sysPath': sys.path.copy(), 'machine': platform.machine()}


def verify_inventory(value):
    from packaging.requirements import Requirement
    normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
    lock = tomllib.loads((OUTPUT / 'original/uv.lock').read_text())
    locked = {p['name']: p for p in lock['package']}
    local = {p['source']['editable']: p['name'] for p in lock['package'] if 'editable' in p['source']}
    expected = set()
    for line in (OUTPUT / 'resolved-requirements.txt').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        if line.startswith('-e '):
            expected.add(local[line[3:]])
        else:
            requirement = Requirement(line)
            if requirement.marker is None or requirement.marker.evaluate():
                expected.add(normalize(requirement.name))
    actual = [normalize(row['name']) for row in value['graph']]
    assert len(actual) == len(set(actual)) and set(actual) == expected
    for row in value['graph']:
        package = locked[normalize(row['name'])]
        assert row['version'] == package['version'], row
        source, direct = package['source'], row['direct']
        if 'git' in source:
            url = urlsplit(source['git'])
            assert direct['url'] == url._replace(query='', fragment='').geturl()
            assert direct['vcs_info']['commit_id'] == url.fragment and direct['vcs_info']['vcs'] == 'git'
            assert direct.get('subdirectory') == parse_qs(url.query).get('subdirectory', [None])[0]
        elif 'editable' in source:
            assert direct['url'] == Path(source['editable']).as_uri() and direct['dir_info']['editable'] is True
        else:
            assert source == {'registry': 'https://pypi.org/simple'} and direct is None
    candidate = json.loads((OUTPUT / 'candidate.json').read_text())
    checkouts = json.loads((OUTPUT / 'dependency-sources.json').read_text())
    installed = {str((Path(row['root']) / name).resolve()): digest
                 for row in value['files'].values() for name, digest in row['files'].items()}
    for name, origin in value['origins'].items():
        path = Path(origin['path'])
        if path.is_relative_to(ROOT / '.ci'):
            checkout, *relative = path.relative_to(ROOT / '.ci').parts
            assert origin['sha256'] == checkouts[checkout]['files']['/'.join(relative)]['sha256'], name
        elif path.is_relative_to(ROOT):
            assert origin['sha256'] == candidate['files'][str(path.relative_to(ROOT))]['sha256'], name
        elif name.startswith('amplifier_'):
            assert installed.get(str(path)) == origin['sha256'], name
    assert value['coreWheel']['version'] == '2.0.2'


def test_phase(name):
    os.chdir(ROOT)  # Keep process probes rooted in this selected application checkout.
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
    for module in ('amplifier_web.runtime_worker', 'amplifier_web.service', 'amplifier_core', 'amplifier_foundation',
                   'amplifier_module_loop_live.orchestrator', 'amplifier_module_loop_streaming',
                   'amplifier_module_context_simple', 'fixtures.collaboration_native_probe'):
        importlib.import_module(module)  # No importorskip admission.
    import pytest
    expected = Counter((module.replace('.', '/') + '.py::' + case)
                       for module, names in (EXPECTED if name == 'native' else {'tests.test_peer_auto_delivery': AUTO_NAMES}).items()
                       for case in names)
    assert sum(expected.values()) == (43 if name == 'native' else 20)
    class Collection:
        def pytest_collection_finish(self, session):
            nodes = [item.nodeid for item in session.items]
            write(OUTPUT / (name + '-collection.json'), nodes)
            if Counter(nodes) != expected:
                raise pytest.UsageError('Fixed collection differs from the required exact identities')
    before = inventory()
    write(OUTPUT / (name + '-before.json'), before)
    verify_inventory(before)
    if name == 'auto':
        previous = json.loads((OUTPUT / 'native-after.json').read_text())
        assert all(previous[key] == before[key] for key in ('graph', 'files', 'coreWheel'))
    try:
        args = ['-q', '--maxfail=1', '-p', 'no:cacheprovider', '--rootdir=' + str(ROOT),
                '--junitxml=' + str(OUTPUT / (name + '.xml')), *SELECTORS[name]]
        write(OUTPUT / (name + '-pytest.json'), {'call': 'pytest.main', 'args': args})
        code = pytest.main(args, plugins=[Collection()])
    finally:
        after = inventory()
        write(OUTPUT / (name + '-after.json'), after)
        verify_inventory(after)
        assert all(before[key] == after[key] for key in ('graph', 'files', 'coreWheel'))
        assert all(after['origins'].get(key) == origin for key, origin in before['origins'].items())
    assert code == 0
    if name == 'native':
        assert check_native(OUTPUT / 'native.xml') == 43
    else:
        root = ET.parse(OUTPUT / 'auto.xml').getroot()
        assert Counter((c.get('classname'), c.get('name')) for c in root.findall('.//testcase')) == Counter(
            ('tests.test_peer_auto_delivery', case) for case in AUTO_NAMES)
        assert not any(n.tag in ('failure', 'error', 'skipped') for n in root.iter())
        assert all(not int(s.get(k, '0')) for s in root.iter('testsuite') for k in ('failures', 'errors', 'skipped'))


def execute(name, command, seconds):
    phase(name, 'running', command=command, budgetSeconds=seconds)
    plan = {'schemaVersion': 1, 'scenarios': [{'id': name, 'command': command}]}
    code = run_scenarios(ROOT, plan, OUTPUT / name, budget=seconds, case_timeout=seconds,
                         provenance={'candidateSha256': sha(OUTPUT / 'candidate.json'), 'originalInputs': INPUTS})
    result = json.loads((OUTPUT / name / 'results.json').read_text())
    row, = result['scenarios']
    passed = (code == 0 and result['complete'] and result['passed'] and row['status'] == 'passed'
              and row['exitCode'] == 0 and row['reason'] is None
              and row['cleanup'] == {'remaining': [], 'terminated': 0})
    phase(name, 'passed' if passed else 'failed', receipt=name + '/results.json')
    assert passed


def run():
    phase('prepare', 'running')
    assert json.loads((OUTPUT / 'report.json').read_text())['phases']['inputs']['status'] == 'passed'
    assert sys.version_info[:3] == (3, 13, 17) and platform.machine() == 'x86_64'
    assert platform.freedesktop_os_release()['VERSION_ID'] == '24.04'
    uv_version = subprocess.check_output(['uv', '--version'], text=True, timeout=10).strip()
    assert uv_version.split()[1] == '0.13.6'
    write(OUTPUT / 'environment.json', {'python': sys.version, 'abi': sysconfig.get_config_var('SOABI'),
        'uv': uv_version, 'osRelease': platform.freedesktop_os_release(),
        'imageVersion': os.environ.get('ImageVersion'), 'machine': platform.machine()})
    assert source(ROOT) == json.loads((OUTPUT / 'candidate.json').read_text())
    sources = {name: source(ROOT / '.ci' / name) for name in CHECKOUTS}
    write(OUTPUT / 'dependency-sources.json', sources)
    assert all(sources[name]['revision'] == revision for name, revision in CHECKOUTS.items())
    runtime = Path(os.environ['RUNNER_TEMP']) / 'surface-runtime'
    assert not runtime.exists()
    assert {p.name for p in (OUTPUT / 'original').iterdir()} == set(INPUTS)
    shutil.copytree(OUTPUT / 'original', runtime)
    assert files(runtime, INPUTS) == INPUTS
    environment = clean_environment(os.environ)
    os.environ.clear()
    os.environ.update(environment)
    home = runtime / 'home'
    home.mkdir()
    os.environ.update(HOME=str(home), TMPDIR=str(home), UV_CACHE_DIR=str(runtime / 'cache'),
                      GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0',
                      PIP_CONFIG_FILE='/dev/null', PYTHONDONTWRITEBYTECODE='1',
                      UNIFIED_RUNTIME_PYTHON=str(runtime / '.venv/bin/python'), GOAL_PEER_EVIDENCE=str(OUTPUT / 'traces'))
    python = str(runtime / '.venv/bin/python')
    try:
        deadline = time.monotonic() + 900
        for name, command in (
            ('prepare', ['uv', 'sync', '--project', str(runtime), '--locked', '--python', sys.executable]),
            ('dependencies', ['uv', 'pip', 'check', '--python', python]),
            ('export', ['uv', 'export', '--project', str(runtime), '--frozen', '--format', 'requirements-txt',
                        '--no-emit-project', '--no-header', '--no-annotate', '--no-hashes',
                        '--output-file', str(OUTPUT / 'resolved-requirements.txt')]),
        ):
            assert deadline > time.monotonic()
            execute(name, command, deadline - time.monotonic())
            assert files(runtime, INPUTS) == INPUTS and files(OUTPUT / 'original', INPUTS) == INPUTS
        os.environ.update(UV_OFFLINE='1', PIP_NO_INDEX='1')
        deadline = time.monotonic() + 300
        for name in ('native', 'auto'):
            assert deadline > time.monotonic()
            execute(name, [python, '-I', '-B', str(Path(__file__).resolve()), '--' + name], deadline - time.monotonic())
    finally:
        # Preserve attempted installation inputs even when preparation/tests fail.
        phase('verify', 'running')
        retained = OUTPUT / 'restored-inputs'
        retained.mkdir()
        for name in INPUTS:
            if (runtime / name).is_file():
                shutil.copy2(runtime / name, retained / name)
        observed = {name: sha(retained / name) if (retained / name).is_file() else None for name in INPUTS}
        write(OUTPUT / 'restored-input-hashes.json', observed)
        assert observed == INPUTS and files(OUTPUT / 'original', INPUTS) == INPUTS
        assert source(ROOT) == json.loads((OUTPUT / 'candidate.json').read_text())
        assert sources == {name: source(ROOT / '.ci' / name) for name in CHECKOUTS}
        phase('verify', 'passed', originalInputs=INPUTS, candidateUnchanged=True, dependencySourcesUnchanged=True)


def main():
    assert not sys.flags.optimize
    mode, = sys.argv[1:]
    if mode in ('--native', '--auto'):
        assert str(ROOT) == '/home/runner/work/amplifier-unified/amplifier-unified'
        assert sys.executable == os.environ['UNIFIED_RUNTIME_PYTHON'] and os.environ['AMPLIFIER_BROWSER_CHECK_OWNER']
        assert json.loads((OUTPUT / 'report.json').read_text())['phases'][mode[2:]]['status'] == 'running'
        return test_phase(mode[2:])
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    assert str(ROOT) == os.environ['GITHUB_WORKSPACE'] == '/home/runner/work/amplifier-unified/amplifier-unified'
    if mode == '--initialize':
        OUTPUT.mkdir(parents=True, exist_ok=False)
        write(OUTPUT / 'report.json', {'passed': False, 'complete': False, 'fullSuite': False,
            'publisherRun': 41179992422, 'publisherAttempt': 1, 'artifactId': 13033939559,
            'runId': os.environ['GITHUB_RUN_ID'], 'runAttempt': os.environ['GITHUB_RUN_ATTEMPT'],
            'preparationBudgetSeconds': 900, 'combinedTestBudgetSeconds': 300,
            'limits': ['Locked graph and API hash attestation, not bit-identical old machine.',
                       'uv/pip offline flags are not a network sandbox.',
                       'Scripted providers; no full Worker process, installed-package, browser or publication acceptance.'],
            'phases': {name: {'status': 'unrun'} for name in PHASES}})
        candidate = source(ROOT)
        assert candidate['revision'] == os.environ['GITHUB_SHA']
        write(OUTPUT / 'candidate.json', candidate)
        with tarfile.open(OUTPUT / 'candidate-source.tar', 'w') as archive:
            for name in candidate['files']:
                archive.add(ROOT / name, arcname=name, recursive=False)
    elif mode == '--finalize':
        report = json.loads((OUTPUT / 'report.json').read_text())
        report['workflowStatus'] = os.environ['QUALIFICATION_JOB_STATUS']
        report['actionOutcomes'] = json.loads(os.environ['QUALIFICATION_STEP_OUTCOMES'])
        for value in report['phases'].values():
            if value['status'] == 'running':
                value.update(status='unknown', reason='Workflow ended without a settled phase receipt')
        report['complete'] = all(p['status'] == 'passed' for p in report['phases'].values())
        report['passed'] = report['complete'] and report['workflowStatus'] == 'success'
        write(OUTPUT / 'report.json', report)
    else:
        assert mode in ('--inputs', '--run')
        current = 'inputs' if mode == '--inputs' else 'prepare'
        try:
            if mode == '--inputs':
                phase(current, 'running')
                inputs()
                phase(current, 'passed', archiveSha256=ARCHIVE_SHA, originalInputs=INPUTS)
            else:
                run()
        except BaseException as exc:
            report = json.loads((OUTPUT / 'report.json').read_text())
            for name, value in report['phases'].items():
                if value['status'] == 'running':
                    value.update(status='failed', error=type(exc).__name__)
            report['error'] = type(exc).__name__
            write(OUTPUT / 'report.json', report)
            raise


if __name__ == '__main__':
    main()
