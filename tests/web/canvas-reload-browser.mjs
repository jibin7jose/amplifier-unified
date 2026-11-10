import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const built=JSON.parse(readFileSync(root+'amplifier_web/static/build.json'));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,release;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await page.addInitScript(({built})=>{
  window.fixtureInstalled=built;const Native=window.WebSocket;
  window.WebSocket=class extends Native{set onmessage(handler){super.onmessage=event=>{const frame=JSON.parse(event.data),build={serverVersion:window.fixtureInstalled.version,frontend:window.fixtureInstalled};if(frame.event==='state')frame.data.client.build=build;if(frame.event==='state-delta')frame.data.changes.push({path:['client','build'],value:build});handler(new MessageEvent('message',{data:JSON.stringify(frame)}))}}};
 },{built});
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create');await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'markdown',title:'Local editor',content:'# Saved source'});
 const manifest={id:'example.reload-editor',label:'Local editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());const [status,setStatus]=React.useState("");return React.createElement("div",null,React.createElement("label",null,"Local unsaved edit",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true).then(()=>setStatus("acknowledged"),()=>setStatus("rejected"))}})),React.createElement("output",null,status),React.createElement("button",{onClick:async()=>{try{await host.setDirty(false);setText("");setStatus("discarded")}catch{setStatus("discard rejected")}}},"Discard local edit"))}';
 const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
 assert.equal((await action('shell.packages.validate',{digest})).result.status,'passed');
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const input=page.getByLabel('Local unsaved edit'),reload=page.getByRole('button',{name:'Reload now',exact:true});await input.waitFor();
 await page.evaluate(value=>{window.fixtureInstalled=value},{...built,id:'bbbbbbbbbbbbbbbb'});
 await action('view.update',{patch:{navExpanded:false}});await expect(reload).toBeEnabled();
 let navigations=0;page.on('framenavigated',frame=>{if(frame===page.mainFrame())navigations++});
 const mount=await input.getAttribute('data-mount');
 await input.fill('ADMITTED LOCAL EDIT');await expect(page.locator('output')).toHaveText('acknowledged');
 // On the old app this actual click reloads and destroys both text and mount.
 if(await reload.isEnabled())await Promise.all([page.waitForEvent('load'),reload.click()]);
 await expect(input).toHaveValue('ADMITTED LOCAL EDIT');assert.equal(await input.getAttribute('data-mount'),mount);assert.equal(navigations,0);await expect(reload).toBeDisabled();
 await page.getByRole('button',{name:'Discard local edit',exact:true}).click();await expect(input).toHaveValue('');await expect(reload).toBeEnabled();
 let held=false;const gate=new Promise(resolve=>{release=resolve});
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body.action==='canvas.views.dirty'&&body.args.dirty){held=true;await gate;return route.fulfill({status:503,json:{error:'Dirty acknowledgement unavailable'}})}return route.continue()});
 await input.fill('UNACKNOWLEDGED LOCAL EDIT');await expect.poll(()=>held).toBe(true);await expect(reload).toBeDisabled();
 assert.equal((await page.evaluate(()=>window.amplifier.getState())).canvasWorkspace.views[0].dirty,false);
 release();await expect(page.locator('output')).toHaveText('rejected');await expect(reload).toBeDisabled();
 await expect(input).toHaveValue('UNACKNOWLEDGED LOCAL EDIT');assert.equal(await input.getAttribute('data-mount'),mount);assert.equal(navigations,0);
 await page.unroute('**/api/actions');await page.getByRole('button',{name:'Discard local edit',exact:true}).click();await expect(reload).toBeEnabled();
 await Promise.all([page.waitForEvent('load'),reload.click()]);await expect(input).toHaveValue('');assert.notEqual(await input.getAttribute('data-mount'),mount);assert.equal(navigations,1);
 console.log(JSON.stringify({admittedEditRetained:true,pendingDeclarationBlocks:true,rejectedDeclarationBlocks:true,exactMountRetained:true,explicitDiscardAllowsReload:true,mainNavigations:navigations}));
}finally{release?.();try{await browser?.close()}finally{if(fixture.exitCode===null){fixture.kill('SIGTERM');await Promise.race([new Promise(resolve=>fixture.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,3000))]);if(fixture.exitCode===null)fixture.kill('SIGKILL')}}}
