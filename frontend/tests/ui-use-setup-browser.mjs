// Run only against the owned DTU preview. Uses the shipped client and its real
// action wrapper; every installation request is intercepted before the server.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {hostname} from 'node:os';
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {openSettingsPage} from './browser-settings.mjs';

assert.match(hostname(),/device-connect/,'This acceptance test must run in its owned DTU');
const base='/var/lib/device-connect',origin='https://127.0.0.1:8961';
const access=JSON.parse(readFileSync(base+'/access.json'));
const profile=mkdtempSync(base+'/browser/setup-recovery-');
const report={checks:[],installationRequestsReachedServer:0},calls=[],accepted=new Map(),errors=[];
const pass=name=>{report.checks.push(name);console.log('PASS '+name)};
let context,page,mode='reject',failState=false,failedStateReads=0,supportReads=0;
const stored=(kind='browser')=>page.evaluate(kind=>JSON.parse(sessionStorage.getItem('amplifier.uiUseSetup.request.'+kind)||'null'),kind);
try{
 context=await chromium.launchPersistentContext(profile,{headless:true,ignoreHTTPSErrors:true,args:['--no-sandbox','--single-process','--no-zygote','--disable-gpu'],viewport:{width:1500,height:1000}});
 page=await context.newPage();page.setDefaultTimeout(20000);
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/api/ui-use/v1/state*',async route=>{
  const response=await route.fetch(),value=await response.json();supportReads++;
  value.featureSetup={supported:true,pending:false,features:[{id:'managed-browser',label:'Amplifier browser',installed:false},{id:'decisions-openai',label:'OpenAI decisions',installed:false}]};
  await route.fulfill({response,json:value});
 });
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action!=='updates.featureInstall')return route.continue();
  calls.push(body);
  if(mode==='reject')return route.fulfill({status:400,json:{accepted:false,error:'Synthetic setup refusal'}});
  const previous=accepted.get(body.id);
  if(previous){
   assert.deepEqual(body.args,previous.args,'Reconciliation must preserve the original exact arguments');
   return route.fulfill({json:{accepted:true,requestId:body.id}});
  }
  accepted.set(body.id,body);failState=true;
  return route.fulfill({json:{accepted:true,requestId:body.id,hostInstanceId:body.args.hostInstanceId,stateRevision:Number.MAX_SAFE_INTEGER}});
 });
 await page.route('**/api/state',route=>{
  if(!failState)return route.continue();
  failedStateReads++;
  return route.fulfill({status:403,json:{error:'Synthetic post-acceptance state refusal'}});
 });
 await page.goto(origin+'/');
 if(page.url().includes('/login')){
  await page.locator('input[name=username]').fill(access.username);
  await page.locator('input[name=password]').fill(access.password);
  await page.locator('button[type=submit]').click();
 }
 await openSettingsPage(page,'desktop');
 const setup=()=>page.locator('[data-part="ui-use-setup"]').filter({has:page.getByText('Add browser support',{exact:true})});
 async function openSetup(){
  const summary=setup().locator('summary');await summary.waitFor();
  if(await setup().getAttribute('open')===null)await summary.click();
 }
 await openSetup();
 const install=()=>setup().getByRole('button',{name:'Install Amplifier browser',exact:true});
 await install().click();
 await setup().getByRole('alert').filter({hasText:'Synthetic setup refusal'}).waitFor();
 assert.equal(await stored(),null);assert.equal(await install().isEnabled(),true);
 assert.equal(calls.length,1);const refusedId=calls[0].id;
 const reads=supportReads;
 await setup().getByRole('button',{name:'Check support again',exact:true}).click();
 await setup().getByRole('alert').waitFor({state:'hidden'});
 assert.ok(supportReads>reads,'Refresh must read current support even with a supplied report');
 await install().click();
 await setup().getByRole('alert').filter({hasText:'Synthetic setup refusal'}).waitFor();
 assert.equal(calls.length,2);assert.notEqual(calls[1].id,refusedId);
 assert.equal(await stored(),null);assert.equal(await install().isEnabled(),true);
 pass('Confirmed action 400 clears the intent; a new explicit attempt gets a new ID');
 pass('Check support again performs a real status read');

 mode='accept';
 await install().click();
 await setup().getByRole('alert').filter({hasText:'Synthetic post-acceptance state refusal'}).waitFor();
 assert.ok(failedStateReads>0,'The real dispatch waited for state after action acceptance');
 failState=false;
 const uncertain=await stored();assert.ok(uncertain?.id);assert.equal(uncertain.id,calls[2].id);
 assert.equal(await install().isEnabled(),false);assert.equal(accepted.size,1);
 pass('Accepted installation followed by state 403 retains its exact intent');
 await page.reload();
 await openSettingsPage(page,'desktop');await openSetup();
 await setup().getByRole('button',{name:'Check installation request',exact:true}).waitFor();
 assert.deepEqual(await stored(),uncertain);
 assert.equal(await install().isEnabled(),false);
 assert.equal(calls.length,3,'Reload must not resubmit an uncertain installation');
 pass('Reload preserves the unresolved request and never submits another installation');
 await setup().getByRole('button',{name:'Check installation request',exact:true}).click();
 await page.waitForFunction(()=>{const details=[...document.querySelectorAll('[data-part="ui-use-setup"]')].find(node=>node.querySelector('summary')?.textContent==='Add browser support');return [...(details?.querySelectorAll('button')||[])].some(button=>button.textContent==='Check installation request'&&!button.disabled)});
 assert.equal(calls.length,4);assert.equal(calls[3].id,uncertain.id);assert.deepEqual(calls[3].args,uncertain.args);
 assert.equal(accepted.size,1,'Reconciliation must not create a second accepted installation');
 assert.deepEqual(await stored(),uncertain);
 pass('Explicit reconciliation replays the original ID and arguments only');

 // Provider setup uses the separate configuration-scope action wrapper.
 // Confirm that it preserves identity too, including across a new web client.
 const provider=()=>page.locator('[data-part="ui-use-setup"]').filter({has:page.getByText('Add a decision provider',{exact:true})});
 async function openProvider(){
  await openSettingsPage(page,'ai-connections');
  const summary=page.locator('summary').filter({hasText:/^Decision models \(optional\)$/}),disclosure=summary.locator('..');
  if(await disclosure.getAttribute('open')===null)await summary.click();
  await provider().locator('summary').waitFor();
  if(await provider().getAttribute('open')===null)await provider().locator('summary').click();
 }
 await openProvider();
 await provider().getByRole('button',{name:'Install provider support',exact:true}).click();
 await provider().getByRole('alert').filter({hasText:'Synthetic post-acceptance state refusal'}).waitFor();
 failState=false;
 const providerIntent=await stored('decisions');
 assert.equal(calls.length,5);assert.equal(providerIntent.id,calls[4].id);
 assert.equal(providerIntent.args.feature,'decisions-openai');
 assert.equal(await provider().getByLabel('Decision provider support').isEnabled(),false);
 await page.reload();await openProvider();
 await provider().getByRole('button',{name:'Check installation request',exact:true}).waitFor();
 assert.deepEqual(await stored('decisions'),providerIntent);assert.equal(calls.length,5);
 await provider().getByRole('button',{name:'Check installation request',exact:true}).click();
 await page.waitForFunction(()=>{const details=[...document.querySelectorAll('[data-part="ui-use-setup"]')].find(node=>node.querySelector('summary')?.textContent==='Add a decision provider');return [...(details?.querySelectorAll('button')||[])].some(button=>button.textContent==='Check installation request'&&!button.disabled)});
 assert.equal(calls.length,6);assert.equal(calls[5].id,providerIntent.id);assert.deepEqual(calls[5].args,providerIntent.args);
 assert.equal(accepted.size,2,'Only one browser and one provider installation identity may be accepted');
 pass('Provider setup preserves its exact request through configuration scope, reload and reconciliation');
 assert.deepEqual(errors,[]);pass('No browser JavaScript errors');
 report.passed=true;report.requestCount=calls.length;report.acceptedIdentities=accepted.size;report.failedStateReads=failedStateReads;
}catch(error){report.error=error.message;throw error}finally{
 if(page&&!report.passed)await page.screenshot({path:base+'/evidence/ui-use-setup-failure.png'}).catch(()=>{});
 writeFileSync(base+'/evidence/ui-use-setup-browser.json',JSON.stringify(report,null,2));
 try{await context?.close()}finally{rmSync(profile,{recursive:true,force:true})}
}
