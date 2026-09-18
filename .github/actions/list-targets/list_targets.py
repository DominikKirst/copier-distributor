#!/usr/bin/env python3
"""Emit targets.toml groups as GitHub Actions matrix JSON."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

def targets_path() -> Path:
    override = os.environ.get("TARGETS_FILE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "targets.toml"


TYPES = ("config", "lib", "deployable")
SYNC_TYPES = ("push", "pr", "event")


def as_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        raise SystemExit(f"invalid bool {value!r}")
    return bool(value)


def normalize(entry: dict, *, type_name: str, name: str) -> dict:
    out = dict(entry)
    out["label"] = f"{type_name}.{name}"
    sync = out.pop("sync", None) or {}
    sync_type = sync.get("type", "push")
    if sync_type not in SYNC_TYPES:
        raise SystemExit(f"{out['label']}: unknown sync.type {sync_type!r}")
    out["sync_type"] = sync_type
    out["automerge"] = (
        as_bool(sync.get("automerge"), default=True) if sync_type == "pr" else False
    )
    return out


def load_targets() -> dict[str, list[dict]]:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    with targets_path().open("rb") as f:
        data = tomllib.load(f)
    groups: dict[str, list[dict]] = {t: [] for t in TYPES}
    for type_name in TYPES:
        section = data.get(type_name, {})
        if not isinstance(section, dict):
            raise SystemExit(f"{type_name}: expected [[ {type_name}.name ]] tables")
        for name, entries in section.items():
            if not isinstance(entries, list):
                entries = [entries]
            for entry in entries:
                groups[type_name].append(
                    normalize(entry, type_name=type_name, name=name)
                )
    return groups


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <comma-separated-types>", file=sys.stderr)
        raise SystemExit(2)
    wanted = [t for t in sys.argv[1].split(",") if t]
    data = load_targets()
    groups = {t: (data[t] if t in wanted else []) for t in TYPES}
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        print(json.dumps(groups, indent=2))
        return
    with open(out, "a", encoding="utf-8") as f:
        for type_name in TYPES:
            f.write(f"{type_name}={json.dumps(groups[type_name], separators=(',', ':'))}\n")


if __name__ == "__main__":
    main()
