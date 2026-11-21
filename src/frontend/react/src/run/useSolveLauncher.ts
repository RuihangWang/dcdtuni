import { useRef, useState } from 'react'
import * as store from '../projectStore'
import * as session from './session'
import { openSolverMonitor, shouldOpenMonitor } from './solverMonitor'
import { solverConfigOf } from './solverConfig'
import { confirmSolvePartition } from './solvePartition'
import { t } from '../i18n'

/**
 * Starting a solve — the whole gesture, wherever the button is.
 *
 * There are two buttons: 求解 on the solver tab and ▶ 执行瞬态求解 on the
 * transient one. Both confirm the partition layout, open the live monitor
 * window with its probe poller, and submit — one implementation.
 *
 * Mesh quality does not stop a solve here: it is reported, never required
 * (owner 2026-10-22), and it is read on the mesh tab, the only place that
 * checks the mesh (owner 2026-11-10). What can stop a solve is a mesh that no
 * longer matches the geometry, and the buttons gate on that (`meshBlockNow`).
 */

export interface SolveLauncher {
  /** Press the button: confirm the partition, open the monitor, submit. */
  run(): void
  /** Whether a submit is in flight, for the button's disabled state. */
  preparing: boolean
  error: string | null
}

/** The probes a run should watch: the enabled ones, by name. */
export function monitoredProbeNames(): string[] {
  return store
    .sensorProbes(store.getProject())
    .filter((p) => p.meta.enabled !== false)
    .map((p) => p.meta.name)
}

/**
 * Confirm the partition layout, then open the monitor and submit.
 *
 * The monitor is armed BEFORE the submit: the server takes a while to answer on
 * a large case, and a plot that only appears after it does looks like nothing
 * happened.
 */
let submittingSolve = false

export async function submitSolve(): Promise<void> {
  // Both tabs share this lock, including the asynchronous confirmation period.
  if (submittingSolve) return
  submittingSolve = true
  try {
    if (!await confirmSolvePartition(() => solverConfigOf(store.getProject()))) {
      store.logAction('runSolveOnly', t('status.solve_partition_cancelled'))
      return
    }
    const probes = monitoredProbeNames()
    if (shouldOpenMonitor(probes.length)) openSolverMonitor(probes)
    store.logOperation('solve', `${t('btn.run_solve')} · ${probes.length} probes`)
    await session.runJob('solve')
  } finally {
    submittingSolve = false
  }
}

export function useSolveLauncher(): SolveLauncher {
  const trigger = useRef<HTMLElement | null>(null)
  const [preparing, setPreparing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (preparing) return
    const focusTarget = trigger.current
    setError(null)
    setPreparing(true)
    try {
      await submitSolve()
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      store.logOperation('solve', message)
    } finally {
      setPreparing(false)
      // React disables the run button during preflight. Restore focus after
      // that render is committed, rather than while the dialog closes.
      if (focusTarget) requestAnimationFrame(() => {
        if (focusTarget.isConnected && !focusTarget.matches(':disabled')
          && !document.getElementById('solve-partition-warning-dialog')) focusTarget.focus()
      })
    }
  }

  return {
    preparing,
    error,
    run() {
      trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      void submit()
    },
  }
}
