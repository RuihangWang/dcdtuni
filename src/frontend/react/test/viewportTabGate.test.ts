import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { canSelectGeometryFromViewport } from '../src/interaction/picking'

/**
 * A click in the 3D view selects GEOMETRY only on the geometry tab.
 *
 * On the mesh tab the same click also selected the model behind the mesh
 * patch (measured on Tri_angle: a click on a blanking-panel patch selected
 * `{rackSlot back 18}` of Default_Rack_1 as well as the patch row). Selecting a
 * rack rebuilds it for its slot guides, the rebuild came back at full opacity
 * with depth writes on, and the rack sat coincident with the mesh patches:
 * the z-fighting speckle the user reported. The patch list has its own
 * mouseup listener (`patchViewportPick.ts`) and keeps working on every tab.
 *
 * The legacy gates its ordinary click exactly like this —
 * `endViewportPointerState` (STL_generate.html:18145) selects only when
 * `activeTab === 'geometry'` and the UI is not frozen — and React already had
 * the same rule, but only on first-person mode's crosshair.
 */

describe('canSelectGeometryFromViewport', () => {
  it('lets the geometry tab select', () => {
    expect(canSelectGeometryFromViewport('geometry', false)).toBe(true)
  })

  it.each(['mesh', 'solver', 'postprocess', 'system'])(
    'refuses on the %s tab, whose panel is not the geometry editor',
    (tab) => {
      expect(canSelectGeometryFromViewport(tab, false)).toBe(false)
    },
  )

  it('refuses while a mesh or solve has the UI frozen', () => {
    expect(canSelectGeometryFromViewport('geometry', true)).toBe(false)
  })

  it('refuses when the tab is not known', () => {
    expect(canSelectGeometryFromViewport(undefined, false)).toBe(false)
  })
})

/**
 * Where the predicate is used.
 *
 * The click handler lives inside `Viewport`'s scene-setup effect, reachable
 * only through a WebGL context jsdom cannot give (the convention in
 * `ppLightIntensity.test.ts`), so these pin the two call sites in the source:
 * one predicate, used by both the mouse click and the first-person crosshair,
 * so the two cannot drift apart again.
 */
describe('the viewport click handlers', () => {
  const viewport = readFileSync(join(__dirname, '..', 'src', 'components', 'Viewport.tsx'), 'utf8')
  const start = viewport.indexOf('const onClick = (e: MouseEvent) =>')
  const onClick = viewport.slice(start, viewport.indexOf('\n    }\n', start))

  it('asks the predicate before picking anything', () => {
    expect(start).toBeGreaterThan(0)
    const gate = onClick.indexOf('canSelectGeometryFromViewport(')
    const pick = onClick.indexOf('pickAndReveal(')
    expect(gate).toBeGreaterThan(0)
    expect(pick).toBeGreaterThan(gate)
  })

  it('reads the LIVE tab and freeze, not a captured one', () => {
    expect(onClick).toMatch(/canSelectGeometryFromViewport\(activeTabRef\.current, isUiFrozen\(\)\)/)
  })

  it('shares the predicate with first-person mode', () => {
    expect(viewport).toMatch(
      /canSelectGeometry: \(\) => canSelectGeometryFromViewport\(activeTabRef\.current, isUiFrozen\(\)\)/,
    )
    expect(viewport).not.toMatch(/canSelectGeometry: \(\) => !isUiFrozen\(\)/)
  })
})
