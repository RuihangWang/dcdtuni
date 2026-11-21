/**
 * The ONE writer of the steady/transient choice (transient design §2, E8b).
 *
 * Air and liquid each run steady or transient (`transient.airMode` /
 * `transient.liquidMode`); `transient.mode` is derived — transient when either
 * is — so the timeline, the freeze at t > 0 and playback follow it. In the same
 * edit the air mode is mirrored to `simulationConfig.solverType` (the air
 * runner's truth) and the liquid mode to the DAE's `analysis.mode`, so the
 * exporters keep reading what they read today. The top-bar switch, the
 * automation `setSolverType` and `setSolverConfig('solverType')` all come
 * through here.
 */
import * as store from '../projectStore'
import { isLiquidModeActive } from '../liquid/liquidModeState'
import { transientModel, transientOf, type CoolingDomain, type DomainMode, type TransientBlock } from './model'

export type SimulationMode = DomainMode

/** The cooling domain the editor shows (the 风冷 / 液冷 switch). */
export function activeCoolingDomain(): CoolingDomain {
  return isLiquidModeActive() ? 'liquid' : 'air'
}

type Bag = Record<string, unknown>
const bag = (v: unknown): Bag | null => (v && typeof v === 'object' ? (v as Bag) : null)

export function setSimulationMode(mode: SimulationMode, domain: CoolingDomain = activeCoolingDomain()): boolean {
  const ok = store.edit('setSimulationMode', `${domain} ${mode}`, (draft) => {
    const doc = draft as { transient?: TransientBlock; runDefaults?: Bag }
    const m = transientModel()
    const tr = doc.transient ?? (doc.transient = transientOf(draft))
    const modes = m.domainModes(draft)
    modes[domain] = mode
    tr.airMode = modes.air
    tr.liquidMode = modes.liquid
    tr.mode = m.modeOfDomains(modes.air, modes.liquid)
    const sim = bag(doc.runDefaults?.simulationConfig)
    if (sim) {
      sim.solverType = modes.air
      if (tr.mode === 'transient') sim.endTimeSeconds = tr.durationS
    }
    const experiment = bag(bag(doc.runDefaults?.liquid)?.experiment)
    const analysis = bag(bag(experiment?.dae)?.analysis)
    if (domain === 'liquid' && analysis) {
      analysis.mode = modes.liquid
      // A steady DAE starts from its steady state and has no load step to
      // march through (the rule the liquid panel's own switch applied).
      if (modes.liquid === 'steady') {
        analysis.initialization = 'steady'
        const settings = bag(experiment?.settings)
        if (settings) settings.loadStep = false
      }
    }
  })
  // A steady project has one time, the start.
  if (ok && transientOf(store.getProject()).mode === 'steady') store.setPlayhead(0)
  return ok
}
