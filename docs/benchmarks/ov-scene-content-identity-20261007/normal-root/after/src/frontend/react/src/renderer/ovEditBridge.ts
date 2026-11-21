/**
 * CPU-testable Kit -> source project edit gate. The live Kit selection stream
 * supplies picks; motion commands remain gated on an explicit edit whitelist.
 * The world pose is the MODEL ANCHOR, not an arbitrary child USD prim matrix.
 */
import Ajv2020 from 'ajv/dist/2020'
import schema from '../../../shared/schema/ov-edit-event.schema.json'
import * as store from '../projectStore'
import { cfdrunExport } from '../legacy/cfdrunBuild'
import type { SceneSnapshot } from './sceneSnapshot'
import { validateSceneSnapshot } from './validateScene'

type Axis = 'x' | 'y' | 'z'
type Quaternion = { x: number; y: number; z: number; w: number }
type Vec = { x: number; y: number; z: number }
interface OvEventIdentity {
  schema: 'dcdtuni.ov-edit/v1'
  sceneRevision: number
  projectHash: string
  coordinateSystem: { upAxis: 'Y'; handedness: 'right'; metersPerUnit: 1 }
}
interface OvModelEditEvent extends OvEventIdentity {
  primId: string
  modelUid: string
  action: { type: 'pick' } | { type: 'translate' } | { type: 'rotate'; axis: Axis; degrees: number }
  worldTransform?: { positionMeters: [number, number, number]; quaternion: [number, number, number, number]; scale: [number, number, number] }
}
interface OvSelectionEvent extends OvEventIdentity {
  action: { type: 'selection' }
  selection: {primId: string; modelUid: string}[]
}
export type OvEditEvent = OvModelEditEvent | OvSelectionEvent
function isSelectionEvent(event: OvEditEvent): event is OvSelectionEvent { return event.action.type === 'selection' }
export type OvEditResult =
  | { ok: true; action: 'selection'; modelUids: string[] }
  | { ok: true; action: OvModelEditEvent['action']['type']; modelUid: string }
  | { ok: false; reason: 'invalid_event' | 'stale_scene' | 'stale_project' |
      'unknown_primitive' | 'not_editable' | 'model_mismatch' | 'unsupported_model' |
      'axis_locked' | 'transform_mismatch' | 'no_change' | 'rejected_by_project' | 'editing_disabled' }

export interface OvEditBridgeOptions {
  snapshot: SceneSnapshot
  /** Monotone scene generation issued when this exact packet is ACKed by Kit. */
  sceneRevision: number
  /** Must change on every load, visual-layer swap or replacement snapshot. */
  currentSceneRevision(): number
  /** Explicit source-geometry whitelist; no primitive is editable by default. */
  editablePrimitiveIds: ReadonlySet<string>
  /** Selection can include source geometry that is not yet safe to move. */
  pickablePrimitiveIds?: ReadonlySet<string>
  /** Host checks the same active-tab/job freeze as the Three viewport. */
  canEdit(): boolean
  onSelection(uids: string[]): void
}

const validate = new Ajv2020({ strict: true, strictNumbers: true }).compile<OvEditEvent>(schema)
const POS_TOL_M = 1e-6
const Q_TOL = 1e-6

function near(a: number, b: number, tolerance: number) { return Math.abs(a - b) <= tolerance }
function samePosition(a: readonly number[], b: Vec) {
  return near(a[0]!, b.x, POS_TOL_M) && near(a[1]!, b.y, POS_TOL_M) && near(a[2]!, b.z, POS_TOL_M)
}
function sameRotation(a: readonly number[], b: Quaternion) {
  const values = [b.x, b.y, b.z, b.w]
  return [1, -1].some(sign => a.every((v, i) => near(v, sign * values[i]!, Q_TOL)))
}
function sameScale(a: readonly number[], b: Vec) {
  return near(a[0]!, b.x, Q_TOL) && near(a[1]!, b.y, Q_TOL) && near(a[2]!, b.z, Q_TOL)
}
function normalized(q: readonly number[]) {
  return near(Math.hypot(...q), 1, Q_TOL)
}
function currentHash(project: unknown) { return cfdrunExport().projectContentHashSync(project) }

export function createOvEditBridge(options: OvEditBridgeOptions) {
  validateSceneSnapshot(options.snapshot)
  if (!Number.isSafeInteger(options.sceneRevision) || options.sceneRevision < 0) throw new Error('Invalid OV scene revision')
  const project = store.getProject()
  if (!project?.uid || project.uid !== options.snapshot.project.uid) throw new Error('OV scene does not match loaded project')
  const projectUid = project.uid
  const projectHash = currentHash(project)
  const primitives = new Map(options.snapshot.primitives.map(p => [p.id, p]))
  const editable = new Set(options.editablePrimitiveIds)
  for (const id of editable) if (!primitives.has(id)) throw new Error(`Editable OV primitive is absent from scene: ${id}`)
  const pickable = new Set(options.pickablePrimitiveIds ?? options.editablePrimitiveIds)
  for (const id of pickable) if (!primitives.has(id)) throw new Error(`Pickable OV primitive is absent from scene: ${id}`)

  function apply(raw: unknown): OvEditResult {
    if (!validate(raw) || (!isSelectionEvent(raw) && raw.worldTransform && !normalized(raw.worldTransform.quaternion))) {
      return { ok: false, reason: 'invalid_event' }
    }
    const event = raw
    if (event.sceneRevision !== options.sceneRevision || options.currentSceneRevision() !== options.sceneRevision) {
      return { ok: false, reason: 'stale_scene' }
    }
    const live = store.getProject()
    // Store repaint counters include logs/save. Source UID/hash and the accepted
    // scene generation continue to fence geometry, hidden models and late events.
    if (!live || live.uid !== projectUid ||
        currentHash(live) !== projectHash || event.projectHash !== projectHash) {
      return { ok: false, reason: 'stale_project' }
    }
    if (!options.canEdit()) return { ok: false, reason: 'editing_disabled' }
    if (isSelectionEvent(event)) {
      const uids: string[] = []
      for (const item of event.selection) {
        const primitive = primitives.get(item.primId)
        if (!primitive) return { ok: false, reason: 'unknown_primitive' }
        if (!pickable.has(item.primId) || !primitive.visible) return { ok: false, reason: 'not_editable' }
        if (primitive.modelUid !== item.modelUid || uids.includes(item.modelUid)) return { ok: false, reason: 'model_mismatch' }
        const hit = store.findConstruction(live, item.modelUid)
        if (!hit || hit.model.uid !== item.modelUid) return { ok: false, reason: 'model_mismatch' }
        uids.push(item.modelUid)
      }
      // Apply only after every item validates: a malformed mixed event must not
      // silently clear or partially replace the common React selection.
      options.onSelection(uids)
      return { ok: true, action: 'selection', modelUids: uids }
    }
    const primitive = primitives.get(event.primId)
    if (!primitive) return { ok: false, reason: 'unknown_primitive' }
    if (event.action.type === 'pick' ? !pickable.has(event.primId) : !editable.has(event.primId)) {
      return { ok: false, reason: 'not_editable' }
    }
    if (primitive.modelUid !== event.modelUid) return { ok: false, reason: 'model_mismatch' }
    const hit = store.findConstruction(live, event.modelUid)
    if (!hit || hit.model.uid !== event.modelUid) return { ok: false, reason: 'model_mismatch' }
    if (event.action.type === 'pick') {
      options.onSelection([event.modelUid])
      return { ok: true, action: 'pick', modelUid: event.modelUid }
    }
    if (hit.bucket === 'pipeSegments' || !hit.model.geometry?.location || !event.worldTransform) {
      return { ok: false, reason: 'unsupported_model' }
    }
    const location = hit.model.geometry.location
    const quaternion = hit.model.geometry.quaternion ?? { x: 0, y: 0, z: 0, w: 1 }
    const { positionMeters, quaternion: resultQ, scale: resultScale } = event.worldTransform
    if (!sameScale(resultScale, hit.model.geometry.scale ?? { x: 1, y: 1, z: 1 })) {
      return { ok: false, reason: 'transform_mismatch' }
    }
    if (event.action.type === 'translate') {
      if (!sameRotation(resultQ, quaternion)) return { ok: false, reason: 'transform_mismatch' }
      const tool = hit.model.moveTool as { enabled?: boolean; axes?: Record<Axis, boolean> } | undefined
      const delta = { x: positionMeters[0]! - location.x, y: positionMeters[1]! - location.y,
        z: positionMeters[2]! - location.z }
      const axes: Axis[] = ['x', 'y', 'z']
      if (axes.every(axis => near(delta[axis], 0, POS_TOL_M))) return { ok: false, reason: 'no_change' }
      if (!tool?.enabled || axes.some(axis => !near(delta[axis], 0, POS_TOL_M) && !tool.axes?.[axis])) {
        return { ok: false, reason: 'axis_locked' }
      }
      if (!store.setModelLocation(event.modelUid, { x: positionMeters[0]!, y: positionMeters[1]!, z: positionMeters[2]! })) {
        return { ok: false, reason: 'rejected_by_project' }
      }
      return { ok: true, action: 'translate', modelUid: event.modelUid }
    }
    if (!samePosition(positionMeters, location)) return { ok: false, reason: 'transform_mismatch' }
    const { axis, degrees } = event.action
    if (Math.abs(degrees) < 1e-6) return { ok: false, reason: 'no_change' }
    const tool = hit.model.rotateTool as { enabled?: boolean; axes?: { yaw?: boolean; pitch?: boolean; roll?: boolean } } | undefined
    const ring = axis === 'y' ? 'yaw' : axis === 'x' ? 'pitch' : 'roll'
    if (!tool?.enabled || !tool.axes?.[ring]) return { ok: false, reason: 'axis_locked' }
    const letter = axis.toUpperCase() as 'X' | 'Y' | 'Z'
    const expected = store.multiplyQuaternions(store.axisQuaternion(letter, degrees), quaternion)
    if (!sameRotation(resultQ, expected)) return { ok: false, reason: 'transform_mismatch' }
    store.applyModelTransform(event.modelUid, { kind: 'rot', axis: letter, deg: degrees })
    return { ok: true, action: 'rotate', modelUid: event.modelUid }
  }
  return { projectHash, apply }
}
