好的，其次就是这样的，你看我现在的这个demo还是默认读取的是已经生成好的vtu文件来做渲染的对吧，但是实际上我的最终目的是为了做一个“实时AI 4 CFD求解数据中心流场的demo”，这里用到的AI模型就是我的~/mycode/Gen-FVGN-refine这个仓库下的模型，~/mycode/Gen-FVGN-refine/cases/_archive/GTC这里是模型的各个方案的缓存logger，目前我最想用来进行演示的版本是FronzenText的TransFVGN模型，最快速推演。 

具体的我想这样设计：
1.首先去分析TransFVGN这个AI模型的GTC数据集里面训练的数据范围的服务器功耗啊，流率啊什么的范围，然后把他们存入/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo/data/GTC_TransFVGN_FronzenText/dataset/meta.json这里，然后在/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo/data/GTC_TransFVGN_FronzenText/dataset这个路径下引用软连接连接到真正的GTC数据集
2. 在确定训练范围之后，这样你就能去设计我的OV前端点击选中一个server之后，可改变的功耗和流率范围（做成滑块），这样方便用户修改物理条件，然后为后续预测求解提供基础
3. 你应该设计一个AI预测的tab，平行于几何和后处理tab（目前被命名为了simulate，这是错误的）这个AI预测tab里面应该有几个功能：3.1. 给一个文件夹弹窗（类似几何的json选中那个）让用于去选中可载入的AI模型logger文件夹，具体来说，就是选中/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo/data/GTC_TransFVGN_FronzenText这里。 然后3.2./mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo/data/GTC_TransFVGN_FronzenText这里面应该存储你从Gen-FVGN-refine这个仓库复制过来的源码，和推理启动脚本，以及模型权重文件，我帮你建好文件夹了，请你按照我的习惯来
3.3 AI 预测tab里面还应该给一个simulate按钮，用户点击之后，就是真正的后端要去把OV前端界面的参数转换为我的AI模型识别的边界条件文件（本质上应该是openfoam文件，然后转为h5格式，）这一个链条非常复杂，你要小心分析实现
4. 上述AI预测tab实现之后，/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo/data/GTC_TransFVGN_FronzenText/logger/inference_results这个路径下就应该自动出现vtu文件了，那么我的现在的后处理tab就不再给定一个vtu的读取界面和栏目了，变成一个“开启后处理”的按钮，只要用户切换到后处理tab并点击开启后处理，那么就展开什么ROI box啊，什么流线啊相关的界面设置，然后逻辑就和现在一样，用户可绘制温度场分布和速度场分布以及流线等等
 /browser-verify
 ---
 claude，我现在想请你帮我吧~/mycode/SCANO这个路径下最新的AI模型推理的代码集成进来，实现我在aircooling模式的稳态求解模式下，前端的求解tab里面可以切换一个子tab（AI推理/CFD求解）这样的设计。 具体的我想使用DCTS这个数据集（本质上是GTC2026这个case的参数化数据集）。但是这里有一些上下文想给你讲：
 1. 首先SCNAO模型的训练结果位于我的googledrive上，你可以通过40901上的rclone访问我的google drive 下载我在Jupiter超算上训练好的模型的checkpoint和相关使用文档。
 2. 现在比较棘手的点是：我需要你帮我实现一整套数据处理的pipeline，并且要和SCNAO的处理同步。什么意思呢，我想把我们这个功能做的很干净：即，我期望我们在前端任意设置边界条件，任意网格之后，然后数据能够自然顺利的流入模型的推理过程。（我目前脑子里想到的是STL+cfdrun---> openfoam case----> openfoam计算1时刻场----> npy/h5格式---->scano推理，这里你要注意npy/h5文件可以被后续修改，而无须反复从0生成，when BC changes） 并且你还要保证整个数据处理时间很通畅很快，而且理论上AI模型的输出还要和我们现在后处理所支持的openfoam/vtu模型保持一致，这样我们才能使用threejs渲染结果
 3. 顺便你还应该设计checkpoint放置的文件夹，scano的forward代码放哪里，我建议都先放/mnt/lv/home/litianyu/mycode/dcdtuni/src/backend/scano-Infra这里
 4. 其次就是ai推理 tab里面还应该支持一个“选择模型”功能，用户可以选择不同的SCANO模型checkpoint进行推理，这样可以支持不同的流场预测需求。