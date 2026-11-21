# 材质原 producer 捕获测试的类型补记 v2

原始42-payload baseline 保持不变：manifest SHA `23f810f58b4c617f2459f3dd236abe64c3ee14eed01d35f17b1fd3e08ca7984b`，42 payload / 1,981,311 B。本补记仅修正新测试中 TextureLoader stub 的类型断言，保留原始源码、实际捕获输出和 Domain 缺方法 RED。

`test/boxMaterialRecipeCapture.test.ts` 的 `as THREE.TextureLoader['load']` 增加 `unknown` 桥接；类型断言被 TypeScript 擦除，不改变 stub 返回值、回调、图片占位或运行逻辑。它修复 CTX 现行全局 typecheck 观察到的 TS2352 泛型不兼容。原测试源码 SHA `6d4d73037bcffe743a8bde0c2e66d677e424537be398a908afcfd918ebf8c774`，新测试源码 SHA `53ffca4da22f36c077b13a2b2e428899a35080cac47a8d7f10322db10f1c6762`。

本任务没有重跑 capture、Domain RED、tests、typecheck、build、browser、API、服务或任何业务计算。CTX 按 ROOT 已授权范围只重自己的全局 typecheck；其原错误与新结果将另绑定 raw logs。

本补记不改变任何产品源码、已有结构 golden 或 cfdproj case。材质 live implementation 仍须 ROOT 下一切片明确授权。受控图片/canvas、PBR 未加载/晚绑定和缓存历史的基线边界沿用原42报告；本补记不新增真实 GPU 图片外观验收。

## CTX typecheck closure（原始日志绑定）

CTX 原先 `tsc --noEmit` return2，raw 中唯一 TS2352 指向本新测试 TextureLoader stub；841 B log SHA `9c38ca0dc99d285313a227b3084dc94a6cc9d0b46b2bc3e5c8e6c61aa4464c35` 已逐字复制。窄类型修复后 CTX 只复跑相同 typecheck，return0，21.039774797856808 s，空 log SHA `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。命令、CTX CPU36–39 归属以及其8个 CP source before/after exact 记录均按作者原 receipt 保留；本任务自己没有执行 typecheck，CPU28–31 只用于复制/绑定证据。

本补记复核 live 新capture test 与 after copy 完全一致，且 before→after 仅该一处 `unknown` 断言；4个新test/fixture及旧 structural fixture 与补记bindings一致，原42 payload 仍全部 SHA/size exact。后续产品方法实现时不能将缺方法 RED 当已通过，也不能将这次类型修复当材质运行/GPU验收。
