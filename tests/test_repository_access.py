"""Public downloads must not depend on a usable signed-in GitHub account."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
from unittest.mock import AsyncMock

import httpx
import pytest

from amplifier_web import app_updates, reset, updates, bundles, smart_tools


def test_reset_reads_public_release_before_restricted_cli(monkeypatch):
    monkeypatch.setattr(reset.shutil, 'which', lambda _: '/fixture/gh')
    monkeypatch.setattr(reset, 'command', lambda *a, **kw: pytest.fail('Must not invoke signed-in gh'))
    requests = []
    def public(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"tag_name":"v1.2.3"}')
    monkeypatch.setattr(reset.urllib.request, 'urlopen', public)
    assert reset.release_metadata({})['tag_name'] == 'v1.2.3'
    assert not requests[0].has_header('Authorization')


@pytest.mark.parametrize('anonymous', [False, True])
def test_reset_auth_fallback_is_optional(monkeypatch, anonymous):
    monkeypatch.setattr(reset.shutil, 'which', lambda _: '/fixture/gh')
    def unavailable(*a, **kw):
        raise urllib.error.HTTPError('https://api.github.com', 403, 'rate limit', {}, None)
    monkeypatch.setattr(reset.urllib.request, 'urlopen', unavailable)
    calls = []
    def command(*a, **kw):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, '{"tag_name":"v1.2.3"}')
    monkeypatch.setattr(reset, 'command', command)
    if anonymous:
        with pytest.raises(RuntimeError, match='public release'):
            reset.release_metadata({'AMPLIFIER_GITHUB_ACCESS': 'anonymous'})
        assert not calls
    else:
        assert reset.release_metadata({})['tag_name'] == 'v1.2.3'
        assert calls[0][:2] == ('gh', 'api')


@pytest.mark.parametrize('status,anonymous', [(200, False), (403, False), (404, True)])
async def test_update_metadata_public_first_and_explicit_no_auth(monkeypatch, status, anonymous):
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous' if anonymous else 'auto')
    monkeypatch.setattr(app_updates.shutil, 'which', lambda _: '/fixture/gh')
    authenticated = AsyncMock(return_value='{"tag_name":"v1.2.3"}')
    monkeypatch.setattr(app_updates, 'process', authenticated)
    requests = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers):
            requests.append(headers)
            return httpx.Response(status, json={'tag_name': 'v1.2.3'}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    if anonymous:
        with pytest.raises(RuntimeError, match='release channel'):
            await app_updates.github_read('repos/example/public/releases/latest')
    else:
        assert json.loads(await app_updates.github_read('repos/example/public/releases/latest'))['tag_name'] == 'v1.2.3'
    assert 'Authorization' not in requests[0]
    assert authenticated.await_count == (1 if status == 403 else 0)


def test_anonymous_git_ignores_personal_headers_helpers_and_rewrites(tmp_path):
    config = tmp_path / 'gitconfig'
    config.write_text('[url "ssh://git@invalid.example/"]\n insteadOf = https://github.com/\n'
                      '[http]\n extraHeader = Authorization: synthetic-credential\n'
                      '[credential]\n helper = !exit 77\n')
    original = {**os.environ, 'GIT_CONFIG_GLOBAL': str(config), 'GH_TOKEN': 'synthetic-token',
                'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.extraHeader',
                'GIT_CONFIG_VALUE_0': 'Authorization: inherited-fixture',
                'HTTPS_PROXY': 'http://proxy.example:8080'}
    env = reset.repository_environment(original, anonymous=True)
    assert env['HTTPS_PROXY'] == original['HTTPS_PROXY']
    assert 'GH_TOKEN' not in env
    output = subprocess.run(['git', 'config', '--list'], env=env, cwd=tmp_path,
                            check=True, capture_output=True, text=True).stdout
    assert 'synthetic-credential' not in output and 'inherited-fixture' not in output
    assert 'insteadof' not in output and 'helper=!exit' not in output
    assert 'credential.helper=\n' in output and 'http.extraheader=\n' in output
    assert config.read_text().count('synthetic-credential') == 1
    assert original['GH_TOKEN'] == 'synthetic-token'


async def test_update_subprocess_carries_anonymous_policy_to_nested_installers(monkeypatch):
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous')
    monkeypatch.setenv('GH_TOKEN', 'synthetic-token')
    output = await updates.process(sys.executable, '-c',
        'import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith(("GIT_CONFIG", "GH_TOKEN", "AMPLIFIER_GITHUB_ACCESS"))}))')
    env = json.loads(output)
    assert env['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'
    assert env['GIT_CONFIG_GLOBAL'] == os.devnull
    assert 'GH_TOKEN' not in env
    assert smart_tools._install_environment()['GIT_CONFIG_GLOBAL'] == os.devnull
    assert smart_tools._install_environment()['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'


@pytest.mark.parametrize('platform', ['win32', 'linux', 'darwin'])
@pytest.mark.parametrize('anonymous', [False, True])
def test_git_long_paths_are_windows_child_policy_without_restoring_auth(monkeypatch, platform, anonymous):
    monkeypatch.setattr(reset.sys, 'platform', platform)
    monkeypatch.setattr(reset.shutil, 'which', lambda _: None)
    original = {'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'core.longpaths',
                'GIT_CONFIG_VALUE_0': 'false', 'GIT_CONFIG_KEY_1': 'http.extraHeader',
                'GIT_CONFIG_VALUE_1': 'Authorization: synthetic-only',
                'GH_TOKEN': 'synthetic-token', 'HTTPS_PROXY': 'http://proxy.example:8080'}
    before = dict(original)
    env = reset.repository_environment(original, anonymous=anonymous)
    settings = {env[f'GIT_CONFIG_KEY_{i}']: env[f'GIT_CONFIG_VALUE_{i}']
                for i in range(int(env['GIT_CONFIG_COUNT']))}
    assert settings.get('core.longpaths') == ('true' if platform == 'win32' else None if anonymous else 'false')
    if anonymous:
        assert settings['http.extraHeader'] == ''
        assert settings['credential.helper'] == ''
        assert settings['core.askPass'] == ''
        assert 'GH_TOKEN' not in env
        assert env['GIT_CONFIG_GLOBAL'] == env['GIT_CONFIG_SYSTEM'] == os.devnull
    else:
        assert settings['http.extraHeader'] == original['GIT_CONFIG_VALUE_1']
        assert env['GH_TOKEN'] == original['GH_TOKEN']
    assert env['HTTPS_PROXY'] == original['HTTPS_PROXY'] and original == before


async def test_bundle_fetch_inherits_anonymous_policy(monkeypatch):
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous')
    child = AsyncMock()
    child.communicate.return_value = (b'index', b'')
    child.returncode = 0
    create = AsyncMock(return_value=child)
    monkeypatch.setattr(bundles.asyncio, 'create_subprocess_exec', create)
    assert await bundles.git('ls-remote', 'https://github.com/example/public') == b'index'
    env = create.call_args.kwargs['env']
    assert env['GIT_CONFIG_GLOBAL'] == os.devnull
    assert env['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'


def test_automatic_retry_is_read_only_and_scoped():
    original = {'GH_TOKEN': 'private-fixture', 'HTTPS_PROXY': 'http://proxy.example:8080'}
    denied = b'fatal: https://github.com/example/repo: SAML enforcement HTTP 403'
    env = reset.anonymous_retry_environment(['git', 'fetch', 'https://github.com/example/repo'], original, stderr=denied)
    assert env['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous' and 'GH_TOKEN' not in env
    assert env['HTTPS_PROXY'] == original['HTTPS_PROXY']
    assert original['GH_TOKEN'] == 'private-fixture'
    for command in (['git', 'push', 'https://github.com/example/repo'], ['gh', 'api', '--method', 'POST', 'repos/example/repo/issues'], ['git', 'status']):
        assert reset.anonymous_retry_environment(command, original, stderr=denied) is None
    assert reset.anonymous_retry_environment(['git', 'fetch', 'https://github.com/example/repo'], env, stderr=denied) is None
    assert reset.anonymous_retry_environment(['git', 'fetch', 'https://github.com/example/repo'], original, stderr=b'certificate verification failed') is None


def test_reset_tag_lookup_retries_without_account(monkeypatch):
    monkeypatch.setattr(reset, 'release_metadata', lambda _: {'tag_name': 'v1.2.3'})
    environments = []
    def command(*args, **kwargs):
        environments.append(dict(kwargs['env']))
        if len(environments) == 1:
            raise subprocess.CalledProcessError(128, args, stderr='SAML enforcement for https://github.com/example/repo')
        return subprocess.CompletedProcess(args, 0, 'a'*40+'\trefs/tags/v1.2.3\n')
    monkeypatch.setattr(reset, 'command', command)
    env = {'GH_TOKEN': 'fixture'}
    assert reset.release_source(env).endswith('@'+'a'*40)
    assert len(environments) == 2 and environments[1]['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'
    assert 'GH_TOKEN' not in environments[1]


@pytest.mark.parametrize('argv', [
    ('git', 'ls-remote', 'https://github.com/example/public', 'HEAD'),
    ('uv', 'tool', 'install', 'git+https://github.com/example/public@'+'a'*40),
    ('uv', 'sync', '--locked', '--project', '/fixture/stage'),
])
async def test_update_download_retries_once_with_private_access_preserved(monkeypatch, argv):
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS', raising=False)
    monkeypatch.setenv('GH_TOKEN', 'fixture')
    failed, passed = AsyncMock(), AsyncMock()
    failed.returncode=128; failed.communicate.return_value=(b'', b'https://github.com/example/public SAML enforcement')
    passed.returncode=0; passed.communicate.return_value=(b'result', b'')
    create=AsyncMock(side_effect=[failed, passed]);monkeypatch.setattr(updates.asyncio,'create_subprocess_exec',create)
    result=await updates.process(*argv)
    assert result=='result' and result.diagnostic_facts['anonymousRetry'] is True
    assert create.call_args_list[0].kwargs['env'] is None
    assert 'GH_TOKEN' not in create.call_args_list[1].kwargs['env']
    assert os.environ['GH_TOKEN']=='fixture'


async def test_private_or_broken_source_stops_after_one_anonymous_retry(monkeypatch):
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS', raising=False)
    child=AsyncMock();child.returncode=128;child.communicate.return_value=(b'',b'https://github.com/example/private Authentication failed')
    create=AsyncMock(return_value=child);monkeypatch.setattr(updates.asyncio,'create_subprocess_exec',create)
    with pytest.raises(RuntimeError): await updates.process('git','fetch','https://github.com/example/private')
    assert create.await_count==2


def test_nested_preparation_error_is_classified_without_disclosing_text():
    from amplifier_web.update_diagnostics import probe_failure, probe_record, PROBE_PREFIX
    try:
        try: raise RuntimeError('https://github.com/example/public SAML synthetic-private-value')
        except RuntimeError as cause: raise ValueError('Bundle preparation failed') from cause
    except ValueError as error: facts=probe_failure(error,'prepare')
    assert facts['repositoryAccessFailure'] is True
    encoded=PROBE_PREFIX+json.dumps(facts)
    assert 'synthetic-private-value' not in encoded and 'example/public' not in encoded
    assert probe_record(encoded)['repositoryAccessFailure'] is True
    env=reset.anonymous_retry_environment(['uv','run','python','/fixture/update_probe.py'],{},encoded)
    assert env['AMPLIFIER_GITHUB_ACCESS']=='anonymous'


async def test_bundle_auto_retry_has_bounded_child_environment(monkeypatch):
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS', raising=False)
    failed, passed=AsyncMock(),AsyncMock()
    failed.returncode=128;failed.communicate.return_value=(b'',b'Authentication failed for https://github.com/example/public')
    passed.returncode=0;passed.communicate.return_value=(b'index',b'')
    create=AsyncMock(side_effect=[failed,passed]);monkeypatch.setattr(bundles.asyncio,'create_subprocess_exec',create)
    assert await bundles.git('fetch','https://github.com/example/public')==b'index'
    assert create.call_args_list[1].kwargs['env']['AMPLIFIER_GITHUB_ACCESS']=='anonymous'


async def test_smart_tool_private_auth_success_and_public_retry(monkeypatch):
    from types import SimpleNamespace
    manager = SimpleNamespace(_redact=lambda value: value)
    manager._run = lambda argv, **kwargs: smart_tools.SmartToolsManager._run(manager, argv, **kwargs)
    failed, passed = AsyncMock(), AsyncMock()
    failed.returncode=128;failed.communicate.return_value=(b'',b'https://github.com/example/public: Authentication failed')
    passed.returncode=0;passed.communicate.return_value=(b'commit',b'')
    create=AsyncMock(side_effect=[passed,failed,passed]);monkeypatch.setattr(smart_tools.asyncio,'create_subprocess_exec',create)
    env={'GH_TOKEN':'fixture'}
    assert await manager._run(['git','fetch','https://github.com/example/private'],env=env)=='commit'
    assert create.await_count==1
    assert await manager._run(['git','fetch','https://github.com/example/public'],env=env)=='commit'
    assert create.await_count==3
    assert create.call_args.kwargs['env']['AMPLIFIER_GITHUB_ACCESS']=='anonymous'
    assert env=={'GH_TOKEN':'fixture'}


async def test_terminal_public_download_needs_no_github_cli(monkeypatch):
    from amplifier_web import terminal_release
    original=httpx.AsyncClient
    seen=[]
    def handle(request):
        seen.append(request)
        return httpx.Response(200,content=b'fixture-artifact')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    monkeypatch.setattr(terminal_release.shutil,'which',lambda _:None)
    assert await terminal_release.github('repos/example/public/releases/assets/1',maximum=100,binary=True)==b'fixture-artifact'
    assert 'authorization' not in seen[0].headers
    with pytest.raises(ValueError,match='size'):
        await terminal_release.github('repos/example/public/releases/assets/1',maximum=2,binary=True)


async def test_terminal_private_read_can_use_auth_but_anonymous_mode_never_does(monkeypatch):
    from amplifier_web import terminal_release
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(lambda r:httpx.Response(404)),**kwargs))
    child=AsyncMock();child.returncode=0;child.stdout.read.side_effect=[b'private-artifact',b''];child.wait.return_value=0
    create=AsyncMock(return_value=child);monkeypatch.setattr(terminal_release.asyncio,'create_subprocess_exec',create)
    monkeypatch.setattr(terminal_release.shutil,'which',lambda _: '/fixture/gh')
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS',raising=False)
    assert await terminal_release.github('repos/example/private/releases/assets/1',maximum=100)==b'private-artifact'
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS','anonymous')
    with pytest.raises(ValueError,match='public terminal'):
        await terminal_release.github('repos/example/private/releases/assets/1',maximum=100)
    assert create.await_count==1


async def test_feedback_public_reads_share_policy_and_writes_still_require_auth(monkeypatch):
    from amplifier_web import feedback
    read=AsyncMock(return_value='{"id":12}')
    monkeypatch.setattr(app_updates,'github_read',read)
    monkeypatch.setattr(feedback.shutil,'which',lambda _:None)
    assert await feedback.github_api('repos/example/public/issues/1',None)=={'id':12}
    with pytest.raises(FileNotFoundError):
        await feedback.github_api('repos/example/public/issues',{'title':'fixture'})
    assert read.await_count==1


def test_workspace_download_retries_in_pinned_directory(tmp_path, monkeypatch):
    from amplifier_web import workspace_provisioning as provisioning
    monkeypatch.delenv('AMPLIFIER_GITHUB_ACCESS', raising=False)
    monkeypatch.setenv('GH_TOKEN', 'fixture-token')
    original = subprocess.Popen
    attempts = []
    def spawn(argv, **kwargs):
        attempts.append((argv, dict(kwargs['env']), kwargs['cwd']))
        script = ('import sys; print("https://github.com/example/public: SAML enforcement", file=sys.stderr); sys.exit(128)'
                  if len(attempts) == 1 else 'print("verified-commit")')
        return original([sys.executable, '-c', script], **kwargs)
    monkeypatch.setattr(provisioning.subprocess, 'Popen', spawn)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert provisioning._raw_git(fd, 'fetch', 'https://github.com/example/public', 'main') == 'verified-commit'
    finally:
        os.close(fd)
    assert len(attempts) == 2 and attempts[0][0] == attempts[1][0]
    assert attempts[0][2] == attempts[1][2]
    assert attempts[0][1]['GH_TOKEN'] == 'fixture-token'
    assert 'GH_TOKEN' not in attempts[1][1]
    assert attempts[1][1]['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'
    assert os.environ['GH_TOKEN'] == 'fixture-token'


def test_repair_install_retries_same_source_without_changing_service_policy(monkeypatch):
    environments = []
    source = 'git+https://github.com/example/public@' + 'b'*40
    def run(*argv, **kwargs):
        environments.append((argv, dict(kwargs['env'])))
        if len(environments) == 1:
            raise subprocess.CalledProcessError(1, argv, stderr='Authentication failed for https://github.com/example/public')
        return subprocess.CompletedProcess(argv, 0, '')
    monkeypatch.setattr(reset, 'command', run)
    env = {'GH_TOKEN': 'fixture'}
    reset.download_command('uv', 'tool', 'install', source, env=env)
    assert len(environments) == 2 and environments[0][0] == environments[1][0]
    assert environments[1][1]['AMPLIFIER_GITHUB_ACCESS'] == 'anonymous'
    assert 'GH_TOKEN' not in environments[1][1]
    assert env == {'GH_TOKEN': 'fixture'}
