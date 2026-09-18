from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from helpers import (
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


def sample_config() -> SyncConfig:
    return SyncConfig(
        app_token="token",
        repository="o/r",
        branch="develop",
        template_type="lib",
        vcs_ref="abc",
        sync_type="push",
        dry_run=False,
        automerge=False,
        label="lib.r",
        result_file=Path("sync-result.json"),
        github_workspace=None,
    )


class TestParseBool:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("true", True, id="true"),
            pytest.param("TRUE", True, id="uppercase"),
            pytest.param("1", True, id="one"),
            pytest.param("yes", True, id="yes"),
            pytest.param("false", False, id="false"),
            pytest.param("0", False, id="zero"),
            pytest.param("", False, id="empty"),
        ],
    )
    def test_parses_common_strings(self, value: str, expected: bool) -> None:
        # GIVEN
        raw = value
        # WHEN
        result = parse_bool(raw)
        # THEN
        assert result is expected


class TestRepositoryName:
    def test_strips_owner(self) -> None:
        # GIVEN
        repository = "DominikKirst/copier-client-lib"
        # WHEN
        name = repository_name(repository)
        # THEN
        assert name == "copier-client-lib"


class TestResolveLabel:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            pytest.param("", "copier-client-lib", id="fallback-to-repo-name"),
            pytest.param("lib.x", "lib.x", id="explicit"),
        ],
    )
    def test_uses_label_or_repository_name(self, label: str, expected: str) -> None:
        # GIVEN
        repository = "o/copier-client-lib"
        # WHEN
        resolved = resolve_label(repository=repository, label=label)
        # THEN
        assert resolved == expected


class TestResolveResultFile:
    @pytest.mark.parametrize(
        ("result_file", "workspace", "expected"),
        [
            pytest.param(
                "/tmp/out.json",
                "/ws",
                Path("/tmp/out.json"),
                id="explicit",
            ),
            pytest.param(
                "",
                "/ws",
                Path("/ws/sync-result.json"),
                id="workspace-default",
            ),
            pytest.param(
                "",
                "",
                Path("sync-result.json"),
                id="cwd-default",
            ),
        ],
    )
    def test_prefers_explicit_then_workspace(
        self, result_file: str, workspace: str, expected: Path
    ) -> None:
        # GIVEN
        # WHEN
        path = resolve_result_file(result_file=result_file, workspace=workspace)
        # THEN
        assert path == expected


class TestResultPayload:
    def test_sorts_and_drops_empty_paths(self) -> None:
        # GIVEN
        config = sample_config()
        report = ConflictReport(has_conflicts=True, files=("b.md", "", "a.md"))
        # WHEN
        payload = result_payload(config=config, report=report)
        # THEN
        assert payload == {
            "type": "lib",
            "sync_type": "push",
            "repo": "o/r",
            "label": "lib.r",
            "has_conflicts": True,
            "html_url": "https://github.com/o/r",
            "conflict_files": ["a.md", "b.md"],
        }


class TestDetectConflicts:
    def test_markers_count_even_when_merge_is_clean(self) -> None:
        # GIVEN
        outcome = MergeOutcome(
            marker_files=["README.md"],
            merge_succeeded=True,
            unmerged_files=[],
        )
        # WHEN
        report = detect_conflicts(outcome=outcome)
        # THEN
        assert report == ConflictReport(has_conflicts=True, files=("README.md",))

    def test_unmerged_paths_are_deduped(self) -> None:
        # GIVEN
        outcome = MergeOutcome(
            marker_files=[],
            merge_succeeded=False,
            unmerged_files=["src/a.py", "src/a.py"],
        )
        # WHEN
        report = detect_conflicts(outcome=outcome)
        # THEN
        assert report == ConflictReport(has_conflicts=True, files=("src/a.py",))

    def test_clean_merge_without_markers(self) -> None:
        # GIVEN
        outcome = MergeOutcome(
            marker_files=[""],
            merge_succeeded=True,
            unmerged_files=[],
        )
        # WHEN
        report = detect_conflicts(outcome=outcome)
        # THEN
        assert report == ConflictReport(has_conflicts=False, files=())

    def test_failed_merge_without_names_still_conflicts(self) -> None:
        # GIVEN
        outcome = MergeOutcome(
            marker_files=[],
            merge_succeeded=False,
            unmerged_files=[],
        )
        # WHEN
        report = detect_conflicts(outcome=outcome)
        # THEN
        assert report == ConflictReport(has_conflicts=True, files=())


class TestShouldDispatchEvent:
    @pytest.mark.parametrize(
        ("dry_run", "expected"),
        [
            pytest.param(True, False, id="dry-run"),
            pytest.param(False, True, id="execute"),
        ],
    )
    def test_skips_dry_run(self, dry_run: bool, expected: bool) -> None:
        # GIVEN
        config = replace(sample_config(), dry_run=dry_run)
        # WHEN
        result = should_dispatch_event(config=config)
        # THEN
        assert result is expected


class TestShouldAutomerge:
    @pytest.mark.parametrize(
        ("automerge", "has_conflicts", "expected"),
        [
            pytest.param(True, False, True, id="clean-pr"),
            pytest.param(True, True, False, id="conflicts"),
            pytest.param(False, False, False, id="disabled"),
        ],
    )
    def test_requires_flag_and_clean_tree(
        self, automerge: bool, has_conflicts: bool, expected: bool
    ) -> None:
        # GIVEN
        # WHEN
        config = replace(sample_config(), automerge=automerge)
        report = ConflictReport(has_conflicts=has_conflicts, files=())
        # WHEN
        result = should_automerge(config=config, report=report)
        # THEN
        assert result is expected


class TestApplyAction:
    @pytest.mark.parametrize(
        ("sync_type", "dry_run", "has_conflicts", "expected"),
        [
            pytest.param("push", True, False, "skip", id="dry-run"),
            pytest.param("pr", False, False, "pull_request", id="pr-mode"),
            pytest.param("push", False, True, "pull_request", id="push-conflicts"),
            pytest.param("push", False, False, "push", id="push-clean"),
        ],
    )
    def test_selects_apply_path(
        self,
        sync_type: str,
        dry_run: bool,
        has_conflicts: bool,
        expected: str,
    ) -> None:
        # GIVEN
        # WHEN
        config = replace(sample_config(), sync_type=sync_type, dry_run=dry_run)
        report = ConflictReport(has_conflicts=has_conflicts, files=())
        # WHEN
        action = apply_action(config=config, report=report)
        # THEN
        assert action == expected


class TestPullRequestTitle:
    def test_uses_utc_date(self) -> None:
        # GIVEN
        moment = datetime(2026, 9, 18, 23, 0, tzinfo=timezone.utc)
        # WHEN
        title = pull_request_title(moment=moment)
        # THEN
        assert title == "chore: template sync 2026-09-18"


class TestComposePullRequestBody:
    @pytest.mark.parametrize(
        ("existing_body", "commits", "expected"),
        [
            pytest.param("old\n", "abc msg", "old\n\nabc msg", id="append"),
            pytest.param("", "abc", "abc", id="empty-body"),
            pytest.param("old", "", "old", id="empty-commits"),
        ],
    )
    def test_joins_existing_body_and_commits(
        self, existing_body: str, commits: str, expected: str
    ) -> None:
        # GIVEN
        # WHEN
        body = compose_pull_request_body(
            existing_body=existing_body, commits=commits
        )
        # THEN
        assert body == expected


class TestParsePullRequestNumber:
    @pytest.mark.parametrize(
        ("stdout", "expected"),
        [
            pytest.param(" 12\n", "12", id="number"),
            pytest.param("\n", "", id="empty"),
        ],
    )
    def test_strips_gh_jq_output(self, stdout: str, expected: str) -> None:
        # GIVEN
        # WHEN
        number = parse_pull_request_number(stdout)
        # THEN
        assert number == expected


class TestWarningAnnotation:
    def test_formats_actions_warning(self) -> None:
        # GIVEN
        path = "README.md"
        config = sample_config()
        # WHEN
        annotation = warning_annotation(path=path, config=config)
        # THEN
        assert annotation == "::warning file=README.md,title=lib.r::Copier conflict"


class TestConflictFlagPath:
    def test_sits_in_workspace_root(self) -> None:
        # GIVEN
        workspace = Path("/ws")
        # WHEN
        path = conflict_flag_path(workspace)
        # THEN
        assert path == Path("/ws/copier-conflicts.flag")


class TestEventCommand:
    def test_dispatches_template_sync_on_client(self) -> None:
        # GIVEN
        config = sample_config()
        # WHEN
        command = event_command(config=config)
        # THEN
        assert command == (
            "gh workflow run template-sync.yml --repo o/r --ref develop "
            "-f vcs_ref=abc -f dry_run=false"
        )


class TestPlanLines:
    def test_event_dry_run_does_not_dispatch(self) -> None:
        # GIVEN
        config = replace(
            sample_config(),
            sync_type="event",
            dry_run=True,
            label="lib.event",
        )
        # WHEN
        lines = plan_lines(config=config)
        # THEN
        assert lines[0] == "sync_type=event"
        assert "# dry_run: not dispatched" in lines
        assert any(line.startswith("gh workflow run") for line in lines)
