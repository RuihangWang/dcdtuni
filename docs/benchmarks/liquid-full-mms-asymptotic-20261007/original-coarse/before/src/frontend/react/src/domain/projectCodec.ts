import type { CfdProject } from '../schema/cfdproj'
import { DomainProject } from './DomainProject'
import { objectRecord, type EntityRecord } from './BaseEntity'

/** The established runtime-only test/editor bag; other unknown keys are extensions. */
export const RUNTIME_ONLY_PROJECT_KEYS = ['__runtimeOnly'] as const

/** Ordered canonical keys first, then lossless JSON document extensions. */
export function encodeProjectRecord(source: EntityRecord, canonicalKeys: readonly string[]): EntityRecord {
  const out: EntityRecord = {}
  const assign = (key: string, value: unknown) => Object.defineProperty(out, key, {
    value, enumerable: true, configurable: true, writable: true,
  })
  for (const key of canonicalKeys) if (Object.prototype.hasOwnProperty.call(source, key)) assign(key, source[key])
  for (const [key, value] of Object.entries(source)) {
    if (canonicalKeys.includes(key) || RUNTIME_ONLY_PROJECT_KEYS.includes(key as typeof RUNTIME_ONLY_PROJECT_KEYS[number])) continue
    assign(key, value)
  }
  return out
}

/** Validate before legacy migrations can narrow an integer with bitwise arithmetic. */
export function projectSchemaVersion(record: EntityRecord): number {
  const version = Number(record.schemaVersion ?? 0)
  if (!Number.isSafeInteger(version) || version < 0 || version > 5)
    throw new Error('Unsupported schemaVersion ' + String(record.schemaVersion))
  return version
}

/** Read a migrated document without attaching methods/prototypes or altering data. */
export function decodeDomainProject(source: unknown): DomainProject {
  const record = objectRecord(source)
  if (!record) throw new Error('Expected a plain cfdproj document')
  projectSchemaVersion(record)
  return new DomainProject(record as CfdProject)
}
