"""Low-latency OpenBCI LSL / OpenViBE bridge for Alpha Dash.

Pipeline: OpenBCI GUI -> LSL -> (OpenViBE Python Box or this fallback) -> WS.
Frontal channels detect blink transients; posterior channels estimate 8-13 Hz
alpha relative power. The detector uses robust MAD thresholds and hysteresis.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import threading
import time
from collections import deque
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

DEFAULT_CONFIG: dict[str, Any] = {
    "lsl_stream_type": "EEG", "sample_rate_fallback": 250,
    "blink_window_seconds": 0.45, "alpha_window_seconds": 0.90,
    "eval_interval_seconds": 0.10, "blink_absolute_threshold": 35.0,
    "blink_z_threshold": 5.0, "blink_release_z": 1.8, "blink_refractory_seconds": 0.55,
    "alpha_band": [8.0, 13.0], "alpha_ready_ratio": 0.16,
    "frontal_names": ["Fp1", "Fp2", "AF3", "AF4"],
    "posterior_names": ["O1", "Oz", "O2", "Pz", "PO3", "PO4"],
    "frontal_indices": [0, 1], "posterior_indices": [2, 3],
    "websocket_host": "127.0.0.1", "websocket_port": 8765, "openvibe_command_port": 8766,
}


def load_config(path: Path) -> dict[str, Any]:
    config = {**DEFAULT_CONFIG}
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8-sig")))
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


def choose_channels(labels: list[str], requested: list[str], count: int, fallback: list[int]) -> list[int]:
    normalized = [item.upper().replace(" ", "") for item in labels]
    selected = [i for i, item in enumerate(normalized) if item in {name.upper().replace(" ", "") for name in requested}]
    if selected:
        return selected
    selected = [i for i in fallback if 0 <= i < count]
    return selected or list(range(min(count, 2)))


def alpha_ratio(data: np.ndarray, sample_rate: float, band: tuple[float, float] = (8.0, 13.0)) -> float:
    """Windowed Welch-like relative alpha power with a Hann window."""
    if data.shape[0] < max(32, int(sample_rate * 0.45)):
        return 0.0
    centered = data - np.median(data, axis=0, keepdims=True)
    nfft = max(256, 1 << int(np.ceil(np.log2(centered.shape[0]))))
    power = np.abs(np.fft.rfft(centered * np.hanning(centered.shape[0])[:, None], n=nfft, axis=0)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1.0 / sample_rate)
    total_mask = (freqs >= 4.0) & (freqs <= 30.0)
    alpha_mask = (freqs >= band[0]) & (freqs <= band[1])
    total = float(np.sum(power[total_mask])) if np.any(total_mask) else 1.0
    alpha = float(np.sum(power[alpha_mask])) if np.any(alpha_mask) else 0.0
    return max(0.0, min(1.0, alpha / max(total, 1e-12)))


class BlinkDetector:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.history: deque[float] = deque(maxlen=45)
        self.latched = False
        self.last_event = -1e9

    def update(self, data: np.ndarray, sample_rate: float, now: float) -> tuple[bool, float, float]:
        if data.size == 0:
            return False, 0.0, 0.0
        centered = data - np.median(data, axis=0, keepdims=True)
        ranges = np.percentile(centered, 95, axis=0) - np.percentile(centered, 5, axis=0)
        current = float(np.median(ranges))
        self.history.append(current)
        history = np.asarray(list(self.history)[:-1], dtype=float)
        if history.size < 8:
            return False, 0.0, current
        baseline = float(np.median(history))
        mad = max(1.4826 * float(np.median(np.abs(history - baseline))), 1.0)
        z = (current - baseline) / mad
        threshold = max(float(self.config["blink_absolute_threshold"]), baseline + float(self.config["blink_z_threshold"]) * mad)
        confidence = max(0.0, min(1.0, (current - threshold) / max(threshold, 1.0) + 0.55))
        detected = current >= threshold and z >= float(self.config["blink_z_threshold"])
        if self.latched:
            if z <= float(self.config["blink_release_z"]):
                self.latched = False
            detected = False
        if detected and now - self.last_event >= float(self.config["blink_refractory_seconds"]):
            self.latched = True
            self.last_event = now
            return True, confidence, current
        return False, confidence, current


class Bridge:
    def __init__(self, config: dict[str, Any], simulate: bool = False) -> None:
        self.config, self.simulate = config, simulate
        self.clients: set[Any] = set()
        self.running, self.loop = True, None
        self.lsl_connected, self.lsl_message = False, "尚未找到 LSL EEG 流"
        self.openvibe_connected, self.openvibe_message, self.openvibe_last_seen = False, "尚未收到 OpenViBE 信号", 0.0

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
        clients = tuple(self.clients)
        results = await asyncio.gather(*(client.send(message) for client in clients), return_exceptions=True)
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self.clients.discard(client)

    def send(self, payload: dict[str, Any]) -> None:
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(payload), self.loop)

    def set_lsl_status(self, connected: bool, message: str, stream: str = "") -> None:
        self.lsl_connected, self.lsl_message = connected, message
        self.send(self.connection_payload("status", stream_name=stream))

    def set_openvibe_status(self, connected: bool, message: str = "") -> None:
        self.openvibe_connected, self.openvibe_message = connected, message
        if connected:
            self.openvibe_last_seen = time.monotonic()
        self.send(self.connection_payload("status", source="OpenViBE"))

    def openvibe_watchdog(self) -> None:
        while self.running:
            if self.openvibe_connected and time.monotonic() - self.openvibe_last_seen > 5:
                self.set_openvibe_status(False, "OpenViBE 信号已停止")
            time.sleep(1)

    def connection_payload(self, message_type: str, stream_name: str = "", source: str = "") -> dict[str, Any]:
        return {"type": message_type, "source": source or "OpenBCI Alpha Blink bridge", "lsl_connected": self.lsl_connected, "openvibe_connected": self.openvibe_connected, "message": self.openvibe_message if self.openvibe_connected else self.lsl_message, "stream": stream_name or ("SIMULATED EEG" if self.simulate else ""), "simulated": self.simulate, "timestamp": time.time()}

    def receive_openvibe(self, event: str | None = None, confidence: float = 0.0, alpha_ratio_value: float | None = None, blink_score: float | None = None) -> None:
        self.set_openvibe_status(True, "OpenViBE Alpha/Blink Python Box 已连接")
        if alpha_ratio_value is not None:
            ratio = max(0.0, min(1.0, float(alpha_ratio_value)))
            self.send({"type": "telemetry", "alpha_ratio": ratio, "alpha_confidence": max(0.0, min(1.0, ratio / .32)), "source": "OpenViBE", "timestamp": time.time()})
        if event == "blink":
            self.send({"type": "blink", "blink_confidence": max(0.0, min(1.0, float(confidence))), "blink_score": blink_score, "source": "OpenViBE", "timestamp": time.time()})

    def simulate_loop(self) -> None:
        self.set_lsl_status(True, "模拟 Alpha EEG 已连接", "SIMULATED EEG")
        next_blink = time.monotonic() + 1.4
        while self.running:
            now = time.monotonic()
            self.send({"type": "telemetry", "alpha_ratio": .24 + .04 * np.sin(now), "alpha_confidence": .78, "blink_confidence": .0, "simulated": True, "timestamp": time.time()})
            if now >= next_blink:
                self.send({"type": "blink", "blink_confidence": .94, "blink_score": 9.0, "simulated": True, "timestamp": time.time()})
                next_blink = now + 2.4
            time.sleep(.1)

    def read_lsl_loop(self) -> None:
        if resolve_byprop is None or StreamInlet is None:
            self.set_lsl_status(False, "缺少 pylsl，请先安装项目依赖")
            while self.running:
                time.sleep(5)
            return
        while self.running:
            print(f"[LSL] 查找 type={self.config['lsl_stream_type']} 的 EEG 流…")
            streams = resolve_byprop("type", self.config["lsl_stream_type"], timeout=4)
            if not streams:
                self.set_lsl_status(False, "未找到 LSL EEG 流，请在 OpenBCI GUI 开启 LSL")
                time.sleep(3)
                continue
            try:
                inlet = StreamInlet(streams[0], max_buflen=60, recover=True)
                info, sample_rate = inlet.info(), float(inlet.info().nominal_srate() or self.config["sample_rate_fallback"])
                labels, count = channel_labels(info), info.channel_count()
                frontal = choose_channels(labels, self.config["frontal_names"], count, self.config["frontal_indices"])
                posterior = choose_channels(labels, self.config["posterior_names"], count, self.config["posterior_indices"])
                self.set_lsl_status(True, "LSL Alpha EEG 已连接", info.name())
                print(f"[LSL] {info.name()} | {sample_rate:g} Hz | frontal={frontal} posterior={posterior}")
            except Exception as error:
                self.set_lsl_status(False, f"LSL 连接失败：{error}"); time.sleep(3); continue
            blink_size=max(64,int(sample_rate*self.config["blink_window_seconds"])); alpha_size=max(128,int(sample_rate*self.config["alpha_window_seconds"])); buffer: np.ndarray|None=None; detector=BlinkDetector(self.config); last_eval=0.0
            while self.running:
                try:
                    chunk,_=inlet.pull_chunk(timeout=.5,max_samples=max(32,int(sample_rate*.25)))
                    if not chunk: continue
                    samples=np.asarray(chunk,dtype=float); buffer=samples if buffer is None else np.vstack((buffer,samples)); buffer=buffer[-max(blink_size,alpha_size):]
                    now=time.time()
                    if now-last_eval<float(self.config["eval_interval_seconds"]): continue
                    last_eval=now
                    blink_data=buffer[-blink_size:,frontal]; alpha_data=buffer[-alpha_size:,posterior]; event,confidence,score=detector.update(blink_data,sample_rate,now); ratio=alpha_ratio(alpha_data,sample_rate,tuple(self.config["alpha_band"]))
                    self.send({"type":"telemetry","alpha_ratio":ratio,"alpha_confidence":max(0.,min(1.,ratio/.32)),"blink_confidence":confidence,"blink_score":score,"source":"LSL","timestamp":now})
                    if event:
                        self.send({"type":"blink","blink_confidence":round(confidence,3),"blink_score":round(score,2),"source":"LSL","timestamp":now}); print(f"[BLINK] confidence={confidence:.2f} score={score:.1f}")
                except Exception as error:
                    self.set_lsl_status(False, f"LSL 数据流断开：{error}"); print(f"[LSL] 数据流断开：{error}",file=sys.stderr); break

    def stop(self, *_: Any) -> None:
        self.running=False


class OpenViBECommandHandler(BaseHTTPRequestHandler):
    bridge: Bridge|None=None
    def send_json(self,payload:dict[str,Any],status:int=200)->None:
        body=json.dumps(payload,ensure_ascii=False).encode();self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Access-Control-Allow-Origin","*");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self)->None:
        if not self.bridge:self.send_json({"ok":False},503);return
        query=parse_qs(urlparse(self.path).query);path=urlparse(self.path).path
        if path=="/health":self.send_json({"ok":True,**self.bridge.connection_payload("status")});return
        event="blink" if path=="/openvibe/blink" else None
        if path in {"/openvibe/heartbeat","/openvibe/alpha"}: event=None
        if path in {"/openvibe/blink","/openvibe/heartbeat","/openvibe/alpha"}:
            self.bridge.receive_openvibe(event, float(query.get("confidence",[0])[0]), float(query["alpha_ratio"][0]) if "alpha_ratio" in query else None, float(query["blink_score"][0]) if "blink_score" in query else None);self.send_json({"ok":True,**self.bridge.connection_payload("status",source="OpenViBE")});return
        self.send_json({"ok":False,"error":"unknown endpoint"},404)
    def do_POST(self)->None:self.do_GET()
    def log_message(self,format_string:str,*args:Any)->None:return


async def main()->None:
    parser=argparse.ArgumentParser(description="OpenBCI Alpha/Blink WebSocket bridge");parser.add_argument("--config",default="ssvep_config.json");parser.add_argument("--simulate",action="store_true");args=parser.parse_args()
    if websockets is None:raise SystemExit("缺少 websockets，请先安装 requirements.txt")
    config=load_config(Path(args.config));bridge=Bridge(config,args.simulate);server=ThreadingHTTPServer((config["websocket_host"],int(config["openvibe_command_port"])),OpenViBECommandHandler);OpenViBECommandHandler.bridge=bridge;threading.Thread(target=server.serve_forever,daemon=True).start();threading.Thread(target=bridge.openvibe_watchdog,daemon=True).start();bridge.loop=asyncio.get_running_loop();signal.signal(signal.SIGINT,bridge.stop);signal.signal(signal.SIGTERM,bridge.stop)
    try:
        async with websockets.serve(bridge.websocket_handler,config["websocket_host"],int(config["websocket_port"])):
            print(f"[WS] ws://{config['websocket_host']}:{config['websocket_port']}");print(f"[HTTP] http://{config['websocket_host']}:{config['openvibe_command_port']}");await asyncio.to_thread(bridge.simulate_loop if args.simulate else bridge.read_lsl_loop)
    finally:server.shutdown();server.server_close()

if __name__=="__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
