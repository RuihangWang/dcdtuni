import type { TranslationKey } from '../i18n'

/**
 * The refinement grid's column headings and per-row tooltips.
 *
 * The three numbers in a refinement row are NOT interchangeable: the first two
 * are the surface level's [min, max] pair and the third is the feature-line
 * level, and a value typed into the wrong box changes a different part of
 * `snappyHexMeshDict`. Without the headings the legacy grid carries
 * (STL_generate.html:190) the boxes are three identical spinners.
 *
 * The tooltips name the STL each row governs, which is the other half of the
 * same problem: the row labelled "baffle/floor" writes `wallBafflesRefinementLevel`
 * and `baffleFeatureLevel`, NOT the `floor*` keys — so the file it actually
 * refines is worth stating rather than inferring from the label.
 *
 * Kept out of `meshConfig.ts` because that module is the mesh CONFIG — which
 * keys exist and where they live — while this is presentation for one panel.
 */

export const REFINEMENT_COLUMNS: TranslationKey[] = [
  'label.surface_min',
  'label.surface_max',
  'label.feature_line',
]

/**
 * Tooltip per row id, matching `REFINEMENT_ROWS`.
 *
 * `partitionWall` and `rack` have none: the legacy gives both a raw Chinese
 * `title` with no `data-i18n-title`, so there is no shared key to reuse and
 * inventing one would put a string in only this app's dictionary.
 */
export const REFINEMENT_TOOLTIPS: Record<string, TranslationKey> = {
  room: 'tooltip.refine_room_wall',
  acu: 'tooltip.refine_acu',
  box: 'tooltip.refine_box',
  baffle: 'tooltip.refine_baffle_floor',
}
