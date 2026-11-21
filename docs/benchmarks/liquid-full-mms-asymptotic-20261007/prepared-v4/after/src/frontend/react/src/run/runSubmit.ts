/**
 * Wiring the run body to the live project and the built scene.
 *
 * Split from `runPayload.ts` so the rules there can be tested without a WebGL
 * context: everything below needs a scene that has actually built its meshes,
 * because the .cfdrun's boundary bindings are derived by PARSING the exported
 * STL solid names — the manifest and the geometry have to come from the same
 * tessellation or the runner binds conditions to patches that are not there.
 */

import type * as THREE from 'three'
import type { CfdProject } from '../schema/cfdproj'
import { DomainProject } from '../domain/DomainProject'
import { t } from '../i18n'
import { buildManifestV2, createCfdrunBuild, type CfdrunContext } from '../legacy/cfdrunBuild'
import { LENGTH_UNITS } from '../lengthUnits'
import { MESH_MODES } from './meshConfig'
import {
  collectDigitNameModels,
  collectFloorHeightErrors,
  collectRoomWallGapErrors,
  collectSeedCollisionTargets,
  collectSeedCollisionWarnings,
  doorPorosityMismatch,
  planRoomWallExtension,
  runPreflightChecks,
} from './meshPreflight'
import {
  setMeshProgressStep,
  showDigitNameDialog,
  showDoorPorosityDialog,
  showFloorHeightDialog,
  showRoomWallGapDialog,
  showSeedCollisionDialog,
} from './meshPreflightUi'
import * as store from '../projectStore'
import { buildRunBody, type RunBuildDeps, type RunKind, type RunRequestBody } from './runPayload'
import type { SceneBuilder } from '../interaction/sceneBuilder'
import { buildStlFiles, duplicateSolidNames } from '../io/stlExport'
import { setUiFrozen } from './uiFreeze'

export interface RunSources {
  project: CfdProject | null
  projectPath: string | null
  builder: SceneBuilder | null
}

/** The project being edited and the scene showing it. */
export function currentRunSources(): RunSources {
  return {
    project: store.getProject(),
    projectPath: store.getPath(),
    builder: (window as unknown as { __sceneBuilder?: SceneBuilder }).__sceneBuilder ?? null,
  }
}

/**
 * Flush the world matrices before anything reads them.
 *
 * The manifest's positions come from `matrixWorld`, which Three only refreshes
 * during a render. A model moved in the same frame as the button press would
 * otherwise be exported at its previous position — the legacy calls
 * `wrapperGroup.updateMatrixWorld(true)` first for this reason
 * (STL_generate.html:28079).
 */
function flushWorldMatrices(node: THREE.Object3D | null | undefined): void {
  if (!node) return
  let root: THREE.Object3D = node
  while (root.parent) root = root.parent
  root.updateMatrixWorld(true)
}

/**
 * The grouped STL map, reusing the one the manifest build just produced.
 *
 * `buildComprehensiveStlMap` stashes it on `window._meshExportGroupedMap`
 * (`js/cfdrun_build.js:1142`) precisely so the upload does not re-tessellate
 * every rack. Consumed and cleared, as the legacy does at
 * STL_generate.html:28192, so a later run can never pick up a stale map.
 */
function takeGroupedStl(builder: SceneBuilder, project: CfdProject): Record<string, string> {
  const w = window as unknown as { _meshExportGroupedMap?: Record<string, string> | null }
  const cached = w._meshExportGroupedMap
  w._meshExportGroupedMap = null
  if (cached && Object.keys(cached).length) return cached
  return buildStlFiles(builder, project, true).files
}

export function sceneRunDeps(sources: RunSources): RunBuildDeps {
  const { project, projectPath, builder } = sources
  return {
    project,
    projectPath,
    modelUids: () =>
      project ? new DomainProject(project).structuralForAirExport().map(entity => entity.uid) : [],
    duplicateSolids: (uids) =>
      builder && project ? duplicateSolidNames(builder, project, uids) : [],
    buildManifest: async () => {
      setMeshProgressStep('cfdrun')
      if (!builder || !project) throw new Error('the 3D scene is not ready yet')
      const globalGroup = builder.registries.renderRoots[0]?.parent ?? null
      flushWorldMatrices(globalGroup)
      const api = createCfdrunBuild({
        state: project,
        globalGroup,
        LENGTH_UNITS,
        _MESH_MODES: MESH_MODES,
        // An INSTANCE method of the scene: without it the porous-solid pass
        // fails inside a catch and the manifest comes out with no porous
        // bindings at all.
        buildGroupedStlMap: (modelIds: string[], leakage: boolean) =>
          builder.api.buildGroupedStlMap(modelIds, leakage),
      } as unknown as CfdrunContext)
      // Plain ISO-8601, the string the legacy export passes: it lands in
      // `sourceProject.exportedAt` and `runId`.
      return buildManifestV2(api, project, new Date().toISOString())
    },
    buildStl: () => {
      setMeshProgressStep('stl')
      if (!builder || !project) throw new Error('the 3D scene is not ready yet')
      return takeGroupedStl(builder, project)
    },
  }
}

/**
 * A run refused by the pre-flight rather than by the server.
 *
 * Its own type so a caller can tell "the user pressed Back" apart from "the
 * build blew up": the message is already the status line the legacy writes, and
 * a stack trace would be noise on top of a decision the user just made.
 *
 * An EMPTY message means the dialog already fixed what it found (the room-wall
 * extension): the run still stops, and there is nothing left to report, so the
 * panel shows no error, as the legacy clears its status line.
 */
export class PreflightStop extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'PreflightStop'
  }
}

/**
 * The checks that stand between the run button and `/api/run-mesh`.
 *
 * Mesh runs only, exactly as the legacy gates them (`mode !== 'solve'` at
 * STL_generate.html:28091 and the `mode === 'solve' ? [] :` at :28114). A solve
 * re-uses a mesh that already exists, so a name or a seed the mesher rejected
 * would have stopped the run that BUILT that mesh — re-asking here would only
 * block a solve on geometry that is already meshed and fine.
 *
 * Order is the legacy's: names, floors, room walls, seeds, doors. Each is a modal, and
 * showing the third problem first spends the user's first decision on the wrong
 * one. Duplicate solid names stay in `buildRunBody` — that gate was already
 * there and adding a second one would let the two disagree.
 */
export async function runMeshPreflight(project: CfdProject | null): Promise<void> {
  const digitNames = collectDigitNameModels(project)
  if (digitNames.length) {
    await showDigitNameDialog(digitNames)
    throw new PreflightStop(t('error.digit_name_title'))
  }

  const floorHeights = collectFloorHeightErrors(project)
  if (floorHeights.length) {
    await showFloorHeightDialog(floorHeights)
    throw new PreflightStop(t('status.floor_height_error'))
  }

  const roomWallGaps = collectRoomWallGapErrors(project)
  if (roomWallGaps.length) {
    const choice = await showRoomWallGapDialog(roomWallGaps, () =>
      extendRoomWallsToHeight(
        roomWallGaps.map((gap) => gap.uid),
        roomWallGaps[0]!.roomHeight,
      ),
    )
    // Either way the run stops: after the walls are raised the user sees them
    // and presses run again, as in the legacy.
    throw new PreflightStop(choice === 'extended' ? '' : t('status.room_wall_gap_error'))
  }

  const seedCollisions = collectSeedCollisionWarnings(project)
  if (seedCollisions.length) {
    const proceed = await showSeedCollisionDialog(seedCollisions)
    // The legacy does not put the overlay back when the user continues
    // (:28122). Neither does this: re-opening it would restart the step
    // animation from zero and read as if the run had begun again.
    if (!proceed) throw new PreflightStop(t('status.seed_collision_cancelled'))
  }

  const doors = doorPorosityMismatch(project)
  if (doors.length) {
    await showDoorPorosityDialog(doors)
    throw new PreflightStop(
      doors.map((d) => `${d.name}: ${d.pct}%`).join(', '),
    )
  }
}

/**
 * Raise room walls so their tops reach `roomHeight`, as ONE undo step.
 *
 * The room-wall dialog's extend button. `store.edit` takes the checkpoint, so a
 * single Ctrl+Z puts every wall back. Returns the number of walls changed; 0
 * means nothing needed changing and no undo step was taken.
 */
export function extendRoomWallsToHeight(uids: readonly string[], roomHeight: number): number {
  const plan = planRoomWallExtension(store.getProject(), uids, roomHeight)
  if (!plan.length) return 0
  store.edit(
    'extendRoomWalls',
    `Extend room walls to ${Number(roomHeight)} m: ${plan.map((p) => p.name).join(', ')}`,
    (draft) => {
      for (const p of plan) {
        const geometry = store.findConstruction(draft, p.uid)?.model.geometry as
          | { size?: { y?: number } }
          | undefined
        if (geometry?.size) geometry.size.y = p.to
      }
    },
  )
  return plan.length
}

/** The body for a run of the geometry currently on screen. */
export async function buildRunRequest(
  kind: RunKind,
  sources: RunSources = currentRunSources(),
): Promise<RunRequestBody> {
  const deps = sceneRunDeps(sources)
  if (kind === 'mesh') {
    setMeshProgressStep('preflight')
    await runMeshPreflight(sources.project)
  }
  // The `cfdrun` and `stl` steps are ticked from inside the deps, because
  // `buildRunBody` owns the order those two run in.
  const body = await buildRunBody(kind, deps)
  setMeshProgressStep('upload')
  return body
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx: the run
// body is what the server acts on, and checking it any other way means starting
// a real mesh job and reading the case directory afterwards.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __dcdtuniRun: unknown }).__dcdtuniRun = {
    buildRunRequest,
    currentRunSources,
    setUiFrozen,
    // The pre-flight findings as DATA. The dialogs are modal, so the only other
    // way to see what the gate decided is to press the run button and read the
    // overlay that appears — which is exactly what a check should not require.
    preflight: () => runPreflightChecks(store.getProject()),
    // The AABBs the seed check tests against, so a warning that disagrees with
    // the legacy's can be traced to the box rather than to the seed.
    collisionTargets: () => collectSeedCollisionTargets(store.getProject()),
    runMeshPreflight,
  }
}
