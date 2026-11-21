# Stage 1 回执 — 在线供水温度命令通道(primary_inlet_K,2026-09-28)

分支 `feat/liquid-supply-temp-mms-demo`;计划与决策:`docs/archive/liquid-2026-09-10/liquid_demo_mms_decisions_2026-09-28.md`
+ OneDrive `Research/DCTwin/LIQUID_DEMO_MMS_PLAN_2026-09-28.md`(Stage 1)。

## 改动面(solver 数学零改动,`git diff --stat src/backend/liquidcooling/solver/` 为空)
- `dae_experiment.py`:HX 稳定 edge ID(`graph.hx_indices→edge_str_ids`)、`_active/_queued_inlets`、
  `set_inputs(primary_inlet_K=…)`(全量校验后写入;正数 K;未知 edge 拒绝)、`_theta_at` 覆盖元组加
  `('primary_inlet_K',…)`、step/equilibrium 合并 active+queued、frame 增 `primary_inlet_K` 字段、
  accepted 事件与 `accepted/pending_primary_inlet_K` 属性
- `dae_runtime.py`:JSON 校验透传、metadata `pending_primary_inlet_K`、controls 增字段、
  **checkpoint v3**(携带接受轨迹与 pending;v<3 拒新字段/新事件,循 v1→v2 先例;干净 v2 仍可重放)、
  executed_manifest/result 增 trace 输出
- `accepted_run_input.py`:`build_accepted_manifest(primary_inlet_K=…)`,checked_trace 加 positive 开关;
  inlet trace **整体替换** planned schedule(circuits 按 edge_id 对齐),source 追加 `:accepted-trace`,
  整体重过 LiquidrunDAEV3
- `run_manager.py` / `server/liquid_runtime_api.py`:透传 + allowed 白名单加 `primary_inlet_K`

## 测试(RED→GREEN 各留档于本目录 README 尾部命令)
- 新契约 `test_online_supply_temp.py` **8/8**:排队→下一接受步生效+物理响应(回水温度上升)、
  校验全量原子(7 类非法输入拒绝且零变更)、done 拒绝、失败步 pending 保留不提交、
  executed manifest round-trip(重载后 theta 槽=295)、checkpoint v3 逐值重放、
  v2 拒新字段+干净 v2 兼容、runtime JSON 校验
- 合法适配 3 处旧断言(`test_dae_live_actuators.py` 精确 dict 相等扩新字段;v1 降级测试 pop 列表加两键)
  ——断言仍为严格相等,非削弱;适配后 23/23
- 回归:`test_liquid` **594 passed / 5 skipped**(基线 586+8);全量后端 **1789 passed / 6 failed(既有
  template_params×4+room_enclosure+mesh_engine 同款)/ 64 skipped**(基数上浮来自并库合入的测试)

## 12 帧在线阶跃 demo(demo_12frame.json,可复算脚本同目录)
- N_t=13 schema 测试夹具(events 清空,schedule 拉伸),seq6 在线命令 290→295 K
- 一次回水:阶跃前 290.125000 K → 末帧 291.333908 K
  (**响应向上 1208.91 mK**,assert 通过)
- applied_inputs = [seq6 primary_inlet_K 295.0];executed trace = 290×6 + 295×7;source `frontend:accepted-trace`;N_t=13
- 全程 Newton residual ≤ 4.25e-13,能量余额 ≤ 7.22e-09 W;wall 0.156 s(CPU 1 线程,无 GPU)

## 判读边界
mock 夹具(schema 测试 manifest),非 Shinaide/NV 现场校准;响应量级由夹具热容决定,不代表真实机房。
复算:`PYTHONPATH=src python docs/benchmarks/liquid-supply-temp-online-20260928/demo_supply_step.py`
(脚本内路径写死 40901 仓库根;source_sha256 在 JSON 内)。
