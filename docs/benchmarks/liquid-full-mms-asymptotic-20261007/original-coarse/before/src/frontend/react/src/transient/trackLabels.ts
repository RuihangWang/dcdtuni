/**
 * What a track row is called: the parameter, and which part of the model it
 * belongs to (a server, a cold-plate pair, an ACU patch, a valve). The target
 * string is the registry path's bindings joined by '/' (cfdproj_transient.js).
 */
import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'
import type { Track } from './model'

type Bag = Record<string, unknown>
const bag = (v: unknown): Bag => (v && typeof v === 'object' ? (v as Bag) : {})
const nameOf = (v: unknown): string | undefined => {
  const meta = bag(bag(v).meta)
  return typeof meta.name === 'string' ? meta.name : typeof bag(v).name === 'string' ? (bag(v).name as string) : undefined
}

export function targetLabel(project: CfdProject | null, trk: Track): string {
  if (!project || !trk.target) return ''
  const model = store.findConstruction(project, trk.modelUid)?.model as unknown
  const servers = bag(bag(bag(model).constructions).servers)
  if (trk.param === 'server.power') return nameOf(servers[trk.target]) ?? trk.target
  if (trk.param.startsWith('cp.')) {
    const [server, pair] = trk.target.split('/')
    return `${nameOf(servers[server ?? '']) ?? server} · CP ${Number(pair) + 1}`
  }
  if (trk.param.startsWith('acu.')) {
    for (const face of Object.values(bag(bag(bag(model).geometry).faces))) {
      const patches = bag(face).patches
      if (!Array.isArray(patches)) continue
      const p = patches.find((x) => bag(x).id === trk.target)
      if (p) return nameOf(p) ?? trk.target
    }
  }
  return trk.target
}

/** Does the selected tree node point at this track's target? */
export function nodeMatches(node: { patchId?: string; equipmentId?: string; fittingId?: string } | null, trk: Track): boolean {
  if (!node || !trk.target) return false
  return [node.patchId, node.equipmentId, node.fittingId].some(
    (id) => !!id && (trk.target === id || trk.target.startsWith(`${id}/`)),
  )
}
