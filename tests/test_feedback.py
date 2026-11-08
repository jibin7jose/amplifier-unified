"""No live GitHub calls: verify the shared API, privacy and lost-response boundary."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import feedback
from amplifier_web.service import AppError, AppService
from amplifier_web.updates import UpdateManager


def payload(**values):
    return {"requestId": "fixture-feedback-1", "title": "A useful idea", "body": "Please add this.",
            "category": "idea", "includeDiagnostics": False, **values}


async def settle(app):
    if app.tasks:
        await asyncio.gather(*list(app.tasks))


@pytest.fixture
def github(monkeypatch):
    create = AsyncMock(return_value=feedback.ISSUES_URL + "/42")
    monkeypatch.setattr(feedback, "create_issue", create)
    monkeypatch.setattr(feedback.shutil, "which", lambda _: "/fixture/gh")
    return create


async def test_ui_and_agent_share_durable_receipts_without_private_state(tmp_path, github, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-env-fixture")
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        app.state["settings"]["secret"] = "private-settings-fixture"
        await app.dispatch("session.create", {"title": "private-chat-title"})
        app._session()["messages"].append({"id": "x", "role": "user", "text": "private-transcript-fixture"})
        await app.app_bridge("dispatch", {"action": "feedback.submit", "args": payload(), "id": "agent-command"}, app._session()["id"])
        await settle(app)
        assert app.state["feedback"]["requests"][0]["status"] == "submitted"
        title, body = github.call_args.args
        assert title == "A useful idea"
        assert "Please add this." in body and "Category: Feature idea" in body
        assert "amplifier-feedback:fixture-feedback-1" in body
        assert "App version:" not in body
        assert all(value not in body for value in (str(tmp_path), "private-env-fixture", "private-settings-fixture", "private-chat-title", "private-transcript-fixture"))
        await app.dispatch("feedback.submit", payload(), command_id="ui-retry")
        await settle(app)
        github.assert_awaited_once()
        assert "feedback.submit" in {item["name"] for item in app.get_actions()}
    finally:
        await app.close()
    reopened = AppService(tmp_path, workspace=tmp_path)
    try:
        await reopened.dispatch("feedback.submit", payload(), command_id="after-restart")
        await settle(reopened)
        github.assert_awaited_once()
        assert reopened.state["feedback"]["requests"][0]["url"].endswith("/42")
    finally:
        await reopened.close()


async def test_diagnostics_include_build_and_bounded_state(tmp_path, github):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch("feedback.submit", payload(includeDiagnostics=True))
        await settle(app)
        facts = app.state["feedback"]["diagnostics"]
        assert set(facts) == {"appVersion", "osFamily", "pythonVersion", "packagedFrontendVersion", "packagedFrontendBuild"}
        body = github.call_args.args[1]
        assert "Reproduction diagnostics" in body
        assert json.loads(body.split("```json\n")[1].split("\n```")[0])["appVersion"] == facts["appVersion"]
    finally:
        await app.close()


@pytest.mark.parametrize('include', [False, True])
async def test_local_report_requires_no_gh_or_network_and_never_reads_drafts(tmp_path, monkeypatch, include):
    monkeypatch.setattr(feedback.shutil,'which',lambda _: None)
    monkeypatch.setattr(feedback,'github_api',AsyncMock(side_effect=AssertionError('network')))
    monkeypatch.setenv('OPENAI_API_KEY','private-env-local-report')
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        await app.dispatch('session.create',{'title':'private-conversation'})
        app._session()['messages'].append({'id':'private','role':'user','text':'private-chat-content'})
        app.state['view']['feedbackDraft']={'title':'private-draft','body':'never-read-this-draft'}
        request={'title':'A local report','body':'Exact user-supplied context.','category':'bug',
                 'includeDiagnostics':include,'destination':'none'}
        response=await app.dispatch('feedback.report',request)
        report=response['result']
        assert report['submitted'] is False and report['diagnosticsIncluded'] is include
        assert response['effects']==[] and not app.tasks
        assert report['content'].startswith('# A local report\n\nExact user-supplied context.')
        assert ('Reproduction diagnostics' in report['content']) is include
        assert all(value not in report['content'] for value in (
            str(tmp_path),'private-env-local-report','private-conversation','private-chat-content','private-draft','never-read-this-draft'))
        assert app.db.execute('SELECT count(*) FROM feedback_requests').fetchone()[0]==0
        assert app.state['view']['feedbackDraft']['body']=='never-read-this-draft'
        downloaded=await app.dispatch('feedback.report',{**request,'destination':'download'})
        assert downloaded['effects']==[{'type':'download','filename':'amplifier-feedback.md',
            'mime':'text/markdown','content':downloaded['result']['content']}]
        feedback.github_api.assert_not_awaited()
    finally:
        await app.close()


async def test_accepted_but_lost_github_response_never_reposts(tmp_path, github):
    github.side_effect = TimeoutError("credential-bearing private failure")
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch("feedback.submit", payload(), command_id="first")
        await settle(app)
        assert app.state["feedback"]["requests"][0]["status"] == "unknown"
        assert "credential-bearing" not in json.dumps(app.state)
        await app.dispatch("feedback.submit", payload(), command_id="second")
        await settle(app)
        github.assert_awaited_once()
        with pytest.raises(AppError, match="different text"):
            await app.dispatch("feedback.submit", payload(body="Changed draft"))
        github.assert_awaited_once()
    finally:
        await app.close()


async def test_interrupted_submission_and_exact_retry_remain_unknown(tmp_path, github):
    app = AppService(tmp_path, workspace=tmp_path)
    app.feedback.accept(payload())
    app._save()
    await app.close()
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        assert app.state["feedback"]["requests"][0]["status"] == "unknown"
        await app.dispatch("feedback.submit", payload())
        await settle(app)
        github.assert_not_awaited()
    finally:
        await app.close()


async def test_missing_cli_reports_no_send_and_update_gate_prevents_start(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback.shutil, "which", lambda _: None)
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        app.state["updates"] = {"phase": "activating"}
        with pytest.raises(AppError, match="activating"):
            await app.dispatch("feedback.submit", payload())
        assert not app.state["feedback"]["requests"]
        app.state["updates"]["phase"] = "idle"
        await app.dispatch("feedback.submit", payload())
        assert UpdateManager(app).busy()
        await settle(app)
        result = app.state["feedback"]["requests"][0]
        assert result["status"] == "failed" and "Nothing was sent" in result["message"]
    finally:
        await app.close()


@pytest.mark.parametrize("change", [{"title": " "}, {"body": ""}, {"category": "arbitrary"}, {"requestId": "../invalid"}, {"includeDiagnostics": "yes"}, {"unexpected": "value"}])
async def test_typed_validation_rejects_invalid_requests(tmp_path, github, change):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        with pytest.raises(AppError):
            await app.dispatch("feedback.submit", payload(**change))
        github.assert_not_awaited()
    finally:
        await app.close()


async def test_github_boundary_uses_fixed_repo_structured_stdin_and_validates_url(monkeypatch):
    class Child:
        returncode = 0
        async def communicate(self, raw):
            self.payload = json.loads(raw)
            return json.dumps({"html_url": feedback.ISSUES_URL + "/12"}).encode(), b""
    child = Child()
    spawn = AsyncMock(return_value=child)
    monkeypatch.setattr(feedback.shutil, "which", lambda _: "/fixture/gh")
    monkeypatch.setattr(feedback.asyncio, "create_subprocess_exec", spawn)
    title = "Literal `whoami` $(echo anything)"
    assert await feedback.create_issue(title, "Body\nline two") == feedback.ISSUES_URL + "/12"
    assert spawn.call_args.args == ("/fixture/gh", "api", "--hostname", "github.com", "--method", "POST", "repos/microsoft/amplifier-unified/issues", "--input", "-")
    assert child.payload == {"title": title, "body": "Body\nline two"}
    child.communicate = AsyncMock(return_value=(b'{"html_url":"https://other.example/issue"}', b""))
    with pytest.raises(ValueError, match="invalid issue receipt"):
        await feedback.create_issue("title", "body")


async def test_active_submission_survives_completed_history_limit_and_old_receipt_can_be_read(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        app.feedback.accept(payload(requestId="old-active-request"))
        for index in range(25):
            identity = f"completed-request-{index}"
            app.feedback.accept(payload(requestId=identity))
            await app.feedback.update(identity, status="submitted", url=feedback.ISSUES_URL + "/42")
        assert len(app.state["feedback"]["requests"]) == 21
        assert any(item["requestId"] == "old-active-request" for item in app.state["feedback"]["requests"])
        assert UpdateManager(app).busy()
        assert app.feedback.accept(payload(requestId="completed-request-0")) is False
        assert any(item["requestId"] == "completed-request-0" for item in app.state["feedback"]["requests"])
    finally:
        await app.close()
