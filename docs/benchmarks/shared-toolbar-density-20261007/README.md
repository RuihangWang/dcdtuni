# Shared React toolbar density, 2026-10-07

Source change: `744609f40c6536c7d55a634c78b8a47a1fcbcb8b`; production TypeScript/Vite build passed.

Actual Windows Codex in-app browser at port 8396 loaded `Shinaide_liquid_ring_rear_transient20_GUI.cfdproj` through the normal server file chooser, entered liquid mode, and expanded View tools. The three tool tabs measured equal 193.35-pixel widths. The toolbar body measured clientHeight=scrollHeight=123; grid spacing, both flow values/bars and imbalance status were visible together. Cube and axis remained in the viewport. The ordinary Screenshot button produced the included PNG.

Ordinary Three.js to OV switching retained this shared toolbar. Native video reached 1192x796, readyState 4, paused false and about 61 presented frames/s. This is an observed live display, not a complete OV acceptance: native framebuffer remained 1191x796, background-grid pixel parity was unresolved, a bottom black region and material differences were still visible, and Shift arbitrary-face selection remained pending. No CFD or physical correctness gate is claimed by this layout evidence.
