// Frozen-baseline route/control discovery, with actual UI actions and partial
// assertion records. This is deliberately not an automatic full-workflow pass.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';

const source=fileURLToPath(new URL('../../',import.meta.url));
const out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Set an owned AMPLIFIER_TEST_ARTIFACTS directory.');
await mkdir(out,{recursive:true});
const inventory=JSON.parse(await readFile(source+'frontend/tests/standard-ux-inventory.json','utf8'));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||source+'.venv/bin/python',['-u',source+'tests/fixtures/standard_ux_server.py'],{stdio:['ignore','pipe','pipe']});
let logs='',browser,primary;
fixture.stderr.on('data',chunk=>logs+=chunk);
const recorder=new StandardUxEvidence(out,{baseline:inventory.baseline,inventoryVersion:1,data:'synthetic-standard',runtime:'fixture Runtime; no real provider',timingQualification:'observational-shared-DTU-not-performance-baseline',sourceReceipt:process.env.AMPLIFIER_SOURCE_RECEIPT||null});
const pages=[
 ['ai','ai-connections',['settings.destinations','connections.catalog']],
 ['smart-tools','smart-tools',['settings.destinations','smart-tools.catalog']],
 ['appearance','appearance',['settings.destinations','appearance.mode']],
 ['workspaces','workspaces',['settings.destinations','create.workspace-new']],
 ['voice','voice',['settings.destinations','voice.setup']],
 ['notifications','notifications',['settings.destinations','notifications.badge']],
 ['privacy','privacy',['settings.destinations','privacy.links']],
 ['updates','updates',['settings.destinations','updates.check']],
];
const children=[['workspaces','workspace-starters','starters.browse'],['privacy','storage','storage.inventory'],['privacy','permissions','privacy.file-policy'],['privacy','desktop','voice.screen'],['privacy','history','privacy.import'],['privacy','diagnostics','privacy.capture'],['privacy','repair','privacy.backup']];
async function activate(page,locator,label,mode) {
 await recorder.action(label,async()=>{
  await expect(locator).toBeVisible();await expect(locator).toBeEnabled();
  if(mode==='keyboard'){await locator.focus();await expect(locator).toBeFocused();await page.keyboard.press('Enter');}
  else if(mode==='touch')await locator.tap();else await locator.click();
 });
}
async function settings(page,mode) {
 if(await page.locator('.a-settings-experience').isVisible())return;
 const direct=page.getByRole('button',{name:/^Settings(?: \d+ unread items)?$/});
 if(!await direct.isVisible())await activate(page,page.getByRole('button',{name:'Open navigation',exact:true}),'Open navigation',mode);
 await activate(page,direct,'Open Settings from navigation footer',mode);
 await expect(page.locator('.a-settings-experience')).toBeVisible();
}
async function section(page,id,mode) {
 await settings(page,mode);
 const root=page.locator('.a-settings-experience');
 await page.waitForFunction(()=>{const el=document.querySelector('.a-settings-experience');return el&&el.dataset.compact===String((el.closest('.a-overlay')||el).clientWidth<960)});
 for(let depth=0;await root.getAttribute('data-compact')==='true'&&await root.getAttribute('data-settings-index')!=='true';depth++){
  if(depth>=8)throw Error('Settings Back did not reach its index.');
  const previous=await root.getAttribute('data-settings-route');
  await activate(page,root.locator('.a-settings-mobile-head button').first(),'Back toward Settings index',mode);
  await expect(root).not.toHaveAttribute('data-settings-route',previous);
 }
 await activate(page,root.locator(`[data-settings-section="${id}"]`),'Open Settings section '+id,mode);
 await expect(root.locator('.a-settings-page-content:not([hidden])')).toBeVisible();
}
async function expand(page,mode) {
 const opened=[];
 for(let attempt=0;attempt<512;attempt++){
  const summaries=page.locator('.a-settings-page-content:not([hidden]) details:not([open]) > summary');
  let target;
  for(let i=0;i<await summaries.count();i++)if(await summaries.nth(i).isVisible()){target=summaries.nth(i);break;}
  if(!target)return opened;
  const label=(await target.innerText()).trim();
  // Raw authoring/recovery groups have separate boundary checks, not incidental execution.
  if(/Advanced customization|Advanced recovery/.test(label))return opened;
  await activate(page,target,'Expand '+label,mode);opened.push(label);
 }
 throw Error('Disclosure discovery exceeded512; inspect recursive/dynamic controls.');
}
async function capture(page,id) {
 const root=page.locator('.a-settings-experience');
 recorder.assertion('Active Settings content is visible',await root.locator('.a-settings-page-content:not([hidden])').isVisible());
 recorder.assertion('Dialog has no horizontal overflow',await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
 recorder.assertion('Settings content has no horizontal overflow',await root.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
 return recorder.snapshot(page,id,'.a-settings-experience');
}
try {
 const port=await new Promise((resolve,reject)=>{
  let buffer='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout: '+logs.slice(-2000))),20000);
  fixture.stdout.on('data',chunk=>{buffer+=chunk;for(const line of buffer.split('\n'))try{const value=JSON.parse(line);if(value.port){clearTimeout(timer);resolve(value.port)}}catch{}});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+logs.slice(-2000)))});
 });
 for(const variant of [{id:'desktop-pointer',width:1366,height:900,mode:'pointer'},{id:'narrow-touch',width:390,height:844,mode:'touch'},{id:'desktop-keyboard',width:1366,height:900,mode:'keyboard'},{id:'narrow-keyboard',width:390,height:844,mode:'keyboard'}].filter(item=>!process.env.STANDARD_UX_VARIANTS||process.env.STANDARD_UX_VARIANTS.split(',').includes(item.id))) {
  browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
  const context=await browser.newContext({viewport:{width:variant.width,height:variant.height},hasTouch:variant.mode==='touch',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  const page=await context.newPage(),errors=[],requests=[];recorder.page=page;page.setDefaultTimeout(5000);
  page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')requests.push(request.postDataJSON()?.action)});
  try {
   await recorder.probe(variant.id+'.entry',['entry.boot'],async()=>{
    await recorder.action('Open authenticated synthetic app',()=>page.goto(`http://127.0.0.1:${port}/`));
    await expect(page.locator('#amp-one')).toBeVisible();await page.waitForFunction(()=>window.amplifier?.getState());
    const response=await page.request.get(`http://127.0.0.1:${port}/build.json`);recorder.assertion('Authenticated build metadata readable',response.ok());
    return {variant,build:await response.json()};
   });
   for(const [sectionId,pageId,ids] of pages.filter(row=>!process.env.STANDARD_UX_ROUTES||process.env.STANDARD_UX_ROUTES.split(',').includes(row[1])))await recorder.probe(variant.id+'.settings.'+pageId,ids,async()=>{
    await section(page,sectionId,variant.mode);await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page',pageId);
    const opened=await expand(page,variant.mode);return {variant,opened,...await capture(page,variant.id+'.settings.'+pageId)};
   });
   for(const [parent,destination,id] of children.filter(row=>!process.env.STANDARD_UX_ROUTES||process.env.STANDARD_UX_ROUTES.split(',').includes(row[1])))await recorder.probe(variant.id+'.settings.'+destination,['settings.destinations',id],async()=>{
    await section(page,parent,variant.mode);
    const link=destination==='workspace-starters'?page.getByRole('button',{name:'Manage starters →',exact:true}):page.locator(`.a-settings-page-content:not([hidden]) [data-settings-destination="${destination}"]`);
    await activate(page,link,'Open '+destination+' through '+parent,variant.mode);
    await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page',destination);
    const opened=await expand(page,variant.mode);return {variant,opened,...await capture(page,variant.id+'.settings.'+destination)};
   });
   await recorder.probe(variant.id+'.settings.advanced-boundary',['settings.destinations'],async()=>{
    await section(page,'advanced',variant.mode);await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page','advanced');
    return {variant,exclusion:'Advanced-only destinations are recorded, not activated.',...await capture(page,variant.id+'.settings.advanced-boundary')};
   });
   await recorder.probe(variant.id+'.settings.close',['settings.narrow','accessibility.keyboard'],async()=>{
    const close=page.locator('button[aria-label="Close settings"]:visible,button[aria-label="Close panel"]:visible').last();
    await activate(page,close,'Close Settings using visible control',variant.mode);await expect(page.locator('.a-settings-experience')).toHaveCount(0);
    recorder.assertion('No browser errors',errors,[]);
    recorder.assertion('No model send while discovering controls',requests.filter(x=>['conversation.send','session.send'].includes(x)),[]);
    return {variant,observedActions:requests};
   });
  } finally { await context.close(); await browser.close(); browser=null; }
 }
 await recorder.save();console.log(JSON.stringify({probes:recorder.probes.length,failed:recorder.probes.filter(x=>x.status==='failed').length,completeWorkflows:0}));
 if(recorder.probes.some(x=>x.status==='failed'))process.exitCode=1;
}catch(error){primary=error;throw error;}
finally {
 await browser?.close();
 if(fixture.exitCode===null){const exit=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exit;}finally{clearTimeout(timer)}}
 await writeFile(out+'/fixture.log',logs);await recorder.save();
}
