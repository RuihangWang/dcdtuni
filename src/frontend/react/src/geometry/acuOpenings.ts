import type { CfdProject, Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { addPatch, autoFillPatch } from './patchActions'
import { PATCH_PHYSICS_DEFAULTS, updatePatchPhysics } from './patchPhysics'

/**
 * Whether an ACU can blow: it needs a supply and a return opening. A unit with neither is a standby unit (the DCTS
 * rooms model a switched-off CRAC as a closed box); turning it on opens the faces its record names
 * (`supplyFaces` / `returnFaces`, front and back otherwise) over their whole area, with the values of a running
 * unit of the same size when the project has one. Turning it off removes its supply and return openings and keeps
 * that record, as the dataset's standby units do, so it can be turned on again.
 */

type Bag = Record<string, unknown>

interface Opening {
  face: string
  id: string
  physics: Bag
}

function openings(model: Construction): Opening[] {
  const faces = ((model.geometry as Bag | undefined)?.faces ?? {}) as Record<string, Bag>
  const out: Opening[] = []
  for (const [face, f] of Object.entries(faces)) {
    for (const p of (f?.patches ?? []) as Bag[]) out.push({ face, id: String(p.id), physics: (p.physics ?? {}) as Bag })
  }
  return out
}

export function acuOpeningState(model: Construction): { supply: number; ret: number; standby: boolean } {
  const list = openings(model)
  const supply = list.filter((o) => o.physics.role === 'supply').length
  const ret = list.filter((o) => o.physics.role === 'return').length
  return { supply, ret, standby: supply === 0 || ret === 0 }
}

function recordedFace(model: Construction, key: 'supplyFaces' | 'returnFaces', fallback: string): string {
  const list = (model.geometry as Bag | undefined)?.[key] as { faceId?: string }[] | undefined
  return list?.find((r) => r?.faceId)?.faceId ?? fallback
}

function sizeKey(model: Construction): string {
  const s = ((model.geometry as Bag | undefined)?.size ?? {}) as Bag
  return ['x', 'y', 'z'].map((k) => Number(s[k] ?? 0).toFixed(3)).join('x')
}

/** The supply and return values of a running ACU, the same size first. */
function templateFrom(project: CfdProject, model: Construction): { supply: Bag; ret: Bag } | null {
  const acus = Object.values(((project.constructions as Bag).acus ?? {}) as Record<string, Construction>)
    .filter((m) => m.uid !== model.uid && !acuOpeningState(m).standby)
  acus.sort((a, b) => Number(sizeKey(b) === sizeKey(model)) - Number(sizeKey(a) === sizeKey(model)))
  for (const m of acus) {
    const list = openings(m)
    const supply = list.find((o) => o.physics.role === 'supply')
    const ret = list.find((o) => o.physics.role === 'return')
    if (supply && ret) return { supply: supply.physics, ret: ret.physics }
  }
  return null
}

/** Open a standby ACU's supply and return faces. Returns the new patch ids, or an error. */
export function turnOnAcu(uid: string): { ok: true; supply: string; ret: string } | { ok: false; error: string } {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!project || !model || model.kind !== 'acu') return { ok: false, error: 'not an ACU' }
  const state = acuOpeningState(model)
  if (!state.standby) return { ok: false, error: 'already running' }
  const template = templateFrom(project, model)
  const add = (face: string, role: 'supply' | 'return', values: Bag | undefined): string | null => {
    const res = addPatch(uid, face, 'rect')
    if (!res.ok || !res.patchId) return null
    autoFillPatch(uid, res.patchId, 'H')
    autoFillPatch(uid, res.patchId, 'V')
    updatePatchPhysics(uid, res.patchId, 'role', role, { ...PATCH_PHYSICS_DEFAULTS })
    for (const [k, v] of Object.entries(values ?? {})) {
      if (k !== 'role' && v !== undefined) updatePatchPhysics(uid, res.patchId, k, v, { ...PATCH_PHYSICS_DEFAULTS })
    }
    return res.patchId
  }
  const supply = state.supply ? '' : add(recordedFace(model, 'supplyFaces', 'front'), 'supply', template?.supply)
  const ret = state.ret ? '' : add(recordedFace(model, 'returnFaces', 'back'), 'return', template?.ret)
  if (supply === null || ret === null) return { ok: false, error: 'the face could not take an opening' }
  return { ok: true, supply, ret }
}

/** Close a running ACU: its supply and return openings go, in one edit; the faces they were on stay recorded. */
export function turnOffAcu(uid: string): { ok: true; removed: number } | { ok: false; error: string } {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  if (!project || !model || model.kind !== 'acu') return { ok: false, error: 'not an ACU' }
  const doomed = openings(model).filter((o) => o.physics.role === 'supply' || o.physics.role === 'return')
  if (!doomed.length) return { ok: false, error: 'already on standby' }
  const ids = new Set(doomed.map((o) => o.id))
  store.edit('turnOffAcu', uid, (draft) => {
    const m = store.findConstruction(draft, uid)?.model
    const faces = ((m?.geometry as Bag | undefined)?.faces ?? {}) as Record<string, Bag>
    for (const face of Object.values(faces)) {
      if (Array.isArray(face?.patches)) face.patches = (face.patches as Bag[]).filter((p) => !ids.has(String(p.id)))
    }
  })
  return { ok: true, removed: doomed.length }
}
