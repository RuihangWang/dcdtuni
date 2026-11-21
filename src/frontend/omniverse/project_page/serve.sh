#!/usr/bin/env bash
# Serve the SCANO project page as plain static files.
#
# Deliberately NOT part of the Vite demo app: the project page must stay readable
# when the Omniverse backend is down (a reviewer opening the paper link should not
# need a GPU), and it links OUT to the demo rather than embedding it.
#
# The demo link inside index.html is built from location.hostname at load time, so
# opening this page from a laptop on the intranet points its demo button at the same
# host — no rebuild, nothing hardcoded.
#
# Serves public/ ONLY, never this directory. `python3 -m http.server` hands out every
# file under its working directory, so serving the repo folder also served README.md,
# serve.sh and verify_page.py — all of which carry absolute paths like
# /mnt/lv/home/<user>/... The paper is under double-blind review, and a host username in
# a fetchable file is a de-anonymisation vector. Everything public lives in public/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${DC_PAGE_PORT:-8090}"
ROOT="$HERE/public"

[ -f "$ROOT/index.html" ] || { echo "[project_page] missing $ROOT/index.html"; exit 1; }
cd "$ROOT"
echo "[project_page] serving $ROOT on :$PORT (dev files stay outside the web root)"
echo "[project_page]   local     http://localhost:$PORT/"
for ip in $(hostname -I 2>/dev/null); do
    echo "[project_page]   intranet  http://$ip:$PORT/"
done
echo "[project_page] the 'open demo' button targets <same host>:8081"
exec python3 -m http.server "$PORT" --bind 0.0.0.0
