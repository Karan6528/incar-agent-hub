"""Integration tests over real HTTP: MCP, A2A and authentication."""

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, new_message, new_text_part
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tests.conftest import API_KEY


@pytest.mark.parametrize("path", ["/api/vehicle", "/mcp", "/a2a/charging"])
def test_protected_endpoints_reject_missing_or_wrong_key(base_url, path):
    assert httpx.post(base_url + path, json={}).status_code == 401
    assert httpx.post(base_url + path, json={}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_bearer_token_is_accepted(base_url):
    r = httpx.get(base_url + "/api/vehicle", headers={"Authorization": f"Bearer {API_KEY}"})
    assert r.status_code == 200


def test_public_endpoints(base_url):
    assert httpx.get(base_url + "/health").json()["status"] == "ok"
    card = httpx.get(base_url + "/.well-known/agent-card.json").json()
    assert card["name"] == "Charging Planner Agent"
    assert card["skills"][0]["id"] == "plan_charging"
    assert "apiKey" in card["securitySchemes"]


async def test_mcp_tool_discovery_and_call(base_url, client):
    async with (
        httpx.AsyncClient(headers={"X-API-Key": API_KEY}) as http,
        streamable_http_client(f"{base_url}/mcp", http_client=http) as (r, w, _),
        ClientSession(r, w) as session,
    ):
        await session.initialize()
        names = {t.name for t in (await session.list_tools()).tools}
        assert names == {"get_vehicle_status", "set_cabin_temperature", "get_route_info",
                         "find_charging_stations", "start_navigation"}
        res = await session.call_tool("set_cabin_temperature", {"temperature_c": 23})
        assert not res.isError
        bad = await session.call_tool("set_cabin_temperature", {"temperature_c": 40})
        assert bad.isError
    assert client.get("/api/vehicle").json()["cabin_temp_c"] == 23


async def _a2a_send(base_url, parts):
    async with httpx.AsyncClient(headers={"X-API-Key": API_KEY}) as http:
        a2a = await ClientFactory(ClientConfig(streaming=False, httpx_client=http)).create_from_url(base_url)
        msg = new_message(parts, role=Role.ROLE_USER)
        return [e async for e in a2a.send_message(SendMessageRequest(message=msg))][-1].task


async def test_a2a_text_request_returns_completed_plan(base_url):
    task = await _a2a_send(base_url, [new_text_part("Plan charging to Hamburg with 60%")])
    assert MessageToDict(task.status)["state"] == "TASK_STATE_COMPLETED"
    plan = get_data_parts(task.artifacts[0].parts)[0]
    assert plan["destination"] == "Hamburg" and plan["feasible"]


async def test_a2a_bad_request_fails_cleanly(base_url):
    task = await _a2a_send(base_url, [new_text_part("hello")])
    assert MessageToDict(task.status)["state"] == "TASK_STATE_FAILED"
