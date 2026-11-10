// Case-insensitive search; patterns use fnmatch's *, ?, [abc], [a-z], [!abc].
// Dynamic programming keeps repeated wildcards from causing regex backtracking.
export function listMatcher(query=''){
 const pattern=String(query).trim().toLowerCase();
 if(!/[\*?\[]/.test(pattern))return value=>String(value??'').toLowerCase().includes(pattern);
 const tokens=[];
 for(let i=0;i<pattern.length;i++){
  const c=pattern[i];
  if(c==='*'||c==='?'){tokens.push(c);continue}
  if(c==='['){
   let end=i+1;if(pattern[end]==='!')end++;if(pattern[end]===']')end++;
   while(end<pattern.length&&pattern[end]!==']')end++;
   if(end<pattern.length){
    let body=pattern.slice(i+1,end),negated=body.startsWith('!');if(negated)body=body.slice(1);
    const chars=[...body],ranges=[];
    for(let j=0;j<chars.length;j++){if(j+2<chars.length&&chars[j+1]==='-'){ranges.push([chars[j],chars[j+2]]);j+=2}else ranges.push([chars[j],chars[j]])}
    tokens.push(char=>negated!==ranges.some(([a,b])=>char>=a&&char<=b));i=end;continue;
   }
  }
  tokens.push(char=>char===c);
 }
 return value=>{
  const chars=[...String(value??'').toLowerCase()];let previous=Array(chars.length+1).fill(false);previous[0]=true;
  for(const token of tokens){const next=Array(chars.length+1).fill(false);next[0]=token==='*'&&previous[0];for(let i=1;i<=chars.length;i++)next[i]=token==='*'?(previous[i]||next[i-1]):previous[i-1]&&(token==='?'||token(chars[i-1]));previous=next}
  return previous[chars.length];
 };
}
export function filterList(items,query,fields){const matches=listMatcher(query);return items.filter(item=>fields(item).some(matches))}
