import datetime as dt

from leadbot.bot.scrape import Place
from leadbot.bot.signals import (FALSE, TRUE, UNKNOWN, Enrichment, RankInfo, detect, gbp_status,
                                 rank_info, true_flags)
from leadbot.bot.site_check import SiteCheck

TODAY = dt.date(2026, 11, 6)
GOOD_SITE = SiteCheck(True, "https://test.example.com/", True, True, True, None)
TOP = RankInfo("therapist austin", 20, 1, 30.0)


def place(**over):
    base = dict(place_id="p1", name="Test Practice (fictional)", category="Counselor",
                categories=(), phone="+15125550140", website="https://test.example.com/",
                gbp_url=None, owner_claimed=True, status="", review_count=50, rating=4.5,
                review_dates=(dt.date(2025, 1, 1),), email=None)
    base.update(over)
    return Place(**base)


def sig(p=None, enrich=None, rank=TOP):
    return detect(p or place(), enrich or Enrichment(GOOD_SITE), rank, TODAY)


def test_claimed_and_unclaimed_listing():
    assert sig()["gbp_unverified_or_suspended"].state == FALSE
    assert sig(place(owner_claimed=False))["gbp_unverified_or_suspended"].state == TRUE
    assert gbp_status(place(owner_claimed=False)) == "Unverified"
    assert gbp_status(place()) == "Verified"


def test_broken_website_cases():
    assert sig(place(website=None), Enrichment(None))["broken_website"].state == TRUE
    assert sig(enrich=Enrichment(SiteCheck(False)))["broken_website"].state == TRUE
    assert sig(enrich=Enrichment(None))["broken_website"].state == UNKNOWN
    one = SiteCheck(True, "u", False, True, True, None)
    two = SiteCheck(True, "u", False, False, True, None)
    assert sig(enrich=Enrichment(one))["broken_website"].state == FALSE
    broken = sig(enrich=Enrichment(two))["broken_website"]
    assert broken.state == TRUE
    assert "no lead form" in broken.evidence and "no click-to-call link" in broken.evidence
    assert sig(enrich=Enrichment(one, pagespeed=30))["broken_website"].state == TRUE
    assert sig(enrich=Enrichment(one, pagespeed=40))["broken_website"].state == FALSE


def test_new_business_cases():
    recent = place(review_count=4, review_dates=(dt.date(2026, 9, 1), dt.date(2026, 10, 1)))
    assert sig(recent)["new_business"].state == TRUE
    old = place(review_count=4, review_dates=(dt.date(2026, 7, 1),))
    assert sig(old)["new_business"].state == FALSE
    many = place(review_count=16, review_dates=(dt.date(2026, 10, 1),))
    assert sig(many)["new_business"].state == FALSE
    by_domain = sig(place(review_count=500), Enrichment(GOOD_SITE, registered=dt.date(2026, 8, 1)))
    assert by_domain["new_business"].state == TRUE
    assert "2026-08-01" in by_domain["new_business"].evidence
    none = place(review_count=0, review_dates=())
    assert sig(none)["new_business"].state == UNKNOWN


def test_rank_gap_cases():
    assert sig(rank=RankInfo("k", 20, 3, 30.0))["rank_gap"].state == FALSE
    assert sig(rank=RankInfo("k", 20, 4, 30.0))["rank_gap"].state == TRUE
    missing = sig(rank=RankInfo("k", 20, None, 30.0))["rank_gap"]
    assert missing.state == TRUE and "not in the top 20" in missing.evidence
    assert sig(rank=RankInfo("k", 0, None, None))["rank_gap"].state == UNKNOWN


def test_competitor_gap_cases():
    assert sig(place(review_count=10), rank=RankInfo("k", 20, 5, 30.0))["competitor_gap"].state == TRUE
    assert sig(place(review_count=11), rank=RankInfo("k", 20, 5, 30.0))["competitor_gap"].state == FALSE
    assert sig(rank=RankInfo("k", 0, None, None))["competitor_gap"].state == UNKNOWN


def test_ad_and_audit_signals_are_unknown_in_b1():
    s = sig()
    assert {k for k, v in s.items() if v.state == UNKNOWN} >= {
        "google_ads_spend", "meta_ads_active", "technical_debt"}
    assert len(s) == 8


def test_true_flags():
    assert true_flags(sig(place(owner_claimed=False))) == {"gbp_unverified_or_suspended"}


def test_rank_info_excludes_self_from_top3():
    ps = [place(place_id="a", review_count=100), place(place_id="b", review_count=10),
          place(place_id="c", review_count=40), place(place_id="d", review_count=70)]
    r = rank_info("b", ps, "therapist austin")
    assert (r.position, r.checked, r.top3_avg_reviews) == (2, 4, 70.0)
    assert rank_info("zz", ps, "k").position is None
    assert rank_info("a", [], "k") == RankInfo("k", 0, None, None)
