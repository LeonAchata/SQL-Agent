from sqlagent.db.executor import QueryResult
from sqlagent.evals import results_match


def qr(columns: list[str], rows: list[list[object]]) -> QueryResult:
    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows), truncated=False, elapsed_ms=0
    )


def test_extra_columns_and_order_are_tolerated() -> None:
    gold = qr(["country"], [["UK"], ["USA"]])
    pred = qr(["n", "Country"], [[2, "usa"], [1, "UK"]])
    assert results_match(gold, pred, ordered=False)
    assert not results_match(gold, pred, ordered=True)


def test_numbers_are_compared_with_rounding() -> None:
    gold = qr(["year", "rev"], [["2009", 449.46]])
    pred = qr(["y", "revenue"], [[2009, 449.4600001]])
    assert results_match(gold, pred, ordered=True)


def test_mismatch_detected() -> None:
    assert not results_match(qr(["a"], [[1]]), qr(["a"], [[2]]), ordered=False)
    assert not results_match(qr(["a"], [[1]]), qr(["a"], [[1], [1]]), ordered=False)


def test_columns_cannot_be_reused() -> None:
    gold = qr(["a", "b"], [[1, 1]])
    pred = qr(["x"], [[1]])
    assert not results_match(gold, pred, ordered=False)
