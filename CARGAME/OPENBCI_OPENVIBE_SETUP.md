# EEG Horizon Run：OpenBCI / OpenViBE 接入说明

## 项目数据链路

```text
OpenBCI Cyton / Ganglion
        ↓
OpenBCI GUI
        ↓ LSL
OpenViBE Acquisition Server
        ↓ OpenViBE Designer 场景
Python Box：CCA + 连续确认
        ↓ HTTP :8766
brain_bridge.py
        ↓ WebSocket :8765
EEG Horizon Run 网页
```

现在推荐使用 OpenViBE 主链路：OpenViBE 负责采集、选通道、4–32 Hz 带通滤波和 1.5 秒分段，Python Box 使用参考项目中的正则化 CCA 计算 10 Hz/15 Hz，再通过本机 HTTP 把确认后的左右命令发给 `brain_bridge.py`。`brain_bridge.py` 仍保留直接读取 LSL 的备用模式。

## 第一次安装

在项目目录打开 PowerShell：

```powershell
python -m pip install -r requirements.txt
```

如果 Python 不是默认命令，请把命令中的 `python` 换成 `py -3.10`。

如果出现 `ValueError: check_hostname requires server_hostname`，这是旧版 pip 与系统 HTTPS 代理的兼容问题。优先双击项目中的 `install_dependencies.bat`；手动执行时使用：

```powershell
$env:NO_PROXY='*'
$env:no_proxy='*'
python -m pip install --no-cache-dir -r requirements.txt
```

## OpenBCI GUI 设置

1. 连接 Cyton 或 Ganglion，选择对应 Board。
2. 建议使用 Cyton 8 通道；枕区至少接 O1、Oz、O2、Pz。
3. 在 OpenBCI GUI 中先打开 Time Series 和 FFT，确认信号和阻抗正常。
4. 打开 Networking Widget，选择 LSL 输出。
5. 确认输出数据包含 EEG 通道，流类型为 `EEG`。
6. 启动 `start_eeg_bridge.bat`。它会同时启动网页 WebSocket `8765` 和 OpenViBE 命令入口 `8766`。
7. 使用 OpenViBE 主链路时，再启动 Acquisition Server，并在 Designer 中打开项目内的 `CARGAME_OpenViBE_SSVEP.xml`。
8. 打开网页，点击右上角“模拟信号 · 键盘输入”连接桥接器；运行 OpenViBE 场景并收到信号后会显示“真实 EEG · OpenViBE 已连接”。

## 完整启动路线

按下面顺序开五个窗口/程序：

1. **OpenBCI GUI**：连接 Cyton/Ganglion，确认波形、阻抗和 FFT 正常，然后在 Networking Widget 中开启 LSL EEG 输出。
2. **游戏网页服务**：双击 `start_game.bat`，或在项目目录运行 `python -m http.server 8000`，访问 <http://localhost:8000/>。
3. **OpenViBE Acquisition Server**：选择 LSL 相关驱动，端口保持 `1024`，确认能接收到 OpenBCI 的信号。
4. **EEG 桥接器**：双击 `start_eeg_bridge.bat`。控制台会启动 WebSocket `8765` 和 HTTP `8766`。
5. **OpenViBE Designer**：打开并运行 `CARGAME_OpenViBE_SSVEP.xml`。控制台应出现 `SIGNAL_INFO` 与持续的 `CCA_SCORE`。
6. **页面连接**：点击页面右上角连接按钮。状态含义如下：
   - `桥接在线 · 未找到 LSL EEG`：仅表示桥接器在线，OpenViBE 场景还没有向它发送信号。
   - `真实 EEG · OpenViBE 已连接`：OpenViBE Python Box 已处理到真实 EEG，并且命令入口可用。
   - `真实 EEG · LSL 已连接`：使用备用的 `brain_bridge.py` 直接 LSL 读取模式。
   - `连接失败 · 点击重试`：桥接器未启动或端口不可用。
7. **进入对局**：点击“进入对局”。左道按钮整块以 10 Hz 闪烁，右道按钮整块以 15 Hz 闪烁；注视目标即可尝试换道，车辆默认自动前进。

没有硬件时，把第 3 步换成双击 `test_eeg_bridge.bat`，页面应显示 `模拟 EEG · 桥接已连接`，每 4 秒自动演示一次左右换道。

进入对局后，左道和右道目标会持续分别以 10 Hz 和 15 Hz 闪烁。真实 EEG 模式下请注视目标，不要用方向键触发刺激；方向键只属于无硬件键盘模拟模式。

## OpenViBE 设置

OpenBCI 官方连接方式是：OpenBCI GUI → Networking Widget → LSL → OpenViBE Acquisition Server。启动 Acquisition Server 后选择 LSL 相关驱动，端口保持 `1024`，再在 Designer 中打开项目内的 `CARGAME_OpenViBE_SSVEP.xml`。

场景结构已经按参考 `alpha-detector` 配好：

- Acquisition client → Channel Selector → Temporal Filter → Time based epoching → Python 3 scripting。
- Channel Selector 初始为 `2;4;6`，对应本次 CARGAME 的后枕区通道配置；如果你的电极顺序不同，需要在 OpenViBE 中改成实际的 O1/Oz/O2 或对应后枕区通道。
- Python Box 脚本为 `ssvep_car_openvibe_controller.py`，频率固定为左 10 Hz、右 15 Hz。
- Python Box 将命令发送到 `http://127.0.0.1:8766`，网页仍从 `ws://127.0.0.1:8765` 接收转发结果。

建议先用 OpenViBE 的 Signal Display / Spectrum Display 确认后枕区信号，再启动场景。场景控制台出现 `SIGNAL_INFO`、`CCA_SCORE`，并且网页显示 `真实 EEG · OpenViBE 已连接`，才算链路真正打通。

## 当前分类器

桥接器备用模式使用 1.5 秒滑动窗口，在后枕区计算 10 Hz 与 15 Hz 的基频及二次谐波能量。OpenViBE 主模式使用正则化 CCA。只有当：

- 目标频率能量超过噪声阈值；
- 赢家与第二名有足够差距；
- 连续两次窗口判断一致；
- 距离上次命令超过冷却时间；

才会发送 `left` 或 `right`。阈值可以在 `ssvep_config.json` 调整。

## 没有硬件时测试真实网页链路

运行 `test_eeg_bridge.bat`，再打开游戏并点击右上角连接按钮。桥接器会每 4 秒发送一次左右模拟命令，用于确认 WebSocket、网页换道和状态显示都正常。

## 安全提示

SSVEP 使用闪烁视觉刺激。公开演示前应提醒光敏性癫痫风险，降低亮度和刺激对比度，并准备键盘控制作为人工接管方式。
