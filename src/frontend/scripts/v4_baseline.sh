#!/usr/bin/env bash
# v4_baseline.sh — capture pre-refactor baseline digests for one case.
#
# Usage: v4_baseline.sh <CaseName> <cfdproj-path-relative-to-repo> [--mesh-only]
# Example: v4_baseline.sh Base_leakage case/Base_leakage/Base_leakage.cfdproj
#
# Requires: a dcdtuni server started by src/start_server.sh on $DCDTUNI_PORT
# (default 8000), browser-use on PATH (GPU WebGL install), jq. The login uses
# DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD, else the launcher's local-mode
# file src/server/auth-local/admin_credentials (admin_login.sh).
#
# What it does (and why this split):
#   - GPU WebGL browser-use loads the project and screenshots it. This is the
#     §9.4 "scene renders on the real renderer" evidence (renderer must be NVIDIA).
#   - The server's /api/browser/export-case writes STL + .cfdrun into the case dir
#     deterministically (headless, no download prompt). This is the export baseline.
#   - /api/run-mesh drives mesh (+solve) through the real pipeline; job-status is
#     polled on .done / .returncode (the server has no status-string field).
#
# Digests land in docs/superpowers/specs/baselines/2026-06-12-v4-refactor/<CaseName>/.
set -euo pipefail

NAME="${1:?usage: v4_baseline.sh <CaseName> <cfdproj-rel-path> [--mesh-only]}"
PROJ="${2:?missing cfdproj path}"
MODE="${3:-full}"
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
# The port to drive. Not a hardcoded 8005 — on a shared host that is usually
# someone else's server, and one server holds ONE global case dir. The default
# is the launcher's; set DCDTUNI_PORT to whatever its banner printed.
PORT="${DCDTUNI_PORT:-8000}"
BASE="http://localhost:$PORT"
COOKIE=/tmp/stlgen_cookie.txt
OUT="$REPO/docs/superpowers/specs/baselines/2026-06-12-v4-refactor/$NAME"
CASE_DIR="$REPO/$(dirname "$PROJ")"
PROJ_ABS="$REPO/$PROJ"
mkdir -p "$OUT"
export PATH="$HOME/.local/bin:$PATH"
# shellcheck source=admin_login.sh
. "$REPO/src/frontend/scripts/admin_login.sh"
dcdtuni_admin_credentials admin 'WIN#@desk'

# log() writes to stderr so that functions whose stdout is a return value
# (e.g. poll_job) are not polluted by progress lines.
log() { echo "[v4_baseline:$NAME] $*" >&2; }

# Poll /api/job-status until the job is done; echo the final returncode.
# The server reports {"done": bool, "returncode": int|null, "checkMeshOk": ...},
# NOT a status string.
poll_job() {
  local job="$1" label="$2" interval="$3" last=""
  while :; do
    last=$(curl -sf -b "$COOKIE" "$BASE/api/job-status?job_id=$job") || { sleep "$interval"; continue; }
    local done rc
    done=$(echo "$last" | jq -r '.done')
    rc=$(echo "$last" | jq -r '.returncode // "null"')
    log "$label job=$job done=$done rc=$rc"
    [ "$done" = "true" ] && { echo "$last" > "$OUT/${label}_jobstatus.json"; echo "$rc"; return 0; }
    sleep "$interval"
  done
}

# 1. Auth + point the server at this case dir
dcdtuni_curl_login "$BASE" "$COOKIE"
curl -sf -b "$COOKIE" -X POST "$BASE/api/set-case-dir" -H "Content-Type: application/json" \
  -d "{\"caseDir\":\"$CASE_DIR\"}" | jq -c .

# 2. GPU WebGL browser: renderer sanity check + load + screenshot + scene counts.
# browser-use eval does NOT await promises, so async steps are fire-and-forget
# followed by a sleep + a poll. The heavy editor page makes Page.navigate() time
# out at 20s even though it loads fine, so `open` failure is non-fatal here.
browser-use open "$BASE/STL_generate.html" || log "navigate reported timeout (heavy WebGL page still loads; verified by the evals below)"
RENDERER=$(browser-use eval "(()=>{const c=document.createElement('canvas');const gl=c.getContext('webgl2')||c.getContext('webgl');const i=gl.getExtension('WEBGL_debug_renderer_info');return i?gl.getParameter(i.UNMASKED_RENDERER_WEBGL):'unknown';})()")
log "WebGL renderer: $RENDERER"
case "$RENDERER" in
  *NVIDIA*) ;;
  *) log "FATAL: WebGL not on NVIDIA GPU (got: $RENDERER) — fix /install-WebGL-browse-use first"; browser-use close; exit 1;;
esac

# Authenticate the browser session in-page (same-origin POST stores the auth cookie).
LOGIN_STATUS=$(browser-use eval "$(dcdtuni_browser_login_js)" 2>&1) || true
case "$LOGIN_STATUS" in
  *"result: 200"*) log "browser login ok" ;;
  *) log "FATAL: browser login refused (credentials from $DCDTUNI_LOGIN_SOURCE): $LOGIN_STATUS"; browser-use close; exit 1;;
esac
# Load the project. read-file resolves paths under the server static root, so pass
# the ABSOLUTE path (same as the curl calls). Then fit camera + render before shot.
browser-use eval "window.__bl_n=undefined; window.__bl_err=undefined; fetch('/api/read-file?path=$PROJ_ABS').then(r=>r.json()).then(j=>{if(j.ok){window._applyProjectData(JSON.parse(j.content), '$NAME'); window.__bl_n=window.state.construction.length;}else{window.__bl_err=j.error;}}).catch(e=>{window.__bl_err=String(e);}); 'load-fired'"
sleep 4
browser-use eval "if(typeof fitCameraToScene==='function'){try{fitCameraToScene();}catch(e){}} if(typeof requestRender==='function')requestRender(); window.__bl_err?('ERR:'+window.__bl_err):('loaded:'+window.__bl_n)"
sleep 2
browser-use screenshot "$OUT/load_screenshot.png"
browser-use eval "JSON.stringify({n: window.state.construction.length, kinds: window.state.construction.reduce((a,m)=>(a[m.kind]=(a[m.kind]||0)+1,a),{})})" \
  | sed 's/^result: //; s/^"//; s/"$//; s/\\"/"/g' | tee "$OUT/scene_counts.json"
browser-use close

# 3. Server-side export of STL + .cfdrun into the case dir (deterministic, headless)
EXPORT=$(curl -sf -b "$COOKIE" -X POST "$BASE/api/browser/export-case" \
  -H "Content-Type: application/json" -d "{\"projPath\":\"$PROJ_ABS\"}")
echo "$EXPORT" | jq . > "$OUT/export_case.json"
log "export-case: $(echo "$EXPORT" | jq -c '{modelCount, stlCount, cfdrunPath}')"
CFDRUN=$(echo "$EXPORT" | jq -r '.cfdrunPath')
[ -f "$CFDRUN" ] && cp "$CFDRUN" "$OUT/baseline.cfdrun" || log "WARN: cfdrun not found at $CFDRUN"

# 4. STL inventory: per-file sha256 + sorted solid names
TRI="$CASE_DIR/Mesh/constant/triSurface"
if [ -d "$TRI" ]; then
  ( cd "$TRI" && for f in *.stl; do
      [ -e "$f" ] || continue
      echo "== $f $(sha256sum "$f" | cut -d' ' -f1)"
      grep '^solid ' "$f" | sort
    done ) > "$OUT/stl_solids.txt"
  log "stl inventory: $(grep -c '^==' "$OUT/stl_solids.txt") files, $(grep -c '^solid ' "$OUT/stl_solids.txt") solids"
else
  log "WARN: triSurface dir not found at $TRI"
fi

# 5. Mesh (reuses the .cfdrun + STLs just written; no body payload needed)
MJOB=$(curl -sf -b "$COOKIE" -X POST "$BASE/api/run-mesh" -H "Content-Type: application/json" \
  -d '{"meshOnly":true}' | jq -r '.jobId // .job_id')
log "mesh job: $MJOB"
MRC=$(poll_job "$MJOB" mesh 30)
[ "$MRC" = "0" ] || { log "MESH FAILED rc=$MRC"; exit 1; }

# checkMesh / mesh digest: job-status summary + cell counts. The snappyHexMesh
# log (Mesh/log.mpi_solver after a mesh-only run) carries the snapped cell count
# even when no separate log.checkMesh file is written.
jq -c '{checkMeshOk, failedChecks, returncode}' "$OUT/mesh_jobstatus.json" > "$OUT/checkmesh.txt"
CKLOG=$(ls -t "$CASE_DIR"/Mesh/log.checkMesh* "$CASE_DIR"/Mesh/log.*heckMesh* "$CASE_DIR"/Mesh/log.mpi_solver 2>/dev/null | head -1 || true)
if [ -n "$CKLOG" ]; then
  grep -E 'Snapped mesh|cells:|Number of (cells|regions)|Overall number' "$CKLOG" | tail -20 >> "$OUT/checkmesh.txt" || true
else
  log "WARN: mesh/checkMesh log not located under $CASE_DIR/Mesh (job-status summary still captured)"
fi

if [ "$MODE" = "--mesh-only" ]; then log "mesh-only complete -> $OUT"; exit 0; fi

# 6. Solve (parallel, via the server pipeline — never serial)
SJOB=$(curl -sf -b "$COOKIE" -X POST "$BASE/api/run-mesh" -H "Content-Type: application/json" \
  -d '{"meshOnly":false,"solveOnly":true}' | jq -r '.jobId // .job_id')
log "solve job: $SJOB"
SRC=$(poll_job "$SJOB" solve 60)
[ "$SRC" = "0" ] || { log "SOLVE FAILED rc=$SRC"; exit 1; }

# 7. Solve digests: final residuals + monitor tails + sensor results.
# On a completed run the server archives everything into
# $CASE_DIR/log/<timestamp>_<name>/ and resets Mesh/, so the real solver log,
# postProcessing, and sensorResults live in the newest archive dir — NOT Mesh/.
ARCHIVE=$(ls -dt "$CASE_DIR"/log/*/ 2>/dev/null | head -1 || true)
# Prefer the raw OpenFOAM solver log (no server log-timestamp prefix) so the
# residual digest is byte-comparable across runs; fall back to the combined .log.
if   [ -n "$ARCHIVE" ] && [ -f "${ARCHIVE}log.mpi_solver" ]; then SOLVELOG="${ARCHIVE}log.mpi_solver"
elif [ -f "$CASE_DIR/Mesh/log.mpi_solver" ];                 then SOLVELOG="$CASE_DIR/Mesh/log.mpi_solver"
else SOLVELOG=$(ls -t "$ARCHIVE"*.log 2>/dev/null | head -1 || true); fi
if [ -n "$SOLVELOG" ]; then
  grep -E 'Solving for|continuity errors' "$SOLVELOG" | tail -24 > "$OUT/final_residuals.txt" || true
  log "residuals ($(wc -l < "$OUT/final_residuals.txt") lines) from ${SOLVELOG#$CASE_DIR/}"
else
  log "WARN: solver log not located (looked in '${ARCHIVE:-<none>}' and Mesh/)"
fi
PPDIR=$(ls -dt "$ARCHIVE"postProcessing "$CASE_DIR"/Mesh/postProcessing 2>/dev/null | head -1 || true)
if [ -n "$PPDIR" ] && [ -d "$PPDIR" ]; then
  ( cd "$PPDIR" && find . -name '*.dat' -print0 \
      | xargs -0 -I{} sh -c 'echo "== {}"; tail -2 "{}"' ) > "$OUT/monitors_tail.txt" 2>/dev/null || true
fi
if [ -n "$ARCHIVE" ] && [ -d "${ARCHIVE}sensorResults" ]; then
  cp -r "${ARCHIVE}sensorResults" "$OUT/sensorResults" 2>/dev/null || true
fi
log "baseline complete -> $OUT"
