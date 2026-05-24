import pytest

from relative_value.reference_odds import american_to_implied_probability, no_vig_probabilities


def test_american_to_implied_probability() -> None:
    assert american_to_implied_probability(-110) == pytest.approx(0.5238095)
    assert american_to_implied_probability(150) == pytest.approx(0.4)


def test_no_vig_probabilities_sum_to_one() -> None:
    probabilities = no_vig_probabilities({"A": -120, "B": 100})
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert set(probabilities) == {"A", "B"}


def test_no_vig_requires_two_outcomes() -> None:
    with pytest.raises(ValueError):
        no_vig_probabilities({"A": -120})
