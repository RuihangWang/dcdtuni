/**
 * The pre-flight's screen half: the step overlay and the four stop dialogs.
 *
 * Built as plain DOM rather than React components, for two reasons. The gate
 * runs INSIDE `buildRunRequest` — a promise, not a render — and a modal that has
 * to be awaited from there would otherwise need a React host mounted on every
 * tab that can start a run. And the legacy builds these same overlays
 * imperatively with ids the parity sweep addresses (`mesh-progress-overlay`,
 * `digit-name-warning-dialog`, `floor-height-error-dialog`,
 * `room-wall-gap-error-dialog`, `seed-collision-warning-dialog`), so reusing the
 * ids means the two apps can be compared control for control.
 *
 * Text goes in through `textContent`, never `innerHTML`. Every string in these
 * dialogs is a user-typed model name.
 *
 * Ports `_createProgressOverlay` (STL_generate.html:27804),
 * `_showDigitNameWarningDialog` (:27878), `showFloorHeightErrorDialog` (:27927)
 * and `showSeedCollisionWarningDialog` (:27959).
 */

import { t } from '../i18n'
import type {
  DigitNameModel,
  FloorHeightError,
  RoomWallGapError,
  SeedCollisionWarning,
  SheetGap,
} from './meshPreflight'

const OVERLAY_BASE =
  'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:12000;' +
  'display:flex;align-items:center;justify-content:center;padding:20px;'

const CLIP = 'polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,8px 100%,0 calc(100% - 8px))'

function panel(width: string, borderColor: string): HTMLDivElement {
  const dialog = document.createElement('div')
  dialog.style.cssText =
    `width:${width};max-height:min(78vh,760px);overflow:hidden;display:flex;flex-direction:column;` +
    `background:var(--file-browser-bg,#10151c);border:1px solid ${borderColor};` +
    `box-shadow:0 24px 60px rgba(0,0,0,0.45);clip-path:${CLIP};`
  return dialog
}

function header(text: string, color: string, borderColor: string): HTMLDivElement {
  const el = document.createElement('div')
  el.style.cssText =
    `padding:14px 18px;border-bottom:1px solid ${borderColor};font-family:'Orbitron',monospace;` +
    `font-size:1rem;color:${color};letter-spacing:0.04em;`
  el.textContent = text
  return el
}

function description(text: string): HTMLDivElement {
  const el = document.createElement('div')
  el.style.cssText = 'padding:14px 18px 0;color:var(--text-dim);font-size:0.84rem;line-height:1.7;'
  el.textContent = text
  return el
}

function itemList(): HTMLDivElement {
  const el = document.createElement('div')
  el.style.cssText = 'padding:14px 18px;overflow:auto;display:flex;flex-direction:column;gap:8px;'
  return el
}

function item(title: string, body: string | null, accent: string): HTMLDivElement {
  const row = document.createElement('div')
  row.style.cssText = `padding:10px 12px;border:1px solid ${accent}44;background:${accent}11;font-size:0.82rem;line-height:1.65;`
  const head = document.createElement('div')
  head.style.cssText = `color:${accent};font-family:'Share Tech Mono',monospace;margin-bottom:4px;`
  head.textContent = title
  row.appendChild(head)
  if (body) {
    const detail = document.createElement('div')
    detail.textContent = body
    row.appendChild(detail)
  }
  return row
}

function footer(borderColor: string): HTMLDivElement {
  const el = document.createElement('div')
  el.style.cssText = `display:flex;justify-content:flex-end;gap:10px;padding:14px 18px;border-top:1px solid ${borderColor};`
  return el
}

function button(id: string, label: string, primary = false): HTMLButtonElement {
  const btn = document.createElement('button')
  btn.id = id
  btn.className = primary ? 'btn primary' : 'btn'
  btn.textContent = label
  return btn
}

function mount(id: string): HTMLDivElement {
  document.getElementById(id)?.remove()
  const overlay = document.createElement('div')
  overlay.id = id
  overlay.style.cssText = OVERLAY_BASE
  return overlay
}

// ---------------------------------------------------------------------------
// The step overlay
// ---------------------------------------------------------------------------

/**
 * The steps, in the order THIS app performs them.
 *
 * The legacy lists cfdrun first because its floor-height check reads the built
 * manifest (STL_generate.html:27911). The collectors here read the project
 * instead, so the checks run before anything is built — which means a project
 * with an unusable name fails in milliseconds rather than after tessellating
 * every rack. Showing the rows in a different order from the work would tick
 * "build cfdrun" green before it had run.
 */
const STEP_IDS = ['preflight', 'cfdrun', 'stl', 'upload', 'init'] as const
export type ProgressStep = (typeof STEP_IDS)[number]

const STEP_LABEL_KEYS = {
  cfdrun: 'progress.step_cfdrun',
  preflight: 'progress.step_preflight',
  stl: 'progress.step_stl',
  upload: 'progress.step_upload',
  init: 'progress.step_init',
} as const

const OVERLAY_ID = 'mesh-progress-overlay'
const SPIN_STYLE_ID = 'mp-spin-style'

function ensureSpinKeyframes() {
  if (document.getElementById(SPIN_STYLE_ID)) return
  const style = document.createElement('style')
  style.id = SPIN_STYLE_ID
  style.textContent = '@keyframes mp-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }'
  document.head.appendChild(style)
}

/**
 * Show the five-step overlay.
 *
 * Idempotent: a second call while one is up re-uses it rather than stacking a
 * second modal, because both the panel that starts the run and the gate that
 * drives the steps want to be able to ask for it.
 */
export function openMeshProgress(title: string): void {
  if (document.getElementById(OVERLAY_ID)) {
    setMeshProgressTitle(title)
    return
  }
  ensureSpinKeyframes()
  const overlay = mount(OVERLAY_ID)
  overlay.style.pointerEvents = 'none'
  const dialog = panel('min(480px,90vw)', 'rgba(0,212,255,0.35)')
  dialog.style.pointerEvents = 'auto'
  dialog.style.maxHeight = ''

  const head = header(title, 'var(--accent3)', 'rgba(0,212,255,0.2)')
  head.id = 'mp-title'
  head.style.fontSize = '0.95rem'
  dialog.appendChild(head)

  const steps = document.createElement('div')
  steps.id = 'mp-steps'
  steps.style.cssText = 'padding:18px;display:flex;flex-direction:column;gap:10px;'
  for (const id of STEP_IDS) {
    const row = document.createElement('div')
    row.id = `mp-step-${id}`
    row.style.cssText =
      'display:flex;align-items:center;gap:10px;font-size:0.82rem;color:var(--text-dim);transition:color 0.3s;'
    const icon = document.createElement('span')
    icon.className = 'mp-icon'
    icon.style.cssText = 'width:18px;text-align:center;font-size:14px;'
    icon.textContent = '○'
    const label = document.createElement('span')
    label.textContent = t(STEP_LABEL_KEYS[id])
    row.append(icon, label)
    steps.appendChild(row)
  }
  dialog.appendChild(steps)

  const barWrap = document.createElement('div')
  barWrap.style.cssText = 'padding:0 18px 14px;'
  const track = document.createElement('div')
  track.style.cssText = 'height:3px;background:rgba(0,212,255,0.12);border-radius:2px;overflow:hidden;'
  const bar = document.createElement('div')
  bar.id = 'mp-bar'
  bar.style.cssText =
    'height:100%;width:0%;background:linear-gradient(90deg,var(--accent3),var(--accent1));transition:width 0.4s ease;'
  track.appendChild(bar)
  barWrap.appendChild(track)
  dialog.appendChild(barWrap)

  overlay.appendChild(dialog)
  document.body.appendChild(overlay)
}

export function setMeshProgressTitle(text: string): void {
  const el = document.getElementById('mp-title')
  if (el) el.textContent = text
}

/** Mark a step running and everything before it done. No-op with no overlay up. */
export function setMeshProgressStep(step: ProgressStep): void {
  if (!document.getElementById(OVERLAY_ID)) return
  const active = STEP_IDS.indexOf(step)
  STEP_IDS.forEach((id, i) => {
    const row = document.getElementById(`mp-step-${id}`)
    const icon = row?.querySelector<HTMLElement>('.mp-icon')
    if (!row || !icon) return
    if (i === active) {
      row.style.color = 'var(--accent3)'
      icon.textContent = '⟳'
      icon.style.animation = 'mp-spin 1s linear infinite'
    } else if (i < active) {
      row.style.color = '#4ecdc4'
      icon.textContent = '✓'
      icon.style.animation = ''
    }
  })
  const bar = document.getElementById('mp-bar')
  if (bar) bar.style.width = `${Math.round(((active + 0.5) / STEP_IDS.length) * 100)}%`
}

export function closeMeshProgress(): void {
  document.getElementById(OVERLAY_ID)?.remove()
}

// ---------------------------------------------------------------------------
// The stop dialogs
// ---------------------------------------------------------------------------

function awaitDismiss(overlay: HTMLDivElement, buttons: { el: HTMLButtonElement; value: boolean }[]) {
  return new Promise<boolean>((resolve) => {
    for (const { el, value } of buttons) {
      el.onclick = () => {
        overlay.remove()
        resolve(value)
      }
    }
    document.body.appendChild(overlay)
  })
}

/**
 * The type shown beside an offending name, as the legacy labels it
 * (STL_generate.html:27893). Keyed by bucket here because that is what the
 * collector has — `kind` is non-enumerable and a cloned model can be missing it.
 */
const KIND_LABEL: Record<string, string> = {
  racks: 'RACK',
  boxes: 'BOX',
  acus: 'ACU',
  cdus: 'CDU',
  walls: 'WALL',
  baffles: 'BAFFLE',
  floors: 'FLOOR',
  customMeshes: 'STL',
}

/** Names OpenFOAM cannot use. Acknowledge-only: there is nothing to override. */
export function showDigitNameDialog(models: DigitNameModel[]): Promise<boolean> {
  closeMeshProgress()
  const overlay = mount('digit-name-warning-dialog')
  const dialog = panel('min(600px,92vw)', 'rgba(255,80,80,0.4)')
  dialog.appendChild(header(`${t('error.digit_name_title')}`, '#ff5050', 'rgba(255,80,80,0.25)'))
  dialog.appendChild(description(t('error.digit_name')))
  const list = itemList()
  for (const m of models) {
    list.appendChild(item(`${m.name} (${KIND_LABEL[m.bucket] ?? m.bucket})`, null, '#ff8080'))
  }
  dialog.appendChild(list)
  const foot = footer('rgba(255,80,80,0.18)')
  const ok = button('digit-name-ok-btn', t('btn.ok'))
  foot.appendChild(ok)
  dialog.appendChild(foot)
  overlay.appendChild(dialog)
  return awaitDismiss(overlay, [{ el: ok, value: false }])
}

/** A floor above the ceiling. Acknowledge-only — the geometry has to change. */
export function showFloorHeightDialog(errors: FloorHeightError[]): Promise<boolean> {
  closeMeshProgress()
  const overlay = mount('floor-height-error-dialog')
  const dialog = panel('min(760px,92vw)', 'rgba(255,70,70,0.45)')
  dialog.appendChild(
    header(t('dialog.floor_height_error_title'), '#ff4646', 'rgba(255,70,70,0.25)'),
  )
  dialog.appendChild(description(t('dialog.floor_height_error_desc')))
  const list = itemList()
  for (const e of errors) {
    list.appendChild(
      item(
        e.name,
        t('dialog.floor_height_error_item', {
          name: e.name,
          y: e.y.toFixed(4),
          roomHeight: e.roomHeight.toFixed(4),
        }),
        '#ff7b7b',
      ),
    )
  }
  dialog.appendChild(list)
  const foot = footer('rgba(255,70,70,0.25)')
  const ok = button('floor-height-error-ok-btn', t('btn.floor_height_error_ok'))
  foot.appendChild(ok)
  dialog.appendChild(foot)
  overlay.appendChild(dialog)
  return awaitDismiss(overlay, [{ el: ok, value: false }])
}

export type RoomWallGapChoice = 'ok' | 'extended'

/**
 * Room walls that stop below the ceiling, with an offer to raise them.
 *
 * Either button stops the run: after raising the walls the user sees them and
 * presses run again, as in the legacy `showRoomWallGapErrorDialog`, whose ids
 * these are. `onExtend` makes the edit and returns how many walls it changed;
 * the dialog resolves 'extended' only when that is more than zero. With no
 * `onExtend` there is no extend button.
 */
export function showRoomWallGapDialog(
  errors: RoomWallGapError[],
  onExtend?: () => number,
): Promise<RoomWallGapChoice> {
  closeMeshProgress()
  const overlay = mount('room-wall-gap-error-dialog')
  const dialog = panel('min(760px,92vw)', 'rgba(255,70,70,0.45)')
  dialog.appendChild(header(t('dialog.room_wall_gap_title'), '#ff4646', 'rgba(255,70,70,0.25)'))
  dialog.appendChild(description(t('dialog.room_wall_gap_desc')))
  const list = itemList()
  for (const e of errors) {
    const row = item(
      e.name,
      t('dialog.room_wall_gap_item', {
        name: e.name,
        top: e.top.toFixed(4),
        roomHeight: e.roomHeight.toFixed(4),
      }),
      '#ff7b7b',
    )
    row.classList.add('room-wall-gap-item')
    row.dataset.wall = e.name
    list.appendChild(row)
  }
  dialog.appendChild(list)
  const foot = footer('rgba(255,70,70,0.25)')
  const ok = button('room-wall-gap-error-ok-btn', t('btn.floor_height_error_ok'))
  foot.appendChild(ok)
  const extend = onExtend
    ? button('room-wall-gap-extend-btn', t('btn.room_wall_gap_extend'), true)
    : null
  if (extend) foot.appendChild(extend)
  dialog.appendChild(foot)
  overlay.appendChild(dialog)
  return new Promise<RoomWallGapChoice>((resolve) => {
    const close = (choice: RoomWallGapChoice) => {
      overlay.remove()
      resolve(choice)
    }
    ok.addEventListener('click', () => close('ok'))
    extend?.addEventListener('click', () => {
      let changed = 0
      // A throwing edit still closes the dialog: an open modal nobody can
      // dismiss would leave the run hanging on this promise.
      try {
        changed = onExtend?.() ?? 0
      } finally {
        close(changed > 0 ? 'extended' : 'ok')
      }
    })
    document.body.appendChild(overlay)
  })
}

export type SheetGapChoice = 'back' | 'continue' | 'closed'

/**
 * Baffle edges that stop a few millimetres short, with an offer to close them.
 *
 * Close is the recommended button: it makes the edit (one undo step) and stops
 * the run so the user sees the moved sheets and presses run again, like the
 * room-wall dialog. Continue meshes the geometry as drawn; Back stops.
 */
export function showSheetGapDialog(
  gaps: SheetGap[],
  onClose: () => number,
): Promise<SheetGapChoice> {
  closeMeshProgress()
  const overlay = mount('sheet-gap-warning-dialog')
  const dialog = panel('min(760px,92vw)', 'rgba(255,179,71,0.45)')
  dialog.appendChild(header(t('dialog.sheet_gap_title'), '#ffb347', 'rgba(255,179,71,0.25)'))
  dialog.appendChild(description(t('dialog.sheet_gap_desc')))
  const list = itemList()
  for (const g of gaps) {
    const row = item(
      g.name,
      t(g.move === 'plane' ? 'dialog.sheet_gap_plane_item' : 'dialog.sheet_gap_item', {
        edge: `${g.axis}${g.side < 0 ? '-' : '+'}`,
        gap: (g.gap * 1000).toFixed(1),
        target: g.targetName,
      }),
      '#ffb347',
    )
    row.classList.add('sheet-gap-item')
    row.dataset.sheet = g.name
    list.appendChild(row)
  }
  dialog.appendChild(list)
  const foot = footer('rgba(255,179,71,0.25)')
  const back = button('sheet-gap-back-btn', t('btn.seed_collision_back'))
  const go = button('sheet-gap-continue-btn', t('btn.sheet_gap_continue'))
  const fix = button('sheet-gap-close-btn', t('btn.sheet_gap_close'), true)
  foot.append(back, go, fix)
  dialog.appendChild(foot)
  overlay.appendChild(dialog)
  return new Promise<SheetGapChoice>((resolve) => {
    const done = (choice: SheetGapChoice) => {
      overlay.remove()
      resolve(choice)
    }
    back.addEventListener('click', () => done('back'))
    go.addEventListener('click', () => done('continue'))
    fix.addEventListener('click', () => {
      let changed = 0
      try {
        changed = onClose()
      } finally {
        done(changed > 0 ? 'closed' : 'back')
      }
    })
    document.body.appendChild(overlay)
  })
}

/** Seeds inside a solid. Resolves true when the user chooses to run anyway. */
export function showSeedCollisionDialog(warnings: SeedCollisionWarning[]): Promise<boolean> {
  closeMeshProgress()
  const overlay = mount('seed-collision-warning-dialog')
  const dialog = panel('min(760px,92vw)', 'rgba(90,167,255,0.35)')
  dialog.appendChild(header(t('dialog.seed_collision_title'), '#5aa7ff', 'rgba(90,167,255,0.18)'))
  dialog.appendChild(description(t('dialog.seed_collision_desc')))
  const list = itemList()
  for (const w of warnings) {
    list.appendChild(
      item(
        w.surfaceName,
        t('dialog.seed_collision_item', {
          side: w.side,
          objectType: w.objectType,
          objectName: w.objectName,
          x: w.point.x.toFixed(4),
          y: w.point.y.toFixed(4),
          z: w.point.z.toFixed(4),
        }),
        '#ffb347',
      ),
    )
  }
  dialog.appendChild(list)
  const foot = footer('rgba(90,167,255,0.18)')
  const back = button('seed-collision-cancel-btn', t('btn.seed_collision_back'))
  const ignore = button('seed-collision-ignore-btn', t('btn.seed_collision_ignore'), true)
  foot.append(back, ignore)
  dialog.appendChild(foot)
  overlay.appendChild(dialog)
  return awaitDismiss(overlay, [
    { el: back, value: false },
    { el: ignore, value: true },
  ])
}

/** Explain the conversion needed when a solve cannot reuse the stored partitions. */
export function showSolvePartitionDialog(mesh: number, solve: number): Promise<boolean> {
  closeMeshProgress()
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const overlay = mount('solve-partition-warning-dialog')
  const dialog = panel('min(760px,92vw)', 'rgba(90,167,255,0.35)')
  dialog.setAttribute('role', 'dialog')
  dialog.setAttribute('aria-modal', 'true')
  dialog.setAttribute('aria-labelledby', 'solve-partition-title')
  dialog.setAttribute('aria-describedby', 'solve-partition-description')
  const title = header(t('dialog.solve_partition_title'), '#5aa7ff', 'rgba(90,167,255,0.18)')
  title.id = 'solve-partition-title'
  const desc = description(t('dialog.solve_partition_desc'))
  desc.id = 'solve-partition-description'
  dialog.append(title, desc)
  const list = itemList()
  list.appendChild(item(
    t('dialog.solve_partition_counts', { mesh, solve }),
    t(solve > 1 ? 'dialog.solve_partition_parallel' : 'dialog.solve_partition_serial'),
    '#ffb347',
  ))
  const hint = document.createElement('div')
  hint.style.cssText = 'color:var(--text-dim);font-size:0.84rem;line-height:1.7;'
  hint.textContent = t('dialog.solve_partition_hint', { mesh })
  if (mesh > 1) list.appendChild(hint)
  dialog.appendChild(list)
  const foot = footer('rgba(90,167,255,0.18)')
  const back = button('solve-partition-cancel-btn', t('btn.solve_partition_back'))
  const proceed = button('solve-partition-continue-btn', t('btn.solve_partition_continue'), true)
  back.type = proceed.type = 'button'
  foot.append(back, proceed)
  dialog.appendChild(foot)
  overlay.appendChild(dialog)
  return new Promise<boolean>((resolve) => {
    const close = (value: boolean) => {
      document.removeEventListener('keydown', onKey, true)
      overlay.remove()
      if (previousFocus?.isConnected) previousFocus.focus()
      resolve(value)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close(false)
      } else if (event.key === 'Tab') {
        event.preventDefault()
        event.stopPropagation()
        if (document.activeElement === back) proceed.focus()
        else back.focus()
      }
    }
    back.onclick = () => close(false)
    proceed.onclick = () => close(true)
    document.addEventListener('keydown', onKey, true)
    document.body.appendChild(overlay)
    back.focus()
  })
}

/**
 * Rack doors that disagree about open area.
 *
 * The one string here with no dictionary key. The legacy has none either: it
 * looks up an `error.door_porosity_mismatch` that is in neither locale file and
 * falls back to a hard-coded Chinese sentence, so the message is untranslated
 * there too. English rather than Chinese so the panel is not half-translated
 * under an English locale; the door names and percentages, which are what the
 * user acts on, need no translation either way. Inventing a key is not the fix —
 * the two apps share the dictionaries, and a key only one of them writes is how
 * they drift.
 */
const DOOR_POROSITY_MESSAGE =
  'All rack perforated doors must use the same open area (%). Current settings:'

export function showDoorPorosityDialog(doors: { name: string; pct: number }[]): Promise<boolean> {
  closeMeshProgress()
  const overlay = mount('door-porosity-warning-dialog')
  const dialog = panel('min(600px,92vw)', 'rgba(255,140,0,0.4)')
  dialog.appendChild(header(t('rack.perforated_door'), '#ffb347', 'rgba(255,140,0,0.25)'))
  dialog.appendChild(description(DOOR_POROSITY_MESSAGE))
  const list = itemList()
  for (const d of doors) list.appendChild(item(d.name, `${t('label.open_area_pct')}: ${d.pct}`, '#ffb347'))
  dialog.appendChild(list)
  const foot = footer('rgba(255,140,0,0.25)')
  const ok = button('door-porosity-ok-btn', t('btn.ok'))
  foot.appendChild(ok)
  dialog.appendChild(foot)
  overlay.appendChild(dialog)
  return awaitDismiss(overlay, [{ el: ok, value: false }])
}
