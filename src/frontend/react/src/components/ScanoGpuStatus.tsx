import { useEffect, useState } from 'react'
import { useTranslation } from '../useTranslation'
import type { ScanoInference } from '../run/scanoInference'

/** The AI solve tab's live GPU line (GET /api/ai/scano/gpu): the worker's GPU and its memory, the room's size, and
 * the deployment the server would pick for the current choice with its estimated peak. Polled while shown. */

export interface ScanoGpuReply {
  running: boolean
  busy: boolean
  cpu: boolean
  gpu: {
    index: string
    uuid: string
    name: string
    totalMiB: number
    usedMiB: number
    freeMiB: number
    utilization: number | null
    workerMiB: number | null
  } | null
  freeForAiBytes: number | null
  /** busy: freeForAiBytes is the free memory read before the running request */
  freeMeasuredWhileIdle?: boolean
  workerCacheBytes?: number | null
  room: { nodes: number; cells: number } | null
  plan?: {
    mode: 'whole' | 'tta'
    reason: string
    fits: boolean
    estimate_bytes: number
    chunk_share: number
    passes: number | null
    forwards: number
    wholeBytes: number
    ttaBytes: number
    measured: string[]
  }
}

const POLL_MS = 2000

export function gib(bytes: number | null | undefined): string {
  return bytes == null ? '–' : (bytes / 2 ** 30).toFixed(1)
}

export function ScanoGpuStatus({ model, choice }: { model: string; choice: ScanoInference }) {
  const { t } = useTranslation()
  const [reply, setReply] = useState<ScanoGpuReply | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const query = new URLSearchParams({ model, inference: choice.inference, passes: String(choice.passes) })
    async function poll() {
      if (document.visibilityState === 'visible') {
        try {
          const resp = await fetch(`/api/ai/scano/gpu?${query}`, { cache: 'no-store' })
          const body = (await resp.json()) as ScanoGpuReply & { error?: string }
          if (!live) return
          if (!resp.ok || body.error) throw new Error(body.error ?? String(resp.status))
          setReply(body)
          setError(null)
        } catch (err) {
          if (live) setError(err instanceof Error ? err.message : String(err))
        }
      }
      if (live) timer = setTimeout(() => void poll(), POLL_MS)
    }
    void poll()
    return () => {
      live = false
      if (timer) clearTimeout(timer)
    }
  }, [model, choice.inference, choice.passes])

  if (error && !reply) return <div className="scano-gpu prop-hint" id="scano-gpu">{t('scano.gpu.unavailable', { e: error })}</div>
  if (!reply) return <div className="scano-gpu prop-hint" id="scano-gpu">{t('scano.gpu.reading')}</div>
  const g = reply.gpu
  const plan = reply.plan
  return (
    <div className="scano-gpu" id="scano-gpu" data-mode={plan?.mode ?? ''} data-fits={plan ? String(plan.fits) : ''}>
      {g ? (
        <>
          <div className="scano-gpu-row" id="scano-gpu-device">
            <span className="scano-gpu-dot" data-state={reply.busy ? 'busy' : reply.running ? 'ready' : 'off'} />
            <span>{t('scano.gpu.device', { i: g.index, name: g.name.replace(/^NVIDIA (GeForce )?/, '') })}</span>
            <span className="scano-gpu-dim">
              {reply.busy ? t('scano.gpu.busy') : reply.running ? t('scano.gpu.ready') : t('scano.gpu.off')}
              {g.utilization != null ? ` · ${g.utilization}%` : ''}
            </span>
          </div>
          <div className="scano-gpu-bar" title={t('scano.gpu.bar_hint')}>
            <span style={{ width: `${Math.min(100, (100 * g.usedMiB) / g.totalMiB)}%` }} />
            {plan && (
              <i style={{
                left: `${Math.min(100, (100 * g.usedMiB) / g.totalMiB)}%`,
                width: `${Math.min(100 - (100 * g.usedMiB) / g.totalMiB, (100 * plan.estimate_bytes) / (g.totalMiB * 2 ** 20))}%`,
              }} />
            )}
          </div>
          <div className="scano-gpu-row" id="scano-gpu-memory">
            <span>{t('scano.gpu.memory', { used: gib(g.usedMiB * 2 ** 20), total: gib(g.totalMiB * 2 ** 20) })}</span>
            <span className="scano-gpu-dim">
              {t(reply.freeMeasuredWhileIdle ? 'scano.gpu.free_before' : 'scano.gpu.free', { free: gib(reply.freeForAiBytes) })}
            </span>
          </div>
          {g.workerMiB != null && (
            <div className="scano-gpu-row scano-gpu-dim" id="scano-gpu-worker">
              {t('scano.gpu.worker', { m: gib(g.workerMiB * 2 ** 20), c: gib(reply.workerCacheBytes ?? 0) })}
            </div>
          )}
        </>
      ) : (
        <div className="scano-gpu-row" id="scano-gpu-device">{t('scano.gpu.cpu')}</div>
      )}
      {reply.room && (
        <div className="scano-gpu-row scano-gpu-dim" id="scano-gpu-room">
          {t('scano.gpu.room', { n: (reply.room.nodes / 1e6).toFixed(2) })}
        </div>
      )}
      {plan && (
        <div className={`scano-gpu-plan${plan.fits ? '' : ' warn'}`} id="scano-gpu-plan">
          <div>
            {t(plan.mode === 'tta' ? 'scano.gpu.plan_tta' : 'scano.gpu.plan_whole', {
              est: gib(plan.estimate_bytes), n: plan.forwards, r: plan.passes ?? 1,
            })}
            {plan.reason === 'whole does not fit' ? ` · ${t('scano.gpu.whole_too_big', { w: gib(plan.wholeBytes) })}` : ''}
            {plan.reason === 'model prefers TTA' ? ` · ${t('scano.gpu.prefers_tta')}` : ''}
          </div>
          <div className="scano-gpu-dim">
            {t('scano.gpu.both', { w: gib(plan.wholeBytes), t: gib(plan.ttaBytes) })}
            {plan.measured.length ? ` · ${t('scano.gpu.measured')}` : ''}
          </div>
          {!plan.fits && <div>{t('scano.gpu.no_fit')}</div>}
        </div>
      )}
    </div>
  )
}
