"""FastAPI service for Role 3: Brain Service.

Provides:
- GET  /health: Service status, active LLM provider/model, key readiness, default workspace
- POST /diagnose: Multimodal streaming (SSE) or JSON diagnosis (broadcasts to Core)
- POST /chat: Interactive follow-up mentoring chat (never broadcasts to Core)
- POST /wiki/update: Refresh dynamic workspace architecture wiki
- POST /tools/execute: Sandboxed read-only workspace tool runner
- POST /apply-fix & POST /rollback: Checkpointed workspace patch application & rollback
"""

import argparse
import asyncio
from typing import Dict, Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from brain.config import (
    BRAIN_PORT,
    BRAIN_HOST,
    DEMO_REPO_DIR,
    CORE_SERVER_URL,
    get_active_api_key,
    get_active_model,
    get_active_provider,
    refresh_env_files,
    resolve_repo_dir,
)
from brain.models import (
    ApplyFixRequest,
    ChatRequest,
    ChatResponse,
    DiagnoseRequest,
    RollbackRequest,
    WikiUpdateRequest,
)
from brain.agent import DiagnosticEngine, broadcast_to_core
from brain.tools import (
    execute_tool,
    TOOL_DEFINITIONS,
    apply_fix_to_repo,
    rollback_repo_checkpoint,
    SandboxViolationError,
)
from brain.wiki import update_repo_wiki

app = FastAPI(
    title="WHACK Brain Service",
    description="Multimodal Codebase Context, Diagnosis & Personality Engine (Role 3)",
    version="2.0.0",
)

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
def health_check() -> Dict[str, Any]:
    """Health check endpoint reporting service status, provider/model, and key configuration."""
    refresh_env_files(force=False)
    prov = get_active_provider()
    return {
        "status": "online",
        "service": "brain",
        "role": 3,
        "provider": prov,
        "model": get_active_model(prov),
        "apiKeyConfigured": bool(get_active_api_key(prov)),
        "defaultRepo": str(DEMO_REPO_DIR),
        "demo_repo_path": str(DEMO_REPO_DIR),
        "core_server_url": CORE_SERVER_URL,
        "available_tools": [t["name"] for t in TOOL_DEFINITIONS],
    }


@app.post("/reload-env")
def reload_env_endpoint() -> Dict[str, Any]:
    """Force hot-reload of `.env` / `brain/.env` and return fresh health snapshot."""
    refresh_env_files(force=True)
    return health_check()


@app.post("/diagnose")
async def post_diagnose(req: DiagnoseRequest, request: Request):
    """Diagnose developer issue given multimodal signals. Supports SSE streaming or JSON."""
    accept_header = request.headers.get("accept", "")
    wants_stream = bool(req.stream) or ("text/event-stream" in accept_header)

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

    result = await engine.diagnose(req)
    asyncio.create_task(broadcast_to_core(result.model_dump()))
    return result


@app.post("/chat", response_model=ChatResponse)
async def post_chat(req: ChatRequest) -> ChatResponse:
    """Interactive follow-up chat with WHACK Brain (does NOT broadcast to Core)."""
    return await engine.chat(req)


@app.post("/wiki/update")
async def post_wiki_update(req: WikiUpdateRequest) -> Dict[str, Any]:
    """Refresh architecture wiki for the requested workspace."""
    target_repo = resolve_repo_dir(req.repoPath)
    return await asyncio.to_thread(update_repo_wiki, target_repo, req.allowModel, req.force)


@app.post("/tools/execute")
def run_tool_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a sandboxed inspection tool within the requested or default workspace."""
    tool_name = payload.get("tool") or payload.get("name")
    args = payload.get("args") or payload.get("arguments") or {}
    repo_path = payload.get("repoPath") or payload.get("workspaceRoot") or payload.get("repo_path")
    if not tool_name:
        raise HTTPException(status_code=400, detail="Missing required 'tool' or 'name' field")

    target_repo = resolve_repo_dir(repo_path)
    result = execute_tool(tool_name, args, repo_dir=target_repo)
    if "error" in result and "Sandbox violation" in result["error"]:
        raise HTTPException(status_code=403, detail=result["error"])
    return result


@app.post("/apply-fix")
def apply_fix_endpoint(req: ApplyFixRequest):
    """Verify oldText, create checkpoint commit in target workspace, and apply fix."""
    try:
        repo_dir = resolve_repo_dir(req.repoPath)
        res = apply_fix_to_repo(req.fix, repo_dir=repo_dir)
        if not res.get("success"):
            return JSONResponse(status_code=400, content=res)
        return res
    except Exception as e:
        if isinstance(e, SandboxViolationError) or "Sandbox violation" in str(e) or "Access denied" in str(e):
            raise HTTPException(status_code=403, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rollback")
def rollback_endpoint(req: RollbackRequest):
    """Roll back target workspace repository to checkpoint hash."""
    try:
        repo_dir = resolve_repo_dir(req.repoPath)
        res = rollback_repo_checkpoint(req.checkpointHash, repo_dir=repo_dir)
        if not res.get("success"):
            return JSONResponse(status_code=400, content=res)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def main() -> None:
    parser = argparse.ArgumentParser(description="Start WHACK Brain Service")
    parser.add_argument("--port", type=int, default=BRAIN_PORT, help="Port to listen on")
    parser.add_argument("--host", type=str, default=BRAIN_HOST, help="Host to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload")
    args = parser.parse_args()

    print(f"🧠 [Role 3: Brain] Starting server on http://{args.host}:{args.port}")
    uvicorn.run("brain.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
