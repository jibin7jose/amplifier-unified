"""Application management actions shared by the UI and the agent."""
from __future__ import annotations
import asyncio
import copy
import json
from pathlib import Path
import time
import uuid
from .preferences import SettingsStore
from .host.storage import SessionStore

class Management:
    def __init__(self,service):
        self.service=service
        self.settings=SettingsStore(service.data_dir)
        self.lock=asyncio.Lock()
        from .notifications import Notifications
        self.notifications=Notifications(service.data_dir)
        service.state["notificationSettings"]=self.notifications.public()
        from .provider_catalog import ProviderCatalog
        self.provider_catalog=ProviderCatalog(service.data_dir / "cache" / "provider-catalogs.json")
        self.catalog_limit=asyncio.Semaphore(3)
        self.setup_manager=None
        self.global_setup_manager=None
        self.workspace_setup_managers={}
        self.pending_config_tasks={}
        self.placement_snapshots={}
        self.draft_default_tasks={}
        self.after_publication=[]
        pending_refresh=[session['id'] for session in service.state.get('sessions',[]) if session.get('configurationPending')]
        if pending_refresh:
            async def resume_refresh():
                for identity in pending_refresh:await service.refresh_configuration(identity)
            self.background(resume_refresh())
        restored = set()
        for session in service.state.get('sessions',[]):
            pending = session.get('pendingConfiguration', {})
            if pending.get('phase') not in {'queued', 'applying', 'error'}:
                continue
            try:
                record = self.configuration_record(session['id'], pending.get('operationId'))
                if pending.get('phase') == 'queued' and record['state']['stage'] == 'queued' and record['state']['effect'] == 'none':
                    self.schedule_configuration(session['id'])
                elif pending.get('phase') in {'queued', 'applying'}:
                    # A persisted dispatch can have saved settings before losing
                    # its result. Reopening is observation, never permission to retry.
                    self.configuration_transition(session, record['id'], phase='error',
                        stage='interrupted', error='Configuration application was interrupted; its original operation is retained and was not repeated.')
                if record['state']['effect'] != 'none' or pending.get('recoveryRequired'):
                    session['configurationBusy'] = True
                    session['configurationOperationFence'] = record['id']
            except (OSError, ValueError, KeyError, TypeError):
                # Older records have neither an immutable payload binding nor a
                # way to distinguish a user action from missing agent authority.
                # Keep the old payload/evidence; never reconstruct permission.
                session['pendingConfiguration'] = {**pending, 'phase': 'error',
                    'error': 'The original configuration operation cannot be verified. No pending change was repeated.',
                    'recoveryRequired': True}
                session['configurationBusy'] = True
                session['configurationOperationFence'] = pending.get('operationId')
            restored.add(session['id'])

        service._save_changes(sessions=restored, globals={'notificationSettings'})

    def background(self,operation):
        task=asyncio.create_task(operation)
        self.service.tasks.add(task)
        task.add_done_callback(self.service.tasks.discard)

    async def warm_providers(self,manager,workspace,*,refresh=False):
        catalogs={};pending=[]
        def catalog_entry(result,cache_key,**values):
            return {'sharedCatalogKey':cache_key[1],'models':(result or {}).get('models',[]),'supported':(result or {}).get('modelsSupported',True),'metadata':(result or {}).get('providerMetadata'),'loadedAt':self.provider_catalog.loaded_at.get(cache_key),**values}
        # Publish the cached catalog as one snapshot. Even fresh cache hits used
        # to rebuild and persist the entire app twice for every provider.
        with manager.read_snapshot(workspace):
            enabled=[row for row in manager.provider_rows(workspace) if row.get('enabled',True)]
            for row in enabled:
                key=manager.catalog_key({'id':row['id']},workspace)
                cache_key=('providers.models',key)
                cached=self.provider_catalog.peek(cache_key)
                fresh=not refresh and not row.get('authenticationRequired') and not (row.get('account') or {}).get('refreshable') and self.provider_catalog.fresh(cache_key)
                catalogs[row['id']]=catalog_entry(cached,cache_key,phase='ready' if fresh else 'working')
                if not fresh:pending.append((row,key,cache_key))
        async with self.service.lock:
            setup=self.service.state.setdefault('setup',{})
            if setup.get('providersWorkspace')!=workspace or (setup.get('providersLocation',{}).get('kind')=='managed')!=bool(getattr(manager,'global_only',False)):return
            before=copy.deepcopy({key:setup.get(key) for key in ('models','modelsProviderId','modelCatalogs','providerCatalogs','metadata')})
            valid=set(catalogs)
            if setup.get('modelsProviderId') not in valid:setup.update(models=[],modelsProviderId=None)
            from .conversation_models import publish_catalogs
            setup['providerCatalogs']=catalogs
            catalog_changed=publish_catalogs(self,{entry['sharedCatalogKey']:entry for entry in catalogs.values()})
            setup['modelCatalogs']={identity:entry['models'] for identity,entry in catalogs.items()}
            for row in enabled:
                if catalogs[row['id']].get('metadata'):
                    setup.setdefault('metadata',{})[row['module']]=catalogs[row['id']]['metadata']
            if catalog_changed or before!={key:setup.get(key) for key in before}:
                self.service._publish_changes(globals={'setup','modelCatalogs'})
        if not pending:return
        # Yield after the start snapshot so ordinary requests can proceed.
        await asyncio.sleep(0)
        async def load(row,key,cache_key,*,retry=True):
            identity=row['id'];args={'id':identity}
            async with self.catalog_limit:
                try:
                    result=await manager.perform('providers.models',{'id':identity,'workspace':workspace,'refresh':True})
                    entry=catalog_entry(result,cache_key,phase='ready')
                except Exception:
                    entry=catalog_entry(self.provider_catalog.peek(cache_key),cache_key,phase='error',error='Could not refresh models. Saved models remain available; retry or enter a model ID.')
            # An old request must never overwrite a newer config or workspace.
            current_key=manager.catalog_key(args,workspace)
            if current_key!=key:
                # Renewal changes credential identity. Discard the old result
                # and discover once under the new identity, without retagging.
                async with self.service.lock:
                    setup=self.service.state.setdefault('setup',{})
                    if setup.get('providersWorkspace')!=workspace or (setup.get('providersLocation',{}).get('kind')=='managed')!=bool(getattr(manager,'global_only',False)):return
                    setup['providers']=manager.provider_rows(workspace)
                    current_row=next((item for item in setup['providers'] if item['id']==identity and item.get('enabled',True)),None)
                    self.service._publish_progress(session_ids=set(), record_only=True, global_keys={'setup'})
                if retry and current_row:
                    await load(current_row,current_key,('providers.models',current_key),retry=False)
                return
            async with self.service.lock:
                setup=self.service.state.setdefault('setup',{})
                if setup.get('providersWorkspace')!=workspace or (setup.get('providersLocation',{}).get('kind')=='managed')!=bool(getattr(manager,'global_only',False)):return
                from .conversation_models import publish_catalogs
                setup['providers']=manager.provider_rows(workspace)
                setup.setdefault('providerCatalogs',{})[identity]=entry
                publish_catalogs(self,{entry['sharedCatalogKey']:entry})
                setup.setdefault('modelCatalogs',{})[identity]=entry['models']
                if entry.get('metadata'):setup.setdefault('metadata',{})[row['module']]=entry['metadata']
                self.service._publish_progress(session_ids=set(), record_only=True, global_keys={'setup','modelCatalogs'})
        try:
            await asyncio.gather(*(load(*item) for item in pending))
        finally:
            await self.service._flush_pending_progress()

    @staticmethod
    def runtime_catalog_key(session_id,row,revision=None):
        from .provider_catalog import model_key
        if row.get('sharedCatalogKey'):return model_key(row['sharedCatalogKey'])
        if row.get('catalogKey'):return ('mounted',row['catalogKey'])
        return ('mounted-session',session_id,revision,row['id'])

    async def runtime_models(self,session_id,row,revision=None,*,refresh=False):
        key=self.runtime_catalog_key(session_id,row,revision)
        async def load():
            # The host owns expiry/manual refresh; a second worker cache must
            # not extend the host's TTL after it decides a refresh is needed.
            result=await self.service.runtime.control(session_id,'configuration.providerModels',{'instance':row['id'],'refresh':True})
            if row.get('module') and 'info' in row and 'configSchema' in row:
                result={**result,'providerMetadata':{'module':row['module'],'info':row['info'],'configSchema':row['configSchema']}}
            return result
        return await self.provider_catalog.get(key,load,refresh=refresh)

    async def warm_runtime_models(self,session_id,providers,revision=None):
        """Reuse the host catalog and publish one snapshot per refresh batch."""
        def key(row):
            return self.runtime_catalog_key(session_id,row,revision)
        def entry(result,cache_key,phase):
            result=result or {}
            return {'phase':phase,'models':result.get('models',[]),
                    'supported':result.get('supported',result.get('modelsSupported',True)),
                    'loadedAt':self.provider_catalog.loaded_at.get(cache_key)}
        pending=[];catalogs={}
        for row in providers:
            cache_key=key(row)
            cached=self.provider_catalog.peek(cache_key)
            fresh=self.provider_catalog.fresh(cache_key)
            catalogs[row['id']]=entry(cached,cache_key,'ready' if fresh else 'working')
            if not fresh:pending.append((row,cache_key))
        async with self.service.lock:
            control=self.service.state.setdefault('runtimeControl',{}).setdefault(session_id,{})
            current_revision=control.get('configuration.providers',{}).get('catalogRevision')
            if revision and current_revision and current_revision!=revision:return
            before=(control.get('modelCatalogRevision'),control.get('modelCatalogs'))
            control.update(modelCatalogRevision=revision,modelCatalogs=catalogs)
            if before!=(revision,catalogs):self.service._publish_changes(sessions={session_id})
        if not pending:return
        await asyncio.sleep(0)
        async def load(row,cache_key):
            identity=row['id']
            try:
                async with self.catalog_limit:
                    result=await self.runtime_models(session_id,row,revision)
                value=entry(result,cache_key,'ready')
            except Exception:
                value={**entry(self.provider_catalog.peek(cache_key),cache_key,'error'),
                       'error':'Could not load models from this mounted provider. Retry to check the connection.'}
            async with self.service.lock:
                control=self.service.state.setdefault('runtimeControl',{}).setdefault(session_id,{})
                if control.get('modelCatalogRevision')!=revision:return
                current=control.get('configuration.providers',{}).get('providers',[])
                mounted=next((candidate for candidate in current if candidate['id']==identity),None)
                if current and (mounted is None or key(mounted)!=cache_key):return
                control.setdefault('modelCatalogs',{})[identity]=value
                self.service._publish_progress(session_ids={session_id}, record_only=True)
        try:
            await asyncio.gather(*(load(*item) for item in pending))
        finally:
            await self.service._flush_pending_progress()

    async def publish(self,**values):
        async with self.service.lock:
            self.service.state.update(values)
            self.service._publish_changes(globals=values)

    async def download(self,name,content,mime='application/json'):
        async with self.service.lock:
            effect={'id':str(uuid.uuid4()),'type':'download','filename':name,'content':content,'mimeType':mime,'createdAt':time.time()}
            self.service.state.setdefault('deviceCommands',[]).append(effect)
            self.service.state['deviceCommands']=self.service.state['deviceCommands'][-20:]
            self.service._publish_changes(globals={'deviceCommands'})

    async def provider_status(self,action,args,command_id,phase,error=None):
        await self.command_transition(action,args,command_id,phase,error,active=False)

    async def command_transition(self,action,args,command_id,phase,error=None,*,active=True):
        """Publish status, provider status and durable receipt atomically."""
        async with self.service.lock:
            statuses=self.service.state.setdefault('actionStatus',{})
            previous=statuses.get(action,{})
            if phase in {'queued','working'} or previous.get('commandId')==command_id:
                statuses[action]={'phase':phase,'error':error,'commandId':command_id,'updatedAt':time.time(),'configurationTarget':args.get('configurationTarget'),'target':{key:args[key] for key in ('id','section','name','controlId','sessionId','operation') if key in args}}
            if action.startswith('providers.'):
                key=action+':'+(args.get('module') if action in {'providers.credentials','providers.schema'} else args.get('id','') or '')
                from .configuration_target import operation_key
                key=operation_key(args,key)
                operations=self.service.state.setdefault('setup',{}).setdefault('operations',{})
                previous=operations.get(key,{})
                if phase in {'queued','working'} or previous.get('commandId')==command_id:
                    operations[key]={'phase':phase,'error':error,'commandId':command_id,'envVar':args.get('envVar',''),'updatedAt':time.time(),'configurationTarget':args.get('configurationTarget')}
            if active:self.service.state['management']={'phase':phase,'operation':action,'error':error}
            if phase in {'ready','error'}:self._record_completion(command_id,phase,error)
            self.service._publish_changes(globals={'actionStatus','setup','management','managementResults'})
            if phase in {'ready','error'}:
                callbacks,self.after_publication=self.after_publication,[]
                for callback in callbacks:self.background(callback())

    async def command(self,action,args,command_id=None,authority=None,source=None,*,configuration_operation=None):
        from .configuration_authority import CURRENT
        from functools import partial
        check = (partial(self.service.configuration_authority.check, authority)
            if authority is not None or configuration_operation is not None else CURRENT.get())
        if source is None and (action == 'configuration.apply'
                or action == 'runtime.control' and args.get('operation') == 'configuration.apply'):
            target = self.service._session(args.get('id') or args.get('sessionId'))
            source = {'origin': 'internal', 'target': self.configuration_target_binding(target)}
        token = CURRENT.set(check)
        try:
            return await self._command(action,args,command_id,authority,source,configuration_operation=configuration_operation)
        finally:
            CURRENT.reset(token)

    async def _command(self,action,args,command_id=None,authority=None,source=None,*,configuration_operation=None):
        if action == 'bundles.list':
            return await self.list_bundles(args, command_id)
        if action == 'providers.list':
            return await self.list_providers(args, command_id)
        independent=(action in {'bundle.preview','configuration.defaults','providers.list','providers.decisionModels','providers.credentials','providers.schema','providers.models','providers.imageModels','providers.test','providers.testMessage','routing.list','routing.show'}
                     or action == 'runtime.control' and args.get('operation') == 'configuration.catalog')
        try:
            if not independent and self.lock.locked():
                await self.provider_status(action,args,command_id,'queued')
            if not independent:await self.lock.acquire()
        except asyncio.CancelledError:
            await self.command_transition(action,args,command_id,'error','Settings operation cancelled.',active=False)
            raise
        try:
            guarded=None
            phase,error='ready',None
            try:
                await self.command_transition(action,args,command_id,'working')
                # A published start must reach clients before synchronous
                # settings work; uncontended asyncio locks do not yield.
                await asyncio.sleep(0)
                if action=='bundle.save':
                    async with self.service.lock:
                        current=self.service._session(args['sessionId'])
                        if not self.configuration_idle(current):
                            raise ValueError('Finish active work before saving this conversation bundle')
                        current['configurationBusy']=True;guarded=current['id']
                        self.service._publish_changes(sessions={guarded})
                await self.perform(action,args,command_id=command_id,
                    **({"authority":authority} if authority is not None else {}),
                    **({"source":source} if source is not None else {}),
                    **({"configuration_operation":configuration_operation} if configuration_operation is not None else {}))
            except asyncio.CancelledError:
                phase,error='error','Settings operation cancelled.'
                raise
            except Exception as exc:
                # Host-controlled validation text only; provider/transport errors
                # are converted before reaching this boundary.
                phase,error='error',str(exc)[:1000]
                if action in {'configuration.apply', 'configuration.patchPlacement.bind'} and args.get('whenIdle') and self.queued_path(args['id']).exists():
                    async with self.service.lock:
                        current = self.service._session(args['id'])
                        if configuration_operation and current.get('pendingConfiguration', {}).get('operationId') == configuration_operation:
                            self.configuration_error(current, configuration_operation, error)
                        elif not current.get('pendingConfiguration', {}).get('operationId'):
                            current['pendingConfiguration'] = {**current.get('pendingConfiguration', {}), 'phase':'error', 'error':error}
                        self.service._publish_changes(sessions={current['id']})
            finally:
                if guarded:
                    async with self.service.lock:
                        for session in self.service.state['sessions']:
                            if session['id']==guarded:session['configurationBusy']=False
                        self.service._publish_progress(session_ids={guarded}, record_only=True)
                await self.command_transition(action,args,command_id,phase,error)
        finally:
            if not independent:self.lock.release()

    async def list_providers(self,args,command_id):
        """One start and one result publication for a provider catalog read."""
        from .setup import SetupManager
        from .managed_chats import is_managed
        operation_id=command_id or str(uuid.uuid4())
        await self._provider_list_transition(args,command_id,operation_id,'working')
        await asyncio.sleep(0)
        revision=self.service.state.get('configurationRevision',0)
        try:
            from .configuration_target import resolve
            target=resolve(self.service,args)
            managed=target['global_only'] if target else is_managed(args)
            workspace=(str(self.service.data_dir) if managed else str(Path(args['workspace']).expanduser().resolve())
                       if args.get('workspace') else self.configuration_session(args)['workspace'])
            manager=SetupManager(self.service.data_dir,catalog=self.provider_catalog,
                                 allow_missing_workspace=bool(args.get('workspace')),global_only=managed)
            result=await manager.perform('providers.list',{**args,'workspace':workspace})
            result['configurationTarget']=args.get('configurationTarget')
            result['providersRequestedWorkspace']=args.get('workspace',workspace)
            result['providersLocation']={'kind':'managed' if managed else 'workspace'}
            if await self._provider_list_transition(args,command_id,operation_id,'ready',result=result,revision=revision):
                self.background(self.warm_providers(manager,workspace,refresh=bool(args.get('refresh'))))
        except asyncio.CancelledError:
            await self._provider_list_transition(args,command_id,operation_id,'error',error='Provider listing cancelled.')
            raise
        except Exception as exc:
            await self._provider_list_transition(args,command_id,operation_id,'error',error=str(exc)[:1000])

    async def _provider_list_transition(self,args,command_id,operation_id,phase,*,result=None,error=None,revision=None):
        async with self.service.lock:
            state=self.service.state;setup=state.setdefault('setup',{})
            operations=setup.setdefault('operations',{})
            from .configuration_target import operation_key
            key=operation_key(args,'providers.list:')
            current=phase=='working' or operations.get(key,{}).get('commandId')==operation_id
            if current:
                operations[key]={'phase':phase,'error':error,'commandId':operation_id,'envVar':'','updatedAt':time.time(),'configurationTarget':args.get('configurationTarget')}
                state.setdefault('actionStatus',{})['providers.list']={'phase':phase,'error':error,'commandId':operation_id,'updatedAt':time.time(),'configurationTarget':args.get('configurationTarget'),
                    'target':{key:args[key] for key in ('id','section','name','controlId','sessionId','operation') if key in args}}
                state['management']={'phase':phase,'operation':'providers.list','error':error}
                if result is not None and (revision is None or revision==state.get('configurationRevision',0)):
                    from .configuration_target import state_for
                    setup=state_for(self.service,'setup',args)
                    if not args.get('configurationTarget') and (setup.get('providersWorkspace')!=result.get('providersWorkspace') or setup.get('providersLocation',{}).get('kind','workspace')!=result['providersLocation']['kind']):
                        setup.update(modelCatalogs={},providerCatalogs={},imageCatalogs={},metadata={},models=[],modelsProviderId=None)
                    setup.update(result)
            if phase in {'ready','error'}:self._record_completion(command_id,phase,error)
            if current or command_id:self.service._publish_changes(globals={'actionStatus','setup','management','managementResults'})
            return current and (revision is None or revision==state.get('configurationRevision',0))

    async def list_bundles(self, args, command_id):
        """Read atomic saved settings without waiting for a runtime mutation."""
        action = 'bundles.list'
        operation_id = command_id or str(uuid.uuid4())
        revision = self.service.state.get('configurationRevision', 0)
        try:
            await self._bundle_list_transition(args, command_id, operation_id, 'working')
            # Uncontended async locks do not yield. Let ready clients consume
            # the start snapshot before the final catalog is published.
            await asyncio.sleep(0)
            from .bundles import BundleManager
            workspace = self.configuration_session(args)['workspace']
            result = await BundleManager(self.service.data_dir).perform(
                action, {**args, 'workspace': workspace})
            if revision != self.service.state.get('configurationRevision', 0):
                raise ValueError('Bundle settings changed during discovery. Refresh the choices.')
            await self._bundle_list_transition(args, command_id, operation_id, 'ready',
                values={key: result[key] for key in ('bundles', 'registeredBundles') if key in result})
        except asyncio.CancelledError:
            await self._bundle_list_transition(args, command_id, operation_id, 'error', error='Bundle refresh cancelled.')
            raise
        except Exception as exc:
            await self._bundle_list_transition(args, command_id, operation_id, 'error', error=str(exc)[:1000])
    async def _bundle_list_transition(self, args, command_id, operation_id, phase, *, error=None, values=None):
        async with self.service.lock:
            state = self.service.state
            statuses = state.setdefault('actionStatus', {})
            previous = statuses.get('bundles.list', {})
            changed = {'managementResults'}
            if phase == 'working' or previous.get('commandId') == operation_id:
                state.update(values or {})
                statuses['bundles.list'] = {'phase': phase, 'error': error,
                    'commandId': operation_id, 'updatedAt': time.time(),
                    'target': {key: args[key] for key in ('id', 'section', 'name', 'controlId') if key in args}}
                changed.update({'actionStatus', *(values or {})})
                # Independent catalog reads do not replace a mutation's progress.
                if not self.lock.locked():
                    state['management'] = {'phase': phase, 'operation': 'bundles.list', 'error': error}
                    changed.add('management')
            if phase in {'ready', 'error'}:
                self._record_completion(command_id, phase, error)
            self.service._publish_changes(globals=changed)

    def _record_completion(self, identity, phase, error=None):
        if identity:
            results = self.service.state.setdefault('managementResults', {})
            results[identity] = {'phase': phase, 'error': error}
            while len(results) > 100:
                results.pop(next(iter(results)))

    async def complete(self,identity,phase,error=None):
        if not identity:return
        async with self.service.lock:
            self._record_completion(identity, phase, error)
            self.service._publish_changes(globals={'managementResults'})

    def session(self,args):
        return copy.deepcopy(self.service._session(args.get('sessionId')))

    def configuration_session(self, args):
        from .configuration_target import resolve
        target=resolve(self.service,args)
        if target:return {'workspace':target['workspace']}
        identity = args.get('sessionId') or self.service.state.get('selectedSessionId')
        session = self.session({'sessionId': identity}) if identity else {}
        if session.get('nativeProject') and args.get('scope', 'global') != 'global':
            if not session.get('workspace') or not Path(session['workspace']).is_dir():
                raise ValueError('Choose an existing workspace before changing project or local settings.')
        return {**session, 'workspace': session.get('workspace') or self.service.state['settings'].get('workspace') or self.service.default_workspace}

    def configuration_idle(self,session):
        submitting = self.service.db.execute("""SELECT 1 FROM commands
            WHERE json_extract(receipt,'$.commandAction')='coordination.send'
            AND json_extract(receipt,'$.target.sessionId')=?
            AND json_extract(receipt,'$.delivery')='submitting' LIMIT 1""", (session['id'],)).fetchone()
        return not submitting and not self.unresolved_configuration(session) and session['status'] in {'idle','ready','stopped','interrupted','error'} and not session.get('configurationBusy') and not any(w.get('status') in {'running','working','starting','queued'} or w.get('persistent') and w.get('status')=='idle' for w in session.get('workers',[]))

    @staticmethod
    def unresolved_configuration(session):
        pending = session.get('pendingConfiguration', {})
        return bool(pending.get('recoveryRequired')
            or pending.get('phase') in {'queued', 'applying', 'error'}
            and (not pending.get('operationId') or not pending.get('requestHash'))
            or pending.get('phase') in {'error', 'applying'} and pending.get('effect', 'unknown') != 'none')

    @staticmethod
    def configuration_readback_matches(expected, actual):
        """Only normalize additions made by RuntimeControls.configuration().

        Lists remain ordered and nested configuration dictionaries remain exact.
        Unrecognized normalization fails closed instead of hiding a lost edit.
        Redacted public equality is not proof of the underlying secret bytes.
        """
        from .runtime_controls import PLAN_KEYS, SECTIONS, public_config
        from .configuration_authority import fingerprint
        def normalized(value):
            value = {key: copy.deepcopy(item) for key, item in public_config(value).items() if key in PLAN_KEYS}
            for section in SECTIONS:
                rows = value.setdefault(section, [])
                for row in rows:
                    row.setdefault('enabled', True)
            return value
        return fingerprint(normalized(expected)) == fingerprint(normalized(actual))

    def queued_path(self,identity):
        return self.service.data_dir/'sessions'/identity/'pending-configuration.json'

    def configuration_record_path(self, identity, operation):
        if not isinstance(operation, str) or str(uuid.UUID(operation)) != operation:
            raise ValueError('The original configuration operation identity is missing.')
        return self.queued_path(identity).parent/'configuration-operations'/f'{operation}.json'

    def configuration_record(self, identity, operation):
        from .configuration_authority import fingerprint
        record = json.loads(self.configuration_record_path(identity, operation).read_text())
        if record['version'] != 1 or record['id'] != operation or record['request']['sessionId'] != identity:
            raise ValueError('The original configuration operation binding changed.')
        pending = self.service._session(identity).get('pendingConfiguration', {})
        if (record.get('requestHash') != fingerprint(record['request'])
                or pending.get('operationId') == operation and pending.get('requestHash') != record['requestHash']):
            raise ValueError('The original configuration request binding changed.')
        return record

    def configuration_transition(self, session, operation, *, recovery_source=None, **changes):
        """Called under service.lock (or during startup); never replace request/history."""
        from .host.config import write_private
        if session.get('pendingConfiguration', {}).get('operationId') != operation:
            raise ValueError('The pending configuration operation changed.')
        record = self.configuration_record(session['id'], operation)
        record['state'].update(changes)
        record['revision'] += 1
        record['history'].append({'revision': record['revision'], 'at': time.time(), **copy.deepcopy(record['state']),
            **({'recoverySource': copy.deepcopy(recovery_source)} if recovery_source is not None else {})})
        write_private(self.configuration_record_path(session['id'], operation), json.dumps(record))
        # Private payload and full provenance stay in the operation record,
        # never the shared state or command acceptance receipt.
        session['pendingConfiguration'].update(copy.deepcopy(record['state']))
        session['pendingConfiguration']['revision'] = record['revision']
        return record

    def configuration_error(self, session, operation, error):
        try:
            return self.configuration_transition(session, operation, phase='error', error=error)
        except (OSError, ValueError, KeyError, TypeError):
            # Corrupt/missing private evidence is not an empty user grant. Keep
            # it untouched, and never clear admission based on the cached phase.
            pending = session.get('pendingConfiguration', {})
            if pending.get('operationId') == operation:
                pending.update(phase='error', recoveryRequired=True,
                    error='The original configuration operation cannot be verified. No automatic recovery was attempted.')
                session['configurationBusy'] = True
            return None

    @staticmethod
    def configuration_target_binding(session):
        return {'sessionId': session['id'], 'workspace': session['workspace'],
            'workingDirectory': session.get('workingDirectory'),
            'executionRevision': session.get('executionRevision', 0),
            'interruptionRevision': session.get('interruptionRevision', 0)}

    def recovery_admission_binding(self, session):
        """Bind a deferred control before its management-queue wait."""
        from .configuration_authority import fingerprint
        controls = self.service.state.get('runtimeControl', {}).get(session['id'], {})
        return {**self.configuration_target_binding(session),
            'configurationRevision': self.service.state.get('configurationRevision', 0),
            'controlRevision': session.get('recoveryControlRevision', 0),
            'contractSha256': fingerprint({
                'bundle': session.get('bundle'), 'selection': session.get('selection'),
                'configurationPendingRevision': session.get('configurationPendingRevision'),
                'controls': {key: controls.get(key) for key in (
                    'configuration.apply', 'configuration.toggle', 'provider.select',
                    'provider.reset', 'mode.set', 'mode.clear', 'budget.set', 'budget.clear')}})}

    def settle_configuration_fence(self, session, operation):
        """Only the operation that acquired this fence can release it."""
        if session.get('configurationOperationFence') != operation:
            return
        pending = session.get('pendingConfiguration', {})
        held = bool(pending.get('recoveryRequired') or
            pending.get('effect') != 'none' and pending.get('phase') != 'ready')
        session['configurationBusy'] = held
        if not held:
            session.pop('configurationOperationFence', None)

    def create_configuration_operation(self, session, config, command_id, authority, source):
        from .configuration_authority import CURRENT, fingerprint
        from .host.config import write_private
        previous = session.get('pendingConfiguration', {})
        if previous and previous.get('phase') not in {'ready', 'cancelled'}:
            raise ValueError('Resolve the original pending configuration operation before submitting another plan.')
        if authority is None and CURRENT.get() is not None:
            raise ValueError('The original configuration authority must be retained for a delayed change.')
        source = copy.deepcopy(source) if source is not None else {
            'origin': 'internal', 'target': self.configuration_target_binding(session)}
        if source.get('target') != self.configuration_target_binding(session):
            raise ValueError('The configuration target was stopped or moved after admission. No change was applied.')
        if authority is None and source.get('origin') not in {'ui', 'user', 'internal'}:
            raise ValueError('The original agent configuration authority is missing.')
        operation = str(uuid.uuid4())
        if self.configuration_record_path(session['id'], operation).exists():
            raise ValueError('Configuration operation identity already exists.')
        request = {**source['target'], 'commandId': command_id or operation,
            'source': source,
            'authorization': 'grant' if authority is not None else source['origin'],
            'authority': copy.deepcopy(authority), 'config': copy.deepcopy(config),
            'payloadHash': fingerprint(config)}
        record = {'version': 1, 'id': operation, 'request': request, 'requestHash': fingerprint(request), 'revision': 1,
            'state': {'phase': 'queued', 'stage': 'queued', 'effect': 'none'},
            'history': [{'revision': 1, 'at': time.time(), 'phase': 'queued', 'stage': 'queued', 'effect': 'none'}]}
        # Preserve this original even after cancellation or a later operation.
        write_private(self.configuration_record_path(session['id'], operation), json.dumps(record))
        write_private(self.queued_path(session['id']), json.dumps(config))
        session['pendingConfiguration'] = {'operationId': operation, 'commandId': request['commandId'],
            'payloadHash': request['payloadHash'], 'requestHash': record['requestHash'], 'revision': 1, **record['state'],
            **({'authority': copy.deepcopy(authority)} if authority is not None else {}),
            'detail': 'Waiting for the current turn and worker lanes to finish.'}
        return record

    def check_configuration_operation(self, session, record):
        from .configuration_authority import fingerprint
        request = record['request']
        if request['source'].get('action') == 'configuration.patchPlacement.bind':
            from .patch_placement import check_request
            check_request(self, session, record)
        if (request['sessionId'] != session['id'] or request['workspace'] != session['workspace']
                or request['workingDirectory'] != session.get('workingDirectory')
                or request['executionRevision'] != session.get('executionRevision', 0)
                or request['interruptionRevision'] != session.get('interruptionRevision', 0)):
            raise ValueError('The original configuration target was stopped or moved. No delayed change was repeated.')
        if request['authorization'] == 'grant':
            if not request.get('authority'):
                raise ValueError('The original configuration authority is missing.')
            self.service.configuration_authority.check(request['authority'])
        elif request['authorization'] not in {'ui', 'user', 'internal'} or request.get('authority') is not None:
            raise ValueError('The original configuration authority cannot be verified.')
        for retained in record.get('resumptions', []):
            self.service.configuration_authority.check(retained)
        if (fingerprint(request['config']) != request['payloadHash']
                or fingerprint(json.loads(self.queued_path(session['id']).read_text())) != request['payloadHash']):
            raise ValueError('The original configuration payload changed. It was not applied.')

    def schedule_configuration(self, identity):
        task = self.pending_config_tasks.get(identity)
        if task is None or task.done() or task.cancelling():
            task = asyncio.create_task(self.apply_queued(identity))
            self.pending_config_tasks[identity] = task
            self.service.tasks.add(task)
            task.add_done_callback(self.service.tasks.discard)

    async def apply_queued(self,identity):
        operation = None
        operation_action = 'configuration.apply'
        try:
            while not self.service.closed:
                async with self.service.lock:
                    session = self.service._session(identity)
                    pending = session.get('pendingConfiguration', {})
                    if pending.get('phase') != 'queued':
                        return
                    operation = pending.get('operationId')
                    record = self.configuration_record(identity, operation)
                    operation_action = record['request']['source'].get('action', 'configuration.apply')
                    if record['state']['stage'] != 'queued' or record['state']['effect'] != 'none':
                        raise ValueError('This configuration operation already entered application; it was not repeated.')
                    self.check_configuration_operation(session, record)
                    if self.configuration_idle(session):
                        # The same host admission fence is visible before any
                        # runtime-preparation await or management-lock wait.
                        session['configurationBusy'] = True
                        session['configurationOperationFence'] = operation
                        record = self.configuration_transition(session, operation, phase='applying', stage='preparing')
                        self.service._publish_changes(sessions={identity})
                        break
                await asyncio.sleep(.5)
            else:
                return
            request = record['request']
            await self.command(request['source'].get('action', 'configuration.apply'),
                {'id': identity, 'config': request['config'], 'whenIdle': True},
                request['commandId'], request['authority'], request['source'], configuration_operation=operation)
        except asyncio.CancelledError:
            async with self.service.lock:
                session = self.service._session(identity)
                pending = session.get('pendingConfiguration', {})
                if (operation and pending.get('operationId') == operation
                        and pending.get('phase') == 'applying' and pending.get('effect') == 'none'):
                    self.configuration_error(session, operation, 'Configuration preparation was cancelled before dispatch.')
                    self.service._publish_changes(sessions={identity})
            raise
        except Exception as exc:
            failed_command = None
            message = ('Patch placement admission failed; the retained operation was not replayed.'
                if operation_action == 'configuration.patchPlacement.bind' else str(exc)[:1000])
            async with self.service.lock:
                session = self.service._session(identity)
                if operation and session.get('pendingConfiguration', {}).get('operationId') == operation:
                    self.configuration_error(session, operation, message)
                    failed_command = session['pendingConfiguration'].get('commandId')
                self.service._publish_changes(sessions={identity})
            if failed_command:
                await self.command_transition(operation_action, {'id': identity}, failed_command, 'error', message)
        finally:
            if self.pending_config_tasks.get(identity) is asyncio.current_task():self.pending_config_tasks.pop(identity,None)
            async with self.service.lock:
                session = self.service._session(identity)
                pending = session.get('pendingConfiguration', {})
                if operation and pending.get('operationId') == operation:
                    # No-effect refusal releases admission. Unknown/saved state
                    # does not become permission to run under an uncertain mount.
                    self.settle_configuration_fence(session, operation)
                    self.service._publish_changes(sessions={identity})
                    if not session.get('configurationBusy'):
                        self.background(self.service.collaboration.drain(identity))

    async def ensure_runtime(self,session,*,selection_override=None,bundle_replacement=None,restart_guard=None,preserve_configuration=False):
        if self.unresolved_configuration(self.service._session(session['id'])):
            raise ValueError('The original configuration application is unresolved. Runtime preparation cannot reconcile or repeat it.')
        if session.get('nativeProject'):
            if session.get('historyReadOnlyReason'):
                raise ValueError(session['historyReadOnlyReason'])
            if not session.get('workspace') or not Path(session['workspace']).is_dir():
                raise ValueError('Restore this project folder before continuing its chat.')
        if not self.service.runtime:raise ValueError('Amplifier runtime is unavailable')
        from .updates import work_paused
        if work_paused(self.service.state):raise ValueError('An update is activating; retry shortly')
        # Explicitly using runtime controls makes this a web-owned presentation.
        if session.get('historyManaged') and not session.get('historyLoaded'):
            await self.service.history.ensure_loaded(session['id'])
        async with self.service.lock:
            if restart_guard is not None: restart_guard()
            current = self.service._session(session['id'])
            current['historyManaged'] = False
            session.update(copy.deepcopy(current))
        if selection_override is not None:
            # Only an explicit model-control action may replace a saved pin.
            # This transient startup request is not an input or an automatic retry.
            session['selection'] = copy.deepcopy(selection_override)
            session['replaceSavedSelection'] = True
        if bundle_replacement is not None:
            session['bundleReplacement'] = copy.deepcopy(bundle_replacement)
            session['bundle'] = bundle_replacement['bundle']
            if bundle_replacement.get('resetModel'):
                session.pop('selection', None)
        from .configuration_authority import check_current
        check_current()
        await self.service.runtime.start(session,self.service.on_runtime_event,
            **({'guard': restart_guard} if restart_guard is not None else {}),
            **({'preserve_configuration': True} if preserve_configuration else {}))

    async def recover_provider_catalog(self, session, operation, args):
        if operation not in {'configuration.providers', 'configuration.providerModels'}:
            return False
        from .provider_recovery import catalog
        recovery = catalog(self.service.data_dir, session)
        if recovery is None:
            return False
        result = recovery
        if operation == 'configuration.providerModels':
            identity = args.get('instance') or args.get('provider')
            row = next((row for row in recovery['providers'] if row['id'] == identity), None)
            if row is None:
                raise ValueError('Choose an available replacement connection')
            # Only the actual mounted connection can discover its models. The
            # recovery view retains its last default until explicit selection
            # repairs startup; never probe a similarly named settings entry.
            model = row['info']['defaults']['model']
            result = {'provider': identity, 'models': [model] if model else [], 'supported': False}
        async with self.service.lock:
            current = self.service._session(session['id'])
            if current.get('status') != 'error' or catalog(self.service.data_dir, current) != recovery:
                return True
            controls = self.service.state.setdefault('runtimeControl', {}).setdefault(session['id'], {})
            controls['configuration.providers'] = recovery
            controls[operation] = result
            if operation == 'configuration.providerModels':
                controls.setdefault('modelCatalogs', {})[result['provider']] = {
                    'phase': 'ready', 'models': result.get('models', []), 'supported': result.get('supported', True)}
            self.service._publish_progress(session_ids={session['id']}, record_only=True)
        return True

    async def invalidate_configuration(self,*,publish=True,warm=True,session_ids=None):
        from .setup import SetupManager
        workspace=self.service.state.get('setup',{}).get('providersWorkspace')
        if workspace and warm:self.background(self.warm_providers(SetupManager(self.service.data_dir,catalog=self.provider_catalog),workspace))
        claimed=[]
        changed=set()
        async with self.service.lock:
            # Drafts have no session to refresh. Re-resolve their defaults and
            # prevent an in-flight probe from restoring the old configuration.
            self.service.state['configurationRevision'] = self.service.state.get('configurationRevision', 0) + 1
            self.service.state['draftDefaults'] = {}
            self.service.state.setdefault('setup', {}).pop('providersRequestedWorkspace', None)
            for session in self.service.state['sessions']:
                if (not session.get('historyManaged') or session.get('fileAccessPending')) and (session_ids is None or session['id'] in session_ids):
                    changed.add(session['id'])
                    session['configurationPending']=True
                    session['configurationPendingRevision']=self.service.state['configurationRevision']
                    if not session.get('configurationBusy'):
                        session['configurationRefresh']={'phase':'pending','revision':self.service.state['configurationRevision']}
                    refresh=self.service._claim_configuration_refresh(session)
                    if refresh:claimed.append(refresh)
            if publish:self.service._publish_changes(sessions=changed, globals={'configurationRevision','draftDefaults','setup'})
            else:self.service._publish_progress(session_ids=changed, record_only=True, global_keys={'configurationRevision','draftDefaults','setup'})
        # Reservations are already in the final save snapshot, so another
        # request cannot admit input to an idle worker being retired.
        async def retire():
            await asyncio.sleep(0)
            limit=asyncio.Semaphore(3)
            async def one(item):
                async with limit:await self.service._refresh_claimed_configuration(*item)
            await asyncio.gather(*(one(item) for item in claimed))
            await self.service._flush_pending_progress()
        if claimed:
            if publish:self.background(retire())
            else:self.after_publication.append(retire)

    async def perform(self,action,args,command_id=None,authority=None,*,configuration_operation=None,source=None):
        from .configuration_authority import check_current
        check_current()
        if action=='locations.create':
            from .locations import create_folder
            path = await asyncio.to_thread(create_folder, args['path'], args['name'])
            await self.publish(locationListing={'controlId':args['controlId'],'path':str(path),'parent':str(path.parent),'entries':[],'truncated':False,'createdBy':command_id})
        elif action=='locations.list':
            path=Path(args.get('path') or self.service.default_workspace).expanduser()
            if not path.is_absolute():path=Path(self.service.default_workspace)/path
            path=path.resolve()
            if path.is_file():path=path.parent
            if not path.is_dir():raise ValueError('This folder does not exist. Enter a different location.')
            try:
                entries=[]
                for child in path.iterdir():
                    if child.name.startswith('.'):continue
                    try:
                        directory=child.is_dir()
                        if not directory and (args.get('directoriesOnly') or not child.is_file()):continue
                        entries.append({'name':child.name,'path':str(child),'directory':directory})
                    except OSError:continue
                entries.sort(key=lambda row:(not row['directory'],row['name'].casefold()))
            except PermissionError:raise ValueError('This folder is not readable. Choose another location.') from None
            await self.publish(locationListing={'controlId':args['controlId'],'path':str(path),'parent':str(path.parent),'entries':entries[:300],'truncated':len(entries)>300})
        elif action.startswith(('modules.','sources.')):
            from .registry import RegistryManager
            session=self.configuration_session(args)
            result=await RegistryManager(self.service.data_dir,store=self.settings).perform(action,{**args,'workspace':session['workspace']})
            result['configurationTarget']=args.get('configurationTarget')
            async with self.service.lock:
                from .configuration_target import state_for
                state_for(self.service,'registry',args).update(result)
                self.service._publish_changes(globals={'registry'})
            if action == 'modules.reconcile' and result.get('takesEffect'):
                # Reconciliation changes future defaults, never a loaded or
                # saved plan. Do not retire/remount any existing conversation.
                await self.invalidate_configuration(warm=False, session_ids=set())
            elif action.endswith(('.save','.remove')) and result.get('takesEffect'):
                await self.invalidate_configuration()
        elif action=='configuration.defaults':
            from .draft_defaults import resolve_defaults
            from .managed_chats import is_managed
            managed = is_managed(args)
            workspace = str(self.service.data_dir) if managed else args.get('workspace', '')
            if not workspace or managed and args.get('workspace', '').strip():
                raise ValueError('Choose a workspace or a chat without a workspace.')
            key=json.dumps(['' if managed else workspace,args.get('bundle') or '']+(['managed'] if managed else []),separators=(',',':'),ensure_ascii=False)
            revision = self.service.state.get('configurationRevision', 0)
            identity=(key,revision)
            task=self.draft_default_tasks.get(identity)
            if task is None:
                async def load():
                    try:
                        result=await resolve_defaults(self.service.data_dir,workspace,args.get('bundle'),self.service.state['settings'].get('appBundle'),**({'global_only':True} if managed else {}))
                        result['phase']='ready'
                    except Exception:
                        result={'phase':'error','error':'Could not resolve this bundle’s model. Retry model settings or choose a provider.'}
                    async with self.service.lock:
                        if revision != self.service.state.get('configurationRevision', 0):
                            return
                        entries=self.service.state.setdefault('draftDefaults',{})
                        entries[key]=result
                        while len(entries)>32:entries.pop(next(iter(entries)))
                        self.service._publish_changes(globals={'draftDefaults'})
                # Multiple clients/retries share an in-flight probe, but never
                # share across workspace, bundle, location or settings revision.
                task=asyncio.create_task(load())
                self.draft_default_tasks[identity]=task
                self.service.tasks.add(task)
                def finished(done):
                    self.service.tasks.discard(done)
                    if self.draft_default_tasks.get(identity) is done:
                        self.draft_default_tasks.pop(identity,None)
                task.add_done_callback(finished)
            await asyncio.shield(task)
        elif action.startswith(('providers.','routing.')):
            from .setup import SetupManager
            from .managed_chats import is_managed
            from .configuration_target import resolve
            target=resolve(self.service,args)
            managed_draft = target['global_only'] if target else action in {'providers.list','providers.decisionModels','providers.models','providers.imageModels'} and is_managed(args)
            session=({'workspace':str(self.service.data_dir)} if managed_draft else {'workspace':str(Path(args['workspace']).expanduser().resolve())}
                     if managed_draft or action in {'providers.list','providers.decisionModels','providers.models','providers.imageModels'} and args.get('workspace')
                     else self.configuration_session(args))
            async def runtime_operation(operation,values):
                current=self.session(args)
                await self.ensure_runtime(current)
                return await self.service.runtime.control(current['id'],operation,values)
            async def progress(values):
                values=dict(values)
                changed=values.pop('loginConfigurationChanged',False)
                async with self.service.lock:
                    setup=self.service.state.setdefault('setup',{})
                    if not target and 'providersWorkspace' in values and setup.get('providersWorkspace') not in {None,values['providersWorkspace']}:
                        values={key:value for key,value in values.items() if key not in {'providers','providersWorkspace','providersLoadedAt'}}
                    from .configuration_target import state_for
                    setup=state_for(self.service,'setup',args)
                    setup.update(values)
                    self.service._publish_changes(globals={'setup'})
                if changed:await self.invalidate_configuration(warm=False)
            manager_key='global_setup_manager' if target and target['global_only'] else 'setup_manager'
            workspace_key=args.get('configurationTarget',{}).get('workspaceId')
            persistent=self.workspace_setup_managers.get(workspace_key) if workspace_key else getattr(self,manager_key)
            if persistent is None:
                persistent=SetupManager(self.service.data_dir,runtime_operation=runtime_operation,progress=progress,catalog=self.provider_catalog,global_only=bool(target and target['global_only']))
                if workspace_key:self.workspace_setup_managers[workspace_key]=persistent
                else:setattr(self,manager_key,persistent)
            else:
                persistent.runtime_operation=runtime_operation
                persistent.progress=progress
            manager=SetupManager(self.service.data_dir,catalog=self.provider_catalog,allow_missing_workspace=bool(args.get('workspace')),global_only=managed_draft) if action in {'providers.list','providers.decisionModels','providers.credentials','providers.schema','providers.models','providers.imageModels','providers.test','providers.testMessage','routing.list','routing.show'} else persistent
            probe_key=manager.catalog_key(args,session['workspace']) if action in {'providers.models','providers.imageModels','providers.schema'} else None
            routing_before = None
            if action in {'routing.save', 'routing.use'}:
                # A global matrix may be active in another workspace, while a
                # local matrix can shadow it. Compare resolved inputs for each
                # loaded workspace, not just the settings editor's selection.
                routing_manager = SetupManager(self.service.data_dir, allow_missing_workspace=True)
                mounted = getattr(self.service.runtime, 'workers', {})
                routing_contexts = {row['id']: (row['workspace'], row.get('runtimeSessionId') or row.get('nativeIdentity') or row['id'], row['id'] in mounted)
                                    for row in self.service.state['sessions'] if not row.get('historyManaged')}
                routing_contexts[None] = (session['workspace'], None)
                routing_before = {identity: routing_manager.effective_routing(*context)
                                  for identity, context in routing_contexts.items()}
            result=await manager.perform(action,{**args,'workspace':session['workspace']})
            routing_sessions = None
            if routing_before is not None:
                routing_sessions = {identity for identity, before in routing_before.items()
                                    if before is None or before != routing_manager.effective_routing(*routing_contexts[identity])}
                result['configurationChanged'] = bool(routing_sessions)
                routing_sessions.discard(None)
            result['configurationTarget']=args.get('configurationTarget')
            if action=='providers.list':
                result['providersRequestedWorkspace']=args.get('workspace',session['workspace'])
                result['providersLocation']={'kind':'managed' if managed_draft else 'workspace'}
            if probe_key and probe_key!=manager.catalog_key(args,session['workspace']):return
            async with self.service.lock:
                setup=self.service.state.setdefault('setup',{})
                if not target and action in {'providers.models','providers.imageModels','providers.schema'} and (setup.get('providersWorkspace') not in {None,session['workspace']} or (setup.get('providersLocation',{}).get('kind')=='managed')!=managed_draft):return
                if command_id and action.startswith('providers.'):
                    key=action+':'+(args.get('module') if action in {'providers.credentials','providers.schema'} else args.get('id','') or '')
                    from .configuration_target import operation_key
                    if setup.get('operations',{}).get(operation_key(args,key),{}).get('commandId')!=command_id:return
                from .configuration_target import state_for
                setup=state_for(self.service,'setup',args)
                if not target and action=='providers.list' and (setup.get('providersWorkspace')!=result.get('providersWorkspace') or setup.get('providersLocation',{}).get('kind','workspace')!=result['providersLocation']['kind']):
                    setup.update(modelCatalogs={},providerCatalogs={},imageCatalogs={},metadata={},models=[],modelsProviderId=None)
                if result.get('messageTest'):
                    setup.setdefault('messageTests',{})[args['id']]=result['messageTest']
                setup.update(result)
                if result.get('providerMetadata'):
                    setup.setdefault('metadata',{})[result['providerMetadata']['module']]=result['providerMetadata']
                if 'imageModels' in result:
                    setup.setdefault('imageCatalogs',{})[result['imageModelsProviderId']]={'phase':'ready','models':result['imageModels'],'supported':result.get('imageModelsSupported',False),'metadata':result.get('providerMetadata')}
                if 'models' in result:
                    setup.setdefault('modelCatalogs',{})[result['modelsProviderId']]=result['models']
                    setup.setdefault('providerCatalogs',{})[result['modelsProviderId']]={'phase':'ready','models':result['models'],'supported':result.get('modelsSupported',True),'metadata':result.get('providerMetadata')}
                self.service._publish_progress(session_ids=set(), record_only=True, global_keys={'setup'})
            if action in {'providers.list','providers.save','providers.finishSetup','providers.remove','providers.move','providers.reorder','providers.loginStatus'}:
                async with self.service.lock:
                    from .configuration_target import state_for
                    state_for(self.service,'setup',args).update(providers=manager.provider_rows(session['workspace']),providersWorkspace=session['workspace'],providersLoadedAt=time.time(), providersLocation={'kind':'managed' if managed_draft else 'workspace'}, configurationTarget=args.get('configurationTarget'))
                    self.service._publish_progress(session_ids=set(), record_only=True, global_keys={'setup'})
                self.background(self.warm_providers(manager,session['workspace']))
            if action in {'providers.save','providers.finishSetup','providers.configureDecisions','providers.remove','providers.move','providers.reorder','routing.save','routing.use'} and result.get('configurationChanged',True):
                await self.invalidate_configuration(publish=False,warm=action not in {'providers.save','providers.finishSetup','providers.configureDecisions','providers.remove','providers.move','providers.reorder','routing.save','routing.use'},session_ids=set() if action=='providers.configureDecisions' else routing_sessions)
        elif action in {'bundle.preview', 'bundle.switch', 'bundle.fork'}:
            from .bundle_actions import perform
            await perform(self, action, args)
        elif action.startswith(('bundle.','bundles.')):
            from .bundles import BundleManager
            manager=BundleManager(self.service.data_dir)
            kwargs={}
            if action in {'bundle.export','bundle.save'}:
                session=self.session(args)
                if action=='bundle.save' and session['status'] in {'starting','working','ready','stopping'}:raise ValueError('Finish the current turn before changing its bundle')
                await self.ensure_runtime(session)
                effective=await self.service.runtime.control(session['id'],'configuration.inspect',{})
                resources=await self.service.runtime.control(session['id'],'configuration.exportResources',{})
                kwargs={'effective_config':effective.get('plan',{}),'root_bundle':session['bundle'],'resources':resources}
            workspace=self.configuration_session(args)['workspace']
            result=await manager.perform(action,{**args,'workspace':workspace},**kwargs)
            values={}
            if 'bundles' in result:values['bundles']=result['bundles']
            if 'registeredBundles' in result:values['registeredBundles']=result['registeredBundles']
            if 'discovery' in result:values['bundleDiscovery']=result['discovery']
            if values:
                async with self.service.lock:
                    self.service.state.update(values)
                    self.service._publish_progress(session_ids=set(), record_only=True, global_keys=set(values))
            if action in {'bundles.add','bundles.toggle','bundles.remove','bundles.move','bundles.reorder'}:await self.invalidate_configuration(publish=False)
            if result.get('content') is not None:await self.download(result.get('filename','custom-bundle.yaml'),result['content'],result.get('mimeType','text/yaml'))
            if action=='bundle.save' and result.get('saved'):
                saved=result['saved']
                async with self.service.lock:
                    self.service._session(session['id'])['bundle']=saved['name']
                    self.service._publish_changes(sessions={session['id']})
                await self.service.runtime.stop(session['id'], reason='configuration')
        elif action=='configuration.cancel':
            identity=self.session({'sessionId':args['id']})['id']
            async with self.service.lock:
                current = self.service._session(identity)
                pending = current.get('pendingConfiguration', {})
                if pending.get('phase') == 'applying':
                    raise ValueError('These changes are already applying.')
                if pending:
                    record = self.configuration_record(identity, pending.get('operationId'))
                    if record['state']['effect'] != 'none' and record['state']['phase'] != 'ready':
                        raise ValueError('The original application outcome is unresolved. Cancellation cannot prove it had no effect.')
                    if record['state']['phase'] != 'ready':
                        self.configuration_transition(current, record['id'], phase='cancelled', stage='cancelled')
                    self.settle_configuration_fence(current, record['id'])
                task=self.pending_config_tasks.get(identity)
                if task:task.cancel()
                self.queued_path(identity).unlink(missing_ok=True)
                current.pop('pendingConfiguration',None)
                self.service._publish_changes(sessions={identity})
            self.background(self.service.collaboration.drain(identity))
        elif action in {'configuration.inspect','configuration.apply','configuration.patchPlacement.bind'}:
            session=self.session({'sessionId':args['id']})
            applying = action != 'configuration.inspect'
            if action == 'configuration.patchPlacement.bind' and configuration_operation is None:
                raise ValueError('Placement binding requires its retained scoped operation.')
            if action == 'configuration.apply' and configuration_operation is None:
                from .runtime_controls import validate_plan
                validate_plan(args['config'])
                async with self.service.lock:
                    current = self.service._session(session['id'])
                    idle = self.configuration_idle(current)
                    if not idle and not args.get('whenIdle'):
                        raise ValueError('Finish active work before changing this conversation configuration')
                    record = self.create_configuration_operation(current, args['config'], command_id, authority, source)
                    configuration_operation = record['id']
                    if idle:
                        current['configurationBusy'] = True
                        current['configurationOperationFence'] = configuration_operation
                        self.configuration_transition(current, configuration_operation, phase='applying', stage='preparing')
                    self.service._publish_changes(sessions={session['id']})
                if not idle:
                    self.schedule_configuration(session['id'])
                    return
            forwarded={'sessionId':args['id'],'operation':action,'args':{'config':args['config']} if 'config' in args else {}}
            try:
                if applying:
                    record = self.configuration_record(session['id'], configuration_operation)
                    self.check_configuration_operation(self.service._session(session['id']), record)
                    if args['config'] != record['request']['config']:
                        raise ValueError('The requested plan is not the original operation payload.')
                await self.perform('runtime.control',forwarded,configuration_operation=configuration_operation)
            except Exception as exc:
                if applying:
                    message = ('Patch placement was not confirmed. Inspect the retained operation; no automatic retry.'
                        if action == 'configuration.patchPlacement.bind' else str(exc)[:1000])
                    async with self.service.lock:
                        current = self.service._session(session['id'])
                        record = self.configuration_error(current, configuration_operation, message)
                        self.settle_configuration_fence(current, configuration_operation)
                        self.service._publish_changes(sessions={session['id']})
                        if not current['configurationBusy']:
                            self.background(self.service.collaboration.drain(session['id']))
                    if action == 'configuration.patchPlacement.bind':
                        raise ValueError(message) from None
                raise
            except asyncio.CancelledError:
                if applying:
                    async with self.service.lock:
                        current = self.service._session(session['id'])
                        before = self.configuration_record(current['id'], configuration_operation)
                        record = self.configuration_transition(current, configuration_operation,
                            stage='interrupted', error='Configuration application was interrupted; no automatic retry.',
                            **({'phase': 'error'} if before['state']['effect'] == 'none' else {}))
                        self.settle_configuration_fence(current, configuration_operation)
                        self.service._publish_changes(sessions={session['id']})
                raise
            if applying:
                async with self.service.lock:
                    current = self.service._session(session['id'])
                    self.configuration_transition(current, configuration_operation, phase='ready', stage='readback',
                        detail='Changes are applied to the loaded session.', appliedAt=time.time())
                    self.queued_path(session['id']).unlink(missing_ok=True)
                    self.settle_configuration_fence(current, configuration_operation)
                    self.service._publish_changes(sessions={session['id']})
                self.background(self.service.collaboration.drain(session['id']))
        elif action=='runtime.control':
            placement = args['operation'] == 'configuration.patchPlacement.bind'
            if args['operation'].startswith('configuration.patchPlacement.') and configuration_operation is None:
                raise ValueError('Use the scoped patch placement actions, not raw runtime control.')
            if args['operation'].startswith('bundle.'):
                raise ValueError('Use the bundle actions to preview, switch, or fork a root bundle.')
            if args['operation'] == 'configuration.apply' and configuration_operation is None:
                # The raw public route has the same persistence and no-replay
                # boundary; only host-internal forwarding carries an operation.
                return await self.perform('configuration.apply',
                    {'id': args['sessionId'], 'config': args.get('args', {}).get('config')},
                    command_id=command_id, authority=authority, source=source)
            session=self.session(args)
            recovery_request = args['operation'] == 'context.recovery.request'
            recovery_binding = (source or {}).get('recovery') if recovery_request else None
            if recovery_request:
                if not isinstance(command_id, str) or not command_id:
                    raise ValueError('Explicit recovery requires a stable host command identity')
                if not isinstance(recovery_binding, dict):
                    raise ValueError('Explicit recovery requires its original host admission binding')
                if set(args.get('args', {})) - {'targetBytes'}:
                    raise ValueError('Recovery arguments cannot supply host identity or authority')
            def operation_guard():
                check_current()
                if recovery_request and recovery_binding != self.recovery_admission_binding(
                        self.service._session(session['id'])):
                    raise ValueError('The recovery target was stopped, moved or reconfigured after admission. No delayed recovery was armed.')
                if configuration_operation is not None:
                    self.check_configuration_operation(self.service._session(session['id']),
                        self.configuration_record(session['id'], configuration_operation))
            operation_guard()
            if args['operation']=='configuration.catalog':
                from .conversation_models import request_browse
                await request_browse(self,session,refresh=args.get('args',{}).get('refresh',False))
                return
            if args['operation']=='provider.queueSelection':
                from .conversation_models import select
                await select(self,session,args.get('args',{}))
                return
            if await self.recover_provider_catalog(session, args['operation'], args.get('args', {})):
                return
            mutating=placement or recovery_request or args['operation'] in {'configuration.apply','configuration.toggle','context.clear','provider.select','provider.reset','native.compact'}
            recovery_contract_change = placement or args['operation'] in {
                'configuration.apply', 'configuration.toggle', 'context.clear',
                'provider.select', 'provider.reset', 'native.compact',
                'mode.set', 'mode.clear', 'budget.set', 'budget.clear'}
            if mutating:
                async with self.service.lock:
                    current=self.service._session(session['id'])
                    operation_guard()
                    owns_fence = (args['operation'] in {'configuration.apply', 'configuration.patchPlacement.bind'} and configuration_operation is not None
                        and current.get('pendingConfiguration', {}).get('operationId') == configuration_operation
                        and current.get('configurationBusy')
                        and current.get('configurationOperationFence') == configuration_operation
                        and self.configuration_record(current['id'], configuration_operation)['state']['stage'] == 'preparing')
                    if not self.configuration_idle({**current, **({'configurationBusy': False} if owns_fence else {})}):
                        raise ValueError('Finish active work before changing this conversation configuration')
                    current['configurationBusy']=True
                    self.service._publish_changes(sessions={session['id']})
            try:
                if args['operation'] == 'provider.select':
                    from .new_chat import selection
                    requested = dict(args.get('args', {}))
                    legacy = requested.pop('provider', None)
                    if legacy and not requested.get('instance'):
                        requested['instance'] = legacy
                    selected = selection(requested)
                    if not selected:
                        raise ValueError('Choose a provider and model')
                    await self.ensure_runtime(session, selection_override=selected)
                else:
                    await self.ensure_runtime(session,
                        **({'restart_guard': operation_guard} if configuration_operation is not None or recovery_request else {}),
                        **({'preserve_configuration': True} if placement else {}))
                operation_guard()
                if args['operation']=='configuration.providerModels':
                    provider_id=args.get('args',{}).get('instance') or args.get('args',{}).get('provider')
                    mounted=await self.service.runtime.control(session['id'],'configuration.providers',{})
                    row=next((row for row in mounted.get('providers',[]) if row['id']==provider_id),None)
                    if row is None:raise ValueError('Provider instance is not mounted')
                    result=await self.runtime_models(session['id'],row,mounted.get('catalogRevision'),refresh=args.get('args',{}).get('refresh',False))
                    current=await self.service.runtime.control(session['id'],'configuration.providers',{})
                    if current.get('catalogRevision')!=mounted.get('catalogRevision'):return
                    current_row=next((item for item in current.get('providers',[]) if item['id']==provider_id),None)
                    if current_row is None or self.runtime_catalog_key(session['id'],current_row,current.get('catalogRevision'))!=self.runtime_catalog_key(session['id'],row,mounted.get('catalogRevision')):return
                else:
                    check_current()
                    native_arguments = args.get('args', {})
                    if recovery_request:
                        native_arguments = {**native_arguments, '_recoveryCommand': {
                            'id': command_id,
                            'interruptionRevision': recovery_binding['interruptionRevision']}}
                    if configuration_operation is not None:
                        async with self.service.lock:
                            current = self.service._session(session['id'])
                            record = self.configuration_record(current['id'], configuration_operation)
                            self.check_configuration_operation(current, record)
                            # Persist uncertainty BEFORE dispatch. No exception
                            # string (including a native busy error) proves that
                            # a remote settings write did not occur.
                            from .configuration_recovery import prepare_attempt
                            attempt = prepare_attempt(self, current, configuration_operation)
                            native_arguments = {**native_arguments, '_configurationAttempt': attempt}
                            self.service._publish_changes(sessions={current['id']})
                    result=await self.service.runtime.control(session['id'],args['operation'],native_arguments,
                        **({'guard': operation_guard} if configuration_operation is not None or recovery_request else {}))
                    if configuration_operation is not None and result.get('requiresRestart'):
                        from .configuration_recovery import evidence
                        native = evidence(attempt, home=self.service.data_dir)
                        if not native or native['effect'] != 'saved_not_mounted' or not native.get('terminal'):
                            raise ValueError('The bound native configuration save receipt is unconfirmed.')
                        async with self.service.lock:
                            self.configuration_transition(self.service._session(session['id']), configuration_operation,
                                stage='saved', effect='saved_not_mounted')
                            self.service._publish_changes(sessions={session['id']})
                    elif configuration_operation is not None:
                        raise ValueError('The native configuration save was not confirmed. Its outcome remains unknown.')
                if result.get('requiresRestart'):
                    check_current()
                    if configuration_operation is not None:
                        self.check_configuration_operation(self.service._session(session['id']),
                            self.configuration_record(session['id'], configuration_operation))
                    interruption_revision = session.get('interruptionRevision', 0)
                    await self.service.runtime.stop(session['id'], reason='configuration')
                    check_current()
                    if self.service._session(session['id']).get('interruptionRevision', 0) != interruption_revision:
                        raise ValueError('The settings were saved, but the conversation was stopped before restart. It was not restarted.')
                    if configuration_operation is not None:
                        operation_guard()
                    await self.service.runtime.start(session,self.service.on_runtime_event,
                        **({'guard': operation_guard} if configuration_operation is not None else {}),
                        **({'preserve_configuration': True} if placement else {}))
                    if placement:
                        from .patch_placement import observe, verify_readback
                        inspected = await observe(self.service, session, args['args']['config']['moduleId'])
                        verify_readback(self, session, args['args']['config'], inspected)
                    else:
                        inspected=await self.service.runtime.control(session['id'],'configuration.inspect',{})
                    if not placement and configuration_operation is not None and not isinstance(inspected.get('plan'), dict):
                        raise ValueError('Saved configuration has no confirmed loaded-plan readback.')
                    if not placement and configuration_operation is not None:
                        # The worker acknowledgement is evidence to check, not
                        # authority to replace the immutable requested plan.
                        expected = self.configuration_record(session['id'], configuration_operation)['request']['config']
                        if 'configuration' in result and (
                                not isinstance(result['configuration'], dict)
                                or not self.configuration_readback_matches(expected, result['configuration'])):
                            raise ValueError('Saved configuration acknowledgement does not match the original request. Its operation remains unresolved.')
                        if not self.configuration_readback_matches(expected, inspected['plan']):
                            raise ValueError('Saved configuration does not match loaded-plan readback. Its operation remains unresolved.')
                    if not placement:
                        result['configuration']=inspected
                    if configuration_operation is not None:
                        async with self.service.lock:
                            operation_guard()
                            self.configuration_transition(self.service._session(session['id']), configuration_operation,
                                stage='readback', effect='mounted')
                            self.service._publish_changes(sessions={session['id']})
                refreshed={}
                if result.get('requiresRestart') and not placement:refreshed['configuration.providers']=await self.service.runtime.control(session['id'],'configuration.providers',{})
                refresh={'native.compact':'native.status','mode.set':'mode.list','mode.clear':'mode.list','goals.set':'goals.get','goals.clear':'goals.get','budget.set':'budget.get','provider.select':'configuration.providers','provider.reset':'configuration.providers','configuration.toggle':'configuration.inspect'}.get(args['operation'])
                if refresh:refreshed[refresh]=await self.service.runtime.control(session['id'],refresh,{})
                async with self.service.lock:
                    self.service.state.setdefault('runtimeControl',{}).setdefault(session['id'],{}).update(refreshed)
                    self.service.state.setdefault('runtimeControl',{}).setdefault(session['id'],{})[args['operation']]=result
                    if args['operation'] in {'provider.select', 'provider.reset'}:
                        self.service._session(session['id'])['selection'] = copy.deepcopy(result['selection'])
                    if args['operation'] in {'configuration.inspect','configuration.apply','configuration.toggle'}:
                        configuration=result.get('configuration',result)
                        self.service._session(session['id'])['configuration']=configuration
                    if args['operation']=='configuration.providerModels':
                        identity=args.get('args',{}).get('instance') or args.get('args',{}).get('provider')
                        self.service.state['runtimeControl'][session['id']].setdefault('modelCatalogs',{})[identity]={'phase':'ready','models':result.get('models',[]),'supported':result.get('supported',True)}
                    self.service._publish_progress(session_ids={session['id']}, record_only=True)
                provider_info=result if args['operation']=='configuration.providers' else refreshed.get('configuration.providers')
                if provider_info is not None:self.background(self.warm_runtime_models(session['id'],provider_info.get('providers',[]),provider_info.get('catalogRevision')))
            finally:
                if mutating or recovery_contract_change:
                    async with self.service.lock:
                        current = self.service._session(session['id'])
                        if recovery_contract_change:
                            # Every attempt invalidates a recovery queued during
                            # it, including lost mutation/readback acknowledgements.
                            current['recoveryControlRevision'] = current.get('recoveryControlRevision', 0) + 1
                        pending = current.get('pendingConfiguration', {})
                        # The outer operation releases only after its result is
                        # committed. Preparation refusals are known no-effect;
                        # dispatched/partially saved failures stay fenced.
                        if mutating and configuration_operation is None:
                            current['configurationBusy'] = False
                        self.service._publish_progress(session_ids={session['id']}, record_only=True)
        elif action=='notifications.get':
            await self.publish(notificationSettings=self.notifications.public())
        elif action=='notifications.save':
            await self.publish(notificationSettings=self.notifications.save(args['patch']))
        elif action in {'permissions.get', 'permissions.save', 'permissions.reset'} and args.get('scope') == 'session':
            from .chat_permissions import target, snapshot, change, PENDING_DETAIL
            if action == 'permissions.get':
                session = target(self, args)
                result = snapshot(self, session)
            else:
                marked = False
                try:
                    async with self.service.lock:
                        session = target(self, args)
                        def before_write():
                            nonlocal marked
                            # Persist the admission fence BEFORE Foundation's
                            # atomic settings write. A lost write/ack stays
                            # fenced until retirement proves the next mount
                            # reads canonical settings. No-op calls never enter.
                            session.update(fileAccessPending=True, configurationPending=True)
                            if not session.get('configurationBusy'):
                                session['configurationRefresh'] = {'phase':'pending', 'detail':PENDING_DETAIL}
                            marked = True
                            self.service._publish_changes(sessions={session['id']})
                        result = change(self, args, reset=action == 'permissions.reset', before_write=before_write)
                finally:
                    if marked:
                        await self.invalidate_configuration(warm=False, session_ids={session['id']})
            await self.publish(permissions={**result, 'commandId': command_id})
        elif action=='permissions.get':
            session=self.configuration_session(args);scope=args.get('scope','global')
            config=self.settings.read(session['workspace'],scope)
            values=config.get('overrides',{}).get('tool-filesystem',{}).get('config',{})
            await self.publish(permissions={'scope':scope,'workspace':session['workspace'],'allowed':values.get('allowed_write_paths',[]),'denied':values.get('denied_write_paths',[])})
        elif action=='permissions.save':
            session=self.configuration_session(args);scope=args.get('scope','global')
            allowed=[str(Path(p).expanduser().resolve()) for p in args.get('allowed',[])]
            denied=[str(Path(p).expanduser().resolve()) for p in args.get('denied',[])]
            def edit(config):
                values=config.setdefault('overrides',{}).setdefault('tool-filesystem',{}).setdefault('config',{})
                values.update(allowed_write_paths=allowed,denied_write_paths=denied)
            self.settings.update(session['workspace'],scope,edit)
            await self.publish(permissions={'scope':scope,'workspace':session['workspace'],'allowed':allowed,'denied':denied,'detail':'Saved. Idle conversations use these settings on their next message.'})
            await self.invalidate_configuration()
        elif action=='history.list':
            await self.publish(history=await asyncio.to_thread(self.history,args.get('legacy',False)))
        elif action == 'history.shared.open':
            if not self.service.runtime:
                raise ValueError('The isolated runtime is unavailable.')
            result = await self.service.runtime.shared_state_probe({
                'version': 1, 'op': 'open', 'sessionId': args['id'],
                'workspace': args['workspace'], 'limit': 100})
            async with self.service.lock:
                session = next((s for s in self.service.state['sessions']
                                if s['id'] == result['id']), None)
                if session and Path(session['workspace']).resolve() != Path(result['workspace']):
                    raise ValueError('This session ID is already open for a different workspace.')
                if session is None:
                    session = self.service._new_session({
                        'title': result.get('name') or 'Shared conversation ' + result['id'][:8],
                        'workspace': result['workspace'], 'bundle': result['bundle']})
                    session['id'] = result['id']
                    self.service.state['sessions'].insert(0, session)
                if session['status'] not in {'starting', 'working', 'stopping'}:
                    session.update(status='idle', bundle=result['bundle'], messages=[],
                                   shared=True, sharedHistoryOffset=result['offset'],
                                   sharedHistoryTotal=result['totalMessages'])
                    for index, row in enumerate(result['messages'], result['offset']):
                        session['messages'].append({'id': uuid.uuid5(uuid.NAMESPACE_URL, f"shared:{result['workspace']}:{result['id']}:{index}").hex,
                            'role': row['role'], 'text': row['text'], 'via': 'chat', 'source': 'shared', 'createdAt': session['createdAt']})
                self.service.state['selectedSessionId'] = session['id']
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.service.state, session)
                # Only UI presentation is saved here. The next worker admission
                # locks and reads the complete common checkpoint, never bubbles.
                self.service._publish_full(reason='History import or cleanup changes catalog membership')
        elif action in {'history.shared.list','history.shared.view'}:
            if not self.service.runtime:
                raise ValueError('The isolated runtime is unavailable.')
            request={'version':1,'op':'list' if action.endswith('.list') else 'view',
                     'workspace':args.get('workspace') or self.service.default_workspace}
            if action.endswith('.view'):
                request.update(sessionId=args['id'],offset=args.get('offset',0),limit=args.get('limit',50))
            result=await self.service.runtime.shared_state_probe(request)
            await self.publish(sharedHistory=result)
        elif action=='history.importFile':
            from .session_store import complete_tool_exchanges,text_content
            if args['format']=='jsonl':
                rows=[json.loads(line) for line in args['content'].splitlines() if line.strip()];metadata={}
            else:
                payload=json.loads(args['content'])
                if isinstance(payload,list):rows=payload;metadata={}
                elif isinstance(payload,dict):rows=payload.get('messages');metadata=payload.get('metadata',{})
                else:raise ValueError('Use a JSON message array or a session export')
            if not isinstance(rows,list) or not isinstance(metadata,dict) or not rows:raise ValueError('The transcript needs messages and valid metadata')
            if any(not isinstance(r,dict) or r.get('role') not in {'user','assistant','system','developer','tool'} for r in rows):raise ValueError('Unsupported transcript message role')
            rows=complete_tool_exchanges(rows)
            async with self.service.lock:
                session=self.service._new_session({'title':args.get('title') or 'Imported transcript','workspace':self.service.default_workspace,'bundle':args.get('bundle') or metadata.get('bundle_name') or 'anchors'})
                session['status']='stopped'
                store=SessionStore.for_app(self.service.data_dir,session['workspace'])
                metadata={**metadata,'parent_id':None,'working_dir':session['workspace'],'bundle_name':session['bundle'],'imported_file':True,'jobs_replayed':False,'preserve_system':True}
                store.save(session['id'],rows,metadata,preserve_system=True)
                from .automatic_history import display_message
                for index,row in enumerate(rows):
                    visible=display_message(row,index,session)
                    if visible:self.service._message(session,visible['role'],visible['text'],source='import',nativeIndex=index)
                self.service.state['sessions'].insert(0,session);self.service.state['selectedSessionId']=session['id']
                self.service._publish_full(reason='History import or cleanup changes catalog membership')
        elif action=='history.import':
            identity=args['id']
            saved=SessionStore.find(self.service.data_dir,identity,self.service.default_workspace)
            if not saved:raise ValueError('The requested session was not found')
            rows,meta=saved
            async with self.service.lock:
                metadata=self.service.directory.resolve(identity)
                existing=self.service._session(metadata['id']) if metadata is not None else None
                if existing:self.service.state['selectedSessionId']=existing['id']
                else:
                    workspace=meta.get('working_dir') or meta.get('workspace') or self.service.default_workspace
                    if not Path(workspace).is_dir():raise ValueError('Restore this session’s original workspace before opening it.')
                    session=self.service._new_session({'title':meta.get('name') or meta.get('title') or 'Imported conversation','workspace':workspace,'bundle':(meta.get('bundle_name') or meta.get('bundle') or 'anchors').removeprefix('bundle:')})
                    session['id']=identity;session['status']='stopped'
                    from .automatic_history import display_message
                    for index,row in enumerate(rows):
                        visible=display_message(row,index,session)
                        if visible:self.service._message(session,visible['role'],visible['text'],source='import',nativeIndex=index)
                    self.service.state['sessions'].insert(0,session);self.service.state['selectedSessionId']=identity
                    # Explicit re-import may restore a previously cleaned owner;
                    # do not let its old navigation suppression hide it again.
                    from .automatic_history import identity as native_identity
                    from .session_files import project_slug
                    hidden=native_identity(project_slug(workspace),identity)
                    self.service.state['hiddenNativeSessions']=[key for key in self.service.state.get('hiddenNativeSessions',[]) if key!=hidden]
                self.service._publish_full(reason='History import or cleanup changes catalog membership')
        elif action=='history.export':
            session=self.session(args)
            if session.get('nativeProject'):
                from .automatic_history import directory
                path=directory(session)
                from amplifier_foundation.session.history import SessionHistoryStore
                history=SessionHistoryStore(path).load(include_events=False)
                saved=(history.messages,history.metadata)
                await self.publish(historyDiagnostics=[{'code':d.code,'source':d.source,'line':d.line,'severity':d.severity} for d in history.diagnostics])
            else:
                store=SessionStore.for_app(self.service.data_dir,session['workspace'])
                saved=store.load(session.get('runtimeSessionId') or session['id'])
            if not saved:raise ValueError('This conversation has no runtime transcript yet')
            rows,metadata=saved
            if args.get('format')=='jsonl':
                await self.download('amplifier-transcript.jsonl',''.join(json.dumps(m)+'\n' for m in rows),'application/x-ndjson')
            else:await self.download('amplifier-session.json',json.dumps({'messages':rows,'metadata':metadata},indent=2))
        elif action=='history.cleanup':
            cutoff=time.time()-args.get('days',30)*86400
            async with self.service.publishing.lock, self.service.lock:
                from .recovery import bulk_sessions
                eligible=[s for s in bulk_sessions(self.service, include_native=True) if s['id']!=self.service.state['selectedSessionId'] and not self.service.publishing.owns_records(s['id']) and s['status'] in {'idle','stopped','interrupted','error'} and max([s.get('createdAt',0),s.get('updatedAt',0)]+[m.get('createdAt',0) for m in s.get('messages',[])])<cutoff]
                if args.get('apply'):
                    identities={s['id'] for s in eligible}
                    for session in eligible:
                        self.service.history.hide_session(session)
                    self.service.forget_session_records(identities)
                    self.service.state['sessions']=[s for s in self.service.state['sessions'] if s['id'] not in identities]
                    for identity in identities:
                        self.service.state.get('runtimeControl',{}).pop(identity,None)
                    if args.get('purge'):
                        import shutil
                        store=SessionStore(self.service.data_dir/'sessions')
                        for identity in identities:
                            path=store.directory(identity)
                            if path.exists():shutil.rmtree(path)
                self.service.state['cleanupPreview']={'sessions':[{'id':s['id'],'title':s['title']} for s in eligible],'applied':bool(args.get('apply')),'detail':'Conversation list cleaned. Publishing owners, shared CLI transcripts and event files are retained.'}
                self.service._publish_full(reason='History import or cleanup changes catalog membership')
        elif action=='maintenance.restoreResource':
            from .resource_files import restore
            async with self.service.lock:
                roots=[self.service._state,*self.service.clients.records.values()]
                result=restore(self.service.db,roots,args['id'],args['value'])
                self.service.state.setdefault('maintenance',{})['resourceRecovery']=result
                self.service._publish_full(reason='Explicit resource restoration may replace references in multiple owners')
        elif action=='maintenance.canonicalizeBundleReferences':
            from .bundle_reference_repair import canonicalize
            await canonicalize(self.service, apply=args.get('apply', False))
        elif action=='maintenance.backup':
            from .recovery import backup
            result=await backup(self.service)
            await self.publish(maintenance=result)
        elif action=='maintenance.reset':
            from .recovery import reset
            await reset(self,args)
        elif action=='maintenance.repair':
            updates = self.service.update_manager
            async with self.service.runtime_lifecycle():
                candidate = previous_phase = None
                try:
                    async with self.service.lock:
                        if self.service.closed:
                            raise RuntimeError('The runtime host is closing.')
                        if updates and updates.lock.locked():
                            raise ValueError('Finish active updates before repairing the runtime')
                        if updates and updates.busy():
                            raise ValueError('Finish active work before repairing the runtime')
                        candidate = self.service.runtime_candidate()
                        previous_phase = self.service.state.setdefault('updates', {}).get('phase', 'idle')
                        self.service.state['updates'].update(phase='activating',
                            detail='Repairing runtime dependencies…')
                        self.service._publish_changes(globals={'updates'})
                    from .updates import process
                    from .runtime import RuntimeManager
                    command=RuntimeManager()._command()
                    project=RuntimeManager.project_path(command)
                    await process(command[0],'sync',*(('--locked',) if '--locked' in command else ()),
                                  '--project',project,'--python','3.13','--reinstall',timeout=900)
                    await self.service.replace_runtime(candidate)
                except BaseException:
                    await self.service.discard_runtime(candidate)
                    if previous_phase is not None:
                        async with self.service.lock:
                            self.service.state['updates']['phase'] = previous_phase
                            self.service._publish_changes(globals={'updates'})
                    raise
                async with self.service.lock:
                    self.service.state['updates']['phase'] = previous_phase
                    self.service._publish_changes(globals={'updates'})
            await self.publish(maintenance={'detail':'Runtime dependencies repaired. Conversations will resume when you next send a message.'})
        else:
            raise ValueError('Management action is not implemented: '+action)

    def history(self,legacy=False):
        from .session_files import amplifier_home
        rows=[]
        from amplifier_foundation.session.history import SessionHistoryStore
        paths=sorted({path.parent/'metadata.json' for name in ('metadata.json','metadata.json.backup')
                      for path in (amplifier_home()/'projects').glob('*/sessions/*/'+name)})
        seen={path.parent.name for path in paths}
        paths.extend(path for path in (self.service.data_dir/'sessions').glob('*/metadata.json') if path.parent.name not in seen)
        for path in paths:
            try:
                meta=SessionHistoryStore(path.parent).load_metadata()
                rows.append({'id':path.parent.name,'title':meta.get('name') or meta.get('title') or meta.get('bundle_name') or path.parent.name,'workspace':meta.get('working_dir') or meta.get('workspace'),'updatedAt':meta.get('updated_at'),'legacy':False,'parentId':meta.get('parent_id')})
            except (OSError,ValueError):continue
        return rows
