"""End-to-end tests of the host agent through /api/chat."""


def test_offline_agent_full_trip(client):
    r = client.post("/api/chat", json={"message": "I'm cold and need to charge before Berlin"}).json()
    tools = [(s["protocol"], s["tool"]) for s in r["steps"]]
    assert ("MCP", "set_cabin_temperature") in tools
    assert ("A2A", "ask_charging_planner") in tools
    assert tools[-1] == ("MCP", "start_navigation")
    assert r["vehicle"]["navigation"]["charging_stops"] == ["IONITY Ingolstadt Nord", "IONITY Leipzig-Schkeuditz"]
    assert r["vehicle"]["cabin_temp_c"] == 22


def test_offline_agent_reports_tool_errors(client):
    r = client.post("/api/chat", json={"message": "set temperature to 35 degrees"}).json()
    assert r["steps"][0]["ok"] is False
    assert "16.0 and 28.0" in r["answer"]


def test_llm_tool_calling_loop(client, fake_llm_url, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "fake")
    monkeypatch.setenv("LLM_BASE_URL", fake_llm_url)
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    r = client.post("/api/chat", json={"message": "I'm cold, going to Berlin"}).json()
    assert r["mode"] == "llm (fake-model)"
    assert [s["tool"] for s in r["steps"]] == ["set_cabin_temperature", "ask_charging_planner"]
    assert r["answer"].startswith("Done. Planner said:")
    assert r["vehicle"]["cabin_temp_c"] == 22


def test_empty_message_rejected(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
