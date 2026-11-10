import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {expect} from '@playwright/test';

// Only the seven Canvas-adjacent journeys use this prerequisite. It never
// performs their input, marks a view dirty, replaces a renderer or changes CSS.
export async function prepareCanvasReaderInput(page,{target,label,outputDir,expectCovered=false}){
 assert.match(label,/^[a-z0-9-]+$/);
 await mkdir(outputDir,{recursive:true});
 const report={label,status:'running'},path=join(outputDir,label+'-reader-input');
 const primary=page.locator('[data-canvas-view="primary"]'),details=page.getByRole('region',{name:'Canvas details',exact:true});
 let originalPrimary,originalTarget,failure;
 const snapshot=async()=>({
  ...await primary.evaluate(el=>{
   const panel=el.closest('[data-part="canvas"]'),reader=el.closest('.a-canvas-reader'),sheet=panel.querySelector('#canvas-options');
   const rect=node=>{const {x,y,width,height}=node.getBoundingClientRect();return {x,y,width,height}};
   const view=window.amplifier.getState().canvasWorkspace?.views?.find(row=>row.viewId==='primary');
   return {panel:rect(panel),reader:rect(reader),readerInert:!!el.closest('[inert]'),
    detailsOpen:!sheet.hidden&&sheet.getBoundingClientRect().height>0,overlay:panel.querySelector('.a-canvas-content').dataset.detailsOverlay==='true',
    lease:view?Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]])):null,
    activation:view?.activation?.status??null,preview:view?.renderReports?.preview?.status??null};
  }),
  input:{box:await target.boundingBox(),...await target.evaluate(el=>({tag:el.tagName,label:el.getAttribute('aria-label'),id:el.id,mount:el.getAttribute('data-mount')}))}
 });
 const assertReady=sample=>assert.equal(sample.readerInert,false,'CANVAS_READER_INPUT reader is inert; close Details before input');
 try{
  await target.waitFor({state:'attached'});
  originalPrimary=await primary.elementHandle();originalTarget=await target.elementHandle();
  assert.ok(originalPrimary&&originalTarget,'Retain actual renderer/input DOM identities');
  await expect.poll(()=>primary.evaluate(()=>!!window.amplifier.getState().canvasWorkspace?.views?.some(row=>row.viewId==='primary'&&row.resourceId&&Number.isInteger(row.generation)))).toBe(true);
  report.before=await snapshot();
  await page.screenshot({path:path+'-before.png'});
  if(expectCovered){
   assert.ok(report.before.panel.width<720&&report.before.overlay&&report.before.detailsOpen&&report.before.readerInert,
    'Original narrow setup must expose the covered-reader prerequisite');
  }
  if(report.before.readerInert){
   // A measured baseline, not a fake event or a manufactured dirty state.
   assert.throws(()=>assertReady(report.before),/CANVAS_READER_INPUT reader is inert/);
   report.coveredPrerequisiteRejected=true;
  }
  if(report.before.detailsOpen){
   await details.getByRole('button',{name:'Close canvas details',exact:true}).click();
   await expect(details).toBeHidden();
  }
  await expect.poll(()=>primary.evaluate(el=>!!el.closest('[inert]'))).toBe(false);
  report.after=await snapshot();
  report.sameRenderer=await primary.evaluate((el,old)=>el===old,originalPrimary);
  report.sameInput=await target.evaluate((el,old)=>el===old,originalTarget);
  assert.equal(report.sameRenderer,true,'Closing Details keeps the same renderer root');
  assert.equal(report.sameInput,true,'Closing Details keeps the same mounted input/document');
  assertReady(report.after);
  assert.deepEqual(report.after.lease,report.before.lease,'Closing Details must not replace the primary-view lease');
  report.sameRenderer=true;report.sameInput=true;report.status='passed';
  await page.screenshot({path:path+'-after.png'});
 }catch(error){failure=await recordCanvasInputFailure(page,error,{outputDir,label});report.status='failed';report.error=failure.stack}
 finally{
  try{await writeFile(path+'.json',JSON.stringify(report,null,2)+'\n')}catch(error){failure=failure?new AggregateError([failure,error],'Reader prerequisite and evidence failure'):error}
  await originalTarget?.dispose();await originalPrimary?.dispose();
 }
 if(failure)throw failure;
 return report;
}

// Retain an input failure before its existing fixture/context cleanup. Each
// diagnostic is bounded; failures add evidence errors without replacing cause.
export async function recordCanvasInputFailure(page,error,{outputDir,label}){
 const report={error:error.stack},secondary=[];
 const bounded=async(work,ms)=>{let timer;try{return await Promise.race([work,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Canvas failure diagnostic deadline')),ms)})])}finally{clearTimeout(timer)}};
 try{
  await mkdir(outputDir,{recursive:true});
  if(page&&!page.isClosed()){
   try{report.canvas=await bounded(page.evaluate(()=>{
    const state=window.amplifier?.getState(),panel=document.querySelector('#workspace-canvas');
    const box=el=>{if(!el)return null;const {x,y,width,height}=el.getBoundingClientRect();return {x,y,width,height}};
    return {selectedSessionId:state?.selectedSessionId,panel:box(panel),reader:box(panel?.querySelector('.a-canvas-reader')),
     readerInert:panel?.querySelector('.a-canvas-reader')?.inert,detailsHidden:panel?.querySelector('#canvas-options')?.hidden,
     views:state?.canvasWorkspace?.views?.map(v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation','dirty'].map(k=>[k,v[k]])))};
   }),2000)}catch(e){secondary.push(e)}
   try{await page.screenshot({path:join(outputDir,label+'-failure.png'),timeout:5000})}catch(e){secondary.push(e)}
  }
  report.diagnosticErrors=secondary.map(e=>e.stack);
  await writeFile(join(outputDir,label+'-failure.json'),JSON.stringify(report,null,2)+'\n');
 }catch(e){secondary.push(e)}
 return secondary.length?new AggregateError([error,...secondary],'Canvas input failure with diagnostic failures',{cause:error}):error;
}
