import * as THREE from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Side-effect import: installs window.THREE / polygonClipping and loads the
// shared js modules (rack_state, stl_naming, stl_writer, geometry_pure,
// geometry_scene) the builders below resolve lazily.
import '../src/legacy/geometryBuilder'
import { createCfdrunBuild, cfdrunExport, type CfdrunContext } from '../src/legacy/cfdrunBuild'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createRack } from '../src/legacy/factories'
import { stlNaming } from '../src/legacy/stlNaming'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { resetPreviewForTest } from '../src/io/stlPreview'
import { applyServerOpacityToMeshes } from '../src/geometry/serverOpacity'
import { applyModelOpacityState } from '../src/geometry/modelOpacity'
import type { CfdProject, Construction } from '../src/schema/cfdproj'

/**
 * WP-W6: the server is a CLOSED BOX again.
 *
 * Two confirmed defects, one root each, both in the shared geometry_scene:
 *
 * 1. `_buildSingleServerFace` returned early for `left`/`right`, so a server
 *    had NO side geometry at all — lowering its opacity let the user stare
 *    straight through the rack cavity with nothing marking the server's sides.
 * 2. `doubleSide = !isInletOutlet` rendered inlet/outlet single-sided, so a
 *    transparent server backface-culled its FAR inlet/outlet as well.
 *
 * The fix builds all six faces double-sided. The left/right faces coincide
 * with the rack frame's inner cavity walls, so everything DOWNSTREAM of the
 * scene has to keep telling the two apart, and that is what this file pins:
 *
 * - the STL export routes left/right to their OWN bucket
 *   (merged_server_interface_walls.stl) and the fluid faces stay in theirs;
 * - the cfdrun manifest (v1 server.faces) and the V2 boundaryBindings keep
 *   enumerating ONLY inlet/outlet/top/bottom — the mesh path never sees the
 *   interface walls, so today's closed fluid partition (frame cavity walls +
 *   four server faces) is unchanged;
 * - the six face meshes carry the SAME userData as the five that always
 *   existed, so picking and the per-equipmentId opacity fade cover them.
 */

// ---------------------------------------------------------------------------
// Fixtures: one rack, one 2U fan server, one 1U nonFan block
// ---------------------------------------------------------------------------

interface ServerView {
  id: string
  name: string
  kind: string
  startUnit: number
  spanUnits: number
}

type ServerType = 'fanServer' | 'nonFan'

/** All six faces, empty — the V4 shape `getDefaultEquipmentFaces` produces. */
function emptyFaces(): Record<string, { patches: unknown[] }> {
  return {
    inlet: { patches: [] },
    outlet: { patches: [] },
    top: { patches: [] },
    bottom: { patches: [] },
    left: { patches: [] },
    right: { patches: [] },
  }
}

/**
 * A V4 server record written straight into the dict (the rackExportNaming
 * pattern): depth setbacks included so the server box has positive extent.
 */
function addServer(
  rack: Construction,
  uid: string,
  name: string,
  type: ServerType,
  slotPosition: number,
  slotOccupation: number,
): void {
  const servers = (rack.constructions as { servers: Record<string, unknown> }).servers
  servers[uid] = {
    uid,
    type,
    geometry: { slotPosition, slotOccupation },
    cooling: {},
    constructions: { chips: {}, coldPlates: {} },
    meta: { name, enabled: true },
    faces: emptyFaces(),
    inletOffset: 0.05,
    outletOffset: 0.05,
    liquidCooling: null,
  }
}

function rackWithServers(): { project: CfdProject; rack: Construction } {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  // A footprint keeps buildCfdrunJson off its scene-bbox fallback.
  project.geometry = { height: 3, plane: [[0, 0], [6, 0], [6, 4], [0, 4]] }
  const { model: rack } = createRack(project)
  ;(stateV4() as unknown as { addConstruction(p: unknown, m: unknown): void }).addConstruction(project, rack)
  addServer(rack, 'srv_fan', 'Fan_A', 'fanServer', 9, 2)
  addServer(rack, 'srv_block', 'Block_B', 'nonFan', 19, 1)
  // The factory rack is open-backed: EVERY back slot is deleted, and a deleted
  // back slot legitimately suppresses the outlet face of the server sitting in
  // it. Close the back behind the two servers so all six faces build.
  const deleted = (rack as unknown as { deletedFaces: { back: boolean[] } }).deletedFaces
  for (const i of [9, 10, 19]) deleted.back[i] = false
  return { project, rack }
}

function stemOf(rack: Construction, uid: string): string {
  const views = stlNaming().getRackServerViews(rack) as unknown as ServerView[]
  const view = views.find((v) => v.id === uid)
  if (!view) throw new Error(`no server "${uid}" on the rack`)
  return stlNaming().getRackEquipmentExportStem(rack, view) ?? ''
}

function host(project: CfdProject, webgl: boolean): SceneHost {
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  const scene = new THREE.Group()
  scene.add(wrapperGroup)
  wrapperGroup.add(globalGroup)
  return {
    globalGroup,
    wrapperGroup,
    registries: emptyRegistries(),
    selectedUid: () => null,
    project: () => project,
    webglAvailable: webgl,
    previewMode: () => false,
  }
}

function uids(project: CfdProject): string[] {
  return (stateV4().allModels(project) as { uid: string }[]).map((m) => m.uid)
}

function solidNames(text: string | undefined): string[] {
  if (!text) return []
  return [...text.matchAll(/^solid (.+)$/gm)].map((m) => m[1]!.trim())
}

function setAllModels(project: CfdProject): void {
  const w = window as unknown as { allModels?: () => Construction[] }
  w.allModels = () => stateV4().allModels(project) as Construction[]
}

const LENGTH_UNITS = {
  mm: { scale: 0.001, label: 'mm', step: 0.1, precision: 4 },
  cm: { scale: 0.01, label: 'cm', step: 0.01, precision: 4 },
  m: { scale: 1, label: 'm', step: 0.0001, precision: 4 },
}
const _MESH_MODES = ['bodyFitted', 'cartesianLocalized', 'structured']

beforeEach(() => {
  resetPreviewForTest()
})

afterEach(() => {
  vi.restoreAllMocks()
  delete (window as unknown as { allModels?: unknown }).allModels
})

// ---------------------------------------------------------------------------
// (a) The grouped STL map: left/right in their own bucket, nowhere else
// ---------------------------------------------------------------------------

describe('the grouped STL export routes the interface walls', () => {
  it('puts left/right in merged_server_interface_walls.stl and in no other bucket', () => {
    const { project, rack } = rackWithServers()
    const h = host(project, false)
    const builder = createSceneBuilder(h)
    builder.rebuild()
    const grouped = builder.api.buildGroupedStlMap(uids(project), false)

    const fan = stemOf(rack, 'srv_fan')
    const block = stemOf(rack, 'srv_block')
    const interfaceSolids = [`${fan}_left`, `${fan}_right`, `${block}_left`, `${block}_right`]

    const iface = grouped['merged_server_interface_walls.stl']
    expect(iface, 'the interface-wall bucket must be written').toBeDefined()
    expect(solidNames(iface).sort()).toEqual([...interfaceSolids].sort())

    // No other file carries any of them — in particular not the U-span server
    // buckets, whose stem-prefix routing would otherwise absorb them.
    for (const [file, text] of Object.entries(grouped)) {
      if (file === 'merged_server_interface_walls.stl') continue
      for (const solid of interfaceSolids) {
        expect(solidNames(text), `${file} must not carry ${solid}`).not.toContain(solid)
      }
    }

    // The fluid faces stay in their U-span buckets, named exactly as before.
    const fanBucket = grouped['merged_server_2U.stl']
    expect(fanBucket).toBeDefined()
    expect(solidNames(fanBucket).sort()).toEqual(
      [`${fan}_inlet_patch`, `${fan}_outlet_patch`, `${fan}_top`, `${fan}_bottom`].sort(),
    )
    const blockBucket = grouped['merged_server_1U.stl']
    expect(blockBucket).toBeDefined()
    expect(solidNames(blockBucket).sort()).toEqual(
      [`${block}_front`, `${block}_back`, `${block}_top`, `${block}_bottom`].sort(),
    )
  })

  it('classifies interface-wall solids whole AND patch-subdivided', () => {
    const { rack } = rackWithServers()
    const fan = stemOf(rack, 'srv_fan')
    const rackName = stlNaming().sanitizeName(rack.meta.name)
    const classify = stlNaming().classifyRackSolidToBucketFile

    expect(classify(`${fan}_left`, rack)).toBe('merged_server_interface_walls.stl')
    expect(classify(`${fan}_right`, rack)).toBe('merged_server_interface_walls.stl')
    // A subdivided left/right face (patches + wall remainder) is still an
    // interface wall, not a fluid face.
    expect(classify(`${fan}_left_p0`, rack)).toBe('merged_server_interface_walls.stl')
    expect(classify(`${fan}_right_wall`, rack)).toBe('merged_server_interface_walls.stl')

    // Every other bucket is unchanged.
    expect(classify(`${fan}_inlet_patch`, rack)).toBe('merged_server_2U.stl')
    expect(classify(`${fan}_outlet_patch`, rack)).toBe('merged_server_2U.stl')
    expect(classify(`${fan}_top`, rack)).toBe('merged_server_2U.stl')
    // Frame solids ending in _left/_right are caught by the frame rule FIRST.
    expect(classify(`${rackName}_frame_outer_left`, rack)).toBeNull()
    expect(classify(`${rackName}_frame_outer_right`, rack)).toBeNull()
    // Blanking panels keep their porous bucket.
    expect(classify(`${rackName}_inlet_u3`, rack)).toBe('merged_perforatedBlankingPanel_1U.stl')
  })
})

// ---------------------------------------------------------------------------
// (b) The cfdrun manifest and the V2 bindings never enumerate left/right
// ---------------------------------------------------------------------------

interface FacesEntry {
  solid: string | null
  patches: { solid: string; patchId: string }[]
  wallSolid: string | null
}

interface StlMapEntry {
  patchName?: string
  solidName?: string
  source?: { modelUid?: string; faceId?: string; patchId?: string }
}

interface Binding {
  patchName: string
  solidName?: string
  bcType: string
  role: string
}

function translateRackServers(
  project: CfdProject,
  bySource: Record<string, { patchName: string; solidName?: string }>,
): Record<string, Binding> {
  const mod = cfdrunExport() as unknown as {
    translateRackServers(
      state: unknown,
      ctx: { bySource: Record<string, unknown> },
    ): { boundaryBindings: Record<string, Binding> }
  }
  return mod.translateRackServers(project, { bySource }).boundaryBindings
}

describe('the cfdrun manifest keeps the interface walls out', () => {
  it('v1 server.faces enumerates exactly the four fluid faces, plus the informational pair', () => {
    const { project } = rackWithServers()
    setAllModels(project)
    const ctx = {
      state: project,
      globalGroup: { children: [] },
      LENGTH_UNITS,
      _MESH_MODES,
    } as unknown as CfdrunContext
    const blob = createCfdrunBuild(ctx).buildCfdrunJson()

    const racks = blob.racks as Record<string, { servers?: Record<string, unknown>[] }>
    const servers = Object.values(racks).flatMap((r) => r.servers ?? [])
    expect(servers.length).toBe(2)
    for (const s of servers) {
      const faces = s.faces as Record<string, FacesEntry>
      expect(Object.keys(faces).sort()).toEqual(['bottom', 'inlet', 'outlet', 'top'])
      for (const f of Object.values(faces)) {
        for (const n of [f.solid, f.wallSolid, ...f.patches.map((p) => p.solid)]) {
          expect(n ?? '').not.toMatch(/_(left|right)(_p\d+)?$/)
        }
      }
      // INFORMATIONAL only — the runner must not depend on it. `id` IS the
      // export stem, so the pair is exactly the two interface-wall solids.
      expect(s.interfaceSolidNames).toEqual([`${s.id as string}_left`, `${s.id as string}_right`])
    }
  })

  it('stlMap and the V2 boundaryBindings carry no left/right surface', () => {
    const { project, rack } = rackWithServers()
    setAllModels(project)
    const h = host(project, false)
    const builder = createSceneBuilder(h)
    builder.rebuild()
    const ctx = {
      state: project,
      globalGroup: h.globalGroup,
      LENGTH_UNITS,
      _MESH_MODES,
      buildGroupedStlMap: (ids: string[], leak: boolean) => builder.api.buildGroupedStlMap(ids, leak),
    } as unknown as CfdrunContext
    const stlMap = createCfdrunBuild(ctx).buildComprehensiveStlMap(project) as unknown as Record<
      string,
      StlMapEntry
    >

    const fan = stemOf(rack, 'srv_fan')
    const block = stemOf(rack, 'srv_block')
    // The interface walls are exported solids but NOT stlMap entries — so they
    // get no source triple and the bySource join can never reach them.
    expect(stlMap[`${fan}_left`]).toBeUndefined()
    expect(stlMap[`${fan}_right`]).toBeUndefined()
    expect(stlMap[`${block}_left`]).toBeUndefined()
    expect(stlMap[`${block}_right`]).toBeUndefined()
    // The fluid faces are entries, as before.
    expect(stlMap[`${fan}_inlet_patch`]).toBeDefined()
    expect(stlMap[`${block}_front`]).toBeDefined()

    // bySource exactly the way buildManifest builds it.
    const bySource: Record<string, { patchName: string; solidName?: string }> = {}
    for (const [solidName, entry] of Object.entries(stlMap)) {
      const src = entry.source
      if (src && src.modelUid != null && src.faceId != null && src.patchId != null) {
        bySource[`${src.modelUid}|${src.faceId}|${src.patchId}`] = {
          patchName: entry.patchName ?? solidName,
          solidName,
        }
      }
    }
    const bindings = translateRackServers(project, bySource)
    const keys = Object.keys(bindings)
    expect(keys.length).toBeGreaterThan(0)
    for (const k of keys) {
      expect(k).not.toMatch(/_(left|right)(_p\d+)?$/)
      expect(bindings[k]!.solidName ?? '').not.toMatch(/_(left|right)(_p\d+)?$/)
    }
    // And the four fluid faces ARE bound, with their usual roles.
    expect(bindings[`bb_${fan}_inlet_patch`]?.bcType).toBe('fan_inlet')
    expect(bindings[`bb_${fan}_outlet_patch`]?.bcType).toBe('fan_outlet')
    expect(bindings[`bb_${fan}_top`]?.bcType).toBe('wall')
    expect(bindings[`bb_${block}_front`]?.bcType).toBe('nonfan_fixed_temp')
    expect(bindings[`bb_${block}_back`]?.bcType).toBe('nonfan_fixed_temp')
  })

  it('excludes left/right even when a join target for them EXISTS', () => {
    // The exclusion is the FACE_ORDER list, not an accident of the join index
    // being empty: feed translateRackServers a bySource that DOES carry
    // left/right entries and it still must not bind them.
    const { project, rack } = rackWithServers()
    const fan = stemOf(rack, 'srv_fan')
    const bySource: Record<string, { patchName: string; solidName?: string }> = {
      'srv_fan|inlet|__face__': { patchName: `${fan}_inlet_patch`, solidName: `${fan}_inlet_patch` },
      'srv_fan|left|__face__': { patchName: `${fan}_left`, solidName: `${fan}_left` },
      'srv_fan|right|__face__': { patchName: `${fan}_right`, solidName: `${fan}_right` },
    }
    const bindings = translateRackServers(project, bySource)
    expect(bindings[`bb_${fan}_inlet_patch`]).toBeDefined()
    expect(bindings[`bb_${fan}_left`]).toBeUndefined()
    expect(bindings[`bb_${fan}_right`]).toBeUndefined()
    expect(rack.uid).toBeTruthy() // keep the fixture honest
  })
})

// ---------------------------------------------------------------------------
// (c) The scene builds all six faces, double-sided
// ---------------------------------------------------------------------------

/**
 * jsdom has no 2D canvas, and with `webglAvailable: true` the shared builder
 * draws procedural textures on one. A Proxy that answers every 2D-context
 * member with a no-op (and real zero-filled pixel buffers for the two
 * ImageData calls) is enough for the texture generators; the textures are
 * never rendered, only built.
 */
function stubCanvasAndTextures(): void {
  function fakeCtx(): unknown {
    const store: Record<string | symbol, unknown> = {}
    return new Proxy(store, {
      get(t, p) {
        if (p === 'getImageData' || p === 'createImageData') {
          return (...args: number[]) => {
            const w = p === 'getImageData' ? (args[2] ?? 1) : (args[0] ?? 1)
            const h = p === 'getImageData' ? (args[3] ?? 1) : (args[1] ?? 1)
            return { data: new Uint8ClampedArray(Math.max(1, w * h * 4)), width: w, height: h }
          }
        }
        if (p in t) return t[p]
        return () => undefined
      },
      set(t, p, v) {
        t[p] = v
        return true
      },
    })
  }
  const proto = HTMLCanvasElement.prototype as unknown as {
    getContext: (...args: unknown[]) => unknown
  }
  vi.spyOn(proto, 'getContext').mockImplementation(() => fakeCtx())
  vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation(() => new THREE.Texture())
}

function serverFaceMeshes(
  h: SceneHost,
  equipmentId: string,
): Map<string, THREE.Object3D[]> {
  const faces = new Map<string, THREE.Object3D[]>()
  for (const m of h.registries.renderMeshes) {
    const ud = m.userData as { equipmentId?: string; faceId?: string }
    if (ud.equipmentId !== equipmentId || !ud.faceId) continue
    faces.set(ud.faceId, [...(faces.get(ud.faceId) ?? []), m])
  }
  return faces
}

describe('the scene builds the server as a closed double-sided box', () => {
  it('builds six face meshes per server, every one DoubleSide', () => {
    stubCanvasAndTextures()
    const { project, rack } = rackWithServers()
    const h = host(project, true)
    createSceneBuilder(h).rebuild()

    const fan = stemOf(rack, 'srv_fan')
    const faces = serverFaceMeshes(h, 'srv_fan')
    expect([...faces.keys()].sort()).toEqual(
      ['bottom', 'inlet', 'left', 'outlet', 'right', 'top'],
    )
    for (const [faceId, meshes] of faces) {
      for (const m of meshes) {
        const mat = (m as THREE.Mesh).material as THREE.Material
        expect(mat.side, `server face "${faceId}" must render double-sided`).toBe(THREE.DoubleSide)
      }
    }
    // The interface walls carry their own export names — the same stems the
    // bucket router classifies.
    const exportName = (faceId: string) =>
      (faces.get(faceId)![0]!.userData as { exportName?: string }).exportName
    expect(exportName('left')).toBe(`${fan}_left`)
    expect(exportName('right')).toBe(`${fan}_right`)
    // The nonFan block is closed too.
    expect([...serverFaceMeshes(h, 'srv_block').keys()].sort()).toEqual(
      ['bottom', 'inlet', 'left', 'outlet', 'right', 'top'],
    )
  })
})

// ---------------------------------------------------------------------------
// (d) The per-equipmentId fade covers all six faces
// ---------------------------------------------------------------------------

describe('the per-server opacity fade covers all six faces', () => {
  it('fades every face mesh through userData.equipmentId, and only that server', () => {
    const { project, rack } = rackWithServers()
    const h = host(project, false)
    createSceneBuilder(h).rebuild()

    const root = h.registries.renderRootMap.get(rack.uid)!
    const fanMeshes = h.registries.renderMeshes.filter(
      (m) => (m.userData as { equipmentId?: string }).equipmentId === 'srv_fan',
    )
    const faceIds = new Set((fanMeshes.map((m) => (m.userData as { faceId?: string }).faceId)))
    expect([...faceIds].sort()).toEqual(['bottom', 'inlet', 'left', 'outlet', 'right', 'top'])

    // The two new faces carry the SAME userData contract as the old four, so
    // picking and the fade filter see them without knowing about them.
    for (const m of fanMeshes) {
      const ud = m.userData as Record<string, unknown>
      expect(ud.isRackEquipment).toBe(true)
      expect(ud.modelId).toBe(rack.uid)
      expect(ud.equipmentId).toBe('srv_fan')
      expect(ud.isPatch).toBe(false)
      expect(ud.patchId).toBeNull()
    }

    applyServerOpacityToMeshes(root, new Set(['srv_fan']), 40)
    for (const m of fanMeshes) {
      const mat = (m as THREE.Mesh).material as THREE.Material
      const ud = m.userData as { faceId?: string }
      expect(mat.opacity, `face ${ud.faceId} must fade`).toBeCloseTo(0.4, 12)
      expect(mat.transparent).toBe(true)
    }
    // The sibling server's six faces are untouched — the fade is per-server.
    const blockMeshes = h.registries.renderMeshes.filter(
      (m) => (m.userData as { equipmentId?: string }).equipmentId === 'srv_block',
    )
    expect(blockMeshes.length).toBeGreaterThan(0)
    for (const m of blockMeshes) {
      expect(((m as THREE.Mesh).material as THREE.Material).opacity).toBe(1)
    }
  })
})

// ---------------------------------------------------------------------------
// (e) The rack body's opacity is the FRAME's, not the servers'
// ---------------------------------------------------------------------------

describe('a rebuild after the rack body slider keeps the servers at their own opacity', () => {
  it('draws a server with no opacity of its own opaque under a 30% rack', () => {
    // The live preview already skipped the servers; the REBUILD after the
    // slider's release did not: a server with no stored opacity fell back to
    // the rack's, so the servers faded with the frame the moment the drag
    // ended (2026-09-23). The body knob fades the frame, blanking panels and
    // doors; a server's opacity is its own record's, absent = opaque.
    const { project, rack } = rackWithServers()
    // what the rack body slider commits: the model value, cascaded into its faces
    applyModelOpacityState(rack, 30)
    stubCanvasAndTextures()
    const h = host(project, true)
    createSceneBuilder(h).rebuild()
    const serverMeshes = h.registries.renderMeshes.filter(
      (m) => (m.userData as { equipmentId?: string }).equipmentId,
    )
    expect(serverMeshes.length).toBeGreaterThan(0)
    for (const m of serverMeshes) {
      const ud = m.userData as { equipmentId?: string; faceId?: string }
      expect(((m as THREE.Mesh).material as THREE.Material).opacity, `${ud.equipmentId} ${ud.faceId}`).toBe(1)
    }
    // ...while the frame itself did fade
    const frame = h.registries.renderMeshes.filter((m) => {
      const ud = m.userData as { modelId?: string; equipmentId?: string }
      return ud.modelId === rack.uid && !ud.equipmentId
    })
    expect(frame.some((m) => ((m as THREE.Mesh).material as THREE.Material).opacity < 0.5)).toBe(true)
  })

  it('still draws a server at the opacity it stores', () => {
    const { project, rack } = rackWithServers()
    const servers = (rack.constructions as { servers: Record<string, { meta: { display?: { opacity?: number } } }> }).servers
    servers.srv_fan!.meta.display = { opacity: 0.25 }
    stubCanvasAndTextures()
    const h = host(project, true)
    createSceneBuilder(h).rebuild()
    const fan = h.registries.renderMeshes.filter(
      (m) => (m.userData as { equipmentId?: string }).equipmentId === 'srv_fan',
    )
    expect(fan.length).toBeGreaterThan(0)
    for (const m of fan) expect(((m as THREE.Mesh).material as THREE.Material).opacity).toBeCloseTo(0.25, 12)
  })
})
