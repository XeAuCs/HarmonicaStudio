# 口琴工坊 · Harmonica Studio

Windows 上的 MIDI 口琴工具：主旋律提取、单音简化、可视化修谱、电脑试听、演奏脚本导出，以及局域网手机遥控。

当前版本 **1.3.7**。此仓库提供源码，初始曲库为空；本机下载的歌曲、个人工程、导出记录和安装成品不包含在仓库中。

## 功能

- 读取 MIDI 音轨、声部和速度变化，推荐主旋律，简化为单音。
- 速度、移调与自动八度调整，38 个音高的口琴按键模型。
- 钢琴卷帘编辑：拖动、调整时长、添加删除、撤销重做，保存独立的 .hstudio 工程。
- 电脑试听、暂停、定位及居中滚动的播放线。
- 默认将音符之间超过 3 秒的空白缩短为 0.6 秒，保留长音和正常停顿，可在设置中关闭。
- 自动扫描曲库、四套主题、精简模式和圆角渐变二维码。
- 手机扫码选歌，控制电脑试听或游戏演奏；同一局域网内使用，无需云服务器。

## 从源码运行

需要 Windows 10（1903 或更新）/11 x64、Python 3.10 或更新版本。在仓库根目录打开 PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python launch.py gui
```

从「打开 MIDI」选择曲谱，或将自己的 .mid / .midi / .kar / .rmi 放进 samples。只扫描当前这一层，不扫描子文件夹；「设置」也可指定其他曲库目录。

完整界面先选声部并生成，再编辑或试听；精简模式选歌后自动生成。电脑点击「手机遥控」，用同一局域网中的手机浏览器扫码连接。电脑必须保持运行，声音从电脑或游戏发出。

## 游戏演奏

编辑和电脑试听无需 AutoHotkey。游戏演奏需自行获取官方 **AutoHotkey v2 x64**，将 AutoHotkey64.exe 放在：

```text
third_party/AutoHotkey/AutoHotkey64.exe
```

本地验证版本为 2.0.28，官方来源见 [第三方说明](THIRD_PARTY.md)。没有此文件时，点击演奏会提示缺少运行文件。

点击「演奏」，切到游戏口琴界面后按 F6，3 秒后开始；F6 停止归零，F8 退出，窗口失焦会停止。手机「游戏演奏」沿用同一演奏器。软件使用普通模拟按键；实际游戏兼容性尚未保证，不提供反检测功能。

## 数据与导出

每次生成独立目录，包含试听 WAV、单旋律 MIDI、AHK 脚本、按键时间表、音符表、转换报告和工程文件。试听与脚本共享演奏时间安排。

原工程保留音符位置；「跳过长空白」只影响派生播放结果。图上刻度使用原谱时间，进度条使用实际时长；反复导出不会累积缩短。

配置、暂存、日志和导出默认保存到 data；环境变量 HARMONICA_STUDIO_HOME 可指定其他数据目录。.gitignore 排除了私人数据、曲库文件、可执行程序和验证截图。

## 测试

下面 8 个模块共 107 项测试，不依赖外部曲谱或真实 AutoHotkey，界面测试需要已安装 PySide6：

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD\tests"
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python -B -m unittest test_transport test_script_player test_remote test_project test_playback test_long_rests test_library_preferences test_editor -v
```

scripts/test.ps1 的完整回归含依赖本机示例曲库的测试；--self-test 成品检查还需要原有示例和 AutoHotkey。音乐文件不在仓库中，干净下载本仓库时请先使用上述独立测试集。

## 打包

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
.\scripts\build.ps1 -Python .venv\Scripts\python.exe
```

生成目录为 app/HarmonicaStudio。需要游戏演奏时，打包前补齐 AutoHotkey 运行文件；打包脚本会收集依赖许可证。打包前检查 samples，只加入允许随程序分发的曲谱。

## 目录与许可

```text
src/harmonica_studio/   程序源码、界面、手机页面和图标
tests/                 单元与集成测试
scripts/               测试、图标、打包和快捷方式工具
samples/               用户自行添加的曲库
third_party/           第三方运行文件位置和许可证
```

代码和原创图标使用 MIT 许可证。第三方运行库使用各自许可证，音乐及其编配不属于本项目 MIT 授权。详见 [LICENSE](LICENSE) 和 [THIRD_PARTY.md](THIRD_PARTY.md)。
