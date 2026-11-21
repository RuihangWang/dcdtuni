import type { CfdProject } from '../schema/cfdproj'
import { stateV4 } from '../legacy/cfdprojModules'

/**
 * The solver half of `runDefaults`, typed.
 *
 * Same rule as the mesh side: defaults come from the shared V4 state factory,
 * not from a copy kept here.
 *
 * Steady and transient share this one object, and several fields belong to only
 * one of them (`endTime` is iterations for steady, `endTimeSeconds` is physical
 * seconds for transient). The panel shows the set that matches `solverType`
 * rather than all of them, which is what the legacy panel does through the
 * `#transient-params` visibility toggle.
 */

export type SolverType = 'steady' | 'transient'
/** `fast`, or `custom` once a scheme or relaxation factor is hand-edited (`detectPreset`). */
export type SolverPreset = 'fast' | 'custom'

export interface Schemes {
  divSchemeU?: string
  divSchemeT?: string
  divSchemeK?: string
  divSchemeEpsilon?: string
  laplacianScheme?: string
  snGradScheme?: string
  [key: string]: unknown
}

export interface ResidualControl {
  p_rgh?: number
  U?: number
  T?: number
  k_epsilon?: number
  [key: string]: unknown
}

export interface RelaxationFactors {
  p_rgh?: number
  U?: number
  T?: number
  k_epsilon?: number
  [key: string]: unknown
}

/** Mirrors the CPU fvSolution templates. Transient maxIter is OpenFOAM
 * v2412 lduMatrix.H::defaultMaxIter when the template omits the entry. */
export const PRESSURE_LINEAR_DEFAULTS = {
  steady: { tolerance: 1e-6, relTol: .01, maxIter: 200 },
  transient: { tolerance: 1e-8, relTol: .01, maxIter: 1000 },
} as const

/** Optional PCG pressure settings; null or absence keeps the solver defaults. */
export interface PressureLinearSolver {
  preconditioner?: 'DIC' | 'GAMG' | null
  tolerance?: number | null
  relTol?: number | null
  maxIter?: number | null
}

export interface SolverConfig {
  solverType: SolverType
  endTime: number
  writeInterval: number
  writePrecision: string
  endTimeSeconds: number
  startFrom: string
  adjustTimeStep: boolean
  deltaT: number
  maxDeltaT: number
  maxCo: number
  writeIntervalSeconds: number
  ddtScheme: string
  crankNicolsonCoeff: number
  nOuterCorrectors: number
  nCorrectors: number
  parallelSolve: boolean
  solveProcess: number
  gravityX: number
  gravityY: number
  gravityZ: number
  solverPreset: SolverPreset
  nonOrthoCorrection: boolean
  nNonOrthogonalCorrectors: number
  schemes: Schemes
  residualControl: ResidualControl
  /** Transient per-step outer-loop tolerances (E8c); separate from the steady set. */
  transientResidualControl?: ResidualControl
  /** Steady stop: every ACU return temperature flat within this (K) over the last 100 iterations. */
  stopToleranceK?: number
  relaxationFactors: RelaxationFactors
  pressureLinearSolver?: PressureLinearSolver | null
  [key: string]: unknown
}

let cached: SolverConfig | null = null

export function defaultSolverConfig(): SolverConfig {
  if (!cached) {
    const initial = stateV4().getInitialStateV4() as {
      runDefaults?: { simulationConfig?: Partial<SolverConfig> }
    }
    cached = { ...(initial.runDefaults?.simulationConfig ?? {}) } as SolverConfig
  }
  return cached
}

export function solverConfigOf(project: CfdProject | null): SolverConfig {
  const runDefaults = (project as { runDefaults?: { simulationConfig?: Partial<SolverConfig> } } | null)
    ?.runDefaults
  return { ...defaultSolverConfig(), ...(runDefaults?.simulationConfig ?? {}) }
}

/**
 * Which pressure-velocity algorithm the run will use.
 *
 * Steady is always SIMPLE. Transient is PIMPLE, except that with
 * `nOuterCorrectors === 1` PIMPLE degenerates to PISO — and PISO ignores
 * relaxation factors entirely, which is why the panel hides them there rather
 * than showing numbers that do nothing.
 */
export function solverAlgorithm(config: SolverConfig): 'SIMPLE' | 'PIMPLE' | 'PISO' {
  if (config.solverType === 'steady') return 'SIMPLE'
  return Number(config.nOuterCorrectors) <= 1 ? 'PISO' : 'PIMPLE'
}

/** Relaxation factors only matter outside PISO. */
export function usesRelaxation(config: SolverConfig): boolean {
  return solverAlgorithm(config) !== 'PISO'
}

export const WRITE_PRECISIONS = ['single', 'double'] as const
export const START_FROM = ['latestTime', 'startTime', 'firstTime'] as const
export const DDT_SCHEMES = ['Euler', 'backward', 'CrankNicolson'] as const

export const DIV_U_SCHEMES = [
  'bounded Gauss upwind',
  'bounded Gauss linearUpwind grad(U)',
  'bounded Gauss limitedLinear 1',
  'Gauss linear',
] as const
export const DIV_SCALAR_SCHEMES = [
  'bounded Gauss upwind',
  'bounded Gauss limitedLinear 1',
  'Gauss linear',
] as const
export const LAPLACIAN_SCHEMES = [
  'Gauss linear corrected',
  'Gauss linear limited corrected 0.5',
  'Gauss linear orthogonal',
] as const
export const SNGRAD_SCHEMES = ['corrected', 'limited corrected 0.5', 'orthogonal'] as const

/** The four transient per-step thresholds (E8c), same order, their own DOM ids. */
export const TRANSIENT_RESIDUAL_KEYS: { key: keyof ResidualControl & string; labelKey: string; domId: string }[] = [
  { key: 'p_rgh', labelKey: 'label.res_pressure', domId: 'slv-tres-prgh' },
  { key: 'U', labelKey: 'label.res_velocity', domId: 'slv-tres-U' },
  { key: 'T', labelKey: 'label.res_temperature', domId: 'slv-tres-T' },
  { key: 'k_epsilon', labelKey: 'label.res_turbulence', domId: 'slv-tres-kepsilon' },
]

/** The four residual thresholds, in the panel's order. */
export const RESIDUAL_KEYS: {
  key: keyof ResidualControl & string
  labelKey: string
  /** The legacy editor's DOM id, so one selector addresses both apps. */
  domId: string
}[] = [
  { key: 'p_rgh', labelKey: 'label.res_pressure', domId: 'slv-res-prgh' },
  { key: 'U', labelKey: 'label.res_velocity', domId: 'slv-res-U' },
  { key: 'T', labelKey: 'label.res_temperature', domId: 'slv-res-T' },
  { key: 'k_epsilon', labelKey: 'label.res_turbulence', domId: 'slv-res-kepsilon' },
]

export const RELAXATION_KEYS: { key: string; label: string; domId: string }[] = [
  { key: 'p_rgh', label: 'p_rgh', domId: 'slv-relax-p' },
  { key: 'U', label: 'U', domId: 'slv-relax-u' },
  { key: 'T', label: 'T', domId: 'slv-relax-t' },
  { key: 'k_epsilon', label: 'k / ε', domId: 'slv-relax-ke' },
]
