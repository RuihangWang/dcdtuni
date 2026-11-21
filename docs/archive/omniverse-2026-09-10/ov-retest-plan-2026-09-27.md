# Prepared OV retest after the CPU-resource clarification

Status: CPU preparation complete; no new GPU run is authorized or started.
The preceding one-time GPU episode ended after numerical CUDA acceptance
passed and the live renderer twice timed out. CPU is no longer restricted to
one core; the user's requirement to avoid exhausting CPU capacity remains.

## Concrete candidate

- Same physical GPU0 UUID `GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93` only; check
  its availability immediately before a newly authorized run. Do not repeat
  the already-passed CUDA numerical phase.
- Use existing official Kit and the task bridge, reusing the private runtime
  cache obtained during the first episode. No main-checkout changes.
- `tools/kit_acceptance_harness.py --cpus 0,1,2,3 --numeric-threads 2 --stream-debug`
  provides four-core affinity, four tasking workers, TBB/OMP/MKL/OpenBLAS two
  threads and nice 10. Thirty-two CPU tests validate the plan and actual
  child-process affinity inheritance. No automatic whole-machine allocation.
- Pin one existing NVIDIA ICD in process-local Vulkan variables; do not alter
  system driver files. This follows the official
  [Vulkan loader override mechanism](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderDriverInterface.md#overriding-the-default-driver-discovery).
- Proposed new episode: five-minute ceiling including owned-process cleanup,
  private display/logs/textures and multi-GPU rendering disabled. This ceiling
  cannot be activated merely by preparing this document.
- Check real renderer initialization as well as signaling readiness; the prior
  app-ready/TCP-listener events preceded Optix initialization. Capture server
  StreamSDK info and client timestamped update/start/stop/stats diagnostics.
- Actual browser uses the saved Shinaide DAE project/run, with t=0/600 and
  temperature overlays. Require decoded pixels plus scene/view ACK, then test
  same-time overlay changes and OV → Three camera/time/UID preservation.
- Bounded official multipart-ACK reconstruction and material-only render
  revision fixes have CPU evidence; they are not presented as live passes.

If the live stream still fails, preserve precise diagnostics and clean up
inside the ceiling. Do not infer success from a port, a process, TCP signaling
or CPU USD authoring. Further GPU use would require another explicit approval.
