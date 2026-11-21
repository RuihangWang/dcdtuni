# 风冷原生结果凭证 V1（隔离候选）

`air_run_result_receipt.py` 为一个选定的 OpenFOAM 数值时刻生成并复核**内容身份**。输入是当前 case、实际执行的 `.cfdrun` 路径、`case/log/<run>` 目录及正的有限数值时刻；返回纯 JSON 对象，不改求解器、后处理状态或任何原始文件。格式见 `src/backend/aircooling/runner/air_run_result_receipt.schema.json`。本隔离分支另由 `air_run_execution_receipt.py` 接入 `STLGenRunner` 的成功求解出口；尚未接入 React 或主开发分支。

V1 记录当前 case 名、run 与 `.cfdrun` 的 case 内相对路径、`.cfdrun` 逐字节 SHA-256、其中的 `runId` 和 `sourceProject.contentHash`。它调用现有后处理的 `_openfoam_result_layout`，因此只锁定该时刻实际会选的串行根网格或完整分区网格；五份 `polyMesh` 文件逐文件留路径和 SHA-256，另外留与既有 `mesh_identity_sha256` 同算法的布局摘要。若旧串行根网格与较新的分区结果并存，摘要针对**被后处理选中的布局**，不误用旧根网格。

每个选中 rank 必须恰有一个对应的数值时间目录（允许 `100`/`1e+02` 在不同 rank 上表示同一时刻，但同一 rank 的双别名拒绝），该目录必须同时有 T 与 U。每个场只能是原文件或 `.gz` 中的一种；凭证记录实际文件相对路径及压缩状态下原始**字节** SHA-256。校验器检查 OpenFOAM `FoamFile` 的 `class`/`object`，并从文件头检查 T 的 `[0 0 0 1 0 0 0]`、U 的 `[0 1 -1 0 0 0 0]` `dimensionSet`；压缩流完整读取以核对 gzip CRC。非有限/非正时刻、缺失/歧义文件、越出 case 的路径、case 内 symlink 别名、缺少源工程哈希、空文件、哈希改变均拒绝。仅在日志出现 `Solve complete` 而无 T/U 文件时不能构造凭证。复核时调用方须再次显式传入当前 case、执行清单和 run 目录，校验器从磁盘重建并逐字段比较，不以凭证里的路径自行选择 case。

本对象没有 `accepted` 或 `converged` 字段：它证明所指文件**现在的字节和量纲**，不证明求解器确实执行了该 `.cfdrun`、网格质量、场的完整语法/数值有限性、收敛、守恒或风液耦合。JSON Schema 只描述外部形状；安全边界是实际路径解析、重新哈希与时间/量纲核查。V1 也没有签名，持有文件写权限者可以同时重写数据和凭证。

当前集成：`_run_solve_from_mesh` 先核查有效执行清单在所选 case 内、具有 `runId` 和 `sourceProject.contentHash`。旧 schema 虽允许后者缺省，本接入选择**求解前 fail-closed**，提示从已保存 `.cfdproj` 重新导出，避免昂贵求解结束后才发现无法建立来源身份。随后要求新 run 目录不存在，以免同一秒的重跑继承旧结果；求解命令零退出，且并行重构/显式边界值写入完成后，选后处理可读的最新正时间，并构造及即时复核纯 V1 身份凭证。若任何 rank 出现比选中时刻更晚的部分写出目录，也拒绝退回旧的完整帧。随后以独占链接原子发布 `<run>/air-run-execution-receipt.json`，发布后再复核一次。hybrid 使用其网格面积重标定后的 `hybrid-mesh-<meshhash>.cfdrun`，普通运行使用 `self.cfdrun_path`。字段缺失、量纲不对、来源/网格改变、求解命令抛错或目录碰撞都没有 `accepted` 文件。

外层 `air-run-execution-receipt/v1` 的 `status=accepted` **只表示该 runner 求解命令成功返回，且原生网格与 T/U 字节身份检查通过**；`solverCommand` 是 runner 记录的命令，不是独立执行证明。消费时必须用 `verify_air_run_execution_receipt` 对当前 case、执行清单和 run 目录重新核验。它不代表网格质量、收敛、守恒或场数值可信。若需要从“身份完整”提升到“物理可接受”，须另行关联同一网格身份的严格 `checkMesh -allTopology -allGeometry` 成功回执、`convergence.json` 的明确收敛判定及适用的热量/来源门禁。当前 Shinaide 严格网格检查失败；本隔离分支没有为其生成或验收 CFD 正结果。

V1 没有把求解时环境覆盖的 solver binary、生成的 `controlDict`/`fvSolution`、外部 fvOptions 或代码版本纳入执行来源。下一版应将这些实际求解输入也固定到 run 级凭证，防止仅凭 `.cfdrun` 哈希误认完整执行输入；这不影响现有只读 verifier 的文件身份用途。

验证（Mac，CPU，合成 OpenFOAM 文件与模拟命令，不运行真实 CFD）：纯凭证、接入及邻近求解路径定向测试通过。缺 U、求解命令异常、部分 rank 新时刻、同秒目录碰撞都会拒绝发布；缺项目哈希的旧清单在求解前拒绝。混合风液路径另检验回执绑定实际调整后的 `.cfdrun`，而非原始输入。与质量/上传锁/分区 hybrid 查询组合后的完整后端回归 **1727 通过、40 跳过、10 项既有失败、3 个子测通过**；失败仍在旧液冷 ODIL、meshgen、polyMesh、room enclosure 与旧模板期望。本轮结果回执未由真实 OpenFOAM 求解或 GPU 运行验证；React 浏览器单独完成了液冷 mock 实验，不能当作风冷结果回执的真实求解证据。
