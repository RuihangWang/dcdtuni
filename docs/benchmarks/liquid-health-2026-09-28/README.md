# 40901 当前主仓库液冷运行复核

2026-09-28（SGT），代码基线 `05a2bfbeb8ac506da655d2c02b7cfd626ad5f19c`。使用主仓库启动器的独立 8384/8385 服务，源码明确来自 `/mnt/lv/home/litianyu/mycode/dcdtuni/src`，CPU affinity 4–7、PyTorch 2 线程、float64、GPU 不可见。8360 原服务、原 `.cfdproj` 和原 fixture 未修改；本次输入副本只改线程数，并在首个步进前排队 `heat_multiplier=1.2`。

[实际 HTTP 回执](http-result.json)记录两份严格 v3 fixture 的 SHA、运行 ID、收敛与重放证据：

- NV mock 和 Shinaide mock 均完成 0–10 s、11 帧瞬态，温压流等八组物理量有限。排队功耗命令保留初始帧，仅下一接受步生效。
- 独立稳态查询不推进或覆盖瞬态；直接稳态初始一次侧回水与瞬态的稳态初始化逐值相同。
- `executed.liquidrun` 经另一真实 HTTP 会话独立重放，八组物理场逐值相同。NV 最大 scaled residual `2.2788e-12`，最大全局能量余额 `1.4959e-7 W`；Shinaide 为 `5.7986e-11` 和 `9.0782e-6 W`。
- 当前源码的 DAE runtime/Newton/物理 MMS/在线执行器/可选分块定向测试共 **45 passed**。它们不代表整个后端回归全绿；GMRES 和 block 研究路径保持 opt-in、产品默认 dense。

运行脚本 [check_runtime.py](check_runtime.py)须针对仓库启动器已启动的**独立**服务使用；开发账号从仓库指南读取，不写入回执。完整六次运行的原始输入、结果、检查点和接受输入回执在主仓库内 `.codex-tmp/liquid-health-20260928/http-case/`，未重复上传大运行文件。JSON 回执中的阶段耗时包括瞬态、重放及稳态查询的 HTTP 序列，不能当作单步求解器速度。Shinaide 一次回水在这 10 s 内几乎未变化，因此本检查不宣称已验证完整负载传递曲线。

8384/8385 临时服务已停止。本次没有浏览器操作、OpenFOAM 求解、GPU 使用或 NV 现场标定；长期开发目标仍暂停。
