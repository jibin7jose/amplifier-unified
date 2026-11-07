"""Guarded peer input through ordinary native queue and anchored steering."""
from amplifier_operations.coordination import peer_input
from .task_binding import matches as task_matches


def exhausted(controls):
    task = controls.tasks.record() or {}
    goal = controls.coordinator.session_state.get("goal") or task.get("goalState") or {}
    cap = task.get("maxTurns", goal.get("cap"))
    return cap is not None and goal.get("turns_used", 0) >= cap


async def input_allowed(controls, permission):
    # Only the host's revalidated receipt can admit a deliberate resume input.
    # The saved task remains paused until its owner deliberately resumes it.
    if permission.get("humanResume") is True:
        return await controls.tasks.continuation_allowed(resume=True)
    if permission.get("completedTaskInput") is True:
        return await controls.tasks.peer_input_allowed(completed=True)
    if permission.get("blockedTaskInput") is True:
        return await controls.tasks.peer_input_allowed()
    return await controls.tasks.continuation_allowed()


def checkpoint_anchors(rows, runtime):
    """Correlate native IDs only after canonical persistence and delivered input."""
    generation = getattr(runtime, "generation", None) or {}
    inputs = set(generation.get("input_ids", []))
    if not inputs or not isinstance(rows, list):
        return []
    starts = [i for i, row in enumerate(rows)
              if (row.get("metadata") or {}).get("amplifier_input", {}).get("id") in inputs]
    if not starts:
        return []
    from .automatic_history import display_message
    result = []
    for index in range(min(starts), len(rows)):
        if rows[index].get("role") != "assistant":
            continue
        native = display_message(rows[index], index, {"id": runtime.session_id})
        if native:
            result.append({"messageId": native["id"], "nativeIndex": index, "generationId": generation["id"]})
    return result[-64:]


async def admit(controls, runtime, args, activation, authorize, stop_epoch=lambda: 0):
    # This runs under the worker's ordinary mutation/ownership lock.
    epoch = stop_epoch()
    permission = await authorize(args)
    if not permission.get("admitted"):
        return {"accepted": False, "reason": permission.get("reason"), **({"effect": "none", "waitingForUpdate": True} if permission.get("waitingForUpdate") else {})}
    task = controls.tasks.record()
    if not task_matches(task, args):
        return {"accepted": False, "reason": "The saved task changed before native admission."}
    if not await input_allowed(controls, permission):
        return {"accepted": False, "reason": "The saved task or current budget prevents automatic input."}
    if exhausted(controls):
        return {"accepted": False, "reason": "The saved task's turn budget is exhausted."}
    try:
        controls.require_idle()
    except ValueError as exc:
        return {"accepted": False, "reason": str(exc)}
    # Recheck after awaiting the boundary and use the host's saved original,
    # never a caller-provided envelope/text.
    permission = await authorize(args)
    if not permission.get("admitted"):
        return {"accepted": False, "reason": permission.get("reason"), **({"effect": "none", "waitingForUpdate": True} if permission.get("waitingForUpdate") else {})}
    if not await input_allowed(controls, permission) or stop_epoch() != epoch:
        return {"accepted": False, "reason": "Stop, task or budget changed during peer admission."}
    current = controls.tasks.record() or {}
    if not task_matches(current, args):
        return {"accepted": False, "reason": "The task changed during final authorization."}
    if exhausted(controls):
        return {"accepted": False, "reason": "The saved task's turn budget is exhausted."}
    try:
        controls.require_idle()
    except ValueError as exc:
        return {"accepted": False, "reason": str(exc)}
    message = permission["message"]
    text = peer_input(message["peerEnvelope"], message["text"])
    if len(text) > runtime.max_input_chars:
        return {"accepted": False, "reason": "Peer input exceeds this adapter's input limit; use a durable reference."}
    if permission.get("completedTaskInput") is True:
        controls.tasks.reserve_peer_input(args["inputId"], completed=True)
    elif permission.get("blockedTaskInput") is True:
        controls.tasks.reserve_peer_input(args["inputId"])
    from amplifier_module_loop_live.runtime import Input
    identity = await runtime.submit(Input("user", text, id=args["inputId"], activation=activation))
    return {"accepted": True, "inputId": identity, "mode": "queue", "completed": False}


def _steering_capability(controls):
    native = controls.coordinator.get_capability("web.native_provider")
    if native is not None and native.status().get("steering") == "native":
        return {"version": 1, "mode": "native", "submit": native.submit_steering}
    capability = controls.coordinator.get_capability("live.steering")
    if (isinstance(capability, dict) and type(capability.get("version")) is int
            and capability["version"] == 1 and capability.get("mode") == "request_boundary"
            and callable(capability.get("submit"))):
        return capability
    return None


async def deliver(controls, runtime, args, activation, authorize, stop_epoch=lambda: 0):
    """Select a native boundary for one host-owned automatic input.

    Only a refusal before submit may be reconsidered. Once accepted, anchored
    input keeps the native applied/held/unknown disposition and cannot fall
    forward into another generation.
    """
    def refused(reason, *, waiting=False):
        return {"accepted": False, "effect": "none", "waitingForBoundary": waiting,
                "reason": reason}

    epoch = stop_epoch()
    for _ in range(2):
        permission = await authorize(args)
        if not permission.get("admitted"):
            return {**refused(permission.get("reason")), **({"waitingForUpdate": True} if permission.get("waitingForUpdate") else {})}
        if not await input_allowed(controls, permission):
            return refused("The saved task or current budget prevents automatic input.")
        if not task_matches(controls.tasks.record(), args) or exhausted(controls):
            return refused("The saved task or current budget changed before native admission.")
        if stop_epoch() != epoch or runtime.closed or runtime.stop_requested:
            return refused("Stop or native ownership changed before peer admission.")
    message = permission["message"]
    text = peer_input(message["peerEnvelope"], message["text"])
    if len(text) > runtime.max_input_chars:
        return refused("Peer input exceeds this adapter's input limit; use a durable reference.")
    generation = runtime.generation or {}
    if generation.get('id') != permission.get('currentGenerationId'):
        return refused('Waiting for the host to observe the current native boundary.', waiting=True)
    from amplifier_module_loop_live.runtime import Input
    if generation.get("id"):
        capability = _steering_capability(controls)
        if (capability is None or runtime.steering_closed
                or permission.get("completedTaskInput") or permission.get("blockedTaskInput")
                or permission.get("humanResume")):
            return refused("Waiting for a supported recipient boundary.", waiting=True)
        # No await between reading the current native generation and submit.
        # Do not convert a submission exception into a no-effect refusal.
        outcome = await capability["submit"](
            Input("steer", text, id=args["inputId"], activation=activation), generation["id"])
        if not isinstance(outcome, dict) or type(outcome.get("accepted")) is not bool:
            raise RuntimeError("Automatic steering admission is unknown; do not replay.")
        return {**outcome, "mode": "steer", "completed": False}
    try:
        controls.require_idle()
    except ValueError:
        return refused("Waiting for the recipient's existing native work to settle.", waiting=True)
    if permission.get("completedTaskInput") is True:
        controls.tasks.reserve_peer_input(args["inputId"], completed=True)
    elif permission.get("blockedTaskInput") is True:
        controls.tasks.reserve_peer_input(args["inputId"])
    identity = await runtime.submit(Input("user", text, id=args["inputId"], activation=activation))
    return {"accepted": True, "inputId": identity, "mode": "queue", "completed": False}


async def steer(controls, runtime, args, activation, authorize, stop_epoch=lambda: 0):
    """Admit only to the named live generation, under worker ownership locks.

    Acceptance is not application or completion. The runtime's disposition and
    subsequent applied/held/unknown events own that evidence; never resend.
    """
    def rejected(reason, *, supported=True):
        return {"accepted": False, "supported": supported, "effect": "none", "reason": reason}

    epoch = stop_epoch()
    if _steering_capability(controls) is None:
        return rejected("This runtime does not support anchored request-boundary steering.", supported=False)
    target = args.get("targetGenerationId")
    if not isinstance(target, str) or not 1 <= len(target) <= 128:
        return rejected("An exact current generation identity is required for steering.")
    for _ in range(2):
        permission = await authorize(args)
        if not permission.get("admitted"):
            return rejected(permission.get("reason"))
        if not await controls.tasks.continuation_allowed():
            return rejected("The saved task or current budget prevents automatic input.")
        # Every read below occurs after the awaited host and task guard. Nothing
        # awaits between this last boundary and capability submission.
        current = controls.tasks.record() or {}
        if (current.get("id"), current.get("revision")) != (args.get("taskId"), args.get("taskRevision")):
            return rejected("The saved task changed before native steering admission.")
        goal = controls.coordinator.session_state.get("goal") or {}
        if goal.get("cap") is not None and goal.get("turns_used", 0) >= goal["cap"]:
            return rejected("The saved task's turn budget is exhausted.")
        if stop_epoch() != epoch:
            return rejected("Stop changed during peer steering admission.")
        if getattr(runtime, "closed", False):
            return rejected("The recipient runtime is closed.")
        generation = runtime.generation or {}
        if generation.get("id") != target:
            return rejected("The anchored generation is no longer active.")
        capability = _steering_capability(controls)
        if capability is None:
            return rejected("This runtime does not support anchored request-boundary steering.", supported=False)
    # Only the host's current saved message/envelope reaches the model.
    message = permission["message"]
    text = peer_input(message["peerEnvelope"], message["text"])
    if len(text) > runtime.max_input_chars:
        return rejected("Peer input exceeds this adapter's input limit; use a durable reference.")
    from amplifier_module_loop_live.runtime import Input
    outcome = await capability["submit"](Input("steer", text, id=args["inputId"], activation=activation), target)
    if not isinstance(outcome, dict):
        # Admission may already have happened. An invalid response is unknown,
        # not a definite no-effect refusal and never an invitation to replay.
        raise RuntimeError("The steering adapter returned an unknown admission outcome; do not replay.")
    return {**outcome, "supported": True, "mode": "steer", "completed": False}
