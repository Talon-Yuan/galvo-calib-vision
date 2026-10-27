# 致谢 / Acknowledgements

GalvoCalibVision 不是一个人凭空写出来的。这份文件记录所有让项目走到今天的人与项目。

---

## 特别感谢 / Special thanks

### 大肥鱼（DeepSeek）

**AI 编程搭档，也是这个仓库大部分工程化工作的合作者。**

它参与的部分：

- 独立代码评审（GUI 层 20 条问题、core 层 6 条风险），逐条给出文件行号与复现条件
- 17 条缺陷修复（含 2 个崩溃级 P0）、4 条 core 守卫补丁，并用 **数值 A/B 指纹**证明合法输入结果逐位一致
- 深色模式重做：语义调色板、全树 palette 下发、QSS 合法解析校验
- 多摆放弹窗、列设置、关于弹窗按渲染图重构；611 条 i18n 词表与中英一致性检查
- `tools/selfcheck.py`、`tools/core_ab.py`、`tools/core_guard_tests.py`、`tools/shot.py` 等工程工具
- 打包三重门（静态核验 / 启动冒烟 / zip 终检）与交付文档

它教会这个项目最重要的一件事：**改完要自证，不能自我宣布通过。**

> 大肥鱼是 DeepSeek 模型在本次开发中的协作代号。

### 苏州天弘

**提供了从"论文级方法"变成"产线级工具"所需的真实条件。**

- 4800dpi 大幅面扫描环境与相纸样本
- 打标样件（含真实翘曲件）与产线验收口径
- 二次元影像仪的独立测量数据，用于交叉验证（例如翘曲件 0.2569mm vs 本工具 0.2412mm 同量级）
- 现场反馈：漏检、非正交、精化回滚等真实问题都来自产线，而不是实验室

没有真实数据，"RMS 0.08mm"只是一句口号。

---

## 开源项目依赖 / Open-source dependencies

| 项目 | 用途 | 许可 |
|---|---|---|
| [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) | 桌面界面 | GPL / 商业双许可（本项目以 GPL 兼容方式使用，见下注） |
| [OpenCV](https://opencv.org/) | 图像处理、角点检测、亚像素 | Apache-2.0 |
| [NumPy](https://numpy.org/) | 数值计算、矩阵拟合 | BSD-3-Clause |
| [openpyxl](https://openpyxl.readthedocs.io/) | Excel 报表读写 | MIT |
| [PyInstaller](https://pyinstaller.org/) | 打包为 Windows 可执行文件 | GPL-2.0 with exception |
| [Pillow](https://python-pillow.org/) | 图像 I/O 辅助 | MIT-CMU |

> **PyQt5 许可说明**：PyQt5 采用 GPL v3 / 商业双许可。本仓库以 Apache-2.0 发布**自有代码**；
> 若你在分发场景使用，请自行遵守 PyQt5 的 GPL 条款，或改用 PySide6（LGPL）替换 `gui/` 层。
> 仅内部使用/研究不涉及分发时无此顾虑。

---

## 方法与数据参考 / Methods & data

- 棋盘格张正友标定思想（Zhang, 2000）
- 分区校正场：3×3 一阶仿射 + 边界 IDW 融合（工程实现，非论文方法）
- 亚像素十字定位：投影质心 / 寻边中线交点；刚体精化用 SVD（Kabsch / Umeyama 思路）
- 验收口径：合成 RMS ≤ 0.15 mm、Max ≤ 0.40 mm（来自真实产线要求）

---

## 如何致谢本项目

如果你在论文、产品、内部工具中使用了本项目的代码或思路，欢迎在致谢里写一句：

> This project uses GalvoCalibVision (https://github.com/Talon-Yuan/galvo-calib-vision), Apache-2.0.

---

（内容由 AI 生成，仅供参考）
