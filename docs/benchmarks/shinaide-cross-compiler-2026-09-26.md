# Shinaide 混合工程的双编译契约验证

同一原始 `case/Shinaide_Liquid/Shinaide_liquid.cfdproj` 经正常加载、显式采用
DAE mock 参数并保存重开后，仍能生成内容一致的 OpenFOAM 风冷输入。
本次只验证输入与几何编译，没有运行网格、CFD、DAE 或 GPU 渲染。

新增单个集成测试：
[`shinaideHybridCompiler.test.ts`](../../src/frontend/react/test/shinaideHybridCompiler.test.ts)。
测试使用实际 `normalizeStateV4`、项目 store 加载时的机柜规范化、
`adoptMockDAEExperiment`、工程序列化与 `compileSavedExperiment`。
风冷侧使用既有 CPU 场景几何构造器、grouped STL 导出和
`buildManifestV2`，没有另造图或替换生产导出器。

采用 DAE 前后比较结果：

- 12 份 grouped STL 的文本 SHA-256 全部一致。
- `.cfdrun` 的完整几何、UID、物理输入、边界绑定、源项、孔隙绑定、网格设置、
  OpenFOAM求解设置及过渡 `meshGeometry` 内容逐字段一致。
- 唯一排除的字段是 `sourceProject.contentHash`：工程新增 DAE 设置后，该值
  正确改变；项目 UID 始终为 `proj_Shinaide`。
- 风冷导出仍含 311 个 solid、306 个边界绑定、10 个 porous binding、
  4 个 ACU 送风和4个回风边界，以及14个机柜。
- 同一保存工程导出的 `.liquidrun` 为严格 v3，含187个节点、242条边。
- 构件定义及原风冷 `meshConfig`、`simulationConfig` 未改变。

原文件始终只读，前后 SHA-256 均为：
`cff178754280c22f4aacae181224c6e2292a18819841e41479fbb39aa04de662`。

本地使用单个 Vitest worker、nice 10。最终结果为 **1 passed**，测试体
**192 ms**，整次命令 **1.24 s**；TypeScript 检查通过。复现：

```sh
cd src/frontend/react
HYBRID_COMPILER_OUTPUT_DIR=/tmp/dcdtuni-hybrid-compiler nice -n 10 \
  npm test -- --maxWorkers=1 --minWorkers=1 test/shinaideHybridCompiler.test.ts
```

测试输出 `before.cfdrun`、`after.cfdrun`、`after.liquidrun` 和包含每份 STL
哈希的 `frontend-report.json` 到上述临时目录。两份 `.cfdrun` 与液冷输入
随后传至40901，使用该工作树的 `dcdtuni` Python、单核 affinity、nice 10、
BLAS线程数1、CUDA禁用进行模型校验：

- `CfdrunV2.model_validate`：两份风冷输入均接受。该模型及嵌套模型拒绝未知字段。
- `CFDExport.model_validate(raw['meshGeometry'])`：两份过渡网格几何均接受。
- `LiquidrunDAEV3.model_validate`：液冷输入接受，项目UID一致。

该校验过程耗时 **16.212 s**，包括模块导入；没有调用任何求解器。
文件哈希为：

```text
before.cfdrun  69f878d7393448c45d344afc8dbef0f5140dbec5f18039a478e62fa54c1b5a54
after.cfdrun   c4cd5f6cdd5dde1e8131477206a90431018afb322ce791c9ffe5308e1b0465e3
after.liquidrun 6f7fa9aa9eba78de5658e2819841afc7d3a96fffffc1656856bc485245e77c82
```

本次未发现生产编译错误。测试最初把加载前的机柜默认值与加载后比较，触发了
既有 store 的合法迁移差异；已将两侧基线统一到真实编辑器加载完成之后。
随后保存与采用 DAE 的比较完全通过。未为通过测试修改生产代码。

边界：本证据证明“同一工程保存 DAE 定义不会破坏现有风冷导出”，不证明
OpenFOAM网格/求解已经执行，也不证明风液热耦合、现场参数或动态预测精度。
