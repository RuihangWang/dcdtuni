真实一次BASE/AXIS结果已由Root执行完成，本子代理没有重复APP/checker/build。两组源同为reviewed PRIVATE F3 APP46a3ca…/LIBa68b8c…，实际LD_DEBUG calling-init路径匹配。110s/独立120s watchdog、原stock -allTopology -allGeometry、serial/noCUDA、6legacy+physical1+observer1相同，唯一native环境差异为三kernel变量。Root授权与实际启动/状态/raw日志均保留；executed-plan MANIFEST SHA5fb38c7f…是先批准的不可变计划，不是后补批准。

BASE native Total .283438s，stock Failed3，D13/W8/VR2；AXIS .259776s，moved4、Pareto veto2、screened bad parent5、evaluated65，stock Failed1，仅D4。两组native/checker均rc0且未超时，仍strict RED/strictAccepted=false，不能正常部署为合格网格，也不能推广完整Shinaide、收敛或5min验收。这两次小例时间不是配对性能基准；冷启动/日志/系统抖动未分离。

独立pure offline sets重读（stock-set-replay.json）与Root parser完全一致：D移除9个五面五点pyramids，其中8个nearcorner组+另2987，剩最终written1533/1535/1536/1547四tet；N集合18→3、W8→0、VR2→0；所有added集合为空。完整stock End与真实sets互证，S两次无skewFaces、Max skew2.52654→1.25且OK。faces/owner/neighbour/boundary四项及cell/face/pointZones三项跨组byte exact，只有points改变；因此written labels的集合子集比较合法。这里是无新增stock失败标签，不是所有数值不变或平均值都改善（例如平均非正交角16.469→16.9611）；native/captured/pre-cone IDs仍未等同最终written IDs。

12原输入从批准计划input逐字保留。本次writer只更新sourceRegions实际patchRanges，独立metadata replay验证schema/UID/region/facet inventory/roles完全一致，startFace/nFaces逐一等于真实最终boundary；另外11输入前后exact。原8mesh文件在checker前后以及当前读取exact，所有scientific mesh/实际sets完整复制，无修改结果或丢弃坏cells。未来读包不要把可变原case当未运行input，原12在input/，最终case在cases/。

physical正常链两组capture1/stage0/commit0，旧/新D3/3未严格减count；observer BASE rows16/AXIS6是过程变化，记录与uint64原bits原样保留。不能拿该capture compact/native点号映射到四tet共用final点687。完整当前source39/Main29/normalpair2/I2229/privateF3source39/4served文件与8PID/uid/cwd/start/argvhash，actual before/after/latest指纹完全相等；Root7 UI与ecMzprefix是此次正常baseline。guards仅证明这些检查时刻的身份，不证明Kit内存、媒体或物理收敛。

冻结内容：runtime完整34raw+status/once/auth/guard文件，两组全部24/22case文件（包括全部mesh/sets），原12input、实际执行plan/source-block/guards、source/load/build引用与Root原stdout/status/独立setsproof，加本次离线复核。没有嵌套旧大build包/实际ELF副本；ELF全SHA、calling-init真实日志与原reviewed immutable build manifest是精确外部绑定。REPLAY_SETS.py可仅只读重算这些stock集合/拓扑，stdout不赋新acceptance。interior-star另小包调查，不以未完成分析替代本次strict RED。
