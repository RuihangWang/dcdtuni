import { describe, expect, it } from 'vitest'
import { buildCfdPackage, defaultPackageName, remapStlFiles } from '../src/io/cfdPackage'
import { readZip } from '../src/io/zipWriter'
import { emptyManifest } from '../src/schema/cfdrun'

/**
 * The bucket names and their `stlFile` values are `buildCfdrunJson`'s
 * (js/cfdrun_build.js:263-269, :593-793) — the v1 meshGeometry blob the
 * exporter mutates.
 */
function meshGeometry() {
  return {
    projectName: 'old',
    acus: { acu_1: { stlFile: 'acu_1.stl' }, acu_2: { stlFile: 'merged_acu.stl' } },
    wallObjects: { box_1: { stlFile: 'box_1.stl' } },
    baffles: { baffle_1: { stlFile: 'baffle_1.stl' } },
    floors: { floor_1: { stlFile: 'merged_perforated.stl' } },
    walls: { wall_1: { stlFile: 'wall_1.stl' }, room_bounds: {} },
    partitionWalls: { pw_1: { stlFile: 'pw_1.stl' } },
    racks: { rack_1: { stlFile: 'merged_rack_frame.stl' } },
  }
}

describe('remapStlFiles', () => {
  it('points every object at its merged group file', () => {
    const g = meshGeometry()
    remapStlFiles(g)
    expect(g.acus.acu_1.stlFile).toBe('merged_acu.stl')
    expect(g.wallObjects.box_1.stlFile).toBe('merged_box.stl')
    expect(g.baffles.baffle_1.stlFile).toBe('merged_baffle.stl')
    expect(g.floors.floor_1.stlFile).toBe('merged_floor_ceiling.stl')
    expect(g.walls.wall_1.stlFile).toBe('room_wall.stl')
    expect(g.partitionWalls.pw_1.stlFile).toBe('merged_partition_wall.stl')
  })

  it('leaves a wall with no stlFile alone', () => {
    // A wall entry without a file is a block boundary, not a solid in the STL
    // set; naming a file there declares a patch the package cannot supply.
    const g = meshGeometry()
    remapStlFiles(g)
    expect('stlFile' in g.walls.room_bounds).toBe(false)
  })

  it('does not touch racks, whose frame/door split has its own files', () => {
    const g = meshGeometry()
    remapStlFiles(g)
    expect(g.racks.rack_1.stlFile).toBe('merged_rack_frame.stl')
  })

  it('reports only the entries it actually changed', () => {
    const changed = remapStlFiles(meshGeometry())
    expect(changed.map((c) => c.path).sort()).toEqual([
      'acus.acu_1',
      'baffles.baffle_1',
      'floors.floor_1',
      'partitionWalls.pw_1',
      'wallObjects.box_1',
      'walls.wall_1',
    ])
    expect(changed.find((c) => c.path === 'floors.floor_1')).toEqual({
      path: 'floors.floor_1',
      from: 'merged_perforated.stl',
      to: 'merged_floor_ceiling.stl',
    })
  })

  it('is idempotent, so a second export changes nothing', () => {
    const g = meshGeometry()
    remapStlFiles(g)
    expect(remapStlFiles(g)).toEqual([])
  })

  it('survives a blob with buckets missing', () => {
    expect(() => remapStlFiles({})).not.toThrow()
  })
})

describe('buildCfdPackage', () => {
  const stl = {
    'merged_rack_frame.stl': 'solid rack\nendsolid rack\n',
    'merged_acu.stl': 'solid acu\nendsolid acu\n',
  }

  it('packs one folder holding the STL set and the manifest', () => {
    const pkg = buildCfdPackage('Room7', emptyManifest(), stl)
    expect(pkg.filename).toBe('Room7_cfd.zip')
    expect(pkg.entries).toEqual([
      'Room7_cfd/',
      'Room7_cfd/merged_rack_frame.stl',
      'Room7_cfd/merged_acu.stl',
      'Room7_cfd/Room7.cfdrun',
    ])
  })

  it('writes the manifest as readable JSON inside the archive', () => {
    const pkg = buildCfdPackage('Room7', { ...emptyManifest(), meshGeometry: { projectName: 'Room7' } }, stl)
    const listing = readZip(pkg.bytes)
    const manifest = listing.find((e) => e.name === 'Room7_cfd/Room7.cfdrun')
    const written = JSON.parse(manifest?.text ?? '{}')
    expect(written.schemaVersion).toBe(2)
    expect(written.meshGeometry).toEqual({ projectName: 'Room7' })
  })

  it('keeps the STL text byte-for-byte', () => {
    const listing = readZip(buildCfdPackage('R', emptyManifest(), stl).bytes)
    expect(listing.find((e) => e.name === 'R_cfd/merged_acu.stl')?.text).toBe(stl['merged_acu.stl'])
  })
})

describe('defaultPackageName', () => {
  it('prefers the project name', () => {
    expect(defaultPackageName({ projectName: 'Hall_A', exportFileName: 'x' })).toBe('Hall_A')
  })

  it('falls back to the export filename, as the legacy prompt does', () => {
    expect(defaultPackageName({ exportFileName: 'my_export' })).toBe('my_export')
  })

  it('falls back to CFD_Project when the project has neither', () => {
    expect(defaultPackageName(undefined)).toBe('CFD_Project')
    expect(defaultPackageName({ projectName: '' })).toBe('CFD_Project')
  })
})
