#!/usr/bin/env python3
"""Post-sweep audit of every knowledge.db under data/.

Scans the subject/predicate/object columns of every
stored_procedures table for Unicode code points the sanitiser is
supposed to strip. Non-zero exit code if any slip past — suitable as
a CI gate after a sweep.

Usage:
    scripts/audit-knowledge-stores.py            # scan every data/*/knowledge.db
    scripts/audit-knowledge-stores.py data/xxx/knowledge.db  # a single file

Designed as the v6 #1 companion to the exhaustive Rust unit test: the
Rust test proves the sanitiser correctly handles every forbidden
code point *in isolation*; this script proves that's what actually
landed in storage across every real sweep.
"""
from __future__ import annotations

import sqlite3
import sys
from glob import glob
from pathlib import Path

# Keep these ranges aligned with sanitize_field in
# crates/demo-karpathy-loop/src/knowledge.rs. A drift is a bug; when
# the sanitiser moves, update both.
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


def invisible_chars(s: str) -> list[tuple[int, int]]:
    """Return a list of (position, code_point) for any forbidden char."""
    hits = []
    for i, c in enumerate(s):
        code = ord(c)
        for lo, hi in FORBIDDEN_RANGES:
            if lo <= code <= hi:
                hits.append((i, code))
                break
    return hits


def audit_db(path: Path) -> tuple[int, int, list[tuple[str, str, str, list]]]:
    """(rows_scanned, poisoned_row_count, sample_violations)"""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    try:
        rows = cur.execute(
            "SELECT task_id, subject, predicate, object FROM stored_procedures"
        ).fetchall()
    except sqlite3.OperationalError:
        # Empty db (table not created) — still a valid state for a
        # sweep with no reflections.
        return 0, 0, []
    poisoned = []
    for (task_id, subj, pred, obj) in rows:
        for field_name, val in (("subject", subj), ("predicate", pred), ("object", obj)):
            hits = invisible_chars(val or "")
            if hits:
                poisoned.append((task_id, field_name, val, hits))
    return len(rows), len(poisoned), poisoned


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        paths = [Path(p) for p in argv[1:]]
    else:
        root = Path(__file__).resolve().parent.parent
        paths = sorted(root.glob("data/*/knowledge.db"))
    if not paths:
        print("no knowledge.db files found")
        return 2

    grand_rows = 0
    grand_poisoned = 0
    per_db_failures: list[tuple[Path, list]] = []
    for p in paths:
        rows, poisoned, samples = audit_db(p)
        grand_rows += rows
        grand_poisoned += poisoned
        status = "OK" if poisoned == 0 else f"FAIL ({poisoned} poisoned rows)"
        print(f"  {p.parent.name:<22} rows={rows:<5} {status}")
        if poisoned:
            per_db_failures.append((p, samples))

    print()
    print(f"grand totals: {grand_rows} rows scanned across {len(paths)} db(s)")
    print(f"              {grand_poisoned} rows with sanitiser escapes")

    if grand_poisoned == 0:
        return 0

    print()
    print("sample escape details (first 5):")
    shown = 0
    for p, samples in per_db_failures:
        for task_id, field, val, hits in samples:
            if shown >= 5:
                return 1
            hits_str = ", ".join(f"pos {i} U+{code:04X}" for i, code in hits[:4])
            print(f"  {p.parent.name} {task_id} {field}: {hits_str}")
            print(f"    raw repr: {val!r}")
            shown += 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
