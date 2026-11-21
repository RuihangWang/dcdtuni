import type { CfdProject, Construction } from '../schema/cfdproj'
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
  style?: React.CSSProperties
  primary?: boolean
  disabledKey?: TranslationKey
}

const AIR_TOOLS: Tool[] = [
  { labelKey: 'btn.new_box', kind: 'box', primary: true },
  {
    labelKey: 'btn.new_rack',
    kind: 'rack',
    style: { background: 'rgba(255,184,0,0.06)', borderColor: 'rgba(255,184,0,0.35)', color: '#ffb800' },
  },
  {
    labelKey: 'btn.new_acu',
    kind: 'acu',
    style: { background: 'rgba(0,212,255,0.06)', borderColor: 'rgba(0,212,255,0.35)', color: 'var(--accent3)' },
  },
  {
    labelKey: 'btn.new_wall',
    kind: 'wall',
    style: { background: 'rgba(204,68,255,0.06)', borderColor: 'rgba(204,68,255,0.3)', color: '#cc44ff' },
  },
  {
    labelKey: 'btn.new_partition_wall',
    kind: 'partitionWall',
    style: { background: 'rgba(184,132,63,0.08)', borderColor: 'rgba(184,132,63,0.35)', color: '#b8843f' },
  },
  {
    labelKey: 'btn.new_baffle',
    kind: 'baffle',
    style: { background: 'rgba(0,255,136,0.05)', borderColor: 'rgba(0,255,136,0.25)', color: 'var(--highlight)' },
  },
  {
    labelKey: 'btn.new_floor',
    kind: 'floor',
    style: { background: 'rgba(0,255,136,0.04)', borderColor: 'rgba(0,255,136,0.18)', color: '#00cc6a' },
  },
  {
    labelKey: 'btn.new_air_pipe',
    kind: 'pipe',
    id: 'btn-new-air-pipe',
    style: { background: 'rgba(184,190,198,0.08)', borderColor: 'rgba(184,190,198,0.4)', color: '#b8bec6' },
  },
]

const LIQUID_TOOLS: Tool[] = [
  {
    labelKey: 'liquid.btn_new_cdu',
    kind: 'cdu',
    style: { background: 'rgba(0,212,255,0.06)', borderColor: 'rgba(0,212,255,0.35)', color: 'var(--accent3)' },
  },
  {
    labelKey: 'liquid.btn_new_pipe',
    kind: 'pipe_segment',
    id: 'btn-new-pipe',
    style: { background: 'rgba(0,180,220,0.06)', borderColor: 'rgba(0,180,220,0.35)', color: '#00b4dc' },
  },
  {
    labelKey: 'liquid.btn_new_tbranch',
    kind: 'pipe_junction',
    id: 'btn-new-tbranch',
    style: { background: 'rgba(0,180,220,0.06)', borderColor: 'rgba(0,180,220,0.35)', color: '#00b4dc' },
  },
]

const CONTEXT_BTN: React.CSSProperties = { background: 'rgba(0,180,220,0.06)', color: '#00b4dc' }
const HEAT_BTN: React.CSSProperties = { background: 'rgba(255,87,34,0.1)', color: '#FF5722' }

// The two separators the legacy draws between the air set, the liquid set and
// the contextual set (STL_generate.html:12374, :12382). Inline because they are
// the only place in the app with these rules.
const LIQUID_SECTION: React.CSSProperties = { borderTop: '1px solid #444', margin: '6px 0', paddingTop: 6 }
const CONTEXT_SECTION: React.CSSProperties = { borderTop: '1px solid #333', margin: '4px 0', paddingTop: 4 }
const SECTION_TITLE: React.CSSProperties = { fontSize: 10, padding: '2px 4px' }

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
          style={tool.style}
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
          {t(tool.labelKey)}
        </button>
      )
    })
  }

  return (
    <div id="creation-toolbar">
      <div className="stack">
        {renderTools(AIR_TOOLS)}
        <StlImportButton onImported={onCreated} />
        {liquid && (
          <>
            <div id="creation-liquid-section" style={LIQUID_SECTION}>
              <div style={{ ...SECTION_TITLE, color: '#00b4dc', fontWeight: 'bold' }}>
                {t('liquid.section_liquid')}
              </div>
              {renderTools(LIQUID_TOOLS)}
            </div>
            <div id="creation-contextual-section" style={CONTEXT_SECTION}>
              <div style={{ ...SECTION_TITLE, color: '#888' }}>{t('liquid.contextual')}</div>
              <button
                id="btn-create-crh"
                className="btn"
                style={rackForContext ? CONTEXT_BTN : undefined}
                disabled={!rackForContext}
                onClick={createCrh}
              >
                {rackForContext
                  ? t('liquid.btn_create_crh', { name: rackForContext.meta?.name ?? rackForContext.uid })
                  : t('liquid.btn_create_crh_disabled')}
              </button>
              <button
                id="btn-create-coldplate"
                className="btn"
                style={rackForContext ? CONTEXT_BTN : undefined}
                disabled={!rackForContext}
                onClick={createColdPlate}
              >
                {rackForContext
                  ? t('liquid.btn_create_coldplate', {
                      name: rackForContext.meta?.name ?? rackForContext.uid,
                    })
                  : t('liquid.btn_create_coldplate_disabled')}
              </button>
              <button
                id="btn-create-heatsource"
                className="btn"
                style={server ? HEAT_BTN : undefined}
                disabled={!server}
                onClick={createHeatSource}
              >
                {server
                  ? t('liquid.btn_create_heatsource')
                  : t('liquid.btn_create_heatsource_disabled')}
              </button>
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
