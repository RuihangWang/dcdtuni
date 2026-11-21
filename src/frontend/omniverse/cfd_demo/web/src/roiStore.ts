/** The current ROI box, shared between the panel that owns it and the panel that uses it.
 *
 *  CfdControls (Post-processing, right panel) owns the box: number inputs, Reset, and the
 *  viewport-drag echo all land in its local state. AiPrediction (left panel) needs to read
 *  that box to ask for an ROI-only export. The two live in different subtrees, so the
 *  choice is prop-drilling through App -> LeftPanel/PostProcessing or a module-level store.
 *  A store, because the box is genuinely one piece of viewport state with one writer, and
 *  threading it through four components that do not care about it invites the copies to
 *  drift.
 *
 *  Coordinates are the DEMO world frame: metres, Y-up, the frame the Kit scene manifest
 *  reports and the ROI box is drawn in. They are handed to the worker unconverted — the
 *  solver-frame conversion happens once, in the exporter, which is the only place that
 *  knows the full domain's depth.
 */

export type RoiBox = {
  min: [number, number, number];
  max: [number, number, number];
};

let current: RoiBox | null = null;
const listeners = new Set<(b: RoiBox | null) => void>();

export function setRoiSelection(min: [number, number, number], max: [number, number, number]) {
  current = { min: [...min], max: [...max] };
  listeners.forEach((fn) => fn(current));
}

export function getRoiSelection(): RoiBox | null {
  return current ? { min: [...current.min], max: [...current.max] } : null;
}

export function subscribeRoi(fn: (b: RoiBox | null) => void): () => void {
  listeners.add(fn);
  fn(current);
  return () => {
    listeners.delete(fn);
  };
}
