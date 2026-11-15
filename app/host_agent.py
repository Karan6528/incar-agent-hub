"""Host agent - the assistant the driver actually talks to.

It connects to the MCP server, hands the tools to the LLM and lets the model
pick what to call. Charging questions get delegated to the charging agent over A2A.
If there's no LLM key it uses simple keyword rules instead (handy for testing).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, new_data_part, new_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import Settings, get_settings
from app.vehicle import DESTINATIONS_KM

SYSTEM_PROMPT = """You are the in-car AI assistant of an electric car.
Use the available tools to read and control the car; never invent vehicle data.
For trips: check the route, and if the car might need charging, call ask_charging_planner,
then start_navigation with the station_id values from the plan.
Answer briefly (2-4 sentences) in the driver's language."""

A2A_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_charging_planner",
        "description": "Delegate to the Charging Planner agent (A2A) to plan charging stops for a trip, "
                       "using the car's current battery level.",
        "parameters": {
            "type": "object",
            "properties": {"destination": {"type": "string", "description": "Destination city"}},
            "required": ["destination"],
        },
    },
}

MAX_TOOL_ROUNDS = 8


@dataclass
class Step:
    protocol: str  # "MCP" or "A2A"
    tool: str
    arguments: dict
    result: Any
    ok: bool = True
    ms: int = 0


@dataclass
class AgentResult:
    answer: str
    mode: str
    steps: list[Step] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"answer": self.answer, "mode": self.mode, "steps": [asdict(s) for s in self.steps]}


class ToolError(RuntimeError):
    pass


class Toolbox:
    # runs the tool calls (MCP or A2A) and keeps a log of each one for the UI

    def __init__(self, session: ClientSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.steps: list[Step] = []

    def _auth_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.settings.api_key} if self.settings.api_key else {}

    async def call(self, name: str, arguments: dict) -> Any:
        start = time.perf_counter()
        if name == "ask_charging_planner":
            protocol, (ok, result) = "A2A", await self._ask_charging_planner(**arguments)
        else:
            protocol, (ok, result) = "MCP", await self._call_mcp(name, arguments)
        self.steps.append(Step(protocol, name, arguments, result, ok, int((time.perf_counter() - start) * 1000)))
        if not ok:
            raise ToolError(f"{name} failed: {result}")
        return result

    async def _call_mcp(self, name: str, arguments: dict) -> tuple[bool, Any]:
        res = await self.session.call_tool(name, arguments)
        if res.structuredContent is not None:
            data = res.structuredContent
            # lists come back wrapped as {"result": [...]}
            if isinstance(data, dict) and set(data) == {"result"}:
                data = data["result"]
        else:
            text = "\n".join(getattr(c, "text", "") for c in res.content)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
        return (not res.isError), data

    async def _ask_charging_planner(self, destination: str) -> tuple[bool, Any]:
        _, status = await self._call_mcp("get_vehicle_status", {})
        payload = {"destination": destination, "battery_pct": status["battery_pct"]}
        async with httpx.AsyncClient(headers=self._auth_headers(), timeout=30) as http:
            client = await ClientFactory(ClientConfig(streaming=False, httpx_client=http)).create_from_url(
                self.settings.a2a_agent_url
            )
            message = new_message([new_data_part(payload)], role=Role.ROLE_USER)
            task = None
            async for event in client.send_message(SendMessageRequest(message=message)):
                if event.HasField("task"):
                    task = event.task
        if task is None:
            return False, {"error": "No task returned by the charging planner."}
        state = MessageToDict(task.status).get("state", "")
        for artifact in task.artifacts:
            for data in get_data_parts(artifact.parts):
                return state == "TASK_STATE_COMPLETED", data
        message_text = " ".join(p.text for p in task.status.message.parts if p.text)
        return False, {"error": message_text or state}


class HostAgent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def run(self, user_message: str) -> AgentResult:
        headers = {"X-API-Key": self.settings.api_key} if self.settings.api_key else {}
        async with (
            httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, read=300)) as http,
            streamable_http_client(self.settings.mcp_url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            toolbox = Toolbox(session, self.settings)
            if self.settings.llm_enabled:
                answer = await self._run_llm(user_message, mcp_tools, toolbox)
                mode = f"llm ({self.settings.llm_model})"
            else:
                answer = await self._run_offline(user_message, toolbox)
                mode = "offline (rule-based, no LLM key set)"
            return AgentResult(answer=answer, mode=mode, steps=toolbox.steps)

    # LLM tool-calling loop
    async def _run_llm(self, user_message: str, mcp_tools: list, toolbox: Toolbox) -> str:
        from openai import AsyncOpenAI

        llm = AsyncOpenAI(api_key=self.settings.llm_api_key, base_url=self.settings.llm_base_url)
        tools = [
            {"type": "function",
             "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema}}
            for t in mcp_tools
        ] + [A2A_TOOL]
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await llm.chat.completions.create(model=self.settings.llm_model, messages=messages, tools=tools)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content or ""
            # gpt-oss returns an extra `reasoning` field, Groq rejects it if we send it back
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in msg.tool_calls
                ],
            })
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                    result = await toolbox.call(call.function.name, args)
                except Exception as exc:  # let the model see the error and retry
                    result = {"error": str(exc)}
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, default=str)})
        return "Sorry, I could not finish that request within the tool-call limit."

    # Offline fallback (deterministic, no LLM)
    async def _run_offline(self, user_message: str, toolbox: Toolbox) -> str:
        try:
            return await self._offline_rules(user_message.lower(), toolbox)
        except ToolError as exc:
            return f"Sorry, something went wrong: {exc}"

    async def _offline_rules(self, text: str, toolbox: Toolbox) -> str:
        parts: list[str] = []

        explicit = re.search(r"(\d{2}(?:\.\d)?)\s*(?:°|degrees|grad|c\b)", text)
        if explicit:
            target = float(explicit.group(1))
        elif re.search(r"\b(cold|freezing|chilly|kalt)\b", text):
            target = 22.0
        elif re.search(r"\b(hot|warm|heiß|heiss)\b", text):
            target = 20.0
        else:
            target = None
        if target is not None:
            try:
                await toolbox.call("set_cabin_temperature", {"temperature_c": target})
                parts.append(f"I've set the cabin to {target:g} °C.")
            except ToolError as exc:
                parts.append(f"I couldn't change the temperature ({exc}).")

        destination = next((d for d in DESTINATIONS_KM if re.search(rf"\b{d}\b", text)), None)
        if destination:
            route = await toolbox.call("get_route_info", {"destination": destination})
            stop_ids: list[str] = []
            if not route.get("reachable_without_charging") or re.search(r"charg|lade", text):
                plan = await toolbox.call("ask_charging_planner", {"destination": destination})
                if isinstance(plan, dict) and plan.get("feasible"):
                    stop_ids = [s["station_id"] for s in plan.get("stops", [])]
                    parts.append(plan["summary"])
                else:
                    parts.append(f"The charging planner couldn't plan this trip: {plan}")
            await toolbox.call("start_navigation", {"destination": destination, "charging_stop_ids": stop_ids})
            parts.append(f"Navigation to {destination.title()} ({route['distance_km']:.0f} km) has started.")

        if not parts or re.search(r"\b(battery|range|status|akku|reichweite)\b", text):
            status = await toolbox.call("get_vehicle_status", {})
            parts.append(
                f"Battery is at {status['battery_pct']:.0f}% (about {status['range_km']:.0f} km range); "
                f"cabin is {status['cabin_temp_c']:g} °C."
            )
        return " ".join(parts)
