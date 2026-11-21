import { useState } from 'react'
import type { TranslationKey } from '../i18n'
import type { Construction } from '../schema/cfdproj'
import {
  createEquipmentAtSlot,
  mergeSlotsIntoEquipment,
  partitionAt,
  removeSlotsFromEquipment,
  dissolveThroughZone,
  EQUIPMENT_PRESETS,
  equipmentAtSlot,
  isSlotDeleted,
  isThroughSlot,
  removeEquipment,
  restoreOpposite,
  setRackPartitionDeleted,
  setRackSelectionDeleted,
  setSlotDeleted,
  setThroughZoneSigma,
  slotPartitionIndices,
  syncDeleteOpposite,
  syncDeleteTargets,
  throughZoneAt,
  throughZoneGroups,
  voidRunPartitions,
  voidRuns,
  type EquipmentKind,
  type RackSide,
} from '../geometry/rackSlots'
import {
  equipmentEndUnit,
  equipmentList,
  selectionContext,
  summarise,
} from '../geometry/rackSelection'
import { requestSelectNode } from '../interaction/nodeJump'
import { serverNodeKey } from '../interaction/treeNodes'
import { rackFaceLabel } from '../geometry/rackFaceLabels'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { RackStackedServerSection } from './RackStackedServerSection'

/**
 * One U-slot of a rack.
 *
 * A slot's face panel can be removed, and that is not decoration: an opening on
 * one face is where air enters or leaves, and the SAME slot open on both faces
 * is a through-channel. A run of adjacent through-slots forms a leakage zone
 * with its own loss coefficient — which is exactly how `case_02_leakage` is
 * built, so without this panel that case cannot be authored at all.
 *
 * Ported from `renderRackSlotInspector` (STL_generate.html:9508). Two states
 * the legacy is careful about and this keeps:
 *
 * - **Occupied slots are refused.** The panel is what a server mounts against;
 *   removing it under a server would leave the server floating in a hole.
 * - **Half-open is called out.** One face open and the other closed is a valid
 *   but easy-to-miss state, so it offers the one-click fix rather than leaving
 *   the user to find the opposite face.
 */

export interface RackSlotInspectorProps {
  model: Construction
  index: number
  side: RackSide
  /** Every selected slot, when a shift-click made it a range. */
  indices?: number[]
}

export function RackSlotInspector({ model, index, side, indices }: RackSlotInspectorProps) {
  const { t } = useTranslation()
  const [error, setError] = useState<string | null>(null)

  const opposite: RackSide = side === 'front' ? 'back' : 'front'
  const thisOpen = isSlotDeleted(model, side, index)
  const oppositeOpen = isSlotDeleted(model, opposite, index)
  const through = isThroughSlot(model, index)
  const equipment = equipmentAtSlot(model, index)
  const zone = through ? throughZoneAt(model, index) : null
  const slotCount = Number((model.geometry as { slot?: number } | undefined)?.slot ?? 42)
  const dividers = slotPartitionIndices(index, slotCount)

  function toggle(target: RackSide, open: boolean) {
    setError(null)
    const result = setSlotDeleted(model.uid, target, index, open)
    if (result === 'occupied') setError(t('error.slot_has_equipment_delete'))
  }

  // A range is a different job from a single slot: merging it into one server
  // is the whole point of selecting several.
  if (indices && indices.length > 1) {
    return <MultiSlotPanel model={model} side={side} indices={indices} />
  }

  return (
    <div className="content" id="rack-slot-inspector">
      <div className="prop-group" style={{ borderLeftColor: '#4daafc' }}>
        <div className="prop-title" style={{ color: '#4daafc' }}>
          U-slot #{index + 1}
        </div>
        {/* The side by its airflow role — the outlet for `back` — which is
            what the exported blanking panel and server solids are called.
            `side` itself stays the data key everything below writes with. */}
        <div className="prop-row">
          <label>{t('label.rack_side')}</label>
          <span className="value">{rackFaceLabel(side)}</span>
        </div>
        <div className="prop-row">
          <label>{t('label.slot_status')}</label>
          <span className="value" id="slot-status">
            {equipment
              ? t('label.slot_occupied')
              : through
                ? t('label.slot_through')
                : thisOpen
                  ? t('label.slot_open')
                  : t('label.slot_closed')}
          </span>
        </div>
        {equipment ? (
          <>
            <div className="prop-hint">{t('hint.multi_slot_has_equipment')}</div>
            {/* One U can be taken out of the server it belongs to: the end of a
                4U server is trimmed by selecting that slot alone, and a middle
                one splits it. The context behind this button computes the same
                four modes whatever the selection's length — this panel just
                never offered the route (STL_generate.html:9602). */}
            <RemoveFromEquipmentButton
              model={model}
              side={side}
              indices={[index]}
              id="slot-remove-from-equipment"
              labelKey="btn.remove_slot_from_equipment"
            />
          </>
        ) : (
          <>
            <div className="prop-row">
              <label htmlFor="slot-open-this">{rackFaceLabel(side)}</label>
              <input
                id="slot-open-this"
                type="checkbox"
                checked={thisOpen}
                onChange={(e) => toggle(side, e.target.checked)}
              />
            </div>
            <div className="prop-row">
              <label htmlFor="slot-open-opposite">{rackFaceLabel(opposite)}</label>
              <input
                id="slot-open-opposite"
                type="checkbox"
                checked={oppositeOpen}
                onChange={(e) => toggle(opposite, e.target.checked)}
              />
            </div>
          </>
        )}
        {error && <div className="error">{error}</div>}
      </div>

      {/* Filling the slot. Without this a rack can only ever be empty, and an
          empty rack is not a heat load. */}
      {!equipment && (
        <div className="prop-group" style={{ borderLeftColor: '#2a7a5a' }}>
          <div className="prop-title" style={{ color: '#2a7a5a' }}>
            {t('inspector.equipment_settings')}
          </div>
          {(['fanServer', 'nonFan', 'liquidServer'] as const).map((kind) => (
            <button
              key={kind}
              className="btn"
              id={`slot-create-${kind}`}
              onClick={() => {
                const result = createEquipmentAtSlot(model.uid, index, kind)
                setError(
                  result === 'blocked'
                    ? t('error.span_blocked')
                    : result === 'occupied'
                      ? t('error.slot_occupied')
                      : result === 'depth-range'
                        ? t('error.server_depth_range')
                        : null,
                )
              }}
            >
              ＋ {EQUIPMENT_PRESETS[kind].defaultName}
            </button>
          ))}
        </div>
      )}
      {equipment?.id && (
        <div className="prop-group" style={{ borderLeftColor: '#5a3a3a' }}>
          {/* The slot names the server but the server's own panel is a
              different tree node, and a user who clicked the slot has no reason
              to know that. */}
          <button
            className="btn frozen-ok"
            id="slot-open-equipment"
            onClick={() => requestSelectNode(serverNodeKey(model.uid, equipment.id as string))}
          >
            {t('btn.open_equipment_settings')}
          </button>
          <button
            className="btn danger"
            id="slot-remove-equipment"
            onClick={() => removeEquipment(model.uid, equipment.id as string)}
          >
            {t('btn.delete_equipment')}
          </button>
        </div>
      )}

      {/* An occupied slot is no longer a dead end for adding (spec §7.1): the
          stacked section lists what is at this U and places another server
          behind or in front of it, at a depth nothing here occupies. */}
      <RackStackedServerSection model={model} indices={[index]} />

      {/* One open face is an opening; the SAME slot open on both is a
          through-channel, which is a different boundary entirely. Offered only
          while the slot is half open, as the legacy does. */}
      {thisOpen && !through && !equipment && (
        <div className="prop-group" style={{ borderLeftColor: '#6b3fa0' }}>
          <button
            className="btn"
            id="slot-sync-delete"
            style={{ background: '#6b3fa0', color: '#fff' }}
            onClick={() => syncDeleteOpposite(model.uid, side, [index])}
          >
            {t('btn.sync_delete_opposite')}
          </button>
        </div>
      )}

      {/* Dividers. A partition between two slots closes the channel that runs
          up the inside of the rack, so this is where a cavity is split from or
          merged with its neighbour. */}
      {(dividers.top !== null || dividers.bottom !== null) && (
        <div className="prop-group" id="slot-partitions">
          <div className="prop-title">Slot Partitions</div>
          {dividers.top !== null &&
            (partitionAt(model, dividers.top)?.deleted === false ? (
              <button
                className="btn danger"
                id="slot-remove-top-partition"
                style={{ width: '100%', marginBottom: 4 }}
                onClick={() => setRackPartitionDeleted(model.uid, dividers.top as number, true)}
              >
                {t('rack.remove_top_partition')}
              </button>
            ) : (
              <button
                className="btn"
                id="slot-add-top-partition"
                style={{ width: '100%', marginBottom: 4 }}
                onClick={() => setRackPartitionDeleted(model.uid, dividers.top as number, false)}
              >
                {t('rack.add_top_partition')}
              </button>
            ))}
          {dividers.bottom !== null &&
            (partitionAt(model, dividers.bottom)?.deleted === false ? (
              <button
                className="btn danger"
                id="slot-remove-bottom-partition"
                style={{ width: '100%' }}
                onClick={() => setRackPartitionDeleted(model.uid, dividers.bottom as number, true)}
              >
                {t('rack.remove_bottom_partition')}
              </button>
            ) : (
              <button
                className="btn"
                id="slot-add-bottom-partition"
                style={{ width: '100%' }}
                onClick={() => setRackPartitionDeleted(model.uid, dividers.bottom as number, false)}
              >
                {t('rack.add_bottom_partition')}
              </button>
            ))}
        </div>
      )}

      {/* Half-open: valid, but usually not what was meant.

          Shown on the face that is CLOSED while the other is still open —
          `status.kind === 'wall' && isRackSlotDeleted(oppSide)` in the legacy
          (STL_generate.html:9527). Keyed the other way round it appeared beside
          the sync-delete button on the SAME slot, offering to close a face that
          was already closed, so the button did nothing. Both placeholders are
          filled too: `hint.residual_opposite` names both faces and
          `btn.restore_opposite` names the one it closes. */}
      {!thisOpen && oppositeOpen && !equipment && (
        <div className="prop-group" style={{ borderLeftColor: '#e5a020' }}>
          <div className="prop-title" style={{ color: '#e5a020' }}>
            {t('inspector.residual_opposite')}
          </div>
          <div className="prop-hint">
            {t('hint.residual_opposite', {
              side: rackFaceLabel(side),
              oppSide: rackFaceLabel(opposite),
            })}
          </div>
          <button
            className="btn"
            id="slot-restore-opposite"
            onClick={() => restoreOpposite(model.uid, side, index)}
          >
            {t('btn.restore_opposite', { side: rackFaceLabel(opposite) })}
          </button>
        </div>
      )}

      {zone && (
        <div className="prop-group" style={{ borderLeftColor: '#e5a020' }}>
          <div className="prop-title" style={{ color: '#e5a020' }}>
            {t('inspector.leakage_zone')}
          </div>
          <div className="prop-hint">{t('hint.leakage_zone_desc')}</div>
          <div className="prop-row">
            <label htmlFor="slot-sigma">{t('label.leakage_sigma')}</label>
            <NumberField
              id="slot-sigma"
              value={Number(zone.sigma ?? 0.05)}
              step={0.01}
              min={0}
              max={1}
              onCommit={(v) => setThroughZoneSigma(model.uid, zone.startUnit, v)}
            />
          </div>
          <div className="prop-hint">
            {t('hint.leakage_group_range')}: U{zone.startUnit + 1} – U{zone.endUnit + 1}
          </div>
          <button
            className="btn danger"
            id="slot-dissolve-zone"
            onClick={() => dissolveThroughZone(model.uid, zone.startUnit, zone.endUnit)}
          >
            {t('btn.dissolve_leakage')}
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * Several slots at once: merge them into one server, extend the one there, or
 * take a range back out of it.
 *
 * Which of those is offered comes from the overlap between the selection and
 * the equipment — `selectionContext` decides, and the labels follow it so a
 * button that would be refused is not shown at all. A split is confirmed
 * because it turns one server into two.
 */
/**
 * ➖ take these U-slots out of the server they belong to.
 *
 * The legacy renders this on BOTH inspectors — `btn.remove_slot_from_equipment`
 * on the single-slot one (STL_generate.html:9602) and the batch label on the
 * range one — because `getRackEquipmentSelectionContext` computes the same four
 * modes for a selection of any length. One component, so the confirm and the
 * error message cannot drift between the two.
 *
 * A `split` is the destructive one: the selection is in the MIDDLE of a server,
 * so removing it mints a second server out of the tail. That is the only mode
 * that asks first.
 */
function RemoveFromEquipmentButton({
  model,
  side,
  indices,
  id,
  labelKey,
}: {
  model: Construction
  side: RackSide
  indices: number[]
  id: string
  labelKey: TranslationKey
}) {
  const { t } = useTranslation()
  const [error, setError] = useState<string | null>(null)

  const list = equipmentList(model)
  const at = (i: number) => list.find((e) => i >= e.startUnit && i <= equipmentEndUnit(e)) ?? null
  const summary = summarise(side, indices, at, (s, i) => isSlotDeleted(model, s, i))
  const context = selectionContext(summary, at)
  if (!context?.canRemoveFromEquipment) return null

  function remove() {
    setError(null)
    if (context?.removalMode === 'split') {
      const name = context.singleEquipment?.name ?? context.singleEquipment?.id ?? ''
      if (!window.confirm(t('confirm.split_equipment', { name }))) return
    }
    const { ok } = removeSlotsFromEquipment(model.uid, side, indices)
    if (!ok) setError(t('error.selection_not_removable'))
  }

  return (
    <>
      <button
        className="btn"
        id={id}
        style={{ background: '#8e5a1f', color: '#fff' }}
        onClick={remove}
      >
        {t(labelKey)}
      </button>
      {error && <div className="error">{error}</div>}
    </>
  )
}

function MultiSlotPanel({
  model,
  side,
  indices,
}: {
  model: Construction
  side: RackSide
  indices: number[]
}) {
  const { t } = useTranslation()
  const [error, setError] = useState<string | null>(null)

  const list = equipmentList(model)
  const at = (i: number) => list.find((e) => i >= e.startUnit && i <= equipmentEndUnit(e)) ?? null
  const summary = summarise(side, indices, at, (s, i) => isSlotDeleted(model, s, i))
  const context = selectionContext(summary, at)
  if (!summary) return null

  const extending = context?.canExtendEquipment === true
  const kindOfExisting = context?.singleEquipment?.kind
  const opposite: RackSide = side === 'front' ? 'back' : 'front'
  const slotCount = Number((model.geometry as { slot?: number } | undefined)?.slot ?? 42)

  // A slot open on BOTH faces is a through-channel, and a run of them is a
  // leakage zone with one loss coefficient. Reading which zones the selection
  // covers is the only way to edit one across a range: the single-slot panel is
  // unreachable once more than one slot is selected.
  const throughSlots = summary.slotIndices.filter(
    (i) => isSlotDeleted(model, side, i) && isSlotDeleted(model, opposite, i),
  )
  const groups = throughSlots.length
    ? throughZoneGroups(model).filter((g) =>
        throughSlots.some((i) => i >= g.startUnit && i <= g.endUnit),
      )
    : []
  const singleGroup = groups.length === 1 ? groups[0] : null
  const canSyncDelete = syncDeleteTargets(model, side, summary.slotIndices).length > 0

  const runs =
    summary.contiguous && summary.anyDeleted
      ? voidRuns(summary.slotIndices, (i) => isSlotDeleted(model, side, i))
      : []
  const runDividers = runs
    .map((run) => ({ run, ...voidRunPartitions(run, slotCount) }))
    .filter((entry) => entry.top !== null || entry.bottom !== null)

  function merge(kind: EquipmentKind) {
    setError(null)
    const result = mergeSlotsIntoEquipment(model.uid, side, indices, kind)
    setError(
      result === 'not-contiguous'
        ? t('error.selection_not_contiguous')
        : result === 'wrong-type'
          ? t('error.selection_has_equipment_wrong_type')
          : result === 'occupied'
            ? t('error.selection_has_equipment_no_merge')
            : result === 'blocked'
              ? t('error.span_blocked')
              : result === 'depth-range'
                ? t('error.server_depth_range')
                : null,
    )
  }

  function batchDelete(deleted: boolean) {
    setError(null)
    const result = setRackSelectionDeleted(model.uid, side, indices, deleted)
    // Refused outright rather than skipping the occupied slots: a partial apply
    // would leave the range half open with nothing on screen saying so.
    if (result === 'occupied') setError(t('error.selection_has_equipment_delete'))
  }

  return (
    <div className="content" id="rack-multislot-inspector">
      <div className="prop-group" style={{ borderLeftColor: '#4daafc' }}>
        <div className="prop-title" style={{ color: '#4daafc' }}>
          U-slot #{summary.startUnit + 1} – #{summary.endUnit + 1} ({indices.length}U)
        </div>
        <div className="prop-hint">
          {!summary.contiguous
            ? t('hint.multi_slot_not_contiguous')
            : context?.canRemoveFromEquipment
              ? context.removalMode === 'split'
                ? t('hint.multi_slot_can_split')
                : t('hint.multi_slot_can_remove')
              : extending
                ? t('hint.multi_slot_can_extend')
                : summary.hasEquipment
                  ? t('hint.multi_slot_has_equipment')
                  : t('hint.multi_slot_can_merge')}
        </div>
      </div>

      {throughSlots.length > 0 && (
        <div className="prop-group" id="multislot-leakage" style={{ borderLeftColor: '#e5a020' }}>
          <div className="prop-title" style={{ color: '#e5a020' }}>
            {t('inspector.leakage_zone')}
          </div>
          <div className="prop-hint">
            {t('hint.multi_leakage_summary', {
              count: throughSlots.length,
              groups: groups.length,
            })}
          </div>
          {singleGroup ? (
            <>
              <div className="prop-row">
                <label htmlFor="multislot-sigma">{t('label.leakage_sigma')}</label>
                <NumberField
                  id="multislot-sigma"
                  value={Number(singleGroup.sigma ?? 0.05)}
                  step={0.01}
                  min={0}
                  max={1}
                  onCommit={(v) => setThroughZoneSigma(model.uid, singleGroup.startUnit, v)}
                />
              </div>
              <button
                className="btn danger"
                id="multislot-dissolve-zone"
                onClick={() =>
                  dissolveThroughZone(model.uid, singleGroup.startUnit, singleGroup.endUnit)
                }
              >
                {t('btn.dissolve_leakage')}
              </button>
            </>
          ) : (
            /* Two zones share no single sigma, so editing one of them from here
               would silently pick a zone. The legacy sends the user back to the
               zone itself for the same reason. */
            <div className="prop-hint">{t('hint.multi_leakage_multi_group')}</div>
          )}
        </div>
      )}

      {runDividers.length > 0 && (
        <div
          className="prop-group"
          id="multislot-void-partitions"
          style={{ borderLeftColor: 'var(--section-header-create)' }}
        >
          <div className="prop-title" style={{ color: 'var(--section-header-create)' }}>
            {t('inspector.void_partition')}
          </div>
          <div className="prop-hint">{t('hint.void_partition_desc')}</div>
          {runDividers.map(({ run, top, bottom }) => (
            <div key={`${run.start}-${run.end}`}>
              {top !== null && (
                <label className="void-partition-row" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 0', cursor: 'pointer' }}>
                  <input
                    id={`void-partition-top-${top}`}
                    type="checkbox"
                    style={{ width: 'auto', margin: 0 }}
                    checked={partitionAt(model, top)?.deleted === false}
                    onChange={(e) => setRackPartitionDeleted(model.uid, top, !e.target.checked)}
                  />
                  {t('hint.multi_slot_top_partition', { slot: run.end + 1 })}
                </label>
              )}
              {bottom !== null && (
                <label className="void-partition-row" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 0', cursor: 'pointer' }}>
                  <input
                    id={`void-partition-bottom-${bottom}`}
                    type="checkbox"
                    style={{ width: 'auto', margin: 0 }}
                    checked={partitionAt(model, bottom)?.deleted === false}
                    onChange={(e) => setRackPartitionDeleted(model.uid, bottom, !e.target.checked)}
                  />
                  {t('hint.multi_slot_bottom_partition', { slot: run.start + 1 })}
                </label>
              )}
            </div>
          ))}
        </div>
      )}

      {summary.contiguous && (!summary.hasEquipment || extending) && (
        <div className="prop-group" style={{ borderLeftColor: '#2a7a5a' }}>
          <div className="prop-title" style={{ color: '#2a7a5a' }}>
            {t('inspector.equipment_settings')}
          </div>
          {(['fanServer', 'nonFan', 'liquidServer'] as const)
            // Extending must keep the KIND: turning a fan-server into a non-fan
            // by lengthening it would change a flow boundary into a solid.
            .filter((kind) => !extending || kind === kindOfExisting)
            .map((kind) => (
              <button
                key={kind}
                className="btn"
                id={`multislot-${extending ? 'extend' : 'merge'}-${kind}`}
                onClick={() => merge(kind)}
              >
                {extending ? '⇕' : '⊕'} {EQUIPMENT_PRESETS[kind].defaultName} ({indices.length}U)
              </button>
            ))}
        </div>
      )}

      {/* The range holds a server: the stacked section lists the depth boxes at
          these U's and adds another one where nothing intersects (spec §7.1).
          It renders nothing when no server actually covers the range. */}
      {summary.hasEquipment && (
        <RackStackedServerSection model={model} indices={summary.slotIndices} />
      )}

      <div className="prop-group" id="multislot-batch-actions">
        <div className="prop-title">Batch Actions</div>
        <button
          className="btn"
          id="multislot-delete-surface"
          disabled={summary.hasEquipment}
          onClick={() => batchDelete(true)}
        >
          {t('btn.batch_delete_surface')}
        </button>
        <button
          className="btn"
          id="multislot-restore-surface"
          disabled={!summary.anyDeleted}
          onClick={() => batchDelete(false)}
        >
          {t('btn.batch_restore_surface')}
        </button>
        {/* Only offered while some slot in the range is open on this face and
            still closed on the other: with nothing half open there is no
            through-channel to form. */}
        {canSyncDelete && (
          <button
            className="btn"
            id="multislot-sync-delete"
            style={{ background: '#6b3fa0', color: '#fff' }}
            onClick={() => syncDeleteOpposite(model.uid, side, summary.slotIndices)}
          >
            {t('btn.batch_sync_delete')}
          </button>
        )}
        <RemoveFromEquipmentButton
          model={model}
          side={side}
          indices={indices}
          id="multislot-remove"
          labelKey="btn.batch_remove_from_equipment"
        />
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  )
}
