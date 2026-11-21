> 状态注记（2026-10-21，非所有者原文）：下方原文不改。这里记录的 Adam ODIL 慢且不收敛的问题，后来按文中方向改用牛顿类方法解决：现在的生产求解器是可微 Newton DAE（严格 `.liquidrun` v3/v4，稳态、瞬态与隐式伴随），GGA 水力加热步进是 v1/v2 的兼容路径，Adam ODIL 只作对照基线；T 型接头局部损失、按 ISA Kv 计算的阀门、两容积 CDU 换热器与泵都已实现。当前架构见 [liquid-dae-solver-first.md](../docs/developer/liquid-dae-solver-first.md)，现状见 [PROJECT_STATE.md](../docs/developer/PROJECT_STATE.md)。

# Background
请你思考一下液冷模型如何在我现有的dcdtuni这个仓库里面实现，尤其是要有统一的使用体验，这是一个非常庞大的任务，包含3D如何显示液冷管路（例如如何绑定到RACK的模型上，），2D如何拖拽配置建模液冷环境，如何设置冷板冷头各种物理特性，如何将其与server绑定属性，如何让界面美观可靠。 底层瞬态的液冷模拟solver该如何高效实现，然后每个server的温度又如何点击显示。 最重要的是，如何和我现有的风冷模拟的界面融合起来。 有非常多的事情需要考虑，请你帮我仔细计划一下，这可能需要非常多时间的调研，多多参考文献，搜搜开源代码， 先好好规划一下 /brainstorming
---
Liquid Solver Architecture Review — Synthesis
The Two Problems
1. Too slow (even on GPU): Shinaide_Liquid (56 cold plates, 227 edges, 172 nodes) takes ~5 min on GPU for 10k epochs and doesn't converge (loss=160.6).

2. Correctness questions: rack_summaries show 35°C / zero flow — is the physics right?

Root Cause Analysis
Why It's Slow: Adam Is the Wrong Solver
Our ODIL approach treats all state variables (P, m, T_w, T_s) across all timesteps as free parameters and minimizes physics residuals with Adam. This is designed for million-parameter neural networks, not ~2000-DOF structured algebraic systems.

Approach	Iterations	Per-Iteration Cost	Total
Our ODIL/Adam	5,000-10,000	O(N) gradient	Minutes on GPU
Newton-Raphson (reference approach)	10-50	O(N²) Jacobian solve	Seconds on CPU
Modelica DAE (ORNL Frontier)	Implicit RK steps	Sparse Jacobian factorization	Seconds on CPU
The ORNL datacenterCoolingModel solves a 75-cabinet Frontier cooling loop (larger than our Shinaide case) in seconds via implicit DAE integration. Our approach takes minutes and may not converge.

Contributing factors:

Loss weight imbalance: momentum=1e-5 vs BC=100.0 — a 10^7 conditioning ratio
Magnitude mismatch: P10^5 Pa, m10^-2 kg/s, T~300 K
Naive initialization: all flows at 1.0 kg/s (must flip signs for return paths)
torch.compile fragility: disabled on key code paths, net benefit questionable
Correctness Gaps
Issue	Impact	Reference Approach
No T-junction minor losses	Manifold branch tees have K=0.5-2.0, significantly affecting flow distribution	ORNL uses SpecifiedResistance with measured R values
Valve Kv units inconsistent	Off by factor ~1138 from standard ISA Kv	ORNL uses ValveLinear with dp_nominal/m_flow_nominal
Junction temp only 2 iterations	Insufficient for deep manifold chains	Modelica solves simultaneously via DAE
Near-zero Re instability	f_lam = 64/Re → ∞ at Re→0, gradient explosion	Not an issue with DAE solvers
No CDU model	Our solver has no CDU heat exchanger — just pressure BCs	ORNL models CDU with 2D UA lookup table, pump curves, expansion tank
No cooling tower / secondary loop	Only the rack-side loop is modeled	ORNL models full facility: CDU → hot water loop → cooling towers
What Components Are Modeled vs. Missing
Component	Our Solver	ORNL Reference	Gap
Pipes (L, D, roughness)	✅ Darcy-Weisbach + Swamee-Jain	SpecifiedResistance (linear)	Ours is more detailed
Cold plates (NTU)	✅ UA-based NTU-effectiveness	Available but unused (lumped)	OK
Pumps (head curve)	✅ Quadratic affinity law	✅ CombiTableCurve, variable speed	OK
Valves (ISA equation)	⚠️ Non-standard Kv units	ValveLinear (dp_nominal)	Unit concern
Pipe bends (K_minor)	✅ Auto-detected from waypoints	Lumped into R values	OK
T-junction losses	❌ Missing	Lumped into R values	Gap
Manifold distribution	❌ Plain pipes	MixingVolume plenums	Gap
CDU heat exchanger	❌ Not modeled	✅ 2D UA lookup table	Major gap
CDU pump	❌ Not modeled	✅ Variable speed, PID control	Major gap
Expansion tank	❌ Not modeled	✅ Pressurization	Minor gap
Secondary loop	❌ Not modeled	✅ Hot water + cooling tower	Design scope
Blind ends	✅ Dead-end detection, m=0	N/A	OK
Gravity/hydrostatic	✅ Elevation from waypoints	N/A	OK
Transient events	✅ Time-varying Q, pump speed	✅ Full transient + controls	OK
Recommended Roadmap
Phase 1 — Fix convergence (performance):
Replace Adam with Newton-Raphson or Gauss-Newton for the steady-state algebraic system. Use PyTorch autograd for Jacobian computation + torch.linalg.solve for the linear step. Expected: 10-50 iterations instead of 5000-10000.

Phase 2 — Fix flow distribution (correctness):
Add T-junction minor loss coefficients at manifold branch points. Fix valve Kv unit scaling. Increase junction temperature iteration count (or solve simultaneously).

Phase 3 — Add CDU model:
Model the CDU as: heat exchanger (UA-based or NTU) + pump (head curve) + control valve. This closes the loop and enables facility-level simulation.

Phase 4 — Smart initialization:
Propagate rough pressure/flow estimates from BCs through the network topology before solving. This alone could halve convergence time.

Papers Downloaded (10 papers in Ref_paper/LiquidCooling/)
Key papers for our roadmap:

Brewer2024 — ExaDigiT digital twin framework (the reference we studied)
Jadhav2026 — CDU allocation optimization (Phase 3 reference)
Zhang2022 — Cold plate technology review with NTU models
Biswas2024 — Lumped-parameter reduced-order model (<5% error vs CFD)
Fu2018 — Modelica Buildings library DC cooling package (foundation for ORNL work)
Full survey at /mnt/lv/litianyu/mycode/dcdtuni/Ref_paper/LiquidCooling/paper_survey.md.
---
OK here`s two more bugs or requirements:
1. “Session expired during screenshot. The browser's auth session is expiring too quickly. Let me check and fix the session timeout, then re-login and continue”
This is a very serious bugs, you need to find the code that certifies the duration period and change it into 12 hours.  And try to replicate the bugs after your modfication through browser-use again. If there`s no more session timeout in the short time it works and you are done.

2. please do remeber ,In the liquid mode, the postprocessing tab should not contain any tags/buttons/items that related to aircooling mode(such as ,surface filed, slice view, steamline view, Patch infomation, Openfoam case loading bar .... etc.) verify your fix through WebGL browser-use /verification-before-completion  
---
Then, logg into webfronend ,and switch to liquid mode. and load shinaide_liquid.cfdproj. Then, try to trigger liquid mode solving on the web frontend. Do remeber to monitor the solving proceess, make sure the residual window display properly. Finally please do remeber to navigate to  posprocess tab in the Liquid mode, and click the server in the model tree to visualize CP-CU temperature throgh time as X-axis.

please make sure to do all the action throgh browser-usw with WebGL version /verification-before-completion
---
很好，现在要对你进行终极考验了, 请你以Liquid.cfdproj这个case为测试案例，为我跑通液冷求解用于监控与后处理的完整流程。 请你严格使用browser-use的方式来在我的前端（/mnt/lv/litianyu/mycode/dcdtuni/src/start_server.sh这里启动服务器，然后去浏览器访问打印出来的地址）进行点击触发操作，以实现液冷相关内容的配置和求解触发。

例如我现在能发现的BUG，就是，液冷模式下，点击求解tab之后，右边栏显示的内容还是来自“风冷”模式下的server inlet/outlet patch的监控或者传感器位点的温度监控。 这是不对的，在液冷模式下，这里右边栏tab就应该显示，不同server的CP-CU pair的入口温度/出口/流量的监控（本质就是按server分类生成cp-cu pair条目的tick box， 以及CDU的tick box，监控回水温度和水压），或者说冷板的温度监控，而不是风冷模式下的inlet/outlet patch的监控。 这时候你需要去修改前端的代码逻辑，保证在液冷模式下，点击求解tab之后，右边栏显示的内容是液冷模式相关的监控内容，而不是风冷模式相关的监控内容。 这里你要好好设计一下 /frontend-design

其次就是求解完成之后，点击后处理tab之后，左边栏应该变回“模型树”的显示界面，然后用户任意点击一个管道或者一个server的CP-CU pair，就应该弹出一个浮动窗口（和求解监控时候的窗口一样，服用代码），然后窗口里面就应该显示了该对象的：水温，流量，水压，随时间变化的曲线。

注意上述功能必须帮我严格再浏览器里面跑通测试，并每一步都要视觉验证正确，尤其是这个监控数据的显示界面，必须要有正确的曲线显示，并且要有正确的物理参数显示。 这部分功能实现之后，我们就算是完成了液冷模拟的第一个版本了，后续我们可以在这个基础上继续迭代改进，例如增加更多的物理参数监控，增加更多的后处理功能等等等等。 但是最重要的是，我们要先把这个基本的功能实现了，并且保证它是可靠的，用户体验是好的，界面是美观的，这样我们才有一个好的基础来进行后续的迭代改进。

/brainstorming
---
还有BUG：
1. 你看![alt text](image.png)现在的水阀几何外形完全是浮空在管道上面/下面的，完全没有紧贴管身，这是不对的，请你修复
2. 现在对水阀/T型管鼠标右键弹出来的“翻转90°”选项实际作用是翻转180°，然后“
翻转180°”实际是360度，导致没有任何效果。 请你修复这部分
3. 你的水阀的“黑色”阀门旋转圈圈，内部应该绘制出“支撑架”，就是类似自行车轮圈的支撑部分也要绘制出来，现在完全是一个空心的圆环，这个设计太过于简陋了，完全没有体现出这是一个阀门的感觉了，请你改进一下这个几何设计。
---
继续回到液冷模式，请你改进几个易用性提升：
1. 水阀和T型管，应该在渲染区域可以按住shift/ctrl进行多选，（这部分代码请你去参考rack/baffle/floor/box等obj多选你的时候得代码，可以批量编组，并且右边栏新增给一个按钮：“批量翻转90°”，即可以把水阀或者T型管道沿着管道轴线旋转90°，当然不批量选中的时候，水阀和T型管高亮选中之后的右边栏也应该这些选项） 这一点还是没实现，我决定这样，变为多选水阀/T型管 之后，任意对一个点击右键，现在只会弹出“删除水阀/T型管”的选项对吧，现在增加2个“旋转90度”和"旋转180度"的选项

2. 还有，我现在觉得"水阀"的渲染区域建模太难以和T型管建模区分了，都是圆柱体，我觉得“水阀”这个几何你要好好设计一下，变为一个非常真实，具有复杂外形的阀门的样子，而且显示体积要小一些，你现在绘制的圆柱太高太突出了。

3. 同理T型管外形我觉得还不错，但是问题和水阀一样，你绘制的太大了，请你把T型管的T型突出部分绘制短一些，至少是现在长度的一半

4. 除此之外我还发先了BUG：， 当我在左边栏模型树（不区分液冷模式和风冷模式）disable 管道的item的tick box之后，管道和水阀和T型管以及管道的拐点的黄色球，并不会同步的立刻消失显示，这是极度错误的。 管道的模型树item tick box应该同步管理这个管道上的所有内容：“水阀，T型管，管身，拐角球，高亮球，端点球，盲端” 的所有渲染显示，并且开关tick box都应该立刻马上生效，马上再渲染区域看到结果
/test-driven-development

---
还有BUG：
1. 管道被高亮选中之后，其内部节点和端点的高亮提示圆球无视了“摄象机”遮挡视线的情况，即其可以透过RACK/floor等等等等各种物体显示到最前端（最靠近用户的摄象机），这是不对的，非常影响视角的判断，请你修复这个BUG，保证当摄象机转动直到管道被某些OBJ遮挡住的时候，其高亮提示圆球也会被遮挡住，而不是一直显示在最前端。
2. 同理，这些管道/管道端点/管道内部控制点/水阀/T型管/盲端 等等对象在渲染区域也有“最高优先级”的被选中优先级。 即： 即使用户旋转摄象机看不到管道了，但是鼠标点击遮挡物obj的与管道视角重合的地方，居然也是优先选中“管道/管道端点/管道内部控制点/水阀/T型管/盲端” 等物体obj。这是极度错误的，我再次强调，渲染区域的单机选中层级逻辑一定是 靠近“摄象机”的obj/face等等被优先选中（当然是要忽略被disable显示的物体）
---
还有BUG， 
1. 当用户切换为液冷模式之后，液冷求解的tab里面怎么还在显示风冷模式相关的求解选项，这是及其错误的。请你删除。
2. ![alt text](image.png) 看图，这里没有一个按钮是点的动得，没有任何反应，我也不懂什么是“耦合仿真”
2.1 你这里也违反了太多了双语设置得错误了，说了英语就只有英语，不要用“/”隔开。
2.2 这里的设备级视图和编辑管网两个按钮有啥用？？？？，官网难道不是在“几何” tab里面直接进行3D编辑得嘛？

---
现在我觉得我的仓库里面前端建模已经进行的差不多了，现在最重要的就是进行，后端Solver的设计。 首先我给你一些我和GPT讨论之后的材料：/mnt/lv/litianyu/mycode/dcdtuni/Ref_paper/LiquidCooling/FromGPT.md
然后还有一个现有的参考仓库：/mnt/lv/litianyu/mycode/DiffLiquidTwin

我现在想要你做的就是全面调研我当前仓库的液冷相关模块设计，然后我来理解哈，根据FromGPT.md这个文档里面建议的做法， 我想要你全面设计的是：
1. 从前端JS把cfdproj里面定义的三维几何，导出为抽象的Graph数据结构，保存为一个类似于.cfdrun的文件(液冷模式下我决定命名为.liquidrun)，这个是核心的runtime文件，用于feed给液冷求解python的后端
2. 我期望这个液冷的后端solver，能够像当前的aircool逻辑一样，通过python后端把.liquidrun文件，编译/翻译，为一组pytorch的nn.module函数，然后这些nn.module的类，通过forward构建为一个计算图，然后构建总的loss函数。
3. 目前我想要的是使用adam来做优化，而且是要实现瞬态模拟。
4. 瞬态模拟的意思是，例如对于RACK的server的CP-CU对来说，其流量Q的维度不是0，而是[t,1] 这样的tensor维度，或者说对于每个对象的每个物理参数，都有一个额外的t维度来表示瞬态的变化。
5. 当然你在真正实现Solver之前，你要figure out，我的液冷模拟中的每一个物理部件需要哪些物理参数：
RACK, server, 冷板，歧管接口，T型结构，Flow Control Valve， CP-CU pair， CDU， 管道， 输运管道等等这些部件，他们的物理参数都有哪些？ 例如：压力， 水温， 流速， 还有其他的什么参数？ 我记得FromGPT.md里面有提到一些。
6. 其次最后就是，我想要的是非因果式建模，并且式基于优化器的ODIL算法，我暂时并不想引入二阶方法，例如Newton法等等，我想先试试Adam方法。 本质上就是，后端代码，需要根据前端导出的graph所在的.liquidrun文件（本质上是json）
7. 最后对于这个solver后端，你也要模仿我的aircool的逻辑，liquidrun文件应该被前端生成到/mnt/lv/litianyu/mycode/dcdtuni/case/Liquid/model这里的case指定文件夹里面，每一次求解构建的pytorch模型和求解log，也应该按照 log/日期 这样的路径形式，放到/mnt/lv/litianyu/mycode/dcdtuni/case/Liquid/log这里 

/brainstorming
---
还有BUG：
1. ![alt text](image.png) 你看这里，我不是在模型树这里多选了DC的所有元素吗，然后我在右边栏开启了批量移动按钮， 我成功批量移动了DC的所有元素（包括管道），但是这时候我按ctrl+z，想要撤销上一步的批量移动操作，结果发现管道的撤销操作完全无效，管道依然停留在被移动之后的位置，而其他元素的撤销操作是正常的，这个问题很严重，请你修复一下。/test-driven-development
2. ![alt text](image-1.png), 你看，我从2D设计界面，点击“应用生成3D房间” 之后，生成出来的wall对象的法向量都是指向房间内部的（应该是红色箭头指向房间外面），这是重大BUG
3. 2D平面设计的右边中的“房间高度”一栏的设置，虽然看似是同步了工程根节点里面设置的单位为mm，但是实际上内部的计算数值却是以m为单位的，这个不应该，应该完全以mm为单位来计算才对，请你修复一下这个BUG
4. 液冷模式下，ACU, 白模box, 等物体还是会默认被半透明显示，怎么调整右边栏的透明度条目都没用，这是重大BUG，需要立刻修复 
/test-driven-development
---
还有就是模型树中的 管道和CDU的type条目还是无法被shift多选！！！， 你应该去复制其他type，例如RACK, WALL这些和shift/ctrl等多选按键相关的的代码逻辑。
---
还有双语BUG： 请你找出当前的界面显示内容中，所有同时显示中文和英文的条目，全都进行清理，要么显示中文，要么仅显示英文，这个又前端上边栏下拉框控制。 不要再用 “/” 这个符号来分割显示了，导致条目超长， 完成之后，把这一点加入本仓库的开发rules。 /test-driven-development
---
还有BUG：
1· ![alt text](image-4.png)，如图，模型树里面，按住shift多选，还是没法跨越type选中CDU和管道，例如，我首选选中RACK，然后按住shift，在选中墙壁，CDU和管道就是无法被选中。
2. ctrl + z/y似乎对管道的所有操作都有很强的延迟，例如：我添加一个管道，或者调整管道位置，或者调整管道的长度，或者调整管道的弯曲程度，这些操作之后，我按下ctrl + z，撤销上一步操作，界面始终感觉没反应，但是这个反应非常慢，大概需要等个几秒钟才会有反应，这个延迟太长了，应该优化一下。我怀疑是我需要移动视角才会重新渲染？？？， 这是不应该的需要立刻修复
---
新BUG:
1. ![alt text](image-1.png) 你看图片这右键菜单里面两个字体大小不一样，这是不应该的，应该统一字体大小。
2. ![alt text](image-2.png) 其次就是这样右键管道不在需要弹出这个升级T型分支的菜单选项了，而且这个菜单触发条件还很离谱，无需高亮选中管道端点，直接右键就能弹出，这种逻辑也是禁区
3. ![alt text](image-3.png) 你看现在管道的直角拐角处的渲染图像也太粗糙了，这个拐角看着一点都不光滑，而且这个拐角时常还会很扭曲，反正你这里代码应该保证，这个拐角无论用户怎么弯折，都是沿着弯折的方向来顺滑拐弯的，其次就是中间的这个实心深黄色球，应该始终跟随管身的渲染逻辑，我经常遇到的情况就是，我在模型树disable 管道，管身确实不显示了，但是黄色的关节球还在。
4. 
---
我现在想的是，在前端切换为“液冷模式”之后，点击sever之后，右边栏能够出现一些“冷板-计算设备（简称Coldplate-CU pair, CP-CU）”对的添加选项，然后变成一个history list的样子，即我可以为这个server添加液冷模块，每添加一个 CP-CU，server就会被垂直切片显示为多个不同的片，或者块。 当然取消server选择之后，还是恢复正常的显示。
同时，每一个server都只提供一个出水口/入水口的选项和3D显示渲染的结果。 至于这部分如何开启呢：
用户点击前端的液冷模拟，然后整个界面完全切换为液冷设计的模式，现在不与任何air-cool-cfd模拟产生任何联系。 
切换为液冷模式之后，右上角同样保留正常数据中心添加模块，例如添加,ACU, RACK, baffle等等，只不过多了一个添加CDU的选项。

然后在液冷模式下，用户按住shift点击rack，可以看到右边栏的有一个下拉框（下拉框位于“RACK详细设计”按钮的上面），可以调整为“液冷机柜”或者“风冷机柜”。 一旦切换为液冷机柜之后，“RACK 详细设计界面” 里面，就应该加入各种液冷的设计元素：

例如： 
1. inlet正面 的前面offset大概10cm的地方 左右两边，应该出现与机柜高度平行垂直的两两根歧管。 一根是热水管，另一根是冷水管
2. 一旦添加server之后，server的inlet面自动伸出两个接头，分别于冷热歧管相连
3. RACK细节设计的右边栏下面给一个下拉框，可以选择，热水歧管/冷水歧管 是否同时位于左边，同时位于右边，还是一个再左边，一个在右边
4. 我发现进入液冷设计模式之后，右上角有很多无关的选项，我说了右上角这些内容，仍然全面复制继承风冷那里的可添加的模块的设计，只不过切换为液冷模式之后，新增两个可选的选项： 一个是CDU  , 另一个是 “送水管道”， 特别的对于“送水管道” 这里，默认长度是1M，管径是10cm, 然后渲染区域可以拖拽改变管道的位置，反正就相当于完全新建一个obj.

同时最重要的，对于渲染区域这个管道的两头要有两个可以被高亮选中的圆球。 任意点击一个圆圈之后，会出现一个和“后处理”tab里面添加流线的球体预览的 gizimo（要求直接复用代码）。 本质上两端的圆球，就是用户可视化编辑管道走向、长度的控制头。 用户可以通过拖拽gizimo改变管道长度，和给管道拐弯，直到鼠标拖拽第一端到达CDU附近之后，自动吸附接上CDU。 

同理，用户拖拽另一端的gizimo靠近一个RACK的“歧管”附近之后，自动吸附连接上歧管。 并且自动识别颜色为歧管的颜色。 当然默认是棕色的管道（即：没有连接RACK的管道）。 
/test-driven-development  

你先把这部分逻辑给我设计出来，并使用browser-use视觉验证
---
有几个BUG:
1. 管道的中心点的圆球的gizimo不应该是产生“扭曲”形成夹角的，而是选中中间的gizimo是对管道的整体移动。
2. 两端的gizimo, 应该始终有一个轴是沿着管道的轴向方向的。 或者说，其两端的控制gizimo都应该是沿着轴向方向来+垂直方向来绘制。 然后呢：如果拖拽平行沿着轴向法相的箭头，就是延长这一端的管道。
3. 反之，如果拖拽的是垂直的gizimo箭头，则是将“该管道”沿着垂直方向弯曲，并且弯曲的地方，自然的形成“顺滑的转角”。
4. 任何gizimo都应该具有多次选中并生效的逻辑，不是一次性的。
5. 液冷模式的左边的模型树栏，和“风冷模式”那里相差太多了，请你全面同步相关功能，例如：条目的tickbox, obj条目的复制按钮，obj条目的删除按钮，并且obj条目要和渲染区域同步高亮。
6. 液冷模式下不要透明化RACK， 而为了不遮挡RACK内的歧管，请你把歧管绘制到inlet面前面来，大概距离5cm左右的区域

/test-driven-development  
---
对的，但是现在请你仅实现3D建模的方式，或者说，对RACK， server的 类，尤其是液冷模块的子类的设计。 具体的物理属性建模的事情放到后面来设计。 这里我想要实现为“Openmodlica那样的建模方式”即，每一个“液冷相关的部件（例如冷板，歧管接口，快拆接口， 输运管道）”都有一组“物理参数”，或者多组“物理参数”，例如“压力， 水温， 流速”（具体是啥你要去调研）。 然后他们都需要对这些物理参数满足一个所谓的约束方程，然后这个方程的组装或者实现，都需要被收集为pytorch loss. 当然这部分很麻烦，你先预留设计接口。后面再说，现在先解决前端3D显示的问题 