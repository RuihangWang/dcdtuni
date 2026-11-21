import { useEffect, useState, useSyncExternalStore } from 'react'
import * as store from '../projectStore'
import * as session from '../run/session'
import { meshConfigOf } from '../run/meshConfig'
import { requestPostprocessRun } from '../postprocess/ppRunRequest'
import type { CfdProject } from '../schema/cfdproj'
import { useTranslation } from '../useTranslation'
import { SCANO_MODEL_KEY } from '../run/solveInit'
import { NumberField } from './NumberField'
import { MeshChangeNote, useMeshBlock } from './MeshChangeNote'
import { meshBlockNow } from '../run/meshChange'
import { Icon } from './Icon'
import { Hint } from './Hint'
import { useAppSelection } from '../selectionBus'
import {
  defaultRoiBox, getScanoRoi, modelBounds, roiFitTarget, scanoRoiRequest, setScanoRoi, showScanoRoiInViewport,
  subscribeScanoRoi,
} from '../run/scanoRoi'
import type { Vec3 } from '../postprocess/ppDragMath'
import {
  MAX_PASSES, getScanoInference, scanoInferenceRequest, setScanoInference, subscribeScanoInference,
  type ScanoInferenceMode,
} from '../run/scanoInference'
import { ScanoGpuStatus, gib } from './ScanoGpuStatus'

/** The solve tab's AI sub-tab. Models and their cards come from the server, so a new checkpoint needs no change here. */

interface ModelCard {
  id: string
  name?: string
  name_zh?: string
  description?: string
  description_zh?: string
  recommended?: boolean
  test_error?: number
  test_rmse_T_K?: number
  mesh?: { engine?: string }
  error?: string
}

function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function remember(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* private window */
  }
}

export interface ScanoSolvePanelProps {
  project: CfdProject | null
  onOpenPostprocess?: () => void
}

export function ScanoSolvePanel({ project, onOpenPostprocess }: ScanoSolvePanelProps) {
  const { t, locale } = useTranslation()
  const [, bump] = useState(0)
  const [models, setModels] = useState<ModelCard[]>([])
  const [model, setModel] = useState<string>(() => remembered(SCANO_MODEL_KEY) ?? '')
  const [error, setError] = useState<string | null>(null)
  const [preparing, setPreparing] = useState(false)
  const meshBlock = useMeshBlock()
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  const roi = useSyncExternalStore(subscribeScanoRoi, getScanoRoi)
  const inference = useSyncExternalStore(subscribeScanoInference, getScanoInference)
  useAppSelection()
  const fitTarget = roiFitTarget()
  const fitModel = fitTarget && project ? store.findConstruction(project, fitTarget)?.model : null
  const fitName = String(fitModel?.meta?.name ?? fitTarget ?? '')
  useEffect(() => showScanoRoiInViewport(), [])

  useEffect(() => {
    let live = true
    void (async () => {
      try {
        const resp = await fetch('/api/ai/scano/models', { cache: 'no-store' })
        const body = (await resp.json()) as { models?: ModelCard[]; error?: string }
        if (!resp.ok || body.error) throw new Error(body.error ?? String(resp.status))
        if (!live) return
        const list = body.models ?? []
        setModels(list)
        const usable = list.filter((m) => !m.error)
        setModel((current) =>
          usable.some((m) => m.id === current) ? current : (usable.find((m) => m.recommended) ?? usable[0])?.id ?? '')
      } catch (err) {
        if (live) setError(err instanceof Error ? err.message : String(err))
      }
    })()
    return () => {
      live = false
    }
  }, [])

  // Load the model ahead of the first run.
  useEffect(() => {
    if (!model || models.find((m) => m.id === model)?.error) return
    void fetch('/api/ai/scano/warmup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    }).catch(() => undefined)
  }, [model])

  const job = session.getCurrentCaseJob()
  const running = !!session.getJob()?.running
  const scanoRunning = running && job?.kind === 'scano'
  const validated = session.isValidated()
  const meshPresent = session.isMeshPresent()
  // the note that checks the mesh is shown only with a mesh
  const meshBlocked = meshPresent ? meshBlock : null
  const engine = String(meshConfigOf(project).meshEngine ?? 'dcCart')
  const card = models.find((m) => m.id === model)
  const want = card?.mesh?.engine
  const zh = locale === 'zh-CN'
  const result = job?.kind === 'scano' && !job.running ? job.scanoResult ?? null : null
  const warnings = result?.warnings ?? []

  async function run() {
    if (preparing || !model) return
    const block = meshBlockNow()
    if (block) {
      store.logOperation('scano', t(block === 'pending' ? 'meshchange.checking' : 'meshchange.blocked'))
      return
    }
    setError(null)
    setPreparing(true)
    const how = scanoInferenceRequest()
    store.logOperation('scano', `${t('scano.run')} · ${model} · ${t(`scano.inf.${how.inference}`)}`
      + (how.inference !== 'whole' && how.passes > 1 ? ` ×${how.passes}` : '')
      + (scanoRoiRequest() ? ` · ${t('scano.roi.region_roi')}` : ''))
    try {
      await session.runJob('scano', { model, mode: 'auto', roi: scanoRoiRequest(), ...how })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      store.logOperation('scano', t('scano.failed', { e: message }))
    } finally {
      setPreparing(false)
    }
  }

  function openResult() {
    if (!result) return
    requestPostprocessRun(result.runDir)
    store.logOperation('scano', `${t('scano.open_pp')}: ${result.runDirRelative}`)
    onOpenPostprocess?.()
  }

  return (
    <div className="prop-group" id="scano-panel" style={{ borderLeftColor: 'var(--section-header-solver)' }}>
      <div className="prop-row">
        <label htmlFor="scano-model">{t('scano.model')}</label>
        <select id="scano-model" value={model} disabled={running}
          title={card ? ((zh ? card.description_zh : card.description) ?? '') : undefined}
          onChange={(e) => {
            setModel(e.target.value)
            remember(SCANO_MODEL_KEY, e.target.value)
          }}
          style={{ flex: 1, minWidth: 0 }}>
          {models.map((m) => (
            <option key={m.id} value={m.id} disabled={!!m.error}>
              {(zh ? m.name_zh : m.name) ?? m.id}{m.recommended ? ` · ${t('scano.recommended')}` : ''}
            </option>
          ))}
        </select>
      </div>
      {card?.test_error !== undefined && (
        <div className="prop-hint" id="scano-model-card">
          {t('scano.model_accuracy', { e: card.test_error.toFixed(2), r: (card.test_rmse_T_K ?? 0).toFixed(2) })}
        </div>
      )}
      {card?.error && <div className="error">{t('scano.model_error', { e: card.error })}</div>}
      {want && engine !== want && (
        <div className="warn" id="scano-engine-warn">{t('scano.mesh_engine_warn', { engine, want })}</div>
      )}

      <div className="scano-region-head">
        <span>{t('scano.inf.title')}</span>
        <Hint text={t('scano.inf.hint')} />
      </div>
      <div className="seg" id="scano-inference" role="radiogroup">
        {(['auto', 'whole', 'tta'] as ScanoInferenceMode[]).map((m) => (
          <button key={m} type="button" role="radio" aria-checked={inference.inference === m} id={`scano-inf-${m}`}
            className={inference.inference === m ? 'on' : ''} disabled={running} title={t(`scano.inf.${m}_hint`)}
            onClick={() => setScanoInference({ inference: m })}>{t(`scano.inf.${m}`)}</button>
        ))}
      </div>
      {inference.inference !== 'whole' && (
        <div className="prop-row" id="scano-passes-row">
          <label htmlFor="scano-passes" title={t('scano.inf.passes_hint')}>{t('scano.inf.passes')}</label>
          <NumberField id="scano-passes" value={inference.passes} decimals={0} step={1} min={1} max={MAX_PASSES}
            disabled={running} onCommit={(v) => setScanoInference({ passes: v })} />
        </div>
      )}
      {model && <ScanoGpuStatus model={model} choice={inference} />}

      <div className="scano-region-head">
        <span>{t('scano.roi.region')}</span>
        <Hint text={t('scano.roi.hint')} />
      </div>
      <div className="seg" id="scano-region" role="radiogroup">
        <button type="button" role="radio" aria-checked={!roi.enabled} className={!roi.enabled ? 'on' : ''}
          disabled={running} onClick={() => setScanoRoi({ enabled: false })}>{t('scano.roi.region_full')}</button>
        <button type="button" role="radio" aria-checked={roi.enabled} className={roi.enabled ? 'on' : ''}
          disabled={running} id="scano-region-roi"
          onClick={() => setScanoRoi({ enabled: true, box: roi.box ?? defaultRoiBox() })}>{t('scano.roi.region_roi')}</button>
      </div>
      {roi.enabled && (
        <div className="scano-roi" id="scano-roi">
          {roi.box ? (
            <div className="scano-roi-grid">
              <span />
              {(['X', 'Y', 'Z'] as const).map((a) => <span key={a} className="scano-roi-axis">{a}</span>)}
              {(['min', 'max'] as const).map((end) => (
                <RoiRow key={end} end={end} values={roi.box![end]} disabled={running}
                  onCommit={(i, v) => {
                    const next = { min: [...roi.box!.min] as Vec3, max: [...roi.box!.max] as Vec3 }
                    next[end][i] = v
                    if (next.min[i]! >= next.max[i]!) return false
                    setScanoRoi({ box: next })
                  }} />
              ))}
            </div>
          ) : <div className="prop-hint">{t('scano.roi.no_scene')}</div>}
          <div className="scano-roi-actions">
            <button type="button" className="btn" id="scano-roi-fit" disabled={running}
              title={(fitTarget ? fitName + '\n' : '') + t('scano.roi.fit_hint')}
              onClick={() => {
                const b = modelBounds(fitTarget)
                if (b) setScanoRoi({ box: b })
              }}>
              <Icon name="target" size={13} />
              <span className="scano-roi-fit-label">{fitTarget ? t('scano.roi.fit_object', { name: fitName }) : t('scano.roi.fit_room')}</span>
            </button>
            <button type="button" className="btn" id="scano-roi-reset" disabled={running}
              onClick={() => setScanoRoi({ box: defaultRoiBox() })}>
              <Icon name="refresh" size={13} /> {t('scano.roi.reset')}
            </button>
          </div>
          <div className="prop-hint" id="scano-roi-note">{t('scano.roi.local_note')}</div>
        </div>
      )}

      {meshPresent && <MeshChangeNote />}
      <button className="btn success" id="scano-run-btn" style={{ width: '100%', marginTop: 6 }}
        disabled={!validated || !meshPresent || running || preparing || !model || !!card?.error || !!meshBlocked}
        title={!meshPresent ? t('scano.need_mesh') : meshBlocked === 'pending' ? t('meshchange.checking')
          : meshBlocked ? t('meshchange.blocked') : undefined}
        onClick={() => void run()}>
        {scanoRunning || preparing ? t('scano.running') : t('scano.run')}
      </button>
      {scanoRunning && (
        <button className="btn danger" id="scano-abort-btn" style={{ width: '100%', marginTop: 4 }}
          disabled={!!(job?.abortPending || job?.abortRequested)}
          onClick={() => void session.abortJob()}>
          {t('scano.abort')}
        </button>
      )}
      {!meshPresent && <div className="prop-hint">{t('scano.need_mesh')}</div>}
      {error && <div className="error">{t('scano.failed', { e: error })}</div>}
      {job?.kind === 'scano' && !job.running && job.returncode !== 0 && !job.aborted && (
        <div className="error" id="scano-failed">{t('scano.failed', { e: job.lines.filter((l) => l.startsWith('[SCANO]')).at(-1) ?? String(job.returncode) })}</div>
      )}

      {result && (
        <div id="scano-result" style={{ marginTop: 8 }}>
          <div className="prop-hint">{t('scano.done', { s: (result.timing.total_s ?? 0).toFixed(1), c: result.nCells })}</div>
          {result.inference && (
            <div className="prop-hint" id="scano-result-inference">
              {result.inference.mode === 'tta'
                ? t('scano.inf.result_tta', { r: result.inference.passes ?? 1, n: result.inference.forwards ?? 0,
                  p: gib(result.inference.peakBytes) })
                : t('scano.inf.result_whole', { p: gib(result.inference.peakBytes) })}
              {result.inference.reason && result.inference.reason.startsWith('whole room ran out')
                ? ` · ${t('scano.inf.fell_back')}` : ''}
            </div>
          )}
          {result.roi && (
            <div className="prop-hint" id="scano-result-roi">{t('scano.roi.result', { c: result.roi.cells })}</div>
          )}
          {warnings.length > 0 && (
            <div className="warn" id="scano-warnings">
              {t('scano.warnings')}
              <ul style={{ margin: '2px 0 0 16px', padding: 0 }}>{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          )}
          <button className="btn" id="scano-open-pp" style={{ width: '100%', marginTop: 6 }} onClick={openResult}>
            {t('scano.open_pp')}
          </button>
        </div>
      )}
    </div>
  )
}

function RoiRow({ end, values, disabled, onCommit }: {
  end: 'min' | 'max'
  values: Vec3
  disabled: boolean
  onCommit: (axis: number, value: number) => void | boolean
}) {
  return (
    <>
      <span className="scano-roi-end">{end}</span>
      {values.map((v, i) => (
        <NumberField key={i} id={`scano-roi-${end}-${'xyz'[i]}`} value={v} decimals={2} step={0.1} disabled={disabled}
          onCommit={(next) => onCommit(i, next)} />
      ))}
    </>
  )
}
