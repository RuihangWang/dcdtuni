# 冷板单一真源回执(2026-09-30)——`cpCuPairs` 是服务器冷板的唯一列表

**结论:过门(单元测试 + 全仓液冷算例清单对比)。** 未做浏览器门。机读回执 `receipt.json`,逐文件对比
`manifest_diff.json`。

环境:Mac,独立 worktree `.codex-tmp/wt-coldplate`(自 `origin/main@da9a4d83`),分支
`fix/coldplate-pairs-authoritative`。40901 与 8360 全程未触碰;Mac 主 checkout 仍在 `feat/transient-t2c`。

## 问题

一台液冷服务器的冷板存在两处:`liquidCooling.cpCuPairs`(数组;React 检查器、瞬态参数登记表
`cp.heat`/`cp.dT`、时间轴、旧 HTML 编辑器都写它,`liquid_graphgen.js` 编译它)和
`constructions.coldPlates`(按 uid 键的对象;只有 v3→v4 迁移写过一次,新服务器建为 `{}`)。
`9bbdfe16` 让图编译取 pair 的**数值**,但**成员与顺序**仍来自 `coldPlates`:在迁移过的服务器上,
检查器里新增的冷板不进图,删掉的冷板照样编译。`liquidPostprocess.resolveTreeClick` 读同一份副本,
而编辑器新建服务器的 `coldPlates: {}` 是真值对象,于是点击这类服务器什么也画不出。

## 改了什么

| 文件 | 内容 |
|---|---|
| `react/src/liquid/cpCuPairIdentity.ts`(新) | 纯函数。`backfillServerPairUids(server)`:无 uid 的 pair 按迁移器自己的键规则(`id ?? name ?? cp_<i>`)认领它被迁移进去的那块 plate 的键作 uid,每块 plate 至多认领一次;找不到 plate 的(编辑器建的服务器、迁移后新增的)取服务器内未被任何 pair/plate 用过的 uid。所有 pair 已有 uid 时不动(幂等)。**不从 plate 复制任何数值。** `newPairUid(pairs)`:新 pair 取 `CP-CU_k`,k 从自身位置起找第一个空闲的 |
| `react/src/liquid/dynamicLiquidGraph.ts` | 删去 `coldPlates` 覆盖;对私有快照跑上述回填(任何文档——活文档、原始 JSON、研究 CLI——得到同样的 id),再以 pair 的 uid 作 `name` 与 `_stable_uid` |
| `react/src/liquid/liquidColdPlates.ts` | `addPair` 写入 uid;`editPairs`(检查器增/删/改/预设的唯一入口)在应用修改前先对该服务器回填 uid,同属这一步撤销;播放头离开 t = 0 时不回填 |
| `react/src/liquid/liquidPostprocess.ts` | 只读 `cpCuPairs` |
| `docs/reference/cfdproj-v4-schema.md` | `constructions.coldPlates` 一行注明现行实现 |

`coldPlates` 此后只在上述身份回填里被读,**不再决定成员或数值**;文件里保持原样,未做保存时镜像。

### 为什么 uid 在「首次编辑」写入,而不是在载入时

最初版本在 `projectStore.loadProject` 里回填,活文档一打开就带 uid。但项目内容哈希
(`projectContentHashSync`)是结果与工程版本的绑定:`renderer/windResultProducer.ts:76-78`、`:133-135`
在结果的 `projectContentSha256` 与当前工程哈希不符时**抛错**(「Wind result project revision does not
match the current .cfdproj」「Preview source does not match…」),`renderer/resultLayerRevision.ts:58-60`
清掉过期图层。载入时写 uid 会让 5 个 pair 原本无 uid 的工程(`Liquid`、`LiquidSingle`、Shinaide ×3)
一打开就作废全部已有风冷结果与网格预览。现在:**打开不改变工程**(`projectStore.ts` 与基线逐字节相同),
编译按需推导 uid;第一次在某台服务器上编辑冷板时 uid 随这次编辑写入——这次编辑本来就改变哈希,之后
改名不再移动该冷板的边。播放头离开 t = 0 时静态字段冻结,uid 若随关键帧一起写会让关键帧被拒
(实测未加保护时 `reason: 'static'`),故该情形不回填;增/删/改名在 t ≠ 0 本来就被拒。

## 测试

| 套件 | 结果 |
|---|---|
| vitest 基线(`da9a4d83`) | 307 文件 / 3118 测试全过 |
| vitest 本分支 | 308 文件通过 + 1 跳过 / 3128 测试通过 + 1 跳过(跳过的是按环境变量开启的清单导出器) |
| `tsc -b`、`tsc --noEmit` | 均干净 |

先红后绿(`coldPlatePairs.test.ts`、`liquidPostprocess.test.ts`;另改了 `liquidColdPlates.test.ts`
中 `addPair` 的期望以包含 uid):

- **迁移算例上在编辑器里删 pair**(`Liquid`,`removeCpCuPair('m_1','e_1',0)`):`m_1_e_1_cp_CP-CU_1` 离开编译图,其余 12 条冷板边 id 不变。改前:仍在。
- **迁移算例上在编辑器里加 pair**(`addCpCuPair('m_1','e_1')`):新增且仅新增 `m_1_e_1_cp_CP-CU_5`。改前:没有新边。
- 打开 `Liquid` 后 pair 无 uid;在 `e_1` 上改一个 `Q_W` 后 `e_1` 的四个 pair 带上各自 plate 的键,其他服务器不动。
- 迁移算例上改名后编译边 id 不变(去掉载入回填、尚未加编辑回填时此项变红为 `m_1_e_1_cp_GPU-0`)。
- 编辑器新建服务器(`coldPlates: {}`)删第一个 pair,其余冷板边 id 不变。改前:位置 id 整体前移。
- 旧编辑器「删后再加」造成两个同名 `CP-CU_2`:两者得到不同 uid,plate 只被认领一次,数值取各自 pair;已有 uid 的 pair(NV_PEK02 形)原样不动;新 pair 的 uid 不与服务器上任何 pair 冲突。
- `resolveTreeClick`:`coldPlates: {}` 的服务器能解析到冷板;残留已删 plate 的服务器解析到现存 pair。
- 守护:Shinaide 在 t = 120 s 编辑 `Q_W` 成为关键帧、不被拒、不写 uid(与既有 `projectStoreTransient` 同名场景一起,在未加 t = 0 保护时两者都红);`keeps cold plate identity after another plate is inserted before it` 的夹具从往 `coldPlates` 插改为往 `cpCuPairs` 头部插,断言不变。

## 全仓液冷算例清单对比

导出器 `react/test/coldPlateManifestDump.test.ts`(默认跳过)对 `case/**` 下全部 11 个液冷 `.cfdproj`
各走三条路径,`diff_manifests.py` 逐字段对比(节点、边及其参数、机柜与 `cp_cu_pairs`、绑定、事件、
时间、`source_project`):

- **editor**:`checkProjectDocument → store.loadProject → compileSavedExperiment`
- **raw**:对解析后的 JSON 直接 `compileSavedExperiment`
- **research**:`tools/compile_cfdproj_liquidrun_v3_entry.ts`(仅有已存 DAE 的 5 个文件能编;其余 6 个改前改后都抛出同一条「无已存 DAE」错误,也逐字对比)

**33/33 逐字节一致,包括 `source_project.sha256`。** 其中包括 pair 显示名与 plate 键不同的 NV_PEK02
(编译快照上的 `name` 仍取 uid,`cp_cu_pairs[].id` 不变)。

## 可见的行为变化

1. **仓库外**、pair 无 uid 且 `coldPlates` 为空的服务器(React 新建后加的冷板)经 React 动态编译时,边 id
   从位置式 `…_cp0` 变为 `…_cp_CP-CU_1`(以 pair 名为 uid)。仓库内没有这种服务器;任何算例的
   `runDefaults`/`transient` 里都没有保存冷板边 id(已 grep)。旧「Run Liquid Solve」路径
   (`compileLiquidGraph` → `liquid_graphgen`)不经此处,仍是位置 id,与 `cpCatalogFromProject` 一致,未改。
2. NV_PEK02 冷板后处理窗口标题从 plate 键(`nv_heat_01`)变为 pair 显示名(`MOCK equivalent 01`);
   只影响显示,边解析走结果里的 `cp_edge_ids`。
3. 在某台服务器上第一次编辑冷板时,该服务器所有 pair 多出 `uid` 字段,随保存写盘(实测:`Liquid` 改名
   后保存,文件里为 `uid: "CP-CU_1", name: "GPU-0"`)。

## 顺带核对

- 时间轴轨道按 `server/pairIndex` 指向 pair。在 `Liquid` 上把第 4 个 pair 的 `Q_W` 改为 999 再删第 1 个:
  轨道 `e_1/3=999` 随之变为 `e_1/2=999`,仍落在 CP-CU_4 上——删 pair 时轨道会重排,不需要后续修补。
- 同一服务器上两个 pair 手工写成相同 uid:`liquid_graphgen.js` 的重复边 id 校验拒绝编译(失败即关闭),
  但 React 侧抛出的是笼统的「Liquid topology cannot be compiled; check CDU and rack connections」,
  具体原因只在浏览器 alert/控制台里。已知局限,未改。
- `backend/aidesign/projcomp.py` 两处列表同时缩放、改风冷时同时清空,不依赖哪边为准,未改。

## 未做

- 未把 `coldPlates` 做成保存时镜像:若要做,必须在 `projectForSave` 的 `stripTrackedFields` 之后由 pair 生成,
  否则会把随播放头变化的 `Q_W` 写进 `coldPlates`,让磁盘哈希取决于保存时刻。
- 未跑 40901 浏览器门;分支未推送。

## 合入 main 复验

合并时 `origin/main` 已前进到 `462d0d01`(含 T2c,其中 `projectStore.ts` 多两行;与本改动无冲突)。
合并提交 `225109f2`(`--no-ff`)上重跑:vitest 312 文件通过 + 1 跳过 / 3141 测试通过 + 1 跳过,`tsc -b` 干净;
以 `462d0d01` 为基线重新导出 11 个算例 × 3 条路径,33/33 逐字节一致(含 `source_project.sha256`)。

## 复现

```bash
cd src/frontend/react
npx vitest run
COLDPLATE_DUMP_DIR=$PWD/../../../.codex-tmp/dump_after npx vitest run test/coldPlateManifestDump.test.ts
# 在 da9a4d83 上放入同一个导出器、导出到 dump_before 后(它只调用两边都有的接口):
cd ../../.. && python3 docs/benchmarks/coldplate-pairs-authoritative-2026-09-30/diff_manifests.py .codex-tmp/dump_before .codex-tmp/dump_after
```
