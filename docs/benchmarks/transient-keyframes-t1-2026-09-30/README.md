# 瞬态关键帧 T1 回执(2026-09-30)——cfdproj v5 核心模型

**结论:过门。** cfdproj v5(轨道即全部状态)在 JS / Python / React / 服务器四层落地:旧 v4 工程打开后在 t = 0 的取值与原文件一致,
UI 编辑写入轨道,保存为剥除轨道字段的 v5,重开值不变。途中发现并修复 2 个缺陷(见文末);计划外补 1 处 Python 消费者。
机读回执 `receipt.json`,证据在 `evidence/`。

环境:40901,`src/start_server.sh --app react --port 8390 --case-dir <repo>/case/Base_coldAisleIso`(无 OV),React 构建 `b4c1905c`;
浏览器为内置浏览器窗格经 Mac 隧道。8360/8361 全程未触碰。分支 `feat/transient-keyframes`。

## 测试

| 套件 | 结果 | 与 main 基线 |
|---|---|---|
| node `test_cfdproj_transient.mjs` + `test_html_v5_guard.mjs` | 25/25 | 新增 |
| node 全部 `src/frontend/test/*.mjs` | 除 7 个文件外全过 | 7 个失败文件与干净 main 完全相同(`test_cfdproj_migrate`、`test_html_case_path_restore/pp_patch_tree/solve_partitions`、`test_model_library`、`test_patch_filter`、`test_patch_selection`) |
| vitest | 303 文件 / 3101 测试全过 | 新增 `projectStoreTransient.test.ts` 8 项 |
| `tsc -b` / `vite build` | 干净 / 通过(40901 `--build`) | — |
| Python(40901,CPU,`-m "not slow"`,含 JS↔Python 奇偶 1540 项) | 3398 过、64 跳过、7 失败 | 6 个 FAILED 在 `origin/main 00fa2cd9` 上逐一复现;第 7 个 SUBFAILED 是 dcCart 内存守卫读取主机实时 MemAvailable(当时仅 29 GB,需 ≥ 33 GB),与本分支无关:分支上单独重跑 3/3 通过、main 上通过 |

变异检查:`edit()` 不走 `commitEdit` → store 测试 3 项失败;undo/redo 不重新物化 → 新增用例失败;v5 两处 Python 消费者去掉修复 → 新用例失败。

## 浏览器冒烟(40901)

| # | 项 | 结果 |
|---|---|---|
| 1 | 载入 Shinaide_liquid(磁盘 v4) | 内存中 v5、稳态、126 条轨道(含液冷四类端点边界参数);e_1/e_2/e_5 功率 1000/2000/1000 W、冷板 100/110/140 W、CDU 35 °C / 60、ACU 20/21 °C,与磁盘一致;800 网格 |
| 2 | 载入 Base_coldAisleIso(磁盘 v4) | v5、7 条轨道;ACU 送风 15 °C / 4 m³/s、服务器 150/100/100 W 与磁盘一致;属性面板显示 15 / 4;顶栏送风汇总 4.00 m³/s;92 网格 |
| 3 | 属性面板把送风温度 15 → 17 | 字段 17;轨道 t = 0 关键帧 17;`saveProjectJson` 中该字段已剥除、`role` 等静态字段保留 |
| 4 | 保存到 `/tmp/t1gate/`(未覆盖 case 文件,`git status case/` 无变化) | 文件 v5、`validate_transient` 无错误;Python `project_at(saved, 0)` 与原 v4 相比,轨道字段只有这一处差异 |
| 5 | 重开保存的文件 | 属性面板送风 17 / 风量 4,其余值不变 |

保存文件与原文件的其余差异(`inletOffset`/`serverConflicts`/`liquidCooling` 等补全、`casePath`)来自 React 保存时既有的机架规范化
(`shared/js/rack_state.js`,早于本分支)与 case 目录重设,与瞬态无关。样本见 `evidence/Base_coldAisleIso_saved_v5.cfdproj`。

## 发现并修复

1. **播放头不在 t = 0 时,工程设置被当作"静态"拒绝**(`b4c1905c`)。载入工程会经 `store.edit` 重设过期 casePath;原 `staticText` 把
   `meta`/`runDefaults`/`instrumentation` 也算静态,于是 t ≠ 0 时改 case 目录或求解参数都会被拒。现只冻结场景状态(顶层 `geometry` +
   `constructions`),spec §1.5 已写明。
2. **新 store 测试在 `tsc -b` 的 `noUncheckedIndexedAccess` 下报错**(`3d8802ea`),40901 `--build` 因此失败,已修。

## 计划外

- `aidesign/projcomp.py` 从模板求和服务器功率;v5 模板只在轨道里有功率,会静默编出 0 W 布局 → 改为拒绝 v5 模板(`46863c20`)。
  调用方都在 `ddl/`(历史快照)。

## 事故记录

门禁期间 40901 一度无响应(约 10:10–10:45 SGT):ping 与 TCP 22 正常但 sshd 不发 banner。进入后查明为共用用户 ztw 的
BatteryTwin rebuttal 作业占约 180 GB 常驻 + 51 GB swap(swap 全满,负载峰值 512 / 48 核);本分支的测试已先跑完,规模可忽略。
只结束了本会话自己的 8390 服务;按用户决定未动任何他人作业,机器自行恢复。

## 未决 / 交给后续阶段

- T2:`mutateLive` 拖拽在播放头 ≠ 0 时需拦截;风冷导出读取的遗留全局需取 t = 0。
- T3:退役实验面板的 `duration_s` 镜像与 primary_circuits 标量。
- `test_parallel_mesh` 依赖主机实时内存(已另开任务)。
