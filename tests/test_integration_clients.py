"""Client prerequisite guards must fail closed rather than create green skips."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import integration_clients as clients


def test_source_identity_rejects_untracked_and_modified_package_inputs(tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nname = "owned-fixture"\nversion = "0.0.0"\n')
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "pyproject.toml"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"],
                   check=True, capture_output=True)
    assert len(clients.checked_source(tmp_path)["revision"]) == 40
    extra = tmp_path / "untracked_package_input.py"
    extra.write_text("unexpected = True\n")
    with pytest.raises(ValueError, match="clean, revision-bound checkout"):
        clients.checked_source(tmp_path)
    extra.unlink()
    project.write_text(project.read_text() + "description = 'modified'\n")
    with pytest.raises(ValueError, match="clean, revision-bound checkout"):
        clients.checked_source(tmp_path)


def test_lanes_assign_exact_existing_cases_without_duplicate_ownership(tmp_path):
    selectors = [selector for lane in clients.LANES.values() for selector in lane["selectors"]]
    assert len(selectors) == len(set(selectors)) == 10
    assert clients.LANES["browser-installed"]["manual"] is True
    assert all(not spec["manual"] for name, spec in clients.LANES.items() if name != "browser-installed")
    assert clients.describe("browser-headless", tmp_path)["allowed_skips"] == []


def test_environment_roots_are_private_and_cli_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.shutil, "which", lambda _: None)
    for name in clients.LANES:
        plan = clients.describe(name, tmp_path / name)
        assert plan["interpreter"] == str(tmp_path / name / "env/bin/python")
        for key, value in plan["environment"].items():
            if key not in {"PYTHONDONTWRITEBYTECODE", "AMPLIFIER_TEST_INSTALLED_PWA"}:
                assert Path(value).is_relative_to(tmp_path / name)
    assert "AMPLIFIER_CLI_TEST_ENV" not in clients.describe("browser-headless", tmp_path)["environment"]
    assert "AMPLIFIER_RESET_TEST_UV" not in clients.describe("uv-reset", tmp_path)["environment"]


def test_prepare_refuses_reusing_existing_state(tmp_path):
    with pytest.raises(ValueError, match="new disposable state"):
        clients.prepare("uv-reset", tmp_path, tmp_path, python="3.13", cli_source=None, loop_source=None)


def test_cli_prepare_requires_revision_bound_dependency_sources_before_creating_state(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.shutil, "which", lambda _: "/owned/uv")
    target = tmp_path / "new"
    with pytest.raises(ValueError, match="explicit --cli-source and --loop-source"):
        clients.prepare("cli-interop", target, tmp_path, python="3.13", cli_source=None, loop_source=None)
    assert not target.exists()


def test_filesystem_prepare_requires_its_real_optional_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.shutil, "which", lambda _: "/owned/uv")
    target = tmp_path / "new"
    with pytest.raises(ValueError, match="explicit --filesystem-source and --apply-patch-source"):
        clients.prepare("filesystem-aliases", target, tmp_path, python="3.13", cli_source=None, loop_source=None)
    assert not target.exists()


def test_missing_module_is_failure_not_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.importlib.util, "find_spec", lambda _: None)
    with pytest.raises(ValueError, match="Required integration module is missing"):
        clients.preflight("cli-interop", tmp_path)


def test_cli_refuses_ordinary_interpreter_even_when_importable(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.importlib.util, "find_spec", lambda _: SimpleNamespace(origin="owned/module.py"))
    monkeypatch.delenv("AMPLIFIER_CLI_TEST_ENV", raising=False)
    with pytest.raises(ValueError, match="designated disposable test virtualenv"):
        clients.preflight("cli-interop", tmp_path)


@pytest.mark.parametrize("capabilities", [{"case": False, "unicode": False}, {"case": True, "unicode": False}, {"case": False, "unicode": True}])
def test_filesystem_owner_refuses_inapplicable_volume(tmp_path, monkeypatch, capabilities):
    monkeypatch.setattr(clients.importlib.util, "find_spec", lambda _: SimpleNamespace(origin="owned/module.py"))
    monkeypatch.setattr(clients, "alias_capabilities", lambda _: capabilities)
    with pytest.raises(ValueError, match="must support both aliases, not skip"):
        clients.preflight("filesystem-aliases", tmp_path)


def test_filesystem_preflight_requires_both_semantics(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.importlib.util, "find_spec", lambda _: SimpleNamespace(origin="owned/module.py"))
    monkeypatch.setattr(clients, "alias_capabilities", lambda _: {"case": True, "unicode": True})
    result = clients.preflight("filesystem-aliases", tmp_path)
    assert result["preflight"] == "ready"
    assert result["prerequisites"]["filesystem_aliases"] == {"case": True, "unicode": True}


def test_reset_refuses_missing_uv_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("AMPLIFIER_RESET_TEST_UV", raising=False)
    with pytest.raises(ValueError, match="requires AMPLIFIER_RESET_TEST_UV"):
        clients.preflight("uv-reset", tmp_path)


@pytest.mark.parametrize('dependencies', [False, True])
def test_browser_preparation_can_target_existing_noneditable_interpreter(tmp_path, dependencies):
    import json
    (tmp_path / 'tests/web').mkdir(parents=True)
    (tmp_path / 'tests/web/package-lock.json').write_text(json.dumps({
        'packages': {'node_modules/playwright': {'version': '1.63.0'}}}))
    commands = clients.browser_prepare_commands(tmp_path, '/owned/uv', '/owned/.venv/bin/python',
                                                 install_dependencies=dependencies)
    assert commands[0] == ['/owned/uv', 'pip', 'install', '--python',
                           '/owned/.venv/bin/python', 'playwright==1.63.0']
    assert commands[1] == ['/owned/.venv/bin/python', '-m', 'playwright', 'install',
                           *(['--with-deps'] if dependencies else []), 'chromium']
    assert not any('sync' in row or '--editable' in row for row in commands)



def test_browser_preparation_uses_explicit_old_source_lock_only_when_new_owner_absent(tmp_path):
    import json
    (tmp_path / 'frontend').mkdir()
    (tmp_path / 'frontend/package-lock.json').write_text(json.dumps({
        'packages': {'node_modules/playwright': {'version': '1.62.0'}}}))
    commands = clients.browser_prepare_commands(tmp_path, '/owned/uv', '/owned/python')
    assert commands[0][-1] == 'playwright==1.62.0'
    (tmp_path / 'tests/web').mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        clients.browser_prepare_commands(tmp_path, '/owned/uv', '/owned/python')
