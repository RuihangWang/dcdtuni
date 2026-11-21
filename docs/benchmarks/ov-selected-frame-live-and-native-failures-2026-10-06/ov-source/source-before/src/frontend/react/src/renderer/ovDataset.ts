/** Server side of loading a result into the OV display: list the selected
 * case's runs and ask the server for a signed display grant. The browser only
 * ever picks a run identity; the server converts the run and names the file,
 * and Kit verifies the grant before reading anything. */
import type { OvDisplayGrant } from './ovProtocol'

export interface OvRun { name: string; runId: string }

export interface OvDatasetGrantResponse {
  grant: OvDisplayGrant
  run: string
  time: string
  fields: string[]
  cached: boolean
  format: 'ensight'
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>

async function body(resp: Response, url: string): Promise<Record<string, unknown>> {
  const data = (await resp.json().catch(() => ({}))) as Record<string, unknown>
  if (!resp.ok) throw new Error(typeof data.error === 'string' ? data.error : `${url} -> ${resp.status}`)
  return data
}

export async function listOvRuns(fetchImpl: Fetch = fetch): Promise<OvRun[]> {
  const data = await body(await fetchImpl('/api/pp/runs'), '/api/pp/runs')
  const runs = Array.isArray(data.runs) ? data.runs : []
  return runs.filter((r): r is OvRun => typeof r?.name === 'string' && typeof r?.runId === 'string'
    && /^[0-9a-f]{64}$/.test(r.runId)).map(r => ({ name: r.name, runId: r.runId }))
}

export async function requestOvDatasetGrant(runId: string, fetchImpl: Fetch = fetch): Promise<OvDatasetGrantResponse> {
  const url = '/api/ov/result-dataset'
  const data = await body(await fetchImpl(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ runId }),
  }), url)
  const grant = data.grant as OvDisplayGrant | undefined
  if (!grant || typeof grant.signature !== 'string' || typeof grant.payload !== 'object') {
    throw new Error('server returned no display grant')
  }
  return data as unknown as OvDatasetGrantResponse
}

export interface OvLiquidGrantResponse { grant: OvDisplayGrant; runId: string; status: string; samples: number; solver: string }

/** Ask the server to sign a liquid-result display grant for a DAE run of the
 * selected case. A legacy result without object bindings comes back as an
 * error naming the missing keys. */
export async function requestLiquidResultGrant(runId: string, fetchImpl: Fetch = fetch): Promise<OvLiquidGrantResponse> {
  const url = '/api/ov/liquid-result-grant'
  const resp = await fetchImpl(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ runId }) })
  const data = (await resp.json().catch(() => ({}))) as Record<string, unknown>
  if (!resp.ok) {
    const missing = Array.isArray(data.missing) ? ` (${(data.missing as string[]).join(', ')})` : ''
    throw new Error((typeof data.error === 'string' ? data.error : `${url} -> ${resp.status}`) + missing)
  }
  const grant = data.grant as OvDisplayGrant | undefined
  if (!grant || typeof grant.signature !== 'string') throw new Error('server returned no display grant')
  return data as unknown as OvLiquidGrantResponse
}
