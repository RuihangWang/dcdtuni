/**
 * The air-cooling post-processing data layer.
 *
 * Everything the viewport draws in this tab is a pure function of four binary
 * payloads the server produces from a solved case: the boundary mesh, a field's
 * per-point scalars, a slice, and a streamline set. This module fetches and
 * decodes them; nothing here touches Three.js, which is what makes it testable
 * without a GPU.
 *
 * ## The wire format
 *
 * `/api/pp/mesh/binary` is ONE buffer holding three arrays back to back, with
 * the per-patch offsets carried separately in `/api/pp/mesh`:
 *
 *   Float32 x totalPoints x 3     positions, xyz interleaved
 *   Uint32  x totalTriangles x 3  triangle indices, already offset per patch
 *   Uint32  x totalEdges x 2      original polygon edges, likewise
 *
 * The indices are ALREADY global — `pack_mesh_binary` adds each patch's point
 * offset before packing — so a patch's triangles index into the whole point
 * array, not into its own slice. Re-offsetting them here would fold every patch
 * onto the first one.
 *
 * ## Why this is checkable
 *
 * Three of the four endpoints are deterministic reads of a fixed time
 * directory, and `/api/pp/streamlines` seeds from a fixed RNG
 * (`np.random.default_rng(42)`), so the same request returns the same bytes.
 * That makes a captured response a usable fixture and a real oracle, rather
 * than a screenshot someone squints at.
 */

import {
  assertRevisionHeader,
  assertSessionIdentity,
  revisionHeaders,
  type PostprocessSession,
} from './ppRevision'
import {bindNumericProduct} from './numericProducts'
export type { PostprocessSession } from './ppRevision'

export interface PatchMeta {
  name: string
  nPoints: number
  nTriangles: number
  nEdges: number
  defaultVisible: boolean
}

/** A field the loaded run actually has, as /api/pp/mesh reports it. */
export interface FieldEntry {
  name: string
  type: 'scalar' | 'vector'
  label: string
  /**
   * A vector's per-axis names, `['Ux','Uy','Uz']`, which the server derives
   * itself (`_discover_fields`, postprocess.py:1985). They are the only way to
   * ask for ONE component: `/api/pp/field/U` returns the magnitude.
   */
  components?: string[]
}

export interface MeshMeta {
  patches: PatchMeta[]
  /** Cheap case/mesh stat identity for two-step preview reads; not a content receipt. */
  meshSnapshotKey?: string
  /**
   * The run's fields. This is the ONLY authoritative list — a hardcoded set
   * offers entries a turbulence-free case does not have, and probing with HEAD
   * requests does not work because the endpoint only answers GET.
   */
  fields?: FieldEntry[]
  totalPoints?: number
  totalTriangles?: number
  totalEdges?: number
  /**
   * The mesh's size, as `/api/mesh-preview/load` reports it (server.py:2179).
   *
   * `nCells` and `nBoundaryFaces` are read out of the polyMesh `owner` header's
   * `note`, so a mesh written without one sends them as null — hence the
   * `nBoundarySum` fallback, which is the per-patch face count added up. This
   * is the only place the cell count reaches the UI, and it is the number
   * `maxGlobalCells` was set against.
   */
  nCells?: number | null
  nBoundaryFaces?: number | null
  nBoundarySum?: number | null
  runId?: string
  timeValue?: number | null
  revision?: string
}

export interface DecodedMesh {
  /** xyz interleaved, length = totalPoints * 3. */
  positions: Float32Array
  /** Global point indices, length = totalTriangles * 3. */
  indices: Uint32Array
  /** Global point index pairs, length = totalEdges * 2. */
  edges: Uint32Array
  patches: PatchMeta[]
  totals: { points: number; triangles: number; edges: number }
}

export interface FieldInfo {
  name?: string
  min?: number
  max?: number
  components?: number
  [key: string]: unknown
}

export interface DecodedField {
  values: Float32Array
  info: FieldInfo
}

export interface RunEntry {
  name: string
  path: string
}

/**
 * Split the mesh buffer using the patch table.
 *
 * The buffer carries no header — the sizes come from the metadata — so a
 * mismatch between the two is silent corruption rather than an error. This
 * checks the arithmetic and says which part disagreed.
 */
export function decodeMesh(buffer: ArrayBuffer, meta: MeshMeta): DecodedMesh {
  const patches = meta.patches ?? []
  let points = 0
  let triangles = 0
  let edges = 0
  for (const p of patches) {
    points += p.nPoints
    triangles += p.nTriangles
    edges += p.nEdges
  }

  const expected = points * 3 * 4 + triangles * 3 * 4 + edges * 2 * 4
  if (buffer.byteLength !== expected) {
    throw new Error(
      `mesh buffer is ${buffer.byteLength} bytes, the patch table says ${expected} ` +
        `(${points} points, ${triangles} triangles, ${edges} edges). ` +
        'The buffer has no header, so this mismatch would otherwise read as garbage geometry.'
    )
  }

  let offset = 0
  const positions = new Float32Array(buffer, offset, points * 3)
  offset += points * 3 * 4
  const indices = new Uint32Array(buffer, offset, triangles * 3)
  offset += triangles * 3 * 4
  const edgePairs = new Uint32Array(buffer, offset, edges * 2)

  return {
    positions,
    indices,
    edges: edgePairs,
    patches,
    totals: { points, triangles, edges },
  }
}

/**
 * The [start, end) range of a patch's triangles within the global index array.
 *
 * Triangles are packed in patch order, so a patch owns a contiguous run. Used
 * to build one Three.js group per patch without copying the buffer.
 */
export function triangleRange(patches: PatchMeta[], index: number): [number, number] {
  let start = 0
  for (let i = 0; i < index; i++) start += (patches[i] as PatchMeta).nTriangles
  const self = patches[index] as PatchMeta | undefined
  return [start * 3, (start + (self?.nTriangles ?? 0)) * 3]
}

/** Likewise for a patch's points, which is what a per-patch field slice needs. */
export function pointRange(patches: PatchMeta[], index: number): [number, number] {
  let start = 0
  for (let i = 0; i < index; i++) start += (patches[i] as PatchMeta).nPoints
  const self = patches[index] as PatchMeta | undefined
  return [start, start + (self?.nPoints ?? 0)]
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  const body = (await resp.json()) as T & { error?: string }
  if (!resp.ok || body.error) {
    throw new Error(body.error ?? `${url} -> ${resp.status}`)
  }
  return body
}

export async function listRuns(): Promise<RunEntry[]> {
  const body = await json<{ runs: RunEntry[] }>('/api/pp/runs')
  return body.runs ?? []
}

export interface InitResult {
  success: boolean
  message: string
  cached: boolean
  n_cells: number
  timeValue: number | null
  runId: string
  revision: string
  timing?: Record<string, number>
  /**
   * Fields that came back all-NaN, named by the server
   * (`nan_warnings`, postprocess.py:1954).
   *
   * A NaN field is a reconstructPar artefact of a solve that diverged for that
   * variable. It renders as a blank surface, which reads as "post-processing is
   * broken" rather than "this variable did not converge" — so the panel says so
   * instead of drawing nothing.
   */
  nan_warnings?: string[]
}

/**
 * Load a run into the server's post-processor.
 *
 * Slow and stateful: it runs foamToVTK and reads the result in parallel, ~40 s
 * on a 600k-cell case. The processor is a PROCESS-WIDE singleton, so a second
 * caller pointing at another run replaces the first one's data — the same
 * single-case limitation the mesh/solve jobs have.
 *
 * `signal` abandons the WAIT, not the work: the server has no cancel and keeps
 * converting. That is still what the user wants — it frees the UI and stops the
 * follow-on mesh and field fetches — and it is what the legacy AbortController
 * does too (STL_generate.html:21363).
 */
export async function initRun(
  runDir: string,
  timeValue: number | null,
  signal?: AbortSignal,
): Promise<InitResult> {
  const result = await json<InitResult>('/api/pp/init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ runDir, ...(timeValue === null ? {} : { timeValue }) }),
    signal,
  })
  if (timeValue !== null && result.timeValue !== timeValue) {
    throw new Error(`Postprocess requested time ${timeValue}, server returned ${String(result.timeValue)}`)
  }
  if (!result.runId || !result.revision || result.timeValue === undefined) {
    throw new Error('Postprocess init response lacks run/time/revision identity')
  }
  return result
}

export async function fetchMeshMeta(session: PostprocessSession, signal?: AbortSignal): Promise<MeshMeta> {
  const meta = await json<MeshMeta>('/api/pp/mesh', { headers: revisionHeaders(session), signal })
  assertSessionIdentity(meta, session)
  return meta
}

export async function fetchMesh(meta: MeshMeta, session: PostprocessSession, signal?: AbortSignal): Promise<DecodedMesh> {
  assertSessionIdentity(meta, session)
  const resp = await fetch('/api/pp/mesh/binary', { headers: revisionHeaders(session), signal })
  if (!resp.ok) throw new Error(`/api/pp/mesh/binary -> ${resp.status}`)
  assertRevisionHeader(resp, session)
  return bindNumericProduct(decodeMesh(await resp.arrayBuffer(), meta),resp,'mesh',session)
}

/**
 * A field's per-point scalars.
 *
 * The body opens with a 16-BYTE BINARY HEADER — float min, float max, uint32
 * component count, uint32 point count — and the values follow it. The same
 * numbers are repeated in the `X-Field-Info` HTTP header, which is the fallback
 * when the body is too short to hold a header at all.
 *
 * Reading the body as one flat Float32Array is the mistake this function
 * exists to prevent, and it is nearly invisible: the array comes back FOUR
 * values too long, every point takes the value of the point four positions
 * later, and the auto-scale range picks up `nComponents` reinterpreted as a
 * float — 1.4e-45 — which stretches the colour scale from zero and pushes the
 * whole room into the top of the ramp. The picture still renders, still has
 * structure, and is wrong everywhere. It was caught by the length check in
 * `buildSurface`, not by looking at it.
 */
export function decodeField(buffer: ArrayBuffer, fallback: FieldInfo = {}): DecodedField {
  if (buffer.byteLength < 16) {
    return { values: new Float32Array(buffer), info: fallback }
  }
  const head = new DataView(buffer, 0, 16)
  const min = head.getFloat32(0, true)
  const max = head.getFloat32(4, true)
  const components = head.getUint32(8, true)
  const points = head.getUint32(12, true)
  return {
    values: new Float32Array(buffer, 16, points * components),
    info: { ...fallback, min, max, components, points },
  }
}

export async function fetchField(name: string, session: PostprocessSession, signal?: AbortSignal): Promise<DecodedField> {
  const resp = await fetch(`/api/pp/field/${encodeURIComponent(name)}`, { headers: revisionHeaders(session), signal })
  if (!resp.ok) throw new Error(`/api/pp/field/${name} -> ${resp.status}`)
  assertRevisionHeader(resp, session)
  let info: FieldInfo = {}
  const raw = resp.headers.get('X-Field-Info')
  if (raw) {
    try {
      info = JSON.parse(raw) as FieldInfo
    } catch {
      // A malformed header is not worth failing the fetch over; the body\'s own
      // header carries the same numbers.
    }
  }
  const decoded=decodeField(await resp.arrayBuffer(),info)
  bindNumericProduct(decoded.values,resp,'field',session)
  return decoded
}

export interface SliceRequest {
  origin: [number, number, number]
  normal: [number, number, number]
  field?: string
}

export interface SlicePayload {
  positions: Float32Array
  indices: Uint32Array
  /** One scalar per point, or null when the field was absent. */
  values: Float32Array | null
  scalarRange: [number, number]
  field: string
  nPoints: number
  nTriangles: number
}

/**
 * A cut plane through the volume.
 *
 * Self-describing, unlike the mesh buffer: the payload is
 *
 *   uint32 headerLen | JSON header (padded) | Float32 positions | Uint32 indices | Float32 scalars
 *
 * `headerLen` is the PADDED length. The server pads the JSON to a 4-byte
 * boundary precisely so the typed arrays after it are aligned — reading the
 * unpadded length instead puts the Float32Array on an odd offset and the
 * constructor throws. The trailing NULs are inside the JSON slice and
 * `JSON.parse` ignores them.
 */
export function decodeSlice(buffer: ArrayBuffer): SlicePayload {
  const view = new DataView(buffer)
  const headerLen = view.getUint32(0, true)
  const headerText = new TextDecoder().decode(new Uint8Array(buffer, 4, headerLen))
  const header = JSON.parse(headerText.replace(/\0+$/, '')) as {
    nPoints?: number
    nTriangles?: number
    scalarRange?: [number, number]
    field?: string
  }
  const nPoints = header.nPoints ?? 0
  const nTriangles = header.nTriangles ?? 0

  let off = 4 + headerLen
  const positions = new Float32Array(buffer, off, nPoints * 3)
  off += nPoints * 3 * 4
  const indices = new Uint32Array(buffer, off, nTriangles * 3)
  off += nTriangles * 3 * 4
  // The scalars are optional in practice: an empty slice writes a zero-length
  // array rather than omitting the section.
  const remaining = buffer.byteLength - off
  const values = remaining >= nPoints * 4 && nPoints > 0
    ? new Float32Array(buffer, off, nPoints)
    : null

  return {
    positions,
    indices,
    values,
    scalarRange: header.scalarRange ?? [0, 0],
    field: header.field ?? '',
    nPoints,
    nTriangles,
  }
}

import { percentileRange } from './ppVolume'

/**
 * The colour range a freshly cut slice should open on.
 *
 * NOT the header's `scalarRange`, which is the raw min and max: one cell
 * against a hot server, or one near-zero cell in a velocity field, stretches
 * the ramp so far that the whole cut renders as a single colour and the plane
 * looks empty. The legacy takes a 2–98% percentile of the slice's OWN scalars
 * (`_ppPercentileRange`, STL_generate.html:22943) and uses it for the default
 * clim, the 值域范围 boxes and the colorbar's 自动缩放 alike.
 *
 * Falls back to the header range when the slice carries no scalars — an empty
 * cut writes a zero-length array rather than omitting the section.
 */
export function sliceDisplayRange(payload: SlicePayload): [number, number] {
  if (!payload.values || payload.values.length === 0) return payload.scalarRange
  return percentileRange(payload.values, 0.02, 0.98) ?? payload.scalarRange
}

export async function fetchSlice(req: SliceRequest, session: PostprocessSession): Promise<SlicePayload> {
  const resp = await fetch('/api/pp/slice', {
    method: 'POST',
    headers: revisionHeaders(session, true),
    body: JSON.stringify(req),
  })
  if (!resp.ok) {
    let detail = String(resp.status)
    try {
      detail = ((await resp.json()) as { error?: string }).error ?? detail
    } catch {
      /* body was not JSON */
    }
    throw new Error(`/api/pp/slice -> ${detail}`)
  }
  assertRevisionHeader(resp, session)
  return bindNumericProduct(decodeSlice(await resp.arrayBuffer()),resp,'slice',session)
}


// ---------------------------------------------------------------------------
// Streamlines
// ---------------------------------------------------------------------------

export interface StreamlineRequest {
  center: [number, number, number]
  radius: number
  nSeeds: number
  /** 'speed' means |U|, which is the server's default. */
  field?: string
}

export interface StreamlineSet {
  /** One entry per polyline: its slice of `points` / `scalars`. */
  lines: { start: number; count: number }[]
  /** xyz interleaved, deinterleaved out of the wire's xyzs quads. */
  points: Float32Array
  /** One scalar per point. */
  scalars: Float32Array
  scalarRange: [number, number]
  field: string
}

/**
 * Decode a streamline payload.
 *
 * Same envelope as a slice — `uint32 headerLen | padded JSON | Float32 data` —
 * but the data is NOT three parallel arrays. Every point is a QUAD of
 * (x, y, z, scalar), and the polylines are consecutive runs whose lengths are
 * in the header. Reading it as separate position and scalar blocks produces
 * lines that wander through the room in a plausible-looking tangle.
 */
export function decodeStreamlines(buffer: ArrayBuffer): StreamlineSet {
  const view = new DataView(buffer)
  const headerLen = view.getUint32(0, true)
  const text = new TextDecoder().decode(new Uint8Array(buffer, 4, headerLen))
  const header = JSON.parse(text.replace(/\0+$/, '')) as {
    streamlines?: { nPoints: number }[]
    scalarRange?: [number, number]
    field?: string
  }
  const meta = header.streamlines ?? []
  const totalPoints = meta.reduce((n, l) => n + l.nPoints, 0)

  const quads = new Float32Array(buffer, 4 + headerLen, totalPoints * 4)
  const points = new Float32Array(totalPoints * 3)
  const scalars = new Float32Array(totalPoints)
  for (let i = 0; i < totalPoints; i++) {
    points[i * 3] = quads[i * 4] as number
    points[i * 3 + 1] = quads[i * 4 + 1] as number
    points[i * 3 + 2] = quads[i * 4 + 2] as number
    scalars[i] = quads[i * 4 + 3] as number
  }

  const lines: { start: number; count: number }[] = []
  let cursor = 0
  for (const l of meta) {
    lines.push({ start: cursor, count: l.nPoints })
    cursor += l.nPoints
  }

  return {
    lines,
    points,
    scalars,
    scalarRange: header.scalarRange ?? [0, 0],
    field: header.field ?? 'speed',
  }
}

/**
 * Seed a streamline set from a sphere.
 *
 * Deterministic: the server seeds from `np.random.default_rng(42)`, so the same
 * centre, radius and count return the same lines. That is what makes a captured
 * response a usable fixture rather than a one-off.
 */
export async function fetchStreamlines(req: StreamlineRequest, session: PostprocessSession): Promise<StreamlineSet> {
  const resp = await fetch('/api/pp/streamlines', {
    method: 'POST',
    headers: revisionHeaders(session, true),
    body: JSON.stringify(req),
  })
  if (!resp.ok) {
    let detail = String(resp.status)
    try {
      detail = ((await resp.json()) as { error?: string }).error ?? detail
    } catch {
      /* body was not JSON */
    }
    throw new Error(`/api/pp/streamlines -> ${detail}`)
  }
  assertRevisionHeader(resp, session)
  return bindNumericProduct(decodeStreamlines(await resp.arrayBuffer()),resp,'streamline',session)
}


// Volume rendering lives in its own module (the 3D texture and the ray-marching
// shader), re-exported here so the panel has one import for everything it
// fetches.
export { decodeVolume, fetchVolume, percentileRange, type VolumePayload } from './ppVolume'
