import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { FileBrowser } from '../src/components/FileBrowser'
import { t } from '../src/i18n'

let host: HTMLDivElement
let root: Root
const listing = (current: string) => ({
  current, parent: current === '/cases' ? '/' : '/cases',
  dirs: [{ name: 'child', path: current + '/child', hidden: false, mtime: 0 }],
  files: [{ name: 'mesh-quality-receipt.json', path: current + '/mesh-quality-receipt.json', hidden: false, mtime: 0 }],
})
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
    json: async () => listing(new URL(url, 'http://localhost').searchParams.get('path') ?? '/cases'),
  })))
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
async function render(directory: boolean, onPick: (path: string) => void) {
  await act(async () => root.render(<FileBrowser startPath="/cases" title="Choose" confirmLabel="Open" pickDirectory={directory} onPick={onPick} onCancel={() => {}} />))
}
function click(node: Element, detail: number, type = 'click') {
  node.dispatchEvent(new MouseEvent(type, { bubbles: true, detail }))
}

describe('server directory and file selection', () => {
  it('never offers a regular file as a case directory', async () => {
    const pick = vi.fn()
    await render(true, pick)
    expect(document.querySelectorAll('.fb-file')).toHaveLength(0)
    await act(async () => click(document.querySelector('.fb-dir')!, 1))
    const confirm = [...document.querySelectorAll('.fb-actions button')].find(b => b.textContent === 'Open')!
    act(() => click(confirm, 1))
    expect(pick).toHaveBeenCalledWith('/cases/child')
  })

  it('does not descend again when a new folder row catches click two', async () => {
    const pick = vi.fn()
    await render(true, pick)
    await act(async () => click(document.querySelector('.fb-dir')!, 1))
    await act(async () => click(document.querySelector('.fb-dir')!, 2))
    expect(document.querySelector<HTMLInputElement>('.fb-path input')!.value).toBe('/cases/child')
    expect(pick).not.toHaveBeenCalled()
  })

  it('rejects a stale double-click across listings but opens an intentionally clicked file', async () => {
    const pick = vi.fn()
    await render(false, pick)
    await act(async () => click(document.querySelector('.fb-dir')!, 1))
    const file = document.querySelector('.fb-file')!
    act(() => { click(file, 2); click(file, 2, 'dblclick') })
    expect(pick).not.toHaveBeenCalled()
    act(() => { click(file, 1); click(file, 2); click(file, 2, 'dblclick') })
    expect(pick).toHaveBeenCalledWith('/cases/child/mesh-quality-receipt.json')
  })
})

describe('keyboard selection while a directory is loading', () => {
  it.each([true, false])('blocks Enter just like the button (directory=%s)', async (directory) => {
    const pick = vi.fn()
    await render(directory, pick)
    if (!directory) act(() => click(document.querySelector('.fb-file')!, 1))
    let release!: (value: unknown) => void
    const gate = new Promise(resolve => { release = resolve })
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => await gate })))
    act(() => click(document.querySelector('.fb-dir')!, 1))
    const confirm = [...document.querySelectorAll<HTMLButtonElement>('.fb-actions button')]
      .find(b => b.textContent === 'Open')!
    expect(confirm.disabled).toBe(true)
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    expect(pick).not.toHaveBeenCalled()
    await act(async () => release(listing('/cases/child')))
    if (!directory) act(() => click(document.querySelector('.fb-file')!, 1))
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    expect(pick).toHaveBeenCalledWith(directory ? '/cases/child' : '/cases/child/mesh-quality-receipt.json')
  })
})


// Drive the mounted component and its real /api/create-dir adapter. This host
// deliberately cannot show native prompt(), as the in-app browser cannot.
describe('new folder without native browser dialogs', () => {
  const nameField = () => [...document.querySelectorAll<HTMLInputElement>('input')]
    .find(input => [...(input.labels ?? [])].some(label => label.textContent === t('filebrowser.new_folder_prompt')))
  const posts = () => vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/create-dir')
  function typeName(value: string) {
    const input = nameField()!
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }
  async function openFolder() {
    await act(async () => click(document.querySelector('.fb-new-folder')!, 1))
  }
  function submitName() {
    nameField()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  }
  beforeEach(() => {
    vi.spyOn(window, 'prompt').mockImplementation(() => { throw new Error('prompt() is not supported') })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      json: async () => url === '/api/create-dir' ? { ok: true }
        : listing(new URL(url, 'http://localhost').searchParams.get('path') ?? '/cases'),
    })))
  })

  it.each([true, false])('creates the typed name in the current directory without picking it (directory=%s)', async directory => {
    const pick = vi.fn()
    await render(directory, pick)
    await act(async () => click(document.querySelector('.fb-dir')!, 1))
    await openFolder()
    expect(window.prompt).not.toHaveBeenCalled()
    expect(nameField()).toBeDefined()
    expect(document.activeElement).toBe(nameField())
    act(() => typeName('  液冷 run  '))
    await act(async () => submitName())
    expect(posts()).toHaveLength(1)
    expect(JSON.parse(posts()[0]![1]!.body as string)).toEqual({ path: '/cases/child/液冷 run' })
    expect(pick).not.toHaveBeenCalled()
    expect(nameField()).toBeUndefined()
    expect(document.querySelector<HTMLInputElement>('.fb-path input')!.value).toBe('/cases/child')
  })

  it('cancels a name with Escape without closing the parent or creating anything', async () => {
    const cancel = vi.fn(), pick = vi.fn()
    await act(async () => root.render(<FileBrowser startPath="/cases" title="Choose" confirmLabel="Open" pickDirectory onPick={pick} onCancel={cancel} />))
    await openFolder()
    expect(nameField()).toBeDefined()
    act(() => {
      typeName('not-created')
      nameField()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    })
    expect(nameField()).toBeUndefined()
    expect(posts()).toHaveLength(0)
    expect(cancel).not.toHaveBeenCalled()
    expect(pick).not.toHaveBeenCalled()
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('keeps a failed name and shows the server error so it can be corrected and retried', async () => {
    await render(true, vi.fn())
    let attempts = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ json: async () => url === '/api/create-dir'
      ? (++attempts === 1 ? { ok: false, error: 'folder exists' } : { ok: true })
      : listing('/cases') })))
    await openFolder()
    expect(nameField()).toBeDefined()
    act(() => typeName('existing'))
    await act(async () => submitName())
    expect(document.querySelector('[role="alert"]')?.textContent).toBe('folder exists')
    expect(nameField()!.value).toBe('existing')
    act(() => typeName('new-case'))
    await act(async () => submitName())
    expect(posts()).toHaveLength(2)
    expect(JSON.parse(posts()[1]![1]!.body as string)).toEqual({ path: '/cases/new-case' })
    expect(nameField()).toBeUndefined()
  })

  it('does not POST an empty or whitespace-only name, and the Cancel button abandons it', async () => {
    await render(true, vi.fn())
    await openFolder()
    expect(nameField()).toBeDefined()
    await act(async () => submitName())
    act(() => typeName('  '))
    await act(async () => submitName())
    expect(posts()).toHaveLength(0)
    const form = nameField()!.closest('form')!
    const cancel = [...form.querySelectorAll('button')].find(b => b.textContent === t('common.cancel'))!
    act(() => click(cancel, 1))
    expect(nameField()).toBeUndefined()
    expect(posts()).toHaveLength(0)
  })

  it('prevents repeated Enter and case selection while the creation request is pending', async () => {
    const pick = vi.fn()
    await render(true, pick)
    let finish!: (value: {ok: boolean}) => void
    const gate = new Promise<{ok: boolean}>(resolve => { finish = resolve })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ json: async () => url === '/api/create-dir' ? await gate : listing('/cases') })))
    await openFolder()
    expect(nameField()).toBeDefined()
    act(() => { typeName('pending'); submitName(); submitName() })
    expect(posts()).toHaveLength(1)
    const confirm = [...document.querySelectorAll<HTMLButtonElement>('.fb-actions button')].find(b => b.textContent === 'Open')!
    expect(confirm.disabled).toBe(true)
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      click(document.querySelector('.fb-dir')!, 1)
    })
    expect(pick).not.toHaveBeenCalled()
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
    await act(async () => finish({ok: true}))
    expect(nameField()).toBeUndefined()
    expect(confirm.disabled).toBe(false)
  })
})
