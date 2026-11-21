import { useRef, type CSSProperties } from 'react'
import { Glyph } from './Icon'
import { RENDER_LAYERS, reorderChips } from '../ui/renderOrder'
import { useTranslation } from '../useTranslation'

/**
 * The drag-to-reorder layer chips, shared by both patch viewers.
 *
 * `_roBuildPanel` (STL_generate.html:22378) builds the same list into whichever
 * container id it is given — `pp-render-order-panel` on the post-processing tab,
 * `mp-render-order-panel` on the mesh tab. The ids are what the parity harness
 * diffs, so they are a prop rather than something this component invents.
 *
 * The chip at the TOP draws on top. Which is why the drop target is decided by
 * the pointer's position relative to the MIDPOINT of the chip being dropped on:
 * a drop on the upper half means "above this one", and without that test every
 * drop lands after the target and a chip can never reach the first row.
 */

export interface RenderOrderChipsProps {
  /** `pp-render-order-panel` or `mp-render-order-panel`. */
  panelId: string
  order: string[]
  onReorder(next: string[]): void
}

export function RenderOrderChips({ panelId, order, onReorder }: RenderOrderChipsProps) {
  const { t } = useTranslation()
  const dragKey = useRef<string | null>(null)

  return (
    <div id={panelId}>
      <div className="pp-section-label">⇅ {t('pp.render_order')}</div>
      <div id={`${panelId}-list`}>
        {order.map((key) => {
          const layer = RENDER_LAYERS[key]
          if (!layer) return null
          return (
            <div
              key={key}
              className="ro-chip"
              data-ro-key={key}
              draggable
              onDragStart={(e) => {
                dragKey.current = key
                e.dataTransfer.effectAllowed = 'move'
                e.dataTransfer.setData('text/plain', key)
              }}
              onDragOver={(e) => {
                e.preventDefault()
                e.dataTransfer.dropEffect = 'move'
              }}
              onDrop={(e) => {
                e.preventDefault()
                const src = dragKey.current ?? e.dataTransfer.getData('text/plain')
                if (!src) return
                const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                const before = e.clientY < rect.top + rect.height / 2
                const next = reorderChips(order, src, key, before)
                dragKey.current = null
                if (next.join() !== order.join()) onReorder(next)
              }}
              style={{ '--ro-color': layer.color } as CSSProperties}
            >
              <span className="ro-icon"><Glyph icon={layer.icon} /></span>
              <span className="ro-label">{layer.label}</span>
              <span className="ro-grip">⋮⋮</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
