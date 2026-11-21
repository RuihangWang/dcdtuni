/**
 * How the liquid post-processing panels print numbers and network names.
 *
 * One place for the units the panels promise: temperatures in °C, flows in
 * L/min with kg/s beside them, pressures in kPa, heat in W or kW. A value the
 * result does not have prints as an em dash, never as 0.
 */

import type { TranslationKey } from '../i18n'
import { KELVIN_OFFSET } from './liquidResults'
import type { NetworkEdgeType, NetworkRow } from './liquidNetwork'

export const DASH = '—'

const ok = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

export function fmtNumber(v: number | null | undefined, digits: number): string {
  return ok(v) ? v.toFixed(digits) : DASH
}

/** Kelvin in, °C out. */
export function fmtCelsius(kelvin: number | null | undefined, digits = 2): string {
  return ok(kelvin) ? `${(kelvin - KELVIN_OFFSET).toFixed(digits)} °C` : DASH
}

export function fmtLpm(lpm: number | null | undefined, digits = 2): string {
  return ok(lpm) ? `${lpm.toFixed(digits)} L/min` : DASH
}

export function fmtKgS(kgS: number | null | undefined): string {
  return ok(kgS) ? `${kgS.toPrecision(4)} kg/s` : DASH
}

/** "20.51 L/min · 0.3528 kg/s" — the panels always show both. */
export function fmtFlow(lpm: number | null | undefined, kgS: number | null | undefined, digits = 2): string {
  if (!ok(lpm) && !ok(kgS)) return DASH
  return `${fmtLpm(lpm, digits)} · ${fmtKgS(kgS)}`
}

export function fmtKPa(pa: number | null | undefined, digits = 2): string {
  return ok(pa) ? `${(pa / 1000).toFixed(digits)} kPa` : DASH
}

export function fmtVelocity(ms: number | null | undefined, digits = 3): string {
  return ok(ms) ? `${ms.toFixed(digits)} m/s` : DASH
}

/** Watts, switching to kW from 10 kW so a 25 200 W total stays readable. */
export function fmtWatts(w: number | null | undefined): string {
  if (!ok(w)) return DASH
  const text = Math.abs(w) >= 10000 ? `${(w / 1000).toFixed(2)} kW` : `${w.toFixed(1)} W`
  // A value that rounds to zero is zero, not "-0.0 W".
  return /^-0(\.0+)? /.test(text) ? text.slice(1) : text
}

/** Residuals: tiny numbers in exponent form. */
export function fmtSci(v: number | null | undefined, unit = ''): string {
  return ok(v) ? `${v.toExponential(2)}${unit ? ' ' + unit : ''}` : DASH
}

export const EDGE_TYPE_KEY: Record<NetworkEdgeType, TranslationKey> = {
  pipe: 'lq_pp.type_pipe',
  valve: 'lq_pp.type_valve',
  pump: 'lq_pp.type_pump',
  heat_exchanger: 'lq_pp.type_heat_exchanger',
  cold_plate: 'lq_pp.type_cold_plate',
  other: 'lq_pp.type_other',
}

type T = (key: TranslationKey, vars?: Record<string, string | number>) => string

/** The readable form of an edge's own part ('branch_in' -> 'supply branch'); unknown parts stay as written. */
export function edgePartText(part: string, t: T): string {
  if (part === 'branch_in') return t('lq_pp.part_branch_in')
  if (part === 'branch_out') return t('lq_pp.part_branch_out')
  if (part === 'pump') return t('lq_pp.part_pump')
  if (part === 'hx') return t('lq_pp.part_hx')
  if (/^mf_supply(_bottom)?_header$/.test(part)) return t('lq_pp.part_supply_header')
  if (/^mf_return(_bottom)?_header$/.test(part)) return t('lq_pp.part_return_header')
  const plate = /^cp_(.+)$/.exec(part)
  if (plate) return t('lq_pp.part_cold_plate', { name: plate[1]! })
  return part
}

const NODE_PARTS: Record<string, TranslationKey> = {
  supply: 'lq_pp.node_supply',
  return: 'lq_pp.node_return',
  pump_discharge: 'lq_pp.node_pump_discharge',
  in: 'lq_pp.node_in',
  out: 'lq_pp.node_out',
  mf_supply: 'lq_pp.node_mf_supply',
  mf_return: 'lq_pp.node_mf_return',
  mf_supply_bottom: 'lq_pp.node_mf_supply_bottom',
  mf_return_bottom: 'lq_pp.node_mf_return_bottom',
}

export function nodePartText(part: string, t: T): string {
  if (part === 'junction') return ''
  const key = NODE_PARTS[part]
  return key ? t(key) : part
}

export function joinName(ownerLabel: string | null, part: string, fallback: string): string {
  if (ownerLabel === null) return fallback
  return part ? `${ownerLabel} · ${part}` : ownerLabel
}

/** The row's name as the panel prints it. */
export function rowName(row: NetworkRow, t: T): string {
  return joinName(row.ownerLabel, row.part ? edgePartText(row.part, t) : '', row.edgeId)
}
