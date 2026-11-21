import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import {
  CFDRUN_REQUIRED_KEYS,
  CFDRUN_SCHEMA_VERSION,
  cfdrunSchema,
  emptyManifest,
  validateManifest,
  type BcType,
  type PatchRole,
} from '../../src/schema/cfdrun'

/**
 * The whole point of this file: the TypeScript vocabulary and the shared JS
 * module's vocabulary must be the SAME SET. They are two hand-kept lists of the
 * same thing, and the failure mode when they drift is silent — a manifest that
 * names a bcType nobody implements meshes cleanly and solves the wrong problem.
 */
const TS_BC_TYPES: BcType[] = [
  'wall', 'acu_supply', 'acu_return', 'fan_inlet', 'fan_outlet',
  'nonfan_fixed_temp', 'nonfan_heat_flux', 'fixedTemperature',
  'heat_source_flux', 'perforated_baffle', 'wall_baffle', 'through_zone',
]
const TS_ROLES: PatchRole[] = [
  'acu_supply', 'acu_return', 'fan_inlet', 'fan_outlet', 'chip_heat_source',
  'cold_plate_sink', 'rdhx_surface', 'perforated_baffle', 'wall',
  'fixed_temperature', 'heat_source_flux',
]

describe('the TS vocabulary matches the shared module', () => {
  it('has exactly the bcTypes js/cfdrun_schema.js implements', () => {
    expect([...TS_BC_TYPES].sort()).toEqual([...cfdrunSchema().BC_TYPES].sort())
  })

  it('has exactly the patch roles the shared module defines', () => {
    expect([...TS_ROLES].sort()).toEqual([...cfdrunSchema().ROLES].sort())
  })

  it('agrees on the schema version the runner accepts', () => {
    expect(CFDRUN_SCHEMA_VERSION).toBe(cfdrunSchema().CFDRUN_SCHEMA_VERSION)
    expect(CFDRUN_SCHEMA_VERSION).toBe(2)
  })
})

describe('the empty manifest', () => {
  it('carries every required key, so a fresh one validates', () => {
    const m = emptyManifest()
    for (const key of CFDRUN_REQUIRED_KEYS) expect(m).toHaveProperty(key)
    expect(validateManifest(m)).toEqual([])
  })

  it('does not share nested objects between calls', () => {
    // The skeleton is built inline per call; a shared `inputs` would let one
    // export's racks leak into the next one's manifest.
    const a = emptyManifest()
    const b = emptyManifest()
    expect(a.inputs).not.toBe(b.inputs)
    expect(a.artifacts).not.toBe(b.artifacts)
  })
})

/**
 * Real manifests the editor exported, not files written for the test. A type
 * that only matches a file we wrote for the test is a type that proves nothing.
 * case/<name>/model/ is gitignored, so a clone has none there; the frozen
 * exports in test/__fixtures__/cfdrun are always checked as well (absolute
 * artifact paths made repo-relative, nothing else changed).
 */
const CASE_DIR = resolve(process.cwd(), '../../../case')
const FIXTURE_DIR = join(__dirname, '..', '__fixtures__', 'cfdrun')

function realManifests(limit = 6): { path: string; json: Record<string, unknown> }[] {
  const out: { path: string; json: Record<string, unknown> }[] = []
  if (existsSync(CASE_DIR)) {
    for (const name of readdirSync(CASE_DIR)) {
      const modelDir = join(CASE_DIR, name, 'model')
      if (!existsSync(modelDir)) continue
      for (const f of readdirSync(modelDir)) {
        if (!f.endsWith('.cfdrun') || out.length >= limit) continue
        out.push({ path: join(modelDir, f), json: JSON.parse(readFileSync(join(modelDir, f), 'utf8')) })
      }
    }
  }
  for (const f of readdirSync(FIXTURE_DIR)) {
    if (!f.endsWith('.cfdrun')) continue
    out.push({ path: join(FIXTURE_DIR, f), json: JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) })
  }
  return out
}

describe('manifests this repo actually produced', () => {
  const found = realManifests()

  it('finds some to check', () => {
    expect(found.length).toBeGreaterThan(0)
  })

  it('validates every one, and every bcType in them is in the vocabulary', () => {
    for (const { path, json } of found) {
      expect(validateManifest(json), `${path} failed validateManifest`).toEqual([])
      const bindings = (json.boundaryBindings ?? {}) as Record<string, { bcType?: string }>
      for (const [id, b] of Object.entries(bindings)) {
        if (!b?.bcType) continue
        expect(TS_BC_TYPES, `${path} ${id} has an unknown bcType`).toContain(b.bcType as BcType)
      }
    }
  })

  it('carries the transitional meshGeometry blob, which the runner still reads', () => {
    // Documented so its eventual removal breaks a test that says why, rather
    // than being noticed when a mesh silently uses the wrong gravity.
    for (const { path, json } of found) {
      expect(Object.keys(json), `${path}`).toContain('meshGeometry')
    }
  })
})
