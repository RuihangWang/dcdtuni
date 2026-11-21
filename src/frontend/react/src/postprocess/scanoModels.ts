/** The AI models' cards (`/api/ai/scano/models`), fetched once per page. */

export interface Range {
  min: number
  max: number
}

export type TrainingRanges = Partial<Record<
  'server_power_W' | 'server_airflow_m3s' | 'acu_supply_airflow_m3s' | 'acu_supply_temperature_C'
  | 'servers_per_room' | 'acus_per_room' | 'nodes_per_room', Range>>

export interface ModelCardLite {
  id: string
  recommended?: boolean
  training_ranges?: TrainingRanges | null
  error?: string
}

let pending: Promise<ModelCardLite[]> | null = null

export function fetchModelCards(): Promise<ModelCardLite[]> {
  if (!pending) {
    pending = fetch('/api/ai/scano/models', { cache: 'no-store' })
      .then(async (resp) => ((await resp.json()) as { models?: ModelCardLite[] }).models ?? [])
      .catch(() => {
        pending = null
        return []
      })
  }
  return pending
}

/** Slider bounds: the training range widened to hold the current value, rounded outward to whole steps. */
export function sliderBounds(range: Range | undefined, current: number, fallback: [number, number],
  step: number): [number, number] {
  const [lo, hi] = range ? [range.min, range.max] : fallback
  const snap = (v: number, round: (x: number) => number) => Number((round(v / step) * step).toFixed(6))
  return [snap(Math.min(lo, current), Math.floor), snap(Math.max(hi, current), Math.ceil)]
}

/** Test seam. */
export function resetModelCardsForTest(): void {
  pending = null
}
