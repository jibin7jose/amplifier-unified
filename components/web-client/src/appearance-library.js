// Keep edit targets bound to the exact read, not a later background listing.
export function appearanceMutationTarget(entry){
 if(entry?.source!=='saved'||!entry.id?.startsWith('saved:')||!/^[a-f0-9]{64}$/.test(entry.fingerprint||''))throw Error('Read a saved appearance before updating or deleting it.');
 return {id:entry.id,expectedFingerprint:entry.fingerprint};
}

const CSS_IMPORT_LIMIT=1_000_000;
const PACKAGE_IMPORT_LIMIT=4_000_000;
const PACKAGE_BASE64_LIMIT=Math.ceil(PACKAGE_IMPORT_LIMIT/3)*4;
const BASE64_CHUNK_SIZE=0x8000;

function appearanceFallbackName(fileName){
 return String(fileName||'').replace(/\.amplifier\.css$|\.css$|\.zip$/i,'').slice(0,100)||'Imported appearance';
}

function encodeBase64(bytes){
 let binary='';
 for(let offset=0;offset<bytes.length;offset+=BASE64_CHUNK_SIZE)binary+=String.fromCharCode(...bytes.subarray(offset,offset+BASE64_CHUNK_SIZE));
 return btoa(binary);
}

export function appearanceImportArgs(fileName,css){
 return {css,fallbackName:appearanceFallbackName(fileName)};
}

export async function appearanceImportFileArgs(file){
 if(!file||typeof file.arrayBuffer!=='function')throw Error('Choose a CSS file or ZIP appearance package.');
 if(!Number.isSafeInteger(file.size)||file.size<0)throw Error('Could not read the selected appearance file.');
 if(file.size>PACKAGE_IMPORT_LIMIT)throw Error('Choose a CSS file up to 1 MB or a ZIP package up to 4 MB.');
 const bytes=new Uint8Array(await file.arrayBuffer());
 if(bytes.byteLength>PACKAGE_IMPORT_LIMIT)throw Error('Choose a CSS file up to 1 MB or a ZIP package up to 4 MB.');
 const fallbackName=appearanceFallbackName(file.name);
 if(bytes.length>=2&&bytes[0]===0x50&&bytes[1]===0x4b)return {packageBase64:encodeBase64(bytes),fallbackName};
 if(bytes.byteLength>CSS_IMPORT_LIMIT)throw Error('CSS appearances must be 1 MB or smaller.');
 let css;
 try{css=new TextDecoder('utf-8',{fatal:true}).decode(bytes)}catch{throw Error('CSS appearances must contain valid UTF-8 text.')}
 return {css,fallbackName};
}

function decodeBase64(value){
 const invalid=()=>Error('The binary download payload is invalid.');
 // A flat alphabet scan avoids repeating a group once per quartet on large
 // downloads. atob checks padding; the round-trip check rejects noncanonical bits.
 if(typeof value!=='string'||!value||value.length>PACKAGE_BASE64_LIMIT||value.length%4!==0||/[^A-Za-z0-9+/=]/.test(value))throw invalid();
 let binary;
 try{binary=atob(value)}catch{throw invalid()}
 if(binary.length>PACKAGE_IMPORT_LIMIT||btoa(binary)!==value)throw invalid();
 const bytes=new Uint8Array(binary.length);
 for(let index=0;index<binary.length;index++)bytes[index]=binary.charCodeAt(index);
 return bytes;
}

export function downloadBlobContent(content,type='application/json'){
 if(String(type).split(';',1)[0].trim().toLowerCase()==='application/zip'){
  if(!content||typeof content!=='object'||Array.isArray(content)||!Object.hasOwn(content,'base64')||Object.keys(content).length!==1||typeof content.base64!=='string')throw Error('The binary download payload is invalid.');
  return decodeBase64(content.base64);
 }
 return typeof content==='string'?content:JSON.stringify(content,null,2);
}