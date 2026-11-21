/**
 * Openings on a rack server's face: creating them, shaping them, removing them.
 *
 * A server patch is not decoration. The runner splits the server's stated total
 * flow EVENLY across the patches on the face (`stlgen_runner.py` BCSpec), so
 * adding a second opening halves what each one gets, and deleting the last one
 * hands the whole flow back to the full face. Without these writes the boundary
 * condition a rack exports is frozen at whatever the loaded .cfdproj carried.
 *
 * Patch geometry lives in the face's own 0-1 space — `x/y/w/h` for a rectangle,
 * `cx/cy/r` for a circle — which is why the panel offers percentages rather than
 * metres: the same patch has to mean the same fraction of a 1U face and a 4U one.
 *
 * Ports `_srvAddPatch` (STL_generate.html:9954), `_srvDeletePatch` (:9973),
 * `_srvSetPatchProp` (:9992), `_srvSetFaceColor` (:9940) and the clamp
 * `enforceServerPatchSafety` (:6786).
 */

import type { CfdProject, Construction } from '../schema/cfdproj'
import { nextPatchSeq } from './patchGeometry'
import * as store from '../projectStore'

export type ServerPatchType = 'rect' | 'circle'

export interface ServerPatch {
  id: string
  type?: ServerPatchType
  x?: number
  y?: number
  w?: number
  h?: number
  cx?: number
  cy?: number
  r?: number
  color?: string
  applied?: boolean
}

export interface ServerFace {
  color?: string
  patches?: ServerPatch[]
}

/** The keys a slider row writes. Anything else is a colour or the type. */
export const RECT_KEYS = ['x', 'y', 'w', 'h'] as const
export const CIRCLE_KEYS = ['cx', 'cy', 'r'] as const

type Bag = Record<string, unknown>

function serversOf(model: Construction | null | undefined): Record<string, Bag> {
  return ((model?.constructions as { servers?: Record<string, Bag> } | undefined)?.servers ??
    {}) as Record<string, Bag>
}

/** The face bag of one server, created on demand so a write always lands. */
function faceBag(server: Bag, faceId: string): ServerFace {
  const faces = (server.faces ?? (server.faces = {})) as Record<string, ServerFace>
  const face = faces[faceId] ?? (faces[faceId] = { patches: [] })
  if (!Array.isArray(face.patches)) face.patches = []
  return face
}

export function serverFace(model: Construction, equipmentId: string, faceId: string): ServerFace {
  const server = serversOf(model)[equipmentId]
  const faces = (server?.faces ?? {}) as Record<string, ServerFace>
  return faces[faceId] ?? { patches: [] }
}

export function serverFacePatches(
  model: Construction,
  equipmentId: string,
  faceId: string,
): ServerPatch[] {
  return serverFace(model, equipmentId, faceId).patches ?? []
}

/**
 * Which server on this rack owns a patch id.
 *
 * The tree's `serverPatch` node carries a faceId and a patchId but the panel it
 * routes to is the generic patch panel, which looks the patch up in
 * `geometry.faces[faceId]` — an ARRAY of door entries on a V4 rack, so the
 * lookup always missed and the panel rendered its empty hint. Patch ids are
 * allocated from one project-wide `p_<n>` counter, so scanning the rack's
 * servers resolves the node without the equipmentId the route never passed.
 */
export function findServerPatch(
  model: Construction,
  faceId: string,
  patchId: string,
): { equipmentId: string; patch: ServerPatch; index: number } | null {
  for (const [equipmentId, server] of Object.entries(serversOf(model))) {
    const faces = (server.faces ?? {}) as Record<string, ServerFace>
    const patches = faces[faceId]?.patches ?? []
    const index = patches.findIndex((p) => p.id === patchId)
    if (index >= 0) return { equipmentId, patch: patches[index] as ServerPatch, index }
  }
  return null
}

/**
 * Keep a patch inside its face.
 *
 * A patch that hangs off the edge is not clipped by the exporter — it is
 * written as-is and becomes a boundary the mesh cannot attach, so the clamp is
 * the difference between a refused number and a case that fails at snappy time.
 * Mutates in place, as the legacy does, and returns whether anything moved so
 * the panel can say the value it accepted is not the one that was typed.
 */
export function enforceServerPatchSafety(patch: ServerPatch): boolean {
  const before = JSON.stringify(patch)
  if (patch.type === 'circle') {
    if (!Number.isFinite(patch.r) || (patch.r as number) < 0.01) patch.r = 0.01
    if ((patch.r as number) > 0.5) patch.r = 0.5
    if (!Number.isFinite(patch.cx)) patch.cx = 0.5
    if (!Number.isFinite(patch.cy)) patch.cy = 0.5
    const r = patch.r as number
    if ((patch.cx as number) - r < 0) patch.cx = r
    if ((patch.cx as number) + r > 1) patch.cx = 1 - r
    if ((patch.cy as number) - r < 0) patch.cy = r
    if ((patch.cy as number) + r > 1) patch.cy = 1 - r
  } else {
    if (!Number.isFinite(patch.w) || (patch.w as number) < 0.01) patch.w = 0.01
    if (!Number.isFinite(patch.h) || (patch.h as number) < 0.01) patch.h = 0.01
    if ((patch.w as number) > 1) patch.w = 1
    if ((patch.h as number) > 1) patch.h = 1
    if (!Number.isFinite(patch.x)) patch.x = 0
    if (!Number.isFinite(patch.y)) patch.y = 0
    if ((patch.x as number) < 0) patch.x = 0
    if ((patch.y as number) < 0) patch.y = 0
    if ((patch.x as number) + (patch.w as number) > 1) patch.x = 1 - (patch.w as number)
    if ((patch.y as number) + (patch.h as number) > 1) patch.y = 1 - (patch.h as number)
  }
  return JSON.stringify(patch) !== before
}

/**
 * Rect to circle and back, keeping the opening where it was.
 *
 * The two shapes carry DIFFERENT keys, so the old ones are deleted: a patch
 * left holding both `w` and `r` exports whichever the writer reads first, which
 * is how a vent silently changes size on reload.
 */
export function convertServerPatchType(patch: ServerPatch, next: ServerPatchType): ServerPatch {
  if (next === patch.type) return patch
  if (next === 'circle') {
    patch.type = 'circle'
    patch.cx = (patch.x ?? 0) + (patch.w ?? 0.5) / 2
    patch.cy = (patch.y ?? 0) + (patch.h ?? 0.5) / 2
    patch.r = Math.min(patch.w ?? 0.5, patch.h ?? 0.5) / 2
    delete patch.x
    delete patch.y
    delete patch.w
    delete patch.h
  } else {
    patch.type = 'rect'
    patch.x = (patch.cx ?? 0.5) - (patch.r ?? 0.3)
    patch.y = (patch.cy ?? 0.5) - (patch.r ?? 0.3)
    patch.w = (patch.r ?? 0.3) * 2
    patch.h = (patch.r ?? 0.3) * 2
    delete patch.cx
    delete patch.cy
    delete patch.r
  }
  enforceServerPatchSafety(patch)
  return patch
}

/** The patch `_srvAddPatch` creates: a 60% x 80% rectangle, off-centre-left. */
export function defaultServerPatch(id: string): ServerPatch {
  return { id, type: 'rect', x: 0.2, y: 0.1, w: 0.6, h: 0.8, color: '#00cc66', applied: true }
}

/** The next `p_<n>`, from the ids already in the project — one counter for all patches. */
export function nextServerPatchId(project: CfdProject | null): string {
  return `p_${nextPatchSeq(project)}`
}

// ---------------------------------------------------------------------------
// Writes
// ---------------------------------------------------------------------------

/** Add an opening to a server face. Returns the new patch id. */
export function addServerPatch(
  uid: string,
  equipmentId: string,
  faceId: string,
): string | null {
  const project = store.getProject()
  if (!project) return null
  const id = nextServerPatchId(project)
  store.edit('srvAddPatch', `${uid}/${equipmentId}/${faceId}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    const server = target ? serversOf(target)[equipmentId] : null
    if (!server) return
    faceBag(server, faceId).patches!.push(defaultServerPatch(id))
  })
  return id
}

/**
 * Add a rectangular opening of given geometry (face fractions) to a server face, as one edit.
 *
 * The scripted builder's writer (`addServerPatch` verb): `addServerPatch` above and the
 * prop writes the panel makes, in a single undo entry, clamped by `enforceServerPatchSafety`.
 */
export function addServerPatchRect(
  uid: string,
  equipmentId: string,
  faceId: string,
  rect: { x: number; y: number; w: number; h: number },
): string | null {
  const project = store.getProject()
  if (!project) return null
  const model = store.findConstruction(project, uid)?.model
  if (!model || !serversOf(model)[equipmentId]) return null
  const id = nextServerPatchId(project)
  store.edit('srvAddPatch', `${uid}/${equipmentId}/${faceId}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    const server = target ? serversOf(target)[equipmentId] : null
    if (!server) return
    const patch = { ...defaultServerPatch(id), x: rect.x, y: rect.y, w: rect.w, h: rect.h }
    enforceServerPatchSafety(patch)
    faceBag(server, faceId).patches!.push(patch)
  })
  return id
}

export function deleteServerPatch(
  uid: string,
  equipmentId: string,
  faceId: string,
  patchId: string,
) {
  store.edit('srvDeletePatch', `${uid}/${equipmentId}/${faceId}/${patchId}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    const server = target ? serversOf(target)[equipmentId] : null
    if (!server) return
    const face = faceBag(server, faceId)
    face.patches = face.patches!.filter((p) => p.id !== patchId)
  })
}

export function setServerFaceColor(
  uid: string,
  equipmentId: string,
  faceId: string,
  color: string,
) {
  store.edit('srvSetFaceColor', `${uid}/${equipmentId}/${faceId} -> ${color}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    const server = target ? serversOf(target)[equipmentId] : null
    if (!server) return
    faceBag(server, faceId).color = color
  })
}

/**
 * One checkpoint per slider DRAG, not per frame.
 *
 * A slider emits a value on every pixel of movement. Each through `edit` would
 * clone the project and push an undo entry, so undoing a single drag would take
 * a hundred presses. The legacy solves it with a 500 ms flag
 * (`_srvPatchSliderCheckpointed`, STL_generate.html:9990) and this keeps that
 * exact shape, so the two apps produce undo stacks of the same depth.
 */
let sliderCheckpointed = false
let sliderTimer: ReturnType<typeof setTimeout> | null = null

function checkpointOncePerDrag(action: string) {
  if (sliderCheckpointed) return
  store.pushUndoCheckpoint(action)
  sliderCheckpointed = true
  if (sliderTimer) clearTimeout(sliderTimer)
  sliderTimer = setTimeout(() => {
    sliderCheckpointed = false
    sliderTimer = null
  }, 500)
}

/** Reset the drag window — for tests, which must not depend on a wall clock. */
export function resetServerPatchDragWindow() {
  if (sliderTimer) clearTimeout(sliderTimer)
  sliderTimer = null
  sliderCheckpointed = false
}

export type ServerPatchKey = 'type' | 'color' | 'x' | 'y' | 'w' | 'h' | 'cx' | 'cy' | 'r'

/**
 * Write one property of a server patch.
 *
 * Geometry keys are clamped after the write; the type key converts the shape.
 * Mutates the live project rather than cloning it, so a drag stays one undo
 * entry — the checkpoint above is what makes that honest.
 */
export function setServerPatchProp(
  uid: string,
  equipmentId: string,
  faceId: string,
  patchId: string,
  key: ServerPatchKey,
  value: string | number,
) {
  const project = store.getProject()
  if (!project) return
  checkpointOncePerDrag('srvSetPatchProp')
  store.mutateLive((live) => {
    const target = store.findConstruction(live, uid)?.model
    const server = target ? serversOf(target)[equipmentId] : null
    if (!server) return
    const patch = (faceBag(server, faceId).patches ?? []).find((p) => p.id === patchId)
    if (!patch) return
    if (key === 'type') {
      convertServerPatchType(patch, value === 'circle' ? 'circle' : 'rect')
    } else if (key === 'color') {
      patch.color = String(value)
    } else {
      ;(patch as unknown as Record<string, number>)[key] = Number(value)
      enforceServerPatchSafety(patch)
    }
  })
  store.notifyLiveEdit('srvSetPatchProp', `${equipmentId}/${faceId}/${patchId}.${key}`)
}
