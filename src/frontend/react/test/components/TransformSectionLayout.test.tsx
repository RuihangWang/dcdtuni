import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import type { TreeSelection } from '../../src/interaction/treeNodes'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel, createPipeJunction, createPipeSegment } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { t } from '../../src/i18n'
import { Inspector } from '../../src/components/Inspector'
import { MultiSelectPanel } from '../../src/components/MultiSelectPanel'

/**
 * Move and rotate sit in the SAME place for every kind.
 *
 * The request: whatever is selected — a wall, a baffle, a floor, a rack, an
 * ACU, a box, a CDU, a pipe — the move and rotate controls are the first thing
 * under the panel header, in one fixed order, so the hand learns where they
 * are. Before this the rack put its rack-detail button first, the air pipe
 * had its move group at the BOTTOM under the waypoint list, and the position
 * and angle boxes sat below half a dozen kind-specific groups.
 *
 * Fixed order inside the block: move toggle, rotate toggle, then the position
 * boxes, then the angle boxes. A kind without a gizmo drops its rows; the
 * block still starts at the top.
 */

let host: HTMLDivElement
let root: Root

function project(): CfdProject {
  return store.getProject() as CfdProject
}

function add(kind: string): string {
  const created =
    kind === 'pipe_segment'
      ? createPipeSegment(project())
      : kind === 'pipe_junction'
        ? createPipeJunction(project())
        : createModel(project(), kind)
  return store.addModel(created.bucket, created.model)
}

function render(uid: string | null, node: TreeSelection | null = null) {
  flushSync(() =>
    root.render(
      <Inspector project={store.getProject()} selectedUid={uid} node={node} clearSelection={() => {}} />,
    ),
  )
}

const byId = (id: string) => host.querySelector(`#${id}`)
const content = () => host.querySelector('#prop-content') as HTMLElement

/**
 * The panel's first child — raw, nothing skipped. The face banner used to sit
 * above the block, which pushed the toggles down 3-4 rows whenever the pick
 * landed on a face or a slot.
 */
function firstGroup(): Element | null {
  return content().firstElementChild
}

/** Where an element sits under #prop-content, as child indices from the top. */
function pathOf(id: string): number[] {
  const path: number[] = []
  let el = byId(id) as Element | null
  expect(el, id).not.toBeNull()
  while (el && el !== content()) {
    const parent: Element = el.parentElement!
    path.unshift(Array.prototype.indexOf.call(parent.children, el) as number)
    el = parent
  }
  return path
}

/** Type a number into a NumberField and commit it, as a user does. */
function commit(id: string, value: string) {
  const el = byId(id) as HTMLInputElement
  expect(el, id).not.toBeNull()
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
  flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
}

function before(a: string, b: string) {
  const ea = byId(a)
  const eb = byId(b)
  expect(ea, a).not.toBeNull()
  expect(eb, b).not.toBeNull()
  expect(ea!.compareDocumentPosition(eb!) & Node.DOCUMENT_POSITION_FOLLOWING, `${a} before ${b}`).toBeTruthy()
}

function duplicateIds(): string[] {
  const seen = new Map<string, number>()
  host.querySelectorAll('[id]').forEach((el) => seen.set(el.id, (seen.get(el.id) ?? 0) + 1))
  return [...seen].filter(([, n]) => n > 1).map(([id]) => id)
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  store.setInputUnit('m')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the transform block opens every model panel', () => {
  const FULL = ['rack', 'acu', 'box', 'wall', 'baffle', 'floor', 'partitionWall', 'cdu']

  it.each(FULL)('%s: move, rotate, position, angles — first, in that order', (kind) => {
    render(add(kind))
    expect(firstGroup()?.id).toBe('transform-section')
    expect(firstGroup()?.getAttribute('data-transform-scope')).toBe('full')
    before('model-move-toggle', 'model-rotate-toggle')
    before('model-rotate-toggle', 'model-position-x')
    before('model-position-x', 'model-position-y')
    before('model-position-z', 'model-rotation-yaw')
    before('model-rotation-yaw', 'model-rotation-pitch')
    before('model-rotation-pitch', 'model-rotation-roll')
    expect(duplicateIds()).toEqual([])
  })

  it('an air pipe: the gizmo toggles first, above the name and the waypoint list', () => {
    render(add('pipe'))
    expect(firstGroup()?.id).toBe('transform-section')
    before('model-move-toggle', 'model-rotate-toggle')
    before('model-rotate-toggle', 'air-pipe-name')
    // Its shape is its waypoints; there is no anchor box to type into.
    expect(byId('model-position-x')).toBeNull()
    expect(byId('model-rotation-yaw')).toBeNull()
    // The anchor checkbox moved up with the block — once, not twice.
    expect(byId('model-anchor-marker')).not.toBeNull()
    expect(duplicateIds()).toEqual([])
  })

  it('a rack keeps its rack-detail button, now under the transform block', () => {
    render(add('rack'))
    before('transform-section', 'btn-enter-rack-detail')
  })

  it('a CDU puts the block above its title and name', () => {
    render(add('cdu'))
    before('model-rotation-roll', 'cdu-name')
    // The anchor ball now lives with the position boxes, so the display group
    // below carries none of its own.
    expect(host.querySelectorAll('#model-anchor-marker').length).toBe(1)
  })

  it('a T-junction offers move and position, but no rings and no angle boxes', () => {
    render(add('pipe_junction'))
    expect(firstGroup()?.id).toBe('transform-section')
    expect(byId('model-move-toggle')).not.toBeNull()
    expect(byId('model-position-x')).not.toBeNull()
    expect(byId('model-rotate-toggle')).toBeNull()
    expect(byId('model-rotation-yaw')).toBeNull()
  })

  it('a liquid pipe has no transform block at all', () => {
    render(add('pipe_segment'))
    expect(byId('transform-section')).toBeNull()
    expect(byId('model-move-toggle')).toBeNull()
  })

  it('the old position and angle groups are gone from the bottom of the panel', () => {
    render(add('box'))
    const section = byId('transform-section')!
    // Exactly one of each, and both inside the block.
    expect(host.querySelectorAll('#model-position-x').length).toBe(1)
    expect(section.contains(byId('model-position-x'))).toBe(true)
    expect(section.contains(byId('model-rotation-yaw'))).toBe(true)
    // Delete is still last.
    before('model-rotation-roll', 'btn-remove-model')
  })
})

describe('what the controls do is unchanged', () => {
  it('the move toggle is one setMoveTool edit, and the axis locks appear while it is on', () => {
    const uid = add('box')
    render(uid)
    expect(byId('model-move-axis-x')).toBeNull()
    const depth = store.undoDepth()
    flushSync(() => (byId('model-move-toggle') as HTMLElement).click())
    expect(store.undoDepth()).toBe(depth + 1)
    render(uid)
    const box = byId('model-move-axis-y') as HTMLInputElement
    expect(box).not.toBeNull()
    flushSync(() => box.click())
    const moveTool = (store.findConstruction(project(), uid)!.model as unknown as {
      moveTool: { enabled: boolean; axes: { y: boolean } }
    }).moveTool
    expect(moveTool.enabled).toBe(true)
    expect(moveTool.axes.y).toBe(false)
  })

  it('the rotate toggle turns the rings on and shows which ones', () => {
    const uid = add('acu')
    render(uid)
    flushSync(() => (byId('model-rotate-toggle') as HTMLElement).click())
    render(uid)
    expect(byId('model-rotate-axis-yaw')).not.toBeNull()
    const tool = (store.findConstruction(project(), uid)!.model as unknown as {
      rotateTool: { enabled: boolean }
    }).rotateTool
    expect(tool.enabled).toBe(true)
  })

  it('a position box writes the model’s location', () => {
    const uid = add('box')
    render(uid)
    commit('model-position-x', '1.25')
    const m = store.findConstruction(project(), uid)!.model as Construction
    expect(m.geometry?.location?.x).toBeCloseTo(1.25, 6)
  })

  // The boxes were reordered to yaw / pitch / roll and given new ids, so each
  // one is pinned to the Euler axis it writes: yaw turns about Y (up in this
  // scene), pitch about X, roll about Z — the rings' own axes.
  it.each([
    ['yaw', 'y'],
    ['pitch', 'x'],
    ['roll', 'z'],
  ] as const)('the %s box turns the model about %s only', (name, axis) => {
    const uid = add('box')
    render(uid)
    commit(`model-rotation-${name}`, '30')
    const q = (store.findConstruction(project(), uid)!.model as Construction).geometry!.quaternion!
    const euler = store.quaternionToEulerDeg(q)
    for (const other of ['x', 'y', 'z'] as const) {
      expect(euler[other], other).toBeCloseTo(other === axis ? 30 : 0, 6)
    }
  })

  it('the rotate-axis choices are translated, with a heading of their own', () => {
    const uid = add('box')
    store.setRotateTool(uid, { enabled: true })
    render(uid)
    const labelOf = (axis: string) =>
      host.querySelector(`label[for="model-rotate-axis-${axis}"]`)?.textContent
    expect(labelOf('yaw')).toBe(t('inspector.rotation_yaw'))
    expect(labelOf('pitch')).toBe(t('inspector.rotation_pitch'))
    expect(labelOf('roll')).toBe(t('inspector.rotation_roll'))
    const heading = byId('model-rotate-axes')!.querySelector(':scope > label')!.textContent
    expect(heading).toBe(t('inspector.rotate_axes'))
    // Not the rotation group's title again, a few rows further down.
    expect(heading).not.toBe(t('inspector.rotation_title'))
    expect(heading).not.toBe(t('label.rotation_angles'))
  })
})

describe('a part of the model is selected', () => {
  it('a box face: the whole block for the box, first, with the face banner under it', () => {
    const uid = add('box')
    render(uid, { type: 'face', uid, faceId: 'top' })
    expect(firstGroup()?.id).toBe('transform-section')
    expect(firstGroup()?.getAttribute('data-transform-scope')).toBe('part')
    expect(firstGroup()?.nextElementSibling?.id).toBe('selected-face-banner')
    before('model-move-toggle', 'model-rotate-toggle')
    // The same rows a wall pick gets: the numbers act on the parent model.
    before('model-rotate-toggle', 'model-position-x')
    before('model-position-z', 'model-rotation-yaw')
    expect(duplicateIds()).toEqual([])
  })

  it('says the block acts on the whole model, as its LAST row', () => {
    const uid = add('box')
    render(uid, { type: 'face', uid, faceId: 'top' })
    const section = byId('transform-section')!
    const hint = byId('transform-parent-hint')!
    expect(hint).not.toBeNull()
    expect(section.lastElementChild).toBe(hint)
    before('model-rotation-roll', 'transform-parent-hint')
    // With the tool on, the axis rows still sit above it.
    store.setMoveTool(uid, { enabled: true })
    render(uid, { type: 'face', uid, faceId: 'top' })
    before('model-move-axis-z', 'transform-parent-hint')
    // The model's own panel has no such line.
    render(uid)
    expect(byId('transform-parent-hint')).toBeNull()
  })

  it('a wall’s face is the wall: the whole block, no parent line', () => {
    const uid = add('wall')
    render(uid, { type: 'face', uid, faceId: 'front' })
    expect(firstGroup()?.id).toBe('transform-section')
    expect(firstGroup()?.getAttribute('data-transform-scope')).toBe('full')
    expect(byId('model-position-x')).not.toBeNull()
    expect(byId('transform-parent-hint')).toBeNull()
  })

  it('a rack slot: the whole block for the rack, first', () => {
    const uid = add('rack')
    render(uid, { type: 'rackSlot', uid, index: 0, indices: [0], side: 'front' })
    expect(firstGroup()?.id).toBe('transform-section')
    expect(firstGroup()?.getAttribute('data-transform-scope')).toBe('part')
    expect(byId('model-position-x')).not.toBeNull()
    expect(byId('model-rotation-yaw')).not.toBeNull()
    expect(duplicateIds()).toEqual([])
  })

  it('a wall region: the whole block for the wall, first', () => {
    const uid = add('wall')
    render(uid, { type: 'wallRegion', uid, faceId: 'front', index: 0 })
    expect(firstGroup()?.id).toBe('transform-section')
    expect(firstGroup()?.getAttribute('data-transform-scope')).toBe('part')
    expect(byId('model-position-x')).not.toBeNull()
    expect(duplicateIds()).toEqual([])
  })

  // The request in one test: however the object was picked — its tree row,
  // a face in the viewport, a rack's front — the controls are in the same
  // place, so the hand does not have to look.
  it.each([
    ['rack', { type: 'rackSlot', index: 0, indices: [0], side: 'front' }],
    ['box', { type: 'face', faceId: 'top' }],
    ['acu', { type: 'face', faceId: 'front' }],
    ['wall', { type: 'face', faceId: 'front' }],
    ['baffle', { type: 'face', faceId: 'front' }],
    ['floor', { type: 'face', faceId: 'top' }],
    ['partitionWall', { type: 'face', faceId: 'front' }],
    ['cdu', { type: 'face', faceId: 'front' }],
  ] as const)('%s: every control at the same place for the model and for a part pick', (kind, part) => {
    const uid = add(kind)
    const ids = [
      'model-move-toggle',
      'model-rotate-toggle',
      'model-position-x',
      'model-position-z',
      'model-rotation-yaw',
      'model-rotation-roll',
    ]
    render(uid)
    const whole = ids.map(pathOf)
    render(uid, { uid, ...part } as TreeSelection)
    expect(ids.map(pathOf)).toEqual(whole)
    expect(duplicateIds()).toEqual([])
  })

  it('an imported solid region: the whole block, as the legacy region panel has', () => {
    const { bucket, model } = createModel(project(), 'box')
    ;(model as Record<string, unknown>).kind = 'custom'
    const uid = store.addModel(bucket, model)
    render(uid, { type: 'region', uid, regionName: 'solid_a' })
    expect(firstGroup()?.id).toBe('transform-section')
    expect(firstGroup()?.getAttribute('data-transform-scope')).toBe('full')
    before('transform-section', 'region-inspector')
    expect(host.querySelectorAll('#model-anchor-marker').length).toBe(1)
  })

  it('a patch, a server or a rack partition: no transform block', () => {
    const box = add('box')
    const face = (store.findConstruction(project(), box)!.model.geometry as {
      faces: Record<string, { patches?: { id: string }[] }>
    }).faces
    const patchId = face.front?.patches?.[0]?.id ?? 'p_missing'
    render(box, { type: 'patch', uid: box, faceId: 'front', patchId })
    expect(byId('transform-section')).toBeNull()
    const rack = add('rack')
    render(rack, { type: 'partition', uid: rack, index: 0 })
    expect(byId('transform-section')).toBeNull()
  })
})

describe('the batch panel', () => {
  it('opens with the batch move and rotate toggles, directly under its title', () => {
    const a = add('box')
    const b = add('acu')
    flushSync(() =>
      root.render(
        <MultiSelectPanel project={project()} uids={[a, b]} tools={null} onSelectionChange={() => {}} />,
      ),
    )
    const first = host.querySelector('#multi-select-panel')!.firstElementChild!
    expect(first.id).toBe('multi-transform-group')
    const title = first.querySelector('.prop-title')!
    before('multi-move-toggle', 'multi-rotate-toggle')
    // Nothing between the title and the first toggle: the hint moved below.
    const moveRow = byId('multi-move-toggle')!.closest('.prop-row')!
    expect(title.nextElementSibling).toBe(moveRow)
  })

  it('shows the min-point boxes under the toggles once move is on, as position follows move', () => {
    const a = add('box')
    const b = add('box')
    store.setMoveTool(a, { enabled: true })
    flushSync(() =>
      root.render(
        <MultiSelectPanel project={project()} uids={[a, b]} tools={null} onSelectionChange={() => {}} />,
      ),
    )
    before('multi-rotate-toggle', 'multi-move-axis-x')
    before('multi-move-axis-z', 'multi-min-x')
  })
})
