# OV grid bounded pending observer — 2026-10-07

Default-off diagnostics expose original pending branches and existing first/final native observation cohorts, bounded to eight rows and 4096 bytes per row. Exact opt-in: DC_CFD_NATIVE_GRID_PENDING_DIAGNOSTICS=grid-pending-v1. This does not add viewport reads, move a camera, change tolerances, grant a layout lease or certify pixels.

Root reviewed 48 frozen payloads and independently passed 23 CPU controls: original getter/callback order with diagnostics on/off, pending/final guards, capacity limits, throwing and oversized values. frozen-pending and root-controls preserve actual inputs/results.

actual-cohort preserves preceding actual Kit694688 observations. First two rows have startup default lens; next six have requested authored projection. Persistent default-camera mismatch is therefore withdrawn. These rows do not bind closing observations or encoded-frame crop/layout. Visible geometry/lines and live video are not pixel-lease acceptance.

Normal source contains cohort and pending observers. At this checkpoint diagnostic runtime still has preceding cohort code; controlled restart and new Windows browser observations remain. Main service is held.
