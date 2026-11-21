/**
 * A picture on a face.
 *
 * An electrical cabinet is a wall boundary to the solver and a grey box to the
 * viewer; a photo of its door (pilot lamps, start/stop buttons) on the face is
 * what makes the room readable. The image is stored on the face record —
 * `textureUrl` for a served asset such as /textures/electric-box/panel_lamps.jpg,
 * `textureDataUrl` for a picture loaded from disk and embedded in the .cfdproj —
 * and the shared geometry builder maps whichever is set over the face.
 *
 * Only a face record can carry one (box, ACU, CDU faces and the primary face of
 * a wall, baffle or floor). A rack's faces are door entries; a custom mesh has
 * regions, not faces.
 */

import type { Construction } from '../schema/cfdproj'
import { updateFace } from './patchPhysics'

/** Longest side an embedded picture is scaled down to, in pixels. */
export const FACE_TEXTURE_MAX_PX = 1024

/** Kinds whose faces can carry an image. */
export function faceTextureAllowed(model: Construction | null | undefined): boolean {
  const kind = String(model?.kind ?? '')
  return ['box', 'acu', 'cdu', 'wall', 'baffle', 'floor'].includes(kind)
}

export function faceTextureSource(model: Construction, faceId: string): string | null {
  const faces = (model.geometry as { faces?: unknown } | undefined)?.faces
  if (!faces || Array.isArray(faces) || typeof faces !== 'object') return null
  const face = (faces as Record<string, { textureUrl?: unknown; textureDataUrl?: unknown }>)[faceId]
  const src = face?.textureDataUrl ?? face?.textureUrl
  return typeof src === 'string' && src ? src : null
}

/** Put a picture on the face: a data URL from disk, or a served asset path. */
export function setFaceTexture(uid: string, faceId: string, src: string) {
  const isData = src.startsWith('data:')
  updateFace(uid, faceId, isData ? 'textureDataUrl' : 'textureUrl', src)
  updateFace(uid, faceId, isData ? 'textureUrl' : 'textureDataUrl', undefined)
}

export function clearFaceTexture(uid: string, faceId: string) {
  updateFace(uid, faceId, 'textureDataUrl', undefined)
  updateFace(uid, faceId, 'textureUrl', undefined)
}

/**
 * Downscale an image (a data URL) so the .cfdproj does not swallow a 12 MP
 * photo: the longest side is capped at FACE_TEXTURE_MAX_PX and the result is
 * re-encoded as JPEG (PNG when the source had transparency).
 */
export function shrinkImageDataUrl(dataUrl: string, maxPx = FACE_TEXTURE_MAX_PX): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const scale = Math.min(1, maxPx / Math.max(img.width, img.height))
      if (scale >= 1 && dataUrl.length < 1.5e6) {
        resolve(dataUrl)
        return
      }
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(img.width * scale))
      canvas.height = Math.max(1, Math.round(img.height * scale))
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        resolve(dataUrl)
        return
      }
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      const png = dataUrl.startsWith('data:image/png')
      resolve(png ? canvas.toDataURL('image/png') : canvas.toDataURL('image/jpeg', 0.88))
    }
    img.onerror = () => reject(new Error('not an image'))
    img.src = dataUrl
  })
}

/** Read a picked File as a data URL. */
export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsDataURL(file)
  })
}

/** Fetch a server-side image through the binary read endpoint, as a data URL. */
export async function remoteImageToDataUrl(path: string): Promise<string> {
  const resp = await fetch('/api/read-file-binary?path=' + encodeURIComponent(path))
  const body = (await resp.json()) as { ok: boolean; mime?: string; base64?: string; error?: string }
  if (!body.ok || !body.base64) throw new Error(body.error ?? String(resp.status))
  const mime = body.mime && body.mime.startsWith('image/') ? body.mime : 'image/jpeg'
  return `data:${mime};base64,${body.base64}`
}
