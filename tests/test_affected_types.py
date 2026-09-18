from __future__ import annotations

import pytest

from affected_types import ALL_TYPES, affected_types, type_prefix


class TestTypePrefix:
    def test_points_at_template_type_dir(self) -> None:
        # GIVEN
        type_name = "lib"
        # WHEN
        prefix = type_prefix(type_name)
        # THEN
        assert prefix == "template/lib/"


class TestAffectedTypes:
    @pytest.mark.parametrize(
        ("paths", "expected"),
        [
            pytest.param(["copier.yml"], set(ALL_TYPES), id="copier-yml"),
            pytest.param(
                ["template/_shared/README.md"],
                set(ALL_TYPES),
                id="shared",
            ),
            pytest.param(["template/lib/foo.py"], {"lib"}, id="lib-only"),
            pytest.param(
                ["template/config/a.md", "template/deployable/b.md"],
                {"config", "deployable"},
                id="two-types",
            ),
            pytest.param(["README.md"], set(), id="unrelated"),
            pytest.param([], set(), id="empty"),
        ],
    )
    def test_maps_paths_to_types(self, paths: list[str], expected: set[str]) -> None:
        # GIVEN
        changed = paths
        # WHEN
        types = affected_types(changed)
        # THEN
        assert types == expected
