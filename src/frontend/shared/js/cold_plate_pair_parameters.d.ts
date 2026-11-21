export interface ColdPlatePairInput {
  Q_W?: unknown
  heatLoad?: unknown
  R_th?: unknown
  dp_rated_kPa?: unknown
  Q_rated_LPM?: unknown
  C_solid?: unknown
  V_fluid_mL?: unknown
}
export interface ColdPlateFluidContext { rho?: unknown }
export interface ColdPlatePairParameters {
  dp_a: number
  dp_b: 0
  UA_W_per_K: number
  // These two legacy fields are passed through; validation stays at export.
  C_solid_J_per_K: unknown
  V_fluid_m3: number
  Q_W: unknown
}
export interface ColdPlatePairKernel {
  parameters(pair: ColdPlatePairInput, fluid?: ColdPlateFluidContext): ColdPlatePairParameters
}
declare const kernel: Readonly<ColdPlatePairKernel>
export default kernel
