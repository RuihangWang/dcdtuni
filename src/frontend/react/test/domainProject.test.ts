import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DomainProject, entityRegistry, CONSTRUCTION_BUCKET_TYPES, LEGACY_EXPORT_BUCKETS,
  BaseEntity, RackEntity, ServerEntity, WallEntity, BaffleEntity, CduEntity, BoxEntity, PduEntity, decodeDomainProject } from '../src/domain'
import { allModels, BUCKETS, type CfdProject } from '../src/schema/cfdproj'
import { stateV4, reviveKinds } from '../src/legacy/cfdprojModules'
import { checkProjectDocument, readProjectFileText, projectForSave, serializeProjectState } from '../src/io/projectFiles'
import { sceneRunDeps } from '../src/run/runSubmit'
import { compileDynamicLiquidGraph } from '../src/liquid/dynamicLiquidGraph'
import { cfdrunExport, createCfdrunBuild, buildManifestV2 } from '../src/legacy/cfdrunBuild'
import * as THREE from 'three'
import { createSceneBuilder, emptyRegistries } from '../src/interaction/sceneBuilder'
import { liveProject } from '../src/transient/model'

const model = (uid: string) => ({ uid, meta: { name: uid, enabled: true }, geometry: { location: { x: 0, y: 0, z: 0 } } })
function complete(): CfdProject {
  const constructions: Record<string, unknown> = {}
  for (const bucket of Object.keys(CONSTRUCTION_BUCKET_TYPES)) constructions[bucket] = { [bucket]: model(bucket) }
  constructions.raisedFloor = model('raised')
  constructions.falseCeiling = model('ceiling')
  return { schemaVersion: 5, uid: 'project', geometry: { plane: [], height: 3 }, constructions } as CfdProject
}

describe('complete versioned domain registry and identity views', () => {
  it('covers the exact 18 persisted buckets, two singleton slots and room without registering Three objects', () => {
    expect(Object.keys(CONSTRUCTION_BUCKET_TYPES).sort()).toEqual([...BUCKETS].sort())
    expect(Object.keys(CONSTRUCTION_BUCKET_TYPES)).toHaveLength(18)
    const p = complete()
    const before = JSON.stringify(p)
    const domain = new DomainProject(p)
    expect(domain.allEntities().map(e => e.type)).toEqual([...Object.keys(CONSTRUCTION_BUCKET_TYPES), 'room', 'raisedFloor', 'falseCeiling'])
    expect(domain.allEntities().every(e => e instanceof BaseEntity && e.supported)).toBe(true)
    expect(entityRegistry.resolve('racks', 2)).toBeUndefined()
    expect(entityRegistry.version).toBe(1)
    expect(JSON.stringify(p)).toBe(before)
  })

  it('hydrates named entity classes while PDU names remain physical BOX records', () => {
    const p = complete()
    const entities = new DomainProject(p).topLevel()
    expect(entities.find(e => e.type === 'servers')).toBeInstanceOf(ServerEntity)
    expect(entities.find(e => e.type === 'walls')).toBeInstanceOf(WallEntity)
    expect(entities.find(e => e.type === 'baffles')).toBeInstanceOf(BaffleEntity)
    expect(entities.find(e => e.type === 'cdus')).toBeInstanceOf(CduEntity)
    const box = p.constructions!.boxes!.boxes!
    box.meta.name = 'PDU_named_only'
    expect(new DomainProject(p).topLevel(['boxes'])[0]).toBeInstanceOf(BoxEntity)
    expect(new DomainProject(p).topLevel(['boxes'])[0]).not.toBeInstanceOf(PduEntity)
    box.assetDefinition = { type: 'pdu', futureElectrical: 'not-inferred' }
    const explicit = new DomainProject(p).topLevel(['boxes'])[0]!
    expect(explicit).toBeInstanceOf(PduEntity)
    expect(explicit.kind).toBe('box')
    expect(explicit.record).toBe(box)
    expect(explicit.physics().declarations).not.toHaveProperty('electricity')
  })

  it('does not invent final visibility or physical inclusion from a metadata flag', () => {
    const p = complete()
    const box = p.constructions!.boxes!.boxes!
    box.meta.enabled = true
    box.meta.display = { opacity: .5, hidden: true } as typeof box.meta.display
    const render = new DomainProject(p).topLevel(['boxes'])[0]!.render()
    expect(render.enabled).toBe(true)
    expect(render.display).toEqual({ opacity: .5, hidden: true })
    expect(render).not.toHaveProperty('visible')
  })

  it('preserves parent-local server UIDs and authored CP/valve/port/face/network child records', () => {
    const p = complete() as unknown as Record<string, any>
    p.constructions.racks = {
      r1: { ...model('r1'), constructions: { servers: { s: { ...model('s'), futureChild: 1,
        liquidCooling: { cpCuPairs: [{ uid: 'cp/a', futurePair: 2 }] } } } } },
      r2: { ...model('r2'), constructions: { servers: { s: model('s') } } },
    }
    p.constructions.pipeSegments.ps = { ...model('ps'),
      cooling: { valves: [{ id: 'v' }], ports: { inlet: { id: 'inlet' } } },
      geometry: { waypoints: [{ x: 1, y: 2, z: 3 }], faces: { front: { patches: [{ id: 'patch' }] } } },
    }
    p.constructions.liquidFlowNetworks.net = { ...model('net'), nodes: { n: { id: 'n' } }, edges: [{ id: 'e' }] }
    const entities = new DomainProject(p as CfdProject).allEntities()
    const servers = entities.filter(e => e.type === 'rackServer')
    expect(servers.map(e => [e.uid, e.identity.parentUid])).toEqual([['s', 'r1'], ['s', 'r2']])
    expect(new Set(servers.map(e => e.identity.path)).size).toBe(2)
    expect(entities.find(e => e.type === 'coldPlatePair')?.record.futurePair).toBe(2)
    for (const type of ['coolingPort', 'valve', 'pipeWaypoint', 'modelFace', 'surfacePatch', 'liquidNetworkNode', 'liquidNetworkEdge'])
      expect(entities.some(e => e.type === type)).toBe(true)
    const rack = entities.find(e => e.uid === 'r1') as RackEntity
    expect(rack.serverUids()).toEqual(['s'])
  })

  it('enumerates actual LiquidSingle authored cold plates, equipment internals and face records', () => {
    const p = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/LiquidSingle/LiquidSingle.cfdproj'), 'utf8'))
    const before = JSON.stringify(p)
    const entities = new DomainProject(p).allEntities()
    const plate = entities.find(e => e.type === 'coldPlate' && e.uid === 'CP-CU_1')
    expect(plate?.identity.path).toBe('/constructions/racks/m_2/constructions/servers/e_2/constructions/coldPlates/CP-CU_1')
    expect(plate?.identity.parentUid).toBe('e_2')
    expect(plate?.record.Q_W).toBe(3500)
    const legacyPair = entities.find(e => e.type === 'coldPlatePair')
    expect(legacyPair?.identity.path).not.toBe(plate?.identity.path)
    expect(legacyPair?.record).not.toBe(plate?.record)
    for (const type of ['heatExchanger', 'pump', 'valve'])
      expect(entities.some(e => e.type === type && e.identity.parentUid === 'm_1')).toBe(true)
    expect(entities.some(e => e.type === 'modelFace' && e.identity.path.endsWith('/servers/e_2/faces/inlet'))).toBe(true)
    expect(new Set(entities.map(e => e.identity.path)).size).toBe(entities.length)
    expect(JSON.stringify(p)).toBe(before)
  })

  it('covers declared chips, rack partitions/RDHX, ACU fans and cutouts without assigning new source UIDs', () => {
    const p = complete() as unknown as Record<string, any>
    p.constructions.racks.r = { ...model('r'), constructions: {
      servers: { s: { ...model('s'), constructions: { chips: { chip: { uid: 'chip', power: 10 } }, coldPlates: {} } } },
      partitions: [{ id: 'partition', height: 1 }], rdhx: { enabled: true },
    } }
    p.constructions.acus.a = { ...model('a'), constructions: { heatExchanger: { specName: 'HX' }, fans: { fan: { uid: 'fan' } } } }
    p.constructions.walls.w = { ...model('w'), geometry: { cutouts: [{ faceId: 'front', polygon: [1, 2, 3], sourceName: 'AHU1' }] } }
    const before = JSON.stringify(p)
    const entities = new DomainProject(p as CfdProject).allEntities()
    for (const type of ['chip', 'rackPartition', 'rdhx', 'fan', 'geometryCutout'])
      expect(entities.some(e => e.type === type)).toBe(true)
    expect(entities.find(e => e.type === 'rackPartition')?.identity.source).toBe('id')
    expect(JSON.stringify(p)).toBe(before)
  })

  it('exposes pure data geometry/render/physics descriptions and explicit record copies', () => {
    const p = complete()
    const domain = new DomainProject(p)
    const box = domain.topLevel(['boxes'])[0]!
    expect(box.geometry().strategy).toBe('body')
    expect(box.render().modelUid).toBe('boxes')
    expect(box.physics().sourcePath).toBe('/constructions/boxes/boxes')
    expect(box.toRecord()).toEqual(box.record)
    expect(box.toRecord()).not.toBe(box.record)
    expect(Object.getPrototypeOf(p)).toBe(Object.prototype)
    expect(JSON.stringify(projectForSave(p))).not.toContain('definition')
  })

  it('keeps the real tree enumeration scope/order and object identity, excluding topology-only network entries', () => {
    const p = reviveKinds(complete())
    const before = JSON.stringify(p)
    const legacy = stateV4().allModels(p) as { uid: string }[]
    const actual = new DomainProject(p).structuralForAirExport()
    expect(actual.map(e => e.uid)).toEqual(legacy.map(m => m.uid))
    expect(actual.map(e => e.type)).toEqual(LEGACY_EXPORT_BUCKETS)
    expect(allModels(p).map(e => e.model.uid)).toEqual([
      'boxes', 'racks', 'acus', 'cdus', 'pipeSegments', 'pipeJunctions', 'pipes',
      'walls', 'partitionWalls', 'baffles', 'floors', 'customMeshes', 'surfaces', 'rows', 'servers', 'sensors', 'heatGains',
    ])
    expect(allModels(p)[0]?.model).toBe(p.constructions?.boxes?.boxes)
    expect(JSON.stringify(p)).toBe(before)
  })

  it('uses registry admission in the actual air run model UID path without changing legacy order', () => {
    const p = reviveKinds(complete())
    const deps = sceneRunDeps({ project: p, projectPath: null, builder: null })
    expect(deps.modelUids()).toEqual((stateV4().allModels(p) as { uid: string }[]).map(m => m.uid))
    expect(deps.modelUids()).not.toContain('liquidFlowNetworks')
  })

  it('preserves future records on load and refuses undeclared geometry at physical export', () => {
    const p = complete() as unknown as Record<string, any>
    p.constructions.futureDevices = { f: { ...model('f'), future: 7 } }
    const loaded = checkProjectDocument(p)
    expect(loaded.ok).toBe(true)
    expect(new DomainProject(p as CfdProject).allEntities().find(e => e.type === 'futureDevices')?.supported).toBe(false)
    expect(() => sceneRunDeps({ project: p as CfdProject, projectPath: null, builder: null }).modelUids()).toThrow(/Unsupported geometric bucket/)
    delete p.constructions.futureDevices.f.geometry
    expect(() => new DomainProject(p as CfdProject).structuralForAirExport()).not.toThrow()
  })

  it('refuses unknown geometric arrays and nested rack bodies while retaining their plain source records', () => {
    const p = complete() as unknown as Record<string, any>
    p.constructions.futureDevices = [{ ...model('future') }]
    expect(() => new DomainProject(p as CfdProject).structuralForAirExport()).toThrow(/futureDevices\/0/)
    delete p.constructions.futureDevices
    p.constructions.racks.racks.constructions = { futureDevices: [{ ...model('nested') }] }
    expect(() => new DomainProject(p as CfdProject).structuralForAirExport()).toThrow(/racks\/racks\/constructions\/futureDevices\/0/)
    expect(projectForSave(p as CfdProject).constructions).toEqual(p.constructions)
    p.constructions.racks.racks.constructions = { vendorNotes: { note: { text: 'not geometric' } } }
    expect(() => new DomainProject(p as CfdProject).structuralForAirExport()).not.toThrow()
  })

  it('refuses an unsupported kind in a known bucket rather than admitting the box export fallback', () => {
    const p = complete() as unknown as Record<string, any>
    p.constructions.boxes.boxes.meta.legacyKind = 'futureSolid'
    expect(() => new DomainProject(p as CfdProject).structuralForAirExport()).toThrow(/Unsupported entity/)
    expect(p.constructions.boxes.boxes.meta.legacyKind).toBe('futureSolid')
  })

  it('does not invent missing physical export UIDs from bucket keys', () => {
    const p = complete() as unknown as Record<string, any>
    delete p.constructions.boxes.boxes.uid
    expect(new DomainProject(p as CfdProject).topLevel(['boxes'])[0]?.identity.source).toBe('key')
    expect(() => new DomainProject(p as CfdProject).structuralForAirExport()).toThrow(/Missing stable uid/)
  })
})

describe('document extensions remain separate from strict solver snapshots', () => {
  it('retains JSON own __proto__ extensions without changing the output prototype', () => {
    const p = JSON.parse(JSON.stringify(complete()).replace('"schemaVersion":5',
      '"schemaVersion":5,"__proto__":{"vendor":"retained"}'))
    const saved = projectForSave(p)
    expect(Object.prototype.hasOwnProperty.call(saved, '__proto__')).toBe(true)
    expect(saved.__proto__).toEqual({ vendor: 'retained' })
    expect(Object.getPrototypeOf(saved)).toBe(Object.prototype)
    const reopened = JSON.parse(serializeProjectState(p))
    expect(reopened.__proto__).toEqual({ vendor: 'retained' })
    expect(Object.getPrototypeOf(reopened)).toBe(Object.prototype)
  })

  it.each(['__proto__', 'toString'])('treats an own %s bucket as unknown and preserves its identity', bucket => {
    const p = complete()
    const source = JSON.parse('{"' + bucket + '":{"f":{"uid":"f","meta":{"name":"Future"},"geometry":{}}}}')
    Object.defineProperty(p.constructions, bucket, { value: source[bucket], enumerable: true })
    const entity = new DomainProject(p).allEntities().find(e => e.type === bucket)
    expect(entity?.supported).toBe(false)
    expect(entity?.uid).toBe('f')
    expect(() => new DomainProject(p).structuralForAirExport()).toThrow(/Unsupported geometric bucket/)
    expect(projectForSave(p).constructions).toHaveProperty(bucket)
  })

  it('retains private-named JSON extensions but excludes only the established runtime bag', () => {
    const p = complete() as unknown as Record<string, any>
    p.__vendorExtension = { declared: true }
    p._customEngineeringNote = { note: 'retained' }
    p.__runtimeOnly = { threeJsHandle: 1 }
    const saved = projectForSave(p as CfdProject)
    expect(saved.__vendorExtension).toEqual({ declared: true })
    expect(saved._customEngineeringNote).toEqual({ note: 'retained' })
    expect(saved.__runtimeOnly).toBeUndefined()
    expect(saved.constructions).toEqual(p.constructions)
    expect(decodeDomainProject(saved).document).toBe(saved)
    expect(() => decodeDomainProject({ ...saved, schemaVersion: 6 })).toThrow(/Unsupported/)
  })

  it.each([4294967301, 5.5, -1, 'NaN', 'Infinity'])('returns a failed load for invalid schema version %s without narrowing or throwing', version => {
    const source = { ...complete(), schemaVersion: version }
    expect(() => readProjectFileText(JSON.stringify(source))).not.toThrow()
    expect(readProjectFileText(JSON.stringify(source))).toEqual({ ok: false, error: 'Unsupported schemaVersion ' + String(version) })
    expect(() => decodeDomainProject(source)).toThrow(/Unsupported schemaVersion/)
  })

  it('preserves actual v3 root and BOX own __proto__ fields through migration/save/reload without prototype mutation', () => {
    const raw = JSON.parse('{"schemaVersion":3,"meta":{"projectName":"proto"},"construction":[{"uid":"b","kind":"box","name":"Box","__proto__":{"vendorModel":"retained"}}],"simulation":{},"__proto__":{"vendorDocument":"retained"}}')
    const loaded = checkProjectDocument(raw)
    expect(loaded.ok).toBe(true)
    if (!loaded.ok) return
    const box = loaded.project.constructions!.boxes!.b!
    expect(Object.prototype.hasOwnProperty.call(loaded.project, '__proto__')).toBe(true)
    expect(Object.prototype.hasOwnProperty.call(box, '__proto__')).toBe(true)
    expect(Object.getPrototypeOf(loaded.project)).toBe(Object.prototype)
    expect(Object.getPrototypeOf(box)).toBe(Object.prototype)
    const reopened = readProjectFileText(serializeProjectState(loaded.project))
    expect(reopened.ok).toBe(true)
    if (!reopened.ok) return
    expect((reopened.project as Record<string, unknown>).__proto__).toEqual({ vendorDocument: 'retained' })
    expect((reopened.project.constructions!.boxes!.b! as Record<string, unknown>).__proto__).toEqual({ vendorModel: 'retained' })
    expect(Object.prototype).not.toHaveProperty('vendorDocument')
    expect(Object.prototype).not.toHaveProperty('vendorModel')
  })

  it('preserves own v2 root __proto__ and inherited-key-named extensions before v3 migration', () => {
    const raw = JSON.parse('{"schemaVersion":2,"cfdRoom":{},"models":[],"__proto__":{"vendorDocument":"v2"},"toString":{"vendor":"extension"}}')
    const loaded = checkProjectDocument(raw)
    expect(loaded.ok).toBe(true)
    if (!loaded.ok) return
    const saved = projectForSave(loaded.project)
    expect(Object.prototype.hasOwnProperty.call(saved, '__proto__')).toBe(true)
    expect(saved.__proto__).toEqual({ vendorDocument: 'v2' })
    expect(saved.toString).toEqual({ vendor: 'extension' })
    expect(Object.getPrototypeOf(saved)).toBe(Object.prototype)
  })

  it.each([
  {
    "name": "modelUID",
    "input": {
      "schemaVersion": 3,
      "meta": {
        "projectName": "uid edge case"
      },
      "construction": [
        {
          "uid": "__proto__",
          "kind": "box",
          "name": "Box"
        }
      ],
      "simulation": {}
    },
    "path": [
      "constructions",
      "boxes"
    ]
  },
  {
    "name": "serverUID",
    "input": {
      "schemaVersion": 3,
      "meta": {
        "projectName": "uid edge case"
      },
      "construction": [
        {
          "uid": "r",
          "kind": "rack",
          "name": "Rack",
          "rack": {
            "coolingType": "liquid",
            "equipments": [
              {
                "id": "__proto__",
                "type": "liquidServer",
                "name": "Server",
                "startUnit": 1,
                "spanUnits": 1,
                "liquidCooling": {
                  "cpCuPairs": [
                    {
                      "id": "cp",
                      "name": "Pair",
                      "Q_W": 100
                    }
                  ]
                }
              }
            ],
            "partitions": []
          }
        }
      ],
      "simulation": {}
    },
    "path": [
      "constructions",
      "racks",
      "r",
      "constructions",
      "servers"
    ]
  },
  {
    "name": "coldPlateUID",
    "input": {
      "schemaVersion": 3,
      "meta": {
        "projectName": "uid edge case"
      },
      "construction": [
        {
          "uid": "r",
          "kind": "rack",
          "name": "Rack",
          "rack": {
            "coolingType": "liquid",
            "equipments": [
              {
                "id": "s",
                "type": "liquidServer",
                "name": "Server",
                "startUnit": 1,
                "spanUnits": 1,
                "liquidCooling": {
                  "cpCuPairs": [
                    {
                      "id": "__proto__",
                      "name": "Pair",
                      "Q_W": 100
                    }
                  ]
                }
              }
            ],
            "partitions": []
          }
        }
      ],
      "simulation": {}
    },
    "path": [
      "constructions",
      "racks",
      "r",
      "constructions",
      "servers",
      "s",
      "constructions",
      "coldPlates"
    ]
  },
  {
    "name": "probeUID",
    "input": {
      "schemaVersion": 3,
      "meta": {
        "projectName": "uid edge case"
      },
      "construction": [],
      "simulation": {},
      "probeData": [
        {
          "id": "__proto__",
          "name": "P",
          "x": 0,
          "y": 0,
          "z": 0
        }
      ]
    },
    "path": [
      "instrumentation",
      "probes"
    ]
  },
  {
    "name": "measurementUID",
    "input": {
      "schemaVersion": 3,
      "meta": {
        "projectName": "uid edge case"
      },
      "construction": [],
      "simulation": {},
      "sensorData": [
        {
          "id": "__proto__",
          "name": "M"
        }
      ]
    },
    "path": [
      "instrumentation",
      "measurements"
    ]
  }
])('retains a literal __proto__ $name as an own dictionary entity through migration/save/reload', ({ input, path }) => {
    const loaded = checkProjectDocument(input)
    expect(loaded.ok).toBe(true)
    if (!loaded.ok) return
    const dictionary = path.reduce((value, key) => value[key], loaded.project as Record<string, any>)
    expect(Object.prototype.hasOwnProperty.call(dictionary, '__proto__')).toBe(true)
    expect(Object.getPrototypeOf(dictionary)).toBe(Object.prototype)
    const reopened = readProjectFileText(serializeProjectState(loaded.project))
    expect(reopened.ok).toBe(true)
    if (!reopened.ok) return
    const savedDictionary = path.reduce((value, key) => value[key], reopened.project as Record<string, any>)
    expect(savedDictionary.__proto__).toEqual(dictionary.__proto__)
    expect(Object.keys(savedDictionary)).toContain('__proto__')
    expect(Object.prototype).not.toHaveProperty('uid')
  })

  it.each(['futureSolid', 'toString', '__proto__'])('retains unknown v3 kind %s and refuses its physical export', kind => {
    const raw = { schemaVersion: 3, meta: { projectName: 'Future' }, construction: [{ uid: 'future', kind, name: 'Future' }], simulation: {} }
    expect(() => checkProjectDocument(raw)).not.toThrow()
    const loaded = checkProjectDocument(raw)
    expect(loaded.ok).toBe(true)
    if (!loaded.ok) return
    expect((loaded.project.constructions!.boxes!.future!.meta as unknown as Record<string, unknown>).legacyKind).toBe(kind)
    expect(new DomainProject(loaded.project).topLevel(['boxes'])[0]?.supported).toBe(false)
    expect(new DomainProject(loaded.project).topLevel(['boxes'])[0]?.kind).toBe(kind)
    expect(() => new DomainProject(loaded.project).structuralForAirExport()).toThrow(/Unsupported entity/)
    const reopened = readProjectFileText(serializeProjectState(loaded.project))
    expect(reopened.ok).toBe(true)
    if (reopened.ok) expect((reopened.project.constructions!.boxes!.future!.meta as unknown as Record<string, unknown>).legacyKind).toBe(kind)
  })

  it.each([
  {
    "name": "coldPlateSpec",
    "input": {
      "schemaVersion": 3,
      "meta": {
        "projectName": "dict preservation"
      },
      "construction": [],
      "simulation": {},
      "liquidCooling": {
        "specifications": {
          "coldPlateSpec": [
            {
              "name": "__proto__",
              "R_th": 0.03
            }
          ]
        },
        "fluid": {
          "type": "__proto__",
          "rho": 997,
          "cp": 4180
        }
      }
    },
    "path": [
      "models",
      "coolingModels",
      "coldPlates"
    ]
  },
  {
    "name": "fluid.type",
    "input": {
      "schemaVersion": 3,
      "meta": {
        "projectName": "dict preservation"
      },
      "construction": [],
      "simulation": {},
      "liquidCooling": {
        "specifications": {
          "coldPlateSpec": [
            {
              "name": "__proto__",
              "R_th": 0.03
            }
          ]
        },
        "fluid": {
          "type": "__proto__",
          "rho": 997,
          "cp": 4180
        }
      }
    },
    "path": [
      "models",
      "materialModels",
      "fluids"
    ]
  }
])('preserves the existing migration $name dictionary own __proto__ definition', ({ input, path }) => {
    const loaded = checkProjectDocument(input)
    expect(loaded.ok).toBe(true)
    if (!loaded.ok) return
    const dictionary = path.reduce((value, key) => value[key], loaded.project as Record<string, any>)
    expect(Object.prototype.hasOwnProperty.call(dictionary, '__proto__')).toBe(true)
    expect(Object.getPrototypeOf(dictionary)).toBe(Object.prototype)
    const reopened = readProjectFileText(serializeProjectState(loaded.project))
    expect(reopened.ok).toBe(true)
    if (reopened.ok) {
      const savedDictionary = path.reduce((value, key) => value[key], reopened.project as Record<string, any>)
      expect(Object.keys(savedDictionary)).toContain('__proto__')
      expect(savedDictionary.__proto__).toEqual(dictionary.__proto__)
    }
  })

  it('keeps existing migration server physics matched by own equipmentId and rejects inherited phantom rows', () => {
    const raw = {
  "schemaVersion": 3,
  "meta": {
    "projectName": "physics lookup"
  },
  "construction": [
    {
      "uid": "r",
      "kind": "rack",
      "rack": {
        "equipments": [
          {
            "id": "__proto__",
            "name": "__proto__",
            "type": "fanServer",
            "startUnit": 1,
            "spanUnits": 1
          },
          {
            "id": "server_p",
            "name": "server_p",
            "type": "fanServer",
            "startUnit": 1,
            "spanUnits": 1
          }
        ]
      },
      "cfd": {
        "servers": [
          {
            "equipmentId": "__proto__",
            "volumeFlowRate": 0.1,
            "server_p": {
              "volumeFlowRate": 0.42,
              "fixedTemperature": 48
            }
          }
        ]
      }
    }
  ],
  "simulation": {}
}
    const loaded = checkProjectDocument(raw)
    expect(loaded.ok).toBe(true)
    if (!loaded.ok) return
    const servers = (loaded.project as Record<string, any>).constructions.racks.r.constructions.servers
    expect(servers.__proto__.cooling).toEqual({
      volumeFlowRate: .1, server_p: { volumeFlowRate: .42, fixedTemperature: 48 },
    })
    expect(servers.server_p.cooling).toEqual({})
    expect(Object.getPrototypeOf(servers)).toBe(Object.prototype)
    expect(Object.prototype).not.toHaveProperty('volumeFlowRate')
  })

  it('preserves legacy root extensions through the actual v3 migration and save', () => {
    const loaded = checkProjectDocument({ schemaVersion: 3, meta: { projectName: 'Legacy' }, construction: [],
      vendorDocument: { retained: 7 }, simulation: {} })
    expect(loaded.ok).toBe(true)
    if (loaded.ok) expect(projectForSave(loaded.project).vendorDocument).toEqual({ retained: 7 })
  })

  it.each(['LiquidSingle/LiquidSingle', 'Shinaide_Liquid/Shinaide_liquid'])('preserves actual %s graph bindings/physics and omits project extensions from liquidrun', name => {
    const p = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/' + name + '.cfdproj'), 'utf8'))
    const baseline = compileDynamicLiquidGraph(p)
    p.vendorDocument = { futureVolume: 'retained-not-executed' }
    const loaded = checkProjectDocument(JSON.parse(serializeProjectState(p)))
    expect(loaded.ok).toBe(true)
    if (!loaded.ok) return
    const compiled = compileDynamicLiquidGraph(loaded.project)
    expect(compiled.nodes).toEqual(baseline.nodes)
    expect(compiled.edges).toEqual(baseline.edges)
    expect(compiled.object_bindings).toEqual(baseline.object_bindings)
    expect(compiled).not.toHaveProperty('vendorDocument')
  })

  it('omits a project extension from the real strict air manifest producer', async () => {
    const p = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj'), 'utf8'))
    async function air(source: CfdProject) {
      const current = liveProject(stateV4().normalizeStateV4(structuredClone(source)) as unknown as CfdProject, 0)
      const globalGroup = new THREE.Group(), wrapperGroup = new THREE.Group()
      wrapperGroup.add(globalGroup)
      const globals = window as unknown as { allModels?: () => import('../src/schema/cfdproj').Construction[] }
      const previous = globals.allModels
      globals.allModels = () => stateV4().allModels(current) as import('../src/schema/cfdproj').Construction[]
      const builder = createSceneBuilder({ globalGroup, wrapperGroup, registries: emptyRegistries(),
        selectedUid: () => null, project: () => current, webglAvailable: false, previewMode: () => false })
      try {
        builder.rebuild()
        wrapperGroup.updateMatrixWorld(true)
        return await buildManifestV2(createCfdrunBuild({ state: current, globalGroup,
          LENGTH_UNITS: { m: { scale: 1, label: 'm', step: .0001, precision: 4 } },
          _MESH_MODES: ['bodyFitted', 'cartesianLocalized', 'structured'],
          buildGroupedStlMap: (uids, leakage) => builder.api.buildGroupedStlMap(uids, leakage),
        }), current, '2026-10-06T00:00:00.000Z')
      } finally {
        builder.clear()
        if (previous) globals.allModels = previous
        else delete globals.allModels
      }
    }
    const baseline = await air(p)
    p.vendorDocument = { declared: true }
    const after = await air(p)
    expect(after).not.toHaveProperty('vendorDocument')
    expect(after.inputs).toEqual(baseline.inputs)
    expect(after.boundaryBindings).toEqual(baseline.boundaryBindings)
    expect(after.geometryExport).toEqual(baseline.geometryExport)
    expect(cfdrunExport().projectContentHashSync(p)).not.toBe(baseline.sourceProject?.contentHash)
  })
})
