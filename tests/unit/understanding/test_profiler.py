# tests/unit/understanding/test_profiler.py
from app.services.understanding.profiler import profile_rows


def test_empty_input() -> None:
    r = profile_rows([])
    assert r.row_count == 0
    assert r.column_count == 0
    assert r.columns == []


def test_basic_types() -> None:
    rows = [
        {"id": 1, "price": 1.5, "name": "A", "date": "2026-01-01", "active": True},
        {"id": 2, "price": 2.0, "name": "B", "date": "2026-01-02", "active": False},
        {"id": 3, "price": 3.5, "name": "C", "date": "2026-01-03", "active": True},
    ]
    r = profile_rows(rows)
    by_name = {c.name: c for c in r.columns}
    assert by_name["id"].inferred_type == "integer"
    assert by_name["price"].inferred_type == "float"
    assert by_name["name"].inferred_type == "string"
    assert by_name["date"].inferred_type == "date"
    assert by_name["active"].inferred_type == "boolean"
    assert r.row_count == 3
    assert r.column_count == 5


def test_nulls_and_missingness() -> None:
    rows = [{"a": 1}, {"a": None}, {"a": ""}, {"a": 4}]
    r = profile_rows(rows)
    col = r.columns[0]
    assert col.null_count == 2
    assert col.null_pct == 0.5


def test_candidate_key_single_col() -> None:
    rows = [{"id": 1, "x": "a"}, {"id": 2, "x": "a"}, {"id": 3, "x": "a"}]
    r = profile_rows(rows)
    keys = {tuple(k.columns) for k in r.candidate_keys}
    assert ("id",) in keys
    assert ("x",) not in keys


def test_duplicate_rows() -> None:
    rows = [{"a": 1}, {"a": 1}, {"a": 2}]
    r = profile_rows(rows)
    assert r.duplicate_row_count == 1


def test_mixed_type_column() -> None:
    rows = [{"v": "1"}, {"v": "abc"}, {"v": "2.5"}, {"v": "xyz"}]
    r = profile_rows(rows)
    assert r.columns[0].inferred_type == "mixed"


def test_95pct_threshold_allows_small_dirt() -> None:
    # 19 valid dates, 1 garbage -> 95% valid -> inferred as date
    rows = [{"d": "2026-01-01"} for _ in range(19)] + [{"d": "not-a-date"}]
    r = profile_rows(rows)
    assert r.columns[0].inferred_type == "date"