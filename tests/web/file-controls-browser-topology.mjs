import {chromium} from '@playwright/test';

// One browser process per isolated context. The prepared single-process mode
// cannot supply two independent contexts in one process. Preflight and journeys
// share these exact launch/context options; there is no alternate-flag retry.
export const launchOptions={headless:true,timeout:8000,args:process.env.CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote','--disable-gpu']:[]};
export const desktopOptions={viewport:{width:1440,height:1000}};
export const touchOptions={viewport:{width:390,height:844},isMobile:true,hasTouch:true,deviceScaleFactor:1};
export async function launchOwner(label,owners){
 if(owners.closing)throw Error('Browser owners are closing');
 const owner={label,browser:null,context:null,closed:false};
 owners.push(owner);
 owner.launch=chromium.launch(launchOptions).then(browser=>{owner.browser=browser;return browser});
 await owner.launch;
 if(owners.closing){await closeOwner(owner);throw Error('Browser launch completed after shutdown began')}
 return owner;
}
export async function ownerPage(owner,origin,options){
 if(owner.context)throw Error(`Browser ${owner.label} already owns its one context`);
 owner.context=await owner.browser.newContext({acceptDownloads:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},...options});
 await owner.context.grantPermissions(['clipboard-read','clipboard-write'],{origin});
 const page=await owner.context.newPage();
 page.setDefaultTimeout(4000);page.setDefaultNavigationTimeout(10000);return page;
}
export async function closeOwner(owner){
 return owner.closing??=(async()=>{
  // launch() owns cleanup on a failed launch. Preserve its rejection in the
  // caller; a successfully late-created browser must still be closed here.
  try{await owner.launch}catch{owner.closed=true;return}
  await owner.browser.close();
  if(owner.browser.isConnected())throw Error(`Browser ${owner.label} is still connected after close`);
  owner.closed=true;
 })();
}
