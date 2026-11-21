/**
 * Grouping boundary patches under the models that produced them.
 *
 * The post-processing patch list is not a flat 53-row list of OpenFOAM names —
 * it is a tree: kind (RACK / ACU / WALL …) → model → the patches that model
 * generated. That mapping does not exist anywhere in the solved result. The
 * server knows only patch names, so the tree is reconstructed by matching those
 * names back against the project's model names.
 *
 * This is a faithful port of `_groupPatchesByModelKind` (STL_generate.html:
 * 21169). The match ORDER is the part that matters and is preserved exactly:
 *
 *   1. exact model name, or name + '_' prefix
 *   2. a `merged_*` name, trying each suffix from the second segment on
 *   3. a `_master` / `_slave` baffle pair, stripped and re-matched
 *   4. a dcCart mesh-box side (`domain_xMin` … `domain_zMax`) goes to WALL,
 *      under the fixed group `Domain boundary`
 *   5. anything left over goes to OTHER
 *
 * Longest model name first, so `Rack_10` never gets swallowed by `Rack_1`.
 * Reordering these rules silently reparents patches: every one of them can
 * match a name another rule would also match, and the tree still looks
 * plausible when it is wrong.
 */

import { stlNaming } from '../legacy/stlNaming'

export interface PatchLike {
  name: string
  nTriangles: number
  defaultVisible?: boolean
}

export interface ModelLike {
  kind?: string
  meta?: { name?: string }
  /** A wall or floor that closes the room: the enclosure every mesh must contain. */
  isRoomBoundary?: boolean
}

/**
 * The group, inside WALL, that dcCartMesh's bounding-box sides are filed under.
 *
 * A fixed key rather than a translated string: it is also the collapse-state
 * key and the `data-model` attribute, neither of which may change with the
 * locale. Panels show `patchGroupLabel(...)`.
 */
export const DOMAIN_BOUNDARY_GROUP = 'Domain boundary'

/** dcCartMesh names the six faces of its mesh box `domain_<x|y|z><Min|Max>`. */
export const DOMAIN_PATCH_RE = /^domain_(x|y|z)(Min|Max)$/

/** A model-group header's text: the translated label for the domain group, else the model name. */
export function patchGroupLabel(groupName: string, domainLabel: string): string {
  return groupName === DOMAIN_BOUNDARY_GROUP ? domainLabel : groupName
}

export const KIND_COLORS: Record<string, string> = {
  rack: '#5aa7ff',
  acu: '#98c379',
  cdu: '#00b4dc',
  box: '#e5c07b',
  wall: '#888',
  baffle: '#b08040',
  partitionWall: '#c98cff',
  floor: '#6a8c6a',
}

export const KIND_LABELS: Record<string, string> = {
  rack: 'RACK',
  acu: 'ACU',
  cdu: 'CDU',
  box: 'BOX',
  wall: 'WALL',
  baffle: 'BAFFLE',
  partitionWall: 'PARTITION',
  floor: 'FLOOR',
}

export const KIND_ORDER = [
  'rack',
  'acu',
  'cdu',
  'box',
  'wall',
  'baffle',
  'partitionWall',
  'floor',
  '_other',
]

/** { kind: { modelName: [patch, …] } }, in the legacy's shape. */
export type PatchGroups = Record<string, Record<string, PatchLike[]>>

function push(groups: PatchGroups, kind: string, model: string, patch: PatchLike) {
  const byModel = (groups[kind] ??= {})
  ;(byModel[model] ??= []).push(patch)
}

export function groupPatchesByModelKind(
  patches: readonly PatchLike[],
  models: readonly ModelLike[],
): PatchGroups {
  const groups: PatchGroups = {}
  // Longest first: 'Default_Rack_1' must win over 'Default_Rack' for a patch
  // named 'Default_Rack_1_inlet', which BOTH rules 1 and 2 would accept.
  const sorted = models
    .filter((m) => !!m.meta?.name)
    .slice()
    .sort((a, b) => (b.meta?.name?.length ?? 0) - (a.meta?.name?.length ?? 0))

  const matchModel = (name: string): ModelLike | null => {
    for (const m of sorted) {
      const mn = m.meta?.name as string
      if (name === mn || name.startsWith(mn + '_')) return m
    }
    return null
  }

  for (const p of patches) {
    let matched = false

    const direct = matchModel(p.name)
    if (direct) {
      push(groups, direct.kind ?? '_other', direct.meta?.name as string, p)
      matched = true
    }

    // A merged patch carries the model name somewhere after the first segment:
    // 'merged_wall_Room_north' → try 'wall_Room_north', then 'Room_north', …
    if (!matched && p.name.startsWith('merged_')) {
      const parts = p.name.split('_')
      for (let si = 1; si < parts.length && !matched; si++) {
        const suffix = parts.slice(si).join('_')
        const m = matchModel(suffix)
        if (m) {
          push(groups, m.kind ?? '_other', m.meta?.name as string, p)
          matched = true
        }
      }
    }

    // createBaffles splits one faceZone into a cyclic master/slave pair. Both
    // halves belong to the model that owns the zone, so strip the suffix and
    // re-run the model match.
    if (!matched && (p.name.endsWith('_master') || p.name.endsWith('_slave'))) {
      const baseName = p.name.replace(/_(master|slave)$/, '')
      const m = matchModel(baseName)
      if (m) {
        push(groups, m.kind ?? '_other', m.meta?.name as string, p)
        matched = true
      }
      if (!matched && baseName === 'merged_wall_baffle') {
        push(groups, 'baffle', 'Wall Baffle', p)
        matched = true
      }
      // Blanking panels merge ACROSS racks into one patch per U-span, so the
      // patch has no single owner. With exactly one rack in the room the owner
      // is unambiguous; with several it gets its own BAFFLE bucket rather than
      // being attributed to an arbitrary rack.
      if (
        !matched &&
        (baseName.startsWith('merged_wallBlankingPanel_') ||
          baseName.startsWith('merged_perforatedBlankingPanel_'))
      ) {
        const racks = sorted.filter((m2) => m2.kind === 'rack')
        if (racks.length === 1) {
          push(groups, 'rack', racks[0]?.meta?.name as string, p)
        } else {
          push(groups, 'baffle', 'Blanking Panel', p)
        }
        matched = true
      }
    }

    // After every model rule, so a model really named domain_xMin keeps its
    // patch. This only files the sides: on a leaked mesh whose room walls are
    // gone it gives WALL a head too, which is what `missingEnclosurePatches`
    // exists to point out.
    if (!matched && DOMAIN_PATCH_RE.test(p.name)) {
      push(groups, 'wall', DOMAIN_BOUNDARY_GROUP, p)
      matched = true
    }

    if (!matched) push(groups, '_other', 'Other', p)
  }

  return groups
}

function sanitizeName(text: string): string {
  try {
    return stlNaming().sanitizeName(text)
  } catch {
    // The shared module attaches to `window` on import; this keeps the helper
    // usable from a context that never loaded it.
    return (text || 'model').replace(/[^a-zA-Z0-9_]+/g, '_')
  }
}

const ENCLOSURE_KINDS = new Set(['wall', 'floor'])

/**
 * The room-enclosure patches a LEAKED mesh does not have.
 *
 * When the room shell is open (Base604 on 2026-09-10: walls 3.5 m under a
 * 4.4 m ceiling) the fluid flows over the walls to the mesh box. dcCart then
 * files the box's faces as `domain_*` and gives the room walls, floor and
 * ceiling no patch at all, because a carving surface with fluid on both sides
 * has no boundary face. The grouping above puts `domain_*` under WALL, so
 * without this the list shows a WALL head and looks like a normal mesh.
 *
 * Reported only with evidence of a leak: a `domain_*` patch holding faces. A
 * missing name alone proves nothing, since the project may have changed since
 * the mesh was built and a snappy mesh has no domain sides.
 *
 * A room-boundary wall or floor is present when some patch is its name, raw or
 * sanitised, or starts with that name + '_' (its patch regions, and the
 * `_master`/`_slave` pair createBaffles makes). A missing one is reported by
 * its sanitised name, the name its patch would have had. Same rule as the
 * legacy `_missingEnclosurePatchNames`.
 */
export function missingEnclosurePatches(
  patches: readonly PatchLike[],
  models: readonly ModelLike[],
): string[] {
  const leaked = patches.some((p) => DOMAIN_PATCH_RE.test(p.name) && p.nTriangles > 0)
  if (!leaked) return []
  const names = patches.map((p) => p.name)
  const missing: string[] = []
  for (const m of models) {
    const name = m.meta?.name
    if (!name || m.isRoomBoundary !== true || !ENCLOSURE_KINDS.has(m.kind ?? '')) continue
    const stems = [name, sanitizeName(name)]
    const present = names.some((n) => stems.some((s) => n === s || n.startsWith(s + '_')))
    if (!present) missing.push(sanitizeName(name))
  }
  return missing
}

/** The kinds present, in the panel's fixed order. */
export function orderedKinds(groups: PatchGroups): string[] {
  return KIND_ORDER.filter((k) => groups[k])
}

/** Patch count under a kind, across all its models. */
export function kindPatchCount(groups: PatchGroups, kind: string): number {
  return Object.values(groups[kind] ?? {}).reduce((n, list) => n + list.length, 0)
}

/**
 * Whether a kind renders its models as sub-groups.
 *
 * A kind holding ONE model with at most three patches is drawn flat — a
 * collapsible header wrapping two rows is noise. Same rule as the legacy tree.
 */
export function isFlatKind(byModel: Record<string, PatchLike[]>): boolean {
  const names = Object.keys(byModel)
  return names.length === 1 && (byModel[names[0] as string]?.length ?? 0) <= 3
}

/**
 * The per-SERVER level inside a rack's patch group.
 *
 * A rack's patches are its frame plus five or six per server
 * (`SR1_SR1_40_inlet_patch`, `SR1_SR1_40_outlet_p0`, `SR1_SR1_40_top`, …), and
 * a 42U rack lists forty-odd rows flat under one head, which is where the
 * "not classified at all" impression came from (STDCT, 2026-09-22). The
 * server builder names a server patch `<rack>_<server>_<part>` with the
 * server's own name carrying the rack prefix again, so `<rack>_<rack>_<n>_`
 * is the server `<rack>_<n>`. Patches that do not follow it (the frame, a
 * blanking panel) stay directly under the rack, first.
 */
export interface PatchSubgroup {
  /** The server name, or null for the rack's own patches. */
  server: string | null
  patches: PatchLike[]
}

export function serverSubgroups(modelName: string, patches: readonly PatchLike[]): PatchSubgroup[] {
  const own: PatchLike[] = []
  const byServer = new Map<string, PatchLike[]>()
  const re = new RegExp(`^${escapeRe(modelName)}_(${escapeRe(modelName)}_\\d+)_`)
  for (const p of patches) {
    const m = re.exec(p.name)
    if (!m) {
      own.push(p)
      continue
    }
    const key = m[1] as string
    ;(byServer.get(key) ?? byServer.set(key, []).get(key)!).push(p)
  }
  const out: PatchSubgroup[] = []
  if (own.length) out.push({ server: null, patches: own })
  const servers = [...byServer.keys()].sort((a, b) => {
    const na = Number(a.slice(modelName.length + 1))
    const nb = Number(b.slice(modelName.length + 1))
    return Number.isFinite(na) && Number.isFinite(nb) ? nb - na : a.localeCompare(b)
  })
  for (const sv of servers) out.push({ server: sv, patches: byServer.get(sv)! })
  return out
}

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
