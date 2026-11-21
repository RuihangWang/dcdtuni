import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import * as THREE from 'three'
import { getViewportHandle } from '../viewportBus'
import { getExtremeTags, setExtremeTags, subscribeExtremeTags } from '../postprocess/ppExtremes'
import { Icon } from './Icon'

/**
 * The max / min tags over the viewport: a ring on the point, a leader line, and a tag the user can drag
 * anywhere or close. Positions follow the camera every frame; a tag keeps its drag offset until extremes are
 * switched off.
 */

interface Tag {
  key: string
  which: 'max' | 'min'
  text: string
  at: THREE.Vector3
}

const DEFAULT_OFFSET: Record<string, [number, number]> = {
  surface: [28, -44],
  volume: [28, 30],
}

function defaultOffset(id: string, which: 'max' | 'min'): [number, number] {
  return DEFAULT_OFFSET[id] ?? [28, which === 'max' ? -14 : 8]
}

export function ExtremeTags() {
  const markers = useSyncExternalStore(subscribeExtremeTags, getExtremeTags)
  const [closed, setClosed] = useState<ReadonlySet<string>>(new Set())
  const offsets = useRef(new Map<string, [number, number]>())
  const nodes = useRef(new Map<string, { tag: HTMLDivElement | null; line: SVGLineElement | null; ring: SVGCircleElement | null }>())

  useEffect(() => () => setExtremeTags([]), [])
  useEffect(() => {
    if (!markers.length) {
      offsets.current.clear()
      setClosed(new Set())
    }
  }, [markers.length])

  const tags: Tag[] = markers.flatMap((m) => (['max', 'min'] as const).map((which) => ({
    key: `${m.id}:${which}`,
    which,
    text: which === 'max' ? m.maxLabel : m.minLabel,
    at: new THREE.Vector3(...m.pair[which].position),
  }))).filter((tag) => !closed.has(tag.key))

  useEffect(() => {
    if (!tags.length) return
    let frame = 0
    const v = new THREE.Vector3()
    const tick = () => {
      frame = requestAnimationFrame(tick)
      const handle = getViewportHandle()
      const canvas = handle?.controls.domElement as HTMLElement | undefined
      if (!handle || !canvas) return
      const rect = canvas.getBoundingClientRect()
      for (const tag of tags) {
        const n = nodes.current.get(tag.key)
        if (!n?.tag || !n.line || !n.ring) continue
        v.copy(tag.at).project(handle.camera)
        const x = rect.left + (v.x + 1) / 2 * rect.width
        const y = rect.top + (1 - v.y) / 2 * rect.height
        const visible = v.z < 1 && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
        const [dx, dy] = offsets.current.get(tag.key) ?? defaultOffset(tag.key.split(':')[0]!, tag.which)
        const tx = x + dx, ty = y + dy
        n.tag.style.display = n.line.style.display = n.ring.style.display = visible ? '' : 'none'
        n.tag.style.transform = `translate(${tx}px, ${ty}px) translateY(-50%)`
        n.ring.setAttribute('cx', String(x)); n.ring.setAttribute('cy', String(y))
        // the line ends at the tag's near edge
        const box = n.tag.getBoundingClientRect()
        const ex = Math.min(Math.max(x, tx), tx + box.width)
        n.line.setAttribute('x1', String(x)); n.line.setAttribute('y1', String(y))
        n.line.setAttribute('x2', String(ex)); n.line.setAttribute('y2', String(ty))
      }
    }
    tick()
    return () => cancelAnimationFrame(frame)
  })

  function drag(key: string, which: 'max' | 'min', e: React.PointerEvent<HTMLDivElement>) {
    if ((e.target as HTMLElement).closest('.extreme-tag-close')) return
    e.preventDefault()
    const start = offsets.current.get(key) ?? defaultOffset(key.split(':')[0]!, which)
    const x0 = e.clientX, y0 = e.clientY
    const el = e.currentTarget
    el.classList.add('dragging')
    const move = (ev: PointerEvent) => offsets.current.set(key, [start[0] + ev.clientX - x0, start[1] + ev.clientY - y0])
    const up = () => {
      el.classList.remove('dragging')
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
  }

  if (!tags.length) return null
  const slot = (key: string) => {
    let n = nodes.current.get(key)
    if (!n) nodes.current.set(key, n = { tag: null, line: null, ring: null })
    return n
  }
  return createPortal(
    <div className="extreme-tags" id="pp-extreme-tags">
      <svg className="extreme-tag-lines">
        {tags.map((tag) => (
          <g key={tag.key} className={`extreme-tag-${tag.which}`}>
            <line ref={(el) => { slot(tag.key).line = el }} />
            <circle ref={(el) => { slot(tag.key).ring = el }} r={6} />
          </g>
        ))}
      </svg>
      {tags.map((tag) => (
        <div key={tag.key} ref={(el) => { slot(tag.key).tag = el }} data-extreme-tag={tag.key}
          className={`extreme-tag extreme-tag-${tag.which}`} onPointerDown={(e) => drag(tag.key, tag.which, e)}>
          <Icon name={tag.which === 'max' ? 'arrowUp' : 'arrowDown'} size={12} />
          <span>{tag.text}</span>
          <button type="button" className="extreme-tag-close" aria-label="close"
            onClick={() => setClosed((prev) => new Set(prev).add(tag.key))}>
            <Icon name="close" size={11} />
          </button>
        </div>
      ))}
    </div>,
    document.body,
  )
}
