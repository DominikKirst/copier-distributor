#!/usr/bin/env python3
"""Upsert a PR comment summarizing dry-run fan-out."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

MARKER = "<!-- copier-fanout-summary -->"
TYPE_ORDER = ("config", "lib", "deployable")
SYNC_ORDER = ("push", "pr", "event")


def load_results(root: Path) -> list[dict]:
    rows: list[dict] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "repo" in data and "type" in data:
            rows.append(data)
    return rows


def sort_key(row: dict) -> tuple:
    t = row.get("type", "")
    s = row.get("sync_type", "")
    return (
        TYPE_ORDER.index(t) if t in TYPE_ORDER else 99,
        SYNC_ORDER.index(s) if s in SYNC_ORDER else 99,
        row.get("repo", ""),
    )


def render(rows: list[dict]) -> str:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("type", ""), row.get("sync_type", "push"))].append(row)

    lines = [
        MARKER,
        f"Would run **{len(rows)}** target(s).",
        "",
        "| type | sync-type | repo | has_conflicts |",
        "| --- | --- | --- | --- |",
    ]
    keys = sorted(
        grouped,
        key=lambda k: (
            TYPE_ORDER.index(k[0]) if k[0] in TYPE_ORDER else 99,
            SYNC_ORDER.index(k[1]) if k[1] in SYNC_ORDER else 99,
        ),
    )
    for type_name, sync_type in keys:
        group = grouped[(type_name, sync_type)]
        conflicts = sum(1 for r in group if r.get("has_conflicts"))
        lines.append(
            f"| {type_name} | {sync_type} | {len(group)} | {conflicts} |"
        )

    conflicted = [r for r in sorted(rows, key=sort_key) if r.get("has_conflicts")]
    lines += ["", f"<details><summary>Conflicts ({len(conflicted)})</summary>", ""]
    if conflicted:
        for row in conflicted:
            url = row.get("html_url") or f"https://github.com/{row['repo']}"
            label = row.get("label") or row["repo"]
            lines.append(f"- [{label}]({url})")
    else:
        lines.append("_None._")
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def upsert_comment(body: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    pr = os.environ["PR_NUMBER"]
    listed = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/issues/{pr}/comments",
            "--paginate",
            "--jq",
            f'.[] | select(.body | contains("{MARKER}")) | .id',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    comment_id = listed.stdout.strip().splitlines()[0] if listed.stdout.strip() else ""
    payload = json.dumps({"body": body})
    if comment_id:
        url = f"repos/{repo}/issues/comments/{comment_id}"
        method = "PATCH"
    else:
        url = f"repos/{repo}/issues/{pr}/comments"
        method = "POST"
    subprocess.run(
        ["gh", "api", "-X", method, url, "--input", "-"],
        check=True,
        input=payload,
        text=True,
    )


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "results")
    body = render(load_results(root))
    print(body)
    upsert_comment(body)


if __name__ == "__main__":
    main()
