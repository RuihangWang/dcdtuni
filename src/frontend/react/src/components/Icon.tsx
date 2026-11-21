import type { CSSProperties } from 'react'

/** Outline icons drawn in the current text colour (24-unit grid, 1.6 stroke), so they follow the theme. */

const PATHS = {
  folder: 'M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z',
  folderOpen: 'M3 18V7.5A1.5 1.5 0 0 1 4.5 6h4l2 2H18a1.5 1.5 0 0 1 1.5 1.5V11 M3 18l2.6-6.2A1.5 1.5 0 0 1 7 11h13.2a.8.8 0 0 1 .7 1.1L18.6 18a1.5 1.5 0 0 1-1.4 1H4a1 1 0 0 1-1-1z',
  save: 'M5 4h11l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1z M8 4v5h7V4 M8 20v-6h8v6',
  plan: 'M4 4h16v16H4z M4 10h7 M11 4v9 M15 10h5 M11 16v4',
  firstPerson: 'M12 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M5.5 20a6.5 6.5 0 0 1 13 0',
  droplet: 'M12 3.5s-6 6.4-6 10.5a6 6 0 0 0 12 0c0-4.1-6-10.5-6-10.5z',
  wind: 'M3 9h11a3 3 0 1 0-3-3 M3 13h15a3 3 0 1 1-3 3 M3 17h7',
  cube: 'M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9',
  mesh: 'M4 4h16v16H4z M4 9.3h16 M4 14.7h16 M9.3 4v16 M14.7 4v16',
  solve: 'M9 3h6 M10 3v6.5L5 18a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3L14 9.5V3 M7.5 15h9',
  chart: 'M4 20V4 M4 20h16 M8 16v-4 M12 16V8 M16 16v-7',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M19.4 13.5a7.6 7.6 0 0 0 0-3l2-1.6-2-3.4-2.4 1a7.5 7.5 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.5A7.5 7.5 0 0 0 7 6.5l-2.4-1-2 3.4 2 1.6a7.6 7.6 0 0 0 0 3l-2 1.6 2 3.4 2.4-1a7.5 7.5 0 0 0 2.6 1.5l.4 2.5h4l.4-2.5a7.5 7.5 0 0 0 2.6-1.5l2.4 1 2-3.4z',
  plus: 'M12 5v14 M5 12h14',
  minus: 'M5 12h14',
  rack: 'M6 3h12v18H6z M6 8h12 M6 13h12 M9 5.5h1 M9 10.5h1 M9 15.5h1',
  snowflake: 'M12 2.5v19 M3.8 7.2l16.4 9.6 M3.8 16.8l16.4-9.6 M9.5 4l2.5 2 2.5-2 M9.5 20l2.5-2 2.5 2',
  wall: 'M3 5h18v14H3z M3 9.7h18 M3 14.3h18 M9 5v4.7 M15 5v4.7 M12 9.7v4.6 M6 9.7v4.6 M18 9.7v4.6 M9 14.3V19 M15 14.3V19',
  partition: 'M4 20V6l8-3 8 3v14 M12 3v17 M4 20h16',
  sheet: 'M6 3h9l3 3v15H6z M15 3v3h3',
  floor: 'M3 15l9-5 9 5-9 5z M3 15v2l9 5 9-5v-2',
  pipe: 'M3 9h18 M3 15h18 M5 7v10 M19 7v10',
  import: 'M12 3v12 M7 10l5 5 5-5 M4 17v3h16v-3',
  export: 'M12 15V3 M7 8l5-5 5 5 M4 17v3h16v-3',
  trash: 'M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 11v6 M14 11v6',
  refresh: 'M20 11a8 8 0 0 0-14.3-4.9L4 8 M4 4v4h4 M4 13a8 8 0 0 0 14.3 4.9L20 16 M20 20v-4h-4',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z',
  camera: 'M4 8h3l1.5-2.5h7L17 8h3v11H4z M12 10.5a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2.5v2 M12 19.5v2 M2.5 12h2 M19.5 12h2 M5.3 5.3l1.4 1.4 M17.3 17.3l1.4 1.4 M5.3 18.7l1.4-1.4 M17.3 6.7l1.4-1.4',
  layers: 'M12 3l9 5-9 5-9-5z M3 12.5l9 5 9-5 M3 17l9 5 9-5',
  slice: 'M3 16l9-5 9 5-9 5z M12 3v8 M8.5 6.5L12 3l3.5 3.5',
  streamline: 'M3 7c4-3 7 3 11 0s5-2 7-1 M3 12c4-3 7 3 11 0s5-2 7-1 M3 17c4-3 7 3 11 0s5-2 7-1',
  volume: 'M12 3l8 4.5v9L12 21l-8-4.5v-9z M12 8.5l4 2.2v4.6L12 17.5l-4-2.2v-4.6z',
  palette: 'M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.8 1.8-1.7 0-1.2-1-1.6-1-2.6 0-.9.8-1.7 1.7-1.7H17a4 4 0 0 0 4-4C21 6.6 17 3 12 3z M7.5 11.5h.01 M10 7.5h.01 M14.5 7.5h.01',
  hexagon: 'M12 3l7.8 4.5v9L12 21l-7.8-4.5v-9z',
  thermometer: 'M14 14.5V5a2 2 0 0 0-4 0v9.5a4 4 0 1 0 4 0z M12 9v8',
  play: 'M7 4.5v15l12-7.5z',
  stop: 'M6 6h12v12H6z',
  check: 'M4.5 12.5l5 5 10-11',
  close: 'M6 6l12 12 M18 6L6 18',
  alert: 'M12 4l9 16H3z M12 10v4.5 M12 17.5h.01',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 11v5.5 M12 7.5h.01',
  link: 'M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1 M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1',
  target: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 11.5v1',
  pin: 'M12 21s6.5-6 6.5-11a6.5 6.5 0 0 0-13 0c0 5 6.5 11 6.5 11z M12 7.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z',
  ruler: 'M3 16.5L16.5 3 21 7.5 7.5 21z M7 12.5l2 2 M10 9.5l2 2 M13 6.5l2 2',
  rotate: 'M20 12a8 8 0 1 1-2.3-5.7 M20 4v4.5h-4.5',
  move: 'M12 3v18 M3 12h18 M9 6l3-3 3 3 M9 18l3 3 3-3 M6 9l-3 3 3 3 M18 9l3 3-3 3',
  wrench: 'M14.5 6.5a4 4 0 0 0 5 5l-9 9a2.1 2.1 0 0 1-3-3l9-9a4 4 0 0 1-2-2z',
  flame: 'M12 21a6 6 0 0 0 6-6c0-4-3-6-4-9-1.5 2-2 3-2 4.5C11 9 9.5 8 9 6.5 7.5 8.5 6 11 6 15a6 6 0 0 0 6 6z',
  sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M18.5 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z',
  arrowUp: 'M12 19V5 M6 11l6-6 6 6',
  arrowDown: 'M12 5v14 M6 13l6 6 6-6',
  chevronDown: 'M6 9l6 6 6-6',
  chevronRight: 'M9 6l6 6-6 6',
  undo: 'M9 14L4 9l5-5 M4 9h10a6 6 0 0 1 0 12h-3',
  redo: 'M15 14l5-5-5-5 M20 9H10a6 6 0 0 0 0 12h3',
  terminal: 'M4 5h16v14H4z M7.5 9.5l3 2.5-3 2.5 M12.5 15h4',
  grid: 'M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z',
  shadow: 'M12 3a9 9 0 1 0 0 18z M12 3a9 9 0 0 1 0 18',
  upload: 'M12 15V4 M7 9l5-5 5 5 M4 15v5h16v-5',
  copy: 'M8 8h12v12H8z M4 16V4h12',
  ai: 'M5 5h14v14H5z M9 2.5V5 M15 2.5V5 M9 19v2.5 M15 19v2.5 M2.5 9H5 M2.5 15H5 M19 9h2.5 M19 15h2.5 M9 9h6v6H9z',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 15, style, className }: {
  name: IconName
  size?: number
  style?: CSSProperties
  className?: string
}) {
  return (
    <svg className={`icon${className ? ` ${className}` : ''}`} width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false" style={style}>
      <path d={PATHS[name]} />
    </svg>
  )
}

/** An icon by name, or the given text when it names none (labels supplied from elsewhere). */
export function Glyph({ icon, size }: { icon: string; size?: number }) {
  return icon in PATHS ? <Icon name={icon as IconName} size={size} /> : <span>{icon}</span>
}

export type Status = 'ok' | 'error' | 'warn' | 'busy'

const STATUS_ICON: Record<Status, IconName> = { ok: 'check', error: 'close', warn: 'alert', busy: 'refresh' }

/** A status mark in its status colour. */
export function StatusMark({ status, size = 13 }: { status: Status; size?: number }) {
  return <Icon name={STATUS_ICON[status]} size={size} className={`status-mark status-${status}`} />
}
