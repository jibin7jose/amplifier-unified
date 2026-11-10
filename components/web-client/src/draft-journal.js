// Only unacknowledged composer edits live here. sessionStorage gives a reloaded
// or duplicated tab its own copy; the server still owns acknowledged drafts.
const key='amplifier.pendingDrafts.v1';
export function createDraftJournal(owner,storage=()=>sessionStorage){
 let rows=new Map();
 const persist=()=>{try{const target=storage();if(rows.size)target.setItem(key,JSON.stringify({owner,rows:[...rows.values()]}));else target.removeItem(key);return true}catch{return false}};
 const same=entry=>rows.get(entry.sessionId)?.revision===entry.revision;
 return {
  resume(previous){
   try{const saved=JSON.parse(storage().getItem(key)||'null');if(previous&&saved?.owner===previous&&Array.isArray(saved.rows))rows=new Map(saved.rows.filter(row=>row&&(row.sessionId===null||typeof row.sessionId==='string')&&typeof row.text==='string'&&typeof row.revision==='string').map(row=>[row.sessionId,row]));}catch{}
   persist();
  },
  entries:()=>[...rows.values()].map(row=>({...row})),
  stage(sessionId,text,document=null){const entry={sessionId,text,document,revision:crypto.randomUUID()};rows.set(sessionId,entry);return {...entry,persisted:persist()}},
  acknowledge(entry){if(entry&&same(entry)){rows.delete(entry.sessionId);return persist()}return null},
  bind(entry,sessionId){if(!entry||entry.sessionId===sessionId)return true;const found=same(entry);if(found){rows.delete(entry.sessionId);rows.set(sessionId,{...entry,sessionId})}entry.sessionId=sessionId;return !found||persist()},
 };
}
