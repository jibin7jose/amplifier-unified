// Deliberate negative control, never used by npm run build. Compile the old
// automatic HTML-inline default in memory; do not edit production source.
import assert from 'node:assert/strict';
import base from '../vite.config.js';

const current="const expanded=!primaryOwns&&(choice?choice.presentation==='inline'&&choice.messageId===messageId:row.kind==='image'&&(row.publications||[]).find(link=>link.version===version)?.messageId===messageId);";
const regression="const expanded=row.kind==='html'||(!primaryOwns&&(choice?choice.presentation==='inline'&&choice.messageId===messageId:row.kind==='image'&&(row.publications||[]).find(link=>link.version===version)?.messageId===messageId));";
export default {
 ...base,
 plugins:[{
  name:'negative-restore-duplicate-html-default',
  enforce:'pre',
  transform(source,id){
   if(!id.endsWith('/src/inline-artifacts.jsx'))return;
   assert.ok(source.includes(current),'Refresh the exact negative-control binding');
   assert.ok(source.includes('presentationRequest={!!choice}'));
   return source.replace(current,regression).replace('presentationRequest={!!choice}','presentationRequest={row.kind==="html"?false:!!choice}');
  },
 },...base.plugins],
};