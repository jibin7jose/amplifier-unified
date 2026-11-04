"""Fixed public-only CI launcher; never prepares or starts a canonical worker.

Only official uv metadata/wheel acquisition uses the job's network. The workload
runs after unshare --net, identity/capability drop and an empty environment. This
is a supporting experiment, not #1279 acceptance or a general runner interface.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
METADATA_URL = 'https://pypi.org/pypi/uv/0.13.6/json'
FILENAME = 'uv-0.13.6-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
URL = 'https://files.pythonhosted.org/packages/5c/43/0688ff5142934238f2812e348f4a24e09b57d48bfb38dd8f850325a0d9de/' + FILENAME
SIZE = 17333810
SHA256 = '22759b8895e1103578de6fb4cd27e94b4399d021023378d29f01333717751fba'
FIXTURE_SHA256 = 'ee88ac215191cf511a600f492083a765465dd3839d749a90505e1d4ba7b41aec'
HELPER_SHA256 = '76967354992ef6f60376d730afd9bb1518c624aef53d09e29f30c6fc6df5c316'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Public artifact redirects are not permitted')


def download(url, limit):
    # Never inherit proxies, auth handlers or job credentials. Only the two fixed
    # public origins above are requested; a redirect cannot change the origin.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=30) as response:
        require(response.url == url, 'Unexpected public artifact redirect')
        data = response.read(limit + 1)
    require(len(data) <= limit, 'Public artifact exceeds bound')
    return data


def main():
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'Use the fixed hosted job')
    parent = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(parent != ROOT and not parent.is_relative_to(ROOT), 'Use runner temporary storage')
    output = parent / 'omitted-acquisition-evidence'
    output.mkdir(mode=0o700, exist_ok=False)
    work = parent / 'omitted-acquisition'
    record = {'status': 'preparing', 'phase': 'readiness', 'fixtureStarted': False,
              'workerStarts': 0, 'modelCalls': 'not-started', 'rawLogs': 'public-synthetic-only'}
    child = None
    try:
        require(sys.version_info[:2] == (3, 13) and sys.flags.isolated and sys.flags.no_site
                and sys.flags.dont_write_bytecode, 'Requires Python3.13 -I -S -B')
        require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Requires Linux x86_64 runner')
        require(os.getuid() != 0 and os.getuid() == os.geteuid() and os.getgid() != 0,
                'Launcher must start as the ordinary runner identity')
        require(not work.exists(), 'No replay into an existing work directory')
        work.mkdir(mode=0o700)
        supply = work / 'supply'; supply.mkdir(mode=0o700)
        path = str(Path(sys.executable).parent) + ':/usr/bin:/bin'
        clean = {'PATH': path, 'LANG': 'C.UTF-8'}
        git = lambda *args: subprocess.check_output(['/usr/bin/git', *args], cwd=ROOT,
            env={**clean, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
            text=True, timeout=10).strip()
        sources = {name: digest(ROOT / name) for name in (
            'scripts/run_omitted_ref_acquisition.py', 'scripts/qualification_diagnostics.py',
            'tests/fixtures/omitted_ref_acquisition_experiment.py',
            'tests/fixtures/omitted_ref_worker_probe.py', '.github/workflows/updater-synthetic-acquisition.yml')}
        require(sources['tests/fixtures/omitted_ref_worker_probe.py'] == FIXTURE_SHA256
                and sources['scripts/qualification_diagnostics.py'] == HELPER_SHA256, 'Original fixture/helper changed')
        record.update(commit=git('rev-parse', 'HEAD'), tree=git('rev-parse', 'HEAD^{tree}'),
            runId=os.environ['GITHUB_RUN_ID'], runAttempt=os.environ['GITHUB_RUN_ATTEMPT'],
            sourceHashes=sources, python=sys.version, interpreter=sys.executable,
            machine=platform.machine(), uid=os.getuid(), gid=os.getgid())
        record['phase'] = 'public-tool-acquisition'
        metadata_bytes = download(METADATA_URL, 1024 * 1024)
        metadata = json.loads(metadata_bytes)
        entry, = [row for row in metadata['urls'] if row['filename'] == FILENAME]
        require(entry['url'] == URL and entry['size'] == SIZE and entry['digests']['sha256'] == SHA256
                and not entry['yanked'], 'Official uv metadata differs from inspected pin')
        (output / 'uv-metadata.json').write_bytes(metadata_bytes)
        wheel_bytes = download(URL, SIZE)
        require(len(wheel_bytes) == SIZE and hashlib.sha256(wheel_bytes).hexdigest() == SHA256, 'uv download identity differs')
        wheel = supply / FILENAME
        with wheel.open('xb') as stream:
            stream.write(wheel_bytes)
        record['tool'] = {'metadataURL': METADATA_URL, 'metadataSha256': hashlib.sha256(metadata_bytes).hexdigest(),
            'url': URL, 'filename': FILENAME, 'bytes': SIZE, 'sha256': SHA256,
            'publishedAt': entry['upload_time_iso_8601'], 'architecture': 'x86_64'}
        # The namespace identity is read immediately before this one launch. sudo
        # is only the setup transition; the workload is nonroot with all caps
        # dropped and no-new-privileges, independently asserted by the fixture.
        record['phase'] = 'synthetic-experiment'
        parent_netns = os.readlink('/proc/self/ns/net')
        parent_pidns = os.readlink('/proc/self/ns/pid')
        # The timeout process is PID 1 in a fresh PID namespace. When it exits,
        # the kernel kills every remaining namespace member, including separate
        # sessions. unshare waits for that init; --kill-child covers its loss.
        command = ['/usr/bin/sudo', '-n', '/usr/bin/unshare', '--net', '--pid',
            '--fork', '--kill-child=KILL', '--',
            '/usr/bin/setpriv', '--reuid', str(os.getuid()), '--regid', str(os.getgid()),
            '--clear-groups', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all',
            '--no-new-privs', '--pdeathsig=KILL',
            '/usr/bin/env', '-i', 'PATH=' + path, 'LANG=C.UTF-8',
            '/usr/bin/timeout', '--signal=TERM', '--kill-after=20s', '600s',
            sys.executable, '-I', '-S', '-B', str(ROOT / 'tests/fixtures/omitted_ref_acquisition_experiment.py'),
            '--root', str(work / 'fixture'), '--output', str(output / 'fixture'),
            '--parent-netns', parent_netns, '--parent-pidns', parent_pidns, '--wheel', str(wheel)]
        record.update(command=command, parentNetworkNamespace=parent_netns, parentPidNamespace=parent_pidns)
        (output / 'launcher.json').write_text(json.dumps(record, indent=2) + '\n')
        with (output / 'experiment.stdout').open('xb') as out, (output / 'experiment.stderr').open('xb') as err:
            child = subprocess.Popen(command, cwd=ROOT, env=clean, stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=err, start_new_session=True)
            record.update(fixtureStarted=True, launcherPid=child.pid)
            record['returncode'] = child.wait(timeout=640)
        record['namespaceSupervisorExited'] = True
        require(record['returncode'] == 0, 'Public synthetic experiment failed')
        result = json.loads((output / 'fixture/result.json').read_text())
        require(result['status'] == 'passed' and result['actualSyntheticWheelInstall']
                and result['processCleanup']['allOwnedGroupsAbsent'], 'Synthetic receipt is not a settled pass')
        record.update(status='passed', phase='complete', modelCalls=0)
    except BaseException as error:
        record.update(status='failed', errorType=type(error).__name__, message=str(error))
        if child is not None and child.poll() is None:
            record['cleanup'] = 'unknown; hosted job teardown required'
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=5)
            except BaseException as cleanup_error:
                record['cleanupErrorType'] = type(cleanup_error).__name__
        # No retries, downloads of other versions, source repairs or namespace fallback.
    finally:
        record['launcherExited'] = child is None or child.poll() is not None
        record['evidenceComplete'] = False
        if record['fixtureStarted']:
            try:
                final = json.loads((output / 'fixture/result.json').read_text())
                settled = (final.get('status') in ('passed', 'failed', 'blocked-readiness')
                           and final.get('processCleanup', {}).get('allOwnedGroupsAbsent') is True
                           and record.get('namespaceSupervisorExited') is True)
                record['cleanup'] = 'settled' if settled else 'unknown; partial evidence'
                record['evidenceComplete'] = settled
            except (OSError, ValueError):
                record['cleanup'] = 'unknown; final fixture receipt absent or unreadable'
            if not record['evidenceComplete']:
                record['status'] = 'failed'
        record['retainedWork'] = work.exists()
        record['physicalRunnerDestruction'] = 'GitHub-owned; not observed here'
        (output / 'launcher.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': record['status'], 'phase': record['phase'], 'returncode': record.get('returncode')}))
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
