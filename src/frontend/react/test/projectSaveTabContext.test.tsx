/** @vitest-environment jsdom */
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ProjectFileDialog } from '../src/components/ProjectFileDialog'
import { rememberRemoteProjectPath } from '../src/io/projectFiles'
import type { CfdProject } from '../src/schema/cfdproj'
vi.mock('../src/useTranslation', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
let host: HTMLDivElement, root: Root
beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => ({ current: '/cases/A', parent: '/cases', dirs: [], files: [] }) })))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals() })
const project = { schemaVersion: 5, uid: 'A', name: 'A', meta: { projectName: 'A' }, geometry: { height: 3, plane: [] }, models: [], constructions: { racks: {} } } as CfdProject
it.each(['/cases/A/other-tab.cfdproj', '/cases/B/other-tab.cfdproj'])('saves the open tab project after another tab remembers %s', async (foreignPath) => {
  rememberRemoteProjectPath(foreignPath)
  await act(async () => root.render(<ProjectFileDialog mode="save" project={project} currentProjectPath="/cases/A/original.cfdproj" startPath="/cases/A" onLoaded={() => {}} onSaved={() => {}} onLocalSaved={() => {}} onPickLocalFile={() => {}} onClose={() => {}} />))
  expect((host.querySelector('.lp-selected-path') as HTMLInputElement).value).toBe('/cases/A/original.cfdproj')
})
it('offers a fresh project name when this tab has no open remote file', async () => {
  rememberRemoteProjectPath('/cases/A/other-tab.cfdproj')
  await act(async () => root.render(<ProjectFileDialog mode="save" project={project} currentProjectPath={null} startPath="/cases/A" onLoaded={() => {}} onSaved={() => {}} onLocalSaved={() => {}} onPickLocalFile={() => {}} onClose={() => {}} />))
  expect((host.querySelector('.lp-selected-path') as HTMLInputElement).value).toBe('/cases/A/A.cfdproj')
})
