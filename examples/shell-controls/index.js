import {defineModule,useShellComponent} from '@amplifier/shell-sdk';

export default defineModule(({React})=>{
 const h=React.createElement;
 return function PresentationControls({host}){
  const snapshot=useShellComponent(React,host);
  const [error,setError]=React.useState(''),[busy,setBusy]=React.useState(false);
  const [note,setNote]=React.useState(snapshot.view.note||'');
  const inflight=React.useRef(false);
  async function run(action,args){
   if(inflight.current)return;
   inflight.current=true;setBusy(true);setError('');
   try{const result=await host.dispatch(action,args);if(!result.accepted)throw Error(result.result?.reason||'Change was deferred.');return true;}
   catch(error){setError(error.message)}finally{inflight.current=false;setBusy(false)}
  }
  const compact=snapshot.presentation.density==='compact';
  if(snapshot.slot==='app.status')return h('span',{'aria-label':'Conversation status'},snapshot.conversation?.status||'No conversation');
  return h('div',{'aria-label':'Presentation controls',style:{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}},
   h('button',{type:'button',disabled:busy,'data-action':'shell.command','data-shell-command':'presentation.update','aria-label':'Toggle compact spacing','aria-pressed':compact,onClick:()=>run('presentation.update',{expectedRevision:snapshot.compositionRevision,patch:{density:compact?'comfortable':'compact'}})},compact?'Roomy':'Compact'),
   h('button',{type:'button',disabled:busy,'data-action':'shell.command','data-shell-command':'panel.open',onClick:()=>run('panel.open',{panel:'settings'})},'Open settings'),
   snapshot.slot==='settings.section'&&h('label',null,'Component note',h('input',{'aria-label':'Component note',value:note,onChange:event=>{setNote(event.target.value);host.setDirty(true).catch(error=>setError(error.message))}})),
   snapshot.slot==='settings.section'&&h('button',{type:'button',disabled:busy,onClick:async()=>{if(await run('view.update',{patch:{note}}))await host.setDirty(false).catch(error=>setError(error.message))}},'Save component note'),
   error&&h('p',{role:'alert'},error));
 };
});
