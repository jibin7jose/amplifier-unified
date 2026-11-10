const variants=new Set(['standard','document','email','chat_message','social_post']);
export function writingParts(text,{offsets=false}={}){
 const lines=String(text||'').split('\n'),parts=[];let plain=[],fence=null,plainStart=0,offset=0;
 const starts=lines.map(line=>{const start=offset;offset+=line.length+1;return start});
 const add=(line,index)=>{if(!plain.length)plainStart=starts[index];plain.push(line)};
 const flush=()=>{if(plain.length){parts.push({type:'text',text:plain.join('\n'),...(offsets?{sourceOffset:plainStart}:{})});plain=[]}};
 for(let i=0;i<lines.length;i++){
  const line=lines[i],code=/^\s{0,3}(`{3,}|~{3,})/.exec(line);
  if(code){if(!fence)fence=code[1];else if(code[1][0]===fence[0]&&code[1].length>=fence.length)fence=null;add(line,i);continue}
  const match=!fence&&/^:::writing\{([^{}]*)\}\s*$/.exec(line);
  if(!match){add(line,i);continue}
  const attrs={},pattern=/([a-z]+)="([^"\r\n]*)"/g;let item;while((item=pattern.exec(match[1])))attrs[item[1]]=item[2];
  const residue=match[1].replace(pattern,'').trim();
  if(residue||!variants.has(attrs.variant)||!/^\d{5}$/.test(attrs.id)||Object.keys(attrs).some(key=>!['variant','id','subject','recipient','cc','bcc'].includes(key))){add(line,i);continue}
  let end=i+1;for(;end<lines.length&&lines[end]!==':::';end++);
  if(end===lines.length){add(line,i);continue}
  flush();parts.push({type:'writing',text:lines.slice(i+1,end).join('\n'),...(offsets?{sourceOffset:starts[i+1]}:{}),...attrs});i=end;
 }
 flush();return parts;
}
