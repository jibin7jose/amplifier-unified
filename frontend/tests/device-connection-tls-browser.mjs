// The certificate exception is accepted only for this disposable negative fixture.
// The recovery phase uses the owned DTU's trusted certificate, without TLS flags.
import {chromium} from 'playwright';
import {createServer} from 'node:https';
import {mkdtempSync,readFileSync,writeFileSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
assert.equal(execFileSync('hostname',{encoding:'utf8'}).trim(),'dtu-unified-device-connect-20261020');
const base='/var/lib/device-connect',temp=mkdtempSync(base+'/browser/tls-negative-');
const extension=fileURLToPath(new URL('../../amplifier_web/device_client/extension',import.meta.url));
const report={startedAt:new Date().toISOString(),checks:[],networkErrors:[],platform:'Chromium on isolated Linux ARM64'};
const check=name=>{report.checks.push(name);console.log('PASS:',name)};
const requests=[];let context,server;
try{
 execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',temp+'/key.pem','-out',temp+'/cert.pem','-days','1','-subj','/CN=Owned negative TLS fixture','-addext','subjectAltName=IP:127.0.0.1'],{stdio:'ignore'});
 server=createServer({key:readFileSync(temp+'/key.pem'),cert:readFileSync(temp+'/cert.pem')},(req,res)=>{
  requests.push(req.url);
  const json=req.url.startsWith('/api/');
  // New TLS handshakes matter: sharing a page's socket can mask worker failure.
  res.writeHead(200,{'Connection':'close','Content-Type':json?'application/json':'text/html',
   'Access-Control-Allow-Origin':req.headers.origin||'*','Access-Control-Allow-Headers':'Authorization, Content-Type','Access-Control-Allow-Methods':'GET, POST, OPTIONS'});
  res.end(json?JSON.stringify(req.url.endsWith('/poll')?{operation:null}:{status:'connected',grants:['connection.ping']}):'<title>Owned TLS fixture</title><p>Owned synthetic TLS fixture</p>');
 });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const host='https://127.0.0.1:'+server.address().port;
 context=await chromium.launchPersistentContext(temp+'/profile',{channel:'chromium',headless:false,args:['--no-sandbox','--single-process','--no-zygote','--disable-dev-shm-usage',`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
 context.on('requestfailed',r=>report.networkErrors.push({type:r.resourceType(),error:r.failure()?.errorText}));
 const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
 const extensionOrigin=worker.url().replace('/background.js','');
 const page=await context.newPage();await page.goto(host).catch(()=>{});
 await page.locator('#details-button').click();await page.locator('#proceed-link').click();await page.getByText('Owned synthetic TLS fixture').waitFor();
 assert.equal(await page.evaluate(async()=> (await fetch(location.origin)).ok),true);
 check('A web page can work after its certificate warning is dismissed');
 // A fake fixture-only enrollment exercises readiness without touching any real
 // paired device or transmitting a service credential to the negative fixture.
 await worker.evaluate(async host=>chrome.storage.local.set({connection:{host,token:'fixture-only-not-a-service-credential',deviceID:'fixture-only',grants:['connection.ping'],stopped:true}}),host);
 const setup=await context.newPage();await setup.goto(extensionOrigin+'/connect.html?'+new URLSearchParams({host}));
 assert.equal(await setup.evaluate(async host=>(await fetch(host)).ok,host),true);
 await setup.locator('#retry:not([hidden])').waitFor();
 const status=await setup.locator('#status').textContent();
 assert.match(status,/background connection is not ready/);assert.match(status,/certificate warning/);
 assert.equal(await setup.locator('#network-help').getAttribute('href'),host+'/setup');
 assert.equal(await setup.locator('#network-help').isVisible(),true);
 assert.ok(report.networkErrors.some(row=>row.error==='net::ERR_CERT_AUTHORITY_INVALID'));
 check('Foreground approval connectivity cannot falsely declare the background ready');
 const popup=await context.newPage();await popup.goto(extensionOrigin+'/popup.html');
 await popup.locator('#retry:not([hidden])').waitFor();
 assert.match(await popup.locator('#status').textContent(),/background connection is not ready/);
 assert.equal(await popup.locator('#network-help').isVisible(),true);
 check('Popup explains the transport failure and offers connection help and Retry');
 server.setSecureContext({key:readFileSync(base+'/app/config/tls/leaf.key'),cert:readFileSync(base+'/app/config/tls/leaf.crt')});
 await setup.locator('#retry').click();
 await setup.getByText('Connected. Open the extension on the browser tab you want to use, then choose Use this tab.',{exact:true}).waitFor();
 assert.equal(await setup.locator('#retry').isVisible(),false);
 await popup.locator('#retry').click();
 await popup.getByText('Connected to '+host+'. Choose a tab to begin.',{exact:true}).waitFor();
 assert.equal(await worker.evaluate(async()=> (await chrome.storage.local.get('connection')).connection.token==='fixture-only-not-a-service-credential'),true);
 assert.equal(requests.some(path=>path.includes('/enroll')||path.includes('/token')),false);
 check('Trusted TLS restores the saved background connection without pairing again');
 report.passed=true;
}finally{
 report.completedAt=new Date().toISOString();
 writeFileSync(base+'/evidence/extension-tls-recovery.json',JSON.stringify(report,null,2)+'\n');
 await context?.close();if(server)await new Promise(resolve=>server.close(resolve));rmSync(temp,{recursive:true,force:true});
}
