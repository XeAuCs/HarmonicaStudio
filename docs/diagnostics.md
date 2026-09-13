# 控制层与界面性能诊断

诊断入口复用 `AppController` 的真实业务流程，使用确定性合成 MIDI、独立临时目录、无声播放器和不会发送按键的演奏器。无需下载曲谱、连接手机或启动游戏，也不会使用个人曲库和 `HARMONICA_STUDIO_HOME` 中的数据。

本文集中维护诊断命令、指标和接口；模块与线程设计见 [应用架构](architecture.md)，工作流程约束见 [协作指南](../AGENTS.md#性能诊断与-agent-测试)。

## 命令

在项目根目录运行：

```powershell
# 控制层：加载一万音符、八个声部，重复三次
.\.venv\Scripts\python.exe launch.py diagnose --scenario load --notes 10000 --repeat 3

# 实际 Qt 窗口：扫描一千个文件，每个文件一千音符
.\.venv\Scripts\python.exe launch.py diagnose --scenario library --files 1000 --notes 1000 --ui --report verification/diagnose-library.json

# 实际编辑器提交修改，等待 400 毫秒防抖和自动保存
.\.venv\Scripts\python.exe launch.py diagnose --scenario autosave --notes 10000 --ui --report verification/diagnose-autosave.json

# 完整导出并接收结果：分别观察后台执行、主线程结果安装与界面延迟
.\.venv\Scripts\python.exe launch.py diagnose --scenario export --notes 1000 --repeat 3 --ui --report verification/diagnose-export.json

# 向后台注入两秒延迟，在任务开始后停止并取消，检查不会自动播放
.\.venv\Scripts\python.exe launch.py diagnose --scenario cancel --worker-delay-ms 2000 --ui

# 在加载时测量事件循环，超过 100 毫秒延迟则返回失败
.\.venv\Scripts\python.exe launch.py diagnose --scenario load --notes 10000 --ui --max-ui-lag-ms 100 --timeout 60
```

`--ui` 创建独立的真实 Qt 窗口；默认使用系统显示后端。自动化环境可显式设置 `QT_QPA_PLATFORM=offscreen` 验证行为，但离屏数据不能代替实际桌面响应测量。无 `--ui` 时不会加载 Qt，只测控制层。

场景说明：

| 场景 | 实际执行与检查 |
| --- | --- |
| `load` | 后台解析合成多轨 MIDI 并计算排名，主线程安装声部，核对音符数量；Qt 模式还更新声部表格 |
| `library` | 后台扫描带有效 catalog 哈希的合成曲库，等待本次或合并后更新请求成功安装且扫描通道空闲，再核对文件数量；Qt 模式包含菜单重建 |
| `autosave` | 打开自包含工程后修改音符并自动保存，等待当前版本的暂存成功回执且保存队列空闲，核对落盘内容且不推进手动保存状态；Qt 模式通过编辑器提交并等待现有防抖定时器，不能仅以文件存在判断完成 |
| `cancel` | 请求生成试听，等待工作线程启动，停止试听并取消任务；检查不接收结果、不播放、不修改曲谱 |
| `export` | 从合成工程执行完整导出，等待结果安装及后续自动暂存完成，核对原谱、实际发声音符及试听加载，确认没有自动播放；包含真实 WAV 渲染，不使用人工延迟 |

`--worker-delay-ms` 仍只支持 `load`、`cancel`，后台等待可被取消唤醒，主线程不等待。曲库和保存使用独立任务通道，不接收此延迟参数。顺序测试可注入 `library_executor`、`save_executor`，复用 `ControlledExecutor`，不把人工等待计为真实性能。保存失败测试替换 `save_coordinator.save_project`，在放行后台任务后通过正常轮询验证回执处理。

默认重复 3 次、总超时 120 秒。音符数为 1–100000，曲库文件数为 1–5000，重复次数为 1–30，后台延迟为 0–30000 毫秒，总超时为 1–600 秒；曲库总音符数最多 500 万。超时包括数据准备和清理，由外层进程执行硬超时，仅结束本次诊断进程并清理其临时数据。

父进程通过临时 JSON 文件向子进程传递请求并收取结果，子进程按场景生成数据、执行控制层操作和检查结果。超时由父进程处理，因此主线程同步操作卡住时也能结束本次诊断；不会结束用户正在使用的桌面程序。

## 输出协议

标准输出为 UTF-8 JSON；`--help` 仍输出普通帮助文字。`--report` 先写临时文件，完成后再原子替换目标报告。退出码为 0 表示场景断言及可选延迟阈值通过，1 表示执行失败、非法数值、超时或阈值未通过，2 表示命令行语法错误。

- `schema_version`：报告结构版本，当前为 1。
- `version`、`environment`、`config`：程序版本、Python/Qt/显示后端和测试参数，不包含个人曲谱内容。
- `mode`：`controller` 或 `qt`。
- `simulated_delay`：是否启用了人工延迟。开启时不是正常运行性能基线。
- `runs`：每次场景耗时、原始采样和按操作汇总的数据。
- `summary`：跨重复场景聚合的操作耗时；`elapsed` 汇总场景耗时。
- `error`：失败时的错误类型和说明。超时可能无法保留进程内尚未返回的采样。

时间单位均为毫秒。汇总包含样本数、均值、P50、P95、最大值；百分位使用排序后的 nearest-rank。仅重复三次时应重点参考原始值和最大值，不能把 P95 当成大量样本下的稳定分布。

| 指标 | 含义 |
| --- | --- |
| `controller.*` | 对应控制层函数的同步墙钟耗时，包含其同步订阅者；嵌套操作不可相加 |
| `job.queue` | 提交任务到工作线程开始执行的时间 |
| `job.execute` | 工作线程执行时间，启用延迟时包含人工等待 |
| `job.delivery` | 后台函数结束到主线程取出结果的等待时间，包含正常轮询间隔；不包含结果安装 |
| `job.result` | 结果处理结论：`ok`、`error`、`cancelled`、`stale`；这是零时长的状态记录 |
| `ui.timer_lag` | 10 毫秒 Qt 精确定时器相邻回调间隔超出 10 毫秒的部分，反映事件循环延迟，也受系统调度影响 |
| `scenario.stop_and_cancel` | 提交停止和取消请求的同步耗时；完整取消等待仍体现在场景耗时内 |

每次运行最多记录 20000 个样本；超过容量会明确失败，不把截断后的数据当完整测量。生成测试数据、创建窗口、打开自动保存/取消/导出场景的初始工程和最终关闭不计入场景耗时。导出场景总耗时包含结果内容核对；该核对的文件读取不计入 `controller.show_result`。首次运行和后续运行分别保留，但没有主动清理操作系统文件缓存，不宣称冷盘基准。

Qt 窗口初始化时的曲库扫描及安装属于准备阶段，诊断等待其结束后才开启计时，避免污染加载、导出等场景。所有场景收尾先等待保存队列处理完毕，再关闭保存通道并取消工程和曲库任务，等待三个执行器退出后清理临时目录。保存失败会导致诊断失败，不把文件存在或任务已提交算作成功。

后台化后的 `controller.refresh_library` 只测请求提交及同步监听注册，新增 `controller.install_library` 测结果安装与同步菜单展示。扫描和文件哈希计入 `kind=library` 的 `job.execute`，完成后的轮询等待计入 `job.delivery`。后台化前的同名刷新指标包含整段扫描和菜单展示，不能只比较请求提交耗时就宣称整次刷新变快。任务编号只在各通道内递增，区分任务时同时使用 `kind` 和 `job`；曲库任务的 `revision` 是扫描请求代次。

`controller.autosave` 现在测独立快照复制、请求入队和同步订阅者耗时；工程校验、序列化及写盘计入 `kind=save` 的 `job.execute`，回执等待计入 `job.delivery`。保存的 `job.result` 额外包含 `document`（工程身份）、`revision`（快照版本）和 `save_kind`（auto/manual/preserve/close）。`job.queue` 从请求交给执行器时起算，不包含它在保存协调器待执行队列内的等待；连续自动暂存可合并，请求数不等于实际写入数。

声部排名已计入加载任务的 `job.execute`，`controller.install_parts` 只计状态安装和同步展示。正常导出任务的时间映射准备计入 `job.execute`；`controller.show_result` 仍包含状态安装、同步展示、自动保存的快照与请求提交和音频加载，但不再包含工程/音符文件回读、映射构建或自动暂存写盘。导出场景会同时记录 export 和 save 任务，分析时按 `kind` 区分。JSON 结构和现有指标名称保持兼容，跨改造比较时必须同时观察后台、主线程、轮询等待和总耗时，不能把工作转移到后台解释为总计算量减少。

Qt 模式使用窗口原有的 100 毫秒业务轮询；控制层模式按同样间隔调用 `poll()`。事件循环探针使用真实单调时钟，独立于播放器的可控时钟。场景总耗时、后台耗时、结果轮询等待和界面停顿应分别判断。

## Python 注入点

```python
from harmonica_studio.controller import AppController
from harmonica_studio.metrics import MetricsRecorder
from harmonica_studio.performance import WorkerDelay

metrics = MetricsRecorder()
# audio/player 应注入无声、无输入的诊断后端，home 使用临时目录。
controller = AppController(home, audio=audio, player=player,
                           metrics=metrics, before_work=WorkerDelay(2000))
# 正常调用业务操作，在所属线程驱动 controller.poll()。
report = metrics.snapshot()
```

`metrics` 和 `before_work` 默认均为 `None`。`before_work(kind, cancel_event)` 在工作线程内调用，不得修改控制层状态或 Qt 控件。统计仅用于观测，不推进 `revision`、`saved_revision`、`export_revision`，不改变任务后续动作。时钟单元测试可给 `MetricsRecorder(now=...)` 注入确定性时间源；这不应用于真实性能测量。

播放资源由控制层内部的 `PlaybackSession` 管理，`AppController` 的 `audio`、`player`、`clock` 构造器注入和转发访问保持可用。组件同步执行，`controller.show_result`、`controller.poll` 等原有指标仍包含其中的播放操作；本次职责拆分没有新增线程、诊断参数或 JSON 字段，也不把它当作音频性能优化。

需要精确复现“停止后任务才完成”等顺序时，使用 `diagnostic_backends.ControlledExecutor` 手动放行任务，比任意等待几秒稳定。原来的 `tests/workflow_fakes.py` 保留兼容导入，共用相同假后端实现。

本入口提供性能基线和慢任务行为验证，遵循当前控制层的执行边界：声部排名、正常导出结果准备、曲库扫描和工程写入在后台，快照复制、编辑校验和界面展示仍在主线程。没有新增真实游戏输入或原生音频设备性能测量。
