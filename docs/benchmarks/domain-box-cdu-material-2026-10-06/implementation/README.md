# BOX/CDU material recipe implementation — source freeze

本包交付用户已授权的材质所有权小切片，供 ROOT 独立逐行审查。八个产品路径、两个新定向测试以及原基线绑定均已冻结。没有运行构建、浏览器、API、服务、Kit、网格或求解器，没有 Git 操作。

## 变化与责任边界

`shared/js/box_material_recipe.js` 是无 renderer 依赖的 UMD/CommonJS 数值核。它拥有 BOX 的默认材质预设、painted-steel 角色及集合默认值、粗糙度归一化、照片资源引用选择、透明度/深度写入/偏移、纹理重复和 UV 处理意图。函数没有读取场景、THREE、DOM、纹理缓存或 IO；脚本入口只注册 API。返回每次新建的普通描述对象。`materialRecipeKernel.ts` 仅作类型与模块桥接。

`GeometryEntity.materialRecipe(part, context)` 委托该核，使用 registry 已解析的 kind，保留实际没有 record.kind 的 BOX/CDU 支持。定义并未扩充，名称不推断 PDU 的物理类别。ACU、custom 等类型不会获得伪 BOX 材质方法支持。cfdproj/cfdrun schema、图编译、物理字段、冷板六个源文件均未修改。

`geometry_scene.js` 的实际 BOX/CDU structural part lowering 消费同一描述；照片、CDU library 与 BOX/缺 library 的 Phong 分支消费数值参数。默认 BOX tile 与 normalScale 也来自核。现有 texture IO、THREE.Color 转換、procedural canvas、material/texture cache、PBR late binding 和 typed UV 写入仍由原载体完成。原非 BOX 类型的 preset/分支保留，shared `face-image:` 分支数值委托与原生成输出比较。

载体注入当时可见的 library 是否存在、painted-steel 公共角色与集合均值、外部纹理 clamp/tile 情况，以及原 patch preset 资源解析结果。没有把 library 存在视为资源 loaded，也没有将 plain recipe 描述视为缓存当前值。已捕获的历史 rounded-opacity cache collision 行为保留：相近透明度请求可能复用原材质。recipe 是创建意图，实际当前材质、资源加载状态及已变换 UV 以载体为准。

`pbr_materials.js` 的 painted-steel 默认值和该集合归一化委托同核；其资源/异步绑定/cache 机制保留。React geometry loader 在 PBR 前导入同核，classic HTML 在 PBR 前载入同一脚本。classic 页面原 getCachedMaterial/spawnRenderable/renderBoxModel 三个 wrapper 与 shared getCachedMaterial 函数逐字保持，见 preserved-carrier-spans.json。

本切片没有声称完成所有 DC 实物的材质方法，也没有新增 USD 独立材质 lowering；现有 renderSnapshot 继续承接真实载体属性。它也没有改变几何产品、STL lowering 或 physical type。

## 原始 producer 对照

原 42-payload 包 `box-cdu-material-original-baseline-20261006` 已于实现前捕获实际 LiquidSingle CDU 与 Shinaide BOX/CDU 三种无 record.kind 的原 producer 输出，附原源/输入绑定。16 个原材质控制覆盖 actual creation、library absent/present-unloaded、late texture binding、图片、阈值、显式 false、tile 与历史 cache collision。另四个原 face/patch dataUrl/URL precedence 控制也使用原 producer；原 structural fixture 的完整 F32/index/UV、grouped STL 与 snapshot 不重录。

新 `boxMaterialRecipeParity.test.ts` 重放这 16+4 组输入，经当前真实 SceneBuilder/geometry_scene/PBR 输出，对 frozen 原材料、资源引用、浮点位、UV 字节及 renderSnapshot 完整比较，不从未来 kernel 计算 golden。没有改写任一基线 JSON。新的 controlled assertions 补足原 capture 格式未列的 renderOrder、polygonOffsetFactor 与显式 transparent/depthWrite；新增 mutable public role/mean、实际载体 UV 仅缩放一次及 pbrUvReady 保留的控制。

材质 parity 使用原 capture 的 canvas/TextureLoader 受控 seam。fake canvas PNG、空 Texture、人工 late binding 都标明在 test/source 中；这证明原 producer 与新 producer 的属性/资源引用/快照一致，不证明网络图片解码、像素质量或真实 GPU 渲染。没有把 creation intent 等同任意缓存历史的 live state。

## 原始失败与验证记录

原缺 `materialRecipe` 三个实际样本 RED + 一个 supported-kind control PASS 留在原 42 包，也复制 domain-missing-method 原 log/command 到本包。原捕获的 canvas stub setup failure 与 type-only TS2352/修正重查仍由原 42/type13 包保留；其 manifest 与每个 payload 在本次完成时逐 size/SHA 核验，原包没有覆盖。

首次实现定向七文件实际为 96 PASS / 1 FAIL（总 97）：唯一失败是新 standalone CommonJS expected object 漏列原 shininess/specular 字段。原测试源、raw log/receipt 保留，修正自己的断言，不算产品缺陷 RED。随后五文件 42 PASS；首次 tsc exit2 为本新测试 JSON inferred union 的 pbrRole 属性 TS2339，原测试源/日志保留，只改 erased 类型断言。

最后一次 BOX 参数消费补实后，同五文件再次 42 PASS，24.491385s，raw log SHA daab866379dc085f29894947587b4d7771339dde25a1eafe61ddc6425162aeed。这五文件包括 materialRecipeKernel、原20材质 parity、materialRepeat、原 BOX structural F32/index/STL/snapshot 与 hole-order parity。其后全局 tsc --noEmit exit0，34.359127s，empty log SHA e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855。计数互相有重叠，不能相加当独立覆盖数。首轮其它 PBR/Domain/CP 相关 tests 的实际 PASS 原日志也保留。

全部本代理命令使用 CPU28–31、CUDA empty、IT owned TMP；command.json/receipt.json 保存完整 cwd/argv/原始 returncode。未运行 tests 全库，也没有后续无依据重复测试。

## 冻结和保全

source-before.json / source-final.json 列 exact 八个产品路径，before/after 原文与 exact-eight-product.diff 可逐行核验。test-final.json 列两新测试、原缺方法测试与现 type-only capture 测试的源绑定。golden-identities-before/final.json 列原 16、4、结构三个 fixture，完全相同；immutable-*.json 是字节保全副本。

prior-packets-final-preservation.json 对原 42、type13、CTX CP57 共三个冻结包的每个 payload SHA/size 作独立再次核验。CP BaseEntity/HTML before byte 与 CP freeze 相同；此次只增已授权 material method/import/script。其余六个 CP 生产/测试源当前 SHA 与作者 freeze 相同，见 protected-cp-six-sources.json。source-final.json 和 after copies 是本次后置冻结绑定；不冒称记录过每次测试前所有八源 SHA。

本包未生成 dist 或删除旧证据；manifest 排除 owned tmp/cache，不包括 manifest 自身。原 RED、own-test assertion/type error、GREEN 与实际 producer JSON 全保留。产品源与测试现已 hold，等待 ROOT 独立审查、正常构建和真实浏览器验收；本包不提供 runtime/GPU/物理验收结论。
