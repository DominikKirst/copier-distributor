from __future__ import annotations

from pathlib import Path

import pytest

from comment import (
    MARKER,
    load_results,
    match_job_label,
    render,
    sort_key,
    target_row,
)


def sample_row(
    *,
    repo: str = "o/r",
    type_name: str = "lib",
    sync_type: str = "push",
    has_conflicts: bool = False,
    label: str = "",
    conflict_files: list[str] | None = None,
) -> dict[str, object]:
    return {
        "repo": repo,
        "type": type_name,
        "sync_type": sync_type,
        "has_conflicts": has_conflicts,
        "label": label or repo,
        "html_url": f"https://github.com/{repo}",
        "conflict_files": conflict_files or [],
    }


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
        assert rows == [{"repo": "o/r", "type": "lib"}]


class TestSortKey:
    def test_orders_by_type_then_sync(self) -> None:
        # GIVEN
        lib_pr = sample_row(type_name="lib", sync_type="pr", repo="o/b")
        config_push = sample_row(type_name="config", sync_type="push", repo="o/a")
        # WHEN
        ordered = sorted([lib_pr, config_push], key=sort_key)
        # THEN
        assert ordered[0]["type"] == "config"
        assert ordered[1]["type"] == "lib"


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
        row = sample_row(repo="o/copier-client-lib", type_name="lib")
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
        rows: list[dict[str, object]] = []
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
            sample_row(
                repo="o/r",
                has_conflicts=True,
                conflict_files=["README.md"],
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
        rows = [sample_row(repo="o/a"), sample_row(repo="o/b", type_name="config")]
        # WHEN
        body = render(rows)
        # THEN
        assert "## ✅ Sync Preview" in body
        assert "**2** target(s) would sync without conflicts" in body
        assert "Conflicts (0)" in body
