// The solve tab's solver-type row is read-only (the top bar is its one writer)
// and says what the AIR solve will run (E8b: air and liquid have their own
// modes, so a hybrid — liquid transient, air steady — simply reads steady).
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { SolverPanel } from '../src/components/SolverPanel'
import * as store from '../src/projectStore'
import { getLocale, setLocale, t } from '../src/i18n'
import { readProjectFileText } from '../src/io/projectFiles'
import { setSimulationMode } from '../src/transient/simulationMode'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let prev: ReturnType<typeof getLocale>
beforeEach(() => { prev = getLocale(); setLocale('zh-CN'); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(() => { root.unmount(); host.remove(); setLocale(prev) })
const open = (rel: string) => {
  const r = readProjectFileText(readFileSync(resolve(process.cwd(), '../../..', rel), 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  flushSync(() => root.render(<SolverPanel project={store.getProject()} />))
}
const row = () => host.querySelector('#slv-mode')!.textContent!

it('no mode radios or end-time box; the row follows the top bar', () => {
  open('case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')
  expect(host.querySelector('#slv-type-steady')).toBeNull()
  expect(row()).toContain(t('transient.mode.air_hint'))
  setSimulationMode('transient', 'air')
  flushSync(() => root.render(<SolverPanel project={store.getProject()} />))
  expect(row()).toContain(t('transient.mode.transient'))
  expect(host.querySelector('input#slv-end-time-s')).toBeNull()
  expect(host.querySelector('#slv-end-time-s')!.textContent).toContain('600')
})

it('a hybrid (transient timeline, steady air) reads steady, pointing at the air view of the top bar', () => {
  open('src/frontend/react/test/fixtures/cases/NV_PEK02_mock.cfdproj')
  expect((store.getProject() as any).transient).toMatchObject({ mode: 'transient', airMode: 'steady' })
  expect(row()).toContain(t('transient.mode.steady'))
  expect(row()).toContain(t('transient.mode.air_hint'))
})
