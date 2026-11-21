import type { CfdProject } from '../schema/cfdproj'
import { FLUID_OPTIONS, FLUID_PRESETS, fluidPresetKey, readFluid, type FluidProps } from '../liquid/liquidConfig'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'

/** One project-owned secondary coolant, consumed by both legacy and Newton compilation. */
export function LiquidFluidEditor({project}:{project:CfdProject|null}) {
  const {t}=useTranslation()
  const fluid=readFluid(project)
  const presetKey=fluidPresetKey(fluid)
  function setFluidProp(key:keyof FluidProps,value:number) {
    if(!project||!Number.isFinite(value)||value<=0)return false
    store.setFluid({...fluid,[key]:value,type:'custom'})
  }
  function selectPreset(value:string) {
    if(!project||value==='custom')return
    const preset=FLUID_PRESETS[value]
    if(preset)store.setFluid({...preset})
  }
  return <fieldset className="liquid-fluid-fieldset" disabled={!project}>
      <div className="prop-group liquid-fluid-editor">
        <div className="prop-title">
          {t('liquid.fluid_config_title')}
        </div>
        <div className="prop-row">
          <label htmlFor="lc-fluid-select">{t('liquid.coolant_preset')}</label>
          <select id="lc-fluid-select" value={presetKey} onChange={(e) => selectPreset(e.target.value)}>
            {FLUID_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <details id="lsm-fluid-details" className="lc-details">
          <summary>{t('liquid.fluid_properties')}</summary>
          <div className="lc-details-body">
            <div className="prop-row">
              <label htmlFor="lsm-fluid-rho">{t('liquid.fluid_rho')}</label>
              <NumberField
                id="lsm-fluid-rho"
                value={fluid.rho}
                step={1}
                min={500}
                onCommit={(v) => setFluidProp('rho', v)}
              />
            </div>
            <div className="prop-row">
              <label htmlFor="lsm-fluid-cp">{t('liquid.fluid_cp')}</label>
              <NumberField
                id="lsm-fluid-cp"
                value={fluid.cp}
                step={10}
                min={100}
                onCommit={(v) => setFluidProp('cp', v)}
              />
            </div>
            <div className="prop-row">
              <label htmlFor="lsm-fluid-mu">{t('liquid.fluid_mu')}</label>
              <NumberField
                id="lsm-fluid-mu"
                value={fluid.mu}
                step={0.0001}
                min={0.00001}
                onCommit={(v) => setFluidProp('mu', v)}
              />
            </div>
            <div className="prop-row">
              <label htmlFor="lsm-fluid-k">{t('liquid.fluid_k')}</label>
              <NumberField
                id="lsm-fluid-k"
                value={fluid.k}
                step={0.01}
                min={0.01}
                onCommit={(v) => setFluidProp('k', v)}
              />
            </div>
          </div>
        </details>
      </div>

  </fieldset>
}
