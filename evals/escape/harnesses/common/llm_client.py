"""Minimal OpenRouter chat-completions client with tool-call support.

Returns the raw OpenRouter response so the harness can inspect tool
calls, content, and finish reason without this module taking opinions
about the loop shape. Reads OPENROUTER_API_KEY from env (matches the
existing demo's provider).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class LlmResponse:
    content: str | None
    tool_calls: list[dict]
    raw: dict


class OpenRouterClient:
    def __init__(self, model: str, api_key: str | None = None, timeout: float = 60.0):
        self.model = model
        self.api_key = api_key or os.environ["OPENROUTER_API_KEY"]
        self.timeout = timeout

    def chat(self, messages: list[dict], tools: list[dict]) -> LlmResponse:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                OPENROUTER_URL,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            data = r.json()
        msg = data["choices"][0]["message"]
        return LlmResponse(
            content=msg.get("content"),
            tool_calls=msg.get("tool_calls", []) or [],
            raw=data,
        )
