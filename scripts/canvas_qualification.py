"""Fixed Canvas source + installed-wheel qualification. No arbitrary command input.

Uses the existing serial browser runner for owned process cleanup. Normal PR
checks remain separate; this opt-in path is not a new publication gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from browser_check_runner import run_scenarios

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/canvas-qualification'
PUBLIC = ROOT / 'output/canvas-qualification-public'
BROWSERS = ('canvas-chrome-browser.mjs', 'canvas-dirty-browser.mjs',
            'canvas-versions-browser.mjs',
            'destination-history-browser.mjs', 'automation-hub-browser.mjs',
            'compact-composer-browser.mjs', 'peer-preview-browser.mjs',
            'pin-rapid-reorder-browser.mjs')
UNITS = ('canvas-overview.test.mjs', 'shell-panels.test.mjs', 'work-shell.test.mjs',
         'canvas-reference.test.mjs', 'inline-media.test.mjs',
         'canvas-reload-guard.test.mjs', 'canvas-app-viewer.test.mjs', 'navigation-demand.test.mjs',
         'destination-history.test.mjs', 'destination-history-ui.test.mjs', 'api.test.mjs',
         'settings-drafts.test.mjs', 'automation-hub.test.mjs', 'schedule-editor.test.mjs',
         'submission-reset.test.mjs', 'composer.test.mjs', 'peer-groups.test.mjs',
         'peer-preview-pages.test.mjs', 'pin-order-settlement.test.mjs')

def selection(scope):
    """Fixed opt-in integration slice; full coverage remains the default."""
    if scope not in {'full', 'artifact-feedback'}:
        raise ValueError('Unknown Canvas qualification scope')
    return {
        'scope': scope,
        'browsers': list(BROWSERS if scope == 'full' else ('canvas-versions-browser.mjs',)),
        'units': list(UNITS if scope == 'full' else ('canvas-reference.test.mjs', 'inline-media.test.mjs')),
        'omittedBrowsers': [] if scope == 'full' else [name for name in BROWSERS if name != 'canvas-versions-browser.mjs'],
        'coverageNote': ('Full fixed Canvas qualification.' if scope == 'full' else
                         'Artifact feedback integration only; omitted journeys are not requalified or declared passing.'),
    }


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def checked(*args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def payload(folder):
    files = {}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise ValueError('Payload symlink')
        if path.is_file():
            files[path.relative_to(folder).as_posix()] = sha(path)
    if not files:
        raise ValueError('Empty payload')
    return files


def initialize():
    selected = selection(os.environ.get('CANVAS_QUALIFICATION_SCOPE', 'full'))
    OUT.mkdir(parents=True, exist_ok=False)
    tracked = checked('git', 'ls-files', '-z').split('\0')
    source = {name: sha(ROOT / name) for name in tracked if name and not name.startswith('amplifier_web/static/')}
    write(OUT / 'identity.json', {'revision': checked('git', 'rev-parse', 'HEAD'),
          'tree': checked('git', 'rev-parse', 'HEAD^{tree}'), 'sourceSha256': source,
          'runId': os.environ.get('GITHUB_RUN_ID'), 'attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
          'selection': selected, 'python': sys.version})
    write(OUT / 'summary.json', {'status': 'not_started', 'stages': [],
          'selection': selected,
          'scope': 'Synthetic runtime on hosted Linux; no physical touch/native desktop/screen reader claim'})


def imports():
    import importlib.metadata as metadata
    import amplifier_web
    import amplifier_core._engine as core
    import aiohttp
    print(json.dumps({'python': sys.version, 'package': amplifier_web.__file__,
          'core': metadata.version('amplifier-core'), 'nativeSha256': sha(Path(core.__file__)),
          'aiohttp': aiohttp.__version__}))


def prepare_installed():
    # Copy only the fixture harness: its sys.path insertion cannot import source.
    wheel, = sorted((OUT / 'dist').glob('*.whl'))
    venv = OUT / 'installed-venv'
    harness = OUT / 'installed-harness'
    # The relocated synthetic Automation fixture imports the existing locked
    # test helpers (including pytest); keep test support out of product imports.
    subprocess.run(['uv', 'export', '--locked', '--group', 'dev', '--no-emit-project',
                    '--format', 'requirements-txt', '--output-file', str(OUT / 'requirements.txt')], cwd=ROOT, check=True)
    subprocess.run(['uv', 'venv', '--python', sys.executable, str(venv)], check=True)
    # This is the complete uv.lock export, not a list of direct requirements.
    # Do not re-resolve transitive Git @main metadata against its pinned commit.
    subprocess.run(['uv', 'pip', 'install', '--python', str(venv / 'bin/python'),
                    '--no-deps', '--only-binary', 'amplifier-core', '-r', str(OUT / 'requirements.txt')], check=True)
    subprocess.run(['uv', 'pip', 'install', '--python', str(venv / 'bin/python'), '--no-deps', str(wheel)], check=True)
    subprocess.run(['uv', 'pip', 'check', '--python', str(venv / 'bin/python')], check=True)
    (harness / 'frontend').mkdir(parents=True)
    shutil.copytree(ROOT / 'tests', harness / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'frontend/tests', harness / 'frontend/tests')
    # Main's held-frame history oracle imports this pure command encoder in
    # Node. It is test support only: Chromium still loads the wheel's built UI.
    helpers = ('frontend/src/destination-history.js', 'frontend/src/settings-navigation.js')
    support = {}
    for name in helpers:
        helper = Path(name)
        (harness / helper).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / helper, harness / helper)
        assert sha(ROOT / helper) == sha(harness / helper)
        support[name] = sha(harness / helper)
    write(OUT / 'installed-test-support.json', {
        'files': support,
        'purpose': 'Node-only history/settings test helpers; not browser-served product code',
    })
    shutil.copy2(ROOT / 'frontend/package.json', harness / 'frontend/package.json')
    (harness / 'frontend/node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
    (harness / '.venv').symlink_to(venv, target_is_directory=True)
    assert not (harness / 'amplifier_web').exists()
    verify_installed()


def verify_installed():
    wheel, = sorted((OUT / 'dist').glob('*.whl'))
    venv = OUT / 'installed-venv'
    harness = OUT / 'installed-harness'
    toolchain = (harness / 'frontend/node_modules').resolve(strict=True)
    assert toolchain == (ROOT / 'frontend/node_modules').resolve(strict=True)
    assert (toolchain / 'playwright/package.json').is_file()
    assert shutil.which('node'), 'Installed renderer validation requires the prepared Node executable'
    write(OUT / 'installed-toolchain.json', {
        'nodeModules': str(toolchain), 'node': shutil.which('node'),
        'packageLockSha256': sha(ROOT / 'frontend/package-lock.json'),
        'playwrightPackageSha256': sha(toolchain / 'playwright/package.json'),
    })
    probe = '''import amplifier_web,hashlib,json,pathlib,sys,zipfile
package=pathlib.Path(amplifier_web.__file__).resolve().parent
assert package.is_relative_to(pathlib.Path(sys.prefix).resolve())
assert (package/'shell_validator.mjs').is_file(), 'Installed renderer validator is missing'
wheel=pathlib.Path(sys.argv[1]); count=0
with zipfile.ZipFile(wheel) as z:
 for name in z.namelist():
  if name.endswith('/') or '.dist-info/' in name: continue
  p=package.parent/name
  assert p.is_file() and hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256(z.read(name)).digest(),name
  count+=1
print(json.dumps({'package':str(package),'python':sys.version,'wheelSha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'payloadFilesChecked':count}))'''
    identity = json.loads(checked(str(venv / 'bin/python'), '-I', '-c', probe, str(wheel), cwd=harness))
    path = OUT / 'installed-identity.json'
    if path.exists():
        assert identity == json.loads(path.read_text()), 'Installed payload changed during journeys'
        write(OUT / 'installed-final-identity.json', identity)
    else:
        write(path, identity)
    write(OUT / 'wheel.json', {'name': wheel.name, 'sha256': sha(wheel), 'bytes': wheel.stat().st_size})
    with zipfile.ZipFile(wheel) as archive:
        static = {name.removeprefix('amplifier_web/static/'): hashlib.sha256(archive.read(name)).hexdigest()
                  for name in archive.namelist() if name.startswith('amplifier_web/static/') and not name.endswith('/')}
    assert static == json.loads((OUT / 'static.json').read_text()), 'Wheel static mismatch'


def run():
    record = json.loads((OUT / 'summary.json').read_text())
    if record['status'] != 'not_started':
        raise ValueError('Qualification already started; no replay')
    record['status'] = 'running'
    write(OUT / 'summary.json', record)
    python = str(ROOT / '.venv/bin/python')
    def stage(name, command, *, root=ROOT, seconds=180, env=None):
        entry = {'name': name, 'status': 'running'}
        record['stages'].append(entry);write(OUT / 'summary.json', record)
        plan = {'schemaVersion': 1, 'scenarios': [{'id': name, 'command': command, 'env': env or {}}]}
        rc = run_scenarios(root, plan, OUT / name, budget=seconds + 10, case_timeout=seconds, provenance=identity)
        entry.update(status='passed' if rc == 0 else 'failed', exitCode=rc)
        write(OUT / 'summary.json', record)
        cleanup = json.loads((OUT / name / 'results.json').read_text())['scenarios'][0]['cleanup']
        if cleanup['remaining']:
            raise RuntimeError('Uncertain process cleanup; no further scenarios')
        return rc == 0
    try:
        identity = json.loads((OUT / 'identity.json').read_text())
        selected = selection(identity['selection']['scope'])
        assert selected == identity['selection'] == record['selection'], 'Canvas qualification selection changed'
        assert identity['revision'] == checked('git', 'rev-parse', 'HEAD')
        assert all(sha(ROOT / name) == value for name, value in identity['sourceSha256'].items())
        if not stage('imports', [python, str(Path(__file__).resolve()), 'imports'], seconds=30):
            raise RuntimeError('Host imports unavailable')
        if not stage('browser-preflight', ['node', 'tests/canvas-readiness.mjs'], seconds=30):
            raise RuntimeError('Browser unavailable')
        stage('units', ['node', '--test', *['tests/' + name for name in selected['units']]], seconds=120)
        stage('artifact-feedback-contracts', [python, '-m', 'pytest', '-q',
              str(ROOT / 'tests/test_canvas_feedback.py'),
              str(ROOT / 'tests/test_canvas_versions.py'),
              str(ROOT / 'tests/test_canvas_views.py')], seconds=120)
        stage('selector-contracts', [python, '-m', 'pytest', '-q', *[str(ROOT / target) for target in (
              'tests/test_release_qualification.py::test_canvas_qualification_is_fixed_manual_and_installed_not_a_release_gate',
              'tests/test_release_qualification.py::test_app004_opt_in_is_fixed_manual_only_and_keeps_default_browser_budget',
              'tests/test_browser_check_runner.py::test_attachment_focus_workflow_retains_failures_and_default_coverage',
              'tests/test_browser_check_runner.py::test_destination_history_manual_route_is_fixed_and_preserves_default_coverage',
              'tests/test_mcp_boundary_checks.py::test_mcp_workflow_is_fixed_default_off_with_separate_bounded_job',
              'tests/test_release_qualification.py::test_compact_composer_is_fixed_manual_only_with_independent_failure_evidence',
              'tests/test_peer_qualification.py::test_peer_hosted_route_is_fixed_and_separate',
              'tests/test_browser_check_runner.py::test_compact_composer_peer_preview_and_artifact_keep_automatic_inventory_owners',
              'tests/test_browser_check_runner.py::test_source_browser_duplicates_have_one_owner_and_package_overlaps_remain',
              'tests/test_release_qualification.py::test_canvas_feedback_scope_is_fixed_and_preserves_full_coverage')]],
              seconds=120)
        if not stage('build', ['npm', 'run', 'build'], seconds=180):
            raise RuntimeError('Production build failed')
        write(OUT / 'static.json', payload(ROOT / 'amplifier_web/static'))
        shutil.make_archive(str(OUT / 'static'), 'zip', ROOT / 'amplifier_web/static')
        # Reuse the maintained importer contract, rather than an unverified
        # static overlay when recovering this exact build into its source PR.
        from frontend_build_artifact import create
        with tempfile.TemporaryDirectory(prefix='canvas-build-') as folder:
            output = Path(folder) / 'build'
            create(ROOT, output, os.environ)
            shutil.make_archive(str(OUT / 'frontend-build-output'), 'zip', output)
        for name in selected['browsers']:
            label = 'source-' + name.removesuffix('.mjs')
            stage(label, ['node', 'tests/' + name], seconds=240,
                  env={'AMPLIFIER_TEST_ARTIFACTS': str(OUT / label / 'artifacts'),
                       'AMPLIFIER_TEST_OUTPUT': str(OUT / label / 'artifacts'),
                       'AMPLIFIER_PROOF_DIR': str(OUT / label / 'artifacts'),
                       'AMPLIFIER_TEST_PYTHON': python})
        if not stage('wheel-build', ['uv', 'build', '--wheel', '--project', str(ROOT), '--out-dir', str(OUT / 'dist')], seconds=180):
            raise RuntimeError('Wheel build failed')
        if not stage('installed-setup', [python, str(Path(__file__).resolve()), 'prepare-installed'], seconds=300):
            raise RuntimeError('Installed wheel setup failed')
        harness = OUT / 'installed-harness'
        package = json.loads((OUT / 'installed-identity.json').read_text())['package']
        for name in selected['browsers']:
            label = 'installed-' + name.removesuffix('.mjs')
            stage(label, ['node', 'tests/' + name], root=harness, seconds=240,
                  env={'AMPLIFIER_TEST_ARTIFACTS': str(OUT / label / 'artifacts'),
                       'AMPLIFIER_TEST_OUTPUT': str(OUT / label / 'artifacts'),
                       'AMPLIFIER_PROOF_DIR': str(OUT / label / 'artifacts'),
                       'AMPLIFIER_TEST_PACKAGE_ROOT': package,
                       'AMPLIFIER_TEST_INSTALLED_STATIC': '1',
                       'AMPLIFIER_TEST_PYTHON': str(OUT / 'installed-venv/bin/python'), 'PYTHONPATH': '',
                       'AMPLIFIER_SHELL_NODE_MODULES': str(harness / 'frontend/node_modules')})
        stage('installed-final-identity', [python, str(Path(__file__).resolve()), 'verify-installed'], seconds=30)
        assert payload(ROOT / 'amplifier_web/static') == json.loads((OUT / 'static.json').read_text())
        assert all(sha(ROOT / name) == value for name, value in identity['sourceSha256'].items())
        record['status'] = 'passed' if all(row['status'] == 'passed' for row in record['stages']) else 'failed'
    except Exception as error:
        record.update(status='failed', error=str(error))
    finally:
        write(OUT / 'summary.json', record)
    return 0 if record['status'] == 'passed' else 1


def retain():
    # Raw process exits and counts stay unchanged; map only runner/private paths.
    PUBLIC.mkdir(parents=True, exist_ok=False)
    replacements = [(str(ROOT), '<checkout>'), (os.environ.get('RUNNER_TEMP', ''), '<runner-temp>'), (str(Path.home()), '<runner-home>')]
    rows = []
    for path in sorted(OUT.rglob('*')):
        name = path.relative_to(OUT)
        if any(part in {'installed-venv', 'installed-harness'} for part in name.parts) or path.is_dir():
            continue
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError('Unsafe evidence link')
        if path.suffix not in {'.json', '.log', '.txt', '.png', '.whl', '.zip'} or path.name == 'trace.zip':
            continue
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError('Oversized evidence')
        data = path.read_bytes(); original = hashlib.sha256(data).hexdigest()
        if path.suffix in {'.json', '.log', '.txt'}:
            text = data.decode()
            for old, new in replacements:
                if old: text = text.replace(old, new)
            data = text.encode()
        dest = PUBLIC / name; dest.parent.mkdir(parents=True, exist_ok=True);dest.write_bytes(data)
        rows.append({'path': name.as_posix(), 'originalSha256': original, 'publishedSha256': hashlib.sha256(data).hexdigest()})
    write(PUBLIC / 'retention.json', {'files': rows, 'rawTrace': 'withheld (synthetic requests may contain runner paths)',
          'screenshots': 'synthetic fixture; saved pixels are not visual review'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['initialize', 'imports', 'prepare-installed', 'verify-installed', 'run', 'retain'])
    args = parser.parse_args()
    raise SystemExit(globals()[args.operation.replace('-', '_')]() or 0)
