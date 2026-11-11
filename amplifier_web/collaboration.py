"""Scoped peer authority and receipts in existing session/command records.

No transcript or scheduler is owned here. Native adapters must qualify final
results before dependency continuation can be enabled.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
import json
import time
import uuid

from amplifier_operations.coordination import fingerprint
from .task_binding import continuation_epoch, matches as task_matches, dependency_blocked
from .peer_scope import descriptor, location, participant_matches, valid_binding

PRINCIPAL = ContextVar("collaboration_principal", default=None)
BINDING = ContextVar("collaboration_binding", default=None)
CREATION = ContextVar("collaboration_creation", default=None)
BUSY = {"working", "starting", "running", "busy", "stopping", "ready"}
PROTOCOL = 2
RETIRED_HOP_LIMIT = 'The causal peer-input chain reached 16 hops; its report remains inspectable.'
RETIRED_NOTIFICATION_LIMIT = 'Automatic outcome notification limit reached; inspect the retained result.'
DEFAULT_QUEUE_SECONDS = 86400
REPORT_OUTCOMES = {'failed', 'deferred', 'declined', 'question', 'conflict', 'cancelled'}


def blocked_task_handling(task):
    return {
        'taskId': task['id'], 'taskRevision': task['revision'],
        'instruction': 'Handle this new peer input within existing authority. The saved objective '
            'remains blocked. Reconcile the dependency; do not infer approval, resume the goal '
            'automatically, discard conditions or replay older input. You may reply, defer, decline '
            'or deliberately call task.resume once you verify the dependency is resolved within your '
            'standing scope. That owner decision needs no fresh human approval; actual Stop, '
            'unresolved questions and uncertain effects remain in force.'}


def intake_task_binding(receipt):
    """Use the already captured objective identity for ordinary peer intake.

    The receipt and its original edit revision stay immutable. Special resume,
    commissioning and blocked-input paths retain their own exact contracts.
    """
    binding = {key: receipt.get(key) for key in ('taskId', 'taskRevision')}
    if 'taskContinuationEpoch' in receipt:
        binding['taskContinuationEpoch'] = receipt['taskContinuationEpoch']
    elif (receipt.get('commandAction') == 'coordination.send' and receipt.get('taskId')
            and receipt.get('mode') in {'auto', 'queue'}
            and receipt.get('origin') in {'agent', 'ui', 'user'}
            and 'replyTaskContinuationEpoch' in receipt
            and not any(receipt.get(key) for key in ('dependencyRequestId', 'creationOperationId',
                'humanResume', 'scheduleRepair', 'blockedTaskInput', 'completedFollowup'))):
        binding['taskContinuationEpoch'] = receipt['replyTaskContinuationEpoch']
    return binding


def response_ready(response):
    """A verified negative report can wake a waiter without satisfying its work."""
    return (response.get("kind") != "ack" and (
        response.get("status") in {"rejected", "unknown"}
        or response.get("status") in {"staged", "sealed"} and (
            response.get("qualified") is True or response.get("outcome") in REPORT_OUTCOMES
            or response.get('status') == 'sealed' and response.get('kind') == 'result')))



def continuation_outcome(request):
    response = request.get('response') or {}
    terminal = request.get('terminalOutcome') or {}
    candidates = ([response] if response_ready(response) else []) + (
        [terminal] if terminal.get('status') == 'observed' else [])
    return max(candidates, key=lambda row: row.get('outcomeRevision', 0)) if candidates else None


def outcome_sender(request):
    # Preparation failures are host observations about the caller's operation;
    # an uncreated peer must never be impersonated as their author.
    return (request['senderSessionId'] if request.get('commandAction') == 'coordination.create'
            else request['target']['sessionId'])


def automatic_delivery(receipt):
    return receipt.get('mode') == 'auto' or receipt.get('automaticDelivery') is True


def admission_observation(receipt, phase, result):
    """An awaited transport failure cannot erase evidence already committed."""
    current = receipt.get('delivery')
    recorded = receipt.get('admission') or {}
    if (phase == 'unknown' and current not in {'accepted', 'applied', 'held'}
            and recorded.get('accepted') is False and recorded.get('effect') == 'none'
            and (receipt.get('steering') or {}).get('event') != 'steering.unknown'):
        return 'not_sent', recorded
    if current in {'accepted', 'applied', 'held', 'not_sent'}:
        return current, receipt.get('admission', result) if phase == 'unknown' else result
    if current == 'unknown' and (automatic_delivery(receipt) or receipt.get('mode') == 'steer'):
        return current, result
    return phase, result


def definitions(schema, string):
    from .result_artifacts import REFERENCE_HELP
    from .workspace_work_invocation import references_schema
    sid = {"sessionId": string(200)}
    references = {"type": "array", "maxItems": 16, "items": string(2000)}
    message = {**sid, "senderSessionId": string(200), "grantId": string(200),
               "text": {**string(16000), "minLength": 1},
               "mode": {"enum": ["auto", "notify", "queue", "steer"], "default": "auto",
                   "description": "Auto delivers at the next supported request boundary or when idle. Queue deliberately waits for idle; notify never executes; steer targets only the current generation."},
               "returnNotification": {"type": "boolean", "default": False,
                   "description": "Auto or queue: atomically register a guarded return before the recipient can run. Omitted/false keeps one-way send behavior; exact retries never rearm a saved wait."},
               "replyToRequestId": string(200), "references": references,
               "expiresInSeconds": {"type": "integer", "minimum": 1, "maximum": 86400},
               "resume": {**schema({"sourceMessageId": string(200),
                   "sourceRequestId": {**string(200), "description":
                       "Alternative to direct sourceMessageId: exact host-issued resumeHandoff inputId positively "
                       "admitted in the commissioning owner's current authenticated native generation. One fresh "
                       "member input only; preserve humanInput:null, budgets, Stop and unknown outcomes."},
                   "expectedInterruptionRevision": {"type": "integer", "minimum": 0}},
                   ["expectedInterruptionRevision"]),
                   "oneOf": [{"required": ["sourceMessageId"]}, {"required": ["sourceRequestId"]}]},
               "resumeHandoff": {**schema({"sourceMessageId": string(200), "memberSessionId": string(200),
                   "expectedInterruptionRevision": {"type": "integer", "minimum": 0}},
                   ["sourceMessageId", "memberSessionId", "expectedInterruptionRevision"]),
                   "description": "Direct-human origin only, auto/queue to the actual commissioning owner for one "
                       "exact existing member. Host captures original human text/digest, native generation, all "
                       "three human/Stop/task/access/placement boundaries and deadline. Owner assesses canonical "
                       "humanInstruction, then deliberately uses resume.sourceRequestId. Not recursive peer, "
                       "Full/configuration/question/schedule authority; cancellation revokes unadmitted scope."},
               "scheduleRepair": schema({"sourceMessageId": string(200),
                   "schedules": {"type": "array", "minItems": 1, "maxItems": 16,
                       "items": schema({"id": string(200), "expectedRevision": {"type": "integer", "minimum": 0}}, ["id", "expectedRevision"])}},
                   ["sourceMessageId", "schedules"])}
    from .coordination_watches import definitions as watch_definitions
    return {
        **watch_definitions(schema, string),
        "coordination.grant": ("Retired. Historical grants remain readable; fresh collaboration uses authenticated root generations.", schema({
            **sid, "participants": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": string(200)},
            "sourceMessageId": string(200),
            "purpose": {**string(4000), "minLength": 1},
            "modes": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": ["notify", "queue", "steer"]}},
            "idleStart": {"type": "boolean"}, "allowCreate": {"type": "boolean"},
        }, ["sessionId", "participants", "purpose", "modes"])),
        "coordination.revoke": ("Retired. Legacy work cannot resume; use ordinary human Stop for current task work.", schema({**sid, "grantId": string(200)}, ["sessionId", "grantId"])),
        "coordination.context": ("Read bounded related receipts, current admitted humanInput identity and historical grants without loading peer transcripts. humanInput is null outside the current human-backed root generation; it is not a permission grant.", schema(sid, ["sessionId"])),
        "coordination.recap": ("Compare bounded saved work with an optional private caller cursor. Passive; no model, history hydration or shared read-state write. nextCursor advances only returned changes; retain it only after presenting those changes.", schema({**sid, "cursor": string(16000)}, ["sessionId"])),
        "coordination.diagnostics": ("Read portable bounded phase/timing metadata for this root. Hashed correlation IDs, no prompts, drafts, paths or tool payloads; unavailable timing remains explicit. Does not start work.", schema(sid, ["sessionId"])),
        "coordination.decide": ("Retired. Legacy pending proposals cannot authorize new work.", schema({
            **sid, "proposalId": string(200), "decision": {"enum": ["allow", "deny"]},
        }, ["sessionId", "proposalId", "decision"])),
        "coordination.send": ("Commit attributed task input to an exact existing root in this user's Unified installation, including other workspaces and managed chats. Discover IDs with coordination.list scope=all. No assignment, permissions, files or resume authority transfers. Auto (the default) delivers at the next supported model request boundary or when idle. Explicit queue waits for idle; notify never wakes. Preparation continues under service ownership. Read results, never replay uncertain input. For an agent-recorded dependency block, use ordinary auto/queue WITHOUT resume or humanInput: the recipient may inspect fresh input and deliberately resume its own task once the dependency is verified resolved. Human blocks, unknown block provenance and actual Stop remain protected. Reserve resume for a stopped/paused/failed commissioned same-workspace member after an explicit human instruction; use queue with resume={sourceMessageId, expectedInterruptionRevision}; the host binds the current human input and exact Stop/task/assignment boundaries. This admits one fresh input without resetting budgets, permissions or replaying old work. For explicit human repair of existing same-workspace schedules, scheduleRepair scopes the sourceMessageId and exact target IDs/revisions; the owner may resume those schedules unchanged.", schema(message, ["sessionId", "text"])),
        "coordination.create": ("Commission one authorized durable worker; repeat independently for as many parallel tasks as needed. Inherits configuration and workspace. Optional execution creates a clean isolated checkout and optional objective installs its saved goal before the first brief can run. A saved return notification reports its outcome. This does not create an independent user chat in a different folder. Inspect this operation's requestId for its initial result. Returns before preparation and does not select the peer. Set returnNotification:false only when no automatic return is wanted.", schema({
            "senderSessionId": string(200), "grantId": string(200), "title": {**string(100), "minLength": 1},
            "text": {**string(16000), "minLength": 1}, "references": references,
            "returnNotification": {"type": "boolean", "default": True},
            "workReferences": references_schema(schema, string),
            "objective": {**string(16000), "minLength": 1,
                          "description": "Authorized durable goal to pursue through completion, saved before the initial brief."},
            "constraints": {"type": "array", "maxItems": 100, "items": {**string(4000), "minLength": 1}},
            "execution": schema({"kind": {"const": "worktree"}, "repositoryPath": string(4000),
                                 "expectedCommit": {**string(64), "description": "Full Git commit ID for a clean checkout; obtain with worktree.inspect mode=clean. Local changes are not scanned or copied."},
                                 "sourceRevision": {**string(100), "description": "Legacy inspected working-folder snapshot digest, not a Git commit. Supply this or expectedCommit, never both."},
                                 "ref": string(500), "branch": string(500)}, ["kind"]),
        }, ["title", "text"])),
        "coordination.read": ("Read a bounded window from one existing native conversation without selecting or warming it.", schema({
            **sid, "scope": {"enum": ["workspace", "all"]}, "messageId": string(200), "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "textLimit": {"type": "integer", "minimum": 1, "maximum": 4000},
        }, ["sessionId"])),
        "coordination.cancel": ("Withdraw your exact queued request, or request cancellation of already admitted work. Does not stop the recipient chat, undo effects, or prove cancellation. Active recipients see the request at their next coordination boundary and report outcome=cancelled when settled. Inspect coordination.result; never replay an unknown admission.", schema({
            **sid, "requestId": string(200), "reason": string(1000)}, ["sessionId", "requestId"])),
        "coordination.acknowledge": ("After reviewing an exact result, retire only its unsubmitted completion notification. Use the review reference from coordination.result or workspace.tests.read/result and a stable command ID. Does not cancel work, qualify success, change limits or suppress later changed outcomes. Reading alone never acknowledges.", schema({**sid, "requestId": string(200), "outcomeRevision": {"type": "integer", "minimum": 1}, "reviewKey": string(200)}, ["sessionId", "requestId", "outcomeRevision", "reviewKey"])),
        "coordination.result": ("Inspect admission and exact qualified final result links for one request without loading history. Original participants retain access; a Full-access observer or its current delegated reviewer may inspect a registered request using workspace/all read scope. Observation does not change participants, producer or notification ownership. Acknowledgements and unrelated turns do not count.", schema({"requestId": string(200), "scope": {"enum": ["workspace", "all"]}}, ["requestId"])),
        "coordination.artifact": ("Read an exact retained file as an original request participant or Full-access observer, even after its source disappears. Current delegated Full-access reviewers may use this passive read. Observers use workspace/all read scope. Bounded text and download identity; no peer output-library access or work admission. Retained bytes are not proof of correctness.", schema({**sid,
            "requestId": string(200), "reference": string(2000), "scope": {"enum": ["workspace", "all"]}, "responseRevision": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 4000}}, ["sessionId", "requestId", "reference"])),
        "coordination.artifact.save": ("Explicitly save one exact request-authorized retained file, including large binaries, into your current working folder. Supply the responseRevision and reference from coordination.result, and a relative path using / separators with an existing parent folder. Requires current mounted file-write access. Streams verified bytes without HTTP or peer-library access; refuses links, escapes and different existing bytes. Identical existing bytes are hash-verified and reused. After an unknown return inspect the destination; this is not a durable exactly-once operation or correctness qualification.", schema({**sid,
            "requestId": string(200), "reference": string(2000), "scope": {"enum": ["workspace", "all"]},
            "responseRevision": {"type": "integer", "minimum": 1}, "path": {**string(2000), "minLength": 1}},
            ["sessionId", "requestId", "reference", "responseRevision", "path"])),
        "coordination.reply": ("Record a typed reply to your exact delivered request. Optional messageIds name your current-generation assistant output, never the incoming request messageId; omit them when unknown. Retain files using path@sha256:<64 lowercase hex digits> in references. Before ending the turn, check artifactVerification and each artifacts[].status: bytes are saved before this reply is acknowledged; later terminal sealing only qualifies the report. Correct a staged report in the same current generation, or reply from a later current generation after it settles, using expectedResponseRevision from coordination.result. Questions/deferrals stay open; reports are unqualified until separately proven terminal. Exact command IDs deduplicate retries. Meaningful revisions retain the original request identity without a lifetime count limit.", schema({
            "requestId": string(200), "kind": {"enum": ["result", "ack", "defer", "decline", "question", "conflict"]},
            "expectedResponseRevision": {"type": "integer", "minimum": 0},
            "outcome": {"enum": ["success", "failed", "deferred", "declined", "question", "conflict", "cancelled"]},
            "text": {**string(4000), "minLength": 1}, "references": {**references,
                "description": REFERENCE_HELP,
                "items": {**string(2000), "description": "Exact retained local file reference or opaque non-file citation."}},
            "messageIds": {"type": "array", "maxItems": 16, "items": string(200),
                           "description": "Optional exact assistant output IDs from your current generation. Never use the incoming request's messageId, a user/peer input ID, another conversation's output, or an earlier generation. Omit messageIds if no current assistant output ID is known; requestId already correlates the request. Supply evidence in references. Invalid IDs are refused before a reply revision is recorded."},
        }, ["requestId", "kind", "outcome", "text"])),
        "coordination.subscribe": ("Subscribe to meaningful revisions of one exact request, once per outcome revision without a lifetime count limit. Questions, deferrals and negative outcomes never satisfy a dependency; only qualified success does. Every continuation rechecks the saved task/Stop boundary. Cursor reads confer no authority.", schema({
            **sid, "requestId": string(200), "grantId": string(200),
        }, ["sessionId", "requestId"])),
    }


def restore_owner(session, host_instance):
    """Retire prior-host authority and native waits without replaying work."""
    if session.get('_collaborationHostInstanceId') == host_instance:
        return
    active = session.get('collaborationGeneration')
    if active and not active.get('terminal'):
        active.update(terminal=True, detail='Host restarted; no live generation authority.')
    for approval in session.get('approvals', []):
        if approval.get('status', 'pending') not in {None, 'pending'}:
            continue
        identity = approval.get('id', '')
        if identity.startswith('collaboration:'):
            approval['status'] = 'retired'
        elif identity.startswith('capability:'):
            # Its owner determines whether this was a live native wait or a
            # durable raw-API request after configuration authority is loaded.
            approval['_hostRestarted'] = True
        else:
            # Ordinary approvals resolve an in-memory native waiter. It cannot
            # survive host replacement. Configuration grants instead retain a
            # durable decision and their own authority checks across restarts.
            approval.update(status='expired', resolutionReason='host_restarted',
                detail='The app restarted before this permission was answered. Review the saved work and ask again if needed; nothing was replayed.')
    # The marker travels with this one durable owner, not a growing process-wide
    # set of every chat visited. Same-host eviction is not another restart.
    session['_collaborationHostInstanceId'] = host_instance


class Collaboration:
    def __init__(self, service):
        self.service = service
        self.draining = set()
        self.redrain = set()
        self.creating = set()
        self.expiry_handles = {}
        # Private preparation configuration, never returned by receipt APIs.
        # Native conversation history and existing command receipts stay authoritative.
        service.db.execute('''CREATE TABLE IF NOT EXISTS collaboration_preparations(
            request_id TEXT PRIMARY KEY, configuration TEXT NOT NULL)''')
        for session in service.state["sessions"]:
            restore_owner(session, service.instance_id)
        # Index receipts, not conversations or transcripts.
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_target ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.target.sessionId'))""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_sender ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.senderSessionId'))""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_admission_order ON commands(
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.admissionOrder'))
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_queued_sender ON commands(
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.senderSessionId'))
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.delivery')='queued'""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_automatic_sender ON commands(
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.senderSessionId'))
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.delivery')='queued'
            AND (json_extract(receipt,'$.mode')='auto' OR json_extract(receipt,'$.automaticDelivery')=1)""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_sender_admission ON commands(
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.senderSessionId'),
            json_extract(receipt,'$.admissionOrder'))
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.admissionOrder') IS NOT NULL""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_pending_outcomes ON commands(
            json_extract(receipt,'$.subscription.status'), json_extract(receipt,'$.senderSessionId'))
            WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','coordination.watch')
            AND (coalesce(json_extract(receipt,'$.outcomeRevision'),0)>coalesce(json_extract(receipt,'$.subscription.lastClaimedRevision'),0)
                OR json_extract(receipt,'$.subscription.status')='waiting')""")
        service.db.execute("DROP INDEX IF EXISTS coordination_backpressured_sender")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_backpressured_sender_v2 ON commands(
            json_extract(receipt,'$.senderSessionId'))
            WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','coordination.create','coordination.watch','worktree.handoff','updates.app','configuration.apply')
            AND json_extract(receipt,'$.subscription.status')='backpressured'""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_replies ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.result.requestId'))""")
        service.db.execute("""CREATE INDEX IF NOT EXISTS coordination_pair_window ON commands(
            json_extract(receipt,'$.commandAction'), json_extract(receipt,'$.senderSessionId'),
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.acceptedAt'))""")
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.create'
                AND json_extract(receipt,'$.delivery') IN ('creation_pending','created_initial_pending')""").fetchall():
            value = json.loads(encoded)
            if value.get('creationVersion') == 1:
                continue
            value.update(delivery="unknown", detail="Host restarted during creation; retained effects are not replayed.")
            self.save(identity, value)
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery')='submitting'""").fetchall():
            value = json.loads(encoded)
            value.update(delivery="unknown", detail="Host restarted during admission; no replay.")
            self.observe_admission(value, notify=False)
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.response.status')='staged'""").fetchall():
            value = json.loads(encoded)
            value["response"].update(status="unknown", qualified=False,
                detail="Host restarted before terminal proof; declaration retained, no replay.")
            value['response'].pop('outcomeRevision', None)
            self.record_outcome(value, value['response'])
            self.save_response(value)
            if value.get("subscription") and value['subscription'].get('notificationVersion') != 2:
                value["subscription"].update(status="needs_attention", detail=value["response"]["detail"])
            self.save(identity, value)
        # Suppress old automatic work before start() or runtime events can drain
        # it. Keep its original evidence; a new protocol is not retroactive consent.
        for identity, encoded in service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction') IN
                    ('coordination.send','coordination.grant.pending')""").fetchall():
            value = json.loads(encoded)
            if value.get("protocol") == PROTOCOL:
                continue
            changed = False
            if value.get("delivery") in {"queued", "pending_steer", "awaiting_approval"}:
                value["legacyDelivery"] = value["delivery"]
                value.update(delivery="suppressed", detail="Legacy coordination is read-only; no automatic admission after upgrade.")
                changed = True
            wait = value.get("subscription")
            if wait and wait.get("status") == "waiting":
                wait.update(status="suppressed", detail="Legacy wait retained without continuation.")
                changed = True
            if changed:
                self.save(identity, value)
        service.db.execute("""CREATE INDEX IF NOT EXISTS peer_request_cancellations ON commands(
            json_extract(receipt,'$.target.sessionId'), json_extract(receipt,'$.cancellation.status'))
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'""")
        from .coordination_watches import Watches
        self.watches = Watches(self)
        service.db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS peer_resume_authorization
            ON commands(json_extract(receipt,'$.humanResume.authorizationKey'))
            WHERE json_extract(receipt,'$.humanResume.authorizationKey') IS NOT NULL""")
        self.upgrade_queued_returns()
        service.db.commit()

    def upgrade_queued_returns(self):
        """Upgrade only host-created, positively unadmitted outcome notices.

        Original authored queue intent and display provenance remain unchanged.
        The same saved notification ID/claim becomes boundary eligible; no input
        with any prior submission evidence can be migrated or replayed.
        """
        after = 0
        upper = self.service.db.execute('SELECT coalesce(max(rowid),0) FROM commands').fetchone()[0]
        while after < upper:
            rows = self.service.db.execute("""SELECT rowid,receipt FROM commands INDEXED BY coordination_queued_sender
                WHERE rowid>? AND rowid<=?
                AND json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery')='queued'
                AND json_extract(receipt,'$.mode')='queue'
                AND json_extract(receipt,'$.origin')='system'
                AND json_extract(receipt,'$.automaticDelivery') IS NOT 1
                AND json_extract(receipt,'$.dependencyRequestId') IS NOT NULL
                ORDER BY rowid LIMIT 64""", (after, upper)).fetchall()
            if not rows:
                break
            after = rows[-1][0]
            for _, encoded in rows:
                value = json.loads(encoded)
                if (value.get('protocol') != PROTOCOL or value.get('inputId') != value.get('requestId')
                        or any(key in value for key in ('admission', 'admissionOrder', 'admittedAt', 'steering'))):
                    continue
                row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?',
                    (value['dependencyRequestId'],)).fetchone()
                request = json.loads(row[0]) if row else {}
                if (request.get('protocol') == PROTOCOL and request.get('subscription')
                        and request.get('senderSessionId') == value.get('target', {}).get('sessionId')
                        and outcome_sender(request) == value.get('senderSessionId')
                        and self.claim_matches(request, value)):
                    value['automaticDelivery'] = True
                    self.save(value['requestId'], value)

    def error(self, text, status=403):
        from .errors import AppError
        raise AppError(text, status)

    def source(self, args, origin, caller, *, hydrate=True, allow_descendant=False):
        sid = caller if origin not in {"ui", "user"} else args.get("senderSessionId") or args.get("sessionId")
        if not sid:
            self.error("A user must explicitly authorize a calling conversation.")
        source = self.service._session(sid, hydrate=hydrate)
        actor = PRINCIPAL.get()
        native = source.get("runtimeSessionId") or source.get("nativeIdentity") or source["id"]
        if origin not in {"ui", "user"} and actor and actor != native:
            if not allow_descendant or not self.service.access_mode.full(source):
                from .coordination_guidance import ROOT_SCOPE_MESSAGE
                self.error(ROOT_SCOPE_MESSAGE)
            self.active_binding(source, allow_descendant=True)
        if source.get("sessionKind", "root") != "root":
            self.error("Collaboration belongs to ordinary root conversations.")
        return source

    def grant(self, source, identity):
        row = self.service.db.execute("SELECT receipt FROM commands WHERE id=?", (identity,)).fetchone()
        if not row or json.loads(row[0]).get("commandAction") != "coordination.grant":
            self.error("A user must explicitly authorize a current collaboration grant.")
        value = json.loads(row[0])["result"]
        if value.get("revoked") or source["id"] not in value["participants"]:
            self.error("The collaboration grant is revoked or outside this caller's scope.")
        if source["workspace"] != value["workspace"]:
            self.error("The collaboration grant's workspace changed.")
        return value

    def authorize(self, source, args, *, hydrate=True, cross_workspace=False):
        # _session resolves only this installation's registered identities. Keep
        # the default narrow for adoption, watches and other non-message callers.
        target = self.service._session(args["sessionId"], hydrate=hydrate)
        if not cross_workspace and target["workspace"] != source["workspace"]:
            self.error("The target is outside this caller's workspace.")
        if target.get("sessionKind", "root") != "root":
            self.error("Peer worker admission is unsupported; target its authorized root instead.", 409)
        return target

    def request_target(self, source, receipt, *, hydrate=True):
        """Revalidate both frozen locations; never widen historical receipts."""
        if not valid_binding(receipt) or source.get('id') != receipt.get('senderSessionId'):
            self.error('The exact request location binding is invalid.', 409)
        target = self.authorize(source, receipt['target'], hydrate=hydrate,
                                cross_workspace=receipt.get('endpointBindingVersion') == 1)
        if not participant_matches(receipt, source) or not participant_matches(receipt, target):
            self.error('The request participant workspace or location changed.', 409)
        return target

    def lineage(self, source, origin):
        if origin not in {"ui", "user"}:
            binding, active = self.active_binding(source)
            return {"sourceGenerationId": active["id"],
                    "sourceInterruptionRevision": source.get("interruptionRevision", 0),
                    "sourceInputIds": [row.get("inputId") for row in binding.get("_inputBindings", [])
                                       if row.get("inputId") in active.get("inputIds", [])]}
        return {"sourceGenerationId": None, "sourceInputIds": [],
                "sourceInterruptionRevision": source.get("interruptionRevision", 0)}

    def receipt(self, identity):
        row = self.service.db.execute("SELECT receipt FROM commands WHERE id=?", (identity,)).fetchone()
        if not row:
            self.error("Request receipt not found.", 404)
        return json.loads(row[0])

    def save(self, identity, receipt):
        self.service.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
        self.refresh_reference(receipt)

    def duplicate(self, identity, digest):
        row = self.service.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (identity,)).fetchone()
        if row:
            if row[0] != digest:
                self.error("This command ID was already used with different contents.", 409)
            return {**json.loads(row[1]), "duplicate": True}

    def insert(self, identity, digest, receipt):
        receipt.setdefault('acceptedAt', time.time())
        self.service.db.execute("INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)",
                                (identity, digest, json.dumps(receipt)))
        self.refresh_reference(receipt)

    def require_inbox_slot(self, sid, sender=None, assignment=None, *, slots=1):
        from .test_execution_settings import concurrency, PEER_INBOX_COUNTS
        limits = concurrency(self.service)
        if not any(limits[key] for key in PEER_INBOX_COUNTS):
            return  # Uncapped admission needs no receipt counts or native task read.
        # A saved wait reserves one slot for its eventual outcome. Interim
        # notifications keep both their queued input and the next return slot.
        if limits['maxPendingPeerInputs']:
            pending = self.service.db.execute("""SELECT count(*) FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer')""", (sid,)).fetchone()[0]
            session = self.service._session(sid)
            task = session.get('task') or self.service.coordination.task(sid)
            waiting = self.service.db.execute("""SELECT count(*) FROM commands
                WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','coordination.create','coordination.watch','workspace.tests.submit','worktree.handoff','configuration.apply')
                AND json_extract(receipt,'$.senderSessionId')=?
                AND (json_extract(receipt,'$.subscription.status') IN ('waiting','reserved','backpressured')
                    OR json_extract(receipt,'$.subscription.open')=1)
                AND json_extract(receipt,'$.subscription.interruptionRevision')=?
                AND json_extract(receipt,'$.subscription.taskId') IS ?
                AND ((json_type(receipt,'$.subscription.taskContinuationEpoch') IS NOT NULL
                      AND json_extract(receipt,'$.subscription.taskContinuationEpoch') IS ?)
                     OR (json_type(receipt,'$.subscription.taskContinuationEpoch') IS NULL
                         AND json_extract(receipt,'$.subscription.taskRevision') IS ?))""",
                (sid, session.get('interruptionRevision', 0), task.get('id'),
                 continuation_epoch(task), task.get('revision'))).fetchone()[0]
            if pending + waiting + slots > limits['maxPendingPeerInputs']:
                self.error('This conversation inbox is full, including reserved replies. Wait for pending work to settle; no input was added.', 429)
        for key, value, limit, label in [
                ('senderSessionId', sender, limits['maxPendingPeerInputsPerSender'], 'sender'),
                ('assignmentId', assignment, limits['maxPendingPeerInputsPerAssignment'], 'assignment')]:
            if not value or not limit:
                continue
            count = self.service.db.execute(f"""SELECT count(*) FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.{key}')=?
                AND json_extract(receipt,'$.delivery') IN ('queued','submitting','pending_steer')""", (sid, value)).fetchone()[0]
            if count >= limit:
                self.error(f'This {label} has {limit} pending inputs in the conversation inbox. Wait for admission before adding more work.', 429)

    def causal_depth(self, source, lineage, origin):
        if origin in {'ui', 'user'}:
            return 0
        inputs = set(lineage.get('sourceInputIds', []))
        # Only retained host provenance can reset a causal chain. Equal prose,
        # a role=user native row, or a model's claimed human approval cannot.
        if any(row.get('inputId') in inputs and row.get('inputOrigin') in {'ui', 'user'}
               for row in source.get('messages', [])):
            return 0
        depth = 0
        for identity in inputs:
            row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if row:
                receipt = json.loads(row[0])
                if receipt.get('commandAction') == 'coordination.send' and receipt.get('target', {}).get('sessionId') == source['id']:
                    depth = max(depth, receipt.get('causalDepth', 0))
        return depth + 1

    def require_message_rate(self, source, target):
        from .test_execution_settings import concurrency
        limit = concurrency(self.service)['maxPeerMessagesPerMinute']
        if not limit:
            return
        count = self.service.db.execute("""SELECT count(*) FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.senderSessionId')=?
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.acceptedAt')>?""",
            (source, target, time.time() - 60)).fetchone()[0]
        if count >= limit:
            self.error(f'The configured limit of {limit} messages between these conversations in one minute was reached; no input was added.', 429)

    def refresh_reference(self, receipt):
        """Only affected roots retain a tiny reference projection, not bodies."""
        if receipt.get('commandAction') == 'coordination.watch':
            sids = [receipt.get('senderSessionId')]
        elif receipt.get("commandAction") == "coordination.grant":
            sids = receipt["result"]["participants"]
        else:
            sids = [receipt.get("senderSessionId"), receipt.get("target", {}).get("sessionId")]
        for sid in set(sids):
            if not sid:
                continue
            try:
                session = self.service._session(sid, hydrate=False)
            except Exception:
                continue
            value = self.current(sid)
            from .request_cancellation import references
            session["coordinationReference"] = {
                "sessionId": sid, "capturedAt": value["capturedAt"], "bounded": True,
                "grants": [{key: row[key] for key in ("id", "revision", "revoked", "participants", "idleStart", "allowCreate")}
                           for row in value["grants"][:8]],
                "cancellations": references(self, sid),
                "requests": [{key: row.get(key) for key in ("requestId", "senderSessionId", "target", "delivery", "grantId", "cancellation")}
                             for row in value["requests"][:8]], "continuation": value["continuation"]}

    def current(self, sid):
        grants = [json.loads(row[0])["result"] for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.grant'
            AND EXISTS (SELECT 1 FROM json_each(json_extract(receipt,'$.result.participants')) WHERE value=?)
            ORDER BY rowid DESC LIMIT 32""", (sid,)).fetchall()]
        requests = [json.loads(row[0]) for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND (json_extract(receipt,'$.senderSessionId')=? OR json_extract(receipt,'$.target.sessionId')=?)
            ORDER BY rowid DESC LIMIT 32""", (sid, sid)).fetchall()]
        from .request_cancellation import status as cancellation_status
        for request in requests:
            if request.get('cancellation'):
                request['cancellation'] = cancellation_status(request)
        proposals = [json.loads(row[0]) for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.grant.pending'
            AND EXISTS (SELECT 1 FROM json_each(json_extract(receipt,'$.result.participants')) WHERE value=?)
            ORDER BY rowid DESC LIMIT 32""", (sid,)).fetchall()]
        watches = [json.loads(row[0]) for row in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.watch'
            AND json_extract(receipt,'$.senderSessionId')=? ORDER BY rowid DESC LIMIT 16""", (sid,)).fetchall()]
        return {"grants": [row for row in grants if sid in row["participants"]], "proposals": proposals, 'watches': watches,
                "workspace": self.service._session(sid, hydrate=False)["workspace"],
                "requests": requests, "capturedAt": time.time(),
                "continuation": {"supported": hasattr(self.service.runtime, "collaboration_input"),
                                 "qualification": "recipient declaration plus successful root terminal evidence",
                                 "independentArtifactVerification": False}}

    def active_binding(self, source, *, allow_descendant=False):
        """Validate current native work; root identity remains the default.

        An opted-in effect may accept an actual descendant principal only when
        its trusted bridge supplies the owner's admitted generation. This does
        not grant root identity or infer authority from a human input role.
        """
        binding = BINDING.get() or {}
        native = source.get("runtimeSessionId") or source.get("nativeIdentity") or source["id"]
        active = source.get("collaborationGeneration") or {}
        if allow_descendant and '_childOrigin' in binding:
            from .child_execution import current
            child = current(self.service, source)
            if child is None:
                from .configuration_recovery import recovery_child
                child = recovery_child(self.service, source)
            if child is None:
                self.error('The admitted native child execution is no longer current.')
            # The child owner validates its original assignment independently
            # of a normal parent turn finish or a newer parent generation.
            active = {'id': child['generationId'], 'inputIds': list(child['inputIds']),
                      'interruptionRevision': child['interruptionRevision'], 'terminal': False}
        principal = PRINCIPAL.get()
        descendant = (allow_descendant and isinstance(principal, str) and bool(principal)
            and binding.get('_runtimeSessionId') == principal)
        if (principal != native and not descendant or not binding.get("_generationId")
                or binding["_generationId"] != active.get("id") or active.get("terminal")):
            self.error("Collaboration requires the current transport-bound root generation.")
        if active.get('interruptionRevision', 0) != source.get('interruptionRevision', 0):
            self.error('The current transport-bound generation crossed Stop.')
        return binding, active

    def current_human_input(self, source):
        """Expose a bounded current-input identity, never a permission grant."""
        from .errors import AppError
        from .peer_resume import human_input
        try:
            binding, active = self.active_binding(source)
        except AppError:
            return None
        human, question = human_input(self.service, source)
        admitted = set(active.get('inputIds', [])) & {
            row.get('inputId') for row in binding.get('_inputBindings', [])}
        if not human or human.get('inputId') not in admitted:
            return None
        return {'sourceMessageId': human['id'], 'sourceInputId': human['inputId'],
                'createdAt': human.get('createdAt'), 'generationId': active['id'],
                **({'questionAnswer': question} if question else {})}

    def validate_reply(self, source, args):
        binding, active = self.active_binding(source)
        request = self.receipt(args["requestId"])
        delivered = set(active.get('inputIds', [])) & {row.get('inputId') for row in binding.get('_inputBindings', [])}
        if (request.get('commandAction') != 'coordination.send'
                or request.get('target', {}).get('sessionId') != source['id']
                or not delivered or request.get('delivery') not in {'accepted', 'applied', 'submitting'}):
            self.error('Declare only your exact delivered request from a current root generation.')
        if request.get('protocol') != PROTOCOL:
            self.error('Legacy requests cannot receive fresh response declarations.', 410)
        if (not participant_matches(request, source)
                or not participant_matches(request, self.service._session(request['senderSessionId'], hydrate=False))):
            self.error('The recipient request workspace or location changed.', 409)
        revision = request.get('responseRevision', 1 if request.get('response') else 0)
        if args.get('expectedResponseRevision', 0) != revision:
            self.error('The response revision changed. Read the retained result and use its expectedResponseRevision.', 409)
        previous = request.get('response') or {}
        if previous.get('status') == 'staged' and previous.get('generationId') != active['id']:
            self.error('The previous report belongs to another generation awaiting terminal evidence. Inspect it before submitting another revision.', 409)
        if args['requestId'] not in delivered:
            if request.get('delivery') not in {'accepted', 'applied'}:
                self.error('A later generation needs confirmed prior input admission.', 409)
            reason = self.guard(request, for_reply=True)
            if reason:
                self.error('The original request boundary no longer permits a later reply: ' + reason, 409)
        self.validate_reply_message_ids(source, args.get('messageIds', []), active['id'])
        return active, request, revision, delivered

    def validate_reply_message_ids(self, source, identities, generation):
        if not identities:
            return
        # This preflight uses current retained metadata only. A checkpoint can
        # name a canonical native row outside the display window; the terminal
        # path still resolves that exact row and rejects conflicting evidence.
        messages = {row['id']: row for row in source.get('messages', [])}
        anchors = {row['messageId']: row for row in source.get('collaborationMessageAnchors', [])}
        for identity in identities:
            message, anchor = messages.get(identity), anchors.get(identity)
            if message is not None:
                linked_generation = message.get('generationId')
                if (linked_generation is None and anchor
                        and anchor.get('nativeIndex') == message.get('nativeIndex')):
                    linked_generation = anchor.get('generationId')
                valid = message.get('role') == 'assistant' and linked_generation == generation
            else:
                valid = (anchor is not None and anchor.get('generationId') == generation
                         and type(anchor.get('nativeIndex')) is int and anchor['nativeIndex'] >= 0)
            if not valid:
                self.error('Invalid messageIds: each ID must name your assistant output from this current generation, '
                           'never the incoming request messageId, a user/peer input, or another conversation or generation. '
                           'Omit messageIds if no current assistant output ID is known; requestId already correlates the request. '
                           'Keep any evidence in references. No reply revision was recorded; correct the arguments and retry.', 400)

    def require_result_reader(self, receipt, origin, caller, *, scope='workspace'):
        if origin in {'ui', 'user'}:
            return
        self.require_request_reader(receipt, self.result_source({}, origin, caller), scope=scope)

    def result_source(self, args, origin, caller):
        """Passive evidence reads may use a currently admitted Full child."""
        source = self.source(args, origin, caller, hydrate=False, allow_descendant=True)
        actor = PRINCIPAL.get()
        native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
        if actor and actor != native:
            from .child_execution import current
            # Generic configuration recovery is not an admitted evidence review.
            if not self.service.access_mode.full(source) or current(self.service, source) is None:
                self.error('This delegated evidence reader is no longer current.')
        return source

    def require_request_reader(self, receipt, reader, *, scope='workspace'):
        """Authorize observation separately from the original request owners."""
        sid = receipt.get('target', {}).get('sessionId') or receipt['sessionId']
        sender = receipt.get('senderSessionId') or receipt.get('result', {}).get('creatorSessionId')
        participant = reader['id'] in {sender, sid}
        if not participant and not self.service.access_mode.full(reader):
            self.error("This result is outside the caller's request and workspace scope.")
        fields = ('id', 'workspace', 'location')
        if receipt.get('commandAction') == 'coordination.send':
            original, target = self.service._session_read_sources([sender, sid], fields)
            if not all(participant_matches(receipt, row) for row in (original, target)):
                self.error("This result is outside the caller's recorded request workspace scope.")
        else:
            original, = self.service._session_read_sources([sender], fields)
            workspace = receipt.get('workspace') or original['workspace']
            # A creation receipt can precede its reserved root. Its recorded
            # workspace still scopes inspection without admitting that root.
            target = {'id': sid, 'workspace': workspace}
            if (participant and reader['workspace'] != workspace) or original['workspace'] != workspace:
                self.error("This result is outside the caller's request and workspace scope.")
        if not participant:
            from .owned_history import includes
            if not includes(reader, target, scope):
                self.error('Result is outside the requested history scope. Use explicit scope=all for registered own-history.')

    def resolve_message(self, session, identity, *, strict_source=False):
        def correlated(row):
            anchor = next((item for item in session.get("collaborationMessageAnchors", [])
                           if item["messageId"] == identity and item["nativeIndex"] == row.get("nativeIndex")), None)
            if (row.get("role") == "assistant" and anchor
                    and row.get("generationId") in {None, anchor["generationId"]}):
                return {**row, "generationId": anchor["generationId"]}
            return row
        message = next((row for row in session.get("messages", []) if row["id"] == identity), None)
        if message:
            return correlated(message)
        # A new Worker can checkpoint a terminal before catalog discovery has
        # populated nativeProject. Resolve its existing canonical history home
        # without rewriting placement or relying on a catalog refresh.
        from .session_identity import project
        native_project = project(session)
        if not native_project:
            return None
        from .automatic_history import read_transcript
        rows = read_transcript({**session, "nativeProject": native_project}, limit=None)["messages"]
        native = next((row for row in rows if row["id"] == identity), None)
        if native is None:
            return None
        # User authority comes only from retained host input provenance, never
        # from native role=user or equal prose. Index anchors resolve assistants.
        candidates = [row for row in session.get("messages", []) if (
            native.get("nativeInputId") and row.get("inputId") == native["nativeInputId"]
            or type(native.get("nativeIndex")) is int and row.get("nativeIndex") == native["nativeIndex"])]
        if candidates:
            if len(candidates) != 1 or native.get("nativeInputAmbiguous"):
                self.error("The exact saved message cannot be reconciled unambiguously.", 409)
            retained = candidates[0]
            if strict_source and (
                    native.get("nativeInputId") and retained.get("inputId")
                    and native["nativeInputId"] != retained["inputId"]
                    or retained.get("text") != native.get("text")):
                self.error("The retained source conflicts with its exact native input.", 409)
            if (strict_source and native.get("role") == "user"
                    and not (native.get("nativeInputId") and retained.get("inputId") == native["nativeInputId"])):
                # An index alone is not host input provenance for accepted Work.
                retained = {**retained, "inputOrigin": "unknown"}
            anchor = next((item for item in session.get("collaborationMessageAnchors", [])
                           if item["messageId"] == identity and item["nativeIndex"] == native.get("nativeIndex")), None)
            if (retained.get("role") != native.get("role")
                    or retained.get("role") == "assistant" and (
                        retained.get("text") != native.get("text")
                        or anchor and retained.get("generationId") not in {None, anchor["generationId"]})):
                self.error("The retained message conflicts with its exact native anchor.", 409)
            # Return linkage only after resolving the real canonical row. Keep
            # the original web identity and history untouched; never alias by prose.
            return correlated({**retained, "nativeMessageId": native["id"],
                               "nativeIndex": native["nativeIndex"]})
        anchor = next((row for row in session.get("collaborationMessageAnchors", [])
                       if row["messageId"] == identity and row["nativeIndex"] == native.get("nativeIndex")), None)
        return {**native, "generationId": anchor["generationId"]} if anchor else None

    def publish_receipt(self, receipt):
        ids = {receipt.get('senderSessionId'), receipt.get('sourceSessionId'),
               receipt.get('sessionId'), (receipt.get('target') or {}).get('sessionId')}
        ids.discard(None)
        if receipt.get('commandAction') == 'coordination.reply':
            request = self.receipt(receipt['result']['requestId'])
            ids = {request['senderSessionId'], request['target']['sessionId']}
        if receipt.get('commandAction') == 'coordination.watch':
            ids = {receipt['senderSessionId']}
        self.service._publish_changes(sessions=ids)

    async def decide(self, args, origin):
        self.error("Coordination approval APIs are retired. Historical decisions are read-only.", 410)

    def require_continuation(self):
        if not hasattr(self.service.runtime, "collaboration_input"):
            self.error("This runtime lacks guarded idle peer admission; no task or wait was created.", 409)

    def saved_wait(self, source, request_id, *, status='waiting'):
        task = source.get('task') or self.service.coordination.task(source['id'])
        return {'requestId': request_id, 'taskId': task.get('id'), 'taskRevision': task.get('revision'),
                'taskContinuationEpoch': continuation_epoch(task),
                'interruptionRevision': source.get('interruptionRevision', 0), 'status': status,
                'continuationId': str(uuid.uuid5(uuid.NAMESPACE_URL, 'collaboration-wait:' + source['id'] + ':' + request_id)),
                'notificationVersion': 2, 'lastClaimedRevision': 0, 'open': True,
                'supported': hasattr(self.service.runtime, 'collaboration_input')}

    def record_outcome(self, request, outcome):
        if outcome.get('outcomeRevision') is None:
            request['outcomeRevision'] = request.get('outcomeRevision', 0) + 1
            outcome['outcomeRevision'] = request['outcomeRevision']
        return outcome['outcomeRevision']

    def save_response(self, request):
        response = request.get('response') or {}
        operation = response.get('operationId')
        if operation:
            row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (operation,)).fetchone()
            if row:
                receipt = json.loads(row[0])
                receipt['result'] = copy.deepcopy(response)
                self.save(operation, receipt)

    def expire_later(self, receipt):
        identity, deadline = receipt.get('requestId'), receipt.get('expiresAt')
        if not identity or not deadline or identity in self.expiry_handles:
            return
        def wake():
            self.expiry_handles.pop(identity, None)
            if not self.service.closed:
                self.service._task(self.expire(identity))
        self.expiry_handles[identity] = asyncio.get_running_loop().call_later(max(0, deadline-time.time()), wake)

    def cancel_expiry(self, identity):
        handle = self.expiry_handles.pop(identity, None)
        if handle:
            handle.cancel()

    async def expire(self, identity):
        async with self.service.lock:
            receipt = self.receipt(identity)
            if receipt.get('delivery') != 'queued' or receipt.get('expiresAt', float('inf')) > time.time():
                return
            receipt.update(delivery='expired', detail='The queued request expired before runtime admission; its input was not executed.')
            self.observe_admission(receipt)
            self.publish_receipt(receipt)

    def close(self):
        self.watches.close()
        for handle in self.expiry_handles.values():
            handle.cancel()
        self.expiry_handles.clear()

    def observe_admission(self, receipt, *, notify=True):
        phase = receipt.get('delivery')
        if phase in {'unknown', 'not_sent', 'suppressed', 'expired'}:
            detail = (receipt.get('admission') or {}).get('reason') or receipt.get('detail') or 'Inspect the retained admission receipt.'
            outcome = {'status': 'observed', 'kind': 'admission',
                'outcome': 'unknown' if phase == 'unknown' else 'failed', 'qualified': False,
                'requestId': receipt['requestId'],
                'text': f'The host recorded input admission as {phase}. {detail}'}
            if any((receipt.get('terminalOutcome') or {}).get(k) != v for k, v in outcome.items()):
                receipt['terminalOutcome'] = {**outcome, 'observedAt': time.time()}
                self.record_outcome(receipt, receipt['terminalOutcome'])
        self.save(receipt['requestId'], receipt)
        if notify and receipt.get('subscription') and continuation_outcome(receipt):
            self.enqueue_continuation(receipt)

    def creation_guard(self, receipt, *, for_reply=False):
        if reason := self.service.assignments.request_membership_reason(receipt):
            return reason
        if receipt.get('assignmentId'):
            reason = self.service.assignments.blocked(identity=receipt['assignmentId'])
            if reason:
                return reason
        try:
            source = self.service._session(receipt['senderSessionId'])
        except Exception:
            return 'The requester is no longer available.'
        task = source.get('task') or self.service.coordination.task(source['id'])
        from .updates import work_paused
        if work_paused(self.service.state):
            return 'Host work is paused for an update.'
        if (source.get('interruptionRevision', 0) != receipt['sourceInterruptionRevision']
                or task.get('status') in {'paused', 'blocked', 'completed'}):
            return 'The requester or its assignment was stopped.'
        binding = {'taskId': receipt.get('sourceTaskId'), 'taskRevision': receipt.get('sourceTaskRevision')}
        # A later report describes already admitted work. Progress on the same
        # objective may retain that evidence without authorizing new execution.
        if for_reply and 'sourceTaskContinuationEpoch' in receipt:
            binding['taskContinuationEpoch'] = receipt['sourceTaskContinuationEpoch']
        if not task_matches(task, binding):
            return 'The requesting assignment changed.'
        if (source['workspace'] != receipt['workspace']
                or (source.get('workingDirectory') or source['workspace']) != receipt.get('sourceWorkingDirectory', receipt['result']['workingDirectory'])):
            return 'The requester execution placement changed.'
        if receipt.get('workReferences'):
            return self.service.work_invocation.plan_reason(receipt)

    def can_steer(self, target):
        active = target.get("collaborationGeneration") or {}
        return bool(hasattr(self.service.runtime, "collaboration_steer") and active.get("id") and not active.get("terminal"))

    def blocked_input(self, receipt, task):
        # Canonical result returns already carry the saved wait and exact
        # outcome claim, rechecked by guard(). They use the same bounded idle
        # review admission as fresh dependency input, without resuming the goal.
        if receipt.get('dependencyRequestId'):
            return (receipt.get('origin') == 'system'
                    and receipt.get('commandAction') == 'coordination.send'
                    and receipt.get('mode') == 'auto'
                    and bool(receipt.get('dependencyOutcomeRevision'))
                    and 'taskContinuationEpoch' in receipt
                    and task_matches(task, receipt) and dependency_blocked(task))
        return (receipt.get('blockedTaskInput') is True
                and receipt.get('commandAction') == 'coordination.send'
                and receipt.get('mode') in {'queue', 'auto'}
                and not any(receipt.get(key) for key in
                            ('dependencyRequestId', 'creationOperationId', 'humanResume', 'scheduleRepair'))
                and 'taskContinuationEpoch' not in receipt
                and task_matches(task, receipt) and dependency_blocked(task))

    def guard(self, receipt, *, for_reply=False):
        if not for_reply and receipt.get('cancellation'):
            return 'The requester cancelled this exact request before further admission.'
        try:
            if receipt.get('delivery') == 'queued' and receipt.get('expiresAt', float('inf')) <= time.time():
                return 'The queued request expired before runtime admission.'
            if receipt.get("protocol") != PROTOCOL:
                return "Legacy coordination is read-only; automatic admission is suppressed."
            if reason := self.service.assignments.request_membership_reason(receipt):
                return reason
            from .updates import work_paused
            if work_paused(self.service.state):
                return "Host work is paused for an update; peer admission is suppressed."
            source = self.service._session(receipt["senderSessionId"])
            reason = self.service.assignments.blocked(source, receipt.get('assignmentId'))
            if reason:
                return reason
            if (receipt.get('assignmentId') and not receipt.get('dependencyRequestId')
                    and receipt.get('sourceInterruptionRevision') != source.get('interruptionRevision', 0)):
                return 'The assignment requester was interrupted after this request; old input is not replayed.'
            if receipt.get('creationOperationId'):
                creation = self.receipt(receipt['creationOperationId'])
                reason = self.creation_guard(creation, for_reply=for_reply)
                if reason:
                    return reason
            if receipt.get('dependencyRequestId'):
                dependency = self.receipt(receipt['dependencyRequestId'])
                if reason := self.service.assignments.request_membership_reason(dependency):
                    return reason
                if dependency.get('cancellation'):
                    return 'The requester cancelled the originating request; no new automatic return is admitted.'
                if dependency.get('commandAction') == 'configuration.apply':
                    from .configuration_continuation import continuation_guard
                    if reason := continuation_guard(self.service, dependency):
                        return reason
                if dependency.get('commandAction') == 'updates.app':
                    from .update_drain import continuation_guard
                    if reason := continuation_guard(self.service, dependency):
                        return reason
                if dependency.get('commandAction') == 'workspace.tests.submit':
                    if reason := self.service.test_queue.continuation_guard(dependency):
                        return reason
                if dependency.get('commandAction') == 'worktree.handoff':
                    reason = self.service.worktrees.continuation_guard(dependency)
                    if reason:
                        return reason
                if dependency.get('commandAction') == 'coordination.watch' and dependency.get('watch', {}).get('status') == 'cancelled':
                    return 'The one-shot watch was cancelled.'
                if receipt.get('dependencyOutcomeRevision') and not self.claim_matches(dependency, receipt):
                    return 'This outcome notification was superseded or its qualification changed; inspect the current result.'
            elif 'taskContinuationEpoch' in receipt:
                return 'Only retained outcome notifications can follow a continuing objective.'
            target = self.request_target(source, receipt)
            if target.get('fileAccessPending'):
                return 'The recipient has pending file-access settings; existing work must settle before new input.'
            reason = self.service.assignments.blocked(target)
            if reason:
                return reason
            if source["workspace"] != receipt.get("workspace"):
                return "The request workspace changed."
            if target.get("interruptionRevision", 0) != receipt["interruptionRevision"]:
                return "The user interrupted the recipient after this request."
            from . import peer_resume
            if (receipt.get('resumeHandoff') and receipt.get('delivery') in {'queued', 'submitting'}):
                from .resume_handoff import guard as handoff_guard
                if reason := handoff_guard(self.service, receipt):
                    return reason
            resuming = peer_resume.pending(receipt)
            if resuming and (reason := peer_resume.guard(self.service, receipt, source, target)):
                return reason
            if target.get("status") == "stopping" or (
                    target.get("status") in {"stopped", "interrupted", "error", "cancelled", "failed"} and not resuming):
                return "The recipient is stopped or unavailable."
            task = target.get("task") or self.service.coordination.task(target["id"])
            from .completed_followup import matches_request
            completed_followup = matches_request(self.service, receipt, task, for_reply=for_reply)
            if (task.get("status") in {"paused", "blocked", "completed", "cancelled"}
                    and not resuming and not self.blocked_input(receipt, task) and not completed_followup):
                return "The recipient task is not active."
            task_binding = intake_task_binding(receipt)
            if for_reply and 'replyTaskContinuationEpoch' in receipt:
                task_binding = {**receipt, 'taskContinuationEpoch': receipt['replyTaskContinuationEpoch']}
            if not task_matches(task, task_binding) and not completed_followup:
                return "The recipient task changed."
            if target.get("ownership", {}).get("status") in {"blocked", "yielding", "yielded", "yield-failed", "taking-over"}:
                return "The native owner is unavailable."
            if receipt.get("mode") == "steer":
                active = target.get("collaborationGeneration") or {}
                if active.get("terminal") or active.get("id") != receipt.get("targetGenerationId"):
                    return "The anchored recipient generation is no longer active."
        except Exception as exc:
            return str(exc)

    def pending_continuation(self, sid):
        """Read an outcome-backed, unsubmitted exact wait; never claim or publish."""
        from .update_drain import owner_waiting
        if owner_waiting(self.service, sid):
            return
        for (encoded,) in self.service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.delivery')='queued'
                AND json_extract(receipt,'$.mode') IN ('queue','auto')
                AND json_extract(receipt,'$.dependencyRequestId') IS NOT NULL
                ORDER BY rowid""", (sid,)):
            receipt = json.loads(encoded)
            if self.guard(receipt):
                continue
            row = self.service.db.execute("SELECT receipt FROM commands WHERE id=?",
                                          (receipt["dependencyRequestId"],)).fetchone()
            request = json.loads(row[0]) if row else {}
            wait, response = request.get("subscription") or {}, request.get("response") or {}
            if (request.get("protocol") == PROTOCOL and request.get("senderSessionId") == sid
                    and outcome_sender(request) == receipt.get("senderSessionId")
                    and receipt.get('inputId') == receipt.get('requestId')
                    and self.claim_matches(request, receipt)
                    and (receipt.get('outcomeSnapshot') is not None or continuation_outcome(request) is not None)
                    and any(m.get("id") == receipt.get("messageId") and m.get("inputId") == receipt["inputId"]
                            and m.get("inputOrigin") == "peer"
                            for m in self.service._session(sid).get("messages", []))):
                return receipt

    def continuation_ready(self, sid, args):
        source = self.source({}, "agent", sid)
        self.active_binding(source)  # Missing/foreign root or stale generation fails closed.
        task = source.get("task") or self.service.coordination.task(sid)
        if (task.get("id"), task.get("revision")) != (args.get("taskId"), args.get("taskRevision")):
            return {"ready": False}
        from .capacity import saved_policy, usage_snapshot, evaluate
        if not evaluate(saved_policy(self.service, sid), usage_snapshot(source))["allowed"]:
            return {"ready": False}
        return {"ready": self.pending_continuation(sid) is not None}

    def claim_matches(self, request, receipt):
        wait = request.get('subscription') or {}
        revision = receipt.get('dependencyOutcomeRevision')
        if wait.get('notificationVersion') == 2 and revision:
            identity = self.continuation_identity(wait, revision)
            snapshot = receipt.get('outcomeSnapshot') or {}
            # A staged report may later seal, but changed qualification of the
            # very same outcome cannot retain an earlier success claim.
            current = next((row for row in (request.get('response') or {}, request.get('terminalOutcome') or {})
                            if row.get('outcomeRevision') == revision), None)
            if current is not None and current.get('qualified') is not snapshot.get('qualified'):
                return False
            if request.get('outcomeRevision', revision) > revision:
                # A later meaningful outcome replaces the queued wake, including
                # an old question/failure. Full reports remain in their receipts.
                # Staged success does not advance outcomeRevision before sealing.
                return False
            return revision <= wait.get('lastClaimedRevision', 0) and identity == receipt.get('requestId')
        return wait.get('status') == 'claimed' and wait.get('continuationId') == receipt.get('requestId')

    def fair_rows(self, sid, *, active=False, accepted_after=None):
        # Pick the least recently served sender across the complete pending set,
        # using indexed scalar metadata. A long FIFO from one sender must not
        # hide another sender beyond the first receipt page. NULL senders (host
        # notifications) form their own group and receive the same fair turn.
        eligible = (" AND (json_extract(receipt,'$.mode')='auto' OR json_extract(receipt,'$.automaticDelivery')=1)"
                    if active else "")
        cutoff = ()
        if accepted_after is not None:
            eligible += (" AND coalesce(json_extract(receipt,'$.acceptedAt'),0)>?"
                         " AND json_extract(receipt,'$.dependencyRequestId') IS NULL")
            cutoff = (accepted_after,)
        index = 'coordination_automatic_sender' if active else 'coordination_queued_sender'
        sender = self.service.db.execute(f"""WITH pending AS (
            SELECT json_extract(receipt,'$.senderSessionId') AS sender, min(rowid) AS first_row
            FROM commands INDEXED BY {index}
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.delivery')='queued' {eligible}
            GROUP BY json_extract(receipt,'$.senderSessionId'))
            SELECT sender FROM pending ORDER BY coalesce((
                SELECT max(json_extract(receipt,'$.admissionOrder'))
                FROM commands INDEXED BY coordination_sender_admission
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.senderSessionId') IS pending.sender
                AND json_extract(receipt,'$.admissionOrder') IS NOT NULL),0), first_row LIMIT 1""",
            (sid, *cutoff, sid)).fetchone()
        if sender is None:
            return []
        return self.service.db.execute(f"""SELECT id,receipt FROM commands INDEXED BY {index}
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.senderSessionId') IS ?
            AND json_extract(receipt,'$.delivery')='queued' {eligible} ORDER BY rowid LIMIT 64""",
            (sid, sender[0], *cutoff)).fetchall()

    def refresh_inboxes(self):
        """Reconsider saved work after a relaxed inbox policy, never replay it."""
        recipients = self.service.db.execute("""SELECT json_extract(receipt,'$.target.sessionId')
            FROM commands WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.delivery')='queued'
            UNION SELECT json_extract(receipt,'$.senderSessionId') FROM commands
            WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','coordination.create','coordination.watch','worktree.handoff','updates.app','configuration.apply')
            AND json_extract(receipt,'$.subscription.status')='backpressured'""").fetchall()
        for (sid,) in recipients:
            if sid:
                self.service._task(self.drain(sid))

    def resume_outcomes(self, sid):
        for (encoded,) in self.service.db.execute("""SELECT receipt FROM commands
            WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','coordination.create','coordination.watch','worktree.handoff','updates.app','configuration.apply')
            AND json_extract(receipt,'$.senderSessionId')=?
            AND json_extract(receipt,'$.subscription.status')='backpressured' LIMIT 64""", (sid,)).fetchall():
            self.enqueue_continuation(json.loads(encoded))

    async def drain(self, sid):
        if sid in self.draining:
            self.redrain.add(sid)  # Retain a lifecycle edge that beats its admission acknowledgement.
            return
        self.draining.add(sid)
        freed_return = False
        try:
            async with self.service.lock:
                from .configuration_recovery import suppress_input
                current = self.service._session(sid)
                holding = suppress_input(current)
                if not holding:
                    self.resume_outcomes(sid)
                rows = self.fair_rows(sid, active=current.get('status') in BUSY,
                    accepted_after=current['pendingConfiguration']['recoveryNoInput']['acceptedAt'] if holding else None)
            for identity, encoded in rows:
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    if receipt.get("delivery") != "queued":
                        self.cancel_expiry(identity)
                        continue
                    target = self.service._session(sid)
                    if suppress_input(target, receipt):
                        continue  # Retain older input; fresh explicit work may run.
                    from .update_drain import held, owner_waiting
                    if held(self.service) or owner_waiting(self.service, sid):
                        break
                    if target.get('fileAccessPending'):
                        break  # Keep queued input until the existing refresh owner retires the old mount.
                    reason = self.guard(receipt)
                    if reason:
                        receipt.update(delivery='expired' if receipt.get('expiresAt', float('inf')) <= time.time() else 'suppressed', detail=reason)
                        self.observe_admission(receipt)
                        self.publish_receipt(receipt)
                        self.cancel_expiry(identity)
                        freed_return = freed_return or bool(receipt.get('dependencyRequestId'))
                        continue
                    if (target.get("status") in BUSY and not automatic_delivery(receipt) or target.get('configurationBusy')
                            or target.get('pendingConfiguration', {}).get('phase') in {'queued', 'applying'}
                            or target.get('pendingConfiguration', {}).get('recoveryRequired')
                            or target.get('pendingConfiguration', {}).get('phase') == 'error'
                            and target.get('pendingConfiguration', {}).get('effect', 'none') != 'none'):
                        break
                    # Durable claim precedes any runtime effect. Unknown never
                    # returns to queued, including cancellation and restart.
                    receipt['admissionOrder'] = self.service.db.execute("""SELECT coalesce(max(json_extract(receipt,'$.admissionOrder')),0)+1
                        FROM commands WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                        AND json_extract(receipt,'$.target.sessionId')=?""", (sid,)).fetchone()[0]
                    receipt['admittedAt'] = time.time()
                    receipt["delivery"] = "submitting"
                    # This field describes only the newly claimed attempt. A
                    # previous safe wait is not absence proof for this control.
                    receipt.pop("admission", None)
                    self.save(identity, receipt)
                    self.service.db.commit()
                    self.cancel_expiry(identity)
                    snapshot = copy.deepcopy(target)
                arguments = {"inputId": identity, **intake_task_binding(receipt)}
                if automatic_delivery(receipt):
                    arguments['automaticDelivery'] = True
                try:
                    result = await self.service.runtime.collaboration_input(
                        snapshot, arguments, lambda: self.guard(self.receipt(identity)), self.service.on_runtime_event)
                    phase = "accepted" if result.get("accepted") else (
                        "queued" if result.get('effect') == 'none' and (result.get('waitingForUpdate') is True
                        or automatic_delivery(receipt) and result.get('waitingForBoundary') is True) else
                        "not_sent" if result.get("delivery") == "not_sent" else "suppressed")
                except BaseException as exc:
                    result, phase = {"reason": "Admission outcome unknown; no replay."}, "unknown"
                    if isinstance(exc, asyncio.CancelledError):
                        from .runtime import RuntimePreparationCancelled
                        if isinstance(exc, RuntimePreparationCancelled):
                            result = {"accepted": False, "delivery": "not_sent",
                                      "reason": "Preparation cancelled before input admission."}
                            phase = "not_sent"
                        async with self.service.lock:
                            # Cancellation, replies and subscriptions can commit
                            # while transport is awaited. Reconcile only admission
                            # against the durable owner, never the pre-await copy.
                            receipt = self.receipt(identity)
                            phase, result = admission_observation(receipt, phase, result)
                            receipt.update(delivery=phase, detail=result.get("reason"), admission=result)
                            self.observe_admission(receipt)
                            self.service.db.commit()
                        raise
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    phase, result = admission_observation(receipt, phase, result)
                    receipt.update(delivery=phase, admission=result)
                    if automatic_delivery(receipt) and result.get('accepted'):
                        receipt['admissionMode'] = result.get('mode')
                        if result.get('generationId'):
                            receipt['targetGenerationId'] = result['generationId']
                    self.observe_admission(receipt)
                    self.publish_receipt(receipt)
                    if phase == 'queued':
                        self.expire_later(receipt)
                # Native generation events own status. Do not send a second
                # input merely because its acknowledgement arrived first.
                if phase in {"accepted", "applied", "held", "unknown", "queued"}:
                    break
            else:
                if rows:
                    # This bounded batch contained only suppressed/expired or
                    # confirmed unsubmitted inputs. Continue without requiring
                    # an unrelated runtime event; accepted/unknown never retry.
                    self.service._task(self.drain(sid))
        finally:
            self.draining.discard(sid)
            if sid in self.redrain:
                self.redrain.discard(sid)
                self.service._task(self.drain(sid))
        if freed_return:
            # The newest outcome may have been backpressured by the old queued
            # return. Reuse the released slot now; no unrelated event is needed.
            async with self.service.lock:
                if not suppress_input(self.service._session(sid)):
                    self.resume_outcomes(sid)

    def admission(self, sid, args):
        receipt = self.receipt(args["inputId"])
        from .configuration_recovery import suppress_input
        if suppress_input(self.service._session(sid), receipt):
            return {'admitted': False, 'reason': 'Operator configuration recovery did not authorize conversation input.'}
        binding = intake_task_binding(receipt)
        if (receipt.get("commandAction") != "coordination.send" or receipt.get("delivery") != "submitting"
                or receipt.get("protocol") != PROTOCOL or receipt["target"]["sessionId"] != sid
                or args.get("taskId") != binding.get("taskId") or args.get("taskRevision") != binding.get("taskRevision")
                or ('taskContinuationEpoch' in args) != ('taskContinuationEpoch' in binding)
                or args.get('taskContinuationEpoch') != binding.get('taskContinuationEpoch')):
            return {"admitted": False, "reason": "The exact host request is no longer submitting."}
        if (args.get('automaticDelivery') is True) != automatic_delivery(receipt):
            return {"admitted": False, "reason": "The saved input delivery policy changed."}
        if receipt.get("mode") == "steer" and args.get("targetGenerationId") != receipt.get("targetGenerationId"):
            return {"admitted": False, "reason": "The steering generation anchor changed."}
        from .update_drain import held, owner_waiting
        if (held(self.service) or owner_waiting(self.service, sid)) and receipt.get('mode') != 'steer':
            return {'admitted': False, 'waitingForUpdate': True, 'reason': 'Waiting for the app update.'}
        reason = self.guard(receipt)
        message = next((row for row in self.service._session(sid).get("messages", [])
                        if row.get("inputId") == args["inputId"] and row.get("inputOrigin") == "peer"), None)
        if message is None:
            return {"admitted": False, "reason": "The saved host-attributed input is unavailable."}
        from .peer_resume import pending
        from .completed_followup import matches_request
        task = self.service._session(sid).get("task") or self.service.coordination.task(sid)
        active = self.service._session(sid).get('collaborationGeneration') or {}
        return {"admitted": not reason, "reason": reason, "message": copy.deepcopy(message),
                "currentGenerationId": active.get('id') if not active.get('terminal') else None,
                "humanResume": not reason and pending(receipt),
                "blockedTaskInput": not reason and self.blocked_input(receipt, task),
                "completedTaskInput": not reason and matches_request(self.service, receipt, task)}

    async def submit_steer(self, identity):
        async with self.service.lock:
            receipt = self.receipt(identity)
            if receipt.get("delivery") != "pending_steer":
                return receipt
            reason = self.guard(receipt)
            if reason:
                receipt.update(delivery="suppressed", detail=reason)
                self.save(identity, receipt)
                self.publish_receipt(receipt)
                return receipt
            receipt["delivery"] = "submitting"
            self.save(identity, receipt)
            self.service.db.commit()
            target = copy.deepcopy(self.service._session(receipt["target"]["sessionId"]))
        args = {key: receipt.get(key) for key in ("inputId", "grantId", "taskId", "taskRevision", "targetGenerationId")}
        try:
            result = await self.service.runtime.collaboration_steer(
                target, args, lambda: self.guard(self.receipt(identity)), self.service.on_runtime_event)
            phase = "accepted" if result.get("accepted") else "suppressed"
        except BaseException as exc:
            result, phase = {"reason": "Steering outcome unknown; no replay."}, "unknown"
            if isinstance(exc, asyncio.CancelledError):
                async with self.service.lock:
                    receipt = self.receipt(identity)
                    # Native applied/held evidence can beat the acknowledgement.
                    phase, result = admission_observation(receipt, phase, result)
                    receipt.update(delivery=phase, admission=result)
                    if phase == 'not_sent':
                        self.observe_admission(receipt)
                    else:
                        self.save(identity, receipt)
                    self.service.db.commit()
                raise
        async with self.service.lock:
            receipt = self.receipt(identity)
            # Applied/held events can precede the transport acknowledgement.
            phase, result = admission_observation(receipt, phase, result)
            receipt.update(delivery=phase, admission=result)
            if phase == 'not_sent':
                self.observe_admission(receipt)
            else:
                self.save(identity, receipt)
            self.publish_receipt(receipt)
            return receipt

    def reconcile_admission(self, session, payload):
        """Settle a timed-out control from its original worker reply, without replay."""
        identity = payload.get('inputId')
        try:
            receipt = self.receipt(identity)
        except Exception:
            return set()
        result = payload.get('peerAdmission')
        if (not isinstance(result, dict) or receipt.get('commandAction') != 'coordination.send'
                or receipt.get('protocol') != PROTOCOL or receipt.get('inputId') != identity
                or receipt.get('target', {}).get('sessionId') != session['id']
                or receipt.get('delivery') not in {'submitting', 'unknown'}
                or (receipt.get('steering') or {}).get('event') == 'steering.unknown'
                or payload.get('interruptionRevision') != receipt.get('interruptionRevision', 0)
                or payload.get('interruptionRevision') != session.get('interruptionRevision', 0)
                or result.get('inputId', identity) != identity):
            return set()
        anchor = receipt.get('targetGenerationId')
        if payload.get('targetGenerationId') and payload['targetGenerationId'] != anchor:
            return set()
        if result.get('accepted') is True:
            if result.get('mode') == 'steer' or receipt.get('mode') == 'steer':
                generation = result.get('generationId') or payload.get('targetGenerationId')
                if not generation or (anchor or (session.get('collaborationGeneration') or {}).get('id')) != generation:
                    return set()
                receipt.update(targetGenerationId=generation, admissionMode='steer')
            elif automatic_delivery(receipt) and result.get('mode') != 'queue':
                return set()  # Auto must identify which native admission route succeeded.
            receipt.update(delivery='accepted', admission=copy.deepcopy(result))
            self.reconcile_unknown_outcome(receipt)
            self.save(identity, receipt)
        elif result.get('accepted') is False and result.get('effect') == 'none':
            if receipt['delivery'] == 'submitting':
                # The normal caller still owns wait/suppression disposition.
                # Keep absence evidence if cancellation wins before it resumes.
                receipt['admission'] = copy.deepcopy(result)
                self.save(identity, receipt)
                return set()
            # Positive absence settles the uncertainty, but is not permission to
            # retarget a historical request or retry its original control.
            receipt.update(delivery='not_sent', admission=copy.deepcopy(result))
            self.observe_admission(receipt)
        else:
            return set()
        return {receipt['senderSessionId'], session['id']}

    @staticmethod
    def reconcile_unknown_outcome(receipt):
        outcome = receipt.get('terminalOutcome') or {}
        if (outcome.get('status') == 'observed' and outcome.get('kind') == 'admission'
                and outcome.get('outcome') == 'unknown'):
            # Keep the old observation, but it no longer describes current
            # admission. Positive admission is still not successful task work.
            outcome.update(status='reconciled', reconciledAt=time.time(), delivery=receipt['delivery'])

    def observe(self, session, kind, payload):
        """Host event linkage, never prose classification or semantic success."""
        changed = self.watches.observe(session, kind, payload)
        if kind in {'runtime.status', 'runtime.ended', 'runtime.error'} and not payload.get('activityOnly'):
            from .navigation_waiting import affected_sources
            changed.update(affected_sources(self.service.db, session['id']))
        if kind == 'runtime.delivery' and 'peerAdmission' in payload:
            return changed | self.reconcile_admission(session, payload)
        if (kind == 'runtime.ended' and not payload.get('backgroundOnly')
                and payload.get('sessionId') is not None
                and payload.get('sessionId') in {session['id'], session.get('runtimeSessionId')}
                and payload.get('rootSessionId', session['id']) == session['id']):
            # Confirmed process exit ends routing authority even when the worker
            # could not flush its terminal generation event. It proves neither
            # successful completion nor a staged reply: keep those outcomes
            # unqualified and never replay any previously accepted input.
            active = session.get('collaborationGeneration') or {}
            # Passive presentation fence only. Configuration replacement can
            # retain a terminal generation/task but not its old live wait view.
            if active.get('id'):
                session['_navigationEndedGeneration'] = active['id']
            if active and not active.get('terminal'):
                active.update(terminal=True, completed=False,
                              detail='Worker exited before terminal generation evidence was received.')
            return changed
        if kind == "runtime.collaboration_checkpoint":
            active = session.get("collaborationGeneration") or {}
            if (payload.get("sessionId") == session["id"] and payload.get("rootSessionId") == session["id"]
                    and active.get("id") == payload.get("generation_id") and not active.get("terminal")):
                previous = {row["messageId"]: row for row in session.get("collaborationMessageAnchors", [])}
                for anchor in payload.get("messageAnchors", [])[:64]:
                    if anchor.get("generationId") == active["id"]:
                        previous[anchor["messageId"]] = anchor
                session["collaborationMessageAnchors"] = list(previous.values())[-128:]
            return changed
        if kind == "runtime.status" and payload.get("event") == "input.delivered":
            active = session.get("collaborationGeneration") or {}
            if active and not active.get("terminal") and payload.get("inputId"):
                # Native membership is authority, not a display-history window.
                inputs = active.setdefault("inputIds", [])
                if payload["inputId"] not in inputs:
                    inputs.append(payload["inputId"])
        if kind == "runtime.steering" and payload.get("input_id"):
            try:
                receipt = self.receipt(payload["input_id"])
            except Exception:
                return changed
            automatic = automatic_delivery(receipt)
            if (receipt.get("mode") != "steer" and not automatic
                    or receipt.get("target", {}).get("sessionId") != session["id"]):
                return changed
            target_generation = payload.get('target_generation_id')
            if receipt.get('interruptionRevision', 0) != session.get('interruptionRevision', 0):
                return changed
            if automatic and not receipt.get('targetGenerationId'):
                active = session.get('collaborationGeneration') or {}
                if (receipt.get('delivery') not in {'submitting', 'accepted', 'unknown'}
                        or not target_generation or active.get('id') != target_generation):
                    return changed
                # The native steering event, never a caller-supplied generation,
                # binds the single accepted input to its selected generation.
                receipt.update(targetGenerationId=target_generation, admissionMode='steer')
                self.save(receipt['requestId'], receipt)
            if receipt.get('targetGenerationId') != target_generation:
                return changed
            phases = {"steering.accepted": "accepted", "steering.applied": "applied",
                      "steering.held": "held", "steering.unknown": "unknown"}
            if payload.get("event") in phases:
                receipt.update(delivery=phases[payload["event"]], steering=copy.deepcopy(payload))
                if receipt["delivery"] != "unknown":
                    self.reconcile_unknown_outcome(receipt)
                self.save(receipt["requestId"], receipt)
                changed.update({receipt["senderSessionId"], session["id"]})
            if payload.get("event") == "steering.applied":
                active = session.get("collaborationGeneration") or {}
                if active.get("id") == receipt["targetGenerationId"] and not active.get("terminal"):
                    active["inputIds"] = list(dict.fromkeys([*active.get("inputIds", []), receipt["inputId"]]))
                self.service._task(self.drain(session['id']))
        if (kind != "runtime.generation" or payload.get("rootSessionId", session["id"]) != session["id"]
                or payload.get("sessionId", session["id"]) != session["id"]):
            return changed
        if payload.get("event") == "generation.started":
            identity = payload.get('generation_id')
            active = session.get('collaborationGeneration') or {}
            if not isinstance(identity, str) or not identity or active.get('id') == identity:
                return changed  # Duplicate starts never reset inputs, terminal state or Stop authority.
            history = session.get('generations', [])
            # The service appends this observation before calling this reducer.
            # Earlier starts/terminals remain evidence, never fresh admission.
            if history and history[-1].get('generation_id') == identity and history[-1].get('event') == 'generation.started':
                history = history[:-1]
            if any(row.get('generation_id') == identity for row in history):
                return changed
            # Native input admission must capture the owner's durable Stop
            # revision. Arrival time cannot bless an event delayed across Stop.
            # Legacy missing epochs are usable only before the first Stop.
            interruption = payload.get('interruptionRevision', 0)
            if type(interruption) is not int or interruption != session.get('interruptionRevision', 0):
                return changed
            session["collaborationGeneration"] = {"id": identity, "inputIds": [], "terminal": False,
                'interruptionRevision': interruption}
            session['_collaborationHostInstanceId'] = self.service.instance_id
            self.service._task(self.drain(session['id']))
            return changed
        active = session.get("collaborationGeneration") or {}
        if active.get("id") != payload.get("generation_id") or active.get("terminal"):
            return changed
        active["terminal"] = True
        self.service._task(self.drain(session['id']))
        active["completed"] = (payload.get("event") == "generation.finished"
            and payload.get("disposition") == "manager_turn_finished"
            and not payload.get("active_job_ids"))
        # Only declarations staged in this actual generation can be sealed.
        for identity, encoded in self.service.db.execute("""SELECT id,receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.response.status')='staged'""", (session["id"],)).fetchall():
            receipt = json.loads(encoded)
            response = receipt["response"]
            if response["generationId"] != payload.get("generation_id"):
                continue
            valid = (payload.get("event") == "generation.finished"
                     and bool(set(response.get('admittedInputIds') or [identity]) & set(payload.get('input_ids', [])))
                     and bool(payload.get("text", "").strip())
                     and payload.get("disposition") == "manager_turn_finished"
                     and receipt.get("delivery") in {"accepted", "applied", "submitting"})
            from .errors import AppError
            try:
                linked = [self.resolve_message(session, mid) for mid in response.get("messageIds", [])]
            except AppError:
                # Explicit reads still fail loudly on conflicting identities.
                # In the event reader this is negative linkage evidence, not a
                # worker communication failure; reject via the normal path.
                linked = [None]
            valid = valid and all(m and m.get("role") == "assistant" and m.get("generationId") == response["generationId"] for m in linked)
            anchor = payload.get("nativeTerminal") or {}
            native_id = session.get("runtimeSessionId") or session.get("nativeIdentity") or session["id"]
            from .automatic_history import display_identity
            valid = (valid and anchor.get("rootSessionId") == native_id
                     and anchor.get("generationId") == response["generationId"]
                     and type(anchor.get("nativeIndex")) is int and anchor["nativeIndex"] >= 0
                     and anchor.get("textDigest") == fingerprint(payload.get("text", ""))
                     and isinstance(anchor.get("nativeText"), str)
                     and anchor.get("messageId") == display_identity(session, anchor["nativeIndex"], "assistant", anchor["nativeText"]))
            if valid:
                # This ID names the exact checkpointed native row, not a prose
                # match or newly synthesized web assistant message.
                response.update(status="sealed", terminalMessageId=anchor["messageId"], nativeTerminal=anchor, sealedAt=time.time(),
                    qualified=(response["kind"] == "result" and response["outcome"] == "success"
                               and response.get('artifactVerification') != 'incomplete'
                               and bool(response.get("references") or response.get("messageIds"))),
                    independentArtifactVerification=False)
            else:
                response.update(status="rejected", qualified=False, detail="Terminal evidence or exact linkage failed.")
            if response.get('status') == 'rejected':
                response.pop('outcomeRevision', None)
            if response_ready(response) and payload.get('event') != 'generation.failed':
                self.record_outcome(receipt, response)
            self.save_response(receipt)
            self.save(identity, receipt)
            changed.update({receipt["senderSessionId"], session["id"]})
            if response_ready(response) and payload.get('event') != 'generation.failed' and receipt.get("subscription"):
                self.enqueue_continuation(receipt)
        if payload.get('event') == 'generation.failed':
            # A host-observed failure is useful even if the model never managed
            # to declare a reply. It supplies no successful artifact evidence.
            delivered = set(payload.get('input_ids', [])) & set(active.get('inputIds', []))
            for identity, encoded in self.service.db.execute("""SELECT id,receipt FROM commands
                    WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                    AND json_extract(receipt,'$.target.sessionId')=?
                    AND json_extract(receipt,'$.protocol')=?""", (session['id'], PROTOCOL)).fetchall():
                if identity not in delivered:
                    continue
                receipt = json.loads(encoded)
                if receipt.get('delivery') not in {'accepted', 'applied', 'submitting'}:
                    continue
                receipt['terminalOutcome'] = {'status': 'observed', 'kind': 'result', 'outcome': 'failed',
                    'qualified': False, 'requestId': identity, 'generationId': active['id'],
                    'recipientSessionId': session['id'], 'observedAt': time.time(),
                    'text': 'The recipient runtime reported a failed generation for this delivered request. Inspect its retained history before deciding whether to retry.'}
                self.record_outcome(receipt, receipt['terminalOutcome'])
                self.save(identity, receipt)
                changed.update({receipt['senderSessionId'], session['id']})
                if receipt.get('subscription'):
                    self.enqueue_continuation(receipt)
        return changed

    def committed(self, identity):
        # Only the failure path needs a second connection: reading our own
        # transaction cannot establish whether a lost acknowledgement committed.
        import sqlite3
        path = self.service.db.execute('PRAGMA database_list').fetchone()[2]
        with sqlite3.connect(path) as database:
            return database.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone() is not None

    @contextmanager
    def return_send_transaction(self, identity, source, target, *, notify=False):
        """Return-enabled sends and passive notifications own one commit.

        Creation-owned sends retain their existing transaction owner. Under the
        service lock a passive notification never awaits an adapter: snapshot
        only its append and metadata, preserving unrelated cold bodies. Queue
        sends keep their full affected-root rollback and post-commit scheduling.
        """
        sessions = {row['id']: row for row in (source, target)}
        if notify:
            messages = target['messages']
            length = len(messages)
            fields = ('historyManaged', 'coordinationReference', 'recentActivityAt',
                      'navigationActivityAt', 'navigationActivityPending', 'navigationPostAdmissions')
            snapshots = {sid: {key: copy.deepcopy(row[key]) for key in fields if key in row}
                         for sid, row in sessions.items()}
        else:
            snapshots = {sid: copy.deepcopy(row) for sid, row in sessions.items()}
        self.service.db.commit()  # Preserve unrelated pending receipt work.
        self.service.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            if not self.committed(identity):
                self.service.db.rollback()
                if notify:
                    del messages[length:]
                    for sid, row in sessions.items():
                        for key in fields:
                            if key in snapshots[sid]:
                                row[key] = snapshots[sid][key]
                            else:
                                row.pop(key, None)
                else:
                    for sid, row in sessions.items():
                        row.clear()
                        row.update(snapshots[sid])
                self.service.projections.invalidate(state=self.service.state, session_ids=set(sessions))
            elif not notify:
                # A lost publication acknowledgement cannot abandon committed
                # work or depend on the caller retrying to start the queue.
                retained = self.receipt(identity)
                self.expire_later(retained)
                self.service._task(self.drain(target['id']))
            raise

    def send_result(self, receipt):
        from .coordination_return_state import project
        return {**receipt, 'returnState': project(self, receipt)}

    def continuation_identity(self, wait, revision):
        return wait['continuationId'] if revision == 1 else str(uuid.uuid5(
            uuid.NAMESPACE_URL, wait['continuationId'] + ':' + str(revision)))

    def enqueue_continuation(self, request):
        """Commit one guarded input; live callers hold the service writer lock.

        Synchronous startup also uses this before requests are served. Never
        enter from an unlocked background task: commit affects other savepoints
        on the shared connection, including an awaited retained file write.
        """
        wait = request['subscription']
        versioned = wait.get('notificationVersion') == 2
        # Recover only receipts identifiable as these retired implementation
        # limits, not arbitrary limits, cancellation or uncertain delivery.
        retired_limit = (versioned and wait.get('status') == 'limit_reached' and (
            wait.get('detail') == RETIRED_HOP_LIMIT or (
                wait.get('detail') == RETIRED_NOTIFICATION_LIMIT
                and type(request.get('outcomeRevision')) is int
                and request['outcomeRevision'] > 32)))
        if request.get('protocol') != PROTOCOL or (not retired_limit and wait.get('status') not in (
                {'waiting', 'claimed', 'backpressured'} if versioned else {'waiting'})):
            return
        response = continuation_outcome(request)
        if response is None:
            return
        revision = self.record_outcome(request, response)
        from .outcome_acknowledgement import acknowledged
        if versioned and acknowledged(request, response):
            return
        if versioned and revision <= wait.get('lastClaimedRevision', 0):
            return
        identity = self.continuation_identity(wait, revision) if versioned else wait['continuationId']
        if self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
            return
        sender = self.service._session(request['senderSessionId'])
        from .configuration_recovery import suppress_input
        if suppress_input(sender):
            # The result remains retained, including backpressured settings
            # waits. A newly materialized return is not fresh explicit work.
            self.save(request['requestId'], request)
            self.service.db.commit()
            return
        if (self.service.assignments.blocked(sender, request.get('assignmentId'))
                or self.service.assignments.request_membership_reason(request)
                or wait.get('interruptionRevision') != sender.get('interruptionRevision', 0)):
            wait.update(status='suppressed', open=False, detail='The saved wait was interrupted; late evidence remains inspectable.')
            self.save(request['requestId'], request)
            self.service.db.commit()
            return
        producer = outcome_sender(request)
        depth = max(request.get('causalDepth', 0), response.get('causalDepth', 0))+1
        # A report remains durable even if its independent notification cannot
        # commit. The payload and claim below are one transaction.
        self.save(request['requestId'], request)
        self.service.db.commit()
        prior_wait = copy.deepcopy(wait)
        prior_messages = list(sender.get('messages', []))
        prior_history = sender.get('historyManaged')
        try:
            if versioned:
                from .errors import AppError
                # Temporarily release this wait's reserved slot before exchanging it
                # for a queued notification. Preserve a slot for further interim reports.
                wait['open'] = response.get('outcome') in {'deferred', 'question', 'conflict'}
                wait['status'] = 'claiming'
                self.save(request['requestId'], request)
                try:
                    self.require_inbox_slot(sender['id'], producer, request.get('assignmentId'))
                except AppError as exc:
                    wait.update(status='backpressured', open=True, detail=str(exc))
                    self.save(request['requestId'], request)
                    self.service.db.commit()
                    return
            producer_session = self.service._session(producer, hydrate=False)
            # Return direction is B -> A, not the original request's A -> B.
            # Freeze from the original request, not newly moved participants.
            locations_match = (participant_matches(request, sender) and participant_matches(request, producer_session)
                if request.get('commandAction') == 'coordination.send'
                else sender['workspace'] == producer_session['workspace'] == request['workspace'])
            if not locations_match:
                wait.update(status='suppressed', open=False, detail='The request participant location changed; no return was sent.')
                self.save(request['requestId'], request)
                self.service.db.commit()
                return
            sender['historyManaged'] = False
            envelope = {"senderSessionId": producer, "recipientSessionId": sender["id"],
                        "senderLocation": location(producer_session), "recipientLocation": location(sender),
                        "sender": descriptor(producer_session), "recipient": descriptor(sender),
                        "requestId": identity, "inputId": identity, "replyToRequestId": request["requestId"],
                        "mode": "auto", "purpose": "Resume the saved request-specific wait",
                        "references": ([response['terminalMessageId']] if response.get('terminalMessageId') else []) + response.get('references', [])[:15]}
            task = sender.get('task') or self.service.coordination.task(sender['id'])
            if dependency_blocked(task) and task_matches(task, wait):
                envelope['blockedTaskHandling'] = blocked_task_handling(task)
            if response.get("qualified"):
                text = "A recipient declared a successful result and its root generation terminated. Independently check the referenced artifact before using it.\n"
            elif request.get('commandAction') == 'coordination.create':
                text = 'The host reported a preparation outcome for your commissioned task. This does not authorize replaying uncertain effects.\n'
            elif request.get('commandAction') == 'configuration.apply':
                text = 'The host settled the settings wait for your existing task.\n'
            elif response.get('status') == 'observed':
                text = f"The host observed {response['outcome']} for the saved request. This does not satisfy the dependency or authorize a retry.\n"
            else:
                text = (f"A recipient reported {response['outcome']} for the saved request. "
                        "This does not satisfy the dependency or authorize a retry. Review the report and decide how to proceed within the original task.\n")
            if response.get('status') in {'rejected', 'unknown'}:
                text = f"The request report has {response['status']} terminal evidence. This does not satisfy the dependency or authorize replay.\n"
            text += response["text"]
            text += ('\nThis is a retained result for the same objective, not new execution authority. '
                     'Apply the current task constraints and permissions; do not repeat completed or uncertain effects.')
            message = self.service._message(sender, "user", text, "peer", inputId=identity, inputOrigin="peer",
                                            peerEnvelope=envelope)
            receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity, "inputId": identity,
                       "messageId": message["id"], "senderSessionId": producer,
                       "sender": descriptor(producer_session),
                       "endpointBindingVersion": 1,
                       "target": {**descriptor(sender), "workspace": sender['workspace']},
                       "senderLocation": location(producer_session), "recipientLocation": location(sender),
                       "protocol": PROTOCOL, "workspace": producer_session["workspace"],
                       "mode": "auto", "delivery": "queued", "interruptionRevision": wait["interruptionRevision"],
                       "taskId": wait["taskId"], "taskRevision": wait["taskRevision"], "dependencyRequestId": request["requestId"],
                       **({'taskContinuationEpoch': wait['taskContinuationEpoch']}
                          if 'taskContinuationEpoch' in wait else {}),
                       "dependencySatisfied": response.get("qualified") is True, "continuationReason": response.get('notificationOutcome') or response["outcome"],
                       'dependencyOutcomeRevision': revision, 'outcomeSnapshot': {key: copy.deepcopy(response[key]) for key in ('outcomeRevision', 'responseRevision', 'operationId', 'status', 'outcome', 'qualified', 'text') if key in response},
                       'expiresAt': time.time()+DEFAULT_QUEUE_SECONDS, 'causalDepth': depth,
                       "origin": "system", "displayBinding": fingerprint([envelope, text])}
            receipt['assignmentId'] = request.get('assignmentId')
            wait.update(status='claimed', lastClaimedRevision=revision,
                        open=versioned and response.get('outcome') in {'deferred', 'question', 'conflict'})
            wait.pop('detail', None)
            self.insert(identity, fingerprint(["continuation", request["requestId"], wait]), receipt)
            self.save(request["requestId"], request)
            self.service._publish_changes(sessions={sender['id'], producer})  # Payload and claim commit together.
            self.expire_later(receipt)
            self.service._task(self.drain(sender["id"]))
        except BaseException:
            if not self.committed(identity):
                self.service.db.rollback()
                sender['messages'] = prior_messages
                if prior_history is None:
                    sender.pop('historyManaged', None)
                else:
                    sender['historyManaged'] = prior_history
                wait.clear()
                wait.update(prior_wait)
                self.refresh_reference(request)
            else:
                # Lost response after durable commit: the known queue still
                # belongs to the service and must not wait for caller retry.
                retained = self.receipt(identity)
                self.expire_later(retained)
                self.service._task(self.drain(sender['id']))
            raise

    def start(self):
        """One recovery pass over known unsubmitted queues, not a scheduler."""
        self.watches.start()
        # Reconsider only saved waits stopped by retired implementation limits.
        # Normal outcome idempotency and task/Stop admission guards still apply.
        for (encoded,) in self.service.db.execute('''SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction') IN
                    ('coordination.send','coordination.watch','worktree.handoff','workspace.tests.submit')
                AND json_extract(receipt,'$.subscription.status')='limit_reached'
                AND json_extract(receipt,'$.subscription.notificationVersion')=2
                AND json_extract(receipt,'$.subscription.detail') IN (?,?)''',
                (RETIRED_HOP_LIMIT, RETIRED_NOTIFICATION_LIMIT)).fetchall():
            self.enqueue_continuation(json.loads(encoded))
        for (encoded,) in self.service.db.execute('''SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction') IN ('coordination.send','updates.app','configuration.apply')
                AND json_extract(receipt,'$.subscription.status') IN ('waiting','claimed','backpressured')
                AND (coalesce(json_extract(receipt,'$.outcomeRevision'),0)>coalesce(json_extract(receipt,'$.subscription.lastClaimedRevision'),0)
                    OR json_extract(receipt,'$.subscription.status')='waiting') ''').fetchall():
            self.enqueue_continuation(json.loads(encoded))
        for (encoded,) in self.service.db.execute('''SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.create'
                AND json_extract(receipt,'$.creationVersion')=1
                AND json_extract(receipt,'$.delivery') IN ('creation_pending','created_initial_pending')'''):
            self.service._task(self.finish_create(json.loads(encoded)['requestId']))
        targets = self.service.db.execute("""SELECT DISTINCT json_extract(receipt,'$.target.sessionId')
            FROM commands WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.delivery')='queued'""").fetchall()
        for (sid,) in targets:
            self.service._task(self.drain(sid))
        for (encoded,) in self.service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.delivery')='queued'
                AND json_extract(receipt,'$.expiresAt') IS NOT NULL""").fetchall():
            self.expire_later(json.loads(encoded))

    async def route(self, action, args, origin, command_id, caller):
        """One gate for equivalent old/new host entry points."""
        if self.service.handoffs.execution(action, args, origin, command_id, caller) is not None:
            return None
        if action == "conversation.send" and (args.get("grantId") or (
                origin not in {"ui", "user", "scheduler"} and args.get("sessionId") != caller)):
            values = {key: value for key, value in args.items() if key != "preserveDraft"}
            if origin in {"ui", "user"}:
                values.setdefault("senderSessionId", self.service.state.get("selectedSessionId"))
            elif args.get("senderSessionId") not in {None, caller}:
                self.error("A model cannot impersonate a different peer sender.")
            return await self.service.dispatch("coordination.send", values, origin, command_id,
                include_state=False, caller_session_id=caller)
        if origin in {"ui", "user", "scheduler"}:
            return None
        if action == "session.create":
            authorized = CREATION.get()
            if authorized and authorized[:2] == (caller, fingerprint(args)):
                self.source({}, origin, caller)
                return None
            self.error("Use coordination.create for a task inheriting this conversation's workspace and configuration; raw agent session.create is not authorized. For an independent chat in another folder that the user explicitly requested, use session.handoff.start directly. Use session.handoff.propose for an unapproved suggestion, or session.draft for editable setup. No conversation was created or work started.")
        from .service import SESSION_ID_ACTIONS, ACTION_POLICIES
        policy = ACTION_POLICIES.get(action, {})
        access = policy.get("access")
        if access in {"peer_read", "presentation", "domain_authoritative"}:
            return None
        target_key = policy.get("target") or ("sessionId" if "sessionId" in args else "id" if action in SESSION_ID_ACTIONS else None)
        target = args.get(target_key) if target_key else None
        if access == "naming":
            if "automatic" in args and not args.get("regenerate"):
                return None
            access = "caller_controlled"
        if access == "caller_controlled":
            if not caller:
                self.error("A user must explicitly authorize a calling conversation.")
            # Configuration effects use their existing owner, with Full and
            # the child's assigned binding. Other root controls stay strict.
            from .configuration_authority import requirement
            if action == "permissions.save" and "scope" not in args:
                self.error("Choose an explicit file-access scope: local, project, or global. No settings were changed.", 400)
            try:
                configuration = requirement(action, args) is not None
            except ValueError as exc:
                self.error(str(exc), 400)
            self.source({}, origin, caller, allow_descendant=configuration)
            target = target or caller
            if target_key and not args.get("configurationTarget"):
                args[target_key] = target
            elif not args.get("configurationTarget") and not action.startswith("session.") and action != "configuration.apply":
                args["sessionId"] = target
            if target == caller:
                return None
            if action == "conversation.send":
                self.error("A user must explicitly authorize a current collaboration grant.")
            self.error("A user must explicitly perform this peer mutation; collaboration does not grant stop, worker or settings authority.")
        if target and target != caller and not action.startswith("coordination."):
            self.error("A user must explicitly perform peer mutations outside a collaboration send grant.")
        return None

    async def dispatch(self, action, args, origin, command_id, caller, *, admission_check=None):
        if action in {'coordination.recap', 'coordination.diagnostics'}:
            from .collaboration_observations import read
            return {'accepted': True, 'result': await read(self, action, args, origin, caller)}
        if action.startswith('coordination.watch'):
            return await self.watches.dispatch(action, args, origin, command_id, caller)
        if action in {"coordination.grant", "coordination.decide", "coordination.revoke"}:
            self.error("Coordination approval APIs are retired. Historical records are read-only.", 410)
        source = (self.result_source(args, origin, caller) if action == 'coordination.artifact'
                  else self.source(args, origin, caller, hydrate=False) if action == 'coordination.artifact.save'
                  else self.source(args, origin, caller) if action not in {"coordination.result", "coordination.read"} else None)
        if action == 'coordination.acknowledge':
            from .outcome_acknowledgement import acknowledge
            return await acknowledge(self, source, args, origin, command_id)
        if action == 'coordination.cancel':
            from .request_cancellation import cancel
            return await cancel(self, source, args, origin, command_id)
        if action == 'coordination.artifact.save':
            from .result_artifact_save import save
            return {'accepted': True, 'result': await save(self, source, args, origin, caller, admission_check=admission_check)}
        if action == 'coordination.artifact':
            if source['id'] != args['sessionId']:
                self.error('Read a retained result from the calling conversation.')
            from .result_artifacts import read
            result = await read(self, source['id'], args)
            self.result_source(args, origin, caller)  # Stop or access changes during file I/O win.
            return {'accepted': True, 'result': result}
        if action in {'coordination.awareness', 'coordination.intent.read', 'coordination.intent'}:
            from .workspace_awareness import packet, intent_for, declare
            if action == 'coordination.intent':
                return await declare(self, source, args, origin, command_id)
            target = self.service._session(args['sessionId'], hydrate=False)
            if origin not in {'ui', 'user'} and target['workspace'] != source['workspace']:
                self.error('Awareness and shared declarations belong to the calling workspace.')
            if action == 'coordination.intent.read':
                return {'accepted': True, 'result': intent_for(self.service, target)}
            if origin not in {'ui', 'user'} and target['id'] != source['id']:
                self.error('Read workspace awareness from the calling conversation.')
            return {'accepted': True, 'result': packet(self.service, target['id'],
                principal=PRINCIPAL.get(), offset=args.get('offset', 0), limit=args.get('limit', 8))}
        if action == "coordination.read":
            from .owned_history import require, selected
            if origin not in {'ui','user'} and not caller:
                self.error('History reads require a registered calling conversation.')
            scope = selected(args, 'workspace' if caller else 'all')
            reader = caller or (self.service.state.get('selectedSessionId') if 'scope' in args else None) or args['sessionId']
            require(self.service,reader,args['sessionId'],scope)
            if args.get("messageId"):
                target = self.service._session(args["sessionId"])
                await self.service.history.ensure_loaded(target["id"])
                require(self.service,reader,target['id'],scope)
                message = self.resolve_message(target, args["messageId"])
                if not message:
                    self.error("The exact original message is unavailable.", 404)
                limit = args.get("textLimit", 4000)
                text = message.get("text", "")
                return {"accepted": True, "result": {"message": {
                    "id": message["id"], "sessionId": target["id"], "role": message.get("role"),
                    **{key: message[key] for key in ("nativeMessageId", "nativeIndex", "generationId")
                       if message.get(key) is not None},
                    "text": text[:limit], "truncated": len(text) > limit}}}
            from .history_query import query_history
            return {"accepted": True, "result": await query_history(self.service, {
                "action": "read", "session_id": args["sessionId"], "scope": scope,
                "offset": args.get("offset", 0), "limit": args.get("limit", 16), "text_limit": args.get("textLimit", 1000),
            }, caller or args["sessionId"])}
        if action == "coordination.context":
            if origin not in {"ui", "user"} and args["sessionId"] != caller:
                self.error("Current coordination context belongs to the caller.")
            result = self.current(args["sessionId"])
            source = self.service._session(args['sessionId'])
            result['humanInput'] = self.current_human_input(source)
            from .workspace_awareness import packet, observed_intent
            result['awareness'] = packet(self.service, source['id'], principal=PRINCIPAL.get())
            result['writeIntent'] = observed_intent(self.service, source)
            workspace = next((w for w in self.service.state['workspaces'] if w['path'] == source['workspace']), None)
            if workspace:
                from .workspace_resources import dispatch_reservation
                resources = await dispatch_reservation(self.service, 'workspace.resources.reservations',
                    {'workspaceId': workspace['id'], 'limit': 16}, origin, None, caller)
                # Explicit reads only: never load transcripts or duplicate this
                # inventory in per-session persistent guidance.
                result['resources'] = {**resources['result'], 'workspaceId': workspace['id']}
            return {"accepted": True, "result": result}
        if action == "coordination.result":
            receipt = self.receipt(args["requestId"])
            if receipt.get("commandAction") not in {"coordination.send", "coordination.create"}:
                self.error("This receipt is not a peer request.", 409)
            sid = receipt.get("target", {}).get("sessionId") or receipt["sessionId"]
            self.require_result_reader(receipt, origin, caller, scope=args.get('scope', 'workspace'))
            target = next((row for row in self.service.state["sessions"] if row["id"] == sid), None)
            initial = None
            if receipt.get('commandAction') == 'coordination.create' and receipt.get('initialInputId'):
                row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (receipt['initialInputId'],)).fetchone()
                candidate = json.loads(row[0]) if row else {}
                if (candidate.get('commandAction') == 'coordination.send'
                        and candidate.get('senderSessionId') == receipt['senderSessionId']
                        and candidate.get('target', {}).get('sessionId') == sid):
                    self.require_result_reader(candidate, origin, caller, scope=args.get('scope', 'workspace'))
                    initial = candidate
            results = []
            report = initial or receipt
            revisions = [json.loads(row[0])['result'] for row in self.service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.reply'
                AND json_extract(receipt,'$.result.requestId')=? ORDER BY rowid DESC LIMIT 32""", (report['requestId'],)).fetchall()]
            response = report.get("response") or {}
            if response.get("qualified") and response.get("status") == "sealed":
                results.append({"messageId": response["terminalMessageId"], "sessionId": sid,
                                "inputId": (initial or receipt)['requestId'], "declaration": response})
            from .request_cancellation import status as cancellation_status
            from .outcome_acknowledgement import reference as notification_reference
            from .coordination_return_state import project as return_state
            return {"accepted": True, "result": {"receipt": receipt, "results": results,
                'returnState': return_state(self, report),
                'cancellation': cancellation_status(report),
                'notification': notification_reference(report),
                'responseRevision': report.get('responseRevision', 1 if response else 0), 'revisions': revisions,
                **({'initialRequest': initial} if initial is not None else {}),
                "preparation": copy.deepcopy((target or {}).get("activity")),
                "qualified": bool(results), "qualificationSupported": True,
                "detail": "Typed agent declaration plus host terminal evidence; not independently verified correctness."}}
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source["id"]])
        if action == "coordination.send":
            async with self.service.lock:
                duplicate = self.duplicate(identity, digest)
                if duplicate:
                    return self.send_result(duplicate)
            # Refuse unsupported return modes before hydration or fresh effects.
            if args.get('returnNotification') is True and args.get('mode', 'auto') not in {'queue', 'auto'}:
                self.error('returnNotification:true requires auto or queue mode; no input or wait was created.', 400)
            if args.get('mode') == 'notify':
                await self.service.history.ensure_loaded(args['sessionId'], hydrate=False)
            else:
                await self.service.history.ensure_loaded(args["sessionId"])
        prepared_artifacts = None
        if action == 'coordination.reply':
            from .result_artifacts import prepare
            prepared_artifacts = await prepare(self, source, args, origin, identity, digest)
            if prepared_artifacts.get('duplicate'):
                return prepared_artifacts['duplicate']
        async with ((prepared_artifacts['items'] if prepared_artifacts else AsyncExitStack()),
                    self.service.lock,
                    self.service.work_invocation.initial_transaction(action, identity),
                    AsyncExitStack() as recovery_boundary):
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                # Re-reading a prior receipt never dispatches work.
                return self.send_result(duplicate) if action == 'coordination.send' else duplicate
            from .workspace_work_effects import guard
            guard(self.service, action, args, origin, source['id'], final=True)
            if args.get("grantId"):
                self.error("Legacy grant-bearing writes are unsupported. Read the original receipt; do not replay it as new work.", 410)
            creation = CREATION.get()
            if (creation and action == "coordination.send" and creation[0] == source["id"]
                    and identity == creation[4]["requestId"] + ":brief"
                    and args["sessionId"] == str(uuid.uuid5(uuid.NAMESPACE_URL,
                        "collaborative-task:" + source["id"] + ":" + creation[4]["requestId"]))):
                lineage = {name: creation[4][name] for name in
                           ("sourceGenerationId", "sourceInputIds", "sourceInterruptionRevision")}
                if source.get("interruptionRevision", 0) != lineage["sourceInterruptionRevision"]:
                    self.error("Source was stopped before initial brief admission.", 409)
                operation = self.receipt(creation[4]['requestId'])
                if operation.get('creationVersion') == 1:
                    reason = self.creation_guard(operation)
                    if reason:
                        self.error(reason, 409)
            else:
                lineage = self.lineage(source, origin)
            if action == "coordination.subscribe":
                self.require_continuation()
                request = self.receipt(args["requestId"])
                if (request.get("commandAction") != "coordination.send" or request.get("senderSessionId") != source["id"]
                        or args["sessionId"] != source["id"] or request.get("protocol") != PROTOCOL
                        or request.get("workspace") != source["workspace"]):
                    self.error("A wait must bind this sender's exact current request.")
                self.request_target(source, request, hydrate=False)
                if request.get("subscription"):
                    return {"accepted": True, "result": request["subscription"], "duplicate": True}
                self.require_inbox_slot(source['id'])
                wait = self.saved_wait(source, args['requestId'])
                request["subscription"] = wait
                self.save(args["requestId"], request)
                receipt = {"accepted": wait["supported"], "commandAction": action, "result": wait}
            elif action == "coordination.reply":
                if origin != "agent":
                    self.error("Response declarations require the actual recipient agent generation.")
                active, request, revision, delivered = self.validate_reply(source, args)
                response = {**copy.deepcopy(args), 'generationId': active['id'], 'recipientSessionId': source['id'],
                            'operationId': identity, 'responseRevision': revision+1,
                            'causalDepth': self.causal_depth(source, lineage, origin),
                            'admittedInputIds': sorted(delivered),
                            'executionDirectory': source.get('workingDirectory') or source['workspace'],
                            'status': 'staged', 'qualified': False, 'declaredAt': time.time()}
                from .result_artifacts import store
                # Keep the reply, output relationships and resource roots in one
                # owner transaction. Prior unrelated pending receipts survive a
                # failed artifact save; a lost post-commit acknowledgment does
                # not discard retained bytes or repeat their capture.
                self.service.db.commit()
                original = copy.deepcopy(request)
                try:
                    await store(self, source, request, response, prepared_artifacts, identity)
                    if original.get('response', {}).get('status') == 'staged':
                        # Corrections are new receipts, not edits to saved evidence.
                        # Only the latest declaration can be sealed at this turn's end.
                        prior = copy.deepcopy(original)
                        prior['response'].update(status='superseded', qualified=False,
                                                 supersededBy=identity)
                        self.save_response(prior)
                    request['responseRevision'] = revision+1
                    request['response'] = response
                    if response_ready(response):
                        self.record_outcome(request, response)
                    self.save(args['requestId'], request)
                    receipt = {'accepted': True, 'commandAction': action, 'result': response}
                    self.insert(identity, digest, receipt)
                    self.publish_receipt(receipt)
                except BaseException:
                    if self.service.db.in_transaction:
                        self.service.db.rollback()
                        self.refresh_reference(original)
                    raise
                if continuation_outcome(request) is not None and request.get('subscription'):
                    self.enqueue_continuation(request)
                return receipt
            elif action == "coordination.create":
                if source.get('fileAccessPending'):
                    self.error('Finish applying the source chat’s file settings before creating another task.', 409)
                if args.get('workReferences'):
                    self.service.work_invocation.validate_references(source, args['workReferences'])
                # Creation is handled outside the service lock below.
                self.require_continuation()
                receipt = None
            else:
                target = self.authorize(source, args, cross_workspace=True,
                                        hydrate=args.get('mode') != 'notify')
                if args.get('resume') and source['workspace'] != target['workspace']:
                    self.error('Cross-workspace messaging does not authorize commissioned-member resume.')
                reason = self.service.assignments.blocked(source)
                if reason:
                    self.error(reason, 409)
                mode = args.get("mode", "auto")
                if sum(bool(args.get(key)) for key in ('resume', 'resumeHandoff', 'scheduleRepair')) > 1:
                    self.error('Choose exactly one scoped handoff or resume operation.', 400)
                notify_return = args.get('returnNotification', False)
                creation_owned = bool(creation and identity == creation[4]['requestId'] + ':brief')
                if notify_return:
                    self.require_continuation()
                if args.get('resume') and (origin != 'agent' or mode not in {'queue', 'auto'}):
                    self.error('Resume requires a current human-rooted agent request using auto or queue.')
                if args.get('resumeHandoff') and (
                        origin != 'agent' or mode not in {'queue', 'auto'}
                        or args.get('resume') or args.get('scheduleRepair')):
                    self.error('Issue a resume handoff only from a direct human-rooted agent send using auto or queue.')
                self.service.assignments.require_request(source, target, mode)
                if mode == "steer":
                    active = target.get("collaborationGeneration") or {}
                    if not self.can_steer(target):
                        return {"accepted": False, "result": {"supported": False, "effect": "none",
                            "reason": "No supported active generation-anchored recipient adapter."}}
                if mode in {"queue", "auto"} and not hasattr(self.service.runtime, "collaboration_input"):
                    return {"accepted": False, "result": {"supported": False, "effect": "none",
                        "reason": "This runtime lacks guarded idle peer admission."}}
                if not args["text"].strip():
                    self.error("Enter a peer message.", 400)
                self.require_message_rate(source['id'], target['id'])
                depth = self.causal_depth(source, lineage, origin)
                if mode != 'notify':
                    self.require_inbox_slot(target['id'], source['id'],
                        source.get('assignmentId') or (source.get('collaboration') or {}).get('assignmentId'),
                        slots=2 if notify_return and source['id'] == target['id'] else 1)
                if notify_return and source['id'] != target['id']:
                    self.require_inbox_slot(source['id'])
                if args.get("replyToRequestId"):
                    previous = self.receipt(args["replyToRequestId"])
                    if (previous.get("commandAction") != "coordination.send" or previous["target"]["sessionId"] != source["id"]
                            or previous["senderSessionId"] != target["id"] or previous.get("protocol") != PROTOCOL):
                        self.error("Reply linkage must identify the exact incoming request.")
                if mode in {'queue', 'auto', 'steer'} and not creation_owned:
                    # Only a NEW deliberate input captures current native task
                    # state. Old queued/accepted inputs retain their bindings.
                    from .task_continuity import _load_control, observe_task
                    try:
                        observation = _load_control(self.service, target, current_only=True)
                    except ValueError:
                        # Never-mounted/alternate adapters may have no local
                        # record. Keep the prior observation and native checks;
                        # unavailable is not a fabricated empty task.
                        pass
                    else:
                        observe_task(self.service, target, observation)
                task = target.get("task") or self.service.coordination.task(target["id"])
                from .completed_followup import prepare as prepare_completed
                completed_followup = prepare_completed(self.service, source, target, task, mode, args, creation=creation_owned)
                blocked_input = (mode in {'queue', 'auto'} and source['id'] != target['id']
                    and not args.get('resume') and not args.get('scheduleRepair')
                    and dependency_blocked(task))
                envelope = {"senderSessionId": source["id"], "senderRuntimeSessionId": source.get("runtimeSessionId") or source["id"],
                    "recipientSessionId": target["id"], **lineage,
                    "senderLocation": location(source), "recipientLocation": location(target),
                    "sender": descriptor(source), "recipient": descriptor(target),
                    "requestId": identity, "inputId": identity,
                    "mode": mode, "purpose": "Scoped collaboration task input", "references": args.get("references", []),
                    "replyToRequestId": args.get("replyToRequestId")}
                if blocked_input:
                    envelope['blockedTaskHandling'] = blocked_task_handling(task)
                if completed_followup:
                    envelope['completedTaskHandling'] = {**completed_followup,
                        'instruction': 'Handle this fresh authorized peer request within your existing authority. '
                            'Keep the earlier objective and evidence completed. Do the requested work directly, '
                            'or use task.create for its follow-up objective; '
                            'that successor retains the existing turn allowance and used turns. Report with '
                            'coordination.reply for this exact request. Do not replay older or unknown work.'}
                if target.get("collaboration"):
                    envelope["task"] = {key: target["collaboration"][key] for key in
                        ("creatorSessionId", "requestId", "outputNamespace", "configurationHash")}
                wait = None
                if not creation_owned:
                    if mode == 'notify' or notify_return or args.get('resume') or args.get('resumeHandoff'):
                        recovery_boundary.enter_context(self.return_send_transaction(
                            identity, source, target, notify=mode == 'notify'))
                    if notify_return:
                        wait = self.saved_wait(source, identity)
                    envelope['returnContract'] = {
                        'version': 1, 'requestId': identity, 'returnNotification': notify_return,
                        'subscriptionRegistered': wait is not None,
                        **({'continuationId': wait['continuationId'], 'notificationVersion': wait['notificationVersion']}
                           if wait else {})}
                    if wait:
                        envelope['returnContract']['instruction'] = (
                            'Report with coordination.reply for this exact request. A saved report is not '
                            'requester consumption or independent acceptance. Automatic returns remain subject '
                            'to the saved Stop/task, capacity and outcome-revision guards.')
                repair = None
                resume = None
                handoff = None
                if args.get('resumeHandoff'):
                    from .resume_handoff import prepare, view
                    handoff = prepare(self.service, source, target, args['resumeHandoff'], lineage)
                    envelope['resumeHandoff'] = {**view(self.service, handoff),
                        'instruction': 'The host captured an exact one-hop human resume scope. Read the original '
                            'human instruction and assess its intent, not sender prose. Only you, the commissioning '
                            'owner, may deliberately send one fresh member input with resume.sourceRequestId '
                            'equal to this inputId. This is not humanInput, forwarding authority, task/Stop '
                            'clearance, permission, a question answer or schedule/configuration authority.'}
                if args.get('resume'):
                    from .peer_resume import prepare
                    resume = prepare(self.service, source, target, args['resume'], lineage, recovery_boundary)
                    if resume.get('handoffRequestId'):
                        from .resume_handoff import reserve
                        reserve(self.service, resume['handoffRequestId'], identity)
                    envelope['humanResume'] = {
                        'sourceMessageId': resume['sourceMessageId'],
                        **({'handoffRequestId': resume['handoffRequestId']} if resume.get('handoffRequestId') else {}),
                        'instruction': 'The host verified a fresh human resume scope through the commissioning owner. '
                            'Reconcile completed effects first. You may resume your own paused task if its blocker '
                            'is resolved. Keep completed objectives completed; only establish follow-on work '
                            'within the human request. This does not answer questions, grant permissions or reset budgets.'}
                if args.get('scheduleRepair'):
                    if origin != 'agent' or mode == 'notify':
                        self.error('Schedule repair requires a current human-rooted agent handoff using queue or steer.')
                    from .schedule_handoff import prepare
                    repair = prepare(self.service, source, target, args['scheduleRepair'], lineage)
                target["historyManaged"] = False
                message = self.service._message(target, "user", args["text"], "peer", inputId=identity,
                                               inputOrigin="peer", peerEnvelope=envelope)
                receipt = {"accepted": True, "commandAction": "coordination.send", "requestId": identity,
                    "inputId": identity, "messageId": message["id"], "senderSessionId": source["id"],
                    "endpointBindingVersion": 1,
                    "sender": descriptor(source), "target": {**descriptor(target), "workspace": target["workspace"]},
                    "senderLocation": location(source), "recipientLocation": location(target),
                    "protocol": PROTOCOL, "workspace": source["workspace"], **lineage,
                    "mode": mode, 'causalDepth': depth, "delivery": "notified" if mode == "notify" else "queued",
                    "interruptionRevision": target.get("interruptionRevision", 0),
                    "taskId": task.get("id"), "taskRevision": task.get("revision"),
                    **({'blockedTaskInput': True} if blocked_input else {}),
                    **({'completedTaskFollowup': completed_followup} if completed_followup else {}),
                    'replyTaskContinuationEpoch': continuation_epoch(task),
                    # Dispatcher provenance is host-owned. Never copy actor
                    # claims from args, message prose, or the peer envelope.
                    **({'expiresAt': time.time()+args.get('expiresInSeconds', DEFAULT_QUEUE_SECONDS)} if mode != 'notify' else {}),
                    "origin": origin if origin in {"agent", "ui", "user"} else None,
                    "displayBinding": fingerprint([envelope, args["text"]])}
                receipt['assignmentId'] = (self.service.assignments.current(source) or {}).get('id')
                if not creation_owned:
                    receipt['returnNotification'] = notify_return
                    if wait:
                        receipt['subscription'] = wait
                if repair:
                    receipt['scheduleRepair'] = repair
                if resume:
                    receipt['humanResume'] = resume
                    if resume.get('handoffRequestId'):
                        receipt['expiresAt'] = min(receipt['expiresAt'],
                            self.receipt(resume['handoffRequestId'])['expiresAt'])
                if handoff:
                    receipt['resumeHandoff'] = handoff
                if mode == "steer":
                    receipt.update(delivery="pending_steer", targetGenerationId=active["id"])
                if creation and identity == creation[4]['requestId'] + ':brief':
                    operation = self.receipt(creation[4]['requestId'])
                    if operation.get('creationVersion') == 1:
                        if operation.get('workReferences'):
                            self.service.work_invocation.seal(operation, receipt, source, target)
                        receipt['creationOperationId'] = operation['requestId']
                        wait = operation.get('subscription')
                        if wait:
                            receipt['subscription'] = {**wait, 'requestId': identity, 'status': 'waiting'}
                            operation['subscription'] = {**wait, 'requestId': identity, 'status': 'linked', 'open': False}
                        operation.update(delivery='created', phase='input_committed', initialDelivery=receipt['delivery'],
                                         detail='Peer, initial request and return policy committed before admission.')
                        self.save(operation['requestId'], operation)
                        self.service.db.execute('DELETE FROM collaboration_preparations WHERE request_id=?', (operation['requestId'],))
            if receipt is not None:
                self.insert(identity, digest, receipt)
                self.publish_receipt(receipt)
                if action in {'coordination.reply', 'coordination.subscribe'} and continuation_outcome(request) is not None and request.get('subscription'):
                    self.enqueue_continuation(request)
        if action == "coordination.create":
            return await self.create(source, args, origin, identity, digest, lineage)
        if receipt.get("delivery") == "pending_steer":
            return self.send_result(await self.submit_steer(identity))
        if receipt.get("delivery") == "queued":
            # The committed receipt owns the operation, not the request socket.
            self.expire_later(receipt)
            self.service._task(self.drain(receipt["target"]["sessionId"]))
        return self.send_result(receipt) if action == 'coordination.send' else receipt

    async def create(self, source, args, origin, identity, digest, lineage):
        from .session_creation import template
        try:
            config, summary = template(self.service, source)
        except ValueError as exc:
            self.error(str(exc), 409)
        # Rebind the same host-composed instance/source when the child prepares;
        # never copy expanded credentials or guess a provider-family account.
        from .provider_environment import credential_bindings
        config = credential_bindings(config)
        summary["configurationHash"] = fingerprint(config)
        if args.get('constraints') and not args.get('objective'):
            self.error('Commissioned constraints require an objective.', 400)
        if 'objective' in args and not args['objective'].strip():
            self.error('Enter a nonempty commissioned objective.', 400)
        execution = copy.deepcopy(args.get('execution'))
        if execution:
            from amplifier_worktrees.git import source_binding
            source_binding(execution.get('sourceRevision'), execution.get('expectedCommit'), 'clean')
            self.service.worktrees.check_host(source)
            execution['repositoryPath'] = await asyncio.to_thread(
                self.service.worktrees.repository_path, source, execution.get('repositoryPath'))
        new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "collaborative-task:" + source["id"] + ":" + identity))
        metadata = {"creatorSessionId": source["id"], "creatorTitle": source["title"], "requestId": identity,
            "configurationHash": summary["configurationHash"], **lineage,
            "workingDirectory": source.get("workingDirectory") or source["workspace"],
            "outputNamespace": "working-files/tasks/" + new_id, "brief": args["text"], "references": args.get("references", [])}
        async with self.service.lock, self.service.assignments.mutation(source, identity):
            duplicate = self.duplicate(identity, digest)
            if duplicate:
                return duplicate
            if args.get('workReferences'):
                self.service.work_invocation.validate_references(source, args['workReferences'])
            from .test_execution_settings import concurrency
            maximum = concurrency(self.service)['maxTaskChatsPerSource']
            if maximum:
                # A root occupies one slot while executable work is outstanding.
                # Idle history and uncertain historical receipts are not work; their
                # evidence stays unknown and is never resolved/replayed by this cap.
                roots = [row for row in self.service.state["sessions"]
                         if (row.get("collaboration") or {}).get("creatorSessionId") == source["id"]]
                outstanding = set()
                for row in roots:
                    active = row.get("collaborationGeneration") or {}
                    task = row.get("task") or self.service.coordination.task(row["id"]) or {}
                    if (row.get("status") in {"starting", "working", "running", "busy", "stopping"}
                            or active.get("id") and not active.get("terminal")
                            or row["id"] in getattr(self.service.runtime, "_preparations", {})
                            or task.get("id") and task.get("status") in {"active", "running", "working"}):
                        outstanding.add(row["id"])
                # Idle presentation can be nonresident while a durable task or an
                # unsubmitted input still occupies a slot. Count exact directory
                # owners without admitting or reading unrelated saved bodies.
                sql, bound = self.service.directory.effective()
                outstanding.update(row[0] for row in self.service.db.execute(sql + """
                    SELECT d.id FROM directory_rows d
                    WHERE json_extract(d.summary,'$.collaboration.creatorSessionId')=?
                    AND (json_extract(d.summary,'$._coordinationTaskActive')=1 OR EXISTS(
                        SELECT 1 FROM commands c
                        WHERE json_extract(c.receipt,'$.commandAction')='coordination.send'
                        AND json_extract(c.receipt,'$.target.sessionId')=d.id
                        AND json_extract(c.receipt,'$.delivery') IN ('queued','submitting','pending_steer')))
                    LIMIT ?""", (*bound, source['id'], maximum)))
                pending = self.service.db.execute("""SELECT receipt FROM commands
                    WHERE json_extract(receipt,'$.commandAction')='coordination.create'
                    AND json_extract(receipt,'$.senderSessionId')=?
                    AND json_extract(receipt,'$.protocol')=?
                    AND json_extract(receipt,'$.delivery') IN ('creation_pending','created_initial_pending')""",
                    (source["id"], PROTOCOL)).fetchall()
                outstanding.update(json.loads(row[0])["sessionId"] for row in pending)
                if len(outstanding) >= maximum:
                    self.error(f"The limit of {maximum} outstanding task chats per source has been reached. Adjust Advanced execution limits in Settings to change it.", 409)
            if not args['title'].strip() or not args['text'].strip():
                self.error('Enter a task title and brief.', 400)
            notify = args.get('returnNotification', True)
            if notify:
                self.require_inbox_slot(source['id'])
            assignment_id = self.service.assignments.reserve_member(source, new_id, identity)
            metadata['assignmentId'] = assignment_id
            task = source.get('task') or self.service.coordination.task(source['id'])
            receipt = {"accepted": True, "commandAction": "coordination.create",
                "requestId": identity, "senderSessionId": source["id"], "workspace": source["workspace"], "protocol": PROTOCOL,
                "sessionId": new_id, "delivery": "creation_pending", "result": metadata,
                'creationVersion': 1, 'phase': 'planned', 'title': args['title'], 'origin': origin,
                'sourceWorkingDirectory': metadata['workingDirectory'],
                **lineage,
                'sourceTaskId': task.get('id'), 'sourceTaskRevision': task.get('revision'),
                'sourceTaskContinuationEpoch': continuation_epoch(task),
                "initialInputId": identity + ":brief", "detail": "Creation committed; preparation is owned by the service."}
            receipt['assignmentId'] = assignment_id
            if execution:
                receipt['execution'] = execution
            if args.get('objective'):
                receipt['objective'] = {'objective': args['objective'], 'constraints': args.get('constraints', [])}
            if args.get('workReferences'):
                receipt['workReferences'] = copy.deepcopy(args['workReferences'])
            if notify:
                receipt['subscription'] = self.saved_wait(source, identity, status='reserved')
            self.service.db.execute('INSERT INTO collaboration_preparations(request_id,configuration) VALUES(?,?)',
                                    (identity, json.dumps(config)))
            self.insert(identity, digest, receipt)
            self.service._save_changes(sessions={source['id']})
        self.service._task(self.finish_create(identity))
        return receipt

    def end_creation(self, receipt, delivery, detail, *, notify=False):
        if receipt.get('assignmentId') and delivery == 'suppressed' and receipt.get('phase') == 'planned':
            assignment = self.service.assignments.load(receipt['assignmentId'])
            assignment['members'][receipt['sessionId']]['status'] = 'not_created'
            assignment['revision'] += 1
            self.service.assignments.save(assignment)
        receipt.update(delivery=delivery, detail=detail)
        wait = receipt.get('subscription')
        if wait and wait.get('status') == 'reserved':
            wait['status'] = 'waiting' if notify else 'suppressed'
        if notify:
            receipt['terminalOutcome'] = {'status': 'observed', 'outcome': 'unknown' if delivery == 'unknown' else 'failed',
                'qualified': False, 'requestId': receipt['requestId'], 'text': detail, 'observedAt': time.time()}
        self.save(receipt['requestId'], receipt)
        if notify and wait:
            self.enqueue_continuation(receipt)
        self.publish_receipt(receipt)

    async def finish_create(self, identity):
        """Resume only steps backed by the private plan and durable effect receipts."""
        if identity in self.creating:
            return
        self.creating.add(identity)
        try:
            async with self.service.lock:
                receipt = self.receipt(identity)
                if receipt.get('delivery') not in {'creation_pending', 'created_initial_pending'}:
                    return
                reason = self.creation_guard(receipt)
                if reason:
                    self.end_creation(receipt, 'suppressed', reason)
                    return
                if receipt.get('phase') not in {'planned', 'creating_checkout', 'checkout_ready', 'creating_root', 'root_retained', 'installing_objective', 'objective_ready'}:
                    self.end_creation(receipt, 'unknown', 'The preparation phase is unsupported. Retained effects were not replayed.', notify=True)
                    return
                row = self.service.db.execute('SELECT configuration FROM collaboration_preparations WHERE request_id=?', (identity,)).fetchone()
                if not row:
                    self.end_creation(receipt, 'unknown', 'The captured preparation plan is unavailable. Retained effects were not replayed.', notify=True)
                    return
                config = json.loads(row[0])
                if fingerprint(config) != receipt['result']['configurationHash']:
                    self.end_creation(receipt, 'unknown', 'The captured preparation plan changed. Retained effects were not replayed.', notify=True)
                    return
                source = self.service._session(receipt['senderSessionId'])
                args = {'title': receipt['title'], 'text': receipt['result']['brief'], 'references': receipt['result']['references']}
            await self._finish_create(source, config, args, receipt['origin'], identity, receipt['sessionId'], receipt['result'])
        except asyncio.CancelledError:
            # Phases already precede effects; orderly shutdown need not destroy
            # the evidence required to resume a still-unperformed step.
            raise
        except BaseException as exc:
            async with self.service.lock:
                receipt = self.receipt(identity)
                # A lost acknowledgment after the input transaction is not a lost
                # operation. Keep its exact admitted/queued receipt untouched.
                if receipt.get('phase') != 'input_committed':
                    from amplifier_worktrees.git import CheckoutNotStarted
                    receipt['errorType'] = type(exc).__name__
                    reason = self.creation_guard(receipt)
                    if reason:
                        self.end_creation(receipt, 'suppressed', reason)
                    elif isinstance(exc, CheckoutNotStarted):
                        receipt['preparationFailure'] = {'kind': 'checkout_not_started', 'detail': str(exc)[:1000]}
                        self.end_creation(receipt, 'not_sent',
                            'Checkout preparation refused before any checkout effect or initial input. '
                            'The existing worker is retained. ' + str(exc)[:1000], notify=True)
                    else:
                        self.end_creation(receipt, 'unknown', 'Preparation did not confirm all effects. Inspect the retained operation; no effects were replayed.', notify=True)
        finally:
            self.creating.discard(identity)

    async def _finish_create(self, source, config, args, origin, identity, new_id, metadata):
        values = {"id": new_id, "title": args["title"], "workspace": source["workspace"],
                  "bundle": config["bundle"], "select": False}
        async with self.service.lock:
            receipt = self.receipt(identity)
            reason = self.creation_guard(receipt)
            if reason:
                self.end_creation(receipt, 'suppressed', reason)
                return
            create_root = False
            if receipt['phase'] == 'creating_root':
                # Only a committed subordinate receipt establishes root creation;
                # partial files or a matching-looking title do not prove completion.
                row = self.service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity + ':create',)).fetchone()
                created = json.loads(row[0]) if row else {}
                if created.get('sessionId') != new_id or not created.get('accepted'):
                    self.end_creation(receipt, 'unknown', 'Root creation has no committed acknowledgment. Existing files and history were retained without replay.', notify=True)
                    return
            elif receipt['phase'] == 'planned':
                receipt['phase'] = 'creating_root'
                self.save(identity, receipt)
                self.service.db.commit()
                create_root = True
        if create_root:
            token = CREATION.set((source["id"], fingerprint(values), None, config, metadata))
            try:
                await self.service.dispatch("session.create", values, origin, identity + ":create",
                                            include_state=False, caller_session_id=source["id"])
            finally:
                CREATION.reset(token)
        async with self.service.lock:
            receipt = self.receipt(identity)
            target = self.service._session(new_id)
            if (target.get('collaboration') != metadata or target['workspace'] != receipt['workspace']
                    or (target.get('workingDirectory') or target['workspace']) != metadata['workingDirectory']):
                self.error('The retained root does not match this creation operation.', 409)
            directory = self.service.data_dir / 'sessions' / (target.get('runtimeSessionId') or target['id'])
            expected_files = [('configuration.json', config['plan'])]
            if receipt['phase'] not in {'installing_objective', 'objective_ready'}:
                expected_files.append(('control-state.json', config['controls']))
            for filename, expected in expected_files:
                if json.loads((directory / filename).read_text()) != expected:
                    self.error('The retained root configuration changed before initial input.', 409)
            receipt.update(delivery='created_initial_pending',
                           phase=receipt['phase'] if receipt['phase'] in {'creating_checkout', 'checkout_ready', 'installing_objective', 'objective_ready'} else 'root_retained',
                           detail='Root retained. Objective, initial input and return policy are being committed.')
            if receipt.get('assignmentId'):
                assignment = self.service.assignments.load(receipt['assignmentId'])
                assignment['members'][new_id]['status'] = 'bound'
                self.service.assignments.save(assignment)
            self.save(identity, receipt)
            self.publish_receipt(receipt)
        if receipt.get('execution'):
            from .worker_commissioning import prepare_checkout
            await prepare_checkout(self, identity)
            receipt = self.receipt(identity)
            if receipt['delivery'] not in {'creation_pending', 'created_initial_pending'}:
                return
            metadata = receipt['result']
        if receipt.get('objective'):
            from .worker_commissioning import prepare_objective
            await prepare_objective(self, identity)
        # The durable brief is ordinary attributed input, not copied history.
        token = CREATION.set((source["id"], fingerprint(values), None, config, metadata))
        try:
            await self.dispatch("coordination.send", {"sessionId": new_id, "senderSessionId": source["id"],
                "text": args["text"], "references": args.get("references", []),
                "mode": "queue"}, origin, identity + ":brief", source["id"])
        finally:
            CREATION.reset(token)
        return self.receipt(identity)
