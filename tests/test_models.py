from __future__ import annotations

import pytest

from magia_updater.core.models import Version


def test_version_parse_and_display() -> None:
    assert str(Version.parse("v6.5.4")) == "6.5.4"
    assert str(Version.parse("1.12.1")) == "1.12.1"
    assert str(Version.parse("1.6.3.0-b962f75")) == "1.6.3.0"
    assert Version.parse("v3.6.1").parts == (3, 6, 1)


def test_version_equivalence_ignores_trailing_zeroes() -> None:
    assert Version.parse("1.12.1") == Version.parse("1.12.1.0")
    assert hash(Version.parse("1.12.1")) == hash(Version.parse("1.12.1.0"))
    assert Version.parse("1.6.3.0") == Version.parse("1.6.3")


def test_version_ordering_is_numeric() -> None:
    assert Version.parse("1.9.5") < Version.parse("1.10.2")
    assert Version.parse("6.5.4") > Version.parse("6.4.9")
    assert Version.parse("1.12.1") < Version.parse("1.12.2")
    assert Version.parse("1.12.1") <= Version.parse("1.12.1.0")


def test_version_from_parts_keeps_display_text() -> None:
    version = Version.from_parts((1, 12, 1, 0), "1.12.1")
    assert version.parts == (1, 12, 1, 0)
    assert str(version) == "1.12.1"


@pytest.mark.parametrize("value", ["", "v", "abc", "1.2.beta", "1..2"])
def test_version_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        Version.parse(value)
