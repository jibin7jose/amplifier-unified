// Executable readiness only, not product acceptance. No external URLs.
import {createServer} from 'node:http';
import {chromium,expect} from '@playwright/test';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
const server=createServer((_,response)=>{response.writeHead(200,{'content-type':'text/html'});response.end('<button onclick="this.textContent=\'Ready\'">Probe</button>')});
let browser;
try{
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage();await page.goto(`http://127.0.0.1:${server.address().port}`);
 await page.getByRole('button',{name:'Probe'}).click();await expect(page.getByRole('button',{name:'Ready'})).toBeVisible();
 console.log(JSON.stringify({ready:true,node:process.version,chromium:browser.version(),chromiumSha256:createHash('sha256').update(await readFile(chromium.executablePath())).digest('hex')}));
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve))}
