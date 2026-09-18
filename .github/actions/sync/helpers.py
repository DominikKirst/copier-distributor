from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SYNC_BRANCH = "sync/template"
COPIER_CONFLICT_START = "^<<<<<<< before updating"
COPIER_CONFLICT_END = "^>>>>>>> after updating"


@dataclass(frozen=True)
class SyncConfig:
    app_token: str
    repository: str
    branch: str
    template_type: str
    vcs_ref: str
    sync_type: str
    dry_run: bool
    automerge: bool
    label: str
    result_file: Path
    github_workspace: Path | None


@dataclass(frozen=True)
class ConflictReport:
    has_conflicts: bool
    files: tuple[str, ...]


@dataclass(frozen=True)
class MergeOutcome:
    marker_files: Sequence[str]
    merge_succeeded: bool
    unmerged_files: Sequence[str]


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


def repository_name(repository: str) -> str:
    return repository.rsplit("/", 1)[-1]


def resolve_label(*, repository: str, label: str) -> str:
    return label or repository_name(repository)


def resolve_result_file(*, result_file: str, workspace: str) -> Path:
    if result_file:
        return Path(result_file)
    root = Path(workspace) if workspace else Path(".")
    return root / "sync-result.json"


def result_payload(*, config: SyncConfig, report: ConflictReport) -> dict[str, object]:
    return {
        "type": config.template_type,
        "sync_type": config.sync_type,
        "repo": config.repository,
        "label": config.label,
        "has_conflicts": report.has_conflicts,
        "html_url": f"https://github.com/{config.repository}",
        "conflict_files": sorted(path for path in report.files if path),
    }


def event_command(*, config: SyncConfig) -> str:
    return (
        "gh workflow run template-sync.yml "
        f"--repo {config.repository} --ref {config.branch} "
        f"-f vcs_ref={config.vcs_ref} -f dry_run=false"
    )


def plan_lines(*, config: SyncConfig) -> list[str]:
    lines = [
        f"sync_type={config.sync_type}",
        f"automerge={config.automerge}",
        f"dry_run={config.dry_run}",
        f"label={config.label}",
        "",
    ]
    if config.sync_type == "event":
        if config.dry_run:
            lines.append("# dry_run: not dispatched")
        lines.append(event_command(config=config))
    elif config.sync_type == "pr":
        lines.append(
            f"clone {config.repository}@{config.branch} → copier update → "
            f"push {SYNC_BRANCH} → upsert PR"
        )
        lines.append(f"automerge={config.automerge} (skipped on conflicts)")
    else:
        lines.append(f"clone {config.repository}@{config.branch} → copier update")
        lines.append(f"clean: merge + push {config.branch}")
        lines.append(f"conflicts: push {SYNC_BRANCH} + upsert PR")
    lines.append("")
    return lines


def detect_conflicts(*, outcome: MergeOutcome) -> ConflictReport:
    files = {path for path in outcome.marker_files if path}
    if not outcome.merge_succeeded:
        files.update(path for path in outcome.unmerged_files if path)
    # markers survive FF merge — git UU never fires
    has_conflicts = bool(files) or not outcome.merge_succeeded
    return ConflictReport(has_conflicts=has_conflicts, files=tuple(sorted(files)))


def should_dispatch_event(*, config: SyncConfig) -> bool:
    return not config.dry_run


def should_automerge(*, config: SyncConfig, report: ConflictReport) -> bool:
    return config.automerge and not report.has_conflicts


def apply_action(*, config: SyncConfig, report: ConflictReport) -> str:
    if config.dry_run:
        return "skip"
    if config.sync_type == "pr" or report.has_conflicts:
        return "pull_request"
    return "push"


def pull_request_title(*, moment: datetime) -> str:
    return f"chore: template sync {moment.astimezone(timezone.utc).strftime('%Y-%m-%d')}"


def compose_pull_request_body(*, existing_body: str, commits: str) -> str:
    if not existing_body:
        return commits
    if not commits:
        return existing_body
    return f"{existing_body.rstrip()}\n\n{commits}"


def parse_pull_request_number(stdout: str) -> str:
    return stdout.strip()


def warning_annotation(*, path: str, config: SyncConfig) -> str:
    return f"::warning file={path},title={config.label}::Copier conflict"


def conflict_flag_path(workspace: Path) -> Path:
    return workspace / "copier-conflicts.flag"
