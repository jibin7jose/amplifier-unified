"""Private change reviews; GitHub issue text is never replaced by this workflow."""
from __future__ import annotations

import json

ACTION = "feedback.edits.review"


def client(followups):
    from .errors import AppError
    identity = followups.service.clients.current.get()
    if not identity:
        raise AppError("Open feedback in an attached client to review changes.", 409)
    return identity


def receipt(followups, identity):
    row = followups.service.db.execute("SELECT receipt FROM feedback_followups WHERE id=?", (identity,)).fetchone()
    return json.loads(row[0]) if row else {}


def baseline(followups, args):
    from .errors import AppError
    read = receipt(followups, args["readRequestId"])
    if read.get("action") != "feedback.get" or read.get("status") != "completed" or read.get("feedbackId") != args["feedbackId"]:
        raise AppError("Refresh this report before reviewing changes.", 409)
    return read["report"]


def reviewed(followups, args, owner):
    from .errors import AppError
    review = receipt(followups, args["reviewRequestId"])
    proposal = review.get("review", {}).get("proposed", {})
    latest = review.get("review", {}).get("latest", {})
    if (review.get("action") != ACTION or review.get("status") != "completed"
            or review.get("clientId") != owner or review.get("feedbackId") != args["feedbackId"]
            or proposal != {"title": args["title"], "body": args["body"]}
            or latest.get("revision") != args["expectedRevision"] or review.get("consumedBy")):
        raise AppError("Review these exact changes in this client before publishing a correction.", 409)
    return review


def consume(followups, review, identity):
    review = {**review, "consumedBy": identity}
    followups.service.db.execute("UPDATE feedback_followups SET receipt=? WHERE id=?", (json.dumps(review), review["requestId"]))


async def run(followups, identity, args, issue, actor):
    from .feedback_lifecycle import revision
    before = baseline(followups, args)
    original = json.loads(followups.service.db.execute("SELECT payload FROM feedback_requests WHERE id=?", (args["feedbackId"],)).fetchone()[0])
    latest = {"title": str(issue.get("title", "")), "body": str(issue.get("body") or ""),
              "state": issue.get("state"), "revision": revision(issue), "updatedAt": issue.get("updated_at")}
    previous = {key: before.get(key) for key in ("title", "body", "state", "revision", "updatedAt", "textTruncated")}
    review = {"original": {key: original[key] for key in ("title", "body")},
              "previous": previous, "latest": latest, "proposed": {key: args[key] for key in ("title", "body")},
              "changedSinceRead": before["revision"] != latest["revision"],
              "changedFields": [key for key in ("title", "body", "state") if previous[key] != latest[key]],
              "author": actor, "automaticReplacement": False}
    await followups.update(identity, status="completed", review=review,
                           message="Changes reviewed. Publishing adds a correction comment; it does not replace the original report.")


def project(followups):
    owner = followups.service.clients.current.get()
    draft = followups.service.state["view"].get("feedbackCorrectionDraft") or {}
    row = receipt(followups, draft.get("reviewRequestId"))
    # A copied or writable draft pointer never grants another client's review.
    if owner and row.get("clientId") == owner and row.get("action") == ACTION:
        return {"editReview": {key: value for key, value in row.items() if key not in {"audit", "clientId"}}}
    return {}
