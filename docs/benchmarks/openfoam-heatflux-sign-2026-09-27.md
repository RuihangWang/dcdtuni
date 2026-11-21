# OpenFOAM 热通量正负号小盒验证（2026-09-27）

在 40901 的独立 scratch 中，用 OpenFOAM v2412 `buoyantBoussinesqSimpleFoam` 验证 `externalWallHeatFluxTemperature` 的 `mode flux` 符号。算例为 1 × 10 × 1 单元的小盒，高 0.5 m；重力设为零、流动为层流，顶板固定 300 K，侧壁为零温度梯度。底板面积从 `polyMesh` 的点和面独立计算为 1.0 m²，底板使用 `kappaMethod function`、`kappaValue=0.017 W/(m·K)`。两次运行仅将底板 `q` 分别设为 +0.2 和 −0.2 W/m²。

- `q=+0.2 W/m²`：底板 305.882 K，顶板 300 K；底板输入为 +0.2 W，按顶层温度梯度估算的顶板排热为 +0.19992 W。
- `q=−0.2 W/m²`：底板 294.118 K，顶板 300 K；底板输入为 −0.2 W，顶板对应 −0.19992 W。

两例从已有场继续至第 3102 步时均报告 `SIMPLE solution converged`；末步 T 初始残差分别为 3.21056×10⁻¹⁴ 与 2.83624×10⁻¹⁴。底板温度与一维解析值 305.88235 K / 294.11765 K 一致到输出精度。因而此版本中**正 `q` 向空气加热，负 `q` 从空气移热**。安装的 OpenFOAM 实现也在 `externalWallHeatFluxTemperatureFvPatchScalarField.C` 的 `fixedHeatFlux` 分支将 `refGrad` 设为 `(heatFlux + qr) / kappa`。

[机器可读报告](openfoam-heatflux-sign-2026-09-27.json)保存边界值、网格面积、输出温度、积分估算及输入/输出 SHA-256。40901 上的完整 scratch 位于 `/mnt/lv/home/litianyu/benchmarks/dcdtuni_heatflux_sign_20260927/`；正负算例的最终日志分别为 `positive_strict/solver_full_relax.log` 和 `negative_strict/solver_full_relax.log`，最终温度场分别为各自的 `3102/T`。

这只验证小盒边界条件的符号和传热方向，**不能外推为 Shinaide 网格质量、收敛、风液余热分配或预测结果已经通过**。实验没有使用 GPU，也没有修改仓库后端或 Shinaide 原工程。
