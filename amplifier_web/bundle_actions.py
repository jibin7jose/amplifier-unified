"""Bundle actions shared by browser controls and app_control."""
from __future__ import annotations
import copy
import shutil
import uuid
from pathlib import Path


async def preview_saved(management, args):
    """Compose a candidate without mounting either conversation runtime."""
    from .draft_defaults import resolve_defaults
    app, sid = management.service, args['sessionId']
    token = str(uuid.uuid4())
    async with app.lock:
        current = app._session(sid, hydrate=False)
        source = copy.deepcopy({key: current[key] for key in (
            'workspace', 'workingDirectory', 'runtimeSessionId', 'nativeIdentity',
            'selection', 'nextMessageRevision', 'interruptionRevision') if key in current})
        revision = (app.state.get('configurationRevision', 0), source.get('nextMessageRevision', 0),
                    source.get('interruptionRevision', 0))
        current['bundlePreviewRequest'] = token
        current['bundleChange'] = {'phase': 'working', 'action': 'bundle.preview', 'bundle': args['bundle']}
        app._publish_changes(sessions={sid})
    try:
        checked = await resolve_defaults(app.data_dir, source['workspace'], args['bundle'],
            session_id=source.get('runtimeSessionId') or source.get('nativeIdentity') or sid,
            replacement={'selection': source.get('selection'), 'workingDirectory': source.get('workingDirectory'),
                         'replaceSavedSelection': 'selection' in source})
        result = {**checked, 'previewId': token, 'baseline': 'saved',
            'detail': 'Declared bundle settings only. Model and mounted capabilities are checked when work begins.'}
        error = None
    except Exception:
        result = None
        error = 'Bundle preview is unavailable. Your saved choices are unchanged; retry when its source is available.'
    async with app.lock:
        current = app._session(sid, hydrate=False)
        if current.get('bundlePreviewRequest') != token:
            return
        if revision != (app.state.get('configurationRevision', 0), current.get('nextMessageRevision', 0),
                        current.get('interruptionRevision', 0)):
            if current.get('bundleChange', {}).get('action') == 'bundle.preview':
                current['bundleChange'] = {'phase': 'stale', 'action': 'bundle.preview',
                    'bundle': args['bundle'], 'detail': 'Choices changed. Preview again for current settings.'}
                app._publish_changes(sessions={sid})
            return
        if result is not None:
            current['bundlePreview'] = result
        current['bundleChange'] = {'phase': 'error' if error else 'ready', 'action': 'bundle.preview',
            'bundle': args['bundle'], **({'error': error} if error else {})}
        app._publish_changes(sessions={sid})
    if error:
        raise ValueError(error)


async def perform(management, action, args):
    from .configuration_authority import check_current
    check_current()
    if action == 'bundle.preview':
        return await preview_saved(management, args)
    app = management.service
    sid = args['sessionId']
    async with app.lock:
        current = app._session(sid)
        if current.get('pendingConfiguration', {}).get('phase') in {'queued', 'applying'}:
            raise ValueError('Cancel or finish the queued module changes before changing bundles.')
        if not management.configuration_idle(current):
            raise ValueError('Finish the current turn and its workers before changing bundles.')
        if action == 'bundle.switch' and current.get('pendingBundleSelection'):
            raise ValueError('A Next message bundle choice is saved. Change that choice, or cancel it by selecting the current bundle, before switching immediately.')
        current['configurationBusy'] = True
        current['bundleChange'] = {'phase': 'working', 'action': action, 'bundle': args['bundle']}
        source = copy.deepcopy(current)
        interruption_revision = current.get('interruptionRevision', 0)
        app._publish_changes(sessions={sid})
    def restart_guard():
        check_current()
        if app._session(sid).get('interruptionRevision', 0) != interruption_revision:
            raise ValueError('The conversation was stopped before changing bundles. It was not restarted.')

    try:
        recovering = (source.get('errorType') == 'RuntimeStartupError'
                      or source.get('failure', {}).get('category') == 'worker_startup')
        replacement = None
        if recovering and action in {'bundle.preview', 'bundle.switch'}:
            from .draft_defaults import resolve_defaults
            checked = await resolve_defaults(app.data_dir, source['workspace'], args['bundle'],
                session_id=source.get('runtimeSessionId') or source.get('nativeIdentity') or sid,
                replacement={key: source[key] for key in ('selection', 'workingDirectory') if key in source})
            expected = source.get('bundlePreview', {})
            if args.get('previewId') and (expected.get('previewId') != args['previewId'] or any(
                    expected.get(key) != checked.get(key) for key in ('bundle', 'fingerprint', 'selection'))):
                raise ValueError('The configuration changed. Preview the selected bundle again.')
            checked['previewId'] = str(uuid.uuid4())
            async with app.lock:
                app._session(sid)['bundlePreview'] = checked
            if action == 'bundle.switch':
                if not checked['modelCompatible'] and not args.get('resetModel'):
                    raise ValueError('Your pinned model is unavailable in this bundle. Select Use the new bundle’s model to continue.')
                replacement = {**checked, 'resetModel': args.get('resetModel', False)}
                restart_guard()
                await management.ensure_runtime(source, bundle_replacement=replacement, restart_guard=restart_guard)
        else:
            await management.ensure_runtime(source, restart_guard=restart_guard)
        if action == 'bundle.preview':
            result = checked if recovering else await app.runtime.control(sid, action, {'bundle': args['bundle']})
            async with app.lock:
                app._session(sid)['bundlePreview'] = result
        elif action == 'bundle.switch':
            if replacement is None:
                check_current()
                switch_args = args
                if args.get('previewId') and source.get('bundlePreview', {}).get('baseline') == 'saved':
                    expected = source.get('bundlePreview', {})
                    if expected.get('previewId') != args['previewId'] or expected.get('bundle') != args['bundle']:
                        raise ValueError('The configuration changed. Preview the selected bundle again.')
                    mounted_preview = await app.runtime.control(sid, 'bundle.preview', {'bundle': args['bundle']})
                    if any(expected.get(key) != mounted_preview.get(key) for key in ('fingerprint', 'selection')):
                        raise ValueError('The configuration changed. Preview the selected bundle again.')
                    switch_args = {**args, 'previewId': mounted_preview['previewId']}
                prepared = await app.runtime.control(sid, 'bundle.prepare', switch_args)
                if prepared.get('requiresModelChoice'):
                    async with app.lock:
                        app._session(sid)['bundlePreview'] = prepared['preview']
                    raise ValueError('Your pinned model is unavailable in this bundle. Select Use the new bundle’s model to continue.')
                replacement = prepared['replacement']
                restart_guard()
                await app.runtime.stop(sid, reason='configuration')
                restart_guard()
                try:
                    await management.ensure_runtime(copy.deepcopy(source), bundle_replacement=replacement, restart_guard=restart_guard)
                except Exception:
                    # The candidate restores the journal before exiting. Confirm
                    # its process is gone before restoring the previous worker;
                    # never reuse candidate imports or replay a user input.
                    await app.runtime.stop(sid, reason='configuration')
                    restart_guard()
                    await management.ensure_runtime(copy.deepcopy(source), restart_guard=restart_guard)
                    raise
            result = {'bundle': args['bundle'], 'historyPreserved': True,
                'configuration': await app.runtime.control(sid, 'configuration.inspect', {}),
                'providers': await app.runtime.control(sid, 'configuration.providers', {})}
            async with app.lock:
                current = app._session(sid)
                current.update(bundle=result['bundle'], configuration=result['configuration'])
                current.pop('bundlePreview', None)
                if args.get('resetModel'): current.pop('selection', None)
                app.state.setdefault('runtimeControl', {})[sid] = {'configuration.providers': result['providers']}
        else:
            expected = app._session(sid).get('bundlePreview', {})
            if args.get('previewId') and (expected.get('previewId') != args['previewId'] or expected.get('bundle') != args['bundle']):
                raise ValueError('Preview the selected bundle again before forking.')
            checked = await app.runtime.control(sid, 'bundle.preview', {'bundle': args['bundle']})
            if args.get('previewId') and any(checked.get(key) != expected.get(key) for key in ('fingerprint', 'selection')):
                raise ValueError('The configuration changed. Preview the selected bundle again.')
            # Refresh the original preview token too, so a failed fork can be retried.
            async with app.lock: app._session(sid)['bundlePreview'] = checked
            if not checked['modelCompatible'] and not args.get('resetModel'):
                raise ValueError('Your pinned model is unavailable in this bundle. Select Use the new bundle’s model to continue.')
            from .session_store import fork_session
            from .host.storage import SessionStore
            target = app._new_session({'title': source['title']+' · fork', 'workspace': source['workspace'], 'bundle': args['bundle']})
            identity = target['id']
            store = SessionStore.for_app(app.data_dir, source['workspace'])
            # Capture the complete current context before copying. No user work is submitted.
            live = await app.runtime.control(sid, 'history.snapshot', {})
            events = []
            async def collect(event, payload): events.append((event, payload))
            try:
                check_current()
                target.update(fork_session(app.data_dir, source, identity, live_messages=live.get('messages'),
                                          bundle=args['bundle'], reset_model=args.get('resetModel', False)))
                check_current()
                await app.runtime.start(target, collect)
                configuration = await app.runtime.control(identity, 'configuration.inspect', {})
                providers = await app.runtime.control(identity, 'configuration.providers', {})
            except BaseException:
                await app.runtime.stop(identity)
                # These are newly allocated fork directories, never source data.
                for directory in (store.directory(identity), app.data_dir/'sessions'/identity):
                    if directory.exists(): shutil.rmtree(directory)
                raise
            async with app.lock:
                from .canvas_library import fork_artifacts, remember
                from .workspace_canvas import select_session_workspace
                from .naming import persist
                remember(app.state, app.db)
                app.clients.remember_canvas()
                target.update(configuration=configuration, status='ready')
                app.state['sessions'].insert(0, target)
                fork_artifacts(app.state, sid, target, app.db)
                # A slow fork must not retarget a different chat opened meanwhile.
                if app.state.get('selectedSessionId') == sid:
                    if app.clients.record() is None:
                        app._session(sid)['draft'] = app.state['view'].get('draft', '')
                    app.state['selectedSessionId'] = identity
                    app.state['view']['draft'] = ''
                    select_session_workspace(app.state, target)
                    app.clients.restore_canvas()
                persist(app.data_dir, target)
                app.state.setdefault('runtimeControl', {})[identity] = {'configuration.providers': providers}
                app._publish_full(reason='Bundle fork adds a session and copies its artifacts')
            await app.runtime.start(target, app.on_runtime_event)
            for event, payload in events:
                if event == 'runtime.status' and payload.get('report'):
                    await app.on_runtime_event(event, payload)
            result = {'sessionId': identity, 'bundle': args['bundle'], 'historyPreserved': True}
        async with app.lock:
            app._session(sid)['bundleChange'] = {'phase': 'ready', 'action': action, 'bundle': args['bundle'],
                                                **({'sessionId': result['sessionId']} if result.get('sessionId') else {})}
            app._publish_changes(sessions={sid})
    except BaseException as exc:
        async with app.lock:
            app._session(sid)['bundleChange'] = {'phase': 'error', 'action': action, 'bundle': args['bundle'], 'error': str(exc)[:1000]}
            app._publish_changes(sessions={sid})
        raise
    finally:
        async with app.lock:
            app._session(sid)['configurationBusy'] = False
            app._publish_changes(sessions={sid})
