import { expect, it } from 'vitest'
import * as THREE from 'three'
import { buildMeshPreviewOverlay, meshOverlayRevision } from '../src/renderer/meshPreviewOverlay'
import { getMeshPreviewSession, type MeshPreviewSession } from '../src/run/meshPreviewSession'

function preview(): MeshPreviewSession {
  const positions = new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0])
  const surfaceGeometry = new THREE.BufferGeometry()
  surfaceGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  surfaceGeometry.setIndex([0, 1, 2, 0, 2, 3])
  surfaceGeometry.computeVertexNormals()
  const edgeGeometry = new THREE.BufferGeometry()
  edgeGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  // Four original polygon edges: no [0, 2] triangle diagonal.
  edgeGeometry.setIndex([0, 1, 1, 2, 2, 3, 3, 0])
  const surface = new THREE.Mesh(surfaceGeometry, new THREE.MeshPhongMaterial({
    color: 0x7799bb, transparent: true, opacity: 0.65, side: THREE.DoubleSide,
  }))
  const edges = new THREE.LineSegments(edgeGeometry, new THREE.LineBasicMaterial({ color: 0x112244 }))
  const group = new THREE.Group()
  group.add(surface, edges)
  return {
    ...getMeshPreviewSession(),
    meshSnapshotKey: 'stat-revision-500-cell',
    built: { group, byPatch: new Map([['rack/front', { surface, edges }]]),
      merged: false, source: null, mergedPair: null, mergedRanges: null },
    patches: [{ name: 'rack/front', nPoints: 4, nTriangles: 2, nEdges: 4, defaultVisible: true }],
    hidden: new Set<string>(), visible: true, meshOpacity: 0.65,
  }
}

it('transfers a mesh patch and its original polygon edges with source identity, not a fake model UID', () => {
  const result = buildMeshPreviewOverlay('project-synthetic', preview())
  expect(result.schema).toBe('dcdtuni.render-overlay/v1')
  expect(result.source).toEqual({ kind: 'meshPreview', meshSnapshotKey: 'stat-revision-500-cell' })
  expect(result.primitives).toHaveLength(2)
  expect(result.primitives.map(p => [p.kind, p.patchName, p.visible])).toEqual([
    ['triangles', 'rack/front', true], ['lines', 'rack/front', true],
  ])
  expect(result.primitives[0]!.material.opacity).toBe(0.65)
  expect(result.primitives[1]!.indices).toEqual([0, 1, 1, 2, 2, 3, 3, 0])
  expect('modelUid' in result.primitives[0]!).toBe(false)
})

it('keeps patch visibility and clearing explicit without inventing mesh provenance', () => {
  const hidden = preview()
  hidden.hidden = new Set(['rack/front'])
  expect(buildMeshPreviewOverlay('project-synthetic', hidden).primitives.every(p => !p.visible)).toBe(true)
  hidden.built = null
  hidden.meshSnapshotKey = null
  expect(buildMeshPreviewOverlay('project-synthetic', hidden)).toMatchObject({
    source: { kind: 'meshPreview', meshSnapshotKey: null }, primitives: [],
  })
})

it('changes the OV sync identity for visibility and opacity, not patch-list folding', () => {
  const state = preview()
  const initial = meshOverlayRevision(state)
  expect(meshOverlayRevision({ ...state, collapsed: { rack: true } })).toBe(initial)
  expect(meshOverlayRevision({ ...state, hidden: new Set(['rack/front']) })).not.toBe(initial)
  expect(meshOverlayRevision({ ...state, meshOpacity: 0.2 })).not.toBe(initial)
  expect(meshOverlayRevision({ ...state, visible: false })).not.toBe(initial)
})
