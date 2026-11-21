import { useEffect, useRef, useState } from "react";
import { FileDialog } from "./FileDialog";
import { logCmd, logError, logInfo } from "../logger";
import {
  ACU_NAMES, BASE_ACU_FLOW, BASE_ACU_T, BASE_SERVER_FLOW, BASE_SERVER_POWER,
  DELTA_T_K, deltaT,
} from "../aiRanges";
import { AI_BUNDLE_DIR, DATA_DIR } from "../geometry";
import { SCENARIOS } from "../scenarios";
import { subscribeRoi, type RoiBox } from "../roiStore";

export type BcState = {
  serverPower: Record<string, number>;
  serverFlow: Record<string, number>;
  acuT: Record<string, number>;
  acuFlow: Record<string, number>;
};

type WorkerStatus = {
  ok: boolean; offline?: boolean; gpus?: string[]; tta?: boolean;
  busy?: boolean; runs?: number; error?: string;
};

type Props = {
  bc: BcState;
  onLoadScenario: (bc: BcState) => void;
  onPredicted: (vtuPath: string) => void;
};

const near = (a: number, b: number) => Math.abs(a - b) < 1e-6;

/** AI Prediction tab — turn the edited boundary conditions into a real prediction.
 *
 *  The chain behind the button: the UI's per-server values become an OpenFOAM `0/`
 *  specification, one relaxed solver step turns that into the t=1 boundary field, the
 *  model encodes that field numerically (z-scored per patch) and predicts the converged
 *  state, and the result is rotated into the demo's Y-up frame.
 *
 *  The numerical encoding is what makes the sliders matter. The frozen-text arm shipped
 *  first and predicted the SAME field for every operating point — its boundary signal is
 *  diluted ~1200x before it reaches the trunk — so the demo serves the numerical model
 *  and pays ~13 s for the solver step. */
export function AiPrediction({ bc, onLoadScenario, onPredicted }: Props) {
  const [status, setStatus] = useState<WorkerStatus | null>(null);
  const [bundle, setBundle] = useState<string>(AI_BUNDLE_DIR);
  const [picking, setPicking] = useState(false);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<any>(null);
  const [scenario, setScenario] = useState<string>(SCENARIOS[0]?.case ?? "case0");
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  // The ROI box currently drawn in the Post-processing tab, in the demo world frame.
  const [roi, setRoiState] = useState<RoiBox | null>(null);
  const [simRoi, setSimRoi] = useState(false);
  const [roiOnly, setRoiOnly] = useState(false);
  useEffect(() => subscribeRoi(setRoiState), []);

  // Poll the worker so the tab reflects reality (it may be disabled via DC_CFD_AI=0).
  useEffect(() => {
    let alive = true;
    const poll = () =>
      fetch("/api/ai-status")
        .then((r) => r.json())
        .then((s) => alive && setStatus(s))
        .catch(() => alive && setStatus({ ok: false, offline: true }));
    poll();
    const id = window.setInterval(poll, 5000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  // How far the user has moved from the demo case's own operating point.
  const changedServers = Object.keys(BASE_SERVER_POWER).filter(
    (k) => !near(bc.serverPower[k] ?? 0, BASE_SERVER_POWER[k]) ||
           !near(bc.serverFlow[k] ?? 0, BASE_SERVER_FLOW[k]),
  ).length;
  const changedAcus = ACU_NAMES.filter(
    (a) => !near(bc.acuT[a] ?? 0, BASE_ACU_T[a]) ||
           !near(bc.acuFlow[a] ?? 0, BASE_ACU_FLOW[a]),
  ).length;

  // The model sees delta-T, not watts, so flag operating points outside the band it
  // was trained on rather than letting them silently degrade the prediction.
  const dts = Object.keys(bc.serverPower).map(
    (k) => deltaT(bc.serverPower[k], bc.serverFlow[k]));
  const outOfBand = dts.filter((d) => d < DELTA_T_K.p5 || d > DELTA_T_K.p95).length;
  const dtMin = dts.length ? Math.min(...dts) : 0;
  const dtMax = dts.length ? Math.max(...dts) : 0;

  const runPredict = async () => {
    if (running) return;
    setRunning(true); setError(null); setResult(null); setElapsed(0);
    const t0 = Date.now();
    timer.current = window.setInterval(
      () => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    logCmd("→ aiPredict (boundary conditions → 1-step solve → model)");
    try {
      // Sent ONLY when the user asked for it. Omitting the key is what selects the
      // unchanged whole-field export, so the default request is byte-for-byte the request
      // this endpoint has always received. The box restricts the EXPORT, not the forward
      // pass — see the note under the checkbox.
      const body: Record<string, unknown> = {
        case: scenario,
        bc: {
          server_powers: bc.serverPower,
          server_volume_flow_rates: bc.serverFlow,
          supply_air_temperatures: bc.acuT,
          supply_air_volume_flow_rates: bc.acuFlow,
        },
      };
      if (roiOnly && roi) {
        body.roi = { min: roi.min, max: roi.max };
        if (simRoi) {
          body.simRoi = true;
          logInfo("ai", "SIM-ROI: pivots = the box + 45,000 room-wide anchors. Measured "
            + "delta -0.003 vs the full 51-draw run — but this touches ~55% of the "
            + "domain, so it is faster, NOT ROI-local.");
        }
        logInfo("ai", `ROI-only export: [${roi.min.map((v) => v.toFixed(2)).join(", ")}] – ` +
          `[${roi.max.map((v) => v.toFixed(2)).join(", ")}] m`);
      }
      const r = await fetch("/api/ai-predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || `HTTP ${r.status}`);
      setResult(j);
      logInfo("ai", `prediction ready in ${j.seconds}s → ${j.vtu}`);
      if (j.roi) {
        logInfo("ai", `exported ${j.roi.n_cells_exported.toLocaleString()} of ` +
          `${j.roi.n_cells_total.toLocaleString()} cells ` +
          `(${(j.roi.fraction * 100).toFixed(1)}%) → ${j.roi.vtu}`);
      }
      onPredicted(j.latest || j.vtu);
    } catch (e: any) {
      setError(String(e.message || e));
      logError("ai", `prediction failed: ${e}`);
    } finally {
      window.clearInterval(timer.current);
      setRunning(false);
    }
  };

  const online = status?.ok && !status.offline;

  return (
    <div className="controls">
      <section>
        <h3>AI Model</h3>
        <div className={online ? "ai-status ok" : "ai-status off"}>
          {status === null ? "checking worker…"
            : online
              ? `worker online · GPU ${status.gpus?.join(", ")}${status.tta ? " · TTA multi-rank" : ""}`
              : "worker offline — start it with DC_CFD_AI=1 in start_demo.sh"}
        </div>
        {/* Show the bundle name, not the absolute path. The full path contains the host
            user name, and this panel appears in every screenshot and screen recording of
            the demo — the paper is under double-blind review. The full path stays in the
            tooltip for whoever actually needs it. */}
        <div className="ai-path" title={bundle}>
          {bundle.replace(/^.*\//, "") || bundle}
          <span className="ai-path-dim"> · hover for full path</span>
        </div>
        <button className="btn block" onClick={() => setPicking(true)}>
          Choose model folder…
        </button>
        <div className="ds-note">
          The folder holds the inference source, the checkpoint under <b>logger/model</b>,
          and the dataset symlink. Predictions are written to
          <b> logger/inference_results</b>.
        </div>
      </section>

      <section>
        <h3>Operating Point</h3>
        {/* A scenario is a PRESET: picking one loads that dataset case's per-slot values
            into the Geometry-tab sliders, which the user can then edit. It cannot be a
            separate "replay this case" mode, because the UI always posts a fully
            populated bc and the worker takes bc over the case name — so before this
            wiring the dropdown changed the label and nothing else. */}
        <select className="scenario-sel" value={scenario}
          onChange={(e) => {
            const c = e.target.value;
            setScenario(c);
            const s = SCENARIOS.find((x) => x.case === c);
            if (!s) return;
            onLoadScenario({
              serverPower: { ...s.bc.server_powers },
              serverFlow: { ...s.bc.server_volume_flow_rates },
              acuT: { ...s.bc.supply_air_temperatures },
              acuFlow: { ...s.bc.supply_air_volume_flow_rates },
            });
            logInfo("ai", `loaded ${s.label} into the sliders`);
          }}
          disabled={running}>
          {SCENARIOS.map((s) => (
            <option key={s.case} value={s.case}>{s.label}</option>
          ))}
        </select>
        {SCENARIOS.filter((s) => s.case === scenario).map((s) => (
          <div key={s.case}>
            <div className="ai-kv"><span>total IT load</span><b>{s.totalKW} kW</b></div>
            <div className="ai-kv"><span>mean server power</span><b>{s.meanPowerW} W</b></div>
            <div className="ai-kv"><span>mean ΔT</span><b>{s.meanDeltaTK} K</b></div>
            <div className="ai-kv"><span>ACU supply</span><b>{s.acuTC} °C</b></div>
          </div>
        ))}
      </section>

      <section>
        <h3>Per-server detail</h3>
        <div className="ai-kv"><span>servers changed</span><b>{changedServers} / {Object.keys(BASE_SERVER_POWER).length}</b></div>
        <div className="ai-kv"><span>ACUs changed</span><b>{changedAcus} / {ACU_NAMES.length}</b></div>
        <div className="ai-kv">
          <span>derived ΔT range</span>
          <b>{dtMin.toFixed(1)} – {dtMax.toFixed(1)} K</b>
        </div>
        {outOfBand > 0 && (
          <div className="ai-warn">
            {outOfBand} server{outOfBand > 1 ? "s" : ""} outside the trained ΔT band
            ({DELTA_T_K.p5.toFixed(1)}–{DELTA_T_K.p95.toFixed(1)} K). The model was never
            shown operating points like that, so its answer there is extrapolation.
          </div>
        )}
        <div className="ds-note">
          Edit per-server power and flow by selecting a rack in the Geometry tab.
        </div>
      </section>

      <section>
        <h3>Export region</h3>
        <label className="ai-check">
          <input type="checkbox" checked={roiOnly} disabled={running || !roi}
            onChange={(e) => setRoiOnly(e.target.checked)} />
          <span>Export the ROI only</span>
        </label>
        {/* The second, opt-in step: also restrict the PIVOTS. Separate from the export
            checkbox on purpose — export-only is exact, this one trades accuracy for
            compute and the user should choose it knowingly. */}
        <label className="ai-check">
          <input type="checkbox" checked={simRoi} disabled={running || !roi || !roiOnly}
            onChange={(e) => setSimRoi(e.target.checked)} />
          <span>Also simulate on the ROI (faster, ~1/14 the compute)</span>
        </label>
        {roi ? (
          <div className="ai-kv">
            <span>box (m)</span>
            <b>{roi.min.map((v) => v.toFixed(1)).join(", ")} → {roi.max.map((v) => v.toFixed(1)).join(", ")}</b>
          </div>
        ) : (
          <div className="ds-note">
            Open <b>Post-processing</b> and position the ROI box first — this reads the box
            drawn there.
          </div>
        )}
        <div className="ds-note">
          {/* The obvious reading of "ROI only" is that the model would run on the box
              alone, and that is the one thing this must not do. Say so here, because the
              cost was measured and it is large. */}
          The model still predicts the <b>whole room</b>; only the file handed to the
          viewer is cut down to the box, so every exported value is identical to the
          full-field one. Inferring the box alone was measured and fails — a 3% region
          costs +0.406 nRMSE (threshold 0.077) and 1.03 K of temperature error, more than
          the 0.94 K effect this demo exists to show.
        </div>
      </section>

      <section>
        <h3>Run{running && <span className="rebuilding">{elapsed}s…</span>}</h3>
        <button className="btn block primary" onClick={runPredict}
          disabled={running || !online}>
          {running ? "Simulating…" : "simulate"}
        </button>
        <div className="ds-note">
          {/* This used to read "No CFD solver runs: the model reads the boundary straight
              from the serialized text" — true of the frozen-text arm this demo shipped
              with, and wrong ever since it moved to the numerical (A3) one, which needs a
              relaxed solver step per operating point. Measured on this host, three
              consecutive calls: 45–100 s end to end, of which the forward is 13–15 s. */}
          A one-step CFD solve turns your settings into the boundary field the model reads,
          then the network predicts the converged state. <b>45–100 s</b> end to end; the
          forward pass itself is ~14 s, the rest is the solve, process start-up and
          resampling for the viewport.
        </div>
        {error && <div className="ai-warn">{error}</div>}
        {result && (
          <div className="ai-result">
            <div className="ai-kv"><span>time</span><b>{result.seconds}s</b></div>
            <div className="ai-kv"><span>cells</span><b>{result.n_cells?.toLocaleString()}</b></div>
            {result.roi && (
              <div className="ai-kv">
                <span>exported</span>
                <b>{result.roi.n_cells_exported?.toLocaleString()} / {result.roi.n_cells_total?.toLocaleString()}
                  {" "}({(result.roi.fraction * 100).toFixed(1)}%)</b>
              </div>
            )}
            {result.stats?.T && (
              <div className="ai-kv">
                <span>T range</span>
                <b>{result.stats.T.min.toFixed(1)} – {result.stats.T.max.toFixed(1)} K</b>
              </div>
            )}
            <div className="ds-note">
              Switch to <b>Post-processing</b> and press <b>Start post-processing</b> to
              render it.
            </div>
          </div>
        )}
      </section>

      {picking && (
        <FileDialog
          title="Choose an AI model folder"
          hint="Navigate into the bundle (it holds src/, logger/model/, dataset/) and press Use this folder."
          startDir={DATA_DIR}
          exts={[]}
          dirsOnly
          onPick={(p) => { setBundle(p); setPicking(false); logInfo("ai", `model folder: ${p}`); }}
          onCancel={() => setPicking(false)}
        />
      )}
    </div>
  );
}
