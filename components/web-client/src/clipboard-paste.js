export const PASTED_TEXT_THRESHOLD=10_000;
export function clipboardPaste(clipboard){
 const files=[...(clipboard.items||[])].filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);
 const text=clipboard.getData('text/plain');
 if(text.length>PASTED_TEXT_THRESHOLD){
  const file=new File([text],'Pasted text.txt',{type:'text/plain'});
  file.attachmentSource='clipboard-text';
  return {files:[...files,file],text:'',handled:true};
 }
 return {files,text,handled:files.length>0};
}
