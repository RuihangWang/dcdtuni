import { AppStreamer } from "@nvidia/omniverse-webrtc-streaming-library";
import { logCmd, logError } from "./logger";

// Only command_name + message survive the WebRTC -> carb bridge.
// `args` is JSON-stringified into `message` (or sent bare for string args, e.g. camera).
export async function sendCommand(command_name: string, args: object | string = {}) {
  const message = typeof args === "string" ? args : JSON.stringify(args);
  // Mirror EVERY command into the global Operation Log (src "ui") so the bottom
  // panel shows exactly what the UI dispatched to Kit, in order.
  const argStr = message === "{}" ? "" : ` ${message}`;
  logCmd(`→ ${command_name}${argStr}`);
  try {
    await AppStreamer.sendMessage({
      event_type: "send_message_from_event",
      payload: { command_name, message },
    });
  } catch (e) {
    logError("ui", `sendCommand(${command_name}) failed: ${e}`);
    console.error(`sendCommand(${command_name}) failed:`, e);
  }
}

// Recording hook. `hotspot_record.py` drives 31 frames through this channel directly
// rather than through the React controls, because selecting a field resets the colour
// domain to the LOADED dataset's p1-p99 — which across an animation renormalises the
// hotspot away frame by frame, and the sliders cannot reach a domain wider than the
// current frame's own extent. Send-only, and every command it can reach is one the UI
// already exposes as a button.
(window as unknown as { __dcSend?: typeof sendCommand }).__dcSend = sendCommand;

// ── Typed senders (match dc.cfd_bridge parsers) ─────────────────────────────
export type Field = "T" | "U";

/** Build + reveal the data-center scene from a server-side scene-definition JSON
 *  (e.g. GTC2025.json). The backend places geometry from the JSON and frames the
 *  camera. Empty path => the backend's default (DC_CFD_JSON). */
export const loadScene = (path = "") => sendCommand("loadScene", { path });

/** Reset the Kit viewport to a clean slate (fresh-Kit look): tears down the
 *  dataset/volume/streamlines, hides the geometry + floor grid + ROI box, and clears
 *  post-processing state. Fired ONCE per page load so a browser refresh is a genuine
 *  from-scratch demo — the geometry stays built, so the next Load CFD Geometry is fast. */
export const resetScene = () => sendCommand("resetScene", {});

/** Show/hide the voxelized CFD volume, colored by temperature (T) or |U|. */
export const setVolume = (field: Field, visible: boolean) =>
  sendCommand("setVolume", { field, visible });

/** Set the colormap domain (low/high thresholds) for the active field. */
export const setVolumeRange = (min: number, max: number) =>
  sendCommand("setVolumeRange", { min, max });

/** Show/hide the draggable region-of-interest box. */
export const setRoiVisible = (visible: boolean) =>
  sendCommand("setRoiVisible", { visible });

/** Clip the volume to the ROI box (true) or render the full domain (false). */
export const setRoiClip = (enabled: boolean) =>
  sendCommand("setRoiClip", { enabled });

/** Set the ROI box AABB in world (meters): min/max corners [x,y,z]. */
export const setRoiBox = (
  min: [number, number, number],
  max: [number, number, number],
) => sendCommand("setRoiBox", { min, max });

/** Re-render whatever was DRAGGED in the viewport (ROI box and/or seed sphere).
 *  Kit sends no drag events back over the one-way channel, so a gizmo drag never
 *  reaches the render on its own — this pulls it through: the backend bakes the
 *  dragged box into its clip bounds, adopts the dragged seed centre, re-voxelises
 *  the volume inside the new region and re-traces the streamlines. The backend
 *  echoes the resulting bounds/centre to the log, which the UI parses to refresh
 *  its number inputs. */
export const syncFromViewport = () => sendCommand("syncFromViewport", {});

/** Show/hide a named geometry group (USD model kind), e.g. "Racks", "ACUs". */
export const setObjectVisible = (name: string, visible: boolean) =>
  sendCommand("setObjectVisible", { name, visible });

/** Set a geometry group's rendered opacity (0..1) via its material. */
export const setObjectOpacity = (name: string, opacity: number) =>
  sendCommand("setObjectOpacity", { name, opacity });

/** Highlight a geometry group in the viewport (selection outline). "" clears. */
export const selectObject = (name: string) =>
  sendCommand("selectObject", { name });

/** Frame the viewport on one geometry group — double-click a model-tree row. Mirrors
 *  the STLGen editor, where double-clicking a node zooms to it. In OV it is also the
 *  only way to see a rack's internals, which are millimetres on a room-scale view. */
export const focusObject = (name: string) =>
  sendCommand("focusObject", { name });

/** Cut the current ROI out of the loaded result and make THAT the dataset.
 *
 *  Not the same as setRoiClip: the clip keeps the whole field loaded and only limits
 *  what the voxeliser reads, so hiding the ROI box brings it all back. Extraction
 *  replaces the dataset with the ROI's cells, so the field stays confined. */
export const extractRoi = (format: "vtu" | "vti" = "vtu") =>
  sendCommand("extractRoi", { format });

/** Load a new CFD dataset at runtime from a server-side path on the Kit host. */
export const loadDataset = (path: string) =>
  sendCommand("loadDataset", { path });

/** Show/hide + position the draggable streamline seed-sphere preview. Seed COUNT sets
 *  the sphere tessellation. No streamlines are traced until setStreamlines(true, …). */
export const setSeedSphere = (
  visible: boolean,
  center: [number, number, number],
  radius: number,
  nSeeds: number,
) => sendCommand("setSeedSphere", { visible, center, radius, nSeeds });

/** Generate (enabled) or remove (disabled) streamlines. Integration is ALWAYS on the
 *  vec3 U field; `field` (T|U) is only the COLOUR field. Seeds + integrated lines are
 *  confined to the ROI box when clipping is active ("ROI is god"). */
export const setStreamlines = (
  enabled: boolean,
  field: Field,
  seedCenter: [number, number, number],
  seedRadius: number,
  nSeeds: number,
  maxSteps: number,
  direction: "forward" | "backward" | "both" = "forward",
) =>
  sendCommand("setStreamlines", {
    enabled,
    field,
    seedCenter,
    seedRadius,
    nSeeds,
    maxSteps,
    direction,
  });
