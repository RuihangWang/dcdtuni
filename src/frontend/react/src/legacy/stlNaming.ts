/**
 * The STL naming and grouping layer, shared with the legacy editor.
 *
 * `js/stl_naming.js` decides the `solid <name>` written into each exported file
 * and which merged file a rack's solids are routed into. It is pure — model
 * data in, strings out — so both editors can call it and route every solid to
 * the same OpenFOAM patch.
 *
 * A wrong name here is not a rendering bug. The solid name IS the patch name, so
 * a rename silently moves a boundary condition onto a different surface, and it
 * still passes the runner's mesh-acceptance gate: that gate checks every region
 * declared in snappyHexMeshDict/refinementSurfaces exists in polyMesh/boundary,
 * which a mis-routed solid satisfies while heating the wrong wall.
 */

import '../../../shared/js/stl_naming.js'

import type { Construction } from '../schema/cfdproj'

export interface ThroughZoneGroup {
  startUnit: number
  endUnit: number
  sigma?: number
}

interface StlNamingModule {
  /** Everything outside [A-Za-z0-9_] becomes '_'; an empty name becomes 'model'. */
  sanitizeName(text: string | null | undefined): string
  getFaceDisplayName(face: { displayName?: string; label?: string }): string | undefined
  /** Walls, floors and baffles get a name with no face component. */
  usesSyntheticSingleFaceExportName(model: Construction | null | undefined): boolean
  composeModelScopedExportName(
    model: Construction,
    faceId: string,
    face: unknown,
    opts?: Record<string, unknown>
  ): string
  getPatchFinalExportName(model: Construction, faceId: string, patch: { id: string }): string
  getRackServerViews(model: Construction): unknown[]
  getRackEquipmentEndUnit(equipment: unknown): number
  getRackEquipmentExportStem(model: Construction, equipment: unknown): string | null
  /** Contiguous U ranges where both front and back are deleted — the through zones. */
  getRackThroughZoneGroups(model: Construction): ThroughZoneGroup[]
  /**
   * The server bucket key ("14" for a 14U server) for a solid name.
   *
   * Pass `ownerModel`. Without it the function falls back to
   * `window.allModels()`, which is both slower and the module's only impure
   * edge; the fallback exists only to preserve legacy behaviour.
   */
  computeServerBucketKeyForSolid(solidName: string, ownerModel?: Construction): string | null
  /** Which merged_*.stl a rack solid belongs in, or null when the scene handles it. */
  classifyRackSolidToBucketFile(
    solidName: string,
    model: Construction,
    opts?: { blankingPerRack?: boolean }
  ): string | null
  /** `meshConfig.blankingPanelPatches === 'perRack'`: one blanking bucket per rack and U span. */
  blankingBucketOptions(project: unknown): { blankingPerRack: boolean }
}

export function stlNaming(): StlNamingModule {
  const mod = (window as unknown as { stlNaming?: StlNamingModule }).stlNaming
  if (!mod) {
    throw new Error(
      'window.stlNaming is missing — js/stl_naming.js did not load. It attaches to window on import.'
    )
  }
  return mod
}
