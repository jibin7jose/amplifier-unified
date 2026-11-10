// Production UI + real app bridge; synthetic runtime, no provider calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser;
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const markers=['PRIVATE_COMPOSER_A','PRIVATE_COMPOSER_B','PRIVATE_ATTACHMENT','PRIVATE_CREDENTIAL','PRIVATE_EDIT'];
const safe=value=>{const text=JSON.stringify(value);for(const marker of markers)assert.ok(!text.includes(marker),`Ambient disclosure: ${marker}`)};
try{
 const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row)}}catch{}})});
 const url=`http://127.0.0.1:${info.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 const bridge=(operation,args)=>control({op:'agent-bridge',operation,args,sessionId:info.alpha});
 const index=await(await fetch(url,{headers})).text(),asset=index.match(/src="(\/assets\/app-[^"]+\.js)"/)[1];
 const served=Buffer.from(await(await fetch(url+asset,{headers})).arrayBuffer()),built=readFileSync(root+'amplifier_web/static'+asset);
 assert.equal(sha(served),sha(built));
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:headers});
 const pages=[await context.newPage(),await context.newPage()],reports=[],errors=[],clients=[];
 for(const [i,page] of pages.entries()){
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(new URL(request.url()).pathname==='/api/view')reports.push(request.postDataJSON())});
  await page.goto(url);
  const editor=page.getByRole('textbox',{name:'Message Amplifier'});await editor.waitFor();
  clients.push(await page.evaluate(()=>window.amplifier.getState().client.id));
  await editor.fill(markers[i]);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe(markers[i]);
  await editor.evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);const selection=getSelection();selection.removeAllRanges();selection.addRange(range)});
  safe(await page.evaluate(()=>window.amplifier.getState().renderedView));
  await expect.poll(async()=> (await bridge('get_state',{path:'/view/draft',clientId:clients[i]})).value).toBe(markers[i]);
 }
 const [page]=pages,clientId=clients[0];
 await page.evaluate(async()=>{
  await window.amplifier.dispatch('attachment.add',{name:'PRIVATE_ATTACHMENT.txt',base64:btoa('PRIVATE_ATTACHMENT content')});
  const state=window.amplifier.getState();
  await window.amplifier.dispatch('view.update',{patch:{messageEdit:{sessionId:state.selectedSessionId,messageId:'answer-0',text:'PRIVATE_EDIT'},panel:'agent'}});
 });
 await expect(page.getByText('Local browser state (includes private input)')).toBeVisible();
 // The local debug panel contains private state but is itself excluded from observations.
 assert.ok((await page.locator('.a-state-view').first().innerText()).includes(markers[0]));
 await page.evaluate(()=>{
  const privateRegion=document.createElement('div');privateRegion.dataset.private='true';
  privateRegion.innerHTML='<label>PRIVATE_CREDENTIAL label<textarea id="private-observation-test" aria-label="PRIVATE_CREDENTIAL label">PRIVATE_CREDENTIAL value</textarea></label>';
  document.querySelector('[data-part="composer"]').append(privateRegion);
  const before=document.createElement('span'),after=document.createElement('span');
  before.textContent='Visible before';after.textContent='Visible after';
  privateRegion.before(before);privateRegion.after(after);
  const range=document.createRange();range.setStartBefore(before);range.setEndAfter(after);
  const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
 });
 safe(await page.evaluate(()=>window.amplifier.getState().renderedView));
 // Trigger another real observation while the private subtree and selected text exist.
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{navWidth:270}}));
 await expect.poll(()=>reports.some(report=>report.controls?.some(control=>control.id==='private-observation-test'))).toBe(true);
 const reported=reports.findLast(report=>report.controls?.some(control=>control.id==='private-observation-test'));
 assert.equal(reported.selectedText,'');
 assert.equal(reported.controls.find(control=>control.id==='private-observation-test').value,'[redacted]');
 await page.evaluate(()=>{const text=document.createElement('p');text.id='public-observation-test';text.textContent='Public selected text';document.querySelector('[data-part="composer"]').append(text);const range=document.createRange();range.selectNodeContents(text);const selection=getSelection();selection.removeAllRanges();selection.addRange(range)});
 assert.equal((await page.evaluate(()=>window.amplifier.getState().renderedView)).selectedText,'Public selected text');
 for(const report of reports)safe(report);
 for(const path of [undefined,'','/view','/sessions','/devices'])safe(await bridge('get_state',{clientId,...(path===undefined?{}:{path})}));
 safe(await bridge('dispatch',{action:'session.rename',args:{id:info.alpha,title:'Privacy accepted'}}));
 assert.equal((await bridge('get_state',{path:'/view/draft',clientId})).value,markers[0]);
 assert.equal((await bridge('get_state',{path:'/view/draft',clientId:clients[1]})).value,markers[1]);
 const edit=await bridge('get_state',{path:'/view/messageEdit',clientId});
 assert.equal(edit.items.find(row=>row.key==='text').value,'PRIVATE_EDIT');
 await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe(markers[0]);
 assert.deepEqual(errors,[]);
 const status=await control({op:'status'});assert.equal(status.runtimeSends,0);
 const receipt={passed:true,productionAsset:asset,sha256:sha(served),browserReports:reports.length,clients:clients.length,runtimeSends:status.runtimeSends,checks:['ambient overview, pointer and unrelated dispatch projection','exact-client targeted composer and unfinished edit reads','DOM text, controls, private ancestors and selection exclusion','local debug panel exclusion','independent same-chat drafts and reload preservation'],limits:['synthetic runtime; no live provider acceptance']};
 if(process.env.PRIVACY_EVIDENCE){mkdirSync(process.env.PRIVACY_EVIDENCE,{recursive:true});writeFileSync(process.env.PRIVACY_EVIDENCE+'/browser-receipt.json',JSON.stringify(receipt,null,2)+'\n');await page.screenshot({path:process.env.PRIVACY_EVIDENCE+'/privacy.png'});}
 console.log(JSON.stringify(receipt));
}finally{await browser?.close();fixture.kill('SIGTERM');}
