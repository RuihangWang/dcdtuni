import { expect,it } from 'vitest'
import { auditLiteralClosedCarrier } from './helpers/literalCarrierOracle'
const O=[0,0,0],A=[1,0,0],B=[0,1,0],C=[0,0,1]
const facet=(v:number[][],n:number[])=>`  facet normal ${n.join(' ')}\n    outer loop\n${v.map(p=>`      vertex ${p.join(' ')}\n`).join('')}    endloop\n  endfacet\n`
const f=[facet([O,B,A],[0,0,-1]),facet([O,A,C],[0,-1,0]),facet([O,C,B],[-1,0,0]),facet([A,B,C],[1,1,1])]
const region=(name:string,faces:string[])=>`solid ${name}\n${faces.join('')}endsolid ${name}\n`
const carrier=()=>region('supply',f.slice(0,2))+region('return',f.slice(2))
it('checks one closed carrier whose individual source regions are partial exterior sheets',()=>{
  const a=auditLiteralClosedCarrier(carrier());expect(a.triangles).toBe(4);expect(a.volumeM3).toBeCloseTo(1/6,15);expect(a.regionFacetCounts).toEqual({supply:2,return:2})
  expect(()=>auditLiteralClosedCarrier(region('supply',f.slice(0,2)))).toThrow('not a closed oriented solid')
})
it('rejects missing and duplicate facets rather than filling holes or dropping them',()=>{
  expect(()=>auditLiteralClosedCarrier(region('supply',f.slice(0,3)))).toThrow('not a closed oriented solid')
  expect(()=>auditLiteralClosedCarrier(region('supply',[...f,f[0]!]))).toThrow('not a closed oriented solid')
})
it('rejects reversed normals and aliased STL source labels',()=>{
  expect(()=>auditLiteralClosedCarrier(carrier().replace('normal 0 0 -1','normal 0 0 1'))).toThrow('reversed written STL facet')
  expect(()=>auditLiteralClosedCarrier(carrier().replaceAll('return','supply'))).toThrow('duplicate STL solid')
})
