# Alpha Dash：OpenBCI / OpenViBE 眨眼跳跃游戏

这是一个 40 秒 Canvas 闯关游戏：小人自动前进，眨眼触发跳跃；路上有金币和障碍，撞到障碍立即失败，坚持到终点即胜利。结算时会同时显示“拾取金币”和“有效金币”：失败局有效金币固定为 0，只有胜利局金币才计入战绩。

## 数据链路

```text
OpenBCI Cyton / Ganglion → OpenBCI GUI（LSL 输出）
        → OpenViBE Acquisition Server → OpenViBE Designer
        → alpha_blink_openvibe_controller.py → HTTP 8766
        → brain_bridge.py → WebSocket 8765 → index.html
```

算法分两路：硬件通道 1/2（Fp1/Fp2）的短窗时域波形用于眨眼事件，硬件通道 15/14/16/13（O1/Oz/O2/Pz）的 8–13 Hz 相对功率用于 Alpha 状态遥测。真实设备时只由 OpenViBE Python Box 检测眨眼，`brain_bridge.py` 不读取 LSL、不会运行第二个检测器。眨眼检测采用边缘安全平滑、双通道同步约束、鲁棒自适应基线、候选—确认—恢复状态机和事件冷却，避免单点噪声触发。无硬件演示才使用 `--simulate`。

## 无硬件演示

```powershell
cd E:\dijikeji\naojijiekou\ALPHA_BLINK_RUNNER
python -m pip install -r requirements.txt
python brain_bridge.py --config ssvep_config.json --simulate
python -m http.server 8000
```

此模式会主动生成模拟眨眼，仅用于测试网页和游戏，不代表真实设备检测结果。打开 <http://127.0.0.1:8000/>，点击右上角连接桥接器，再点击“开始闯关”。

## 真实设备启动（方案 A：OpenViBE-only）

按以下顺序启动：

1. 启动 OpenBCI GUI，连接 Cyton，在 Networking Widget 中开启 LSL 输出，流类型设为 `EEG`。
2. 启动 OpenViBE Acquisition Server，连接到 OpenBCI GUI 的 LSL 数据，默认端口 `1024`。
   - LSL 驱动选择 `obci_eeg1`，确认打开的是 16 通道 EEG。
   - `Sample count per sent block` 优先设为 `32`；如果运行稳定可设为 `16`。500 Hz 下分别约为 64 ms 和 32 ms，`128` 会带来约 256 ms 的数据块等待。
   - OpenViBE Designer 连接后，Acquisition Server 底部应显示 `1 client connected`，不能停留在 `0 clients connected`。
3. 在本目录启动桥接器，注意真实设备命令**不要带 `--simulate`**：

   ```powershell
   cd E:\dijikeji\naojijiekou\ALPHA_BLINK_RUNNER
   python brain_bridge.py --config ssvep_config.json
   ```

4. 在 OpenViBE Designer 打开并运行 `SUPER-mario.xml`。
5. 另开终端启动网页：

   ```powershell
   cd E:\dijikeji\naojijiekou\ALPHA_BLINK_RUNNER
   python -m http.server 8000
   ```

6. 打开 <http://127.0.0.1:8000/>，点击右上角连接桥接器；确认显示“OpenViBE 已连接”后，再点击“开始闯关”。

真实模式下，`brain_bridge.py` 只等待和接收 OpenViBE 的 HTTP 事件，不读取 LSL，也不会启动备用眨眼检测器。不要再额外启动旧版 LSL 检测脚本。场景默认选择 `1;2;15;14;16;13`，输出顺序为 `Fp1;Fp2;O1;Oz;O2;Pz`；如果设备标签不同，请调整场景的 Channel Selector。

## 眨眼检测逻辑

- OpenViBE 场景先对信号做 1–30 Hz 带通，并以 0.32 秒 epoch、0.04 秒步进送入 Python Box；Python Box 使用整个最近 0.32 秒作为眨眼检测窗，从源头降低等待延迟。
- 眨眼候选必须同时满足：1/2 通道负向幅度、通道幅度一致、峰值时间接近、相关性、峰值前噪声稳定、极性和波形紧凑度条件。
- 普通强度候选仍经过两个检测窗口确认，并要求峰值之后出现回落恢复；只有极强、双通道同步且已经回落的事件才走快速触发路径，以降低延迟而不放宽普通噪声判定。
- 网页收到 OpenViBE 的 `BLINK_JUMP` 后直接触发跳跃；Alpha 指标仅用于显示，不再因为 Alpha 偏低而丢弃已经确认的眨眼事件。
- 触发后必须回落到实际释放阈值，并经过约 0.85 秒冷却，才会重新武装。
- 启动前约 5 秒用于个人噪声校准；校准和运行期间只用稳定窗口更新基线，疑似眨眼不会污染基线。
- 刺激输出会把重叠 epoch 转换为连续、非重叠的时间段，并将 Label 01 放在当前可用数据末端，避免 OpenViBE 报告 `inconsistent chunk dates` 和下游使用过期刺激时间。

### 延迟排查

如果日志中的 `BLINK_CANDIDATE` 和 `BLINK_JUMP` 已经同一时刻出现，但网页仍然慢，优先检查 Acquisition Server 的发送块大小、是否确实有 1 个客户端连接，以及是否存在旧的 `brain_bridge.py` 进程占用 8765/8766 端口。`Device drift is too high` 属于时钟漂移警告，通常造成抖动而不是秒级延迟，应先检查设备、网络和 CPU 负载，不能仅靠放宽容差掩盖问题。

如果需要调参，优先修改 `SUPER-mario.xml` 中 Python Box 的眨眼设置；`ssvep_config.json` 主要用于桥接器地址和演示配置，真实方案 A 的眨眼判定不从该 JSON 读取。

## 参考来源

- OpenBCI GUI 官方仓库提供 UDP、OSC、LSL 等网络输出能力。
- `moonish1211/Cosmic-Crashout-Public` 是本项目眨眼控制策略的开源参考：其文档使用个体校准、幅度阈值和冷却来检测 blink。
- `nemathahmed/OpenVibe-to-Python` 提供了 OpenViBE → LSL/Python 与 Theta/Alpha/Gamma 频带功率估计的参考链路。

本项目是实验原型，不是医疗设备；真实 EEG 的阈值必须按电极位置、阻抗和个体信号重新校准。对闪烁刺激敏感者不要使用闪烁刺激。
