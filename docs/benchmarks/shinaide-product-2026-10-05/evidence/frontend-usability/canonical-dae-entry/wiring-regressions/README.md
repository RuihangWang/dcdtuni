# Display wiring regression upkeep

The full liquid-entry suite exposed two existing stale source-wiring assertions at `89f8b48b`. Their production rendering files were unchanged by the liquid work. The highlight assertion now preserves the live fade argument and actual repaint call while also recognizing the existing cabinet-role resolver. The volume assertion enforces the existing selected-field identity guard rather than requiring stale payloads to be passed. Only tests changed.

The first focused attempt had a worker-count startup configuration error and ran zero tests. It is retained as `focused-startup-invalid.log`. The retry specifies both minimum and maximum workers; see its raw log and execution receipt for acceptance.
