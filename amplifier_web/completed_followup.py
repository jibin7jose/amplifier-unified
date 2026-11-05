"""Fresh authorized requests to finished roots, without reopening old work."""
from .task_binding import matches


def owner_assignment(service, source, target):
    assignment = service.assignments.current(source)
    if (not assignment or assignment['status'] != 'active'
            or assignment['ownerSessionId'] != source['id'] or source['id'] == target['id']
            or assignment['members'].get(target['id'], {}).get('status') != 'bound'):
        return None
    current = service.assignments.current(target)
    return assignment if current and current['id'] == assignment['id'] else None


def prepare(service, source, target, task, mode, args, *, creation=False):
    if (mode not in {'queue', 'auto'} or creation or args.get('resume') or args.get('scheduleRepair')
            or task.get('status') != 'completed' or not task.get('id')):
        return None
    proof = {'version': 1, 'taskId': task['id'], 'taskRevision': task['revision']}
    if owner_assignment(service, source, target):
        return proof
    # Independent managers keep their own authority and task history. Completing
    # one objective is not a user Stop or a permanent ban on fresh peer input.
    authority = independent_authority(service, source, target)
    return {**proof, 'version': 2, **authority} if authority else None


def independent_authority(service, source, target):
    if source['id'] == target['id']:
        return None
    assignment = service.assignments.current(target)
    if assignment and (assignment['status'] != 'active' or assignment['ownerSessionId'] != target['id']):
        return None  # Another manager's commissioned worker still uses its owner.
    sender = service.access_mode.snapshot(source)
    recipient = service.access_mode.snapshot(target)
    if sender['mode'] != 'full' or recipient['mode'] != 'full':
        return None
    return {'sourceAccessRevision': sender['revision'], 'targetAccessRevision': recipient['revision'],
            'targetAssignmentId': assignment['id'] if assignment else None}


def matches_request(service, receipt, task, *, for_reply=False):
    proof = receipt.get('completedTaskFollowup')
    if (not isinstance(proof, dict) or proof.get('version') not in {1, 2}
            or receipt.get('commandAction') != 'coordination.send'
            or receipt.get('mode') not in {'queue', 'auto'}
            or any(receipt.get(key) for key in
                   ('dependencyRequestId', 'creationOperationId', 'humanResume', 'scheduleRepair'))
            or 'taskContinuationEpoch' in receipt
            or (proof.get('taskId'), proof.get('taskRevision')) !=
               (receipt.get('taskId'), receipt.get('taskRevision'))):
        return False
    source = service._session(receipt['senderSessionId'])
    target = service._session(receipt['target']['sessionId'])
    if proof['version'] == 1:
        assignment = owner_assignment(service, source, target)
        if not assignment or assignment['id'] != receipt.get('assignmentId'):
            return False
    else:
        authority = independent_authority(service, source, target)
        if (not authority or any(proof.get(key) != value for key, value in authority.items())
                or source.get('interruptionRevision', 0) != receipt.get('sourceInterruptionRevision')):
            return False
    if task.get('status') == 'completed' and matches(task, proof):
        return True
    # Reporting may describe native objectives created within this correction.
    # This never admits another input, and does not search or rewrite history.
    previous = task.get('followupOf') or {}
    return bool(for_reply and task.get('status') in {'active', 'completed'}
                and previous.get('taskId') == proof['taskId']
                and previous.get('revision') == proof['taskRevision']
                and previous.get('inputId') == receipt.get('inputId')
                and previous.get('generationId'))
