"""Host-owned next-message intent; native bundle conversion stays in the worker."""
from __future__ import annotations

import copy


def definitions(schema, string):
    from .new_chat import SELECTION
    value = schema({'sessionId': string(200), 'expectedRevision': {'type': 'integer', 'minimum': 0},
        'bundle': {**string(2000), 'minLength': 1}, 'selection': SELECTION,
        'resetModel': {'type': 'boolean'}}, ['sessionId', 'expectedRevision'])
    value['anyOf'] = [{'required': ['bundle']}, {'required': ['selection']}]
    return {'conversation.configure': ('Save this conversation’s Next message bundle/model choices without preparing a worker. '
        'expectedRevision is nextMessageRevision, not the app revision. Saved is not mounted; application is validated on genuine input.', value)}


def target(session):
    return {key: session.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision')}


def pending_input(service, session):
    """Use existing admission evidence, never load retired display payloads."""
    from .execution import LIVE_PHASES, voice_usage_turns
    generation = session.get('collaborationGeneration') or {}
    if generation.get('id') and not generation.get('terminal'):
        return True
    # Ordinary, voice, question and scheduled input create an execution turn
    # before any preparation await, including sends without a command id.
    # ColdDisplay retains the same pending-work fact with its exact body ref.
    reference = dict.get(session, '_coldFields', {}).get('execution')
    if not dict.__contains__(session, 'execution') and reference is not None:
        if reference.get('pendingWork') is not False:
            return True  # Legacy/unknown is not proof that input has settled.
    else:
        tree = dict.get(session, 'execution', {})
        voice_turns = voice_usage_turns(tree)
        # The call owns its duration/response receipts independently of manager
        # work. A spoken input's manager turn still blocks configuration.
        if any(turn.get('phase') in LIVE_PHASES and turn['id'] not in voice_turns
               for turn in tree.get('turns', [])):
            return True
    # Keep original admission receipts authoritative even if presentation
    # status has become ready before delivery is acknowledged.
    return service.db.execute("""SELECT 1 FROM commands
        WHERE json_extract(receipt,'$.sessionId')=?
        AND json_extract(receipt,'$.inputId') IS NOT NULL
        AND json_extract(receipt,'$.delivery')='sending' LIMIT 1""", (session['id'],)).fetchone() is not None


def save(service, args, authority=None):
    """Under the normal host commit lock and command dedup, with no await."""
    from .errors import AppError
    from .new_chat import selection
    session = service._session(args['sessionId'], hydrate=False)
    revision = session.get('nextMessageRevision', 0)
    if args['expectedRevision'] != revision:
        raise AppError('Next message settings changed. Review the current choices before saving again.', 409)
    manager = service.management
    pending = session.get('pendingConfiguration', {})
    if (session.get('configurationBusy') or manager.unresolved_configuration(session)
            or pending.get('phase') in {'queued', 'applying'}):
        raise AppError('Resolve the existing configuration operation before changing Next message settings.', 409)
    # Preparing without accepted input is not a live turn. Never relabel actual
    # work or clear its ownership/fences merely to make a setting editable.
    if pending_input(service, session):
        raise AppError('Finish active work before changing Next message settings.', 409)
    # A ColdRecord copy/deepcopy intentionally materializes every cold field.
    # configuration_idle needs only these hot fields plus its peer-input claim.
    presentation = {key: session[key] for key in
        ('id', 'status', 'configurationBusy', 'pendingConfiguration', 'workers') if key in session}
    if presentation['status'] == 'starting':
        presentation['status'] = 'idle'
    if not manager.configuration_idle(presentation):
        raise AppError('Finish active work before changing Next message settings.', 409)
    if session.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
        raise AppError('This conversation is owned elsewhere. Continue here before changing its settings.', 409)
    if session.get('historyReadOnlyReason'):
        raise AppError(session['historyReadOnlyReason'], 409)
    selected = selection(args['selection']) if 'selection' in args else None
    if 'selection' in args and not selected:
        raise AppError('Choose a provider and model.', 400)
    if args.get('resetModel') and ('bundle' not in args or selected):
        raise AppError('Choose either an explicit model or the new bundle’s default model.', 400)
    bundle = args.get('bundle', '').strip()
    if 'bundle' in args and not bundle:
        raise AppError('Choose a bundle.', 400)
    if 'bundle' in args:
        if bundle == session.get('bundle') and not args.get('resetModel'):
            session.pop('pendingBundleSelection', None)
        else:
            session['pendingBundleSelection'] = {'bundle': bundle, 'resetModel': args.get('resetModel', False),
                'authority': copy.deepcopy(authority), 'target': target(session)}
        if args.get('resetModel'):
            session.pop('pendingModelSelection', None)
            session.pop('modelConnectionKey', None)
            session['selection'] = None
        session.pop('bundlePreview', None)
    if selected is not None:
        from .provider_catalog import configured_binding
        binding = configured_binding(service.data_dir, session['workspace'], selected['instance'],
            global_only=session.get('location',{}).get('kind')=='managed')
        session['modelConnectionKey'] = binding
        session['selection'] = copy.deepcopy(selected)
        session['pendingModelSelection'] = copy.deepcopy(selected)
        # An explicit model chosen after a bundle-default choice supersedes the
        # reset, without borrowing or renewing the original bundle authority.
        if session.get('pendingBundleSelection'):
            session['pendingBundleSelection']['resetModel'] = False
    session['nextMessageRevision'] = revision + 1
    session.pop('nextMessageError', None)
    session.pop('nextMessageFailure', None)
    session['nextMessageStatus'] = 'saved'
    return {'revision': revision + 1, 'status': 'saved', 'applies': 'next-message', 'mounted': False}


def bind(service, runtime):
    """Only host code supplies this callback. It never crosses the worker wire."""
    if callable(getattr(runtime, 'bind_input_configuration', None)):
        runtime.bind_input_configuration(lambda session: prepare(service, session))


def guard_for(service, captured):
    from .runtime import RuntimeStartupError
    sid = captured['id']
    revision = captured.get('nextMessageRevision', 0)
    interruption = captured.get('interruptionRevision', 0)
    configuration_revision = service.state.get('configurationRevision', 0)
    placement = target(captured)
    bundle = copy.deepcopy(captured.get('pendingBundleSelection'))
    def check():
        current = service._session(sid, hydrate=False)
        if current.get('interruptionRevision', 0) != interruption or current.get('status') in {'stopping', 'stopped'}:
            raise RuntimeStartupError('This preparation was stopped. No input was sent.')
        if target(current) != placement or current.get('nextMessageRevision', 0) != revision:
            raise RuntimeStartupError('Next message settings or execution placement changed. No input was sent.')
        if service.state.get('configurationRevision', 0) != configuration_revision:
            raise RuntimeStartupError('Connection settings changed during input preparation. No input was sent.')
        if service.management.unresolved_configuration(current) or current.get('configurationOperationFence'):
            raise RuntimeStartupError('The original configuration operation is unresolved. No input was sent.')
        if current.get('fileAccessPending'):
            raise RuntimeStartupError('File permissions are awaiting application. No input was sent.')
        if service.assignments.blocked(current):
            raise RuntimeStartupError('The assignment is stopped. No input was sent.')
        if bundle:
            if bundle.get('target') != placement:
                raise ValueError('The saved bundle target moved. Choose its settings again.')
            try:
                service.configuration_authority.check(bundle.get('authority'))
            except Exception as exc:
                raise RuntimeStartupError('The saved bundle authority changed. No input was sent.') from exc
    return check


async def prepare(service, captured):
    """Resolve on genuine input, without mounting the old bundle for validation.

    Return a startup snapshot and a synchronous guard checked by RuntimeManager
    after all waits and immediately before native input admission.
    """
    from .draft_defaults import resolve_defaults
    from .runtime import RuntimeStartupError
    session = copy.deepcopy(captured)
    check = guard_for(service, captured)
    pending = session.get('pendingBundleSelection')
    selection = session.get('pendingModelSelection')
    failure = None
    try:
        check()
        if pending:
            checked = await resolve_defaults(service.data_dir, session['workspace'], pending['bundle'],
                session_id=session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'],
                replacement={'selection': session.get('selection'), 'replaceSavedSelection': True,
                    'workingDirectory': session.get('workingDirectory')})
            check()
            if not checked.get('modelCompatible') and not pending.get('resetModel'):
                failure = {'code': 'incompatible-model', 'bundle': pending['bundle']}
                raise ValueError('The saved model connection is not available in this bundle. Choose a compatible model or explicitly use the bundle default.')
            session['bundleReplacement'] = {**checked, 'resetModel': pending.get('resetModel', False),
                'replaceSavedSelection': True, 'deferred': True}
            session['bundle'] = pending['bundle']
            selection = checked.get('selection')
            if pending.get('resetModel'):
                session.pop('pendingModelSelection', None)
                session.pop('selection', None)
                selection = None
            elif selection:
                session['selection'] = copy.deepcopy(selection)
                session['pendingModelSelection'] = copy.deepcopy(selection)
        # Listing informs capabilities, not model entitlement. Hidden/manual
        # exact IDs remain intent; account binding still gates native admission.
        if selection:
            resolved = await resolve_defaults(service.data_dir, session['workspace'], session.get('bundle'),
                service.state['settings'].get('appBundle'),
                session_id=session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'],
                catalog=True, desired_bundle=bool(pending),
                instance=selection['instance'], selected_only=True,
                catalog_cache=service.management.provider_catalog,
                global_only=session.get('location', {}).get('kind') == 'managed')
            check()
            validate_selection(selection, resolved)
            provider = next(row for row in resolved['providers'] if row['id']==selection['instance'])
            expected = session.get('modelConnectionKey')
            if expected and expected != provider.get('connectionKey'):
                raise ValueError('The configured connection changed after this choice was saved. Review the connection and save the model again.')
            session['modelConnectionKey'] = provider.get('connectionKey')
        if pending:
            # Existing warm ownership must settle before fresh-process bundle
            # replacement. A cold conversation has nothing to retire.
            if session['id'] in getattr(service.runtime, 'workers', {}):
                await service.runtime.stop(session['id'], reason='configuration')
                check()
        return session, check
    except Exception as exc:
        async with service.lock:
            current = service._session(session['id'], hydrate=False)
            if current.get('nextMessageRevision', 0) == session.get('nextMessageRevision', 0):
                current['nextMessageStatus'] = 'not-applied'
                current['nextMessageError'] = str(exc) if isinstance(exc, ValueError) else 'Could not validate Next message settings. Your choices are kept.'
                if failure:
                    current['nextMessageFailure'] = failure
                service._publish_changes(sessions={session['id']})
        raise RuntimeStartupError('Next message settings were not applied. No input was sent. Your choices are kept.') from exc


def validate_selection(selected, catalog):
    provider = next((row for row in catalog.get('providers', []) if row['id'] == selected['instance']), None)
    if provider is None:
        raise ValueError('The saved provider is unavailable. Choose an available connection.')
    entry = catalog.get('catalogs', {}).get(provider.get('sharedCatalogKey'), {})
    if provider.get('config', {}).get('chat_enabled') is False:
        raise ValueError('This connection is not enabled for chat.')
    # A recommendation filter (even a successful empty list) is not rejection
    # by the provider. An explicit model/deployment ID remains intent, never an
    # invented catalog row or an entitlement claim; invocation still validates
    # credentials, endpoint, mounted connection and the provider's response.
    metadata = entry.get('metadata') or {}
    if selected.get('effort') and isinstance(metadata.get('configSchema', {}).get('fields'), list):
        import re
        schema = entry.get('metadata', {}).get('configSchema', {})
        config = {'model': selected['model'], 'default_model': selected['model']}
        def shown(field):
            for key, expected in (field.get('show_when') or {}).items():
                actual, value = str(config.get(key, '')).lower(), str(expected).lower()
                if value.startswith('matches:'):
                    try:
                        if re.search(str(expected)[8:], actual, re.I) is None:
                            return False
                    except re.error:
                        return False
                    continue
                matched = False
                for prefix, predicate in (
                        ('not_contains:', lambda v: v not in actual), ('contains:', lambda v: v in actual),
                        ('not_startswith:', lambda v: not actual.startswith(v)), ('startswith:', lambda v: actual.startswith(v))):
                    if value.startswith(prefix):
                        if not predicate(value[len(prefix):]):
                            return False
                        matched = True
                        break
                if not matched and actual != value:
                    return False
            return True
        field = next((field for field in schema.get('fields', [])
            if field.get('id') == 'reasoning_effort' and shown(field)), None)
        choices = field.get('choices', []) if field else []
        if choices and selected['effort'] not in [row.get('value') if isinstance(row, dict) else row for row in choices]:
            raise ValueError('The saved reasoning effort is unavailable for this model.')


def applied(session, payload):
    """Exact revision acknowledgment cannot clear a newer A→B→A choice."""
    if payload.get('revision') != session.get('nextMessageRevision', 0):
        return
    pending = session.get('pendingBundleSelection')
    if pending and pending['bundle'] != payload.get('bundle'):
        return
    if pending:
        session['bundle'] = pending['bundle']
        session.pop('pendingBundleSelection', None)
        session.pop('bundlePreview', None)
    session.pop('pendingModelSelection', None)
    session['nextMessageStatus'] = 'applied'
    session['appliedConfigurationRevision'] = payload['revision']
    session.pop('nextMessageError', None)
    session.pop('nextMessageFailure', None)
