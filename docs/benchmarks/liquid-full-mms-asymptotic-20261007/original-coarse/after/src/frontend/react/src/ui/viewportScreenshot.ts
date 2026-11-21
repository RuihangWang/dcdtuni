/**
 * Capture the renderer currently displayed in the viewport.
 *
 * OV is mounted over a retained Three canvas. A mounted OV view must therefore
 * either supply a decoded frame or fail; silently using Three would save a
 * different scene/camera from the one the user is looking at.
 */
export function captureViewportPng(
  doc: Document,
  renderThree?: () => HTMLCanvasElement | null,
): string {
  const ov = doc.querySelector<HTMLElement>('.ov-render-viewport')
  if (ov) {
    const video = ov.querySelector<HTMLVideoElement>('video')
    if (!video) throw new Error('Omniverse screenshot: viewport video is unavailable')
    const width = video.videoWidth
    const height = video.videoHeight
    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || width <= 0 || height <= 0) {
      throw new Error('Omniverse screenshot: no decoded current frame; wait for the video stream')
    }
    const canvas = doc.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Omniverse screenshot: 2D capture context is unavailable')
    try {
      context.drawImage(video, 0, 0, width, height)
      return canvas.toDataURL('image/png')
    } catch (error) {
      throw new Error('Omniverse screenshot: ' + (error instanceof Error ? error.message : String(error)))
    }
  }

  // Without preserveDrawingBuffer, Three's canvas must be read immediately
  // after the existing hook has rendered it; keep that call before toDataURL.
  const canvas = renderThree?.() ?? doc.querySelector<HTMLCanvasElement>('#center-view canvas')
  if (!canvas) throw new Error('no canvas')
  return canvas.toDataURL('image/png')
}
