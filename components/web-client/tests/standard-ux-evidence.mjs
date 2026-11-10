import {mkdir, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';

// A probe records only its own assertions. It never promotes a source workflow
// or a discovered control to complete merely because a page was visited.
export class StandardUxEvidence {
 constructor(directory, context) { this.directory=directory; this.context=context; this.probes=[]; this.actions=[]; this.assertions=[]; }
 async action(label, operation) {
  const start=performance.now();
  try { const value=await operation(); this.actions.push({label,status:'completed',durationMs:performance.now()-start}); return value; }
  catch(error) { this.actions.push({label,status:'failed',durationMs:performance.now()-start,error:error.message}); throw error; }
 }
 assertion(label, observed, expected=true) {
  const passed=JSON.stringify(observed)===JSON.stringify(expected);
  this.assertions.push({label,observed,expected,passed});
  if(!passed)throw Error(label+': '+JSON.stringify({observed,expected}));
 }
 async probe(id, workflowIds, operation) {
  this.actions=[];this.assertions=[];const start=performance.now();
  const record={id,workflowIds,coverage:'partial-explicit-assertions-only',startedAt:new Date().toISOString(),status:'running'};
  try { Object.assign(record,await operation()||{});record.status='passed'; }
  catch(error) {
   record.status='failed';record.error=error.message;
   if(this.page)try {record.failureScreenshot=id.replace(/[^a-zA-Z0-9_.-]/g,'-')+'-failed.png';await this.page.screenshot({path:this.directory+'/'+record.failureScreenshot,animations:'disabled'});record.failureAria=await this.page.locator('body').ariaSnapshot();}catch{}
  }
  record.durationMs=performance.now()-start;record.actions=this.actions;record.assertions=this.assertions;this.probes.push(record);
  await this.save();return record;
 }
 async controls(page, scope) {
  return page.locator(scope).evaluate(root=>{
   const visible=el=>!!el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden'&&!el.closest('[hidden],[inert]');
   return [...root.querySelectorAll('button,a[href],input,select,textarea,summary,[role="button"],[role="tab"],[role="menuitem"],[contenteditable="true"]')].filter(visible).map((el,index)=>({
    index,tag:el.tagName.toLowerCase(),role:el.getAttribute('role'),id:el.id||null,type:el.getAttribute('type'),
    name:el.getAttribute('aria-label')||[...el.labels||[]].map(label=>label.textContent.trim()).join(' ')||el.getAttribute('title')||(el.innerText||'').trim().slice(0,300),
    action:el.getAttribute('data-action'),destination:el.getAttribute('data-settings-destination'),disabled:el.matches(':disabled')||el.getAttribute('aria-disabled')==='true',
    expanded:el.getAttribute('aria-expanded'),checked:el.matches('input[type=checkbox],input[type=radio]')?el.checked:null,
    options:el.tagName==='SELECT'?[...el.options].map(option=>({value:option.value,label:option.label,disabled:option.disabled})):undefined,
    rect:{width:Math.round(el.getBoundingClientRect().width),height:Math.round(el.getBoundingClientRect().height)},
   }));
  });
 }
 async snapshot(page,id,scope) {
  const controls=await this.controls(page,scope);
  for(const control of controls)control.instanceId=id+':'+createHash('sha256').update(JSON.stringify([control.tag,control.role,control.id,control.name,control.action,control.destination,control.index])).digest('hex').slice(0,12);
  const screenshot=id.replace(/[^a-zA-Z0-9_.-]/g,'-')+'.png';
  await page.screenshot({path:this.directory+'/'+screenshot,animations:'disabled'});
  return {controls,screenshot,aria:await page.locator(scope).ariaSnapshot()};
 }
 async save() {
  await mkdir(this.directory,{recursive:true});
  const result={schemaVersion:1,context:this.context,summary:{probes:this.probes.length,passed:this.probes.filter(x=>x.status==='passed').length,failed:this.probes.filter(x=>x.status==='failed').length,completeWorkflows:0},probes:this.probes};
  await writeFile(this.directory+'/observations.json',JSON.stringify(result,null,2)+'\n');
 }
}
