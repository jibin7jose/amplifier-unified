"""A checkout move waits for native work to settle; it never cancels a tool."""
import copy


async def inspect(worker, request_id, cancel=False, *, update=False, expected=None):
    async with worker.command_lock:
        marker = getattr(worker, '_handoff_boundary', None)
        if cancel:
            if marker and marker['id'] == request_id:
                marker['unregister']()
                worker._handoff_boundary = None
            return {'cancelled': True}
        if marker and marker['id'] != request_id:
            raise ValueError('Another checkout move owns this runtime boundary.')
        if not worker.session or not worker.runtime:
            return {'ready': worker.parked, 'waitingFor': ['runtime']}
        coordinator = worker.session.coordinator
        if expected and expected.get('observeOnly'):
            if not marker:
                raise ValueError('The original update boundary is unavailable; it was not recreated.')
        elif expected:
            task = coordinator.session_state.get('task') or {}
            from .task_binding import matches
            if (not matches(task, expected) or task.get('revision') != expected['taskRevision']
                    or task.get('status') != 'active'
                    or (worker.runtime.generation or {}).get('id') != expected['generationId']):
                raise ValueError('The requesting task or generation changed before its update boundary.')
        if (update and worker.runtime.generation and coordinator.session_state.get('goal')
                and not coordinator.get_capability('live.continuation_guard_supported')):
            return {'ready': False, 'waitingFor': ['unsupported-native-boundary']}
        if not marker:
            from amplifier_core import HookResult
            task = coordinator.session_state.get('task')
            requested_task = ({key: task.get(key) for key in ('id', 'revision', 'continuationEpoch')}
                              if update and task else None)
            requested_generation = ((worker.runtime.generation or {}).get('id') if update else None)
            # Native admission retains immutable Input identities in insertion
            # order before emitting/awaiting steering events. Inspect only new
            # admissions, not history or applied input_ids (which also contains
            # already-admitted child observations).
            accepted = getattr(worker.runtime, 'accepted', {})
            admitted_tail = next(reversed(accepted), None)
            superseded = False

            def current_update():
                nonlocal superseded
                task = coordinator.session_state.get('task') or {}
                generation = (worker.runtime.generation or {}).get('id')
                if not superseded:
                    for identity in reversed(accepted):
                        if identity == admitted_tail:
                            break
                        if accepted[identity].kind in {'user', 'steer'}:
                            superseded = True
                            break
                return (not superseded and (generation is None or generation == requested_generation)
                    and (not requested_task or task.get('status') == 'active' and
                         all(task.get(key) == value for key, value in requested_task.items())))

            async def finish_turn(event, data):
                goal = coordinator.session_state.get('goal')
                if update and (not current_update()
                        or (worker.runtime.generation or {}).get('id') != requested_generation):
                    return HookResult()
                if (data.get('state') == 'continuing' and goal
                        and data.get('session_id', coordinator.session_id) == coordinator.session_id
                        and data.get('condition') == goal.get('condition')
                        and data.get('turn') == goal.get('turns_used')):
                    # Native goal continuation checks object identity after this
                    # awaited hook. Preserve progress while ending this turn at
                    # the same boundary used by a queued peer outcome.
                    coordinator.session_state['goal'] = copy.deepcopy(goal)
                    try:
                        worker.controls.persist(task_only=True)
                        if update:
                            from .task_binding import continuation_epoch
                            worker._handoff_boundary['yieldedGoal'] = copy.deepcopy(goal)
                            task = coordinator.session_state.get('task') or {}
                            worker._handoff_boundary['yieldedTaskBinding'] = {
                                'taskId': task.get('id'), 'taskRevision': task.get('revision'),
                                'taskContinuationEpoch': continuation_epoch(task)}
                    except BaseException:
                        coordinator.session_state['goal'] = goal
                        raise
                return HookResult()

            unregister = coordinator.hooks.register('orchestrator:goal_progress', finish_turn,
                name='unified-checkout-boundary', priority=0)
            worker._handoff_boundary = {'id': request_id, 'unregister': unregister, 'update': update,
                'hadGoal': bool(coordinator.session_state.get('goal'))}
            if update:
                worker._handoff_boundary['updateCurrent'] = current_update
        if update:
            from .handoff_drain import _freeze
            _freeze(worker)
        loop = coordinator.get('orchestrator')
        waiting = []
        if worker.runtime.generation: waiting.append('generation')
        if worker.bridges: waiting.append('tool-replies')
        if worker.approvals: waiting.append('approval')
        if worker.operation_ids or worker.operation_controls: waiting.append('operations')
        if worker.runtime.queued_inputs or not worker.runtime.inbox.empty(): waiting.append('inputs')
        if loop and (loop.pending or loop._active_jobs()): waiting.append('children')
        if worker.naming and worker.naming.pending and not worker.naming.pending.done(): waiting.append('naming')
        if worker.remounting or worker.ownership.yielding: waiting.append('ownership')
        # Park is the positive checkpoint + release evidence. An idle UI event
        # alone does not establish that children or the calling tool settled.
        if not worker.parked: waiting.append('checkpoint')
        marker = worker._handoff_boundary
        # A goal may reach its cap or complete while the calling turn settles.
        # A host observation must not re-arm that exhausted task as fresh input.
        has_task = bool(coordinator.session_state.get('task'))
        continuation_allowed = (not has_task and not marker['hadGoal']) or bool(coordinator.session_state.get('goal'))
        result = {'ready': not waiting, 'waitingFor': waiting, 'continuationAllowed': continuation_allowed}
        if update and not waiting:
            goal = coordinator.session_state.get('goal')
            yielded = marker.get('yieldedGoal')
            task = coordinator.session_state.get('task')
            from .task_binding import matches
            if (yielded and goal and task and task.get('status') == 'active'
                    and yielded.get('condition') == goal.get('condition') == task.get('objective')
                    and matches(task, marker['yieldedTaskBinding'])):
                result.update(
                    yieldedGoal={key: goal.get(key) for key in ('task_id', 'cap', 'turns_used', 'continuations')},
                    task={key: task.get(key) for key in ('id', 'revision', 'status', 'continuationEpoch')})
        return result
