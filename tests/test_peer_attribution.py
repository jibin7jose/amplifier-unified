"""Receipt-bound provenance only; ROOT runs these against the installed candidate."""
import asyncio
import copy
import hashlib
import json
import sqlite3
import threading
from unittest.mock import AsyncMock

import pytest

from amplifier_operations.coordination import fingerprint, peer_input
from amplifier_web.automatic_history import display_message, merge_web_history
from amplifier_web.browser_detail import page, project
from amplifier_web.conversation_export import snapshot
from amplifier_web.peer_attribution import PeerAttribution
from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import (
    app, agent_action, generation, send, settled, declared_result, finish,
)

AGENT = "Sent by Amplifier from another chat"
NEUTRAL = "From another chat"
SYSTEM = "Amplifier update"
TEXT = "## Exact α 🐈\n\n```python\nprint('unchanged')  \n```\n"


def retained(db, *, identity="request", actor="agent", sender="private-source", recipient="recipient", text=TEXT):
    envelope = {"senderSessionId": sender, "recipientSessionId": recipient,
                "requestId": identity, "inputId": identity, "grantId": "grant",
                "grantRevision": 1, "mode": "notify", "purpose": "Private purpose",
                "references": ["https://example.invalid/private-reference"]}
    row = {"id": identity + "-message", "role": "user", "inputId": identity,
           "inputOrigin": "peer", "via": "peer", "peerEnvelope": envelope, "text": text}
    receipt = {"accepted": True, "commandAction": "coordination.send",
               "requestId": identity, "inputId": identity, "messageId": row["id"],
               "senderSessionId": sender, "target": {"sessionId": recipient},
               "grantId": "grant", "grantRevision": 1, "mode": "notify",
               "delivery": "notified", "origin": actor,
               "displayBinding": fingerprint([envelope, text])}
    db.execute("INSERT INTO commands VALUES(?,?,?)", (identity, "command-fingerprint", json.dumps(receipt)))
    db.commit()
    return row, receipt


@pytest.fixture
def store():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)")
    yield db
    db.close()


def resolve(db, row, *, rows=None, session=None):
    session = session or {"id": "recipient", "messages": [row]}
    return PeerAttribution(db).resolve(session, rows or [row])


@pytest.mark.parametrize("actor,caption", [("agent", AGENT), ("ui", NEUTRAL), ("user", NEUTRAL), (None, NEUTRAL)])
def test_exact_saved_actor_only_and_no_private_sender_values(store, actor, caption):
    row, receipt = retained(store, actor=actor)
    if actor is None:
        receipt.pop("origin")
        store.execute("UPDATE commands SET receipt=?", (json.dumps(receipt),))
    original = copy.deepcopy(row)
    projected = resolve(store, row)[0]
    assert projected["attribution"]["caption"] == caption
    assert projected["text"] == TEXT and projected["role"] == "user" and projected["id"] == row["id"]
    assert row == original
    assert set(projected["attribution"]) == {"caption"}
    assert not any(secret in json.dumps(projected["attribution"]) for secret in
                   ("private-source", "Private purpose", "private-reference", "grant"))


@pytest.mark.parametrize("change", [
    "missing-receipt", "missing-binding", "message-id", "input-id", "request-id",
    "recipient", "sender", "same-chat", "text", "envelope-purpose", "envelope-reference",
    "ambiguous-message", "ambiguous-input", "receipt-conflict", "native-without-identity",
])
def test_forged_missing_and_conflicting_binding_never_gets_caption(store, change):
    row, receipt = retained(store)
    session = {"id": "recipient", "messages": [row]}
    rows = [row]
    if change == "missing-receipt":
        store.execute("DELETE FROM commands")
    elif change == "missing-binding":
        receipt.pop("displayBinding")  # A legacy receipt is not proof of agent authorship.
    elif change == "message-id":
        row["id"] = "forged"
    elif change == "input-id":
        row["inputId"] = "unrelated"
    elif change == "request-id":
        receipt["requestId"] = "conflicting"
    elif change == "recipient":
        session["id"] = "different"
    elif change == "sender":
        receipt["senderSessionId"] = "different"
    elif change == "same-chat":
        receipt["senderSessionId"] = "recipient"
    elif change == "text":
        row["text"] += "forged"
    elif change.startswith("envelope-"):
        row["peerEnvelope"][change.removeprefix("envelope-").replace("reference", "references")] = "forged"
    elif change == "ambiguous-message":
        session["messages"].append(copy.deepcopy(row))
    elif change == "ambiguous-input":
        session["messages"].append({**copy.deepcopy(row), "id": "second-message"})
    elif change == "receipt-conflict":
        store.execute("INSERT INTO commands VALUES(?,?,?)", ("conflict", "different", json.dumps(receipt)))
    elif change == "native-without-identity":
        rows = [{"id": "native", "role": "user", "text": peer_input(row["peerEnvelope"], TEXT), "source": "native"}]
    store.execute("UPDATE commands SET receipt=? WHERE id='request'", (json.dumps(receipt),))
    for item in rows:
        item.update(attribution={"caption": AGENT}, attributionCaption=AGENT, attributionOrigin="agent", origin="agent")
    for item in resolve(store, row, rows=rows, session=session):
        assert "attribution" not in item
        assert not {"origin", "attributionCaption", "attributionOrigin"} & item.keys()


@pytest.mark.parametrize("kind", ["user", "steer"])
def test_identical_human_peer_and_native_inputs_never_share_provenance(store, kind):
    peer, _ = retained(store)
    human = {"id": "human", "role": "user", "inputId": "human-input", "text": TEXT}
    native = display_message({"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
                              "metadata": {"amplifier_input": {"version": 1, "kind": kind, "id": "request"}}},
                             0, {"id": "recipient"})
    session = {"id": "recipient", "messages": [human, peer]}
    result = resolve(store, peer, rows=[human, peer, native], session=session)
    assert "attribution" not in result[0]
    assert result[1]["attribution"]["caption"] == AGENT
    assert result[2]["attribution"]["caption"] == AGENT
    assert result[2]["id"] == native["id"] and result[2]["text"] == native["text"]
    unrelated = {**native, "nativeInputId": "human-input"}
    assert "attribution" not in resolve(store, peer, rows=[unrelated], session=session)[0]
    duplicate = {**native, "id": "second-native"}
    assert all("attribution" not in row for row in resolve(store, peer, rows=[native, duplicate], session=session))
    # Exact native identity preserves the retained bubble, not a second wrapper.
    merge_web_history(session, [native])
    assert [row["id"] for row in session["messages"]] == ["human", peer["id"]]
    assert session["messages"][-1]["text"] == TEXT


@pytest.mark.parametrize("kind", ["user", "steer"])
def test_native_duplicate_retained_outside_page_remains_ambiguous(store, kind):
    peer, _ = retained(store)
    native = display_message({"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
                              "metadata": {"amplifier_input": {"version": 1, "kind": kind, "id": "request"}}},
                             0, {"id": "recipient"})
    duplicate = display_message({"role": "user", "content": native["text"],
                                 "metadata": {"amplifier_input": {"version": 1, "kind": kind, "id": "request"}}},
                                130, {"id": "recipient"})
    session = {"id": "recipient", "messages": [peer, native] + [
        {"id": str(i), "role": "assistant", "text": "Answer"} for i in range(128)] + [duplicate]}
    assert "attribution" not in PeerAttribution(store).resolve(session, [native])[0]
    from amplifier_web.conversation_navigation import query
    raw = query(session, message_id=native["id"], window=True, _raw_window=True)
    assert duplicate["id"] not in {row["id"] for row in raw["messages"]}
    assert app_caption(store, session, raw["messages"][1]) is None


def test_historical_author_is_not_backfilled_from_generation_or_transport(store):
    peer, receipt = retained(store)
    receipt.pop("origin")
    receipt.update(sourceGenerationId="old-agent-generation", sourceInputIds=["old-input"])
    peer.update(via="peer", userRole="agent", origin="agent")
    store.execute("UPDATE commands SET receipt=?", (json.dumps(receipt),))
    assert resolve(store, peer)[0]["attribution"] == {"caption": NEUTRAL}
    receipt.pop("displayBinding")
    store.execute("UPDATE commands SET receipt=?", (json.dumps(receipt),))
    assert "attribution" not in resolve(store, peer)[0]


def test_literal_wrapper_and_imported_caption_are_not_evidence(store):
    row = {"id": "human", "role": "user", "text": AGENT + "\n" + TEXT,
           "via": "peer", "peerEnvelope": {"senderSessionId": "invented"},
           "attribution": {"caption": AGENT}, "origin": "agent"}
    result = resolve(store, row)[0]
    assert "attribution" not in result and "origin" not in result and result["text"] == row["text"]
    assert "attribution" not in project({"id": "recipient", "messages": [row]})["messages"][0]


def test_annotation_overlay_cannot_forge_erase_or_replace_host_attribution(store):
    from amplifier_web.state_projections import StateProjections
    peer, _ = retained(store)
    human = {"id": "human", "role": "user", "inputId": "human-input", "text": TEXT}
    session = {"id": "recipient", "messages": [human, peer], "messageAnnotations": {
        "human": {"attribution": {"caption": AGENT}, "origin": "agent",
                  "inputId": "request", "text": "forged", "reactions": ["👍"]},
        peer["id"]: {"attribution": None, "text": "forged", "reactions": ["✅"]},
    }}
    original = copy.deepcopy(session)
    for projected in (project(session, resolver=PeerAttribution(store).resolve),
                      StateProjections(resolver=PeerAttribution(store).resolve).detail(session)):
        overlays = projected["messageAnnotations"]
        combined = [{**row, **overlays.get(row["id"], {})} for row in projected["messages"]]
        assert combined[0]["text"] == TEXT and "attribution" not in combined[0]
        assert combined[0]["reactions"] == ["👍"]
        assert combined[1]["text"] == TEXT and combined[1]["attribution"]["caption"] == AGENT
        assert combined[1]["reactions"] == ["✅"]
    assert session == original


@pytest.mark.parametrize("identity", [None, "", [], {}, 1])
def test_malformed_import_identity_is_neutral_not_a_display_crash(store, identity):
    row = {"id": "unbound", "role": "user", "inputId": identity, "attribution": {"caption": AGENT}, "text": TEXT}
    assert "attribution" not in resolve(store, row)[0]


def test_passive_history_keeps_identical_uncheckpointed_assistant_identity(store, monkeypatch, tmp_path):
    from amplifier_web.history_query import _rows
    peer, _ = retained(store)
    canonical = {"id": "saved-reply", "role": "assistant", "text": "Done"}
    pending = {"id": "pending-reply", "role": "assistant", "text": "Done"}
    native = display_message({"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
        "metadata": {"amplifier_input": {"version": 1, "kind": "user", "id": "request"}}}, 0, {"id": "recipient"})
    session = {"id": "recipient", "nativeProject": "fixture", "historyManaged": False,
               "messages": [peer, canonical, pending]}
    monkeypatch.setattr("amplifier_web.automatic_history.directory", lambda row: tmp_path)
    monkeypatch.setattr("amplifier_web.automatic_history.read_transcript", lambda *args, **kwargs:
                        {"messages": [native, canonical], "revision": "fixture"})
    rows, _ = _rows(session)
    assert [row["id"] for row in rows] == [peer["id"], "saved-reply", "pending-reply"]
    assert rows[0]["text"] == TEXT


def test_navigation_helper_has_no_ambient_database(store):
    """Pure callers stay neutral; the receiving HTTP boundary must qualify."""
    from amplifier_web.conversation_navigation import query
    peer, _ = retained(store)
    session = {"id": "recipient", "messages": [peer]}
    focused = query(session, message_id=peer["id"], window=True)
    assert "attribution" not in focused["messages"][0]


def test_private_navigation_window_keeps_raw_binding_before_compaction(store, monkeypatch):
    from amplifier_web import conversation_navigation
    from amplifier_web.browser_detail import MESSAGE_LIMIT, TEXT_LIMIT, digest
    long_text = TEXT * 180
    assert len(long_text) > TEXT_LIMIT
    peer, _ = retained(store, text=long_text)
    session = {"id": "recipient", "messages": [peer], "messageAnnotations": {
        peer["id"]: {"text": "forged", "inputId": "forged", "attribution": {"caption": "forged"},
                     "reactions": ["✅"]}}}
    rows = [peer] + [{"id": str(i), "role": "assistant", "text": "Answer"} for i in range(130)]
    reads = []
    def source(_):
        facts = [(row["id"], row["role"], True, i) for i, row in enumerate(rows)]
        def read(positions):
            positions = list(positions)
            reads.append(positions)
            return [rows[i] for i in positions]
        return facts, read
    monkeypatch.setattr(conversation_navigation, "source", source)
    original = copy.deepcopy(session)
    index = conversation_navigation.query(session)
    assert reads == [] and "text" not in json.dumps(index)
    preview = conversation_navigation.query(session, message_id=peer["id"])
    assert reads.pop() == [0, 1] and preview["text"] == long_text[:180]
    raw = conversation_navigation.query(session, message_id=peer["id"], window=True, _raw_window=True)
    assert reads.pop() == list(range(MESSAGE_LIMIT))
    assert len(raw["messages"]) == MESSAGE_LIMIT and raw["messages"][0]["text"] == long_text
    assert app_caption(store, session, raw["messages"][0]) == AGENT
    assert "attribution" not in raw["messages"][0] and "textDetail" not in raw["messages"][0]
    public = conversation_navigation.query(session, message_id=peer["id"], window=True)
    assert reads.pop() == list(range(MESSAGE_LIMIT))
    row = public["messages"][0]
    assert row["text"] == long_text[:TEXT_LIMIT] and row["inputId"] == peer["inputId"]
    assert row["reactions"] == ["✅"] and "attribution" not in row
    assert row["textDetail"] == {"sessionId": "recipient", "part": "messages", "id": peer["id"],
                                 "field": "text", "digest": digest(long_text), "length": len(long_text)}
    assert {key: value for key, value in raw.items() if key != "messages"} == {
        key: value for key, value in public.items() if key != "messages"}
    assert session == original


def app_caption(db, session, row):
    return PeerAttribution(db).resolve(session, [row])[0].get("attribution", {}).get("caption")


@pytest.mark.parametrize("kind", ["user", "steer"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_native_navigation_keeps_index_wide_input_ambiguity_without_body_scan(store, tmp_path, monkeypatch, duplicate, kind):
    from amplifier_foundation.session.jsonl import TranscriptIndex
    from amplifier_web import automatic_history
    from amplifier_web.conversation_navigation import query
    from amplifier_web.browser_detail import MESSAGE_LIMIT
    peer, _ = retained(store, text=TEXT * 180)
    native = {"role": "user", "content": peer_input(peer["peerEnvelope"], peer["text"]),
              "metadata": {"amplifier_input": {"version": 1, "kind": kind, "id": "request"}}}
    rows = [native] + [{"role": "assistant", "content": "Saved " + str(i)} for i in range(130)]
    if duplicate:
        rows.append(copy.deepcopy(native))  # Outside the focused window.
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    before = path.read_bytes()
    monkeypatch.setattr(automatic_history, "directory", lambda _: tmp_path)
    session = {"id": "recipient", "nativeProject": "fixture", "historyManaged": True, "messages": [peer]}
    reads = []
    original_read = TranscriptIndex.read_positions
    def read(index, positions):
        positions = list(positions)
        reads.append(positions)
        return original_read(index, positions)
    monkeypatch.setattr(TranscriptIndex, "read_positions", read)
    index = query(session)
    assert reads == [] and "text" not in json.dumps(index)
    identity = index["turns"][0]["id"]
    focused = query(session, message_id=identity, window=True, _raw_window=True)
    assert reads == [list(range(MESSAGE_LIMIT))]
    native_row = focused["messages"][0]
    assert native_row["nativeInputId"] == "request" and native_row["text"] == native["content"]
    assert bool(native_row.get("nativeInputAmbiguous")) == duplicate
    assert app_caption(store, session, native_row) == (None if duplicate else AGENT)
    assert focused["offset"] == focused["sourceOffset"] == 0 and focused["total"] == len(rows)
    assert path.read_bytes() == before and session["messages"] == [peer]


async def test_focused_http_qualifies_raw_window_on_eventloop_and_filters_annotations(
        authenticated_client, tmp_path, monkeypatch):
    from amplifier_web.server import create_app
    from amplifier_web.browser_detail import TEXT_LIMIT, digest, read_text
    from amplifier_web import conversation_navigation
    runtime = NoModel()
    webapp = await create_app(tmp_path / "http", workspace=tmp_path, runtime=runtime,
                              voice=False, background_updates=False, preload_providers=False)
    service = webapp["service"]
    if service.history.task:
        service.history.task.cancel()
        await asyncio.gather(service.history.task, return_exceptions=True)
        service.history.task = None
    await service.event_log_view.close()
    client = await authenticated_client(webapp)
    long_text = TEXT * 180
    peer, _ = retained(service.db, recipient="recipient", text=long_text)
    session = service._new_session({"title": "Receiving"})
    human = {"id": "human", "role": "user", "inputId": "human-input", "text": long_text}
    session.update(id="recipient", historyLoaded=True, messages=[human, peer],
                   messageAnnotations={
                       "human": {"text": "forged", "inputId": peer["inputId"],
                                 "attribution": {"caption": AGENT}, "reactions": ["👍"]},
                       peer["id"]: {"text": "forged", "inputId": "forged",
                                    "attribution": None, "reactions": ["✅"]}})
    service.state["sessions"] = [session]
    service.state["selectedSessionId"] = session["id"]
    service._publish()
    original = copy.deepcopy(session)
    owner = threading.get_ident()
    queries, resolutions = [], []
    original_query, original_resolve = conversation_navigation.query, service.peer_attribution.resolve
    def query(*args, **kwargs):
        assert threading.get_ident() != owner
        queries.append(kwargs)
        return original_query(*args, **kwargs)
    def resolve_on_owner(target, rows):
        assert threading.get_ident() == owner
        resolutions.append([row["text"] for row in rows])
        return original_resolve(target, rows)
    monkeypatch.setattr(conversation_navigation, "query", query)
    monkeypatch.setattr(service.peer_attribution, "resolve", resolve_on_owner)
    endpoint = "/api/conversation/navigation"
    for params in ({"sessionId": "recipient"}, {"sessionId": "recipient", "messageId": peer["id"]}):
        response = await client.get(endpoint, params=params)
        assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
    assert resolutions == []  # Index/preview never perform a receipt lookup.
    response = await client.get(endpoint, params={"sessionId": "recipient", "messageId": peer["id"], "window": "true"})
    assert response.status == 200
    focused = await response.json()
    assert resolutions == [[long_text, long_text]]
    assert all(call.get("_raw_window") is True for call in queries)
    assert focused["messages"][0]["id"] == human["id"]
    assert "attribution" not in focused["messages"][0] and focused["messages"][0]["reactions"] == ["👍"]
    row = focused["messages"][1]
    assert row["id"] == peer["id"] and row["inputId"] == peer["inputId"] and row["role"] == "user"
    assert row["text"] == long_text[:TEXT_LIMIT] and row["reactions"] == ["✅"]
    assert row["attribution"] == {"caption": AGENT}
    assert row["textDetail"]["digest"] == digest(long_text)
    assert read_text(session, row["textDetail"])["value"] == long_text
    assert focused["offset"] == focused["sourceOffset"] == focused["userOffset"] == 0
    assert focused["total"] == 2 and focused["before"] is focused["after"] is None
    older = await client.get("/api/conversation/detail", params={"sessionId": "recipient", "part": "messages"})
    assert older.status == 200
    assert (await older.json())["items"][1]["attribution"] == {"caption": AGENT}
    assert session == original and runtime.starts == runtime.sends == 0


def test_genuine_human_exact_peer_wrapper_is_not_provenance(store):
    peer, _ = retained(store)
    human = {"id": "human-wrapper", "role": "user", "inputId": "human-wrapper-input",
             "text": peer_input(peer["peerEnvelope"], TEXT), "via": "chat"}
    result = resolve(store, human)[0]
    assert "attribution" not in result and result["text"] == human["text"]


def test_page_lookup_is_one_bound_in_query_not_per_message_or_history_scan(store):
    rows = [retained(store, identity="input-" + str(i))[0] for i in range(130)]
    queries = []
    store.set_trace_callback(queries.append)
    result = page({"id": "recipient", "messages": rows}, "messages", resolver=PeerAttribution(store).resolve)
    assert len(result["items"]) == 60
    assert all(row["attribution"]["caption"] == AGENT for row in result["items"])
    selects = [sql for sql in queries if sql.startswith("SELECT")]
    assert len(selects) == 1 and " IN (" in selects[0]
    assert "'input-0'" not in selects[0] and "'input-129'" in selects[0]


@pytest.mark.parametrize("mode", ["auto", "notify", "queue", "steer"])
async def test_dispatch_actor_does_not_come_from_caller_and_caption_is_not_delivery(app, mode):
    source, target = app.state["sessions"]
    if mode == "steer":
        app.runtime.collaboration_steer = AsyncMock(return_value={"accepted": True})
        await generation(app, target, "active", ["recipient-input"])
    elif mode == "queue":
        target["status"] = "working"
    receipt = await send(app, identity="actor-" + mode, mode=mode)
    assert receipt["origin"] == "agent"
    assert receipt["protocol"] == 2 and "grantId" not in receipt
    assert receipt["delivery"] in {"notified", "queued", "accepted"}
    result = app.peer_attribution.resolve(target, target["messages"])
    assert result[-1]["attribution"]["caption"] == AGENT
    assert "completed" not in result[-1]["attribution"]
    if mode == "queue":
        assert receipt["delivery"] == "queued" and not app.runtime.inputs
        await app.on_runtime_event("runtime.status", {"sessionId": target["id"], "status": "idle"})
        await settled(app, receipt["inputId"], "accepted")
        assert app.peer_attribution.resolve(target, target["messages"])[-1]["attribution"] == {"caption": AGENT}
    for key in ("origin", "attribution", "peerEnvelope"):
        with pytest.raises(Exception):
            await send(app, identity="forged-" + key, mode="notify", **{key: "agent"})
    forwarded = await app.dispatch("coordination.send", {
        "sessionId": target["id"], "senderSessionId": source["id"],
        "text": TEXT, "mode": "notify"}, origin="ui", command_id="human-forward")
    assert forwarded["origin"] == "ui"
    assert app.peer_attribution.resolve(target, target["messages"])[-1]["attribution"]["caption"] == NEUTRAL
    spoofed = await send(app, identity="forged-sender", mode="notify", senderSessionId="different")
    assert spoofed["senderSessionId"] == source["id"]
    assert target["messages"][-1]["peerEnvelope"]["senderSessionId"] == source["id"]
    with pytest.raises(AppError, match="impersonate"):
        await agent_action(app, source, "conversation.send",
            {"sessionId": target["id"], "senderSessionId": "different", "text": TEXT, "mode": "notify"}, "alias-spoof")


async def test_publication_invalidates_neutral_cache_and_revocation_keeps_provenance(app):
    source, target = app.state["sessions"]
    message, receipt = retained(app.db, identity="after-cache", sender=source["id"], recipient=target["id"])
    app.db.execute("DELETE FROM commands WHERE id=?", (receipt["inputId"],))
    target["messages"].append(message)
    app._publish_changes(sessions={target["id"]})
    cached = app.projections.detail(target)["messages"][-1]
    assert cached["id"] == message["id"] and "attribution" not in cached
    assert target["id"] in app.projections.detail_bodies
    app.db.execute("INSERT INTO commands VALUES(?,?,?)",
                   (receipt["inputId"], "command-fingerprint", json.dumps(receipt)))
    app.collaboration.publish_receipt(receipt)
    assert target["id"] not in app.projections.detail_bodies
    projected = app.projections.detail(target)["messages"][-1]
    assert projected["id"] == cached["id"] and projected["attribution"] == {"caption": AGENT}
    # Historical records stay readable; retired public mutation APIs are not used.
    historic = {"accepted": True, "commandAction": "coordination.grant",
                "result": {"id": "grant", "revoked": True, "revision": 2}}
    app.db.execute("INSERT INTO commands VALUES(?,?,?)", ("grant", "historical", json.dumps(historic)))
    source["title"] = "PRIVATE CHANGED TITLE"
    app._publish_changes(sessions={target["id"]})
    projected = app.projections.detail(target)["messages"][-1]
    assert projected["id"] == cached["id"] and projected["attribution"] == {"caption": AGENT}
    assert set(projected["attribution"]) == {"caption"} and message["text"] == TEXT


async def test_historical_continuation_attribution_uses_retained_claim_not_current_qualification(app):
    source, target = app.state["sessions"]
    source["status"] = "working"
    await declared_result(app)
    wait = await agent_action(app, source, "coordination.subscribe", {
        "sessionId": source["id"], "requestId": "result-request"}, "wait")
    await finish(app, target, "result-request")
    identity = wait["result"]["continuationId"]
    receipt = app.collaboration.receipt(identity)
    assert receipt["origin"] == "system"
    assert app.peer_attribution.resolve(source, source["messages"])[-1]["attribution"]["caption"] == SYSTEM
    request = app.collaboration.receipt("result-request")
    request["response"]["status"] = "staged"
    app.collaboration.save("result-request", request)
    # A later report/qualification change cannot turn a real saved host input
    # into something the human authored. No success claim comes from the caption.
    assert app.peer_attribution.resolve(source, source["messages"])[-1]["attribution"]["caption"] == SYSTEM
    request['subscription']['lastClaimedRevision'] = 0
    app.collaboration.save('result-request', request)
    assert 'attribution' not in app.peer_attribution.resolve(source, source['messages'])[-1]


class NoModel:
    """A receiving fixture must never mount, send, or start a worker."""
    def __init__(self):
        self.starts = self.sends = 0

    async def start(self, *args):
        self.starts += 1
        raise AssertionError("No model starts in an attribution fixture")

    async def send(self, *args):
        self.sends += 1
        raise AssertionError("No model sends in an attribution fixture")

    async def close(self):
        pass


async def test_two_services_colliding_ids_clients_close_and_restart_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    services = [AppService(tmp_path / name, NoModel(), workspace=tmp_path) for name in ("one", "two")]
    try:
        for service, actor in zip(services, ("agent", "ui")):
            row, _ = retained(service.db, actor=actor)
            session = service._new_session({"title": "Receiving"})
            session.update(id="recipient", messages=[row])
            service.state["sessions"] = [session]
            service.state["selectedSessionId"] = session["id"]
            service._publish()
        async def read(service):
            result = await service.app_bridge("history", {"action": "read", "session_id": "recipient"}, "recipient")
            return result["messages"][0]["attribution"]["caption"]
        assert await asyncio.gather(*(read(service) for service in services)) == [AGENT, NEUTRAL]
        for service, caption in zip(services, (AGENT, NEUTRAL)):
            for client_id in ("client-one", "client-two"):
                client = service.clients.attach(client_id)
                client["selectedSessionId"] = "recipient"
                with service.clients.bind(client_id):
                    visible = service.browser_state()
                row = next(row for row in visible["sessions"] if row["id"] == "recipient")
                assert row["messages"][0]["attribution"]["caption"] == caption
        await services[1].close()
        assert await read(services[0]) == AGENT
        await services[0].close()
        reopened = [AppService(tmp_path / name, NoModel(), workspace=tmp_path) for name in ("one", "two")]
        try:
            assert await asyncio.gather(*(read(service) for service in reopened)) == [AGENT, NEUTRAL]
            await reopened[0].close()
            assert await read(reopened[1]) == NEUTRAL
            assert all(service.runtime.starts == service.runtime.sends == 0 for service in reopened)
        finally:
            for service in reopened:
                if not service.closed:
                    await service.close()
    finally:
        for service in services:
            if not service.closed:
                await service.close()


@pytest.mark.parametrize('cold', [False, True])
@pytest.mark.parametrize('partial', [False, True])
async def test_saved_window_attribution_requires_complete_bounded_uniqueness(tmp_path, monkeypatch, cold, partial):
    from amplifier_web import cold_display
    from amplifier_web.resource_files import put
    service = AppService(tmp_path / 'app', NoModel(), workspace=tmp_path)
    try:
        row, _ = retained(service.db)
        # A duplicate outside the latest page must not become a unique local
        # match. Even its exact receipt cannot establish global uniqueness.
        messages = ([copy.deepcopy(row)] + [
            {'id': f'answer-{i}', 'role': 'assistant', 'text': 'Answer'}
            for i in range(101)] if partial else []) + [row]
        session = service._new_session({'title': 'Receiving'})
        session.update(id='recipient', messages=messages, historyManaged=False)
        if cold:
            reference = put(service.db, messages)
            session = cold_display.ColdRecord({**session, '_coldFields': {'messages': reference}}, service.db)
            dict.pop(session, 'messages')
        service.state['sessions'] = [session]
        monkeypatch.setattr(cold_display, 'load', lambda *args: (_ for _ in ()).throw(
            AssertionError('Attribution must not hydrate the full saved history')))
        await service.history.windows.load('recipient')
        projected = service.history.windows.project(session)
        shown = next(item for item in projected['messages'] if item['id'] == row['id'])
        assert shown['id'] == row['id'] and shown['text'] == TEXT
        assert ('attribution' in shown) is not partial
        if not partial:
            assert shown['attribution'] == {'caption': AGENT}
        else:
            assert projected['messageWindow']['hasEarlier']
        assert '_savedComplete' not in projected['messageWindow']
        assert service.runtime.starts == service.runtime.sends == 0
    finally:
        await service.close()


@pytest.mark.parametrize("options", [{}, {"minimal": True},
    {"scope": "range", "fromMessageId": "request-message", "throughMessageId": "request-message", "minimal": True}])
def test_full_range_minimal_export_safe_caption_and_original_markdown(store, tmp_path, options):
    row, _ = retained(store)
    session = {"id": "recipient", "messages": [row], "title": "Receiving"}
    original = copy.deepcopy(session)
    content, _ = snapshot(tmp_path, session, [], options, resolver=PeerAttribution(store).resolve)
    assert content.count(AGENT) == 1 and TEXT in content
    assert not any(secret in content for secret in ("private-source", "Private purpose", "private-reference", "grant"))
    assert session == original
    row["attribution"] = {"caption": AGENT}
    store.execute("DELETE FROM commands")
    assert AGENT not in snapshot(tmp_path, session, [], options)[0]


@pytest.mark.parametrize("kind", ["user", "steer"])
@pytest.mark.parametrize("cached_duplicate", [False, True])
async def test_checkpoint_reload_older_pages_query_export_keep_one_caption_and_native_bytes(app, tmp_path, monkeypatch, kind, cached_duplicate):
    from amplifier_web.session_files import project_slug
    source, target = app.state["sessions"]
    await send(app, identity="checkpoint-peer", mode="notify", text=TEXT)
    peer = target["messages"][-1]
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setattr("amplifier_web.automatic_history.directory", lambda session: root)
    monkeypatch.setattr("amplifier_web.conversation_export.directory", lambda session: root)
    native = [{"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
               "metadata": {"amplifier_input": {"version": 1, "kind": kind, "id": peer["inputId"]}}}]
    native += [{"role": "assistant", "content": "Saved reply " + str(i)} for i in range(125)]
    path = root / "transcript.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in native))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target.update(nativeProject=project_slug(tmp_path), nativeIdentity=target["id"], historyManaged=False)
    if cached_duplicate:
        # Earlier display readers discarded kind=steer's identity. Retain the
        # exact old display copy and append cursor, then reconstruct from native
        # metadata without rewriting either the native bytes or the receipt.
        cached = [display_message(row, index, target) for index, row in enumerate(native)]
        cached[0].pop("nativeInputId", None)
        target["messages"].extend(cached)
        target.update(nativeBoundary=cached[-1]["nativeIndex"], nativeBoundaryId=cached[-1]["id"])
    await app.history.load(target["id"])
    await app.history.load(target["id"])
    assert sum(row["id"] == peer["id"] for row in target["messages"]) == 1
    assert len(target["messages"]) == len(native)
    assert not any(row.get("text") == native[0]["content"] for row in target["messages"])
    older = page(target, "messages", before=target["messages"][-60]["id"], resolver=app.peer_attribution.resolve)
    # Load all earlier pages, not only the middle window.
    while older["before"]:
        older = page(target, "messages", before=older["before"], resolver=app.peer_attribution.resolve)
    assert next(row for row in older["items"] if row["id"] == peer["id"])["attribution"]["caption"] == AGENT
    query = await app.app_bridge("history", {"action": "read", "session_id": target["id"], "limit": 2}, source["id"])
    assert query["messages"][0]["attribution"]["caption"] == AGENT
    assert query["messages"][0]["text"] == TEXT
    export = await app.dispatch("session.export", {"id": target["id"], "format": "markdown", "destination": "none"})
    content = app.state_resource(export["result"]["content"]["$resource"])
    assert content.count(AGENT) == 1 and TEXT in content
    assert peer_input(peer["peerEnvelope"], TEXT) not in content
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("marker", [
    None, {}, {"version": 1, "kind": "service", "id": "request", "source": "notification"},
    {"version": 2, "kind": "steer", "id": "request"},
    {"version": 1, "kind": "steer", "id": ""},
    {"version": 1, "kind": "steer", "id": ["request"]},
])
def test_unproven_native_envelope_is_not_repaired_from_text(store, marker):
    peer, _ = retained(store)
    body = peer_input(peer["peerEnvelope"], TEXT)
    raw = {"role": "user", "content": body, "metadata": {"amplifier_input": marker}}
    native = display_message(raw, 0, {"id": "recipient"})
    assert "nativeInputId" not in native
    session = {"id": "recipient", "messages": [peer]}
    merge_web_history(session, [native])
    assert len(session["messages"]) == 2
    projected = PeerAttribution(store).resolve(session, session["messages"])
    assert projected[0]["attribution"]["caption"] == AGENT
    assert projected[1]["text"] == body and "attribution" not in projected[1]


@pytest.mark.parametrize("change", ["receipt", "binding", "target", "sender", "body", "identity", "duplicate"])
def test_native_steering_identity_does_not_replace_receipt_binding(store, change):
    peer, receipt = retained(store)
    native = display_message({"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
        "metadata": {"amplifier_input": {"version": 1, "kind": "steer", "id": "request"}}},
        0, {"id": "recipient"})
    session = {"id": "recipient", "messages": [peer]}
    if change == "receipt":
        store.execute("DELETE FROM commands")
    elif change == "binding":
        receipt["displayBinding"] = "wrong"
    elif change == "target":
        receipt["target"]["sessionId"] = "other"
    elif change == "sender":
        receipt["senderSessionId"] = "other"
    elif change == "body":
        native["text"] += "changed"
    elif change == "identity":
        native["nativeInputId"] = "other"
    else:
        session["messages"].append({**peer, "id": "duplicate"})
    store.execute("UPDATE commands SET receipt=? WHERE id='request'", (json.dumps(receipt),))
    assert "attribution" not in PeerAttribution(store).resolve(session, [native])[0]


def test_partial_native_steering_ambiguity_cannot_alias_or_remove_cached_copy(store):
    from amplifier_web.automatic_history import align_expanded_inputs, alias_peer_inputs
    peer, _ = retained(store)
    native = display_message({"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
        "metadata": {"amplifier_input": {"version": 1, "kind": "steer", "id": "request"}}},
        0, {"id": "recipient"})
    native["nativeInputAmbiguous"] = True  # Second occurrence outside this page.
    cached = {key: value for key, value in native.items() if key not in {"nativeInputId", "nativeInputAmbiguous"}}
    original = copy.deepcopy([peer, cached])
    session = {"id": "recipient", "messages": original}
    assert align_expanded_inputs(original, [native]) == original
    assert alias_peer_inputs(session, [native]) == [native]


@pytest.mark.parametrize("field,value", [
    ("id", "unrelated"), ("nativeIndex", 3), ("text", "Changed body"),
    ("role", "assistant"), ("source", "human"),
])
def test_cached_copy_repair_requires_every_native_anchor(store, field, value):
    from amplifier_web.automatic_history import align_expanded_inputs
    peer, _ = retained(store)
    native = display_message({"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
        "metadata": {"amplifier_input": {"version": 1, "kind": "steer", "id": "request"}}},
        0, {"id": "recipient"})
    cached = {key: item for key, item in native.items() if key != "nativeInputId"}
    cached[field] = value
    current = [peer, cached]
    before = copy.deepcopy(current)
    aligned = align_expanded_inputs(current, [native], peer_only=True)
    assert len(aligned) == 2 and aligned[-1] == cached
    assert current == before


@pytest.mark.parametrize("length", [3, 301])
def test_steering_projection_replaces_old_cache_and_reuses_warm_facts(store, tmp_path, monkeypatch, length):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web import automatic_history, conversation_navigation
    peer, _ = retained(store)
    raw = {"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
           "metadata": {"amplifier_input": {"version": 1, "kind": "steer", "id": "request"}}}
    rows = [raw] + [{"role": "assistant", "content": f"Answer {i}"} for i in range(length - 2)] + [raw]
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    before = path.read_bytes()
    session = {"id": "recipient", "nativeProject": "fixture", "historyManaged": True, "messages": [peer]}
    monkeypatch.setattr(automatic_history, "directory", lambda _: tmp_path)
    index = SessionHistoryStore(tmp_path).indexed_messages()
    # Seed precisely the old descriptor policy, not fake body contents. A new
    # reader policy must not trust its missing steering identity on a warm file.
    index.project("unified-display-v3:recipient", lambda row, i: (
        automatic_history.display_identity(session, i, row["role"], row["content"]),
        automatic_history.display_identity(session, i, row["role"], row["content"]), row["role"], None))
    index.project("unified-navigation-v3:recipient", lambda row, i: (
        automatic_history.display_identity(session, i, row["role"], row["content"]), row["role"], True, i, None))
    calls = []
    display = automatic_history.display_message
    def observed(*args, **kwargs):
        calls.append(args[1])
        return display(*args, **kwargs)
    monkeypatch.setattr(automatic_history, "display_message", observed)
    for warm in (False, True):
        calls.clear()
        page = automatic_history.read_transcript(session, limit=1)
        assert page["messages"][0]["nativeInputId"] == "request"
        assert page["messages"][0]["nativeInputAmbiguous"] is True
        if warm:
            assert len(calls) == 1  # Only the selected body, not N native bodies.
        calls.clear()
        facts, read = conversation_navigation.source(session)
        selected = read([0])
        assert selected[0]["nativeInputId"] == "request"
        assert selected[0]["nativeInputAmbiguous"] is True
        assert len(facts) == length
        if warm:
            assert len(calls) == 1
    assert path.read_bytes() == before


async def test_restart_reconstructs_cached_steering_copy_then_pages_original(store, tmp_path, monkeypatch):
    from amplifier_web import automatic_history
    root = tmp_path / "native"
    root.mkdir()
    monkeypatch.setattr(automatic_history, "directory", lambda _: root)
    app_path = tmp_path / "app"
    service = AppService(app_path, NoModel(), workspace=tmp_path)
    try:
        peer, receipt = retained(service.db)
        session = service._new_session({"title": "Receiving"})
        session.update(id="recipient", messages=[peer], nativeProject="fixture", historyManaged=False)
        raw = {"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
               "metadata": {"amplifier_input": {"version": 1, "kind": "steer", "id": "request"}}}
        native = display_message(raw, 0, session)
        session["messages"].append({key: value for key, value in native.items() if key != "nativeInputId"})
        session.update(nativeBoundary=0, nativeBoundaryId=native["id"])
        path = root / "transcript.jsonl"
        path.write_text(json.dumps(raw) + "\n")
        before = path.read_bytes()
        service.state["sessions"] = [session]
        service.state["selectedSessionId"] = "recipient"
        service._publish()
    finally:
        await service.close()
    reopened = AppService(app_path, NoModel(), workspace=tmp_path)
    try:
        # Supported passive repair action, even with an unchanged native stamp:
        # session.history -> history_page -> canonical load, not browser reload.
        # No migration guesses from the saved display-only copy.
        from test_collaborative_workspaces import until
        restored = reopened._session("recipient")
        restored["nativeRevision"] = automatic_history.revision(restored)
        assert len(restored["messages"]) == 2
        accepted = await reopened.dispatch("session.history", {"id": "recipient"}, command_id="repair-display")
        assert accepted["accepted"] is True
        await until(lambda: len(reopened._session("recipient")["messages"]) == 1)
        restored = reopened._session("recipient")
        assert len(restored["messages"]) == 1
        assert restored["messages"][0]["id"] == peer["id"]
        await reopened.history.windows.load("recipient")
        window = reopened.history.windows.project(restored)
        assert len(window["messages"]) == 1
        assert window["messages"][0]["attribution"]["caption"] == AGENT
        assert window["messages"][0]["text"] == TEXT
        assert json.loads(reopened.db.execute("SELECT receipt FROM commands WHERE id='request'").fetchone()[0]) == receipt
        assert path.read_bytes() == before
        assert reopened.runtime.starts == reopened.runtime.sends == 0
    finally:
        await reopened.close()


@pytest.mark.parametrize("kind", ["user", "steer"])
def test_passive_query_and_export_repair_old_display_copy_without_prior_merge(store, tmp_path, monkeypatch, kind):
    from amplifier_web import automatic_history
    from amplifier_web.history_query import _rows
    peer, _ = retained(store)
    raw = {"role": "user", "content": peer_input(peer["peerEnvelope"], TEXT),
           "metadata": {"amplifier_input": {"version": 1, "kind": kind, "id": "request"}}}
    native = display_message(raw, 0, {"id": "recipient"})
    cached = {key: value for key, value in native.items() if key != "nativeInputId"}
    # Preserve a separate human input whose entire body is the model envelope.
    human = {"id": "human", "role": "user", "inputId": "human", "text": raw["content"]}
    session = {"id": "recipient", "title": "Receiving", "nativeProject": "fixture",
               "historyManaged": False, "messages": [peer, cached, human]}
    original = copy.deepcopy(session)
    monkeypatch.setattr(automatic_history, "directory", lambda _: tmp_path)
    monkeypatch.setattr("amplifier_web.conversation_export.directory", lambda _: tmp_path)
    path = tmp_path / "transcript.jsonl"
    path.write_text(json.dumps(raw) + "\n")
    before = path.read_bytes()
    rows, _ = _rows(session)
    assert [row["id"] for row in rows] == [peer["id"], "human"]
    exported, _ = snapshot(tmp_path, session, [], {}, resolver=PeerAttribution(store).resolve)
    assert exported.count(AGENT) == 1
    assert exported.count(raw["content"]) == 1  # The human's literal copy survives.
    assert session == original and path.read_bytes() == before


async def test_ambiguous_steering_cannot_alias_a_retained_human_for_work(app, monkeypatch):
    source, _ = app.state["sessions"]
    retained_human = {"id": "human", "role": "user", "inputId": "request",
                      "inputOrigin": "user", "text": "Same"}
    source.update(nativeProject="fixture", messages=[retained_human])
    native = {"id": "native", "role": "user", "nativeInputId": "request",
              "nativeInputAmbiguous": True, "nativeIndex": 2, "text": "Same"}
    monkeypatch.setattr("amplifier_web.automatic_history.read_transcript", lambda *args, **kwargs: {"messages": [native]})
    with pytest.raises(AppError, match="unambiguously"):
        app.collaboration.resolve_message(source, "native", strict_source=True)


@pytest.mark.parametrize("kind", ["user", "steer"])
@pytest.mark.parametrize("attachment", [False, True])
def test_peer_repair_does_not_change_nonpeer_expanded_export_policy(tmp_path, monkeypatch, kind, attachment):
    from amplifier_web import automatic_history
    from amplifier_web.automatic_history import align_expanded_inputs
    visible = {"id": "human", "role": "user", "inputId": "request", "text": "Read @note.md"}
    content = "Read @note.md\n\nExpanded mention content"
    if attachment:
        visible["attachments"] = [{"name": "note.md", "id": "fixture"}]
        content = [{"type": "text", "text": visible["text"]},
                   {"type": "text", "text": "Expanded attachment content"}]
    raw = {"role": "user", "content": content,
           "metadata": {"amplifier_input": {"version": 1, "kind": kind, "id": "request"}}}
    session = {"id": "recipient", "title": "Receiving", "nativeProject": "fixture",
               "historyManaged": False, "messages": [visible]}
    native = display_message(raw, 0, session)
    session["messages"].append({key: value for key, value in native.items() if key != "nativeInputId"})
    original = copy.deepcopy(session)
    assert align_expanded_inputs(session["messages"], [native], peer_only=True) == session["messages"]
    monkeypatch.setattr(automatic_history, "directory", lambda _: tmp_path)
    monkeypatch.setattr("amplifier_web.conversation_export.directory", lambda _: tmp_path)
    (tmp_path / "transcript.jsonl").write_text(json.dumps(raw) + "\n")
    exported, _ = snapshot(tmp_path, session, [], {})
    assert "Read @note.md" in exported
    assert session == original

@pytest.mark.parametrize("identity", [{"bad": "request"}, ["request"], 1, True, "x" * 201])
@pytest.mark.parametrize("peer", [False, True])
def test_malformed_identity_survives_passive_history_and_export(store, tmp_path, monkeypatch, identity, peer):
    from amplifier_web.automatic_history import alias_peer_inputs, align_expanded_inputs
    from amplifier_web.history_query import _rows
    row, _ = retained(store)
    native_value = {"role": "user", "content": peer_input(row["peerEnvelope"], TEXT),
                    "metadata": {"amplifier_input": {"version": 1, "kind": "user", "id": "request"}}}
    session = {"id": "recipient", "title": "Receiving", "nativeProject": "fixture", "messages": [row]}
    native = display_message(native_value, 0, session)
    row["inputId"] = identity
    if not peer:
        row.pop("peerEnvelope")
    original = copy.deepcopy(session)
    monkeypatch.setattr("amplifier_web.automatic_history.directory", lambda row: tmp_path)
    monkeypatch.setattr("amplifier_web.conversation_export.directory", lambda row: tmp_path)
    (tmp_path / "transcript.jsonl").write_text(json.dumps(native_value) + "\n")
    assert alias_peer_inputs(session, [native]) == [native]
    assert align_expanded_inputs([row], [native]) == [row]
    read, _ = _rows(session)
    assert any(item["id"] == row["id"] for item in read)
    assert all("attribution" not in item for item in PeerAttribution(store).resolve(session, read))
    exported, _ = snapshot(tmp_path, session, [], {}, resolver=PeerAttribution(store).resolve)
    assert TEXT in exported and AGENT not in exported
    assert session == original


@pytest.mark.parametrize('origin,via', [('agent', 'chat'), ('peer', 'peer'), ('scheduler', 'schedule')])
async def test_nonhuman_input_cannot_be_edited_into_a_user_message(tmp_path, origin, via):
    service = AppService(tmp_path, workspace=tmp_path)
    try:
        await service.dispatch('session.create', {})
        session = service._session()
        message = service._message(session, 'user', 'Original input', via, inputOrigin=origin)
        before = copy.deepcopy(session['messages'])
        with pytest.raises(AppError, match='own messages'):
            await service.dispatch('message.edit', {'sessionId': session['id'],
                'messageId': message['id'], 'text': 'Replacement', 'mode': 'current'})
        assert session['messages'] == before
    finally:
        await service.close()


@pytest.mark.parametrize('kind,outcome', [('question','question'),('defer','deferred'),('result','failed'),('decline','declined'),('conflict','conflict')])
async def test_negative_report_notifications_are_host_attributed_before_and_after_sealing(app, kind, outcome):
    source, peer = app.state['sessions']
    source['status'] = 'working'
    await declared_result(app, kind=kind, outcome=outcome)
    await agent_action(app, source, 'coordination.subscribe', {'sessionId':source['id'],'requestId':'result-request'}, 'negative-wait')
    row = source['messages'][-1]
    assert app_caption(app.db, source, row) == SYSTEM
    await finish(app, peer, 'result-request')
    assert app_caption(app.db, source, row) == SYSTEM
    assert row['inputOrigin'] == 'peer' and row['role'] == 'user'
    native = display_message({"role": "user", "content": peer_input(row["peerEnvelope"], row["text"]),
        "metadata": {"amplifier_input": {"version": 1, "kind": "steer", "id": row["inputId"]}}},
        0, source)
    assert app_caption(app.db, source, native) == SYSTEM


async def test_later_reply_and_old_success_keep_exact_independent_display_claims(app):
    source, peer = app.state['sessions']
    source['status'] = 'working'
    await declared_result(app)
    await agent_action(app, source, 'coordination.subscribe', {'sessionId':source['id'],'requestId':'result-request'}, 'revision-wait')
    await finish(app, peer, 'result-request')
    first = source['messages'][-1]
    await generation(app, peer, 'later-generation', ['follow-up'])
    await agent_action(app, peer, 'coordination.reply', {'requestId':'result-request', 'expectedResponseRevision':1,
        'kind':'conflict','outcome':'conflict','text':'Later independent report'}, 'later-report')
    second = source['messages'][-1]
    assert first['inputId'] != second['inputId']
    assert [row.get('attribution', {}).get('caption') for row in app.peer_attribution.resolve(source, [first,second])] == [SYSTEM,SYSTEM]
    # Read only the older message, with no scan/open of the peer history.
    assert app_caption(app.db, source, first) == SYSTEM


@pytest.mark.parametrize('outcome', ['idle','expired','creation_failure'])
async def test_watch_and_same_root_preparation_failures_have_verified_system_attribution(app, monkeypatch, outcome):
    source, peer = app.state['sessions']
    source['status'] = 'working'
    if outcome == 'creation_failure':
        from test_peer_creation_operation import plan
        plan(app, source)
        async def unavailable(*args):
            raise RuntimeError('Fixture preparation failure')
        monkeypatch.setattr(app.collaboration, '_finish_create', unavailable)
        saved = await agent_action(app, source, 'coordination.create', {'title':'Unavailable helper','text':'Inspect dependency'}, 'host-notice')
        from test_collaborative_workspaces import until
        await until(lambda: app.collaboration.receipt('host-notice')['subscription']['status'] == 'claimed')
    else:
        saved = await agent_action(app, source, 'coordination.watch', {'sessionId':source['id'],'targetSessionId':peer['id'],
            'condition':'idle' if outcome=='idle' else 'status_changed','expiresInSeconds':60}, 'host-notice')
        if outcome == 'expired':
            saved['watch']['expiresAt'] = 0
            app.collaboration.save('host-notice', saved)
            await app.collaboration.watches.expire('host-notice')
    row = source['messages'][-1]
    assert row['inputOrigin'] == 'peer'
    assert app_caption(app.db, source, row) == SYSTEM
    if outcome == 'creation_failure':
        assert row['peerEnvelope']['senderSessionId'] == source['id']


@pytest.mark.parametrize('change', ['claim','revision','snapshot','dependency','producer','request','satisfied'])
async def test_versioned_notification_cannot_get_caption_without_exact_retained_claim(app, change):
    source, peer = app.state['sessions']
    source['status'] = 'working'
    observed = await agent_action(app, source, 'coordination.watch', {'sessionId':source['id'],'targetSessionId':peer['id'],
        'condition':'idle','expiresInSeconds':60}, 'exact-watch')
    row = source['messages'][-1]
    notice = app.collaboration.receipt(row['inputId'])
    assert app_caption(app.db, source, row) == SYSTEM
    if change == 'claim': observed['subscription']['lastClaimedRevision'] = 0
    elif change == 'revision': notice['dependencyOutcomeRevision'] = 2
    elif change == 'snapshot': notice['outcomeSnapshot'].pop('outcomeRevision')
    elif change == 'dependency': notice['dependencyRequestId'] = 'missing'
    elif change == 'producer': observed['target']['sessionId'] = 'unrelated'
    elif change == 'request': observed['requestId'] = 'unrelated'
    else: notice['dependencySatisfied'] = True
    app.collaboration.save('exact-watch', observed)
    app.collaboration.save(row['inputId'], notice)
    assert app_caption(app.db, source, row) is None


@pytest.mark.parametrize('change', [
    'none', 'input', 'recipient', 'generation', 'automatic', 'origin', 'mode',
    'dependency', 'guard', 'admission', 'accepted', 'unknown', 'start', 'send', 'submit',
])
async def test_receiving_browser_fixture_only_waits_for_exact_watch_boundary(app, monkeypatch, change):
    from types import SimpleNamespace
    from fixtures.peer_attribution_ui_server import NoModel as ReceivingOnly
    from amplifier_web import collaboration_input
    from test_collaborative_workspaces import until

    runtime = ReceivingOnly()
    app.runtime = runtime
    observer, peer = app.state['sessions']
    await generation(app, observer, 'fixture-observer-generation', ['observer-input'])
    await generation(app, peer, 'fixture-source-generation', ['source-input'])
    watch = await agent_action(app, observer, 'coordination.watch', {
        'sessionId': observer['id'], 'targetSessionId': peer['id'],
        'condition': 'status_changed', 'expiresInSeconds': 60}, 'visible-watch')
    runtime.bind_watch(app, watch, observer)
    expected = copy.deepcopy(runtime.expected)
    original = runtime.collaboration_input

    if change == 'admission':
        monkeypatch.setattr(app.collaboration, 'admission', lambda *args: {'admitted': False})
    elif change in {'accepted', 'unknown'}:
        async def wrong_outcome(controls, native, args, activation, authorize, **kwargs):
            await authorize(args)
            await authorize(args)
            return {'accepted': True} if change == 'accepted' else {'reason': 'Outcome unknown'}
        monkeypatch.setattr(collaboration_input, 'deliver', wrong_outcome)

    async def attempt(session, args, guard, emit):
        if change == 'input': args = {**args, 'inputId': 'unexpected-input'}
        elif change == 'recipient': session = {**session, 'id': 'unexpected-recipient'}
        elif change == 'generation':
            session = {**session, 'collaborationGeneration': {'id': 'unexpected-generation'}}
        elif change == 'automatic': args = {**args, 'automaticDelivery': False}
        elif change in {'origin', 'mode', 'dependency'}:
            receipt = app.collaboration.receipt(args['inputId'])
            key, value = {'origin': ('origin', 'agent'), 'mode': ('mode', 'queue'),
                          'dependency': ('dependencyRequestId', 'unexpected-watch')}[change]
            receipt[key] = value
            app.collaboration.save(args['inputId'], receipt)
        elif change == 'guard': guard = lambda: 'Synthetic guard refusal'
        elif change == 'start': return await runtime.start(session, emit)
        elif change == 'send': return await runtime.send(session, 'Forbidden', args['inputId'], emit)
        elif change == 'submit': return await runtime.submit(SimpleNamespace(id=args['inputId']))
        return await original(session, args, guard, emit)
    monkeypatch.setattr(runtime, 'collaboration_input', attempt)
    await app.on_runtime_event('runtime.status', {'sessionId': peer['id'], 'status': 'idle'})
    identity = expected['inputId']
    await until(lambda: bool(app.collaboration.receipt(identity).get('admission')))
    receipt = app.collaboration.receipt(identity)
    assert runtime.expected == expected
    assert runtime.calls
    if change == 'none':
        assert receipt['delivery'] == 'queued'
        assert receipt['admission'] == {'accepted': False, 'effect': 'none',
            'waitingForBoundary': True, 'reason': 'Waiting for a supported recipient boundary.'}
        assert receipt['dependencyRequestId'] == watch['requestId']
        assert receipt['dependencySatisfied'] is False
        for call in runtime.calls:
            assert call['method'] == 'collaboration_input' and 'fatal' not in call
            assert call['arguments']['inputId'] == identity
            assert call['result'] == receipt['admission']
            assert call['authorizations'] == [{'guardReason': None, 'admitted': True,
                'currentGenerationId': expected['generationId']}] * 2
    else:
        assert receipt['delivery'] == 'unknown'
        assert receipt['admission'] == {'reason': 'Admission outcome unknown; no replay.'}
        assert any(call.get('fatal') for call in runtime.calls)
    assert runtime.starts == int(change == 'start')
    assert runtime.sends == int(change == 'send')
    assert runtime.native_submits == int(change == 'submit')
    assert observer['collaborationGeneration']['id'] == expected['generationId']
    assert identity not in observer['collaborationGeneration'].get('inputIds', [])
