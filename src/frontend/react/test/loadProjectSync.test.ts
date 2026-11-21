import { describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'

/**
 * `loadProject` runs the rack normalizer once per rack (spec §3.2 / WP2-D11).
 *
 * The bug this pins: a document saved before the absolute server-depth fields
 * existed carries `extraInletOffset` / `extraOutletOffset` and no per-server
 * opacity. React used to load it as-is and only sync a rack when some edit
 * happened to touch it — until then the cfdrun/STL export read the absolute
 * fields as 0 and every server sat flush with both rack faces. The legacy app
 * synced on load; now both do.
 *
 * The sync is a MIGRATION, not an edit: the project must come out of the load
 * clean — not dirty, nothing on the undo stack — or opening a file would arm
 * Ctrl+Z against the file itself and offer to "save" a document the user has
 * not touched.
 */

/** An old-document rack: relative extras, no absolute fields, no server opacity. */
function oldDocument(): CfdProject {
  return {
    name: 'Old',
    constructions: {
      racks: {
        r1: {
          uid: 'r1',
          meta: { name: 'Rack_Old', enabled: true, display: { opacity: 0.5 } },
          geometry: {
            location: { x: 0, y: 0, z: 0 },
            size: { x: 0.6, y: 2, z: 1.2 },
            slot: 42,
            slotHeight: 0.04445,
            slotWidthRatio: 0.8,
          },
          inletOffset: 0.1,
          outletOffset: 0.05,
          constructions: {
            servers: {
              e_1: {
                uid: 'e_1',
                type: 'fanServer',
                geometry: { slotPosition: 8, slotOccupation: 2 },
                cooling: { power: 4200 },
                meta: { name: 'srv', enabled: true },
                extraInletOffset: 0.02,
                extraOutletOffset: 0.3,
              },
            },
          },
        },
      },
    },
  } as unknown as CfdProject
}

type ServerRecord = {
  inletOffset?: number
  outletOffset?: number
  extraInletOffset?: number
  extraOutletOffset?: number
  cooling?: { power?: number }
  meta?: { display?: { opacity?: number } }
}

function serverOf(project: CfdProject | null): ServerRecord {
  const rack = project?.constructions?.racks?.['r1'] as unknown as {
    constructions: { servers: Record<string, ServerRecord> }
  }
  return rack.constructions.servers['e_1']!
}

describe('loadProject rack sync', () => {
  it('migrates the retired extras into absolute setbacks on load', () => {
    store.loadProject(oldDocument(), null)
    const srv = serverOf(store.getProject())
    // rack inset + extra, the sum the old derivation rendered with.
    expect(srv.inletOffset).toBeCloseTo(0.12, 12)
    expect(srv.outletOffset).toBeCloseTo(0.35, 12)
    expect(srv).not.toHaveProperty('extraInletOffset')
    expect(srv).not.toHaveProperty('extraOutletOffset')
  })

  it('backfills the per-server opacity from the rack and keeps the physics', () => {
    store.loadProject(oldDocument(), null)
    const srv = serverOf(store.getProject())
    expect(srv.meta?.display?.opacity).toBeCloseTo(0.5, 12)
    expect(srv.cooling?.power).toBe(4200)
  })

  it('rebuilds the conflict report so a badge can read it before any edit', () => {
    store.loadProject(oldDocument(), null)
    const rack = store.getProject()?.constructions?.racks?.['r1'] as unknown as {
      constructions: { serverConflicts?: unknown[] }
    }
    expect(rack.constructions.serverConflicts).toEqual([])
  })

  it('is a migration, not an edit: clean flag, empty undo stack', () => {
    store.loadProject(oldDocument(), '/tmp/Old.cfdproj')
    expect(store.isDirty()).toBe(false)
    expect(store.canUndo()).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })

  it('loads a rack too malformed to normalise instead of throwing', () => {
    // The sync is guarded per rack: one broken model cannot take the whole
    // document down at open — it fails on its first edit, as it always did.
    const broken = {
      name: 'Broken',
      constructions: {
        racks: { bad: { uid: 'bad', meta: { name: 'Bad' }, geometry: {} } },
      },
    } as unknown as CfdProject
    expect(() => store.loadProject(broken, null)).not.toThrow()
    expect(store.getProject()?.constructions?.racks?.['bad']).toBeDefined()
  })
})
