/**
 * Fresh nested ids for a duplicated model.
 *
 * A copy needs more than a new model uid. Its face patches and, for a rack, its
 * servers carry ids of their own, and the export joins on them: server uids are
 * the keys of `inputs.servers` and of the cfdrun `bySource` join, so two racks
 * holding the same server uid refuse to export ("duplicate server uid ... in
 * racks ..."). In a v5 project the transient tracks are keyed by those ids too,
 * so a copy that kept them would share its keyframes with the original.
 *
 * The legacy `cloneModelForDuplication` (STL_generate.html) renumbers both; the
 * React port changed only the model uid. This is that renumbering, against the
 * ids the project already holds.
 */
import type { CfdProject, Construction } from '../schema/cfdproj'
import { nextPatchSeq } from './patchGeometry'

/** The next free `e_<n>` sequence number over every server in the project. */
export function nextEquipmentSeq(project: CfdProject): number {
  let max = 0
  for (const items of Object.values(project.constructions ?? {})) {
    for (const model of Object.values(items ?? {})) {
      const servers = ((model as { constructions?: { servers?: Record<string, unknown> } }).constructions?.servers ??
        {}) as Record<string, unknown>
      for (const uid of Object.keys(servers)) {
        const m = /^e_(\d+)$/.exec(uid)
        if (m) max = Math.max(max, Number(m[1]))
      }
    }
  }
  return max + 1
}

type Faces = Record<string, { patches?: { id?: unknown }[] } | null> | { patches?: { id?: unknown }[] }[]

function eachPatch(faces: unknown, visit: (patch: { id?: unknown }) => void): void {
  if (!faces || typeof faces !== 'object') return
  for (const face of Object.values(faces as Faces)) {
    const patches = (face as { patches?: unknown } | null)?.patches
    if (Array.isArray(patches)) patches.forEach((patch) => patch && typeof patch === 'object' && visit(patch))
  }
}

/**
 * Give `copy` (not yet in `project`) new patch ids and new server uids.
 *
 * Face patches are the object form (`{front: {patches}}`) or the rack door
 * array; server faces are renumbered too. A server's legacy `id` alias follows
 * its uid when it held the same value.
 */
export function renumberNestedIds(project: CfdProject, copy: Construction): void {
  let patchSeq = nextPatchSeq(project)
  const renumber = (patch: { id?: unknown }) => {
    patch.id = `p_${patchSeq++}`
  }
  eachPatch(copy.geometry?.faces, renumber)

  const constructions = copy.constructions as { servers?: Record<string, Record<string, unknown> | null> } | undefined
  const servers = constructions?.servers
  if (!servers || typeof servers !== 'object') return
  let serverSeq = nextEquipmentSeq(project)
  const renamed: Record<string, Record<string, unknown> | null> = {}
  for (const [oldUid, server] of Object.entries(servers)) {
    const uid = `e_${serverSeq++}`
    if (server) {
      server.uid = uid
      if (server.id === oldUid) server.id = uid
      eachPatch(server.faces, renumber)
    }
    renamed[uid] = server
  }
  constructions!.servers = renamed
}
