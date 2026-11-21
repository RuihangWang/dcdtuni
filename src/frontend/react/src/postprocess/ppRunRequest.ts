/** A run another tab asks post-processing to open (once). */

let requested: string | null = null

export function requestPostprocessRun(path: string): void {
  requested = path
}

export function takeRequestedPostprocessRun(): string | null {
  const path = requested
  requested = null
  return path
}
