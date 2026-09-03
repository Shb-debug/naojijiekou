# OpenBCI GUI + OpenViBE 接入

## 运行顺序

1. 安装依赖：`python -m pip install -r requirements.txt`。
2. 启动 OpenBCI GUI，连接 Cyton/Ganglion，在 Networking Widget 中选择 LSL 输出，确认流类型为 `EEG`。
3. 启动 OpenViBE Acquisition Server，接收 OpenBCI GUI 的 LSL 数据，默认端口 `1024`。
4. 在 OpenViBE Designer 打开 `SUPER-mario.xml` 并运行。
5. 启动桥接器：`python brain_bridge.py --config ssvep_config.json`。
6. 启动网页：`python -m http.server 8000`，打开 `http://127.0.0.1:8000/`，点击页面右上角连接按钮。

## 场景设置

场景依次为：Acquisition client → Channel Selector → 1–30 Hz Temporal Filter → 0.9 秒 epoch / 0.1 秒步进 → Alpha + blink Python Box。Channel Selector 固定选择硬件通道 `1;2;15;14;16;13`，输出顺序为 `Fp1;Fp2;O1;Oz;O2;Pz`。其中前两路用于眨眼检测，后四路用于 Alpha 功率计算。若设备实际电极标签不同，请按实际标签调整选择器，并同步修改 `ssvep_config.json`。

## 算法说明

- 眨眼：0.45 秒窗、每 0.1 秒评估；每通道去中位数后计算 5–95% 振幅范围，使用滑动基线的 MAD 估计噪声；同时满足绝对振幅阈值和 robust z-score 阈值才触发。
- 抗误触发：触发锁存，信号回落到 release z-score 后才重新武装；事件间至少 550 ms。
- Alpha：后枕通道使用 Hann 窗 FFT，计算 8–13 Hz / 4–30 Hz 相对功率，实时发送到页面。
- 低延迟：OpenViBE epoch 每 100 ms 更新，桥接器 HTTP 请求超时 80 ms；实际端到端延迟还受硬件、LSL 缓冲、OpenViBE 调度和浏览器刷新影响。

## 无硬件验证

```powershell
python brain_bridge.py --config ssvep_config.json --simulate
```

模拟模式会发送 Alpha 遥测和周期性 blink 事件；网页也支持空格、向上箭头和右下角按钮作为人工演示输入。可用 `http://127.0.0.1:8766/health` 检查桥接器状态。

## 安全与准确性

这里的“Alpha 在线”是实时信号指标，不是医学结论。实际部署前要用个人自然眨眼数据重新调节 `blink_absolute_threshold` 与 `blink_z_threshold`，并检查电极阻抗；对闪烁刺激敏感或有光敏性癫痫风险者不要使用闪烁实验。
