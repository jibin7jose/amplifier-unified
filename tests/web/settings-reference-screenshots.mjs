// Capture an approved design reference without modifying the original artifact.
import {chromium} from '@playwright/test';
import {readFileSync,mkdirSync} from 'node:fs';
const [source,out]=process.argv.slice(2);if(!source||!out)throw Error('Pass reference HTML and output directory');
mkdirSync(out,{recursive:true});const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1280,height:940}});
 for(const mode of ['dark','light']){
  await page.emulateMedia({colorScheme:mode});
  await page.setContent('<style>html{color-scheme:light dark}body{margin:50px auto;max-width:1120px;background:light-dark(#e5e8f1,#1b2033);font-family:system-ui}button,input,select{font:inherit}*{box-sizing:border-box}button{cursor:pointer}</style>'+readFileSync(source,'utf8'));
  for(const [name,id] of [['ai-connections','ai'],['smart-tools','tools'],['appearance','appearance'],['voice','voice'],['notifications','notifications'],['privacy','privacy'],['updates','updates'],['advanced','advanced']]){
   await page.locator('.as-nav [data-act='+id+']').click();
   await page.screenshot({path:out+'/'+name+'-'+mode+'.png'});
  }
 }
}finally{await browser.close()}
