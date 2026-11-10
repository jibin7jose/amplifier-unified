"""Emergency repair must work when the ordinary application cannot import."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from amplifier_web import reset


@pytest.fixture
def repair_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(reset, "service_commands", lambda home: None)
    monkeypatch.setattr(reset, "assert_no_processes", lambda *args: None)
    monkeypatch.setattr(reset, "post_checks", lambda *a, **kw: {"doctor": {"ok": True}, "service status": {"ok": True}})
    home = tmp_path / "data"
    tool = tmp_path / "tools"
    bins = tmp_path / "bin"
    for root in (home, tool, bins):
        root.mkdir()
    prefix = tool / "amplifier-unified"
    prefix.mkdir()
    (prefix / "old").write_text("locally edited install")
    (bins / "amplifier-unified").symlink_to(prefix / "bin/amplifier-unified")
    for name in reset.REGENERABLE:
        (home / name).mkdir()
        (home / name / "broken").write_text("bad cache or pointer")
    for name in ("config", "attachments", "unknown-plugin", "sessions"):
        (home / name).mkdir()
        (home / name / "keep").write_bytes(b"private user data\x00")
    with sqlite3.connect(home / "app.sqlite3") as db:
        db.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO state VALUES(1, ?)", (json.dumps({"updates": {"pendingReplacement": True}, "sessions": [{"id": "chat", "text": "preserve"}]}),))
        db.execute("CREATE TABLE state_records(kind TEXT, id TEXT, value TEXT)")
        db.execute("INSERT INTO state_records VALUES('global','updates',?)", (json.dumps({"present": True, "value": {"pendingRestart": True}}),))
        db.execute("INSERT INTO state_records VALUES('session','chat','original bytes')")
    return home, tool, bins


def fake_install(monkeypatch, *, fail=False, probe_fail=False):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == "uv":
            prefix = Path(os.environ["TEST_RESET_TOOLS"]) / "amplifier-unified"
            prefix.mkdir()
            (prefix / "fresh").write_text("new install")
            if fail:
                raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(returncode=0)
    def probe(*args):
        if probe_fail:
            raise ValueError("candidate import failed")
    monkeypatch.setattr(reset, "command", run)
    monkeypatch.setattr(reset, "verify", probe)
    return calls


def test_success_retires_only_owned_caches_and_update_fences(repair_tree, monkeypatch):
    home, tool, bins = repair_tree
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    backup = reset.repair(home, "uv", tool, bins, "trusted.whl", {}, None, True)
    assert (tool / "amplifier-unified/fresh").is_file()
    assert not (tool / "amplifier-unified/old").exists()
    # uv treats every directory here as a tool environment. Recovery copies
    # and reset's lock must not become bogus installed tools.
    assert sorted(path.name for path in tool.iterdir()) == ["amplifier-unified"]
    saved = Path(json.loads((backup / "manifest.json").read_text())["toolBackup"])
    assert not saved.is_relative_to(tool)
    assert (saved / "old").read_text() == "locally edited install"
    for name in reset.REGENERABLE:
        assert not (home / name).exists()
        assert (backup / name / "broken").is_file()
    for name in ("config", "attachments", "unknown-plugin", "sessions"):
        assert (home / name / "keep").read_bytes() == b"private user data\x00"
    with sqlite3.connect(home / "app.sqlite3") as db:
        assert json.loads(db.execute("SELECT value FROM state").fetchone()[0]) == {"sessions": [{"id": "chat", "text": "preserve"}]}
        assert db.execute("SELECT * FROM state_records").fetchall() == [('session', 'chat', 'original bytes')]
    with sqlite3.connect(backup / "app.sqlite3") as db:
        assert "updates" in json.loads(db.execute("SELECT value FROM state").fetchone()[0])


@pytest.mark.parametrize("failure", ["install", "probe", "database"])
def test_failure_restores_install_caches_launcher_and_data(repair_tree, monkeypatch, failure):
    home, tool, bins = repair_tree
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    calls = fake_install(monkeypatch, fail=failure == "install", probe_fail=failure == "probe")
    if failure == "database":
        def bad_db(*args):
            raise sqlite3.DatabaseError("corrupt database")
        monkeypatch.setattr(reset, "reset_update_state", bad_db)
    with pytest.raises((subprocess.CalledProcessError, ValueError, sqlite3.DatabaseError)):
        reset.repair(home, "uv", tool, bins, "trusted.whl", {}, (["stop"], ["start"], None), False)
    assert (tool / "amplifier-unified/old").read_text() == "locally edited install"
    assert not (tool / "amplifier-unified/fresh").exists()
    assert sorted(path.name for path in tool.iterdir()) == ["amplifier-unified"]
    assert (bins / "amplifier-unified").is_symlink()
    for name in reset.REGENERABLE:
        assert (home / name / "broken").exists()
    assert calls[0] == ("stop",)
    assert calls[-1] == ("start",)
    with sqlite3.connect(home / "app.sqlite3") as db:
        assert "updates" in json.loads(db.execute("SELECT value FROM state").fetchone()[0])


def test_cache_symlinks_retire_links_without_touching_targets(repair_tree, monkeypatch, tmp_path):
    home, tool, bins = repair_tree
    (home / "foundation/broken").unlink()
    (home / "foundation").rmdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep").write_text("untouched")
    (home / "foundation").symlink_to(external)
    monkeypatch.setenv("TEST_RESET_TOOLS", str(tool))
    fake_install(monkeypatch)
    backup = reset.repair(home, "uv", tool, bins, "trusted.whl", {}, None, True)
    assert (backup / "foundation").is_symlink()
    assert (external / "keep").read_text() == "untouched"


def test_dependency_free_cli_ignores_corrupt_generation(tmp_path):
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates/application-active.json").write_text("not json")
    code = "import sys; sys.path.insert(0, sys.argv.pop(1)); from amplifier_web.cli import main; main()"
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(Path(__file__).parents[1]),
                             "reset", "--data-dir", str(tmp_path), "--dry-run"],
                            env={**os.environ, "HOME": str(tmp_path / "user")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Your chats, settings, sign-ins, and files will be kept." in result.stdout
    assert not (tmp_path / "reset-backups").exists()


def test_existing_host_lease_blocks_reset(tmp_path):
    with reset.repair_lock(tmp_path, shared=True):
        with pytest.raises(RuntimeError, match="already using"):
            with reset.repair_lock(tmp_path):
                pytest.fail("reset acquired an active host's lease")


def test_noninteractive_requires_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(reset, "service_commands", lambda home: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    args = argparse.Namespace(data_dir=str(tmp_path), source=None, yes=False, dry_run=False, no_start=True)
    with pytest.raises(SystemExit, match="--yes"):
        reset.run(args)
    assert not list(tmp_path.iterdir())


def test_service_exact_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    home = tmp_path / "data"
    unit = tmp_path / ".config/systemd/user/amplifier-unified.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"# Managed by amplifier-unified; do not edit.\nExecStart=/bin/amplifier-unified --data-dir '{home}' serve\n")
    assert reset.service_commands(home)[0] == ["systemctl", "--user", "stop", reset.UNIT]
    with pytest.raises(ValueError, match="service uses"):
        reset.service_commands(tmp_path / "dat")


def test_environment_bypasses_contaminated_uv_and_python_configuration(monkeypatch):
    for key in ("PYTHONPATH", "UV_CONSTRAINT", "UV_OVERRIDE", "UV_INDEX_URL", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        monkeypatch.setenv(key, "bad")
    monkeypatch.setenv("UV_TOOL_DIR", "/owned/tools")
    env = reset.environment()
    assert env["UV_NO_CONFIG"] == env["UV_NO_CACHE"] == "1"
    assert env["UV_TOOL_DIR"] == "/owned/tools"
    assert "PYTHONPATH" not in env and "UV_CONSTRAINT" not in env


def test_running_old_worker_prevents_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(reset, "command", lambda *a, **kw: SimpleNamespace(stdout=f"123 {tmp_path}/runtime/abc/.venv/bin/python worker.py\n"))
    with pytest.raises(RuntimeError, match="still closing"):
        reset.assert_no_processes(tmp_path / "tools/amplifier-unified", tmp_path)


def wheel(folder, name, files, requires=()):
    path = folder / f"{name}-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for key, value in files.items():
            archive.writestr(key, value)
        info = f"{name}-1.0.0.dist-info"
        archive.writestr(info + "/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n" + "".join(f"Requires-Dist: {dep}\n" for dep in requires))
        archive.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nGenerator: reset-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        if name == "amplifier_unified":
            archive.writestr(info + "/entry_points.txt", "[console_scripts]\namplifier-unified = amplifier_web.cli:main\n")
        archive.writestr(info + "/RECORD", "")
    return path


@pytest.mark.skipif(not os.environ.get("AMPLIFIER_RESET_TEST_UV"), reason="explicit DTU-only uv installation test")
def test_real_uv_repairs_modified_packages_and_missing_dependencies(tmp_path):
    """Actual launcher/self replacement with uv, using synthetic local wheels."""
    uv = os.environ["AMPLIFIER_RESET_TEST_UV"]
    package = Path(reset.__file__).parent
    dep = wheel(tmp_path, "reset_fixture_dependency", {"reset_fixture_dependency/__init__.py": "VALUE = 'fresh'\n"})
    app = wheel(tmp_path, "amplifier_unified", {
        "amplifier_web/__init__.py": "__version__ = '1.0.0'\n",
        "amplifier_web/cli.py": (package / "cli.py").read_bytes(),
        "amplifier_web/reset.py": package.joinpath("reset.py").read_bytes(),
        "amplifier_web/reset_retention.py": package.joinpath("reset_retention.py").read_bytes(),
        "amplifier_web/host_dependencies.py": package.joinpath("host_dependencies.py").read_bytes(),
        "amplifier_web/__main__.py": "from .cli import main; main()\n",
        "amplifier_web/application_generations.py": "def delegate(): pass\n",
        "amplifier_web/browser_entry.py": "def is_windows(): return False\n",
        "amplifier_web/deployment.py": "def load_server_config(*a, **kw): return {}\n",
        "amplifier_web/deployment_service.py": "# synthetic service module\n",
        "amplifier_web/host/__init__.py": "",
        "amplifier_web/host/config.py": "from pathlib import Path\ndef app_home(): return Path.home() / '.amplifier-unified'\n",
        "amplifier_web/installation_health.py": "import json\ndef doctor(home): return {'ok': True}\nasync def status(home, wait=0): return {'ok': False, 'service': 'stopped'}\ndef display(result, **kw): print(json.dumps(result))\n",
        "amplifier_web/app_updates.py": "PROBE = \"import reset_fixture_dependency; assert reset_fixture_dependency.VALUE == 'fresh'\"\n",
    }, [f"reset-fixture-dependency @ {dep.as_uri()}"])
    # uv tool deliberately ignores virtualenv interpreters while resolving 3.13.
    # Keep the already-running standalone Python discoverable after HOME changes.
    interpreter_bin = str(Path(sys._base_executable).resolve().parent)
    env = {**os.environ, "HOME": str(tmp_path), "UV_TOOL_DIR": str(tmp_path / "tools"),
           "UV_TOOL_BIN_DIR": str(tmp_path / "bin"), "PATH": os.pathsep.join((str(Path(uv).parent), interpreter_bin, os.environ["PATH"])),
           "AMPLIFIER_WEB_HOME": str(tmp_path / "data")}
    for key in ("AMPLIFIER_SOURCE_STORE", "PYTHONPATH"):
        env.pop(key, None)
    installed = subprocess.run([uv, "tool", "install", "--no-cache", "--python", "3.13", str(app)], env=env, capture_output=True, text=True)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    prefix = tmp_path / "tools/amplifier-unified"
    sites = next(prefix.glob("lib/python*/site-packages"))
    (sites / "amplifier_web/app_updates.py").write_text("raise RuntimeError('locally hacked app')\n")
    # Emergency reset must not import the damaged ordinary startup helper.
    (sites / "amplifier_web/host_dependencies.py").unlink()
    (sites / "reset_fixture_dependency/__init__.py").unlink()
    (sites / "rogue.py").write_text("untracked installation file")
    data = tmp_path / "data"
    (data / "updates").mkdir(parents=True)
    (data / "updates/application-active.json").write_text("broken receipt")
    (data / "keep").write_text("history sentinel")
    result = subprocess.run([str(tmp_path / "bin/amplifier-unified"), "reset", "--yes", "--no-start", "--source", str(app)], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Unified is ready." not in result.stdout
    assert "left stopped" in result.stdout
    assert "Prepared " not in result.stdout and "Installed 1 executable" not in result.stdout
    reports = list((data / "reset-reports").glob('*.log'))
    assert len(reports) == 1
    log = reports[0].read_text()
    assert "doctor --json" in log and "service status --json --wait 0" in log
    assert reports[0].stat().st_mode & 0o777 == 0o600
    assert (data / "keep").read_text() == "history sentinel"
    assert not (sites / "rogue.py").exists()
    assert not (data / "updates").exists()
    listed = subprocess.run([uv, "tool", "list"], env=env, capture_output=True, text=True, check=True)
    assert "amplifier-unified" in listed.stdout
    assert not listed.stderr, listed.stderr
    assert not (tmp_path / "tools/.reset.lock").exists()
    subprocess.run([str(prefix / "bin/python"), "-I", "-c", "import reset_fixture_dependency; assert reset_fixture_dependency.VALUE == 'fresh'"], env=env, check=True)


def test_latest_release_resolves_annotated_tag_to_immutable_commit(monkeypatch):
    commit = "a" * 40
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == "gh":
            return SimpleNamespace(stdout=json.dumps({"tag_name": "v1.2.3", "draft": False, "prerelease": False}))
        return SimpleNamespace(stdout=f"{'b' * 40}\trefs/tags/v1.2.3\n{commit}\trefs/tags/v1.2.3^{{}}\n")
    monkeypatch.setattr(reset, "command", run)
    monkeypatch.setattr(reset, "release_metadata", lambda env: {"tag_name": "v1.2.3"})
    assert reset.release_source({}) == f"git+https://github.com/{reset.REPOSITORY}@{commit}"
    assert calls[0][:2] == ("git", "ls-remote")


def test_launchd_service_uses_exact_owner_and_generated_marker(monkeypatch, tmp_path):
    import plistlib
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    path = tmp_path / "Library/LaunchAgents" / f"{reset.LABEL}.plist"
    path.parent.mkdir(parents=True)
    data = {"Label": reset.LABEL, "AmplifierUnifiedMarker": "amplifier-unified launch agent v1",
            "ProgramArguments": ["/owned/bin/amplifier-unified", "--data-dir", str(tmp_path / "data"), "serve"]}
    path.write_bytes(plistlib.dumps(data))
    stop, start, loaded = reset.service_commands(tmp_path / "data")
    assert stop[1] == "bootout" and start[1] == "bootstrap" and loaded[1] == "print"
    data.pop("AmplifierUnifiedMarker")
    path.write_bytes(plistlib.dumps(data))
    with pytest.raises(ValueError, match="unmanaged"):
        reset.service_commands(tmp_path / "data")


def test_subprocess_timeout_terminates_installer_children(tmp_path):
    import time
    marker = tmp_path / "unexpected-write"
    child = f"import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1); open({str(marker)!r}, 'w').write('late')"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    with pytest.raises(subprocess.TimeoutExpired):
        reset.command(sys.executable, "-c", parent, timeout=.2, capture=True)
    time.sleep(1.2)
    assert not marker.exists()


def test_reset_data_directory_before_and_after_subcommand(monkeypatch):
    from amplifier_web.cli import _parse
    for args in (["--data-dir", "/custom", "reset", "--dry-run"], ["reset", "--data-dir", "/custom", "--dry-run"]):
        monkeypatch.setattr(sys, "argv", ["amplifier-unified", *args])
        assert _parse().data_dir == "/custom"


@pytest.mark.parametrize('failed_check', ['doctor', 'service status'])
def test_failed_post_repair_check_keeps_fresh_install_but_never_claims_ready(repair_tree, monkeypatch, capsys, failed_check):
    home, tool, bins = repair_tree
    monkeypatch.setenv('TEST_RESET_TOOLS', str(tool))
    fake_install(monkeypatch)
    results = {'doctor': {'ok': True}, 'service status': {'ok': True}}
    results[failed_check] = {'ok': False}
    monkeypatch.setattr(reset, 'post_checks', lambda *a, **kw: results)
    with pytest.raises(RuntimeError):
        reset.repair(home, 'uv', tool, bins, 'trusted.whl', {}, (['stop'], ['start'], None), False)
    assert (tool / 'amplifier-unified/fresh').exists()
    assert not (tool / 'amplifier-unified/old').exists()
    assert 'Unified is ready' not in capsys.readouterr().out


def test_no_start_runs_both_checks_without_claiming_ready(repair_tree, monkeypatch, capsys):
    home, tool, bins = repair_tree
    monkeypatch.setenv('TEST_RESET_TOOLS', str(tool))
    calls = fake_install(monkeypatch)
    waits = []
    def checks(*a, **kw):
        waits.append(kw['wait'])
        return {'doctor': {'ok': True}, 'service status': {'ok': False}}
    monkeypatch.setattr(reset, 'post_checks', checks)
    reset.repair(home, 'uv', tool, bins, 'trusted.whl', {}, (['stop'], ['start'], None), True)
    assert waits == [0] and ('start',) not in calls
    output = capsys.readouterr().out
    assert 'left stopped' in output and 'Unified is ready' not in output


def test_repair_installs_missing_service_and_checks_readiness(repair_tree, monkeypatch, capsys):
    home, tool, bins = repair_tree
    monkeypatch.setenv('TEST_RESET_TOOLS', str(tool))
    calls = fake_install(monkeypatch)
    waits = []
    def checks(*a, **kw):
        waits.append(kw['wait'])
        return {'doctor': {'ok': True}, 'service status': {'ok': True}}
    monkeypatch.setattr(reset, 'post_checks', checks)
    reset.repair(home, 'uv', tool, bins, 'trusted.whl', {}, None, False)
    assert any(args[-2:] == ('service', 'install') for args in calls)
    assert waits == [60]
    assert 'Unified is ready on this computer.' in capsys.readouterr().out


def test_repair_distinguishes_local_readiness_from_public_address(repair_tree, monkeypatch, capsys):
    home, tool, bins = repair_tree
    monkeypatch.setenv('TEST_RESET_TOOLS', str(tool))
    fake_install(monkeypatch)
    monkeypatch.setattr(reset, 'post_checks', lambda *a, **kw: {
        'doctor': {'ok': True},
        'service status': {'ok': True, 'url': 'https://127.0.0.1:8941',
                           'publicUrls': ['https://unreachable-onboarding.invalid'],
                           'publicUrlsVerified': False}})
    reset.repair(home, 'uv', tool, bins, 'trusted.whl', {}, None, False)
    output = capsys.readouterr().out
    assert 'Unified is ready on this computer.' in output
    assert 'Host listener: https://127.0.0.1:8941' in output
    assert 'Configured public address (not checked): https://unreachable-onboarding.invalid' in output
    assert 'Open:' not in output


@pytest.mark.parametrize('code', [403, 429, 503])
@pytest.mark.parametrize('access', ['auto', 'anonymous'])
def test_release_lookup_uses_public_link_when_api_unavailable(monkeypatch, code, access):
    import urllib.error
    from contextlib import contextmanager
    calls = []
    @contextmanager
    def urlopen(request, **kwargs):
        calls.append((request.full_url, request.get_method(), dict(request.header_items())))
        if 'api.github.com' in request.full_url:
            raise urllib.error.HTTPError(request.full_url, code, 'Unavailable', {}, None)
        yield SimpleNamespace(geturl=lambda: f'https://github.com/{reset.REPOSITORY}/releases/tag/v1.2.3')
    monkeypatch.setattr(reset.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(reset, 'command', lambda *a, **k: pytest.fail('Must not ask gh for sign-in'))
    assert reset.release_metadata({'AMPLIFIER_GITHUB_ACCESS': access})['tag_name'] == 'v1.2.3'
    assert len(calls) == 2 and calls[1][1] == 'HEAD'
    assert all('Authorization' not in headers for _, _, headers in calls)


@pytest.mark.parametrize('url', [
    'https://github.com/other/repo/releases/tag/v1.2.3',
    'http://github.com/microsoft/amplifier-unified/releases/tag/v1.2.3',
    'https://github.com/microsoft/amplifier-unified/releases/tag/v1.2.3-rc1',
    'https://github.com/microsoft/amplifier-unified/releases/tag/main',
    'https://github.com/microsoft/amplifier-unified/releases/tag/v1.2.3?next=bad',
    'https://github.com/login',
])
def test_release_link_requires_canonical_stable_release(url):
    with pytest.raises(ValueError, match='supported release'):
        reset.release_link_metadata(url)


def test_release_failure_explains_rate_limit_and_tls_without_private_details(monkeypatch):
    import urllib.error
    import ssl
    def fail_api(*args, **kwargs):
        raise urllib.error.HTTPError('https://user:secret@proxy.invalid', 403, 'private detail',
                                     {'X-RateLimit-Remaining': '0'}, None)
    def fail_link(*args, **kwargs):
        raise urllib.error.URLError(ssl.SSLCertVerificationError('private certificate detail'))
    monkeypatch.setattr(reset.urllib.request, 'urlopen', fail_api)
    monkeypatch.setattr(reset, 'public_release_link', fail_link)
    with pytest.raises(RuntimeError) as raised:
        reset.release_metadata({'AMPLIFIER_GITHUB_ACCESS': 'anonymous'})
    message = str(raised.value)
    assert 'rate limit' in message and 'TLS certificate verification failed' in message
    assert 'secret' not in message and 'private' not in message and 'proxy.invalid' not in message
