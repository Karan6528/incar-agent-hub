# Notes: REST vs MCP vs A2A

These are my notes after building the prototype. I wanted to understand when each approach makes sense for connecting an AI assistant to car and backend services.

## Quick comparison

| | REST API + tool calling | MCP | A2A |
|---|---|---|---|
| Connects | one app to one API | any AI assistant to tools/data | an agent to another agent |
| Discovery | manual, you write the tool definition yourself | client calls `tools/list` | agent card at `/.well-known/agent-card.json` |
| Who decides | my code | the LLM picks the tools | the remote agent decides how to solve the task |
| State | stateless | sessions (I run it stateless) | tasks with states (working, completed, failed...) |
| Auth | anything | OAuth 2.1 in the spec, API key works | declared in the agent card |
| Effort here | - | ~70 lines for 5 tools | ~120 lines incl. agent card |

## How I used them

- **MCP** for the car functions. The nice part is that the host agent discovers the tools at runtime. When I added `get_route_info` later, I didn't have to change anything in the agent.
- **A2A** for the charging planner. The host agent just sends destination + battery level and gets back a finished plan. In real life this agent could belong to another team or a charging provider.
- **Tool calling** connects it all: the LLM gets the MCP tools plus one tool that calls the A2A agent, and decides the order itself. With the real LLM it checked the battery first before planning. My offline rules don't do that.

## Security things I noticed

1. **Not every function should be available to an LLM.** I marked tools as read-only or not (MCP tool annotations). Climate and navigation are fine, but nothing driving-related should ever be exposed.
2. **Validate on the server.** The 16-28 °C limit is checked in the car service, not in the prompt. "Set the temperature to 35 degrees" gets rejected.
3. **Prompt injection.** Tool results go back into the LLM context. If a charger name came from a third party, it could contain instructions. Anything that changes the car should probably need a confirmation from the driver.
4. **Auth.** I used one shared API key and compare it with `hmac.compare_digest`. A real system would need OAuth 2.1 with per-user and per-vehicle scopes and short-lived tokens.
5. **Logging.** Every tool call is logged with arguments, result and time. That was really useful for debugging and would matter for audits too.
6. **DNS rebinding protection** in the MCP SDK blocked requests behind the App Runner domain. I turned it off and rely on the API key instead.

## Problems I ran into

- **SDKs changing.** MCP Python SDK 2.x renamed `FastMCP` to `MCPServer`, and a2a-sdk 1.x switched to protobuf types and the A2A 1.0 spec (`SendMessage` instead of `message/send`). I pinned `mcp<2` and `a2a-sdk<2`.
- **Models disappearing.** Groq removed `llama-3.3-70b-versatile` from the free tier, so I switched to `openai/gpt-oss-120b`. That model returns an extra `reasoning` field that I had to strip before sending the conversation back.
- **A2A has overhead** (agent card fetch, task wrapper). It's worth it for bigger tasks, but for something simple like reading the battery level MCP is enough.
- **Latency.** Tool calls take ~2-20 ms locally, the LLM takes hundreds of ms. The protocols aren't the bottleneck.

## My conclusion

MCP is a good default for exposing car and backend functions to any assistant. A2A makes sense when you want to hand off a whole task to a specialised agent. Plain REST is still fine for normal service-to-service calls without an LLM in between.
