#!/usr/bin/env python3
"""Lint .cedar policy files for homoglyph / non-ASCII identifiers.

v6 #3 — "What if someone registers `store_knоwledge` (Cyrillic о) into
Cedar by mistake? A linter that normalises all action identifiers and
warns on any non-ASCII character closes this class."

Rules:
  1. Every `action == Action::"…"` literal must be pure ASCII.
  2. Every `Agent::"…"` principal literal must be pure ASCII.
  3. No invisible-control characters anywhere in the file (ZWSP/
     tag-block/DEL/etc.) — keeps stego payloads out of the policy
     surface itself.

Exit code is the count of findings. Suitable as a pre-commit hook and
a CI gate.

Usage:
    scripts/lint-cedar-policies.py                       # scan policies/*.cedar
    scripts/lint-cedar-policies.py path/to/policy.cedar  # scan one file
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Mirrored from sanitize_field in
# crates/demo-karpathy-loop/src/knowledge.rs. Drift is a bug.
FORBIDDEN_RANGES: list[tuple[int, int]] = [
    (0x00, 0x08),
    (0x0B, 0x0C),
    (0x0E, 0x1F),
    (0x7F, 0x7F),
    (0x80, 0x9F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0xFEFF, 0xFEFF),
    (0x180E, 0x180E),
    (0x1D173, 0x1D17A),
    (0xFE00, 0xFE0F),
    (0xE0000, 0xE007F),
    (0xE0100, 0xE01EF),
]

# Capture the string literal after `Action::` or `Agent::`. We do a
# shallow tokenise — good enough for the few Cedar files this repo
# ships, and adding a real parser is out of scope.
ID_PATTERN = re.compile(r'(Action|Agent)::"([^"]+)"')


def find_invisible(s: str) -> list[tuple[int, int]]:
    out = []
    for i, c in enumerate(s):
        code = ord(c)
        for lo, hi in FORBIDDEN_RANGES:
            if lo <= code <= hi:
                out.append((i, code))
                break
    return out


def lint_one(path: Path) -> int:
    text = path.read_text()
    findings = 0

    # Rule 1 + 2: identifiers must be pure ASCII.
    for m in ID_PATTERN.finditer(text):
        kind, ident = m.group(1), m.group(2)
        non_ascii = [(i, ord(c)) for i, c in enumerate(ident) if ord(c) >= 0x80]
        if non_ascii:
            line = text.count("\n", 0, m.start()) + 1
            code_hex = ", ".join(f"U+{c:04X} at pos {i}" for i, c in non_ascii[:4])
            print(
                f"  {path}:{line}  {kind}::\"{ident}\" contains non-ASCII code points "
                f"({code_hex}) — homoglyph risk; rewrite with ASCII."
            )
            findings += 1

    # Rule 3: no invisible-control characters anywhere.
    hits = find_invisible(text)
    if hits:
        by_line: dict[int, list[int]] = {}
        for pos, code in hits:
            line = text.count("\n", 0, pos) + 1
            by_line.setdefault(line, []).append(code)
        for line, codes in sorted(by_line.items()):
            code_hex = ", ".join(f"U+{c:04X}" for c in codes[:4])
            print(
                f"  {path}:{line}  invisible control char(s) ({code_hex}) — "
                "strip before committing."
            )
            findings += 1

    return findings


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        paths = [Path(p) for p in argv[1:]]
    else:
        root = Path(__file__).resolve().parent.parent
        paths = sorted(root.glob("policies/*.cedar"))
    if not paths:
        print("no .cedar files found")
        return 2

    total = 0
    for p in paths:
        n = lint_one(p)
        total += n

    if total == 0:
        print(f"✓ {len(paths)} Cedar policy file(s) clean — ASCII identifiers, "
              "no invisible control chars.")
        return 0
    print()
    print(f"✗ {total} finding(s) across {len(paths)} file(s).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
