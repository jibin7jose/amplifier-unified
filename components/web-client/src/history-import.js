const MAX_BYTES=1_000_000;
export async function transcriptFileArgs(file,options={}){
 if(!file)throw new Error('Choose a conversation export.');
 if(file.size>MAX_BYTES)throw new Error('Choose a conversation export smaller than 1 MB.');
 if(!/\.(json|jsonl)$/i.test(file.name||''))throw new Error('Choose a JSON or JSONL conversation export.');
 const content=await file.text();
 if(new TextEncoder().encode(content).length>MAX_BYTES)throw new Error('Choose a conversation export smaller than 1 MB.');
 if(!content.trim())throw new Error('The selected conversation export is empty.');
 return {content,format:/\.jsonl$/i.test(file.name)?'jsonl':'json',...(options.title?.trim()?{title:options.title.trim()}:{}),...(options.bundle?.trim()?{bundle:options.bundle.trim()}:{})};
}
