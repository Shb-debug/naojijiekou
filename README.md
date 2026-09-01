# 脑机接口实验项目

这是一个基于 OpenBCI、LSL、OpenViBE 和 SSVEP（稳态视觉诱发电位）的脑机接口实验项目，包含 EEG 状态检测、SSVEP 数字/算术交互，以及一个可用真实 EEG 控制的网页赛车小游戏。

## 项目内容

### `alpha-detector`

OpenViBE Python Box 示例与配套服务：

- Alpha 波状态检测：`alpha_state_detector.py`
- 十频率 SSVEP 算术控制：`ssvep_arithmetic_controller.py`
- 分组行列式 SSVEP 算术控制：`ssvep_grouped_arithmetic_controller.py`
- SSVEP 音乐控制：`ssvep_music_controller.py`
- 算术交互后端服务：`ssvep_arithmetic_server.py`、`ssvep_grouped_arithmetic_server.py`
- 对应的 OpenViBE 场景文件、网页界面和测试数据

### `CARGAME`

EEG Horizon Run 脑机接口赛车小游戏：

- `index.html`、`app.js`、`styles.css`：网页游戏界面
- `brain_bridge.py`：OpenBCI LSL / OpenViBE 与网页之间的本地桥接器
- `ssvep_car_openvibe_controller.py`：OpenViBE Python Box 控制器
- `CARGAME_OpenViBE_SSVEP.xml`：OpenViBE 场景
- `ssvep_config.json`：桥接器配置
- `OPENBCI_OPENVIBE_SETUP.md`：详细硬件与 OpenViBE 接入说明

## 工作链路

```text
OpenBCI EEG
    ↓ LSL
OpenBCI GUI / OpenViBE Acquisition Server
    ↓
OpenViBE Python Box（CCA / SSVEP 分类）
    ↓ HTTP :8766
brain_bridge.py
    ↓ WebSocket :8765
网页赛车游戏
```

项目默认将左、右指令分别映射到 **20 Hz** 和 **15 Hz** SSVEP 目标。实际使用时，应根据电极布局调整 OpenViBE 场景中的后枕区通道选择。

## 环境要求

- Windows
- Python 3.10 或更高版本
- Git（用于版本管理）
- OpenBCI GUI（使用真实 EEG 时）
- OpenViBE Designer 与 Acquisition Server（使用 OpenViBE 主链路时）
- OpenBCI Cyton / Ganglion 等 EEG 设备（无硬件时可使用模拟模式）

## 安装依赖

进入 `CARGAME` 目录，运行：

```powershell
python -m pip install -r requirements.txt
```

依赖包括：`numpy`、`pylsl` 和 `websockets`。Windows 用户也可以双击 `CARGAME/install_dependencies.bat` 安装。

## 运行网页游戏

在 `CARGAME` 目录运行：

```powershell
python -m http.server 8000
```

然后打开：<http://localhost:8000/>

也可以直接双击 `CARGAME/start_game.bat`。

## 无硬件模拟测试

先运行：

```powershell
cd CARGAME
python brain_bridge.py --config ssvep_config.json --simulate
```

或者双击 `CARGAME/test_eeg_bridge.bat`。模拟桥接器会定时发送左右指令，用于检查网页、WebSocket 和换道逻辑。

## 真实 EEG 运行概要

1. 在 OpenBCI GUI 中连接设备，并开启 LSL EEG 输出。
2. 启动 `CARGAME/start_eeg_bridge.bat`。
3. 启动 OpenViBE Acquisition Server，接收 OpenBCI 的 LSL 数据。
4. 在 OpenViBE Designer 中打开并运行 `CARGAME/CARGAME_OpenViBE_SSVEP.xml`。
5. 启动网页并连接桥接器。
6. 进入校准和游戏流程，注视对应频率的视觉目标。

更完整的端口、通道、滤波器和故障排查说明，请查看 [`CARGAME/OPENBCI_OPENVIBE_SETUP.md`](CARGAME/OPENBCI_OPENVIBE_SETUP.md)。

## 配置

主要配置位于 `CARGAME/ssvep_config.json`：

- EEG 流类型：`EEG`
- 滑动窗口：`1.5` 秒
- 默认频率：左 `20 Hz`，右 `15 Hz`
- WebSocket：`127.0.0.1:8765`
- OpenViBE HTTP 命令入口：`127.0.0.1:8766`
- 默认后枕区通道索引：`[1, 3, 5]`

如果设备通道命名或排列不同，请同步调整配置和 OpenViBE 场景中的 Channel Selector。

## 安全提示

项目使用闪烁视觉刺激。对闪烁敏感、存在光敏性癫痫风险或感到不适的人员不应使用真实刺激模式。建议降低亮度和对比度，并始终保留键盘控制作为人工接管方式。

## 说明

本项目用于学习、实验和原型验证，不构成医疗设备或医疗诊断工具。真实 EEG 的分类效果会受到电极接触、通道布局、环境噪声和个体差异影响。
