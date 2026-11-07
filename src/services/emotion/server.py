"""
Stream B: Emotion Streaming Server
Listens on local port 5001 and emits EmotionEvent payloads conforming to src/contracts/events.ts.
Provides:
  - WebSocket stream at ws://localhost:5001/ws or ws://localhost:5001/
  - HTTP Server-Sent Events (SSE) at http://localhost:5001/events
  - HTTP JSON snapshot at http://localhost:5001/current
  - HTTP health status at http://localhost:5001/health
"""

import sys
import os
import json
import time
import argparse
import asyncio
import logging
from typing import Set

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from src.services.emotion.emotion_detector import EmotionDetector, MockEmotionGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [Stream B: Emotion] %(message)s")
logger = logging.getLogger("emotion-service")


class EmotionStreamingServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 5001, is_mock: bool = False, fps: float = 20.0):
        self.host = host
        self.port = port
        self.is_mock = is_mock
        self.target_interval = 1.0 / max(1.0, fps)
        self.latest_event = None

        if self.is_mock:
            logger.info("Initializing in MOCK mode (synthetic escalating stress curves enabled).")
            self.detector = MockEmotionGenerator(cycle_duration_sec=35.0)
        else:
            logger.info("Initializing in HARDWARE mode (OpenCV webcam detection).")
            self.detector = EmotionDetector()
            self.detector.open_camera(0)

    def get_latest_payload(self) -> dict:
        if self.is_mock:
            self.latest_event = self.detector.get_next_event()
        else:
            self.latest_event = self.detector.process_frame()
        return self.latest_event

    async def run(self):
        try:
            from aiohttp import web
            app = web.Application()
            ws_clients: Set[web.WebSocketResponse] = set()

            async def handle_health(request):
                return web.json_response({
                    "status": "live",
                    "service": "Stream B - Emotion Detect Lead",
                    "mock": self.is_mock,
                    "port": self.port,
                })

            async def handle_current(request):
                payload = self.latest_event or self.get_latest_payload()
                return web.json_response(payload)

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
                    payload = self.latest_event or self.get_latest_payload()
                    data = f"data: {json.dumps(payload)}\n\n"
                    await response.write(data.encode("utf-8"))
                    await asyncio.sleep(self.target_interval)
                return response

            async def handle_ws(request):
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                ws_clients.add(ws)
                logger.info(f"WebSocket client connected from {request.remote}. Total clients: {len(ws_clients)}")
                try:
                    async for msg in ws:
                        pass
                finally:
                    ws_clients.remove(ws)
                    logger.info("WebSocket client disconnected.")
                return ws

            app.router.add_get("/", handle_current)
            app.router.add_get("/health", handle_health)
            app.router.add_get("/current", handle_current)
            app.router.add_get("/events", handle_sse)
            app.router.add_get("/ws", handle_ws)
            app.router.add_get("/ws/stream", handle_ws)

            # Background broadcast task
            async def broadcast_loop():
                while True:
                    payload = self.get_latest_payload()
                    if ws_clients:
                        msg_text = json.dumps(payload)
                        for ws in list(ws_clients):
                            if not ws.closed:
                                try:
                                    await ws.send_str(msg_text)
                                except Exception:
                                    pass
                    await asyncio.sleep(self.target_interval)

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
            logger.info(f"Stream B Emotion Service live at http://{self.host}:{self.port} (WS & SSE)")
            await broadcast_loop()

        except ImportError:
            # Fallback pure asyncio socket / HTTP implementation if aiohttp is not yet installed
            logger.info("aiohttp not found, falling back to pure asyncio HTTP/WebSocket streaming server...")
            await self._run_std_asyncio_server()

    async def _run_std_asyncio_server(self):
        """Zero-dependency fallback HTTP/SSE/JSON server using pure python asyncio."""
        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                line = await reader.readline()
                request_line = line.decode().strip()
                if not request_line:
                    writer.close()
                    return
                parts = request_line.split()
                path = parts[1] if len(parts) > 1 else "/"

                # Consume rest of headers
                while True:
                    h = await reader.readline()
                    if not h or h == b"\r\n" or h == b"\n":
                        break

                if "/health" in path:
                    body = json.dumps({"status": "live", "service": "emotion", "mock": self.is_mock}).encode()
                    writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nAccess-Control-Allow-Origin: *\r\n\r\n".encode() + body)
                    await writer.drain()
                    writer.close()
                elif "/events" in path or "/stream" in path:
                    # SSE stream
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nAccess-Control-Allow-Origin: *\r\n\r\n")
                    await writer.drain()
                    while True:
                        payload = self.get_latest_payload()
                        chunk = f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        writer.write(chunk)
                        await writer.drain()
                        await asyncio.sleep(self.target_interval)
                else:
                    payload = self.latest_event or self.get_latest_payload()
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
        logger.info(f"Stream B Emotion Server (Standard Library) listening on {self.host}:{self.port}")
        async with server:
            while True:
                self.get_latest_payload()
                await asyncio.sleep(self.target_interval)


def main():
    parser = argparse.ArgumentParser(description="Stream B: Emotion Detect Lead Service")
    parser.add_argument("--port", type=int, default=5001, help="Port to bind server (default: 5001)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind server (default: 0.0.0.0)")
    parser.add_argument("--mock", action="store_true", help="Enable synthetic escalating stress curves for demo testing")
    parser.add_argument("--fps", type=float, default=20.0, help="Frames / inferences per second (default: 20)")
    args = parser.parse_args()

    server = EmotionStreamingServer(host=args.host, port=args.port, is_mock=args.mock, fps=args.fps)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Emotion service stopping...")


if __name__ == "__main__":
    main()
