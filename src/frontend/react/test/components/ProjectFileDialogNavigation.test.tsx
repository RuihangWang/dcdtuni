import { act, StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ProjectFileDialog } from '../../src/components/ProjectFileDialog'
import * as files from '../../src/io/projectFiles'
import { getLocale, setLocale } from '../../src/i18n'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
type Listing = { current: string; parent: string | null; dirs: unknown[]; files: unknown[] }
function listing(path: string): Listing {
  return { current: path, parent: '/', dirs: [], files: [{ name: 'case.cfdproj', path: `${path}/case.cfdproj`, hidden: false, mtime: 1 }] }
}
function deferred() {
  let resolve!: (body: Listing | { error: string }) => void
  let reject!: (error: Error) => void
  const promise = new Promise<Response>((accept, fail) => {
    resolve = body => accept({ json: async () => body, status: 200, ok: true } as Response)
    reject = fail
  })
  return { promise, resolve, reject }
}
let host: HTMLDivElement
let root: Root
let oldLocale: ReturnType<typeof getLocale>
let fetchMock: ReturnType<typeof vi.fn>
let onClose: ReturnType<typeof vi.fn>
beforeEach(() => {
  oldLocale = getLocale(); setLocale('zh-CN')
  vi.spyOn(files, 'getCurrentRemoteProjectFile').mockReturnValue('/old/case.cfdproj')
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  onClose = vi.fn()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setLocale(oldLocale) })
function render(strict = false) {
  const dialog = <ProjectFileDialog mode="load" project={null} startPath="/old"
    onLoaded={vi.fn()} onSaved={vi.fn()} onLocalSaved={vi.fn()} onPickLocalFile={vi.fn()} onClose={onClose} />
  act(() => root.render(strict ? <StrictMode>{dialog}</StrictMode> : dialog))
}
const pathBar = () => host.querySelector<HTMLInputElement>('.lp-path-bar')!
function navigate(path: string) {
  const input = pathBar()
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, path)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  act(() => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })) })
}
async function complete(request: ReturnType<typeof deferred>, body: Listing | { error: string }) {
  await act(async () => { request.resolve(body) })
}
function expectDirectory(path: string) {
  expect(pathBar().value).toBe(path)
  expect(host.querySelector(`[data-path="${path}/case.cfdproj"]`)).not.toBeNull()
}
it('keeps the latest navigation and selection when the slow initial directory finishes last', async () => {
  const old = deferred(), next = deferred()
  fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
  render()
  navigate('/new')
  await complete(next, listing('/new'))
  const row = host.querySelector<HTMLElement>('[data-path="/new/case.cfdproj"]')!
  act(() => { row.click() })
  expect(host.querySelector<HTMLInputElement>('.lp-selected-path')!.value).toBe('/new/case.cfdproj')
  await complete(old, listing('/old'))
  expectDirectory('/new')
  expect(host.querySelector<HTMLInputElement>('.lp-selected-path')!.value).toBe('/new/case.cfdproj')
  expect(host.querySelector('[data-path="/old/case.cfdproj"]')).toBeNull()
})
it('ignores an obsolete initial error instead of starting a home fallback over a new navigation', async () => {
  const old = deferred(), next = deferred()
  fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
  render(); navigate('/new')
  await complete(next, listing('/new'))
  await complete(old, { error: 'old path no longer exists' })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expectDirectory('/new')
  expect(host.textContent).not.toContain('old path no longer exists')
})
it('keeps loading for the active request when an older request fails', async () => {
  const old = deferred(), next = deferred()
  fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
  render(); navigate('/new')
  await act(async () => { old.reject(new Error('stale failure')) })
  expect(host.textContent).toContain('正在加载目录')
  expect(host.textContent).not.toContain('stale failure')
  await complete(next, listing('/new'))
  expectDirectory('/new')
})
it('invalidates pending responses as soon as the dialog closes, before any delayed unmount', async () => {
  const old = deferred()
  fetchMock.mockReturnValueOnce(old.promise)
  render()
  act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
  expect(onClose).toHaveBeenCalledOnce()
  await complete(old, listing('/old'))
  expect(host.querySelector('[data-path="/old/case.cfdproj"]')).toBeNull()
})
it('handles a current initial home fallback as one request generation', async () => {
  const old = deferred(), home = deferred()
  fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(home.promise)
  render()
  await complete(old, { error: 'gone' })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  await complete(home, listing('/home'))
  expectDirectory('/home')
})
it('restores a fresh initial request after StrictMode cleanup and never applies its stale first response', async () => {
  const stale = deferred(), fresh = deferred()
  fetchMock.mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise)
  render(true)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  await complete(fresh, listing('/fresh'))
  await complete(stale, listing('/stale'))
  expectDirectory('/fresh')
})

it('fences a home fallback already in flight when newer navigation takes over', async () => {
  const old = deferred(), home = deferred(), next = deferred()
  fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(home.promise).mockReturnValueOnce(next.promise)
  render()
  await complete(old, { error: 'gone' })
  navigate('/new')
  await complete(next, listing('/new'))
  await complete(home, listing('/home'))
  expectDirectory('/new')
  expect(host.querySelector('[data-path="/home/case.cfdproj"]')).toBeNull()
})
