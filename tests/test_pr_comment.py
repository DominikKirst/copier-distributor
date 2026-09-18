from __future__ import annotations

from pathlib import Path

import pytest

from comment import (
    MARKER,
    SyncResult,
    load_results,
    match_job_label,
    render,
    target_row,
)


def sample_result(
    *,
    repository: str = "o/r",
    template_type: str = "lib",
    sync_type: str = "push",
    has_conflicts: bool = False,
    label: str = "",
    conflict_files: tuple[str, ...] = (),
) -> SyncResult:
    return SyncResult(
        repository=repository,
        template_type=template_type,
        sync_type=sync_type,
        label=label or repository,
        html_url=f"https://github.com/{repository}",
        has_conflicts=has_conflicts,
        conflict_files=conflict_files,
    )


class TestSyncResultFromMapping:
    def test_requires_repo_and_type(self) -> None:
        # GIVEN
        data = {"repo": "o/r"}
        # WHEN
        result = SyncResult.from_mapping(data)
        # THEN
        assert result is None

    def test_fills_defaults(self) -> None:
        # GIVEN
        data = {"repo": "o/r", "type": "lib", "conflict_files": ["b.md", "", "a.md"]}
        # WHEN
        result = SyncResult.from_mapping(data)
        # THEN
        assert result == SyncResult(
            repository="o/r",
            template_type="lib",
            sync_type="push",
            label="",
            html_url="",
            has_conflicts=False,
            conflict_files=("b.md", "a.md"),
        )


class TestLoadResults:
    def test_missing_root_is_empty(self, tmp_path: Path) -> None:
        # GIVEN
        root = tmp_path / "missing"
        # WHEN
        rows = load_results(root)
        # THEN
        assert rows == []

    def test_skips_invalid_and_incomplete_json(self, tmp_path: Path) -> None:
        # GIVEN
        (tmp_path / "bad.json").write_text("{", encoding="utf-8")
        (tmp_path / "partial.json").write_text('{"repo": "o/r"}', encoding="utf-8")
        (tmp_path / "ok.json").write_text(
            '{"repo": "o/r", "type": "lib"}', encoding="utf-8"
        )
        # WHEN
        rows = load_results(tmp_path)
        # THEN
        assert rows == [
            SyncResult(repository="o/r", template_type="lib"),
        ]


class TestSortKey:
    def test_orders_by_type_then_sync(self) -> None:
        # GIVEN
        lib_pr = sample_result(template_type="lib", sync_type="pr", repository="o/b")
        config_push = sample_result(
            template_type="config", sync_type="push", repository="o/a"
        )
        # WHEN
        ordered = sorted([lib_pr, config_push], key=lambda item: item.sort_key)
        # THEN
        assert ordered[0].template_type == "config"
        assert ordered[1].template_type == "lib"


class TestMatchJobLabel:
    @pytest.mark.parametrize(
        ("job_name", "labels", "expected"),
        [
            pytest.param(
                "lib.copier-client-lib-pr (dry-run)",
                ["lib.copier-client-lib", "lib.copier-client-lib-pr"],
                "lib.copier-client-lib-pr",
                id="longest-dry-run",
            ),
            pytest.param(
                "lib.copier-client-lib sync",
                ["lib.copier-client-lib", "lib.copier-client-lib-pr"],
                "lib.copier-client-lib",
                id="exact-sync",
            ),
            pytest.param(
                "unrelated",
                ["lib.copier-client-lib"],
                "",
                id="no-match",
            ),
        ],
    )
    def test_prefers_longest_suffix(
        self, job_name: str, labels: list[str], expected: str
    ) -> None:
        # GIVEN
        # WHEN
        matched = match_job_label(job_name=job_name, labels=labels)
        # THEN
        assert matched == expected


class TestTargetRow:
    def test_renders_markdown_link(self) -> None:
        # GIVEN
        row = sample_result(repository="o/copier-client-lib", template_type="lib")
        # WHEN
        line = target_row(row)
        # THEN
        assert (
            line
            == "| `lib` | `push` | [copier-client-lib](https://github.com/o/copier-client-lib) |"
        )


class TestRender:
    def test_empty_is_checkmark_with_no_targets(self) -> None:
        # GIVEN
        rows: list[SyncResult] = []
        # WHEN
        body = render(rows)
        # THEN
        assert MARKER in body
        assert "## ✅ Sync Preview" in body
        assert "No targets in this diff" in body
        assert "Affected Targets (0)" in body
        assert "Conflicts (0)" in body

    def test_conflicts_use_warning_headline(self) -> None:
        # GIVEN
        rows = [
            sample_result(
                repository="o/r",
                has_conflicts=True,
                conflict_files=("README.md",),
                label="lib.r",
            )
        ]
        # WHEN
        body = render(rows)
        # THEN
        assert "## ⚠️ Sync Preview" in body
        assert "**1** conflict(s) — fan-out would stall" in body
        assert "`README.md`" in body
        assert "Affected Targets (1)" in body

    def test_clean_targets_use_checkmark(self) -> None:
        # GIVEN
        rows = [
            sample_result(repository="o/a"),
            sample_result(repository="o/b", template_type="config"),
        ]
        # WHEN
        body = render(rows)
        # THEN
        assert "## ✅ Sync Preview" in body
        assert "**2** target(s) would sync without conflicts" in body
        assert "Conflicts (0)" in body
