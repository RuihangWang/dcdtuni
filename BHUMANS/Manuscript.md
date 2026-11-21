> 状态注记（2026-10-21，非所有者原文）：下方原文不改。文中路径已经变化：`paintToDC1` 数据现为匿名算例 `docs/manuscript/paper_experiment/case_00_sketch`，人工建好的工程在 `case/<name>/`，数据集在 `docs/manuscript/paper_experiment/`。这篇论文已被 AIFLUIDs 2026 录用，正文由 `docs/manuscript/paper_build/gen_docxml.py` 生成；自动 CFD 工作流的技能在 `.claude/commands/dc-*.md`，跑网格与求解用脚本化 API（`python -m backend.api`）。

你还记得我文章的思路吗，我现在需要你帮我造数据集，即你还记得我的故事线吗。 在/mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript/paper_experiment/paintToDC1这里是我的一个测试agent的一个case。其中/mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript/paper_experiment/paintToDC1/input是我需要输入给模型的提示词和“手绘/粗绘”的数据中心2D平面截图。 而/mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript/paper_experiment/paintToDC1/label这个是agent建造的结果。

本质上我的论文是想验证“自动CFD”工作流对吧。因此你需要帮我做两件事：
1. 想办法使用Skill-creator来生成 自动化CFD 工作流的各个流程。 例如，拿到用户prompt之后，要判断缺什么，还需要哪些内容，数据中心尺寸是否给定，等等，先去询问用户。 然后调研本机的cfdproj文件的schema，根据schema来生成符合用户需求的cfdproj文件。
2. 拿到文件之后，通过api的方式，触发网格生成，CFD计算，结果分析等等流程（其中结果分析，省去流程绘制，先只用分析ACU return 面的温度是否平衡（定常收敛条件）

3. 在上述skill生成之后，去调用测试一下，就以/mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript/paper_experiment/paintToDC1这个case为例，反复打磨你的skill直到生成合理的skill

4. 在这个skill生成之后（这个skill和本仓库的代码构建方式“脚本定义工程+暴露原生api个agent” 是我们的核心创新点，这也是区分于foamAgent，openfoamGPT的核心卖点） 去帮我完善的生成数据集放到这里。

5. 你可以反向制作数据集，/mnt/lv/litianyu/mycode/dcdtuni/src/frontend/projects这里有很多人类已经帮你造好的cfdproj的case，你需要做的则是使用browser-use，一个一个的去2D平面设计模式截图，作为粗稿，然后生成对应的server U位，功耗，ACU 送风分量，流率等等的说明文件。把造好的数据集整理放到/mnt/lv/litianyu/mycode/dcdtuni/paper_experiment这里。