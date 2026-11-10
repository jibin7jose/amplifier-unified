import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const script=readFileSync(new URL('../public/pwa.js',import.meta.url),'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
class Target {
 constructor(){this.events=new Map()}
 addEventListener(name,fn){this.events.set(name,[...(this.events.get(name)||[]),fn])}
 emit(name){for(const fn of this.events.get(name)||[])fn()}
}
function harness({offline=true,path='/',fetcher=async()=>{throw Error('offline')}}={}){
 const window=new Target(),document=new Target(),retry=new Target(),status={},origin={};
 const timers=new Map(),calls=[],navigation=[];let next=0;
 document.hidden=false;document.querySelector=selector=>({'[data-offline-recovery]':offline?{}:null,'[data-offline-status]':status,'[data-offline-retry]':retry,'[data-offline-origin]':origin})[selector];
 const context={window,document,navigator:{},location:{origin:'https://fixture.invalid',pathname:path,replace:value=>navigation.push(value)},AbortController,
  setTimeout:(fn,delay)=>{timers.set(++next,{fn,delay});return next},clearTimeout:id=>timers.delete(id),
  fetch:(url,options)=>{calls.push({url,options});return fetcher(url,options)}};
 vm.runInNewContext(script,context);
 const fire=delay=>{const row=[...timers].find(([,value])=>value.delay===delay);assert.ok(row,`timer ${delay}`);timers.delete(row[0]);row[1].fn()};
 return {window,document,retry,status,origin,timers,calls,navigation,fire};
}
const health=()=>({ok:true,json:async()=>({ok:true,app:'amplifier-unified'})});
const html=()=>({ok:true,headers:new Map([['content-type','text/html; charset=utf-8']])});
test('active chat pages do not probe or navigate',async()=>{
 const h=harness({offline:false});h.window.emit('pageshow');h.window.emit('online');await flush();
 assert.equal(h.calls.length,0);assert.equal(h.navigation.length,0);assert.equal(h.timers.size,0);
});
test('failed probes back off to a cap; foreground/manual retry stays read-only',async()=>{
 const h=harness();await flush();
 for(const delay of [2000,4000,8000,16000,30000,30000]){assert.equal(h.timers.size,1);h.fire(delay);await flush()}
 h.retry.emit('click');await flush();assert.ok([...h.timers.values()].some(t=>t.delay===2000));
 assert.equal(h.navigation.length,0);assert.equal(h.origin.textContent,'https://fixture.invalid');
 assert.ok(h.calls.every(c=>c.url==='/api/health'&&c.options.cache==='no-store'&&!c.options.method));
});
test('only identified healthy host and available HTML entry trigger navigation',async()=>{
 for(const path of ['/','/login','/offline.html']){
  const h=harness({path,fetcher:async url=>url==='/api/health'?health():html()});await flush();
  assert.deepEqual(h.navigation,[path==='/login'?'/login':'/']);assert.equal(h.calls.length,2);assert.equal(h.timers.size,0);
  assert.equal(h.retry.disabled,true);
 }
 for(const fetcher of [async()=>({ok:true,json:async()=>({ok:true,app:'another-service'})}),async url=>url==='/api/health'?health():{ok:false}]){
  const h=harness({fetcher});await flush();assert.equal(h.navigation.length,0);assert.equal(h.timers.size,1);
 }
});
test('hung request is aborted and concurrent wake events cannot multiply probes',async()=>{
 const h=harness({fetcher:(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted'))))});
 h.window.emit('online');h.window.emit('pageshow');h.retry.emit('click');assert.equal(h.calls.length,1);assert.equal(h.retry.disabled,true);
 h.fire(5000);await flush();assert.equal(h.calls[0].options.signal.aborted,true);assert.equal(h.retry.disabled,false);assert.equal(h.timers.size,1);
});
test('hidden and pagehide suspend retries; restored foreground probes again',async()=>{
 const h=harness();await flush();h.document.hidden=true;h.document.emit('visibilitychange');assert.equal(h.timers.size,0);
 h.window.emit('online');assert.equal(h.calls.length,1);
 h.document.hidden=false;h.document.emit('visibilitychange');await flush();assert.equal(h.calls.length,2);
 h.window.emit('pagehide');assert.equal(h.timers.size,0);h.window.emit('online');assert.equal(h.calls.length,2);
 h.window.emit('pageshow');await flush();assert.equal(h.calls.length,3);
});
