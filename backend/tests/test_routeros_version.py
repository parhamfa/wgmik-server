import pytest

from backend.routeros.version import assert_routeros_supported, is_routeros_supported, parse_routeros_version


@pytest.mark.parametrize(
    "value",
    ["7.15", "7.15.1", "7.16", "7.20.4 stable"],
)
def test_routeros_version_supported(value):
    assert parse_routeros_version(value) is not None
    assert is_routeros_supported(value) is True
    assert assert_routeros_supported(value).supported is True


@pytest.mark.parametrize(
    "value",
    ["7.14.9", "6.49.10", "", "not-a-version"],
)
def test_routeros_version_unsupported(value):
    assert is_routeros_supported(value) is False
    with pytest.raises(ValueError):
        assert_routeros_supported(value)
