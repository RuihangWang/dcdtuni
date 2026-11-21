import { useEffect, useState } from 'react'
import { useTranslation } from '../useTranslation'
import { gib } from './ScanoGpuStatus'

/** The solve page's live GPU block while the GPU engine is selected (GET /api/gpu-solver/status): every card the
 * launch gave the solver with its memory and utilisation, what the solver does on it, the mesh size with the
 * memory one solve needs at most, and the out-of-memory message of the last solve. Polled while shown. */

export interface GpuSolverCard {
  index: string
  uuid: string
  name: string
  totalMiB: number
  usedMiB: number
  freeMiB: number
  utilization: number | null
  /** what the solver does on this card */
  solver: 'solving' | 'standby' | null
}

export interface GpuSolverReply {
  enabled: boolean
  devices: string[] | null
  cards: GpuSolverCard[]
  /** cells: the mesh's cells (a solve takes the open region, at most this many); estimateMiB: for all of them */
  mesh: { cells: number | null; estimateMiB: number | null; bestFreeMiB: number | null; fits: boolean | null } | null
  message: string | null
}

/** A solve that ended because the GPU solver ran out of memory (job status `solverFailure`). */
export interface SolverFailure {
  code: string
  error: string
}

const POLL_MS = 2000
const mib = (value: number | null | undefined) => gib(value == null ? null : value * 2 ** 20)

export function GpuSolverStatus({ failure }: { failure?: SolverFailure | null }) {
  const { t } = useTranslation()
  const [reply, setReply] = useState<GpuSolverReply | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      if (document.visibilityState === 'visible') {
        try {
          const resp = await fetch('/api/gpu-solver/status', { cache: 'no-store' })
          const body = (await resp.json()) as GpuSolverReply & { error?: string }
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
  }, [])

  const alert = failure?.code === 'gpu_out_of_memory' && (
    <div className="gpu-solver-alert" id="gpu-solver-failure" role="alert">
      <b>{t('solver.gpu.oom_title')}</b>
      <div>{t('solver.gpu.oom_action')}</div>
      <div className="scano-gpu-dim">{failure.error}</div>
    </div>
  )
  if (error && !reply) {
    return <div className="scano-gpu prop-hint" id="gpu-solver-status">{alert}{t('scano.gpu.unavailable', { e: error })}</div>
  }
  if (!reply) return <div className="scano-gpu prop-hint" id="gpu-solver-status">{alert}{t('scano.gpu.reading')}</div>
  const mesh = reply.mesh
  const tight = mesh?.fits === false
  return (
    <div className="scano-gpu" id="gpu-solver-status" data-fits={mesh?.fits == null ? '' : String(mesh.fits)}>
      {alert}
      {reply.cards.length === 0 && <div className="scano-gpu-row" id="gpu-solver-no-cards">{t('solver.gpu.no_cards')}</div>}
      {reply.cards.map((card) => {
        const used = Math.min(100, (100 * card.usedMiB) / card.totalMiB)
        // the estimate is drawn on the card a solve would take: the one with the most free memory
        const target = mesh?.estimateMiB != null && card.freeMiB === mesh.bestFreeMiB && card.solver !== 'solving'
        return (
          <div className="gpu-solver-card" key={card.uuid} data-card={card.index} data-solver={card.solver ?? ''}>
            <div className="scano-gpu-row">
              <span className="scano-gpu-dot"
                data-state={card.solver === 'solving' ? 'busy' : card.solver === 'standby' ? 'ready' : 'off'} />
              <span>{t('scano.gpu.device', { i: card.index, name: card.name.replace(/^NVIDIA (GeForce )?/, '') })}</span>
              <span className="scano-gpu-dim">
                {card.solver === 'solving' ? t('solver.gpu.solving')
                  : card.solver === 'standby' ? t('solver.gpu.standby') : t('solver.gpu.idle')}
                {card.utilization != null ? ` · ${t('solver.gpu.utilization', { u: String(card.utilization) })}` : ''}
              </span>
            </div>
            <div className="scano-gpu-bar" title={t('solver.gpu.bar_hint')}>
              <span style={{ width: `${used}%` }} />
              {target && (
                <i style={{ left: `${used}%`,
                  width: `${Math.min(100 - used, (100 * mesh!.estimateMiB!) / card.totalMiB)}%` }} />
              )}
            </div>
            <div className="scano-gpu-row">
              <span>{t('scano.gpu.memory', { used: mib(card.usedMiB), total: mib(card.totalMiB) })}</span>
              <span className="scano-gpu-dim">{t('solver.gpu.free', { free: mib(card.freeMiB) })}</span>
            </div>
          </div>
        )
      })}
      {mesh?.cells != null && (
        <div className="scano-gpu-row scano-gpu-dim scano-gpu-plan" id="gpu-solver-mesh">
          {t('solver.gpu.mesh', { n: (mesh.cells / 1e6).toFixed(2), est: mib(mesh.estimateMiB) })}
        </div>
      )}
      {tight && (
        <div className="scano-gpu-row gpu-solver-warn" id="gpu-solver-no-fit">
          {t('solver.gpu.no_fit', { est: mib(mesh!.estimateMiB), free: mib(mesh!.bestFreeMiB) })}
        </div>
      )}
      <div className="scano-gpu-row scano-gpu-dim" id="gpu-solver-note">{t('solver.gpu.one_card')}</div>
    </div>
  )
}
