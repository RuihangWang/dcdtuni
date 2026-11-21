import { useEffect, useRef, useState } from 'react'
import type { Construction } from '../schema/cfdproj'
import { setShowAnchorMarker, showsAnchorMarker } from '../geometry/anchorMarkers'
import {
  clampOpacityPercent,
  commitModelOpacity,
  modelOpacityPercent,
  previewModelOpacity,
} from '../geometry/modelOpacity'
import { useTranslation } from '../useTranslation'

/**
 * The inspector's two display controls: the opacity slider and the anchor ball.
 *
 * The slider is the reason this is not a plain React input. It has TWO events
 * with different meanings — `input` previews and `change` commits — and React's
 * `onChange` is the `input` event, so a component that used it would commit
 * sixty times a second and there would be no drag to undo in one press. Both
 * listeners are therefore attached natively, exactly as the legacy markup binds
 * `oninput` and `onchange` (STL_generate.html:8445).
 *
 * The element stays UNCONTROLLED for the same reason. Its value is pushed back
 * imperatively when the committed value changes underneath it — an undo, or a
 * different model selected — which is the one case an uncontrolled input cannot
 * notice on its own.
 */

function ModelOpacitySlider({ model }: { model: Construction }) {
  const committed = modelOpacityPercent(model)
  const uid = model.uid
  const ref = useRef<HTMLInputElement | null>(null)
  // The badge only. Kept in state rather than written to the DOM so the number
  // beside the slider is React's, not a second writer of the same node.
  const [percent, setPercent] = useState(committed)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onInput = () => {
      const value = clampOpacityPercent(Number(el.value))
      setPercent(value)
      previewModelOpacity(uid, value)
    }
    const onChange = () => {
      commitModelOpacity(uid, clampOpacityPercent(Number(el.value)))
    }
    el.addEventListener('input', onInput)
    el.addEventListener('change', onChange)
    return () => {
      el.removeEventListener('input', onInput)
      el.removeEventListener('change', onChange)
    }
  }, [uid])

  // A drag mutates the project in place and never re-renders, so this only
  // fires when the value changed for another reason: an undo, a redo, or a
  // different model in the panel.
  useEffect(() => {
    setPercent(committed)
    if (ref.current) ref.current.value = String(committed)
  }, [committed, uid])

  return (
    <>
      <input
        ref={ref}
        id="model-opacity-slider"
        className="frozen-ok"
        type="range"
        min={0}
        max={100}
        step={1}
        defaultValue={committed}
      />
      <span className="badge" id="model-opacity-badge">
        {percent}%
      </span>
    </>
  )
}

/** The show/hide checkbox for the anchor ball. */
export function AnchorMarkerRow({ model }: { model: Construction }) {
  const { t } = useTranslation()
  return (
    // Display-only: stays live while the inspector is frozen off t = 0.
    <div className="prop-row frozen-ok">
      <label htmlFor="model-anchor-marker">{t('inspector.show_anchor')}</label>
      <input
        type="checkbox"
        id="model-anchor-marker"
        checked={showsAnchorMarker(model)}
        onChange={(e) => setShowAnchorMarker(model.uid, e.target.checked)}
      />
    </div>
  )
}

export function ModelDisplayGroup({
  model,
  /**
   * Whether to carry the anchor checkbox too.
   *
   * Every inspector panel now gets it from the transform block at its top
   * (TransformSection), so no caller passes this; it stays for a panel that
   * shows a model without that block.
   */
  withAnchor = false,
}: {
  model: Construction
  withAnchor?: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="prop-group">
      <div className="prop-title">{t('common.display')}</div>
      <div className="prop-row">
        <label htmlFor="model-opacity-slider">{t('label.model_opacity')}</label>
        <ModelOpacitySlider model={model} />
      </div>
      {withAnchor && <AnchorMarkerRow model={model} />}
    </div>
  )
}
