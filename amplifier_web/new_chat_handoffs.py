"""Independent-chat handoffs; creation and input keep their canonical owners.

Explicit user requests can start directly through the agent action. Suggestions
remain proposals for human review. Both use fixed child command identities and
retained phase receipts, never replaying uncertain effects.
"""
from __future__ import annotations

import asyncio
import copy
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import time
import uuid

EXECUTION = ContextVar("independent_handoff_execution", default=None)

PREFIX = "session.handoff."
ACTIVE = {"creating", "created", "delivering"}
UNFINISHED = ACTIVE | {"pending", "unknown"}
MAX_REVIEW_REFERENCES = 64
PUBLIC = {"requestId", "sourceSessionId", "workspace", "title", "text", "bundle",
          "selection", "reviewHash", "status", "revision", "targetSessionId",
          "creation", "delivery", "message", "createdAt", "updatedAt", "inputId", "mode"}


def definitions(schema, string):
    from .new_chat import SELECTION
    request = {"requestId": {**string(100), "minLength": 1}}
    creation = schema({
        **request, "sessionId": {**string(200), "minLength": 1},
        "workspace": {**string(4000), "minLength": 1},
        "title": {**string(200), "minLength": 1}, "text": {**string(16000), "minLength": 1},
        "bundle": string(2000), "selection": SELECTION, "clientId": string(100),
    }, ["requestId", "sessionId", "workspace", "title", "text"])
    return {
        PREFIX + "start": ("Create and start an independent chat in an existing target folder. This does not commission a worker, establish a completion dependency or return its result to the source manager; use coordination.create for subordinate work in the inherited workspace. Full access supports autonomous roots, assigned children and trusted systems without a browser or fresh human request; destination access inherits the saved source relationship unless explicitly overridden. Ask requires an explicit user request and connected client, without a second confirmation. Uses the target folder's normal configuration. Keep requestId stable; read uncertain results without replaying. Preserves selection and drafts; opening the target is separate.", creation),
        PREFIX + "propose": ("Suggest an independent chat for compact human review when the user has not authorized starting it. No chat or work is created until the attached client confirms the exact target and brief. Confirmation does not commission a worker or establish a completion return to the source manager. For an explicit user request use session.handoff.start directly. Reuse requestId after lost acknowledgement; read uncertain results, never replay them.", creation),
        PREFIX + "read": ("Read one retained handoff receipt without creating a chat, starting a runtime, navigating or replaying input. Accepted means the brief was accepted, not that its work completed or that its result will return to the source manager.", schema(request, ["requestId"])),
        PREFIX + "confirm": ("Human client only: confirm this exact reviewed independent-chat handoff. Creates once and submits its exact brief once through normal creation, permissions and input handlers. Preserves selection and drafts. An unknown outcome must be inspected, never automatically resubmitted.", schema({**request, "reviewHash": {**string(64), "minLength": 64}}, ["requestId", "reviewHash"])),
        PREFIX + "cancel": ("Human client only: cancel a pending handoff without creating or starting work. After creation use the target chat's ordinary Stop control.", schema(request, ["requestId"])),
    }


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def public(row):
    return copy.deepcopy({key: value for key, value in row.items() if key in PUBLIC})


class NewChatHandoffs:
    def __init__(self, service):
        self.service = service
        self.changed_sources = set()
        service.db.execute("""CREATE TABLE IF NOT EXISTS new_chat_handoffs(
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, source TEXT NOT NULL,
            status TEXT NOT NULL, updated REAL NOT NULL, value TEXT NOT NULL)""")
        service.db.execute("CREATE INDEX IF NOT EXISTS handoff_client_source ON new_chat_handoffs(owner,source,updated DESC)")
        service.db.execute("CREATE INDEX IF NOT EXISTS handoff_pending ON new_chat_handoffs(owner,source,status,updated DESC)")
        service.db.execute("CREATE INDEX IF NOT EXISTS handoff_owner_recent ON new_chat_handoffs(owner,updated DESC)")
        service.db.execute("CREATE INDEX IF NOT EXISTS handoff_owner_pending ON new_chat_handoffs(owner,status,updated DESC)")
        service.db.execute("CREATE INDEX IF NOT EXISTS handoff_input ON new_chat_handoffs(json_extract(value,'$.inputId'))")
        for (encoded,) in service.db.execute("SELECT value FROM new_chat_handoffs WHERE status IN ('creating','created','delivering')").fetchall():
            row = json.loads(encoded)
            row.update(status="unknown", message="The app restarted before this handoff was confirmed. Inspect its saved chat and delivery; nothing was replayed.")
            self.known(row)
            self.save(row)
        service.db.commit()

    @staticmethod
    def error(message, status=409):
        from .errors import AppError
        raise AppError(message, status)

    def load(self, identity):
        found = self.service.db.execute("SELECT value FROM new_chat_handoffs WHERE id=?", (identity,)).fetchone()
        return json.loads(found[0]) if found else None

    def save(self, row, *, publish=False, commit=True):
        previous = self.service.db.execute("SELECT json_extract(value,'$.revision') FROM new_chat_handoffs WHERE id=?", (row["requestId"],)).fetchone()
        row["revision"] = max(row.get("revision", 0), previous[0] if previous else 0) + 1
        row["updatedAt"] = time.time()
        self.service.db.execute("INSERT INTO new_chat_handoffs VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated=excluded.updated,value=excluded.value",
            (row["requestId"], row["owner"], row["sourceSessionId"], row["status"], row["updatedAt"], json.dumps(row)))
        if commit:
            self.service.db.commit()
        if publish:
            self.service._publish_changes(sessions=(row["sourceSessionId"],), globals=(), detail_only=True)

    def project(self):
        owner = self.service.clients.current.get()
        source = self.service.state.get("selectedSessionId")
        if not owner or not source:
            return []
        return [public(row) for row in self.visible(owner, source)]

    def visible(self, client, source):
        # Both owner reads use bounded indexed ranges; copied references use
        # exact primary keys. Sorting never scans the owner's retained history.
        rows = {}
        for condition in (" AND status IN ('pending','creating','created','delivering','unknown')", ""):
            index = "handoff_pending" if condition else "handoff_client_source"
            for (value,) in self.service.db.execute(
                    "SELECT value FROM new_chat_handoffs INDEXED BY " + index + " WHERE owner=? AND source=?" + condition + " ORDER BY updated DESC LIMIT 8", (client, source)):
                row = json.loads(value); rows[row["requestId"]] = row
        for row in self.captured(client):
            if row["sourceSessionId"] == source:
                rows[row["requestId"]] = row
        return sorted(rows.values(), key=lambda row: (row["status"] in UNFINISHED, row["updatedAt"]), reverse=True)[:8]

    def captured(self, client):
        references = self.service.clients.records.get(client, {}).get("handoffReviews", [])
        captured = {reference["requestId"]: reference["reviewHash"] for reference in references[:MAX_REVIEW_REFERENCES]}
        rows = []
        if captured:
            for (value,) in self.service.db.execute("SELECT value FROM new_chat_handoffs WHERE id IN (" + ",".join("?" for _ in captured) + ")", list(captured)):
                row = json.loads(value)
                if row["reviewHash"] == captured[row["requestId"]]:
                    rows.append(row)
        return rows

    def unfinished(self, client):
        rows = {row["requestId"]: row for row in self.captured(client) if row["status"] in UNFINISHED}
        for (value,) in self.service.db.execute(
                "SELECT value FROM new_chat_handoffs INDEXED BY handoff_owner_pending WHERE owner=? AND status IN ('pending','creating','created','delivering','unknown') ORDER BY updated DESC LIMIT ?", (client, MAX_REVIEW_REFERENCES)):
            row = json.loads(value); rows[row["requestId"]] = row
        return rows

    def capture_on_attach(self, previous_client, previous, record):
        # Preserve pending reviews across navigation and exact prior captures,
        # never a lineage-wide authority over future proposals. Indexed LIMITs
        # bound work even when this client has years of terminal receipts.
        rows = {row["requestId"]: row for row in self.captured(previous_client)}
        rows.update(self.unfinished(previous_client))
        for (value,) in self.service.db.execute(
                "SELECT value FROM new_chat_handoffs WHERE owner=? ORDER BY updated DESC LIMIT ?", (previous_client, MAX_REVIEW_REFERENCES)):
            row = json.loads(value); rows[row["requestId"]] = row
        selected = sorted(rows.values(), key=lambda row: (row["status"] in UNFINISHED, row["updatedAt"]), reverse=True)[:MAX_REVIEW_REFERENCES]
        record["handoffReviews"] = [{"requestId": row["requestId"], "reviewHash": row["reviewHash"]} for row in selected]

    def client_can_review(self, row, client):
        if client == row["owner"]:
            return True
        return any(reference.get("requestId") == row["requestId"] and reference.get("reviewHash") == row["reviewHash"]
                   for reference in self.service.clients.records.get(client, {}).get("handoffReviews", [])[:MAX_REVIEW_REFERENCES])

    def authorize(self, row, origin, caller, *, human=False):
        owner = self.service.clients.current.get()
        if row.get("mode") == "full":
            if human or origin in {"ui", "user"} or not caller or row["sourceSessionId"] != caller:
                self.error("This handoff belongs to its calling conversation.", 403)
            return
        if human and origin not in {"ui", "user"}:
            self.error("Only the attached human client can confirm or cancel this review.", 403)
        if origin not in {"ui", "user"}:
            if not caller or row["sourceSessionId"] != caller:
                self.error("This handoff belongs to another conversation.", 403)
        elif not owner or not self.client_can_review(row, owner):
            self.error("This handoff belongs to another client.", 403)
        if human and self.service.state.get("selectedSessionId") != row["sourceSessionId"]:
            self.error("Open the source chat in this client to review this handoff.")

    def target(self, values):
        from .bundle_selection import defaults
        from .host.config import read_config
        from .new_chat import selection
        from .shared_settings import settings_paths
        path = Path(values["workspace"]).expanduser().resolve(strict=True)
        if not path.is_dir():
            self.error("Choose an existing workspace folder.")
        self.service.worktrees.require_available_path(str(path))
        self.service._require_workspace_scaffold(str(path))
        config = read_config(path, home=self.service.data_dir)
        bundle = values.get("bundle") or defaults(self.service.data_dir, path,
            self.service.state["settings"].get("appBundle"))["effective"]
        if bundle not in config.registrations:
            self.error("Choose a registered bundle for the target workspace before proposing this handoff.")
        chosen = selection(values.get("selection", {}))
        stat = path.stat()
        # Bind canonical settings locations as well as their merged semantics.
        # A retargeted settings symlink is not the file the human reviewed.
        settings_files = {name: str(file.resolve()) for name, file in settings_paths(path).items()}
        return {"workspace": str(path), "bundle": bundle, "selection": chosen,
                "targetIdentity": [stat.st_dev, stat.st_ino],
                "configurationHash": fingerprint({"settings": config.settings, "settingsFiles": settings_files,
                    "registrations": config.registrations, "appBundle": self.service.state["settings"].get("appBundle"),
                    "bundle": bundle, "rootSource": self.root_source(config, bundle), "selection": chosen})}

    def root_source(self, config, bundle):
        """Bind the local root only; never fetch/activate a dependency graph."""
        from .host.bundle_paths import local_bundle_path
        registered = config.registrations[bundle]
        if not isinstance(registered, str):
            self.error("Choose a registered bundle with an inspectable source.")
        reference = config.resolve_source(bundle) or config.resolve_source(registered) or bundle
        candidate = local_bundle_path(config, reference)
        if candidate is None:
            candidate = local_bundle_path(config, registered)
        if candidate is None:
            if registered.startswith(("git+", "https://", "http://", "ssh://")):
                return {"reference": registered}
            self.error("The chosen local bundle is unavailable. Repair its registration before reviewing this handoff.")
        root = candidate.resolve(strict=True)
        files = [root / name for name in ("bundle.md", "bundle.yaml", "bundle.yml")] if root.is_dir() else [root]
        identities = []
        for file in files:
            if not file.exists():
                continue
            resolved = file.resolve(strict=True)
            info = resolved.stat()
            if not resolved.is_file() or info.st_size > 4 * 1024 * 1024:
                self.error("The local bundle root cannot be safely reviewed. Choose a regular bundle file up to 4 MB.")
            with resolved.open("rb") as stream:
                contents = stream.read(4 * 1024 * 1024 + 1)
            if len(contents) > 4 * 1024 * 1024:
                self.error("The local bundle root exceeds the review size limit.")
            after = resolved.stat()
            if (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
                self.error("The local bundle changed while it was being reviewed. Try a fresh review.")
            identities.append({"path": str(resolved), "identity": [info.st_dev, info.st_ino], "sha256": hashlib.sha256(contents).hexdigest()})
        if not identities:
            self.error("The chosen local bundle has no readable root manifest.")
        return {"path": str(root), "files": identities}

    def guard(self, row, *, delivery=False):
        if row.get("mode") == "full":
            self.guard_source(row["authority"])
        from .updates import work_paused
        if work_paused(self.service.state):
            self.error("An ecosystem update is activating. Review this handoff when it finishes.")
        if self.service.closed:
            self.error("The app is shutting down. Inspect this handoff after reconnecting.")
        try:
            current = self.target(row)
        except (OSError, ValueError):
            self.error("The reviewed workspace or configuration is unavailable. Propose a new review.")
        for key in ("workspace", "bundle", "selection", "targetIdentity", "configurationHash"):
            if current[key] != row[key]:
                self.error("The reviewed workspace or configuration changed. Propose a new review.")
        if delivery:
            # A new independent chat has no session-level permission/configuration
            # override. Refuse a later edit instead of sending with unreviewed
            # authority. Global/project/local changes were checked above.
            from .shared_settings import settings_paths
            session_file = settings_paths(row["workspace"], session_id=row["targetSessionId"])["session"]
            config_file = self.service.data_dir / "sessions" / row["targetSessionId"] / "configuration.json"
            if any(file.exists() or file.is_symlink() for file in (session_file, config_file)):
                self.error("The new chat's settings changed before the brief was submitted. Nothing was resent.")
            target = self.service._session(row["targetSessionId"], hydrate=False)
            if row.get('mode') == 'full' and (
                    self.service.access_mode.snapshot(target)['revision'] != row.get('targetAccessRevision')
                    or target.get('executionRevision', 0) != row.get('targetExecutionRevision')
                    or (target.get('workingDirectory') or target['workspace']) != row['workspace']):
                self.error("The new chat's access or execution placement changed before the brief was submitted. Nothing was resent.")
            if (target.get("interruptionRevision", 0) != row.get("interruptionRevision", 0)
                    or target["workspace"] != row["workspace"] or target["bundle"] != row["bundle"]
                    or target.get("selection", {}) != row["selection"]):
                self.error("The new chat was stopped or changed before the brief was submitted. Nothing was resent.")

    def capture(self, source, origin):
        from .access_mode import execution_id
        authority = self.service.access_mode.capture_full(source, origin)
        if authority is not None:
            authority.update(workspace=source['workspace'], executionId=execution_id(source),
                             interruptionRevision=source.get('interruptionRevision', 0))
        return authority

    def guard_source(self, authority):
        from .access_mode import execution_id
        source = self.service._session(authority['sessionId'], hydrate=False)
        if (source['workspace'] != authority['workspace']
                or execution_id(source) != authority['executionId']
                or source.get('interruptionRevision', 0) != authority['interruptionRevision']
                or not self.service.access_mode.full(source, authority['accessRevision'])):
            self.error('The handoff source was stopped, replaced or its Full access changed.')
        if blocked := self.service.assignments.blocked(source):
            self.error(blocked)

    @staticmethod
    def values(row, action):
        if action == 'session.create':
            return {'id': row['targetSessionId'], 'workspace': row['workspace'], 'title': row['title'],
                    'bundle': row['bundle'], 'selection': row['selection'], 'select': False}
        return {'sessionId': row['targetSessionId'], 'text': row['text'], 'preserveDraft': True}

    def execution(self, action, args, origin, command_id, caller):
        """Only the saved owner phase can compose canonical create/input effects."""
        context = EXECUTION.get()
        if (not context or context[0] is not self or context[3] is not asyncio.current_task()
                or action not in {'session.create', 'conversation.send'}):
            return None
        row = self.load(context[1])
        creating = action == 'session.create'
        if (not row or row.get('mode') != 'full'
                or origin != row['authority']['origin'] or caller != row['sourceSessionId']
                or context[2] != action or row['status'] != ('creating' if creating else 'delivering')
                or command_id != row['createCommandId' if creating else 'inputId']
                or args != self.values(row, action)):
            self.error('The saved independent handoff no longer matches this effect.')
        self.guard(row, delivery=not creating)
        return row

    @staticmethod
    def lineage(row):
        return {'sessionId': row['sourceSessionId'], 'requestId': row['requestId'],
                'createCommandId': row['createCommandId']}

    def input_guard(self, sid, input_id):
        """Recheck accepted Full work at native preparation/transport, without replay."""
        from .errors import AppError
        from .runtime import RuntimeStartupError
        found = self.service.db.execute(
            "SELECT value FROM new_chat_handoffs WHERE json_extract(value,'$.inputId')=?", (input_id,)).fetchone()
        try:
            row = json.loads(found[0]) if found else None
            if not row or row.get('mode') != 'full' or row['targetSessionId'] != sid:
                self.error('The saved handoff input is unavailable.')
            self.guard(row, delivery=True)
        except AppError as exc:
            raise RuntimeStartupError(str(exc) + ' This attempt did not send the handoff input.') from exc

    @staticmethod
    def actor(row):
        return {key: value for key, value in row['authority'].items()
                if key in {'origin', 'sessionId', 'runtimeSessionId', 'generationId', 'inputIds', 'inputCoverage'}}

    async def perform(self, row, action):
        # Admission already captured the native actor. Canonical delayed work is
        # guarded by that capture, not a root generation that may have finished.
        from .workspace_work_effects import APP_EFFECT
        context = EXECUTION.set((self, row['requestId'], action, asyncio.current_task())) if row.get('mode') == 'full' else None
        effect = APP_EFFECT.set(None) if context is not None else None
        try:
            return await self.service.dispatch(action, self.values(row, action),
                origin=row['authority']['origin'] if context is not None else 'ui',
                caller_session_id=row['sourceSessionId'] if context is not None else None,
                command_id=row['createCommandId' if action == 'session.create' else 'inputId'], include_state=False,
                admission_check=lambda: self.guard(row, delivery=action == 'conversation.send'))
        finally:
            if context is not None:
                EXECUTION.reset(context)
                APP_EFFECT.reset(effect)

    async def dispatch(self, action, args, origin, caller, *, include_state=True, admission_check=None):
        service = self.service
        direct_agent = action == PREFIX + "start" and origin not in {"ui", "user"}
        authority = None
        if direct_agent:
            if not caller or args['sessionId'] != caller:
                self.error("A handoff proposal must belong to the calling conversation.", 403)
            source = service._session(caller, hydrate=False)
            interruption = source.get("interruptionRevision", 0)
            # Retained observation does not recapture authority or renew work.
            if not self.load(args['requestId']) and origin in {'agent', 'system'}:
                source = service.collaboration.source({}, origin, caller, hydrate=False, allow_descendant=True)
                authority = self.capture(source, origin)
        async with service.lock:
            if admission_check:
                admission_check()
            row = self.load(args["requestId"])
            if action in {PREFIX + "propose", PREFIX + "start"}:
                mode = "full" if authority else "direct" if action == PREFIX + "start" else "review"
                if origin not in {"ui", "user"} and args["sessionId"] != caller:
                    self.error("A handoff proposal must belong to the calling conversation.", 403)
                service._session(args["sessionId"], hydrate=False)
                # A retry observes its original mode, including after revocation.
                if row:
                    self.authorize(row, origin, caller)
                    proposed = {key: value for key, value in args.items() if key != "clientId"}
                    compatible = row.get('mode', 'review') in ({'direct', 'full'} if action == PREFIX + 'start' else {'review'})
                    if (fingerprint(proposed) != row["proposalHash"] or not compatible
                            or row.get('mode') != 'full' and not self.client_can_review(row, args.get("clientId", row["owner"]))):
                        self.error("This request ID was already used for a different handoff.")
                    return self.response(row, include_state, duplicate=True)
                if authority:
                    self.guard_source(authority)
                elif direct_agent:
                    source = service.collaboration.source({}, origin, caller, hydrate=False)
                    blocked = service.assignments.blocked(source)
                    if blocked:
                        self.error(blocked)
                    if (source.get("interruptionRevision", 0) != interruption
                            or source.get("status") in {"stopping", "stopped", "interrupted", "error"}):
                        self.error("The source conversation was stopped before this request was admitted.")
                    service.collaboration.active_binding(source)
                from .agent_canvas import target
                owner = ('conversation:' + caller) if authority else target(
                    service, args["sessionId"], args.get("clientId"), required=True,
                    connected_only=origin not in {"ui", "user"})[0]
                if not owner or (origin in {"ui", "user"} and owner != service.clients.current.get()):
                    self.error("Use the attached client displaying the source conversation.", 403)
                if not args["text"].strip() or not args["title"].strip():
                    self.error("Give this handoff a title and initial brief.")
                pending = sum(row["status"] in UNFINISHED for row in self.visible(owner, args["sessionId"]))
                if pending >= 8:
                    self.error("Review or cancel pending handoffs and inspect uncertain outcomes before proposing another.")
                if len(self.unfinished(owner)) >= MAX_REVIEW_REFERENCES:
                    self.error("There are too many unfinished handoffs in this client. Review pending requests and inspect uncertain outcomes before proposing more.")
                try:
                    checked = self.target(args)
                except (OSError, ValueError):
                    self.error("Choose an available target folder and configuration.")
                row = {"requestId": args["requestId"], "sourceSessionId": args["sessionId"],
                       **({'authority': authority} if authority else {}),
                       "owner": owner, "mode": mode, "proposalHash": fingerprint({key: value for key, value in args.items() if key != "clientId"}),
                       "title": args["title"], "text": args["text"].strip(), **checked,
                       "status": "pending", "creation": "not_started", "delivery": "not_started",
                       "message": "Review the target folder, configuration and exact initial brief before creating this chat.",
                       "createdAt": time.time(), "targetSessionId": str(uuid.uuid4()), "interruptionRevision": 0,
                       "createCommandId": "handoff-create-" + uuid.uuid4().hex,
                       "inputId": "handoff-input-" + uuid.uuid4().hex}
                row["reviewHash"] = fingerprint({key: row[key] for key in (
                    "requestId", "owner", "sourceSessionId", "workspace", "title", "text", "bundle", "selection",
                    "targetIdentity", "configurationHash", "targetSessionId", "createCommandId", "inputId", "mode")})
                if mode in {"direct", "full"}:
                    self.start(row, None if authority else owner)
                else:
                    self.save(row, publish=True)
                return self.response(row, include_state)
            if row is None:
                self.error("This handoff was not found.", 404)
            self.authorize(row, origin, caller, human=action != PREFIX + "read")
            if action == PREFIX + "read":
                if self.known(row):
                    self.save(row, publish=True)
                return self.response(row, include_state)
            if action == PREFIX + "cancel":
                if row["status"] == "cancelled":
                    return self.response(row, include_state, duplicate=True)
                if row["status"] != "pending":
                    self.error("This handoff has already started. Open its chat and use Stop.")
                row.update(status="cancelled", message="Cancelled. No chat was created or brief submitted.")
                self.save(row, publish=True)
                return self.response(row, include_state)
            if args["reviewHash"] != row["reviewHash"]:
                self.error("Review this exact target and brief before creating the chat.")
            if row["status"] != "pending":
                return self.response(row, include_state, duplicate=True)
            self.start(row, service.clients.current.get())
            return self.response(row, include_state)

    def start(self, row, client):
        """Admit once under the service lock, sharing all canonical guards."""
        self.guard(row)
        row.update(status="creating", creation="pending", executionClientId=client,
                   message="Creating the requested chat. Your current chat stays open.")
        try:
            self.save(row, publish=True)
        except Exception:
            # Persistence can succeed before publication fails. Nothing has
            # been scheduled; retain uncertainty without starting on a retry.
            row.update(status="unknown", creation="not_started", delivery="not_started",
                       message="The request was saved, but creation did not start because its update could not be published. Inspect this receipt; nothing was replayed.")
            self.save(row)
            raise
        self.service._task(self.finish(row["requestId"]))

    def response(self, row, include_state, **extra):
        return {"accepted": True, "result": public(row), **extra,
                **({"state": self.service.browser_state()} if include_state else {})}

    async def finish(self, identity):
        service = self.service
        row = self.load(identity)
        try:
            with service.clients.bind(row["executionClientId"]):
                creation = await self.perform(row, 'session.create')
                async with service.lock:
                    if row.get('mode') == 'full':
                        target = service._session(row['targetSessionId'], hydrate=False)
                        row.update(targetAccessRevision=service.access_mode.snapshot(target)['revision'],
                                   targetExecutionRevision=target.get('executionRevision', 0))
                    row.update(status="created", creation="created", createReceipt=creation,
                               message="Chat created. Its initial brief has not yet been accepted.")
                    self.save(row, publish=True)
                    self.guard(row, delivery=True)
                    row.update(status="delivering", delivery="pending", message="Submitting the requested initial brief.")
                    self.save(row, publish=True)
                delivery = await self.perform(row, 'conversation.send')
                async with service.lock:
                    row["sendReceipt"] = delivery
                    if not self.known(row):
                        row.update(status="unknown", delivery="unknown", message="The initial brief is saved but acceptance is not confirmed. Inspect its delivery; nothing was resent.")
                    self.save(row, publish=True)
        except (Exception, asyncio.CancelledError) as exc:
            # A committed canonical receipt or saved message may outlive this task.
            # Never infer no effect from a lost response, cancellation or host exit.
            async with service.lock:
                self.record_failure(row, exc)
            if isinstance(exc, asyncio.CancelledError):
                raise

    def known(self, row):
        """Reconcile exact canonical receipts only; no history or runtime read."""
        before = (row["status"], row["creation"], row["delivery"], row["message"])
        created = self.service.db.execute("SELECT receipt FROM commands WHERE id=?", (row["createCommandId"],)).fetchone()
        if created:
            receipt = json.loads(created[0])
            if receipt.get("accepted") and receipt.get("sessionId") == row["targetSessionId"]:
                row["creation"] = "created"
        sent = self.service.db.execute("SELECT receipt FROM commands WHERE id=?", (row["inputId"],)).fetchone()
        if sent:
            receipt = json.loads(sent[0])
            if receipt.get("sessionId") == row["targetSessionId"] and receipt.get("inputId") == row["inputId"]:
                if receipt.get("delivery") == "accepted":
                    row.update(status="accepted", delivery="accepted", message="Initial brief accepted. This does not mean its requested work has completed.")
                elif receipt.get("delivery") == "failed":
                    row.update(status="failed", delivery="failed", message="The initial brief was not delivered. Inspect its saved chat before deciding what to do; nothing was resent.")
        return before != (row["status"], row["creation"], row["delivery"], row["message"])

    def observe_delivery(self, input_id):
        found = self.service.db.execute("SELECT value FROM new_chat_handoffs WHERE json_extract(value,'$.inputId')=?", (input_id,)).fetchone()
        if found:
            row = json.loads(found[0])
            if self.known(row):
                self.save(row, commit=False)
                self.changed_sources.add(row["sourceSessionId"])

    def record_failure(self, row, exc):
        from .message_delivery import find_message
        from .errors import AppError
        create = self.service.db.execute("SELECT receipt FROM commands WHERE id=?", (row["createCommandId"],)).fetchone()
        if create:
            row["creation"] = "created"
            row["createReceipt"] = json.loads(create[0])
        sent = self.service.db.execute("SELECT receipt FROM commands WHERE id=?", (row["inputId"],)).fetchone()
        target = next((value for value in self.service.state["sessions"] if value["id"] == row["targetSessionId"]), None)
        message = find_message(target, row["inputId"]) if target else None
        delivery = (message or {}).get("delivery", {}).get("status")
        if delivery == "accepted":
            row.update(status="accepted", delivery="accepted", message="The saved brief is confirmed accepted. Nothing was replayed.")
        elif sent or not isinstance(exc, AppError):
            if not create:
                row["creation"] = "unknown"
            row.update(status="failed" if delivery == "failed" else "unknown", delivery=(delivery or "unknown") if sent else "not_started",
                       message="The brief could not be confirmed. Inspect the saved chat before deciding what to do; nothing was resent.")
        else:
            row.update(status="failed", delivery="not_started", message=str(exc) or "The handoff could not finish. Nothing was resent.")
        self.save(row, publish=True)
