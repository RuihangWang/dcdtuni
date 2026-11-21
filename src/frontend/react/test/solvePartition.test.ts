import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmSolvePartition } from '../src/run/solvePartition'
import { setLocale, t } from '../src/i18n'

const settle = async () => { for (let i = 0; i < 3; i++) await Promise.resolve() }
const dialog = () => document.getElementById('solve-partition-warning-dialog')
const click = (id: string) => document.getElementById(id)!.click()
const config = () => ({ parallelSolve: true, solveProcess: 4 })
const status = (data: unknown) => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => data })
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}

afterEach(() => {
  if (dialog()) document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.useRealTimers()
  setLocale('zh-CN')
})

describe('solve partition confirmation', () => {
  it('shows the actual mesh partitions and preserves solver settings on cancel', async () => {
    const fetcher = status({ meshReady: true, decomposed: true, nPartitions: 8 })
    const settings = config()
    const pending = confirmSolvePartition(() => settings)
    await settle()
    expect(fetcher).toHaveBeenCalledWith('/api/check-mesh', expect.objectContaining({ cache: 'no-store' }))
    expect(dialog()?.textContent).toContain(t('dialog.solve_partition_counts', { mesh: 8, solve: 4 }))
    expect(dialog()?.textContent).toContain(t('dialog.solve_partition_parallel'))
    expect(dialog()?.querySelector('[role="dialog"]')?.getAttribute('aria-modal')).toBe('true')
    expect(document.activeElement?.id).toBe('solve-partition-cancel-btn')
    click('solve-partition-cancel-btn')
    expect(await pending).toBe(false)
    expect(settings).toEqual(config())
    expect(dialog()).toBeNull()
  })

  it('continues only after confirmation and also supports English', async () => {
    setLocale('en-US')
    status({ meshReady: true, decomposed: true, nPartitions: 8 })
    const pending = confirmSolvePartition(config)
    await settle()
    expect(dialog()?.textContent).toContain('Existing mesh: 8 partitions; requested solve: 4 core(s).')
    click('solve-partition-continue-btn')
    expect(await pending).toBe(true)
  })

  it.each([
    { meshReady: true, decomposed: true, nPartitions: 4 },
    { meshReady: true, decomposed: false, nPartitions: 0 },
  ])('does not interrupt a reusable layout or a serial root mesh', async (mesh) => {
    status(mesh)
    expect(await confirmSolvePartition(config)).toBe(true)
    expect(dialog()).toBeNull()
  })

  it.each([
    { parallelSolve: false, solveProcess: 8 },
    { parallelSolve: true, solveProcess: 1 },
  ])('explains serial reconstruction and lets Escape cancel', async (settings) => {
    status({ meshReady: true, decomposed: true, nPartitions: 8 })
    const pending = confirmSolvePartition(() => settings)
    await settle()
    expect(dialog()?.textContent).toContain(t('dialog.solve_partition_counts', { mesh: 8, solve: 1 }))
    expect(dialog()?.textContent).toContain(t('dialog.solve_partition_serial'))
    expect(dialog()?.textContent).not.toContain(t('dialog.solve_partition_parallel'))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(await pending).toBe(false)
  })

  it('reads current settings after the asynchronous mesh probe', async () => {
    let finish!: (value: unknown) => void
    vi.stubGlobal('fetch', () => new Promise((resolve) => { finish = resolve }))
    const settings = config()
    const pending = confirmSolvePartition(() => settings)
    settings.solveProcess = 8
    finish({ ok: true, json: async () => ({ meshReady: true, decomposed: true, nPartitions: 8 }) })
    expect(await pending).toBe(true)
    expect(dialog()).toBeNull()
  })

  it('does not suggest one-core parallel reuse for a single stored processor', async () => {
    status({ meshReady: true, decomposed: true, nPartitions: 1 })
    const pending = confirmSolvePartition(() => ({ parallelSolve: false, solveProcess: 1 }))
    await settle()
    expect(dialog()?.textContent).toContain(t('dialog.solve_partition_serial'))
    expect(dialog()?.textContent).not.toContain(t('dialog.solve_partition_hint', { mesh: 1 }))
    click('solve-partition-cancel-btn')
    expect(await pending).toBe(false)
  })

  it.each([
    {}, { meshReady: true },
    { meshReady: true, decomposed: true, nPartitions: 0 },
    { meshReady: true, decomposed: true, nPartitions: '8' },
  ])('refuses an unknown partition layout instead of silently submitting', async (mesh) => {
    status(mesh)
    await expect(confirmSolvePartition(config)).rejects.toThrow(t('error.solve_partition_check_failed'))
  })

  it('reports a removed mesh', async () => {
    status({ meshReady: false })
    await expect(confirmSolvePartition(config)).rejects.toThrow(t('error.mesh_first'))
  })

  it('times out a stalled mesh probe', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', (_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal!.addEventListener('abort', () => reject(new Error('timeout')))
    }))
    const pending = expect(confirmSolvePartition(config)).rejects.toThrow(t('error.solve_partition_check_failed'))
    await vi.advanceTimersByTimeAsync(10000)
    await pending
    expect(dialog()).toBeNull()
  })
})
