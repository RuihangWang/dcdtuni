// The .liquidrun v4 endpoint forms are chosen in the inspector (plan T3b.1):
// the CDU's secondary-supply and pump control, and each cold plate's heat form.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import * as store from '../src/projectStore'
import { getLocale, setLocale } from '../src/i18n'
import { readProjectFileText } from '../src/io/projectFiles'
import { CduProperties } from '../src/components/CduInspector'
import { ColdPlateSection } from '../src/components/ColdPlateSection'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let prev: ReturnType<typeof getLocale>
beforeEach(() => {
  prev = getLocale(); setLocale('zh-CN')
  const r = readProjectFileText(readFileSync(resolve(process.cwd(), '../../../case/LiquidSingle/LiquidSingle.cfdproj'), 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); setLocale(prev) })
const model = (uid: string) => store.findConstruction(store.getProject()!, uid)!.model as any
const choose = (id: string, value: string) => act(() => {
  const el = host.querySelector(`#${id}`) as HTMLSelectElement
  el.value = value
  el.dispatchEvent(new Event('change', { bubbles: true }))
})

it('the CDU holds its secondary supply and its pump flow when asked', () => {
  const render = () => act(() => root.render(<CduProperties project={store.getProject()!} model={model('m_1')} />))
  render()
  expect((host.querySelector('#cdu-secondary-control') as HTMLSelectElement).value).toBe('physics')
  choose('cdu-secondary-control', 'setpoint'); render()
  choose('cdu-pump-control', 'flow'); render()
  expect(model('m_1').cooling.secondaryControl).toBe('setpoint')
  expect(model('m_1').cooling.pumpControl).toBe('flow')
  expect((host.querySelector('#cdu-pump-control') as HTMLSelectElement).value).toBe('flow')
})

it('a cold plate switches to a coolant temperature rise in one edit', () => {
  const render = () => act(() => root.render(<ColdPlateSection model={model('m_2')} serverId="e_2" />))
  render()
  expect(host.querySelector('#cpcu-0-Q_W')).not.toBeNull()
  expect(host.querySelector('#cpcu-0-dT_K')).toBeNull()
  const depth = store.undoDepth()
  choose('cpcu-0-heat-mode', 'temperature_rise'); render()
  expect(store.undoDepth()).toBe(depth + 1)
  const pair = model('m_2').constructions.servers.e_2.liquidCooling.cpCuPairs[0]
  expect(pair.heatMode).toBe('temperature_rise')
  expect(pair.dT_K).toBe(5)
  expect(host.querySelector('#cpcu-0-Q_W')).toBeNull()
  expect((host.querySelector('#cpcu-0-dT_K') as HTMLInputElement).value).toBe('5')
})
