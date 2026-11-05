import dataclasses
import urllib.error
import datetime as dt
import json
import os

import pytest

from leadbot.bot.collect import Deps, collect_city
from leadbot.bot.candidates import to_payload
from leadbot.bot.dnc import DncList
from leadbot.bot.scrape import ScrapeError, read_results
from leadbot.bot.score import load_scoring
from leadbot.bot.seeds import city_plan, load_seeds
from leadbot.bot.seen import SeenStore
from leadbot.bot.site_check import SiteCheck
from leadbot.ghl.client import GHLAuthError, GHLError
from leadbot.ghl.schema import load_schema

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "maps_rows.jsonl")
TODAY = dt.date(2026, 11, 6)
RUN_ID = "2026-11-06-counselors-therapists-austin-tx"
PLACES = {p.place_id: p for p in read_results(FIXTURE)}
MONEY = [PLACES["fx-lakeview"], PLACES["fx-sunrise"], PLACES["fx-cedar"]]


def sites(url):
    if "lakeview" in url:
        return SiteCheck(True, url, True, True, True, None)
    return SiteCheck(True, url, False, False, False, None)


def make_deps(contacts=()):
    return Deps(scrape=lambda queries, name: MONEY if name == "money" else list(PLACES.values()),
                site_check=sites, pagespeed=lambda url: 35,
                domain_age=lambda url: dt.date(2015, 1, 1),
                find_contact=lambda phone, email: {"id": "c1"} if phone in contacts else None)


def run(tmp_path, seeds=None, deps=None, **kw):
    seeds = seeds or load_seeds()
    plan = city_plan(seeds, "Counselors / therapists", "Austin, TX")
    report = collect_city(plan, seeds, load_scoring(), load_schema(), deps or make_deps(),
                          run_id=RUN_ID, run_dir=str(tmp_path), today=TODAY, **kw)

    def rows(name):
        path = tmp_path / name
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] \
            if path.exists() else []
    return (report, {c["place_id"]: c for c in rows("candidates.jsonl")},
            {d["place_id"]: d["reason"] for d in rows("dropped.jsonl")})


def test_drops_closed_and_phoneless(tmp_path):
    _, cands, dropped = run(tmp_path)
    assert dropped == {"fx-oakhill": "closed", "fx-quietmind": "no_phone"}
    assert set(cands) == {"fx-lakeview", "fx-brightpath", "fx-cedar", "fx-sunrise"}


def test_unclaimed_new_unranked_listing_is_warm(tmp_path):
    _, cands, _ = run(tmp_path)
    c = cands["fx-brightpath"]
    # Not in the money results: rank_gap and competitor_gap are UNKNOWN now (were TRUE).
    assert c["signals"] == ["gbp_unverified_or_suspended", "broken_website", "new_business"]
    assert (c["lead_score"], c["lead_tier"], c["primary_signal"]) == (60, "Warm", "GBP Suspended")
    assert c["negatives"] == []
    assert c["notes"] == {"rank_gap": "not in the top 3 on Maps for 'therapist austin'",
                          "competitor_gap": "not in the results for 'therapist austin'"}
    assert "rank_gap" not in c["evidence"] and "competitor_gap" not in c["evidence"]
    assert c["gbp_status"] == "Unverified"
    assert c["current_rank"] is None
    assert c["money_keyword"] == "therapist austin"
    assert c["unknown"] == ["google_ads_spend", "meta_ads_active", "rank_gap", "technical_debt",
                            "competitor_gap"]
    assert c["bot_run_id"].startswith(RUN_ID + "-")
    assert c["audit_summary"] == "" and c["call_opener"] == ""


def test_established_listing_is_cold_without_primary(tmp_path):
    _, cands, _ = run(tmp_path)
    c = cands["fx-lakeview"]
    assert (c["signals"], c["lead_score"], c["lead_tier"], c["primary_signal"]) == \
        ([], 5, "Cold", None)
    assert c["current_rank"] == 1


def test_report_counts(tmp_path):
    report, _, _ = run(tmp_path)
    assert report["status"] == "ok" and report["scraped"] == 6
    assert report["tiers"] == {"Cold": 3, "Warm": 1}
    assert report["needs_text"] == 2  # Bright Path (Warm) and Cedar (Cold with an email)
    assert report["dnc_checked"] is False
    assert report["dropped"] == {"closed": 1, "no_phone": 1}
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == report


def test_dnc_seen_and_ghl_contact_drops(tmp_path):
    seen = SeenStore(":memory:")
    seen.mark("fx-lakeview", TODAY - dt.timedelta(days=3), "Cold")
    report, cands, dropped = run(tmp_path, deps=make_deps(contacts=("+15125550136",)),
                                 dnc=DncList({"5125550135"}), seen=seen)
    seen.close()
    assert dropped["fx-cedar"] == "dnc"
    assert dropped["fx-lakeview"] == "seen_recently"
    assert dropped["fx-sunrise"] == "ghl_contact"
    assert set(cands) == {"fx-brightpath"}
    assert report["dnc_checked"] is True


def test_old_seen_entry_is_collected_again(tmp_path):
    seen = SeenStore(":memory:")
    seen.mark("fx-lakeview", TODAY - dt.timedelta(days=31), "Cold")
    _, cands, _ = run(tmp_path, seen=seen)
    seen.close()
    assert "fx-lakeview" in cands


def test_nightly_cap_keeps_maps_order(tmp_path):
    seeds = dataclasses.replace(load_seeds(), max_enrich=2)
    _, cands, dropped = run(tmp_path, seeds=seeds)
    assert set(cands) == {"fx-lakeview", "fx-sunrise"}
    assert dropped["fx-cedar"] == "over_nightly_cap"
    assert dropped["fx-brightpath"] == "over_nightly_cap"


def test_pagespeed_only_for_places_with_another_signal(tmp_path):
    calls = []
    deps = make_deps()
    deps.pagespeed = lambda url: calls.append(url) or 90
    run(tmp_path, deps=deps)
    assert "https://brightpath.example.com/" in calls
    assert "https://lakeview.example.com/" not in calls


def test_no_results_is_blocked(tmp_path):
    deps = make_deps()
    deps.scrape = lambda queries, name: []
    report, cands, _ = run(tmp_path, deps=deps)
    assert report["status"] == "blocked_or_failed"
    assert cands == {}


def test_scraper_failure_is_blocked(tmp_path):
    def boom(queries, name):
        raise ScrapeError("scraper exited 1")
    deps = make_deps()
    deps.scrape = boom
    report, cands, _ = run(tmp_path, deps=deps)
    assert report["status"] == "blocked_or_failed"
    assert "exited 1" in report["error"]
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["status"] == \
        "blocked_or_failed"


class CountingDnc(DncList):
    calls = 0

    @property
    def area_codes(self):
        CountingDnc.calls += 1
        return {n[:3] for n in self.numbers}


def test_phone_in_unloaded_area_code_is_dropped(tmp_path):
    out_of_area = dataclasses.replace(PLACES["fx-brightpath"], place_id="fx-dallas",
                                      phone="+12145550140")
    deps = make_deps()
    everything = list(PLACES.values()) + [out_of_area]
    deps.scrape = lambda queries, name: MONEY if name == "money" else everything
    CountingDnc.calls = 0
    report, cands, dropped = run(tmp_path, deps=deps, dnc=CountingDnc({"5125550199"}))
    assert dropped["fx-dallas"] == "dnc_area_not_loaded"
    assert "fx-brightpath" in cands
    assert report["dropped"]["dnc_area_not_loaded"] == 1
    assert CountingDnc.calls == 1  # loaded area codes computed once per city


def test_dnc_listing_wins_over_area_check(tmp_path):
    _, _, dropped = run(tmp_path, dnc=DncList({"5125550135"}))
    assert dropped["fx-cedar"] == "dnc"


@pytest.mark.parametrize("error", [GHLError(500, "server"), urllib.error.URLError("down"),
                                   TimeoutError("slow"), ValueError("bad json")])
def test_failed_ghl_lookup_drops_only_that_place(tmp_path, error):
    def find_contact(phone, email):
        if phone == "+15125550136":
            raise error
        return None
    deps = make_deps()
    deps.find_contact = find_contact
    report, cands, dropped = run(tmp_path, deps=deps)
    assert report["status"] == "ok"
    assert dropped["fx-sunrise"] == "ghl_lookup_failed"
    assert set(cands) == {"fx-lakeview", "fx-brightpath", "fx-cedar"}


def test_ghl_auth_error_stops_the_city(tmp_path):
    def find_contact(phone, email):
        raise GHLAuthError(401, "token revoked")
    deps = make_deps()
    deps.find_contact = find_contact
    with pytest.raises(GHLAuthError):
        run(tmp_path, deps=deps)


def test_enrichment_errors_become_not_checked(tmp_path):
    def site_check(url):
        if "brightpath" in url:
            raise RuntimeError("parser crashed")
        return sites(url)

    def domain_age(url):
        raise ValueError("odd RDAP date")

    def pagespeed(url):
        raise KeyError("lighthouseResult")
    deps = make_deps()
    deps.site_check, deps.domain_age, deps.pagespeed = site_check, domain_age, pagespeed
    report, cands, _ = run(tmp_path, deps=deps)
    assert report["status"] == "ok"
    bright = cands["fx-brightpath"]
    assert "broken_website" in bright["unknown"]
    assert "broken_website" not in bright["signals"]
    assert set(cands) == {"fx-lakeview", "fx-brightpath", "fx-cedar", "fx-sunrise"}


def test_mid_file_bad_json_is_blocked(tmp_path):
    with open(FIXTURE, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    bad = tmp_path / "bad.raw.jsonl"
    bad.write_text(lines[0] + "\n{bad json\n" + lines[1] + "\n", encoding="utf-8")
    deps = make_deps()
    deps.scrape = lambda queries, name: read_results(str(bad))
    run_dir = tmp_path / "run"
    report, cands, _ = run(run_dir, deps=deps)
    assert report["status"] == "blocked_or_failed"
    assert json.loads((run_dir / "report.json").read_text(encoding="utf-8"))["status"] == \
        "blocked_or_failed"


def test_report_counts_bad_rows(tmp_path):
    with open(FIXTURE, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    rows = tmp_path / "areas.raw.jsonl"
    bad_row = '{"place_id": "fx-bad", "title": "Bad (fictional)", "review_count": "lots"}'
    rows.write_text("\n".join(lines + [bad_row, lines[0]]) + "\n", encoding="utf-8")
    deps = make_deps()
    deps.scrape = lambda queries, name: MONEY if name == "money" else read_results(str(rows))
    run_dir = tmp_path / "run"
    report, _, _ = run(run_dir, deps=deps)
    assert report["status"] == "ok" and report["bad_rows"] == 1
    assert run(tmp_path / "plain")[0]["bad_rows"] == 0


def with_extra(*extra):
    deps = make_deps()
    everything = list(PLACES.values()) + list(extra)
    deps.scrape = lambda queries, name: MONEY if name == "money" else everything
    return deps


def test_second_place_with_same_phone_is_dropped(tmp_path):
    twin = dataclasses.replace(PLACES["fx-brightpath"], place_id="fx-twin",
                               phone=PLACES["fx-lakeview"].phone)
    report, cands, dropped = run(tmp_path, deps=with_extra(twin))
    assert dropped["fx-twin"] == "shared_phone"
    assert "fx-lakeview" in cands and "fx-twin" not in cands
    assert report["dropped"]["shared_phone"] == 1


def test_shared_phone_is_dropped_before_the_cap(tmp_path):
    twin = dataclasses.replace(PLACES["fx-brightpath"], place_id="fx-twin",
                               phone=PLACES["fx-lakeview"].phone)
    deps = make_deps()
    deps.scrape = lambda queries, name: [PLACES["fx-lakeview"], twin, PLACES["fx-sunrise"]]
    seeds = dataclasses.replace(load_seeds(), max_enrich=2)
    _, cands, dropped = run(tmp_path, seeds=seeds, deps=deps)
    assert set(cands) == {"fx-lakeview", "fx-sunrise"}
    assert dropped == {"fx-twin": "shared_phone"}


def test_email_found_on_two_sites_is_cleared_on_both(tmp_path):
    deps = make_deps()
    deps.site_check = lambda url: dataclasses.replace(sites(url), email="info@shared.example.com")
    report, cands, _ = run(tmp_path, deps=deps)
    assert cands["fx-lakeview"]["email"] is None
    assert cands["fx-brightpath"]["email"] is None
    assert cands["fx-sunrise"]["email"] is None
    assert cands["fx-cedar"]["email"] == "hello@cedar.example.com"  # Maps email, not shared
    assert report["needs_text"] == 2  # the cleared email does not make Lakeview need text


def test_site_email_on_another_domain_is_dropped(tmp_path):
    deps = make_deps()

    def site_check(url):
        found = {"lakeview": "studio@webdesign.example.net",
                 "brightpath": "hello@brightpath.example.com"}
        key = next((k for k in found if k in url), None)
        return dataclasses.replace(sites(url), email=found.get(key))
    deps.site_check = site_check
    _, cands, _ = run(tmp_path, deps=deps)
    assert cands["fx-lakeview"]["email"] is None
    assert cands["fx-brightpath"]["email"] == "hello@brightpath.example.com"


def test_foreign_site_email_falls_back_to_maps_email(tmp_path):
    cedar_site = dataclasses.replace(PLACES["fx-cedar"], place_id="fx-cedar2",
                                     phone="+15125550138", website="https://cedar2.example.org/",
                                     email="owner@cedar2.example.org")
    deps = with_extra(cedar_site)
    deps.site_check = lambda url: dataclasses.replace(sites(url), email="studio@webdesign.example.net")
    _, cands, _ = run(tmp_path, deps=deps)
    assert cands["fx-cedar2"]["email"] == "owner@cedar2.example.org"


def test_inactive_listing_is_a_negative_that_lowers_the_score(tmp_path):
    def twin(place_id, name, phone, newest):
        return dataclasses.replace(PLACES["fx-brightpath"], place_id=place_id, name=name, phone=phone,
                                   website=None, owner_claimed=False, review_count=1,
                                   review_dates=(newest,), email=None)
    active = twin("fx-active", "Active Practice (fictional)", "+15125550150", TODAY - dt.timedelta(days=120))
    stale = twin("fx-stale", "Stale Practice (fictional)", "+15125550151", TODAY - dt.timedelta(days=731))
    _, cands, _ = run(tmp_path, deps=with_extra(active, stale))
    assert cands["fx-active"]["negatives"] == []
    assert cands["fx-stale"]["negatives"] == ["inactive_listing"]
    assert cands["fx-stale"]["evidence"]["inactive_listing"] == \
        f"newest visible review {(TODAY - dt.timedelta(days=731)).isoformat()}, over 2 years ago"
    assert cands["fx-stale"]["lead_score"] == cands["fx-active"]["lead_score"] - 30
    assert cands["fx-stale"]["signals"] == cands["fx-active"]["signals"]  # negatives stay out of flags
    assert cands["fx-stale"]["primary_signal"] == cands["fx-active"]["primary_signal"] == "GBP Suspended"
    assert "inactive_listing" not in cands["fx-active"]["evidence"]


def test_payload_never_carries_negatives_or_notes(tmp_path):
    _, cands, _ = run(tmp_path)
    for record in cands.values():
        payload = to_payload(record)
        assert "negatives" not in payload and "notes" not in payload


def test_chain_listings_are_not_rivals(tmp_path):
    chain_a = dataclasses.replace(PLACES["fx-sunrise"], place_id="fx-chain-a", phone="+15125550160",
                                  name="Mega Therapy - Austin", review_count=2000)
    chain_b = dataclasses.replace(chain_a, place_id="fx-chain-b", phone="+15125550161",
                                  name="Mega Therapy - Round Rock")
    deps = make_deps()
    money = [chain_a, chain_b, PLACES["fx-lakeview"], PLACES["fx-cedar"], PLACES["fx-brightpath"]]
    everything = list(PLACES.values()) + [chain_a, chain_b]
    deps.scrape = lambda queries, name: money if name == "money" else everything
    _, cands, _ = run(tmp_path, deps=deps)
    # Bright Path is 5th; rivals are Lakeview (412) and Cedar (9), not the 2000-review chain.
    assert cands["fx-brightpath"]["evidence"]["competitor_gap"].startswith("top 3 for 'therapist austin' average 210 ")


def test_sampled_old_reviews_do_not_make_a_listing_inactive(tmp_path):
    _, cands, _ = run(tmp_path)
    cedar = cands["fx-cedar"]  # 9 reviews, only 2 visible, both from 2024
    assert cedar["negatives"] == [] and "inactive_listing" not in cedar["evidence"]
    assert cedar["lead_score"] == 25  # broken 15 + rank 0 + competitor 5 + retention 5
