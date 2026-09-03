# 脑机接口实验项目

项目包含 OpenViBE Python Box 示例和 `CARGAME` Alpha Dash 眨眼跳跃游戏。

## Alpha Dash

`CARGAME` 使用 OpenBCI GUI 的 LSL EEG 输出，接入 OpenViBE Designer；前额 EEG 检测眨眼触发跳跃，后枕 8–13 Hz Alpha 波提供神经状态遥测。游戏为 40 秒自动前进关卡，收集金币、跳过障碍并抵达终点获胜；失败局有效金币为 0。

启动说明、OpenViBE 场景和算法参数见 [`CARGAME/README.md`](CARGAME/README.md) 与 [`CARGAME/OPENBCI_OPENVIBE_SETUP.md`](CARGAME/OPENBCI_OPENVIBE_SETUP.md)。

无硬件演示：

```powershell
cd E:\dijikeji\naojijiekou\CARGAME
python brain_bridge.py --config ssvep_config.json --simulate
python -m http.server 8000
```
