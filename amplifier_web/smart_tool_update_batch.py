"""Independent, durable results for managed Smart Tool update attempts."""
import asyncio
import re
import time

from .managed_runner_backend import backend, skip_key

from .update_diagnostics import exception_type


def identity(item):
    values = {'component': item.get('component'), 'installationId': item['previous'],
              'targetInstallationId': item.get('target')}
    return {key: value for key, value in values.items() if isinstance(value, str)
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value)}


async def attempt(manager, item, phase, operation, *args):
    try:
        return await manager.diagnostics.run('smart-tool-' + phase, operation, *args,
                                             _context=identity(item))
    except Exception as error:
        # A tool's stderr/exception may contain credentials or private paths.
        # Keep only fixed reason codes and code-defined error classes.
        item['failure'] = {'phase': phase, 'errorType': exception_type(error),
                           'reason': getattr(error, 'diagnostic_facts', {}).get('reason', 'smart-tool-update-failed')}
        if item['failure']['reason'] not in {'smart-tool-startup-failed', 'missing-configured-environment', 'managed-runner-qualification-failed', 'managed-runner-selection-changed', 'managed-runner-package-changed'}:
            item['failure']['reason'] = 'smart-tool-update-failed'
        return None


async def stage(manager):
    if manager.lock.locked():
        return
    async with manager.lock:
        pending = list(manager.service.state['updates'].get('pendingSmartTools', []))
        if not pending or manager.independent_tool_results(manager.service.state['updates']):
            retained = {row['previous'] for row in pending}
            sources = [row for row in manager.inventory if row.get('kind') == 'smart tool'
                       and row.get('status') == 'update' and row.get('eligible')
                       and row.get('installationId') not in retained]
            manager.diagnostics.begin('smart-tools', tier='other', components=[row.get('label', '') for row in sources])
            await manager.publish(phase='staging', error=None, runnerAvailabilityCheck=False,
                                  detail='Preparing Smart Tool updates…')
            for source in sources:
                item = {'previous': source['installationId'], 'revision': source.get('latest')}
                if source.get('runnerKind'):
                    item['runnerSource'] = source
                    item['runnerUpdate'] = backend(source['runnerKind']).intent_id(source)
                    item['resultId'] = source['runnerKind'] + '-' + item['runnerUpdate']
                label = source.get('label')
                if isinstance(label, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', label):
                    item['component'] = label
                # Save the attempt before doing work, including interruption.
                pending.append(item)
                await manager.publish(pendingSmartTools=pending)
                target = await attempt(manager, item, 'stage', manager.service.smart_tools.stage_update, source)
                if target is not None:
                    item['target'] = target['id']
                    if target.get('runnerUpdate'):
                        item['runnerUpdate'] = target['runnerUpdate']
                await manager.publish(pendingSmartTools=pending)
        await manager.publish(phase='staged', detail='Smart Tools prepared; waiting for work to finish.')
    await activate(manager)


async def finish(manager, pending):
    if manager.service.state['updates'].get('runnerAvailabilityCheck'):
        # Once the app/component sequence has resumed, retained tool results belong
        # only to their own rows. Never overwrite its phase, errors, inventory
        # or restart continuation with a capacity wait or a late completion.
        await manager.publish(pendingSmartTools=pending, runnerAvailabilityCheck=bool(pending))
        return
    failures = [row for row in pending if row.get('failure')]
    if pending and any(row.get('runnerUpdate') and not row.get('failure') for row in pending):
        await manager.publish(phase='staged', pendingSmartTools=pending, error=None,
                              detail='Checking the updated test runner when machine capacity is available. Existing work keeps its original runner.')
        return
    sequence = {**manager.service.state['updates'].get('sequence', {})}
    if pending:
        if manager.diagnostics.state.get('batch', {}).get('activatedComponents'):
            manager.diagnostics.record('smart-tools-partial', 'succeeded')
        sequence.pop('nextStage', None)
        sequence['install'] = False
        await manager.publish(phase='error', pendingSmartTools=pending, sequence=sequence,
                              error=f'{len(failures) or len(pending)} Smart Tool update(s) need attention. Other ready tools were updated. Retry or skip each affected tool below.')
    else:
        sequence['nextStage'] = 'application' if manager.service.state['updates'].get('runnerAvailabilityCheck') else 'other'
        manager.diagnostics.record('smart-tools-complete', 'succeeded')
        manager.diagnostics.clear_failure()
        await manager.publish(phase='installed', pendingSmartTools=[], sequence=sequence, error=None, runnerAvailabilityCheck=False,
                              detail='Smart Tools updated. Previous versions are retained for rollback.')
        manager.inventory = []


async def activate(manager):
    # Runner candidates qualify through their own exact resource ownership. They
    # must not wait for unrelated chats to finish or hold the app lifecycle lock.
    if not manager.lock.locked():
        async with manager.lock:
            pending = list(manager.service.state['updates'].get('pendingSmartTools', []))
            for item in list(pending):
                if not item.get('runnerSource') or item.get('failure'):
                    continue
                if not item.get('runnerUpdate') or not item.get('target'):
                    item['failure'] = {'phase': 'stage', 'reason': 'managed-runner-qualification-failed', 'errorType': 'Exception'}
                    await manager.publish(pendingSmartTools=pending)
                    continue
                advance = backend(item['runnerSource']['runnerKind']).advance
                started = time.monotonic()
                for step in range(8):
                    result = await attempt(manager, item, 'qualify', advance,
                                           manager.service, item['runnerUpdate'])
                    if not result or result.get('complete') or not result.get('progressed') or time.monotonic() - started > 15:
                        break
                    # Yield while the owned worker starts; never hold app.lock.
                    await asyncio.sleep(0.1)
                if result and result.get('complete'):
                    pending.remove(item)
                elif result:
                    item['runnerWaiting'] = result.get('waiting')
                await manager.publish(pendingSmartTools=pending)
            if not pending or all(row.get('runnerSource') or row.get('failure') for row in pending):
                await finish(manager, pending)
                return
    async with manager.service.runtime_lifecycle():
        async with manager.lock:
            pending = list(manager.service.state['updates'].get('pendingSmartTools', []))
            if not pending:
                return
            async with manager.service.lock:
                if manager.busy():
                    return
                if not manager.service.state['updates'].get('runnerAvailabilityCheck'):
                    manager.service.state['updates'].update(phase='activating', error=None, detail='Checking and switching Smart Tools…')
                manager.service._publish_changes(globals={'updates', 'settings'})
            try:
                for item in list(pending):
                    if item.get('failure') or item.get('runnerSource'):
                        continue
                    if not item.get('target'):
                        # A restart during staging must not replay installation.
                        item['failure'] = {'phase': 'stage', 'reason': 'smart-tool-update-failed', 'errorType': 'Exception'}
                        await manager.publish(pendingSmartTools=pending)
                        continue
                    await attempt(manager, item, 'activate', manager.service.smart_tools.activate_update,
                                  item['previous'], item['target'])
                    if not item.get('failure'):
                        batch = manager.diagnostics.state.get('batch', {})
                        if batch.get('attemptId') == manager.diagnostics.state.get('attemptId'):
                            activated = batch.setdefault('activatedComponents', [])
                            component = identity(item).get('component')
                            if component and component not in activated:
                                activated.append(component)
                        pending.remove(item)
                    await manager.publish(pendingSmartTools=pending)
                await finish(manager, pending)
            except asyncio.CancelledError:
                await manager.publish(phase='interrupted', pendingSmartTools=pending,
                                      error='Smart Tool updates were interrupted. Inspect the pending tools before retrying.')
                raise


async def retry(manager, identity_value):
    if manager.lock.locked():
        raise ValueError('Wait for the current update to finish.')
    async with manager.lock:
        pending = list(manager.service.state['updates'].get('pendingSmartTools', []))
        item = next((row for row in pending if row.get('resultId', row['previous']) == identity_value), None)
        if not item or not item.get('failure'):
            raise ValueError('This tool has no failed update to retry.')
        item.pop('failure')
        independent = manager.service.state['updates'].get('runnerAvailabilityCheck')
        await manager.publish(pendingSmartTools=pending, **({} if independent else {'phase': 'staging', 'error': None}))
        if not item.get('target'):
            source = item.get('runnerSource') or {'installationId': item['previous'], 'latest': item['revision']}
            target = await attempt(manager, item, 'stage', manager.service.smart_tools.stage_update, source)
            if target is not None:
                item['target'] = target['id']
                if target.get('runnerUpdate'):
                    item['runnerUpdate'] = target['runnerUpdate']
        await manager.publish(pendingSmartTools=pending, **({} if independent else {'phase': 'staged'}))
    await activate(manager)


async def skip(manager, identity_value):
    if manager.lock.locked():
        raise ValueError('Wait for the current update to finish.')
    async with manager.lock:
        state = manager.service.state['updates']
        pending = list(state.get('pendingSmartTools', []))
        item = next((row for row in pending if row.get('resultId', row['previous']) == identity_value), None)
        if not item or not item.get('failure'):
            raise ValueError('This tool has no failed update to skip.')
        if item.get('runnerSource'):
            # Failed/uncertain owned checks keep their exact package and receipt.
            # Skipping only declines selection; it never erases recovery evidence.
            await backend(item['runnerSource']['runnerKind']).skip(manager.service, item.get('runnerUpdate'))
            pending.remove(item)
            await manager.publish(pendingSmartTools=pending,
                                  skippedSmartTools={**state.get('skippedSmartTools', {}), skip_key(item['runnerSource']): item['revision']})
            await finish(manager, pending)
            return
        cleanup = {key: value for key, value in item.items() if key != 'failure'}
        await attempt(manager, cleanup, 'cleanup', manager.service.smart_tools.discard_update,
                      item['previous'], item.get('revision'))
        if cleanup.get('failure'):
            item['failure'] = cleanup['failure']
            await manager.publish(pendingSmartTools=pending, **({} if state.get('runnerAvailabilityCheck') else {
                'phase': 'error', 'error': 'The failed tool update could not be removed. Its current version is unchanged. Try Skip again to finish cleanup.'}))
            return
        skipped = {**state.get('skippedSmartTools', {})}
        if item.get('revision'):
            skipped[identity_value] = item['revision']
        pending.remove(item)
        await manager.publish(pendingSmartTools=pending, skippedSmartTools=skipped)
        if pending or state.get('runnerAvailabilityCheck'):
            await finish(manager, pending)
        else:
            manager.diagnostics.clear_failure()
            await manager.publish(phase='checked', error=None, available=0,
                                  sequence={**state.get('sequence', {}), 'install': False, 'nextStage': 'other'},
                                  detail='The failed tool update was skipped. Its current version is unchanged.')
            manager.inventory = []
