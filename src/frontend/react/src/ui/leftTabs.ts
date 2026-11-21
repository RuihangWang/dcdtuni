/**
 * Which left-panel tabs exist, what they are called, and when they are locked.
 *
 * Two rules live here and they pull in opposite directions, which is why they
 * are one module rather than an `if` in the tab bar:
 *
 *   - AIR gates the mesh tab on a usable backend and post-processing on a
 *     result (`_updateMeshTabEnabled` / `_updatePostprocessTabEnabled`,
 *     STL_generate.html:19485-19541). The solver tab is always open; the solve
 *     button needs a mesh on disk, never an accepted quality verdict.
 *   - LIQUID has no such steps. `_applyLeftTabBarForMode` (:19275) hides mesh
 *     and system outright while keeping the shared solver label, and both enable-checks
 *     early-return `disabled = false` in liquid mode. There is no OpenFOAM mesh
 *     to wait for, so the air gates would lock the liquid workflow out of its
 *     own solver for ever.
 */

import type { TranslationKey } from '../i18n'

export type LeftTab = 'geometry' | 'mesh' | 'solver' | 'postprocess' | 'system'

export type CoolingMode = 'air' | 'liquid'

export interface LeftTabSpec {
  id: LeftTab
  labelKey: TranslationKey
}

export const LEFT_TABS: LeftTabSpec[] = [
  { id: 'geometry', labelKey: 'nav.geometry' },
  { id: 'mesh', labelKey: 'nav.mesh' },
  { id: 'solver', labelKey: 'nav.solver' },
  { id: 'postprocess', labelKey: 'nav.postprocess' },
  { id: 'system', labelKey: 'nav.system' },
]

/**
 * Tabs liquid mode removes from the bar.
 *
 * Not merely disabled: an OpenFOAM mesh and the backend/system page are air
 * concepts, and offering them greyed out reads as "not yet" rather than "never
 * in this mode".
 */
const LIQUID_HIDDEN: LeftTab[] = ['mesh', 'system']

/**
 * Tabs disabled until the session is validated (`mesh`) or has results
 * (`postprocess`).
 *
 * The solver tab is never locked (owner rule 2026-10-22): its settings can be
 * edited before any mesh exists, and the solve itself needs only a mesh on
 * disk, not an accepted checkMesh verdict -- the solve button says so.
 */
const NEEDS_VALIDATION: LeftTab[] = ['mesh', 'postprocess']
const NEEDS_SOLVE: LeftTab[] = ['postprocess']

/** Tabs liquid mode ungates outright, matching the two early returns. */
const LIQUID_UNGATED: LeftTab[] = ['solver', 'postprocess']

/**
 * Whether a tab is off the bar in this mode.
 *
 * The button is still RENDERED and still carries its id — the legacy hides it
 * with `style.display = 'none'` rather than removing it, and a parity check
 * that looks up `#tab-btn-mesh` has to find the same thing in both apps.
 */
export function isLeftTabHidden(tab: LeftTab, mode: CoolingMode): boolean {
  return mode === 'liquid' && LIQUID_HIDDEN.includes(tab)
}

export function visibleLeftTabs(mode: CoolingMode): LeftTabSpec[] {
  return LEFT_TABS.filter((tab) => !isLeftTabHidden(tab.id, mode))
}

/**
 * Liquid mode names its two run tabs for what they hold — 液冷求解 /
 * 液冷后处理 — so a screenshot or a sentence in the docs says which solver
 * it is about. The keys are the legacy dictionary's own.
 */
const LIQUID_LABELS: Partial<Record<LeftTab, { full: TranslationKey; compact: TranslationKey }>> = {
  // Two words each, so 液冷求解 and 液冷结果 read as a pair under their icons.
  solver: { full: 'nav.liquid_solver', compact: 'lq_ux.nav_compact_liquid_solver' },
  postprocess: { full: 'nav.liquid_postprocess', compact: 'lq_ux.nav_compact_liquid_postprocess' },
}

export function leftTabLabelKey(tab: LeftTab, mode: CoolingMode): TranslationKey {
  if (mode === 'liquid') {
    const liquid = LIQUID_LABELS[tab]
    if (liquid) return liquid.full
  }
  const spec = LEFT_TABS.find((s) => s.id === tab)
  return spec ? spec.labelKey : 'nav.geometry'
}

/** The short label under the tab icon. */
export function leftTabCompactKey(tab: LeftTab, mode: CoolingMode): TranslationKey {
  if (mode === 'liquid') {
    const liquid = LIQUID_LABELS[tab]
    if (liquid) return liquid.compact
  }
  return `nav.compact.${tab}` as TranslationKey
}

/** Why a tab is locked, or null when it is not. The value names the missing step. */
export type LeftTabGate = 'validate' | 'mesh' | 'solve' | null

export interface SessionProgress {
  validated: boolean
  meshReady: boolean
  solved: boolean
}

export function leftTabGate(
  tab: LeftTab,
  mode: CoolingMode,
  progress: SessionProgress,
): LeftTabGate {
  if (mode === 'liquid' && LIQUID_UNGATED.includes(tab)) return null
  if (NEEDS_VALIDATION.includes(tab) && !progress.validated) return 'validate'
  if (NEEDS_SOLVE.includes(tab) && !progress.solved) return 'solve'
  return null
}

/**
 * The tab to show when `tab` is not on the bar in this mode.
 *
 * Switching to liquid while the mesh tab is open otherwise leaves the panel
 * showing a mesh page whose button has just disappeared, with no way back to it
 * — the legacy rewrites `activeTab` to 'geometry' for exactly that case.
 */
export function resolveLeftTab(tab: LeftTab, mode: CoolingMode): LeftTab {
  return visibleLeftTabs(mode).some((s) => s.id === tab) ? tab : 'geometry'
}
