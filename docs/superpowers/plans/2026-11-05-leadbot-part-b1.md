# Lead Bot Part B1 (collect, score, qualify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nightly pipeline that scrapes Google Maps for counselors/therapists in Austin, Dallas and Houston, detects the SOP signals with evidence, scores and tiers each place, lets a Claude scheduled task write the rep-facing text, and pushes the result into GoHighLevel through part A's `push_lead()`.

**Architecture:** `python -m leadbot.bot collect` (scrape → filter → enrich → signals → score → `candidates.jsonl`), then the Claude task fills `audit_summary` / `call_opener` into `qualified.jsonl` following `.claude/skills/leadbot-nightly/SKILL.md`, then `python -m leadbot.bot push` re-validates every line against its candidate and calls `push_lead()` (or only validates with `--dry-run`). Every network edge (scraper exe, HTTP, GHL) is injected, so all unit tests run offline.

**Tech Stack:** Python 3.14 stdlib only (tomllib, sqlite3, subprocess, urllib, json, re), pytest 9.1.1, gosom/google-maps-scraper v1.18.1 (patched Windows build), part A's `leadbot.ghl` package.

**Spec:** `docs/superpowers/specs/2026-11-05-leadbot-part-b1-design.md`.

## Global Constraints

- Python stdlib only; tests need nothing but pytest. Tests never touch the network or the real scraper.
- Part A code (`leadbot/ghl/`, `scripts/`, `config/ghl_schema.toml`) is not changed.
- HTTP requests send part A's `USER_AGENT` (`riseridge-leadbot/0.1`), imported from `leadbot.ghl.client`.
- Never print, log or open `.env` values. `.env` gets one new key, `GMAPS_SCRAPER_EXE`.
- Test data is fictional: phones `+1 512-555-01NN`, websites and emails under `example.com`, names suffixed "(fictional)".
- Scraper flags: `-json -depth <seeds depth> -c 1 -lang en -exit-on-inactivity 3m`; one worker always.
- Signals are `TRUE` / `FALSE` / `UNKNOWN`; only `TRUE` scores. `google_ads_spend`, `meta_ads_active`, `technical_debt` are always `UNKNOWN` in B1.
- Weights (SOP Step 4): gbp 25, google ads 20 (+10 over $3,000/month), meta ads 10, broken site 15, new business 15, rank gap 10, technical debt 5, competitor gap 5, retention niche +5; cap 100. Tiers: Hot ≥ 70, Warm ≥ 45, else Cold.
- Tie order for the primary signal: `gbp_unverified_or_suspended, google_ads_spend, meta_ads_active, broken_website, new_business, rank_gap, technical_debt, competitor_gap`.
- `run_id` = `<YYYY-MM-DD>-<slug(niche)>-<slug(city)>`; `bot_run_id` = `<run_id>-<NNNN>`.
- A live push requires `live = true` in `config/seeds.toml` AND a run collected with the DNC check. `--no-dnc` runs can only be dry-run.
- The seen store (`state/seen.sqlite`) is written only by a live push, never by `collect` or a dry run, and never for `over_nightly_cap` drops.
- Any write to the live GHL sub-account, any scheduled-task creation and any `live = true` switch needs the user's yes in chat first.

## File Structure

```
config/seeds.toml                 niches, cities, areas, area codes, depth, caps, live flag
config/scoring.toml               SOP Step 4 weights and tier cut-offs
leadbot/bot/__init__.py           empty
leadbot/bot/__main__.py           `python -m leadbot.bot`
leadbot/bot/seeds.py              load_seeds, city_plan -> CityPlan
leadbot/bot/score.py              load_scoring, lead_score, lead_tier, primary_flag, SIGNAL_ORDER
leadbot/bot/scrape.py             Place, to_e164, clean_website, parse_row, read_results, run_scraper
leadbot/bot/http.py               get_text, get_json (stdlib, USER_AGENT)
leadbot/bot/site_check.py         SiteCheck, inspect_html, check_site
leadbot/bot/pagespeed.py          mobile_score
leadbot/bot/domain_age.py         registrable_domain, registration_date
leadbot/bot/dnc.py                DncList, load_dnc, is_listed, missing_area_codes
leadbot/bot/seen.py               SeenStore
leadbot/bot/signals.py            Signal, Enrichment, RankInfo, rank_info, detect, gbp_status, true_flags
leadbot/bot/candidates.py         build_candidate, needs_text, to_payload, check_qualified, read/write_jsonl
leadbot/bot/collect.py            Deps, collect_city
leadbot/bot/cli.py                main(argv): collect | push
tests/fixtures/maps_rows.jsonl    7 fictional scraper rows
tests/test_bot_*.py               one test file per module
.claude/skills/leadbot-nightly/SKILL.md   the scheduled task's instructions
docs/scraper-build.md             how the patched scraper exe is rebuilt
```

---

### Task 1: Seeds config and loader

**Files:**
- Create: `config/seeds.toml`, `leadbot/bot/__init__.py` (empty), `leadbot/bot/seeds.py`
- Test: `tests/test_bot_seeds.py`

**Interfaces:**
- Consumes: `leadbot.env.ROOT`, `leadbot.ghl.schema.load_schema` (test only).
- Produces: `load_seeds(path=DEFAULT_SEEDS_PATH) -> Seeds(live: bool, depth: int, max_enrich: int, seen_days: int, niches: dict, cities: dict)`; `city_plan(seeds, niche, city) -> CityPlan(niche, city, money_query, money_keyword, queries: tuple, area_codes: tuple)`; `SeedError(ValueError)`.

- [ ] **Step 1: Write the config**

`config/seeds.toml`:

```toml
# Part B1 seeds (spec 3.1). live = false keeps every push a dry run.
live = false
scraper_depth = 5
max_enrich_per_city = 150
seen_days = 30

[niches."Counselors / therapists"]
money_query = "therapist in {city}"
money_keyword = "therapist {city_name}"
queries = ["counseling in {area}", "couples counseling {city}", "psychologist in {city}"]

[cities."Austin, TX"]
areas = ["Austin, TX", "Round Rock, TX", "Cedar Park, TX"]
area_codes = ["512", "737"]

[cities."Dallas, TX"]
areas = ["Dallas, TX", "Plano, TX", "Irving, TX"]
area_codes = ["214", "469", "972", "945"]

[cities."Houston, TX"]
areas = ["Houston, TX", "Katy, TX", "Sugar Land, TX"]
area_codes = ["713", "281", "832", "346"]
```

- [ ] **Step 2: Write the failing tests**

`tests/test_bot_seeds.py`:

```python
import pytest

from leadbot.bot.seeds import SeedError, city_plan, load_seeds
from leadbot.ghl.schema import load_schema

NICHE = "Counselors / therapists"


def test_austin_plan():
    p = city_plan(load_seeds(), NICHE, "Austin, TX")
    assert p.money_query == "therapist in Austin, TX"
    assert p.money_keyword == "therapist austin"
    assert p.queries == ("counseling in Austin, TX", "counseling in Round Rock, TX",
                         "counseling in Cedar Park, TX", "couples counseling Austin, TX",
                         "psychologist in Austin, TX")
    assert p.area_codes == ("512", "737")


def test_every_city_has_area_codes():
    seeds = load_seeds()
    for city in ("Austin, TX", "Dallas, TX", "Houston, TX"):
        assert city_plan(seeds, NICHE, city).area_codes


def test_seeds_default_to_dry_run():
    assert load_seeds().live is False


def test_seed_niches_exist_in_ghl_schema():
    assert set(load_seeds().niches) <= set(load_schema().niches)


def test_unknown_city_or_niche_raises():
    with pytest.raises(SeedError):
        city_plan(load_seeds(), NICHE, "Miami, FL")
    with pytest.raises(SeedError):
        city_plan(load_seeds(), "Plumbers", "Austin, TX")


def test_money_query_is_not_repeated(tmp_path):
    path = tmp_path / "seeds.toml"
    path.write_text(
        'live = false\nscraper_depth = 1\nmax_enrich_per_city = 5\nseen_days = 30\n'
        '[niches."HVAC"]\nmoney_query = "ac repair {city}"\nmoney_keyword = "ac repair {city_name}"\n'
        'queries = ["ac repair {city}", "hvac in {area}"]\n'
        '[cities."Austin, TX"]\nareas = ["Austin, TX"]\narea_codes = ["512"]\n',
        encoding="utf-8")
    p = city_plan(load_seeds(str(path)), "HVAC", "Austin, TX")
    assert p.queries == ("hvac in Austin, TX",)
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_bot_seeds.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'leadbot.bot'`.

- [ ] **Step 4: Implement**

`leadbot/bot/__init__.py`: empty file.

`leadbot/bot/seeds.py`:

```python
"""Seeds: which Maps queries to run for one niche in one city (spec 3.1)."""

import os
import tomllib
from dataclasses import dataclass

from leadbot.env import ROOT

DEFAULT_SEEDS_PATH = os.path.join(ROOT, "config", "seeds.toml")


class SeedError(ValueError):
    pass


@dataclass(frozen=True)
class Seeds:
    live: bool
    depth: int
    max_enrich: int
    seen_days: int
    niches: dict
    cities: dict


@dataclass(frozen=True)
class CityPlan:
    niche: str
    city: str
    money_query: str
    money_keyword: str
    queries: tuple
    area_codes: tuple


def load_seeds(path=DEFAULT_SEEDS_PATH):
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    return Seeds(bool(raw.get("live", False)), int(raw["scraper_depth"]),
                 int(raw["max_enrich_per_city"]), int(raw["seen_days"]),
                 dict(raw["niches"]), dict(raw["cities"]))


def city_plan(seeds, niche, city):
    if niche not in seeds.niches:
        raise SeedError(f"niche {niche!r} is not in seeds.toml")
    if city not in seeds.cities:
        raise SeedError(f"city {city!r} is not in seeds.toml")
    n, c = seeds.niches[niche], seeds.cities[city]
    fmt = {"city": city, "city_name": city.split(",")[0].strip()}
    money_query = n["money_query"].format(**fmt)
    queries = []
    for template in n["queries"]:
        for area in (c["areas"] if "{area}" in template else [None]):
            query = template.format(area=area, **fmt)
            if query != money_query and query not in queries:
                queries.append(query)
    return CityPlan(niche, city, money_query, n["money_keyword"].format(**fmt).lower(),
                    tuple(queries), tuple(c["area_codes"]))
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_bot_seeds.py -q` → 6 passed. Then `python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add config/seeds.toml leadbot/bot/__init__.py leadbot/bot/seeds.py tests/test_bot_seeds.py
git commit -m "feat(bot): seeds config and city query plan

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Scoring config and score module

**Files:**
- Create: `config/scoring.toml`, `leadbot/bot/score.py`
- Test: `tests/test_bot_score.py`

**Interfaces:**
- Produces: `SIGNAL_ORDER` (tuple, Global Constraints order); `load_scoring(path=DEFAULT_SCORING_PATH) -> Scoring(weights: dict, hot: int, warm: int, retention_niches: tuple)`; `lead_score(true_signals: set, niche: str, scoring, ads_spend=None) -> int`; `lead_tier(score, scoring) -> "Hot"|"Warm"|"Cold"`; `primary_flag(true_signals, scoring) -> str|None` (a signal flag, not a GHL category).

- [ ] **Step 1: Write the config**

`config/scoring.toml`:

```toml
# SOP Step 4 weights (spec 3.6). Re-weight monthly against closed deals.
retention_niches = ["Counselors / therapists"]

[weights]
gbp_unverified_or_suspended = 25
google_ads_spend = 20
google_ads_spend_over_3k_bonus = 10
meta_ads_active = 10
broken_website = 15
new_business = 15
rank_gap = 10
technical_debt = 5
competitor_gap = 5
retention_niche = 5

[tiers]
hot = 70
warm = 45
```

- [ ] **Step 2: Write the failing tests**

`tests/test_bot_score.py`:

```python
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
        "retention_niche": 5}
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
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_bot_score.py -q` → FAIL (`No module named 'leadbot.bot.score'`).

- [ ] **Step 4: Implement**

`leadbot/bot/score.py`:

```python
"""Score, tier and primary signal from the SOP Step 4 table (spec 3.6)."""

import os
import tomllib
from dataclasses import dataclass

from leadbot.env import ROOT

DEFAULT_SCORING_PATH = os.path.join(ROOT, "config", "scoring.toml")

# SOP Step 4 table order; also breaks ties for the primary signal.
SIGNAL_ORDER = ("gbp_unverified_or_suspended", "google_ads_spend", "meta_ads_active",
                "broken_website", "new_business", "rank_gap", "technical_debt", "competitor_gap")


@dataclass(frozen=True)
class Scoring:
    weights: dict
    hot: int
    warm: int
    retention_niches: tuple


def load_scoring(path=DEFAULT_SCORING_PATH):
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    return Scoring(dict(raw["weights"]), int(raw["tiers"]["hot"]), int(raw["tiers"]["warm"]),
                   tuple(raw.get("retention_niches", ())))


def lead_score(true_signals, niche, scoring, ads_spend=None):
    weights = scoring.weights
    total = sum(weights[s] for s in SIGNAL_ORDER if s in true_signals)
    if "google_ads_spend" in true_signals and ads_spend and ads_spend > 3000:
        total += weights["google_ads_spend_over_3k_bonus"]
    if niche in scoring.retention_niches:
        total += weights["retention_niche"]
    return max(0, min(total, 100))


def lead_tier(score, scoring):
    if score >= scoring.hot:
        return "Hot"
    if score >= scoring.warm:
        return "Warm"
    return "Cold"


def primary_flag(true_signals, scoring):
    present = [s for s in SIGNAL_ORDER if s in true_signals]
    if not present:
        return None
    return max(present, key=lambda s: scoring.weights[s])  # max keeps the first of equals
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_bot_score.py -q` → 14 passed; `python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add config/scoring.toml leadbot/bot/score.py tests/test_bot_score.py
git commit -m "feat(bot): SOP scoring table, tiers and primary signal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Scraper wrapper and fixture

**Files:**
- Create: `leadbot/bot/scrape.py`, `tests/fixtures/maps_rows.jsonl`
- Test: `tests/test_bot_scrape.py`

**Interfaces:**
- Produces: `Place` (frozen dataclass: `place_id, name, category, categories: tuple, phone: str|None (E.164), website: str|None, gbp_url: str|None, owner_claimed: bool, status: str, review_count: int, rating: float|None, review_dates: tuple[date] (sorted ascending), email: str|None`; property `closed`); `to_e164(raw)`; `clean_website(url)`; `parse_row(row: dict) -> Place`; `read_results(path) -> list[Place]` (file order, skips rows without place_id or title); `run_scraper(queries, out_dir, name, *, exe, depth, runner=subprocess.run, timeout=10800) -> list[Place]`, writes `<name>.queries.txt`, `<name>.raw.jsonl`, `<name>.scraper.log` in `out_dir`; `ScrapeError`.
- Review dates come from each review's `published_at` (ISO, `Z`), falling back to `posted_at_unix_micros`. The relative `When` text is not parsed (the scraper's absolute timestamps make that unnecessary; this refines spec 3.5's "relative ages are parsed").

- [ ] **Step 1: Write the fixture**

`tests/fixtures/maps_rows.jsonl` (seven lines, each one JSON object):

```
{"title": "Lakeview Family Therapy (fictional)", "category": "Psychotherapist", "categories": ["Psychotherapist", "Family counselor"], "phone": "+1 512-555-0131", "web_site": "https://lakeview.example.com/", "link": "https://www.google.com/maps/place/?q=place_id:fx-lakeview", "owner": {"id": "900000000000000000001", "name": "Lakeview Family Therapy (Owner)", "link": ""}, "status": "", "review_count": 412, "review_rating": 4.8, "user_reviews": [{"When": "2 weeks ago", "published_at": "2026-10-20T10:00:00Z"}, {"When": "5 months ago", "published_at": "2026-05-11T10:00:00Z"}], "place_id": "fx-lakeview", "emails": null}
{"title": "Bright Path Counseling (fictional)", "category": "Counselor", "categories": ["Counselor"], "phone": "(512) 555-0132", "web_site": "https://BrightPath.example.com/?utm_source=gbp", "link": "https://www.google.com/maps/place/?q=place_id:fx-brightpath", "owner": {"id": "", "name": "", "link": ""}, "status": "", "review_count": 4, "review_rating": 5, "user_reviews": [{"When": "a week ago", "published_at": "2026-10-25T09:00:00Z"}, {"When": "a month ago", "published_at": "2026-10-02T09:00:00Z"}, {"When": "a month ago", "published_at": "2026-09-20T09:00:00Z"}], "place_id": "fx-brightpath", "emails": null}
{"title": "Oak Hill Psychology (fictional)", "category": "Psychologist", "categories": ["Psychologist"], "phone": "512-555-0133", "web_site": "https://oakhill.example.com/", "link": "https://www.google.com/maps/place/?q=place_id:fx-oakhill", "owner": {"id": "900000000000000000003", "name": "Oak Hill Psychology (Owner)", "link": ""}, "status": "Permanently closed", "review_count": 37, "review_rating": 4.1, "user_reviews": [], "place_id": "fx-oakhill", "emails": null}
{"title": "Quiet Mind Therapy (fictional)", "category": "Therapist", "categories": ["Therapist"], "phone": "", "web_site": "", "link": "https://www.google.com/maps/place/?q=place_id:fx-quietmind", "owner": null, "status": "", "review_count": 2, "review_rating": 5, "user_reviews": [], "place_id": "fx-quietmind", "emails": null}
{"title": "Cedar Counseling Group (fictional)", "category": "Counselor", "categories": ["Counselor"], "phone": "512-555-0135", "web_site": "", "link": "https://www.google.com/maps/place/?q=place_id:fx-cedar", "owner": {"id": "900000000000000000005", "name": "Cedar Counseling Group (Owner)", "link": ""}, "status": "", "review_count": 9, "review_rating": 4.9, "user_reviews": [{"When": "2 years ago", "published_at": "2024-06-01T09:00:00Z"}, {"When": "2 years ago", "published_at": "2024-03-01T09:00:00Z"}], "place_id": "fx-cedar", "emails": ["hello@cedar.example.com"]}
{"title": "Sunrise Behavioral Health - Austin (fictional)", "category": "Mental health clinic", "categories": ["Mental health clinic"], "phone": "+1 512-555-0136", "web_site": "https://sunrise.example.com/austin", "link": "https://www.google.com/maps/place/?q=place_id:fx-sunrise", "owner": {"id": "900000000000000000006", "name": "Sunrise Behavioral Health (Owner)", "link": ""}, "status": "", "review_count": 1200, "review_rating": 4.6, "user_reviews": [{"When": "3 weeks ago", "published_at": "2026-10-15T09:00:00Z"}], "place_id": "fx-sunrise", "emails": null}
{"title": "Row Without Place Id (fictional)", "phone": "+1 512-555-0137"}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_bot_scrape.py`:

```python
import datetime as dt
import os
import shutil
import subprocess

import pytest

from leadbot.bot.scrape import ScrapeError, clean_website, read_results, run_scraper, to_e164

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "maps_rows.jsonl")


def by_name(places, prefix):
    return next(p for p in places if p.name.startswith(prefix))


def test_reads_rows_and_skips_rows_without_place_id():
    places = read_results(FIXTURE)
    assert len(places) == 6
    assert [p.place_id for p in places][:2] == ["fx-lakeview", "fx-brightpath"]


def test_unclaimed_new_listing_is_normalised():
    p = by_name(read_results(FIXTURE), "Bright Path")
    assert p.owner_claimed is False
    assert p.phone == "+15125550132"
    assert p.website == "https://brightpath.example.com/"
    assert p.review_count == 4
    assert p.rating == 5.0
    assert p.review_dates == (dt.date(2026, 9, 20), dt.date(2026, 10, 2), dt.date(2026, 10, 25))
    assert p.email is None


def test_claimed_closed_and_email():
    places = read_results(FIXTURE)
    assert by_name(places, "Lakeview").owner_claimed is True
    assert by_name(places, "Oak Hill").closed is True
    assert by_name(places, "Lakeview").closed is False
    assert by_name(places, "Quiet Mind").owner_claimed is False
    assert by_name(places, "Quiet Mind").phone is None
    assert by_name(places, "Cedar").email == "hello@cedar.example.com"


def test_missing_results_file_reads_as_empty(tmp_path):
    assert read_results(str(tmp_path / "none.jsonl")) == []


@pytest.mark.parametrize("raw,expected", [
    ("+1 512-555-0132", "+15125550132"), ("(512) 555-0132", "+15125550132"),
    ("15125550132", "+15125550132"), ("555-0132", None), ("", None), (None, None)])
def test_to_e164(raw, expected):
    assert to_e164(raw) == expected


def test_clean_website():
    assert clean_website("https://BrightPath.example.com/?utm_source=gbp") == \
        "https://brightpath.example.com/"
    assert clean_website("https://sunrise.example.com/austin") == "https://sunrise.example.com/austin"
    assert clean_website("brightpath.example.com") is None
    assert clean_website("") is None


class FakeRunner:
    def __init__(self, returncode=0):
        self.returncode, self.args = returncode, None

    def __call__(self, args, stdout, stderr, timeout):
        self.args = args
        shutil.copy(FIXTURE, args[args.index("-results") + 1])
        return subprocess.CompletedProcess(args, self.returncode)


def test_run_scraper_writes_queries_and_reads_results(tmp_path):
    runner = FakeRunner()
    places = run_scraper(["therapist in Austin, TX"], str(tmp_path), "money",
                         exe="gmaps.exe", depth=5, runner=runner)
    assert len(places) == 6
    assert (tmp_path / "money.queries.txt").read_text(encoding="utf-8") == "therapist in Austin, TX\n"
    a = runner.args
    assert a[0] == "gmaps.exe"
    assert a[a.index("-depth") + 1] == "5" and a[a.index("-c") + 1] == "1"
    assert "-json" in a and a[a.index("-exit-on-inactivity") + 1] == "3m"


def test_run_scraper_nonzero_exit_raises(tmp_path):
    with pytest.raises(ScrapeError):
        run_scraper(["q"], str(tmp_path), "money", exe="gmaps.exe", depth=1,
                    runner=FakeRunner(returncode=1))
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_bot_scrape.py -q` → FAIL (module missing).

- [ ] **Step 4: Implement**

`leadbot/bot/scrape.py`:

```python
"""Runs gosom's Google Maps scraper and normalises its JSON Lines (spec 3.2)."""

import datetime as dt
import json
import os
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class ScrapeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Place:
    place_id: str
    name: str
    category: str
    categories: tuple
    phone: str | None
    website: str | None
    gbp_url: str | None
    owner_claimed: bool
    status: str
    review_count: int
    rating: float | None
    review_dates: tuple
    email: str | None

    @property
    def closed(self):
        return "closed" in self.status.lower()


def to_e164(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def clean_website(url):
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", "", ""))


def _review_date(review):
    raw = review.get("published_at")
    if raw:
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    micros = review.get("posted_at_unix_micros")
    if micros:
        return dt.datetime.fromtimestamp(micros / 1_000_000, dt.timezone.utc).date()
    return None


def parse_row(row):
    owner = row.get("owner") or {}
    emails = row.get("emails") or []
    dates = [d for d in (_review_date(r) for r in row.get("user_reviews") or []) if d]
    return Place(
        place_id=row.get("place_id") or "",
        name=(row.get("title") or "").strip(),
        category=row.get("category") or "",
        categories=tuple(row.get("categories") or ()),
        phone=to_e164(row.get("phone")),
        website=clean_website(row.get("web_site")),
        gbp_url=row.get("link") or None,
        owner_claimed=bool(owner.get("id")),
        status=row.get("status") or "",
        review_count=int(row.get("review_count") or 0),
        rating=float(row["review_rating"]) if row.get("review_rating") else None,
        review_dates=tuple(sorted(dates)),
        email=emails[0].lower() if emails else None,
    )


def read_results(path):
    places = []
    if not os.path.exists(path):
        return places
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            place = parse_row(json.loads(line))
            if place.place_id and place.name:
                places.append(place)
    return places


def run_scraper(queries, out_dir, name, *, exe, depth, runner=subprocess.run, timeout=3 * 3600):
    os.makedirs(out_dir, exist_ok=True)
    query_file = os.path.join(out_dir, f"{name}.queries.txt")
    results = os.path.join(out_dir, f"{name}.raw.jsonl")
    log = os.path.join(out_dir, f"{name}.scraper.log")
    with open(query_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(queries) + "\n")
    args = [exe, "-input", query_file, "-results", results, "-json", "-depth", str(depth),
            "-c", "1", "-lang", "en", "-exit-on-inactivity", "3m"]
    with open(log, "w", encoding="utf-8") as fh:
        proc = runner(args, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
    if proc.returncode != 0:
        raise ScrapeError(f"scraper exited {proc.returncode}; see {log}")
    return read_results(results)
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_bot_scrape.py -q` → 13 passed; `python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add leadbot/bot/scrape.py tests/fixtures/maps_rows.jsonl tests/test_bot_scrape.py
git commit -m "feat(bot): Google Maps scraper wrapper and row normalisation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Enrichment (site check, PageSpeed, domain age)

**Files:**
- Create: `leadbot/bot/http.py`, `leadbot/bot/site_check.py`, `leadbot/bot/pagespeed.py`, `leadbot/bot/domain_age.py`
- Test: `tests/test_bot_enrich.py`

**Interfaces:**
- Consumes: `leadbot.ghl.client.USER_AGENT`.
- Produces: `http.get_text(url, timeout=15) -> (final_url, status, text)`; `http.get_json(url, timeout=60) -> dict`; `SiteCheck(reachable, final_url=None, has_form=False, has_tel=False, has_tracking=False, email=None)` (frozen); `check_site(url, fetch=http.get_text) -> SiteCheck` (never raises); `mobile_score(url, get_json=http.get_json) -> int|None`; `registrable_domain(url) -> str|None`; `registration_date(url, get_json=http.get_json) -> date|None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_enrich.py`:

```python
import datetime as dt

from leadbot.bot.domain_age import registrable_domain, registration_date
from leadbot.bot.pagespeed import mobile_score
from leadbot.bot.site_check import SiteCheck, check_site

GOOD = ('<html><head><script async src="https://www.googletagmanager.com/gtag/js?id=G-X">'
        '</script></head><body><a href="tel:+15125550132">Call</a><form action="/c"></form>'
        '<a href="mailto:Hello@BrightPath.example.com">Email</a></body></html>')


def test_good_homepage():
    r = check_site("https://x.example.com/", fetch=lambda url: (url, 200, GOOD))
    assert r == SiteCheck(True, "https://x.example.com/", True, True, True,
                          "hello@brightpath.example.com")


def test_bare_homepage():
    r = check_site("https://x.example.com/", fetch=lambda url: (url, 200, "<html><body>Hi</body></html>"))
    assert (r.reachable, r.has_form, r.has_tel, r.has_tracking, r.email) == (True, False, False, False, None)


def test_meta_pixel_counts_as_tracking():
    html = "<script>fbq('init', '1');</script>"
    assert check_site("https://x.example.com/", fetch=lambda url: (url, 200, html)).has_tracking


def test_error_status_is_unreachable():
    assert check_site("https://x.example.com/", fetch=lambda url: (url, 404, "")).reachable is False


def test_network_error_is_unreachable():
    def boom(url):
        raise OSError("timed out")
    assert check_site("https://x.example.com/", fetch=boom) == SiteCheck(False)


def test_pagespeed_score_and_request():
    seen = []

    def fake(url):
        seen.append(url)
        return {"lighthouseResult": {"categories": {"performance": {"score": 0.37}}}}
    assert mobile_score("https://x.example.com/a?b=1", get_json=fake) == 37
    assert "strategy=mobile" in seen[0]
    assert "url=https%3A%2F%2Fx.example.com%2Fa%3Fb%3D1" in seen[0]


def test_pagespeed_error_is_unknown():
    def boom(url):
        raise OSError("429")
    assert mobile_score("https://x.example.com/", get_json=boom) is None


def test_registrable_domain():
    assert registrable_domain("https://www.brightpath.example.com/x") == "example.com"
    assert registrable_domain("https://www.calmtherapy.com/") == "calmtherapy.com"
    assert registrable_domain("") is None


def test_registration_date():
    seen = []

    def fake(url):
        seen.append(url)
        return {"events": [{"eventAction": "last changed", "eventDate": "2026-01-01T00:00:00Z"},
                           {"eventAction": "registration", "eventDate": "2026-08-01T12:00:00Z"}]}
    assert registration_date("https://www.calmtherapy.com/", get_json=fake) == dt.date(2026, 8, 1)
    assert seen == ["https://rdap.org/domain/calmtherapy.com"]


def test_registration_error_is_unknown():
    def boom(url):
        raise OSError("404")
    assert registration_date("https://www.calmtherapy.com/", get_json=boom) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bot_enrich.py -q` → FAIL (modules missing).

- [ ] **Step 3: Implement**

`leadbot/bot/http.py`:

```python
"""Small stdlib HTTP helpers for enrichment (spec 3.4)."""

import json
import urllib.request

from leadbot.ghl.client import USER_AGENT

MAX_BYTES = 2_000_000


def get_text(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/html,application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), resp.status, raw.decode(charset, errors="replace")


def get_json(url, timeout=60):
    _, _, text = get_text(url, timeout)
    return json.loads(text)
```

`leadbot/bot/site_check.py`:

```python
"""Homepage check for the broken-website signal (spec 3.4)."""

import re
from dataclasses import dataclass

from leadbot.bot import http

TRACKING = ("googletagmanager.com", "gtag(", "fbq(", "connect.facebook.net")
_FORM = re.compile(r"<form\b", re.I)
_TEL = re.compile(r"""href\s*=\s*["']\s*tel:""", re.I)
_MAILTO = re.compile(r"mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.I)


@dataclass(frozen=True)
class SiteCheck:
    reachable: bool
    final_url: str | None = None
    has_form: bool = False
    has_tel: bool = False
    has_tracking: bool = False
    email: str | None = None


def inspect_html(final_url, html):
    lower = html.lower()
    mail = _MAILTO.search(html)
    return SiteCheck(True, final_url, bool(_FORM.search(html)), bool(_TEL.search(html)),
                     any(t in lower for t in TRACKING), mail.group(1).lower() if mail else None)


def check_site(url, fetch=http.get_text):
    try:
        final_url, status, html = fetch(url)
    except Exception:  # noqa: BLE001 - any network failure means "did not load"
        return SiteCheck(False)
    if status >= 400:
        return SiteCheck(False, final_url)
    return inspect_html(final_url, html)
```

`leadbot/bot/pagespeed.py`:

```python
"""PageSpeed Insights mobile performance score, no API key (spec 3.4)."""

import urllib.parse

from leadbot.bot import http

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def mobile_score(url, get_json=http.get_json):
    query = urllib.parse.urlencode({"url": url, "strategy": "mobile", "category": "performance"})
    try:
        score = get_json(f"{PSI_URL}?{query}")["lighthouseResult"]["categories"]["performance"]["score"]
    except Exception:  # noqa: BLE001 - quota, timeout or shape change all mean "unknown"
        return None
    return None if score is None else round(score * 100)
```

`leadbot/bot/domain_age.py`:

```python
"""Domain registration date through RDAP (spec 3.4)."""

import datetime as dt
from urllib.parse import urlsplit

from leadbot.bot import http

RDAP_URL = "https://rdap.org/domain/{}"


def registrable_domain(url):
    host = (urlsplit(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else None


def registration_date(url, get_json=http.get_json):
    domain = registrable_domain(url)
    if not domain:
        return None
    try:
        data = get_json(RDAP_URL.format(domain))
    except Exception:  # noqa: BLE001 - unknown registry, rate limit or timeout
        return None
    for event in data.get("events") or []:
        if event.get("eventAction") == "registration" and event.get("eventDate"):
            try:
                return dt.datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00")).date()
            except ValueError:
                return None
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bot_enrich.py -q` → 10 passed; `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add leadbot/bot/http.py leadbot/bot/site_check.py leadbot/bot/pagespeed.py leadbot/bot/domain_age.py tests/test_bot_enrich.py
git commit -m "feat(bot): website check, PageSpeed and domain age enrichment

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: DNC lookup and seen store

**Files:**
- Create: `leadbot/bot/dnc.py`, `leadbot/bot/seen.py`
- Test: `tests/test_bot_dnc_seen.py`

**Interfaces:**
- Produces: `DncList(numbers: set)` with property `area_codes`; `load_dnc(directory=DEFAULT_DNC_DIR) -> DncList` (reads `*.txt` / `*.csv`, keeps every line that holds a 10-digit US number, with or without a leading 1); `is_listed(dnc, e164) -> bool`; `missing_area_codes(dnc, area_codes) -> list`; `SeenStore(path)` with `recently_seen(place_id, today, days) -> bool` (True when last seen fewer than `days` days ago), `mark(place_id, today, outcome)`, `close()`.
- The FTC file layout is not known yet; the loader is deliberately tolerant, and Task 11 checks it against a real file.

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_dnc_seen.py`:

```python
import datetime as dt

from leadbot.bot.dnc import is_listed, load_dnc, missing_area_codes
from leadbot.bot.seen import SeenStore


def test_dnc_loads_ten_digit_numbers(tmp_path):
    d = tmp_path / "dnc"
    d.mkdir()
    (d / "512.txt").write_text("5125550101\n512,5550102\n1-512-555-0103\nheader line\n",
                               encoding="utf-8")
    (d / "notes.md").write_text("5125550199\n", encoding="utf-8")
    dnc = load_dnc(str(d))
    assert dnc.numbers == {"5125550101", "5125550102", "5125550103"}
    assert is_listed(dnc, "+15125550102")
    assert not is_listed(dnc, "+15125550199")
    assert not is_listed(dnc, None)
    assert missing_area_codes(dnc, ("512", "737")) == ["737"]


def test_missing_dnc_dir_is_empty(tmp_path):
    assert load_dnc(str(tmp_path / "none")).numbers == set()


def test_seen_window():
    s = SeenStore(":memory:")
    s.mark("p1", dt.date(2026, 11, 1), "Hot")
    assert s.recently_seen("p1", dt.date(2026, 11, 30), 30)
    assert not s.recently_seen("p1", dt.date(2026, 12, 1), 30)
    assert not s.recently_seen("other", dt.date(2026, 11, 2), 30)


def test_seen_mark_moves_last_seen(tmp_path):
    path = str(tmp_path / "seen.sqlite")
    s = SeenStore(path)
    s.mark("p1", dt.date(2026, 1, 1), "Cold")
    s.mark("p1", dt.date(2026, 11, 1), "Warm")
    s.close()
    assert SeenStore(path).recently_seen("p1", dt.date(2026, 11, 20), 30)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bot_dnc_seen.py -q` → FAIL (modules missing).

- [ ] **Step 3: Implement**

`leadbot/bot/dnc.py`:

```python
"""National Do Not Call lookup from FTC area-code files in state/dnc/ (spec 4)."""

import os
import re
from dataclasses import dataclass, field

from leadbot.env import ROOT

DEFAULT_DNC_DIR = os.path.join(ROOT, "state", "dnc")


@dataclass
class DncList:
    numbers: set = field(default_factory=set)

    @property
    def area_codes(self):
        return {n[:3] for n in self.numbers}


def _ten_digits(text):
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def load_dnc(directory=DEFAULT_DNC_DIR):
    dnc = DncList()
    if not os.path.isdir(directory):
        return dnc
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith((".txt", ".csv")):
            continue
        with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                number = _ten_digits(line)
                if number:
                    dnc.numbers.add(number)
    return dnc


def is_listed(dnc, e164):
    number = _ten_digits(e164)
    return bool(number) and number in dnc.numbers


def missing_area_codes(dnc, area_codes):
    have = dnc.area_codes
    return [code for code in area_codes if code not in have]
```

`leadbot/bot/seen.py`:

```python
"""Which places a live run already handled (spec 3.3, filter 4)."""

import datetime as dt
import os
import sqlite3


class SeenStore:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (place_id TEXT PRIMARY KEY, "
                        "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_outcome TEXT)")

    def recently_seen(self, place_id, today, days):
        row = self.db.execute("SELECT last_seen FROM seen WHERE place_id = ?",
                              (place_id,)).fetchone()
        return bool(row) and (today - dt.date.fromisoformat(row[0])).days < days

    def mark(self, place_id, today, outcome):
        day = today.isoformat()
        self.db.execute(
            "INSERT INTO seen VALUES (?, ?, ?, ?) ON CONFLICT(place_id) DO UPDATE SET "
            "last_seen = excluded.last_seen, last_outcome = excluded.last_outcome",
            (place_id, day, day, outcome))
        self.db.commit()

    def close(self):
        self.db.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bot_dnc_seen.py -q` → 4 passed; `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add leadbot/bot/dnc.py leadbot/bot/seen.py tests/test_bot_dnc_seen.py
git commit -m "feat(bot): DNC area-code lookup and seen-places store

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Signal detection

**Files:**
- Create: `leadbot/bot/signals.py`
- Test: `tests/test_bot_signals.py`

**Interfaces:**
- Consumes: `Place` (Task 3), `SiteCheck` (Task 4).
- Produces: `TRUE, FALSE, UNKNOWN` (strings); `Signal(state, evidence)`; `Enrichment(site=None, pagespeed=None, registered=None)`; `RankInfo(keyword, checked, position, top3_avg_reviews)`; `rank_info(place_id, money_places, keyword) -> RankInfo` (position 1-based or None; top-3 average excludes the place itself); `detect(place, enrich, rank, today) -> dict[str, Signal]` with all eight `SIGNAL_ORDER` keys; `gbp_status(place) -> "Verified"|"Unverified"`; `true_flags(signals) -> set`.

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_signals.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bot_signals.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

`leadbot/bot/signals.py`:

```python
"""Signal detection with evidence (spec 3.5). States: TRUE, FALSE, UNKNOWN."""

import datetime as dt
from dataclasses import dataclass

TRUE, FALSE, UNKNOWN = "TRUE", "FALSE", "UNKNOWN"
NOT_IN_B1 = ("google_ads_spend", "meta_ads_active", "technical_debt")
NEW_DOMAIN_DAYS = 183
NEW_REVIEW_DAYS = 92
NEW_MAX_REVIEWS = 15
SLOW_PAGESPEED = 40
MAP_PACK = 3


@dataclass(frozen=True)
class Signal:
    state: str
    evidence: str


@dataclass(frozen=True)
class Enrichment:
    site: object = None  # SiteCheck, or None when the site was not checked
    pagespeed: int | None = None
    registered: dt.date | None = None


@dataclass(frozen=True)
class RankInfo:
    keyword: str
    checked: int  # places returned for the money keyword
    position: int | None  # 1-based; None when the place is not in those results
    top3_avg_reviews: float | None


def rank_info(place_id, money_places, keyword):
    position = next((i for i, p in enumerate(money_places, 1) if p.place_id == place_id), None)
    rivals = [p.review_count for p in money_places if p.place_id != place_id][:3]
    average = sum(rivals) / len(rivals) if rivals else None
    return RankInfo(keyword, len(money_places), position, average)


def gbp_status(place):
    return "Verified" if place.owner_claimed else "Unverified"


def _gbp(place):
    if place.owner_claimed:
        return Signal(FALSE, "Google listing is owner-claimed")
    return Signal(TRUE, "Google listing has no owner (unclaimed)")


def _site(place, enrich):
    if not place.website:
        return Signal(TRUE, "no website on the Google listing")
    site = enrich.site
    if site is None:
        return Signal(UNKNOWN, "website not checked")
    if not site.reachable:
        return Signal(TRUE, f"website {place.website} did not load")
    fails = []
    if not site.has_form:
        fails.append("no lead form")
    if not site.has_tel:
        fails.append("no click-to-call link")
    if not site.has_tracking:
        fails.append("no conversion tag (gtag/GTM/Meta Pixel)")
    if enrich.pagespeed is not None and enrich.pagespeed < SLOW_PAGESPEED:
        fails.append(f"mobile PageSpeed {enrich.pagespeed}")
    if len(fails) >= 2:
        return Signal(TRUE, "homepage: " + ", ".join(fails))
    return Signal(FALSE, "homepage: " + (fails[0] if fails else "form, call link and tag present"))


def _new(place, enrich, today):
    if enrich.registered and (today - enrich.registered).days < NEW_DOMAIN_DAYS:
        return Signal(TRUE, f"domain registered {enrich.registered.isoformat()}")
    if place.review_dates and place.review_count <= NEW_MAX_REVIEWS:
        oldest = place.review_dates[0]
        if (today - oldest).days <= NEW_REVIEW_DAYS:
            return Signal(TRUE, f"{place.review_count} reviews, oldest visible {oldest.isoformat()}")
        return Signal(FALSE, f"oldest visible review {oldest.isoformat()}")
    if place.review_count > NEW_MAX_REVIEWS:
        return Signal(FALSE, f"{place.review_count} reviews")
    if enrich.registered:
        return Signal(FALSE, f"domain registered {enrich.registered.isoformat()}")
    return Signal(UNKNOWN, "no reviews and no domain date")


def _rank(rank):
    if rank.checked == 0:
        return Signal(UNKNOWN, f"no Maps results for '{rank.keyword}'")
    if rank.position is None:
        return Signal(TRUE, f"not in the top {rank.checked} on Maps for '{rank.keyword}'")
    if rank.position > MAP_PACK:
        return Signal(TRUE, f"#{rank.position} on Maps for '{rank.keyword}'")
    return Signal(FALSE, f"#{rank.position} on Maps for '{rank.keyword}' (map pack)")


def _gap(place, rank):
    average = rank.top3_avg_reviews
    if average is None:
        return Signal(UNKNOWN, "no competitors to compare")
    text = f"top 3 for '{rank.keyword}' average {average:.0f} reviews vs {place.review_count}"
    return Signal(TRUE if average >= 3 * max(place.review_count, 1) else FALSE, text)


def detect(place, enrich, rank, today):
    signals = {
        "gbp_unverified_or_suspended": _gbp(place),
        "broken_website": _site(place, enrich),
        "new_business": _new(place, enrich, today),
        "rank_gap": _rank(rank),
        "competitor_gap": _gap(place, rank),
    }
    for name in NOT_IN_B1:
        signals[name] = Signal(UNKNOWN, "not checked in B1")
    return signals


def true_flags(signals):
    return {name for name, s in signals.items() if s.state == TRUE}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bot_signals.py -q` → 8 passed; `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add leadbot/bot/signals.py tests/test_bot_signals.py
git commit -m "feat(bot): signal detection with evidence

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Candidates and the collect step

**Files:**
- Create: `leadbot/bot/candidates.py`, `leadbot/bot/collect.py`
- Test: `tests/test_bot_candidates.py`, `tests/test_bot_collect.py`

**Interfaces:**
- Consumes: Tasks 1-6; `schema.signals` (flag → GHL category) from `leadbot.ghl.schema.load_schema`.
- Produces (candidates): `EXTRA_KEYS = ("evidence", "unknown", "place_id")`; `LOCKED_KEYS`; `build_candidate(place, signals, *, score, tier, primary, plan, bot_run_id, email, gbp_status, rank) -> dict`; `needs_text(record) -> bool` (Hot/Warm, or Cold with an email); `to_payload(record) -> dict` (drops `EXTRA_KEYS`); `check_qualified(qualified, candidate)` raises `QualifiedError`; `write_jsonl(path, records)`; `read_jsonl(path) -> list`.
- Produces (collect): `Deps(scrape, site_check, pagespeed, domain_age, find_contact)` (mutable dataclass of callables: `scrape(queries: list, name: str) -> list[Place]`, `site_check(url) -> SiteCheck`, `pagespeed(url) -> int|None`, `domain_age(url) -> date|None`, `find_contact(phone, email) -> dict|None`); `collect_city(plan, seeds, scoring, schema, deps, *, run_id, run_dir, today, dnc=None, seen=None) -> dict report`, writing `candidates.jsonl`, `dropped.jsonl`, `report.json` in `run_dir`. Report keys: `run_id, niche, city, dnc_checked, scraped, dropped (reason → count), tiers (tier → count), needs_text, status ("ok" | "blocked_or_failed")`.
- Drop reasons, in this order: `closed`, `no_phone`, `dnc`, `seen_recently`, `ghl_contact`, then `over_nightly_cap` for places past `seeds.max_enrich`.
- PageSpeed runs only for a reachable website whose place already has a TRUE signal.

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_candidates.py`:

```python
import pytest

from leadbot.bot.candidates import QualifiedError, check_qualified, needs_text, to_payload

BASE = {"business_name": "Bright Path Counseling (fictional)", "phone": "+15125550132",
        "email": None, "niche": "Counselors / therapists", "signals": ["new_business"],
        "primary_signal": "New Biz", "lead_score": 20, "lead_tier": "Cold",
        "audit_summary": "", "call_opener": "", "bot_run_id": "r-0001",
        "evidence": {"new_business": "4 reviews"}, "unknown": [], "place_id": "fx-1"}


def filled(**over):
    q = dict(BASE, audit_summary="Only 4 reviews, all recent.",
             call_opener="Hi, congrats on opening Bright Path Counseling.")
    q.update(over)
    return q


def test_needs_text():
    assert needs_text(dict(BASE, lead_tier="Hot"))
    assert needs_text(dict(BASE, lead_tier="Warm"))
    assert not needs_text(BASE)
    assert needs_text(dict(BASE, email="hi@x.example.com"))


def test_to_payload_drops_extra_keys():
    payload = to_payload(BASE)
    assert "evidence" not in payload and "place_id" not in payload and "unknown" not in payload
    assert payload["bot_run_id"] == "r-0001"


def test_valid_qualified_passes():
    check_qualified(filled(), BASE)


@pytest.mark.parametrize("over,needle", [
    ({"lead_score": 90}, "lead_score"), ({"lead_tier": "Hot"}, "lead_tier"),
    ({"signals": []}, "signals"), ({"phone": "+15125550199"}, "phone"),
    ({"audit_summary": ""}, "audit_summary"), ({"call_opener": " "}, "call_opener"),
    ({"audit_summary": "a\nb\nc\nd"}, "audit_summary"),
    ({"call_opener": " ".join(["word"] * 31)}, "call_opener")])
def test_invalid_qualified_rejected(over, needle):
    with pytest.raises(QualifiedError, match=needle):
        check_qualified(filled(**over), BASE)
```

`tests/test_bot_collect.py`:

```python
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
    assert dropped["fx-cedar"] == "dnc"
    assert dropped["fx-lakeview"] == "seen_recently"
    assert dropped["fx-sunrise"] == "ghl_contact"
    assert set(cands) == {"fx-brightpath"}
    assert report["dnc_checked"] is True


def test_old_seen_entry_is_collected_again(tmp_path):
    seen = SeenStore(":memory:")
    seen.mark("fx-lakeview", TODAY - dt.timedelta(days=31), "Cold")
    _, cands, _ = run(tmp_path, seen=seen)
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bot_candidates.py tests/test_bot_collect.py -q` → FAIL (modules missing).

- [ ] **Step 3: Implement**

`leadbot/bot/candidates.py`:

```python
"""Candidate and qualified records (spec 3.7, 3.8)."""

import json
import os

from leadbot.bot.score import SIGNAL_ORDER
from leadbot.bot.signals import TRUE, UNKNOWN

EXTRA_KEYS = ("evidence", "unknown", "place_id")
# Claude may only fill the two text fields; everything here must come back unchanged.
LOCKED_KEYS = ("business_name", "phone", "niche", "signals", "primary_signal", "lead_score",
               "lead_tier", "bot_run_id")
MAX_SUMMARY_LINES = 3
MAX_SUMMARY_CHARS = 400
MAX_OPENER_WORDS = 30


class QualifiedError(ValueError):
    pass


def build_candidate(place, signals, *, score, tier, primary, plan, bot_run_id, email,
                    gbp_status, rank):
    return {
        "business_name": place.name,
        "phone": place.phone,
        "email": email,
        "website": place.website,
        "niche": plan.niche,
        "city": plan.city,
        "gbp_url": place.gbp_url,
        "gbp_status": gbp_status,
        "review_count": place.review_count,
        "rating": place.rating,
        "money_keyword": plan.money_keyword,
        "current_rank": rank.position,
        "signals": [s for s in SIGNAL_ORDER if signals[s].state == TRUE],
        "primary_signal": primary,
        "lead_score": score,
        "lead_tier": tier,
        "audit_summary": "",
        "call_opener": "",
        "bot_run_id": bot_run_id,
        "evidence": {k: s.evidence for k, s in signals.items() if s.state != UNKNOWN},
        "unknown": [s for s in SIGNAL_ORDER if signals[s].state == UNKNOWN],
        "place_id": place.place_id,
    }


def needs_text(record):
    return record["lead_tier"] in ("Hot", "Warm") or bool(record.get("email"))


def to_payload(record):
    return {k: v for k, v in record.items() if k not in EXTRA_KEYS}


def check_qualified(qualified, candidate):
    run_id = candidate["bot_run_id"]
    for key in LOCKED_KEYS:
        if qualified.get(key) != candidate.get(key):
            raise QualifiedError(f"{run_id}: {key} changed")
    summary = (qualified.get("audit_summary") or "").strip()
    if not summary:
        raise QualifiedError(f"{run_id}: audit_summary is empty")
    if len(summary.splitlines()) > MAX_SUMMARY_LINES or len(summary) > MAX_SUMMARY_CHARS:
        raise QualifiedError(f"{run_id}: audit_summary over {MAX_SUMMARY_LINES} lines "
                             f"or {MAX_SUMMARY_CHARS} characters")
    opener = (qualified.get("call_opener") or "").strip()
    if not opener:
        raise QualifiedError(f"{run_id}: call_opener is empty")
    if len(opener.split()) > MAX_OPENER_WORDS:
        raise QualifiedError(f"{run_id}: call_opener over {MAX_OPENER_WORDS} words")


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
```

`leadbot/bot/collect.py`:

```python
"""One city's collect step: scrape, filter, enrich, score (spec 2, 3.3-3.7)."""

import json
import os
from collections import Counter
from dataclasses import dataclass

from leadbot.bot import candidates as cand
from leadbot.bot.dnc import is_listed
from leadbot.bot.score import lead_score, lead_tier, primary_flag
from leadbot.bot.scrape import ScrapeError
from leadbot.bot.signals import Enrichment, detect, gbp_status, rank_info, true_flags


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


def _drop_reason(place, deps, *, dnc, seen, seen_days, today):
    if place.closed:
        return "closed"
    if not place.phone:
        return "no_phone"
    if dnc is not None and is_listed(dnc, place.phone):
        return "dnc"
    if seen is not None and seen.recently_seen(place.place_id, today, seen_days):
        return "seen_recently"
    if deps.find_contact(place.phone, None) or (place.email and deps.find_contact(None, place.email)):
        return "ghl_contact"
    return None


def _enrich(place, money, plan, deps, today):
    site = deps.site_check(place.website) if place.website else None
    registered = deps.domain_age(place.website) if place.website else None
    enrich = Enrichment(site, None, registered)
    rank = rank_info(place.place_id, money, plan.money_keyword)
    signals = detect(place, enrich, rank, today)
    if site is not None and site.reachable and true_flags(signals):
        enrich = Enrichment(site, deps.pagespeed(place.website), registered)
        signals = detect(place, enrich, rank, today)
    return enrich, rank, signals


def _drop_row(place, reason):
    return {"place_id": place.place_id, "business_name": place.name, "reason": reason}


def _write_report(run_dir, report):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)


def collect_city(plan, seeds, scoring, schema, deps, *, run_id, run_dir, today, dnc=None, seen=None):
    report = {"run_id": run_id, "niche": plan.niche, "city": plan.city,
              "dnc_checked": dnc is not None, "scraped": 0, "dropped": {}, "tiers": {},
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
    if not places:
        report["status"] = "blocked_or_failed"
        _write_report(run_dir, report)
        return report

    kept, dropped = [], []
    for place in places:
        reason = _drop_reason(place, deps, dnc=dnc, seen=seen, seen_days=seeds.seen_days, today=today)
        if reason:
            dropped.append(_drop_row(place, reason))
        else:
            kept.append(place)
    dropped += [_drop_row(p, "over_nightly_cap") for p in kept[seeds.max_enrich:]]
    kept = kept[:seeds.max_enrich]

    records = []
    for n, place in enumerate(kept, 1):
        enrich, rank, signals = _enrich(place, money, plan, deps, today)
        flags = true_flags(signals)
        score = lead_score(flags, plan.niche, scoring)
        flag = primary_flag(flags, scoring)
        records.append(cand.build_candidate(
            place, signals, score=score, tier=lead_tier(score, scoring),
            primary=schema.signals[flag] if flag else None, plan=plan,
            bot_run_id=f"{run_id}-{n:04d}",
            email=(enrich.site.email if enrich.site else None) or place.email,
            gbp_status=gbp_status(place), rank=rank))

    cand.write_jsonl(os.path.join(run_dir, "candidates.jsonl"), records)
    cand.write_jsonl(os.path.join(run_dir, "dropped.jsonl"), dropped)
    report["dropped"] = dict(Counter(d["reason"] for d in dropped))
    report["tiers"] = dict(Counter(r["lead_tier"] for r in records))
    report["needs_text"] = sum(cand.needs_text(r) for r in records)
    _write_report(run_dir, report)
    return report
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bot_candidates.py tests/test_bot_collect.py -q` → 22 passed; `python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add leadbot/bot/candidates.py leadbot/bot/collect.py tests/test_bot_candidates.py tests/test_bot_collect.py
git commit -m "feat(bot): candidate records and the per-city collect step

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: CLI (collect and push)

**Files:**
- Create: `leadbot/bot/cli.py`, `leadbot/bot/__main__.py`
- Modify: `.env.example` (add `GMAPS_SCRAPER_EXE=`)
- Test: `tests/test_bot_cli.py`

**Interfaces:**
- Consumes: everything above; part A `client_from_env`, `load_schema`, `slug`, `load_context`, `push_lead`, `validate_payload`, `PayloadError`.
- Produces: `main(argv=None) -> int`; `run_id_for(today, niche, city) -> str`; module-level `RUNS_DIR`, `SEEN_PATH` (patchable). Commands:
  - `collect --niche N --city C [--no-dnc] [--today YYYY-MM-DD]` → prints the report; exit 0 when status ok, 2 when blocked_or_failed. Without `--no-dnc` it exits with a message naming the missing area codes when `state/dnc/` lacks them.
  - `push FILE [--dry-run]` → reads `report.json`, `candidates.jsonl` (and `dropped.jsonl`) next to FILE; checks each qualified line with `check_qualified`; dry run: `validate_payload` only, writes `dry_run_report.json`, never builds a GHL client; live: refuses unless `load_seeds().live` and `report["dnc_checked"]`, then `push_lead` per line, writes `push_report.json`, marks every candidate and every drop except `over_nightly_cap` in the seen store. Exit 1 if any line was rejected or failed, else 0.

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_cli.py`:

```python
import dataclasses
import datetime as dt
import json

import pytest

from leadbot.bot import cli
from leadbot.bot.seeds import load_seeds
from leadbot.bot.seen import SeenStore
from leadbot.ghl.schema import load_schema
from leadbot.ghl.setup import run_setup
from tests.fakes import FakeClient

RUN_ID = "2026-11-06-counselors-therapists-austin-tx"


def candidate(n=1, **over):
    c = {"business_name": "Bright Path Counseling (fictional)", "phone": "+15125550132",
         "email": None, "website": "https://brightpath.example.com/",
         "niche": "Counselors / therapists", "city": "Austin, TX",
         "gbp_url": "https://www.google.com/maps/place/?q=place_id:fx-brightpath",
         "gbp_status": "Unverified", "review_count": 4, "rating": 5.0,
         "money_keyword": "therapist austin", "current_rank": None,
         "signals": ["gbp_unverified_or_suspended", "new_business"],
         "primary_signal": "GBP Suspended", "lead_score": 45, "lead_tier": "Warm",
         "audit_summary": "", "call_opener": "", "bot_run_id": f"{RUN_ID}-{n:04d}",
         "evidence": {"gbp_unverified_or_suspended": "Google listing has no owner (unclaimed)"},
         "unknown": [], "place_id": f"fx-{n}"}
    c.update(over)
    return c


def qualified(c, **over):
    q = dict(c, audit_summary="Google listing has no owner.\nOnly 4 reviews, all from the last two months.",
             call_opener="Hi, I noticed Bright Path Counseling's Google listing has no owner, "
                         "so it may be missing calls.")
    q.update(over)
    return q


def write_run(tmp_path, cands, quals, dnc_checked=True, dropped=()):
    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps({"run_id": RUN_ID, "dnc_checked": dnc_checked}),
                                         encoding="utf-8")
    for name, rows in (("candidates.jsonl", cands), ("qualified.jsonl", quals),
                       ("dropped.jsonl", dropped)):
        (run_dir / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return run_dir


def push(run_dir, *extra):
    return cli.main(["push", str(run_dir / "qualified.jsonl"), *extra])


def report_of(run_dir, name):
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def test_run_id():
    assert cli.run_id_for(dt.date(2026, 11, 6), "Counselors / therapists", "Austin, TX") == RUN_ID


def test_dry_run_validates_without_ghl(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "client_from_env", lambda: pytest.fail("dry run touched GHL"))
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)])
    assert push(run_dir, "--dry-run") == 0
    assert report_of(run_dir, "dry_run_report.json")["counts"] == {"would_push": 1}


def test_changed_score_is_rejected(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c, lead_score=90)])
    assert push(run_dir, "--dry-run") == 1
    result = report_of(run_dir, "dry_run_report.json")["results"][0]
    assert result["outcome"] == "rejected" and "lead_score" in result["message"]


def test_unknown_bot_run_id_is_rejected(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(candidate(n=7))])
    assert push(run_dir, "--dry-run") == 1


def test_live_refused_while_seeds_not_live(tmp_path):
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)])
    with pytest.raises(SystemExit, match="live = false"):
        push(run_dir)


def test_live_refused_for_no_dnc_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), live=True))
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)], dnc_checked=False)
    with pytest.raises(SystemExit, match="--no-dnc"):
        push(run_dir)


def test_live_push_creates_lead_and_marks_seen(tmp_path, monkeypatch):
    schema = load_schema()
    client = FakeClient()
    run_setup(client, schema)
    client.create_pipeline("02. GSM Appointment New Client", ["New client!"])
    for f in schema.fields:
        if f.created_by == "ui":
            client.fields.append({"id": "ui-" + f.name, "name": f.name, "dataType": f.data_type,
                                  "picklistOptions": list(f.options)})
    seen_path = str(tmp_path / "seen.sqlite")
    monkeypatch.setattr(cli, "client_from_env", lambda: client)
    monkeypatch.setattr(cli, "load_seeds", lambda: dataclasses.replace(load_seeds(), live=True))
    monkeypatch.setattr(cli, "SEEN_PATH", seen_path)
    c = candidate()
    run_dir = write_run(tmp_path, [c], [qualified(c)], dropped=[
        {"place_id": "fx-closed", "business_name": "x", "reason": "closed"},
        {"place_id": "fx-cap", "business_name": "y", "reason": "over_nightly_cap"}])
    assert push(run_dir) == 0
    assert report_of(run_dir, "push_report.json")["counts"] == {"created": 1}
    assert len(client.opportunities) == 1
    store, today = SeenStore(seen_path), dt.date.today()
    assert store.recently_seen("fx-1", today, 30)
    assert store.recently_seen("fx-closed", today, 30)
    assert not store.recently_seen("fx-cap", today, 30)


def test_collect_refuses_without_dnc_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_dnc", lambda: cli.DncList())
    with pytest.raises(SystemExit, match="512"):
        cli.main(["collect", "--niche", "Counselors / therapists", "--city", "Austin, TX"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bot_cli.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

`leadbot/bot/cli.py`:

```python
"""python -m leadbot.bot collect|push (spec 2, 3.9)."""

import argparse
import datetime as dt
import json
import os
from collections import Counter

from leadbot.bot import candidates as cand
from leadbot.bot import domain_age, pagespeed, scrape, site_check
from leadbot.bot.collect import Deps, collect_city
from leadbot.bot.dnc import DncList, load_dnc, missing_area_codes
from leadbot.bot.score import load_scoring
from leadbot.bot.seeds import city_plan, load_seeds
from leadbot.bot.seen import SeenStore
from leadbot.env import ROOT, client_from_env
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


def _mark_seen(run_dir, candidates, today):
    store = SeenStore(SEEN_PATH)
    try:
        for c in candidates:
            store.mark(c["place_id"], today, c["lead_tier"])
        for d in cand.read_jsonl(os.path.join(run_dir, "dropped.jsonl")):
            if d.get("reason") != "over_nightly_cap" and d.get("place_id"):
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
    client = ctx = None
    if live:
        client = client_from_env()
        ctx = load_context(client, schema)

    results = []
    for q in cand.read_jsonl(args.file):
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
            r = push_lead(client, schema, ctx, payload)
            results.append({"bot_run_id": c["bot_run_id"], "outcome": r.outcome, "message": r.message,
                            "contact_id": r.contact_id, "opportunity_id": r.opportunity_id})
            continue
        try:
            validate_payload(payload, schema)
            results.append({"bot_run_id": c["bot_run_id"], "outcome": "would_push", "message": ""})
        except PayloadError as exc:
            results.append({"bot_run_id": c["bot_run_id"], "outcome": "rejected", "message": str(exc)})

    if live:
        _mark_seen(run_dir, candidates.values(), dt.date.today())
    counts = dict(Counter(r["outcome"] for r in results))
    name = "push_report.json" if live else "dry_run_report.json"
    with open(os.path.join(run_dir, name), "w", encoding="utf-8") as fh:
        json.dump({"live": live, "counts": counts, "results": results}, fh, indent=2)
    print(json.dumps({"live": live, "counts": counts}, indent=2))
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
```

`leadbot/bot/__main__.py`:

```python
from leadbot.bot.cli import main

raise SystemExit(main())
```

`.env.example`: append the line `GMAPS_SCRAPER_EXE=` after `GHL_LOCATION_ID=` (no value).

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bot_cli.py -q` → 8 passed; `python -m pytest -q` → all pass (part A's 85 plus the new ones).

- [ ] **Step 5: Commit**

```bash
git add leadbot/bot/cli.py leadbot/bot/__main__.py .env.example tests/test_bot_cli.py
git commit -m "feat(bot): collect and push commands with dry-run and live gates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Nightly skill, scraper build doc, installed scraper skill

**Files:**
- Create: `.claude/skills/leadbot-nightly/SKILL.md`, `docs/scraper-build.md`
- Commit (already on disk, untracked): `.claude/skills/google-maps-scraper/` and `skills-lock.json`

**Interfaces:**
- Consumes: the CLI from Task 8 (exact commands below).
- Produces: the instructions the scheduled task (Task 11) runs.

- [ ] **Step 1: Write the nightly skill**

`.claude/skills/leadbot-nightly/SKILL.md`:

````markdown
---
name: leadbot-nightly
description: Nightly RiseRidge lead bot run (part B1). Collects counselor/therapist leads per city, writes the audit summary and call opener for each lead that needs one, then pushes to GoHighLevel or dry-runs. Use when the scheduled task "leadbot-nightly" fires or the user asks to run the lead bot.
---

# Lead bot nightly run

Repository: `D:/Claude Code/riseridge-leadbot`. Run every command from there with `python`. Never print or open `.env`.

## 1. Collect, once per city

Cities: every `[cities."..."]` table in `config/seeds.toml`, in file order. Niche: `Counselors / therapists`.

```
python -m leadbot.bot collect --niche "Counselors / therapists" --city "<city>"
```

- Exit 0: continue with that city.
- Exit 2 (`blocked_or_failed`): note it for the report and skip the city. Do not retry in the same run.
- Stops with "No DNC file for area codes": if `config/seeds.toml` has `live = false`, rerun the same command with `--no-dnc`. If `live = true`, skip the city and report the missing area codes.

The printed report names the `run_id`; the run folder is `state/runs/<run_id>/`.

## 2. Qualify

Read `state/runs/<run_id>/candidates.jsonl`. Work only on records whose `lead_tier` is Hot or Warm, or Cold with a non-empty `email`.

For each such record:
1. **Chain or franchise?** Decide from `business_name`, `website` and `evidence`: national or regional brands, names with a location suffix ("- Austin"), practices that list many locations. If yes, append one line to `dropped.jsonl` in the same folder: `{"place_id": ..., "business_name": ..., "reason": "franchise_or_chain", "note": "<why>"}` and do not qualify it.
2. Otherwise copy the record exactly and fill only:
   - `audit_summary`: at most 3 short lines separated by newlines, 400 characters in total, facts a rep can say out loud. Use only what `evidence` states. Never invent numbers. If a needed fact is missing, say "unknown".
   - `call_opener`: one sentence under 30 words that names the business and its primary signal. Friendly, no hype.
3. Never change any other field. `push` rejects a record whose signals, score, tier, phone or other fields differ from the candidate.

Write all qualified records, one JSON object per line, UTF-8, to `state/runs/<run_id>/qualified.jsonl` in one write.

## 3. Push

- `live = false` in `config/seeds.toml`: `python -m leadbot.bot push state/runs/<run_id>/qualified.jsonl --dry-run`
- `live = true`: `python -m leadbot.bot push state/runs/<run_id>/qualified.jsonl`

A non-zero exit means some lines were rejected or failed. Read `dry_run_report.json` or `push_report.json` and include each reason in the report.

## 4. Report

One short message: whether the run was live; per city the scraped count, drops by reason, tiers, qualified, pushed (or would_push), rejected and failed. Name only Hot leads, by business name. No phone numbers or emails.
````

- [ ] **Step 2: Write the scraper build doc**

`docs/scraper-build.md`:

````markdown
# Patched Google Maps scraper (Windows)

The official gosom/google-maps-scraper v1.18.1 Windows exe returns "unexpected page type" and 0 rows on this PC. Cause: gosom/scrapemate starts Chrome with `--single-process`, which crashes Chrome on Windows (upstream issue gosom/google-maps-scraper#322, open as of 2026-11-05). The fix is to drop that flag and build locally.

Built 2026-11-05 on this PC:

- Go 1.27.3 from https://go.dev/dl/ (zip, SHA-256 checked), unpacked to `C:/Users/dimaf/tools/go`.
- Sources in `C:/Users/dimaf/tools/gmaps-src`:

```bash
git clone --depth 1 --branch v1.18.1 https://github.com/gosom/google-maps-scraper.git
git clone --depth 1 --branch v1.4.0 https://github.com/gosom/scrapemate.git
sed -i '/--single-process/d' scrapemate/adapters/fetchers/jshttp/jshttp.go
cd google-maps-scraper
../../go/bin/go.exe mod edit -replace github.com/gosom/scrapemate=../scrapemate
../../go/bin/go.exe build -o ../../google-maps-scraper/gmaps-patched.exe .
```

- Output: `C:/Users/dimaf/tools/google-maps-scraper/gmaps-patched.exe`, set as `GMAPS_SCRAPER_EXE` in `.env`.
- First run downloads Playwright's Chrome (about 184 MB) to `%LOCALAPPDATA%/ms-playwright-go`.

To update: check that issue #322 is still open, re-clone at the new tags (match the scrapemate version in the scraper's `go.mod`), re-apply the two edits, rebuild, and run one small query before switching `.env`.
````

- [ ] **Step 3: Check the skill files and commit**

Run: `git status --short` → shows `.claude/skills/google-maps-scraper/`, `.claude/skills/leadbot-nightly/`, `skills-lock.json`, `docs/scraper-build.md`. Make sure no other `.claude/` file (for example `settings.local.json`) is staged.

```bash
git add .claude/skills/google-maps-scraper .claude/skills/leadbot-nightly skills-lock.json docs/scraper-build.md
git commit -m "docs(bot): nightly skill, patched scraper build notes, installed scraper skill

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Live dry run, Austin (controller with the user)

Writes nothing to GHL: `collect` only reads (`/contacts/search/duplicate`) and `push --dry-run` never builds a client.

- [ ] **Step 1: Point `.env` at the scraper** (append, never read the file)

```bash
printf '\nGMAPS_SCRAPER_EXE=C:/Users/dimaf/tools/google-maps-scraper/gmaps-patched.exe\n' >> .env
```

- [ ] **Step 2: Collect Austin without DNC** (run in the background; 6 queries at depth 5 can take 20-60 minutes)

Run: `python -m leadbot.bot collect --niche "Counselors / therapists" --city "Austin, TX" --no-dnc`
Expected: exit 0, report with `scraped` > 0 and `status: ok`. If exit 2, read `money.scraper.log` / `areas.scraper.log` for blocking before anything else.

- [ ] **Step 3: Spot-check the money-keyword rank**

Compare the first 5 places of `money.raw.jsonl` with a manual Google Maps search for "therapist in Austin, TX" in the browser pane. Record whether the order matches (the rank signal assumes file order = Maps order).

- [ ] **Step 4: Qualify by following the nightly skill's section 2 by hand**, writing `qualified.jsonl`.

- [ ] **Step 5: Dry-run push**

Run: `python -m leadbot.bot push state/runs/<run_id>/qualified.jsonl --dry-run`
Expected: exit 0, `would_push` for every qualified line.

- [ ] **Step 6: User review**

Show the user every Hot and Warm lead: business name, signals with evidence, score, audit summary, call opener; plus drop counts. SOP QA target: 90%+ of signals correct. Fix the signal rule or skill wording for anything wrong, rerun the unit tests, and repeat Steps 2-5 with `--today` set to the same date if needed.

- [ ] **Step 7: Record** the results in `docs/b1-live-notes.md` (counts, rank spot-check, QA findings, run time) and commit it.

---

### Task 11: Go-live (each step needs the user's yes)

- [ ] **Step 1: DNC files.** The user registers for an FTC National DNC Subscription Account Number and saves the area-code files in `state/dnc/`. Check the format without printing numbers: count numbers per area code with `python -c "from leadbot.bot.dnc import load_dnc; from collections import Counter; print(Counter(n[:3] for n in load_dnc().numbers))"`. If the counts are zero or wrong, adapt `dnc._ten_digits` to the real layout with a test built from a fictional line in that layout.
- [ ] **Step 2: Existing-automation check (observation 0087).** A live push creates real contacts tagged `bot-lead` and opportunities in "Bot Leads – SEO", the same events the part A tests produced without side effects. List published workflows through the API (`GET /workflows/`) and show the user any published since 2026-11-04 before going on.
- [ ] **Step 3: Switch on.** With the user's yes, set `live = true` in `config/seeds.toml` and commit.
- [ ] **Step 4: First live push, Austin only.** Run the nightly skill for Austin alone (collect with DNC, qualify, push). Then check in GHL: contacts, owner Sergey, Bot Leads deals in New, call tasks. Every Hot lead is checked by hand with the user (SOP week 2).
- [ ] **Step 5: Schedule.** With the user's yes, create the Claude Code scheduled task `leadbot-nightly`, cron `0 2 * * *` (local time), prompt: "In D:/Claude Code/riseridge-leadbot, run the project skill .claude/skills/leadbot-nightly/SKILL.md for every city in config/seeds.toml and report as that skill says." Tell the user it runs only while the Claude app is open.
- [ ] **Step 6: Record** the go-live in `docs/b1-live-notes.md` and commit.

---

### Task 12: Signal calibration (added 2026-11-05 after the Austin dry run)

Why: in the Austin dry run (`docs/b1-live-notes.md`) `rank_gap` was TRUE for 147 of 150 places and `competitor_gap` for 130, so neither separated leads; `broken_website` (84 of 150) counted bot-refused fetches and JavaScript-rendered sites as broken; and most Warm leads were unclaimed listings with no review for years. Rules approved by the user in chat on 2026-11-05.

**Files:** Modify `leadbot/bot/signals.py`, `leadbot/bot/site_check.py`, `leadbot/bot/score.py`, `config/scoring.toml`, `leadbot/bot/collect.py`, `leadbot/bot/candidates.py`; tests in the matching `tests/test_bot_*.py`; spec sections 3.4-3.7 in `docs/superpowers/specs/2026-11-05-leadbot-part-b1-design.md`.

**Rules (exact):**

1. `rank_gap` (money keyword position, 1-based, from `RankInfo.position`):
   - position 1-3 → FALSE, evidence "#N on Maps for '<kw>' (map pack)";
   - position 4-20 → TRUE, evidence "#N on Maps for '<kw>', just outside the map pack";
   - position 21 or worse → FALSE, evidence "#N on Maps for '<kw>'";
   - not in the money results (position None) → UNKNOWN, evidence "not in the top <checked> on Maps for '<kw>'" (evidence is still recorded for UNKNOWN in the candidate's `unknown` list; see rule 6);
   - no money results at all (checked 0) → UNKNOWN as today.
   - Constants: `MAP_PACK = 3`, `NEAR_MISS_MAX = 20`.
2. `competitor_gap`: evaluated only when the place is in the money results (position not None); otherwise UNKNOWN "not in the results for '<kw>'". Rivals are the first 3 money places, excluding the place itself AND excluding chain listings. A chain listing is one whose brand key appears on more than one listing among all places scraped in the run (money + areas). Brand key: the name lowercased, cut at the first " - ", " | ", " – " or " (", stripped. `rank_info(place_id, money_places, keyword, chain_keys=frozenset())` takes the set of chain brand keys; `collect_city` computes it once per city from the merged place list. If fewer than 1 non-chain rival exists → UNKNOWN "no independent competitors to compare". TRUE when the rivals' average review_count ≥ 3 × max(place.review_count, 1), as today.
3. `broken_website`:
   - `SiteCheck` gains `blocked: bool = False` and `status: int | None = None`. `check_site`: an `urllib.error.HTTPError` (or a returned status ≥ 400) with status 401, 403, 429 or 503 → `SiteCheck(False, final_url, blocked=True, status=code)`; any other HTTP error status → `SiteCheck(False, final_url, status=code)`; any other exception (DNS, refused connection, timeout, TLS) → `SiteCheck(False)`.
   - `_site`: no website → TRUE (unchanged); site not checked → UNKNOWN (unchanged); `blocked` → UNKNOWN "website refused the bot's request (HTTP <code>)"; not reachable → TRUE "website <url> did not load" (add " (HTTP <code>)" when status is known).
   - Loaded page: TRUE when (no lead form AND no click-to-call link) OR (pagespeed is not None and pagespeed < 40). Evidence lists every missing item including "no conversion tag" as information. Otherwise FALSE. The missing tracking tag alone never makes it TRUE.
4. Inactive listing (new negative signal): `negatives(place, today) -> dict[str, Signal]` in signals.py returns `{"inactive_listing": Signal(TRUE, "newest visible review <date>, over 2 years ago")}` when `place.review_dates` is non-empty and `(today - place.review_dates[-1]).days > 730`; otherwise `{"inactive_listing": Signal(FALSE, ...)}` (or UNKNOWN "no review dates" when there are no dates). Constant `INACTIVE_DAYS = 730`.
5. Scoring: `config/scoring.toml` `[weights]` gains `negative_signal = 30`. `lead_score(true_signals, niche, scoring, ads_spend=None, negatives=())` subtracts `negative_signal` once per TRUE negative, after the positives and the retention bonus, floor 0, cap 100. Existing callers without `negatives` behave as today. `primary_flag` is unchanged (negatives never become the primary signal).
6. Candidate record: gains `negatives` (list of TRUE negative names, e.g. `["inactive_listing"]`) and the negative's evidence under `evidence` (key `inactive_listing`) when TRUE. Add `"negatives"` to `EXTRA_KEYS` so it never reaches GHL. `signals` stays positive flags only (the schema rejects unknown flags). Also keep the evidence of UNKNOWN rank/competitor signals under a new candidate key `notes` (dict, signal → evidence) so the rep still sees "not in the top 60"; add `"notes"` to `EXTRA_KEYS`.
7. Nightly skill: in section 2, the audit summary may use `evidence` and `notes`; never present a `notes` item as a problem signal (it is context, e.g. "not in the top 60 for 'therapist austin'").

**Tests (each written first, seen failing):**
- signals: rank 3 FALSE, 4 TRUE, 20 TRUE, 21 FALSE, None UNKNOWN with "not in the top" evidence; competitor gap UNKNOWN when position None; chain rivals excluded (a money list whose first two places share a brand key "acme counseling" gives rivals from the next independents); all rivals chains → UNKNOWN; blocked site → UNKNOWN; loaded page with no form only (call link present, tag missing) → FALSE; no form and no call link → TRUE; call link present, PageSpeed 30 → TRUE; inactive: newest review 731 days ago → TRUE, 730 → FALSE, no dates → UNKNOWN.
- site_check: fake fetch raising `urllib.error.HTTPError(url, 403, ...)` → `blocked=True, status=403`; 404 → `blocked=False, status=404, reachable=False`; OSError → `SiteCheck(False)`.
- score: negatives subtract 30, floor 0 (e.g. score 20 with one negative → 0); without negatives unchanged.
- collect: update existing expectations deliberately (show the reasoning in the report): Bright Path (not in money results) now has rank_gap and competitor_gap UNKNOWN, so its score is 25+15+15+5 = 60 (Warm); assert `negatives == []` and `notes` contains rank_gap; add a fixture-independent test that an inactive place (newest review over 2 years old) carries `negatives == ["inactive_listing"]` and a score lowered by 30; `to_payload` output contains neither `negatives` nor `notes`.
- cli: the existing live-push test still passes (payload unchanged).

Commit in one or two commits ("feat(bot): calibrate rank, competitor and website signals; inactive-listing negative").
