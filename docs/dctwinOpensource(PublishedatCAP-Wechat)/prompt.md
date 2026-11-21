请问你还记我的dctwin的开源计划嘛，我现在需要撰写一份微信公众号的中文文章。然后呢/mnt/lv/litianyu/mycode/dctwin/docs/dctwinOpensource/Dctwin.pptx这个PPT我主要总结了我们这个软件的几个卖点，然后/mnt/lv/litianyu/mycode/dctwin/docs/dctwinOpensource这个文件夹里面有一些软件截图和折线图对比结果。 你现在有以下几个任务：

1. 请你修改/mnt/lv/litianyu/mycode/dctwin/docs/dctwinOpensource/0pythonFigure
   /mnt/lv/litianyu/mycode/dctwin/docs/dctwinOpensource/FigureFine这两处的绘图代码，完全重绘这两幅图，保证两幅图各自都只有 OF，6sigma，和sensor的结果，保证两幅图的风格统一，X和Y轴的数字和ticks间距合适，不要有重叠
2. 根据docs/dctwinOpensource/Dctwin.pptx这个里面关于我们这个数据中心CFD软件的特性来整理一篇微信推文的故事线，然后引用文件夹里面的图片，例如软件操作截图，结果对比图等等。
3. 然后是我们的软件亮点：
4. 关于AI agent驱动这件事，我想说明的核心逻辑就是我们的软件前端CAD建模部分完全是由cfdproj这样的配置文件来决定的，因此Agent可以根据我们给定SKILLs了解本软件的工程文件定义schema，然后通过创建/编辑工程文件+Browser-use等工具反馈debug来自动生成数据中心算例，这种方法具有巨大潜力。而本功能的前身是本组的“/mnt/lv/litianyu/mycode/dctwin/docs/dctwinOpensource/Ref” 这个路径下的两篇工作。
5. 其次就是基于AI的快速Drag and 	Sim的推理，具体的你还记得40901这个ssh服务器嘛（HostName 40901.dynip.ntu.edu.sg  ， User litianyu，可以直接连无需密码），在40901这个ssh服务器的/mnt/lv/home/litianyu/mycode/Gen-FVGN-refine/docs/DragandSim/tex这个路径下，我已经完成了drag and sim的大致性能测试，你可以简单Ref一些图过来，说明我们这个软件支持基于AI的快速推理
6. 最后是最重要的，请你引用我们的git仓库截图，给出git仓库地址，然后把文章以markdown格式写到/mnt/lv/litianyu/mycode/dctwin/docs/dctwinOpensource这个路径下，使用中文。 /braingstorming
7.
