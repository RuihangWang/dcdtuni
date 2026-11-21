import { useEffect, useState } from 'react'
import { allModels, type CfdProject, type Construction } from '../schema/cfdproj'
import { toDisplay, unitOf } from '../lengthUnits'
import { exportFileNameFor } from '../io/singleModelExport'
import {
  previewCache,
  previewEntry,
  previewFlags,
  previewModelFullyVisible,
  previewModelVisible,
  refreshPreview,
  selectedPreviewRegion,
  selectPreviewRegion,
  setPreviewActive,
  subscribePreview,
  togglePreviewModelVisible,
  togglePreviewRegionReverse,
  togglePreviewRegionVisible,
} from '../io/stlPreview'
import { isPreviewRegionReversed, isPreviewRegionVisible, previewSummary } from '../io/stlPreviewState'
import { useTranslation } from '../useTranslation'

/**
 * The right panel while STL Preview is on.
 *
 * Three panels, one per selection depth, ported from
 * `renderStlPreviewRootInspector` / `…ModelInspector` / `…RegionInspector`
 * (STL_generate.html:11419, 11442, 11466). Everything they show is a fact
 * about the file that WOULD be written — solid names, region counts, triangle
 * counts, the export filename — which is the only place in either editor those
 * numbers appear before a mesh run.
 */

export interface StlPreviewInspectorProps {
  project: CfdProject
  selectedUid: string | null
}

export function StlPreviewInspector({ project, selectedUid }: StlPreviewInspectorProps) {
  // The cache is filled during the scene rebuild, so this panel has to follow
  // the preview store, not just the project.
  const [, bump] = useState(0)
  useEffect(() => subscribePreview(() => bump((n) => n + 1)), [])

  const region = selectedPreviewRegion()
  const model = selectedUid
    ? allModels(project).find(({ model: m }) => m.uid === selectedUid)?.model
    : undefined

  if (model && region && region.modelId === model.uid) {
    return <PreviewRegionPanel project={project} model={model} regionName={region.regionName} />
  }
  if (model) return <PreviewModelPanel model={model} />
  return <PreviewRootPanel project={project} />
}

function BackToModelling() {
  const { t } = useTranslation()
  return (
    <button className="btn frozen-ok" id="preview-back-to-modeling" onClick={() => setPreviewActive(false)}>
      {t('btn.back_to_modeling')}
    </button>
  )
}

function PreviewRootPanel({ project }: { project: CfdProject }) {
  const { t } = useTranslation()
  const models = allModels(project)
  const entries = [...previewCache().values()]
  const totals = previewSummary(entries)
  const visible = models.filter(({ model }) => previewModelVisible(model.uid)).length

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
        <div className="prop-title" style={{ color: '#16825d' }}>
          STL Preview
        </div>
        <div className="prop-hint">{t('preview.root_desc')}</div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('preview.summary')}</div>
        <div className="prop-row">
          <label>{t('preview.model_count')}</label>
          <span className="value" id="preview-model-count">
            {models.length}
          </span>
        </div>
        <div className="prop-row">
          <label>{t('preview.visible_count')}</label>
          <span className="value" id="preview-visible-count">
            {visible}
          </span>
        </div>
        <div className="prop-row">
          <label>Solid Regions</label>
          <span className="value" id="preview-region-total">
            {totals.regions}
          </span>
        </div>
        <div className="prop-row">
          <label>Triangles</label>
          <span className="value" id="preview-triangle-total">
            {totals.triangles}
          </span>
        </div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('preview.actions')}</div>
        <BackToModelling />
        <button
          className="btn primary frozen-ok"
          id="preview-regenerate"
          style={{ background: '#0e639c' }}
          onClick={() => refreshPreview()}
        >
          {t('btn.regenerate_preview')}
        </button>
      </div>
    </>
  )
}

function PreviewModelPanel({ model }: { model: Construction }) {
  const { t } = useTranslation()
  const entry = previewEntry(model.uid)
  const regionCount = entry?.regions.length ?? 0

  return (
    <>
      <div className="prop-group">
        <div className="prop-title">{t('preview.model_title')}</div>
        <div className="prop-row">
          <label>{t('preview.parent_model')}</label>
          <span className="value">{model.meta?.name ?? model.uid}</span>
        </div>
        <div className="prop-hint">{t('preview.model_desc')}</div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('preview.export_info')}</div>
        <div className="prop-row">
          <label>{t('preview.model_type')}</label>
          <span className="value" id="preview-model-kind">
            {String(model.kind ?? '')}
          </span>
        </div>
        <div className="prop-row">
          <label>{t('preview.export_filename')}</label>
          <span className="value" id="preview-export-filename" style={{ color: 'var(--hint-color)' }}>
            {exportFileNameFor(model)}
          </span>
        </div>
        <div className="prop-row">
          <label>Solid Regions</label>
          <span className="value" id="preview-model-regions">
            {regionCount}
          </span>
        </div>
        <div className="prop-row">
          <label>Triangles</label>
          <span className="value" id="preview-model-triangles">
            {entry?.triangleCount ?? 0}
          </span>
        </div>
        <div className="prop-row">
          <label htmlFor="preview-show-model">{t('preview.show_model')}</label>
          <input
            type="checkbox"
            id="preview-show-model"
            checked={previewModelFullyVisible(model.uid)}
            onChange={(e) => togglePreviewModelVisible(model.uid, e.target.checked)}
          />
        </div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('preview.actions')}</div>
        <BackToModelling />
        <button
          className="btn primary frozen-ok"
          id="preview-refresh"
          style={{ background: '#0e639c' }}
          onClick={() => refreshPreview()}
        >
          {t('btn.refresh_preview')}
        </button>
      </div>
    </>
  )
}

function PreviewRegionPanel({
  project,
  model,
  regionName,
}: {
  project: CfdProject
  model: Construction
  regionName: string
}) {
  const { t } = useTranslation()
  const entry = previewEntry(model.uid)
  const region = entry?.regions.find((r) => r.name === regionName)
  if (!region) return <PreviewModelPanel model={model} />

  const unit = unitOf(project)
  const box = region.geometry.boundingBox
  const size = box
    ? { x: box.max.x - box.min.x, y: box.max.y - box.min.y, z: box.max.z - box.min.z }
    : { x: 0, y: 0, z: 0 }
  const flags = previewFlags()

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: region.color }}>
        <div className="prop-title" style={{ color: region.color }}>
          {t('preview.region_title')}
        </div>
        <div className="prop-row">
          <label>Solid</label>
          <span className="value" id="preview-region-name">
            {region.name}
          </span>
        </div>
        <div className="prop-hint">{t('preview.region_desc')}</div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('preview.region_info')}</div>
        <div className="prop-row">
          <label>{t('preview.parent_model')}</label>
          <span className="value">{model.meta?.name ?? model.uid}</span>
        </div>
        <div className="prop-row">
          <label>Triangles</label>
          <span className="value" id="preview-region-triangles">
            {region.triangleCount}
          </span>
        </div>
        <div className="prop-row">
          <label htmlFor="preview-show-region">{t('preview.show_region')}</label>
          <input
            type="checkbox"
            id="preview-show-region"
            checked={isPreviewRegionVisible(flags, model.uid, region.name)}
            onChange={(e) => togglePreviewRegionVisible(model.uid, region.name, e.target.checked)}
          />
        </div>
        {/* Not a view setting. `applyModelSolidOrientationOverrides` reads this
            same flag on every export path, so ticking it swaps two vertices of
            every facet in this solid — which is what tells snappyHexMesh which
            side of the surface the fluid is on. */}
        <div className="prop-row">
          <label htmlFor="preview-reverse-normal">{t('preview.reverse_normal')}</label>
          <input
            type="checkbox"
            id="preview-reverse-normal"
            checked={isPreviewRegionReversed(flags, model.uid, region.name)}
            onChange={(e) => togglePreviewRegionReverse(model.uid, region.name, e.target.checked)}
          />
        </div>
        {(
          [
            ['x', 'preview-bbox-w'],
            ['y', 'preview-bbox-d'],
            ['z', 'preview-bbox-h'],
          ] as const
        ).map(([axis, id]) => (
          <div className="prop-row" key={axis}>
            <label>
              BBox {axis.toUpperCase()} ({unit})
            </label>
            <span className="value" id={id}>
              {toDisplay(size[axis], unit).toFixed(4)}
            </span>
          </div>
        ))}
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('preview.actions')}</div>
        <button
          className="btn frozen-ok"
          id="preview-view-parent"
          onClick={() => selectPreviewRegion(null)}
        >
          {t('btn.view_parent_model')}
        </button>
        <button
          className="btn primary frozen-ok"
          id="preview-refresh"
          style={{ background: '#0e639c' }}
          onClick={() => refreshPreview()}
        >
          {t('btn.refresh_preview')}
        </button>
      </div>
    </>
  )
}
