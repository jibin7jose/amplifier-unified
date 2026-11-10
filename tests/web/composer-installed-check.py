"""Focused managed-runner package proof; no network, Git metadata or host install.

Uses the existing browser runner and the managed multi-quote fresh-venv pattern.
Run after the bundled frontend build. Prepared dependencies remain read-only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path('/artifacts/composer-installed')
sys.path.insert(0, str(ROOT / 'scripts'))
from browser_check_runner import run_scenarios


def digest(data):
    return hashlib.sha256(data).hexdigest()


def manifest(root):
    result = {}
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(name for name in folders if name not in {'node_modules', '__pycache__'})
        for name in sorted(files):
            path = Path(directory) / name
            result[str(path.relative_to(root))] = digest(path.read_bytes())
    return result


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def command(name, args, *, cwd=ROOT, env=None):
    with (OUT / (name + '.log')).open('w') as stream:
        result = subprocess.run(args, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError(f'{name}: exit {result.returncode}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--header-only', action='store_true',
                        help='Fixed header-occlusion regression plus served-asset parity.')
    parser.add_argument('--markdown-only', action='store_true',
                        help='Markdown/GFM input and quote journeys plus served-asset parity.')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=False)
    result = {'status': 'running', 'limits': ['Read-only locked development dependencies, not minimal production dependency install', 'Synthetic runtime, no real provider/physical IME/audio']}
    write('result.json', result)
    try:
        contract = json.loads(Path(os.environ['AMPLIFIER_TEST_CONTRACT']).read_text())
        assert contract['profileId'] == 'browser-test'
        write('execution-contract.json', contract)
        # Hash actual copied inputs. Managed source has no Git metadata.
        inputs = {}
        for folder in ['.build/web-client/src', '.build/web-client/tests', 'tests/web', 'tests/fixtures']:
            inputs.update({folder + '/' + p: value for p, value in manifest(ROOT / folder).items()})
        for file in ['pyproject.toml', 'uv.lock', 'tests/web/package.json', 'tests/web/package-lock.json', '.build/web-client/package.json', '.build/web-client/package-lock.json', '.build/web-client/vite.config.js']:
            inputs[file] = digest((ROOT / file).read_bytes())
        source = {'inputs': inputs, 'static': manifest(ROOT / 'amplifier_web/static')}
        write('source.json', source)
        command('wheel-build', ['/opt/unified-prepared/build-venv/bin/python', '-m', 'build', '--wheel', '--no-isolation', '--outdir', str(OUT / 'wheel'), str(ROOT)])
        wheel, = (OUT / 'wheel').glob('*.whl')
        venv = OUT / 'venv'
        command('venv', ['uv', 'venv', '--python', sys.executable, str(venv)])
        site = venv / 'lib' / ('python' + '.'.join(map(str, sys.version_info[:2]))) / 'site-packages'
        (site / 'prepared-dependencies.pth').write_text(sysconfig.get_path('purelib') + '\n')
        python = venv / 'bin/python'
        command('install', ['uv', 'pip', 'install', '--python', str(python), '--no-deps', '--reinstall-package', 'amplifier-unified', str(wheel)])
        expected = {}
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.startswith('amplifier_web/') and not name.endswith('/'):
                    expected[name] = digest(archive.read(name))
        static = {p.removeprefix('amplifier_web/static/'): value for p, value in expected.items() if p.startswith('amplifier_web/static/')}
        assert static == source['static'], 'Wheel differs from built static'
        identity = {'wheelSha256': digest(wheel.read_bytes()), 'files': expected, 'static': static}
        write('wheel-identity.json', identity)
        env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}
        probe = '''import sys,json,hashlib
from pathlib import Path
import amplifier_core._engine,amplifier_foundation,aiohttp,amplifier_web.server
root=Path(amplifier_web.__file__).resolve().parent
expected=json.loads(Path(sys.argv[1]).read_text())
assert root.is_relative_to(Path(sys.argv[2])),str(root)
assert not any(Path(p or '.').resolve().is_relative_to(Path('/workspace')) for p in sys.path)
assert all(hashlib.sha256((root.parent/p).read_bytes()).hexdigest()==h for p,h in expected['files'].items())
print(json.dumps({'packageRoot':str(root),'verified':True,'fileCount':len(expected['files']),'wheelSha256':expected['wheelSha256']}))
'''
        command('identity', [str(python), '-I', '-c', probe, str(OUT / 'wheel-identity.json'), str(venv)], cwd=OUT, env=env)
        package = json.loads((OUT / 'identity.log').read_text())['packageRoot']
        harness = OUT / 'harness'
        shutil.copytree(ROOT / 'tests/fixtures', harness / 'tests/fixtures')
        shutil.copytree(ROOT / 'tests/web', harness / 'tests/web', ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
        for folder in ['src', 'tests']:
            shutil.copytree(ROOT / '.build/web-client' / folder, harness / '.build/web-client' / folder,
                            ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
        for name in ['package.json', 'package-lock.json']:
            shutil.copy2(ROOT / '.build/web-client' / name, harness / '.build/web-client' / name)
        (harness / '.build/web-client/node_modules').symlink_to(ROOT / '.build/web-client/node_modules', target_is_directory=True)
        (harness / 'tests/web/node_modules').symlink_to(ROOT / 'tests/web/node_modules', target_is_directory=True)
        (harness / '.venv').symlink_to(venv, target_is_directory=True)
        scenarios = []
        selected = (['markdown-composer', 'multi-quote', 'compact-composer'] if args.markdown_only else
                    ['composer-header', 'compact-composer'] if args.header_only else
                    ['composer-fidelity', 'composer-options', 'compact-composer', 'composer-primary-action', 'message-delivery'])
        result['selection'] = selected
        for name in selected:
            output = OUT / name
            scenarios.append({'id': name, 'command': ['node', f'tests/web/{name}-browser.mjs'],
                              'env': {'AMPLIFIER_TEST_PYTHON': str(python), 'AMPLIFIER_TEST_PACKAGE_ROOT': package,
                                      'PYTHONPATH': '', 'AMPLIFIER_TEST_ARTIFACTS': str(output), 'AMPLIFIER_TEST_OUTPUT': str(output)}})
        plan = {'schemaVersion': 1, 'scenarios': scenarios}
        code = run_scenarios(harness, plan, OUT / 'browser', budget=300, case_timeout=90, provenance={'inputs': inputs, 'wheel': identity['wheelSha256']})
        result.update(status='passed' if code == 0 else 'failed', browserExit=code, packageRoot=package, wheelSha256=identity['wheelSha256'], staticCount=len(static))
        if code == 0:
            proof = json.loads((OUT / 'compact-composer/compact-composer.json').read_text())
            served = proof.get('installedServedAssets')
            assert served and all(static.get(name) == value for name, value in served.items()), 'Positive served-byte proof missing'
            result['servedAssets'] = served
        write('result.json', result)
        return code
    except Exception as error:
        result.update(status='error', error=str(error)); write('result.json', result); raise


if __name__ == '__main__':
    raise SystemExit(main())
