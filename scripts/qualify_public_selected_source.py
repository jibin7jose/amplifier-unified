"""Opt-in hosted updater qualification; public packages plus lock-exact private Loop.

Uses the existing acquisition/containment helpers. No production source override,
provider request, repacked wheel, or synthetic module is part of this graph.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('omitted_adapter', ROOT / 'scripts/qualify_omitted_ref.py')
owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(owner)


def clean_environment(work, mirror):
    """Only fixed acquisition metadata enters the no-account worker process."""
    supplied = owner.mirror_seed_environment(work / 'uv-cache', mirror)
    keys = ('PATH', 'LANG', 'UV_PYTHON_DOWNLOADS', 'UV_NO_GITHUB_FAST_PATH',
            'UV_CACHE_DIR', 'GIT_TERMINAL_PROMPT', 'GIT_CONFIG_NOSYSTEM',
            'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0')
    env = {key: supplied[key] for key in keys if key in supplied}
    env.update(HOME=str(work / 'home'), LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=str(ROOT), GIT_CONFIG_GLOBAL=os.devnull,
               PUBLIC_SELECTED_WORK=str(work), PUBLIC_SELECTED_OUTPUT=str(work / 'proof'))
    return env


def bounded_junit(source, target):
    """Export test outcomes, not traceback text containing private dependency bodies."""
    original = ET.parse(source).getroot()
    root = ET.Element('testsuites')
    for suite in original.iter('testsuite'):
        safe = ET.SubElement(root, 'testsuite', {k: suite.attrib[k] for k in
            ('name', 'tests', 'failures', 'errors', 'skipped', 'time') if k in suite.attrib})
        for case in suite.findall('testcase'):
            item = ET.SubElement(safe, 'testcase', {k: case.attrib[k] for k in
                ('classname', 'name', 'time') if k in case.attrib})
            for tag in ('failure', 'error', 'skipped'):
                if case.find(tag) is not None:
                    ET.SubElement(item, tag, {'message': 'See bounded qualification facts; raw log retained privately.'})
    ET.ElementTree(root).write(target, encoding='utf-8', xml_declaration=True)


def remove_owned_work(work):
    """Retire only this settled disposable tree, including immutable source copies."""
    for root, dirs, _ in os.walk(work, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        os.chmod(root, 0o700, follow_symlinks=False)
    shutil.rmtree(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loop-source', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    work, output, loop = args.work.resolve(), args.output.resolve(), args.loop_source.resolve()
    assert sys.version_info[:2] == (3, 13)
    assert os.environ.get('GITHUB_ACTIONS') == 'true'
    assert not work.is_relative_to(ROOT) and not output.is_relative_to(work) and not work.is_relative_to(output)
    work.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=True)
    (work / 'proof').mkdir()
    (work / 'home').mkdir()
    status = {'status': 'preparing', 'mode': 'public-selected-source', 'commands': [],
              'accounts': False, 'modelCalls': 0, 'privateLoopException': True}
    owner.save(output / 'public-status.json', status)

    def run(label, command, env=None, timeout=900):
        record = {'label': label, 'returncode': None}
        status['commands'].append(record)
        owner.save(output / 'public-status.json', status)
        log = work / (label + '.private.log')
        try:
            with log.open('wb') as stream:
                result = owner.preflight_process(command, cwd=ROOT, env=env, stream=stream,
                                                  timeout=timeout, record=record)
            if result.returncode:
                raise RuntimeError(label + ' failed')
        finally:
            record.update(owner.command_log_facts(log, record['returncode']))
            record['rawLogExported'] = False
            owner.save(output / 'public-status.json', status)

    try:
        lock = tomllib.loads((ROOT / 'uv.lock').read_text())
        row, = [p for p in lock['package'] if p['name'] == owner.LOOP]
        pin = row['source']['git'].rsplit('#', 1)[1]
        owner.verified_git_source(loop, pin)
        assert subprocess.check_output(['git', '-C', str(loop), 'status', '--porcelain'], text=True) == ''
        mirror = work / 'loop-transport.git'
        run('private-loop-mirror', ['git', 'clone', '--bare', '--no-hardlinks', str(loop), str(mirror)])
        run('private-loop-pin', ['git', '-C', str(mirror), 'update-ref', 'refs/heads/main', pin])
        owner.verified_git_source(mirror, pin, 'refs/heads/main')
        sources = ('scripts/qualify_public_selected_source.py', 'scripts/qualify_omitted_ref.py',
                   'tests/fixtures/public_selected_source_probe.py', 'amplifier_web/updates.py',
                   'amplifier_web/runtime_source_alignment.py', 'amplifier_web/update_sequence.py',
                   'amplifier_web/runtime_qualification.py', 'amplifier_web/update_probe.py',
                   'amplifier_web/runtime_deps/pyproject.toml', 'uv.lock')
        owner.save(output / 'public-source.json', {
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
            'runId': os.environ['GITHUB_RUN_ID'], 'runAttempt': os.environ['GITHUB_RUN_ATTEMPT'],
            'loopCommit': pin, 'privateLoopTransport': 'lock-exact checked-out mirror; no credentials persisted',
            'sourceHashes': {name: owner.digest(ROOT / name) for name in sources}})
        env = clean_environment(work, mirror)
        run('source-policy-units', [str(ROOT / '.venv/bin/python'), '-m', 'pytest',
            'tests/test_selected_runtime_dependencies.py', 'tests/test_public_selected_source_harness.py',
            '-q', '--tb=short', '--junitxml=' + str(work / 'private-policy-junit.xml')], env=env, timeout=180)
        # Keep all descendants in an owned PID namespace, including detached uv/Git
        # helpers. Normal public acquisition networking remains available; no
        # credentials, agent sockets, vendor tokens or model execution are supplied.
        command = ['/usr/bin/sudo', '-n', '/usr/bin/unshare', '--pid', '--fork', '--mount-proc',
                   '--kill-child=KILL', '--', '/usr/bin/setpriv', '--reuid', str(os.getuid()),
                   '--regid', str(os.getgid()), '--clear-groups', '--bounding-set=-all',
                   '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs', '--pdeathsig=KILL',
                   '/usr/bin/env', '-i', *[key + '=' + value for key, value in env.items()],
                   str(ROOT / '.venv/bin/python'), '-m', 'pytest', '--noconftest', '-c', '/dev/null',
                   '-p', 'pytest_asyncio.plugin', '--import-mode=importlib',
                   str(ROOT / 'tests/fixtures/public_selected_source_probe.py'), '-q', '--tb=short',
                   '--junitxml=' + str(work / 'private-junit.xml')]
        # Plugin autoload would duplicate the explicitly selected asyncio plugin.
        command.insert(command.index(str(ROOT / '.venv/bin/python')), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1')
        run('public-manager', command, env={'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8'}, timeout=1500)
        status['status'] = 'passed'
    except BaseException as error:
        status.update(status='failed', errorType=type(error).__name__)
    finally:
        if (work / 'private-junit.xml').exists():
            bounded_junit(work / 'private-junit.xml', output / 'public-junit.xml')
            status['rawJunitSha256'] = owner.digest(work / 'private-junit.xml')
        if (work / 'private-policy-junit.xml').exists():
            bounded_junit(work / 'private-policy-junit.xml', output / 'public-policy-junit.xml')
            status['rawPolicyJunitSha256'] = owner.digest(work / 'private-policy-junit.xml')
        # The fixture writes only explicit bounded proof files into this directory.
        for path in (work / 'proof').glob('*.json'):
            assert path.stat().st_size <= 4 * 1024 * 1024
            shutil.copyfile(path, output / ('public-' + path.name))
        settled = all(r.get('processGroupSettled') is True for r in status['commands'])
        status['processesSettled'] = settled
        try:
            if settled:
                remove_owned_work(work)
        except OSError as error:
            status.update(status='failed', cleanupErrorType=type(error).__name__)
        status['ownedWorkRemoved'] = not work.exists()
        if not status['ownedWorkRemoved']:
            status['status'] = 'failed'
        owner.save(output / 'public-status.json', status)
    return 0 if status['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
