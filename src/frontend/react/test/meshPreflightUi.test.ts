import { afterEach, describe, expect, it } from 'vitest'
import {
  closeMeshProgress,
  openMeshProgress,
  setMeshProgressStep,
  showDigitNameDialog,
  showRoomWallGapDialog,
  showSeedCollisionDialog,
} from '../src/run/meshPreflightUi'
import { t } from '../src/i18n'

/**
 * The ids are the contract. The parity sweep addresses these overlays by the
 * legacy editor's ids, so a dialog that works but is called something else is a
 * dialog the sweep reports as missing.
 */

afterEach(() => {
  document.body.innerHTML = ''
  closeMeshProgress()
})

describe('the step overlay', () => {
  it('carries the legacy ids for the overlay, the title, the bar and every step', () => {
    openMeshProgress('preparing')
    expect(document.getElementById('mesh-progress-overlay')).toBeTruthy()
    expect(document.getElementById('mp-title')?.textContent).toBe('preparing')
    expect(document.getElementById('mp-bar')).toBeTruthy()
    for (const id of ['preflight', 'cfdrun', 'stl', 'upload', 'init']) {
      expect(document.getElementById(`mp-step-${id}`)).toBeTruthy()
    }
  })

  it('does not stack a second overlay', () => {
    openMeshProgress('a')
    openMeshProgress('b')
    expect(document.querySelectorAll('#mesh-progress-overlay').length).toBe(1)
    expect(document.getElementById('mp-title')?.textContent).toBe('b')
  })

  it('ticks everything before the active step and nothing after it', () => {
    openMeshProgress('preparing')
    setMeshProgressStep('stl')
    const icon = (id: string) =>
      document.getElementById(`mp-step-${id}`)?.querySelector('.mp-icon')?.textContent
    expect(icon('preflight')).toBe('✓')
    expect(icon('cfdrun')).toBe('✓')
    expect(icon('stl')).toBe('⟳')
    expect(icon('upload')).toBe('○')
    expect(document.getElementById('mp-bar')?.style.width).toBe('50%')
  })

  it('is a no-op with no overlay up, so a solve elsewhere is unaffected', () => {
    expect(() => setMeshProgressStep('stl')).not.toThrow()
    expect(document.getElementById('mesh-progress-overlay')).toBeNull()
  })
})

describe('the stop dialogs', () => {
  it('closes the progress overlay before showing, so the modal is reachable', () => {
    openMeshProgress('preparing')
    void showDigitNameDialog([{ uid: 'm_1', name: '10 Rack', bucket: 'racks' }])
    expect(document.getElementById('mesh-progress-overlay')).toBeNull()
    expect(document.getElementById('digit-name-warning-dialog')).toBeTruthy()
    document.getElementById('digit-name-ok-btn')?.dispatchEvent(new MouseEvent('click'))
  })

  it('renders a model name as text, never as markup', () => {
    void showDigitNameDialog([{ uid: 'm_1', name: '1<img src=x>', bucket: 'racks' }])
    const dialog = document.getElementById('digit-name-warning-dialog')!
    expect(dialog.querySelector('img')).toBeNull()
    expect(dialog.textContent).toContain('1<img src=x>')
    document.getElementById('digit-name-ok-btn')?.dispatchEvent(new MouseEvent('click'))
  })

  it('resolves false on Back and true on Ignore', async () => {
    const warning = {
      surfaceName: 'raised_floor_part1',
      surfaceOwner: 'floor-default',
      side: 'positive' as const,
      objectName: 'Rack_1',
      objectType: 'rack',
      point: { x: 1, y: 2, z: 3 },
    }
    const back = showSeedCollisionDialog([warning])
    document.getElementById('seed-collision-cancel-btn')!.dispatchEvent(new MouseEvent('click'))
    expect(await back).toBe(false)
    expect(document.getElementById('seed-collision-warning-dialog')).toBeNull()

    const ignore = showSeedCollisionDialog([warning])
    document.getElementById('seed-collision-ignore-btn')!.dispatchEvent(new MouseEvent('click'))
    expect(await ignore).toBe(true)
  })

  it('names the object and the coordinates the seed landed at', () => {
    void showSeedCollisionDialog([
      {
        surfaceName: 'raised_floor_part1',
        surfaceOwner: 'floor-default',
        side: 'positive',
        objectName: 'Rack_1',
        objectType: 'rack',
        point: { x: 1.5, y: 0.2425, z: 3 },
      },
    ])
    const text = document.getElementById('seed-collision-warning-dialog')!.textContent!
    expect(text).toContain('Rack_1')
    expect(text).toContain('0.2425')
    document.getElementById('seed-collision-cancel-btn')!.dispatchEvent(new MouseEvent('click'))
  })
})

describe('the room-wall-gap dialog', () => {
  const gap = { uid: 'm_2', name: 'Wall_2', top: 2, roomHeight: 2.7 }
  const click = (id: string) => document.getElementById(id)!.dispatchEvent(new MouseEvent('click'))

  it('carries the legacy ids, one item per wall, heights to four places', () => {
    openMeshProgress('preparing')
    void showRoomWallGapDialog([gap, { ...gap, uid: 'm_3', name: 'Wall_3', top: 1.5 }], () => 1)
    expect(document.getElementById('mesh-progress-overlay')).toBeNull()
    const dialog = document.getElementById('room-wall-gap-error-dialog')!
    expect(dialog).toBeTruthy()
    const items = [...dialog.querySelectorAll<HTMLElement>('.room-wall-gap-item')]
    expect(items.map((el) => el.dataset.wall)).toEqual(['Wall_2', 'Wall_3'])
    expect(items[0]!.textContent).toContain(
      t('dialog.room_wall_gap_item', { name: 'Wall_2', top: '2.0000', roomHeight: '2.7000' }),
    )
    expect(document.getElementById('room-wall-gap-error-ok-btn')?.textContent).toBe(
      t('btn.floor_height_error_ok'),
    )
    expect(document.getElementById('room-wall-gap-extend-btn')?.textContent).toBe(
      t('btn.room_wall_gap_extend'),
    )
    click('room-wall-gap-error-ok-btn')
  })

  it('resolves ok on Back without extending anything', async () => {
    let calls = 0
    const choice = showRoomWallGapDialog([gap], () => {
      calls++
      return 1
    })
    click('room-wall-gap-error-ok-btn')
    expect(await choice).toBe('ok')
    expect(calls).toBe(0)
    expect(document.getElementById('room-wall-gap-error-dialog')).toBeNull()
  })

  it('extends once and resolves extended', async () => {
    let calls = 0
    const choice = showRoomWallGapDialog([gap], () => {
      calls++
      return 1
    })
    click('room-wall-gap-extend-btn')
    expect(await choice).toBe('extended')
    expect(calls).toBe(1)
    expect(document.getElementById('room-wall-gap-error-dialog')).toBeNull()
  })

  it('resolves ok when the extension changed nothing', async () => {
    const choice = showRoomWallGapDialog([gap], () => 0)
    click('room-wall-gap-extend-btn')
    expect(await choice).toBe('ok')
  })

  it('renders a wall name as text, never as markup', () => {
    void showRoomWallGapDialog([{ ...gap, name: 'W<img src=x>' }], () => 0)
    const dialog = document.getElementById('room-wall-gap-error-dialog')!
    expect(dialog.querySelector('img')).toBeNull()
    expect(dialog.textContent).toContain('W<img src=x>')
    click('room-wall-gap-error-ok-btn')
  })
})
