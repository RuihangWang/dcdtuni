import { PATCH_PALETTE } from '../geometry/patchGeometry'
import {
  setServerPatchProp,
  type ServerPatch,
  type ServerPatchKey,
} from '../geometry/serverPatches'

/**
 * The editor for ONE opening on a rack server's face.
 *
 * Kept apart from the face panel because two panels need it: the face
 * inspector, which lists every patch on the face, and the generic patch panel,
 * which the tree's `serverPatch` node routes into. Duplicating it would be two
 * places to keep a clamp in step.
 *
 * Geometry is offered as PERCENTAGES because a server patch lives in the face's
 * own 0-1 space — `x/y/w/h` for a rectangle, `cx/cy/r` for a circle. The same
 * patch has to mean the same fraction of a 1U face and of a 4U one, so metres
 * would be the wrong unit here even though every other patch panel uses them.
 *
 * Ports `_renderServerPatchEditor` and `_srvSliderRow` (STL_generate.html:9743,
 * 9767).
 */

export interface ServerPatchEditorProps {
  uid: string
  equipmentId: string
  faceId: string
  patch: ServerPatch
}

/**
 * A 0-100% slider with a keyboard-editable box beside it.
 *
 * Both controls write the SAME property, and the clamp may move the value: a
 * typed 150% becomes the largest that fits. Rendering from the committed patch
 * rather than from local state is what makes that visible — the box snaps to
 * what was accepted instead of leaving a number on screen the project never
 * took. That is the legacy `syncServerPatchInspectorInputs`
 * (STL_generate.html:17193), which exists for the same reason.
 */
function SliderRow({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (fraction: number) => void
}) {
  const pct = Math.round(value * 100)
  const inputId = `srv-sl-${label.replace(/[^a-zA-Z0-9]/g, '')}`
  const commit = (raw: number) => {
    const n = Number.isFinite(raw) ? Math.max(0, Math.min(100, Math.round(raw))) : 0
    onChange(n / 100)
  }
  return (
    <div className="prop-row" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'nowrap' }}>
      <label style={{ flex: '0 0 auto', minWidth: 46, fontSize: 11, whiteSpace: 'nowrap' }}>
        {label}
      </label>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={pct}
        style={{ flex: '1 1 auto', minWidth: 28, margin: 0 }}
        onChange={(e) => onChange(Number(e.target.value) / 100)}
      />
      <input
        id={`${inputId}-val`}
        type="number"
        min={0}
        max={100}
        step={1}
        value={pct}
        title={`${label} (0–100%)`}
        style={{ flex: '0 0 auto', width: 52, minWidth: 52, fontSize: 11, textAlign: 'right', padding: '2px 4px' }}
        onChange={(e) => commit(parseFloat(e.target.value))}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            e.currentTarget.blur()
          }
        }}
      />
      <span style={{ flex: '0 0 auto', fontSize: 11, color: 'var(--text-dim)' }}>%</span>
    </div>
  )
}

export function ServerPatchEditor({ uid, equipmentId, faceId, patch }: ServerPatchEditorProps) {
  const set = (key: ServerPatchKey, value: string | number) =>
    setServerPatchProp(uid, equipmentId, faceId, patch.id, key, value)

  return (
    <>
      <div className="prop-row">
        <label htmlFor={`srv-patch-type-${patch.id}`}>Type</label>
        <select
          id={`srv-patch-type-${patch.id}`}
          style={{ fontSize: 11 }}
          value={patch.type === 'circle' ? 'circle' : 'rect'}
          onChange={(e) => set('type', e.target.value)}
        >
          <option value="rect">Rect</option>
          <option value="circle">Circle</option>
        </select>
      </div>

      {/* The same six swatches the patch panel offers, so a server opening and
          a floor tile can be given the same colour and read as one group. */}
      <div className="prop-row" style={{ marginTop: 4 }}>
        <label>Color</label>
        <div className="color-swatches" style={{ display: 'flex', gap: 4, flex: 1 }}>
          {PATCH_PALETTE.map((color) => (
            <div
              key={color}
              className={`color-swatch${(patch.color ?? '#00cc66') === color ? ' active' : ''}`}
              style={{ background: color }}
              onClick={(e) => {
                e.stopPropagation()
                set('color', color)
              }}
            />
          ))}
        </div>
      </div>

      {patch.type === 'circle' ? (
        <>
          <SliderRow label="Center X" value={patch.cx ?? 0.5} onChange={(v) => set('cx', v)} />
          <SliderRow label="Center Y" value={patch.cy ?? 0.5} onChange={(v) => set('cy', v)} />
          <SliderRow label="Radius" value={patch.r ?? 0.3} onChange={(v) => set('r', v)} />
        </>
      ) : (
        <>
          <SliderRow label="X Pos" value={patch.x ?? 0} onChange={(v) => set('x', v)} />
          <SliderRow label="Y Pos" value={patch.y ?? 0} onChange={(v) => set('y', v)} />
          <SliderRow label="Width" value={patch.w ?? 0.5} onChange={(v) => set('w', v)} />
          <SliderRow label="Height" value={patch.h ?? 0.5} onChange={(v) => set('h', v)} />
        </>
      )}
    </>
  )
}
