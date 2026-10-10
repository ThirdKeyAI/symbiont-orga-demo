"""Bounded chat-completions access for an untrusted, networkless worker.

The socket is a deliberately available inference channel, not a general HTTP
proxy. Provider credentials and the protected request journal stay in the
host process. This journal does not attest to worker tool effects.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import socketserver
import tempfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

import httpx

MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_COMPLETION_TOKENS = 2048
MAX_REQUESTS = 8
BROKER_PATH = "/v1/chat/completions"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode(raw: bytes):
    def invalid_constant(_):
        raise ValueError("non-finite JSON number")
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=invalid_constant)


def validate_request(raw: bytes, model: str, tools: list[dict]) -> dict:
    """Accept text/tool messages only; fix all provider options on the host."""
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request too large")
    body = _decode(raw)
    if not isinstance(body, dict) or set(body) != {"model", "messages", "tools", "tool_choice"}:
        raise ValueError("unexpected request fields")
    if body["model"] != model or body["tools"] != tools or body["tool_choice"] != "auto":
        raise ValueError("trial configuration mismatch")
    messages = body["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise ValueError("invalid messages")
    for message in messages:
        if not isinstance(message, dict) or set(message) - {"role", "content", "tool_calls", "tool_call_id"}:
            raise ValueError("unexpected message fields")
        role = message.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            raise ValueError("invalid role")
        if message.get("content") is not None and not isinstance(message["content"], str):
            raise ValueError("only text content is supported")
        if "tool_call_id" in message and (role != "tool" or not isinstance(message["tool_call_id"], str)):
            raise ValueError("invalid tool response")
        if "tool_calls" in message:
            calls = message["tool_calls"]
            if role != "assistant" or not isinstance(calls, list) or len(calls) > 32:
                raise ValueError("invalid tool calls")
            for call in calls:
                if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
                    raise ValueError("invalid tool call")
                function = call["function"]
                if not isinstance(call["id"], str) or call["type"] != "function":
                    raise ValueError("invalid tool call identity")
                if not isinstance(function, dict) or set(function) != {"name", "arguments"}:
                    raise ValueError("invalid function")
                if not all(isinstance(function[key], str) for key in ("name", "arguments")):
                    raise ValueError("invalid function values")
    return {**body, "max_tokens": MAX_COMPLETION_TOKENS, "stream": False}


def _serve(socket_path, journal_path, endpoint, api_key, model, tools, ready):
    # Single-request server bounds concurrency and serializes the budget/journal.
    class Server(socketserver.UnixStreamServer):
        attempts = 0

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *_args):
            pass

        def reply(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def deny(self, status=400):
            self.reply(status, b'{"error":"inference broker rejected request"}')

        def do_GET(self):
            self.deny(405)

        def do_POST(self):
            def event(kind, **fields):
                line = json.dumps({"event": kind, **fields}, sort_keys=True) + "\n"
                journal.write(line)
                journal.flush()
                os.fsync(journal.fileno())

            try:
                if self.path != BROKER_PATH:
                    self.deny(404)
                    return
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                    self.deny()
                    return
                length = int(lengths[0])
                if not 0 < length <= MAX_REQUEST_BYTES or "Transfer-Encoding" in self.headers:
                    self.deny(413)
                    return
                if self.headers.get_all("Content-Type", []) != ["application/json"]:
                    self.deny(415)
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    self.deny()
                    return
                body = validate_request(raw, model, tools)
                if self.server.attempts >= MAX_REQUESTS:
                    self.deny(429)
                    return
                self.server.attempts += 1
                sequence = self.server.attempts
                # Required durable authorization before any provider request.
                event("inference_started", sequence=sequence,
                      request_sha256=hashlib.sha256(raw).hexdigest(),
                      max_completion_tokens=MAX_COMPLETION_TOKENS)
                headers = {"Accept-Encoding": "identity"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
                    with client.stream("POST", endpoint, json=body, headers=headers) as response:
                        if response.status_code != 200:
                            raise ValueError("provider refused completion")
                        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                            raise ValueError("encoded provider response is unsupported")
                        chunks = bytearray()
                        for chunk in response.iter_raw(chunk_size=65536):
                            chunks.extend(chunk)
                            if len(chunks) > MAX_RESPONSE_BYTES:
                                raise ValueError("response too large")
                data = _decode(bytes(chunks))
                if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
                    raise ValueError("invalid provider completion")
                # Never forward provider headers, redirects or error bodies.
                event("inference_completed", sequence=sequence,
                      response_sha256=hashlib.sha256(chunks).hexdigest())
                self.reply(200, bytes(chunks))
            except (ValueError, TypeError, RecursionError, UnicodeError):
                self.deny()
            except Exception:
                # No credential, endpoint, or upstream response in errors.
                self.deny(502)

    try:
        with open(journal_path, "x", encoding="utf-8") as journal:
            os.chmod(journal_path, 0o600)
            with Server(socket_path, Handler) as server:
                os.chmod(socket_path, 0o666)
                ready.send(True)
                ready.close()
                server.serve_forever(poll_interval=0.1)
    except Exception:
        ready.close()
        raise


@contextmanager
def inference_broker(*, endpoint: str, api_key: str, model: str,
                     tools: list[dict], journal_path: Path):
    """Start a disposable host broker. Only mount socket_dir, read-only."""
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/chat/completions")):
        raise ValueError("expected a fixed HTTP(S) chat-completions endpoint")
    with tempfile.TemporaryDirectory(prefix="escape-inference-") as directory:
        socket_dir = Path(directory)
        os.chmod(socket_dir, 0o755)
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_serve, args=(
            str(socket_dir / "llm.sock"), str(journal_path), endpoint,
            api_key, model, tools, send,
        ))
        try:
            process.start()
            send.close()
            try:
                started = receive.poll(10) and receive.recv()
            except EOFError:
                started = False
            if not started:
                raise RuntimeError("inference broker did not start")
            yield socket_dir
        finally:
            receive.close()
            send.close()
            if process.pid is not None:
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
                if process.is_alive():
                    raise RuntimeError("inference broker cleanup failed")
                process.close()
