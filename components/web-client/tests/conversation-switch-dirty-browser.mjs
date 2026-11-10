import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try {
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--no-sandbox','--single-process','--disable-dev-shm-usage']:[]});
 const page=await browser.newPage({viewport:{width:1500,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const inspect=async()=>(await action('canvas.views.inspect')).result.views;
 const view=async id=>(await inspect()).find(v=>v.viewId===id);
 const target=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(k=>[k,v[k]]));
 await action('session.create');const otherSession=(await state()).selectedSessionId;
 await action('session.create');const session=(await state()).selectedSessionId;
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,draft:'Keep composer target and draft'}});
 await action('canvas.show',{kind:'markdown',title:'Other artifact',content:'# Other source'});
 const otherArtifact=await view('primary');
 await action('canvas.show',{kind:'markdown',title:'Editable artifact',content:'# Original retained source'});
 const first=await view('primary');
 const manifest={id:'example.dirty-editor',label:'Unsaved editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 // The edit deliberately lives only in React state, exercising the public dirty contract.
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");return React.createElement("label",null,"Unsaved renderer edit",React.createElement("input",{value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
 const validation=(await action('shell.packages.validate',{digest})).result;
 assert.equal(validation.status,'passed',JSON.stringify(validation));
 // Primary edits block all transitions that would replace the primary mount.
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const primary=page.locator('[data-canvas-view="primary"]').getByLabel('Unsaved renderer edit');
 // Slow the dirty declaration: the separate navigation queue must not overtake it.
 let releaseDirty,dirtyHeld=false;
 const dirtyGate=new Promise(resolve=>{releaseDirty=resolve});
 const holdDirty=async route=>{
  const request=route.request(),body=request.method()==='POST'?request.postDataJSON():{};
  if(body.action==='canvas.views.dirty'&&body.args.dirty){dirtyHeld=true;await dirtyGate}
  await route.continue();
 };
 await page.route('**/api/actions',holdDirty);
 await primary.fill('PRIMARY UNSAVED EDIT');
 await expect.poll(()=>dirtyHeld).toBe(true);
 const overtaken=page.waitForRequest(request=>request.method()==='POST'&&request.url().endsWith('/api/actions')&&request.postDataJSON().action==='session.select',{timeout:250}).then(()=>true,()=>false);
 const navigation=action('session.select',{id:otherSession}).then(()=>({accepted:true}),error=>({error:error.message}));
 assert.equal(await overtaken,false,'Navigation must wait for the preceding dirty declaration');
 releaseDirty();
 assert.match((await navigation).error,/viewer edit/);
 await page.unroute('**/api/actions',holdDirty);
 await expect.poll(async()=>(await view('primary')).dirty).toBe(true);
 const before=(await inspect()).map(target);
 await expect(primary).toHaveValue('PRIMARY UNSAVED EDIT');
 assert.equal((await state()).selectedSessionId,session);
 assert.deepEqual((await inspect()).map(target),before);
 assert.deepEqual(errors,[]);
 console.log('Chat switching preserves a dirty renderer and waits for its pending edit declaration');
} finally {await browser?.close();fixture.kill();}
