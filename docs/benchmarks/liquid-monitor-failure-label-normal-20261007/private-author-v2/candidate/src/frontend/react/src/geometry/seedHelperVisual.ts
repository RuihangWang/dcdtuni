/**
 * The seed helper: a hub at a surface's seed anchor and an arrow to each side.
 *
 * A port of `updateSeedSurfaceHelper` (STL_generate.html:7330). Without it the
 * inspector's 显示 toggle wrote a flag nothing read — the same defect class as
 * the rotation-snap flag that had no gizmo to snap.
 *
 * Why it matters: snappyHexMesh keeps the side of a surface its seed lands in.
 * The two arrows show WHICH two points the offset produces, so a seed that has
 * ended up in the wrong volume — a plenum instead of the room — is visible
 * instead of being discovered as a mesh with the wrong half of the domain.
 *
 * `depthTest: false` throughout, as in the legacy: a seed sits ON a surface, so
 * a depth-tested helper is hidden by the very face it belongs to.
 */

import * as THREE from 'three'
import { registerHelper, unregisterHelper } from './helperRegistry'
import { seedEndpoints, type SeedConfig, type SeedSurface } from './seedConfig'

const SEED_COLOR = 0xf0c040

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export interface SeedHelperHandle {
  group: THREE.Group
  /**
   * Move the hub and tips to a new config, without rebuilding.
   *
   * A drag emits a config every frame. Rebuilding would allocate three
   * geometries and two arrows per frame, and — worse — the builder reads the
   * PUBLISHED surface, which the panel only republishes on re-render, so a
   * rebuild mid-drag would keep snapping the helper back to where the drag
   * started. The legacy has the same split: `updateSeedSurfaceHelper` builds,
   * `repositionSeedSurfaceHelper` (STL_generate.html:7420) moves.
   */
  reposition(cfg: SeedConfig): void
  /** The three meshes a pointer can grab: the hub and one tip per side. */
  handles(): THREE.Object3D[]
  dispose(): void
}

/**
 * Build the helper for one surface, in world space.
 *
 * Sized off the surface's local cell size, like the legacy: a helper scaled to
 * the room would be invisible on a 40 mm floor tile and a helper at a fixed
 * size would swallow it.
 */
export function buildSeedHelper(surface: SeedSurface): SeedHelperHandle {
  const { anchor, positive, negative } = seedEndpoints(surface.seedConfig)
  const extent = Math.max(surface.localCellSize || 0.05, 0.05)
  const tipRadius = clamp(extent * 0.16, 0.025, 0.08)
  const headLength = clamp(extent * 0.24, 0.05, 0.18)
  const headWidth = clamp(extent * 0.16, 0.04, 0.1)
  const hubRadius = clamp(tipRadius * 0.7, 0.02, 0.06)

  const group = new THREE.Group()
  group.name = 'seedHelper'
  const owned: (THREE.BufferGeometry | THREE.Material)[] = []
  const arrows: Record<'positive' | 'negative', THREE.ArrowHelper | null> = {
    positive: null,
    negative: null,
  }
  const tips: Record<'positive' | 'negative', THREE.Mesh | null> = {
    positive: null,
    negative: null,
  }

  const origin = new THREE.Vector3(anchor.x, anchor.y, anchor.z)

  const arm = (side: 'positive' | 'negative', point: { x: number; y: number; z: number }) => {
    const target = new THREE.Vector3(point.x, point.y, point.z)
    const direction = target.clone().sub(origin)
    const length = Math.max(direction.length(), 1e-6)
    const arrow = new THREE.ArrowHelper(
      direction.normalize(),
      origin,
      length,
      SEED_COLOR,
      Math.min(headLength, length * 0.4),
      headWidth,
    )
    for (const part of [arrow.line, arrow.cone] as { renderOrder: number; material: THREE.Material }[]) {
      part.renderOrder = 80
      part.material.depthTest = false
      part.material.transparent = true
    }
    group.add(arrow)
    arrows[side] = arrow

    // WP-S3: UNIT sphere at the legacy tip radius, registered with the shared
    // per-frame helper scaling. The scale write is the initial condition (and
    // the fallback while the registry is absent); once registered, the
    // render-loop sync owns it.
    const geometry = new THREE.SphereGeometry(1, 18, 18)
    const material = new THREE.MeshPhongMaterial({
      color: SEED_COLOR,
      shininess: 28,
      specular: 0x111111,
      depthTest: false,
    })
    const tip = new THREE.Mesh(geometry, material)
    tip.scale.setScalar(tipRadius)
    registerHelper(tip, tipRadius)
    tip.position.copy(target)
    tip.renderOrder = 81
    // The legacy tags these so a click can grab them; the tag is kept so a
    // future drag has the same handle to find, and so a pick never mistakes a
    // helper for geometry.
    tip.userData = {
      isSeedHandle: true,
      seedSide: side,
      seedSurfaceKey: `${surface.owner}:${surface.modelUid}:${surface.solidName}`,
      modelId: surface.modelUid,
      faceId: surface.faceId,
      patchId: surface.patchId,
      wallIndex: surface.wallIndex,
    }
    group.add(tip)
    tips[side] = tip
    owned.push(geometry, material)
  }

  arm('negative', negative)
  arm('positive', positive)

  const hubGeometry = new THREE.SphereGeometry(1, 18, 18)
  const hubMaterial = new THREE.MeshPhongMaterial({
    color: SEED_COLOR,
    shininess: 16,
    specular: 0x111111,
    depthTest: false,
  })
  const hub = new THREE.Mesh(hubGeometry, hubMaterial)
  hub.scale.setScalar(hubRadius)
  registerHelper(hub, hubRadius)
  hub.position.copy(origin)
  hub.renderOrder = 82
  hub.userData = {
    isSeedHandle: true,
    seedSide: 'anchor',
    seedSurfaceKey: `${surface.owner}:${surface.modelUid}:${surface.solidName}`,
    modelId: surface.modelUid,
  }
  group.add(hub)
  owned.push(hubGeometry, hubMaterial)

  return {
    group,
    reposition(cfg: SeedConfig) {
      const next = seedEndpoints(cfg)
      const centre = new THREE.Vector3(next.anchor.x, next.anchor.y, next.anchor.z)
      hub.position.copy(centre)
      for (const side of ['positive', 'negative'] as const) {
        const point = side === 'positive' ? next.positive : next.negative
        const target = new THREE.Vector3(point.x, point.y, point.z)
        tips[side]?.position.copy(target)
        const arrow = arrows[side]
        if (!arrow) continue
        const direction = target.clone().sub(centre)
        const length = Math.max(direction.length(), 1e-6)
        arrow.position.copy(centre)
        arrow.setDirection(direction.normalize())
        arrow.setLength(length, Math.min(headLength, length * 0.4), headWidth)
        // `setLength` rebuilds the cone's transform and, in three r184, resets
        // the material state it was given at construction — so depth testing
        // has to be turned back off or the helper vanishes into the surface it
        // sits on, mid-drag.
        for (const part of [arrow.line, arrow.cone] as { material: THREE.Material }[]) {
          part.material.depthTest = false
          part.material.transparent = true
        }
      }
    },
    handles() {
      return [hub, tips.positive, tips.negative].filter(Boolean) as THREE.Object3D[]
    },
    dispose() {
      group.parent?.remove(group)
      unregisterHelper(hub)
      for (const tip of [tips.positive, tips.negative]) {
        if (tip) unregisterHelper(tip)
      }
      for (const item of owned) item.dispose()
      group.traverse((o) => {
        const helper = o as unknown as { dispose?: () => void }
        if (typeof helper.dispose === 'function' && o !== group) helper.dispose()
      })
      group.clear()
    },
  }
}
