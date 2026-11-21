# OpenFOAM v2412 分区风侧网格的严格质量与 patch 面积试验

本试验只验证分区网格的 OpenFOAM 调用与输出语义，不验证 Shinaide 的风侧网格质量或温度预测。40901 的隔离目录为 `/mnt/lv/home/litianyu/benchmarks/air_parallel_area_probe_20260927/`。输入是已通过严格检查的 500-cell 正控盒子网格；`Mesh_partition_only` 保留 2 个 `processor*` 网格，并将根级 `constant/polyMesh` 移到 `polyMesh.serial_hold`。执行限制为 `taskset -c 0,1`、2 个 MPI rank、`OMP/MKL/OPENBLAS_NUM_THREADS=1`、`CUDA_VISIBLE_DEVICES=''`，无 GPU 或求解器运行。

在激活 `dcdtuni` conda 环境后，实测命令为：

```bash
mpirun -np 2 checkMesh -case CASE -parallel -constant -allTopology -allGeometry
mpirun -np 2 postProcess -case CASE -parallel -time 0 -constant -noZero -dict AREA_DICT
```

正控的严格 `checkMesh` 汇总 500 个 cell，打印 `Mesh OK.`，退出码为 0。`postProcess` 将每个 rank 上 50 个 `floor` 面汇成根级唯一的 `CASE/postProcessing/<function>/0/surfaceFieldValue.dat`，其中 `Faces=100`、`Area=1.0 m²`，与根级串行网格相同。去掉根级网格后沿旧串行命令查询会因找不到 `polyMesh/points` 退出 1。另用 500-cell 同尺寸盒子把 `leftWall` 单独列成 patch：processor0 有 50 面，processor1 为 0 面；并行查询仍产生唯一根级输出，`Faces=50`、`Area=0.5 m²`。

负控仅在隔离副本中把 processor0 一个内点从 `(0.2 0.2 0.2)` 移到 `(0.2 0.2 0.4)`。严格并行 `checkMesh` 打印 `Failed 3 mesh checks.`，但**进程退出码仍为 0**。因此质量门禁必须检查最终文本 verdict，并在任何失败数时拒绝求解。候选 Python API 的真实调用已经拒绝此负控，抛出 `OpenFOAM checkMesh failed 3 mesh checks`。

本候选按仓库既有 `mesh_layout.mesh_directories` 的权威布局运行：根级完整 `polyMesh` 存在时仍选择串行；仅有完整、连续且与 `decomposeParDict` 一致的 `processor*` 网格时，质量检查与面积查询均走 `mpirun -np N ... -parallel`。面积仍只读取根级聚合数据，网格哈希须与所选布局一致。不能单凭 `processor*` 目录存在就切换为并行，否则可能检查一份网格、为另一份网格计算热通量。

原始命令日志及 API 结果保留在隔离目录，主要为 `checkMesh.partition_only.log`、`checkMesh.partition_invalid.log`、`postProcess.partition_only.log`、`integration-v1.json`。试验只覆盖 OpenFOAM v2412 的 `uncollated` 输出和正控盒子；Shinaide 当前严格网格失败仍需独立解决。

候选源码的 SHA-256 为 `07498c6d9803bc9b655d91a59415eee053a07ad0d0a52a565abb5fd22e4e3ce6`，与40901隔离试验加载的文件一致。Mac 上对应四组定向后端测试为 32 passed / 3 subtests passed；完整 `src/backend/tests` 为 1635 passed / 40 skipped / 10 failed / 3 subtests passed。10项失败分别落在旧液冷 ODIL 热环、meshgen 命令/退化导出、polyMesh fixture、room enclosure 假对象与旧温度模板测试，与本候选变动文件无交集；因此不能称全套绿色。
