import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { CduProperties } from '../../src/components/CduInspector'
import { MultiSelectPanel } from '../../src/components/MultiSelectPanel'

/**
 * Two panels that were still hard-coded to metres.
 *
 * Every other length box in the app follows `meta.inputUnit`, and these two did
 * not — the CDU envelope and the multi-select min point. That is not a display
 * nit: the boxes are editable, so in a millimetre project (the common case,
 * because rack datasheets are in mm) the CDU offered `0.8` beside racks reading
 * `600`, and typing the `800` it looks like it wants built an 800-METRE
 * cabinet. The min point was worse: typing 1500 moved the whole selection 1.5
 * kilometres.
 */

let host: HTMLDivElement
let root: Root

function project(): CfdProject {
  return store.getProject() as CfdProject
}

function setUnit(unit: 'mm' | 'cm' | 'm') {
  store.setInputUnit(unit)
}

function add(kind: string): string {
  const { bucket, model } = createModel(project(), kind)
  return store.addModel(bucket, model)
}

function field(id: string): HTMLInputElement {
  return host.querySelector(`#${id}`) as HTMLInputElement
}

function type(id: string, value: string) {
  const el = field(id)
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
  // `focusout`, not `blur`: React 18 delegates at the root and only sees the
  // bubbling one, so a plain `blur` never reaches `NumberField.onBlur` and the
  // commit silently does not happen.
  flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
}

function model(uid: string): Construction {
  return store.findConstruction(project(), uid)!.model
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the CDU envelope', () => {
  let uid: string

  beforeEach(() => {
    uid = add('cdu')
    store.setGeometryComponent(uid, 'size', 'x', 0.8)
  })

  function render() {
    flushSync(() => root.render(<CduProperties project={project()} model={model(uid)} />))
  }

  it('reads in millimetres when the project is in millimetres', () => {
    setUnit('mm')
    render()
    // 0.8 m, shown as millimetres. Compared numerically because
    // `NumberField` only pads decimals when it is told to.
    expect(Number(field('cdu-size-x').value)).toBeCloseTo(800, 4)
  })

  it('reads in metres when the project is in metres', () => {
    setUnit('m')
    render()
    expect(Number(field('cdu-size-x').value)).toBeCloseTo(0.8, 4)
  })

  it('takes a typed millimetre value as millimetres', () => {
    setUnit('mm')
    render()
    type('cdu-size-x', '1200')
    // 1.2 m — not a 1200-metre cabinet.
    expect(model(uid).geometry!.size!.x).toBeCloseTo(1.2, 6)
  })
})

describe('the multi-select min point', () => {
  let uids: string[]

  beforeEach(() => {
    uids = [add('box'), add('box')]
    store.setModelLocation(uids[0]!, { x: 1.5, y: 0, z: 0 })
    store.setModelLocation(uids[1]!, { x: 3, y: 0, z: 0 })
    // The row only appears when the selection can be moved.
    for (const uid of uids) store.setMoveTool(uid, { enabled: true })
  })

  function render() {
    flushSync(() =>
      root.render(
        <MultiSelectPanel
          project={project()}
          uids={uids}
          tools={null}
          onSelectionChange={() => {}}
        />,
      ),
    )
  }

  it('reads the corner in millimetres', () => {
    setUnit('mm')
    render()
    expect(Number(field('multi-min-x').value)).toBeCloseTo(1500, 4)
  })

  it('moves the group by the typed MILLIMETRES, not by that many metres', () => {
    setUnit('mm')
    render()
    type('multi-min-x', '2500')
    // The corner was 1.5 m; asking for 2500 mm moves everything +1 m.
    expect(model(uids[0]!).geometry!.location!.x).toBeCloseTo(2.5, 6)
    expect(model(uids[1]!).geometry!.location!.x).toBeCloseTo(4, 6)
  })

  it('still works in metres', () => {
    setUnit('m')
    render()
    expect(Number(field('multi-min-x').value)).toBeCloseTo(1.5, 4)
    type('multi-min-x', '2.5')
    expect(model(uids[0]!).geometry!.location!.x).toBeCloseTo(2.5, 6)
  })
})
