import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { expect, it } from 'vitest'
import * as store from '../src/projectStore'
import { serializeProjectState } from '../src/io/projectFiles'
import { adoptMockDAEExperiment, readExperiment } from '../src/liquid/liquidExperiment'
import { cfdrunExport } from '../src/legacy/cfdrunBuild'
import { compileProjectDocument } from '../../../../tools/compile_cfdproj_liquidrun_v3_entry'
import type { CfdProject } from '../src/schema/cfdproj'

const repo = resolve(process.cwd(), '../../..')
const basePath = join(repo, 'case/LiquidSingle_Control/LiquidSingle_Control.cfdproj')
const fixturePath = join(repo, 'src/frontend/react/test/fixtures/cases/LiquidSingle_Control_dae_v3.cfdproj')
const cli = join(repo, 'tools/compile_cfdproj_liquidrun_v3.mjs')
const sha256 = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex')

function savedSmallProject(): CfdProject {
  const base = JSON.parse(readFileSync(basePath, 'utf8')) as CfdProject
  store.loadProject(base, null)
  // This is the frozen saved-v3 research fixture, whose explicit independent
  // clock must remain compatible rather than inheriting the new default intent.
  const legacy=readExperiment(store.getProject())
  delete legacy.engine
  store.setRunConfig('liquid','experiment',legacy)
  const adopted = adoptMockDAEExperiment(store.getProject()!, {
    source_reference: 'Synthetic one-rack test fixture, not site measurements',
  })
  store.setRunConfig('liquid', 'experiment', adopted)
  return JSON.parse(serializeProjectState(store.getProject()!)) as CfdProject
}

// The v4 fixture is frozen: benchD (ddl/case/benchD_v3, at the tag agent-pre-slim-2026-10-22) and its tracked run
// records pin it. What the editor saves today (cfdproj v5) is the v5 golden.
const fixtureV5Path = join(repo, 'src/frontend/react/test/fixtures/cases/LiquidSingle_Control_dae_v5.cfdproj')
// Saving now records the bounded CPU execution preference explicitly.
// Thread count changes scheduling; backend, device and dtype must still match.
const withoutSourceIdentityAndCpuThreadCount = (m: any) => ({ ...m,
  source_project: { ...m.source_project, sha256: '', schema_version: 0 },
  execution: { ...m.execution, max_threads: 0 },
})

it('compiles one saved small engineering project through the real React graph compiler', () => {
  const saved = savedSmallProject()
  if (process.env.RECORD_LIQUID_SINGLE_DAE_V5 === '1') {
    writeFileSync(fixtureV5Path, JSON.stringify(saved, null, 2) + '\n')
  }
  const golden = JSON.parse(readFileSync(fixtureV5Path, 'utf8')) as CfdProject
  expect(golden).toEqual(saved)
  expect((golden as { schemaVersion?: number }).schemaVersion).toBe(5)
  const v5manifest = compileProjectDocument(golden)
  expect(v5manifest.source_project.schema_version).toBe(5)
  // The frozen v4 file and its v5 save compile to the same physics.
  const fixture = JSON.parse(readFileSync(fixturePath, 'utf8')) as CfdProject
  const frozenManifest = compileProjectDocument(fixture)
  expect(frozenManifest.execution).toEqual({backend: 'pytorch', device: 'cpu', dtype: 'float64', max_threads: 1})
  expect(v5manifest.execution).toEqual({backend: 'pytorch', device: 'cpu', dtype: 'float64', max_threads: 2})
  expect(withoutSourceIdentityAndCpuThreadCount(frozenManifest)).toEqual(withoutSourceIdentityAndCpuThreadCount(v5manifest))
  const before = JSON.stringify(fixture)
  const manifest = compileProjectDocument(fixture)
  expect(JSON.stringify(fixture)).toBe(before)
  expect(manifest.schemaVersion).toBe(3)
  expect(manifest.source_project.project_uid).toBe(fixture.uid)
  expect(manifest.source_project.sha256).toBe(cfdrunExport().projectContentHashSync(fixture))
  expect(manifest.nodes.length).toBeGreaterThan(0)
  expect(manifest.edges.length).toBeGreaterThan(0)
  expect(() => compileProjectDocument(JSON.parse(readFileSync(basePath, 'utf8')))).toThrow(/saved DAE V3/)
  const malformed = structuredClone(fixture) as any
  malformed.runDefaults.liquid.experiment.dae.primary_circuits = []
  expect(() => compileProjectDocument(malformed)).toThrow(/primary_circuits|heat.exchanger|HX/)
})

it('CLI emits one deterministic v3 manifest with separate file, project and output hashes', () => {
  mkdirSync(join(repo, '.codex-tmp'), { recursive: true })
  const temp = mkdtempSync(join(repo, '.codex-tmp/liquidrun-v3-test-'))
  const output = join(temp, 'small.liquidrun')
  try {
    const sourceBytes = readFileSync(fixturePath)
    const report = JSON.parse(execFileSync(process.execPath,
      [cli, '--project', fixturePath, '--output', output], { encoding: 'utf8' }))
    const outputBytes = readFileSync(output)
    const manifest = JSON.parse(outputBytes.toString('utf8'))
    expect(report.source_file_sha256).toBe(sha256(sourceBytes))
    expect(report.project_content_sha256).toBe(manifest.source_project.sha256)
    expect(report.project_content_sha256).not.toBe(report.source_file_sha256)
    expect(report.output_sha256).toBe(sha256(outputBytes))
    expect(report.schema_version).toBe(3)
    expect(manifest).toEqual(compileProjectDocument(JSON.parse(sourceBytes.toString('utf8'))))
    const replay = JSON.parse(execFileSync(process.execPath,
      [cli, '--project', fixturePath, '--output', output, '--check'], { encoding: 'utf8' }))
    expect(replay.checked).toBe(true)
    expect(replay.output_sha256).toBe(report.output_sha256)
    expect(readFileSync(fixturePath)).toEqual(sourceBytes)
    const changed = JSON.parse(sourceBytes.toString('utf8')) as any
    changed.constructions.pipeSegments.m_3.geometry.diameterM = .08
    const changedPath = join(temp, 'changed.cfdproj')
    const changedOutput = join(temp, 'changed.liquidrun')
    writeFileSync(changedPath, JSON.stringify(changed, null, 2) + '\n')
    const variant = JSON.parse(execFileSync(process.execPath,
      [cli, '--project', changedPath, '--output', changedOutput], { encoding: 'utf8' }))
    const changedManifest = JSON.parse(readFileSync(changedOutput, 'utf8'))
    expect(variant.source_file_sha256).not.toBe(report.source_file_sha256)
    expect(variant.project_content_sha256).not.toBe(report.project_content_sha256)
    expect(variant.output_sha256).not.toBe(report.output_sha256)
    const m3 = changedManifest.edges.filter((edge: any) => edge.type === 'pipe' && edge.id.startsWith('m_3_'))
    expect(m3.length).toBeGreaterThan(0)
    expect(m3.every((edge: any) => edge.params.D_m === .08)).toBe(true)
    expect(changedManifest.edges.filter((edge: any) => edge.type === 'pipe' && edge.id.startsWith('m_4_'))
      .every((edge: any) => edge.params.D_m === .1)).toBe(true)
    const legacyOutput = join(temp, 'legacy.liquidrun')
    expect(() => execFileSync(process.execPath,
      [cli, '--project', basePath, '--output', legacyOutput], { stdio: 'pipe' }))
      .toThrow()
    expect(existsSync(legacyOutput)).toBe(false)
  } finally {
    rmSync(temp, { recursive: true, force: true })
  }
})
