import pytest

from app.services.analytics.kpi import _result_unit


@pytest.mark.parametrize(
    ("definition_unit", "concept_unit", "expected"),
    [
        (None, "days", "days"),
        ("days", None, "days"),
        ("currency", "days", "days"),
        ("L/100 km", "litres_per_100km", "L/100 km"),
        ("percent", "percentage", "percent"),
        (None, None, None),
    ],
)
def test_result_unit_preserves_measure_and_compatible_display_labels(
    definition_unit, concept_unit, expected
):
    assert _result_unit(definition_unit, concept_unit) == expected
