# 正常 React MeshGenerator b5a0cbc5：只读 native rc1 诊断

结论：这次正式路径的真实 native 失败是 **S10 单元457不闭合而拒绝导出**；不是schema4未解析，也不是runner的1000cells门槛。完整浏览器fullLog原解码第173行为：`S10 闭合断言失败：单元 457 的 ΣSf=0.003906 > 容差 0.000000（开放单元，拒绝导出）`，第194行terminal returncode=1。正常启动00:19:03到00:19:06约3s是失败时间，不是可用网格性能验收。

## 直接退出与源码绑定

实际命令采用正式vendor meshgen-cli、`cfdrun /proc/656586/fd/4 --surface-dir ... --dc-mode --dc-raw --dc-seeds pre --export-foam .../scratch/constant/polyMesh --report .../scratch/dc_report.json`。完整原命令与stderr/traceback保留在browser-extracted/fullLog.txt，30863B SHA706bb4fd508d776ae69660151e48f558edfdf856d7258e5859d33e675ea3b34d；native-exit-line-bindings.json逐行绑定。

实际--dc-raw分支是CLI main.cpp:1863–1866：generateCartesian返回false，写dcReportSidecar、打印rerr、return1。核心Cartesian.cpp:32679–32693对首个kind1失败构造同一开放单元错误并returnfalse。实际S7完整报告有kind1四胞457/463/469/475，sigmaSf全0.00390625，互斥旧分类coplanar1/none3；实际aligned census日志另报coplanar4。这两种分类口径都原样保留，没有合并改写。0.000000来自std::to_string对小容差的十进制舍入；源31211定义tolClose=1e-10*c.H*c.H，不能解释成零阈值。本诊断未重算未导出的胞面/点表，未声称已定位其全部缺面成因。

案例当前scratch/constant/polyMesh目录为空，Mesh/constant/polyMesh不存在，任何quality/checkMesh/execution receipt均未生成。run_meshgen_pipeline在8241调用失败，8255 assemble、8328小网格floor、8262checkMesh全部在后续不可达路径。S7 kept.leaves1336不是已写出最终nCells；不能由它判断未来floor一定过门。

## P2：真实诊断报告路径与结构错配

runner8217把report设为scratch/dc_report.json；8239用于CLI --report。CLI1698–1712把这个路径写成 **dc17-ingest-mapping/v1**，字段是schema/valid/counts/domain/keepPoints/solids等。实际该报告有效，22solid全部映射，无unexplained，domain=(-.25,-.25,-.25)..(1.25,1.25,1.25)，seed=(.173,.113,.173)，hLocal=.125，baseLevel3/maxLevel6。

真正的S7生成器报告由CLI558–569写到exportDir父目录即 **scratch/constant/dc_report.json**。其完整字段保留在case-original/Mesh/.meshgen_scratch/constant/dc_report.json：stageS7、mode dcMode-report-not-refusal、kind1cells4、nonManifoldShells1、Channel_supply_Back_patch有8nonManifoldEdges、0openEdges；后者是patch-shell普查，不单凭它推断胞457具体因果。mode字符串也不代表S10已通过。

runner8248实际传错report到8271–8293消费者；消费者期待stage/mode/shells/problemShells，所以原fullLog第174行确实显示stage=None/mode=None/shells=None/problemShells=0，第175行fullreport打印的是ingest JSON，掩盖另一路的真实几何报告。这是有真实正常路径证据的P2诊断缺口，不是native拒绝的数值原因。建议下一最小修复只在诊断层分离ingest与nativeS7路径，保留CLI --report映射，使用out_poly.parent/dc_report.json消费实际S7；若S7尚不存在则明确阶段缺失，不能把任意JSON当S7。保持native严格拒门，不能以日志修复声称几何已修好。此处没有改源或执行新增测试。

## 输入、STL batch与运行绑定

原model/Meshgen_Formal_Channel.cfdrun为20308B SHA9a6a99ffeef07b64ff078082ab119508fe0775faee64b800c1e1d4e996d2c43d。原schema4/runId run_2026-10-06T00:19:03.321Z、sourceProject.uid/schemaVersion5/contentHash e6a60c994992e5bb5e3bcb20ef12376cb9c9157dea1fbdadeb259ff9b6bc6f83/filePath/exportedAt完整保留。正常实际启动expectedSHA与Owned input snapshot日志都匹配原cfdrun字节，而不是重encode后的JSON。原保存物理project37473B SHA34f8807faed3c9472c2abf9bcb4dd8ee5397a39b2b50cd255902f6edce2a1cc5也保存；本审查没有将rawfileSHA混称canonical sourceProject.contentHash，后者作为原声明值记录。

独立stdlib读取四原triSurface文件，用已冻结helper89b71所示v1 framing重新计算batch SHA87e20ad7866eebca37074b782d43e80a557b9106b4f9764183930505d092a906，匹配启动expected-stl-batch与Owned日志。未import或调用runner/生产helper；文件名/长度/各contentSHA与整体框架保存在input-and-report-observations.json。实际ingest与桥接日志识别22patches/44tris、角色冲突0，seed/domain正常，已经抵达S10。

正式候选文件SHA204841e6043848075f18daf1669d45d01ca10c584e06df23bc8795f1830e4e8f/3967000B保持。保留ROOT startup script pin/TASKSET0及其control文件。实际native快速进程观察为空，未独立捕获/proc/exe、mapped ELF或F_GET_SEALS；当前磁盘候选SHA、启动命令和/proc/runnerPID/fd字符串不等于实测mapped binary或memfd seals。源中存在sealed carrier也不升级此运行的观察范围。

## 浏览器原证据与保全

ROOT用新空工程保存MeshgenFormal_BrowserProof_Only.cfdproj；本审查原字节复制其507437B SHAa73644f74abc5543ca6eb9b9da344f1de90da5273fc5942865538d5d0a7e5e55，从meta.description精确JSON解码为原字符串值，不reencode description、fullLog、AX或base64字符串。两JPEG仅base64解码，无编辑；原始base64也保留。实际脚本URL是index-o3jJDEnS.js，console errors=[]，原AX含终态及控件。未由本审查操作浏览器、访问API或重新保存工程，也不单凭日志/AX声称GPU或图像物理准确。

原15运行案例文件与后来新增两个ProofOnly文档分开记录：MeshgenFormal_BrowserProof_Only及MaterialRecipe_BrowserProof_Only都是证据载体，均禁止作为物理输入。当前17文件及8完整源码前后SHA/size/mode一致，源/案例未修改。server-launch.log是正常持续追加的文件，保留观察时snapshot及原startup658B前缀匹配；未把当前增长日志强行等同旧startup manifest。所有证据和新目录仅IT内，CPU32–35/CUDA空，没有mesh/checkMesh/solver/tests/build/service/UI/API/Git或产品源码修改。
