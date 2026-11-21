import { useEffect, useRef, useState } from 'react'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { clampOpacityPercent } from '../geometry/modelOpacity'
import { requestSelect } from '../selectionBus'
import { useTranslation } from '../useTranslation'
import {
  commitWallRegionOpacity,
  previewWallRegionOpacity,
  setWallRegionName,
  wallRegionName,
  wallRegionOpacityPercent,
  wallRegionSolidName,
} from '../geometry/wallRegion'
import { RemainingAreaBc } from './PatchInspector'
import { SeedInspector } from './SeedInspector'

/**
 * The panel for one wall region — the sheet left over once the patches are cut
 * out of a face.
 *
 * The tree has offered these nodes since `sheetChildren`
 * (treeNodes.ts) started listing them, but the inspector dispatched only
 * patches, faces and equipment, so selecting a region showed the whole model's
 * panel instead: the wrong object, silently, which is exactly the failure the
 * patch panels exist to prevent.
 *
 * Ported from `renderWallRegionInspector` (STL_generate.html:10602).
 */

export interface WallRegionInspectorProps {
  project: CfdProject
  model: Construction
  bucket: string
  faceId: string
  index: number
}

export function WallRegionInspector({
  project,
  model,
  bucket,
  faceId,
  index,
}: WallRegionInspectorProps) {
  const { t } = useTranslation()
  const name = wallRegionName(model, faceId, index)
  const solidName = wallRegionSolidName(model, faceId, index)
  const faces = (model.geometry as { faces?: Record<string, { displayName?: string; label?: string }> } | undefined)
    ?.faces
  const faceName = faces?.[faceId]?.displayName ?? faces?.[faceId]?.label ?? faceId

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: 'var(--highlight)' }}>
        <div className="prop-title" style={{ color: 'var(--highlight)' }}>
          {t('inspector.wall_region_title')}
        </div>
        <div className="prop-row">
          <b id="wall-region-label">
            {faceName} / {t('label.wall_region_zone', { index: index + 1 })}
          </b>
        </div>
        <div className="prop-hint">{t('hint.wall_region_desc')}</div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
        <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
          Patch Naming
        </div>
        <div className="prop-row">
          <label htmlFor="wall-region-name">Patch Name</label>
          <input
            id="wall-region-name"
            type="text"
            // Keyed on the region so switching selection reloads the box; an
            // uncontrolled input would otherwise keep the previous region's
            // name and write it onto the new one at the next blur.
            key={`${model.uid}/${faceId}/${index}`}
            defaultValue={name}
            onBlur={(e) => {
              if (e.target.value !== name) setWallRegionName(model.uid, faceId, index, e.target.value)
            }}
          />
        </div>
        <div className="prop-row">
          <label className="dim">Final STL Solid Name</label>
        </div>
        <div className="patch-final-name" id="wall-region-final-name">
          {solidName}
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: 'var(--accent3)' }}>
        <div className="prop-title" style={{ color: 'var(--accent3)' }}>
          {t('common.display')}
        </div>
        <div className="prop-row">
          <label htmlFor="wall-region-opacity">{t('label.opacity')}</label>
          <WallRegionOpacitySlider model={model} faceId={faceId} index={index} />
        </div>
      </div>

      {/* Floor and baffle only, and the same control the face panel carries:
          the region IS the remaining area, so its boundary condition is the
          model's default one. */}
      <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
        <div className="prop-title" style={{ color: '#16825d' }}>
          {t('inspector.region_cfd')}
        </div>
        <RemainingAreaBc model={model} />
      </div>

      {/* The shared seed panel, not a second copy: `cooling.wallRegionSeedConfigs`
          and `cooling.defaultSeedConfigs` are keyed by the region's SOLID name,
          and two writers of that store would file an override under a key the
          other never reads back. It renders nothing for a plain `wall` region,
          which has one side and so no seed pair. */}
      <SeedInspector
        project={project}
        model={model}
        bucket={bucket}
        faceId={faceId}
        wallIndex={index}
      />

      {/* The sheet kinds have no face node in this tree — their patches and
          regions hang straight off the model row — so the way back up from a
          region is the model itself. */}
      <button className="btn frozen-ok" id="wall-region-back" onClick={() => requestSelect(model.uid)}>
        {t('btn.back_to_face')}
      </button>
    </>
  )
}

/**
 * The region's opacity slider.
 *
 * Native `input`/`change` listeners rather than React's `onChange`, for the
 * reason `ModelOpacitySlider` gives: React's `onChange` IS the `input` event,
 * so a controlled slider would commit on every pointer move and one drag would
 * take eighty presses of Ctrl+Z to undo.
 */
function WallRegionOpacitySlider({
  model,
  faceId,
  index,
}: {
  model: Construction
  faceId: string
  index: number
}) {
  const committed = wallRegionOpacityPercent(model, faceId, index)
  const uid = model.uid
  const ref = useRef<HTMLInputElement | null>(null)
  const [percent, setPercent] = useState(committed)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onInput = () => {
      const value = clampOpacityPercent(Number(el.value))
      setPercent(value)
      previewWallRegionOpacity(uid, faceId, index, value)
    }
    const onChange = () => {
      commitWallRegionOpacity(uid, faceId, index, clampOpacityPercent(Number(el.value)))
    }
    el.addEventListener('input', onInput)
    el.addEventListener('change', onChange)
    return () => {
      el.removeEventListener('input', onInput)
      el.removeEventListener('change', onChange)
    }
  }, [uid, faceId, index])

  // A drag mutates in place and never re-renders, so this only fires when the
  // value changed for another reason: an undo, or a different region selected.
  useEffect(() => {
    setPercent(committed)
    if (ref.current) ref.current.value = String(committed)
  }, [committed, uid, faceId, index])

  return (
    <>
      <input
        ref={ref}
        id="wall-region-opacity"
        className="frozen-ok"
        type="range"
        min={0}
        max={100}
        step={1}
        defaultValue={committed}
      />
      <span className="badge" id="wall-region-opacity-badge">
        {percent}%
      </span>
    </>
  )
}
