"""
Stream D: Fusion Orchestrator Server
Listens on local port 8080.
Subscribes to Emotion (:5001) & Action (:5002), computes fusionScore,
and broadcasts TakeoverTriggerEvent to Stream A (Frontend) via WebSocket /ws/stream.
"""

import sys
import os
import json
import time
import argparse
import asyncio
import logging
from typing import Set

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.orchestrator.fusion_engine import FusionEngine
from src.orchestrator.stream_listener import StreamListener

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [Stream D: Core] %(message)s")
logger = logging.getLogger("orchestrator-server")


class OrchestratorServer:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        emotion_port: int = 5001,
        action_port: int = 5002,
        w1: float = 0.5,
        w2: float = 0.5,
        threshold: float = 0.8,
    ):
        self.host = host
        self.port = port
        self.engine = FusionEngine(w1=w1, w2=w2, threshold=threshold)
        self.listener = StreamListener(
            emotion_url=f"http://localhost:{emotion_port}/current",
            action_url=f"http://localhost:{action_port}/current",
            on_emotion_event=self.engine.update_emotion,
            on_action_event=self.engine.update_action,
        )
        self.ws_clients: Set = set()

    async def broadcast(self, payload: dict):
        if not self.ws_clients:
            return
        msg = json.dumps(payload)
        for ws in list(self.ws_clients):
            try:
                if hasattr(ws, "send_str"):
                    await ws.send_str(msg)
                elif hasattr(ws, "write"):
                    ws.write(f"data: {msg}\n\n".encode("utf-8"))
                    await ws.drain()
            except Exception:
                pass

    async def run(self):
        # Start background stream listener
        asyncio.create_task(self.listener.start())

        try:
            from aiohttp import web
            app = web.Application()

            async def handle_health(request):
                return web.json_response({
                    "status": "live",
                    "service": "Stream D - Fusion Orchestrator & Antigravity Lead",
                    "port": self.port,
                })

            async def handle_status(request):
                return web.json_response(self.engine.get_current_event())

            async def handle_manual_trigger(request):
                self.engine._trigger_takeover()
                return web.json_response({"success": True, "event": self.engine.get_current_event()})

            async def handle_ws(request):
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                self.ws_clients.add(ws)
                logger.info(f"Frontend HUD connected from {request.remote}. Total subscribers: {len(self.ws_clients)}")
                # Send immediate current snapshot
                await ws.send_str(json.dumps(self.engine.get_current_event()))
                try:
                    async for msg in ws:
                        pass
                finally:
                    self.ws_clients.discard(ws)
                    logger.info("Frontend HUD disconnected.")
                return ws

            async def handle_sse(request):
                response = web.StreamResponse(
                    status=200,
                    reason="OK",
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "Access-Control-Allow-Origin": "*",
                    },
                )
                await response.prepare(request)
                while True:
                    payload = self.engine.get_current_event()
                    data = f"data: {json.dumps(payload)}\n\n"
                    await response.write(data.encode("utf-8"))
                    await asyncio.sleep(0.1)
                return response

            app.router.add_get("/", handle_status)
            app.router.add_get("/health", handle_health)
            app.router.add_get("/status", handle_status)
            app.router.add_post("/api/trigger", handle_manual_trigger)
            app.router.add_get("/events", handle_sse)
            app.router.add_get("/ws", handle_ws)
            app.router.add_get("/ws/stream", handle_ws)

            async def broadcast_loop():
                while True:
                    event = self.engine.get_current_event()
                    if self.ws_clients:
                        msg = json.dumps(event)
                        for ws in list(self.ws_clients):
                            if not ws.closed:
                                try:
                                    await ws.send_str(msg)
                                except Exception:
                                    pass
                    await asyncio.sleep(0.1)

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
            logger.info(f"Fusion Orchestrator live on port {self.port}. Broadcasting to Stream A HUD.")
            await broadcast_loop()

        except ImportError:
            logger.info("aiohttp not installed, launching pure asyncio Orchestrator server...")
            await self._run_std_asyncio_server()

    async def _run_std_asyncio_server(self):
        """Zero-dependency fallback Orchestrator server."""
        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                line = await reader.readline()
                request_line = line.decode().strip()
                if not request_line:
                    writer.close()
                    return
                parts = request_line.split()
                path = parts[1] if len(parts) > 1 else "/"

                while True:
                    h = await reader.readline()
                    if not h or h == b"\r\n" or h == b"\n":
                        break

                if "/health" in path:
                    body = json.dumps({"status": "live", "service": "orchestrator"}).encode()
                    writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nAccess-Control-Allow-Origin: *\r\n\r\n".encode() + body)
                    await writer.drain()
                    writer.close()
                elif "/events" in path or "/stream" in path:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nAccess-Control-Allow-Origin: *\r\n\r\n")
                    await writer.drain()
                    while True:
                        payload = self.engine.get_current_event()
                        writer.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                        await writer.drain()
                        await asyncio.sleep(0.1)
                else:
                    payload = self.engine.get_current_event()
                    body = json.dumps(payload).encode()
                    writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nAccess-Control-Allow-Origin: *\r\n\r\n".encode() + body)
                    await writer.drain()
                    writer.close()
            except Exception:
                try:
                    writer.close()
                except Exception:
                    pass

        server = await asyncio.start_server(handle_client, self.host, self.port)
        logger.info(f"Fusion Orchestrator (Standard Library) listening on {self.host}:{self.port}")
        async with server:
            while True:
                await asyncio.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description="Stream D: Orchestrator & Antigravity Lead Service")
    parser.add_argument("--port", type=int, default=8080, help="Port for Orchestrator (default: 8080)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    parser.add_argument("--emotion-port", type=int, default=5001, help="Emotion stream port (default: 5001)")
    parser.add_argument("--action-port", type=int, default=5002, help="Action stream port (default: 5002)")
    parser.add_argument("--w1", type=float, default=0.5, help="Weight for stressIndex (default: 0.5)")
    parser.add_argument("--w2", type=float, default=0.5, help="Weight for panicScore (default: 0.5)")
    parser.add_argument("--threshold", type=float, default=0.8, help="Fusion trigger threshold (default: 0.8)")
    args = parser.parse_args()

    orchestrator = OrchestratorServer(
        host=args.host,
        port=args.port,
        emotion_port=args.emotion_port,
        action_port=args.action_port,
        w1=args.w1,
        w2=args.w2,
        threshold=args.threshold,
    )
    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Fusion Orchestrator stopping...")


if __name__ == "__main__":
    main()
