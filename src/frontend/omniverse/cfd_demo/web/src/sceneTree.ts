// The model-tree manifest Kit writes after every geometry build (scenetree.py) and the
// Vite dev server hands back at /api/scene-tree.
//
// This replaces the hardcoded GEO_GROUPS list the tree used to render. That list was
// written for the GTC scene and stayed on screen for a .cfdproj build, so it offered
// rows like "Floor" that a .cfdproj never creates — ticking one sent setObjectVisible
// for a group the backend does not have, which it logs as "unknown group" and ignores.
// From the user's side that is a checkbox that does nothing.

export type TreeNode = {
  id: string;                 // the name setObjectVisible/selectObject take
  label: string;
  icon: string;
  badge?: string;
  /** A patch / equipment swatch: the editor draws these as a filled dot in its own
   *  colour rather than a glyph, which is how you tell two patches apart at a glance. */
  color?: string;
  enabled?: boolean;          // meta.enabled from the project
  opacity?: number;           // meta.display.opacity from the project
  children?: TreeNode[];
};

export type TreeCategory = {
  id: string;                 // USD group name ("Racks", "Floors", …)
  kind: string;
  icon: string;
  label: string;
  count: number;
  items: TreeNode[];
};

export type SceneManifest = {
  source: "cfdproj" | "gtc";
  project: string;
  categories: TreeCategory[];
  objects: number;
  /** The scene's own ROI AABB in metres. The Box Bounds inputs seed from this; without
   *  it they showed the GTC room's 31 x 5 x 19.2 for every project. */
  roi?: { min: number[]; max: number[] } | null;
  mtime?: number;
};

/** Fetch the manifest, retrying until one written AFTER `since` appears.
 *
 *  The Kit process outlives any page load, so a stale manifest from the PREVIOUS scene
 *  is always on disk — rendering it would show the old project's objects under the new
 *  project's geometry. `since` is the wall-clock time the load was fired; anything
 *  older than that belongs to a build we did not start. */
export async function fetchManifest(
  since = 0, tries = 40, delayMs = 500,
): Promise<SceneManifest | null> {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch("/api/scene-tree", { cache: "no-store" });
      if (r.ok) {
        const j = (await r.json()) as SceneManifest;
        if (!since || (j.mtime ?? 0) >= since) return j;
      }
    } catch {
      /* dev server hiccup — retry */
    }
    await new Promise((res) => setTimeout(res, delayMs));
  }
  return null;
}

/** Every toggleable id in the manifest, with the visibility the PROJECT gives it.
 *
 *  Seeding the checkboxes from this is what fixes the older `hiddenGroups=` echo hack:
 *  a disabled object's box now starts unticked because the manifest says so, instead of
 *  starting ticked and being corrected by a log line the page had to parse. */
export function initialVisibility(m: SceneManifest): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const c of m.categories) {
    // A category is on unless every one of its objects is off.
    out[c.id] = c.items.length === 0 || c.items.some((it) => it.enabled !== false);
    for (const it of c.items) out[it.id] = it.enabled !== false;
  }
  return out;
}

export function initialOpacity(m: SceneManifest): Record<string, number> {
  const out: Record<string, number> = {};
  for (const c of m.categories) {
    for (const it of c.items) {
      if (typeof it.opacity === "number") out[it.id] = it.opacity;
    }
  }
  return out;
}

/** Flatten to id -> node, so the Inspector can look up whatever the tree selected. */
export function indexNodes(m: SceneManifest): Record<string, TreeNode & { category: string }> {
  const out: Record<string, TreeNode & { category: string }> = {};
  const walk = (n: TreeNode, cat: string) => {
    out[n.id] = { ...n, category: cat };
    (n.children || []).forEach((k) => walk(k, cat));
  };
  for (const c of m.categories) {
    out[c.id] = { id: c.id, label: c.label, icon: c.icon, category: c.label,
                  badge: String(c.count) };
    c.items.forEach((it) => walk(it, c.label));
  }
  return out;
}


/** Per-field statistics for the currently loaded result, written by Kit on each
 *  successful load_dataset. */
export type FieldStats = { min: number; max: number; p1: number; p99: number };
export type DatasetInfo = {
  path: string;
  fields: Record<string, FieldStats>;   // keyed "T" / "Umag"
  mtime?: number;
};

/** Fetch the ranges for the result just loaded. `since` rejects the previous
 *  dataset's file, exactly as fetchManifest does for the scene. */
export async function fetchDatasetInfo(
  since = 0, tries = 20, delayMs = 400,
): Promise<DatasetInfo | null> {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch("/api/dataset-info", { cache: "no-store" });
      if (r.ok) {
        const j = (await r.json()) as DatasetInfo;
        if (!since || (j.mtime ?? 0) >= since) return j;
      }
    } catch { /* dev server hiccup — retry */ }
    await new Promise((res) => setTimeout(res, delayMs));
  }
  return null;
}
