import * as THREE from 'three'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import { createOvEditBridge } from '../src/renderer/ovEditBridge'

function project(): CfdProject {
  return {
    uid: 'project-a', name: 'OV edit fixture',
    constructions: { racks: {
      rack: { uid: 'rack', meta: { name: 'Rack' },
        geometry: { location: { x: 1, y: 1, z: 1 }, size: { x: .6, y: 2, z: 1 },
          quaternion: { x: 0, y: 0, z: 0, w: 1 } },
        moveTool: { enabled: true, axes: { x: true, y: false, z: true } },
        rotateTool: { enabled: true, axes: { yaw: true, pitch: false, roll: false } },
      },
    } },
  }
}

let currentRevision = 7
const selected = vi.fn()
function fixture(canEdit: () => boolean = () => true) {
  const root = new THREE.Group()
  const rack = new THREE.Group()
  rack.userData.modelId = 'rack'
  rack.position.set(1, 1, 1)
  root.add(rack)
  rack.add(new THREE.Mesh(new THREE.BoxGeometry(.6, 2, 1), new THREE.MeshStandardMaterial()))
  // A CFD result may share the rack UID, but it is never an editable source prim.
  const result = new THREE.Mesh(new THREE.BoxGeometry(.6, 2, 1), new THREE.MeshBasicMaterial())
  result.name = 'result-temperature-layer'
  rack.add(result)
  const snapshot = buildSceneSnapshot({ root, project: store.getProject()! })
  const editableId = snapshot.primitives[0]!.id
  const resultId = snapshot.primitives[1]!.id
  const bridge = createOvEditBridge({ snapshot, sceneRevision: 7,
    editablePrimitiveIds: new Set([editableId]), currentSceneRevision: () => currentRevision,
    canEdit, onSelection: selected })
  const base = {
    schema: 'dcdtuni.ov-edit/v1', sceneRevision: 7, projectHash: bridge.projectHash,
    primId: editableId, modelUid: 'rack',
    coordinateSystem: { upAxis: 'Y', handedness: 'right', metersPerUnit: 1 },
    action: { type: 'pick' },
    worldTransform: { positionMeters: [1, 1, 1], quaternion: [0, 0, 0, 1], scale: [1, 1, 1] },
  }
  const event = (overrides: Record<string, unknown> = {}) => ({
    ...base, ...overrides,
    worldTransform: { ...base.worldTransform,
      ...(overrides.worldTransform as Record<string, unknown> | undefined) },
  })
  return { bridge, event, editableId, resultId, snapshot }
}

beforeEach(() => {
  store.loadProject(project(), null)
  currentRevision = 7
  selected.mockReset()
})

describe('OV edit events as one-commit project transactions', () => {
  it('maps an authenticated source pick to React selection without changing the project', () => {
    const { bridge, event } = fixture()
    const depth = store.undoDepth()
    expect(bridge.apply(event())).toEqual({ ok: true, action: 'pick', modelUid: 'rack' })
    expect(selected).toHaveBeenCalledWith(['rack'])
    expect(store.undoDepth()).toBe(depth)
  })

  it('accepts a Kit selection event without a model-anchor pose while keeping motion disabled', () => {
    const { event, editableId, snapshot } = fixture()
    const bridge = createOvEditBridge({ snapshot, sceneRevision: 7,
      currentSceneRevision: () => currentRevision,
      pickablePrimitiveIds: new Set([editableId]), editablePrimitiveIds: new Set(),
      canEdit: () => true, onSelection: selected })
    const picked = event()
    delete (picked as { worldTransform?: unknown }).worldTransform
    expect(bridge.apply(picked)).toEqual({ ok: true, action: 'pick', modelUid: 'rack' })
    expect(selected).toHaveBeenCalledWith(['rack'])
    expect(bridge.apply(event({ action: { type: 'translate' },
      worldTransform: { positionMeters: [1.25, 1, 1] } }))).toMatchObject({ ok: false, reason: 'not_editable' })
    expect(store.undoDepth()).toBe(0)
  })

  it('commits one allowed metric translation through the existing location setter and undo', () => {
    const { bridge, event } = fixture()
    expect(bridge.apply(event({ action: { type: 'translate' },
      worldTransform: { positionMeters: [1.25, 1, .8], quaternion: [0, 0, 0, 1] } }))).toEqual({ ok: true, action: 'translate', modelUid: 'rack' })
    expect(store.getProject()?.constructions?.racks?.rack?.geometry?.location).toEqual({ x: 1.25, y: 1, z: .8 })
    expect(store.undoDepth()).toBe(1)
    store.undo()
    expect(store.getProject()?.constructions?.racks?.rack?.geometry?.location).toEqual({ x: 1, y: 1, z: 1 })
  })

  it('commits an allowed yaw using the existing world-axis rotation and undo', () => {
    const { bridge, event } = fixture()
    const half = Math.SQRT1_2
    expect(bridge.apply(event({ action: { type: 'rotate', axis: 'y', degrees: 90 },
      worldTransform: { positionMeters: [1, 1, 1], quaternion: [0, half, 0, half] } }))).toEqual({ ok: true, action: 'rotate', modelUid: 'rack' })
    expect(store.getProject()?.constructions?.racks?.rack?.geometry?.quaternion?.y).toBeCloseTo(half)
    expect(store.undoDepth()).toBe(1)
    store.undo()
    expect(store.getProject()?.constructions?.racks?.rack?.geometry?.quaternion).toEqual({ x: 0, y: 0, z: 0, w: 1 })
  })

  it('rejects stale scene revisions and project hashes before any selection or edit', () => {
    const { bridge, event } = fixture()
    expect(bridge.apply(event({ sceneRevision: 6 }))).toMatchObject({ ok: false, reason: 'stale_scene' })
    currentRevision = 8
    expect(bridge.apply(event())).toMatchObject({ ok: false, reason: 'stale_scene' })
    currentRevision = 7
    expect(bridge.apply(event({ projectHash: '0'.repeat(64) }))).toMatchObject({ ok: false, reason: 'stale_project' })
    store.renameModel('rack', 'Changed since snapshot')
    expect(bridge.apply(event())).toMatchObject({ ok: false, reason: 'stale_project' })
    expect(selected).not.toHaveBeenCalled()
  })

  it('honours the host editor freeze before selection or geometry changes', () => {
    const { bridge, event } = fixture(() => false)
    expect(bridge.apply(event())).toMatchObject({ ok: false, reason: 'editing_disabled' })
    expect(bridge.apply(event({ action: { type: 'translate' },
      worldTransform: { positionMeters: [1.2, 1, 1], quaternion: [0, 0, 0, 1] } }))).toMatchObject({ ok: false, reason: 'editing_disabled' })
    expect(store.undoDepth()).toBe(0)
    expect(selected).not.toHaveBeenCalled()
  })

  it('rejects unknown, mismatched and result-layer primitives even when the model UID exists', () => {
    const { bridge, event, resultId } = fixture()
    expect(bridge.apply(event({ primId: 'unknown' }))).toMatchObject({ ok: false, reason: 'unknown_primitive' })
    expect(bridge.apply(event({ modelUid: 'not-rack' }))).toMatchObject({ ok: false, reason: 'model_mismatch' })
    expect(bridge.apply(event({ primId: resultId }))).toMatchObject({ ok: false, reason: 'not_editable' })
    expect(selected).not.toHaveBeenCalled()
    expect(store.undoDepth()).toBe(0)
  })

  it('rejects coordinate and action forgeries rather than guessing unit conversion', () => {
    const { bridge, event } = fixture()
    expect(bridge.apply(event({ coordinateSystem: { upAxis: 'Z', handedness: 'right', metersPerUnit: 1 } }))).toMatchObject({ ok: false, reason: 'invalid_event' })
    expect(bridge.apply(event({ coordinateSystem: { upAxis: 'Y', handedness: 'right', metersPerUnit: .01 } }))).toMatchObject({ ok: false, reason: 'invalid_event' })
    expect(bridge.apply(event({ worldTransform: { positionMeters: [NaN, 1, 1], quaternion: [0, 0, 0, 1] } }))).toMatchObject({ ok: false, reason: 'invalid_event' })
    expect(bridge.apply(event({ action: { type: 'delete' } }))).toMatchObject({ ok: false, reason: 'invalid_event' })
    expect(store.undoDepth()).toBe(0)
  })

  it('refuses an unrequested scale change in a claimed rigid-world transform', () => {
    const { bridge, event } = fixture()
    expect(bridge.apply(event({ action: { type: 'translate' },
      worldTransform: { positionMeters: [1.1, 1, 1], quaternion: [0, 0, 0, 1], scale: [2, 1, 1] } }))).toMatchObject({ ok: false, reason: 'transform_mismatch' })
    expect(store.undoDepth()).toBe(0)
  })

  it('rejects a construction whose stored UID disagrees with its bucket key', () => {
    const malformed = project()
    malformed.constructions!.racks!.rack!.uid = 'other-rack'
    store.loadProject(malformed, null)
    const { bridge, event } = fixture()
    expect(bridge.apply(event())).toMatchObject({ ok: false, reason: 'model_mismatch' })
    expect(selected).not.toHaveBeenCalled()
  })

  it('enforces gizmo axes and the claimed rigid transform before touching the store', () => {
    const { bridge, event } = fixture()
    expect(bridge.apply(event({ action: { type: 'translate' },
      worldTransform: { positionMeters: [1, 1.1, 1], quaternion: [0, 0, 0, 1] } }))).toMatchObject({ ok: false, reason: 'axis_locked' })
    expect(bridge.apply(event({ action: { type: 'translate' },
      worldTransform: { positionMeters: [1.1, 1, 1], quaternion: [0, .6, 0, .8] } }))).toMatchObject({ ok: false, reason: 'transform_mismatch' })
    expect(bridge.apply(event({ action: { type: 'rotate', axis: 'x', degrees: 90 },
      worldTransform: { positionMeters: [1, 1, 1], quaternion: [Math.SQRT1_2, 0, 0, Math.SQRT1_2] } }))).toMatchObject({ ok: false, reason: 'axis_locked' })
    expect(bridge.apply(event({ action: { type: 'rotate', axis: 'y', degrees: 90 },
      worldTransform: { positionMeters: [2, 1, 1], quaternion: [0, Math.SQRT1_2, 0, Math.SQRT1_2] } }))).toMatchObject({ ok: false, reason: 'transform_mismatch' })
    expect(store.undoDepth()).toBe(0)
  })
})
