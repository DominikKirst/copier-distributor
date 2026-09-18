#!/usr/bin/env python3
"""Which template types changed between two git refs.

- copier.yml or template/_shared/ → config, lib, deployable
- template/{% if type == 'X' %}X{% endif %}/ → that type only
- anything else → none
"""

from __future__ import annotations

import subprocess
import sys

ALL_TYPES = ("config", "lib", "deployable")
SHARED_PREFIX = "template/_shared/"


def type_prefix(type_name: str) -> str:
    return f"template/{{% if type == '{type_name}' %}}{type_name}{{% endif %}}/"


def changed_files(base_ref: str, head_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def affected_types(paths: list[str]) -> set[str]:
    affected: set[str] = set()
    for path in paths:
        if path == "copier.yml" or path.startswith(SHARED_PREFIX):
            return set(ALL_TYPES)
        for type_name in ALL_TYPES:
            if path.startswith(type_prefix(type_name)):
                affected.add(type_name)
    return affected


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <base-ref> <head-ref>", file=sys.stderr)
        raise SystemExit(2)
    print(",".join(sorted(affected_types(changed_files(sys.argv[1], sys.argv[2])))))


if __name__ == "__main__":
    main()
