"""Preparation steps of coordination.create, before its first input is admitted.

The existing Git receipt owns the checkout; the existing task controller owns
its objective. This module adds no scheduler, writer lease or task store.
"""
import asyncio
import copy
import uuid

from . import task_continuity


def guard(owner, identity):
    receipt = owner.receipt(identity)
    if reason := owner.creation_guard(receipt):
        raise ValueError(reason)
    target = owner.service._session(receipt['sessionId'])
    active = target.get('collaborationGeneration') or {}
    if (target.get('interruptionRevision', 0) or active.get('id')
            or target.get('messages') or target.get('status') in {'working', 'stopping', 'stopped', 'error'}
            or any(target.get(key) for key in ('configurationBusy', 'configurationPending', 'fileAccessPending'))
            or target.get('collaboration') != receipt['result']
            or target['workspace'] != receipt['workspace']
            or (target.get('workingDirectory') or target['workspace']) != receipt['result']['workingDirectory']):
        raise ValueError('The worker changed before commissioning finished. No initial brief was sent.')
    return receipt


async def prepare_checkout(owner, identity):
    app = owner.service
    async with app.lock:
        receipt = guard(owner, identity)
        if receipt['phase'] not in {'root_retained', 'creating_checkout'}:
            return
        checkout_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'managed-worktree:' + identity + ':checkout'))
        if receipt['phase'] == 'creating_checkout':
            # No repeated Git effect after restart. Only its durable ready receipt
            # proves completion; a partial folder is not a successful checkout.
            try:
                checkout = app.worktrees.git.get(checkout_id)
            except ValueError:
                checkout = None
            if not checkout or checkout.get('status') != 'ready':
                owner.end_creation(receipt, 'unknown',
                    'Checkout preparation has no completed ownership receipt. Inspect retained files and the original operation; no work started.', notify=True)
                return
        else:
            checkout = None
            receipt.update(phase='creating_checkout', checkoutId=checkout_id)
            owner.save(identity, receipt)
            app.db.commit()
    if checkout is None:
        execution = receipt['execution']
        loop = asyncio.get_running_loop()
        cancelled = False

        def cancel():
            nonlocal cancelled
            cancelled = True

        async def admit(effect):
            async with app.lock:
                if cancelled:
                    raise ValueError('Worker commissioning was cancelled before checkout admission.')
                guard(owner, identity)
                return effect()

        checkout = await app.worktrees.finish_thread(app.worktrees.git.create,
            execution['repositoryPath'], command_id=identity + ':checkout',
            expected_revision=execution['sourceRevision'], mode='clean',
            ref=execution.get('ref', 'HEAD'), branch=execution.get('branch'),
            session_id=receipt['sessionId'],
            admit=lambda effect: asyncio.run_coroutine_threadsafe(admit(effect), loop).result(),
            on_cancel=cancel)
    async with app.lock:
        receipt = guard(owner, identity)
        if (checkout.get('id') != receipt['checkoutId'] or checkout.get('sessionId') != receipt['sessionId']
                or checkout.get('source') != receipt['execution']['repositoryPath']
                or checkout.get('sourceRevision') != receipt['execution']['sourceRevision']
                or checkout.get('status') != 'ready'):
            raise ValueError('The prepared checkout does not match its commissioning receipt.')
        app.worktrees.check_host(checkout)
        receipt['result']['workingDirectory'] = checkout['path']
        receipt['result']['checkoutId'] = checkout['id']
        target = app._session(receipt['sessionId'])
        target['workingDirectory'] = checkout['path']
        target['executionHost'] = copy.deepcopy(checkout['executionHost'])
        target['collaboration'] = copy.deepcopy(receipt['result'])
        receipt['phase'] = 'checkout_ready'
        owner.save(identity, receipt)
        app.worktrees.changed({target['id']})


async def prepare_objective(owner, identity):
    app = owner.service

    def current():
        return guard(owner, identity)

    async with app.lock:
        receipt = current()
        if receipt['phase'] == 'objective_ready':
            confirm_objective(app, receipt)
            return
        receipt['phase'] = 'installing_objective'
        owner.save(identity, receipt)
        app.db.commit()
    # task.create already persists and deduplicates the exact command across
    # process restart. It also checks actual goal support and preserves inherited
    # limits. Do not manufacture a task record or pretend a model turn did this.
    result = await task_continuity.dispatch(app, 'task.create', {
        'sessionId': receipt['sessionId'], 'expectedRevision': 0, **receipt['objective']},
        'agent', identity + ':objective', False, guard=current)
    async with app.lock:
        receipt = current()
        if not result.get('accepted'):
            raise ValueError('The commissioned objective is not confirmed active; no initial brief was sent.')
        task = confirm_objective(app, receipt)
        receipt['objectiveTaskId'] = task['id']
        receipt['phase'] = 'objective_ready'
        owner.save(identity, receipt)
        app.db.commit()


def confirm_objective(app, receipt):
    saved = task_continuity._load_control(app, app._session(receipt['sessionId']))
    task = saved.get('task') or {}
    command = saved.get('taskReceipts', {}).get(receipt['requestId'] + ':objective', {}).get('receipt') or {}
    if (command.get('action') != 'task.create' or command.get('taskId') != task.get('id')
            or command.get('revision') != task.get('revision') or task.get('status') != 'active'
            or task.get('objective') != receipt['objective']['objective']
            or task.get('constraints') != receipt['objective']['constraints']
            or (saved.get('goal') or {}).get('task_id') != task.get('id')):
        raise ValueError('The commissioned objective changed or is not confirmed active; no initial brief was sent.')
    return task
