import { useRef, useState } from 'react'
import * as store from '../projectStore'
import * as session from './session'
import { blockingMeshWarnings, getMeshWarnings, hasRejectedMeshQuality, type MeshWarning } from './meshWarnings'
import { openSolverMonitor, shouldOpenMonitor } from './solverMonitor'
import { solverConfigOf } from './solverConfig'
import { confirmSolvePartition } from './solvePartition'
import { t } from '../i18n'

/**
 * Starting a solve — the whole gesture, wherever the button is.
 *
 * There are two buttons: 求解 on the solver tab and ▶ 执行瞬态求解 on the
 * transient one. In the legacy they are the SAME function —
 * `runTransientSolve = function() { window.runSolveOnly(); }`
 * (STL_generate.html:26661) — so the transient run gets the mesh-quality
 * confirmation, the live monitor window and the probe poller for free.
 *
 * React's transient button called `session.runJob('solve')` directly and got
 * none of them. The two that matter:
 *
 * - **The mesh-quality gate.** `checkMesh` flagged something at level 1 or 2
 *   and a transient run is hours. The solver tab stops for a confirmation; the
 *   transient tab launched straight into it, so the case most likely to waste a
 *   night was the one with no brakes.
 * - **The monitor window and its probe poller.** Without them a transient solve
 *   runs with no live curve at all — and being able to watch a transient
 *   develop is most of why it is being run.
 *
 * So the logic lives here and both buttons use it. The DIALOG stays in each
 * panel, because it is the panel's own render — but the decision of whether to
 * raise it, and everything that happens after, is one implementation.
 */

export interface SolveLauncher {
  /**
   * Press the button. Raises the gate if `checkMesh` blocked, else submits.
   */
  run(): void
  /** The warnings held back for confirmation. Non-null means the gate is up. */
  gate: readonly MeshWarning[] | null
  gateRejected: boolean
  /** 取消 — put the gate away and do nothing. */
  dismiss(): void
  /** 继续 — put the gate away and solve anyway. */
  proceed(): void
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
    if (hasRejectedMeshQuality()) throw new Error(t('mesh_warn.desc_rejected'))
    if (!await confirmSolvePartition(() => solverConfigOf(store.getProject()))) {
      store.logAction('runSolveOnly', t('status.solve_partition_cancelled'))
      return
    }
    if (hasRejectedMeshQuality()) throw new Error(t('mesh_warn.desc_rejected'))
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
  const [gate, setGate] = useState<readonly MeshWarning[] | null>(null)
  const [gateRejected, setGateRejected] = useState(false)
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
    gate,
    gateRejected,
    preparing,
    error,
    run() {
      trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      // Level 3 passes straight through — a second large mesh region is what a
      // containment case looks like, not a defect.
      const rejected = hasRejectedMeshQuality()
      if (rejected || blockingMeshWarnings().length > 0) {
        setGateRejected(rejected)
        setGate(getMeshWarnings())
        return
      }
      void submit()
    },
    dismiss: () => setGate(null),
    proceed() {
      if (gateRejected || hasRejectedMeshQuality()) return
      setGate(null)
      void submit()
    },
  }
}
