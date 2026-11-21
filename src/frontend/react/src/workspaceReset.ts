/**
 * Empty the workspace before a project is installed in it.
 *
 * Loading a project replaces the PROJECT. Everything else the editor has
 * accumulated around it — a result surface, a mesh preview, streamlines, a
 * dimmed model, a selection, a 2D editor — belongs to the file that is being
 * closed, and none of it is in the .cfdproj, so nothing about the new document
 * can remove it. The measured symptom was a post-processing surface from one
 * project still drawn over the geometry of the next
 * (`__ppViewport.stats().layers` stayed `{surface: 1}` across a load).
 *
 * The legacy editor solves this the same way, at the top of
 * `_applyProjectData` (STL_generate.html:14953-14961): exit the 2D editor,
 * `ppClearAll`, drop the resize handles, close the mesh preview, remove the
 * blockMesh grid, destroy the solver-monitor window — all before the new state
 * is normalised. This module is that sequence for the port.
 *
 * ## Best effort, one step at a time
 *
 * Every step is wrapped, and a step that throws does not stop the ones after
 * it. The legacy wraps its whole cleanup block in a `try` for the same reason:
 * some of these helpers touch WebGL objects that do not exist in a headless
 * context, and a cleanup failure must never cost the user the load they asked
 * for. The report says which steps ran and which did not, so a silent
 * half-reset is visible rather than mysterious.
 *
 * ## What is NOT reset
 *
 * The connection: the case directory, the server session, the auth cookie, a
 * mesh or solve job already running. Those belong to the BACKEND, not to the
 * document — a load that dropped them would leave the app unable to mesh the
 * project it had just opened.
 */

import { resetMeshPreviewSession } from './run/meshPreviewSession'
import { setMeshWarnings } from './run/meshWarnings'
import { getMeshVisuals } from './run/meshViewState'
import { exitRackDetail } from './geometry/rackDetail'
import { exitEditor as exitRoom2dEditor } from './room2d/room2dApi'
import { setActiveSeedSurface } from './geometry/seedActions'
import { resetSensorResults } from './run/sensorResults'
import { resetSolverMonitor } from './run/solverMonitor'
import { setPreviewActive } from './io/stlPreview'
import { liquidRunState, resetLiquidRun } from './liquid/liquidSession'
import { setSelectedLiquidRun } from './liquid/liquidPostprocess'
import { resetLiquidMonitorSelection } from './liquid/liquidMonitorConfig'
import { resetRuntimeClient } from './liquid/liquidRuntimeClient'
import { resetTempColoring } from './liquid/liquidTempColor'
import { clearPpHighlight } from './postprocess/ppPatchHighlight'
import { resetPpView } from './postprocess/ppViewState'
import { setTempUnit } from './postprocess/ppUnits'

/**
 * The viewport handles the reset reaches for.
 *
 * Optional on purpose: the viewport publishes them when it mounts, and a reset
 * can run before that — the harness calls `_applyProjectData` on a page that
 * has only just started.
 */
export interface WorkspaceResetWindow {
  __ppViewport?: {
    clear?(): void
    setModelOpacity?(v: number): void
    setFieldOpacity?(v: number): void
    setLightIntensity?(v: number): void
    setAO?(on: boolean): unknown
  }
  __attachMeshPreview?(group: unknown): void
  __setGeometryOpacity?(value: number): void
}

/**
 * The post-processing display defaults.
 *
 * The right panel dims the geometry to 15% when it mounts and never puts it
 * back, so a project loaded after a visit to that tab drew its model at 15%
 * with no panel on screen to explain why. These are the values the viewport
 * starts at.
 */
export const POSTPROCESS_DISPLAY_DEFAULTS = {
  modelOpacity: 1,
  fieldOpacity: 1,
  lightIntensity: 1,
  ao: false,
} as const

export interface WorkspaceResetStep {
  name: string
  run(): void
}

export interface WorkspaceResetReport {
  ran: string[]
  failed: { name: string; error: string }[]
}

/**
 * The steps, in the legacy's order, with two ends pinned.
 *
 * The 2D editor goes FIRST: it can still be holding a draft of the room that is
 * being replaced, and its exit writes into the project.
 *
 * The scene clear goes LAST, and that is not cosmetic. These stores notify
 * synchronously, and a post-processing panel that is still mounted answers a
 * notification by re-applying its layers — `resetPpView` hands it a new hidden
 * set, which is exactly the change it redraws for. Clearing the group before
 * that runs measured as a group that was still holding the previous project's
 * 137 577-vertex surface a moment later. Emptying it after every store has
 * settled gives the clear the last word.
 */
export function workspaceResetSteps(win: WorkspaceResetWindow): WorkspaceResetStep[] {
  return [
    // `exitEditor(false)` — close WITHOUT applying, as the legacy does. A load
    // is not the moment to commit a footprint the user was still drawing.
    { name: 'room2d', run: () => exitRoom2dEditor(false) },
    // The patch list, the hidden set and the three pending drags (seed, ring,
    // AI box). Patch names repeat across cases, so a hidden set carried over
    // would hide a patch of the new run that happens to share a name.
    { name: 'postprocessView', run: () => resetPpView() },
    // `_ppTempUnit = 'C'` at the end of `ppClearAll`.
    { name: 'temperatureUnit', run: () => setTempUnit('C') },
    {
      // `mpClose()`: drop the preview group AND undo the fade it put on the
      // geometry, or the new project renders at 30% with nothing over it.
      //
      // The session, not just the scene. Since the preview outlives the panel
      // that loaded it (`meshPreviewSession`), a load has to end it too: the
      // decoded mesh, the patch table, the hidden set and the fold state all
      // describe the case being closed, and its patch names would toggle
      // boundaries the next case's `polyMesh/boundary` does not have.
      name: 'meshPreview',
      run: () => {
        resetMeshPreviewSession()
        win.__attachMeshPreview?.(null)
        win.__setGeometryOpacity?.(1)
      },
    },
    {
      // `_removeBmGrid()`. The grid is planned from the OLD project's bounds,
      // so it is wrong the moment the room changes.
      name: 'blockMeshGrid',
      run: () => {
        const visuals = getMeshVisuals()
        if (visuals?.gridShown()) visuals.toggleGrid()
      },
    },
    // The warnings describe the mesh of the project being closed.
    { name: 'meshWarnings', run: () => setMeshWarnings([]) },
    // `_smDestroyMonitorWindow()` plus the `_solverMonitor` / `_smHiddenSeries`
    // reset that follows it.
    { name: 'solverMonitor', run: () => resetSolverMonitor() },
    { name: 'sensorResults', run: () => resetSensorResults() },
    {
      name: 'liquid',
      run: () => {
        // The results and the run selector belong to the closed project. The
        // run state itself is only dropped when nothing is in flight: dropping
        // it mid-solve would orphan the AbortController and leave the stop
        // button with nothing to stop.
        if (!liquidRunState().running) resetLiquidRun()
        setSelectedLiquidRun('')
        resetLiquidMonitorSelection()
      },
    },
    // The DAE run session (solver tab, live monitor, exports, hybrid handoff)
    // belongs to the closed project. The SERVER keeps the run: a finished one
    // is listed and restorable from disk, a running one keeps running there.
    { name: 'liquidRuntime', run: () => resetRuntimeClient() },
    // The temperature overlays paint meshes of the closed project and their
    // switches would otherwise come back on for the next one's first result.
    { name: 'liquidTempColoring', run: () => resetTempColoring() },
    // A rack detail view targets a uid that the next project will not have.
    { name: 'rackDetail', run: () => exitRackDetail() },
    // The seed helper points at a face of a model that is about to be gone.
    { name: 'seedSurface', run: () => setActiveSeedSurface(null) },
    // Leaving the STL preview mode on would draw the NEW project as export
    // regions, which is not what a freshly opened file should look like.
    { name: 'stlPreview', run: () => setPreviewActive(false) },

    // ── last, once nothing can redraw into the group ──────────────────────
    { name: 'postprocessScene', run: () => win.__ppViewport?.clear?.() },
    // The overlay is added to the group by the patch list, not by the layer
    // builder, so `clear()` does not know about it and leaves it behind.
    { name: 'postprocessPatchHighlight', run: () => clearPpHighlight() },
    {
      name: 'postprocessDisplay',
      run: () => {
        const vp = win.__ppViewport
        vp?.setModelOpacity?.(POSTPROCESS_DISPLAY_DEFAULTS.modelOpacity)
        vp?.setFieldOpacity?.(POSTPROCESS_DISPLAY_DEFAULTS.fieldOpacity)
        // Lighting is the user's (ui/lightingState), not the project's: a new
        // project opens in the light the user last chose.
        vp?.setAO?.(POSTPROCESS_DISPLAY_DEFAULTS.ao)
      },
    },
  ]
}

/** Run the steps, keeping going past one that throws. */
export function runWorkspaceReset(steps: WorkspaceResetStep[]): WorkspaceResetReport {
  const report: WorkspaceResetReport = { ran: [], failed: [] }
  for (const step of steps) {
    try {
      step.run()
      report.ran.push(step.name)
    } catch (err) {
      report.failed.push({ name: step.name, error: err instanceof Error ? err.message : String(err) })
    }
  }
  return report
}

/**
 * Empty the workspace. Call BEFORE the new project reaches the store.
 *
 * Before, not after: `setPreviewActive` and the 2D editor's exit both read the
 * open project, and running them against the incoming one would tear down the
 * wrong thing — and, in the preview's case, rebuild the new scene twice.
 */
export function resetWorkspace(
  win: WorkspaceResetWindow = typeof window === 'undefined'
    ? {}
    : (window as unknown as WorkspaceResetWindow),
): WorkspaceResetReport {
  const report = runWorkspaceReset(workspaceResetSteps(win))
  // The same verification seam the viewport's `stats()` is: a reset leaves no
  // trace of its own on screen, so without the report the only way to tell a
  // reset that ran from one that was never called is to guess from the
  // symptoms it was supposed to remove.
  ;(win as { __lastWorkspaceReset?: WorkspaceResetReport }).__lastWorkspaceReset = report
  return report
}
