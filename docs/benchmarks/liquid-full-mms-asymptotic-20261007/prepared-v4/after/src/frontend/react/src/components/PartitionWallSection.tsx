import { useState } from 'react'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { fromDisplay, stepFor, toDisplay, unitOf } from '../lengthUnits'
import {
  partitionPatches,
  setPartitionPatchLeakage,
  updatePartitionWallSize,
} from '../geometry/partitionWall'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'

/**
 * The partition wall's own size, and the leakage of each hole through it.
 *
 * The size boxes REPLACE the generic X/Y/Z ones because a resize has to re-fit
 * the holes: `syncPartitionWallModelState` clamps them into the new face and
 * refuses an overlap, and the generic boxes skip it, leaving holes hanging off
 * the edge of the wall they are cut from.
 *
 * The leakage rows are the legacy's per-patch `leakageRate`
 * (STL_generate.html:10821). The legacy puts them on the PATCH inspector; they
 * are listed here as well as being per-patch because a containment divider is
 * usually a handful of holes and reading them one selection at a time hides the
 * one that was left at 100%.
 */

export interface PartitionWallSectionProps {
  model: Construction
  project: CfdProject | null
}

export function PartitionWallSection({ model, project }: PartitionWallSectionProps) {
  const { t } = useTranslation()
  const [error, setError] = useState<string | null>(null)
  if (model.kind !== 'partitionWall') return null

  const unit = unitOf(project)
  const step = stepFor(unit)
  const size = (model.geometry?.size ?? {}) as Record<string, number>
  const patches = partitionPatches(model)

  function resize(key: 'W' | 'H' | 'D', metres: number) {
    const result = updatePartitionWallSize(model.uid, key, metres)
    setError(result.ok ? null : result.error)
  }

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
        <div className="prop-title" style={{ color: '#16825d' }}>
          {t('inspector.partition_wall_settings')}
        </div>
        <div className="prop-hint">{t('hint.partition_wall_role')}</div>
        <div className="prop-row">
          <label htmlFor="partition-wall-w">{`${t('label.width_w')} (${unit})`}</label>
          <NumberField
            id="partition-wall-w"
            value={toDisplay(Number(size.x ?? 0), unit)}
            step={step}
            decimals={4}
            min={0}
            onCommit={(v) => resize('W', fromDisplay(v, unit))}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="partition-wall-d">{`${t('label.thickness_d')} (${unit})`}</label>
          <NumberField
            id="partition-wall-d"
            value={toDisplay(Number(size.z ?? 0), unit)}
            step={step}
            decimals={4}
            min={0}
            onCommit={(v) => resize('D', fromDisplay(v, unit))}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="partition-wall-h">{`${t('label.height_h')} (${unit})`}</label>
          <NumberField
            id="partition-wall-h"
            value={toDisplay(Number(size.y ?? 0), unit)}
            step={step}
            decimals={4}
            min={0}
            onCommit={(v) => resize('H', fromDisplay(v, unit))}
          />
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
        <div className="prop-title" style={{ color: '#16825d' }}>
          {t('inspector.partition_wall_cfd')}
        </div>
        {patches.map((patch) => (
          <div className="prop-row" key={patch.id}>
            <label htmlFor={`partition-leakage-${patch.id}`}>
              {patch.name ?? patch.id} — {t('label.leakage_rate')}
            </label>
            <NumberField
              id={`partition-leakage-${patch.id}`}
              value={patch.leakageRate}
              step={1}
              min={0}
              max={100}
              onCommit={(v) => setPartitionPatchLeakage(model.uid, patch.id, v)}
            />
            <span>%</span>
          </div>
        ))}
        <div className="prop-hint">{t('hint.partition_wall_leakage')}</div>
      </div>
    </>
  )
}
