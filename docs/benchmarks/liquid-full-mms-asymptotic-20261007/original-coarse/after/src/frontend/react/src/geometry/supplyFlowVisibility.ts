/**
 * Where the wall-ACU supply streams (the shared js/supply_flow_anim.js) may show.
 *
 * A geometry-tab helper, following the rule that a scene helper is visible on
 * its owning tab and nowhere else (CLAUDE.md, frontend rule 7) — the gate the
 * anchor markers and the move gizmo already use. And a ROOM helper: rack detail
 * mode builds one rack at the origin, where streams placed in room coordinates
 * would float with no unit behind them (the viewport clears them there).
 */
export function supplyFlowVisible(activeTab: string, rackDetailTarget: string | null | undefined): boolean {
  return activeTab === 'geometry' && !rackDetailTarget
}
