# S3a GPU episode 回执 — 物理 CUDA 验收 + A2 微基准(2026-09-28)

单卡 episode(排期授权内):GPU3 = RTX 4090,UUID `GPU-9cc41e7b-4c4a-d642-8992-842550a6c4f3`,
`CUDA_VISIBLE_DEVICES` 以 UUID 钉死(pinned check: `device_count()==1`)。避开 GPU1/2(用户 FVGN
训练,pid 2571474/2571496 在跑)与 GPU7(OV Kit 模板卡);GPU0 当时亦有用户进程,未用。
窗口 **04:05:17Z–04:06:17Z(60 s,远低于 ~8 min 包络)**;结束 `nvidia-smi` 确认 GPU3 无 compute app(已释放)。

## 物理 CUDA 验收(8 passed / 35.00 s,`DCDTUNI_RUN_CUDA_TESTS=1`)
- `test_dae_compiled_cuda.py`(收养自长驻代理 03:02 遗留 harness,提交 b71a6fc5):
  `_physical_graph(cuda:0)` 上 eager vs `compiled_problem` 的稳态 + 一步 BE + 隐式伴随梯度
  **atol 1e-8 parity**,路由计数断言(n_compiled>0、0 fallback、无 broken 闩锁)全过。
  这是用户验收项「GPU + torch.compile 求解通过 + autograd 反传梯度」的物理 GPU 证据。
- `test_equation_graph_cuda.py` + `test_dae_devices.py`(既有 opt-in 套件)同窗通过。
- 环境:conda dcdtuni,torch 2.5.1+cu121,triton GPU 编译无 LIBRARY_PATH 问题(本机 cu121 wheel 自带 stubs)。

## A2 微基准(microbench_a2_results.json,可复算脚本同目录)
- matmul 1024³(线性求解尺度):fp64 **1125 GFLOPS** vs fp32(TF32 关闭)**46934 GFLOPS** →
  **比值 41.7×**——A2 假设(“1/64”)修正为实测 41.7×。
- vecops n=1604/65536:fp64 45.3μs / fp32 98.4μs——**发射开销主导区,数字反直觉,不构成
  fp64/fp32 吞吐证据**(两档 n 耗时几乎相同即为发射主导的自证);如实记录。
- 推论(记录,非宣称):1604 态级别的小算子在 GPU 上无延迟优势(~45μs/发射 vs CPU warm
  compiled 67μs/整残差)——支持 A3“GPU 慢的根因是主机同步与小核发射,不是 fp64 吞吐”;
  GPU 价值在大图(>4000 态稠密上限之外)与批量多设计评估,论证留给 S4。

## 判读边界
mock/夹具级验收,非现场校准;NV1604 的 GPU parity 属 S3b(需 `load_dae_input` device 透传
小改造,RED 先行);本 episode 未跑 NV 大图、未测 pgmres GPU 路径(S4 组合门)。
