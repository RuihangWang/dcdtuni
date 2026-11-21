# MMS 产品 case 解析推导 — 49 维线性热 ODE 的闭式瞬态解(2026-09-28)

配套:`case/MMS_Product/`(fixture,`f73614bb` 生成)、`tools/mms_product_acceptance.py`(验收)、
回执 `docs/benchmarks/liquid-dae-mms-product-20260928/`。
**独立性协议**:拓扑索引与参数**数值**读自编译后 manifest(数据事实);本文的组装**公式**是
对连续物理(控制体能量守恒 + NTU 代数 + 节点混合)的独立手推;验收脚本按本文公式用 numpy
重新实现,**生产残差从不生成解析轨迹**——它只出现在单向的组装审计(A·v 对比 JVP  restricted)里。

## 1. 系统与控制体

fixture 编译图:90 态 = 13 压力 + 14 流量 + 14 节点温度(代数)+ **46 水 cell 温度 + 1 solid
+ 1 hx secondary + 1 primary return(动态热态,共 49)**。水力与热解耦(液冷网络无浮升耦合项),
稳态水力解给每条边定常流量 m_e(验收断言全程恒定;实测单环 0.183157 kg/s,回水侧为负,
`supply_seg1/return_seg1` 为零流盲支)。

三类边的出口温度规则(`_thermal_fields` 的连续物理含义):
- **pipe**:出口 = 末 cell 水温(正向流;逆向取首 cell)——水 cell 串联平流链,进入下游节点混合;
- **cold plate(NTU 集总)**:ε = 1 − exp(−UA_cp/(w·cp)),出口 = (1−ε)·T_node,up + ε·T_s;
  换热量 cp_heat = w·cp·ε·(T_s − T_node,up) 只进入 solid 方程。**冷板与泵不含水 cell 储能态**:全部 46 个水 cell 属于 pipe 边;
  `supply_seg0/return_seg0` 各 10 个,`supply_seg1/return_seg1` 各 9 个,另四条支管各 2 个。
- **hx**:出口 = T_sec(二次侧集总体)。

节点温度(代数):有入流节点 T_k = Σ_e w_e·cp·outlet_e / Σ_e w_e·cp(入流边出口的流量加权平均);
声明温度 BC 的节点才钉在 `temperature_bc`;本 fixture 的 `cdu_return` 仅声明压力参考,
其温度由混合方程求解。零入流节点钉在 `node_gauge_temperature`。
cp 边出口含 T_node,up → 节点方程之间线性耦合,写成 14×14 线性系统 **N·T_node = P·x + q**
(bc/gauge 行是单位行+常数;cp 贡献移项到 N),消元得 T_node = N⁻¹(P·x + q)。

## 2. 动态方程(逐控制体)

记 x = [T_w(46), T_s, T_sec, T_pri]ᵀ,u₁ = 冷板热负载 Q_W(t),u₂ = 一次侧供水温度 T_inlet(t)
(**demo 场景的固定边界,验收里按 schedule 变**)。

- 水 cell i(边 e,热容 C_i = ρ·cp·V_i):
  `C_i·Ṫ_i = w_e·cp·(T_up,i − T_i) + Q_cell,i`,T_up,i = 首 cell 取上游节点温度,否则取链上前一
  cell(流向由 m_e 符号定;零流边 w=0 → Ṫ=0,恒常态,λ=0);本 fixture Q_cell ≡ 0(断言)。
- solid:`C_s·Ṫ_s = −G·(T_s − T_node,up_cp) + u₁`,G = w_cp·cp·ε, C_s = 1000 J/K。
- hx secondary:`C_sec·Ṫ_sec = w_hx·cp·(T_node,up_hx − T_sec) − UA_hx·(T_sec − T_pri)`,
  C_sec = ρ·cp·V_sec = 997·4182·0.002 J/K。
- primary return:`C_pri·Ṫ_pri = UA_hx·(T_sec − T_pri) + m_p·cp_p·(u₂ − T_pri)`,
  C_pri = ρ_p·cp_p·V_pri = 997·4182·0.001 J/K。

节点消元代入后得 **ẋ = A·x + B₁·u₁ + B₂·u₂ + c**(49 维;A 含 node 反馈的稠密化贡献,c 收
bc/gauge 常数项)。全部系数在定常流下为常数 → 线性时不变。

## 3. 闭式解与奇异性

零流死区使 A **奇异**(λ=0 分量),平衡点不唯一 → 不用 A⁻¹,用**增广矩阵指数**:

    A_aug = [[A, B·u_i + c], [0, 0]]  (50×50),  z = [x; 1]
    z(t_i) = expm(A_aug·Δt_i)·z(t_{i-1}),  u_i = 该采样区间的常值输入

输入语义与生产 BE 严格对齐:**sample i 的值 u_i 作用在区间 (t_{i−1}, t_i]**(BE step(seq=i)
用 parameters(i)),阶跃在第一个 schedule[i]=新值的区间生效——闭式与数值吃同一段常值输入,
不存在时间错位解释空间。

稳态初始化等价性门:DAE 用 steady 初始化;闭式侧断言 `‖A·x₀ + B·u₀ + c‖∞ ≤ 1e-9`
(x₀ 取 DAE 的稳态初值;死区分量对应 0=0 行,自然满足)——证明两者从同一平衡点出发。

## 4. BE 时间误差与验收口径

局部截断:‖x(t_i) − BE一步‖ ≤ (dt²/2)·max‖ẍ‖,ẍ = A·ẋ = A(Ax+Bu+c) 由闭式解析求出;
全局(Lipschitz L = ‖A‖₂):E(T) ≤ (e^{LT}−1)/L · (dt/2) · max_{[0,T]}‖ẍ(t)‖。
这条理论界需连续区间的二阶导数上确界;旧脚本没有计算它。其 `derived_error_bound_K`
仅是采样曲率参考量,见文末 10-05 修正,现已从验收门中移除。
验收门:
- **收敛阶**:order = log₂(E(dt)/E(dt/2)) ∈ [0.85, 1.15] (BE 一阶;E 取三关键温度
  T_s/T_sec/T_pri 在全部采样点的 max 绝对误差);
- **绝对误差(dt=0.25)**:load 阶跃 ≤ 5e-3 K、supply 阶跃 ≤ 2e-2 K(计划预测值 9.3e-4 /
  7.5e-3 的 ~5×/2.7× 安全系数,系数在此记录);并同时报告采样曲率参考量 E_reference 供对照;
- **组装审计**:随机 v,A·v 对生产残差 JVP 在动态热态子块的作用一致(rel ≤ 1e-8,float64);
- **物理门**:每步 Newton scaled residual < 1e-8;|energy_balance_W| < 1e-6;流量全程恒定。

## 5. 场景

| 场景 | 输入 | schedule(t<30 / t≥30) |
|---|---|---|
| S1 负载阶跃 | u₁ = Q_W | 100 W / 200 W(u₂ = 290 K 固定) |
| S2 供水温度阶跃 | u₂ = T_inlet | 290 K / 295 K(u₁ = 100 W 固定) |

dt ∈ {2, 1, 0.5, 0.25}(T = 60 s),每档由脚本按 dt 重生成 schedule 列表(schema 支持
标量|list[N_t],Stage 1 已证)并经 `load_dae_input` 全链加载——**验证走产品链,不走测试
adapter**。解析曲线同步落 `analytic_curves.json`(Stage 3 前端 overlay 的唯一数据源,
sha 绑定本回执)。

## 6. 门限实测记录与方法论教训(2026-09-28,验收通过)

| 场景 | BE 收敛阶(dt 2→1→0.5→0.25) | err@dt=0.25 | 采样曲率参考量 E_reference | err/reference |
|---|---|---|---|---|
| S1 负载阶跃 100→200 W | 0.941 / 0.973 / 0.986 | **4.55e-3 K** | 1.15e-2 K | 0.40 |
| S2 供水温度阶跃 290→295 K | 0.878 / 0.941 / 0.970 | **2.04e-2 K** | 5.93e-2 K | 0.34 |

其余门:steady-init 等价 1.5e-10、组装审计(JVP)1.1e-16、**节点一致性 3.1e-11**、
Newton residual ≤ 2.1e-13、能量余额 ≤ 2.3e-9 W、流量全程恒定(断言 <1e-12)。

**两条方法论教训(入库,防止重演)**:
1. **交叉验证不得与被验对象共享中间量**:首版节点消元 `Pn` 符号写反,JVP 组装审计却以
   1e-16 通过——因为审计的节点扰动方向 `v_node = S_x·v_x` 与 A 用的是**同一个错误的
   S_x**,系统性误差自洽抵消。抓住它的是稳态残差门(常数项不说谎),随后新增的
   **节点一致性门**(S_x·x₀+S_c 对生产 unpack 的节点温度,≤1e-9)成为 S_x/S_c 的独立锚。
2. **采样参考量不能当严格误差上界**:计划的 supply 预测 7.5e-3 低估了 5 K 阶跃在小一次侧容积
   上的截断误差(实测 2.04e-2,收敛阶趋于 1)。09-28 用
   E_reference=(dt²/2)·Σ‖ẍ(t_i)‖代替绝对误差门。10-05 审计撤销其严格上界表述:
   采样点曲率没有控制区间上确界,三温度投影也没有闭合的传播算子。
   该量只作后验参考;现行验收依靠独立闭式轨迹、时间细化阶数和物理约束。

## 2026-10-05 可复现性与误差口径修正

产品清单现以 `docs/benchmarks/fixtures/mms-product-v3.liquidrun` 跟踪入库;
旧 `case/.../Liquid/model/` 属于忽略的运行产物,干净 worktree 不会带入。
上表为 09-28 原始回执快照。`derived_error_bound_K` 是历史 JSON 字段名,其实际含义是
采样曲率参考量,不构成保证。完整 Shinaide 图的独立制造解与闭式验收另见
[shinaide_liquid_full_graph_verification_2026-10-05.md](../archive/liquid-2026-09-10/shinaide_liquid_full_graph_verification_2026-10-05.md);本小环路不能替代完整图证据。

## 2026-10-21 HX 定律随清单选择（hxModel）

工具从清单唯一一条 `heat_exchanger` 边的 `params.hxModel` 读出 HX 定律（缺省即
`mixed-volumes`，属于数据事实，与拓扑同等对待），两种定律都在工具内手推。把换热量写成
`Q = k_in·(T_in − T_pi) + UA·(T_sec − T_pri)`：

- `mixed-volumes`：`k_in = 0`，即上文的推导，矩阵逐位不变；
- `counterflow-ntu`：`k_in = ε·C_min − UA·(1 − a − b)`，`ε` 取教科书逆流有效度
  （`C_r = 1` 极限单列），`a = ε·C_min/C_sec`、`b = ε·C_min/C_pri`。流量恒定时仍为线性系统，
  二次侧行多出 `B2 = k_in/C_sec`，一次侧行的节点耦合为 `k_in/C_pri·S_x`。

`--fixture` 指定被验收的清单（默认冻结夹具）。2026-10-21 三份清单全部 8/8 门通过：冻结夹具；
经产品编译器重生成的夹具（与冻结夹具只差 `source_project.sha256`，HX 不写 `hxModel`）；
把冻结夹具的 HX 改成 `counterflow-ntu` 的变体（稳态初值 2.8e-14，组装审计 1.1e-16，
两场景收敛阶均在 0.93–0.99）。回执在仓库内被忽略的 `_tmp/campaign_A_fix/mms/out_*/receipt.json`。
