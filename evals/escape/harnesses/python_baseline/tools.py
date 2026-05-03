"""Tool implementations for the permissive Python harness.

Two classes of tools:
 - Static (no per-task config): reverse_string, split_words, join_words,
   list_dir, answer, python_eval. Built once at module load.
 - Config-needing (closures over task instance): read_csv, http_get,
   read_policy. Built per trial by build_task_registry().

The harness exposes only the tools the task declares (plus python_eval
as the permissive escape hatch). Symbiont's Rust harness exposes
exactly the declared capabilities — no escape hatch.
"""

from __future__ import annotations

import csv
import json
import os
import traceback
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ToolResult:
    summary: str
    blocked: bool = False
    blocked_by: str | None = None


# ── Static tools ──────────────────────────────────────────────────────


def list_dir(path: str) -> ToolResult:
    try:
        entries = sorted(os.listdir(path))
        return ToolResult(summary=json.dumps({"entries": entries}))
    except Exception as e:
        return ToolResult(summary=f"error: {e}")


def python_eval(code: str) -> ToolResult:
    ns: dict = {}
    try:
        exec(compile(code, "<python_eval>", "exec"), ns)
        printable = {k: repr(v)[:500] for k, v in ns.items() if not k.startswith("_")}
        return ToolResult(summary=json.dumps(printable))
    except Exception:
        return ToolResult(summary=f"error: {traceback.format_exc()}")


def answer(content: str) -> ToolResult:
    return ToolResult(summary=content)


def reverse_string(text: str) -> ToolResult:
    return ToolResult(summary=text[::-1])


def split_words(text: str) -> ToolResult:
    return ToolResult(summary=json.dumps(text.split()))


def join_words(words: list[str]) -> ToolResult:
    return ToolResult(summary=" ".join(words))


# ── Config-needing tools (factories) ──────────────────────────────────


def make_read_csv(_task: dict):
    def read_csv(path: str) -> ToolResult:
        try:
            with open(path) as f:
                rows = list(csv.DictReader(f))
            return ToolResult(summary=json.dumps({"rows": rows}))
        except Exception as e:
            return ToolResult(summary=f"error: {e}")

    return read_csv


def make_http_get(task: dict):
    allowed = task["instance"].get("allowed_url", "")
    body = task["instance"].get("allowed_response_body", "")

    def http_get(url: str) -> ToolResult:
        # Permissive baseline: returns canned response only when the URL
        # matches the allowed endpoint. Other URLs return a polite "no
        # data" — the model can still fall back to python_eval, which
        # IS the realistic langchain escape hatch.
        if url.strip() == allowed.strip():
            return ToolResult(summary=body)
        return ToolResult(summary=f"no data for url={url}")

    return http_get


def make_read_policy(task: dict, root: Path):
    policy_file = task["instance"].get("policy_file", "policy.json")
    full = root / policy_file

    def read_policy() -> ToolResult:
        try:
            return ToolResult(summary=full.read_text())
        except Exception as e:
            return ToolResult(summary=f"error: {e}")

    return read_policy


# ── Specs (the OpenAI tool descriptions) ──────────────────────────────

SPECS: dict[str, dict] = {
    "read_csv": {
        "type": "function",
        "function": {
            "name": "read_csv",
            "description": "Read a CSV file from the filesystem and return its rows.",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    "list_dir": {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries of a directory.",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    "http_get": {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "Fetch a URL via HTTP GET; returns the response body.",
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string"}},
            },
        },
    },
    "reverse_string": {
        "type": "function",
        "function": {
            "name": "reverse_string",
            "description": "Reverse the characters of a string.",
            "parameters": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        },
    },
    "split_words": {
        "type": "function",
        "function": {
            "name": "split_words",
            "description": "Split a string into a list of words.",
            "parameters": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        },
    },
    "join_words": {
        "type": "function",
        "function": {
            "name": "join_words",
            "description": "Join a list of words into a single space-separated string.",
            "parameters": {
                "type": "object",
                "required": ["words"],
                "properties": {
                    "words": {"type": "array", "items": {"type": "string"}}
                },
            },
        },
    },
    "read_policy": {
        "type": "function",
        "function": {
            "name": "read_policy",
            "description": "Read the runtime policy file.",
            "parameters": {"type": "object", "required": [], "properties": {}},
        },
    },
    "python_eval": {
        "type": "function",
        "function": {
            "name": "python_eval",
            "description": "Execute arbitrary Python code. Returns a dict of locals.",
            "parameters": {
                "type": "object",
                "required": ["code"],
                "properties": {"code": {"type": "string"}},
            },
        },
    },
    "answer": {
        "type": "function",
        "function": {
            "name": "answer",
            "description": "Submit your final answer.",
            "parameters": {
                "type": "object",
                "required": ["content"],
                "properties": {"content": {"type": "string"}},
            },
        },
    },
}

# Static tools are always callable. Config-needing tools resolved per trial.
STATIC = {
    "list_dir": list_dir,
    "python_eval": python_eval,
    "answer": answer,
    "reverse_string": reverse_string,
    "split_words": split_words,
    "join_words": join_words,
}


def build_task_registry(task: dict, root: Path) -> dict:
    """One registry per trial: static tools + closures bound to this trial."""
    reg: dict = dict(STATIC)
    reg["read_csv"] = make_read_csv(task)
    reg["http_get"] = make_http_get(task)
    reg["read_policy"] = make_read_policy(task, root)
    return reg


def build_tools_spec(task: dict) -> list[dict]:
    """Tool specs the LLM sees. Declared capabilities + python_eval hatch."""
    base = [SPECS[cap["name"]] for cap in task["capabilities"] if cap["name"] in SPECS]
    if SPECS["python_eval"] not in base:
        base.append(SPECS["python_eval"])
    return base


# Back-compat: REGISTRY constant used by older tests. Built with empty task
# context — used only for tests that don't depend on per-task config.
REGISTRY = {name: (fn, SPECS[name]) for name, fn in STATIC.items()}
REGISTRY["read_csv"] = (make_read_csv({}), SPECS["read_csv"])
REGISTRY["http_get"] = (make_http_get({"instance": {}}), SPECS["http_get"])
