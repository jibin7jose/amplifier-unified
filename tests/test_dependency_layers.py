"""Source-free identity, source finalization and exact shared-image custody."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import threading
import time

import pytest

from amplifier_web import test_dependency_layers as layers
from amplifier_web import test_environment_preparation as preparation

NATIVE_OBSERVATION = layers.native  # Exercise the real reader with fake engine bytes.
BACKEND_IDENTITY = preparation.backend_identity

PROJECT = '[project]\nname="fixture"\nversion="1"\ndependencies=["third-party==1"]\n'
LOCK = '''version=1
[[package]]
name="fixture"
version="1"
source={editable="."}
[[package]]
name="third-party"
version="1"
source={registry="https://pypi.org/simple"}
wheels=[{url="https://files.pythonhosted.org/pkg.whl",hash="sha256:''' + 'a' * 64 + '"}]\n'


def source(root, body='first', project=PROJECT, lock=LOCK, extra=None):
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(root / 'source.tar', 'w') as archive:
        for name, data in {'pyproject.toml': project, 'uv.lock': lock, 'fixture.py': body, **(extra or {})}.items():
            item = tarfile.TarInfo(name)
            item.size = len(data.encode())
            archive.addfile(item, io.BytesIO(data.encode()))
    identity = {'backend': 'docker', 'daemon': 'fixture'}
    (root / 'backend-binding.json').write_text(json.dumps({'backendIdentity': identity}))
    return {'archive_sha256': preparation.digest(root / 'source.tar')}


def settle(root):
    # This unit adapter has no native images; model the canonical removal receipt.
    preparation.write(root / 'preparation/cleanup.json', {'complete': True, 'status': 'removed'})
    layers.release(root / 'preparation')


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    config = {'backend': 'docker', 'dockerHost': 'unix:///fixture.sock', 'image': 'python@sha256:' + '1' * 64}
    calls = []
    def adapter(config, folder, plan, env):
        with tarfile.open(folder / 'context/source.tar') as archive:
            contents = {item.name: archive.extractfile(item).read().decode() for item in archive}
        mode = json.loads((folder / 'context/plan.json').read_text()).get('dependencyMode')
        calls.append({'mode': mode, 'contents': contents, 'base': plan.get('_dependencyBase')})
        value = {'preparationId': plan['preparationId'], 'sourceSha256': plan['sourceSha256']}
        if mode == 'layer':
            value.update(dependencyKey=plan['dependencyKey'], preparedFiles={key: 'a' * 64 for key in layers.prepared_directories(plan['profileId'])})
        else:
            value['executionContract'] = {**plan['executionContract'], 'pythonBuild': {'available': False, 'reason': 'This source declares no Python build backend.'}}
        return 'sha256:' + hashlib.sha256(plan['resourceName'].encode()).hexdigest(), json.dumps(value)
    monkeypatch.setattr(preparation, '_docker', adapter)
    monkeypatch.setattr(preparation, 'backend_identity', lambda *args, **kwargs: {'backend': 'docker', 'daemon': 'fixture'})
    monkeypatch.setattr(layers, 'native', lambda *args, **kwargs: 100)
    def run(name, body='first', profile='python-project', **kwargs):
        root = tmp_path / name
        snapshot = source(root, body, **kwargs)
        result = preparation.prepare(config, root, snapshot, profile, 'request-' + name,
            host_reservation={'admitted': True}, dependency_store=layers.store(tmp_path))
        return root, result
    return run, calls, config


def test_changed_source_reuses_only_dependencies_and_finalizes_current_source(prepared, tmp_path):
    run, calls, _ = prepared
    first, receipt1 = run('first')
    second, receipt2 = run('second', 'changed source')
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'finalize']
    assert set(calls[0]['contents']) == {'pyproject.toml', 'uv.lock'}
    assert calls[1]['contents']['fixture.py'] == 'first'
    assert calls[2]['contents']['fixture.py'] == 'changed source'
    assert calls[1]['base'] == calls[2]['base']
    assert receipt1['image'] != receipt2['image']
    assert receipt1['sourceSha256'] != receipt2['sourceSha256']
    assert layers.read(first / 'preparation/dependency-reuse.json')['hit'] is False
    assert layers.read(second / 'preparation/dependency-reuse.json')['hit'] is True
    assert len(list(layers.store(tmp_path).glob('*/borrowers/*.json'))) == 2
    assert layers.inspect(tmp_path)['candidates'] == []
    settle(first)
    assert layers.inspect(tmp_path)['candidates'] == []
    settle(second)
    assert len(layers.inspect(tmp_path)['candidates']) == 1


@pytest.mark.parametrize('root_source', ['editable', 'virtual'])
def test_root_version_change_reuses_dependencies_but_finalizes_exact_current_manifests(prepared, tmp_path, root_source):
    run, calls, _ = prepared
    locked = LOCK.replace('editable=', root_source + '=')
    first, before = run('first', lock=locked)
    project = PROJECT.replace('version="1"', 'version="2"')
    changed_lock = locked.replace('version="1"', 'version="2"', 1)
    second, after = run('second', 'release source', project=project, lock=changed_lock)
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'finalize']
    assert calls[2]['contents']['pyproject.toml'] == project
    assert calls[2]['contents']['uv.lock'] == changed_lock
    assert calls[2]['contents']['fixture.py'] == 'release source'
    assert calls[1]['base'] == calls[2]['base']
    assert before['lockfiles'] != after['lockfiles']
    assert before['sourceSha256'] != after['sourceSha256']
    assert before['image'] != after['image']
    assert layers.read(second / 'preparation/dependency-reuse.json')['hit'] is True
    entry = layers.store(tmp_path) / layers.read(first / 'preparation/dependency-reuse.json')['entry']
    assert layers.receipt(entry)['intent']['lockfiles'] == before['lockfiles']
    assert layers.read(first / 'preparation/receipt.json') == before
    settle(first)
    settle(second)
    assert len(layers.inspect(tmp_path)['candidates']) == 1


@pytest.mark.parametrize('project,lock', [
    (PROJECT.replace('third-party==1', 'third-party==2'), LOCK),
    (PROJECT + 'requires-python=">=3.12"\n', LOCK),
    (PROJECT + '[dependency-groups]\ndev=["pytest"]\n', LOCK),
    (PROJECT + '[tool.uv]\nno-build=true\n', LOCK),
    (PROJECT + '[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n', LOCK),
    (PROJECT, LOCK.replace('name="third-party"\nversion="1"', 'name="third-party"\nversion="2"')),
    (PROJECT, LOCK.replace('sha256:' + 'a' * 64, 'sha256:' + 'b' * 64)),
    (PROJECT, LOCK.replace('source={editable="."}', 'source={editable="."}\ndependencies=[{name="third-party"}]')),
])
def test_non_version_dependency_inputs_still_build_new_seed(prepared, project, lock):
    run, calls, _ = prepared
    run('first')
    second, _ = run('changed', project=project, lock=lock)
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'layer', 'finalize']
    assert layers.read(second / 'preparation/dependency-reuse.json')['hit'] is False


@pytest.mark.parametrize('change', ['lock', 'manifest', 'base', 'daemon', 'recipe'])
def test_each_qualified_dependency_input_changes_key(tmp_path, change):
    snapshot = source(tmp_path / 'source')
    value = preparation.plan(tmp_path / 'source/source.tar', snapshot, 'python-project')
    material = layers.inputs(tmp_path / 'source/source.tar', value)
    value.update(backendIdentity={'backend': 'docker', 'daemon': 'first'}, baseImage='one')
    original = layers.key_for(value, material)
    if change in {'lock', 'manifest'}:
        material['lockfiles'][0 if change == 'manifest' else 1]['sha256'] = 'b' * 64
    elif change == 'base':
        value['baseImage'] = 'two'
    elif change == 'daemon':
        value['backendIdentity']['daemon'] = 'second'
    else:
        value['recipeSha256'] = 'c' * 64
    assert layers.key_for(value, material) != original


def test_procps_recipe_change_invalidates_preparation_and_reusable_layer(prepared, tmp_path, monkeypatch):
    run, calls, config = prepared
    recipes = tmp_path / 'recipes'
    recipes.mkdir()
    module = Path(preparation.__file__)
    for name in (*preparation.RECIPE_FILES, module.name):
        (recipes / name).write_bytes(module.with_name(name).read_bytes())
    monkeypatch.setattr(preparation, '__file__', str(recipes / module.name))
    recipe = recipes / 'test_environment_prepare_guest.py'
    current = recipe.read_text()
    legacy = current.replace(", 'procps')", ')').replace(
        "    run('ps', '-axo', 'command=', capture=True)\n", '')
    assert legacy != current and "'procps'" not in legacy
    recipe.write_text(legacy)
    first, before = run('legacy')
    recipe.write_text(current)
    with pytest.raises(ValueError, match='different frozen source'):
        preparation.prepare(config, first, {'archive_sha256': before['sourceSha256']},
                            'python-project', 'request-legacy',
                            host_reservation={'admitted': True}, dependency_store=layers.store(tmp_path))
    second, after = run('procps')
    assert before['sourceSha256'] == after['sourceSha256']
    assert before['lockfiles'] == after['lockfiles']
    assert before['recipeSha256'] != after['recipeSha256']
    assert before['intent']['preparationId'] != after['intent']['preparationId']
    assert layers.key_for(before['intent'], layers.inputs(first / 'source.tar', before['intent'])) != layers.key_for(
        after['intent'], layers.inputs(second / 'source.tar', after['intent']))
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'layer', 'finalize']
    assert layers.read(second / 'preparation/dependency-reuse.json')['hit'] is False
    third, same = run('same-procps')
    assert same['recipeSha256'] == after['recipeSha256']
    assert layers.read(third / 'preparation/dependency-reuse.json')['hit'] is True
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'layer', 'finalize', 'finalize']
    # No old receipt was rewritten to claim the new prerequisite.
    assert layers.read(first / 'preparation/receipt.json') == before


@pytest.mark.parametrize('project,lock', [
    (PROJECT.replace('version="1"', 'dynamic=["version"]'), LOCK),
    (PROJECT + '[tool.uv.workspace]\nmembers=["child"]', LOCK),
    (PROJECT, LOCK.replace('registry="https://pypi.org/simple"', 'path="../other"')),
    (PROJECT, LOCK.replace('registry="https://pypi.org/simple"', 'git="https://example.test/repo?branch=main"')),
    (PROJECT, LOCK.replace('https://pypi.org/simple', 'https://secret@example.test/simple')),
    (PROJECT, LOCK.replace('sha256:' + 'a' * 64, 'sha256:incomplete')),
    (PROJECT + '[build-system]\nbackend-path=["backend"]', LOCK),
])
def test_unsupported_sources_stay_on_existing_cold_path(prepared, project, lock):
    run, calls, _ = prepared
    root, result = run('cold', project=project, lock=lock)
    assert result['status'] == 'ready'
    assert [call['mode'] for call in calls] == [None]
    assert not (root / 'preparation/dependency-borrow.json').exists()


def test_resolved_git_commit_eligible_and_source_policy_not_rewritten(tmp_path):
    commit = 'c' * 40
    locked = LOCK.replace('registry="https://pypi.org/simple"', 'git="https://example.test/repo?branch=main#' + commit + '"')
    snapshot = source(tmp_path / 'source', lock=locked)
    expected = preparation.plan(tmp_path / 'source/source.tar', snapshot, 'python-project')
    material = layers.inputs(tmp_path / 'source/source.tar', expected)
    assert material['files']['uv.lock'] == locked.encode()
    assert '?branch=main#' + commit in material['files']['uv.lock'].decode()
    expected.update(backendIdentity={'backend': 'docker', 'daemon': 'one'}, baseImage='base')
    before = layers.key_for(expected, material)
    snapshot = source(tmp_path / 'source', lock=locked.replace(commit, 'd' * 40))
    changed = preparation.plan(tmp_path / 'source/source.tar', snapshot, 'python-project')
    changed.update(backendIdentity=expected['backendIdentity'], baseImage='base')
    assert layers.key_for(changed, layers.inputs(tmp_path / 'source/source.tar', changed)) != before


def test_changed_frozen_archive_refused(tmp_path):
    snapshot = source(tmp_path / 'source')
    expected = preparation.plan(tmp_path / 'source/source.tar', snapshot, 'python-project')
    source(tmp_path / 'source', body='changed after freeze')
    with pytest.raises(ValueError, match='changed'):
        layers.inputs(tmp_path / 'source/source.tar', expected)


def test_concurrent_first_use_builds_one_seed(prepared):
    run, calls, _ = prepared
    errors = []
    def execute(name):
        try:
            run(name, name)
        except BaseException as error:
            errors.append(error)
    workers = [threading.Thread(target=execute, args=(name,)) for name in ('first', 'second')]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert errors == []
    assert [call['mode'] for call in calls].count('layer') == 1
    assert [call['mode'] for call in calls].count('finalize') == 2


def test_uncertain_finalizer_keeps_borrow_until_exact_cleanup(prepared, tmp_path, monkeypatch):
    run, calls, config = prepared
    original = preparation._docker
    def fail(config, folder, plan, env):
        if plan.get('_dependencyBase'):
            raise TimeoutError('Lost finalizer reply')
        return original(config, folder, plan, env)
    monkeypatch.setattr(preparation, '_docker', fail)
    with pytest.raises(TimeoutError):
        run('uncertain')
    leases = list(layers.store(tmp_path).glob('*/borrowers/*.json'))
    assert len(leases) == 1
    assert layers.inspect(tmp_path)['candidates'] == []
    monkeypatch.setattr(preparation, 'cleanup_interrupted', lambda *args: {'complete': False, 'status': 'unknown'})
    assert preparation.cleanup(config, tmp_path / 'uncertain')['complete'] is False
    assert leases[0].exists()
    def removed(config, folder):
        preparation.write(folder / 'cleanup.json', {'complete': True, 'status': 'removed'})
        return {'complete': True, 'status': 'removed'}
    monkeypatch.setattr(preparation, 'cleanup_interrupted', removed)
    assert preparation.cleanup(config, tmp_path / 'uncertain')['complete'] is True
    assert not leases[0].exists()
    assert len(layers.inspect(tmp_path)['candidates']) == 1


def test_unknown_seed_is_not_replayed_and_other_task_uses_cold_path(prepared, monkeypatch):
    run, calls, _ = prepared
    original = preparation._docker
    def fail(config, folder, plan, env):
        if plan.get('dependencyMode') == 'layer':
            raise TimeoutError('Publication response lost')
        return original(config, folder, plan, env)
    monkeypatch.setattr(preparation, '_docker', fail)
    with pytest.raises(TimeoutError):
        run('first')
    monkeypatch.setattr(preparation, '_docker', original)
    root, result = run('second')
    assert result['status'] == 'ready'
    assert [call['mode'] for call in calls] == [None]
    assert not (root / 'preparation/dependency-borrow.json').exists()


def test_cleanup_borrow_race_and_policy_guard_keep_image(prepared, tmp_path, monkeypatch):
    run, _, config = prepared
    root, _ = run('first')
    settle(root)
    name = layers.inspect(tmp_path)['candidates'][0]['id']
    entry = layers.store(tmp_path) / name
    preparation.write(entry / 'borrowers/new.json', {'live': True})
    attempts = []
    layers.retire(tmp_path, {name}, lambda *args: attempts.append(args))
    assert attempts == []
    (entry / 'borrowers/new.json').unlink()
    def cleanup(config, entry, *, cleanup_guard):
        cleanup_guard()
        pytest.fail('A disabled policy must not reach removal')
    monkeypatch.setattr(preparation, 'cleanup', cleanup)
    def guard(status, item):
        attempts.append(status)
        if len(attempts) == 2:
            raise ValueError('Policy disabled while observing native state')
    layers.retire(tmp_path, {name}, guard)
    assert attempts == ['attempting', 'attempting']
    assert not (entry / 'preparation/cleanup.json').exists()


def test_retired_layer_rebuild_has_new_owner_identity(prepared, tmp_path, monkeypatch):
    run, calls, _ = prepared
    root, _ = run('first')
    settle(root)
    row = layers.inspect(tmp_path)['candidates'][0]
    def cleanup(config, entry, *, cleanup_guard):
        cleanup_guard()
        preparation.write(entry / 'preparation/cleanup.json', {'complete': True, 'status': 'removed'})
        return {'complete': True}
    monkeypatch.setattr(preparation, 'cleanup', cleanup)
    layers.retire(tmp_path, {row['id']}, lambda *args: None)
    second, _ = run('second')
    settle(second)
    assert [call['mode'] for call in calls] == ['layer', 'finalize', 'layer', 'finalize']
    assert layers.inspect(tmp_path)['candidates'][0]['id'] != row['id']


def test_record_symlink_and_foreign_pointer_are_refused(prepared, tmp_path):
    run, _, _ = prepared
    run('first')
    directory = layers.store(tmp_path)
    pointer = next(path for path in directory.glob('*.json') if path.name != 'inspection-cursor.json')
    preparation.write(pointer, {'entry': '../outside'})
    with pytest.raises(ValueError, match='reference'):
        run('second')
    pointer.unlink()
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    pointer.symlink_to(outside)
    with pytest.raises(ValueError, match='links'):
        run('third')


def test_current_unified_manifests_are_eligible_with_public_registry_and_git(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    snapshot = source(tmp_path / 'source', project=(repository / 'pyproject.toml').read_text(), lock=(repository / 'uv.lock').read_text())
    expected = preparation.plan(tmp_path / 'source/source.tar', snapshot, 'python-project')
    assert layers.inputs(tmp_path / 'source/source.tar', expected)['project'] == 'amplifier-unified'


def test_seed_effect_is_task_owned_before_creation_and_unknown_cleanup_holds(prepared, tmp_path, monkeypatch):
    run, calls, config = prepared
    original = preparation._docker
    def fail(config, folder, plan, env):
        if plan.get('dependencyMode') == 'layer':
            task = Path(plan['_cancelFolder'])
            saved = layers.read(task / 'dependency-seed.json')
            assert saved['entry'] == folder.parent.name
            assert saved['resourceName'] == layers.read(task / 'intent.json')['resourceName']
            assert layers.owner_record(folder.parent)['origin']['task'] == str(task)
            raise TimeoutError('Native publication is uncertain')
        return original(config, folder, plan, env)
    monkeypatch.setattr(preparation, '_docker', fail)
    with pytest.raises(TimeoutError):
        run('unknown-seed')
    visited = []
    def uncertain(config, folder):
        visited.append(folder)
        return {'complete': False, 'status': 'unknown'}
    monkeypatch.setattr(preparation, 'cleanup_interrupted', uncertain)
    result = preparation.cleanup(config, tmp_path / 'unknown-seed')
    assert result['complete'] is False and result['dependencySeed']['status'] == 'unknown'
    assert len(visited) == 1 and visited[0].parent.name.startswith(layers.read(tmp_path / 'unknown-seed/preparation/dependency-seed.json')['entry'])
    assert visited[0] != tmp_path / 'unknown-seed/preparation'
    def removed(config, folder):
        visited.append(folder)
        preparation.write(folder / 'cleanup.json', {'complete': True, 'status': 'removed'})
        return {'complete': True, 'status': 'removed'}
    monkeypatch.setattr(preparation, 'cleanup_interrupted', removed)
    assert preparation.cleanup(config, tmp_path / 'unknown-seed')['complete'] is True
    assert visited[-1] == tmp_path / 'unknown-seed/preparation'
    assert (tmp_path / 'unknown-seed/preparation/dependency-seed-settled.json').exists()


def test_seed_receipt_and_owner_projection_tampering_refused(prepared, tmp_path):
    run, _, _ = prepared
    first, _ = run('first')
    link = layers.read(first / 'preparation/dependency-reuse.json')
    entry = layers.store(tmp_path) / link['entry']
    value = layers.read(entry / 'preparation/receipt.json')
    value['intent']['baseImage'] = 'another'
    preparation.write(entry / 'preparation/receipt.json', value)
    with pytest.raises(ValueError, match='inventory'):
        run('second')
    assert preparation.cleanup({}, first)['complete'] is False


def test_seed_owner_config_tampering_refused(prepared, tmp_path):
    run, _, _ = prepared
    first, _ = run('first')
    entry = layers.store(tmp_path) / layers.read(first / 'preparation/dependency-reuse.json')['entry']
    value = layers.read(entry / 'owner.json')
    value['config']['image'] = 'another'
    preparation.write(entry / 'owner.json', value)
    with pytest.raises(ValueError, match='configuration'):
        run('second')


def test_prepared_image_borrow_cannot_release_before_its_removal(prepared, tmp_path):
    run, _, _ = prepared
    root, _ = run('first')
    with pytest.raises(ValueError, match='not confirmed removed'):
        layers.release(root / 'preparation')
    assert len(list(layers.store(tmp_path).glob('*/borrowers/*.json'))) == 1


def test_recent_reuse_invalidates_stale_storage_selection(prepared, tmp_path, monkeypatch):
    run, _, _ = prepared
    first, _ = run('first')
    settle(first)
    row = layers.inspect(tmp_path)['candidates'][0]
    second, _ = run('second')
    settle(second)
    calls = []
    monkeypatch.setattr(preparation, 'cleanup', lambda *args, **kwargs: calls.append(args))
    layers.retire(tmp_path, {row['id']: row}, lambda *args: calls.append(args))
    assert calls == []


def test_no_shared_lock_support_keeps_ordinary_preparation(prepared, tmp_path, monkeypatch):
    run, calls, _ = prepared
    monkeypatch.setattr(preparation, 'fcntl', None)
    root, result = run('unsupported-lock')
    assert result['status'] == 'ready'
    assert [row['mode'] for row in calls] == [None]
    assert not layers.store(tmp_path).exists()
    assert not (root / 'preparation/dependency-borrow.json').exists()


def test_docker_size_excludes_shared_base_content(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, 'backend_identity', lambda *args, **kwargs: {'backend': 'docker', 'daemon': 'fixture'})
    image, name = 'sha256:' + 'a' * 64, 'au-deps-fixture'
    replies = [json.dumps([{'Id': image, 'Config': {'Labels': {'io.amplifier.preparation': name}},
                           'RepoTags': [name + ':latest'], 'Size': 1000}]),
               json.dumps([{'Size': 800}])]
    monkeypatch.setattr(preparation, 'run', lambda *args, **kwargs: replies.pop(0))
    config = {'backend': 'docker', 'dockerHost': 'unix:///fixture.sock'}
    value = {'backend': 'docker', 'image': image, 'intent': {'resourceName': name,
             'backendIdentity': {'backend': 'docker', 'daemon': 'fixture'}, 'baseImage': 'base@sha256:' + '1' * 64}}
    assert layers.native(config, tmp_path, value) == 200
    assert not replies


@pytest.mark.parametrize('error', [ValueError('Image is absent'), ValueError('Malformed native reply'), TimeoutError('Backend unavailable'), OSError('Engine command unavailable')])
def test_unavailable_native_layer_keeps_cold_path_without_cache_mutation(prepared, tmp_path, monkeypatch, error):
    run, calls, _ = prepared
    first, _ = run('first')
    settle(first)
    entry = layers.store(tmp_path) / layers.read(first / 'preparation/dependency-reuse.json')['entry']
    before = {str(path.relative_to(entry)): path.read_bytes() for path in entry.rglob('*') if path.is_file()}
    def unavailable(*args, **kwargs):
        raise error
    monkeypatch.setattr(layers, 'native', unavailable)
    second, result = run('second', 'fresh source')
    assert result['status'] == 'ready'
    assert [row['mode'] for row in calls] == ['layer', 'finalize', None]
    assert calls[-1]['contents']['fixture.py'] == 'fresh source'
    assert not (second / 'preparation/dependency-borrow.json').exists()
    assert not (second / 'preparation/dependency-seed.json').exists()
    assert before == {str(path.relative_to(entry)): path.read_bytes() for path in entry.rglob('*') if path.is_file()}


@pytest.mark.parametrize('reply', [None, {}, [None], [{}]])
def test_actual_native_reader_malformed_json_shape_falls_back(prepared, monkeypatch, reply):
    run, calls, _ = prepared
    first, _ = run('first')
    settle(first)
    monkeypatch.setattr(layers, 'native', NATIVE_OBSERVATION)
    monkeypatch.setattr(preparation, 'run', lambda *args, **kwargs: json.dumps(reply))
    second, result = run('second', 'fresh source')
    assert result['status'] == 'ready'
    assert [row['mode'] for row in calls] == ['layer', 'finalize', None]
    assert not (second / 'preparation/dependency-borrow.json').exists()
    assert not (second / 'preparation/dependency-seed.json').exists()


@pytest.mark.parametrize('malformed', ['image-null', 'image-row-null', 'config-null', 'labels-null', 'tag-null', 'base-row-null', 'daemon-null', 'daemon-row-null'])
def test_actual_malformed_native_observation_stays_protected_in_storage(prepared, tmp_path, monkeypatch, malformed):
    run, _, _ = prepared
    first, _ = run('first')
    settle(first)
    row = layers.inspect(tmp_path)['candidates'][0]
    entry = layers.store(tmp_path) / row['id']
    value = layers.receipt(entry)
    image, name = value['image'], value['intent']['resourceName']
    reply = [{'Id': image, 'Config': {'Labels': {'io.amplifier.preparation': name}},
              'RepoTags': [name + ':latest'], 'Size': 1000}]
    if malformed == 'image-null':
        reply = None
    elif malformed == 'image-row-null':
        reply = [None]
    elif malformed == 'config-null':
        reply[0]['Config'] = None
    elif malformed == 'labels-null':
        reply[0]['Config']['Labels'] = None
    elif malformed == 'tag-null':
        reply[0]['RepoTags'] = [None]
    before = {str(path.relative_to(entry)): path.read_bytes() for path in entry.rglob('*') if path.is_file()}
    task_before = {str(path.relative_to(first)): path.read_bytes() for path in first.rglob('*') if path.is_file()}
    commands, removals = [], []
    def observe(args, *unused, **kwargs):
        commands.append(args)
        if 'info' in args:
            return json.dumps(None if malformed == 'daemon-null' else [None] if malformed == 'daemon-row-null' else {'ID': 'fixture'})
        if 'ps' in args:
            return ''
        assert args[-2] == 'inspect'
        return json.dumps(reply if args[-1] == image else [None] if malformed == 'base-row-null' else [{'Size': 800}])
    monkeypatch.setattr(layers, 'native', NATIVE_OBSERVATION)
    monkeypatch.setattr(preparation, 'backend_identity', BACKEND_IDENTITY)
    monkeypatch.setattr(preparation, 'run', observe)
    monkeypatch.setattr(preparation, 'cleanup', lambda *args, **kwargs: removals.append(args))
    report = layers.inspect(tmp_path)
    assert report == {'candidates': [], 'protectedIds': [entry.name], 'complete': True}
    layers.retire(tmp_path, {row['id']: row}, lambda *args: removals.append(args))
    assert sum('info' in args for args in commands) == 2  # Both callers reach the actual native reader.
    assert removals == []
    assert before == {str(path.relative_to(entry)): path.read_bytes() for path in entry.rglob('*') if path.is_file()}
    assert task_before == {str(path.relative_to(first)): path.read_bytes() for path in first.rglob('*') if path.is_file()}
    assert list((entry / 'borrowers').iterdir()) == []


@pytest.mark.parametrize('malformed', ['image-row-null', 'properties-null', 'alias-null', 'instance-row-null', 'instance-config-null', 'daemon-environment-null'])
def test_actual_incus_nested_observations_raise_validation_error(tmp_path, monkeypatch, malformed):
    image, name = 'a' * 64, 'au-deps-fixture'
    backend = {'backend': 'incus', 'daemon': 'fixture', 'project': 'owned'}
    config = {'backend': 'incus', 'incus': {'incusExecutable': 'incus', 'project': 'owned', 'configDir': str(tmp_path)}}
    receipt = {'backend': 'incus', 'image': image, 'intent': {'resourceName': name, 'backendIdentity': backend}}
    reply = [{'fingerprint': image, 'properties': {'user.amplifier.preparation': name}, 'aliases': [{'name': name}], 'size': 200}]
    if malformed == 'image-row-null':
        reply = [None]
    elif malformed == 'properties-null':
        reply[0]['properties'] = None
    elif malformed == 'alias-null':
        reply[0]['aliases'] = [None]
    def observe(args, *unused, **kwargs):
        if 'query' in args:
            return json.dumps({'environment': None if malformed == 'daemon-environment-null' else {'project': 'owned', 'certificate_fingerprint': 'fixture'}})
        if 'image' in args:
            return json.dumps(reply)
        return json.dumps([None] if malformed == 'instance-row-null' else [{'config': None}])
    monkeypatch.setattr(preparation, 'backend_identity', BACKEND_IDENTITY)
    monkeypatch.setattr(preparation, 'run', observe)
    with pytest.raises(ValueError):
        NATIVE_OBSERVATION(config, tmp_path, receipt, unused=True)
