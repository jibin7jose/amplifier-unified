"""Fixed, focused multi-quote qualification inside the owned managed guest only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--installed', action='store_true')
    args = parser.parse_args()
    managed = ROOT == Path('/workspace') and os.environ.get('UV_OFFLINE') == '1'
    hosted = os.environ.get('GITHUB_ACTIONS') == 'true' and ROOT == Path(os.environ.get('GITHUB_WORKSPACE', ''))
    if not (managed or hosted):
        raise SystemExit('Run only in the owned managed guest or the fixed GitHub workflow; never on the host.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = []

    def save():
        (output / 'results.json').write_text(json.dumps(results, indent=2))

    # The managed export is a measured source tree, not a Git checkout. Reuse
    # the standard scenario executor without inventing a revision or Git repo.
    if os.environ.get('AMPLIFIER_MULTI_QUOTE_SCENARIOS'):
        from browser_check_runner import run_scenarios
        plan_path = Path(os.environ['AMPLIFIER_MULTI_QUOTE_SCENARIOS'])
        manifest = Path(os.environ['AMPLIFIER_MULTI_QUOTE_SOURCE_MANIFEST'])
        provenance = {'sourceKind': 'managed-snapshot', 'revision': None, 'tree': None,
                      'sourceManifestSha256': sha(manifest.read_bytes()),
                      'planSha256': sha(plan_path.read_bytes()),
                      'runnerSha256': sha((ROOT / 'scripts/browser_check_runner.py').read_bytes())}
        return run_scenarios(ROOT, json.loads(plan_path.read_text()), output,
                             budget=180, provenance=provenance)

    def run(name, command, *, cwd=ROOT, env=None, stdin=None, expected=0):
        started = time.monotonic()
        try:
            result = subprocess.run(command, cwd=cwd, env=env, input=stdin, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
            code, text = result.returncode, result.stdout
        except subprocess.TimeoutExpired as error:
            code, text = 124, str(error.stdout or '') + '\nCHECK TIMEOUT'
        except OSError as error:
            code, text = 127, str(error)
        (output / (name + '.log')).write_text(text)
        results.append({'name': name, 'command': command, 'exitCode': code,
                        'expectedExit': expected, 'passed': code == expected,
                        'seconds': round(time.monotonic() - started, 3)})
        save()
        print(name, 'exit', code, text[-2500:], flush=True)
        return code, text

    # Managed source exports deliberately omit .git. The host snapshot receipt
    # supplies commit/tree identity; this manifest measures actual guest bytes.
    excluded = {'.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache', 'output'}
    source = {}
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in excluded]
        for name in files:
            path = Path(folder) / name
            if path.is_file():
                source[str(path.relative_to(ROOT))] = sha(path.read_bytes())
    (output / 'source.json').write_text(json.dumps({'files': source}, indent=2))
    run('backend', [sys.executable, '-m', 'pytest', '-q', '--tb=short',
        'tests/test_multi_quote.py', 'tests/test_message_interactions.py',
        '--junitxml=' + str(output / 'backend.xml')])
    run('coverage-contracts', [sys.executable, '-m', 'pytest', '-q', '--tb=short',
        'tests/test_browser_check_runner.py', 'tests/test_release_qualification.py',
        'tests/test_peer_qualification.py', 'tests/test_compact_composer_checks.py',
        '--junitxml=' + str(output / 'coverage-contracts.xml')])
    units = ['multi-quote', 'markdown-composer', 'draft-attach', 'draft-journal',
             'pending-view', 'message-render-stability', 'message-reaction-feedback', 'composer',
             'message-quote-selection', 'canvas-reference']
    unit_code, _ = run('model-units', ['node', '--test', *['tests/' + name + '.test.mjs' for name in units]], cwd=ROOT / '.build/web-client')
    model = ROOT / '.build/web-client/src/markdown-composer.js'
    original = model.read_bytes()
    if unit_code == 0:
        before = 'if(direction===0)tr=tr.delete(here.pos,here.pos+here.node.nodeSize);'
        assert original.decode().count(before) == 1
        try:
            model.write_text(original.decode().replace(before, 'if(direction===0){tr=tr.delete(here.pos,here.pos+here.node.nodeSize);tr=tr.delete(1,2);}'))
            code, text = run('negative-commentary-removal', ['node', '--test', '--test-name-pattern=three references accumulate',
                'tests/multi-quote.test.mjs'], cwd=ROOT / '.build/web-client', expected=1)
            results[-1]['passed'] = code == 1 and 'quote removal preserves every commentary segment' in text
            results[-1]['namedAssertion'] = 'quote removal preserves every commentary segment'
        finally:
            model.write_bytes(original)
        results[-1]['sourceRestored'] = sha(model.read_bytes()) == sha(original)
        results[-1]['restoredSha256'] = sha(original)
        results[-1]['passed'] &= results[-1]['sourceRestored']
        save()
        mapping = ROOT / '.build/web-client/src/message-quote-selection.js'
        mapping_original = mapping.read_bytes()
        before = b"source.slice(at,node.type==='text'?end:at+value.length)!==value"
        assert mapping_original.count(before) == 1
        mapping_mutant = mapping_original.replace(before, b'source.slice(at,at+value.length)!==value')
        try:
            mapping.write_bytes(mapping_mutant)
            code, text = run('negative-selection-mapping', ['node', '--test', '--test-name-pattern=unsupported entities',
                'tests/message-quote-selection.test.mjs'], cwd=ROOT / '.build/web-client', expected=1)
            results[-1]['passed'] = code == 1 and 'first **&amp;** last' in text
            results[-1]['namedAssertion'] = 'first **&amp;** last'
        finally:
            mapping.write_bytes(mapping_original)
            restored = mapping.read_bytes()
            results.append({'name': 'selection-mapping-source-restoration', 'passed': restored == mapping_original,
                            'originalSha256': sha(mapping_original), 'mutatedSha256': sha(mapping_mutant),
                            'restoredSha256': sha(restored)})
            save()
    else:
        results.append({'name': 'negative-commentary-removal', 'passed': False, 'status': 'not-run', 'reason': 'Baseline units failed'})
        results.append({'name': 'negative-selection-mapping', 'passed': False, 'status': 'not-run', 'reason': 'Baseline units failed'})
        save()
    build, _ = run('production-build', [sys.executable, 'scripts/web_client_build.py', 'build'])
    browser_env = {**os.environ, 'AMPLIFIER_TEST_PYTHON': sys.executable,
                   'AMPLIFIER_PROOF_DIR': str(output / 'source-browser')}
    ordinary_env = {key: value for key, value in browser_env.items() if key != 'AMPLIFIER_QUOTE_REFERENCE'}
    if build == 0:
        browser_code, _ = run('source-browser', ['node', 'tests/web/multi-quote-browser.mjs'], env=browser_env)
        if browser_code == 0:
            # Hide only the header: preserve its geometry so the unchanged
            # visible-excerpt/hit-test assertion, not a bounds error, must fail.
            css = ROOT / '.build/web-client/src/message-interactions.css'
            css_original = css.read_bytes()
            before = b'#amp-one .a-composer-quote-head{display:flex;'
            assert css_original.count(before) == 1
            css_mutant = css_original.replace(before, before + b'visibility:hidden;')
            try:
                css.write_bytes(css_mutant)
                negative_build, _ = run('negative-quote-alignment-build', [sys.executable, 'scripts/web_client_build.py', 'build-development'])
                negative_proof = output / 'negative-quote-alignment'
                if negative_build == 0:
                    code, _ = run('negative-quote-alignment', ['node', 'tests/web/multi-quote-browser.mjs'],
                                  env={**ordinary_env, 'AMPLIFIER_PROOF_DIR': str(negative_proof)}, expected=1)
                    named = 'first quote scrolled: visible quote has reachable source/move/remove controls'
                    failure_path = negative_proof / 'failure.json'
                    failure = json.loads(failure_path.read_text()) if failure_path.is_file() else {}
                    results[-1]['passed'] = code == 1 and failure.get('message') == named
                    results[-1]['namedAssertion'] = named
                    results[-1]['actualFailure'] = failure.get('message')
                else:
                    results.append({'name': 'negative-quote-alignment', 'passed': False,
                                    'status': 'not-run', 'reason': 'Mutant production build failed'})
            finally:
                css.write_bytes(css_original)
                restored = css.read_bytes()
                results.append({'name': 'quote-alignment-source-restoration', 'passed': restored == css_original,
                                'originalSha256': sha(css_original), 'mutatedSha256': sha(css_mutant),
                                'restoredSha256': sha(restored)})
                save()
            # Mutate actual navigation, not expected data: End now moves to the
            # beginning, placing attempted commentary before the first quote.
            model_original = model.read_bytes()
            before = "event.key==='Home'?TextSelection.atStart(view.state.doc):TextSelection.atEnd(view.state.doc)"
            assert model_original.decode().count(before) == 1
            try:
                model.write_text(model_original.decode().replace(before, 'TextSelection.atStart(view.state.doc)'))
                negative_build, _ = run('negative-interleaving-build', [sys.executable, 'scripts/web_client_build.py', 'build-development'])
                negative_env = {**ordinary_env, 'AMPLIFIER_PROOF_DIR': str(output / 'negative-interleaving')}
                if negative_build == 0:
                    code, text = run('negative-interleaving', ['node', 'tests/web/multi-quote-browser.mjs'],
                                     env=negative_env, expected=1)
                    results[-1]['passed'] = code == 1 and 'between typed: exact commentary/quote DOM order' in text
                    results[-1]['namedAssertion'] = 'between typed: exact commentary/quote DOM order'
            finally:
                model.write_bytes(model_original)
            results.append({'name': 'interleaving-source-restoration', 'passed': model.read_bytes() == model_original,
                            'sha256': sha(model_original)})
            save()
            # One normal rebuild after both independent browser mutants also
            # removes their generated assets before the restored/installed checks.
            build, _ = run('restored-production-build', [sys.executable, 'scripts/web_client_build.py', 'build'])
            if build == 0:
                run('restored-source-browser', ['node', 'tests/web/multi-quote-browser.mjs'],
                    env={**ordinary_env, 'AMPLIFIER_PROOF_DIR': str(output / 'restored-source-browser')})
        else:
            results.append({'name': 'negative-quote-alignment', 'passed': False,
                            'status': 'not-run', 'reason': 'Source browser baseline failed'})
            save()
            return 1
    else:
        results.append({'name': 'negative-quote-alignment', 'passed': False,
                        'status': 'not-run', 'reason': 'Baseline production build failed'})
        save()
    if build == 0:
        # Exercise the ordinary source owners through the unchanged standard
        # runner. Derive rows from the accepted plan, never a parallel command list.
        from browser_check_runner import load_plan
        plan, registry_source = load_plan(ROOT, ROOT / 'tests/web/browser-checks.json')
        (output / 'registry-source.json').write_text(json.dumps(registry_source, indent=2))
        names = {'multi-quote', 'compact-composer'}
        rows = [row for row in plan['scenarios'] if row['id'] in names]
        assert len(rows) == len(names) and {row['id'] for row in rows} == names
        selected = output / 'automatic-source-plan.json'
        selected.write_text(json.dumps({**plan, 'scenarios': rows}, indent=2))
        if managed:
            run('automatic-source-smoke', [sys.executable, 'scripts/multi_quote_checks.py',
                '--output', str(output / 'automatic-source-smoke')],
                env={**ordinary_env, 'AMPLIFIER_MULTI_QUOTE_SCENARIOS': str(selected),
                     'AMPLIFIER_MULTI_QUOTE_SOURCE_MANIFEST': str(output / 'source.json')})
        else:
            run('automatic-source-smoke', [sys.executable, 'scripts/browser_check_runner.py',
                '--plan', str(selected), '--output', str(output / 'automatic-source-smoke'),
                '--budget-seconds', '180'])
    if args.installed and build == 0:
        # The immutable managed image already has the locked dev build backend,
        # but deliberately has no registry cache or network in the check guest.
        wheel_code, _ = run('wheel-build', ['uv', 'build', '--wheel',
            *(['--offline', '--no-build-isolation', '--python', sys.executable] if managed else []), '--out-dir', str(output / 'wheel')])
        if wheel_code == 0:
            wheel, = (output / 'wheel').glob('*.whl')
            outside = Path(os.environ.get('RUNNER_TEMP', '/artifacts')) / ('multi-quote-installed-' + output.name)
            outside.mkdir()
            python = outside / 'venv/bin/python'
            venv_code, _ = run('installed-venv', ['uv', 'venv', '--python', sys.executable, str(outside / 'venv')])
            if venv_code:
                return 1
            if managed:
                # A fresh environment owns the wheel. Dependencies are read
                # from the immutable preparation after this venv's package
                # directory. Do not execute the preparation's editable .pth
                # files, copy its interpreter, or modify its files.
                import sysconfig
                site = outside / 'venv/lib' / ('python' + '.'.join(map(str, sys.version_info[:2]))) / 'site-packages'
                (site / 'prepared-dependencies.pth').write_text(sysconfig.get_path('purelib') + '\n')
                results.append({'name': 'installed-dependencies', 'passed': True,
                                'method': 'read-only-prepared-dependency-directory',
                                'sourcePrefix': sys.prefix,
                                'limitation': 'Uses locked dev dependencies; not a minimal production-dependency installation'})
                save()
            else:
                exported, _ = run('locked-dependency-export', ['uv', 'export', '--locked', '--no-dev',
                    '--no-emit-project', '--format', 'requirements-txt', '--output-file', str(outside / 'dependencies.txt')])
                if exported:
                    return 1
                dependencies, _ = run('installed-dependencies', ['uv', 'pip', 'install', '--python', str(python), '--no-deps',
                    '--requirement', str(outside / 'dependencies.txt')])
                if dependencies:
                    return 1
            install, _ = run('wheel-install', ['uv', 'pip', 'install', '--python', str(python), '--no-deps', '--reinstall-package', 'amplifier-unified', str(wheel)])
            if install == 0:
                expected = {}
                with zipfile.ZipFile(wheel) as archive:
                    for name in archive.namelist():
                        if name.startswith('amplifier_web/') and not name.endswith('/'):
                            expected[name] = sha(archive.read(name))
                            path = ROOT / name
                            if '/static/' in name or name.endswith('.py'):
                                assert not path.exists() or sha(path.read_bytes()) == expected[name], name
                (output / 'wheel-identity.json').write_text(json.dumps({'wheelSha256': sha(wheel.read_bytes()), 'files': expected}, indent=2))
                env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}
                identity_path = output / 'installed-identity.json'
                probe = f"""import amplifier_web,json,hashlib
from pathlib import Path
root=Path(amplifier_web.__file__).resolve().parent
assert root.is_relative_to(Path({str(outside / 'venv')!r}).resolve()), 'Package must be inside the installed venv'
assert not root.is_relative_to(Path({str(ROOT)!r}).resolve()), 'Package must not resolve to checkout source'
wheel=json.loads(Path({str(output / 'wheel-identity.json')!r}).read_text())
expected=wheel['files']
assert expected, 'Wheel package manifest must not be empty'
assert all(hashlib.sha256((root.parent/name).read_bytes()).hexdigest()==digest for name,digest in expected.items()), 'Installed package/static hashes must match wheel'
identity={{'packageRoot':str(root),'fileCount':len(expected),'wheelSha256':wheel['wheelSha256']}}
Path({str(identity_path)!r}).write_text(json.dumps(identity,indent=2))
print(json.dumps(identity))
"""
                identity_code, _ = run('installed-identity', [str(python), '-I', '-c', probe], cwd=outside, env=env)
                if identity_code:
                    return 1
                installed_root = json.loads(identity_path.read_text())['packageRoot']
                fixtures = outside / 'tests/fixtures'; fixtures.mkdir(parents=True)
                for name in ('message_interactions_ui_server.py', 'chat_ui_server.py', 'settings_ui_server.py'):
                    shutil.copyfile(ROOT / 'tests/fixtures' / name, fixtures / name)
                env.update(AMPLIFIER_TEST_PYTHON=str(python), AMPLIFIER_QUOTE_FIXTURE=str(fixtures / 'message_interactions_ui_server.py'),
                           AMPLIFIER_FIXTURE_CWD=str(outside), AMPLIFIER_PROOF_DIR=str(output / 'installed-browser'),
                           AMPLIFIER_TEST_PACKAGE_ROOT=installed_root)
                installed_code, text = run('installed-browser', ['node', str(ROOT / 'tests/web/multi-quote-browser.mjs')], cwd=outside, env=env)
                identities = []
                for line in text.splitlines():
                    try:
                        observation = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(observation, dict) and observation.get('installedPackageVerified') is True:
                        identities.append(observation)
                results[-1]['installedPackageRoot'] = installed_root
                results[-1]['fixtureOriginVerified'] = any(row.get('packageRoot') == installed_root for row in identities)
                results[-1]['passed'] = installed_code == 0 and results[-1]['fixtureOriginVerified']
                save()
    print('FINAL_RESULTS', json.dumps(results), flush=True)
    return 0 if results and all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
