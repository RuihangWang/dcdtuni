/**
 * Post-processing panel state.
 *
 * Deliberately NOT in the project. None of this is saved in a .cfdproj — the
 * legacy editor keeps it in `window._ppState`, a view-only object that dies
 * with the page — so putting it in the store would make every colour-map change
 * dirty the project and land in the undo stack.
 *
 * The field lists are read from the loaded run rather than hardcoded: a case
 * solved without turbulence has no k/epsilon, and offering them would produce a
 * request the server answers with an error.
 */

// The colour maps live in `ppColormaps`, where they are 256-entry LUTs rather
// than a stop list — the shaders sample them as a texture. Re-exported here so
// the panel keeps importing its options from one place.
export { COLOR_MAPS, type ColorMap } from './ppColormaps'
import type { ColorMap } from './ppColormaps'
import { SPEED_FIELD } from './ppFieldOptions'
import { t } from '../i18n'

export interface SliceNormalOption {
  id: string
  /** What the option reads, already translated. */
  readonly label: string
  normal: [number, number, number]
}

/**
 * Named slice orientations, matching `pp-slice-normal`.
 *
 * The axes are the legacy's bare `X` / `Y` / `Z` (STL_generate.html:612); only
 * the last option has a name to translate, and it is a GETTER rather than a
 * plain string because this array is built once at module load. Two selects
 * read `label` — the sidebar's and the slice edit dialog's — and a string
 * frozen at load time would keep showing the language the page was opened in
 * after a switch.
 */
export const SLICE_NORMALS: SliceNormalOption[] = [
  { id: 'x', label: 'X', normal: [1, 0, 0] },
  { id: 'y', label: 'Y', normal: [0, 1, 0] },
  { id: 'z', label: 'Z', normal: [0, 0, 1] },
  {
    id: 'custom',
    get label() {
      return t('pp.slice_custom')
    },
    normal: [0, 1, 0],
  },
]

export interface SurfaceFieldState {
  field: string
  climMin: number
  climMax: number
  /** True while the range should follow the data instead of the two inputs. */
  autoScale: boolean
  colorMap: ColorMap
  visible: boolean
  /** Mark the highest and lowest value of the surface and of every slice. */
  showExtremes?: boolean
}

/**
 * The streamline FORM — what the next 添加流线 will produce.
 *
 * Distinct from the sets it creates. Moving the seed sphere must not silently
 * re-seed the sets already on screen, which is the whole reason a user adds
 * several: one from the cold aisle, one from an ACU return.
 */
export interface StreamlineState {
  show: boolean
  centre: [number, number, number]
  radius: number
  count: number
  tubeRadius: number
  field: string
  colorMap: ColorMap
  /** Master visibility for every set at once. */
  visible: boolean
  /** The seed sphere itself, separate from the lines it produced. */
  seedVisible: boolean
}

/** One generated streamline set, as its row in `pp-streamline-list` shows it. */
export interface StreamlineSetState {
  id: number
  label: string
  visible: boolean
  opacity: number
  colorMap: ColorMap
  tubeRadius: number
  field: string
  /**
   * The limits the tubes are COLOURED with. They start at `range` and are then
   * narrowed from the colorbar dialog, exactly as a slice's are — one fast cell
   * at an ACU nozzle stretches the data range until the whole room reads as the
   * same blue, and the way out is to type a tighter pair. `range` stays beside
   * it untouched so Auto-scale has something to go back to.
   */
  clim: [number, number]
  range: [number, number]
}

/** One computed slice, as its row in `pp-slice-list` shows it. */
export interface SliceSetState {
  /** View-only intent retained when reopening this cut; absent means manual. */
  autoScale?: boolean
  id: number
  label: string
  visible: boolean
  opacity: number
  colorMap: ColorMap
  field: string
  clim: [number, number]
  range: [number, number]
}

/** The slice FORM — what the next 应用 will cut. */
export interface SliceState {
  /** Only true chooses the data range; equal raw values remain valid manual limits. */
  autoScale?: boolean
  show: boolean
  showRing: boolean
  normalPreset: string
  normal: [number, number, number]
  origin: [number, number, number]
  field: string
  colorMap: ColorMap
  climMin: number
  climMax: number
  visible: boolean
}

export interface VolumeState {
  show: boolean
  field: string
  /** Grid resolution per axis; the fetch is repeated when it changes. */
  resolution: number
  opacity: number
  colorMap: ColorMap
  /**
   * Colour limits in RAW units, or null for "use the grid's own range".
   *
   * Nullable where the surface's are not, because the legacy's two volume boxes
   * start EMPTY and mean auto until an apply fills them
   * (STL_generate.html:20281 reads them as `isNaN(userMin) ? sRange[0] : …`). A
   * plain 0 is not a neutral placeholder here — the field is Kelvin, so the box
   * would open reading -273.1 °C.
   */
  climMin: number | null
  climMax: number | null
  visible: boolean
  showExtremes?: boolean
  /** Values drawn, in raw units; null keeps the grid's whole range. */
  threshold?: [number, number] | null
  /** Draw the values OUTSIDE the threshold window instead of inside it. */
  thresholdInvert?: boolean
}

export interface PostprocessState {
  runDir: string | null
  initialised: boolean
  /** Fields the loaded run actually has. */
  fields: string[]
  /** Iteration steps the selected run wrote, newest last. */
  timesteps: number[]
  timestep: number | null
  surface: SurfaceFieldState
  streamlines: StreamlineState
  streamlineSets: StreamlineSetState[]
  slice: SliceState
  sliceSets: SliceSetState[]
  volume: VolumeState
}

export function defaultPostprocessState(): PostprocessState {
  return {
    runDir: null,
    initialised: false,
    fields: [],
    timesteps: [],
    timestep: null,
    surface: {
      field: 'T',
      climMin: 0,
      climMax: 0,
      // Starts on: the first thing a user wants is to see the data, and a
      // fixed range chosen before the data is loaded shows a flat colour.
      autoScale: true,
      colorMap: 'coolwarm',
      visible: true,
    },
    streamlines: {
      show: false,
      centre: [0, 0, 0],
      radius: 0.5,
      count: 200,
      tubeRadius: 0.01,
      field: SPEED_FIELD,
      colorMap: 'jet',
      visible: true,
      seedVisible: true,
    },
    streamlineSets: [],
    slice: {
      autoScale: true,
      show: false,
      showRing: true,
      normalPreset: 'y',
      normal: [0, 1, 0],
      origin: [0, 0, 0],
      field: 'T',
      colorMap: 'coolwarm',
      climMin: 0,
      climMax: 0,
      visible: true,
    },
    sliceSets: [],
    volume: {
      show: false,
      field: 'T',
      // 128, the legacy default: 64 is visibly blocky on a room-scale grid and
      // 256 costs 8x the memory for a preview.
      resolution: 128,
      // 0.5, the legacy input's own default value (STL_generate.html:698).
      opacity: 0.5,
      colorMap: 'coolwarm',
      climMin: null,
      climMax: null,
      visible: true,
    },
  }
}
