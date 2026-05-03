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
class LlmUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    request_id: str | None
    served_by_model: str | None

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "request_id": self.request_id,
            "served_by_model": self.served_by_model,
        }


@dataclass
class LlmResponse:
    content: str | None
    tool_calls: list[dict]
    raw: dict
    usage: LlmUsage


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
        u = data.get("usage") or {}
        usage = LlmUsage(
            prompt_tokens=int(u.get("prompt_tokens", 0)),
            completion_tokens=int(u.get("completion_tokens", 0)),
            total_tokens=int(u.get("total_tokens", 0)),
            request_id=data.get("id"),
            served_by_model=data.get("model"),
        )
        return LlmResponse(
            content=msg.get("content"),
            tool_calls=msg.get("tool_calls", []) or [],
            raw=data,
            usage=usage,
        )

    @staticmethod
    def fetch_credits(api_key: str | None = None, timeout: float = 10.0) -> dict | None:
        """Snapshot remaining credit balance. Returns None on error."""
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            return None
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(
                    "https://openrouter.ai/api/v1/credits",
                    headers={"Authorization": f"Bearer {key}"},
                )
                r.raise_for_status()
                return r.json()
        except Exception:
            return None
