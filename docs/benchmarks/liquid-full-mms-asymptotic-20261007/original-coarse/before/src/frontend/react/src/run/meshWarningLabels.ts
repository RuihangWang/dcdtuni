import { t, type TranslationKey } from '../i18n'
import type { MeshWarning } from './meshWarnings'

const LABELS: Record<string, TranslationKey> = {
  bad_face_tets: 'mesh_warn.finding.bad_face_tets',
  concave_cells: 'mesh_warn.finding.concave_cells',
  high_non_orthogonality: 'mesh_warn.finding.high_non_orthogonality',
  low_interpolation_weight: 'mesh_warn.finding.low_interpolation_weight',
  low_volume_ratio: 'mesh_warn.finding.low_volume_ratio',
}
const DETAILS: Record<string, TranslationKey> = {
  bad_face_tets: 'mesh_warn.detail.bad_face_tets',
  concave_cells: 'mesh_warn.detail.concave_cells',
  high_non_orthogonality: 'mesh_warn.detail.high_non_orthogonality',
  low_interpolation_weight: 'mesh_warn.detail.low_interpolation_weight',
  low_volume_ratio: 'mesh_warn.detail.low_volume_ratio',
}
export function meshWarningLabel(warning: MeshWarning): string {
  const key = warning.code ? LABELS[warning.code] : undefined
  return key ? t(key) : warning.code ?? ''
}
export function meshWarningMessage(warning: MeshWarning): string {
  const code = warning.code ?? ''
  const key = DETAILS[code]
  if (!key) return warning.message ?? ''
  const number = code === 'high_non_orthogonality' ? warning.max_value : warning.count
  if (typeof number !== 'number' || !Number.isFinite(number)) return warning.message ?? ''
  if (code.startsWith('low_') && (typeof warning.threshold !== 'number' || !Number.isFinite(warning.threshold))) {
    return warning.message ?? ''
  }
  return t(key, {
    count: number, max: number.toFixed(1), threshold: warning.threshold ?? '',
  })
}
