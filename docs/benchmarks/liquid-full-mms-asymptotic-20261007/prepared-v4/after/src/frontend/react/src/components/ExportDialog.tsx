import { useMemo, useState } from 'react'
import {
  buildCfdPackage,
  defaultPackageName,
  downloadZip,
  rememberPackage,
  remapStlFiles,
} from '../io/cfdPackage'
import { allModels, type CfdProject } from '../schema/cfdproj'
import { buildManifestV2, createCfdrunBuild } from '../legacy/cfdrunBuild'
import { stlNaming } from '../legacy/stlNaming'
import { buildStlFiles, downloadText, duplicateSolidNames } from '../io/stlExport'
import type { SceneBuilder } from '../interaction/sceneBuilder'
import { anyLeakage, exportRowBadges, rememberExportFileName } from '../io/exportRows'
import { runBlocking } from '../run/busyOverlay'
import { useTranslation } from '../useTranslation'
import { closeModelGaps, findModelGaps } from '../legacy/transformTools'
import * as store from '../projectStore'

/**
 * The STL / CFD-package export dialog.
 *
 * Replicates `exportMergedSTL`'s dialog and `exportCFDProject`
 * (STL_generate.html:29252, 29632): pick the models, choose grouped or merged,
 * include the rack leakage zones or not, name the file.
 *
 * ## Duplicate solid names REFUSE the export
 *
 * Two solids with the same name collapse into one OpenFOAM patch, so a boundary
 * condition meant for one lands on both and the case solves a different
 * problem — silently, with a mesh that looks correct. The legacy exporter
 * refuses on this and so does this dialog.
 *
 * ## The package is ONE file
 *
 * `.cfdrun` and its merged STL files are useless apart — the manifest names
 * files that have to sit beside it — so the package mode zips them under
 * `<project>_cfd/` the way `exportCFDProject` does, rather than firing a dozen
 * separate downloads a user has to reassemble.
 *
 * ## Grouped is the default
 *
 * The runner reads merged per-kind files (`merged_rack.stl`, `merged_acu.stl`),
 * so a single merged STL is the odd case — useful for inspecting a room in an
 * external viewer, not for meshing it.
 */

export interface ExportDialogProps {
  project: CfdProject
  builder: SceneBuilder
  mode: 'stl' | 'package'
  onClose(): void
}

export function ExportDialog({ project, builder, mode, onClose }: ExportDialogProps) {
  const { t } = useTranslation()
  const models = useMemo(() => allModels(project).map(({ model }) => model), [project])
  // The seams a drag left behind: neighbouring nominal boxes 5 mm or less
  // apart. Shown here because export is where a 1 mm gap becomes a sliver
  // channel in the mesh; closing moves the smaller model onto its neighbour.
  const gaps = useMemo(
    () => findModelGaps(models.filter((m) => m.meta?.enabled !== false) as never[], 0.005),
    [models],
  )
  const closeGaps = () => {
    store.edit('closeModelGaps', `${gaps.length} gap(s)`, (draft) => {
      const live = allModels(draft as CfdProject)
        .map(({ model }) => model)
        .filter((m) => m.meta?.enabled !== false)
      closeModelGaps(live as never[], 0.005)
    })
  }
  const [selected, setSelected] = useState<Set<string>>(() => new Set(models.map((m) => m.uid)))
  const [grouped, setGrouped] = useState(true)
  const [includeLeakage, setIncludeLeakage] = useState(true)
  // The package is named after the PROJECT and the STL after the export file:
  // `exportCFDProject` prompts with `state.meta.projectName` (:29171) while
  // `exportMergedSTL` uses `exportFileName`, and a package called `default.zip`
  // is what the two being conflated looks like.
  const meta = project.meta as { projectName?: string; exportFileName?: string } | undefined
  const [name, setName] = useState(() =>
    mode === 'package' ? defaultPackageName(meta) : meta?.exportFileName || 'default',
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ids = [...selected]
  const hasLeakage = useMemo(() => anyLeakage(models), [models])
  const allSelected = models.length > 0 && selected.size === models.length

  async function run() {
    setError(null)
    if (!ids.length) {
      setError(t('error.select_at_least_one'))
      return
    }
    const duplicates = duplicateSolidNames(builder, project, ids)
    if (duplicates.length) {
      setError(
        `${t('error.duplicate_solid_names', { details: '' })} ` +
          duplicates.map((d) => `"${d.name}" => ${d.sources.join(', ')}`).join('; '),
      )
      return
    }
    setBusy(true)
    // The name the user typed becomes the project's, as `confirmExportMergedSTL`
    // does (STL_generate.html:29800) — otherwise the box resets to 'default' on
    // every export and a project-specific prefix has to be retyped each time.
    if (mode === 'stl') rememberExportFileName(name)
    try {
      // Behind the blocking overlay: the tessellation and the CSG passes below
      // run synchronously on the main thread, so on a large project the dialog
      // simply stops responding. The legacy raises `#loading` around exactly
      // these two paths (:29178 and :29805).
      const done = await runBlocking(async () => {
      const safe = stlNaming().sanitizeName(name.trim() || 'default') || 'default'
      if (mode === 'package') {
        if (!models.length) {
          setError(t('error.no_models_to_export'))
          return false
        }
        // The same context the legacy page hands the builder: the project, the
        // scene root (world matrices), the unit table and the mesh-mode list.
        const api = createCfdrunBuild({
          state: project,
          globalGroup: builder.registries.renderRoots[0]?.parent ?? null,
          LENGTH_UNITS: {
            mm: { scale: 0.001, label: 'mm', step: 0.1, precision: 4 },
            cm: { scale: 0.01, label: 'cm', step: 0.01, precision: 4 },
            m: { scale: 1, label: 'm', step: 0.0001, precision: 4 },
          },
          _MESH_MODES: ['bodyFitted', 'cartesianLocalized', 'structured'],
          buildGroupedStlMap: (modelIds, leakage) =>
            builder.api.buildGroupedStlMap(modelIds, leakage),
        })
        // Plain ISO-8601, as the legacy export passes: this string lands in
        // `sourceProject.exportedAt` and `runId`, and a sanitised variant would
        // give the two apps different-SHAPED data, not just different values.
        const stamp = new Date().toISOString()
        const manifest = await buildManifestV2(api, project, stamp)
        // The name the user typed wins over whatever the project was built
        // with, exactly as the legacy prompt does (STL_generate.html:29188).
        const meshGeometry = manifest.meshGeometry as Record<string, unknown> | undefined
        let remapped: ReturnType<typeof remapStlFiles> = []
        if (meshGeometry) {
          meshGeometry.projectName = safe
          remapped = remapStlFiles(meshGeometry)
        }
        // Every model, leakage included: a model hidden in the tree still has
        // to reach the mesh or its patch vanishes from the case.
        const files = buildStlFiles(builder, project, true).files
        const pkg = buildCfdPackage(safe, manifest, files, new Date(), project)
        rememberPackage({ ...pkg, manifest, remapped })
        downloadZip(pkg.filename, pkg.bytes)
      } else if (grouped) {
        const files = buildStlFiles(builder, project, includeLeakage).files
        const entries = Object.entries(files)
        if (!entries.length) {
          setError(t('error.no_valid_geometry'))
          return false
        }
        const prefix = safe && safe !== 'default' ? `${safe}_` : ''
        entries.forEach(([filename, stl], i) => {
          setTimeout(() => downloadText(prefix + filename, stl), i * 150)
        })
      } else {
        const exportModels = builder.api.exportModelsToStl as (
          ids: string[],
          leakage: boolean,
        ) => string
        const stl = exportModels(ids, includeLeakage)
        downloadText(`${safe}.stl`, stl)
      }
      return true
      })
      // Only on success: a dialog that closes over its own error message leaves
      // the user with nothing to read.
      if (done) onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div id="export-stl-dialog" className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">
          {mode === 'package' ? t('btn.export_cfd_package') : t('export.dialog_title')}
        </div>
        <div className="prop-hint">{t('export.dialog_desc')}</div>
        <div className="prop-row" id="export-gap-audit">
          <label style={{ color: gaps.length ? '#ffaa00' : undefined }}>
            {gaps.length
              ? t('log.gap_audit_found', { n: gaps.length })
              : t('log.gap_audit_none')}
          </label>
          {gaps.length > 0 && (
            <button className="btn" id="export-close-gaps" onClick={closeGaps} title={t('tooltip.gap_close')}>
              {t('btn.gap_close')}
            </button>
          )}
        </div>

        <div className="prop-row">
          <label htmlFor="export-stl-name">
            {mode === 'package' ? t('dialog.enter_project_name_export') : t('export.stl_name_label')}
          </label>
          <input
            id="export-stl-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        {mode === 'stl' && (
          <>
            {/* Grouped export turns the name into a PREFIX rather than the
                filename, and grouped is this app's default — so without the
                swap a user typing "MyRoom" expects MyRoom.stl and gets six
                files called MyRoom_merged_*.stl. `_onExportGroupedToggle`
                (STL_generate.html:29788) swaps the same two strings. */}
            <div className="prop-hint" id="export-name-hint">
              {grouped ? t('export.stl_name_hint_grouped') : t('export.stl_name_hint')}
            </div>
            <div className="prop-row">
              <label htmlFor="export-grouped">{t('export.grouped_export')}</label>
              <input
                type="checkbox"
                id="export-grouped"
                checked={grouped}
                onChange={(e) => setGrouped(e.target.checked)}
              />
            </div>
            {grouped && (
              <div className="prop-hint" id="export-grouped-hint" style={{ paddingLeft: 22 }}>
                {t('export.grouped_hint')}
              </div>
            )}
            {/* Only when a rack actually has through-zones, as the legacy is
                (:29768): a checkbox that cannot change the output is worse than
                no checkbox, because unticking it looks like it did something. */}
            {hasLeakage && (
              <div className="prop-row">
                <label htmlFor="export-include-leakage">{t('export.include_leakage')}</label>
                <input
                  type="checkbox"
                  id="export-include-leakage"
                  checked={includeLeakage}
                  onChange={(e) => setIncludeLeakage(e.target.checked)}
                />
              </div>
            )}
          </>
        )}

        <div className="export-model-list">
          <div className="export-model-actions">
            <label className="export-model-row" htmlFor="export-toggle-all">
              <input
                type="checkbox"
                id="export-toggle-all"
                checked={allSelected}
                onChange={(e) =>
                  setSelected(e.target.checked ? new Set(models.map((m) => m.uid)) : new Set())
                }
              />
              <span style={{ color: 'var(--section-header-block)' }}>{t('export.toggle_all')}</span>
            </label>
            <button id="export-select-all" onClick={() => setSelected(new Set(models.map((m) => m.uid)))}>
              {t('common.select_all')}
            </button>
            <button id="export-select-none" onClick={() => setSelected(new Set())}>
              {t('common.deselect_all')}
            </button>
          </div>
          {models.map((m) => {
            const badges = exportRowBadges(m)
            return (
              <label className="export-model-row" key={m.uid}>
                <input
                  type="checkbox"
                  className="export-model-cb"
                  data-model-id={m.uid}
                  checked={selected.has(m.uid)}
                  onChange={(e) =>
                    setSelected((prev) => {
                      const next = new Set(prev)
                      if (e.target.checked) next.add(m.uid)
                      else next.delete(m.uid)
                      return next
                    })
                  }
                />
                <span>{m.meta?.name ?? m.uid}</span>
                <span className="dim">{String(m.kind ?? '')}</span>
                {/* Two facts a name cannot carry: this rack exports extra
                    leakage solids, and this model is hidden in the viewport but
                    exported anyway. */}
                {badges.leakGroups > 0 && (
                  <span className="export-badge-leak" style={{ color: '#e5a020', fontSize: '0.75rem' }}>
                    [{badges.leakGroups} {t('export.leak_groups')}]
                  </span>
                )}
                {badges.hidden && (
                  <span className="export-badge-hidden" style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
                    [{t('export.hidden')}]
                  </span>
                )}
              </label>
            )
          })}
        </div>

        {error && <div className="prop-hint export-error">{error}</div>}

        <div className="modal-buttons">
          <button className="btn" onClick={onClose}>
            {t('export.cancel')}
          </button>
          <button className="btn primary" id="export-confirm" disabled={busy} onClick={() => void run()}>
            {busy ? t('status.loading') : t('export.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}
