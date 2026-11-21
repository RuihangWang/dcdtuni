/**
 * The two patch-list modules the legacy page loads (STL_generate.html:1198-1199).
 *
 * Declared here rather than in `legacy-modules.d.ts` so the patch-list port owns
 * its own declarations; the accessors that give them types live next to this
 * file in `patchListModules.ts`.
 */
declare module '*/shared/js/patch_filter.js'
declare module '*/shared/js/patch_selection.js'
