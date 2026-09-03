# EEG Horizon Run · SSVEP Game

一个可直接打开的本地网页小游戏：小车自动前进，在三条车道间收集随机能量目标；支持 OpenBCI GUI 的 LSL EEG 实时接入，也支持键盘模拟。

## 启动

双击 `index.html` 即可浏览游戏页面；推荐在该文件夹中启动静态服务器：

```powershell
python -m http.server 8000
```

然后打开 `http://localhost:8000/`。

真实 EEG 推荐使用 OpenViBE 主链路：OpenBCI GUI 开启 LSL EEG 输出，OpenViBE Acquisition Server 接收后，在 Designer 运行 `CARGAME_OpenViBE_SSVEP.xml`；同时双击 `start_eeg_bridge.bat`。网页右上角显示 `真实 EEG · OpenViBE 已连接` 才表示 OpenViBE Python Box 已经处理到真实数据。备用的直接 LSL 模式显示 `真实 EEG · LSL 已连接`；`桥接在线 · 未找到 LSL EEG` 只代表桥接器在线，不代表 EEG 已接入。没有硬件时使用 `test_eeg_bridge.bat`，页面会显示 `模拟 EEG · 桥接已连接`。

如果依赖安装遇到 `check_hostname requires server_hostname`，不要直接重复原命令；双击 `install_dependencies.bat`，它会临时绕过旧 pip 与本机代理的兼容问题，然后安装 `numpy`、`pylsl` 和 `websockets`。

## 游戏规则

- 每局 90 秒，小车自动前进。
- 目标以较慢速度随机出现在三条车道，普通目标为 10、15 分；每局 90 秒内随机安排 5 个 20 分目标。
- 小车碰到当前车道的目标后自动计分，单局分数上限为 100。
- 只通过左右两个 SSVEP 指令换道：左道 20 Hz，右道 15 Hz。
- OpenViBE 默认使用后枕区通道 2、4、6；OpenBCI GUI 仍建议输出全部 8 个 Cyton 通道。
- 首次使用先点击“校准 EEG”：15 秒放松基线、20 秒注视左道、20 秒注视右道；分类器会按个人基线换算相对分数，减少误触和无响应。
- 左右目标会持续闪烁，用户应在进入对局后注视目标；方向键仅用于无硬件时模拟分类结果，不会启动闪烁。
- 键盘 `←` / `1` 控制左道，`→` / `2` 控制右道。
- 按 `Esc` 可以提前结束本局并进入结算。
- 结算后可选择“再来一局”或“结束游戏”；已完成对局会保存在浏览器本地历史记录中。

## 接入 OpenBCI / OpenViBE

当前 `app.js` 中的 `issueCommand(command)` 是控制入口。后续把 OpenViBE/Python 的 SSVEP 分类结果映射为：

```js
issueCommand('left');
issueCommand('right');
```

当前实现已保留置信度阈值、连续两次确认、平滑、冷却时间和“未识别”状态；个人校准完成后还会使用基线 z 分数阈值，避免 EEG 短时伪迹导致小车频繁换道。
