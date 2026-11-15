"""Settings from env vars (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    port: int
    api_key: str
    public_base_url: str
    mcp_url: str
    a2a_agent_url: str
    llm_api_key: str
    llm_base_url: str | None
    llm_model: str

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


def get_settings() -> Settings:
    port = int(os.getenv("PORT", "8000"))
    local = f"http://127.0.0.1:{port}"
    return Settings(
        port=port,
        api_key=os.getenv("API_KEY", ""),
        public_base_url=os.getenv("PUBLIC_BASE_URL", f"http://localhost:{port}"),
        mcp_url=os.getenv("MCP_URL", f"{local}/mcp"),
        a2a_agent_url=os.getenv("A2A_AGENT_URL", local),
        llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
    )
