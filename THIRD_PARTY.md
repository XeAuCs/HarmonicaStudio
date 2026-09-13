# 第三方与来源

- **1.2.0 本地曲库，共 7 首**：《欢乐颂》《小星星》《友谊地久天长》《绿袖子》《奇异恩典》《斯卡布罗集市》《春日影》。下载链接、文件校验、署名与转换结果见 `samples/来源说明.md` 和可选的 `samples/catalog.json`。这些曲谱不属于本项目代码的 MIT 授权；新增自己的 MIDI 无需编辑目录文件。
- **《春日影》曲谱**：来自 [Online Sequencer #4368404](https://onlinesequencer.net/4368404) 的网友钢琴曲谱，页面未署编配者姓名。下载的 `春日影.sequence` 原件保留在 `samples`；`春日影.mid` 是从该公开序列转换的标准 MIDI，不是官方 MIDI，也不是直接下载的 MIDI 文件。原始 1,490 个音符与转换 MIDI 已逐一回读核对；分轨依据、速度换算和 SHA-256 见 `samples/来源说明.md`。该编配未列明再分发许可，原曲与编配权利仍归各自权利人；公开软件发行时应移除曲谱或取得许可。
- **线稿口琴图标与四套主题**：本项目原创。矢量源文件为 `src/harmonica_studio/assets/studio.svg`，主题在 `src/harmonica_studio/theme.py`，随工程代码采用 MIT 授权。PNG 与 ICO 由项目脚本生成，没有采用第三方图标包。

- **示例 MIDI**：《欢乐颂》，作曲 Ludwig van Beethoven，编配 Andy Ralls，mfiles / Music Files Ltd。原文件 SHA-256 见样例导出报告。此编配文件不是按 MIT 授权；网站提供个人使用下载。
  - 页面：https://www.mfiles.co.uk/scores/beethoven-symphony9-4-ode-to-joy-bassoon-piano.htm
  - 文件：https://www.mfiles.co.uk/downloads/beethoven-symphony9-4-ode-to-joy-bassoon-piano.mid
  - 条款：https://www.mfiles.co.uk/about-mfiles.htm
- **AutoHotkey 2.0.28**：官方便携版，GPL-2.0。完整许可证在 `third_party/AutoHotkey/license.txt`，不属于本工程 MIT 授权。
  - 下载：https://www.autohotkey.com/download/ahk-v2.zip
  - 对应源码：https://github.com/AutoHotkey/AutoHotkey/tree/v2.0.28
- **PySide6 Essentials / Qt / Shiboken 6.11.2**：官方 Python 包，适用 LGPL/GPL/商业许可证。此包采用可替换的动态运行库，发行包保留 wheel 中的许可证文件；具体第三方许可请查看发行包中 `licenses`。
  - 许可证保存在 `third_party/licenses`（程序内另有 `_internal/third_party/licenses`）。
  - https://doc.qt.io/qtforpython-6/licenses.html
  - https://code.qt.io/cgit/pyside/pyside-setup.git/
  - https://download.qt.io/archive/qt/
- **Python**：Python Software Foundation 许可证，打包运行库保留其许可。
- **Segno 1.6.6**：离线二维码生成库，BSD-3-Clause；许可证保存在 `third_party/licenses/segno`。来源：https://pypi.org/project/segno/ 。
- **PyInstaller**：构建工具，GPL 与引导程序例外；本工程源码不会因此被改为 GPL。
  - https://pyinstaller.org/en/stable/license.html
- 口琴按键映射参考用户提供的项目：https://github.com/ChickenD233/harmonica-auto-player 。本工程由前一版自行编写的工具重构，未复制该仓库实现。
- 1.1.0 的谱曲交互设计参考用户提供的 https://github.com/DilemmaGX/df-harmonica ，借鉴音符拖拽编辑、定位试听和工程保存的思路；本工程相关实现自行编写。
- 游戏规则来源：https://deltaforce.garena.com/zh_tw/news/announcement/94ABXR

这里的源代码链接用于记录来源，不代表允许绕过游戏规则或音乐著作权要求。若公开再分发工程，请复核并满足各运行库和示例素材的再分发条件。
