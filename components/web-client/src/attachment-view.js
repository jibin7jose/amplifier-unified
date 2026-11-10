// Attachment zoom is in decoded CSS pixels, independent of devicePixelRatio.
export function imageLayout(natural, viewport, zoom=null){
 const [width,height]=natural,[vw,vh]=viewport;
 if(![width,height,vw,vh].every(n=>Number.isFinite(n)&&n>0))return null;
 const fit=Math.min(1,Math.max(1,vw-32)/width,Math.max(1,vh-32)/height);
 const scale=zoom===null?fit:Math.max(fit,Math.min(8,zoom));
 return {fit,scale,width:width*scale,height:height*scale,
  planeWidth:Math.max(vw,width*scale+32),planeHeight:Math.max(vh,height*scale+32)};
}
export function imageAttachment(file){
 return ['image/png','image/jpeg','image/gif','image/webp'].includes(file?.mime)
  && /^\/api\/attachments\/[a-f0-9]{32}$/.test(file?.url||'');
}
