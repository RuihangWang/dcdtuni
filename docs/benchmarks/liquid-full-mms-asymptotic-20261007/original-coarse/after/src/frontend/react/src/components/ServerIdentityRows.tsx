import type { Construction } from '../schema/cfdproj'
import { serverSlotRange } from '../geometry/serverExportNames'
import { useTranslation } from '../useTranslation'

/**
 * Which server a surface belongs to, and where in the rack it sits.
 *
 * Shown on every panel a server is reached through by one of its surfaces: the
 * face panel and the patch panel. From a face or a patch nothing else on screen
 * says which U-slots the surface belongs to, and the patch panel said nothing
 * about the server at all. One component, so the two panels cannot drift apart.
 *
 * `#sf-range` is the id the legacy face panel gives the same range
 * (`renderServerFaceInspector`), so one selector reads it in both editors. The
 * equipment panel keeps its own `#eq-range`.
 */
export function ServerIdentityRows({
  model,
  equipmentId,
}: {
  model: Construction
  equipmentId: string
}) {
  const { t } = useTranslation()
  const servers = ((model.constructions as { servers?: Record<string, Record<string, unknown>> })
    ?.servers ?? {}) as Record<string, Record<string, unknown>>
  const meta = (servers[equipmentId]?.meta ?? {}) as { name?: string }
  return (
    <div className="prop-group">
      <div className="prop-row">
        <label>{t('label.equipment_name')}</label>
        <span className="value">{meta.name ?? equipmentId}</span>
      </div>
      <div className="prop-row">
        <label>{t('label.equipment_range')}</label>
        <span className="value" id="sf-range">
          {serverSlotRange(model, equipmentId) || '—'}
        </span>
      </div>
    </div>
  )
}
