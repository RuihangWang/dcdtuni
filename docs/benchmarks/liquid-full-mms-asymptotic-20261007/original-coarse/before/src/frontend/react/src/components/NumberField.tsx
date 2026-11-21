import { useEffect, useRef, useState } from 'react'

/**
 * A numeric input that commits on blur or Enter, not on every keystroke.
 *
 * Committing per keystroke would push one undo checkpoint per character and
 * would fight the user mid-edit: typing "1.25" passes through "1.", which parses
 * to 1 and would snap the model back. So the field holds a string while it is
 * being edited and only writes a number when the edit ends.
 *
 * It re-syncs from `value` whenever the model changes underneath it (an undo, a
 * different selection), but not while it has focus — that would overwrite what
 * the user is typing.
 */

export interface NumberFieldProps {
  /**
   * `null` means "not set", and it renders as an EMPTY field, not as 0.
   *
   * Several mesh settings are optional and the runner reads their absence as
   * "derive it" — `firstCellSize` most visibly. Showing 0 there claims the user
   * asked for a zero-metre first cell, and committing that 0 back turns an
   * automatic setting into an explicit one behind their back.
   */
  value: number | null
  /**
   * Take the committed number.
   *
   * Returning `false` means REFUSED — the box puts the model's own value back,
   * so a rejected edit does not leave the panel showing a number the document
   * does not hold. The bounds refusal is the case that needs it: typing a
   * position below `yMin` leaves the model where it was, and without this the
   * box read −500 beside a model standing at 0.
   */
  onCommit: (value: number) => void | boolean
  step?: number
  min?: number
  max?: number
  disabled?: boolean
  /**
   * Called when the user CLEARS the field. Providing it is what makes the
   * field clearable at all; without it an empty box redisplays the old value.
   *
   * Separate from `onCommit` rather than widening it to `number | null`,
   * because every other caller would then have to handle a null it can never
   * receive — and the compiler would stop catching the ones that should.
   */
  onClear?: () => void
  placeholder?: string
  /**
   * DOM id for the input. Carry the legacy editor's id wherever one exists:
   * the parity sweep addresses controls by id, so a field without its legacy
   * id cannot be compared against its HTML counterpart automatically.
   */
  id?: string
  /** Accessible name for compact controls whose visible label uses notation. */
  ariaLabel?: string
  /** Help text associated with this control. */
  ariaDescribedBy?: string
  /**
   * A class on the input itself, for the few boxes the shared stylesheet sizes
   * by class rather than by container — `mesh-lvl-input`, say.
   */
  className?: string
  /**
   * Fixed decimal places for DISPLAY only.
   *
   * The colour-limit boxes show `15.00 .. 37.73` in the legacy editor while the
   * shader keeps the field's full 37.7258 — the rounding is a label, not a
   * value. Committing the rounded number instead would move the colour scale
   * every time the field was focused and left.
   */
  decimals?: number
  /**
   * Width override, for a row that also carries a slider.
   *
   * The default 110px input plus a label leaves a slider a few pixels wide.
   * The legacy narrows the box to 70px in exactly those rows.
   */
  width?: number | string
}

export function display(value: number | null, decimals?: number): string {
  if (value === null) return ''
  if (!Number.isFinite(value)) return ''
  if (typeof decimals === 'number') return value.toFixed(decimals)
  // 4 decimals is millimetre resolution on a metre-scale model, and drops the
  // float dust a stored 3.6000000000000005 would otherwise show.
  //
  // But it is resolution relative to 1, not relative to the value, so it
  // annihilates anything small: a coolant viscosity of 6.53e-4 would render as
  // 7e-4 and a Newton tolerance of 1e-6 as 0. The field COMMITS what it
  // displays, so that rounding would be written straight back into the project.
  // Keep the 4-decimal form only where it is a faithful rendering of the value.
  const fixed = Number(value.toFixed(4))
  if (value === 0 || Math.abs(fixed - value) <= Math.abs(value) * 1e-4) {
    return String(fixed)
  }
  // The 4-decimal form moved the value by more than 0.01% — it is destroying
  // information, not dropping dust. Fall back to significant digits, which
  // holds at any magnitude.
  return String(Number(value.toPrecision(6)))
}

export function NumberField({
  value,
  onCommit,
  step = 0.1,
  min,
  max,
  disabled,
  id,
  ariaLabel,
  ariaDescribedBy,
  className,
  onClear,
  placeholder,
  decimals,
  width,
}: NumberFieldProps) {
  const [text, setText] = useState(() => display(value, decimals))
  const [editing, setEditing] = useState(false)
  const cancelled = useRef(false)
  const edited = useRef(false)

  useEffect(() => {
    if (!editing) setText(display(value, decimals))
  }, [value, editing, decimals])

  function commit(raw: string) {
    // Focusing a rounded display is not an edit to the stored number.
    if (!edited.current) {
      setText(display(value, decimals))
      return
    }
    edited.current = false
    if (raw.trim() === '') {
      // Clearing an optional field means "not set". For a field that has no
      // null state, fall back to redisplaying the current value rather than
      // writing NaN.
      if (onClear) {
        setText('')
        if (value !== null) onClear()
      } else {
        setText(display(value, decimals))
      }
      return
    }
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) {
      setText(display(value, decimals))
      return
    }
    let next = parsed
    if (typeof min === 'number') next = Math.max(min, next)
    if (typeof max === 'number') next = Math.min(max, next)
    // Redisplay from the COMMITTED number, not from the rounded text: with
    // `decimals` set the two differ, and echoing the rounded form back would
    // make the next blur commit the rounding.
    setText(display(next, decimals))
    if (next !== value && onCommit(next) === false) setText(display(value, decimals))
  }

  return (
    <input
      id={id}
      aria-label={ariaLabel}
      aria-describedby={ariaDescribedBy}
      {...(className === undefined ? {} : { className })}
      type="number"
      {...(width === undefined ? {} : { style: { width } })}
      step={step}
      placeholder={placeholder}
      min={min}
      max={max}
      disabled={disabled}
      value={text}
      onFocus={() => { cancelled.current = false; edited.current = false; setEditing(true) }}
      onChange={(e) => { edited.current = true; setText(e.target.value) }}
      onBlur={(e) => {
        setEditing(false)
        if (cancelled.current) { cancelled.current = false; setText(display(value, decimals)) }
        else commit(e.target.value)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') {
          cancelled.current = true
          setText(display(value, decimals))
          e.currentTarget.blur()
        }
      }}
    />
  )
}
