import type { TranslationKey } from '../i18n'

export interface SolveAssessment {
  format: 'air-solve-assessment/v1'
  caseDir: string
  cfdrunSha256: string
  runDir: string | null
  reportSha256: string | null
  mode: 'steady' | 'transient' | null
  verdict: 'CONVERGED' | 'NOT_CONVERGED' | 'INCONCLUSIVE' | 'TRANSIENT_COMPLETED' | 'TRANSIENT_INCOMPLETE' | 'UNKNOWN'
  reasons: string[]
}

/** A final report may only establish status for the input accepted by this job. */
export function readSolveAssessment(value: unknown, caseDir?: string, sha256?: string): SolveAssessment | null {
  if (!value || typeof value !== 'object' || !caseDir || !sha256) return null
  const v = value as Record<string, unknown>
  if (v.format !== 'air-solve-assessment/v1' || v.caseDir !== caseDir ||
      v.cfdrunSha256 !== sha256 || !/^[a-f0-9]{64}$/.test(sha256) ||
      !Array.isArray(v.reasons) || !v.reasons.every(reason => typeof reason === 'string')) return null
  if (v.verdict === 'UNKNOWN' && v.mode === null && v.reportSha256 === null &&
      (v.runDir === null || typeof v.runDir === 'string')) return v as unknown as SolveAssessment
  if (typeof v.runDir !== 'string' || !v.runDir.startsWith(`${caseDir}/log/`) ||
      typeof v.reportSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(v.reportSha256)) return null
  if (v.mode === 'steady' && ['CONVERGED', 'NOT_CONVERGED', 'INCONCLUSIVE'].includes(String(v.verdict))) return v as unknown as SolveAssessment
  if (v.mode === 'transient' && ['TRANSIENT_COMPLETED', 'TRANSIENT_INCOMPLETE'].includes(String(v.verdict))) return v as unknown as SolveAssessment
  return null
}

export function solveStatus(job: { aborted: boolean; returncode: number | null; solveAssessment?: SolveAssessment | null }):
  { key: TranslationKey; outcome: string; color: string } {
  if (job.aborted) return { key: 'status.aborted', outcome: 'aborted', color: 'var(--hint-color)' }
  if (job.returncode !== 0 && job.returncode !== null) return { key: 'status.solve_failed', outcome: 'failed', color: '#ff5c72' }
  if (job.returncode === 0) {
    switch (job.solveAssessment?.verdict) {
      case 'CONVERGED': return { key: 'status.solve_converged', outcome: 'converged', color: 'var(--highlight)' }
      case 'TRANSIENT_COMPLETED': return { key: 'status.solve_transient_complete', outcome: 'transient-completed', color: 'var(--highlight)' }
      case 'NOT_CONVERGED': return { key: 'status.solve_not_converged', outcome: 'not-converged', color: '#d99b2c' }
      case 'TRANSIENT_INCOMPLETE': return { key: 'status.solve_transient_incomplete', outcome: 'transient-incomplete', color: '#d99b2c' }
    }
  }
  return { key: 'status.solve_unconfirmed', outcome: 'unconfirmed', color: '#d99b2c' }
}
