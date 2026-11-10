// Preserve equal branches without allocating two large serialized strings.
export function retainEqual(previous,next){
 if(previous===next)return previous;
 if(!previous||!next||typeof previous!=='object'||typeof next!=='object'||Array.isArray(previous)!==Array.isArray(next))return next;
 const keys=Object.keys(next);let equal=keys.length===Object.keys(previous).length;
 const result=Array.isArray(next)?[]:{};
 for(const key of keys){result[key]=retainEqual(previous[key],next[key]);if(!Object.hasOwn(previous,key)||result[key]!==previous[key])equal=false}
 return equal?previous:result;
}
