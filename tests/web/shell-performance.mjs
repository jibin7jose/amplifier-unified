import {fixturePython} from './fixture-python.mjs';
// Compare production navigation on the same disposable fixture and machine.
// Pass a clean baseline checkout; this script never builds or edits it.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),baseline=process.argv[2];
if(!baseline)throw Error('Pass the baseline checkout directory.');
const browser=await chromium.launch({headless:true});
async function measure(directory){
 const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',directory+'/tests/fixtures/chat_library_server.py'],{stdio:['ignore','pipe','pipe']});
 let log='';fixture.stderr.on('data',chunk=>log+=chunk);
 try{
  const port=await new Promise((resolve,reject)=>{
   let output='';fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
   fixture.once('exit',code=>reject(Error(`Fixture ${code}: ${log}`)));
  });
  const samples=[];
  for(let run=0;run<3;run++){
   const page=await browser.newPage({viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
   let streams=0;page.on('websocket',socket=>{if(new URL(socket.url()).pathname==='/api/events/ws')streams++});page.on('request',request=>{if(new URL(request.url()).pathname==='/api/events')streams++});
   await page.goto(`http://127.0.0.1:${port}`);
   await page.locator('.a-nav-chat').nth(99).waitFor();
   const startupMs=await page.evaluate(()=>performance.now());
   const started=performance.now();
   await page.getByRole('searchbox',{name:'Filter conversations'}).fill('Alpha 20');
   await page.waitForFunction(()=>document.querySelectorAll('.a-nav-chat').length===2);
   const searchMs=performance.now()-started;
   const sizes=await page.evaluate(async()=>{
    const state=await (await fetch('/api/state')).text(),clientId=window.amplifier.shellClientId;
    const shell=clientId?await (await fetch('/api/shell?clientId='+clientId+'&navigationResources='+encodeURIComponent(JSON.stringify(['chatNavigation','homeNavigation','pinnedNavigation','recentNavigation','workspaceShortcuts','workspaceExplorer','sidebarSearch','sidebarWorkspace','sidebarRecent','sidebarPinned','workspaceOverview'])))).text():'';
    return {stateBytes:new TextEncoder().encode(state).length,shellBytes:new TextEncoder().encode(shell).length,domNodes:document.getElementsByTagName('*').length};
   });
   samples.push({startupMs:Math.round(startupMs),searchMs:Math.round(searchMs),streams,...sizes});
   // Reset the baseline's global filter before the next fresh browser.
   await page.getByRole('searchbox',{name:'Filter conversations'}).fill('');
   await page.locator('.a-nav-chat').nth(99).waitFor();
   await page.close();
  }
  return samples;
 }finally{fixture.kill('SIGTERM');await once(fixture,'exit')}
}
try{
 const results={fixture:'206 root chats, 100 rows/page; Chromium; 1440x1050; three fresh pages each',baseline:await measure(baseline),modular:await measure(root)};
 await mkdir(root+'output/shell-proof',{recursive:true});
 await writeFile(root+'output/shell-proof/performance.json',JSON.stringify(results,null,2)+'\n');
 console.log(JSON.stringify(results));
}finally{await browser.close()}
