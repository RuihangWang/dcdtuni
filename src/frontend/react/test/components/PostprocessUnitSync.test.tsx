import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ColorbarOverlay } from '../../src/components/ColorbarOverlay'
import { PostprocessPanel } from '../../src/components/PostprocessPanel'
import { PpSliceEditDialog, type SliceEditForm } from '../../src/components/PpEditDialogs'
import { setTempUnit } from '../../src/postprocess/ppUnits'

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  setTempUnit('C')
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    fillStyle: '', fillRect: vi.fn(),
  } as unknown as CanvasRenderingContext2D)
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ logRuns: [] }) })))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  setTempUnit('C')
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const $ = <T extends Element>(selector: string) => document.querySelector<T>(selector)!
function open(id: string) {
  act(() => $('#'+id).dispatchEvent(new MouseEvent('dblclick', { bubbles: true, detail: 2 })))
}
function change(input: HTMLInputElement | HTMLSelectElement, value: string) {
  act(() => {
    const prototype = input instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLSelectElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event(input instanceof HTMLInputElement ? 'input' : 'change', { bubbles: true }))
  })
}
function renderBars(apply=vi.fn()) {
  act(() => root.render(<>
    <ColorbarOverlay id="surface-unit" label="Surface" field="T" colorMap="coolwarm"
      climMin={293.15} climMax={303.15} onApply={apply} />
    <ColorbarOverlay id="slice-unit" label="Slice" field="T" colorMap="coolwarm"
      climMin={298.15} climMax={308.15} onApply={apply} />
    <ColorbarOverlay id="speed-unit" label="Speed" field="Umag" colorMap="coolwarm"
      climMin={0} climMax={0.005780115723609924} onApply={apply} />
  </>))
  return apply
}

it('updates every bar and both open dialogs after one unit change', () => {
  renderBars()
  open('surface-unit'); open('slice-unit')
  change($('#surface-unit-dialog .pp-cbw-unit'), 'K')
  for (const id of ['surface-unit', 'slice-unit']) {
    expect($('#'+id+' .pp-colorbar-field').textContent).toContain('(K)')
    expect($('#'+id+' .pp-colorbar-max').textContent).toContain(' K')
    expect($('#'+id+'-dialog .pp-cbw-head').textContent).toContain('(K)')
    expect($<HTMLSelectElement>('#'+id+'-dialog .pp-cbw-unit').value).toBe('K')
  }
  expect(Number($<HTMLInputElement>('#surface-unit-dialog .pp-cbw-min').value)).toBeCloseTo(293.15, 10)
  expect(Number($<HTMLInputElement>('#slice-unit-dialog .pp-cbw-min').value)).toBeCloseTo(298.15, 10)
})

it('converts a pending edit once and applies its same Kelvin value', () => {
  const apply=renderBars()
  open('surface-unit'); open('slice-unit')
  change($('#slice-unit-dialog .pp-cbw-min'), '26.125')
  change($('#surface-unit-dialog .pp-cbw-unit'), 'F')
  expect(Number($<HTMLInputElement>('#slice-unit-dialog .pp-cbw-min').value)).toBeCloseTo(79.025, 10)
  change($('#slice-unit-dialog .pp-cbw-unit'), 'K')
  expect(Number($<HTMLInputElement>('#slice-unit-dialog .pp-cbw-min').value)).toBeCloseTo(299.275, 10)
  act(() => $<HTMLButtonElement>('#slice-unit-dialog .pp-cbw-apply').click())
  expect(apply).toHaveBeenCalledTimes(1)
  expect(apply.mock.calls[0]![0]).toBeCloseTo(299.275, 10)
  expect(apply.mock.calls[0]![1]).toBeCloseTo(308.15, 10)
})

it('a dialog opened later uses the current global unit and raw precision', () => {
  renderBars()
  open('surface-unit')
  change($('#surface-unit-dialog .pp-cbw-unit'), 'K')
  open('slice-unit')
  expect($<HTMLSelectElement>('#slice-unit-dialog .pp-cbw-unit').value).toBe('K')
  expect(Number($<HTMLInputElement>('#slice-unit-dialog .pp-cbw-min').value)).toBeCloseTo(298.15, 10)
  open('speed-unit')
  expect($('#speed-unit .pp-colorbar-field').textContent).toContain('(m/s)')
  expect($<HTMLInputElement>('#speed-unit-dialog .pp-cbw-max').value).toBe('0.005780115723609924')
})

it('keeps an empty pending draft empty across an external unit update', () => {
  renderBars()
  open('surface-unit')
  change($('#surface-unit-dialog .pp-cbw-min'), '')
  act(() => setTempUnit('K'))
  expect($<HTMLSelectElement>('#surface-unit-dialog .pp-cbw-unit').value).toBe('K')
  expect($<HTMLInputElement>('#surface-unit-dialog .pp-cbw-min').value).toBe('')
})

it.each(['pp', 'pp-slice', 'pp-vol'])('resynchronizes %s sidebar labels and values without committing a stale focused draft', async (prefix) => {
  await act(async () => root.render(<PostprocessPanel />))
  const min = $<HTMLInputElement>('#'+prefix+'-clim-min')
  act(() => min.focus())
  change(min, '20')
  act(() => min.blur())
  const max = $<HTMLInputElement>('#'+prefix+'-clim-max')
  act(() => max.focus())
  change(max, '30')
  act(() => max.blur())
  const initial = $<HTMLInputElement>('#'+prefix+'-clim-min')
  act(() => initial.focus())
  change(initial, '123')
  act(() => setTempUnit('K'))
  expect($('#postprocess-controls label[for="'+prefix+'-clim-min"]').textContent).toContain('(K)')
  const after = $<HTMLInputElement>('#'+prefix+'-clim-min')
  expect(after).not.toBe(initial)
  const displayedKelvin = 293.15
  expect(Number(after.value)).toBe(displayedKelvin)
  act(() => { after.focus(); after.blur() })
  expect(Number($<HTMLInputElement>('#'+prefix+'-clim-min').value)).toBe(displayedKelvin)
  act(() => setTempUnit('C'))
  expect(Number($<HTMLInputElement>('#'+prefix+'-clim-min').value)).toBe(20)
})


it('updates an open slice editor and refuses a stale focused temperature draft', () => {
  const committed = vi.fn()
  function SliceEditor() {
    const [form, setForm] = useState<SliceEditForm>({
      normalPreset: 'x', normal: [1, 0, 0], origin: [0, 0, 0],
      field: 'T', colorMap: 'coolwarm', climMin: 293.15, climMax: 303.15,
    })
    return <PpSliceEditDialog id={1} form={form} fields={['T', 'Umag']} busy={false}
      onChange={(patch) => { committed(patch); setForm((old) => ({ ...old, ...patch })) }}
      onClose={vi.fn()} onApply={vi.fn()} />
  }
  act(() => root.render(<SliceEditor />))
  const before = $<HTMLInputElement>('#dlg-slice-clim-min')
  act(() => before.focus())
  change(before, '123')
  act(() => setTempUnit('K'))
  expect($('#pp-slice-edit-overlay').textContent).toContain('Min: (K)')
  const after = $<HTMLInputElement>('#dlg-slice-clim-min')
  expect(after).not.toBe(before)
  expect(Number(after.value)).toBeCloseTo(293.15, 10)
  act(() => { after.focus(); after.blur() })
  expect(committed).not.toHaveBeenCalled()
})
