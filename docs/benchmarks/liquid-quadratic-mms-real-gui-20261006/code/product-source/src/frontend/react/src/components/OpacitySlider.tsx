import { useEffect, useRef, useState } from 'react'
import { clampOpacityPercent } from '../geometry/modelOpacity'

/**
 * A 0-100 opacity slider with a live preview and a separate commit.
 *
 * The reason this is not a plain React input: the element has TWO events with
 * different meanings — `input` previews and `change` ends the drag — and
 * React's `onChange` IS the `input` event, so a controlled component would
 * commit sixty times a second and there would be no drag left to undo in one
 * press. Both listeners are attached natively, exactly as the legacy markup
 * binds `oninput` and `onchange` (STL_generate.html:8445 for one model,
 * :11012 for a selection).
 *
 * The element stays UNCONTROLLED for the same reason. Its value is pushed back
 * imperatively when the committed number changes underneath it — an undo, or a
 * different model in the panel — which is the one case an uncontrolled input
 * cannot notice on its own.
 */
export function OpacitySlider({
  id,
  badgeId,
  committed,
  onPreview,
  onCommit,
}: {
  id: string
  badgeId: string
  /** The value the project holds, 0-100. */
  committed: number
  onPreview: (percent: number) => void
  onCommit: (percent: number) => void
}) {
  const ref = useRef<HTMLInputElement | null>(null)
  // The badge only. Kept in state rather than written to the DOM so the number
  // beside the slider is React's, not a second writer of the same node.
  const [percent, setPercent] = useState(committed)
  // Held in refs so the listeners are attached ONCE. A dependency on callbacks
  // the parent rebuilds every render would tear them down and re-add them
  // mid-drag, and the drag would lose its `change`.
  const preview = useRef(onPreview)
  const commit = useRef(onCommit)
  preview.current = onPreview
  commit.current = onCommit

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onInput = () => {
      const value = clampOpacityPercent(Number(el.value))
      setPercent(value)
      preview.current(value)
    }
    const onChange = () => commit.current(clampOpacityPercent(Number(el.value)))
    el.addEventListener('input', onInput)
    el.addEventListener('change', onChange)
    return () => {
      el.removeEventListener('input', onInput)
      el.removeEventListener('change', onChange)
    }
  }, [])

  // A drag mutates the project in place and never re-renders, so this only
  // fires when the value changed for another reason: an undo, a redo, or a
  // different selection in the panel.
  useEffect(() => {
    setPercent(committed)
    if (ref.current) ref.current.value = String(committed)
  }, [committed])

  return (
    <>
      <input ref={ref} id={id} className="frozen-ok" type="range" min={0} max={100} step={1} defaultValue={committed} />
      <span className="badge" id={badgeId}>
        {percent}%
      </span>
    </>
  )
}
