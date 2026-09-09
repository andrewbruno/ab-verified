"""ABN handling: the modulus-89 checksum (FR-104) and the name-match score
(FR-202)."""

from __future__ import annotations

import pytest

from app.demo.fixture import demo_abn
from app.domain import abn

# Real, published ABNs, used here only as arithmetic that is known to satisfy
# the ATO checksum.
VALID = [
    "51824753556",   # Australian Taxation Office
    "53004085616",   # Telstra Corporation Limited
    "83914571673",   # Commonwealth Bank of Australia
    "29002589460",
    "48123123124",
]

INVALID = [
    "12345678901",   # sequential digits, fails the weighting
    "51824753557",   # a single transposed final digit
    "11223344556",
    "00000000000",
    "99999999999",
]


@pytest.mark.parametrize("value", VALID)
def test_fr104_a_valid_abn_passes_the_modulus_89_check(value):
    assert abn.is_valid(value) is True


@pytest.mark.parametrize("value", INVALID)
def test_fr104_an_invalid_abn_fails_the_modulus_89_check(value):
    assert abn.is_valid(value) is False


@pytest.mark.parametrize("value", ["", "   ", "5182475355", "518247535566", "abcdefghijk", None])
def test_fr104_anything_that_is_not_eleven_digits_fails(value):
    assert abn.is_valid(value) is False


def test_fr104_the_check_ignores_spaces_and_punctuation():
    assert abn.is_valid("51 824 753 556") is True
    assert abn.is_valid("51-824-753-556") is True


def test_normalise_keeps_digits_only():
    assert abn.normalise(" 51 824-753.556 ") == "51824753556"
    assert abn.normalise(None) == ""


def test_format_abn_uses_the_two_three_three_three_grouping():
    assert abn.format_abn("51824753556") == "51 824 753 556"
    assert abn.format_abn("51 824 753 556") == "51 824 753 556"


def test_format_abn_returns_the_input_unchanged_when_it_is_not_an_abn():
    assert abn.format_abn("not an abn") == "not an abn"


def test_the_demo_fixture_only_mints_structurally_valid_abns():
    """The fixture passes the same check a real registration does, so a demo
    walk-through exercises FR-104 rather than skipping it."""
    for seed in ("bayside", "meridian", "southern", "bunya", "warrigal"):
        assert abn.is_valid(demo_abn(seed)) is True


# --------------------------------------------------------------------------
# FR-202: the name-match score is evidence for staff, not a gate.
# --------------------------------------------------------------------------
def test_fr202_an_exact_match_scores_at_the_top():
    assert abn.name_match_score(
        "Meridian Cloud Works Pty Ltd", "MERIDIAN CLOUD WORKS PTY LTD"
    ) == 100


def test_fr202_pty_ltd_noise_does_not_depress_an_otherwise_exact_match():
    """The registered entity carries the suffix and the applicant often does
    not. That is not a mismatch."""
    assert abn.name_match_score("Bunya Networks", "BUNYA NETWORKS PTY LTD") >= 95
    assert abn.name_match_score(
        "The Coolabah Technologies Group", "COOLABAH TECHNOLOGIES PTY LTD"
    ) >= 90


def test_fr202_case_and_punctuation_do_not_matter():
    assert abn.name_match_score(
        "bayside health services", "BAYSIDE HEALTH SERVICES PTY. LTD."
    ) >= 95


def test_fr202_an_unrelated_name_scores_low():
    """The fixture's name-mismatch applicant: the score is what puts it in
    front of staff."""
    assert abn.name_match_score(
        "Warrigal IT Solutions", "M J HOLDINGS (AUST) PTY LTD"
    ) < 40


def test_fr202_a_partial_match_lands_in_between():
    score = abn.name_match_score("Meridian Cloud", "MERIDIAN CLOUD WORKS PTY LTD")
    assert 40 < score < 100


def test_fr202_an_empty_side_scores_zero_rather_than_matching_everything():
    assert abn.name_match_score("", "MERIDIAN CLOUD WORKS PTY LTD") == 0
    assert abn.name_match_score("Meridian Cloud Works", "") == 0
    assert abn.name_match_score("Pty Ltd", "PTY LTD") == 0


def test_fr202_the_score_is_always_within_range():
    pairs = [
        ("Bayside Health Services Pty Ltd", "BAYSIDE HEALTH SERVICES PTY LTD"),
        ("Southern Cross Digital", "NORTHERN LIGHTS TRADING"),
        ("A", "B"),
    ]
    for submitted, registered in pairs:
        assert 0 <= abn.name_match_score(submitted, registered) <= 100
