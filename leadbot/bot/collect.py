"""One city's collect step: scrape, filter, enrich, score (spec 2, 3.3-3.7)."""

import json
import os
from collections import Counter
from dataclasses import dataclass

from leadbot.bot import candidates as cand
from leadbot.bot.dnc import area_code, is_listed
from leadbot.bot.domain_age import registrable_domain
from leadbot.bot.score import lead_score, lead_tier, primary_flag
from leadbot.bot.scrape import ScrapeError
from leadbot.bot.signals import Enrichment, detect, gbp_status, rank_info, true_flags
from leadbot.ghl.client import GHLAuthError


@dataclass
class Deps:
    scrape: object        # (queries: list, name: str) -> list[Place]
    site_check: object    # url -> SiteCheck
    pagespeed: object     # url -> int | None
    domain_age: object    # url -> date | None
    find_contact: object  # (phone, email) -> dict | None


def _merge(*lists):
    seen, out = set(), []
    for places in lists:
        for place in places:
            if place.place_id not in seen:
                seen.add(place.place_id)
                out.append(place)
    return out


def _drop_reason(place, deps, *, dnc, dnc_areas, seen, seen_days, today):
    if place.closed:
        return "closed"
    if not place.phone:
        return "no_phone"
    if dnc is not None and is_listed(dnc, place.phone):
        return "dnc"
    if dnc is not None and area_code(place.phone) not in dnc_areas:
        return "dnc_area_not_loaded"  # fail closed: no list loaded for this number
    if seen is not None and seen.recently_seen(place.place_id, today, seen_days):
        return "seen_recently"
    try:
        found = (deps.find_contact(place.phone, None)
                 or (place.email and deps.find_contact(None, place.email)))
    except GHLAuthError:
        raise  # a token problem fails every lookup: stop the run
    except Exception:  # noqa: BLE001 - one failed lookup drops only this place
        return "ghl_lookup_failed"
    if found:
        return "ghl_contact"
    return None


def _checked(check, url):
    """One enrichment piece; any error means "not checked" (spec 5)."""
    try:
        return check(url)
    except Exception:  # noqa: BLE001
        return None


def _enrich(place, money, plan, deps, today):
    site = _checked(deps.site_check, place.website) if place.website else None
    registered = _checked(deps.domain_age, place.website) if place.website else None
    enrich = Enrichment(site, None, registered)
    rank = rank_info(place.place_id, money, plan.money_keyword)
    signals = detect(place, enrich, rank, today)
    if site is not None and site.reachable and true_flags(signals):
        enrich = Enrichment(site, _checked(deps.pagespeed, place.website), registered)
        signals = detect(place, enrich, rank, today)
    return enrich, rank, signals


def _email(place, enrich):
    """The site's mailto address only when it is on the website's own domain (a web
    designer's or platform's address would merge unrelated leads); else the Maps email."""
    site_email = enrich.site.email if enrich.site else None
    if site_email:
        own = registrable_domain(place.website)
        if not own or registrable_domain("https://" + site_email.rsplit("@", 1)[-1]) != own:
            site_email = None
    email = site_email or place.email
    return email.lower() if email else None


def _drop_row(place, reason):
    return {"place_id": place.place_id, "business_name": place.name, "reason": reason}


def _write_report(run_dir, report):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)


def collect_city(plan, seeds, scoring, schema, deps, *, run_id, run_dir, today, dnc=None, seen=None):
    report = {"run_id": run_id, "niche": plan.niche, "city": plan.city,
              "dnc_checked": dnc is not None, "scraped": 0, "bad_rows": 0, "dropped": {}, "tiers": {},
              "needs_text": 0, "status": "ok"}
    try:
        money = deps.scrape([plan.money_query], "money")
        areas = deps.scrape(list(plan.queries), "areas") if plan.queries else []
    except ScrapeError as exc:
        report.update(status="blocked_or_failed", error=str(exc))
        _write_report(run_dir, report)
        return report
    places = _merge(money, areas)
    report["scraped"] = len(places)
    report["bad_rows"] = getattr(money, "bad_rows", 0) + getattr(areas, "bad_rows", 0)
    if not places:
        report["status"] = "blocked_or_failed"
        _write_report(run_dir, report)
        return report

    dnc_areas = dnc.area_codes if dnc is not None else None  # once per city
    kept, dropped = [], []
    for place in places:
        reason = _drop_reason(place, deps, dnc=dnc, dnc_areas=dnc_areas, seen=seen,
                              seen_days=seeds.seen_days, today=today)
        if reason:
            dropped.append(_drop_row(place, reason))
        else:
            kept.append(place)
    # One phone, one lead: a later listing would overwrite the first one's GHL contact.
    phones, unique = set(), []
    for place in kept:
        if place.phone in phones:
            dropped.append(_drop_row(place, "shared_phone"))
        else:
            phones.add(place.phone)
            unique.append(place)
    kept = unique
    dropped += [_drop_row(p, "over_nightly_cap") for p in kept[seeds.max_enrich:]]
    kept = kept[:seeds.max_enrich]

    enriched = [(place, *_enrich(place, money, plan, deps, today)) for place in kept]
    emails = [_email(place, enrich) for place, enrich, _, _ in enriched]
    shared = {e for e, count in Counter(e for e in emails if e).items() if count > 1}
    records = []
    for n, ((place, enrich, rank, signals), email) in enumerate(zip(enriched, emails), 1):
        flags = true_flags(signals)
        score = lead_score(flags, plan.niche, scoring)
        flag = primary_flag(flags, scoring)
        records.append(cand.build_candidate(
            place, signals, score=score, tier=lead_tier(score, scoring),
            primary=schema.signals[flag] if flag else None, plan=plan,
            bot_run_id=f"{run_id}-{n:04d}",
            email=None if email in shared else email,
            gbp_status=gbp_status(place), rank=rank))

    cand.write_jsonl(os.path.join(run_dir, "candidates.jsonl"), records)
    cand.write_jsonl(os.path.join(run_dir, "dropped.jsonl"), dropped)
    report["dropped"] = dict(Counter(d["reason"] for d in dropped))
    report["tiers"] = dict(Counter(r["lead_tier"] for r in records))
    report["needs_text"] = sum(cand.needs_text(r) for r in records)
    _write_report(run_dir, report)
    return report
