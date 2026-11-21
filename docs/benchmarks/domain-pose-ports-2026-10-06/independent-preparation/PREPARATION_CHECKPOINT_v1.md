# Pose/ports 独立只读准备 checkpoint v1

2026-10-06。AIR 冻结审查优先，本材料尚未形成最终pose/ports审查。无测试/build/browser/source mutation。

已读实际source风险（不是新实现缺陷判定）：

- geometry_pure.js:240–261 q/scale逐component Number.isFinite，不coerce字符串；270–307 point/anchor Number(v)||0。负数/零scale当前允许，direction scale×vec→rotation→normalize，不能换normal matrix。
- Three0.184.0源码 Quaternion:623–659 sqrt(sum of squares)，zero→identity，nonzero乘1/length；Vector3:471–490 cross-expanded applyQuaternion，769–796 normalize使用length||1。hypot或矩阵展开顺序会破float64 parity；极大/极小finite值需baselinecases。
- Rack _rackManifoldLocalPoint:310–345供端substring supply/inlet或exact manifold_in；CDU:353–365没有manifold_in alias，unknownrole落return。end只有exact bottom；slotheight>1e-6 fallback语义，manifoldEndpoint:212–216外层>=1e-6独立。diameter/spacing finite允许0/负；both-left/right和其他layout分支、mountFace exact outlet不能合并校正。
- liquid graphgen:8–38 明示storage m,inputUnit为UI显示,inputScale=1。本slice应留原_worldToMeters不动，不按外部DSX mm属性或Z-up要求改domain source coordinates；领域描述若写SI要注明当前工程chain验证依据。
- 调用者：SceneBuilder/applyModelTransform；Viewport拖动；cfdrunBuild/modelWorldTransform；LiquidEquipmentVisuals local frame→group visual；liquidGlobals manifold snap world frame；liquid_pipe_renderer world port/coupler/route；liquid_graphgen header frame及server slot local point变换。所有location/quaternion/scale必须geometry source truth，不用model.transform mirror。
- 脚本序列：legacy STL_generate.html:1229 graphgen,1231 pipe_renderer先载入；1244 rack_state,1248 geometry_pure再载入。新增kernel必须在pure前，保lazy使用；React geometryBuilder shared side-effect imports对应核必须先于pure。
- 既有 HTML cloneModelTransform wrapper:2922调用(rawTransform={})丢弃argument；此为已读旧代码事实，未实验，不属于新pose核自动修复范围。
- DSX/SimReady单位与port属性来自现publication research/sources.json的日期化官方观察；geometry单位不等于port flow/temp/pressure单位。此切片不导出USD、不转换气压gauge/abs或流量、不承诺certification。

待独立paritycases：真实LiquidSingle/Shinaide+identity/rotated/nonunit/zero q、负/非uniform/zero scale、字符串/null/NaN/Infinity和默认coercion、signedzero、Rack3layout/未知layout和mountFace、供回别名/unknown/bottom大小写、slotheight=1e-6与两侧、diameter/spacing0/负、无geometry/kind unsupportednull、local/world一致和store输入hash不变、CJS/legacy order。cases基线必须原producer预先捕获float64bits，不能新实现自己生成expected。此处仅计划，不执行。
