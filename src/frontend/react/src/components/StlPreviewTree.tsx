import { useEffect, useState } from 'react'
import { Glyph, Icon } from './Icon'
import { CATEGORIES, type CfdProject, type Construction, modelsIn } from '../schema/cfdproj'
import {
  previewEntry,
  previewFlags,
  previewModelFullyVisible,
  previewModelVisible,
  selectedPreviewRegion,
  selectPreviewRegion,
  subscribePreview,
  togglePreviewModelVisible,
  togglePreviewRegionVisible,
} from '../io/stlPreview'
import { isPreviewRegionVisible } from '../io/stlPreviewState'
import { useTranslation } from '../useTranslation'

/**
 * The tree while STL Preview is on: solids, not faces.
 *
 * `updateStlPreviewUI` (STL_generate.html:11517) replaces the whole geometry
 * tree in this mode, and so does this. The rows below a model are not its
 * faces or its patches — they are the `solid` blocks the exporter would write,
 * each with its triangle count and the colour it is drawn in. That is the
 * point of the mode: the modelling tree cannot show them, because they do not
 * exist until the exporter runs.
 *
 * The DOM ids are the modelling tree's (`tree-header-title`, `mode-badge`,
 * `tree-container`): the legacy reuses the same elements for both modes, and
 * the parity harness diffs ids.
 */

export interface StlPreviewTreeProps {
  project: CfdProject | null
  selectedUid: string | null
  onSelect: (uid: string | null) => void
}

export function StlPreviewTree({ project, selectedUid, onSelect }: StlPreviewTreeProps) {
  const { t } = useTranslation()
  // The cache is filled by the scene builder during a rebuild, so this panel
  // has to re-render when the preview does — otherwise a regenerate leaves the
  // tree describing the previous build.
  const [, bump] = useState(0)
  useEffect(() => subscribePreview(() => bump((n) => n + 1)), [])
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const region = selectedPreviewRegion()

  function toggle(key: string) {
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const categories = project
    ? CATEGORIES.map((cat) => ({ ...cat, models: modelsIn(project, cat.bucket) })).filter(
        (cat) => cat.models.length > 0,
      )
    : []
  const all = categories.flatMap((cat) => cat.models)
  const visibleCount = all.filter((model) => previewModelVisible(model.uid)).length

  return (
    <>
      <div className="header" id="left-geo-header">
        <span className="title" id="tree-header-title">
          {t('preview.tree_header')}
        </span>
        <span className="badge" id="mode-badge">
          {t('preview.mode_badge', { count: all.length })}
        </span>
        <span className="badge" id="preview-visible-badge" title={t('preview.visible_count')}>
          {visibleCount}
        </span>
      </div>

      <div id="tree-root-sticky">
        <div
          className={`node-label${selectedUid === null && !region ? ' selected' : ''}`}
          id="preview-root-node"
          onClick={() => {
            selectPreviewRegion(null)
            onSelect(null)
          }}
        >
          <span className="node-main">
            <span className="node-expander">▾</span>
            <Icon name="eye" />
            <span className="name">STL Preview</span>
          </span>
          <span className="badge">{all.length}</span>
        </div>
      </div>

      <div className="content" id="tree-container">
        {!all.length && <div className="empty">{t('error.no_models_for_preview')}</div>}
        {all.length > 0 && (
          <ul className="tree">
            {categories.map((cat) => {
              const key = `preview-category:${cat.bucket}`
              const open = !collapsed[key]
              return (
                <li className="tree-node" key={cat.bucket}>
                  <div className="node-label" data-category={cat.bucket} onClick={() => toggle(key)}>
                    <span className="node-main">
                      <span className="node-expander">{open ? '▾' : '▸'}</span>
                      <Glyph icon={cat.icon} />
                      <span className="name">{t(cat.labelKey)}</span>
                    </span>
                    <span className="badge">{cat.models.length}</span>
                  </div>
                  {open && (
                    <ul>
                      {cat.models.map((model) => (
                        <PreviewModelNode
                          key={model.uid}
                          model={model}
                          icon={cat.icon}
                          selectedUid={selectedUid}
                          selectedRegion={region?.modelId === model.uid ? region.regionName : null}
                          collapsed={collapsed}
                          toggle={toggle}
                          onSelect={onSelect}
                        />
                      ))}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </>
  )
}

function PreviewModelNode({
  model,
  icon,
  selectedUid,
  selectedRegion,
  collapsed,
  toggle,
  onSelect,
}: {
  model: Construction
  icon: string
  selectedUid: string | null
  selectedRegion: string | null
  collapsed: Record<string, boolean>
  toggle: (key: string) => void
  onSelect: (uid: string | null) => void
}) {
  const { t } = useTranslation()
  const entry = previewEntry(model.uid)
  const regions = entry?.regions ?? []
  const key = `preview-model:${model.uid}`
  const open = !collapsed[key]
  const anyVisible = previewModelVisible(model.uid)
  const allVisible = previewModelFullyVisible(model.uid)
  const flags = previewFlags()

  return (
    <li className="tree-node">
      <div
        className={
          `node-label model-root-label${selectedUid === model.uid ? ' selected' : ''}` +
          `${anyVisible ? '' : ' disabled'}`
        }
        data-model-id={model.uid}
        onClick={(e) => {
          e.stopPropagation()
          selectPreviewRegion(null)
          onSelect(model.uid)
        }}
      >
        <span className="node-main">
          {regions.length > 0 && (
            <span
              className="node-expander"
              onClick={(e) => {
                e.stopPropagation()
                toggle(key)
              }}
            >
              {open ? '▾' : '▸'}
            </span>
          )}
          <input
            type="checkbox"
            className="node-visibility-toggle"
            data-preview-model={model.uid}
            checked={allVisible}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => togglePreviewModelVisible(model.uid, e.target.checked)}
            title={t('preview.toggle_model_title')}
          />
          <Glyph icon={icon} />
          <span className="name">{model.meta?.name ?? model.uid}</span>
        </span>
        <span className="node-actions">
          <span className="badge">{regions.length} Region</span>
        </span>
      </div>
      {open && regions.length > 0 && (
        <ul>
          {regions.map((region) => (
            <li className="tree-node" key={region.name}>
              <div
                className={`node-label${selectedRegion === region.name ? ' selected' : ''}`}
                data-preview-region={`${model.uid}/${region.name}`}
                onClick={(e) => {
                  e.stopPropagation()
                  // The MODEL is selected too, so the viewport highlight and
                  // every panel that follows the selection stay in step.
                  onSelect(model.uid)
                  selectPreviewRegion({ modelId: model.uid, regionName: region.name })
                }}
              >
                <span className="node-main">
                  <input
                    type="checkbox"
                    className="node-visibility-toggle"
                    data-preview-region-toggle={`${model.uid}/${region.name}`}
                    // Read from the live flags rather than a captured value:
                    // the model-level checkbox writes every region's flag, and
                    // a stale copy would leave these boxes disagreeing with
                    // what the viewport draws.
                    checked={isPreviewRegionVisible(flags, model.uid, region.name)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) =>
                      togglePreviewRegionVisible(model.uid, region.name, e.target.checked)
                    }
                    title={t('preview.toggle_region_title')}
                  />
                  <span className="node-dot" style={{ background: region.color }} />
                  <span className="name">{region.name}</span>
                </span>
                <span className="badge">{region.triangleCount}△</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}
