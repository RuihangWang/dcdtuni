import { useState } from "react";
import { Viewport } from "./components/Viewport";
import { LeftPanel } from "./components/LeftPanel";
import { Inspector } from "./components/Inspector";
import { LogPanel } from "./components/LogPanel";
import { DEFAULT_OPACITY } from "./geometry";
import {
  BASE_ACU_FLOW, BASE_ACU_T, BASE_SERVER_FLOW, BASE_SERVER_POWER,
} from "./aiRanges";
import type { BcState } from "./components/AiPrediction";
import {
  focusObject, selectObject, setObjectOpacity, setObjectVisible,
} from "./streamMessages";
import {
  indexNodes, initialOpacity, initialVisibility,
  type SceneManifest, type TreeNode,
} from "./sceneTree";
import "./styles.css";

export default function App() {
  const [selected, setSelected] = useState<string | null>(null);
  const [sceneLoaded, setSceneLoaded] = useState(false);
  const [manifest, setManifest] = useState<SceneManifest | null>(null);
  const [nodes, setNodes] = useState<Record<string, TreeNode & { category: string }>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [opacity, setOpacity] = useState<Record<string, number>>({ ...DEFAULT_OPACITY });
  // Real, per-server boundary conditions — 317 server slots and 6 ACUs, seeded from the
  // demo case's own operating point. These are what the AI Prediction tab sends to the
  // model, so they are the source of truth, not display-only mock values.
  const [bc, setBc] = useState<BcState>(() => ({
    serverPower: { ...BASE_SERVER_POWER },
    serverFlow: { ...BASE_SERVER_FLOW },
    acuT: { ...BASE_ACU_T },
    acuFlow: { ...BASE_ACU_FLOW },
  }));

  // The manifest is the ONLY source of tree rows and of their initial state. It
  // replaces two things: the hardcoded GTC group list (which offered rows for a scene
  // that was not loaded — the "Floor checkbox does nothing" report), and the
  // `hiddenGroups=` log-scraping that used to correct the checkboxes AFTER the fact.
  // A disabled object's box now starts unticked because the project says it is
  // disabled, not because a log line arrived and flipped it.
  const onManifest = (m: SceneManifest | null) => {
    setSceneLoaded(true);
    setManifest(m);
    if (!m) return;
    setNodes(indexNodes(m));
    setVisible(initialVisibility(m));
    setOpacity((o) => ({ ...o, ...initialOpacity(m) }));
    setSelected(null);
  };

  const onSelect = (name: string) => {
    const next = selected === name ? null : name; // click again to deselect
    setSelected(next);
    selectObject(next ?? "");
  };

  // Double-click frames the object, as the editor does. Selection alone cannot show a
  // rack's internals — they are millimetres on a room-scale view.
  const onFocus = (name: string) => {
    setSelected(name);
    selectObject(name);
    focusObject(name);
  };

  const onToggleVisible = (name: string) => {
    const next = !(visible[name] ?? true);
    setVisible((v) => ({ ...v, [name]: next }));
    setObjectVisible(name, next);
  };

  const onOpacity = (name: string, v: number) => {
    setOpacity((o) => ({ ...o, [name]: v }));
    setObjectOpacity(name, v);
  };

  // NOTE the old signature merged into `p[name]`, which was undefined for every
  // per-rack name (only the two group entries were ever seeded), so editing power
  // dropped flow and the input went uncontrolled. Each field is now its own flat map,
  // which cannot lose a sibling value.
  const onSlotParam = (slot: string, key: "power" | "flow", v: number) =>
    setBc((b) => key === "power"
      ? { ...b, serverPower: { ...b.serverPower, [slot]: v } }
      : { ...b, serverFlow: { ...b.serverFlow, [slot]: v } });

  const onAcuParam = (acu: string, key: "t" | "flow", v: number) =>
    setBc((b) => key === "t"
      ? { ...b, acuT: { ...b.acuT, [acu]: v } }
      : { ...b, acuFlow: { ...b.acuFlow, [acu]: v } });

  return (
    <div className="app">
      <Viewport />
      <div className="overlay overlay-left">
        <LeftPanel
          selected={selected}
          visible={visible}
          sceneLoaded={sceneLoaded}
          manifest={manifest}
          onSelect={onSelect}
          onFocus={onFocus}
          onToggleVisible={onToggleVisible}
          onSceneLoaded={onManifest}
          bc={bc}
          onLoadScenario={setBc}
        />
      </div>
      <div className="overlay overlay-right">
        <Inspector
          selected={selected}
          node={selected ? nodes[selected] ?? null : null}
          source={manifest?.source ?? "gtc"}
          visible={visible}
          opacity={opacity}
          bc={bc}
          onToggleVisible={onToggleVisible}
          onOpacity={onOpacity}
          onSlotParam={onSlotParam}
          onAcuParam={onAcuParam}
        />
      </div>
      <LogPanel />
    </div>
  );
}
