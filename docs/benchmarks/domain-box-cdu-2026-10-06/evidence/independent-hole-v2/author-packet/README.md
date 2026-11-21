# BOX/CDU hole orientation closure v2

The numerical product now independently normalizes every hole ring, matching the installed Three ShapeGeometry implementation even when the supplied outer ring is already clockwise. The original implementation reversed holes only when reversing the outer ring. The new installed-carrier regression first produced **1 failure / 1 pass**, then both winding cases passed with exact F32 position/normal/UV and index bytes.

The sole production change moves the existing hole-reversal loop outside the outer-ring condition. No arithmetic, triangulation library, layout transform or index sorting is changed. This closes a general supplied-polygon-context compatibility edge. No current canonical polygon-clipping normal caller failure was demonstrated.

Validation: **82 tests in 7 focused files passed**, including the original 23 BOX/CDU tests, kernel and scene snapshots. The final two-case rerun passed after a test-only overload typing correction; these two tests overlap the 82 and are not an additional count. Final tsc --noEmit exited 0. The initial authored-test type error and its exact source/log remain in this package.

The original 56-payload implementation manifest and the original 31-payload structural baseline were rechecked byte for byte. Original fixture and capture producer are unchanged, and no golden was recaptured. The other 13 original current source/test/fixture paths remain exact. See original-final-unmodified-verification.json and source-final.json.

This remains structural BOX/CDU geometry and raw UV ownership. The existing PBR/image material and UV carrier is unchanged. No frontend production build, browser, native/solver run, service operation or Git/index operation was performed here.

manifest.json enumerates declared evidence only; owned tmp/ and npm cache are excluded. source-final/ contains the two changed paths. All 15 current implementation paths are SHA-bound in source-final.json. The installed ShapeGeometry/ShapeUtils sources used by the new oracle are copied and SHA-bound separately.
