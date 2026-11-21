import {
  addCpCuPair,
  applyCpCuPreset,
  CP_PRESET_KEYS,
  pairHeatMode,
  readPairs,
  readRawPairs,
  removeCpCuPair,
  setCpCuHeatMode,
  updateCpCuPair,
  type CpHeatMode,
  type CpNumericField,
  type CpPresetKey,
} from '../liquid/liquidColdPlates'
import type { Construction } from '../schema/cfdproj'
import type { TranslationKey } from '../i18n'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { KeyframeBadge } from '../transient/KeyframeBadge'

/**
 * The 液冷热力学 group: a server's cold plate / compute unit pairs.
 *
 * Ported from `renderEquipmentLiquidInspector` (STL_generate.html:10175). One
 * card per pair, a preset dropdown that stamps six numbers at once, and the six
 * numbers themselves — heat load, thermal resistance, the rated operating point
 * of the plate (Δp at a flow rate), and the two transient terms.
 *
 * The preset is not a category, it is a SHORTCUT: picking 'cpu' writes the six
 * values and editing any of them re-types the pair as 'custom'. So the dropdown
 * always describes the numbers actually in the pair.
 */

export interface ColdPlateSectionProps {
  /** The RACK. Pairs hang off its `constructions.servers[serverId]`. */
  model: Construction
  serverId: string
}

const UNITS: Record<CpNumericField, string> = {
  Q_W: 'W',
  R_th: '°C/W',
  dp_rated_kPa: 'kPa',
  Q_rated_LPM: 'LPM',
  C_solid: 'J/K',
  V_fluid_mL: 'mL',
}

const PRESET_LABEL_KEYS: Record<CpPresetKey, TranslationKey> = {
  cpu: 'liquid.cpPreset.cpu',
  gpu: 'liquid.cpPreset.gpu',
  custom: 'liquid.cpPreset.custom',
}

const LABEL_KEYS: Record<CpNumericField, TranslationKey> = {
  Q_W: 'liquid.Q_W',
  R_th: 'liquid.R_th',
  dp_rated_kPa: 'liquid.dp_rated',
  Q_rated_LPM: 'liquid.Q_rated',
  C_solid: 'liquid.C_solid',
  V_fluid_mL: 'liquid.V_fluid',
}

/** step / min per field, as the legacy's number inputs declare them. */
const STEPS: Record<CpNumericField, { step: number; min: number }> = {
  Q_W: { step: 50, min: 0 },
  R_th: { step: 0.005, min: 0.001 },
  dp_rated_kPa: { step: 1, min: 0 },
  Q_rated_LPM: { step: 0.1, min: 0.1 },
  C_solid: { step: 10, min: 0 },
  V_fluid_mL: { step: 1, min: 0 },
}

const FIELDS: CpNumericField[] = [
  'Q_W',
  'R_th',
  'dp_rated_kPa',
  'Q_rated_LPM',
  'C_solid',
  'V_fluid_mL',
]

export function ColdPlateSection({ model, serverId }: ColdPlateSectionProps) {
  const { t } = useTranslation()
  const servers = (model.constructions as { servers?: Record<string, Record<string, unknown>> })
    ?.servers
  const pairs = readPairs(servers?.[serverId])

  return (
    <div className="prop-group" id="cpcu-group" style={{ borderLeftColor: '#00b4dc' }}>
      <div className="prop-title" style={{ color: '#00b4dc' }}>
        {t('liquid.cpcu_thermal_title')}
      </div>
      <div className="prop-hint">{t('liquid.cpcu_thermal_desc')}</div>

      <div id="cpcu-pairs">
        {pairs.map((pair, idx) => (
          <div
            key={idx}
            className="cpcu-card"
            data-cpcu-index={idx}
            style={{
              border: '1px solid rgba(0,180,220,0.3)',
              borderRadius: 4,
              padding: '6px 8px',
              marginBottom: 6,
              background: 'rgba(0,180,220,0.05)',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 4,
              }}
            >
              <span style={{ fontSize: 12, color: '#00b4dc', fontWeight: 500 }}>
                CP-CU #{idx + 1}
              </span>
              <button
                id={`cpcu-${idx}-remove`}
                className="btn"
                style={{
                  // `.btn` is a full-width block in this stylesheet; the legacy
                  // remove button is a chip beside the title, so it has to opt
                  // out of the width or it pushes the title onto its own line.
                  width: 'auto',
                  display: 'inline-block',
                  margin: 0,
                  padding: '1px 8px',
                  fontSize: 10,
                  color: '#e06c75',
                  borderColor: '#e06c75',
                  background: 'transparent',
                }}
                onClick={() => removeCpCuPair(model.uid, serverId, idx)}
              >
                ✕
              </button>
            </div>

            <div className="prop-row">
              <label htmlFor={`cpcu-${idx}-preset`}>{t('liquid.cpPreset')}</label>
              <select
                id={`cpcu-${idx}-preset`}
                value={pair.preset}
                onChange={(e) =>
                  applyCpCuPreset(model.uid, serverId, idx, e.target.value as CpPresetKey)
                }
              >
                {CP_PRESET_KEYS.map((key) => (
                  <option key={key} value={key}>
                    {t(PRESET_LABEL_KEYS[key])}
                  </option>
                ))}
              </select>
            </div>

            <div className="prop-row">
              <label htmlFor={`cpcu-${idx}-name`}>{t('liquid.cpcu_name')}</label>
              <input
                id={`cpcu-${idx}-name`}
                type="text"
                key={`${serverId}-${idx}-${pair.name}`}
                defaultValue={pair.name}
                onBlur={(e) =>
                  e.target.value !== pair.name &&
                  updateCpCuPair(model.uid, serverId, idx, 'name', e.target.value)
                }
                onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
              />
            </div>

            {(() => {
              // .liquidrun v4: the heat is a prescribed power, or a prescribed
              // coolant temperature rise whose heat follows the flow.
              const heat = pairHeatMode(readRawPairs(servers?.[serverId])[idx])
              return (
                <>
                  <div className="prop-row">
                    <label htmlFor={`cpcu-${idx}-heat-mode`}>{t('liquid.cp_heat_mode')}</label>
                    <select
                      id={`cpcu-${idx}-heat-mode`}
                      value={heat.mode}
                      onChange={(e) => setCpCuHeatMode(model.uid, serverId, idx, e.target.value as CpHeatMode)}
                    >
                      <option value="power">{t('liquid.cp_heat_mode_power')}</option>
                      <option value="temperature_rise">{t('liquid.cp_heat_mode_rise')}</option>
                    </select>
                  </div>
                  {heat.mode === 'temperature_rise' && (
                    <div className="prop-row kf-field">
                      <label htmlFor={`cpcu-${idx}-dT_K`}>
                        {t('liquid.cp_dT')} (K)
                        <KeyframeBadge modelUid={model.uid} param="cp.dT" target={`${serverId}/${idx}`} />
                      </label>
                      <NumberField
                        id={`cpcu-${idx}-dT_K`}
                        value={heat.dT_K}
                        step={0.5}
                        min={0}
                        onCommit={(v) => updateCpCuPair(model.uid, serverId, idx, 'dT_K', v)}
                      />
                    </div>
                  )}
                </>
              )
            })()}
            {FIELDS.filter((field) => field !== 'Q_W' || pairHeatMode(readRawPairs(servers?.[serverId])[idx]).mode === 'power').map((field) => (
              <div className={field === 'Q_W' ? 'prop-row kf-field' : 'prop-row'} key={field}>
                <label htmlFor={`cpcu-${idx}-${field}`}>
                  {t(LABEL_KEYS[field])} ({UNITS[field]})
                  {field === 'Q_W' && <KeyframeBadge modelUid={model.uid} param="cp.heat" target={`${serverId}/${idx}`} />}
                </label>
                <NumberField
                  id={`cpcu-${idx}-${field}`}
                  value={pair[field]}
                  step={STEPS[field].step}
                  min={STEPS[field].min}
                  onCommit={(v) => updateCpCuPair(model.uid, serverId, idx, field, v)}
                />
              </div>
            ))}
          </div>
        ))}
      </div>

      <button
        id="cpcu-add"
        className="btn"
        style={{
          width: '100%',
          background: 'rgba(0,180,220,0.1)',
          borderColor: 'rgba(0,180,220,0.4)',
          color: '#00b4dc',
        }}
        onClick={() => addCpCuPair(model.uid, serverId)}
      >
        {t('liquid.add_cpcu_pair')}
      </button>
      {/* The server's total chip power, which is what the loop has to carry.
          Reading it off the cards one by one is how a mis-typed heat load gets
          missed. */}
      <div className="prop-hint" id="cpcu-total">
        {`Σ Q = ${pairs.reduce((sum, p) => sum + p.Q_W, 0)} W`}
      </div>
    </div>
  )
}
