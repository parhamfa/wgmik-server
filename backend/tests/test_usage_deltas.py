from backend.usage_deltas import CounterQuarantineState, counter_delta


def test_counter_delta_missed_zero_reset_uses_new_baseline_next_poll():
    first = counter_delta(4_200_000_000, 12_000_000)
    second = counter_delta(12_000_000, 20_000_000)

    assert first.delta == 0
    assert first.dropped is True
    assert first.near_32bit_drop is True
    assert second.delta == 8_000_000
    assert second.dropped is False


def test_counter_quarantine_zeroes_direction_for_rest_of_day():
    state = CounterQuarantineState()

    first = state.apply("tx", counter_delta(4_200_000_000, 12_000_000), "2026-06-16")
    second = state.apply("tx", counter_delta(12_000_000, 20_000_000), "2026-06-16")
    next_day = state.apply("tx", counter_delta(20_000_000, 25_000_000), "2026-06-17")

    assert first == 0
    assert second == 0
    assert next_day == 5_000_000
