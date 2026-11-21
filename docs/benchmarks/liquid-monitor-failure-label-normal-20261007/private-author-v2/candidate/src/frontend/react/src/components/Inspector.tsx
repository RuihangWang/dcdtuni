import { useEffect, useMemo, useState } from 'react'
import { allModels, type CfdProject, type Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { projectName, setProjectName } from '../io/projectMeta'
import { enterRackDetail } from '../geometry/rackDetail'
import * as session from '../run/session'
import { readCasePath } from '../run/caseDir'
import { FileBrowser } from './FileBrowser'
import { NumberField } from './NumberField'
import { RoomTemperature } from './RoomTemperature'
import { SeedInspector } from './SeedInspector'
import { EquipmentInspector } from './EquipmentInspector'
import { PartitionInspector } from './PartitionInspector'
import { ServerFaceInspector } from './ServerFaceInspector'
import { RackCfdSection } from './RackCfdSection'
import { BoxCfdSection } from './BoxCfdSection'
import { SheetCfdSection } from './SheetCfdSection'
import { AcuCoolingModel } from './AcuCoolingModel'
import { ModelLibraryPin } from './ModelLibrary'
import { ImportedStlOverview } from './ImportedStlOverview'
import { SheetSizeSection } from './SheetSizeSection'
import { sheetSizeSpec } from '../geometry/sheetSizeFields'
import { RackSlotInspector } from './RackSlotInspector'
import { autoSurfaceBoolDiff, canAutoCut, clearCutouts, cutoutCount } from '../geometry/boolDiff'
import { fromDisplay, stepFor, toDisplay, unitOf } from '../lengthUnits'
import { inspectorPanelFor, type InspectorPanel } from '../interaction/inspectorPanel'
import { transformTarget } from '../interaction/transformSupport'
import { viewportBoolDiffHost } from './MultiSelectPanel'
import type { TreeSelection } from '../interaction/treeNodes'
import { CduProperties } from './CduInspector'
import { TransformSection } from './TransformSection'
import { PipeSegmentInspector } from './PipeSegmentInspector'
import { AirPipeInspector } from './AirPipeInspector'
import { CustomMeshInspector } from './CustomMeshInspector'
import { FaceInspector, PatchAddPanel, PatchInspector, WallRegionInspector } from './PatchInspector'
import { useTranslation } from '../useTranslation'
import { alertIfOutOfBounds } from '../ui/boundsAlert'
import { RichText } from '../ui/richText'
import { ModelDisplayGroup } from './ModelDisplayControls'
import { RackServerOpacityGroup } from './RackServerOpacityGroup'
import { RackLayoutSection } from './RackLayoutSection'
import { RackServersPanel } from './RackServersPanel'
import { PartitionWallSection } from './PartitionWallSection'
import { FloorPolygonSection } from './FloorPolygonSection'
import { SurfaceOrientationSection } from './SurfaceOrientationSection'
import { SelectedFaceBanner } from './SelectedFaceBanner'
import { floorPolygon } from '../geometry/floorPolygon'
import { isPreviewActive, subscribePreview } from '../io/stlPreview'
import { StlPreviewInspector } from './StlPreviewInspector'
import { FacePrefixSection } from './FacePrefixSection'
import { exportFileNameFor, exportSingleModel } from '../io/singleModelExport'

/**
 * The right panel's property inspector.
 *
 * Two states, matching the legacy panel: nothing selected shows the project
 * (room, name, description, overview); a selection shows that model.
 *
 * Numbers are shown to 3 decimals in metres. The legacy panel has a unit
 * selector and converts on display, but geometry is stored in metres either
 * way — so metres is the honest default and a unit switch can be added without
 * changing what is stored.
 */

export interface InspectorProps {
  project: CfdProject | null
  selectedUid: string | null
  /**
   * The narrower selection under the model, when the tree made one.
   *
   * A face and a patch are different things to edit — the patch is where a
   * boundary condition is set — so the panel dispatches on this rather than
   * always showing the model.
   */
  node?: TreeSelection | null
  /**
   * Drop the selection — the panel's delete button has just removed the model
   * it was showing. Without it the panel keeps rendering a construction that is
   * no longer in the project.
   */
  clearSelection?: () => void
}

function fmt(v: number | undefined): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(3) : '—'
}

export function Inspector({ project, selectedUid, node, clearSelection }: InspectorProps) {
  const { t } = useTranslation()
  const selected = useMemo(() => {
    if (!project || !selectedUid) return null
    return store.findConstruction(project, selectedUid)
  }, [project, selectedUid])
  // STL Preview replaces this panel with its own three, the way
  // `updateStlPreviewUI` replaces #prop-content. Subscribed rather than passed
  // down: the mode is switched from the toolbar and from the preview's own
  // buttons, neither of which is an ancestor of this component.
  const [, bumpPreview] = useState(0)
  useEffect(() => subscribePreview(() => bumpPreview((n) => n + 1)), [])

  if (!project) {
    return (
      <div className="content" id="prop-content">
        <div className="empty">{t('inspector.no_project')}</div>
      </div>
    )
  }

  if (isPreviewActive()) {
    return (
      <div className="content" id="prop-content">
        <StlPreviewInspector project={project} selectedUid={selectedUid} />
      </div>
    )
  }

  // WHICH panel a selection opens is `inspectorPanelFor`'s decision — the
  // branch order that used to live here, moved so the transform block and the
  // R / V shortcuts read the same answer.
  const model = selected?.model ?? null
  const panel: InspectorPanel | null = model ? inspectorPanelFor(model, node) : null
  const transform = model ? transformTarget(model, node) : null

  return (
    <div className="content" id="prop-content">
      {/* The transform block, FIRST for every object: move and rotate in the
          same place whatever is selected, so the hand learns where they are.
          Above even the banner that names a selected face — the banner only
          appears for a face or slot pick, and above the block it pushed the
          toggles down 3-4 rows depending on HOW the model was picked. */}
      {model && transform && (
        <TransformSection project={project} model={model} scope={transform.scope} />
      )}
      {selected && node && <SelectedFaceBanner model={selected.model} node={node} />}
      {selected && panel ? (
        <SelectionPanel
          panel={panel}
          project={project}
          model={selected.model}
          bucket={selected.bucket}
          node={node ?? null}
          clearSelection={clearSelection}
        />
      ) : (
        <ProjectProperties project={project} />
      )}
    </div>
  )
}

/** The panel under the transform block, one per `InspectorPanel`. */
function SelectionPanel({
  panel,
  project,
  model,
  bucket,
  node,
  clearSelection,
}: {
  panel: InspectorPanel
  project: CfdProject
  model: Construction
  bucket: string
  node: TreeSelection | null
  clearSelection?: () => void
}) {
  switch (panel) {
    case 'region':
    case 'customPatch':
      return <CustomMeshInspector project={project} model={model} node={node as TreeSelection} />
    case 'patch':
      return (
        <PatchInspector
          project={project}
          model={model}
          faceId={node!.faceId as string}
          patchId={node!.patchId as string}
        />
      )
    case 'equipment':
      // A server inside a rack. `serverFace` and `serverPatch` carry an
      // equipmentId too, but those have their own panels — this is the
      // equipment ITSELF.
      return <EquipmentInspector model={model} equipmentId={node!.equipmentId as string} />
    case 'rackSlot':
      return (
        <RackSlotInspector
          model={model}
          index={node!.index as number}
          indices={node!.indices}
          side={(node!.side as 'front' | 'back') ?? 'front'}
        />
      )
    case 'serverFace':
      return (
        <ServerFaceInspector
          model={model}
          equipmentId={node!.equipmentId as string}
          faceId={node!.faceId as string}
        />
      )
    case 'partition':
      return <PartitionInspector model={model} index={node!.index as number} />
    case 'wallRegion':
      // The sheet left over once a face's patches are cut out of it. It is a
      // real OpenFOAM patch with its own name, opacity and seed pair, so it
      // gets its own panel — without this it falls through to the model's.
      return (
        <WallRegionInspector
          project={project}
          model={model}
          bucket={bucket}
          faceId={node!.faceId as string}
          index={node!.index as number}
        />
      )
    case 'face':
      return (
        <>
          {/* The legacy face panel opens with the naming/prefix group
              (STL_generate.html:10416), above everything else: the solid name
              is what the boundary conditions below get attached to. */}
          <FacePrefixSection model={model} faceId={node!.faceId as string} />
          <FaceInspector model={model} faceId={node!.faceId as string} />
        </>
      )
    case 'pipeSegment':
      return <PipeSegmentInspector project={project} model={model} node={node} />
    case 'airPipe':
      return <AirPipeInspector project={project} model={model} />
    case 'cdu':
      return (
        <>
          {/* The anchor ball is in the transform block above, with the position
              boxes, so this display group carries none of its own. */}
          <ModelDisplayGroup model={model} />
          <CduProperties project={project} model={model} />
        </>
      )
    // A wall, baffle, floor or partition wall's face is the model: the sheet
    // has one working face, and its panel (add-patch, cutouts, seeds) is what
    // a click on it should open. The banner under the transform block names
    // the face.
    case 'planarFace':
    case 'model':
      return (
        <ModelProperties
          model={model}
          bucket={bucket}
          project={project}
          clearSelection={clearSelection}
        />
      )
  }
}

function ProjectProperties({ project }: { project: CfdProject }) {
  const { t } = useTranslation()
  const geo = project.geometry
  const plane = geo?.plane ?? []
  // The footprint is a polygon, so the room's extent is its bounding box —
  // reading plane[0] and plane[2] would be wrong for the non-rectangular rooms
  // in this dataset (case_04_polygon, case_05_triangle).
  const xs = plane.map((p) => p[0])
  const zs = plane.map((p) => p[1])
  const width = xs.length ? Math.max(...xs) - Math.min(...xs) : undefined
  const depth = zs.length ? Math.max(...zs) - Math.min(...zs) : undefined
  const total = allModels(project).length

  return (
    <>
      <UnitControls project={project} />
      <ProjectDescription project={project} />
      <SensorRecords project={project} />
      <ProjectBounds project={project} />
      <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
        <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
          {t('inspector.room_geom_title')}
        </div>
        <div className="prop-row">
          <label>{t('inspector.room_width_x')}</label>
          <span className="value">{fmt(width)}</span>
        </div>
        <div className="prop-row">
          <label>{t('inspector.room_depth_z')}</label>
          <span className="value">{fmt(depth)}</span>
        </div>
        <div className="prop-row">
          <label>{t('inspector.room_height_y')}</label>
          <span className="value">{fmt(geo?.height)}</span>
        </div>
        <div className="prop-row">
          <label>{t('inspector.room_polygon_vertices')}</label>
          <span className="value">{plane.length}</span>
        </div>
        <div className="prop-hint">{t('inspector.room_geom_hint')}</div>
      </div>

      <RoomTemperature project={project} />

      <ProjectNameGroup project={project} />

      <CaseDirectoryGroup />

      <div className="prop-group">
        <div className="prop-title">{t('inspector.project_overview')}</div>
        <div className="prop-row">
          <label>{t('inspector.model_count_label')}</label>
          <span className="value">{total}</span>
        </div>
        <div className="prop-row">
          <label>{t('inspector.schema_version')}</label>
          <span className="value">{String(project.schemaVersion ?? '—')}</span>
        </div>
        <div className="prop-hint">{t('inspector.select_model_hint')}</div>
      </div>
    </>
  )
}

function ModelProperties({
  model,
  bucket,
  project,
  clearSelection,
}: {
  model: Construction
  bucket: string
  project: CfdProject | null
  clearSelection?: () => void
}) {
  const { t } = useTranslation()
  // Geometry boxes follow the project's input unit; the project itself always
  // stores metres.
  const unit = unitOf(project)
  const step = stepFor(unit)
  const g = model.geometry
  return (
    <>
      {/* The move / rotate / position / angle block is ABOVE this panel: the
          inspector draws it first for every kind (TransformSection), so the
          move buttons are in the same place on a rack as on a wall.

          Rack detail mode comes next, the first rack-only group: the room view
          is the wrong place to set a 19" rack's internals, and the legacy puts
          this button at the top of the rack's own groups
          (STL_generate.html:9241). */}
      {model.kind === 'rack' && (
        <div className="prop-group" style={{ borderLeftColor: '#ff8c00' }}>
          <button
            className="btn frozen-ok"
            id="btn-enter-rack-detail"
            style={{ color: '#ffaa33', borderColor: '#ff8c00' }}
            onClick={() => enterRackDetail(model.uid)}
          >
            {t('liquid.rack_detail_editor')}
          </button>
        </div>
      )}

      <UnitControls project={project} />

      {/* The legacy puts the two add-patch buttons on the MODEL inspector for the
          sheet kinds (STL_generate.html:8398-8404), because a wall, baffle or
          floor has no face node in the tree — its patches hang straight off the
          model. Without this a patch-less floor has nowhere to press Add, which
          is exactly the case that matters: the first vent tile. */}
      {(model.kind === 'wall' || model.kind === 'baffle' || model.kind === 'floor') && (
        <PatchAddPanel model={model} faceId={model.kind === 'floor' ? 'top' : 'front'} />
      )}

      {/* A floor or baffle's REMAINING area is a seed surface in its own right,
          and the legacy hosts its seed panel on the model inspector (:8883,
          :8945) — the patch inspector only covers the openings cut INTO it. */}
      {(model.kind === 'baffle' || model.kind === 'floor') && (
        <SeedInspector project={project} model={model} />
      )}

      <AutoCutSection model={model} />

      <RackCfdSection model={model} />
      <BoxCfdSection model={model} />
      <SheetCfdSection model={model} />
      <AcuCoolingModel project={project} model={model} />
      <ModelLibraryPin model={model} />

      <div className="prop-group">
        <div className="prop-title">{t('inspector.model_settings')}</div>
        <div className="prop-row">
          <label>{t('inspector.sensor_name')}</label>
          <input
            type="text"
            defaultValue={model.meta?.name ?? ''}
            key={model.uid}
            onBlur={(e) => {
              if (e.target.value !== model.meta?.name) store.renameModel(model.uid, e.target.value)
            }}
            onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
          />
        </div>
        <div className="prop-row">
          <label>{t('inspector.category')}</label>
          <span className="value">{bucket}</span>
        </div>
        <div className="prop-row">
          <label>{t('label.enabled')}</label>
          <input
            type="checkbox"
            checked={model.meta?.enabled !== false}
            onChange={(e) => store.setEnabled(model.uid, e.target.checked)}
          />
        </div>
        <div className="prop-row">
          <label>{t('inspector.uid')}</label>
          <span className="value" title={model.uid}>
            {model.uid}
          </span>
        </div>
        <SingleExportRows model={model} />
      </div>

      <ModelDisplayGroup model={model} />

      {/* The second opacity knob, racks only (spec §5.1): the group above fades
          the rack BODY — this one batch-writes every server's own opacity, and
          the two no longer drag each other. */}
      {model.kind === 'rack' && <RackServerOpacityGroup model={model} />}

      {/* The size boxes are per KIND, not generic, for the three kinds whose
          size is not a free variable:

          - a **rack**'s slot table, servers, doors and partitions are derived
            from it and have to be re-derived with it;
          - a **polygon floor**'s size IS its outline's bounding box, so it is
            rescaled rather than written;
          - a **partition wall**'s holes are re-fitted into the new face, and an
            overlap that would break the CSG cutter is refused.

          Writing `geometry.size` straight through in any of those three leaves
          the model disagreeing with itself. */}
      {model.kind === 'rack' ? (
        <RackLayoutSection model={model} project={project} />
      ) : model.kind === 'partitionWall' ? (
        <PartitionWallSection model={model} project={project} />
      ) : floorPolygon(model) ? (
        <FloorPolygonSection model={model} project={project} />
      ) : model.kind === 'custom' ? (
        // An imported mesh's size is a MEASUREMENT of the triangles the file
        // brought in, not a setting: editable boxes would be typed over while
        // nothing re-tessellates. Read-only, plus the solid-region count that
        // decides how many OpenFOAM patches it exports as
        // (STL_generate.html:9022-9034).
        <ImportedStlOverview project={project} model={model} />
      ) : project && sheetSizeSpec(model.kind as string | undefined) ? (
        // A wall, baffle or rectangular floor is a SHEET: it has two in-plane
        // dimensions and no thickness. The generic three-box group below gave it
        // a third, editable box — and `setGeometryComponent` wrote whatever was
        // typed there straight into `geometry.size`, so a baffle could be saved
        // 0.35 m thick in a .cfdproj that nothing draws with a thickness.
        //
        // This section has existed since the port and was imported by nothing.
        <SheetSizeSection project={project} model={model} />
      ) : (
        <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
          <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
            {t('inspector.size_title', { unit })}
          </div>
          {(['x', 'y', 'z'] as const).map((axis) => (
            <div className="prop-row" key={axis}>
              <label>
                {axis === 'x'
                  ? t('inspector.size_x')
                  : axis === 'y'
                    ? t('inspector.size_y')
                    : t('inspector.size_z')}
              </label>
              <NumberField
                value={toDisplay(g?.size?.[axis] ?? 0, unit)}
                step={step}
                decimals={4}
                min={0}
                onCommit={(v) =>
                  alertIfOutOfBounds(
                    store.setGeometryComponent(model.uid, 'size', axis, fromDisplay(v, unit)),
                  )
                }
              />
            </div>
          ))}
        </div>
      )}

      {/* Directly under the size group, as the legacy places it
          (STL_generate.html:8449). */}
      <SurfaceOrientationSection model={model} />

      {model.kind === 'rack' && <RackServersPanel model={model} key={model.uid} />}

      {/* Last in the panel, as it is in every legacy model panel — the sheet one
          (STL_generate.html:8453), the partition wall (:8987), the generic box
          and imported STL (:9035), the ACU (:8760) and the rack (:9304).
          Deleting ONE model with no confirmation, which is what
          `removeModel(null, uid)` does: `evt` is null there, so its batch branch
          and the confirm that goes with it never run. Deleting several is the
          multi-select panel's job, and that panel replaces this one. */}
      <button
        className="btn danger"
        id="btn-remove-model"
        onClick={() => {
          store.removeModel(model.uid)
          clearSelection?.()
        }}
      >
        {t(
          model.kind === 'rack'
            ? 'btn.remove_rack'
            : model.kind === 'acu'
              ? 'btn.remove_acu'
              : 'btn.remove_model',
        )}
      </button>
    </>
  )
}


/**
 * The server's case directory, in the project inspector.
 *
 * The legacy editor keeps it here rather than on a settings tab because it is
 * the thing every mesh and solve writes into — and on a shared host it is the
 * one setting that silently belongs to someone else.
 */
/**
 * 工程名称 — the name a save and an export are offered under.
 *
 * It was a read-only span, and with no Save As either the project was locked to
 * both its name and its file for as long as the app was open. The legacy field
 * (STL_generate.html:8293) is a plain text input committed on `onchange`, so it
 * is uncontrolled here too: a controlled input would push one undo checkpoint
 * per keystroke, and undoing a rename would then take as many presses as the
 * name has letters.
 *
 * `key` remounts the input when the name changes underneath it — a project
 * load, or an undo — which is the only way an uncontrolled input picks up a new
 * value.
 */
function ProjectNameGroup({ project }: { project: CfdProject }) {
  const { t } = useTranslation()
  const name = projectName(project)
  return (
    <div className="prop-group">
      <div className="prop-title">{t('inspector.project_name_title')}</div>
      <div className="prop-row">
        <label htmlFor="project-name-input">{t('inspector.project_name_label')}</label>
        <input
          id="project-name-input"
          key={`${project.uid ?? ''}:${name}`}
          type="text"
          defaultValue={name}
          placeholder={t('inspector.project_name_placeholder')}
          style={{ flex: 1, minWidth: 80 }}
          onBlur={(e) => e.target.value !== name && setProjectName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        />
      </div>
      <div className="prop-hint">{t('inspector.project_name_hint')}</div>
    </div>
  )
}

function CaseDirectoryGroup() {
  const { t } = useTranslation()
  const [picking, setPicking] = useState(false)
  const [, bump] = useState(0)
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  const info = session.getServerInfo()
  const caseDir = readCasePath() || info?.caseDir
  const caseDirOk = !!caseDir && info?.caseDir === caseDir && info.caseDirOk
  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-create)' }}>
      <div className="prop-title" style={{ color: 'var(--section-header-create)' }}>
        {t('inspector.dctwin_title')}
      </div>
      <div className="prop-row">
        <label>{t('inspector.dctwin_case_path')}</label>
        <span className="value" id="dctwin-case-display" title={caseDir}>
          {caseDir || t('inspector.dctwin_not_set')}
        </span>
      </div>
      <button className="btn primary frozen-ok" id="dctwin-setdir-btn" onClick={() => setPicking(true)}>
        {caseDirOk
          ? `✅ ${t('inspector.dctwin_ready_browse')}`
          : t('inspector.dctwin_set_case')}
      </button>
      {picking && (
        <FileBrowser
          pickDirectory
          startPath={caseDir || undefined}
          title={t('inspector.dctwin_set_case')}
          confirmLabel={t('btn.open')}
          onPick={(path) => {
            setPicking(false)
            void session.setCaseDir(path)
          }}
          onCancel={() => setPicking(false)}
        />
      )}
    </div>
  )
}

/**
 * Auto Bool Diff: cut this surface for everything that passes through it.
 *
 * Shown on floors, walls and baffles — the kinds with a face to cut. A raised
 * floor under a 200-rack room needs a hole per rack, and doing that one pair at
 * a time is not a workflow, which is why the legacy grew this button
 * (`autoFloorBoolDiff` / `autoWallBoolDiff`, STL_generate.html:11203, 11320).
 */
function AutoCutSection({ model }: { model: Construction }) {
  const { t } = useTranslation()
  const [note, setNote] = useState<string | null>(null)
  if (!canAutoCut(model)) return null
  const count = cutoutCount(model)

  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--error-color)' }}>
      <div className="prop-title" style={{ color: 'var(--error-color)' }}>
        {t('inspector.auto_bool_diff')}
      </div>
      <div className="prop-hint">
        {model.kind === 'floor'
          ? t('hint.auto_floor_diff')
          : t('hint.auto_wall_diff', {
              kind:
                model.kind === 'wall'
                  ? t('label.surface_label_wall')
                  : t('label.surface_label_baffle'),
            })}
      </div>
      <div className="prop-row">
        <label>{t('label.cutout_count')}</label>
        <span className="badge" id="cutout-count">
          {count}
        </span>
      </div>
      <button
        className="btn primary"
        id="auto-cutout"
        onClick={() => {
          const n = autoSurfaceBoolDiff(viewportBoolDiffHost(), model.uid)
          setNote(n ? null : t('error.no_wall_overlap'))
        }}
      >
        {t('btn.auto_cutout')}
      </button>
      {count > 0 && (
        <button className="btn" id="clear-cutouts" onClick={() => clearCutouts(model.uid)}>
          {t('btn.clear_cutouts')}
        </button>
      )}
      {note && <div className="error">{note}</div>}
    </div>
  )
}

/**
 * The input-unit selector the legacy puts at the top of every inspector.
 *
 * The project stores metres whatever this says; it changes what the geometry
 * boxes show and accept, so a rack datasheet in millimetres can be typed
 * straight in (`renderUnitControls`, STL_generate.html:4702).
 */
export function UnitControls({ project }: { project: CfdProject | null }) {
  const { t } = useTranslation()
  const unit = unitOf(project)
  return (
    <div className="prop-group" style={{ borderLeftColor: '#1bbfd4' }}>
      <div className="prop-title" style={{ color: '#1bbfd4' }}>
        {t('inspector.unit_title')}
      </div>
      <div className="prop-row">
        <label htmlFor="input-unit">{t('label.length_unit')}</label>
        <select
          id="input-unit"
          value={unit}
          disabled={!project}
          onChange={(e) => store.setInputUnit(e.target.value as 'mm' | 'cm' | 'm')}
        >
          <option value="mm">mm</option>
          <option value="cm">cm</option>
          <option value="m">m</option>
        </select>
      </div>
      <div className="prop-hint">
        <RichText text={t('inspector.unit_hint', { unit })} />
      </div>
    </div>
  )
}

/**
 * The project's boundary constraints.
 *
 * A checked axis limit is a wall no model may be dragged past — `yMin` at 0 is
 * the common one, and it stops a rack being pushed below the floor while
 * looking at the room from above, which meshes geometry outside the domain.
 * The move gizmo enforces them, so these boxes decide whether a drag is even
 * possible.
 */
function ProjectBounds({ project }: { project: CfdProject }) {
  const { t } = useTranslation()
  const unit = unitOf(project)
  const bounds = ((project.geometry as { bounds?: Record<string, { enabled?: boolean; value?: number }> })
    ?.bounds ?? {}) as Record<string, { enabled?: boolean; value?: number }>

  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
      <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
        {t('inspector.bounds_title', { unit })}
      </div>
      <div className="prop-hint">{t('inspector.bounds_desc')}</div>
      {(['x', 'y', 'z'] as const).map((axis) => {
        const keys = [`${axis}Min`, `${axis}Max`] as const
        return (
          <div className="prop-row" key={axis} style={{ display: 'grid', gridTemplateColumns: '18px minmax(0,1fr) minmax(0,1fr)', gap: 8, alignItems: 'end' }}>
            <label style={{ flex: '0 0 14px', fontWeight: 'bold' }}>{axis.toUpperCase()}</label>
            {keys.map((key) => {
              const entry = bounds[key] ?? { enabled: false, value: 0 }
              return (
                <span key={key} style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                  <label htmlFor={'bound-' + key + '-enabled'} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <input
                    id={`bound-${key}-enabled`}
                    type="checkbox"
                    checked={entry.enabled === true}
                    onChange={(e) => store.setProjectBound(key, 'enabled', e.target.checked)}
                    style={{ width: 'auto', margin: 0 }}
                  />
                  {key.endsWith('Min') ? 'Min' : 'Max'}
                  </label>
                  <NumberField
                    id={`bound-${key}-value`}
                    value={toDisplay(Number(entry.value ?? 0), unit)}
                    step={stepFor(unit)}
                    ariaLabel={axis.toUpperCase() + ' ' + (key.endsWith('Min') ? 'Min' : 'Max') + ' (' + unit + ')'}
                    width="100%"
                    disabled={entry.enabled !== true}
                    onCommit={(v) => store.setProjectBound(key, 'value', fromDisplay(v, unit))}
                  />
                </span>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

/** The project's free-text description. */
function ProjectDescription({ project }: { project: CfdProject }) {
  const { t } = useTranslation()
  const description = String((project.meta as { description?: string })?.description ?? '')
  const [loadRevision, setLoadRevision] = useState(0)
  // A new document can reuse both UID and committed text. Drop its predecessor's
  // uncommitted draft on load, while preserving drafts during ordinary edits.
  useEffect(() => store.subscribeProjectLoad(() => setLoadRevision((n) => n + 1)), [])
  return (
    <div className="prop-group">
      <div className="prop-title">{t('inspector.description_title')}</div>
      <textarea
        id="project-description"
        rows={4}
        style={{ width: '100%', resize: 'vertical' }}
        placeholder={t('inspector.description_placeholder')}
        defaultValue={description}
        key={`${project.uid ?? 'project'}:${loadRevision}:${description}`}
        onBlur={(e) => e.target.value !== description && store.setProjectDescription(e.target.value)}
      />
    </div>
  )
}

/**
 * Measured sensor records.
 *
 * NOT the solver's monitoring points — those are places the run reports from.
 * These are readings from the physical room, kept beside the model so a solved
 * case can be held against what the building actually did, which is the only
 * way a model gets validated.
 */
function SensorRecords({ project }: { project: CfdProject }) {
  const { t } = useTranslation()
  const measurements = ((project as unknown as {
    instrumentation?: { measurements?: Record<string, Record<string, unknown>> }
  }).instrumentation?.measurements ?? {}) as Record<string, Record<string, unknown>>
  const records = Object.values(measurements)

  return (
    <div className="prop-group">
      <div className="prop-title">{t('inspector.sensor_data_title')}</div>
      <div className="prop-hint">{t('inspector.sensor_data_hint')}</div>
      {records.map((record, i) => {
        const id = String(record.id ?? '')
        const location = (record.location ?? {}) as { x?: number; y?: number; z?: number }
        const series = (record.series ?? []) as unknown[]
        return (
          <div className="prop-group" key={id || i} style={{ borderLeftColor: 'var(--text-dim)' }}>
            <div className="prop-row">
              <label>{t('inspector.sensor_name')}</label>
              <input
                id={`sensor-rec-${id}-name`}
                type="text"
                defaultValue={String(record.name ?? `sensor_${i + 1}`)}
                key={`${id}-name`}
                onBlur={(e) => store.updateSensorRecord(id, 'name', e.target.value)}
              />
            </div>
            <div className="prop-row">
              <label>{t('inspector.sensor_quantity')}</label>
              <input
                id={`sensor-rec-${id}-quantity`}
                type="text"
                defaultValue={String(record.quantity ?? '')}
                key={`${id}-quantity`}
                onBlur={(e) => store.updateSensorRecord(id, 'quantity', e.target.value)}
              />
            </div>
            <div className="prop-row">
              <label>{t('inspector.sensor_location')}</label>
              {(['x', 'y', 'z'] as const).map((axis) => (
                <NumberField
                  key={axis}
                  id={`sensor-rec-${id}-${axis}`}
                  value={Number(location[axis] ?? 0)}
                  step={0.1}
                  width={60}
                  onCommit={(v) => store.updateSensorRecord(id, axis, v)}
                />
              ))}
            </div>
            <div className="prop-row">
              <span className="prop-hint">{series.length} pts</span>
              <button
                className="node-action-btn"
                id={`sensor-rec-${id}-delete`}
                title={t('tooltip.delete')}
                onClick={() => store.deleteSensorRecord(id)}
              >
                ✕
              </button>
            </div>
          </div>
        )
      })}
      <button className="btn primary" id="sensor-rec-add" onClick={() => store.addSensorRecord()}>
        {t('inspector.sensor_data_add')}
      </button>
    </div>
  )
}

/**
 * Dump ONE model's STL, and show the name the file will get.
 *
 * `exportSingleModel` (STL_generate.html:29226), offered on the four
 * inspectors the legacy offers it on. The whole-project export writes merged
 * per-kind files, so without this there is no way to open a single rack in an
 * external viewer — which is the first thing you do when one object is what
 * snappyHexMesh is failing on.
 *
 * The result is reported inline rather than through an alert: a disabled model
 * has no built meshes and exports as an empty file, and that has to be visible
 * instead of arriving as a valid-looking 0-facet STL.
 */
function SingleExportRows({ model }: { model: Construction }) {
  const { t } = useTranslation()
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const kind = String(model.kind ?? '')
  if (!['box', 'acu', 'custom', 'rack'].includes(kind)) return null
  const label =
    kind === 'rack'
      ? t('btn.export_current_rack')
      : kind === 'acu'
        ? t('btn.export_current_acu')
        : t('btn.export_current_model')

  return (
    <>
      <div className="prop-row">
        <label>{t('label.export_filename')}</label>
        <span className="value" id="model-export-filename" style={{ color: 'var(--hint-color)' }}>
          {exportFileNameFor(model)}
        </span>
      </div>
      <div className="prop-row">
        <label>{t('label.single_export')}</label>
        <button
          className="btn frozen-ok"
          id="btn-export-single-model"
          style={{ width: '160px', margin: 0 }}
          onClick={() => {
            const r = exportSingleModel(model.uid)
            setResult(
              r.ok
                ? { ok: true, text: `${r.filename} · ${r.facets}` }
                : { ok: false, text: t('error.export_failed', { error: r.error ?? '' }) },
            )
          }}
        >
          {label}
        </button>
      </div>
      {result && (
        <div className="prop-hint" id="model-export-result" style={result.ok ? undefined : { color: 'var(--danger, #e06c75)' }}>
          {result.text}
        </div>
      )}
    </>
  )
}
