import {showFixtureConversation} from './browser-chat-pages.mjs';
import {prepareCanvasReaderInput} from './canvas-reader-input.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync} from 'node:fs';
import {dirname,join} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
if(process.env.DIAGRAM_EVIDENCE)mkdirSync(dirname(process.env.DIAGRAM_EVIDENCE),{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Diagram fixture'});
let browser,page,primary,fixtureExit;const results=[],errors=[],panFacts=[],readyLayouts=[],secondary=[];
async function readyLayout(artifact,kind,content,{undersize=false}={}){
 // A decoded image can precede its render receipt and the feedback portal's
 // layout change. Bind readiness to this artifact's exact primary-view lease.
 let target;
 await expect.poll(async()=>{
  target=await page.evaluate(({id,kind,content})=>{
   const state=window.amplifier.getState(),view=state.canvasWorkspace?.views?.find(row=>row.viewId==='primary');
   return state.canvas?.id===id&&state.canvas.kind===kind&&state.canvas.content===content&&view?.resourceId===id
    ?Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]])):null;
  },{id:artifact.id,kind,content});
  return !!target?.resourceRevision&&Number.isInteger(target?.generation);
 }).toBe(true);
 await expect.poll(()=>page.evaluate(target=>{
  const view=window.amplifier.getState().canvasWorkspace?.views?.find(row=>row.viewId===target.viewId);
  return Object.entries(target).every(([key,value])=>view?.[key]===value)&&
   view.activation?.status==='ready'&&view.renderReports?.preview?.status==='ready';
 },target)).toBe(true);
 const stage=page.locator('[data-canvas-view="primary"] .a-diagram-stage');
 await expect(stage.locator('img')).toBeVisible();
 if(undersize)await stage.evaluate(el=>{
  el.style.setProperty('flex','0 0 80px','important');
  for(const name of ['height','min-height','max-height'])el.style.setProperty(name,'80px','important');
 });
 let layout;
 await expect.poll(async()=>{
  layout=await page.evaluate(async({target,kind,content})=>{
   const sample=()=>{
    const state=window.amplifier.getState(),view=state.canvasWorkspace?.views?.find(row=>row.viewId===target.viewId);
    const stage=document.querySelector('[data-canvas-view="primary"] .a-diagram-stage'),image=stage?.querySelector('img');
    const panel=stage?.closest('[data-part="canvas"]'),viewer=stage?.closest('.a-canvas-viewer');
    if(state.canvas?.id!==target.resourceId||state.canvas.kind!==kind||state.canvas.content!==content||
     !Object.entries(target).every(([key,value])=>view?.[key]===value)||
     view.activation?.status!=='ready'||view.renderReports?.preview?.status!=='ready'||
     !image?.complete||!image.naturalWidth||getComputedStyle(image).visibility!=='visible'||!viewer||
     panel.querySelector('.a-canvas-result:not(.success)'))return null;
    const rect=node=>{const {x,y,width,height}=node.getBoundingClientRect();return {x,y,width,height}};
    return {box:rect(stage),picture:rect(image),body:rect(viewer),src:image.src};
   };
   let previous=sample();
   for(let frame=0;frame<3;frame++){
    await new Promise(resolve=>requestAnimationFrame(resolve));
    const current=sample();
    if(!previous||!current||JSON.stringify(previous)!==JSON.stringify(current))return null;
    previous=current;
   }
   const {src,...geometry}=previous;return geometry;
  },{target,kind,content});
  return !!layout;
 }).toBe(true);
 // Stability is independent of acceptance: a stable undersized stage must fail
 // the same fit checks, not be polled until it happens to satisfy them.
 readyLayouts.push({kind,target,undersize,...layout});
 return layout;
}
async function recordPan(kind,phase,assertionImageBox=null){
 const sample={kind,phase,assertionImageBox};panFacts.push(sample);let timer;
 try{
  assert.ok(panFacts.length<=7,'At most three pan boundaries per kind and one failure boundary');
  sample.facts=await Promise.race([page.evaluate(()=>{
   const state=window.amplifier?.getState(),canvas=state?.canvas;
   const view=state?.canvasWorkspace?.views?.find(row=>row.viewId==='primary');
   const stage=document.querySelector('[data-canvas-view="primary"] .a-diagram-stage');
   const image=stage?.querySelector('img');
   const scalar=value=>typeof value==='string'&&value.length<=200?value:null;
   const number=value=>Number.isFinite(value)?value:null;
   const rect=node=>{if(!node)return null;const b=node.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height}};
   const style=image?getComputedStyle(image):null;
   const matrix=style&&style.transform!=='none'?new DOMMatrixReadOnly(style.transform):null;
   return {at:performance.now(),
    artifact:{id:scalar(canvas?.id),kind:scalar(canvas?.kind),selectedVersion:number(canvas?.selectedVersion),
     resourceId:scalar(view?.resourceId),resourceRevision:scalar(view?.resourceRevision),generation:number(view?.generation)},
    state:{viewStatus:scalar(view?.activation?.status),previewStatus:scalar(view?.renderReports?.preview?.status),
     canvasPreviewStatus:scalar(canvas?.renderReports?.preview?.status),
     savedPanX:number(view?.view?.panX),savedPanY:number(view?.view?.panY),savedZoom:number(view?.view?.zoom)},
    stage:{box:rect(stage),clientWidth:stage?.clientWidth??null,clientHeight:stage?.clientHeight??null,
     scrollLeft:stage?.scrollLeft??null,scrollTop:stage?.scrollTop??null},
    image:{box:rect(image),complete:image?.complete??null,naturalWidth:image?.naturalWidth??null,
     naturalHeight:image?.naturalHeight??null,offsetWidth:image?.offsetWidth??null,offsetHeight:image?.offsetHeight??null,
     cssWidth:scalar(style?.width),cssHeight:scalar(style?.height),visibility:scalar(style?.visibility),
     transform:scalar(style?.transform),transformOrigin:scalar(style?.transformOrigin),
     matrix:matrix?{a:matrix.a,b:matrix.b,c:matrix.c,d:matrix.d,e:matrix.e,f:matrix.f}:null},
    layout:{viewer:rect(stage?.closest('.a-canvas-viewer')),preview:rect(stage?.closest('.a-canvas-preview')),
     panel:rect(stage?.closest('[data-part="canvas"]')),viewport:{width:innerWidth,height:innerHeight},
     resultBox:rect(document.querySelector('[data-part="canvas"] .a-canvas-result[role="status"]')),
     resultPending:document.querySelectorAll('[data-part="canvas"] .a-canvas-result:not(.success):not(.error)').length,
     resultSuccess:document.querySelectorAll('[data-part="canvas"] .a-canvas-result.success').length,
     resultError:document.querySelectorAll('[data-part="canvas"] .a-canvas-result.error').length}};
  }),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Pan diagnostic observation timed out')),2000)})]);
 }catch(error){sample.error={name:error.name,code:error.code??null};secondary.push(error)}
 finally{clearTimeout(timer)}
}
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1400,height:1100},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 const commands=[];page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){const body=request.postDataJSON();commands.push(body)}});
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const saved=()=>page.evaluate(()=>window.amplifier.getState().canvas.view||{});
 const record=(kind,name,passed,measurements={})=>results.push({kind,name,passed,...measurements});
 await page.goto(url);await showFixtureConversation(page);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await act('session.create',{});await act('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,canvasWidth:700}});
 for(const kind of ['mermaid','dot']){
  const content=kind==='mermaid'?'flowchart TD\n'+Array.from({length:10},(_,i)=>`N${i}[Step ${i}] --> N${i+1}[Step ${i+1}]`).join('\n'):'digraph { '+Array.from({length:10},(_,i)=>`n${i}->n${i+1};`).join(' ')+' }';
  const artifact=(await act('canvas.show',{kind,content,title:kind+' navigation'})).result;
  const stage=page.locator('[data-canvas-view="primary"] .a-diagram-stage'),img=stage.locator('img');
  await expect(img).toBeVisible();
  await prepareCanvasReaderInput(page,{target:stage,label:kind+'-pan',
   outputDir:process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/browser-checks/diagram-navigation',expectCovered:kind==='mermaid'});
  const undersize=process.env.DIAGRAM_UNDERSIZED_STAGE==='1';
  const {box,picture,body}=await readyLayout(artifact,kind,content,{undersize});
  record(kind,'fit uses available pane height',box.height>=body.height*.75,{stageHeight:box.height,paneHeight:body.height});
  record(kind,'fit retains complete tall graph',picture.height<=box.height+1&&picture.width<=box.width+1&&picture.height>=box.height*.8,{imageHeight:picture.height});
  if(undersize)assert.ok(results.every(row=>row.passed),JSON.stringify(results.filter(row=>!row.passed)));
  const x=box.x+box.width*.65,y=box.y+box.height*.6;
  const before=await img.boundingBox(),requestStart=commands.length;
  await recordPan(kind,'baseline',before);
  await page.mouse.move(x,y);await page.mouse.down();await recordPan(kind,'pointer-down');
  await page.mouse.move(x+80,y+45,{steps:8});
  const during=await img.boundingBox();
  await recordPan(kind,'during-drag',during);
  record(kind,'pan follows pointer before release',Math.abs((during.x-before.x)-80)<2&&Math.abs((during.y-before.y)-45)<2,{deltaX:during.x-before.x,deltaY:during.y-before.y});
  record(kind,'pan does not send per-move commands',commands.length===requestStart,{commandsDuringDrag:commands.length-requestStart});
  await page.mouse.up();await expect.poll(async()=>Math.round((await saved()).panX||0)).toBe(80);
  await page.getByRole('button',{name:'Fit',exact:true}).click();await expect.poll(async()=>Math.round((await saved()).panX||0)).toBe(0);
  const initial=await img.boundingBox();
  await page.mouse.move(x,y);const wheelStart=commands.length;
  await page.mouse.wheel(0,-250);
  // A frame is sufficient for local feedback; durable wheel state is debounced.
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const zoomed=await img.boundingBox();const scale=zoomed.width/initial.width;
  record(kind,'wheel zooms around pointer',scale>1.05&&Math.abs((zoomed.x+(x-initial.x)*scale)-x)<3&&Math.abs((zoomed.y+(y-initial.y)*scale)-y)<3,{scale});
  // The gesture paints before its debounced save is acknowledged. Capture the
  // reload baseline only after the real view contains that exact wheel patch.
  let wheel;
  await expect.poll(()=>{
   wheel=commands.slice(wheelStart).find(row=>row.action==='canvas.views.command'&&
    row.args.action==='canvas.view'&&row.args.args.patch?.zoom>1);
   return !!wheel;
  }).toBe(true);
  await expect.poll(saved).toMatchObject(wheel.args.args.patch);
  record(kind,'wheel persistence is bounded',commands.length-wheelStart<=2,{commands:commands.length-wheelStart});
  const retained=await saved();await page.reload();await readyLayout(artifact,kind,content);
  await expect.poll(async()=>(await saved()).zoom).toBe(retained.zoom);
  record(kind,'reload retains zoom and pan',(await saved()).panX===retained.panX&&(await saved()).panY===retained.panY);
  const keyBefore=(await saved()).panX||0;await stage.focus();await page.keyboard.press('ArrowRight');
  await expect.poll(async()=>(await saved()).panX).toBe(keyBefore+30);
  const keyboard=await saved();record(kind,'keyboard pan persists',Number.isFinite(keyboard.panX)&&keyboard.panX!==0);
  await page.getByRole('button',{name:'Fit',exact:true}).click();
  await page.setViewportSize({width:390,height:844});await act('view.update',{patch:{canvasFocused:true}});
  await readyLayout(artifact,kind,content);
  await expect.poll(async()=>{const b=await stage.boundingBox(),i=await img.boundingBox();return i.width<=b.width+1&&i.height<=b.height+1}).toBe(true);
  record(kind,'narrow resize refits complete graph',true);
  const touchBox=await stage.boundingBox(),touchBefore=await img.boundingBox(),cdp=await page.context().newCDPSession(page);
  const tx=touchBox.x+touchBox.width/2,ty=touchBox.y+touchBox.height/2;
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:tx,y:ty}]});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:tx+40,y:ty+30}]});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const touchDuring=await img.boundingBox();
  record(kind,'touch pan follows finger before release',Math.abs(touchDuring.x-touchBefore.x-40)<2&&Math.abs(touchDuring.y-touchBefore.y-30)<2);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await cdp.detach();
  await expect.poll(async()=>Math.round((await saved()).panX)).toBe(40);
  const cancelledStart=await img.boundingBox(),cancelCommands=commands.length,abort=await page.context().newCDPSession(page);
  await abort.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:tx,y:ty}]});
  await abort.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:tx+35,y:ty+25}]});
  await abort.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});await abort.detach();
  await expect.poll(async()=>Math.round((await img.boundingBox()).x-cancelledStart.x)).toBe(0);
  record(kind,'cancelled touch restores saved view without committing partial pan',(await saved()).panX===40&&commands.length===cancelCommands);
 await page.setViewportSize({width:1400,height:1100});await act('view.update',{patch:{canvasFocused:false}});
 }
 // Viewer controls stay available without opening the general Canvas options.
 await act('view.update',{patch:{canvasControlsPinned:false,canvasControlsExpanded:false}});
 const authored='digraph { rankdir=LR; a [label="Authored node",shape=diamond,style=filled,fillcolor="#ffcc88",color="#713600"]; a -> b [color="#07802a"]; }';
 const authoredArtifact=(await act('canvas.show',{kind:'dot',content:authored,title:'Diagram presentation'})).result;
 await readyLayout(authoredArtifact,'dot',authored);
 const graph=page.locator('.a-diagram-stage img');await expect(graph).toBeVisible();
 await expect(page.getByRole('group',{name:'Diagram view'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Zoom in',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Fit',exact:true})).toBeVisible();
 const svg=await graph.evaluate(img=>decodeURIComponent(img.src.split(',').slice(1).join(',')));
 assert.match(svg,/#ffcc88/);assert.match(svg,/#713600/);assert.match(svg,/#07802a/);
 await expect(page.getByRole('combobox',{name:'Inspect graph node'})).toBeHidden();
 await page.getByText(/^Node details ·/).click();
 await page.getByRole('combobox',{name:'Inspect graph node'}).selectOption('a');
 await expect(page.locator('.a-node-inspector dl')).toContainText('Authored node');
 const flowSource=await graph.getAttribute('src');
 await page.getByRole('combobox',{name:'Graph layout'}).selectOption('neato');
 await expect.poll(async()=>(await saved()).engine).toBe('neato');
 await expect(graph).toBeVisible();
 await expect(graph).not.toHaveAttribute('src',flowSource);
 await readyLayout(authoredArtifact,'dot',authored);
 const separated=await graph.evaluate(img=>{
  const svg=new DOMParser().parseFromString(decodeURIComponent(img.src.split(',').slice(1).join(',')),'image/svg+xml').documentElement;
  svg.style.cssText='position:fixed;visibility:hidden;pointer-events:none';document.body.append(svg);
  try{const [a,b]=[...svg.querySelectorAll('g.node')].map(node=>node.getBBox());return a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y}finally{svg.remove()}
 });
 assert.ok(separated,'Network layout should separate default node bounds');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.content),authored);
 record('dot','visible controls, authored styling/source and node inspection preserved',true);
 if(process.env.DIAGRAM_EVIDENCE)await page.screenshot({path:join(dirname(process.env.DIAGRAM_EVIDENCE),'diagram-viewer-desktop.png')});
 await page.setViewportSize({width:390,height:844});await act('view.update',{patch:{canvasFocused:true}});
 await readyLayout(authoredArtifact,'dot',authored);
 const controls=await page.getByRole('group',{name:'Diagram view'}).boundingBox();assert.ok(controls.x>=0&&controls.x+controls.width<=390);
 record('dot','narrow toolbar stays within viewport',true);
 if(process.env.DIAGRAM_EVIDENCE)await page.screenshot({path:join(dirname(process.env.DIAGRAM_EVIDENCE),'diagram-viewer-narrow.png')});
 const mermaid='flowchart LR\n A[Authored Mermaid] --> B[Output]\n style A fill:#ffcc88,stroke:#713600';
 const mermaidArtifact=(await act('canvas.show',{kind:'mermaid',content:mermaid,title:'Authored Mermaid'})).result;
 await readyLayout(mermaidArtifact,'mermaid',mermaid);
 const msvg=await graph.evaluate(img=>decodeURIComponent(img.src.split(',').slice(1).join(',')));assert.match(msvg,/#ffcc88/);assert.match(msvg,/#713600/);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.content),mermaid);
 record('mermaid','authored styling and source preserved',true);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({results,errors}));
 assert.ok(results.every(row=>row.passed),JSON.stringify(results.filter(row=>!row.passed)));
}catch(error){
 primary=error;
 if(page&&!page.isClosed())await recordPan('failure','before-cleanup');
}finally{
 try{await finishFixture({label:'Diagram fixture',close:[['browser',()=>browser?.close()]],stop:async()=>{fixtureExit=await stopFixture()}})}catch(error){secondary.push(error)}
 try{
  const path=process.env.DIAGRAM_EVIDENCE||root+'output/browser-checks/diagram-navigation/report.json';
  mkdirSync(dirname(path),{recursive:true});
  writeFileSync(path,JSON.stringify({results,errors,panFacts,readyLayouts,fixtureExit,
   primary:primary?{name:primary.name,code:primary.code??null}:null,
   diagnosticErrors:secondary.map(error=>({name:error.name,code:error.code??null}))},null,2));
 }catch(error){secondary.push(error)}
}
if(primary&&secondary.length)throw new AggregateError([primary,...secondary],'Diagram primary failure with diagnostic/cleanup failures',{cause:primary});
if(primary)throw primary;
if(secondary.length)throw new AggregateError(secondary,'Diagram diagnostic/cleanup failure');
