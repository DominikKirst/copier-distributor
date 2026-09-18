#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "<!-- copier-fanout-summary -->"
TYPE_ORDER = ("config", "lib", "deployable")
SYNC_ORDER = ("push", "pr", "event")


@dataclass(frozen=True)
class SyncResult:
    repository: str
    template_type: str
    sync_type: str = "push"
    label: str = ""
    html_url: str = ""
    has_conflicts: bool = False
    conflict_files: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> SyncResult | None:
        repository = str(data.get("repo") or "")
        template_type = str(data.get("type") or "")
        if not repository or not template_type:
            return None
        raw_files = data.get("conflict_files") or ()
        files = tuple(str(path) for path in raw_files if path)
        html_url = str(data.get("html_url") or "")
        label = str(data.get("label") or "")
        return cls(
            repository=repository,
            template_type=template_type,
            sync_type=str(data.get("sync_type") or "push"),
            label=label,
            html_url=html_url,
            has_conflicts=bool(data.get("has_conflicts")),
            conflict_files=files,
        )

    @property
    def display_name(self) -> str:
        return self.repository.rsplit("/", 1)[-1]

    @property
    def display_label(self) -> str:
        return self.label or self.repository

    @property
    def page_url(self) -> str:
        return self.html_url or f"https://github.com/{self.repository}"

    @property
    def sort_key(self) -> tuple[int, int, str]:
        type_rank = (
            TYPE_ORDER.index(self.template_type)
            if self.template_type in TYPE_ORDER
            else 99
        )
        sync_rank = (
            SYNC_ORDER.index(self.sync_type) if self.sync_type in SYNC_ORDER else 99
        )
        return (type_rank, sync_rank, self.repository)


def load_results(root: Path) -> list[SyncResult]:
    rows: list[SyncResult] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        result = SyncResult.from_mapping(data)
        if result is not None:
            rows.append(result)
    return rows


def match_job_label(*, job_name: str, labels: Sequence[str]) -> str:
    # longest first — lib.foo must not steal lib.foo-pr
    for label in sorted((item for item in labels if item), key=len, reverse=True):
        if job_name.endswith(f"{label} (dry-run)") or job_name.endswith(f"{label} sync"):
            return label
    return ""


def job_url_by_label(rows: Sequence[SyncResult]) -> dict[str, str]:
    run_id = os.environ.get("GITHUB_RUN_ID")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not run_id or not repo:
        return {}
    listed = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            ".jobs[] | {name,html_url}",
            f"repos/{repo}/actions/runs/{run_id}/jobs",
        ],
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        return {}
    mapping: dict[str, str] = {}
    labels = [row.display_label for row in rows]
    for line in listed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(job.get("name") or "")
        html = str(job.get("html_url") or "")
        label = match_job_label(job_name=name, labels=labels)
        if label:
            mapping[label] = html
    return mapping


def target_row(row: SyncResult, *, extra: str = "") -> str:
    return (
        f"| `{row.template_type}` | `{row.sync_type}` "
        f"| [{row.display_name}]({row.page_url}) |{extra}"
    )


def render(rows: Sequence[SyncResult]) -> str:
    grouped: dict[tuple[str, str], list[SyncResult]] = defaultdict(list)
    for row in rows:
        grouped[(row.template_type, row.sync_type)].append(row)

    conflicted = [row for row in sorted(rows, key=lambda item: item.sort_key) if row.has_conflicts]
    n = len(rows)
    if conflicted:
        headline = f"**{len(conflicted)}** conflict(s) — fan-out would stall"
    elif n:
        headline = f"**{n}** target(s) would sync without conflicts"
    else:
        headline = "No targets in this diff"

    lines = [
        MARKER,
        f"## {'⚠️' if conflicted else '✅'} Sync Preview",
        "",
        headline,
        "",
        "| Type | Sync | Repos | Conflicts |",
        "| --- | --- | ---: | ---: |",
    ]
    keys = sorted(
        grouped,
        key=lambda item: (
            TYPE_ORDER.index(item[0]) if item[0] in TYPE_ORDER else 99,
            SYNC_ORDER.index(item[1]) if item[1] in SYNC_ORDER else 99,
        ),
    )
    for type_name, sync_type in keys:
        group = grouped[(type_name, sync_type)]
        conflicts = sum(1 for row in group if row.has_conflicts)
        lines.append(
            f"| `{type_name}` | `{sync_type}` | {len(group)} | {conflicts} |"
        )

    jobs = job_url_by_label(conflicted) if conflicted else {}
    lines += [
        "",
        f"<details><summary>Affected Targets ({n})</summary>",
        "",
        "| Type | Sync | Name |",
        "| --- | --- | --- |",
    ]
    if rows:
        for row in sorted(rows, key=lambda item: item.sort_key):
            lines.append(target_row(row))
    else:
        lines.append("| | | |")
    lines += ["", "</details>", ""]

    lines += [
        f"<details><summary>Conflicts ({len(conflicted)})</summary>",
        "",
        "| Type | Sync | Name | Files | Job |",
        "| --- | --- | --- | --- | --- |",
    ]
    if conflicted:
        for row in conflicted:
            job = jobs.get(row.display_label, "")
            job_cell = f"[log]({job})" if job else ""
            files = ", ".join(f"`{path}`" for path in row.conflict_files)
            lines.append(target_row(row, extra=f" {files} | {job_cell} |"))
    else:
        lines.append("| | | | | |")
    lines += [
        "",
        "</details>",
        "",
        "<sub>🤖 copier-distributor · dry-run only · this comment updates in place</sub>",
        "",
    ]
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
