# BOX/CDU v2 independent closure

结论：原 v1 报告的一般 polygon context 孔方向 parity gap 已关闭。冻结 structural BOX/CDU product 源审没有剩余 P1/P2，支持 ROOT 继续正常 build/browser 产品验证。此结论不替代真实 WebGL/OV/CFD 验收；本审查没有执行 tests、build、probe、浏览器、服务、Git 或业务。

作者 v2 manifest `fb4a8bed21602600851606ed940df2eaa353c446c200d9bffddf07f55be6979f` 的全部26 payload / 83478 B 独立逐 SHA/长度核验。新 kernel `4a7465de8fa3433f636f93849fa43f56dba57b8edc0bfb0635429bc99fc509f4` /12716 B，唯一生产变化是156–159把既有 holes.forEach 反向移出 outer反向条件，与绑定 installed ShapeGeometry103–119独立规范每条hole的行为一致。没有改变 Earcut、算术、layout/两次F32、命名、wallOpacity、carrier/material/PBR/image UV；其他13条原source/test/fixture全部身份保持，只新增focused test。

独立完整 v2 diff 与作者diff字节相同；15 live sources全部匹配source-final身份。原作者56实现包、原31baseline、原独立74payload checkpoint-v1均重新逐 byte/hash核验不变。原fixture/capture没有重录；原 observed failure/REPORT-v1不覆盖。

focused test `b8225804f3bf3844e9beb7dbd7b97e11a023739477dc885ce6c60be77c3d0b3c` /2854 B 直接使用绑定 installed Three Shape→ShapeGeometry(32)→原front layout.applyMatrix4，为 CWouter+CWholes 与 CCWouter+CWholes 各自产 oracle。patch position/normal/UV全部F32字节和index类型/顺序比对，没有使用新kernel为旧oracle重录值。实际原7e10191 kernel RED1fail/1pass，rawlog保留expected/received positions差异；修复后82focused PASS含原23golden/15kernel及scene组。新test初始2-arg声明/3-argruntime seam的typing报错与原source保留；最终仅test显式runtime cast后两case重跑PASS/tsc0，两case与82重叠，不累加。数字来自作者日志，本审查未执行。

当前 normal自产quad/circle无holes或clipper规范outer/inner来源，仍未证明正常caller生成原CW/CW故障；修复是一般lowering语义一致性收口，不能称已有case产品P2或网格修复。原材料范围维持：rawUV/source recipe被迁出，真实PBR/image UV/texture IO继续carrier，原golden为webglfalse/noimages，没有全变体验收。对象UID/registry kind、pure ownedbuffers/record无mutation、CJS/classic/React加载、非BOX旧调用保留的原v1源审结论继续成立。

证据：v2 author-packet、independent-v2.diff、independent-identities.json；全部IT/.codex-tmp/owned、CPU28–31/CUDA空。原56/31及独立v1保持，最终source-only接收不把剩余domain/USD/RAM transport/solver/MMS工作称完成。
