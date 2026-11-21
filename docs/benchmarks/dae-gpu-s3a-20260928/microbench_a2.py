"""A2 microbench: RTX 4090 fp64 vs fp32 throughput, DAE-relevant shapes.

Settles plan assumption A2 ("4090 fp64 ~ 1/64 fp32" — hardware-spec folklore,
never measured on this box). Measures: dense matmul 1024^3 (linear-solve
scale), elementwise+transcendental vector ops at n=1604 (NV DAE state size)
and n=65536 (large-graph target), all float64 vs float32, one physical GPU
(CUDA_VISIBLE_DEVICES pinned by the caller). Records only; no product claims.
"""
import json, pathlib, time
import torch

assert torch.cuda.is_available(), "CUDA must be visible"
dev = torch.device("cuda")
torch.backends.cuda.matmul.allow_tf32 = False  # measure true fp32, not TF32


def bench(fn, n=100, warm=20):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / n


environ = pathlib.Path("/proc/self/environ").read_bytes()
cvd = None
if b"CUDA_VISIBLE_DEVICES=" in environ:
    cvd = environ.split(b"CUDA_VISIBLE_DEVICES=")[-1].split(b"\x00")[0].decode()

out = {
    "probe": "a2-fp64-fp32-microbench",
    "device_name": torch.cuda.get_device_name(0),
    "cuda_visible_devices": cvd,
    "torch": torch.__version__,
    "tf32_matmul": bool(torch.backends.cuda.matmul.allow_tf32),
}

for dt_name, dt in (("float64", torch.float64), ("float32", torch.float32)):
    a = torch.randn(1024, 1024, device=dev, dtype=dt)
    b = torch.randn(1024, 1024, device=dev, dtype=dt)
    s = bench(lambda: a @ b, n=30, warm=5)
    out[f"matmul1024_{dt_name}"] = {"seconds_per_call": s, "gflops": 2 * 1024**3 / s / 1e9}
    for n in (1604, 65536):
        x = torch.randn(n, device=dev, dtype=dt)
        s2 = bench(lambda: x * 1.0001 + torch.sin(x) - torch.abs(x))
        out[f"vecops_n{n}_{dt_name}"] = {"seconds_per_call": s2}

out["ratio_matmul_fp32_over_fp64_gflops"] = (
    out["matmul1024_float32"]["gflops"] / out["matmul1024_float64"]["gflops"])
out["ratio_vec1604_fp64_over_fp32_seconds"] = (
    out["vecops_n1604_float64"]["seconds_per_call"]
    / out["vecops_n1604_float32"]["seconds_per_call"])
p = pathlib.Path(__file__).resolve().parent / "microbench_a2_results.json"
p.write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
