/**
 * Generic cabinet detail for explicitly named PDU and UPS power BOXes.
 *
 * Local meshes share the original owner and transform but never enter
 * renderMeshes. The saved BOX and its six surfaces remain the STL/CFD
 * authority; a display role identifies the skin, not a new electrical component.
 */
import * as THREE from 'three'
import type { Construction } from '../schema/cfdproj'
import { pbrMaterials } from '../legacy/geometryBuilder'

type Role = 'painted-steel' | 'stainless' | 'rubber' | null
type FaceId = 'front' | 'back' | 'left' | 'right' | 'top' | 'bottom'
type Vec3 = [number, number, number]
interface PartOptions {
  color?: number
  role?: Role
  metalness?: number
  roughness?: number
  bevel?: number
  unlit?: boolean
  faceId?: FaceId
}

const GRAPHITE = 0x252c33
const DOOR = 0x35404b
const STEEL = 0x8b969e
const DARK = 0x131b22

/** Copy suffixes and separators are accepted. UPS/rack strips and PDU-like
 * substrings never acquire this visual through their physical BOX type. */
export function isPduDisplayModel(model: Construction): boolean {
  return model.kind === 'box' &&
    /^\s*pdu(?=$|[\s_\-\u2010-\u2015])/iu.test(String(model.meta?.name ?? ''))
}

/** Explicit UPS cabinets use the same display-only fabrication details while
 * retaining their UPS identity and BOX geometry authority. */
function powerCabinetDisplayRole(model: Construction): 'pdu' | 'ups' | null {
  if (isPduDisplayModel(model)) return 'pdu'
  return model.kind === 'box' &&
    /^\s*ups(?=$|[\s_\-\u2010-\u2015])/iu.test(String(model.meta?.name ?? ''))
    ? 'ups' : null
}

function bevelBox(x: number, y: number, z: number, radius: number) {
  const b = Math.min(radius, x / 4, y / 4, z / 4)
  const sx = x / 2 - b
  const sy = y / 2 - b
  const outline = new THREE.Shape()
  outline.moveTo(-sx, -sy)
  outline.lineTo(sx, -sy)
  outline.lineTo(sx, sy)
  outline.lineTo(-sx, sy)
  outline.closePath()
  const geometry = new THREE.ExtrudeGeometry(outline, {
    depth: z - 2 * b,
    steps: 1,
    bevelEnabled: true,
    bevelSize: b,
    bevelThickness: b,
    bevelSegments: 2,
  })
  geometry.translate(0, 0, -z / 2 + b)
  return geometry
}

/** Metre-based projection also handles the bevel's non-BoxGeometry layout. */
function physicalUv(geometry: THREE.BufferGeometry, role: Exclude<Role, null>) {
  const tile = pbrMaterials().ROLES[role]!.tileM
  const positions = geometry.getAttribute('position')
  const normals = geometry.getAttribute('normal')
  const uv = new Float32Array(positions.count * 2)
  for (let i = 0; i < positions.count; i += 1) {
    const nx = Math.abs(normals.getX(i))
    const ny = Math.abs(normals.getY(i))
    const nz = Math.abs(normals.getZ(i))
    if (nx >= ny && nx >= nz) {
      uv[2 * i] = positions.getZ(i) / tile
      uv[2 * i + 1] = positions.getY(i) / tile
    } else if (ny >= nz) {
      uv[2 * i] = positions.getX(i) / tile
      uv[2 * i + 1] = positions.getZ(i) / tile
    } else {
      uv[2 * i] = positions.getX(i) / tile
      uv[2 * i + 1] = positions.getY(i) / tile
    }
  }
  geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2))
}

/** Existing PDU-only callers retain their original admission contract. */
export function buildPduEquipmentVisual(model: Construction): THREE.Group | null {
  return isPduDisplayModel(model) ? buildPowerCabinetVisual(model) : null
}

export function buildPowerCabinetVisual(model: Construction): THREE.Group | null {
  const cabinetRole = powerCabinetDisplayRole(model)
  if (!cabinetRole) return null
  // Keep the owned BOX surfaces usable without the optional texture library.
  if (!(window as unknown as { pbrMaterials?: unknown }).pbrMaterials) return null
  const size = model.geometry?.size
  if (!size || ![size.x, size.y, size.z].every(v => Number.isFinite(v) && v > 0)) {
    return null
  }
  // A rectangular skin must not cover an authored opening, imported shape or
  // editable CFD patch. Keep those BOXes' existing display and export.
  const faces = model.geometry?.faces
  const hasPatches = faces && Object.values(faces).some(face => {
    const patches = (face as { patches?: unknown } | null)?.patches
    return Array.isArray(patches) && patches.length > 0
  })
  if (model.customSTLBase64 || (model.geometry?.cutouts?.length ?? 0) > 0 || hasPatches) {
    return null
  }

  const { x: w, y: h, z: d } = size
  const skin = Math.min(0.0018, Math.min(w, h, d) * 0.003)
  const lip = Math.min(0.016, d * 0.025)
  const plinth = Math.min(0.085, h * 0.04)
  const group = new THREE.Group()
  group.name = cabinetRole + '-equipment-detail:' + model.uid
  group.userData = {
    equipmentVisualRole: cabinetRole,
    equipmentVisualDetail: true,
    exportExcluded: true,
    selectable: true,
    modelId: model.uid,
    geometryAuthority: 'project-structural-model',
  }
  const value = model.meta?.display?.opacity
  const modelOpacity = Number.isFinite(value) ? Math.min(1, Math.max(0, value as number)) : 1

  function part(name: string, dimensions: Vec3, position: Vec3, options: PartOptions = {}) {
    const {
      color = DOOR,
      role = 'painted-steel',
      metalness = 0.18,
      roughness = 0.66,
      bevel = 0,
      unlit = false,
      faceId = 'front',
    } = options
    const [x, y, z] = dimensions
    const geometry = bevel > 0
      ? bevelBox(x, y, z, bevel)
      : new THREE.BoxGeometry(x, y, z)
    if (role) physicalUv(geometry, role)
    const material = role
      ? pbrMaterials().material(role, { color, metalness, roughness })
      : unlit
        ? new THREE.MeshBasicMaterial({ color, toneMapped: false })
        : new THREE.MeshStandardMaterial({ color, metalness, roughness })
    // The actual CDU browser review showed powder-coat normal relief can
    // overwhelm the sheet-metal finish. Soften only this local PDU role.
    if (role === 'painted-steel') {
      (material as THREE.MeshStandardMaterial).normalScale.set(0.16, 0.16)
      material.userData.pbrNormalScale = 0.16 // pending texture binding reads this scalar
    }
    const authored = (faces as Record<string, { opacity?: unknown }> | undefined)?.[faceId]?.opacity
    const faceOpacity = Number.isFinite(authored) ? Math.min(1, Math.max(0, authored as number)) : 1
    const opacity = Math.min(modelOpacity, faceOpacity)
    material.opacity = opacity
    material.transparent = opacity < 1
    material.depthWrite = opacity >= 1
    const mesh = new THREE.Mesh(geometry, material)
    mesh.name = 'generic-' + cabinetRole + '-' + name
    mesh.userData = {
      equipmentVisualDetail: true,
      equipmentVisualRole: cabinetRole,
      exportExcluded: true,
      selectable: true,
      modelId: model.uid,
      // A plain click on the visible skin still edits the corresponding
      // existing BOX face. Shift is handled by the original Viewport gesture.
      isBaseWall: true,
      faceId,
    }
    // These part materials are private, including a retained fade/highlight
    // original. The shared builder disposes only the material currently on a
    // mesh. Release hidden private originals when its geometry is cleared;
    // current materials and shared texture maps keep their existing lifecycle.
    let initialDisposed = false
    let geometryDisposed = false
    material.addEventListener('dispose', () => { initialDisposed = true })
    geometry.addEventListener('dispose', () => {
      if (geometryDisposed) return
      geometryDisposed = true
      const current = new Set<THREE.Material>(Array.isArray(mesh.material) ? mesh.material : [mesh.material])
      const kept = mesh.userData._origMaterial as THREE.Material | THREE.Material[] | undefined
      const hidden = new Set<THREE.Material>([material, ...(kept ? Array.isArray(kept) ? kept : [kept] : [])])
      for (const retained of hidden) {
        if (current.has(retained) || (retained === material && initialDisposed)) continue
        retained.dispose()
      }
    })
    mesh.position.set(...position)
    group.add(mesh)
    return mesh
  }

  function shell(name: string, bottom: number, height: number, color: number, roughness: number) {
    const middle = bottom + height / 2
    const common: PartOptions = { color, metalness: 0.2, roughness, bevel: skin * 0.35 }
    part(name, [w + 2 * skin, height, skin], [w / 2, middle, -skin / 2],
      { ...common, faceId: 'front' })
    part(name + '-back', [w + 2 * skin, height, skin], [w / 2, middle, d + skin / 2],
      { ...common, faceId: 'back' })
    part(name + '-left', [skin, height, d + 2 * skin], [-skin / 2, middle, d / 2],
      { ...common, faceId: 'left' })
    part(name + '-right', [skin, height, d + 2 * skin], [w + skin / 2, middle, d / 2],
      { ...common, faceId: 'right' })
    // A quarter-skin exterior lip avoids coplanar z-fighting with the
    // original BOX top without renderer-only polygon-offset tricks.
    part(name + '-top', [w + 2 * skin, skin, d + 2 * skin], [w / 2, bottom + height - skin / 4, d / 2],
      { ...common, faceId: 'top' })
    part(name + '-bottom', [w + 2 * skin, skin, d + 2 * skin], [w / 2, bottom + skin / 2, d / 2],
      { ...common, faceId: 'bottom' })
  }

  // Six independent visual panels preserve each BOX face's editing identity.
  // The folded housing has at most a thin skin outside the logical BOX.
  shell('cabinet', plinth, h - plinth, GRAPHITE, 0.68)
  shell('plinth', 0, plinth, DARK, 0.74)
  const rim = Math.min(w * 0.035, h * 0.018)
  const doorBottom = plinth + rim
  const doorH = h - doorBottom - rim
  part('door-gasket', [w - 2 * rim, doorH, skin * 1.3],
    [w / 2, doorBottom + doorH / 2, -skin * 1.7],
    { color: DARK, role: 'rubber', metalness: 0.02, roughness: 0.9 })
  part('service-door', [w - 2 * rim - 2 * skin, doorH - 2 * skin, skin],
    [w / 2, doorBottom + doorH / 2, -skin * 2.8], { roughness: 0.64, bevel: skin * 0.4 })

  const steel: PartOptions = { color: STEEL, role: 'stainless', metalness: 0.65, roughness: 0.38 }
  for (const sign of [-1, 1]) {
    part('door-frame-' + sign, [rim * 0.22, doorH, skin * 0.7],
      [w / 2 + sign * (w / 2 - rim * 0.88), doorBottom + doorH / 2, -skin * 2.8], steel)
  }
  for (let i = 0; i < 3; i += 1) {
    part('hinge-' + i, [rim * 0.48, Math.min(0.065, h * 0.028), lip * 0.55],
      [rim * 0.95, doorBottom + doorH * (0.18 + i * 0.32), -lip * 0.28],
      { ...steel, bevel: skin * 0.35 })
  }
  const hx = w - rim * 1.8
  const hy = doorBottom + doorH * 0.45
  const handleH = Math.min(0.16, h * 0.075)
  for (const sign of [-1, 1]) {
    part('handle-' + (sign > 0 ? 'top' : 'bottom'), [rim * 0.38, skin * 3, lip],
      [hx, hy + sign * handleH / 2, -lip * 0.55], { ...steel, bevel: skin * 0.3 })
  }
  part('handle-grip', [rim * 0.3, handleH, skin * 3], [hx, hy, -lip + skin * 1.5],
    { color: DARK, role: 'rubber', metalness: 0.02, roughness: 0.8, bevel: skin * 0.3 })

  // Generic instrument cluster and breaker access; no vendor ratings/states.
  const mw = w * 0.28
  const mh = h * 0.13
  const mx = w * 0.5
  const my = h * 0.79
  part('meter-frame', [mw, mh, skin * 2], [mx, my, -skin * 4],
    { ...steel, metalness: 0.55, roughness: 0.4, bevel: skin * 0.5 })
  part('meter-bezel', [mw * 0.91, mh * 0.88, skin * 1.5], [mx, my, -skin * 5.7],
    { color: DARK, role: 'rubber', metalness: 0.02, roughness: 0.85, bevel: skin * 0.4 })
  // Unlit ink survives editor exposure/light controls. Fixed diagram bars
  // illustrate a display without inventing live numeric measurements.
  part('hmi-screen', [mw * 0.77, mh * 0.58, skin * 0.5], [mx, my + mh * 0.06, -skin * 6.6],
    { color: 0x1b3847, role: null, unlit: true })
  for (let i = 0; i < 3; i += 1) {
    part('hmi-line-' + i, [mw * (0.43 - i * 0.06), mh * 0.032, skin * 0.25],
      [mx - mw * 0.05, my + mh * (0.19 - i * 0.14), -skin * 7],
      { color: 0x75a6aa, role: null, unlit: true })
    part('meter-button-' + i, [mh * 0.065, mh * 0.065, skin * 0.7],
      [mx + (i - 1) * mw * 0.22, my - mh * 0.32, -skin * 6.6],
      { color: 0x515e69, metalness: 0.15, roughness: 0.7, bevel: skin * 0.3 })
  }
  const bw = w * 0.52
  const bh = h * 0.22
  const by = h * 0.53
  part('breaker-frame', [bw, bh, skin * 1.7], [w / 2, by, -skin * 4],
    { ...steel, metalness: 0.55, roughness: 0.4, bevel: skin * 0.4 })
  part('breaker-panel', [bw * 0.95, bh * 0.95, skin], [w / 2, by, -skin * 5.5],
    { color: GRAPHITE, metalness: 0.15, roughness: 0.7, bevel: skin * 0.3 })
  for (let i = 0; i < 6; i += 1) {
    const x = w / 2 + (i % 3 - 1) * bw * 0.28
    const y = by + (i < 3 ? 1 : -1) * bh * 0.23
    part('breaker-recess-' + i, [bw * 0.22, bh * 0.32, skin * 0.8], [x, y, -skin * 6.5],
      { color: DARK, role: 'rubber', metalness: 0.02, roughness: 0.85, bevel: skin * 0.3 })
    part('breaker-toggle-' + i, [bw * 0.1, bh * 0.13, skin * 2], [x, y, -skin * 7.7],
      { color: 0x66737f, metalness: 0.15, roughness: 0.62, bevel: skin * 0.3 })
    part('breaker-label-' + i, [bw * 0.15, bh * 0.025, skin * 0.4], [x, y - bh * 0.19, -skin * 6.6],
      { color: 0xaab4b9, role: null, metalness: 0.02, roughness: 0.7 })
  }

  // Folded louver lips are surface detail: no physical vent holes or new
  // CFD patch names are introduced.
  const lw = w * 0.61
  const ly = doorBottom + doorH * 0.17
  const spacing = Math.min(0.018, doorH * 0.018)
  for (let i = 0; i < 8; i += 1) {
    part('louver-shadow-' + i, [lw, spacing * 0.35, skin * 0.7], [w / 2, ly + i * spacing, -skin * 3.5],
      { color: DARK, role: 'rubber', metalness: 0.02, roughness: 0.9 })
    const blade = part('louver-blade-' + i, [lw, spacing * 0.25, skin * 1.5],
      [w / 2, ly + i * spacing + spacing * 0.12, -skin * 4.7],
      { color: GRAPHITE, metalness: 0.2, roughness: 0.67 })
    blade.rotation.x = -0.22
  }
  for (const x of [rim * 1.4, w - rim * 1.4]) {
    for (const y of [doorBottom + rim, h - rim * 1.4]) {
      part('captive-fastener', [skin * 3, skin * 3, skin * 0.7], [x, y, -skin * 3.4],
        { ...steel, roughness: 0.33, bevel: skin * 0.2 })
    }
  }
  return group
}
