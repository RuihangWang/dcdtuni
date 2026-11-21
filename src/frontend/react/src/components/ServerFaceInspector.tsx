import type { Construction } from '../schema/cfdproj'
import { serverFaceExportName } from '../geometry/serverExportNames'
import {
  addServerPatch,
  deleteServerPatch,
  serverFace,
  setServerFaceColor,
} from '../geometry/serverPatches'
import { rackFaceColorLabel, rackFaceLabel } from '../geometry/rackFaceLabels'
import { useTranslation } from '../useTranslation'
import { ServerIdentityRows } from './ServerIdentityRows'
import { ServerPatchEditor } from './ServerPatchEditor'

/**
 * One face of a rack server, and the openings on it.
 *
 * The flow split is the reason this panel exists. A fan-server states ONE total
 * flow rate, and the runner divides it equally among the patches on the face
 * (`stlgen_runner.py` BCSpec) — so adding a second patch halves what each one
 * gets. That is invisible from the patch panel, which shows only its own patch,
 * and it changes the boundary condition the solver runs.
 *
 * Ported from `renderServerFaceInspector` (STL_generate.html:9800). The face
 * colour is offered on inlet and outlet only, matching the legacy: those two
 * are what a rack shows to the room, and the other four are internal.
 */

export interface ServerFaceInspectorProps {
  model: Construction
  equipmentId: string
  faceId: string
}

export function ServerFaceInspector({ model, equipmentId, faceId }: ServerFaceInspectorProps) {
  const { t } = useTranslation()
  const servers = ((model.constructions as { servers?: Record<string, Record<string, unknown>> })
    ?.servers ?? {}) as Record<string, Record<string, unknown>>
  const server = servers[equipmentId]
  if (!server) return null

  const face = serverFace(model, equipmentId, faceId)
  const patches = face.patches ?? []
  const meta = (server.meta ?? {}) as { name?: string }
  const cooling = (server.cooling ?? {}) as Record<string, unknown>
  const isFan = String(server.type ?? server.kind ?? '') === 'fanServer'
  const showColor = faceId === 'inlet' || faceId === 'outlet'
  const totalQ = Number(cooling.volumeFlowRate ?? 0)
  const perPatchQ = patches.length ? totalQ / patches.length : totalQ

  return (
    <div className="content" id="server-face-inspector">
      {/* Which server, and where in the rack it sits: reached from a face,
          nothing else on screen says which U-slots this face belongs to. */}
      <ServerIdentityRows model={model} equipmentId={equipmentId} />
      <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
        <div className="prop-title" style={{ color: '#16825d' }}>
          Server Face
        </div>
        <div className="prop-row">
          <label>{meta.name ?? equipmentId}</label>
          <span className="value">{rackFaceLabel(faceId)}</span>
        </div>
        {showColor && (
          <div className="prop-row">
            {/* The colour of THIS face: it read label.front_color on the outlet too. */}
            <label htmlFor="srv-face-color">{rackFaceColorLabel(faceId)}</label>
            <input
              id="srv-face-color"
              type="color"
              value={String(face.color ?? '#7f8c9a')}
              onChange={(e) => setServerFaceColor(model.uid, equipmentId, faceId, e.target.value)}
            />
          </div>
        )}
        <div className="prop-row">
          <label>STL Region</label>
          <input
            id="srv-face-region"
            type="text"
            value={serverFaceExportName(model, equipmentId, faceId)}
            disabled
          />
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
        <div className="prop-title" style={{ color: '#16825d' }}>
          {isFan && faceId === 'outlet'
            ? t('liquid.outlet_patches')
            : isFan && faceId === 'inlet'
              ? t('liquid.inlet_patches')
              : 'Patches'}
        </div>
        {isFan && (faceId === 'inlet' || faceId === 'outlet') && (
          <div className="prop-hint" id="srv-face-flow">
            {patches.length
              ? t('equipment.patch_flow_summary', {
                  count: patches.length,
                  totalQ: totalQ.toFixed(4),
                  perQ: perPatchQ.toFixed(4),
                })
              : t('equipment.patch_flow_no_patches', { totalQ: totalQ.toFixed(4) })}
          </div>
        )}
        {patches.length === 0 ? (
          <div className="prop-hint">No patches</div>
        ) : (
          patches.map((patch, i) => (
            <div
              className="prop-group"
              key={patch.id}
              style={{ borderLeftColor: 'rgba(229,192,123,0.4)' }}
            >
              <div
                className="prop-title"
                style={{
                  color: 'var(--hint-color)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <span
                  style={{
                    display: 'inline-block',
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    background: patch.color ?? '#00cc66',
                  }}
                />
                <span style={{ flex: 1 }}>Patch {i + 1}</span>
                <span
                  className="srv-patch-delete"
                  id={`srv-delete-patch-${patch.id}`}
                  title="Delete patch"
                  style={{ color: '#f88', fontSize: 11, padding: '1px 6px', cursor: 'pointer' }}
                  onClick={(e) => {
                    e.stopPropagation()
                    deleteServerPatch(model.uid, equipmentId, faceId, patch.id)
                  }}
                >
                  ✕
                </span>
              </div>
              <div className="prop-row">
                <label className="dim">Final STL Solid Name</label>
              </div>
              <div className="patch-final-name">
                {serverFaceExportName(model, equipmentId, faceId, { isPatch: true, patchIndex: i })}
              </div>
              <ServerPatchEditor
                uid={model.uid}
                equipmentId={equipmentId}
                faceId={faceId}
                patch={patch}
              />
            </div>
          ))
        )}
        <button
          className="btn"
          id="srv-add-patch"
          style={{ width: '100%', marginTop: 6 }}
          onClick={() => addServerPatch(model.uid, equipmentId, faceId)}
        >
          + Add Patch
        </button>
      </div>
    </div>
  )
}
