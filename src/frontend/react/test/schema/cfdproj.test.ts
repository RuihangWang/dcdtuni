import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { schema } from '../../src/legacy/cfdprojModules'
import { BUCKETS, SINGLETON_SLOTS, type CfdProject } from '../../src/schema/cfdproj'

/**
 * The .cfdproj types are hand-written TypeScript over a format defined by
 * js/cfdproj_schema.js. Nothing makes them agree except a test that reads REAL
 * projects — every field in schema/cfdproj.ts was read off case/Base_leakage,
 * and a bucket added to the format without a matching type is invisible until
 * something reaches for it and finds `undefined`.
 */
const CASE_DIR = resolve(process.cwd(), '../../../case')

function realProjects(limit = 8): { path: string; json: CfdProject }[] {
  if (!existsSync(CASE_DIR)) return []
  const out: { path: string; json: CfdProject }[] = []
  for (const name of readdirSync(CASE_DIR)) {
    const dir = join(CASE_DIR, name)
    if (!existsSync(dir)) continue
    for (const f of readdirSync(dir)) {
      if (!f.endsWith('.cfdproj') || out.length >= limit) continue
      out.push({ path: join(dir, f), json: JSON.parse(readFileSync(join(dir, f), 'utf8')) })
    }
  }
  return out
}

describe('the typed .cfdproj shape against real projects', () => {
  const found = realProjects()

  it('finds some to check', () => {
    expect(found.length).toBeGreaterThan(0)
  })

  it('declares exactly the buckets the shared format module does', () => {
    // TWO hand-kept lists of the same thing: schema/cfdproj.ts BUCKETS and
    // js/cfdproj_schema.js BUCKETS. A bucket added to the format and not here
    // is invisible until something reaches into it and finds undefined.
    expect([...BUCKETS].sort()).toEqual([...schema().BUCKETS].sort())
  })

  it('sees only buckets the shared schema declares', () => {
    // The two singleton slots are legal keys of `constructions` too, even
    // though they are not buckets — every saved project carries them.
    const known = new Set([...(schema().BUCKETS as string[]), ...SINGLETON_SLOTS])
    for (const { path, json } of found) {
      for (const bucket of Object.keys(json.constructions ?? {})) {
        expect(known, `${path} has an unknown bucket ${bucket}`).toContain(bucket)
      }
    }
  })

  it('keys every construction by its own uid', () => {
    // The V4 invariant the whole store leans on: `constructions[bucket][uid].uid === uid`.
    // A file where they disagree makes findConstruction return a model whose
    // edits land on a different object.
    for (const { path, json } of found) {
      for (const [bucket, items] of Object.entries(json.constructions ?? {})) {
        for (const [uid, model] of Object.entries(items ?? {})) {
          expect((model as { uid?: string }).uid, `${path} ${bucket}.${uid}`).toBe(uid)
        }
      }
    }
  })

  it('is V4 or V5 or carries no schemaVersion at all', () => {
    // V5 is V4 plus the transient block (js/cfdproj_transient.js), and it is
    // what the editor saves; case/Base2-9 was re-saved as V5 on 2026-10-21.
    for (const { path, json } of found) {
      const v = (json as unknown as { schemaVersion?: number }).schemaVersion
      if (v !== undefined) expect(v, path).toBeLessThanOrEqual(5)
    }
  })
})
