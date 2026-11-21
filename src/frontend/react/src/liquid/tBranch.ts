import type {Quat, Vec3} from '../schema/cfdproj'

/** New authoring defaults in metres; never applied while loading old documents. */
export const DEFAULT_T_BRANCH_DIAMETER_M = 0.025
export interface TBranchPort {
  id: string
  offset: Vec3
  direction: Vec3
  type: string
  connectedPipeId: string | null
}
export function positivePhysicalDiameter(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}
export function createAuthoredTBranch(fittingDiameterM = DEFAULT_T_BRANCH_DIAMETER_M) {
  if (!positivePhysicalDiameter(fittingDiameterM)) throw new Error('T fitting needs a finite positive physical diameter in metres')
  // Preserve the existing authored local +/-X through-run and +Z branch frame.
  const ports: TBranchPort[] = [
    {id:'port_1',offset:{x:-0.04,y:0,z:0},direction:{x:-1,y:0,z:0},type:'inout',connectedPipeId:null},
    {id:'port_2',offset:{x:0.04,y:0,z:0},direction:{x:1,y:0,z:0},type:'inout',connectedPipeId:null},
    {id:'port_3',offset:{x:0,y:0,z:0.04},direction:{x:0,y:0,z:1},type:'out',connectedPipeId:null},
  ]
  return {type:'T_branch',quaternion:{x:0,y:0,z:0,w:1} as Quat,fittingDiameterM,ports,connectedPipes:[] as string[]}
}
function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function nonzeroPoint(value: unknown): boolean {
  const p=record(value)
  return !!p && ['x','y','z'].every(k=>typeof p[k]==='number' && Number.isFinite(p[k])) && Math.hypot(p.x as number,p.y as number,p.z as number)>0
}
export function hasAuthoredTBranchLayout(cooling: unknown): boolean {
  const c=record(cooling),q=record(c?.quaternion)
  if (!c || c.type!=='T_branch' || !q || !['x','y','z','w'].every(k=>typeof q[k]==='number' && Number.isFinite(q[k])) || Math.hypot(q.x as number,q.y as number,q.z as number,q.w as number)===0 || !Array.isArray(c.ports) || c.ports.length!==3) return false
  const ids=new Set<string>()
  for (const raw of c.ports) {
    const port=record(raw)
    if (!port || typeof port.id!=='string' || !port.id || ids.has(port.id) || !nonzeroPoint(port.offset) || !nonzeroPoint(port.direction)) return false
    ids.add(port.id)
  }
  return true
}
