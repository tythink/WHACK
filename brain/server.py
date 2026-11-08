"""FastAPI service for Role 3: Brain Lead.

Provides:
- GET  /health: Service status & loaded tools
- GET  /diagnose: Stub phase mock diagnostic
- POST /diagnose: Multimodal streaming or JSON diagnosis
- POST /tools/execute: Sandboxed tool runner
"""

import argparse
import asyncio
from typing import Dict, Any, Optional
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from brain.config import BRAIN_PORT, BRAIN_HOST, DEMO_REPO_DIR, CORE_SERVER_URL
from brain.models import DiagnosisResponse, DiagnoseRequest
from brain.agent import DiagnosticEngine, broadcast_to_core
from brain.tools import execute_tool, TOOL_DEFINITIONS

app = FastAPI(
    title="WHACK Brain Service",
    description="Multimodal Codebase Context, Diagnosis & Personality Engine (Role 3)",
    version="1.0.0",
)

# Enable CORS for local Electron / Core server requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = DiagnosticEngine()

@app.get("/")
@app.get("/health")
def health_check():
    """Health check endpoint reporting service status and configuration."""
    return {
        "status": "online",
        "service": "brain",
        "role": 3,
        "demo_repo_path": str(DEMO_REPO_DIR),
        "core_server_url": CORE_SERVER_URL,
        "available_tools": [t["name"] for t in TOOL_DEFINITIONS],
    }

@app.get("/diagnose")
def get_stub_diagnose():
    """Stub Phase mock diagnostic conforming to AGENT_ROLE3_BRAIN.md roadmap (0:00 - 0:45)."""
    return {
        "summary": "Index error on line 42",
        "file": "utils.py",
        "lines": [40, 45],
        "confidence": 0.95,
        "quip": "Computers do what you say, not what you want.",
        "uncertain": False,
        "hintLevel": "nudge",
    }

@app.post("/diagnose")
async def post_diagnose(req: DiagnoseRequest, request: Request):
    """Diagnose user problem given multimodal signals. Supports SSE streaming or direct JSON."""
    accept_header = request.headers.get("accept", "")
    wants_stream = req.stream or "text/event-stream" in accept_header

    if wants_stream:
        return StreamingResponse(
            engine.diagnose_stream(req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Standard JSON response
    diagnosis = await engine.diagnose(req)
    
    # Broadcast asynchronously to Core Server if active
    asyncio.create_task(broadcast_to_core(diagnosis))
    
    return diagnosis

@app.post("/tools/execute")
def run_tool_endpoint(payload: Dict[str, Any]):
    """Execute a sandboxed inspection tool (strictly within demo-repo/)."""
    tool_name = payload.get("tool") or payload.get("name")
    args = payload.get("args") or payload.get("arguments") or {}
    if not tool_name:
        raise HTTPException(status_code=400, detail="Missing required 'tool' or 'name' field")

    result = execute_tool(tool_name, args)
    if "error" in result and "Sandbox violation" in result["error"]:
        raise HTTPException(status_code=403, detail=result["error"])
    return result

def main():
    parser = argparse.ArgumentParser(description="Start WHACK Brain Service")
    parser.add_argument("--port", type=int, default=BRAIN_PORT, help="Port to listen on")
    parser.add_argument("--host", type=str, default=BRAIN_HOST, help="Host to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload")
    args = parser.parse_args()

    print(f"🧠 [Role 3: Brain] Starting server on http://{args.host}:{args.port}")
    uvicorn.run("brain.server:app", host=args.host, port=args.port, reload=args.reload)

if __name__ == "__main__":
    main()
