#!/usr/bin/env python3
"""Emit targets.toml groups as GitHub Actions matrix JSON."""

from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path

TARGETS_FILE = Path(__file__).resolve().parent.parent / "targets.toml"
TYPES = ("config", "lib", "deployable")


def normalize(entry: dict) -> dict:
    out = dict(entry)
    out.setdefault("use_pr_integration", False)
    return out


def load_targets() -> dict[str, list[dict]]:
    with TARGETS_FILE.open("rb") as f:
        data = tomllib.load(f)
    return {t: [normalize(e) for e in data.get(t, [])] for t in TYPES}


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <comma-separated-types>", file=sys.stderr)
        raise SystemExit(2)
    wanted = [t for t in sys.argv[1].split(",") if t]
    data = load_targets()
    groups = {t: (data[t] if t in wanted else []) for t in TYPES}
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        print(json.dumps(groups))
        return
    with open(out, "a", encoding="utf-8") as f:
        for type_name in TYPES:
            f.write(f"{type_name}={json.dumps(groups[type_name], separators=(',', ':'))}\n")


if __name__ == "__main__":
    main()
