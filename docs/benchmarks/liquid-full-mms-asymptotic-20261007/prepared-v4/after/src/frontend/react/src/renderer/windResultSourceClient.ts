/** The only production entry to verified result provenance is the backend. */
import { assertSessionIdentity, revisionHeaders, type PostprocessSession } from '../postprocess/ppRevision'
import type { VerifiedAirResultDescriptor, VerifiedMeshPreviewDescriptor } from './windResultProducer'

async function bodyOrError(response: Response): Promise<Record<string, unknown>> {
  const body = await response.json() as Record<string, unknown>
  if (response.status === 422 && body.code === 'render_source_unavailable') return body
  if (!response.ok) throw new Error(`Renderer source: ${String(body.code ?? body.error ?? response.status)}`)
  return body
}

export async function fetchVerifiedAirResultSource(
  session: PostprocessSession, signal?: AbortSignal,
): Promise<VerifiedAirResultDescriptor | null> {
  const response = await fetch('/api/pp/render-source', {
    headers: revisionHeaders(session), cache: 'no-store', signal,
  })
  const body = await bodyOrError(response)
  if (response.status === 422) return null
  const descriptor = body as unknown as VerifiedAirResultDescriptor
  if (descriptor.schema !== 'dcdtuni.verified-air-result-source/v1') throw new Error('Unverified air result source schema')
  assertSessionIdentity(descriptor.postprocess, session)
  return descriptor
}

export async function fetchVerifiedMeshPreviewSource(
  meshSnapshotKey: string, signal?: AbortSignal,
): Promise<VerifiedMeshPreviewDescriptor | null> {
  if (!/^[0-9a-f]{64}$/.test(meshSnapshotKey)) throw new Error('Invalid preview mesh snapshot key')
  const response = await fetch(`/api/mesh-preview/render-source?meshSnapshotKey=${meshSnapshotKey}`, {
    cache: 'no-store', signal,
  })
  const body = await bodyOrError(response)
  if (response.status === 422) return null
  const descriptor = body as unknown as VerifiedMeshPreviewDescriptor
  if (descriptor.schema !== 'dcdtuni.verified-mesh-preview-source/v1' ||
      descriptor.meshSnapshotKey !== meshSnapshotKey || descriptor.source.run !== null) {
    throw new Error('Verified mesh preview source does not match current mesh')
  }
  return descriptor
}
