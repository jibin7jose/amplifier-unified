import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/terminal_setup_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
  const url=await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(Error('Terminal fixture startup timed out')),15000);
    let output='';
    fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Terminal fixture exited '+code));});
    fixture.stdout.on('data',chunk=>{
      output+=chunk;
      for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timeout);resolve(value.url);}}catch{}
    });
  });
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  const page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(url+'/setup/terminal');
  await expect(page.getByText('No terminal installations are registered yet.',{exact:false})).toBeVisible();
  await page.getByLabel('Install on',{exact:true}).selectOption('macos-arm64');
  await page.getByLabel('Name this connection').fill('Fixture Mac');
  const preparing=page.waitForResponse(r=>r.url().endsWith('/api/actions')&&r.request().postDataJSON()?.action==='terminal.prepare');
  await page.getByRole('button',{name:'Prepare setup file',exact:true}).click();
  await expect(page.getByRole('button',{name:'Preparing…',exact:true})).toBeDisabled();
  const receipt=await (await preparing).json();
  await expect(page.getByRole('link',{name:'Download setup file'})).toBeVisible();
  await expect(page.getByText('Open your preferred terminal app',{exact:false})).toBeVisible();
  await expect(page.getByText('without launching a different terminal app.',{exact:false})).toBeVisible();
  // Registration on a different computer/process must appear without clicking Refresh.
  const response=await page.request.get(url+receipt.installer.downloadUrl);
  assert.equal(response.status(),200);
  const download=await response.text();
  const profile=JSON.parse(Buffer.from(download.match(/<<'AMPLIFIER_PROFILE'\n([^\n]+)\nAMPLIFIER_PROFILE/)[1],'base64'));
  const redeemed=await page.request.post(url+'/api/terminal/redeem',{data:{grant:profile.grant}});
  assert.equal(redeemed.status(),200);
  const device=await redeemed.json();
  await expect(page.locator('#status')).toContainText('Connection registered.',{timeout:12000});
  await expect(page.locator('#devices')).toContainText('Fixture Mac · registered');
  await expect(page.locator('#download')).toBeHidden();
  await expect(page.getByRole('button',{name:'Copy command'})).toBeDisabled();
  await expect(page.locator('#expiry')).toContainText('does not show whether setup finished locally or the terminal is open');
  // A periodic refresh must preserve a focused existing control.
  const remove=page.getByRole('button',{name:'Remove access for Fixture Mac'});
  await remove.focus();
  await page.waitForTimeout(5500);
  await expect(remove).toBeFocused();
  // Reload retains only a non-secret receipt, then reconciles with host truth.
  const storage=await page.evaluate(()=>sessionStorage.getItem('amplifier.terminal.setup'));
  assert.ok(!storage.includes(profile.grant)&&!storage.includes(device.token));
  await page.reload();
  await expect(page.locator('#status')).toContainText('Connection registered.');
  // An unavailable refresh retains last-known registration details and says so.
  await page.route('**/api/actions',async route=>{
    if(route.request().postDataJSON()?.action==='terminal.devices')return route.fulfill({status:503,json:{error:'Temporarily unavailable.'}});
    await route.continue();
  });
  await page.getByRole('button',{name:'Refresh connections'}).click();
  await expect(page.locator('#device-status')).toContainText('may be out of date');
  await expect(page.locator('#devices')).toContainText('Fixture Mac');
  await page.unroute('**/api/actions');
  await page.getByRole('button',{name:'Remove access for Fixture Mac'}).click();
  await expect(page.locator('#devices').locator('li')).toHaveCount(0);
  await expect(page.locator('#status')).toContainText('access was removed');
  // Same setup identity, older expiry must not masquerade as the new enrollment.
  const fake={...receipt.installer,expiresAt:receipt.installer.expiresAt+1800};
  await page.evaluate(value=>sessionStorage.setItem('amplifier.terminal.setup',JSON.stringify(value)),fake);
  await page.route('**/api/actions',async route=>{
    if(route.request().postDataJSON()?.action==='terminal.devices')return route.fulfill({json:{devices:[{id:device.id,name:'Older setup',createdAt:1,setupId:receipt.installer.id,setupExpiresAt:receipt.installer.expiresAt}]}});
    await route.continue();
  });
  await page.reload();
  await expect(page.locator('#devices')).toContainText('Older setup');
  await expect(page.locator('#status')).toContainText('Your setup file is ready.');
  await expect(page.locator('#download')).toBeVisible();
  await page.setViewportSize({width:375,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);
  console.log('Terminal setup: real enrollment auto-refresh, exact receipt matching, reload, focus retention, failure preservation, revocation and narrow layout passed. No client installed.');
}finally{
  await browser?.close();
  fixture.kill('SIGTERM');
}
