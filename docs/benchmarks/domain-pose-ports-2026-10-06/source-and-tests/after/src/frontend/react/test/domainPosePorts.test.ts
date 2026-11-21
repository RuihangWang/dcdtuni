import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import vm from 'node:vm'
import { spawnSync } from 'node:child_process'
import { modelPoseKernel } from '../src/domain/modelPoseKernel'
import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { DomainProject, type BaseEntity } from '../src/domain'
import type { CfdProject } from '../src/schema/cfdproj'
import golden from './fixtures/modelPosePortsParity.json'

type Point = { x: number; y: number; z: number }
type Pose = { position: Point; quaternion: Point & { w: number }; scale: Point }
type Frame = { position: Point; direction: Point; localPosition: Point; localDirection: Point }
type NumericEntity = BaseEntity & {
  pose(): Pose
  localPointToWorld(value: unknown): Point | null
  localDirectionToWorld(value: unknown): Point | null
  portFrame(type: string, end: string): Frame | null
}
const root = resolve(process.cwd(), '../../..')
function legacy() {
  const context = vm.createContext({ window: {}, THREE })
  const core = resolve(root, 'src/frontend/shared/js/model_pose_ports.js')
  if (existsSync(core)) vm.runInContext(readFileSync(core, 'utf8'), context)
  for (const file of ['rack_state.js', 'geometry_pure.js']) {
    vm.runInContext(readFileSync(resolve(root, 'src/frontend/shared/js', file), 'utf8'), context)
  }
  return (context.window as { geometryPure: Record<string, (...args: any[]) => any> }).geometryPure
}
function bits(values: number[]) {
  const b = Buffer.alloc(values.length * 8)
  values.forEach((v, i) => b.writeDoubleLE(v, i * 8))
  return b.toString('hex')
}
function expectLegacyNumericBits(actual: Point, expectedHex: string) {
  const expected = Buffer.from(expectedHex, 'hex')
  Object.values(actual).forEach((value, index) => {
    const legacy = expected.readDoubleLE(index * 8)
    // IEEE NaN sign/payload is not a value contract. All finite values and
    // signed zero retain the stricter bit comparison used by the main corpus.
    if (Number.isNaN(legacy)) expect(Number.isNaN(value)).toBe(true)
    else expect(bits([value])).toBe(expected.subarray(index * 8, index * 8 + 8).toString('hex'))
  })
}
function frameValues(frame: Frame) {
  return [...Object.values(frame.position), ...Object.values(frame.direction),
    ...Object.values(frame.localPosition), ...Object.values(frame.localDirection)]
}
describe('actual domain pose and ports own the legacy numeric geometry', () => {
  for (const sample of golden.cases) {
    it(sample.name, () => {
      const record = structuredClone(sample.model)
      const project = { schemaVersion: 5, uid: 'ports', geometry: { plane: [], height: 3 },
        constructions: { [sample.bucket]: { [record.uid]: record } } } as unknown as CfdProject
      const before = JSON.stringify(project)
      const entity = new DomainProject(project).topLevel()[0] as NumericEntity
      expect(entity.uid).toBe(record.uid)
      expect(record).not.toHaveProperty('kind')
      expect(typeof entity.pose).toBe('function')
      expect(typeof entity.localPointToWorld).toBe('function')
      expect(typeof entity.localDirectionToWorld).toBe('function')
      expect(typeof entity.portFrame).toBe('function')
      const pose = entity.pose(), point = entity.localPointToWorld(sample.point)!
      const direction = entity.localDirectionToWorld(sample.direction)!
      const port = entity.portFrame(sample.portType, sample.end)!
      expect(pose).toEqual(sample.expected.pose)
      expect(point).toEqual(sample.expected.point)
      expect(direction).toEqual(sample.expected.direction)
      expect(port).toEqual(sample.expected.port)
      expect(bits([...Object.values(pose.position), ...Object.values(pose.quaternion),
        ...Object.values(pose.scale), ...Object.values(point), ...Object.values(direction),
        ...frameValues(port)])).toBe(sample.expected.float64LEHex)
      expect(JSON.stringify(project)).toBe(before)
      const again = entity.pose()
      expect(again).not.toBe(pose)
      expect(again.position).not.toBe(pose.position)
    })
  }

  it('keeps legacy carrier output matched to the same pre-change producer without changing model data', () => {
    const api = legacy()
    for (const sample of golden.cases) {
      const model = { ...sample.model, kind: sample.bucket === 'racks' ? 'rack' : 'cdu' }
      const before = JSON.stringify(model)
      const point = api.modelLocalPointToWorld!(model, sample.point) as THREE.Vector3
      const direction = api.modelLocalDirectionToWorld!(model, sample.direction) as THREE.Vector3
      const frame = api.liquidEquipmentPortFrame!(model, sample.portType, sample.end) as Frame
      expect(point).toBeInstanceOf(THREE.Vector3)
      expect(direction).toBeInstanceOf(THREE.Vector3)
      expect(bits([...point.toArray(), ...direction.toArray(), ...frameValues(frame)])).toBe(
        sample.expected.float64LEHex.slice((3 + 4 + 3) * 16))
      expect(JSON.stringify(model)).toBe(before)
    }
  })

  it('executes without renderer globals and preserves fresh defaults in standalone CommonJS callers', () => {
    const script = [
      "const assert = require('node:assert/strict');",
      'const core = require(' + JSON.stringify(resolve(root, 'src/frontend/shared/js/model_pose_ports.js')) + ');',
      'const rack = require(' + JSON.stringify(resolve(root, 'src/frontend/shared/js/rack_state.js')) + ');',
      'const pure = require(' + JSON.stringify(resolve(root, 'src/frontend/shared/js/geometry_pure.js')) + ');',
      "assert.equal(typeof global.THREE, 'undefined');",
      "assert.equal(typeof global.window, 'undefined');",
      'const first = rack.getDefaultModelTransform();',
      'first.quaternion.x = 8; first.scale.y = 9;',
      'assert.deepEqual(rack.getDefaultModelTransform(), core.getDefaultModelTransform());',
      'assert.notStrictEqual(core.getDefaultModelTransform().scale, core.getDefaultModelTransform().scale);',
      'const cloned = pure.cloneModelTransform({quaternion:{x:.25},scale:{z:-2}});',
      'assert.deepEqual(cloned, core.cloneModelTransform({quaternion:{x:.25},scale:{z:-2}}));',
      'assert.deepEqual(core.modelLocalPointToWorld({geometry:{}},{x:1,y:2,z:3}),{x:1,y:2,z:3});',
      'assert.equal(pure.modelLocalPointToWorld({geometry:{}},{x:1,y:2,z:3}),null);',
      'assert.equal(Object.isFrozen(core),true);',
      'const original = core.modelPose;',
      "assert.throws(()=>Object.defineProperty(core,'modelPose',{value:()=>null}), TypeError);",
      'assert.strictEqual(core.modelPose,original);',
    ].join('\n')
    const child = spawnSync(process.execPath, ['-e', script], { encoding: 'utf8' })
    expect(child.status, child.stderr).toBe(0)
  })

  it('preserves coercion, non-finite arithmetic, missing-carrier and unsupported-kind boundaries', () => {
    const model = { geometry: { location: { x: Infinity, y: '2', z: NaN },
      quaternion: { x: Number.MAX_VALUE, y: Number.MAX_VALUE, z: 0, w: 0 },
      scale: { x: '2', y: -0, z: Infinity } } }
    const point = { x: Infinity, y: NaN, z: '3' }
    const direction = { x: 0, y: 0, z: 0 }
    expectLegacyNumericBits(modelPoseKernel.modelLocalPointToWorld(model, point)!, golden.nonfiniteOriginal.pointFloat64LEHex)
    expectLegacyNumericBits(modelPoseKernel.modelLocalDirectionToWorld(model, direction)!, golden.nonfiniteOriginal.directionFloat64LEHex)
    expect(modelPoseKernel.modelLocalPointToWorld(model, null)).toBeNull()
    expect(modelPoseKernel.modelLocalDirectionToWorld(null, direction)).toBeNull()
    expect(modelPoseKernel.equipmentPortFrame({ geometry: {} }, 'box', 'supply', 'top')).toBeNull()
    expect(modelPoseKernel.equipmentPortFrame({}, 'rack', 'supply', 'top')).toBeNull()
    expect(modelPoseKernel.cloneModelTransform({ quaternion: { x: '2' }, scale: { x: '2' } })).toEqual(
      modelPoseKernel.getDefaultModelTransform())
  })
})
