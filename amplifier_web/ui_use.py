"""Client-neutral UI target host. Controllers and executors live in shared bundles."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import importlib.util
import os
import json
import time
import uuid
from pathlib import Path
from aiohttp import web
from .deployment import write_private
from .device_connections import owner, csrf, body


class UIUse:
    def __init__(self, service, connections):
        self.service, self.connections = service, connections
        self.path = service.data_dir / 'ui-use-preferences.json'
        self.preferences = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.browsers, self.active, self.stopped, self.progress, self.locks, self.pending = {}, {}, set(), {}, {}, {}
        self.remote_observations = {}
        self._managed_executable, self._managed_checked, self._managed_check = None, 0, None
        self.stop_path = service.data_dir / 'ui-use-stopped.json'
        self.stopped = set(json.loads(self.stop_path.read_text())) if self.stop_path.exists() else set()

    def stop(self, sid, stopped):
        values = self.stopped | {sid} if stopped else self.stopped - {sid}
        errors = []
        if stopped:
            # A storage refusal must not leave this process accepting more input.
            self.stopped = values
            for operation_id in tuple(self.pending.get(sid,())):
                try:
                    self.connections.cancel(operation_id)
                except (OSError, ValueError, RuntimeError) as exc:
                    errors.append(exc)
        try:
            write_private(self.stop_path, json.dumps(sorted(values)), owned_root=self.service.data_dir)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(exc)
        if errors:
            raise errors[0]
        self.stopped = values

    def workspace(self, sid):
        return self.service._session(sid).get('workspace') if sid else None

    def effective(self, sid):
        workspace = self.workspace(sid)
        result = {'target':'managed', 'selection':'configured'}
        for key in ['global', 'workspace:'+str(workspace), 'chat:'+str(sid)]:
            result.update(self.preferences.get(key, {}))
        return result

    def managed_available(self):
        path = self._managed_executable
        return bool(path and Path(path).is_file() and os.access(path, os.X_OK))

    async def refresh_managed(self):
        """Coalesce readiness checks without blocking delivery or Stop requests."""
        from .app_features import managed_browser_executable
        if self._managed_checked and time.monotonic() - self._managed_checked < 30:
            return
        async def probe():
            self._managed_executable = await asyncio.to_thread(managed_browser_executable, self.service.data_dir) if importlib.util.find_spec('amplifier_ui_use') else None
            self._managed_checked = time.monotonic()
        if self._managed_check is None:
            self._managed_check = asyncio.create_task(probe())
        task = self._managed_check
        try:
            await asyncio.shield(task)
        finally:
            if task.done() and self._managed_check is task:
                self._managed_check = None

    def targets(self):
        available = self.managed_available()
        result = [{'id':'managed','label':'Amplifier browser','kind':'browser','location':'server','ready':available,
                   'detail':'Private browser for this chat. Sign in using its live view.' if available else 'Amplifier browser needs setup on this service. Add browser support below, or connect your own browser.'}]
        for device in self.connections.listing():
            for target in device['targets']:
                if 'browser.observe' in target['capabilities']:
                    result.append({'id':device['id']+':'+target['id'], 'label':device['name']+' · '+target['label'],
                                   'kind':'browser','location':'connected-device','ready':target['ready'],'fullTab':'browser.task' in target['capabilities'],
                                   'detail':'One selected tab across pages until Stop.' if 'browser.task' in target['capabilities'] else 'Selected document. Navigation requires choosing the new document in the extension.'})
        return result

    def status(self, sid):
        from .app_features import status as feature_status
        value = self.effective(sid)
        current = self.active.get(sid) or value['target']
        browser = self.browsers.get(sid)
        targets = self.targets()
        return {'sessionId':sid,'preferences':value,'target':current,'targets':targets,
                'hostInstanceId':getattr(self.service,'instance_id',None),
                'featureSetup':feature_status(getattr(self.service,'update_manager',None)),
                'stopped':sid in self.stopped,'opened':bool(browser and browser.page) or any(t['id']==current and t.get('fullTab') for t in targets),
                'url':browser.page.url if browser and browser.page else None,
                'progress':self.progress.get(sid), 'connectUrl':'/connect',
                'computer':{'owner':'amplifier-bundle-computer-use','detail':'Computer tasks use the desktop configured for the chat runtime, which may be on the server. The extension controls web pages only.'}}

    def configure(self, sid, args):
        if set(args)-{'scope','target','selection','reset'}: raise web.HTTPBadRequest(text='Unsupported UI preference.')
        scope = args.get('scope','chat')
        if scope not in {'global','workspace','chat'} or scope!='global' and not sid:
            raise web.HTTPBadRequest(text='Select a chat for this preference.')
        if 'reset' in args and (args['reset'] is not True or set(args)-{'scope','reset'}):
            raise web.HTTPBadRequest(text='Reset only the selected preference scope.')
        patch = {k:args[k] for k in ('target','selection') if k in args}
        if patch.get('selection','configured') not in {'configured','standard'}:
            raise web.HTTPBadRequest(text='Choose configured decision selection or the ordinary model.')
        target = patch.get('target')
        if target and target not in {r['id'] for r in self.targets()}:
            raise web.HTTPConflict(text='That target is unavailable. Refresh or reconnect it.')
        key = scope if scope=='global' else scope+':'+str(self.workspace(sid) if scope=='workspace' else sid)
        updated = dict(self.preferences)
        if args.get('reset'):updated.pop(key,None)
        else:updated[key]={**self.preferences.get(key,{}),**patch}
        write_private(self.path,json.dumps(updated),owned_root=self.service.data_dir); self.preferences=updated
        if target or args.get('reset'): self.active.pop(sid,None)
        return self.status(sid)

    async def managed(self, sid):
        await self.refresh_managed()
        if sid not in self.browsers:
            from amplifier_ui_use.browser import Browser
            from .app_features import managed_browser_launch_args
            async def authorize(action): return sid not in self.stopped
            # Binary and installation belong to the host, never inferred by a model.
            import os
            self.browsers[sid] = Browser(allowed_origins=[],authorize=authorize,
                executable_path=self._managed_executable,
                launch_args=managed_browser_launch_args(),
                allow_requested_origins=True,allow_web_navigation=True)
        return self.browsers[sid]

    async def remote(self, sid, target_id, action, args):
        device_id, selected = target_id.split(':',1)
        device = next((d for d in self.connections.listing() if d['id']==device_id), None)
        target = next((t for t in (device or {}).get('targets',[]) if t['id']==selected and t['ready']),None)
        if not target:
            return {'status':'blocked','reason':'selected_device_unavailable','recovery':'Reconnect and choose the intended tab, or explicitly choose Amplifier browser. Your task has not moved to another account.'}
        op = self.connections.submit({'id':'ui-'+uuid.uuid4().hex,'deviceId':device_id,'targetId':selected,
            'revision':target['revision'],'action':action,'args':args})
        self.pending.setdefault(sid,set()).add(op['id'])
        try:
            while op['status'] in {'queued','delivered'}:
                await asyncio.sleep(.1)
                self.connections.refresh()
                row = self.connections.lookup(op['id'])
                if row is None:
                    return {'status':'blocked','reason':'observation_expired','recovery':'Request a fresh view of the selected target.'}
                op=self.connections.receipt(row)
            if op['status']=='completed': return op['result']
            return {'status':'unknown' if op['status'] in {'unknown','failed'} and action!='browser.observe' else 'blocked','reason':op.get('detail',op['status']), 'operationId':op['id']}
        except asyncio.CancelledError:
            self.connections.cancel(op['id']); raise
        finally:
            self.pending.get(sid,set()).discard(op['id'])
            if not self.pending.get(sid):self.pending.pop(sid,None)

    async def connected(self, sid, target, args):
        action=args['action']
        device_id, target_id=target.split(':',1)
        device=next((d for d in self.connections.listing() if d['id']==device_id),{})
        full=next((t for t in device.get('targets',[]) if t['id']==target_id and 'browser.task' in t['capabilities']),None)
        if full:
            if action=='open':
                result=await self.remote(sid,target,'browser.task.open',{'url':args['url']})
                if result.get('status')!='completed':return result
                action='observe'
            if action=='observe':
                result=await self.remote(sid,target,'browser.task.observe',{})
                if 'state' not in result:return result
                from amplifier_ui_use.browser import observation_for
                observed=observation_for(result['state'],result['revision'])
                self.remote_observations[(sid,target)]=observed
                return observed
            saved=self.remote_observations.get((sid,target))
            if not saved or saved['revision']!=args.get('revision') or not any(c['id']==args.get('candidate') for c in saved['candidates']):
                return {'status':'stale','reason':'unobserved_candidate'}
            return await self.remote(sid,target,'browser.task.act',{'revision':args['revision'],'candidate':args['candidate'],'text':args.get('text','')})
        if action=='open': return {'status':'blocked','reason':'document_only_connection','recovery':'Open the desired website on the connected device and choose Use this tab. Then ask me to continue.'}
        if action=='observe':
            state = await self.remote(sid,target,'browser.observe',{})
            if state.get('status') in {'blocked','unknown'}: return state
            elements=[]; candidates=[]
            for index,control in enumerate(state.get('controls',[])):
                if not control.get('selector'): continue
                ident='control-'+str(index); elements.append({**control,'id':ident,'name':control.get('label','')})
                operation='TYPE_TEXT' if control['tag'] in {'input','textarea'} and control.get('type') not in {'radio','checkbox','submit','button','password','file','hidden'} else 'CLICK'
                candidates.append({'id':ident,'target':ident,'operation':operation,'name':control.get('label','')})
            candidates += [{'id':op.lower(),'operation':op} for op in ('WAIT','DONE','BLOCKED')]
            value={'state':{**state,'elements':elements},'candidates':candidates,'created_at':time.monotonic(),'truncated':False}
            value['revision']=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
            self.remote_observations[(sid,target)] = value
            return copy.deepcopy(value)
        old=self.remote_observations.get((sid,target))
        if not old or old['revision']!=args.get('revision') or time.monotonic()-old['created_at']>15:
            return {'status':'stale','reason':'observation_expired'}
        candidate=next((c for c in old['candidates'] if c['id']==args.get('candidate')),None)
        if not candidate:return {'status':'stale','reason':'unobserved_candidate'}
        if candidate['operation']=='WAIT': await asyncio.sleep(.2); return {'status':'completed'}
        current=await self.connected(sid,target,{'action':'observe'})
        if current.get('revision')!=old['revision']:return {'status':'stale','reason':'observation_changed'}
        element=next(e for e in old['state']['elements'] if e['id']==candidate['target'])
        operation='browser.type' if candidate['operation']=='TYPE_TEXT' else 'browser.click'
        params={'selector':element['selector']}
        if operation=='browser.type': params['text']=args.get('text','')
        result=await self.remote(sid,target,operation,params)
        return result if 'status' in result else {'status':'completed'}

    async def call(self, sid, operation, args):
        if not sid: raise web.HTTPBadRequest(text='A chat is required.')
        self.workspace(sid)
        if operation=='configuration':
            # The worker owns its mounted provider/bundle snapshot. Live host
            # preferences may force ordinary selection, never name a backend
            # added after this worker started or erase bundle-local bindings.
            return {'stopped':sid in self.stopped,
                    **({'selection':{'mode':'standard'}} if self.effective(sid)['selection']=='standard' else {})}
        if operation=='progress':
            self.progress[sid]={**args,'at':time.time()};return {'status':'recorded'}
        if operation!='browser':raise web.HTTPBadRequest(text='Unsupported UI operation.')
        if args.get('action')=='targets':
            await self.refresh_managed()
            return self.status(sid)
        if sid in self.stopped:return {'status':'denied','reason':'user_stopped','recovery':'The user can resume from Browser & computer controls.'}
        target=args.get('target') or self.active.get(sid) or self.effective(sid)['target']
        async with self.locks.setdefault(sid,asyncio.Lock()):
            if sid in self.stopped:return {'status':'denied','reason':'user_stopped'}
            if target!='managed':
                result=await self.connected(sid,target,args)
            else:
                await self.refresh_managed()
                if sid not in self.browsers and len(self.browsers)>=8:
                    return {'status':'blocked','reason':'managed_browser_limit','recovery':'Close an Amplifier browser in another chat before opening another one.'}
                if not self.managed_available():return {'status':'blocked','reason':'managed_browser_unavailable','recovery':'Connect a browser from Browser & computer, or install managed browser support on this service.'}
                browser=await self.managed(sid)
                if args['action']=='open':
                    try:result=await browser.open(args['url'])
                    except ValueError as exc:raise web.HTTPBadRequest(text=str(exc))
                    except Exception:
                        return {'status':'blocked','reason':'browser_open_unavailable','recovery':'The browser could not open that page. Check its address and service browser setup, or choose a connected browser.'}
                elif args['action']=='observe':
                    if browser.page is None:return {'status':'blocked','reason':'browser_not_open','recovery':'Open the requested URL with browser first.'}
                    result=await browser.observe()
                elif args['action']=='act':
                    observed=browser.observations.get(args.get('revision'))
                    candidate=next((c for c in observed['candidates'] if c['id']==args.get('candidate')),None) if observed else None
                    result=await browser.execute(observed,candidate,args.get('text')) if candidate else {'status':'stale','reason':'unobserved_candidate'}
                else:raise web.HTTPBadRequest(text='Unsupported browser action.')
            if result.get('status') not in {'blocked','denied'}:self.active[sid]=target
            return {**result,'target':target}

    async def close(self):
        if self._managed_check:
            self._managed_check.cancel()
            await asyncio.gather(self._managed_check,return_exceptions=True)
        await asyncio.gather(*(b.close() for b in self.browsers.values()),return_exceptions=True)


def setup_routes(app):
    host=UIUse(app['service'],app['device_connections']);app['service'].ui_use=host
    async def api(request):
        owner(request,mutation=request.method=='POST')
        sid=request.query.get('sessionId') or None
        suffix=request.match_info['operation']
        if request.method=='GET' and suffix=='state':
            await host.refresh_managed()
            return web.json_response({**host.status(sid),'csrf':csrf(request) if request.cookies else None},headers={'Cache-Control':'no-store'})
        if suffix=='desktop-frame' and request.method=='GET':
            host.workspace(sid)
            if not sid or not host.service.runtime or sid not in host.service.runtime.workers:
                raise web.HTTPConflict(text='Start this chat with a configured computer executor first.')
            receipt=await host.service.runtime.control(sid,'tool.invoke',{'name':'computer','arguments':{'action':'screenshot'}})
            result=receipt.get('result',{})
            if not result.get('success'):raise web.HTTPConflict(text='Desktop capture is unavailable or was denied.')
            payload=json.loads(result.get('output','{}'))
            paths=payload.get('images',[])
            if len(paths)!=1:raise web.HTTPConflict(text='The computer executor did not return a screenshot.')
            path=Path(paths[0]).resolve()
            # Only canonical computer-use's private captures may be served here.
            if not path.is_relative_to((Path.home()/'.amplifier/computer-use/shots').resolve()) or not path.is_file() or path.stat().st_size>8_000_000:
                raise web.HTTPConflict(text='The desktop capture is outside its private store.')
            return web.Response(body=path.read_bytes(),content_type='image/png',headers={'Cache-Control':'no-store'})
        if suffix=='frame' and request.method=='GET':
            host.workspace(sid)
            target=host.active.get(sid) or host.effective(sid)['target']
            if target!='managed':
                import base64
                result=await host.remote(sid,target,'browser.task.screenshot',{})
                if not result.get('image'):raise web.HTTPConflict(text=result.get('recovery','Live view requires full-tab access.'))
                return web.Response(body=base64.b64decode(result['image'],validate=True),content_type='image/jpeg',headers={'Cache-Control':'no-store'})
            browser=host.browsers.get(sid)
            if not browser or not browser.page:raise web.HTTPNotFound(text='Open a page in this chat first.')
            async with browser.lock:
                data=await browser.page.screenshot(type='jpeg',quality=70,timeout=5000)
            return web.Response(body=data,content_type='image/jpeg',headers={'Cache-Control':'no-store'})
        if request.method!='POST':raise web.HTTPMethodNotAllowed(request.method,['POST'])
        args=await body(request)
        if suffix=='preferences':return web.json_response(host.configure(sid,args))
        if not sid:raise web.HTTPBadRequest(text='Select a chat first.')
        host.workspace(sid)
        if suffix=='stop':
            host.stop(sid,True)
            return web.json_response(host.status(sid))
        if suffix=='close':
            host.stop(sid,True)
            async with host.locks.setdefault(sid,asyncio.Lock()):
                browser=host.browsers.pop(sid,None)
                if browser:await browser.close()
                host.active.pop(sid,None)
            return web.json_response(host.status(sid))
        if suffix=='resume':host.stop(sid,False);return web.json_response(host.status(sid))
        if suffix=='browser':return web.json_response(await host.call(sid,'browser',args))
        if suffix=='interact':
            browser=host.browsers.get(sid)
            if not browser or not browser.page:raise web.HTTPConflict(text='No managed browser is open.')
            # Human takeover pauses agent actions. Observations are invalidated.
            host.stop(sid,True)
            async with browser.lock:
                browser.observations.clear()
                if args.get('action')=='click':
                    x,y=args.get('x'),args.get('y')
                    if not isinstance(x,(int,float)) or not isinstance(y,(int,float)) or not 0<=x<1280 or not 0<=y<900:raise web.HTTPBadRequest()
                    await browser.page.mouse.click(x,y)
                elif args.get('action')=='type' and isinstance(args.get('text'),str) and len(args['text'])<=4000:
                    await browser.page.keyboard.insert_text(args['text'])
                elif args.get('action')=='key' and args.get('key') in {'Tab','Enter','Escape','Backspace','ArrowDown','ArrowUp','Control+A','Meta+A'}:
                    await browser.page.keyboard.press(args['key'])
                elif args.get('action')=='scroll' and args.get('direction') in {'up','down'}:
                    await browser.page.mouse.wheel(0,600 if args['direction']=='down' else -600)
                else:raise web.HTTPBadRequest(text='Unsupported browser input.')
            return web.json_response(host.status(sid))
        raise web.HTTPNotFound()
    app.router.add_route('GET','/api/ui-use/v1/{operation}',api)
    app.router.add_route('POST','/api/ui-use/v1/{operation}',api)
    async def cleanup(app):await host.close()
    app.on_cleanup.append(cleanup)
