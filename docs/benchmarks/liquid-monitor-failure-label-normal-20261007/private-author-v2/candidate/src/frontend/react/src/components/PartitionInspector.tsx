import type { Construction } from '../schema/cfdproj'
import { rackState } from '../legacy/cfdprojModules'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'

/**
 * A rack partition: the horizontal plate between two U-slots.
 *
 * Partitions are what make a rack's slots separate air paths. Deleting one
 * MERGES the two slots either side into a single through-channel — which is how
 * a 4U server gets one opening instead of four — so "deleted" is a modelling
 * decision, not a tidy-up, and it is reversible.
 *
 * The plate is not removed from the project when deleted: it keeps its index
 * and its name and is flagged, because the index is what the exporter names the
 * remaining plates by. Ported from `renderRackPartitionInspector`
 * (STL_generate.html:10389).
 */

export interface PartitionInspectorProps {
  model: Construction
  index: number
}

type Partition = { index: number; name?: string; deleted?: boolean }

function partitionsOf(model: Construction): Partition[] {
  return ((model.constructions as { partitions?: Partition[] })?.partitions ?? []) as Partition[]
}

export function PartitionInspector({ model, index }: PartitionInspectorProps) {
  const { t } = useTranslation()
  const partition = partitionsOf(model).find((p) => p.index === index)

  function set(key: 'name' | 'deleted', value: unknown) {
    store.edit('updateRackPartition', `${model.uid}#${index} ${key}`, (draft) => {
      const target = store.findConstruction(draft, model.uid)?.model
      if (!target || target.kind !== 'rack') return
      const hit = partitionsOf(target).find((p) => p.index === index)
      if (!hit) return
      if (key === 'deleted') hit.deleted = !!value
      else hit.name = String(value)
      // Re-derives the slot table: deleting a partition merges two slots, and
      // the servers' spans have to follow.
      rackState().syncRackModelState(target)
    })
  }

  if (!partition) {
    return (
      <div className="content" id="partition-inspector">
        <div className="prop-group">
          <div className="prop-title">Partition</div>
          <div className="prop-hint">#{index}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="content" id="partition-inspector">
      <div className="prop-group">
        <div className="prop-title">Partition</div>
        <div className="prop-row">
          <label>{t('label.partition_name')}</label>
          <input
            id="partition-name"
            type="text"
            defaultValue={partition.name ?? `partition_${index}`}
            key={`${model.uid}#${index}`}
            onBlur={(e) => e.target.value !== partition.name && set('name', e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
          />
        </div>
        <div className="prop-row">
          <label>{t('hint.partition_status')}</label>
          <span className="value" id="partition-status">
            {/* The legacy prints these two states in English inline
                (STL_generate.html:10399); kept as-is so a screenshot of one app
                reads the same as the other. */}
            {partition.deleted ? 'deleted' : 'active wall'}
          </span>
        </div>
        <div className="prop-hint">{t('hint.partition_deleted_status')}</div>
        <div className="prop-hint">{t('hint.partition_desc')}</div>
      </div>
      <div className="prop-group">
        <div className="prop-title">Partition Actions</div>
        <button
          className="btn"
          id="partition-delete"
          disabled={partition.deleted === true}
          onClick={() => set('deleted', true)}
        >
          {t('btn.delete_partition')}
        </button>
        <button
          className="btn"
          id="partition-restore"
          disabled={partition.deleted !== true}
          onClick={() => set('deleted', false)}
        >
          {t('btn.restore_partition')}
        </button>
      </div>
    </div>
  )
}
