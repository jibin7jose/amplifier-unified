"""Saved attachments use exact owner metadata without warming native execution."""
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from amplifier_web.errors import AppError
from amplifier_web.execution_owner import ExecutionOwner
from amplifier_web.service import AppService
from test_automatic_history import app_factory


@pytest.fixture
async def saved(tmp_path):
    runtime = SimpleNamespace(start=AsyncMock(), send=AsyncMock(), close=AsyncMock())
    app = AppService(tmp_path / "app", runtime, workspace=tmp_path)
    owner = ExecutionOwner(app, workspace=tmp_path, bundle="foundation", origin="ui")
    identity = await owner.create(str(tmp_path))
    await owner.release(identity)
    yield app, owner, runtime, identity
    await owner.close()


async def test_attach_is_metadata_only_and_preserves_saved_location(saved, tmp_path, monkeypatch):
    app, owner, runtime, identity = saved
    moved = tmp_path / "committed-checkout"
    moved.mkdir()
    source = app._session(identity)
    source["workingDirectory"] = str(moved)
    source["executionRevision"] = 2
    source["messages"] = [{"id": str(i), "role": "user", "text": "retained"} for i in range(2000)]
    selected = app.state["selectedSessionId"]
    count = app.db.execute("SELECT count(*) FROM commands").fetchone()[0]

    def forbidden(*args, **kwargs):
        raise AssertionError("Attachment must not hydrate or admit saved history")
    monkeypatch.setattr(app, "_session", forbidden)
    monkeypatch.setattr(app, "_admit_session", forbidden)
    monkeypatch.setattr(app.cold_display, "hydrate", forbidden)
    monkeypatch.setattr(app.history, "load", forbidden)
    monkeypatch.setattr(app.history.windows, "read", forbidden)
    assert await owner.attach(identity, str(moved)) == identity
    assert owner.attached == {identity}
    assert app.state["selectedSessionId"] == selected
    assert app.db.execute("SELECT count(*) FROM commands").fetchone()[0] == count
    assert source["executionRevision"] == 2 and len(source["messages"]) == 2000
    runtime.start.assert_not_called()
    runtime.send.assert_not_called()


@pytest.mark.parametrize("invalid", ["missing", "foreign-workspace", "wrong-cwd", "worker", "deleted", "alias"])
async def test_invalid_attachment_never_creates_or_moves(saved, tmp_path, invalid):
    app, owner, runtime, identity = saved
    source = app._session(identity)
    request_id, cwd = identity, str(tmp_path)
    if invalid == "missing":
        request_id = "missing"
    elif invalid == "foreign-workspace":
        other = tmp_path / "other"
        other.mkdir()
        source["workspace"] = str(other)
    elif invalid == "wrong-cwd":
        other = tmp_path / "other"
        other.mkdir()
        cwd = str(other)
    elif invalid == "worker":
        source["sessionKind"] = "worker"
    elif invalid == "deleted":
        source["_deleting"] = True
    else:
        request_id = "native-alias"
        source["runtimeSessionId"] = request_id
        assert app._session_read_sources([request_id], ("id",))[0]["id"] == identity
    before = (len(app.state["sessions"]), source.get("workingDirectory"),
              app.db.execute("SELECT count(*) FROM commands").fetchone()[0])
    with pytest.raises((ValueError, AppError)):
        await owner.attach(request_id, cwd)
    assert not owner.attached
    assert before == (len(app.state["sessions"]), source.get("workingDirectory"),
                      app.db.execute("SELECT count(*) FROM commands").fetchone()[0])
    runtime.start.assert_not_called()
    runtime.send.assert_not_called()


async def test_attachment_does_not_claim_writer_ownership(saved, tmp_path):
    app, owner, runtime, identity = saved
    app._session(identity)["ownership"] = {"status": "blocked", "owner": "another-runtime"}
    assert await owner.attach(identity, str(tmp_path)) == identity
    with pytest.raises(AppError, match="read-only"):
        await owner.prompt(identity, "new-input", "Never replace the owner", AsyncMock())
    runtime.start.assert_not_called()
    runtime.send.assert_not_called()


async def test_dormant_saved_owner_stays_out_of_live_overlay(app_factory, monkeypatch):
    from amplifier_web.state_records import load_owner
    from test_history_directory_persistence import saved_chats
    app, rows = saved_chats(app_factory, count=2)
    identity, workspace = rows[-1]["id"], rows[-1]["workspace"]
    retained = load_owner(app.db, identity)
    await app.close()
    restored = app_factory(home=app.data_dir)
    before = [row["id"] for row in restored.state["sessions"]]
    assert identity not in before
    assert restored.directory.resolve(identity)["id"] == identity
    owner = ExecutionOwner(restored, workspace=workspace, bundle="foundation", origin="ui")
    def forbidden(*args, **kwargs):
        raise AssertionError("Dormant attachment must not hydrate or admit its history")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(restored, "_session", forbidden)
            patch.setattr(restored, "_admit_session", forbidden)
            patch.setattr(restored.cold_display, "hydrate", forbidden)
            patch.setattr(restored.history, "load", forbidden)
            patch.setattr(restored.history.windows, "read", forbidden)
            assert await owner.attach(identity, workspace) == identity
            assert [row["id"] for row in restored.state["sessions"]] == before
            assert not restored.runtime.started and not restored.runtime.sent
        assert load_owner(restored.db, identity) == retained
    finally:
        await owner.close()
