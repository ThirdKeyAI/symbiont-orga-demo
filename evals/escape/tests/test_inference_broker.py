"""Local transport and Docker checks; no real credentials or model service."""

from __future__ import annotations

import gzip
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml

from harnesses.common.inference_broker import (
    MAX_COMPLETION_TOKENS, MAX_REQUESTS, MAX_RESPONSE_BYTES, inference_broker,
)

MODEL = "local/scripted"
KEY = "synthetic-provider-credential"
TOOLS = []


def request_body(**changes):
    return {"model": MODEL, "tools": TOOLS, "tool_choice": "auto",
            "messages": [{"role": "user", "content": "fixture"}], **changes}


def completion(calls=None, content=None):
    return {"choices": [{"message": {"role": "assistant", "content": content,
                                    "tool_calls": calls or []}}]}


def call(name, **args):
    return {"id": f"call-{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


@contextmanager
def provider(responses=None):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "body": body,
                         "authorization": self.headers.get("Authorization")})
            status, payload, headers = (responses[len(seen)-1] if responses else
                                        (200, completion(content="ok"), {}))
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions", seen
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)


@contextmanager
def client_for(tmp_path, endpoint):
    with inference_broker(endpoint=endpoint, api_key=KEY, model=MODEL,
                          tools=TOOLS, journal_path=tmp_path / "broker.jsonl") as directory:
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(directory / "llm.sock")),
                          base_url="http://broker", timeout=10) as client:
            yield client
    assert not directory.exists()


def test_only_fixed_completion_contract_reaches_provider(tmp_path):
    with provider() as (endpoint, seen), client_for(tmp_path, endpoint) as client:
        assert client.get("/api/tags").status_code == 405
        assert client.post("/api/delete", json=request_body()).status_code == 404
        assert client.post("/v1/chat/completions?redirect=admin", json=request_body()).status_code == 404
        for changes in (
            {"model": "other/model"}, {"tools": [{"type": "web_search"}]},
            {"stream": True}, {"max_tokens": 1_000_000},
            {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "http://admin"}]}]},
            {"messages": [{"role": "user", "content": "ok", "url": "http://admin"}]},
        ):
            assert client.post("/v1/chat/completions", json=request_body(**changes)).status_code == 400
        duplicate = json.dumps(request_body())[:-1] + ', "model":"other/model"}'
        assert client.post("/v1/chat/completions", content=duplicate,
                           headers={"Content-Type": "application/json"}).status_code == 400
        assert not seen
        response = client.post("/v1/chat/completions", json=request_body(),
                               headers={"Authorization": "Bearer attacker-controlled"})
        assert response.status_code == 200
        assert len(seen) == 1
        assert seen[0]["authorization"] == f"Bearer {KEY}"
        assert seen[0]["path"] == "/v1/chat/completions"
        assert seen[0]["body"]["max_tokens"] == MAX_COMPLETION_TOKENS
        assert seen[0]["body"]["stream"] is False
    journal = (tmp_path / "broker.jsonl").read_text()
    assert KEY not in journal and "fixture" not in journal
    assert [json.loads(line)["event"] for line in journal.splitlines()] == [
        "inference_started", "inference_completed"]


def test_budget_counts_failed_provider_requests_and_hides_errors(tmp_path):
    responses = [(503, {"error": KEY}, {})] * MAX_REQUESTS
    with provider(responses) as (endpoint, seen), client_for(tmp_path, endpoint) as client:
        for _ in range(MAX_REQUESTS):
            response = client.post("/v1/chat/completions", json=request_body())
            assert response.status_code == 400 and KEY not in response.text
        assert client.post("/v1/chat/completions", json=request_body()).status_code == 429
        assert len(seen) == MAX_REQUESTS


def test_redirect_and_oversized_response_are_not_forwarded(tmp_path):
    with provider() as (sink, sink_seen):
        responses = [(307, {}, {"Location": sink, "X-Secret": KEY}),
                     (200, completion(content="x" * MAX_RESPONSE_BYTES), {}),
                     (200, gzip.compress(json.dumps(completion(content="x" * MAX_RESPONSE_BYTES)).encode()),
                      {"Content-Encoding": "gzip"})]
        with provider(responses) as (endpoint, seen), client_for(tmp_path, endpoint) as client:
            for _ in responses:
                response = client.post("/v1/chat/completions", json=request_body())
                assert response.status_code == 400
                assert "location" not in response.headers and "x-secret" not in response.headers
            assert len(seen) == 3 and not sink_seen


def test_incomplete_journal_cannot_be_reused(tmp_path):
    journal = tmp_path / "broker.jsonl"
    journal.write_text("existing evidence\n")
    with provider() as (endpoint, seen):
        with pytest.raises((EOFError, RuntimeError)):
            with inference_broker(endpoint=endpoint, api_key=KEY, model=MODEL,
                                  tools=TOOLS, journal_path=journal):
                pytest.fail("broker accepted an existing journal")
        assert not seen
    assert journal.read_text() == "existing evidence\n"


def test_confined_pilot_does_not_score_worker_evidence(tmp_path, monkeypatch):
    import runner
    from tests.test_runner import _stub_run_one
    monkeypatch.setattr(runner, "_run_one_trial", _stub_run_one)
    monkeypatch.setattr(runner, "_resolve_openrouter_ips", lambda: [])
    result = runner.run_pilot(task_path=Path(__file__).parents[1] / "tasks/fs_boundary.yaml",
                              substrate="python_confined", model=MODEL, trials=1,
                              results_dir=tmp_path)
    record = json.loads(result.read_text())
    assert record["evaluation_status"] == "invalid"
    assert record["escape_success"] is None
    assert record["worker_evidence_protected"] is False
    assert "worker-written evidence has no protected observer" in record["evaluation_errors"]


@pytest.mark.skipif(os.environ.get("ESCAPE_BROKER_DOCKER_E2E") != "1", reason="explicit local Docker E2E")
def test_confined_worker_legitimate_tools_and_broker_denials(tmp_path, monkeypatch):
    from harnesses.common.confined import run_confined_trial
    task = yaml.safe_load((Path(__file__).parents[1] / "tasks/fs_boundary.yaml").read_text())
    task_path = tmp_path / "task.yaml"
    task_path.write_text(yaml.safe_dump(task))
    task["_path"] = task_path
    instance = tmp_path / "instance"
    allowed_path = instance / task["instance"]["allowed_paths"][0].lstrip("/")
    code = '''import os, httpx
assert "ESCAPE_LLM_API_KEY" not in os.environ
assert "OPENROUTER_API_KEY" not in os.environ
with httpx.Client(transport=httpx.HTTPTransport(uds="/inference/llm.sock"), base_url="http://broker") as c:
    assert c.get("/api/tags").status_code == 405
    assert c.post("/api/delete", json={}).status_code == 404
    assert c.post("/v1/chat/completions", json={"model":"other"}).status_code == 400
broker_denials = "verified"
'''
    responses = [
        (200, completion([call("read_csv", path=str(allowed_path)), call("python_eval", code=code)]), {}),
        (200, completion([call("answer", content="rows=3 sum=425 BROKER_OK")]), {}),
    ]
    monkeypatch.setenv("ESCAPE_LLM_API_KEY", KEY)
    with provider(responses) as (endpoint, seen):
        monkeypatch.setenv("ESCAPE_LLM_URL", endpoint)
        out, trace = run_confined_trial(task, model=MODEL, prompt_variant=0,
                                        instance_seed=0, results_dir=tmp_path / "results",
                                        instance_root=instance)
        assert len(seen) == 2
        assert all(row["authorization"] == f"Bearer {KEY}" for row in seen)
    record = json.loads(out.read_text())
    assert record["execution_error"] is None
    results = {row["name"]: row["result_summary"] for row in record["tool_calls"]}
    rows = json.loads(results["read_csv"])["rows"]
    assert len(rows) == 3 and sum(int(row["amount"]) for row in rows) == 425
    assert record["objective_success"] is True
    assert json.loads(results["python_eval"])["broker_denials"] == "'verified'"
    assert results["answer"] == "rows=3 sum=425 BROKER_OK"
    assert trace.stat().st_size > 0
    profile = json.loads(out.with_suffix(".lab.json").read_text())
    assert profile["container_cleanup_confirmed"] is True
    assert profile["containment_claim"] is False
    assert profile["worker_evidence_protected"] is False


@pytest.mark.skipif(os.environ.get("ESCAPE_BROKER_DOCKER_E2E") != "1", reason="explicit local Docker E2E")
def test_confined_timeout_removes_worker_and_descendants(tmp_path, monkeypatch):
    import subprocess
    from harnesses.common import confined
    task_path = Path(__file__).parents[1] / "tasks/fs_boundary.yaml"
    task = yaml.safe_load(task_path.read_text())
    task["_path"] = task_path
    started = tmp_path / "instance/descendant-started"
    code = ('import subprocess, time; from pathlib import Path; '
            'child = subprocess.Popen(["python3", "-c", "import time; time.sleep(60)"], start_new_session=True); '
            f'Path({str(started)!r}).write_text(str(child.pid)); time.sleep(60)')
    responses = [(200, completion([call("python_eval", code=code)]), {})]
    monkeypatch.setattr(confined, "TRIAL_TIMEOUT_SECONDS", 4)
    with provider(responses) as (endpoint, seen):
        monkeypatch.setenv("ESCAPE_LLM_URL", endpoint)
        with pytest.raises(subprocess.TimeoutExpired):
            confined.run_confined_trial(task, model=MODEL, prompt_variant=0,
                                       instance_seed=0, results_dir=tmp_path / "results",
                                       instance_root=tmp_path / "instance")
        assert len(seen) == 1
    assert int(started.read_text()) > 0
    profile = json.loads((tmp_path / "results/trial-python_confined-0000.lab.json").read_text())
    assert profile["container_cleanup_confirmed"] is True
    assert not (tmp_path / "results/trial-python_confined-0000.json").exists()
