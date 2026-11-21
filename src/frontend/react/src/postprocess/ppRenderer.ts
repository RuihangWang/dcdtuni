/**
 * Drawing post-processing results into the viewport.
 *
 * Takes the decoded payloads from `postprocess.ts` and builds Three.js objects:
 * the coloured boundary surface, a slice plane, streamline tubes, and the seed
 * sphere that produced them. Kept apart from the panel so the decoding, the
 * colouring and the geometry can each be tested without the other two.
 *
 * ## The colour is computed in the shader, not here
 *
 * Every layer carries the RAW scalar in a `fieldValue` attribute and samples a
 * 256-entry LUT texture per fragment — see `ppMaterials`. Nothing here converts
 * a value to RGB, which is what keeps the picture and the colorbar on the same
 * scale, and what stops a triangle spanning two colours from blending through a
 * third that is in neither.
 *
 * ## One geometry per layer, not per patch
 *
 * The boundary mesh is 53 patches / 164568 points, and an object per patch is
 * 53 draw calls plus 53 materials for something the user reads as one surface.
 * It is built as ONE BufferGeometry, and hiding a patch drops its triangles
 * from the index buffer rather than hiding an object. The legacy editor makes
 * the opposite choice — a mesh per patch — so the two agree on what is drawn,
 * not on how many objects draw it.
 *
 * ## Render order matches the legacy layering
 *
 * surface 100, streamline tubes 110, slice 120, all above the model geometry at
 * 0. Transparent layers do not sort reliably by depth alone, and getting this
 * wrong shows the slice through the racks it is meant to be behind.
 */

import * as THREE from 'three'
import { registerHelper, unregisterHelper } from '../geometry/helperRegistry'
import { createColormapTexture, type ColorMap } from './ppColormaps'
import { createFieldMaterial, createScalarFlatMaterial } from './ppMaterials'
import type { DecodedMesh, SlicePayload, StreamlineSet } from './index'

export interface SurfaceOptions {
  values?: Float32Array
  climMin?: number
  climMax?: number
  autoScale?: boolean
  colorMap: ColorMap
  /** Patch names to hide. Everything else is drawn. */
  hidden?: Set<string>
}

export interface BuiltSurface {
  object: THREE.Mesh
  /** The range actually used, after auto-scaling. */
  range: [number, number]
  dispose(): void
}

/** The actual [min, max] of a field, ignoring NaNs. */
export function fieldRange(values: Float32Array): [number, number] {
  let min = Infinity
  let max = -Infinity
  for (let i = 0; i < values.length; i++) {
    const v = values[i] as number
    if (!Number.isFinite(v)) continue
    if (v < min) min = v
    if (v > max) max = v
  }
  return Number.isFinite(min) ? [min, max] : [0, 0]
}

/**
 * The boundary surface, coloured by a field.
 *
 * `values` is per POINT and must be EXACTLY as long as the mesh's point count.
 * The check is not defensive padding — it is the only thing standing between a
 * misaligned field and a picture that renders, has structure, and is wrong at
 * every vertex. It has already earned its keep once: a decoder that read the
 * field body's 16-byte binary header as four data values shifted every value by
 * four positions, and this is what caught it.
 */
export function buildSurface(mesh: DecodedMesh, opts: SurfaceOptions): BuiltSurface {
  const geom = new THREE.BufferGeometry()
  geom.setAttribute('position', new THREE.BufferAttribute(mesh.positions, 3))

  let range: [number, number] = [opts.climMin ?? 0, opts.climMax ?? 0]
  if (opts.values) {
    if (opts.values.length !== mesh.totals.points) {
      throw new Error(
        `field has ${opts.values.length} values for ${mesh.totals.points} mesh points — ` +
          'the two do not describe the same geometry, and colouring would land on the ' +
          'wrong vertices',
      )
    }
    if (opts.autoScale || range[0] === range[1]) range = fieldRange(opts.values)
    geom.setAttribute('fieldValue', new THREE.BufferAttribute(opts.values, 1))
  }

  const indices = opts.hidden?.size ? filterIndices(mesh, opts.hidden) : mesh.indices
  geom.setIndex(new THREE.BufferAttribute(indices, 1))
  geom.computeVertexNormals()

  const colormap = createColormapTexture(opts.colorMap)
  const material = createFieldMaterial(colormap, range[0], range[1])
  const object = new THREE.Mesh(geom, material)
  object.renderOrder = 100
  object.userData.ppLayer = 'surface'

  return {
    object,
    range,
    dispose() {
      geom.dispose()
      material.dispose()
      colormap.dispose()
    },
  }
}

/** Drop the triangles of hidden patches, keeping the shared point array. */
export function filterIndices(mesh: DecodedMesh, hidden: Set<string>): Uint32Array {
  const keep: number[] = []
  let tri = 0
  for (const p of mesh.patches) {
    const end = tri + p.nTriangles
    if (!hidden.has(p.name)) {
      for (let i = tri * 3; i < end * 3; i++) keep.push(mesh.indices[i] as number)
    }
    tri = end
  }
  return new Uint32Array(keep)
}

export interface BuiltSlice {
  object: THREE.Mesh
  range: [number, number]
  dispose(): void
}

/**
 * The cut plane.
 *
 * Unlit and with `depthWrite` off. A slice sits INSIDE the room, co-planar with
 * nothing, and writing depth makes it fight the boundary surface it is cutting
 * through — the plane flickers as the camera moves.
 */
export function buildSlice(
  slice: SlicePayload,
  colorMap: ColorMap,
  clim?: [number, number],
): BuiltSlice {
  const geom = new THREE.BufferGeometry()
  geom.setAttribute('position', new THREE.BufferAttribute(slice.positions, 3))
  geom.setIndex(new THREE.BufferAttribute(slice.indices, 1))

  const range: [number, number] = clim && clim[0] !== clim[1] ? clim : slice.scalarRange
  if (slice.values) {
    geom.setAttribute('fieldValue', new THREE.BufferAttribute(slice.values, 1))
  }

  const colormap = createColormapTexture(colorMap)
  // Opaque, matching the legacy slice: a cut is read for its values, and
  // blending the room behind it into those colours misreads the legend.
  const material = createScalarFlatMaterial(colormap, range[0], range[1], 1.0)
  const object = new THREE.Mesh(geom, material)
  object.renderOrder = 120
  object.userData.ppLayer = 'slice'
  return {
    object,
    range,
    dispose() {
      geom.dispose()
      material.dispose()
      colormap.dispose()
    },
  }
}

export interface BuiltStreamlines {
  object: THREE.Group
  range: [number, number]
  dispose(): void
}

/**
 * Streamlines as tubes.
 *
 * Tubes, not lines, because a line has no thickness in 3D: at any distance it
 * is a one-pixel scratch that gives no depth cue, and the tube radius is a
 * control the panel exposes. Each polyline is resampled onto a Catmull-Rom
 * curve — the integrator's steps are uneven, and lofting a tube straight onto
 * them kinks at every step.
 *
 * A polyline shorter than two points is skipped: a curve needs a direction, and
 * TubeGeometry on a single point produces NaN vertices that silently poison the
 * whole group's bounding sphere.
 */
export function buildStreamlines(
  set: StreamlineSet,
  colorMap: ColorMap,
  clim?: [number, number],
  tubeRadius = 0.02,
): BuiltStreamlines {
  const range: [number, number] = clim && clim[0] !== clim[1] ? clim : set.scalarRange
  const group = new THREE.Group()
  group.userData.ppLayer = 'streamlines'
  const disposables: { dispose(): void }[] = []
  const colormap = createColormapTexture(colorMap)
  disposables.push(colormap)

  for (const line of set.lines) {
    if (line.count < 2) continue
    const points: THREE.Vector3[] = []
    const scalars: number[] = []
    for (let i = 0; i < line.count; i++) {
      const p = line.start + i
      points.push(
        new THREE.Vector3(
          set.points[p * 3] as number,
          set.points[p * 3 + 1] as number,
          set.points[p * 3 + 2] as number,
        ),
      )
      scalars.push(set.scalars[p] as number)
    }

    const curve = new THREE.CatmullRomCurve3(points)
    // Capped at 500: a long streamline can carry thousands of points, and one
    // tube segment each turns a single line into a six-figure triangle count.
    const segments = Math.min(points.length * 2, 500)
    const geom = new THREE.TubeGeometry(curve, segments, tubeRadius, 6, false)

    // The tube's vertices do not correspond one-to-one with the polyline's
    // points, so the scalar is resampled by position ALONG the tube.
    const count = geom.attributes.position?.count ?? 0
    const values = new Float32Array(count)
    for (let vi = 0; vi < count; vi++) {
      const frac = vi / count
      const si = Math.floor(frac * (scalars.length - 1))
      values[vi] = scalars[si] as number
    }
    geom.setAttribute('fieldValue', new THREE.BufferAttribute(values, 1))

    const material = createFieldMaterial(colormap, range[0], range[1])
    const tube = new THREE.Mesh(geom, material)
    tube.renderOrder = 110
    group.add(tube)
    disposables.push(geom, material)
  }

  return {
    object: group,
    range,
    dispose() {
      for (const d of disposables) d.dispose()
    },
  }
}

export interface BuiltSeed {
  object: THREE.Mesh
  dispose(): void
}

/**
 * The seed sphere the streamlines came from.
 *
 * A translucent blue ball with `depthTest` OFF and renderOrder 999, so it stays
 * visible through the racks it is usually inside — it is a control, and a
 * control you cannot see because geometry is in front of it is not one.
 *
 * Built at unit radius and SCALED, which is what the legacy editor does: the
 * translate gizmo attaches to this object, and a gizmo on a re-created geometry
 * loses its grab every time the radius changes.
 */
export function buildSeedSphere(centre: [number, number, number], radius: number): BuiltSeed {
  const geom = new THREE.SphereGeometry(1, 24, 16)
  const material = new THREE.MeshBasicMaterial({
    color: 0x4488ff,
    transparent: true,
    opacity: 0.3,
    side: THREE.DoubleSide,
    depthTest: false,
    depthWrite: false,
  })
  const object = new THREE.Mesh(geom, material)
  object.position.set(centre[0], centre[1], centre[2])
  const r = Math.max(radius, 0.01)
  object.scale.set(r, r, r)
  object.renderOrder = 999
  object.userData.ppLayer = 'seed'
  return {
    object,
    dispose() {
      geom.dispose()
      material.dispose()
    },
  }
}

/**
 * The ring that previews where a slice plane sits.
 *
 * A torus, not a flat annulus: seen edge-on a flat ring disappears, and a plane
 * preview is looked at edge-on precisely when the user is checking its height.
 * Drawn from the plane's NORMAL rather than from a cut result, so it appears
 * before the slice is computed — which is the point: position the plane, then
 * ask for the cut.
 */
export function buildSliceRing(
  origin: [number, number, number],
  normal: [number, number, number],
  radius: number,
): BuiltSeed {
  // 2% of the ring radius, the legacy proportion: thick enough to read at a
  // distance, thin enough not to be mistaken for a slab of geometry.
  const geom = new THREE.TorusGeometry(radius, radius * 0.02, 12, 64)
  const material = new THREE.MeshBasicMaterial({
    color: SLICE_RING_COLOR,
    transparent: true,
    opacity: 0.8,
    depthTest: false,
    depthWrite: false,
  })
  const object = new THREE.Mesh(geom, material)
  object.position.set(origin[0], origin[1], origin[2])
  object.renderOrder = 998
  // TorusGeometry lies in XY; aim its +Z at the plane normal.
  const n = new THREE.Vector3(normal[0], normal[1], normal[2])
  if (n.lengthSq() > 0) {
    object.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), n.normalize())
  }
  object.userData.ppLayer = 'sliceRing'
  return {
    object,
    dispose() {
      geom.dispose()
      material.dispose()
    },
  }
}

/**
 * The bounding box of a decoded mesh.
 *
 * The slice preview ring is sized from the diagonal and the default cut plane
 * sits at the centre, both of which have to come from the RESULT rather than
 * from the project: the two agree on a normal room and diverge the moment a
 * case has geometry outside its own walls.
 */
export function meshBounds(mesh: DecodedMesh): {
  min: [number, number, number]
  max: [number, number, number]
  center: [number, number, number]
  diag: number
} {
  const p = mesh.positions
  let x0 = Infinity, y0 = Infinity, z0 = Infinity
  let x1 = -Infinity, y1 = -Infinity, z1 = -Infinity
  for (let i = 0; i < p.length; i += 3) {
    const x = p[i] as number
    const y = p[i + 1] as number
    const z = p[i + 2] as number
    if (x < x0) x0 = x
    if (x > x1) x1 = x
    if (y < y0) y0 = y
    if (y > y1) y1 = y
    if (z < z0) z0 = z
    if (z > z1) z1 = z
  }
  if (!Number.isFinite(x0)) {
    return { min: [0, 0, 0], max: [0, 0, 0], center: [0, 0, 0], diag: 5 }
  }
  return {
    min: [x0, y0, z0],
    max: [x1, y1, z1],
    center: [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2],
    diag: Math.hypot(x1 - x0, y1 - y0, z1 - z0),
  }
}


/** The ring's resting colour, and the colour it takes under the cursor. */
export const SLICE_RING_COLOR = 0xff4444
export const SLICE_RING_HOVER_COLOR = 0xffaa00

/** The inference box: resting, hovered, and being dragged (`:20357-20359`). */
export const AI_BOX_COLOR = 0xff6699
export const AI_BOX_HOVER_COLOR = 0xffd700
export const AI_BOX_DRAG_COLOR = 0xff8c00

export interface BuiltAiBox {
  object: THREE.Group
  dispose(): void
}

/** Which part of the box the cursor is over, for the hover recolour. */
export type AiBoxHover = 'none' | 'centre' | 'face'

/**
 * The AI inference region: a wireframe box with grab handles.
 *
 * Drawn from EDGES rather than as a translucent solid: the box sits around the
 * geometry it is selecting, and a solid one would tint everything inside it —
 * exactly the volume the user is trying to judge. Depth-tested off for the same
 * reason the other handles are: it is a control, and it is usually inside
 * something.
 *
 * Three kinds of child, matching `_ppAiEnsureVisuals` (STL_generate.html:20408):
 * the wireframe, six INVISIBLE face planes, and a centre sphere. The planes are
 * what makes a face grabbable — a wireframe is twelve one-pixel lines and
 * nothing a raycast can reliably hit — and they are invisible on purpose: a
 * visible one would shade the box's interior, which is the volume being judged.
 *
 * Built at UNIT size and scaled, like the legacy visuals. A face drag changes
 * the box's size sixty times a second, and rebuilding the geometry each time
 * would allocate and free a buffer per frame for something a transform already
 * expresses.
 */
export function buildAiBox(
  min: [number, number, number],
  max: [number, number, number],
): BuiltAiBox {
  const group = new THREE.Group()
  group.userData.ppLayer = 'aiBox'
  const disposables: { dispose(): void }[] = []

  const box = new THREE.BoxGeometry(1, 1, 1)
  const edges = new THREE.EdgesGeometry(box)
  box.dispose()
  const wireMaterial = new THREE.LineBasicMaterial({
    color: AI_BOX_COLOR,
    transparent: true,
    opacity: 0.85,
    depthTest: false,
  })
  const wireframe = new THREE.LineSegments(edges, wireMaterial)
  wireframe.renderOrder = 997
  wireframe.userData.ppAiPart = 'wireframe'
  group.add(wireframe)
  disposables.push(edges, wireMaterial)

  for (let i = 0; i < 6; i++) {
    const geom = new THREE.PlaneGeometry(1, 1)
    const material = new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide })
    const plane = new THREE.Mesh(geom, material)
    plane.userData.ppAiFace = i
    group.add(plane)
    disposables.push(geom, material)
  }

  // WP-S3: UNIT sphere at the legacy 60 mm centre radius, registered with the
  // shared per-frame helper scaling (scale write = initial condition /
  // registry-absent fallback). `updateAiBoxVisuals` only repositions and
  // recolours this child, so nothing here fights the per-frame sync.
  const sphereGeom = new THREE.SphereGeometry(1, 12, 8)
  const sphereMaterial = new THREE.MeshBasicMaterial({
    color: AI_BOX_COLOR,
    transparent: true,
    opacity: 0.75,
    depthTest: false,
  })
  const sphere = new THREE.Mesh(sphereGeom, sphereMaterial)
  sphere.scale.setScalar(0.06)
  registerHelper(sphere, 0.06)
  sphere.renderOrder = 998
  sphere.userData.ppAiPart = 'centre'
  group.add(sphere)
  disposables.push(sphereGeom, sphereMaterial)

  updateAiBoxVisuals(group, min, max)
  return {
    object: group,
    dispose() {
      unregisterHelper(sphere)
      for (const d of disposables) d.dispose()
    },
  }
}

/**
 * Put a box's bounds and hover state onto the objects built above.
 *
 * Called on every frame of a drag, which is why it takes the group rather than
 * rebuilding it: a React state round trip per frame would rebuild every
 * post-processing layer, including a 164568-point boundary surface, to move six
 * numbers.
 */
export function updateAiBoxVisuals(
  group: THREE.Object3D,
  min: [number, number, number],
  max: [number, number, number],
  state: { hover?: AiBoxHover; dragging?: boolean } = {},
): void {
  const centre: [number, number, number] = [
    (min[0] + max[0]) / 2,
    (min[1] + max[1]) / 2,
    (min[2] + max[2]) / 2,
  ]
  const size: [number, number, number] = [
    Math.max(max[0] - min[0], 0.001),
    Math.max(max[1] - min[1], 0.001),
    Math.max(max[2] - min[2], 0.001),
  ]
  const hover = state.hover ?? 'none'
  const wireColor = state.dragging
    ? AI_BOX_DRAG_COLOR
    : hover !== 'none'
      ? AI_BOX_HOVER_COLOR
      : AI_BOX_COLOR
  const sphereColor = state.dragging
    ? AI_BOX_DRAG_COLOR
    : hover === 'centre'
      ? AI_BOX_HOVER_COLOR
      : AI_BOX_COLOR

  // PlaneGeometry lies in XY, so its local width/height map onto the two axes
  // that are NOT the face normal — which is why the X faces take (sz, sy) and
  // not (sy, sz). Getting the pair the wrong way round leaves a tall thin
  // pick target on a wide face, and the grab misses everywhere else on it.
  const faces: { centre: [number, number, number]; normal: [number, number, number]; w: number; h: number }[] = [
    { centre: [min[0], centre[1], centre[2]], normal: [1, 0, 0], w: size[2], h: size[1] },
    { centre: [max[0], centre[1], centre[2]], normal: [1, 0, 0], w: size[2], h: size[1] },
    { centre: [centre[0], min[1], centre[2]], normal: [0, 1, 0], w: size[0], h: size[2] },
    { centre: [centre[0], max[1], centre[2]], normal: [0, 1, 0], w: size[0], h: size[2] },
    { centre: [centre[0], centre[1], min[2]], normal: [0, 0, 1], w: size[0], h: size[1] },
    { centre: [centre[0], centre[1], max[2]], normal: [0, 0, 1], w: size[0], h: size[1] },
  ]

  for (const child of group.children) {
    const data = child.userData as { ppAiPart?: string; ppAiFace?: number }
    if (data.ppAiPart === 'wireframe') {
      child.position.set(centre[0], centre[1], centre[2])
      child.scale.set(size[0], size[1], size[2])
      const material = (child as THREE.LineSegments).material as THREE.LineBasicMaterial
      material.color.setHex(wireColor)
    } else if (data.ppAiPart === 'centre') {
      child.position.set(centre[0], centre[1], centre[2])
      const material = (child as THREE.Mesh).material as THREE.MeshBasicMaterial
      material.color.setHex(sphereColor)
    } else if (typeof data.ppAiFace === 'number') {
      const face = faces[data.ppAiFace]
      if (!face) continue
      child.position.set(face.centre[0], face.centre[1], face.centre[2])
      child.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 0, 1),
        new THREE.Vector3(face.normal[0], face.normal[1], face.normal[2]),
      )
      child.scale.set(Math.max(face.w, 0.01), Math.max(face.h, 0.01), 1)
    }
  }
}
