#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from helpers import (
    COPIER_CONFLICT_END,
    COPIER_CONFLICT_START,
    SYNC_BRANCH,
    ConflictReport,
    MergeOutcome,
    SyncConfig,
    apply_action,
    compose_pull_request_body,
    conflict_flag_path,
    detect_conflicts,
    event_command,
    parse_bool,
    parse_pull_request_number,
    plan_lines,
    pull_request_title,
    repository_name,
    resolve_label,
    resolve_result_file,
    result_payload,
    should_automerge,
    should_dispatch_event,
    warning_annotation,
)


def run() -> None:
    config = _load_config()
    _print_plan(config=config)
    if config.sync_type == "event":
        _dispatch_event(config=config)
        return
    _sync_repository(config=config)


def _load_config() -> SyncConfig:
    app_token = os.environ.get("APP_TOKEN", "")
    if not app_token:
        raise SystemExit("GitHub App installation token is required")
    repository = os.environ["REPO"]
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    return SyncConfig(
        app_token=app_token,
        repository=repository,
        branch=os.environ["BRANCH"],
        template_type=os.environ["TYPE"],
        vcs_ref=os.environ["VCS_REF"],
        sync_type=os.environ.get("SYNC_TYPE", "push"),
        dry_run=parse_bool(os.environ.get("DRY_RUN", "false")),
        automerge=parse_bool(os.environ.get("AUTOMERGE", "false")),
        label=resolve_label(repository=repository, label=os.environ.get("LABEL", "")),
        result_file=resolve_result_file(
            result_file=os.environ.get("RESULT_FILE", ""),
            workspace=workspace,
        ),
        github_workspace=Path(workspace) if workspace else None,
    )


def _print_plan(*, config: SyncConfig) -> None:
    print("\n".join(plan_lines(config=config)))


def _dispatch_event(*, config: SyncConfig) -> None:
    command = event_command(config=config)
    print(f"would run: {command}")
    report = ConflictReport(has_conflicts=False, files=())
    _write_result(config=config, report=report)
    if not should_dispatch_event(config=config):
        print("dry_run: not dispatching")
        return
    _run_github_cli(
    [
        "workflow",
        "run",
        "template-sync.yml",
        "--ref",
        config.branch,
        "-f",
        f"vcs_ref={config.vcs_ref}",
        "-f",
        "dry_run=false",
    ],
    app_token=config.app_token,
    repository=config.repository,
    )


def _sync_repository(*, config: SyncConfig) -> None:
    _configure_git(app_token=config.app_token)
    with tempfile.TemporaryDirectory() as temporary_directory:
        repository_path = _clone_and_update(
            config=config, temporary_directory=Path(temporary_directory)
        )
        report = _detect_conflicts(config=config, repository_path=repository_path)
        _write_result(config=config, report=report)
        _warn_conflicts(config=config, report=report)
        action = apply_action(config=config, report=report)
        if action == "skip":
            print(f"dry_run: not pushing (has_conflicts={report.has_conflicts})")
            return
        if action == "pull_request":
            _apply_pull_request(
                config=config, repository_path=repository_path, report=report
            )
            return
        _apply_push(config=config, repository_path=repository_path)


def _configure_git(*, app_token: str) -> None:
    _run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "copier>=9"])
    user_bin = Path.home() / ".local" / "bin"
    os.environ["PATH"] = f"{user_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    _run_git(["config", "--global", "user.name", "copier-distributor"])
    _run_git(
        [
            "config",
            "--global",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ]
    )
    # copier answers still point at ssh
    _run_git(
        ["config", "--global", "url.https://github.com/.insteadOf", "git@github.com:"]
    )
    _run_git(
        [
            "config",
            "--global",
            "--add",
            "url.https://github.com/.insteadOf",
            "ssh://git@github.com/",
        ]
    )
    # copier clone of private template ignores client clone URL
    basic = base64.b64encode(f"x-access-token:{app_token}".encode()).decode()
    _run_git(
        [
            "config",
            "--global",
            "http.https://github.com/.extraheader",
            f"AUTHORIZATION: basic {basic}",
        ]
    )


def _clone_and_update(*, config: SyncConfig, temporary_directory: Path) -> Path:
    repository_path = temporary_directory / repository_name(config.repository)
    clone_url = (
        f"https://x-access-token:{config.app_token}@github.com/{config.repository}.git"
    )
    _run_git(["clone", "--branch", config.branch, clone_url, str(repository_path)])
    _run_git(["checkout", "-B", SYNC_BRANCH], cwd=repository_path)
    _run(
        [
            sys.executable,
            "-m",
            "copier",
            "update",
            "--trust",
            "--defaults",
            "--skip-answered",
            "--vcs-ref",
            config.vcs_ref,
            "-d",
            f"type={config.template_type}",
        ],
        cwd=repository_path,
    )
    _run_git(["add", "-A"], cwd=repository_path)
    print("=== git status ===")
    _run_git(["status", "--short"], cwd=repository_path)
    print("=== git diff ===")
    _run_git(["diff", "--cached"], cwd=repository_path)
    # empty commit = no template delta
    _run_git(["commit", "-m", "chore: template sync"], cwd=repository_path, check=False)
    return repository_path


def _detect_conflicts(*, config: SyncConfig, repository_path: Path) -> ConflictReport:
    marker_files: list[str] = []
    if _git_grep_matches(
        COPIER_CONFLICT_START, repository_path=repository_path
    ) or _git_grep_matches(COPIER_CONFLICT_END, repository_path=repository_path):
        print("copier conflict markers in the update")
        marker_files = _git_grep_files(
            COPIER_CONFLICT_START, repository_path=repository_path
        )
    _run_git(["checkout", config.branch], cwd=repository_path)
    merge = _run_git(
        ["merge", "--no-edit", SYNC_BRANCH],
        cwd=repository_path,
        check=False,
    )
    unmerged_files: list[str] = []
    if merge.returncode != 0:
        unmerged = _run_git(
            ["diff", "--name-only", "--diff-filter=U"],
            cwd=repository_path,
            capture_output=True,
            check=False,
        )
        unmerged_files = unmerged.stdout.splitlines()
        _run_git(["merge", "--abort"], cwd=repository_path, check=False)
    else:
        # trial merge FF's develop; gh pr create then sees no gap
        _run_git(
            ["reset", "--hard", f"origin/{config.branch}"],
            cwd=repository_path,
        )
    return detect_conflicts(
        outcome=MergeOutcome(
            marker_files=marker_files,
            merge_succeeded=merge.returncode == 0,
            unmerged_files=unmerged_files,
        )
    )


def _apply_pull_request(
    *, config: SyncConfig, repository_path: Path, report: ConflictReport
) -> None:
    _push_sync_branch(repository_path=repository_path)
    _upsert_pull_request(config=config, repository_path=repository_path)
    if should_automerge(config=config, report=report):
        _merge_pull_request(config=config, repository_path=repository_path)


def _apply_push(*, config: SyncConfig, repository_path: Path) -> None:
    _run_git(["checkout", config.branch], cwd=repository_path)
    _run_git(["merge", "--no-edit", SYNC_BRANCH], cwd=repository_path, check=False)
    _run_git(["push", "origin", config.branch], cwd=repository_path)


def _push_sync_branch(*, repository_path: Path) -> None:
    _run_git(["checkout", SYNC_BRANCH], cwd=repository_path)
    _run_git(
        ["push", "--force-with-lease", "-u", "origin", SYNC_BRANCH],
        cwd=repository_path,
    )


def _upsert_pull_request(*, config: SyncConfig, repository_path: Path) -> None:
    title = pull_request_title(moment=datetime.now(timezone.utc))
    log = _run_git(
        ["log", f"origin/{config.branch}..{SYNC_BRANCH}", "--format=%h %s"],
        cwd=repository_path,
        capture_output=True,
        check=False,
    )
    commits = log.stdout.strip()
    listed = _run_github_cli(
        [
            "pr",
            "list",
            "--head",
            SYNC_BRANCH,
            "--base",
            config.branch,
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            ".[0].number // empty",
        ],
        app_token=config.app_token,
        repository=config.repository,
        cwd=repository_path,
        capture_output=True,
    )
    pull_request = parse_pull_request_number(listed.stdout)
    if pull_request:
        viewed = _run_github_cli(
            ["pr", "view", pull_request, "--json", "body", "--jq", ".body"],
            app_token=config.app_token,
            repository=config.repository,
            cwd=repository_path,
            capture_output=True,
        )
        body = compose_pull_request_body(existing_body=viewed.stdout, commits=commits)
        _run_github_cli(
            ["pr", "edit", pull_request, "--title", title, "--body", body],
            app_token=config.app_token,
            repository=config.repository,
            cwd=repository_path,
        )
        return
    _run_github_cli(
        [
            "pr",
            "create",
            "--base",
            config.branch,
            "--head",
            SYNC_BRANCH,
            "--title",
            title,
            "--body",
            commits,
        ],
        app_token=config.app_token,
        repository=config.repository,
        cwd=repository_path,
    )


def _merge_pull_request(*, config: SyncConfig, repository_path: Path) -> None:
    automatic = _run_github_cli(
        ["pr", "merge", SYNC_BRANCH, "--merge", "--auto"],
        app_token=config.app_token,
        repository=config.repository,
        cwd=repository_path,
        check=False,
    )
    if automatic.returncode != 0:
        _run_github_cli(
            ["pr", "merge", SYNC_BRANCH, "--merge"],
            app_token=config.app_token,
            repository=config.repository,
            cwd=repository_path,
        )


def _write_result(*, config: SyncConfig, report: ConflictReport) -> None:
    config.result_file.parent.mkdir(parents=True, exist_ok=True)
    payload = result_payload(config=config, report=report)
    config.result_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _warn_conflicts(*, config: SyncConfig, report: ConflictReport) -> None:
    if not report.has_conflicts:
        return
    for path in report.files:
        print(warning_annotation(path=path, config=config))
    if config.github_workspace is not None:
        conflict_flag_path(config.github_workspace).write_text("1\n", encoding="utf-8")


def _git_grep_matches(pattern: str, *, repository_path: Path) -> bool:
    result = _run_git(
        ["grep", "-q", pattern, "HEAD"],
        cwd=repository_path,
        check=False,
    )
    return result.returncode == 0


def _git_grep_files(pattern: str, *, repository_path: Path) -> list[str]:
    result = _run_git(
        ["grep", "-l", pattern, "HEAD"],
        cwd=repository_path,
        capture_output=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line]


def _run_github_cli(
    arguments: Sequence[str],
    *,
    app_token: str,
    repository: str,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GH_TOKEN"] = app_token  # gh ignores APP_TOKEN
    return _run(
        ["gh", "--repo", repository, *arguments],
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        environment=environment,
    )


def _run_git(
    arguments: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["git", *arguments],
        cwd=cwd,
        check=check,
        capture_output=capture_output,
    )


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = False,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=capture_output,
        text=True,
        env=environment,
    )
    if check and result.returncode != 0:
        if capture_output and result.stderr:
            sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result


def main() -> None:
    run()


if __name__ == "__main__":
    main()
