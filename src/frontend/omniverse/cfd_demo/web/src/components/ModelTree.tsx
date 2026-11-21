import { useState } from "react";
import { SCENE_EXTS, SCENE_DIR } from "../geometry";
import { FileDialog } from "./FileDialog";
import { loadScene } from "../streamMessages";
import { logInfo, logError, waitForLog } from "../logger";
import { fetchManifest, type SceneManifest, type TreeNode } from "../sceneTree";

type Props = {
  selected: string | null;
  visible: Record<string, boolean>;
  loaded: boolean;            // scene built/revealed -> show the geometry list
  manifest: SceneManifest | null;
  onSelect: (name: string) => void;
  onFocus: (name: string) => void;
  onToggleVisible: (name: string) => void;
  onLoaded: (m: SceneManifest | null) => void;
};

// Model tree, replicating the STLGen editor's Geometry Topology Tree:
//
//   🗂️ Project Root                   16
//     📦 Box (BOX)                     2   <- category: icon, label, count, twisty
//       ☑ 📦 Box_1                   BOX   <- object: checkbox, icon, name, kind badge
//     🗄️ Rack (RACK)                   4
//       ☑ 🗄️ Default_Rack_1          RACK
//         🧰 Equipments                1
//           ● fan-server_1  fan-server · 14U
//             ◻ Inlet
//             ◻ Outlet             1 patch
//               ■ Patch 1          25×100%
//         🪟 Partitions               41
//
// Every row is built from the manifest Kit writes at build time, NOT from a hardcoded
// list. The old list was the GTC scene's, so a .cfdproj build showed seven rows for
// groups it had not created — "Floor" among them, whose checkbox therefore did nothing.
export function ModelTree({
  selected, visible, loaded, manifest, onSelect, onFocus, onToggleVisible, onLoaded,
}: Props) {
  const [dlgOpen, setDlgOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Collapsed-by-KEY, mirroring the editor's uiState.treeCollapsed. Categories open,
  // objects closed: a 238-rack project with every rack expanded is 1,400 rows.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const isOpen = (k: string, dflt = true) => !(collapsed[k] ?? !dflt);
  const toggle = (k: string, dflt = true) =>
    setCollapsed((c) => ({ ...c, [k]: c[k] === undefined ? dflt : !c[k] }));

  const pickScene = async (path: string) => {
    setDlgOpen(false);
    setLoading(true);
    setErr(null);
    const firedAt = Date.now();
    logInfo("ui", `loading scene: ${path}`);
    loadScene(path);
    try {
      // Building the GB300 scene takes minutes; wait generously for the receipt.
      await waitForLog(
        (e) => e.src === "kit" && /loadScene: revealed/.test(e.msg),
        360000,
      );
      // Only a manifest written after we fired belongs to THIS scene — Kit is
      // long-lived and the previous project's file is still on disk.
      const m = await fetchManifest(firedAt);
      if (!m) logError("ui", "scene tree manifest never appeared (/api/scene-tree)");
      else logInfo("ui", `model tree: ${m.objects} objects, ${m.categories.length} categories`);
      setLoading(false);
      onLoaded(m);
    } catch {
      setLoading(false);
      setErr("Scene load timed out — see the Operational Log.");
      logError("ui", "loadScene receipt timeout");
    }
  };

  // One row. `depth` only drives indentation; the twisty is present iff there are kids.
  const renderNode = (n: TreeNode, key: string, dflt: boolean) => {
    const kids = n.children || [];
    const open = isOpen(key, dflt);
    const togglable = n.enabled !== undefined;   // faces/servers are informational
    return (
      <li key={key} className="tree-node">
        <div
          className={selected === n.id ? "node-label selected" : "node-label"}
          onClick={() => onSelect(n.id)}
          onDoubleClick={() => onFocus(n.id)}
          title={`${n.id} — double-click to frame`}
        >
          <span className="node-main">
            {kids.length ? (
              <span
                className="node-twisty"
                onClick={(e) => { e.stopPropagation(); toggle(key, dflt); }}
              >{open ? "▾" : "▸"}</span>
            ) : (
              <span className="node-twisty node-twisty-leaf" />
            )}
            {togglable ? (
              <input
                type="checkbox"
                className="node-vis"
                checked={visible[n.id] ?? true}
                onClick={(e) => e.stopPropagation()}
                onChange={() => onToggleVisible(n.id)}
              />
            ) : (
              <span className="node-vis-spacer" />
            )}
            {n.color ? (
              <span className="node-swatch" style={{ background: n.color }} />
            ) : (
              <span className="node-icon">{n.icon}</span>
            )}
            <span className="node-name">{n.label}</span>
          </span>
          {n.badge ? <span className="node-count">{n.badge}</span> : null}
        </div>
        {kids.length > 0 && open && (
          <ul>{kids.map((k, i) => renderNode(k, `${key}/${k.id}#${i}`, false))}</ul>
        )}
      </li>
    );
  };

  return (
    <>
    <button
      className="btn block load-geo"
      disabled={loading}
      onClick={() => setDlgOpen(true)}
      title="Choose a scene file and render the data center"
    >
      {loading ? "Building scene…" : loaded ? "↻ Reload CFD Geometry" : "⬇  Load CFD Geometry"}
    </button>
    {err && <div className="ds-status error" style={{ margin: "0 8px" }}>⚠ {err}</div>}

    {!loaded && !loading && (
      <div className="tree-empty">
        No geometry loaded.<br />
        Click <b>Load CFD Geometry</b> to choose a scene JSON or a <b>.cfdproj</b>.
      </div>
    )}
    {loading && (
      <div className="ds-status loading" style={{ margin: "8px" }}>
        <span className="ds-spinner" /> Placing geometry…
      </div>
    )}

    {loaded && !manifest && (
      <div className="tree-empty">
        Geometry is rendered, but Kit wrote no model-tree manifest.<br />
        Check the Operational Log for <b>sceneTree written</b>.
      </div>
    )}

    {loaded && manifest && (
    <ul className="tree">
      <li className="tree-node">
        <div className="node-label node-root" onClick={() => toggle("root")}>
          <span className="node-main">
            <span className="node-twisty">{isOpen("root") ? "▾" : "▸"}</span>
            <span className="node-icon">🗂️</span>
            <span className="node-name">{manifest.project || "Project Root"}</span>
          </span>
          <span className="node-count">{manifest.objects}</span>
        </div>
        {isOpen("root") && (
        <ul>
          {manifest.categories.map((c) => {
            const ck = `category:${c.id}`;
            const open = isOpen(ck);
            return (
              <li key={c.id} className="tree-node">
                <div
                  className={selected === c.id ? "node-label selected" : "node-label"}
                  onClick={() => onSelect(c.id)}
                  onDoubleClick={() => onFocus(c.id)}
                  title={`${c.id} — double-click to frame`}
                >
                  <span className="node-main">
                    <span
                      className="node-twisty"
                      onClick={(e) => { e.stopPropagation(); toggle(ck); }}
                    >{c.items.length ? (open ? "▾" : "▸") : ""}</span>
                    <input
                      type="checkbox"
                      className="node-vis"
                      checked={visible[c.id] ?? true}
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => onToggleVisible(c.id)}
                    />
                    <span className="node-icon">{c.icon}</span>
                    <span className="node-name">{c.label}</span>
                  </span>
                  <span className="node-count">{c.count}</span>
                </div>
                {open && c.items.length > 0 && (
                  <ul>
                    {c.items.map((it) => renderNode(it, `${c.id}/${it.id}`, false))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
        )}
      </li>
    </ul>
    )}

    {dlgOpen && (
      <FileDialog
        title="Load scene geometry"
        hint="Choose a scene file on the Kit host: a GTC-style scene JSON, or a .cfdproj saved by the STLGen editor (browse Up to reach case/)."
        exts={SCENE_EXTS}
        startDir={SCENE_DIR}
        onCancel={() => setDlgOpen(false)}
        onPick={pickScene}
      />
    )}
    </>
  );
}
