import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { CfdControls } from "./CfdControls";
import { FileDialog } from "./FileDialog";
import { AI_LATEST_VTU, AI_RESULTS_DIR, DATASET_EXTS, DATA_DIR } from "../geometry";
import { loadDataset } from "../streamMessages";
import { logInfo, logError, waitForLog, subscribeLog } from "../logger";
import { fetchDatasetInfo, type DatasetInfo } from "../sceneTree";

// Post-processing tab. The field-visualisation controls (volume colouring,
// thresholds, ROI clip) are GATED behind an explicit dataset load: by default
// they are disabled, and "Load dataset" opens a server file picker. When the user
// chooses a vtu/vtm/vti/vts/vtp/vtk/case file and clicks Open, we fire loadDataset
// (the Kit backend runs its read+voxelise pipeline) and watch the Operation Log
// for the backend receipt ("load_dataset OK/FAILED"). On success the controls
// become enabled. The WebRTC command channel is one-way, so the log line is the
// only completion signal — hence the waitForLog().
//
// BLOCKING-LOAD UX: a real CFD result is a large UNSTRUCTURED mesh (e.g. a ~1M-cell
// .vtu, 169 MB). Loading it means a server-side read PLUS an on-demand voxelisation
// to a NanoVDB grid for IndeX — that can take minutes, and the one-way channel gives
// no progress callback. So while it runs we (a) BLOCK the whole UI with a modal
// overlay so there are no half-loaded clicks, (b) show elapsed seconds + the live
// tail of the Kit log so it never looks frozen, and (c) wait a generous 10 min for
// the receipt before declaring a timeout.
type Status = "idle" | "loading" | "ready" | "error";
/** Where the field being visualised came from. Switchable at ANY time, in both
 *  directions — that is the whole point of this control. */
type Source = "ai" | "file";

const LOAD_TIMEOUT_MS = 600000; // 10 min — a 1M-cell .vtu voxelise is slow

export function PostProcessing({
  sceneReady, predicted, roi = null,
}: {
  sceneReady: boolean;
  predicted: string | null;
  roi?: { min: number[]; max: number[] } | null;
}) {
  const [dlgOpen, setDlgOpen] = useState(false);
  // TWO pieces of state, deliberately. `source` is which tab of the switch is showing;
  // `loadedSource` is where the field currently in the viewport came from. Binding the
  // badge to `source` made flipping the switch relabel an already-loaded CFD result as
  // "AI" without anything being reloaded — the label would have lied about the data.
  const [source, setSource] = useState<Source>("ai");
  const [loadedSource, setLoadedSource] = useState<Source | null>(null);
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  const [dsInfo, setDsInfo] = useState<DatasetInfo | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [loadedName, setLoadedName] = useState<string | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [lastKitLine, setLastKitLine] = useState("");

  // While a dataset is loading, drive the blocking overlay: tick elapsed seconds and
  // mirror the latest Kit-log line so a slow voxelise shows live progress (the Kit
  // reader/voxeliser prints stage messages that stream in as src "kit").
  useEffect(() => {
    if (status !== "loading") return;
    const t0 = Date.now();
    setElapsed(0);
    const tick = window.setInterval(
      () => setElapsed(Math.round((Date.now() - t0) / 1000)),
      1000,
    );
    const unsub = subscribeLog((entries) => {
      for (let i = entries.length - 1; i >= 0; i--) {
        const e = entries[i];
        // Mirror the latest real backend PROGRESS line — skip the raw command echo
        // ("command=loadDataset …"), which otherwise flashes stale "load dataset" text
        // in this loading overlay.
        if (e.src === "kit" && !/command=/.test(e.msg)) {
          setLastKitLine(e.msg);
          break;
        }
      }
    });
    return () => {
      window.clearInterval(tick);
      unsub();
    };
  }, [status]);

  const pick = async (path: string, from: Source) => {
    setDlgOpen(false);
    const name = path.split("/").pop() || path;
    setStatus("loading");
    setSource(from);
    setLoadedName(name);
    setLoadedPath(path);
    const firedAt = Date.now();
    setErrMsg(null);
    logInfo("ui", `loading result: ${path}`);
    loadDataset(path);
    try {
      // Backend prints "[dc.cfd_bridge] load_dataset OK: <path>" (or FAILED).
      // A large unstructured .vtu read + voxelise can take minutes.
      const hit = await waitForLog(
        (e) => e.src === "kit" && /load_dataset (OK|FAILED)/.test(e.msg),
        LOAD_TIMEOUT_MS,
      );
      if (/load_dataset OK/.test(hit.msg)) {
        setStatus("ready");
        setLoadedSource(from);
        // Field ranges for THIS result, so the threshold sliders do not open on the
        // GTC/A3 constants. Missing info is not an error — the sliders fall back.
        const info = await fetchDatasetInfo(firedAt);
        setDsInfo(info);
        if (info) logInfo("ui", `dataset ranges: ${JSON.stringify(info.fields)}`);
      } else {
        setStatus("error");
        setErrMsg("Backend could not load this file (see Operational Log).");
      }
    } catch {
      setStatus("error");
      setErrMsg("Timed out waiting for the backend (see Operational Log).");
      logError("ui", `load receipt timeout for ${name}`);
    }
  };

  const ready = status === "ready";

  return (
    <div className="postproc">
      <section className="panel-section">
        <h3>Post-processing</h3>

        {/* SOURCE SWITCH. Previously the tab had one primary button wired to the AI
            prediction and a secondary "Load a different result…", so the AI path was
            the assumed default and a solver result was the exception. They are two
            equal sources of the same thing — a field to visualise — and either can be
            re-picked at any time without leaving or reloading the tab. */}
        <div className="seg" role="tablist" aria-label="Result source">
          <button
            role="tab"
            aria-selected={source === "ai"}
            className={source === "ai" ? "seg-btn active" : "seg-btn"}
            disabled={status === "loading"}
            onClick={() => setSource("ai")}
          >AI prediction</button>
          <button
            role="tab"
            aria-selected={source === "file"}
            className={source === "file" ? "seg-btn active" : "seg-btn"}
            disabled={status === "loading"}
            onClick={() => setSource("file")}
          >CFD result file</button>
        </div>

        {source === "ai" ? (
          <>
            <button
              className="btn block load-geo"
              disabled={status === "loading" || !sceneReady}
              onClick={() => pick(predicted || AI_LATEST_VTU, "ai")}
            >
              {status === "loading" ? "Loading…" : "Load AI prediction"}
            </button>
            <div className="ds-hint">
              {predicted
                ? "Loads the prediction you just ran in the AI Prediction tab."
                : `Loads the newest prediction from ${AI_RESULTS_DIR.split("/").slice(-2).join("/")}. ` +
                  "Run one in the AI Prediction tab first, or use the other tab above."}
            </div>
          </>
        ) : (
          <>
            <button
              className="btn block load-geo"
              disabled={status === "loading" || !sceneReady}
              onClick={() => setDlgOpen(true)}
            >
              {status === "loading" ? "Loading…" : "Choose result file…"}
            </button>
            <div className="ds-hint">
              Any solver result on the Kit host — including this project's own OpenFOAM
              run at <b>case/&lt;name&gt;/log/&lt;timestamp&gt;/VTK/…/internal.vtu</b>.
            </div>
          </>
        )}

        {!sceneReady && (
          <div className="ds-hint">Load the scene geometry first (Geometry tab).</div>
        )}
        {status === "loading" && (
          <div className="ds-status loading">
            <span className="ds-spinner" /> loading <b>{loadedName}</b>…
          </div>
        )}
        {status === "ready" && (
          <div className="ds-status ready" title={loadedPath ?? undefined}>
            ✓ Showing <b>{loadedName}</b>
            <span className="ds-src">{loadedSource === "ai" ? "AI" : "CFD"}</span>
          </div>
        )}
        {status === "error" && (
          <div className="ds-status error">⚠ {errMsg}</div>
        )}
        {/* Re-loading the SAME source is a real action, not a no-op: an AI run or a
            re-solve overwrites the file in place. */}
        {status === "ready" && (
          <button
            className="btn block"
            disabled={!sceneReady}
            onClick={() => (loadedSource === "ai"
              ? pick(predicted || AI_LATEST_VTU, "ai")
              : setDlgOpen(true))}
          >↻ Reload {loadedSource === "ai" ? "prediction" : "file"}</button>
        )}
      </section>

      <CfdControls enabled={ready} sceneReady={sceneReady} roi={roi}
                   stats={dsInfo?.fields ?? null} />

      {dlgOpen && (
        <FileDialog
          title="Choose a CFD result"
          hint="Choose a result file (.vtu/.vti/.vts/.vtp/.vtk/.vtm/.case) on the Kit host."
          exts={DATASET_EXTS}
          startDir={DATA_DIR}
          onCancel={() => setDlgOpen(false)}
          onPick={(p) => pick(p, "file")}
        />
      )}

      {/* Blocking overlay during a (potentially multi-minute) dataset load. Portaled
          to <body> so it covers the whole app and intercepts every click — no
          half-loaded interaction. Shows elapsed time + the live Kit-log tail so the
          slow voxelise is legible instead of looking frozen. */}
      {status === "loading" && createPortal(
        <div className="load-overlay">
          <div className="load-card">
            <span className="ds-spinner big" />
            <h4>loading <b>{loadedName}</b></h4>
            <div className="load-elapsed">Elapsed&nbsp;<b>{elapsed}s</b></div>
            <div className="load-logline" title="latest Kit log line">
              {lastKitLine || "waiting for backend…"}
            </div>
            <div className="load-hint">
              Full progress streams in the Operational Log below.
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
