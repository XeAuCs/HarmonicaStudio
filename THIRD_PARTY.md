# 第三方组件与来源

本工程代码及原创线稿口琴图标采用 MIT 许可证。以下组件使用各自许可证。

- **AutoHotkey v2**：GPL-2.0。本仓库保留 third_party/AutoHotkey/license.txt，不含可执行程序。本地验证版本 2.0.28；[官方项目](https://github.com/AutoHotkey/AutoHotkey)、[对应源码](https://github.com/AutoHotkey/AutoHotkey/tree/v2.0.28)、[发布页](https://github.com/AutoHotkey/AutoHotkey/releases/tag/v2.0.28)。下载 v2 x64 运行文件后放到 third_party/AutoHotkey/AutoHotkey64.exe。
- **PySide6 Essentials / Qt / Shiboken 6.11.2**：LGPL/GPL/商业许可证。使用动态运行库，打包脚本收集安装包内许可证。见 [Qt for Python 许可证](https://doc.qt.io/qtforpython-6/licenses.html) 与 [源码](https://code.qt.io/cgit/pyside/pyside-setup.git/)。
- **Python**：Python Software Foundation 许可证；打包运行库时保留许可。
- **Segno 1.6.6**：BSD-3-Clause，离线二维码库。[项目主页](https://pypi.org/project/segno/)。
- **PyInstaller**：GPL 与引导程序例外。[许可证说明](https://pyinstaller.org/en/stable/license.html)。
- **设计参考**：按键映射参考 [harmonica-auto-player](https://github.com/ChickenD233/harmonica-auto-player)，谱曲交互参考 [df-harmonica](https://github.com/DilemmaGX/df-harmonica)。本工程实现自行编写，未复制上述仓库代码。

仓库不包含下载的 MIDI、原谱图片或外部编配。用户添加的音乐和编配权利归相应权利人。制作发行包时应检查运行库和附带曲谱的分发条件。
