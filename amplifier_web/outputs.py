"""Typed output links and immutable evidence on existing private resources."""
import asyncio
import base64
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from urllib.parse import urlsplit

from amplifier_outputs import OutputRegistry
from amplifier_outputs.git_review import anchors, snapshot as git_snapshot

MAX_FILE = 8*1024*1024
VARIANTS = ['standard','document','email','chat_message','social_post']


def definitions(schema,string):
    common={'sessionId':string(200)}
    identity={**common,'id':string(100)}
    origin={'messageId':string(200),'parentId':string(100),'evidenceIds':{'type':'array','maxItems':20,'items':string(100)}}
    writing={'content':string(100000),'variant':{'enum':VARIANTS},'subject':string(500)}
    image_identity=schema({'id':string(100),'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['id','sha256'])
    return {
        'outputs.list':('List exact saved output relationships without opening or sending anything.',schema({**common,'includeUnlinked':{'type':'boolean'},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':50}},['sessionId'])),
        'outputs.read':('Read bounded immutable content, provenance, lineage and local review comments. External links are references only unless version evidence is supplied.',schema({**identity,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':4000},'commentOffset':{'type':'integer','minimum':0},'commentLimit':{'type':'integer','minimum':1,'maximum':10}},['sessionId','id'])),
        'outputs.image':('Inspect an exact saved RGB/RGBA PNG snapshot up to8MB and4096px per side. UI previews it; a direct app_control call (not nested inside tool_exec) requests typed pixels for its next vision-capable model request. Own snapshots and exact retained results shared through an authorized conversation request are supported. The receipt alone is not visual evidence. Does not read changing source files or start work.',schema({**identity,'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['sessionId','id','sha256'])),
        'outputs.images':('Inspect two saved PNGs together, in the supplied order, for visual comparison. Choose distinct images with exact id and sha256, up to8MB combined and4096px per side. Call directly through app_control to replace the current image selection for this input. Both images must be available and owned here or retained for an authorized conversation request; the compact receipt is metadata-only. Compare the separate current model-input images when their observation says comparisonReady:true; that field is not in this tool receipt. Does not generate or start work.',schema({**common,'images':{'type':'array','minItems':2,'maxItems':2,'items':image_identity}},['sessionId','images'])),
        'outputs.attach':('Attach a file/dataset snapshot, exact saved canvas body, PR or external document reference. Does not publish, fetch remote contents or change selection. File snapshots stay within this conversation workspace.',schema({**common,**origin,'kind':{'enum':['file','dataset','canvas','pull_request','external_document']},'title':string(200),'path':string(4000),'url':string(4000),'canvasId':string(100),'version':string(500),'expectedSha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['sessionId','kind','title'])),
        'outputs.attachImage':('Save an exact generated PNG from a completed image-tool receipt in this workspace. Verifies bytes and SHA-256. Edits require parentId matching the target input hash. Shares the saved image with Canvas; messageId publishes it inline at that original turn, otherwise library-only. Preserves originals and producer-reported provenance; does not generate, spend, fetch or publish externally.',schema({**common,**origin,'title':string(200),'receiptPath':string(4000)},['sessionId','title','receiptPath'])),
        'outputs.attachMedia':('Save exact GIF bytes up to64MiB or MP4 up to32MiB at an existing assistant message. Supply its saved messageId and inspected SHA-256; saving is not browser playback proof.',schema({**common,'messageId':string(200),'title':string(200),'path':string(4000),'expectedSha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['sessionId','messageId','title','path','expectedSha256'])),
        'outputs.attachChatReference':('Attach a fixed Open chat card below an exact saved assistant reply in the calling conversation. The host resolves the existing ordinary chat title and workspace. Does not select, create, send, approve or start work. Opening requires an explicit click in that browser.',schema({**common,'messageId':string(200),'targetSessionId':string(200),'targetNativeProject':string(4000)},['sessionId','messageId','targetSessionId'])),
        'outputs.chatReferences':('Read bounded host-resolved chat cards for an exact assistant reply in the attached browser. Does not navigate or start work.',schema({**common,'messageId':string(200)},['sessionId','messageId'])),
        'outputs.mediaReport':('Authenticated UI media observation, not model attestation; never attaches or starts work. Control state alone does not prove advancement; presentedFrames/mediaTime describe actual presentation callbacks.',schema({**identity,'messageId':string(200),'version':{'type':'integer','minimum':1},'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'},'generation':{'type':'integer','minimum':1},'sequence':{'type':'integer','minimum':1},'renderState':{'enum':['loading','ready','error']},'playback':{'enum':['paused','playing','ended']},'presentedFrames':{'type':'integer','minimum':0},'mediaTime':{'type':'number','minimum':0},'detail':string(500)},['sessionId','id','messageId','version','sha256','generation','sequence','renderState','playback'])),
        'outputs.write':('Save a reusable writing output or an immutable next version. Does not send, publish or overwrite any original.',schema({**common,**origin,**writing,'title':string(200)},['sessionId','title','content','variant'])),
        'outputs.review':('Snapshot a bounded read-only local Git diff for review. No changes applied; untracked and binary content excluded. Branch mode resolves existing base and HEAD commits.',schema({**common,**origin,'mode':{'enum':['unstaged','staged','branch']},'base':string(200),'path':string(4000),'title':string(200)},['sessionId','mode'])),
        'outputs.unlink':('Unlink an output from the active list. The underlying object, snapshot and comments are retained.',schema({**identity,'expectedRevision':{'type':'integer','minimum':1}},['sessionId','id','expectedRevision'])),
        'outputs.relink':('Restore a previously unlinked output relationship.',schema({**identity,'expectedRevision':{'type':'integer','minimum':1}},['sessionId','id','expectedRevision'])),
        'outputs.comment':('Save a local review comment on an exact saved version. It is not posted to a remote PR. Diff locations must match a displayed saved hunk.',schema({**identity,'body':{**string(8000),'minLength':1},'path':string(4000),'side':{'enum':['left','right']},'line':{'type':'integer','minimum':1}},['sessionId','id','body'])),
    }


@contextmanager
def workspace_file(root,value):
    root=Path(root).resolve()
    path=Path(value).expanduser()
    path=(path if path.is_absolute() else root/path).resolve()
    try:relative=path.relative_to(root)
    except ValueError:raise ValueError('Output files must stay inside this conversation workspace.') from None
    if not relative.parts:raise ValueError('Choose a file.')
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        fd=os.open(relative.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Choose a regular output file.')
            yield stream,str(path),relative.name
    finally:os.close(directory)


def file_bytes(root,value):
    with workspace_file(root,value) as (stream,path,name):
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_FILE:
            raise ValueError('Choose a regular output file up to8MB.')
        data=stream.read(MAX_FILE+1)
        after=os.fstat(stream.fileno())
        if len(data)>MAX_FILE or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
            raise ValueError('The output changed while reading. Attach it again deliberately.')
    return data,path,name


class Outputs:
    def __init__(self,app):
        self.app=app
        self.store=OutputRegistry(app.db)
        from .output_media import Media
        self.media=Media(self)
        from .chat_references import ChatReferences
        self.chat_references=ChatReferences(self)

    def record(self,sid,identity):
        record=self.store.read(identity)
        if record['sessionId']!=sid:raise ValueError('This output belongs to another conversation.')
        return record

    def content(self,record):
        if not record.get('body'):return None
        from . import retained_files
        if record.get('bodyFormat') == retained_files.FORMAT:
            from .resource_files import detach
            chunks=retained_files.manifest(detach(self.app.db,record['body']['$resource']),record)
            return retained_files.materialize([detach(self.app.db,row['$resource']) for row in chunks],record,MAX_FILE)
        value=self.app.state_resource(record['body']['$resource'])
        return self.decode_content(record,value)

    @staticmethod
    def decode_content(record,value):
        if record.get('bodyFormat') == 'canvas-image-v1':
            prefix = 'data:image/png;base64,'
            content = value.get('content')
            if not isinstance(content, str) or not content.startswith(prefix):
                raise ValueError('Saved image source is unavailable.')
            data = base64.b64decode(content[len(prefix):], validate=True)
        else:
            data=base64.b64decode(value['data'],validate=True) if value['encoding']=='base64' else value['data'].encode()
        if hashlib.sha256(data).hexdigest()!=record['sha256']:
            raise ValueError('Saved output content no longer matches its evidence hash.')
        return data

    async def content_async(self,record):
        """Read immutable output bytes away from the event loop and app lock."""
        if not record.get('body'):return None
        from . import retained_files
        if record.get('bodyFormat') == retained_files.FORMAT:
            sources=await retained_files.plan(self.app,record)
            return await asyncio.to_thread(retained_files.materialize,sources,record,MAX_FILE)
        from .resource_files import detach, read_detached
        async with self.app.lock:
            source=detach(self.app.db,record['body']['$resource'])
        def materialize():
            try:
                value=read_detached(source,max_bytes=6*20*1024*1024+4096)
            except ValueError as exc:
                raise ValueError('Saved output content is unavailable or fails its evidence hash.') from exc
            return self.decode_content(record,value)
        return await asyncio.to_thread(materialize)

    async def preview_async(self,record,offset=0,limit=4000):
        from . import retained_files
        if record.get('bodyFormat') == retained_files.FORMAT:
            sources=await retained_files.plan(self.app,record)
            return await asyncio.to_thread(retained_files.preview,sources,record,offset,limit)
        data=await self.content_async(record)
        if data is None:return {}
        try:
            text=data.decode('utf-8')
            return {'text':text[offset:offset+limit],'offset':offset,
                'nextOffset':offset+limit if offset+limit<len(text) else None}
        except UnicodeDecodeError:return {'binary':True}

    async def read(self,args):
        async with self.app.lock:
            record=self.record(args['sessionId'],args['id'])
            if record.get('kind') == 'chat_reference':
                return {**self.chat_references.descriptor(record),'kind':'chat_reference','linked':bool(record.get('linked'))}
            if record.get('kind') == 'media':
                observation=self.media.observation(record)
                return {**self.media.descriptor(record),'saved':True,'attached':bool(record.get('linked')),
                    'browserObservation':observation,'playback':observation.get('playback','unknown')}
            comments=self.store.comments(record['id']);start=args.get('commentOffset',0);count=args.get('commentLimit',5)
            result={**record,'comments':comments[start:start+count],'nextCommentOffset':start+count if start+count<len(comments) else None}
        if record.get('body'):
            result.update(await self.preview_async(record,args.get('offset',0),args.get('limit',4000)))
            result['downloadUrl']='/api/outputs/'+record['id']+'/content'
        return result

    def image_record(self,sid,identity,expected_hash):
        self.app._session(sid, hydrate=False)
        record=self.store.read(identity)
        if record['sessionId']!=sid:
            from .result_artifacts import image_record
            record=image_record(self.app.collaboration,sid,record)
        if record.get('sha256')!=expected_hash or record.get('kind') not in {'file','dataset'}:
            raise ValueError('Choose the exact saved file snapshot and content hash.')
        return record

    @staticmethod
    def image_value(record,data):
        if data is None:raise ValueError('This output has no saved image bytes.')
        from .voice_visual_image import validate_png
        width,height=validate_png(data,max_bytes=MAX_FILE,max_dimension=4096)
        return {'id':record['id'],'sha256':record['sha256'],'title':record['title'],'width':width,'height':height,
            'mimeType':'image/png','bytes':len(data),'evidence':'immutable-output-snapshot','untrustedData':True,
            'imageUrl':'/api/outputs/'+record['id']+'/image','_image':base64.b64encode(data).decode()}

    def image(self,sid,identity,expected_hash):
        # The synchronous browser route reads its own authenticated output.
        self.app._session(sid, hydrate=False)
        record=self.record(sid,identity)
        if record.get('sha256')!=expected_hash or record.get('kind') not in {'file','dataset'}:
            raise ValueError('Choose the exact saved file snapshot and content hash.')
        return self.image_value(record,self.content(record))

    @staticmethod
    def validate_pair(identities):
        if (not isinstance(identities,list) or len(identities)!=2 or
                any(not isinstance(item,dict) or set(item)!={'id','sha256'} or
                    any(not isinstance(item[key],str) for key in ('id','sha256')) for item in identities)):
            raise ValueError('Choose exactly two saved image IDs and hashes.')
        if len({item['id'] for item in identities})!=2 or len({item['sha256'] for item in identities})!=2:
            raise ValueError('Choose two different saved images; duplicate IDs or hashes cannot form a comparison.')

    @staticmethod
    def image_pair(images):
        total=sum(image['bytes'] for image in images)
        if total>MAX_FILE:
            raise ValueError('The two saved images exceed the combined8MB limit.')
        return {'images':images,'bytes':total,'limits':{'images':2,'maxBytes':MAX_FILE,'maxSide':4096},
                'imageDelivery':'Both saved images are available. A direct agent inspection requests pixels together; this receipt is not pixels.'}

    def images(self,sid,identities):
        self.validate_pair(identities)
        return self.image_pair([self.image(sid,item['id'],item['sha256']) for item in identities])

    async def image_async(self,sid,identity,expected_hash):
        return (await self._read_images(sid,[{'id':identity,'sha256':expected_hash}]))[0]

    async def images_async(self,sid,identities):
        self.validate_pair(identities)
        return self.image_pair(await self._read_images(sid,identities))

    async def _read_images(self,sid,identities):
        # Detach the selected set once. Hash/decode/PNG/base64 work shares one
        # worker turn, rather than queuing behind the busy app loop per image
        # and per conversion. The database never crosses the worker boundary.
        from . import resource_files, retained_files
        async with self.app.lock:
            records=[self.image_record(sid,item['id'],item['sha256']) for item in identities]
            sources=[resource_files.detach(self.app.db,record['body']['$resource']) for record in records]
        def read_roots():
            values=[]
            for record,source in zip(records,sources):
                if record.get('bodyFormat') == retained_files.FORMAT:
                    values.append(retained_files.manifest(source,record))
                else:
                    value=resource_files.read_detached(source,max_bytes=6*20*1024*1024+4096)
                    values.append(self.image_value(record,self.decode_content(record,value)))
            return values
        images=await asyncio.to_thread(read_roots)
        chunked=[index for index,record in enumerate(records) if record.get('bodyFormat') == retained_files.FORMAT]
        if chunked:
            async with self.app.lock:
                chunks={index:[resource_files.detach(self.app.db,item['$resource']) for item in images[index]]
                        for index in chunked}
            def read_chunks():
                return {index:self.image_value(records[index],retained_files.materialize(sources,records[index],MAX_FILE))
                        for index,sources in chunks.items()}
            for index,value in (await asyncio.to_thread(read_chunks)).items():images[index]=value
        async with self.app.lock:
            current=[self.image_record(sid,item['id'],item['sha256']) for item in identities]
            if current!=records:
                raise ValueError('The saved image relationship changed while reading; inspect again.')
        return images

    def fork(self,source_id,target,canvas_mapping=None,*,media_origins=None):
        kept={row['id'] for row in target.get('messages',[]) if row.get('id')}
        rows=self.app.db.execute('SELECT value FROM output_records WHERE session_id=? ORDER BY created',(source_id,)).fetchall()
        mapping={}
        for saved, in rows:
            original=json.loads(saved)
            if not original.get('linked'):
                continue
            if original.get('kind') == 'chat_reference':
                # A fork/import is not new source authority. Preserve the
                # original relationship only in its source; exports are inert.
                continue
            message_id = original.get('origin', {}).get('messageId')
            if original.get('kind') == 'media':
                if media_origins is not None:
                    # Exact transcript-owner preflight, including old windows
                    # and inserted receipt/bundle index shifts. No text search.
                    message_id = media_origins.get(message_id)
                    if message_id is None:
                        continue
                elif not any(row.get('id') == message_id and row.get('role') == 'assistant'
                             and not row.get('observation') and not row.get('observation_id')
                             for row in target.get('messages', [])):
                    continue
            elif message_id not in kept:
                continue
            value={key:copy.deepcopy(value) for key,value in original.items() if key not in {'id','sessionId','createdAt','revision','version','linked','parentId','evidenceIds'}}
            value['forkSource']={'sessionId':source_id,'outputId':original['id'],'sha256':original.get('sha256')}
            if original.get('kind') == 'media':
                value['origin'] = {**value['origin'], 'messageId': message_id}
            value['parentId']=mapping.get(original.get('parentId'))
            value['evidenceIds']=[mapping[key] for key in original.get('evidenceIds',[]) if key in mapping]
            if original.get('canvasId'):
                value.pop('canvasId', None)
                if canvas_mapping and original['canvasId'] in canvas_mapping:
                    value['canvasId'] = canvas_mapping[original['canvasId']]
            clone=self.store.create(target['id'],value);mapping[original['id']]=clone['id']
            for comment in self.store.comments(original['id']):
                self.store.comment(clone['id'],{**comment,'forkSourceCommentId':comment['id']})

    async def dispatch(self,action,args,origin,command_id,*,image_identity=None):
        from .errors import AppError
        try:
            if action == 'outputs.attachChatReference':
                return await self.chat_references.attach(args, origin, command_id)
            if action == 'outputs.chatReferences':
                return await self.chat_references.describe(args['sessionId'], args['messageId'], origin)
            if action == 'outputs.attachMedia':
                return await self.media.attach(args, origin, command_id)
            if action == 'outputs.mediaReport':
                return await self.media.observe(args, origin)
            if action=='outputs.image':
                result=await self.image_async(args['sessionId'],args['id'],args['sha256']);result.pop('_image')
                result['imageDelivery']='Agent requests receive typed pixels at the next supported model boundary; this receipt is not pixels.'
                return {'accepted':True,'result':result}
            if action=='outputs.images':
                result=await self.images_async(args['sessionId'],args['images'])
                for image in result['images']:image.pop('_image')
                return {'accepted':True,'result':result}
            if action=='outputs.read':
                return {'accepted':True,'result':await self.read(args)}
            async with self.app.lock:
                source = self.app._session(args['sessionId'], hydrate=bool(args.get('messageId')))
                # Output records own their bodies. Retain only the execution
                # fence and location across the file/Git read below.
                session = {key: source[key] for key in
                           ('id', 'workspace', 'workingDirectory', 'executionRevision') if key in source}
                if action=='outputs.list':
                    return {'accepted':True,'result':self.store.list(session['id'],include_unlinked=args.get('includeUnlinked',False),offset=args.get('offset',0),limit=args.get('limit',20))}
                portability = getattr(self.app, 'portability', None)
                transfer_context = portability.write_context(session['id']) if portability else None
                request=[action,args,origin];identity=command_id or uuid.uuid4().hex
                previous=self.store.receipt(identity,request)
                if previous:return {'accepted':True,'result':previous}
                for reference in args.get('evidenceIds',[]):self.record(session['id'],reference)
                if args.get('parentId'):self.record(session['id'],args['parentId'])
                if args.get('messageId') and not any(row.get('id')==args['messageId'] for row in source.get('messages',[])):
                    raise ValueError('Choose an original message visible in this conversation; no turn was inferred.')
            data=None
            value={'kind':args.get('kind','writing' if action=='outputs.write' else 'file' if action=='outputs.attachImage' else 'git_review'),
                'title':args.get('title') or 'Saved Git review','origin':{'source':origin,'messageId':args.get('messageId')},
                'parentId':args.get('parentId'),'evidenceIds':args.get('evidenceIds',[])}
            workspace=session.get('workingDirectory') or session.get('workspace')
            if action=='outputs.attachImage':
                from .image_receipts import read_image_receipt
                data,path,name,receipt=await asyncio.to_thread(read_image_receipt,workspace,args['receiptPath'])
                if image_identity and any(receipt.get(key) != image_identity[key]
                        for key in ('requestId', 'requestHash', 'operation')):
                    raise ValueError('The completed image receipt changed before it could be displayed.')
                if receipt['operation']=='edit':
                    if image_identity and not args.get('parentId'):
                        existing=self.app.db.execute("SELECT id FROM output_records WHERE session_id=? AND json_extract(value,'$.kind')='file' AND json_extract(value,'$.sha256')=? ORDER BY created DESC LIMIT 1",
                            (session['id'],receipt['inputs'][0]['sha256'])).fetchone()
                        if existing:
                            args={**args,'parentId':existing[0]}
                        else:
                            original=receipt['inputs'][0]
                            parent_result=await self.dispatch('outputs.attach',{'sessionId':session['id'],
                                'kind':'file','title':'Original image','path':original['path'],
                                'expectedSha256':original['sha256']},origin,identity+':parent')
                            args={**args,'parentId':parent_result['result']['id']}
                        value['parentId']=args['parentId']
                    if not args.get('parentId'):
                        raise ValueError('Attach the original image first, then supply its parentId for this edit.')
                    parent=self.record(session['id'],args['parentId'])
                    if parent.get('kind')!='file' or parent.get('sha256')!=receipt['inputs'][0]['sha256']:
                        raise ValueError('The saved parent does not match the exact edit input hash.')
                elif args.get('parentId'):
                    raise ValueError('A generated image has no edit parent.')
                value.update(path=path,filename=name,versionEvidence='snapshot',imageGeneration=receipt)
            elif action=='outputs.attach':
                kind=args['kind']
                expected_field='path' if kind in {'file','dataset'} else 'canvasId' if kind=='canvas' else 'url'
                if not args.get(expected_field) or any(args.get(key) for key in {'path','url','canvasId'}-{expected_field}):
                    raise ValueError('Provide exactly the location matching this output kind.')
                if kind in {'file','dataset'}:
                    data,path,name=await asyncio.to_thread(file_bytes,workspace,args['path'])
                    value.update(path=path,filename=name,versionEvidence='snapshot')
                elif kind=='canvas':
                    async with self.app.lock:
                        canvas=next((r for r in self.app.state.get('canvasArtifacts',[]) if r['id']==args['canvasId'] and r.get('sessionId')==session['id']),None)
                        if not canvas:raise ValueError('This saved canvas is unavailable in the conversation.')
                        body=self.app.state_resource(canvas['body']['$resource'])
                        data=json.dumps(body,ensure_ascii=False,sort_keys=True).encode()
                        value.update(canvasId=canvas['id'],canvasKind=canvas['kind'],canvasSource=copy.deepcopy(canvas['body']),filename='canvas.json',versionEvidence='snapshot')
                else:
                    parsed=urlsplit(args['url'])
                    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or any(ord(c)<32 for c in args['url']):
                        raise ValueError('Choose an HTTP(S) link without credentials or control characters.')
                    value.update(url=args['url'],externalVersion=args.get('version'),versionEvidence='user_supplied_reference' if args.get('version') else 'unversioned_reference')
            elif action=='outputs.write':
                if not args['content'].strip():raise ValueError('Writing content is empty.')
                data=args['content'].encode();value.update(variant=args['variant'],subject=args.get('subject'),filename='writing.md',versionEvidence='snapshot')
            elif action=='outputs.review':
                path=args.get('path')
                if path and (Path(path).is_absolute() or '..' in Path(path).parts):raise ValueError('Review paths must be relative to the conversation workspace.')
                review=await git_snapshot(workspace,args['mode'],args.get('base'),path)
                data=review.pop('text').encode();value.update(review=review,filename='review.diff',versionEvidence='snapshot')
            async with self.app.lock:
                current = self.app._session(session['id'], hydrate=bool(args.get('messageId')))
                # File/Git reads above can outlive a task transfer. Recheck
                # authority under the same lock that commits its source fence.
                if portability and portability.write_context(session['id']) != transfer_context:
                    raise ValueError('The task moved while reading the output; inspect and retry.')
                if current.get('executionRevision', 0) != session.get('executionRevision', 0):
                    raise ValueError('The task execution folder changed while reading the output; inspect and retry.')
                if args.get('messageId') and not any(row.get('id') == args['messageId'] for row in current.get('messages', [])):
                    raise ValueError('The original message changed while saving the output. Refresh this conversation.')
                previous=self.store.receipt(identity,request)
                if previous:return {'accepted':True,'result':previous}
                if action=='outputs.attachImage':
                    # A model may also explicitly attach the same receipt after
                    # runtime delivery. Keep one result for that exact origin.
                    existing=self.app.db.execute("SELECT value FROM output_records WHERE session_id=? AND json_extract(value,'$.imageGeneration.receiptSha256')=? AND json_extract(value,'$.origin.messageId') IS ? AND json_extract(value,'$.linked')=1 LIMIT 1",
                        (session['id'],receipt['receiptSha256'],args.get('messageId'))).fetchone()
                    if existing:
                        result=json.loads(existing[0]);self.store.remember(identity,request,result)
                        return {'accepted':True,'result':result}
                if action in {'outputs.unlink','outputs.relink'}:
                    linked_record = self.record(session['id'],args['id'])
                    if action == 'outputs.relink' and linked_record.get('kind') == 'chat_reference' and linked_record.get('referenceImported'):
                        raise ValueError('Imported chat references require a new validated attachment.')
                    if linked_record.get('kind') in {'media', 'chat_reference'}:
                        self.media.authority(current, origin)
                    result=self.store.link(args['id'],args['expectedRevision'],action=='outputs.relink')
                elif action=='outputs.comment':
                    record=self.record(session['id'],args['id'])
                    location={key:args[key] for key in ('path','side','line') if key in args}
                    if location:
                        if len(location)!=3 or record['kind']!='git_review' or (args.get('path'),args.get('side'),args.get('line')) not in anchors(self.content(record).decode()):
                            raise ValueError('Choose a line in this exact saved diff.')
                    result=self.store.comment(record['id'],{'body':args['body'],**location,'source':origin,'sha256':record.get('sha256'),'externalPosted':False})
                else:
                    if data is not None:
                        digest=hashlib.sha256(data).hexdigest()
                        if args.get('expectedSha256') and digest!=args['expectedSha256']:
                            raise ValueError('File contents changed from the expected version.')
                        from .resource_files import put
                        if action == 'outputs.attachImage':
                            body = {'content': 'data:image/png;base64,' + base64.b64encode(data).decode()}
                            value.update(bodyFormat='canvas-image-v1', canvasId=uuid.uuid4().hex)
                        else:
                            try:body={'encoding':'utf-8','data':data.decode('utf-8')}
                            except UnicodeDecodeError:body={'encoding':'base64','data':base64.b64encode(data).decode()}
                        value.update(body=put(self.app.db,body),sha256=digest,bytes=len(data))
                    result=self.store.create(session['id'],value)
                    if action == 'outputs.attachImage':
                        from .canvas_library import image_output
                        image_output(self.app.state, current, result)
                self.store.remember(identity,request,result)
                if action in {'outputs.unlink','outputs.relink'} and result.get('kind') in {'media', 'chat_reference'}:
                    key = 'mediaRevision' if result['kind'] == 'media' else 'chatReferenceVersion'
                    current[key] = current.get(key, 0) + 1
                    self.app._publish_changes(sessions={current['id']})
                elif action == 'outputs.attachImage':
                    self.app._publish_changes(globals={'canvasArtifacts'})
                else:
                    self.app._save_changes()
                return {'accepted':True,'result':result}
        except (ValueError,KeyError,OSError) as exc:
            raise AppError(str(exc),409) from exc
