"""Main FastAPI app.

Runs everything in one service for now:
  /mcp            MCP server
  /a2a/charging   charging agent (agent card at /.well-known/agent-card.json)
  /api/chat       host agent
  /               small UI for testing
The parts only call each other over HTTP, so they could be split later.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.auth import ApiKeyMiddleware
from app.charging_agent import A2A_PATH, ChargingPlannerExecutor, build_agent_card
from app.config import get_settings
from app.host_agent import HostAgent
from app.mcp_server import mcp
from app.vehicle import vehicle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("incar-agent-hub")
STATIC = Path(__file__).parent / "static"

settings = get_settings()
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp.session_manager.run():
        log.info("MCP server ready at /mcp; A2A agent at %s; LLM %s",
                 A2A_PATH, "enabled" if get_settings().llm_enabled else "disabled (offline mode)")
        yield


app = FastAPI(
    title="In-Car AI Agent Hub",
    description="Prototype: connecting AI assistants to vehicle services with MCP and A2A.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(ApiKeyMiddleware)

# A2A: Charging Planner agent
agent_card = build_agent_card(settings.public_base_url)
a2a_handler = DefaultRequestHandler(ChargingPlannerExecutor(), InMemoryTaskStore(), agent_card)
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card),
    jsonrpc_routes=create_jsonrpc_routes(a2a_handler, rpc_url=A2A_PATH),
)


# Host agent / demo API
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000, examples=["I'm cold and need to charge before Berlin"])


def _root_cause(exc: BaseException) -> BaseException:
    # anyio wraps errors in ExceptionGroups, dig out the real one for the UI
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict:
    try:
        result = await HostAgent(get_settings()).run(req.message)
    except Exception as exc:
        log.exception("Agent run failed")
        root = _root_cause(exc)
        raise HTTPException(status_code=502, detail=f"Agent error: {type(root).__name__}: {root}") from exc
    return {**result.as_dict(), "vehicle": vehicle.status()}


@app.get("/api/vehicle")
async def vehicle_status() -> dict:
    return vehicle.status()


@app.post("/api/reset")
async def reset() -> dict:
    vehicle.reset()
    return vehicle.status()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "llm": get_settings().llm_enabled}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# has to be mounted last, otherwise it swallows the other routes
app.mount("/", mcp_app)
