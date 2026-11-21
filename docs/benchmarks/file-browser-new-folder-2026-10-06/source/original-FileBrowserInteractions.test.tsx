import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { FileBrowser } from '../src/components/FileBrowser'

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
