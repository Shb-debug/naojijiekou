# OpenBCI GUI + OpenViBE 接入

## 运行顺序

1. 安装依赖：`python -m pip install -r requirements.txt`。
2. 启动 OpenBCI GUI，连接 Cyton/Ganglion，在 Networking Widget 中选择 LSL 输出，确认流类型为 `EEG`。
3. 启动 OpenViBE Acquisition Server，接收 OpenBCI GUI 的 LSL 数据，默认端口 `1024`。驱动选择 `obci_eeg1`，发送块大小优先设为 `32`，稳定后可尝试 `16`；Designer 连接后应显示 `1 client connected`。
4. 启动桥接器：`python brain_bridge.py --config ssvep_config.json`。真实设备不要加 `--simulate`；桥接器在方案 A 中只接收 OpenViBE，不读取 LSL。
5. 在 OpenViBE Designer 打开 `SUPER-mario.xml` 并运行。
6. 启动网页：`python -m http.server 8000`，打开 `http://127.0.0.1:8000/`，点击页面右上角连接按钮。

## 场景设置

场景依次为：Acquisition client → Channel Selector → 1–30 Hz Temporal Filter → 0.32 秒 epoch / 0.04 秒步进 → Alpha + blink Python Box。Channel Selector 固定选择硬件通道 `1;2;15;14;16;13`，输出顺序为 `Fp1;Fp2;O1;Oz;O2;Pz`。其中前两路用于眨眼检测，后四路用于 Alpha 功率计算。若设备实际电极标签不同，请按实际标签调整选择器，并同步修改 `ssvep_config.json`。

## 算法说明

- 眨眼：0.32 秒 epoch、0.04 秒步进；使用整个最近 0.32 秒作为检测窗。使用边缘安全平滑，避免窗口零填充造成的末端伪峰。两通道必须在时间上同步，并同时满足幅度、相关性、极性、峰值前噪声和波形紧凑度条件。
- 抗误触发：候选至少连续两个检测窗口确认，并要求峰值后回落恢复；触发锁存，信号回落到实际 release z-score 后才重新武装；事件间至少 850 ms。
- 网页跳跃：OpenViBE 已确认的 `BLINK_JUMP` 直接触发网页跳跃；Alpha 仅作状态遥测显示，不会拦截确认后的眨眼事件。
- Alpha：后枕通道使用 Hann 窗 FFT，计算 8–13 Hz / 4–30 Hz 相对功率，实时发送到页面。
- 低延迟：OpenViBE epoch 每 40 ms 更新；Acquisition Server 的 32/16 样本块在 500 Hz 下约为 64/32 ms。实际端到端延迟还受硬件、LSL 缓冲、OpenViBE 调度和浏览器刷新影响。
- 时间戳：由于 epoch 彼此重叠，控制器会把它们转换为连续、非重叠的刺激块，并将 Label 01 放在当前可用数据末端，避免刺激监听器因时间戳跳变或历史日期产生延迟和警告。

## 无硬件验证

```powershell
python brain_bridge.py --config ssvep_config.json --simulate
```

模拟模式会发送 Alpha 遥测和周期性 blink 事件，不能用于判断真实设备误判。真实模式使用不带 `--simulate` 的命令，并必须运行 OpenViBE Designer 的 `SUPER-mario.xml`。可用 `http://127.0.0.1:8766/health` 检查桥接器状态。

## 安全与准确性

这里的“Alpha 在线”是实时信号指标，不是医学结论。实际部署前要用个人自然眨眼数据重新调节 `blink_absolute_threshold` 与 `blink_z_threshold`，并检查电极阻抗；对闪烁刺激敏感或有光敏性癫痫风险者不要使用闪烁实验。
