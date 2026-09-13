# 控制层与界面性能诊断

诊断入口复用 `AppController` 的真实业务流程，使用确定性合成 MIDI、独立临时目录、无声播放器和不会发送按键的演奏器。无需下载曲谱、连接手机或启动游戏，也不会使用个人曲库和 `HARMONICA_STUDIO_HOME` 中的数据。

## 命令

在项目根目录运行：

```powershell
# 控制层：加载一万音符、八个声部，重复三次
.\.venv\Scripts\python.exe launch.py diagnose --scenario load --notes 10000 --repeat 3

# 实际 Qt 窗口：扫描一千个文件，每个文件一千音符
.\.venv\Scripts\python.exe launch.py diagnose --scenario library --files 1000 --notes 1000 --ui --report verification/diagnose-library.json

# 实际编辑器提交修改，等待 400 毫秒防抖和自动保存
.\.venv\Scripts\python.exe launch.py diagnose --scenario autosave --notes 10000 --ui --report verification/diagnose-autosave.json

# 向后台注入两秒延迟，在任务开始后停止并取消，检查不会自动播放
.\.venv\Scripts\python.exe launch.py diagnose --scenario cancel --worker-delay-ms 2000 --ui

# 在加载时测量事件循环，超过 100 毫秒延迟则返回失败
.\.venv\Scripts\python.exe launch.py diagnose --scenario load --notes 10000 --ui --max-ui-lag-ms 100 --timeout 60
```

`--ui` 创建独立的真实 Qt 窗口；默认使用系统显示后端。自动化环境可显式设置 `QT_QPA_PLATFORM=offscreen` 验证行为，但离屏数据不能代替实际桌面响应测量。无 `--ui` 时不会加载 Qt，只测控制层。

场景说明：

| 场景 | 实际执行与检查 |
| --- | --- |
| `load` | 解析合成多轨 MIDI，主线程安装声部、计算排名，核对音符数量；Qt 模式还更新声部表格 |
| `library` | 扫描带有效 catalog 哈希的合成曲库，核对文件数量；Qt 模式包含菜单重建 |
| `autosave` | 打开自包含工程后修改音符并自动保存，核对落盘内容且不推进手动保存状态；Qt 模式通过编辑器提交并等待现有防抖定时器 |
| `cancel` | 请求生成试听，等待工作线程启动，停止试听并取消任务；检查不接收结果、不播放、不修改曲谱 |

`--worker-delay-ms` 只支持 `load`、`cancel`，后台等待可被取消唤醒，主线程不等待。同步的曲库扫描和自动保存场景不会假装支持此延迟参数。需要模拟保存失败时，单元测试可继续注入/替换保存依赖。

默认重复 3 次、总超时 120 秒。音符数为 1–100000，曲库文件数为 1–5000，重复次数为 1–30，后台延迟为 0–30000 毫秒，总超时为 1–600 秒；曲库总音符数最多 500 万。超时包括数据准备和清理，由外层进程执行硬超时，仅结束本次诊断进程并清理其临时数据。

## 输出协议

标准输出为 UTF-8 JSON；`--help` 仍输出普通帮助文字。`--report` 可额外原子保存同一份 JSON，不会把临时报告作为成品覆盖旧报告。退出码为 0 表示场景断言及可选延迟阈值通过，1 表示执行失败、非法数值、超时或阈值未通过，2 表示命令行语法错误。

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

每次运行最多记录 20000 个样本；超过容量会明确失败，不把截断后的数据当完整测量。生成测试数据、创建窗口、打开自动保存场景的初始工程和最终关闭不计入场景耗时。首次运行和后续运行分别保留，但没有主动清理操作系统文件缓存，不宣称冷盘基准。

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

需要精确复现“停止后任务才完成”等顺序时，使用 `diagnostic_backends.ControlledExecutor` 手动放行任务，比任意等待几秒稳定。原来的 `tests/workflow_fakes.py` 保留兼容导入，共用相同假后端实现。

本入口提供性能基线和慢任务行为验证；没有把声部排名、曲库扫描或保存改成后台执行，也没有新增真实游戏输入或原生音频设备性能测量。
