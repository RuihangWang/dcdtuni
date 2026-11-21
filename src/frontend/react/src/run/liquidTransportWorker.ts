import type { LiquidTransportInput, LiquidTransportProduct, PreparedLiquidTransport } from './liquidTransportProtocol'

export function throwIfPreparationAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException('Run preparation cancelled', 'AbortError')
}

export async function yieldPreparation(signal?: AbortSignal): Promise<void> {
  throwIfPreparationAborted(signal)
  // A macrotask lets the overlay, stop control, and video render before CPU work.
  await new Promise<void>(resolve => setTimeout(resolve, 0))
  throwIfPreparationAborted(signal)
}

export function prepareLiquidTransport(input: LiquidTransportInput, signal?: AbortSignal,
  createWorker: () => Worker = () => new Worker(new URL('./liquidTransport.worker.ts', import.meta.url), { type: 'module' }),
): Promise<PreparedLiquidTransport> {
  throwIfPreparationAborted(signal)
  const ids = new Set(input.modelIds)
  if (!input.models.some(model => ids.has(model.uid) && (model.kind === 'pipe_segment' || model.kind === 'pipe_junction'))) {
    return Promise.resolve({ input, product: null })
  }
  return new Promise((resolve, reject) => {
    const worker = createWorker()
    let settled = false
    const cleanup = () => {
      signal?.removeEventListener('abort', abort)
      worker.onmessage = null
      worker.onerror = null
      worker.onmessageerror = null
      worker.terminate()
    }
    const finish = (error?: Error, product?: LiquidTransportProduct | null) => {
      if (settled) return
      settled = true
      cleanup()
      if (error) reject(error)
      else resolve({ input, product: product ?? null })
    }
    const abort = () => finish(new DOMException('Run preparation cancelled', 'AbortError'))
    worker.onmessage = event => {
      if (signal?.aborted) { abort(); return }
      const reply = event.data as { ok?: boolean; error?: string; product?: LiquidTransportProduct | null }
      if (reply.ok === true) finish(undefined, reply.product)
      else finish(new Error(reply.error || 'Liquid transport worker failed'))
    }
    worker.onerror = event => finish(new Error(event.message || 'Liquid transport worker failed'))
    worker.onmessageerror = () => finish(new Error('Liquid transport worker response could not be decoded'))
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) { abort(); return }
    try { worker.postMessage(input) } catch (error) {
      finish(error instanceof Error ? error : new Error(String(error)))
    }
  })
}
