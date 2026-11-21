import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { t } from '../src/i18n'
import { collectSheetGaps, modelWorldBounds, planSheetGapClosure } from '../src/run/meshPreflight'
import { runMeshPreflight } from '../src/run/runSubmit'

/**
 * Baffle edges that stop a few millimetres short of what they face.
 *
 * CMI-Guichou's fullBlock project never converged: the roof CD4 over the CD
 * aisle starts at z=27.010 while the door CD2 in the empty slot of row D stands
 * at z=27.000, so a 10 mm slit runs along the top of the door, and the whole
 * airflow imbalance between the aisles jets through it.
 */

const CASE_DIR = resolve(process.cwd(), '../../../case')

function load(rel: string): CfdProject {
  return reviveKinds(JSON.parse(readFileSync(resolve(CASE_DIR, rel), 'utf8')) as CfdProject)
}

/**
 * The working projects under case/ were closed by the closure itself
 * (4346ebf96). The detector is tested on the gaps they had: the sheets the
 * closure moved go back to where they stood (test/fixtures/sheet-gaps).
 */
function loadReopened(rel: string, fixture: string): CfdProject {
  const raw = JSON.parse(readFileSync(resolve(CASE_DIR, rel), 'utf8')) as unknown
  const reopen = JSON.parse(readFileSync(resolve(process.cwd(), 'test/fixtures/sheet-gaps', `${fixture}.reopen.json`), 'utf8')) as {
    models: Record<string, { before: Record<string, unknown> }>
  }
  let applied = 0
  const walk = (x: unknown): void => {
    if (Array.isArray(x)) return x.forEach(walk)
    if (!x || typeof x !== 'object') return
    const o = x as { uid?: string; geometry?: Record<string, unknown> }
    const before = o.uid ? reopen.models[o.uid]?.before : undefined
    if (before && o.geometry) {
      Object.assign(o.geometry, structuredClone(before))
      applied += 1
    }
    Object.values(o).forEach(walk)
  }
  walk(raw)
  expect(applied).toBe(Object.keys(reopen.models).length)
  return reviveKinds(raw as CfdProject)
}

const CMI = 'CMI-Guichou/CMI-Guichou_fullBlock.cfdproj'
const COLD = 'Base_coldAisleIso/Base_coldAisleIso.cfdproj'
const cmiOpen = () => loadReopened(CMI, 'cmi-guichou-fullblock')
const coldOpen = () => loadReopened(COLD, 'base-coldaisleiso')

const settle = () => new Promise((r) => setTimeout(r, 0))
const click = (id: string) => document.getElementById(id)!.dispatchEvent(new MouseEvent('click'))

afterEach(() => {
  document.body.innerHTML = ''
})

function byName(project: CfdProject, name: string) {
  const all = Object.values(project.constructions?.baffles ?? {}) as { meta?: { name?: string } }[]
  return all.find((m) => m.meta?.name === name) as Parameters<typeof modelWorldBounds>[0]
}

describe('near-miss baffle edges', () => {
  it('finds the roof edge that stops short of row B in CMI-Guichou', () => {
    const gaps = collectSheetGaps(cmiOpen())
    // After the doors move onto the rack faces, roof AB4 still stops 2 mm short of row B.
    const ab4 = gaps.find((g) => g.name === 'AB4')
    expect(ab4).toMatchObject({ move: 'edge', axis: 'z', side: -1, targetName: 'B001' })
    expect(ab4!.gap).toBeCloseTo(0.002, 6)
    // A roof 1 mm short of its door is reported too: the weld does not close it.
    expect(gaps.find((g) => g.name === 'GH4')).toMatchObject({ move: 'edge', axis: 'z', side: 1 })
    for (const g of gaps) expect(g.gap).toBeGreaterThan(5e-4)
    for (const g of gaps) expect(g.gap).toBeLessThanOrEqual(0.02 + 1e-9)
  })

  it('sees the roof slits over row J past the door the roof overhangs', () => {
    // IJ4 runs 2 mm past the door IJ2 in row J's empty slot; that overhang is not open space,
    // so the 6 mm slits over J10 and J13 further along the same edge are reported.
    const ij4 = collectSheetGaps(cmiOpen()).find((g) => g.name === 'IJ4')
    expect(ij4).toMatchObject({ move: 'edge', axis: 'z', side: -1, targetName: 'J10' })
    expect(ij4!.gap).toBeCloseTo(0.006, 5)
  })

  it('moves the aisle-end doors that stand off the rack faces onto their plane', () => {
    const planes = collectSheetGaps(cmiOpen()).filter((g) => g.move === 'plane')
    const moved = Object.fromEntries(planes.map((g) => [g.name, Math.round(g.gap * 1e4) / 10]))
    // AB2 stands 14 mm in front of A9/ACU_A2, CD2 11 mm inside D9/ACU_D2, ...
    expect(moved).toEqual({ MN2: 2, IJ2: 7, GH2: 8, CD2: 11, AB2: 14 })
    expect(planes.every((g) => g.axis === 'z')).toBe(true)
  })

  it('closes them: after the plan, nothing is left to report', () => {
    const project = cmiOpen()
    const gaps = collectSheetGaps(project)
    for (const edit of planSheetGapClosure(project, gaps)) {
      const g = project.constructions!.baffles![edit.uid]!.geometry as unknown as {
        location: object
        size: object
      }
      Object.assign(g.location, edit.location)
      Object.assign(g.size, edit.size)
    }
    expect(collectSheetGaps(project)).toEqual([])
    const door = modelWorldBounds(byName(project, 'AB2'))!
    expect(door.min.z).toBeCloseTo(32.814, 6)
    expect(door.max.z).toBeCloseTo(32.814, 6)
  })

  it('reports the 1 mm slits of the cold-aisle base case', () => {
    expect(collectSheetGaps(coldOpen()).map((g) => g.name).sort())
      .toEqual(['Baffle_1', 'Baffle_1_copy2', 'Baffle_4'])
  })

  it.each([
    'Base_leakage/Base_leakage.cfdproj',
    'DAML-Testbed/DMAL_Testbed-patched.cfdproj',
    'Shinaide/Shinaide.cfdproj',
    'STDCT/STDCT.cfdproj',
    'Base604/Base604.cfdproj',
    // closed by the closure itself (4346ebf96)
    CMI,
    COLD,
  ])('leaves %s alone (no near-miss)', (rel) => {
    expect(collectSheetGaps(load(rel))).toEqual([])
  })
})

describe('the baffle-gap gate in the mesh pre-flight', () => {
  it('closes the gaps in ONE undo step and stops the run', async () => {
    store.loadProject(cmiOpen(), null)
    const depth = store.undoDepth()
    const run = runMeshPreflight(store.getProject())
    await settle()
    const items = [...document.querySelectorAll<HTMLElement>('#sheet-gap-warning-dialog .sheet-gap-item')]
    expect(items.map((el) => el.dataset.sheet)).toContain('CD2')
    click('sheet-gap-close-btn')
    await expect(run).rejects.toMatchObject({ name: 'PreflightStop', message: '' })
    expect(store.undoDepth()).toBe(depth + 1)
    expect(store.getLog()[0]).toMatchObject({ action: 'closeSheetGaps' })
    expect(collectSheetGaps(store.getProject())).toEqual([])
  })

  it('stops on Back and leaves the project alone', async () => {
    store.loadProject(cmiOpen(), null)
    const depth = store.undoDepth()
    const run = runMeshPreflight(store.getProject())
    await settle()
    click('sheet-gap-back-btn')
    await expect(run).rejects.toMatchObject({ name: 'PreflightStop', message: t('status.sheet_gap_cancelled') })
    expect(store.undoDepth()).toBe(depth)
    expect(collectSheetGaps(store.getProject()).length).toBeGreaterThan(0)
  })
})
