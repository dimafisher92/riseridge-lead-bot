import pytest

from leadbot.bot.score import SIGNAL_ORDER, lead_score, lead_tier, load_scoring, primary_flag

S = load_scoring()
NICHE = "Counselors / therapists"
B1 = {"gbp_unverified_or_suspended", "broken_website", "new_business", "rank_gap", "competitor_gap"}


def test_weights_match_sop():
    assert S.weights == {
        "gbp_unverified_or_suspended": 25, "google_ads_spend": 20,
        "google_ads_spend_over_3k_bonus": 10, "meta_ads_active": 10, "broken_website": 15,
        "new_business": 15, "rank_gap": 10, "technical_debt": 5, "competitor_gap": 5,
        "retention_niche": 5, "negative_signal": 30}
    assert (S.hot, S.warm) == (70, 45)


def test_b1_maximum_is_75():
    assert lead_score(B1, NICHE, S) == 75


def test_retention_bonus_only_for_listed_niches():
    assert lead_score(set(), "Roofing", S) == 0
    assert lead_score(set(), NICHE, S) == 5


def test_ads_bonus_needs_more_than_3000():
    assert lead_score({"google_ads_spend"}, "Roofing", S, ads_spend=3500) == 30
    assert lead_score({"google_ads_spend"}, "Roofing", S, ads_spend=3000) == 20


def test_score_is_capped_at_100():
    assert lead_score(set(SIGNAL_ORDER), NICHE, S, ads_spend=5000) == 100


@pytest.mark.parametrize("score,tier", [(0, "Cold"), (44, "Cold"), (45, "Warm"),
                                        (69, "Warm"), (70, "Hot"), (100, "Hot")])
def test_tier_boundaries(score, tier):
    assert lead_tier(score, S) == tier


def test_primary_is_highest_weight():
    assert primary_flag({"competitor_gap", "gbp_unverified_or_suspended"}, S) == \
        "gbp_unverified_or_suspended"


def test_primary_tie_follows_sop_table_order():
    assert primary_flag({"new_business", "broken_website"}, S) == "broken_website"


def test_no_signals_no_primary():
    assert primary_flag(set(), S) is None


def test_negative_signal_subtracts_30_after_positives_and_bonus():
    assert lead_score(B1, NICHE, S, negatives=("inactive_listing",)) == 45
    assert lead_score(B1, NICHE, S, negatives=()) == 75


def test_negative_score_floors_at_zero():
    assert lead_score({"rank_gap", "competitor_gap"}, "Roofing", S) == 15
    assert lead_score({"new_business"}, "Roofing", S, negatives=("inactive_listing",)) == 0
    assert lead_score(set(), "Roofing", S, negatives=("inactive_listing",)) == 0


def test_negatives_never_change_the_primary_flag():
    assert primary_flag({"rank_gap"}, S) == "rank_gap"
