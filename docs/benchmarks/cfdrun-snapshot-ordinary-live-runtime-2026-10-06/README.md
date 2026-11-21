# 普通 RAM 快照：真实 React 小通道运行证据

本目录持久保留ROOT的普通React网格、瞬态求解和归档证据，以及独立只读复核。primary/是原883个声明payload逐字节副本，independent-runtime-review/是原27个声明payload逐字节副本；两目录manifest.json均为原始字节，原root路径字段未改写。publication-receipt.json逐项绑定源、目标、SHA、长度和模式。复制没有重新生成cfdrun、场或receipt，也没有重新运行验证脚本、求解、浏览器或服务。

原883包manifest SHA：2ce1c134255cd5397190f2c1818da472b5affe825ef6ea82a4cbcb7e1233b545；独立27包manifest SHA：d8677d3a958f02fa986ef6c49216affafe3b09a7b905d75137935cc32a339557。全部原声明文件在复制前后均重新匹配原SHA/长度。原包保持不变。

## 实际运行结论与边界

ROOT正常IT React index-Cv1ZvInu.js实际完成独立RamSnapshot_Archive_CUA工程的mesh与两次普通瞬态solve。严格完整-allTopology -allGeometry checkMesh报告Mesh OK，511cells；第一次从0到0.02s两步，第二次修改同工程dt为0.005后四步至0.02s，两者TRANSIENT_COMPLETED、rc0。实际启动expected cfdrun SHA、owned snapshot日志、最初publication捕获和各自run/model归档字节一致。第一次完整194file run归档在第二次publication之后仍逐SHA一致。原日志、全场、完整两case拷贝与JPEG均在primary/；独立复核逐文件和实际日志绑定在independent-runtime-review/。细节见[原运行说明](primary/README.md)和[独立复核](independent-runtime-review/README.md)。

这是零负载合成小通道的普通RAM输入、质量门、瞬态完成与归档生命周期验证。它没有验收完整Shinaide风冷求解、任意case/engine、hybrid或取消流程，也没有新增MMS、可微分性/AD、科学精度、GPU/Kit或native memfd seals证明。快进程观察未捕获native/fd，空观察不能推断进程从未存在。browser-proof-only.cfdproj仅承载原浏览器证据，禁止当物理输入。

## 版本控制候选

FILES.txt是本新目录的精确文件清单。GIT_CANDIDATES.txt与GIT_IGNORED.txt由只读git check-ignore --no-index按当前规则分类，未stage/commit。被忽略的原日志也作为本地持久证据完整复制、纳入本manifest；源原包、RED与既有源码/unit证据未改写。忽略文件不能仅靠普通git add目录带入，ROOT提交时须按清单决定其显式证据保全。此整理不编辑HANDOFF或产品源。
