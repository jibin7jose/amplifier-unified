// Gate the exact desktop/touch process topology before any product journey.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {expect} from '@playwright/test';
import {launchOwner,ownerPage,closeOwner,launchOptions,desktopOptions,touchOptions} from './file-controls-browser-topology.mjs';

const out=process.env.FILE_CONTROLS_PREFLIGHT_EVIDENCE;
assert.ok(out,'Owned preflight evidence directory is required');await mkdir(out,{recursive:true});
const bytes=Buffer.from('Topology download α\r\n','utf8'),sha=value=>createHash('sha256').update(value).digest('hex');
const owners=[],checks=[];let failure,timer,running,closedServer=false;
async function bounded(promise,milliseconds,label){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label)),milliseconds)})])}
 finally{clearTimeout(timer)}
}
const server=createServer((req,res)=>{
 if(req.url==='/download'){res.writeHead(200,{'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="topology.txt"'});res.end(bytes);return}
 res.writeHead(200,{'Content-Type':'text/html'});
 res.end('<button onclick="this.textContent=\'Interacted\'">Interact</button><a href="/download" download>Download</a>');
});
async function run(){
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)});
 const origin=`http://127.0.0.1:${server.address().port}`;
 const desktop=await launchOwner('desktop',owners),first=await ownerPage(desktop,origin,desktopOptions);
 await first.goto(origin);
 const touch=await launchOwner('touch',owners),second=await ownerPage(touch,origin,touchOptions);
 await second.goto(origin);
 assert.notEqual(desktop.browser,touch.browser);assert.ok(owners.every(owner=>owner.browser.isConnected()));
 for(const [owner,page,isTouch] of [[desktop,first,false],[touch,second,true]]){
  assert.equal(await page.evaluate(()=>navigator.maxTouchPoints>0),isTouch);
  const control=page.getByRole('button',{name:'Interact',exact:true});
  if(isTouch)await control.tap();else await control.click();
  await expect(page.getByRole('button',{name:'Interacted',exact:true})).toBeVisible();
  const sentinel=`preflight-${owner.label}-clipboard-α`;
  await page.evaluate(text=>navigator.clipboard.writeText(text),sentinel);
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),sentinel);
  const pending=page.waitForEvent('download',{timeout:5000});
  const [download]=await Promise.all([pending,isTouch?page.getByRole('link',{name:'Download'}).tap():page.getByRole('link',{name:'Download'}).click()]);
  assert.equal(await download.failure(),null);const file=join(out,owner.label+'-download.txt');await download.saveAs(file);
  const actual=await readFile(file);assert.deepEqual(actual,bytes);
  checks.push({owner:owner.label,browser:owner.browser.version(),touch:isTouch,clipboard:sentinel,downloadSha256:sha(actual),bytes:actual.length});
 }
 // Both pages remain alive after the second context's download and interaction.
 await expect(first.getByRole('button',{name:'Interacted',exact:true})).toBeVisible();
 await expect(second.getByRole('button',{name:'Interacted',exact:true})).toBeVisible();
 assert.ok(owners.every(owner=>owner.browser.isConnected()));
}
try{
 running=run();await Promise.race([running,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Topology preflight exceeded 45 seconds')),45000)})]);
}catch(error){failure=error}
finally{
 clearTimeout(timer);owners.closing=true;
 const settled=await Promise.allSettled(owners.map(owner=>bounded(closeOwner(owner),12000,`Browser ${owner.label} cleanup exceeded 12 seconds`)));
 const failed=settled.filter(row=>row.status==='rejected').map(row=>row.reason);
 if(failed.length)failure=new AggregateError([...(failure?[failure]:[]),...failed],'Browser cleanup failed');
 try{await bounded(new Promise((resolve,reject)=>{server.close(error=>error?reject(error):resolve());server.closeAllConnections()}),5000,'Server close exceeded 5 seconds');closedServer=true}catch(error){failure=new AggregateError([...(failure?[failure]:[]),error],'Server cleanup failed')}
 if(running)try{await bounded(running,10000,'Driver did not settle after close')}catch(error){if(error!==failure)failure=new AggregateError([...(failure?[failure]:[]),error],'Preflight driver failed')}
 await writeFile(join(out,'result.json'),JSON.stringify({passed:!failure,error:failure?.stack,launchOptions,desktopOptions,touchOptions,checks,
  owners:owners.map(owner=>({label:owner.label,closed:owner.closed,connected:owner.browser?.isConnected()??false})),serverClosed:closedServer,attempts:1,noFallback:true},null,2));
}
if(failure)throw failure;
console.log('Two simultaneous browser owners passed interaction, touch, clipboard, actual downloads and shutdown.');
