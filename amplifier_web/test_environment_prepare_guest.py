"""Fixed dependency preparation recipe. Runs only inside the owned build guest.

This file is copied as data into a build guest, never run in the app host. Source
dependency installation can execute package build code there, with outbound
network but without host files, inherited credentials or a container socket.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tomllib
import time
import urllib.request
from pathlib import Path

PREFIX = Path('/opt/unified-prepared')
UV_VERSION = '0.9.18'
NODE_VERSION = '22.16.0'


def run(*args, cwd=None, capture=False):
    return subprocess.check_output(args, cwd=cwd, text=True) if capture else subprocess.run(args, cwd=cwd, check=True)


def build_requirements(value):
    if (not isinstance(value, list) or len(value) > 64
            or any(not isinstance(item, str) or not item.strip() or len(item) > 2048
                   or item.startswith('-') or '\x00' in item for item in value)
            or sum(map(len, value)) > 16384):
        raise ValueError('Python build requirements must be a bounded list of package requirements.')
    return value


def prepare_build(uv, python, project, prefix):
    """Resolve packaging separately; never add build dependencies to locked tests."""
    project, prefix = Path(project), Path(prefix)
    settings = tomllib.loads((project / 'pyproject.toml').read_text())
    backend = settings.get('build-system')
    if backend is None:
        return {'available': False, 'reason': 'This source declares no Python build backend.'}
    requires = build_requirements(backend.get('requires'))
    if not isinstance(backend.get('build-backend'), str) or len(backend['build-backend']) > 512:
        raise ValueError('The Python project must declare its build backend.')
    environment = prefix / 'build-venv'
    run(uv, 'venv', '--python', str(python), str(environment))
    executable = str(environment / 'bin/python')
    run(uv, 'pip', 'install', '--python', executable, '--', 'build', *requires)
    # Backend-declared dynamic wheel requirements are prefetched in the owned
    # networked builder. This is prerequisite discovery, not a second wheel build.
    requirements_file = prefix / 'build-requirements.json'
    probe = ("import build,json,sys; "
             "builder=build.ProjectBuilder(sys.argv[1]); "
             "json.dump(sorted(builder.get_requires_for_build('wheel')),open(sys.argv[2],'w'))")
    run(executable, '-c', probe, str(project), str(requirements_file))
    if requirements_file.stat().st_size > 32768:
        raise ValueError('Python build requirements exceed their observation bound.')
    dynamic = build_requirements(json.loads(requirements_file.read_text()))
    if dynamic:
        run(uv, 'pip', 'install', '--python', executable, '--', *dynamic)
    run(executable, '-c', "import build,sys; missing=build.ProjectBuilder(sys.argv[1]).check_dependencies('wheel'); assert not missing, missing", str(project))
    return {'available': True, 'python': executable, 'backend': backend['build-backend'],
            'command': executable + ' -m build --wheel --no-isolation --outdir /artifacts/dist /workspace',
            'requirements': requires, 'dynamicRequirements': dynamic,
            'packages': run(uv, 'pip', 'freeze', '--python', executable, capture=True).splitlines()}


def optional_build(uv, python, project, prefix):
    """Packaging failure does not remove a successfully prepared test capability."""
    started = time.monotonic()
    try:
        result = prepare_build(uv, python, project, prefix)
    except (subprocess.CalledProcessError, ValueError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.returncode < 0:
            raise  # A signalled helper is not an ordinary packaging rejection.
        # Full bounded subprocess diagnostics remain in the retained builder log.
        # Cancellation, timeout, helper loss and filesystem failures still escape.
        result = {'available': False,
                  'reason': 'Python packaging prerequisites are unavailable. Source checks can run; inspect the retained preparation log before building.'}
    diagnostic = {'status': 'ready' if result['available'] else 'unavailable',
                  'elapsedSeconds': time.monotonic() - started,
                  **({'reason': result['reason']} if 'reason' in result else {})}
    (Path(prefix) / 'build-preparation.json').write_text(json.dumps(diagnostic, sort_keys=True))
    return result


def verify_contract(contract):
    """Cheap checks fail before publishing an unusable prepared image."""
    python = contract['python']
    run(python, '-c', 'import sys; assert sys.version_info >= (3, 13)')
    # Storage's fail-closed reference guard needs this exact process listing.
    # Capture it without publishing command lines in preparation diagnostics.
    run('ps', '-axo', 'command=', capture=True)
    if contract.get('pythonBuild', {}).get('available'):
        run(contract['pythonBuild']['python'], '-c', 'import build')
    if contract['profileId'] == 'browser-test':
        run(str(PREFIX / 'node-runtime/bin/node'), '--version')
        if not (PREFIX / 'node').is_dir() or not (PREFIX / 'browsers').is_dir():
            raise ValueError('The prepared browser dependencies are incomplete.')


def restore_browser(plan, uv):
    """Restore only retained wheels after locked sync removes extra packages."""
    saved = json.loads((PREFIX / 'inventory.json').read_text())
    node = saved.get('node', {})
    wheels = saved.get('browserWheels')
    if (saved.get('profileId') != 'browser-test' or node.get('playwright') != plan['playwrightVersion']
            or node.get('version') != 'v' + NODE_VERSION or not isinstance(wheels, list)
            or not 1 <= len(wheels) <= 32 or any(not isinstance(wheel, dict) for wheel in wheels)
            or ('nodeProjects' in plan and node.get('projects') != plan['nodeProjects'])):
        raise ValueError('The reusable browser inventory changed.')
    folder = PREFIX / 'browser-wheels'
    for wheel in wheels:
        name = wheel.get('filename')
        if (not isinstance(name, str) or Path(name).name != name or not name.endswith('.whl')
                or (folder / name).is_symlink()
                or hashlib.sha256((folder / name).read_bytes()).hexdigest() != wheel.get('sha256')):
            raise ValueError('The retained browser wheel changed.')
    if len({wheel['filename'] for wheel in wheels}) != len(wheels) or set(folder.iterdir()) != {folder / wheel['filename'] for wheel in wheels}:
        raise ValueError('The retained browser wheel set changed.')
    python = str(PREFIX / 'venv/bin/python')
    probe = 'import importlib.metadata,json; print(json.dumps({d.metadata["Name"].lower().replace("_", "-"):d.version for d in importlib.metadata.distributions()}))'
    locked = json.loads(run(python, '-c', probe, capture=True))
    constraints = PREFIX / 'python-base-constraints.txt'
    constraints.write_text(''.join(name + '==' + version + '\n' for name, version in sorted(locked.items())))
    run(uv, 'pip', 'install', '--python', python, '--no-index', '--find-links', str(folder),
        '--constraint', str(constraints), 'playwright==' + plan['playwrightVersion'])
    installed = json.loads(run(python, '-c', probe, capture=True))
    if any(installed.get(name) != version for name, version in locked.items()):
        raise ValueError('Browser dependency preparation changed a locked Python project dependency.')
    return {'node': node, 'browserWheels': wheels}


def prepare_node_projects(plan, workspace=Path('/workspace')):
    """Install each lock in its own directory; only browser tools choose Python's Playwright."""
    projects = plan.get('nodeProjects', [{'directory': plan['nodeDirectory'],
                                        'key': plan['nodeKey'],
                                        'manifestDirectory': plan['nodeDirectory']}])
    for project in projects:
        root = workspace / project['manifestDirectory']
        run('npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', cwd=root)
    installed = set()
    for project in projects:
        browser = workspace / project['manifestDirectory']
        cli = browser / 'node_modules/playwright/cli.js'
        if not cli.is_file():
            if project['key'] == plan['nodeKey']:
                raise ValueError('The locked browser project must include Playwright.')
            continue
        version = json.loads(cli.with_name('package.json').read_text())['version']
        if version not in installed:
            run('node', str(cli), 'install', '--with-deps', 'chromium', cwd=browser)
            installed.add(version)
    return projects


def retain_node_projects(projects, workspace=Path('/workspace')):
    for project in projects:
        target = PREFIX / 'node' / project['key']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(workspace / project['manifestDirectory'] / 'node_modules'), str(target))


def main():
    plan = json.loads(Path('/prepare/plan.json').read_text())
    os.environ.clear()
    # A warm clone can start this recipe before systemd's boot cleanup of /tmp.
    # Keep interpreter probes, caches and downloads within the owned build tree;
    # both adapters remove /prepare before publishing the prepared image.
    os.environ.update(PATH='/usr/local/bin:/usr/bin:/bin', HOME='/prepare/build-home', TMPDIR='/prepare/tmp',
                      LANG='C.UTF-8', DEBIAN_FRONTEND='noninteractive',
                      GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
                      PIP_CONFIG_FILE='/dev/null', UV_NO_CONFIG='1',
                      UV_PROJECT_ENVIRONMENT=str(PREFIX / 'venv'),
                      UV_PYTHON_INSTALL_DIR=str(PREFIX / 'python'),
                      PLAYWRIGHT_BROWSERS_PATH=str(PREFIX / 'browsers'))
    Path(os.environ['HOME']).mkdir(mode=0o700, exist_ok=True)
    Path(os.environ['TMPDIR']).mkdir(mode=0o700, exist_ok=True)
    PREFIX.mkdir(parents=True, exist_ok=True)
    reused = plan.get('dependencyMode') == 'finalize'
    layer = plan.get('dependencyMode') == 'layer'
    if not reused:
        run('apt-get', 'update')
        run('apt-get', 'install', '-y', '--no-install-recommends', 'ca-certificates', 'git',
            'build-essential', 'python3', 'python3-venv', 'pkg-config', 'libffi-dev', 'procps')
        run('python3', '-m', 'venv', str(PREFIX / 'bootstrap'))
    bootstrap = str(PREFIX / 'bootstrap/bin/python')
    if not reused:
        run(bootstrap, '-m', 'pip', '--isolated', 'install', '--index-url',
            'https://pypi.org/simple', 'uv==' + UV_VERSION)
    uv = str(PREFIX / 'bootstrap/bin/uv')
    # Install an exact-source wheel for package metadata and console scripts;
    # virtual projects naturally install dependencies only. Python source checks
    # still prefer the exact workspace through the executor's PYTHONPATH.
    # The manifest's default groups are honored, not every optional integration.
    if plan['pythonProject']:
        run(uv, 'sync', '--locked', '--no-editable', *(['--no-install-local'] if layer else []), '--python',
            '3.13', cwd='/workspace')
    else:
        run(uv, 'venv', '--python', '3.13', str(PREFIX / 'venv'))
    inventory = {'profileId': plan['profileId'], 'preparationId': plan['preparationId'],
                 'sourceSha256': plan['sourceSha256'], 'lockfiles': plan['lockfiles'],
                 'uv': run(uv, '--version', capture=True).strip(),
                 'python': run(str(PREFIX / 'venv/bin/python'), '--version', capture=True).strip()}
    contract = dict(plan.get('executionContract', {}))
    contract['pythonBuild'] = {'available': False, 'reason': 'This source declares no Python build backend.'}
    if plan['pythonProject'] and not layer:
        build = optional_build(uv, PREFIX / 'venv/bin/python', '/workspace', PREFIX)
        inventory['pythonBuild'] = build
        contract['pythonBuild'] = {key: build[key] for key in ('available', 'reason', 'python', 'backend', 'command') if key in build}
    if not layer:
        inventory['executionContract'] = contract
    else:
        # The seed has never received the source archive. Do not publish a root
        # package, build environment, task plan or execution contract in it.
        name = plan['dependencyProject']
        run(str(PREFIX / 'venv/bin/python'), '-c',
            'import importlib.metadata as m,re,sys; assert not any(re.sub(r"[-_.]+","-",d.metadata["Name"].lower())==sys.argv[1] for d in m.distributions())', name)
        inventory['dependencyKey'] = plan['dependencyKey']
    if plan['profileId'] == 'browser-test' and reused:
        inventory.update(restore_browser(plan, uv))
    elif plan['profileId'] == 'browser-test':
        architecture = {'aarch64': 'arm64', 'x86_64': 'x64'}.get(os.uname().machine)
        if not architecture:
            raise ValueError('This browser profile supports Linux arm64 and x64.')
        name = 'node-v' + NODE_VERSION + '-linux-' + architecture
        filename = name + '.tar.xz'
        base = 'https://nodejs.org/dist/v' + NODE_VERSION + '/'
        sums = urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=60).read().decode()
        expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] == filename)
        target = Path(os.environ['TMPDIR']) / filename
        with urllib.request.urlopen(base + filename, timeout=120) as source, target.open('wb') as output:
            shutil.copyfileobj(source, output)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('The fixed Node runtime download failed its checksum.')
        with tarfile.open(target) as archive:
            archive.extractall(PREFIX, filter='data')
        (PREFIX / name).rename(PREFIX / 'node-runtime')
        os.environ['PATH'] = str(PREFIX / 'node-runtime/bin') + ':' + os.environ['PATH']
        projects = prepare_node_projects(plan)
        # Python browser probes use the same Playwright release as the locked
        # JavaScript project. Resolve its platform wheels once, retain their
        # exact hashes, then install only from that local wheel set.
        wheels = PREFIX / 'browser-wheels'
        wheels.mkdir()
        package_probe = 'import importlib.metadata,json; print(json.dumps({d.metadata["Name"].lower().replace("_", "-"):d.version for d in importlib.metadata.distributions()}))'
        locked_packages = json.loads(run(str(PREFIX / 'venv/bin/python'), '-c', package_probe, capture=True))
        constraints = PREFIX / 'python-base-constraints.txt'
        constraints.write_text(''.join(name + '==' + version + '\n' for name, version in sorted(locked_packages.items())))
        run(bootstrap, '-m', 'pip', '--isolated', 'download', '--only-binary=:all:',
            '--index-url', 'https://pypi.org/simple', '--dest', str(wheels),
            '--constraint', str(constraints), '--python-version', '3.13', 'playwright==' + plan['playwrightVersion'])
        wheel_lock = [{'filename': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                      for path in sorted(wheels.iterdir())]
        run(uv, 'pip', 'install', '--python', str(PREFIX / 'venv/bin/python'),
            '--no-index', '--find-links', str(wheels), '--constraint', str(constraints),
            'playwright==' + plan['playwrightVersion'])
        installed = json.loads(run(str(PREFIX / 'venv/bin/python'), '-c', package_probe, capture=True))
        if any(installed.get(name) != version for name, version in locked_packages.items()):
            raise ValueError('Browser dependency preparation changed a locked Python project dependency.')
        inventory['browserWheels'] = wheel_lock
        if not layer:
            shutil.rmtree(wheels)
        run('apt-get', 'install', '-y', '--no-install-recommends', 'xvfb', 'xauth', 'libxss1', 'x11-utils')
        retain_node_projects(projects)
        inventory['node'] = {'version': run('node', '--version', capture=True).strip(),
                             'archiveSha256': actual, 'playwright': plan['playwrightVersion'],
                             'projects': projects}
    inventory['pythonPackages'] = run(uv, 'pip', 'freeze', '--python', str(PREFIX / 'venv/bin/python'), capture=True).splitlines()
    inventory['systemPackages'] = run('dpkg-query', '-W', '-f=${Package}=${Version}\n', capture=True).splitlines()
    inventory['preparedFiles'] = {}
    directories = ('venv', 'bootstrap', 'python') if layer else ('browsers', 'node', 'build-venv')
    if layer and plan['profileId'] == 'browser-test':
        directories += ('node-runtime', 'node', 'browsers', 'browser-wheels')
    for directory in directories:
        digest = hashlib.sha256()
        for path in sorted((PREFIX / directory).rglob('*')) if (PREFIX / directory).exists() else []:
            digest.update(str(path.relative_to(PREFIX)).encode())
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
        inventory['preparedFiles'][directory] = digest.hexdigest()
    if not layer:
        verify_contract(contract)
        (PREFIX / 'execution-contract.json').write_text(json.dumps(contract, sort_keys=True))
    (PREFIX / 'inventory.json').write_text(json.dumps(inventory, sort_keys=True))
    if not layer:
        shutil.copyfile('/prepare/initialize.py', PREFIX / 'initialize.py')
        (PREFIX / 'plan.json').write_text(json.dumps(plan, sort_keys=True))
    # Keep all prepared dependencies accessible to the unprivileged check user.
    for root, directories, files in os.walk(PREFIX):
        for name in directories:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o555)
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o444)
    shutil.rmtree(os.environ['HOME'])
    shutil.rmtree('/root/.cache', ignore_errors=True)
    shutil.rmtree('/var/lib/apt/lists', ignore_errors=True)
    target = locals().get('target')
    if target:
        target.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
