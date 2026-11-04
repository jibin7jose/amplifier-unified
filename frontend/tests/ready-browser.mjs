// Maintained source journey. Build production assets before the prepared managed
// browser-test route or ordinary background runner. About one minute when warm.
// Synthetic fixture only; no live provider, saved appearance edits or guest Git.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {readyExecution} from './ready-runtime.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON,out=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(python,'AMPLIFIER_TEST_PYTHON must name the prepared interpreter; no .venv fallback');
assert.ok(out,'AMPLIFIER_TEST_ARTIFACTS must name the owned evidence directory');
await mkdir(out,{recursive:true});
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const report={runtime:'synthetic',productionAssets:true,liveProvider:false,currentPhase:'preflight',
 fixture:{spawned:false,exit:null,close:null},browserClose:{attempted:false,completed:false},
 checks:[],states:[],screenshots:[],errors:[],httpErrors:[],externalRequests:[],
 limits:['Chromium only; no all-platform or font-availability claim.',
  'Synthetic completed messages; no streaming, provider or personal-data qualification.',
  'Computed paint/contrast checks and saved screenshots are not pixel review or aesthetic approval.',
  'Source-only authoring is not runtime acceptance; run this journey against the exact built candidate.']};
let fixture,stop,browser,page,failure,watchdog,stderr='',stderrTruncated=false;
const phase=(name,details={})=>{report.currentPhase=name;report.current={...details}};
const check=(kind,value)=>{assert.ok(report.checks.length<320,'Bounded check report');report.checks.push({kind,...report.current,...value})};
const snapshot=(kind,value)=>{assert.ok(report.states.length<24,'Bounded state report');report.states.push({kind,...value})};
const shot=async(name,{failed=false}={})=>{
 assert.ok(failed?!report.screenshots.some(row=>row.failure):report.screenshots.filter(row=>!row.failure).length<6,
  'Six success screenshots plus at most one failure screenshot');
 const file=name+'.png',bytes=await page.screenshot({path:join(out,file),timeout:5000});
 report.screenshots.push({file,sha256:hash(bytes),phase:report.currentPhase,failure:failed,pixelReviewed:false});
};
const rgb=value=>{
 if(value==='transparent')return [0,0,0,0];
 const m=value.match(/^(rgba?|color)\((.*)\)$/);assert.ok(m,'Unsupported computed color: '+value);
 let parts=m[2].trim().split(/[\s,/]+/).filter(Boolean);
 const srgb=m[1]==='color';
 if(srgb)assert.equal(parts.shift(),'srgb','Only resolved sRGB paint is qualified: '+value);
 assert.ok(parts.length===3||parts.length===4,'Unresolved computed color: '+value);
 const values=parts.map((v,i)=>v.endsWith('%')?parseFloat(v)*(i===3?1:255)/100:Number(v)*(srgb&&i<3?255:1));
 assert.ok(values.every(Number.isFinite),'Non-numeric computed color: '+value);
 return [...values.slice(0,3),values[3]??1];
};
const over=(a,b)=>{const alpha=a[3]+b[3]*(1-a[3]);assert.ok(alpha>0,'No opaque paint backing');
 return [...a.slice(0,3).map((v,i)=>(v*a[3]+b[i]*b[3]*(1-a[3]))/alpha),alpha]};
const ratio=(a,b)=>{
 const luminance=c=>c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
  .reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
 const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
};
// Resolve the actual leaf's paint chain, never assume a white browser canvas or
// substitute a case/navigation token for an unknown transparent ancestor.
const backing=(rows,label)=>{
 const end=rows.findIndex(row=>rgb(row.background)[3]===1);
 assert.ok(end>=0,label+': transparent chain has no measured opaque backing');
 const visible=rows.slice(0,end+1);
 for(const row of visible){
  if(row.image==='none')continue;
  // The authored header stripe is confined below this text's bounds. It is not
  // used as the contrast backing; any other overlapping artwork stays unqualified.
  assert.ok(row.headerStripeClear,`${label}: image-dependent backing at ${row.node}: ${row.image}`);
 }
 let result=rgb(visible.at(-1).background);
 for(const row of visible.slice(0,-1).reverse())result=over(rgb(row.background),result);
 return result;
};
const cssList=value=>{
 const result=[];let depth=0,start=0;
 for(let i=0;i<value.length;i++){if(value[i]==='(')depth++;else if(value[i]===')')depth--;
  else if(value[i]===','&&!depth){result.push(value.slice(start,i).trim());start=i+1}}
 result.push(value.slice(start).trim());return result;
};

try{
 Object.assign(report,await readyExecution());
 report.observedRuntime={node:process.version,platform:process.platform,arch:process.arch,
  selectedPython:python,pythonIdentity:'selected executable only; not an installed-distribution attestation'};
 // Background source/tree/plan provenance remains the canonical runner's
 // results.json, not a synthesized managed execution contract.
 report.sourceSha256={};
 for(const file of ['frontend/tests/ready-browser.mjs','frontend/tests/ready-runtime.mjs','frontend/tests/fixture-lifecycle.mjs',
  'frontend/tests/browser-chat-pages.mjs','frontend/tests/composer-test-helpers.mjs',
  'tests/fixtures/empty_host_ui_server.py','frontend/src/chat-presentation.css',
  'frontend/src/conversation-rail.css','amplifier_web/appearances/ready.amplifier.css']){
  report.sourceSha256[file]=hash(await readFile(join(root,file)));
 }
 // Candidate CSS is read only at qualification time, not derived from another
 // author's unfinished implementation. Its palette is deliberately not an oracle.
 const css=await readFile(join(root,'amplifier_web/appearances/ready.amplifier.css'),'utf8');
 report.cssSha256=hash(css);
 const buildBytes=await readFile(join(root,'amplifier_web/static/build.json'));
 report.staticBuildIdentity={sha256:hash(buildBytes),build:JSON.parse(buildBytes.toString())};
 fixture=spawn(python,[join(root,'tests/fixtures/empty_host_ui_server.py'),'--chat-controls'],
  {cwd:root,stdio:['ignore','pipe','pipe']});
 stop=ownFixture(fixture,{label:'READY fixture'}); // Shared strict 10s graceful / 2s forced reap.
 report.fixture={spawned:true,pid:fixture.pid,exit:null,close:null,gracefulTimeoutMs:10000,killedTimeoutMs:2000};
 fixture.stderr.on('data',chunk=>{const next=stderr+chunk;stderrTruncated ||= next.length>24000;stderr=next.slice(-24000)});
 fixture.once('error',error=>{report.fixture.spawnError=String(error).slice(0,2000)});
 fixture.once('exit',(code,signal)=>{report.fixture.exit={code,signal}});
 fixture.once('close',(code,signal)=>{report.fixture.close={code,signal}});
 phase('fixture-startup');
 const url=await new Promise((resolve,reject)=>{
  let pending='',settled=false;
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value)};
  const timer=setTimeout(()=>finish(Error('READY fixture startup exceeded 20s')),20000);
  fixture.once('error',error=>finish(error));
  fixture.once('exit',(code,signal)=>finish(Error(`Fixture exited before readiness: ${code??signal}`)));
  fixture.stdout.on('data',chunk=>{
   pending=(pending+chunk).slice(-24000);const lines=pending.split('\n');pending=lines.pop();
   for(const line of lines){let row;try{row=JSON.parse(line)}catch{continue}if(row.url)finish(null,row.url)}
  });
 });
 const origin=new URL(url).origin;
 assert.equal(new URL(url).hostname,'127.0.0.1','Only the owned loopback fixture may be driven');
 browser=await chromium.launch({headless:true,
  args:JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]'),
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 report.observedRuntime.chromium=browser.version();
 page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1,colorScheme:'light',
  reducedMotion:'no-preference',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(6000);page.setDefaultNavigationTimeout(20000);
 watchdog=setTimeout(()=>{report.deadlineExceeded=true;void page.close().catch(()=>{})},120000);
 await page.route('**/*',route=>{
  const target=new URL(route.request().url());
  if(['http:','https:'].includes(target.protocol)&&target.origin!==origin){
   if(report.externalRequests.length<20)report.externalRequests.push(target.origin+target.pathname);
   return route.abort('blockedbyclient');
  }
  return route.continue();
 });
 page.on('pageerror',error=>{if(report.errors.length<30)report.errors.push(error.message.slice(0,2000))});
 page.on('response',response=>{if(response.status()>=400&&report.httpErrors.length<30)
  report.httpErrors.push({path:new URL(response.url()).pathname.slice(0,300),status:response.status()})});
 const action=async(name,args={})=>{
  const receipt=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  assert.equal(receipt.accepted,true,name+': '+JSON.stringify(receipt.error||receipt));return receipt;
 };
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const preferences=()=>page.evaluate(()=>{
  const s=window.amplifier.getState(),shell=window.amplifier.getShellState();
  return {settings:s.settings,view:s.view,selectedSessionId:s.selectedSessionId,selectedWorkspaceId:s.selectedWorkspaceId,
   presentation:shell.effectiveComposition.presentation};
 });
 // Apply owns exactly these two view fields. Keep all other view keys,
 // including editor drafts, in the preservation comparison.
 const unaffectedByApply=({view,...rest})=>{
  const {themeUndo,themePreview,...unaffectedView}=view;
  return {...rest,view:unaffectedView};
 };
 const changePresentation=(value,replace)=>page.evaluate(async({value,replace})=>{
  const s=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:s.revision,
   composition:{...s.effectiveComposition,presentation:replace?value:{...s.effectiveComposition.presentation,...value}}});
  if(!prepared.accepted)throw Error('Presentation prepare refused: '+JSON.stringify(prepared.error));
  const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:s.revision,changeId:prepared.result.id});
  if(!applied.accepted)throw Error('Presentation apply refused: '+JSON.stringify(applied.error));
  return {changeId:prepared.result.id,baseRevision:s.revision,appliedRevision:applied.result.revision,
   status:applied.result.status};
 },{value,replace});
 const presentation=patch=>changePresentation(patch,false);
 // Restoration replaces the complete saved presentation, including absent keys.
 // Merging an empty original would retain the temporary scheme/decorations.
 const replacePresentation=value=>changePresentation(value,true);
 const settled=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const app=page.locator('#amp-one'),pane=page.locator('[data-part="messages"]');
 const input=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const send=page.getByRole('button',{name:'Send message',exact:true});
 const work=page.locator('[data-part="work-browser"]:visible');
 const nav=page.locator('[data-part="navigation"]');
 const rail=nav.locator(':scope > .a-nav-rail');
 const paint=locator=>locator.evaluate(el=>{
  const s=getComputedStyle(el),ancestors=[],contexts=[];
  const leafBounds=el.getBoundingClientRect();
  const name=n=>(n.id?'#'+n.id:n.tagName.toLowerCase()+'.'+[...n.classList].slice(0,3).join('.')).slice(0,160);
  for(let node=el;node;node=node.parentElement){
   if(contexts.length>=24)throw Error('Paint ancestry exceeded bounded inspection');
   const v=getComputedStyle(node);
   contexts.push({node:name(node),hover:node.matches(':hover'),opacity:v.opacity,filter:v.filter,blend:v.mixBlendMode});
   const box=node.getBoundingClientRect();
   const headerStripeClear=node.matches('[data-part="header"]')&&
    v.backgroundImage.startsWith('linear-gradient(')&&v.backgroundSize==='100% 3px'&&
    v.backgroundRepeat==='no-repeat'&&v.backgroundPosition==='0% 100%'&&v.backgroundOrigin==='padding-box'&&
    leafBounds.top>=box.top+parseFloat(v.borderTopWidth)&&
    leafBounds.bottom<=box.bottom-parseFloat(v.borderBottomWidth)-3;
   ancestors.push({node:name(node),background:v.backgroundColor,image:v.backgroundImage,
    origin:v.backgroundOrigin,bottomBorder:parseFloat(v.borderBottomWidth),headerStripeClear});
  }
  return {node:name(el),color:s.color,stroke:s.stroke,background:s.backgroundColor,font:s.fontFamily,fontSize:s.fontSize,
   outline:s.outlineColor,outlineWidth:parseFloat(s.outlineWidth),outlineOffset:parseFloat(s.outlineOffset),
   outlineStyle:s.outlineStyle,focusVisible:el.matches(':focus-visible'),
   hoverWithin:el.matches(':hover')||!!el.querySelector(':hover'),ancestors,contexts};
 });
 const contrast=async(locator,label)=>{
  let sample,background,pointerParking;
  try{
   // click() leaves the real pointer over Brief (or a nested button leaf).
   // Native button:hover applies brightness(.97) to the whole painted group,
   // not the resolved color values. Measure the non-hover baseline instead.
   pointerParking=await page.locator('[data-part="header"]').evaluate(el=>{
    const box=el.getBoundingClientRect(),x=box.left+box.width/2,y=box.top+2;
    return {x,y,safe:document.elementFromPoint(x,y)===el};
   });
   assert.ok(pointerParking.safe,label+': observed header padding must be clear of controls');
   await page.mouse.move(pointerParking.x,pointerParking.y);await settled();
   await expect.poll(async()=>{
    sample=await paint(locator);
    return !sample.hoverWithin&&sample.contexts.every(row=>row.filter==='none');
   },{message:label+': non-hover subtree and unfiltered paint must settle'}).toBe(true);
   for(const row of sample.contexts){
    assert.equal(Number(row.opacity),1,label+': unqualified group opacity at '+row.node);
    assert.equal(row.filter,'none',label+': unqualified filtered paint at '+row.node);
    assert.equal(row.blend,'normal',label+': unqualified blended paint at '+row.node);
   }
   background=backing(sample.ancestors,label);
  }
  catch(error){check('unqualified-paint',{label,pointerParking,...sample});throw error}
  const foreground=rgb(sample.color);
  const value=ratio(over(foreground,background),background);
  check('contrast',{label,ratio:value,minimum:4.5,resolvedBackground:background,...sample});
  assert.ok(value>=4.5,`${label}: measured leaf contrast ${value.toFixed(2)} < 4.5`);
  return {...sample,background,ratio:value};
 };
 // Text nodes, not a container's inherited color, are the contrast oracle.
 const textLeaves=async(locator,label,{maximum=12}={})=>{
  const samples=await locator.evaluate(el=>{
   const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),parents=[];
   while(walker.nextNode()){
    const node=walker.currentNode.parentElement,s=getComputedStyle(node);
    if(walker.currentNode.textContent.trim()&&s.display!=='none'&&s.visibility==='visible'&&node.getClientRects().length&&!parents.includes(node))parents.push(node);
   }
   return parents.length;
  });
  assert.ok(samples>0&&samples<=maximum,`${label}: expected 1..${maximum} actual text leaves, found ${samples}`);
  // Locator expressions are stable DOM selectors; no injected IDs/classes.
  const leaves=locator.locator('xpath=descendant-or-self::*[text()[normalize-space()]]');
  let checked=0;
  for(let i=0;i<await leaves.count();i++){
   const leaf=leaves.nth(i);if(!await leaf.isVisible())continue;
   await contrast(leaf,label+' leaf '+(++checked));
  }
  assert.equal(checked,samples,label+': every rendered text-bearing element measured');
 };
 const focus=async(locator,label)=>{
  await locator.focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
  await expect(locator).toBeFocused();await expect(locator).toBeInViewport();
  const bounds=await locator.boundingBox();
  assert.ok(bounds&&bounds.width>0&&bounds.height>0,label+': focused control has painted dimensions');
  const sample=await paint(locator);
  assert.equal(sample.focusVisible,true,label+': real keyboard :focus-visible');
  assert.notEqual(sample.outlineStyle,'none');assert.ok(sample.outlineWidth>=2,label+': visible focus width');
  assert.ok(sample.outlineOffset>=0,label+': outside focus ring');
  // Exclude the button's own fill. An offset outline is on its parent paint.
  const surroundings=backing(sample.ancestors.slice(1),label+' outline surroundings');
  assert.ok(sample.contexts.every(row=>Number(row.opacity)===1&&row.filter==='none'&&row.blend==='normal'),
   label+': unsupported focus paint compositing');
  const outlineRatio=ratio(over(rgb(sample.outline),surroundings),surroundings);
  check('focus',{label,outlineRatio,minimum:3,surroundings,...sample});
  assert.ok(outlineRatio>=3,`${label}: external outline contrast ${outlineRatio.toFixed(2)} < 3`);
 };
 const noOverflow=async(locator,label)=>{
  const geometry=await locator.evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth}));
  assert.ok(geometry.scroll<=geometry.client+1,label+': no horizontal surface overflow '+JSON.stringify(geometry));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page horizontal overflow');
  return geometry;
 };
 const composerFocus=async label=>{
  await input.focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
  await expect(input).toBeFocused();await expect(input).toBeInViewport();
  const boundary=await input.evaluate(el=>{
   const surface=el.closest('[data-part="composer-surface"]');
   if(!surface)throw Error('The editor must be inside the real composer surface');
   const editorStyle=getComputedStyle(el),surfaceStyle=getComputedStyle(surface);
   return {editorFocusVisible:el.matches(':focus-visible'),editorOutline:editorStyle.outlineStyle,
    surfaceFocusWithin:surface.matches(':focus-within'),surfaceOutline:surfaceStyle.outlineStyle,
    surfaceOutlineWidth:parseFloat(surfaceStyle.outlineWidth),surfaceOutlineOffset:parseFloat(surfaceStyle.outlineOffset)};
  });
  assert.equal(boundary.editorFocusVisible,true,label+': actual keyboard editing focus');
  assert.equal(boundary.editorOutline,'none',label+': no nested rectangle around text');
  assert.equal(boundary.surfaceFocusWithin,true);
  assert.equal(boundary.surfaceOutline,'solid');assert.ok(boundary.surfaceOutlineWidth>=2);
  assert.ok(boundary.surfaceOutlineOffset>=0,'Composer boundary retains its visible external focus ring');
  check('composer-focus',{label,...boundary});
 };
 const fade=()=>pane.evaluate(el=>{
  const s=getComputedStyle(el);
  return {top:el.scrollTop,remaining:el.scrollHeight-el.clientHeight-el.scrollTop,above:el.dataset.overflowAbove,
   below:el.dataset.overflowBelow,mask:s.maskImage,edge:s.getPropertyValue('--_a-transcript-edge-mask').trim(),
   bottom:s.getPropertyValue('--_a-transcript-fade-bottom').trim()};
 });
 const end=async(label,{short=false,forced=false}={})=>{
  await input.focus();
  for(let i=0;i<2;i++){await pane.evaluate(el=>{el.scrollTop=el.scrollHeight});await settled()}
  await expect.poll(async()=>(await fade()).remaining).toBeLessThanOrEqual(1);
  const value=await fade();check('fade',{label,...value});
  assert.equal(value.below,'false',label+': last content is not bottom-faded');
  if(short){assert.equal(value.above,'false');assert.equal(value.mask,'none')}
  else{
   assert.equal(value.above,'true',label+': long synthetic transcript exercises shared edges');
   assert.equal(value.bottom,'0px',label+': shared bottom fade disabled at end');
   if(forced)assert.equal(value.mask,'none');
   else{
    assert.notEqual(value.mask,'none');
    const vertical=cssList(value.mask).filter(layer=>!/\bto right\b|\b90deg\b/.test(layer));
    assert.equal(vertical.length,1,'One shared vertical edge mask');
    assert.match(vertical[0],/black 100%|rgb\(0, 0, 0\) 100%/,'End of shared edge is opaque');
   }
  }
  await expect(pane.locator('.a-message.a-assistant').last()).toBeInViewport();
 };
 const selection=async(locator,label,primary)=>{
  await locator.click({clickCount:3});
  assert.ok(await page.evaluate(()=>!!window.getSelection()?.toString().trim()),label+': real text selection');
  const value=await locator.evaluate(el=>{const s=getComputedStyle(el,'::selection');return {color:s.color,background:s.backgroundColor}});
  const forced=await page.evaluate(()=>matchMedia('(forced-colors: active)').matches);
  const sample=await paint(locator);
  // Retain raw system colors and the selected leaf's real backing even if a
  // paint qualification below fails. Forced Highlight may be translucent.
  check('selection-paint',{label,forced,...value,paint:sample});
  for(const row of sample.contexts){
   assert.equal(Number(row.opacity),1,label+': unqualified selection opacity at '+row.node);
   assert.equal(row.filter,'none',label+': unqualified selection filter at '+row.node);
   assert.equal(row.blend,'normal',label+': unqualified selection blend at '+row.node);
  }
  const bg=rgb(value.background),ink=rgb(value.color);
  if(forced)assert.ok(bg[3]>0&&bg[3]<=1,label+': visible system selection paint');
  else assert.equal(bg[3],1,label+': opaque selection paint');
  const measuredBacking=backing(sample.ancestors,label+' selection backing');
  const resolvedBackground=over(bg,measuredBacking);
  const valueRatio=ratio(over(ink,resolvedBackground),resolvedBackground);
  check('selection',{label,forced,...value,measuredBacking,resolvedBackground,ratio:valueRatio,minimum:4.5});
  assert.ok(valueRatio>=4.5,label+': selection text contrast >= 4.5');
  if(primary){assert.deepEqual(bg,rgb(primary.background),'Selection uses the actual primary accent fill');
   assert.deepEqual(ink,rgb(primary.color),'Selection uses actual on-accent ink')}
  await input.focus();
 };
 const bubbles=async width=>{
  const rows=await pane.locator('.a-message.a-user:has(> .a-message-actions),.a-message.a-assistant:has(> .a-message-actions)')
   .evaluateAll(nodes=>nodes.map(el=>{
    const body=el.querySelector(':scope > .a-message-body'),footer=el.querySelector(':scope > .a-message-actions');
    const s=getComputedStyle(el),b=getComputedStyle(body),f=getComputedStyle(footer);
    return {role:el.classList.contains('a-user')?'user':'assistant',outer:s.backgroundColor,outerImage:s.backgroundImage,
     outerBorder:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth],
     outerPadding:[s.paddingTop,s.paddingRight,s.paddingBottom,s.paddingLeft],outerShadow:s.boxShadow,
     body:b.backgroundColor,padding:[b.paddingTop,b.paddingRight,b.paddingBottom,b.paddingLeft],
     footer:f.backgroundColor,footerImage:f.backgroundImage,footerShadow:f.boxShadow,
     footerBorder:[f.borderTopWidth,f.borderRightWidth,f.borderBottomWidth,f.borderLeftWidth],
     bodyBottom:body.getBoundingClientRect().bottom,footerTop:footer.getBoundingClientRect().top};
   }));
  assert.equal(rows.length,2,'One real user/assistant exchange');
  for(const row of rows){
   assert.equal(rgb(row.outer)[3],0);assert.equal(row.outerImage,'none');
   assert.deepEqual(row.outerBorder,['0px','0px','0px','0px']);
   assert.deepEqual(row.outerPadding,['0px','0px','0px','0px']);assert.equal(row.outerShadow,'none');
   assert.equal(rgb(row.body)[3],1,'Bubble body owns opaque reading paint');
   assert.equal(rgb(row.footer)[3],0);assert.equal(row.footerImage,'none');assert.equal(row.footerShadow,'none');
   assert.deepEqual(row.footerBorder,['0px','0px','0px','0px']);
   assert.ok(row.footerTop>=row.bodyBottom,'Footer stays outside body paint');
   if(row.role==='assistant')assert.deepEqual(row.padding,width===390?['12px','12px','12px','12px']:['16px','20px','16px','20px']);
  }
  check('bubbles',{rows});
 };
 const artwork=()=>app.evaluate(el=>{
  const selectors=[':scope','[data-part="header"]','[data-part="conversation"]','[data-part="navigation"] > .a-nav-rail',
   '[data-part="work-browser"]'];
  const rows=[];
  for(const selector of selectors)for(const node of selector===':scope'?[el]:el.querySelectorAll(selector)){
   if(!node.getClientRects().length)continue;
   for(const pseudo of [null,'::before','::after']){
    const s=getComputedStyle(node,pseudo);
    rows.push({selector,pseudo,content:s.content,image:s.backgroundImage,animation:s.animationName,
     painted:s.display!=='none'&&s.visibility==='visible'&&Number(s.opacity)>0&&(!pseudo||!['none','normal'].includes(s.content))});
   }
  }
  return rows;
 });
 const staticArt=async(label,off=false)=>{
  const rows=await artwork();
  for(const row of rows){if(!row.painted)continue;assert.equal(row.animation,'none',label+': no theme animation');
   if(off)assert.equal(row.image,'none',`${label}: artwork removed on ${row.selector} ${row.pseudo||''}`)}
  check('artwork',{label,off,rows});
 };

 phase('passive-library');
 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 const servedBuild=await page.request.get(url+'/build.json');assert.equal(servedBuild.status(),200);
 assert.equal(hash(await servedBuild.body()),report.staticBuildIdentity.sha256,'Served exact production build identity');
 const initial=await state(),beforeListing=await preferences(),listing=(await action('theme.list')).result;
 const ready=(await action('theme.read',{id:'builtin:ready'})).result;
 assert.equal(listing.items.filter(row=>row.id==='builtin:ready').length,1);
 assert.equal(ready.name,'Breadbin');assert.equal(ready.source,'built-in');assert.equal(ready.css,css);
 assert.equal(ready.fingerprint,report.cssSha256,'Served builtin CSS fingerprint matches exact source resource');
 assert.deepEqual((await state()).theme,initial.theme);assert.deepEqual(await preferences(),beforeListing);
 assert.doesNotMatch(css,/(?:-webkit-)?mask-image\s*:/i,'Candidate inherits the native transcript mask');
 check('library',{id:ready.id,name:ready.name,source:ready.source,resource:'amplifier_web/appearances/ready.amplifier.css',
  servedCssSha256:ready.fingerprint,passive:true});
 const workspacePath=initial.settings.workspace;
 const workspace=(await action('workspace.list')).result.items.find(row=>row.path===workspacePath);
 assert.ok(workspace,'Use the fixture existing registered settings.workspace; no arbitrary guest directories');
 await action('workspace.pin',{id:workspace.id,pinned:true});
 const create=async title=>{
  const receipt=await action('session.create',{workspace:workspacePath,title,select:false});
  const id=receipt.sessionId||receipt.result?.sessionId;assert.ok(id);return id;
 };
 const title='Synthetic READY reading',longId=await create(title),shortId=await create('Synthetic READY short');
 await action('session.pin',{id:longId,pinned:true});
 await action('session.select',{id:longId});await showFixtureConversation(page,{navPinned:false});
 phase('synthetic-message-setup');
 // Put the short Markdown/code sample at the end so the four end-position
 // screenshots show it beside the real assistant response and composer.
 const markdown=[...Array.from({length:14},(_,i)=>`Section ${i+1}. `+
   'This synthetic reading passage checks ordinary paragraphs, navigation and controls. '.repeat(3).trim()),
  '## Synthetic hardware notes','Readable **body text** with `inline code`.',
  '```js\nconst ready = true;\nconsole.log(ready);\n```'].join('\n\n');
 // Native paste follows the composer's Markdown intake. fill() would insert
 // literal text into a rich editor and can legitimately escape its syntax.
 await page.context().grantPermissions(['clipboard-read','clipboard-write'],{origin});
 await page.evaluate(text=>navigator.clipboard.writeText(text),markdown);
 await input.focus();await page.keyboard.press('ControlOrMeta+V');
 await expect(input).toHaveDraft(markdown);await send.click();
 await expect(pane.locator('.a-message.a-assistant')).toHaveCount(1);await expect(input).toHaveDraft('');
 const draft='Keep this synthetic unsent draft across READY appearance and navigation.';
 await input.fill(draft);
 const beforeApply=await preferences(),priorTheme=(await state()).theme;
 phase('candidate-apply');
 await action('theme.apply',{name:ready.name,css:ready.css});
 await expect.poll(async()=>hash((await state()).theme.css)).toBe(report.cssSha256);
 await page.waitForFunction(css=>[...document.querySelectorAll('#amp-one > style')].some(node=>node.textContent===css),css);
 const applied=await state(),appliedPreferences=await preferences();
 assert.deepEqual(applied.theme,{name:ready.name,css:ready.css},'Apply commits the exact candidate name and CSS');
 assert.equal(applied.view.themePreview,false,'Apply ends preview mode');
 assert.deepEqual(unaffectedByApply(appliedPreferences),unaffectedByApply(beforeApply),
  'Apply preserves every unrelated view field, draft, selection, setting and presentation preference');
 assert.ok(applied.view.themeUndo?.before?.$resource,'Undo retains the prior theme resource');
 assert.match(applied.view.themeUndo.after,/^[a-f0-9]{64}$/,'Undo identifies the applied theme');
 await expect(input).toHaveDraft(draft);
 // Exercise the canonical guard and restoration rather than reproducing the
 // backend fingerprint or trusting that an opaque resource ID is the right one.
 await action('theme.revert');
 const reverted=await state();
 assert.deepEqual(reverted.theme,priorTheme,'Undo restores the exact theme that preceded Apply');
 assert.equal(Object.hasOwn(reverted.view,'themeUndo'),false,'Successful Undo clears its consumed record');
 assert.equal(reverted.view.themePreview,false);
 assert.deepEqual(unaffectedByApply(await preferences()),unaffectedByApply(beforeApply));
 await expect(input).toHaveDraft(draft);
 await action('theme.apply',{name:ready.name,css:ready.css});
 const reapplied=await state();
 assert.deepEqual(reapplied.theme,applied.theme,'Reapply restores the exact candidate for the matrix');
 assert.equal(reapplied.view.themePreview,false);
 assert.deepEqual(reapplied.view.themeUndo,applied.view.themeUndo,'Undo binds the same prior and applied themes');
 assert.deepEqual(unaffectedByApply(await preferences()),unaffectedByApply(beforeApply));
 await expect(input).toHaveDraft(draft);
 await page.waitForFunction(css=>[...document.querySelectorAll('#amp-one > style')].some(node=>node.textContent===css),css);
 snapshot('apply-invariance',{selectedSessionId:longId,draft,
  preferencesSha256:hash(JSON.stringify(unaffectedByApply(beforeApply))),
  themeBookkeepingChecked:['themeUndo','themePreview'],undoVerifiedBy:'fixture Apply/Revert/Reapply'});
 const originalPresentation=beforeApply.presentation;
 const user=()=>pane.locator('.a-message.a-user > .a-message-body');
 const assistant=()=>pane.locator('.a-message.a-assistant > .a-message-body');
 const reading=()=>user().locator('p').filter({hasText:'Readable body text with inline code.'});
 const sidebar=async({rename=true,forced=false}={})=>{
  const narrow=page.viewportSize().width<=900;
  await expect(page.locator('[data-part="workspace"][data-pane-layout]')).toHaveAttribute('data-narrow',String(narrow));
  await expect.poll(()=>nav.getAttribute('role')).toBe(narrow?'dialog':null);
  await action('view.update',{patch:{navExpanded:false,navPinned:false,navMode:'browse',navSectionsCollapsed:[]}});
  await expect(nav).toHaveAttribute('hidden','');await expect(nav).toHaveAttribute('inert','');
  const toggle=page.locator('[data-part="header"] #navigation-toggle');
  await expect(toggle).toHaveAttribute('aria-expanded','false');
  await toggle.click();await expect(toggle).toHaveAttribute('aria-expanded','true');
  if(!narrow)await action('view.update',{patch:{navPinned:false}});
  await expect(rail).toBeVisible();
  // The outer navigation slot may be zero-width. Its painted rail and pinned
  // rows are the visibility/geometry owners; the details portal lives in the slot.
  const pinned=rail.locator('[data-sidebar-section="pinned"]');
  const row=pinned.locator(`.a-nav-chat[data-session-id="${longId}"]:not(.a-nav-chat-rename)`);
  const workspaceRow=pinned.locator('.a-workspace-row');
  await expect(row).toBeVisible();await expect(workspaceRow).toHaveCount(1);
  await expect(row).toHaveClass(/is-selected/);
  const select=row.locator(':scope > .a-nav-chat-select'),grip=row.locator(':scope > .a-reorder-grip');
  const details=row.locator(':scope > .a-navigation-more');
  await grip.focus();await page.keyboard.press('Tab');await expect(select).toBeFocused();
  await focus(select,'Pinned chat select');
  await page.keyboard.press('Tab');await expect(details).toBeFocused();
  await focus(details,'Pinned chat details');
  const geometry=await row.evaluate(el=>{
   const r=el.getBoundingClientRect(),s=getComputedStyle(el);
   const inset=selector=>{const b=el.querySelector(selector).getBoundingClientRect();return {start:b.left-r.left,end:r.right-b.right,width:b.width}};
   return {start:s.paddingInlineStart,end:s.paddingInlineEnd,boxSizing:s.boxSizing,shadow:s.boxShadow,
    outline:s.outlineStyle,outlineColor:s.outlineColor,grip:inset(':scope > .a-reorder-grip'),
    icon:inset('.a-navigation-status'),title:inset('.a-nav-chat-title')};
  });
  const workspacePadding=await workspaceRow.evaluate(el=>{const s=getComputedStyle(el);return [s.paddingInlineStart,s.paddingInlineEnd,s.boxSizing]});
  check('sidebar',{geometry,workspacePadding,forced});
  assert.equal(geometry.start,'8px');assert.equal(geometry.end,'4px');assert.equal(geometry.boxSizing,'border-box');
  assert.deepEqual(workspacePadding,['8px','4px','border-box']);
  for(const key of ['grip','icon','title'])assert.ok(geometry[key].width>0&&geometry[key].start>=8&&geometry[key].end>=4,
   key+': actual content clears the 3px selected stripe and remains inside row padding');
  if(!forced)assert.match(geometry.shadow,/inset.*\b3px 0(?:px)?|(?:^|,)\s*[^,]*\b3px 0(?:px)?[^,]*inset/,'Actual 3px inset selection stripe');
  else{assert.notEqual(geometry.outline,'none');assert.ok(rgb(geometry.outlineColor)[3]>0,'Forced colors retains selected outline')}
  await textLeaves(row.locator('.a-nav-chat-title > span'),'Selected navigation title');
  await textLeaves(workspaceRow.locator('.a-workspace-label > [data-scroll-title]:first-child > span'),'Workspace navigation title');
  await textLeaves(rail.getByRole('group',{name:'Sidebar view',exact:true}).getByRole('button',{name:'Browse',exact:true}),'Sidebar selected view');
  await textLeaves(rail.getByRole('group',{name:'Sidebar view',exact:true}).getByRole('button',{name:'Status',exact:true}),'Sidebar unselected view');
  await noOverflow(rail,'Visible sidebar rail');
  if(rename){
   await details.click();
   const dialog=nav.getByRole('dialog',{name:'Details for '+title,exact:true});
   await expect(dialog).toBeVisible();
   await dialog.getByRole('button',{name:'Rename '+title,exact:true}).click();
   const form=dialog.locator('.a-nav-chat-rename');
   await expect(form.getByRole('textbox',{name:'New name for '+title,exact:true})).toBeFocused();
   const native=await form.evaluate(el=>{const s=getComputedStyle(el);return {start:s.paddingInlineStart,end:s.paddingInlineEnd,wrap:s.flexWrap}});
   assert.deepEqual(native,{start:'3px',end:'3px',wrap:'wrap'},'Sidebar row padding excludes the native rename form');
   await form.getByRole('textbox').fill('Synthetic cancelled rename');
   await form.getByRole('button',{name:'Cancel conversation rename',exact:true}).click();
   await expect(form).toHaveCount(0);await page.keyboard.press('Escape');await expect(details).toBeFocused();
   await expect(row.locator('.a-nav-chat-title')).toHaveText(title);
  }
  assert.equal((await state()).selectedSessionId,longId);await expect(input).toHaveDraft(draft);
  await action('view.update',{patch:{navExpanded:false}});
 };
 const enter=async surface=>{
  if(surface==='chat'){await showFixtureConversation(page,{navPinned:false});return}
  await action('view.update',{patch:{workSurface:surface,workWorkspaceId:workspace.id,workWorkspaceTab:'chats',
   workHomeScope:'all',workHomeFilter:'all',workHomeQuery:'',navExpanded:false,navPinned:false}});
  await expect(work).toHaveAttribute('data-landing',surface);
  const brief=work.getByRole('group',{name:'Work view',exact:true}).getByRole('button',{name:'Brief',exact:true});
  await brief.click();await expect(work).toHaveAttribute('data-view','brief');
  await expect.poll(()=>work.locator('.a-work-card,.a-landing-chat').count()).toBeGreaterThan(0);
 };
 for(const scheme of ['light','dark'])for(const width of [1600,390]){
  phase('matrix',{scheme,width});
  await page.setViewportSize({width,height:1000});await presentation({scheme,decorations:true});
  await expect(app).toHaveAttribute('data-theme-scheme',scheme);
  for(const surface of ['home','workspace']){
   phase('landing',{scheme,width,surface});await enter(surface);
   await textLeaves(work.getByRole('heading',{level:1}),'Native '+surface+' heading');
   const brief=work.getByRole('group',{name:'Work view',exact:true}).getByRole('button',{name:'Brief',exact:true});
   await contrast(brief,surface+' selected view');await focus(brief,surface+' view');
   await noOverflow(work,surface);await staticArt(surface);
   if(width===1600&&((surface==='home'&&scheme==='light')||(surface==='workspace'&&scheme==='dark')))
    await shot(`ready-${surface}-${scheme}-${width}`);
  }
  phase('chat',{scheme,width});await enter('chat');await expect(input).toHaveDraft(draft);
  await expect(user().getByRole('heading',{name:'Synthetic hardware notes',exact:true})).toBeVisible();
  await expect(user().locator('pre code')).toHaveText('const ready = true;\nconsole.log(ready);');
  await bubbles(width);await noOverflow(pane,'Transcript');await noOverflow(page.locator('form[data-part="composer"]'),'Composer');
  await textLeaves(page.locator('[data-part="header"] .a-work-heading > strong'),'Hardware case chat heading');
  await textLeaves(user().getByRole('heading',{name:'Synthetic hardware notes',exact:true}),'User markdown heading');
  await textLeaves(reading(),'User paragraph');
  await textLeaves(user().locator('pre code'),'Code block',{maximum:24});
  await textLeaves(assistant().locator('p').first(),'Assistant paragraph');
  const primary=await contrast(send.locator('svg'),'Primary Send icon');
  assert.equal(primary.stroke,primary.color,'Actual Send icon stroke uses its measured ink');
  const primaryFill=await paint(send);
  await contrast(input,'Composer container');await textLeaves(input,'Composer text');
  await composerFocus(`${scheme}-${width}`);await focus(send,'Send');
  const add=page.getByRole('button',{name:'Add attachments',exact:true});
  const model=page.getByRole('button',{name:'Model and reasoning settings',exact:true});
  for(const control of [add,send,model]){await expect(control).toBeVisible();await expect(control).toBeInViewport();await expect(control).toBeEnabled()}
  await textLeaves(model.locator('span').first(),'Model control');
  await add.click();const addDialog=page.getByRole('dialog',{name:'Add to your message',exact:true});
  await expect(addDialog).toBeVisible();await expect(addDialog.getByRole('button',{name:'Attach files',exact:true})).toBeInViewport();
  await addDialog.getByRole('button',{name:'Close attachment menu',exact:true}).click();await expect(add).toBeFocused();
  await model.click();await expect(page.getByRole('button',{name:'Close chat options',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Close chat options',exact:true}).click();
  await selection(reading(),'Native selection',{background:primaryFill.background,color:primary.color});
  await staticArt('chat');await sidebar();
  await end(`${scheme}-${width} end`);await shot(`ready-chat-${scheme}-${width}`);
  snapshot('matrix-cell',{scheme,width,selectedSessionId:(await state()).selectedSessionId,draftRetained:true,
   semanticTokens:await app.evaluate(el=>Object.fromEntries(['--a-bg','--a-surface','--a-ink','--a-accent','--a-on-accent']
    .map(token=>[token,getComputedStyle(el).getPropertyValue(token).trim()])))});
 }
 phase('system-scheme');await presentation({scheme:'system'});
 for(const scheme of ['light','dark']){
  await page.emulateMedia({colorScheme:scheme});await expect(app).toHaveAttribute('data-theme-scheme',scheme);
 }
 check('system-scheme',{follows:['light','dark']});
 await page.setViewportSize({width:1600,height:1000});await presentation({scheme:'dark',decorations:true});
 phase('reduced-motion');await page.emulateMedia({reducedMotion:'reduce'});await staticArt('Reduced motion');
 await composerFocus('Reduced motion');await focus(send,'Reduced-motion Send');await sidebar({rename:false});await end('Reduced-motion end');
 phase('decorations-off');await presentation({decorations:false});await expect(app).toHaveAttribute('data-decorations','off');
 await staticArt('Decorations off chat',true);await composerFocus('Decorations off');await focus(send,'Decorations-off Send');
 await selection(reading(),'Decorations-off selection');
 await sidebar({rename:false});await end('Decorations-off end');
 await enter('home');await staticArt('Decorations off Home',true);await enter('chat');
 phase('forced-colors');await presentation({decorations:true});await page.emulateMedia({forcedColors:'active'});
 await staticArt('Forced colors chat',true);await composerFocus('Forced colors');await focus(send,'Forced-colors Send');
 await selection(reading(),'Forced-colors selection');await sidebar({rename:false,forced:true});
 await end('Forced-colors end',{forced:true});await enter('workspace');await staticArt('Forced colors workspace',true);
 await page.emulateMedia({forcedColors:'none',reducedMotion:'no-preference'});
 phase('short-transcript');await action('session.select',{id:shortId});await enter('chat');
 await input.fill('Synthetic short exchange.');await send.click();
 await expect(pane.locator('.a-message.a-assistant')).toHaveCount(1);await end('Short transcript',{short:true});
 await action('session.select',{id:longId});await enter('chat');await expect(input).toHaveDraft(draft);
 phase('restore-and-final-readback');
 const compositionBeforeRestore=await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition);
 const restoreReceipt=await replacePresentation(originalPresentation);
 assert.ok(Number.isSafeInteger(restoreReceipt.appliedRevision),'Restoration returns an exact committed shell revision');
 snapshot('presentation-restoration-request',{before:compositionBeforeRestore.presentation,
  expected:originalPresentation,receipt:restoreReceipt});
 // Apply commits before the browser asynchronously refreshes its shell bootstrap.
 // Observe that exact revision before asserting the restored composition.
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().revision),
  {message:'Browser observes the exact restored shell revision'}).toBe(restoreReceipt.appliedRevision);
 const compositionAfterRestore=await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition);
 snapshot('presentation-restoration',{before:compositionBeforeRestore.presentation,expected:originalPresentation,
  after:compositionAfterRestore.presentation,receipt:restoreReceipt});
 assert.deepEqual(compositionAfterRestore.presentation,originalPresentation,
  'Scheme/decorations changes were temporary fixture presentation only');
 const {presentation:beforePresentation,...otherBeforeRestore}=compositionBeforeRestore;
 const {presentation:afterPresentation,...otherAfterRestore}=compositionAfterRestore;
 assert.deepEqual(otherAfterRestore,otherBeforeRestore,'Restoring presentation preserves every other composition field');
 const final=await state();assert.equal(final.selectedSessionId,longId);assert.equal(hash(final.theme.css),report.cssSha256);
 assert.deepEqual(final.settings,beforeApply.settings,'Settings remain unchanged');
 const libraryIdentity=items=>items.map(({id,name,source,fingerprint})=>({id,name,source,fingerprint}));
 assert.deepEqual(libraryIdentity((await action('theme.list')).result.items),libraryIdentity(listing.items),'No library originals edited');
 const inspection=await (await page.request.get(url+'/fixture')).json();
 assert.equal(inspection.sent.length,2,'Exactly two synthetic fixture sends; no provider execution');
 assert.deepEqual(inspection.sent.map(row=>row.text),[markdown,'Synthetic short exchange.']);
 assert.equal(inspection.workerCount,0,'No live runtime workers');
 assert.equal(report.screenshots.filter(row=>!row.failure).length,6);
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.httpErrors,[]);assert.deepEqual(report.externalRequests,[]);
 report.assertionsPassed=true;
}catch(error){
 failure=error;report.failure=String(error.stack||error).slice(0,12000);
 if(page&&!page.isClosed())try{await shot('ready-failure',{failed:true})}catch(error){report.failureCaptureError=String(error).slice(0,2000)}
}finally{
 clearTimeout(watchdog);report.completedPhase=report.currentPhase;
 // Do not pass originalError: finishFixture must throw cleanup failures even if
 // assertions already failed. Its own bounded browser close still always stops
 // our fixture. Record observed exit AND close even when strict stop rejects.
 if(stop)try{
  await finishFixture({label:'READY fixture',close:[['READY browser',async()=>{
   report.browserClose={attempted:true};if(browser)await browser.close();report.browserClose.completed=true;
  }]],stop:async()=>{report.fixtureStop={attempted:true};
   const exit=await stop();report.fixtureStop={attempted:true,completed:true,...exit};
  }});
 }catch(error){report.cleanupError=String(error.stack||error).slice(0,6000);failure ||= error}
 if(report.deadlineExceeded||report.errors.length||report.httpErrors.length||report.externalRequests.length)
  failure ||= Error('Deadline or browser/network errors; inspect report.json');
 report.passed=!failure;report.stderrTail=stderr;report.stderrTruncated=stderrTruncated;
 report.currentPhase=failure?report.completedPhase:'complete';
 // Collections and strings above are bounded; retain raw measured colors,
 // source/build identities and the failing phase rather than an invented verdict.
 await writeFile(join(out,'report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,states:report.states.length,
  screenshots:report.screenshots,fixtureExit:report.fixture?.exit,fixtureClose:report.fixture?.close,
  browserClose:report.browserClose,currentPhase:report.currentPhase,limits:report.limits}));
}
if(failure)throw failure;