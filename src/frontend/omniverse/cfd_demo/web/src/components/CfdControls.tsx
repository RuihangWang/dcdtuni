import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  extractRoi,
  setVolume,
  setVolumeRange,
  setRoiVisible,
  setRoiClip,
  setRoiBox,
  setStreamlines,
  setSeedSphere,
  syncFromViewport,
  type Field,
} from "../streamMessages";
import { subscribeLog, waitForLog } from "../logger";
import { setRoiSelection } from "../roiStore";

// Fallback dataset world bounds in metres (the GTC room). Used ONLY until the scene
// manifest arrives with the real ones — see the `roi` prop. Hardcoding these was a bug
// on every other project: loading a 4.4 x 3.6 x 6.0 m case left the Box Bounds inputs
// reading 31 / 5 / 19.2 while the backend had built the box around the actual room, so
// the panel disagreed with the viewport and editing a field snapped the box to a room
// that did not exist.
const ROI_FALLBACK = {
  min: [0, 0, 0] as [number, number, number],
  max: [31, 5, 19.2] as [number, number, number],
};
const ROI_AXES: { i: 0 | 1 | 2; label: string }[] = [
  { i: 0, label: "Width (X)" },
  { i: 1, label: "Height (Y)" },
  { i: 2, label: "Depth (Z)" },
];

// Streamline seed-sphere FALLBACK defaults — the GTC room's centre and a 2 m ball.
// Used only until the scene manifest supplies real bounds; see the seed effect below.
// Left hardcoded, these put every seed outside a 4.4 x 3.6 x 6.0 m project. With Clip
// ON the backend's ROI clamp dragged them back in and it merely looked sparse; with
// Clip OFF nothing rescues it — every seed misses the dataset and no curve is traced.
const SEED_DEFAULT = {
  center: [15.5, 2.5, 9.6] as [number, number, number],
  radius: 2.0,
  nSeeds: 27,
  maxSteps: 500,
};

// Switching field or toggling the ROI clip re-voxelises the volume in the
// backend (kit-cae voxelises once per volume), which takes ~1-2s. There is no
// completion signal over the one-way command channel, so we flash a
// "Rebuilding…" state for a fixed window and disable the volume controls to
// make the pause legible instead of looking frozen.
const REBUILD_MS = 2600;
// Showing a field or clipping RE-VOXELISES the volume on the Kit side — far slower
// than an opacity/visibility tweak (tens of seconds for the 1.35M-cell GTC dataset).
// Those ops hold the busy/"Rebuilding…" state for this longer window instead of
// clearing after 2.6 s and making the (still-rebuilding, briefly empty) volume look
// broken. There is no completion signal on the one-way data channel, so it's a timer.
const VOXELIZE_MS = 180000; // fallback ceiling; a 1M-cell .vtu voxelise can be slow

// Field domains for the threshold sliders (the slider TRACK bounds). These bracket
// each field's FULL data extent so the Autoscale button (which sets the domain to the
// full min/max) and manual dragging can reach the extremes.
const DOMAIN: Record<Field, { min: number; max: number; unit: string; label: string }> = {
  T: { min: 291, max: 318, unit: "K", label: "Temperature" },
  U: { min: 0, max: 28, unit: "m/s", label: "Velocity |U|" },
};
// Full data extent per field (from data/ranges.json) — the target of the Autoscale
// button, which fits the colour domain to the field's true min/max. (The DEFAULT_RANGE
// below is the narrower, vivid p1..p99 band used on first show / variable switch.)
const FULL_RANGE: Record<Field, [number, number]> = {
  T: [292.4, 319.4],   // A3 predictions; the pre-baked internal.vtu was [291.7, 317.5]
  U: [0.0, 3.9],       // A3 max 3.83 m/s — internal.vtu reached 27.9
};
// Default thresholds = the backend's PINNED display domain (_DISPLAY_DOMAIN in
// scene.py), the p1..p99 working band of each field. Both fields are heavily
// right-skewed, so mapping over the raw min/max washes the volume to one colour;
// these percentile bands spread the blue->amber->red gradient across where the data
// actually lives. The backend re-pins the same domain on every rebuild, so keeping
// these in sync means the floating colorbar ticks match the rendered colours. The
// Low/High sliders re-tighten the band live (clampToEdge: outside [lo,hi] clamps to
// the nearest edge colour, so the >hi exhaust stays solid red rather than vanishing).
const DEFAULT_RANGE: Record<Field, [number, number]> = {
  T: [292.1, 309.0],   // K; A3 predictions run p1 292.9 .. p99 308.9
  U: [0.0, 1.2],       // m/s; A3 |U| p99 = 1.15 (was 4.5, from the pre-baked file)
};

// `enabled`  = a CFD dataset is loaded (gates the volume + streamline sections).
// `sceneReady` = the geometry is loaded (gates the ROI section, which is dataset-
// independent — the user sets the region of interest BEFORE a result is loaded).
export function CfdControls(
  { enabled = true, sceneReady = true, roi = null, stats = null }: {
    enabled?: boolean;
    sceneReady?: boolean;
    /** Real ROI AABB of the loaded scene, from the Kit manifest. */
    roi?: { min: number[]; max: number[] } | null;
    /** Per-field min/max/p1/p99 of the LOADED result, from Kit. Keyed "T"/"Umag".
     *  Absent for a dataset Kit could not measure — the constants below then apply. */
    stats?: Record<string, { min: number; max: number; p1: number; p99: number }> | null;
  },
) {
  // The constants below were measured on the GTC/A3 data and are wrong for anything
  // else; a result whose |U| peaks at 8.2 m/s opened on a 0..1.2 domain, clamping
  // almost every voxel to the top colour. Prefer the loaded dataset's own numbers.
  const fstat = (f: Field) => stats?.[f === "T" ? "T" : "Umag"] ?? null;
  const domainOf = (f: Field) => {
    const st = fstat(f);
    return st ? { ...DOMAIN[f], min: st.min, max: st.max } : DOMAIN[f];
  };
  const defaultBand = (f: Field): [number, number] => {
    const st = fstat(f);
    return st ? [st.p1, st.p99] : DEFAULT_RANGE[f];
  };
  const fullRangeOf = (f: Field): [number, number] => {
    const st = fstat(f);
    return st ? [st.min, st.max] : FULL_RANGE[f];
  };
  const bounds = {
    min: (roi?.min ?? ROI_FALLBACK.min) as [number, number, number],
    max: (roi?.max ?? ROI_FALLBACK.max) as [number, number, number],
  };
  const [field, setField] = useState<Field | null>(null);
  const [range, setRange] = useState<Record<Field, [number, number]>>({
    T: [...DEFAULT_RANGE.T] as [number, number],
    U: [...DEFAULT_RANGE.U] as [number, number],
  });
  const [roiVisible, setRoi] = useState(false);
  const [roiClip, setClip] = useState(false);
  const [roiMin, setRoiMin] = useState<[number, number, number]>([...bounds.min]);
  const [roiMax, setRoiMax] = useState<[number, number, number]>([...bounds.max]);
  const [busy, setBusy] = useState(false);
  const busyTimer = useRef<number | undefined>(undefined);
  // Floating colour-legend (colorbar): drag position + range-edit dialog state.
  // Default sits at the bottom-right of the VIEWPORT but clears the Operational
  // Log dock (~200px tall, z-index 80) so it isn't occluded; the colorbar itself
  // renders above the dock (z-index 90) and is freely draggable from there.
  const [cbPos, setCbPos] = useState(() => ({
    x: Math.max(20, (typeof window !== "undefined" ? window.innerWidth : 1920) - 250),
    y: Math.max(20, (typeof window !== "undefined" ? window.innerHeight : 1080) - 320),
  }));
  const [cbEdit, setCbEdit] = useState(false);
  const [cbLo, setCbLo] = useState("");
  const [cbHi, setCbHi] = useState("");
  // Streamlines: ON reveals the draggable seed sphere + settings; Apply traces the
  // curves (integrated on U, coloured by slField). slApplied = curves currently exist.
  const [slOn, setSlOn] = useState(false);
  const [slApplied, setSlApplied] = useState(false);
  const [slField, setSlField] = useState<Field>("T");
  // Visibility of the draggable seed-sphere PREVIEW (independent of whether streamlines
  // are traced) — mirrors the ROI box's Show/Hide toggle.
  const [seedVisible, setSeedVisible] = useState(true);
  const [seedCenter, setSeedCenter] = useState<[number, number, number]>([...SEED_DEFAULT.center]);
  const [seedRadius, setSeedRadius] = useState(SEED_DEFAULT.radius);
  const [nSeeds, setNSeeds] = useState(SEED_DEFAULT.nSeeds);
  const [maxSteps, setMaxSteps] = useState(SEED_DEFAULT.maxSteps);
  const [slBusy, setSlBusy] = useState(false);
  const slBusyTimer = useRef<number | undefined>(undefined);

  // When the controls become disabled (a new dataset is loading / none loaded),
  // drop any active field selection so the UI shows "Off" and the colorbar/sliders
  // for the previous dataset disappear (the backend tore that volume down anyway).
  useEffect(() => {
    if (!enabled) {
      if (field !== null) setField(null);
      // The backend tears down streamlines on a dataset reload; mirror that in the UI.
      if (slOn) setSlOn(false);
      if (slApplied) setSlApplied(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  // Flash the volume controls as busy for the rebuild window.
  const flashBusy = (ms = REBUILD_MS) => {
    setBusy(true);
    window.clearTimeout(busyTimer.current);
    busyTimer.current = window.setTimeout(() => setBusy(false), ms);
    // The fixed timer is only a FALLBACK — the one-way WebRTC channel emits no
    // completion signal. But the backend DOES print "volume rebuilt field=..." when
    // the (possibly slow, ~1M-cell .vtu) voxelise finishes, so clear busy the moment
    // that lands: the controls then re-enable exactly when the volume is ready
    // instead of on a fixed guess that's too short (premature clicks) or too long
    // (dead UI). waitForLog resolves on the NEXT receipt after this call.
    waitForLog((e) => e.src === "kit" && /volume rebuilt field=/.test(e.msg), ms)
      .then(() => {
        window.clearTimeout(busyTimer.current);
        setBusy(false);
      })
      .catch(() => {});
  };

  const selectField = (f: Field | null) => {
    if (busy) return;
    // turn off the previously active field, then show the new one
    if (field && field !== f) setVolume(field, false);
    setField(f);
    if (f) {
      setVolume(f, true);
      // Load this field's NATURAL vivid default band on every (re)selection, so
      // switching variables (T<->U) always starts from the good per-field domain
      // instead of inheriting a previous Autoscale/slider state. Autoscale + the
      // sliders adjust from there until the next field-button click.
      const [lo, hi] = defaultBand(f);
      setRange((r) => ({ ...r, [f]: [lo, hi] }));
      setVolumeRange(lo, hi);
      flashBusy(VOXELIZE_MS); // showing a field re-voxelises (~tens of seconds)
    } else if (field) {
      setVolume(field, false);
    }
  };

  const onSlide = (which: 0 | 1, value: number) => {
    if (!field) return;
    const next: [number, number] = [...range[field]] as [number, number];
    next[which] = value;
    // keep min <= max
    if (next[0] > next[1]) next[which === 0 ? 1 : 0] = value;
    setRange({ ...range, [field]: next });
    setVolumeRange(next[0], next[1]);
  };

  // Autoscale: fit the colour domain to the active field's FULL data extent (its true
  // min/max). The sliders + floating colorbar update to match, and the backend volume
  // re-reads the domain live (setVolumeRange). Switching variables already restores
  // each field's own DEFAULT (vivid) band, so autoscale is a deliberate "show the full
  // range" action, distinct from the natural per-variable default.
  const onAutoscale = () => {
    if (!field) return;
    const [lo, hi] = fullRangeOf(field);
    setRange({ ...range, [field]: [lo, hi] });
    setVolumeRange(lo, hi);
  };

  const toggleRoi = () => {
    const v = !roiVisible;
    setRoi(v);
    if (v) {
      // Sync the backend box to the current inputs before showing it, so the wireframe
      // matches the numbers the user sees. The box already exists (created at geometry
      // load, sized to the scene AABB), which may differ from these input defaults.
      setRoiBox(roiMin, roiMax);
      setRoiVisible(true);
    } else {
      setRoiVisible(false);
      if (roiClip) {
        setClip(false);
        setRoiClip(false);
      }
    }
  };

  const toggleClip = () => {
    if (busy) return;
    const v = !roiClip;
    setClip(v);
    setRoiClip(v);
    // Clipping only re-voxelises the volume when a field is actually shown; before
    // a result is loaded (or with the field Off) it just records the clip intent for the next
    // time a field is shown, so don't flash a spurious "Rebuilding…".
    if (field) flashBusy(VOXELIZE_MS);
  };

  // Edit one ROI corner component, keeping min <= max per axis, and push the new
  // world AABB to the backend (it re-authors the box; re-voxelises if clipping).
  const onRoiEdit = (which: "min" | "max", i: 0 | 1 | 2, value: number) => {
    if (Number.isNaN(value)) return;
    // Once the user types a bound, stop re-seeding from the scene manifest — otherwise
    // loading another result would silently discard their region.
    roiTouched.current = true;
    const nmin = [...roiMin] as [number, number, number];
    const nmax = [...roiMax] as [number, number, number];
    if (which === "min") {
      nmin[i] = value;
      if (nmin[i] > nmax[i]) nmax[i] = value;
    } else {
      nmax[i] = value;
      if (nmax[i] < nmin[i]) nmin[i] = value;
    }
    setRoiMin(nmin);
    setRoiMax(nmax);
    setRoiBox(nmin, nmax);
    if (roiClip && field) flashBusy(); // re-voxelises within the new box only if a field is shown
  };

  // A useState initialiser runs ONCE, so the fallback bounds would stick forever if the
  // manifest arrived after mount — which it always does, since the scene is loaded from
  // the UI. Re-seed the inputs whenever the scene's real bounds change, unless the user
  // has already edited them.
  const roiKey = `${bounds.min.join()}|${bounds.max.join()}`;
  const roiTouched = useRef(false);
  useEffect(() => {
    if (roiTouched.current) return;
    setRoiMin([...bounds.min] as [number, number, number]);
    setRoiMax([...bounds.max] as [number, number, number]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roiKey]);

  // Publish the box so the AI tab can ask for an ROI-only export of its prediction.
  // Deliberately keyed on the STATE rather than hooked into setRoiBox(): a viewport gizmo
  // drag reaches React only through the log echo above, which updates roiMin/roiMax and
  // never calls setRoiBox — wiring the publish to the command would silently miss exactly
  // the boxes the user positioned by hand.
  useEffect(() => {
    setRoiSelection(roiMin, roiMax);
  }, [roiMin, roiMax]);

  // Same treatment the ROI inputs get: seed the sphere from the scene's real bounds
  // once the manifest arrives, unless the user has already placed it themselves.
  const seedTouched = useRef(false);
  useEffect(() => {
    if (seedTouched.current) return;
    const mid = [0, 1, 2].map((i) => (bounds.min[i] + bounds.max[i]) / 2) as
      [number, number, number];
    const span = Math.min(...[0, 1, 2].map((i) => bounds.max[i] - bounds.min[i]));
    setSeedCenter(mid);
    // A quarter of the SMALLEST extent: big enough to spread seeds, small enough that
    // the backend's per-axis ROI clamp does not have to shrink it.
    setSeedRadius(Math.max(0.05, Math.round(span * 0.25 * 100) / 100));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roiKey]);

  const doExtract = () => {
    flashBusy(VOXELIZE_MS);
    extractRoi("vtu");
  };

  const resetRoi = () => {
    roiTouched.current = false;
    const nmin = [...bounds.min] as [number, number, number];
    const nmax = [...bounds.max] as [number, number, number];
    setRoiMin(nmin);
    setRoiMax(nmax);
    setRoiBox(nmin, nmax);
    if (roiClip && field) flashBusy();
  };

  // ── Viewport drag → render ────────────────────────────────────────────────
  // Dragging the ROI box or the seed sphere with Omniverse's gizmo only moves the USD
  // prim; Kit sends no drag event back over the one-way command channel, so nothing
  // re-renders and the numbers below go stale. This button pulls the drag through:
  // the backend bakes the dragged box into its clip bounds, adopts the dragged seed
  // centre, re-voxelises inside the new region and re-traces the streamlines.
  const applyViewportDrag = () => {
    if (busy || slBusy) return;
    syncFromViewport();
    if (roiClip && field) flashBusy(VOXELIZE_MS);
    if (slApplied) flashSlBusy();
  };

  // The backend echoes what it adopted from the viewport ("ROI from viewport …",
  // "seed from viewport …") to the Kit log, which the Operation Log already streams
  // into this page. Parse those receipts so the number inputs show where the gizmos
  // ACTUALLY are after a drag — the only path by which viewport state reaches React.
  const lastEchoId = useRef(0);
  useEffect(() => {
    const ROI_RE = /ROI from viewport -> min=\(([^)]+)\)\s*max=\(([^)]+)\)/;
    const SEED_RE = /seed from viewport -> center=\(([^)]+)\)/;
    const triple = (s: string) =>
      s.split(",").map((v) => parseFloat(v.trim())) as [number, number, number];
    const ok = (v: number[]) => v.length === 3 && v.every((n) => Number.isFinite(n));
    return subscribeLog((entries) => {
      for (const e of entries) {
        if (e.id <= lastEchoId.current) continue;
        lastEchoId.current = e.id;
        if (e.src !== "kit") continue;
        const roi = ROI_RE.exec(e.msg);
        if (roi) {
          const mn = triple(roi[1]);
          const mx = triple(roi[2]);
          if (ok(mn) && ok(mx)) {
            setRoiMin(mn);
            setRoiMax(mx);
          }
        }
        const seed = SEED_RE.exec(e.msg);
        if (seed) {
          const c = triple(seed[1]);
          if (ok(c)) setSeedCenter(c);
        }
      }
    });
  }, []);

  // ── Streamlines ───────────────────────────────────────────────────────────
  // Tracing re-integrates on the GPU (native kit-cae Warp RK4(5)) — seconds for the
  // GTC dataset. Flash a busy state until the backend logs "streamlines generated"
  // (or the fallback timer elapses), mirroring the volume's flashBusy.
  const flashSlBusy = (ms = VOXELIZE_MS) => {
    setSlBusy(true);
    window.clearTimeout(slBusyTimer.current);
    slBusyTimer.current = window.setTimeout(() => setSlBusy(false), ms);
    waitForLog((e) => e.src === "kit" && /streamlines (generated|removed)/.test(e.msg), ms)
      .then(() => {
        window.clearTimeout(slBusyTimer.current);
        setSlBusy(false);
      })
      .catch(() => {});
  };

  const toggleStreamlines = () => {
    const v = !slOn;
    setSlOn(v);
    if (v) {
      // Show the draggable seed-sphere preview (no trace until Apply).
      setSeedVisible(true);
      setSeedSphere(true, seedCenter, seedRadius, nSeeds);
    } else {
      // Remove the streamlines + seed sphere.
      setStreamlines(false, slField, seedCenter, seedRadius, nSeeds, maxSteps);
      setSlApplied(false);
    }
  };

  // Show/hide the seed-sphere preview without removing the streamlines (like the ROI box).
  const toggleSeedSphere = () => {
    const v = !seedVisible;
    setSeedVisible(v);
    setSeedSphere(v, seedCenter, seedRadius, nSeeds);
  };

  // Live-move the preview sphere as the user edits its centre / radius / count (keeping
  // its current visibility).
  const onSeedEdit = (i: 0 | 1 | 2, value: number) => {
    if (Number.isNaN(value)) return;
    seedTouched.current = true;
    const c = [...seedCenter] as [number, number, number];
    c[i] = value;
    setSeedCenter(c);
    if (slOn) setSeedSphere(seedVisible, c, seedRadius, nSeeds);
  };
  const onSeedRadius = (value: number) => {
    if (Number.isNaN(value) || value <= 0) return;
    seedTouched.current = true;
    setSeedRadius(value);
    if (slOn) setSeedSphere(seedVisible, seedCenter, value, nSeeds);
  };
  const onNSeeds = (value: number) => {
    if (!Number.isFinite(value)) return;
    const n = Math.max(1, Math.min(1600, Math.round(value)));
    setNSeeds(n);
    if (slOn) setSeedSphere(seedVisible, seedCenter, seedRadius, n);
  };

  const applyStreamlines = () => {
    if (slBusy) return;
    setStreamlines(true, slField, seedCenter, seedRadius, nSeeds, maxSteps);
    setSlApplied(true);
    flashSlBusy();
  };

  // Switching the colour field re-traces (the backend regenerates), so only fire it
  // eagerly if streamlines already exist; otherwise it takes effect on the next Apply.
  const onSlField = (f: Field) => {
    if (slBusy) return;
    setSlField(f);
    if (slApplied) {
      setStreamlines(true, f, seedCenter, seedRadius, nSeeds, maxSteps);
      flashSlBusy();
    }
  };

  const dom = field ? domainOf(field) : null;
  const cur = field ? range[field] : null;

  // Drag the floating colorbar to reposition it anywhere over the viewport.
  const startCbDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY, ox = cbPos.x, oy = cbPos.y;
    const onMove = (ev: MouseEvent) => {
      setCbPos({
        x: Math.min(Math.max(0, ox + ev.clientX - sx), window.innerWidth - 70),
        y: Math.min(Math.max(0, oy + ev.clientY - sy), window.innerHeight - 30),
      });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };
  // Double-click the colorbar to edit its colour (display) range; Apply pushes it.
  const openCbEdit = () => {
    if (!cur) return;
    setCbLo(String(cur[0]));
    setCbHi(String(cur[1]));
    setCbEdit(true);
  };
  const applyCbEdit = () => {
    const lo = parseFloat(cbLo), hi = parseFloat(cbHi);
    if (field && isFinite(lo) && isFinite(hi) && lo < hi) {
      setRange({ ...range, [field]: [lo, hi] });
      setVolumeRange(lo, hi);
    }
    setCbEdit(false);
  };

  return (
    <>
    <div className={sceneReady ? "controls" : "controls disabled"}>
      <section className={enabled ? undefined : "sec-disabled"}>
        <h3>CFD Volume{busy && <span className="rebuilding">Rebuilding…</span>}</h3>
        <div className="seg">
          <button className={field === null ? "seg-btn active" : "seg-btn"} disabled={busy || !enabled} onClick={() => selectField(null)}>
            Off
          </button>
          <button className={field === "T" ? "seg-btn active" : "seg-btn"} disabled={busy || !enabled} onClick={() => selectField("T")}>
            Temp
          </button>
          <button className={field === "U" ? "seg-btn active" : "seg-btn"} disabled={busy || !enabled} onClick={() => selectField("U")}>
            Velocity
          </button>
        </div>
        {dom && cur && (
          <div className="sliders">
            <label>
              <span>Low&nbsp;<b>{cur[0].toFixed(1)} {dom.unit}</b></span>
              <input type="range" min={dom.min} max={dom.max} step={(dom.max - dom.min) / 200}
                value={cur[0]} onChange={(e) => onSlide(0, parseFloat(e.target.value))} />
            </label>
            <label>
              <span>High&nbsp;<b>{cur[1].toFixed(1)} {dom.unit}</b></span>
              <input type="range" min={dom.min} max={dom.max} step={(dom.max - dom.min) / 200}
                value={cur[1]} onChange={(e) => onSlide(1, parseFloat(e.target.value))} />
            </label>
            <button className="btn autoscale" onClick={onAutoscale} disabled={busy} title="Fit the colour range to the field's full min/max">
              ⤢ Autoscale range
            </button>
            <div className="cb-hint">Colour legend floats on the viewport — drag to move, double-click to set range.</div>
          </div>
        )}
      </section>

      {/* ROI is dataset-INDEPENDENT: usable as soon as the geometry loads, so the user
          can set the region + Clip BEFORE any result is loaded. Gated on sceneReady, not
          on a loaded dataset. */}
      <section>
        <h3>Region of Interest</h3>
        <div className="ds-note">
          Set the region <b>before</b> or after loading a result. With <b>Clip</b> on,
          showing a field (Temp/Velocity) renders it only inside this box.
        </div>
        <button className={roiVisible ? "btn block active" : "btn block"} onClick={toggleRoi} disabled={!sceneReady}>
          {roiVisible ? "Hide ROI Box" : "Show ROI Box"}
        </button>
        <button className={roiClip ? "btn block active" : "btn block"} onClick={toggleClip} disabled={!roiVisible || busy || !sceneReady}>
          {roiClip ? "Clipping: ON" : "Clip Volume to ROI"}
        </button>
        {/* EXTRACTION, not clipping. The clip keeps the whole result loaded and limits
            what the voxeliser reads; this replaces the dataset with the ROI's cells, so
            the field stays confined even with the box hidden — which is also how you can
            tell the two apart on screen. */}
        <button className="btn block" onClick={doExtract} disabled={!enabled || busy}
          title="Replace the dataset with only the cells inside the ROI box">
          ✂ Extract ROI as dataset
        </button>
        <button className="btn block" onClick={applyViewportDrag}
          disabled={!sceneReady || busy || slBusy}
          title="Re-voxelise the volume and re-trace the streamlines where you dragged the gizmos">
          ⟳ Re-render Dragged Region
        </button>
        <div className="ds-note">
          Dragged the box or the seed ball in the viewport? The render does not follow a
          drag on its own — click <b>Re-render Dragged Region</b> to rebuild it there.
        </div>
        <div className="roi-box">
          <div className="roi-head">
            <span>Box bounds (m)</span>
            <button className="roi-reset" onClick={resetRoi} disabled={!sceneReady}>Reset</button>
          </div>
          {ROI_AXES.map(({ i, label }) => (
            <div className="roi-row" key={i}>
              <span className="roi-axis">{label}</span>
              <input type="number" step={0.1} value={roiMin[i]} disabled={!sceneReady}
                onChange={(e) => onRoiEdit("min", i, parseFloat(e.target.value))} />
              <span className="roi-dash">–</span>
              <input type="number" step={0.1} value={roiMax[i]} disabled={!sceneReady}
                onChange={(e) => onRoiEdit("max", i, parseFloat(e.target.value))} />
            </div>
          ))}
        </div>
      </section>

      <section className={enabled ? undefined : "sec-disabled"}>
        <h3>Streamlines{slBusy && <span className="rebuilding">Tracing…</span>}</h3>
        <button className={slOn ? "btn block active" : "btn block"} onClick={toggleStreamlines} disabled={!enabled}>
          {slOn ? "Streamlines: ON" : "Add Streamlines"}
        </button>
        {slOn && (
          <>
            <div className="ds-note">
              Drag the blue <b>seed sphere</b> in the viewport (or set its position below),
              then <b>Apply</b>. Lines follow velocity <b>U</b>; colour by the field below.
              With ROI <b>Clip</b> on, seeds and lines stay inside the ROI box.
            </div>
            <button className={seedVisible ? "btn block active" : "btn block"}
                    onClick={toggleSeedSphere} disabled={!enabled}>
              {seedVisible ? "Hide seed sphere" : "Show seed sphere"}
            </button>
            <div className="seg">
              <button className={slField === "T" ? "seg-btn active" : "seg-btn"} disabled={slBusy} onClick={() => onSlField("T")}>
                Colour: Temp
              </button>
              <button className={slField === "U" ? "seg-btn active" : "seg-btn"} disabled={slBusy} onClick={() => onSlField("U")}>
                Colour: |U|
              </button>
            </div>
            <div className="roi-box">
              <div className="roi-head"><span>Seed sphere (m)</span></div>
              {ROI_AXES.map(({ i, label }) => (
                <div className="roi-row" key={i}>
                  <span className="roi-axis">{label}</span>
                  <input type="number" step={0.1} value={seedCenter[i]} disabled={!enabled}
                    onChange={(e) => onSeedEdit(i, parseFloat(e.target.value))} />
                </div>
              ))}
              <div className="roi-row">
                <span className="roi-axis">Radius</span>
                <input type="number" step={0.1} min={0.1} value={seedRadius} disabled={!enabled}
                  onChange={(e) => onSeedRadius(parseFloat(e.target.value))} />
              </div>
              <div className="roi-row">
                <span className="roi-axis">Seeds</span>
                <input type="number" step={1} min={1} max={1600} value={nSeeds} disabled={!enabled}
                  onChange={(e) => onNSeeds(parseFloat(e.target.value))} />
              </div>
              <div className="roi-row">
                <span className="roi-axis">Max steps</span>
                <input type="number" step={50} min={50} max={5000} value={maxSteps} disabled={!enabled}
                  onChange={(e) => setMaxSteps(Math.max(50, Math.round(parseFloat(e.target.value) || 500)))} />
              </div>
            </div>
            <button className="btn block primary" onClick={applyStreamlines} disabled={slBusy || !enabled}>
              {slBusy ? "Tracing…" : slApplied ? "Re-apply" : "Apply"}
            </button>
          </>
        )}
      </section>
    </div>

    {/* Floating colour legend (colorbar) — drag to move, double-click to edit range.
        PORTALED to <body>: CfdControls lives inside a `.panel` whose backdrop-filter
        makes it the containing block for position:fixed AND whose overflow:hidden
        would clip this widget (it sits far outside the panel). The portal escapes
        both, so the colorbar floats freely over the whole viewport. Same for the
        range-edit modal. */}
    {field && dom && cur && createPortal(
      <div className="colorbar-float" style={{ left: cbPos.x, top: cbPos.y }}
           onMouseDown={startCbDrag} onDoubleClick={openCbEdit}
           title="Drag to move · double-click to set colour range">
        <div className="colorbar-label">{dom.label} ({dom.unit})</div>
        <div className="colorbar-gradient" />
        <div className="colorbar-ticks">
          <span>{cur[0].toFixed(1)}</span>
          <span>{((cur[0] + cur[1]) / 2).toFixed(1)}</span>
          <span>{cur[1].toFixed(1)}</span>
        </div>
      </div>,
      document.body,
    )}

    {/* Range-edit dialog (opened by double-clicking the colorbar). */}
    {cbEdit && dom && createPortal(
      <div className="cb-modal-backdrop" onMouseDown={() => setCbEdit(false)}>
        <div className="cb-modal" onMouseDown={(e) => e.stopPropagation()}>
          <h4>Colour range — {dom.label} ({dom.unit})</h4>
          <label>Low<input type="number" step={0.1} value={cbLo}
            onChange={(e) => setCbLo(e.target.value)} autoFocus /></label>
          <label>High<input type="number" step={0.1} value={cbHi}
            onChange={(e) => setCbHi(e.target.value)} /></label>
          <div className="cb-modal-btns">
            <button onClick={() => setCbEdit(false)}>Cancel</button>
            <button className="primary" onClick={applyCbEdit}>Apply</button>
          </div>
        </div>
      </div>,
      document.body,
    )}
    </>
  );
}
