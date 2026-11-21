#!/usr/bin/env bash
# Hardened /browser-verify acceptance gate.
#
# Loads every .cfdproj under the target path(s) through the REAL frontend load path
# in headless WebGL Chromium — window._applyProjectData(...) PLUS the post-load render
# hooks that a bare _applyProjectData skips (the liquid pipe renderer / valve render,
# which read state.construction and previously threw on the v2->v3 rename) — and asserts
# schemaVersion===3, construction>0, and visible meshes>0. This catches module-level
# state-shape regressions that a structure-only check misses.
#
# Usage:
#   bash src/frontend/scripts/verify_all_load.sh                    # all of case/
#   SKIP_SLAB=1 bash src/frontend/scripts/verify_all_load.sh case   # real case/ projects only
#   bash src/frontend/scripts/verify_all_load.sh case/Slab_dataset  # just the dataset
#   LOG=/tmp/x.log RELOAD_EVERY=250 ... (env): output log path, memory-reset cadence
#
# Requires: a server started by src/start_server.sh on $DCDTUNI_PORT (default 8000),
# Xvfb display in /tmp/browser-verify-xvfb.state, and browser-use. The script opens
# STL_generate.html and logs in itself with DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD,
# else the launcher's local-mode file src/server/auth-local/admin_credentials
# (admin_login.sh), and again after every RELOAD_EVERY projects.
set -uo pipefail

# The port to drive. Not a hardcoded 8005 — on a shared host that is usually
# someone else's server, and one server holds ONE global case dir. The default
# is the launcher's; set DCDTUNI_PORT to whatever its banner printed.
PORT="${DCDTUNI_PORT:-8000}"
# shellcheck source=admin_login.sh
. "$(dirname "${BASH_SOURCE[0]}")/admin_login.sh"
dcdtuni_admin_credentials admin 'WIN#@desk' || exit 1
export PATH="$HOME/.local/bin:$PATH"
export DISPLAY=:$(awk '{print $2}' /tmp/browser-verify-xvfb.state)

WEB=src/frontend/web/projects
LOG="${LOG:-/tmp/v3refactor/verify_all_case.log}"
RELOAD_EVERY="${RELOAD_EVERY:-250}"
SKIP_SLAB="${SKIP_SLAB:-0}"
TARGETS=("${@:-case}")
mkdir -p "$WEB" "$(dirname "$LOG")"; : > "$LOG"

if [ "$SKIP_SLAB" = "1" ]; then
  mapfile -t FILES < <(find "${TARGETS[@]}" -name '*.cfdproj' ! -name '*.bak' ! -path '*Slab_dataset*' | sort)
else
  mapfile -t FILES < <(find "${TARGETS[@]}" -name '*.cfdproj' ! -name '*.bak' | sort)
fi
TOTAL=${#FILES[@]}; OK=0; FAIL=0; i=0
log(){ echo "$@" | tee -a "$LOG"; }
log "[$(date +%T)] verifying $TOTAL .cfdproj (targets=${TARGETS[*]} skip_slab=$SKIP_SLAB)"

relogin(){
  browser-use open "http://localhost:$PORT/STL_generate.html" >/dev/null 2>&1; sleep 3
  local status
  status=$(browser-use eval "$(dcdtuni_browser_login_js)" 2>&1)
  case "$status" in
    *"result: 200"*) ;;
    *) log "FATAL: login on :$PORT refused (credentials from $DCDTUNI_LOGIN_SOURCE): ${status//$'\n'/ }"; exit 1;;
  esac
}
relogin

for f in "${FILES[@]}"; do
  i=$((i+1)); nm="v3v_${i}.cfdproj"; cp "$f" "$WEB/$nm"
  # _applyProjectData wraps its body in try/catch and swallows internal errors
  # (console.error + alert, no re-throw); `state` is a reused module-level var.
  # So a throw INSIDE _applyProjectData leaves stale state from the prior project
  # and the sv/c/m checks would pass on it. Capture the swallowed alert/console.error
  # during the call and surface it as `err` so such a load is failed, not silently passed.
  js="(function(){var _e=null,_oa=window.alert,_oc=console.error;window.alert=function(m){if(_e===null)_e=('alert:'+m).slice(0,180);};console.error=function(){if(_e===null)_e=('cerr:'+Array.prototype.slice.call(arguments).join(' ')).slice(0,180);};try{var x=new XMLHttpRequest();x.open('GET','/projects/${nm}',false);x.send();var d=JSON.parse(x.responseText);window._applyProjectData(d,'${nm}',{clearRemotePath:true});if(window._liquidPipeRenderer&&window._liquidPipeRenderer.updatePipeGeometry)window._liquidPipeRenderer.updatePipeGeometry();if(window._liquidMode&&window._liquidMode.renderValves)window._liquidMode.renderValves();if(window.requestRender)window.requestRender();var n=0;scene.traverse(function(o){if(o.isMesh&&o.visible)n++});return JSON.stringify({sv:window.state.schemaVersion,c:window.state.construction.length,m:n,err:_e});}catch(e){return 'ERR:'+(e&&e.message||e);}finally{window.alert=_oa;console.error=_oc;}})()"
  res=$(timeout 90 browser-use eval "$js" 2>/dev/null | grep -oE 'result: .*' | sed 's/^result: //')
  rm -f "$WEB/$nm"
  [ -z "$res" ] && res="ERR:no-result(timeout/crash)"
  ok=1
  echo "$res" | grep -q '"sv":3,' || ok=0
  echo "$res" | grep -qE '"c":0|"m":0|ERR:|"err":"' && ok=0
  if [ $ok -eq 1 ]; then OK=$((OK+1)); st=PASS; else FAIL=$((FAIL+1)); st=FAIL; fi
  echo "[$i/$TOTAL] $st $f :: $res" >> "$LOG"
  [ "$st" = FAIL ] && echo "[$i/$TOTAL] FAIL $f :: $res"
  if [ $((i % RELOAD_EVERY)) -eq 0 ]; then log "[$(date +%T)] ...$i/$TOTAL (pass=$OK fail=$FAIL) — reloading to free memory"; relogin; fi
done
rm -f "$WEB"/v3v_*.cfdproj 2>/dev/null
log "[$(date +%T)] SUMMARY: $OK/$TOTAL pass, $FAIL fail  RESULT: $([ $FAIL -eq 0 ] && echo PASS || echo FAIL)"
[ $FAIL -eq 0 ]
