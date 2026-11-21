# 真实 GUI 复现操作口径

只在已授权、正确的 React 服务上操作；本文件不授权启动、重启、写 API 或接管别人浏览器。每次沿真实 UI 加载完整匹配项目，保留当前 source/config/run identity。

1. 先准备四个**可写、各自拥有输出的 case 目录**，优先与保存项目的 meta.casePath 对应。将 evidence/gui/saved-project-{2p0,1p0,0p5,0p25}.cfdproj 原字节复制到相应 own caseDir；不要从 frozen 或 docs/benchmarks evidence 目录直接加载后 Prepare。
2. 用左侧 Load project 的真实远程文件浏览器，从该 writable own caseDir 加载复制件。远程加载实际执行 session.setCaseDir(dirname(remotePath))，不按 meta.casePath 自动选择输出。确认正常 session caseDir 与此目录相同；修改 provenance/UID/casePath 等内容产生新 canonical SHA，须真实导出/保存后重新记录，不能粘贴旧 SHA。
3. 确认 liquid transient、8 s、匹配 dt、PyTorch CPU float64/2线程、consistent、manual、无 load step、mock-1-dae-v3、pump/valve/heat均1，primary 1 kg/s/293.15 K、HX 双侧 .02 m³。不要 re-adopt mock、改 uniform/steady 初值或只改 dt；MMS 表格及 explicit初值/导数必须保留。
4. 用正常 Export input/Prepare，记录 download 原bytes、source_project SHA、config hash、run_id。要求 Ready、t0、frame0且无错误。input.json 拆 liquidrun 后比较，不把 wrapper 与裸 manifest 混比。
5. 不在线改 control，真实点击 Start，要求 Completed/t8/5、9、17、33帧。保存真实像素/console 回执；确认本 run 六持久化 artifact 在 own caseDir/Liquid/log/run-<id>。取消/暂停不是完成。
6. 用真实 Result/Save checkpoint/Export accepted input 下载并保留原 bytes。checkpoint v3 保留 full original输入，accepted表为实际完成前缀；每 run 的 runtime、source与accepted hash分别核验。不得将私人 solver frame复制到 normal logs。
7. 四项目分别加载。新8s MMS不应冒用历史60s analytic overlay身份；缺旧 overlay是预期。这里的 equation heat含人工源，不作无forcing实物热平衡/厂商标定宣称。

原私人 runbook直接从FROZEN/results加载的 P1输出风险已识别；Root真实四GUI采用 own directory原字节复制流程处置。原runbook留在 evidence/product供审计，不作为操作指令。checkpoint/本次GUI不代表OV像素、风冷strict或整体goal完成。
