// Authored for the exact-source browser-test runner. Build production assets first.
// Required: AMPLIFIER_TEST_PYTHON and AMPLIFIER_TEST_ARTIFACTS from executionContract.
// No Git dependency, source dev server, live host, real provider, saved-theme edits,
// injected DOM/classes, replaced app functions, or fixture-internal state writes.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON,out=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(python,'Set AMPLIFIER_TEST_PYTHON to the prepared interpreter; no host .venv fallback');
assert.ok(out,'Set AMPLIFIER_TEST_ARTIFACTS to the owned evidence directory');
await mkdir(out,{recursive:true});
const hash=value=>createHash('sha256').update(value).digest('hex');
const report={runtime:'synthetic',productionAssets:true,liveProvider:false,cases:[],screenshots:[],
 errors:[],httpErrors:[],externalRequests:[],limits:[
  'Chromium only; synthetic fixture messages and settings, not personal conversations or private appearances.',
  'Controlled streaming is unavailable in empty_host_ui_server --chat-controls. No stream-growth qualification claimed.',
  'Computed colors/contrast, geometry and bounded screenshot comparisons are checks, not human aesthetic approval.',
 ]};
let fixture,stop,browser,page,failure,stderr='',watchdog;
const shot=async name=>{
 assert.ok(report.screenshots.length<12,'Evidence screenshot cap');
 const file=name+'.png',bytes=await page.screenshot({path:join(out,file)});
 report.screenshots.push(file);return bytes;
};
// CSS lists have nested commas; never split gradients on every comma.
const cssList=value=>{let depth=0,start=0;const rows=[];
 for(let i=0;i<value.length;i++){if(value[i]==='(')depth++;else if(value[i]===')')depth--;
  else if(value[i]===','&&!depth){rows.push(value.slice(start,i).trim());start=i+1}}
 rows.push(value.slice(start).trim());return rows;
};
const transparentGradient=value=>value.includes('linear-gradient')&&/transparent|rgba\([^)]*,\s*0\)/.test(value);
const rgb=value=>{const m=value.match(/^rgba?\(([^)]+)\)$/);assert.ok(m,'Unsupported computed color: '+value);
 const channels=m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
 assert.ok(channels.length===3||channels.length===4);return [...channels.slice(0,3),channels[3]??1];
};
const over=(front,back)=>{const a=front[3]+back[3]*(1-front[3]);
 return [...front.slice(0,3).map((c,i)=>(c*front[3]+back[i]*back[3]*(1-front[3]))/a),a];
};
const ratio=(a,b)=>{const lum=c=>c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
 .reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
const hexRgb=hex=>`rgb(${[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(', ')})`;
try{
 // Guest Git metadata is deliberately absent. Retain the admitted source identity
 // and hashes of current inputs instead of invoking git in the copied snapshot.
 const contract=JSON.parse(await readFile(process.env.AMPLIFIER_TEST_CONTRACT||
  '/opt/unified-prepared/execution-contract.json','utf8'));
 assert.equal(contract.profileId,'browser-test');assert.equal(contract.python,python);
 assert.equal(contract.source.gitMetadataAvailable,false);
 report.executionContract={version:contract.version,profileId:contract.profileId,source:contract.source};
 report.sourceSha256={};
 for(const file of ['frontend/tests/lightline-shared-browser.mjs','frontend/src/chat-scroll.js',
  'frontend/src/conversation-rail.css','amplifier_web/appearances/lightline.amplifier.css',
  'amplifier_web/appearances/digital-rain.amplifier.css','amplifier_web/theme_library.py'])
  report.sourceSha256[file]=hash(await readFile(join(root,file)));
 report.build=JSON.parse(await readFile(join(root,'amplifier_web/static/build.json'),'utf8'));
 fixture=spawn(python,[join(root,'tests/fixtures/empty_host_ui_server.py'),'--chat-controls'],
  {cwd:root,stdio:['ignore','pipe','pipe']});
 stop=ownFixture(fixture,{label:'Lightline shared fixture'});
 fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-24000)});
 const url=await new Promise((resolve,reject)=>{
  let pending='',settled=false;
  const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value)};
  const timer=setTimeout(()=>finish(Error('Lightline fixture startup timeout')),20000);
  fixture.once('error',error=>finish(error));
  fixture.once('exit',(code,signal)=>finish(Error(`Fixture exited before readiness: ${code??signal}`)));
  fixture.stdout.on('data',chunk=>{
   pending=(pending+chunk).slice(-24000);const lines=pending.split('\n');pending=lines.pop();
   for(const line of lines){let row;try{row=JSON.parse(line)}catch{continue}if(row.url)finish(null,row.url)}
  });
 });
 const origin=new URL(url).origin;
 browser=await chromium.launch({headless:true,ignoreDefaultArgs:['--hide-scrollbars'],
  args:['--disable-features=OverlayScrollbar,FluentOverlayScrollbar',...JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]')],
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1,colorScheme:'light',
  reducedMotion:'no-preference',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(20000);
 watchdog=setTimeout(()=>{report.deadlineExceeded=true;void page.close().catch(()=>{})},180000);
 // Refuse accidental external requests. Do not ignore page errors or failed local HTTP responses.
 await page.route('**/*',route=>{
  const target=new URL(route.request().url());
  if(['http:','https:'].includes(target.protocol)&&target.origin!==origin){
   if(report.externalRequests.length<20)report.externalRequests.push(target.origin+target.pathname);
   return route.abort('blockedbyclient');
  }
  return route.continue();
 });
 page.on('pageerror',error=>{if(report.errors.length<30)report.errors.push(error.message)});
 page.on('response',response=>{if(response.status()>=400&&report.httpErrors.length<30)
  report.httpErrors.push({path:new URL(response.url()).pathname,status:response.status()})});
 const action=async(name,args={})=>{
  const receipt=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  assert.equal(receipt.accepted,true,`${name}: ${JSON.stringify(receipt.error||receipt)}`);return receipt;
 };
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const presentation=patch=>page.evaluate(async patch=>{
  const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,
   composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}});
  if(!prepared.accepted)throw Error('Presentation prepare refused: '+JSON.stringify(prepared.error));
  const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id});
  if(!applied.accepted)throw Error('Presentation apply refused: '+JSON.stringify(applied.error));
 },patch);
 const settled=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const pane=page.locator('[data-part="messages"]'),app=page.locator('#amp-one');
 const input=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const latest=page.getByRole('button',{name:'Jump to latest messages',exact:true});
 const work=page.locator('[data-part="work-browser"]:visible');
 const noOverflow=async()=>{
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page horizontal overflow');
  const scroller=await work.count()?work:pane;
  assert.ok(await scroller.evaluate(el=>el.scrollWidth<=el.clientWidth+1),'No surface horizontal overflow');
 };
 const colors=async(locator,label,minimum=4.5)=>{
  const sample=await locator.evaluate(el=>{
   const s=getComputedStyle(el),ancestors=[];
   for(let node=el;node;node=node.parentElement){const v=getComputedStyle(node);
    ancestors.push({background:v.backgroundColor,image:v.backgroundImage,opacity:Number(v.opacity)})}
   return {color:s.color,outline:s.outlineColor,outlineStyle:s.outlineStyle,outlineWidth:parseFloat(s.outlineWidth),
    outlineOffset:parseFloat(s.outlineOffset),focusVisible:el.matches(':focus-visible'),ancestors};
  });
  let bg=[255,255,255,1];for(const row of [...sample.ancestors].reverse())bg=over(rgb(row.background),bg);
  const opacity=sample.ancestors.reduce((v,row)=>v*row.opacity,1);
  const fg=rgb(sample.color);fg[3]*=opacity;const contrast=ratio(over(fg,bg),bg);
  assert.ok(contrast>=minimum,`${label}: actual contrast ${contrast.toFixed(2)} < ${minimum}`);
  return {label,contrast,foreground:sample.color,background:bg,opacity,...sample};
 };
 const keyboardFocus=async(locator,label)=>{
  await locator.focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
  await expect(locator).toBeFocused();await expect(locator).toBeInViewport();
  const value=await colors(locator,label);
  assert.equal(value.focusVisible,true,label+' receives keyboard :focus-visible');
  assert.notEqual(value.outlineStyle,'none');assert.ok(value.outlineWidth>=2);
  assert.ok(value.outlineOffset>=0,label+' focus ring is outside the control');
  // An offset ring sits on the surrounding surface, not the button's own fill.
  // Composite the ancestor paint separately; reject an uncovered image rather
  // than inventing flat-color contrast over artwork.
  const surrounding=value.ancestors.slice(1),opaque=surrounding.findIndex(row=>rgb(row.background)[3]===1);
  assert.ok(opaque>=0,label+' surrounding paint has an opaque backing');
  const visible=surrounding.slice(0,opaque+1);
  assert.ok(visible.every(row=>row.image==='none'),label+' surrounding ring paint is not image-dependent');
  let background=[255,255,255,1];for(const row of [...visible].reverse())background=over(rgb(row.background),background);
  const ring=rgb(value.outline);ring[3]*=value.opacity;
  const outlineContrast=ratio(over(ring,background),background);
  assert.ok(outlineContrast>=3,`${label} external focus outline contrast ${outlineContrast.toFixed(2)}`);
  return {label,contrast:value.contrast,outline:value.outline,outlineWidth:value.outlineWidth,
   surroundingBackground:background,outlineContrast};
 };
 const apply=async theme=>{
  await action('theme.apply',{name:theme.name,css:theme.css});
  await expect.poll(async()=>hash((await state()).theme.css)).toBe(hash(theme.css));
  await page.waitForFunction(css=>[...document.querySelectorAll('#amp-one > style')].some(node=>node.textContent===css),theme.css);
 };
 const fade=()=>pane.evaluate(el=>{
  const s=getComputedStyle(el),box=el.getBoundingClientRect();
  return {top:el.scrollTop,remaining:el.scrollHeight-el.clientHeight-el.scrollTop,
   above:el.dataset.overflowAbove,below:el.dataset.overflowBelow,focus:el.matches(':focus-within'),
   mask:s.maskImage,size:s.maskSize,position:s.maskPosition,repeat:s.maskRepeat,
   composite:s.maskComposite,
   gutter:el.offsetWidth-el.clientWidth-parseFloat(s.borderLeftWidth)-parseFloat(s.borderRightWidth),
   gutterInsets:{left:parseFloat(s.getPropertyValue('--_a-transcript-gutter-left')),
    right:parseFloat(s.getPropertyValue('--_a-transcript-gutter-right'))},
   measuredInsets:{left:el.clientLeft-parseFloat(s.borderLeftWidth),
    right:el.offsetWidth-el.clientLeft-el.clientWidth-parseFloat(s.borderRightWidth)},
   box:{x:box.x,y:box.y,width:box.width,height:box.height},borderRight:parseFloat(s.borderRightWidth)};
 });
 const position=async where=>{
  await input.focus(); // Focus outside the transcript restores the native mask.
  // Latest is an existing flex sibling: crossing its 80px threshold changes
  // the available scroll range. One bounded second placement uses that settled
  // native range, so "remaining 1" cannot accidentally qualify at remaining 0.
  let requested;
  for(let pass=0;pass<2;pass++){
   requested=await pane.evaluate((el,where)=>{
    const max=el.scrollHeight-el.clientHeight;
    const target=where==='top'?0:where==='middle'?Math.floor(max/2):where==='end'?max:
     typeof where==='number'?where:max-where.remaining;
    el.scrollTop=target;return Math.max(0,Math.min(max,target));
   },where);
   await settled();
  }
  await expect.poll(async()=>{const f=await fade();return f.above===String(f.top>8)&&f.below===String(f.remaining>1)}).toBe(true);
  const value=await fade();assert.ok(Math.abs(value.top-requested)<.5,'Requested real scroll position settled');
  if(where&&typeof where==='object')assert.ok(Math.abs(value.remaining-where.remaining)<.5,'Exact remaining-distance boundary');
  return value;
 };
 const checkFade=async(label,where,{above,below,top=18,bottom=24}={})=>{
  const value=await position(where);assert.equal(value.above,String(above),label+' above');
  assert.equal(value.below,String(below),label+' below');
  await expect(latest).toHaveCount(value.remaining>80?1:0);
  if(!above&&!below)assert.equal(value.mask,'none',label+' has no unnecessary mask');
  else{
   const layers=cssList(value.mask);assert.ok(layers.length<=2,label+' one edge gradient plus optional solid gutter');
   // The actual shared owner uses a vertical edge gradient and a full-width
   // horizontal stencil, transparent in the middle and solid over both gutters.
   const edges=layers.filter(layer=>transparentGradient(layer)&&!/\bto right\b|\b90deg\b/.test(layer));
   assert.equal(edges.length,1,label+' one combined transparent vertical edge gradient');
   // Browsers can retain mixed length/percentage math as min(18px, 50%).
   // Match the length token, not a particular space before a bare stop.
   assert.equal(new RegExp(`(?:^|[ (])${top}px(?=[ ,)])`).test(edges[0]),above,label+' top fade distance');
   assert.equal(edges[0].includes(`${bottom}px`),below,label+' bottom fade distance');
  }
  report.cases.push({kind:'fade',label,...value});return value;
 };
 const layout=()=>page.locator('[data-part="conversation"]').evaluate(el=>{
  const selectors={messages:'[data-part="messages"]',composer:'form[data-part="composer"]',
   navigator:'.a-conversation-rail',latest:'.a-chat-jump'};
  return Object.fromEntries(Object.entries(selectors).map(([key,selector])=>{
   const node=el.querySelector(selector);if(!node)return [key,null];
   const s=getComputedStyle(node),r=node.getBoundingClientRect();
   return [key,{sibling:node.parentElement===el,position:s.position,display:s.display,flex:s.flex,
    x:r.x,y:r.y,width:r.width,height:r.height}];
  }));
 });
 const bubbles=async(scheme,width,theme)=>{
  const palette=Object.fromEntries([...theme.css.matchAll(/(--[\w-]+):light-dark\((#[\da-f]+),(#[\da-f]+)\)/gi)]
   .map(([,name,light,dark])=>[name,scheme==='light'?light:dark]));
  const rows=await pane.locator('.a-message.a-user:has(> .a-message-actions),.a-message.a-assistant:has(> .a-message-actions)')
   .evaluateAll(nodes=>nodes.slice(0,12).map(el=>{
    const body=el.querySelector(':scope > .a-message-body'),footer=el.querySelector(':scope > .a-message-actions');
    const s=getComputedStyle(el),b=getComputedStyle(body),f=getComputedStyle(footer);
    return {role:el.classList.contains('a-user')?'user':'assistant',outer:s.backgroundColor,border:s.borderTopWidth,
     padding:s.paddingTop,shadow:s.boxShadow,body:b.backgroundColor,ink:b.color,bodyBorder:b.borderTopWidth,
     bodyPadding:b.paddingTop,font:b.fontFamily,footer:f.backgroundColor,footerImage:f.backgroundImage,
     footerBorder:f.borderTopWidth,bodyBottom:body.getBoundingClientRect().bottom,footerTop:footer.getBoundingClientRect().top};
   }));
  assert.ok(rows.some(row=>row.role==='user')&&rows.some(row=>row.role==='assistant'),'Actual user and assistant footer-bearing messages');
  for(const row of rows){
   assert.equal(row.outer,'rgba(0, 0, 0, 0)');assert.equal(row.border,'0px');assert.equal(row.padding,'0px');assert.equal(row.shadow,'none');
   assert.equal(row.body,hexRgb(palette[row.role==='user'?'--ll-user':'--a-surface']));
   assert.equal(row.ink,hexRgb(palette[row.role==='user'?'--ll-user-ink':'--a-ink']));
   assert.equal(row.bodyBorder,'1px');assert.match(row.font,/mono|Menlo|Consolas/i);
   assert.equal(row.footer,'rgba(0, 0, 0, 0)');assert.equal(row.footerImage,'none');assert.equal(row.footerBorder,'0px');
   assert.ok(row.footerTop>=row.bodyBottom,'Actions and metadata stay beneath, outside bubble paint');
   if(row.role==='assistant')assert.equal(row.bodyPadding,width===390?'12px':'16px');
   assert.ok(ratio(rgb(row.ink),rgb(row.body))>=4.5,'Actual bubble text contrast');
  }
  return rows;
 };
 const media=async(surface)=>{
  const target=surface==='chat'?page.locator('[data-part="conversation"]'):work;
  const paint=()=>target.evaluate(el=>{const s=getComputedStyle(el);return {image:s.backgroundImage,position:s.backgroundPosition,animation:s.animationName}});
  const normal=await paint();assert.equal(normal.animation,'none','Lightline paint is static');
  await settled();assert.deepEqual(await paint(),normal,'Static paint across actual frames');
  await page.emulateMedia({reducedMotion:'reduce'});await settled();assert.deepEqual(await paint(),normal);
  let baseline;
  if(surface==='chat'){
   await checkFade('decorations-on middle','middle',{above:true,below:true});
   const middle=await layout();
   await checkFade('decorations-on end','end',{above:true,below:false});
   baseline={middle,end:await layout()};
  }
  await presentation({decorations:false});await expect(app).toHaveAttribute('data-decorations','off');
  await expect.poll(async()=>(await paint()).image).toBe('none');
  const disabled=await paint();assert.equal(disabled.animation,'none');
  if(surface==='chat'){
   await checkFade('decorations-off middle','middle',{above:true,below:true});
   assert.deepEqual(await layout(),baseline.middle,'Artwork off preserves native middle geometry and functional fade');
   await checkFade('decorations-off end','end',{above:true,below:false});
   assert.deepEqual(await layout(),baseline.end,'Artwork off leaves final content opaque without moving controls');
  }
  await presentation({decorations:true});await page.emulateMedia({reducedMotion:'no-preference'});
  await expect.poll(paint).toEqual(normal);
  report.cases.push({kind:'media',surface,scheme:await app.getAttribute('data-theme-scheme'),normal,disabled});
 };

 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 assert.deepEqual(await (await page.request.get(url+'/build.json')).json(),report.build,'Loaded production build identity');
 const initial=await state(),listing=(await action('theme.list')).result;
 const lightline=(await action('theme.read',{id:'builtin:lightline'})).result;
 const rain=(await action('theme.read',{id:'builtin:digital-rain'})).result;
 const original=(await action('theme.read',{id:'builtin:default'})).result;
 assert.equal(listing.items.filter(row=>row.id==='builtin:lightline').length,1);
 assert.equal(lightline.source,'built-in');assert.equal(lightline.name,'Lightline');
 assert.equal(lightline.css,await readFile(join(root,'amplifier_web/appearances/lightline.amplifier.css'),'utf8'));
 assert.equal(lightline.fingerprint,hash(lightline.css));report.themeSha256=hash(lightline.css);
 assert.deepEqual((await state()).theme,initial.theme,'Listing/reading Lightline does not select or apply it');
 assert.equal((await state()).selectedSessionId,initial.selectedSessionId);
 assert.doesNotMatch(lightline.css,/mask-image\s*:/i,'Lightline inherits the shared primitive');
 assert.doesNotMatch(rain.css,/mask-image\s*:/i,'Digital Rain no longer owns a duplicate transcript mask');
 // Fixed dark colors protect the accepted dark palette, while the revised light
 // palette is checked by material/readability constraints rather than old cyan values.
 for(const [token,dark] of Object.entries({'--a-bg':'#050a10','--a-surface':'#0c151e','--a-soft':'#12222e',
  '--a-ink':'#e1f1f7','--a-muted':'#9fb7c6','--a-line':'#355064','--a-accent':'#79e6ff','--a-tint':'#123441',
  '--a-green':'#91dbb6','--a-danger':'#ffb2ba','--a-on-accent':'#041b25','--ll-user':'#2a2117',
  '--ll-user-ink':'#ffe2b5','--ll-amber':'#e9b46c','--ll-edge':'#438ba4','--ll-trace':'#79e6ff12',
  '--ll-glow':'#79e6ff16','--ll-sheen':'#e1f1f709','--ll-header':'#08121a'}))
  assert.ok(lightline.css.includes(`${token}:light-dark(`)&&new RegExp(`${token}:light-dark\\([^,]+,${dark}\\)`).test(lightline.css),token+' accepted dark value');
 await openSettingsPage(page,'appearance');
 await expect(page.getByRole('button',{name:'Use Lightline',exact:true})).toBeVisible();
 assert.deepEqual((await state()).theme,initial.theme,'Gallery availability alone does not apply Lightline');
 await page.getByRole('button',{name:'Use Lightline',exact:true}).click();
 await expect.poll(async()=>hash((await state()).theme.css)).toBe(report.themeSha256);
 await page.waitForFunction(css=>[...document.querySelectorAll('#amp-one > style')].some(node=>node.textContent===css),lightline.css);
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 report.cases.push({kind:'library',id:lightline.id,availableWithoutSelection:true,appliedFromGallery:true,sha256:report.themeSha256});
 const projectPath=join(dirname((await state()).workspaceDefaults.root),'synthetic-lightline');
 await mkdir(projectPath);await action('workspace.add',{path:projectPath,name:'Synthetic Lightline project'});
 const project=(await action('workspace.list',{query:'Synthetic Lightline project'})).result.items.find(row=>row.path===projectPath);
 assert.ok(project);await action('workspace.pin',{id:project.id,pinned:true});
 const create=async title=>{const receipt=await action('session.create',{workspace:projectPath,title,select:false});
  const id=receipt.sessionId||receipt.result?.sessionId;assert.ok(id);return id};
 const longId=await create('Synthetic material review'),shortId=await create('Synthetic short chat');
 await action('session.select',{id:longId});await showFixtureConversation(page,{navPinned:false});
 for(let i=0;i<4;i++){
  const before=await pane.locator('.a-message.a-assistant').count();
  await input.fill(`Synthetic review ${i+1}\n\n`+Array.from({length:7},(_,j)=>
   `Section ${j+1}. White lacquer, clear acrylic and platinum details keep this synthetic reading surface calm. Graphite text keeps each control legible.`).join('\n\n'));
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await expect(pane.locator('.a-message.a-assistant')).toHaveCount(before+1);
  await expect(input).toHaveDraft('');
 }
 const draft='Retain this synthetic draft across appearance, media and chat changes.';await input.fill(draft);
 const enter=async surface=>{
  if(surface==='chat'){await showFixtureConversation(page,{navPinned:false});return}
  await action('view.update',{patch:{workSurface:surface,workWorkspaceId:project.id,workWorkspaceTab:'chats',
   workHomeScope:'all',workHomeFilter:'all',workHomeQuery:'',navPinned:false,navExpanded:false}});
  await expect(work).toHaveAttribute('data-landing',surface);
  await work.getByRole('group',{name:'Work view',exact:true}).getByRole('button',{name:'Brief',exact:true}).click();
  await expect(work).toHaveAttribute('data-view','brief');
  await expect.poll(()=>work.locator('.a-work-card,.a-landing-chat').count()).toBeGreaterThan(0);
 };
 for(const scheme of ['light','dark']){
  await presentation({scheme,decorations:true});await expect(app).toHaveAttribute('data-theme-scheme',scheme);
  for(const width of [1600,390]){
   report.current={scheme,width};await page.setViewportSize({width,height:1000});
   for(const surface of ['home','workspace']){
    await enter(surface);await noOverflow();
    const focus=await keyboardFocus(work.getByRole('group',{name:'Work view',exact:true})
     .getByRole('button',{name:'Brief',exact:true}),`${surface} work-view`);
    if(width===1600){await media(surface);await shot(`lightline-${surface}-${scheme}`)}
    report.cases.push({kind:'landing',scheme,width,surface,focus});
   }
   await enter('chat');await expect(input).toHaveDraft(draft);await noOverflow();
   if(width===390)await expect(page.getByRole('navigation',{name:'Chat navigator',exact:true})).toBeHidden();
   else await expect(page.getByRole('navigation',{name:'Chat navigator',exact:true})).toBeVisible();
   const painted=await bubbles(scheme,width,lightline);
   const controls=await keyboardFocus(page.getByRole('button',{name:'Send message',exact:true}),'Send message');
   await input.focus();await colors(input,'Composer text');
   const composerFocus=await page.locator('[data-part="composer-surface"]').evaluate(el=>{
    const s=getComputedStyle(el);return {style:s.outlineStyle,width:parseFloat(s.outlineWidth)};
   });
   assert.notEqual(composerFocus.style,'none');assert.ok(composerFocus.width>=2,'Writing surface has a visible focus boundary');
   const actual=await app.evaluate(el=>{const bg=getComputedStyle(el).backgroundColor;
    return {background:bg,logo:getComputedStyle(el.querySelector('.a-brand img')).filter}});
   if(scheme==='light'){
    const channels=rgb(actual.background).slice(0,3);assert.ok(channels.every(v=>v>=220)&&Math.max(...channels)-Math.min(...channels)<=10,'Neutral luminous light background');
    const assistant=painted.find(row=>row.role==='assistant'),white=rgb(assistant.body).slice(0,3),graphite=rgb(assistant.ink).slice(0,3);
    assert.ok(white.every(v=>v>=245)&&Math.max(...white)-Math.min(...white)<=5,'Neutral luminous white reading surface');
    assert.ok(graphite.every(v=>v<=60)&&Math.max(...graphite)-Math.min(...graphite)<=20,'Graphite reading text, not pastel ink');
   }else assert.equal(actual.background,'rgb(5, 10, 16)');
   assert.notEqual(actual.logo,'none','Normal light/dark logo treatment remains');
   await checkFade(`${scheme}-${width}-top`,'top',{above:false,below:true});
   await checkFade(`${scheme}-${width}-middle`,'middle',{above:true,below:true});
   await checkFade(`${scheme}-${width}-end`,'end',{above:true,below:false});
   await expect(pane.locator('.a-message.a-assistant').last()).toBeInViewport();
   if(width===1600)await media('chat');
   await shot(`lightline-chat-${scheme}-${width}`);
   // Sidebar opens through shared view state, never by fabricating classes.
   await action('view.update',{patch:{navExpanded:true,navPinned:false,navMode:'browse'}});
   const nav=page.locator('[data-part="navigation"]');await expect(nav.locator(':scope > .a-nav-rail')).toBeVisible();
   await expect.poll(()=>nav.locator('.a-nav-chat:not(.a-nav-chat-rename):visible').count()).toBeGreaterThan(0);
   await expect.poll(()=>nav.locator('.a-workspace-row:visible').count()).toBeGreaterThan(0);
   const capsules=await nav.locator('.a-nav-chat:not(.a-nav-chat-rename):visible,.a-workspace-row:visible').evaluateAll(nodes=>
    nodes.slice(0,12).map(el=>{
     const r=el.getBoundingClientRect(),label=el.querySelector('.a-nav-chat-label,.a-workspace-label').getBoundingClientRect();
     return {kind:el.classList.contains('a-workspace-row')?'workspace':'chat',
      start:getComputedStyle(el).paddingInlineStart,end:getComputedStyle(el).paddingInlineEnd,
      labelInsetStart:label.left-r.left,labelInsetEnd:r.right-label.right};
    }));
   for(const row of capsules){
    assert.equal(row.start,'8px');assert.equal(row.end,'4px');
    assert.ok(row.labelInsetStart>=7.5&&row.labelInsetEnd>=3.5,'Actual capsule text stays inside its padded row');
   }
   await colors(nav.getByRole('group',{name:'Sidebar view',exact:true}).getByRole('button',{name:'Browse',exact:true}),'Sidebar selected');
   await colors(nav.getByRole('group',{name:'Sidebar view',exact:true}).getByRole('button',{name:'Status',exact:true}),'Sidebar unselected');
   await action('view.update',{patch:{navExpanded:false}});
   report.cases.push({kind:'chat',scheme,width,painted,controls,logo:actual.logo,capsules});
  }
 }

 await page.setViewportSize({width:1600,height:1000});await presentation({scheme:'dark'});
 await enter('chat');await input.focus();
 const native={};
 for(const theme of [original,lightline,rain]){
  await apply(theme);const current=await checkFade(`${theme.name} native middle`,'middle',{above:true,below:true});
  native[theme.name]=await layout();
  assert.equal(native[theme.name].navigator.position,'absolute');
  for(const name of ['messages','composer','navigator','latest'])assert.equal(native[theme.name][name].sibling,true,name+' retains its native sibling owner');
  assert.equal(native[theme.name].latest.position,native[original.name].latest.position);
  assert.equal(native[theme.name].latest.flex,native[original.name].latest.flex);
  const before=await layout();
  await pane.locator('.a-message-actions button').last().evaluate(el=>el.focus({preventScroll:true}));
  await expect.poll(async()=>(await fade()).mask).toBe('none');
  assert.deepEqual(await layout(),before,'Focus suppresses paint only, without moving composer/navigator/Latest siblings');
  await input.focus();await expect.poll(async()=>(await fade()).mask).not.toBe('none');
  report.cases.push({kind:'native-inheritance',theme:theme.name,geometry:before,fade:current});
 }
 await apply(lightline);
 await checkFade('above threshold 8',8,{above:false,below:true});
 await checkFade('above threshold 9',9,{above:true,below:true});
 await checkFade('below threshold 1',{remaining:1},{above:true,below:false});
 await checkFade('below threshold 2',{remaining:2},{above:true,below:true});
 await checkFade('Latest threshold 80',{remaining:80},{above:true,below:true});
 await checkFade('Latest threshold 81',{remaining:81},{above:true,below:true});
 // Optional theme tokens travel through the real theme action and affect only
 // this disposable fixture's applied CSS, not any saved library entry.
 await apply({name:'Synthetic fade sizes',css:lightline.css+'\n#amp-one{--a-transcript-fade-top:9px;--a-transcript-fade-bottom:13px}'});
 await checkFade('author tokens','middle',{above:true,below:true,top:9,bottom:13});await apply(lightline);

 // Force a classic scrollbar in launch configuration, measure its actual width,
 // then compare masked vs focus-suppressed pixels. No arbitrary gutter width or
 // screenshot-derived tap coordinates, and no DOM/CSS paint modifications.
 const masked=await checkFade('classic gutter near top',9,{above:true,below:true});
 assert.ok(masked.gutter>=3,'Classic scrollbar unavailable: this runner cannot qualify the solid gutter');
 const masks=cssList(masked.mask);
 assert.equal(masks.length,2,'Combined edge gradient plus a solid scrollbar-gutter layer');
 const gutterLayer=masks.find(layer=>/\bto right\b|\b90deg\b/.test(layer));assert.ok(gutterLayer,'Horizontal gutter stencil');
 const stops=cssList(gutterLayer.slice(gutterLayer.indexOf('(')+1,-1));
 assert.equal(stops.length,7,'Two opaque gutter regions and a transparent content region');
 assert.match(stops[1],/^rgb\(0, 0, 0\)/);assert.match(stops[2],/^rgb\(0, 0, 0\)/);
 assert.match(stops[3],/^rgba\(0, 0, 0, 0\)/);assert.match(stops[4],/^rgba\(0, 0, 0, 0\)/);
 assert.match(stops[5],/^rgb\(0, 0, 0\)/);assert.match(stops[6],/^rgb\(0, 0, 0\)/);
 for(const side of ['left','right'])assert.ok(Math.abs(masked.gutterInsets[side]-masked.measuredInsets[side])<=1,
  side+' gutter stencil follows the measured native gutter, not a theme-supplied guess');
 assert.ok(masked.composite.split(',').every(value=>value.trim()==='add'),'Edge and gutter masks add, not intersect');
 await page.mouse.move(0,0);await settled();const maskedBytes=await shot('lightline-gutter-masked');
 const beforeFocus=await layout();
 await pane.locator('.a-message-actions button').last().evaluate(el=>el.focus({preventScroll:true}));
 await expect.poll(async()=>(await fade()).mask).toBe('none');
 assert.deepEqual(await layout(),beforeFocus);assert.equal((await fade()).top,masked.top);
 const focusedBytes=await shot('lightline-gutter-focused');
 const gutterPixels=await page.evaluate(async({a,b,geometry})=>{
  const decode=async base64=>{const image=new Image();image.src='data:image/png;base64,'+base64;await image.decode();
   const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
   const context=canvas.getContext('2d');context.drawImage(image,0,0);return context.getImageData(0,0,canvas.width,canvas.height)};
  const [first,second]=await Promise.all([decode(a),decode(b)]),r=geometry.box;
  const left=Math.ceil(r.x+r.width-geometry.borderRight-geometry.gutter)+1,right=Math.floor(r.x+r.width-geometry.borderRight)-1;
  let pixels=0,maxDelta=0;
  for(const [start,end] of [[Math.ceil(r.y)+2,Math.ceil(r.y)+17],[Math.floor(r.y+r.height)-23,Math.floor(r.y+r.height)-2]])
   for(let y=start;y<end;y++)for(let x=left;x<right;x++){pixels++;
    for(let c=0;c<3;c++){const i=(y*first.width+x)*4+c;maxDelta=Math.max(maxDelta,Math.abs(first.data[i]-second.data[i]))}}
  return {pixels,maxDelta,measuredWidth:geometry.gutter};
 },{a:maskedBytes.toString('base64'),b:focusedBytes.toString('base64'),geometry:masked});
 assert.ok(gutterPixels.pixels>0);assert.ok(gutterPixels.maxDelta<=2,'Top and bottom scrollbar pixels must not fade');
 report.cases.push({kind:'classic-gutter',...gutterPixels,mask:masked.mask,size:masked.size});
 await input.focus();await position('middle');
 const normalLayout=await layout();
 await page.emulateMedia({forcedColors:'active'});
 await expect.poll(async()=>(await fade()).mask).toBe('none');
 await expect.poll(()=>app.locator('.a-brand img').evaluate(el=>getComputedStyle(el).filter)).toBe('none');
 assert.deepEqual(await layout(),normalLayout,'Forced colors suppresses paint, not native layout');
 await shot('lightline-forced-colors-dark');await page.emulateMedia({forcedColors:'none'});
 await expect.poll(async()=>(await fade()).mask).not.toBe('none');

 // Preserve a visible message/offset during actual viewport reflow and chat
 // switching. Position via scrolling only; no private controller access.
 const anchorNode=pane.locator('.a-message.a-user').nth(1);
 await anchorNode.evaluate(el=>{const pane=el.closest('[data-part="messages"]');
  pane.scrollTop+=el.getBoundingClientRect().top-pane.getBoundingClientRect().top+24});await settled();
 const reading=()=>pane.evaluate(el=>{const top=el.getBoundingClientRect().top;
  const row=[...el.querySelectorAll('[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>top);
  assertDefined(row);return {id:row.dataset.messageId,offset:row.getBoundingClientRect().top-top};
  function assertDefined(row){if(!row)throw Error('No real message for reading anchor')}
 });
 const anchor=await reading();assert.ok(anchor.id);
 for(const width of [1200,1600]){
  await page.setViewportSize({width,height:1000});await settled();
  await expect.poll(async()=>{const current=await reading();return current.id===anchor.id&&Math.abs(current.offset-anchor.offset)<3}).toBe(true);
 }
 await action('session.select',{id:shortId});await showFixtureConversation(page,{navPinned:false});
 await input.fill('Synthetic short exchange.');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(pane.locator('.a-message.a-assistant')).toHaveCount(1);
 await expect.poll(async()=>(await fade()).remaining).toBeLessThanOrEqual(1);
 await checkFade('short transcript','top',{above:false,below:false});
 await action('session.select',{id:longId});await showFixtureConversation(page,{navPinned:false});
 await expect(input).toHaveDraft(draft);
 await expect.poll(async()=>{const current=await reading();return current.id===anchor.id&&Math.abs(current.offset-anchor.offset)<3}).toBe(true);
 report.cases.push({kind:'reading-anchor',anchor,after:await reading(),resizeWidths:[1200,1600],chatSwitch:true});
 const inspection=await (await page.request.get(url+'/fixture')).json();
 assert.equal(inspection.sent.length,5,'Exactly four long-chat sends and one short-chat send, all synthetic');
 assert.deepEqual((await action('theme.list')).result.items.filter(row=>row.source!=='built-in'),
  listing.items.filter(row=>row.source!=='built-in'),'Saved appearances remain untouched');
 assert.equal(hash((await state()).theme.css),report.themeSha256);
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.httpErrors,[]);assert.deepEqual(report.externalRequests,[]);
 delete report.current;
}catch(error){
 failure=error;report.failure=error.stack||String(error);
 if(page&&!page.isClosed()&&report.screenshots.length<12)try{await shot('failure')}catch(captureError){report.failureCaptureError=captureError.message}
}finally{
 clearTimeout(watchdog);
 if(stop)try{
  await finishFixture({label:'Lightline shared fixture',close:[['Lightline shared browser',async()=>{
   if(browser){await browser.close();report.browserClosed=true}
  }]],stop:async()=>{const exit=await stop();report.fixtureExit={code:exit.code,signal:exit.signal,closed:true}}});
 }catch(error){report.cleanupError=error.stack||String(error);failure ||= error}
 if(!failure&&(report.errors.length||report.httpErrors.length||report.externalRequests.length))
  failure=Error('Browser errors arrived after the final assertion; inspect report.json');
 report.passed=!failure;
 if(failure)await writeFile(join(out,'fixture-stderr.log'),stderr);
 await writeFile(join(out,'report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({passed:report.passed,cases:report.cases.length,screenshots:report.screenshots,
  fixtureExit:report.fixtureExit,limits:report.limits}));
}
if(failure)throw failure;
