import { t } from '../i18n'
import type { SolverConfig } from './solverConfig'
import { showSolvePartitionDialog } from './meshPreflightUi'

type ParallelConfig = Pick<SolverConfig, 'parallelSolve' | 'solveProcess'>

/** Check the mesh on disk before export or opening the solver monitor. */
export async function confirmSolvePartition(getConfig: () => ParallelConfig): Promise<boolean> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 10000)
  let mesh: { meshReady?: boolean; decomposed?: boolean; nPartitions?: number }
  try {
    const response = await fetch('/api/check-mesh', { cache: 'no-store', signal: controller.signal })
    if (!response.ok) throw new Error('mesh status unavailable')
    mesh = await response.json()
    if (!mesh || typeof mesh.meshReady !== 'boolean') throw new Error('invalid mesh status')
  } catch {
    throw new Error(t('error.solve_partition_check_failed'))
  } finally {
    window.clearTimeout(timer)
  }
  if (!mesh.meshReady) throw new Error(t('error.mesh_first'))
  if (typeof mesh.decomposed !== 'boolean') throw new Error(t('error.solve_partition_check_failed'))
  if (!mesh.decomposed) return true
  const partitions = mesh.nPartitions
  if (typeof partitions !== 'number' || !Number.isInteger(partitions) || partitions < 1) {
    throw new Error(t('error.solve_partition_check_failed'))
  }

  // Settings may have changed while the filesystem probe was in flight.
  const config = getConfig()
  const configured = Number(config.solveProcess)
  const ranks = config.parallelSolve && Number.isFinite(configured)
    ? Math.max(1, Math.trunc(configured)) : 1
  if (ranks > 1 && ranks === partitions) return true
  return showSolvePartitionDialog(partitions, ranks)
}
