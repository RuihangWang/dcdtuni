# ColdPlatePair：正常浏览器与实际数值产物证据

本目录是新的运行期补充，保留ROOT正常浏览器9payload、独立实际产物比较33payload及各自原manifest。normal-browser/与independent-actual-products/按原声明路径逐字节复制，原manifest的root字段保持原值。原normal-browser manifest SHA228234d9a83a4abb4649193b3b841199f429aa172f87a95310e9cbd965ff5948，独立actual-products manifest SHA8b62d38235c0cd095cc2aa881a2c96348b7a8796d5947a9c5a4b5adacecfacad。publication-receipt.json记录每个原/副本的SHA、长度和模式。

既有[源码、RED/unit与正常构建checkpoint](../domain-coldplate-pair-2026-10-06/README.md)保持原80payload与原manifest SHA86bd266d9a1f81d1034644b67410251fcc2a1cd100e57c35afbe5412c6f7cd5e。此次前后独立重读全部80payload匹配，未修改旧README的日期化“runtime待验证”状态，也未重写旧manifest。source-build-checkpoint-manifest.json仅是其原manifest逐字节副本；本新运行期说明与旧checkpoint并列。

## 已有真实运行与独立比较

ROOT通过普通IT React index-Cv1ZvInu.js Prepare/Start流程运行另存后的DomainColdPlate工程，新run5aa7f3340e0849bdb6772d3d73c73965。独立比较将新旧各六个实际产物完整展开，枚举19个改变的typed scalar leaves：run_id、input.json project_revision 1→2、source_project.sha256及其派生身份hash。所有19旧/新值保存在independent-actual-products/all-19-typed-product-differences.json；没有排除整块metadata以隐藏差异。

除这19处运行身份外，两运行产物的JSON形状、scalar类型和值相同；全部有限浮点叶的Float64 bits相同，覆盖CP六参数、图拓扑/顺序/绑定、控制器/轨迹与50帧数值结果。两者均为CPU float64、2threads、dt0.5s、0到24.5s completed/converged。实际保存工程的完整语义只改变meta.casePath；stable UID、几何/物理、runDefaults、关键帧和unknown扩展保持。证据和原输入不是重新生成的golden，见[独立实际产物比较](independent-actual-products/README.md)。

正常浏览器AX报告50帧，heat audit核查56台server/2800sample、最大液冷热残差3.38e-8W且运行身份一致；原browser proof、原始两JPEG与文本在normal-browser/。这是ROOT实际UI证据；本整理未调用heat-audit API，也未重新运行solver/compiler/browser。独立审查明确未计算空气侧余热。

这关闭了ColdPlatePair纯参数委托在该人工定义的Shinaide fullgraph/轨迹上的数值兼容验证，不是vendor校准、任意轨迹、所有domain实体或完整Domain/SimReady架构验收。它没有新增MMS/AD、native memfd seals、GPU或OV光栅/贴图正确性证明；后续materialRecipe源变化不属于Cv1 runtime绑定。

## 版本控制候选

FILES.txt列本新目录全部文件，GIT_CANDIDATES.txt/GIT_IGNORED.txt按只读git check-ignore --no-index分类；未stage/commit。原单位RED与首次loader/类型失败仍留在原80包，未重录、更名或覆盖。被忽略文件仍按原字节持久复制并纳入本manifest；实际提交须按清单明确保全。HANDOFF、产品源、case及服务未修改。
