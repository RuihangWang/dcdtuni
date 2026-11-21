import { RACKS } from "../geometry";
import {
  ACU_NAMES, BASE_ACU_FLOW, BASE_ACU_T, BASE_SERVER_FLOW, BASE_SERVER_POWER,
  ACU_FLOW_M3S, ACU_T_C, DELTA_T_K, FLOW_M3S, POWER_W, RACK_SLOTS, deltaT, slotKey,
} from "../aiRanges";
import type { BcState } from "./AiPrediction";
import type { TreeNode } from "../sceneTree";

type Props = {
  selected: string | null;
  node: (TreeNode & { category: string }) | null;
  source: "cfdproj" | "gtc";
  visible: Record<string, boolean>;
  opacity: Record<string, number>;
  bc: BcState;
  onToggleVisible: (name: string) => void;
  onOpacity: (name: string, v: number) => void;
  onSlotParam: (slot: string, key: "power" | "flow", v: number) => void;
  onAcuParam: (acu: string, key: "t" | "flow", v: number) => void;
};

// Right-hand "Inspector": context properties for whatever the model tree selected —
// visibility, opacity, and (for GTC equipment) the real per-slot boundary conditions.
//
// The subject is the manifest node, not a hardcoded group: the tree can now select any
// object in the project, and looking it up in a static GTC list found nothing for a
// .cfdproj object, so the panel just said "select an object" with one already selected.
export function Inspector({
  selected, node, source, visible, opacity, bc,
  onToggleVisible, onOpacity, onSlotParam, onAcuParam,
}: Props) {
  // GTC per-rack boundary conditions still key off the GTC rack list; a .cfdproj rack
  // has no entry there and simply shows no slot editor.
  const rk = source === "gtc" ? RACKS.find((r) => r.name === selected) : undefined;
  const slots = rk ? (RACK_SLOTS[rk.name] ?? []) : [];
  // Faces and servers live inside an INSTANCED prototype, so they share one GPU
  // prototype with every identical rack and have no per-instance prim to hide. Their
  // manifest ids carry a '/' (rack/face/server); a real toggleable object's does not.
  const addressable = !!node && !node.id.includes("/");

  return (
    <div className="panel inspector-panel">
      <div className="panel-header">Inspector</div>
      {!node ? (
        <div className="inspector-hint">Select an object in the model tree to edit its properties.</div>
      ) : (
        <>
          <div className="prop-group">
            <div className="prop-title">{node.icon} {node.label}</div>
            <div className="prop-row">
              <label>Category</label>
              <span className="prop-mono">{node.category}</span>
            </div>
            <div className="prop-row">
              <label>Prim name</label>
              <span className="prop-mono">{node.id}</span>
            </div>
            {addressable ? (
              <div className="prop-row">
                <label>Visible</label>
                <input
                  type="checkbox"
                  checked={visible[node.id] ?? true}
                  onChange={() => onToggleVisible(node.id)}
                />
              </div>
            ) : (
              <div className="prop-note">
                This row is part of an instanced prototype — it is shown for structure
                and cannot be toggled on its own. Use its parent object.
              </div>
            )}
          </div>

          {addressable && (
          <div className="prop-group">
            <div className="prop-title">Appearance</div>
            <div className="prop-row">
              <label>Opacity</label>
              <span className="prop-mono">{Math.round((opacity[node.id] ?? 1) * 100)}%</span>
            </div>
            <input
              type="range" min={0} max={1} step={0.05}
              value={opacity[node.id] ?? 1}
              onChange={(e) => onOpacity(node.id, parseFloat(e.target.value))}
            />
          </div>
          )}

          {rk && slots.length > 0 && (
            <div className="prop-group">
              <div className="prop-title">
                Servers <span className="prop-mono">{slots.length} slots</span>
              </div>
              <div className="prop-note">
                Power reaches the model only as ΔT = P / (ρcp·Q), so the derived rise is
                shown per slot. Amber = outside the trained {DELTA_T_K.p5.toFixed(1)}–
                {DELTA_T_K.p95.toFixed(1)} K band.
              </div>
              {slots.map((n) => {
                const key = slotKey(rk.name, n);
                const p = bc.serverPower[key] ?? BASE_SERVER_POWER[key] ?? 0;
                const q = bc.serverFlow[key] ?? BASE_SERVER_FLOW[key] ?? 0.1;
                const dt = deltaT(p, q);
                const hot = dt < DELTA_T_K.p5 || dt > DELTA_T_K.p95;
                return (
                  <div className="slot" key={key}>
                    <div className="slot-head">
                      <span>slot {n}</span>
                      <span className={hot ? "slot-dt warn" : "slot-dt"}>
                        ΔT {dt.toFixed(1)} K
                      </span>
                    </div>
                    <div className="slot-row">
                      <label>P</label>
                      <input type="range"
                        min={POWER_W.min} max={POWER_W.max} step={10}
                        value={p}
                        onChange={(e) => onSlotParam(key, "power", parseFloat(e.target.value))} />
                      <span className="prop-mono">{Math.round(p)} W</span>
                    </div>
                    <div className="slot-row">
                      <label>Q</label>
                      <input type="range"
                        min={FLOW_M3S.min} max={FLOW_M3S.max} step={0.001}
                        value={q}
                        onChange={(e) => onSlotParam(key, "flow", parseFloat(e.target.value))} />
                      <span className="prop-mono">{q.toFixed(3)} m³/s</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {source === "gtc" && node.id === "ACUs" && (
            <div className="prop-group">
              <div className="prop-title">
                Cooling units <span className="prop-mono">{ACU_NAMES.length}</span>
              </div>
              <div className="prop-note">
                Supply set-point and flow, bounded by the trained range
                ({ACU_T_C.min}–{ACU_T_C.max} °C, {ACU_FLOW_M3S.min.toFixed(1)}–
                {ACU_FLOW_M3S.max.toFixed(1)} m³/s).
              </div>
              {ACU_NAMES.map((a) => {
                const t = bc.acuT[a] ?? BASE_ACU_T[a] ?? 22;
                const f = bc.acuFlow[a] ?? BASE_ACU_FLOW[a] ?? 6;
                return (
                  <div className="slot" key={a}>
                    <div className="slot-head"><span>{a.replace(/_/g, " ")}</span></div>
                    <div className="slot-row">
                      <label>T</label>
                      <input type="range"
                        min={ACU_T_C.min} max={ACU_T_C.max} step={0.1}
                        value={t}
                        onChange={(e) => onAcuParam(a, "t", parseFloat(e.target.value))} />
                      <span className="prop-mono">{t.toFixed(1)} °C</span>
                    </div>
                    <div className="slot-row">
                      <label>Q</label>
                      <input type="range"
                        min={ACU_FLOW_M3S.min} max={ACU_FLOW_M3S.max} step={0.01}
                        value={f}
                        onChange={(e) => onAcuParam(a, "flow", parseFloat(e.target.value))} />
                      <span className="prop-mono">{f.toFixed(2)} m³/s</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

        </>
      )}
    </div>
  );
}
