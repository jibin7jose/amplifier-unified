import {prepareCanvasReaderInput,recordCanvasInputFailure} from './canvas-reader-input.mjs';
// Real host + built bundled client. Synthetic file data and runtime only.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {bounded,withActionRoute} from './chat-reference-harness.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/attachment-experience');
const hash=b=>createHash('sha256').update(b).digest('hex');
const report={source:'built client and real host actions',runtime:'synthetic',physicalGestures:false,screenReader:false,cases:[],errors:[],routes:[],downloads:[],geometry:[]};
await mkdir(out,{recursive:true});
const save=()=>writeFile(join(out,'report.json'),JSON.stringify(report,null,2));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),['-u',join(process.env.AMPLIFIER_TEST_FIXTURE_ROOT||join(root,'tests/fixtures'),'attachment_consumption_ui_server.py')],{stdio:['ignore','pipe','pipe']});
const stop=ownFixture(fixture,{label:'Attachment experience fixture'});let browser,base,primary,stderr='';
fixture.stderr.on('data',b=>{stderr=(stderr+b).slice(-12000)});
const ready=new Promise((resolve,reject)=>{
 let data='';const timer=setTimeout(()=>end(Error('Fixture readiness timeout')),30000);
 const end=(error,value)=>{clearTimeout(timer);fixture.stdout.off('data',receive);fixture.off('exit',exit);fixture.off('error',end);error?reject(error):resolve(value)};
 const exit=code=>end(Error('Fixture exit '+code+' '+stderr));
 const receive=b=>{data+=b;for(let i;(i=data.indexOf('\n'))>=0;){const line=data.slice(0,i);data=data.slice(i+1);let row;try{row=JSON.parse(line)}catch{continue}if(row.fixture==='attachment-consumption')end(null,row)}};
 fixture.stdout.on('data',receive);fixture.once('exit',exit);fixture.once('error',end);
});
const call=(page,action,args={})=>bounded(page.evaluate(([a,b])=>window.amplifier.dispatch(a,b),[action,args]),15000,'Action '+action);
const state=page=>bounded(page.evaluate(()=>window.amplifier.getState()),5000,'State read');
async function inspect(page){const s=await state(page);const r=await page.request.get(base+'/fixture/attachment-consumption?clientId='+s.client.id,{timeout:5000});assert.equal(r.status(),200);return r.json()}
async function control(page,data){const r=await page.request.post(base+'/fixture/attachment-consumption',{data,timeout:7000});assert.equal(r.status(),200)}
async function png(page,w,h){return Buffer.from(await page.evaluate(([w,h])=>{const c=document.createElement('canvas');c.width=w;c.height=h;const x=c.getContext('2d');x.fillStyle='#607ca2';x.fillRect(0,0,w,h);x.fillStyle='#eff3ff';x.fillRect(w/4,h/4,w/2,h/2);return c.toDataURL('image/png').split(',')[1]},[w,h]),'base64')}
async function upload(page,name,data){await page.getByLabel('Attach files',{exact:true}).setInputFiles({name,mimeType:'image/png',buffer:data});await expect.poll(async()=>{const s=await state(page);return s.sessions.find(r=>r.id===s.selectedSessionId)?.draftAttachments?.find(f=>f.name===name)?.id},{timeout:10000}).toMatch(/^[a-f0-9]{32}$/);const s=await inspect(page);return s.attachments[s.selectedSessionId].find(f=>f.name===name)}
async function drops(page,selector,files,type='drop'){
 const dt=await page.evaluateHandle(files=>{const d=new DataTransfer();for(const f of files)d.items.add(new File([Uint8Array.from(atob(f.base64),c=>c.charCodeAt(0))],f.name,{type:f.mime||'image/png'}));return d},files);
 try{await page.locator(selector).first().dispatchEvent(type,{dataTransfer:dt})}finally{await dt.dispose()}
}
async function original(page,file,data){const r=await page.request.get(base+file.url,{timeout:5000});assert.equal(r.status(),200);assert.equal(hash(await r.body()),hash(data))}
async function geometry(page){return page.locator('[data-part="attachment-preview"] .a-image-preview-plane img').evaluate(img=>{const stage=img.closest('.a-image-preview-stage'),a=img.getBoundingClientRect(),b=stage.getBoundingClientRect();return {width:a.width,height:a.height,natural:[img.naturalWidth,img.naturalHeight],stage:[b.width,b.height],scroll:[stage.scrollLeft,stage.scrollTop,stage.scrollWidth,stage.scrollHeight],viewport:[innerWidth,innerHeight]}})}
function assertFit(g){assert.ok(g.width<=g.natural[0]+.6&&g.height<=g.natural[1]+.6,'No natural-size upscaling');assert.ok(g.width<=g.stage[0]+.6&&g.height<=g.stage[1]+.6,'Whole image contained');assert.ok(Math.abs(g.width/g.height-g.natural[0]/g.natural[1])<.01)}
async function open(page,name,scope='.a-composer'){
 const opener=page.locator(scope).getByRole('button',{name:'Preview '+name,exact:true}).first();await opener.click();await expect(page.getByRole('dialog',{name:'Image preview',exact:true})).toBeVisible();await expect(page.locator('.a-image-preview-plane img')).toBeVisible();return opener;
}
async function close(page,opener){await page.keyboard.press('Escape');await expect(page.locator('dialog[open]')).toHaveCount(0);await expect(opener).toBeFocused()}
async function snapshot(page){const s=await state(page),i=await inspect(page);return {selected:s.selectedSessionId,canvas:s.canvas,drafts:i.drafts,attachments:i.attachments,sendCount:i.sends.length,scroll:await page.locator('.a-messages').evaluate(n=>n.scrollTop)}}
async function contextFor(viewport,scheme){const context=await browser.newContext({acceptDownloads:true,viewport,colorScheme:scheme,hasTouch:viewport.width<500,isMobile:viewport.width<500,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});await context.route(url=>url.origin!==base,r=>r.abort('blockedbyclient'));const page=await context.newPage();page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(15000);page.on('pageerror',e=>report.errors.push(e.message));assert.equal((await page.request.post(base+'/fixture/browser-session',{timeout:5000})).status(),200);await page.goto(base);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id,null,{timeout:15000});await call(page,'session.create',{title:'Image study',bundle:'anchors'});await call(page,'view.update',{patch:{scheme}});await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();return {context,page}}
async function run(name,fn){const row={name,status:'running'};report.cases.push(row);try{await fn();row.status='passed'}catch(e){row.status='failed';row.error=e.stack;primary??=e}await save()}
try{
 const r=await ready;base=r.base;assert.equal(r.pid,fixture.pid);const build=await readFile(join(root,'amplifier_web/static/build.json'));assert.equal(hash(build),r.buildSha256);report.build={sha256:r.buildSha256,package:r.packageRoot,metadata:JSON.parse(build)};
 browser=await chromium.launch({headless:true,timeout:20000});
 for(const [width,height,scheme] of [[1280,900,'light'],[390,844,'dark'],[320,700,'light']])await run('viewer-'+width+'-'+scheme,async()=>{
  const {context,page}=await contextFor({width,height},scheme);
  try{
   const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.fill('Keep this unsent text');
   const small=await png(page,28,16),large=await png(page,2400,1500),tall=await png(page,400,2000),wide=await png(page,2200,300);
   const files=[];for(const [name,data] of [['small.png',small],['large.png',large],['tall.png',tall],['wide.png',wide]])files.push(await upload(page,name,data));
   const before=await snapshot(page),pages=context.pages().length;
   for(const file of files){
    const opener=await open(page,file.name);const g=await geometry(page);assertFit(g);report.geometry.push({width,scheme,name:file.name,...g});assert.equal(context.pages().length,pages,'Preview never opens another window');
    await expect(page.getByRole('button',{name:'Close image preview'})).toBeFocused();
    for(let i=0;i<10;i++){await page.keyboard.press('Tab');assert.ok(await page.locator('dialog').evaluate(d=>d.contains(document.activeElement)))}
    await page.getByRole('button',{name:'100%',exact:true}).click();const actual=await geometry(page);assert.ok(Math.abs(actual.width-actual.natural[0])<.6);
    await page.getByRole('button',{name:'Zoom in',exact:true}).click();const zoomed=await geometry(page);assert.ok(zoomed.width>actual.width);
    const stage=page.getByLabel('Image viewport',{exact:true});await stage.focus();await page.keyboard.press('0');assertFit(await geometry(page));await page.keyboard.press('1');
    await stage.evaluate(n=>{n.scrollLeft=1e8;n.scrollTop=1e8});const pan=await geometry(page);assert.ok(pan.scroll[0]<=pan.scroll[2]-pan.stage[0]+2);assert.ok(pan.scroll[1]<=pan.scroll[3]-pan.stage[1]+2);
    await page.getByRole('button',{name:'Fit',exact:true}).click();assertFit(await geometry(page));
    const closeBox=await page.getByRole('button',{name:'Close image preview'}).boundingBox();assert.ok(closeBox.x>=0&&closeBox.y>=0&&closeBox.x+closeBox.width<=width&&closeBox.y+closeBox.height<=height,'Close remains in viewport');
    await close(page,opener);
   }
   let opener=await open(page,'large.png');await page.screenshot({path:join(out,`viewer-${width}-${scheme}.png`)});
   await page.setViewportSize({width:height,height:width});await expect.poll(async()=>{try{assertFit(await geometry(page));return true}catch{return false}}).toBe(true);
   await page.setViewportSize({width,height});await expect.poll(async()=>{try{assertFit(await geometry(page));return true}catch{return false}}).toBe(true);
   await page.getByRole('button',{name:'100%',exact:true}).click();const stage=page.getByLabel('Image viewport',{exact:true});
   if(width>500){const box=await stage.boundingBox(),beforePan=await geometry(page);await page.mouse.move(box.x+100,box.y+100);await page.mouse.down();await page.mouse.move(box.x+30,box.y+30,{steps:5});await page.mouse.up();const afterPan=await geometry(page);assert.ok(afterPan.scroll[0]>beforePan.scroll[0]&&afterPan.scroll[1]>beforePan.scroll[1],'Actual mouse drag pans');}
   else{
    const cdp=await context.newCDPSession(page),box=await stage.boundingBox();await stage.evaluate(n=>{n.scrollTop=0;n.scrollLeft=0});
    try{const x=box.x+box.width/2,y=box.y+box.height/2;await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});for(let step=1;step<=5;step++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y-step*16}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await expect.poll(async()=>(await geometry(page)).scroll[1]).toBeGreaterThan(0);assert.equal(await stage.evaluate(n=>getComputedStyle(n).touchAction),'auto');}
    finally{await cdp.detach()}
   }
   for(let n=0;n<12;n++)if(await page.getByRole('button',{name:'Zoom in',exact:true}).isEnabled())await page.getByRole('button',{name:'Zoom in',exact:true}).click();
   await expect(page.getByRole('button',{name:'Zoom in',exact:true})).toBeDisabled();assert.ok((await geometry(page)).width<=2400*8+.6);
   await page.getByRole('button',{name:'Fit',exact:true}).click();
   const download=page.waitForEvent('download',{timeout:10000});await page.getByRole('link',{name:'Download original',exact:true}).click();const dl=await download;assert.equal(dl.suggestedFilename(),'large.png');const bytes=await readFile(await dl.path());assert.equal(hash(bytes),hash(large));report.downloads.push({width,sha256:hash(bytes),bytes:bytes.length});
   await drops(page,'.a-image-preview-tools',[{name:'blocked.png',base64:small.toString('base64')}]);await expect(page.getByText('Close the image preview before attaching files.',{exact:true})).toBeVisible();await close(page,opener);
   await expect(composer).toHaveDraft('Keep this unsent text');const after=await snapshot(page);assert.deepEqual(after,before,'Preview preserves chat, draft, Canvas, reading and sends');
   // The same no-popup oracle rejects the old explicit _blank interaction.
   const popup=page.waitForEvent('popup',{timeout:5000});await page.evaluate(url=>{const a=document.createElement('a');a.href=url;a.target='_blank';a.textContent='Old preview negative control';document.body.append(a);a.click();a.remove()},files[0].url);const extra=await popup;assert.throws(()=>assert.equal(context.pages().length,pages));await extra.close();
   // The fit oracle rejects a rendered oversize/upscaled candidate.
   opener=await open(page,'small.png');await page.locator('.a-image-preview-plane img').evaluate(n=>{n.style.width='280px';n.style.height='160px'});const bad=await geometry(page);assert.throws(()=>assertFit(bad));await close(page,opener);
   // Send only to the isolated synthetic runtime to exercise the sent strip.
   const owner=(await state(page)).selectedSessionId;await composer.fill('Synthetic send');await page.getByRole('button',{name:'Send message',exact:true}).click();await expect.poll(async()=>(await inspect(page)).sends.filter(s=>s.sessionId===owner).length).toBe(1);const input=(await inspect(page)).sends.find(s=>s.sessionId===owner).inputId;await control(page,{op:'accept',inputId:input});await control(page,{op:'finish',inputId:input});await expect(page.locator('.a-composer .a-attachment')).toHaveCount(0);opener=await open(page,'small.png','.a-message');await close(page,opener);await original(page,files[0],small);
  }finally{try{await control(page,{op:'release-all'})}finally{await bounded(context.close(),10000,'Close viewer context')}}
 });
 await run('decode-error-identity-reload-and-dirty-canvas',async()=>{
  const {context,page}=await contextFor({width:1280,height:900},'light');
  let failure;
  try{
   const aBytes=await png(page,640,480),bBytes=await png(page,32,24),a=await upload(page,'decode-a.png',aBytes),b=await upload(page,'decode-b.png',bBytes);
   // Delay the real decode boundary, not a fabricated host projection.
   await page.evaluate(url=>{const original=HTMLImageElement.prototype.decode;window.restoreDecode=()=>{HTMLImageElement.prototype.decode=original};HTMLImageElement.prototype.decode=function(){const work=original.call(this);if(this.src.endsWith(url))return work.then(()=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(Error('Decode gate deadline')),10000);window.releaseDecode=()=>{clearTimeout(timeout);resolve()}}));return work}},a.url);
   const first=page.locator('.a-composer').getByRole('button',{name:'Preview decode-a.png',exact:true}).first();await first.click();await expect(page.getByText('Loading image…',{exact:true})).toBeVisible();await expect(page.locator('.a-image-preview-plane img')).toHaveCount(0);await page.waitForFunction(()=>!!window.releaseDecode);
   await close(page,first);const second=await open(page,'decode-b.png');await page.evaluate(()=>window.releaseDecode());assert.deepEqual((await geometry(page)).natural,[32,24]);await close(page,second);await page.evaluate(()=>window.restoreDecode());
   // The image can be decoded from memory; an HTTP 404 interception alone is
   // not a decode-error control. Reject the actual decoder boundary once.
   await page.evaluate(()=>{const decode=HTMLImageElement.prototype.decode;HTMLImageElement.prototype.decode=function(){return Promise.reject(new DOMException('Controlled decode failure','EncodingError'))};window.restoreDecode=()=>{HTMLImageElement.prototype.decode=decode}});
   try{await first.click();await expect(page.getByRole('button',{name:'Retry preview'})).toBeVisible();await expect(page.locator('.a-image-preview-plane img')).toHaveCount(0);}
   finally{await page.evaluate(()=>window.restoreDecode())}
   await page.getByRole('button',{name:'Retry preview'}).click();await expect(page.locator('.a-image-preview-plane img')).toBeVisible();assertFit(await geometry(page));await close(page,first);
   await page.reload();await expect(first).toBeVisible();const reopened=await open(page,'decode-b.png');assert.deepEqual((await geometry(page)).natural,[32,24]);await close(page,reopened);await original(page,a,aBytes);await original(page,b,bBytes);
   await call(page,'view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
   await call(page,'canvas.show',{kind:'markdown',title:'Kept editing',content:'# Original'});
   const manifest={id:'example.attachment-dirty',label:'Attachment dirty check',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
   const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");return React.createElement("label",null,"Unsaved attachment-test edit",React.createElement("input",{value:text,onChange:e=>{setText(e.target.value);host.setDirty(true)}}))}';
   const digest=(await call(page,'shell.packages.stage',{manifest,source})).result.digest;assert.equal((await call(page,'shell.packages.validate',{digest})).result.status,'passed');
   await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);const editor=page.getByLabel('Unsaved attachment-test edit');
   await prepareCanvasReaderInput(page,{target:editor,label:'attachment-editor',outputDir:out,expectCovered:true});
   await editor.fill('Keep unsaved Canvas edit');await expect.poll(async()=>(await call(page,'canvas.views.inspect')).result.views.find(v=>v.viewId==='primary')?.dirty).toBe(true);
   const before=(await call(page,'canvas.views.inspect')).result.views,opener=await open(page,'decode-a.png');await close(page,opener);await expect(editor).toHaveValue('Keep unsaved Canvas edit');assert.deepEqual((await call(page,'canvas.views.inspect')).result.views,before);
   await drops(page,'[data-canvas-view="primary"]',[{name:'not-chat.png',base64:bBytes.toString('base64')}]);assert.equal((await inspect(page)).attachments[(await state(page)).selectedSessionId].length,2,'Canvas is not an attachment target');
  }catch(error){failure=await recordCanvasInputFailure(page,error,{outputDir:out,label:'attachment-identity'});throw failure}
  finally{try{await bounded(context.close(),10000,'Close identity context')}catch(error){throw failure?new AggregateError([failure,error],'Attachment failure and context cleanup',{cause:failure}):error}}
 });
 await run('whole-chat-drop-and-owner',async()=>{
  const {context,page}=await contextFor({width:1280,height:900},'light');
  try{
   const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.fill('Never send this draft');const owner=(await state(page)).selectedSessionId;const data=await png(page,40,24);let admissions=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/api/actions')){try{if(r.postDataJSON()?.action==='attachment.add')admissions++}catch{}}});
   for(const [index,selector] of ['.a-conversation','.a-messages','.a-composer','.a-markdown-composer','.a-composer button[aria-label="Add attachments"]','.a-work-header','.a-work-header button'].entries()){
    const start=admissions;const name='drop-'+index+'.png';const files=[{name,base64:data.toString('base64')}];await drops(page,selector,files,'dragenter');await expect(page.locator('[data-part="chat-file-drop"]')).toBeVisible();await drops(page,selector,files);
    await expect.poll(async()=>(await inspect(page)).attachments[owner]?.filter(x=>x.name===name).length).toBe(1);assert.equal(admissions-start,1,'Exactly one upload despite nested handlers');await expect(page.locator('[data-part="chat-file-drop"]')).toHaveCount(0);await expect(composer).toHaveDraft('Never send this draft');
   }
   // User removal frees two slots; the ordered multi-file case still checks
   // distinct same-name files, rather than relaxing the existing eight-file cap.
   await page.getByRole('button',{name:'Remove drop-0.png',exact:true}).click();
   await page.getByRole('button',{name:'Remove drop-1.png',exact:true}).click();
   await expect.poll(async()=>(await inspect(page)).attachments[owner]?.length).toBe(5);
   const multi=[{name:'same.png',base64:data.toString('base64')},{name:'same.png',base64:data.toString('base64')}];const start=admissions;await drops(page,'.a-attachment img',multi);await expect.poll(async()=>(await inspect(page)).attachments[owner]?.filter(x=>x.name==='same.png').length).toBe(2);assert.equal(admissions-start,2);
   const originalDraft=await inspect(page);assert.equal(originalDraft.sends.filter(s=>s.sessionId===owner).length,0);
   const text=await page.evaluateHandle(()=>{const d=new DataTransfer();d.setData('text/uri-list','https://example.invalid/not-a-file');return d});await page.locator('.a-messages').dispatchEvent('drop',{dataTransfer:text});await text.dispose();assert.equal(admissions,start+2,'Link drag is not a remote upload');
   await drops(page,'.a-messages',[{name:'empty.png',base64:''}]);await expect(page.getByText(/Choose a nonempty file/)).toBeVisible();assert.equal(admissions,start+2);
   await drops(page,'.a-messages',multi,'dragenter');await page.keyboard.press('Escape');await expect(page.locator('[data-part="chat-file-drop"]')).toHaveCount(0);
   // Hold a real response, then select another chat. Admission stays at owner.
   const other=(await call(page,'session.create',{title:'Other draft',bundle:'anchors'})).sessionId;await call(page,'session.select',{id:owner});let release,seen;const gate=new Promise(r=>release=r),arrived=new Promise(r=>seen=r);let held=false;
   await withActionRoute(page,{label:'late-drop-upload',trace:report.routes,release,handle:async route=>{const body=route.request().method()==='POST'?route.request().postDataJSON():null;if(!held&&body?.action==='attachment.add'){held=true;const response=await route.fetch();seen();await gate;await route.fulfill({response});return;}await route.continue();}},async()=>{await drops(page,'.a-messages',[{name:'late.png',base64:data.toString('base64')}]);await bounded(arrived,10000,'Drop action arrived');await call(page,'session.select',{id:other});await composer.fill('Keep other text');release();});
   await expect.poll(async()=>(await inspect(page)).attachments[owner]?.filter(x=>x.name==='late.png').length).toBe(1);assert.equal((await inspect(page)).attachments[other]?.length||0,0);await expect(composer).toHaveDraft('Keep other text');assert.equal((await inspect(page)).sends.filter(s=>[owner,other].includes(s.sessionId)).length,0);
  }finally{try{await control(page,{op:'release-all'})}finally{await bounded(context.close(),10000,'Close drop context')}}
 });
 await run('upload-error-unknown-and-unnamed-handoff',async()=>{
  const {context,page}=await contextFor({width:1280,height:900},'light');
  try{
   const composer=page.getByRole('textbox',{name:'Message Amplifier'}),data=await png(page,48,32),owner=(await state(page)).selectedSessionId;
   await composer.fill('Preserve text across upload errors');
   let requests=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/api/actions')&&r.postDataJSON()?.action==='attachment.add')requests++});
   await drops(page,'.a-messages',[{name:'ordered-first.png',base64:data.toString('base64')},{name:'empty.png',base64:''},{name:'must-not-upload.png',base64:data.toString('base64')}]);
   await expect(page.getByText(/remaining file was not uploaded/)).toBeVisible();
   assert.deepEqual((await inspect(page)).attachments[owner].map(f=>f.name),['ordered-first.png']);assert.equal(requests,1);await expect(composer).toHaveDraft('Preserve text across upload errors');
   // A dropped response is unknown, not permission to retry an attachment action.
   let lost=false;
   await withActionRoute(page,{label:'upload-lost-reply',trace:report.routes,handle:async route=>{
    const body=route.request().method()==='POST'?route.request().postDataJSON():null;
    if(!lost&&body?.action==='attachment.add'){lost=true;await route.fetch();await route.abort('failed');return;}await route.continue();
   }},async()=>{
    await drops(page,'.a-messages',[{name:'unknown.png',base64:data.toString('base64')}]);
    await expect.poll(async()=>(await inspect(page)).attachments[owner]?.filter(f=>f.name==='unknown.png').length).toBe(1);
    await expect(page.locator('.a-upload-status')).toHaveCount(0);assert.equal(requests,2);await expect(composer).toHaveDraft('Preserve text across upload errors');
   });
   await page.reload();await expect(composer).toHaveDraft('Preserve text across upload errors');assert.equal(requests,2,'Reload never replays an unknown upload');
   const workspace=(await state(page)).sessions.find(s=>s.id===owner).workspace;
   await call(page,'session.draft',{workspace});await composer.fill('New unnamed text');
   const draftId=(await state(page)).attachmentDraftId;
   await page.evaluate(()=>{
    const read=FileReader.prototype.readAsDataURL;
    FileReader.prototype.readAsDataURL=function(file){const loaded=this.onload;this.onload=event=>{const timer=setTimeout(()=>{this.onerror?.(new ProgressEvent('error'))},10000);window.releaseFileRead=()=>{clearTimeout(timer);loaded.call(this,event)}};return read.call(this,file)};
    window.restoreFileRead=()=>{FileReader.prototype.readAsDataURL=read};
   });
   try{
    await drops(page,'.a-messages',[{name:'unnamed-late.png',base64:data.toString('base64')}]);await page.waitForFunction(()=>!!window.releaseFileRead);
    const created=(await call(page,'session.create',{workspace,fromDraft:true,expectedDraftId:draftId})).sessionId;
    await call(page,'session.draft',{workspace});await composer.fill('Newer unnamed draft stays here');
    await page.evaluate(()=>window.releaseFileRead());
    await expect.poll(async()=>(await inspect(page)).attachments[created]?.filter(f=>f.name==='unnamed-late.png').length).toBe(1);
    assert.equal((await state(page)).selectedSessionId,null);await expect(composer).toHaveDraft('Newer unnamed draft stays here');
    assert.equal((await inspect(page)).attachments['']?.length||0,0);
    // Deliberate wrong-owner/duplicate controls use real actions, then remove only their own synthetic rows.
    await call(page,'attachment.add',{sessionId:owner,name:'negative.png',base64:data.toString('base64')});
    await call(page,'attachment.add',{sessionId:owner,name:'negative.png',base64:data.toString('base64')});
    const wrong=(await inspect(page)).attachments[owner].filter(f=>f.name==='negative.png');
    assert.throws(()=>assert.equal(wrong.length,1),'Exactly-once oracle detects duplicate admission');
    assert.throws(()=>assert.equal((wrong.length?owner:null),created),'Owner oracle detects wrong draft');
    for(const file of wrong)await call(page,'attachment.remove',{sessionId:owner,id:file.id});
    assert.equal((await inspect(page)).sends.filter(s=>[owner,created].includes(s.sessionId)).length,0);
   }finally{await page.evaluate(()=>{window.releaseFileRead?.();window.restoreFileRead?.()})}
  }finally{await bounded(context.close(),10000,'Close upload recovery context')}
 });
 assert.deepEqual(report.errors,[]);if(primary)throw primary;
}catch(error){primary??=error;report.error=error.stack}
finally{
 try{await finishFixture({label:'Attachment experience',close:[['Chromium',()=>browser?.close()]],stop:async()=>{report.fixtureExit=await stop()}})}catch(e){report.cleanupError=e.stack;primary??=e}
 report.complete=true;report.passed=!primary&&report.cases.every(x=>x.status==='passed')&&report.fixtureExit?.code===0;report.stderr=stderr;
 try{await save()}catch(error){
  report.passed=false;
  primary=primary?new AggregateError([primary,error],'Attachment failure and final report write failure',{cause:primary}):error;
 }
}
if(!report.passed)throw primary||Error('Attachment experience checks incomplete');
console.log('Attachment experience: all focused browser cases passed; emulated mobile, no physical gesture/screen-reader claim.');
