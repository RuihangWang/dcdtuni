# E8a 回执(2026-10-01)——cfdrun v4:只有一份 simulationConfig

**结论:过门。** 用户裁决「simulationConfig 只保留一份」。风冷导出从此是 **cfdrun v4**:唯一的 simulationConfig 在根上,
`meshGeometry` 里不再有派生副本;原来只有副本才有的两项(房间温度、监测配置)移到根上(`roomTemperatureC`、`monitoring`)。
runner 对 v4 用 `derived_config.execution_blob` 从根推导它执行的配置(`derive_sim_config`,即 golden 测试逐字节对齐副本的那个解析器),
下游所有读取点不变;v2/v3 旧文件照旧走副本。机读回执 `receipt.json`。

环境:40901,独立 worktree `~/scratch/wt-e8` 临时起 8390;8360 已按用户要求停掉。

## 验证

| 项 | 结果 |
|---|---|
| 新增后端单测(`test_cfdrun_v4_single_config.py`,22 项) | 全过。16 个 golden 全部改写成 v4(副本搬到根)后执行的配置与原副本**逐字段相同**(无跳过);Base_leakage 完整清单经 runner 真实加载路径,v4 与 v2 得到相同的 simulationConfig 与 meshConfig;v4 带副本、缺 `roomTemperatureC` 在两条加载路径都被拒;v4 瞬态块可选、有时间表必须有瞬态块;v5 要求重新导出 |
| 后端全量(`014e9c4e`) | 1875 通过 / 67 跳过 / 4 失败;4 项与 main 相同(2 项 dcCart、2 项 MMS 夹具不在 git) |
| vitest | 317 文件 / 3166 测试通过,1 文件按设计跳过 |
| JS 模式节点测试 | `test_cfdrun_schema.mjs` 5/5,`test_cfdrun_export.mjs` 58/58 |
| tsc | 干净 |

## 浏览器门(Slab 稳态,全部界面操作)

求解页把迭代步数改 40、写入间隔 40、求解核数 4、p_rgh 残差 0.002;几何与属性页把房间温度改 26 °C。浏览器导出的清单:
schemaVersion 4、无瞬态块、`meshGeometry` 里没有 simulationConfig、根上 `roomTemperatureC` 26、监测配置为对象,JS 模式校验 0 错。
「检测网格状态」→「执行求解」,40 步跑完。运行目录逐项对上界面设置:

| 界面 | 运行目录 |
|---|---|
| 迭代 40、写入间隔 40 | controlDict `endTime 40`、`writeInterval 40`,日志 `Time = 40`、`End` |
| p_rgh 0.002 | fvSolution SIMPLE residualControl `p_rgh 0.002`(其余 0.001) |
| 房间温度 26 °C | `0/T` `internalField uniform 299.15`,`TRef 299.15` |
| 求解核数 4 | runner 收到「requested 4」;该网格分区带 processorCyclic 挡板对、无法重构,runner 按既有规则在原有 8 个分区上求解(日志明说) |

## 取舍与记录

- meshConfig 仍有两份(根与副本),这次只按裁决处理 simulationConfig;meshConfig 的单份化留给后续的薄契约工作。
- 旧版 HTML 编辑器与只写副本的工具(aidesign、测试夹具)仍产出 v2,照常可跑。
- 观察到、既有行为:求解前的分区提示说「将重构网格再按本次核数重新分区」,但带 processorCyclic 挡板对的 dcCart 网格无法重构,
  后端回退到原分区数——提示与后端行为不一致,记下,未在 E8a 内改。
