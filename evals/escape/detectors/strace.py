"""Normalize complete and resumed strace calls without inventing success."""

from __future__ import annotations

import re
from collections.abc import Iterator

_PREFIX = re.compile(r"^(?:\[pid\s+(\d+)\]\s*|(\d+)\s+)?(.*)$")
_RESUMED = re.compile(r"<\.\.\. (\w+) resumed>(.*)")
_RESULT = re.compile(r"\)\s+=\s+(-?\d+)(?:<[^>]*>)?(?:\s|$)")


def calls(trace: str) -> Iterator[tuple[str, str, int | None]]:
    pending: dict[str, str] = {}
    for line in trace.splitlines():
        match = _PREFIX.match(line.strip())
        if not match:
            continue
        pid = match[1] or match[2] or "main"
        body = match[3]
        if "<unfinished ...>" in body:
            pending[pid] = body.split("<unfinished ...>", 1)[0]
            continue
        resumed = _RESUMED.match(body)
        if resumed:
            prefix = pending.pop(pid, "")
            if not prefix.startswith(resumed[1] + "("):
                continue
            body = prefix + resumed[2]
        result = _RESULT.search(body)
        yield pid, body, int(result[1]) if result else None
    # Unfinished calls contribute only to attempts, never success.
    for pid, body in pending.items():
        yield pid, body, None
