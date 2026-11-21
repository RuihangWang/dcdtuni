import { Icon } from './Icon'

/** An explanation kept out of the panel's text: an info mark that shows it on hover. */
export function Hint({ text, id }: { text: string; id?: string }) {
  return (
    <span className="hint-mark" id={id} role="note" aria-label={text} title={text}>
      <Icon name="info" size={13} />
    </span>
  )
}
