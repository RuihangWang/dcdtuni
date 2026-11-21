#!/usr/bin/env bash
# Launch the kit-cae streaming app with the dc.cfd_bridge extension + GTC data.
set -euo pipefail
# Derived from this script's own location, never hardcoded: the previous absolute
# path meant a clone anywhere else (different user, different mount) died at the
# `cd "$BUILD"` below with "No such file or directory" before Kit ever started.
# scripts -> cfd_demo -> omniverse -> frontend -> src -> repo root, i.e. five levels.
# Built Kit/resources may be borrowed via separate explicit overrides.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"
# A worktree may borrow built Kit/resources; its bridge extension stays local.
ASSET_ROOT="${DC_CFD_ASSET_ROOT:-$ROOT}"
REF="$ASSET_ROOT/src/frontend/omniverse/Ref_code/kit-cae"
BUILD="${DC_CFD_KIT_BUILD:-$REF/_build/linux-x86_64/release}"
DEMO="$ROOT/src/frontend/omniverse/cfd_demo"
EXT_DIR="$DEMO/kit/extensions"
# One physical GPU, default GPU0. CUDA is pinned by UUID; Vulkan's index
# must still match the same PCI bus (its enumeration ignores the CUDA mask).
GPU="${DC_CFD_GPU:-${CFD_GPU:-0}}"
[[ "$GPU" =~ ^[0-9]+$ ]] || { echo "[start_demo] ERROR: GPU must be a physical index" >&2; exit 2; }
# Signalling port the web UI dials. Parameterised for start_server.sh --ov-port;
# the historical fixed value stays the default.
SIGNAL_PORT="${DC_CFD_SIGNAL_PORT:-8301}"
[[ "$SIGNAL_PORT" =~ ^[0-9]+$ ]] && (( SIGNAL_PORT >= 1 && SIGNAL_PORT <= 65535 )) \
  || { echo "[start_demo] ERROR: signalling port must be 1..65535" >&2; exit 2; }
# The RTP/UDP video transport has its own port. A signaling override alone
# cannot isolate two Kit instances; retain the vendor default for legacy use.
STREAM_PORT="${DC_CFD_STREAM_PORT-47998}"
[[ "$STREAM_PORT" =~ ^0*([1-9][0-9]{0,4})$ ]] &&
  (( 10#${BASH_REMATCH[1]} <= 65535 )) \
  || { echo "[start_demo] ERROR: UDP stream port must be an integer in 1..65535" >&2; exit 2; }
STREAM_PORT="${BASH_REMATCH[1]}"
mkdir -p "$ROOT/.codex-tmp/ov-runtime"
RUNTIME="${DC_CFD_RUNTIME_DIR:-$(mktemp -d "$ROOT/.codex-tmp/ov-runtime/${SIGNAL_PORT}.XXXXXX")}"
RUNTIME="$(realpath -m "$RUNTIME")"
[[ "$RUNTIME" == "$ROOT/"* ]] \
  || { echo "[start_demo] ERROR: runtime directory must stay inside this checkout" >&2; exit 2; }
mkdir -p "$RUNTIME"
export TMPDIR="$RUNTIME/tmp"
mkdir -p "$TMPDIR"
# Kit portable mode redirects data/cache/log tokens into this checkout.
# Stable GPU caches avoid discarding compiled shaders on every restart.
CACHE_ROOT="$ROOT/.codex-tmp/ov-cache"
mkdir -p "$CACHE_ROOT"/{kit,gl,cuda,xdg-cache,xdg-config,xdg-data}
export __GL_SHADER_DISK_CACHE_PATH="$CACHE_ROOT/gl"
export CUDA_CACHE_PATH="$CACHE_ROOT/cuda"
export XDG_CACHE_HOME="$CACHE_ROOT/xdg-cache"
export XDG_CONFIG_HOME="$CACHE_ROOT/xdg-config"
export XDG_DATA_HOME="$CACHE_ROOT/xdg-data"
# Establish ownership before GPU/display preflight. The supervised child is
# reached only with the supervisor's resolved, UUID-bound selection.
if [[ "${1:-}" != --supervised-child ]]; then
    # Refuse synchronously even in unmanaged/background mode; exit 0 below
    # means a supervisor was launched, never that streaming video is ready.
    # Reuse the same checker as the supervisor without probing GPU/display.
    python3 - "$DEMO/scripts" "$STREAM_PORT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from supervise_kit import _check_stream_port
try:
    _check_stream_port(int(sys.argv[2]))
except RuntimeError as error:
    print(f"[start_demo] ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
    SUPERVISOR=(python3 "$DEMO/scripts/supervise_kit.py" --runtime "$RUNTIME"
        --display "${DC_CFD_DISPLAY:-:99}" --gpu "$GPU" --signal-port "$SIGNAL_PORT"
        --stream-port "$STREAM_PORT"
        --required-executable "$BUILD/omni.cae_streaming.kit.sh"
        --startup-timeout "${DC_CFD_STARTUP_TIMEOUT:-300}"
        --preflight-timeout "${DC_CFD_PREFLIGHT_TIMEOUT:-20}")
    if [[ "${DC_CFD_MANAGED:-0}" == 1 ]]; then
        exec "${SUPERVISOR[@]}" --managed --parent-pid "${DC_CFD_PARENT_PID:-$PPID}" -- \
            bash "$DEMO/scripts/start_demo.sh" --supervised-child
    fi
    "${SUPERVISOR[@]}" -- bash "$DEMO/scripts/start_demo.sh" --supervised-child \
        > "$RUNTIME/launcher.log" 2>&1 &
    echo "[start_demo] Supervisor PID $! ; log: $RUNTIME/kit.log"
    echo "[start_demo] to start web UI: (cd $DEMO/web && npm run dev)"
    exit 0
fi
[[ "${DC_CFD_SUPERVISOR_PID:-}" =~ ^[0-9]+$ ]] &&
    [[ "$PPID" == "$DC_CFD_SUPERVISOR_PID" ]] &&
    [[ "$(cat "$RUNTIME/supervisor.pid")" == "$DC_CFD_SUPERVISOR_PID" ]] ||
    { echo "[start_demo] ERROR: supervised child requires its live owner" >&2; exit 2; }
GPU_INDEX="${DC_CFD_RESOLVED_GPU:-}"
GPU_UUID="${DC_CFD_RESOLVED_UUID:-}"
GPU_BUS_ID="${DC_CFD_RESOLVED_PCI:-}"
ACTIVE_GPU="${DC_CFD_RESOLVED_ACTIVE_GPU:-}"
VULKAN_ICD="${DC_CFD_RESOLVED_ICD:-}"
[[ "$GPU_INDEX" == "$GPU" && "$GPU_UUID" == GPU-* &&
   "$ACTIVE_GPU" =~ ^[0-9]+$ && -f "$VULKAN_ICD" ]] ||
    { echo "[start_demo] ERROR: missing supervised GPU selection" >&2; exit 2; }
export CUDA_VISIBLE_DEVICES="$GPU_UUID"
export VK_DRIVER_FILES="$VULKAN_ICD" VK_ICD_FILENAMES="$VULKAN_ICD"
CAE_DEVICE="cuda:0"
AI_ENABLED="${DC_CFD_AI:-0}"
[[ "$AI_ENABLED" == 0 ]] || { echo "[start_demo] ERROR: this display launcher does not start an AI GPU worker" >&2; exit 2; }
# NOTE: /app/window/hideUi MUST stay false. In this headless --no-window
# streaming config it suppresses the RTX geometry render (only scene-overlay
# layers like the grid survive; USD meshes go black). Render-only is instead
# achieved by the dc.cfd_bridge extension, which hides the viewport menubar +
# axis layers + HUD + docked panels without disturbing the render. Left as a
# toggle only for debugging.
HIDE_UI="${CFD_HIDE_UI:-false}"

# Data for the extension. internal.vti = the 1000-step result resampled from
# internal.vtu onto a regular grid (~0.2 m). The raw 1.35 M-cell unstructured .vtu
# fails NanoVDB voxelisation ("No source dataset found" / empty extent — the
# voxeliser produced no grid), while a STRUCTURED image voxelises directly and
# reliably. Regenerate with scripts' resample_vti.py if internal.vtu changes.
export DC_CFD_VTU="$ASSET_ROOT/src/frontend/omniverse/cfd_demo/data/internal.vtu"
export DC_CFD_JSON="$REF/data/GTC/GeoUSD/GTC2025.json"
export DC_CFD_GEO="$REF/data/GTC/GeoUSD"
export DC_CFD_RANGES="$ASSET_ROOT/src/frontend/omniverse/cfd_demo/data/ranges.json"
# Both come from the out-of-band asset bundle. Warn only: the backend starts and a
# .cfdproj scene loads without them; scene.py opens them on demand.
[ -f "$DC_CFD_JSON" ] || echo "[start_demo] WARNING: GTC scene assets missing ($DC_CFD_JSON);" \
    "only .cfdproj geometry will load - extract gtc_demo_assets.tar.zst, cfd_demo/DEPLOY.md section 2c." >&2
[ -f "$DC_CFD_VTU" ] || echo "[start_demo] note: optional pre-baked result $DC_CFD_VTU absent" \
    "(it ships in the same bundle, cfd_demo/DEPLOY.md section 2c)." >&2
# Rack model: the GB300 NVL72 asset (golden inlet panels) from the kit-cae
# simulation-in-context example. It is a HEAVY photoreal asset (1927 prims +
# 115 MB textures); USD scenegraph-instancing collapses all copies to ONE
# prototype so RAM is bounded (~17 GB), BUT Hydra's first-frame sync of the
# instances on a SINGLE GPU scales super-linearly: ~8 racks reach a render-ready
# scene in ~2-3 min, while 24/50 racks stall the build past 7-10 min (the scene
# never finishes the initial sync). So only the first DC_CFD_GB300_COUNT racks
# (the camera-facing "hero" row) use GB300; the rest keep the lightweight
# GTC-sample rack. Default = 8 (a usable startup with NVL72 racks in the
# foreground). Raise it for more golden racks at the cost of a much slower
# startup; set 0 (or unset DC_CFD_RACK_USD) for the all-lightweight fast path.
# Full-50 NVL72 needs a decimated GB300 proxy/LOD, not the raw asset.
export DC_CFD_RACK_USD="${DC_CFD_RACK_USD:-$REF/data/kit_cae_user_guide_data/examples/02_simulation-data-in-context/GB300.usd}"
export DC_CFD_GB300_COUNT="${DC_CFD_GB300_COUNT:-8}"

# vtk delegate python deps (per kit-cae CLAUDE.md). Kit embeds python3.10 and needs
# vtk importable, so its user site-packages goes on the path.
#
# This used to hardcode one user's ~/.local path. That fails SILENTLY on any other
# machine: Python just drops non-existent sys.path entries, so Kit boots, geometry
# renders, and only loading a .vtu dataset fails — with the reason buried in
# $RUNTIME/kit.log. Ask python3.10 where its user site actually is, and only
# prepend directories that exist.
DC_VTK_SITE="${DC_CFD_VTK_SITE:-$(python3.10 -c 'import site;print(site.getusersitepackages())' 2>/dev/null || true)}"
for d in "$DC_VTK_SITE" /usr/lib/python3/dist-packages; do
    [ -n "$d" ] && [ -d "$d" ] && PYTHONPATH="$d:${PYTHONPATH:-}"
done
export PYTHONPATH="${PYTHONPATH:-}"

# Reuse a live display or let the supervisor own its Xvfb child. Standard
# X11 locks/sockets keep their system location; our logs stay in the checkout.
export DISPLAY="${DC_CFD_DISPLAY:-:99}"

cd "$BUILD"
echo "[start_demo] launching kit-cae streaming + dc.cfd_bridge on single GPU #$GPU (Vulkan activeGpu=$ACTIVE_GPU, multiGpu off; signal :$SIGNAL_PORT, UDP stream :$STREAM_PORT)"
# Load the bridge as a normal Kit extension: --ext-folder points Kit at our
# extension tree, --enable resolves dc.cfd_bridge (and its declared deps) through
# the dependency solver and drives the standard IExt lifecycle. This is only
# possible because the kit-cae _build was rebuilt on this host (repo.sh build), so
# the dependency solver resolves the app's own kit_cae_streaming template + all
# omni.cae.* extensions locally.
# Render-only viewport (user req #1): hideUi makes the viewport fill the window
# and hides its menubar + axis gizmo + dock tabs; the HUD flag drops the
# FPS/GPU/memory overlay. The main menu bar + docked panels are hidden by the
# dc.cfd_bridge extension (_hide_native_ui). The web React UI is the sole chrome.
KIT_COMMAND=( "$BUILD/omni.cae_streaming.kit.sh" --no-window
  --portable-root "$CACHE_ROOT/kit"
  --/log/file="$RUNTIME/kit-internal.log"
  --/app/userConfigPath="$RUNTIME/user.config.json"
  --/app/renderer/resolution/width=1600
  --/app/renderer/resolution/height=900
  --/app/window/width=1600
  --/app/window/height=900
  --ext-folder "$EXT_DIR"
  --enable dc.cfd_bridge
  --/exts/omni.kit.livestream.app/primaryStream/signalPort="$SIGNAL_PORT"
  --/exts/omni.kit.livestream.app/primaryStream/streamPort="$STREAM_PORT"
  --/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize=true
  --/renderer/activeGpu="$ACTIVE_GPU"
  --/renderer/multiGpu/enabled=false
  --/persistent/exts/omni.cae.viz/defaultDeviceForAuto="$CAE_DEVICE"
  --/persistent/exts/omni.cae.viz/defaultBoundingBoxDevice="$CAE_DEVICE"
  --/exts/omni.cae.viz/defaultDeviceForAuto="$CAE_DEVICE"
  --/exts/omni.cae.viz/defaultBoundingBoxDevice="$CAE_DEVICE"
  --/exts/omni.warp.core/devices="$CAE_DEVICE"
  --/app/window/hideUi="$HIDE_UI"
  --/persistent/app/viewport/Viewport/Viewport0/hud/visible=false
 )
# Fixed opt-in only; request messages and arbitrary argument strings cannot enable it.
if [[ "${DC_CFD_BOUNDED_DISPLAY_READBACK:-}" == bounded-display-v1 ]]; then
    KIT_COMMAND+=( --/exts/dc.cfd_bridge/boundedDisplayReadback/enabled=true )
fi
# The built wrapper also uses exec; disallow an inherited EXEC override.
unset EXEC
exec "${KIT_COMMAND[@]}"
