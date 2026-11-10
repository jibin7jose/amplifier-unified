export function messageTime(at,now=Date.now(),locale){
 const date=new Date(at*1000),today=new Date(now);
 if(!Number.isFinite(date.getTime()))return null;
 const day=d=>Date.UTC(d.getFullYear(),d.getMonth(),d.getDate())/86400000;
 const age=day(today)-day(date),time={hour:'numeric',minute:'2-digit'};
 const options=age===0?time:age>0&&age<7?{weekday:'long',...time}:{month:'short',day:'numeric',...(date.getFullYear()!==today.getFullYear()?{year:'numeric'}:{})};
 return {text:date.toLocaleString(locale,options),full:date.toLocaleString(locale,{dateStyle:'full',timeStyle:'long'}),iso:date.toISOString()};
}
