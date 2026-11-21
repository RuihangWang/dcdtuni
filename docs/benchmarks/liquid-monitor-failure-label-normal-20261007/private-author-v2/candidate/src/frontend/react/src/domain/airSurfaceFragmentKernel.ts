import '../../../shared/js/cfdrun_export.js'
export interface AirSurfaceExportEntry extends Record<string,unknown> {
 kind:'baffle'|'blanking_panel'|'floor_opening'|'floor_remaining'|'room_wall'|'partition_wall'
 patchName?:string|null
}
export interface AirSurfaceFragment {
 schema:'dcdtuni.air-surface-fragment/v1'
 sourceScope:'export-solid'
 sourceSolidName:string
 sourceEntry:AirSurfaceExportEntry
 boundaryBindings:Record<string,unknown>
 porousBindings:Record<string,unknown>
 sourceTerms:Record<string,never>
}
type Kernel=typeof globalThis & {cfdrunExport?:{resolveAirSurfaceFragment(solidName:string,entry:AirSurfaceExportEntry):AirSurfaceFragment}}
const api=(globalThis as Kernel).cfdrunExport
if(!api)throw new Error('cfdrun_export.js is not loaded')
/** No entity-UID reassignment: caller must provide the actual exported-solid context. */
export function resolveAirSurfaceFragment(solidName:string,entry:AirSurfaceExportEntry):AirSurfaceFragment {
 return api!.resolveAirSurfaceFragment(solidName,entry)
}
