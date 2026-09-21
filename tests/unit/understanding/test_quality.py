# tests/unit/understanding/test_quality.py
from app.services.understanding.profiler import profile_rows
from app.services.understanding.quality import assess_quality


def _codes(issues) -> set[str]:
    return {i.code for i in issues}


def test_high_missingness_warning() -> None:
    rows = [{"a": i if i % 4 == 0 else None} for i in range(20)]
    # 15/20 null = 75%
    profile = profile_rows(rows)
    issues = assess_quality(profile, rows)
    assert "high_missingness" in _codes(issues)


def test_mixed_types_flagged() -> None:
    rows = [{"v": "1"}, {"v": "abc"}, {"v": "2.5"}, {"v": "xyz"}]
    profile = profile_rows(rows)
    issues = assess_quality(profile, rows)
    assert "mixed_types" in _codes(issues)


def test_impossible_quantity_negative() -> None:
    rows = [{"order_quantity": 5}, {"order_quantity": -3}, {"order_quantity": 10}]
    profile = profile_rows(rows)
    issues = assess_quality(profile, rows)
    assert "impossible_quantity" in _codes(issues)


def test_impossible_quantity_ignores_non_quantity_negative() -> None:
    rows = [{"balance": -100}, {"balance": 50}]
    profile = profile_rows(rows)
    issues = assess_quality(profile, rows)
    assert "impossible_quantity" not in _codes(issues)


def test_inconsistent_identifier() -> None:
    rows = [{"supplier": "ACME"}, {"supplier": "acme"}, {"supplier": "Acme"}, {"supplier": "Other"}]
    profile = profile_rows(rows)
    issues = assess_quality(profile, rows)
    assert "inconsistent_identifier" in _codes(issues)


def test_duplicate_entity_flag() -> None:
    rows = [{"supplier_id": 1}, {"supplier_id": 1}, {"supplier_id": 2}]
    profile = profile_rows(rows)
    issues = assess_quality(profile, rows)
    assert "duplicate_entity" in _codes(issues)


def test_clean_data_no_issues() -> None:
    rows = [
        {"id": 1, "qty": 5, "supplier": "ACME"},
        {"id": 2, "qty": 3, "supplier": "Beacon"},
        {"id": 3, "qty": 8, "supplier": "Cobalt"},
    ]
    profile = profile_rows(rows)
    issues = assess_quality(profile, rows)
    assert issues == []