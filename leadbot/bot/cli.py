"""python -m leadbot.bot collect|push (spec 2, 3.9)."""

import argparse
import datetime as dt
import json
import os
from collections import Counter

from leadbot.bot import candidates as cand
from leadbot.bot import domain_age, pagespeed, scrape, site_check
from leadbot.bot.collect import Deps, collect_city
from leadbot.bot.dnc import (DNC_MAX_AGE_DAYS, DncList, area_code, is_listed, load_dnc,
                             missing_area_codes)
from leadbot.bot.score import load_scoring
from leadbot.bot.seeds import city_plan, load_seeds
from leadbot.bot.seen import SeenStore
from leadbot.env import ROOT, client_from_env
from leadbot.ghl.client import GHLAuthError
from leadbot.ghl.mapping import PayloadError, validate_payload
from leadbot.ghl.push import load_context, push_lead
from leadbot.ghl.schema import load_schema, slug

RUNS_DIR = os.path.join(ROOT, "state", "runs")
SEEN_PATH = os.path.join(ROOT, "state", "seen.sqlite")

__all__ = ["main", "run_id_for", "DncList"]


def run_id_for(today, niche, city):
    return f"{today.isoformat()}-{slug(niche)}-{slug(city)}"


def _real_deps(run_dir, depth, client):
    exe = os.environ.get("GMAPS_SCRAPER_EXE")
    if not exe or not os.path.exists(exe):
        raise SystemExit("Set GMAPS_SCRAPER_EXE in .env to the patched scraper exe "
                         "(see docs/scraper-build.md)")
    return Deps(
        scrape=lambda queries, name: scrape.run_scraper(queries, run_dir, name, exe=exe, depth=depth),
        site_check=site_check.check_site,
        pagespeed=pagespeed.mobile_score,
        domain_age=domain_age.registration_date,
        find_contact=client.find_duplicate)


def cmd_collect(args):
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    seeds = load_seeds()
    plan = city_plan(seeds, args.niche, args.city)
    dnc = None
    if not args.no_dnc:
        dnc = load_dnc()
        missing = missing_area_codes(dnc, plan.area_codes)
        if missing:
            raise SystemExit(f"No DNC file for area codes {', '.join(missing)} in state/dnc/. "
                             "Add them, or use --no-dnc for a dry run.")
        # File age is measured against the real date, not --today (fail closed on re-runs).
        age = (dt.date.today() - dnc.oldest_file_date).days if dnc.oldest_file_date else 0
        if age > DNC_MAX_AGE_DAYS:
            raise SystemExit(f"DNC files in state/dnc/ are {age} days old (oldest "
                             f"{dnc.oldest_file_date.isoformat()}); the limit is {DNC_MAX_AGE_DAYS}. "
                             "Download fresh FTC files, or use --no-dnc for a dry run.")
    client = client_from_env()  # also loads .env, which holds GMAPS_SCRAPER_EXE
    run_id = run_id_for(today, plan.niche, plan.city)
    run_dir = os.path.join(RUNS_DIR, run_id)
    seen = SeenStore(SEEN_PATH)
    try:
        report = collect_city(plan, seeds, load_scoring(), load_schema(),
                              _real_deps(run_dir, seeds.depth, client), run_id=run_id,
                              run_dir=run_dir, today=today, dnc=dnc, seen=seen)
    finally:
        seen.close()
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 2


HANDLED_OUTCOMES = ("created", "updated", "duplicate", "existing")
# Drops that say nothing about the place itself: it is collected again next night.
UNSEEN_DROPS = ("over_nightly_cap", "ghl_lookup_failed")


def _mark_seen(run_dir, candidates, outcomes, today):
    """Mark only leads the bot has handled: pushed (or deduped) ones, and Cold leads
    without an email, which are never pushed by design."""
    store = SeenStore(SEEN_PATH)
    try:
        for c in candidates.values():
            outcome = outcomes.get(c["bot_run_id"])
            if outcome in HANDLED_OUTCOMES:
                store.mark(c["place_id"], today, outcome)
            elif outcome is None and not cand.needs_text(c):
                store.mark(c["place_id"], today, "cold_no_email")
        for d in cand.read_jsonl(os.path.join(run_dir, "dropped.jsonl")):
            if d.get("reason") not in UNSEEN_DROPS and d.get("place_id"):
                store.mark(d["place_id"], today, d["reason"])
    finally:
        store.close()


def cmd_push(args):
    run_dir = os.path.dirname(os.path.abspath(args.file))
    with open(os.path.join(run_dir, "report.json"), encoding="utf-8") as fh:
        report = json.load(fh)
    candidates = {c["bot_run_id"]: c for c in cand.read_jsonl(os.path.join(run_dir, "candidates.jsonl"))}
    live = not args.dry_run
    if live:
        if not load_seeds().live:
            raise SystemExit("config/seeds.toml has live = false; use --dry-run")
        if not report.get("dnc_checked"):
            raise SystemExit("this run was collected with --no-dnc; it can only be a --dry-run")
    schema = load_schema()
    client = ctx = dnc = dnc_areas = None
    if live:
        dnc = load_dnc()  # defence in depth: re-check every lead against the DNC files
        dnc_areas = dnc.area_codes
        client = client_from_env()
        ctx = load_context(client, schema)

    results = []
    auth_error = None  # set once a GHL auth error stops the batch (spec 5)
    for q in cand.read_jsonl(args.file):
        if auth_error:
            results.append({"bot_run_id": q.get("bot_run_id"), "outcome": "aborted",
                            "message": auth_error})
            continue
        c = candidates.get(q.get("bot_run_id"))
        if c is None:
            results.append({"bot_run_id": q.get("bot_run_id"), "outcome": "rejected",
                            "message": "not in candidates.jsonl"})
            continue
        try:
            cand.check_qualified(q, c)
        except cand.QualifiedError as exc:
            results.append({"bot_run_id": c["bot_run_id"], "outcome": "rejected", "message": str(exc)})
            continue
        payload = cand.to_payload(q)
        if live:
            blocked = ("dnc" if is_listed(dnc, c["phone"]) else
                       "dnc_area_not_loaded" if area_code(c["phone"]) not in dnc_areas else None)
            if blocked:
                results.append({"bot_run_id": c["bot_run_id"], "outcome": "rejected",
                                "message": blocked})
                continue
            try:
                r = push_lead(client, schema, ctx, payload)
            except GHLAuthError as exc:
                auth_error = f"GHL auth error: {exc}"
                results.append({"bot_run_id": c["bot_run_id"], "outcome": "aborted",
                                "message": auth_error})
                continue
            results.append({"bot_run_id": c["bot_run_id"], "outcome": r.outcome, "message": r.message,
                            "contact_id": r.contact_id, "opportunity_id": r.opportunity_id})
            continue
        try:
            validate_payload(payload, schema)
            results.append({"bot_run_id": c["bot_run_id"], "outcome": "would_push", "message": ""})
        except PayloadError as exc:
            results.append({"bot_run_id": c["bot_run_id"], "outcome": "rejected", "message": str(exc)})

    if live:
        _mark_seen(run_dir, candidates, {r["bot_run_id"]: r["outcome"] for r in results},
                   dt.date.today())
    counts = dict(Counter(r["outcome"] for r in results))
    name = "push_report.json" if live else "dry_run_report.json"
    with open(os.path.join(run_dir, name), "w", encoding="utf-8") as fh:
        json.dump({"live": live, "counts": counts, "results": results}, fh, indent=2)
    print(json.dumps({"live": live, "counts": counts}, indent=2))
    if auth_error:
        print(f"Run stopped on a GHL auth error; remaining leads marked aborted. ({auth_error})")
        return 3
    return 1 if any(r["outcome"] in ("rejected", "failed") for r in results) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m leadbot.bot")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="scrape, filter, enrich and score one city")
    c.add_argument("--niche", required=True)
    c.add_argument("--city", required=True)
    c.add_argument("--no-dnc", action="store_true", help="skip the DNC scrub (dry runs only)")
    c.add_argument("--today", help="YYYY-MM-DD, for re-runs")
    p = sub.add_parser("push", help="validate qualified.jsonl and push it to GHL")
    p.add_argument("file")
    p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return cmd_collect(args) if args.command == "collect" else cmd_push(args)
