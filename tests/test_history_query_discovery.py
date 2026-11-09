"""Small history reads retain coverage without repeating global issue samples."""
import copy
import json

import pytest

from amplifier_web.service import ACTION_DEFINITIONS, AppService


@pytest.mark.parametrize("action", ["list", "search", "read"])
async def test_discovery_samples_are_explicit_and_do_not_change_history(action, tmp_path, monkeypatch):
    app = AppService(tmp_path / "app", workspace=tmp_path)
    try:
        root = app._new_session({"title": "Owned history", "workspace": str(tmp_path)})
        root["messages"] = [{"id": "message", "role": "user", "text": "synthetic needle"}]
        app.state["sessions"].append(root)
        await app.history.refresh()

        async def refreshed(*, force=False):
            assert force is False

        monkeypatch.setattr(app.history, "refresh_catalog", refreshed)
        discovery = {
            "loading": False, "error": None, "issueCount": 838,
            "reconciliation": {"phase": "complete", "completedAt": 123},
            "issues": [{"path": f"synthetic/project-{i}/session", "error": "Retained metadata unavailable " + "x" * 80}
                       for i in range(100)],
        }
        app.state["sharedHistory"] = copy.deepcopy(discovery)
        before = copy.deepcopy(app.state)
        args = {"action": action, "limit": 1}
        if action == "read":
            args["session_id"] = root["id"]
        elif action == "search":
            args["query"] = "needle"

        compact = await app.app_bridge("history", args, root["id"])
        detailed = await app.app_bridge("history", {**args, "include_discovery_issues": True}, root["id"])
        assert compact["complete"] is detailed["complete"] is False
        assert "issues" not in compact["discovery"]
        assert compact["discovery"]["issueCount"] == 838
        assert compact["discovery"]["issueSamplesAvailable"] == 100
        assert compact["discovery"]["issuesOmitted"] is True
        assert "include_discovery_issues=true" in compact["discovery"]["detailsHint"]
        assert detailed["discovery"]["issues"] == discovery["issues"]
        assert detailed["discovery"]["issuesOmitted"] is False
        assert {k: v for k, v in compact.items() if k != "discovery"} == {
            k: v for k, v in detailed.items() if k != "discovery"}
        assert len(json.dumps(compact["discovery"])) < 500
        assert len(json.dumps(detailed["discovery"])) > 10000
        assert app.state == before
        # A caller cannot mutate the shared diagnostic state through a receipt.
        detailed["discovery"]["issues"][0]["error"] = "changed"
        assert app.state["sharedHistory"] == discovery
    finally:
        await app.close()


@pytest.mark.parametrize("diagnostics,complete", [
    ({"loading": False, "error": None, "issueCount": 0, "reconciliation": {"phase": "complete"}}, True),
    ({"loading": True, "issueCount": 0}, False),
    ({"error": "Could not refresh history", "issueCount": 0}, False),
    ({"issueCount": 838, "issues": []}, False),
    ({"issueCount": 0, "reconciliation": {"phase": "scanning"}}, False),
])
async def test_coverage_and_errors_survive_without_samples(diagnostics, complete, tmp_path, monkeypatch):
    app = AppService(tmp_path / "app", workspace=tmp_path)
    try:
        root = app._new_session({"title": "Owned history", "workspace": str(tmp_path)})
        app.state["sessions"].append(root)
        await app.history.refresh()

        async def refreshed(*, force=False):
            pass

        monkeypatch.setattr(app.history, "refresh_catalog", refreshed)
        app.state["sharedHistory"] = copy.deepcopy(diagnostics)
        result = await app.app_bridge("history", {"action": "list"}, root["id"])
        assert result["complete"] is complete
        for key in ("loading", "error", "issueCount", "reconciliation"):
            assert result["discovery"][key] == diagnostics.get(key)
        assert result["discovery"]["issueSamplesAvailable"] == 0
        assert result["discovery"]["issuesOmitted"] is False
    finally:
        await app.close()


@pytest.fixture
async def alias_history(tmp_path, monkeypatch):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    app = AppService(tmp_path / "app", workspace=tmp_path)
    try:
        rows = [app._new_session({"title": f"Chat {i}", "workspace": str(tmp_path / space)})
                for i, space in enumerate(("one", "one", "one", "two"))]
        for row in rows:
            row["messages"] = [{"id": f"message-{i}", "role": "user", "text": f"Saved text {i}"}
                               for i in range(3)]
        app.state["sessions"] = rows
        app.state["selectedSessionId"] = rows[0]["id"]
        app.state["view"]["draft"] = "Unsent draft"
        app._publish()
        await app.history.refresh()

        async def refreshed(*, force=False):
            assert force is False

        monkeypatch.setattr(app.history, "refresh_catalog", refreshed)
        app.state["sharedHistory"] = {"loading": False, "error": None, "issueCount": 838,
            "reconciliation": {"phase": "complete"},
            "issues": [{"path": f"synthetic/{i}", "error": "Unavailable metadata"} for i in range(100)]}
        yield app, rows
    finally:
        await app.close()


@pytest.mark.parametrize("action", ["history.list", "history.shared.list", "history.shared.view"])
async def test_alias_can_follow_its_discovery_hint_without_changing_scope_or_page(alias_history, action):
    app, rows = alias_history
    owner, other = rows[0], rows[-1]
    before = copy.deepcopy(app.state)
    args = {"offset": 1, "limit": 1}
    if action.endswith(".view"):
        args.update(id=owner["id"], workspace=owner["workspace"])

    async def dispatch(values):
        return (await app.app_bridge("dispatch", {"action": action, "args": values}, owner["id"]))["result"]

    compact = await dispatch(args)
    assert compact["complete"] is False and compact["scope"] == "workspace"
    assert compact["next_offset"] == 2
    assert "issues" not in compact["discovery"]
    assert compact["discovery"]["issueCount"] == 838
    assert compact["discovery"]["issueSamplesAvailable"] == 100
    # Follow the exact recovery advertised by this alias, through real validation
    # and dispatch. Merely testing query_history directly misses this boundary.
    assert "include_discovery_issues=true" in compact["discovery"]["detailsHint"]
    detailed = await dispatch({**args, "include_discovery_issues": True})
    assert detailed["complete"] is False
    assert detailed["discovery"]["issueCount"] == 838
    assert detailed["discovery"]["issues"] == before["sharedHistory"]["issues"]
    assert len(detailed["discovery"]["issues"]) == 100
    assert detailed["discovery"]["issuesOmitted"] is False
    assert {k: v for k, v in detailed.items() if k != "discovery"} == {
        k: v for k, v in compact.items() if k != "discovery"}
    assert await dispatch({**args, "include_discovery_issues": False}) == compact
    assert ACTION_DEFINITIONS[action][1]["properties"]["include_discovery_issues"]["type"] == "boolean"

    if action.endswith(".view"):
        foreign = {"id": other["id"], "workspace": other["workspace"], "limit": 1}
        for opt_in in (False, True):
            with pytest.raises(ValueError, match="scope"):
                await dispatch({**foreign, "include_discovery_issues": opt_in})
        assert (await dispatch({**foreign, "scope": "all", "include_discovery_issues": True}))["session"]["id"] == other["id"]
    else:
        own = await dispatch({"limit": 50, "include_discovery_issues": True})
        all_rows = await dispatch({"scope": "all", "limit": 50, "include_discovery_issues": True})
        assert {row["id"] for row in own["items"]} == {row["id"] for row in rows[:-1]}
        assert {row["id"] for row in all_rows["items"]} == {row["id"] for row in rows}
    assert app.state == before
    detailed["discovery"]["issues"][0]["error"] = "Caller mutation"
    assert app.state == before


@pytest.mark.parametrize("opt_in", [False, True])
async def test_requested_history_errors_remain_separate_from_global_samples(alias_history, monkeypatch, opt_in):
    import amplifier_web.history_query as history
    app, rows = alias_history
    owner = rows[0]

    def unavailable(session):
        raise ValueError("Synthetic requested history failure")

    monkeypatch.setattr(history, "_rows", unavailable)
    result = await app.app_bridge("history", {"action": "search", "query": "not present",
        "include_discovery_issues": opt_in}, owner["id"])
    assert result["complete"] is False
    assert {row["session_id"] for row in result["errors"]} == {row["id"] for row in rows[:-1]}
    assert result["discovery"]["issueCount"] == 838
    assert ("issues" in result["discovery"]) is opt_in
