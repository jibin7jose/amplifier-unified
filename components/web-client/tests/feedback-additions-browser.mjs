// UNIT fixture: current-source component plus synthetic host, NOT HTTP/SSE E2E.
// No SDK, GitHub, account or user files.
// Run only in the manager's retained DTU after service routing is integrated.
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const directory=await mkdtemp(root+'tests/.feedback-additions-');
const name=directory.split('/').at(-1);
let server,browser;
try{
 await writeFile(directory+'/index.html','<div id="fixture"></div><script type="module" src="./main.jsx"></script>');
 await writeFile(directory+'/main.jsx',String.raw`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {FeedbackFiles} from '../../src/feedback-followup.jsx';
const FID='original-feedback',OTHER='other-feedback';
const fresh=()=>({clients:{a:{files:{},saved:{},receipts:[]},b:{files:{},saved:{},receipts:[]}},calls:[],stageIds:{},mode:'success'});
let storage=JSON.parse(localStorage.getItem('file-fixture')||'null')||fresh();
window.fixture=storage;
function App(){
 const [client,setClient]=useState('a'),[feedbackId,setReport]=useState(FID),[,refresh]=useState(0);
 function commit(){localStorage.setItem('file-fixture',JSON.stringify(storage));refresh(n=>n+1)}
 const record=storage.clients[client];
 async function act(action,args){
  storage.calls.push({action,args:structuredClone(args),client});
  if(action==='feedback.attachment.add'){
   if(storage.mode==='stage-lost-before'){commit();throw Error('Synthetic staging acknowledgement lost before processing')}
   if(!storage.stageIds[args.requestId]){
    const bytes=Uint8Array.from(atob(args.base64),c=>c.charCodeAt(0));
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
    const id=crypto.randomUUID().replaceAll('-','');
    record.files[feedbackId]=[...(record.files[feedbackId]||[]),{id,name:args.name,mime:'text/plain',size:bytes.length,sha256:hash,url:'/fixture-file/'+id}];
    storage.stageIds[args.requestId]={client,feedbackId,id,size:bytes.length,sha256:hash};
   }
   if(storage.mode==='stage-lost-after'){commit();throw Error('Synthetic staging acknowledgement lost after processing')}
  }else if(action==='feedback.attachment.remove'){
   record.files[feedbackId]=(record.files[feedbackId]||[]).filter(row=>row.id!==args.id);
  }else if(action==='feedback.attachments.review'){
   record.receipts.unshift({requestId:args.requestId,feedbackId,action,status:'completed',message:'Reviewed; nothing uploaded.',
    review:{url:'https://github.com/microsoft/amplifier-unified/issues/'+(feedbackId===FID?42:43),issueId:feedbackId===FID?42:43,
     repository:{id:101,full_name:'microsoft/amplifier-unified',private:true},account:{id:7,login:'fixture-owner'},
     manifest:(record.files[feedbackId]||[]).map(({id,name,mime,size,sha256})=>({id,name,mime,size,sha256})).sort((a,b)=>a.id.localeCompare(b.id)),
     disclosure:'Private preflight is not an atomic visibility guarantee. Files remain in repository history; local removal does not delete uploaded history.'}});
  }else if(action==='feedback.attachments.add'){
   const existing=record.receipts.find(row=>row.requestId===args.requestId);
   if(!existing){
    const review=record.receipts.find(row=>row.requestId===args.reviewRequestId);review.consumedBy=args.requestId;
    const partial=storage.mode==='unknown';
    record.receipts.unshift({requestId:args.requestId,feedbackId,action,status:partial?'partial':'submitted',
     filesStored:true,commentStatus:partial?'unknown':'succeeded',message:partial?'Files stored; comment delivery unknown. No replay.':'Files linked; original report preserved.',
     phases:[{phaseId:args.requestId+':ref',status:'succeeded'},{phaseId:args.requestId+':comment',status:partial?'unknown':'succeeded'}],
     attachments:review.review.manifest.map(row=>({...row,url:'https://github.com/microsoft/amplifier-unified/blob/'+('c'.repeat(40))+'/'+row.id+'/'+row.name})),
     ...(partial?{}:{commentUrl:review.review.url+'#issuecomment-123'})});
    if(!partial)record.files[feedbackId]=[];
   }
  }else if(action==='feedback.attachments.reconcile'){
   record.receipts.unshift({requestId:args.requestId,feedbackId,action,status:'completed',message:'Read only; no uploads repeated.'});
  }else throw Error('Unexpected fixture action');
  commit();return {accepted:true};
 }
 window.publishStagingReceipts=()=>{
  const rows=Object.entries(storage.stageIds).filter(([,row])=>row.client===client).map(([requestId,row])=>({requestId,...row}));
  record.stagingReceipts=rows;commit();
 };
 window.copyForRecovery=source=>{
  const original=storage.clients[source],target=source==='a'?'b':'a';
  storage.clients[target]={files:{},saved:structuredClone(original.saved),receipts:original.receipts.map(row=>({...structuredClone(row),readOnly:true})),
   recovery:{[FID]:{resumed:true,blocked:true,files:(original.files[FID]||[]).map(row=>({...structuredClone(row),readOnly:true})),
    hints:[{requestId:'unobserved-addition',action:'feedback.attachments.add',status:'acceptance_not_observed'}]}}};
  commit();
 };
 window.resolvedStagingCopy=()=>{
  const [requestId,binding]=Object.entries(storage.stageIds).find(([,row])=>row.client==='a'&&row.feedbackId===FID);
  const file=storage.clients.a.files[FID].find(row=>row.id===binding.id);
  storage.clients.b={files:{},receipts:[],saved:{[FID]:{ownerClientId:'b',comment:'',
   staging:[{requestId,feedbackId:FID,name:file.name,mime:file.mime,size:file.size,sha256:file.sha256}]}},
   recovery:{[FID]:{resumed:true,blocked:false,files:[{...structuredClone(file),requestId,readOnly:true}],hints:[]}}};
  commit();
 };
 async function save(value){
  const attempt={client,feedbackId,value:structuredClone(value),status:'saving'};
  (storage.saveAttempts||=[]).push(attempt);
  try{
   if(storage.mode==='save-uncertain')throw Error('Synthetic durable intent acknowledgement unavailable');
   if(storage.mode==='save-delayed')await new Promise(resolve=>window.releaseSave=resolve);
   record.saved[feedbackId]=structuredClone(value);attempt.status='acknowledged';commit();
  }catch(error){attempt.status='rejected';commit();throw error}
 }
 return <><label>Fixture client<select aria-label="Fixture client" value={client} onChange={e=>setClient(e.target.value)}><option value="a">a</option><option value="b">b</option></select></label>
  <label>Fixture report<select aria-label="Fixture report" value={feedbackId} onChange={e=>setReport(e.target.value)}><option value={FID}>original</option><option value={OTHER}>other</option></select></label>
  <FeedbackFiles key={client+feedbackId} feedbackId={feedbackId}
   state={{client:{id:client},feedback:{attachmentDrafts:record.files,additions:record.receipts,stagingReceipts:record.stagingReceipts||[],attachmentRecovery:record.recovery||{}}}}
   act={act} saved={record.saved[feedbackId]} save={save}/></>;
}
createRoot(document.getElementById('fixture')).render(<App/>);
`);
 server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false},
  optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await server.listen();browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(server.resolvedUrls.local[0]+'tests/'+name+'/index.html');
 const add=page.getByRole('button',{name:'Add reviewed files',exact:true});
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'reviewed.txt',mimeType:'text/plain',buffer:Buffer.from('fixture bytes')});
 await page.getByText(/SHA256 [a-f0-9]{64}/).waitFor();
 assert.equal(await add.isEnabled(),false);
 await page.getByRole('button',{name:'Review file destination',exact:true}).click();
 await page.getByText('GitHub account fixture-owner (ID 7)',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachments.add').length),0);
 assert.equal(await add.isEnabled(),false); // review alone cannot consent
 await page.getByRole('checkbox').check();assert.equal(await add.isEnabled(),true);
 // Changing the set invalidates review; exact manifest/SHA must be shown again.
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'second.txt',mimeType:'text/plain',buffer:Buffer.from('second fixture')});
 await page.waitForFunction(()=>window.fixture.clients.a.files['original-feedback']?.length===2);
 assert.equal(await add.isEnabled(),false);
 await page.getByRole('button',{name:'Review file destination',exact:true}).click();
 await page.getByText('GitHub account fixture-owner (ID 7)',{exact:true}).waitFor();
 await page.getByRole('checkbox').check();
 await page.getByLabel('File addition comment (optional)',{exact:true}).fill('A retained ordinary-file draft');
 await page.getByLabel('File addition comment (optional)',{exact:true}).blur();
 await page.waitForFunction(()=>window.fixture.clients.a.saved['original-feedback']?.comment==='A retained ordinary-file draft');
 await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 assert.equal(await page.getByText(/SHA256 [a-f0-9]{64}/).count(),0);
 await page.getByLabel('File addition comment (optional)',{exact:true}).fill('Other client draft');
 await page.getByLabel('File addition comment (optional)',{exact:true}).blur();
 await page.waitForFunction(()=>window.fixture.clients.b.saved['original-feedback']?.comment==='Other client draft');
 await page.getByLabel('Fixture client',{exact:true}).selectOption('a');
 assert.equal(await page.getByLabel('File addition comment (optional)',{exact:true}).inputValue(),'A retained ordinary-file draft');
 await page.getByLabel('Fixture report',{exact:true}).selectOption('other-feedback');
 assert.equal(await page.getByText(/SHA256 [a-f0-9]{64}/).count(),0);
 await page.getByLabel('Fixture report',{exact:true}).selectOption('original-feedback');
 await page.reload();
 assert.equal(await page.getByLabel('File addition comment (optional)',{exact:true}).inputValue(),'A retained ordinary-file draft');
 assert.equal(await add.isEnabled(),false); // reload does not recover transient consent
 await page.getByRole('checkbox').check();
 await page.evaluate(()=>window.fixture.mode='unknown');
 await add.click();
 await page.getByText('Files stored; comment delivery unknown. No replay.',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('Follow-up files',{exact:true}).isDisabled(),true);
 assert.equal(await page.getByLabel('File addition comment (optional)',{exact:true}).isDisabled(),true);
 assert.equal(await page.getByRole('button',{name:'Add reviewed files',exact:true}).count(),0);
 const payload=await page.evaluate(()=>window.fixture.clients.a.saved['original-feedback'].pending);
 assert.equal(payload.confirmedFiles.length,2);assert.equal(payload.comment,'A retained ordinary-file draft');
 await page.reload();
 await page.getByRole('button',{name:'Check file delivery (read only)',exact:true}).click();
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachments.add').length),1);
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachments.reconcile').length),1);
 assert.equal(await page.getByRole('link',{name:/Stored /}).count(),2);
 await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 assert.equal(await page.getByLabel('File addition comment (optional)',{exact:true}).inputValue(),'Other client draft');
 // Independent successful addition exposes the canonical comment/file links.
 await page.evaluate(()=>window.fixture.mode='success');
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'success.txt',mimeType:'text/plain',buffer:Buffer.from('success fixture')});
 await page.getByRole('button',{name:'Review file destination',exact:true}).click();
 await page.getByText('GitHub account fixture-owner (ID 7)',{exact:true}).waitFor();
 await page.getByRole('checkbox').check();await add.click();
 await page.getByRole('link',{name:'View file addition comment',exact:true}).waitFor();
 assert.equal(await page.getByRole('link',{name:'View file addition comment',exact:true}).getAttribute('href'),'https://github.com/microsoft/amplifier-unified/issues/42#issuecomment-123');
 await page.setViewportSize({width:390,height:844});
 // Staging loss before processing retains in-memory bytes and the same ID.
 await page.getByRole('button',{name:'Start a new file draft',exact:true}).click();
 await page.evaluate(()=>window.fixture.mode='stage-lost-before');
 const beforeFile={name:'unacknowledged.txt',mimeType:'text/plain',buffer:Buffer.from('pending staging fixture')};
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles(beforeFile);
 await page.getByRole('region',{name:'Unacknowledged file staging'}).waitFor();
 const beforeIntent=await page.evaluate(()=>window.fixture.clients.b.saved['original-feedback'].staging[0]);
 assert.equal(await page.getByLabel('Follow-up files',{exact:true}).isDisabled(),true);
 await page.evaluate(()=>window.fixture.mode='success');
 await page.getByRole('button',{name:'Check same staging request',exact:true}).click();
 await page.waitForFunction(()=>!window.fixture.clients.b.saved['original-feedback'].staging?.length);
 assert.equal(await page.evaluate(id=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add'&&row.args.requestId===id).length,beforeIntent.requestId),2);
 assert.equal(await page.evaluate(id=>Object.keys(window.fixture.stageIds).filter(row=>row===id).length,beforeIntent.requestId),1);
 // Loss after processing: reload cannot retain File objects. Wrong reselection
 // sends nothing; exact reselection uses the original intended ID/hash.
 await page.getByLabel('Fixture report',{exact:true}).selectOption('other-feedback');
 await page.evaluate(()=>window.fixture.mode='stage-lost-after');
 const afterFile={name:'lost-after.txt',mimeType:'text/plain',buffer:Buffer.from('processed staging fixture')};
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles(afterFile);
 await page.getByRole('region',{name:'Unacknowledged file staging'}).waitFor();
 const afterIntent=await page.evaluate(()=>window.fixture.clients.b.saved['other-feedback'].staging[0]);
 await page.reload();
 await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 await page.getByLabel('Fixture report',{exact:true}).selectOption('other-feedback');
 assert.equal(await page.getByRole('button',{name:'Check same staging request',exact:true}).isDisabled(),true);
 await page.getByLabel('Reselect exact pending files',{exact:true}).setInputFiles({...afterFile,buffer:Buffer.from('wrong file bytes')});
 await page.getByText('The operation was not acknowledged. Keep this exact request; an uncertain upload must not be started again.',{exact:true}).waitFor();
 assert.equal(await page.evaluate(id=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add'&&row.args.requestId===id).length,afterIntent.requestId),1);
 await page.evaluate(()=>window.fixture.mode='success');
 await page.getByLabel('Reselect exact pending files',{exact:true}).setInputFiles(afterFile);
 await page.waitForFunction(()=>!window.fixture.clients.b.saved['other-feedback'].staging?.length);
 assert.equal(await page.evaluate(id=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add'&&row.args.requestId===id).length,afterIntent.requestId),2);
 assert.equal(await page.evaluate(()=>window.fixture.clients.b.files['other-feedback'].length),1);
 assert.equal(await page.evaluate(()=>window.fixture.clients.a.saved['original-feedback'].pending.comment),'A retained ordinary-file draft');
 // Positive saved host staging evidence clears uncertainty after reload with
 // no call to staging. This is still a synthetic projection, not an SSE test.
 await page.getByLabel('Fixture report',{exact:true}).selectOption('original-feedback');
 await page.evaluate(()=>window.fixture.mode='stage-lost-after');
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'read-receipt.txt',mimeType:'text/plain',buffer:Buffer.from('saved receipt fixture')});
 await page.getByRole('region',{name:'Unacknowledged file staging'}).waitFor();
 const observedIntent=await page.evaluate(()=>window.fixture.clients.b.saved['original-feedback'].staging[0]);
 await page.reload();await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 await page.evaluate(()=>window.publishStagingReceipts());
 await page.waitForFunction(()=>!window.fixture.clients.b.saved['original-feedback'].staging?.length);
 assert.equal(await page.evaluate(id=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add'&&row.args.requestId===id).length,observedIntent.requestId),1);
 // Saving the complete intended binding must be acknowledged BEFORE dispatch.
 await page.getByLabel('Fixture report',{exact:true}).selectOption('other-feedback');
 await page.evaluate(()=>window.fixture.mode='save-delayed');
 const stageCount=await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add').length);
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'ack-fence.txt',mimeType:'text/plain',buffer:Buffer.from('durable binding fixture')});
 await page.waitForFunction(()=>typeof window.releaseSave==='function');
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add').length),stageCount);
 await page.evaluate(()=>{window.fixture.mode='success';window.releaseSave()});
 await page.waitForFunction(count=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add').length===count,stageCount+1);
 await page.waitForFunction(()=>!window.fixture.clients.b.saved['other-feedback'].staging?.length);
 const fenced=await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add').at(-1));
 assert.equal(fenced.args.name,'ack-fence.txt');
 await page.evaluate(()=>window.fixture.mode='save-uncertain');
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'no-dispatch.txt',mimeType:'text/plain',buffer:Buffer.from('uncertain save')});
 await page.getByText('The operation was not acknowledged. Keep this exact request; an uncertain upload must not be started again.',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add').length),stageCount+1);
 // A same-ID check after the failed save must also remain behind the ACK
 // fence. It must not use locally remembered File objects to bypass it.
 const saveAttemptCount=await page.evaluate(()=>window.fixture.saveAttempts.length);
 await page.getByRole('button',{name:'Check same staging request',exact:true}).click();
 await page.waitForFunction(count=>window.fixture.saveAttempts.length>count&&window.fixture.saveAttempts.at(-1).status==='rejected',saveAttemptCount);
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachment.add').length),stageCount+1);
 // Explicit synthetic attach projection. This does NOT qualify natural api.js
 // reload or installed two-client HTTP/SSE; the manager owns that receiving test.
 await page.evaluate(()=>{window.fixture.mode='success';window.copyForRecovery('a')});
 await page.reload();
 await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 const callsBeforeCopy=await page.evaluate(()=>window.fixture.calls.length);
 await page.getByText(/Read-only recovery/).waitFor();
 assert.equal(await page.getByLabel('Follow-up files',{exact:true}).isDisabled(),true);
 assert.equal(await page.getByRole('button',{name:'Add reviewed files',exact:true}).count(),0);
 assert.equal(await page.getByLabel('Reselect exact pending files',{exact:true}).count(),0);
 assert.equal(await page.getByRole('button',{name:'Check same file request',exact:true}).count(),0);
 assert.equal(await page.getByRole('checkbox').count(),0);
 assert.equal(await page.getByRole('link',{name:/Stored /}).count(),2);
 await page.reload();await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 assert.equal(await page.evaluate(()=>window.fixture.calls.length),callsBeforeCopy);
 await page.getByRole('button',{name:'Check file delivery (read only)',exact:true}).click();
 assert.equal(await page.evaluate(()=>window.fixture.calls.at(-1).action),'feedback.attachments.reconcile');
 await page.getByLabel('Fixture report',{exact:true}).selectOption('other-feedback');
 assert.equal(await page.getByLabel('Follow-up files',{exact:true}).isEnabled(),true);
 await page.getByLabel('Fixture client',{exact:true}).selectOption('a');
 await page.getByLabel('Fixture report',{exact:true}).selectOption('original-feedback');
 assert.equal(await page.evaluate(()=>window.fixture.clients.a.saved['original-feedback'].pending.comment),'A retained ordinary-file draft');
 // Even a forged writable owner label must not expose inherited staging's
 // reselect/send controls. Validated saved-file links remain available.
 await page.evaluate(()=>window.resolvedStagingCopy());
 const resolvedCount=await page.evaluate(()=>window.fixture.calls.length);
 await page.reload();await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 await page.getByRole('link',{name:/Saved .*read only/}).waitFor();
 assert.equal(await page.getByLabel('Reselect exact pending files',{exact:true}).count(),0);
 assert.equal(await page.getByRole('button',{name:'Check same staging request',exact:true}).count(),0);
 assert.equal(await page.getByLabel('Follow-up files',{exact:true}).isDisabled(),true);
 assert.equal(await page.evaluate(()=>window.fixture.calls.length),resolvedCount);
 await page.getByRole('button',{name:'Start a new file draft',exact:true}).click();
 await page.waitForFunction(()=>!window.fixture.clients.b.saved['original-feedback'].staging?.length);
 assert.equal(await page.getByLabel('Follow-up files',{exact:true}).isEnabled(),true);
 assert.equal(await page.evaluate(()=>window.fixture.calls.length),resolvedCount);
 assert.deepEqual(errors,[]);
 console.log('Component UNIT fixture only: original review/consent and exact staging checks retained; complete intent ACK fence; read-only copied recovery, no upload/reselection or transient consent. Natural installed HTTP/SSE two-client receiving remains manager-owned and unqualified here.');
}finally{
 await browser?.close();await server?.close();await rm(directory,{recursive:true,force:true});
}