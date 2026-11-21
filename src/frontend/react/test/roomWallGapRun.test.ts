import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { t } from '../src/i18n'
import { collectRoomWallGapErrors } from '../src/run/meshPreflight'
import { extendRoomWallsToHeight, runMeshPreflight } from '../src/run/runSubmit'

/**
 * The room-wall gate as the run button meets it: the dialog, Back, and the
 * extend button editing the store in ONE undo step.
 *
 * Base604 meshed with 3.5 m walls under a 4.4 m ceiling came out with no room
 * wall, floor or ceiling patch at all, and nothing said why. These tests run on
 * DAML with two walls cut short, a real project with one thing wrong.
 */

const CASE_DIR = resolve(process.cwd(), '../../../case')

function daml(): CfdProject {
  return reviveKinds(
    JSON.parse(readFileSync(resolve(CASE_DIR, 'DAML-Testbed/DMAL_Testbed-patched.cfdproj'), 'utf8')) as CfdProject,
  )
}

type Geo = { size: { y: number }; location: { y: number } }
const geoIn = (project: CfdProject, bucket: 'walls' | 'floors', uid: string) =>
  (project.constructions?.[bucket]?.[uid] as Construction).geometry as unknown as Geo
const sizeY = (uid: string) => geoIn(store.getProject()!, 'walls', uid).size.y

/** DAML with Wall_2 and Wall_4 cut to 2.0 m under the 2.7 m ceiling, in the store. */
function loadShrunk() {
  const project = daml()
  for (const uid of ['m_2', 'm_4']) geoIn(project, 'walls', uid).size.y = 2
  store.loadProject(project, null)
}

const settle = () => new Promise((r) => setTimeout(r, 0))
const click = (id: string) => document.getElementById(id)!.dispatchEvent(new MouseEvent('click'))

afterEach(() => {
  document.body.innerHTML = ''
})

describe('the room-wall gate in the mesh pre-flight', () => {
  it('stops the run with the dialog and leaves the project alone on Back', async () => {
    loadShrunk()
    const depth = store.undoDepth()
    const run = runMeshPreflight(store.getProject())
    await settle()
    const items = [
      ...document.querySelectorAll<HTMLElement>('#room-wall-gap-error-dialog .room-wall-gap-item'),
    ]
    expect(items.map((el) => el.dataset.wall)).toEqual(['Wall_2', 'Wall_4'])
    click('room-wall-gap-error-ok-btn')
    await expect(run).rejects.toMatchObject({
      name: 'PreflightStop',
      message: t('status.room_wall_gap_error'),
    })
    expect(sizeY('m_2')).toBe(2)
    expect(store.undoDepth()).toBe(depth)
  })

  it('extends every listed wall in ONE undo step, and still stops the run', async () => {
    loadShrunk()
    const depth = store.undoDepth()
    const run = runMeshPreflight(store.getProject())
    await settle()
    click('room-wall-gap-extend-btn')
    // Nothing is wrong any more, so the status line is cleared as the legacy
    // clears it; the user sees the raised walls and presses run again.
    await expect(run).rejects.toMatchObject({ name: 'PreflightStop', message: '' })
    expect(document.getElementById('room-wall-gap-error-dialog')).toBeNull()
    expect(sizeY('m_2')).toBeCloseTo(2.7, 9)
    expect(sizeY('m_4')).toBeCloseTo(2.7, 9)
    expect(store.undoDepth()).toBe(depth + 1)
    expect(store.getLog()[0]).toMatchObject({ action: 'extendRoomWalls' })
    expect(store.getLog()[0]!.detail).toContain('Wall_2')
    expect(collectRoomWallGapErrors(store.getProject())).toEqual([])

    store.undo()
    expect(sizeY('m_2')).toBe(2)
    expect(sizeY('m_4')).toBe(2)
  })

  it('asks about a floor through the ceiling before the walls', async () => {
    const project = daml()
    geoIn(project, 'walls', 'm_2').size.y = 2
    // m_5 is the DAML floor; the top-bound ceiling keeps the room at 2.7 m.
    geoIn(project, 'floors', 'm_5').location.y = 3
    store.loadProject(project, null)
    const run = runMeshPreflight(store.getProject())
    await settle()
    expect(document.getElementById('floor-height-error-dialog')).not.toBeNull()
    expect(document.getElementById('room-wall-gap-error-dialog')).toBeNull()
    click('floor-height-error-ok-btn')
    await expect(run).rejects.toMatchObject({ message: t('status.floor_height_error') })
  })
})

describe('extendRoomWallsToHeight', () => {
  it('returns 0 and takes no undo step when nothing needs to change', () => {
    store.loadProject(daml(), null)
    const depth = store.undoDepth()
    expect(extendRoomWallsToHeight(['m_1', 'm_2'], 2.7)).toBe(0)
    expect(store.undoDepth()).toBe(depth)
  })

  it('returns the number of walls it raised', () => {
    loadShrunk()
    expect(extendRoomWallsToHeight(['m_2', 'm_4', 'm_1'], 2.7)).toBe(2)
    expect(sizeY('m_4')).toBeCloseTo(2.7, 9)
  })
})
