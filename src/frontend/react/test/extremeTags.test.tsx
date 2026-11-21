import { afterEach, beforeEach, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ExtremeTags } from '../src/components/ExtremeTags'
import { setExtremeTags, type ExtremeMarker } from '../src/postprocess/ppExtremes'

// The viewport overlay: one tag per max and min, each closable; closing survives updates until extremes go off.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const marker = (id: string): ExtremeMarker => ({
  id, maxLabel: `${id} max`, minLabel: `${id} min`,
  pair: { max: { value: 2, position: [1, 1, 1] }, min: { value: 1, position: [0, 0, 0] } },
})
let root: Root
let host: HTMLDivElement
const tags = () => [...document.querySelectorAll<HTMLElement>('[data-extreme-tag]')].map((t) => t.dataset.extremeTag)

beforeEach(() => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root.render(<ExtremeTags />))
})
afterEach(() => { act(() => root.unmount()); host.remove() })

it('shows a closable tag for every max and min, and reopens them once extremes are switched off', () => {
  act(() => setExtremeTags([marker('surface'), marker('volume')]))
  expect(tags()).toEqual(['surface:max', 'surface:min', 'volume:max', 'volume:min'])
  expect(document.querySelectorAll('.extreme-tag-lines circle')).toHaveLength(4)
  act(() => document.querySelector<HTMLButtonElement>('[data-extreme-tag="volume:min"] .extreme-tag-close')!.click())
  expect(tags()).toEqual(['surface:max', 'surface:min', 'volume:max'])
  act(() => setExtremeTags([marker('surface'), { ...marker('volume'), maxLabel: 'volume max 2' }]))
  expect(tags()).not.toContain('volume:min')
  act(() => setExtremeTags([]))
  expect(tags()).toEqual([])
  act(() => setExtremeTags([marker('volume')]))
  expect(tags()).toEqual(['volume:max', 'volume:min'])
})
