"""SQLite operation receipts/output with bounded reads and explicit uncertainty."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

ACTIVE = {"queued", "running", "cancel_requested"}
TERMINAL = {"completed", "failed", "cancelled", "outcome_unknown", "interrupted"}
SETTLED = {"completed", "failed", "cancelled"}


class OperationJournal:
    def __init__(self, path, *, max_output_bytes=10_000_000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS operations (
              id TEXT PRIMARY KEY, session_id TEXT NOT NULL, value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS operations_session ON operations(session_id);
            CREATE INDEX IF NOT EXISTS operations_resources ON operations(
              session_id, json_extract(value,'$.createdAt') DESC, id DESC);
            CREATE INDEX IF NOT EXISTS operations_output_expiry ON operations(
              json_extract(value,'$.outputExpiresAt'), id)
              WHERE json_extract(value,'$.outputExpiresAt') IS NOT NULL
                AND json_extract(value,'$.outputAvailability') IS NOT 'expired'
                AND json_extract(value,'$.retainedChunks')>0;
            CREATE TABLE IF NOT EXISTS operation_events (
              operation_id TEXT NOT NULL, sequence INTEGER NOT NULL,
              event_id TEXT NOT NULL, value TEXT NOT NULL,
              PRIMARY KEY(operation_id, sequence), UNIQUE(operation_id, event_id));
            CREATE TABLE IF NOT EXISTS operation_output (
              operation_id TEXT NOT NULL, cursor INTEGER NOT NULL,
              bytes INTEGER NOT NULL, value TEXT NOT NULL,
              PRIMARY KEY(operation_id, cursor));
        """)
        self.lock = threading.RLock()
        self.max_output_bytes = max_output_bytes

    def _record(self, identity, session_id):
        row = self.db.execute(
            "SELECT session_id,value FROM operations WHERE id=?", (identity,)
        ).fetchone()
        if row is None or row[0] != session_id:
            raise ValueError("Operation not found in this conversation")
        return json.loads(row[1])

    def _save(self, record):
        self.db.execute(
            "INSERT OR REPLACE INTO operations VALUES (?,?,?)",
            (record["id"], record["sessionId"], json.dumps(record)),
        )

    def recover(
        self, session_id=None, reason="The host restarted; work was not replayed."
    ):
        """Invalidate only unobserved work. Preserve exact terminal evidence."""
        with self.lock, self.db:
            rows = self.db.execute(
                "SELECT value FROM operations"
                + (" WHERE session_id=?" if session_id else ""),
                (session_id,) if session_id else (),
            ).fetchall()
            for row in rows:
                value = json.loads(row[0])
                if value["state"] in ACTIVE:
                    value.update(
                        state="outcome_unknown",
                        interruptionReason=reason,
                        updatedAt=time.time(),
                        revision=value["revision"] + 1,
                        outputComplete=False,
                        controlAvailable=False,
                    )
                    self._save(value)

    def ingest(self, session_id, runtime_session_id, event, *, output_keep_days=None):
        if output_keep_days is not None and (type(output_keep_days) is not int or not 1 <= output_keep_days <= 3650):
            raise ValueError("Output retention must be 1–3650 days or disabled")
        if (
            event.get("schemaVersion") != 1
            or not isinstance(event.get("source"), str)
            or not isinstance(event.get("kind"), str)
            or not 1 <= len(event["source"]) <= 100
            or not 1 <= len(event["kind"]) <= 50
        ):
            raise ValueError("Unsupported operation observation schema")
        identity = event.get("operationId")
        sequence = event.get("sequence")
        event_id = event.get("eventId")
        if (
            not isinstance(identity, str)
            or not 1 <= len(identity) <= 200
            or not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 240
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
        ):
            raise ValueError("Invalid operation event identity or sequence")
        phase = event.get("phase")
        if phase not in {"started", "output", "state", "finished"}:
            raise ValueError("Invalid operation event phase")
        encoded = json.dumps(event)
        if len(encoded.encode()) > 65536:
            raise ValueError("Operation event exceeds 64 KB")
        with self.lock, self.db:
            previous = self.db.execute(
                "SELECT session_id,value FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if previous and previous[0] != session_id:
                raise ValueError("Operation belongs to another conversation")
            if previous:
                prior = json.loads(previous[1])
                if (
                    prior["runtimeSessionId"] != runtime_session_id
                    or prior.get("ownerId") != event.get("ownerId")
                    or prior["producer"] != event["source"]
                    or prior["kind"] != event["kind"]
                ):
                    raise ValueError("Operation observation owner or producer changed")
            duplicate = self.db.execute(
                "SELECT value FROM operation_events WHERE operation_id=? AND (sequence=? OR event_id=?)",
                (identity, sequence, event_id),
            ).fetchone()
            if duplicate:
                if duplicate[0] not in {
                    encoded,
                    "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
                }:
                    raise ValueError("Conflicting duplicate operation event")
                return self._record(identity, session_id), False
            value = (
                json.loads(previous[1])
                if previous
                else {
                    "id": identity,
                    "sessionId": session_id,
                    "runtimeSessionId": runtime_session_id,
                    "source": event["kind"],
                    "producer": event["source"],
                    "sourceId": identity,
                    "kind": event["kind"],
                    "state": "running",
                    "createdAt": time.time(),
                    "revision": 0,
                    "sequence": 0,
                    "outputComplete": False,
                    "outputAvailability": "retained",
                    "captureComplete": True,
                    "controlAvailable": True,
                    "earliestCursor": 0,
                    "latestCursor": 0,
                    "droppedOutputBytes": 0,
                    "totalOutputBytes": 0,
                    "retainedOutputBytes": 0,
                    "retainedChunks": 0,
                    "ownerId": event.get("ownerId"),
                    "returncode": None,
                }
            )
            # Policy is prospective: later observations never classify an old
            # operation. Reads and retries cannot extend this captured lifetime.
            if previous is None and output_keep_days is not None:
                value["outputRetentionDays"] = output_keep_days
            if value["runtimeSessionId"] != runtime_session_id or value.get(
                "ownerId"
            ) != event.get("ownerId"):
                raise ValueError("Operation observation owner changed")
            if sequence <= value["sequence"]:
                raise ValueError("Out-of-order operation observation")
            if sequence != value["sequence"] + 1 or event.get(
                "observerDroppedEvents", 0
            ):
                value["captureComplete"] = False
            # Only real final evidence from the same bound mount can settle a
            # previous unknown outcome. Observation never restores execution.
            if value["state"] in TERMINAL and (
                phase != "finished" or value["state"] != "outcome_unknown"
            ):
                raise ValueError("Operation already has terminal evidence")
            if phase == "output":
                chunk = event.get("chunk")
                if (
                    not isinstance(chunk, dict)
                    or chunk.get("stream") not in {"stdout", "stderr"}
                    or not isinstance(chunk.get("text"), str)
                ):
                    raise ValueError("Invalid output chunk")
                cursor = chunk.get("cursor")
                size = chunk.get("source_bytes")
                if (
                    isinstance(cursor, bool)
                    or not isinstance(cursor, int)
                    or cursor < value["latestCursor"]
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                    or not 0 <= size <= 4096
                    or chunk.get("next_cursor") != cursor + 1
                ):
                    raise ValueError("Invalid output cursor or size")
                if (
                    cursor != value["latestCursor"]
                    or chunk.get("binary_output_withheld")
                    or chunk.get("encoding_loss")
                ):
                    value["captureComplete"] = False
                storage_bytes = max(size, len(chunk["text"].encode("utf-8")))
                self.db.execute(
                    "INSERT INTO operation_output VALUES (?,?,?,?)",
                    (identity, cursor, storage_bytes, json.dumps(chunk)),
                )
                value["latestCursor"] = cursor + 1
                value["totalOutputBytes"] += size
                value["retainedOutputBytes"] += storage_bytes
                # Budget by bytes and row count so one-byte writes stay bounded.
                value["retainedChunks"] = value.get("retainedChunks", 0) + 1
                while (
                    value["retainedOutputBytes"] > self.max_output_bytes
                    or value["retainedChunks"] > 10000
                ):
                    old_cursor, old_size, old_json = self.db.execute(
                        "SELECT cursor,bytes,value FROM operation_output WHERE operation_id=? ORDER BY cursor LIMIT 1",
                        (identity,),
                    ).fetchone()
                    self.db.execute(
                        "DELETE FROM operation_output WHERE operation_id=? AND cursor=?",
                        (identity, old_cursor),
                    )
                    value["retainedOutputBytes"] -= old_size
                    value["retainedChunks"] -= 1
                    value["droppedOutputBytes"] += json.loads(old_json)["source_bytes"]
                oldest = self.db.execute(
                    "SELECT MIN(cursor) FROM operation_output WHERE operation_id=?",
                    (identity,),
                ).fetchone()[0]
                value["earliestCursor"] = (
                    oldest if oldest is not None else value["latestCursor"]
                )
            else:
                status = event.get("status", {})
                state = status.get("state", "running")
                if state not in ACTIVE | TERMINAL:
                    raise ValueError("Invalid observed operation state")
                if phase == "finished" and state in ACTIVE:
                    raise ValueError("Final event has no terminal evidence")
                if (
                    phase == "finished"
                    and value["kind"] == "process"
                    and state in {"completed", "failed", "cancelled"}
                    and (
                        isinstance(status.get("returncode"), bool)
                        or not isinstance(status.get("returncode"), int)
                    )
                ):
                    raise ValueError(
                        "Terminal process result requires observed exit code"
                    )
                if (
                    value["kind"] == "process"
                    and state == "completed"
                    and status.get("returncode") != 0
                ):
                    raise ValueError("Completed process requires zero exit code")
                if status.get("capture_complete") is False:
                    value["captureComplete"] = False
                value.update(
                    state=state,
                    returncode=status.get("returncode"),
                    cancellationRequested=bool(status.get("cancellation_requested")),
                    terminationReason=status.get("termination_reason"),
                    streamComplete=bool(status.get("output_complete")),
                    outputComplete=bool(status.get("output_complete"))
                    and value["captureComplete"]
                    and value["droppedOutputBytes"] == 0,
                    controlAvailable=state in ACTIVE,
                    pid=status.get("pid"),
                    timeout=status.get("timeout"),
                    pty=status.get("pty") is True,
                    stdinAllowed=status.get("stdin_allowed") is True,
                    stdinClosed=status.get("stdin_closed") is True,
                    outputStreams=status.get("output_streams"),
                    eofSemantics=status.get("eof_semantics"),
                    questionIds=status.get("question_ids", []),
                )
                # A source's local ring can truncate while this journal still
                # captured everything. Only observer gaps affect journal capture.
                if phase == "finished":
                    value["endedAt"] = status.get("ended_at") or time.time()
                    if state in SETTLED and value.get("outputRetentionDays") is not None:
                        value["outputSettledAt"] = time.time()
                        value["outputExpiresAt"] = value["outputSettledAt"] + value["outputRetentionDays"] * 86400
                    if status.get("total_output_bytes", 0) != value["totalOutputBytes"]:
                        value["captureComplete"] = value["outputComplete"] = False
            if "metadata" in event:
                if not isinstance(event["metadata"], dict):
                    raise ValueError("Operation metadata must be an object")
                value["metadata"] = event["metadata"]
            if phase == "finished":
                value["evidence"] = {
                    key: status[key]
                    for key in ("result", "error", "resultTruncated")
                    if key in status
                }
            value.update(
                sequence=sequence, revision=value["revision"] + 1, updatedAt=time.time()
            )
            self._save(value)
            # Event identities remain durable for dedup, but output text lives
            # in one bounded output table, not a second unlimited event copy.
            self.db.execute(
                "INSERT INTO operation_events VALUES (?,?,?,?)",
                (identity, sequence, event_id, encoded),
            )
            # Hash output event bodies so dedup evidence does not duplicate
            # the output archive. Retain the latest 10000 source identities.
            if phase == "output":
                self.db.execute(
                    "UPDATE operation_events SET value=? WHERE operation_id=? AND sequence=?",
                    (
                        "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
                        identity,
                        sequence,
                    ),
                )
            self.db.execute(
                "DELETE FROM operation_events WHERE operation_id=? AND sequence<?",
                (identity, sequence - 10000),
            )
            return value, True

    def status(self, session_id, identity):
        with self.lock:
            return self._record(identity, session_id)

    def output_expiry_candidates(self, now, *, after=None, limit=8):
        """An indexed page of prospective deadlines, without output hydration."""
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("Output expiry page must contain 1–8 operations")
        where = """json_extract(value,'$.outputExpiresAt') IS NOT NULL
            AND json_extract(value,'$.outputAvailability') IS NOT 'expired'
            AND json_extract(value,'$.retainedChunks')>0
            AND json_extract(value,'$.outputExpiresAt')<=?"""
        params = [now]
        if after is not None:
            where += " AND (json_extract(value,'$.outputExpiresAt'),id)>(?,?)"
            params.extend(after)
        with self.lock:
            rows = self.db.execute("""SELECT id,session_id,
                json_extract(value,'$.outputExpiresAt'),json_extract(value,'$.revision')
                FROM operations WHERE """ + where + """
                ORDER BY json_extract(value,'$.outputExpiresAt'),id LIMIT ?""", (*params, limit + 1)).fetchall()
        items = [{"id": row[0], "sessionId": row[1], "expiresAt": row[2], "revision": row[3]}
                 for row in rows[:limit]]
        return {"items": items,
                "nextCursor": [items[-1]["expiresAt"], items[-1]["id"]] if len(rows) > limit else None}

    def expire_output(self, session_id, identity, expected_revision, now, *, keep_days=None):
        """Release chunks atomically; preserve outcome and exact retry evidence."""
        if keep_days is not None and (type(keep_days) is not int or not 1 <= keep_days <= 3650):
            raise ValueError("Output retention must be 1–3650 days or disabled")
        with self.lock, self.db:
            value = self._record(identity, session_id)
            if value.get("outputAvailability") == "expired":
                return {"status": "already-expired", "removedOutputBytes": 0}
            if (value["revision"] != expected_revision or value["state"] not in SETTLED
                    or value.get("outputExpiresAt") is None or value["outputExpiresAt"] > now
                    or not value.get("retainedChunks")):
                return {"status": "protected", "reason": "Outcome or retention eligibility changed", "removedOutputBytes": 0}
            if keep_days is not None and (value.get("outputSettledAt") is None
                    or value["outputSettledAt"] + keep_days * 86400 > now):
                return {"status": "protected", "reason": "Current policy retains this output longer", "removedOutputBytes": 0}
            size = self.db.execute("SELECT COALESCE(SUM(bytes),0) FROM operation_output WHERE operation_id=?",
                                   (identity,)).fetchone()[0]
            self.db.execute("DELETE FROM operation_output WHERE operation_id=?", (identity,))
            value.update(outputAvailability="expired", outputExpiredAt=now,
                         expiredOutputBytes=size, retainedOutputBytes=0, retainedChunks=0,
                         earliestCursor=value["latestCursor"], droppedOutputBytes=value["totalOutputBytes"],
                         outputComplete=False, revision=value["revision"] + 1, updatedAt=now)
            self._save(value)
            return {"status": "expired", "removedOutputBytes": size}

    def list(self, session_id, limit=50, before=None):
        with self.lock:
            values = [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT value FROM operations WHERE session_id=?", (session_id,)
                )
            ]
            values.sort(key=lambda row: (row["createdAt"], row["id"]), reverse=True)
            if before:
                index = next(
                    (i for i, row in enumerate(values) if row["id"] == before), None
                )
                if index is None:
                    raise ValueError("Unknown operation page cursor")
                values = values[index + 1 :]
            return values[:limit]

    def resources(self, session_id, limit=25, before=None):
        """Bounded lifecycle observations; never infer liveness from a PID.

        Whitelist in SQL so commands, output, kernel code and evidence do not
        enter this projection. The index bounds both session and page position.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Resource page size must be between 1 and 100")
        with self.lock:
            where, params = "session_id=?", [session_id]
            if before:
                cursor = self.db.execute(
                    "SELECT json_extract(value,'$.createdAt') FROM operations WHERE session_id=? AND id=?",
                    (session_id, before),
                ).fetchone()
                if cursor is None:
                    raise ValueError("Unknown operation resource cursor")
                where += " AND (json_extract(value,'$.createdAt'),id)<(?,?)"
                params.extend((cursor[0], before))
            rows = self.db.execute("""
                SELECT json_object('id',id,
                  'sessionId',session_id,
                  'kind',json_extract(value,'$.kind'),
                  'producer',json_extract(value,'$.producer'),
                  'state',json_extract(value,'$.state'),
                  'revision',json_extract(value,'$.revision'),
                  'createdAt',json_extract(value,'$.createdAt'),
                  'updatedAt',json_extract(value,'$.updatedAt'),
                  'pid',json_extract(value,'$.pid'),
                  'returncode',json_extract(value,'$.returncode'),
                  'processId',substr(json_extract(value,'$.metadata.processId'),1,200),
                  'language',substr(json_extract(value,'$.metadata.language'),1,100))
                FROM operations WHERE """ + where + """
                ORDER BY json_extract(value,'$.createdAt') DESC,id DESC LIMIT ?
                """, (*params, limit + 1)).fetchall()
            items = [dict(json.loads(row[0]), owner='operations',
                          evidence='recorded-lifecycle', workingDirectory=None)
                     for row in rows[:limit]]
            return {'items': items, 'nextCursor': items[-1]['id'] if len(rows) > limit else None}

    def read(self, session_id, identity, cursor=0, max_bytes=16384):
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError("Output cursor must be a nonnegative integer")
        if not isinstance(max_bytes, int) or not 4096 <= max_bytes <= 100000:
            raise ValueError("Output page budget must be 4096–100000 source bytes")
        with self.lock:
            value = self._record(identity, session_id)
            if cursor > value["latestCursor"]:
                raise ValueError("Output cursor is ahead of observed output")
            chunks, size, next_cursor = [], 0, max(cursor, value["earliestCursor"])
            rows = self.db.execute(
                "SELECT value FROM operation_output WHERE operation_id=? AND cursor>=? ORDER BY cursor LIMIT 128",
                (identity, next_cursor),
            )
            gap = cursor < value["earliestCursor"]
            for row in rows:
                chunk = json.loads(row[0])
                if size + chunk["source_bytes"] > max_bytes:
                    break
                gap = gap or chunk["cursor"] != next_cursor
                chunks.append(chunk)
                size += chunk["source_bytes"]
                next_cursor = chunk["next_cursor"]
            return {
                **value,
                "chunks": chunks,
                "cursor": cursor,
                "nextCursor": next_cursor,
                "cursorGap": gap,
                "hasMore": next_cursor < value["latestCursor"],
            }

    def close(self):
        with self.lock:
            self.db.close()
