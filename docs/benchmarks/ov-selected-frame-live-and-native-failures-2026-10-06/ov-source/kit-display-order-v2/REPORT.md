# Actual Kit display-writer ordering closure

The original Kit race packet is preserved unchanged. That packet covers dataset/scene replacement. The panel's awaited fire promise means transport send completed; it does not mean the Kit coroutine completed.

The first new test run had missing fake geom and extra strict-command fields; that invalid draft and log remain. The corrected original-source run has 9 actual failures: six blocked writers allowed a new frame import before settling, and three queued fire commands changed or wrote across a newer scene.

The extension now serializes volume, slice, slice readback, streamline, seed-sphere and viewport-sync writers using the same commit lock as dataset and scene installation. A generation check occurs before a queued writer can even change scene parameters. Legacy handlers accept an optional scheduler; absent it they use the original ensure_future. Under the bridge, the scheduler captures the one original coroutine and awaits it within the lock, with no duplicate background ensure_future and no new fire ACK protocol. Handler numerical operations and default behavior remain unchanged.

Final controlled tests: 34 PASS. Six writer/import ordering cases, three queued-old-fire rejection cases, receipt-order/no-yield FIFO case and default legacy scheduler control are included alongside the 23 earlier dataset/scene/routing/grant cases. Actual router/handler and scene loader methods execute from source AST with CPU fake Kit APIs and blocked coroutine writers. This is not real Kit/GPU acceptance.

The barrier lets an already running old writer settle before the new scene/frame commits. It does not preempt a converter, impose new time bounds or cover unrelated legacy demo commands. Errors on legacy fire remain visible in the Kit log; existing ACK commands retain their existing ACK protocol.
