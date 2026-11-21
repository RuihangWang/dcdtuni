# Tri_angle 风侧热量账本差 2.3 %：原因与修法（2026-10-25）

**原因：** 服务器风扇入口的温度边界原为 `inletOutlet`。风扇入口的速度边界 `flowRateOutletVelocity` 去掉了一切回流，所以没有出流的面上 φ 只是舍入意义上的 ±0；`inletOutlet` 把 −0 当作回流，把这些面钉在初始温度 295.15 K。它们旁边的空气更冷，经切割单元的小间距向房间导热，约 118 W，就是 ACU 移热比服务器加热多出的那部分。

**修法：** 风扇入口的温度改用 `zeroGradient`，与 ACU 回风口相同（提交 `a283c0354`，测试 `src/backend/tests/test_fan_inlet_t_bc.py`）。

**修复后的整算例复算：** 经脚本化 API 从网格到求解重跑 Tri_angle（提交 `d3c764bfd`，4 个 rank，严格 checkMesh 通过），热量账本闭合：

| | 修复前（`1e7fcc0c0`） | 修复后（`d3c764bfd`） |
|---|---:|---:|
| ACU 移热 | 6,393 W | 6,243 W |
| 服务器加给空气 | 6,250 W | 6,250 W |
| ACU 对申报功率的相对差 | +2.29 % | −0.11 % |
| 温度方程末步初始残差 | 约 3e-4（停滞） | 1.8e-6 |
| 收敛判据 | CONVERGED（1025 步） | CONVERGED（1251 步） |
| 回风传感器温度 | 15.83 °C | 15.78 °C |

温度残差不再停滞，说明此前的平台正是这些被钉住的面造成的。剩下 −0.11 %（约 7 W）与开口处的扩散热同一量级。运行记录在本机 `case/Tri_angle/log/api/2026-10-26_112747_SGT__air/result.json`（`case/*/log/` 不入库）。

## 排查证据（`diag/`）

| 检查 | 结果 | 文件 |
|---|---|---|
| 从第 3000 步续算，逐步记录各开口的焓通量 | 差值稳定在约 137 W，不围绕零摆动 | `balance_relaxedT.txt` |
| 同上，但每步把温度方程解到 1e-12 | 仍约 136 W：不是线性求解残差 | `balance_exactT.txt` |
| 温度方程所用通量的散度 | 约 1e-6 m³/s，bounded 项贡献 0 W：不是连续性误差 | `identity.py` |
| 服务器出口改为固定温度 | 仍 108 W：不是出口表达式边界 | — |
| 用求解器自身离散算 ∫div(φT) 与 ∫∇·(α∇T) | 108.5 W 对 108.7 W，方程自洽；扩散热按 patch 分解，三个服务器入口共 +118 W，其中 3、8、47 个面 valueFraction = 1 | `tresid.C`、`tresid_output.txt` |

`tresid.C` 是一个用 wmake 编译的小工具（`files`、`options` 为其 Make 文件）：读入某一步的 T、alphat 与上一步的 phi，组装与求解器相同的温度方程并按 patch 输出扩散热。续算均在本机的临时副本上用 `mpirun -np 4 … -parallel` 运行，不改动算例。
