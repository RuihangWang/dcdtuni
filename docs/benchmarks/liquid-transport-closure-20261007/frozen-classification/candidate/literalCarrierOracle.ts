/** Independent literal whole-carrier oracle, not per partial source region. */
export function auditLiteralClosedCarrier(text:string) {
  const points:number[][]=[],pointIds=new Map<string,number>(),edges=new Map<string,[number,number]>()
  const regions=new Map<string,number>();let solid:string|null=null,triangle:number[]=[],count=0,volume=0
  let normal:number[]=[]
  for(const line of text.split('\n')) {
    const words=line.trim().split(/\s+/)
    if(words[0]==='solid') {if(solid)throw new Error('nested STL solid');solid=words[1]!;if(regions.has(solid))throw new Error('duplicate STL solid');regions.set(solid,0)}
    else if(words[0]==='endsolid') {if(words[1]!==solid)throw new Error('STL region delimiter mismatch');solid=null}
    else if(words[0]==='facet') {normal=words.slice(2).map(Number);triangle=[]}
    else if(words[0]==='vertex') {
      const v=words.slice(1).map(Number);if(v.length!==3||v.some(n=>!Number.isFinite(n)))throw new Error('nonfinite STL point')
      const key=v.join(',');let id=pointIds.get(key);if(id===undefined){id=points.length;points.push(v);pointIds.set(key,id)}triangle.push(id)
    } else if(words[0]==='endfacet') {
      if(!solid||triangle.length!==3)throw new Error('invalid STL facet')
      const [ia,ib,ic]=triangle as [number,number,number],a=points[ia]!,b=points[ib]!,c=points[ic]!
      const u=b.map((v,i)=>v-a[i]!),v=c.map((n,i)=>n-a[i]!),cross=[u[1]!*v[2]!-u[2]!*v[1]!,u[2]!*v[0]!-u[0]!*v[2]!,u[0]!*v[1]!-u[1]!*v[0]!]
      const area=Math.hypot(...cross)/2
      if(!Number.isFinite(area)||area<=1e-20||normal.length!==3||normal.some(n=>!Number.isFinite(n))||normal.reduce((s,n,i)=>s+n*cross[i]!,0)<=0)throw new Error('degenerate or reversed written STL facet')
      for(const [x,y] of [[ia,ib],[ib,ic],[ic,ia]] as Array<[number,number]>) {const key=Math.min(x,y)+':'+Math.max(x,y);const row:[number,number]=edges.get(key)??[0,0];row[x<y?0:1]++;edges.set(key,row)}
      volume+=(a[0]!*(b[1]!*c[2]!-b[2]!*c[1]!)+a[1]!*(b[2]!*c[0]!-b[0]!*c[2]!)+a[2]!*(b[0]!*c[1]!-b[1]!*c[0]!))/6
      regions.set(solid,regions.get(solid)!+1);count++
    }
  }
  if(solid||!count||!Number.isFinite(volume)||volume<=1e-12||[...edges.values()].some(([a,b])=>a!==1||b!==1))throw new Error('written carrier is not a closed oriented solid')
  return {triangles:count,volumeM3:volume,regionFacetCounts:Object.fromEntries(regions)}
}
