"""Bind release checks to one candidate and cache only resolved builds.

This file is copied from the workflow checkout before selecting a historical
application revision. All application paths are relative to the working tree.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import tomllib


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def files(folder, names):
    return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in names}


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def identity(root):
    return {'revision': run('git', '-C', str(root), 'rev-parse', 'HEAD'),
            'version': tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']}


HOST_FILES = ('resolution.json', 'qualified-overrides.txt', 'development.lock')
LANES = ('package', 'frontend', 'runtime')


def candidate(root, evidence):
    report = json.loads((evidence / 'resolution.json').read_text())
    if report.get('ok') is not True:
        raise ValueError('Host dependency resolution did not succeed')
    value = {'schema': 1, **identity(root), 'hostEvidence': files(evidence, HOST_FILES)}
    write(evidence / 'candidate.json', value)
    return digest(value)


def verify_candidate(root, evidence, expected):
    value = json.loads((evidence / 'candidate.json').read_text())
    if digest(value) != expected or any(value.get(k) != v for k, v in identity(root).items()):
        raise ValueError('Qualification does not match the selected application candidate')
    if value.get('hostEvidence') != files(evidence, HOST_FILES):
        raise ValueError('Candidate host dependency evidence changed')
    return value


def checkout_identity(path):
    run('git', '-C', str(path), 'diff', '--exit-code', 'HEAD')
    return run('git', '-C', str(path), 'rev-parse', 'HEAD')


def build_identity(root, runtime):
    """uv has already refreshed/locked all moving refs before this cache key."""
    flags = ('RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'CARGO_BUILD_TARGET',
             'CC', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS', 'MACOSX_DEPLOYMENT_TARGET',
             'MATURIN_PEP517_ARGS', 'CARGO_PROFILE_RELEASE_LTO', 'CARGO_PROFILE_RELEASE_CODEGEN_UNITS')
    return {
        'schema': 1,
        'resolvedInputs': files(runtime, ('uv.lock', 'pyproject.toml', 'build-constraints.txt')),
        'loopLive': checkout_identity(root / '.ci/loop-live'),
        'recipes': checkout_identity(root / '.ci/recipes'),
        'platform': {'system': sys.platform, 'machine': platform.machine(),
                     'osRelease': Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else platform.platform(),
                     'image': os.environ.get('ImageOS'), 'imageVersion': os.environ.get('ImageVersion')},
        'python': {'version': sys.version, 'abi': sysconfig.get_config_var('SOABI'),
                   'implementation': sys.implementation.name},
        'tools': {'uv': run('uv', '--version'), 'rustc': run('rustc', '--version', '--verbose'),
                  'cargo': run('cargo', '--version'), 'cc': run('cc', '--version'),
                  'maturin': importlib.metadata.version('maturin')},
        # Bind relevant flags without copying arbitrary environment contents into
        # evidence. Compiler flags can themselves contain private definitions.
        'buildEnvironment': {name: digest(os.environ.get(name, '')) for name in flags},
    }


def runtime_project(root, runtime):
    runtime.mkdir(parents=True)
    source = (root / 'amplifier_web/runtime_deps/pyproject.toml').read_text()
    constraint = 'maturin==' + importlib.metadata.version('maturin')
    if '[tool.uv]\n' not in source or 'build-constraint-dependencies' in source:
        raise ValueError('Review the runtime build configuration before qualification')
    source = source.replace('[tool.uv]\n', '[tool.uv]\nbuild-constraint-dependencies = [' + json.dumps(constraint) + ']\n', 1)
    (runtime / 'pyproject.toml').write_text(source)
    (runtime / 'build-constraints.txt').write_text(constraint + '\n')


def graph(python):
    probe = '''import importlib.metadata as m,json
rows=[{'name':d.metadata['Name'],'version':d.version,
       'direct':json.loads(d.read_text('direct_url.json') or 'null')} for d in m.distributions()]
print(json.dumps(sorted(rows,key=lambda row:row['name'])))'''
    return json.loads(run(str(python), '-I', '-c', probe))


def core_wheel(python):
    """Record the installed native binary without importing any CLI or app."""
    probe = '''import hashlib,importlib.metadata as m,importlib.machinery as machinery,json,platform,sys
d=m.distribution('amplifier-core')
native=[p for p in d.files or [] if str(p).startswith('amplifier_core/_engine.') and any(str(p).endswith(s) for s in machinery.EXTENSION_SUFFIXES)]
print(json.dumps({'version':d.version,'direct':json.loads(d.read_text('direct_url.json') or 'null'),
 'wheel':d.read_text('WHEEL'),'native':[{'name':str(p),'sha256':hashlib.sha256(d.locate_file(p).read_bytes()).hexdigest()} for p in native],
 'python':sys.version,'machine':platform.machine(),
 'cliInstalled':any(x.metadata['Name'].lower().replace('_','-')=='amplifier-app-cli' for x in m.distributions())}))'''
    value = json.loads(run(str(python), '-I', '-c', probe))
    if (value['direct'] is not None or not value['wheel'] or 'Root-Is-Purelib: false' not in value['wheel']
            or len(value['native']) != 1 or value['cliInstalled']):
        raise ValueError('Core must be a registry native wheel without amplifier-app-cli')
    return value


def runtime_snapshot(root, runtime):
    build = json.loads((runtime / 'build-identity.json').read_text())
    if build != build_identity(root, runtime):
        raise ValueError('Runtime source or toolchain changed after cache selection')
    value = {'build': digest(build), 'recipes': checkout_identity(root / '.ci/recipes'),
             'graph': graph(runtime / '.venv/bin/python')}
    policy = tomllib.loads((runtime / 'pyproject.toml').read_text()).get('tool', {}).get('uv', {})
    if 'amplifier-core' in policy.get('no-build-package', []):
        value['coreWheel'] = core_wheel(runtime / '.venv/bin/python')
    return value


def receipt(root, evidence, expected, lane, destination, runtime=None, dist=None):
    selected = verify_candidate(root, evidence, expected)
    value = {'schema': 1, 'candidate': expected, 'revision': selected['revision'], 'lane': lane}
    if lane == 'runtime':
        qualified = json.loads((runtime / 'qualified-runtime.json').read_text())
        if runtime_snapshot(root, runtime) != qualified:
            raise ValueError('Runtime graph changed during qualification')
        value['runtime'] = digest(qualified)
    if lane == 'package':
        names = sorted(p.name for p in dist.iterdir() if p.suffix == '.whl' or p.name.endswith('.tar.gz') or p.name == 'SHA256SUMS')
        if len(names) != 3:
            raise ValueError('Expected the verified wheel, source archive and checksums')
        value['distributions'] = files(dist, names)
    write(destination, value)


def verify_receipts(root, evidence, expected, receipts, dist):
    selected = verify_candidate(root, evidence, expected)
    for lane in LANES:
        value = json.loads((receipts / (lane + '.json')).read_text())
        if (value.get('lane') != lane or value.get('candidate') != expected
                or value.get('revision') != selected['revision']):
            raise ValueError('Qualification receipt belongs to a different candidate or lane')
        if lane == 'package':
            recorded = value.get('distributions', {})
            if not recorded or any(Path(name).name != name for name in recorded):
                raise ValueError('Malformed distribution receipt')
            actual = sorted(p.name for p in dist.iterdir() if p.is_file())
            if actual != sorted(recorded) or files(dist, actual) != recorded:
                raise ValueError('Distributions changed after Python qualification')
        if lane == 'runtime' and value.get('runtime') != digest(json.loads((receipts / 'qualified-runtime.json').read_text())):
            raise ValueError('Runtime qualification evidence changed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('candidate', 'verify-candidate', 'runtime-project', 'cache-key', 'runtime-snapshot', 'core-wheel', 'receipt', 'verify-receipts'))
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--candidate')
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--lane', choices=LANES)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--receipts', type=Path)
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    root = Path.cwd()
    output = None
    if args.command == 'candidate':
        output = ('candidate', candidate(root, args.evidence))
    elif args.command == 'verify-candidate':
        verify_candidate(root, args.evidence, args.candidate)
    elif args.command == 'runtime-project':
        runtime_project(root, args.runtime)
    elif args.command == 'cache-key':
        value = build_identity(root, args.runtime)
        write(args.runtime / 'build-identity.json', value)
        output = ('key', 'release-runtime-v1-' + digest(value))
    elif args.command == 'runtime-snapshot':
        write(args.runtime / 'qualified-runtime.json', runtime_snapshot(root, args.runtime))
    elif args.command == 'core-wheel':
        write(args.destination, core_wheel(args.python))
    elif args.command == 'receipt':
        receipt(root, args.evidence, args.candidate, args.lane, args.destination, args.runtime, args.dist)
    else:
        verify_receipts(root, args.evidence, args.candidate, args.receipts, args.dist)
    if output:
        if args.output:
            with args.output.open('a') as stream:
                stream.write(f'{output[0]}={output[1]}\n')
        print(output[1])


if __name__ == '__main__':
    main()
