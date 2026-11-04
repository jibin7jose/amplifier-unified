import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
// Run against the already-built production app. Only navigator's native geometry
// is simulated; never inject/rewrite the shipped CSS or claim OS drag acceptance.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {inflateSync} from 'node:zlib';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Window controls geometry fixture'});
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/window-controls-overlay';
const headerOnly=process.argv.includes('--header-clearance-only');
let browser,ordinaryBrowser,failure;
const launchOptions={headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]};
// Chromium screenshots encode RGB/RGBA PNGs. Decode only the one-row samples
// used here; compression and one-channel raster rounding are not visual seams.
function pngRow(png){
  let width,channels,offset=8;const chunks=[];
  while(offset<png.length){
    const length=png.readUInt32BE(offset),type=png.toString('ascii',offset+4,offset+8),data=png.subarray(offset+8,offset+8+length);
    if(type==='IHDR'){width=data.readUInt32BE(0);assert.equal(data.readUInt32BE(4),1);assert.equal(data[8],8);assert.ok([2,6].includes(data[9]));channels=data[9]===2?3:4;assert.equal(data[12],0)}
    if(type==='IDAT')chunks.push(data);offset+=length+12;
  }
  const raw=inflateSync(Buffer.concat(chunks)),filter=raw[0],row=Buffer.alloc(width*channels);
  assert.ok(filter<=4);assert.equal(raw.length,row.length+1);
  for(let i=0;i<row.length;i++){
    const left=i>=channels?row[i-channels]:0;
    row[i]=(raw[i+1]+(filter===1||filter===4?left:filter===3?Math.floor(left/2):0))&255;
  }
  return {width,channels,row};
}
function samePaint(actual,expected,label){
  const a=pngRow(actual),b=pngRow(expected);assert.equal(a.width,b.width);assert.equal(a.channels,b.channels);
  const delta=Math.max(...a.row.map((value,i)=>Math.abs(value-b.row[i])));
  assert.ok(delta<=1,label+'; maximum channel difference '+delta);
}
try{
  const url=await new Promise((resolve,reject)=>{
    let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
    fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
    fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
  });
  browser=await chromium.launch(launchOptions);await mkdir(out,{recursive:true});
  const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  await context.addInitScript(()=>{
    const overlay=new EventTarget();let rect={x:80,y:0,width:1200,height:32};
    overlay.visible=true;overlay.getTitlebarAreaRect=()=>rect;
    Object.defineProperty(navigator,'windowControlsOverlay',{configurable:true,value:overlay});
    window.__simulatedWco=(next,visible=true)=>{rect=next;overlay.visible=visible;overlay.dispatchEvent(new Event('geometrychange'))};
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const app=page.locator('#amp-one');
  const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  const presentation=patch=>page.evaluate(async patch=>{
    const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
    const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}});
    await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id});
  },patch);
  const geometry=async(rect,visible=true)=>{
    await page.evaluate(([rect,visible])=>window.__simulatedWco(rect,visible),[rect,visible]);
    if(!visible){await expect(app).not.toHaveAttribute('data-window-controls-overlay','true');return}
    await expect(app).toHaveAttribute('data-window-controls-overlay','true');
    await expect.poll(()=>app.evaluate(el=>el.style.getPropertyValue('--wco-width'))).toBe(rect.width+'px');
    await expect.poll(()=>app.evaluate(el=>el.style.getPropertyValue('--wco-y'))).toBe(rect.y+'px');
    await expect.poll(()=>app.evaluate(el=>el.style.getPropertyValue('--wco-height'))).toBe(rect.height+'px');
  };
  const clearHeader=async(selector,rect)=>{
    await expect.poll(()=>page.locator(selector).evaluate((header,rect)=>{
      const safe=Math.max(48,rect.y+rect.height),stacked=document.getElementById('amp-one').dataset.windowControlsOverlayStacked==='true';
      const b=header.getBoundingClientRect();
      return stacked?b.top>=safe-1:Math.abs(b.x)<1&&Math.abs(b.width-innerWidth)<1&&Math.abs(b.y-rect.y)<1;
    },rect)).toBe(true);
    const controls=await page.locator(selector).evaluate((header,rect)=>[...header.querySelectorAll('button,a,input,select,textarea,summary,[role=button]')].filter(el=>el.getClientRects().length).map(el=>{
      const b=el.getBoundingClientRect();return {name:el.getAttribute('aria-label')||el.textContent,box:{x:b.x,y:b.y,width:b.width,right:b.right},viewport:innerWidth,drag:getComputedStyle(el).getPropertyValue('-webkit-app-region'),fits:b.left>=-1&&b.right<=innerWidth+1,safe:b.top>=Math.max(48,rect.y+rect.height)-1||(b.left>=rect.x-1&&b.right<=rect.x+rect.width+1)};
    }),rect);
    for(const control of controls){assert.equal(control.drag,'no-drag',control.name);assert.ok(control.safe,'Native controls overlap '+control.name);assert.ok(control.fits,'Control clipped outside viewport: '+JSON.stringify({selector,rect,control}))}
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow');
  };
  const centeredChrome=async(selector,rect)=>{
    if(await app.getAttribute('data-window-controls-overlay-stacked')==='true')return;
    const facts=await page.locator(selector).evaluate((header,rect)=>{
      const center=rect.y+rect.height/2;
      return [...header.querySelectorAll('.a-work-brand img,.a-work-heading>strong,.a-work-heading>svg,button:not(.a-work-menu-items button):not(.a-canvas-tabs button),button>svg')]
        .filter(el=>el.getClientRects().length&&!el.closest('.a-work-menu-items'))
        .map(el=>{const b=el.getBoundingClientRect();return {name:el.getAttribute('aria-label')||el.tagName,delta:Math.abs(b.y+b.height/2-center),top:b.top,bottom:b.bottom}});
    },rect);
    assert.ok(facts.length>0,'No rendered chrome alignment targets');
    for(const fact of facts){
      assert.ok(fact.delta<=1,'Chrome not centered on native strip: '+JSON.stringify({selector,rect,fact}));
      assert.ok(fact.top>=rect.y-1&&fact.bottom<=rect.y+rect.height+1,'Chrome exceeds native strip: '+JSON.stringify(fact));
    }
  };
  const continuousChrome=async(selector,rect)=>{
    const facts=await page.locator(selector).evaluate(header=>{
      const style=getComputedStyle(header),box=header.getBoundingClientRect();
      return {x:box.x,y:box.y,width:box.width,height:box.height,background:style.backgroundColor,image:style.backgroundImage};
    });
    assert.equal(facts.x,0);assert.equal(facts.y,rect.y);
    assert.equal(facts.width,(await page.viewportSize()).width);assert.ok(facts.height>=Math.max(48,rect.y+rect.height)-rect.y);
    // Raster evidence, not just correct-looking computed styles: the bottom
    // of the band must actually paint under the reserved native side areas.
    const pixel=x=>page.screenshot({clip:{x,y:Math.max(48,rect.y+rect.height)-2,width:1,height:1}});
    const center=await pixel(rect.x+2),left=await pixel(2),right=await pixel((await page.viewportSize()).width-2);
    assert.deepEqual(left,center,'Unpainted band below left native controls');
    assert.deepEqual(right,center,'Unpainted band below right native controls');
  };
  await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
  await expect(app).toHaveAttribute('data-window-controls-overlay','true');
  const manifest=await page.evaluate(()=>fetch('/manifest.webmanifest').then(r=>r.json()));
  assert.deepEqual(manifest.display_override,['window-controls-overlay','standalone']);
  if(headerOnly){
    // The changed owner is header clearance. The existing full journey below
    // retains separate focused-Canvas/artwork/menu coverage and its failures.
    for(const width of [1280,800,390,320]){
      await page.setViewportSize({width,height:900});
      for(const rect of [{x:80,y:0,width:width-80,height:32},{x:86,y:0,width:width-180,height:38},{x:0,y:0,width:width-140,height:32},{x:80,y:0,width:width-80,height:24},{x:70,y:8,width:width-210,height:52}]){
        await geometry(rect);
        for(const reserved of [0,16]){
          await action('theme.preview',{name:'Header decoration clearance',css:`#amp-one{--a-header-decoration-height:${reserved}px}`});
          await clearHeader('.a-work-header',rect);await centeredChrome('.a-work-header',rect);
          const stacked=await app.getAttribute('data-window-controls-overlay-stacked')==='true';
          const actual=await page.locator('.a-work-header').evaluate(el=>el.getBoundingClientRect().height);
          assert.ok(actual>=Math.max(48,rect.y+rect.height)-rect.y+reserved-1,'Reserved strip must have layout space');
          if(!stacked)assert.equal(Math.round(actual),Math.max(48,rect.y+rect.height)-rect.y+reserved);
          await action('theme.revert');
        }
      }
    }
    assert.deepEqual(errors,[]);
    console.log('SIMULATED HEADER CLEARANCE passed: 0/16px decoration strips, 20 left/right/nonzero-y geometries at 320/390/800/1280px, native control centering unchanged. No focused-Canvas/OS-drag acceptance claimed.');
  }else{
  // WorkDialog is a real portal into the app, not a hand-authored fixture.
  await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Choose a workspace'})).toBeVisible();
  assert.ok((await page.locator('.a-work-dialog-backdrop').boundingBox()).y>=48);
  await page.getByRole('button',{name:'Close Choose a workspace',exact:true}).click();
  await action('session.create');
  await action('canvas.show',{kind:'html',title:'WCO retained viewer',content:'<h1>Retained viewer</h1><input aria-label="Retained note" value="Initial">'});
  const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('textbox',{name:'Retained note'}).fill('Do not remount');
  await page.evaluate(()=>{window.__retainedFrame=document.querySelector('.a-canvas-html')});
  // Sandboxed HTML has an opaque origin: inspect its identity inside the frame
  // rather than treating the parent's null contentDocument as proof.
  const documentIdentity=await frame.locator('html').evaluate(()=>{
    window.__wcoDocumentIdentity='retained-'+Date.now()+'-'+Math.random();return window.__wcoDocumentIdentity;
  });
  await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Unsent WCO draft');

  for(const width of [1280,800,390,320]){
    await page.setViewportSize({width,height:900});
    for(const rect of [{x:80,y:0,width:width-80,height:32},{x:86,y:0,width:width-180,height:38},{x:0,y:0,width:width-140,height:32},{x:80,y:0,width:width-80,height:24},{x:70,y:8,width:width-210,height:52}]){
      await geometry(rect);await clearHeader('.a-work-header',rect);await centeredChrome('.a-work-header',rect);
      await action('view.update',{patch:{canvasFocused:true}});await expect(page.locator('.a-canvas-panel')).toHaveAttribute('data-focused','true');
      await clearHeader('.a-canvas-panel[data-focused=true] .a-canvas-head',rect);await centeredChrome('.a-canvas-panel[data-focused=true] .a-canvas-head',rect);
      await expect(page.locator('.a-work-header')).toBeHidden();
      const exit=page.getByRole('button',{name:'Exit canvas focus',exact:true});
      assert.ok(await exit.evaluate(el=>{const b=el.getBoundingClientRect();return el.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2))}),'Focused header control is occluded');
      await expect(frame.getByRole('textbox',{name:'Retained note'})).toHaveValue('Do not remount');
      assert.ok(await page.evaluate(()=>window.__retainedFrame===document.querySelector('.a-canvas-html')),'Geometry/focus remounted iframe');
      assert.equal(await frame.locator('html').evaluate(()=>window.__wcoDocumentIdentity),documentIdentity,'Geometry/focus reloaded viewer document');
      await exit.click();await expect(page.locator('.a-canvas-panel')).toHaveAttribute('data-focused','false');
    }
    await action('view.update',{patch:{panel:'settings'}});await expect(page.locator('.a-settings-experience')).toBeVisible();
    const overlay=page.locator('.a-settings-redesign-overlay');
    await expect(page.locator('.a-work-header')).toHaveAttribute('inert','');
    await expect.poll(()=>overlay.evaluate(el=>el.getBoundingClientRect().top)).toBeGreaterThanOrEqual(60);
    if(width<960){
      // Simulate keyboard viewport movement using the existing hook's variables,
      // without rewriting a single shipped stylesheet rule.
      await overlay.evaluate(el=>{el.style.setProperty('--a-settings-viewport-top','20px');el.style.setProperty('--a-settings-viewport-height','500px')});
      await expect.poll(()=>overlay.evaluate(el=>el.getBoundingClientRect().height)).toBe(460);
      await overlay.evaluate(el=>{el.style.setProperty('--a-settings-viewport-top','80px');el.style.setProperty('--a-settings-viewport-height','500px')});
      await expect.poll(()=>overlay.evaluate(el=>el.getBoundingClientRect().top)).toBe(80);
      await expect.poll(()=>overlay.evaluate(el=>el.getBoundingClientRect().height)).toBe(500);
    }
    await page.screenshot({path:out+'/settings-'+width+'.png'});
    await action('view.update',{patch:{panel:null}});
  }
  await page.setViewportSize({width:1280,height:900});
  // Real MacBook report: both native ends are reserved, the OS strip is 38px,
  // and the compact app header is 48px. Keep the entire extra 10px continuous.
  const macRect={x:86,y:0,width:1100,height:38};await geometry(macRect);
  await presentation({scheme:'dark'});
  const aurora=(await action('theme.read',{id:'builtin:aurora'})).result;
  await action('theme.preview',{name:aurora.name,css:aurora.css});
  await expect(app).toHaveAttribute('data-window-chrome-blend','true');
  await continuousChrome('.a-work-header',macRect);
  await page.screenshot({path:out+'/mac-chrome-aurora-dark.png'});
  await action('view.update',{patch:{canvasFocused:true}});
  await continuousChrome('.a-canvas-panel[data-focused=true] .a-canvas-head',macRect);
  await action('view.update',{patch:{canvasFocused:false}});await action('theme.revert');
  // Test the reported boundary itself, not just paint inside the title band.
  // Temporarily hide existing layout content to sample the backdrop without
  // panel borders/shadows; no stylesheet or shipped paint rule is injected.
  const backdropImage='linear-gradient(90deg, rgb(70, 40, 100), rgb(30, 90, 110))';
  await action('theme.preview',{name:'Joined root artwork',css:`#amp-one{--a-bg:rgb(20,30,40);background-color:var(--a-bg);background-image:${backdropImage}}`});
  await expect(app).toHaveAttribute('data-window-chrome-blend','true');
  const layout=page.locator('#amp-one>.a-layout');await layout.evaluate(el=>el.hidden=true);
  const row=y=>page.screenshot({clip:{x:0,y,width:1280,height:1}});
  samePaint(await row(47),await row(48),'Header-to-root boundary still has a hard color step');
  const blend=page.locator('.a-window-chrome-blend');
  assert.equal(await blend.evaluate(el=>getComputedStyle(el).pointerEvents),'none');
  assert.equal(await app.evaluate(el=>getComputedStyle(el).backgroundImage),backdropImage,'Original artwork changed');
  const plain=await context.newPage();
  await plain.setContent(`<style>body{margin:0;background:${backdropImage}}</style>`);
  samePaint(await row(145),await plain.screenshot({clip:{x:0,y:145,width:1280,height:1}}),'Artwork beyond join changed');
  await page.screenshot({path:out+'/joined-background.png'});
  await layout.evaluate(el=>el.hidden=false);await plain.close();
  await page.screenshot({path:out+'/joined-background-with-content.png'});
  // Resolved header paint, not the palette token, owns the join color.
  await action('theme.preview',{name:'Matching paint outside palette',css:`#amp-one{background-color:rgb(80,90,100)!important;background-image:${backdropImage}}#amp-one .a-work-header{background:rgb(80,90,100)!important}`});
  await expect(app).toHaveAttribute('data-window-chrome-blend','true');
  await layout.evaluate(el=>el.hidden=true);
  samePaint(await row(47),await row(48),'Join incorrectly used the palette instead of painted header');
  await layout.evaluate(el=>el.hidden=false);
  await presentation({decorations:false});await expect(app).toHaveAttribute('data-window-chrome-blend','false');
  await presentation({decorations:true});await expect(app).toHaveAttribute('data-window-chrome-blend','true');
  await action('theme.preview',{name:'Explicit custom chrome',css:`#amp-one{--a-chrome-bg:rgb(20,30,40);background-image:${backdropImage}}`});
  await expect(app).toHaveAttribute('data-window-chrome-blend','false');
  await action('theme.preview',{name:'Header-local custom chrome',css:`#amp-one{background-image:${backdropImage}}#amp-one .a-work-header{--a-chrome-bg:var(--a-bg)}`});
  await expect(app).toHaveAttribute('data-window-chrome-blend','false');
  await action('theme.preview',{name:'Transparent custom header',css:`#amp-one{background-image:${backdropImage}}#amp-one .a-work-header{background:transparent!important}`});
  await expect(app).toHaveAttribute('data-window-chrome-blend','false');
  await action('theme.revert');
  // A single full-width paint also preserves gradient continuity. Compare
  // rendered pixels against the same gradient in a plain viewport-wide box.
  const gradient='linear-gradient(90deg, rgb(240, 20, 20), rgb(20, 20, 240))';
  await action('theme.preview',{name:'Gradient chrome regression',css:`#amp-one .a-work-header,#amp-one .a-canvas-panel[data-focused=true] .a-canvas-head{background:${gradient}!important}`});
  await expect.poll(()=>page.locator('.a-work-header').evaluate(el=>getComputedStyle(el).backgroundImage)).toBe(gradient);
  await expect(app).toHaveAttribute('data-window-chrome-blend','false');
  const reference=await context.newPage();
  await reference.setContent(`<style>body{margin:0}div{height:53px;background:${gradient}}</style><div></div>`);
  await page.mouse.move(640,700);
  await page.evaluate(()=>document.activeElement?.blur());
  const band={x:0,y:46,width:1280,height:1},expectedBand=await reference.screenshot({clip:band});
  await writeFile(out+'/gradient-expected.png',expectedBand);await page.screenshot({clip:band,path:out+'/gradient-actual.png'});
  samePaint(await page.screenshot({clip:band}),expectedBand,'WorkHeader gradient restarted at native boundary');
  await action('view.update',{patch:{canvasFocused:true}});
  await expect(page.locator('.a-canvas-panel')).toHaveAttribute('data-focused','true');
  await page.evaluate(()=>document.activeElement?.blur());
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  samePaint(await page.screenshot({clip:band}),expectedBand,'Focused Canvas gradient restarted at native boundary');
  await reference.close();await action('view.update',{patch:{canvasFocused:false}});await action('theme.revert');
  const rect={x:80,y:0,width:1200,height:32};await geometry(rect);
  const chatActions=page.getByRole('button',{name:'Chat',exact:true});
  await expect(chatActions.locator('.lucide-message-square-more')).toHaveCount(0);
  await expect(chatActions.locator('.lucide-sliders-horizontal')).toHaveCount(1);
  await chatActions.focus();await page.keyboard.press('Enter');
  const menu=page.getByRole('group',{name:'Chat',exact:true});await expect(menu).toBeVisible();
  await expect(menu.getByRole('button',{name:'Rename',exact:true})).toBeFocused();
  await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(chatActions).toBeFocused();
  await chatActions.click();await expect(menu).toBeVisible();
  assert.equal(await menu.evaluate(el=>getComputedStyle(el).getPropertyValue('-webkit-app-region')),'no-drag');
  await menu.getByRole('button',{name:'Chat info',exact:true}).click();
  await expect(page.locator('.a-overlay')).toBeVisible();assert.ok((await page.locator('.a-overlay').boundingBox()).y>=48);
  await expect(page.locator('.a-work-header')).toHaveAttribute('inert','');
  await action('view.update',{patch:{panel:null}});
  const palette=await action('theme.list');
  for(const id of ['builtin:default','builtin:atelier','builtin:aurora','builtin:graphite']){
    assert.ok(palette.result.items.some(row=>row.id===id));
    const theme=(await action('theme.read',{id})).result;
    for(const scheme of ['light','dark']){
      await presentation({scheme});await action('theme.preview',{name:theme.name,css:theme.css});
      await expect(app).toHaveAttribute('data-theme-scheme',scheme);await geometry(macRect);
      await clearHeader('.a-work-header',macRect);await centeredChrome('.a-work-header',macRect);
      await action('view.update',{patch:{canvasFocused:true}});await centeredChrome('.a-canvas-panel[data-focused=true] .a-canvas-head',macRect);
      await action('view.update',{patch:{canvasFocused:false}});
      await page.screenshot({path:out+'/'+id.split(':')[1]+'-'+scheme+'.png'});
      await geometry(rect);
      await action('theme.revert');
    }
  }
  // Persist a CSS skin that changes geometry as well as paint. Host safety rules
  // must win the former without flattening the latter, including after reload.
  const skin=(await action('theme.read',{id:'builtin:graphite'})).result.css+'\n#amp-one .a-work-header{position:relative;margin:0;width:100%;min-height:80px;background:rgb(21,37,51)!important}';
  const saved=(await action('theme.save',{name:'Saved WCO geometry skin',css:skin})).result;
  const savedTheme=(await action('theme.read',{id:saved.id})).result;
  await action('theme.apply',{name:savedTheme.name,css:savedTheme.css});await clearHeader('.a-work-header',rect);
  assert.equal(await page.locator('.a-work-header').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(21, 37, 51)');
  // Only interactive parts are excluded. Passive title text and the large
  // flex wrapper must remain drag surfaces, even in a saved custom skin.
  const dragFacts=await page.evaluate(()=>{
    const host=document.createElement('div');host.innerHTML='<span role="switch" tabindex="0">Switch</span><span role="menuitemradio">Choice</span><span contenteditable="true">Edit</span><span draggable="true">Move</span><summary>Disclosure</summary>';
    document.querySelector('.a-work-header').append(host);
    const facts=[...host.children].map(el=>getComputedStyle(el).getPropertyValue('-webkit-app-region'));host.remove();
    const region=selector=>getComputedStyle(document.querySelector(selector)).getPropertyValue('-webkit-app-region');
    return {facts,header:region('.a-work-header'),heading:region('.a-work-heading'),title:region('.a-work-heading strong'),actions:region('.a-work-header-actions'),select:getComputedStyle(document.querySelector('.a-work-heading strong')).userSelect};
  });
  assert.ok(dragFacts.facts.every(value=>value==='no-drag'));
  for(const key of ['header','heading','title','actions'])assert.equal(dragFacts[key],'drag',key+' must not block window movement');
  assert.equal(dragFacts.select,'none');
  for(const focused of [false,true]){
    await action('view.update',{patch:{canvasFocused:focused}});
    const selector=focused?'.a-canvas-panel[data-focused=true] .a-canvas-head':'.a-work-header';
    await page.locator(selector).evaluate(header=>{
      const host=document.createElement('div');host.id='label-regression';
      host.innerHTML='<label for="header-filter"><span>Filter regression</span></label><input id="header-filter"><label><input type="checkbox"><span>Checkbox regression</span></label>';
      header.append(host);
    });
    assert.ok((await page.locator('#label-regression label,#label-regression label *').evaluateAll(elements=>elements.map(el=>getComputedStyle(el).getPropertyValue('-webkit-app-region')))).every(value=>value==='no-drag'));
    await page.locator('#label-regression label[for]').click();await expect(page.locator('#header-filter')).toBeFocused();
    await page.locator('#label-regression label:not([for]) span').click();await expect(page.locator('#label-regression input[type=checkbox]')).toBeChecked();
    await page.locator('#label-regression').evaluate(el=>el.remove());
  }
  await action('view.update',{patch:{canvasFocused:false}});
  await geometry(rect,false);await expect(app).not.toHaveAttribute('data-window-controls-overlay','true');
  assert.equal(await app.evaluate(el=>el.style.getPropertyValue('--wco-safe-top')),'');
  assert.equal(await page.locator('.a-work-header').evaluate(el=>getComputedStyle(el).position),'relative');
  await geometry(rect);
  await page.evaluate(()=>window.__simulatedWco({x:0,y:0,width:Infinity,height:32}));
  await expect(app).not.toHaveAttribute('data-window-controls-overlay','true');await geometry(rect);
  await page.screenshot({path:out+'/saved-skin.png'});
  await expect(frame.getByRole('textbox',{name:'Retained note'})).toHaveValue('Do not remount');
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Unsent WCO draft');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe('Unsent WCO draft');
  await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();await clearHeader('.a-work-header',rect);
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Unsent WCO draft');
  assert.equal(await page.locator('.a-work-header').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(21, 37, 51)');
  ordinaryBrowser=await chromium.launch(launchOptions);
  const ordinary=await ordinaryBrowser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  await ordinary.goto(url);await showFixtureConversation(ordinary);await ordinary.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
  await expect(ordinary.locator('#amp-one')).not.toHaveAttribute('data-window-controls-overlay','true');await ordinary.close();
  assert.deepEqual(errors,[]);
  console.log('SIMULATED WCO GEOMETRY passed against shipped build: left/right/nonzero-y, 320–1280px, off/malformed recovery, menus/dialogs/compact Settings, retained viewer and saved appearance reload. Native installed launch, OS controls and dragging NOT verified.');
  }
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Window controls geometry fixture',close:[['Ordinary browser',()=>ordinaryBrowser?.close()],['WCO browser',()=>browser?.close()]],stop,originalError:failure})}
