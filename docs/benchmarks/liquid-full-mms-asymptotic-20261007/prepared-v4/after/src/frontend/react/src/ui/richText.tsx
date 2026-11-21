import { Fragment, type ReactNode } from 'react'

/**
 * Translated strings that carry `<b>` emphasis.
 *
 * A handful of dictionary entries mark up the value inside the sentence —
 * `"当前输入和显示单位为 <b>{unit}</b>。"`. The legacy page rendered every hint
 * through `innerHTML`, so the tag worked. React renders text, so the same
 * string reaches the panel as the literal characters `<b>mm</b>`.
 *
 * Stripping the tags is the wrong repair, and one component had already done it
 * locally (`ImportedStlOverview`, with a `.replace(/<\/?b>/g, '')`): the
 * emphasis is there because the value is the point of the sentence, and a
 * per-call regex means the next consumer of the same key gets the raw tags
 * again — which is exactly what happened on the unit hint.
 *
 * So the markup is PARSED, not deleted, and not handed to
 * `dangerouslySetInnerHTML` either: these strings are translations, and a
 * translation should not be able to inject markup. `<b>` is the only tag the
 * dictionaries use, and the only one this understands.
 */

/** One run of the string, and whether it was inside `<b>…</b>`. */
export interface RichSpan {
  text: string
  bold: boolean
}

const BOLD = /<b>(.*?)<\/b>/gs

/**
 * Split on `<b>…</b>`.
 *
 * An unclosed `<b>` matches nothing and is left as literal text, which is the
 * conservative reading: a dictionary typo should show up as a visible tag
 * somebody fixes, not silently bold the rest of the sentence.
 */
export function splitBold(text: string): RichSpan[] {
  const out: RichSpan[] = []
  let last = 0
  for (const match of text.matchAll(BOLD)) {
    const at = match.index
    if (at > last) out.push({ text: text.slice(last, at), bold: false })
    if (match[1]) out.push({ text: match[1], bold: true })
    last = at + match[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), bold: false })
  return out
}

/** Render a translated string, honouring its `<b>` emphasis. */
export function RichText({ text }: { text: string }): ReactNode {
  return (
    <>
      {splitBold(text).map((span, i) => (
        <Fragment key={i}>{span.bold ? <b>{span.text}</b> : span.text}</Fragment>
      ))}
    </>
  )
}
