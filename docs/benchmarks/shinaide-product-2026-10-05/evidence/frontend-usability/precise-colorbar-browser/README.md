# Precise colorbar and directory interaction — 2026-10-05

Real main React at 1280×720 was inspected through ordinary browser controls after main 091ecff3. The numerical display uses field-relative precision and the high-precision dialog retains its original full value. Focusing, tabbing away and applying without an edit keeps the velocity colorbar at 0.00578 m/s; the earlier main browser reproduced a change to 0.0100 before the repair. User edits still commit. This addresses display-rounding mutation, not numerical solver accuracy.

The rendered colorbar labels share a right edge and clear the side panel. Both dialog input rows align. Monitor overlays sit above the colorbar. The actual result selector and timeline playhead both show 0.02 s. Document and viewport width match, with no horizontal spill at this tested size. Directory-only browsing excludes files and ordinary double-click opens the intended owned case folder.

The 62 related tests, 20 directory/postprocess tests, 30 format/colorbar tests and real main production-build logs are preserved separately. Historical console errors remain in browser logs; none was added during these checks. This witness precedes the adjacent temperature-unit subscription and busy-Enter repairs, which require their own follow-up validation.
