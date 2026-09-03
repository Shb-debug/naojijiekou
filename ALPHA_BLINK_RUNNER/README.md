# Alpha Dash：OpenBCI / OpenViBE 眨眼跳跃游戏

这是一个 40 秒 Canvas 闯关游戏：小人自动前进，眨眼触发跳跃；路上有金币和障碍，撞到障碍立即失败，坚持到终点即胜利。结算时会同时显示“拾取金币”和“有效金币”：失败局有效金币固定为 0，只有胜利局金币才计入战绩。

## 数据链路

```text
OpenBCI Cyton / Ganglion → OpenBCI GUI（LSL 输出）
        → OpenViBE Acquisition Server → OpenViBE Designer
        → alpha_blink_openvibe_controller.py → HTTP 8766
        → brain_bridge.py → WebSocket 8765 → index.html
```

算法分两路：硬件通道 1/2（Fp1/Fp2）的短窗振幅瞬态用于眨眼事件，硬件通道 15/14/16/13（O1/Oz/O2/Pz）的 8–13 Hz 相对功率用于 Alpha 状态遥测。眨眼检测采用滑动鲁棒基线、MAD 自适应阈值、滞回释放和 550 ms refractory，默认每 100 ms 评估一次，避免单点噪声重复触发。无硬件时可用 `--simulate` 或空格/向上箭头演示。

## 启动

```powershell
cd E:\dijikeji\naojijiekou\ALPHA_BLINK_RUNNER
python -m pip install -r requirements.txt
python brain_bridge.py --config ssvep_config.json --simulate
python -m http.server 8000
```

打开 <http://127.0.0.1:8000/>，点击右上角连接桥接器，再点击“开始闯关”。真实设备时，在 OpenBCI GUI 的 Networking Widget 开启 LSL EEG 输出；OpenViBE Designer 打开并运行 `SUPER-mario.xml`。场景默认选择 `1;2;15;14;16;13`，输出顺序为 `Fp1;Fp2;O1;Oz;O2;Pz`；如果设备标签不同，请在场景的 Channel Selector 和 `ssvep_config.json` 中同步调整。

## 参考来源

- OpenBCI GUI 官方仓库提供 UDP、OSC、LSL 等网络输出能力。
- `moonish1211/Cosmic-Crashout-Public` 是本项目眨眼控制策略的开源参考：其文档使用个体校准、幅度阈值和冷却来检测 blink。
- `nemathahmed/OpenVibe-to-Python` 提供了 OpenViBE → LSL/Python 与 Theta/Alpha/Gamma 频带功率估计的参考链路。

本项目是实验原型，不是医疗设备；真实 EEG 的阈值必须按电极位置、阻抗和个体信号重新校准。对闪烁刺激敏感者不要使用闪烁刺激。
