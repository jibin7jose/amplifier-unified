// Optional owned-DTU launch configuration, including nested validator processes.
// Activate explicitly with NODE_OPTIONS=--import=<absolute path>; never used by
// production startup. Browser execution and all product assertions stay real.
import {realpath} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const args=JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]');
if(!Array.isArray(args)||!args.every(value=>typeof value==='string'))throw Error('Invalid Chromium launch arguments');
if(args.length){
 const owners=await Promise.all(['../web/node_modules/playwright/index.mjs','../../.build/web-client/node_modules/playwright/index.mjs'].map(async path=>
  (await import(pathToFileURL(await realpath(new URL(path,import.meta.url))).href)).chromium));
 for(const chromium of new Set(owners)){
 const launch=chromium.launch.bind(chromium);
 const persistent=chromium.launchPersistentContext.bind(chromium);
 const options=value=>({...value,args:[...new Set([...(value?.args||[]),...args])]});
 chromium.launch=value=>launch(options(value));
 chromium.launchPersistentContext=(directory,value)=>persistent(directory,options(value));
}
}
