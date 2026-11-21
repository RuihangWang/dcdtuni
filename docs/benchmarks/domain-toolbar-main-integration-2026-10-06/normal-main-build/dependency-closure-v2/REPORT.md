# MAIN RAM dependency closure v2：source GO

补齐accepted reader7e996db…23506B/base snapshot testf464152…6667B后，MAIN最新owned Snapshotdf296…7065B完整保留，不覆盖回45a92早期类。原Base_leakage fixture71439B/300bc5…已存在。原ROOT pending cherry baseline包括全部严格RAM batch/CaseLock/sourceSTL/quality/execution/归档/传输及MeshGenerator诊断修复。

Main140 stage候选：138 exact与IT一致，两差异明确有意保留：stlgenMAINcc4c9def…456082B排除IT61条c7 sourceauthority WIP（IT8fd239…459056B）；MAIN runner test1f1f710…31386B仅将一侧线attribute断言替换为现有_source_stls_verified True，并删另一侧线receiptNone断言，保verified rawSHA及真实STL篡改content hash拒门。整个test字节按两处精确replace验证一致，没有其他改动。RAM+诊断单独22paths口径为20equal+2；reader和base test两额外acceptedfoundation不是140 stage原集合，已精准补齐。

MAIN原openfoam_casecacf42d…24518B及point/weight authority缺席保持；不给native源侧线开权限，不引入IT WIP全局材料resolver或契约模块。旧nativewriter/sourceauthority行为与新RAM功能接口可共存。原before143source/rootHASH记录、原raw148PASS/1FAIL完整保留；v1后复制test碰到ROOT并发修改而实际记录绿版1f1f，已用v2逐字原c970源和correction.json澄清，不把误命名文件当原RED。原c970SHA已与原测试前/后record及IT/root before绑定相同。

Root两测试断言修改后授权同11file一次复验：**149 PASS，rc0，pytest7.27s、外层8.648s**。前后Main144文件（140stage sources+reader+base test+oldopenfoam_case+HANDOFF_ACCOUNT）以及IT140 SHA/长度无变化。第一次v2 preflight错用不存在B/HANDOFF.md，未启动pytest，错误记录保留；实际handoff路径是docs/developer/HANDOFF_ACCOUNT_2026-10-06.md。没有重跑原RED或扩大scope。

结论：ROOT可继续MAIN cherry/source提交。这里只有source+受控149cases验收，未证明MAIN后端已重启、实际mesh/solve/UI或原生sourceauthority能力。正常MAIN frontend build仍HOLD，需要ROOT新的HEAD、正式IT重启后的实际6PID/flags/listeners及明确BUILD GO；不依赖旧IT PID作未来controls。

本代理未Git/source mutation、build、mesh/checkMesh/solve/native、browser/API/服务操作。测试stub子进程和临时输入都在B-owned目录，CPU32–35/CUDA空，cacheprovider/bytecode关闭。v1原包manifest7c8332ba…不覆盖。
