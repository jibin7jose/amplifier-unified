"""Explicit ordinary-file additions to a locally receipted private issue.

Separate from initial uploads: every POST has a durable sending fence. A crash
or uncertain response stops the entire request, including not-started phases.
Reconciliation only reads; it neither resumes nor creates a new upload intent.
Audit uses the existing follow-up store, never a second delivery journal.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import re
import time
import stat
from collections import OrderedDict
from urllib.parse import quote

from . import attachments, feedback, feedback_context, feedback_attachments as transport
from .feedback_lifecycle import owned

READS = {"feedback.attachments.review", "feedback.attachments.reconcile"}
ACTIONS = READS | {"feedback.attachments.add"}
DISCLOSURE = (
    "Only these ordinary files will be stored in this repository and "
    "linked in a new comment on this exact issue. Repository access is required. "
    "Files remain in repository history; removing a local file does not delete "
    "uploaded history. Privacy is checked before upload, not guaranteed "
    "atomically against later repository visibility changes."
)
UNKNOWN = "Delivery is uncertain. Use the read-only file delivery check; no upload or comment will be repeated."
MANIFEST_KEYS = ("id", "name", "mime", "size", "sha256")
REFERENCE_KEY = "_feedbackAttachmentRefs"
TERMINAL_HISTORY = 20
UNCERTAIN = {"queued", "sending", "unknown", "partial"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def manifest(rows):
    return sorted(({key: row[key] for key in MANIFEST_KEYS} for row in rows), key=lambda row: row["id"])


def definitions(schema, string):
    identity = {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,100}$"}
    common = {"requestId": identity, "feedbackId": identity}
    confirmed = schema({"id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                        "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}, ["id", "sha256"])
    return {
        "feedback.attachments.review": (
            "Read-only review of the complete ordinary-file follow-up draft for this attached client, or calling Full conversation, and original feedbackId. Freezes the exact issue URL, repository/account IDs, manifest and retention disclosure. Does not upload or consent. Optional under Full; changes require a fresh review requestId.",
            schema(common, ["requestId", "feedbackId"])),
        "feedback.attachments.add": (
            "Add the complete selected ordinary-file set to the original issue as immutable links in an append-only comment. Pass exact confirmedFiles [{id,sha256}]. Full agents, assigned children and trusted systems use their calling conversation's staging without a browser or human confirmation; reviewRequestId is optional and, if supplied, remains an exact destination/manifest precondition. Ask requires an attached client's completed reviewRequestId and explicit user confirmation. No issue PATCH, new issue, excerpt, main-branch write or deletion. Every phase is attempted once; same requestId/payload reads its receipt, unknown phases require read-only reconciliation, never a new ID to retry.",
            schema({**common, "reviewRequestId": identity,
                    "confirmedFiles": {"type": "array", "minItems": 1, "maxItems": transport.MAX_FILES,
                                       "uniqueItems": True, "items": confirmed},
                    "comment": string(16000)}, ["requestId", "feedbackId", "confirmedFiles"])),
        "feedback.attachments.reconcile": (
            "Read-only delivery check for additionRequestId on this client's or calling conversation's exact feedbackId. Conversation-owned receipts remain readable after Stop/revocation without renewing upload authority. Only uniquely verified objects/comment can acknowledge attempted phases. Absence, ambiguity or access failure leaves uncertainty; not-started phases stay not started. Never resumes uploads.",
            schema({**common, "additionRequestId": identity}, ["requestId", "feedbackId", "additionRequestId"])),
    }


def git_sha(kind, data):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).hexdigest()


def tree_binding(rows, blobs):
    """Exact root tree for one directory per staged ID; no base/main tree."""
    entries = []
    for row in rows:
        path = row["id"] + "/" + row["name"]
        blob = blobs[row["id"]]
        subtree = git_sha("tree", b"100644 " + row["name"].encode() + b"\0" + bytes.fromhex(blob))
        entries.extend([{"path": row["id"], "mode": "040000", "type": "tree", "sha": subtree},
                        {"path": path, "mode": "100644", "type": "blob", "sha": blob}])
    root = b"".join(b"40000 " + row["id"].encode() + b"\0" + bytes.fromhex(
        next(item["sha"] for item in entries if item["path"] == row["id"])) for row in sorted(rows, key=lambda row: row["id"]))
    return git_sha("tree", root), entries


class Additions:
    def __init__(self, owner):
        self.owner, self.service = owner, owner.service
        self._projection_cache = OrderedDict()
        for identity, raw in self.service.db.execute("SELECT id,receipt FROM feedback_followups").fetchall():
            receipt = json.loads(raw)
            if receipt.get("action") != "feedback.attachments.add":
                continue
            audit = receipt.get("audit", {})
            for phase in audit.get("phases", {}).values():
                if phase["status"] == "sending":
                    phase["status"] = "unknown"
            if receipt["status"] == "unknown":
                self.summarize(receipt)
                self.store(identity, receipt)

    def client(self):
        from .errors import AppError
        client = self.service.clients.current.get()
        if not client:
            raise AppError("Attach the intended browser before staging or reviewing follow-up files.", 409)
        return client

    def scope(self, origin, caller, *, conversation=False):
        """Keep conversation ownership distinct from an actual browser ID.

        Retained conversation receipts can be read after revocation. New
        staging/delivery still captures current Full authority at admission.
        """
        if (origin in {'agent', 'system'} and caller and
                (conversation or feedback_context.enabled(self.service, origin, caller))):
            self.service._session(caller, hydrate=False)
            return {'sessionId': caller}
        return self.client()

    @staticmethod
    def scope_of(receipt):
        return {'sessionId': receipt['sourceSessionId']} if receipt.get('sourceSessionId') else receipt['clientId']

    @staticmethod
    def binding(owner, *, staging=False):
        # Reuse initial feedback's session field and the existing browser field;
        # never synthesize a browser identifier from a conversation identity.
        if isinstance(owner, dict):
            return ('_sessionId' if staging else 'sourceSessionId'), owner['sessionId']
        return ('_clientId' if staging else 'clientId'), owner

    def load(self, identity):
        raw = self.service.db.execute("SELECT payload,receipt FROM feedback_followups WHERE id=?", (identity,)).fetchone()
        if not raw:
            raise ValueError("No matching file review or addition exists.")
        return tuple(map(json.loads, raw))

    def store(self, identity, receipt):
        self.service.db.execute("UPDATE feedback_followups SET receipt=? WHERE id=?", (json.dumps(receipt), identity))

    def result(self, identity, caller):
        """Exact conversation-owned receipt, including ordinary retry reads."""
        _, receipt = self.load(identity)
        if caller and self.scope_of(receipt) == {'sessionId': caller}:
            return {key: value for key, value in receipt.items() if key not in {'audit', 'authority'}}
        return None

    def references(self, client):
        # This field is outside view/LOCAL_KEYS and is never projected directly.
        return [] if isinstance(client, dict) else self.service.clients.records.get(client, {}).get(REFERENCE_KEY, [])

    def operation(self, identity, *, review_only=False):
        """Return a canonically bound operation, not a guessed receipt ID."""
        from .errors import AppError
        raw = self.service.db.execute(
            "SELECT fingerprint,payload,receipt FROM feedback_followups WHERE id=?", (identity,)).fetchone()
        if not raw:
            return None
        try:
            fingerprint, args, receipt = raw[0], json.loads(raw[1]), json.loads(raw[2])
            action, owner = receipt["action"], self.scope_of(receipt)
            if (action not in ACTIONS or (review_only and action != "feedback.attachments.review") or
                    receipt["requestId"] != identity or args["requestId"] != identity or
                    args["feedbackId"] != receipt["feedbackId"] or fingerprint != digest([action, args, owner])):
                return None
            url, _ = self.owner.followups.target(receipt["feedbackId"])
            if receipt["url"] != url:
                return None
            if action == "feedback.attachments.review" and receipt["status"] == "completed":
                if (receipt["review"]["url"] != url or
                        receipt["review"]["manifest"] != receipt["audit"]["manifest"]):
                    return None
            if action == "feedback.attachments.add":
                # A naked review ID cannot license access to its manifest.
                audit = receipt["audit"]
                if args.get('reviewRequestId'):
                    review = self.operation(args["reviewRequestId"], review_only=True)
                    if (not review or self.scope_of(review[2]) != owner or
                            review[2]["feedbackId"] != receipt["feedbackId"] or
                            review[2]["status"] != "completed" or review[2].get("consumedBy") != identity or
                            review[2]["review"] != audit["review"]):
                        return None
                elif not receipt.get('authority') or not isinstance(owner, dict):
                    return None
                if ((audit.get('review') is not None and audit["manifest"] != audit["review"]["manifest"]) or
                        sorted((item["id"], item["sha256"]) for item in args["confirmedFiles"]) !=
                        [(item["id"], item["sha256"]) for item in audit["manifest"]]):
                    return None
            return fingerprint, args, receipt
        except (AppError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def operation_intent(receipt):
        audit = receipt.get("audit", {})
        return digest([receipt["url"], audit.get("manifest"), audit.get("review")])

    def resolve_operation(self, ref):
        if ref.get("invalid"):
            return None
        row = self.operation(ref["requestId"])
        if not row:
            return None
        fingerprint, args, receipt = row
        if (self.scope_of(receipt) != ref["owner"] or receipt["feedbackId"] != ref["feedbackId"] or
                receipt["action"] != ref["action"] or fingerprint != ref["fingerprint"] or
                ("args" in ref and args != ref["args"]) or
                ("intent" in ref and self.operation_intent(receipt) != ref["intent"]) or
                ("review" in ref and receipt.get("review") != ref["review"])):
            return None
        return receipt

    @staticmethod
    def staging_intent(row):
        return digest([manifest([row]), row.get("_submittedBinding"), row.get("_storedBinding"),
                       row.get("_submittedFingerprint")])

    def _project_file(self, row):
        """Cache display verification only; upload admission always rereads bytes.

        Replacement, writes, permission/type changes and missing files invalidate
        the bounded cache. No file contents are retained in memory.
        """
        directory = attachments.location(self.service.data_dir, row['id'])

        def signature():
            infos = [path.lstat() for path in (directory.parent, directory, directory / 'content')]
            if not all(stat.S_ISDIR(info.st_mode) for info in infos[:2]) or not stat.S_ISREG(infos[2].st_mode):
                raise ValueError('Staged file is unavailable.')
            return tuple((i.st_dev, i.st_ino, i.st_mode, i.st_size, i.st_mtime_ns, i.st_ctime_ns) for i in infos)

        key = digest(manifest([row]))
        before = signature()
        if self._projection_cache.get(key) == before:
            self._projection_cache.move_to_end(key)
            return
        transport.read_verified(self.service.data_dir, row)
        if signature() != before:
            raise ValueError('Staged file changed during verification.')
        self._projection_cache[key] = before
        self._projection_cache.move_to_end(key)
        while len(self._projection_cache) > 256:
            self._projection_cache.popitem(last=False)

    def resolve_staging(self, ref, *, projection=False):
        if ref.get("invalid"):
            return None
        raw = self.service.db.execute(
            "SELECT fingerprint,metadata FROM feedback_attachments WHERE request_id=?", (ref["requestId"],)).fetchone()
        if not raw:
            return None
        try:
            fingerprint, row = raw[0], json.loads(raw[1])
            if (row.get("_clientId") != ref["owner"] or row.get("_feedbackId") != ref["feedbackId"] or
                    row.get("excerpt") or row["url"] != "/api/attachments/" + row["id"]):
                return None
            if "binding" in ref:
                # Missing staging acceptance: compare the submitted name, not
                # a browser MIME or an unsanitized name to the stored filename.
                if (row.get("_submittedBinding") != ref["binding"] or
                        row.get("_storedBinding") != manifest([row])[0] or
                        row.get("_submittedFingerprint") != fingerprint or
                        row["size"] != ref["binding"]["size"] or row["sha256"] != ref["binding"]["sha256"]):
                    return None
            elif fingerprint != ref["fingerprint"] or self.staging_intent(row) != ref["intent"]:
                return None
            if projection:
                self._project_file(row)
            else:
                transport.read_verified(self.service.data_dir, row)
            return row
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def capture_on_attach(self, source_client_id, source_record, target_record):
        """Called only for a new copied client, inside the existing attach save.

        Queries the source's present rows ONCE. Thereafter all resolution is by
        exact row ID and immutable binding, never by ancestor-client membership.
        Writable hints supply uncertainty, not authority; late rows must prove
        the original owner/report/type/payload or submitted-byte binding.
        """
        refs = copy.deepcopy(source_record.get(REFERENCE_KEY, []))
        keys = {(ref["kind"], ref["requestId"]) for ref in refs}

        def retain(ref):
            # IDs are canonical store keys. A copied writable hint on a second
            # reload must not rebind an existing reference to the intermediate
            # client, or create an unresolvable second blocker for the same ID.
            key = (ref["kind"], ref["requestId"])
            if key not in keys:
                refs.append(ref)
                keys.add(key)

        where = "json_extract(receipt,'$.clientId')=? AND json_extract(receipt,'$.action') LIKE 'feedback.attachments.%'"
        rows = self.service.db.execute("SELECT id FROM feedback_followups WHERE " + where +
            " AND json_extract(receipt,'$.status') IN ('queued','sending','unknown','partial') ORDER BY rowid DESC",
            (source_client_id,)).fetchall()
        rows += self.service.db.execute("SELECT id FROM feedback_followups WHERE " + where +
            " AND json_extract(receipt,'$.status') NOT IN ('queued','sending','unknown','partial') ORDER BY rowid DESC LIMIT ?",
            (source_client_id, TERMINAL_HISTORY)).fetchall()
        for (identity,) in rows:
            if row := self.operation(identity):
                fingerprint, _, receipt = row
                ref = {"kind": "operation", "owner": source_client_id, "requestId": identity,
                       "feedbackId": receipt["feedbackId"], "action": receipt["action"],
                       "fingerprint": fingerprint, "intent": self.operation_intent(receipt)}
                if receipt.get("review"):
                    ref["review"] = copy.deepcopy(receipt["review"])
                retain(ref)
        where = "json_extract(metadata,'$._clientId')=? AND json_extract(metadata,'$._feedbackId') IS NOT NULL"
        rows = self.service.db.execute("SELECT request_id,fingerprint,metadata FROM feedback_attachments WHERE " + where +
            " AND json_extract(metadata,'$._selected')=1 ORDER BY rowid DESC", (source_client_id,)).fetchall()
        rows += self.service.db.execute("SELECT request_id,fingerprint,metadata FROM feedback_attachments WHERE " + where +
            " AND json_extract(metadata,'$._selected')=0 ORDER BY rowid DESC LIMIT ?",
            (source_client_id, TERMINAL_HISTORY)).fetchall()
        for identity, fingerprint, raw in rows:
            row = json.loads(raw)
            ref = {"kind": "staging", "owner": source_client_id, "requestId": identity,
                   "feedbackId": row["_feedbackId"], "action": "feedback.attachment.add",
                   "fingerprint": fingerprint, "intent": self.staging_intent(row)}
            if self.resolve_staging(ref):
                retain(ref)
        drafts = source_record.get("view", {}).get("feedbackFollowupDraft", {})
        drafts = drafts.get("fileAdditions", {}) if isinstance(drafts, dict) else {}
        for feedback_id, draft in drafts.items() if isinstance(drafts, dict) else ():
            if not isinstance(draft, dict) or not isinstance(feedback_id, str):
                continue
            args = draft.get("pending")
            if isinstance(args, dict) and isinstance(args.get("requestId"), str):
                ref = {"kind": "operation", "owner": source_client_id, "requestId": args["requestId"],
                       "feedbackId": feedback_id, "action": "feedback.attachments.add",
                       "fingerprint": digest(["feedback.attachments.add", args, source_client_id])}
                if (args.get("feedbackId") == feedback_id and
                        not set(args) - {"requestId", "feedbackId", "reviewRequestId", "confirmedFiles", "comment"}):
                    ref["args"] = copy.deepcopy(args)
                else:
                    ref["invalid"] = True
                retain(ref)
            hints = draft.get("staging", [])
            for hint in hints if isinstance(hints, list) else ():
                if not isinstance(hint, dict) or not isinstance(hint.get("requestId"), str):
                    continue
                ref = {"kind": "staging", "owner": source_client_id, "requestId": hint["requestId"],
                       "feedbackId": feedback_id, "action": "feedback.attachment.add"}
                if (hint.get("feedbackId") == feedback_id and isinstance(hint.get("name"), str) and
                        type(hint.get("size")) is int and 0 < hint["size"] <= transport.MAX_FILE_BYTES and
                        isinstance(hint.get("sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", hint["sha256"])):
                    ref["binding"] = {key: hint[key] for key in ("name", "size", "sha256")}
                else:
                    ref["invalid"] = True
                retain(ref)
        # Prior exact references and unresolved hints are never silently evicted.
        target_record[REFERENCE_KEY] = refs

    def inherited(self, client, kind, identity):
        return next((ref for ref in self.references(client)
                     if ref["kind"] == kind and ref["requestId"] == identity), None)

    def inherited_blocked(self, client, feedback_id):
        for ref in self.references(client):
            if ref["feedbackId"] != feedback_id:
                continue
            if ref["kind"] == "staging":
                if not self.resolve_staging(ref):
                    return True
            else:
                receipt = self.resolve_operation(ref)
                if ref["action"] == "feedback.attachments.add" and (not receipt or receipt["status"] in UNCERTAIN):
                    return True
        return False

    def scoped_rows(self, client, feedback_id):
        field, identity = self.binding(client, staging=True)
        rows = [json.loads(raw[0]) for raw in self.service.db.execute(
            f"SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$.{field}')=? AND json_extract(metadata,'$._feedbackId')=?",
            (identity, feedback_id))]
        return sorted((row for row in rows if row.get("_selected") is True), key=lambda row: row["id"])

    def frozen(self, client, feedback_id):
        if self.inherited_blocked(client, feedback_id):
            return True
        field, identity = self.binding(client)
        return self.service.db.execute(
            "SELECT id FROM feedback_followups WHERE json_extract(receipt,'$.action')='feedback.attachments.add' "
            f"AND json_extract(receipt,'$.{field}')=? AND json_extract(receipt,'$.feedbackId')=? "
            "AND json_extract(receipt,'$.status') IN ('queued','sending','unknown','partial') LIMIT 1",
            (identity, feedback_id)).fetchone()

    def files(self, client, feedback_id):
        rows = self.scoped_rows(client, feedback_id)
        if (not 1 <= len(rows) <= transport.MAX_FILES or any(row.get("excerpt") or not 0 < row["size"] <= transport.MAX_FILE_BYTES for row in rows)
                or sum(row["size"] for row in rows) > transport.MAX_TOTAL_BYTES):
            raise ValueError("Choose ordinary files only: up to 8 files, 8 MiB each and 24 MiB total.")
        return [(row, transport.read_verified(self.service.data_dir, row)) for row in rows]

    def stage(self, action, args, origin=None, caller=None):
        from .errors import AppError
        previous = self.service.db.execute('SELECT fingerprint,metadata FROM feedback_attachments WHERE request_id=?',
            (args['requestId'],)).fetchone() if action == 'feedback.attachment.add' else None
        client = self.scope(origin, caller, conversation=bool(previous and json.loads(previous[1]).get('_sessionId')))
        owner_field, owner_id = self.binding(client, staging=True)
        feedback_id = args["feedbackId"]
        self.owner.followups.target(feedback_id)
        fingerprint = digest([client, args])
        if action == "feedback.attachment.add":
            ref = self.inherited(client, "staging", args["requestId"])
            if ref:
                if (ref["feedbackId"] != feedback_id or not self.resolve_staging(ref) or not previous or
                        previous[0] != digest([ref["owner"], args])):
                    raise AppError("Inherited staging is read only; no matching exact acceptance was observed.", 409)
                return
            if previous:
                if previous[0] != fingerprint:
                    raise AppError("This attachment request ID already belongs to a different file or scope.", 409)
                return feedback_context.public_file(json.loads(previous[1])) if isinstance(client, dict) else None
        if isinstance(client, dict):
            if not feedback_context.capture(self.service, origin, caller):
                raise AppError('Full access is required to change this conversation’s feedback files.', 403)
        if self.frozen(client, feedback_id):
            raise AppError("This file addition is frozen. Unknown phases require a read-only check, not another upload.", 409)
        rows = self.scoped_rows(client, feedback_id)
        if action == "feedback.attachment.remove":
            saved = self.service.db.execute("SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$.id')=?", (args["id"],)).fetchone()
            metadata = json.loads(saved[0]) if saved else {}
            if metadata.get(owner_field) != owner_id or metadata.get("_feedbackId") != feedback_id:
                raise AppError("This file does not belong to the selected client's feedback draft.", 409)
            for row in rows:
                if row["id"] == args["id"]:
                    row["_selected"] = False
                    self.service.db.execute("UPDATE feedback_attachments SET metadata=? WHERE json_extract(metadata,'$.id')=?",
                                            (json.dumps(row), row["id"]))
            return {'id': args['id'], 'selected': False} if isinstance(client, dict) else None
        if len(rows) >= transport.MAX_FILES:
            raise AppError("Attach up to 8 files per feedback addition.")
        try:
            size = len(args["base64"]) // 4 * 3 - (len(args["base64"]) - len(args["base64"].rstrip("=")))
            if sum(row["size"] for row in rows) + size > transport.MAX_TOTAL_BYTES:
                raise ValueError("Feedback attachments can total up to 24 MiB.")
            row = attachments.save(self.service.data_dir, args["name"], args["base64"], max_bytes=transport.MAX_FILE_BYTES)
            # New scope only. Initial staging and its wire/metadata remain unchanged.
            data = base64.b64decode(args["base64"], validate=True)
            row["sha256"] = hashlib.sha256(data).hexdigest()
            row.update({owner_field: owner_id}, _feedbackId=feedback_id, _selected=True)
            row["_submittedBinding"] = {"name": args["name"], "size": len(data), "sha256": row["sha256"]}
            row["_submittedFingerprint"] = fingerprint
            row["_storedBinding"] = manifest([row])[0]
            transport.read_verified(self.service.data_dir, row)
        except (OSError, ValueError):
            raise AppError("The ordinary file could not be staged. Choose a nonempty file up to 8 MiB, within the 24 MiB total limit.") from None
        self.service.db.execute("INSERT INTO feedback_attachments VALUES (?,?,?)", (args["requestId"], fingerprint, json.dumps(row)))
        return feedback_context.public_file(row) if isinstance(client, dict) else None

    def project(self):
        client = self.service.clients.current.get()
        staging = []
        for request_id, raw in self.service.db.execute(
                "SELECT request_id,metadata FROM feedback_attachments WHERE json_extract(metadata,'$._clientId')=?", (client,)):
            row = json.loads(raw)
            staging.append({"requestId": request_id, "feedbackId": row["_feedbackId"],
                            "id": row["id"], "size": row["size"], "sha256": row["sha256"],
                            "selected": row["_selected"]})
        drafts = {}
        for raw in self.service.db.execute("SELECT metadata FROM feedback_attachments WHERE json_extract(metadata,'$._clientId')=? AND json_extract(metadata,'$._selected')=1", (client,)):
            row = json.loads(raw[0])
            drafts.setdefault(row["_feedbackId"], []).append({**{key: row[key] for key in MANIFEST_KEYS}, "url": row["url"]})
        receipts = []
        where = "json_extract(receipt,'$.clientId')=? AND json_extract(receipt,'$.action') LIKE 'feedback.attachments.%'"
        active = self.service.db.execute("SELECT receipt FROM feedback_followups WHERE " + where +
            " AND json_extract(receipt,'$.status') IN ('queued','sending','unknown','partial') ORDER BY rowid DESC", (client,)).fetchall()
        terminal = self.service.db.execute("SELECT receipt FROM feedback_followups WHERE " + where +
            " AND json_extract(receipt,'$.status') NOT IN ('queued','sending','unknown','partial') ORDER BY rowid DESC LIMIT 20", (client,)).fetchall()
        for raw in active + terminal:
            row = json.loads(raw[0])
            receipts.append({key: value for key, value in row.items() if key != "audit"})
        recovery = {}
        for ref in self.references(client):
            report = recovery.setdefault(ref["feedbackId"], {"resumed": True, "blocked": False, "files": [], "hints": []})
            if ref["kind"] == "staging":
                row = self.resolve_staging(ref, projection=True)
                if row:
                    report["files"].append({**{key: row[key] for key in MANIFEST_KEYS},
                                            "requestId": ref["requestId"], "url": row["url"], "readOnly": True})
                else:
                    report["blocked"] = True
            else:
                row = self.resolve_operation(ref)
                if row:
                    receipts.append({**{key: value for key, value in row.items() if key != "audit"}, "readOnly": True})
                    if ref["action"] == "feedback.attachments.add" and row["status"] in UNCERTAIN:
                        report["blocked"] = True
                elif ref["action"] == "feedback.attachments.add":
                    report["blocked"] = True
            if not row:
                report["hints"].append({"requestId": ref["requestId"], "action": ref["action"],
                                        "status": "acceptance_not_observed"})
        return {"attachmentDrafts": drafts, "additions": receipts, "stagingReceipts": staging,
                "attachmentRecovery": recovery}

    def accept(self, action, args, origin, caller=None):
        from .errors import AppError
        previous = self.service.db.execute('SELECT fingerprint,receipt FROM feedback_followups WHERE id=?',
            (args['requestId'],)).fetchone()
        addition = None
        if action == 'feedback.attachments.reconcile':
            try:
                _, addition = self.load(args['additionRequestId'])
            except ValueError as exc:
                raise AppError(str(exc), 409) from None
        retained = json.loads(previous[1]) if previous else addition or {}
        client = self.scope(origin, caller, conversation=bool(retained.get('sourceSessionId')))
        fingerprint = digest([action, args, client])
        ref = self.inherited(client, "operation", args["requestId"])
        if ref:
            if (action != ref["action"] or args["feedbackId"] != ref["feedbackId"] or
                    digest([action, args, ref["owner"]]) != ref["fingerprint"] or
                    not self.resolve_operation(ref)):
                raise AppError("Inherited requests are read only; no matching exact acceptance was observed.", 409)
            return False
        if previous:
            if previous[0] != fingerprint:
                raise AppError("This file request ID already belongs to different contents or client.", 409)
            return False
        authority = feedback_context.capture(self.service, origin, caller) if isinstance(client, dict) and action != 'feedback.attachments.reconcile' else None
        if isinstance(client, dict) and action != 'feedback.attachments.reconcile' and not authority:
            raise AppError('Full access is required to accept new conversation-owned file delivery.', 403)
        url, _ = self.owner.followups.target(args["feedbackId"])
        owner_field, owner_id = self.binding(client)
        receipt = {"requestId": args["requestId"], "feedbackId": args["feedbackId"], owner_field: owner_id,
                   "action": action, "origin": origin, "status": "queued", "url": url,
                   "createdAt": time.time(), "message": "Checking the exact reviewed file destination…"}
        if authority:
            receipt['authority'] = authority
        if isinstance(client, dict) and origin == 'agent':
            from .collaboration import PRINCIPAL
            receipt['runtimeSessionId'] = PRINCIPAL.get()
        review = None
        try:
            if action == "feedback.attachments.reconcile":
                inherited = self.inherited(client, "operation", args["additionRequestId"])
                readable = inherited and self.resolve_operation(inherited)
                if (addition["action"] != "feedback.attachments.add" or
                        (self.scope_of(addition) != client and not readable) or
                        addition["feedbackId"] != args["feedbackId"] or addition["status"] not in {"unknown", "partial"}):
                    raise ValueError("Choose an uncertain file addition for this client and report.")
            else:
                if self.frozen(client, args["feedbackId"]):
                    raise ValueError("This report's file addition is frozen; use a read-only delivery check.")
                files = self.files(client, args["feedbackId"])
                receipt["audit"] = {"manifest": manifest([row for row, _ in files])}
                if action == "feedback.attachments.add":
                    approved = sorted((row["id"], row["sha256"]) for row in args["confirmedFiles"])
                    actual = [(row["id"], row["sha256"]) for row in receipt["audit"]["manifest"]]
                    if args.get('reviewRequestId'):
                        _, review = self.load(args['reviewRequestId'])
                        if (review['action'] != 'feedback.attachments.review' or review['status'] != 'completed' or
                                self.scope_of(review) != client or review['feedbackId'] != args['feedbackId'] or
                                review.get('consumedBy') or review['review']['manifest'] != receipt['audit']['manifest'] or
                                review['review']['url'] != url):
                            raise ValueError('The exact review changed or was consumed.')
                    if approved != actual or review is None and authority is None:
                        raise ValueError("The complete file set changed or its review was already used. Review the exact files again.")
                    receipt["audit"].update(review=copy.deepcopy(review['review']) if review else None, phases={})
                    prefix = "repos/" + url.removeprefix('https://github.com/').split('/issues/')[0]
                    phases = [("blob:" + row["id"], prefix + "/git/blobs") for row, _ in files]
                    phases += [("tree", prefix + "/git/trees"), ("commit", prefix + "/git/commits"),
                               ("ref", prefix + "/git/refs"), ("comment", "repos/" + url.removeprefix("https://github.com/") + "/comments")]
                    receipt["audit"]["phases"] = {key: {"phaseId": args["requestId"] + ":" + key, "status": "not_started", "endpoint": endpoint} for key, endpoint in phases}
                    self.summarize(receipt)
        except (ValueError, OSError, KeyError, TypeError):
            raise AppError("The reviewed destination or exact ordinary-file review is unavailable, changed, frozen, or belongs to another scope. Review again; nothing was sent.", 409) from None
        active = self.service.db.execute("SELECT count(*) FROM feedback_followups WHERE json_extract(receipt,'$.status') IN ('queued','sending')").fetchone()[0]
        if active >= 8:
            raise AppError("Wait for a feedback check or follow-up to finish.", 409)
        if action == "feedback.attachments.add" and review is not None:
            review["consumedBy"] = args["requestId"]
            self.store(args["reviewRequestId"], review)
        self.service.db.execute("INSERT INTO feedback_followups VALUES (?,?,?,?)", (args["requestId"], fingerprint, json.dumps(args), json.dumps(receipt)))
        self.owner.refresh()
        return True

    async def destination(self, feedback_id):
        url, number = self.owner.followups.target(feedback_id)
        repository = url.removeprefix("https://github.com/").split("/issues/")[0]
        user = await feedback.github_api("user", None)
        issue = await feedback.github_api("repos/" + url.removeprefix("https://github.com/"), None)
        repo = await feedback.github_api("repos/" + repository, None)
        if (not owned(issue, user, feedback_id, url) or issue["number"] != number or type(issue.get("id")) is not int or
                type(repo.get("private")) is not bool or repo.get("full_name") != repository or type(repo.get("id")) is not int):
            raise ValueError("Unverified issue destination.")
        return {"url": url, "issueId": issue["id"], "issueNumber": number,
                "repository": {"id": repo["id"], "full_name": repository, "private": repo["private"]},
                "account": {"id": user["id"], "login": str(user.get("login", ""))}, "disclosure": ('PUBLIC: Anyone can read these files. ' if not repo['private'] else 'Private: Repository access is required. ') + DISCLOSURE}

    @staticmethod
    def summarize(receipt):
        phases = receipt.get("audit", {}).get("phases", {})
        receipt["phases"] = [{"phaseId": row["phaseId"], "status": row["status"]} for row in phases.values()]
        ref = phases.get("ref", {})
        receipt["filesStored"] = ref.get("status") == "succeeded"
        receipt["commentStatus"] = phases.get("comment", {}).get("status", "not_started")

    @staticmethod
    def merge_reconciled(latest, observed):
        """Merge positive read evidence, never an older uncertain snapshot.

        Called under the service lock, with no network await. Immutable intent
        and each attempted phase's input binding must still match disk.
        """
        for key in ("requestId", "feedbackId", "clientId", "sourceSessionId", "action", "url"):
            if latest.get(key) != observed.get(key):
                raise ValueError("Reconciliation scope changed.")
        for key in ("manifest", "review"):
            if latest["audit"][key] != observed["audit"][key]:
                raise ValueError("Reconciliation intent changed.")
        current, incoming = latest["audit"]["phases"], observed["audit"]["phases"]
        if current.keys() != incoming.keys():
            raise ValueError("Reconciliation phases changed.")
        for key, phase in incoming.items():
            saved = current[key]
            for field in ("phaseId", "endpoint", "inputFingerprint", "prerequisites"):
                if saved.get(field) != phase.get(field):
                    raise ValueError("Reconciliation input binding changed.")
            if phase["status"] != "succeeded":
                continue
            if saved["status"] not in {"unknown", "succeeded"}:
                raise ValueError("An unattempted phase cannot be acknowledged.")
            if saved.get("result") is not None and saved["result"] != phase["result"]:
                raise ValueError("Reconciliation result conflicts with known evidence.")
            if saved["status"] == "unknown":
                current[key] = copy.deepcopy(phase)
        if current["ref"]["status"] == "succeeded" and observed.get("attachments"):
            latest["attachments"] = observed["attachments"]
        if current["comment"]["status"] == "succeeded":
            latest.update(current["comment"]["result"])
        all_done = all(phase["status"] == "succeeded" for phase in current.values())
        latest.update(status="submitted" if all_done else "partial" if current["ref"]["status"] == "succeeded" else "unknown",
                      message="File addition delivery verified by read-only checks." if all_done else UNKNOWN)
        return latest

    async def persist(self, identity, receipt, *, reconciled=False, check_client=None, admit=False):
        async with self.service.lock:
            if admit and receipt.get('authority'):
                feedback_context.check(self.service, receipt['authority'])
            if reconciled:
                _, latest = self.load(identity)
                if check_client and self.scope_of(latest) != check_client:
                    ref = self.inherited(check_client, "operation", identity)
                    if not ref or not self.resolve_operation(ref):
                        raise ValueError("Inherited binding changed during the read-only check.")
                receipt = self.merge_reconciled(latest, receipt)
            receipt["updatedAt"] = time.time()
            if receipt["action"] == "feedback.attachments.add":
                self.summarize(receipt)
            self.store(identity, receipt)
            self.owner.refresh()
            # This commits the SQLite fence BEFORE control reaches the POST.
            self.service._publish_changes(globals={"feedback"})
        return receipt

    async def phase(self, identity, receipt, key, payload, prerequisites, validate, verify=None):
        phase = receipt["audit"]["phases"][key]
        if phase["status"] != "not_started":
            raise ValueError("An attempted phase cannot be replayed.")
        phase.update(status="sending", inputFingerprint=digest(payload), prerequisites=prerequisites)
        await self.persist(identity, receipt, admit=True)
        if receipt.get('authority'):
            feedback_context.check(self.service, receipt['authority'])
        result = await feedback.github_api(phase["endpoint"], payload)
        phase["result"] = validate(result)
        # Retain immutable returned evidence before any follow-up await. A
        # returned object is not yet an acknowledgement of actor/account.
        await self.persist(identity, receipt)
        if verify is not None:
            await verify(phase["result"])
        # Do not mark the working copy succeeded until the acknowledgement is
        # durably saved. A failed save must stop later phases, even in-process.
        acknowledged = copy.deepcopy(receipt)
        acknowledged["audit"]["phases"][key]["status"] = "succeeded"
        await self.persist(identity, acknowledged)
        phase["status"] = "succeeded"
        return phase["result"]

    @staticmethod
    def object(result, repository, kind, expected=None):
        sha = transport.object_sha(result)
        if (expected is not None and sha != expected or
                result.get("url") != f"https://api.github.com/repos/{repository}/git/{kind}/{sha}"):
            raise ValueError("Invalid object binding.")
        return {"sha": sha, "url": result["url"]}

    @staticmethod
    def comment(result, review, body):
        identity = result.get("id")
        url = review["url"] + f"#issuecomment-{identity}"
        if (type(identity) is not int or identity <= 0 or result.get("html_url") != url or
                result.get("issue_url") != "https://api.github.com/repos/" + review["url"].removeprefix("https://github.com/") or
                result.get("user", {}).get("id") != review["account"]["id"] or result.get("body") != body):
            raise ValueError("Invalid comment binding.")
        return {"commentId": identity, "commentUrl": url}

    async def commit_actor(self, repository, sha, account_id):
        result = await feedback.github_api("repos/" + repository + "/commits/" + sha, None)
        if (result.get("sha") != sha or result.get("url") != f"https://api.github.com/repos/{repository}/commits/{sha}" or
                type(result.get("committer", {}).get("id")) is not int or result["committer"]["id"] != account_id):
            raise ValueError("Unverified commit actor.")

    async def run(self, identity):
        # Atomically claim the one queued execution, including direct callers.
        async with self.service.lock:
            args, receipt = self.load(identity)
            if receipt["status"] != "queued":
                return
            receipt["status"] = "sending"
            self.store(identity, receipt)
            self.owner.refresh()
            self.service._publish_changes(globals={"feedback"})
        try:
            if receipt["action"] == "feedback.attachments.reconcile":
                await self.reconcile(args, receipt)
            elif receipt["action"] == "feedback.attachments.review":
                target = await self.destination(args["feedbackId"])
                async with self.service.lock:
                    current = manifest([row for row, _ in self.files(self.scope_of(receipt), args["feedbackId"])])
                    if current != receipt["audit"]["manifest"] or self.frozen(self.scope_of(receipt), args["feedbackId"]):
                        raise ValueError("Draft changed during review.")
                receipt.update(status="completed", review={**target, "manifest": current}, message=(
                    "Exact destination and files verified. Nothing uploaded; Full access authorizes a separately requested addition."
                    if receipt.get('authority') else "Exact reviewed destination and files reviewed. Nothing uploaded; confirmation is still required."))
            else:
                await self.send(args, receipt)
        except BaseException as exc:
            if not isinstance(exc, (Exception, asyncio.CancelledError)):
                raise
            if receipt["action"] == "feedback.attachments.add":
                phases = receipt["audit"]["phases"]
                _, durable = self.load(identity)
                for key, phase in phases.items():
                    saved = durable["audit"]["phases"][key]
                    if saved['status'] == 'not_started':
                        phases[key] = saved
                        continue
                    if saved["status"] == "succeeded":
                        phases[key] = saved
                        continue
                    if phase["status"] == "sending":
                        phase["status"] = "unknown"
                attempted = any(phase["status"] != "not_started" for phase in phases.values())
                receipt.update(status=("partial" if phases["ref"]["status"] == "succeeded" else "unknown") if attempted else "failed",
                               message=UNKNOWN if attempted else "The exact reviewed destination or staged bytes changed or could not be verified. Nothing was uploaded. Review again.")
            else:
                receipt.update(status="failed", message="The read-only check could not verify delivery or the reviewed destination. No upload or comment was sent.")
            await self.persist(identity, receipt)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        await self.persist(identity, receipt)

    async def send(self, args, receipt):
        identity, audit = args["requestId"], receipt["audit"]
        target = await self.destination(args["feedbackId"])
        files = self.files(self.scope_of(receipt), args["feedbackId"])
        selected = manifest([row for row, _ in files])
        if selected != audit['manifest']:
            raise ValueError('The selected file manifest changed.')
        if audit['review'] is None and receipt.get('authority'):
            audit['review'] = {**target, 'manifest': selected}
            await self.persist(identity, receipt)
        if {**target, "manifest": selected} != audit["review"]:
            raise ValueError("Fresh review required.")
        # No await between complete byte/scope verification and the first fence.
        review, repository = audit["review"], target["repository"]["full_name"]
        blobs, tree = {}, []
        for row, data in files:
            expected = git_sha("blob", data)
            result = await self.phase(identity, receipt, "blob:" + row["id"],
                {"encoding": "base64", "content": base64.b64encode(data).decode("ascii")},
                {"id": row["id"], "sha256": row["sha256"], "expectedSha": expected},
                lambda result, expected=expected: self.object(result, repository, "blobs", expected))
            blobs[row["id"]] = result["sha"]
            tree.append({"path": row["id"] + "/" + row["name"], "mode": "100644", "type": "blob", "sha": result["sha"]})
        expected_tree, entries = tree_binding(audit["manifest"], blobs)
        tree_result = await self.phase(identity, receipt, "tree", {"tree": tree},
            {"expectedSha": expected_tree, "entries": entries},
            lambda result: self.object(result, repository, "trees", expected_tree))
        commit_payload = {"message": "Feedback attachments " + identity, "tree": tree_result["sha"], "parents": []}
        def validate_commit(result):
            if result.get("tree", {}).get("sha") != tree_result["sha"] or result.get("parents") != [] or result.get("message") != commit_payload["message"]:
                raise ValueError("Invalid isolated commit.")
            return self.object(result, repository, "commits")
        commit = await self.phase(identity, receipt, "commit", commit_payload, commit_payload, validate_commit,
                                  lambda result: self.commit_actor(repository, result["sha"], review["account"]["id"]))
        ref_name = "refs/heads/feedback-assets/" + identity
        def validate_ref(result):
            if (result.get("ref") != ref_name or result.get("url") != f"https://api.github.com/repos/{repository}/git/refs/heads/feedback-assets/{identity}" or
                    result.get("object", {}).get("type") != "commit" or transport.object_sha(result.get("object", {})) != commit["sha"]):
                raise ValueError("Invalid isolated attachment ref.")
            return {"ref": ref_name, "sha": commit["sha"], "url": result["url"]}
        await self.phase(identity, receipt, "ref", {"ref": ref_name, "sha": commit["sha"]}, {"commit": commit["sha"], "ref": ref_name}, validate_ref)
        receipt["attachments"] = [{**row, "visibility": "private" if review["repository"]["private"] else "public",
            "url": f"https://github.com/{repository}/blob/{commit['sha']}/" + quote(row["id"] + "/" + row["name"], safe="/")} for row in audit["manifest"]]
        body = args.get("comment", "").strip() + transport.markdown(receipt["attachments"])
        body += f"\n\n<!-- amplifier-feedback-attachments:{identity} -->"
        audit["commentBody"] = body
        result = await self.phase(identity, receipt, "comment", {"body": body},
            {"issueId": review["issueId"], "accountId": review["account"]["id"], "bodyFingerprint": digest(body)},
            lambda result: self.comment(result, review, body))
        receipt.update(result, status="submitted", message="Selected files linked in a new comment. Original report and maintainer edits were preserved.")
        # Successful files leave the staging selection, not repository history.
        async with self.service.lock:
            for row, _ in files:
                row["_selected"] = False
                self.service.db.execute("UPDATE feedback_attachments SET metadata=? WHERE json_extract(metadata,'$.id')=?", (json.dumps(row), row["id"]))

    async def reconcile(self, args, check):
        _, receipt = self.load(args["additionRequestId"])
        if self.scope_of(receipt) != self.scope_of(check):
            ref = self.inherited(self.scope_of(check), "operation", args["additionRequestId"])
            if not ref or not self.resolve_operation(ref):
                raise ValueError("The exact inherited addition binding is unavailable.")
        audit, identity = receipt["audit"], receipt["requestId"]
        review = audit["review"]
        if await self.destination(args["feedbackId"]) != {key: value for key, value in review.items() if key != "manifest"}:
            raise ValueError("Destination/account changed.")
        repository, phases = review["repository"]["full_name"], audit["phases"]
        prefix = "repos/" + repository
        verified = []
        # Objects with exact known SHA/input can be verified independently.
        # A lost commit reply with no known SHA stays unknown.
        for key, phase in phases.items():
            if phase["status"] != "unknown" or key in {"commit", "ref", "comment"}:
                continue
            try:
                expected = phase["prerequisites"]["expectedSha"]
                if key.startswith("blob:"):
                    result = await feedback.github_api(prefix + "/git/blobs/" + expected, None)
                    self.object(result, repository, "blobs", expected)
                    if result.get("encoding") != "base64":
                        raise ValueError("Invalid blob encoding.")
                    data = base64.b64decode("".join(result["content"].split()), validate=True)
                    row = next(row for row in audit["manifest"] if row["id"] == phase["prerequisites"]["id"])
                    if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"] or git_sha("blob", data) != expected:
                        raise ValueError("Invalid blob bytes.")
                else:
                    result = await feedback.github_api(prefix + "/git/trees/" + expected + "?recursive=1", None)
                    self.object(result, repository, "trees", expected)
                    entries = [{key: item[key] for key in ("path", "mode", "type", "sha")} for item in result["tree"]]
                    if result.get("truncated") is not False or sorted(entries, key=lambda item: item["path"]) != sorted(phase["prerequisites"]["entries"], key=lambda item: item["path"]):
                        raise ValueError("Invalid manifest tree.")
                phase.update(status="succeeded", result={"sha": expected, "url": result["url"]}, reconciledBy=args["requestId"])
                verified.append(key)
            except Exception:
                pass  # Absence/inaccessibility is uncertainty, never failure/resend.
        commit_phase = phases["commit"]
        if commit_phase["status"] == "unknown" and commit_phase.get("result"):
            try:
                sha = transport.object_sha(commit_phase["result"])
                commit = await feedback.github_api(prefix + "/git/commits/" + sha, None)
                result = self.object(commit, repository, "commits", sha)
                binding = commit_phase["prerequisites"]
                if (commit.get("tree", {}).get("sha") != binding["tree"] or
                        commit.get("parents") != [] or commit.get("message") != binding["message"] or
                        phases["tree"]["status"] != "succeeded"):
                    raise ValueError("Invalid isolated commit binding.")
                await self.commit_actor(repository, sha, review["account"]["id"])
                commit_phase.update(status="succeeded", result=result, reconciledBy=args["requestId"])
                verified.append("commit")
            except Exception:
                pass
        if phases["ref"]["status"] in {"unknown", "succeeded"}:
            try:
                ref_name = "refs/heads/feedback-assets/" + identity
                ref = await feedback.github_api(prefix + "/git/ref/heads/feedback-assets/" + identity, None)
                sha = transport.object_sha(ref["object"])
                if (ref.get("ref") != ref_name or ref["object"].get("type") != "commit" or
                        ref.get("url") != f"https://api.github.com/repos/{repository}/git/refs/heads/feedback-assets/{identity}" or
                        sha != phases["ref"]["prerequisites"]["commit"]):
                    raise ValueError("Invalid ref binding.")
                commit = await feedback.github_api(prefix + "/git/commits/" + sha, None)
                self.object(commit, repository, "commits", sha)
                binding = phases["commit"]["prerequisites"]
                if (phases["commit"]["status"] != "succeeded" or commit.get("tree", {}).get("sha") != binding["tree"] or
                        commit.get("parents") != [] or commit.get("message") != binding["message"]):
                    raise ValueError("Invalid commit binding.")
                await self.commit_actor(repository, sha, review["account"]["id"])
                # Re-read the full known tree and every blob; ref alone is not proof.
                tree = await feedback.github_api(prefix + "/git/trees/" + binding["tree"] + "?recursive=1", None)
                self.object(tree, repository, "trees", binding["tree"])
                entries = [{key: item[key] for key in ("path", "mode", "type", "sha")} for item in tree["tree"]]
                if tree.get("truncated") is not False or sorted(entries, key=lambda item: item["path"]) != sorted(phases["tree"]["prerequisites"]["entries"], key=lambda item: item["path"]):
                    raise ValueError("Invalid full manifest.")
                for row in audit["manifest"]:
                    expected = phases["blob:" + row["id"]]["result"]["sha"]
                    blob = await feedback.github_api(prefix + "/git/blobs/" + expected, None)
                    self.object(blob, repository, "blobs", expected)
                    data = base64.b64decode("".join(blob["content"].split()), validate=True)
                    if blob.get("encoding") != "base64" or len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                        raise ValueError("Invalid stored bytes.")
                if phases["ref"]["status"] == "unknown":
                    phases["ref"].update(status="succeeded", result={"ref": ref_name, "sha": sha, "url": ref["url"]}, reconciledBy=args["requestId"])
                    verified.append("ref")
                receipt["attachments"] = [{**row, "visibility": "private" if review["repository"]["private"] else "public", "url": f"https://github.com/{repository}/blob/{sha}/" + quote(row["id"] + "/" + row["name"], safe="/")} for row in audit["manifest"]]
            except Exception:
                pass
        comment_phase = phases["comment"]
        if comment_phase["status"] == "unknown":
            matches, complete = {}, False
            endpoint = "repos/" + review["url"].removeprefix("https://github.com/") + "/comments"
            try:
                for page in range(1, 6):
                    rows = await feedback.github_api(endpoint + f"?per_page=100&page={page}", None)
                    if not isinstance(rows, list) or len(rows) > 100:
                        raise ValueError("Invalid comments page.")
                    for row in rows:
                        if f"<!-- amplifier-feedback-attachments:{identity} -->" in (row.get("body") or ""):
                            result = self.comment(row, review, audit["commentBody"])
                            matches[result["commentId"]] = result
                    if len(rows) < 100:
                        complete = True
                        break
                if complete and len(matches) == 1:
                    result = next(iter(matches.values()))
                    comment_phase.update(status="succeeded", result=result, reconciledBy=args["requestId"])
                    receipt.update(result)
                    verified.append("comment")
            except Exception:
                pass
        all_done = all(phase["status"] == "succeeded" for phase in phases.values())
        receipt.update(status="submitted" if all_done else "partial" if phases["ref"]["status"] == "succeeded" else "unknown",
                       message="File addition delivery verified by read-only checks." if all_done else UNKNOWN)
        receipt = await self.persist(identity, receipt, reconciled=True, check_client=self.scope_of(check))
        all_done = receipt["status"] == "submitted"
        if all_done and self.scope_of(check) == self.scope_of(receipt):
            async with self.service.lock:
                for row in self.scoped_rows(self.scope_of(receipt), args["feedbackId"]):
                    if row["id"] in {item["id"] for item in audit["manifest"]}:
                        row["_selected"] = False
                        self.service.db.execute("UPDATE feedback_attachments SET metadata=? WHERE json_extract(metadata,'$.id')=?",
                                                (json.dumps(row), row["id"]))
        check.update(status="completed", verifiedPhases=verified,
                     message="Read-only delivery check complete. Not-started phases remain stopped; nothing was uploaded or reposted.")
