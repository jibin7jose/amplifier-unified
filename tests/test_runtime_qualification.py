"""Fresh dependency preparation must be isolated from frozen generation replay."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import runtime_environment, runtime_qualification, updates
from amplifier_web.updates import UpdateManager


@pytest.mark.parametrize('platform', ['windows', 'posix'])
@pytest.mark.parametrize('relative', ['modules/fixture', '.', 'modules/with % and \u03bb'])
def test_cache_subdirectory_uses_uri_separators(platform, relative):
    from pathlib import PurePosixPath, PureWindowsPath
    path_type = PureWindowsPath if platform == 'windows' else PurePosixPath
    root = path_type('C:/fixture/cache' if platform == 'windows' else '/fixture/cache')
    assert runtime_qualification._cache_subdirectory(root / relative, root) == relative


def test_cache_subdirectory_does_not_accept_an_external_path():
    from pathlib import PureWindowsPath
    with pytest.raises(ValueError):
        runtime_qualification._cache_subdirectory(
            PureWindowsPath('C:/outside/module'), PureWindowsPath('C:/fixture/cache'))


@pytest.mark.parametrize('authority', ['', 'localhost', 'fixture-server'])
def test_installed_graph_decodes_file_uri_with_platform_path_rules(tmp_path, monkeypatch, authority):
    """PEP 610 file URLs must not become /C:/ paths relative to a UNC cwd."""
    from urllib import request
    source = tmp_path / 'source with % and \u03bb'
    source.mkdir()
    project = tmp_path / 'project'
    metadata = project / '.venv/Lib/site-packages/fixture-1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n')
    reference = source.as_uri()
    if authority:
        reference = reference.replace('file:///', 'file://' + authority + '/', 1)
    (metadata / 'direct_url.json').write_text(json.dumps({'url': reference, 'dir_info': {}}))
    calls = []
    def decode(value):
        calls.append(value)
        # A controlled platform decoder lets the authority cases run without
        # contacting a remote share. The ordinary case below uses the real one.
        return str(source)
    monkeypatch.setattr(request, 'url2pathname', decode)
    graph = runtime_qualification.installed_graph(project)
    assert len(graph) == 1 and graph[0]['path'] == str(source.resolve())
    parsed = runtime_qualification.urlsplit(reference)
    prefix = '//' + authority if authority and authority != 'localhost' else ''
    assert calls == [prefix + parsed.path]
    assert graph[0]['directUrl']['url'] == reference


@pytest.mark.parametrize('authority', ['', 'localhost', 'LOCALHOST', 'LocalHost'])
def test_installed_graph_real_file_uri_preserves_absolute_path(tmp_path, authority):
    source = tmp_path / 'source with % and \u03bb'
    source.mkdir()
    project = tmp_path / 'project'
    metadata = project / '.venv/Lib/site-packages/fixture-1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n')
    reference = source.as_uri()
    if authority:
        reference = reference.replace('file:///', 'file://' + authority + '/', 1)
    (metadata / 'direct_url.json').write_text(json.dumps({'url': reference, 'dir_info': {}}))
    graph = runtime_qualification.installed_graph(project)
    assert graph[0]['path'] == str(source.resolve())
    assert graph[0]['directUrl']['url'] == reference


@pytest.mark.parametrize('policy', [None, {'QUALIFICATION_FIXTURE': 'child-only'}])
def test_installed_graph_file_uri_keeps_git_identity_and_dirty_refusal(tmp_path, monkeypatch, policy):
    import subprocess
    import os
    child_env = None if policy is None else {**os.environ, **policy}
    monkeypatch.setattr(runtime_qualification, '_installed_git_environment', lambda: child_env)
    original = subprocess.check_output
    reads = []
    def observe(*args, **kwargs):
        if args[0][:2] in (['git', 'rev-parse'], ['git', '--no-optional-locks'], ['git', 'ls-files']):
            reads.append(kwargs.get('env'))
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime_qualification.subprocess, 'check_output', observe)
    source = tmp_path / 'cache % \u03bb'
    source.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@example.invalid', *args], cwd=source, text=True).strip()
    git('init')
    tracked = source / 'tracked.txt'
    tracked.write_text('original\n')
    git('add', 'tracked.txt')
    git('commit', '-m', 'fixture')
    revision = git('rev-parse', 'HEAD')
    reads.clear()
    (source / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': 'https://example.invalid/fixture', 'ref': 'main', 'commit': revision}))
    project = tmp_path / 'project'
    metadata = project / '.venv/Lib/site-packages/amplifier_fixture-1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: amplifier-fixture\nVersion: 1.0\n')
    reference = source.as_uri()
    (metadata / 'direct_url.json').write_text(json.dumps({'url': reference, 'dir_info': {}}))
    clean = runtime_qualification.installed_graph(project)
    assert clean[0]['path'] == str(source.resolve())
    assert clean[0]['cacheSource']['revision'] == revision
    assert clean[0]['cacheSource']['dirty'] is False
    observed = runtime_environment.installed_sources(project, graph=clean)['amplifier-fixture']
    assert observed['url'] == 'https://example.invalid/fixture'
    assert observed['current'] == revision and not observed.get('override')
    tracked.write_text('changed\n')
    dirty = runtime_qualification.installed_graph(project)
    assert dirty[0]['cacheSource']['dirty'] is True
    assert runtime_environment.installed_sources(project, graph=dirty)['amplifier-fixture']['override'] is True
    assert dirty[0]['directUrl']['url'] == reference
    assert len(reads) == 6
    assert all(env is child_env for env in reads)


@pytest.mark.parametrize('platform', ['win32', 'linux', 'darwin'])
def test_installed_git_metadata_policy_is_windows_only(monkeypatch, platform):
    from amplifier_web import reset
    expected = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.longpaths', 'GIT_CONFIG_VALUE_0': 'true'}
    calls = []
    monkeypatch.setattr(runtime_qualification, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(reset, 'repository_environment', lambda: calls.append(True) or expected)
    assert runtime_qualification._installed_git_environment() == (expected if platform == 'win32' else None)
    assert calls == ([True] if platform == 'win32' else [])


def _windows_eol_fixture(tmp_path, monkeypatch, *, setting='true'):
    """A real CRLF checkout plus isolated personal config, without network."""
    import os
    import subprocess
    from amplifier_web import reset
    user = tmp_path / 'user'
    user.mkdir()
    config = (
        '[core]\n autocrlf = true\n'
        '[credential]\n helper = !DO_NOT_EXECUTE\n'
        '[http]\n extraHeader = Authorization: fixture-secret\n'
        '[url "ssh://DO_NOT_USE/"]\n insteadOf = https://example.invalid/\n'
        '[include]\n path = include.gitconfig\n')
    (user / '.gitconfig').write_text(config)
    (user / 'include.gitconfig').write_text('[core]\n autocrlf = false\n')
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('USERPROFILE', str(user))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(user))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(user / '.gitconfig'))
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', os.devnull)
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous')
    source = tmp_path / 'cache'
    source.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@example.invalid', *args], cwd=source).strip()
    git('init')
    tracked = source / 'tracked.txt'
    tracked.write_bytes(b'original\nsecond\n')
    (source / 'binary.dat').write_bytes(b'\x00binary\n')
    git('-c', 'core.autocrlf=false', 'add', '.')
    git('commit', '-m', 'fixture')
    tracked.write_bytes(b'original\r\nsecond\r\n')
    revision = git('rev-parse', 'HEAD').decode()
    (source / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': 'https://example.invalid/fixture', 'ref': 'main', 'commit': revision}))
    project = tmp_path / 'project'
    for name in ('amplifier_fixture_a', 'amplifier_fixture_b'):
        dist = project / '.venv/Lib/site-packages' / (name + '-1.0.dist-info')
        dist.mkdir(parents=True)
        (dist / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n')
        (dist / 'direct_url.json').write_text(json.dumps({'url': source.as_uri(), 'dir_info': {}}))
    (user / '.gitconfig').write_text(config.replace('autocrlf = true', 'autocrlf = ' + setting))
    isolated = reset.repository_environment(anonymous=True)
    monkeypatch.setattr(runtime_qualification, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(runtime_qualification, '_installed_git_environment', lambda: isolated)
    return source, project, isolated, git


def test_anonymous_windows_metadata_preserves_eol_without_personal_auth(tmp_path, monkeypatch):
    import hashlib
    import os
    import subprocess
    source, project, isolated, git = _windows_eol_fixture(tmp_path, monkeypatch)
    # Actual predecessor discriminator: the anonymous environment alone reports M.
    assert subprocess.check_output(['git', '--no-optional-locks', 'status',
        '--porcelain', '--untracked-files=no'], cwd=source, env=isolated).strip()
    before_env = dict(os.environ)
    before_index = hashlib.sha256((source / '.git/index').read_bytes()).hexdigest()
    before_content = (source / 'tracked.txt').read_bytes()
    original = runtime_qualification.subprocess.run
    lookups = []
    def observe(argv, **kwargs):
        if 'config' in argv:
            lookups.append(argv)
            assert argv == ['git', '--no-optional-locks', 'config', '--no-includes',
                            '--get', 'core.autocrlf']
        return original(argv, **kwargs)
    monkeypatch.setattr(runtime_qualification.subprocess, 'run', observe)
    graph = runtime_qualification.installed_graph(project)
    assert len(graph) == 2 and not any(row['cacheSource']['dirty'] for row in graph)
    assert len(lookups) == 1  # One scalar lookup per distinct checkout, not distribution.
    assert hashlib.sha256((source / '.git/index').read_bytes()).hexdigest() == before_index
    assert (source / 'tracked.txt').read_bytes() == before_content
    assert dict(os.environ) == before_env
    monkeypatch.setattr(runtime_qualification.subprocess, 'run', original)
    child = runtime_qualification._installed_checkout_environment(source, isolated)
    assert child['GIT_CONFIG_GLOBAL'] == os.devnull and child['GIT_CONFIG_SYSTEM'] == os.devnull
    pairs = {child[f'GIT_CONFIG_KEY_{i}']: child[f'GIT_CONFIG_VALUE_{i}']
             for i in range(int(child['GIT_CONFIG_COUNT']))}
    assert pairs['core.autocrlf'] == 'true'
    assert pairs['credential.helper'] == '' and pairs['http.extraHeader'] == ''
    assert not any(key.startswith(('url.', 'include.')) for key in pairs)
    assert 'core.autocrlf' not in {isolated[f'GIT_CONFIG_KEY_{i}']
                                  for i in range(int(isolated['GIT_CONFIG_COUNT']))}


@pytest.mark.parametrize('change', ['text', 'binary', 'staged', 'staged-restored', 'local-false', 'local-input'])
def test_windows_eol_metadata_keeps_real_changes_and_explicit_local_policy(tmp_path, monkeypatch, change):
    source, project, isolated, git = _windows_eol_fixture(tmp_path, monkeypatch)
    if change == 'text':
        (source / 'tracked.txt').write_bytes(b'changed\r\nsecond\r\n')
    elif change == 'binary':
        (source / 'binary.dat').write_bytes(b'\x00binary\r\n')
    elif change in {'staged', 'staged-restored'}:
        (source / 'tracked.txt').write_bytes(b'changed\r\nsecond\r\n')
        git('-c', 'core.autocrlf=true', 'add', 'tracked.txt')
        if change == 'staged-restored':
            (source / 'tracked.txt').write_bytes(b'original\r\nsecond\r\n')
    else:
        git('config', '--local', 'core.autocrlf', change.removeprefix('local-'))
    graph = runtime_qualification.installed_graph(project)
    expected_dirty = change != 'local-input'  # input also cleans CRLF on add.
    assert all(row['cacheSource']['dirty'] is expected_dirty for row in graph)
    assert all(bool(row.get('override')) is expected_dirty for row in
               runtime_environment.installed_sources(project, graph=graph).values())
    if change.startswith('local-'):
        child = runtime_qualification._installed_checkout_environment(source, isolated)
        last = int(child['GIT_CONFIG_COUNT']) - 1
        assert child[f'GIT_CONFIG_VALUE_{last}'] == change.removeprefix('local-')


def test_windows_eol_metadata_rejects_malformed_policy(tmp_path, monkeypatch):
    source, project, isolated, git = _windows_eol_fixture(tmp_path, monkeypatch, setting='unexpected')
    with pytest.raises(ValueError, match='line-ending policy'):
        runtime_qualification.installed_graph(project)


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_checkout_eol_lookup_does_not_change_posix_policy(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(runtime_qualification, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(runtime_qualification.subprocess, 'run',
                        lambda *a, **k: pytest.fail('POSIX metadata must not read Windows EOL policy'))
    env = {'AMPLIFIER_GITHUB_ACCESS': 'anonymous'}
    assert runtime_qualification._installed_checkout_environment(tmp_path, env) is env


async def test_fresh_probe_freezes_then_uses_an_ordinary_resolver(tmp_path, monkeypatch):
    calls = []
    release = '2' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, release)
    receipt.mkdir(parents=True)
    first, frozen = tmp_path / 'first', tmp_path / 'frozen'
    selection = {'packages': {}, 'batchPaths': {}, 'protectedPaths': []}
    async def stage(manager, generation, candidates, *, finalize):
        assert not finalize
        calls.append('stage')
        return first
    async def freeze(manager, generation, project):
        assert project == first
        calls.append('freeze')
        (receipt / 'runtime-installed.json').write_text('[]')
        (frozen / '.venv').mkdir(parents=True)
        return frozen
    async def overrides(project, target, *, owned_root):
        assert owned_root == tmp_path
        calls.append(('overrides', project))
        return target
    def verify(project, target):
        assert project == frozen and target == receipt
        calls.append('verify')
    class Diagnostics:
        state = {'attemptId': release}
        async def run(self, phase, function, *command, **kwargs):
            if phase == 'ecosystem-source-preflight':
                assert calls[-1] == 'collect'
                assert kwargs == {'selection': selection}
                calls.append('source-alignment')
                return await function(*command, **kwargs)
            if phase == 'ecosystem-runtime-policy':
                return await function(*command, **kwargs)
            if phase == 'ecosystem-runtime-prepare':
                assert command[1:3]==('sync','--locked')
                calls.append('runtime-sync')
                return
            assert command[command.index('--project') + 1] in {str(first), str(frozen)}
            assert '--no-sync' in command
            if phase == 'ecosystem-source-policy':
                assert command[command.index('--project') + 1] == str(first)
                assert '--profiles' in command
                assert '--read-only' not in command and '--checked-sources' not in command
                target = Path(command[command.index('--source-policy-only') + 1])
                assert target == receipt / 'selected-source-declarations.json'
                target.write_text(json.dumps(selection))
                calls.append('collect')
                return
            assert phase in {'ecosystem-prepare', 'ecosystem-compatibility'}
            assert '--source-policy-only' not in command
            assert ('--profiles' in command) == (phase == 'ecosystem-prepare')
            assert ('--checked-sources' in command) == (phase == 'ecosystem-prepare')
            assert ('--read-only' in command) == (phase == 'ecosystem-compatibility')
            if phase == 'ecosystem-prepare':
                assert command[command.index('--checked-sources') + 1] == str(receipt / 'runtime-sources.json')
            calls.append(('probe', '--profiles' in command))
    monkeypatch.setattr(runtime_environment, 'stage', stage)
    monkeypatch.setattr(runtime_qualification, 'freeze', freeze)
    monkeypatch.setattr(runtime_qualification, 'prepare_overrides', overrides)
    monkeypatch.setattr(runtime_qualification, 'verify_recorded', verify)
    manager = SimpleNamespace(home=tmp_path, inventory=[], diagnostics=Diagnostics(),publish=AsyncMock(),
        service=SimpleNamespace(get_state=lambda: {'sessions': [], 'settings': {'workspace': str(tmp_path), 'bundle': 'work'}}))
    await UpdateManager.validate(manager, receipt, release)
    assert calls == ['stage', 'runtime-sync', ('overrides', first), 'collect', 'source-alignment', ('overrides', first), ('probe', True), 'freeze', 'verify', ('overrides', frozen), ('probe', False), ('probe', False), ('probe', False), ('probe', False), 'verify']


async def test_historical_receipt_never_gets_refresh_or_new_foundation_arguments(tmp_path, monkeypatch):
    release = '3' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, release)
    receipt.mkdir(parents=True)
    (receipt / 'runtime.lock').write_bytes(b'recorded')
    async def stage(manager, generation, candidates, *, finalize):
        assert finalize
        return tmp_path / 'historical'
    class Diagnostics:
        state = {'attemptId': release}
        async def run(self, phase, function, *command, **kwargs):
            assert '--refresh-dependencies' not in command
            assert '--install-overrides' not in command
    async def unexpected(*args):
        raise AssertionError('A historical generation was refreshed')
    monkeypatch.setattr(runtime_environment, 'stage', stage)
    monkeypatch.setattr(runtime_qualification, 'freeze', unexpected)
    manager = SimpleNamespace(home=tmp_path, inventory=[], diagnostics=Diagnostics(),publish=AsyncMock(),
        service=SimpleNamespace(get_state=lambda: {'sessions': [], 'settings': {'workspace': str(tmp_path), 'bundle': 'work'}}))
    await UpdateManager.validate(manager, receipt, release)
    assert (receipt / 'runtime.lock').read_bytes() == b'recorded'


async def test_resume_cannot_enable_refresh(tmp_path):
    from amplifier_web.host.session import prepare_manager
    with pytest.raises(ValueError, match='new isolated qualification'):
        await prepare_manager(tmp_path, resume=True, refresh_dependencies=True)


def test_recorded_graph_rejects_later_distribution_changes(tmp_path, monkeypatch):
    row = {'name': 'amplifier-fixture', 'version': '1.0', 'directUrl': {'url': 'https://example.invalid/fixture',
        'vcs_info': {'vcs': 'git', 'requested_revision': 'main', 'commit_id': 'a' * 40}}}
    (tmp_path / 'runtime-installed.json').write_text(json.dumps([row]))
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [row])
    runtime_qualification.verify_recorded(tmp_path / 'project', tmp_path)
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [{**row, 'version': '2.0'}])
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path / 'project', tmp_path)


from test_host_session_resume import mounted_host


async def test_host_forwards_fresh_policy_but_ordinary_preparation_keeps_defaults(mounted_host, tmp_path):
    from amplifier_web.host.session import prepare_manager
    h = mounted_host
    overrides = tmp_path / 'qualified-overrides.txt'
    overrides.write_text('amplifier-core==1.6.1\n')
    await prepare_manager(h.config.workspace, runtime=h.runtime, resume=False,
                          refresh_dependencies=True, install_overrides=overrides)
    policy = h.loaded.prepare.call_args.kwargs
    assert policy['refresh_dependencies'] is True and policy['install_overrides'] == overrides
    h.session.execute.assert_not_called()
    h.runtime.session_id = 'ordinary-new-session'
    await prepare_manager(h.config.workspace, runtime=h.runtime, resume=False)
    policy = h.loaded.prepare.call_args.kwargs
    assert 'refresh_dependencies' not in policy and 'install_overrides' not in policy
    h.session.execute.assert_not_called()


def test_active_generation_uses_qualified_policy_without_overriding_user_choice(tmp_path, monkeypatch):
    release = '6' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, release)
    receipt.mkdir(parents=True)
    (tmp_path / 'updates/active.json').write_text(json.dumps({'current': release}))
    (receipt / 'runtime-installed.json').write_text('[]')
    target = receipt / 'runtime-install-overrides.txt'
    target.write_text('amplifier-core==1.6.1\n')
    (receipt / 'runtime.lock').write_text('version=1\n[[package]]\nname="amplifier-core"\nversion="1.6.1"\nsource={registry="https://pypi.org/simple"}\n')
    runtime_environment.prepare_project(tmp_path, release)
    calls = []
    monkeypatch.setattr(runtime_qualification, 'verify_recorded', lambda project, directory, **policy: calls.append((project, directory)))
    assert runtime_qualification.active_install_overrides(tmp_path) == target
    assert runtime_qualification.active_install_overrides(tmp_path, str(target)) == target
    assert len(calls) == 2
    assert runtime_qualification.active_install_overrides(tmp_path, '/user/explicit-overrides.txt') is None
    assert len(calls) == 2
    target.write_text('amplifier-core==0.0.1\n')
    with pytest.raises(ValueError, match='policy changed'):
        runtime_qualification.active_install_overrides(tmp_path)
    target.unlink()
    with pytest.raises(ValueError, match='policy is missing'):
        runtime_qualification.active_install_overrides(tmp_path)


def test_legacy_worker_generation_has_no_new_install_policy(tmp_path):
    assert runtime_qualification.active_install_overrides(tmp_path) is None
    assert not (tmp_path / 'updates').exists()


def test_active_additions_cannot_replace_any_qualified_dependency(tmp_path, monkeypatch):
    original = {'name': 'amplifier-original', 'version': '1.0'}
    added = {'name': 'amplifier-lazy', 'version': '2.0'}
    (tmp_path / 'runtime-installed.json').write_text(json.dumps([original]))
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [original, added])
    runtime_qualification.verify_recorded(tmp_path, tmp_path, allow_additions=True)
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path, tmp_path)
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda project: [{**original, 'version': '1.1'}, added])
    with pytest.raises(ValueError, match='changed after qualification'):
        runtime_qualification.verify_recorded(tmp_path, tmp_path, allow_additions=True)


def test_installer_overrides_preserve_editable_and_local_sources(tmp_path):
    (tmp_path / 'uv.lock').write_text('''version=1
[[package]]
name="amplifier-editable"
version="1.0"
source={editable="../cached-module"}
[[package]]
name="amplifier-local"
version="1.0"
source={directory="../local-module"}
''')
    output = runtime_qualification.lock_overrides(tmp_path, tmp_path / 'overrides.txt').read_text()
    assert '-e ' + (tmp_path.parent / 'cached-module').as_uri() in output
    assert 'amplifier-local @ ' + (tmp_path.parent / 'local-module').as_uri() in output


async def test_refresh_preparation_never_imports_or_mounts_live_runtime(mounted_host, monkeypatch):
    import builtins
    from amplifier_web.host.session import prepare_dependencies
    h = mounted_host
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith('amplifier_module_loop_live') or name in {'children', 'storage'}:
            raise AssertionError('Refresh preparation imported a live session before its sources froze')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr('amplifier_web.host.session.installed_loop_source', lambda: '/qualified/loop-package')
    overrides = h.home / 'qualified-overrides.txt'
    await prepare_dependencies(h.config.workspace, bundle='anchors', install_overrides=overrides)
    h.loaded.prepare.assert_awaited_once()
    policy = h.loaded.prepare.call_args.kwargs
    assert policy['refresh_dependencies'] is True and policy['strict'] is True
    assert policy['install_overrides'] == overrides
    assert policy['cache_dir'] == h.config.registry_home / 'cache'
    assert policy['source_resolver']('loop-live', 'old-source') == '/qualified/loop-package'
    h.prepared.create_session.assert_not_awaited()
    h.session.execute.assert_not_awaited()
    assert not h.path.exists() and not h.writes


@pytest.mark.parametrize('source_kind', ['git-wheel', 'editable', 'local-wheel'])
def test_loop_mount_uses_the_exact_installed_runtime_source(tmp_path, monkeypatch, source_kind):
    import importlib.metadata
    import importlib.util
    from amplifier_web.host.session import installed_loop_source, module_source
    editable = source_kind == 'editable'
    package = tmp_path / ('src/amplifier_module_loop_live' if editable else 'amplifier_module_loop_live')
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('raise AssertionError("Do not import source while locating it")\n')
    direct = {'url': tmp_path.as_uri(), 'dir_info': {'editable': True}} if editable else {'url':'https://example.invalid/loop','vcs_info':{'vcs':'git','commit_id':'a'*40}}
    if source_kind == 'local-wheel':
        direct = {'url': (tmp_path / 'local-build-source').as_uri(), 'dir_info': {}}
    distribution = SimpleNamespace(read_text=lambda _: json.dumps(direct), locate_file=lambda path: tmp_path / path)
    monkeypatch.setattr(importlib.metadata, 'distribution', lambda name: distribution)
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: SimpleNamespace(origin=str(package / '__init__.py')))
    assert installed_loop_source() == str(package)
    assert module_source(SimpleNamespace(module_sources={}), False, 'loop-live', 'git+https://example.invalid/second-copy') == str(package)
    # Matching version/revision is not enough to excuse a second import tree.
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: SimpleNamespace(origin=str(tmp_path / 'other-copy/__init__.py')))
    with pytest.raises(ValueError, match='does not match its installed distribution'):
        installed_loop_source()


@pytest.mark.parametrize('fail', [False, True])
@pytest.mark.parametrize('caller_policy', [None, '/caller/policy.txt'])
@pytest.mark.parametrize('partial', [False, True])
async def test_one_batch_and_readonly_offered_profiles_exclude_history(tmp_path, monkeypatch, fail, caller_policy, partial):
    import asyncio
    import os
    if caller_policy is None:
        monkeypatch.delenv('UV_OVERRIDE', raising=False)
    else:
        monkeypatch.setenv('UV_OVERRIDE', caller_policy)
    monkeypatch.setenv('UV_CONSTRAINT', '/caller/constraints.txt')
    caller_environment = dict(os.environ)
    counts = {'prepare': 0, 'check': 0}
    peaks = dict(counts)
    completed = []
    checked_profiles = []
    project = tmp_path / 'project'
    release = '9' * 32
    receipt = runtime_environment.receipt_directory(tmp_path, release)
    receipt.mkdir(parents=True)
    selection = {'packages': {}, 'batchPaths': {}, 'protectedPaths': []}
    if partial:
        (receipt/'shared-config').mkdir()
        (receipt/'shared-config/settings.yaml').write_text('bundle:\n  added:\n    library: any-location\n')
    async def stage(*args, **kwargs): return project
    async def freeze(*args):
        assert completed[-1] == 'prepare'
        completed.append('freeze')
        return project
    async def overrides(project, target, *, owned_root):
        assert owned_root == tmp_path
        return target
    class Diagnostics:
        state = {'attemptId': release}
        async def run(self, phase, function, *command, **kwargs):
            if phase == 'ecosystem-runtime-policy':
                return await function(*command, **kwargs)
            if phase == 'ecosystem-source-preflight':
                assert completed[-1] == 'collect'
                assert kwargs == {'selection': selection}
                result = await function(*command, **kwargs)
                completed.append('align')
                return result
            if phase == 'ecosystem-runtime-prepare':
                assert command[1:3]==('sync','--locked')
                completed.append('sync')
                return
            key = {'ecosystem-source-policy': 'collect', 'ecosystem-prepare': 'prepare',
                   'ecosystem-compatibility': 'check'}[phase]
            assert kwargs['env'].get('UV_OVERRIDE') == caller_policy
            assert kwargs['env']['UV_CONSTRAINT'] == '/caller/constraints.txt'
            assert '--install-overrides' in command
            assert ('--read-only' in command) == (key == 'check')
            assert '--no-sync' in command  # All probes reuse the one-time sync.
            assert ('--profiles' in command) == (key in {'collect', 'prepare'})
            assert ('--source-policy-only' in command) == (key == 'collect')
            assert ('--checked-sources' in command) == (key == 'prepare')
            expected = ['anchors', 'anchors-amp-dev', 'work', 'work-amp-dev']
            if key in {'collect', 'prepare'}:
                candidates = sorted([*expected, *(['library'] if partial else [])])
                assert json.loads(Path(command[command.index('--profiles') + 1]).read_text()) == candidates
                if key == 'collect':
                    target = Path(command[command.index('--source-policy-only') + 1])
                    assert target == receipt / 'selected-source-declarations.json'
                    target.write_text(json.dumps(selection))
                    completed.append('collect')
                    return
                assert completed[-1] == 'align'
                assert command[command.index('--checked-sources') + 1] == str(receipt / 'runtime-sources.json')
                if partial:
                    from amplifier_web.host.config import read_config
                    from amplifier_web.profile_catalog import save_catalog
                    config = read_config(receipt/'qualification-workspace', home=receipt, shared_home=receipt/'shared-config', global_only=True)
                    save_catalog(config, candidates, {name: {'complete': name!='library'} for name in candidates})
            else:
                assert 'freeze' in completed
                profile = command[command.index('--global-only') - 3]
                checked_profiles.append(profile)
            counts[key] += 1
            peaks[key] = max(peaks[key], counts[key])
            try:
                if fail and key == 'check': raise ValueError('incompatible')
                await asyncio.sleep(.01)
                completed.append(key)
            finally: counts[key] -= 1
    monkeypatch.setattr(runtime_environment, 'stage', stage)
    monkeypatch.setattr(runtime_qualification, 'freeze', freeze)
    monkeypatch.setattr(runtime_qualification, 'prepare_overrides', overrides)
    monkeypatch.setattr(runtime_qualification, 'verify_recorded', lambda *args: None)
    settings = {'workspace': str(tmp_path), 'bundle': 'work'}
    state = {'sessions': [dict(settings, bundle=f'bundle-{i}') for i in range(7)] + [dict(settings, bundle='historical', historyManaged=True)], 'settings': settings}
    published=[]
    async def publish(**values):published.append(values)
    manager = SimpleNamespace(home=tmp_path, inventory=[], diagnostics=Diagnostics(),publish=publish,service=SimpleNamespace(get_state=lambda: state))
    if fail:
        with pytest.raises(ValueError, match='incompatible'): await UpdateManager.validate(manager, receipt, release)
    else:
        await UpdateManager.validate(manager, receipt, release)
        assert completed.count('check') == 4 and peaks['check'] == 4
        assert sorted(checked_profiles) == ['anchors', 'anchors-amp-dev', 'work', 'work-amp-dev']
        assert json.loads((receipt/'profiles-qualified.json').read_text())['profiles'] == sorted(checked_profiles)
    assert completed.count('prepare') == 1 and peaks['prepare'] == 1
    assert completed.count('sync')==1
    assert completed[:5] == ['sync', 'collect', 'align', 'prepare', 'freeze']
    assert completed.count('collect') == 1
    assert counts == {'prepare': 0, 'check': 0}  # No orphan probes after a failure.
    preparation=[row['probeProgress'] for row in published if row.get('probeProgress',{} ) is not None and row.get('probeProgress',{}).get('phase')=='prepare']
    checks=[row['probeProgress'] for row in published if row.get('probeProgress',{}) is not None and row.get('probeProgress',{}).get('phase')=='compatibility']
    assert not preparation  # One dependency batch, no historical preparation loop.
    assert all(row['total']==4 for row in checks)
    assert checks[-1]['completed']==(0 if fail else 4)
    assert str(tmp_path) not in json.dumps(preparation+checks)
    assert dict(os.environ) == caller_environment


def test_package_payload_verification_keeps_content_and_symlink_boundaries(tmp_path):
    from amplifier_web.runtime_qualification import package_content
    root=tmp_path/'.venv/lib/python3.13/site-packages';root.mkdir(parents=True)
    metadata=root/'fixture-1.dist-info';metadata.mkdir()
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: fixture\nVersion: 1\n')
    payload=root/'fixture';payload.mkdir()
    (payload/'code.py').write_text('version=1')
    outside=tmp_path/'outside';outside.mkdir()
    (outside/'ignored.py').write_text('external=1')
    (payload/'external').symlink_to(outside,target_is_directory=True)
    (metadata/'RECORD').write_text('fixture/code.py,,\nfixture/external/ignored.py,,\n')
    first=package_content(tmp_path)
    (outside/'ignored.py').write_text('external=2')
    assert package_content(tmp_path)==first
    (payload/'code.py').write_text('version=2')
    assert package_content(tmp_path)!=first
