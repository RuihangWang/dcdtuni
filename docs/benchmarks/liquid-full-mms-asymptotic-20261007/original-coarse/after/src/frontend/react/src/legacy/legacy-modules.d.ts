/**
 * The shared `shared/js/*.js` modules have no types of their own.
 *
 * They are plain UMD-ish files that attach an API to `window`, and each has a
 * hand-written typed accessor next to it here (`stlWriter()`, `stlNaming()`,
 * …). Declaring them as side-effect-only modules is therefore accurate: the
 * import exists to run the file, and nothing should be reading a value off it.
 *
 * Anything that DOES need the API must go through the accessor, so the types
 * stay in one place and a drift shows up as a compile error rather than as
 * `any` spreading through a call site.
 */
declare module '*/shared/js/stl_writer.js'
declare module '*/shared/js/supply_flow_anim.js'
declare module '*/shared/js/stl_naming.js'
declare module '*/shared/js/liquid_graphgen.js'
declare module '*/shared/js/cfdproj_schema.js'
declare module '*/shared/js/cfdproj_migrate.js'
declare module '*/shared/js/cfdproj_state_v4.js'
declare module '*/shared/js/rack_state.js'
declare module '*/shared/js/transform_tools.js'
declare module '*/shared/js/first_person_mode.js'
declare module '*/shared/js/liquid_cooling.js'
declare module '*/shared/js/liquid_pipe_renderer.js'
declare module '*/shared/js/liquid_mode.js'
declare module '*/shared/vendor/three-mesh-bvh-global.min.js'
declare module '*/shared/js/geometry_pure.js'
declare module '*/shared/js/pbr_materials.js'
declare module '*/shared/js/geometry_scene.js'
declare module '*/shared/vendor/polygon-clipping.umd.js'
declare module '*/shared/js/cfdrun_schema.js'
declare module '*/shared/js/cfdrun_export.js'
declare module '*/shared/js/cfdrun_build.js'
declare module '*/shared/js/mesh_sizing.js'
