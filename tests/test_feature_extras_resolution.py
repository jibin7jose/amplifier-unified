"""Exercise the real uv tool installer without network or application state."""
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from amplifier_web import app_component_graph as components
from amplifier_web import app_feature_probe as probe


def distribution(name, requirements):
    return SimpleNamespace(metadata={'Name': name}, requires=requirements)


def test_extra_closure_follows_markers_cycles_and_normalized_names(monkeypatch):
    rows = [
        distribution('amplifier-unified', [
            'bundle[Browser_Mode]; extra == "managed-browser"',
            'unused[secret]; extra == "other"',
            'wrong_platform[secret]; python_version < "2"',
        ]),
        distribution('bundle', ['engine[codec]; extra == "browser-mode"']),
        distribution('engine', ['bundle[browser-mode]; extra == "codec"',
                                'new_package[transport]; extra == "codec"']),
        distribution('unrelated', ['base[client]']),
        distribution('base', []),
    ]
    monkeypatch.setattr(probe.metadata, 'distributions', lambda: rows)
    actual = probe.installed_dependency_extras(['managed-browser'])
    assert actual == {
        'amplifier-unified': ['managed-browser'], 'bundle': ['browser-mode'],
        'engine': ['codec'], 'new-package': ['transport'], 'base': ['client'],
    }
    assert probe.installed_dependency_extras([]) == {'base': ['client']}


def test_exact_git_source_subdirectory_and_version_survive_extra_activation():
    graph = [{'name': 'bundle', 'version': '1.0',
              'url': 'https://github.com/microsoft/example', 'revision': 'a' * 40,
              'subdirectory': 'modules/bundle'}, {'name': 'engine', 'version': '2.3.4'}]
    assert components.requirements(graph, extras={'bundle': ['browser'], 'engine': ['codec']}) == (
        'bundle[browser] @ git+https://github.com/microsoft/example@' + 'a' * 40
        + '#subdirectory=modules/bundle\nengine[codec]==2.3.4\n')
    with pytest.raises(ValueError, match='extra'):
        components.requirements(graph, extras={'bundle': ['browser]\nother']})


def wheel(folder, name, requires=(), extras=(), *, script=False):
    normalized = name.replace('-', '_')
    info = normalized + '-1.0.dist-info'
    package = normalized + '.py'
    files = {
        package: 'def main(): pass\n',
        info + '/METADATA': '\n'.join([
            'Metadata-Version: 2.3', 'Name: ' + name, 'Version: 1.0',
            *['Provides-Extra: ' + value for value in extras],
            *['Requires-Dist: ' + value for value in requires], '',
        ]),
        info + '/WHEEL': 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    if script:
        files[info + '/entry_points.txt'] = '[console_scripts]\namplifier-unified = amplifier_unified:main\n'
    files[info + '/RECORD'] = ''.join(path + ',,\n' for path in [*files, info + '/RECORD'])
    target = folder / (normalized + '-1.0-py3-none-any.whl')
    with ZipFile(target, 'w') as archive:
        for path, content in files.items():
            archive.writestr(path, content)


@pytest.mark.skipif(shutil.which('uv') is None, reason='uv tool installer is not installed')
@pytest.mark.parametrize('feature,expected', [
    ('managed-browser', {'playwright'}),
    ('decisions-openai', {'decision-transport'}),
    ('decisions-azure', {'decision-transport'}),
    ('ordinary', set()),
    ('future-feature', None),
])
def test_real_uv_preserves_pins_and_installs_selected_transitive_extras(tmp_path, monkeypatch, feature, expected):
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    wheel(wheels, 'amplifier-unified', [
        'amplifier-bundle-ui-use==1.0', 'amplifier-module-provider-openai==1.0',
        'amplifier-module-provider-azure-openai==1.0', 'amplifier-lib-decisions==1.0',
        'browser-engine==1.0',
        'amplifier-bundle-ui-use[browser]==1.0; extra == "managed-browser"',
        'amplifier-module-provider-openai[decisions]==1.0; extra == "decisions-openai"',
        'amplifier-module-provider-azure-openai[decisions]==1.0; extra == "decisions-azure"',
        'new-bridge==1.0; extra == "future-feature"',
    ], ['managed-browser', 'decisions-openai', 'decisions-azure', 'future-feature'], script=True)
    wheel(wheels, 'amplifier-bundle-ui-use', ['browser-engine[codec]==1.0; extra == "browser"'], ['browser'])
    wheel(wheels, 'browser-engine', ['playwright==1.0; extra == "codec"'], ['codec'])
    wheel(wheels, 'playwright')
    for name in ['amplifier-module-provider-openai', 'amplifier-module-provider-azure-openai']:
        wheel(wheels, name, ['amplifier-lib-decisions[transport]==1.0; extra == "decisions"'], ['decisions'])
    wheel(wheels, 'amplifier-lib-decisions', ['decision-transport==1.0; extra == "transport"'], ['transport'])
    wheel(wheels, 'decision-transport')
    wheel(wheels, 'new-bridge', ['browser-engine[codec]==1.0'])
    environment = {key: value for key, value in os.environ.items() if not key.startswith('UV_')}
    environment.update(UV_TOOL_DIR=str(tmp_path / 'tools'), UV_TOOL_BIN_DIR=str(tmp_path / 'bin'),
                       UV_CACHE_DIR=str(tmp_path / 'cache'), UV_PYTHON_DOWNLOADS='never')

    def install(root, pins=None):
        args = [shutil.which('uv'), 'tool', 'install', '--force', '--no-config', '--no-index',
                '--find-links', str(wheels), '--python', sys.executable]
        if pins:
            args += ['--overrides', str(pins), '--with-requirements', str(pins)]
        result = subprocess.run([*args, root], env=environment, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr

    python = tmp_path / 'tools/amplifier-unified' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def snapshot():
        result = subprocess.run([str(python), '-I', '-c',
            'import importlib.metadata as m,json; print(json.dumps([{'
            '"name":d.metadata["Name"],"version":d.version,"requires":d.requires} '
            'for d in m.distributions()]))'], capture_output=True, text=True, check=True, timeout=15)
        return json.loads(result.stdout)

    install('amplifier-unified==1.0')
    baseline = snapshot()
    graph = components.normalized([{'name': d['name'], 'version': d['version']} for d in baseline])
    rows = [distribution(d['name'], d['requires']) for d in baseline]
    monkeypatch.setattr(probe.metadata, 'distributions', lambda: rows)
    extras = [] if feature == 'ordinary' else [feature]
    pins = tmp_path / 'host-components.txt'
    pins.write_text(components.requirements(graph, extras=probe.installed_dependency_extras(extras)))
    root = 'amplifier-unified' + ('[' + feature + ']' if extras else '') + '==1.0'
    install(root, pins)
    candidate = snapshot()
    versions = {d['name']: d['version'] for d in candidate}
    assert {d['name']: versions[d['name']] for d in baseline} == {d['name']: d['version'] for d in baseline}
    if feature == 'future-feature':
        # Installed metadata cannot see an absent intermediary's downstream
        # extras. The real resolver demonstrates that boundary; qualification
        # must reject it using the actual resulting distribution metadata.
        assert set(versions) - {d['name'] for d in baseline} == {'new-bridge'}
        rows = [SimpleNamespace(metadata={'Name': d['name']}, version=d['version'],
                                requires=d['requires']) for d in candidate]
        monkeypatch.setattr(probe.metadata, 'distributions', lambda: rows)
        monkeypatch.setattr(sys, 'argv', ['probe', feature])
        with pytest.raises(ValueError, match='conflicts with the preserved dependency environment'):
            exec(compile(probe.DEPENDENCY_PROBE, '<candidate-dependencies>', 'exec'), {})
        return
    assert set(versions) - {d['name'] for d in baseline} == expected
    # Older-bootstrap activation reuses the complete qualified graph as both
    # overrides and explicit requirements: retain its extras' dependencies too.
    pins.write_text(components.requirements(components.normalized(
        [{'name': d['name'], 'version': d['version']} for d in candidate])))
    install(root, pins)
    assert {d['name']: d['version'] for d in snapshot()} == versions
