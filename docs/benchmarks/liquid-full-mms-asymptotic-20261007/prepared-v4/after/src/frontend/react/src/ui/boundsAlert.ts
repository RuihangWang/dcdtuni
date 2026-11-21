import { t } from '../i18n'

/**
 * Tell the user a typed geometry edit was refused for leaving the bounds.
 *
 * The refusal itself lives in `projectStore` — every writer has to obey it,
 * including the automation contract — but the store must not `alert`: it is
 * imported by every unit test and driven by `window.__dcdtuni`, and a blocking
 * dialog in a headless build would hang a scripted run rather than fail it. So
 * the store returns `false` and logs, and the PANELS say so.
 *
 * The legacy alerts from `updateModel` itself (STL_generate.html:14022) because
 * there is no script path there to hang.
 */
export function alertIfOutOfBounds(accepted: boolean): boolean {
  if (accepted) return true
  if (typeof window !== 'undefined' && typeof window.alert === 'function') {
    window.alert(t('error.undo_out_of_bounds'))
  }
  return false
}
