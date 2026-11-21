# BOX/CDU structural product — original frozen implementation independent review v1

结论：当前正常 BOX/CDU 调用链未发现 P1/P2。完整 numerical kernel 与 carrier delta 的源审支持这一结构几何切片；另发现一个一般 context 的孔方向顺序差异，ROOT 已授权作者单独窄修与原 ShapeGeometry focused 对照。在 v2 closure 前，不声称一般 context 三角化语义全部等价。没有我方测试、probe、编译/build、浏览器、服务、业务或 Git 操作。

## 冻结身份和原始 oracle

作者 manifest `0199177c7e933b5025c8be946ac0bf4915a06aa3bee480b65a9efc4537918a0a`：56 payload / 5026380 B，全包逐 SHA/长度验证，完整原字节复制到 author-packet。source-final.json 14 条 live/frozen copy 全匹配。新 kernel `7e10191dc14f151b0446a94c1839138295f60e7f5f4211b60344bfe6d71321a5`；Earcut `91d75c133fb4a16ede87a607a59c52b0d68c8efa1008a413dae68ea424ad66ce`；BaseEntity `dcf0b057506f477cba6eca1d950334f9fa046ad406417693e968c3dbf67d7312`。本审查独立重建 implementation.diff 与作者完整 diff 相同。

旧 31-file baseline manifest `22a7a807558c507b2e3ff7a63eb2fc5d68a2a48a506acc150bc3d826a1e6c90c` 及全部 payload 重新逐 hash 验证。当前 capture 与 fixture 字节保持原样；原 regression 只删除 unused Construction import，所有断言未改。三条真实记录是 LiquidSingle CDU、Shinaide UPS BOX/CDU，另七个明确标注 synthetic；源输入/无 kind 形态已在独立 baseline review 绑定。10 controls 的 F32 raw position/normal/UV/index、精确 index order/type、world matrix 数值序列、完整 grouped STL toFixed(6)、snapshot/camera 来自原 producer。不能把 JSON matrices 当所有 F64 signed-zero bit 证明。

## 单一几何所有权和算序

box_geometry_product.js 23–87 复制原 circle/quad/coercion、patch island 合并/refIds/name/color 行为；geometry_pure.js 382–406/563–566 对这些委托共享 kernel。923–945 的一般 box frames 委托 numerical frames，仅包装 Three Vector3/Matrix4，wall-mounted ACU 分支保留。新核心 100–110 使用相同六 face basis、origin、size；154–190 保留 path line t=0/1 连续精确去重、closing endpoint 去重、area/triangulation 流程。

安装 Three 原源码与新 kernel 对读：ShapeGeometry 初始 Float32BufferAttributes→BufferGeometry.applyMatrix4 的 layout 二次 F32；新代码 168–173 首次 F32，175–186 读取该 F32、按原 Vector3/Matrix3 inverse-transpose arithmetic 再写 F32。World transform 和 STL Double/toFixed(6) 仍在原 carrier/export 路径，未把 world 提前量化或把两次 F32 合并。Uint16/32 分界与原 setIndex 的 >=65535 行为一致。新 Earcut numeric body 与安装 Three0.184 原始源仅三 export removal 后完整相同，ISC notice 完整存在。

BaseEntity 45–49 从 registered definition.kind 和稳定 identity.uid 派生 product，不要求源 record.kind；unsupported entity 或非 box/cdu product 明确抛错。registry 明确 assetDefinition.type=pdu 才选 PduEntity，物理 kind 仍 box，名字不改变物理类型。产品 positions/normals/UV/index 为每次新 typed arrays，source recipes/refIds/patchIds 新容器，project record 不 materialize 或深拷贝全项目；没有 Three/renderer/DOM 依赖进入 numerical kernel。partId 是固定几何下的 uid/face/island/fragment 派生身份，不是跨任意拓扑编辑不变的持久 object UID。

## 已保留的 carrier 行为与加载

geometry_scene.js 1478–1519 只把 box/cdu 改成 BufferGeometry 消费 product；non-box/cdu ACU/cold_plate/custom-no-cache 仍走旧 _buildSingleBoxFace。SceneBuilder 339–365 与 legacy temp export 1054–1064 当前都先取 getFaceLayouts/buildFaceIslands，再按既有 kind 派发。raw PDU display 和 liquid CDU 工程细节继续由 SceneBuilder 加非导出 carrier。

壁 opacity 的旧 ensureWallOpacity 文档补默认仍在 carrier（1491），kernel 191–194 只读取同一默认，原 disconnected fragments 的文档 mutation 行为没有冒称已消除。CDU face palette/color layout mutation 保留；image patch/wall 按原 first-refId/faceSource 路径读取，并保持 white color/objectKind/stretch UV/renderOrder。完整 `_buildSingleBoxFace`→module EOF suffix before/after 字节相同，SHA `20840e8bec29774167d5cb648c5211b9720170900452e7f2abe9664713e2b066`，PBR/image UV、texture/material IO/cache 原实现未改。

Classic HTML 插入 Earcut 在 clipper 后、product 在 stl_naming 后和 geometry_pure 前。React bridge/kernel side-effect imports 同依赖顺序；browser UMD 使用 globalThis===window，CJS product require 三纯依赖，几何 pure 保留 lazy CJS lookup。作者 retained classic/CJS tests 实际含无 THREE/document 环境，不依赖 ShapeGeometry fallback；本审查只读其源码/日志，未执行。

## 一般 context 算序观察（保留原冻结，待 v2 closure）

新 kernel 156–159 在 `!(area(contour)<0)` 分支中同时反向 clockwise holes。安装 Three ShapeGeometry.js 103–119 对 contour 的反向和 holes 的反向分别独立。若内部 context.faceIslands 提供 clockwise outer + clockwise hole，旧流程仍反向 hole，而新流程跳过；即使 Earcut 仍能三角化，vertex/index/F32 顺序不等价。直接数值例可取外环 [(0,0),(0,4),(4,4),(4,0),(0,0)]、孔 [(1,1),(1,2),(2,2),(2,1),(1,1)]；这是源推导线索，未运行。

当前实际 domain method 不接 supplied context；normal buildFaceIslands 的未合并 quad/circle 无 holes，union/difference 使用 clipper canonical outer/inner；正常 carrier context 亦来自同一 producer。作者确认未找到真实正常 caller 生成上述 CW/CW 双环。因此此观察是一般 lowering parity gap，不称现有 case 产品 P2 或额外业务阻断。ROOT 已授权移出 holes reversal 分支并添加与原 installed ShapeGeometry 直接对照的 focused 基线，原 10 golden 不可重录；须以独立 v2 包/closure 完成。

## 证据限制与后续

作者最新相关 186 PASS、Base-level/domain-project 77 PASS 有重叠，receipt 明确 unique 227；tsc0。不是本审查执行，也不是浏览器/真实 WebGL 验收。原 golden 为 webglAvailable=false/no images，对 raw UV 与保留源码有证，对所有 PBR/image UV/material 变体无全量验收。无需扩大 capture 或制造新的批准门；正常 build/browser 属 ROOT 后续产品验证。

本次是 BOX/CDU structural GeometryProduct 切片；其他 entity product、物理端口、直接 USD/OV 消费 domain、cfdrun RAM transport、网格/求解/MMS 未在此完成。上述数值类从 plain record 派生，不等于整个对象架构已经迁移完。

证据目录包含原56packet、独立 source identities/完整 diff/原 regression diff、13个实际 dependency/caller source context。全部新证据在 IT/.codex-tmp，CPU28–31、CUDA空。旧 baseline/作者 source/服务均只读。
