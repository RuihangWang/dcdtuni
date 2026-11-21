/**
 * Named model groups — the tree's 📁 folders.
 *
 * A port of the legacy group functions (STL_generate.html:4492-4610). Two facts
 * about where the data lives shape everything here:
 *
 * - **A group is NOT part of the .cfdproj.** The legacy keeps `uiState.groups`,
 *   and `stateForSaveV4` (STL_generate.html:14888) writes only the nine
 *   canonical V4 keys — `ui` is not one of them. Groups are persisted per
 *   project uid in `localStorage['cfdproj.ui.<uid>']` instead
 *   (`UI_PREF_KEYS`, :3247). So a group survives a reload but never reaches the
 *   file, in EITHER app.
 * - **A group of one is not a group.** Every mutation ends by dropping groups
 *   with fewer than two members, which is what stops a group left behind by a
 *   deletion from lingering as a folder around a single rack.
 *
 * The functions are pure and return NEW arrays: the store swaps the array in
 * one assignment, so a subscriber that captured the old one cannot see a
 * half-applied edit.
 */

export interface ModelGroup {
  id: string
  name: string
  memberIds: string[]
}

/** Natural ordering, matching the tree's `_treeCollator` (STL_generate.html:12581). */
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

export function sortGroupsByName(groups: ModelGroup[], descending = false): ModelGroup[] {
  const dir = descending ? -1 : 1
  return [...groups].sort((a, b) => dir * collator.compare(a.name ?? '', b.name ?? ''))
}

/**
 * `grp_<ms>_<4 random chars>`, the legacy id shape (STL_generate.html:4523).
 *
 * The random tail is what keeps two groups made in the same millisecond apart —
 * a "group each of these four rows" run does exactly that.
 */
export function newGroupId(): string {
  return `grp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`
}

/** Drop groups that no longer hold at least two models. */
export function autoDissolve(groups: ModelGroup[]): ModelGroup[] {
  return groups.filter((g) => g.memberIds.length >= 2)
}

export function groupById(groups: ModelGroup[], groupId: string): ModelGroup | null {
  return groups.find((g) => g.id === groupId) ?? null
}

export function groupOf(groups: ModelGroup[], modelId: string): ModelGroup | null {
  return groups.find((g) => g.memberIds.includes(modelId)) ?? null
}

/** Every model id held by any group, for "which rows are already foldered". */
export function groupedIds(groups: ModelGroup[]): Set<string> {
  const out = new Set<string>()
  for (const g of groups) for (const id of g.memberIds) out.add(id)
  return out
}

/**
 * Read a stored group list, keeping only what still exists.
 *
 * The legacy runs the same three filters when a project loads
 * (STL_generate.html:3770-3777): a group needs an id and an array of members,
 * members that name a model no longer in the project are dropped, and a
 * nameless group is called "Group". A stale member id is not hypothetical — the
 * groups outlive the project file, so any model deleted since produces one.
 */
export function normalizeGroups(raw: unknown, validIds: Set<string>): ModelGroup[] {
  if (!Array.isArray(raw)) return []
  const out: ModelGroup[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const g = item as Partial<ModelGroup>
    if (!g.id || !Array.isArray(g.memberIds)) continue
    out.push({
      id: String(g.id),
      name: typeof g.name === 'string' ? g.name : 'Group',
      memberIds: g.memberIds.filter((id) => typeof id === 'string' && validIds.has(id)),
    })
  }
  return autoDissolve(out)
}

/** The same prune, for a project that changed under a live group list. */
export function pruneGroups(groups: ModelGroup[], validIds: Set<string>): ModelGroup[] {
  return autoDissolve(
    groups.map((g) => ({ ...g, memberIds: g.memberIds.filter((id) => validIds.has(id)) })),
  )
}

/** True when two lists hold the same groups with the same members, in order. */
export function sameGroups(a: ModelGroup[], b: ModelGroup[]): boolean {
  if (a.length !== b.length) return false
  return a.every((g, i) => {
    const other = b[i]!
    return (
      g.id === other.id &&
      g.name === other.name &&
      g.memberIds.length === other.memberIds.length &&
      g.memberIds.every((id, j) => id === other.memberIds[j])
    )
  })
}

/**
 * "Rack Group 1", "Rack Group 2", … — the first name not already taken.
 *
 * Ported from `_nextGroupName` (STL_generate.html:4512): the kind is
 * capitalised as-is, so the default name says what the group holds.
 */
export function nextGroupName(groups: ModelGroup[], kind: string): string {
  const prefix = `${kind.charAt(0).toUpperCase()}${kind.slice(1)} Group `
  const taken = new Set(groups.map((g) => g.name))
  for (let n = 1; ; n++) {
    const name = prefix + n
    if (!taken.has(name)) return name
  }
}

/**
 * A new group holding `memberIds`, taken out of whatever group held them.
 *
 * The auto-dissolve runs BEFORE the new group is added, exactly as the legacy
 * does — so a two-model group that just lost one member disappears, while the
 * group being created is never itself dissolved for being small.
 */
export function createGroup(
  groups: ModelGroup[],
  name: string,
  memberIds: string[],
  id: string = newGroupId(),
): ModelGroup[] {
  const taken = new Set(memberIds)
  const next = groups.map((g) => ({
    ...g,
    memberIds: g.memberIds.filter((mid) => !taken.has(mid)),
  }))
  return [...autoDissolve(next), { id, name, memberIds: [...memberIds] }]
}

export function dissolveGroup(groups: ModelGroup[], groupId: string): ModelGroup[] {
  return groups.filter((g) => g.id !== groupId)
}

export function removeFromGroup(groups: ModelGroup[], modelId: string): ModelGroup[] {
  const owner = groupOf(groups, modelId)
  if (!owner) return groups
  return autoDissolve(
    groups.map((g) =>
      g.id === owner.id ? { ...g, memberIds: g.memberIds.filter((id) => id !== modelId) } : g,
    ),
  )
}

/** Move models into an existing group, out of any other group they were in. */
export function addToGroup(
  groups: ModelGroup[],
  groupId: string,
  modelIds: string[],
): ModelGroup[] {
  if (!groupById(groups, groupId)) return groups
  const moving = new Set(modelIds)
  const next = groups.map((g) => {
    if (g.id !== groupId) return { ...g, memberIds: g.memberIds.filter((id) => !moving.has(id)) }
    const members = [...g.memberIds]
    for (const id of modelIds) if (!members.includes(id)) members.push(id)
    return { ...g, memberIds: members }
  })
  return autoDissolve(next)
}

/** Rename in place. A no-op for an unchanged name, so it makes no log entry. */
export function renameGroup(groups: ModelGroup[], groupId: string, name: string): ModelGroup[] {
  const group = groupById(groups, groupId)
  if (!group || group.name === name) return groups
  return groups.map((g) => (g.id === groupId ? { ...g, name } : g))
}

/**
 * The groups a selection could be ADDED to.
 *
 * Ported from the multi-select panel's `_compatGrps` (STL_generate.html:11033):
 * a group qualifies when it holds the same kind of model and does not already
 * contain the whole selection — offering "add to" for a group the models are
 * already all in would do nothing.
 */
export function compatibleGroups(
  groups: ModelGroup[],
  selectedIds: string[],
  kindOf: (uid: string) => string | null,
  selectionKind: string,
): ModelGroup[] {
  return groups.filter((g) => {
    const first = g.memberIds[0]
    if (!first || kindOf(first) !== selectionKind) return false
    return !selectedIds.every((id) => g.memberIds.includes(id))
  })
}
