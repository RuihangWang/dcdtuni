/**
 * How many cores a decomposition should get.
 *
 * The number that matters for OpenFOAM is the PHYSICAL core count, not the
 * logical one `/api/server-info` reports: two hyperthreads share one core's
 * arithmetic units, so a 48-way decomposition on 24 physical cores runs slower
 * than a 24-way one while using twice the memory. The legacy panel warns above
 * half the logical count (STL_generate.html:25776) and this is that rule.
 *
 * The host these are run on reports 48 logical cores, so the React panel's old
 * `procs > cpuCount` test could not fire below 49 — twice the threshold that
 * matters.
 */

export interface CoreWarning {
  n: number
  physical: number
  total: number
  recommended: number
}

/** Half the logical count, which is what the legacy calls physical. */
export function physicalCores(cpuCount: number | undefined): number {
  return Math.floor((cpuCount ?? 0) / 2)
}

/**
 * The `status.cores_warning` substitutions, or null when the count is fine.
 *
 * Null when the server never reported a CPU count: a warning computed from
 * zero would fire on every project.
 */
export function coreWarning(procs: number, cpuCount: number | undefined): CoreWarning | null {
  const total = cpuCount ?? 0
  if (total <= 0) return null
  const physical = physicalCores(total)
  if (!(procs > physical)) return null
  return { n: procs, physical, total, recommended: physical }
}
