// Run against rebuilt production assets and the real isolated host fixture.
// Only delivery of a real mutation response is held; no theme result is mocked.
// Target-switch/ABA races are also exercised in appearance-settings.test.mjs:
// the production command queue serializes library reads behind a held mutation.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Appearance late-completion fixture'});
let browser,failure,held;
const action=(page,name,args={})=>page.evaluate(async([name,args])=>{
 const receipt=await window.amplifier.dispatch(name,args);
 if(receipt.accepted===false)throw Error(receipt.error||name+' rejected');
 return receipt;
},[name,args]);
const list=async page=>(await action(page,'theme.list')).result.items;
const stored=page=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.appearance-editor.v1')));
const editor=page=>page.locator('.a-settings-page-content:not([hidden]) .a-appearance-advanced');
const field=(page,id)=>editor(page).locator('#theme-'+id);
const button=(page,name)=>page.getByRole('button',{name,exact:true});
async function openEditor(page){
 if(!await field(page,'name').isVisible())await editor(page).locator('summary').click();
 await expect(field(page,'name')).toBeVisible();
}
async function fill(page,values){
 for(const [id,value] of Object.entries(values))await field(page,id).fill(value);
 await expect.poll(async()=>(await stored(page)).name).toBe(values.name);
}
async function select(page,entry){
 await button(page,'Preview '+entry.name).click();
 await button(page,'Edit '+entry.name).click();
 await expect(field(page,'name')).toHaveValue(entry.name);
}
async function seed(page,name){
 return (await action(page,'theme.save',{name,description:'Synthetic saved entry',css:'#amp-one{--a-accent:#445588}'})).result;
}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
async function holdResponse(page,name){
 const arrived=deferred(),release=deferred(),finished=deferred();
 let captured=false;
 const timer=setTimeout(()=>arrived.reject(Error('The held '+name+' response did not arrive')),20000);
 const handler=async route=>{
  if(captured||route.request().postDataJSON()?.action!==name)return route.continue();
  captured=true;
  try{
   const response=await route.fetch({timeout:15000}),receipt=await response.json();
   assert.ok(response.ok(),'The held response must be an actual successful host mutation');
   assert.equal(receipt.accepted,true);
   arrived.resolve(receipt);
   await release.promise;
   await route.fulfill({response});
  }catch(error){arrived.reject(error);await route.abort().catch(()=>{})}
  finally{clearTimeout(timer);finished.resolve()}
 };
 await page.route('**/api/actions',handler);
 return {
  arrived:arrived.promise,
  async release(){clearTimeout(timer);release.resolve();if(captured)await finished.promise;await page.unroute('**/api/actions',handler)},
 };
}
async function mutate(page,operation,entry){
 if(operation==='delete'){
  await button(page,'Delete '+entry.name).click();
  await expect(page.getByRole('group',{name:'Delete appearance confirmation'})).toBeVisible();
  await button(page,'Confirm delete').click();
 }else await button(page,operation==='save'?'Save as copy':'Update saved appearance').click();
}

try{
 const url=await new Promise((resolve,reject)=>{
  let output='';
  const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const options={viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}};
 const page=await browser.newPage(options),peer=await browser.newPage(options),errors=[],requests=[];
 page.on('request',request=>{if(request.url().endsWith('/api/actions'))requests.push(request.postDataJSON()?.action)});
 for(const client of [page,peer]){client.on('pageerror',error=>errors.push(error.message));await client.goto(url);await showFixtureConversation(client)}
 await action(page,'session.create');
 await openSettingsPage(page,'custom-appearance');

 for(const operation of ['save','update','delete']){
  const entry=await seed(page,'Held '+operation);
  await expect(button(page,'Preview '+entry.name)).toBeVisible({timeout:12000});
  await select(page,entry);
  const payload={name:'Submitted '+operation,description:'Submitted description',css:'#amp-one{--a-accent:#765abc}'};
  await fill(page,payload);
  const before=requests.filter(name=>name==='theme.'+operation).length;
  held=await holdResponse(page,'theme.'+operation);
  await mutate(page,operation,entry);
  const receipt=await held.arrived;
  await expect(field(page,'name')).toBeDisabled();
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
  await openSettingsPage(page,'custom-appearance');
  await openEditor(page);
  await expect(field(page,'name')).toBeEnabled();
  const successor={name:'Successor '+operation,description:'Keep newer description',css:'#amp-one{--a-accent:#8844aa}'};
  await fill(page,successor);
  const expected=await stored(page);
  await held.release();held=null;
  // Drain the real command queue before asserting: a pre-response assertion
  // could otherwise pass before the defective completion actually runs.
  const items=await list(page);
  assert.deepEqual(await stored(page),expected,'Late '+operation+' must preserve the entire newer draft and original target');
  for(const [id,value] of Object.entries(successor))await expect(field(page,id)).toHaveValue(value);
  await expect(field(page,'name')).toBeEnabled();
  assert.equal(requests.filter(name=>name==='theme.'+operation).length,before+1,'No mutation replay on remount');
  if(operation==='delete')assert.ok(!items.some(item=>item.id===entry.id),'The real confirmed deletion stands');
  else{
   const saved=(await action(page,'theme.read',{id:receipt.result.id})).result;
   assert.equal(saved.name,payload.name);assert.equal(saved.description,payload.description);
   assert.ok(saved.css.endsWith(payload.css),'The submitted result remains in the real library');
  }
 }

 for(const operation of ['save','update','delete']){
  const entry=await seed(page,'Failed '+operation);
  await expect(button(page,'Preview '+entry.name)).toBeVisible({timeout:12000});
  await select(page,entry);
  await fill(page,{name:operation==='save'?'':'Retain failed '+operation,description:'Retain failure draft',css:'#amp-one{--a-accent:#998833}'});
  // A blank save name and stale update/delete fingerprints are real host
  // rejections. Neither may clear intent or leave the editor disabled.
  if(operation!=='save')await action(peer,'theme.update',{id:entry.id,expectedFingerprint:entry.fingerprint,name:'Changed by peer '+operation,description:'Peer update',css:'#amp-one{--a-accent:#334499}'});
  const expected=await stored(page),before=requests.filter(name=>name==='theme.'+operation).length;
  const response=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.'+operation);
  await mutate(page,operation,entry);
  assert.ok(!(await response).ok(),'The host must reject this mutation');
  await list(page);
  await expect(field(page,'name')).toBeEnabled();
  await expect(page.getByRole('alert').filter({hasText:operation==='save'?/name|not confirmed/i:/changed|not confirmed/i}).first()).toBeVisible();
  assert.deepEqual(await stored(page),expected,'Rejected '+operation+' cannot settle the draft as saved/deleted');
  assert.equal(requests.filter(name=>name==='theme.'+operation).length,before+1,'A rejection cannot retry itself');
 }
 assert.deepEqual(errors,[]);
 console.log('Appearance late-completion passed: 3 held real save/update/delete responses with close/remount/newer fields; actual library results retained; 3 real host failures preserve intent and settle locks; no automatic mutation replay.');
}catch(error){failure=error;throw error}
finally{
 if(held)await held.release().catch(()=>{});
 await finishFixture({label:'Appearance late-completion fixture',close:[['Appearance late-completion browser',()=>browser?.close()]],stop,originalError:failure});
}