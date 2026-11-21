我现在请你帮我规划一个改动思路：
1. OV的demo读取cfdproj是必须的
2. 为现有的cfdproj定义或新增USD资产是必须的，但是这里我觉得你i是不是可以首先拆分现有的USD资产为小块，把他们变成主要的“材质与纹理”的库，然后放置进入“/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo/USDresource” 这里
对于真正的cfdproj里面定义的几何大小和尺寸，由OV读取cfproj的时候自动生成真实的对应的USD文件然后缓存存入/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo/kit/cache这里，然后再在OV里面显示？
---
不行，你没理解我的ROI得意思。 请你从底层完全重构ROI得含义：
1. 本质上这个仓库得作用是drag & sim: 即我们只想simulate ROI，然后也只加载读取并“可视化”ROI区域得内容。 这应该是算法底层决定得。
2. 目前来看我的思路似乎还是全局读取VTU或者ai推理的时候也是直接传输得全场得VTU过来，这是不对的，其实这是违背我们的愿意得。
3. 我来给你规划一下这个思路，这个思路应该是来自我们得论文SCANO得做法：
3.1. 当一个case得网格由snapphex生成好之后，相当于我们获得了一个全局不变得 cell数组（cpd_cell： 边界face+内部cell），对于cpd_cell来说，用户的ROI（假如现在是部分ROI），一定是能够在cpd_cell中“切出一块区域的”，那么我现在想的是，能不能我们设计一个缓存数据结构，这个数据结构能够支持只输出ROI中的vtu文件，交给OV来渲染和处理。
3.2 更甚一步，能不能，深入kit-cae得逻辑，把drag &sim 做的更加高效和流场：
具体来说： 我们的这个缓存数据对象，应该支持多次扩展得能力， 即随着用户改变ROI得位置和大小，其会被反复填充，直到完全填满cpd_cell得所有物理值，这里不应该产生重复计算（注意，第三点这部分是针对AI simulate这个功能来说的，因为SCANO这篇文章得核心思想，就是恢复“采样“导致得边界消失从而实现可以仅模拟物理空间内部分区域，或者多张卡各自模拟部分区域后TTA得思想，而这个采样就对应我们这里的ROI，只不过ROI是直接给定一整块区域而已）
3.3 回到如果我们不是AI推理的模式，如果是直接读取openfoam得结果，那么这个时候，我觉得为了处理方便，就可以保留我们现在得做法，即： 如果是读取vtu, ROI得作用就回退为现在得这个”只渲染/裁切出“ROI区域得内容。 
