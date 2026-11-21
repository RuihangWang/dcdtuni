/**
 * Procedural, generic liquid-equipment detail for the React liquid editor.
 *
 * These meshes illustrate serviceable equipment without claiming a particular
 * vendor/model. They are children of the already transformed structural root,
 * never enter renderMeshes, and therefore cannot become STL/CFD solids.
 * Visible CDU service parts select and highlight their existing model; they
 * never create a new selection identity. The structural model remains the
 * geometry authority.
 */

import * as THREE from 'three'
import type { Construction } from '../schema/cfdproj'
import { geometryPure, pbrMaterials } from '../legacy/geometryBuilder'
import { createCduDisplayTexture } from './equipmentDisplayTexture'

const STEEL = 0x879097
const CABINET = 0x758896
const PANEL = 0x8497a5
const DARK = 0x202831
const SUPPLY = 0x2d78a7
const RETURN = 0xa54b45

function clamp(value: number, low: number, high: number) {
  return Math.min(high, Math.max(low, value))
}

/**
 * The surface each detail colour stands for. Every part keeps its own colour,
 * metalness and roughness; the role adds the matching texture set
 * (shared/js/pbr_materials.js), whose tintable colour maps are neutral so the
 * colour still reads — and a liquid result colour still replaces it.
 */
type SurfaceRole = 'stainless' | 'painted-steel' | 'rubber' | null

function roleFor(color: number): SurfaceRole {
  if (color === STEEL) return 'stainless'
  if (color === CABINET || color === PANEL || color === DARK || color === SUPPLY || color === RETURN) {
    return 'painted-steel'
  }
  return null // Display electronics use their own plain material
}

function material(color: number, metalness = 0.35, roughness = 0.55, role: SurfaceRole = roleFor(color), normalScale?: number) {
  // One fresh material per mesh, as before; only the textures are shared.
  const mat = role
    ? pbrMaterials().material(role, { color, metalness, roughness })
    : new THREE.MeshStandardMaterial({ color, metalness, roughness })
  if (normalScale !== undefined) {
    mat.normalScale.setScalar(normalScale)
    // The shared loader reads this scalar when its cached maps land later.
    if (role) mat.userData.pbrNormalScale = normalScale
  }
  return mat
}

const detailOwnedMaterials = new WeakMap<THREE.Mesh, THREE.Material[]>()

function detailMesh(
  geometry: THREE.BufferGeometry,
  mat: THREE.Material,
  name: string,
  owner?: string,
): THREE.Mesh {
  const mesh = new THREE.Mesh(geometry, mat)
  mesh.name = name
  mesh.userData = {
    liquidVisualDetail: true,
    exportExcluded: true,
    selectable: Boolean(owner),
    ...(owner ? { modelId: owner } : {}),
  }
  if (!owner) mesh.raycast = () => {}
  const originalMaterials = [mat]
  detailOwnedMaterials.set(mesh, originalMaterials)
  // Detail materials are private to this mesh, while PBR textures are cached.
  // Painting and opacity previews replace material with a clone. The shared
  // scene disposer only sees that current clone, so release retained private
  // materials when the owning geometry leaves the scene too.
  let released = false
  geometry.addEventListener('dispose', () => {
    if (released) return
    released = true
    const list = (value: THREE.Material | THREE.Material[]) => Array.isArray(value) ? value : [value]
    const current = new Set(list(mesh.material))
    const retained = mesh.userData._origMaterial as THREE.Material | THREE.Material[] | undefined
    const owned = new Set([...originalMaterials, ...(retained ? list(retained) : [])])
    for (const material of owned) {
      if (!current.has(material) && !(material as THREE.Material & { _isCached?: boolean })._isCached) {
        material.dispose()
      }
    }
  })
  return mesh
}

function box(
  group: THREE.Group,
  name: string,
  size: THREE.Vector3,
  position: THREE.Vector3,
  color: number,
  metalness = 0.35,
  roughness = 0.55,
  bevel = 0,
  roleOverride?: SurfaceRole,
  normalScale?: number,
) {
  const geometry = bevel > 0 ? bevelledBox(size, bevel) : new THREE.BoxGeometry(size.x, size.y, size.z)
  const role = roleOverride === undefined ? roleFor(color) : roleOverride
  if (role) {
    if (bevel > 0) bevelUv(geometry, role)
    else pbrMaterials().boxUv(geometry, role)
  }
  const mesh = detailMesh(geometry, material(color, metalness, roughness, role, normalScale), name, group.userData.modelId)
  mesh.position.copy(position)
  group.add(mesh)
  return mesh
}

/** BoxGeometry's UV helper relies on its fixed six-face vertex layout. The
 * bevel has a different topology; project in physical metres per face instead.
 */
function bevelUv(geometry: THREE.BufferGeometry, role: SurfaceRole) {
  if (!role) return
  const tile = pbrMaterials().ROLES[role]!.tileM
  const positions = geometry.getAttribute('position')
  const normals = geometry.getAttribute('normal')
  const uv = geometry.getAttribute('uv')
  for (let i = 0; i < positions.count; i += 1) {
    const nx = Math.abs(normals.getX(i))
    const ny = Math.abs(normals.getY(i))
    const nz = Math.abs(normals.getZ(i))
    if (nx >= ny && nx >= nz) uv.setXY(i, positions.getZ(i) / tile, positions.getY(i) / tile)
    else if (ny >= nz) uv.setXY(i, positions.getX(i) / tile, positions.getZ(i) / tile)
    else uv.setXY(i, positions.getX(i) / tile, positions.getY(i) / tile)
  }
  uv.needsUpdate = true
}

function cylinderToEndpoint(
  group: THREE.Group,
  name: string,
  endpoint: THREE.Vector3,
  outward: THREE.Vector3,
  radius: number,
  length: number,
  color: number,
  radialSegments = 16,
) {
  const axis = outward.clone().normalize()
  const geometry = new THREE.CylinderGeometry(radius, radius, length, radialSegments)
  const role = roleFor(color)
  if (role) pbrMaterials().cylinderUv(geometry, radius, length, role)
  const mesh = detailMesh(
    geometry,
    material(color, color === STEEL ? 0.72 : 0.28, color === STEEL ? 0.3 : 0.45, role),
    name,
    group.userData.modelId,
  )
  // Cylinder +Y ends at the logical endpoint; all visual neck lies inward.
  mesh.position.copy(endpoint).addScaledVector(axis, -length / 2)
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis)
  group.add(mesh)
  return mesh
}

function addEndpointCoupler(
  group: THREE.Group,
  name: string,
  endpoint: THREE.Vector3,
  outward: THREE.Vector3,
  radius: number,
  marker: number,
) {
  const length = clamp(radius * 3.2, 0.026, 0.06)
  cylinderToEndpoint(group, `${name}-stub`, endpoint, outward, radius, length, STEEL)
  cylinderToEndpoint(
    group,
    `${name}-marker`,
    endpoint,
    outward,
    radius * 1.3,
    clamp(length * 0.28, 0.008, 0.014),
    marker,
  )
}

/**
 * A folded-sheet housing with a small rolled edge. ExtrudeGeometry's bevel
 * grows beyond the input contour, so inset the contour/depth first: the
 * resulting bounds are exactly the requested box, including the bevel.
 */
function bevelledBox(size: THREE.Vector3, radius: number) {
  const b = Math.min(radius, size.x / 4, size.y / 4, size.z / 4)
  const x = size.x / 2 - b
  const y = size.y / 2 - b
  const outline = new THREE.Shape()
  outline.moveTo(-x, -y)
  outline.lineTo(x, -y)
  outline.lineTo(x, y)
  outline.lineTo(-x, y)
  outline.closePath()
  const geometry = new THREE.ExtrudeGeometry(outline, {
    depth: size.z - 2 * b, steps: 1,
    bevelEnabled: true, bevelSize: b, bevelThickness: b, bevelSegments: 2,
  })
  geometry.translate(0, 0, -size.z / 2 + b)
  return geometry
}

type CduFace = 'right' | 'left' | 'top' | 'bottom' | 'back' | 'front'
const CDU_FACES: CduFace[] = ['right', 'left', 'top', 'bottom', 'back', 'front']
function normalFaceIndex(x: number, y: number, z: number) {
  const ax = Math.abs(x), ay = Math.abs(y), az = Math.abs(z)
  if (ax >= ay && ax >= az) return x >= 0 ? 0 : 1
  if (ay >= az) return y >= 0 ? 2 : 3
  return z >= 0 ? 4 : 5
}

/** Display opacity follows its source model/face; it never rewrites the source. */
function applyCduDisplayOpacity(model: Construction, group: THREE.Group) {
  const finiteOpacity = (value: unknown, fallback: number) =>
    typeof value === 'number' && Number.isFinite(value) ? clamp(value, 0, 1) : fallback
  const modelOpacity = finiteOpacity(model.meta?.display?.opacity, 1)
  const faces = model.geometry?.faces as Partial<Record<CduFace, { opacity?: unknown }>> | undefined
  const values = CDU_FACES.map(face => Math.min(modelOpacity,
    finiteOpacity(faces?.[face]?.opacity, modelOpacity)))
  const setOpacity = (mat: THREE.Material, value: number) => {
    mat.opacity = value
    mat.transparent = value < 0.999
    mat.depthWrite = value >= 0.999
  }
  group.traverse(object => {
    const mesh = object as THREE.Mesh
    if (!mesh.isMesh || !mesh.userData.liquidVisualDetail) return
    const original = mesh.material as THREE.Material
    const shell = mesh.name === 'generic-cdu-cabinet' || mesh.name === 'generic-cdu-plinth'
    if (shell && values.some(value => value !== values[0])) {
      // Bevelled boxes have variable topology. Preserve vertices/index order and
      // assign each triangle to its dominant face, as the existing UV helper does.
      const materials = values.map(value => {
        // Register the actual visible material for late PBR maps as well.
        // A plain clone would be absent from the shared loader's pending list.
        const role = original.userData.pbrRole as Exclude<SurfaceRole, null> | undefined
        const mat = role ? pbrMaterials().material(role, {}).copy(original) : original.clone()
        setOpacity(mat, value)
        return mat
      })
      detailOwnedMaterials.get(mesh)!.push(...materials)
      mesh.material = materials
      const geometry = mesh.geometry, normals = geometry.getAttribute('normal'), indices = geometry.index
      const count = indices?.count ?? geometry.getAttribute('position').count
      geometry.clearGroups()
      let previous = -1
      for (let offset = 0; offset + 2 < count; offset += 3) {
        let nx = 0, ny = 0, nz = 0
        for (let j = 0; j < 3; j += 1) {
          const vertex = indices ? indices.getX(offset + j) : offset + j
          nx += normals.getX(vertex); ny += normals.getY(vertex); nz += normals.getZ(vertex)
        }
        const face = normalFaceIndex(nx, ny, nz)
        if (face === previous) geometry.groups[geometry.groups.length - 1]!.count += 3
        else geometry.addGroup(offset, 3, face)
        previous = face
      }
      return
    }
    const mountedFace = mesh.name.includes('-foot-') ? 3
      : mesh.name.includes('-supply-') || mesh.name.includes('-return-')
        ? (() => {
          const outward = new THREE.Vector3(0, 1, 0).applyQuaternion(mesh.quaternion)
          return normalFaceIndex(outward.x, outward.y, outward.z)
        })() : 5
    setOpacity(original, values[shell ? 0 : mountedFace]!)
  })
}

function buildCdu(model: Construction, group: THREE.Group) {
  const size = model.geometry?.size ?? { x: 0, y: 0, z: 0 }
  const w = Math.max(Number(size.x) || 0, 0.08)
  const h = Math.max(Number(size.y) || 0, 0.12)
  const d = Math.max(Number(size.z) || 0, 0.08)
  const skin = clamp(Math.min(w, h, d) * 0.006, 0.0008, 0.004)
  const foot = Math.min(h * 0.03, clamp(Math.min(w, d) * 0.035, 0.004, 0.032))
  const plinthH = Math.min(h * 0.055, 0.09)
  const bodyBottom = foot + plinthH
  const bodyH = h - bodyBottom

  // The rear panel stays on the exact logical connection plane. The thin
  // service/front skin covers the structural face without moving a port or
  // entering the STL registry.
  box(group, 'generic-cdu-cabinet',
    new THREE.Vector3(w + skin, bodyH, d + skin),
    new THREE.Vector3(w / 2, bodyBottom + bodyH / 2, (d - skin) / 2),
    CABINET, 0.08, 0.64, skin * 0.7, undefined, 0.16)
  box(group, 'generic-cdu-plinth',
    new THREE.Vector3(w * 0.94, plinthH, d * 0.94),
    new THREE.Vector3(w / 2, foot + plinthH / 2, d / 2),
    DARK, 0.22, 0.62, skin * 0.45)

  const doorW = w * 0.84
  const doorH = bodyH * 0.71
  const doorY = bodyBottom + bodyH * 0.39
  box(group, 'generic-cdu-door-gasket',
    new THREE.Vector3(doorW + skin * 2, doorH + skin * 2, skin * 1.2),
    new THREE.Vector3(w / 2, doorY, -skin * 1.1),
    DARK, 0.0, 0.88, skin * 0.3, 'rubber')
  box(group, 'generic-cdu-service-door',
    new THREE.Vector3(doorW, doorH, skin * 1.6),
    new THREE.Vector3(w / 2, doorY, -skin * 1.8),
    PANEL, 0.06, 0.66, skin * 0.45, undefined, 0.16)

  // A recessed grille and individual folded louvers read as fabricated metal,
  // rather than dark texture rectangles. All parts remain within the body
  // envelope in X/Y; the front service furniture is deliberately proud of it.
  const ventW = w * 0.65
  const ventH = bodyH * 0.15
  const ventY = bodyBottom + bodyH * 0.16
  box(group, 'generic-cdu-grille-recess',
    new THREE.Vector3(ventW, ventH, skin),
    new THREE.Vector3(w / 2, ventY, -skin * 2.7),
    DARK, 0.08, 0.86)
  for (let i = 0; i < 9; i += 1) {
    const louver = box(group, 'generic-cdu-grille-louver-' + i,
      new THREE.Vector3(ventW * 0.97, ventH * 0.045, skin * 1.2),
      new THREE.Vector3(w / 2, ventY + ventH * (i - 4) / 10, -skin * 3.4),
      PANEL, 0.32, 0.52)
    louver.rotation.x = -Math.PI / 10
  }

  const hmiW = Math.min(w * 0.3, 0.2)
  const hmiH = Math.min(bodyH * 0.085, 0.12)
  const hmiY = bodyBottom + bodyH * 0.88
  box(group, 'generic-cdu-hmi-bezel',
    new THREE.Vector3(hmiW, hmiH, skin * 2.4),
    new THREE.Vector3(w * 0.33, hmiY, -skin * 1.7),
    DARK, 0.15, 0.44, skin * 0.7)
  // This is a static equipment label, independent of the room lighting and
  // display exposure. MeshBasic + map is also an explicit supported OV snapshot
  // material; an emissiveMap would currently be rejected by that adapter.
  const texture = createCduDisplayTexture()
  const displayMaterial = new THREE.MeshBasicMaterial({
    color: texture ? 0xffffff : 0x123138, map: texture, toneMapped: false,
  })
  const screen = detailMesh(
    new THREE.BoxGeometry(hmiW * 0.79, hmiH * 0.67, skin * 0.4),
    displayMaterial, 'generic-cdu-hmi-screen', group.userData.modelId,
  )
  screen.position.set(w * 0.33, hmiY, -skin * 3.1)
  group.add(screen)
  // Geometry owns the texture even while paint/opacity has removed its map
  // from the current material. Disposal is idempotent with the material walk.
  screen.geometry.addEventListener('dispose', () => texture?.dispose())

  const handleH = bodyH * 0.15
  box(group, 'generic-cdu-handle-recess',
    new THREE.Vector3(w * 0.048, handleH + skin * 3, skin),
    new THREE.Vector3(w * 0.86, doorY + doorH * 0.08, -skin * 2.8),
    DARK, 0.08, 0.78, skin * 0.25)
  box(group, 'generic-cdu-door-handle',
    new THREE.Vector3(w * 0.02, handleH, skin * 2.5),
    new THREE.Vector3(w * 0.86, doorY + doorH * 0.08, -skin * 4),
    STEEL, 0.8, 0.3, skin * 0.4)
  for (const y of [doorY - doorH * 0.3, doorY + doorH * 0.3]) {
    box(group, 'generic-cdu-door-hinge-' + y,
      new THREE.Vector3(w * 0.025, bodyH * 0.032, skin * 2.2),
      new THREE.Vector3(w * 0.075, y, -skin * 2),
      STEEL, 0.76, 0.38, skin * 0.4)
  }
  for (const x of [w * 0.12, w * 0.88]) {
    for (const z of [d * 0.12, d * 0.88]) {
      box(group, 'generic-cdu-foot-' + x + '-' + z,
        new THREE.Vector3(foot, foot, foot),
        new THREE.Vector3(x, foot / 2, z),
        DARK, 0.0, 0.9, skin * 0.4, 'rubber')
    }
  }

  const pure = geometryPure()
  for (const [port, marker] of [['supply', SUPPLY], ['return', RETURN]] as const) {
    const frame = pure.liquidEquipmentPortFrame(model, port, 'top')
    if (!frame) continue
    // Detail necks end inward on the unchanged rear plane. No decorative
    // through-cabinet pipe is introduced; external authored pipes are separate.
    const ports = (model.cooling as { ports?: { id: string; diameterM?: number }[] } | undefined)?.ports
    const diameter = Array.isArray(ports) ? ports.find(value => value.id === port)?.diameterM : undefined
    const radius = typeof diameter === 'number' && Number.isFinite(diameter) && diameter > 0
      ? diameter / 2 : Math.min(w * 0.025, h * 0.04, d * 0.025, 0.025)
    addEndpointCoupler(group, 'generic-cdu-' + port,
      frame.localPosition, frame.localDirection, radius, marker)
  }
}

function buildRack(model: Construction, group: THREE.Group) {
  const pure = geometryPure()
  const size = model.geometry?.size ?? { x: 0, y: 0, z: 0 }
  const cooling = (model.cooling ?? {}) as {
    type?: string
    liquidManifold?: {
      supplyPipe?: { diameter?: number }
      returnPipe?: { diameter?: number }
    }
  }
  const w = Math.max(Number(size.x) || 0, 0.08)
  const d = Math.max(Number(size.z) || 0, 0.08)
  const slotHeight = Number((model.geometry as { slotHeight?: number } | undefined)?.slotHeight) || 0
  const safeBottom = Number((model as Construction & { safeMarginBottom?: number }).safeMarginBottom) || 0
  type RackServer = {
    uid?: string
    meta?: { enabled?: boolean }
    geometry?: { slotPosition?: number; slotOccupation?: number }
    liquidCooling?: { cpCuPairs?: unknown[] }
  }
  const constructions = (model.constructions ?? {}) as { servers?: Record<string, RackServer> }
  const branchLevels = Object.values(constructions.servers ?? {})
    .filter((server) =>
      server.meta?.enabled !== false &&
      (server.liquidCooling?.cpCuPairs?.length ?? 0) > 0 &&
      Number.isFinite(server.geometry?.slotPosition) &&
      slotHeight > 0,
    )
    .map((server) => ({
      id: server.uid ?? 'server',
      y: safeBottom +
        ((Number(server.geometry?.slotPosition) || 0) +
          (Number(server.geometry?.slotOccupation) || 1) / 2) * slotHeight,
    }))
  for (const [port, marker] of [['supply', SUPPLY], ['return', RETURN]] as const) {
    const top = pure.liquidEquipmentPortFrame(model, port, 'top')
    const bottom = pure.liquidEquipmentPortFrame(model, port, 'bottom')
    if (!top || !bottom) continue
    const diameter = port === 'supply'
      ? Number(cooling.liquidManifold?.supplyPipe?.diameter)
      : Number(cooling.liquidManifold?.returnPipe?.diameter)
    const radius = clamp((Number.isFinite(diameter) ? diameter : 0.025) / 2, 0.006, 0.04)
    const height = Math.max(0.01, top.localPosition.distanceTo(bottom.localPosition))
    const manifoldGeometry = new THREE.CylinderGeometry(radius, radius, height, 14)
    pbrMaterials().cylinderUv(manifoldGeometry, radius, height, 'stainless')
    const manifold = detailMesh(
      manifoldGeometry,
      material(STEEL, 0.72, 0.3, 'stainless'),
      `generic-rack-${port}-manifold`,
    )
    manifold.position.copy(top.localPosition).add(bottom.localPosition).multiplyScalar(0.5)
    group.add(manifold)
    addEndpointCoupler(
      group,
      `generic-rack-${port}-quick-connect`,
      top.localPosition,
      top.localDirection,
      radius,
      marker,
    )

    const bodyZ = top.localPosition.z < 0 ? 0 : d
    for (const level of branchLevels) {
      const y = clamp(level.y, bottom.localPosition.y, top.localPosition.y)
      const start = new THREE.Vector3(top.localPosition.x, y, top.localPosition.z)
      const end = new THREE.Vector3(
        clamp(top.localPosition.x, 0, w),
        y,
        bodyZ,
      )
      const direction = end.clone().sub(start)
      const length = direction.length()
      if (length < 1e-5) continue
      // A colour-coded rubber branch hose.
      const hoseGeometry = new THREE.CylinderGeometry(radius * 0.55, radius * 0.55, length, 10)
      pbrMaterials().cylinderUv(hoseGeometry, radius * 0.55, length, 'rubber')
      const hose = detailMesh(
        hoseGeometry,
        material(marker, 0.0, 0.7, 'rubber'),
        `generic-rack-${port}-branch-${level.id}`,
      )
      hose.position.copy(start).add(end).multiplyScalar(0.5)
      hose.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize())
      group.add(hose)
    }
  }
}

export function buildLiquidEquipmentVisual(model: Construction): THREE.Group | null {
  if (model.kind !== 'cdu' && model.kind !== 'rack') return null
  // The structural renderer supports a missing optional PBR library.
  // Defer its textured display skin as well, then rebuild when the library returns.
  if (!(window as unknown as { pbrMaterials?: unknown }).pbrMaterials) return null
  // A generic skin must not hide authored CFD patches, holes or imported shapes.
  // The structural renderer remains available for these editable CDU variants.
  if (model.kind === 'cdu') {
    const faces = model.geometry?.faces as Record<string, { patches?: unknown }> | undefined
    const hasPatches = Object.values(faces ?? {}).some(face => Array.isArray(face?.patches) && face.patches.length > 0)
    if (model.customSTLBase64 || (model.geometry?.cutouts?.length ?? 0) > 0 || hasPatches) return null
  }
  const cooling = (model.cooling ?? {}) as { type?: string }
  if (model.kind === 'rack' && cooling.type !== 'liquid') return null
  const group = new THREE.Group()
  group.name = `liquid-equipment-detail:${model.uid}`
  group.userData = {
    isLiquidEquipmentDetail: true,
    exportExcluded: true,
    selectable: model.kind === 'cdu',
    ...(model.kind === 'cdu' ? { modelId: model.uid } : {}),
    geometryAuthority: 'project-structural-model',
  }
  if (model.kind === 'cdu') {
    buildCdu(model, group)
    applyCduDisplayOpacity(model, group)
  }
  else buildRack(model, group)
  return group
}
