"""Opt-in native Docker cleanup, with no mocked observation or teardown."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import uuid

import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_worker as worker
from amplifier_web.service import AppError, AppService
from amplifier_web.smart_tools import SmartToolsManager

pytestmark = pytest.mark.skipif(
    os.environ.get('CHECK_SUITE') != 'fresh-cleanup-native',
    reason='Only the fixed disposable hosted native-cleanup suite owns Docker here.')


class NoModelRuntime:
    async def start(self, *args, **kwargs):
        raise AssertionError('Native cleanup qualification must not start a model')

    async def close(self):
        pass


def command(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=kwargs.pop('timeout', 90), **kwargs).stdout.strip()


def docker(*args, **kwargs):
    return command('docker', '--host', 'unix:///var/run/docker.sock', *args, **kwargs)


def save(output, name, value):
    (output / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def tar_bytes(path, suffix):
    with tarfile.open(path) as archive:
        matches = [member for member in archive.getmembers()
                   if member.isfile() and member.name.rstrip('/').endswith(suffix)]
        assert len(matches) == 1, [member.name for member in archive.getmembers()]
        return archive.extractfile(matches[0]).read()


async def test_native_unknown_cleanup_keeps_history_and_preserves_current_files(monkeypatch):
    root = Path(os.environ['CLEANUP_NATIVE_ROOT']).resolve()
    output = Path(os.environ['CLEANUP_NATIVE_OUTPUT']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert sys.platform.startswith('linux') and os.environ.get('GITHUB_ACTIONS') == 'true'
    assert shutil.disk_usage(root).free >= 9 * 1024**3, 'Native cleanup requires 9 GiB free'
    assert owner.host_resources.local_target('unix:///var/run/docker.sock', {'dockerd'})['supported']
    command('docker', 'compose', 'version')
    image = json.loads(docker('image', 'inspect', owner.APPROVED_IMAGE))[0]
    assert image['Os'] == 'linux' and image['Architecture'] == 'amd64'
    assert command('git', 'rev-parse', 'HEAD') == os.environ['GITHUB_SHA']
    core = Path('.ci/dtu-core').resolve()
    assert command('git', '-C', str(core), 'rev-parse', 'HEAD') == owner.DTU_COMMIT
    installation_id = hashlib.sha256(owner.DTU_COMMIT.encode()).hexdigest()[:24]
    interpreter = root / 'app/smart-tools/installs' / installation_id / 'venv/bin/python'
    package = json.loads(command(str(interpreter), '-I', '-c',
        'import importlib.metadata as m,json; print(json.dumps({"version":m.version("digital-twin-universe"),"source":json.loads(m.distribution("digital-twin-universe").read_text("direct_url.json"))}))'))
    save(output, 'prerequisites.json', {'source': os.environ['GITHUB_SHA'],
        'coreCommit': owner.DTU_COMMIT, 'corePackage': package, 'image': owner.APPROVED_IMAGE,
        'imageId': image['Id'], 'docker': json.loads(docker('info', '--format', '{{json .}}')),
        'freeBytes': shutil.disk_usage(root).free})

    repository = root / 'fixture-source'
    repository.mkdir()
    command('git', '-C', str(repository), 'init', '-q')
    command('git', '-C', str(repository), 'config', 'user.email', 'fixture@example.invalid')
    command('git', '-C', str(repository), 'config', 'user.name', 'Native cleanup fixture')
    (repository / 'source.txt').write_text('original source\n')
    command('git', '-C', str(repository), 'add', 'source.txt')
    command('git', '-C', str(repository), 'commit', '-qm', 'native fixture')
    monkeypatch.setenv('AMPLIFIER_HOME', str(root / 'native-home'))
    app = AppService(root / 'app', NoModelRuntime(), workspace=repository)
    app.smart_tools = SmartToolsManager(app)
    source = app._new_session({'title': 'Native cleanup fixture'})
    app.state['sessions'] = [source]
    app.state['selectedSessionId'] = source['id']
    app._publish()
    # Seed only installation inventory for the actually locked, installed core.
    # Installation UI/model setup is not part of this cleanup qualification.
    app.smart_tools.state['installations'].append({'id': installation_id, 'status': 'installed',
        'commit': owner.DTU_COMMIT, 'repository': owner.REPOSITORY, 'extras': [],
        'python': str(interpreter)})
    raw_config = {'enabled': True, 'installationId': installation_id,
        'commit': owner.DTU_COMMIT, 'backend': 'docker',
        'dockerHost': 'unix:///var/run/docker.sock', 'image': owner.APPROVED_IMAGE,
        'maxRunning': 2, 'maxInstances': 2, 'minFreeBytes': 1024**3}
    (app.data_dir / 'test-environments.json').write_text(json.dumps(raw_config))
    (app.data_dir / 'test-environments.json').chmod(0o600)
    config, availability, reason = owner.configuration(app)
    assert availability == 'ready', reason
    workspace = next(row for row in app.state['workspaces'] if row['path'] == str(repository))
    scope = {'workspaceId': workspace['id'], 'sessionId': source['id']}

    async def act(action, row=None, request=None, **args):
        arguments = {**scope, 'requestId': request or action, **args}
        if row:
            arguments.update(id=row['id'], expectedRevision=row['revision'])
        result = await app.dispatch('workspace.environments.' + action, arguments,
                                    command_id='native-' + arguments['requestId'])
        return result['result']

    helper, foreign, row = None, None, None
    try:
        launched = await act('start', profileId='linux-python')
        save(output, 'launch.json', launched)
        assert launched['request']['state'] == 'completed', launched
        row = launched['resource']
        executed = await act('execute', row, command=
            "printf 'current workspace\\n' > /workspace/current.txt; "
            "printf 'current artifact\\n' > /artifacts/proof.txt")
        save(output, 'execute.json', executed)
        assert executed['request']['state'] == 'completed', executed
        stopped = await act('stop', executed['resource'])
        assert stopped['request']['state'] == 'completed', stopped
        row = stopped['resource']
        raw = owner.get(app.data_dir, source['id'], row['id'])
        directory = owner.storage.directory(app.data_dir, raw)
        source_before = (directory / 'source.tar').read_bytes()
        real_popen = subprocess.Popen
        terminated = []

        def interrupt_launched_helper(argv, *args, **kwargs):
            process = real_popen(argv, *args, **kwargs)
            if any(str(value).endswith('/test_environment_worker.py') for value in argv):
                # Fault injection only: terminate the actual canonical child.
                # Observation, local-process proof, exports and teardown remain real.
                os.kill(process.pid, signal.SIGKILL)
                terminated.append(process.pid)
            return process

        with monkeypatch.context() as injection:
            injection.setattr(owner.subprocess, 'Popen', interrupt_launched_helper)
            interrupted = await act('remove', row, request='interrupted-native-remove')
        save(output, 'interrupted.json', interrupted)
        assert len(terminated) == 1
        assert interrupted['request']['state'] == 'outcome_unknown', interrupted
        original = interrupted['request']
        row = (await act('reconcile', interrupted['resource'], request='observe-stopped'))['resource']
        assert row['status'] == 'stopped'
        original_bytes = json.dumps(original, sort_keys=True)
        old_retained = directory / ('retained-' + hashlib.sha256(original['requestId'].encode()).hexdigest())
        old_retained.mkdir()
        (old_retained / 'partial-export.tar').write_bytes(b'previous interrupted partial export')

        # A wrong-owner object under the same project must block before export.
        name = 'native-cleanup-foreign-' + uuid.uuid4().hex
        foreign = docker('create', '--name', name, '--network', 'none',
            '--label', 'com.docker.compose.project=' + raw['universeId'],
            '--label', 'io.amplifier.root-owner=foreign-fixture-owner',
            '--label', 'io.amplifier.operation-id=' + raw['launchOperationId'],
            '--label', 'io.amplifier.native-cleanup-fixture=' + name,
            owner.APPROVED_IMAGE, 'sleep', 'infinity')
        before = sorted(path.name for path in directory.glob('retained-*'))
        with pytest.raises(AppError):
            await act('finishCleanup', row, request='wrong-owner-refusal',
                      previousRequestId=original['requestId'])
        assert sorted(path.name for path in directory.glob('retained-*')) == before
        labels = json.loads(docker('inspect', foreign))[0]['Config']['Labels']
        assert labels['io.amplifier.native-cleanup-fixture'] == name
        docker('rm', foreign)
        foreign = None
        save(output, 'foreign-owner-refusal.json', {'refused': True, 'exportsUnchanged': True})

        # An actual canonical helper awaiting input is still a capable executor.
        helper = subprocess.Popen([str(interpreter), '-I', '-B', str(Path(worker.__file__).resolve())],
            cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={'PATH': os.defpath, 'HOME': str(directory), 'LANG': 'C.UTF-8'},
            start_new_session=True)
        with pytest.raises(AppError, match='previous local cleanup process'):
            await act('finishCleanup', row, request='active-helper-refusal',
                      previousRequestId=original['requestId'])
        assert helper.poll() is None
        assert sorted(path.name for path in directory.glob('retained-*')) == before
        helper.terminate()
        helper.communicate(timeout=10)
        assert helper.returncode is not None
        save(output, 'active-helper-refusal.json', {'refused': True,
            'pid': helper.pid, 'returncodeAfterSettlement': helper.returncode})
        helper = None

        completed = await act('finishCleanup', row, request='fresh-native-cleanup',
                              previousRequestId=original['requestId'])
        save(output, 'completed.json', completed)
        assert completed['request']['state'] == 'completed', completed
        assert completed['resource']['status'] == 'removed'
        assert completed['request']['result']['retention']['verified'] is True
        assert json.dumps(app.operations.requests.read(source['id'], original['requestId']), sort_keys=True) == original_bytes
        assert (directory / 'source.tar').read_bytes() == source_before
        assert (old_retained / 'partial-export.tar').read_bytes() == b'previous interrupted partial export'
        new_retained = directory / ('retained-' + hashlib.sha256(b'fresh-native-cleanup').hexdigest())
        assert tar_bytes(new_retained / 'workspace.tar', 'current.txt') == b'current workspace\n'
        assert tar_bytes(new_retained / 'artifacts.tar', 'proof.txt') == b'current artifact\n'
        assert not docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + raw['universeId'])
        for kind in ('volume', 'network'):
            assert not docker(kind, 'ls', '-q', '--filter',
                              'label=com.docker.compose.project=' + raw['universeId'])
        duplicate = await act('finishCleanup', row, request='fresh-native-cleanup',
                              previousRequestId=original['requestId'])
        assert duplicate['request'] == completed['request']
        assert len(list(directory.glob('retained-*'))) == 2
        save(output, 'proof.json', {'passed': True, 'originalUnknownUnchanged': True,
            'oldPartialExportUnchanged': True, 'currentWorkspaceVerified': True,
            'currentArtifactsVerified': True, 'absenceVerified': True,
            'duplicateReceiptUnchanged': True, 'source': os.environ['GITHUB_SHA']})
    finally:
        if helper is not None:
            helper.kill()
            helper.communicate(timeout=10)
        if foreign is not None:
            # Only this fixture's exact object, never an inferred project sweep.
            record = json.loads(docker('inspect', foreign))[0]
            assert record['Config']['Labels'].get('io.amplifier.native-cleanup-fixture') == name
            docker('rm', '-f', foreign)
        if row is not None:
            raw = owner.get(app.data_dir, source['id'], row['id'])
            folder = owner.storage.directory(app.data_dir, raw)
            save(output, 'final-resource.json', owner.public(app.data_dir, raw))
            shutil.copytree(folder, output / 'retained-owner', dirs_exist_ok=True)
        await app.close()
