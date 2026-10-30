import sqlite3

from backend.usage_deltas import counter_delta, fresh_start_delta_sql


def test_counter_delta_counts_growth():
    result = counter_delta(1_000, 1_500)

    assert result.delta == 500
    assert result.dropped is False


def test_counter_delta_drop_is_a_fresh_start():
    first = counter_delta(4_200_000_000, 12_000_000)
    second = counter_delta(12_000_000, 20_000_000)

    assert first.delta == 12_000_000
    assert first.dropped is True
    assert second.delta == 8_000_000
    assert second.dropped is False


def test_counter_delta_reset_to_zero_counts_nothing_until_growth():
    assert counter_delta(6_830_000_000, 0).delta == 0
    assert counter_delta(0, 27_000_000_000).delta == 27_000_000_000


def test_fresh_start_delta_sql_matches_python_rule():
    conn = sqlite3.connect(":memory:")
    expr = fresh_start_delta_sql("cur", "prev")
    cases = [(None, 5), (100, 150), (4_200_000_000, 12_000_000), (10, 10)]
    for prev, cur in cases:
        value = conn.execute(f"SELECT {expr} FROM (SELECT ? AS prev, ? AS cur)", (prev, cur)).fetchone()[0]
        expected = 0 if prev is None else counter_delta(prev, cur).delta
        assert value == expected
    conn.close()
