/**
 * Redrawing the liquid pipes after something moved them.
 *
 * Pipes are not built by the geometry builder. `js/liquid_pipe_renderer.js`
 * sweeps its own tubes into its own group from the project's waypoints, so a
 * live drag that mutates those waypoints changes nothing on screen until the
 * renderer is asked to run again — which is what the legacy does on every drag
 * frame (`applyPipe`, STL_generate.html:15925).
 *
 * Read off `window` rather than imported from `legacy/liquidScene`: these ARE
 * globals — the legacy modules publish themselves there and `liquidScene` only
 * reads the same two names — and importing that module would drag react-dom
 * and the post-processing window into every unit test that touches a gizmo.
 */

interface PipeRendererApi {
  updatePipeGeometry(): void
}

interface LiquidModeApi {
  renderValves(): void
}

export function redrawPipes(): void {
  const w = window as unknown as {
    _liquidPipeRenderer?: PipeRendererApi
    _liquidMode?: LiquidModeApi
  }
  w._liquidPipeRenderer?.updatePipeGeometry()
  // Valves are drawn as their own meshes at their own world positions, so they
  // do not follow the tube they sit on.
  w._liquidMode?.renderValves()
}
