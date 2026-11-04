"""Durable app state and the shared UI/agent command surface."""
from __future__ import annotations

import asyncio
import copy
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import socket
import time
import uuid
from weakref import WeakValueDictionary

from jsonschema import validate, ValidationError
import tinycss2
from .errors import AppError
from .execution import ensure_turn, ingest as ingest_execution, finish as finish_execution, finish_background, interrupt_unfinished, restore_voice_usage
from .updates import CHECK_INTERVAL_HOURS, DEFAULT_CHECK_INTERVAL_HOURS, work_paused
from .managed_chats import LOCATION
from .attachments import MAX_ENCODED_BYTES
from .performance_diagnostics import PerformanceDiagnostics, measure, measured, TimedLock
from .action_schema import schema, string


def settle_stream(session):
    """Preserve interrupted display text without claiming a completed response."""
    text = session.pop('streaming', None)
    identity = session.pop('streamingId', None)
    input_id = session.pop('streamingInputId', None)
    if text:
        session.setdefault('messages', []).append({
            'id': str(uuid.uuid4()), 'role': 'assistant', 'text': '[Interrupted response]\n\n' + text, 'via': 'call' if str(input_id or '').startswith('voice:') else 'chat',
            'source': 'interrupted-stream', 'partial': True, 'createdAt': time.time(),
            **({'streamId': identity} if identity else {}),
            **({'inputId': input_id} if input_id else {}),
        })


def command_fingerprint(action, args, origin, client_id, *, caller_session_id=None):
    # One ledger identity for admission and passive receipt retrieval.
    values = [action, args, origin]
    if client_id is not None and action not in {'conversation.send', 'worker.message'} and not action.startswith('question.') and not (action=='session.create' and args.get('fromDraft')):
        values.append(client_id)
    # Later-file receipts belong to their conversation even when no browser is
    # attached. This is a stable replay identity, not a fresh permission check.
    if (origin in {'agent', 'system'} and caller_session_id and
            (action.startswith('feedback.attachments.') or
             action in {'feedback.attachment.add', 'feedback.attachment.remove'} and args.get('feedbackId'))):
        values.append({'sourceSessionId': caller_session_id})
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


ACTION_DEFINITIONS = {
    "desktop.readiness": ("Inspect the serving host's native observation preflight and already-mounted conversation tools. No activation, capture, permission prompt, worker startup or provider change. Browser account/tab context is reported only when the owning transport exposes it.", schema({"sessionId": string(200)}, [])),
    "runtime.dependencies": ("Inspect exact host and already-running session runtime paths, public artifact package versions, and optional rendering tools. Set verifyImports to probe the six public authoring libraries in isolated child processes. Does not install dependencies, start a session, select a chat or prove rendering success.", schema({"sessionId": string(200), "verifyImports": {"type": "boolean"}}, [])),
    "terminal.prepare": ("Prepare a private, short-lived terminal setup download for a configured service address. Installs on the computer where the user runs it; does not install on the server.", schema({"server": string(500), "platform": {"enum": ["macos-arm64", "linux-arm64"]}, "name": string(80)}, ["server", "platform", "name"])),
    "terminal.devices": ("List enrolled terminal connections without revealing credentials.", schema({})),
    "terminal.revoke": ("Remove one terminal connection's access; accepted work continues on the host.", schema({"id": string(100)}, ["id"])),
    "diagnostics.configure": ("Configure local Context Intelligence capture and explicitly enabled per-server stream routes. API keys are environment references. Changing a destination cancels its queued deliveries; already accepted or in-flight data cannot be recalled.", schema({"config":{"type":"object"},"patch":{"type":"object"}}, [])),
    "diagnostics.test": ("Test saved destination authentication and write access by sending one synthetic probe; no conversation content.", schema({"id":string(100)})),
    "diagnostics.environment": ("Check that a credential environment variable exists in the service without revealing it.",schema({"name":string(200)})),
    "diagnostics.retry": ("Retry failed deliveries still authorized by this destination's current policy; never replay historical unselected data.",schema({"id":string(100)})),
    "diagnostics.records": ("Read retained diagnostics by session and stream glob, newest first. Continue using nextBefore. Metadata is the default; conversation text is captured only if explicitly enabled.",schema({"sessionId":string(200),"stream":string(100),"before":{"type":"integer","minimum":1},"limit":{"type":"integer","minimum":1,"maximum":100}},[])),
    "diagnostics.export": ("Download the explicitly inspected page of local Context Intelligence records as JSONL, including only those visible records.",schema()),
    "workspace.add": ("Register an existing workspace folder and use it for new chats", schema({"path":string(4000),"name":string(200),"fromDraft":{"type":"boolean"}},["path"])),
    "workspace.list": ("List available workspace folders, including empty workspaces, with bounded search. Saved chats come from native history.", schema({"query":string(500),"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":100}},[])),
    "workspace.prepare": ("Resolve a name and snapshot an optional starter without creating files or chats. Inspect disposition and exact starter before creating.", schema({"name":string(200),"root":string(4000),"starterId":string(100)},["name"])),
    "workspace.create": ("Create the reviewed workspace plan with a stable command ID. fromDraft preserves the unsent chat and selects the result. Legacy explicit path remains supported.", schema({"planId":string(100),"path":{**string(4000),"minLength":1},"name":string(200),"fromDraft":{"type":"boolean"}},[])),
    "workspace.select": ("Select the workspace used for new chats and canvas files", schema({"id":string(100)})),
    "workspace.archive": ("Archive a workspace without deleting files, stopping work or changing chat history.", schema({"id":string(100)})),
    "workspace.restore": ("Restore an archived workspace to active navigation.", schema({"id":string(100)})),
    "workspace.rename": ("Rename a workspace registration", schema({"id":string(100),"name":string(200)})),
    "workspace.remove": ("Remove a workspace registration without deleting folders or chats", schema({"id":string(100)})),
    "workspace.pin": ("Pin or unpin a registered workspace, including one with no chats. A passive ordered app preference, independent of chat pins, selection and recency. Unavailable registrations retain their pins; explicit removal prunes them.", schema({"id": {**string(100), "minLength": 1}, "pinned": {"type": "boolean"}}, ["id", "pinned"])),
    "workspace.pinOrder": ("Reorder every currently pinned workspace ID exactly once, including unavailable folders. Read the complete pinnedWorkspaceIds vector from shell.query; no selection or execution.", schema({"ids": {"type": "array", "maxItems": 10000, "uniqueItems": True, "items": {**string(100), "minLength": 1}}})),
    "workspace.starters.list": ("List built-in and custom workspace starters. Reading never creates files or changes existing workspaces.", schema()),
    "workspace.starters.save": ("Create or revise a custom starter. Built-ins are immutable; updates need the observed starter revision. Changes affect future plans only.", schema({"id":string(100),"expectedRevision":{"type":"integer","minimum":1},"starter":{"type":"object"}},["starter"])),
    "workspace.starters.duplicate": ("Copy a starter into an independent custom definition without creating a workspace.", schema({"id":string(100),"name":string(200)},["id"])),
    "workspace.starters.remove": ("Delete only a custom starter definition, never its created workspaces or files.", schema({"id":string(100),"expectedRevision":{"type":"integer","minimum":1}})),
    "workspace.setup.inspect": ("Inspect retained setup status, exact repository revisions, preserved instruction files and failed imports. Does not retry work.", schema({"workspaceId":string(100)})),
    "workspace.setup.retry": ("Explicitly retry only unfinished setup steps at the observed receipt revision. Completed repositories are untouched; interrupted unknown imports require inspection.", schema({"workspaceId":string(100),"expectedRevision":{"type":"integer","minimum":1}})),
    "workspace.setup.reconcile": ("Inspect retained unknown imports with local read-only Git checks and record their observed qualification. Does not fetch, clone, reset or delete files.", schema({"workspaceId":string(100)})),
    "workspace.resources.list": ("Read retained external resource inventory; recording is not teardown.", schema({"workspaceId":string(100)})),
    "workspace.resources.add": ("Record an external resource and its owner. Does not create or delete infrastructure.", schema({"workspaceId":string(100),"kind":string(1000),"resourceId":string(1000),"owner":string(1000),"note":string(1000)},["workspaceId","kind","resourceId","owner"])),
    "workspace.resources.update": ("Record observed resource status with evidence at an exact revision. Never performs teardown or treats observation as ownership.", schema({"workspaceId":string(100),"id":string(100),"expectedRevision":{"type":"integer","minimum":1},"status":{"enum":["active","reaped","observed_absent"]},"evidence":string(4000)})),
    "canvas.show": ("Save a durable artifact and choose one full presentation: canvas (default), inline, or saved (compact card only). Inline needs an attached client and an originating turn, and preserves unrelated Canvas work. Direct content creates an artifact; a canonical file reuses its tab and versions changes. Formats: sandboxed HTML, Markdown, Mermaid, DOT, structured data, text, code, image, PDF workspace files (20 MB, read-only original download), auto-detected file, Babylon.js HTML, A2UI, browser URL, or live Settings. PDF/browser/A2UI/Settings open in Canvas, not inline. HTML/Babylon files support 20 MB; embedded data/blob media only, no remote media. Browser embedding can fail; inspect render reports. A2UI supports Text, Row, Column, Card, Button and Divider. Saving is not render verification.", schema({"kind":{"enum":["auto","text","markdown","code","html","mermaid","dot","json","jsonl","image","pdf","a2ui","browser","babylon","settings"]},"title":string(200),"content":string(7000000),"path":string(4000),"url":string(4000),"sessionId":string(200),"clientId":string(100),"surface":{"type":"object"},"presentation":{"enum":["canvas","inline","saved"],"default":"canvas"}},["kind"])),
    "canvas.view": ("Adjust shared canvas viewer controls", schema({"id":string(100),"patch":schema({"source":{"type":"boolean"},"help":{"type":"boolean"},"reload":{"type":"number","minimum":0},"zoom":{"type":"number","minimum":0.2,"maximum":4},"panX":{"type":"number","minimum":-10000,"maximum":10000},"panY":{"type":"number","minimum":-10000,"maximum":10000},"engine":{"enum":["dot","neato","fdp","sfdp","circo","twopi"]},"node":string(500),"query":string(500)}, [])})),
    "canvas.report": ("Report browser rendering success or failure for a canvas part; this is display evidence only", schema({"id":string(100),"part":string(100),"status":{"enum":["pending","ready","unverified","error"]},"message":string(2000)},["id","part","status"])),
    "canvas.snapshot": ("Report visible HTML preview text and standard controls as untrusted display data", schema({"id":string(100),"document":schema({"text":string(16000),"controls":{"type":"array","maxItems":100,"items":schema({"id":string(100),"tag":string(30),"type":string(30),"label":string(200),"value":string(4000),"disabled":{"type":"boolean"}},["id","tag","type","label","value","disabled"])}})})),
    "canvas.interact": ("Operate a standard HTML preview control from the current canvas.document snapshot. Never executes arbitrary JavaScript.", schema({"id":string(100),"controlId":string(100),"event":{"enum":["click","input"]},"value":string(4000)},["id","controlId","event"])),
    "canvas.copyPath": ("Copy the selected artifact file path on the app server, absolute or relative to its original workspace; never opens a file or submits a message.", schema({"id":string(100),"format":{"enum":["absolute","relative"]}},["id","format"])),
    "canvas.copy": ("Copy the current canvas source to the browser clipboard", schema({"id":string(100)})),
    "canvas.openAttachment": ("Open a sent text attachment from the currently displayed conversation in Canvas; never submits or replays work.", schema({"sessionId":string(200),"id":string(32),"clientId":string(200)}, ["sessionId","id"])),
    "canvas.openGif": ("Open an exact retained assistant GIF in the originating browser client's Canvas, only on an explicit UI click. Does not send, generate, or attach anything.", schema({"sessionId":string(200),"messageId":string(200),"id":string(100),"version":{"type":"integer","minimum":1},"sha256":{"type":"string","pattern":"^[a-f0-9]{64}$"},"clientId":string(200)}, ["sessionId","messageId","id","version","sha256"])),
    "canvas.openFile": ("Open a local file in Canvas for the selected chat and its unchanged workspace. Exact artifact references verify reported bytes in the recorded execution folder. Optional showConversation opens the named chat from that workspace's Files page atomically with a successful preview; stale pages and unavailable files do not navigate. Never submits a message.", schema({"sessionId":string(200),"workspace":string(4000),"path":string(4000),"clientId":string(200),"showConversation":{"type":"boolean"},"referenceRequestId":string(200),"referenceResponseRevision":{"type":"integer","minimum":1},"expectedSha256":{"type":"string","pattern":"^[a-f0-9]{64}$"}}, ["sessionId","workspace","path"])),
    "canvas.download": ("Download the current canvas source", schema({"id":string(100)})),
    "canvas.openExternal": ("Open the active browser preview URL in a browser tab; popup permissions may apply", schema({"id":string(100)})),
    "canvas.select": ("Present an existing artifact in canvas (default), inline, or saved (compact card only), without creating content or replaying work. A source-bound presentation requires an exact version and either publication messageId or questionId plus immutable questionContentRevision, never both. Inline requires one of these bindings. Omit version for Latest in Canvas; exact versions are read-only. Preserves unrelated Canvas and drafts; dirty transitions refuse. Moving a mounted HTML/3D preview requires an explicit UI resetPreview choice, never an agent reset. Does not force full-frame focus. Supply clientId when needed.", schema({"id":string(100),"clientId":string(100),"sessionId":string(200),"version":{"type":"integer","minimum":1},"messageId":string(200),"questionId":string(200),"questionContentRevision":{"type":"integer","minimum":1},"presentation":{"enum":["canvas","inline","saved"],"default":"canvas"},"resetPreview":{"type":"boolean"}}, ["id"])),
    "canvas.visibility": ("Show or hide this client's retained Canvas viewer without discarding edits. Bind sessionId and canvasId from the current state; optionally address an attached clientId.", schema({"open":{"type":"boolean"},"sessionId":{"type":["string","null"]},"canvasId":{"type":["string","null"]},"clientId":string(200)},["open","sessionId","canvasId"])),
    "canvas.reopen": ("Show this chat's canvas and saved artifacts", schema()),
    "canvas.tabClose": ("Close a canvas tab; keep the artifact in chat history", schema({"id":string(100)})),
    "canvas.close": ("Close the canvas without losing its content", schema()),
    "canvas.event": ("Record an A2UI button interaction in shared agent-visible state", schema({"surfaceId":string(100),"componentId":string(100),"name":string(200),"value":{}},["surfaceId","componentId","name"])),
    "session.draft": ("Open configurable new-chat setup without creating a conversation or starting work. Optional workspaceId binds an explicit workspace path to an available registration. Edit view.newSessionDraft. The user must submit the first message to create this chat; an agent cannot finish this through raw session.create. For an authorized task in the caller's inherited workspace, use coordination.create instead. For an authorized independent chat and opening brief in another existing folder, including unattended Full access, use session.handoff.start without a second confirmation; use session.handoff.propose for suggestions.", schema({"workspace": string(4000), "workspaceId": {**string(100), "minLength": 1}, "location": LOCATION}, [])),
    "session.create": ("Create a conversation through the human UI or host-managed creation flow. Direct agent calls are refused, including after session.draft; coordination.create commissions a task in the caller's inherited workspace, not an independent chat in another folder. session.handoff.start creates and delivers an independent chat directly under Full access or an explicit user request; session.handoff.propose reserves human review for suggestions; session.handoff.read inspects its durable outcome without replay. location.kind managed allocates a private app-owned folder (not a security sandbox); workspace uses an existing or explicitly supplied new folder. Reviewed inheritance does not copy history, tasks or running work; select:false preserves the view. purpose creates (or reuses an idle matching) internal session omitted from chat lists, never selected, e.g. terminal-tool. expectedDraftId guards the originating private unnamed draft during fromDraft creation.", schema({"id": string(100), "location": LOCATION, "title": string(200), "purpose": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]{0,79}$"}, "bundle": string(2000), "workspace": string(4000), "select": {"type": "boolean"}, "fromDraft": {"type": "boolean"}, "expectedDraftId": {"type": "string", "pattern": "^[a-f0-9]{32}$"}, "selection": {"type": "object", "properties": {"instance": string(200), "model": string(500), "effort": string(100)}, "additionalProperties": False}, "inheritConfiguration": schema({"sessionId": string(200), "configurationHash": string(100), "scheduledRunId": string(200)}, ["sessionId", "configurationHash"])}, [])),
    "session.plans": ("Read current plan progress and recorded trajectory for up to 50 chats without selecting them. Include items for at most one chat to inspect its todo list.", schema({"ids":{"type":"array","minItems":1,"maxItems":50,"uniqueItems":True,"items":string(200)},"includeItems":{"type":"boolean"}},["ids"])),
    "session.select": ("Select a conversation without preparing a runtime. A saved chatReference proof is UI-only and revalidated before the ordinary navigation guards.", schema({"id": string(100), "chatReference": schema({
        "id": string(100), "version": {"type": "integer", "minimum": 1},
        "sourceSessionId": string(200), "messageId": string(200),
        "expectedRevision": {"type": "integer", "minimum": 1}},
        ["id", "version", "sourceSessionId", "messageId", "expectedRevision"]),
        "peerPreview": schema({"proof": string(4096), "sourceSessionId": string(200)}, ["proof", "sourceSessionId"])}, ["id"])),
    "peer.preview": ("Inspect a receipt-bound source in a read-only Canvas preview without selecting or starting it.", schema({
        "proof": string(4096), "sourceSessionId": string(200)}, ["proof", "sourceSessionId"])),
    "session.warm": ("Prepare a conversation in the background without sending input or requesting takeover", schema({"id": string(200)})),
    "runtime.retention.update": ("Set this host's idle worker count and lifetime", schema({"patch": {
        "type": "object", "properties": {
            "max_warm_workers": {"type": "integer", "minimum": 0},
            "idle_timeout_hours": {"type": "number", "minimum": 0}}, "additionalProperties": False}}, ["patch"])),
    "session.takeover": ("Explicitly request execution ownership here; the current owner saves and releases automatically.", schema({"id": string(200)})),
    "session.rename": ("Rename a conversation", schema({"id": string(100), "title": string(200)})),
    "session.naming.backfill": ("Name existing chats that still have a fallback or placeholder title and Auto naming on, without starting a conversation worker or replaying work: one naming call per chat with the default provider, at most two at a time. Manual and generated names, Auto=false, busy chats and worker/internal sessions are skipped. Omit ids to take the most recent eligible chats up to limit (default 20, max 200). Progress is each chat's naming.status; the summary (named, skipped and failed with reasons) is /namingBackfill.", schema({"ids": {"type": "array", "items": string(100), "maxItems": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, [])),
    "session.naming": ("Enable or disable future automatic naming, or generate a name once without sending a chat turn. Regeneration preserves the Auto preference and rejects late results after a newer edit.", schema({"id": string(100), "automatic": {"type": "boolean"}, "regenerate": {"const": True}}, ["id"])),
    "session.pin": ("Pin or unpin a top-level chat in workspace and All chats lists. This app preference does not change shared conversation files.", schema({"id": {**string(200), "minLength": 1}, "pinned": {"type": "boolean"}}, ["id", "pinned"])),
    "session.deletePreview": ("Review permanent deletion of an idle managed chat and its owned files/history. Show the returned scope to the user before confirmation. Workspace chats can only be archived.", schema({"id": string(100)})),
    "session.delete": ("Permanently delete the managed chat reviewed by session.deletePreview, only after explicit user confirmation of that scope. Requires the unexpired confirmationToken; never infer permission from a preview. Active work and workspace chats refuse.", schema({"id": string(100), "confirmationToken": string(100)}, ["id", "confirmationToken"])),
    "session.export": ("Freeze user-visible Markdown history. Optional scope=all/from/range uses exact fromMessageId/throughMessageId boundaries; minimal omits nonessential metadata. destination=none previews an immutable snapshot with counts/size; read result.statePath with get_state and follow every nextOffset for exact text, then session.exportDeliver can deliver those reviewed bytes. Use format=markdown for readable text; the snapshot stays unchanged as the conversation continues. Hidden instructions, reasoning and tool payloads are omitted; visible text is not automatically redacted. Default JSON is unchanged.", schema({"id": string(200), "format": {"enum": ["json", "markdown"]}, "scope": {"enum": ["all", "from", "range"]}, "fromMessageId": string(200), "throughMessageId": string(200), "minimal": {"type": "boolean"}, "includeExecution": {"type": "boolean"}, "includeConversationId": {"type": "boolean"}, "includeTimestamps": {"type": "boolean"}, "includeReferenceIds": {"type": "boolean"}, "destination": {"enum": ["download", "clipboard", "none"]}}, ["id"])),
    "session.exportDeliver": ("Copy or download an existing reviewed Markdown snapshot without rereading or rerunning its conversation. Delivery requires a connected browser; inspect view.conversationExport for its delivery report. A download receipt means started, not proof of a saved file.", schema({"id": string(200), "clientId": string(200), "snapshotId": string(200), "destination": {"enum": ["download", "clipboard"]}}, ["id", "snapshotId", "destination"])),
    "session.exportResult": ("Report conversation export browser delivery; a download report means started, not proof of a saved file.", schema({"requestId": string(100), "status": {"enum": ["ready", "error"]}, "message": string(2000)}, ["requestId", "status"])),
    "session.titlePreview": ("Read a bounded first-message label for one unnamed chat without loading its history or running work.", schema({"sessionId": string(200)}, ["sessionId"])),
    "session.inspect": ("Inspect conversation identity, status and recorded failure without running work.", schema({"id": string(200)}, ["id"])),
    "session.recover": ("Create an independent recovery copy with readable history, excluding old native tool/image payloads. Preserve the original and safety stops. Never start or replay work.", schema({"id": string(200)}, ["id"])),
    "session.fork": ("Fork conversation history through an optional user turn or exact saved user message", schema({"id": string(100),"turn":{"type":"integer","minimum":1},"afterMessageId":string(200)},["id"])),
    "message.copy": ("Copy the entire message text as Markdown on the connected browser",schema({"sessionId":string(200),"messageId":string(200)})),
    "message.copyResult": ("Report clipboard success or failure",schema({"requestId":string(100),"status":{"enum":["ready","error"]},"message":string(2000)},["requestId","status"])),
    "message.edit": ("Edit a user message and regenerate in the current conversation (mode current), or fork a new conversation (mode fork, also the legacy default). Later active context is replaced; original events and external tool effects remain.",schema({"sessionId":string(200),"messageId":string(200),"text":string(100000),"mode":{"enum":["current","fork"]}},["sessionId","messageId","text"])),
    "conversation.send": ("Send to the main Amplifier session. During a live run, steer that run without creating a new turn. expectedGenerationId binds a message to the observed run. Explicit steering stays bound; allowNextTurn lets an ordinary message start the next turn only after that exact run completes normally.", schema({"sessionId":string(200),"text": string(100000), "document":{"type":"object"}, "expectedGenerationId":{**string(128),"minLength":1}, "allowNextTurn":{"type":"boolean"}, "preserveDraft":{"type":"boolean"}, "replyId":string(64), "attachmentIds":{"type":"array","maxItems":8,"uniqueItems":True,"items":string(32)}, "via": {"enum": ["chat", "text", "call"]}}, ["text"])),
    "attachment.add": ("Attach a file or image up to 32 MB to a conversation draft. Provider-specific image limits still apply. expectedDraftId binds an upload admitted from an unnamed browser draft, including its exact creation handoff.", schema({"sessionId":{"type":["string","null"],"maxLength":200},"expectedDraftId":{"type":"string","pattern":"^[a-f0-9]{32}$"},"name":string(200),"base64":string(MAX_ENCODED_BYTES),"source":{"type":"string","enum":["clipboard-text"]}},["name","base64"])),
    "attachment.remove": ("Remove an attachment from a conversation draft", schema({"sessionId":{"type":["string","null"],"maxLength":200},"id":string(32)},["id"])),
    "conversation.delivery": ("Check a saved input's delivery without sending or starting work. Missing evidence remains uncertain.", schema({"sessionId": string(200), "inputId": string(200)}, ["sessionId", "inputId"])),
    "conversation.retry": ("Explicitly resend an unconfirmed latest message, preserving its input identity and attachments. Unknown delivery requires confirmUncertain after the user accepts that prior effects might repeat. Never call as a passive check.", schema({"sessionId": string(200), "inputId": string(200), "confirmUncertain": {"type": "boolean"}}, ["sessionId", "inputId"])),
    "conversation.stop": ("Stop session execution", schema({"sessionId": string(200)}, [])),
    "worker.spawn": ("Start a worker lane for heavier work", schema({"sessionId": string(200), "instruction": string(100000), "bundle": string(2000)}, ["instruction"])),
    "worker.stop": ("Stop one worker lane", schema({"sessionId": string(200), "id": string(100)}, ["id"])),
    "worker.message": ("Send a follow-up to an active persistent worker", schema({"sessionId": string(200), "id": string(100), "text": string(100000)}, ["sessionId", "id", "text"])),
    "worker.steer": ("Send a correction to a worker", schema({"sessionId": string(200), "id": string(100), "text": string(100000)}, ["id", "text"])),
    "approval.respond": ("Respond to an Amplifier permission request", schema({"sessionId": string(200), "id": string(100), "decision": {"enum": ["allow", "deny", "approve", "reject"]}, "comment": string(4000), "expectedRequestHash": string(64)}, ["id", "decision"])),
    "attention.read": ("Mark reviewed attention items as read without resolving the underlying condition. Reviewing the exact conversation error clears its historical navigation marker without changing saved errors, replaying work, or answering questions/approvals. Include fingerprints from /attention/items to avoid acknowledging newer results by mistake.", schema({"ids":{"type":"array","items":string(300),"maxItems":500},"fingerprints":{"type":"object","maxProperties":500,"additionalProperties":string(100)}},["ids"])),
    "view.update": ("Change panels, modality, draft, appearance or layout. Chat controls: panel=runtime, runtimeDraft.tab=overview/direction/limits/tools/computer. runtimeDraft.section reveals a known section (overview, direction, limits, tools, computer, screen-source, desktop-host, capture), opens its tab/panel and increments revealRevision; repeat requests reveal again. For visible agent navigation, choose clientId from canvasContext.connectedClientIds; disconnected saved clients are ineligible. Supply clientId when multiple connected browsers display the calling chat; opening controls never grants capture permission. Optional sessionId binds draft updates to that conversation without changing selection; null saves the attached client's draft before a conversation exists. Agent draft updates are limited to the calling conversation; other saved chats and the new-chat draft remain private. Canvas: canvasWidth and canvasOverviewWidth (300–16384 preferred pixels, separate artifact and overview widths), canvasFocused (full frame), canvasControlsPinned/Expanded (booleans). Navigation: navWidth (216–16384 preferred pixels), navPinned/Expanded (booleans). Workspace explorer: navWorkspacePath browses folders from /workspaceExplorer without selecting a chat, navWorkspaceFilter searches paths or aliases with case-insensitive fnmatch or plain text, navWorkspacePage selects a 1-based page, navWorkspaceAncestorsOpen toggles the ancestor menu. Use view.update workSurface=workspaces/chats/workspace and workWorkspaceId to browse without changing the active chat or its execution folder; workSurface=chat shows the selected conversation. Use session.select with an explicit id to open a particular chat. workWorkspaceTab=chats/work/files/details changes the workspace page tab. Use workspace.select to change the active workspace. Browser fits widths to the available space, preserving a 360px chat.", schema({"patch": {"type": "object"}, "clientId": string(100), "sessionId": {"type": ["string", "null"], "minLength": 1, "maxLength": 200}}, ["patch"])),
    "providers.credentials": ("Check provider credential environment availability without revealing values",schema({"sessionId":string(200),"module":string(200),"envVar":string(200)},["module"])),
    "providers.reorder": ("Save complete provider preference order atomically; expectedIds must match the current order",schema({"ids":{"type":"array","uniqueItems":True,"maxItems":1000,"items":string(200)},"expectedIds":{"type":"array","items":string(200)},"scope":{"enum":["global","project","local"]},"sessionId":string(200)},["ids","expectedIds"])),
    "bundles.reorder": ("Save composition order of enabled app capabilities; excludes standalone aliases",schema({"ids":{"type":"array","uniqueItems":True,"maxItems":1000,"items":string(200)},"expectedIds":{"type":"array","items":string(200)}},["ids","expectedIds"])),
    "providers.move": ("Reorder saved provider connections",schema({"id":string(200),"beforeId":{"type":["string","null"]},"scope":{"enum":["global","project","local"]},"sessionId":string(200)},["id"])),
    "locations.create": ("Create a new folder inside the chosen existing parent. Does not create a workspace registration or conversation.",schema({"path":string(4000),"name":string(255),"controlId":string(200)},["path","name","controlId"])),
    "locations.list": ("Browse local folders and files for a location control",schema({"path":string(4000),"directoriesOnly":{"type":"boolean"},"controlId":string(200)},["controlId"])),
    "providers.schema": ("Read a provider module’s configuration fields and choices",schema({"module":string(200),"id":string(200),"sessionId":string(200)},["module"])),
    "configuration.defaults": ("Resolve new-chat bundle and model without creating a conversation",schema({"location": LOCATION,"workspace":string(4000),"bundle":string(4000)},[])),
    "providers.list": ("List provider connections and setup status without creating a conversation",schema({"refresh":{"type":"boolean"},"location": LOCATION,"sessionId":string(200),"workspace":string(4000)},[])),
    "providers.save": ("Add or edit a provider connection",schema({"sessionId":string(200),"id":string(200),"module":string(200),"source":string(4000),"config":{"type":"object"},"apiKey":string(16000),"apiKeyEnv":string(200),"useGitHubCli":{"type":"boolean"},"scope":{"enum":["global","project","local"]}},["module","config"])),
    "providers.finishSetup": ("Save a connection's default model while preserving provider fields and existing model rules. Optionally initialize general and fast rules only for the first connection without custom routing.",schema({"sessionId":string(200),"id":string(200),"model":string(200),"scope":{"enum":["global","project","local"]},"initializeRouting":{"type":"boolean"}},["id","model"])),
    "providers.decisionModels": ("List installed optional decision backends and saved per-surface preferences. No inference or credential discovery.", schema({"sessionId":string(200),"workspace":string(4000),"location":LOCATION}, [])),
    "providers.configureDecisions": ("Save an explicit browser, computer, or both selection preference for new sessions. Both saves the same choice for the two independent purposes atomically. Standard remains the default. Requires separately composed UI-use tools; decision selection also requires a decision backend. Does not install or enable tools or change approvals.", schema({"sessionId":string(200),"purpose":{"enum":["browser","computer","both"]},"mode":{"enum":["standard","decision"]},"allowExternalState":{"type":"boolean"},"backend":schema({"provider":string(100),"model":string(200),"endpoint":string(2000)},["provider","model"]),"scope":{"enum":["global","project","local"]}},["purpose","mode","scope"])),
    "providers.configureImages": ("Choose automatic provider-priority image selection (mode=automatic), turn images off (mode=off), or select an existing connection with id, enabled, and a catalog model. Set exclusive=true when choosing the single image connection: the previous image choices are disabled atomically in the selected scope. Preserve chat models, credentials and tool permissions. No image request is sent.",schema({"sessionId":string(200),"id":string(200),"enabled":{"type":"boolean"},"exclusive":{"type":"boolean"},"mode":{"enum":["automatic","off"]},"model":string(200),"scope":{"enum":["global","project","local"]}},[])),
    "providers.imageModels": ("Discover compatible image models through a saved connection without generating an image",schema({"location":LOCATION,"id":string(200),"sessionId":string(200),"workspace":string(4000),"refresh":{"type":"boolean"}},["id"])),
    "providers.remove": ("Remove a provider connection",schema({"sessionId":string(200),"id":string(200),"scope":{"enum":["global","project","local"]}},["id"])),
    "providers.testMessage": ("Send a small real inference request using a saved provider and model. May incur provider usage charges. Creates no conversation.",schema({"id":string(200),"sessionId":string(200),"workspace":string(4000),"model":string(200)},["id"])),
    "providers.test": ("Test a configured provider",schema({"id":string(200),"sessionId":string(200)},["id"])),
    "providers.models": ("Browse cached provider models; refresh only this provider when requested",schema({"location": LOCATION,"id":string(200),"sessionId":string(200),"workspace":string(4000),"refresh":{"type":"boolean"}},["id"])),
    "providers.login": ("Sign in to a provider",schema({"id":string(200),"sessionId":string(200),"scope":{"enum":["global","project","local"]},"authMode":{"enum":["chatgpt_codex","chatgpt_plan","legacy_codex"]},"enablePlan":{"type":"boolean"}},["id"])),
    "providers.loginStatus": ("Check provider sign-in progress",schema({"id":string(200)},["id"])),
    "providers.loginCancel": ("Cancel provider sign-in",schema({"id":string(200)},["id"])),
    "routing.list": ("List model routing presets",schema()),
    "routing.show": ("Inspect a routing preset",schema({"name":string(200)})),
    "routing.use": ("Use a model routing preset",schema({"name":string(200),"scope":{"enum":["global","project","local"]}},["name"])),
    "routing.save": ("Save a custom routing preset",schema({"activate":{"type":"boolean"},"name":string(200),"matrix":{"type":"object"},"scope":{"enum":["global","project","local"]}},["name","matrix"])),
    "bundle.preview": ("Preview a replacement root bundle for an idle conversation", schema({"sessionId":string(200),"bundle":string(2000)},["sessionId","bundle"])),
    "bundle.switch": ("Switch an idle conversation; optional previewId checks a reviewed preview; preserve history and compatible model selection", schema({"sessionId":string(200),"bundle":string(2000),"previewId":string(100),"resetModel":{"type":"boolean"}},["sessionId","bundle"])),
    "bundle.fork": ("Fork history into a different root bundle; optional previewId checks a reviewed preview", schema({"sessionId":string(200),"bundle":string(2000),"previewId":string(100),"resetModel":{"type":"boolean"}},["sessionId","bundle"])),
    "bundle.default": ("Set or clear the default root for this app, workspace, or shared Amplifier settings", schema({"scope":{"enum":["app","workspace","shared"]},"bundle":{"type":["string","null"],"minLength":1,"maxLength":2000},"workspace":string(4000)},["scope","bundle"])),
    "workspace.bundleDefault.inspect": ("Read bundle defaults for an explicitly registered workspace", schema({"workspaceId":string(100)},["workspaceId"])),
    "bundle.discover": ("Browse bundles and behaviors in a Git repository", schema({"url":string(4000)})),
    "bundles.list": ("List app behaviors and standalone bundles",schema()),
    "bundles.add": ("Add a behavior or standalone bundle",schema({"uri":string(4000),"reviewId":string(32),"name":string(200),"role":{"enum":["behavior","standalone"]}},["uri","role"])),
    "bundles.toggle": ("Enable or disable an app behavior",schema({"id":string(200),"enabled":{"type":"boolean"}})),
    "bundles.remove": ("Remove a registered bundle",schema({"id":string(200)})),
    "bundles.move": ("Reorder app behaviors",schema({"id":string(200),"direction":{"enum":["up","down"]},"beforeId":{"type":["string","null"]}},["id"])),
    "bundle.export": ("Export the conversation's customized bundle",schema({"sessionId":string(200),"name":string(200),"description":string(1000)},["sessionId"])),
    "bundle.save": ("Save and use the conversation's customized bundle",schema({"sessionId":string(200),"name":string(200),"description":string(1000)},["sessionId","name"])),
    "configuration.cancel": ("Cancel queued mount-plan changes",schema({"id":string(200)})),
    "configuration.inspect": ("Inspect resolved modules and configuration",schema({"id":string(200)})),
    "configuration.apply": ("Apply edits to the loaded session mount plan, optionally waiting for idle",schema({"id":string(200),"config":{"type":"object"},"whenIdle":{"type":"boolean"}},["id","config"])),
    "runtime.control": ("Manage runtime modes, goals, provider, budgets, skills and tools",schema({"sessionId":string(200),"operation":string(100),"args":{"type":"object"}},["sessionId","operation"])),
    "modules.list": ("List scoped module configuration",schema({"scope":{"enum":["global","project","local"]}},[])),
    "modules.save": ("Add or override a scoped module",schema({"section":{"enum":["tools","hooks","providers","orchestrator","context"]},"module":string(200),"id":string(200),"source":string(4000),"config":{"type":"object"},"enabled":{"type":"boolean"},"scope":{"enum":["global","project","local"]}},["section","module"])),
    "modules.remove": ("Remove a scoped module override",schema({"section":string(100),"id":string(200),"scope":{"enum":["global","project","local"]}},["section","id"])),
    "modules.validate": ("Validate a module using its public Core contract",schema({"behavioral":{"type":"boolean"},"section":string(100),"id":string(200),"scope":{"enum":["global","project","local"]}},["section","id"])),
    "sources.list": ("List scoped source overrides",schema({"scope":{"enum":["global","project","local"]}},[])),
    "sources.validate": ("Validate a candidate module source without saving it",schema({"kind":{"enum":["module"]},"name":string(200),"source":string(4000),"section":{"enum":["tools","hooks","providers","orchestrator","context"]},"scope":{"enum":["global","project","local"]}},["kind","name","source","section"])),
    "sources.save": ("Set a scoped bundle or module source; validate=true validates the candidate first and saves only on success",schema({"kind":{"enum":["module","bundle"]},"name":string(200),"source":string(4000),"validate":{"type":"boolean"},"section":{"enum":["tools","hooks","providers","orchestrator","context"]},"scope":{"enum":["global","project","local"]}},["kind","name","source"])),
    "sources.remove": ("Remove a scoped source override",schema({"kind":{"enum":["module","bundle"]},"name":string(200),"scope":{"enum":["global","project","local"]}},["kind","name"])),
    "history.importFile": ("Import a transcript as an independent conversation",schema({"content":string(1000000),"format":{"enum":["json","jsonl"]},"title":string(200),"bundle":string(2000)},["content","format"])),
    "notifications.get": ("Inspect notification delivery preferences",schema()),
    "notifications.save": ("Configure browser and ntfy notifications",schema({"patch":{"type":"object"}})),
    "permissions.get": ("Inspect scoped write settings. scope=session requires sessionId and returns this chat’s overrides, inherited workspace defaults and a revision for save/reset; this is saved policy, not mounted access.",schema({"sessionId":string(200),"scope":{"enum":["global","project","local","session"]}},[])),
    "permissions.save": ("Save scoped write settings. scope=session changes only this chat and requires sessionId plus expectedRevision from permissions.get. Agents require file_access authority at the selected scope.",schema({"sessionId":string(200),"scope":{"enum":["global","project","local","session"]},"expectedRevision":string(64),"allowed":{"type":"array","maxItems":256,"items":string(4000)},"denied":{"type":"array","maxItems":256,"items":string(4000)}},["allowed","denied"])),
    "permissions.reset": ("Return this chat’s write overrides to workspace defaults. Removes only session allowed/denied write overrides, preserving read policy and shared settings. May widen access; agents need file_access/session authority. Preview with permissions.get first.",schema({"sessionId":string(200),"scope":{"enum":["session"]},"expectedRevision":string(64)},["sessionId","scope","expectedRevision"])),
    "history.list": ("Browse persisted and optionally legacy sessions",schema({"legacy":{"type":"boolean"}},[])),
    "history.refresh": ("Refresh automatically discovered CLI workspaces and chats without mounting or replaying sessions", schema()),
    "session.history": ("Refresh saved chat text, or load earlier messages before an offset. This never starts an Amplifier runtime.", schema({"id":string(200),"before":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":100}},["id"])),
    "history.shared.list": ("List common shared sessions for a workspace without mounting a worker",schema({"workspace":string(4000)},[])),
    "history.shared.view": ("View common shared session history without mounting a worker",schema({"id":string(200),"workspace":string(4000),"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":100}},["id","workspace"])),
    "history.shared.open": ("Open the same shared conversation without copying its runtime history",schema({"id":string(200),"workspace":string(4000)},["id","workspace"])),
    "history.import": ("Open a persisted or legacy session",schema({"id":string(200)})),
    "history.export": ("Export runtime transcript and metadata",schema({"sessionId":string(200),"format":{"enum":["json","jsonl"]}},["sessionId"])),
    "history.cleanup": ("Preview or clean old conversation entries",schema({"days":{"type":"integer","minimum":1},"apply":{"type":"boolean"},"purge":{"type":"boolean"}},[])),
    "maintenance.backup": ("Back up shared session files, app settings, artifacts and receipts",schema()),
    "maintenance.canonicalizeBundleReferences": ("Preview or repair legacy Work chat IDs to work without replacing bundles or replaying messages", schema({"apply": {"type": "boolean"}})),
    "maintenance.restoreResource": ("Restore missing saved content from an exact hash-matching JSON value without replaying tools",schema({"id":string(64),"value":{"type":"object"}},["id","value"])),
    "maintenance.reset": ("Preview or reset selected app data with a retained private backup",schema({"parts":{"type":"array","items":{"enum":["runtime","cache","settings","conversations"]}},"apply":{"type":"boolean"},"confirmation":string(20)},["parts"])),
    "maintenance.repair": ("Repair runtime dependency installation while idle",schema()),
    "updates.app": ("Stage a published application release and restart when idle",schema()),
    "updates.featureInstall": ("Explicitly add optional browser, decision-provider or native observation support to the same serving app revision, preserving installed dependencies and extras. Qualifies an isolated candidate, installs and restarts through existing idle/queue guards. Does not configure a model, grant OS permission or grant control. Inspect updates.featureResults for the durable outcome; do not replay an unknown result.", schema({"feature":{"enum":["native-desktop","managed-browser","decisions-openai","decisions-azure","decisions-jev","decisions-local"]},"hostInstanceId":string(200)})),
    "updates.smartToolRollback": ("Restore the previous installed Smart Tool revision when idle; preserves conversation history.", schema({"id":string(200)},["id"])),
    "updates.smartToolRetry": ("Retry one failed Smart Tool update using its current update resultId (or previous installation ID when no resultId is present). Healthy tools are not repeated.", schema({"id":string(200)},["id"])),
    "updates.smartToolSkip": ("Skip one failed Smart Tool revision using its current update resultId (or previous installation ID when no resultId is present). Retain the current installation; newer revisions remain eligible.", schema({"id":string(200)},["id"])),
    "updates.check": ("Check published application releases and ecosystem sources for updates", schema()),
    "updates.failureDisposition": ("User-only: close one exact overflow or unknown-feature-scope failure as acknowledged uncertainty, not verified recovery. Use dispositionOccurrence from attention; reading alone never closes it.", schema({"id":string(300),"expectedOccurrence":{"type":"string","pattern":"^[a-f0-9]{64}$"},"disposition":{"enum":["acknowledged-uncertainty"]}},["id","expectedOccurrence","disposition"])),
    "updates.diagnostics": ("Collect a sanitized update support report, including source provenance, without credentials, chat content or private paths.", schema()),
    "updates.forceRestart": ("Explicitly confirm interrupting active work to restart into one staged qualified app. Saved history and unresolved checkout fences remain; no work is replayed.", schema({"confirm":{"enum":[True]},"hostInstanceId":string(200),"revision":string(40),"generation":string(32),"attemptId":string(200)}, ["confirm","hostInstanceId","revision","generation","attemptId"])),
    "updates.install": ("Stage and validate available application or ecosystem updates; activate when idle. Application updates restart the host.", schema()),
    "updates.rollback": ("Restore the previous ecosystem version when idle", schema()),
    "settings.update": ("Change voice, workspace or update defaults. Update checks support intervalHours 1, 4, 8 or 24.", schema({"patch": {"type": "object"}})),
    "theme.apply": ("Apply a complete single-file CSS skin", schema({"name": string(100), "css": string(1000000)})),
    "theme.reset": ("Restore the default skin", schema()),
    "state.export": ("Export app state and attached device views", schema()),
    "notification.request": ("Request notification permission on this device", schema()),
    "call.start": ("Start a realtime voice call on the connected browser", schema({"attemptId": string(200)}, [])),
    "call.mute": ("Mute or unmute the call microphone", schema({"muted": {"type": "boolean"}, "callId": string(200), "attemptId": string(200), "controlId": string(200)}, ["muted"])),
    "call.keepAwake": ("Keep the calling browser screen awake while its voice call is active; does not grant background execution.", schema({"enabled": {"type": "boolean"}}, ["enabled"])),
    "call.resumeAudio": ("Retry speaker playback on the calling browser without starting a new call or microphone capture.", schema()),
    "call.end": ("End audio while leaving the work running", schema({"callId": string(200), "attemptId": string(200), "controlId": string(200)}, [])),
}


from .voice_settings import definitions as voice_settings_definitions
ACTION_DEFINITIONS.update(voice_settings_definitions(schema, string))

from .storage_cleanup import definitions as storage_cleanup_definitions
ACTION_DEFINITIONS.update(storage_cleanup_definitions(schema, string))

from .storage_usage import definitions as storage_definitions
ACTION_DEFINITIONS.update(storage_definitions(schema, string))
from .operations import definitions as operation_definitions
ACTION_DEFINITIONS.update(operation_definitions())
from .computation import definitions as computation_definitions
ACTION_DEFINITIONS.update(computation_definitions())

from .capacity import definitions as capacity_definitions
ACTION_DEFINITIONS.update(capacity_definitions(schema, string))
from .model_capacity_settings import definitions as model_capacity_definitions
ACTION_DEFINITIONS.update(model_capacity_definitions(schema, string))
from .configuration_authority import definitions as configuration_authority_definitions
ACTION_DEFINITIONS.update(configuration_authority_definitions(schema, string))
from .access_mode import definitions as access_mode_definitions
ACTION_DEFINITIONS.update(access_mode_definitions(schema, string))


def validate_theme(css):
    """Reject network-bearing or malformed skins; assets must travel with the file."""
    if not css.strip():
        raise AppError("The skin is empty.")
    if "</style" in css.lower():
        raise AppError("A skin must contain CSS only.")
    rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    def visit(nodes):
        for node in nodes:
            if node.type == "error":
                raise AppError("The stylesheet contains invalid CSS: " + node.message)
            if node.type == "at-rule" and node.lower_at_keyword in {"import", "namespace", "document"}:
                raise AppError("Skins must be self-contained; external imports are unsupported.")
            if node.type == "url":
                check_url(node.value)
            if node.type == "function" and node.lower_name == "url":
                arguments = [argument for argument in node.arguments if argument.type not in ('comment', 'whitespace')]
                if len(arguments) != 1 or arguments[0].type != 'string':
                    raise AppError('Use one literal asset reference in url(), not substitutions.')
                check_url(arguments[0].value)
            if node.type == 'function' and node.lower_name in {'image-set', '-webkit-image-set', 'image', 'src'}:
                # These CSS image forms also accept bare strings as URLs.
                # Fail closed for nested/unknown substitutions, not just var().
                from .appearance_packages import literal_image_functions
                literal_image_functions(node)
                for argument in node.arguments:
                    if argument.type == 'string':
                        check_url(argument.value)
            for attr in ("prelude", "content", "arguments"):
                child = getattr(node, attr, None)
                if child:
                    visit(child)
    def check_url(value):
        if not value.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,", "data:font/woff;base64,", "data:font/woff2;base64,")):
            raise AppError("Embed images and fonts in the skin instead of loading external URLs.")
    visit(rules)


from .theme_library import definitions as theme_library_definitions
ACTION_DEFINITIONS.update(theme_library_definitions(schema, string))

from .smart_canvas import definitions as smart_tool_definitions
ACTION_DEFINITIONS.update(smart_tool_definitions(schema, string))
from .conversation_library import definitions as library_definitions
LIBRARY_ACTIONS = library_definitions(schema, string)
ACTION_DEFINITIONS.update(LIBRARY_ACTIONS)
from .recall import definitions as recall_definitions
ACTION_DEFINITIONS.update(recall_definitions(schema, string))
from .outputs import definitions as output_definitions
ACTION_DEFINITIONS.update(output_definitions(schema, string))
from .publishing import definitions as publishing_definitions
ACTION_DEFINITIONS.update(publishing_definitions(schema, string))
from .feedback import definitions as feedback_definitions
ACTION_DEFINITIONS.update(feedback_definitions(schema, string))
from .new_chat_handoffs import definitions as handoff_definitions
ACTION_DEFINITIONS.update(handoff_definitions(schema, string))


from .shell_modules import ShellModules, definitions as shell_definitions
ACTION_DEFINITIONS.update(shell_definitions(schema, string))
from .canvas_views import CanvasViews, definitions as canvas_view_definitions
ACTION_DEFINITIONS.update(canvas_view_definitions(schema, string))
from .canvas_apps import definitions as canvas_app_definitions, THEME_TOKENS
ACTION_DEFINITIONS.update(canvas_app_definitions(schema, string))
from .canvas_versions import definitions as canvas_version_definitions
ACTION_DEFINITIONS.update(canvas_version_definitions(schema, string))
from .canvas_reference import definitions as canvas_reference_definitions
ACTION_DEFINITIONS.update(canvas_reference_definitions(schema, string))
from .message_interactions import definitions as message_interaction_definitions, ACTIONS as MESSAGE_INTERACTIONS
ACTION_DEFINITIONS.update(message_interaction_definitions(schema, string))
from .questions import definitions as question_definitions
ACTION_DEFINITIONS.update(question_definitions(schema, string))
from .task_continuity import definitions as task_definitions
ACTION_DEFINITIONS.update(task_definitions(schema, string))
from .coordination import definitions as coordination_definitions
ACTION_DEFINITIONS.update(coordination_definitions())
from .integration_evidence import definitions as integration_definitions
ACTION_DEFINITIONS.update(integration_definitions(schema, string))
from .collaboration import definitions as collaboration_definitions
ACTION_DEFINITIONS.update(collaboration_definitions(schema, string))
from .assignments import definitions as assignment_definitions
ACTION_DEFINITIONS.update(assignment_definitions(schema, string))
from .workspace_awareness import definitions as workspace_awareness_definitions
ACTION_DEFINITIONS.update(workspace_awareness_definitions(schema, string))
from .workspace_work import definitions as workspace_work_definitions
ACTION_DEFINITIONS.update(workspace_work_definitions(schema, string))
for _peer_action in ("conversation.send", "coordination.followup"):
    ACTION_DEFINITIONS[_peer_action][1]["properties"].update({
        "grantId": string(200), "mode": {"enum": ["auto", "notify", "queue", "steer"]},
        "senderSessionId": string(200),
        "replyToRequestId": string(200),
        "expiresInSeconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        "references": {"type": "array", "maxItems": 16, "items": string(2000)},
    })
from .schedules import definitions as schedule_definitions
ACTION_DEFINITIONS.update(schedule_definitions(schema, string))
from .observations import definitions as observation_definitions
ACTION_DEFINITIONS.update(observation_definitions(schema, string))
from .voice_visual import VoiceVisual, definitions as visual_definitions
ACTION_DEFINITIONS.update(visual_definitions(schema, string))
from .computer_visual import ComputerVisuals, definitions as computer_visual_definitions
ACTION_DEFINITIONS.update(computer_visual_definitions(schema, string))
from .worktrees import definitions as worktree_definitions
ACTION_DEFINITIONS.update(worktree_definitions(schema, string))


def _register_actions(definitions):
    """Register schemas once and retain their exact names for routing."""
    ACTION_DEFINITIONS.update(definitions)
    return frozenset(definitions)


from .workspace_resources import definitions as resource_reservation_definitions
RESOURCE_RESERVATION_ACTIONS = _register_actions(resource_reservation_definitions(schema, string))
from .task_environments import definitions as environment_definitions
ENVIRONMENT_ACTIONS = _register_actions(environment_definitions(schema, string))
from .test_capacity_settings import definitions as test_capacity_definitions
TEST_CAPACITY_ACTIONS = _register_actions(test_capacity_definitions(schema, string))
from .registered_test_runners import definitions as test_runner_definitions
TEST_RUNNER_ACTIONS = _register_actions(test_runner_definitions(schema, string))
from .test_execution_settings import definitions as test_execution_definitions
TEST_EXECUTION_ACTIONS = _register_actions(test_execution_definitions(schema, string))
from .test_queue import definitions as test_queue_definitions
TEST_QUEUE_ACTIONS = _register_actions(test_queue_definitions(schema, string))
from .portability import definitions as portability_definitions
ACTION_DEFINITIONS.update(portability_definitions(schema, string))
ACTION_DEFINITIONS['theme.preview'] = ('Preview a validated skin on an attached client.', schema({'name': string(100), 'css': string(1000000), 'clientId': string(100)}, ['name', 'css']))
ACTION_DEFINITIONS['theme.revert'] = ('End a preview or undo this client’s last applied skin if it is still current.', schema({'clientId': string(100)}, []))
for theme_action in ('theme.apply', 'theme.preview'):
    ACTION_DEFINITIONS[theme_action] = (("Apply a complete theme definition or CSS skin, or patch the current palette." if theme_action == 'theme.apply' else "Preview a complete theme definition or CSS skin, or a palette patch, on an attached client."), ACTION_DEFINITIONS[theme_action][1])
    theme_spec = ACTION_DEFINITIONS[theme_action][1]
    theme_spec['properties']['tokens'] = {'type': 'object', 'minProperties': 1, 'additionalProperties': False,
        'properties': {key: {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'} for key in THEME_TOKENS}}
    from .themes import DEFINITION
    theme_spec['properties']['definition'] = DEFINITION
    theme_spec['required'] = ['name']
    theme_spec['oneOf'] = [{'required': [key], 'not': {'anyOf': [{'required': [other]} for other in ('css', 'tokens', 'definition') if other != key]}}
                           for key in ('css', 'tokens', 'definition')]




from .profiling_host import DEFINITIONS as PROFILING_DEFINITIONS
ACTION_DEFINITIONS.update(PROFILING_DEFINITIONS)
from .configuration_recovery import definitions as configuration_recovery_definitions
ACTION_DEFINITIONS.update(configuration_recovery_definitions(schema, string))
from .patch_placement import definitions as patch_placement_definitions
ACTION_DEFINITIONS.update(patch_placement_definitions(schema, string))
from .next_message_configuration import definitions as next_message_definitions
ACTION_DEFINITIONS.update(next_message_definitions(schema, string))
ACTION_DEFINITIONS['conversation.send'][1]['properties']['expectedConfigurationRevision'] = {'type': 'integer', 'minimum': 0}

from .settings_actions import definitions as settings_definitions, ACTIONS as SETTINGS_ACTIONS, NAVIGATION_ACTIONS as SETTINGS_NAVIGATION
ACTION_DEFINITIONS.update(settings_definitions(schema, string))

from .session_identity import ID_ACTIONS as SESSION_ID_ACTIONS
# Host-owned classifications, not capabilities inferred from an action's suffix.
# Domain entries only defer to their existing ownership/provenance checks.
ACTION_POLICIES = {}
for _access, _actions in {
    "caller_controlled": {
        "conversation.send", "conversation.stop", "conversation.retry", "conversation.configure", "worker.spawn",
        "worker.message", "worker.steer", "worker.stop", "runtime.control", "message.edit",
        "session.warm", "session.takeover", "session.fork", "configuration.apply",
        "configuration.cancel", "bundle.switch", "bundle.fork", "bundle.save", "bundle.export",
        "call.start", "permissions.save", "permissions.reset", "providers.save", "providers.finishSetup", "providers.configureImages",
        "providers.remove", "providers.move", "providers.reorder", "providers.login",
        "providers.test", "providers.testMessage", "providers.models", "providers.imageModels",
        "routing.use", "routing.save", "modules.save", "modules.remove", "modules.validate",
        "sources.save", "sources.remove", "sources.validate",
        "worktree.remove", "worktree.archive", "worktree.restore", "worktree.reconcile",
        "coordination.intent",
    },
    "presentation": {
        *SETTINGS_NAVIGATION,
        "session.select", "session.pin", "session.markUnread", "session.archive", "session.restore", "session.rename",
        "workspace.archive", "workspace.restore", "canvas.visibility", "view.update", "attachment.add", "attachment.remove",
    },
    "peer_read": {
        "settings.connection.services",
        "session.inspect", "session.titlePreview", "session.history", "session.deletePreview", "session.export",
        "session.exportDeliver", "session.sharePreview", "session.shareRead", "session.shareList",
        "message.copy", "history.export", "conversation.delivery", "runtime.dependencies",
        "permissions.get", "providers.credentials", "providers.schema", "providers.list", "providers.decisionModels",
        "diagnostics.records", "task.get", "task.history", "task.detail", "task.receipt",
        "capacity.read", "modelCapacity.read", "question.list", "question.read",
        "schedule.list", "schedule.read", "schedule.overview", "session.plans", "storage.inspect", "workspace.deleteFilesPreview", "feedback.destination",
        "worktree.inspect", "worktree.list", "worktree.status",
        "coordination.awareness", "coordination.intent.read",
    },
    "domain_authoritative": {
        *configuration_recovery_definitions(schema, string),
        *patch_placement_definitions(schema, string),
        "worktree.create", "worktree.attach", "worktree.handoff",
        "coordination.review",
        *integration_definitions(schema, string),
        "workspace.deleteFiles", "desktop.readiness", "configuration.inspect", "session.recover", "approval.respond",
        "canvas.show", "canvas.openFile", "canvas.openAttachment", "canvas.openGif", "canvas.select", "canvas.reference",
        *MESSAGE_INTERACTIONS,
        *operation_definitions(), *observation_definitions(schema, string),
        *visual_definitions(schema, string), *computer_visual_definitions(schema, string),
        *canvas_view_definitions(schema, string), *canvas_app_definitions(schema, string),
        *canvas_version_definitions(schema, string),
        *publishing_definitions(schema, string),
        *RESOURCE_RESERVATION_ACTIONS,
        *ENVIRONMENT_ACTIONS,
        *TEST_QUEUE_ACTIONS,
        *TEST_CAPACITY_ACTIONS,
        *TEST_RUNNER_ACTIONS,
        *assignment_definitions(schema, string),
        *workspace_work_definitions(schema, string),
        *handoff_definitions(schema, string),
    },
}.items():
    for _action in _actions:
        ACTION_POLICIES[_action] = {
            "access": _access,
            "target": "id" if _action in SESSION_ID_ACTIONS or _action == "configuration.cancel" else "sessionId",
        }
ACTION_POLICIES["session.create"] = {"access": "grant_creation", "target": None}
ACTION_POLICIES["session.naming"] = {"access": "naming", "target": "id"}
from .configuration_authority import BUNDLE_GLOBAL
from .owned_history import SESSION_READS, SCOPE as HISTORY_SCOPE, guard_action as guard_history_action
from .history_query import SCHEMA as HISTORY_QUERY_SCHEMA
for _action in SESSION_READS:
    ACTION_DEFINITIONS[_action][1]['properties']['scope'] = HISTORY_SCOPE
for _action in {'history.list', 'history.shared.list', 'history.shared.view'}:
    _properties=ACTION_DEFINITIONS[_action][1]['properties']
    _properties['scope']=HISTORY_SCOPE
    _properties.setdefault('offset',{'type':'integer','minimum':0})
    _properties.setdefault('limit',{'type':'integer','minimum':1,'maximum':50})
    _properties['include_discovery_issues']=HISTORY_QUERY_SCHEMA['properties']['include_discovery_issues']
for _action in BUNDLE_GLOBAL:
    ACTION_POLICIES[_action] = {"access": "caller_controlled", "target": "sessionId"}
    ACTION_DEFINITIONS[_action][1]["properties"]["sessionId"] = string(200)

for _action in {"routing.use", "routing.save", "modules.save", "modules.remove", "modules.validate",
                "sources.save", "sources.remove", "sources.validate", "call.start"}:
    ACTION_DEFINITIONS[_action][1]["properties"]["sessionId"] = string(200)
from .configuration_target import PREFIXES as CONFIGURATION_PREFIXES
for _action, (_, _spec) in ACTION_DEFINITIONS.items():
    if _action.startswith(CONFIGURATION_PREFIXES):
        _spec['properties'].setdefault('scope', {'enum': ['global', 'project', 'local']})
        _spec['properties']['configurationTarget'] = {'oneOf': [
            schema({'kind': {'const': 'global'}}, ['kind']),
            schema({'kind': {'const': 'workspace'}, 'workspaceId': string(200)}, ['kind', 'workspaceId']),
        ]}
for _action, (_, _spec) in ACTION_DEFINITIONS.items():
    if (_action not in {'workspace.environments.removal.read', 'workspace.environments.removal.list'}
            and ('sessionId' in _spec.get('properties', {}) or _action in SESSION_ID_ACTIONS)):
        _spec['properties']['nativeProject'] = string(4000)


class AppService:
    @property
    def state(self):
        clients = getattr(self, 'clients', None)
        return clients.state(self._state) if clients else self._state

    @state.setter
    def state(self, value):
        self._state = value

    def __init__(self, data_dir: Path, runtime=None, workspace=None):
        from .runtime_event_batches import EventSink
        self.on_runtime_event = EventSink(self.on_runtime_event, self.on_runtime_events)
        from .installed_build import capture
        self.installed_build = capture()
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.data_dir / "app.sqlite3")
        (self.data_dir / "app.sqlite3").chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS integration_receipts ON commands(json_extract(receipt,'$.commandAction'),json_extract(receipt,'$.result.sessionId'))")
        self.db.execute("CREATE TABLE IF NOT EXISTS state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        from .state_records import initialize as initialize_records
        initialize_records(self.db)
        self.db.commit()
        self.shell = ShellModules(self)
        self.canvas_views = CanvasViews(self)
        from .surface_context import SurfaceContext
        self.surface_context = SurfaceContext(self)
        self.default_workspace = str(Path(workspace or os.getcwd()).resolve())
        self.runtime = runtime
        if runtime is not None and hasattr(runtime, "home"):
            runtime.home = self.data_dir
        from .next_message_configuration import bind
        bind(self, runtime)
        from .update_drain import bind as bind_update
        bind_update(self, runtime)
        self.voice_service = None
        self.voice_visual = VoiceVisual(self)
        self.computer_visual = ComputerVisuals(self)
        self.update_manager = None
        self.management = None
        self.smart_tools = None
        self.smart_canvas = None
        self.queues = set()
        self.queue_tokens = {}
        self.queue_clients = {}
        self.queue_sessions = {}
        self.instance_id = str(uuid.uuid4())
        self.tasks = set()
        self.workspace_setup_tasks = set()
        self.smart_tool_tasks = set()
        self.smart_tool_requests = {}
        self.lock = TimedLock(self, 'state.lock.wait')
        self._creation_locks = WeakValueDictionary()
        # RuntimeManager.close() is terminal.  Every host-continuing operation
        # that retires it therefore shares this exclusion and installs a fresh
        # manager before reopening normal work.
        self.runtime_lifecycle_lock = asyncio.Lock()
        self._runtime_lifecycle_tasks = set()
        self._runtime_alias = None
        self.closed = False
        self._close_started = False
        self.performance_diagnostics = PerformanceDiagnostics()
        from .history_directory import HistoryDirectory, ready as directory_ready
        self.directory = HistoryDirectory(self)
        # Finish schema initialization before a legacy storage upgrade backs up
        # this connection. SQLite backup waits indefinitely on its own writer;
        # no application state has been loaded or changed at this boundary.
        self.db.commit()
        self._directory_migrated = directory_ready(self.db)
        migrating_directory = not self._directory_migrated
        from .session_warmup import SessionWarmup
        self.warmup = SessionWarmup(self)
        row = self.db.execute("SELECT value FROM state WHERE id=1").fetchone()
        from .state_records import load as load_records, load_working
        from . import __version__
        self.state = (load_working(self.db) if self._directory_migrated else load_records(self.db)) if row else {
            "schemaVersion": 1, "revision": 0, "sessions": [], "selectedSessionId": None,
            # Only genuinely new state starts here. Existing installations must
            # retain unread migration guidance, including skipped releases.
            "releaseNoticeStartVersion": __version__,
            "updates": {"initialPreparation": True},
            "settings": {"preferredVoice": "gpt-live-1", "fallbackVoice": "gpt-realtime-2.1", "bundle": "work", "workspace": self.default_workspace},
            "theme": {"name": "Amplifier Unified", "css": self.default_theme()},
            "view": {"setupJourney":"welcome", "mode": "chat", "panel": None, "draft": "", "scheme": "system", "layout": "balanced"},
            "voice": {"status": "disconnected"}, "runtime": {"available": runtime is not None}, "devices": {}, "events": [],
        }
        from .managed_deletion import recover as recover_managed_deletions
        # Confirmed deletion recovery is an explicit offline reconciliation of
        # complete durable state. Ordinary warm starts admit only selected or
        # pending owners, and must never feed that partial set to checkpoint().
        recovering_deletion = bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='managed_deletions'").fetchone()
            and self.db.execute("SELECT 1 FROM managed_deletions WHERE phase='confirmed' LIMIT 1").fetchone())
        if recovering_deletion and self._directory_migrated:
            self.state = load_records(self.db)
        recover_managed_deletions(self.data_dir, self.db, self.state)
        if recovering_deletion and self._directory_migrated:
            self.state = load_working(self.db)
        from .managed_deletion import tombstones
        self._deleted_session_ids = {sid for row in tombstones(self.db) for sid in row['ids']}
        from .default_typography import upgrade_default
        upgrade_default(self.state, self.default_theme())
        from .settings_migration import migrate_settings
        migrate_settings(self.data_dir, {self.default_workspace, *[s["workspace"] for s in self.state["sessions"] if not s.get("historyManaged") and s.get("workspace")]})
        if row and not self.state.get("sharedVoiceMigration"):
            from .preferences import SettingsStore
            def migrate_voice(settings):
                voice = settings.setdefault("voice", {})
                for old, key in (("preferredVoice", "preferred_model"), ("fallbackVoice", "fallback_model"), ("voiceName", "voice"), ("fallbackVoiceName", "fallback_voice"), ("voiceInterruptions", "interruptions")):
                    if self.state["settings"].get(old):
                        voice.setdefault(key, self.state["settings"][old])
            SettingsStore(self.data_dir).update(self.default_workspace, "global", migrate_voice)
        self.state["sharedVoiceMigration"] = True
        self._view_cache = {}
        from .message_preparation import MessagePreparation
        self._message_preparation = MessagePreparation()
        from .execution_preparation import ExecutionPreparation
        self._execution_preparation = ExecutionPreparation()
        self._session_projection_refs = {item['id']: copy.deepcopy(item) for item in self.state['sessions']}
        self._history_local_revision = 0
        from .cold_display import ColdDisplay
        self.cold_display = ColdDisplay(self)
        from .session_projection import hydrate
        hydrate(self.data_dir, self.state, self.db)
        from .storage_migration import upgrade
        upgrade(self)
        from .cold_display import compact_execution_references
        compact_execution_references(self._state, self.db)
        from .chat_navigation import initialize as initialize_chat_navigation
        initialize_chat_navigation(self.state)
        from .conversation_library import ConversationLibrary
        self.conversation_library = ConversationLibrary(self)
        from .recall import Recall
        self.recall = Recall(self)
        self.state["voice"] = {"status": "disconnected"}
        self.state["runtime"] = {"available": runtime is not None, "description": "Isolated Amplifier sessions; runtime is prepared on first use."}
        from .session_ownership import restore
        for session in self.state["sessions"]:
            from .capacity import restore_observation
            restore_observation(session)
            restore(session)
            settle_stream(session)
            session["configurationBusy"]=False
            if session.get("bundleChange", {}).get("phase") == "working":
                session["bundleChange"] = {"phase":"error", "error":"The app restarted during a bundle change. Load the conversation and preview again; work was not replayed."}
            session.pop("bundlePreview", None)
            restore_voice_usage(session)
            interrupt_unfinished(session)
            if session["status"] in {"working", "starting", "ready", "stopping"}:
                session["status"] = "interrupted"
                session["activity"] = {"phase": "interrupted", "label": "Previous work was interrupted; it has not been replayed.", "activeTools": [], "updatedAt": time.time()}
            for worker in session.get("workers", []):
                if worker.get("status") in {"working", "running", "starting", "queued", "pending", "stopping"} or worker.get("persistent") and worker.get("status") == "idle":
                    worker["status"] = "interrupted"
        if not self.state.get("defaultBundleMigration"):
            if self.state["settings"].get("bundle") == "foundation":
                self.state["settings"]["bundle"] = "anchors"
            self.state["defaultBundleMigration"] = True
        update_options = self.state["settings"].setdefault("updates", {})
        update_options.setdefault("autoCheck", True)
        update_options.setdefault("autoInstall", update_options["autoCheck"])
        update_options.setdefault("intervalHours", DEFAULT_CHECK_INTERVAL_HOURS)
        self.state["devices"] = {}
        from .workspace_canvas import initialize
        initialize(self.state)
        from .workspace_provisioning import recover as recover_setup
        for workspace_row in self.state['workspaces']:
            if workspace_row.get('setupId'):
                try:
                    workspace_row['setup'] = recover_setup(self.data_dir, workspace_row['setupId'])
                    if workspace_row['setup'].get('scaffolded'):
                        self.state['configurationRevision'] = self.state.get('configurationRevision', 0) + 1
                        self.state['draftDefaults'] = {}
                except (ValueError, OSError):
                    workspace_row['setup'] = {**workspace_row.get('setup', {}), 'status': 'failed',
                                              'error': 'Setup receipt is unavailable. Preserve the folder and inspect it.'}
        from .workspace_placement import defaults as workspace_root
        self.state['settings'].setdefault('workspaces', {})
        self.state['workspaceDefaults'] = {'root': workspace_root(self), 'hostLabel': socket.gethostname()}
        from .canvas_library import recover_legacy
        recover_legacy(self.state,self.db,self.data_dir)
        from .naming import automatic,refresh
        for session in self.state['sessions']:
            if session.get('naming', {}).get('status') == 'working':
                session['naming'] = {'status': 'error', 'error': 'Name generation was interrupted. You can try again.'}
            session.setdefault('titleSource','automatic' if automatic(session) else 'manual')
            try:
                refresh(self.data_dir,session,migrate=True)
            except (OSError, ValueError):
                # A damaged session must not prevent the entire app starting.
                # Native history diagnostics handle repair; never replace it
                # with a stale display title during startup.
                import logging
                logging.getLogger(__name__).warning("Could not read a saved session name; original metadata retained.")
        from .feedback import Feedback
        self.feedback = Feedback(self)
        from .new_chat_handoffs import NewChatHandoffs
        self.handoffs = NewChatHandoffs(self)
        from .diagnostics import Diagnostics
        self.diagnostics = Diagnostics(self)
        for session in self.state['sessions']:
            for message in session.get('messages', []):
                if (message.get('steering') or {}).get('disposition') in {'sending', 'queued'}:
                    from .conversation_steering import record as record_steering
                    record_steering(self, session, message.get('inputId'), 'unknown',
                                    'The app restarted before steering application was confirmed. Nothing was resent.',
                                    preserve_pending_post=True)
                if message.get('delivery', {}).get('status') == 'sending':
                    self._delivery(session, message.get('inputId'), 'unknown')
        # A durable request receipt is not an acknowledgement from a retired
        # worker. Preserve uncertainty and never replay its message on restart.
        for identity, saved_receipt in self.db.execute("SELECT id,receipt FROM commands WHERE json_extract(receipt,'$.commandAction')='worker.message' AND json_extract(receipt,'$.delivery')='requested'").fetchall():
            receipt = json.loads(saved_receipt)
            receipt["delivery"] = "unknown"
            self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
        from .history_revision import recover_views
        recover_views(self)
        from .automatic_history import AutomaticHistory
        self.history = AutomaticHistory(self)
        from .native_catalog import NativeCatalog
        try:
            self.directory.bind(NativeCatalog(self.data_dir / 'native-catalog.sqlite3', self.history.index.home))
        except (OSError, ValueError, sqlite3.Error):
            # Disposable native cache failure must not hide saved app owners or
            # prevent startup. Discovery reports/rebuilds it independently.
            logging.getLogger(__name__).warning('Native directory cache unavailable; saved app records retained.', exc_info=True)
        from .outputs import Outputs
        self.outputs = Outputs(self)
        from .event_log_view import EventLogView
        self.event_log_view = EventLogView(self)
        from .client_views import ClientViews
        self.clients = ClientViews(self)
        self._client_snapshots = {}
        from .browser_state import SnapshotCopies
        self._snapshot_copies = SnapshotCopies()
        from .questions import Questions
        self.questions = Questions(self)
        from .coordination import Coordination
        self.coordination = Coordination(self)
        from .collaboration import Collaboration
        self.collaboration = Collaboration(self)
        from .configuration_authority import ConfigurationAuthority
        self.configuration_authority = ConfigurationAuthority(self)
        from .access_mode import AccessMode
        self.access_mode = AccessMode(self)
        from .assignments import Assignments
        self.assignments = Assignments(self)
        from .workspace_work import WorkspaceWork
        self.workspace_work = WorkspaceWork(self)
        from .workspace_work_invocation import WorkInvocation
        self.work_invocation = WorkInvocation(self)
        from .test_queue import TestQueue
        self.test_queue = TestQueue(self)
        from .peer_attribution import PeerAttribution
        from .peer_preview import PeerPreview
        self.peer_preview = PeerPreview(self)
        self.peer_attribution = PeerAttribution(self.db, self.peer_preview.describe)
        self.projections.resolver = self.peer_attribution.resolve
        from .schedules import Schedules
        self.schedules = Schedules(self)
        from .observations import Observations
        self.observations = Observations(self)
        from .worktrees import Worktrees
        self.worktrees = Worktrees(self)
        from .publishing import Publishing
        self.publishing = Publishing(self)
        from .portability import Portability
        self.portability = Portability(self)
        self._refresh_shared_preferences()
        from .operations import Operations
        self.operations = Operations(self)
        self._save_full(reason='Startup reconciles persisted stores and interrupted operations')
        if migrating_directory:
            # The one-time legacy checkpoint has now indexed every owner. No
            # application caller can yet hold a mutable owner alias, so retain
            # only startup obligations before accepting the first request.
            retained = {identity for identity, in self.db.execute('SELECT id FROM history_local_rows WHERE startup=1')}
            retained.add(self._state.get('selectedSessionId'))
            self._state['sessions'] = [item for item in self._state['sessions'] if item['id'] in retained]
            self._state['runtimeControl'] = {identity: value for identity, value in self._state.get('runtimeControl', {}).items()
                                            if identity in retained}
            self._session_projection_refs = {identity: value for identity, value in self._session_projection_refs.items()
                                             if identity in retained}
            self._saved_detail_facts = {identity: value for identity, value in self._saved_detail_facts.items()
                                       if identity in retained}
            self._view_cache.clear()
            self._message_preparation.entries.clear()
            self._execution_preparation.retain(())
            for identity in tuple(self.cold_display.recent):
                if identity not in retained:
                    self.cold_display.recent.pop(identity, None)
            self.projections.invalidate(state=self._state)
        self.cold_display.track_restored()
        from .status_emitter import create as create_status_emitter
        self.status_emitter = create_status_emitter(self)
        from .conversation_models import bind_runtime
        bind_runtime(self)

    def default_theme(self):
        for name in ("unified.amplifier.css", "default-theme.css"):
            path = Path(__file__).parent / "static" / name
            if path.exists():
                return path.read_text()
        return "/* Amplifier Unified uses the app's bundled default styling. */"

    def _refresh_shared_preferences(self):
        from collections import OrderedDict
        from .shared_settings import read_settings, settings_paths
        selected = next((row for row in self.state.get("workspaces", []) if row["id"] == self.state.get("selectedWorkspaceId")), {})
        workspace = selected.get("path") or self.state["settings"]["workspace"]
        app_bundle = self.state["settings"].get("appBundle")
        def stamp(path):
            try:
                st = path.stat()
                return (str(path), st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            except (FileNotFoundError, NotADirectoryError):
                return (str(path), None)
        def current_stamp():
            # Managed scopes and symlink targets can change independently of
            # the files. Preserve ordered scope identity and the output path.
            return (str(Path(workspace).expanduser().resolve()), app_bundle,
                    tuple((scope, stamp(path)) for scope, path in settings_paths(workspace).items()))
        stamp_value = current_stamp()
        if getattr(self, "_shared_preferences_stamp", None) == stamp_value:
            return
        if not hasattr(self, '_shared_preferences_cache'):
            self._shared_preferences_cache = OrderedDict()
        cache = self._shared_preferences_cache
        if stamp_value in cache:
            cache.move_to_end(stamp_value)
            preferences = copy.deepcopy(cache[stamp_value])
        else:
            settings = read_settings(workspace)
            voice = settings.get("voice", {})
            from .bundle_selection import defaults
            preferences = {
                'bundleDefaults': defaults(self.data_dir, workspace, app_bundle),
                'preferredVoice': voice.get("preferred_model", "gpt-live-1"),
                'fallbackVoice': voice.get("fallback_model", "gpt-realtime-2.1"),
                'voiceName': voice.get('voice','marin'),
                'fallbackVoiceName': voice.get('fallback_voice',''),
                'voiceInterruptions': voice.get('interruptions',True),
            }
            # Retain derived preferences only, never complete configuration or
            # credentials. A changing file must be read again on the next call.
            if current_stamp() == stamp_value:
                cache[stamp_value] = copy.deepcopy(preferences)
                while len(cache) > 128:
                    cache.popitem(last=False)
            else:
                stamp_value = None
        bundle_defaults = preferences['bundleDefaults']
        self.state["bundleDefaults"] = bundle_defaults
        self.state["settings"].update(
            bundle=bundle_defaults["effective"],
            preferredVoice=preferences['preferredVoice'],
            fallbackVoiceName=preferences['fallbackVoiceName'],fallbackVoice=preferences['fallbackVoice'],voiceName=preferences['voiceName'],voiceInterruptions=preferences['voiceInterruptions'])
        self._shared_preferences_stamp = stamp_value
        self._browser_snapshot = None
        # A different client can select a different workspace. Its preferences
        # do not change shared navigation or invalidate other clients' snapshots.

    @property
    def projections(self):
        from .state_projections import StateProjections
        if getattr(self, '_projections', None) is None:
            self._projections = StateProjections(service=self)
        return self._projections

    def state_context(self, *, complete=False):
        """Read-only active context; complete legacy snapshots are explicit."""
        if not complete:
            # Navigation, attention and workspace projections consume the same
            # admitted metadata. Prepare it once within this synchronous read,
            # as the bundled browser does, rather than per derived section.
            return self.directory.read_frame(self._state_context)
        return self._state_context(complete=True)

    def _state_context(self, *, complete=False):
        from .attention import snapshot
        from .workspace_navigation import snapshot as workspace_snapshot
        from .browser_state import navigation
        self._refresh_shared_preferences()
        result = dict(self.state)
        if not complete:
            result.update(self.projections.browser(self.state))
            result.pop('attentionRead', None)
            return self.clients.project(result)
        # This deliberately complete compatibility read is never used by the
        # ordinary browser shell. Durable owners stay authoritative off-resident.
        from .state_records import load as load_records
        from .session_projection import hydrate
        durable = load_records(self.db)
        hydrate(self.data_dir, durable, self.db)
        rows = {row['id']: row for row in durable.get('sessions', [])}
        residents = {row['id']: row for row in self.state['sessions']}
        result['sessions'] = [residents.get(metadata['id']) or {**rows.get(metadata['id'], {}), **metadata}
                              for metadata in self.directory.select(limit=-1, order='ordinal,id')]
        result["attention"] = snapshot(result)
        result.update(navigation(result))
        result["workspaceExplorer"] = workspace_snapshot(result)
        result.pop("attentionRead", None)
        return self.clients.project(result)

    def get_state(self):
        # Explicit full reads remain compatible; browser hot paths use pages.
        return copy.deepcopy(self.state_context(complete=True))

    @measured('browser.snapshot')
    def browser_state(self, session_id=None):
        # Full/session and non-web reads may hydrate an existing owner in place
        # midway through projection. Only the bound web display is read-only.
        client = self.clients.record()
        if session_id is not None or client is None or client['kind'] != 'web':
            return self._browser_state(session_id)
        return self.directory.read_frame(self._browser_state, session_id)

    def _browser_state(self, session_id=None):
        self._refresh_shared_preferences()
        client_id = self.clients.current.get()
        selected = session_id or self.state.get('selectedSessionId')
        if selected is not None:
            try:
                self._session(selected, hydrate=False)
            except AppError as exc:
                if exc.status != 404:
                    raise
        if client_id is not None:
            self.clients.reconcile(client_id)
            cached = self._client_snapshots.get(client_id)
            if (cached is None or cached['revision'] != self.state['revision'] or session_id is not None
                    or self._client_snapshot_preferences.get(client_id) != self._shared_preferences_stamp):
                from .browser_state import snapshot
                derived = self.projections.browser(self.state, selected_context=(
                    session_id is None and self.clients.records[client_id]['kind'] == 'web'))
                if session_id is not None:
                    self._session(session_id)
                cached = self.clients.project(snapshot(self.state, derived, session_id=session_id, index=self.projections.sessions(self.state),
                    copies=self._snapshot_copies if session_id is None else None, client_id=client_id,
                    detail_project=self.projections.detail, demand=self.clients.records[client_id]['kind'] == 'web', directory=self.directory))
                if self.clients.records[client_id]['kind'] == 'web':
                    # Descriptor identity exists before resource registration,
                    # including the first frame after a host restart. Clients
                    # can then discard the previous host's resources and load
                    # its new descriptors without an ambiguous missing key.
                    cached.update(self.shell.resources.browser_projection(client_id))
                else:
                    cached['shellDataKey'] = self.projections.shell_key(self.state)
                    cached['shellChangeToken'] = self.shell.change_token(client_id)
                if session_id is None:
                    self._client_snapshots[client_id] = cached
                    self._client_snapshot_preferences[client_id] = self._shared_preferences_stamp
            return cached
        cached = getattr(self, '_browser_snapshot', None)
        if cached is None or cached['revision'] != self.state['revision']:
            from .browser_state import snapshot
            derived = self.projections.browser(self.state)
            self._browser_snapshot = snapshot(self.state, derived, index=self.projections.sessions(self.state),
                                              copies=self._snapshot_copies, detail_project=self.projections.detail, directory=self.directory)
            self._browser_snapshot['shellDataKey'] = self.projections.shell_key(self.state)
        if session_id is not None:
            self._session(session_id)
            from .browser_state import snapshot
            derived = {key: self._browser_snapshot[key] for key in ('attention', 'workspaceExplorer',
                'chatNavigation', 'headerChatNavigation', 'subagentNavigation')}
            return snapshot(self.state, derived, session_id=session_id, index=self.projections.sessions(self.state), directory=self.directory)
        return self._browser_snapshot

    def session_state(self, session_id):
        """Single-session transport projection, independent of browser navigation."""
        try:
            row = self._session(session_id)
        except AppError as exc:
            if exc.status != 404:
                raise
            return {'revision': self.state['revision'], 'sessions': []}
        index = self.projections.sessions(self.state)
        session = copy.deepcopy(row)
        if session_id == self.state.get('selectedSessionId'):
            session['subagentCount'] = len(index.children(row))
        session['workers'] = [{key: value for key, value in worker.items() if key != 'reportReceipts'}
                              for worker in session.get('workers', [])]
        record = self.clients.record()
        if record is not None:
            session['draft'] = record.get('drafts', {}).get(session_id, '')
            session['draftAttachments'] = copy.deepcopy(record.get('attachments', {}).get(session_id, []))
        return {'revision': self.state['revision'], 'sessions': [session]}

    def get_actions(self):
        return [{"name": name, "description": desc, "inputSchema": copy.deepcopy(spec)} for name, (desc, spec) in ACTION_DEFINITIONS.items()]

    def forget_session_records(self, identities):
        """Stage explicit local record deletion; a missing resident is not deletion."""
        self._forgotten_session_ids = getattr(self, '_forgotten_session_ids', set()) | set(identities)

    @measured('state.save')
    def _save(self, *, session_ids=None, record_only=False):
        if session_ids is None:
            session_ids = getattr(self, '_publish_save_scope', None)
        worktrees = getattr(self, 'worktrees', None)
        if worktrees is not None:
            worktrees.resolve_placements(session_ids)
        if getattr(self, 'event_log_view', None) is not None:
            self.event_log_view.invalidate(session_ids, notify=False)
        if session_ids is None:
            self._history_local_revision += 1
        detail_only = getattr(self, '_publish_detail_only', False)
        record_only = bool(session_ids is not None and
                           (record_only or getattr(self, '_publish_record_only', False)))
        global_keys = getattr(self, '_publish_global_keys', set())
        if record_only and global_keys.intersection({'canvas', 'canvasArtifacts'}):
            from .canvas_apps import sync
            from .canvas_versions import sync as sync_versions
            sync(self)
            sync_versions(self)
        if not detail_only and not record_only:
            self.questions.sync()
            self.schedules.sync()
            self.worktrees.sync()
            self.portability.sync()
            from .canvas_apps import sync
            sync(self)
            from .canvas_versions import sync as sync_versions
            sync_versions(self)
        prior_revision = getattr(self, '_saved_revision', None)
        from .state_projections import detail_facts
        index = self.projections.sessions(self._state) if session_ids is not None else None
        facts = {sid: detail_facts(index.by_id[sid]) for sid in session_ids or () if sid in index.by_id}
        prior_facts = getattr(self, '_saved_detail_facts', {})
        narrow = bool(record_only and detail_only and session_ids is not None
                      and not global_keys
                      and all(prior_facts.get(sid) == facts.get(sid) for sid in session_ids))
        self._publish_client_narrow = narrow
        if getattr(self, '_projections', None) is not None:
            self._projections.invalidate(state=self.state, session_ids=session_ids,
                                         detail_only=narrow)
        from .state_storage import normalize_state
        index = self.projections.sessions(self.state) if session_ids is not None else None
        normalized = self.state if index is None else {
            **({key: self.state[key] for key in global_keys | ({'canvasTabs','selectedSessionId','selectedWorkspaceId'} if 'canvas' in global_keys else set()) if key in self.state} if record_only else self.state),
            'sessions': [index.by_id[key] for key in session_ids if key in index.by_id],
            'runtimeControl': {key: self._state.get('runtimeControl', {}).get(key, {})
                               for key in session_ids} if record_only else self._state.get('runtimeControl', {})}
        from .session_projection import persist
        pending_views = dict(self._view_cache)
        pending_messages = {}
        pending_execution = {}
        pending_references = dict(self._session_projection_refs)
        normalization_undo, view_undo = [], []
        from .cold_display import saved as saved_cold, MARKER
        from .state_records import save_working
        admitted_ids = ({row['id'] for row in self._state['sessions']}
                        if session_ids is None else set(session_ids))
        admitted_index = {row['id']: row for row in self._state['sessions']} if index is None else index.by_id
        # A scoped publication may name an owner whose metadata is still cold.
        # Absence from this working set is never permission to clear its record.
        admitted_ids.intersection_update(admitted_index)
        forgotten = set(getattr(self, '_forgotten_session_ids', ()))
        deleted_ids = self._deleted_session_ids | forgotten
        # File replacement may precede SQLite commit; it is not rolled back.
        # Private client acknowledgements and SQLite overlays become current
        # only after commit. A failed transaction retains all retry scopes.
        try:
            staged_work_inputs = []
            if getattr(self, 'workspace_work', None):
                from .workspace_work_coverage import stage_saved_inputs
                staged_work_inputs = stage_saved_inputs(self.workspace_work, admitted_index, admitted_ids, deleted_ids)
            with self.performance_diagnostics.measure('state.serialize', synchronous=True):
                normalize_state(normalized, self.db, undo=normalization_undo)
                saved = persist(self.data_dir, self._state, pending_views,
                                session_ids=admitted_ids, by_id=admitted_index,
                                references=pending_references, scoped_result=True,
                                undo=view_undo, db=self.db,
                                retain_native=lambda row: not self.directory.native_backing(row),
                                message_preparation=self._message_preparation, pending_messages=pending_messages,
                                execution_preparation=self._execution_preparation, pending_execution=pending_execution)
            pending_clients = self.clients.save(defer_ack=True)
            if record_only:
                # Persist sanitized global presentation just as full checkpoints
                # do (not the live canvas body that persist() externalized).
                record_state = {**self._state, **{key: saved[key] for key in global_keys if key in saved}}
                changed_global_keys = save_working(self.db, record_state, pending_references, admitted_ids, global_keys=global_keys,
                                                   deleted=deleted_ids)
            else:
                saved['runtimeControl'] = {identity: saved_cold(record)
                                           for identity, record in saved.get('runtimeControl', {}).items()}
                changed_global_keys = save_working(self.db, saved, pending_references, admitted_ids, deleted=deleted_ids)
            self.directory.sync((row for row in self._state['sessions']
                                 if row['id'] in admitted_ids and row['id'] in pending_references), self._state,
                                deleted=(admitted_ids - pending_references.keys()) | deleted_ids)
            self.directory.finish_migration()
            self.db.commit()
            if getattr(self, 'workspace_work', None):
                from .workspace_work_coverage import acknowledge_saved_inputs
                acknowledge_saved_inputs(self.workspace_work, staged_work_inputs)
        except BaseException:
            self.db.rollback()
            for record, key, value in reversed(normalization_undo):
                record[key] = value
            from .host.storage import SessionStore
            for path, text in reversed(view_undo):
                try:
                    if text is None:
                        path.unlink(missing_ok=True)
                    else:
                        SessionStore._atomic(path, text)
                except OSError:
                    # This presentation may already contain new blob roots.
                    # Mark storage uncertain until a successful save repairs it;
                    # GC below refuses collection while recovery is unresolved.
                    self._state['_viewRecoveryPending'] = True
                    self._view_cache.pop(str(path), None)
            self._browser_snapshot = None
            self._client_snapshots.clear()
            self._client_snapshot_preferences = {}
            raise
        self._message_preparation.committed(pending_messages, admitted_index, deleted_ids)
        self._execution_preparation.committed(pending_execution, admitted_index, deleted_ids)
        self._execution_preparation.retain({sid for sid, row in admitted_index.items()
                                           if sid not in deleted_ids and dict.__contains__(row, 'execution')
                                           and 'execution' not in dict.get(row, MARKER, {})})
        changed_clients = {identity for identity, value in pending_clients.items()
                           if self.clients.saved.get(identity) != value}
        self.clients.acknowledge(pending_clients)
        if 'workspaces' in changed_global_keys:
            # Native reconciliation may replace registrations without changing
            # their count. Refresh the admitted index on committed registry
            # changes, while retaining it during ordinary streaming updates.
            self.projections.values.pop(('session-index',), None)
        self.shell.resources.invalidate(changed_global_keys)
        self._view_cache = pending_views
        self._session_projection_refs = pending_references
        self._directory_migrated = True
        self._forgotten_session_ids = getattr(self, '_forgotten_session_ids', set()) - forgotten
        for identity in deleted_ids:
            self._session_projection_refs.pop(identity, None)
        if session_ids is None:
            self._state.pop('_viewRecoveryPending', None)
        self._saved_revision = self._state['revision']
        if session_ids is None:
            # Disposable facts, not another unbounded hot library. First update
            # of an untracked native row falls back to ordinary invalidation.
            selected = {record.get('selectedSessionId') for record in self.clients.records.values()}
            self._saved_detail_facts = {row['id']: detail_facts(row)
                for row in self._state['sessions']
                if not row.get('historyManaged') or row['id'] in selected}
        else:
            self._saved_detail_facts.update(facts)
        while len(self._saved_detail_facts) > 256:
            self._saved_detail_facts.pop(next(iter(self._saved_detail_facts)))
        self._advance_client_snapshots(session_ids, narrow, prior_revision, changed_clients)
        from .storage_migration import maintenance
        try:
            maintenance(self)
        except (OSError, ValueError, sqlite3.Error):
            # The durable revision is already committed. Optional maintenance
            # failure must not be reported as an uncommitted publication.
            self.db.rollback()
            logging.getLogger(__name__).warning('Storage maintenance deferred.', exc_info=True)

    def _affected_detail(self, session_ids):
        """One dependency boundary for queue routing and retained snapshots."""
        from .browser_state import direct_child
        affected = set(session_ids or ())
        index = self.projections.sessions(self._state)
        for identity in session_ids or ():
            row = index.by_id.get(identity, {})
            affected.add(row.get('parentId'))
            if row.get('nativeParentId'):
                for record in self.clients.records.values():
                    for sid in (record.get('selectedSessionId'),
                                (record.get('view', {}).get('subagentHistory') or {}).get('sessionId')):
                        if direct_child(row, index.by_id.get(sid)):
                            affected.add(sid)
        return affected - {None}

    def _advance_client_snapshots(self, session_ids, narrow, previous, local_changes=()):
        self._browser_snapshot = None
        if not narrow or previous is None:
            self._client_snapshots.clear()
            self._client_snapshot_preferences = {}
            return
        affected = self._affected_detail(session_ids)
        index = self.projections.sessions(self._state)
        for client, frame in list(self._client_snapshots.items()):
            record = self.clients.records.get(client, {})
            interests = {record.get('selectedSessionId'), self._state.get('voice', {}).get('sessionId'),
                         (record.get('view', {}).get('subagentHistory') or {}).get('sessionId')}
            local_changed = client in local_changes and (
                any(frame.get(key) != record.get(key) for key in
                    ('view', 'selectedSessionId', 'selectedWorkspaceId', 'canvas', 'deviceCommands'))
                or any(row.get('draft', '') != record.get('drafts', {}).get(row['id'], '')
                       or row.get('draftAttachments', []) != record.get('attachments', {}).get(row['id'], [])
                       for row in frame['sessions']))
            if local_changed or frame['revision'] != previous or interests.intersection(affected):
                self._client_snapshots.pop(client, None)
                self._client_snapshot_preferences.pop(client, None)
                continue
            # Preserve issued frames. Streaming recency is a scalar visible in
            # session summaries even though navigation ordering stays settled.
            rows = frame['sessions']
            if any(row['id'] in session_ids and row.get('recentActivityAt') !=
                   index.by_id[row['id']].get('recentActivityAt') for row in rows):
                rows = [{**row, 'recentActivityAt': index.by_id[row['id']].get('recentActivityAt')}
                        if row['id'] in session_ids else row for row in rows]
            self._client_snapshots[client] = {**frame, 'revision': self._state['revision'], 'sessions': rows}

    def _publish_full(self, *, reason):
        """Explicit catalog-wide reconciliation, never the default for a field edit."""
        if not reason:
            raise ValueError('Full state reconciliation requires a reason')
        self._last_full_save_reason = reason
        self._publish()

    def _save_full(self, *, reason):
        if not reason:
            raise ValueError('Full state reconciliation requires a reason')
        self._last_full_save_reason = reason
        self._save()

    def _save_changes(self, *, sessions=(), globals=()):
        """Commit declared records without visiting unrelated session history.

        The caller owns its lock, receipt and in-memory rollback. This does not
        flush or acknowledge an independently scheduled progress publication.
        """
        scope = (self._publish_save_scope, self._publish_detail_only,
                 self._publish_record_only, self._publish_global_keys) if hasattr(self, '_publish_save_scope') else (None, False, False, set())
        self._publish_save_scope = set(sessions)
        self._publish_detail_only = False
        self._publish_record_only = True
        self._publish_global_keys = set(globals)
        try:
            self._save()
        finally:
            (self._publish_save_scope, self._publish_detail_only,
             self._publish_record_only, self._publish_global_keys) = scope

    def _publish_changes(self, *, sessions=(), globals=(), detail_only=False):
        """Save only changed authority, then publish the affected projection.

        Empty sessions means no conversation writes. Catalog membership changes
        still require the explicit full reconciliation path. Pending progress
        scopes are joined by _publish, so a receipt never loses earlier work.
        """
        self._publish(session_ids=set(sessions), global_keys=set(globals),
                      record_only=True, detail_only=detail_only)

    @measured('state.publish')
    def _publish(self, *, session_ids=None, detail_only=False, record_only=False, global_keys=()):
        # Exact input receipts can change an independent-chat review displayed
        # in its source conversation. Join that projection scope to this same
        # canonical commit; do not publish from inside the delivery transaction.
        handoff_sources = set(getattr(getattr(self, 'handoffs', None), 'changed_sources', ()))
        if session_ids is not None:
            session_ids = set(session_ids) | handoff_sources
        if getattr(self, 'event_log_view', None) is not None:
            self.event_log_view.invalidate(session_ids, notify=False)
        # A mixed publication commits the union, never just the newer writer's
        # scope. Unknown writers retain the conservative complete-save path.
        if getattr(self, '_progress_dirty', False):
            pending = (getattr(self, '_progress_session_ids', None)
                       if getattr(self, '_progress_scope_known', False) else None)
            session_ids = (set(session_ids) | pending
                           if session_ids is not None and pending is not None else None)
            detail_only = detail_only and getattr(self, '_progress_detail_only', False)
            record_only = record_only and getattr(self, '_progress_record_only', False)
            global_keys = set(global_keys) | getattr(self, '_progress_global_keys', set())
        task = getattr(self, '_progress_publish_task', None)
        if task and task is not asyncio.current_task():
            task.cancel()
            self._progress_publish_task = None
        authority = getattr(self, 'configuration_authority', None)
        if authority is not None:
            for owner in self.state['sessions']:
                if session_ids is None or owner['id'] in session_ids:
                    authority.reconcile(owner)
        previous = self.state["revision"]
        self.state["revision"] = previous + 1
        self._publish_save_scope = session_ids
        self._publish_detail_only = detail_only and session_ids is not None
        self._publish_record_only = record_only and session_ids is not None
        self._publish_global_keys = set(global_keys)
        try:
            # Keep the original no-argument boundary for host save hooks and
            # error-injection checks. The owned scope is visible only while
            # this synchronous publication commits.
            self._save()
        except Exception:
            self.state["revision"] = previous
            self._browser_snapshot = None
            # Retain every attempted writer, including a newly introduced
            # scoped commit. An ordinary retry flush must not omit that record.
            self._progress_dirty = True
            self._progress_scope_known = True
            self._progress_session_ids = set(session_ids) if session_ids is not None else None
            self._progress_detail_only = detail_only and session_ids is not None
            self._progress_record_only = record_only and session_ids is not None
            self._progress_global_keys = set(global_keys)
            raise
        finally:
            self._publish_save_scope = None
            self._publish_detail_only = False
            self._publish_record_only = False
            self._publish_global_keys = set()
        # The save guard compares all shared navigation facts. A requested
        # detail publication must broaden when status/attention actually changed.
        if handoff_sources:
            self.handoffs.changed_sources.difference_update(handoff_sources)
        if getattr(self, 'status_emitter', None):
            self.status_emitter.publish(session_ids)
        if record_only:
            detail_only = self._publish_client_narrow
        published = {}
        affected_detail = self._affected_detail(session_ids) if session_ids is not None else set()
        if detail_only and session_ids is not None:
            index = self.projections.sessions(self.state)
            affected_detail.update(index.by_id[key].get('parentId') for key in session_ids
                                   if key in index.by_id)
            affected_detail.discard(None)
        for queue in self.queues:
            key = (self.queue_clients.get(queue), self.queue_sessions.get(queue))
            if session_ids is not None and key[1] is not None and key[1] not in session_ids:
                continue
            if detail_only and session_ids is not None and key[1] is None and key[0] is not None:
                record = self.clients.records.get(key[0], {})
                interested = {record.get('selectedSessionId'), self._state.get('voice', {}).get('sessionId'),
                              (record.get('view', {}).get('subagentHistory') or {}).get('sessionId')}
                if not interested.intersection(affected_detail):
                    continue
            if key not in published:
                with self.clients.bind(key[0]):
                    published[key] = self.session_state(key[1]) if key[1] is not None else self.browser_state()
            snapshot = published[key]
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)
        if hasattr(self, "coordination"):
            self.coordination.notify()
        if self.update_manager:
            self.update_manager.notify_idle()
        # Only after the publication committed and its snapshots were handed
        # out. Dirty/error retries remain hot; retirement itself emits no work.
        try:
            self.cold_display.retire()
        except (OSError, ValueError, sqlite3.Error):
            # Publication already committed. Optional cold retirement failure
            # cannot turn its successful durable command into a failed receipt.
            logging.getLogger(__name__).warning('Cold display retirement deferred; resident data kept.', exc_info=True)
        self._progress_dirty = False
        self._progress_session_ids = set()
        self._progress_scope_known = False
        self._progress_detail_only = True
        self._progress_record_only = True
        self._progress_global_keys = set()
        self._progress_publish_error = None
        if getattr(self, 'event_log_view', None) is not None:
            self.event_log_view.flush_work_notifications()
        from .history_residency import retire
        try:
            retire(self)
        except (OSError, ValueError, sqlite3.Error):
            logging.getLogger(__name__).warning('History owner retirement deferred; saved and resident state retained.', exc_info=True)

    def _publish_progress(self, *, session_ids=None, detail_only=False, record_only=False, global_keys=(), stream=False):
        """Batch stream/progress updates; final responses and approvals flush now."""
        if getattr(self, 'event_log_view', None) is not None:
            self.event_log_view.invalidate(session_ids, notify=False)
        if not getattr(self, '_progress_dirty', False):
            self._progress_session_ids = set(session_ids) if session_ids is not None else None
            self._progress_detail_only = detail_only
            self._progress_record_only = record_only
            self._progress_global_keys = set(global_keys)
        else:
            pending = (getattr(self, '_progress_session_ids', None)
                       if getattr(self, '_progress_scope_known', False) else None)
            self._progress_session_ids = (pending | set(session_ids)
                                         if pending is not None and session_ids is not None else None)
            self._progress_detail_only = getattr(self, '_progress_detail_only', False) and detail_only
            self._progress_record_only = getattr(self, '_progress_record_only', False) and record_only
            self._progress_global_keys = getattr(self, '_progress_global_keys', set()) | set(global_keys)
        self._progress_dirty = True
        self._progress_scope_known = True
        # Only a live subscriber viewing this text needs the shorter batch.
        # Retained/offline clients and hidden workers keep the background window.
        delay = .05 if stream and self._stream_is_observed(session_ids) else .25
        due = asyncio.get_running_loop().time() + delay
        if getattr(self, '_progress_publish_task', None) is None:
            self._progress_publish_due = due
            self._progress_publish_wake = asyncio.Event()
            self._progress_publish_task = self._task(self._flush_progress())
        elif due < getattr(self, '_progress_publish_due', float('inf')):
            self._progress_publish_due = due
            wake = getattr(self, '_progress_publish_wake', None)
            if wake is not None:
                wake.set()

    def _stream_is_observed(self, session_ids):
        if not session_ids or not self.queues:
            return False
        affected = self._affected_detail(session_ids)
        for queue in self.queues:
            session = self.queue_sessions.get(queue)
            if session is not None:
                if session in affected:
                    return True
                continue
            client = self.queue_clients.get(queue)
            record = self.clients.records.get(client, {}) if client is not None else self._state
            interests = {record.get('selectedSessionId'),
                         self._state.get('voice', {}).get('sessionId'),
                         (record.get('view', {}).get('subagentHistory') or {}).get('sessionId')}
            if interests.intersection(affected):
                return True
        return False

    def _publish_smart_tool_update(self, *, defer_publish=False):
        """Commit tool receipts before effects/results, batching only the UI snapshot."""
        if defer_publish:
            self.db.commit()
            self._publish_progress(session_ids=set(), record_only=True, global_keys={'smartTools','canvas','canvasArtifacts','view','events','deviceCommands'})
        else:
            self._publish_changes(globals={'smartTools','canvas','canvasArtifacts','view','events','deviceCommands'})

    async def _flush_progress(self):
        try:
            # A newly observed stream may shorten an existing background wait.
            # Later events never move the deadline out, so bursts cannot starve it.
            while True:
                remaining = self._progress_publish_due - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self._progress_publish_wake.wait(), remaining)
                    self._progress_publish_wake.clear()
                except asyncio.TimeoutError:
                    break
            async with self.lock:
                if self._progress_dirty and not self.closed:
                    try:
                        self._commit_pending_progress()
                    except Exception as exc:
                        # Retain dirty data for the next transition or shutdown.
                        self._progress_publish_error = str(exc)
        finally:
            if self._progress_publish_task is asyncio.current_task():
                self._progress_publish_task = None

    async def _flush_pending_progress(self):
        """Publish pending runtime mutations before revision-sensitive access.

        A coalesced update already changed internal state. Externally observed
        reads and compare-and-set commands must first give it a durable revision.
        Internal synchronous readers remain available to code owning the state.
        """
        task = getattr(self, '_progress_publish_task', None)
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._progress_publish_task is task:
                self._progress_publish_task = None
        async with self.lock:
            if getattr(self, '_progress_dirty', False):
                self._commit_pending_progress()
                return True
        return False

    def _commit_pending_progress(self):
        scope = getattr(self, '_progress_session_ids', None)
        if scope is None:
            # Existing host observers wrap the public no-argument boundary.
            # Unknown/global writers retain that exact boundary and full scope.
            self._publish()
        else:
            self._publish(session_ids=scope,
                          detail_only=getattr(self, '_progress_detail_only', False),
                          record_only=getattr(self, '_progress_record_only', False),
                          global_keys=getattr(self, '_progress_global_keys', set()))

    def subscribe(self, session_id=None):
        queue = asyncio.Queue(maxsize=4)
        self.queues.add(queue)
        self.queue_tokens[queue] = uuid.uuid4().hex
        self.queue_clients[queue] = self.clients.current.get()
        self.queue_sessions[queue] = session_id
        return queue

    def unsubscribe(self, queue):
        self.queues.discard(queue)
        self.queue_tokens.pop(queue, None)
        client_id = self.queue_clients.pop(queue, None)
        if client_id and client_id not in self.queue_clients.values():
            self.computer_visual.reconcile(client_id, disconnect=True)
        self.queue_sessions.pop(queue, None)

    def _admit_session(self, metadata):
        """Admit one exact owner; summaries and paging never call this method."""
        identity = metadata['id']
        existing = next((row for row in self._state['sessions'] if row['id'] == identity), None)
        if existing is not None:
            return existing
        from .state_records import load_owner
        saved, control = load_owner(self.db, identity)
        compacted = False
        try:
            if saved is not None:
                from .session_projection import hydrate
                context = {'sessions': [copy.deepcopy(saved)], 'runtimeControl': {identity: control} if control is not None else {}}
                hydrate(self.data_dir, context, self.db)
                from .cold_display import compact_execution_references
                reference = dict.get(context['sessions'][0], '_coldFields', {}).get('execution', {})
                # Optional legacy compaction cannot commit a caller's in-flight
                # command. When this admission owns the transaction, its new blob
                # and exact owner pointer commit together before returning.
                compacted = bool(reference and reference.get('executionProjection') != 1
                                 and not self.db.in_transaction)
                if compacted:
                    compact_execution_references(context, self.db)
                session = context['sessions'][0]
                # Hydration owns whether a body is actually present. Directory
                # metadata cannot turn an unloaded native view into a loaded one.
                session.update({key: value for key, value in metadata.items()
                                if key not in {'historyLoaded', 'historyLoading'}})
                if control is not None:
                    self._state.setdefault('runtimeControl', {})[identity] = context['runtimeControl'][identity]
                self._session_projection_refs[identity] = saved
                # Lifecycle recovery belongs to keyed admission as well as startup.
                # A dormant owner must not retain a pre-restart executing state or
                # gain permission to replay a command when it is first selected.
                from .session_ownership import restore
                from .capacity import restore_observation
                restore_observation(session)
                restore(session)
                settle_stream(session)
                restore_voice_usage(session)
                interrupt_unfinished(session)
                from .management import Management
                session['configurationBusy'] = Management.unresolved_configuration(session)
                if session.get('status') in {'working', 'starting', 'ready', 'stopping'}:
                    session['status'] = 'interrupted'
                    session['activity'] = {'phase': 'interrupted', 'label': 'Previous work was interrupted; it has not been replayed.',
                                           'activeTools': [], 'updatedAt': time.time()}
                if session.get('naming', {}).get('status') == 'working':
                    session['naming'] = {'status': 'error', 'error': 'Name generation was interrupted. You can try again.'}
                if session.get('bundleChange', {}).get('phase') == 'working':
                    session['bundleChange'] = {'phase': 'error', 'error': 'The app restarted during a bundle change. Load the conversation and preview again; work was not replayed.'}
                session.pop('bundlePreview', None)
                for message in session.get('messages', []):
                    if (message.get('steering') or {}).get('disposition') in {'sending', 'queued'}:
                        from .conversation_steering import record as record_steering
                        record_steering(self, session, message.get('inputId'), 'unknown',
                            'The app restarted before steering application was confirmed. Nothing was resent.', preserve_pending_post=True)
                    if message.get('delivery', {}).get('status') == 'sending':
                        self._delivery(session, message.get('inputId'), 'unknown')
                for worker in session.get('workers', []):
                    if worker.get('status') in {'working', 'running', 'starting', 'queued', 'pending', 'stopping'} or worker.get('persistent') and worker.get('status') == 'idle':
                        worker['status'] = 'interrupted'
            else:
                if self.db.execute('SELECT 1 FROM history_local_rows WHERE id=?', (identity,)).fetchone():
                    raise ValueError('The saved conversation record is unavailable; its directory entry was retained.')
                session = self.cold_display.record({**metadata, 'historyLoaded': False, 'historyLoading': False,
                                                    'messages': [], 'workers': [], 'approvals': []})
            from .collaboration import restore_owner
            restore_owner(session, self.instance_id)
            authority = getattr(self, 'configuration_authority', None)
            if authority is not None:
                compacted = authority.reconcile(session) or compacted
            self._state['sessions'].append(session)
            self.cold_display.touch(identity)
            self.projections.invalidate(state=self._state, session_ids={identity})
            # Durable stores remain authoritative when an owner was not resident at
            # startup. Reapply their fences without starting a worker or schedule.
            for name in ('questions', 'schedules', 'worktrees', 'portability'):
                owner = getattr(self, name, None)
                if owner is not None:
                    owner.sync()
            if compacted:
                self._save(session_ids={identity}, record_only=True)
            return session
        except BaseException:
            if compacted:
                self.db.rollback()
                self._state['sessions'][:] = [row for row in self._state['sessions'] if row['id'] != identity]
                self._state.get('runtimeControl', {}).pop(identity, None)
                self._session_projection_refs.pop(identity, None)
                self.projections.invalidate(state=self._state, session_ids={identity})
            raise

    def _session(self, sid=None, *, hydrate=True):
        sid = sid or self.state["selectedSessionId"]
        from .session_identity import resolve
        try:
            projections = getattr(self, '_projections', None)
            index = projections.values.get(('session-index',)) if projections is not None else None
            session = index.by_id.get(sid) if index is not None else None
            position = index.positions.get(sid) if session is not None else None
            if (position is None or position >= len(self.state['sessions'])
                    or self.state['sessions'][position] is not session):
                session = next((row for row in self.state['sessions'] if row['id'] == sid), None)
            # Exact app IDs are durable receipt identities. Alias lookup must
            # inspect the complete directory, including dormant duplicate IDs.
            if session is None:
                metadata = self.directory.resolve(sid)
                if metadata is not None:
                    session = self._admit_session(metadata)
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
        if session:
            if session.get('_deleting'):
                raise AppError('This chat is being deleted. No new work was started.', 409)
            return self.cold_display.hydrate(session) if hydrate else session
        raise AppError("Select or create a conversation first.", 404)

    def _session_read_sources(self, identities, fields):
        """Read bounded metadata references without admitting dormant owners.

        Optional row labels and plans are not runtime demand. Even
        _session(hydrate=False) would grow the live overlay by a viewed page.
        """
        def collect():
            known = {row['id']: row for row in self.state['sessions']}
            missing = [identity for identity in identities if identity not in known]
            known.update((row['id'], row) for row in self.directory.summary_ids(missing))
            sources = []
            for identity in identities:
                try:
                    session = known.get(identity) or self.directory.resolve(identity)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if session is None:
                    raise AppError('Select or create a conversation first.', 404)
                session = known.get(session['id'], session)
                if session.get('_deleting'):
                    raise AppError('This chat is being deleted. No new work was started.', 409)
                sources.append({key: session[key] for key in fields if key in session})
            return sources
        return self.directory.read_frame(collect)

    def _session_destination(self, args):
        selected = next((w for w in self.state.get('workspaces', []) if w['id'] == self.state.get('selectedWorkspaceId')), {})
        if not args.get('workspace') and selected.get('available') is False:
            raise AppError('This project folder is unavailable. Choose an existing workspace to start work.')
        return str(Path(args.get("workspace") or selected.get("path") or self.state["settings"]["workspace"]).expanduser().resolve())

    def _require_workspace_scaffold(self, workspace):
        target = next((w for w in self.state['workspaces'] if w.get('path') == workspace), {})
        if not target.get('setupId'):
            return
        from .workspace_provisioning import inspect as inspect_setup
        try:
            setup = inspect_setup(self.data_dir, target['setupId'])
        except (ValueError, OSError):
            raise AppError('Workspace setup cannot be confirmed. Inspect its readiness before starting a chat.', 409) from None
        if not setup.get('scaffolded'):
            raise AppError('This workspace starter is still preparing. Wait for its readiness result before starting a chat.', 409)

    def _new_session(self, args, *, new_root=False):
        from .shared_settings import read_settings
        workspace = self._session_destination(args)
        try:
            self.worktrees.require_available_path(workspace)
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
        self._require_workspace_scaffold(workspace)
        if not Path(workspace).is_dir():
            raise AppError("The workspace folder does not exist.")
        from .bundle_selection import defaults
        selected_bundle = defaults(self.data_dir, workspace, self.state["settings"].get("appBundle"))["effective"]
        from .new_chat import selection
        try:
            chosen = selection(args.get('selection', {}))
        except ValueError as exc:
            raise AppError(str(exc)) from None
        now = time.time()
        # Only ordinary public creation establishes placement proof. This
        # constructor also builds historical import views under existing IDs.
        placement = ({'sessionKind': 'internal', 'sessionPurpose': args['purpose']} if args.get('purpose')
                     else {'sessionKind': 'root', 'workingDirectory': workspace,
                           'executionRevision': 0, 'interruptionRevision': 0} if new_root else {})
        return self.cold_display.record({**({'selection': chosen} if chosen else {}), **({'location': {'kind': 'managed'}} if args.get('location', {}).get('kind') == 'managed' else {}), "id": str(uuid.uuid4()), "title": args.get("title") or "New chat", "titleSource":"manual" if args.get("title") and args["title"] not in {"New chat","New conversation","A new conversation","Untitled conversation"} else "automatic", "bundle": args.get("bundle") or selected_bundle, "workspace": workspace, "status": "idle", "createdAt": now, "recentActivityAt": now, "messages": [], "workers": [], "approvals": [], '_collaborationHostInstanceId': self.instance_id, **placement})

    def _reusable_internal(self, candidate):
        """An idle internal session for the same purpose, folder and bundle."""
        offset = 0
        while True:
            rows = self.directory.select(
                "session_kind='internal' AND purpose=? AND workspace=? AND status IN ('idle','ready')",
                (candidate['sessionPurpose'], candidate['workspace']), limit=16, offset=offset, state=self.state)
            for row in rows:
                if (row.get('bundle') == candidate['bundle'] and not row.get('configurationBusy')
                        and not any(a.get('status') == 'pending' for a in row.get('approvals', []))):
                    # Reuse is an explicit request for this one mutable owner;
                    # unrelated dormant candidates remain metadata only.
                    return self._session(row['id'], hydrate=False)
            if len(rows) < 16:
                return None
            offset += len(rows)

    def _message(self, session, role, text, via="chat", *, human_post=False, **extra):
        message = {"id": str(uuid.uuid4()), "role": role, "text": text, "via": via, "createdAt": time.time(), **extra}
        session["messages"].append(message)
        if getattr(self, 'workspace_work', None):
            from .workspace_work_coverage import observe
            observe(self.workspace_work, session, message)
        from .chat_navigation import touch, touch_independent_activity
        # Only ordinary send has prepared a trusted UI admission. Other inputs,
        # including UI host actions, must retain their own raw progress truth.
        (touch if human_post else touch_independent_activity)(session)
        self.diagnostics.record('conversation',{'event':'prompt:submit' if role=='user' else 'prompt:complete','data':{'id':message['id'],'role':role,'prompt' if role=='user' else 'response':text,'inputId':extra.get('inputId')}},session_id=session.get('runtimeSessionId') or session['id'],workspace=session['workspace'])
        return message

    def _activity(self, session, phase, label, *, reset=False):
        now = time.time()
        previous = session.get("activity", {})
        session["activity"] = {"phase": phase, "label": label,
            "startedAt": now if reset else previous.get("startedAt", now), "updatedAt": now,
            "activeTools": [] if reset else previous.get("activeTools", []),
            "lastEvent": None if reset else previous.get("lastEvent")}
        return session["activity"]

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def bind_runtime_alias(self, setter):
        """Keep an embedding host's runtime reference aligned with this service."""
        self._runtime_alias = setter
        setter(self.runtime)

    @asynccontextmanager
    async def runtime_lifecycle(self):
        """Serialize and track a runtime-retiring operation during host shutdown."""
        if self.closed:
            raise RuntimeError("The runtime host is closing.")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Runtime lifecycle work requires an asyncio task.")
        self._runtime_lifecycle_tasks.add(task)
        try:
            async with self.runtime_lifecycle_lock:
                # A task can begin after close() has taken its lifecycle-task
                # snapshot, or wait here while shutdown begins.  Reject it after
                # it owns the exclusion, before it can mutate state or run work.
                if self.closed:
                    raise RuntimeError("The runtime host is closing.")
                yield
        finally:
            self._runtime_lifecycle_tasks.discard(task)

    def runtime_candidate(self, *, retention=None):
        """Construct a replacement without mutating or retiring the live runtime."""
        from .runtime import RuntimeManager

        current = self.runtime
        if isinstance(current, RuntimeManager):
            return RuntimeManager(
                app_bridge=current.app_bridge,
                command=current.command,
                startup_timeout=current.startup_timeout,
                progress_interval=current.progress_interval,
                retention=retention if retention is not None else dict(current.retention.settings),
                performance_diagnostics=self.performance_diagnostics,
            )
        from .deployment import load_server_config
        config = getattr(self, "server_config", None) or load_server_config(self.data_dir)
        return RuntimeManager(app_bridge=self.app_bridge,
                              performance_diagnostics=self.performance_diagnostics,
                              retention=retention if retention is not None else config["runtime"])

    async def install_runtime(self, runtime):
        """Publish a prepared runtime consistently to the service and embedding host."""
        if self.closed:
            if runtime:
                await runtime.close()
            raise RuntimeError("The runtime host is closing.")
        self.runtime = runtime
        if runtime is not None and hasattr(runtime, "home"):
            runtime.home = self.data_dir
        from .next_message_configuration import bind
        bind(self, runtime)
        from .update_drain import bind as bind_update
        bind_update(self, runtime)
        self.worktrees.bind_runtime()
        from .conversation_models import bind_runtime
        bind_runtime(self)
        self.state["runtime"]["available"] = runtime is not None
        if hasattr(runtime, "retention"):
            self.state["runtime"]["retention"] = dict(runtime.retention.settings)
        if self._runtime_alias:
            self._runtime_alias(runtime)

    async def replace_runtime(self, candidate):
        """Switch to a preflighted runtime before terminally closing the old one."""
        if self.closed:
            await candidate.close()
            raise RuntimeError("The runtime host is closing.")
        previous = self.runtime
        await self.install_runtime(candidate)
        if previous:
            close_task = asyncio.create_task(previous.close())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                # Shutdown cancels the lifecycle operation, but the displaced
                # runtime must finish its own cleanup before the new one closes.
                await asyncio.gather(close_task, return_exceptions=True)
                raise
        return previous

    async def discard_runtime(self, candidate):
        """Close an unused replacement candidate without retiring the live runtime."""
        if candidate is not None and candidate is not self.runtime and not getattr(candidate, "_closed", False):
            await candidate.close()

    async def dispatch(self, action, args=None, origin="ui", command_id=None, expected_revision=None, *, include_state=True, caller_session_id=None, admission_check=None):
        # Directory preparation yields outside the service lock. Keep concurrent
        # retries on the same creation command behind its durable receipt, so a
        # conflicting retry cannot create a second folder during that interval.
        reservation = None
        if action == 'session.create':
            from .managed_chats import is_managed
            if not (command_id and self.db.execute('SELECT 1 FROM commands WHERE id=?', (command_id,)).fetchone()):
                try:
                    paths = [] if is_managed(args) else [self._session_destination(args or {})]
                    from .collaboration import CREATION
                    creation = CREATION.get()
                    if creation and creation[:2] == (caller_session_id, hashlib.sha256(
                            json.dumps(args or {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()):
                        paths.append(creation[4]['workingDirectory'])
                    if paths:
                        reservation = self.worktrees.reserve_creation(*paths)
                except (ValueError, OSError) as exc:
                    raise AppError(str(exc), 409) from None
        try:
            if action in {'session.create', 'coordination.create', 'workspace.setup.reconcile'} and command_id:
                lock = self._creation_locks.setdefault(command_id, asyncio.Lock())
                async with lock:
                    return await self._dispatch(action, args, origin, command_id, expected_revision, include_state=include_state, caller_session_id=caller_session_id, admission_check=admission_check)
            return await self._dispatch(action, args, origin, command_id, expected_revision, include_state=include_state, caller_session_id=caller_session_id, admission_check=admission_check)
        finally:
            self.worktrees.release_creation(reservation)

    async def _dispatch(self, action, args=None, origin="ui", command_id=None, expected_revision=None, *, include_state=True, caller_session_id=None, admission_check=None):
        args = dict(args or {})
        # Four exact passive task actions (including their public alias) use
        # directory metadata plus canonical control-state only. Keep the normal
        # validators and caller guards; never refresh history/admit an overlay.
        passive_task = action in {'task.get', 'task.history', 'task.detail', 'task.receipt'} or (
            action == 'runtime.control' and isinstance(args.get('operation'), str) and args['operation'] in
            {'task.get', 'task.history', 'task.detail', 'task.receipt'})
        passive_metadata = passive_task or action == 'assignment.brief'
        if action in {'workspace.environments.removal.read', 'workspace.environments.removal.list'}:
            # Exact retained IDs only. General alias resolution can refresh
            # history and admit a dormant owner before the passive read runs.
            try:
                validate(args, ACTION_DEFINITIONS[action][1])
            except ValidationError as exc:
                raise AppError(exc.message) from None
            from .task_environments import dispatch as environment_dispatch
            return await environment_dispatch(self, action, args, origin, caller_session_id, None)
        for key in ("sessionId", "id") if action in SESSION_ID_ACTIONS else ("sessionId",):
            if key == "sessionId" and args.get(key) is None and action in {
                    "canvas.visibility", "view.update", "attachment.add", "attachment.remove"}:
                # These public schemas permit a client draft before a chat exists.
                # Leave Canvas open/close semantics to its scope-specific guard.
                continue
            if key in args and (not isinstance(args[key], str) or not args[key].strip()):
                raise AppError("Choose a nonempty conversation identity.", 400)
        from .session_identity import resolve
        if args.get('configurationTarget') and args.get('nativeProject'):
            raise AppError('Choose one configuration target.')
        scope = args.pop('nativeProject', None)
        if scope is not None and (not isinstance(scope, str) or not scope or len(scope) > 4000):
            raise AppError('Choose a valid native project.')
        resolved_target = None
        target_key = 'sessionId' if args.get('sessionId') else 'id' if action in SESSION_ID_ACTIONS else None
        if target_key and args.get(target_key):
            if scope is None and origin == 'ui':
                exact = next((row for row in self.state['sessions'] if row['id'] == args[target_key]), None)
                if exact:
                    from .session_identity import project as native_project
                    scope = native_project(exact)
            try:
                target = self.directory.resolve(args[target_key], scope,
                                                include_hidden_workspaces=action == 'session.select' and not {'chatReference', 'peerPreview'} & args.keys())
                if target is None and not passive_metadata:
                    await self.history.refresh()
                    target = self.directory.resolve(args[target_key], scope,
                                                    include_hidden_workspaces=action == 'session.select' and not {'chatReference', 'peerPreview'} & args.keys())
            except ValueError as exc:
                raise AppError(str(exc), 409) from None
            if scope and target is None:
                raise AppError('Session is not in the requested native project.', 404)
            if target:
                args[target_key] = target['id']
                resolved_target = target
        elif scope:
            raise AppError('Supply a session ID with nativeProject.')
        if action == 'view.update' and origin == 'agent' and 'draft' in args.get('patch', {}):
            # A connected browser can retain drafts for many chats. Its visible
            # chat is checked separately, so it is not authority to overwrite
            # any other saved draft (including the pre-conversation draft).
            if not caller_session_id or args.get('sessionId', caller_session_id) != caller_session_id:
                raise AppError('Agent draft updates must target the calling conversation.', 409)
            args.setdefault('sessionId', caller_session_id)
        if origin not in {"ui", "user", "scheduler"} and caller_session_id and action in {
                "conversation.send", "conversation.stop", "worker.spawn", "worker.message", "worker.steer", "worker.stop"}:
            args.setdefault("sessionId", caller_session_id)
        # Keep older shells/agents on the same non-committing launcher.
        if action == 'view.update' and args.get('patch', {}).get('panel') == 'new-session':
            action, args = 'session.draft', {}
        client_id = self.clients.current.get()
        if client_id is not None and action in {'conversation.send', 'conversation.stop', 'worker.spawn', 'worker.stop', 'worker.steer', 'worker.message', 'approval.respond', 'attachment.add', 'attachment.remove'}:
            args.setdefault('sessionId', self.state.get('selectedSessionId'))
            if not args['sessionId'] and action not in {'attachment.add', 'attachment.remove'}:
                raise AppError('Select a chat first.', 404)
        if action == 'runtime.control' and args.get('operation', '').startswith(('kernels.', 'operations.')):
            raise AppError('Use the shared computation and operation actions; their ownership and dependency checks cannot be bypassed.', 403)
        if action == 'runtime.control' and args.get('operation', '').startswith('context.recovery.'):
            if origin == 'agent' and (not caller_session_id or args.get('sessionId') != caller_session_id):
                raise AppError('Context recovery belongs to the calling conversation.', 409)
            from .collaboration import PRINCIPAL
            if origin == 'agent' and PRINCIPAL.get() not in {None, caller_session_id}:
                raise AppError('A child cannot borrow its parent context recovery authority.', 403)
        if action == 'runtime.control' and args.get('operation') == 'tool.invoke':
            if origin == 'agent' and (not caller_session_id or args.get('sessionId') != caller_session_id):
                raise AppError('Tool invocation must target the calling conversation.', 409)
            from .tool_authority import require_generic_tool
            invocation = dict(args.get('args') or {})
            try:
                require_generic_tool(invocation.get('name'), invocation.get('arguments', {}))
            except ValueError as exc:
                raise AppError(str(exc), 403) from None
            # Actor is host provenance, never a claim from the request body.
            args['args'] = {**invocation, 'actor': origin}
        if action == 'runtime.control' and args.get('operation', '').startswith(('schedule.', 'observation.', 'coordination.')):
            raise AppError('Use the shared schedule actions; direct scheduled input admission is internal.', 403)
        if action == 'runtime.control' and args.get('operation', '').startswith(('configuration.operation.', 'configuration.effect.')):
            raise AppError('Use the operation-bound configuration recovery actions.', 403)
        if action == 'runtime.control' and args.get('operation', '').startswith('memory.'):
            raise AppError('Use the shared memory controls; model consolidation is internal.', 403)
        if action == 'runtime.control' and args.get('operation', '').startswith(('task.', 'capacity.')):
            action, args = args['operation'], {**args.get('args', {}), 'sessionId': args.get('sessionId')}
        defer_publish = action == 'smartTools.appCall' and not include_state
        if action.startswith(("smartTools.","providers.")) or action in {'updates.featureInstall', 'updates.forceRestart'} or action.startswith('permissions.') and args.get('scope') == 'session':
            command_id = command_id or str(uuid.uuid4())
        if action not in ACTION_DEFINITIONS:
            raise AppError("Unknown action: " + action, 404)
        try:
            validate(args, ACTION_DEFINITIONS[action][1])
            if action.startswith(CONFIGURATION_PREFIXES) and args.get("configurationTarget"):
                from .configuration_target import resolve as configuration_target
                resolved = configuration_target(self, args)
                args["scope"] = resolved["scope"]
        except ValidationError as exc:
            if action.startswith('feedback.excerpt.'):
                message = 'Choose a smaller excerpt, up to 64 KB. Nothing was sent.' if list(exc.path) == ['text'] else 'Invalid feedback excerpt request. Nothing was sent.'
                raise AppError(message) from None
            raise AppError(exc.message) from exc
        except ValueError as exc:
            raise AppError(str(exc)) from None
        from .workspace_work_effects import guard as work_effect_guard
        work_effect_guard(self, action, args, origin, caller_session_id)
        if action == 'canvas.openGif' and (origin != 'ui' or client_id is None
                or args.get('clientId', client_id) != client_id
                or self.clients.record().get('kind') != 'web'):
            raise AppError('Open in Canvas requires an explicit click in the originating browser client.', 403)
        if action == 'session.select' and {'chatReference', 'peerPreview'} & args.keys() and (
                origin != 'ui' or client_id is None or self.clients.record().get('kind') != 'web'):
            raise AppError('A chat reference opens only from its originating browser.', 403)
        if action == "approval.respond" and args["id"].startswith("capability:"):
            return await self.configuration_authority.decide(args, origin, command_id)
        guard_history_action(self, action, args, origin, caller_session_id)
        if passive_task and origin not in {'ui', 'user'}:
            if not caller_session_id:
                raise AppError('Task reads require a registered calling conversation.', 403)
            from .owned_history import require
            require(self, caller_session_id, args['sessionId'])
        if action == 'peer.preview':
            async with self.lock:
                return self.peer_preview.open(args, origin)
        if action == 'session.select' and 'peerPreview' in args:
            if 'chatReference' in args:
                raise AppError('Choose one source-chat reference.', 400)
            self.peer_preview.select(args, origin)
        if resolved_target is not None and not passive_metadata and action != 'session.titlePreview' and not (action == 'session.select' and {'chatReference', 'peerPreview'} & args.keys()):
            self._admit_session(resolved_target)
        if (origin not in {'ui', 'user'} or 'scope' in args) and action in {'history.list', 'history.shared.list', 'history.shared.view'}:
            reader = caller_session_id or (self.state.get('selectedSessionId') if origin in {'ui','user'} else None)
            if not reader:
                raise AppError('History reads require a registered calling conversation.', 403)
            from .history_query import query_history
            values = {'action':'read' if action.endswith('.view') else 'list',
                'scope':args.get('scope','workspace'), 'offset':args.get('offset',0), 'limit':min(50,args.get('limit',20))}
            if 'include_discovery_issues' in args:
                values['include_discovery_issues'] = args['include_discovery_issues']
            if args.get('workspace'):
                values['workspace'] = args['workspace']
            if action.endswith('.view'):
                values['session_id'] = args['id']
            return {'accepted':True, 'result':await query_history(self,values,reader)}
        if action == "approval.respond" and args["id"].startswith("collaboration:"):
            return await self.collaboration.decide(args, origin)
        routed = await self.collaboration.route(action, args, origin, command_id, caller_session_id)
        if routed is not None:
            return routed
        if action == 'configuration.patchPlacement.read':
            from .patch_placement import read
            try:
                return {'accepted': True, 'result': await read(self.management, args, origin, caller_session_id)}
            except (ValueError, OSError, KeyError, TypeError):
                raise AppError('Patch placement cannot be verified for this calling root. No worker was started.', 409) from None
        if action == 'configuration.operation.read':
            from .configuration_recovery import command as recovery_command
            async with self.lock:
                try:
                    result = recovery_command(self.management, action, args, origin, caller_session_id)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    raise AppError('The retained configuration operation cannot be read: ' + str(exc), 409) from None
            return {'accepted': True, 'result': result}
        if action.startswith("session.handoff."):
            return await self.handoffs.dispatch(action, args, origin, caller_session_id,
                include_state=include_state, admission_check=admission_check)
        if action.startswith("access."):
            return await self.access_mode.dispatch(action, args, origin, command_id, caller_session_id,
                admission_check=admission_check, expected_revision=expected_revision)
        if action.startswith("capability."):
            return await self.configuration_authority.dispatch(action, args, origin, command_id, caller_session_id)
        if action.startswith("workspace.work."):
            return await self.workspace_work.dispatch(action, args, origin, command_id, caller_session_id)
        if action == 'configuration.patchPlacement.bind' and command_id:
            from .patch_placement import retained_receipt
            async with self.lock:
                if admission_check is not None:
                    admission_check()
                try:
                    retained = retained_receipt(self.management, args, origin, caller_session_id,
                        command_id, command_fingerprint(action, args, origin, client_id))
                except (ValueError, OSError, KeyError, TypeError):
                    raise AppError('The retained patch binding cannot be verified for this root. No effect was admitted.', 409) from None
            if retained is not None:
                return retained
        configuration_authority = self.configuration_authority.capture(action, args, origin, caller_session_id)
        if action in TEST_RUNNER_ACTIONS:
            from .registered_test_runners import dispatch as test_runner_dispatch
            return await test_runner_dispatch(self, action, args, configuration_authority)
        if action in TEST_CAPACITY_ACTIONS:
            from .test_capacity_settings import dispatch as test_capacity_dispatch
            return await test_capacity_dispatch(self, action, args, configuration_authority)
        if action in TEST_EXECUTION_ACTIONS:
            from .test_execution_settings import dispatch as test_execution_dispatch
            return await test_execution_dispatch(self, action, args, configuration_authority)
        if action in TEST_QUEUE_ACTIONS:
            return await self.test_queue.dispatch(action, args, origin, caller_session_id, configuration_authority)
        if action in ENVIRONMENT_ACTIONS:
            from .task_environments import dispatch as environment_dispatch
            return await environment_dispatch(self, action, args, origin, caller_session_id, configuration_authority)
        if action in RESOURCE_RESERVATION_ACTIONS:
            from .workspace_resources import dispatch_reservation
            return await dispatch_reservation(self, action, args, origin, command_id, caller_session_id)
        if action.startswith("profiling."):
            host = getattr(self, "profiling_host", None)
            if host is None:
                raise AppError("Profiling requires the running host adapter.", 503)
            return await host.dispatch(action, args, command_id)
        transfer_sid = None
        if action in {'outputs.attach', 'outputs.attachMedia', 'outputs.attachChatReference', 'outputs.write', 'outputs.review', 'outputs.unlink', 'outputs.relink', 'outputs.comment',
                      'session.rename', 'session.naming', 'session.delete', 'message.edit', 'conversation.send', 'conversation.retry',
                      'worker.spawn', 'worker.message', 'worker.steer', 'question.answer', 'operations.submit', 'operations.write'}:
            transfer_sid = args.get('sessionId') or (args.get('id') if action.startswith('session.') else None) or self.state.get('selectedSessionId')
            if transfer_sid:
                # Snapshot without yielding; final admission below checks it
                # under the mutation lock without changing its queue ordering.
                try: transfer_context = self.portability.write_context(transfer_sid)
                except ValueError as exc: raise AppError(str(exc), 409) from exc
        if action == 'settings.connection.services':
            from .settings_actions import services
            return {'accepted': True, 'revision': self.state['revision'], 'effects': [],
                    'result': {'services': services(), 'kind': 'supported-services', 'savedConnections': False}}
        if action in {'voice.configuration','voice.configure','voice.preview'}:
            from .voice_settings import dispatch as voice_settings_dispatch
            from .voice import VoiceError
            try:
                result = await voice_settings_dispatch(self, action, args)
            except VoiceError as exc:
                raise AppError(str(exc), exc.status) from None
            return {'accepted': True, 'revision': self.state['revision'], 'effects': [], 'result': result,
                    **({'state': self.browser_state()} if include_state else {})}
        if action == 'feedback.excerpt.review':
            try:
                result = await self.feedback.excerpts.review(args)
            except ValueError as exc:
                raise AppError(str(exc), 409) from None
            except Exception:
                raise AppError('The excerpt destination could not be checked. Nothing was sent.', 409) from None
            return {'accepted': True, 'revision': self.state['revision'], 'effects': [], 'result': result}
        if action == 'feedback.destination':
            from . import feedback as feedback_module
            from . import feedback_context
            from .attachments import file_path as feedback_file_path
            full_owner = caller_session_id if feedback_context.enabled(self, origin, caller_session_id) else None
            try:
                repo = await feedback_module.github_api('repos/' + feedback_module.REPOSITORY, None)
                if repo.get('full_name') != feedback_module.REPOSITORY or type(repo.get('id')) is not int or type(repo.get('private')) is not bool:
                    raise ValueError('Invalid destination')
            except Exception:
                raise AppError('The feedback destination could not be verified. Nothing was uploaded.', 409) from None
            # Re-read exact staged bytes, including drafts saved before hashes
            # were exposed in the UI. This is a review, never upload consent.
            async with self.lock:
                reviewed_files = []
                staged = feedback_context.rows(self, full_owner) if full_owner else self.state.get('view', {}).get('feedbackDraft', {}).get('attachments', [])
                for row in staged:
                    if row.get('excerpt'):
                        continue
                    try:
                        path, _ = feedback_file_path(self.data_dir, row['id'])
                        digest = await asyncio.to_thread(lambda p=path: hashlib.sha256(p.read_bytes()).hexdigest())
                    except (OSError, ValueError):
                        raise AppError('A staged file is unavailable. Remove it or attach it again before review.', 409) from None
                    reviewed_files.append({'id': row['id'], 'sha256': digest})
            return {'accepted': True, 'result': {'repository': repo['full_name'], 'repositoryId': repo['id'],
                    'visibility': 'private' if repo['private'] else 'public'}, 'files': reviewed_files, 'effects': []}
        if action == 'feedback.diagnostics':
            async with self.lock:
                try:
                    result = self.feedback.diagnostics(args)
                except ValueError as exc:
                    raise AppError(str(exc), 404) from None
                return {'accepted': True, 'revision': self.state['revision'], 'effects': [], 'result': result}
        if action == 'updates.diagnostics':
            if not self.update_manager:
                raise AppError('Update service is unavailable.')
            from .update_report import collect
            result = await collect(self.update_manager)
            return {'accepted': True, 'revision': self.state['revision'], 'effects': [], 'result': result}
        if action == 'smartTools.readResult':
            if not self.smart_tools:
                raise AppError('Smart Tools service is unavailable.')
            async with self.lock:
                if expected_revision is not None and expected_revision != self.state['revision']:
                    raise AppError('The app changed. Refresh its state and retry.', 409)
                try:
                    value = self.smart_tools.read_operation(args)
                except ValueError as exc:
                    raise AppError(str(exc)) from None
                return {'accepted': True, 'revision': self.state['revision'], 'effects': [], 'result': value}
        if action == 'session.titlePreview':
            from .chat_title_preview import read as read_title
            # Copy only catalog identity/name fields. Reading a cold message
            # property here would hydrate a whole chat just to label a row.
            async with self.lock:
                source = self._session_read_sources([args['sessionId']],
                    ('id', 'runtimeSessionId', 'nativeIdentity', 'nativeProject', 'workspace',
                     'title', 'titleSource', 'nativeNameSource'))[0]
            result = await asyncio.to_thread(read_title, self.data_dir, source)
            return {'accepted': True, 'revision': self.state['revision'], 'effects': [], 'result': result}
        if action in {'theme.list', 'theme.read'}:
            from . import theme_library
            value = await asyncio.to_thread(theme_library.listing, self) if action == 'theme.list' else await asyncio.to_thread(theme_library.read, self, args['id'])
            return {'accepted': True, 'revision': self.state['revision'], 'effects': [], 'result': value}
        if action == 'canvas.views.reveal':
            from .canvas_reveal import reveal
            async with self.lock:
                row, preference = self.canvas_views.target(args)
                try:
                    result = await reveal(row.get('path'), self.instance_id, args)
                except (ValueError, OSError) as error:
                    raise AppError(str(error), 409) from None
            return {'accepted':True,'result':result,'effects':[]}
        if action in storage_cleanup_definitions(schema, string):
            from .storage_cleanup import dispatch as cleanup_dispatch
            return await cleanup_dispatch(self, action, args, origin, command_id, caller_session_id)
        if action in {'storage.historyDeletePreview', 'storage.historyDelete'}:
            from .managed_deletion import dispatch as delete_history
            selection = {key: args[key] for key in ('project', 'nativeId') if args.get(key)}
            identity = 'history-' + uuid.uuid4().hex
            if action == 'storage.historyDelete':
                saved = self.db.execute('SELECT id,value FROM managed_deletions WHERE token=?', (args['confirmationToken'],)).fetchone()
                if not saved or json.loads(saved[1]).get('historySelection') != selection:
                    raise AppError('Review this session history before confirming deletion.', 409)
                identity = saved[0]
            return await delete_history(self, 'session.deletePreview' if action.endswith('Preview') else 'session.delete', {**args, 'id':identity}, origin, include_state, history=selection)
        if action in {'storage.backupDeletePreview', 'storage.backupDelete'}:
            from .storage_backup_cleanup import dispatch as backup_cleanup
            return await backup_cleanup(self, action, args)
        if action in {'storage.inspect','workspace.deleteFilesPreview','workspace.deleteFiles'}:
            from .storage_usage import dispatch as storage_dispatch
            return await storage_dispatch(self, action, args, include_state)
        if action in {'session.deletePreview', 'session.delete'}:
            from .managed_deletion import dispatch as delete_managed_chat
            return await delete_managed_chat(self, action, args, origin, include_state)
        if action == 'desktop.readiness':
            from .desktop_readiness import dispatch as desktop_readiness
            return await desktop_readiness(self, args, origin, expected_revision, caller_session_id)
        if action in integration_definitions(schema, string):
            from .integration_evidence import dispatch as integration_dispatch
            return await integration_dispatch(self, action, args, origin, command_id, caller_session_id)
        if action == 'runtime.dependencies':
            from .artifact_runtime import discover
            async with self.lock:
                if expected_revision is not None and expected_revision != self.state['revision']:
                    raise AppError('The app changed. Refresh its state and retry.', 409)
                sid = args.get('sessionId') or self.state.get('selectedSessionId')
                if sid:
                    self._session(sid)
            verify = args.get('verifyImports') is True
            host = await discover('host', **({'verify': True} if verify else {}))
            worker = {'status': 'unavailable', 'reason': 'No running session runtime.', 'sessionId': sid}
            if sid and self.runtime and hasattr(self.runtime, 'dependencies'):
                worker = await self.runtime.dependencies(sid, **({'verify': True} if verify else {}))
            return {'accepted': True, 'revision': self.state['revision'], 'effects': [],
                'result': {'host': host, 'worker': worker},
                **({'state': self.browser_state()} if include_state else {})}
        if action.startswith('observation.'):
            try:
                result = await self.observations.dispatch(action, args, origin, command_id)
            except ValueError as exc:
                raise AppError(str(exc), 409) from None
            return {'accepted': True, 'result': result}
        if action.startswith('schedule.'):
            try:
                result = await self.schedules.dispatch(action, args, origin, command_id, caller_session_id)
            except ValueError as exc:
                raise AppError(str(exc), 409) from None
            return {'accepted': True, 'result': result, **({'state': self.browser_state()} if include_state else {})}
        if action.startswith('assignment.'):
            return await self.assignments.dispatch(action, args, origin, command_id, caller_session_id)
        if action.startswith("modelCapacity."):
            from .model_capacity_settings import dispatch as model_capacity_dispatch
            return await model_capacity_dispatch(self, action, args, origin, command_id, caller_session_id)
        if action.startswith("capacity."):
            from .capacity import dispatch as capacity_dispatch
            return await capacity_dispatch(self, action, args, origin, command_id, include_state)
        if action.startswith('task.'):
            from .task_continuity import dispatch as task_dispatch
            return await task_dispatch(self, action, args, origin, command_id, include_state, guard=admission_check)
        if action in {'question.list', 'question.read'}:
            async with self.lock:
                return {'accepted': True, 'result': self.questions.read(action, args),
                        **({'state': self.browser_state()} if include_state else {})}
        if action.startswith("coordination."):
            try:
                return await self.coordination.dispatch(action, args, origin, command_id, include_state, caller_session_id)
            except ValueError as exc:
                raise AppError(str(exc)) from None
        if action.startswith(('recall.', 'memory.')):
            return await self.recall.dispatch(action,args,origin,command_id,caller_session_id)
        if action in MESSAGE_INTERACTIONS and origin == 'agent' and (not caller_session_id or args['sessionId'] != caller_session_id):
            raise AppError('Message actions must target the calling conversation.', 403)
        reveal_message = None
        if action == "message.reveal":
            session = self._session(args["sessionId"])
            if not args.get('loadedOnly') and session.get("nativeProject") and not any(row["id"] == args["messageId"] for row in session.get("messages", [])):
                # Presentation needs saved bytes/identity, not generation
                # authority. Native indexes are not visible paging ordinals.
                from .automatic_history import read_transcript
                window = await asyncio.to_thread(read_transcript, copy.deepcopy(session), limit=None)
                reveal_message = next((row for row in window["messages"] if row["id"] == args["messageId"]), None)
        if (action.startswith(('canvas.views.', 'canvas.apps.', 'canvas.versions.')) or action in {'theme.preview', 'theme.revert', 'canvas.visibility', 'canvas.show', 'canvas.select', 'canvas.reference', 'smartTools.viewStatus', 'smartTools.reconnectView', 'smartTools.reviewView', 'smartTools.updateView', 'message.reply', 'message.replyClear', 'message.reveal'}) and 'clientId' in args and not (action == 'smartTools.viewStatus' and origin == 'agent'):
            if client_id is None:
                with self.clients.bind(args['clientId']):
                    return await self.dispatch(action, args, origin, command_id, expected_revision, include_state=include_state, caller_session_id=caller_session_id)
            if args['clientId'] != client_id:
                raise AppError('The canvas view command targets a different client.')
        if action in {'smartTools.reconnectView', 'smartTools.reviewView'} and origin == 'agent' and (
                not caller_session_id or self.state.get('selectedSessionId') != caller_session_id):
            raise AppError('Choose a client displaying the calling conversation before recovering its tool view.', 409)
        if action == 'smartTools.updateView' and origin not in {'ui', 'user'}:
            raise AppError('A person must review and approve changed tool contracts in their own view.', 403)
        if action == 'smartTools.viewStatus':
            from .mcp_view_recovery import inspect, inspect_for_session
            if not self.smart_tools:
                raise AppError('Smart Tools service is unavailable.')
            async with self.lock:
                result = (inspect_for_session(self, args['canvasId'], caller_session_id, args.get('clientId'))
                          if origin == 'agent' else inspect(self, args['canvasId']))
                return {'accepted': True, 'result': result, 'effects': []}
        if action.startswith("kernels."):
            from .computation import dispatch
            return await dispatch(self, action, args, origin)
        if action.startswith("operations."):
            return await self.operations.dispatch(action, args, origin, command_id)
        if action.startswith("computer.visual."):
            return await self.computer_visual.dispatch(action, args, command_id, origin)
        if action.startswith("voice.visual."):
            return await self.voice_visual.dispatch(action, args, command_id, origin)
        if action.startswith("outputs."):
            return await self.outputs.dispatch(action,args,origin,command_id)
        if action.startswith('publishing.'):
            if origin == 'agent' and (not caller_session_id or args['sessionId'] != caller_session_id):
                raise AppError('Publishing actions must target the calling conversation.', 409)
            try:
                result = await self.publishing.dispatch(action, args, origin, command_id)
            except ValueError as exc:
                code = getattr(exc, 'code', None)
                raise AppError(str(exc), 503 if code == 'unknown_outcome' else 409, code=code, receipt=getattr(exc, 'receipt', None)) from None
            return {'accepted': True, 'result': result, **({'state': self.browser_state()} if include_state else {})}
        if action.startswith("shell."):
            return await self.shell.dispatch(action, args, origin, command_id)
        if action.startswith('terminal.'):
            manager = getattr(self, 'terminal_setup', None)
            if manager is None:
                raise AppError('Terminal setup is available through the running Unified service.', 503)
            try:
                return await manager.perform(action, args, command_id)
            except (ValueError, OSError, TimeoutError) as exc:
                raise AppError(str(exc) if isinstance(exc, ValueError) else 'Terminal setup could not finish. Check server release access and retry.', 409) from None
        if action == 'runtime.control' and args.get('operation') in {'history.edit','history.rewind'}:
            raise AppError('Use message.edit to revise conversation history.')
        checked_session = None
        implicit_session = False
        if action.startswith('portability.'):
            try:
                result = await self.portability.dispatch(action, args, origin, command_id)
            except (ValueError, OSError, KeyError) as exc:
                raise AppError(str(exc), 409) from None
            return {'accepted': True, 'result': result, **({'state': self.browser_state()} if include_state else {})}
        if action.startswith('worktree.'):
            try:
                result = await self.worktrees.dispatch(action, args, origin, command_id, caller_session_id)
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from None
            return {'accepted': True, 'result': result, **({'state': self.browser_state()} if include_state else {})}
        passive_configuration = action == 'bundle.preview' or (
            action == 'runtime.control' and args.get('operation') in {'configuration.catalog', 'provider.queueSelection'})
        if not passive_configuration and action in {'question.answer', 'conversation.send', 'conversation.retry', 'conversation.delivery', 'worker.spawn', 'worker.message', 'worker.steer', 'worker.stop', 'call.start', 'message.edit', 'session.fork', 'session.recover', 'session.takeover', 'session.naming', 'runtime.control', 'configuration.inspect', 'configuration.apply', 'bundle.save', 'bundle.export', 'bundle.preview', 'bundle.switch', 'bundle.fork'}:
            sid = args.get('sessionId') or (args.get('id') if action in {'session.fork', 'session.recover', 'session.takeover', 'session.naming', 'configuration.inspect', 'configuration.apply'} else None) or self.state.get('selectedSessionId')
            if sid:
                checked_session = sid
                implicit_session = not (args.get('sessionId') or (args.get('id') if action in {'session.fork', 'session.recover', 'session.takeover', 'session.naming', 'configuration.inspect', 'configuration.apply'} else None))
                try:
                    with measure(self, 'input.history', session_id=sid) if action in {'conversation.send', 'conversation.retry'} else nullcontext():
                        await self.history.ensure_loaded(sid)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if action in {'conversation.send','conversation.retry','worker.spawn','call.start','runtime.control','configuration.inspect','configuration.apply','bundle.save','bundle.export','bundle.preview','bundle.switch','bundle.fork'}:
                    with measure(self, 'input.configuration', session_id=sid) if action in {'conversation.send', 'conversation.retry'} else nullcontext():
                        await self.refresh_configuration(sid)
                    current=self._session(sid)
                    if current.get('configurationPending') and current.get('configurationRefresh',{}).get('phase')=='error':
                        raise AppError(current['configurationRefresh']['error'],409)
        fingerprint = command_fingerprint(action, args, origin, client_id, caller_session_id=caller_session_id)
        prepared_workspace = None
        prepared_identity = None
        prepared_draft_id = None
        from .managed_chats import is_managed, allocate, creation_identity
        managed_creation = action == 'session.create' and is_managed(args)
        if action == 'session.create' and not managed_creation and not (command_id and self.db.execute('SELECT 1 FROM commands WHERE id=?', (command_id,)).fetchone()):
            try:
                target_path = self._session_destination(args)
            except (ValueError, OSError):
                raise AppError('Choose a valid workspace folder.', 409) from None
            self._require_workspace_scaffold(target_path)
        if managed_creation and args.get('workspace', '').strip():
            raise AppError('A chat without a workspace cannot also choose a workspace folder.')
        if action == 'session.create' and (managed_creation or args.get('workspace', '').strip()):
            from .new_chat import selection
            from .session_creation import prepare
            from .workspace_canvas import _create_workspace_folder
            async with self.lock:
                if expected_revision is not None and getattr(self, '_progress_dirty', False):
                    self._commit_pending_progress()
                previous = self.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone() if command_id else None
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError('This command ID was already used with different contents.', 409)
                    if include_state and getattr(self, '_progress_dirty', False):
                        self._commit_pending_progress()
                    return {**json.loads(previous[1]), **({'state': self.browser_state()} if include_state else {}), 'duplicate': True}
                if admission_check is not None:
                    admission_check()
                if expected_revision is not None and expected_revision != self.state['revision']:
                    raise AppError('The app changed. Refresh its state and retry.', 409)
                try:
                    selection(args.get('selection', {}))
                    prepare(self, args, origin, caller_session_id)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if args.get('select', True):
                    self.canvas_views.guard_transition(action, args)
                    if client_id is not None and (args.get('fromDraft') or self.state.get('selectedSessionId') is None):
                        prepared_draft_id = self.clients.attachment_draft_id()
                        if args.get('expectedDraftId', prepared_draft_id) != prepared_draft_id:
                            raise AppError('The original new-chat draft changed. Nothing was sent.', 409)
            # Opening/editing a draft never creates directories. First submit
            # creates only its explicit path, away from the event loop and lock.
            if managed_creation:
                prepared_identity = creation_identity(self.data_dir, args, command_id)
                from .managed_deletion import removed
                if removed(self.db, prepared_identity):
                    raise AppError("This conversation was permanently deleted. Start a new chat instead.", 409)
                try:
                    prepared_workspace = await asyncio.to_thread(allocate, self.data_dir, prepared_identity, command_id or prepared_identity)
                except (ValueError, OSError) as exc:
                    raise AppError(str(exc), 409) from None
            else:
                prepared_workspace = str(await self.worktrees.finish_thread(_create_workspace_folder, args['workspace']))
        prepared_health = None
        if action == 'session.inspect':
            from .session_health import inspect_session
            async with self.lock:
                snapshot = copy.deepcopy(self._session(args['id']))
                captured_at = time.time()
            prepared_health = await asyncio.to_thread(inspect_session, self.data_dir, snapshot)
            bridge_events = await asyncio.to_thread(self.diagnostics.bridge_events, snapshot)
            prepared_health['timeline'] = [row for row in prepared_health['timeline']
                                          if row.get('type') != 'runtime.bridge'] + bridge_events
            prepared_health['capturedAt'] = captured_at
            prepared_health['host'] = {**getattr(self.update_manager, 'running_identity', {}), 'hostInstanceId': self.instance_id}
            prepared_health['recentRequests'] = list(getattr(self, 'request_timings', []))[-30:]
            prepared_health['performance'] = self.performance_diagnostics.snapshot()
            prepared_health['capture']['limitations'].append('Request timings cover handler execution, not time before the server accepts a connection.')
        if action == 'session.plans':
            if args.get('includeItems') and len(args['ids']) != 1:
                raise AppError('Inspect one chat at a time for plan details.')
            sources = self._session_read_sources(args['ids'],
                ('id', 'nativeIdentity', 'runtimeSessionId', 'workspace', 'nativeProject'))
            rows = []
            for source in sources:
                rows.append(await asyncio.to_thread(self.event_log_view.read_plan, source,
                    include_items=args.get('includeItems', False)))
            # The result is this read's complete receipt. It does not publish
            # app state, and cannot require a later unrelated stream revision.
            return {'accepted': True, 'result': {'items': rows}, 'effects': [],
                    '_stateIndependent': True}
        prepared_export = None
        prepared_share = None
        if action == 'session.sharePreview':
            from .conversation_export import messages, snapshot
            async with self.lock:
                previous = self.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone() if command_id else None
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError('This command ID was already used with different contents.', 409)
                    return {**json.loads(previous[1]), **({'state': self.browser_state()} if include_state else {}), 'duplicate': True}
                source = copy.deepcopy(self._session(args['sessionId']))
                artifacts = copy.deepcopy(self.state.get('canvasArtifacts', []))
            try:
                chat_references = await self.outputs.chat_references.export_references(source['id'])
                rows = await asyncio.to_thread(messages, self.data_dir, source)
                prepared_share = (source, snapshot(self.data_dir, source, artifacts,
                    resolver=self.peer_attribution.resolve, message_rows=rows,
                    chat_references=chat_references)[0])
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from exc
        if action == 'session.export' and args.get('format', 'json') != 'markdown' and any(key in args for key in ('scope', 'fromMessageId', 'throughMessageId', 'minimal', 'includeExecution', 'includeConversationId', 'includeTimestamps', 'includeReferenceIds')):
            raise AppError('Choose Markdown to export a message range.')
        if action == 'session.export' and args.get('format') == 'markdown':
            from .conversation_export import messages, snapshot
            async with self.lock:
                # Retried receipts refer to the original bytes even if their
                # source is now missing. Do not read native history again.
                previous = self.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone() if command_id else None
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError('This command ID was already used with different contents.', 409)
                    if include_state and getattr(self, '_progress_dirty', False):
                        self._commit_pending_progress()
                    return {**json.loads(previous[1]), **({'state': self.browser_state()} if include_state else {}), 'duplicate': True}
                source = copy.deepcopy(self._session(args['id']))
                artifacts = copy.deepcopy(self.state.get('canvasArtifacts', []))
            # Native storage may be slow. Freeze the host-owned portion first,
            # then let navigation and runtime events continue during the read.
            try:
                chat_references = await self.outputs.chat_references.export_references(source['id'])
                rows = await asyncio.to_thread(messages, self.data_dir, source)
                prepared_export = (source, snapshot(self.data_dir, source, artifacts, args,
                    resolver=self.peer_attribution.resolve, message_rows=rows,
                    chat_references=chat_references))
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from exc
        retained_file = None
        if action == 'canvas.openFile' and args.get('referenceRequestId'):
            from .result_artifacts import load as load_retained_file
            async with self.lock:
                previous = self.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone() if command_id else None
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError('This command ID was already used with different contents.', 409)
                    return {**json.loads(previous[1]), **({'state': self.browser_state()} if include_state else {}), 'duplicate': True}
                response = self.collaboration.receipt(args['referenceRequestId']).get('response') or {}
                has_retained_version = 'artifacts' in response or args.get('referenceResponseRevision') is not None
            if has_retained_version:
                retained_file = await load_retained_file(self.collaboration, args['sessionId'], args['referenceRequestId'],
                    args['path'] + '@sha256:' + args.get('expectedSha256', ''), args.get('referenceResponseRevision'))
        gif_proof = gif_record = None
        if action == 'canvas.openGif':
            media = self.outputs.media
            try:
                # Canonical identity work owns its own lock and worker. Never
                # call identity/guarded inside the action mutation lock.
                proof = await media.identity(args['sessionId'], args['messageId'], scoped=True)
                async with self.lock:
                    gif_record = copy.deepcopy(media.guard(args, proof=proof))
                    if gif_record.get('media', {}).get('mimeType') != 'image/gif':
                        raise ValueError('Open in Canvas is available only for retained GIFs.')
                    self.canvas_views.guard_transition(action, args)
                # Validate the same existing retained body before any library
                # mutation, including when the source file has disappeared.
                # bytes() uses the existing bounded workers/cache/hash guards;
                # none of these bytes enter Canvas state or action receipts.
                await media.bytes(args)
                gif_proof = await media.identity(args['sessionId'], args['messageId'], scoped=True)
                if gif_proof != proof:
                    raise ValueError('The saved assistant identity changed while opening Canvas.')
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from None
        chat_reference_proof = None
        if action == 'session.select' and 'chatReference' in args:
            try:
                chat_reference_proof = await self.outputs.chat_references.preflight(args, origin)
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from None
        copy_references = []
        if action == 'message.copy':
            try:
                copy_references = await self.outputs.chat_references.export_references(
                    args['sessionId'], args['messageId'])
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from None
        pending = []
        async with self.lock, AsyncExitStack() as recovery_boundary:
            if action == 'session.select' and 'peerPreview' in args:
                self.peer_preview.select(args, origin)
                self.canvas_views.guard_transition(action, args)
                if resolved_target is not None:
                    self._admit_session(resolved_target)
            if chat_reference_proof is not None:
                try:
                    self.outputs.chat_references.guard(args, origin, chat_reference_proof)
                except (ValueError, KeyError) as exc:
                    raise AppError(str(exc), 409) from None
                # Call the same sidebar guard even for a same-ID click/replay.
                # Its own same-scope preservation rules remain authoritative.
                self.canvas_views.guard_transition(action, args)
                if resolved_target is not None:
                    self._admit_session(resolved_target)
            if action == 'canvas.openGif':
                try:
                    current = self.outputs.media.guard(args, proof=gif_proof)
                    if current != gif_record:
                        raise ValueError('The retained GIF changed while opening Canvas.')
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                # Re-check dirty state after every await, even before a replay
                # receipt. No remember/save/select has happened yet.
                self.canvas_views.guard_transition(action, args)
            if action in {'canvas.versions.revise', 'canvas.versions.restore'}:
                row = next((r for r in self.state.get('canvasArtifacts', [])
                            if r['id'] == args['id']), {})
                if row.get('kind') == 'gif':
                    raise AppError('Retained GIF references are immutable. Open the original GIF instead.', 409)
            if transfer_sid:
                try:
                    current_sid = args.get('sessionId') or (args.get('id') if action.startswith('session.') else None) or self.state.get('selectedSessionId')
                    if current_sid != transfer_sid:
                        raise ValueError('The selected chat changed. Retry in the intended chat.')
                    if self.portability.write_context(transfer_sid) != transfer_context:
                        raise ValueError('The task moved while this request was waiting; inspect and retry.')
                except ValueError as exc:
                    raise AppError(str(exc), 409) from exc
            # Flush and compare under the same lock: a queued runtime event
            # must not mutate progress between a read barrier and CAS admission.
            if expected_revision is not None and getattr(self, '_progress_dirty', False):
                self._commit_pending_progress()
            if command_id:
                previous = self.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (command_id,)).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError("This command ID was already used with different contents.", 409)
                    if include_state and getattr(self, '_progress_dirty', False):
                        self._commit_pending_progress()
                    saved_receipt = json.loads(previous[1])
                    if action == 'question.answer':
                        saved_receipt['result'] = self.questions.read('question.read', args)
                    return {**saved_receipt, **({'state': self.browser_state()} if include_state else {}), "duplicate": True}
            # A shielded task may have waited on history, configuration, creation
            # or this lock after HTTP revocation. Recheck before new acceptance;
            # existing receipt replay above and already accepted work stay intact.
            if admission_check is not None:
                admission_check()
            self.configuration_authority.check(configuration_authority)
            guard_history_action(self, action, args, origin, caller_session_id)
            if checked_session:
                if implicit_session and self.state.get('selectedSessionId') != checked_session:
                    raise AppError('The selected chat changed. Retry in the intended chat.', 409)
                checked = self._session(checked_session)
                if action not in {'conversation.delivery', 'session.takeover', 'session.fork', 'session.recover', 'session.inspect', 'message.edit', 'bundle.export'} and checked.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
                    raise AppError('This session is read-only here. Choose Continue here to request ownership.', 409, code='session_busy')
                if checked.get('nativeProject') and action != 'session.inspect':
                    reason = checked.get('historyReadOnlyReason') or checked.get('historyError')
                    if reason:
                        raise AppError(reason, 409)
                    if not checked.get('workspace') or not Path(checked['workspace']).is_dir():
                        raise AppError('Restore this project folder before continuing its chat.', 409)
            if expected_revision is not None and expected_revision != self.state["revision"]:
                raise AppError("The app changed. Refresh its state and retry.", 409)
            from .update_drain import held as update_held, new_work as update_new_work
            update_message = action == 'conversation.send' and update_held(self) and (args.get('allowNextTurn') is True or not args.get('expectedGenerationId'))
            if work_paused(self.state) and not update_message and update_new_work(action, args):
                raise AppError("An ecosystem update is activating. Please retry in a moment.", 409)
            if update_held(self) and update_new_work(action, args) and action not in {'question.answer', 'conversation.send', 'worker.steer'}:
                raise AppError('The app is finishing existing work for an update. Start new work after it finishes.', 409)
            if action in {"question.answer","conversation.send","conversation.retry","worker.spawn","worker.steer","worker.message","call.start"}:
                current=next((s for s in self.state['sessions'] if s['id']==args.get('sessionId',self.state['selectedSessionId'])),{})
                if action != 'question.answer' and current and self.assignments.blocked(current):
                    raise AppError('This assignment is stopped. Open Shared tasks and assignment in the commissioning chat and choose Resume assignment before sending new work.', 409, code='assignment_stopped')
                if (current.get('configurationBusy') or self.management and self.management.unresolved_configuration(current)) and action != 'question.answer':
                    raise AppError('Applying or reconciling conversation settings; new input must wait.',409)
                if action != 'question.answer' and current.get('fileAccessPending'):
                    generation = current.get('collaborationGeneration') or {}
                    # Existing work retains its mounted policy until it settles.
                    # Let the human guide that work without starting another run.
                    steering_existing = (
                        origin == 'ui' and action == 'conversation.send'
                        and current.get('status') in {'working', 'starting', 'running'}
                        and generation.get('id') and not generation.get('terminal')
                    )
                    if not steering_existing:
                        from .chat_permissions import PENDING_DETAIL
                        raise AppError(PENDING_DETAIL, 409)
            if action == 'session.pin':
                work_effect_guard(self, action, args, origin, caller_session_id, final=True)
                from .pinned_chats import update
                return update(self, args, command_id, fingerprint, origin, include_state=include_state)
            if action in {'workspace.pin', 'workspace.pinOrder', 'navigation.pinOrder'}:
                from .workspace_navigation import update_pins
                return update_pins(self, action, args, command_id, fingerprint, origin, include_state=include_state)
            if (action == 'view.update' or action in SETTINGS_NAVIGATION) and origin == 'agent' and caller_session_id and client_id is not None:
                from .agent_canvas import target
                target(self, caller_session_id, client_id, required=True, connected_only=True)
            if action == 'view.update' and client_id is not None:
                if 'sessionId' in args and any(key.startswith('canvas') for key in args['patch']) and args['sessionId'] != self.state.get('selectedSessionId'):
                    raise AppError('The chat changed. Adjust Canvas in the intended chat.', 409)
                from .client_layout import accepts, update
                if accepts(args['patch']):
                    return update(self, args['patch'], command_id, fingerprint, include_state=include_state)
            if action == 'canvas.visibility':
                from .canvas_visibility import update
                return update(self, args, command_id, fingerprint, include_state=include_state)
            if action in {'canvas.views.status', 'canvas.views.observe'} and client_id is not None:
                from .client_observation import update
                return update(self, action, args, command_id, fingerprint, include_state=include_state)
            opens_selected_canvas = action in {'canvas.select', 'canvas.reopen', 'canvas.tabClose'} or (action == 'canvas.show' and not args.get('sessionId'))
            if action == 'canvas.select' and args.get('sessionId', self.state.get('selectedSessionId')) != self.state.get('selectedSessionId'):
                raise AppError('The chat changed. Open this saved artifact from its original chat.', 409)
            if action == 'canvas.select' and origin == 'agent' and caller_session_id and self.state.get('selectedSessionId') != caller_session_id:
                raise AppError('The client changed chats. Choose a client displaying the calling conversation.', 409)
            if opens_selected_canvas and self.state.get('selectedSessionId') is None:
                raise AppError('Start a chat before opening Canvas.', 409, code='canvas_requires_session')
            from .canvas_library import remember, fork_artifacts
            if action != "session.create" or args.get("select", True):
                self.canvas_views.guard_transition(action, args)
            if action == 'canvas.close' and client_id is not None:
                views = self.canvas_views.record()
                views.pop('retained', None)
                views.pop('primaryBinding', None)
            if action == 'session.select' and client_id is not None:
                from .client_navigation import accepts, select
                target = self.projections.sessions(self.state).by_id.get(args['id'])
                if target is not None and accepts(self, target):
                    return select(self, target, command_id, fingerprint, include_state=include_state)
            if action in {'canvas.copy', 'canvas.download'}:
                canvas = self.state['canvas']
                if args['id'] == canvas.get('id') and canvas.get('kind') == 'gif':
                    raise AppError('Use Download original on the retained GIF.', 409)
            remember(self.state,self.db)
            previous_scope=(self.state.get('selectedSessionId'),self.state.get('selectedWorkspaceId'))
            previous_draft=self.state['view'].get('draft','')
            self.clients.remember_canvas()
            effects = []
            diagnostic_result = None
            if action in SETTINGS_ACTIONS:
                from .settings_actions import command as settings_command
                diagnostic_result = settings_command(self, action, args, pending, command_id)
            elif action in LIBRARY_ACTIONS:
                try:
                    diagnostic_result = self.conversation_library.perform(action, args, prepared_share)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from exc
            elif action == 'diagnostics.export':
                page=self.state.get('diagnostics',{}).get('lastResult',{})
                if page.get('action')!='diagnostics.records':raise AppError('Inspect the records to export first.')
                lines=[json.dumps({'event':r['event'],'workspace':r['workspace'],'timestamp':r['data']['timestamp'],'data':r['data']}) for r in reversed(page.get('items',[]))]
                effects.append({'type':'download','filename':'amplifier-diagnostics.jsonl','mime':'application/x-ndjson','content':'\n'.join(lines)+'\n'})
            elif action=='diagnostics.test':
                if not any(row['id']==args['id'] for row in self.diagnostics.config['destinations']):
                    raise AppError('Save this destination before testing it.')
                if self.diagnostics.results.get(args['id'],{}).get('phase')=='working':
                    raise AppError('A connection test is already running for this destination.',409)
                self.diagnostics.results[args['id']]={'phase':'working','message':'Checking authentication and event ingestion…'}
                self.state['diagnostics']['results']=copy.deepcopy(self.diagnostics.results)
                pending.append((self._diagnostics_test,(args['id'],self.diagnostics.policy_generation)))
            elif action.startswith('diagnostics.'):
                try:diagnostic_result=await self.diagnostics.command(action,args)
                except ValueError as exc:raise AppError(str(exc)) from None
            elif action == 'history.refresh':
                self.state['sharedHistory'].update(loading=True, error=None)
                pending.append((self.history.refresh, ()))
            elif action == 'session.history':
                session = self._session(args['id'])
                if session.get('nativeProject'):
                    pending.append((self.history_page, (session['id'], args.get('before'), args.get('limit', 100))))
            elif action.startswith('workspace.starters.'):
                from .workspace_starters import configured_catalog
                try:
                    catalog = configured_catalog(self)
                    diagnostic_result = catalog.command(action, args, command_id)
                    self.state['workspaceStarters'] = catalog.listing()
                except (ValueError, OSError) as exc:
                    raise AppError(str(exc), 409) from None
            elif action == 'workspace.bundleDefault.inspect':
                from .bundle_selection import defaults
                workspace = next((row for row in self.state['workspaces'] if row['id'] == args['workspaceId']), None)
                if not workspace:
                    raise AppError('Choose a registered workspace.')
                diagnostic_result = defaults(self.data_dir, workspace['path'], self.state['settings'].get('appBundle'))
            elif action.startswith(('workspace.setup.', 'workspace.resources.')):
                from . import workspace_provisioning, workspace_resources
                workspace = next((w for w in self.state['workspaces'] if w['id'] == args['workspaceId']), None)
                if workspace is None:
                    raise AppError('This workspace is no longer registered.', 409)
                try:
                    if action.startswith('workspace.resources.'):
                        diagnostic_result = workspace_resources.command(self.data_dir, workspace['id'], action, args, command_id)
                        workspace['resources'] = workspace_resources.command(self.data_dir, workspace['id'], 'workspace.resources.list', {})
                    else:
                        identity = workspace.get('setupId')
                        if not identity:
                            raise ValueError('This workspace was not created with a starter.')
                        diagnostic_result = (workspace_provisioning.retry(self.data_dir, identity, args['expectedRevision'])
                                             if action == 'workspace.setup.retry' else workspace_provisioning.inspect(self.data_dir, identity))
                        workspace['setup'] = diagnostic_result
                        if action == 'workspace.setup.retry' and diagnostic_result['status'] == 'pending':
                            pending.append((self._provision_workspace, (identity,)))
                        if action == 'workspace.setup.reconcile':
                            # Admission is durable before yielding, but is not a
                            # completed inspection or a usable retry revision.
                            diagnostic_result = {**diagnostic_result, 'inspectionStatus': 'pending'}
                            pending.append((self._provision_workspace, (identity, True)))
                except (ValueError, OSError) as exc:
                    raise AppError(str(exc), 409) from None
            elif action.startswith("workspace."):
                from .workspace_canvas import workspace_command
                if action == 'workspace.remove':
                    removed = next((w for w in self.state['workspaces'] if w['id'] == args['id']), None)
                    if removed and len(self.state['workspaces']) > 1:
                        self.history.hide_workspace(removed)
                from .workspace_placement import prepare as prepare_workspace, create as create_workspace
                was_existing = bool(args.get('path') and Path(args['path']).expanduser().is_dir())
                placement_args = dict(args)
                placement_result = None
                try:
                    if action == 'workspace.list':
                        from .workspace_placement import listing
                        diagnostic_result = listing(self, args)
                    elif action == 'workspace.prepare':
                        diagnostic_result = prepare_workspace(self, args)
                    elif action == 'workspace.create' and args.get('planId'):
                        if args.get('path') or args.get('name'):
                            raise ValueError('Use the reviewed plan without changing its name or path.')
                        placement_result = create_workspace(self, args['planId'], command_id)
                        placement_args.update(path=placement_result['path'], name=placement_result['name'])
                        if any(w.get('path') == placement_result['path'] for w in self.state['workspaces']):
                            placement_args.pop('name', None)  # A retry cannot undo a later display rename.
                        workspace_command(self.state, 'workspace.add', placement_args)
                        if placement_result.get('starter'):
                            from .workspace_provisioning import initialize
                            setup = initialize(self.data_dir, placement_result)
                            workspace = next(w for w in self.state['workspaces'] if w['id'] == self.state['selectedWorkspaceId'])
                            workspace.update(setupId=setup['id'], setup=setup, resourceTracking=setup['starter']['trackResources'])
                            if workspace['resourceTracking']:
                                from .workspace_resources import command as resource_command
                                workspace['resources'] = resource_command(self.data_dir, workspace['id'], 'workspace.resources.list', {})
                            if setup['status'] == 'pending':
                                pending.append((self._provision_workspace, (setup['id'],)))
                    elif action == 'workspace.create' and not args.get('path'):
                        raise ValueError('Prepare a workspace name before creating it.')
                    else:
                        workspace_command(self.state, action, args)
                except (ValueError, OSError) as exc:
                    raise AppError(str(exc), 409) from None
                if action in {'workspace.add', 'workspace.create'}:
                    hidden = self.state.get('hiddenNativeWorkspaces', [])
                    from .session_files import project_slug
                    restored = {self.state['selectedWorkspaceId'], uuid.uuid5(uuid.NAMESPACE_URL, 'amplifier-project:' + project_slug(placement_args['path'])).hex}
                    hidden[:] = [identity for identity in hidden if identity not in restored]
                    if action == 'workspace.add' or was_existing:
                        pending.append((self.history.refresh, ()))
                    diagnostic_result = {'workspaceId': self.state['selectedWorkspaceId'], 'path': next(w['path'] for w in self.state['workspaces'] if w['id'] == self.state['selectedWorkspaceId']),
                                         'receiptId': (placement_result or {}).get('receiptId', command_id),
                                         'outcome': (placement_result or {}).get('outcome', 'attached')}
                    if placement_result and placement_result.get('starter'):
                        diagnostic_result['setupId'] = placement_result['planId']
                if action in {'workspace.select', 'workspace.add', 'workspace.create', 'workspace.remove'}:
                    from .session_navigation import is_top_level
                    workspace = next(w for w in self.state['workspaces'] if w['id'] == self.state['selectedWorkspaceId'])
                    matches = self.directory.select(
                        "session_kind='root' AND (workspace_id=? OR workspace=?)",
                        (workspace['id'], workspace.get('path')), limit=1,
                        order='(id=' + "'" + str(self.state.get('selectedSessionId', '')).replace("'", "''") + "') DESC,ordinal")
                    selected = self._admit_session(matches[0]) if matches else None
                    if args.get('fromDraft'):
                        # Attaching a location is a property of this client's
                        # unsent chat. Keep text, attachments, model and bundle.
                        from .new_chat import open_draft
                        open_draft(self, {'workspace': workspace['path']})
                        selected = None
                    else:
                        self.state['selectedSessionId'] = selected['id'] if selected else None
                    if action == 'workspace.create' and selected is None and not args.get('fromDraft'):
                        from .new_chat import open_draft
                        open_draft(self, {'workspace': workspace['path']})
                    if selected and (selected.get('nativeProject') or
                            client_id is not None and self.clients.records.get(client_id, {}).get('kind') == 'web'):
                        pending.append((self.history.load, (selected['id'],)))
            elif action.startswith("canvas."):
                from .workspace_canvas import canvas_command
                if action == 'canvas.openFile':
                    from .canvas_files import open_file
                    diagnostic_result = open_file(self, args, origin, retained_file=retained_file)
                elif action == 'canvas.openGif':
                    from .canvas_library import open_gif
                    diagnostic_result = open_gif(self, args, gif_proof)
                elif action == 'canvas.openAttachment':
                    from .canvas_files import open_attachment
                    diagnostic_result = open_attachment(self, args, origin)
                elif action == 'canvas.reference':
                    from .canvas_reference import command
                    diagnostic_result = command(self, args)
                elif action.startswith('canvas.versions.'):
                    from .canvas_versions import command
                    diagnostic_result = command(self, action, args, origin)
                elif action.startswith('canvas.apps.'):
                    from .canvas_apps import command
                    diagnostic_result = command(self, action, args, origin)
                elif action.startswith('canvas.views.'):
                    diagnostic_result, view_effects = self.canvas_views.command(action, args, origin)
                    effects.extend(view_effects)
                elif action in {'canvas.select','canvas.reopen','canvas.tabClose'}:
                    from .canvas_library import command
                    if action == 'canvas.select':
                        diagnostic_result = self.canvas_views.select_presentation(args, origin)
                    elif action == 'canvas.reopen' and self.state.get('canvas', {}).get('_presentationDisposed'):
                        current = self.state['canvas']
                        self.canvas_views.select_presentation({'id': current['id'],
                            'version': current.get('selectedVersion') or current.get('revision', 1)}, origin)
                    if action != 'canvas.select' or args.get('presentation', 'canvas') == 'canvas':
                        command(self.state,self.db,action,args)
                    if action=='canvas.select' and args.get('presentation', 'canvas') == 'canvas':
                        self.state['view']['settingsCanvas'] = False
                        self.state['view'].setdefault('canvasDraft',{}).update(library=False,open=False,browser=False,overview=False)
                elif action=='canvas.openExternal':
                    canvas=self.state['canvas']
                    if canvas.get('id')!=args['id'] or canvas.get('kind')!='browser':raise AppError('Select a browser artifact first.')
                    effects.append({'type':'browser.open','url':canvas['url']})
                elif action == 'canvas.copyPath':
                    from .canvas_paths import copy_path
                    diagnostic_result, path_effects = copy_path(self.state, args)
                    effects.extend(path_effects)
                elif action in {'canvas.copy', 'canvas.download'}:
                    canvas = self.state['canvas']
                    if args['id'] != canvas.get('id'):
                        raise AppError('This canvas has been replaced.')
                    content = canvas.get('url') if canvas.get('kind')=='browser' else canvas.get('content', json.dumps(canvas.get('surface', {}), indent=2))
                    from .canvas_downloads import filename
                    if action == "canvas.copy" and canvas.get("kind") == "pdf":
                        raise AppError("Select text in the PDF to copy it, or download the original file.")
                    download_name = filename(canvas)
                    version_query = '?version=' + str(canvas.get('selectedVersion') or canvas.get('app', {}).get('revision', canvas.get('revision', 1)))
                    if action=='canvas.copy' and canvas.get('contentResource'):
                        effects.append({'type':'clipboard.url','url':'/api/canvas/'+canvas['id']+'/source'+version_query,'canvasId':canvas['id']})
                    elif action=='canvas.download' and (canvas.get('kind') in {'babylon', 'image'} or canvas.get('contentResource')):
                        effects.append({'type':'download.url','url':'/api/canvas/'+canvas['id']+'/download'+version_query,'filename':download_name})
                    else:
                        effects.append({'type':'clipboard.write' if action == 'canvas.copy' else 'download',
                        'content':content,'filename':download_name,'mime':'text/plain','canvasId':args['id']})
                elif action=='canvas.show':
                    sid=args.get('sessionId',self.state.get('selectedSessionId'))
                    destination = args.get('presentation', 'canvas')
                    if args.get('kind') == 'settings' and destination != 'canvas':
                        raise AppError('Settings opens in Canvas.', 409)
                    from .agent_canvas import scope
                    workspace_id=scope(self,sid)[1] if sid else self.state['selectedWorkspaceId']
                    scoped={**self.state,'selectedSessionId':sid,'selectedWorkspaceId':workspace_id}
                    if args.get('kind') == 'settings':
                        if any(key in args for key in ('content', 'path', 'url', 'surface')):
                            raise AppError('Settings is a live app view; do not supply content or credentials.')
                        if not sid:
                            raise AppError('Create or select a chat before opening Settings in Canvas.')
                        from .settings_navigation import open_canvas
                        open_canvas(scoped, self.db)
                    else:
                        owner = self._session(sid) if sid else None
                        file_root = (owner.get('workingDirectory') or owner.get('workspace')) if owner else None
                        canvas_command(scoped,action,args,origin,file_root=file_root)
                    from .canvas_versions import assert_clean, reference, message_id, ordinary_preview
                    publication_message = message_id(scoped, sid)
                    if destination == 'inline':
                        from .canvas_views import INLINE_KINDS
                        if scoped['canvas']['kind'] not in INLINE_KINDS:
                            raise AppError('This artifact opens in Canvas.', 409)
                        if (not publication_message or self.clients.record() is None
                                or sid != self.state.get('selectedSessionId')):
                            raise AppError('Inline presentation needs an originating turn in the attached chat.', 409)
                    assert_clean(self, scoped['canvas']['id'])
                    # Stage the publication before its presentation guard. A
                    # refusal must not add a version/publication or change tabs.
                    scoped['canvasArtifacts'] = [copy.deepcopy(row) if row['id'] == scoped['canvas']['id'] else row
                                                 for row in scoped.get('canvasArtifacts', [])]
                    if 'canvasTabs' in scoped:
                        scoped['canvasTabs'] = copy.deepcopy(scoped['canvasTabs'])
                    remember(scoped,self.db)
                    diagnostic_result = {'id': scoped['canvas']['id'], 'revision': scoped['canvas'].get('revision', 1), 'reference': reference(scoped['canvas'])}
                    if sid==self.state.get('selectedSessionId') and scoped['selectedWorkspaceId']==self.state['selectedWorkspaceId']:
                        current = self.state.get('canvas', {})
                        retain_preview = (destination == 'canvas' and current.get('id') == scoped['canvas']['id']
                                          and ordinary_preview(current) and not current.get('_presentationDisposed')
                                          and (current.get('open') or (self.clients.record() or {}).get('canvasViews', {}).get('retained')))
                        if not retain_preview:
                            self.canvas_views.choose(scoped['canvas'], diagnostic_result['revision'], destination,
                                                     message_id=publication_message, origin=origin)
                        if destination == 'canvas':
                            if not retain_preview:
                                self.state['canvas']=scoped['canvas']
                            elif current['id'] in scoped.get('canvasTabs', {}):
                                scoped['canvasTabs'][current['id']]['view'] = copy.deepcopy(current.get('view', {}))
                            if args.get('kind') == 'settings':
                                self.state['view']['panel'] = None
                            self.state['view'].setdefault('canvasDraft',{}).update(library=False,open=False,browser=False,overview=False)
                    self.state['canvasArtifacts'] = scoped['canvasArtifacts']
                    if 'canvasTabs' in scoped:
                        self.state['canvasTabs'] = scoped['canvasTabs']
                    diagnostic_result['presentation'] = destination
                else:
                    canvas_command(self.state, action, args, origin)
            elif action == 'session.draft':
                if 'workspaceId' in args:
                    from .workspace_navigation import _path
                    workspace = next((row for row in self.state['workspaces'] if row['id'] == args['workspaceId']), None)
                    if (not workspace or workspace.get('available') is not True
                            or _path(workspace.get('path')) is None or args.get('workspace') != workspace['path']
                            or args.get('location', {}).get('kind', 'workspace') != 'workspace'
                            or not Path(workspace['path']).is_dir()):
                        raise AppError('This workspace folder is unavailable. Refresh workspaces before starting a chat.', 409)
                from .new_chat import open_draft
                open_draft(self, args)
                self.state['view']['workSurface'] = 'chat'
                if 'workspaceId' in args:
                    self.state['view']['navExpanded'] = False
            elif action == "session.create":
                if prepared_draft_id is not None and prepared_draft_id != self.clients.attachment_draft_id():
                    raise AppError('The original new-chat draft changed. Nothing was sent.', 409)
                if args.get('fromDraft') and not managed_creation and not args.get('workspace', '').strip():
                    raise AppError('Choose a workspace folder before starting this chat.')
                from .session_creation import prepare, apply
                try:
                    inherited = prepare(self, args, origin, caller_session_id)
                    session = self._new_session({**args, 'workspace': prepared_workspace} if prepared_workspace else args,
                                                new_root=True)
                    if args.get('id') or prepared_identity: session['id'] = args.get('id') or prepared_identity
                    handoff = self.handoffs.execution(action, args, origin, command_id, caller_session_id)
                    if handoff is not None:
                        session['handoffOrigin'] = self.handoffs.lineage(handoff)
                    if origin == 'scheduler' and args.get('inheritConfiguration', {}).get('scheduledRunId'):
                        source = self._session(args['inheritConfiguration']['sessionId'])
                        run = self.schedules.store.run(source['id'], args['inheritConfiguration']['scheduledRunId'])
                        # prepare qualified this exact source/run/destination under
                        # the app lock. Persist its lineage before the first mount.
                        session['scheduledOrigin'] = {'sessionId': source['id'], 'scheduleId': run['scheduleId'], 'runId': run['id']}
                        if self.assignments.current(source):
                            session['assignmentId'] = self.assignments.reserve_member(source, session['id'], command_id)
                            assignment = self.assignments.load(session['assignmentId'])
                            assignment['members'][session['id']]['status'] = 'bound'
                            self.assignments.save(assignment)
                    reused = (self._reusable_internal(session) if args.get('purpose') and not args.get('id')
                              and not prepared_identity and not args.get('inheritConfiguration') and not args.get('selection') else None)
                    if reused is None:
                        apply(self, session, inherited)
                        from .collaboration import CREATION
                        creation = CREATION.get()
                        if creation and creation[:2] == (caller_session_id, hashlib.sha256(
                                json.dumps(args, sort_keys=True, separators=(",", ":")).encode()).hexdigest()):
                            apply(self, session, creation[3])
                            session["collaboration"] = copy.deepcopy(creation[4])
                            session['workingDirectory'] = creation[4]['workingDirectory']
                        self.worktrees.require_available_path(session.get('workingDirectory') or session['workspace'])
                        from .new_chat import initial_model
                        initial = initial_model(self.state, args, session['workspace'], session['bundle'])
                        if initial:
                            session['initialModel'] = initial
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if reused is not None:
                    session = reused
                else:
                    if args.get('fromDraft') and command_id:
                        session['creationCommandId'] = command_id
                    from .naming import persist
                    persist(self.data_dir,session,shared_rename=True)
                    self.state["sessions"].insert(0, session)
                diagnostic_result = {"sessionId": session['id']}
                # Internal work is never an ordinary chat and never changes
                # the shared selection.
                if args.get('select', True) and not args.get('purpose'):
                    if self.state['view'].get('setupJourney') == 'welcome':
                        self.state['view']['setupJourney'] = 'done'
                    from .workspace_canvas import select_session_workspace
                    select_session_workspace(self.state, session)
                    self.state["selectedSessionId"] = session["id"]
                    self.state['view']['workSurface'] = 'chat'
                    self.state["view"]["draft"] = ""
                    if client_id is not None and (previous_scope[0] is None or args.get('fromDraft')):
                        drafts = self.clients.record().setdefault("drafts", {})
                        documents = self.clients.record().setdefault("draftDocuments", {})
                        self.clients.draft(session["id"], drafts.pop("", ""), documents.pop("", None))
                        attachments = self.clients.record().setdefault('attachments', {})
                        attachments[session['id']] = attachments.pop('', [])
                        self.clients.bind_attachment_draft(session['id'])
                    self.state['view'].pop('newSessionDraft', None)
            elif action == "session.select":
                bundled = client_id is not None and self.clients.records.get(client_id, {}).get('kind') == 'web'
                session = self._session(args["id"], hydrate=not bundled)
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.state, session)
                previous = next((row for row in self.state['sessions'] if row['id'] == self.state.get('selectedSessionId')), None)
                if client_id is None and previous is not None and (self.state['view'].get('draft') or 'draft' in previous):
                    previous['draft'] = self.state['view'].get('draft', '')
                self.state["selectedSessionId"] = session["id"]
                self.state["view"]["workSurface"] = "chat"
                self.state["view"]["unreadVisit"] = session.get("manualUnread")
                self.state["view"]["draft"] = session.get('draft', '')
                if session.get('nativeProject') or bundled:
                    pending.append((self.history.refresh_session, (session['id'],)))
            elif action == "session.warm":
                session = self._session(args['id'])
                pending.append((self.warmup.schedule, (session['id'],)))
            elif action == 'runtime.retention.update':
                if not callable(getattr(self.runtime, 'configure_retention', None)):
                    raise AppError('This host does not support worker retention settings.')
                from .deployment import load_server_config, save_server_config
                config = load_server_config(self.data_dir)
                config['runtime'].update(args['patch'])
                # Validate and durably save before applying the running policy.
                from .runtime_retention import validate_retention
                policy = validate_retention(config['runtime'])
                policy['max_background_starts'] = self.runtime.retention.settings['max_background_starts']
                saved = save_server_config(self.data_dir, {**config, 'runtime': policy})
                self.runtime.configure_retention(policy)
                self.server_config = {**getattr(self, 'server_config', saved), 'runtime': policy}
                self.state['runtime']['retention'] = dict(policy)
            elif action == "session.naming.backfill":
                backfill = getattr(self, 'naming_backfill', None)
                if backfill is None:
                    from .naming_backfill import NamingBackfill
                    backfill = self.naming_backfill = NamingBackfill(self)
                try:
                    diagnostic_result, identities = backfill.start(args, command_id)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if identities:
                    pending.append((backfill.run, (identities,)))
            elif action == "session.naming":
                from .naming import set_automatic
                session = self._session(args['id'], hydrate=bool(args.get('regenerate')))
                if 'automatic' not in args and not args.get('regenerate'):
                    raise AppError('Choose automatic naming or regeneration.')
                if args.get('regenerate'):
                    if not self.runtime or not callable(getattr(self.runtime, 'control', None)):
                        raise AppError('The naming runtime is unavailable.', 503)
                    if session.get('naming', {}).get('status') == 'working':
                        raise AppError('A chat name is already being generated.', 409)
                    if session.get('configurationBusy'):
                        raise AppError('Finish changing the conversation configuration before regenerating its name.', 409)
                    if not session.get('messages'):
                        raise AppError('Send a message before generating a chat name.')
                if 'automatic' in args:
                    set_automatic(self.data_dir, session, args['automatic'])
                if args.get('regenerate'):
                    from .naming import prepare_regeneration
                    base = prepare_regeneration(self.data_dir, session)
                    session['naming'] = {'status': 'working'}
                    pending.append((self._regenerate_name, (copy.deepcopy(session), base)))
                diagnostic_result = {'automatic': session.get('autoName'), 'status': session.get('naming', {}).get('status', 'idle')}
            elif action == "session.rename":
                if not args["title"].strip():
                    raise AppError("Enter a title.")
                session=self._session(args['id'], hydrate=False)
                from .naming import persist, set_automatic
                renamed = {**session, 'title':args['title'].strip(), 'titleSource':'manual','autoName':False}
                set_automatic(self.data_dir, renamed, False)
                persist(self.data_dir,renamed,shared_rename=True)
                session.update({key:renamed[key] for key in ('title','titleSource','nativeNameSource','nativeNameRevision','nativeNamePolicyRevision','description','autoName') if key in renamed})
            elif action == 'session.takeover':
                session = self._session(args['id'])
                if not self.runtime:
                    raise AppError('The Amplifier runtime is unavailable.')
                if session.get('ownership', {}).get('status') in {'taking-over', 'yielding', 'yield-failed'}:
                    raise AppError('An ownership change is already in progress.', 409)
                session['ownership'] = {'status': 'taking-over'}
                session.pop('error', None)
                session.pop('failure', None)
                session.pop('health', None)
                pending.append((self._takeover, (copy.deepcopy(session),)))
            elif action in MESSAGE_INTERACTIONS:
                if reveal_message:
                    session = self._session(args["sessionId"])
                    from .message_interactions import reconcile_native_reveal
                    reconcile_native_reveal(session, reveal_message)
                from .message_interactions import command
                try:
                    diagnostic_result = command(self, action, args, origin=origin)
                except AppError as exc:
                    # This synchronous reaction boundary rejects before changing
                    # annotations. Retain that exact evidence for a lost reply;
                    # absence of a receipt still never means rejection.
                    if action == 'message.reaction' and command_id and 400 <= exc.status < 500:
                        rejected = {'accepted': False, 'status': exc.status, 'error': str(exc),
                                    'effects': [], 'result': {'sessionId': args['sessionId'],
                                    'messageId': args['messageId'], 'reactionActor': 'assistant' if origin == 'agent' else 'user',
                                    'sent': False}}
                        self.db.execute('INSERT INTO commands VALUES (?,?,?)',
                                        (command_id, fingerprint, json.dumps(rejected)))
                        self.db.commit()
                    raise
            elif action == 'message.copy':
                source = self._session(args['sessionId'])
                message = next((m for m in source['messages'] if m['id']==args['messageId']), None)
                if not message:
                    raise AppError('This message is no longer available.',404)
                identity = str(uuid.uuid4())
                self.state['view']['messageCopy'] = {**args,'requestId':identity,'status':'pending'}
                from .conversation_export import chat_reference_lines
                fallback = '\n\n'.join(chat_reference_lines(copy_references, message))
                effects.append({'type':'message.copy',**args,'requestId':identity,
                                **({'chatReferenceText':fallback} if fallback else {})})
            elif action == 'message.copyResult':
                result = self.state['view'].get('messageCopy',{})
                if result.get('requestId') == args['requestId']:
                    result.update(status=args['status'],message=args.get('message',''))
            elif action == 'message.edit':
                source = self._session(args['sessionId'])
                original = next((m for m in source['messages'] if m['id']==args['messageId'] and m['role']=='user'),None)
                if (not original or original.get('inputOrigin') in {'agent', 'peer', 'scheduler'}
                        or original.get('via') in {'peer', 'schedule'}
                        or self.peer_attribution.resolve(source, [original])[0].get('attribution')):
                    raise AppError('Choose one of your own messages to edit.')
                if (original.get('replyTo') or {}).get('version') == 2:
                    raise AppError('This message contains immutable quotes. Send a reply with your correction; its saved quotations were kept.', 409)
                text = args['text'].strip()
                if not text:
                    raise AppError('Enter a message before regenerating.')
                if source.get('configurationBusy') or source.get('fileAccessPending'):
                    raise AppError('Wait for configuration changes to finish.',409)
                if not self.runtime:
                    raise AppError('The Amplifier runtime is unavailable.')
                if work_paused(self.state):
                    raise AppError('An update is activating. Please retry in a moment.',409)
                if args.get('mode', 'fork') == 'current':
                    if source['status'] in {'working','running','starting','stopping','busy'} or any(
                            row.get('status') in {'working','running','starting','stopping','queued','pending'} or row.get('persistent') and row.get('status')=='idle'
                            for row in source.get('workers', [])) or any(row.get('status', 'pending')=='pending' for row in source.get('approvals', [])):
                        raise AppError('Finish active work and pending interactions before editing history.',409)
                    if source.get('ownership', {}).get('status') in {'blocked','yielding','yielded','yield-failed','taking-over'}:
                        raise AppError('Choose Continue here before editing this conversation.',409,code='session_busy')
                    session = source
                    session['historyManaged'] = False
                    session['configurationBusy'] = True
                    session['historyEdit'] = {'operationId':command_id or str(uuid.uuid4()),'messageId':original['id'],
                        'text':text,'via':'text' if original.get('via')=='text' else 'chat','inputOrigin':origin,
                        'attachments':copy.deepcopy(original.get('attachments',[])), 'replyTo':copy.deepcopy(original.get('replyTo')), 'phase':'working'}
                    pending.append((self._edit_current,(copy.deepcopy(session),)))
                else:
                    session = self._new_session({'title':source['title']+' · edited','workspace':source['workspace'],'bundle':source['bundle']})
                    from .session_store import fork_session
                    # Legacy UI-only sources keep their existing ID membership;
                    # canonical sources require exact pre-alignment provenance.
                    media_origins = {} if source.get('nativeProject') else None
                    try:
                        session.update(fork_session(self.data_dir,source,session['id'],before_message_id=original['id'],
                                                    media_origins=media_origins))
                    except ValueError as exc:
                        raise AppError(str(exc),409) from exc
                    session['editOrigin'] = {'sessionId':source['id'],'messageId':original['id']}
                    input_id = command_id or str(uuid.uuid4())
                    self._message(session,'user',text,'text' if original.get('via')=='text' else 'chat',inputId=input_id,inputOrigin=origin,attachments=copy.deepcopy(original.get('attachments',[])),**({'replyTo':copy.deepcopy(original['replyTo'])} if original.get('replyTo') else {}),delivery={'status':'sending'})
                    self._activity(session,'queued','Generating from your edited message.',reset=True)
                    session['status']='working'
                    ensure_turn(session,input_id,text)
                    from .naming import persist
                    persist(self.data_dir,session,shared_rename=True)
                    self.state['sessions'].insert(0,session)
                    self.state['selectedSessionId']=session['id']
                    self.state['view']['messageEdit']=None
                    from .workspace_canvas import select_session_workspace
                    select_session_workspace(self.state,session)
                    pending.append((self._send,(copy.deepcopy(session),text,input_id)))
            elif action == 'session.inspect':
                diagnostic_result = prepared_health
                from .session_health import inspection_stamp
                if inspection_stamp(self._session(args['id'])) == inspection_stamp(snapshot):
                    self._session(args['id'])['health'] = diagnostic_result
                else:
                    # A lifecycle/failure can change while the bounded disk
                    # inspection runs. Never cache that older view as current.
                    diagnostic_result = {**diagnostic_result, 'stale': True}
            elif action in {"session.fork", "session.recover"}:
                source = self._session(args["id"])
                if source.get("configurationBusy") or source.get('fileAccessPending'):
                    raise AppError("Wait for configuration changes to finish before forking.",409)
                session = self._new_session({"title": source["title"] + (" · recovery" if action == "session.recover" else " · fork"), "workspace": source["workspace"], "bundle": source["bundle"]})
                # loop-live checkpoints before publishing idle. Fork that full
                # context (including tool receipts), never reconstruct a running
                # session from the visible assistant bubbles alone.
                from .session_store import fork_session
                media_origins = {} if source.get('nativeProject') else None
                try:
                    session.update(fork_session(self.data_dir,source,session["id"],turn=args.get("turn"),after_message_id=args.get('afterMessageId'),
                                                recovery=action == "session.recover", media_origins=media_origins))
                except ValueError as exc:
                    raise AppError(str(exc),409) from exc
                from .naming import persist
                persist(self.data_dir,session,shared_rename=True)
                self.state["sessions"].insert(0, session)
                self.state["selectedSessionId"] = session["id"]
                self.state['view']['messageEdit']=None
                if action == 'session.recover':
                    diagnostic_result = {'sessionId': session['id'], 'sourceSessionId': source['id'], 'workReplayed': False, 'status': 'idle'}
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.state,session)
            elif action == 'session.export' and args.get('format') == 'markdown':
                from .resource_files import put
                source, (content, summary) = prepared_export
                reference = put(self.db, content)
                from datetime import datetime, timezone
                import re
                identity = hashlib.sha256(json.dumps([source['id'], reference, summary], sort_keys=True).encode()).hexdigest()
                captured = datetime.now(timezone.utc).isoformat()
                title = re.sub(r'[^A-Za-z0-9_-]+', '-', source.get('title') or 'conversation').strip('-')[:64] or 'conversation'
                filename = title + '-' + captured[:10] + '-' + identity[:8] + '.md'
                diagnostic_result = {'snapshotId': identity, 'sessionId': source['id'], 'filename': filename,
                    'mimeType': 'text/markdown', 'content': reference, 'summary': summary, 'capturedAt': captured,
                    'statePath': '/conversationExports/' + identity + '/content',
                    'url': '/api/conversation/exports/' + identity}
                exports = self.state.setdefault('conversationExports', {})
                diagnostic_result = copy.deepcopy(exports.setdefault(identity, diagnostic_result))
                destination = args.get('destination', 'download')
                if destination != 'none':
                    request_id = str(uuid.uuid4())
                    self.state['view']['conversationExport'] = {'sessionId': source['id'], 'requestId': request_id, 'status': 'pending'}
                    effects.append({'type': 'conversation.export', 'requestId': request_id,
                                    'destination': destination, 'url': diagnostic_result['url']})
            elif action == 'session.exportDeliver':
                snapshot = self.state.get('conversationExports', {}).get(args['snapshotId'])
                if not snapshot or snapshot['sessionId'] != args['id']:
                    raise AppError('This reviewed export is unavailable for the selected conversation.', 404)
                diagnostic_result = copy.deepcopy(snapshot)
                request_id = str(uuid.uuid4())
                self.state['view']['conversationExport'] = {'sessionId': args['id'], 'requestId': request_id, 'status': 'pending'}
                effects.append({'type': 'conversation.export', 'requestId': request_id,
                                'destination': args['destination'], 'url': snapshot['url']})
            elif action == 'session.exportResult':
                result = self.state['view'].get('conversationExport', {})
                if result.get('requestId') == args['requestId']:
                    result.update(status=args['status'], message=args.get('message', ''))
            elif action in {"state.export", "session.export", "theme.export"}:
                if action == 'session.export' and args.get('destination', 'download') != 'download':
                    raise AppError('Choose Markdown to copy a conversation or create a readable snapshot.')
                if action == 'theme.export':
                    from .theme_library import export
                    diagnostic_result = export(self, args.get('id'), args.get('format', 'css'))
                    zipped = diagnostic_result.get('format') == 'zip'
                    content = {'base64': diagnostic_result['base64']} if zipped else diagnostic_result['css']
                    mime = 'application/zip' if zipped else 'text/css'
                    filename = diagnostic_result['filename'] if zipped or args.get('id') else 'amplifier-skin.css'
                else:
                    content = json.dumps(self.state if action == 'state.export' else self._session(args['id']), indent=2)
                    mime, filename = 'application/json', 'amplifier-export.json'
                effects.append({"type": "download", "filename": filename, "mime": mime, "mimeType": mime, "content": content})
            elif action == "attachment.add":
                from .attachments import save,MAX_FILES
                target = args.get('sessionId', self.state.get('selectedSessionId'))
                target = self.clients.attachment_target(target, args.get('expectedDraftId'))
                session = self._session(target) if target is not None else None
                draft=self.clients.attachments(session)
                if len(draft)>=MAX_FILES:raise AppError('Attach up to 8 files per message.')
                draft.append(save(self.data_dir,args['name'],args['base64'],source=args.get('source')))
            elif action == "attachment.remove":
                target = args.get('sessionId', self.state.get('selectedSessionId'))
                session = self._session(target) if target is not None else None
                draft=self.clients.attachments(session)
                draft[:]=[row for row in draft if row['id']!=args['id']]
            elif action.startswith('question.'):
                diagnostic_result = self.questions.dispatch(action, args, origin, client_id, pending)
            elif action == 'conversation.configure':
                from .next_message_configuration import save
                diagnostic_result = save(self, args, configuration_authority)
            elif action == "conversation.send":
                session = self._session(args.get("sessionId"))
                if ('expectedConfigurationRevision' in args
                        and args['expectedConfigurationRevision'] != session.get('nextMessageRevision', 0)):
                    raise AppError('Next message settings changed. Review them before sending.', 409)
                from .conversation_steering import target as steering_target, recover_failed_generation
                # A run ending before delivery must not turn this correction
                # into new work under a policy that is awaiting replacement.
                allow_next_turn = args.get('allowNextTurn') is True and not session.get('fileAccessPending')
                # Whitespace can carry Markdown meaning; validate blank input
                # separately without changing the saved or delivered message.
                text = args["text"]
                from .message_interactions import resolve_quote, resolve_document, document_text
                quote = resolve_quote(session, args.get("replyId"))
                if args.get('document') is not None:
                    if quote:
                        raise AppError('Choose one quoted document, not a second reply context.')
                    document = resolve_document(session, args['document'])
                    quote = {'version': 2, 'segments': document['segments']}
                    text = document_text(document)
                requested=args.get('attachmentIds',[])
                available={row['id']:row for row in self.clients.attachments(session)}
                if any(identity not in available for identity in requested):raise AppError('An attachment is no longer in this draft. Refresh and retry.')
                attachments=[available[identity] for identity in requested]
                if not text.strip():
                    if not attachments:
                        raise AppError("Enter a message or attach a file.")
                    text = 'Please review the attached files.'
                if not self.runtime:
                    raise AppError("The Amplifier runtime is unavailable.")
                stopped_generation = (recover_failed_generation(self, session, recovery_boundary)
                    if origin == 'ui' and not update_message and args.get('expectedGenerationId') is None else None)
                target_generation = None if update_message else steering_target(session, args.get('expectedGenerationId'), allow_next_turn)
                input_id = command_id or str(uuid.uuid4())
                from .chat_navigation import recent_activity, prepare_human_post, promote_human_post
                # Origin is chosen by the host adapter, never role/via/action args.
                post_previous = prepare_human_post(session) if origin == 'ui' else None
                previous_activity = recent_activity(session)
                session['historyManaged'] = False
                session.setdefault('surfaceInputs', {})[input_id] = self.surface_context.bind_input(session['id'])
                self.computer_visual.bind_input(session['id'], input_id)
                session['surfaceInputs'] = dict(list(session['surfaceInputs'].items())[-16:])
                message = self._message(session, "user", text, args.get("via", self.state["view"]["mode"]), human_post=post_previous is not None,inputId=input_id,inputOrigin=origin,attachments=attachments,**({'steering': {'generationId': target_generation, 'disposition': 'sending', 'allowNextTurn': allow_next_turn}} if target_generation else {}),**({"replyTo":quote} if quote else {}),delivery={'status':'sending'})
                handoff = self.handoffs.execution(action, args, origin, command_id, caller_session_id)
                if handoff is not None:
                    message['handoffOrigin'] = self.handoffs.lineage(handoff)
                    message['inputActor'] = self.handoffs.actor(handoff)
                if post_previous is not None:
                    promote_human_post(session, message, post_previous)
                if session["title"] in {"New chat","New conversation","A new conversation","Untitled conversation"}:
                    from .naming import fallback_title
                    session["title"] = fallback_title(text) or text[:64]
                from .naming import persist
                persist(self.data_dir,session)
                if update_message:
                    from .update_drain import hold_message
                    hold_message(self, session, message)
                    self._clear_sent_draft(session, message, text, args.get("preserveDraft", False))
                elif not target_generation:
                    preparing = session.get('preparation', {}).get('status') == 'preparing'
                    self._activity(session, "runtime-setup" if preparing else "queued",
                        (session['preparation'].get('detail') or 'Preparing this chat…') if preparing else "Your message is queued for Amplifier.",
                        reset=session["status"] not in {"working", "starting"})
                    session["status"] = "working"
                    session.pop("error", None)
                    ensure_turn(session,input_id,text)
                if not update_message:
                    pending.append((self._send, (copy.deepcopy(session), text, input_id, previous_activity, args.get('preserveDraft', False))))
            elif action == "conversation.delivery":
                session = self._session(args['sessionId'])
                pending.append((self._check_delivery, (session['id'], args['inputId'])))
            elif action == "conversation.retry":
                from .message_delivery import find_message
                session = self._session(args['sessionId'])
                message = find_message(session, args['inputId'])
                if message is None:
                    raise AppError('This message is not saved in this conversation.', 404)
                if message.get('steering'):
                    raise AppError('A steering message belongs to its original run and cannot be resent as a new turn. Check delivery, or write a new message explicitly.', 409)
                if message.get('delivery', {}).get('status') == 'accepted':
                    diagnostic_result = {'delivery': 'accepted', 'resent': False}
                else:
                    if message.get('delivery', {}).get('status') not in {'unknown', 'failed', 'sending'}:
                        raise AppError('This message is not awaiting delivery recovery.', 409)
                    if message is not next((m for m in reversed(session['messages']) if m.get('role') == 'user'), None):
                        raise AppError('Only the latest unconfirmed message can be resent. Review later messages first.', 409)
                    if session['status'] in {'working', 'starting', 'running', 'stopping'} or message.get('delivery', {}).get('status') == 'sending':
                        raise AppError('Wait for the current delivery or work to settle before resending.', 409)
                    if message.get('delivery', {}).get('status') != 'failed' and not args.get('confirmUncertain'):
                        raise AppError('Delivery is uncertain. Confirm that sending again may repeat earlier work.', 409, code='delivery_uncertain')
                    if not self.runtime or not hasattr(self.runtime, 'retry'):
                        raise AppError('This runtime does not support message recovery.', 409)
                    original_status = session['status']
                    known_undelivered = message.get('delivery', {}).get('status') == 'failed'
                    original_turn = copy.deepcopy(next((t for t in session.get('execution', {}).get('turns', []) if t['id'] == args['inputId']), {}))
                    self._delivery(session, args['inputId'], 'sending')
                    self._activity(session, 'queued', 'Sending your saved message again.', reset=True)
                    session['status'] = 'working'
                    session.pop('error', None)
                    tree = ensure_turn(session, args['inputId'], message['text'])
                    turn = next(t for t in tree['turns'] if t['id'] == args['inputId'])
                    turn['phase'] = 'running'
                    turn['retriedAt'] = time.time()
                    turn.pop('endedAt', None)
                    pending.append((self._retry_message, (copy.deepcopy(session), message['text'], args['inputId'], original_status, original_turn, known_undelivered)))
            elif action == "conversation.stop":
                session = self._session(args.get("sessionId"))
                session["interruptionRevision"] = session.get("interruptionRevision", 0) + 1
                session["lastInterruption"] = {"commandId": command_id, "at": time.time(), "origin": origin}
                session["status"] = "stopping"
                pending.append((self._stop, (session["id"],)))
            elif action == "worker.spawn":
                session = self._session(args.get("sessionId"))
                text = args["instruction"].strip()
                if not text:
                    raise AppError("Describe the worker's task.")
                if not self.runtime:
                    raise AppError('The Amplifier runtime is unavailable.')
                session['historyManaged'] = False
                instruction = "Delegate the following task to a background worker lane using your delegation tool. Keep the main conversation available. Task: " + text
                if args.get("bundle"):
                    instruction += "\nRequested worker bundle: " + args["bundle"]
                input_id = command_id or str(uuid.uuid4())
                self._message(session, "user", text, "worker request", inputId=input_id)
                session["status"] = "working"
                ensure_turn(session,input_id,text)
                pending.append((self._send, (copy.deepcopy(session), instruction, input_id)))
            elif action in {"worker.stop", "worker.steer", "worker.message"}:
                session = self._session(args.get("sessionId"))
                if not self.runtime:
                    raise AppError("The execution runtime is unavailable.")
                worker = next((row for row in session["workers"] if row["id"] == args["id"]), None)
                if not worker:
                    raise AppError("Worker not found in this conversation.", 404)
                if action == "worker.message" and (not worker.get("persistent") or worker.get("status") not in {"idle", "running"}):
                    raise AppError("This worker cannot receive a follow-up.", 409)
                if action == "worker.stop":
                    worker["interruptionRevision"] = worker.get("interruptionRevision", 0) + 1
                    worker["lastInterruption"] = {"commandId": command_id, "at": time.time(), "origin": origin}
                method = self.runtime.stop_worker if action == "worker.stop" else self.runtime.message_worker if action == "worker.message" else self.runtime.steer_worker
                call_args = (session["id"], args["id"]) + ((args["text"], command_id) if action == "worker.message" else (args["text"],) if action == "worker.steer" else ())
                pending.append((method, call_args))
            elif action == "approval.respond":
                session = self._session(args.get("sessionId"))
                approval = next((a for a in session["approvals"] if a["id"] == args["id"]), None)
                if approval and approval.get('resolutionReason') == 'host_restarted':
                    raise AppError('This permission request expired when the app restarted. Review the saved work and ask again if needed; nothing was replayed.', 409)
                if not approval or approval.get("status") != "pending":
                    raise AppError("This permission request is no longer pending.", 409)
                if origin not in {"ui", "user"}:
                    raise AppError("This tool requested user approval. It must be answered by the user.", 403)
                decision = "allow" if args["decision"] in {"allow", "approve"} else "deny"
                offered = approval.get("options") or ["allow", "deny"]
                selected = next((value for value in offered
                    if isinstance(value, str) and value.casefold() == decision), None)
                if selected is None:
                    raise AppError("This permission request does not offer that decision.", 409)
                approval["status"] = decision
                comment = args.get('comment', '').strip()
                if comment:
                    approval['comment'] = comment
                    self._message(session, 'user', f'Permission {decision}:\n\n{comment}', 'permission',
                                  approvalId=args['id'], inputOrigin='approval')
                if self.runtime:
                    values = (session["id"], args["id"], selected) + ((comment,) if comment else ())
                    pending.append((self.runtime.approval, values))
            elif action == "attention.read":
                from .attention import snapshot
                # Acknowledge current owner facts, including globals changed
                # since the last published projection. Only indexed notice
                # owners supply session metadata; idle history stays dormant.
                current = {item['id']:item for item in snapshot({
                    **self.state, 'sessions': self.directory.attention_rows(self.state)})['items']}
                if any(identity not in current for identity in args['ids']):
                    raise AppError('Attention items changed; refresh before marking them read.')
                receipts = self.state.setdefault('attentionRead', {})
                for identity in args['ids']:
                    if 'fingerprints' not in args or args['fingerprints'].get(identity)==current[identity]['fingerprint']:
                        receipts[identity] = current[identity]['fingerprint']
                self.state['attentionRead'] = {key:value for key,value in receipts.items() if key in current}
            elif action == "view.update":
                patch = dict(args["patch"])
                from .settings_navigation import route_legacy
                patch = route_legacy(self.state, patch)
                allowed = {"mode", "panel", "draft", "scheme", "layout", "selectedWorkerId", "contextVisible", "commandsVisible", "notificationPermission", "themeDraft", "themeDraftName", "themePreview", "newSessionDraft", "sessionSetup", "workerDraft", "notice", "agentAction", "agentArgs", "bundleManager", "moduleEditor", "settingsSection", "maintenanceDraft", "providerEditor", "aiConnectionEditor", "imageSettingsEditor", "routingEditor","registryDraft", "historyFilter", "runtimeDraft", "expandedExecutions", "executionExpanded", "executionDetails", "settingsExpanded", "settingsRootVisit", "settingsFilters", "locationPicker", "composerModel", "composerBundle", "bundleDefaultsDraft", "bundleSources", "canvasWidth", "canvasOverviewWidth", "navWidth", "canvasFocused", "canvasControlsPinned", "canvasControlsExpanded", "toolbarMenuOpen", "navPinned", "navExpanded", "navSectionsCollapsed", "navRecentView", "navPinnedPage", "navFilter", "navChatPage", "navChatScope", "navLocationFilter", "navWorkspacePath", "navWorkspaceFilter", "navWorkspacePage", "navWorkspaceAncestorsOpen", "subagentHistory", "workspaceDraft", "canvasDraft", "messageEdit", "smartToolsEditor", "feedbackDraft", "feedbackFollowupDraft", "feedbackCorrectionDraft", "feedbackLifecycleDraft", "diagnosticsDraft"}
                allowed.update({'navWorkspaceArchive', 'projectPermissionsDraft', 'planExpanded', 'settingsCanvas', 'setupJourney', 'navArchive', 'navCollection', 'navSort', 'navShowAgentCreated', 'navRecentLimit', 'workSurface', 'workWorkspaceId', 'workWorkspaceTab', 'workspaceStarterEditor', 'configurationDrafts', 'workWorkspaceSettings'})
                allowed.update({'workHomeView', 'workHomeFilter', 'workHomeScope', 'workHomeQuery', 'workHomePages'})
                allowed.update({'navMode', 'navStatusCollapsed', 'navStatusExpanded', 'navStatusPages'})
                allowed.add('draftDocument')
                if 'draftDocument' in patch and 'draft' not in patch:
                    raise AppError('Save quoted content with its draft text.')
                if 'planExpanded' in patch:
                    choices = patch['planExpanded']
                    if not isinstance(choices, dict) or len(choices) > 500 or any(not isinstance(k, str) or len(k) > 200 or type(v) is not bool for k, v in choices.items()):
                        raise AppError('Plan expansion choices must map chat IDs to booleans (up to 500).')
                if 'setupJourney' in patch and patch['setupJourney'] not in {'welcome','connect','assisted','done'}:
                    raise AppError('Invalid setup step.')
                if 'settingsCanvas' in patch and type(patch['settingsCanvas']) is not bool:
                    raise AppError('Settings canvas visibility must be true or false.')
                if 'workspaceStarterEditor' in patch:
                    editor = patch['workspaceStarterEditor']
                    if (not isinstance(editor, dict) or set(editor) - {'id', 'detailOpen'}
                            or ('id' in editor and editor['id'] is not None and not isinstance(editor['id'], str))
                            or ('detailOpen' in editor and type(editor['detailOpen']) is not bool)):
                        raise AppError('Starter navigation accepts only an ID and detail visibility.')
                if set(patch) - allowed:
                    raise AppError("Unknown view setting.")
                for key, options in {"mode": {"call", "text", "chat"}, "scheme": {"light", "dark", "system"}, "layout": {"balanced", "conversation", "work"}}.items():
                    if key in patch and patch[key] not in options:
                        raise AppError("Invalid " + key)
                for key, minimum in (("canvasWidth", 300), ("canvasOverviewWidth", 300), ("navWidth", 216)):
                    if key in patch and (type(patch[key]) not in {int, float} or not minimum <= patch[key] <= 16384):
                        raise AppError(f"{key} must be between {minimum} and 16384 pixels.")
                for key in ("navPinned", "navExpanded", "navWorkspaceAncestorsOpen", "canvasFocused", "canvasControlsPinned", "canvasControlsExpanded", "toolbarMenuOpen"):
                    if key in patch and type(patch[key]) is not bool:
                        raise AppError("Layout switches must be true or false.")
                if patch.get('settingsCanvas'):
                    if not self.state.get('selectedSessionId'):
                        raise AppError('Create or select a chat before opening Settings in Canvas.')
                    patch['panel'] = None
                elif patch.get('panel') in {'settings','appearance'}:
                    patch['settingsCanvas'] = False
                from .workspace_navigation import view_patch
                from .chat_navigation import view_patch as chat_view_patch
                try:
                    patch = view_patch(self.state, chat_view_patch(patch))
                    if patch.get("workWorkspaceId") and not any(row["id"] == patch["workWorkspaceId"] for row in self.state["workspaces"]):
                        raise ValueError("This workspace is no longer registered.")
                except ValueError as exc:
                    raise AppError(str(exc)) from None
                from .runtime_navigation import update as runtime_view_patch
                patch = runtime_view_patch(self.state["view"], copy.deepcopy(patch))
                if 'newSessionDraft' in patch:
                    from .new_chat import validate_setup
                    try:
                        patch['newSessionDraft'] = validate_setup(patch['newSessionDraft'])
                    except ValidationError as exc:
                        raise AppError(exc.message) from None
                if patch.get('panel'):
                    patch['toolbarMenuOpen'] = False
                if 'draft' in patch:
                    if not isinstance(patch['draft'], str):
                        raise AppError('Draft must be text.')
                    if client_id is None and patch.get('draftDocument') is not None:
                        raise AppError('Attach a client to save an editor checkpoint.')
                    target = args.get('sessionId', self.state.get('selectedSessionId'))
                    if client_id is not None:
                        if target is not None:
                            self._session(target, hydrate=False)
                        from .message_interactions import resolve_document
                        rich = patch.pop('draftDocument', ...)
                        if rich is not None and rich is not ...:
                            rich = resolve_document(self._session(target) if target is not None else None,
                                                    rich, draft=True)
                        self.clients.draft(target, patch.pop('draft'), rich)
                    elif target:
                        self._session(target, hydrate=False)['draft'] = patch['draft']
                        if target != self.state.get('selectedSessionId'):
                            patch.pop('draft')
                    elif self.state.get('selectedSessionId') is not None:
                        raise AppError('Attach a client to save a draft without a conversation.')
                    else:
                        self.state['view']['newChatText'] = patch['draft']
                if patch.get('settingsCanvas'):
                    # Compatibility for earlier agents and saved setup flows.
                    from .settings_navigation import open_canvas
                    open_canvas(self.state, self.db)
                    patch['canvasDraft'] = {**patch.get('canvasDraft', self.state['view'].get('canvasDraft', {})), 'library': False}
                elif patch.get('settingsCanvas') is False and self.state.get('canvas', {}).get('kind') == 'settings':
                    patch['canvasDraft'] = {**patch.get('canvasDraft', self.state['view'].get('canvasDraft', {})), 'library': True}
                if patch.get('canvasDraft', {}).get('delivery') in {'outputs','publishing','permissions'}:
                    self.state.setdefault('canvas', {})['open'] = True
                self.state["view"].update(patch)
            elif action == 'feedback.excerpt.stage':
                diagnostic_result = self.feedback.excerpts.stage(args, origin, caller_session_id)
            elif action in {"feedback.attachment.add","feedback.attachment.remove"}:
                diagnostic_result = self.feedback.attachment_command(action, args, origin, caller_session_id)
            elif action == "feedback.submit":
                if self.feedback.accept(args, origin, caller_session_id):
                    pending.append((self.feedback.send, (args['requestId'],)))
                # Close only the submitting draft, atomically with durable acceptance.
                view=self.state['view']
                accepted_payload = json.loads(self.db.execute('SELECT payload FROM feedback_requests WHERE id=?', (args['requestId'],)).fetchone()[0])
                if not accepted_payload.get('_authority') and view.get('panel')=='feedback' and view.get('feedbackDraft',{}).get('pending',{}).get('requestId')==args['requestId']:
                    view['panel']=None
            elif action in {"feedback.edits.review", "feedback.get", "feedback.comment", "feedback.reconcile", "feedback.update", "feedback.close", "feedback.reopen",
                            "feedback.attachments.review", "feedback.attachments.add", "feedback.attachments.reconcile"}:
                if self.feedback.followups.accept(action, args, origin, caller_session_id):
                    pending.append((self.feedback.followups.run, (args['requestId'],)))
                if action.startswith('feedback.attachments.') and origin in {'agent', 'system'}:
                    diagnostic_result = self.feedback.additions.result(args['requestId'], caller_session_id)
            elif action.startswith("smartTools."):
                if not self.smart_tools: raise AppError("Smart Tools service is unavailable.")
                if action == 'smartTools.context':
                    self.smart_canvas.context(args)
                else:
                    scoped_args = copy.deepcopy(args)
                    if action in {'smartTools.call','smartTools.open'}:
                        scoped_args.setdefault('sessionId',self.state.get('selectedSessionId'))
                    if action == 'smartTools.appCall':
                        self.smart_canvas.admit_call(args)
                    if action in {'smartTools.reviewView', 'smartTools.updateView'}:
                        from .mcp_contract_review import _guard
                        _guard(self, args)
                    if action == 'smartTools.reconnectView':
                        from .mcp_view_recovery import admit
                        admit(self, args)
                    pending.append((self.smart_canvas.command,(action,scoped_args,command_id,origin)))
            elif action == 'bundle.default':
                from .preferences import SettingsStore
                bundle = args['bundle'].strip() if isinstance(args['bundle'], str) else None
                if bundle == '': raise AppError('Choose a bundle or clear the override.')
                workspace = str(Path(args.get('workspace') or self.state['settings']['workspace']).expanduser().resolve())
                if not Path(workspace).is_dir(): raise AppError('Choose an existing workspace.')
                if args['scope'] == 'app':
                    self.state['settings']['appBundle'] = bundle
                else:
                    def update_default(value):
                        if bundle: value.setdefault('bundle', {})['active'] = bundle
                        else: value.get('bundle', {}).pop('active', None)
                    SettingsStore(self.data_dir).update(workspace, 'local' if args['scope']=='workspace' else 'global', update_default)
                self._shared_preferences_stamp = None
                self._refresh_shared_preferences()
            elif action == 'configuration.patchPlacement.bind':
                from .patch_placement import admit
                try:
                    diagnostic_result = admit(self.management, args, origin, caller_session_id,
                        command_id, configuration_authority)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                except (OSError, KeyError, TypeError):
                    raise AppError('Patch configuration cannot be verified; no binding was admitted.', 409) from None
            elif action in {'configuration.operation.reconcile', 'configuration.operation.resume'}:
                from .configuration_recovery import command as recovery_command
                try:
                    diagnostic_result = recovery_command(self.management, action, args, origin, caller_session_id)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    raise AppError('Configuration recovery refused: ' + str(exc), 409) from None
            elif action.startswith(("bundle.","bundles.","configuration.","runtime.","permissions.","history.","maintenance.","notifications.","providers.","routing.","modules.","sources.","locations.")):
                if not self.management: raise AppError("Management service is unavailable.")
                values = (action, copy.deepcopy(args), command_id, configuration_authority)
                if action == 'configuration.apply' or action == 'runtime.control' and args.get('operation') == 'configuration.apply':
                    target = self._session(args.get('id') or args.get('sessionId'))
                    # Host-only provenance, captured before the background wait;
                    # never accept origin or caller identity from model arguments.
                    values += ({'origin': origin, 'callerSessionId': caller_session_id,
                        'generation': copy.deepcopy(target.get('collaborationGeneration')),
                        'target': self.management.configuration_target_binding(target)},)
                elif action == 'runtime.control' and args.get('operation') == 'context.recovery.request':
                    # Capture before the queue. A Stop or configuration change
                    # cannot lend new authority to this older command.
                    target = self._session(args['sessionId'])
                    values += ({'recovery': self.management.recovery_admission_binding(target)},)
                pending.append((self.management.command, values))
            elif action == "updates.failureDisposition":
                if origin not in {'ui', 'user'}:
                    raise AppError('Closing update uncertainty requires an explicit user action.', 403)
                if not self.update_manager: raise AppError("Update service is unavailable.")
                try:
                    diagnostic_result = self.update_manager.diagnostics.dispose(args['id'], args['expectedOccurrence'])
                except ValueError as error:
                    raise AppError(str(error), 409) from None
            elif action.startswith("updates."):
                if not self.update_manager: raise AppError("Update service is unavailable.")
                if action == 'updates.forceRestart':
                    if origin not in {'ui', 'user'}:
                        raise AppError('Force restart requires explicit user confirmation in Updates.', 403)
                    from .update_restart import require
                    try:
                        require(self.update_manager, args)
                    except ValueError as error:
                        raise AppError(str(error), 409) from None
                    self.state['updates']['forceRestartResult'] = {'requestId': command_id, 'status': 'queued'}
                if action == 'updates.featureInstall':
                    rows = self.state['updates'].setdefault('featureResults', {})
                    rows[command_id] = {'requestId':command_id, 'feature':args['feature'], 'phase':'queued',
                        'updatedAt':time.time(), 'detail':'Feature installation requested; awaiting qualification.'}
                    while len(rows) > 20:
                        rows.pop(next(iter(rows)))
                pending.append((self.update_manager.command, (action.split(".")[1],copy.deepcopy(args),command_id)))
            elif action == "settings.update":
                patch = dict(args["patch"])
                if set(patch) - {"preferredVoice", "fallbackVoice", "voiceName", "fallbackVoiceName", "voiceInterruptions", "bundle", "workspace", "notifications", "updates", "workspaces"}:
                    raise AppError("Unknown setting.")
                if 'workspaces' in patch:
                    from .workspace_placement import validate_settings
                    try:
                        options = validate_settings(patch['workspaces'])
                    except ValueError as exc:
                        raise AppError(str(exc)) from None
                    patch = {**patch, 'workspaces': {**self.state['settings'].get('workspaces', {}), **options}}
                for key in ("preferredVoice", "fallbackVoice"):
                    if key in patch and patch[key] not in ({"gpt-live-1", "gpt-realtime-2.1", ""} if key == "fallbackVoice" else {"gpt-live-1", "gpt-realtime-2.1"}):
                        raise AppError("Select a supported voice model.")
                if {'preferredVoice','voiceName'}.intersection(patch):
                    from .voice_options import voices_for,selected_voice
                    model=patch.get('preferredVoice',self.state['settings'].get('preferredVoice','gpt-live-1'))
                    if 'voiceName' in patch and (not isinstance(patch['voiceName'],str) or patch['voiceName'] not in voices_for(model)):
                        raise AppError('Choose a voice available for the selected model.')
                    patch={**patch,'voiceName':patch.get('voiceName',selected_voice(model,self.state['settings'].get('voiceName','marin')))}
                if {'fallbackVoice','fallbackVoiceName'}.intersection(patch):
                    from .voice_options import voices_for
                    model=patch.get('fallbackVoice',self.state['settings'].get('fallbackVoice','gpt-realtime-2.1'))
                    name=patch.get('fallbackVoiceName',self.state['settings'].get('fallbackVoiceName',''))
                    if 'fallbackVoiceName' in patch and (not isinstance(name,str) or name and name not in voices_for(model)):
                        raise AppError('Choose a voice available for the fallback model, or automatic.')
                    if name and name not in voices_for(model):
                        patch={**patch,'fallbackVoiceName':''}
                if 'voiceInterruptions' in patch and type(patch['voiceInterruptions']) is not bool:
                    raise AppError('Voice interruptions must be on or off.')
                if "bundle" in patch and (not isinstance(patch["bundle"],str) or not patch["bundle"].strip()):
                    raise AppError("Enter a default bundle.")
                if "updates" in patch:
                    options = patch["updates"]
                    if not isinstance(options,dict) or set(options)-{"autoCheck","autoInstall","intervalHours"}:
                        raise AppError("Invalid update settings.")
                    for key in ("autoCheck","autoInstall"):
                        if key in options and type(options[key]) is not bool: raise AppError("Update switches must be true or false.")
                    if "intervalHours" in options and (type(options["intervalHours"]) is not int or options["intervalHours"] not in CHECK_INTERVAL_HOURS):
                        raise AppError("Choose an update check interval of 1, 4 or 8 hours, or daily (24 hours).")
                    if options.get("autoCheck") is False and "autoInstall" not in options:
                        options = {**options, "autoInstall": False}
                    patch = {**patch, "updates": {**self.state["settings"].get("updates",{}), **options}}
                    if patch["updates"].get("autoInstall") and not patch["updates"].get("autoCheck"):
                        raise AppError("Enable automatic checking before automatic installation.")
                if {"preferredVoice", "fallbackVoice", "voiceName", "fallbackVoiceName", "voiceInterruptions", "bundle"}.intersection(patch):
                    from .preferences import SettingsStore
                    def save_shared(settings):
                        for old, key in (("preferredVoice", "preferred_model"), ("fallbackVoice", "fallback_model"), ("voiceName", "voice"), ("fallbackVoiceName", "fallback_voice"), ("voiceInterruptions", "interruptions")):
                            if old in patch:
                                settings.setdefault("voice", {})[key] = patch[old]
                        if "bundle" in patch:
                            settings.setdefault("bundle", {})["active"] = patch["bundle"]
                    SettingsStore(self.data_dir).update(self.state["settings"]["workspace"], "global", save_shared)
                self.state["settings"].update(copy.deepcopy(patch))
                from .workspace_placement import defaults as workspace_root
                self.state['workspaceDefaults'] = {'root': workspace_root(self), 'hostLabel': socket.gethostname()}
                self._refresh_shared_preferences()
            elif action in {'theme.save', 'theme.import', 'theme.update', 'theme.delete'}:
                from . import theme_library
                if action == 'theme.import':
                    diagnostic_result = theme_library.import_appearance(self, args)
                elif action == 'theme.save':
                    diagnostic_result = theme_library.save(self, args)
                else:
                    diagnostic_result = theme_library.mutate(self, args, deleting=action == 'theme.delete')
            elif action in {'theme.apply', 'theme.preview', 'theme.revert'}:
                from .canvas_apps import theme_command
                theme_command(self, action, args)
            elif action == "theme.reset":
                self.state["theme"] = {"name": "Amplifier Unified", "css": self.default_theme()}
                self.state["view"]["themePreview"] = False
            elif action == "notification.request":
                effects.append({"type": "notification.request"})
            elif action in {"call.start", "call.mute", "call.end", "call.keepAwake", "call.resumeAudio"}:
                call_args = dict(args)
                current_call = getattr(self.voice_service, 'call', None)
                scoped = bool(args.get('callId') or args.get('attemptId'))
                if (current_call and current_call.closed and args.get('attemptId')
                        and not args.get('callId') and args['attemptId'] != getattr(current_call, 'attempt_id', None)):
                    current_call = None  # A new local microphone setup owns no host call yet.
                if action in {'call.mute', 'call.end'} and scoped and current_call:
                    if (args.get('callId') and args['callId'] != current_call.id
                            or args.get('attemptId') and args['attemptId'] != getattr(current_call, 'attempt_id', None)):
                        raise AppError("This call is no longer active.", 409, code='stale_call')
                if action == "call.start":
                    if self.state.get("voicePreviewBusy"):
                        raise AppError("Wait for the voice preview to finish before starting a call.", 409)
                    if current_call and not current_call.closed:
                        raise AppError("A call is already active. End it before starting another.", 409, code='call_active')
                    session = self._session(args.get("sessionId"))
                    if origin == "agent":
                        from .agent_canvas import target
                        eligible = target(self, session["id"], client_id, required=True, connected_only=True)[0]
                        if client_id != eligible:
                            raise AppError("Choose a connected browser displaying the calling conversation before starting a call.", 409)
                    try: self.portability.write_context(session['id'])
                    except ValueError as exc: raise AppError(str(exc), 409) from exc
                    if self.voice_service and not self.voice_service.api_key:
                        raise AppError("Add an OpenAI API key in Voice settings to enable calls.")
                    session["historyManaged"] = False
                    # The browser owns microphone permission/setup. /connect
                    # claims shared connecting state only after it owns a call.
                    call_args["sessionId"] = session["id"]
                elif action == "call.mute":
                    if current_call or not scoped:
                        self.state["voice"]["muted"] = args["muted"]
                elif action == "call.keepAwake":
                    self.state["voice"]["keepAwake"] = args["enabled"]
                elif action == "call.end" and current_call:
                    # Capture the owner now. Delayed reconciliation must never
                    # close a replacement call, including explicit agent ends.
                    pending.append((self._end_call, (current_call,)))
                if action in {'call.end', 'call.mute'} and not scoped and current_call:
                    call_args.update({key: value for key, value in {
                        'callId': current_call.id, 'attemptId': getattr(current_call, 'attempt_id', None)
                    }.items() if value})
                self.state["voice"]["command"] = {"id": command_id or str(uuid.uuid4()), "type": action, "args": call_args}
                effects.append({"type": action, "args": call_args, **call_args})
            if action in {'session.create','session.fork','session.recover','message.edit'}:
                from .naming import persist
                persist(self.data_dir,session)
            if action in {'session.fork','session.recover'} or action == 'message.edit' and args.get('mode','fork')=='fork':
                canvas_mapping = fork_artifacts(self.state,source['id'],session,self.db)
                self.outputs.fork(source['id'],session,canvas_mapping,media_origins=media_origins)
            if client_id is None and previous_scope[0] != self.state.get('selectedSessionId'):
                previous=next((row for row in self.state['sessions'] if row['id']==previous_scope[0]),None)
                if previous is not None and (previous_draft or 'draft' in previous):previous['draft']=previous_draft
                selected=next((row for row in self.state['sessions'] if row['id']==self.state.get('selectedSessionId')),None)
                self.state['view']['draft']=selected.get('draft','') if selected else self.state['view'].get('newChatText','')
            if previous_scope != (self.state.get('selectedSessionId'),self.state.get('selectedWorkspaceId')):
                self.clients.restore_canvas()
            if client_id is not None and previous_scope[0] != self.state.get('selectedSessionId'):
                self.computer_visual.reconcile(client_id)
                self.clients.reconcile(client_id)
            if previous_scope[1] != self.state.get('selectedWorkspaceId') or action in {'workspace.select', 'workspace.add', 'workspace.create', 'session.select'}:
                # An explicit selection reveals its folder, including returning
                # to a workspace whose old browse scope otherwise still matches.
                from .workspace_navigation import NAV_KEYS
                for key in NAV_KEYS | {'navWorkspaceBrowseFor', 'navWorkspaceAncestorsOpen'}:
                    self.state['view'].pop(key, None)
            view_action = args.get('action') if action == 'canvas.views.command' else action
            if action not in {'session.pin', 'canvas.views.inspect', 'canvas.views.status'} and view_action != 'canvas.snapshot' and not action.startswith(('diagnostics.','view.','attention.','canvas.snapshot')):
                artifact_id = self.state.get('canvas', {}).get('id')
                if action.startswith('canvas.views.'):
                    artifact_id = args.get('resourceId')
                elif action.startswith(('canvas.apps.', 'canvas.versions.')):
                    artifact_id = args.get('id', (diagnostic_result or {}).get('id'))
                owner_id = args.get('sessionId',self.state.get('selectedSessionId'))
                if action.startswith('canvas.views.'):
                    artifact = next((row for row in self.state.get('canvasArtifacts', []) if row['id'] == artifact_id), {})
                    owner_id = artifact.get('sessionId')
                owner=next((s for s in self.state['sessions'] if s['id']==owner_id),{})
                if owner.get('historyManaged'):
                    owner = {}  # Browsing must not append to the observed CLI capture.
                stream='canvas' if action.startswith('canvas.') else 'smartTools' if action.startswith('smartTools.') else 'sessions' if action.startswith('session.') else 'workers' if action.startswith('worker.') else 'app'
                self.diagnostics.record(stream,{'event':'app:action','data':{'action':action,'origin':origin,'commandId':command_id,'sessionId':owner.get('id'),'runtimeSessionId':owner.get('runtimeSessionId'),'artifactId':artifact_id if stream=='canvas' else None}},session_id=owner.get('runtimeSessionId') or owner.get('id'),workspace=owner.get('workspace'))
            self.state["events"].append({"id": command_id, "action": action, "origin": origin, "at": time.time()})
            self.state["events"] = self.state["events"][-200:]
            for effect in effects:
                effect.update({"id": str(uuid.uuid4()), "createdAt": time.time(), "origin": origin, **({"clientId": client_id} if client_id else {})})
            self.state.setdefault("deviceCommands", []).extend(copy.deepcopy([effect for effect in effects if effect["type"] != "download" or view_action == "canvas.download"]))
            self.state["deviceCommands"] = self.state["deviceCommands"][-20:]
            receipt = {"accepted": True, "revision": self.state["revision"] + 1, "effects": effects}
            if action in {"worker.message", "worker.stop", "worker.steer"}:
                receipt.update(delivery="requested", commandAction=action, target={"sessionId": args["sessionId"], "workerId": args["id"]}, completed=False, effectsState="not_rolled_back")
            if action == 'session.create':receipt['sessionId']=session['id']
            if action in {'session.create', 'conversation.send'} and handoff is not None:
                receipt['handoffOrigin'] = self.handoffs.lineage(handoff)
                receipt['actor'] = self.handoffs.actor(handoff)
            if action == 'conversation.send':
                receipt.update(delivery=message['delivery']['status'], sessionId=session['id'], inputId=input_id)
                if stopped_generation is not None:
                    receipt['stoppedGenerationRecovery'] = stopped_generation
                if message.get('navigationPost'):
                    receipt['navigationPost'] = copy.deepcopy(message['navigationPost'])
            if action == 'conversation.retry':receipt['result']={'delivery':'sending', 'message':'The saved message is being checked and sent. No additional resend was started.'}
            if diagnostic_result is not None:receipt['result']=diagnostic_result
            if action in {"locations.create", "notifications.save"} or action.startswith("providers.") or action.startswith("smartTools.") and action != "smartTools.context" or action.startswith('permissions.') and args.get('scope') == 'session':
                receipt["operationId"] = command_id
            if action in {"feedback.submit", "feedback.get", "feedback.comment", "feedback.reconcile", "feedback.update", "feedback.close", "feedback.reopen",
                          "feedback.attachments.review", "feedback.attachments.add", "feedback.attachments.reconcile"}:
                receipt["requestId"] = args['requestId']
            if action in {'updates.featureInstall', 'updates.forceRestart'}:
                receipt['requestId'] = command_id
            if command_id:
                self.db.execute("INSERT INTO commands VALUES (?,?,?)", (command_id, fingerprint, json.dumps(receipt)))
            if defer_publish:
                # Admission and the queued receipt commit together. If the host
                # exits before starting the task, restart can mark it interrupted.
                queued = {'id': command_id, 'action': action, 'origin': 'app',
                          'target': {'canvasId': args['canvasId'], 'name': args['name']},
                          'status': 'queued', 'createdAt': time.time(), 'updatedAt': time.time()}
                self.state['smartTools']['operations'].append(queued)
                self.smart_tools.persist_operation(queued)
            from .action_save_scope import scope as action_scope
            write_scope = action_scope(self, action, args, previous_session=previous_scope[0])
            if write_scope is None:
                self._publish_full(reason='Command reconciliation: ' + action)
            elif defer_publish:
                self._publish_smart_tool_update(defer_publish=True)
            else:
                session_ids, global_keys = write_scope
                if action == 'conversation.send' and update_message:
                    global_keys = set(global_keys) | {'updates'}
                self._publish_changes(sessions=session_ids, globals=global_keys)
            result = {**receipt, **({'state': self.browser_state()} if include_state else {})}
        for fn, values in pending:
            if action == 'workspace.setup.reconcile':
                # Local Git inspection runs off-loop and outside self.lock.
                # Return its final revision; queuing alone lets Retry race the
                # later subscription projection and submit the obsolete one.
                inspection_error = None
                try:
                    setup = await fn(*values)
                    receipt['result'] = {**setup, 'inspectionStatus': 'complete'}
                except Exception as exc:
                    inspection_error = str(exc) or 'Retained workspace inspection failed.'
                    receipt.update(accepted=False, error=inspection_error, status=409)
                    receipt['result'] = {**diagnostic_result, 'inspectionStatus': 'failed'}
                async with self.lock:
                    receipt['revision'] = self.state['revision']
                    if command_id:
                        self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), command_id))
                        self.db.commit()
                    result = {**receipt, **({'state': self.browser_state()} if include_state else {})}
                if inspection_error:
                    raise AppError(inspection_error, 409)
            elif action == "conversation.send" and fn == self._send or action == "message.edit" and fn == self._edit_current or action in {"question.answer", "worker.message", "conversation.delivery", "conversation.retry"}:
                # Runtime progress callbacks acquire self.lock. Admission must
                # run outside it, and the HTTP receipt waits for the actual ack.
                try:
                    acknowledged = await fn(*values)
                except Exception as exc:
                    if action == "worker.message" and command_id:
                        async with self.lock:
                            result["delivery"] = "unknown"
                            saved = {key: value for key, value in result.items() if key != "state"}
                            self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(saved), command_id))
                            self.db.commit()
                    if action == 'conversation.retry' and command_id:
                        async with self.lock:
                            receipt['result'] = {'delivery': 'unknown', 'message': 'Delivery could not be confirmed. Check delivery before trying again.'}
                            if isinstance(exc, AppError) and exc.code == 'worker_startup_failed':
                                receipt.update(accepted=False, status=exc.status, code=exc.code,
                                               error=str(exc), receipt=exc.receipt)
                                receipt['result'] = {'delivery': exc.receipt['delivery'], 'message': str(exc)}
                            self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), command_id))
                            self.db.commit()
                    raise
                if action == 'conversation.send' and isinstance(acknowledged, dict) and (acknowledged.get('steering') or acknowledged.get('delivery') == 'queued_for_update'):
                    result.update(acknowledged)
                if action in {'conversation.delivery', 'conversation.retry'}:
                    result['result'] = acknowledged
                    async with self.lock:
                        if command_id:
                            receipt['result'] = acknowledged
                            self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), command_id))
                            self.db.commit()
                if action == "worker.message":
                    result["result"] = acknowledged
                    result["delivery"] = "accepted"
                    if command_id:
                        async with self.lock:
                            saved = {key: value for key, value in result.items() if key != "state"}
                            self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(saved), command_id))
                            self.db.commit()
            else:
                kwargs = {'defer_publish': True} if defer_publish else {}
                task = self._task(self._guard(fn, values, kwargs))
                if fn == self._provision_workspace:
                    self.workspace_setup_tasks.add(task)
                    task.add_done_callback(self.workspace_setup_tasks.discard)
                if action.startswith("smartTools."):
                    self.smart_tool_tasks.add(task)
                    task.add_done_callback(self.smart_tool_tasks.discard)
                    self.smart_tool_requests[command_id] = task
                    task.add_done_callback(lambda finished, identity=command_id: self.smart_tool_requests.pop(identity, None))
        if action == 'conversation.send':
            if not result.get('steering') and result.get('delivery') != 'queued_for_update':
                result['delivery'] = 'accepted'
            if result.get('delivery') == 'accepted':
                saved = self.db.execute('SELECT receipt FROM commands WHERE id=?', (result['inputId'],)).fetchone()
                # Capture before returning to the HTTP task. Its later resumption
                # may follow a publication that this client never subscribed to.
                result['_deliveryStateRevision'] = (json.loads(saved[0]).get('_deliveryStateRevision') if saved else None) or self._state['revision']
        if action == 'question.answer':result['result'] = self.questions.read('question.read', args)
        return {**result, **({'state': self.browser_state()} if include_state else {})}

    async def _provision_workspace(self, identity, reconcile=False):
        """Provisioning or awaited inspection updates facts, never selection/drafts."""
        from . import workspace_provisioning
        try:
            if reconcile:
                setup = await asyncio.to_thread(workspace_provisioning.reconcile, self.data_dir, identity)
            else:
                loop = asyncio.get_running_loop()
                def progress(value):
                    if self.closed:
                        return False
                    future = asyncio.run_coroutine_threadsafe(self._setup_progress(value), loop)
                    try:
                        future.result(timeout=30)
                    except TimeoutError:
                        future.cancel()
                        return False
                    return not self.closed
                setup = await asyncio.to_thread(workspace_provisioning.run, self.data_dir, identity, progress)
        except Exception:
            if reconcile:
                raise  # An acknowledged inspection must report its actual outcome.
            setup = workspace_provisioning.recover(self.data_dir, identity)
        await self._setup_progress(setup)
        return setup

    async def _setup_progress(self, setup):
        async with self.lock:
            scaffold_changed = False
            for workspace in self.state['workspaces']:
                if workspace.get('setupId') == setup['id']:
                    scaffold_changed = scaffold_changed or (setup.get('scaffolded') and not workspace.get('setup', {}).get('scaffolded'))
                    workspace['setup'] = setup
            if scaffold_changed:
                self.state['configurationRevision'] = self.state.get('configurationRevision', 0) + 1
                for key in list(self.state.get('draftDefaults', {})):
                    if json.loads(key)[0] == setup['path']:
                        self.state['draftDefaults'].pop(key, None)
            self._publish_changes(globals={'workspaces', 'configurationRevision', 'draftDefaults'})

    async def wait_smart_tool(self, identity, timeout=300):
        """Wait for the original admitted operation; disconnect never replays it."""
        task = self.smart_tool_requests.get(identity)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except TimeoutError:
                pass
        return self.smart_tools.operation(identity) or {'id': identity, 'status': 'pending'}

    async def _regenerate_name(self, source, base):
        from .naming import accept_generated, directory_for, refresh
        identity = source['id']
        active = True
        async def emit(kind, payload):
            # Naming preparation cannot turn a saved error into a new failed or
            # active conversation. Actual naming telemetry remains observable.
            if active and kind in {'runtime.status', 'runtime.error', 'session.naming'}:
                return
            await self.on_runtime_event(kind, payload)
        try:
            await self.runtime.start(source, emit, preserve_emit=True)
            candidate = await self.runtime.control(identity, 'session.naming', {})
            async with self.lock:
                session = self._session(identity)
                # Preparation can be slow. Protect edits made after the click,
                # including edits before the naming model took its snapshot.
                same_base = all(base.get(key, 0) == candidate.get(key, 0)
                                for key in ('name_revision', 'name_policy_revision'))
                accepted = same_base and accept_generated(directory_for(self.data_dir, session), candidate, explicit=True)[1]
                refresh(self.data_dir, session)
                session['naming'] = {'status': 'ready' if accepted else 'conflict',
                    **({} if accepted else {'error': 'The name or Auto preference changed. Your newer choice was kept.'})}
                self._publish_changes(sessions={identity})
        except Exception as exc:
            async with self.lock:
                self._session(identity)['naming'] = {'status': 'error', 'error': str(exc)}
                self._publish_changes(sessions={identity})
        finally:
            active = False

    async def _guard(self, fn, args, kwargs=None):
        try:
            await fn(*args, **(kwargs or {}))
        except Exception as exc:
            from .runtime import RuntimeOperationPending, RuntimeHostClosingError
            if isinstance(exc, RuntimeOperationPending) and exc.operation == 'send' and fn == self._send:
                # _send already published this input's uncertain delivery. A
                # missing acknowledgement does not terminate its live turn.
                return
            if (kwargs or {}).get('defer_publish'):
                # A failure outside the manager's effect/result handler must not
                # leave an accepted interactive call polling a queued receipt.
                async with self.lock:
                    operation = self.smart_tools.operation(args[2])
                    if operation and operation.get('status') in {'queued', 'running'}:
                        operation.update(status='interrupted', updatedAt=time.time(),
                                         error='No verified tool outcome was recorded. Work was not replayed.')
                        self.smart_tools.persist_operation(operation)
                        self._publish_smart_tool_update(defer_publish=True)
            if isinstance(exc, RuntimeHostClosingError):
                return  # Expected host retirement is not a failed conversation.
            sid = args[0].get("id") if args and isinstance(args[0], dict) else args[0] if args else None
            if isinstance(exc, AppError) and exc.code == 'session_busy':
                return  # The rejected admission already published its ownership state.
            from .runtime import SessionInUseError
            if isinstance(exc, SessionInUseError):
                await self.on_runtime_event('runtime.ownership', {'sessionId': sid, 'status': 'blocked', 'owner': exc.owner})
                return
            from .worker_diagnostics import diagnostic_reference
            await self.on_runtime_event("runtime.error", {"sessionId": sid, "error": str(exc), **diagnostic_reference(exc)})

    async def history_page(self, session_id, before, limit):
        await self.history.load(session_id, before=before, limit=limit, canonical=True)

    async def _edit_current(self, source):
        edit = source['historyEdit']
        from .history_revision import apply_revision, receipt_path
        from .runtime import SessionInUseError
        try:
            from .session_store import capture_edit_context
            source['historyEditContextDigest'] = await asyncio.to_thread(capture_edit_context, self.data_dir, source)
            await self.runtime.start(source, self.on_runtime_event)
            result = await self.runtime.control(source['id'], 'history.edit', {
                'source':source,'messageId':edit['messageId'],'operationId':edit['operationId'],
                'text':edit['text'],'reply_context':edit.get('replyTo'),'attachments':edit['attachments']})
            async with self.lock:
                current = self._session(source['id'])
                apply_revision(self, current, result)
                current['configurationBusy'] = False
                if (self.state['view'].get('messageEdit') or {}).get('messageId') == edit['messageId']:
                    self.state['view']['messageEdit'] = None
                self._publish_changes(sessions={source['id']}, globals={'view', 'canvasArtifacts', 'canvas'})
        except Exception as exc:
            async with self.lock:
                current = self._session(source['id'])
                path = receipt_path(self.data_dir, source.get('runtimeSessionId') or source['id'], edit['operationId'])
                try:
                    if path.exists():
                        saved = json.loads(path.read_text())
                        if saved.get('phase') == 'committed':
                            apply_revision(self, current, saved['result'], interrupted=True)
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # Keep interrupted evidence; never guess or replay.
                current['configurationBusy'] = False
                current['historyEdit'].update(phase='error', error=str(exc))
                if isinstance(exc, SessionInUseError):
                    from .session_ownership import blocked
                    blocked(current, exc.owner)
                elif current['historyEdit'].get('applied'):
                    current['status'] = 'error'
                    current['error'] = 'The edit was saved but its response was not confirmed. No work was automatically replayed.'
                receipt = {'accepted':False,'error':str(exc),'status':409,'code':'session_busy' if isinstance(exc,SessionInUseError) else 'edit_failed'}
                self.db.execute('UPDATE commands SET receipt=? WHERE id=?',(json.dumps(receipt),edit['operationId']))
                self._publish_changes(sessions={source['id']}, globals={'view', 'canvasArtifacts', 'canvas'})
            raise AppError(str(exc),409,code=receipt['code']) from exc

    async def _check_delivery(self, sid, input_id):
        from .message_delivery import find_message
        async with self.lock:
            session = copy.deepcopy(self._session(sid))
            question = self.questions.for_input(sid, input_id)
        message = find_message(session, input_id)
        if message is None and question is not None:
            # The answer record survives a cold/pruned message projection.
            # Its exact input binding permits a passive evidence probe, not send.
            message = {'delivery': question['delivery']}
        if message is None:
            return {'delivery': 'not_saved', 'message': 'This app has no saved copy. You can try sending the original message again with the same delivery identity.'}
        if message.get('steering'):
            # Admission is distinct from application. Checking must not route a
            # held correction through ordinary input or wake a retired worker.
            return {'delivery': message['delivery']['status'], 'steering': copy.deepcopy(message['steering']),
                    'message': 'Steering status: ' + message['steering']['disposition'] + '. This correction is bound to its original run and was not sent again.'}
        status = message.get('delivery', {}).get('status', 'unknown')
        if status in {'queued_for_update', 'held'}:
            return {'delivery': status, 'message': message['delivery'].get('reason') or 'Saved for delivery after the app update. Nothing has been sent yet.'}
        if status != 'accepted' and self.runtime and hasattr(self.runtime, 'delivery'):
            try:
                evidence = await self.runtime.delivery(session, input_id)
                if evidence == 'accepted':
                    status = 'accepted'
            except Exception:
                pass  # A failed probe cannot prove that the input was not delivered.
        async with self.lock:
            current = self._session(sid)
            current_message = find_message(current, input_id)
            current_question = self.questions.for_input(sid, input_id)
            if ((current_message and current_message.get('delivery', {}).get('status') == 'accepted') or
                    (current_question and current_question['delivery']['status'] == 'accepted')):
                status = 'accepted'
            if status == 'accepted':
                self._delivery(current, input_id, status)
                self._publish_changes(sessions={sid})
        return {'delivery': status, 'message': {
            'accepted': 'Amplifier received this message. It was not sent again.',
            'sending': 'The original send is still in progress. Nothing was sent again.',
            'failed': 'The worker failed to start before this message was sent. Your saved message is available to retry.',
        }.get(status, 'Delivery is still uncertain. Checking does not resend it. Sending again may repeat work if the earlier attempt ran.')}

    async def _retry_message(self, session, text, input_id, original_status, original_turn, known_undelivered=False):
        result = await self._send(session, text, input_id, preserve_draft=True, retry=True, known_undelivered=known_undelivered)
        if isinstance(result, dict) and result.get('duplicate'):
            async with self.lock:
                current = self._session(session['id'])
                if current['status'] == 'working' and current.get('execution', {}).get('currentTurnId') == input_id:
                    current['status'] = original_status
                    self._activity(current, original_status, 'Delivery confirmed. Amplifier already received this message.')
                    turn = next(t for t in current['execution']['turns'] if t['id'] == input_id)
                    for key in ('phase', 'endedAt', 'retriedAt'):
                        if key in original_turn:
                            turn[key] = original_turn[key]
                        else:
                            turn.pop(key, None)
                    self._publish_changes(sessions={session['id']})
        return {'delivery': 'accepted', 'resent': not bool(isinstance(result, dict) and result.get('duplicate'))}

    def _delivery(self, session, input_id, status):
        # Question command IDs differ from their immutable answer input IDs.
        # Reconcile their canonical record even after its message left the view.
        if status == 'accepted' and (questions := getattr(self, 'questions', None)):
            questions.accept_delivery(session['id'], input_id)
        message = next((row for row in session['messages'] if row.get('inputId') == input_id and row.get('role') == 'user'), None)
        row = self.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone()
        receipt = json.loads(row[0]) if row else {}
        message_bound = message is not None and 'delivery' in message
        # Voice delegation has no separate user bubble. Only its saved exact
        # input/session binding permits a receipt update without that message.
        receipt_bound = receipt.get('inputId') == input_id and receipt.get('sessionId') == session['id']
        if not message_bound and not receipt_bound:
            return
        if status in {'unknown', 'failed'} and (receipt.get('delivery') == 'accepted' or
                message_bound and message['delivery'].get('status') == 'accepted'):
            return
        if status == 'unknown' and (receipt.get('delivery') == 'failed' or
                message_bound and message['delivery'].get('status') == 'failed'):
            return  # A racing timeout cannot erase an explicit pre-input rejection.
        if message_bound:
            if status == 'accepted':
                # Publish acceptance and consumption together: the browser may
                # retire its outbox before the awaited send RPC returns.
                self.clients.consume_attachments(session, message.get('attachments', []))
            message['delivery'] = {'status':status}
            if status in {'accepted', 'failed', 'unknown'}:
                self._post_disposition(session, message, 'accepted' if status == 'accepted' else 'retained')
                if message.get('navigationPost'):
                    receipt['navigationPost'] = copy.deepcopy(message['navigationPost'])
        if row:
            if status == 'accepted' and message_bound:
                # Acceptance/consumption and this receipt commit in the next
                # synchronous owned publication under the service lock. Keep
                # that exact observation for a lost-response replay, including
                # early acceptance while the send RPC is still outstanding.
                receipt.setdefault('_deliveryStateRevision', self._state['revision'] + 1)
            if status == 'accepted' and receipt.get('code') == 'worker_startup_failed':
                receipt['accepted'] = True
                for key in ('error', 'status', 'code', 'receipt'):
                    receipt.pop(key, None)
            self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps({**receipt, 'delivery':status}), input_id))
            if handoffs := getattr(self, "handoffs", None):
                handoffs.observe_delivery(input_id)

    def _post_disposition(self, session, message, disposition):
        from .chat_navigation import finish_human_post
        finish_human_post(session, message, disposition)
        if not message or not message.get('navigationPost'):
            return
        row = self.db.execute('SELECT receipt FROM commands WHERE id=?', (message['inputId'],)).fetchone()
        if row:
            receipt = json.loads(row[0])
            receipt['navigationPost'] = copy.deepcopy(message['navigationPost'])
            self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), message['inputId']))

    async def _send(self, session, text, input_id, previous_activity=None, preserve_draft=False, retry=False, known_undelivered=False):
        if not self.runtime:
            raise AppError("The Amplifier runtime is unavailable.")
        from .message_delivery import find_message
        from .update_drain import UpdateDeferred, held, defer_send
        original = find_message(session, input_id) or {}
        if not retry and held(self) and (not original.get('steering') or original['steering'].get('allowNextTurn')):
            from .update_drain import input_gate
            try:
                input_gate(self, session['id'], input_id)
            except UpdateDeferred:
                return await defer_send(self, session['id'], input_id, preserve_draft)
        if original.get('steering'):
            from .conversation_steering import send
            return await send(self, session, text, input_id, preserve_draft)
        previous_error_at = session.get('errorAt')
        from .runtime import RuntimeOperationPending, RuntimeStartupError, SessionInUseError, RuntimeHostClosingError
        try:
            session.setdefault('surfaceInputs', {}).setdefault(input_id, self.surface_context.bind_input(session['id']))
            sender = self.runtime.retry if retry else self.runtime.send
            admission = ({'admission_check': lambda: self.handoffs.input_guard(session['id'], input_id)}
                         if original.get('handoffOrigin') else {})
            send_result = await sender(session, text, input_id, self.on_runtime_event, **admission)
        except UpdateDeferred:
            return await defer_send(self, session['id'], input_id, preserve_draft)
        except RuntimeHostClosingError:
            async with self.lock:
                current = self._session(session['id'])
                self._delivery(current, input_id, 'unknown')
                interrupt_unfinished(current)
                if current['status'] in {'working', 'starting', 'ready', 'stopping'}:
                    current['status'] = 'interrupted'
                self._publish_changes(sessions={session['id']}, globals={'view'})
            raise
        except RuntimeOperationPending:
            async with self.lock:
                self._delivery(self._session(session['id']), input_id, 'unknown')
                self._publish_changes(sessions={session['id']}, globals={'view'})
            # A missing acknowledgement is not a failed turn. Keep the saved
            # input and live work; the exact late receipt can reconcile delivery.
            raise
        except RuntimeStartupError as exc:
            # A failed retry says nothing about whether an earlier attempt ran.
            # Preserve proven non-delivery, but never erase prior uncertainty.
            delivery = 'unknown' if retry and not known_undelivered else 'failed'
            message = str(exc) + (' Earlier delivery is still uncertain; checking does not resend it.' if delivery == 'unknown' else ' Your message has been saved.')
            receipt = {'delivery': delivery, 'inputId': input_id, 'sessionId': session['id']}
            async with self.lock:
                current = self._session(session['id'])
                # The worker may already have published a specific startup
                # error or the location of its private stderr log. Preserve
                # that public detail, but never reuse an earlier attempt's
                # error or expose the exception chain / stderr itself.
                if current.get('error') and current.get('errorAt') != previous_error_at:
                    message += '\n\n' + current['error']
                self._delivery(current, input_id, delivery)
                if not retry:
                    post = (find_message(current, input_id) or {}).get('navigationPost')
                    saved = {'accepted': False, 'status': 503, 'code': 'worker_startup_failed',
                             'error': message, 'receipt': receipt, **receipt,
                             **({'navigationPost': copy.deepcopy(post)} if post else {})}
                    if original.get('handoffOrigin'):
                        saved.update(handoffOrigin=original['handoffOrigin'], actor=original['inputActor'])
                    self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(saved), input_id))
                self._publish_changes(sessions={session['id']}, globals={'view'})
            from .worker_diagnostics import diagnostic_reference
            await self.on_runtime_event('runtime.error', {
                'sessionId': session['id'], 'error': message, 'errorType': 'RuntimeStartupError',
                'phase': 'worker_startup',
                **({'moduleFailures': exc.failures} if getattr(exc, 'failures', None) else {}),
                **diagnostic_reference(exc),
            })
            raise AppError(message, 503, code='worker_startup_failed', receipt=receipt) from exc
        except SessionInUseError as exc:
            if retry:
                async with self.lock:
                    current = self._session(session['id'])
                    self._delivery(current, input_id, 'unknown')
                    from .session_ownership import blocked
                    from .execution import finish
                    blocked(current, exc.owner)
                    finish(current, 'stopped')
                    self._activity(current, 'stopped', 'Resend was not admitted. This conversation is owned elsewhere.')
                    self._publish_changes(sessions={session['id']}, globals={'view'})
                raise AppError('This conversation is owned elsewhere. Continue here before resending.', 409, code='session_busy') from exc
            async with self.lock:
                current = self._session(session["id"])
                rejected = find_message(current, input_id)
                if (rejected and rejected.get('navigationPost') and
                        rejected.get('delivery', {}).get('status') == 'accepted'):
                    # Exact positive input evidence wins a contradictory late
                    # transport refusal; it cannot erase an admitted human post.
                    self._clear_sent_draft(current, rejected, text, preserve_draft)
                    self._publish_changes(sessions={session['id']}, globals={'view'})
                    return {'accepted': True, 'duplicate': True}
                self._post_disposition(current, rejected, 'rejected')
                current["messages"] = [
                    row for row in current["messages"] if row.get("inputId") != input_id
                ]
                if not (rejected or {}).get('navigationPost') and previous_activity is not None and current.get('recentActivityAt') == session.get('recentActivityAt'):
                    current['recentActivityAt'] = previous_activity
                    current.pop('navigationActivityPending', None)
                execution = current.get("execution", {})
                execution["turns"] = [
                    row for row in execution.get("turns", []) if row.get("id") != input_id
                ]
                if execution.get("currentTurnId") == input_id:
                    execution["currentTurnId"] = (
                        execution["turns"][-1]["id"] if execution["turns"] else None
                    )
                from .session_ownership import blocked
                blocked(current, exc.owner)
                self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (
                    json.dumps({"accepted": False, "error": str(exc), "status": 409, "code": "session_busy",
                                'sessionId': current['id'], 'inputId': input_id,
                                **({'navigationPost': copy.deepcopy(rejected['navigationPost'])}
                                   if rejected and rejected.get('navigationPost') else {})}), input_id))
                self._publish_changes(sessions={session['id']}, globals={'view'})
            raise AppError(str(exc), 409, code="session_busy") from exc
        except Exception:
            async with self.lock:
                current = self._session(session['id'])
                self._delivery(current, input_id, 'unknown')
                needs_error = current['status'] != 'error'
                self._publish_changes(sessions={session['id']}, globals={'view'})
            if needs_error:
                await self.on_runtime_event('runtime.error', {
                    'sessionId': session['id'],
                    'error': 'Message delivery could not be confirmed. The conversation worker is unavailable. Work was not automatically replayed.',
                })
            raise
        with measure(self, 'input.delivery.publish', session_id=session['id']):
            async with self.lock:
                current = self._session(session["id"])
                self._delivery(current, input_id, 'accepted')
                sent = next((row for row in session["messages"] if row.get("inputId") == input_id), {})
                self._clear_sent_draft(current, sent, text, preserve_draft)
                current.pop("lockOwner", None)
                # A bound client's draft/reply/attachments are saved by clients.save,
                # not the shared legacy view. Delivery changes only this owner unless
                # the existing save guard finds changed status/navigation/attention.
                self._publish_changes(sessions={session['id']}, detail_only=True,
                                      globals={'view'} if self.clients.record() is None else ())
        return send_result

    def _clear_sent_draft(self, current, sent, text, preserve_draft):
        attached = {row['id'] for row in sent.get('attachments', [])}
        draft_attachments = self.clients.attachments(current)
        draft_attachments[:] = [row for row in draft_attachments if row['id'] not in attached]
        client = self.clients.record()
        if client is not None:
            quote = sent.get('replyTo')
            if quote and quote.get('id') and client.get('messageReplies', {}).get(current['id'], {}).get('id') == quote['id']:
                client['messageReplies'].pop(current['id'], None)
                if client.get('selectedSessionId') == current['id']:
                    client['view']['messageReply'] = None
            if not preserve_draft and client.get('drafts', {}).get(current['id'], '').strip() == text.strip():
                self.clients.draft(current['id'], '')
        elif (not preserve_draft and self.state['selectedSessionId'] == current['id']
                and self.state['view'].get('draft', '').strip() == text.strip()):
            self.state['view']['draft'] = ''
        if not preserve_draft and current.get('draft', '').strip() == text.strip():
            current['draft'] = ''

    async def _end_call(self, call):
        if getattr(self.voice_service, 'call', None) is not call:
            return
        try:
            await self.voice_service.end(call.id, attempt_id=getattr(call, 'attempt_id', None), expected_call=call)
        except Exception as exc:
            if getattr(self.voice_service, 'call', None) is call:
                await self.set_voice_status({"status": "error", "id": call.id, "error": str(exc)})

    async def _stop(self, sid):
        # The Stop revision is already durable. Release invalidated inbox slots
        # even while the actual worker interruption is slow or unavailable.
        await self.collaboration.drain(sid)
        if self.runtime:
            await self.runtime.stop(sid)
        await self.on_runtime_event("runtime.status", {"sessionId": sid, "status": "stopped"})

    async def record_voice_usage(self,session_id,call_id,response_id,model,usage,phase='completed', *, transfer_id=None):
        from .voice_usage import normalize_voice_usage
        async with self.lock:
            try:session=self._session(session_id)
            except AppError:return
            self._check_voice_owner(session['id'], transfer_id)
            identity='voice:'+call_id+':'+response_id
            tree=session.get('execution',{})
            existing=next((n for n in tree.get('nodes',[]) if n['id']==identity),None)
            # Live duration updates replace one session receipt. Completion is
            # authoritative; a delayed running update cannot reopen it.
            if existing and existing.get('phase') == 'completed' and phase != 'completed':
                return
            normalized=normalize_voice_usage(usage)
            if response_id != 'session' and phase == 'incomplete' and existing:
                # A lost response.done can still leave observed token/cost
                # evidence. Missing fields are not a report of zero usage.
                previous_usage=existing.get('usage', {})
                supplied={key:value for key,value in normalized.items()
                          if key != 'costType' or 'costUsd' in normalized}
                normalized={**previous_usage,**supplied}
                normalized.setdefault('costType','unavailable')
            if response_id == 'session':
                final_duration_reported=phase == 'completed' and 'voiceSeconds' in normalized
                previous_usage=(existing or {}).get('usage', {})
                if 'voiceSeconds' not in normalized and 'voiceSeconds' in previous_usage:
                    normalized['voiceSeconds']=previous_usage['voiceSeconds']
                if phase != 'completed' and 'voiceSeconds' in previous_usage and 'voiceSeconds' in normalized:
                    normalized['voiceSeconds']=max(previous_usage['voiceSeconds'], normalized['voiceSeconds'])
                normalized['usageFinalized']=final_duration_reported
                normalized['usageStatus']='final' if final_duration_reported else 'running' if phase == 'running' else 'incomplete'
            if response_id != 'session' and (existing is None or existing.get('phase') != phase):
                from .chat_navigation import touch_independent_activity
                touch_independent_activity(session)
            turn_id=('voice:'+call_id) if response_id=='session' else existing.get('turnId') if existing else tree.get('currentTurnId')
            if not turn_id:turn_id='voice:'+call_id
            if not any(t['id']==turn_id for t in tree.get('turns',[])):
                previous=tree.get('currentTurnId')
                ensure_turn(session,turn_id,'Voice call · session usage' if response_id=='session' else 'Voice call')
                session['execution']['currentTurnId']=previous
            if turn_id == 'voice:'+call_id:
                for turn in session['execution']['turns']:
                    if turn['id'] == turn_id:
                        turn['lifecycle'] = 'voice'
            ingest_execution(session,{'id':identity,'turnId':turn_id,'sessionId':session_id,'rootSessionId':session_id,
                'kind':'llm','phase':phase,'label':model+' · voice','provider':'OpenAI voice','model':model,'lifecycle':'voice',
                'startedAt':existing.get('startedAt',time.time()) if existing else time.time(),
                'usage':normalized,
                **({'endedAt':time.time()} if phase in {'completed','incomplete'} else {})})
            if phase in {'completed','incomplete'} and session.get('status') not in {'working', 'starting', 'running', 'stopping'}:
                from .chat_navigation import settle_activity
                settle_activity(session)
            if phase in {'completed','incomplete'} and turn_id == 'voice:'+call_id:
                for turn in session['execution']['turns']:
                    if turn['id']==turn_id:turn.update(phase=phase,endedAt=time.time())
            self._publish_changes(sessions={session['id']})

    async def _takeover(self, session):
        from .runtime import SessionInUseError
        try:
            await self.runtime.takeover(session, self.on_runtime_event, session.get('lockOwner'))
            await self.history.load(session['id'], canonical=True)
            async with self.lock:
                current = self._session(session['id'])
                current.pop('lockOwner', None)
                current.pop('error', None)
                current['ownership'] = {'status': 'available'}
                current['status'] = 'ready'
                self._publish_changes(sessions={session['id']})
        except Exception as exc:
            async with self.lock:
                current = self._session(session['id'])
                if isinstance(exc, SessionInUseError):
                    from .session_ownership import blocked
                    blocked(current, exc.owner, detail=str(exc))
                else:
                    # Failure does not establish ownership. Keep the shared
                    # read-only gate until an explicit retry succeeds.
                    current['ownership'] = {'status': 'blocked', 'reason': 'takeover-failed', 'detail': str(exc)}
                    current.update(status='error', error=str(exc))
                self._publish_changes(sessions={session['id']})

    def _ingest_runtime_execution(self, session, payload):
        with self.performance_diagnostics.measure('execution.ingest', synchronous=True, session_id=session['id']):
            ingest_execution(session,payload)
        if getattr(self, 'status_emitter', None):
            self.status_emitter.execution(session, payload)
        if payload.get('failure') and payload.get('sessionId') in {session['id'], session.get('runtimeSessionId')} and payload.get('lifecycle') != 'background' and payload.get('purpose') != 'context_compaction':
            # Auxiliary preparation failures stay on their call. The
            # manager's terminal generation.failed event owns whether
            # this turn stopped; a successful recovery is not an app fault.
            session['failure'] = {**payload['failure'], 'inputId': payload.get('turnId'), 'recordedAt': payload.get('endedAt')}
            session.pop('health', None)

    def _apply_runtime_receipt(self, session, kind, payload):
        """Shared synchronous reducer for eligible single and batched receipts."""
        if kind == 'runtime.timing':
            self.performance_diagnostics.record_runtime_timing(payload.get('sessionId'), payload.get('timing'))
            return set()
        if kind == 'runtime.bridge':
            # Observation, not conversation state. Its existing diagnostic
            # owner retains the facts without a session save or publication.
            self.diagnostics.runtime_event(kind, payload, session)
            return set()
        self.event_log_view.invalidate({session['id']}, notify=False)
        self.diagnostics.runtime_event(kind, payload, session)
        from .chat_navigation import runtime_activity
        runtime_activity(session, kind, payload)
        if kind == 'execution.event':
            self._ingest_runtime_execution(session, payload)
        elif kind == 'runtime.status':
            # Eligibility requires the exact normalized runtime.activity shape:
            # no report, delivery, lifecycle, runtime identity or settings data.
            if session.get('status') not in {'working', 'starting'}:
                return None
            session['status'] = 'working'
            activity = self._activity(session, payload['phase'], payload.get('detail') or 'Waiting for the model response…')
            if activity['activeTools'] and not payload.get('detail'):
                activity.update({'phase': 'tools', 'label': 'Running tools'})
            session['progress'] = {key: payload[key] for key in ('phase', 'detail', 'elapsedSeconds') if key in payload}
        changed = self.collaboration.observe(session, kind, payload)
        from .conversation_steering import observe as observe_steering
        observe_steering(self, session, kind, payload)
        return {session['id']} | changed

    async def on_runtime_events(self, events):
        """Apply ordered receipts/observations, then durably publish their union."""
        from .runtime_event_batches import MAX_EVENTS, receipt, terminal
        if (not events or len(events) > MAX_EVENTS or not terminal(*events[0])
                or not all(receipt(*event) for event in events)):
            raise ValueError('Invalid terminal event batch')
        roots = {payload.get('rootSessionId') or payload.get('sessionId') for _, payload in events}
        if len(roots) != 1 or not next(iter(roots)):
            raise ValueError('Terminal event batch must belong to one root')
        async with self.lock:
            try:
                session = self._session(next(iter(roots)))
            except AppError:
                return
            changed = {session['id']}
            detail_only = True
            try:
                for kind, payload in events:
                    observed = self._apply_runtime_receipt(session, kind, payload)
                    if observed is not None:
                        changed.update(observed)
                        detail_only &= kind in {'execution.event', 'runtime.timing', 'runtime.bridge'}
            except Exception:
                # Preserve the applied prefix and unknown partial observer
                # effects before stopping. Neither prefix nor tail is replayed.
                self._publish_full(reason='Terminal receipt observer failed; preserve unknown partial effects')
                raise
            self._publish(session_ids=changed, record_only=True, detail_only=detail_only)

    async def on_runtime_event(self, kind, payload):
        if kind == 'runtime.timing':
            # Disposable diagnostics must not hydrate a session, acquire its
            # state lock, dirty progress or trigger another durable publication.
            self.performance_diagnostics.record_runtime_timing(payload.get('sessionId'), payload.get('timing'))
            return
        if kind == 'runtime.bridge':
            try:
                session = self._session(payload.get('rootSessionId') or payload.get('sessionId'), hydrate=False)
            except AppError:
                return
            self.diagnostics.runtime_event(kind, payload, session)
            return
        if kind == 'image.generated':
            from .image_results import attach_completed
            await attach_completed(self, payload)
            return
        async with self.lock:
            try:
                session = self._session(payload.get("rootSessionId") or payload.get("sessionId"))
            except AppError:
                return
            from .runtime_event_batches import receipt, terminal
            if receipt(kind, payload):
                changed = self._apply_runtime_receipt(session, kind, payload)
                if changed is not None:
                    if terminal(kind, payload):
                        self._publish(session_ids=changed, record_only=True, detail_only=True)
                    else:
                        self._publish_progress(session_ids=changed, record_only=True)
                return
            if kind == 'worker.updated' and payload.get('activityOnly'):
                worker = next((w for w in session.get('workers', []) if w['id'] == payload.get('id')), None)
                # Progress is not lifecycle authority. Delayed hooks cannot
                # create workers, reopen settled/unknown work, or change runs.
                if not worker or worker.get('status') not in {'starting', 'running', 'working', 'stopping'}:
                    return
                if not payload.get('runId') or payload['runId'] != worker.get('runId'):
                    return
                payload = {**{key: payload[key] for key in (
                    'sessionId', 'id', 'phase', 'detail', 'updatedAt', 'retryAttempt', 'retryMax') if key in payload},
                    'status': worker['status'], 'activityOnly': True}
            self.event_log_view.invalidate({session['id']}, notify=False)
            self.diagnostics.runtime_event(kind,payload,session)
            from .chat_navigation import runtime_activity
            runtime_activity(session, kind, payload)
            if kind == 'history.revised':
                from .history_revision import apply_revision
                apply_revision(self, session, payload)
            elif kind == 'session.naming':
                from .naming import automatic,persist
                name=payload.get('name');description=payload.get('description')
                if automatic(session) and isinstance(name,str) and name.strip():
                    session.update(title=name.strip()[:200],titleSource='generated')
                if isinstance(description,str) and description.strip():session['description']=description.strip()[:1000]
                persist(self.data_dir,session,expected_revision=payload.get('nameRevision'))
            elif kind == 'session.naming.progress':
                from .naming import legacy
                from .host.storage import SessionStore
                directory=SessionStore.for_app(self.data_dir,session['workspace']).directory(session.get('runtimeSessionId') or session['id'])
                directory.mkdir(parents=True,exist_ok=True,mode=0o700)
                data=legacy(directory);data['naming_completed_inputs']=payload.get('completedInputs',[])
                SessionStore._atomic(directory/'naming.json',json.dumps(data))
            elif kind == "execution.event":
                self._ingest_runtime_execution(session, payload)
            elif kind == 'runtime.delivery' and 'peerAdmission' not in payload:
                self._delivery(session, payload['inputId'], 'failed' if payload.get('delivery') == 'failed' else 'accepted')
            elif kind == "runtime.ended":
                self.operations.interrupted(session["id"])
                self.schedules.runtime_ended(session)
                # A turn may finish before naming does; only the runtime host
                # can confirm that no independent call can still be running.
                if payload.get("sessionId") in {session["id"], session.get("runtimeSessionId")} and not payload.get("backgroundOnly"):
                    settle_stream(session)
                    interrupt_unfinished(session)
                    from .execution import interrupt_owned_workers
                    interrupt_owned_workers(session, payload.get('runtimeOwnerId'))
                    if payload.get('reason') == 'shutdown' and session['status'] in {'working', 'starting', 'ready', 'stopping'}:
                        session['status'] = 'interrupted'
                finish_background(session,payload.get("backgroundCallIds",[]),payload.get("status","interrupted"))
            elif kind == 'runtime.ownership':
                if payload.get('status') == 'blocked':
                    from .session_ownership import blocked
                    blocked(session, payload.get('owner') or {}, keep_pending=True)
                else:
                    session['ownership'] = {key: payload[key] for key in ('status', 'source', 'detail') if key in payload}
                    session['status'] = 'read-only'
                    session.pop('lockOwner', None)
                if payload.get('status') == 'yielded':
                    settle_stream(session)
                    finish_execution(session, 'interrupted')
            elif kind == "runtime.warmth":
                session['preparation'] = {'status': payload['status']}
                if payload['status'] == 'warm' and session['status'] == 'ready':
                    session['status'] = 'idle'
                    # Parking a prepared worker releases the same idle boundary
                    # as session.idle. Only retained guarded input may run.
                    self._task(self.collaboration.drain(session["id"]))
            elif kind == "runtime.status":
                if payload.get('event') == 'input.delivered' and payload.get('inputId'):
                    self._delivery(session, payload['inputId'], 'accepted')
                # Provider requests/retries describe current work; only lifecycle
                # events or accepted input can start work. Late/background notices
                # must not lock a finished conversation's fork/edit controls.
                if payload.get('activityOnly') and session.get('status') not in {'working','starting'}:
                    return
                status = payload.get("status", "idle")
                # A worker can report its settled lifecycle after the manager has
                # recorded a terminal error. That report is not evidence that the
                # failed turn recovered, and must not relabel its execution as
                # completed.
                if status in {"idle", "stopped"} and session.get("status") == "error" and session.get("error"):
                    return
                session["status"] = status
                if status in {"starting", "ready"}:
                    session.pop("diagnosticReceipt", None)
                    if isinstance(session.get("health"), dict):
                        session["health"].pop("diagnosticReceipt", None)
                if session["status"] == "idle":
                    self.schedules.idle(session)
                    self.recall.personalization.idle(session)
                    self._task(self.collaboration.drain(session["id"]))
                # A successfully initialized session supersedes its old startup
                # failure. Idle/stopped alone do not prove recovery (providers
                # may report an error immediately before becoming idle).
                if session["status"] == "ready":
                    from .session_health import current_terminal_failure
                    retained_failure = current_terminal_failure(session)
                    if (session.get('failure', {}).get('category') != 'execution_interrupted'
                            and not retained_failure):
                        session.pop("error", None)
                        session.pop("failure", None)
                    if not retained_failure:
                        session.pop("errorType", None)
                        session.pop("turnErrorType", None)
                    session.pop("moduleFailures", None)
                    if isinstance(session.get("health"), dict):
                        session["health"].pop("moduleFailures", None)
                    session['preparation'] = {'status': 'ready'}
                labels = {"starting": "Preparing your Amplifier session…", "working": "Waiting for the model response…", "ready": "Ready to work", "idle": "Ready", "stopped": "Stopped", "stopping": "Stopping work…"}
                activity = self._activity(session, payload.get("phase", session["status"]), payload.get("detail") or labels.get(session["status"], session["status"]))
                if session["status"] in {"idle", "stopped"}:
                    settle_stream(session)
                    activity["activeTools"] = []
                    finish_execution(session,"completed" if session["status"]=="idle" else "stopped")
                    if session.get("configurationPending") and session["status"]=="idle":self._task(self.refresh_configuration(session["id"]))
                elif activity["activeTools"] and not payload.get("detail"):
                    activity.update({"phase": "tools", "label": "Running tools"})
                if payload.get("phase") or payload.get("detail"):
                    session["progress"] = {key: payload[key] for key in ("phase", "detail", "elapsedSeconds") if key in payload}
                elif session["status"] != "starting":
                    session.pop("progress", None)
                if payload.get("report"):
                    from .conversation_models import accept_report
                    accept_report(self, session, payload)
                if payload.get("runtimeSessionId"):
                    session["runtimeSessionId"] = payload["runtimeSessionId"]
            elif kind == "runtime.error":
                if 'terminalFailure' in payload:
                    from .session_health import terminal_exception_failure
                    enriched = terminal_exception_failure(session, payload['terminalFailure'])
                    if enriched is None:
                        return
                    session['failure'] = enriched
                settle_stream(session)
                session["status"] = "error"
                finish_execution(session,"error")
                session["errorAt"] = time.time()
                from .worker_diagnostics import receipt_path
                diagnostic = receipt_path(payload.get("diagnosticReceipt"), home=self.data_dir)
                if diagnostic:
                    session["diagnosticReceipt"] = diagnostic.name
                detail = str(payload.get("error") or payload.get("message") or "Runtime failed")
                if payload.get('moduleFailures'):
                    from .module_failures import ConfiguredModuleError
                    failure=ConfiguredModuleError(payload['moduleFailures'])
                    session['moduleFailures']=failure.failures
                    detail=str(failure)
                startup = payload.get('errorType') == 'RuntimeStartupError' or payload.get('phase') == 'worker_startup'
                error_type = payload.get('errorType') or (None if startup else session.get('turnErrorType'))
                from .session_health import current_terminal_failure, failure_details
                projected = failure_details(detail, error_type)
                existing = session.get('failure')
                if startup and projected['category'] == 'unknown':
                    error_type = 'RuntimeStartupError'
                    projected = failure_details(detail, error_type)
                current_input = session.get('execution', {}).get('currentTurnId')
                failure_inputs = (existing.get('inputIds') or [existing.get('inputId')]) if isinstance(existing, dict) else []
                same_input = (current_terminal_failure(session) or not current_input
                    or not any(failure_inputs) or current_input in failure_inputs)
                if (not startup and same_input and isinstance(existing, dict)
                        and (existing.get('stage') or projected['category'] == 'unknown'
                             and existing.get('category') != 'unknown')):
                    # A later generic process-exit error must not replace the
                    # structured cause published at the manager boundary.
                    projected = existing
                    error_type = projected.get('errorType', error_type)
                    detail = projected['summary'] + ' ' + projected['guidance']
                    session['errorAt'] = existing.get('recordedAt', session['errorAt'])
                session['errorType'] = error_type
                session['error'] = ('This turn exceeded the model context limit. Your conversation and saved surfaces are kept. '
                    'Inspect the current state and continue with a smaller, focused request; completed actions were not replayed.'
                    if projected['category'] == 'context_limit' and not projected.get('stage') else detail)
                session['failure'] = {**projected, 'recordedAt': session['errorAt']}
                if projected['category']=='authentication' and self.management is not None:
                    # Recheck current connections outside the event lock. A late
                    # failure from an old worker must not label a newly signed-in account.
                    self.management.background(self.management.command('providers.list',{'sessionId':sid,'refresh':True}))
                session.pop('health', None)
                self._activity(session, "error", session["error"])["activeTools"] = []
            elif kind == "runtime.generation":
                scheduled_generation = self.schedules.generation(session, payload)
                event = {**payload, "at": time.time()}
                session.setdefault("generations", []).append(event)
                session["generations"] = session["generations"][-200:]
                from .response_completion import apply as apply_response_completion
                apply_response_completion(session, payload)
                root_generation = (payload.get('sessionId', session['id']) == session['id']
                    and payload.get('rootSessionId', session['id']) == session['id'])
                if root_generation and payload.get('event') == 'generation.started':
                    session.pop('modelAdmission', None)
                    session.pop('diagnosticReceipt', None)
                    session.pop('turnErrorType', None)
                    session.pop('errorType', None)
                    session.pop('error', None)
                    session.pop('failure', None)
                    session.pop('health', None)
                    session.pop('errorAt', None)
                elif root_generation and payload.get('event') == 'generation.failed':
                    from .execution import fail_generation
                    fail_generation(session, payload.get('input_ids', []))
                    session['turnErrorType'] = payload.get('error_type')
                    from .session_health import generation_failure
                    from .model_admission import failure as admission_failure
                    failure = admission_failure(session, payload) or generation_failure(payload)
                    existing = session.get('failure')
                    safety_categories = {'content_filter', 'computer_capture_stop'}
                    explicit_change = (failure and failure.get('category') != 'unknown'
                        and isinstance(existing, dict) and failure['category'] != existing.get('category'))
                    # A terminal's explicit cause supersedes an earlier transient
                    # provider error. Never turn a terminal safety stop into a
                    # resumable rate-limit failure by binding the earlier cause.
                    # Conversely, a later non-safety wrapper cannot erase an
                    # already observed safety halt; retain its separate cause.
                    if explicit_change and existing.get('category') in safety_categories and failure['category'] not in safety_categories:
                        existing['terminalFailure'] = copy.deepcopy(failure)
                    if failure and (not isinstance(existing, dict) or existing.get('category') == 'unknown'
                            or explicit_change and (existing.get('category') not in safety_categories
                                                    or failure['category'] in safety_categories)):
                        session['failure'] = {**failure, 'recordedAt': event['at'],
                            'generationId': payload.get('generation_id'), 'inputIds': payload.get('input_ids', [])}
                    elif isinstance(existing, dict):
                        active = session.get('collaborationGeneration') or {}
                        # A classified provider cause precedes the terminal
                        # event. Bind it only to its observed admitted input
                        # and generation, never to an unrelated later failure.
                        if (active.get('id') == payload.get('generation_id') and not active.get('terminal')
                                and existing.get('generationId') in {None, active.get('id')}
                                and existing.get('inputId')
                                and existing['inputId'] in active.get('inputIds', [])
                                and existing['inputId'] in payload.get('input_ids', [])):
                            session['failure'] = {**existing, 'generationId': active['id'],
                                'inputIds': payload['input_ids']}
                if root_generation and payload.get("event") == "generation.finished":
                    if not scheduled_generation:
                        from .attention import completed
                        completed(session,event)
                    if self.management and not scheduled_generation:
                        self._task(self._notify_completion(copy.deepcopy(session),copy.deepcopy(payload)))
                    pending = payload.get("active_job_ids", [])
                    self._activity(session, "waiting-workers" if pending else "processing",
                        f"Waiting for {len(pending)} delegated tasks" if pending else "Response ready")
            elif kind == "assistant.message":
                from .assistant_messages import duplicate
                original = next((m for m in reversed(session["messages"]) if m.get("inputId") == payload.get("inputId") and m["role"] == "user"), {})
                generation_id = payload.get("generationId")
                matching_stream = not session.get('streamingInputId') or session['streamingInputId'] == payload.get('inputId')
                repeated = duplicate(session["messages"], payload)
                if not repeated:
                    self._message(session, "assistant", payload.get("text", ""), "observation" if payload.get("observation_id") else "schedule" if payload.get("scheduled_monitor_only") else original.get("via", "call" if str(payload.get("inputId", "")).startswith("voice:") else "chat"), inputId=payload.get("inputId"), generationId=generation_id, source="amplifier", **({"streamId": session["streamingId"]} if matching_stream and session.get("streamingId") else {}), **{key: payload[key] for key in ("runtimeMessage", "createdAt", "timestampKnown", "observation_id") if key in payload})
                if matching_stream and (not repeated or session.get("streaming") == payload.get("text")):
                    session.pop("streaming", None)
                    session.pop("streamingId", None)
                    session.pop("streamingInputId", None)
                # Transport may deliver completion before its saved message.
                terminal = next((event for event in reversed(session.get("generations", []))
                                 if event.get("generation_id") == generation_id
                                 and event.get("event") in {"generation.finished", "generation.failed", "generation.detached"}), None)
                if terminal:
                    from .response_completion import apply as apply_response_completion
                    apply_response_completion(session, terminal)
            elif kind == "assistant.delta":
                input_id = payload.get("inputId")
                if session.get("streamingInputId") and input_id and session["streamingInputId"] != input_id:
                    settle_stream(session)
                if input_id:
                    session["streamingInputId"] = input_id
                session.setdefault("streamingId", str(uuid.uuid4()))
                session["streaming"] = session.get("streaming", "") + payload.get("text", payload.get("delta", ""))
            elif kind == "worker.updated":
                self.schedules.worker(session, payload)
                worker = next((w for w in session["workers"] if w["id"] == payload.get("id")), None)
                if worker:
                    worker.update({key: value for key, value in payload.items() if key != 'activityOnly'})
                else:
                    worker = copy.deepcopy(payload)
                    task = self.coordination.task(session["id"])
                    worker["taskId"] = task.get("id")
                    session["workers"].append(worker)
                from .coordination import observe_worker
                observe_worker(worker, payload)
                self.operations.notify()
                retrying = payload.get("phase") == "retrying"
                activity = self._activity(session, "retrying" if retrying else "workers", "A worker is retrying a model request" if retrying else "Delegated work is reporting progress")
                activity["lastEvent"] = {"worker": payload.get("name", "Worker"), "status": payload.get("status"), "at": time.time()}
            elif kind == "approval.requested":
                session["approvals"].append({**payload, "status": "pending"})
                self._activity(session, "approval", "Waiting for your approval")
                if self.access_mode.full(session):
                    self._task(self.access_mode.settle(session["id"]))
            elif kind == "approval.resolved":
                for approval in session["approvals"]:
                    if approval["id"] == payload.get("id"):
                        approval["status"] = payload.get("decision", "expired")
            else:
                event = {"type": kind, **payload, "at": time.time()}
                if kind == "runtime.tool":
                    activity = self._activity(session, "tools", "Running tools")
                    tools = activity["activeTools"]
                    identity = payload.get("callId") or payload.get("tool")
                    if payload.get("phase") == "pre":
                        if not any((t.get("callId") or t.get("tool")) == identity for t in tools):
                            tools.append({"callId": payload.get("callId"), "tool": payload.get("tool", "tool"), "startedAt": event["at"]})
                        activity["label"] = "Delegating work" if payload.get("tool") == "delegate" else "Using " + str(payload.get("tool") or "a tool")
                    elif payload.get("phase") in {"post", "error"}:
                        activity["activeTools"] = [t for t in tools if (t.get("callId") or t.get("tool")) != identity]
                        if not activity["activeTools"]:
                            activity.update({"phase": "model", "label": "Waiting for the model response…"})
                    activity["lastEvent"] = {"tool": payload.get("tool"), "phase": payload.get("phase"), "at": event["at"]}
                session.setdefault("runtimeEvents", []).append(event)
                session["runtimeEvents"] = session["runtimeEvents"][-100:]
            if kind == 'runtime.nextMessageApplied':
                from .next_message_configuration import applied
                applied(session, payload)
            if kind == 'runtime.modelSelectionApplied' and session.get('pendingModelSelection') == payload.get('selection'):
                session.pop('pendingModelSelection', None)
            if (kind == 'approval.requested' or kind == 'runtime.error' or
                    kind == 'runtime.status' and not payload.get('activityOnly') and
                    session.get('status') in {'idle', 'stopped', 'interrupted'}):
                from .chat_navigation import settle_activity
                settle_activity(session)
            progress = kind in {'assistant.delta', 'runtime.tool'} or (
                kind == 'worker.updated' and payload.get('activityOnly')) or (
                kind == 'runtime.status' and (payload.get('activityOnly') or
                    payload.get('preparationProgress') and payload.get('status') == 'starting')) or (
                kind == 'execution.event' and payload.get('phase') in {'running', 'working', 'streaming'})
            collaboration_changed = self.collaboration.observe(session, kind, payload)
            if (kind == 'runtime.generation' and root_generation
                    and payload.get('event') == 'generation.failed'
                    and session.get('status') not in {'stopped', 'stopping'}):
                from .session_health import current_terminal_failure
                if current_terminal_failure(session):
                    # A confirmed failed root stays failed when its worker
                    # closes. Closure cannot erase the bound cause or make a
                    # later diagnostic supplement look like a user Stop.
                    failure = session['failure']
                    session.update(status='error', errorType=failure.get('errorType'),
                                   errorAt=failure['recordedAt'],
                                   error=failure['summary'] + ' ' + failure['guidance'])
            from .conversation_steering import observe as observe_steering
            observe_steering(self, session, kind, payload)
            if progress:
                self._publish_progress(session_ids={session['id']},
                                       detail_only=kind in {'assistant.delta', 'execution.event'}, record_only=True,
                                       stream=kind == 'assistant.delta')
            elif kind == 'execution.event':
                # Terminal call evidence must commit immediately, but changes
                # only this session. Reuse the catalog when the save guard
                # proves navigation/attention unchanged; widen when it changes.
                self._publish(session_ids={session['id']}, record_only=True, detail_only=True)
            else:
                # Lifecycle may update an originating schedule or a peer's
                # saved receipt reference on another chat.
                changed = self.schedules.sync()
                # Repeated working reports and generation-start details often
                # leave navigation/attention unchanged. The save guard proves
                # that before reusing shared projections. Terminal generation
                # and schedule/collaboration changes retain broad publication.
                detail_only = (kind == 'runtime.delivery' or
                               kind == 'runtime.status' and payload.get('status') == 'working' or
                               kind == 'runtime.generation' and payload.get('event') == 'generation.started')
                self._publish_changes(sessions={session['id']} | changed | collaboration_changed,
                                      detail_only=detail_only and not changed and not collaboration_changed)
            if (self.update_manager and (kind == 'runtime.warmth' and payload.get('status') in {'warm', 'cold'}
                    or kind == 'runtime.status' and payload.get('status') == 'stopped' and not payload.get('activityOnly'))):
                self.update_manager.notify_idle(settled_session_id=session['id'])


    def _unmounted_legacy_refresh(self, session):
        """Prove only that passive freshness has no owned mount to retire.

        The unbound settings operation remains unknown and fenced. Never send
        this case through runtime retirement, whose completion releases busy.
        Called synchronously under the service lock; unavailable evidence holds.
        """
        pending = session.get('pendingConfiguration', {})
        ownership = session.get('ownership', {})
        if not isinstance(ownership, dict) or ownership.get('status') not in {None, 'available'}:
            return False
        if (pending.get('phase') != 'error' or pending.get('recoveryRequired') is not True
                or pending.get('operationId') or session.get('configurationOperationFence')):
            return False
        if session.get('configurationRefresh', {}).get('phase') not in {None, 'pending'}:
            return False
        if any(row.get('phase') in {'pending', 'unknown'} for row in session.get('worktreeHandoffs', [])):
            return False
        if any(turn.get('phase') == 'running' for turn in session.get('execution', {}).get('turns', [])):
            return False
        if any(worker.get('status') in {'queued', 'starting', 'running', 'working', 'stopping'}
                or worker.get('persistent') and worker.get('status') == 'idle'
                for worker in session.get('workers', [])):
            return False
        workers = getattr(self.runtime, 'workers', None)
        if not isinstance(workers, dict) or session['id'] in workers:
            return False
        tasks = getattr(self.management, 'pending_config_tasks', None)
        lock = getattr(self.management, 'lock', None)
        observe = getattr(self.runtime, 'has_pending_operations', None)
        if not isinstance(tasks, dict) or not callable(getattr(lock, 'locked', None)) or not callable(observe):
            return False
        try:
            task = tasks.get(session['id'])
            return (not lock.locked() and (task is None or task.done() is True)
                    and observe() is False)
        except Exception:
            return False

    def _claim_configuration_refresh(self,session):
        """Reserve an idle runtime under the service lock before deferring it."""
        if (session.get('historyManaged') and not session.get('fileAccessPending')) or not session.get('configurationPending') or session['status'] not in {'idle','stopped','interrupted','error'}:return
        if session.get('configurationBusy') and not self._unmounted_legacy_refresh(session):return
        if session.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:return
        workers=getattr(self.runtime,'workers',None)
        if self.runtime is None or isinstance(workers,dict) and session['id'] not in workers:
            # There is no mounted configuration to retire. The next start reads
            # the committed settings; no busy cycle or per-chat save is needed.
            session['configurationPending']=False
            session.pop('fileAccessPending', None)
            session.pop('configurationRefresh',None)
            return
        if any(w.get('persistent') and w.get('status') in {'idle','running','starting'} for w in session.get('workers',[])):return
        session['configurationBusy']=True
        revision=session.get('configurationPendingRevision',0)
        session['configurationRefresh']={'phase':'applying','revision':revision}
        return session['id'],revision

    async def _refresh_claimed_configuration(self,identity,revision):
        succeeded=False
        try:
            if self.runtime:await self.runtime.stop(identity, reason='configuration')
            succeeded=True
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self.lock:
                session=self._session(identity)
                session['configurationRefresh']={'phase':'error','revision':revision,
                    'error':'Saved settings could not be applied to this idle conversation. Retry its configuration refresh.'}
        finally:
            async with self.lock:
                try:session=self._session(identity)
                except AppError:return
                session['configurationBusy']=False
                if succeeded and session.get('configurationPendingRevision',0)==revision:
                    session['configurationPending']=False
                    session.pop('fileAccessPending', None)
                    session.pop('configurationRefresh',None)
                elif session.get('configurationRefresh',{}).get('phase')!='error':
                    session['configurationRefresh']={'phase':'pending','revision':session.get('configurationPendingRevision',revision)}
                self._publish_progress(session_ids={identity}, record_only=True)
                if succeeded and session.get('configurationPending'):
                    self._task(self.refresh_configuration(identity))
                elif succeeded:
                    # Peer input held by the refresh reservation may now try
                    # its existing durable guards. A real Stop still suppresses it.
                    self._task(self.collaboration.drain(identity))

    async def refresh_configuration(self,identity):
        # Ordinary requests must keep their existing admission-lock order.
        # An extra no-op lock turn lets later maintenance overtake queued input.
        try:
            if not self._session(identity).get('configurationPending'):return
        except AppError:return
        async with self.lock:
            try:session=self._session(identity)
            except AppError:return
            previous=bool(session.get('configurationPending'))
            claimed=self._claim_configuration_refresh(session)
            if claimed or previous!=bool(session.get('configurationPending')):self._publish_progress(session_ids={identity}, record_only=True)
        if claimed:await self._refresh_claimed_configuration(*claimed)

    async def _notify_completion(self,session,generation):
        try:
            await self.management.notifications.send(session,generation)
        except Exception:
            async with self.lock:
                self.state['notificationError']='Notification delivery failed. Check notification settings.'
                self._publish_changes(globals={'notificationError'})

    def state_resource(self, identity):
        from .state_storage import resource
        return resource(self.db, identity)

    async def app_bridge(self, operation, args, session_id):
        from .collaboration import PRINCIPAL, BINDING
        token = PRINCIPAL.set(args.get('_runtimeSessionId'))
        binding_token = BINDING.set(args)
        try:
            return await self._bound_app_bridge(operation, args, session_id)
        except Exception as exc:
            if args.get('_handoffSnapshot') is not None:
                from .handoff_drain import failed_disposition
                try:
                    async with self.lock:
                        disposition = failed_disposition(self, session_id, operation, args)
                    if disposition is not None:
                        exc._handoff_disposition = disposition
                except Exception:
                    pass  # An unreadable receipt remains unknown, never absent.
            raise
        finally:
            PRINCIPAL.reset(token)
            BINDING.reset(binding_token)

    async def _bound_app_bridge(self, operation, args, session_id):
        # app_bridge retains the authenticated worker envelope in BINDING.
        # Operation schemas receive public args, not transport-only fields.
        # Remove only known fields: arbitrary extras must still be validated.
        transport = args
        args = {key: value for key, value in args.items()
                if key not in {'_generationId', '_runtimeSessionId', '_inputBindings', '_inputClients',
                               '_workOrigin', '_workDispatchGuarded', '_responseMode', '_childOrigin', '_runtimeOwnerId',
                               '_handoffSnapshot'}
                and (operation == 'capacity.admit' or key not in {'_handoffDrain', '_nativeChild'})}
        from .workspace_work_effects import APP_EFFECT, app_request
        token = APP_EFFECT.set(app_request(operation, args, session_id))
        try:
            return await self._client_app_bridge(operation, args, session_id, transport)
        finally:
            APP_EFFECT.reset(token)

    async def _client_app_bridge(self, operation, args, session_id, transport):
        if '_inputClients' not in transport:
            return await self._app_bridge(operation, args, session_id)
        # The worker stamps this from accepted inputs, replacing any tool args.
        # Do not inherit the browser context captured when the runtime started.
        clients = transport['_inputClients']
        identity = (clients[0] if isinstance(clients, list) and clients
                    and all(isinstance(value, str) and value and value == clients[0] for value in clients) else None)
        origin = {'clientId': identity, 'status': 'client' if identity else 'unavailable'}
        with self.clients.bind(identity if identity in self.clients.records else None):
            return await self._app_bridge(operation, args, session_id, input_origin=origin)

    async def _app_bridge(self, operation, args, session_id, *, input_origin=None):
        if operation == 'ui.use':
            return await self.ui_use.call(session_id, args.get('operation'), args.get('args', {}))
        if operation == 'context.prepare':
            from .preparation_context import OPERATIONS, SECTION_TIMEOUT
            async def read(name):
                try:
                    value = await asyncio.wait_for(self._app_bridge(
                        name, args if name == 'context.manifest' else {},
                        session_id, input_origin=input_origin), SECTION_TIMEOUT)
                    return name, {'available': True, 'value': value}
                except Exception:
                    # Partial coverage remains explicit; one unavailable source
                    # must not turn other current sections into empty context.
                    return name, {'available': False}
            return dict(await asyncio.gather(*(read(name) for name in OPERATIONS)))
        if operation == 'work.effect.check':
            from .workspace_work_effects import check
            from .performance_diagnostics import active_bridge, mark
            # Private worker transport only. This synchronous read is a Work
            # precondition, not a reusable permission or delayed-effect grant.
            timing = active_bridge()
            mark(timing, 'lock.start')
            async with self.lock:
                mark(timing, 'lock.end')
                return check(self.work_invocation, session_id, args)
        if operation == 'work.invocation.revalidate':
            if set(args) != {'version'} or not isinstance(args['version'], str) or len(args['version']) > 64:
                raise AppError('Revalidation requires only the observed invocation version.')
            required, _ = self.work_invocation.required(session_id)
            return {'current': required['status'] == 'available' and required['version'] == args['version'],
                    'required': required, 'effectAuthorized': False}
        if operation == 'configuration.effect.admit':
            from .configuration_recovery import authorize
            async with self.lock:
                return authorize(self.management, session_id, args)
        if operation == "coordination.continuation_ready":
            return self.collaboration.continuation_ready(session_id, args)
        if operation == "coordination.admit":
            return self.collaboration.admission(session_id, args)
        if operation == "coordination.current":
            from .workspace_awareness import packet
            from .collaboration import PRINCIPAL
            from .coordination_guidance import assignment_orientation
            session = self._session(session_id, hydrate=False)
            reference = self._session(session_id, hydrate=False).get("coordinationReference", {
                "sessionId": session_id, "grants": [], "requests": [], "bounded": True,
                "detail": "No retained collaboration reference; discover current coordination.context."})
            return {**reference, 'awareness': packet(self, session_id, principal=PRINCIPAL.get()),
                    'assignment': assignment_orientation(session, self.assignments.summary(session), PRINCIPAL.get()),
                    'work': self.work_invocation.current(session_id)}
        def visual_origin(explicit=None):
            if input_origin is None:
                return
            identity = input_origin['clientId']
            if identity is None or explicit not in (None, identity):
                raise AppError("Screen consent requires one originating browser; another client's consent cannot be borrowed.",
                               409, code='visual_owner')
            from .agent_canvas import target
            target(self, session_id, identity, required=True, connected_only=True)

        def agent_snapshot(client_id=None, **options):
            from .agent_canvas import state
            if input_origin is not None and client_id is None:
                client_id = input_origin['clientId']
                options.update(connected_only=True, allow_detached=True, detached=client_id is None)
            snapshot = state(self, session_id, client_id, **options)
            if input_origin is not None:
                origin_id = input_origin['clientId']
                live = origin_id in snapshot['canvasContext']['connectedClientIds']
                snapshot['inputOrigin'] = {**input_origin, 'status': 'client' if live else 'unavailable'}
                if not live or snapshot['canvasContext']['clientId'] != origin_id:
                    snapshot['computerVisual'] = {'available': False, 'captureMode': 'explicit-frame',
                                                 'reason': 'Screen consent requires the connected originating browser.'}
            return snapshot

        if operation == 'observation.admit':
            return self.observations.authorize_admission(session_id, args)
        if operation == 'memory.context':
            return await self.recall.personalization.context(session_id, expected=args.get('expected'))
        if operation == 'message.reactions.context':
            from .message_interactions import reaction_context
            # A previous publication failure leaves progress dirty. Do not
            # describe an unsaved in-memory annotation as committed context.
            async with self.lock:
                if getattr(self, '_progress_dirty', False):
                    self._commit_pending_progress()
                return reaction_context(self._session(session_id, hydrate=False))
        if operation == "questions.admit":
            async with self.lock:
                self._session(session_id)
                values = args.get("questionIds")
                if (not isinstance(values, list) or len(values) > 32
                        or any(not isinstance(q, str) or not 1 <= len(q) <= 200 for q in values)
                        or len(set(values)) != len(values)):
                    raise AppError("Invalid required question IDs")
                for question_id in values:
                    self.questions.answer_for_dependency(session_id, question_id)
                return {"admitted": True, "questionIds": values}
        if operation == 'integration.admit':
            from .integration_validation import admit
            return await admit(self, session_id, args.get('id'), args.get('command'))
        if operation == "operations.observe":
            return await self.operations.observe(session_id, args["runtimeSessionId"], args["event"])
        if operation == "computer.visual.read":
            visual_origin()
            if input_origin is not None:
                visual = self.computer_visual.clients.get(input_origin['clientId'])
                if visual is None or args.get('captureId') not in visual.receipts:
                    raise AppError("This capture belongs to another originating browser.", 409, code='visual_owner')
            return self.computer_visual.read(session_id, args.get("captureId"))
        if operation == "voice.visual.read":
            return self.voice_visual.read(session_id, args.get("captureId"))
        if operation == "outputs.image.read":
            return await self.outputs.image_async(session_id, args.get('id'), args.get('sha256'))
        if operation == "outputs.images.read":
            return await self.outputs.images_async(session_id, args.get('images'))
        if operation in {'context.manifest', 'context.read'}:
            bindings = args.get('_contextBindings', [])
            async with self.lock:
                if operation == 'context.manifest':
                    result = self.surface_context.manifest(session_id, bindings)
                    return {**result, 'inputIds': args.get('_contextInputs', [])}
                return self.surface_context.read(session_id, args, bindings)
        if operation == "capacity.admit":
            from .model_admission import admit
            return await admit(self, session_id, args)
        if operation == 'capacity.abandoned':
            from .capacity import abandoned
            return await abandoned(self, session_id, args)
        if operation == "history":
            from .history_query import query_history
            return await query_history(self, args, session_id)
        if operation in {"get_state", "state.get"}:
            await self._flush_pending_progress()
            from .agent_state import read_state
            return read_state(agent_snapshot(args.get('clientId')), args, session_id=session_id, resolve=self.state_resource, directory=self.directory)
        if operation in {"list_actions", "actions.list"}:
            from .agent_actions import read_actions
            return read_actions(self.get_actions(), args)
        if operation in {"dispatch", "action.dispatch"}:
            from .agent_state import read_state
            from .collaboration import BINDING
            receipt_mode = (args.get('responseMode') == 'receipt'
                            or (BINDING.get() or {}).get('_responseMode') == 'receipt')
            action_args=copy.deepcopy(args.get('args',{}))
            if args['action'] in {'workspace.environments.removal.read', 'workspace.environments.removal.list', 'assignment.brief'}:
                # Do not infer nativeProject, flush progress or build a snapshot:
                # even _session(hydrate=False) admits dormant owners. Each passive
                # action validates its own caller scope without runtime demand.
                return await self.dispatch(args['action'], action_args, origin='agent',
                    command_id=args.get('id'), include_state=False, caller_session_id=session_id)
            # Resolve native aliases before the existing caller-ownership checks;
            # aliases never authorize a different conversation.
            from .session_identity import resolve
            key = 'sessionId' if action_args.get('sessionId') else 'id' if args['action'] in SESSION_ID_ACTIONS else None
            if key and action_args.get(key):
                try:
                    row = self.directory.resolve(action_args[key], action_args.get('nativeProject'))
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if row:
                    action_args[key] = row['id']
            schedule_read = args['action'] in {'schedule.list', 'schedule.read', 'schedule.overview'}
            if schedule_read and args['action'] != 'schedule.overview':
                # The app instance is the authenticated user's library. Reads
                # may inspect another registered chat; mutations stay caller-owned.
                action_args.setdefault('sessionId', session_id)
                self._session(action_args['sessionId'], hydrate=False)
            if (args['action'].startswith(('question.', 'task.', 'schedule.', 'observation.', 'capacity.')) and not schedule_read) or (args['action'] == 'runtime.control' and action_args.get('operation', '').startswith(('task.', 'capacity.'))):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Task, question, and schedule actions must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'] in {'task.get', 'task.history', 'task.detail', 'task.receipt'} or (
                    args['action'] == 'runtime.control' and isinstance(action_args.get('operation'), str) and action_args['operation'] in
                    {'task.get', 'task.history', 'task.detail', 'task.receipt'}):
                # Caller ownership was checked above. Avoid the later
                # _session(nativeProject), pending-progress flush and full
                # agent snapshot, which would turn this into an active read.
                return await self.dispatch(args['action'], action_args, origin='agent',
                    command_id=args.get('id'), expected_revision=args.get('expectedRevision'),
                    include_state=False, caller_session_id=session_id)
            if args['action'].startswith('worktree.'):
                # The canonical worktree owner checks commissioned-manager scope.
                action_args.setdefault('sessionId', session_id)
            if args['action'].startswith('portability.') and args['action'] not in {'portability.stage', 'portability.activate', 'portability.discard'}:
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Worktree actions must target the calling task.', 409)
                action_args['sessionId'] = session_id
            if args['action'] == 'runtime.control' and str(action_args.get('operation', '')).startswith(('native.', 'context.recovery.')):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Native provider actions belong to the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'] == 'runtime.control' and action_args.get('operation') == 'tool.invoke':
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Tool invocation must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'] == 'bundle.default' and action_args.get('scope') == 'workspace':
                action_args.setdefault('workspace', self._session(session_id)['workspace'])
            if args['action'].startswith(('operations.', 'kernels.')):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Operation actions must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'].startswith(('recall.','memory.')):
                if action_args.get('sessionId',session_id) != session_id:
                    raise AppError('Recall actions must identify the calling conversation.',409)
                action_args['sessionId'] = session_id
            if args['action'] == 'desktop.readiness':
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Desktop readiness must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'].startswith(('voice.visual.', 'computer.visual.')):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Visual capture must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'] in {'canvas.openFile', 'canvas.openAttachment'}:
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('File navigation must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'] in MESSAGE_INTERACTIONS or args['action'] == 'canvas.reference' or args['action'].startswith(('canvas.apps.', 'canvas.versions.')):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Surface actions must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'].startswith('outputs.'):
                if action_args.get('sessionId',session_id)!=session_id:
                    raise AppError('Output actions must target the calling conversation.',409)
                action_args['sessionId']=session_id
            if args['action'].startswith('publishing.'):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Publishing actions must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'] in {'canvas.show','smartTools.call','smartTools.open','runtime.dependencies','session.sharePreview','session.shareList'}:
                action_args.setdefault('sessionId',session_id)
            if args['action'] in {'session.export', 'session.exportDeliver', 'feedback.excerpt.review', 'feedback.excerpt.stage'}:
                if action_args.get('id', session_id) != session_id:
                    raise AppError('Exports must target the calling conversation.', 409)
                action_args.setdefault('id', session_id)
            if action_args.get('sessionId') == session_id and session_id:
                from .session_identity import project as native_project
                action_args.setdefault('nativeProject', native_project(self._session(session_id, hydrate=False)))
            if args['action'] == 'canvas.views.command' and action_args.get('action') == 'canvas.reference':
                if action_args.get('args', {}).get('sessionId') != session_id:
                    raise AppError('References must target the calling conversation.', 409)
            if schedule_read:
                result = await self.dispatch(args['action'], action_args, origin='agent', command_id=args.get('id'), include_state=False, caller_session_id=session_id)
                if args['action'] != 'schedule.overview':
                    result['result'] = {**result['result'], 'sessionId': action_args['sessionId'], 'readOnly': True}
                return result
            compact_smart_tool = args['action'].startswith('smartTools.')
            compact_domain_receipt = args['action'] in SETTINGS_ACTIONS or args['action'].startswith(('session.handoff.', 'capability.', 'coordination.', 'worktree.', 'assignment.', 'integration.', 'workspace.', 'permissions.', 'providers.', 'configuration.', 'bundles.', 'modules.', 'sources.', 'locations.', 'outputs.')) or args['action'] == 'runtime.provenance' or args['action'] in RESOURCE_RESERVATION_ACTIONS
            task_action = action_args.get('operation') if args['action'] == 'runtime.control' else args['action']
            compact_task_receipt = (isinstance(task_action, str) and task_action in task_definitions(schema, string)
                                    or args['action'].startswith('assignment.task.'))
            compact_domain_receipt = compact_domain_receipt or compact_task_receipt
            if args['action'].startswith('profiling.'):
                # Profiling must not flush progress or build an unrelated
                # full-catalog agent snapshot merely to inspect host timings.
                return await self.dispatch(args['action'], action_args, origin='agent',
                                           command_id=args.get('id'), include_state=False,
                                           caller_session_id=session_id)
            canvas_client = None
            if args['action'].startswith('observation.'):
                from .collaboration import BINDING
                token = self.observations.input_bindings.set((BINDING.get() or {}).get('_inputBindings', []))
                try:
                    result = await self.dispatch(args['action'], action_args, origin='agent', command_id=args.get('id'), caller_session_id=session_id, include_state=not receipt_mode)
                finally:
                    self.observations.input_bindings.reset(token)
            elif args['action'] == 'view.update' or args['action'] in SETTINGS_NAVIGATION or args['action'].startswith('computer.visual.'):
                if args['action'].startswith('computer.visual.'):
                    visual_origin(action_args.get('clientId'))
                elif input_origin is not None and not action_args.get('clientId') and input_origin['clientId'] is None:
                    raise AppError('Choose an explicit connected navigation target; the input has no single originating browser.',
                                   409, code='ui_client_required')
                from .agent_canvas import target
                requested_client = action_args.get('clientId', input_origin['clientId'] if input_origin is not None else None)
                canvas_client = target(self, session_id, requested_client, required=True, connected_only=True)[0]
                with self.clients.bind(canvas_client):
                    result = await self.dispatch(args['action'], action_args, origin='agent', command_id=args.get('id'), expected_revision=args.get('expectedRevision'), caller_session_id=session_id, include_state=not receipt_mode)
            elif args['action'] in {'session.exportDeliver', 'feedback.excerpt.stage'}:
                from .agent_canvas import target
                from . import feedback_context
                requested = action_args.pop('clientId', input_origin['clientId'] if input_origin is not None else None)
                if args['action'] == 'feedback.excerpt.stage' and feedback_context.enabled(self, 'agent', session_id):
                    result = await self.dispatch(args['action'], action_args, origin='agent', command_id=args.get('id'), caller_session_id=session_id, include_state=not receipt_mode)
                else:
                    canvas_client = target(self, session_id, requested, required=True, connected_only=True)[0]
                    if canvas_client is None:
                        raise AppError('Open the calling conversation in a connected browser before delivering an export.', 409)
                    with self.clients.bind(canvas_client):
                        result = await self.dispatch(args['action'], action_args, origin='agent', command_id=args.get('id'), caller_session_id=session_id, include_state=not receipt_mode)
            elif args['action'] in {'canvas.openFile', 'canvas.openAttachment', 'canvas.reference', 'message.reply', 'message.replyClear', 'message.reveal'}:
                from .agent_canvas import target
                canvas_client = target(self, session_id, action_args.get('clientId'), required=True, connected_only=args['action'] in MESSAGE_INTERACTIONS)[0]
                with self.clients.bind(canvas_client):
                    result = await self.dispatch(args['action'], action_args, origin='agent', command_id=args.get('id'), caller_session_id=session_id, include_state=not receipt_mode)
            elif args['action'] == 'smartTools.viewStatus':
                result = await self.dispatch(args['action'], action_args, origin='agent', caller_session_id=session_id, include_state=not receipt_mode)
                canvas_client = result['result'].get('clientId')
            elif args['action'] == 'canvas.show' and (action_args.get('presentation') == 'inline' or action_args.get('clientId')):
                from .agent_canvas import target
                requested_client = action_args.get('clientId', input_origin['clientId'] if input_origin is not None else None)
                canvas_client = target(self, session_id, requested_client, required=True)[0]
                with self.clients.bind(canvas_client):
                    result = await self.dispatch(args['action'], action_args, origin='agent',
                        command_id=args.get('id'), caller_session_id=session_id, include_state=not receipt_mode)
            elif args['action'] in {'canvas.select', 'smartTools.reconnectView', 'smartTools.reviewView'}:
                from .agent_canvas import selection_target
                canvas_client = selection_target(self, session_id, {**action_args, 'id': action_args.get('canvasId', action_args.get('id'))})
                if canvas_client is not None:
                    action_args['clientId'] = canvas_client
                with self.clients.bind(canvas_client):
                    result = await self.dispatch(args["action"], action_args, origin="agent", command_id=args.get("id"), expected_revision=args.get("expectedRevision"), caller_session_id=session_id, include_state=not receipt_mode)
            elif input_origin is not None and 'clientId' in action_args and (
                    args['action'].startswith(('canvas.views.', 'canvas.apps.'))
                    or args['action'] in {'canvas.visibility', 'theme.preview', 'theme.revert'}):
                # An explicit Canvas target is independent of screen consent.
                canvas_client = action_args['clientId']
                with self.clients.bind(canvas_client):
                    result = await self.dispatch(args['action'], action_args, origin='agent', command_id=args.get('id'), expected_revision=args.get('expectedRevision'), caller_session_id=session_id, include_state=not receipt_mode)
            else:
                result = await self.dispatch(args["action"], action_args, origin="agent", command_id=args.get("id"), expected_revision=args.get("expectedRevision"), caller_session_id=session_id, include_state=not (receipt_mode or compact_smart_tool or compact_domain_receipt))
            if compact_task_receipt:
                # Task dispatch already published its canonical cache or
                # reported publication unavailable with the committed receipt.
                # Unrelated progress publication must not lose that receipt.
                return result
            await self._flush_pending_progress()
            if receipt_mode:
                from .agent_receipts import compact_receipt
                return compact_receipt(result, action=args['action'], session_id=session_id,
                    revision=self.state['revision'], client_id=canvas_client, command_id=args.get('id'))
            if compact_domain_receipt:
                # The exact domain receipt already carries admission, correlation
                # and result evidence. Repeating the app overview on each tool
                # call can exhaust a single, non-compactable human turn.
                return {**result, 'state': {
                    'revision': self.state['revision'], 'sessionId': session_id,
                    '_stateAccess': {'note': 'The domain receipt preserves admission and the full action result when available. A pending operation is not completion. Use get_state with a JSON Pointer for saved results or other state.',
                        **({'resultPaths': ['/permissions', '/managementResults/' + result['operationId']]} if args['action'].startswith('permissions.') and result.get('operationId') else {})}}}
            if compact_smart_tool:
                context = {'revision': self.state['revision'], 'sessionId': session_id,
                           '_stateAccess': {'note': 'Use smartTools.readResult with the receipt operationId to read status and results. Use get_state with a JSON Pointer for other app state.'}}
                if result.get('operationId'):
                    result['read'] = {'action': 'smartTools.readResult', 'args': {'operationId': result['operationId']}}
            elif args['action'].startswith(('canvas.apps.', 'canvas.versions.')):
                from .agent_state import surface_context
                context = surface_context(self.state, session_id, self.clients.records)
            else:
                context = read_state(agent_snapshot(canvas_client, allow_detached=True, connected_only=args['action'] == 'view.update' or args['action'].startswith('computer.visual.')), {}, session_id=session_id, resolve=self.state_resource, directory=self.directory)
            return {**result, 'effects':[{'id':e.get('id'),'type':e.get('type')} for e in result.get('effects',[])], 'state':context}
        raise AppError("Unknown app bridge operation.")

    async def update_device(self, payload):
        bound = self.clients.current.get()
        if bound is not None and payload.get('clientId', bound) != bound:
            raise AppError('Device report belongs to another client.')
        client_id = bound or str(payload.get("clientId") or "browser")[:100]
        self.state["devices"][client_id] = {**payload, "updatedAt": time.time()}
        # Rendered controls are live observations, reset on startup. Agents read
        # them directly; no durable command or browser projection changed.

    def _check_voice_owner(self, session_id, transfer_id):
        try:
            if self.portability.write_context(session_id) != transfer_id:
                raise ValueError('This voice call predates the task transfer; its late events cannot change the task.')
        except ValueError as exc:
            raise AppError(str(exc), 409) from exc

    async def record_voice_transcript(self, role, text, *, voice_id, item_id, append=False, session_id=None, transfer_id=None):
        async with self.lock:
            session = self._session(session_id)
            self._check_voice_owner(session['id'], transfer_id)
            existing = next((m for m in session["messages"] if m.get("voiceId") == voice_id and m.get("voiceItemId") == item_id), None)
            if existing:
                existing["text"] = existing["text"] + text if append else text
                from .chat_navigation import touch_independent_activity
                touch_independent_activity(session)
            else:
                self._message(session, role, text, "call", voiceId=voice_id, voiceItemId=item_id, inputOrigin='voice')
            self._publish_changes(sessions={session['id']})

    async def set_voice_status(self, payload):
        async with self.lock:
            call=getattr(self.voice_service, 'call', None)
            if call and (payload.get('id') and payload['id'] != getattr(call, 'id', None)
                         or payload.get('attemptId') and payload['attemptId'] != getattr(call, 'attempt_id', None)):
                return  # A late event cannot replace the currently owned call.
            self.state["voice"].update(payload)
            if self.state["voice"].get("status") != "connected":
                self.voice_visual.revoke()
            self.voice_visual.publish()

    async def voice_delegate(self, text, command_id, session_id=None, *, transfer_id=None, spoken_item_id=None):
        # Persist acceptance before scheduling, just like typed commands. A repeated
        # provider event or reconnect must never execute the same tool request twice.
        async with self.lock:
            self._check_voice_owner(self._session(session_id)['id'], transfer_id)
        input_context = await self.surface_context.checkpoint(self._session(session_id)['id'])
        async with self.lock:
            if work_paused(self.state):
                raise AppError("An ecosystem update is activating. Please retry in a moment.",409)
            session = self._session(session_id)
            self._check_voice_owner(session['id'], transfer_id)
            if self.assignments.blocked(session):
                raise AppError('This assignment is stopped. Open Shared tasks and assignment in the commissioning chat and choose Resume assignment before sending new work.', 409, code='assignment_stopped')
            if session.get('configurationBusy') or self.management and self.management.unresolved_configuration(session):
                raise AppError('Applying or reconciling conversation settings; new input must wait.', 409)
            if session.get('fileAccessPending'):
                from .chat_permissions import PENDING_DETAIL
                raise AppError(PENDING_DETAIL, 409)
            fingerprint = hashlib.sha256(json.dumps(["voice_delegate", session["id"], text]).encode()).hexdigest()
            previous = self.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (command_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise AppError("This voice command ID was already used with different contents.", 409)
                return {**json.loads(previous[1]), "duplicate": True}
            receipt = {"accepted": True, "inputId": command_id, "sessionId": session["id"], "delivery": "sending"}
            self.db.execute("INSERT INTO commands VALUES (?,?,?)", (command_id, fingerprint, json.dumps(receipt)))
            # The voice owner supplies the captured utterance ID. Text alone
            # cannot bind a spoken bubble to a canonical execution boundary.
            from .voice_input_projection import decode
            spoken = decode(text, command_id)
            if spoken and spoken_item_id:
                candidates = [row for row in session['messages']
                              if row.get('role') == 'user' and row.get('voiceId') == spoken['callId']
                              and row.get('voiceItemId') == spoken_item_id]
                if (len(candidates) == 1 and candidates[0].get('text', '').strip() == spoken['text']
                        and not candidates[0].get('voiceInputId')):
                    # delegate_live trims the request; normalize that same
                    # identified bubble, never find a match by text alone.
                    candidates[0]['text'] = spoken['text']
                    candidates[0]['voiceInputId'] = command_id
            session["status"] = "working"
            self._activity(session, "queued", "Sending voice request to Amplifier", reset=True)
            ensure_turn(session,command_id,text)
            self.voice_visual.bind_input(session["id"], command_id)
            self.computer_visual.bind_input(session["id"], command_id)
            session.setdefault('surfaceInputs', {})[command_id] = input_context
            session['surfaceInputs'] = dict(list(session['surfaceInputs'].items())[-16:])
            self._publish_changes(sessions={session['id']})
            snapshot = copy.deepcopy(session)
        self._task(self._guard(self._send, (snapshot, text, command_id)))
        return receipt

    async def wait_for_response(self, session_id, input_id=None, after_count=0, timeout=120):
        # Text blocks can precede tools. Only loop-live's identified completion
        # event proves the manager has finished a turn for this input.
        queue = self.subscribe()
        try:
            async with asyncio.timeout(timeout):
                while True:
                    session = self._session(session_id)
                    for event in session.get("generations", []):
                        if (event.get('sessionId') != session_id
                                or event.get('rootSessionId', session_id) != session_id):
                            continue
                        if input_id and input_id not in event.get("input_ids", []):
                            continue
                        if event["event"] == "generation.finished":
                            return {key: event.get(key) for key in
                                ("text", "generation_id", "input_ids", "active_job_ids", "disposition", "sessionId", "rootSessionId")}
                        if event["event"] in {"generation.failed", "generation.detached"}:
                            raise AppError("The Amplifier turn ended without a completed response.")
                    if session["status"] == "read-only":
                        raise AppError("This conversation is in use elsewhere. Choose Continue here to resume.", 409, code="session_busy")
                    if session["status"] in {"error", "stopped", "interrupted"}:
                        raise AppError(session.get("error", "Execution ended before the response completed."))
                    await queue.get()
        finally:
            self.unsubscribe(queue)

    async def _diagnostics_test(self, identity, generation):
        try:result=await self.diagnostics.test(identity,generation)
        except ValueError:result={'phase':'error','message':'Save this destination before testing it.'}
        if result is None:return  # The destination changed while its test was running.
        async with self.lock:
            self.state['diagnostics']['results']=copy.deepcopy(self.diagnostics.results)
            self.state['diagnostics']['lastResult']={'action':'diagnostics.test',**result}
            self._publish_changes(globals={'diagnostics'})

    def begin_shutdown(self):
        """Fence lifecycle admission before the HTTP host yields during shutdown."""
        self.closed = True
        begin = getattr(self.runtime, 'begin_shutdown', None)
        if begin:
            begin()

    async def close(self):
        if self._close_started:
            return
        self._close_started = True
        self.begin_shutdown()
        lifecycle_tasks = [task for task in self._runtime_lifecycle_tasks
                           if task is not asyncio.current_task()]
        for task in lifecycle_tasks:
            task.cancel()
        await self.shell.resources.close()
        self.collaboration.close()
        await self.test_queue.close()
        from .test_environment_setup import close as close_test_setup
        await close_test_setup(self)
        await self.performance_diagnostics.close()
        if getattr(self, "profiling_host", None):
            await self.profiling_host.close()
        from .storage_migration import close_maintenance
        await close_maintenance(self)
        if lifecycle_tasks:
            await asyncio.gather(*lifecycle_tasks, return_exceptions=True)
        await self.voice_visual.close()
        await self.computer_visual.close()
        await self.schedules.close()
        if getattr(self, '_storage_scans', None):
            await self._storage_scans.close()
        await self.observations.close()
        await self.recall.personalization.close()
        await self.worktrees.close()
        await self.publishing.close()
        await self.portability.close()
        async with self.runtime_lifecycle_lock:
            if self.runtime:
                await self.runtime.close()
        await self.warmup.close()
        await self.history.close()
        await self.event_log_view.close()
        if self.update_manager:
            await self.update_manager.close()
        if self.management:
            for manager in (self.management.setup_manager, self.management.global_setup_manager, *self.management.workspace_setup_managers.values()):
                if manager:await manager.close()
        for task in list(self.tasks):
            if task in self.workspace_setup_tasks:
                continue  # Thread/Git completion must settle before the DB closes.
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.smart_tools:
            await self.smart_tools.close()
        if self.management:
            await self.management.provider_catalog.close()
        await self.diagnostics.close()
        if getattr(self, '_progress_dirty', False):
            self._commit_pending_progress()
        self._save_full(reason='Shutdown reconciliation of durable controller stores')
        resource_scan = getattr(self, '_resource_scan', None)
        if resource_scan is not None:
            resource_scan.close()
            self._resource_scan = None
        await self.operations.close()
        self.schedules.store.close()
        self.observations.store.close()
        await self.recall.close()
        if getattr(self, 'status_emitter', None):
            await self.status_emitter.close()
        self.db.close()
