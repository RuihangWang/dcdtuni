// NOTE: the model tree's group list used to live here, as a hardcoded array carrying
// BOTH the GTC scene's groups and the .cfdproj ones. It had to carry both because the
// command channel is one-way and the page could not ask Kit what it had built — so a
// .cfdproj build rendered seven rows for a scene that was not loaded, and ticking one
// (`Floor`, when the build makes `Floors`) sent a command the backend logged as
// "unknown group" and dropped. That is what "the Floor checkbox does nothing" was.
//
// Kit now writes a manifest of what it ACTUALLY built (scenetree.py) and the dev server
// serves it at /api/scene-tree; see sceneTree.ts. Do not reintroduce a static list here
// — a row must exist because a prim exists.

// Individual racks under the "Racks" group (the backend exposes each rack prim
// name in Scene.geo_groups, so setObjectVisible/selectObject(name) work per-rack).
// Order + names match GTC2025.json; labels are Group·Row·#Rack.
export type Rack = { name: string; label: string };
export const RACKS: Rack[] = [
  { name: "rack_group_1_rack_row_1_rack_1", label: "G1·R1·#1" },
  { name: "rack_group_1_rack_row_1_rack_2", label: "G1·R1·#2" },
  { name: "rack_group_1_rack_row_1_rack_3", label: "G1·R1·#3" },
  { name: "rack_group_1_rack_row_1_rack_4", label: "G1·R1·#4" },
  { name: "rack_group_1_rack_row_1_rack_5", label: "G1·R1·#5" },
  { name: "rack_group_1_rack_row_1_rack_6", label: "G1·R1·#6" },
  { name: "rack_group_1_rack_row_1_rack_7", label: "G1·R1·#7" },
  { name: "rack_group_1_rack_row_1_rack_8", label: "G1·R1·#8" },
  { name: "rack_group_1_rack_row_1_rack_9", label: "G1·R1·#9" },
  { name: "rack_group_1_rack_row_1_rack_10", label: "G1·R1·#10" },
  { name: "rack_group_1_rack_row_1_rack_11", label: "G1·R1·#11" },
  { name: "rack_group_1_rack_row_1_rack_12", label: "G1·R1·#12" },
  { name: "rack_group_1_rack_row_1_rack_13", label: "G1·R1·#13" },
  { name: "rack_group_1_rack_row_2_rack_1", label: "G1·R2·#1" },
  { name: "rack_group_1_rack_row_2_rack_2", label: "G1·R2·#2" },
  { name: "rack_group_1_rack_row_2_rack_4", label: "G1·R2·#4" },
  { name: "rack_group_1_rack_row_2_rack_5", label: "G1·R2·#5" },
  { name: "rack_group_1_rack_row_2_rack_6", label: "G1·R2·#6" },
  { name: "rack_group_1_rack_row_2_rack_7", label: "G1·R2·#7" },
  { name: "rack_group_1_rack_row_2_rack_8", label: "G1·R2·#8" },
  { name: "rack_group_1_rack_row_2_rack_9", label: "G1·R2·#9" },
  { name: "rack_group_1_rack_row_2_rack_10", label: "G1·R2·#10" },
  { name: "rack_group_1_rack_row_2_rack_12", label: "G1·R2·#12" },
  { name: "rack_group_1_rack_row_2_rack_13", label: "G1·R2·#13" },
  { name: "rack_group_2_rack_row_1_rack_1", label: "G2·R1·#1" },
  { name: "rack_group_2_rack_row_1_rack_2", label: "G2·R1·#2" },
  { name: "rack_group_2_rack_row_1_rack_3", label: "G2·R1·#3" },
  { name: "rack_group_2_rack_row_1_rack_4", label: "G2·R1·#4" },
  { name: "rack_group_2_rack_row_1_rack_5", label: "G2·R1·#5" },
  { name: "rack_group_2_rack_row_1_rack_6", label: "G2·R1·#6" },
  { name: "rack_group_2_rack_row_1_rack_7", label: "G2·R1·#7" },
  { name: "rack_group_2_rack_row_1_rack_8", label: "G2·R1·#8" },
  { name: "rack_group_2_rack_row_1_rack_9", label: "G2·R1·#9" },
  { name: "rack_group_2_rack_row_1_rack_10", label: "G2·R1·#10" },
  { name: "rack_group_2_rack_row_1_rack_11", label: "G2·R1·#11" },
  { name: "rack_group_2_rack_row_1_rack_12", label: "G2·R1·#12" },
  { name: "rack_group_2_rack_row_1_rack_13", label: "G2·R1·#13" },
  { name: "rack_group_2_rack_row_2_rack_1", label: "G2·R2·#1" },
  { name: "rack_group_2_rack_row_2_rack_2", label: "G2·R2·#2" },
  { name: "rack_group_2_rack_row_2_rack_3", label: "G2·R2·#3" },
  { name: "rack_group_2_rack_row_2_rack_4", label: "G2·R2·#4" },
  { name: "rack_group_2_rack_row_2_rack_5", label: "G2·R2·#5" },
  { name: "rack_group_2_rack_row_2_rack_6", label: "G2·R2·#6" },
  { name: "rack_group_2_rack_row_2_rack_7", label: "G2·R2·#7" },
  { name: "rack_group_2_rack_row_2_rack_8", label: "G2·R2·#8" },
  { name: "rack_group_2_rack_row_2_rack_9", label: "G2·R2·#9" },
  { name: "rack_group_2_rack_row_2_rack_10", label: "G2·R2·#10" },
  { name: "rack_group_2_rack_row_2_rack_11", label: "G2·R2·#11" },
  { name: "rack_group_2_rack_row_2_rack_12", label: "G2·R2·#12" },
  { name: "rack_group_2_rack_row_2_rack_13", label: "G2·R2·#13" },
];

// Initial opacity per group, matching the materials the backend binds at build.
export const DEFAULT_OPACITY: Record<string, number> = {
  Room: 0.1,
  Floor: 1,
  Racks: 1,
  ACUs: 1,
  Containment: 0.5,
  Ducts: 1,
  Partitions: 0.22,
};

// Mock equipment parameters (display-only — not fed back into the CFD solve).
export type ServerParams = { power: number; flow: number };
export const DEFAULT_SERVER_PARAMS: Record<string, ServerParams> = {
  Racks: { power: 8.5, flow: 0.42 },
  ACUs: { power: 0, flow: 2.4 },
};

// Post-processing dataset presets. Paths are on the Kit host (the backend reads
// the file locally — there is no browser upload). The free-text field accepts
// any other server-side path.
export type DatasetPreset = { label: string; path: string };
// Injected by vite.config.ts from ITS own location (see `define` there). Browser code
// cannot resolve a filesystem path, but these strings are handed to Kit and resolved on
// the SERVER, so they must match wherever the repo actually lives — not one checkout.
declare const __DC_REPO_ROOT__: string;
const REPO = typeof __DC_REPO_ROOT__ === "string" ? __DC_REPO_ROOT__ : "";
export const DATASET_PRESETS: DatasetPreset[] = [
  // Default = the 1000-step result resampled to a regular grid (.vti). IndeX
  // voxelises a structured image directly; the raw 1.35 M-cell unstructured .vtu
  // produces an empty grid. The .vtu is offered as a (heavy) alternative.
  { label: "GTC 1000-step (default .vti)", path: `${REPO}/src/frontend/omniverse/cfd_demo/data/internal.vti` },
  { label: "GTC 1000-step raw (.vtu, heavy)", path: `${REPO}/src/frontend/omniverse/cfd_demo/data/internal.vtu` },
  { label: "kit-cae sample (multicomb .vtu)", path: `${REPO}/src/backend/kit-cae/data/multicomb_0_polyhedra.vtu` },
];

// File-picker (FileDialog) configuration.
// CFD result formats the Kit backend can load (load_dataset + converters).
export const DATASET_EXTS = [".vtu", ".vtm", ".vti", ".vts", ".vtp", ".vtk", ".case"];
// Where the dataset dialog opens (the demo's resampled GTC results live here).
export const DATA_DIR = `${REPO}/src/frontend/omniverse/cfd_demo/data`;
// Scene-definition JSON: the "Load CFD Geometry" dialog filters to .json and opens
// at the GTC GeoUSD folder (which holds GTC2025.json + the USD model assets).
// Both geometry sources are picked through the same dialog: a GTC-style scene JSON,
// or a .cfdproj saved by the STLGen editor (authored at true size by the Kit-side
// generator). The extension is what the backend dispatches on.
export const SCENE_EXTS = [".json", ".cfdproj"];
// AI model bundle (source + checkpoint + dataset symlink) and where the worker writes
// its predictions. The AI Prediction tab opens its folder picker at DATA_DIR so the
// bundle is one click away, and Post-processing loads the newest file from the results
// dir instead of asking the user to hunt for a .vtu.
// The demo serves the NUMERICAL (A3) arm. Its boundary K/V is the per-face t=1 field,
// z-scored with patch_statistics.json, so it actually responds to a change of operating
// point: two dataset cases predict measurably different fields (mean |dT| 0.72 K, 63% of
// cells) where the frozen-text arm predicted the same field for every case (0.007 K, 0%).
// The frozen-text bundle is still on disk at GTC_TransFVGN_FronzenText for comparison.
export const AI_BUNDLE_DIR = `${REPO}/src/frontend/omniverse/cfd_demo/SCANO/GTC_A3_Numerical`;
export const AI_RESULTS_DIR = `${AI_BUNDLE_DIR}/logger/inference_results`;
export const AI_LATEST_VTU = `${AI_RESULTS_DIR}/latest.vti`;

export const SCENE_DIR = `${REPO}/src/backend/kit-cae/data/GTC/GeoUSD`;
/** Where the STLGen editor saves its projects — browse here for a .cfdproj. */
export const CASE_DIR = `${REPO}/case`;
