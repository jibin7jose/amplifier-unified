"""One fresh conversation per occurrence, with no automatic creation/input replay."""
import asyncio

from .session_creation import template, configuration_changes
from .managed_chats import is_managed


def creation_guard(schedules, source, identity, destination):
    """Runs under the same app lock as shared session.create."""
    run = schedules.store.run(source, identity)
    record = schedules.store.get(source, run['scheduleId'])
    if not schedules.store.owns(schedules.clock()) or run['owner'] != schedules.store.owner:
        raise ValueError('Scheduler ownership changed before conversation creation')
    if run['phase'] != 'creating' or run.get('destinationSessionId') != destination:
        raise ValueError('This occurrence has no matching new-task reservation')
    if record['status'] != 'active' or record['revision'] != run['scheduleRevision']:
        raise ValueError('The schedule changed before conversation creation')
    reason = schedules.dependency_reason(schedules.app._session(source), None, record)
    if reason: raise ValueError(reason)


async def create_destination(schedules, current, run):
    app, sid, identity = schedules.app, current['sessionId'], run['destinationSessionId']
    config = current['newTaskConfiguration']
    location = {'location': {'kind': 'managed'}} if is_managed(config) else {'workspace': config['workspace']}
    rejected = False
    configuration_comparison = {}
    def guard():
        nonlocal rejected
        try:
            creation_guard(schedules, sid, run['id'], identity)
            if run.get('creationConfirmed'):
                destination = app._session(identity, hydrate=False)
                if (destination.get('interruptionRevision', 0) != run['destinationInterruptionRevision']
                        or destination.get('executionRevision', 0) != run['destinationExecutionRevision']
                        or destination.get('configurationBusy') or destination.get('fileAccessPending')):
                    raise ValueError('The destination changed or stopped before task creation.')
                observed = template(app, destination)[1]
                if observed['configurationHash'] != run['destinationConfigurationHash']:
                    previous = {'configurationHash': run['destinationConfigurationHash'],
                                'configurationParts': run.get('destinationConfigurationParts', {})}
                    fields = configuration_changes(previous, observed)
                    configuration_comparison.update(
                        before={'stage': 'destination_created', **previous},
                        after={'stage': 'task_creation_admission', 'configurationHash': observed['configurationHash'],
                               'configurationParts': observed['configurationParts']},
                        changedCategories=fields)
                    raise ValueError('The new task configuration changed before task creation'
                                     + (': ' + ', '.join(fields) if fields else '')
                                     + ' (destination creation compared with task-creation admission).')
        except ValueError:
            rejected = True
            raise
    try:
        await app.dispatch('session.create', {
            'id': identity, 'select': False, 'title': current['newTaskTitle'],
            **location, 'bundle': config['bundle'],
            'inheritConfiguration': {'sessionId': sid, 'configurationHash': config['configurationHash'], 'scheduledRunId': run['id']},
        }, origin='scheduler', command_id=run['id'] + ':create', include_state=False, admission_check=guard)
        async with app.lock:
            destination = app._session(identity)
            configuration = template(app, destination)[1]
            run = schedules.store.transition(sid, run['id'], ['creating'], 'creating', schedules.clock(),
                destinationConfigurationHash=configuration['configurationHash'],
                destinationConfigurationParts=configuration['configurationParts'],
                destinationInterruptionRevision=0, destinationExecutionRevision=0, creationConfirmed=True)
            schedules.changed()
        result = await app.dispatch('task.create', {'sessionId': identity, 'expectedRevision': 0,
            'objective': current['prompt'], 'maxTurns': current['newTaskMaxTurns']},
            origin='scheduler', command_id=run['id'] + ':task', include_state=False, admission_check=guard)
        task = result['result']['task']
        async with app.lock:
            run = schedules.store.transition(sid, run['id'], ['creating'], 'creating', schedules.clock(), taskId=task['id'], taskRevision=task['revision'])
            schedules.changed()
        return run, destination, task
    except BaseException as exc:
        # Creation or task configuration can have committed before a connection
        # failed. Save the reserved identity, never guess that a retry is safe.
        async with app.lock:
            phase = 'skipped' if rejected else 'unknown'
            detail = ('New-task preparation stopped before the next effect: ' if rejected else
                      'New-task preparation outcome requires inspection; no automatic retry: ') + str(exc)[:500]
            saved = schedules.store.transition(sid, run['id'], ['creating'], phase, schedules.clock(), detail=detail,
                **({'destinationConfigurationComparison': configuration_comparison} if configuration_comparison else {}))
            schedules.store.review(sid, current['id'], detail, schedules.clock())
            schedules.decide_notification(app._session(sid), saved)
            schedules.changed()
        if isinstance(exc, asyncio.CancelledError): raise
        return None
