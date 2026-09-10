#!/usr/bin/env python3
"""Verify a saved matched laboratory archive without running workloads."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harnesses.matched.evidence import verify_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        result = verify_report(args.directory)
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(json.dumps({"evidence_valid": False, "error": str(error)}))
        return 1
    print(json.dumps(result))
    return 0 if result["expectations_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
