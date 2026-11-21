/**
 * The mesh preview: draw the meshed patches over the model geometry.
 *
 * The question it answers is "did snappyHexMesh actually put a patch where I
 * put a solid" — so the mesh is drawn with its polygon EDGES, at 85% opacity,
 * over the geometry faded to 30%. Both numbers are the legacy's defaults
 * (STL_generate.html:1012-1023): opaque mesh over solid geometry hides which
 * of the two you are looking at, which is the whole point of the view.
 *
 * The wire format is the post-processing mesh format, so `decodeMesh` is
 * shared. Two endpoints: `/api/mesh-preview/load` returns the patch table,
 * `/api/mesh-preview/binary` the points, triangles and edges.
 *
 * Edges are the part worth getting right. The server sends the ORIGINAL
 * polygon edges — a hex cell face is one quad, four edges — and falling back to
 * `WireframeGeometry` triangulates it, drawing a diagonal across every face
 * that is not in the mesh. The fallback is still there for a server that sends
 * none, but a mesh with edges must use them.
 */

import * as THREE from 'three'
import { t } from '../i18n'
import { decodeMesh, type MeshMeta, type PatchMeta } from '../postprocess'

export interface MeshPreviewState {
  patches: PatchMeta[]
  hidden: Set<string>
  meshOpacity: number
  geometryOpacity: number
  loaded: boolean
}

export const DEFAULT_MESH_OPACITY = 0.85
export const DEFAULT_GEOMETRY_OPACITY = 0.3

/** Where each patch's points, triangles and edges start in the global arrays. */
export interface PatchOffsets {
  point: number
  triangle: number
  edge: number
}

export function patchOffsets(patches: PatchMeta[]): PatchOffsets[] {
  const out: PatchOffsets[] = []
  let point = 0
  let triangle = 0
  let edge = 0
  for (const p of patches) {
    out.push({ point, triangle, edge })
    point += p.nPoints
    triangle += p.nTriangles
    edge += p.nEdges ?? 0
  }
  return out
}

/**
 * Re-base a patch's global indices onto its own slice of the point array.
 *
 * Each patch gets its own BufferGeometry over a `subarray` of the shared
 * positions, so the indices — which count from the start of the WHOLE mesh —
 * have to be shifted down by the patch's point offset or they address another
 * patch's vertices.
 */
export function localIndices(global: Uint32Array, pointOffset: number): Uint32Array {
  const out = new Uint32Array(global.length)
  for (let i = 0; i < global.length; i++) out[i] = global[i]! - pointOffset
  return out
}

/**
 * Above this many patches the preview draws MERGED.
 *
 * One surface and one edge set — two draw calls — instead of two per patch.
 * A large data centre meshes into well over a thousand patches (200 racks at
 * seven patches each), so the per-patch form is ~2800 draw calls a frame and
 * the preview stops being usable on exactly the cases that most need looking
 * at. `_MP_MERGE_THRESHOLD` in the legacy (STL_generate.html:24438).
 */
export const MERGE_THRESHOLD = 200

/**
 * Room-shell patch names: the enclosure the fluid domain is bounded by.
 *
 * Every transparent layer alpha-compounds over what is behind it, and the
 * enclosure sits in front of every solid in the case — on a 200-rack room that
 * compound is a mass of dark "shadow" structure that reads as a broken mesh
 * (the same buffers at opacity 1 render clean). Hiding the shell by default
 * leaves the solids readable and one tick on the WALL / FLOOR group head brings
 * it back. Name-based because the server meta carries no "this is the shell"
 * flag; anchored prefixes so `merged_wall_baffle` — a baffle INSIDE the room —
 * is not caught by a substring like "wall".
 */
export function isEnclosurePatchName(name: string | undefined): boolean {
  return /^(Room_|Raised_Floor|domain_)/.test(name ?? '')
}

export interface BuiltPreview {
  group: THREE.Group
  /**
   * Per patch, so the list can toggle one without rebuilding.
   *
   * Populated in BOTH modes. In merged mode these objects are built but not
   * added to the group: highlighting and picking address them by name, and the
   * legacy keeps them for the same reason (it builds them, then removes them
   * from the group at :24439).
   */
  byPatch: Map<string, { surface: THREE.Mesh; edges: THREE.LineSegments }>
  /** True when the group holds one merged pair instead of 2N objects. */
  merged: boolean
  /**
   * What a re-merge needs: the decoded arrays, the patch table, and which
   * patches are currently drawn. Merging uses the GLOBAL position array with
   * global indices, so only the index buffer is rebuilt.
   */
  source: {
    positions: Float32Array
    indices: Uint32Array
    edges: Uint32Array
    patches: readonly PatchMeta[]
    visible: Set<string>
    opacity: number
  } | null
  /** The merged pair, so a re-merge can dispose the previous one. */
  mergedPair: { surface: THREE.Mesh; edges: THREE.LineSegments | null } | null
  /**
   * Where each patch's triangles sit in the merged index buffer that is ON
   * SCREEN, in buffer order — the only thing a raycast `faceIndex` on the
   * merged surface can be resolved against.
   *
   * Recorded by `rebuildMerged` as it writes the buffer, not derived from the
   * patch table: the buffer skips hidden patches, skips every `_slave`
   * duplicate and drops a degenerate triangle here and there, so a count
   * walked over `patches` minus `hidden` is off by every slave that precedes
   * the hit. With 26 blanking-panel pairs ahead of the racks that put a click
   * on a server one whole rack away (STDCT, 2026-09-22).
   */
  mergedRanges: MergedRange[] | null
}

export interface MergedRange {
  name: string
  /** First triangle of the patch in the merged index buffer. */
  first: number
  count: number
}

/** The patch a triangle of the merged surface belongs to, or null. */
export function mergedPatchAtTriangle(built: BuiltPreview, faceIndex: number): string | null {
  const ranges = built.mergedRanges
  if (!ranges || !Number.isInteger(faceIndex) || faceIndex < 0) return null
  let lo = 0
  let hi = ranges.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const r = ranges[mid]!
    if (faceIndex < r.first) hi = mid - 1
    else if (faceIndex >= r.first + r.count) lo = mid + 1
    else return r.name
  }
  return null
}

/** Build the preview group from the two payloads. */
export function buildMeshPreview(
  meta: MeshMeta,
  buffer: ArrayBuffer,
  opacity = DEFAULT_MESH_OPACITY,
): BuiltPreview {
  const decoded = decodeMesh(buffer, meta)
  const offsets = patchOffsets(decoded.patches)
  const group = new THREE.Group()
  group.name = 'meshPreview'
  const byPatch = new Map<string, { surface: THREE.Mesh; edges: THREE.LineSegments }>()
  const merged = decoded.patches.length > MERGE_THRESHOLD

  decoded.patches.forEach((p, i) => {
    const off = offsets[i]!
    const pos = decoded.positions.subarray(off.point * 3, (off.point + p.nPoints) * 3)
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    geo.setIndex(
      new THREE.BufferAttribute(
        localIndices(
          decoded.indices.subarray(off.triangle * 3, (off.triangle + p.nTriangles) * 3),
          off.point,
        ),
        1,
      ),
    )
    geo.computeVertexNormals()

    const surface = new THREE.Mesh(
      geo,
      new THREE.MeshPhongMaterial({
        color: 0x7799bb,
        transparent: true,
        opacity,
        side: THREE.DoubleSide,
        // Per-face normals: averaged vertex normals shade a sharp 90° edge
        // as a dark rounded bevel, which reads as a chamfered (or missing)
        // cell face when there is none.
        flatShading: true,
        // ON at every opacity, not only at 100%. With it off, a closed patch
        // blends its own far hull and every layer behind it in creation order
        // — every patch mesh shares the origin (0,0,0), so Three's transparent
        // sort has no key to order them by — which is the dark structural
        // "shadow" that reads as a broken mesh. Writing depth keeps the
        // NEAREST patch surface per pixel; the fade still blends over the
        // dimmed geometry, which draws first at renderOrder 0.
        depthWrite: true,
        polygonOffset: true,
        polygonOffsetFactor: 1,
        polygonOffsetUnits: 1,
        shininess: 30,
      }),
    )
    surface.visible = p.defaultVisible !== false
    // A baffle's side (FrontSide or DoubleSide) depends on whether its partner
    // is drawn too — set for every pair by `syncBaffleSides` below.
    // After the model geometry (0), so the blend is stable.
    surface.renderOrder = 100

    const nEdges = p.nEdges ?? 0
    let edgeGeo: THREE.BufferGeometry
    if (nEdges > 0) {
      edgeGeo = new THREE.BufferGeometry()
      edgeGeo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
      edgeGeo.setIndex(
        new THREE.BufferAttribute(
          localIndices(decoded.edges.subarray(off.edge * 2, (off.edge + nEdges) * 2), off.point),
          1,
        ),
      )
    } else {
      edgeGeo = new THREE.WireframeGeometry(geo)
    }
    const edges = new THREE.LineSegments(
      edgeGeo,
      new THREE.LineBasicMaterial({ color: 0x112244, transparent: true, opacity: 0.35 }),
    )
    edges.visible = surface.visible
    edges.renderOrder = 101

    // In merged mode these stay OUT of the scene: they exist so highlighting
    // and picking can still address a patch by name, and the merged pair below
    // is what actually draws.
    if (!merged) {
      group.add(surface)
      group.add(edges)
    }
    byPatch.set(p.name, { surface, edges })
  })

  const built: BuiltPreview = {
    group,
    byPatch,
    merged,
    source: merged
      ? {
          positions: decoded.positions,
          indices: decoded.indices,
          edges: decoded.edges,
          patches: decoded.patches,
          visible: new Set(
            decoded.patches.filter((p) => p.defaultVisible !== false).map((p) => p.name),
          ),
          opacity,
        }
      : null,
    mergedPair: null,
    mergedRanges: null,
  }
  if (merged) rebuildMerged(built)
  syncBaffleSides(built)
  return built
}

/** The other side of a baffle pair, or null for an ordinary patch. */
function bafflePartner(name: string): string | null {
  if (name.endsWith('_master')) return `${name.slice(0, -'_master'.length)}_slave`
  if (name.endsWith('_slave')) return `${name.slice(0, -'_slave'.length)}_master`
  return null
}

/** Whether the patch list has a patch checked (merged mode keeps that on `source`). */
function patchShown(built: BuiltPreview, name: string): boolean {
  if (built.merged) return built.source?.visible.has(name) ?? false
  return built.byPatch.get(name)?.surface.visible ?? false
}

/**
 * Draw each baffle sheet from the sides nothing else covers.
 *
 * A zero-thickness baffle arrives as a master/slave pair of COINCIDENT sheets
 * with opposite normals.
 *
 * - **Both drawn:** FrontSide each. DoubleSide, both would be visible from
 *   every side and z-fight into dark slots that read as holes in the sheet;
 *   one side each gives one surface per viewing direction.
 * - **One drawn** — the default, since the server hides every `_slave`: that
 *   one DoubleSide. FrontSide alone made the sheet invisible from the side its
 *   normal points away from, and unpickable there (the raycaster honours the
 *   side). On Tri_angle the U17 partition's master faces +y, into the U18..U23
 *   box, so from below the box had no bottom — a complete sheet that read as
 *   a missing one. The legacy draws every patch DoubleSide; the merged path
 *   keeps the master and draws it DoubleSide.
 *
 * In merged mode the per-patch surfaces are only the highlight overlay, and the
 * same rule keeps a highlighted master visible from both sides.
 */
export function syncBaffleSides(built: BuiltPreview): void {
  for (const [name, { surface }] of built.byPatch) {
    const partner = bafflePartner(name)
    if (!partner) continue
    const want = patchShown(built, partner) ? THREE.FrontSide : THREE.DoubleSide
    const mat = surface.material as THREE.MeshPhongMaterial
    if (mat.side !== want) {
      mat.side = want
      mat.needsUpdate = true
    }
  }
}

/**
 * Rebuild the single merged surface and edge set from what is visible.
 *
 * The GLOBAL position array is shared by both and never rebuilt — only the
 * index buffers are, by concatenating the ranges of the visible patches. That
 * is what makes a visibility toggle cheap enough to do on every checkbox, and
 * it is how the legacy does it (`_mpRebuildMerged`, STL_generate.html:24304).
 */
export function rebuildMerged(built: BuiltPreview): void {
  const src = built.source
  if (!src) return

  if (built.mergedPair) {
    for (const obj of [built.mergedPair.surface, built.mergedPair.edges]) {
      if (!obj) continue
      built.group.remove(obj)
      obj.geometry.dispose()
      ;(obj.material as THREE.Material).dispose()
    }
    built.mergedPair = null
  }
  built.mergedRanges = null

  const tri: number[] = []
  const edge: number[] = []
  const ranges: MergedRange[] = []
  let triOff = 0
  let edgeOff = 0
  const pos = src.positions
  for (const p of src.patches) {
    const nEdges = p.nEdges ?? 0
    // A zero-thickness baffle sheet is stored as a master/slave pair of
    // coincident, oppositely-wound triangles. Merging BOTH puts duplicate
    // opposite-wound triangles on one surface: computeVertexNormals cancels
    // them into garbage normals (dark slots that read as holes) and the two
    // copies z-fight. Keep the master side; it renders from both sides.
    const slaveDuplicate = p.name.endsWith('_slave')
    // Caps of one sheet must all wind the same way. Faces tagged onto a sheet
    // plane by the carve keep whatever winding the solid cut gave them, so a
    // sheet can carry a few reversed caps: under DoubleSide they shade as dark
    // slots, under FrontSide they vanish — both read as holes. Orient every
    // triangle of a baffle patch to the patch's first triangle.
    const unify = p.name.endsWith('_master') || p.name.endsWith('_slave')
    let ref: [number, number, number] | null = null
    if (unify && src.visible.has(p.name) && !slaveDuplicate) {
      // Majority normal first: a few outlier windings must not become the
      // reference, or the majority would be flipped into back-facing caps
      // (dark slots under DoubleSide, see-through under FrontSide).
      let sx = 0, sy = 0, sz = 0
      for (let t = triOff; t < triOff + p.nTriangles; t++) {
        const ia = src.indices[t * 3]
        const ib = src.indices[t * 3 + 1]
        const ic = src.indices[t * 3 + 2]
        if (ia === undefined || ib === undefined || ic === undefined) continue
        const a = ia * 3, b = ib * 3, c = ic * 3
        const ax = pos[a] ?? 0, ay = pos[a + 1] ?? 0, az = pos[a + 2] ?? 0
        const e1: [number, number, number] = [
          (pos[b] ?? 0) - ax,
          (pos[b + 1] ?? 0) - ay,
          (pos[b + 2] ?? 0) - az,
        ]
        const e2: [number, number, number] = [
          (pos[c] ?? 0) - ax,
          (pos[c + 1] ?? 0) - ay,
          (pos[c + 2] ?? 0) - az,
        ]
        sx += e1[1] * e2[2] - e1[2] * e2[1]
        sy += e1[2] * e2[0] - e1[0] * e2[2]
        sz += e1[0] * e2[1] - e1[1] * e2[0]
      }
      if (Math.hypot(sx, sy, sz) > 1e-12) ref = [sx, sy, sz]
    }
    if (src.visible.has(p.name) && !slaveDuplicate) {
      const first = tri.length / 3
      for (let t = triOff; t < triOff + p.nTriangles; t++) {
        const ia = src.indices[t * 3]
        const ib = src.indices[t * 3 + 1]
        const ic = src.indices[t * 3 + 2]
        if (ia === undefined || ib === undefined || ic === undefined) continue
        const a = ia * 3
        const b = ib * 3
        const c = ic * 3
        const ax = pos[a] ?? 0, ay = pos[a + 1] ?? 0, az = pos[a + 2] ?? 0
        const e1: [number, number, number] = [
          (pos[b] ?? 0) - ax,
          (pos[b + 1] ?? 0) - ay,
          (pos[b + 2] ?? 0) - az,
        ]
        const e2: [number, number, number] = [
          (pos[c] ?? 0) - ax,
          (pos[c + 1] ?? 0) - ay,
          (pos[c + 2] ?? 0) - az,
        ]
        const n: [number, number, number] = [
          e1[1] * e2[2] - e1[2] * e2[1],
          e1[2] * e2[0] - e1[0] * e2[2],
          e1[0] * e2[1] - e1[1] * e2[0],
        ]
        if (ref === null) {
          if (Math.hypot(n[0], n[1], n[2]) < 1e-12) continue
          ref = [n[0], n[1], n[2]]
          tri.push(ia, ib, ic)
          continue
        }
        const dot = n[0] * ref[0] + n[1] * ref[1] + n[2] * ref[2]
        if (unify && dot < 0) tri.push(ia, ic, ib)
        else tri.push(ia, ib, ic)
      }
      const count = tri.length / 3 - first
      if (count > 0) ranges.push({ name: p.name, first, count })
      for (let e = edgeOff * 2; e < (edgeOff + nEdges) * 2; e++) {
        edge.push(src.edges[e]!)
      }
    }
    triOff += p.nTriangles
    edgeOff += nEdges
  }
  // Everything unchecked: no geometry to draw, and an empty BufferGeometry
  // would still cost a draw call and a normals pass.
  if (!tri.length) return
  built.mergedRanges = ranges

  const sg = new THREE.BufferGeometry()
  sg.setAttribute('position', new THREE.Float32BufferAttribute(src.positions, 3))
  sg.setIndex(new THREE.BufferAttribute(new Uint32Array(tri), 1))
  sg.computeVertexNormals()
  const surface = new THREE.Mesh(
    sg,
    new THREE.MeshPhongMaterial({
      color: 0x7799bb,
      transparent: true,
      opacity: src.opacity,
      side: THREE.DoubleSide,
      flatShading: true,
      depthWrite: true,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
      shininess: 30,
    }),
  )
  surface.renderOrder = 100
  built.group.add(surface)

  let edges: THREE.LineSegments | null = null
  if (edge.length) {
    const eg = new THREE.BufferGeometry()
    eg.setAttribute('position', new THREE.Float32BufferAttribute(src.positions, 3))
    eg.setIndex(new THREE.BufferAttribute(new Uint32Array(edge), 1))
    edges = new THREE.LineSegments(
      eg,
      new THREE.LineBasicMaterial({ color: 0x112244, transparent: true, opacity: 0.35 }),
    )
    edges.renderOrder = 101
    built.group.add(edges)
  }
  built.mergedPair = { surface, edges }
}

/**
 * Apply visibility to MANY patches, re-merging at most once.
 *
 * `setPatchVisible` in a loop is O(N) full re-merges — on the 4336-patch case
 * this fix exists for, that is 4336 rebuilds of a three-million-triangle index
 * buffer, and 全选 stops responding. Measured: the tab hung. The legacy
 * re-merges once for the whole batch (`mpUpdateVisibility`,
 * STL_generate.html:24571).
 */
export function setPatchesVisible(
  built: BuiltPreview,
  names: Iterable<string>,
  visible: boolean,
): void {
  for (const name of names) {
    const pair = built.byPatch.get(name)
    if (!pair) continue
    pair.surface.visible = visible
    pair.edges.visible = visible
    if (built.source) {
      if (visible) built.source.visible.add(name)
      else built.source.visible.delete(name)
    }
  }
  if (built.merged) rebuildMerged(built)
  syncBaffleSides(built)
}

/** Apply a patch's visibility to both its surface and its edges. */
export function setPatchVisible(built: BuiltPreview, name: string, visible: boolean) {
  const pair = built.byPatch.get(name)
  if (!pair) return
  pair.surface.visible = visible
  pair.edges.visible = visible
  // Merged mode draws from ONE index buffer, so a `.visible` flag on an
  // off-scene object changes nothing on screen — the buffer has to be rebuilt.
  if (built.merged && built.source) {
    if (visible) built.source.visible.add(name)
    else built.source.visible.delete(name)
    rebuildMerged(built)
  }
  syncBaffleSides(built)
}

export function setMeshOpacity(built: BuiltPreview, opacity: number) {
  built.byPatch.forEach(({ surface }) => {
    const mat = surface.material as THREE.MeshPhongMaterial
    mat.opacity = opacity
    // Stays on at every opacity — see the material note in buildMeshPreview.
    mat.depthWrite = true
    mat.needsUpdate = true
  })
  // The merged surface is what is DRAWN in merged mode, and it is not in
  // `byPatch`. Remembered on the source too, so the next re-merge builds its
  // material at the opacity the slider is actually at.
  if (built.source) built.source.opacity = opacity
  const surface = built.mergedPair?.surface
  if (surface) {
    const mat = surface.material as THREE.MeshPhongMaterial
    mat.opacity = opacity
    mat.depthWrite = true
    mat.needsUpdate = true
  }
}

export function disposeMeshPreview(built: BuiltPreview | null) {
  if (!built) return
  // The per-patch objects FIRST, by name. In merged mode they are not in the
  // group, so a traversal alone would leave every one of their geometries and
  // materials on the GPU — on the cases that trigger merging, that is a
  // thousand of each.
  built.byPatch.forEach(({ surface, edges }) => {
    surface.geometry.dispose()
    ;(surface.material as THREE.Material).dispose()
    edges.geometry.dispose()
    ;(edges.material as THREE.Material).dispose()
  })
  built.group.traverse((o) => {
    const mesh = o as THREE.Mesh
    mesh.geometry?.dispose()
    const mat = mesh.material as THREE.Material | THREE.Material[] | undefined
    if (Array.isArray(mat)) mat.forEach((m) => m.dispose())
    else mat?.dispose()
  })
  built.group.parent?.remove(built.group)
  built.byPatch.clear()
  built.mergedPair = null
  built.mergedRanges = null
  built.source = null
}

export interface MeshPreviewLoadOptions {
  /** Aborts BOTH requests. The binary is the long one, and the one to cancel. */
  signal?: AbortSignal
  /** Called as each stage starts, with a line for the task dialog. */
  onStage?: (line: string) => void
}

/**
 * Fetch both payloads, reporting each step.
 *
 * The stages are the legacy's (`mpLoadMesh`, STL_generate.html:24346-24359) and
 * each is logged BEFORE the request it names — a dialog that only fills in
 * after the server answers is a dialog that is empty for the whole wait.
 */
export async function loadMeshPreview(
  opts: MeshPreviewLoadOptions = {},
): Promise<{ meta: MeshMeta; buffer: ArrayBuffer }> {
  const { signal, onStage } = opts
  onStage?.(t('pp.requesting_mesh_metadata'))
  const resp = await fetch('/api/mesh-preview/load', { method: 'POST', signal })
  const meta = (await resp.json()) as MeshMeta & { error?: string }
  if (meta.error) throw new Error(meta.error)
  onStage?.(
    t('pp.loading_mesh_binary', {
      points: meta.totalPoints ?? 0,
      triangles: meta.totalTriangles ?? 0,
    }),
  )
  const key = meta.meshSnapshotKey
  if (key !== undefined && !/^[0-9a-f]{64}$/.test(key)) throw new Error('Invalid mesh preview snapshot key')
  const bin = await fetch(key
    ? `/api/mesh-preview/binary?meshSnapshotKey=${key}`
    : '/api/mesh-preview/binary', { signal })
  if (!bin.ok) throw new Error(`/api/mesh-preview/binary -> ${bin.status}`)
  if (key && bin.headers.get('X-Mesh-Snapshot-Key') !== key) {
    throw new Error('Mesh preview binary snapshot differs from metadata')
  }
  const buffer = await bin.arrayBuffer()
  onStage?.(t('pp.parsing_mesh_data'))
  return { meta, buffer }
}

/**
 * What was loaded, in one line: the legacy's post-load readout.
 *
 * `N patches, N cells, N boundary faces, N points, N triangles`
 * (STL_generate.html:24471-24479). The cell count is why it exists — it is the
 * figure `maxGlobalCells` is set against, so it is how you find out that the
 * mesh you just waited for is ten times the size you asked for. It goes both to
 * the dialog and to the operation log, because the dialog closes.
 */
export function meshPreviewSummary(meta: MeshMeta): string {
  const cells = meta.nCells != null ? meta.nCells.toLocaleString('en-US') : '?'
  const faces =
    meta.nBoundaryFaces != null
      ? meta.nBoundaryFaces.toLocaleString('en-US')
      : meta.nBoundarySum != null
        ? `${meta.nBoundarySum.toLocaleString('en-US')} (from boundary)`
        : '?'
  return (
    `${meta.patches?.length ?? 0} patches, ${cells} cells, ${faces} boundary faces, ` +
    `${meta.totalPoints ?? 0} points, ${meta.totalTriangles ?? 0} triangles`
  )
}
