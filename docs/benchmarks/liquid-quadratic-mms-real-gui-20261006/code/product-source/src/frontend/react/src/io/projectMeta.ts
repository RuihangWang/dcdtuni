/**
 * Project-level metadata edits that are not geometry.
 *
 * A separate module from `projectStore.ts` on purpose: the store owns the undo
 * machinery and the construction helpers, and every panel that needs a new
 * field otherwise grows it there until nobody can see what the store is for.
 * This one reaches the project through the store's `edit()`, so the checkpoint
 * is taken the same way as for any other mutation.
 */

import type { CfdProject } from '../schema/cfdproj'
import { edit } from '../projectStore'

/**
 * The name shown in the inspector and offered as the save filename.
 *
 * Lives in `meta.projectName`, NOT in the document's root `name`. The root field
 * is written once by the v3→v4 migrator (`cfdproj_migrate.js:255`) and the
 * legacy `setProjectName` (STL_generate.html:13242) never touches it again — so
 * writing both here would make the two editors save different bytes for the same
 * edit, which is the one thing the parity harness is there to catch.
 */
export function projectName(project: CfdProject | null): string {
  const meta = project?.meta as Record<string, unknown> | undefined
  const value = meta?.projectName
  return typeof value === 'string' ? value : ''
}

export function setProjectName(value: string) {
  const next = (value || '').trim()
  edit('setProjectName', `Set project name: ${next || '(empty)'}`, (draft: CfdProject) => {
    const meta = (draft.meta ?? (draft.meta = {})) as Record<string, unknown>
    meta.projectName = next
  })
}
