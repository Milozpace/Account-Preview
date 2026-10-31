/** Display identity only: no budget/netting calculations or cryptographic decisions. */
export function sameSnapshotContent(left:unknown,right:unknown):boolean {
  const canonical=(value:unknown,root=false):unknown=>{
    if(Array.isArray(value))return value.map(item=>canonical(item));
    if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([key])=>!(root&&key==='generatedAt')).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>[key,canonical(item)]));
    return value;
  };
  return JSON.stringify(canonical(left,true))===JSON.stringify(canonical(right,true));
}
