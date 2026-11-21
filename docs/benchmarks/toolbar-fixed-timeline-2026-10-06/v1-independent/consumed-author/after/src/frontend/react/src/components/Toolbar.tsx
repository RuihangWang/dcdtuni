import { useEffect, useState, useSyncExternalStore } from 'react'
import { applySceneTransform } from '../interaction/sceneTransform'
import { transformModels, type TransformAction } from '../interaction/transformActions'
import { FlowMonitor } from './FlowMonitor'
import { allModels, type CfdProject } from '../schema/cfdproj'
import { LOCALES, setLocale, type Locale } from '../i18n'
import { isPreviewActive, subscribePreview, togglePreviewMode } from '../io/stlPreview'
import { useTranslation } from '../useTranslation'
import { ShortcutSettingsDialog } from './ShortcutSettingsDialog'
import { ModeSwitch } from '../transient/ModeSwitch'
import { TimelineStrip } from '../transient/TimelineStrip'
import { isUiFrozen, subscribeSession } from '../run/session'
import { requestSelectionFocus } from '../interaction/frameSelection'
import {isLiquidModeActive,subscribeLiquidMode} from '../liquid/liquidModeState'
import {showCabinetPipes,setShowCabinetPipes,subscribeClosedCabinetDisplay,
  leafDisplayRevision,closedCabinetDiagnostics} from '../liquid/closedCabinetDisplay'

/** Viewport actions grouped around the active simulation mode and renderer. */
export const THEME_STORAGE_KEY = 'dcdtuni.theme'
type Theme = 'cyberpunk-dark' | 'vscode-dark'

function storedTheme(): Theme {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === 'vscode-dark' ? 'vscode-dark' : 'cyberpunk-dark'
  } catch {
    return 'cyberpunk-dark'
  }
}

export interface ViewOptions {
  showGrid: boolean
  lights: boolean
  snapFace: boolean
  snapEdge: boolean
  snapGrid: boolean
  rotationSnap: boolean
  gridSpacingMm: number
}

/**
 * `showGrid` starts ON, which is the one place this bar deliberately parts from
 * the legacy (`getDefaultViewSettings`, STL_generate.html:2520, starts it off).
 * An empty viewport with no grid gives a new room no sense of scale or of where
 * the origin is, and the first thing a user does is turn it on.
 *
 * `gridSpacingMm` is the legacy's 600, not 100: at 100 mm the lines are closer
 * together than the room is deep and the grid reads as a wash rather than as a
 * measure. A loaded project overrides this with its own tile size.
 */
export const DEFAULT_VIEW_OPTIONS: ViewOptions = {
  showGrid: true,
  lights: true,
  snapFace: true,
  snapEdge: true,
  snapGrid: false,
  rotationSnap: false,
  gridSpacingMm: 600,
}

export interface ToolbarProps {
  view: ViewOptions
  /** Accepts an updater so two toggles in one batch cannot clobber each other. */
  onChange: (next: ViewOptions | ((prev: ViewOptions) => ViewOptions)) => void
  /**
   * Kept for the callers that pass it, and deliberately NOT read: the transform
   * buttons fall through to the whole scene when nothing is selected, so gating
   * them on a selection is what made that fallback unreachable.
   */
  hasSelection: boolean
  /** Every selected model — the transform buttons act on all of them. */
  selectedUids?: string[]
  /** The model the transform buttons act on. */
  selectedUid?: string | null
  project: CfdProject | null
}

export function Toolbar({
  view,
  onChange,
  selectedUid,
  selectedUids,
  project,
}: ToolbarProps) {
  // A multi-selection transforms as a GROUP about the FIRST selected model's
  // anchor — not the last-clicked one. The legacy uses
  // `anchorModelId || selectedIds[0]` (STL_generate.html:15374), and picking
  // the active model instead spins the row about the wrong end: every other
  // rack lands somewhere different.
  const targets = selectedUids?.length ? selectedUids : selectedUid ? [selectedUid] : []
  const frozen = useSyncExternalStore(subscribeSession, isUiFrozen, () => false)
  const liquidMode = useSyncExternalStore(subscribeLiquidMode,isLiquidModeActive,()=>false)
  useSyncExternalStore(subscribeClosedCabinetDisplay,leafDisplayRevision,()=>0)
  const showInterior = showCabinetPipes()
  const cabinetWarnings = closedCabinetDiagnostics()
  const { t, locale } = useTranslation()
  const [collapsed, setCollapsed] = useState(false)
  const [expandedGroup, setExpandedGroup] = useState<'model' | 'view' | 'preferences' | null>(null)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [theme, setTheme] = useState<Theme>(storedTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem(THEME_STORAGE_KEY, theme) } catch { /* Private browser storage can be unavailable. */ }
  }, [theme])
  // The STL Preview button reads a module-level mode, so this bar has to be
  // told when it changes — the mode can also be left from the preview's own
  // panel, and the button would otherwise still say "STL Preview".
  const [, bumpPreview] = useState(0)
  useEffect(() => subscribePreview(() => bumpPreview((n) => n + 1)), [])
  const previewOn = isPreviewActive()
  // The mode has nothing to show without geometry, and the legacy refuses to
  // enter it with an alert (`toggleStlPreviewMode`, STL_generate.html:11731).
  const modelCount = project ? allModels(project).length : 0
  // Per-axis angle, kept in the toolbar rather than the project: it is what the
  // NEXT press will apply, not a property of any model.
  const [rotAngle, setRotAngle] = useState<
    Record<'x' | 'y' | 'z', { mode: string; custom: string }>
  >({
    x: { mode: '90', custom: '45' },
    y: { mode: '90', custom: '45' },
    z: { mode: '90', custom: '45' },
  })

  /**
   * Flip one view option.
   *
   * A functional update, not `{ ...view, [key]: value }`: `view` is the prop
   * this render captured, so two toggles inside one React batch would both
   * spread the SAME stale object and the second would undo the first. The
   * selection had the same bug and lost a ctrl-click for it.
   */
  function toggle<K extends keyof ViewOptions>(key: K, value: ViewOptions[K]) {
    onChange((prev) => ({ ...prev, [key]: value }))
  }

  /**
   * The selection if there is one, the whole scene otherwise.
   *
   * `transformModels` returns early on an empty list, so the two branches are
   * exclusive for the same reason the legacy's are: nothing selected means
   * nothing to transform but the wrapper.
   */
  function apply(action: TransformAction) {
    if (targets.length) transformModels(targets, action)
    else applySceneTransform(action)
  }

  return (
    <div id="toolbar" className={collapsed ? 'collapsed' : undefined}>
      <button
        id="toolbar-collapse-btn"
        onClick={() => setCollapsed((v) => !v)}
        title={collapsed ? t('toolbar.expand') : t('toolbar.collapse')}
        aria-label={collapsed ? t('toolbar.expand') : t('toolbar.collapse')}
        aria-expanded={!collapsed}
      >
        {collapsed ? '⌄' : '⌃'}
      </button>
      <div id="toolbar-content">
        <div id="toolbar-body">
        <div className="toolbar-primary">
          <ModeSwitch />
          <div id="renderer-switch-slot" />
        </div>
        <div className="toolbar-groups">
          <div className="toolbar-group-tabs">
            {([
              ['model', 'toolbar.model_tools', 'toolbar-model-tools'],
              ['view', 'toolbar.view_tools', 'toolbar-view-tools'],
              ['preferences', 'toolbar.preferences', 'toolbar-preferences'],
            ] as const).map(([group, label, panelId]) => (
              <button key={group} id={`${panelId}-toggle`} type="button"
                className="toolbar-group-toggle" aria-controls={panelId}
                aria-expanded={expandedGroup === group}
                onClick={() => setExpandedGroup(current => current === group ? null : group)}>
                <span>{t(label)}</span><span aria-hidden="true">{expandedGroup === group ? '⌃' : '⌄'}</span>
              </button>
            ))}
          </div>
          <section className="toolbar-tool-group" id="toolbar-model-tools"
            hidden={expandedGroup !== 'model'} aria-labelledby="toolbar-model-tools-toggle">
            <div className="toolbar-tool-content">
              {(['X', 'Y', 'Z'] as const).map((axis) => {
                const lower = axis.toLowerCase() as 'x' | 'y' | 'z'
                const angle = rotAngle[lower]
                return (
                  <div className="rot-group" data-axis={axis} key={axis}>
                    <select
                      className="rot-select"
                      id={`rot-sel-${lower}`}
                      value={angle.mode}
                      onChange={(e) =>
                        setRotAngle((prev) => ({
                          ...prev,
                          [lower]: { ...prev[lower], mode: e.target.value },
                        }))
                      }
                    >
                      <option value="90">90°</option>
                      <option value="180">180°</option>
                      <option value="270">270°</option>
                      <option value="custom">{t('toolbar.rot_custom')}</option>
                    </select>
                    {/* Only shown for 'custom', as in the legacy toolbar: a number box
                        beside a dropdown that already says 90° reads as two answers to
                        the same question. */}
                    {angle.mode === 'custom' && (
                      <input
                        className="rot-custom"
                        id={`rot-custom-${lower}`}
                        type="number"
                        step={1}
                        value={angle.custom}
                        onChange={(e) =>
                          setRotAngle((prev) => ({
                            ...prev,
                            [lower]: { ...prev[lower], custom: e.target.value },
                          }))
                        }
                      />
                    )}
                    <button
                      className="rot-apply-btn"
                      id={`rot-apply-${lower}`}
                      onClick={() => {
                        const deg =
                          angle.mode === 'custom' ? Number(angle.custom) || 90 : Number(angle.mode) || 90
                        apply({ kind: 'rotate', axis: lower, degrees: deg })
                      }}
                    >
                      {t(`toolbar.rot_apply_${lower}` as 'toolbar.rot_apply_x')}
                    </button>
                  </div>
                )
              })}

              {(['X', 'Y', 'Z'] as const).map((axis) => (
                <button
                  key={`mirror${axis}`}
                  className="tool-btn"
                  id={`mirror-${axis.toLowerCase()}`}
                  style={{ color: 'var(--hint-color)' }}
                  onClick={() => apply({ kind: 'mirror', axis: axis.toLowerCase() as 'x' | 'y' | 'z' })}
                >
                  {t(`toolbar.mirror_${axis.toLowerCase()}` as 'toolbar.mirror_x')}
                </button>
              ))}
              <button
                className="tool-btn"
                id="reset-transform"
                data-tone="danger"
                onClick={() => apply({ kind: 'reset' })}
              >
                {t('toolbar.reset_transform')}
              </button>

              <button
                id="rot-snap-toggle"
                className={`tool-btn${view.rotationSnap ? ' active' : ''}`}
                onClick={() => toggle('rotationSnap', !view.rotationSnap)}
              >
                {t('label.rotation_snap')}
              </button>
              <button
                id="snap-face-btn"
                className={`tool-btn${view.snapFace ? ' active' : ''}`}
                onClick={() => toggle('snapFace', !view.snapFace)}
                title={t('tooltip.snap_face')}
              >
                {t('toolbar.face_snap')}
              </button>
              <button
                id="snap-edge-btn"
                className={`tool-btn${view.snapEdge ? ' active' : ''}`}
                onClick={() => toggle('snapEdge', !view.snapEdge)}
                title={t('tooltip.snap_edge')}
              >
                {t('toolbar.edge_align')}
              </button>
              <button
                id="snap-grid-btn"
                className={`tool-btn${view.snapGrid ? ' active' : ''}`}
                onClick={() =>
                  // Mutually exclusive with face/edge snapping, as in the legacy
                  // editor — and a functional update, like `toggle` above and for the
                  // same reason: spreading the `view` THIS render captured undid
                  // whatever another toggle had set in the same batch.
                  onChange((prev) =>
                    prev.snapGrid
                      ? { ...prev, snapGrid: false }
                      : { ...prev, snapGrid: true, snapFace: false, snapEdge: false },
                  )
                }
                title={t('tooltip.snap_grid')}
              >
                {t('toolbar.grid_snap')}
              </button>
            </div>
          </section>
          <section className="toolbar-tool-group" id="toolbar-view-tools"
            hidden={expandedGroup !== 'view'} aria-labelledby="toolbar-view-tools-toggle">
            <div className="toolbar-tool-content">
              <button
                id="focus-selection-btn"
                className="tool-btn"
                disabled={!project || !targets.length || frozen}
                onClick={() => { if (!isUiFrozen()) requestSelectionFocus() }}
                title={t('toolbar.focus_selection')}
              >
                {t('toolbar.focus_selection')}
              </button>
              {liquidMode && <label className="tool-btn" title={t('toolbar.show_cabinet_pipes')}>
                <input id="show-cabinet-pipes" type="checkbox" checked={showInterior}
                  onChange={e=>setShowCabinetPipes(e.target.checked)}/>
                {t('toolbar.show_cabinet_pipes')}
              </label>}
              {liquidMode && cabinetWarnings.length>0 && <span role="note"
                title={cabinetWarnings.join('\n')}>{t('toolbar.cabinet_pipe_display_warning')}</span>}
              <button
                id="grid-toggle-btn"
                className={`tool-btn${view.showGrid ? ' active' : ''}`}
                onClick={() => toggle('showGrid', !view.showGrid)}
              >
                {view.showGrid ? t('toolbar.hide_grid') : t('toolbar.show_grid')}
              </button>
              <button
                id="light-toggle-btn"
                className={`tool-btn${view.lights ? ' active' : ''}`}
                onClick={() => toggle('lights', !view.lights)}
                title={t('tooltip.toggle_lights')}
              >
                {t('toolbar.toggle_lights')}
              </button>

              {/* The entry point into STL Preview.
                  A deliberate RESTORATION, not a copy of the legacy toolbar as it
                  stands: `refreshStlPreviewToggleButton` (STL_generate.html:3883)
                  still looks up `#toggle-preview-btn`, but no such element is left in
                  that page — so over there the mode can only be entered from the
                  console. Same id, so the two pages address it the same way. */}
              <button
                id="toggle-preview-btn"
                className={`tool-btn${previewOn ? ' active' : ''}`}
                disabled={!previewOn && modelCount === 0}
                title={!previewOn && modelCount === 0 ? t('error.no_models_for_preview') : undefined}
                onClick={() => togglePreviewMode()}
              >
                {previewOn ? t('btn.back_to_modeling') : t('btn.stl_preview')}
              </button>

              <div className="tool-inline">
                <span>{t('toolbar.grid_spacing')}</span>
                <input
                  id="grid-density-slider"
                  type="range"
                  min={10}
                  max={1000}
                  step={10}
                  value={view.gridSpacingMm}
                  onChange={(e) => toggle('gridSpacingMm', Number(e.target.value))}
                />
                <span className="badge" id="grid-density-badge">
                  {view.gridSpacingMm.toFixed(4)} mm
                </span>
              </div>

              <FlowMonitor project={project} />

            </div>
          </section>
          <section className="toolbar-tool-group" id="toolbar-preferences"
            hidden={expandedGroup !== 'preferences'} aria-labelledby="toolbar-preferences-toggle">
            <div className="toolbar-tool-content">
              <select
                id="lang-selector"
                aria-label={t('toolbar.language')}
                value={locale}
                onChange={(e) => setLocale(e.target.value as Locale)}
              >
                {LOCALES.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.label}
                  </option>
                ))}
              </select>
              <select
                id="theme-selector"
                aria-label={t('toolbar.theme')}
                value={theme}
                onChange={(e) => setTheme(e.target.value as Theme)}
              >
                <option value="cyberpunk-dark">{t('toolbar.theme_engineering')}</option>
                <option value="vscode-dark">{t('toolbar.theme_vscode')}</option>
              </select>
              <button
                id="shortcut-settings-btn"
                className={`tool-btn${shortcutsOpen ? ' active' : ''}`}
                title={t('tooltip.shortcut_settings')}
                onClick={() => setShortcutsOpen(true)}
              >
                {t('toolbar.shortcuts')}
              </button>
            </div>
          </section>
        </div>
        </div>
        {/* Reserve the whole wrapped timeline below the scrolling tools.
            It remains inside the collapse-owned content subtree. */}
        <TimelineStrip />
      </div>
      {shortcutsOpen && <ShortcutSettingsDialog onClose={() => setShortcutsOpen(false)} />}
    </div>
  )
}
