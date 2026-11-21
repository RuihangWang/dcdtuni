/**
 * Liquid-cooling configuration, typed.
 *
 * Mirrors where the legacy editor keeps this state so a project saved by either
 * app loads in the other:
 *
 *   runDefaults.liquid.enabled           — master switch
 *   runDefaults.liquid.inheritResults    — reuse the previous liquid solve
 *   runDefaults.liquid.solver            — the solver knobs below
 *   models.materialModels.fluids         — a ONE-entry dict keyed by fluid.type
 *
 * The fluids dict holding exactly one entry keyed by `type` is not incidental:
 * `_liqSetFluid` (STL_generate.html:1483) clears the dict and re-inserts under
 * the new type, so editing a property to 'custom' re-keys the entry.  Writing a
 * second key here would make `_liqFluid`, which returns `Object.values(...)[0]`,
 * pick an arbitrary one.
 */

export interface FluidProps {
  type: string
  rho: number
  mu: number
  cp: number
  k: number
}

/** Preset coolants, byte-identical to `_LC_FLUIDS` in js/liquid_cooling.js:13. */
export const FLUID_PRESETS: Record<string, FluidProps> = {
  water: { type: 'water', rho: 992.2, mu: 0.000653, cp: 4180, k: 0.632 },
  water20C: { type: 'water20C', rho: 998.2, mu: 0.001002, cp: 4182, k: 0.6 },
  glycol_25: { type: 'glycol_25', rho: 1032, mu: 0.0018, cp: 3850, k: 0.48 },
  glycol_50: { type: 'glycol_50', rho: 1065, mu: 0.004, cp: 3350, k: 0.37 },
}

export const FLUID_OPTIONS = [
  { value: 'water', label: 'Water 40°C' },
  { value: 'water20C', label: 'Water 20°C' },
  { value: 'glycol_25', label: 'Glycol 25%' },
  { value: 'glycol_50', label: 'Glycol 50%' },
  { value: 'custom', label: 'Custom' },
] as const

export interface LiquidSolverWeights {
  mass: number
  momentum: number
  energy: number
  bc: number
  ic: number
}

export interface LiquidSolverConfig {
  dt_s: number
  N_t: number
  bdf_order: number
  lr: number
  max_epochs: number
  convergence_tol: number
  device: string
  weights: LiquidSolverWeights
}

/** Defaults from `updateLiquidSolverParam` (STL_generate.html:10996). */
export function defaultLiquidSolver(): LiquidSolverConfig {
  return {
    dt_s: 1.0,
    N_t: 10,
    bdf_order: 1,
    lr: 0.1,
    max_epochs: 10000,
    convergence_tol: 1e-6,
    device: 'cpu',
    weights: { mass: 1.0, momentum: 1e-6, energy: 1.0, bc: 100.0, ic: 10.0 },
  }
}

export interface LiquidConfig {
  enabled: boolean
  inheritResults: boolean
  solver: LiquidSolverConfig
}

type Dict = Record<string, unknown>

function asDict(v: unknown): Dict {
  return v && typeof v === 'object' ? (v as Dict) : {}
}

function num(v: unknown, fallback: number): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

/**
 * Read the liquid config out of a project, filling every gap with the legacy
 * default.  A project saved before a knob existed must not read as 0.
 */
export function readLiquidConfig(project: unknown): LiquidConfig {
  const liq = asDict(asDict(asDict(project).runDefaults).liquid)
  const solver = asDict(liq.solver)
  const d = defaultLiquidSolver()
  const w = asDict(solver.weights)
  return {
    enabled: liq.enabled === true,
    inheritResults: liq.inheritResults === true,
    solver: {
      dt_s: num(solver.dt_s, d.dt_s),
      N_t: num(solver.N_t, d.N_t),
      bdf_order: num(solver.bdf_order, d.bdf_order),
      lr: num(solver.lr, d.lr),
      max_epochs: num(solver.max_epochs, d.max_epochs),
      convergence_tol: num(solver.convergence_tol, d.convergence_tol),
      device: typeof solver.device === 'string' ? solver.device : d.device,
      weights: {
        mass: num(w.mass, d.weights.mass),
        momentum: num(w.momentum, d.weights.momentum),
        energy: num(w.energy, d.weights.energy),
        bc: num(w.bc, d.weights.bc),
        ic: num(w.ic, d.weights.ic),
      },
    },
  }
}

/**
 * Read the single coolant entry.  Returns the `water` preset when the project
 * carries none, which is what `_syncFluidInputs` falls back to per-field.
 */
export function readFluid(project: unknown): FluidProps {
  const fluids = asDict(
    asDict(asDict(asDict(project).models).materialModels).fluids
  )
  const first = Object.values(fluids)[0]
  const water = FLUID_PRESETS.water as FluidProps
  if (!first || typeof first !== 'object') return { ...water }
  const f = first as Dict
  return {
    type: typeof f.type === 'string' ? f.type : 'custom',
    rho: num(f.rho, water.rho),
    mu: num(f.mu, water.mu),
    cp: num(f.cp, water.cp),
    k: num(f.k, water.k),
  }
}

/**
 * Which entry of the preset dropdown a fluid corresponds to.
 *
 * Matched on `type` alone, exactly as the legacy select does: editing any
 * property sets `type = 'custom'`, so the type is the authority and a numeric
 * comparison would disagree with the legacy UI whenever a custom fluid happened
 * to hold preset values.
 */
export function fluidPresetKey(fluid: FluidProps): string {
  return fluid.type in FLUID_PRESETS ? fluid.type : 'custom'
}

/** Total simulated time shown next to dt and N_t. */
export function totalTimeSeconds(solver: LiquidSolverConfig): number {
  return solver.dt_s * solver.N_t
}
