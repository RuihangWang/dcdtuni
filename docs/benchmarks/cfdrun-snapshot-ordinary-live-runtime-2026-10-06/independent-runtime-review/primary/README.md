# 普通 RAM 快照的真实 React 瞬态归档验证

ROOT 在 Windows 内置浏览器新页面加载实际 index-Cv1ZvInu.js，经正常服务器文件加载、创建独立 RamSnapshot_Archive_CUA、SaveAs、mesh 和 solve。原 channel 工程保留。511 单元严格完整 checkMesh Mesh OK，job27c7bfb4，网格入口与求解入口遵守质量门。

第一普通瞬态 jobd2424c7e：实际启动 expected cfdrun SHA bd0c082213534b2eaa80d03925a5bd39002674c128dcdab7d70486e31b8aac07，19585B；published raw、首次原始副本与实际 run/model/RamSnapshot_Archive.cfdrun 逐字节相同。SIMPLE 初始化后 PIMPLE 两步0.01/0.02，18/15外迭代收敛，TRANSIENT_COMPLETED、rc0。

第二次通过界面设置从0起、dt/maxdt/writeInterval 0.005，保存同名工程并正常solve，expected与新publication/新runarchive SHA b7a63e2ed74af7341df81ceff8c7d60fa3ad7503f944d42ccc624a8f58111ddb，19587B。实际四步至0.02、无外迭代触顶、TRANSIENT_COMPLETED、rc0。原第一运行整个194file归档在第二次publication与运行后全部原size/SHA不变。当前入口恢复、abort消失、post可用，独立页面console errors=[]。

复制保留 first-solve-complete 全307files、second-solve-complete 全550files及全部实际输入、质量receipt、原始求解日志、convergence/results/field。browser-proof-only.cfdproj 是正常保存的空证据工程，禁止作为物理输入；其描述原JSON、AX、完整DOM日志、两张未编辑JPEG已提取。首次只读进程观察未赶上很快的native，未实测memfd seals，不把该观察作进程从未存在的证据。真实预期SHA和Owned input snapshot日志与原始文件归档构成此运行绑定。

这是合成零负载小通道的归档与生命周期验收，不代表Shinaide风冷求解或校准预测通过；该大工程严格Failed2仍阻断。源码/unit已另审查与v4普通归档窄修绑定，此包不扩大单元计数或声称稳态科学验证。源runner458364B SHAff868797、正常UIentry64199fb5已捕获；库后续typeddiagnostic构建在这些普通native任务全部结束后开展。
