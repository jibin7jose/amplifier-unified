import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_web import app_component_graph as components
from amplifier_web import app_release_artifacts as artifacts

VERSION = '99.0.0'
REVISION = 'a' * 40
STAMP = {'schema': 1, 'repository': artifacts.REPOSITORY, 'version': VERSION, 'revision': REVISION}


def context(folder):
    return {'url': artifacts.SOURCE + '/releases/download/v' + VERSION + '/' + artifacts.filename(VERSION),
            'sha256': 'b' * 64, 'path': str(folder / artifacts.filename(VERSION))}


def raw(artifact):
    return {'name': 'amplifier-unified', 'version': VERSION, 'stamp': STAMP,
            'direct': {'url': Path(artifact['path']).as_uri(), 'archive_info': {}}}


def test_same_app_features_preserve_canonical_row_across_new_install_path(tmp_path):
    first, second = context(tmp_path / 'first'), context(tmp_path / 'second')
    graph = components.normalized([raw(first)], artifact=first)
    assert graph == components.normalized([raw(second)], artifact=second)
    assert graph == [{'name': 'amplifier-unified', 'version': VERSION, 'url': artifacts.SOURCE,
                      'revision': REVISION, 'delivery': {key: first[key] for key in ('url', 'sha256')}}]
    assert components.validate_graph(graph) == graph
    assert components.requirements(graph) == '\n'
    assert str(tmp_path) not in json.dumps(graph)


@pytest.mark.parametrize('fault', ['missing-context', 'other-origin', 'missing-stamp', 'fork-stamp',
    'wrong-version', 'editable', 'mixed-vcs', 'nonroot-archive'])
def test_archive_requires_exact_qualified_context(tmp_path, fault):
    artifact = context(tmp_path)
    row = raw(artifact)
    if fault == 'missing-context':
        artifact = None
    elif fault == 'other-origin':
        row['direct']['url'] = 'file:///another/' + artifacts.filename(VERSION)
    elif fault == 'missing-stamp':
        row.pop('stamp')
    elif fault == 'fork-stamp':
        row['stamp'] = {**STAMP, 'repository': 'someone/fork'}
    elif fault == 'wrong-version':
        row['stamp'] = {**STAMP, 'version': '98.0.0'}
    elif fault == 'editable':
        row['direct']['dir_info'] = {'editable': True}
    elif fault == 'mixed-vcs':
        row['direct']['vcs_info'] = {'vcs': 'git', 'commit_id': REVISION}
    else:
        row['name'] = 'other-package'
    with pytest.raises(ValueError):
        components.normalized([row], artifact=artifact)


def test_legacy_git_graph_and_digest_remain_identical():
    rows = [{'name': 'amplifier-unified', 'version': VERSION,
             'direct': {'url': artifacts.SOURCE, 'vcs_info': {'vcs': 'git', 'commit_id': REVISION}}},
            {'name': 'example-package', 'version': '1.2.3', 'direct': None}]
    graph = components.normalized(rows)
    old_wire = ('[{"name":"amplifier-unified","revision":"' + REVISION + '","url":"' + artifacts.SOURCE
                + '","version":"99.0.0"},{"name":"example-package","version":"1.2.3"}]').encode()
    assert components.digest(graph) == hashlib.sha256(old_wire).hexdigest()
    assert components.validate_graph(graph) == graph
    assert components.requirements(graph) == 'example-package==1.2.3\n'


@pytest.mark.parametrize('fault', ['other-name', 'subdirectory', 'bad-delivery', 'missing-revision'])
def test_structural_graph_validation_does_not_accept_other_archive_shapes(tmp_path, fault):
    artifact = context(tmp_path)
    graph = components.normalized([raw(artifact)], artifact=artifact)
    if fault == 'other-name':
        graph[0]['name'] = 'another-package'
    elif fault == 'subdirectory':
        graph[0]['subdirectory'] = 'nested'
    elif fault == 'bad-delivery':
        graph[0]['delivery']['sha256'] = 'bad'
    else:
        graph[0].pop('revision')
    with pytest.raises(ValueError):
        components.validate_graph(graph)


async def test_probe_reads_stamp_without_importing_target_helper(tmp_path):
    package = tmp_path / 'amplifier_web'
    package.mkdir()
    (package / '__init__.py').write_text('raise AssertionError("target app must not be imported")')
    (package / 'release-source.json').write_text(json.dumps(STAMP))
    dist = tmp_path / ('amplifier_unified-' + VERSION + '.dist-info')
    dist.mkdir()
    (dist / 'METADATA').write_text('Name: amplifier-unified\nVersion: ' + VERSION + '\n')
    artifact = context(tmp_path)
    (dist / 'direct_url.json').write_text(json.dumps(raw(artifact)['direct']))
    observed_commands = []
    async def process(*args, **options):
        observed_commands.append((args, options))
        # Model a target environment containing only this real dist-info fixture.
        script = ('import importlib.metadata as m\noriginal=m.distributions\n'
                  'm.distributions=lambda:original(path=[' + repr(str(tmp_path)) + '])\n' + args[-1])
        return subprocess.check_output([sys.executable, '-I', '-c', script], text=True)
    graph = await components.read_graph(process, sys.executable, artifact=artifact)
    assert graph == components.normalized([raw(artifact)], artifact=artifact)
    assert observed_commands[0][1] == {'timeout': 30}
