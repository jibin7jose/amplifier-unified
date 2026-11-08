"""Windows diagnostics must remain passive and cannot attest a registered task."""
import asyncio
import pytest

from amplifier_web import installation_health as health
from amplifier_web import windows_service


def test_windows_service_state_reads_scheduler_without_posix_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(health.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(windows_service, "task_state",
                        lambda root: calls.append(root) or {"state": "running"})
    monkeypatch.setattr(health.subprocess, "run", lambda *a, **kw: pytest.fail("no POSIX command"))
    assert health.service_state(tmp_path) == "running"
    assert calls == [tmp_path]


@pytest.mark.parametrize("state", ["not configured", "running", "starting", "stopped", "disabled"])
def test_windows_health_cannot_be_true_from_task_state_or_probe_untrusted_listener(tmp_path, monkeypatch, state):
    monkeypatch.setattr(health.sys, "platform", "win32")
    monkeypatch.setattr(health, "service_state", lambda root: state)
    from amplifier_web import deployment, update_readiness
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": "0.0.0", "revision": None})
    monkeypatch.setattr(deployment, "load_server_config", lambda *a, **kw: pytest.fail("no credential/probe path"))
    monkeypatch.setattr(health.asyncio, "sleep", lambda *a: pytest.fail("pending qualification cannot improve by waiting"))
    value = asyncio.run(health.status(tmp_path, wait=60))
    assert not value["ok"] and not value["processVerified"]
    assert value["qualificationPending"] and value["service"] == state
    assert value["responding"] is None and not value["probeAttempted"]
    assert "not readiness" in value["message"]


def test_windows_doctor_does_not_try_pam_or_advise_linux_package_manager(tmp_path, monkeypatch):
    from amplifier_web import deployment, host_dependencies, tls
    import builtins
    import shutil
    monkeypatch.setattr(health.sys, "platform", "win32")
    monkeypatch.setattr(host_dependencies, "require_uv", lambda: "uv.exe")
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(deployment, "load_server_config", lambda root: {"tls": {"method": "none"}})
    monkeypatch.setattr(tls, "ssl_context", lambda *args: None)
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "pam":
            pytest.fail("PAM is not Windows sign-in")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    result = health.doctor(tmp_path)
    assert not result["ok"]
    messages = " ".join(row["message"] for row in result["checks"])
    assert "Git for Windows" in messages and "qualification remain pending" in messages
    assert "apt-get" not in messages