#!/usr/bin/env bash
# v4_baseline_batch.sh — capture the remaining pre-refactor baselines sequentially.
#
# Runs one case at a time (no concurrent browser-use / mesh jobs). The three
# air-cooling cases get a full mesh+solve baseline; the Liquid case gets
# mesh-only (the air OpenFOAM pipeline does not run the liquid network solver,
# so an air "solve" of it is not a meaningful reference — load+export+mesh is).
#
# Usage: v4_baseline_batch.sh
# Output lands under docs/superpowers/specs/baselines/2026-06-12-v4-refactor/<Case>/.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="$HERE/v4_baseline.sh"

run_case() {
  local name="$1" proj="$2" mode="${3:-full}"
  echo "########## BASELINE START: $name ($mode) ##########"
  if bash "$RUN" "$name" "$proj" "$mode"; then
    echo "########## BASELINE OK: $name ##########"
  else
    echo "########## BASELINE FAILED: $name (rc=$?) ##########"
  fi
}

run_case Shinaide  case/Shinaide/Shinaide.cfdproj
run_case CMI-China case/CMI-China/CMI_China.cfdproj
run_case STDCT     case/STDCT/STDCT.cfdproj
run_case Liquid    case/Liquid/Liquid.cfdproj --mesh-only
echo "########## BATCH COMPLETE ##########"
