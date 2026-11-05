import dataclasses
import urllib.error
import datetime as dt
import json
import os

import pytest

from leadbot.bot.collect import Deps, collect_city
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
