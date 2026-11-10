// Opt-in input/layout replay for existing browser assertions. This records only
// real locator activation: fixture dispatch and native-select gestures remain
// outside the keyboard/touch claim. Load with Node --import in an owned DTU.
import {chromium} from '@playwright/test';
import {writeFileSync} from 'node:fs';

const variant=process.env.STANDARD_UX_VARIANT;
if(variant){
 const variants={
  'desktop-pointer':{width:1366,height:900,mode:'pointer'},
  'desktop-keyboard':{width:1366,height:900,mode:'keyboard'},
  'narrow-touch':{width:390,height:844,mode:'touch'},
  'narrow-keyboard':{width:390,height:844,mode:'keyboard'},
 };
 const chosen=variants[variant];if(!chosen)throw Error('Unknown standard UX variant: '+variant);
 const actions=[],patched=new WeakSet();
 const bindPage=page=>{
  const prototype=Object.getPrototypeOf(page.locator('body'));
  if(patched.has(prototype))return;patched.add(prototype);
  const click=prototype.click;
  prototype.click=async function(options={}){
   if(options.button&&options.button!=='left'||options.clickCount>1||options.position||options.modifiers?.length||options.force)
    throw Error('Special locator click requires an explicit variant-specific test');
   const row={selector:this.toString(),variant,startedAt:new Date().toISOString(),status:'running'};actions.push(row);
   try{
    await this.waitFor({state:'visible',timeout:options.timeout});
    if(chosen.mode==='keyboard'){
     await this.focus();
     const focus=await this.evaluate(el=>el===document.activeElement);if(!focus)throw Error('Activation target did not receive keyboard focus');
     const key=await this.evaluate(el=>el.matches('input[type=checkbox],input[type=radio]')||['checkbox','radio','switch'].includes(el.getAttribute('role'))?'Space':'Enter');
     row.input=key;await this.press(key,{timeout:options.timeout});
    }else if(chosen.mode==='touch'){row.input='touchscreen tap';await this.tap(options)}
    else{row.input='pointer click';await click.call(this,options)}
    row.status='completed';
   }catch(error){row.status='failed';row.error=error.message;throw error}
  };
 };
 const launch=chromium.launch.bind(chromium);
 chromium.launch=async options=>{
  const browser=await launch(options),newContext=browser.newContext.bind(browser),newPage=browser.newPage.bind(browser);
  const contextOptions=options=>({...options,viewport:{width:chosen.width,height:chosen.height},hasTouch:chosen.mode==='touch',isMobile:chosen.mode==='touch'});
  browser.newContext=async options=>{const context=await newContext(contextOptions(options)),createPage=context.newPage.bind(context);context.newPage=async()=>{const page=await createPage();bindPage(page);return page};return context};
  browser.newPage=async options=>{const page=await newPage(contextOptions(options));bindPage(page);return page};
  return browser;
 };
 process.on('exit',()=>{
  if(process.env.STANDARD_UX_ACTION_LOG)writeFileSync(process.env.STANDARD_UX_ACTION_LOG,JSON.stringify({variant,actions,limits:['Focus plus key activation, not sequential Tab traversal','Existing fill/selectOption/API prerequisites remain explicitly outside input-gesture acceptance','An existing test may explicitly resize later; review its assertions before claiming every step at initial viewport']},null,2)+'\n');
 });
}
