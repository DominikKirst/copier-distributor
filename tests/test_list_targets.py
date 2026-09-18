from __future__ import annotations

import sys
from pathlib import Path

import pytest

from list_targets import as_bool, load_targets, normalize


class TestAsBool:
    @pytest.mark.parametrize(
        ("value", "default", "expected"),
        [
            pytest.param(None, True, True, id="none-default-true"),
            pytest.param(None, False, False, id="none-default-false"),
            pytest.param(True, False, True, id="bool-true"),
            pytest.param(False, True, False, id="bool-false"),
            pytest.param("true", False, True, id="true"),
            pytest.param("YES", False, True, id="yes"),
            pytest.param("1", False, True, id="one"),
            pytest.param("false", True, False, id="false"),
            pytest.param("0", True, False, id="zero"),
            pytest.param("no", True, False, id="no"),
            pytest.param(2, False, True, id="truthy-int"),
        ],
    )
    def test_parses_values(
        self, value: object, default: bool, expected: bool
    ) -> None:
        # GIVEN
        # WHEN
        result = as_bool(value, default=default)
        # THEN
        assert result is expected

    def test_rejects_unknown_string(self) -> None:
        # GIVEN
        value = "maybe"
        # WHEN / THEN
        with pytest.raises(SystemExit):
            as_bool(value, default=False)


class TestNormalize:
    def test_push_defaults(self) -> None:
        # GIVEN
        entry = {"repo": "o/r", "branch": "develop"}
        # WHEN
        out = normalize(entry, type_name="lib", name="r")
        # THEN
        assert out["label"] == "lib.r"
        assert out["sync_type"] == "push"
        assert out["automerge"] is False
        assert "sync" not in out

    def test_pr_automerge_defaults_true(self) -> None:
        # GIVEN
        entry = {"repo": "o/r", "sync": {"type": "pr"}}
        # WHEN
        out = normalize(entry, type_name="lib", name="r")
        # THEN
        assert out["sync_type"] == "pr"
        assert out["automerge"] is True

    def test_pr_automerge_can_disable(self) -> None:
        # GIVEN
        entry = {"repo": "o/r", "sync": {"type": "pr", "automerge": "false"}}
        # WHEN
        out = normalize(entry, type_name="lib", name="r")
        # THEN
        assert out["automerge"] is False

    def test_event_never_automerge(self) -> None:
        # GIVEN
        entry = {"repo": "o/r", "sync": {"type": "event", "automerge": "true"}}
        # WHEN
        out = normalize(entry, type_name="lib", name="r")
        # THEN
        assert out["sync_type"] == "event"
        assert out["automerge"] is False

    def test_unknown_sync_type_exits(self) -> None:
        # GIVEN
        entry = {"repo": "o/r", "sync": {"type": "ftp"}}
        # WHEN / THEN
        with pytest.raises(SystemExit):
            normalize(entry, type_name="lib", name="r")


class TestLoadTargets:
    @pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")
    def test_reads_toml_groups(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # GIVEN
        targets = tmp_path / "targets.toml"
        targets.write_text(
            """
[[config.foo]]
repo = "o/foo"
branch = "develop"

[[lib.bar]]
repo = "o/bar"
branch = "develop"
sync.type = "pr"
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("TARGETS_FILE", str(targets))
        # WHEN
        groups = load_targets()
        # THEN
        assert len(groups["config"]) == 1
        assert groups["config"][0]["label"] == "config.foo"
        assert groups["lib"][0]["sync_type"] == "pr"
        assert groups["deployable"] == []
