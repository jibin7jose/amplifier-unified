// Presentation only: bounded work, never a replacement for retained arguments.
const short=(text,limit)=>text.slice(0,limit).replace(/\s+/g,' ')+(text.length>limit?'…':'');
export const boundedPreview=text=>text.length>180?text.slice(0,179)+'…':text;
const secretKey=key=>{const name=key.toLowerCase().replace(/[^a-z0-9]/g,'');return key.length>96||name.endsWith('token')||/(password|passwd|secret|apikey|privatekey|signingkey|accesskey|credential|authorization|cookie)/.test(name)};
export function inputPreview(value){
 let remaining=24;
 function render(item,depth=0){
  if(remaining--<=0)return '…';
  if(item===null)return 'null';
  if(typeof item==='boolean')return String(item);
  if(typeof item==='string')return JSON.stringify(short(item,64));
  if(typeof item==='number')return Number.isFinite(item)&&(!Number.isInteger(item)||Number.isSafeInteger(item))?String(item):'…';
  if(Array.isArray(item)){
   if(!item.length)return '[]';
   if(depth>=2)return '[…]';
   return '['+item.slice(0,4).map(child=>render(child,depth+1)).concat(item.length>4?['…']:[]).join(', ')+']';
  }
  if(item&&typeof item==='object'){
   const parts=[];let count=0;
   // Avoid enumerating/sorting the whole object for a tiny heading.
   for(const key in item){
    if(!Object.hasOwn(item,key))continue;
    if(depth>=2)return '{…}';
    if(count++===4){parts.push('…');break}
    parts.push(short(key,40)+(depth===0?'=':': ')+(secretKey(key)?'"[redacted]"':render(item[key],depth+1)));
   }
   if(!parts.length)return '{}';
   return depth===0?parts.join(', '):'{'+parts.join(', ')+'}';
  }
  return '…';
 }
 return boundedPreview(render(value));
}
export function retainedInputPreview(text){
 if(text===undefined||text===null)return '';
 if(typeof text!=='string')return inputPreview(text);
 // Host hints cover large or partial JSON. Do not scan full payloads to skim.
 if(text.length>16384)return /^[\s]*[\[{]/.test(text)?'':inputPreview(text);
 try{return inputPreview(JSON.parse(text))}catch{return /^[\s]*[\[{]/.test(text)?'':inputPreview(text)}
}

// Layout only: preserve numeric spelling, duplicate keys and string escapes.
// Copy still uses the original text. Partial/large/deep records stay literal.
export function readablePayload(text){
 if(typeof text!=='string'||text.length>262144||!/^\s*[\[{]/.test(text))return text;
 try{JSON.parse(text)}catch{return text}
 let result='',depth=0,quoted=false,escaped=false;
 const line=()=>{result+='\n'+'  '.repeat(depth)};
 for(let i=0;i<text.length;i++){
  const ch=text[i];
  if(quoted){result+=ch;if(escaped)escaped=false;else if(ch==='\\')escaped=true;else if(ch==='"')quoted=false;continue}
  if(ch==='"'){quoted=true;result+=ch}
  else if(ch==='{'||ch==='['){result+=ch;depth++;if(depth>32)return text;line()}
  else if(ch==='}'||ch===']'){depth--;result=result.trimEnd();line();result+=ch}
  else if(ch===','){result+=ch;line()}
  else if(ch===':')result+=': ';
  else if(!/\s/.test(ch))result+=ch;
  if(result.length>524288)return text;
 }
 return result;
}
