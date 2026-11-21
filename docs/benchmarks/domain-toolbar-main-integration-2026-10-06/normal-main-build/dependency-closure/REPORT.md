# MAIN RAM dependency closure：受控只读复核 v1

ROOT合并处理中补 accepted reader7e996db…23506B 和 base snapshot testf464152…6667B 后，Main当前owned Snapshot仍df296…7065B，与IT最新SHA一致，未退回早期45基础版。原52BC fixture71439B/300bc5…已在MAIN，未增case。

RAM+diagnostic22候选中21个与IT逐SHA相同；stlgen MAINcc4c9def…与IT8fd239…唯一差异为IT sourceauthority新增61行，MAIN无额外新增/删除（反向diff删除0行）。3段涉及preflight sticky `_point_repair_source_receipt`、native-role contract producer/import、写入字典的两个block。这些61行及point_repair_source_contract.py、weight_union_source_contract.py和openfoam_case的resolve_global_air_material WIP均刻意不移植，保MAIN原writer/sourceauthority行为。严格RAM/STLbatch/CaseLock/归档/receipt以及MeshGen报告路径改动仍在Main。

reader旧signature缺input_snapshot会拒绝RAM调用；现在exact23-line seam已在MAIN，所有backend绝对import在本22文件AST中有真实模块文件，无缺依赖。捕获Main143来源（140 stage sources+reader+basetest+openfoam_case）与IT140 source前后SHA完全一致；本次没有Git/source mutation。直接读取HEAD/ref/index作观察，没有运行Git；ROOT正在cherry阶段，index状态不是最终commit验收。

ROOT明确授权的一次11-file controlled pytest：**148 PASS /1 FAIL**，22.90s pytest报告/26.997s外层计时。唯一原test第166行要求侧线 `_point_repair_source_receipt["rawSha256"]`，该属性来自被排除61行，因而AttributeError。此前第165行 `_verified_source_cfdrun_sha256 == snapshot.sha256` 已通过，日志已证明真实 original STL validator验证成功。原case后面仍写变更STL并要求真实SHA拒绝，未删。

建议ROOT在MAIN只将该侧线专属assert改为既有 `_source_stls_verified is True`，保原SHA及物理STL漂移拒门；它表达此RAM test的实际校验意图，不恢复生产WIP。本代理不改该test，不重跑其他测试，原source/raw已冻结。当前 build/cherry continuation建议 **HOLD此唯一测试兼容差异，补完后可source closure**；不是新增physics问题，不以148通过冒充全部通过。

测试只有现有受控Python fixtures及stub child；未mesh/checkMesh/solve、native编译、browser/API/服务、Git mutation或生产源改动。所有pytest basetemp/TMP在B-owned目录；CPU32–35/CUDA空，关闭bytecode/cacheprovider/自动插件。正规MAIN frontend build仍未启动，需ROOT新的HEAD和BUILD GO。
