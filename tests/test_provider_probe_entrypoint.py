"""Run the real provider probe from an isolated runtime, without a path shim."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import venv

import pytest


PACKAGE = Path(__file__).resolve().parents[1] / 'amplifier_web'

PROVIDER = '''
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import runtime_dependency

assert 'PYTHONPATH' not in os.environ
assert importlib.util.find_spec('host_only_dependency') is None, 'host imports leaked'
assert runtime_dependency.SOURCE == 'runtime', 'host dependency shadows runtime'
try:
    importlib.metadata.distribution('amplifier-probe-host-only')
except importlib.metadata.PackageNotFoundError:
    pass
else:
    raise AssertionError('host distribution metadata leaked')

def record(event):
    with open(os.environ['PROBE_EVENTS'], 'a') as stream:
        stream.write(event + '\\n')

class FixtureProvider:
    def __init__(self, *, api_key=None, config=None):
        self.config = config
        if config:
            assert api_key == 'fixture-private-key'
            assert config == {'api_key': 'fixture-private-key', 'base_url': ''}
        else:
            assert api_key is None and config == {}
        record('construct')

    def get_info(self):
        print('fixture provider diagnostic')
        return {'name': 'Fixture', 'config_fields': [
            {'id': 'api_key', 'field_type': 'secret', 'required': True,
             'default': 'fixture-private-key'},
            {'id': 'base_url', 'field_type': 'text', 'required': False},
        ]}

    async def list_models(self):
        assert self.config, 'schema-only probe must not discover models'
        record('models')
        return [{'id': 'fixture-model', 'api_key': 'fixture-private-key'}]

    def get_model_list_info(self):
        return {'version':1,'source':'provider-api'}

    async def close(self):
        record('close')
'''


@pytest.fixture
def isolated_probe(tmp_path):
    outer = tmp_path / 'host-site-packages'
    package = outer / 'amplifier_web'
    shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns('static', '__pycache__'))
    shutil.copytree(PACKAGE.parent / 'amplifier_operations', outer / 'amplifier_operations',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(PACKAGE.parent / 'amplifier_kernels', outer / 'amplifier_kernels',
                    ignore=shutil.ignore_patterns('__pycache__'))
    (outer / 'host_only_dependency.py').write_text('HOST_ONLY = True\n')
    (outer / 'runtime_dependency.py').write_text("SOURCE = 'host'\n")
    metadata = outer / 'amplifier_probe_host_only-99.0.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text(
        'Metadata-Version: 2.1\nName: amplifier-probe-host-only\nVersion: 99.0\n'
    )

    runtime = tmp_path / 'runtime'
    venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(runtime)
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    env = {'PATH': os.defpath, 'HOME': str(tmp_path),
           **({'SystemRoot': os.environ['SystemRoot']} if 'SystemRoot' in os.environ else {}),
           'PROBE_EVENTS': str(tmp_path / 'events'),
           'PROBE_KEY': 'fixture-private-key',
           'AMPLIFIER_UNIFIED_IMPORT_HOME': str(tmp_path / 'no-legacy-home')}
    site = Path(subprocess.check_output(
        [str(python), '-c', "import sysconfig; print(sysconfig.get_path('purelib'))"],
        text=True, cwd=tmp_path, env=env,
    ).strip())
    # The probe uses these real libraries through the app configuration reader.
    # Copy only runtime-owned dependencies, never the outer app's site-packages.
    for name in ('filelock', 'yaml'):
        origin = Path(importlib.util.find_spec(name).origin).parent
        shutil.copytree(origin, site / name, ignore=shutil.ignore_patterns('__pycache__'))
    (site / 'runtime_dependency.py').write_text("SOURCE = 'runtime'\n")
    (site / 'amplifier_module_provider_fixture.py').write_text(PROVIDER)
    return python, env, site, package


@pytest.mark.parametrize('invocation', ['script', 'package'])
@pytest.mark.parametrize('action', ['providers.schema', 'providers.models', 'providers.test'])
def test_provider_probe_entrypoint_uses_runtime_dependencies_and_clean_protocol(
    tmp_path, isolated_probe, invocation, action,
):
    python, env, site, package = isolated_probe
    if invocation == 'package':
        # Normal package invocation remains supported when the app is installed.
        shutil.copytree(package, site / 'amplifier_web')
        arguments = ['-m', 'amplifier_web.provider_probe']
    else:
        arguments = [str(package / 'provider_probe.py')]

    # In the direct-script case the interpreter cannot import the application
    # before startup. The actual script must bootstrap its own package safely.
    preflight = subprocess.run(
        [str(python), '-c', "import importlib.util, json; print(json.dumps({"
         "'app': importlib.util.find_spec('amplifier_web') is not None, "
         "'host': importlib.util.find_spec('host_only_dependency') is not None}))"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15, check=True,
    )
    assert json.loads(preflight.stdout) == {'app': invocation == 'package', 'host': False}
    request = {'module': 'provider-fixture', 'action': action,
               'config': {'api_key': '${PROBE_KEY}', 'base_url': '${PROBE_OPTIONAL_URL}'}}
    completed = subprocess.run(
        [str(python), *arguments], input=json.dumps(request), cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    result = json.loads(completed.stdout)
    assert 'error' not in result, result
    assert result['info']['name'] == 'Fixture'
    assert result['configSchema']['fields'] == [
        {'id': 'api_key', 'field_type': 'secret', 'required': True},
        {'id': 'base_url', 'field_type': 'text', 'required': False},
    ]
    assert 'fixture-private-key' not in completed.stdout + completed.stderr
    assert 'fixture provider diagnostic' in completed.stderr
    events = Path(env['PROBE_EVENTS']).read_text().splitlines()
    if action == 'providers.schema':
        assert 'models' not in result
        assert events == ['construct', 'close']
    else:
        assert result['models'] == [{'id': 'fixture-model'}]
        assert result['modelsSupported'] is True
        assert events == ['construct', 'close', 'construct', 'models', 'close']
        if action == 'providers.test':
            assert result['test'] == {'reachable': True, 'modelCount': 1,
                                      'method': 'provider.list_models'}
