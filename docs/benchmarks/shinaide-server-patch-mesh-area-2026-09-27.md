# Shinaide 服务器热边界实际网格面积（2026-09-27）

对已归档的 Shinaide 风冷网格，只在 40901 私有 scratch 中将 `polyMesh` 副本转为 ASCII，并从点/面计算 112 个液冷服务器热 patch 的面积；原网格、STL、`.cfdproj` 和后端未修改。[逐 patch 结果](shinaide-server-patch-mesh-area-2026-09-27.json)同时用 OpenFOAM v2412 的 `surfaceFieldValue` 对 112/112 个 patch 作了原生 `mesh.magSf()` 面积交叉核对，解析值与原生输出的最大相对差为 2.83×10⁻⁶。

112 个 patch 均存在且面积为正，共 5030 个网格面。原 STL 总面积 8.971197864 m²，网格总面积 8.921295937 m²（−0.556%）。其中 front 56 个的总偏差为 −0.00746%，rear active 56 个为 −2.751%；最大单 patch 偏差为 −17.94%，43 个 patch 的绝对偏差超过 5%。所以从 STL 面积算得的 `q=W/A_STL` 不能保证网格上逐服务器施加的热量等于目标 W。

在运行链中，应于**网格质量验收通过后、风冷求解前**按严格 `.cfdrun` 的 UID→patch 映射生成独立控制字典，逐 patch 测量网格面积。例如：

```foam
functions
{
    areaOutput_001
    {
        type            surfaceFieldValue;
        libs            (fieldFunctionObjects);
        regionType      patch;
        name            <exactPatchName>;
        operation       sum;
        fields          ();
        writeArea       true;
        writeFields     false;
        writeControl    timeStep;
        writeInterval   1;
    }
}
```

字典还需保留普通 `controlDict` 基础键。在隔离副本上，`postProcess -case <mesh-case> -constant -noZero -dict <area-controlDict>` 对 112 个函数一次执行成功；每项生成 `postProcessing/areaOutput_001/0/surfaceFieldValue.dat`，含 patch 名、面数和 `Area` 数据列。读取时要求命令退出码为零、目标文件恰好 112 份、patch 名与 UID 映射一致、面数与 `polyMesh/boundary` 一致、面积有限且大于零，并核对文件头与数据行的面积相同。随后对选定的余热沉积面设 `q_i=P_i/A_mesh,i`，验证每台及全局 `Σ(q_i A_mesh,i)=ΣP_i`；任一缺失或预算不闭合都应拒绝求解。

完整转换脚本、原生面积日志及输出位于 40901 `/mnt/lv/home/litianyu/benchmarks/dcdtuni_shinaide_patch_area_20260927/`。这份现存 Shinaide 网格已有三项质量检查失败，因此面积核查**不表示该网格或后续风冷物理解通过验收**；上述运行链接入应保持质量失败的阻断条件。
