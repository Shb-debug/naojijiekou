"""OpenViBE-only bridge for Alpha Dash.

The real-device pipeline is intentionally single-source:
OpenBCI GUI -> LSL -> OpenViBE -> this bridge -> WebSocket -> browser.
The bridge does not read LSL and does not run a second blink detector. This
prevents two independent detectors from producing competing jump events.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
try:
    import websockets
except ImportError:
    websockets = None

DEFAULT_CONFIG: dict[str, Any] = {
    "alpha_band": [8.0, 13.0], "alpha_ready_ratio": 0.16,
    "websocket_host": "127.0.0.1", "websocket_port": 8765, "openvibe_command_port": 8766,
}


def load_config(path: Path) -> dict[str, Any]:
    config = {**DEFAULT_CONFIG}
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8-sig")))
    return config


class Bridge:
    def __init__(self, config: dict[str, Any], simulate: bool = False) -> None:
        self.config, self.simulate = config, simulate
        self.clients: set[Any] = set()
        self.running, self.loop = True, None
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
        return {"type": message_type, "source": source or "OpenBCI Alpha Blink bridge", "detector": "simulation" if self.simulate else "OpenViBE", "openvibe_connected": self.openvibe_connected, "message": self.openvibe_message, "stream": stream_name or ("SIMULATED EEG" if self.simulate else ""), "simulated": self.simulate, "timestamp": time.time()}

    def receive_openvibe(self, event: str | None = None, confidence: float = 0.0, alpha_ratio_value: float | None = None, blink_score: float | None = None, blink_state: str = "") -> None:
        self.set_openvibe_status(True, "OpenViBE Alpha/Blink Python Box 已连接")
        if alpha_ratio_value is not None:
            ratio = max(0.0, min(1.0, float(alpha_ratio_value)))
            payload = {"type": "telemetry", "alpha_ratio": ratio, "alpha_confidence": max(0.0, min(1.0, ratio / .32)), "source": "OpenViBE", "timestamp": time.time()}
            if blink_state:
                payload["blink_state"] = blink_state
            if blink_score is not None:
                payload["blink_score"] = float(blink_score)
            payload["blink_confidence"] = max(0.0, min(1.0, float(confidence)))
            self.send(payload)
        if event == "blink":
            self.send({"type": "blink", "blink_confidence": max(0.0, min(1.0, float(confidence))), "blink_score": blink_score, "source": "OpenViBE", "timestamp": time.time()})

    def simulate_loop(self) -> None:
        self.send(self.connection_payload("status", stream_name="SIMULATED EEG"))
        next_blink = time.monotonic() + 1.4
        while self.running:
            now = time.monotonic()
            self.send({"type": "telemetry", "alpha_ratio": .24 + .04 * np.sin(now), "alpha_confidence": .78, "blink_confidence": .0, "simulated": True, "timestamp": time.time()})
            if now >= next_blink:
                self.send({"type": "blink", "blink_confidence": .94, "blink_score": 9.0, "simulated": True, "timestamp": time.time()})
                next_blink = now + 2.4
            time.sleep(.1)

    def wait_openvibe_loop(self) -> None:
        """Keep the bridge alive while OpenViBE owns all signal processing."""
        print("[MODE] OpenViBE-only: bridge will not read LSL or detect blinks locally")
        while self.running:
            time.sleep(1)

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
            self.bridge.receive_openvibe(event, float(query.get("confidence",[query.get("blink_confidence",[0])[0]])[0]), float(query["alpha_ratio"][0]) if "alpha_ratio" in query else None, float(query["blink_score"][0]) if "blink_score" in query else None, str(query.get("blink_state",[""])[0]));self.send_json({"ok":True,**self.bridge.connection_payload("status",source="OpenViBE")});return
        self.send_json({"ok":False,"error":"unknown endpoint"},404)
    def do_POST(self)->None:self.do_GET()
    def log_message(self,format_string:str,*args:Any)->None:return


async def main()->None:
    parser=argparse.ArgumentParser(description="OpenBCI Alpha/Blink WebSocket bridge");parser.add_argument("--config",default="ssvep_config.json");parser.add_argument("--simulate",action="store_true");args=parser.parse_args()
    if websockets is None:raise SystemExit("缺少 websockets，请先安装 requirements.txt")
    config=load_config(Path(args.config));bridge=Bridge(config,args.simulate);server=ThreadingHTTPServer((config["websocket_host"],int(config["openvibe_command_port"])),OpenViBECommandHandler);OpenViBECommandHandler.bridge=bridge;threading.Thread(target=server.serve_forever,daemon=True).start();threading.Thread(target=bridge.openvibe_watchdog,daemon=True).start();bridge.loop=asyncio.get_running_loop();signal.signal(signal.SIGINT,bridge.stop);signal.signal(signal.SIGTERM,bridge.stop)
    try:
        async with websockets.serve(bridge.websocket_handler,config["websocket_host"],int(config["websocket_port"])):
            print(f"[WS] ws://{config['websocket_host']}:{config['websocket_port']}");print(f"[HTTP] http://{config['websocket_host']}:{config['openvibe_command_port']}");await asyncio.to_thread(bridge.simulate_loop if args.simulate else bridge.wait_openvibe_loop)
    finally:server.shutdown();server.server_close()

if __name__=="__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
