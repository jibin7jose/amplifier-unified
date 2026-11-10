import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {bindWindowControlsOverlay,validTitlebarRect} from '../src/window-controls-overlay.js';

function fixture({api=true}={}){
  const overlay=new EventTarget(),win=new EventTarget(),attributes=new Map(),styles=new Map(),frames=new Map();
  let next=0,rect={x:80,y:0,width:920,height:32};
  overlay.visible=true;overlay.getTitlebarAreaRect=()=>rect;
  Object.assign(win,{navigator:api?{windowControlsOverlay:overlay}:{},innerWidth:1000,innerHeight:800,
    requestAnimationFrame:fn=>{frames.set(++next,fn);return next},cancelAnimationFrame:id=>frames.delete(id)});
  const root={ownerDocument:{defaultView:win},style:{setProperty:(key,value)=>styles.set(key,value),removeProperty:key=>styles.delete(key)},
    setAttribute:(key,value)=>attributes.set(key,value),removeAttribute:key=>attributes.delete(key)};
  return {root,win,overlay,attributes,styles,frames,setRect:value=>{rect=value},change:()=>overlay.dispatchEvent(new Event('geometrychange')),
    flush:()=>{const callbacks=[...frames.values()];frames.clear();for(const fn of callbacks)fn()}};
}

test('visible valid geometry sets precise variables and supports both native-control sides',()=>{
  const f=fixture(),dispose=bindWindowControlsOverlay(f.root);
  assert.equal(f.attributes.get('data-window-controls-overlay'),'true');
  assert.equal(f.styles.get('--wco-x'),'80px');assert.equal(f.styles.get('--wco-safe-top'),'48px');
  f.setRect({x:0,y:12,width:860,height:52});f.change();f.flush();
  assert.equal(f.styles.get('--wco-y'),'12px');assert.equal(f.styles.get('--wco-width'),'860px');
  assert.equal(f.styles.get('--wco-height'),'52px');assert.equal(f.styles.get('--wco-safe-top'),'64px');
  assert.equal(f.attributes.get('data-window-controls-overlay-stacked'),'false');
  f.setRect({x:0,y:8,width:860,height:32});f.change();f.flush();
  assert.equal(f.attributes.get('data-window-controls-overlay-stacked'),'true');
  assert.equal(f.styles.get('--wco-safe-top'),'48px');
  dispose();assert.equal(f.attributes.size,0);assert.equal(f.styles.size,0);
});

test('absent API and hidden/throwing geometry never guess overlay clearance',()=>{
  const unsupported=fixture({api:false});bindWindowControlsOverlay(unsupported.root)();
  assert.equal(unsupported.attributes.size,0);assert.equal(unsupported.styles.size,0);
  const f=fixture(),dispose=bindWindowControlsOverlay(f.root);
  f.overlay.visible=false;f.change();f.flush();assert.equal(f.attributes.size,0);assert.equal(f.styles.size,0);
  f.overlay.visible=true;f.overlay.getTitlebarAreaRect=()=>{throw Error('Unavailable')};f.change();f.flush();
  assert.equal(f.attributes.size,0);
  f.overlay.getTitlebarAreaRect=()=>({x:0,y:0,width:900,height:32});f.change();f.flush();
  assert.equal(f.attributes.get('data-window-controls-overlay'),'true');dispose();
});

test('finite, positive, viewport-bounded geometry only; malformed geometry recovers',()=>{
  const valid={x:80,y:0,width:920,height:32};
  const bad=[null,{...valid,x:-1},{...valid,y:-1},{...valid,x:NaN},{...valid,y:Infinity},{...valid,width:0},
    {...valid,width:'920'},{...valid,width:921},{...valid,height:0},{...valid,height:161},{...valid,y:161},
    {...valid,y:150,height:32}];
  for(const rect of bad){const height=rect?.y===150?160:800;assert.equal(validTitlebarRect(rect,1000,height),false,JSON.stringify(rect))}
  const f=fixture(),dispose=bindWindowControlsOverlay(f.root);
  f.setRect({...valid,width:Infinity});f.change();f.flush();assert.equal(f.attributes.size,0);assert.equal(f.styles.size,0);
  f.setRect(valid);f.change();f.flush();assert.equal(f.attributes.get('data-window-controls-overlay'),'true');dispose();
});

test('resize/geometry events coalesce, narrow windows stack, teardown cancels pending work',()=>{
  const f=fixture(),dispose=bindWindowControlsOverlay(f.root);
  f.win.innerWidth=320;f.setRect({x:80,y:0,width:240,height:32});
  f.win.dispatchEvent(new Event('resize'));f.change();f.change();assert.equal(f.frames.size,1);f.flush();
  assert.equal(f.attributes.get('data-window-controls-overlay-stacked'),'true');
  f.win.innerWidth=1280;f.setRect({x:0,y:0,width:1120,height:24});f.change();f.flush();
  assert.equal(f.attributes.get('data-window-controls-overlay-stacked'),'true','A short native strip must not squeeze app targets');
  f.setRect({x:0,y:0,width:1120,height:48});f.change();f.flush();
  assert.equal(f.attributes.get('data-window-controls-overlay-stacked'),'false');
  f.change();assert.equal(f.frames.size,1);dispose();assert.equal(f.frames.size,0);
  f.change();f.win.dispatchEvent(new Event('resize'));f.flush();assert.equal(f.attributes.size,0);assert.equal(f.styles.size,0);
  // React Strict Mode may bind again immediately after teardown.
  const again=bindWindowControlsOverlay(f.root);assert.equal(f.attributes.get('data-window-controls-overlay'),'true');again();
});

test('manifest requests WCO before the standalone fallback',async()=>{
  const manifest=JSON.parse(await readFile(new URL('../public/manifest.webmanifest',import.meta.url),'utf8'));
  assert.deepEqual(manifest.display_override,['window-controls-overlay','standalone']);assert.equal(manifest.display,'standalone');
});
