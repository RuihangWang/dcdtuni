/**
 * Typed facade over the shared `cfdproj_transient.js` module (window global).
 *
 * The module is the single definition of the v5 transient format — the same
 * reason the other .cfdproj modules are shared rather than rewritten
 * (legacy/cfdprojModules.ts). Design: docs/developer/transient_keyframes_design_2026-09-30.md.
 */
import { reviveKinds } from '../legacy/cfdprojModules'
import type { CfdProject } from '../schema/cfdproj'

export interface Keyframe { t: number; value: number; rampS?: number }
export interface Track { modelUid: string; param: string; target: string; keyframes: Keyframe[] }
export interface TransientStart {
  kind: 'steadyConverged' | 'uniform'
  uniform?: { airT_C?: number; waterT_C?: number; solidT_C?: number }
}
export type DomainMode = 'steady' | 'transient'
export type CoolingDomain = 'air' | 'liquid'
export interface TransientBlock {
  /** Derived: transient when either domain is (E8b). Kept for timeline readers. */
  mode: DomainMode
  /** Air runs steady or transient; mirrored to simulationConfig.solverType. */
  airMode?: DomainMode
  /** Liquid runs steady or transient; mirrored to the DAE's analysis.mode. */
  liquidMode?: DomainMode
  durationS: number
  gridS: number
  start: TransientStart
  tracks: Track[]
}
export interface RegistryEntry {
  key: string; mode: 'air' | 'liquid'; bucket: string; path: string; unit: string
  min: number; max: number; default?: number; createWithDefault?: boolean; where?: Record<string, unknown>
}
export type EditOutcome = { ok: true } | { ok: false; reason: 'structure' | 'static'; detail: string[] }
type Doc = CfdProject | Record<string, unknown>

interface TransientModule {
  registry(): { format: string; params: RegistryEntry[] }
  entryOf(key: string): RegistryEntry | undefined
  valueAt(keyframes: Keyframe[], t: number): number
  cornerTable(keyframes: Keyframe[], durationS: number): [number, number][]
  onGrid(x: number, g: number): boolean
  validateTransient(transient: unknown): { path: string; msg: string }[]
  defaultTransient(doc: Doc): TransientBlock
  domainModes(doc: Doc): Record<CoolingDomain, DomainMode>
  withDomainModes<T extends Doc>(doc: T): T
  modeOfDomains(air: DomainMode, liquid: DomainMode): DomainMode
  tracksFromFields(doc: Doc): Track[]
  materializeAt<T extends Doc>(doc: T, t: number): T
  stripTrackedFields<T extends Doc>(doc: T): T
  commitEdit(before: Doc, draft: Doc, t: number): EditOutcome
  /** The scene state an edit off t = 0 must not change (tracked and display-only fields excluded). */
  staticText(doc: Doc): string
  syncFieldsToTracks<T extends Doc>(doc: T, t: number): T
  migrateCfdprojToV5(raw: unknown): unknown
  migrateCfdprojV4toV5WithReport(raw: unknown): { doc: unknown; report: string[] }
}

export function transientModel(): TransientModule {
  const mod = (window as unknown as { cfdprojTransient?: TransientModule }).cfdprojTransient
  if (!mod) throw new Error('cfdproj_transient.js is not loaded')
  return mod
}

/** The block of a live project, or a steady default for a document that lacks one. */
export function transientOf(project: CfdProject | null): TransientBlock {
  const tr = (project as { transient?: TransientBlock } | null)?.transient
  return tr ?? transientModel().defaultTransient((project ?? {}) as Doc)
}

/** Air and liquid modes of a project (derived for blocks written before E8b). */
export function domainModesOf(project: CfdProject | null): Record<CoolingDomain, DomainMode> {
  const m = transientModel()
  const doc = (project ?? {}) as Doc
  return (doc as { transient?: unknown }).transient ? m.domainModes(doc) : { air: 'steady', liquid: 'steady' }
}

/**
 * Any document entering the editor, made live: brought to v5 (older files
 * migrate; their time-variable fields move into tracks) and then materialised
 * at `t`, so every existing reader finds the fields it always read.
 */
export function liveProject<T extends CfdProject>(doc: T, t: number): T {
  const m = transientModel()
  const version = Number((doc as { schemaVersion?: unknown }).schemaVersion ?? 0) | 0
  // Migration copies through JSON, which drops the non-enumerable `kind` the
  // scene builder needs — revive it (as the store's cloneProject does).
  const v5 = (version < 5 ? reviveKinds(m.migrateCfdprojToV5(doc) as T) : doc) as T & { transient?: TransientBlock }
  if (!v5.transient) v5.transient = m.defaultTransient(v5)
  m.withDomainModes(v5)
  return m.materializeAt(v5, t)
}

/**
 * What a STEADY compile reads: a v5 document (live at any playhead, or
 * reopened from disk with its fields stripped) as a copy materialised at
 * t = 0. Older documents pass through untouched.
 */
export function projectAtStart<T extends CfdProject>(doc: T): T {
  const version = Number((doc as { schemaVersion?: unknown }).schemaVersion ?? 0) | 0
  if (version < 5 || !(doc as { transient?: unknown }).transient) return doc
  return transientModel().materializeAt(reviveKinds(structuredClone(doc)) as T, 0)
}

/** The cfdproj schema version a compiled manifest records for its source. */
export function sourceSchemaVersion(doc: CfdProject): 4 | 5 {
  return (Number((doc as { schemaVersion?: unknown }).schemaVersion ?? 0) | 0) >= 5 ? 5 : 4
}
