# Shinaide 风液混合 DAE 工作台

已配置工程为 `case/Shinaide_Liquid/Shinaide_dae_cpu.cfdproj`。它由真实浏览器
从原始 Shinaide 工程显式采用 DAE mock 参数并另存，原文件保留不变。
同一工程继续定义三维几何、OpenFOAM 风冷输入及 PyTorch 液冷输入。

## 实际验证的链路

`.cfdproj → React 几何/连接编译 → strict v3 .liquidrun → 非因果 Newton DAE
→ 结果文件 → 时间选择与设备 UID 绑定 → Three.js 温度显示 → 派生场景 → CPU USD`

- 液冷运行 `737a0ecc25e342acb7932356222e566f`：187 节点、242 边、14 机柜、
  56 冷板，600 s、dt=20 s、31 个接受状态，CPU float64 单线程。
- 通过按钮于下一接受步 t=20 s 将液体热负载从21 kW提高到25.2 kW。
  一次回水从25.0239°C升到25.1527°C；600 s仍有约3.662 kW进入储能，
  不能把该时刻称为新稳态。全轨迹最大能量残差约5.71e-6 W。
- 后处理实际读取该运行，时间滑块显示0/600 s，56个冷板出口、芯片温度、
  14个机柜汇总和对应三维对象由稳定ID连接。
- 同工程采用DAE前后，12份STL哈希和完整风冷物理/几何/UID输入一致；
  仅工程内容哈希按预期改变。严格cfdrun和网格几何模型接受导出。
  本轮没有再次运行OpenFOAM网格或求解。

详见[浏览器运行证据](../../benchmarks/shinaide-dae-browser-2026-09-26.json)与
[双编译验证](../../benchmarks/shinaide-cross-compiler-2026-09-26.md)。

## 场景和贴图

修复了40901工作树上的实际贴图缺失：原先部分JPEG路径提供的是131字节的
Git LFS指针，浏览器无法解码。58份仓库贴图已恢复为真实内容并同步，
原有Git文件内容没有被替换成新的设计。浏览器重开后，当前着色场景包含
25个纹理标识；文件按内容去重后为20份PNG。

**导出渲染场景**保留下载链接，并把已验证的派生文件保存到当前
`case/Render/scenes/`。保存请求包含工程case路径，服务器拒绝错配case、
未知字段、无效几何/纹理/视角及输出路径逃逸；旧场景不会被覆盖。
导出确认仅显示简短状态，完整保存位置和转换说明在“导出详情”中。

0/600 s两个实际场景均含2143个几何片段和24个可视模型UID。几何、变换、
UID、UV与源贴图数据逐字段相同；29个材质片段随温度场变化。
两帧均已用受限CPU写成并重新打开USD，每帧928个相对纹理引用全部可解析。
USD点坐标和变换与导出的Three.js数据吻合，工程UID、物理时间与颜色保留。

可打开的CPU转换结果：

- [初始USD](../../../case/Shinaide_Liquid/Render/verified-20260926/Shinaide_dae_t0s.usda)
- [600 s USD](../../../case/Shinaide_Liquid/Render/verified-20260926/Shinaide_dae_t600s.usda)
- [完整场景核查数据](../../benchmarks/shinaide-dae-scene-export-2026-09-26.json)
- [贴图恢复清单](../../benchmarks/texture-assets-2026-09-26.json)

本证据验证数据转换与资源可读性。实际Omniverse RTX/WebRTC画面和CUDA数值
运行仍未执行；本轮遵守CPU限制。泵、HX与一次侧参数是可替换的mock，
不属于PEK02现场校准或CDU70厂商性能曲线。
