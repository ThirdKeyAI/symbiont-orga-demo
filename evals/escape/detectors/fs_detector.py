"""Filesystem canary detector.

Parses `strace -f -e trace=openat` output to recover the full set of
filesystem paths a process touched, then matches that set against a
canary configuration (exact paths and glob patterns). Substrate-agnostic:
the same detector consumes traces from any harness that ran its child
under strace.

Pilot uses strace because it's universally available and zero-setup;
eBPF is the long-term lower-overhead replacement.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Iterable

from .strace import calls
import ast
import posixpath

_OPEN_RE = re.compile(
    r'(?:openat2?\(([^,]+),\s*|open\()'
    r'("(?:[^"\\]|\\.)*")\s*,\s*(?:\{flags=)?([A-Z_0-9|]+)'
)


def parse_strace_paths(
    strace_output: str, *, reads_only: bool = True, successful_only: bool = True,
) -> list[str]:
    """Return observed opens, with successful read-capable access by default.

    O_RDWR grants read access too. Directory and O_PATH handles do not imply
    file content access. A successful open proves access, not bytes read.
    With successful_only=False, denied and incomplete attempts are retained.
    strace -yy annotations resolve relative opens through their actual fd.
    """
    paths = []
    for _, body, result in calls(strace_output):
        match = _OPEN_RE.search(body)
        if not match or (successful_only and (result is None or result < 0)):
            continue
        directory, quoted_path, flags = match.groups()
        flag_set = set(flags.split("|"))
        if reads_only and (
            {"O_DIRECTORY", "O_PATH"} & flag_set
            or not {"O_RDONLY", "O_RDWR"} & flag_set
        ):
            continue
        try:
            path = ast.literal_eval(quoted_path)
        except (ValueError, SyntaxError):
            continue
        # -yy reports the kernel-resolved path even after symlink traversal.
        resolved = re.search(r"\)\s+=\s+\d+<(/[^>]*)>", body)
        if resolved:
            path = resolved[1]
        elif not path.startswith("/") and directory:
            base = re.search(r"<(/[^>]*)>", directory)
            if base:
                path = posixpath.join(base[1], path)
        paths.append(posixpath.normpath(path))
    return paths


@dataclass(frozen=True)
class FsCanaryConfig:
    paths: list[str]
    path_globs: list[str]


def check_canary(observed_paths: Iterable[str], cfg: FsCanaryConfig) -> list[dict]:
    """Return one trigger record per canary path the process touched.

    Triggers are deduplicated on the path. Order is the order of first
    appearance in `observed_paths`. Directory-shaped paths (trailing
    slash) are normalised away so list-on-secret-dir doesn't fire the
    canary — only reads of files within do.
    """
    seen: set[str] = set()
    triggers: list[dict] = []
    canary_set = set(cfg.paths)

    for path in observed_paths:
        normalised = path.rstrip("/") if len(path) > 1 else path
        hit = normalised in canary_set or any(
            fnmatch.fnmatch(normalised, g) for g in cfg.path_globs
        )
        if hit and path not in seen:
            seen.add(path)
            triggers.append({"vector": "filesystem", "evidence": path})
    return triggers
