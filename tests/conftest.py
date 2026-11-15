"""Test fixtures: start the real app (and a fake LLM) with uvicorn on free ports."""

from __future__ import annotations

import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

API_KEY = "test-key-123"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("server did not start")


PORT = _free_port()
os.environ.update({
    "PORT": str(PORT),
    "API_KEY": API_KEY,
    "PUBLIC_BASE_URL": f"http://127.0.0.1:{PORT}",
})
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)


@pytest.fixture(scope="session")
def base_url() -> str:
    from app.main import app

    server = _serve(app, PORT)
    yield f"http://127.0.0.1:{PORT}"
    server.should_exit = True


@pytest.fixture()
def client(base_url):
    with httpx.Client(base_url=base_url, headers={"X-API-Key": API_KEY}, timeout=30) as c:
        c.post("/api/reset")
        yield c


@pytest.fixture(scope="session")
def fake_llm_url() -> str:
    """A minimal OpenAI-compatible server that scripts a tool-calling conversation."""
    from fastapi import FastAPI

    llm = FastAPI()

    @llm.post("/v1/chat/completions")
    async def completions(body: dict) -> dict:
        messages = body["messages"]
        tool_names = {t["function"]["name"] for t in body.get("tools", [])}
        tool_results = [m for m in messages if m["role"] == "tool"]
        if not tool_results:
            assert {"get_vehicle_status", "ask_charging_planner", "start_navigation"} <= tool_names
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "set_cabin_temperature", "arguments": '{"temperature_c": 22}'}},
                {"id": "call_2", "type": "function",
                 "function": {"name": "ask_charging_planner", "arguments": '{"destination": "Berlin"}'}},
            ]}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": f"Done. Planner said: {tool_results[-1]['content'][:80]}"}
            finish = "stop"
        return {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "message": message, "finish_reason": finish}]}

    port = _free_port()
    server = _serve(llm, port)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
