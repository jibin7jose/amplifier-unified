"""Manual, fail-stop readiness trio qualification; no live model or full campaign."""
from __future__ import annotations

import ast
import base64
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
OUTPUT = ROOT / 'output/readiness-trio'
# CI 41158162026 resolved this supplement, then failed on its context-simple@main
# URL versus the host lock. Keep that exact source; never re-resolve locked deps.
CONTEXT_REVISION = '897cb6ce25628dc49a6b463b9127b010828b73b7'
CONTEXT_REQUIREMENT = ('amplifier-module-context-managed @ git+https://github.com/microsoft/'
                       'amplifier-bundle-context-managed@' + CONTEXT_REVISION + '#subdirectory=modules/context-managed')
SELECTORS = [
    *('tests/test_' + name + '.py' for name in ('questions', 'question_handoffs',
      'question_presentation', 'schedule_new_tasks', 'schedule_prepared_admission',
      'session_health', 'context_continuity', 'failure_projection_identity', 'retained_failure_recovery',
      'collaboration_generation_authority')),
    'tests/test_message_delivery.py::test_check_is_passive_and_explains_uncertainty',
    'tests/test_message_delivery.py::test_late_acceptance_during_check_is_never_downgraded',
    'tests/test_delivery_ack_timeout.py::test_late_ack_reconciles_exact_input_without_restarting_turn',
    'tests/test_guarded_delivery_ack_timeout.py::test_delivery_without_message_requires_exact_saved_input_and_session',
    'tests/test_access_lineage.py::test_scheduled_root_inherits_after_host_restart_without_source_residency',
    'tests/test_access_lineage.py::test_scheduled_origin_requires_the_reserved_source_run_and_destination',
    'tests/test_execution_events.py::ExecutionTests::test_compaction_calls_have_distinct_label_usage_and_no_public_stream',
    'tests/test_execution_events.py::ExecutionTests::test_failed_compaction_restores_purpose_and_records_safe_failure',
]
MODULES = ('amplifier_core', 'amplifier_foundation', 'amplifier_module_loop_live',
           'amplifier_module_loop_streaming', 'amplifier_module_context_simple',
           'amplifier_module_context_managed.boundary', 'amplifier_module_hook_context_intelligence')
PACKAGES = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']['packages']


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args, root=ROOT):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, timeout=10).strip()


def probe(installed):
    names = (*MODULES, *PACKAGES) if installed else MODULES
    modules = {name: str(Path(importlib.import_module(name).__file__).resolve()) for name in names}
    distributions = sorted([{'name': d.metadata['Name'], 'version': d.version,
        'direct': json.loads(d.read_text('direct_url.json') or 'null')} for d in metadata.distributions()],
        key=lambda row: row['name'])
    context, = [row for row in distributions if row['name'] == 'amplifier-module-context-managed']
    assert context['direct']['vcs_info']['commit_id'] == CONTEXT_REVISION
    assert context['direct']['subdirectory'] == 'modules/context-managed'
    print(json.dumps({'python': sys.version, 'executable': sys.executable,
                     'modules': modules, 'distributions': distributions}))


def main():
    from browser_check_runner import run_scenarios
    from frontend_build_artifact import create, source_identity, verify
    from web_test_harness import stage_web_test_harness
    assert sys.version_info[:2] == (3, 13), 'Qualification requires Python 3.13'
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    assert not sys.flags.optimize, 'Qualification assertions must not be disabled'
    OUTPUT.mkdir(parents=True, exist_ok=False)  # Never overwrite an earlier attempt.
    report = {'passed': False, 'complete': False, 'phases': {}, 'normalBudgetSeconds': 300,
              'maximumSeconds': 600, 'limits': ['Synthetic providers only; original scheduler and compaction incidents remain open.']}
    work = Path(tempfile.mkdtemp(prefix='readiness-trio-', dir=os.environ['RUNNER_TEMP']))
    started = time.monotonic()
    def expired(*_):
        raise TimeoutError('Readiness trio exceeded its 600 second overall budget')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(600)
    host, installed = work / 'host/bin/python', work / 'installed/bin/python'
    harness = work / 'harness'
    wheel = OUTPUT / 'wheel' / ('amplifier_unified-' + tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version'] + '-py3-none-any.whl')
    runtime = tomllib.loads((ROOT / 'uv.lock').read_text())
    loop, = [row for row in runtime['package'] if row['name'] == 'amplifier-module-loop-live']
    # Only the authenticated CI checkout supplies this private dependency. No key is read here.
    common = {'UV_PROJECT_ENVIRONMENT': str(work / 'host'), 'UV_NO_BUILD_PACKAGE': 'amplifier-core',
              'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'url.' + (ROOT / '.ci/loop-live').as_uri() + '.insteadOf',
              'GIT_CONFIG_VALUE_0': 'https://github.com/microsoft/amplifier-module-loop-live',
              'HOME': str(work / 'home'), 'TMPDIR': str(work / 'tmp'),
              'PLAYWRIGHT_BROWSERS_PATH': str(work / 'browsers'), 'PYTHONPATH': '', 'NODE_PATH': ''}
    for name in ('home', 'tmp'):
        (work / name).mkdir()
    source_env = {**common, 'PYTHONPATH': str(ROOT), 'AMPLIFIER_TEST_PYTHON': str(host),
                  'UNIFIED_RUNTIME_PYTHON': str(host), 'AMPLIFIER_TEST_ARTIFACTS': str(OUTPUT / 'python-evidence')}
    browser_env = {**common, 'AMPLIFIER_TEST_PYTHON': str(installed),
                   'AMPLIFIER_TEST_INSTALLED_STATIC': '1', 'AMPLIFIER_QUESTIONS_FIXTURE': str(harness / 'tests/fixtures/questions_ui_server.py'),
                   'AMPLIFIER_QUESTIONS_OUTPUT': str(OUTPUT / 'questions-evidence')}
    # Full intended commands exist before preparation; only the verified package path is filled after install.
    plans = [
        ('locked-host', 150, ['uv', 'sync', '--project', str(ROOT), '--locked', '--group', 'dev', '--group', 'artifacts', '--no-install-project', '--python', sys.executable], common, ROOT),
        ('locked-freeze', 15, ['uv', '--quiet', 'pip', 'freeze', '--python', str(host)], common, ROOT),
        ('context-dependency', 90, ['uv', 'pip', 'install', '--python', str(host), '--no-deps', '--no-build-isolation', CONTEXT_REQUIREMENT], common, ROOT),
        ('source-dependencies', 15, ['uv', 'pip', 'check', '--python', str(host)], common, ROOT),
        ('source-imports', 20, [str(host), '-I', str(Path(__file__).resolve()), '--probe-source'], common, ROOT),
        ('runtime-freeze', 15, ['uv', '--quiet', 'pip', 'freeze', '--python', str(host)], common, ROOT),
        ('npm-ci', 90, ['npm', 'ci', '--prefix', 'tests/web', '--no-audit', '--no-fund'], common, ROOT),
        ('client-npm-ci', 90, ['npm', 'ci', '--prefix', '.build/web-client', '--no-audit', '--no-fund'], common, ROOT),
        ('chromium', 120, ['node', 'tests/web/node_modules/playwright/cli.js', 'install', '--with-deps', 'chromium'], common, ROOT),
        ('python', 150, [str(host), '-m', 'pytest', '-q', '-ra', '--tb=short', *[str(ROOT / s) for s in SELECTORS], '--junitxml=' + str(OUTPUT / 'python.xml')], source_env, ROOT),
        ('node', 20, ['node', '--test', '--test-reporter=tap', '../../tests/web/fixture-lifecycle.test.mjs',
                     'tests/conversation-navigation.test.mjs', 'tests/history-window.test.mjs'], common, ROOT),
        ('build', 90, [sys.executable, 'scripts/web_client_build.py', 'build'], common, ROOT),
        ('delegate-browser', 60, ['node', 'tests/web/delegate-activity-browser.mjs'], source_env, ROOT),
        ('wheel', 30, ['uv', 'build', '--wheel', '--no-build-isolation', '--python', str(host), '--out-dir', str(wheel.parent), str(ROOT)], common, ROOT),
        ('fresh-runtime', 15, ['uv', 'venv', '--python', sys.executable, str(work / 'installed')], common, ROOT),
        ('install-dependencies', 90, ['uv', 'pip', 'install', '--python', str(installed), '--no-deps', '-r', str(OUTPUT / 'runtime-requirements.txt')], common, ROOT),
        ('install-wheel', 20, ['uv', 'pip', 'install', '--python', str(installed), '--no-deps', str(wheel)], common, ROOT),
        ('installed-dependencies', 15, ['uv', 'pip', 'check', '--python', str(installed)], common, ROOT),
        ('installed-imports', 20, [str(installed), '-I', str(Path(__file__).resolve()), '--probe-installed'], common, ROOT),
        ('questions-browser', 120, ['node', 'tests/web/questions-browser.mjs'], browser_env, harness),
        ('count-browser', 60, ['node', 'tests/web/count-recovery-browser.mjs'], {**browser_env, 'TEST_NATIVE_NO_REDUCTION': '0', 'AMPLIFIER_TEST_ARTIFACTS': str(OUTPUT / 'count-evidence')}, harness),
        ('nonreduction-browser', 60, ['node', 'tests/web/count-recovery-browser.mjs'], {**browser_env, 'TEST_NATIVE_NO_REDUCTION': '1', 'AMPLIFIER_TEST_ARTIFACTS': str(OUTPUT / 'nonreduction-evidence')}, harness),
    ]
    save(OUTPUT / 'plan.json', [{'id': n, 'timeoutSeconds': cap, 'command': cmd, 'env': env, 'cwd': str(root / '.build/web-client' if n == 'node' else root)} for n, cap, cmd, env, root in plans])
    report['phases'] = {name: {'status': 'not_run'} for name, *_ in plans}
    save(OUTPUT / 'report.json', report)
    try:
        revision = git('rev-parse', 'HEAD')
        assert revision == os.environ['GITHUB_SHA'], 'Run the immutable dispatch branch head, never a moving checkout'
        assert git('rev-parse', 'HEAD', root=ROOT / '.ci/loop-live') == loop['source']['git'].rsplit('#', 1)[1]
        assert not git('status', '--porcelain', '--untracked-files=no')
        source = {'commit': revision, 'tree': git('rev-parse', 'HEAD^{tree}'), 'identity': source_identity(ROOT),
                  'files': {p: sha(ROOT / p) for p in git('ls-files').splitlines() if (ROOT / p).is_file()},
                  'loopSource': loop['source'], 'contextDeclaration': CONTEXT_REQUIREMENT,
                  'runId': os.environ['GITHUB_RUN_ID'], 'runAttempt': os.environ['GITHUB_RUN_ATTEMPT']}
        save(OUTPUT / 'source.json', source)
        for selector in SELECTORS:
            path, *node = selector.split('::')
            tree = ast.parse((ROOT / path).read_text())
            for part in node:
                matches = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part]
                assert len(matches) == 1, selector
                tree = matches[0]
        (OUTPUT / 'python-evidence').mkdir()
        package = None
        for name, cap, command, env, root in plans:
            if package and root == harness:
                env = {**env, 'AMPLIFIER_TEST_PACKAGE_ROOT': str(package / 'amplifier_web')}
            plan = {'schemaVersion': 1, 'scenarios': [{'id': name, 'command': command, 'env': env,
                'cwd': '.build/web-client' if name == 'node' else '.'}]}
            remaining = 600 - (time.monotonic() - started)
            assert remaining > 1, 'Overall qualification budget exhausted'
            report['phases'][name] = {'status': 'running'}
            save(OUTPUT / 'report.json', report)
            code = run_scenarios(root, plan, OUTPUT / name, budget=min(cap, remaining), case_timeout=cap, provenance={'commit': revision})
            result = json.loads((OUTPUT / name / 'results.json').read_text())
            row, = result['scenarios']
            report['phases'][name] = row
            save(OUTPUT / 'report.json', report)
            assert code == 0 and result['complete'] and result['passed'] and row['status'] == 'passed', name
            assert row['exitCode'] == 0 and row['reason'] is None and row['cleanup'] == {'remaining': [], 'terminated': 0}, name
            log = OUTPUT / name / (name + '.log')
            if name in ('locked-freeze', 'runtime-freeze'):
                text = log.read_text()
                assert text.strip() and not any(line.startswith(('-e ', 'amplifier-unified')) for line in text.splitlines())
                if name == 'runtime-freeze':
                    locked = (OUTPUT / 'locked-requirements.txt').read_text().splitlines()
                    assert CONTEXT_REQUIREMENT not in locked
                    assert sorted(text.splitlines()) == sorted([*locked, CONTEXT_REQUIREMENT]), 'Supplement changed locked dependencies'
                (OUTPUT / ('locked-requirements.txt' if name == 'locked-freeze' else 'runtime-requirements.txt')).write_text(text)
            elif name == 'python':
                cases = ET.parse(OUTPUT / 'python.xml').getroot().findall('.//testcase')
                assert cases and all(not any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for case in cases)
                prepared = [case.attrib['name'] for case in cases if case.attrib.get('classname', '').endswith('test_schedule_prepared_admission')]
                assert sorted(prepared) == sorted(['test_execution_oracle_rejects_additional_accepted_execution', 'test_prepared_first_and_second_occurrences_execute_once[default]', 'test_prepared_first_and_second_occurrences_execute_once[pinned]'])
                report['pythonCases'] = len(cases)
            elif name == 'node':
                counts = dict(re.findall(r'^# (tests|pass|fail|cancelled|skipped|todo) (\d+)$', log.read_text(), re.M))
                assert int(counts['tests']) > 0 and counts['pass'] == counts['tests']
                assert all(counts[key] == '0' for key in ('fail', 'cancelled', 'skipped', 'todo'))
                shutil.copy2(log, OUTPUT / 'node.tap')
            elif name == 'build':
                build = create(ROOT, OUTPUT / 'built-frontend', os.environ)
                report['build'] = json.loads((ROOT / 'amplifier_web/static/build.json').read_text())
            elif name == 'wheel':
                assert list(wheel.parent.glob('*.whl')) == [wheel], 'Exactly one newly built wheel'
                with zipfile.ZipFile(wheel) as archive:
                    record, = [p for p in archive.namelist() if p.endswith('.dist-info/RECORD')]
                    entries = list(csv.reader(io.StringIO(archive.read(record).decode())))
                    assert {r[0] for r in entries} == set(archive.namelist())
                    for path, encoded, size in entries:
                        if path == record:
                            assert not encoded and not size
                            continue
                        data = archive.read(path)
                        assert encoded == 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=') and len(data) == int(size), path
                        if path.startswith(tuple(p + '/' for p in PACKAGES)) and (ROOT / path).is_file():
                            assert hashlib.sha256(data).hexdigest() == sha(ROOT / path), path
                    assets = {p.removeprefix('amplifier_web/static/'): hashlib.sha256(archive.read(p)).hexdigest() for p in archive.namelist() if p.startswith('amplifier_web/static/')}
                    assert assets == {p: v['sha256'] for p, v in build['assets'].items()}
                report['wheel'] = {'path': str(wheel), 'sha256': sha(wheel), 'recordEntries': len(entries)}
            elif name == 'installed-imports':
                origins = json.loads(log.read_text())
                package = Path(origins['modules']['amplifier_web']).parent.parent
                assert package.is_relative_to(work / 'installed') and 'site-packages' in package.parts
                assert all(Path(path).is_relative_to(work / 'installed') for path in origins['modules'].values())
                assert all(Path(origins['modules'][p]).parent == package / p for p in PACKAGES)
                dist, = [d for d in origins['distributions'] if d['name'] == 'amplifier-unified']
                assert not dist['direct'].get('dir_info', {}).get('editable')
                assert dist['direct']['url'] == wheel.as_uri()
                save(OUTPUT / 'installed.json', origins)
                installed_record = {path: (encoded, size) for path, encoded, size in
                    csv.reader(io.StringIO((package / record).read_text()))}
                with zipfile.ZipFile(wheel) as archive:
                    for path in archive.namelist():
                        if path.startswith(tuple(p + '/' for p in PACKAGES)):
                            assert sha(package / path) == hashlib.sha256(archive.read(path)).hexdigest(), path
                            encoded, size = installed_record[path]
                            assert encoded == 'sha256=' + base64.urlsafe_b64encode(bytes.fromhex(sha(package / path))).decode().rstrip('=')
                            assert int(size) == (package / path).stat().st_size
                # Real copies: the installed harness cannot resolve source node_modules or product code.
                stage_web_test_harness(ROOT, harness, copy_dependencies=True)
                assert not any(p.is_symlink() for p in harness.rglob('*'))
                assert not (harness / 'amplifier_web').exists() and not (harness / '.build/web-client/src').exists()
                copied = {str(p.relative_to(harness)): sha(p) for folder in ('tests/fixtures', 'tests/web', '.build/web-client/tests')
                          for p in (harness / folder).rglob('*') if p.is_file() and 'node_modules' not in p.parts}
                assert all(sha(ROOT / path) == value for path, value in copied.items())
                save(OUTPUT / 'harness.json', {'path': str(harness), 'nodeModules': 'real copy',
                    'files': copied})
            elif name in ('count-browser', 'nonreduction-browser'):
                proof = json.loads((Path(env['AMPLIFIER_TEST_ARTIFACTS']) / 'count-recovery-result.json').read_text())
                assert proof['passed'] and proof['mode'] == 'installed-static'
                assert Path(proof['packageRoot']) == package / 'amplifier_web'
                assert proof['indexSha256'] == sha(package / 'amplifier_web/static/index.html')
                assert proof['cleanup']['code'] == 0 and proof['cleanup']['signal'] is None
            elif name == 'questions-browser':
                assert '"installedPackageVerified": true' in log.read_text()
                exit_receipt = json.loads((OUTPUT / 'questions-evidence/fixture-exit.json').read_text())
                assert exit_receipt['code'] == 0 and exit_receipt['signal'] is None
        assert verify(ROOT, OUTPUT / 'built-frontend')['source'] == source['identity']
        assert all(sha(ROOT / p) == value for p, value in source['files'].items() if not p.startswith('amplifier_web/static/'))
        with zipfile.ZipFile(wheel) as archive:
            assert all(sha(package / p) == hashlib.sha256(archive.read(p)).hexdigest()
                       for p in archive.namelist() if p.startswith(tuple(name + '/' for name in PACKAGES)))
        report.update(passed=True, complete=True)
    except BaseException as exc:
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        if 'name' in locals() and name in report['phases']:
            report['phases'][name].update(status='failed', verificationFailure=str(exc))
        raise
    finally:
        signal.alarm(0)
        report['durationSeconds'] = round(time.monotonic() - started, 3)
        report['workDirectory'] = str(work)
        # CI retires the disposable runner; evidence survives failed checks and cleanup.
        save(OUTPUT / 'report.json', report)


if __name__ == '__main__':
    if sys.argv[1:] in (['--probe-source'], ['--probe-installed']):
        probe(sys.argv[1] == '--probe-installed')
    else:
        assert not sys.argv[1:], 'No arbitrary selection or command overrides'
        main()
