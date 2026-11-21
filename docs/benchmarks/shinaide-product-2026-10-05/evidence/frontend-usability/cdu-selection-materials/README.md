# CDU visible selection and service cabinet detail

This change gives the visible CDU service meshes the existing model's pick/highlight owner. It keeps them out of the structural/STL registry. Shift remains the existing additive whole-model gesture; the geometry-only / frozen viewport mode guards remain in force. Rack decoration remains click-through, and visible seed handles remain selectable.

The display cabinet has shallow rolled edges, a door gasket, hinges, handle, recessed louvers and HMI. Its per-part standard PBR materials preserve colour/normal/roughness maps and finite UVs in the OV snapshot. The rear collars terminate inward at the unchanged logical port plane. Cabinet/feet and service details stay in the vertical model envelope, including a small CDU fixture that previously exceeded its height. Materials and service-front furniture are render detail, not new CFD geometry.

## Verification and provenance

- Initial interaction regression: 6 failures / 8 tests. baseline-vitest-tool-excerpt.txt is a verbatim excerpt captured from the exec tool response at 04:15:44 UTC. No stdout redirect file was created for that invocation. It demonstrates missing visible owner/raycast/highlight and the small-height overshoot.
- The final interaction suite has 9 tests, including an added world-transform raycast. The earlier intermediate selection/PBR/OV/export regressions were 91/91; the final recorded run expands this to 111/111 across 13 files, including temperature colour / material restore.
- related-vitest-output.txt and production-build-output.txt are full Python-subprocess stdout. validation.json records arguments, CPU24–31, environment, clocks and actual return codes. Both return 0; tsc and production Vite build pass.
- source-test-identity.json binds exactly the 4 owned source/test files. initial-proof-context.json binds HEAD baseline source hashes, the actual Shinaide test project and unchanged physical geometry/pipe/mode modules.
- The Shinaide scene test confirms model ownership, finite geometry/UV and standard textured OV snapshot serialization; it does not prove a live OV stream.

## Outstanding user-facing verification

Root owns real-browser Shift selection, highlighting, camera/view and appearance acceptance after deployment. PDU has not been identified in the current project and is not assumed to mean UPS_Rack.

The cyan/orange floor loops in the before screenshot are separate external pipe models from shared liquid_pipe_renderer, not meshes introduced by this CDU detail module. The pipe renderer's existing logical endpoint binding and authored external route are unchanged. This receipt therefore does not claim the user's protruding-pipe complaint is resolved; the visible loop must be assessed in the actual view before any route change. No project/network/endpoint/CFD input was edited.
