/** Identity of the one postprocess snapshot currently loaded by the server. */
export interface PostprocessSession {
  runId: string
  timeValue: number | null
  revision: string
}

export function assertSessionIdentity(
  actual: Partial<PostprocessSession>,
  expected: PostprocessSession,
): void {
  if (actual.runId !== expected.runId || actual.timeValue !== expected.timeValue ||
      actual.revision !== expected.revision) {
    throw new Error('Postprocess revision changed; reopen the selected run and time')
  }
}

export function revisionHeaders(session: PostprocessSession, json = false): Headers {
  const headers = new Headers()
  headers.set('X-PP-Revision', session.revision)
  if (json) headers.set('Content-Type', 'application/json')
  return headers
}

export function assertRevisionHeader(
  response: Pick<Response, 'headers'>,
  session: PostprocessSession,
): void {
  if (response.headers.get('X-PP-Revision') !== session.revision) {
    throw new Error('Postprocess revision response mismatch; reopen the selected run and time')
  }
}
