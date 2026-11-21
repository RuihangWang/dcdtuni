import { useState } from "react";
import { ModelTree } from "./ModelTree";
import { PostProcessing } from "./PostProcessing";
import { AiPrediction, type BcState } from "./AiPrediction";
import type { SceneManifest } from "../sceneTree";

type Tab = "geo" | "ai" | "post";

type Props = {
  selected: string | null;
  visible: Record<string, boolean>;
  sceneLoaded: boolean;
  manifest: SceneManifest | null;
  onSelect: (name: string) => void;
  onFocus: (name: string) => void;
  onToggleVisible: (name: string) => void;
  onSceneLoaded: (m: SceneManifest | null) => void;
  bc: BcState;
  onLoadScenario: (bc: BcState) => void;
};

// Tabbed left panel: "Geometry" (model tree, gated behind the scene-JSON load) and
// "Post-processing" (dataset load + field controls, gated behind both the scene
// and a dataset load).
export function LeftPanel({
  selected, visible, sceneLoaded, manifest, onSelect, onFocus, onToggleVisible, onSceneLoaded,
  bc, onLoadScenario,
}: Props) {
  const [tab, setTab] = useState<Tab>("geo");
  // Set by a finished AI run so Post-processing knows there is something to render.
  const [predicted, setPredicted] = useState<string | null>(null);
  return (
    <div className="panel left-panel">
      <div className="tabs">
        <button className={tab === "geo" ? "tab active" : "tab"} onClick={() => setTab("geo")}>
          Geometry
        </button>
        <button className={tab === "ai" ? "tab active" : "tab"} onClick={() => setTab("ai")}>
          AI Prediction
        </button>
        <button className={tab === "post" ? "tab active" : "tab"} onClick={() => setTab("post")}>
          Post-processing
        </button>
      </div>
      {/* Both panels stay MOUNTED; we toggle visibility with `hidden` instead of
          unmounting the inactive tab. Unmounting PostProcessing on a tab switch
          dropped its dataset-loaded state (status="ready") and CfdControls' field
          selection / ROI box — so loading a dataset, switching to Geometry to hide
          racks, then switching back re-greyed every control even though the backend
          still had the dataset rendered. Keeping both mounted preserves that state. */}
      <div className="tab-body">
        <div hidden={tab !== "geo"}>
          <ModelTree
            selected={selected}
            visible={visible}
            loaded={sceneLoaded}
            manifest={manifest}
            onSelect={onSelect}
            onFocus={onFocus}
            onToggleVisible={onToggleVisible}
            onLoaded={onSceneLoaded}
          />
        </div>
        <div hidden={tab !== "ai"}>
          <AiPrediction bc={bc} onLoadScenario={onLoadScenario} onPredicted={setPredicted} />
        </div>
        <div hidden={tab !== "post"}>
          <PostProcessing sceneReady={sceneLoaded} predicted={predicted}
                          roi={manifest?.roi ?? null} />
        </div>
      </div>
    </div>
  );
}
