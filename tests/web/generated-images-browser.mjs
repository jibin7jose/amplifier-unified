import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/generated_images_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page,releaseSwapGate,primaryFailure,swapObserving=false;
const swapDiagnostic={schema:2,kind:'generated-image-preview-swap',initial:null,records:[],dropped:0,controls:[],boundaries:null,cleanup:[]};
let swapPhase=null;
const assertPlacement=(result,label)=>assert.equal(result.problem,null,label);
function installPreviewProbe(pane,targets){
 const galleries=pane.querySelectorAll('.a-inline-gallery'),gallery=galleries.length===1?galleries[0]:null;
 let observedEntries=null;
 // Pane/gallery identity is stable; Preview's per-image key may replace its frame.
 // Resolve one current frame and its public selected-entry tuple in one JS turn.
 const resolveFrame=expectedEntry=>{
  const previews=gallery?.querySelectorAll(':scope > section.a-inline-artifact[data-image-frame]')||[];
  const frames=gallery?.querySelectorAll('.a-gallery-image-frame')||[];
  const rails=gallery?.querySelectorAll(':scope > .a-inline-gallery-choices')||[];
  const buttons=rails.length===1?[...rails[0].querySelectorAll(':scope > button')]:[];
  const pressed=buttons.filter(button=>button.getAttribute('aria-pressed')==='true');
  const preview=previews.length===1?previews[0]:null,frame=frames.length===1?frames[0]:null;
  const headers=preview?.querySelectorAll(':scope > button.a-document-output-open[data-action="canvas.select"]')||[];
  const header=headers.length===1?headers[0]:null;
  const titles=header?.querySelectorAll(':scope > span > strong')||[];
  const versions=header?.querySelectorAll(':scope > span > small')||[];
  const fullPreviews=preview?.querySelectorAll('[data-full-artifact-preview]')||[];
  const fullPreview=fullPreviews.length===1?fullPreviews[0]:null;
  const target=pressed.length===1?targets.find(row=>buttons[row.slot-1]===pressed[0]&&
   pressed[0].getAttribute('aria-label')===`Image ${row.slot}: ${row.title}`&&
   preview?.getAttribute('data-artifact-id')===row.resourceId&&preview?.getAttribute('data-artifact-version')===String(row.version)&&
   fullPreview?.getAttribute('data-full-artifact-preview')===row.resourceId&&fullPreview?.getAttribute('data-artifact-version')===String(row.version)&&
   header?.getAttribute('aria-label')===`Open ${row.title} in canvas`&&
   preview?.getAttribute('aria-label')===row.title&&titles.length===1&&titles[0].textContent===row.title&&
   versions.length===1&&versions[0].textContent===`Image · Version ${row.version}`):null;
  const panes=pane.ownerDocument.querySelectorAll('.a-messages'),currentGalleries=pane.querySelectorAll('.a-inline-gallery');
  return{frame,preview,buttons,target,identity:{
   sameAnchors:!!(pane.isConnected&&gallery?.isConnected&&panes.length===1&&panes[0]===pane&&
    currentGalleries.length===1&&currentGalleries[0]===gallery&&gallery.closest('.a-messages')===pane),
   currentFrame:!!(frame?.isConnected&&preview?.isConnected&&fullPreview?.isConnected&&
    frame.parentElement===fullPreview&&fullPreview.closest('section.a-inline-artifact[data-image-frame]')===preview&&
    header?.parentElement===preview&&
    preview.parentElement===gallery&&frame.closest('.a-inline-gallery')===gallery),
   selectedTarget:!!(target&&buttons.length===targets.length&&targets.every(row=>
    buttons[row.slot-1].getAttribute('aria-label')===`Image ${row.slot}: ${row.title}`)&&
    (!observedEntries||observedEntries.includes(target.entry))&&(!expectedEntry||target.entry===expectedEntry)),
   previewCount:previews.length,fullPreviewCount:fullPreviews.length,headerCount:headers.length,
   frameCount:frames.length,railCount:rails.length,pressedCount:pressed.length,
   entry:target?.entry??null,resourceId:target?.resourceId??null,version:target?.version??null}};
 };
 const initialFrame=resolveFrame().frame;
 const rect=node=>{if(!node)return null;const r=node.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}};
 const snapshot=expectedEntry=>{
  const {frame,target,identity}=resolveFrame(expectedEntry);
  const images=frame?.querySelectorAll(':scope > .a-inline-image > img')||[],image=images.length===1?images[0]:null;
  const box=rect(gallery),main=rect(frame),p=rect(pane);
  const notices=[...pane.querySelectorAll('[data-part="work-projection-status"]')];
  return{available:true,identity:{...identity,frameIsInitial:!!frame&&frame===initialFrame,
   initialFrameConnected:!!initialFrame?.isConnected,imageMatches:images.length<=1&&(!image||image.getAttribute('alt')===target?.title)},
   pane:{...p,scrollTop:pane.scrollTop,scrollHeight:pane.scrollHeight,clientHeight:pane.clientHeight,clientWidth:pane.clientWidth},
   box,frame:main,workNotice:{count:notices.length,heights:notices.slice(0,4).map(n=>n.getBoundingClientRect().height),truncated:notices.length>4},
   selectedImage:image?{complete:image.complete,naturalWidth:image.naturalWidth,naturalHeight:image.naturalHeight}:null};
 };
 const valid=s=>s?.available===true&&['sameAnchors','currentFrame','selectedTarget','imageMatches'].every(k=>s.identity?.[k]===true)&&['pane','box','frame'].every(k=>
  s[k]&&['x','y','width','height'].every(f=>Number.isFinite(s[k][f]))&&s[k].width>0&&s[k].height>0)&&
  ['scrollTop','scrollHeight','clientHeight','clientWidth'].every(k=>Number.isFinite(s.pane[k]))&&
  s.pane.scrollTop>=0&&s.pane.scrollHeight>=s.pane.clientHeight&&s.pane.clientHeight>0&&s.pane.clientWidth>0;
 const compare=(before,after)=>{
  if(!valid(before)||!valid(after))return'invalid-geometry';
  if(['x','y','width','height','clientHeight','clientWidth'].some(k=>before.pane[k]!==after.pane[k]))return'pane-geometry';
  if(before.box.height!==after.box.height)return'gallery-height';
  if(before.frame.height!==after.frame.height)return'frame-height';
  const offset=(s,k,axis)=>s[k][axis]-s.pane[axis];
  const contentTop=(s,k)=>offset(s,k,'y')+s.pane.scrollTop;
  // With unchanged content geometry, retain the original raw-scroll control.
  if(before.pane.scrollHeight===after.pane.scrollHeight&&
   ['box','frame'].every(k=>Math.abs(contentTop(after,k)-contentTop(before,k))<1)&&
   Math.abs(after.pane.scrollTop-before.pane.scrollTop)>=1)return'raw-scroll';
  if(['x','y'].some(k=>Math.abs(offset(after,'box',k)-offset(before,'box',k))>=1))return'gallery-position';
  if(['x','y'].some(k=>Math.abs(offset(after,'frame',k)-offset(before,'frame',k))>=1))return'frame-position';
  return null;
 };
 let baseline,observer,timer,records=[],firstViolation=null,dropped=0,expired=false,last;
 const loaded=()=>record('image-load');
 const disconnect=()=>{observer?.disconnect();pane.removeEventListener('load',loaded,true);clearTimeout(timer);observer=null;timer=null};
 const record=label=>{
  const value=snapshot(),problem=compare(baseline,value),row={label,problem,value};
  if(problem&&!firstViolation)firstViolation=row;
  const signature=JSON.stringify(value);
  if(signature!==last){
   if(records.length<64)records.push(row);
   else{dropped++;firstViolation??={label:'capacity',problem:'observation-capacity',value:null};disconnect()}
   last=signature;
  }
  return{before:baseline,after:value,problem};
 };
 const begin=(before,entries=[before.identity.entry])=>{
  disconnect();baseline=before;records=[];firstViolation=null;dropped=0;expired=false;last=null;
  observedEntries=entries;
  record('start');
  // Observe pinned anchors and the current selected frame before a later frame
  // can conceal a shift. This is not an all-frame or physical-display sensor.
  observer=new MutationObserver(()=>record('mutation'));
  observer.observe(pane,{subtree:true,childList:true,characterData:true,attributes:true});
  pane.addEventListener('load',loaded,true);
  timer=setTimeout(()=>{expired=true;firstViolation??={label:'deadline',problem:'observation-deadline',value:null};disconnect()},30000);
 };
 const finish=()=>{
  if(baseline)record('finish');
  disconnect();observedEntries=null;
  return{records,firstViolation,dropped,expired};
 };
 // Fixed synthetic gallery/frame only. Keep absence distinct from an empty
 // attribute; bounded diagnostics do not normalize the strict restoration check.
 const attributeFact=value=>({present:value!==null,length:value?.length??0,
  value:value===null?null:value.length<=512?value:null,bounded:value===null||value.length<=512});
 const propertyFacts=node=>{
  const present=node.hasAttribute('style'),value=node.style.getPropertyValue('transform'),priority=node.style.getPropertyPriority('transform');
  return{present,transform:value.length<=128?value:null,transformLength:value.length,
   priority:['','important'].includes(priority)?priority:null,
   bounded:value.length<=128&&['','important'].includes(priority),
   attributeAfterPropertyRead:attributeFact(node.getAttribute('style'))};
 };
 const restorationFacts=(node,style,scroll,properties,actualStyle,actualScroll)=>{
  // These comparisons use the captured values from the original assertion
  // point, before reading CSSOM properties for the diagnostic.
  const facts={expectedAttribute:attributeFact(style),actualAttribute:attributeFact(actualStyle),
   expectedScroll:scroll,actualScroll,scrollExact:actualScroll===scroll,
   attributeExact:actualStyle===style,expectedProperties:properties,actualProperties:propertyFacts(node)};
  facts.bounded=facts.expectedAttribute.bounded&&facts.actualAttribute.bounded&&
   properties.bounded&&properties.attributeAfterPropertyRead.bounded&&
   facts.actualProperties.bounded&&facts.actualProperties.attributeAfterPropertyRead.bounded;
  return facts;
 };
 const controls=async()=>{
  const results=[],original=snapshot(),add=(name,before,after,expected)=>results.push({name,before,after,problem:compare(before,after),expected});
  if(!valid(original))throw Error('Preview controls require one valid selected frame');
  const {frame,preview,buttons}=resolveFrame();
  add('unchanged',original,snapshot(),null);
  // Only the retained raw-scroll/extent pair is synthesized; other fields
  // come from this actual snapshot. This is comparator, not layout, proof.
  const measured=structuredClone(original);measured.pane.scrollTop=1193;measured.pane.scrollHeight=2028;
  const compensated=structuredClone(measured);compensated.pane.scrollTop=1126;compensated.pane.scrollHeight=1961;
  add('retained-compensated-67',measured,compensated,null);
  add('missing',original,null,'invalid-geometry');
  const malformed=structuredClone(original);malformed.frame.height=null;
  add('malformed',original,malformed,'invalid-geometry');
  for(const kind of ['scroll','gallery','frame']){
   const before=snapshot(),node=kind==='gallery'?gallery:frame,style=node.getAttribute('style'),scroll=pane.scrollTop;
   const properties=propertyFacts(node);
   try{
    if(kind==='scroll')pane.scrollTop-=8;
    // Mutate and restore the same raw attribute boundary; keep null distinct.
    else node.setAttribute('style',`${style===null?'':style};transform:translateY(8px);`);
    add(kind,before,snapshot(),kind==='scroll'?'raw-scroll':kind==='gallery'?'gallery-position':'frame-position');
   }finally{
    if(kind==='scroll')pane.scrollTop=scroll;
    else if(style===null)node.removeAttribute('style');else node.setAttribute('style',style);
    add(kind+'-restored',before,snapshot(),null);
    const actualScroll=pane.scrollTop,actualStyle=node.getAttribute('style');
    results.at(-1).restorationExact=actualScroll===scroll&&actualStyle===style;
    results.at(-1).restorationFacts=restorationFacts(node,style,scroll,properties,actualStyle,actualScroll);
   }
  }
  const before=snapshot(),style=gallery.getAttribute('style'),properties=propertyFacts(gallery);
  begin(before);
  try{
   gallery.setAttribute('style',`${style===null?'':style};transform:translateY(8px);`);
   await Promise.resolve(); // Deliver the real mutation, not a stability wait.
  }finally{
   if(style===null)gallery.removeAttribute('style');else gallery.setAttribute('style',style);
   await Promise.resolve();
   add('shift-return-restored',before,snapshot(),null);
   const actualStyle=gallery.getAttribute('style'),actualScroll=pane.scrollTop;
   results.at(-1).restorationExact=actualStyle===style&&actualScroll===before.pane.scrollTop;
   results.at(-1).restorationFacts=restorationFacts(gallery,style,before.pane.scrollTop,properties,actualStyle,actualScroll);
  }
  const transient=finish();
  results.push({name:'shift-return-first-violation',problem:transient.firstViolation?.problem??null,expected:'gallery-position',boundaries:transient});
  // Replace only the frame shell. Move the existing loaded children rather than
  // cloning images/starting requests; restore React's exact nodes before clicking.
  const parent=frame.parentNode,next=frame.nextSibling,children=[...frame.childNodes];
  const replacement=frame.cloneNode(false),duplicate=frame.cloneNode(false);
  const frameStyle=frame.getAttribute('style'),frameProperties=propertyFacts(frame),previewLabel=preview.getAttribute('aria-label');
  const selections=buttons.map(button=>button.getAttribute('aria-pressed'));
  const restoreAttribute=(node,name,value)=>{if(value===null)node.removeAttribute(name);else node.setAttribute(name,value)};
  try{
   replacement.append(...children);frame.replaceWith(replacement);
   add('replacement',original,snapshot(),null);
   results.at(-1).replacementWitness={oldRetired:!frame.isConnected,currentConnected:replacement.isConnected,
    selectedFrame:resolveFrame().frame===replacement,childrenRetained:children.every((node,index)=>replacement.childNodes[index]===node)};
   replacement.remove();
   add('replacement-missing',original,snapshot(),'invalid-geometry');
   parent.insertBefore(replacement,next);pane.scrollTop=original.pane.scrollTop;
   parent.insertBefore(duplicate,next);
   add('replacement-duplicate',original,snapshot(),'invalid-geometry');
   duplicate.remove();pane.scrollTop=original.pane.scrollTop;
   preview.setAttribute('aria-label',targets[1].title);
   add('replacement-wrong-identity',original,snapshot(),'invalid-geometry');
   restoreAttribute(preview,'aria-label',previewLabel);
   buttons[0].setAttribute('aria-pressed','false');buttons[1].setAttribute('aria-pressed','true');
   add('replacement-wrong-selection',original,snapshot(),'invalid-geometry');
   buttons.forEach((button,index)=>restoreAttribute(button,'aria-pressed',selections[index]));
   const replacementStyle=replacement.getAttribute('style'),replacementProperties=propertyFacts(replacement);
   begin(original);
   try{
    replacement.setAttribute('style',`${replacementStyle===null?'':replacementStyle};transform:translateY(8px);`);
    add('replacement-shifted',original,snapshot(),'frame-position');
    await Promise.resolve(); // Deliver the shifted replacement's real mutation.
   }finally{
    restoreAttribute(replacement,'style',replacementStyle);
    await Promise.resolve();
    add('replacement-shift-return-restored',original,snapshot(),null);
    const actualStyle=replacement.getAttribute('style'),actualScroll=pane.scrollTop;
    results.at(-1).restorationExact=actualStyle===replacementStyle&&actualScroll===original.pane.scrollTop;
    results.at(-1).restorationFacts=restorationFacts(replacement,replacementStyle,original.pane.scrollTop,replacementProperties,actualStyle,actualScroll);
   }
   const replacedTransient=finish();
   results.push({name:'replacement-shift-return-first-violation',problem:replacedTransient.firstViolation?.problem??null,
    expected:'frame-position',boundaries:replacedTransient});
  }finally{
   disconnect();observedEntries=null;
   duplicate.remove();replacement.remove();frame.append(...children);parent.insertBefore(frame,next);
   restoreAttribute(frame,'style',frameStyle);restoreAttribute(preview,'aria-label',previewLabel);
   buttons.forEach((button,index)=>restoreAttribute(button,'aria-pressed',selections[index]));
   pane.scrollTop=original.pane.scrollTop;
   add('replacement-original-restored',original,snapshot(),null);
   const actualStyle=frame.getAttribute('style'),actualScroll=pane.scrollTop;
   results.at(-1).restorationExact=actualStyle===frameStyle&&actualScroll===original.pane.scrollTop&&
    resolveFrame().frame===frame&&frame.nextSibling===next&&!replacement.isConnected&&!duplicate.isConnected&&
    frame.childNodes.length===children.length&&children.every((node,index)=>frame.childNodes[index]===node)&&
    preview.getAttribute('aria-label')===previewLabel&&buttons.every((button,index)=>button.getAttribute('aria-pressed')===selections[index]);
   results.at(-1).restorationFacts=restorationFacts(frame,frameStyle,original.pane.scrollTop,frameProperties,actualStyle,actualScroll);
  }
  return results;
 };
 window.imagePreviewProbe={snapshot,compare,begin,record,finish,controls};
}
try{
 const boot=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Image fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Image fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});
 });
 if(process.env.AMPLIFIER_EXPECTED_PACKAGE)assert.equal(boot.package,process.env.AMPLIFIER_EXPECTED_PACKAGE+'/server.py');
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-generated-images'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const control=async(op,id)=>{const response=await page.request.post(boot.url+'/api/fixture/images',{data:{op,id}});assert.equal(response.status(),200);return response.json()};
 await page.goto(boot.url+'/login');await page.getByLabel('Username').fill('image-fixture');await page.getByLabel('Password').fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this unsent');
 // A persisted older appearance is loaded after application styles.
 const legacyCss=await readFile(root+'tests/fixtures/legacy_theme.css','utf8');
 await page.evaluate(css=>window.amplifier.dispatch('theme.apply',{name:'Older image layout',css:css+' #amp-one .a-inline-gallery{width:100%;grid-template-columns:minmax(0,1fr) 60px} #amp-one .a-image-generation[data-running]{width:100%;flex:1;aspect-ratio:1}'}),legacyCss);
 await control('start','first');
 const gallery=page.locator('.a-inline-gallery');
 const frame=gallery.locator('.a-gallery-image-frame');
 const geometry=async expectedEntry=>{
  const measured=await page.evaluate(entry=>{const probe=window.imagePreviewProbe,value=probe.snapshot(entry);return{value,problem:probe.compare(value,value)}},expectedEntry);
  const {box,frame:frameBox,pane}=measured.value;
  if(swapPhase){
   if(swapDiagnostic.records.length<3)swapDiagnostic.records.push({phase:swapPhase,expectedEntry,problem:measured.problem,
    original:{scroll:pane?.scrollTop??null,galleryTop:box?.y??null,galleryHeight:box?.height??null,frameTop:frameBox?.y??null,frameHeight:frameBox?.height??null},observation:measured.value});
   else swapDiagnostic.dropped++;
  }
  assertPlacement(measured,'Geometry must be present, finite and bound to '+expectedEntry);
  return{box,frame:frameBox,scroll:pane.scrollTop,snapshot:measured.value};
 };
 await expect(gallery).toBeVisible();
 const single=await frame.boundingBox();assert.ok(single.width<=320);
 await control('start','second');await control('start','third');
 await expect(gallery).toHaveAttribute('aria-label','Image gallery, 3 images');
 await gallery.scrollIntoViewIfNeeded();
 const rail=gallery.locator('.a-inline-gallery-choices');
 await expect(rail.locator('[data-running]')).toHaveCount(3);
 assert.equal((await frame.boundingBox()).width,single.width,'Adding slots does not resize the main image');
 const generating=page.locator('.a-image-generation[data-running]');
 await expect(generating).toHaveCount(1);
 await expect(generating.first()).toHaveText('Creating image…');
 assert.ok(await page.evaluate(()=>{
  const work=document.querySelector('.a-execution-turn'),image=document.querySelector('.a-image-generation');
  return work&&!!(work.compareDocumentPosition(image)&Node.DOCUMENT_POSITION_FOLLOWING);
 }),'Image progress follows the work that started it');
 const placeholder=await generating.first().boundingBox();
 assert.ok(Math.abs(placeholder.width-placeholder.height)<1,'Generation has an image-shaped placeholder');
 assert.ok(placeholder.width<=321,'Generation stays compact instead of flex-growing to the chat width');

 await page.screenshot({path:'/tmp/unified-generated-images-running.png'});
 assert.notEqual(await generating.first().locator('svg').evaluate(node=>getComputedStyle(node).animationName),'none');
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await generating.first().locator('svg').evaluate(node=>getComputedStyle(node).animationName),'none');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Keep this unsent');
 await control('later');
 // Complete a later slot first; selection and all three positions stay put.
 const runningBox=await frame.boundingBox();
 await control('finish','second');
 await expect(rail.locator('[data-running]')).toHaveCount(2);
 await expect(rail.locator('button').nth(1).locator('img')).toBeVisible();
 await expect(generating).toHaveCount(1);
 assert.equal((await frame.boundingBox()).height,runningBox.height);
 const first=(await control('finish','first')).result;
 await expect(generating).toHaveCount(0);
 await expect(rail.locator('[data-running]')).toHaveCount(1);
 assert.ok((await gallery.boundingBox()).width<=388,'Gallery stays compact with its side rail');
 await gallery.scrollIntoViewIfNeeded();await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 await control('finish','third');await control('stop');
 await expect(rail.locator('[data-running]')).toHaveCount(0);
 await expect(rail.locator('img')).toHaveCount(3);
 await expect.poll(()=>rail.locator('img').evaluateAll(images=>images.every(image=>image.complete&&image.naturalWidth>0))).toBe(true);
 await expect(gallery).toHaveAttribute('aria-label','Image gallery, 3 images');
 assert.equal((await frame.boundingBox()).height,runningBox.height);

 assert.ok(await page.evaluate(()=>{
  const work=document.querySelector('.a-execution-turn'),image=document.querySelector('.a-inline-gallery'),later=document.querySelector('[data-message-id="later"]');
  return work&&later&&!!(work.compareDocumentPosition(image)&Node.DOCUMENT_POSITION_FOLLOWING)&&!!(image.compareDocumentPosition(later)&Node.DOCUMENT_POSITION_FOLLOWING);
 }),'Saved image follows its work and stays before the next request');

 const publication=await page.evaluate(id=>window.amplifier.getState().canvasArtifacts.find(row=>row.id===id),first.canvasId);
 assert.deepEqual(publication.publications,[{messageId:'origin',version:1}]);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
 // Freeze only the three synthetic public identities, not artifact bodies or URLs.
 // Bind the current card and full preview's public resource/version markers to
 // the visible title, version and pressed slot.
 const targets=await page.evaluate(firstId=>{
  const state=window.amplifier.getState();
  return ['first','second','third'].map((entry,index)=>{
   const rows=state.canvasArtifacts.filter(row=>row.sessionId===state.selectedSessionId&&row.kind==='image'&&row.imageRequestId===entry);
   if(rows.length!==1)throw Error('Expected one synthetic image: '+entry);
   const row=rows[0],links=(row.publications||[]).filter(link=>link.messageId==='origin'),versions=(row.versions||[]).filter(version=>version.version===1);
   if(links.length!==1||links[0].version!==1||versions.length!==1||entry==='first'&&row.id!==firstId)throw Error('Unexpected synthetic publication: '+entry);
   const title=versions[0].title||row.title;
   if(typeof row.id!=='string'||!row.id.length||row.id.length>128||typeof title!=='string'||!title.length||title.length>128)throw Error('Unbounded synthetic identity');
   return{entry,slot:index+1,resourceId:row.id,title,version:1};
  });
 },first.canvasId);
 assert.equal(new Set(targets.map(row=>row.title)).size,3,'Synthetic public titles must distinguish entries');
 assert.equal(targets[1].title,'Second concept');assert.equal(targets[2].title,'Third concept');
 await page.locator('.a-messages').evaluate(installPreviewProbe,targets);
 swapDiagnostic.initial=await page.evaluate(()=>{const probe=window.imagePreviewProbe,value=probe.snapshot('first');return{value,problem:probe.compare(value,value)}});
 assertPlacement(swapDiagnostic.initial,'Controls target the first synthetic entry');
 swapDiagnostic.controls=await page.evaluate(()=>window.imagePreviewProbe.controls());
 for(const result of swapDiagnostic.controls){
  if(result.replacementWitness)for(const [name,value] of Object.entries(result.replacementWitness))assert.equal(value,true,'Replacement witness: '+name);
  if(result.boundaries){assert.equal(result.boundaries.dropped,0,'Control observations retained: '+result.name);assert.equal(result.boundaries.expired,false,'Control observation deadline: '+result.name)}
  if(result.name.endsWith('-restored'))assert.equal(result.restorationFacts.bounded,true,'Bounded restoration facts: '+result.name);
  if(result.name.endsWith('-restored'))assert.equal(result.restorationExact,true,'Restore original scroll/style: '+result.name);
  if(result.expected===null)assertPlacement(result,'Placement control: '+result.name);
  else assert.throws(()=>assertPlacement(result,'Placement control: '+result.name),
   error=>error instanceof assert.AssertionError&&error.actual===result.expected&&error.expected===null,
   'The actual comparator must reject exactly '+result.name);
 }
 assert.equal(swapDiagnostic.controls.length,21,'All 12 original and 9 replacement controls ran');
 // Hold the resource request to measure the loading frame and scroll position.
 let entered;
 const waiting=new Promise(resolve=>{entered=resolve}),gate=new Promise(resolve=>{releaseSwapGate=resolve});
 await page.route('**/api/canvas/views/*/resource?*',async route=>{entered();await gate;await route.continue()});
 await gallery.scrollIntoViewIfNeeded();
 swapPhase='before';
 const before=await geometry('first');
 swapPhase=null;
 assert.ok(before.scroll>100,'Exercise image swapping from a scrolled reading position');
 await page.evaluate(value=>window.imagePreviewProbe.begin(value,['first','second']),before.snapshot);
 swapObserving=true;
 await gallery.getByRole('button',{name:'Image 2: Second concept',exact:true}).click();
 await waiting;
 await expect(gallery.getByText('Loading preview…')).toBeVisible();
 swapPhase='held';
 const during=await geometry('second');
 swapPhase=null;
 assert.equal(during.box.height,before.box.height,'No collapse during delayed loading');
 assertPlacement(await page.evaluate(([before,after])=>({problem:window.imagePreviewProbe.compare(before,after)}),
  [before.snapshot,during.snapshot]),'No visible gallery/frame jump during delayed loading');
 releaseSwapGate();await page.unrouteAll({behavior:'wait'});
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 // Decode readiness is not a layout-stability wait: the same baseline and
 // first-violation observer remain active throughout, with no tolerance reset.
 await expect.poll(()=>gallery.locator('.a-inline-image img').evaluate(image=>
  image.complete&&image.naturalWidth>0&&image.naturalHeight>0)).toBe(true);
 swapPhase='released';
 const after=await geometry('second');
 swapPhase=null;
 assert.equal(after.box.height,before.box.height,'Landscape uses the same frame');
 assertPlacement(await page.evaluate(([before,after])=>({problem:window.imagePreviewProbe.compare(before,after)}),
  [before.snapshot,after.snapshot]),'No visible gallery/frame jump when loading completes');
 swapDiagnostic.boundaries=await page.evaluate(()=>window.imagePreviewProbe.finish());
 swapObserving=false;
 assert.equal(swapDiagnostic.boundaries.dropped,0,'No lost mutation-boundary observations');
 assert.equal(swapDiagnostic.boundaries.expired,false,'Observation stayed within its original action budget');
 assertPlacement({problem:swapDiagnostic.boundaries.firstViolation?.problem??null},
  'No gallery/frame movement at an observed mutation boundary');
 await page.route('**/api/canvas/views/*/resource?*',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Preview temporarily unavailable'})}));
 await gallery.getByRole('button',{name:'Image 3: Third concept',exact:true}).click();
 await expect(gallery.getByRole('button',{name:'Try preview again',exact:true})).toBeVisible();
 assert.equal((await geometry('third')).box.height,before.box.height,'Error keeps the same frame');
 await page.unrouteAll({behavior:'wait'});
 await gallery.getByRole('button',{name:'Try preview again',exact:true}).click();
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 assert.equal((await geometry('third')).box.height,before.box.height,'Portrait uses the same frame');
 await gallery.getByRole('button',{name:'Image 2: Second concept',exact:true}).click();
 await gallery.getByRole('button',{name:'Open Second concept in canvas',exact:true}).first().click();
 await expect(page.locator('.a-canvas-workspace img').first()).toBeVisible();
 await page.evaluate(async()=>{await window.amplifier.dispatch('view.update',{patch:{canvasFocused:false}});await window.amplifier.dispatch('canvas.close')});
 await page.screenshot({path:'/tmp/unified-generated-images-complete.png'});
 await control('start','uncertain');await gallery.getByRole('button',{name:'Image 4: Creating image…',exact:true}).click();await expect(generating).toHaveCount(1);
 await control('unknown','uncertain');await expect(generating).toHaveCount(0);
 await expect(page.locator('.a-image-generation')).toHaveText('Image generation stopped; check activity for its outcome');
 await control('stop');await page.reload();
 await gallery.scrollIntoViewIfNeeded();
 await expect(rail.locator('img')).toHaveCount(3);
 await gallery.getByRole('button',{name:'Image 4: Image generation stopped; check activity for its outcome',exact:true}).click();
 await expect(page.locator('.a-image-generation')).toHaveText('Image generation stopped; check activity for its outcome');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Keep this unsent');
 await page.setViewportSize({width:390,height:844});
 await expect(gallery).toHaveAttribute('aria-label','Image gallery, 4 images');
 await gallery.getByRole('button',{name:/^Image 1:/}).click();
 await gallery.scrollIntoViewIfNeeded();
 await expect(gallery.locator('.a-inline-image img')).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:'/tmp/unified-generated-images-mobile.png'});
 await control('delegated-start');
 await gallery.getByRole('button',{name:'Image 5: Creating image…',exact:true}).click();
 await expect(generating).toHaveCount(1);
 await expect(generating).toHaveText('Creating image…');
 await page.reload();await gallery.getByRole('button',{name:'Image 5: Creating image…',exact:true}).click();await expect(generating).toHaveCount(1);
 await control('delegated-finish');await expect(generating).toHaveCount(0);
 await expect(page.locator('.a-image-generation').filter({hasText:'Image generated'})).toHaveCount(1);
 await control('stop');await page.reload();await expect(generating).toHaveCount(0);
 assert.deepEqual(errors,[]);
 console.log('Generated images passed: stable pending/result gallery slots, out-of-order completion, delayed landscape/portrait swaps preserve visible gallery/frame positions with bounded mutation observations, older saved appearance, automatic native receipt display without a model attach action, reduced motion, exact original turn, shared gallery and full view, unsent draft retained, unknown outcome settled across reload, mobile containment. Synthetic receipts; no model/image API calls.');
}catch(error){primaryFailure=error;throw error}
finally{
 const cleanupErrors=[];
 const cleanup=async(label,work)=>{
  let timer;
  try{
   await Promise.race([work(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Owned cleanup deadline: '+label)),5000)})]);
   swapDiagnostic.cleanup.push({label,status:'completed'});
  }catch(error){cleanupErrors.push(error);swapDiagnostic.cleanup.push({label,status:'incomplete'})}
  finally{clearTimeout(timer)}
 };
 if(swapObserving)await cleanup('capture-and-disconnect',async()=>{swapDiagnostic.boundaries=await page.evaluate(()=>window.imagePreviewProbe.finish())});
 // Releasing this owned network gate is not a replay of an app command.
 releaseSwapGate?.();
 if(page)await cleanup('routes',()=>page.unrouteAll({behavior:'wait'}));
 if(browser)await cleanup('browser',()=>browser.close());
 await cleanup('fixture',async()=>{
  if(fixture.exitCode===null&&fixture.signalCode===null){
   const exited=new Promise(resolve=>fixture.once('exit',resolve));
   fixture.kill('SIGTERM');
   await exited;
  }
  assert.equal(fixture.exitCode,0,'Owned fixture must exit normally');
  assert.equal(fixture.signalCode,null,'Owned fixture must not require a signal kill');
 });
 if(fixture.exitCode===null&&fixture.signalCode===null){
  fixture.kill('SIGKILL');
  await cleanup('fixture-reap',()=>new Promise(resolve=>{if(fixture.exitCode!==null||fixture.signalCode!==null)resolve();else fixture.once('exit',resolve)}));
 }
 console.log('GENERATED_IMAGE_SWAP_DIAGNOSTIC '+JSON.stringify(swapDiagnostic));
 if(cleanupErrors.length)throw new AggregateError([...(primaryFailure?[primaryFailure]:[]),...cleanupErrors],'Generated image owned cleanup incomplete');
}
