import { useEffect, useState } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import { liquidReadiness, type LiquidReadiness, type ReadinessStatus } from '../liquid/liquidReadiness'
import { useTranslation } from '../useTranslation'

/**
 * The liquid readiness checklist, live while modelling.
 *
 * Recomputed a moment after the project stops changing rather than on every
 * edit: a drag publishes a project per frame, and compiling the Shinaide
 * network on each would make the drag stutter. The first result is immediate.
 */
export function useLiquidReadiness(project: CfdProject | null, delayMs = 250): LiquidReadiness | null {
  const [report, setReport] = useState<LiquidReadiness | null>(() => (project ? liquidReadiness(project) : null))
  useEffect(() => {
    if (!project) { setReport(null); return }
    const timer = setTimeout(() => setReport(liquidReadiness(project)), delayMs)
    return () => clearTimeout(timer)
  }, [project, delayMs])
  return report
}

const MARK: Record<ReadinessStatus, string> = { ok: '✓', warn: '!', error: '✗', skip: '–' }
const COLOR: Record<ReadinessStatus, string> = { ok: '#7fd18b', warn: '#e5c07b', error: '#ef6b73', skip: '#808590' }
const DETAILS_SHOWN = 6

export function LiquidReadinessCard({ report, compact = false, id = 'liquid-readiness' }:
  { report: LiquidReadiness | null; compact?: boolean; id?: string }) {
  const { t } = useTranslation()
  if (!report) return <div className="prop-hint" id={id}>{t('lq_ux.ready_checking')}</div>
  const errors = report.items.filter(i => i.status === 'error').length
  const warns = report.items.filter(i => i.status === 'warn').length
  const summary = errors ? t('lq_ux.ready_summary_errors', { count: errors })
    : warns ? t('lq_ux.ready_summary_warns', { count: warns }) : t('lq_ux.ready_summary_ok')
  const problems = report.items.filter(i => i.status === 'error' || i.status === 'warn')
  const passed = compact ? [] : report.items.filter(i => i.status === 'ok' || i.status === 'skip')
  const full = (item: (typeof report.items)[number]) =>
    [t(item.key, item.vars), item.details.slice(0, DETAILS_SHOWN).join('；')].filter(Boolean).join('\n')
  // A passed check is a chip: the subject of its sentence ("CDU：1 台" -> "CDU：1 台"; a long one -> its subject).
  const chip = (text: string) => (text.length <= 10 ? text : text.split(/[：:]/)[0]!)
  return (
    <div id={id} className="liquid-readiness" data-ready={report.ready} data-errors={errors} data-warnings={warns}>
      <div className="liquid-readiness-summary" style={{ color: errors ? COLOR.error : warns ? COLOR.warn : COLOR.ok }}>
        {summary}
      </div>
      {passed.length > 0 && (
        <div className="liquid-readiness-chips">
          {passed.map(item => (
            <span key={item.id} className="liquid-readiness-chip" data-readiness={item.id} data-status={item.status}
              title={full(item)}>
              <span aria-hidden="true" style={{ color: COLOR[item.status] }}>{MARK[item.status]}</span>
              {chip(t(item.key, item.vars))}
            </span>
          ))}
        </div>
      )}
      {problems.length > 0 && (
        <ul className="liquid-readiness-problems">
          {problems.map(item => (
            <li key={item.id} data-readiness={item.id} data-status={item.status}>
              <span aria-hidden="true" style={{ color: COLOR[item.status], fontWeight: 700 }}>{MARK[item.status]}</span>
              <span style={{ minWidth: 0 }}>
                <span style={{ color: COLOR[item.status] }}>{t(item.key, item.vars)}</span>
                {item.details.length > 0 && (
                  <span className="liquid-readiness-details">
                    {item.details.slice(0, DETAILS_SHOWN).join('；')}
                    {item.details.length > DETAILS_SHOWN ? ` … +${item.details.length - DETAILS_SHOWN}` : ''}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
