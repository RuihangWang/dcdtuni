/**
 * The stable identity of a cold plate / compute unit pair.
 *
 * A server's cold plates are ONE list: `liquidCooling.cpCuPairs`. The React
 * inspector, the parameter registry (`cp.heat` / `cp.dT`), the timeline and the
 * legacy HTML editor all write it, and `liquid_graphgen.js` compiles it. The
 * v3→v4 migration (`cfdproj_migrate.js` `_migrateRack`) also copied each pair
 * into `constructions.coldPlates`, keyed `pair.id ?? pair.name ?? 'cp_<i>'`;
 * nothing writes that copy afterwards, so it goes stale the moment a pair is
 * added, deleted or edited.
 *
 * What the copy did give is a uid, and the compiled edge is named after it
 * (`<rack>_<server>_cp_<uid>`), so a plate keeps its edge when another one is
 * inserted before it. Here that uid moves onto the pair itself: a pair without
 * one takes the key of the plate it was migrated into; a pair with one is never
 * matched against coldPlates again. A pair with no plate (a server the editor
 * created, or a pair added after the migration) gets a fresh uid no other pair
 * or plate on the server uses.
 *
 * Pure and dependency-free, so the store, the graph compiler and the research
 * CLI (`tools/compile_cfdproj_liquidrun_v3_entry.ts`) all run the same code.
 */

type Bag = Record<string, unknown>

function hasUid(pair: Bag): boolean {
  return (typeof pair.uid === 'string' && pair.uid !== '') || typeof pair.uid === 'number'
}

function pairsOf(server: Bag): Bag[] {
  const pairs = (server.liquidCooling as { cpCuPairs?: unknown } | undefined)?.cpCuPairs
  return Array.isArray(pairs) ? (pairs as Bag[]) : []
}

/** `base` if the server does not use it yet, else `base_2`, `base_3`, … */
function freeUid(base: string, taken: Set<string>): string {
  let uid = base
  for (let n = 2; taken.has(uid); n++) uid = `${base}_${n}`
  return uid
}

/**
 * The uid for a pair appended to `pairs`: `CP-CU_<k>` with the smallest k from
 * the new pair's own position up that no pair on the server uses as a uid or a
 * name. After a deletion the positional name is often still taken (delete
 * CP-CU_1 of two, the survivor is CP-CU_2), which is why k climbs.
 */
export function newPairUid(pairs: readonly Bag[]): string {
  const taken = new Set(pairs.flatMap((p) => [p.uid, p.name]).filter((v) => v != null).map(String))
  let k = pairs.length + 1
  while (taken.has(`CP-CU_${k}`)) k++
  return `CP-CU_${k}`
}

/**
 * Give every pair on one server a `uid`.
 *
 * A server whose pairs all carry one is left untouched, so this is a no-op on
 * anything stamped before. Otherwise, first every pair that has a uid claims
 * the plate of that uid; then each remaining pair claims the unclaimed plate
 * the migrator would have keyed it by (`id ?? name ?? 'cp_<i>'`), each plate at
 * most once — the legacy editor names a new pair by position, so two pairs can
 * share a name; last, a pair that found no plate gets a fresh uid that no pair
 * or plate on the server uses. No value is copied from a plate: the pair is
 * what every writer has been editing.
 *
 * Deliberately NOT run when a project is opened: the project's content hash
 * binds air results and mesh previews (`renderer/windResultProducer.ts`), so
 * writing uids on open would orphan every result of an older file. The
 * compiler runs it on its private snapshot instead, and the inspector runs it
 * as part of the first pair edit on a server — the edit changes the hash
 * anyway, and from then on a rename cannot move the plate's edge.
 */
export function backfillServerPairUids(server: Bag): void {
  if (!server || typeof server !== 'object') return
  const pairs = pairsOf(server)
  if (pairs.every(hasUid)) return
  const plates = ((server.constructions as { coldPlates?: unknown } | undefined)?.coldPlates ?? {}) as Record<string, Bag>
  const plateUid = new Map<string, string>()
  for (const [key, plate] of Object.entries(plates)) {
    if (plate && typeof plate === 'object') plateUid.set(key, String(plate.uid || key))
  }
  const taken = new Set<string>([...plateUid.keys(), ...plateUid.values()])
  const claimed = new Set<string>(pairs.filter(hasUid).map((p) => String(p.uid)))
  const uids: (string | null)[] = pairs.map((p) => (hasUid(p) ? String(p.uid) : null))
  pairs.forEach((pair, i) => {
    if (uids[i] != null) return
    const key = pair.id != null ? String(pair.id) : pair.name != null ? String(pair.name) : `cp_${i}`
    const uid = plateUid.get(key)
    if (uid == null || claimed.has(uid)) return
    claimed.add(uid)
    uids[i] = uid
  })
  for (const uid of uids) if (uid != null) taken.add(uid)
  pairs.forEach((pair, i) => {
    if (uids[i] != null) return
    const base = pair.name != null && pair.name !== '' ? String(pair.name) : `CP-CU_${i + 1}`
    uids[i] = freeUid(base, taken)
    taken.add(uids[i]!)
  })
  server.liquidCooling = {
    ...(server.liquidCooling as Bag),
    cpCuPairs: pairs.map((pair, i) => {
      if (hasUid(pair)) return pair
      const { uid: _unset, ...rest } = pair
      return { uid: uids[i], ...rest }
    }),
  }
}

/** `backfillServerPairUids` for every server in a rack. */
export function backfillPairUids(rack: Bag): void {
  const servers = (rack.constructions as { servers?: Record<string, unknown> } | undefined)?.servers
  if (!servers || typeof servers !== 'object') return
  for (const server of Object.values(servers) as Bag[]) backfillServerPairUids(server)
}
