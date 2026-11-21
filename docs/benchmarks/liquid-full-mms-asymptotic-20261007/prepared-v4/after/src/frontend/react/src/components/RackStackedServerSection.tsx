import { useState } from 'react'
import type { Construction } from '../schema/cfdproj'
import { geometryPure } from '../legacy/geometryBuilder'
import { rackState } from '../legacy/cfdprojModules'
import {
  findCandidateDepthConflicts,
  resolveServerDepth,
  type EquipmentKind,
} from '../geometry/rackSlots'
import { addRackServerQuick } from '../geometry/rackServers'
import { requestSelectNode } from '../interaction/nodeJump'
import { serverNodeKey } from '../interaction/treeNodes'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'

/**
 * The servers stacked in one U range, and the form that adds another (spec §7.1).
 *
 * Rendered by the slot inspector — single slot or range — whenever the selected
 * U's already hold a server. Before the stacked-servers spec this selection had
 * no add path at all: "occupied ⇒ refuse" was the rule at four layers, and a
 * 1U switch recessed behind a 5U fan server at the same U could not be built.
 *
 * The blocking rule is the shared depth predicate, dry-run before anything is
 * written (spec §7.3): a U range that intersects an existing server's depth
 * interval disables the confirm button and names the other server; touching
 * faces are legal. "Auto-fit into the largest free gap" is the one-click answer
 * — the widest depth interval no selected server occupies, with the new box
 * centred in it at min(gap, 0.3 m) deep.
 *
 * Confirm is ONE `store.edit` (through `addRackServerQuick`), and the selection
 * moves to the new server so its inspector opens on the record just placed.
 */

export interface RackStackedServerSectionProps {
  model: Construction
  /** The selected U-slots, 0-based, in any order. */
  indices: number[]
}

/** One existing server's row, as the section reads and draws it. */
export interface StackedServerRow {
  id: string
  name: string
  kind: string
  startUnit: number
  endUnit: number
  inletOffset: number
  outletOffset: number
  /** Absolute depth box in rack-local Z: inlet face 0, outlet face D. */
  z0: number
  z1: number
}

type PureApi = {
  getRackServersForSlot(
    model: unknown,
    slotIndex: number,
  ): {
    id: string
    name?: string
    kind?: string
    startUnit: number
    spanUnits: number
    inletOffset: number
    outletOffset: number
  }[]
  getServerDepthRange(model: unknown, server: unknown): { z0: number; z1: number }
}

const pure = () => geometryPure() as unknown as PureApi

/** Rack depth D in metres — the axis every bar and offset here lives on. */
export function rackDepthOf(model: Construction): number {
  const z = Number((model.geometry as { size?: { z?: number } } | undefined)?.size?.z ?? 0)
  return Number.isFinite(z) && z > 0 ? z : 0
}

/**
 * Every server covering ANY of the given slots, deduplicated and sorted by
 * depth — the reading order of the depth axis, nearest the inlet plane first.
 *
 * Read through the SHARED `getRackServersForSlot` / `getServerDepthRange` so
 * the rows, the bars and the conflict predicate all place a server at the one
 * Z the normalizer gave it.
 */
export function stackedServersAt(model: Construction, indices: number[]): StackedServerRow[] {
  const seen = new Map<string, StackedServerRow>()
  for (const index of indices) {
    for (const view of pure().getRackServersForSlot(model, index)) {
      if (seen.has(view.id)) continue
      const range = pure().getServerDepthRange(model, view)
      seen.set(view.id, {
        id: view.id,
        name: view.name ?? view.id,
        kind: String(view.kind ?? ''),
        startUnit: Number(view.startUnit ?? 0),
        endUnit: Number(view.startUnit ?? 0) + Math.max(1, Number(view.spanUnits ?? 1)) - 1,
        inletOffset: Number(view.inletOffset ?? 0),
        outletOffset: Number(view.outletOffset ?? 0),
        z0: Number(range.z0 ?? 0),
        z1: Number(range.z1 ?? 0),
      })
    }
  }
  return [...seen.values()].sort((a, b) => a.z0 - b.z0 || a.z1 - b.z1)
}

export interface DepthInterval {
  z0: number
  z1: number
}

/** The complement of a set of intervals within [0, D], merged and sorted. */
export function freeDepthGaps(intervals: readonly DepthInterval[], D: number): DepthInterval[] {
  if (!(D > 0)) return []
  const sorted = [...intervals]
    .filter((i) => i.z1 > i.z0)
    .sort((a, b) => a.z0 - b.z0)
  const gaps: DepthInterval[] = []
  let cursor = 0
  for (const box of sorted) {
    if (box.z0 > cursor) gaps.push({ z0: cursor, z1: Math.min(box.z0, D) })
    cursor = Math.max(cursor, box.z1)
    if (cursor >= D) break
  }
  if (cursor < D) gaps.push({ z0: cursor, z1: D })
  return gaps.filter((g) => g.z1 - g.z0 > 1e-9)
}

/** The widest free depth interval, or null when the rack depth is full. */
export function largestFreeDepthGap(
  intervals: readonly DepthInterval[],
  D: number,
): DepthInterval | null {
  let best: DepthInterval | null = null
  for (const gap of freeDepthGaps(intervals, D)) {
    if (!best || gap.z1 - gap.z0 > best.z1 - best.z0) best = gap
  }
  return best
}

/**
 * Where "auto-fit" puts a new server: centred in the largest free gap, at
 * min(gap width, 0.3 m) deep (spec §7.1). Offsets are the absolute pair —
 * inletOffset is z0, outletOffset is D minus the far face.
 */
export function autoFitPlacement(
  intervals: readonly DepthInterval[],
  D: number,
): { inletOffset: number; outletOffset: number } | null {
  const gap = largestFreeDepthGap(intervals, D)
  if (!gap) return null
  const width = gap.z1 - gap.z0
  const depth = Math.min(width, 0.3)
  const z0 = gap.z0 + (width - depth) / 2
  return {
    inletOffset: r3(z0),
    outletOffset: r3(D - (z0 + depth)),
  }
}

/** The parts of a set of intervals that intersect the candidate — what the preview tints red. */
export function overlapSegments(
  candidate: DepthInterval,
  intervals: readonly DepthInterval[],
): DepthInterval[] {
  const out: DepthInterval[] = []
  for (const box of intervals) {
    const z0 = Math.max(candidate.z0, box.z0)
    const z1 = Math.min(candidate.z1, box.z1)
    // The predicate's epsilon: touching faces are a legal stack, not an overlap.
    if (z1 - z0 > 1e-6) out.push({ z0, z1 })
  }
  return out
}

/** Round to the millimetre grid the number boxes and sliders show. */
const r3 = (x: number) => Number(x.toFixed(3))

const KIND_LABEL: Record<string, 'rack.servers.kind_fan' | 'rack.servers.kind_nonfan' | 'rack.servers.kind_liquid'> = {
  fanServer: 'rack.servers.kind_fan',
  nonFan: 'rack.servers.kind_nonfan',
  liquidServer: 'rack.servers.kind_liquid',
}

/** One normalised 0..D depth bar. */
function DepthBar({
  D,
  segments,
  id,
  color,
}: {
  D: number
  segments: { z0: number; z1: number; overlap?: boolean }[]
  id: string
  color: string
}) {
  return (
    <div
      id={id}
      style={{
        position: 'relative',
        height: 8,
        width: '100%',
        background: 'var(--border, #333)',
        borderRadius: 2,
        overflow: 'hidden',
      }}
    >
      {segments.map((seg, i) => (
        <div
          key={i}
          data-overlap={seg.overlap ? 'true' : undefined}
          style={{
            position: 'absolute',
            left: `${(seg.z0 / D) * 100}%`,
            width: `${((seg.z1 - seg.z0) / D) * 100}%`,
            top: 0,
            bottom: 0,
            background: seg.overlap ? '#e05252' : color,
          }}
        />
      ))}
    </div>
  )
}

export function RackStackedServerSection({ model, indices }: RackStackedServerSectionProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<EquipmentKind>('fanServer')
  // null until the user types: the form then SHOWS the auto-fit placement (or
  // the rack plane when the depth is full), recomputed live against whatever
  // the rack holds — a state seed would go stale as servers are added.
  const [edited, setEdited] = useState<{ inletOffset: number; outletOffset: number } | null>(null)

  const D = rackDepthOf(model)
  const sorted = [...indices].sort((a, b) => a - b)
  const startU = sorted[0] ?? 0
  const endU = sorted[sorted.length - 1] ?? 0
  const span = endU - startU + 1
  const contiguous = sorted.length > 0 && span === sorted.length
  const rows = contiguous && D > 0 ? stackedServersAt(model, sorted) : []

  // Hooks stay above every early return: the section is mounted for the whole
  // life of the slot inspector, and a conditional hook order would remount
  // state mid-selection.
  const maxSum = rackState().SERVER_DEPTH_MAX_FRACTION * D
  const defaults = resolveServerDepth(model) ?? { inletOffset: 0, outletOffset: 0 }
  const candidate = edited ?? autoFitPlacement(rows, D) ?? defaults
  if (!rows.length || D <= 0) return null

  const candidateBox: DepthInterval = {
    z0: candidate.inletOffset,
    z1: D - candidate.outletOffset,
  }
  const overlaps = overlapSegments(candidateBox, rows)
  const outOfRange =
    !Number.isFinite(candidate.inletOffset) ||
    !Number.isFinite(candidate.outletOffset) ||
    candidate.inletOffset < 0 ||
    candidate.outletOffset < 0 ||
    candidate.inletOffset + candidate.outletOffset > maxSum ||
    candidateBox.z1 - candidateBox.z0 <= 1e-6
  const conflicts = outOfRange
    ? []
    : findCandidateDepthConflicts(model, {
        slotPosition: startU,
        slotOccupation: span,
        inletOffset: candidate.inletOffset,
        outletOffset: candidate.outletOffset,
      })
  const conflictName = conflicts.length
    ? rows.find((r) => r.id === conflicts[0]!.aUid)?.name ??
      rows.find((r) => r.id === conflicts[0]!.bUid)?.name ??
      null
    : null
  const blocked = outOfRange || conflicts.length > 0 || !contiguous

  function setOffsets(next: { inletOffset?: number; outletOffset?: number }) {
    setEdited({
      inletOffset: r3(next.inletOffset ?? candidate.inletOffset),
      outletOffset: r3(next.outletOffset ?? candidate.outletOffset),
    })
  }

  function autoFit() {
    const fit = autoFitPlacement(rows, D)
    if (fit) setEdited(fit)
  }

  function confirm() {
    const result = addRackServerQuick(model.uid, startU, span, kind, {
      inletOffset: candidate.inletOffset,
      outletOffset: candidate.outletOffset,
    })
    if (result.ok) {
      setOpen(false)
      setEdited(null)
      // The selection moves to the record just created: its own inspector is
      // where the depth just typed gets verified (spec §7.1).
      requestSelectNode(serverNodeKey(model.uid, result.uid))
    }
  }

  return (
    <div className="prop-group" id="rack-stacked-servers" style={{ borderLeftColor: '#7d5bed' }}>
      <div className="prop-title" style={{ color: '#9b7dff' }}>
        {t('rack.stacked_servers_title')}
      </div>
      {rows.map((row) => (
        <div key={row.id} id={`rss-row-${row.id}`} style={{ padding: '2px 0' }}>
          <div
            className="prop-row"
            style={{ justifyContent: 'space-between', gap: 6, alignItems: 'center' }}
          >
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {row.name}
            </span>
            <span style={{ flex: '0 0 auto', opacity: 0.8, fontFamily: 'monospace' }}>
              U{row.startUnit + 1}–U{row.endUnit + 1}
            </span>
            <span className="badge" style={{ flex: '0 0 auto' }}>
              {t(KIND_LABEL[row.kind] ?? 'rack.servers.kind_fan')}
            </span>
          </div>
          <div className="prop-row" style={{ gap: 6, alignItems: 'center' }}>
            <span className="mono-pct" style={{ flex: '0 0 auto' }} id={`rss-depth-${row.id}`}>
              {row.inletOffset.toFixed(3)} / {row.outletOffset.toFixed(3)} m
            </span>
            <div style={{ flex: 1 }}>
              <DepthBar id={`rss-bar-${row.id}`} D={D} color="#7d5bed" segments={[row]} />
            </div>
          </div>
        </div>
      ))}

      <button className="btn" id="rss-toggle" style={{ width: '100%' }} onClick={() => setOpen(!open)}>
        {t('rack.stacked_add')}
      </button>

      {open && (
        <div id="rss-add-form" style={{ paddingTop: 4 }}>
          <div className="prop-row">
            <label htmlFor="rss-kind">{t('label.equipment_type')}</label>
            <select
              id="rss-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as EquipmentKind)}
            >
              <option value="fanServer">{t('rack.servers.kind_fan')}</option>
              <option value="nonFan">{t('rack.servers.kind_nonfan')}</option>
              <option value="liquidServer">{t('rack.servers.kind_liquid')}</option>
            </select>
          </div>
          {(['inlet', 'outlet'] as const).map((side) => {
            const value = side === 'inlet' ? candidate.inletOffset : candidate.outletOffset
            const pctValue = Math.round((value / D) * 1000) / 10
            return (
              <div className="prop-row" key={side}>
                <label
                  htmlFor={`rss-${side}`}
                  style={{ fontSize: '0.7rem', color: side === 'inlet' ? '#4af' : '#f64' }}
                >
                  {side === 'inlet' ? t('equipment.extra_inlet') : t('equipment.extra_outlet')}
                </label>
                <NumberField
                  id={`rss-${side}`}
                  value={value}
                  width={64}
                  step={0.01}
                  min={0}
                  decimals={3}
                  onCommit={(v) =>
                    setOffsets(side === 'inlet' ? { inletOffset: v } : { outletOffset: v })
                  }
                />
                <input
                  id={`rss-${side}-pct`}
                  type="range"
                  min={0}
                  max={100}
                  step={0.1}
                  value={pctValue}
                  onChange={(e) => {
                    const meters = (Number(e.target.value) / 100) * D
                    setOffsets(
                      side === 'inlet' ? { inletOffset: meters } : { outletOffset: meters },
                    )
                  }}
                  style={{ flex: 1 }}
                />
                <span className="mono-pct">{pctValue.toFixed(1)}%</span>
              </div>
            )
          })}
          {/* The live preview: the candidate highlighted, and every part of it
              that intersects an existing box tinted red — the conflict drawn
              where the depth is being typed. */}
          <div className="prop-row" style={{ gap: 6, alignItems: 'center' }}>
            <span className="mono-pct" style={{ flex: '0 0 auto' }} id="rss-preview-depth">
              {candidate.inletOffset.toFixed(3)} / {candidate.outletOffset.toFixed(3)} m
            </span>
            <div style={{ flex: 1 }}>
              <DepthBar
                id="rss-preview"
                D={D}
                color="#2a7a5a"
                segments={[
                  ...rows.map((row) => ({ ...row, overlap: false })),
                  { z0: candidateBox.z0, z1: candidateBox.z1 },
                  ...overlaps.map((seg) => ({ ...seg, overlap: true })),
                ]}
              />
            </div>
          </div>
          {outOfRange ? (
            <div className="error" id="rss-range-error">
              {t('error.server_depth_range')}
            </div>
          ) : conflicts.length > 0 ? (
            <div className="error" id="rss-conflict">
              {conflictName
                ? t('rack.stacked_conflict', { name: conflictName })
                : t('error.server_depth_overlap')}
            </div>
          ) : null}
          <button className="btn" id="rss-auto-fit" style={{ width: '100%' }} onClick={autoFit}>
            {t('rack.stacked_auto_fit')}
          </button>
          <button
            className="btn primary"
            id="rss-confirm"
            style={{ width: '100%', margin: 0 }}
            disabled={blocked}
            onClick={confirm}
          >
            {t('rack.servers.add')}
          </button>
        </div>
      )}
    </div>
  )
}
