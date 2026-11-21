# Independent review of the SeedInspector event repair

The eight original scratch proof files, including manifest.json, are copied byte for byte without changing their original SHA or log content. This review is additional documentation; it does not replace that immutable manifest.

The existing component test reproduced 2 failures / 5 passes at the unchanged 89f8b48b source. SeedInspector, NumberField, seedActions, seedConfig and the actual Base_leakage input all have identical bytes to that baseline. Only the test is repaired: native input prototype setter bypasses React's value tracker, and separately flushed focus/input/real blur exercises the actual onChange→commit path. The original .2 offset precision and custom-badge assertions remain; reset additionally requires the .2 override to exist before deletion.

After the repair the existing file passes 7 tests. No production component, project physics, schema, API or native source changed. The test uses CPU36–39, two workers, CUDA empty, and repository-owned temporary/cache locations. This is component evidence, not browser or native acceptance.
