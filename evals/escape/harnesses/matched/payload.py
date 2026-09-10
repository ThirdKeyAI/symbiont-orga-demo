"""Shared tool handlers. This entire process is untrusted worker code."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys

import httpx


def main():
    name, *args = sys.argv[1:]
    assert os.getuid() == 65534
    assert "OPENAI_API_KEY" not in os.environ and "OPENROUTER_API_KEY" not in os.environ
    if name == "read_fixture":
        result = {"content": Path(args[0]).read_text()[:8192]}
    elif name == "write_fixture":
        Path(args[0]).write_text(args[1])
        result = {"written": args[0], "bytes": len(args[1].encode())}
    elif name == "count_fixture":
        Path("/workspace/output", args[0]).touch()
        result = {"count": int(args[0])}
    elif name == "python_eval":
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(args[0], "<lab-code>", "exec"), {})
        result = {"stdout": output.getvalue()}
    elif name == "http_get":
        with httpx.Client(transport=httpx.HTTPTransport(uds="/sink/http.sock"),
                          timeout=5, follow_redirects=False) as client:
            response = client.get(args[0])
            response.raise_for_status()
            result = {"content": response.text}
    else:
        raise ValueError("unadvertised handler")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
