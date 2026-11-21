import { useEffect, useRef, useState } from 'react'
import {
  fetchDemoModelPath,
  normaliseBox,
  runAiInference,
  startAiWorker,
  type AiBox,
} from '../postprocess/ppAi'
import {
  aiBoxIsEmpty,
  aiDevice,
  aiInferStats,
  aiReadyMessage,
  aiWarmup,
} from '../postprocess/ppAiStatus'
import { ppLog } from '../postprocess/ppLog'
import { getPpView, subscribePpView } from '../postprocess/ppViewState'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import type { PostprocessSession } from '../postprocess/ppRevision'

/**
 * AI adaptive-region inference.
 *
 * Start a worker with a checkpoint and a case, place a box in the room, and the
 * model predicts the field inside it. The prediction comes back as extra fields
 * (`AI_T`, …) which the volume renderer then draws — which is why confirming an
 * inference switches the volume section on and points it at AI_T.
 *
 * Ported from `#pp-ai-*` (STL_generate.html:725) and its handlers.
 *
 * The model path is fetched from the server, never defaulted here: a hardcoded
 * absolute path would carry a username and a private repository name into every
 * browser that opens the editor, and this project is under anonymous review.
 */

export interface AiInferencePanelProps {
  /** The run directory the panel defaults its case path to. */
  runDir: string | null
  /** Whether /api/pp/init has been run — inference needs the loaded case. */
  initialised: boolean
  session: PostprocessSession | null
  /** Called with the fields the inference produced, to extend the field list. */
  onFields?: (
    fields: string[], ranges: Record<string, [number, number]> | undefined,
    session: PostprocessSession,
  ) => void
  /**
   * The region, whenever it changes, so the viewport can draw it.
   *
   * Reported UP rather than drawn here: the box belongs in the same layer group
   * as the result it selects, or it would survive a 清除全部渲染 that cleared
   * everything else.
   */
  onBox?: (box: AiBox | null) => void
}

export function AiInferencePanel({ runDir, initialised, session, onFields, onBox }: AiInferencePanelProps) {
  const { t } = useTranslation()
  const [modelPath, setModelPath] = useState('')
  const [casePath, setCasePath] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState(false)
  const [ready, setReady] = useState(false)
  const [boxVisible, setBoxVisible] = useState(true)
  const [box, setBox] = useState<AiBox>({ min: [0, 0, 0], max: [1, 1, 1] })
  const [defaultBox, setDefaultBox] = useState<AiBox | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const sessionRef = useRef(session)
  sessionRef.current = session

  useEffect(() => {
    if (!initialised || !session) {
      setReady(false)
      onBox?.(null)
    }
  }, [initialised, session?.runId, session?.timeValue])

  function say(message: string, isError = false) {
    setStatus(message)
    setError(isError)
  }

  // A viewport drag reports the box it left behind, and these six boxes are its
  // readout: a handle whose numbers do not follow it is two controls that
  // disagree about the same region. Same round trip as the streamline seed
  // sphere's gizmo, through the shared view store rather than through props —
  // the handles live in the viewport, a different React subtree entirely.
  const onBoxRef = useRef(onBox)
  onBoxRef.current = onBox
  const lastDrag = useRef(getPpView().aiBoxDrag)
  useEffect(
    () =>
      subscribePpView(() => {
        const dragged = getPpView().aiBoxDrag
        if (!dragged || dragged === lastDrag.current) return
        lastDrag.current = dragged
        setBox(dragged)
        onBoxRef.current?.(normaliseBox(dragged))
      }),
    [],
  )

  async function start() {
    if (!initialised || !session) return say(t('pp.ai_please_init_pp'), true)
    const requestedSession = session
    const model = modelPath.trim()
    const path = casePath.trim() || runDir || ''
    if (!model) return say(t('pp.ai_set_model_path'), true)
    if (!path) return say(t('pp.ai_set_case_path'), true)
    setBusy('start')
    say(t('pp.ai_starting_worker'))
    try {
      const data = await startAiWorker(model, path, requestedSession)
      if (sessionRef.current !== requestedSession) return
      const bounds = data.status?.bounds ?? data.result?.bounds ?? null
      setReady(true)
      if (bounds) {
        setBox(bounds)
        setDefaultBox(bounds)
      }
      publish(boxVisible, true, bounds ?? box)
      // The device is what tells a user whether the run they are about to wait
      // for is on the GPU or on the CPU, and the warm-up figures are the only
      // estimate of what an inference will cost before they start one.
      const warm = aiWarmup(data)
      say(
        aiReadyMessage(
          t('pp.ai_worker_ready'),
          aiDevice(data),
          warm ? t('pp.ai_warmup_info', { count: warm.count, seconds: warm.seconds }) : null,
        ),
      )
      ppLog(`worker ready${warm ? ` (${warm.count} cells warmup)` : ''}`, 'PP AI')
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setReady(false)
      publish(boxVisible, false)
      say(`${t('pp.ai_worker_start_failed')}: ${message}`, true)
      ppLog(`start failed: ${message}`, 'PP AI')
    } finally {
      setBusy(null)
    }
  }

  async function confirm() {
    if (!ready) return say(t('pp.ai_please_start_test'), true)
    if (!session) return say(t('pp.ai_please_init_pp'), true)
    const requestedSession = session
    // Normalised first: six number boxes make an inverted box easy, and the
    // server reads one as an empty region.
    const region = normaliseBox(box)
    // A flat box is the legacy's uninitialised one: the request succeeds and
    // comes back with nothing selected, which looks like the model failing.
    if (aiBoxIsEmpty(region)) return say(t('pp.ai_box_not_initialized'), true)
    setBusy('infer')
    say(t('pp.ai_submitting_inference'))
    try {
      const data = await runAiInference(region, requestedSession)
      if (sessionRef.current !== requestedSession) return
      const names = (data.fields ?? []).map((f) => f.name)
      onFields?.(names, data.result?.ranges, data)
      const stats = aiInferStats(data)
      say(t('pp.ai_inference_complete', { cells: stats.cells, seconds: stats.seconds }))
      ppLog(`inference finished (${stats.cells} cells, ${stats.seconds}s)`, 'PP AI')
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      say(`${t('pp.ai_inference_failed')}: ${message}`, true)
      ppLog(`inference failed: ${message}`, 'PP AI')
    } finally {
      setBusy(null)
    }
  }

  function setBound(which: 'min' | 'max', axis: 0 | 1 | 2, value: number) {
    setBox((prev) => {
      const next = { min: [...prev.min], max: [...prev.max] } as AiBox
      next[which][axis] = value
      onBox?.(normaliseBox(next))
      return next
    })
  }

  // Report the box whenever its VISIBILITY or readiness changes too, not only
  // when a number moves: unchecking the box has to remove it from the scene.
  function publish(nextVisible = boxVisible, nextReady = ready, nextBox = box) {
    onBox?.(nextReady && nextVisible ? normaliseBox(nextBox) : null)
  }

  return (
    <fieldset className="prop-group" disabled={!initialised} style={{ borderLeftColor: 'var(--accent3)' }}>
      <div className="prop-title" style={{ color: 'var(--accent3)' }}>
        {t('title.ai_adaptive_inference')}
      </div>
      <div className="prop-hint" id="pp-ai-time-provenance">
        {t('pp.ai_independent_h5_notice')}
      </div>

      <div className="prop-row">
        <label htmlFor="pp-ai-model-path">{t('label.model_weights')}</label>
        <input
          id="pp-ai-model-path"
          type="text"
          spellCheck={false}
          placeholder="fixedEmbedding checkpoint (*.state)"
          value={modelPath}
          onChange={(e) => setModelPath(e.target.value)}
        />
      </div>
      <div className="prop-row pp-row-buttons">
        <button
          id="pp-ai-demo-weights"
          className="btn"
          onClick={() =>
            void fetchDemoModelPath().then((p) =>
              p ? setModelPath(p) : say(t('pp.ai_demo_model_unset'), true),
            )
          }
        >
          {t('btn.demo_weights')}
        </button>
        <button
          id="pp-ai-use-run"
          className="btn"
          onClick={() => runDir && setCasePath(runDir)}
        >
          {t('btn.current_result_dir')}
        </button>
      </div>

      <div className="prop-row">
        <label htmlFor="pp-ai-case-path">{t('label.case_path')}</label>
        <input
          id="pp-ai-case-path"
          type="text"
          spellCheck={false}
          placeholder="OpenFOAM case path (demo contains h5/)"
          value={casePath}
          onChange={(e) => setCasePath(e.target.value)}
        />
      </div>
      <div className="prop-row pp-row-buttons">
        <button id="pp-ai-start" className="btn" disabled={busy === 'start'} onClick={() => void start()}>
          {busy === 'start' ? t('status.loading') : t('btn.start_test')}
        </button>
      </div>

      <div id="pp-ai-status" className="prop-hint" style={error ? { color: '#e06c75' } : undefined}>
        {status}
      </div>

      {ready && (
        <div id="pp-ai-controls">
          <div className="prop-row">
            <label htmlFor="pp-ai-box-visible">{t('label.show_inference_box')}</label>
            <input
              type="checkbox"
              id="pp-ai-box-visible"
              checked={boxVisible}
              onChange={(e) => {
                setBoxVisible(e.target.checked)
                publish(e.target.checked)
              }}
            />
          </div>
          {/* Three decimals, the legacy's `_ppAiSyncInputsFromBox` precision
              (STL_generate.html:20449). It matters now that a drag writes these
              boxes: a millimetre readout beside a handle that moves in metres
              reads as noise, and the two apps' inputs are diffed against each
              other by the parity sweep. */}
          {(['x', 'y', 'z'] as const).map((axis, i) => (
            <div className="prop-row" key={axis}>
              <label>{axis.toUpperCase()}</label>
              <NumberField
                id={`pp-ai-min-${axis}`}
                decimals={3}
                value={box.min[i] as number}
                step={0.1}
                onCommit={(v) => setBound('min', i as 0 | 1 | 2, v)}
              />
              <NumberField
                id={`pp-ai-max-${axis}`}
                decimals={3}
                value={box.max[i] as number}
                step={0.1}
                onCommit={(v) => setBound('max', i as 0 | 1 | 2, v)}
              />
            </div>
          ))}
          <div className="prop-row pp-row-buttons">
            <button
              id="pp-ai-reset-box"
              className="btn"
              disabled={!defaultBox}
              onClick={() => {
                if (!defaultBox) return
                setBox(defaultBox)
                publish(boxVisible, ready, defaultBox)
              }}
            >
              {t('btn.reset_box')}
            </button>
            <button
              id="pp-ai-confirm"
              className="btn primary"
              disabled={busy === 'infer'}
              onClick={() => void confirm()}
            >
              {busy === 'infer' ? t('status.loading') : t('btn.confirm_inference')}
            </button>
          </div>
        </div>
      )}
    </fieldset>
  )
}
