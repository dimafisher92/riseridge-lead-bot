import datetime as dt

from leadbot.bot.scrape import Place
from leadbot.bot.signals import (FALSE, TRUE, UNKNOWN, Enrichment, RankInfo, Signal, brand_key,
                                 chain_keys, detect, gbp_status, negatives, rank_info, true_flags)
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
    no_form = SiteCheck(True, "u", False, True, True, None)
    no_form_no_call = SiteCheck(True, "u", False, False, True, None)
    assert sig(enrich=Enrichment(no_form))["broken_website"].state == FALSE
    broken = sig(enrich=Enrichment(no_form_no_call))["broken_website"]
    assert broken.state == TRUE
    assert "no lead form" in broken.evidence and "no click-to-call link" in broken.evidence
    assert sig(enrich=Enrichment(no_form, pagespeed=30))["broken_website"].state == TRUE
    assert sig(enrich=Enrichment(no_form, pagespeed=40))["broken_website"].state == FALSE


def test_missing_tracking_tag_alone_is_not_broken():
    tag_missing = SiteCheck(True, "u", True, True, False, None)
    result = sig(enrich=Enrichment(tag_missing))["broken_website"]
    assert result.state == FALSE and "no conversion tag" in result.evidence
    call_only = SiteCheck(True, "u", False, True, False, None)  # no form, no tag, call link present
    assert sig(enrich=Enrichment(call_only))["broken_website"].state == FALSE


def test_no_form_and_no_call_link_is_broken_and_lists_the_tag():
    result = sig(enrich=Enrichment(SiteCheck(True, "u", False, False, False, None)))["broken_website"]
    assert result.state == TRUE
    assert "no conversion tag" in result.evidence


def test_blocked_site_is_unknown():
    blocked = SiteCheck(False, "u", blocked=True, status=403)
    result = sig(enrich=Enrichment(blocked))["broken_website"]
    assert result == Signal(UNKNOWN, "website refused the bot's request (HTTP 403)")


def test_unreachable_site_evidence_includes_status_when_known():
    plain = sig(enrich=Enrichment(SiteCheck(False)))["broken_website"]
    assert plain == Signal(TRUE, "website https://test.example.com/ did not load")
    missing = sig(enrich=Enrichment(SiteCheck(False, "u", status=404)))["broken_website"]
    assert missing == Signal(TRUE, "website https://test.example.com/ did not load (HTTP 404)")


def test_new_business_cases():
    recent = place(review_count=2, review_dates=(dt.date(2026, 9, 1), dt.date(2026, 10, 1)))
    assert sig(recent)["new_business"].state == TRUE
    old = place(review_count=4, review_dates=(dt.date(2026, 7, 1),))
    assert sig(old)["new_business"].state == FALSE
    many = place(review_count=16, review_dates=(dt.date(2026, 10, 1),))
    assert sig(many)["new_business"].state == FALSE
    by_domain = sig(place(review_count=500), Enrichment(GOOD_SITE, registered=dt.date(2026, 8, 1)))
    assert by_domain["new_business"].state == TRUE
    assert "2026-08-01" in by_domain["new_business"].evidence
    none = place(review_count=0, review_dates=())
    assert sig(none)["new_business"] == Signal(UNKNOWN, "no reviews and no domain date")


def test_new_business_needs_every_review_visible():
    partial = place(review_count=4, review_dates=(dt.date(2026, 9, 1), dt.date(2026, 10, 1)))
    assert sig(partial)["new_business"] == Signal(UNKNOWN, "only 2 of 4 reviews visible")
    old_domain = Enrichment(GOOD_SITE, registered=dt.date(2015, 1, 1))
    assert sig(partial, old_domain)["new_business"].state == UNKNOWN
    new_domain = Enrichment(GOOD_SITE, registered=dt.date(2026, 8, 1))
    assert sig(partial, new_domain)["new_business"].state == TRUE  # domain wins regardless


def test_new_business_review_window_and_cap():
    def at(days, count=1):
        dates = tuple(TODAY - dt.timedelta(days=days) for _ in range(count))
        return sig(place(review_count=count, review_dates=dates))["new_business"].state
    assert at(92) == TRUE and at(93) == FALSE
    assert at(10, count=15) == TRUE and at(10, count=16) == FALSE


def test_new_business_reviews_without_dates():
    undated = place(review_count=3, review_dates=())
    assert sig(undated)["new_business"] == Signal(UNKNOWN, "no review dates and no domain date")


def test_rank_gap_cases():
    pack = sig(rank=RankInfo("k", 20, 3, 30.0))["rank_gap"]
    assert pack == Signal(FALSE, "#3 on Maps for 'k' (map pack)")
    near = sig(rank=RankInfo("k", 60, 4, 30.0))["rank_gap"]
    assert near == Signal(TRUE, "#4 on Maps for 'k', just outside the map pack")
    assert sig(rank=RankInfo("k", 60, 20, 30.0))["rank_gap"].state == TRUE
    far = sig(rank=RankInfo("k", 60, 21, 30.0))["rank_gap"]
    assert far == Signal(FALSE, "#21 on Maps for 'k'")
    missing = sig(rank=RankInfo("k", 60, None, 30.0))["rank_gap"]
    assert missing == Signal(UNKNOWN, "not in the top 60 on Maps for 'k'")
    assert sig(rank=RankInfo("k", 0, None, None))["rank_gap"].state == UNKNOWN


def test_competitor_gap_cases():
    assert sig(place(review_count=10), rank=RankInfo("k", 20, 5, 30.0))["competitor_gap"].state == TRUE
    assert sig(place(review_count=11), rank=RankInfo("k", 20, 5, 30.0))["competitor_gap"].state == FALSE
    assert sig(rank=RankInfo("k", 0, None, None))["competitor_gap"].state == UNKNOWN


def test_competitor_gap_unknown_when_place_not_in_money_results():
    result = sig(place(review_count=1), rank=RankInfo("k", 60, None, 300.0))["competitor_gap"]
    assert result == Signal(UNKNOWN, "not in the results for 'k'")


def test_competitor_gap_unknown_without_independent_rivals():
    result = sig(rank=RankInfo("k", 20, 5, None))["competitor_gap"]
    assert result == Signal(UNKNOWN, "no independent competitors to compare")


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


def named(place_id, name, reviews):
    return place(place_id=place_id, name=name, review_count=reviews)


def test_brand_key():
    assert brand_key("Acme Counseling - Austin") == "acme counseling"
    assert brand_key("Acme Counseling | Round Rock") == "acme counseling"
    assert brand_key("Acme Counseling \u2013 North") == "acme counseling"
    assert brand_key("  ACME Counseling (Downtown) ") == "acme counseling"
    assert brand_key("Plain Name") == "plain name"


def test_chain_keys_need_more_than_one_listing():
    ps = [named("a", "Acme Counseling - Austin", 5), named("b", "Acme Counseling (Cedar Park)", 5),
          named("c", "Solo Therapy", 5)]
    assert chain_keys(ps) == frozenset({"acme counseling"})


def test_chain_rivals_are_excluded_from_the_top3_average():
    ps = [named("a1", "Acme Counseling - Austin", 1000), named("a2", "Acme Counseling - Cedar Park", 900),
          named("me", "Me Therapy", 10), named("x", "Indie One", 30), named("y", "Indie Two", 60),
          named("z", "Indie Three", 90), named("w", "Indie Four", 600)]
    keys = chain_keys(ps)
    r = rank_info("me", ps, "k", keys)
    assert r.position == 3
    assert r.top3_avg_reviews == 60.0  # x, y, z only
    assert rank_info("me", ps, "k").top3_avg_reviews == (1000 + 900 + 30) / 3  # default: no chains


def test_all_rivals_chains_gives_no_average():
    ps = [named("a1", "Acme Counseling - Austin", 1000), named("a2", "Acme Counseling - Cedar Park", 900),
          named("me", "Me Therapy", 10)]
    r = rank_info("me", ps, "k", chain_keys(ps))
    assert r.top3_avg_reviews is None
    assert sig(place(review_count=10), rank=r)["competitor_gap"].state == UNKNOWN


def test_inactive_listing_window():
    def at(days):
        dates = (TODAY - dt.timedelta(days=days),)
        return negatives(place(review_count=1, review_dates=dates), TODAY)["inactive_listing"]
    assert at(731).state == TRUE and "over 2 years ago" in at(731).evidence
    assert at(730).state == FALSE
    assert negatives(place(review_dates=()), TODAY)["inactive_listing"].state == UNKNOWN
    newest = negatives(place(review_count=2, review_dates=(dt.date(2020, 1, 1), TODAY)), TODAY)
    assert newest["inactive_listing"].state == FALSE  # uses the newest date, not the oldest


def test_inactive_listing_needs_every_review_visible():
    old = (dt.date(2020, 1, 1), dt.date(2021, 1, 1))
    sampled = negatives(place(review_count=9, review_dates=old), TODAY)["inactive_listing"]
    assert sampled == Signal(UNKNOWN, "only 2 of 9 reviews visible")
    all_visible = negatives(place(review_count=2, review_dates=old), TODAY)["inactive_listing"]
    assert all_visible.state == TRUE
