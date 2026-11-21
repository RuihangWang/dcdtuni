import type { CfdProject, Construction } from '../schema/cfdproj'
import { Icon, type IconName } from './Icon'
import { beginDragCreate, DRAG_CREATE_KINDS, takeDragCreateClick } from '../interaction/dragCreate'
import type { TranslationKey } from '../i18n'
import { createModel, createPipeJunction, createPipeSegment } from '../legacy/factories'
import { buildColdPlate, buildCrh, buildHeatSource, findCrhForRack, rackServer } from '../liquid/liquidFactories'
import * as store from '../projectStore'
import { liquidPipes } from '../legacy/liquidScene'
import { LibraryAddButtons } from './ModelLibrary'
import { useAppSelection, type AppSelection } from '../selectionBus'
import { StlImportButton } from './StlImportButton'
import { useTranslation } from '../useTranslation'
import { useEffect, useState } from 'react'
import { LiquidReadinessCard, useLiquidReadiness } from './LiquidReadinessCard'
import './LiquidWorkflow.css'

/** The shared liquid module's connect mode (liquid_mode.js enterPipeCreationMode). */
type PipeModeWindow = {
  _enterPipeCreationMode?: () => void
  _exitPipeCreationMode?: () => void
  _isPipeCreationMode?: () => boolean
}
function pipeModeApi(): PipeModeWindow {
  return (typeof window === 'undefined' ? {} : window) as unknown as PipeModeWindow
}

/**
 * The right panel's Create Geometry buttons.
 *
 * Colours are the legacy toolbar's per-kind accents (rack amber, ACU cyan, wall
 * purple, partition bronze, baffle green) — they are how a user finds the right
 * button at a glance, so they are part of the layout, not decoration.
 *
 * Every kind here is built by shared code, not by anything reimplemented in
 * TypeScript: plain kinds go through the v3->v4 migrator, racks go through
 * js/rack_state.js. That is what makes a model created here byte-identical to
 * one created by the HTML editor.
 *
 * LIQUID MODE KEEPS THE AIR TOOLS. `_renderLiquidToolbar`
 * (STL_generate.html:12366-12373) emits the whole air button set BEFORE its own
 * separator, and it has to: a cold plate attaches to a server, a manifold to a
 * rack, and a CDU's served-rack list is empty until racks exist. Dropping them
 * meant a liquid model could not be started from an empty project at all.
 *
 * The contextual buttons below the separator act on the SELECTION — a CRH and a
 * cold plate need the rack they belong to, a heat source needs the server.
 */

interface Tool {
  labelKey: TranslationKey
  kind?: string
  /** DOM id, where the legacy modules restyle the button by id. */
  id?: string
  icon?: IconName
  primary?: boolean
  disabledKey?: TranslationKey
}

const AIR_TOOLS: Tool[] = [
  { labelKey: 'btn.new_box', kind: 'box', icon: 'cube', primary: true },
  {
    labelKey: 'btn.new_rack',
    kind: 'rack',
    icon: 'rack',
  },
  {
    labelKey: 'btn.new_acu',
    kind: 'acu',
    icon: 'snowflake',
  },
  {
    labelKey: 'btn.new_wall',
    kind: 'wall',
    icon: 'wall',
  },
  {
    labelKey: 'btn.new_partition_wall',
    kind: 'partitionWall',
    icon: 'partition',
  },
  {
    labelKey: 'btn.new_baffle',
    kind: 'baffle',
    icon: 'sheet',
  },
  {
    labelKey: 'btn.new_floor',
    kind: 'floor',
    icon: 'floor',
  },
  {
    labelKey: 'btn.new_air_pipe',
    kind: 'pipe',
    icon: 'pipe',
    id: 'btn-new-air-pipe',
  },
]

const LIQUID_TOOLS: Tool[] = [
  {
    labelKey: 'liquid.btn_new_cdu',
    kind: 'cdu',
    icon: 'droplet',
  },
  {
    labelKey: 'liquid.btn_new_pipe',
    kind: 'pipe_segment',
    icon: 'pipe',
    id: 'btn-new-pipe',
  },
  {
    labelKey: 'liquid.btn_new_tbranch',
    kind: 'pipe_junction',
    icon: 'link',
    id: 'btn-new-tbranch',
  },
]


// The two separators the legacy draws between the air set, the liquid set and
// the contextual set (STL_generate.html:12374, :12382). Inline because they are
// the only place in the app with these rules.
const LIQUID_SECTION: React.CSSProperties = { borderTop: '1px solid #444', margin: '6px 0', paddingTop: 6 }
const CONTEXT_SECTION: React.CSSProperties = { borderTop: '1px solid #333', margin: '4px 0', paddingTop: 4 }

export interface CreationToolbarProps {
  mode: 'air' | 'liquid'
  project: CfdProject | null
  onCreated: (uid: string) => void
  /**
   * The app's selection, when the caller has it.
   *
   * Optional so the toolbar keeps working from `selectionBus` while the app is
   * not passing it — the contextual buttons are the only thing that reads it.
   */
  selection?: AppSelection
}

function modelOf(project: CfdProject | null, uid: string | null): Construction | null {
  if (!project || !uid) return null
  return store.findConstruction(project, uid)?.model ?? null
}

export function CreationToolbar({ mode, project, onCreated, selection }: CreationToolbarProps) {
  const { t } = useTranslation()
  const bus = useAppSelection()
  const sel = selection ?? bus
  const liquid = mode === 'liquid'

  // A CRH and a cold plate hang off the selected RACK; a heat source off the
  // selected SERVER, whose own node carries the rack it lives in.
  const selectedRack = modelOf(project, sel.uid)
  const rackForContext = selectedRack?.kind === 'rack' ? selectedRack : null
  const node = sel.node
  const serverNode =
    node && (node.type === 'equipment' || node.type === 'serverFace' || node.type === 'serverPatch')
      ? node
      : null
  const rackForServer = serverNode ? modelOf(project, serverNode.uid) : null
  const server =
    rackForServer && serverNode?.equipmentId
      ? rackServer(rackForServer, serverNode.equipmentId)
      : null

  function place(created: { bucket: string; model: Construction }) {
    const uid = store.addModel(created.bucket, created.model)
    // The geometry builder does not draw pipes; the liquid renderer does, from
    // its own group. Without this a new pipe is in the project and invisible.
    liquidPipes()?.updatePipeGeometry()
    onCreated(uid)
  }

  function create(kind: string) {
    if (!project) return
    // Pipes and fittings have their own factories: a pipe has no box — its
    // `base` is zero on every axis and its shape lives in `waypoints`.
    place(
      kind === 'pipe_segment'
        ? createPipeSegment(project)
        : kind === 'pipe_junction'
          ? createPipeJunction(project)
          : createModel(project, kind),
    )
  }

  function createCrh() {
    if (!project || !rackForContext) return
    // One per rack: a second one would double the heat the loop removes while
    // looking like a duplicate of a model the user cannot see behind the rack.
    if (findCrhForRack(project, rackForContext.uid)) {
      window.alert(t('liquid.alert_rack_has_crh'))
      return
    }
    place(buildCrh(project, rackForContext))
  }

  function createColdPlate() {
    if (!project || !rackForContext) return
    place(buildColdPlate(project, rackForContext))
  }

  function createHeatSource() {
    if (!project || !rackForServer || !server) return
    place(buildHeatSource(project, rackForServer, server))
  }

  // Connect mode is the shared module's state; mirrored here for the button
  // label, and left on Esc as the legacy toolbar does.
  const [connecting, setConnecting] = useState(() => pipeModeApi()._isPipeCreationMode?.() === true)
  useEffect(() => {
    if (!connecting) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      pipeModeApi()._exitPipeCreationMode?.()
      setConnecting(false)
    }
    // The module leaves connect mode by itself after the second port; poll
    // its flag rather than patch the shared code with a callback.
    const timer = setInterval(() => {
      if (pipeModeApi()._isPipeCreationMode?.() !== true) setConnecting(false)
    }, 400)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); clearInterval(timer) }
  }, [connecting])
  function toggleConnectMode() {
    const api = pipeModeApi()
    if (connecting) { api._exitPipeCreationMode?.(); setConnecting(false); return }
    if (!api._enterPipeCreationMode) return
    api._enterPipeCreationMode()
    setConnecting(api._isPipeCreationMode?.() === true)
  }
  const readiness = useLiquidReadiness(liquid ? project : null)
  const serverType = server && rackForServer
    ? (rackForServer.constructions as { servers?: Record<string, { type?: string }> } | undefined)?.servers?.[server.uid]?.type
    : undefined
  const liquidServerSelected = serverType === 'liquidServer'

  function renderTools(tools: Tool[]) {
    return tools.map((tool) => {
      const disabled = !project || !tool.kind
      const title = tool.disabledKey
        ? t(tool.disabledKey)
        : project
          ? undefined
          : t('hint.load_project_first')
      // Press-and-drag places the model where the pointer is released, which is
      // why the gesture starts on POINTERDOWN and the click is only the
      // fallback for a press that never moved (STL_generate.html:12348).
      const draggable = !!tool.kind && !disabled && DRAG_CREATE_KINDS.has(tool.kind)
      return (
        <button
          key={tool.labelKey}
          id={tool.id}
          className={`btn${tool.primary ? ' primary' : ''}`}
          disabled={disabled}
          title={title}
          onPointerDown={
            draggable
              ? (event) =>
                  beginDragCreate(event.nativeEvent, tool.kind as string, {
                    project: () => project,
                    onCreated,
                    cannotFitMessage: t('error.model_cannot_fit_bounds'),
                  })
              : undefined
          }
          onClick={() => {
            // A press that travelled far enough to create a model still fires a
            // click when it is released back over the button; without this the
            // gesture would leave two models behind.
            if (takeDragCreateClick()) return
            if (tool.kind) create(tool.kind)
          }}
        >
          {tool.icon && <Icon name={tool.icon} />}
          {t(tool.labelKey)}
        </button>
      )
    })
  }

  const airTools = (
    <>
      {renderTools(AIR_TOOLS)}
      <StlImportButton onImported={onCreated} />
    </>
  )

  return (
    <div id="creation-toolbar">
      <div className="stack">
        {/* Liquid mode leads with its own tools: under the liquid-tools title the
            nine air builders used to come first and pushed CDU, pipe and the
            connect mode to the bottom of the short scrolling bar. The common
            builders (racks are needed in both modes) follow under their own title. */}
        {!liquid && airTools}
        {liquid && (
          <>
            <div id="creation-liquid-section" style={LIQUID_SECTION}>
              <div className="creation-section-title">
                {t('liquid.section_liquid')}
              </div>
              {renderTools(LIQUID_TOOLS)}
              <button
                id="btn-connect-pipes"
                className={`btn${connecting ? ' primary' : ''}`}
                disabled={!project || !pipeModeApi()._enterPipeCreationMode}
                aria-pressed={connecting}
                onClick={toggleConnectMode}
              >
                <Icon name="link" />
                {connecting ? t('lq_ux.connect_mode_exit') : t('lq_ux.connect_mode')}
              </button>
              {connecting && <div className="lq-toolbar-hint" id="connect-mode-hint">{t('lq_ux.connect_mode_hint')}</div>}
              <div className="lq-toolbar-hint" id="liquid-rightclick-hint">{t('lq_ux.rightclick_hint')}</div>
              <details id="liquid-readiness-toolbar" open={!!readiness && !readiness.ready} style={{ margin: '4px 2px' }}>
                <summary style={{ fontSize: 11, cursor: 'pointer' }}>{t('lq_ux.ready_card_title')}</summary>
                <LiquidReadinessCard report={readiness} compact id="liquid-readiness-compact" />
              </details>
            </div>
            <div id="creation-contextual-section" style={CONTEXT_SECTION}>
              <div className="creation-section-title">{t('liquid.contextual')}</div>
              {/* The cold plates the liquid solve reads are a server's CP-CU
                  pairs; the buttons below build display or air-side models. */}
              <div className="lq-toolbar-hint" id="cpcu-guide">
                {liquidServerSelected
                  ? t('lq_ux.ctx_cpcu_selected', { name: server?.name ?? '' })
                  : t('lq_ux.ctx_cpcu_guide')}
              </div>
              <button
                id="btn-create-crh"
                className="btn"
                disabled={!rackForContext}
                onClick={createCrh}
              >
                {rackForContext
                  ? t('liquid.btn_create_crh', { name: rackForContext.meta?.name ?? rackForContext.uid })
                  : t('liquid.btn_create_crh_disabled')}
                <span className="lq-ctx-tag">{t('lq_ux.ctx_display_only')}</span>
              </button>
              <button
                id="btn-create-coldplate"
                className="btn"
                disabled={!rackForContext}
                onClick={createColdPlate}
              >
                {rackForContext
                  ? t('liquid.btn_create_coldplate', {
                      name: rackForContext.meta?.name ?? rackForContext.uid,
                    })
                  : t('liquid.btn_create_coldplate_disabled')}
                <span className="lq-ctx-tag">{t('lq_ux.ctx_display_only')}</span>
              </button>
              <button
                id="btn-create-heatsource"
                className="btn"
                disabled={!server}
                onClick={createHeatSource}
              >
                {server
                  ? t('liquid.btn_create_heatsource')
                  : t('liquid.btn_create_heatsource_disabled')}
                <span className="lq-ctx-tag">{t('lq_ux.ctx_air_heat')}</span>
              </button>
            </div>
            <div id="creation-common-section" style={LIQUID_SECTION}>
              <div className="creation-section-title">{t('liquid.section_common')}</div>
              {airTools}
            </div>
          </>
        )}
        {/* Below the liquid and contextual sections, as the legacy appends it
            (STL_generate.html:12325) — the library grows at the bottom of the
            bar rather than pushing the fixed create buttons down. */}
        <LibraryAddButtons project={project} onCreated={onCreated} />
      </div>
    </div>
  )
}
