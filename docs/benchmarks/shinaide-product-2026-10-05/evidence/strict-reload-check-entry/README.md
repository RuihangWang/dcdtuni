# Strict reload readiness and normal MeshPanel check entry

A rejected or merely existing polyMesh previously enabled the solver after page reload. Readiness now requires the existing strict quality receipt to match the current mesh and exactly one current cfdrun input. Physical presence is separate, so MeshPanel provides the normal check-only entry while the solver remains locked.

A completed mesh/check job grants readiness only for explicit strict success. Readiness generations prevent an older disk scan from overriding a new success, while fresh scans can revoke stale acceptance. An explicitly missing physical mesh invalidates cached acceptance. Check-only polling errors are visible, and the preparation/probe period disables duplicate launches.

Actual installed-source validation: 84 focused React tests, 31 backend tests, TypeScript typecheck and production build passed. Unit fixtures cover accepted strict receipts and successful immediate readiness; these synthetic fixtures are not business mesh acceptance. Existing jsdom canvas messages are retained in stderr.

Actual browser negative validation on the normal 8396 React service: load the 32-model Shinaide project, click the new MeshPanel check button, full strict check job 7efdbb5f ends rc=1/Failed 2/checkMeshOk=false, both mesh/check controls unlock, solver remains disabled. Reload after that real rejected receipt and reload the project: check stays available, solver remains disabled, quality hint appears; baseSize0.25/ranks8/rack4. Captured browser warn/error entries were empty for that final reload observation. The surrounding full UI was captured as a CUA tool image, not a saved filesystem image; viewport-only images are not used as button proof.

Only the verified owned 8396 process was stopped via fuser -k -TERM; both 8396/8397 were empty before normal start_server.sh restart. Main8390/8391 and OV were not touched. This repair does not certify the large Shinaide mesh: current native V6 still D867/W98/Failed2, and no large-case solve or transient acceptance is claimed.

Final hashes and independent review closure bind the exact tested implementation. Large full-case archives are outside this small receipt; raw checker SSE and the real rejected receipt are included here.
