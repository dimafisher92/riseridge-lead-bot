import dataclasses
import datetime as dt
import json
import os

from leadbot.bot.collect import Deps, collect_city
from leadbot.bot.dnc import DncList
from leadbot.bot.scrape import ScrapeError, read_results
from leadbot.bot.score import load_scoring
from leadbot.bot.seeds import city_plan, load_seeds
from leadbot.bot.seen import SeenStore
from leadbot.bot.site_check import SiteCheck
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


def test_unclaimed_new_unranked_listing_is_hot(tmp_path):
    _, cands, _ = run(tmp_path)
    c = cands["fx-brightpath"]
    assert c["signals"] == ["gbp_unverified_or_suspended", "broken_website", "new_business",
                            "rank_gap", "competitor_gap"]
    assert (c["lead_score"], c["lead_tier"], c["primary_signal"]) == (75, "Hot", "GBP Suspended")
    assert c["gbp_status"] == "Unverified"
    assert c["current_rank"] is None
    assert c["money_keyword"] == "therapist austin"
    assert c["unknown"] == ["google_ads_spend", "meta_ads_active", "technical_debt"]
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
    assert report["tiers"] == {"Cold": 3, "Hot": 1}
    assert report["needs_text"] == 2  # Bright Path (Hot) and Cedar (Cold with an email)
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
