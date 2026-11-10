"""Deterministic adapter contracts; no Docker, network, install or desktop calls.

These checks do not replace the manual installed-wheel qualification. They are
safe to run in its owned preparation environment before admitting Docker work.
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('default_installed_adapter', ROOT / 'scripts/qualify_default_installed.py')
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def fake_wheel(path, name, requirements=(), payload=None):
    with zipfile.ZipFile(path, 'w') as archive:
        info = 'Name: ' + name + '\nVersion: 1.0\n'
        info += ''.join('Requires-Dist: ' + row + '\n' for row in requirements)
        archive.writestr(name + '-1.0.dist-info/METADATA', info)
        for relative, data in (payload or {}).items():
            archive.writestr(relative, data)
    return path


def test_unselected_direct_extras_do_not_require_unrelated_packages(tmp_path):
    wheel = fake_wheel(tmp_path / 'host.whl', 'amplifier-unified', [
        'amplifier-foundation @ https://example.invalid/foundation',
        'amplifier-app-tui @ https://example.invalid/tui ; extra == "tui"'])
    adapter.check_direct_requirements([wheel], ['amplifier-unified', 'amplifier-foundation'], '3.13.16')


def test_selected_ci_client_direct_dependency_must_be_pinned(tmp_path):
    wheel = fake_wheel(tmp_path / 'ci.whl', 'amplifier-bundle-context-intelligence', [
        'missing-library @ https://example.invalid/library ; extra == "client"'])
    with pytest.raises(ValueError, match='Undeclared active'):
        adapter.check_direct_requirements([wheel], ['amplifier-bundle-context-intelligence'], '3.13.16')


def test_source_inventory_checks_links_with_explicit_owned_boundary(tmp_path):
    root = tmp_path / '.venv'
    root.mkdir()
    python = tmp_path / 'python'
    python.write_text('fake interpreter bytes')
    (root / 'python').symlink_to(python)
    with pytest.raises(ValueError, match='symlink escapes'):
        adapter.inventory(root)
    assert adapter.inventory(root, boundary=tmp_path)['python'] == {'link': str(python)}


@pytest.mark.parametrize('drift', ['changed', 'missing', 'extra'])
def test_packaged_payload_must_equal_source_and_resources(tmp_path, drift):
    source = tmp_path / 'candidate'
    source.mkdir()
    (source / 'pkg').mkdir()
    (source / 'pkg/__init__.py').write_text('value=1\n')
    (source / 'resource.txt').write_text('resource\n')
    (source / 'pyproject.toml').write_text('''[tool.hatch.build.targets.wheel]
packages = ["pkg"]
[tool.hatch.build.targets.wheel.force-include]
"resource.txt" = "pkg/resource.txt"
''')
    payload = {'pkg/__init__.py': 'value=1\n', 'pkg/resource.txt': 'resource\n'}
    wheel = fake_wheel(tmp_path / 'good.whl', 'example', payload=payload)
    assert adapter.verify_candidate_wheel(wheel, source)['pkg/resource.txt'] == adapter.digest(source / 'resource.txt')
    if drift == 'changed':
        payload['pkg/resource.txt'] = 'wrong\n'
    elif drift == 'missing':
        payload.pop('pkg/resource.txt')
    else:
        payload['pkg/unexpected.bin'] = 'extra'
    wheel = fake_wheel(tmp_path / 'bad.whl', 'example', payload=payload)
    with pytest.raises(ValueError, match='payload differs'):
        adapter.verify_candidate_wheel(wheel, source)


def test_pins_require_exact_sources(tmp_path):
    data = json.loads((ROOT / 'scripts/default-installed-pins.json').read_text())
    assert adapter.pins(ROOT / 'scripts/default-installed-pins.json')['core']['version'] == '2.0.2'
    data['repositories'][0]['commit'] = 'main'
    path = tmp_path / 'pins.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Exact repository'):
        adapter.pins(path)


def test_environment_contains_no_caller_credentials(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-must-not-leak')
    monkeypatch.setenv('SSH_AUTH_SOCK', '/synthetic/socket')
    monkeypatch.setenv('DISPLAY', ':7')
    env = adapter.clean_env()
    assert not {'OPENAI_API_KEY', 'SSH_AUTH_SOCK', 'DISPLAY', 'PYTHONPATH'} & env.keys()
    assert env['GIT_CONFIG_GLOBAL'] == '/dev/null'


def test_host_refusal_happens_before_any_execution(monkeypatch, tmp_path):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setattr(adapter, 'command', lambda *a, **k: pytest.fail('No process may start'))
    with pytest.raises(ValueError, match='GitHub-hosted'):
        adapter.hosted_run(tmp_path / 'work', tmp_path / 'output', tmp_path / 'loop')
    assert list(tmp_path.iterdir()) == []


def test_unexpected_case_count_cannot_publish_success(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 65532)
    (tmp_path / 'prepared-evidence').mkdir()
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'run').mkdir()
    monkeypatch.setattr(adapter, 'command', lambda *a, **k: None)

    def run(*args, **kwargs):
        (tmp_path / 'evidence/junit.xml').write_text('<testsuites><testsuite><testcase/></testsuite></testsuites>')
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    with pytest.raises(ValueError, match='all 12'):
        adapter.guest_test()
    result = json.loads((tmp_path / 'evidence/result.json').read_text())
    assert result['passed'] is False and result['attestation'] is True


def test_workflow_is_manual_and_bridge_does_not_change_normal_matrix():
    # BaseLoader retains YAML's 'on' key rather than interpreting it as Boolean.
    workflow = yaml.load((ROOT / '.github/workflows/default-installed.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    assert workflow['permissions'] == {'contents': 'read'}
    steps = workflow['jobs']['default-installed']['steps']
    private = next(s for s in steps if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    assert private['with']['persist-credentials'] == 'false'
    assert private['with']['ref'] == '${{ steps.pins.outputs.loop }}'
    bridge = yaml.load((ROOT / '.github/workflows/integration-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert bridge['on']['workflow_dispatch']['inputs']['default_installed']['default'] == 'false'
    assert bridge['jobs']['default-installed']['if'] == "github.event_name == 'workflow_dispatch' && (inputs.default_installed || inputs.root_guest || inputs.installed_pwa)"
    assert bridge['jobs']['plan']['if'] == "github.event_name != 'workflow_dispatch' || !(inputs.default_installed || inputs.root_guest || inputs.installed_pwa)"
    assert bridge['jobs']['default-installed']['uses'] == './.github/workflows/default-installed.yml'
    assert bridge['on']['workflow_dispatch']['inputs']['desktop_safety']['default'] == 'false'
    assert bridge['jobs']['default-installed']['with']['desktop_safety'] == '${{ inputs.desktop_safety }}'
    assert workflow['on']['workflow_call']['inputs']['desktop_safety']['default'] == 'false'


@pytest.mark.parametrize('name,kind', [('../escape', 'file'), ('link', 'link'), ('/absolute', 'file')])
def test_evidence_extraction_rejects_paths_and_links(tmp_path, name, kind):
    archive = tmp_path / 'evidence.tar'
    with tarfile.open(archive, 'w') as tar:
        member = tarfile.TarInfo(name)
        if kind == 'link':
            member.type = tarfile.SYMTYPE
            member.linkname = '/outside'
            tar.addfile(member)
        else:
            member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='Unsafe evidence'):
        adapter.extract_evidence(archive, tmp_path / 'extracted')
    assert not (tmp_path / 'extracted').exists()


def test_evidence_archive_retains_exact_regular_bytes(tmp_path):
    archive = tmp_path / 'evidence.tar'
    with tarfile.open(archive, 'w') as tar:
        member = tarfile.TarInfo('./result.json')
        payload = b'{"passed":false}\n'
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    adapter.extract_evidence(archive, tmp_path / 'extracted')
    assert (tmp_path / 'extracted/result.json').read_bytes() == payload


def test_container_execution_contract_keeps_evidence_until_collection():
    # Structural guard, not Docker/runtime acceptance. Actual tmpfs readback and
    # daemon cleanup still require the manual hosted run.
    source = (ROOT / 'scripts/qualify_default_installed.py').read_text()
    assert "'--network=none'" in source and "'--read-only'" in source
    assert "'--cap-drop=ALL'" in source and "'--user=65532:65532'" in source
    assert 'exit-code.tmp /opt/defaults/evidence/exit-code' in source
    assert "['docker', 'exec', test, 'tar'" in source
    assert "test + ':' + str(GUEST / 'evidence')" not in source
    assert source.index("state['preparationEvidenceRetained'] = True") < source.index("effect(['docker', 'stop'")
    assert "except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit, OSError):" in source


def desktop_junit(path, group, bad=None):
    root = ET.Element('testsuite')
    for name, count in adapter.DESKTOP_CASES[group].items():
        for index in range(count):
            case = ET.SubElement(root, 'testcase', classname='tests.test_default_desktop_safety',
                                 name=f'{name}[{index}]')
    if bad:
        ET.SubElement(case, bad)
    ET.ElementTree(root).write(path)
    return root


@pytest.mark.parametrize('bad', [None, 'failure', 'skipped'])
def test_desktop_group_keeps_original_count_and_failed_verdict(tmp_path, bad):
    path = tmp_path / 'junit.xml'
    desktop_junit(path, 'preserved-22', bad)
    report = adapter.desktop_result(path, 'preserved-22', 0)
    assert len(report['cases']) == 22
    assert report['passed'] is (bad is None)
    if bad:
        assert report['cases'][-1]['outcome'] == bad


@pytest.mark.parametrize('change', ['missing', 'duplicate'])
def test_desktop_group_requires_every_unique_case(tmp_path, change):
    path = tmp_path / 'junit.xml'
    root = desktop_junit(path, 'remote-defaults')
    if change == 'missing':
        root.remove(root[-1])
    else:
        root.append(ET.fromstring(ET.tostring(root[-1])))
    ET.ElementTree(root).write(path)
    with pytest.raises(ValueError, match='Missing desktop|duplicate'):
        adapter.desktop_result(path, 'remote-defaults', 0)


def test_desktop_only_does_not_rerun_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 65532)
    (tmp_path / 'prepared-evidence').mkdir()
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'run').mkdir()
    monkeypatch.setattr(adapter, 'command', lambda *a, **k: None)
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('No baseline subprocess'))
    calls = []
    monkeypatch.setattr(adapter, 'run_desktop_safety', lambda *a: calls.append(a))
    adapter.guest_test(desktop_safety=True)
    result = json.loads((tmp_path / 'evidence/result.json').read_text())
    assert len(calls) == 1 and result['passed'] and result['attestation']
    assert result['stage'] == 'desktop-safety' and result['baselineExecuted'] is False
    assert result['testExit'] is result['checkExit'] is None
    assert not (tmp_path / 'evidence/junit.xml').exists()


def test_failed_preserved_group_does_not_erase_remote_discriminator(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    calls = []

    def run(argv, **kwargs):
        junit = Path(next(a for a in argv if a.startswith('--junitxml=')).split('=', 1)[1])
        group = 'preserved-22' if 'preserved-22' in str(junit) else 'remote-defaults'
        calls.append(group)
        desktop_junit(junit, group, 'failure' if group == 'preserved-22' else None)
        return subprocess.CompletedProcess(argv, 1 if group == 'preserved-22' else 0)

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    with pytest.raises(ValueError, match='Desktop safety check failed'):
        adapter.run_desktop_safety(Path('/synthetic/python'), {}, tmp_path)
    report = json.loads((tmp_path / 'desktop-safety-result.json').read_text())
    assert calls == ['preserved-22', 'remote-defaults']
    assert report['passed'] is False and report['baselineExecuted'] is False
    assert not report['groups']['preserved-22']['passed']
    assert report['groups']['remote-defaults']['passed']


@pytest.mark.parametrize('flags,expected', [
    ({}, 'ic13'), ({'desktop_safety': True}, 'desktop-safety'),
    ({'root_guest': True}, 'root-guest'), ({'installed_pwa': True}, 'installed-pwa'),
    ({'root_guest': True, 'installed_pwa': True}, None),
    ({'desktop_safety': True, 'root_guest': True}, None),
    ({'desktop_safety': True, 'installed_pwa': True}, None),
])
def test_fixed_stages_are_explicit_and_mutually_exclusive(flags, expected):
    if expected is None:
        with pytest.raises(ValueError, match='only one'):
            adapter.selected_stage(**flags)
    else:
        assert adapter.selected_stage(**flags) == expected


def fixed_junit(path, stage):
    file, names = adapter.FIXED_CASES[stage]
    root = ET.Element('testsuite')
    for name in names:
        ET.SubElement(root, 'testcase', classname='tests.' + file.removesuffix('.py'), name=name)
    ET.ElementTree(root).write(path)
    return root


@pytest.mark.parametrize('stage', ['root-guest', 'installed-pwa'])
@pytest.mark.parametrize('change', ['none', 'missing', 'duplicate', 'extra', 'parameter', 'wrong-module',
                                    'skipped', 'error', 'failure', 'counter', 'exit'])
def test_fixed_stage_requires_exact_successful_identities(tmp_path, stage, change):
    path = tmp_path / 'junit.xml'
    root = fixed_junit(path, stage)
    if change == 'missing':
        root.remove(root[-1])
    elif change == 'duplicate':
        root.append(ET.fromstring(ET.tostring(root[-1])))
    elif change == 'extra':
        ET.SubElement(root, 'testcase', classname=root[0].get('classname'), name='test_other')
    elif change == 'parameter':
        root[-1].set('name', root[-1].get('name') + '[unexpected]')
    elif change == 'wrong-module':
        root[-1].set('classname', 'other.' + root[-1].get('classname'))
    elif change in {'skipped', 'error', 'failure'}:
        ET.SubElement(root[-1], change)
    elif change == 'counter':
        root.set('skipped', '1')
    ET.ElementTree(root).write(path)
    if change in {'missing', 'duplicate', 'extra', 'parameter', 'wrong-module'}:
        with pytest.raises(ValueError, match='identities'):
            adapter.fixed_result(path, stage, 0)
    else:
        result = adapter.fixed_result(path, stage, 1 if change == 'exit' else 0)
        assert result['passed'] is (change == 'none')
        assert len(result['cases']) == (8 if stage == 'root-guest' else 1)


def test_container_default_options_remain_unprivileged_and_unchanged():
    expected = ['--network=none', '--user=65532:65532', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--cpus=2', '--memory=4g',
                '--pids-limit=256', '--read-only']
    for folder in ('home', 'tmp', 'run', 'cache', 'evidence'):
        expected += ['--tmpfs', '/opt/defaults/' + folder + ':rw,uid=65532,gid=65532,mode=0700,size=512m']
    assert adapter.test_container_options('ic13') == expected
    assert adapter.test_container_options('desktop-safety') == expected
    root = adapter.test_container_options('root-guest')
    assert '--user=0:0' in root
    assert {v for v in root if v.startswith('--cap-add=')} == {
        '--cap-add=KILL', '--cap-add=SETUID', '--cap-add=SETGID'}
    assert '/opt/defaults/tmp:rw,uid=0,gid=0,mode=0755,size=512m' in root
    assert '/opt/defaults/evidence:rw,uid=0,gid=0,mode=0700,size=512m' in root
    pwa = adapter.test_container_options('installed-pwa')
    assert pwa == expected + ['--tmpfs', '/tmp:rw,uid=0,gid=0,mode=1777,size=512m']
    assert not any('privileged' in v or 'pid=host' in v or v == '--mount' for v in root + pwa)


@pytest.mark.parametrize('stage', ['ic13', 'desktop-safety', 'root-guest', 'installed-pwa'])
@pytest.mark.parametrize('change', [
    'none', 'reported-tmpfs', 'privileged', 'host-pid', 'capability', 'bind', 'mount', 'user',
    'missing-tmpfs', 'extra-tmpfs', 'wrong-mode', 'wrong-uid', 'wrong-gid', 'wrong-size',
    'missing-tmpfs-options', 'extra-tmpfs-option', 'volume', 'extra-mount', 'duplicate-mount',
    'host-config-mount', 'volumes-from', 'tmpfs-with-source',
])
def test_actual_container_inspection_refuses_widened_stage(stage, change):
    root = stage == 'root-guest'
    owner = '0' if root else '65532'
    tmpfs = {str(adapter.GUEST / name):
             f"rw,uid={owner},gid={owner},mode={'0755' if root and name == 'tmp' else '0700'},size=512m"
             for name in ('home', 'tmp', 'run', 'cache', 'evidence')}
    if stage == 'installed-pwa':
        tmpfs['/tmp'] = 'rw,uid=0,gid=0,mode=1777,size=512m'
    host = {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False, 'Binds': None,
            'PidMode': '', 'CapDrop': ['ALL'], 'CapAdd': ['SETUID', 'SETGID', 'KILL'] if root else None,
            'Memory': 4 * 1024 ** 3, 'NanoCpus': 2 * 10 ** 9, 'PidsLimit': 256,
            'SecurityOpt': ['no-new-privileges'], 'Tmpfs': tmpfs}
    info = {'HostConfig': host, 'Config': {'User': '0:0' if root else '65532:65532'},
            'Mounts': []}
    destination = str(adapter.GUEST / 'evidence')
    if change == 'reported-tmpfs':
        info['Mounts'] = [{'Type': 'tmpfs', 'Destination': path} for path in tmpfs]
    elif change == 'privileged':
        host['Privileged'] = True
    elif change == 'host-pid':
        host['PidMode'] = 'host'
    elif change == 'capability':
        host['CapAdd'] = [*(host['CapAdd'] or []), 'SYS_ADMIN']
    elif change == 'bind':
        host['Binds'] = ['/private:/private']
    elif change == 'mount':
        info['Mounts'] = [{'Type': 'bind', 'Destination': destination, 'Source': '/private'}]
    elif change == 'user':
        info['Config']['User'] = '65534:65534'
    elif change == 'missing-tmpfs':
        del tmpfs[destination]
    elif change == 'extra-tmpfs':
        tmpfs['/extra'] = tmpfs[destination]
    elif change.startswith('wrong-'):
        key, value = {'wrong-mode': ('mode', '0777'), 'wrong-uid': ('uid', '65534'),
                      'wrong-gid': ('gid', '65534'), 'wrong-size': ('size', '1g')}[change]
        tmpfs[destination] = ','.join(key + '=' + value if option.startswith(key + '=') else option
                                     for option in tmpfs[destination].split(','))
    elif change == 'missing-tmpfs-options':
        tmpfs[destination] = 'rw'
    elif change == 'extra-tmpfs-option':
        tmpfs[destination] += ',exec'
    elif change == 'volume':
        info['Mounts'] = [{'Type': 'volume', 'Destination': destination}]
    elif change == 'extra-mount':
        info['Mounts'] = [{'Type': 'tmpfs', 'Destination': '/extra'}]
    elif change == 'duplicate-mount':
        info['Mounts'] = [{'Type': 'tmpfs', 'Destination': destination}] * 2
    elif change == 'host-config-mount':
        host['Mounts'] = [{'Type': 'bind', 'Source': '/private', 'Target': destination}]
    elif change == 'volumes-from':
        host['VolumesFrom'] = ['unrelated-container']
    elif change == 'tmpfs-with-source':
        info['Mounts'] = [{'Type': 'tmpfs', 'Destination': destination, 'Source': '/private'}]
    if change in {'none', 'reported-tmpfs'}:
        assert adapter.verify_test_isolation(info, stage)['NetworkMode'] == 'none'
        assert adapter.verify_test_isolation(info, stage)['Tmpfs'] == tmpfs
    else:
        with pytest.raises(ValueError, match='isolation'):
            adapter.verify_test_isolation(info, stage)


@pytest.mark.parametrize('change', ['none', 'missing', 'duplicate', 'symlink', 'oversized', 'bad-json', 'bad-png'])
def test_pwa_retention_is_bounded_and_never_copies_private_profile(tmp_path, change):
    base, evidence = tmp_path / 'tests', tmp_path / 'evidence'
    base.mkdir()
    evidence.mkdir()
    (base / 'browser-profile').mkdir()
    (base / 'browser-profile/cookies').write_text('synthetic-private-must-not-export')
    data = {key: {} for key in ('browser', 'os', 'window', 'requests', 'nativeDragging', 'physicalAndroid', 'macOS')}
    result = base / 'installed-pwa.json'
    result.write_text(json.dumps(data))
    png = base / 'installed-pwa.png'
    png.write_bytes(b'\x89PNG\r\n\x1a\nIEND\xaeB`\x82')
    if change == 'missing':
        result.unlink()
    elif change == 'duplicate':
        (base / 'other').mkdir()
        (base / 'other/installed-pwa.json').write_text(json.dumps(data))
    elif change == 'symlink':
        result.unlink()
        result.symlink_to(base / 'browser-profile/cookies')
    elif change == 'oversized':
        result.write_bytes(b'x' * (1024 * 1024 + 1))
    elif change == 'bad-json':
        result.write_text('{"cookies":"unexpected"}')
    elif change == 'bad-png':
        png.write_bytes(b'partial')
    if change in {'none', 'missing'}:
        report = adapter.retain_pwa_artifacts(base, evidence)
        assert report['missing'] == (['installed-pwa.json'] if change == 'missing' else [])
        assert {row['name'] for row in report['retained']} == (
            {'installed-pwa.png'} if change == 'missing' else {'installed-pwa.json', 'installed-pwa.png'})
    else:
        with pytest.raises(ValueError):
            adapter.retain_pwa_artifacts(base, evidence)
    assert not (evidence / 'browser-profile').exists()


@pytest.mark.parametrize('guard_rejects', [False, True])
def test_failed_fixed_stage_preserves_raw_result_and_outer_attestation(monkeypatch, tmp_path, guard_rejects):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 0)
    for name in ('prepared-evidence', 'evidence', 'run', 'tmp'):
        (tmp_path / name).mkdir()
    def command(argv, **kwargs):
        if 'check' in argv:
            receipt = json.loads((tmp_path / 'evidence/fixed-stage-result.json').read_text())
            assert receipt['exitCode'] == 0 and receipt['cases'][0]['outcome'] == 'skipped'
            assert receipt['passed'] is False
            if guard_rejects:
                raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(adapter, 'command', command)
    monkeypatch.setattr(adapter, 'root_guest_preflight', lambda env: {'fixture': True})
    monkeypatch.setattr(adapter, 'run_residual_helpers', lambda *args: {'passed': True})

    def run(argv, **kwargs):
        path = tmp_path / 'evidence/junit.xml'
        xml = fixed_junit(path, 'root-guest')
        ET.SubElement(xml[0], 'skipped')
        ET.ElementTree(xml).write(path)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    error = subprocess.CalledProcessError if guard_rejects else ValueError
    with pytest.raises(error):
        adapter.guest_test(root_guest=True)
    assert not json.loads((tmp_path / 'evidence/result.json').read_text())['passed']
    assert json.loads((tmp_path / 'evidence/result.json').read_text())['attestation']
    fixed = json.loads((tmp_path / 'evidence/fixed-stage-result.json').read_text())
    assert fixed['passed'] is False and fixed['exitCode'] == 0
    assert len(fixed['cases']) == 8 and fixed['cases'][0]['outcome'] == 'skipped'
    assert ET.parse(tmp_path / 'evidence/junit.xml').find('.//skipped') is not None


def test_pwa_uninstall_failure_still_closes_context_without_hiding_failure():
    import ast
    import asyncio
    from unittest.mock import AsyncMock
    tree = ast.parse((ROOT / 'tests/test_browser_auth.py').read_text())
    test = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'test_installed_pwa_launch')
    cleanup = next(n.finalbody for n in ast.walk(test) if isinstance(n, ast.Try) and n.finalbody
                   and 'PWA.uninstall' in ast.unparse(n.finalbody))
    function = ast.AsyncFunctionDef(name='cleanup', args=ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=cleanup, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    context = type('Context', (), {'close': AsyncMock()})()
    cdp = type('CDP', (), {'send': AsyncMock(side_effect=RuntimeError('synthetic uninstall failure'))})()
    namespace = {'installed': True, 'cdp': cdp, 'context': context, 'origin': 'http://127.0.0.1:12345'}
    exec(compile(module, '<actual-pwa-cleanup>', 'exec'), namespace)
    with pytest.raises(RuntimeError, match='synthetic uninstall failure'):
        asyncio.run(namespace['cleanup']())
    context.close.assert_awaited_once()


def test_fixed_stage_workflow_guards_and_preparation_are_scoped():
    workflow = yaml.load((ROOT / '.github/workflows/default-installed.yml').read_text(), Loader=yaml.BaseLoader)
    bridge = yaml.load((ROOT / '.github/workflows/integration-checks.yml').read_text(), Loader=yaml.BaseLoader)
    for name in ('root_guest', 'installed_pwa'):
        assert workflow['on']['workflow_dispatch']['inputs'][name]['default'] == 'false'
        assert workflow['on']['workflow_call']['inputs'][name]['default'] == 'false'
        assert bridge['on']['workflow_dispatch']['inputs'][name]['default'] == 'false'
        assert bridge['jobs']['default-installed']['with'][name] == '${{ inputs.' + name + ' }}'
    job = workflow['jobs']['default-installed']
    assert job['timeout-minutes'] == '30'
    steps = job['steps']
    guard = next(i for i, s in enumerate(steps) if s.get('name') == 'Reject conflicting fixed stages before preparation')
    private = next(i for i, s in enumerate(steps) if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    assert guard < private
    assert 'exit 1' in steps[guard]['run']
    source = (ROOT / 'scripts/qualify_default_installed.py').read_text()
    assert "client_helpers().browser_prepare_commands" in source and "'--lane', 'browser-installed'" in source
    assert "timeout=300" in source and "timeout=420" in source
    assert "Preparation must stop before tests" in source
    assert "snapshot['wheels']" in source and "snapshot['browsers']" in source and "snapshot['system']" in source


def test_pwa_sbin_path_is_preparation_only(monkeypatch):
    monkeypatch.setenv('PRIVATE_TOKEN', 'synthetic-must-not-leak')
    original = adapter.clean_env()
    prepared = adapter.pwa_preparation_environment(original)
    assert '/usr/sbin' in prepared['PATH'].split(':') and '/sbin' in prepared['PATH'].split(':')
    assert prepared['PLAYWRIGHT_BROWSERS_PATH'] == str(adapter.GUEST / 'browsers')
    assert original == adapter.clean_env()
    assert '/usr/sbin' not in original['PATH'].split(':') and 'PRIVATE_TOKEN' not in prepared


def test_root_evidence_copy_cannot_widen_receipt_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    prepared, evidence = tmp_path / 'prepared-evidence', tmp_path / 'evidence'
    prepared.mkdir(mode=0o755)
    prepared.chmod(0o755)
    (prepared / 'receipt.json').write_text('{"prepared":true}')
    evidence.mkdir(mode=0o700)
    adapter.copy_prepared_evidence(evidence, 'root-guest')
    assert evidence.stat().st_mode & 0o777 == 0o700
    assert (evidence / 'receipt.json').read_bytes() == (prepared / 'receipt.json').read_bytes()


def test_pwa_preflight_records_effective_environment_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    (tmp_path / 'candidate/tests/web').mkdir(parents=True)
    (tmp_path / 'candidate/tests/web/package-lock.json').write_text(json.dumps({
        'packages': {'node_modules/playwright': {'version': '1.63.0'}}}))
    python = tmp_path / '.venv/bin/python'
    original = {'actual_interpreter': str(python), 'interpreter': '/unused/env/bin/python',
                'environment': {'PLAYWRIGHT_BROWSERS_PATH': '/unused/browsers', 'HOME': '/unused/home'},
                'prerequisites': {'playwright_version': '1.63.0', 'chromium': str(tmp_path / 'browsers/chromium/chrome')}}
    env = {'PLAYWRIGHT_BROWSERS_PATH': str(tmp_path / 'browsers'), 'HOME': str(tmp_path / 'home'),
           'DISPLAY': ':99', 'XDG_RUNTIME_DIR': str(tmp_path / 'run'), 'PRIVATE_TOKEN': 'not-retained'}
    actual = adapter.pwa_preflight_receipt(original, env, python)
    assert actual['interpreter'] == str(python)
    assert actual['environment'] == {k: v for k, v in env.items() if k != 'PRIVATE_TOKEN'}
    actual['prerequisites']['playwright_version'] = '0.0.0'
    with pytest.raises(ValueError, match='lock-matched'):
        adapter.pwa_preflight_receipt(actual, env, python)


def residual_helper_junit(path):
    root = ET.Element('testsuite')
    for module, count in adapter.RESIDUAL_HELPER_COUNTS.items():
        for index in range(count):
            ET.SubElement(root, 'testcase', classname=module, name=f'test_contract_{index}')
    ET.ElementTree(root).write(path)
    return root


@pytest.mark.parametrize('change', ['none', 'missing', 'duplicate', 'extra', 'wrong-module',
                                    'skipped', 'failure', 'error', 'counter', 'exit'])
def test_residual_helpers_keep_module_counts_separate_from_product(tmp_path, change):
    path = tmp_path / 'helper-contracts.xml'
    root = residual_helper_junit(path)
    if change == 'missing':
        root.remove(root[0])
    elif change == 'duplicate':
        root[1].set('name', root[0].get('name'))
    elif change == 'extra':
        ET.SubElement(root, 'testcase', classname='tests.test_integration_clients', name='test_extra')
    elif change == 'wrong-module':
        root[0].set('classname', 'tests.test_browser_auth')
    elif change in {'skipped', 'failure', 'error'}:
        ET.SubElement(root[0], change)
    elif change == 'counter':
        root.set('errors', '1')
    ET.ElementTree(root).write(path)
    report = adapter.residual_helper_result(path, 1 if change == 'exit' else 0)
    assert report['passed'] is (change == 'none')
    assert report['expectedCounts'] == {
        'tests.test_integration_clients': 15, 'tests.test_integration_checks': 64}
    assert report['exitCode'] == (1 if change == 'exit' else 0)
    assert not (tmp_path / 'junit.xml').exists()
    assert len(report['cases']) == len(list(root))


@pytest.mark.parametrize('outcome', ['passed', 'skipped', 'failed-exit', 'launch-error'])
def test_residual_helper_subprocess_has_scoped_import_path_and_retained_receipt(monkeypatch, tmp_path, outcome):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    env = adapter.clean_env()
    original = dict(env)
    python = tmp_path / '.venv/bin/python'

    def run(argv, **kwargs):
        assert argv[:5] == [str(python), '-I', '-B', '-m', 'pytest']
        assert '--noconftest' in argv and '--import-mode=importlib' in argv
        assert argv[argv.index('-c') + 1] == '/dev/null'
        assert argv[argv.index('-o') + 1] == 'pythonpath=' + str(tmp_path / 'candidate')
        assert '--rootdir=' + str(tmp_path / 'candidate') in argv
        assert argv[-2:] == [str(tmp_path / 'candidate/tests/test_integration_clients.py'),
                             str(tmp_path / 'candidate/tests/test_integration_checks.py')]
        assert kwargs['cwd'] == tmp_path / 'run' and kwargs['timeout'] == 120
        assert kwargs['env'] == original and 'PYTHONPATH' not in kwargs['env']
        if outcome == 'launch-error':
            raise FileNotFoundError('synthetic interpreter unavailable')
        path = evidence / 'helper-contracts.xml'
        root = residual_helper_junit(path)
        if outcome == 'skipped':
            ET.SubElement(root[0], 'skipped')
            ET.ElementTree(root).write(path)
        return subprocess.CompletedProcess(argv, 1 if outcome == 'failed-exit' else 0)

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    if outcome == 'passed':
        report = adapter.run_residual_helpers(python, env, evidence)
        assert report['counts'] == adapter.RESIDUAL_HELPER_COUNTS
    else:
        with pytest.raises((ValueError, FileNotFoundError)):
            adapter.run_residual_helpers(python, env, evidence)
    retained = json.loads((evidence / 'helper-contracts-result.json').read_text())
    assert retained['passed'] is (outcome == 'passed')
    assert retained['exitCode'] == (None if outcome == 'launch-error' else 1 if outcome == 'failed-exit' else 0)
    assert env == original and not (evidence / 'junit.xml').exists()
    if outcome == 'skipped':
        assert retained['cases'][0]['outcome'] == 'skipped'


@pytest.mark.parametrize('stage', ['root-guest', 'installed-pwa'])
@pytest.mark.parametrize('helper_passes', [False, True])
def test_fixed_product_waits_for_changed_helper_contracts(monkeypatch, tmp_path, stage, helper_passes):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 0 if stage == 'root-guest' else 65532)
    for name in ('prepared-evidence', 'evidence', 'run', 'tmp'):
        (tmp_path / name).mkdir()
    order = []

    def command(argv, **kwargs):
        order.append('attest' if 'guest-attest' in argv else 'adapter')
        assert not any(str(arg).startswith('pythonpath=') for arg in argv)

    def helpers(*args):
        order.append('helpers')
        if not helper_passes:
            raise ValueError('helper contracts failed')
        return {'passed': True, 'counts': adapter.RESIDUAL_HELPER_COUNTS}

    monkeypatch.setattr(adapter, 'command', command)
    monkeypatch.setattr(adapter, 'run_residual_helpers', helpers)
    monkeypatch.setattr(adapter, 'run_fixed_stage', lambda *args: order.append('product'))
    flags = {'root_guest': stage == 'root-guest', 'installed_pwa': stage == 'installed-pwa'}
    if helper_passes:
        adapter.guest_test(**flags)
    else:
        with pytest.raises(ValueError, match='helper contracts failed'):
            adapter.guest_test(**flags)
    assert order == ['attest', 'adapter', 'helpers', *(['product'] if helper_passes else []), 'attest']
    result = json.loads((tmp_path / 'evidence/result.json').read_text())
    assert result['passed'] is helper_passes and result['attestation']
    assert result['baselineExecuted'] is False
    if helper_passes:
        assert result['helperContracts']['counts'] == adapter.RESIDUAL_HELPER_COUNTS
    else:
        assert result['testExit'] is None


def test_residual_helper_expected_counts_match_the_two_current_modules():
    import ast
    for module, expected in adapter.RESIDUAL_HELPER_COUNTS.items():
        tree = ast.parse((ROOT / (module.replace('.', '/') + '.py')).read_text())
        count = 0
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith('test_'):
                continue
            instances = 1
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == 'parametrize':
                    instances *= len(ast.literal_eval(decorator.args[1]))
            count += instances
        assert count == expected
    assert set(adapter.RESIDUAL_HELPER_COUNTS) == {
        'tests.test_integration_clients', 'tests.test_integration_checks'}


def prepared_wheel_directory(tmp_path):
    root, evidence = tmp_path / 'wheels', tmp_path / 'evidence'
    root.mkdir()
    evidence.mkdir()
    wheel = fake_wheel(root / 'example-1.0-py3-none-any.whl', 'example')
    # Exact default output of the pinned uv build, not arbitrary metadata.
    (root / '.gitignore').write_bytes(b'*')
    return root, evidence, wheel


@pytest.mark.parametrize('stage', ['root-guest', 'installed-pwa'])
def test_fixed_wheel_directory_keeps_archives_and_known_metadata(tmp_path, stage):
    root, evidence, wheel = prepared_wheel_directory(tmp_path)
    archives = adapter.wheel_archive_hashes([wheel], evidence)
    baseline = adapter.verify_wheel_directory(root, archives, evidence)
    assert archives == {wheel.name: adapter.digest(wheel)}
    assert baseline == {
        wheel.name: {'kind': 'file', 'sha256': adapter.digest(wheel)},
        '.gitignore': {'kind': 'file', 'sha256': adapter.digest(root / '.gitignore')},
    }
    # Exercise the actual capture/comparison path, with no mocked digest,
    # directory inventory or attestation. The same invariant serves both stages.
    preparation = {'stage': stage, 'wheels': archives, 'wheelDirectory': baseline}
    assert adapter.verify_wheel_directory(
        root, preparation['wheels'], evidence, baseline=preparation['wheelDirectory']) == baseline
    assert not (evidence / 'wheel-integrity-failure.json').exists()
    # The old comparison rejects this unchanged, valid directory.
    assert adapter.inventory(root) != archives


@pytest.mark.parametrize('stage', ['root-guest', 'installed-pwa'])
@pytest.mark.parametrize('change', [
    'wheel-missing', 'wheel-extra', 'wheel-changed', 'wheel-substituted',
    'metadata-missing', 'metadata-added', 'metadata-changed', 'metadata-newline',
    'wheel-symlink', 'metadata-symlink', 'escaping-symlink', 'nested-symlink',
    'directory', 'fifo', 'wheel-directory', 'forged-baseline',
])
def test_fixed_wheel_directory_refuses_drift_before_and_after_freeze(tmp_path, stage, change):
    root, evidence, wheel = prepared_wheel_directory(tmp_path)
    archives = adapter.wheel_archive_hashes([wheel], evidence)
    baseline = adapter.verify_wheel_directory(root, archives, evidence)
    if change == 'wheel-missing':
        wheel.unlink()
    elif change == 'wheel-extra':
        fake_wheel(root / 'extra.whl', 'extra')
    elif change == 'wheel-changed':
        wheel.write_bytes(b'changed wheel bytes')
    elif change == 'wheel-substituted':
        wheel.rename(root / 'substitute.whl')
    elif change == 'metadata-missing':
        (root / '.gitignore').unlink()
    elif change == 'metadata-added':
        (root / 'unexpected.txt').write_bytes(b'synthetic private content must not be retained')
    elif change in {'metadata-changed', 'metadata-newline'}:
        (root / '.gitignore').write_bytes(b'changed' if change == 'metadata-changed' else b'*\n')
    elif change in {'wheel-symlink', 'metadata-symlink', 'escaping-symlink'}:
        target = wheel if change == 'wheel-symlink' else root / '.gitignore'
        target.unlink()
        if change == 'escaping-symlink':
            outside = tmp_path / 'outside'
            outside.write_bytes(b'synthetic private content must not be retained')
            target.symlink_to(outside)
        else:
            target.symlink_to(root / ('.gitignore' if change == 'wheel-symlink' else wheel.name))
    elif change in {'nested-symlink', 'directory'}:
        folder = root / 'nested'
        folder.mkdir()
        if change == 'nested-symlink':
            (folder / 'link').symlink_to(tmp_path / 'outside')
    elif change == 'fifo':
        adapter.os.mkfifo(root / 'pipe')
    elif change == 'wheel-directory':
        wheel.unlink()
        wheel.mkdir()
    else:
        baseline = {**baseline, '.gitignore': {'kind': 'file', 'sha256': '0' * 64}}

    with pytest.raises(ValueError, match='Wheel directory integrity'):
        adapter.verify_wheel_directory(root, archives, evidence, baseline=baseline)
    record = json.loads((evidence / 'wheel-integrity-failure.json').read_text())
    assert record['boundary'] == ('baseline' if change == 'forged-baseline' else 'directory')
    assert record['differenceCount'] > 0 and record['differences']
    assert not record['truncated']
    serialized = json.dumps(record)
    assert str(tmp_path) not in serialized
    assert 'synthetic private content must not be retained' not in serialized
    assert not (evidence / 'junit.xml').exists()
    if change != 'forged-baseline':
        # Cannot accept an unsafe or drifted directory as a new baseline.
        with pytest.raises(ValueError, match='Wheel directory integrity'):
            adapter.verify_wheel_directory(root, archives, evidence)


@pytest.mark.parametrize('kind', ['symlink', 'escaping-symlink', 'directory', 'fifo'])
def test_selected_wheel_type_is_checked_before_hashing(tmp_path, kind):
    root, evidence, wheel = prepared_wheel_directory(tmp_path)
    wheel.unlink()
    if kind == 'directory':
        wheel.mkdir()
    elif kind == 'fifo':
        adapter.os.mkfifo(wheel)
    else:
        wheel.symlink_to(root / '.gitignore' if kind == 'symlink' else tmp_path / 'outside')
    with pytest.raises(ValueError, match='Wheel directory integrity'):
        adapter.wheel_archive_hashes([wheel], evidence)
    record = json.loads((evidence / 'wheel-integrity-failure.json').read_text())
    assert record['boundary'] == 'selected-wheels'
    assert record['differences'][0]['change'] == 'type'


def test_wheel_diagnostics_are_bounded_without_accepting_omitted_differences(tmp_path):
    root, evidence, wheel = prepared_wheel_directory(tmp_path)
    archives = adapter.wheel_archive_hashes([wheel], evidence)
    for index in range(40):
        (root / f'unexpected-{index:02}.txt').write_bytes(b'not retained')
    with pytest.raises(ValueError, match='Wheel directory integrity'):
        adapter.verify_wheel_directory(root, archives, evidence)
    report = json.loads((evidence / 'wheel-integrity-failure.json').read_text())
    assert report['differenceCount'] == 40
    assert len(report['differences']) == 32 and report['truncated']
    assert all(row['change'] == 'extra' for row in report['differences'])
    assert 'not retained' not in json.dumps(report)


@pytest.mark.parametrize('malformation', ['scalar', 'oversized-hash', 'extra-content', 'invalid-kind', 'manifest', 'path'])
def test_wheel_baseline_failure_retains_only_validated_metadata(tmp_path, malformation):
    root, evidence, wheel = prepared_wheel_directory(tmp_path)
    archives = adapter.wheel_archive_hashes([wheel], evidence)
    baseline = adapter.verify_wheel_directory(root, archives, evidence)
    private = 'synthetic-private-content-' * 10000
    if malformation == 'scalar':
        baseline['.gitignore'] = private
    elif malformation == 'oversized-hash':
        baseline['.gitignore']['sha256'] = private
    elif malformation == 'extra-content':
        baseline['.gitignore']['content'] = private
    elif malformation == 'invalid-kind':
        baseline['.gitignore']['kind'] = private
    elif malformation == 'manifest':
        baseline = [private]
    else:
        baseline[str(tmp_path / 'private')] = {'kind': 'file', 'content': private}
    with pytest.raises(ValueError, match='Wheel directory integrity'):
        adapter.verify_wheel_directory(root, archives, evidence, baseline=baseline)
    raw = (evidence / 'wheel-integrity-failure.json').read_text()
    record = json.loads(raw)
    assert record['boundary'] == 'baseline' and record['differenceCount'] > 0
    assert len(raw) < 2000
    assert 'synthetic-private-content-' not in raw and str(tmp_path) not in raw
    assert any(row['actual'] == {'kind': 'invalid'} for row in record['differences'])


@pytest.mark.parametrize('location', ['directory', 'baseline'])
def test_wheel_diagnostics_retain_non_utf8_names_without_losing_refusal(tmp_path, location):
    import hashlib
    root, evidence, wheel = prepared_wheel_directory(tmp_path)
    archives = adapter.wheel_archive_hashes([wheel], evidence)
    baseline = adapter.verify_wheel_directory(root, archives, evidence)
    name = adapter.os.fsdecode(b'extra-\xff.txt') if location == 'directory' else 'extra-\ud800.txt'
    if location == 'directory':
        (root / name).write_bytes(b'synthetic bytes not retained')
    else:
        baseline[name] = {'kind': 'file', 'sha256': '0' * 64}
    with pytest.raises(ValueError, match='Wheel directory integrity'):
        adapter.verify_wheel_directory(root, archives, evidence, baseline=baseline)
    raw = (evidence / 'wheel-integrity-failure.json').read_text()
    record = json.loads(raw)
    assert record['boundary'] == location and record['differenceCount'] == 1
    row = record['differences'][0]
    assert row['name'] == name and row['change'] == 'extra'
    assert row['nameHashEncoding'] == 'utf-8-surrogatepass'
    assert row['nameSha256'] == hashlib.sha256(name.encode('utf-8', 'surrogatepass')).hexdigest()
    assert len(raw) < 2000 and 'synthetic bytes not retained' not in raw


def test_fixed_wheel_baseline_precedes_install_and_attestation_uses_both_maps():
    import ast
    tree = ast.parse((ROOT / 'scripts/qualify_default_installed.py').read_text())
    prepare = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'guest_prepare')
    attest = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'guest_attest')
    source = ast.unparse(prepare)
    assert source.index('wheel_archive_hashes(wheels,') < source.index('wheel_metadata(p)')
    assert source.index('verify_wheel_directory(') < source.index("'pip', 'install'")
    assert "'wheels': original_wheels" in source
    assert "'wheelDirectory': wheel_directory" in source
    actual = ast.unparse(attest)
    assert "verify_wheel_directory(GUEST / 'wheels', preparation['wheels']" in actual
    assert "baseline=preparation['wheelDirectory']" in actual
    assert 'snapshot == json.loads(before.read_text())' in actual


def prepared_browser_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    root, evidence = tmp_path / 'browsers', tmp_path / 'evidence'
    folder = root / 'chromium/chrome'
    folder.mkdir(parents=True)
    evidence.mkdir()
    (tmp_path / 'run').mkdir()
    (folder / 'deb.deps').write_bytes(b'synthetic browser dependencies')
    (folder / 'deb.deps').chmod(0o600)
    (folder / 'browser').write_bytes(b'synthetic executable')
    (folder / 'browser').chmod(0o750)
    (evidence / 'before.json').write_text(json.dumps({'browsers': adapter.inventory(root)}))
    return root, evidence


@pytest.mark.parametrize('denied', ['file', 'directory'])
def test_browser_probe_refuses_target_access_even_when_preparation_could_read(tmp_path, monkeypatch, denied):
    root, evidence = prepared_browser_tree(tmp_path, monkeypatch)
    original = adapter.browser_access_tree(root)
    assert original['failure'] is None
    expected = {'files': original['files'], 'directories': original['directories']}
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    for name in ('getuid', 'geteuid', 'getgid', 'getegid'):
        monkeypatch.setattr(adapter.os, name, lambda: 65532)
    monkeypatch.setattr(adapter.os, 'getgroups', lambda: [])
    if denied == 'file':
        real_digest = adapter.digest

        def digest(path):
            if Path(path).name == 'deb.deps':
                raise PermissionError(13, 'synthetic private error must not escape')
            return real_digest(path)

        monkeypatch.setattr(adapter, 'digest', digest)
    else:
        real_access = adapter.os.access
        monkeypatch.setattr(adapter.os, 'access',
                            lambda path, *a, **k: False if Path(path).name == 'chrome'
                            else real_access(path, *a, **k))
    result = adapter.browser_readiness_probe(expected)
    assert result['passed'] is False and result['identity']['uid'] == 65532
    assert result['failure']['errno'] == 13
    assert result['failure']['operation'] == ('read' if denied == 'file' else 'enumerate')
    assert {'path', 'kind', 'mode', 'uid', 'gid'} <= result['failure'].keys()
    assert str(tmp_path) not in json.dumps(result)
    assert 'synthetic private error' not in json.dumps(result)
    assert list(evidence.iterdir()) == [evidence / 'before.json']  # Read-only child.


@pytest.mark.parametrize('unsafe', ['file-link', 'directory-link', 'escape-link', 'fifo', 'setuid'])
def test_browser_preparation_refuses_unsafe_entries_before_ownership(tmp_path, monkeypatch, unsafe):
    root, evidence = prepared_browser_tree(tmp_path, monkeypatch)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'outside bytes must stay unchanged')
    if unsafe == 'fifo':
        adapter.os.mkfifo(root / 'unsafe')
    elif unsafe == 'setuid':
        (root / 'chromium/chrome/browser').chmod(0o4750)
    else:
        target = (outside if unsafe == 'escape-link' else
                  root / 'chromium' if unsafe == 'directory-link' else root / 'chromium/chrome/deb.deps')
        (root / 'unsafe').symlink_to(target)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 0)
    monkeypatch.setattr(adapter.os, 'fchown', lambda *a: pytest.fail('No mutation before full inspection'))
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('No child for unsafe tree'))
    with pytest.raises(ValueError, match='complete browser tree'):
        adapter.prepare_pwa_browser_access(tmp_path / '.venv/bin/python')
    report = json.loads((evidence / 'browser-readability.json').read_text())
    assert report['passed'] is False and report['beforeFailure']
    assert outside.read_bytes() == b'outside bytes must stay unchanged'


@pytest.mark.parametrize('outcome', ['passed', 'nonzero', 'wrong-identity', 'read-denied',
                                     'timeout', 'malformed', 'oversized', 'nested-content',
                                     'absolute-path', 'invalid-identity'])
def test_browser_preparation_is_scoped_and_requires_actual_identity_receipt(tmp_path, monkeypatch, outcome):
    root, evidence = prepared_browser_tree(tmp_path, monkeypatch)
    original = adapter.browser_access_tree(root)
    original_hashes = dict(original['files'])
    original_modes = {name: row['mode'] for name, row in original['entries'].items()}
    # Simulate only ownership/identity and child launch; chmod, traversal,
    # content hashing and the production comparison path use real files.
    real_fstat, real_lstat = adapter.os.fstat, Path.lstat
    browser_inodes = {real_lstat(root / name).st_ino for name in original['entries']}
    owners, touched = {}, []

    def observed_stat(info):
        values = list(info)
        if info.st_ino in browser_inodes:
            values[4:6] = owners.get(info.st_ino, (0, 0))
        return adapter.os.stat_result(values)

    def fchown(fd, uid, gid):
        info = real_fstat(fd)
        assert info.st_ino in browser_inodes and (uid, gid) == (65532, 65532)
        owners[info.st_ino] = (uid, gid)
        touched.append(info.st_ino)

    monkeypatch.setattr(adapter.os, 'fstat', lambda fd: observed_stat(real_fstat(fd)))
    monkeypatch.setattr(Path, 'lstat', lambda path, *a, **k: observed_stat(real_lstat(path, *a, **k)))
    monkeypatch.setattr(adapter.os, 'fchown', fchown)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 0)
    python = tmp_path / '.venv/bin/python'
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv == [str(python), '-I', '-B', str(ROOT / 'scripts/qualify_default_installed.py'),
                        'guest-browser-readiness']
        assert kwargs['user'] == kwargs['group'] == 65532 and kwargs['extra_groups'] == ()
        assert kwargs['cwd'] == tmp_path / 'run' and kwargs['timeout'] == 120
        assert kwargs['env'] == adapter.clean_env()
        assert 'PYTHONPATH' not in kwargs['env'] and '/usr/sbin' not in kwargs['env']['PATH']
        expected = json.loads(kwargs['input'])
        assert expected == {'files': original_hashes, 'directories': original['directories']}
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(argv, 120)
        if outcome in {'malformed', 'oversized'}:
            return subprocess.CompletedProcess(argv, 0, '{}' if outcome == 'malformed' else 'x' * 16385, '')
        with monkeypatch.context() as child:
            for name in ('getuid', 'geteuid', 'getgid', 'getegid'):
                child.setattr(adapter.os, name, lambda: 65532)
            child.setattr(adapter.os, 'getgroups', lambda: [])
            if outcome == 'wrong-identity':
                child.setattr(adapter.os, 'geteuid', lambda: 0)
            receipt = adapter.browser_readiness_probe(expected)
        assert receipt['passed'] is (outcome != 'wrong-identity')
        if outcome == 'read-denied':
            receipt['passed'] = False
            receipt['failure'] = {
                'path': 'chromium/chrome/deb.deps', 'pathTruncated': False,
                'pathSha256': '0' * 64, 'kind': 'file', 'mode': 0o600,
                'uid': 0, 'gid': 0, 'operation': 'read', 'errno': 13}
        elif outcome in {'nested-content', 'absolute-path'}:
            receipt['passed'] = False
            receipt['failure'] = {
                'path': '/synthetic-private/path', 'pathTruncated': False,
                'pathSha256': '0' * 64, 'kind': 'file', 'operation': 'read', 'errno': 13}
            if outcome == 'nested-content':
                receipt['failure']['content'] = 'synthetic-private-content'
        elif outcome == 'invalid-identity':
            receipt['identity']['groups'] = ['synthetic-private-content']
        return subprocess.CompletedProcess(argv, 1 if outcome == 'nonzero' else 0,
                                           json.dumps(receipt), '')

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    if outcome == 'passed':
        adapter.prepare_pwa_browser_access(python)
    else:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            adapter.prepare_pwa_browser_access(python)
    report = json.loads((evidence / 'browser-readability.json').read_text())
    assert report['passed'] is (outcome == 'passed') and len(calls) == 1
    assert set(touched) == browser_inodes and len(touched) == len(browser_inodes)
    after = adapter.browser_access_tree(root)
    assert after['files'] == original_hashes
    for name, row in after['entries'].items():
        assert (row['uid'], row['gid']) == (65532, 65532)
        assert row['mode'] == original_modes[name] | (0o500 if row['kind'] == 'directory' else 0o400)
        if row['kind'] == 'file':
            assert row['mode'] & 0o111 == original_modes[name] & 0o111
    assert (evidence / 'before.json').read_text() == json.dumps({'browsers': original_hashes})
    assert not (evidence / 'after.json').exists()
    assert 'synthetic-private' not in json.dumps(report)
    if outcome == 'wrong-identity':
        assert report['probe']['identity']['euid'] == 0
        assert report['probe']['failure'] == {'operation': 'identity'}
    elif outcome == 'read-denied':
        assert report['probe']['failure']['errno'] == 13
    elif outcome in {'malformed', 'nested-content', 'absolute-path', 'invalid-identity'}:
        assert report['probe'] == {'valid': False, 'reason': 'malformed-receipt'}


@pytest.mark.parametrize('identity_change', ['uid', 'euid', 'groups'])
def test_browser_readiness_identity_refusal_has_complete_safe_schema(tmp_path, monkeypatch, identity_change):
    root, _ = prepared_browser_tree(tmp_path, monkeypatch)
    before = adapter.browser_access_tree(root)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    for name in ('getuid', 'geteuid', 'getgid', 'getegid'):
        monkeypatch.setattr(adapter.os, name, lambda: 65532)
    monkeypatch.setattr(adapter.os, 'getgroups', lambda: [])
    if identity_change == 'groups':
        monkeypatch.setattr(adapter.os, 'getgroups', lambda: [1])
    else:
        monkeypatch.setattr(adapter.os, 'get' + identity_change, lambda: 0)
    monkeypatch.setattr(adapter, 'browser_access_tree', lambda *a, **k: pytest.fail('Identity checked before reads'))
    result = adapter.browser_readiness_probe({'files': before['files'], 'directories': before['directories']})
    assert not result['passed'] and result['failure'] == {'operation': 'identity'}
    assert result['fileCount'] == result['directoryCount'] == 0
    assert adapter.validated_browser_receipt(result) == result


@pytest.mark.parametrize('layout', ['flat', 'nested'])
def test_browser_tree_bounds_enumeration_before_materializing_oversized_directory(tmp_path, monkeypatch, layout):
    root = tmp_path / 'browsers'
    root.mkdir()
    folders = [root] if layout == 'flat' else [root / 'a', root / 'b']
    for folder in folders:
        folder.mkdir(exist_ok=True)
        for index in range(4100 if layout == 'flat' else 2050):
            (folder / f'entry-{index:04}').write_bytes(b'')
    real_scan = adapter.os.scandir
    consumed = 0

    class CountedScan:
        def __init__(self, path):
            self.scan = real_scan(path)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.scan.close()
        def __iter__(self):
            for entry in self.scan:
                nonlocal consumed
                consumed += 1
                yield entry

    monkeypatch.setattr(adapter.os, 'scandir', CountedScan)
    result = adapter.browser_access_tree(root)
    assert result['failure']['operation'] == 'enumerate'
    assert len(result['entries']) <= 4096 and consumed <= 4096


def test_browser_readability_hook_is_pwa_only_before_preparation_handoff():
    import ast
    tree = ast.parse((ROOT / 'scripts/qualify_default_installed.py').read_text())
    prepare = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'guest_prepare')
    guards = [n for n in ast.walk(prepare) if isinstance(n, ast.If)
              and 'prepare_pwa_browser_access(python)' in ast.unparse(n)]
    assert len(guards) == 1 and ast.unparse(guards[0].test) == "stage == 'installed-pwa'"
    source = ast.unparse(prepare)
    assert source.index("'guest-attest'") < source.index('prepare_pwa_browser_access(python)')
    assert source.index('prepare_pwa_browser_access(python)') < source.index(".rename(GUEST / 'prepared-evidence')")
