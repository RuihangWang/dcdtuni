/**
 * A v3 .cfdproj must load.
 *
 * `checkProjectDocument` gates every load — the file dialog, the drop target and
 * `window._applyProjectData`, which is what the automation and the whole
 * 3-agent pipeline go through. Its marker test asked for `constructions` (V4),
 * `models`, or `cfdRoom` (v2) and forgot v3's `construction`: singular, an
 * array. So every v3 document was refused as "not a .cfdproj document", and the
 * refusal is silent — `_applyProjectData` returns false and sets an error
 * string, so a script sees the load "succeed" against an unchanged project.
 *
 * That is not a corner: all sixteen labels under
 * docs/manuscript/paper_experiment are v3 on disk, and so is every project
 * saved before b821e27. The shared migrator handles them; only this guard did
 * not.
 */

import { describe, expect, it } from 'vitest'

import { checkProjectDocument } from '../src/io/projectFiles'

/** The shape of a real v3 label: singular `construction`, no `constructions`. */
function v3Document() {
  return {
    schemaVersion: 3,
    meta: { projectName: 'v3 case', inputUnit: 'm' },
    construction: [
      {
        id: 'm_1',
        kind: 'floor',
        name: 'Floor',
        enabled: true,
        anchor: { x: 0, y: 0, z: 0 },
        base: { W: 6, D: 4, H: 0 },
        transform: { quaternion: { x: 0, y: 0, z: 0, w: 1 } },
        faces: {},
      },
    ],
    simulation: {},
    ui: {},
  }
}

describe('loading a v3 .cfdproj', () => {
  it('accepts the singular `construction` array as a document marker', () => {
    const result = checkProjectDocument(v3Document())
    expect(result.ok).toBe(true)
  })

  it('migrates it through V4 to v5, with the model in a bucket', () => {
    const result = checkProjectDocument(v3Document())
    expect(result.ok).toBe(true)
    if (!result.ok) return
    const project = result.project as unknown as {
      schemaVersion?: number
      constructions?: Record<string, Record<string, unknown>>
    }
    expect(project.schemaVersion).toBe(5)
    const total = Object.values(project.constructions ?? {}).reduce(
      (n, bucket) => n + Object.keys(bucket ?? {}).length,
      0,
    )
    expect(total).toBe(1)
  })

  it('still refuses something that is not a project at all', () => {
    expect(checkProjectDocument({ hello: 'world' }).ok).toBe(false)
    expect(checkProjectDocument({ schemaVersion: 3 }).ok).toBe(false)
  })

  it('still refuses a version from the future', () => {
    const doc = { ...v3Document(), schemaVersion: 6 }
    expect(checkProjectDocument(doc).ok).toBe(false)
  })
})
