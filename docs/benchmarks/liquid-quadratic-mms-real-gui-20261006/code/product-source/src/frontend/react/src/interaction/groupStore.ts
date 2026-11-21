/**
 * Where the model groups live, and how they survive a reload.
 *
 * Groups are NOT project data. `stateForSaveV4` (STL_generate.html:14888)
 * writes nine keys and `ui` is not one of them, so the legacy persists them in
 * `localStorage['cfdproj.ui.<uid>']` beside the other view preferences
 * (`UI_PREF_KEYS`, :3247). This module keeps the same key and the same JSON
 * shape, so the two editors read and write the same blob — the group a user
 * made in one app is the group the other app shows.
 *
 * The blob holds more than groups (`treeCollapsed`, `previewVisibility`, …), so
 * every write is a read-modify-write. Replacing it wholesale would wipe the
 * view preferences the other app had just saved into it.
 *
 * Groups are pruned against the live project rather than swept on delete: a
 * model can leave the project by an undo, a batch delete or a fresh load, and a
 * hook on each of those is three chances to forget one.
 */

import { allModels, type CfdProject } from '../schema/cfdproj'
import {
  addToGroup,
  createGroup,
  dissolveGroup,
  type ModelGroup,
  newGroupId,
  normalizeGroups,
  pruneGroups,
  removeFromGroup,
  renameGroup,
  sameGroups,
} from '../geometry/modelGroups'
import * as store from '../projectStore'

/** The legacy `_uiPrefsStorageKey` (STL_generate.html:3250). */
export function uiPrefsKey(projectUid: string | null | undefined): string {
  return `cfdproj.ui.${projectUid || 'default'}`
}

export function readGroupsFromStorage(projectUid: string | null | undefined): unknown {
  try {
    const raw = localStorage.getItem(uiPrefsKey(projectUid))
    if (!raw) return []
    const parsed = JSON.parse(raw) as Record<string, unknown> | null
    return parsed && typeof parsed === 'object' ? parsed.groups : []
  } catch {
    return []
  }
}

export function writeGroupsToStorage(
  projectUid: string | null | undefined,
  groups: ModelGroup[],
): void {
  try {
    const key = uiPrefsKey(projectUid)
    const raw = localStorage.getItem(key)
    let blob: Record<string, unknown> = {}
    if (raw) {
      const parsed = JSON.parse(raw) as Record<string, unknown> | null
      if (parsed && typeof parsed === 'object') blob = parsed
    }
    blob.groups = groups
    localStorage.setItem(key, JSON.stringify(blob))
  } catch {
    /* private mode / headless: the groups just do not outlive the session */
  }
}

let groups: ModelGroup[] = []
let boundUid: string | null = null
let bound = false

type Listener = () => void
const listeners = new Set<Listener>()

function emit() {
  listeners.forEach((fn) => fn())
}

export function subscribeGroups(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getGroups(): ModelGroup[] {
  return groups
}

/** Test seam: drop the cached list so the next sync re-reads storage. */
export function resetGroupsForTest() {
  groups = []
  boundUid = null
}

function modelIds(project: CfdProject | null): Set<string> {
  if (!project) return new Set()
  return new Set(allModels(project).map((m) => m.model.uid))
}

/**
 * Re-read or re-check the groups against the project.
 *
 * A different project uid means a different stored list. The same uid means the
 * list stands, minus any model that has since left the project — and only a
 * change is written back, or every store notification would rewrite storage.
 */
export function syncGroups(project: CfdProject | null = store.getProject()) {
  const uid = project?.uid ?? null
  const valid = modelIds(project)
  if (uid !== boundUid) {
    boundUid = uid
    groups = normalizeGroups(readGroupsFromStorage(uid), valid)
    emit()
    return
  }
  const pruned = pruneGroups(groups, valid)
  if (sameGroups(pruned, groups)) return
  groups = pruned
  writeGroupsToStorage(boundUid, groups)
  emit()
}

/** Subscribe to the project store once, however many panels import this. */
export function bindGroupsToProject() {
  if (bound) return
  bound = true
  store.subscribe(() => syncGroups())
  syncGroups()
}

function commit(next: ModelGroup[], action: string, detail: string) {
  if (sameGroups(next, groups)) return
  groups = next
  writeGroupsToStorage(boundUid, groups)
  // Logged, not `edit`ed: a group is not project data, so an undo entry here
  // would restore an identical project and read as a broken undo.
  store.logOperation(action, detail)
  emit()
}

export function createModelGroup(name: string, memberIds: string[]): string {
  const id = newGroupId()
  commit(createGroup(groups, name, memberIds, id), 'createGroup', `${name} (${memberIds.length})`)
  return id
}

export function dissolveModelGroup(groupId: string) {
  const name = groups.find((g) => g.id === groupId)?.name ?? groupId
  commit(dissolveGroup(groups, groupId), 'dissolveGroup', name)
}

export function removeModelFromGroup(modelId: string) {
  commit(removeFromGroup(groups, modelId), 'removeFromGroup', modelId)
}

export function addModelsToGroup(groupId: string, modelIds: string[]) {
  const name = groups.find((g) => g.id === groupId)?.name ?? groupId
  commit(addToGroup(groups, groupId, modelIds), 'addToGroup', `${name} (${modelIds.length})`)
}

export function renameModelGroup(groupId: string, name: string) {
  commit(renameGroup(groups, groupId, name), 'renameGroup', name)
}
