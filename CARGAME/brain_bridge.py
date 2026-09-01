"""OpenBCI LSL -> SSVEP classifier -> EEG Horizon Run WebSocket bridge.

The browser game stays independent from hardware. This process reads an EEG
stream published by OpenBCI GUI (LSL), detects 20/15 Hz SSVEP responses over
posterior channels, and sends confirmed left/right commands to the browser.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

try:
    from pylsl import StreamInlet, resolve_byprop
except ImportError:
    StreamInlet = None
    resolve_byprop = None

try:
    import websockets
except ImportError:
    websockets = None


DEFAULT_CONFIG = {
    "lsl_stream_type": "EEG",
    "sample_rate_fallback": 250,
    "window_seconds": 1.5,
    "eval_interval_seconds": 0.25,
    "min_score": 1.45,
    "min_margin": 0.18,
    "confirmations": 2,
    "cooldown_seconds": 0.8,
    "frequencies": {"left": 20, "right": 15},
    "posterior_names": ["P01", "P02", "P03", "P04", "PO1", "PO2", "PO3", "PO4"],
    "posterior_indices": [1, 3, 5],
    "websocket_host": "127.0.0.1",
    "websocket_port": 8765,
    "openvibe_command_port": 8766,
}


def load_config(path: Path) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8-sig")))
    config["frequencies"] = {**DEFAULT_CONFIG["frequencies"], **config.get("frequencies", {})}
    return config


def channel_labels(info: Any) -> list[str]:
    labels: list[str] = []
    try:
        channel = info.desc().child("channels").child("channel")
        while channel:
            label = channel.child_value("label") or channel.child_value("name")
            labels.append(label.strip() if label else f"EEG {len(labels) + 1}")
            channel = channel.next_sibling("channel")
    except Exception:
        pass
    return labels


def choose_channels(labels: list[str], requested: list[str], count: int, fallback_indices: list[int] | None = None) -> list[int]:
    upper = [label.upper().replace(" ", "") for label in labels]
    selected = [i for i, label in enumerate(upper) if any(name.upper().replace(" ", "") == label for name in requested)]
    if selected:
        return selected
    if fallback_indices:
        selected = [index for index in fallback_indices if 0 <= index < count]
        if selected:
            return selected
    # A safe fallback for unnamed four-channel headbands: use all available channels.
    return list(range(min(count, 4)))


def spectral_score(data: np.ndarray, sample_rate: float, frequency: float) -> float:
    """Return a normalized fundamental + harmonic SSVEP score."""
    samples = data.shape[0]
    if samples < 32:
        return 0.0
    window = np.hanning(samples)[:, None]
    spectrum = np.abs(np.fft.rfft((data - data.mean(axis=0)) * window, axis=0))
    bins = np.fft.rfftfreq(samples, 1.0 / sample_rate)

    def band_amplitude(target: float) -> float:
        if target >= sample_rate / 2:
            return 0.0
        width = max(0.45, sample_rate / samples * 1.5)
        mask = np.abs(bins - target) <= width
        return float(np.mean(np.max(spectrum[mask], axis=0))) if np.any(mask) else 0.0

    fundamental = band_amplitude(frequency)
    harmonic = band_amplitude(frequency * 2.0)
    low = max(1, int(round(6 * samples / sample_rate)))
    high = min(spectrum.shape[0] - 1, int(round(30 * samples / sample_rate)))
    background = float(np.median(spectrum[low:high])) if high > low else 1.0
    return (fundamental + 0.45 * harmonic) / max(background, 1e-9)


class Bridge:
    def __init__(self, config: dict[str, Any], simulate: bool = False) -> None:
        self.config = config
        self.simulate = simulate
        self.clients: set[Any] = set()
        self.running = True
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lsl_connected = False
        self.lsl_message = "尚未找到 LSL EEG 流"
        self.openvibe_connected = False
        self.openvibe_message = "尚未收到 OpenViBE 信号"
        self.openvibe_last_seen = 0.0
        self.calibration_lock = threading.Lock()
        self.calibration_phase_index = -1
        self.calibration_started_at = 0.0
        self.calibration_phase_end = 0.0
        self.calibration_complete = False
        self.calibration_phases = [("neutral", 15.0), ("left", 20.0), ("right", 20.0)]

    async def websocket_handler(self, websocket: Any, path: str | None = None) -> None:
        self.clients.add(websocket)
        await websocket.send(json.dumps(self.connection_payload("hello"), ensure_ascii=False))
        try:
            await websocket.wait_closed()
        finally:
            self.clients.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self.clients:
            return
        message = json.dumps(payload, ensure_ascii=False)
        results = await asyncio.gather(*(client.send(message) for client in tuple(self.clients)), return_exceptions=True)
        for client, result in zip(tuple(self.clients), results):
            if isinstance(result, Exception):
                self.clients.discard(client)

    def send(self, payload: dict[str, Any]) -> None:
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(payload), self.loop)

    def set_lsl_status(self, connected: bool, message: str, stream_name: str = "") -> None:
        self.lsl_connected = connected
        self.lsl_message = message
        self.send(self.connection_payload("status", stream_name=stream_name))

    def set_openvibe_status(self, connected: bool, message: str) -> None:
        self.openvibe_connected = connected
        self.openvibe_message = message
        if connected:
            self.openvibe_last_seen = time.monotonic()
        self.send(self.connection_payload("status", source="OpenViBE"))

    def openvibe_watchdog(self) -> None:
        while self.running:
            if self.openvibe_connected and time.monotonic() - self.openvibe_last_seen > 5.0:
                self.set_openvibe_status(False, "OpenViBE 信号已停止")
            time.sleep(1.0)

    def calibration_snapshot(self) -> dict[str, Any]:
        with self.calibration_lock:
            now = time.monotonic()
            if self.calibration_phase_index >= 0 and self.calibration_phase_index < len(self.calibration_phases):
                while now >= self.calibration_phase_end:
                    self.calibration_phase_index += 1
                    if self.calibration_phase_index >= len(self.calibration_phases):
                        self.calibration_complete = True
                        break
                    self.calibration_phase_end += self.calibration_phases[self.calibration_phase_index][1]
            active = 0 <= self.calibration_phase_index < len(self.calibration_phases)
            if active:
                phase, duration = self.calibration_phases[self.calibration_phase_index]
                remaining = max(0.0, self.calibration_phase_end - now)
                labels = {"neutral": "放松，不要注视任何按钮", "left": "请持续注视左道按钮", "right": "请持续注视右道按钮"}
                message = labels[phase]
            else:
                phase, duration, remaining = ("done", 0.0, 0.0) if self.calibration_complete else ("idle", 0.0, 0.0)
                message = "校准完成，可以开始游戏" if self.calibration_complete else "等待开始校准"
            total = sum(item[1] for item in self.calibration_phases)
            elapsed = 0.0 if self.calibration_phase_index < 0 else min(total, now - self.calibration_started_at)
            return {"active": active, "complete": self.calibration_complete, "phase": phase, "message": message, "remaining": round(remaining, 1), "elapsed": round(elapsed, 1), "total": total}

    def start_calibration(self) -> dict[str, Any]:
        with self.calibration_lock:
            self.calibration_phase_index = 0
            self.calibration_started_at = time.monotonic()
            self.calibration_phase_end = self.calibration_started_at + self.calibration_phases[0][1]
            self.calibration_complete = False
        snapshot = self.calibration_snapshot()
        self.send({"type": "calibration", **snapshot})
        return snapshot

    def connection_payload(self, message_type: str, stream_name: str = "", source: str = "") -> dict[str, Any]:
        return {
            "type": message_type,
            "source": source or "OpenBCI LSL SSVEP bridge",
            "lsl_connected": self.lsl_connected,
            "openvibe_connected": self.openvibe_connected,
            "message": self.openvibe_message if self.openvibe_connected else self.lsl_message,
            "stream": stream_name or ("SIMULATED EEG" if self.simulate else ""),
            "simulated": self.simulate,
            "timestamp": time.time(),
        }

    def receive_openvibe(self, command: str | None = None, confidence: float = 0.0, scores: Any = None) -> None:
        self.set_openvibe_status(True, "OpenViBE Python Box 已连接")
        if command not in {"left", "right"}:
            return
        payload: dict[str, Any] = {
            "type": "command",
            "command": command,
            "confidence": round(max(0.0, min(1.0, confidence)), 3),
            "source": "OpenViBE",
            "openvibe_connected": True,
            "timestamp": time.time(),
        }
        if isinstance(scores, dict):
            payload["scores"] = scores
        self.send(payload)

    def simulate_loop(self) -> None:
        self.set_lsl_status(True, "模拟 EEG 已连接", "SIMULATED EEG")
        commands = [("right", 15), ("left", 20)]
        index = 0
        while self.running:
            command, frequency = commands[index % len(commands)]
            self.send({"type": "command", "command": command, "frequency": frequency, "confidence": 0.92, "simulated": True, "timestamp": time.time()})
            index += 1
            time.sleep(4.0)

    def read_lsl_loop(self) -> None:
        if resolve_byprop is None or StreamInlet is None:
            self.set_lsl_status(False, "缺少 pylsl，请先安装项目依赖")
            while self.running:
                time.sleep(5)
            return

        while self.running:
            print(f"[LSL] 正在查找 type={self.config['lsl_stream_type']} 的 EEG 流…")
            streams = resolve_byprop("type", self.config["lsl_stream_type"], timeout=4)
            if not streams:
                self.set_lsl_status(False, "未找到 LSL EEG 流，请在 OpenBCI GUI 开启 LSL")
                print("[LSL] 暂无 EEG 流，4 秒后重试。", file=sys.stderr)
                time.sleep(4)
                continue

            try:
                inlet = StreamInlet(streams[0], max_buflen=60, recover=True)
                info = inlet.info()
                sample_rate = float(info.nominal_srate() or self.config["sample_rate_fallback"])
                labels = channel_labels(info)
                indices = choose_channels(labels, self.config["posterior_names"], info.channel_count(), self.config.get("posterior_indices"))
                self.set_lsl_status(True, "LSL EEG 已连接", info.name())
                print(f"[LSL] 已连接: {info.name()} | {sample_rate:g} Hz | 通道: {labels or '未命名'}")
                print(f"[SSVEP] 使用通道索引: {indices} | 目标频率: {self.config['frequencies']}")
            except Exception as error:
                self.set_lsl_status(False, f"LSL 连接失败：{error}")
                time.sleep(3)
                continue

            window_size = max(64, int(sample_rate * self.config["window_seconds"]))
            buffer: np.ndarray | None = None
            last_eval = 0.0
            candidate: str | None = None
            candidate_count = 0
            last_command = 0.0

            while self.running:
                try:
                    chunk, _ = inlet.pull_chunk(timeout=1.0, max_samples=max(32, int(sample_rate * 0.5)))
                    if not chunk:
                        continue
                    samples = np.asarray(chunk, dtype=float)
                    samples = samples[:, indices]
                    buffer = samples if buffer is None else np.vstack((buffer, samples))
                    if buffer.shape[0] > window_size:
                        buffer = buffer[-window_size:]
                    now = time.time()
                    if buffer.shape[0] < window_size or now - last_eval < self.config["eval_interval_seconds"]:
                        continue
                    last_eval = now
                    scores = {command: spectral_score(buffer, sample_rate, frequency) for command, frequency in self.config["frequencies"].items()}
                    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
                    winner, best = ordered[0]
                    second = ordered[1][1]
                    confidence = clamp((best - second) / max(best, 1e-9), 0.0, 1.0)
                    accepted = best >= self.config["min_score"] and confidence >= self.config["min_margin"]
                    if accepted and winner == candidate:
                        candidate_count += 1
                    elif accepted:
                        candidate, candidate_count = winner, 1
                    else:
                        candidate, candidate_count = None, 0
                    if accepted and candidate_count >= self.config["confirmations"] and now - last_command >= self.config["cooldown_seconds"]:
                        frequency = self.config["frequencies"][winner]
                        self.send({"type": "command", "command": winner, "frequency": frequency, "confidence": round(confidence, 3), "scores": scores, "timestamp": now})
                        print(f"[COMMAND] {winner} @ {frequency} Hz | confidence={confidence:.2f}")
                        last_command = now
                        candidate_count = 0
                except Exception as error:
                    self.set_lsl_status(False, f"LSL 数据流断开：{error}")
                    print(f"[LSL] 数据流断开，稍后重连：{error}", file=sys.stderr)
                    break

    def stop(self, *_: Any) -> None:
        self.running = False


class OpenViBECommandHandler(BaseHTTPRequestHandler):
    bridge: Bridge | None = None

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def request_values(self) -> dict[str, str]:
        values = {key: items[0] for key, items in parse_qs(urlparse(self.path).query).items() if items}
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            values.update({key: items[0] for key, items in parse_qs(body).items() if items})
        return values

    def handle_request(self) -> None:
        bridge = self.bridge
        if bridge is None:
            self.send_json({"ok": False, "error": "bridge_not_ready"}, 503)
            return
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json({"ok": True, **bridge.connection_payload("health")})
            return
        if path == "/calibration/start":
            self.send_json({"ok": True, "type": "calibration", **bridge.start_calibration()})
            return
        if path == "/calibration/state":
            self.send_json({"ok": True, "type": "calibration", **bridge.calibration_snapshot()})
            return
        if path not in {"/openvibe/heartbeat", "/openvibe/command"}:
            self.send_json({"ok": False, "error": "not_found"}, 404)
            return
        values = self.request_values()
        command = values.get("command") if path == "/openvibe/command" else None
        try:
            confidence = float(values.get("confidence", "0"))
        except ValueError:
            confidence = 0.0
        scores: Any = None
        if values.get("scores"):
            try:
                scores = json.loads(values["scores"])
            except json.JSONDecodeError:
                scores = None
        bridge.receive_openvibe(command, confidence, scores)
        self.send_json({"ok": True, "accepted": command in {"left", "right"}, **bridge.connection_payload("status", source="OpenViBE")})

    def do_GET(self) -> None:
        self.handle_request()

    def do_POST(self) -> None:
        self.handle_request()

    def log_message(self, format_string: str, *args: Any) -> None:
        return


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


async def main() -> None:
    parser = argparse.ArgumentParser(description="OpenBCI LSL SSVEP WebSocket bridge")
    parser.add_argument("--config", default="ssvep_config.json", help="JSON 配置文件")
    parser.add_argument("--simulate", action="store_true", help="不读取硬件，按周期发送左右模拟命令")
    args = parser.parse_args()
    if websockets is None:
        raise SystemExit("缺少 websockets。请先运行: python -m pip install -r requirements.txt")

    config = load_config(Path(args.config))
    bridge = Bridge(config, simulate=args.simulate)
    command_server = ThreadingHTTPServer(
        (config["websocket_host"], int(config.get("openvibe_command_port", 8766))),
        OpenViBECommandHandler,
    )
    OpenViBECommandHandler.bridge = bridge
    command_thread = threading.Thread(target=command_server.serve_forever, daemon=True)
    command_thread.start()
    watchdog_thread = threading.Thread(target=bridge.openvibe_watchdog, daemon=True)
    watchdog_thread.start()
    signal.signal(signal.SIGINT, bridge.stop)
    signal.signal(signal.SIGTERM, bridge.stop)
    bridge.loop = asyncio.get_running_loop()
    try:
        async with websockets.serve(bridge.websocket_handler, config["websocket_host"], config["websocket_port"]):
            print(f"[WS] WebSocket 服务已启动: ws://{config['websocket_host']}:{config['websocket_port']}")
            print(f"[HTTP] OpenViBE 命令入口: http://{config['websocket_host']}:{config.get('openvibe_command_port', 8766)}")
            worker = asyncio.to_thread(bridge.simulate_loop if args.simulate else bridge.read_lsl_loop)
            await worker
            while bridge.running:
                await asyncio.sleep(0.2)
    finally:
        command_server.shutdown()
        command_server.server_close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
